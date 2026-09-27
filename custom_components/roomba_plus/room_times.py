"""How long a room takes -- learned, per cleaning mode (4.2.15).

WHY THIS MODULE EXISTS. The per-room figures behind mission progress,
the room display and the room-change gate came from two places: the
cloud's own estimates, filed only by one or two passes, and what room
tracking measured when it saw the robot move to the next room. A Braava
gives neither. Its commands carry no pass setting the cloud estimates are
filed under, and it sends no travel signal, so room tracking almost never
sees a room change. @ScenicSystemsLLC's Braava m6 fell back to the
average of all recent missions -- about 68 minutes whatever the number of
rooms -- and a five-room mop run read 99 % after an hour of a two and a
half hour clean.

THE CLOUD'S ROOM EVENTS HAVE THE ANSWER for every robot, after every
mission: each `room` event carries the time the robot started on that
room (`ts`), and the next room or drive starts where it ends. That is
learned here, per room and per cleaning mode.

`ts` IS THE START OF THE ROOM. One captured cloud payload (PyRoomba, see
MISSIONSTORE_FIELD_REGISTRY.md) has start 1696660066, room 1696660084,
travel 1696660945: the room event is stamped 18 s after the mission
started and the drive home 861 s later. `ets` on the same events is a
constant mission-end reference, not a per-event end, and is not used.

PER MODE, because a mop pass and a vacuum pass over the same floor are
not the same job. Measurements have been stored per mode since 4.1, but
the reader took the smallest figure across all of them, so on a Combo a
vacuum time could stand in for a mop run. The mode is read from
`operatingMode`'s bits (2 vacuum, 4 mop, 32 combo; bit 0 is travel and
says nothing about the job).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .const import (
    MIN_CLEANED_ROOM_SHARE,
    ROOM_EVENT_DONE_STATUSES,
    is_braava,
    is_mop,
    room_event_covered_share,
)

#: A room taking less than this is not a room clean (a doorway strip, a
#: skipped room); more than this is a stuck robot or a lost event.
MIN_ROOM_SECONDS: float = 30.0
MAX_ROOM_SECONDS: float = 3 * 3600.0

#: Events that end the room before them: the next room, or a drive
#: (to the next room, to the dock, to recharge or empty the bin).
_BOUNDARY_TYPES: frozenset[str] = frozenset({"room", "travel", "charge", "evac"})

#: How many learned missions are remembered, to never count one twice.
#: Far more than the cloud's mission list ever returns at once.
MAX_LEARNED_KEYS = 1000


def cleaning_mode(operating_mode: Any) -> str | None:
    """"vacuum", "mop", "vacuum_mop" or None from an `operatingMode`
    bitmask. Travel (bit 0) is ignored: it is where the robot is going,
    not what it does there."""
    if isinstance(operating_mode, bool) or not isinstance(operating_mode, int):
        return None
    if operating_mode & 32 or (operating_mode & 2 and operating_mode & 4):
        return "vacuum_mop"
    if operating_mode & 4:
        return "mop"
    if operating_mode & 2:
        return "vacuum"
    return None


def _key_mode(suffix: str) -> str | None:
    """The cleaning mode a stored key was written for. Keys from 4.2.14
    and earlier carry the raw `operatingMode` number, or "unknown"."""
    if suffix in ("vacuum", "mop", "vacuum_mop"):
        return suffix
    try:
        return cleaning_mode(int(suffix))
    except ValueError:
        return None


def measured_key(room: str, mode: str | None) -> str:
    return f"{room}|measured|{mode or 'unknown'}"


def remember(cache: dict[str, float], room: str, seconds: float, mode: str | None) -> None:
    """Fold one measurement into the running mean for this room and mode.
    Stored unrounded; readers round."""
    key = measured_key(room, mode)
    count_key = f"{key}|count"
    n = int(cache.get(count_key, 0) or 0)
    mean = float(cache.get(key, 0.0) or 0.0)
    if n > 0 and mean > 0:
        mean = (mean * n + float(seconds)) / (n + 1)
        n += 1
    else:
        mean, n = float(seconds), 1
    cache[key] = mean
    cache[count_key] = float(n)


def _measurements(cache: dict[str, float], room: str | None = None) -> list[tuple[str, str | None, float, float]]:
    """(room, mode, mean seconds, count) for every measured mean in the
    cache. Keys written before counts existed count once."""
    found: list[tuple[str, str | None, float, float]] = []
    for key, value in cache.items():
        if key.endswith("|count") or "|measured|" not in key:
            continue
        name, _, suffix = key.partition("|measured|")
        if room is not None and name != room:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            count = cache.get(f"{key}|count", 1.0)
            weight = float(count) if isinstance(count, (int, float)) and count > 0 else 1.0
            found.append((name, _key_mode(suffix), float(value), weight))
    return found


def _weighted(rows: list[tuple[str, str | None, float, float]]) -> float:
    total = sum(w for _r, _m, _s, w in rows)
    return sum(s * w for _r, _m, s, w in rows) / total


def measured_room_seconds(cache: dict[str, float], room: str, mode: str | None) -> float | None:
    """What this room took before, in this cleaning mode if known.

    Same mode first, as one mean over every bucket of that mode, weighted
    by how many runs each holds -- a 4.2.14 key ("2", "3") and a named one
    ("vacuum") are the same job and are averaged, not raced. Without a
    measurement in this mode, the smallest figure of any mode, the rule
    before 4.2.15.
    """
    rows = _measurements(cache, room)
    if not rows:
        return None
    if mode is not None:
        same = [r for r in rows if r[1] == mode]
        if same:
            return _weighted(same)
    return min(s for _r, _m, s, _w in rows)


def typical_room_seconds(cache: dict[str, float], mode: str | None) -> float | None:
    """The mean of every room this robot has been measured in, in this
    mode if any -- the figure for a room it has no measurement of yet.

    Replaces, where it exists, the average of whole missions divided by
    the number of planned rooms, which knows nothing about rooms: a
    one-room run and a whole-house run weigh the same in it."""
    rows = _measurements(cache)
    if not rows:
        return None
    same = [r for r in rows if mode is not None and r[1] == mode]
    pool = same or rows
    per_room: dict[str, list[tuple[str, str | None, float, float]]] = {}
    for row in pool:
        per_room.setdefault(row[0], []).append(row)
    means = [_weighted(r) for r in per_room.values()]
    return sum(means) / len(means)


def current_cleaning_mode(state: dict[str, Any]) -> str | None:
    """The cleaning mode of the mission the robot is on or about to run.

    The mission status first; then the last command, whose regions may
    carry their own; then what the robot is -- a Braava only mops.
    """
    cms = state.get("cleanMissionStatus") if isinstance(state, dict) else None
    mode = cleaning_mode((cms or {}).get("operatingMode")) if isinstance(cms, dict) else None
    if mode:
        return mode
    last = state.get("lastCommand") if isinstance(state, dict) else None
    if isinstance(last, dict):
        mode = cleaning_mode(last.get("operatingMode"))
        if mode:
            return mode
        for region in last.get("regions") or []:
            if isinstance(region, dict):
                mode = cleaning_mode((region.get("params") or {}).get("operatingMode"))
                if mode:
                    return mode
    if isinstance(state, dict) and is_braava(state):
        return "mop"
    return None


def robot_default_mode(state: dict[str, Any]) -> str | None:
    """What a recorded mission of this robot did when the record does not
    say: a Braava mops, a robot without a pad vacuums, a Combo could have
    done either."""
    if not isinstance(state, dict) or not state:
        return None   # nothing known about the robot: no guess
    if is_braava(state):
        return "mop"
    if not is_mop(state):
        return "vacuum"
    return None


def record_cleaning_mode(rec: dict[str, Any], default: str | None) -> str | None:
    """The cleaning mode of a recorded mission: its start command's
    `operatingMode` if the cloud kept it, else `default` (what the robot
    is -- a Braava mops, a robot without a pad vacuums)."""
    cmd = rec.get("cmd")
    if isinstance(cmd, dict):
        mode = cleaning_mode(cmd.get("operatingMode"))
        if mode:
            return mode
        for region in cmd.get("regions") or []:
            if isinstance(region, dict):
                mode = cleaning_mode((region.get("params") or {}).get("operatingMode"))
                if mode:
                    return mode
    return default


def _record_end(rec: dict[str, Any]) -> float | None:
    """When the mission ended, as Unix time: `timestamp` on a cloud
    record, `ended_at` on one of ours."""
    end = rec.get("timestamp")
    if isinstance(end, (int, float)) and not isinstance(end, bool):
        return float(end)
    ended = rec.get("ended_at")
    if isinstance(ended, str):
        try:
            parsed = datetime.fromisoformat(ended)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def timeline_room_seconds(rec: dict[str, Any]) -> dict[str, float]:
    """{region id: seconds} for one mission, from the cloud's room events.

    A room runs from its event's `ts` to the next room, drive, recharge
    or bin run -- or, for the last room, to the mission's end (`timestamp`
    on a cloud record). Several passes over one room add up. Only rooms
    with a finished pass (status 0 or 6) that covered at least
    MIN_CLEANED_ROOM_SHARE count; figures outside
    MIN_ROOM_SECONDS..MAX_ROOM_SECONDS are dropped.
    """
    timeline = rec.get("timeline")
    if not isinstance(timeline, dict):
        return {}
    events: list[tuple[float, dict[str, Any]]] = []
    for ev in timeline.get("finEvents") or []:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        events.append((float(ts), ev))
    # NOT ALWAYS IN ORDER: the i3+ fixture lists its events newest first.
    events.sort(key=lambda pair: pair[0])

    mission_end = _record_end(rec)

    spent: dict[str, float] = {}
    cleaned: set[str] = set()
    for index, (ts, ev) in enumerate(events):
        if ev.get("type") != "room":
            continue
        room = ev.get("room") or {}
        if not isinstance(room, dict):
            continue
        rid = str(room.get("rid", "") or "")
        if not rid:
            continue
        # A FINISHED PASS OVER A ROOM THE ROBOT REALLY CLEANED: the rule
        # for learned figures (ROOM_EVENT_DONE_STATUSES), and not a room
        # only reached into (MIN_CLEANED_ROOM_SHARE). A room closed at a
        # cancelled mission's end would teach a fraction of a room.
        share = room_event_covered_share(room)
        if room.get("status") in ROOM_EVENT_DONE_STATUSES and (
            share is None or share >= MIN_CLEANED_ROOM_SHARE
        ):
            cleaned.add(rid)
        until = next(
            (t for t, later in events[index + 1:]
             if later.get("type") in _BOUNDARY_TYPES and t > ts),
            mission_end,
        )
        if until is not None and until > ts:
            spent[rid] = spent.get(rid, 0.0) + (until - ts)
    return {
        rid: seconds for rid, seconds in spent.items()
        if rid in cleaned and MIN_ROOM_SECONDS <= seconds <= MAX_ROOM_SECONDS
    }


def _mission_key(rec: dict[str, Any]) -> str | None:
    """A key that names the same mission on every refresh and restart:
    the cloud's mission id, else its mission counter, else our id."""
    for field in ("missionId", "nMssn", "id"):
        value = rec.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return None


def _teaches_nothing(rec: dict[str, Any]) -> bool:
    """A mission whose clock ran without cleaning: stuck, failed,
    cancelled, or paused by someone. The waiting would count into
    whatever room it happened in."""
    result = str(rec.get("result") or rec.get("classified_result") or "").lower()
    if any(word in result for word in ("stuck", "error", "cancel")):
        return True
    pause = rec.get("pauseM")
    return isinstance(pause, (int, float)) and not isinstance(pause, bool) and pause > 0


def learn_from_records(
    records: list[dict[str, Any]],
    cache: dict[str, float],
    learned_keys: list[str],
    names_for: Any,
    default_mode: str | None,
) -> tuple[int, int]:
    """Learn room times from every record with a cloud timeline whose
    mission is not in `learned_keys` yet, and add its key. A record whose
    rooms have no names yet is left for the next refresh.

    `learned_keys` belongs to the same store as `cache`, so saving that
    store keeps figures and keys together. Returns (room figures learned,
    missions added to `learned_keys`) -- a non-zero second value means
    the store needs saving.
    """
    known = set(learned_keys)
    learned = 0
    added = 0
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("timeline"), dict):
            continue
        key = _mission_key(rec)
        if key is None or key in known:
            continue
        seconds = {} if _teaches_nothing(rec) else timeline_room_seconds(rec)
        names = names_for(rec) or {}
        if any(rid not in names for rid in seconds):
            continue   # a map not loaded yet: try again next refresh
        mode = record_cleaning_mode(rec, default_mode)
        for rid, value in seconds.items():
            remember(cache, names[rid], value, mode)
            learned += 1
        learned_keys.append(key)
        known.add(key)
        added += 1
    if len(learned_keys) > MAX_LEARNED_KEYS:
        del learned_keys[: len(learned_keys) - MAX_LEARNED_KEYS]
    return learned, added


def learn_via(store: Any, *args: Any) -> tuple[int, int]:
    """`store.learn_room_times(*args)`, answering (0, 0) for anything
    that is not a real result -- a store that failed to load must not
    stop a cloud refresh or a setup."""
    try:
        result = store.learn_room_times(*args)
    except Exception:  # noqa: BLE001 -- learning is a bonus; the refresh is not
        return 0, 0
    if (
        isinstance(result, tuple) and len(result) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in result)
    ):
        return result[0], result[1]
    return 0, 0
