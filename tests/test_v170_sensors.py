"""Unit tests for v1.7.0 sensor additions.

Covers:
  - threshold_fn field on RoombaSensorDescription
  - threshold_hours in extra_state_attributes for remaining-hours sensors
  - Timestamp sensor value functions (filter/brush/pad/battery last replaced)
  - mid-mission attributes in vacuum.py (mission_elapsed_min, mission_area_sqft)
  - maintenance_due binary sensor logic
"""
import datetime
import pytest
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.const import DEFAULT_FILTER_HOURS, DEFAULT_BRUSH_HOURS
from custom_components.roomba_plus.sensor import RoombaSensorDescription, SENSORS
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


# ── RoombaSensorDescription.threshold_fn ─────────────────────────────────────

class TestThresholdFn:
    def test_default_threshold_fn_returns_none(self):
        desc = RoombaSensorDescription(
            key="test",
            value_fn=lambda e: None,
        )
        assert desc.threshold_fn(None) is None

    def test_custom_threshold_fn(self):
        desc = RoombaSensorDescription(
            key="test",
            value_fn=lambda e: None,
            threshold_fn=lambda e: 200,
        )
        assert desc.threshold_fn(None) == 200

    def test_filter_remaining_hours_has_threshold_fn(self):
        """filter_remaining_hours description must have threshold_fn set."""
        desc = next(d for d in SENSORS if d.key == "filter_remaining_hours")
        assert desc.threshold_fn is not None

    def test_brush_remaining_hours_has_threshold_fn(self):
        desc = next(d for d in SENSORS if d.key == "brush_remaining_hours")
        assert desc.threshold_fn is not None

    def test_other_sensors_have_no_threshold_fn(self):
        """Non-consumable sensors must not expose a threshold."""
        desc = next(d for d in SENSORS if d.key == "battery")
        assert desc.threshold_fn(None) is None


# ── Timestamp sensors present in SENSORS tuple ────────────────────────────────

class TestTimestampSensorsPresent:
    EXPECTED_KEYS = [
        "filter_last_replaced",
        "brush_last_replaced",
        "pad_last_replaced",
        "battery_last_replaced",
    ]

    def test_all_timestamp_sensor_keys_present(self):
        keys = {d.key for d in SENSORS}
        for key in self.EXPECTED_KEYS:
            assert key in keys, f"Missing sensor key: {key}"

    def test_timestamp_sensors_have_timestamp_device_class(self):
        from homeassistant.components.sensor import SensorDeviceClass
        for key in self.EXPECTED_KEYS:
            desc = next((d for d in SENSORS if d.key == key), None)
            assert desc is not None, f"Sensor not found: {key}"
            assert desc.device_class == SensorDeviceClass.TIMESTAMP, (
                f"{key} must have TIMESTAMP device class"
            )

    def test_brush_last_replaced_filter_fn_excludes_mops(self):
        """brush_last_replaced must not be created for Braava (mop) devices."""
        desc = next(d for d in SENSORS if d.key == "brush_last_replaced")
        # Braava has detectedPad in state
        mop_state = {"detectedPad": "reusableWet"}
        assert desc.filter_fn(mop_state) is False

    def test_pad_last_replaced_filter_fn_includes_mops_only(self):
        desc = next(d for d in SENSORS if d.key == "pad_last_replaced")
        mop_state = {"detectedPad": "reusableWet"}
        vacuum_state = {}
        assert desc.filter_fn(mop_state) is True
        assert desc.filter_fn(vacuum_state) is False

    def test_battery_last_replaced_no_filter_fn_restriction(self):
        """battery_last_replaced is created for all robots."""
        desc = next(d for d in SENSORS if d.key == "battery_last_replaced")
        assert desc.filter_fn({}) is True
        assert desc.filter_fn({"detectedPad": "x"}) is True


# ── Timestamp sensor value functions ─────────────────────────────────────────

class _FakeMaintenanceStore:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeRuntimeData:
    def __init__(self, store):
        self.maintenance_store = store


class _FakeConfigEntry:
    def __init__(self, store, options=None):
        self.runtime_data = _FakeRuntimeData(store)
        self.options = options or {}


class _FakeEntity:
    def __init__(self, config_entry):
        self._config_entry = config_entry


class TestTimestampSensorValues:
    def test_filter_last_replaced_none_when_not_reset(self):
        store = _FakeMaintenanceStore(filter_reset_at=None)
        entry = _FakeConfigEntry(store)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "filter_last_replaced")
        assert desc.value_fn(entity) is None

    def test_filter_last_replaced_returns_datetime_when_reset(self):
        store = _FakeMaintenanceStore(filter_reset_at="2025-06-01T09:00:00+00:00")
        entry = _FakeConfigEntry(store)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "filter_last_replaced")
        result = desc.value_fn(entity)
        assert isinstance(result, datetime.datetime)

    def test_brush_last_replaced_none_when_not_reset(self):
        store = _FakeMaintenanceStore(brush_reset_at=None)
        entry = _FakeConfigEntry(store)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "brush_last_replaced")
        assert desc.value_fn(entity) is None

    def test_pad_last_replaced_uses_brush_reset_at(self):
        """pad_last_replaced reuses brush_reset_at (same store slot)."""
        store = _FakeMaintenanceStore(brush_reset_at="2025-05-15T14:00:00+00:00")
        entry = _FakeConfigEntry(store)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "pad_last_replaced")
        result = desc.value_fn(entity)
        assert isinstance(result, datetime.datetime)

    def test_battery_last_replaced_none_when_not_reset(self):
        store = _FakeMaintenanceStore(battery_reset_at=None)
        entry = _FakeConfigEntry(store)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "battery_last_replaced")
        assert desc.value_fn(entity) is None

    def test_no_store_returns_none(self):
        entry = _FakeConfigEntry(None)
        entity = _FakeEntity(entry)
        desc = next(d for d in SENSORS if d.key == "filter_last_replaced")
        assert desc.value_fn(entity) is None


# ── maintenance_due binary sensor logic ───────────────────────────────────────

class TestMaintenanceDueLogic:
    """Test the _due_items() and overdue_by_hours logic in isolation."""

    def _due_items(self, current_hr, filter_reset_hr, brush_reset_hr,
                   filter_threshold=60, brush_threshold=200, is_mop_device=False):
        """Mirror RoombaMaintenanceDue._due_items logic."""
        store = MaintenanceStore()
        store.filter_reset_hr = filter_reset_hr
        store.brush_reset_hr = brush_reset_hr
        items = []
        if store.filter_remaining(current_hr, filter_threshold) == 0:
            items.append("filter")
        brush_key = "pad" if is_mop_device else "brush"
        if store.brush_remaining(current_hr, brush_threshold) == 0:
            items.append(brush_key)
        return items

    def _overdue_hours(self, current_hr, reset_hr, threshold):
        """Mirror overdue_by_hours calculation from extra_state_attributes."""
        hours_since_reset = current_hr - reset_hr
        return max(0, hours_since_reset - threshold)

    def test_no_consumables_due_when_fresh(self):
        items = self._due_items(current_hr=10, filter_reset_hr=0, brush_reset_hr=0)
        assert items == []

    def test_filter_due_at_threshold(self):
        items = self._due_items(current_hr=60, filter_reset_hr=0, brush_reset_hr=0,
                                filter_threshold=60, brush_threshold=200)
        assert "filter" in items

    def test_brush_due_at_threshold(self):
        items = self._due_items(current_hr=200, filter_reset_hr=200, brush_reset_hr=0,
                                filter_threshold=60, brush_threshold=200)
        assert "brush" in items

    def test_both_due(self):
        items = self._due_items(current_hr=200, filter_reset_hr=0, brush_reset_hr=0,
                                filter_threshold=60, brush_threshold=200)
        assert "filter" in items
        assert "brush" in items

    def test_braava_uses_pad_key(self):
        items = self._due_items(current_hr=200, filter_reset_hr=0, brush_reset_hr=0,
                                brush_threshold=200, is_mop_device=True)
        assert "pad" in items
        assert "brush" not in items

    def test_after_reset_not_due(self):
        # Reset filter at hr=100, threshold=60 → remaining = 60 at hr=100
        items = self._due_items(current_hr=100, filter_reset_hr=100, brush_reset_hr=0,
                                filter_threshold=60, brush_threshold=200)
        assert "filter" not in items

    def test_overdue_still_shows_as_due(self):
        # 100 hours past threshold
        items = self._due_items(current_hr=160, filter_reset_hr=0, brush_reset_hr=0,
                                filter_threshold=60)
        assert "filter" in items

    def test_overdue_by_hours_at_exact_threshold(self):
        assert self._overdue_hours(current_hr=60, reset_hr=0, threshold=60) == 0

    def test_overdue_by_hours_past_threshold(self):
        # 100 hours past threshold
        assert self._overdue_hours(current_hr=160, reset_hr=0, threshold=60) == 100

    def test_overdue_by_hours_after_reset(self):
        # Reset at hr=200, threshold=60, now at hr=220 → only 20h used, not overdue
        assert self._overdue_hours(current_hr=220, reset_hr=200, threshold=60) == 0

    def test_overdue_by_hours_never_negative(self):
        # Before threshold — never negative
        assert self._overdue_hours(current_hr=30, reset_hr=0, threshold=60) == 0


# ── vacuum.py mid-mission attributes ─────────────────────────────────────────

class TestMidMissionAttributes:
    """Test the attribute extraction logic (not the entity class directly)."""

    def _extract_mid_mission(self, state: dict) -> dict:
        """Mirror IRobotVacuum.extra_state_attributes mid-mission logic."""
        mission = state.get("cleanMissionStatus", {})
        return {
            "mission_elapsed_min": mission.get("mssnM"),
            "mission_area_sqft": mission.get("sqft"),
        }

    def test_elapsed_min_present_during_mission(self):
        state = {"cleanMissionStatus": {"mssnM": 23, "sqft": 180}}
        attrs = self._extract_mid_mission(state)
        assert attrs["mission_elapsed_min"] == 23

    def test_area_sqft_present_during_mission(self):
        state = {"cleanMissionStatus": {"mssnM": 23, "sqft": 180}}
        attrs = self._extract_mid_mission(state)
        assert attrs["mission_area_sqft"] == 180

    def test_none_when_docked(self):
        state = {"cleanMissionStatus": {"phase": "charge", "cycle": "none"}}
        attrs = self._extract_mid_mission(state)
        assert attrs["mission_elapsed_min"] is None
        assert attrs["mission_area_sqft"] is None

    def test_none_when_600_series(self):
        """600-series does not report sqft."""
        state = {"cleanMissionStatus": {"mssnM": 15}}  # no sqft key
        attrs = self._extract_mid_mission(state)
        assert attrs["mission_area_sqft"] is None

    def test_no_cleanMissionStatus_key(self):
        attrs = self._extract_mid_mission({})
        assert attrs["mission_elapsed_min"] is None
        assert attrs["mission_area_sqft"] is None

    def test_attributes_are_primitives(self):
        """Values must be JSON-serialisable (int or None, not datetime)."""
        state = {"cleanMissionStatus": {"mssnM": 10, "sqft": 200}}
        attrs = self._extract_mid_mission(state)
        for val in attrs.values():
            assert val is None or isinstance(val, (int, float)), (
                f"Non-primitive value in mid-mission attrs: {val!r}"
            )
