"""Answers for scripts and Assist: rooms, missions, a plan (4.6.0, B6, B8).

`get_rooms`, `get_missions`, `get_mission` and `plan_clean` are response
actions (`response_variable:` in a script). Until 4.6.0 a script that
wanted "which rooms does the robot know" or "what did the last run do"
had to read a sensor's attributes, which change shape between sensors
and are recorded with every state change. These read the stores and
answer once.

BOTH GENERATIONS, ONE SHAPE. Rooms come from the Classic cloud regions
or the Prime room names; missions from the mission store, which both
fill. A field a generation does not know is None, never missing.

NOTHING HERE TALKS TO THE CLOUD: every answer is what Roomba+ already
holds, so a call is cheap and works with the account offline.
"""
from __future__ import annotations

import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .area_resolver import async_room_area_fields
from .estimate_quality import mission_estimate_error, room_quality
from .mission_result import mission_outcome, named_rooms
from .models import ConnectionType
from .room_times import current_cleaning_mode, room_learning

#: The mission fields every answer carries, in this order.
_SUMMARY_FIELDS = (
    "id", "started_at", "ended_at", "duration_min", "result", "initiator",
    "scope", "area_sqft", "error_code",
)


def _data_state(data: Any) -> dict[str, Any]:
    """The robot's reported state, Prime's assembled from its shadows."""
    from .prime_coordinator import prime_reported_state  # noqa: PLC0415

    try:
        # Classic first; on Prime it is {} and the shadows answer.
        state = data.roomba_reported_state() or prime_reported_state(data)
    except Exception:  # noqa: BLE001 -- a missing state only loses the mode
        return {}
    return state if isinstance(state, dict) else {}


def _estimate(entry: Any, room: str) -> int | None:
    from .sensor_rooms import _compute_room_time_estimates  # noqa: PLC0415

    try:
        values = _compute_room_time_estimates(entry, [room])
    except Exception:  # noqa: BLE001 -- an estimate is a bonus here
        return None
    value = values[0] if values else None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cache(data: Any) -> dict[str, float]:
    profile = getattr(data, "robot_profile_store", None)
    cache = getattr(profile, "room_estimate_cache", None)
    return cache if isinstance(cache, dict) else {}


def rooms_of(hass: HomeAssistant, entry: Any) -> list[dict[str, Any]]:
    """The rooms and zones the robot knows, with area and estimate.

    `[{"id", "name", "map_id", "type", "area_id", "area_name",
    "estimate_sec", "estimate_quality"}]`. Classic from the cloud's
    regions of the active map (`type` is the cloud's region type); Prime
    from the room names of its maps (`type` None: Prime keeps rooms and
    zones in one id set). Empty for a robot without a room map.
    """
    data = entry.runtime_data
    mode = current_cleaning_mode(_data_state(data))
    cache = _cache(data)
    rows: list[tuple[str, str, Any, Any]] = []
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        maps = getattr(data, "prime_room_map_ids", None) or {}
        for rid, name in sorted((getattr(data, "prime_room_names", None) or {}).items()):
            if name:
                rows.append((str(rid), str(name), maps.get(rid), None))
    else:
        cloud = getattr(data, "cloud_coordinator", None)
        for region in getattr(cloud, "regions", None) or []:
            if isinstance(region, dict) and region.get("name"):
                rows.append((
                    str(region.get("id") or ""), str(region["name"]),
                    region.get("pmap_id"), region.get("region_type"),
                ))
    out: list[dict[str, Any]] = []
    for rid, name, map_id, kind in rows:
        out.append({
            "id": rid,
            "name": name,
            "map_id": map_id,
            "type": kind,
            **async_room_area_fields(
                hass, entry, name,
                rid if data.connection_type is ConnectionType.CLOUD_ONLY else None,
            ),
            "estimate_sec": _estimate(entry, name),
            "estimate_quality": room_quality(cache, name, mode),
        })
    return out


def _names_for(data: Any) -> dict[str, str]:
    """{region id: name} across maps, for a record's room ids."""
    names: dict[str, str] = {
        str(k): str(v) for k, v in (getattr(data, "prime_room_names", None) or {}).items() if v
    }
    cloud = getattr(data, "cloud_coordinator", None)
    for by_map in (getattr(cloud, "regions_by_pmap", None) or {}).values():
        if isinstance(by_map, dict):
            for rid, name in by_map.items():
                names.setdefault(str(rid), str(name))
    return names


def mission_summary(data: Any, record: dict[str, Any]) -> dict[str, Any]:
    """One stored mission, in the shape every answer uses."""
    out: dict[str, Any] = {key: record.get(key) for key in _SUMMARY_FIELDS}
    outcome = mission_outcome(record) or {}
    out["type"] = outcome.get("type")
    for key in ("requested_rooms", "skipped_rooms", "missed_rooms"):
        out[key] = named_rooms(data, record, list(outcome.get(key) or []))
    # Classic records keep room names; Prime records region ids.
    if record.get("last_cleaned_rooms"):
        out["cleaned_rooms"] = [str(r) for r in record["last_cleaned_rooms"]]
    else:
        names = _names_for(data)
        out["cleaned_rooms"] = [
            names.get(str(r), str(r)) for r in record.get("rooms_cleaned") or []
        ]
    out["rooms_source"] = record.get("rooms_source")
    out["battery_used_pct"] = record.get("battery_used_pct")
    out["estimated_duration_min"] = record.get("estimated_duration_min")
    return out


def mission_detail(data: Any, record: dict[str, Any]) -> dict[str, Any]:
    """mission_summary() plus the battery and what the run taught (B9, B12)."""
    out = mission_summary(data, record)
    for key in (
        "battery_start_pct", "battery_end_pct", "battery_recharged",
        "battery_used_pct_per_m2", "recharge_min", "nMssn",
    ):
        out[key] = record.get(key)
    learning = record.get("room_learning")
    rows = [dict(r) for r in learning] if isinstance(learning, list) else room_learning(record)
    names = _names_for(data)
    for row in rows:
        if "room" not in row:
            row["room"] = names.get(str(row.get("room_id")), row.get("room_id"))
    out["room_learning"] = rows
    # Where the figures came from: the cloud's room events (Classic with
    # an account, and every Prime mission), or room tracking as the run
    # went (Classic without one), which takes every room it measures.
    out["room_learning_source"] = (
        None if not rows
        else "room_tracking" if rows and "room" in (learning or [{}])[0]
        else "timeline"
    )
    return out


def missions_of(
    data: Any, *, limit: int = 20, days: int | None = None
) -> list[dict[str, Any]]:
    """The newest stored missions first, at most `limit`, within `days`."""
    store = getattr(data, "mission_store", None)
    records = list(getattr(store, "records", None) or [])
    since = (
        dt_util.utcnow() - datetime.timedelta(days=days) if days is not None else None
    )
    out: list[dict[str, Any]] = []
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if since is not None:
            ended = dt_util.parse_datetime(str(record.get("ended_at") or ""))
            if ended is None or ended < since:
                continue
        out.append(mission_summary(data, record))
        if len(out) >= limit:
            break
    return out


def find_mission(data: Any, mission_id: str | None) -> dict[str, Any] | None:
    """A stored mission by id, or the newest one."""
    store = getattr(data, "mission_store", None)
    records = [r for r in getattr(store, "records", None) or [] if isinstance(r, dict)]
    if mission_id is None:
        return records[-1] if records else None
    return next((r for r in reversed(records) if r.get("id") == mission_id), None)


def plan_clean(
    hass: HomeAssistant,
    entry: Any,
    rooms: list[str] | None = None,
    *,
    end_by: datetime.datetime | None = None,
) -> dict[str, Any]:
    """What a clean would take, started now, without starting it (B8).

    ROOMS IN THE ORDER GIVEN, each with its estimate and how good that is
    (estimate_quality). The total is their sum, and None when any room has
    no estimate -- a total that leaves a room out would look complete. A
    whole-home plan (no rooms) is the robot's mean mission length: which
    rooms a whole-home run visits in which order is the robot's choice.

    NOT IN IT: travel between rooms and to the dock, and a recharge on the
    way. `past_error` says how far the recent missions' estimates were off,
    which is what that leaves out, measured.

    `end_by`: whether the plan ends by then (`fits`); None when the total
    is not known.
    """
    data = entry.runtime_data
    now = dt_util.utcnow()
    known = {r["name"].casefold(): r for r in rooms_of(hass, entry)}
    store = getattr(data, "mission_store", None)
    past = mission_estimate_error(getattr(store, "records", None) or [])
    planned: list[dict[str, Any]] = []
    unknown: list[str] = []
    total: float | None
    if rooms:
        total = 0.0
        for name in rooms:
            room = known.get(str(name).casefold())
            if room is None:
                unknown.append(str(name))
                continue
            planned.append({
                "room": room["name"],
                "area_id": room["area_id"],
                "estimate_sec": room["estimate_sec"],
                "estimate_quality": room["estimate_quality"],
            })
            if room["estimate_sec"] is None:
                total = None
            elif total is not None:
                total += room["estimate_sec"]
        if not planned:
            total = None
        scope = "rooms"
    else:
        profile = getattr(data, "robot_profile_store", None)
        mean = getattr(profile, "mission_duration_mean_sec", None)
        total = float(mean) if isinstance(mean, (int, float)) and mean > 0 else None
        scope = "whole_home"
    end = now + datetime.timedelta(seconds=total) if total is not None else None
    if end_by is not None and end_by.tzinfo is None:
        end_by = end_by.replace(tzinfo=dt_util.get_default_time_zone())
    return {
        "scope": scope,
        "rooms": planned,
        "unknown_rooms": unknown,
        "rooms_without_estimate": [r["room"] for r in planned if r["estimate_sec"] is None],
        "total_sec": round(total) if total is not None else None,
        "start": now.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds") if end is not None else None,
        "end_by": end_by.isoformat(timespec="seconds") if end_by is not None else None,
        "fits": (end <= end_by) if end is not None and end_by is not None else None,
        "past_error": past,
    }
