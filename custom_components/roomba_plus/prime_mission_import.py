"""Filling the mission store from a Prime robot's own mission history.

WHAT THIS IS FOR. Four sensors -- clean streak, last mission, last
duration, area cleaned today -- read MissionStore, and only the Classic
path ever fills it. On a Prime robot they read "unknown" forever, which
@DaRealGuGu reported as four separate faults. They were one.

WHY IT CAN BE BUILT NOW. The endpoint is the same one Classic uses,
`GET /v1/{blid}/missionhistory`, and its shape is settled rather than
guessed:

  - the response is a BARE ARRAY. The app's own restservices package
    returns `Result<List<MissionHistory>>` with no envelope class in 63
    files. The `responseCode`/`responseBody` hull around the vendor's
    sample is the simulator's, not the server's.
  - every field of the vendor's own 20-entry sample maps onto
    MissionHistoryEntry, none left over.

So this is a translation between two confirmed shapes, not a guess about
either.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Prime's `done_raw` to the store's `result` vocabulary.
#:
#: THE STORE'S WORDS ARE LOAD-BEARING, not labels: clean_streak counts
#: only "completed" and "stuck_and_resumed", so a mission translated to
#: the wrong word silently breaks a streak or extends one.
#:
#: `usrEnd` is a user-ended mission, which is Classic's "cancelled" --
#: deliberate, not a failure. `ok` is a completed run. `stuck` keeps its
#: name in both vocabularies.
#:
#: Values seen in the vendor's sample: ok, stuck, usrEnd. Anything else
#: is passed through unchanged rather than forced into one of these --
#: an unrecognised outcome should look unrecognised.
_DONE_TO_RESULT: dict[str, str] = {
    "ok": "completed",
    "usrEnd": "cancelled",
    "stuck": "stuck",
    "cancelled": "cancelled",
}


def _iso(epoch: Any) -> str | None:
    """An epoch second as the ISO string the store stores.

    Milliseconds are not expected here -- the vendor's sample is in
    seconds throughout -- but a value past the year 2200 is read as
    milliseconds anyway, on the same threshold used for the live-map
    expiry. Cheap, and it fails loudly in the one direction that
    matters: a timestamp in 1970 would put every mission on the same day
    and quietly ruin "area cleaned today".
    """
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    if epoch <= 0:
        return None
    seconds = epoch / 1000.0 if epoch > 7_258_118_400 else float(epoch)
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def record_from_history_entry(entry: Any, blid: str) -> dict[str, Any] | None:
    """One MissionHistoryEntry as a MissionStore record.

    Returns None when the entry cannot be given a stable id, because
    `append_validated` rejects those anyway and a caller should be able
    to count what it lost.

    THE ID IS BUILT, NOT READ. The vendor's sample carries no
    `mission_id` at all -- it has `nMssn`, a per-robot counter, and
    `robot_id`. Combining them gives an id that is stable across
    re-imports (so re-reading the history does not duplicate anything)
    and unique across robots sharing one Home Assistant.

    DURATION IS `runM`, NOT `durationM`. Four minute fields exist and
    they are not interchangeable: `durationM` is wall clock, `runM` is
    time actually cleaning. A robot that returns to charge halfway
    through reports a wall-clock duration several times its cleaning
    time, and "last mission duration" means the second one. `durationM`
    is the fallback for entries that do not carry `runM`.
    """
    # `nMssn` IS NOT MODELLED, and this is where that bites. The first
    # version of this reached for `entry.number_of_missions`, a field
    # that does not exist -- so every entry would have been skipped for
    # want of an id, silently, and the four sensors would have stayed
    # exactly as dead as before.
    #
    # MissionHistoryEntry keeps the original payload on `.raw`, which is
    # the honest place to read a field the model chose not to name.
    raw = getattr(entry, "raw", None)
    nmssn = raw.get("nMssn") if isinstance(raw, dict) else None
    robot_id = getattr(entry, "robot_id", None) or blid
    mission_id = getattr(entry, "mission_id", None)
    if mission_id:
        record_id = str(mission_id)
    elif nmssn is not None and robot_id:
        record_id = f"{robot_id}_{nmssn}"
    else:
        return None

    started = _iso(getattr(entry, "start_time", None))
    ended = _iso(getattr(entry, "timestamp", None))
    running = getattr(entry, "minutes_running", None)
    duration = running if running is not None else getattr(entry, "duration_m", None)
    done = getattr(entry, "done_raw", None)

    return {
        "id": record_id,
        "started_at": started,
        "ended_at": ended,
        "duration_min": duration,
        "area_sqft": getattr(entry, "square_feet_covered", None),
        "error_code": getattr(entry, "error_code", None),
        "result": _DONE_TO_RESULT.get(done, done) if done else None,
        "initiator": getattr(entry, "initiator", None),
        # Not carried by this endpoint. Left absent rather than empty:
        # the store's own validator accepts null, and an empty list would
        # claim a mission cleaned no rooms.
        "zones": None,
    }


def records_from_history(entries: list[Any], blid: str) -> list[dict[str, Any]]:
    """Every entry that can be turned into a record, oldest first.

    Sorted because the store trims to a maximum and keeps what it was
    given last -- an unsorted import would decide which missions to keep
    by whatever order the server happened to answer in.
    """
    records = []
    skipped = 0
    for entry in entries or []:
        record = record_from_history_entry(entry, blid)
        if record is None:
            skipped += 1
            continue
        records.append(record)
    if skipped:
        _LOGGER.debug(
            "roomba_plus: %d mission history entries had no usable id and were "
            "skipped -- they carry neither a mission id nor a mission number",
            skipped,
        )
    return sorted(records, key=lambda r: r.get("started_at") or "")
