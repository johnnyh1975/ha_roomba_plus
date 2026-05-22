"""Sensor platform for Roomba+.

Sensors are arranged in four logical groups that determine their visibility
and placement on the HA device page:

GROUP 1 — Primary Status (EntityCategory = None)
  Appears under "Sensoren" alongside the vacuum control.
  Always visible, daily-use values: battery, phase, error.

GROUP 2 — Operational (EntityCategory.DIAGNOSTIC, enabled)
  Appears under "Diagnose". Useful for automations and troubleshooting.

GROUP 3 — Maintenance (EntityCategory.DIAGNOSTIC, enabled)
  Filter/brush life and battery wear — actionable values.

GROUP 4 — Statistics (EntityCategory.DIAGNOSTIC, enabled)
  Mission counters and timing — informational.

GROUP 5 — Opt-in (EntityCategory.DIAGNOSTIC, disabled)
  Hidden until user explicitly enables.

DEVICE-SPECIFIC (capability-gated)
  Created only when the robot reports the relevant hardware.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
import datetime
import datetime as dt_stdlib

from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import (
    CARPET_BOOST_LABELS,
    CLEAN_BASE_LABELS,
    CLEAN_MODE_LABELS,
    CONF_BRUSH_HOURS,
    CONF_FILTER_HOURS,
    DEFAULT_BRUSH_HOURS,
    DEFAULT_FILTER_HOURS,
    ERROR_CODE_LABELS,
    JOB_INITIATOR_LABELS,
    MOP_RANK_LABELS,
    NOT_READY_LABELS,
    PAD_LABELS,
    PHASE_LABELS,
    has_carpet_boost,
    has_clean_base,
    has_pose,
    has_smart_map,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry


@dataclass(frozen=True, kw_only=True)
class RoombaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[IRobotEntity], StateType]
    filter_fn: Callable[[dict[str, Any]], bool] = field(
        default_factory=lambda: lambda _: True
    )


def _carpet_boost_mode(entity: IRobotEntity) -> str:
    vac_high = entity.vacuum_state.get("vacHigh")
    carpet_boost = entity.vacuum_state.get("carpetBoost")
    if vac_high is None or carpet_boost is None:
        return CARPET_BOOST_LABELS["n-a"]
    if carpet_boost:
        return CARPET_BOOST_LABELS["auto"]
    if vac_high:
        return CARPET_BOOST_LABELS["performance"]
    return CARPET_BOOST_LABELS["eco"]


def _clean_mode(entity: IRobotEntity) -> str:
    no_auto = entity.vacuum_state.get("noAutoPasses")
    two_pass = entity.vacuum_state.get("twoPass")
    if no_auto is None or two_pass is None:
        return CLEAN_MODE_LABELS["n-a"]
    if no_auto and two_pass:
        return CLEAN_MODE_LABELS["two"]
    if no_auto and not two_pass:
        return CLEAN_MODE_LABELS["one"]
    return CLEAN_MODE_LABELS["auto"]


_ACTIVE_PHASES = {"run", "hmMidMsn", "hmPostMsn", "hmUsrDock", "new", "resume"}


# notReady bitmask — individual bit meanings for i7/s9/j-series
_NOT_READY_BITS: dict[int, str] = {
    1:   "Low battery",
    2:   "Bin full",
    4:   "Map not ready",
    8:   "Not on dock",
    16:  "Lid open",
    32:  "Tank empty",
    64:  "Updating map",
    128: "Pending task",
}


def _not_ready_value(entity: "IRobotEntity") -> str:
    """Decode notReady bitmask into a human-readable label.

    NOT_READY_LABELS covers exact combined values seen in the wild.
    For unlisted combinations, decode bit by bit so any value is readable
    rather than falling back to a raw integer.
    """
    nr: int = entity.clean_mission_status.get("notReady", 0)
    if nr in NOT_READY_LABELS:
        return NOT_READY_LABELS[nr]
    if nr == 0:
        return "Ready"
    # Decode individual bits for unknown combinations.
    parts = [label for bit, label in sorted(_NOT_READY_BITS.items()) if nr & bit]
    return ", ".join(parts) if parts else f"Not ready ({nr})"


def _error_value(entity: "IRobotEntity") -> str:
    """Error label — suppressed when the robot is docked/idle after a mission.

    cleanMissionStatus.error persists across missions: the firmware does not
    reset it to 0 when the robot docks after a failure. Showing the stale error
    while the robot charges would be misleading, so we return "None" whenever
    cycle is "none" (no active or queued mission) and phase indicates rest.
    """
    status = entity.clean_mission_status
    cycle = status.get("cycle", "")
    phase = status.get("phase", "")
    error = status.get("error", 0)

    # No active mission and robot is resting — suppress stale error.
    if cycle == "none" and phase in ("charge", "stop", "idle", ""):
        return "None"

    return ERROR_CODE_LABELS.get(error, entity.vacuum.error_message or "None")


def _phase_value(entity: "IRobotEntity") -> str:
    """Phase label with Idle and Stopped detection."""
    status = entity.clean_mission_status
    phase = status.get("phase", "")
    cycle = status.get("cycle", "")
    battery = entity.vacuum_state.get("batPct")
    if phase == "charge" and battery == 100:
        return "Idle"
    if cycle == "none" and phase == "stop":
        return "Stopped"
    return PHASE_LABELS.get(phase, phase or "Unknown")


def _mission_elapsed_value(entity: "IRobotEntity") -> float | None:
    """Elapsed mission time in minutes; None if no active mission."""
    ts = entity.clean_mission_status.get("mssnStrtTm")
    if not ts:
        return None
    try:
        elapsed = dt_util.now(datetime.timezone.utc) - datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        return round(elapsed.total_seconds() / 60, 1)
    except (TypeError, ValueError, OSError):
        return None


def _ts_or_none(ts):
    """Convert Unix timestamp int to UTC datetime, or None."""
    if not ts or ts == 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


SENSORS: tuple[RoombaSensorDescription, ...] = (

    # GROUP 1 — Primary Status (EntityCategory=None → "Sensoren" section)

    RoombaSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda e: e.vacuum_state.get("batPct"),
    ),
    RoombaSensorDescription(
        key="phase",
        translation_key="phase",
        icon="mdi:robot-vacuum",
        entity_category=None,
        value_fn=_phase_value,
    ),
    RoombaSensorDescription(
        key="error",
        translation_key="error",
        icon="mdi:alert-circle-outline",
        entity_category=None,
        value_fn=_error_value,
    ),

    # GROUP 2 — Operational (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="readiness",
        translation_key="readiness",
        icon="mdi:check-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_not_ready_value,
    ),
    RoombaSensorDescription(
        key="job_initiator",
        translation_key="job_initiator",
        icon="mdi:account-arrow-right-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: JOB_INITIATOR_LABELS.get(
            e.clean_mission_status.get("initiator", "none"), "None"
        ),
    ),
    RoombaSensorDescription(
        key="clean_mode",
        translation_key="clean_mode",
        icon="mdi:replay",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_clean_mode,
    ),
    RoombaSensorDescription(
        key="carpet_boost_mode",
        translation_key="carpet_boost_mode",
        icon="mdi:turbine",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_carpet_boost,
        value_fn=_carpet_boost_mode,
    ),

    # GROUP 3 — Maintenance (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="filter_remaining_hours",
        translation_key="filter_remaining_hours",
        icon="mdi:air-filter",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: None,
    ),
    RoombaSensorDescription(
        key="brush_remaining_hours",
        translation_key="brush_remaining_hours",
        icon="mdi:brush",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: None,
    ),
    RoombaSensorDescription(
        key="battery_cycles",
        translation_key="battery_cycles",
        icon="mdi:battery-sync-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            e.battery_stats.get("nLithChrg") or e.battery_stats.get("nNimhChrg")
        ),
    ),

    # GROUP 4 — Statistics (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="total_missions",
        translation_key="total_missions",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssn"),
    ),
    RoombaSensorDescription(
        key="successful_missions",
        translation_key="successful_missions",
        icon="mdi:check-decagram-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnOk"),
    ),
    RoombaSensorDescription(
        key="canceled_missions",
        translation_key="canceled_missions",
        icon="mdi:hand-back-left-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnC"),
    ),
    RoombaSensorDescription(
        key="failed_missions",
        translation_key="failed_missions",
        icon="mdi:close-circle-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnF"),
    ),
    RoombaSensorDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.run_stats.get("hr"),
    ),
    RoombaSensorDescription(
        key="average_mission_time",
        translation_key="average_mission_time",
        icon="mdi:clock-check-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("aMssnM"),
    ),

    # GROUP 5 — Opt-in (DIAGNOSTIC, disabled by default)

    RoombaSensorDescription(
        key="total_cleaned_area",
        translation_key="total_cleaned_area",
        icon="mdi:floor-plan",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            None
            if (sqft := e.run_stats.get("sqft")) is None
            else round(sqft * 0.0929, 1)
        ),
    ),
    RoombaSensorDescription(
        key="last_mission",
        translation_key="last_mission",
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.last_mission,  # datetime object — device_class=TIMESTAMP requires datetime, not str
    ),
    RoombaSensorDescription(
        key="scrubs_count",
        translation_key="scrubs_count",
        icon="mdi:magnify-scan",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.run_stats.get("nScrubs"),
    ),
    RoombaSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("rssi"),
    ),

    RoombaSensorDescription(
        key="snr",
        translation_key="snr",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("snr"),
    ),

    RoombaSensorDescription(
        key="signal_noise",
        translation_key="signal_noise",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("noise"),
    ),

    RoombaSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        icon="mdi:ip-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("netinfo", {}).get("addr"),
    ),


    # Navigation quality (VSLAM robots: 900/i/s/j — opt-in, disabled by default)
    # l_squal: 0–100, measures how well the VSLAM algorithm can navigate.
    # Low values indicate poor lighting or significant environmental changes.
    RoombaSensorDescription(
        key="nav_quality",
        translation_key="nav_quality",
        icon="mdi:map-check-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=has_pose,
        value_fn=lambda e: e.vacuum_state.get("mssnNavStats", {}).get("l_squal"),
    ),

    # Mission-time sensors
    RoombaSensorDescription(
        key="mission_start_time",
        translation_key="mission_start_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            e.last_mission
            if e.clean_mission_status.get("phase") in _ACTIVE_PHASES
            else None
        ),
    ),

    RoombaSensorDescription(
        key="mission_elapsed_time",
        translation_key="mission_elapsed_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timeline-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mission_elapsed_value,
    ),

    RoombaSensorDescription(
        key="mission_recharge_time",
        translation_key="mission_recharge_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:battery-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("rechrgTm")),
    ),

    RoombaSensorDescription(
        key="mission_expire_time",
        translation_key="mission_expire_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:timeline-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("expireTm")),
    ),

    # Schedule sensor (all models with cleanSchedule2 or cleanSchedule)

    RoombaSensorDescription(
        key="next_clean",
        translation_key="next_clean",
        icon="mdi:calendar-clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: bool(s.get("cleanSchedule2") or s.get("cleanSchedule")),
        value_fn=lambda e: None,   # computed in RoombaSensor.native_value
    ),

    # Device-specific: Clean Base

    RoombaSensorDescription(
        key="clean_base_status",
        translation_key="clean_base_status",
        icon="mdi:delete-empty-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_clean_base,
        value_fn=lambda e: CLEAN_BASE_LABELS.get(
            e.vacuum_state.get("dock", {}).get("state", -2), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="dock_tank_level",
        translation_key="dock_tank_level",
        icon="mdi:water-pump",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s.get("dock", {}),
        value_fn=lambda e: e.dock_tank_level,
    ),

    # Device-specific: Braava / mop

    RoombaSensorDescription(
        key="tank_level",
        translation_key="tank_level",
        icon="mdi:water-outline",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.tank_level,
    ),
    RoombaSensorDescription(
        key="mop_pad",
        translation_key="mop_pad",
        icon="mdi:square-rounded-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "detectedPad" in s,
        value_fn=lambda e: PAD_LABELS.get(
            e.vacuum_state.get("detectedPad", "invalid"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_behavior",
        translation_key="mop_behavior",
        icon="mdi:wiper-wash",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "rankOverlap" in s,
        value_fn=lambda e: MOP_RANK_LABELS.get(
            e.vacuum_state.get("rankOverlap"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_tank_level",
        translation_key="mop_tank_level",
        icon="mdi:water",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.vacuum_state.get("tankLvl"),
    ),
)

# Raw state sensor is not in SENSORS tuple — it has a bespoke entity class
# (RawStateSensor) that exposes the full vacuum_state as extra_state_attributes.


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all applicable sensors for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)

    entities = [
        RoombaSensor(roomba, blid, description, config_entry)
        for description in SENSORS
        if description.filter_fn(state)
    ]
    # Raw state sensor: opt-in, always created, exposes full MQTT state as attributes.
    entities.append(RawStateSensor(roomba, blid))
    async_add_entities(entities)


class RoombaSensor(IRobotEntity, SensorEntity):
    """A sensor entity for Roomba+, driven by the EntityDescription pattern."""

    entity_description: RoombaSensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        description: RoombaSensorDescription,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        key = self.entity_description.key
        options = self._config_entry.options
        store = self._config_entry.runtime_data.maintenance_store

        if key == "filter_remaining_hours":
            threshold = options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.filter_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "brush_remaining_hours":
            threshold = options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.brush_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "next_clean":
            return self._calc_next_clean()

        return self.entity_description.value_fn(self)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        key = self.entity_description.key

        if key == "battery":
            return "batPct" in new_state
        if key in ("rssi", "snr", "signal_noise"):
            return "signal" in new_state
        if key == "ip_address":
            return "netinfo" in new_state
        if key in ("mission_start_time", "mission_elapsed_time",
                   "mission_recharge_time", "mission_expire_time"):
            return "cleanMissionStatus" in new_state
        if key in ("phase", "error", "readiness", "job_initiator"):
            return "cleanMissionStatus" in new_state or "error" in new_state
        if key in ("clean_base_status", "dock_tank_level"):
            return "dock" in new_state
        if key in ("mop_pad", "mop_behavior", "mop_tank_level", "tank_level"):
            return any(k in new_state for k in ("detectedPad", "rankOverlap", "tankLvl"))
        if key in ("filter_remaining_hours", "brush_remaining_hours",
                   "scrubs_count", "total_cleaning_time"):
            return "bbrun" in new_state
        if key in ("total_missions", "successful_missions", "canceled_missions",
                   "failed_missions", "average_mission_time", "last_mission"):
            return "bbmssn" in new_state
        if key == "battery_cycles":
            return "bbchg3" in new_state
        if key in ("clean_mode", "carpet_boost_mode"):
            return any(k in new_state for k in
                       ("noAutoPasses", "twoPass", "carpetBoost", "vacHigh"))
        if key == "nav_quality":
            return "mssnNavStats" in new_state
        if key == "next_clean":
            return "cleanSchedule2" in new_state or "cleanSchedule" in new_state

        return len(new_state) > 1 or "signal" not in new_state

    def _calc_next_clean(self):
        """Return next scheduled cleaning time as a timezone-aware datetime.

        Supports cleanSchedule2 (i/s/j, array of entries with cmd) and
        cleanSchedule (900/600-series, simple time list).

        Roomba day numbering: 0=Sunday … 6=Saturday.
        """
        # Try cleanSchedule2 first (richer format)
        schedule2 = self.vacuum_state.get("cleanSchedule2", [])
        if schedule2:
            return self._next_from_schedule2(schedule2)

        # Fall back to legacy cleanSchedule
        schedule = self.vacuum_state.get("cleanSchedule", {})
        if schedule:
            return self._next_from_schedule_v1(schedule)

        return None

    def _next_from_schedule2(self, entries: list) -> dt_util.dt.datetime | None:
        """Calculate next clean from cleanSchedule2 entries."""
        now = dt_util.now()
        candidates: list[dt_util.dt.datetime] = []

        for entry in entries:
            if not entry.get("enabled", False):
                continue
            start = entry.get("start", {})
            hour = start.get("hour", 0)
            minute = start.get("min", 0)
            for roomba_day in start.get("day", []):
                # Roomba: 0=Sun … 6=Sat → Python weekday: Mon=0 … Sun=6
                py_wd = (roomba_day - 1) % 7
                days_ahead = (py_wd - now.weekday()) % 7
                candidate = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ) + dt_stdlib.timedelta(days=days_ahead)
                if candidate <= now:
                    candidate += dt_stdlib.timedelta(days=7)
                candidates.append(candidate)

        return min(candidates) if candidates else None

    def _next_from_schedule_v1(self, schedule: dict) -> dt_util.dt.datetime | None:
        """Calculate next clean from legacy cleanSchedule dict.

        cleanSchedule format: {cycle: ["none","start",...], h: [9,...], m: [0,...]}
        where cycle has one entry per weekday (Sun=0 … Sat=6).
        """
        now = dt_util.now()
        cycle = schedule.get("cycle", [])
        hours = schedule.get("h", [])
        mins = schedule.get("m", [])
        candidates: list[dt_util.dt.datetime] = []

        for i, (cyc, h, m) in enumerate(zip(cycle, hours, mins)):
            if cyc != "start":
                continue
            # i = Roomba day (0=Sun … 6=Sat)
            py_wd = (i - 1) % 7
            days_ahead = (py_wd - now.weekday()) % 7
            candidate = now.replace(
                hour=h, minute=m, second=0, microsecond=0
            ) + dt_stdlib.timedelta(days=days_ahead)
            if candidate <= now:
                candidate += dt_stdlib.timedelta(days=7)
            candidates.append(candidate)

        return min(candidates) if candidates else None


class RawStateSensor(IRobotEntity, SensorEntity):
    """Opt-in sensor that exposes the full MQTT state as extra_state_attributes.

    The sensor state value is a simple count of top-level keys in the reported
    state — useful as a change indicator. All actual data lives in attributes.

    Disabled by default — must be explicitly enabled in the HA UI.
    Intended for power users and debugging unknown robot models.
    """

    _attr_translation_key = "raw_state"
    _attr_icon = "mdi:code-json"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_raw_state"

    @property
    def native_value(self) -> int:
        """Return count of top-level reported state keys."""
        return len(self.vacuum_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full reported state as attributes.

        Complex nested values (dicts, lists) are JSON-serialised to strings
        so that HA's attribute storage never receives un-serialisable objects.
        All values are HA-safe primitives after this conversion.
        """
        import json as _json
        result: dict[str, Any] = {}
        for key, value in self.vacuum_state.items():
            if isinstance(value, (dict, list)):
                try:
                    result[key] = _json.dumps(value, default=str)
                except Exception:  # noqa: BLE001
                    result[key] = str(value)
            else:
                result[key] = value
        return result

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Update on any MQTT message — this is a catch-all debug sensor
        return True
