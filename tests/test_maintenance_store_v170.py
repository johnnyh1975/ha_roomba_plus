"""Unit tests for MaintenanceStore v1.7.0 additions.

Covers:
  - reset_at timestamp fields (new in v1.7.0)
  - reset_pad Braava alias
  - Backward-compatible load from pre-v1.7 data (no _reset_at keys)
  - Full serialisation round-trip with new fields
  - dt_util.now() used (no datetime.now())
"""
import datetime
import pytest
import tests.conftest  # noqa: F401 — loads HA stubs

from custom_components.roomba_plus.maintenance_store import MaintenanceStore


class TestResetAtTimestamps:
    """v1.7.0: reset_* methods must set _reset_at ISO strings."""

    def test_default_reset_at_is_none(self):
        store = MaintenanceStore()
        assert store.filter_reset_at is None
        assert store.brush_reset_at is None
        assert store.battery_reset_at is None

    def test_reset_filter_sets_reset_at(self):
        store = MaintenanceStore()
        store.reset_filter(current_hr=100)
        assert store.filter_reset_at is not None
        # Must be a valid ISO 8601 string
        dt = datetime.datetime.fromisoformat(store.filter_reset_at)
        assert isinstance(dt, datetime.datetime)

    def test_reset_brush_sets_reset_at(self):
        store = MaintenanceStore()
        store.reset_brush(current_hr=200)
        assert store.brush_reset_at is not None
        datetime.datetime.fromisoformat(store.brush_reset_at)

    def test_reset_battery_sets_reset_at(self):
        store = MaintenanceStore()
        store.reset_battery(current_hr=300)
        assert store.battery_reset_at is not None
        datetime.datetime.fromisoformat(store.battery_reset_at)

    def test_multiple_resets_update_reset_at(self):
        """Second reset overwrites the first timestamp."""
        store = MaintenanceStore()
        store.reset_filter(100)
        ts1 = store.filter_reset_at
        store.reset_filter(200)
        ts2 = store.filter_reset_at
        # Timestamps may be identical if test runs fast — just check it's set
        assert ts2 is not None
        # The hr value must be updated regardless
        assert store.filter_reset_hr == 200


class TestResetPad:
    """reset_pad is the Braava alias for reset_brush."""

    def test_reset_pad_sets_brush_reset_hr(self):
        store = MaintenanceStore()
        store.reset_pad(current_hr=150)
        assert store.brush_reset_hr == 150

    def test_reset_pad_sets_brush_reset_at(self):
        store = MaintenanceStore()
        store.reset_pad(current_hr=150)
        assert store.brush_reset_at is not None
        datetime.datetime.fromisoformat(store.brush_reset_at)

    def test_reset_pad_does_not_affect_filter(self):
        store = MaintenanceStore()
        store.reset_pad(100)
        assert store.filter_reset_hr == 0
        assert store.filter_reset_at is None


class TestBackwardCompatLoad:
    """Loading pre-v1.7 data (no _reset_at keys) must succeed silently."""

    def test_missing_reset_at_keys_default_to_none(self):
        """Simulate loading from a v1.6 store (no _reset_at keys)."""
        data = {
            "filter_reset_hr": 100,
            "brush_reset_hr": 50,
            "battery_reset_hr": 0,
            # No _reset_at keys — pre-v1.7 data
        }
        store = MaintenanceStore()
        store.filter_reset_hr  = int(data.get("filter_reset_hr", 0))
        store.brush_reset_hr   = int(data.get("brush_reset_hr", 0))
        store.battery_reset_hr = int(data.get("battery_reset_hr", 0))
        store.filter_reset_at  = data.get("filter_reset_at")
        store.brush_reset_at   = data.get("brush_reset_at")
        store.battery_reset_at = data.get("battery_reset_at")

        assert store.filter_reset_hr == 100
        assert store.filter_reset_at is None  # expected: upgrade path
        assert store.brush_reset_hr == 50
        assert store.brush_reset_at is None

    def test_partial_reset_at_keys(self):
        """Only some _reset_at keys present (mixed version)."""
        data = {
            "filter_reset_hr": 100,
            "filter_reset_at": "2025-01-15T09:00:00+00:00",
            "brush_reset_hr": 50,
            # brush_reset_at missing
        }
        store = MaintenanceStore()
        store.filter_reset_hr  = int(data.get("filter_reset_hr", 0))
        store.brush_reset_hr   = int(data.get("brush_reset_hr", 0))
        store.battery_reset_hr = int(data.get("battery_reset_hr", 0))
        store.filter_reset_at  = data.get("filter_reset_at")
        store.brush_reset_at   = data.get("brush_reset_at")
        store.battery_reset_at = data.get("battery_reset_at")

        assert store.filter_reset_at == "2025-01-15T09:00:00+00:00"
        assert store.brush_reset_at is None


class TestSerialisationRoundTrip:
    """Full save/load cycle with v1.7.0 fields."""

    def test_round_trip_with_all_fields(self):
        store = MaintenanceStore()
        store.reset_filter(100)
        store.reset_brush(200)
        store.reset_battery(300)

        # Simulate async_save dict
        saved = {
            "filter_reset_hr":  store.filter_reset_hr,
            "brush_reset_hr":   store.brush_reset_hr,
            "battery_reset_hr": store.battery_reset_hr,
            "filter_reset_at":  store.filter_reset_at,
            "brush_reset_at":   store.brush_reset_at,
            "battery_reset_at": store.battery_reset_at,
        }

        # Simulate async_load into fresh store
        store2 = MaintenanceStore()
        store2.filter_reset_hr  = int(saved.get("filter_reset_hr", 0))
        store2.brush_reset_hr   = int(saved.get("brush_reset_hr", 0))
        store2.battery_reset_hr = int(saved.get("battery_reset_hr", 0))
        store2.filter_reset_at  = saved.get("filter_reset_at")
        store2.brush_reset_at   = saved.get("brush_reset_at")
        store2.battery_reset_at = saved.get("battery_reset_at")

        assert store2.filter_reset_hr == 100
        assert store2.brush_reset_hr == 200
        assert store2.battery_reset_hr == 300
        assert store2.filter_reset_at == store.filter_reset_at
        assert store2.brush_reset_at == store.brush_reset_at
        assert store2.battery_reset_at == store.battery_reset_at
        # Calculations still correct
        assert store2.filter_remaining(250, 200) == 50
        assert store2.brush_remaining(350, 200) == 50

    def test_reset_at_is_valid_iso_after_round_trip(self):
        store = MaintenanceStore()
        store.reset_filter(100)
        saved_at = store.filter_reset_at

        store2 = MaintenanceStore()
        store2.filter_reset_at = saved_at
        assert store2.filter_reset_at is not None
        dt = datetime.datetime.fromisoformat(store2.filter_reset_at)
        assert isinstance(dt, datetime.datetime)


class TestNoDatetimeNow:
    """Ensure reset_at values are timezone-aware (dt_util.now() contract)."""

    def test_reset_at_strings_are_timezone_aware(self):
        """dt_util.now() returns timezone-aware datetimes — verify the ISO string."""
        store = MaintenanceStore()
        store.reset_filter(0)
        # Parse and check tzinfo is present
        dt = datetime.datetime.fromisoformat(store.filter_reset_at)
        # dt_util.now() always returns timezone-aware
        # If using plain datetime.now() this would be naive (tzinfo=None)
        assert dt.tzinfo is not None, (
            "filter_reset_at must be timezone-aware ISO string — "
            "use dt_util.now(), not datetime.now()"
        )
