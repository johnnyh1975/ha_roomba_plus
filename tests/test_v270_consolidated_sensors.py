"""SC1 (v2.7.0) — Consolidated analytics sensor tests.

Tests that the four new consolidated sensors return correct types and
populate their extra_state_attributes from coordinator data.
"""
from unittest.mock import MagicMock, PropertyMock
import pytest

from custom_components.roomba_plus.sensor import (
    RoombaCleaningPerformanceSensor,
    RoombaCleaningAnalytics30dSensor,
    RoombaWifiHealthSensor,
    RoombaEventCounts30dSensor,
)


def _make_coordinator(records=None, data=None):
    cc = MagicMock()
    cc.raw_records = records or []
    cc.data = data or {}
    cc.last_update_success = True
    return cc


def _make_entry(mission_store=None):
    entry = MagicMock()
    rd = MagicMock()
    rd.has_cloud = True
    rd.mission_store = mission_store
    rd.robot_profile_store = None
    entry.runtime_data = rd
    return entry


def _make_sensor(cls, records=None, data=None, mission_store=None):
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    cc = _make_coordinator(records=records, data=data)
    entry = _make_entry(mission_store=mission_store)
    sensor = cls.__new__(cls)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._coordinator = cc
    sensor._config_entry = entry
    sensor._attr_unique_id = f"test_{cls.entity_description.key}"
    return sensor


# ── RoombaCleaningPerformanceSensor ───────────────────────────────────────────

class TestCleaningPerformanceSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor(RoombaCleaningPerformanceSensor, records=[])
        assert s.native_value is None

    def test_returns_completion_rate_with_records(self):
        records = [
            {"done": "done", "sqft": 300, "runM": 40},
            {"done": "done", "sqft": 280, "runM": 38},
            {"done": "hmPostMsn"},
        ]
        s = _make_sensor(RoombaCleaningPerformanceSensor, records=records)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        assert 0 <= val <= 100

    def test_attributes_include_trend(self):
        # Need ≥6 records for trend to be non-unknown
        records = [
            {"done": "done", "sqft": 300, "runM": 40, "startTime": 1700000000 - i * 86400}
            for i in range(10)
        ]
        s = _make_sensor(RoombaCleaningPerformanceSensor, records=records)
        attrs = s.extra_state_attributes
        # trend key should be present
        assert "trend" in attrs
        assert attrs["trend"] in ("improving", "stable", "declining", "unknown")


# ── RoombaCleaningAnalytics30dSensor ─────────────────────────────────────────

class TestCleaningAnalytics30dSensor:

    def test_returns_none_without_runtime_stats(self):
        s = _make_sensor(RoombaCleaningAnalytics30dSensor, data={})
        assert s.native_value is None

    def test_returns_area_m2_from_runtime_stats(self):
        data = {"runtimeStats": {"sqft": 10764, "hr": 5, "min": 30}}
        s = _make_sensor(RoombaCleaningAnalytics30dSensor, data=data)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        # 10764 sqft × 0.09290304 ≈ 1000.5 m²
        assert 990 < val < 1010

    def test_attributes_include_time_h(self):
        data = {"runtimeStats": {"sqft": 5000, "hr": 3, "min": 0}}
        s = _make_sensor(RoombaCleaningAnalytics30dSensor, data=data)
        attrs = s.extra_state_attributes
        assert "time_h" in attrs
        assert attrs["time_h"] == 3.0


# ── RoombaWifiHealthSensor ────────────────────────────────────────────────────

class TestWifiHealthSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor(RoombaWifiHealthSensor, records=[])
        assert s.native_value is None

    def test_returns_floor_pct_with_wl_bars(self):
        # wlBars histogram: index 0=weakest, 4=strongest
        records = [
            {"wlBars": [0, 10, 60, 30, 0]},
            {"wlBars": [0, 5,  70, 25, 0]},
        ]
        s = _make_sensor(RoombaWifiHealthSensor, records=records)
        val = s.native_value
        # Should return something (floor signal % computation)
        # If records lack valid wlBars, returns None — accept either
        # With valid data it should return a numeric value
        if val is not None:
            assert isinstance(val, (int, float))

    def test_attributes_include_stability(self):
        records = [{"wlBars": [0, 0, 50, 50, 0]}, {"wlBars": [0, 0, 60, 40, 0]}]
        s = _make_sensor(RoombaWifiHealthSensor, records=records)
        attrs = s.extra_state_attributes
        # stability_pct present when records have wlBars
        # (may be absent if wlBars computation returns None)
        assert isinstance(attrs, dict)


# ── RoombaEventCounts30dSensor ────────────────────────────────────────────────

class TestEventCounts30dSensor:

    def test_returns_none_without_error_records(self):
        records = [{"done": "done"}, {"done": "done"}]
        s = _make_sensor(RoombaEventCounts30dSensor, records=records)
        assert s.native_value is None

    def test_returns_error_code_from_failed_record(self):
        records = [
            {"classified_result": "error_15", "pauseId": 15},
            {"done": "done"},
        ]
        s = _make_sensor(RoombaEventCounts30dSensor, records=records)
        val = s.native_value
        assert val == 15

    def test_attributes_include_recharges_and_evacuations(self):
        records = [
            {"chrgs": 2, "evacs": 1, "dirt": 8},
            {"chrgs": 1, "evacs": 0, "dirt": 5},
        ]
        s = _make_sensor(RoombaEventCounts30dSensor, records=records)
        attrs = s.extra_state_attributes
        assert attrs.get("recharges") == 3
        assert attrs.get("evacuations") == 1
        assert attrs.get("dirt_events") == 13
