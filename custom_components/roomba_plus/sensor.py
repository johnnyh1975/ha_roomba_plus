"""Sensor platform for Roomba+.

SENSOR-SPLIT (v3.4.0): this file used to be a 5.812-line monolith
holding every sensor class. It is now the thin platform entry point
(HA loads sensor.py::async_setup_entry by convention — that can't
move) plus a facade that re-exports everything from the five domain
modules below, so every existing import path (tests, callbacks.py,
device_tracker.py, repairs.py, services.py) keeps working unchanged:

  sensor_helpers.py      — descriptor value-functions (pure functions)
  sensor_core.py         — RoombaSensorDescription, SENSORS, RoombaSensor
  sensor_cloud.py        — cloud-history/-raw/-analytics/-health sensors
  sensor_rooms.py        — mission/room/zone sensors + room helpers
  sensor_diagnostics.py  — raw-state/firmware/integration-health/reset sensors

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

from homeassistant.components.sensor import (
    SensorDeviceClass,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    SensorStateClass,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
# ir/dt_util: not used by this module's own code. Kept importable here as
# `sensor.ir`/`sensor.dt_util` purely so that tests using
# mock.patch("...sensor.ir.async_get", ...) or
# mock.patch("...sensor.dt_util.utcnow", ...) still resolve — both are
# shared HA singleton modules, so patching a sub-attribute on them here
# also affects the sensor_helpers.py code that actually calls them. Do not
# remove as "unused" without checking test_repairs.py/test_sensors.py first.
from homeassistant.helpers import issue_registry as ir  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
from homeassistant.util import dt as dt_util  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py

from . import roomba_reported_state
from .const import CONF_CORRELATION_ENTITIES
from .models import RoombaConfigEntry

from .sensor_core import (
    RoombaSensor,
    RoombaSensorDescription,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    SENSORS,
)
from .sensor_helpers import (
    _area_cleaned_today,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _battery_age_days,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _battery_capacity_retention,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _completion_rate_30d,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _compute_integration_health,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _estimated_battery_eol,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _expire_minutes_remaining,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _health_band,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _integration_health_plain_status,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _last_error_code_value,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _last_mission_team_id,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mission_elapsed_value,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mission_store_last_started_at,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mission_store_value,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mop_behavior,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mop_clean_mode,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mop_tank_status,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _next_likely_clean_window,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _parse_netinfo_addr,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _phase_value,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _presence_opportunities,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _presence_utilisation,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _problem_zone_value,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_wifi_floor,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_wifi_quality_pct,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_wifi_stability,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _recharge_minutes_remaining,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _robot_health_plain_status,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _total_energy_consumed_kwh,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _ts_or_none,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
)
from .sensor_cloud import (
    CLOUD_HISTORY_SENSORS,
    CloudHistorySensor,
    CloudHistorySensorDescription,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    CloudRawSensor,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    CloudRawSensorDescription,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    RoombaCleaningAnalytics30dSensor,
    RoombaCleaningPerformanceSensor,
    RoombaEventCounts30dSensor,
    RoombaHealthScoreTrendSensor,
    RoombaMissionsPerChargeSensor,
    RoombaRobotHealthSensor,
    RoombaWifiChannelStabilitySensor,
    RoombaWifiHealthSensor,
    RoombaWifiLastChannelSensor,
    _channel_to_band,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mh_sqft_to_m2,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mh_total_minutes,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _mh_total_missions,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_cleaning_speed,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_cleaning_speed_trend,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_cloud_last_error_attrs,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_cloud_last_error_code,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_cloud_last_error_time,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_completion_rate,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_dirt_density,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_dirt_events,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_evacuations,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_recharge_fraction,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _raw_recharges,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
)
from .sensor_rooms import (
    RoombaDirtCorrelationSensor,
    RoombaEdgeCoverageSensor,
    RoombaLastMissionSummarySensor,
    RoombaLearningPercentageSensor,
    RoombaMissionProgress,
    RoombaRelocalisationRateSensor,
    RoombaRoomAccessibilityScoresSensor,
    RoombaRoomAreasSensor,
    RoombaRoomCleaningHistorySensor,
    RoombaRoomsOverdueSensor,
    RoombaZoneSummarySensor,
    _compute_room_time_estimates,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _get_planned_room_order,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _id_to_display_name,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _region_maps_for,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
    _resolve_smart_tier_room_state,  # noqa: F401 — SENSOR-SPLIT facade re-export, see test_sensor_module_split.py
)
from .sensor_diagnostics import (
    RawStateSensor,
    RoombaFirmwareVersionSensor,
    RoombaIntegrationHealthSensor,
    RoombaOptimalCleanWindow,
    RoombaResetDiagnosticsSensor,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all applicable sensors for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)
    data = config_entry.runtime_data

    entities: list = [
        RoombaSensor(roomba, blid, description, config_entry)
        for description in SENSORS
        if description.filter_fn(state)
    ]

    # Cloud history sensors: lifetime stats from /missionhistory.
    # Available for all robots when cloud credentials are configured.
    # Data comes from the cloud coordinator (daily poll) — not from MQTT.
    if data.has_cloud:
        cc = data.cloud_coordinator  # type: ignore[union-attr]
        entities.extend([
            CloudHistorySensor(roomba, blid, cc, desc)
            for desc in CLOUD_HISTORY_SENSORS
        ])
        # SC1 (v3.0): CloudRawSensor descriptors removed — deprecated sensors
        # deactivated. Consolidated replacements: cleaning_performance,
        # cleaning_analytics_30d, wifi_health, event_counts_30d.
        pass

    # F12d — recent_edge_coverage_ratio (map-capable robots with GridStore)
    if data.grid_store is not None and data.map_capability.value != "none":
        entities.append(RoombaEdgeCoverageSensor(roomba, blid, config_entry))

    # IA74-LP (v2.6.0) — learning_percentage (SMART + cloud only)
    if data.map_capability.value == "smart" and data.cloud_coordinator is not None:
        entities.append(RoombaLearningPercentageSensor(roomba, blid, config_entry))

    # IA74-ZONE (v2.6.0) — zone summary (SMART + cloud only)
    if data.map_capability.value == "smart" and data.cloud_coordinator is not None:
        entities.append(RoombaZoneSummarySensor(roomba, blid, config_entry))

    # v3.3.0 ROOM-SCHED — rooms_overdue (SMART + cloud only: room data
    # source is timeline.finEvents from cloud-enriched records)
    if data.map_capability.value == "smart" and data.cloud_coordinator is not None:
        entities.append(RoombaRoomsOverdueSensor(roomba, blid, config_entry))

    # v3.3.0 CROSS-CORR — opt-in: only when correlation entities are
    # configured; cloud required (dirt field is cloud-enriched)
    if (
        config_entry.options.get(CONF_CORRELATION_ENTITIES)
        and data.cloud_coordinator is not None
    ):
        entities.append(RoombaDirtCorrelationSensor(roomba, blid, config_entry))

    # F12a — optimal_clean_window sensor (presence scheduling active)
    if data.presence_manager is not None:
        entities.append(RoombaOptimalCleanWindow(roomba, blid, config_entry))

    # MP1 (v2.6.0) — mission progress (SMART + cloud + timer store)
    if (
        data.map_capability.value == "smart"
        and data.cloud_coordinator is not None
        and data.mission_timer_store is not None
    ):
        entities.append(RoombaMissionProgress(roomba, blid, config_entry))

    # SC1 (v2.7.0) — consolidated analytics sensors (cloud credentials required)
    if data.has_cloud:
        cc = data.cloud_coordinator  # type: ignore[union-attr]
        entities.extend([
            RoombaCleaningPerformanceSensor(roomba, blid, cc, config_entry),
            RoombaCleaningAnalytics30dSensor(roomba, blid, cc, config_entry),
            RoombaWifiHealthSensor(roomba, blid, cc, config_entry),
            RoombaEventCounts30dSensor(roomba, blid, cc, config_entry),
        ])
        # BAT-ARCH (v2.8.0) — archive-sourced WiFi + charge sensors
        entities.extend([
            RoombaWifiLastChannelSensor(roomba, blid, cc, config_entry),
            RoombaWifiChannelStabilitySensor(roomba, blid, cc, config_entry),
            RoombaMissionsPerChargeSensor(roomba, blid, cc, config_entry),
        ])

    # L8 (v2.7.0) — composite robot health score (cloud credentials required)
    if data.has_cloud and data.cloud_coordinator is not None:
        entities.append(
            RoombaRobotHealthSensor(roomba, blid, data.cloud_coordinator, config_entry)
        )
        # v3.2.0 L10 — self-calibrating health score trend, same gate as L8
        # itself (the trend has nothing to track without the score it's
        # derived from).
        entities.append(
            RoombaHealthScoreTrendSensor(roomba, blid, data.cloud_coordinator, config_entry)
        )

    # Raw state sensor: opt-in, always created, exposes full MQTT state as attributes.
    entities.append(RawStateSensor(roomba, blid))

    # v2.8.3 FW-SENSOR — firmware version, always created (universal across all robot families).
    entities.append(RoombaFirmwareVersionSensor(roomba, blid))

    # v2.9.0 INTEG-HEALTH — integration health meta-sensor, always created.
    entities.append(RoombaIntegrationHealthSensor(roomba, blid, config_entry))

    # v3.1.0 LAST-MISSION-SUMMARY — always created; shows None when no record exists.
    entities.append(RoombaLastMissionSummarySensor(roomba, blid, config_entry))

    # v3.1.0 ROOM-CLEANING-HISTORY — per-room last-clean timestamps (SMART, cloud).
    # Only created when mission_store is available; no tier gate — any robot that
    # accumulates room data in last_cleaned_rooms will populate this sensor.
    if data.mission_store is not None:
        entities.append(RoombaRoomCleaningHistorySensor(roomba, blid, config_entry))

    # v3.1.0 ROOM-SIZE — per-room floor area in m² from UMF polygons (SMART only).
    if data.umf_aligner is not None:
        entities.append(RoombaRoomAreasSensor(roomba, blid, config_entry))

    # v3.2.0 ROOM-ACCESS — same gate as ROOM-SIZE (room polygons come from
    # the same UmfAligner source).
    if data.umf_aligner is not None:
        entities.append(RoombaRoomAccessibilityScoresSensor(roomba, blid, config_entry))

    # v3.2.0 RESET-DIAGNOSTICS — reset-cause breakdown, previously unread.
    if "bbrstinfo" in state:
        entities.append(RoombaResetDiagnosticsSensor(roomba, blid))

    # v3.1.0 L9-MAP — relocalisation rate (SMART only, mssnNavStats confirmed
    # absent on EPHEMERAL tier).
    if data.umf_aligner is not None:
        entities.append(RoombaRelocalisationRateSensor(roomba, blid, config_entry))

    async_add_entities(entities)
