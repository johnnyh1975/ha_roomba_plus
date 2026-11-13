"""How a mission ended, as one event with a type (4.6.0, A5 and B3).

`roomba_plus_mission_completed` fires at every mission end with a
`result` field, and `event.<robot>_mission_completed` mirrors it with one
event type. An automation that wants "the run left rooms out" or "it got
stuck and gave up" has to read the payload and know the result values,
and the rooms a run skipped or never reached are in the stored record
but in no event at all (Thonno's skips, #178).

`roomba_plus_mission_result` carries the same mission with a type an
automation can pick in the editor, and the rooms:

- `completed`: done, every requested room cleaned as far as is known;
- `completed_with_skips`: done, but rooms were skipped by command or
  never reached;
- `cancelled`: stopped by someone, or a start that never happened
  (`blocked_timeout`);
- `stuck_abandoned`: stuck and not resumed;
- `error`: ended on an error.

Nothing that already exists changes: the mission-completed event and its
entity stay as they are (versionsplan: events only additive).

NOT REACHED is only claimed where the rooms the robot cleaned were
observed. A record whose rooms are still the request (`rooms_source`
`requested` or `awaiting_cloud`) says nothing about rooms not reached;
listing every requested room as missed would be a guess dressed as data.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    EVENT_MISSION_COMPLETED,
    EVENT_MISSION_RESULT,
    ROOMS_AWAITING_CLOUD,
    ROOMS_REQUESTED,
)

_LOGGER = logging.getLogger(__name__)

RESULT_TYPES: tuple[str, ...] = (
    "completed",
    "completed_with_skips",
    "cancelled",
    "stuck_abandoned",
    "error",
)

_GUESSED_ROOMS = frozenset({ROOMS_REQUESTED, ROOMS_AWAITING_CLOUD})


def mission_outcome(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """The result type of a stored mission and the rooms it left out.

    None for a mission whose result is not settled (`in_progress`), or
    is `unknown` without an error code: no type would be honest there.
    """
    if not isinstance(record, dict):
        return None
    result = record.get("result")
    skipped = [str(r) for r in record.get("skipped_rooms") or []]
    # PRIME (4.6.0): the regions of the start and the regions the
    # timeline cleaned, both as region ids; Classic keeps room names.
    requested = [
        str(r) for r in (record.get("zones") or record.get("requested_region_ids") or [])
    ]
    cleaned = {
        str(r) for r in (record.get("last_cleaned_rooms") or record.get("rooms_cleaned") or [])
    }
    observed = record.get("rooms_source") not in _GUESSED_ROOMS
    missed: list[str] = []
    if observed and record.get("scope") == "rooms" and cleaned:
        missed = [r for r in requested if r not in cleaned and r not in skipped]

    if result == "error" or (result == "unknown" and record.get("error_code")):
        kind = "error"
    elif result in ("stuck", "stuck_and_abandoned"):
        kind = "stuck_abandoned"
    elif result in ("cancelled", "blocked_timeout"):
        kind = "cancelled"
    elif result in ("completed", "stuck_and_resumed"):
        kind = "completed_with_skips" if skipped or missed else "completed"
    else:
        return None
    return {
        "type": kind,
        "result": result,
        "skipped_rooms": skipped,
        "missed_rooms": missed,
        "requested_rooms": requested,
        "error_code": record.get("error_code"),
    }


#: The result types that make a mission the robot's last error.
_ERROR_TYPES = frozenset({"error", "stuck_abandoned"})


def last_error(records: Any) -> tuple[Any, str | None] | None:
    """(error code, end time) of the newest mission that ended in error.

    The Prime source of `last_error_code` and `last_error_at` (4.6.0).
    Classic keeps both from its own mission end (callbacks.py), by the
    stored result; a Prime record has no `error` or `stuck` result, only
    `unknown` with the error the timeline raised, which `mission_outcome`
    already reads as an error. None when no mission ended that way.
    """
    for record in reversed(list(records or [])):
        outcome = mission_outcome(record)
        if outcome is not None and outcome["type"] in _ERROR_TYPES:
            return outcome["error_code"], record.get("ended_at")
    return None


def note_last_error(data: Any) -> None:
    """Set `last_error_code`/`last_error_at` from the stored missions.

    Leaves them as they are when no stored mission ended in error: the
    last error stays until there is a newer one, as on Classic.
    """
    store = getattr(data, "mission_store", None)
    found = last_error(getattr(store, "records", None))
    if found is None:
        return
    data.last_error_code, data.last_error_at = found


def named_rooms(data: Any, record: dict[str, Any] | None, rooms: list[str]) -> list[str]:
    """Room names for the rooms of `record`, as Classic records carry them.

    A Prime record keeps region ids (4.6.0); they are named from the
    maps (`prime_room_names`), and an id without a name stays an id.
    Classic records are returned as they are.
    """
    if not isinstance(record, dict) or record.get("zones") or not record.get("requested_region_ids"):
        return list(rooms)
    names = getattr(data, "prime_room_names", None)
    if not isinstance(names, dict):
        return list(rooms)
    return [str(names.get(r) or names.get(str(r)) or r) for r in rooms]


def rooms_to_retry(record: dict[str, Any] | None) -> list[str]:
    """Rooms a mission skipped or did not reach, in the order requested."""
    outcome = mission_outcome(record)
    if outcome is None:
        return []
    wanted = set(outcome["skipped_rooms"]) | set(outcome["missed_rooms"])
    ordered = [r for r in outcome["requested_rooms"] if r in wanted]
    ordered += [r for r in outcome["skipped_rooms"] if r not in ordered]
    return ordered


@callback
def async_setup_mission_result(hass: HomeAssistant, entry: Any) -> None:
    """Fire `roomba_plus_mission_result` after this robot's mission ends.

    Listens to the mission-completed event both generations fire after
    the record is stored (callbacks.py, prime_mission_sync.py). A Prime
    sync that added more than one mission (the first one after setup
    adds the whole history) is a backfill, not a mission that just
    ended, and gets no result event.
    """

    @callback
    def _on_completed(event: Event[Any]) -> None:
        data = dict(event.data or {})
        if data.get("entry_id") != entry.entry_id:
            return
        if (data.get("missions_added") or 1) != 1:
            return
        store = getattr(getattr(entry, "runtime_data", None), "mission_store", None)
        mission_id = data.get("mission_id")
        record = store.find_by_id(mission_id) if store is not None and mission_id else None
        outcome = mission_outcome(record)
        if outcome is None:
            return
        runtime = getattr(entry, "runtime_data", None)
        for key in ("skipped_rooms", "missed_rooms", "requested_rooms"):
            outcome[key] = named_rooms(runtime, record, outcome[key])
        hass.bus.async_fire(EVENT_MISSION_RESULT, {
            "entry_id": entry.entry_id,
            "name": getattr(entry, "title", None),
            "mission_id": mission_id,
            **outcome,
        })

    entry.async_on_unload(hass.bus.async_listen(EVENT_MISSION_COMPLETED, _on_completed))
