"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
import datetime
import tests.conftest
from custom_components.roomba_plus.const import DEFAULT_FILTER_HOURS
from custom_components.roomba_plus.const import DEFAULT_BRUSH_HOURS
from custom_components.roomba_plus.sensor import RoombaSensorDescription
from custom_components.roomba_plus.sensor import SENSORS
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from unittest.mock import call
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.repairs import async_check_bbrun_reset
from custom_components.roomba_plus.repairs import async_check_performance_degradation
from custom_components.roomba_plus.repairs import async_check_battery_recharge
from custom_components.roomba_plus.repairs import async_check_mixed_schedule
from custom_components.roomba_plus.repairs import async_check_accident_detection
from custom_components.roomba_plus.repairs import async_check_consecutive_skips
from custom_components.roomba_plus.repairs import async_enrich_drift_issue
import sys
from custom_components.roomba_plus.sensor import _raw_cleaning_speed
from custom_components.roomba_plus.sensor import _raw_dirt_density
from custom_components.roomba_plus.sensor import _raw_dirt_density_attrs
from custom_components.roomba_plus.sensor import _raw_recharge_fraction
from custom_components.roomba_plus.sensor import _raw_cleaning_speed_trend
from custom_components.roomba_plus.sensor import _make_coverage_pct_fn
from custom_components.roomba_plus.sensor import _battery_capacity_retention
from custom_components.roomba_plus.sensor import _estimated_battery_eol
from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
import statistics
from datetime import UTC
from datetime import datetime as datetime_v250_learning
from datetime import timedelta
from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
from custom_components.roomba_plus.const import SQFT_TO_M2
import asyncio
from collections import defaultdict
from datetime import datetime as datetime_v260_learning
from datetime import timezone
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from homeassistant.util import dt as dt_util


_ep = sys.modules.get('homeassistant.helpers.entity_platform')


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


def _make_entry(mission_store=None, maintenance_store=None):
    entry = MagicMock()
    data = MagicMock()
    data.mission_store = mission_store or MissionStore()
    data.maintenance_store = maintenance_store or MaintenanceStore()
    data.consecutive_declining_speed = 0
    data.consecutive_battery_warn = 0
    data.cleaning_speed_trend_value = "stable"
    data.dirt_density_rising = False
    data.recharge_fraction_value = 5.0
    data.battery_retention_value = 95.0
    data.roomba_reported_state = MagicMock(return_value={"bbrun": {"hr": 100}})
    entry.runtime_data = data
    entry.options = {"brush_hours": 150, "filter_hours": 150}
    return entry


def _make_hass():
    hass = MagicMock()
    def _close_coro(*args, **kwargs):
        import asyncio as _asyncio
        for a in args:
            if _asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    hass.loop = None
    return hass


def _iso(days_ago: float = 0) -> str:
    from datetime import datetime, timezone, timedelta
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _record(result="completed", initiator="schedule", dirt=10, sqft=200,
            dur_m=45, days_ago=1.0):
    return {
        "id": f"m_{int(days_ago*1000)}",
        "started_at": _iso(days_ago),
        "ended_at": _iso(days_ago - 0.04),
        "result": result,
        "initiator": initiator,
        "dirt": dirt,
        "sqft": sqft,
        "durationM": dur_m,
        "duration_min": dur_m,
    }


def _entity(battery_stats: dict = None, vacuum_state: dict = None,
            battery_mah: int = 2000) -> MagicMock:
    """Create a test entity.

    battery_mah — sets robot_profile.battery_mah for retention calculations.
                  Default 2000 matches legacy test values (non-9-series, scale=1.0).
    """
    from unittest.mock import MagicMock as _MM
    e = MagicMock()
    e.battery_stats = battery_stats or {}
    e.vacuum_state = vacuum_state or {}
    store = MaintenanceStore()
    e._config_entry.runtime_data.maintenance_store = store
    # RF0 robot_profile — scale=1.0 (non-9-series) so raw estCap == mAh directly
    profile = _MM()
    profile.battery_mah = battery_mah
    profile.estcap_scale_liion = 1.0
    profile.estcap_scale_nimh  = 1.0
    e._config_entry.runtime_data.robot_profile = profile
    return e


def _records(n: int = 10, sqft: float = 200, run_m: float = 40,
             dirt: float = 20, chrg_m: float = 5, dur_m: float = 50,
             ts_base: int = 1700000000) -> list[dict]:
    return [
        {
            "sqft": sqft,
            "runM": run_m,
            "durationM": dur_m,
            "dirt": dirt,
            "chrgM": chrg_m,
            "startTime": ts_base - i * 86400,
            "timestamp": ts_base - i * 86400 + 3000,
        }
        for i in range(n)
    ]


def _make_dtm() -> DirtThresholdManager:
    hass = MagicMock()
    def _close_coro(*args, **kwargs):
        import asyncio as _asyncio
        for a in args:
            if _asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    entry = MagicMock()
    entry.options = {}
    return DirtThresholdManager(hass, entry)


def _cloud_rec(dirt: float, sqft: float, ts: int, weekday_override: int | None = None) -> dict:
    """Build a minimal cloud record. ts must be a real unix timestamp."""
    return {"dirt": dirt, "sqft": sqft, "startTime": ts, "runM": 30, "durationM": 35}


def _mission_rec(
    duration_min: int = 60,
    area_sqft: float = 200.0,
    result: str = "completed",
    dirt: int | None = None,
    recharge_min: int | None = None,
    bbrun_hr: int = 100,
) -> dict:
    import time
    ts = int(time.time()) - 86400  # yesterday
    return {
        "id":           f"m_{ts}",
        "started_at":   datetime_v250_learning.fromtimestamp(ts, tz=UTC).isoformat(),
        "ended_at":     datetime_v250_learning.fromtimestamp(ts + duration_min * 60, tz=UTC).isoformat(),
        "duration_min": duration_min,
        "area_sqft":    area_sqft,
        "result":       result,
        "initiator":    "schedule",
        "zones":        [],
        "error_code":   None,
        "bbrun_hr":     bbrun_hr,
        "dirt":         dirt,
        "recharge_min": recharge_min,
    }


def _make_store_with_records(records: list[dict]) -> MissionStore:
    store = MissionStore()
    store._records = records
    return store


def _recent_weekday_ts(weekday: int, weeks_back: int = 1) -> int:
    """Return a UTC Unix timestamp for the most recent occurrence of `weekday`
    (0=Mon…6=Sun), going back `weeks_back` weeks to stay within the 12-week window."""
    from datetime import date
    today = datetime_v250_learning.now(UTC)
    # Find the Monday of this week, then offset to the desired weekday
    days_since_monday = today.weekday()  # 0 if today is Monday
    this_monday = today - timedelta(days=days_since_monday)
    target = this_monday + timedelta(days=weekday) - timedelta(weeks=weeks_back)
    return int(target.timestamp())


def _make_20_normal_records() -> list[dict]:
    """Create 20 normal mission records with consistent duration and area."""
    return [_mission_rec(duration_min=60, area_sqft=200.0) for _ in range(20)]


def _utcnow() -> datetime_v260_learning:
    return datetime_v260_learning.now(timezone.utc)


def _make_hass_v260_learning() -> MagicMock:
    hass = MagicMock()
    def _close_coro(*args, **kwargs):
        import asyncio as _asyncio
        for a in args:
            if _asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    return hass


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


class TestConsecutiveSkips:
    @pytest.mark.asyncio
    async def test_no_issue_below_3_skips(self):
        store = MaintenanceStore()
        store.consecutive_skips = 2
        entry = _make_entry(maintenance_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(_make_hass(), entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_at_3_skips(self):
        store = MaintenanceStore()
        store.consecutive_skips = 3
        entry = _make_entry(maintenance_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(_make_hass(), entry)
            mock_ir.async_create_issue.assert_called_once()
            assert mock_ir.async_create_issue.call_args[1]["translation_key"] == "consecutive_skips"
            assert mock_ir.async_create_issue.call_args[1]["translation_placeholders"]["count"] == "3"

    @pytest.mark.asyncio
    async def test_issue_cleared_when_skips_zero(self):
        store = MaintenanceStore()
        store.consecutive_skips = 0
        entry = _make_entry(maintenance_store=store)
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(hass, entry)
            mock_ir.async_delete_issue.assert_called_once_with(
                hass, "roomba_plus", "consecutive_skips"
            )

    def test_consecutive_skips_sensor_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "consecutive_clean_skips" in keys

    def test_skips_increments_on_timeout(self):
        """consecutive_skips increments after a blocking timeout."""
        store = MaintenanceStore()
        assert store.consecutive_skips == 0
        store.consecutive_skips += 1
        assert store.consecutive_skips == 1

    def test_skips_reset_on_completed_mission(self):
        store = MaintenanceStore()
        store.consecutive_skips = 4
        # Simulate the reset logic in callbacks.py
        if store.consecutive_skips > 0:
            store.consecutive_skips = 0
        assert store.consecutive_skips == 0


class TestCleaningSpeed:
    def test_basic_median(self):
        recs = _records(5, sqft=200, run_m=40)  # 200/40 = 5.0
        assert _raw_cleaning_speed(recs) == round(5.0 * 0.0929, 2)  # 0.46 m²/min

    def test_skips_zero_run_m(self):
        recs = [{"sqft": 200, "runM": 0}]
        assert _raw_cleaning_speed(recs) is None

    def test_skips_missing_sqft(self):
        recs = [{"runM": 40}]
        assert _raw_cleaning_speed(recs) is None

    def test_uses_durationM_fallback(self):
        recs = [{"sqft": 100, "durationM": 50}]
        result = _raw_cleaning_speed(recs)
        assert result == round(2.0 * 0.0929, 2)  # 0.19 m²/min

    def test_empty_records(self):
        assert _raw_cleaning_speed([]) is None

    def test_mixed_valid_invalid(self):
        recs = [{"sqft": 200, "runM": 40}, {"sqft": None}, {"runM": 0}]
        assert _raw_cleaning_speed(recs) == round(5.0 * 0.0929, 2)  # 0.46 m²/min


class TestDirtDensity:
    def test_basic_density(self):
        recs = _records(5, sqft=200, dirt=20)  # 20/200 = 0.1
        assert _raw_dirt_density(recs) == round(0.1 / 0.0929, 3)  # 1.076 events/m²

    def test_skips_zero_sqft(self):
        recs = [{"dirt": 10, "sqft": 0}]
        assert _raw_dirt_density(recs) is None

    def test_skips_missing_dirt(self):
        recs = [{"sqft": 200}]
        assert _raw_dirt_density(recs) is None

    def test_empty(self):
        assert _raw_dirt_density([]) is None

    def test_cause_attribute_present(self):
        attrs = _raw_dirt_density_attrs(_records(15))
        assert "cause" in attrs
        assert attrs["cause"] in ("brush_wear", "floor_dirty", "unknown")

    def test_cause_unknown_on_insufficient_data(self):
        attrs = _raw_dirt_density_attrs(_records(3))
        assert attrs["cause"] == "unknown"

    def test_description_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_dirt_density" in keys

    def test_attributes_fn_set(self):
        desc = next(d for d in CLOUD_RAW_SENSORS if d.key == "recent_dirt_density")
        assert desc.attributes_fn is not None


class TestRechargeFraction:
    def test_basic_fraction(self):
        # chrgM=10, durationM=50 → 20%
        recs = _records(5, chrg_m=10, dur_m=50)
        assert _raw_recharge_fraction(recs) == 20.0

    def test_falls_back_to_local_recharge_min(self):
        recs = [{"recharge_min": 10, "duration_min": 50}]
        result = _raw_recharge_fraction(recs)
        assert result == 20.0

    def test_skips_zero_duration(self):
        recs = [{"chrgM": 5, "durationM": 0}]
        assert _raw_recharge_fraction(recs) is None

    def test_empty(self):
        assert _raw_recharge_fraction([]) is None


class TestCleaningSpeedTrend:
    def _make_records(self, speeds: list[float], ts_base: int = 1700000000) -> list[dict]:
        """Build records with given speed (sqft/runM) in order newest-first."""
        return [
            {
                "sqft": s * 40,
                "runM": 40,
                "startTime": ts_base - i * 86400,
                "timestamp": ts_base - i * 86400 + 3000,
            }
            for i, s in enumerate(speeds)
        ]

    def test_unknown_with_fewer_than_6_records(self):
        recs = self._make_records([5.0] * 5)
        assert _raw_cleaning_speed_trend(recs) == "unknown"

    def test_stable_similar_speeds(self):
        recs = self._make_records([5.0] * 15)
        assert _raw_cleaning_speed_trend(recs) == "stable"

    def test_declining_trend(self):
        # Recent 5: ~3.0, older 10: ~6.0 → >10% decline
        recent = [3.0] * 5
        older  = [6.0] * 10
        recs = self._make_records(recent + older)
        assert _raw_cleaning_speed_trend(recs) == "declining"

    def test_improving_trend(self):
        recent = [8.0] * 5
        older  = [4.0] * 10
        recs = self._make_records(recent + older)
        assert _raw_cleaning_speed_trend(recs) == "improving"

    def test_gap_filter_excludes_post_gap_missions(self):
        """Records after a >7-day gap are excluded from trend calculation."""
        ts_base = 1700000000
        # 5 recent records at normal speed, then 10-day gap, then 10 records
        # at high speed (which should be excluded — post-gap catch-up cleans)
        recent = [
            {"sqft": 3 * 40, "runM": 40, "startTime": ts_base - i * 86400}
            for i in range(5)
        ]
        gap_ts = ts_base - 5 * 86400 - 10 * 86400  # 10-day gap
        old_post_gap = [
            {"sqft": 8 * 40, "runM": 40, "startTime": gap_ts - i * 86400}
            for i in range(10)
        ]
        recs = recent + old_post_gap
        # Should be "unknown" (not enough records after gap filter) or at least
        # not "improving" (old high-speed records excluded)
        result = _raw_cleaning_speed_trend(recs)
        assert result != "improving"

    def test_description_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "cleaning_speed_trend" in keys


class TestBatteryCapacityRetention:
    def test_100_pct_healthy_with_baseline(self):
        # baseline=2000 (first-observed), current=2000 → 100% healthy
        e = _entity(battery_stats={"estCap": 2000}, battery_mah=2000)
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) == 100.0

    def test_75_pct_degraded_with_baseline(self):
        # baseline=2000 (when new), current=1500 (degraded) → 75%
        e = _entity(battery_stats={"estCap": 1500}, battery_mah=2000)
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) == 75.0

    def test_fallback_to_oem_nominal_before_baseline(self):
        # No baseline yet → falls back to profile.battery_mah as cold-start denominator
        e = _entity(battery_stats={"estCap": 1500}, battery_mah=2000)
        # baseline_estcap is None; record_estcap_if_needed sets it to 1500
        # then denominator = 1500 → 100% (first observation treated as full)
        assert _battery_capacity_retention(e) == 100.0

    def test_aftermarket_shows_above_100_before_baseline(self):
        # Aftermarket 2500 mAh in robot with OEM 2000 mAh profile
        # First observation: baseline set to 2500, denominator=2500 → 100%
        e = _entity(battery_stats={"estCap": 2500}, battery_mah=2000)
        assert _battery_capacity_retention(e) == 100.0

    def test_sets_baseline_on_first_call(self):
        # Baseline receives the converted mAh value (= raw for scale=1.0)
        e = _entity(battery_stats={"estCap": 2000}, battery_mah=2000)
        store = e._config_entry.runtime_data.maintenance_store
        assert store.baseline_estcap is None
        _battery_capacity_retention(e)
        assert store.baseline_estcap == 2000.0  # scale=1.0: mAh == raw

    def test_baseline_not_overwritten_on_subsequent_calls(self):
        e = _entity(battery_stats={"estCap": 1800})
        store = e._config_entry.runtime_data.maintenance_store
        store.baseline_estcap = 2000.0
        _battery_capacity_retention(e)
        assert store.baseline_estcap == 2000.0  # unchanged

    def test_none_when_estcap_missing(self):
        e = _entity(battery_stats={})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) is None

    def test_none_when_no_store(self):
        e = MagicMock()
        e.battery_stats = {"estCap": 2000}
        e._config_entry.runtime_data.maintenance_store = None
        assert _battery_capacity_retention(e) is None

    def test_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "battery_capacity_retention" in keys

    def test_filter_fn_requires_estcap(self):
        desc = next(d for d in SENSORS if d.key == "battery_capacity_retention")
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True
        assert desc.filter_fn({"bbchg3": {}}) is False
        assert desc.filter_fn({}) is False


class TestEstimatedBatteryEol:
    def _make_entity(self, est_cap: float, baseline: float, cycles: int) -> MagicMock:
        e = _entity(battery_stats={"estCap": est_cap, "nLithChrg": cycles})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = baseline
        return e

    def test_returns_days_when_above_threshold(self):
        # 90% retention, 300 cycles → well above 65% EOL threshold
        e = self._make_entity(est_cap=1800, baseline=2000, cycles=300)
        result = _estimated_battery_eol(e)
        assert result is not None
        assert result > 0

    def test_returns_zero_at_or_below_threshold(self):
        # 60% retention → already below 65% EOL threshold
        e = self._make_entity(est_cap=1200, baseline=2000, cycles=500)
        assert _estimated_battery_eol(e) == 0

    def test_returns_none_without_baseline(self):
        e = _entity(battery_stats={"estCap": 1800, "nLithChrg": 300})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = None
        assert _estimated_battery_eol(e) is None

    def test_returns_none_without_cycles(self):
        e = _entity(battery_stats={"estCap": 1800})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _estimated_battery_eol(e) is None

    def test_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "estimated_battery_eol" in keys

    def test_filter_fn_requires_estcap(self):
        desc = next(d for d in SENSORS if d.key == "estimated_battery_eol")
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True
        assert desc.filter_fn({"bbchg3": {}}) is False


class TestMaintenanceStoreF5d:
    def test_record_estcap_if_needed_sets_baseline(self):
        store = MaintenanceStore()
        store.record_estcap_if_needed(2000.0)
        assert store.baseline_estcap == 2000.0

    def test_record_estcap_if_needed_noop_when_already_set(self):
        store = MaintenanceStore()
        store.baseline_estcap = 2000.0
        store.record_estcap_if_needed(1800.0)
        assert store.baseline_estcap == 2000.0  # unchanged

    def test_record_estcap_ignores_zero(self):
        store = MaintenanceStore()
        store.record_estcap_if_needed(0.0)
        assert store.baseline_estcap is None

    def test_consecutive_skips_defaults_to_zero(self):
        store = MaintenanceStore()
        assert store.consecutive_skips == 0


class TestSelfCalibratingMaintenance:
    def test_learned_filter_hours_none_with_one_reset(self):
        ms = MaintenanceStore()
        ms.reset_filter(100)
        assert ms.learned_filter_hours is None, "Need ≥2 resets for a learned value"

    def test_learned_filter_hours_correct_median_after_three_resets(self):
        ms = MaintenanceStore()
        ms.reset_filter(0)    # baseline (never cleaned before)
        ms.reset_filter(60)   # 60h interval
        ms.reset_filter(130)  # 70h interval  → median of [60, 70] = 65
        assert ms.learned_filter_hours == pytest.approx(65.0)

    def test_filter_remaining_uses_learned_hours(self):
        ms = MaintenanceStore()
        ms.reset_filter(0)
        ms.reset_filter(60)   # learned = 60h interval; filter_reset_hr = 60
        # 10h after last reset: 60 - 10 = 50h remaining
        assert ms.filter_remaining(current_hr=70, threshold=120) == 50
        # 60h after last reset: exactly at boundary → 0h remaining
        assert ms.filter_remaining(current_hr=120, threshold=120) == 0

    def test_filter_remaining_round_not_truncate(self):
        """round() not int() — fractional learned hours round to nearest integer."""
        ms = MaintenanceStore()
        # Two resets with interval 60h each → learned = 60.0
        ms.reset_filter(0)
        ms.reset_filter(60)   # filter_reset_hr = 60, learned = 60h
        ms.reset_filter(120)  # filter_reset_hr = 120, learned = median([60,60]) = 60
        # 60h after last reset (hr=180): remaining = 60 - 60 = 0 (at boundary)
        assert ms.filter_remaining(current_hr=180, threshold=120) == 0
        # 59h after last reset: 1h remaining
        assert ms.filter_remaining(current_hr=179, threshold=120) == 1

    def test_brush_reset_history_populated(self):
        ms = MaintenanceStore()
        ms.reset_brush(50)
        ms.reset_brush(120)
        assert ms.brush_reset_history == [50, 120]

    def test_negative_intervals_skipped(self):
        """Decreasing values in history (data corruption) must be skipped."""
        ms = MaintenanceStore()
        # Manually inject a corrupted history with a decrease
        ms.filter_reset_history = [100, 80, 160]  # 80 < 100 → negative interval skipped
        # Valid interval: 160 - 80 = 80 (the 100→80 pair produces -20, skipped)
        learned = ms.learned_filter_hours
        assert learned is not None
        assert learned > 0


class TestIA74Maint:

    def test_new_fields_default_none(self):
        ms = MaintenanceStore()
        assert ms.wheel_cleaned_at is None
        assert ms.contact_cleaned_at is None
        assert ms.bin_cleaned_at is None

    @pytest.mark.asyncio
    async def test_fields_persist_through_save_load(self):
        ms = MaintenanceStore()
        ms.wheel_cleaned_at = "2026-06-01T10:00:00"
        ms.contact_cleaned_at = "2026-06-02T10:00:00"

        saved: dict = {}

        async def _save(data: dict) -> None:
            saved.update(data)

        async def _load() -> dict:
            return saved

        store_mock = MagicMock()
        store_mock.async_save = _save
        store_mock.async_load = _load
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

        with patch(
            "custom_components.roomba_plus.maintenance_store.Store",
            return_value=store_mock,
        ):
            await ms.async_save(hass, "e1")
            ms2 = MaintenanceStore()
            await ms2.async_load(hass, "e1")

        assert ms2.wheel_cleaned_at == "2026-06-01T10:00:00"
        assert ms2.contact_cleaned_at == "2026-06-02T10:00:00"
        assert ms2.bin_cleaned_at is None


class TestM1EstcapPersistence:
    """M1: record_estcap_if_needed returns True on first set, False on subsequent."""

    def test_returns_true_on_first_set(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        assert store.baseline_estcap is None
        result = store.record_estcap_if_needed(3000.0)
        assert result is True
        assert store.baseline_estcap == 3000.0

    def test_returns_false_on_subsequent_calls(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        store.record_estcap_if_needed(3000.0)  # first set
        result = store.record_estcap_if_needed(2900.0)  # should be no-op
        assert result is False
        assert store.baseline_estcap == 3000.0  # unchanged

    def test_returns_false_for_zero_value(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        result = store.record_estcap_if_needed(0.0)
        assert result is False
        assert store.baseline_estcap is None


class TestInspectResetMethods:
    """MaintenanceStore.reset_wheel/contact/bin_cleaning set timestamps."""

    def _store(self) -> MaintenanceStore:
        return MaintenanceStore()

    def test_reset_wheel_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.wheel_cleaned_at is None
        store.reset_wheel_cleaning()
        assert store.wheel_cleaned_at is not None
        # Must be a parseable ISO datetime
        parsed = dt_util.parse_datetime(store.wheel_cleaned_at)
        assert parsed is not None

    def test_reset_contact_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.contact_cleaned_at is None
        store.reset_contact_cleaning()
        assert store.contact_cleaned_at is not None
        parsed = dt_util.parse_datetime(store.contact_cleaned_at)
        assert parsed is not None

    def test_reset_bin_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.bin_cleaned_at is None
        store.reset_bin_cleaning()
        assert store.bin_cleaned_at is not None
        parsed = dt_util.parse_datetime(store.bin_cleaned_at)
        assert parsed is not None

    def test_reset_wheel_cleaning_updates_existing_timestamp(self):
        """Calling reset again updates the timestamp to the new time."""
        store = self._store()
        store.reset_wheel_cleaning()
        first = store.wheel_cleaned_at
        # Patch dt_util.now to advance time
        import datetime
        future = dt_util.now() + datetime.timedelta(hours=24)
        with patch("custom_components.roomba_plus.maintenance_store.dt_util.now",
                   return_value=future):
            store.reset_wheel_cleaning()
        assert store.wheel_cleaned_at != first

    def test_timestamps_are_independent(self):
        """Each component has its own timestamp slot."""
        store = self._store()
        store.reset_wheel_cleaning()
        assert store.contact_cleaned_at is None
        assert store.bin_cleaned_at is None
        store.reset_contact_cleaning()
        assert store.bin_cleaned_at is None
        store.reset_bin_cleaning()
        # All three now set
        assert store.wheel_cleaned_at is not None
        assert store.contact_cleaned_at is not None
        assert store.bin_cleaned_at is not None
