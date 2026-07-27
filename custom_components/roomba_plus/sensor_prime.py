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

from .const import ERROR_CODE_LABELS
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
        super().__init__(None, blid, config_entry)
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
        super().__init__(None, blid, config_entry)
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
        super().__init__(None, blid, config_entry)
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
        super().__init__(None, blid, config_entry)
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


class _PrimeStatsSensorBase(IRobotEntity, SensorEntity):
    """Shared base for V4/Prime sensors reading from
    PrimeStatusCoordinator's "ro-stats" data (StatsShadow) -- all
    confirmed with REAL VALUES this session (chairstacker's
    raw_shadows.json capture), unlike when this shadow was first
    modeled with key names only. See roombapy-prime's own
    models/robot_info.py::StatsShadow for the full evidence trail,
    including the internal-consistency checks that confirm these are
    genuine lifetime counters, not arbitrary numbers (BbMssnStats's
    counters sum exactly; BbSysStats's hour count matches the
    device's registration age)."""

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _stats(self) -> Any:
        from roombapy_prime.models import StatsShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-stats")
        if raw is None:
            return None
        return StatsShadow.from_json(raw)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class PrimeTotalMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime mission count. Reuses Classic's OWN
    translation_key ("total_missions") rather than a new Prime-specific
    one -- StatsShadow.bbmssn.n_mssn is confirmed (this session) to be
    the exact same field (nMssn) Classic's own equivalent sensor reads,
    just via a different transport (cloud shadow vs local MQTT). Real
    value seen: 276, cross-validated against ro-currentstate's own
    cleanMissionStatus.nMssn from the SAME capture.

    COUNTER SEMANTICS, clarified by a real field observation
    (chairstacker, v4.0.0a6): this total does NOT always equal
    successful + canceled + failed. It matches exactly when the robot
    is IDLE (confirmed: 247 + 25 + 4 = 276 in the capture above), but
    is one HIGHER while a mission is in progress -- n_mssn increments
    when a mission STARTS, whereas the three outcome counters only
    increment once it ENDS with a known result. The in-flight mission
    is therefore counted in the total but not yet in any outcome
    bucket. This is the robot's own counter behavior, faithfully
    reported (no arithmetic happens on our side) -- NOT an off-by-one
    on this integration's part. Worth remembering before treating the
    sum as an invariant anywhere: it holds at rest, not during a
    mission."""

    entity_description = SensorEntityDescription(
        key="prime_total_missions",
        translation_key="total_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_total_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return stats.bbmssn.n_mssn


class PrimeSuccessfulMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime successful-mission count. Reuses Classic's own
    "successful_missions" translation_key -- see PrimeTotalMissionsSensor's
    own docstring for the field-equivalence evidence. Real value seen: 247."""

    entity_description = SensorEntityDescription(
        key="prime_successful_missions",
        translation_key="successful_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_successful_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return stats.bbmssn.n_mssn_ok


class PrimeCanceledMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime canceled-mission count. Reuses Classic's own
    "canceled_missions" translation_key. Real value seen: 25."""

    entity_description = SensorEntityDescription(
        key="prime_canceled_missions",
        translation_key="canceled_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_canceled_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return stats.bbmssn.n_mssn_canceled


class PrimeFailedMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime failed-mission count. Reuses Classic's own
    "failed_missions" translation_key. Real value seen: 4."""

    entity_description = SensorEntityDescription(
        key="prime_failed_missions",
        translation_key="failed_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_failed_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return stats.bbmssn.n_mssn_failed


class PrimeChargeCyclesOkSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime successful charge-cycle count
    (StatsShadow.bbchg.n_chg_ok). NEW translation key -- NOT the same
    concept as Classic's own "battery_cycles" sensor, which depends on
    nLithChrg/nNimhChrg (fields absent entirely in the one real Prime
    capture seen so far, see BbChg3Stats's own docstring) -- this reads
    a genuinely different sub-field (bbchg, not bbchg3) that Classic's
    own bbchg sensors don't surface at all (Classic's own bbchg holds
    dock-contact-health counters -- nChatters/nKnockoffs/nAborts --
    not charge-success/failure counts). Real value seen: 561."""

    entity_description = SensorEntityDescription(
        key="prime_charge_cycles_ok",
        translation_key="prime_charge_cycles_ok",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_charge_cycles_ok"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbchg is None:
            return None
        return stats.bbchg.n_chg_ok


class PrimeChargeCyclesErrorSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime failed charge-cycle count
    (StatsShadow.bbchg.n_chg_err). See PrimeChargeCyclesOkSensor's own
    docstring for why this is a new translation key, not a Classic
    reuse. Real value seen: 0."""

    entity_description = SensorEntityDescription(
        key="prime_charge_cycles_error",
        translation_key="prime_charge_cycles_error",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_charge_cycles_error"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbchg is None:
            return None
        return stats.bbchg.n_chg_err


class PrimeSystemUptimeSensor(_PrimeStatsSensorBase):
    """V4/Prime powered-on hours (StatsShadow.bbsys.hours). No Classic
    equivalent -- genuinely new for Prime.

    CONFIRMED as POWERED-ON time rather than time since registration,
    by two field accounts at opposite ends of the range: one robot
    rarely switched off showed a 14-hour gap against wall-clock time,
    another that had been unplugged for months showed a 5579-hour gap.
    Both match what their owners recalled. See BbSysStats's own
    docstring for the full comparison.

    THEREFORE: do not label or describe this as device age or "time
    since you got the robot". On a robot that has spent months
    unplugged the two differ by more than half, and a user reading it
    as age would be badly misled."""

    entity_description = SensorEntityDescription(
        key="prime_system_uptime",
        translation_key="prime_system_uptime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_system_uptime"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbsys is None:
            return None
        return stats.bbsys.hours


class PrimeNavigationResetsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime navigation-reset count
    (StatsShadow.bbrstinfo.n_nav_rst). NEW translation key, DELIBERATELY
    not reusing Classic's own "reset_diagnostics" key: that sensor's
    own native_value is nSafRst (safety-triggered resets), a DIFFERENT
    primary field than the one confirmed for Prime so far (nNavRst --
    nSafRst/nMobRst/safCauses were all absent entirely in the one real
    capture seen). Reusing the same key/wording would imply this shows
    the same metric Classic's does, which isn't confirmed. Real value
    seen: 22."""

    entity_description = SensorEntityDescription(
        key="prime_navigation_resets",
        translation_key="prime_navigation_resets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_navigation_resets"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbrstinfo is None:
            return None
        return stats.bbrstinfo.n_nav_rst


class PrimeSerialNumberSensor(IRobotEntity, SensorEntity):
    """V4/Prime serial number -- read from the named shadow
    "ro-configinfo" (ConfigInfoShadow.hw_parts_rev.nav_serial_no),
    confirmed live (chairstacker) as a real value
    ("G185020H250311N105749", matching the device's own SKU prefix
    G185020). A separate data source from the ro-stats-backed sensors
    above -- same reasoning as PrimeFirmwareVersionSensor's own
    docstring (rw-software): PrimeStatusCoordinator seeds/watches ALL
    eight named shadows independently."""

    entity_description = SensorEntityDescription(
        key="prime_serial_number",
        translation_key="prime_serial_number",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_serial_number"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import ConfigInfoShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-configinfo")
        if raw is None:
            return None
        config_info = ConfigInfoShadow.from_json(raw)
        if config_info.hw_parts_rev is None:
            return None
        return config_info.hw_parts_rev.nav_serial_no or None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class PrimeErrorSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime error label, read from
    CurrentStateShadow.clean_mission_status.error (CONFIRMED LIVE for
    Prime, chairstacker's own ro-currentstate payload).

    Reuses Classic's OWN translation_key ("error") and its
    ERROR_CODE_LABELS catalogue rather than introducing a parallel
    Prime-specific one -- the codes are the same product-wide
    catalogue, only the transport differs (cloud shadow vs local MQTT),
    exactly as with the mission-count sensors.

    INHERITS CLASSIC'S HARD-WON STALE-ERROR SUPPRESSION, deliberately
    rather than reading the field raw: cleanMissionStatus.error
    PERSISTS across missions -- the firmware does not reset it to 0
    when the robot docks after a failure (see _error_value()'s own
    docstring in sensor_helpers.py, where Classic learned this). A
    naive Prime sensor would therefore show a long-finished error
    indefinitely while the robot sits charging. Same suppression rule:
    when there's no active or queued mission (cycle "none") and the
    phase indicates rest, report "None".

    ALSO EXPOSES not_ready / cond_not_ready as attributes (this
    session): per the parallel APK research, a readiness-based START
    REFUSAL (ResolvedMissionStatus 7/8/12/13) surfaces through those
    two fields rather than through `error` -- so a robot that refused
    to start would leave `error` at 0 while cond_not_ready carries the
    actual reasons. Keeping them visible here means that case is
    diagnosable from the entity itself, not only from a CLI script."""

    entity_description = SensorEntityDescription(
        key="prime_error",
        translation_key="error",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_error"

    @property
    def _mission_status(self) -> Any:
        state = self._current_state
        return None if state is None else state.clean_mission_status

    @property
    def native_value(self) -> str | None:
        status = self._mission_status
        if status is None:
            return None
        # Same rule Classic uses -- see this class's own docstring.
        if (status.cycle or "none") == "none" and (status.phase or "") in ("charge", "stop", "idle", ""):
            return "None"
        return ERROR_CODE_LABELS.get(status.error or 0, "None")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._mission_status
        if status is None:
            return {}
        return {
            "error_code": status.error,
            "not_ready": status.not_ready,
            "cond_not_ready": status.cond_not_ready,
        }


#: Maps the server's own count_type to a display unit.
#:
#: Values taken from a real capture (chairstacker's app screenshot plus
#: the endpoint's own response): "hr" for the filter and both brushes,
#: routines for mop pads, evacuations for the dirt disposal bag.
#:
#: Anything unrecognised falls through to no unit rather than being
#: forced into hours -- a wrong unit on a number is worse than none,
#: because it invites arithmetic that does not hold.
_PART_COUNT_UNITS: dict[str, str | None] = {
    "hr": UnitOfTime.HOURS,
    "hours": UnitOfTime.HOURS,
    "routines": "routines",
    "evacs": "evacuations",
    "evacuations": "evacuations",
    "missions": "missions",
}


class PrimeConsumablePartSensor(IRobotEntity, SensorEntity):
    """One V4/Prime consumable: filter, a brush, mop pads, dirt bag.

    Created dynamically per part the robot actually reports, rather
    than from a fixed list. The set differs by model -- a vacuum-only
    robot has no mop pads, a robot without a self-emptying base has no
    dirt bag -- and hard-coding it would either invent entities nobody
    has or miss ones on hardware nobody here owns.

    DIFFERENT IN KIND FROM THE CLASSIC MAINTENANCE SENSORS. Those
    compute wear themselves in maintenance_store.py, because a Classic
    robot reports nothing about it -- including learning the owner's
    real replacement interval after a couple of resets, and taking a
    user-configured threshold. None of that applies here: the cloud
    simply states the remaining count, so this sensor reports it and
    does no arithmetic.

    Which is also why the threshold options in this integration's
    config flow must not be offered for Prime robots. They would change
    nothing.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, blid: str, part_id: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._part_id = part_id
        self.entity_description = SensorEntityDescription(
            key=f"prime_part_{part_id}",
            translation_key="prime_consumable_part",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_translation_placeholders = {"part": part_id.replace("_", " ")}
        self._attr_unique_id = f"{self.robot_unique_id}_prime_part_{part_id}"

    @property
    def suggested_object_id(self) -> str:
        return f"prime_part_{self._part_id}"

    @property
    def _part(self) -> Any:
        coordinator = getattr(self._config_entry.runtime_data, "prime_parts_coordinator", None)
        if coordinator is None or not coordinator.data:
            return None
        return coordinator.data.get(self._part_id)

    @property
    def available(self) -> bool:
        return super().available and self._part is not None

    @property
    def native_value(self) -> int | None:
        part = self._part
        return None if part is None else part.count_remaining

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Taken from the server per part, not fixed.

        The same robot reports hours for its filter and routines for
        its mop pads. A single hard-coded unit would be wrong for most
        of them.
        """
        part = self._part
        if part is None:
            return None
        return _PART_COUNT_UNITS.get((part.count_type or "").lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        part = self._part
        if part is None:
            return {}
        return {
            "part_id": part.part_id,
            "count_type": part.count_type,
            "count_used": part.count_used,
            "minutes_remaining": part.minutes_remaining,
            "category": part.counter_category,
        }
