"""How good a time estimate is (4.6.0, B11).

An estimate of 12 minutes from one run and one from forty runs that never
varied by more than a minute read the same on a dashboard. This says
which is which, from three things Roomba+ has:

- RUNS: how many measurements the room's mean holds (room_times);
- SPREAD: their standard deviation, from the runs since 4.6.0 -- a
  figure learned before has a mean and a count but no record of how
  the runs varied, so its spread starts with the next run;
- THE PAST ERROR: what each mission was estimated to take at its start
  (`estimated_duration_min`, written since 4.6.0 by both generations)
  against what it took (`duration_min`). Per mission, not per room: no
  robot reports when it entered a room accurately enough to judge a
  room's estimate on its own.

`rating` is a summary for a card: `high` with five runs or more and a
spread within 15 % of the mean; `medium` with three or more and 30 %;
`low` otherwise; `none` without a measurement of our own (the figure is
then the cloud's, or a typical room, and has no runs to judge).
"""
from __future__ import annotations

from statistics import median
from typing import Any

from .room_times import measured_room_seconds, room_spread

#: How many recent missions the past error is taken from.
ERROR_WINDOW = 20


def room_quality(cache: dict[str, float], room: str, mode: str | None) -> dict[str, Any]:
    """`{"runs", "spread_sec", "rating"}` for one room in one mode."""
    runs, spread = room_spread(cache, room, mode)
    mean = measured_room_seconds(cache, room, mode)
    if not runs or not mean:
        return {"runs": 0, "spread_sec": None, "rating": "none"}
    share = spread / mean if spread is not None and mean else None
    if runs >= 5 and share is not None and share <= 0.15:
        rating = "high"
    elif runs >= 3 and share is not None and share <= 0.30:
        rating = "medium"
    else:
        rating = "low"
    return {
        "runs": runs,
        "spread_sec": round(spread, 1) if spread is not None else None,
        "rating": rating,
    }


def mission_estimate_error(records: Any, window: int = ERROR_WINDOW) -> dict[str, Any]:
    """How far the recent missions' estimates were off.

    `{"missions": n, "median_error_pct": x}` over the newest `window`
    missions that carry both an estimate and a duration; the error is
    signed per mission (positive: took longer than estimated) and the
    median is of its absolute value. `median_error_pct` None without any.
    """
    errors: list[float] = []
    for record in reversed(list(records or [])):
        if not isinstance(record, dict):
            continue
        estimate = record.get("estimated_duration_min")
        took = record.get("duration_min")
        if (
            isinstance(estimate, (int, float)) and not isinstance(estimate, bool) and estimate > 0
            and isinstance(took, (int, float)) and not isinstance(took, bool) and took > 0
        ):
            errors.append((float(took) - float(estimate)) / float(estimate) * 100.0)
        if len(errors) >= window:
            break
    return {
        "missions": len(errors),
        "median_error_pct": round(median(abs(e) for e in errors), 1) if errors else None,
    }
