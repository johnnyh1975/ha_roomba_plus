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
