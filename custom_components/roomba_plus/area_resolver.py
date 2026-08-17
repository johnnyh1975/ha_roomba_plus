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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Where HA stores the segment-to-area mapping. Read defensively rather
#: than imported: the key belongs to HA's vacuum component and this
#: integration must keep working on versions that predate CLEAN_AREA
#: entirely, where the option simply is not there.
_VACUUM_DOMAIN = "vacuum"
_SEGMENT_MAP_KEYS = ("segment_area_mapping", "area_segment_mapping", "segments")


def _mapping_from_options(options: Any) -> dict[str, str]:
    """Segment id -> HA area id, from the vacuum entity's options.

    HA's own key name for this is not something this integration should
    depend on: it was introduced in 2026.3 and the architecture
    discussion around it is still open. So several plausible keys are
    tried and anything unrecognised yields an empty mapping -- which
    degrades to "no area resolved" rather than to a wrong area.
    """
    vacuum_options = (options or {}).get(_VACUUM_DOMAIN) or {}
    for key in _SEGMENT_MAP_KEYS:
        raw = vacuum_options.get(key)
        if not isinstance(raw, dict) or not raw:
            continue
        # Stored either way round depending on HA version. An area id is
        # a slug; a segment id in this integration always carries a
        # prefix ("rid_", "zid_", or a pmap id), which is what makes the
        # two distinguishable without guessing.
        # Segment ids CONTAIN "rid_"/"zid_"; they do not start with it,
        # because Classic prefixes them with the pmap id. Checking
        # startswith missed every real Classic segment -- caught by a
        # test built from an actual id rather than an imagined one.
        if any(
            "rid_" in str(v) or "zid_" in str(v)
            for v in raw.values()
        ):
            return {str(v): str(k) for k, v in raw.items()}
        return {str(k): str(v) for k, v in raw.items()}
    return {}


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

    registry = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(
        registry, config_entry.entry_id
    ):
        if entry.domain != _VACUUM_DOMAIN:
            continue
        mapping = _mapping_from_options(entry.options)
        if not mapping:
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
