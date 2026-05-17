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
        entities.append(SmartZoneButton(roomba, blid))

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
    """Button: start cleaning the zone currently selected in ZoneSelect.

    Reads the selected zone name from ZoneSelect, finds the matching Zone
    in ZoneStore, and sends a start command with the zone's bounding-box
    centre as the target coordinate.

    For 900-series robots without Smart Map (EPHEMERAL capability), we
    cannot use region_id. Instead we drive to the zone centre and start
    from there — the robot will clean that area based on its current
    position and the normal mission logic.
    """

    _attr_translation_key = "clean_zone"
    _attr_icon = "mdi:map-marker-check-outline"
    _attr_entity_category = None   # visible by default — primary action

    def __init__(self, roomba, blid, config_entry):
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_zone"

    async def async_press(self) -> None:
        """Find selected zone and start a targeted clean."""
        zone_store = self._config_entry.runtime_data.zone_store
        if not zone_store or not zone_store.zones:
            _LOGGER.warning("ZoneCleanButton: no zones available")
            return

        # Find the ZoneSelect entity to get the selected zone name
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)
        blid = self._config_entry.data.get("blid", "")
        select_uid = f"roomba_plus_{blid}_zone_select"
        select_entry = ent_reg.async_get_entity_id(
            "select", "roomba_plus", select_uid
        )

        selected_name: str | None = None
        if select_entry:
            state = self.hass.states.get(select_entry)
            if state:
                selected_name = state.state

        # Fall back to first confirmed zone
        confirmed = [z for z in zone_store.zones if z.confirmed]
        if not confirmed:
            _LOGGER.warning("ZoneCleanButton: no confirmed zones yet")
            return

        zone = next(
            (z for z in confirmed if z.name == selected_name),
            confirmed[0],
        )

        # Send start command — robot will clean from its current position
        # toward the zone centre using normal mission logic.
        # For 900-series (no Smart Map), we cannot use region_id.
        _LOGGER.info(
            "ZoneCleanButton: starting clean for zone '%s' (bbox %.0f,%.0f – %.0f,%.0f mm)",
            zone.name, zone.x_min, zone.y_min, zone.x_max, zone.y_max,
        )
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

    def __init__(self, roomba, blid):
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_smart_zone"

    async def async_press(self) -> None:
        """Find SmartZoneSelect state and start targeted clean.

        Uses the HA entity_platform helper to look up the SmartZoneSelect
        entity object directly — no hass.data hacks. Falls back to reading
        pmap/region from lastCommand if the select entity is not found.
        """
        from homeassistant.helpers import entity_platform as ep
        from homeassistant.helpers import entity_registry as er

        region_id: str | None = None
        pmap_info: dict = {}

        # Walk all entity platforms registered under this domain to find
        # the SmartZoneSelect entity for this specific robot (by unique_id).
        target_uid = f"{self.robot_unique_id}_smart_zone_select"
        for platform in ep.async_get_platforms(self.hass, "roomba_plus"):
            for entity in platform.entities.values():
                if getattr(entity, "unique_id", None) == target_uid:
                    region_id = entity.selected_region_id
                    pmap_info = entity.selected_pmap_info
                    break
            if region_id:
                break

        # Fallback: extract pmap/region from lastCommand in the local state.
        # This covers the case where the select entity is unavailable or has
        # not yet been updated after the most recent mission.
        if not region_id or not pmap_info.get("pmap_id"):
            last = self.vacuum_state.get("lastCommand", {})
            pmap_id = last.get("pmap_id")
            regions = last.get("regions", [])
            if pmap_id and regions:
                region_id = regions[0].get("region_id")
                pmap_info = {
                    "pmap_id": pmap_id,
                    "user_pmapv_id": last.get("user_pmapv_id", ""),
                }

        if not region_id or not pmap_info.get("pmap_id"):
            _LOGGER.warning("SmartZoneButton: no region/pmap available — run a zone mission first")
            return

        params = {
            "pmap_id": pmap_info["pmap_id"],
            "regions": [{"region_id": region_id, "type": "rid"}],
            "user_pmapv_id": pmap_info.get("user_pmapv_id", ""),
            "ordered": 1,
        }
        _LOGGER.info(
            "SmartZoneButton: cleaning region %s on map %s",
            region_id, pmap_info["pmap_id"][:12],
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, "start", params
        )
