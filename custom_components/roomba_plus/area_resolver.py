"""Resolving the robot's current room to a Home Assistant area.

WHY AREAS AND NOT ZONES.

Home Assistant has two similar-sounding concepts and only one of them
fits. ZONES are geographic: a latitude, a longitude and a radius, meant
for presence detection. AREAS are rooms in the house -- Kitchen, Living
room -- and they are very often named the same as the robot's own rooms.

The device tracker has reported the robot's room as a plain string via
`location_name` since v2.9.0. That property is deprecated and stops
working in Home Assistant Core 2027.7 (reported by @mdarocha, issue
#54). HA's guidance is to report zone entity ids, or to move extra
context to a sensor or state attribute.

The zone route does not apply here: creating a geographic zone per
room would misuse the concept. So the room moves to a state attribute -- and
while moving it, it becomes useful rather than merely relocated.

WHAT CHANGES BEYOND THE DEPRECATION FIX.

The room was exposed as iRobot's own name: whatever the user typed in
the iRobot app. An automation had to match that string exactly,
including accents and capitalisation, and it broke if the room was
renamed in the app.

Since the CLEAN_AREA work, Home Assistant holds a mapping from vacuum
segments to HA areas, stored in the vacuum entity's registry options.
That mapping is the user's own statement of which robot room is which
HA area -- so resolving through it produces a stable area id instead of
a mutable label.

WHAT THIS IS NOT.

Architecture proposal #1371 asks for a standard `current_area` attribute
on the vacuum entity itself. It was NOT accepted -- rejected in April
2026 for having too few supporting integrations. So this deliberately
does not invent that attribute name on the vacuum entity: if the
proposal is revived with a different shape, an integration that guessed
early would have to break its own users to comply.

The area is therefore reported on the device tracker, where the room
already was, under a name that makes no claim to be standard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from .models import ConnectionType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Where HA keeps the mapping, in the vacuum entity's registry options
#: under "vacuum": `area_mapping` ({area id: [segment id, ...]}, read by
#: vacuum.clean_area) and `last_seen_segments` (the segments as offered
#: when the mapping was saved: id, name, group). Read defensively rather
#: than imported: an HA before CLEAN_AREA has neither.
_VACUUM_DOMAIN = "vacuum"
_AREA_MAPPING_KEY = "area_mapping"
_SEEN_SEGMENTS_KEY = "last_seen_segments"


def _vacuum_options(options: Any) -> dict[str, Any]:
    raw = (options or {}).get(_VACUUM_DOMAIN)
    return raw if isinstance(raw, dict) else {}


def _mapping_from_options(options: Any) -> dict[str, str]:
    """Segment id -> HA area id, inverted from HA's `area_mapping`.

    HA STORES IT AREA FIRST, WITH A LIST (#253, @willfanguy): {area id:
    [segment id, ...]}, under `area_mapping`. This read three keys HA
    never used, so no mapping was ever found and every room fell back
    to its name. A segment mapped to two areas keeps the first.
    """
    raw = _vacuum_options(options).get(_AREA_MAPPING_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for area_id, segment_ids in raw.items():
        if isinstance(segment_ids, str):
            segment_ids = [segment_ids]
        if not isinstance(segment_ids, (list, tuple)):
            continue
        for segment_id in segment_ids:
            out.setdefault(str(segment_id), str(area_id))
    return out


def _seen_segments(options: Any) -> list[dict[str, Any]]:
    raw = _vacuum_options(options).get(_SEEN_SEGMENTS_KEY)
    if not isinstance(raw, list):
        return []
    return [seg for seg in raw if isinstance(seg, dict)]


def _vacuum_entries(hass: HomeAssistant, config_entry: RoombaConfigEntry) -> list[Any]:
    registry = er.async_get(hass)
    return [
        entry
        for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id)
        if entry.domain == _VACUUM_DOMAIN
    ]


def _is_zone_segment(segment_id: str) -> bool:
    # Prime zones are "zid_...", Classic zones "<pmap>_zid_<id>".
    return "zid_" in segment_id


def async_area_for_segment(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, segment_id: str
) -> str | None:
    """The HA area id a vacuum segment is mapped to, if the user set one.

    Returns None when no mapping exists, which is the normal state until
    someone configures it -- not an error. The room name remains
    available alongside, so nothing is lost by an unmapped robot.
    """
    if not segment_id:
        return None
    for entry in _vacuum_entries(hass, config_entry):
        area = _mapping_from_options(entry.options).get(segment_id)
        if area:
            return area
    return None


def _area_for_prime_region(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, region_id: str
) -> str | None:
    """The area of a Prime room known by its region id alone.

    Prime offers rooms as `rid_<p2map id>/<region id>` (`rid_<id>` before
    the map was known). Without the map, a segment of any map with that
    region id counts, as long as they all name one area: two maps' rooms
    with the same id in different areas say nothing about which is meant.
    """
    if not region_id:
        return None
    suffix = f"/{region_id}"
    bare = f"rid_{region_id}"
    areas: set[str] = set()
    for entry in _vacuum_entries(hass, config_entry):
        for segment_id, area in _mapping_from_options(entry.options).items():
            if segment_id == bare or (
                segment_id.startswith("rid_") and segment_id.endswith(suffix)
            ):
                areas.add(area)
    return areas.pop() if len(areas) == 1 else None


def _area_for_segment_name(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, room_name: str
) -> str | None:
    """The area of the mapped room segment with this name.

    The segments HA showed when the mapping was saved carry their names,
    so a room is found as the user mapped it, whatever form its id takes
    on either generation. Zones are left out: a zone named like a room is
    not that room.
    """
    wanted = room_name.casefold().strip()
    if not wanted:
        return None
    for entry in _vacuum_entries(hass, config_entry):
        mapping = _mapping_from_options(entry.options)
        if not mapping:
            continue
        for segment in _seen_segments(entry.options):
            segment_id = str(segment.get("id") or "")
            if not segment_id or _is_zone_segment(segment_id):
                continue
            if str(segment.get("name") or "").casefold().strip() != wanted:
                continue
            area = mapping.get(segment_id)
            if area:
                return area
    return None


def async_area_for_room_name(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, room_name: str
) -> str | None:
    """The HA area id whose name matches a robot room name.

    FALLBACK ONLY, for robots and users without a configured segment
    mapping. Matching by name is exactly the fragility the mapping
    exists to remove -- a room renamed in the iRobot app silently stops
    resolving -- so it is tried second and never overrides an explicit
    mapping.

    Included because it costs nothing and covers the common case the
    user described: HA areas and robot rooms are very often named the
    same, and someone who never opens the mapping dialog still gets a
    usable area.
    """
    if not room_name:
        return None

    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    wanted = room_name.casefold().strip()
    for area in ar.async_get(hass).async_list_areas():
        if area.name.casefold().strip() == wanted:
            return area.id
        for alias in getattr(area, "aliases", None) or ():
            if str(alias).casefold().strip() == wanted:
                return area.id
    return None


def async_area_for_room(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    room: str,
    prime_rooms: dict[str, str] | None = None,
) -> str | None:
    """The HA area for a robot room name, mapping first and name second.

    One place for what the device tracker did on its own (4.6.0): the
    room-completed event, the mission-started event and the area sensors
    ask the same question and must get the same answer.

    The configured segment mapping wins: it is the user's own statement
    of which robot room is which area. Name matching is the fallback for
    the many setups where HA areas and robot rooms are simply named the
    same and nobody opened the mapping dialog.

    The segment ids are those async_get_segments() offers HA. PRIME:
    `rid_<p2map_id>/<room_id>`, from `prime_rooms` ({name:
    "<p2map_id>/<room_id>"}, the device tracker's cache) when given, else
    by region id from `prime_room_names` ({region_id: name}). CLASSIC:
    `<pmap_id>_<region_id>` from the cloud coordinator's regions. Then
    the mapped segment of that name, then the area named like the room.

    Never raises: no areas defined is a normal configuration.
    """
    if not room:
        return None
    try:
        return _area_for_room(hass, config_entry, room, prime_rooms)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: area resolution failed", exc_info=True)
        return None


def _area_for_room(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    room: str,
    prime_rooms: dict[str, str] | None,
) -> str | None:
    """async_area_for_room() without the guard."""
    data = config_entry.runtime_data
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        qualified = (prime_rooms or {}).get(room)
        area: str | None = None
        if qualified and "/" in str(qualified):
            area = async_area_for_segment(hass, config_entry, f"rid_{qualified}")
        else:
            names = getattr(data, "prime_room_names", None) or {}
            rid = next(
                (str(k) for k, v in names.items() if str(v) == room), None
            )
            if rid:
                area = _area_for_prime_region(hass, config_entry, rid)
        if area:
            return area
    else:
        # Classic offers rooms as `<pmap id>_<region id>`.
        regions = getattr(
            getattr(data, "cloud_coordinator", None), "regions", None
        ) or []
        for region in regions:
            if str(region.get("name") or "") != room:
                continue
            pmap_id, region_id = region.get("pmap_id"), region.get("id")
            if pmap_id and region_id:
                area = async_area_for_segment(
                    hass, config_entry, f"{pmap_id}_{region_id}"
                )
                if area:
                    return area
    return _area_for_segment_name(
        hass, config_entry, room
    ) or async_area_for_room_name(hass, config_entry, room)


def async_area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    """The display name of an HA area, or None."""
    if not area_id:
        return None
    from homeassistant.helpers import area_registry as ar  # noqa: PLC0415

    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area is not None else None


def async_room_area_fields(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    room: Any,
    region_id: Any = None,
) -> dict[str, str | None]:
    """`area_id` and `area_name` of a room, for an event's payload (4.6.0, B4).

    `region_id` is the Prime region the timeline named: its mapping is
    looked up first, so a room the robot reports by id needs no name.
    Both None for a room without an area, or no room: the fields are
    always there, so a template never has to guard against a missing key.
    """
    area_id: str | None = None
    if region_id:
        try:
            area_id = _area_for_prime_region(hass, config_entry, str(region_id))
        except Exception:  # noqa: BLE001
            area_id = None
    if area_id is None and room:
        area_id = async_area_for_room(hass, config_entry, str(room))
    try:
        name = async_area_name(hass, area_id)
    except Exception:  # noqa: BLE001
        name = None
    return {"area_id": area_id, "area_name": name}
