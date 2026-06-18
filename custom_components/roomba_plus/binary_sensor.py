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

import datetime as _dt
import time as _time_mod
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import roomba_reported_state
from .const import (
    CONF_BLOCKING_SENSORS,
    CONF_BRUSH_HOURS,
    CONF_FILTER_HOURS,
    DEFAULT_BRUSH_HOURS,
    DEFAULT_FILTER_HOURS,
    DOMAIN,
    MQTT_WATCHDOG_SECONDS,
    has_smart_map,
    is_mop,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry

PARALLEL_UPDATES = 0

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

    # v1.7.0 L2 — Maintenance due sensor
    if config_entry.runtime_data.maintenance_store is not None:
        entities.append(RoombaMaintenanceDue(roomba, blid, config_entry))

    # v1.7.0 L5 — Start blocked sensor (only when blocking sensors configured)
    if config_entry.options.get(CONF_BLOCKING_SENSORS):
        entities.append(RoombaStartBlocked(roomba, blid, config_entry))

    # v1.8.0 L6 — Schedule hold active sensor (only when robot supports schedHold)
    if "schedHold" in state:
        entities.append(RoombaScheduleHoldActive(roomba, blid, config_entry))

    # v1.9.0 — Braava lid and tank direct sensors
    if "lidOpen" in state:
        entities.append(RoombaMopLidOpen(roomba, blid))
    if "tankPresent" in state:
        entities.append(RoombaMopTankPresentDirect(roomba, blid))

    # v1.9.3 — Mid-mission recharge sensor (all robots)
    entities.append(RoombaMidMissionRecharge(roomba, blid))

    # v2.2.0 — Mission active sensor (all robots) — card fix C1
    entities.append(RoombaMissionActive(roomba, blid))

    # F11 — demand clean blocked sensor (SMART + cloud + demand enabled)
    data = config_entry.runtime_data
    if (
        data.dirt_threshold_manager is not None
        and data.has_cloud
    ):
        entities.append(RoombaDemandCleanBlocked(roomba, blid, config_entry))

    # v2.8.3 — WIFI-CLOUD-HEALTH: robot-side cloud connectivity (always created;
    # returns None when wifistat absent from MQTT state).
    entities.append(RoombaCloudConnected(roomba, blid))

    # v2.8.3 — MQTT-WATCHDOG: silence detection during phase=run (always created).
    entities.append(RoombaMqttStale(roomba, blid, config_entry))

    # v2.8.3 — FW-SENSOR: firmware update indicator (always created; ON for
    # 24 h after softwareVer changes, then auto-resets to OFF).
    entities.append(RoombaFirmwareUpdated(roomba, blid, config_entry))

    async_add_entities(entities)


class RoombaBinStatus(IRobotEntity, BinarySensorEntity):
    """Binary sensor that is ON when the Roomba's bin is full."""

    entity_description = BinarySensorEntityDescription(
        key="bin_full",
        name="Bin full",
        translation_key="bin_full",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="bin_present",
        name="Bin present",
        translation_key="bin_present",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="connected",
        name="Connected",
        translation_key="connected",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="mop_ready",
        name="Mop problem",
        translation_key="mop_ready",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="mop_tank_present",
        name="Mop tank present",
        translation_key="mop_tank_present",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="mop_lid_closed",
        name="Mop lid open",
        translation_key="mop_lid_closed",
    )

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

    entity_description = BinarySensorEntityDescription(
        key="map_saving",
        name="Smart Map saving",
        translation_key="map_saving",
    )

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

class RoombaMaintenanceDue(IRobotEntity, BinarySensorEntity):
    """ON when any consumable has reached zero remaining hours.

    Provides a single trigger point for maintenance automations instead of
    requiring four separate threshold checks. Attributes expose which
    consumables are due and by how many hours they are overdue.
    """

    entity_description = BinarySensorEntityDescription(
        key="maintenance_due",
        name="Maintenance due",
        translation_key="maintenance_due",
    )

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_maintenance_due"

    @property
    def is_on(self) -> bool:
        """Return True when at least one consumable is at zero remaining hours."""
        return bool(self._due_items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return which consumables are due and how many hours overdue each is.

        overdue_by_hours values are 0 when exactly at threshold, positive when
        past it. This is useful for automations that escalate alerts based on
        how long maintenance has been deferred.
        """
        due = self._due_items()
        overdue: dict[str, int] = {}
        store = self._entry.runtime_data.maintenance_store
        if store and due:
            current_hr = self.vacuum_state.get("bbrun", {}).get("hr", 0)
            options = self._entry.options
            if "filter" in due:
                threshold = options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
                hours_since_reset = current_hr - store.filter_reset_hr
                overdue["filter"] = max(0, hours_since_reset - threshold)
            brush_key = "pad" if is_mop(self.vacuum_state) else "brush"
            if brush_key in due:
                threshold = options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
                hours_since_reset = current_hr - store.brush_reset_hr
                overdue[brush_key] = max(0, hours_since_reset - threshold)
        return {
            "due": due,
            "overdue_by_hours": overdue,
        }

    def _due_items(self) -> list[str]:
        """Return list of consumable keys currently at zero remaining hours."""
        store = self._entry.runtime_data.maintenance_store
        if not store:
            return []
        current_hr = self.vacuum_state.get("bbrun", {}).get("hr", 0)
        options = self._entry.options
        items: list[str] = []
        if store.filter_remaining(
            current_hr, options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
        ) == 0:
            items.append("filter")
        brush_key = "pad" if is_mop(self.vacuum_state) else "brush"
        if store.brush_remaining(
            current_hr, options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
        ) == 0:
            items.append(brush_key)
        return items

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "bbrun" in new_state


class RoombaStartBlocked(IRobotEntity, BinarySensorEntity):
    """ON while a smart_start is queued waiting for blocking sensors to clear.

    ON = start is currently blocked/queued (a problem condition).
    OFF = no pending start or all sensors clear.

    Attributes expose which sensors are currently blocking, when queueing
    started, and when the timeout will expire.
    """

    entity_description = BinarySensorEntityDescription(
        key="start_blocked",
        name="Start blocked",
        translation_key="start_blocked",
    )

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_start_blocked"

    @property
    def is_on(self) -> bool:
        """Return True while a start is queued."""
        bm = self._entry.runtime_data.blocking_manager
        return bm is not None and bm.is_queued

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return blocking entity IDs and queue timing."""
        bm = self._entry.runtime_data.blocking_manager
        if bm is None:
            return {}
        return {
            "blocking_entities": bm.blocking_entities,
            "queued_since": bm.queued_since,
            "timeout_at": bm.timeout_at,
        }

    async def async_added_to_hass(self) -> None:
        """Register callback with BlockingManager for immediate state updates."""
        await super().async_added_to_hass()
        bm = self._entry.runtime_data.blocking_manager
        if bm is not None:
            unsub = bm.register_state_callback(self.schedule_update_ha_state)
            self.async_on_remove(unsub)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # This entity is updated externally when the BlockingManager changes
        # state — always accept updates (the filter is mostly cosmetic here).
        return True


class RoombaScheduleHoldActive(IRobotEntity, BinarySensorEntity):
    """ON when schedHold is True for any reason.

    The `source` attribute distinguishes presence-manager-managed holds
    from manual toggles via ScheduleHoldSwitch, allowing the Lovelace
    card to show the correct schedule zone state.

    Only created when the robot reports schedHold in its state.
    """

    entity_description = BinarySensorEntityDescription(
        key="schedule_hold_active",
        name="Schedule hold active",
        translation_key="schedule_hold_active",
    )

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_schedule_hold_active"

    @property
    def is_on(self) -> bool:
        """Return True when schedHold is active for any reason."""
        return bool(self.vacuum_state.get("schedHold", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the source of the current hold (presence_manager or manual)."""
        pm = self._entry.runtime_data.presence_manager
        source = "presence_manager" if (pm and pm.is_managed_hold) else "manual"
        return {"source": source}

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "schedHold" in new_state


class RoombaMopLidOpen(IRobotEntity, BinarySensorEntity):
    """Binary sensor: ON when the Braava lid is open.

    A pre-clean alert — if the lid is open the robot refuses to start a
    mission. Pair with an automation that warns the user before a scheduled
    mopping run begins.

    Reads the top-level `lidOpen` MQTT field.
    Only created when `lidOpen` is present in the initial state.
    """

    entity_description = BinarySensorEntityDescription(
        key="mop_lid_open",
        name="Lid open",
        translation_key="mop_lid_open",
    )

    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mop_lid_open"

    @property
    def is_on(self) -> bool:
        return bool(roomba_reported_state(self.vacuum).get("lidOpen", False))

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "lidOpen" in new_state


class RoombaMopTankPresentDirect(IRobotEntity, BinarySensorEntity):
    """Binary sensor: ON when the Braava water tank is physically present.

    Reads the top-level `tankPresent` MQTT field — distinct from
    `mopReady.tankPresent` which combines tank presence with lid state.
    Both sensors coexist without conflict.

    Only created when `tankPresent` is present as a top-level state key.
    """

    entity_description = BinarySensorEntityDescription(
        key="mop_tank_present_direct",
        name="Tank present",
        translation_key="mop_tank_present_direct",
    )

    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mop_tank_present_direct"

    @property
    def is_on(self) -> bool:
        return bool(roomba_reported_state(self.vacuum).get("tankPresent", True))

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "tankPresent" in new_state


class RoombaMidMissionRecharge(IRobotEntity, BinarySensorEntity):
    """Binary sensor: ON when the robot is recharging mid-mission.

    Distinguishes two states that the standard VacuumActivity.PAUSED covers:
    - mid-mission recharge: phase=charge AND cycle≠none (this sensor is ON)
    - user-paused:          phase=stop  AND cycle≠none (this sensor is OFF)

    Pair with mission_recharge_minutes to show time remaining until resume.
    Always created on all robots — the condition is universal across firmware.
    """

    entity_description = BinarySensorEntityDescription(
        key="mid_mission_recharge",
        name="Mid-mission recharge",
        translation_key="mid_mission_recharge",
    )

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mid_mission_recharge"

    @property
    def is_on(self) -> bool:
        status = roomba_reported_state(self.vacuum).get("cleanMissionStatus", {})
        return (
            status.get("phase") == "charge"
            and status.get("cycle", "none") != "none"
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state


class RoombaMissionActive(IRobotEntity, BinarySensorEntity):
    """ON whenever a mission is in progress — including mid-mission recharge.

    Card fix C1 — binary_sensor.*_mission_active.

    ON when cycle != "none" AND phase is not in the final completion set.
    This covers the full mission arc from start through any mid-mission
    recharge pauses to final dock.

    Distinction from RoombaMidMissionRecharge:
      - MidMissionRecharge: ON only when phase=="charge" AND cycle!="none"
      - MissionActive:      ON for the entire mission (run, hmMidMsn, charge,
                            hmPostMsn, evac...) until cycle returns to "none"

    phase=="charge" with cycle!="none" = mid-mission recharge → still ON.
    phase=="charge" with cycle=="none" = final dock after mission → OFF.
    """

    entity_description = BinarySensorEntityDescription(
        key="mission_active",
        name="Mission active",
        translation_key="mission_active",
    )

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _FINAL_PHASES: frozenset[str] = frozenset({"stop", "cancelled", ""})

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_mission_active"

    @property
    def is_on(self) -> bool:
        status = roomba_reported_state(self.vacuum).get("cleanMissionStatus", {})
        cycle = status.get("cycle", "none")
        if cycle == "none":
            return False
        phase = status.get("phase", "")
        # charge phase with cycle!="none" = mid-mission recharge → still ON
        # charge phase with cycle=="none"  = caught by the guard above → OFF
        return phase not in self._FINAL_PHASES or phase == "charge"

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state


class RoombaDemandCleanBlocked(IRobotEntity, BinarySensorEntity):
    """ON when a demand clean was evaluated but blocked by presence or scheduling.

    F11 (v2.4.0) — diagnostic entity. Shows users why demand cleaning
    did not trigger despite dirt density exceeding the threshold.

    ON states:
      - Robot is busy (active mission, mid-mission recharge)
      - BlockingManager.is_queued is True
      - Presence gate blocked (someone home while demand triggered)

    OFF = demand clean would be allowed to fire if density exceeded threshold.
    None = DirtThresholdManager not configured or no evaluation yet.
    """

    entity_description = BinarySensorEntityDescription(
        key="demand_clean_blocked",
        name="Demand clean blocked",
        translation_key="demand_clean_blocked",
    )

    _attr_entity_category = None  # reclassified DIAG→MAIN (v2.6.0)

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_demand_clean_blocked"

    @property
    def is_on(self) -> bool | None:
        """Return True when demand clean is currently blocked.

        ALG3 (v2.6.0): delegates to DirtThresholdManager.gate_blocked() —
        single source of truth for gate logic.
        """
        data = self._config_entry.runtime_data
        dtm = getattr(data, "dirt_threshold_manager", None)
        if dtm is None:
            return None
        blocked, _ = dtm.gate_blocked()
        return blocked

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state


# ── v2.8.3 ────────────────────────────────────────────────────────────────────

class RoombaCloudConnected(IRobotEntity, BinarySensorEntity):
    """WIFI-CLOUD-HEALTH (v2.8.3) — robot-side iRobot cloud connectivity.

    ON when the robot reports wifistat.cloud != 0, meaning the robot itself
    can reach iRobot cloud servers.

    Distinct from:
      - RoombaConnectionStatus (MQTT between HA and robot)
      - CLOUD-STALE Repair Issue (HA fetching data from iRobot API)

    Returns None (Unknown) when wifistat is absent from MQTT state — older
    9-series firmware does not send this field.
    """

    entity_description = BinarySensorEntityDescription(
        key="cloud_connected",
        name="Cloud connected",
        translation_key="cloud_connected",
    )

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_connected"

    @property
    def is_on(self) -> bool | None:
        """Return True when robot reports cloud connectivity."""
        wifistat = roomba_reported_state(self.vacuum).get("wifistat")
        if wifistat is None:
            return None  # Field absent on 9-series — report Unknown
        cloud_val = wifistat.get("cloud") if isinstance(wifistat, dict) else None
        if cloud_val is None:
            return None
        return bool(cloud_val)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "wifistat" in new_state


_MQTT_WATCHDOG_TICK = _dt.timedelta(seconds=60)
_FIRMWARE_UPDATED_WINDOW_SECONDS: float = 86400.0  # 24 h


class RoombaMqttStale(IRobotEntity, BinarySensorEntity):
    """MQTT-WATCHDOG (v2.8.3) — silence detection during active cleaning.

    ON when phase==run is active AND no MQTT message has been received for
    MQTT_WATCHDOG_SECONDS (5 min).  Checked on a 60-second periodic tick.

    When ON:
      - Entity state turns ON (visible in the UI)
      - mqtt_watchdog Repair Issue fires

    When OFF (new message received):
      - Entity turns OFF
      - Repair Issue auto-resolves

    Returns False when no messages have been received at all since HA startup
    (last_mqtt_message_ts == 0.0) — avoids false positives on first boot.
    """

    entity_description = BinarySensorEntityDescription(
        key="mqtt_stale",
        name="MQTT stale",
        translation_key="mqtt_stale",
    )

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_mqtt_stale"
        self._unsub_tick: Any | None = None
        self._was_stale: bool = False

    async def async_added_to_hass(self) -> None:
        """Start 60-second watchdog tick."""
        await super().async_added_to_hass()
        self._unsub_tick = async_track_time_interval(
            self.hass,
            self._async_watchdog_tick,
            _MQTT_WATCHDOG_TICK,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel watchdog tick and clear any stale issue."""
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        ir.async_delete_issue(
            self.hass, DOMAIN, f"mqtt_watchdog_{self._entry.entry_id}"
        )

    @callback
    def _async_watchdog_tick(self, _now: _dt.datetime) -> None:
        """Re-evaluate watchdog state and fire/clear Repair Issue on transition."""
        now_stale = bool(self.is_on)
        if now_stale and not self._was_stale:
            # Transition OFF → ON: MQTT went silent during an active mission.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"mqtt_watchdog_{self._entry.entry_id}",
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="mqtt_watchdog",
            )
        elif not now_stale and self._was_stale:
            # Transition ON → OFF: MQTT traffic resumed.
            ir.async_delete_issue(
                self.hass, DOMAIN, f"mqtt_watchdog_{self._entry.entry_id}"
            )
        self._was_stale = now_stale
        self.schedule_update_ha_state(force_refresh=True)

    @property
    def is_on(self) -> bool:
        """Return True when MQTT is silent during phase=run."""
        data = self._entry.runtime_data
        ts = data.last_mqtt_message_ts
        if ts == 0.0:
            return False  # No message received yet since HA startup
        phase = (
            roomba_reported_state(self.vacuum)
            .get("cleanMissionStatus", {})
            .get("phase", "")
        )
        if phase != "run":
            return False
        return (_time_mod.time() - ts) > MQTT_WATCHDOG_SECONDS

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state


class RoombaFirmwareUpdated(IRobotEntity, BinarySensorEntity):
    """FW-SENSOR (v2.8.3) — firmware update indicator.

    ON for 24 h after softwareVer changes (detected by callbacks.py comparing
    successive softwareVer values).  Resets to OFF automatically after 24 h.

    OFF = firmware is at the same version as when last seen.
    ON  = firmware was updated within the past 24 hours.
    None = no firmware version seen yet.

    Use with blueprint: 'Notify me when firmware updates.'
    Pairs with sensor.*_firmware_version which shows the current version string.
    """

    entity_description = BinarySensorEntityDescription(
        key="firmware_updated",
        name="Firmware updated",
        translation_key="firmware_updated",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_firmware_updated"

    @property
    def is_on(self) -> bool | None:
        """Return True when a firmware update was detected within the past 24 h."""
        data = self._entry.runtime_data
        if data.last_firmware_version is None:
            return None  # No firmware version seen yet
        updated_at = data.firmware_updated_at
        if updated_at is None:
            return False
        return (_time_mod.time() - updated_at) < _FIRMWARE_UPDATED_WINDOW_SECONDS

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "softwareVer" in new_state
