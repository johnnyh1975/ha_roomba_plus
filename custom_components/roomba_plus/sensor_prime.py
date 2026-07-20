"""V4/Prime (CLOUD_ONLY) sensors for the Roomba+ sensor platform.

NEW (this session) -- the first CLOUD_ONLY-aware sensors this platform
has ever had. sensor.py's existing SENSORS/RoombaSensor machinery
(sensor_core.py) is deeply tied to roomba_reported_state()'s Classic
shape (dozens of filter_fn/value-function callables never audited
against roomba=None) -- rather than risk that large, untested surface,
these are DELIBERATELY separate, minimal entity classes, built directly
on PrimeCoordinator/MissionTimelineReport, mirroring the same
"separate CLOUD_ONLY path" pattern already established for vacuum.py
and _async_setup_entry_prime().

Both entities pass roomba=None into IRobotEntity.__init__() -- already
confirmed safe (roomba_reported_state(None) returns {}), the same
pattern the CLOUD_ONLY vacuum entity already relies on.

Deliberately NOT battery/dock-boolean sensors: RobotStatusV2 remains
unconfirmed (see ROOMBA_PLUS_VERSION_PLAN_v4_onwards.md). Only what
mission/timeline/report itself confirms, plus our own connection
health -- nothing guessed.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory

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
