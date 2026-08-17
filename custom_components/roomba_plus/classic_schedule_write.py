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

_LEGACY_KEY = "cleanSchedule"
_MODERN_KEY = "cleanSchedule2"


class ScheduleFormatError(ValueError):
    """The requested schedule cannot be expressed on this robot."""


def schedule_key(reported: dict[str, Any]) -> str | None:
    """Which key this robot keeps its schedule under, or None.

    Order matters: a robot reporting both should be written under the
    modern one, because that is the one its app maintains.
    """
    for key in (_MODERN_KEY, _LEGACY_KEY):
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
