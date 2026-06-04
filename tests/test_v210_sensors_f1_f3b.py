"""Tests for v2.1.0 new sensors: F1 (WiFi), F2 (mop clean mode),
F3 (mop tank status), F3b (mop ARS behavior), F7h (state_class fixes).

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import sys, importlib, types

# Stub out the HA import that varies by version before importing sensor.py
import unittest.mock as _mock
_ep = sys.modules.get('homeassistant.helpers.entity_platform')
if _ep and not hasattr(_ep, 'AddConfigEntryEntitiesCallback'):
    _ep.AddConfigEntryEntitiesCallback = _ep.AddEntitiesCallback

from custom_components.roomba_plus.sensor import (
    _raw_wifi_floor,
    _raw_wifi_stability,
    _mop_clean_mode,
    _mop_tank_status,
    _mop_behavior,
    SENSORS,
    CLOUD_RAW_SENSORS,
    SensorStateClass,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entity(state: dict) -> MagicMock:
    """Return a fake IRobotEntity with the given vacuum_state."""
    e = MagicMock()
    e.vacuum_state = state
    return e


# ── F1 — WiFi floor / stability ───────────────────────────────────────────────

class TestWifiFloor:
    """Amendment 8d — wlBars is a 5-element histogram, not a time-series."""

    def test_returns_lowest_nonempty_bucket(self):
        # [0, 35, 65, 0, 0]: bucket 1 is lowest non-zero → floor = 1
        records = [{"wlBars": [0, 35, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 1

    def test_bucket_zero_populated(self):
        # [5, 30, 65, 0, 0]: bucket 0 has readings → floor = 0
        records = [{"wlBars": [5, 30, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 0

    def test_all_strong_signal(self):
        # [0, 0, 0, 40, 60]: only buckets 3/4 → floor = 3
        records = [{"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_floor([]) is None

    def test_returns_none_when_wlbars_none(self):
        assert _raw_wifi_floor([{"wlBars": None}]) is None

    def test_returns_none_when_all_zero_histogram(self):
        assert _raw_wifi_floor([{"wlBars": [0, 0, 0, 0, 0]}]) is None

    def test_skips_records_without_wlbars(self):
        records = [{"sqft": 100}, {"wlBars": [0, 0, 70, 30, 0]}]
        assert _raw_wifi_floor(records) == 2

    def test_must_be_exactly_5_elements(self):
        # Wrong length histogram — skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3


class TestWifiStability:
    """Amendment 8d — weighted stdev of signal bucket distribution."""

    def test_concentrated_is_low_stdev(self):
        # All readings in bucket 3 → stdev ≈ 0
        records = [{"wlBars": [0, 0, 0, 100, 0]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val < 0.1

    def test_spread_is_high_stdev(self):
        # Evenly spread across all 5 buckets → high stdev
        records = [{"wlBars": [20, 20, 20, 20, 20]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val > 0.5

    def test_returns_none_when_fewer_than_3_records(self):
        records = [{"wlBars": [0, 35, 65, 0, 0]}] * 2
        assert _raw_wifi_stability(records) is None

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_stability([]) is None

    def test_skips_non_5element_histograms(self):
        # 3-element arrays are invalid — should be skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}] * 3
        val = _raw_wifi_stability(records)
        # Only the valid 5-element records contribute
        assert val is not None

    def test_result_is_float(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert isinstance(result, float)

    def test_result_rounded_to_2_decimals(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert result == round(result, 2)


class TestWifiSensorDescriptions:
    def test_recent_wifi_floor_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_wifi_floor" in keys

    def test_recent_wifi_stability_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_wifi_stability" in keys

    def test_wifi_sensors_disabled_by_default(self):
        """WiFi sensors are opt-in (disabled by default)."""
        for desc in CLOUD_RAW_SENSORS:
            if desc.key in ("recent_wifi_floor", "recent_wifi_stability"):
                assert desc.entity_registry_enabled_default is False


# ── F2 — Mop clean mode ───────────────────────────────────────────────────────

class TestMopCleanMode:
    def test_level_1_is_dry(self):
        e = _entity({"padWetness": {"disposable": 1}})
        assert _mop_clean_mode(e) == "Dry"

    def test_level_2_is_wet(self):
        e = _entity({"padWetness": {"disposable": 2}})
        assert _mop_clean_mode(e) == "Wet"

    def test_level_3_is_wet(self):
        e = _entity({"padWetness": {"reusable": 3}})
        assert _mop_clean_mode(e) == "Wet"

    def test_missing_padwetness_is_unknown(self):
        e = _entity({})
        assert _mop_clean_mode(e) == "Unknown"

    def test_empty_dict_is_unknown(self):
        e = _entity({"padWetness": {}})
        assert _mop_clean_mode(e) == "Unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_clean_mode" in keys

    def test_filter_fn_requires_padwetness(self):
        desc = next(d for d in SENSORS if d.key == "mop_clean_mode")
        assert desc.filter_fn({"padWetness": {}}) is True
        assert desc.filter_fn({}) is False


# ── F3 — Mop tank status ──────────────────────────────────────────────────────

class TestMopTankStatus:
    def test_all_ok_is_ready(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": False}})
        assert _mop_tank_status(e) == "Ready"

    def test_fill_required(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": True}})
        assert _mop_tank_status(e) == "Fill Tank"

    def test_lid_open_takes_priority_over_fill(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "Lid Open"

    def test_tank_missing_highest_priority(self):
        e = _entity({"mopReady": {"tankPresent": False, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "Tank Missing"

    def test_missing_mopready_is_unknown(self):
        e = _entity({})
        assert _mop_tank_status(e) == "Unknown"

    def test_non_dict_mopready_is_unknown(self):
        e = _entity({"mopReady": 1})
        assert _mop_tank_status(e) == "Unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_tank_status" in keys

    def test_filter_fn_requires_mopready(self):
        desc = next(d for d in SENSORS if d.key == "mop_tank_status")
        assert desc.filter_fn({"mopReady": {}}) is True
        assert desc.filter_fn({}) is False


# ── F3b — Mop ARS behavior ────────────────────────────────────────────────────

class TestMopBehavior:
    def test_rank_15_no_mop(self):
        e = _entity({"rankOverlap": 15})
        assert _mop_behavior(e) == "No Mop"

    def test_rank_67_standard(self):
        e = _entity({"rankOverlap": 67})
        assert _mop_behavior(e) == "Standard"

    def test_rank_85_deep(self):
        e = _entity({"rankOverlap": 85})
        assert _mop_behavior(e) == "Deep"

    def test_unknown_rank(self):
        e = _entity({"rankOverlap": 99})
        assert _mop_behavior(e) == "Unknown"

    def test_flag_combination_dry_only(self):
        e = _entity({"padDryAllowed": 1, "padWashAllowed": 0, "padDirtyPause": 0})
        assert _mop_behavior(e) == "Dry"

    def test_flag_combination_dirty_pause_plus_dry_plus_wash(self):
        e = _entity({"padDirtyPause": 1, "padDryAllowed": 1, "padWashAllowed": 1})
        assert _mop_behavior(e) == "Dirty Pause + Dry + Wash"

    def test_no_flags_is_unknown(self):
        e = _entity({"padDryAllowed": 0, "padWashAllowed": 0})
        assert _mop_behavior(e) == "Unknown"

    def test_rankOverlap_takes_precedence_over_flags(self):
        e = _entity({"rankOverlap": 25, "padDryAllowed": 1})
        assert _mop_behavior(e) == "Extended"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_ars_behavior" in keys

    def test_filter_fn_rankOverlap(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"rankOverlap": 67}) is True

    def test_filter_fn_padDryAllowed(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"padDryAllowed": 1}) is True

    def test_filter_fn_absent_for_vacuums(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"batPct": 85}) is False


# ── F7h — state_class fixes ───────────────────────────────────────────────────

class TestStateClassFixes:
    def test_battery_cycles_is_total_increasing(self):
        desc = next(d for d in SENSORS if d.key == "battery_cycles")
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING

    def test_scrubs_count_is_total_increasing(self):
        desc = next(d for d in SENSORS if d.key == "scrubs_count")
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING
