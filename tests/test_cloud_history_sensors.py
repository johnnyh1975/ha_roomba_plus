"""Unit tests for cloud history sensors (lifetime_area, lifetime_time, lifetime_missions).

Tests cover:
  - Value extraction from mission_history API response
  - sqft → m² conversion
  - hr + min → total minutes conversion
  - None returned when data missing or coordinator unavailable
  - Three sensors defined in CLOUD_HISTORY_SENSORS
  - Sensors created in async_setup_entry when has_cloud is True
  - Sensors not created when has_cloud is False
  - CloudHistorySensor does not update from MQTT (new_state_filter)
  - Sensor unavailable when coordinator.last_update_success is False

No HA or roombapy installation required — uses stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from custom_components.roomba_plus.sensor import (
    CLOUD_HISTORY_SENSORS,
    CloudHistorySensor,
    CloudHistorySensorDescription,
    _mh_sqft_to_m2,
    _mh_total_minutes,
    _mh_total_missions,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_history(sqft=None, hr=None, mn=None, n_mssn=None) -> dict:
    h = {}
    if sqft is not None or hr is not None or mn is not None:
        h["runtimeStats"] = {}
        if sqft is not None: h["runtimeStats"]["sqft"] = sqft
        if hr is not None:   h["runtimeStats"]["hr"] = hr
        if mn is not None:   h["runtimeStats"]["min"] = mn
    if n_mssn is not None:
        h["bbmssn"] = {"nMssn": n_mssn}
    return h


def _make_coordinator(history=None, success=True):
    cc = MagicMock()
    cc.last_update_success = success
    cc.data = {
        "pmaps": [],
        "favorites": [],
        "mission_history": history or {},
    } if success else None
    cc.async_add_listener = MagicMock(return_value=lambda: None)
    return cc


def _make_sensor(key: str, history=None, success=True) -> CloudHistorySensor:
    desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == key)
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    cc = _make_coordinator(history, success)
    return CloudHistorySensor(roomba, "test_blid", cc, desc)


# ── Value functions — unit tests ───────────────────────────────────────────────

class TestMhSqftToM2:
    def test_converts_sqft_to_m2(self):
        h = _make_history(sqft=10764)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(1000.0, abs=1)

    def test_rounds_to_one_decimal(self):
        h = _make_history(sqft=100)
        result = _mh_sqft_to_m2(h)
        assert result == round(100 / 10.764, 1)

    def test_none_when_sqft_missing(self):
        assert _mh_sqft_to_m2({}) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_sqft_to_m2({"bbmssn": {"nMssn": 5}}) is None

    def test_none_when_runtimestats_is_none(self):
        assert _mh_sqft_to_m2({"runtimeStats": None}) is None

    def test_zero_sqft(self):
        h = _make_history(sqft=0)
        assert _mh_sqft_to_m2(h) == 0.0

    def test_large_value(self):
        h = _make_history(sqft=50000)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(4645.2, abs=1)


class TestMhTotalMinutes:
    def test_converts_hr_min_to_minutes(self):
        h = _make_history(hr=2, mn=30)
        assert _mh_total_minutes(h) == 150

    def test_zero_hours(self):
        h = _make_history(hr=0, mn=45)
        assert _mh_total_minutes(h) == 45

    def test_zero_minutes(self):
        h = _make_history(hr=3, mn=0)
        assert _mh_total_minutes(h) == 180

    def test_none_when_hr_missing(self):
        h = {"runtimeStats": {"min": 30}}
        assert _mh_total_minutes(h) is None

    def test_none_when_min_missing(self):
        h = {"runtimeStats": {"hr": 2}}
        assert _mh_total_minutes(h) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_total_minutes({}) is None

    def test_none_when_runtimestats_none(self):
        assert _mh_total_minutes({"runtimeStats": None}) is None

    def test_large_values(self):
        h = _make_history(hr=100, mn=59)
        assert _mh_total_minutes(h) == 6059


class TestMhTotalMissions:
    def test_returns_nmssn(self):
        h = _make_history(n_mssn=987)
        assert _mh_total_missions(h) == 987

    def test_none_when_bbmssn_missing(self):
        assert _mh_total_missions({}) is None

    def test_none_when_nmssn_missing(self):
        assert _mh_total_missions({"bbmssn": {}}) is None

    def test_none_when_bbmssn_none(self):
        assert _mh_total_missions({"bbmssn": None}) is None

    def test_zero_missions(self):
        h = _make_history(n_mssn=0)
        assert _mh_total_missions(h) == 0


# ── CLOUD_HISTORY_SENSORS descriptions ────────────────────────────────────────

class TestCloudHistorySensorsDescriptions:
    def test_three_sensors_defined(self):
        assert len(CLOUD_HISTORY_SENSORS) == 3

    def test_keys(self):
        keys = {d.key for d in CLOUD_HISTORY_SENSORS}
        assert keys == {"lifetime_area", "lifetime_time", "lifetime_missions"}

    def test_all_have_translation_keys(self):
        for d in CLOUD_HISTORY_SENSORS:
            assert d.translation_key is not None

    def test_area_unit_m2(self):
        area = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_area")
        assert area.native_unit_of_measurement == "m²"

    def test_time_unit_minutes(self):
        from homeassistant.const import UnitOfTime
        time = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_time")
        assert time.native_unit_of_measurement == UnitOfTime.MINUTES

    def test_missions_unit(self):
        missions = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_missions")
        assert missions.native_unit_of_measurement == "missions"

    def test_all_diagnostic(self):
        from homeassistant.const import EntityCategory
        for d in CLOUD_HISTORY_SENSORS:
            assert d.entity_category == EntityCategory.DIAGNOSTIC


# ── CloudHistorySensor entity ─────────────────────────────────────────────────

class TestCloudHistorySensorNativeValue:
    def test_lifetime_area_value(self):
        sensor = _make_sensor("lifetime_area", _make_history(sqft=10764))
        assert sensor.native_value == pytest.approx(1000.0, abs=1)

    def test_lifetime_time_value(self):
        sensor = _make_sensor("lifetime_time", _make_history(hr=1, mn=30))
        assert sensor.native_value == 90

    def test_lifetime_missions_value(self):
        sensor = _make_sensor("lifetime_missions", _make_history(n_mssn=42))
        assert sensor.native_value == 42

    def test_none_when_no_history_data(self):
        sensor = _make_sensor("lifetime_area", {})
        assert sensor.native_value is None

    def test_none_when_coordinator_has_no_data(self):
        sensor = _make_sensor("lifetime_area", success=False)
        assert sensor.native_value is None


class TestCloudHistorySensorAvailability:
    def test_available_when_coordinator_ok(self):
        sensor = _make_sensor("lifetime_area", _make_history(sqft=100))
        assert sensor.available is True

    def test_unavailable_when_last_update_failed(self):
        sensor = _make_sensor("lifetime_area", success=False)
        assert sensor.available is False

    def test_unavailable_when_data_none(self):
        sensor = _make_sensor("lifetime_area")
        sensor._coordinator.data = None
        assert sensor.available is False


class TestCloudHistorySensorNoMqttUpdate:
    def test_new_state_filter_always_false(self):
        """Cloud sensor must not react to MQTT messages."""
        sensor = _make_sensor("lifetime_missions", _make_history(n_mssn=10))
        assert sensor.new_state_filter({"bbmssn": {"nMssn": 99}}) is False
        assert sensor.new_state_filter({}) is False


class TestCloudHistorySensorUniqueId:
    def test_unique_id_contains_key(self):
        sensor = _make_sensor("lifetime_area", _make_history(sqft=100))
        assert "lifetime_area" in sensor._attr_unique_id

    def test_unique_id_contains_blid(self):
        sensor = _make_sensor("lifetime_missions", _make_history(n_mssn=5))
        assert "test_blid" in sensor._attr_unique_id

    def test_unique_ids_distinct(self):
        s1 = _make_sensor("lifetime_area", _make_history(sqft=100))
        s2 = _make_sensor("lifetime_time", _make_history(hr=1, mn=0))
        assert s1._attr_unique_id != s2._attr_unique_id


# ── async_setup_entry integration ─────────────────────────────────────────────

class TestSensorSetupEntryCloud:
    """Verify async_setup_entry creates cloud sensors when has_cloud is True."""

    def _make_entry(self, has_cloud: bool):
        entry = MagicMock()
        entry.options = {}
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        cc = _make_coordinator(_make_history(sqft=500, hr=10, mn=0, n_mssn=50))
        runtime = MagicMock()
        runtime.roomba = roomba
        runtime.blid = "test_blid"
        runtime.has_cloud = has_cloud
        runtime.cloud_coordinator = cc if has_cloud else None
        entry.runtime_data = runtime
        return entry

    @pytest.mark.asyncio
    async def test_cloud_sensors_created_when_has_cloud(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=True)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        assert len(cloud_sensors) == 3

    @pytest.mark.asyncio
    async def test_cloud_sensors_not_created_without_credentials(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=False)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        assert len(cloud_sensors) == 0

    @pytest.mark.asyncio
    async def test_all_three_sensor_keys_created(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=True)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        keys = {e.entity_description.key for e in cloud_sensors}
        assert keys == {"lifetime_area", "lifetime_time", "lifetime_missions"}
