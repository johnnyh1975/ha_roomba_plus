"""Unit tests for MaintenanceStore.

Tests the pure-Python logic (no hass required):
  - Default values
  - reset_filter / reset_brush / reset_battery
  - filter_remaining / brush_remaining calculations
  - Clamp at zero when overdue
  - serialize / deserialize round-trip (the dict that goes to hass.storage)
"""
import pytest
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


class TestMaintenanceStoreDefaults:
    def test_default_values(self):
        store = MaintenanceStore()
        assert store.filter_reset_hr == 0
        assert store.brush_reset_hr == 0
        assert store.battery_reset_hr == 0

    def test_filter_remaining_no_reset(self):
        """Without any reset, remaining = threshold - lifetime_hours."""
        store = MaintenanceStore()
        # Robot has run 100 h, threshold 200 h → 100 h remaining
        assert store.filter_remaining(100, 200) == 100

    def test_brush_remaining_no_reset(self):
        store = MaintenanceStore()
        assert store.brush_remaining(50, 150) == 100

    def test_remaining_clamped_at_zero(self):
        """Remaining must never go negative."""
        store = MaintenanceStore()
        # Robot has run more hours than the threshold
        assert store.filter_remaining(300, 200) == 0
        assert store.brush_remaining(300, 150) == 0


class TestMaintenanceStoreReset:
    def test_reset_filter_restarts_counter(self):
        store = MaintenanceStore()
        store.reset_filter(current_hr=400)
        # 50 hours after reset, threshold 200 → 150 remaining
        assert store.filter_remaining(450, 200) == 150

    def test_reset_brush_restarts_counter(self):
        store = MaintenanceStore()
        store.reset_brush(current_hr=300)
        assert store.brush_remaining(300, 150) == 150

    def test_reset_battery_stores_hr(self):
        store = MaintenanceStore()
        store.reset_battery(current_hr=500)
        assert store.battery_reset_hr == 500

    def test_multiple_resets(self):
        """Second reset replaces the first."""
        store = MaintenanceStore()
        store.reset_filter(100)
        store.reset_filter(300)
        # From hr=300, threshold=200 → 200 remaining at hr=300
        assert store.filter_remaining(300, 200) == 200

    def test_filter_remaining_after_reset_exact(self):
        """At threshold hours after reset, remaining = 0."""
        store = MaintenanceStore()
        store.reset_filter(200)
        assert store.filter_remaining(400, 200) == 0

    def test_filter_remaining_overdue_after_reset(self):
        """Past threshold, still clamped at 0."""
        store = MaintenanceStore()
        store.reset_filter(200)
        assert store.filter_remaining(500, 200) == 0


class TestMaintenanceStoreSerialisation:
    def test_data_dict_round_trip(self):
        """Simulate what async_save / async_load does with the dict."""
        store = MaintenanceStore()
        store.reset_filter(100)
        store.reset_brush(200)
        store.reset_battery(300)

        # Simulate save
        data = {
            "filter_reset_hr":  store.filter_reset_hr,
            "brush_reset_hr":   store.brush_reset_hr,
            "battery_reset_hr": store.battery_reset_hr,
        }

        # Simulate load into a fresh store
        store2 = MaintenanceStore()
        store2.filter_reset_hr  = int(data.get("filter_reset_hr",  0))
        store2.brush_reset_hr   = int(data.get("brush_reset_hr",   0))
        store2.battery_reset_hr = int(data.get("battery_reset_hr", 0))

        assert store2.filter_reset_hr  == 100
        assert store2.brush_reset_hr   == 200
        assert store2.battery_reset_hr == 300
        assert store2.filter_remaining(250, 200) == 50
        assert store2.brush_remaining(350, 200) == 50

    def test_load_missing_keys_defaults_to_zero(self):
        """Partial data (missing keys) must not crash."""
        store = MaintenanceStore()
        data = {"filter_reset_hr": 50}  # brush and battery missing
        store.filter_reset_hr  = int(data.get("filter_reset_hr",  0))
        store.brush_reset_hr   = int(data.get("brush_reset_hr",   0))
        store.battery_reset_hr = int(data.get("battery_reset_hr", 0))
        assert store.filter_reset_hr  == 50
        assert store.brush_reset_hr   == 0
        assert store.battery_reset_hr == 0

    def test_load_invalid_value_type(self):
        """String values must be safely cast to int."""
        data = {"filter_reset_hr": "150", "brush_reset_hr": "75", "battery_reset_hr": "0"}
        store = MaintenanceStore()
        store.filter_reset_hr  = int(data.get("filter_reset_hr",  0))
        store.brush_reset_hr   = int(data.get("brush_reset_hr",   0))
        store.battery_reset_hr = int(data.get("battery_reset_hr", 0))
        assert store.filter_reset_hr == 150
        assert store.brush_reset_hr  == 75
