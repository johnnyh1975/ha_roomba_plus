"""Schedule parsing for the Roomba+ integration.

v3.4.0 CAL — extracted from sensor_core.py's _calc_next_clean()/
_next_from_schedule2()/_next_from_schedule_v1(), which computed only
the single next occurrence for the existing sensor.*_next_clean
sensor. Generalised here to return ALL recurring occurrences within an
arbitrary [start, end) range, which is what a calendar.py
RoombaScheduleCalendar entity needs (HA's async_get_events() interface)
— the sensor keeps working via a thin wrapper around this module (see
sensor_core.py::_calc_next_clean()).

Pure functions, no HA imports beyond typing — same "reine Funktionen,
kein State" philosophy as grid_store.py/mission_map.py. This lets both
sensor_core.py and calendar.py depend on this neutral module instead of
one platform importing from the other (the exact coupling SENSOR-SPLIT
just finished untangling).
"""
from __future__ import annotations

import datetime
from typing import Any

from roombapy_prime.models.schedules_dnd import ScheduleFrequency

# No entry in either cleanSchedule2 or legacy cleanSchedule carries a
# planned mission DURATION — only a start time. A fixed placeholder is
# used rather than estimating from mission history (that would imply a
# false precision the data doesn't support — see the CAL plan §2.3
# decision, same reasoning that led to the ENERGY feature being cut).
DEFAULT_EVENT_DURATION = datetime.timedelta(minutes=60)


def _weekly_occurrences(
    roomba_day: Any,
    hour: Any,
    minute: Any,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[datetime.datetime]:
    """All weekly occurrences of one (day, hour, minute) recurring slot
    within [start, end) — inclusive of start, exclusive of end.

    Roomba day numbering: 0=Sunday … 6=Saturday. Converted to Python's
    weekday() numbering (Mon=0 … Sun=6) via (roomba_day - 1) % 7 —
    identical conversion to the pre-extraction sensor_core.py code.

    v3.4.0 bug-hunt fix: roomba_day/hour/minute come straight from MQTT
    — untrusted, potentially malformed across firmware variants (this
    is exactly the Feldverifikations-Gate concern from the CAL plan).
    A non-numeric value here used to raise TypeError (str - int, or
    datetime.replace(hour=<str>)), crashing the ENTIRE schedule parse
    — not just skipping the one bad entry, since this function has no
    per-entry isolation of its own. Coerced to int defensively; an
    unparseable slot is skipped (returns no occurrences for it) rather
    than taking down every other valid entry in the same schedule.
    """
    try:
        roomba_day = int(roomba_day)
        hour = int(hour)
        minute = int(minute)
    except (TypeError, ValueError):
        return []

    py_wd = (roomba_day - 1) % 7
    days_ahead = (py_wd - start.weekday()) % 7
    try:
        first = start.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        ) + datetime.timedelta(days=days_ahead)
    except ValueError:
        # e.g. hour=25 or minute=90 — a valid int but out of range.
        return []
    if first < start:
        first += datetime.timedelta(days=7)

    occurrences: list[datetime.datetime] = []
    current = first
    while current < end:
        occurrences.append(current)
        current += datetime.timedelta(days=7)
    return occurrences


def _biweekly_occurrences(
    roomba_day: Any,
    hour: Any,
    minute: Any,
    anchor_date: datetime.date,
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[datetime.datetime]:
    """All bi-weekly occurrences of one (day, hour, minute) recurring
    slot within [start, end), anchored to anchor_date.

    UNCONFIRMED HYPOTHESIS, not settled data (see
    parse_prime_schedule_occurrences()'s own caller-side docstring for
    the full reasoning): anchor_date is meant to come from
    ScheduleOptions.after, a full calendar date. That field's own
    class docstring describes it as a schedule's "start date" -- which
    COULD mean "the first actual occurrence" (making it usable as a
    cadence anchor the way this function assumes), or could just be a
    validity lower-bound unrelated to which specific week the pattern
    started on. Never confirmed against a real captured BI_WEEKLY
    schedule. If a real one is ever captured and this turns out wrong,
    this function (not the callers) is where to fix the assumption.

    Same day-numbering caveat as _weekly_occurrences() (Roomba/Prime
    day numbering assumed 0=Sunday, not separately confirmed for
    Prime's own ScheduleTime -- see this function's own caller)."""
    try:
        roomba_day = int(roomba_day)
        hour = int(hour)
        minute = int(minute)
    except (TypeError, ValueError):
        return []

    py_wd = (roomba_day - 1) % 7
    # tzinfo matched to `start`'s own -- anchor_date itself carries no
    # timezone, and comparing a naive datetime against aware start/end
    # below would raise TypeError otherwise.
    anchor_dt = datetime.datetime.combine(anchor_date, datetime.time(), tzinfo=start.tzinfo)
    days_ahead = (py_wd - anchor_dt.weekday()) % 7
    try:
        # "Week 0": the first real occurrence of this weekday on or
        # after anchor_date -- every subsequent occurrence is exactly
        # 14 days after this one, not just any matching weekday.
        week_zero = anchor_dt.replace(hour=hour, minute=minute) + datetime.timedelta(days=days_ahead)
    except ValueError:
        return []

    occurrences: list[datetime.datetime] = []
    current = week_zero
    # Fast-forward to the first occurrence >= start without a per-step
    # loop from anchor_date (which could be years in the past).
    if current < start:
        periods_behind = (start - current).days // 14
        current += datetime.timedelta(days=14 * periods_behind)
        while current < start:
            current += datetime.timedelta(days=14)
    while current < end:
        occurrences.append(current)
        current += datetime.timedelta(days=14)
    return occurrences


def occurrences_from_schedule2(
    entries: list[Any], start: datetime.datetime, end: datetime.datetime,
) -> list[datetime.datetime]:
    """cleanSchedule2 (i/s/j-series): a list of {"enabled": bool,
    "start": {"hour": int, "min": int, "day": [0..6, ...]}} entries."""
    occurrences: list[datetime.datetime] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("enabled", False):
            continue
        entry_start = entry.get("start") or {}
        hour = entry_start.get("hour", 0)
        minute = entry_start.get("min", 0)
        for roomba_day in entry_start.get("day") or []:
            occurrences.extend(
                _weekly_occurrences(roomba_day, hour, minute, start, end)
            )
    return occurrences


def _region_ids_from_schedule2_entry(entry: dict[str, Any]) -> list[str]:
    """CONFIRMED NECESSARY (real UX review): cleanSchedule2 entries
    carry a "cmd" sub-object with a "regions" list (same shape already
    used by SmartZoneSelect/zone_naming.py to discover known region
    IDs) -- meaning a SMART-tier schedule entry CAN target a specific
    room/zone, not just "whole house" as this module previously only
    surfaced (it only ever extracted the time fields, discarding this
    entirely -- the data was always there, just unused here). An empty
    result means "whole house" (no region reference on this entry),
    not "unknown"."""
    from .const import extract_region_id

    cmd = entry.get("cmd") or {}
    if not isinstance(cmd, dict):
        return []
    regions = cmd.get("regions")
    if not isinstance(regions, list):
        return []
    return [rid for r in regions if (rid := extract_region_id(r))]


def occurrences_from_schedule2_with_regions(
    entries: list[Any], start: datetime.datetime, end: datetime.datetime,
) -> list[tuple[datetime.datetime, list[str]]]:
    """Same as occurrences_from_schedule2(), but pairs each occurrence
    with the region_ids (if any) of the entry it came from -- see
    _region_ids_from_schedule2_entry()'s own docstring for why this
    exists as a separate function rather than changing
    occurrences_from_schedule2()'s own signature (sensor_core.py's
    *_next_clean sensor uses that one and has no need for region
    data)."""
    occurrences: list[tuple[datetime.datetime, list[str]]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("enabled", False):
            continue
        entry_start = entry.get("start") or {}
        hour = entry_start.get("hour", 0)
        minute = entry_start.get("min", 0)
        region_ids = _region_ids_from_schedule2_entry(entry)
        for roomba_day in entry_start.get("day") or []:
            for occurrence in _weekly_occurrences(roomba_day, hour, minute, start, end):
                occurrences.append((occurrence, region_ids))
    return occurrences


def occurrences_from_schedule_v1(
    schedule: dict[str, Any], start: datetime.datetime, end: datetime.datetime,
) -> list[datetime.datetime]:
    """Legacy cleanSchedule (900/600-series): three parallel 7-element
    arrays, {"cycle": ["none"|"start", ...], "h": [...], "m": [...]},
    index 0 = Sunday."""
    cycle = schedule.get("cycle") or []
    hours = schedule.get("h") or []
    mins = schedule.get("m") or []
    occurrences: list[datetime.datetime] = []
    for roomba_day, (cyc, h, m) in enumerate(zip(cycle, hours, mins)):
        if cyc != "start":
            continue
        occurrences.extend(_weekly_occurrences(roomba_day, h, m, start, end))
    return occurrences


def parse_schedule_occurrences(
    state: dict[str, Any],
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """All recurring cleaning start times within [start, end), from
    either cleanSchedule2 (i/s/j-series, preferred) or legacy
    cleanSchedule (900/600-series, fallback) — same source precedence
    as the sensor.*_next_clean sensor this was extracted from.

    Returns a sorted list of (start, end) tuples; each occurrence's own
    end uses DEFAULT_EVENT_DURATION (see module docstring — no real
    planned duration exists in the source data).
    """
    schedule2 = state.get("cleanSchedule2") or []
    if schedule2:
        starts = occurrences_from_schedule2(schedule2, start, end)
    else:
        schedule_v1 = state.get("cleanSchedule") or {}
        starts = (
            occurrences_from_schedule_v1(schedule_v1, start, end)
            if schedule_v1 else []
        )

    starts.sort()
    return [(s, s + DEFAULT_EVENT_DURATION) for s in starts]


def parse_schedule_occurrences_with_regions(
    state: dict[str, Any],
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[tuple[datetime.datetime, datetime.datetime, list[str]]]:
    """Same source precedence as parse_schedule_occurrences() (this
    module's own docstring), but each occurrence also carries the
    region_ids (if any) of the schedule entry it came from.

    EPHEMERAL tier (legacy cleanSchedule, 900/600-series) genuinely has
    no region concept at all -- these robots have no persistent map,
    so region_ids is always [] here, not a gap in this function. SMART
    tier (cleanSchedule2, i/s/j-series) CAN carry a region reference
    per entry -- see _region_ids_from_schedule2_entry()'s own
    docstring for why this was previously unused. An empty list means
    "whole house" either way, whether because the tier can't have
    regions at all, or because this specific entry simply doesn't
    reference one.
    """
    schedule2 = state.get("cleanSchedule2") or []
    if schedule2:
        with_regions = occurrences_from_schedule2_with_regions(schedule2, start, end)
    else:
        schedule_v1 = state.get("cleanSchedule") or {}
        with_regions = (
            [(s, []) for s in occurrences_from_schedule_v1(schedule_v1, start, end)]
            if schedule_v1 else []
        )

    with_regions.sort(key=lambda item: item[0])
    return [(s, s + DEFAULT_EVENT_DURATION, region_ids) for s, region_ids in with_regions]


def parse_prime_schedule_occurrences(
    schedules: list[Any],
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[
    tuple[
        datetime.datetime, datetime.datetime, list[str],
        str | None, str | None, int | None,
    ]
]:
    """Prime's own equivalent of parse_schedule_occurrences_with_regions(),
    for a list of roombapy_prime HouseholdSchedule objects (from
    get_schedules()) instead of a local vacuum_state dict.

    WEEKDAY NUMBERING NOW CONFIRMED for Prime (@DaRealGuGu, b7): the
    raw schedules line up with his app screen exactly.

        sam. 15:45              -> day [6]
        ven. 09:00              -> day [5]
        lun./mar./mer./jeu.     -> day [3, 1, 2, 4]

    So Monday=1 through Saturday=6, and Sunday=0 -- the same 0=Sunday
    convention as Classic's cleanSchedule2, which this code had assumed
    on priors and flagged as unverified.

    The flag said "if a real Prime schedule ever shows up on the wrong
    day of the week, this assumption is the first place to check". It
    was checked, against a real account, and it holds. Anyone chasing a
    schedule that fires on the wrong day should look elsewhere.

    ONLY ScheduleFrequency.WEEKLY is computed, deliberately, for now.
    BI_WEEKLY/MONTHLY/ONCE are skipped -- not because a reasonable
    hypothesis doesn't exist (see _biweekly_occurrences()'s own
    docstring: options.after, a full calendar date, is a plausible
    cadence anchor for BI_WEEKLY specifically), but because there is no
    way to VERIFY any such hypothesis without a real device test that
    waits days/weeks to observe whether the robot actually fires when
    expected -- a materially different risk profile than every other
    staged test in this project (immediate, observable effect). Given
    that risk, and a real decompilation finding that BI_WEEKLY/MONTHLY
    are referenced NOWHERE in the app's own code at all (parallel
    native-analysis track: the native core bridge's own
    ScheduleFrequency only has Invalid/Once/Weekly; the richer REST
    enum and ScheduleOptions itself have zero consumers anywhere else),
    suggesting these may be legacy-only and rarely if ever encountered
    on a real, actively-used account -- the decision for now is to
    skip both rather than ship an unverified guess. _biweekly_occurrences()
    itself is kept, tested, and ready to wire back in if a future
    session decides the long-duration verification is worth doing.

    MONTHLY has the added, independent problem that even a confirmed
    anchor wouldn't resolve: options.start.day is a WEEKDAY list
    ("Tuesday"), not a day-of-month -- "monthly" plus a weekday is
    inherently ambiguous (first Tuesday? last? some other rule?), and
    the decompilation search found no rule to read for this either.

    A schedule with no name set falls back to None (caller decides how
    to label it) -- matches HouseholdSchedule.options.name's own
    optionality.
    """
    from .const import extract_region_id

    # SIX FIELDS, MATCHING THIS FUNCTION'S OWN SIGNATURE.
    #
    # The tuple grew twice -- schedule_id so the calendar can give
    # events a uid (without which delete and edit were unreachable),
    # then operating_mode -- and this local declaration stayed at four.
    # The signature above was right the whole time; only the variable
    # inside disagreed with it.
    results: list[
        tuple[
            datetime.datetime, datetime.datetime, list[str],
            str | None, str | None, int | None,
        ]
    ] = []
    for schedule in schedules:
        options = getattr(schedule, "options", None)
        if options is None or not options.enabled or options.deleted:
            continue
        # WEEKLY AND ONCE. Only WEEKLY was computed, so a one-off
        # schedule produced no occurrences and the calendar stayed "Off"
        # while it ran -- reported by @chairstacker after the ongoing-
        # occurrence fix, which was correct but never saw his schedule.
        #
        # ONCE is the shape an automation creates: run this, at this
        # time, not again.
        #
        # BI_WEEKLY and MONTHLY stay out. Both need a cadence anchor
        # nothing in the payload supplies -- for BI_WEEKLY, which of two
        # weeks is "this" one -- and neither has ever been seen on a real
        # account. Guessing an anchor would put a mission on the calendar
        # on a day the robot does not clean.
        if options.frequency not in (
            ScheduleFrequency.WEEKLY,
            ScheduleFrequency.ONCE,
        ):
            continue
        schedule_start = options.start
        if schedule_start is None:
            continue
        hour = schedule_start.hour or 0
        minute = schedule_start.min or 0

        region_ids: list[str] = []
        operating_mode: int | None = None
        if options.commands:
            first_command = options.commands[0]
            if isinstance(first_command, dict):
                # WHAT THE MISSION WILL DO, from the first region's
                # params. The app shows it above the room list, and a
                # schedule that mops is a different thing from one that
                # vacuums over the same rooms.
                #
                # Read from the first region rather than checked across
                # all of them: a mixed-mode schedule is possible on
                # paper, has never been seen, and the app shows one
                # label per mission.
                for _region in (first_command.get("regions") or []):
                    if isinstance(_region, dict):
                        operating_mode = (
                            _region.get("params") or {}
                        ).get("operatingMode")
                        break
                region_ids = [
                    rid for r in (first_command.get("regions") or []) if (rid := extract_region_id(r))
                ]

        # A ONCE SCHEDULE HAS EXACTLY ONE RUN, and it was being drawn as
        # a weekly one. @DaRealGuGu created a single cleaning from the
        # calendar; the robot stored it as ONCE and the iRobot app showed
        # it that way, while Home Assistant painted it on every Thursday
        # for the next two months.
        #
        # The frequency gate above lets ONCE through -- correctly, the
        # run does exist -- and then the generator below repeats it,
        # because that is all it knows how to do. Taking the first
        # occurrence is the whole fix: the robot fires it once, and the
        # server deletes the schedule within minutes afterwards
        # (@utkjmitch), so there is never a second one to draw.
        # WHAT THE MISSION WILL DO, from the first region's params. The
        # app shows it above the room list, and a schedule that mops is
        # a different thing from one that vacuums over the same rooms.
        #
        # Read from the first region rather than checked across all of
        # them: a mixed-mode schedule is possible on paper and has never
        # been seen, and one label per mission is what the app shows.
        # A ONCE SCHEDULE HAS A DATE, NOT A POSITION IN A WINDOW.
        #
        # Taking the first generated occurrence was wrong in a way that
        # only shows when you scroll: `_weekly_occurrences` yields runs
        # INSIDE the requested window, so `[:1]` is "the first matching
        # weekday you happen to be looking at". @DaRealGuGu saw the same
        # single event land on 31 July, 29 June and 10 August depending
        # on which month the calendar was showing.
        #
        # `options.after` is a real calendar date and is what a ONCE
        # schedule is anchored to. Only the occurrence on or after it
        # counts, and only once.
        once = options.frequency == ScheduleFrequency.ONCE
        # THE ANCHOR'S REAL FIELDS, read from the model rather than
        # guessed: ScheduleDateEntry carries `year`, `month` and
        # `day_of_month`. A first attempt looked for `date` and `day`,
        # found neither, and would have hidden every ONCE schedule
        # instead of placing it -- the same shape of error it was written
        # to fix.
        once_anchor = None
        if once:
            after = getattr(options, "after", None)
            year = getattr(after, "year", None)
            month = getattr(after, "month", None)
            day = getattr(after, "day_of_month", None)
            # Direct comparisons rather than `None not in (...)`: the
            # membership test is correct and narrows nothing, so the
            # int() calls below still read as taking Optionals.
            if year is not None and month is not None and day is not None:
                try:
                    once_anchor = datetime.date(int(year), int(month), int(day))
                except ValueError:
                    # An impossible date is not an anchor. Better no
                    # placement than a wrong one.
                    once_anchor = None
        for roomba_day in schedule_start.day or []:
            generated = _weekly_occurrences(roomba_day, hour, minute, start, end)
            if once:
                # Without an anchor the schedule cannot be placed, and
                # drawing it at an arbitrary spot is worse than not
                # drawing it: the server deletes a fired ONCE within
                # minutes anyway, so an unplaceable one is almost
                # certainly already spent.
                generated = [
                    o for o in generated if o.date() >= once_anchor
                ][:1] if once_anchor is not None else []
            for occurrence in generated:
                # THE SCHEDULE ID TRAVELS WITH THE OCCURRENCE. Without
                # it the calendar cannot give its events a uid, and
                # Home Assistant needs one to call delete or edit at
                # all -- so those handlers existed and were unreachable.
                results.append((
                    occurrence, occurrence + DEFAULT_EVENT_DURATION,
                    region_ids, options.name,
                    getattr(schedule, "schedule_id", None),
                    operating_mode,
                ))

    results.sort(key=lambda item: item[0])
    return results
