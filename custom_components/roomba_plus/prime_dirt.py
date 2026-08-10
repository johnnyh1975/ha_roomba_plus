"""Which rooms a Prime robot considers dirty.

CLASSIC GUESSES; PRIME IS TOLD. The Classic path derives dirt density
from cloud mission records -- dirt events divided by area cleaned --
builds a weekday baseline from at least four of them, and triggers when
today exceeds that baseline by a configurable multiplier. Every part of
that is an inference from what a mission produced.

A Prime robot reports the answer directly. `/v1/p2maps/clean-score`
returns a value per room, and the response carries its own threshold in
`clean_score_ranges`.

**HIGHER MEANS DIRTIER.** Settled by an eleven-room account (@jouwdan)
after a four-room one had looked contradictory: rooms cleaned by the
newest mission read exactly `0.0` with `last_updated_by:
batch_decay_skipped`, while a room untouched for twenty missions read
`0.6973` against a `0.7` threshold. A four-room account had two rooms
sharing a mission and differing anyway -- room size and traffic move the
rate, not the direction.

**THE THRESHOLD COMES FROM THE SERVER, not from us.** Classic needs a
multiplier because its baseline is our own arithmetic; here the number
to beat arrives in the same response as the values. A robot that stops
sending one gets no decision rather than a guessed one -- an automation
that cleans a room nobody asked about is worse than one that stays
quiet.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Used only when the response carries no `clean_score_ranges`. Observed
#: on every capture so far, so this is a floor rather than a default --
#: and it is deliberately at the top of the observed range, because
#: erring towards "not dirty" costs a delayed clean while erring the
#: other way sends a robot into a room somebody is sitting in.
_FALLBACK_THRESHOLD = 0.7


def _get(obj: Any, name: str) -> Any:
    """A field, whether the response is parsed or still a plain dict.

    The library offers `get_clean_score_raw()` and no parsed variant, so
    this reads dicts. It also reads objects, because
    `CleanScoreResponse` exists and a later parsed call should not need
    this module rewritten.
    """
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def dirty_rooms(response: Any) -> list[tuple[str, float]]:
    """Rooms at or past the threshold, dirtiest first.

    Empty when the response cannot be read, which is the same answer as
    "nothing is dirty" to a caller and deliberately so: both mean do
    nothing, and distinguishing them would only be useful if we intended
    to act on a failure.
    """
    if response is None:
        return []

    threshold = _FALLBACK_THRESHOLD
    ranges = _get(response, "clean_score_ranges")
    if isinstance(ranges, list) and ranges:
        try:
            threshold = float(ranges[0])
        except (TypeError, ValueError):
            threshold = _FALLBACK_THRESHOLD

    dirty: list[tuple[str, float]] = []
    for entry in _get(response, "clean_scores") or []:
        for region in _get(entry, "regions") or []:
            score = _get(region, "clean_score")
            region_id = _get(region, "region_id")
            if region_id is None or not isinstance(score, (int, float)):
                continue
            if score >= threshold:
                dirty.append((str(region_id), float(score)))

    # Dirtiest first, so a caller that only wants one room gets the room
    # that most needs it.
    dirty.sort(key=lambda item: item[1], reverse=True)
    return dirty


def unfinished_rooms(response: Any) -> list[str]:
    """Rooms whose last mission did not complete them.

    A separate question from dirtiness and one nothing else answers.
    @chairstacker reported a mission that failed on a blocked door and
    left no trace anywhere; this is where that shows up per room.
    """
    out: list[str] = []
    for entry in _get(response, "clean_scores") or []:
        for region in _get(entry, "regions") or []:
            if _get(region, "mission_last_unfinished"):
                region_id = _get(region, "region_id")
                if region_id is not None:
                    out.append(str(region_id))
    return out
