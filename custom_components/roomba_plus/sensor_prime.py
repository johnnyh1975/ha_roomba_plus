"""V4/Prime (CLOUD_ONLY) sensors for the Roomba+ sensor platform.

The first CLOUD_ONLY-aware sensors this platform ever had.
sensor.py's existing SENSORS/RoombaSensor machinery (sensor_core.py) is
deeply tied to roomba_reported_state()'s Classic shape (dozens of
filter_fn/value-function callables never audited against roomba=None)
-- rather than risk that large, untested surface, these are
DELIBERATELY separate, minimal entity classes, mirroring the same
"separate CLOUD_ONLY path" pattern already established for vacuum.py
and _async_setup_entry_prime().

All entities pass roomba=None into IRobotEntity.__init__() -- already
confirmed safe (roomba_reported_state(None) returns {}), the same
pattern the CLOUD_ONLY vacuum entity already relies on.

TWO DATA SOURCES, TWO GROUPS OF SENSORS: PrimeMissionEventSensor/
PrimeConnectionHealthSensor read PrimeCoordinator's MissionTimelineReport
(mission/timeline/report push data). PrimeBatterySensor/
PrimeDetectedPadSensor/PrimeRuntimeHoursSensor read
PrimeStatusCoordinator's CurrentStateShadow (the named shadow
"ro-currentstate") -- this is what RESOLVES the earlier "no battery/
dock data" gap: the underlying search that used to be described here
as unconfirmed (RobotStatusV2) is a separate, different structure that
genuinely never appears anywhere; the actual battery/dock/bin/tank
data lives in ro-currentstate instead, confirmed live (chairstacker)
with real values, not guessed at. See prime_coordinator.py's own
docstring for the full evidence trail. Bin/tank presence are
BinarySensorEntity, not SensorEntity -- see binary_sensor.py instead,
matching where their Classic equivalents already live.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTime

from .entity import IRobotEntity
from .models import RoombaConfigEntry


class PrimeMissionEventSensor(IRobotEntity, SensorEntity):
    """Current mission-timeline event type, with room-progress attributes.

    native_value: the raw event_type string (e.g. "start"/"reloc"/
    "travel"/"room"/"pause"/"charge"/...) from the most recent
    MissionTimelineReport -- deliberately the raw string, not translated
    into a VacuumActivity here (that translation lives in vacuum.py's
    own activity property; this sensor is the untranslated, diagnostic
    view of the same underlying data, useful for automations that want
    to react to a SPECIFIC event type vacuum.py's activity mapping
    collapses together, e.g. distinguishing "reloc" from "travel" even
    though both currently map to CLEANING).

    extra_state_attributes: mission_id, and current_room_id/area/
    pass_count when the current event is room/travel-shaped -- same
    data vacuum.py's own extra_state_attributes already exposes, kept
    consistent between both rather than diverging.
    """

    entity_description = SensorEntityDescription(
        key="prime_mission_event",
        translation_key="prime_mission_event",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_mission_event"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _report(self) -> Any | None:
        pc = (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )
        return pc.data if pc is not None else None

    @property
    def native_value(self) -> str | None:
        report = self._report
        if report is None or not report.event:
            return None
        return report.event[0].event_type

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        report = self._report
        if report is None or not report.event:
            return {}
        attrs: dict[str, Any] = {"mission_id": report.mission_id}
        current = report.event[0]
        room = current.room or current.travel
        if room is not None:
            attrs["current_room_id"] = room.region_id
        if current.room is not None:
            attrs["current_room_area"] = current.room.area
            attrs["current_room_pass_count"] = current.room.pass_count
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        pc = (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )
        if pc is not None:
            self.async_on_remove(pc.async_add_listener(self.schedule_update_ha_state))


class PrimeConnectionHealthSensor(IRobotEntity, SensorEntity):
    """Whether the mission/timeline/report push connection is currently
    healthy -- our OWN connection state, not anything about the robot
    itself. Deliberately simple (a plain "ok"/"error" string) rather than
    the elaborate 0-100 scored health concept RoombaIntegrationHealthSensor
    (sensor_diagnostics.py) uses for the classic path -- that scoring
    combines several classic-only signals (Repair Issues, MissionArchive
    freshness) that don't apply here; reusing its shape would mean
    fabricating a score from a single boolean. If Prime health tracking
    grows more signals later, revisit unifying with that pattern then.

    native_value: "ok" if the coordinator's last update succeeded (or no
    update has happened yet -- not itself an error), "error" if
    watch_mission_timeline() raised (see PrimeCoordinator's own
    async_set_update_error() call).
    """

    entity_description = SensorEntityDescription(
        key="prime_connection_health",
        translation_key="prime_connection_health",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_connection_health"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _coordinator(self) -> Any | None:
        return (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )

    @property
    def native_value(self) -> str:
        coordinator = self._coordinator
        if coordinator is None or coordinator.last_update_success:
            return "ok"
        return "error"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self._coordinator
        if coordinator is None or coordinator.last_exception is None:
            return {}
        return {"last_error": str(coordinator.last_exception)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        pc = self._coordinator
        if pc is not None:
            self.async_on_remove(pc.async_add_listener(self.schedule_update_ha_state))


class _PrimeCurrentStateSensorBase(IRobotEntity, SensorEntity):
    """Shared base for V4/Prime sensors reading from
    PrimeStatusCoordinator's "ro-currentstate" data. See
    prime_coordinator.py's own docstring for the coordinator itself,
    and binary_sensor.py's _PrimeStatusSensorBase for the
    BinarySensorEntity-flavored counterpart of this same pattern
    (bin/tank presence live there instead, matching where their
    Classic equivalents already live)."""

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid)
        self._config_entry = config_entry

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _current_state(self) -> Any:
        from roombapy_prime.models import CurrentStateShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-currentstate")
        if raw is None:
            return None
        return CurrentStateShadow.from_json(raw)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class PrimeBatterySensor(_PrimeCurrentStateSensorBase):
    """V4/Prime battery percentage -- the actual resolution of this
    whole project's multi-session battery-status search. Reads
    CurrentStateShadow.bat_pct (confirmed live, chairstacker: a plain
    int, 0-100, e.g. 72). Same key/device_class/unit as the Classic
    "battery" sensor (sensor_core.py's own SENSORS tuple) so both
    present identically to the user regardless of connection type."""

    entity_description = SensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )
    _attr_entity_category = None

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_battery"

    @property
    def native_value(self) -> int | None:
        state = self._current_state
        return state.bat_pct if state is not None else None


class PrimeDetectedPadSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime detected mop pad type. Reads
    CurrentStateShadow.detected_pad directly (confirmed live,
    chairstacker: a plain string, e.g. "padPlate") -- the raw reported
    value, not translated into a friendlier label, since the full set
    of possible values isn't confirmed yet (see that field's own
    docstring)."""

    entity_description = SensorEntityDescription(
        key="prime_detected_pad",
        translation_key="prime_detected_pad",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_detected_pad"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        return state.detected_pad if state is not None else None


def _dock_state_label(raw_value: Any) -> str | None:
    """Formats a DockState enum member (or its raw int, if the value
    isn't one of the 86 confirmed members) into a readable label --
    e.g. DOCK_READY -> "Dock ready". Not run through HA's own
    device_class=ENUM/translated-options machinery: DockState has 86
    members, mostly rarely-seen *_ERROR states -- translating all of
    them in all 8 languages would be a disproportionate effort for
    values a real user will almost never see, the same reasoning
    already applied to PrimeDetectedPadSensor above."""
    from roombapy_prime.models.robot_info import DockState

    if raw_value is None:
        return None
    try:
        member = DockState(raw_value)
    except ValueError:
        return f"Unknown ({raw_value})"
    return member.name.replace("_", " ").capitalize()


class PrimeDockStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime dock status. Reads CurrentStateShadow.dock.state
    (confirmed live, chairstacker: 301 -> DockState.DOCK_READY) --
    see DockState's own docstring in roombapy-prime for the full,
    86-value confirmed enum this is drawn from."""

    entity_description = SensorEntityDescription(
        key="prime_dock_status",
        translation_key="prime_dock_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_dock_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return _dock_state_label(state.dock.state)


class PrimePadWashStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime pad wash status. Reads CurrentStateShadow.dock.pw_state
    (confirmed live, chairstacker: 601 -> DockState.PAD_WASH_OKAY)."""

    entity_description = SensorEntityDescription(
        key="prime_pad_wash_status",
        translation_key="prime_pad_wash_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_pad_wash_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return _dock_state_label(state.dock.pw_state)


class PrimePadDryStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime pad dry status. Reads CurrentStateShadow.dock.pd_state
    (confirmed live, chairstacker: 701 -> DockState.PAD_DRY_OKAY)."""

    entity_description = SensorEntityDescription(
        key="prime_pad_dry_status",
        translation_key="prime_pad_dry_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_pad_dry_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return _dock_state_label(state.dock.pd_state)


class PrimeSuctionLevelSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime configured suction level. Reads RobotSettings.suction_level
    from the named shadow "rw-settings" -- a SEPARATE data source from
    the other sensors on this page (ro-currentstate), same pattern as
    PrimeFirmwareVersionSensor's own rw-software read. SuctionLevel is
    fully confirmed (5 values: Invalid/Low/Medium/High/Turbo, see that
    enum's own docstring in roombapy-prime) -- properly modeled as a
    real device_class=ENUM sensor with translated states, unlike the
    dock-status sensors above (which have too many rarely-seen values
    for that to be worth the translation effort)."""

    entity_description = SensorEntityDescription(
        key="prime_suction_level",
        translation_key="prime_suction_level",
        device_class=SensorDeviceClass.ENUM,
        options=["invalid", "low", "medium", "high", "turbo"],
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_suction_level"

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import RobotSettings
        from roombapy_prime.models.mission_control import SuctionLevel

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None
        settings = RobotSettings.from_json(raw)
        if settings.suction_level is None:
            return None
        try:
            return SuctionLevel(settings.suction_level).name.lower()
        except ValueError:
            return None


class PrimeRuntimeHoursSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime lifetime runtime hours. Reads
    CurrentStateShadow.runtime_stats.hours (confirmed live,
    chairstacker: 44) -- minutes exposed as an extra_state_attribute
    rather than a separate entity, since it's a sub-component of the
    same lifetime-runtime figure, not an independent measurement."""

    entity_description = SensorEntityDescription(
        key="prime_runtime_hours",
        translation_key="prime_runtime_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_runtime_hours"

    @property
    def native_value(self) -> int | None:
        state = self._current_state
        if state is None or state.runtime_stats is None:
            return None
        return state.runtime_stats.hours

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._current_state
        if state is None or state.runtime_stats is None:
            return {}
        return {"minutes": state.runtime_stats.minutes}


class PrimeFirmwareVersionSensor(IRobotEntity, SensorEntity):
    """V4/Prime firmware version -- read from the named shadow
    "rw-software" (SoftwareStatusShadow.software_version), confirmed
    live (chairstacker) as a plain string via Ghidra decompilation of
    the app's own constructor signature (type-tag 3). A separate data
    source from the "ro-currentstate"-backed sensors above -- see
    prime_coordinator.py's own docstring: PrimeStatusCoordinator seeds
    and watches ALL eight named shadows, not just ro-currentstate."""

    entity_description = SensorEntityDescription(
        key="prime_firmware_version",
        translation_key="prime_firmware_version",
        entity_registry_enabled_default=True,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_firmware_version"

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import SoftwareStatusShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-software")
        if raw is None:
            return None
        return SoftwareStatusShadow.from_json(raw).software_version

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))
