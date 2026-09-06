"""Writing a Classic robot's own cleaning schedule.

FIELD-CONFIRMED on a 900-series: the schedule was read, written back
unchanged, read again identical, and the iRobot app still showed it
afterwards. That last step is the one that matters -- this project has a
setting which accepts a write, reads back changed, and is ignored
entirely, so "the robot took it" is not the same as "it works".

TWO KEYS, AND THE ROBOT PICKS. Older firmware uses `cleanSchedule`;
anything with room support uses `cleanSchedule2`. Writing under the
wrong one would not replace the schedule, it would create a second,
competing one -- so the key the robot reported is the key it gets back.

WHAT THE LEGACY FORMAT CANNOT DO, and therefore what a caller must
refuse rather than quietly approximate:

    one entry per weekday    no second cleaning on the same day
    no frequency             no ONCE, no fortnightly, no monthly
    no rooms                 whole house only
    no name                  the schedule has no label at all

Prime's format carries all four. Approximating any of them here would
mean storing something the user did not ask for, on a robot that will
then act on it.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Sunday first, as the robot indexes it.
_DAYS = 7

#: PUBLIC, because callers have to branch on which one a robot uses --
#: the two keys hold different TYPES (object vs array), and treating
#: them alike is what made the modern path unwritable.
LEGACY_KEY = "cleanSchedule"
MODERN_KEY = "cleanSchedule2"


class ScheduleFormatError(ValueError):
    """The requested schedule cannot be expressed on this robot."""


def schedule_key(reported: dict[str, Any]) -> str | None:
    """Which key this robot keeps its schedule under, or None.

    Order matters: a robot reporting both should be written under the
    modern one, because that is the one its app maintains.
    """
    for key in (MODERN_KEY, LEGACY_KEY):
        if isinstance(reported.get(key), (dict, list)):
            return key
    return None


def legacy_with_entry(
    current: dict[str, Any],
    *,
    weekday: int,
    hour: int,
    minute: int,
    enabled: bool = True,
) -> dict[str, Any]:
    """The legacy schedule with one weekday set, everything else kept.

    A REPLACEMENT FOR THAT DAY, not an addition. The format holds one
    entry per weekday and has nowhere to put a second, so setting a day
    that already has a cleaning overwrites it. A caller that means to
    add rather than replace has to check first -- there is no way to
    express both.

    Missing or short arrays are padded rather than rejected: a robot
    that has never had a schedule may report empty ones, and refusing
    would make the first schedule the one case that cannot be created.
    """
    if not 0 <= weekday < _DAYS:
        raise ScheduleFormatError(f"weekday {weekday} is outside 0-6")
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ScheduleFormatError(f"{hour:02d}:{minute:02d} is not a time of day")

    def _padded(values: Any, fill: Any) -> list[Any]:
        out = list(values) if isinstance(values, list) else []
        return (out + [fill] * _DAYS)[:_DAYS]

    cycle = _padded(current.get("cycle"), "none")
    hours = _padded(current.get("h"), 0)
    minutes = _padded(current.get("m"), 0)

    cycle[weekday] = "start" if enabled else "none"
    hours[weekday] = int(hour)
    minutes[weekday] = int(minute)
    return {"cycle": cycle, "h": hours, "m": minutes}


def legacy_without_day(current: dict[str, Any], weekday: int) -> dict[str, Any]:
    """The legacy schedule with one weekday switched off.

    The hour and minute are LEFT AS THEY WERE rather than zeroed. A day
    with `cycle` set to "none" does not run whatever the time says, and
    keeping the time means someone re-enabling that day gets their old
    setting back instead of midnight.
    """
    if not 0 <= weekday < _DAYS:
        raise ScheduleFormatError(f"weekday {weekday} is outside 0-6")
    updated = legacy_with_entry(
        current,
        weekday=weekday,
        hour=int((current.get("h") or [0] * _DAYS)[weekday] or 0),
        minute=int((current.get("m") or [0] * _DAYS)[weekday] or 0),
        enabled=False,
    )
    return updated


def reject_unsupported(
    *, frequency: str | None, rooms: list[str] | None, name: str | None
) -> None:
    """Refuses what the legacy format cannot hold.

    Called before anything is written, so the user gets an error instead
    of a schedule that silently means something else. The alternative --
    dropping the parts that do not fit -- would put a robot on the floor
    at a time or in a room nobody chose.
    """
    if frequency and frequency.upper() not in ("", "WEEKLY"):
        raise ScheduleFormatError(
            f"This robot's schedule format only repeats weekly, so "
            f"{frequency} cannot be stored. It has one entry per weekday and "
            "no frequency field at all."
        )
    if rooms:
        raise ScheduleFormatError(
            "This robot's schedule format has no room selection -- a scheduled "
            "mission always cleans everywhere. Start a room clean from the "
            "vacuum entity instead."
        )
    if name:
        _LOGGER.debug(
            "roomba_plus: the legacy schedule format has no name field; %r is "
            "not stored", name,
        )


#: The scheduler refuses an array longer than this outright, with
#: `'cleanSchedule2' array size > %u` -- the immediate `mov ip, #0x15`
#: puts %u at 21. The refusal sits in the validation path before the
#: schedule is taken, so the whole write is very likely rejected rather
#: than the surplus entry dropped. "Very likely": read from the string
#: layout, not proven from the control flow, which is why the guard
#: below raises instead of trimming.
_SCHEDULE2_MAX_ENTRIES = 21

#: `type` is not "one-off vs repeating" -- it selects the command
#: FORMAT. 0 is the `cmd` object form; the parallel `cmdStr` branch
#: exists and answers `cmdStr is not supported`, and a third value gets
#: `Cannot format JSON object. Unknown schedule type`. So 0 is not a
#: default here, it is the only value that works.
_SCHEDULE2_CMD_FORMAT = 0


def schedule2_entry(
    *,
    weekdays: list[int],
    hour: int,
    minute: int,
    enabled: bool = True,
) -> dict[str, Any]:
    """One `cleanSchedule2` entry, in the shape the scheduler validates.

    EVERY FIELD HERE IS REQUIRED, each with its own rejection message in
    the firmware: `Missing 'type' field`, `Missing 'enabled' field`,
    `Missing 'cmd' and 'cmdStr' field`, and for the start object
    `Expected 'day' attribute`, `'hour' must be an integer [0-23]`,
    `'min' must be an integer [0-59]`. An entry missing any of them
    fails validation with a device log line and nothing on the wire --
    which is how this format went unnoticed as unwritable.

    `cmd.command` is the one optional part: the scheduler fills in
    `start` itself when it is absent (`No command attribute, defaulting
    to {"command":"start"}`). It is written explicitly anyway, because
    a payload that says what it means outlives the default.

    SEVERAL DAYS PER ENTRY ARE ALLOWED. `day` is an array and the only
    constraints are that it exists and is not empty, so a weekly
    cleaning on three days is one entry rather than three.
    """
    if not weekdays:
        raise ScheduleFormatError("A schedule entry needs at least one weekday.")
    for day in weekdays:
        if not 0 <= day < _DAYS:
            raise ScheduleFormatError(f"weekday {day} is outside 0-6")
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ScheduleFormatError(f"{hour:02d}:{minute:02d} is not a time of day")

    return {
        "enabled": bool(enabled),
        "type": _SCHEDULE2_CMD_FORMAT,
        "start": {
            "day": [int(d) for d in sorted(set(weekdays))],
            "hour": int(hour),
            "min": int(minute),
        },
        "cmd": {"command": "start"},
    }


def schedule2_with_entry(
    current: list[Any],
    *,
    weekday: int,
    hour: int,
    minute: int,
) -> list[Any]:
    """The whole schedule with one weekday's cleaning set.

    RETURNS THE COMPLETE LIST, because a write replaces it. The
    scheduler stores the schedule as one object under `robot.schedule`
    and reloads it whole; there is no merge, and `[]` clears everything.
    Sending only the changed entry would delete the others -- the same
    semantics as `set_virtual_wall`, learned the same expensive way.

    ENTRIES THIS CODE DOES NOT UNDERSTAND ARE CARRIED THROUGH
    UNCHANGED. A robot may hold entries created in the iRobot app with
    fields nothing here models -- several days at once, a command other
    than `start`, something not yet seen. Dropping them because they do
    not round-trip would delete a user's schedule to make ours fit.

    One weekday is replaced rather than added, matching the legacy
    behaviour and the calendar above it: a day that already has a
    cleaning gets the new time.
    """
    entry = schedule2_entry(weekdays=[weekday], hour=hour, minute=minute)

    kept: list[Any] = []
    for existing in current if isinstance(current, list) else []:
        if not isinstance(existing, dict):
            kept.append(existing)
            continue
        days = ((existing.get("start") or {}).get("day")) or []
        # KEEP FIRST, DROP ONLY ON A MATCH. An earlier version asked
        # whether anything REMAINED after removing this weekday, which
        # answered "no" for an entry that never had days at all -- one
        # with no `start` object, say -- and silently deleted it. Losing
        # an entry we merely failed to understand is the exact outcome
        # this function exists to prevent, and its own test caught it.
        if not isinstance(days, list) or weekday not in days:
            kept.append(existing)
            continue
        remaining = [d for d in days if d != weekday]
        if not remaining:
            continue          # that entry was only this day -- replaced
        if len(remaining) != len(days):
            # It covered other days too. Keep those, drop only ours,
            # rather than overwriting a multi-day entry the user may
            # have made in the app.
            trimmed = dict(existing)
            trimmed["start"] = {**(existing.get("start") or {}), "day": remaining}
            kept.append(trimmed)
            continue
        kept.append(existing)

    result = [*kept, entry]
    if len(result) > _SCHEDULE2_MAX_ENTRIES:
        raise ScheduleFormatError(
            f"This robot holds at most {_SCHEDULE2_MAX_ENTRIES} schedule "
            f"entries and already has {len(kept)}. Remove one before adding "
            "another."
        )
    return result


def schedule2_without_day(current: list[Any], weekday: int) -> list[Any]:
    """The whole schedule with one weekday removed.

    Removed rather than disabled. `enabled: false` does keep an entry
    and skip it (`Scheduled entry exists but is disabled...continuing
    search`), which is a real and useful state -- but the calendar above
    this deletes events, and an event the user deleted should not come
    back as a disabled one nobody can see.
    """
    if not 0 <= weekday < _DAYS:
        raise ScheduleFormatError(f"weekday {weekday} is outside 0-6")

    kept: list[Any] = []
    for existing in current if isinstance(current, list) else []:
        if not isinstance(existing, dict):
            kept.append(existing)
            continue
        days = ((existing.get("start") or {}).get("day")) or []
        if not isinstance(days, list) or weekday not in days:
            kept.append(existing)
            continue
        remaining = [d for d in days if d != weekday]
        if remaining:
            trimmed = dict(existing)
            trimmed["start"] = {**(existing.get("start") or {}), "day": remaining}
            kept.append(trimmed)
    return kept
