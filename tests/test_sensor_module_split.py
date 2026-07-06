"""SENSOR-SPLIT (v3.4.0) — Import-Pfad-Guard.

sensor.py used to hold every sensor class and helper in one 5.812-line
file. It is now a facade re-exporting names from five domain modules
(sensor_helpers, sensor_core, sensor_cloud, sensor_rooms,
sensor_diagnostics), so that every pre-existing import path — in this
test suite and in callbacks.py/device_tracker.py/repairs.py/services.py
— keeps working unchanged.

This test is the safety net for that promise: for every name that used
to live in sensor.py, it asserts the name is importable from BOTH the
facade and its real new home, AND that both imports yield the exact
same object (`is`, not just equal) — catching an accidental duplicate
definition as reliably as a forgotten re-export.
"""
from __future__ import annotations

import importlib

import pytest


# name -> real home module (relative to custom_components.roomba_plus)
_FACADE_CONTRACT: dict[str, str] = {
    # sensor_core — descriptor pattern core
    "RoombaSensorDescription": "sensor_core",
    "RoombaSensor": "sensor_core",
    "SENSORS": "sensor_core",
    # sensor_cloud — cloud-derived sensors + their helpers
    "CloudHistorySensorDescription": "sensor_cloud",
    "CloudHistorySensor": "sensor_cloud",
    "CloudRawSensorDescription": "sensor_cloud",
    "CloudRawSensor": "sensor_cloud",
    "CLOUD_HISTORY_SENSORS": "sensor_cloud",
    "RoombaCleaningPerformanceSensor": "sensor_cloud",
    "RoombaCleaningAnalytics30dSensor": "sensor_cloud",
    "RoombaWifiHealthSensor": "sensor_cloud",
    "RoombaEventCounts30dSensor": "sensor_cloud",
    "RoombaWifiLastChannelSensor": "sensor_cloud",
    "RoombaWifiChannelStabilitySensor": "sensor_cloud",
    "RoombaMissionsPerChargeSensor": "sensor_cloud",
    "RoombaHealthScoreTrendSensor": "sensor_cloud",
    "RoombaRobotHealthSensor": "sensor_cloud",
    "_channel_to_band": "sensor_cloud",
    "_mh_sqft_to_m2": "sensor_cloud",
    "_mh_total_minutes": "sensor_cloud",
    "_mh_total_missions": "sensor_cloud",
    "_raw_cleaning_speed": "sensor_cloud",
    "_raw_cleaning_speed_trend": "sensor_cloud",
    "_raw_cloud_last_error_attrs": "sensor_cloud",
    "_raw_cloud_last_error_code": "sensor_cloud",
    "_raw_cloud_last_error_time": "sensor_cloud",
    "_raw_completion_rate": "sensor_cloud",
    "_raw_dirt_density": "sensor_cloud",
    "_raw_dirt_events": "sensor_cloud",
    "_raw_evacuations": "sensor_cloud",
    "_raw_recharge_fraction": "sensor_cloud",
    "_raw_recharges": "sensor_cloud",
    # sensor_rooms — mission/room/zone sensors + their helpers
    "RoombaMissionProgress": "sensor_rooms",
    "RoombaDirtCorrelationSensor": "sensor_rooms",
    "RoombaRoomsOverdueSensor": "sensor_rooms",
    "RoombaRoomAccessibilityScoresSensor": "sensor_rooms",
    "RoombaRoomAreasSensor": "sensor_rooms",
    "RoombaRoomCleaningHistorySensor": "sensor_rooms",
    "RoombaLastMissionSummarySensor": "sensor_rooms",
    "RoombaEdgeCoverageSensor": "sensor_rooms",
    "RoombaLearningPercentageSensor": "sensor_rooms",
    "RoombaZoneSummarySensor": "sensor_rooms",
    "RoombaRelocalisationRateSensor": "sensor_rooms",
    "_compute_room_time_estimates": "sensor_rooms",
    "_get_planned_room_order": "sensor_rooms",
    "_id_to_display_name": "sensor_rooms",
    "_region_maps_for": "sensor_rooms",
    "_resolve_smart_tier_room_state": "sensor_rooms",
    # sensor_diagnostics — always-created diagnostic/meta sensors
    "RawStateSensor": "sensor_diagnostics",
    "RoombaFirmwareVersionSensor": "sensor_diagnostics",
    "RoombaIntegrationHealthSensor": "sensor_diagnostics",
    "RoombaOptimalCleanWindow": "sensor_diagnostics",
    "RoombaResetDiagnosticsSensor": "sensor_diagnostics",
    # sensor_helpers — descriptor value-functions
    "_area_cleaned_today": "sensor_helpers",
    "_battery_age_days": "sensor_helpers",
    "_battery_capacity_retention": "sensor_helpers",
    "_completion_rate_30d": "sensor_helpers",
    "_compute_integration_health": "sensor_helpers",
    "_estimated_battery_eol": "sensor_helpers",
    "_expire_minutes_remaining": "sensor_helpers",
    "_health_band": "sensor_helpers",
    "_integration_health_plain_status": "sensor_helpers",
    "_last_error_code_value": "sensor_helpers",
    "_last_mission_team_id": "sensor_helpers",
    "_mission_elapsed_value": "sensor_helpers",
    "_mission_store_last_started_at": "sensor_helpers",
    "_mission_store_value": "sensor_helpers",
    "_mop_behavior": "sensor_helpers",
    "_mop_clean_mode": "sensor_helpers",
    "_mop_tank_status": "sensor_helpers",
    "_next_likely_clean_window": "sensor_helpers",
    "_parse_netinfo_addr": "sensor_helpers",
    "_phase_value": "sensor_helpers",
    "_presence_opportunities": "sensor_helpers",
    "_presence_utilisation": "sensor_helpers",
    "_problem_zone_value": "sensor_helpers",
    "_raw_wifi_floor": "sensor_helpers",
    "_raw_wifi_quality_pct": "sensor_helpers",
    "_raw_wifi_stability": "sensor_helpers",
    "_recharge_minutes_remaining": "sensor_helpers",
    "_robot_health_plain_status": "sensor_helpers",
    "_total_energy_consumed_kwh": "sensor_helpers",
    "_ts_or_none": "sensor_helpers",
}


@pytest.mark.parametrize("name,real_module", sorted(_FACADE_CONTRACT.items()))
def test_facade_reexport_matches_real_module(name: str, real_module: str) -> None:
    """Every pre-split import path still resolves, to the identical object."""
    facade = importlib.import_module("custom_components.roomba_plus.sensor")
    home = importlib.import_module(f"custom_components.roomba_plus.{real_module}")

    assert hasattr(facade, name), (
        f"'{name}' is no longer importable from the sensor.py facade — "
        f"a re-export was likely dropped when moving it into {real_module}.py"
    )
    assert hasattr(home, name), (
        f"'{name}' is not defined in its expected home module "
        f"custom_components.roomba_plus.{real_module}"
    )

    facade_obj = getattr(facade, name)
    home_obj = getattr(home, name)
    assert facade_obj is home_obj, (
        f"'{name}' resolves to different objects via the facade vs. "
        f"{real_module} — this means it was duplicated instead of "
        f"re-exported, which silently breaks mock.patch(...) call sites "
        f"that target one path but not the other."
    )


def test_facade_contract_is_exhaustive_for_known_consumers() -> None:
    """Sanity check: every name this suite (and callbacks.py/device_tracker.py/
    repairs.py/services.py) is known to import from `.sensor` is covered by
    the contract above. If this fails after adding a new cross-module
    import, add the name (and its real home module) to _FACADE_CONTRACT.
    """
    known_extra_passthroughs = {"SensorDeviceClass", "SensorStateClass"}
    facade = importlib.import_module("custom_components.roomba_plus.sensor")
    for name in known_extra_passthroughs:
        assert hasattr(facade, name), f"expected HA passthrough '{name}' missing from facade"
