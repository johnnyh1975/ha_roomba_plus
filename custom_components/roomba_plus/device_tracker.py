"""Device tracker platform for Roomba+.

v2.9.0 DEVICE-TRACKER. Tier-aware location reporting via TrackerEntity's
location_name (which HA's own state property checks BEFORE falling back to
lat/lon zone lookups — see homeassistant.components.device_tracker.config_entry
.TrackerEntity.state). Returning a named string here means NO fake GPS
coordinates are needed at all; HA core was already built for exactly this
"named location, not a map point" use case for an indoor robot.

- SMART robots (i/s/j-series, Braava m6): current room name during an
  active mission — reuses sensor.py's _resolve_smart_tier_room_state(),
  the SAME function RoombaMissionProgress's current_room attribute uses,
  so the two entities always agree.
- EPHEMERAL robots (900-series, e.g. the 980): room/zone-level detection
  is NOT yet wired in here. ZoneStore's gap-based zone splitting (the
  original room-detection mechanism) was found structurally limited for
  robots with dense MQTT pose sampling (confirmed: max inter-sample step
  340mm, far short of the 800mm door-gap threshold — see project notes,
  June 2026) and has since been removed entirely (ROOM-SEG, see
  ROOM_SEGMENTATION_NOTES.md). RoomSegStore (watershed segmentation on
  GridStore) replaced it for room naming/the live map, and could in
  principle fill this extension point too — resolving "which room is the
  robot in right now" from RoomSegStore's room cells + live pose is a
  reasonable next step, just not implemented yet. Deliberately
  isolated in its own function (_resolve_ephemeral_tier_room) so that once
  EPHEMERAL room/zone detection improves, only that one function needs to
  change — nothing else in this platform.

Both tiers: "Angedockt" while docked/idle (NOT "home" — the robot is
always physically at home regardless of dock state; "home" carries a
GPS-zone connotation that doesn't fit here). Raw x_mm/y_mm pose
coordinates are always exposed as attributes (when available) regardless
of tier, for users who want to build their own zone logic externally.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .const import MISSION_END_PHASES, POSE_POINT_CM_TO_MM
from .entity import IRobotEntity
from .models import RoombaConfigEntry

PARALLEL_UPDATES = 0

# v2.9.0 — location_name is returned directly as the entity's state by
# TrackerEntity (see module docstring) and does NOT go through HA's normal
# translation_key lookup. A tiny manual table covers the two fixed labels
# this platform needs; expand here if more languages are needed later.
_DOCKED_LABEL: dict[str, str] = {
    "de": "Angedockt",
    "en": "Docked",
}
_ACTIVE_FALLBACK_LABEL: dict[str, str] = {
    "de": "Unterwegs",
    "en": "Cleaning",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the device tracker for this Roomba. Always created — works
    for every robot tier, just with different location granularity."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    async_add_entities([RoombaDeviceTracker(roomba, blid, config_entry)])


class RoombaDeviceTracker(IRobotEntity, TrackerEntity):
    """DEVICE-TRACKER (v2.9.0) — robot location, room-level when available.

    See module docstring for the full tier-aware design and the
    EPHEMERAL-tier extension point.
    """

    _attr_name = None
    _attr_translation_key = "position"
    # TrackerEntity.entity_registry_enabled_default returns False when both
    # mac_address and device_info are None — which is always the case here,
    # since we identify the robot by BLID, not MAC, and deliberately inherit
    # TrackerEntity's device_info=None (device tracker entities should not
    # create device registry entries per HA core design). Without this
    # override the entity is registered but disabled by default, so users
    # never see it in the UI. Confirmed as the root cause of Thonno's report
    # ("I don't seem to have that entity on my i7+") — v2.10.3.
    _attr_entity_registry_enabled_default = True

    def __init__(
        self, roomba: Any, blid: str, config_entry: RoombaConfigEntry
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_position"

    @property
    def source_type(self) -> SourceType:
        # Locally determined from the robot's own onboard pose estimate —
        # not GPS, not router-presence. ROUTER is the closest existing
        # SourceType to "determined by a local, non-GPS data source";
        # there is no dedicated "robot odometry" source type in HA core.
        return SourceType.ROUTER

    def _label(self, table: dict[str, str]) -> str:
        lang = (self.hass.config.language or "en")[:2]
        return table.get(lang, table["en"])

    @property
    def location_name(self) -> str | None:
        """The entity's state. Always returns a sensible value — never
        None — regardless of robot tier or room-detection reliability."""
        data = self._config_entry.runtime_data
        state = roomba_reported_state(self.vacuum)
        phase = state.get("cleanMissionStatus", {}).get("phase", "")

        if phase in MISSION_END_PHASES or phase == "":
            return self._label(_DOCKED_LABEL)

        room = self._resolve_room(data)
        if room:
            return room
        return self._label(_ACTIVE_FALLBACK_LABEL)

    def _resolve_room(self, data: Any) -> str | None:
        """Tier-dispatch to the right room-resolution strategy."""
        if data.map_capability.value == "smart":
            return self._resolve_smart_tier_room(data)
        return self._resolve_ephemeral_tier_room(data)

    def _resolve_smart_tier_room(self, data: Any) -> str | None:
        """SMART-tier room name — delegates to the SAME shared function
        RoombaMissionProgress's current_room attribute uses, so both
        entities always agree on where the robot currently is."""
        from .sensor import _resolve_smart_tier_room_state
        room_state = _resolve_smart_tier_room_state(self._config_entry)
        return room_state.get("current_room")

    def _resolve_ephemeral_tier_room(self, data: Any) -> str | None:
        """EPHEMERAL-tier room/zone resolution — EXTENSION POINT.

        Currently always returns None. The original mechanism this would
        have used (ZoneStore's gap-based zone detection) was found
        structurally limited for robots with dense MQTT pose sampling
        (confirmed June 2026: max inter-sample step 340mm vs. the 800mm
        door-gap threshold — a real doorway is crossed in many small
        steps, never one qualifying gap) and has since been removed
        entirely (ROOM-SEG, see ROOM_SEGMENTATION_NOTES.md). RoomSegStore
        replaced it for room naming/the live map and could fill this
        extension point too (resolve current room from RoomSegStore's
        room cells + live pose) — a reasonable next step, not yet done.
        Nothing else in this platform needs to change: location_name, the
        docked check, and the attribute exposure below are all
        tier-agnostic already.
        """
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}

        # Raw pose, always exposed when available, regardless of tier —
        # for users who want to build their own zone logic externally.
        # v2.9.0 units fix: pose.point.x/y is in centimetres, not
        # millimetres (see POSE_POINT_CM_TO_MM in const.py).
        state = roomba_reported_state(self.vacuum)
        pose = state.get("pose")
        if isinstance(pose, dict):
            point = pose.get("point", {})
            x = point.get("x")
            y = point.get("y")
            if x is not None and y is not None:
                attrs["x_mm"] = round(float(x) * POSE_POINT_CM_TO_MM)
                attrs["y_mm"] = round(float(y) * POSE_POINT_CM_TO_MM)

        data = self._config_entry.runtime_data
        mts = getattr(data, "mission_timer_store", None)
        phase = state.get("cleanMissionStatus", {}).get("phase", "")
        if (
            mts is not None
            and mts.mission_id is not None
            and phase not in MISSION_END_PHASES
            and phase != ""
        ):
            room = self._resolve_room(data)
            attrs["room"] = room
            if data.map_capability.value == "smart":
                from .sensor import _resolve_smart_tier_room_state
                room_state = _resolve_smart_tier_room_state(self._config_entry)
                attrs["next_room"] = room_state.get("next_room")

        return attrs

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state or "pose" in new_state
