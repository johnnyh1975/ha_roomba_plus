"""How much battery a mission used (4.6.0, B12).

THE BATTERY AT THE START AND AT THE END, nothing more. The robots report
the level as a percentage (`batPct`) on both generations; neither reports
an energy figure per mission. So the use of a mission is the drop in
percent, and per m² that drop over the area the mission reports.

A MISSION THAT RECHARGED HAS NO FIGURE. Its end level says nothing about
what it used: a robot that went home at 15 %, charged to 80 % and
finished at 60 % used far more than 20 points. `battery_used_pct` is
None there, with `battery_recharged: true`, rather than a number that
looks measured. Classic knows a recharge from the mission's recharge
minutes; Prime from the level rising during the mission.

NOT ACROSS A RESTART. The start level is kept in memory: a mission that
began before Home Assistant restarted has no start level and no figure.

Fields on the stored mission: `battery_start_pct`, `battery_end_pct`,
`battery_used_pct`, `battery_recharged`, `battery_used_pct_per_m2`.
"""
from __future__ import annotations

from typing import Any

SQFT_TO_M2 = 0.09290304

#: How many finished Prime missions keep their figures until the history
#: sync stores them. The sync runs after every mission end.
_PRIME_DONE_KEEP = 20


def _level(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    level = int(value)
    return level if 0 <= level <= 100 else None


def note_start(data: Any, level: Any, number: Any = None) -> None:
    """A mission began at this battery level."""
    start = _level(level)
    data.mission_battery = (
        None if start is None
        else {"number": number, "start": start, "last": start, "recharged": False}
    )


def note_level(data: Any, level: Any) -> None:
    """A level seen during the mission. A rise is a recharge (Prime)."""
    state = getattr(data, "mission_battery", None)
    now = _level(level)
    if not isinstance(state, dict) or now is None:
        return
    if now > state["last"]:
        state["recharged"] = True
    state["last"] = now


def finish(data: Any, level: Any, *, recharged: bool = False) -> dict[str, Any]:
    """The battery fields of the mission ending now, and forget its start.

    Every field is there; the figures are None where they are not known.
    """
    state = getattr(data, "mission_battery", None)
    data.mission_battery = None
    end = _level(level)
    start = state.get("start") if isinstance(state, dict) else None
    charged = bool(recharged or (isinstance(state, dict) and state.get("recharged")))
    used = (
        start - end
        if start is not None and end is not None and not charged and start >= end
        else None
    )
    return {
        "battery_start_pct": start,
        "battery_end_pct": end,
        "battery_used_pct": used,
        "battery_recharged": charged if start is not None else None,
    }


def per_m2(record: dict[str, Any]) -> float | None:
    """Percentage points per m² of the area the mission reports, or None."""
    used = record.get("battery_used_pct")
    sqft = record.get("area_sqft")
    if (
        not isinstance(used, (int, float)) or isinstance(used, bool)
        or not isinstance(sqft, (int, float)) or isinstance(sqft, bool) or sqft <= 0
    ):
        return None
    return round(float(used) / (float(sqft) * SQFT_TO_M2), 3)


def apply(record: dict[str, Any], fields: dict[str, Any]) -> None:
    """Put the battery fields on a mission record, with the per-m² figure."""
    record.update(fields)
    record["battery_used_pct_per_m2"] = per_m2(record)


def keep_prime(data: Any, number: Any, fields: dict[str, Any]) -> None:
    """Hold a finished Prime mission's figures for the history sync,
    which stores the mission later and matches it by its number.

    The battery fields, and whatever else only the running mission knew
    (`estimated_duration_min`, estimate_quality.py): the record is built
    from the cloud's history, minutes later, when that is gone."""
    if number is None:
        return
    done = getattr(data, "prime_mission_end_figures", None)
    if not isinstance(done, dict):
        done = {}
        data.prime_mission_end_figures = done
    done[str(number)] = dict(fields)
    while len(done) > _PRIME_DONE_KEEP:
        done.pop(next(iter(done)))


def take_prime(data: Any, record: dict[str, Any]) -> None:
    """Put the held figures on a Prime record of the same mission number."""
    done = getattr(data, "prime_mission_end_figures", None)
    number = record.get("nMssn")
    if not isinstance(done, dict) or number is None:
        return
    fields = done.pop(str(number), None)
    if isinstance(fields, dict):
        apply(record, fields)
