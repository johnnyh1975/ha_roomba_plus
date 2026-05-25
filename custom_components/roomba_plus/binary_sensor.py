"""Binary sensor platform for Roomba+.

Entities:
  RoombaBinStatus         — True when the bin is full
  RoombaBinPresentStatus  — True when the bin is present/inserted
  RoombaConnectionStatus  — True when the robot is reachable via MQTT
  RoombaMopReadyStatus    — True when the Braava mop is ready to start
                            (tank present AND lid closed)
  RoombaMapSavingStatus   — True when the robot is saving/updating its
                            Smart Map (notReady bit 6 set). Only created
                            for Smart Map robots (i/s/j/Braava m6).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .const import has_smart_map
from .entity import IRobotEntity
from .models import RoombaConfigEntry

_NOT_READY_MAP_SAVING: int = 64  # notReady bitmask bit 6


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)

    entities: list[IRobotEntity] = []

    # Bin full: only create when the robot reports bin.full
    if "full" in state.get("bin", {}):
        entities.append(RoombaBinStatus(roomba, blid))

    # Bin present: only create when the robot reports bin.present
    if "present" in state.get("bin", {}):
        entities.append(RoombaBinPresentStatus(roomba, blid))

    # Connection sensor: always created
    entities.append(RoombaConnectionStatus(roomba, blid))

    # Mop ready: only for Braava (mopReady dict present in state)
    if "mopReady" in state:
        entities.append(RoombaMopReadyStatus(roomba, blid))
        entities.append(RoombaMopTankPresentStatus(roomba, blid))
        entities.append(RoombaMopLidClosedStatus(roomba, blid))

    # Map saving: only for Smart Map robots (i/s/j/Braava m6).
    # Reads notReady bit 6 — set while the robot is saving or uploading
    # its Smart Map after a training run or boundary edit.
    if has_smart_map(state):
        entities.append(RoombaMapSavingStatus(roomba, blid))

    async_add_entities(entities)


class RoombaBinStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Roomba's bin is full."""

    _attr_translation_key = "bin_full"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_bin_full"

    @property
    def is_on(self) -> bool:
        """Return True when the bin is full."""
        return roomba_reported_state(self.vacuum).get("bin", {}).get("full", False)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "bin" in new_state


class RoombaBinPresentStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the dust bin is inserted.

    Relevant for i-series robots where the bin is removed by the Clean Base
    during evacuation and may accidentally be left out. When OFF (bin missing),
    the robot cannot start a cleaning mission.
    """

    _attr_translation_key = "bin_present"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_bin_present"

    @property
    def is_on(self) -> bool:
        """Return True when the bin is present."""
        return bool(
            roomba_reported_state(self.vacuum).get("bin", {}).get("present", True)
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "bin" in new_state


class RoombaConnectionStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Roomba is connected via MQTT.

    Uses roombapy's roomba_connected flag and the on_disconnect callback
    to reflect real-time connectivity without polling.
    """

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_connected"

    @property
    def is_on(self) -> bool:
        """Return True when the Roomba MQTT connection is active."""
        return bool(self.vacuum.roomba_connected)

    async def async_added_to_hass(self) -> None:
        """Register both message and disconnect callbacks."""
        await super().async_added_to_hass()
        self.vacuum.register_on_disconnect_callback(self._on_disconnect)

    def _on_disconnect(self, error: str | None) -> None:
        """Schedule HA state update when the robot disconnects."""
        self.schedule_update_ha_state()

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return True


class RoombaMopReadyStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Braava mop is ready to start.

    Combines two conditions from mopReady:
      - tankPresent: the water tank is inserted
      - lidClosed:   the lid is closed

    Both must be True for the mop to start a mission. When either is False,
    the entity is OFF, making it easy to build an automation that warns the
    user before a scheduled mopping mission.

    Only created when mopReady is present in the state (Braava m6).
    """

    _attr_translation_key = "mop_ready"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mop_ready"

    @property
    def is_on(self) -> bool:
        """Return True when the mop has a problem (not ready).

        We use PROBLEM device class: ON = problem = mop NOT ready.
        This ensures the entity shows as a warning in the UI when attention
        is needed, consistent with how bin_full works.
        """
        mop_ready = roomba_reported_state(self.vacuum).get("mopReady", {})
        tank_present = mop_ready.get("tankPresent", True)
        lid_closed = mop_ready.get("lidClosed", True)
        # PROBLEM=ON when mop is NOT ready
        return not (tank_present and lid_closed)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return individual mop-ready conditions as attributes."""
        mop_ready = self.vacuum_state.get("mopReady", {})
        return {
            "tank_present": mop_ready.get("tankPresent"),
            "lid_closed": mop_ready.get("lidClosed"),
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "mopReady" in new_state


class RoombaMopTankPresentStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Braava water tank is inserted.

    Separate from the combined mop_ready sensor to allow automations that
    specifically check whether the tank has been removed or forgotten.
    Only created on Braava m6 (mopReady present in state).
    """

    _attr_translation_key = "mop_tank_present"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mop_tank_present"

    @property
    def is_on(self) -> bool:
        """Return True when the water tank is inserted."""
        return bool(
            roomba_reported_state(self.vacuum)
            .get("mopReady", {})
            .get("tankPresent", True)
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "mopReady" in new_state


class RoombaMopLidClosedStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Braava lid is closed.

    Separate from the combined mop_ready sensor to allow automations that
    specifically alert when the lid has been left open after a pad change.
    Only created on Braava m6 (mopReady present in state).
    """

    _attr_translation_key = "mop_lid_closed"
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mop_lid_closed"

    @property
    def is_on(self) -> bool:
        """Return True when the lid is OPEN (OPENING device class: ON = open).

        Note the inversion: OPENING is ON when open. The lid being open is
        the alert condition, consistent with door/window sensors in HA.
        """
        return not bool(
            roomba_reported_state(self.vacuum)
            .get("mopReady", {})
            .get("lidClosed", True)
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "mopReady" in new_state


class RoombaMapSavingStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON while the robot is saving its Smart Map.

    The iRobot firmware sets notReady bit 6 (value 64) during Smart Map
    save/upload operations that follow a training run or boundary edit in
    the iRobot app. While this bit is set:
      - The robot does not respond to region-targeted clean commands
      - Any clean_room or Smart Zone button press will be silently refused
        (the integration already guards against this with error 224)

    This sensor makes that state visible in HA so users can:
      - Build automations that wait for the map save to complete
        before issuing a zone clean
      - Show a warning in the dashboard when commands are blocked
      - Trigger notifications ("Smart Map is updating, please wait")

    Only created for Smart Map robots (i/s/j/Braava m6). The notReady
    field is not present on 900-series or 600-series robots.

    Device class UPDATE: ON = update in progress (map save running),
    OFF = idle (map save complete, commands accepted normally).
    """

    _attr_translation_key = "map_saving"
    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_map_saving"

    @property
    def is_on(self) -> bool:
        """Return True while the robot is saving its Smart Map."""
        not_ready: int = (
            roomba_reported_state(self.vacuum)
            .get("cleanMissionStatus", {})
            .get("notReady") or 0
        )
        return bool(not_ready & _NOT_READY_MAP_SAVING)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return full notReady bitmask value for diagnostics."""
        not_ready: int = (
            roomba_reported_state(self.vacuum)
            .get("cleanMissionStatus", {})
            .get("notReady") or 0
        )
        return {"not_ready_bitmask": not_ready}

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state
