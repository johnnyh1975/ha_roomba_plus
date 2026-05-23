"""Button platform for Roomba+.

Two categories of buttons:

COMMAND BUTTONS — send a direct command to the robot:
  EvacButton      — trigger bin evacuation (Clean Base models only)
  LocateButton    — play find-me tone

MAINTENANCE RESET BUTTONS — record the current bbrun.hr as the new
  start point for remaining-life calculations:
  FilterResetButton  — mark filter as replaced
  BrushResetButton   — mark brushes as replaced
  BatteryResetButton — mark battery as replaced

Reset buttons are always created (every robot has a filter and brushes).
The reset value is persisted in hass.storage via MaintenanceStore.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .entity import IRobotEntity
from .models import MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


# ── Command buttons ───────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class RoombaButtonDescription(ButtonEntityDescription):
    """Command button: sends a direct command to the robot."""
    command: str
    filter_fn: Any = None  # callable(state) -> bool | None = always create


COMMAND_BUTTONS: tuple[RoombaButtonDescription, ...] = (
    RoombaButtonDescription(
        key="evac",
        translation_key="evac",
        icon="mdi:delete-sweep-outline",
        entity_category=EntityCategory.CONFIG,
        command="evac",
        filter_fn=lambda s: s.get("cap", {}).get("dockComm") == 1,
    ),
    RoombaButtonDescription(
        key="locate",
        translation_key="locate",
        icon="mdi:map-marker-radius-outline",
        entity_category=EntityCategory.CONFIG,
        command="find",
        filter_fn=None,
    ),
)


# ── Setup ─────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all button entities for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)

    entities: list[IRobotEntity] = []

    # Command buttons (capability-gated)
    entities.extend(
        RoombaCommandButton(roomba, blid, desc)
        for desc in COMMAND_BUTTONS
        if desc.filter_fn is None or desc.filter_fn(state)
    )

    # Maintenance reset buttons (always present)
    entities.extend([
        FilterResetButton(roomba, blid, config_entry),
        BrushResetButton(roomba, blid, config_entry),
        BatteryResetButton(roomba, blid, config_entry),
    ])

    # Zone clean button: EPHEMERAL only, appears after first zone detected
    data = config_entry.runtime_data
    if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
        entities.append(ZoneCleanButton(roomba, blid, config_entry))

    # Repeat last mission: whenever lastCommand is present in state
    if state.get("lastCommand"):
        entities.append(RepeatLastMissionButton(roomba, blid))

    # Smart zone button: for Smart Map robots (i/s/j/m-series)
    from .const import has_smart_map
    if has_smart_map(state):
        entities.append(SmartZoneButton(roomba, blid, config_entry))

    async_add_entities(entities)


# ── Command button entity ─────────────────────────────────────────────────────

class RoombaCommandButton(IRobotEntity, ButtonEntity):
    """One-shot button that sends a direct command to the robot."""

    entity_description: RoombaButtonDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        description: RoombaButtonDescription,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"

    async def async_press(self) -> None:
        _LOGGER.debug("CommandButton: %s → %s",
                      self.entity_description.key,
                      self.entity_description.command)
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, self.entity_description.command
        )


# ── Maintenance reset button base ─────────────────────────────────────────────

class _MaintenanceResetButton(IRobotEntity, ButtonEntity):
    """Base class for maintenance reset buttons.

    On press: reads current bbrun.hr, calls the appropriate reset method
    on MaintenanceStore, then persists to hass.storage. The sensor entities
    will update on the next bbrun MQTT message.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry

    def _current_hr(self) -> int:
        """Return current bbrun.hr (lifetime operating hours)."""
        return self.vacuum_state.get("bbrun", {}).get("hr", 0)

    def _maintenance_store(self):
        """Return the MaintenanceStore from runtime_data."""
        return self._config_entry.runtime_data.maintenance_store

    async def _save(self) -> None:
        """Persist the updated MaintenanceStore to hass.storage."""
        store = self._maintenance_store()
        if store:
            await store.async_save(self.hass, self._config_entry.entry_id)
        # Force sensor refresh so remaining hours update immediately
        self.schedule_update_ha_state()


class FilterResetButton(_MaintenanceResetButton):
    """Button: mark filter as replaced → restart filter-life countdown."""

    _attr_translation_key = "reset_filter"
    _attr_icon = "mdi:air-filter"

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_filter"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("FilterResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_filter(hr)
            await self._save()


class BrushResetButton(_MaintenanceResetButton):
    """Button: mark brushes as replaced → restart brush-life countdown."""

    _attr_translation_key = "reset_brush"
    _attr_icon = "mdi:brush"

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_brush"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("BrushResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_brush(hr)
            await self._save()


class BatteryResetButton(_MaintenanceResetButton):
    """Button: mark battery as replaced → restart battery-hour tracking."""

    _attr_translation_key = "reset_battery"
    _attr_icon = "mdi:battery-plus-outline"

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_battery"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("BatteryResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_battery(hr)
            await self._save()


class ZoneCleanButton(_MaintenanceResetButton):
    """Button: start a clean with focus on the zone selected in ZoneSelect.

    For 900-series robots (EPHEMERAL capability), the MQTT API does not
    support coordinate-based navigation or region targeting. This button
    sends a standard start command from the robot's current position.

    The practical effect: if the robot is already docked and the user has
    selected a zone, the robot will start its normal coverage clean. The
    zone selection is informational — it does not steer the robot.

    The button is intentionally kept because the zone infrastructure
    (ZoneStore, ZoneSelect) is still useful for zone visualization,
    zone-aware automations, and the future serial OI navigation path.
    """

    _attr_translation_key = "clean_zone"
    _attr_icon = "mdi:map-marker-check-outline"
    _attr_entity_category = None   # visible by default — primary action

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_zone"

    async def async_press(self) -> None:
        """Start a clean. Zone selection is informational on 900-series robots."""
        zone_store = self._config_entry.runtime_data.zone_store
        if not zone_store or not zone_store.zones:
            _LOGGER.warning("ZoneCleanButton: no zones available yet")
            return

        confirmed = [z for z in zone_store.zones if z.confirmed]
        if not confirmed:
            _LOGGER.warning("ZoneCleanButton: no confirmed zones yet — run more missions")
            return

        # Find selected zone name via ZoneSelect entity state
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)
        blid = self._config_entry.data.get("blid", "")
        select_uid = f"roomba_plus_{blid}_zone_select"
        select_entry = ent_reg.async_get_entity_id("select", "roomba_plus", select_uid)

        selected_name: str | None = None
        if select_entry:
            state = self.hass.states.get(select_entry)
            if state:
                selected_name = state.state

        zone = next(
            (z for z in confirmed if z.name == selected_name),
            confirmed[0],
        )

        _LOGGER.info(
            "ZoneCleanButton: starting clean (900-series — no coordinate targeting). "
            "Selected zone: '%s' bbox %.0f,%.0f – %.0f,%.0f mm",
            zone.name, zone.x_min, zone.y_min, zone.x_max, zone.y_max,
        )
        # 900-series: send plain start — robot navigates using its own logic.
        # The zone parameter is noted in the log but cannot be passed to the robot.
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, "start"
        )


class RepeatLastMissionButton(IRobotEntity, ButtonEntity):
    """Button: repeat the last cleaning mission.

    Reads lastCommand from the robot state and resends it verbatim.
    For Smart Map robots (i/s/j): this repeats the exact same zone(s),
    including pmap_id, regions, and team_id.
    For 900-series or simple robots: sends a plain start command.

    No cloud access needed — lastCommand is part of the local MQTT state.
    """

    _attr_translation_key = "repeat_mission"
    _attr_icon = "mdi:repeat"
    _attr_entity_category = None   # primary action → Steuerelemente

    def __init__(self, roomba, blid):
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_repeat_mission"

    async def async_press(self) -> None:
        last = self.vacuum_state.get("lastCommand", {})
        if not last:
            _LOGGER.warning("RepeatLastMission: no lastCommand in state")
            return

        command = last.get("command", "start")

        # Build params from lastCommand — include Smart Map fields if present
        params: dict = {}
        for key in ("pmap_id", "user_pmapv_id", "regions", "ordered", "params"):
            if key in last:
                params[key] = last[key]

        # If a pmap_id is present, refresh user_pmapv_id from live state.pmaps
        # to avoid silent failures after a map retrain.
        if params.get("pmap_id"):
            from . import _resolve_pmapv_id
            fresh = _resolve_pmapv_id(self.vacuum_state, params["pmap_id"])
            if fresh:
                params["user_pmapv_id"] = fresh
            else:
                _LOGGER.warning(
                    "RepeatLastMission: pmap %s not in live state — map may have been retrained",
                    params["pmap_id"],
                )

        _LOGGER.info(
            "RepeatLastMission: sending %s params=%s", command, params or "(none)"
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, command, params or {}
        )

    def new_state_filter(self, new_state: dict) -> bool:
        return "lastCommand" in new_state


class SmartZoneButton(IRobotEntity, ButtonEntity):
    """Button: clean the zone selected in SmartZoneSelect.

    Builds a start command with pmap_id + region_id from the local state.
    No cloud access needed — all data comes from cleanSchedule2 / lastCommand.
    """

    _attr_translation_key = "clean_smart_zone"
    _attr_icon = "mdi:map-marker-check-outline"
    _attr_entity_category = None   # primary action — visible by default

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_smart_zone"
        self._config_entry = config_entry

    async def async_press(self) -> None:
        """Find SmartZoneSelect state and start targeted clean.

        Uses the HA entity_platform helper to look up the SmartZoneSelect
        entity object directly — no hass.data hacks. Falls back to reading
        pmap/region from lastCommand if the select entity is not found.

        user_pmapv_id is always read from live state.pmaps at press time,
        never from lastCommand, to avoid stale-map silent failures.
        """
        from homeassistant.helpers import entity_platform as ep
        from . import _resolve_pmapv_id

        region_id: str | None = None
        pmap_id: str | None = None

        # Walk all entity platforms registered under this domain to find
        # the SmartZoneSelect entity for this specific robot (by unique_id).
        target_uid = f"{self.robot_unique_id}_smart_zone_select"
        for platform in ep.async_get_platforms(self.hass, "roomba_plus"):
            for entity in platform.entities.values():
                if getattr(entity, "unique_id", None) == target_uid:
                    region_id = entity.selected_region_id
                    break
            if region_id:
                break

        # Resolve pmap_id from smart_zone_data first — this is the value
        # persisted at zone-naming time and is correct for the specific region.
        # lastCommand.pmap_id may reflect a full-home clean and be wrong for
        # a targeted region clean. Only fall back to lastCommand when
        # smart_zone_data has no entry for this region_id.
        if region_id:
            zone_data: dict = self._config_entry.options.get("smart_zone_data", {})
            pmap_id = zone_data.get(str(region_id), {}).get("pmap_id") or None

        # Fallback: extract pmap/region from lastCommand when smart_zone_data
        # has no pmap_id (e.g. zone entered before fix) or entity lookup failed.
        if not region_id or not pmap_id:
            last = self.vacuum_state.get("lastCommand", {})
            if not pmap_id:
                pmap_id = last.get("pmap_id")
            if not region_id:
                regions = last.get("regions", [])
                if regions:
                    region_id = str(regions[0].get("region_id", "")) or None

        if not region_id or not pmap_id:
            _LOGGER.warning(
                "SmartZoneButton: no region/pmap available — run a zone mission first"
            )
            return

        # Guard: reject if the robot is currently updating its Smart Map.
        # notReady bit 6 (64) = map save/upload in progress — same guard as
        # the clean_room service action.
        not_ready: int = self.vacuum_state.get(
            "cleanMissionStatus", {}
        ).get("notReady", 0)
        if not_ready & 64:
            _LOGGER.warning(
                "SmartZoneButton: robot is updating Smart Map (notReady=%d) — "
                "wait for map update to complete before starting a zone clean",
                not_ready,
            )
            return

        # Always resolve user_pmapv_id from live state.pmaps — never cached.
        user_pmapv_id = _resolve_pmapv_id(self.vacuum_state, pmap_id)
        if not user_pmapv_id:
            _LOGGER.warning(
                "SmartZoneButton: pmap %s not found in live state — map may have been retrained",
                pmap_id,
            )
            return

        params = {
            "pmap_id": pmap_id,
            "regions": [
                {
                    "region_id": str(region_id),
                    "type": "rid",
                    "params": {"noAutoPasses": False, "twoPass": False},
                }
            ],
            "user_pmapv_id": user_pmapv_id,
            "ordered": 1,
        }
        _LOGGER.info(
            "SmartZoneButton: cleaning region %s on map %s",
            region_id, pmap_id[:12],
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, "start", params
        )
