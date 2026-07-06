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
    SensorDeviceClass,
    SensorStateClass,
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
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import CONF_CORRELATION_ENTITIES
from .models import RoombaConfigEntry

from .sensor_core import (
    RoombaSensor,
    RoombaSensorDescription,
    SENSORS,
)
from .sensor_helpers import (
    _area_cleaned_today,
    _battery_age_days,
    _battery_capacity_retention,
    _completion_rate_30d,
    _compute_integration_health,
    _estimated_battery_eol,
    _expire_minutes_remaining,
    _health_band,
    _integration_health_plain_status,
    _last_error_code_value,
    _last_mission_team_id,
    _mission_elapsed_value,
    _mission_store_last_started_at,
    _mission_store_value,
    _mop_behavior,
    _mop_clean_mode,
    _mop_tank_status,
    _next_likely_clean_window,
    _parse_netinfo_addr,
    _phase_value,
    _presence_opportunities,
    _presence_utilisation,
    _problem_zone_value,
    _raw_wifi_floor,
    _raw_wifi_quality_pct,
    _raw_wifi_stability,
    _recharge_minutes_remaining,
    _robot_health_plain_status,
    _total_energy_consumed_kwh,
    _ts_or_none,
)
from .sensor_cloud import (
    CLOUD_HISTORY_SENSORS,
    CloudHistorySensor,
    CloudHistorySensorDescription,
    CloudRawSensor,
    CloudRawSensorDescription,
    RoombaCleaningAnalytics30dSensor,
    RoombaCleaningPerformanceSensor,
    RoombaEventCounts30dSensor,
    RoombaHealthScoreTrendSensor,
    RoombaMissionsPerChargeSensor,
    RoombaRobotHealthSensor,
    RoombaWifiChannelStabilitySensor,
    RoombaWifiHealthSensor,
    RoombaWifiLastChannelSensor,
    _channel_to_band,
    _mh_sqft_to_m2,
    _mh_total_minutes,
    _mh_total_missions,
    _raw_cleaning_speed,
    _raw_cleaning_speed_trend,
    _raw_cloud_last_error_attrs,
    _raw_cloud_last_error_code,
    _raw_cloud_last_error_time,
    _raw_completion_rate,
    _raw_dirt_density,
    _raw_dirt_events,
    _raw_evacuations,
    _raw_recharge_fraction,
    _raw_recharges,
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
    _compute_room_time_estimates,
    _get_planned_room_order,
    _id_to_display_name,
    _region_maps_for,
    _resolve_smart_tier_room_state,
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
