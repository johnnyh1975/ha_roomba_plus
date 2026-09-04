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


def _regions(response: Any) -> list[Any]:
    """The per-room entries, wherever the response puts them.

    `CleanScoreDto` carries `regions` DIRECTLY -- there is no wrapping
    list. A first version of this module looked for `clean_scores` first,
    a level that appears nowhere in iRobot's own model, and therefore
    found nothing on a real response while every test passed: the
    fixtures were written to match the invented shape.

    The nested form is still accepted because a multi-map account may
    return one document per map, and tolerating both costs one line.
    """
    direct = _get(response, "regions")
    if direct:
        return list(direct)
    out: list[Any] = []
    for entry in _get(response, "clean_scores") or []:
        out.extend(_get(entry, "regions") or [])
    return out


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
    for region in _regions(response):
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


def _mission_number(info: Any) -> Any:
    """The mission number out of a mission-info object.

    `mission_last_cleaned` AND `mission_last_processed` are
    `SmartCleanMissionInfoDto` objects -- `startTime`, `nMssn`,
    `missionId` -- exactly like `mission_last_unfinished`. This module
    labelled the first as if it were already a number, so a caller
    reading `last_cleaned_mission` got a dict where an integer was
    promised.

    A plain number is accepted too: no capture has shown one, and
    rejecting it would trade a working value for a tidy type.
    """
    if isinstance(info, (int, float)):
        return info
    return _get(info, "nMssn") if info else None


def room_details(response: Any) -> dict[str, dict[str, Any]]:
    """Everything the robot says about each room, not just its score.

    `SmartCleanRegionDto` carries three fields this module ignored while
    reading only `clean_score`:

        high_traffic_enum       the robot's own traffic banding
        mission_last_cleaned    which mission last did this room
        mission_last_unfinished which one left it undone

    They cost nothing to expose and answer questions the score cannot:
    a room can be clean because nobody walks through it, or clean
    because it was done an hour ago, and the score alone does not
    distinguish those.
    """
    out: dict[str, dict[str, Any]] = {}
    for region in _regions(response):
        region_id = _get(region, "region_id")
        if region_id is None:
            continue
        out[str(region_id)] = {
            "clean_score": _get(region, "clean_score"),
            "high_traffic": _get(region, "high_traffic_enum"),
            "last_cleaned_mission": _mission_number(
                _get(region, "mission_last_cleaned")
            ),
            "unfinished_mission": _get(region, "mission_last_unfinished"),
            "updated_ts": _get(region, "updated_ts"),
            "updated_by": _get(region, "last_updated_by"),
            # `smart_clean_prefs` -- THIS ROOM'S OWN CLEANING SETTINGS.
            #
            # Typed `RegionParamsDTO` in `CleanScoreDto`, so it carries
            # the same eight keys a region command does: operatingMode,
            # suctionLevel, padWetness, twoPass and the rest.
            #
            # That makes it the server's record of "always mop the
            # kitchen" -- a per-room preference this integration has no
            # other way to see, and which explains why a room can clean
            # differently from the robot's global settings.
            #
            # Empty on every capture so far, so nothing is built on it;
            # it is carried so a robot that does use it is not silently
            # ignored.
            "preferences": _get(region, "smart_clean_prefs") or None,
        }
    return out


def response_error(response: Any) -> str | None:
    """`CleanScoreDto.error`, which nothing was reading.

    A cloud that answers with an error object rather than an HTTP
    failure looks like a successful call returning no dirty rooms --
    which is exactly the shape of "nothing needs cleaning".
    """
    error = _get(response, "error")
    if not error:
        return None
    if isinstance(error, str):
        return error
    return _get(error, "description") or str(error)


def unfinished_missions(response: Any) -> dict[str, dict[str, Any]]:
    """Which mission left each room unfinished, not just that one did.

    `mission_last_unfinished` is a structured object, not a flag:
    `CleanScoreDto$SmartCleanMissionInfoDto` declares `startTime`,
    `nMssn` and `missionId`. This module was reading it for truthiness
    alone.

    With the mission number a caller can say "the kitchen was left
    undone by mission 61, and 62 has since run" -- which is the
    difference between a room that is still waiting and one that was
    picked up on the next pass.
    """
    out: dict[str, dict[str, Any]] = {}
    for region in _regions(response):
        info = _get(region, "mission_last_unfinished")
        region_id = _get(region, "region_id")
        if not info or region_id is None:
            continue
        out[str(region_id)] = {
            "mission_id": _get(info, "missionId"),
            "mission_number": _get(info, "nMssn"),
            "start_time": _get(info, "startTime"),
        }
    return out


def unfinished_rooms(response: Any) -> list[str]:
    """Rooms whose last mission did not complete them.

    A separate question from dirtiness and one nothing else answers.
    @chairstacker reported a mission that failed on a blocked door and
    left no trace anywhere; this is where that shows up per room.
    """
    out: list[str] = []
    for region in _regions(response):
        if _get(region, "mission_last_unfinished"):
            region_id = _get(region, "region_id")
            if region_id is not None:
                out.append(str(region_id))
    return out
