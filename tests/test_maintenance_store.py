"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest

from tests.conftest import robot_mock, hass_mock, entry_mock
from custom_components.roomba_plus.const import DEFAULT_FILTER_HOURS as _F, DEFAULT_BRUSH_HOURS as _B, DEFAULT_SIDE_BRUSH_HOURS as _S, DEFAULT_CLEAN_BASE_BAG_HOURS as _G
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.const import CARE_TASKS
import datetime
from custom_components.roomba_plus.sensor import RoombaSensorDescription
from custom_components.roomba_plus.sensor import SENSORS
from unittest.mock import MagicMock
from unittest.mock import patch
from custom_components.roomba_plus.mission_store import MissionStore
import sys
from custom_components.roomba_plus.sensor import _raw_cleaning_speed
from custom_components.roomba_plus.sensor import _raw_dirt_density
from custom_components.roomba_plus.sensor import _raw_recharge_fraction
from custom_components.roomba_plus.sensor import _raw_cleaning_speed_trend
from custom_components.roomba_plus.sensor import _battery_capacity_retention
from custom_components.roomba_plus.sensor import _estimated_battery_eol
from datetime import UTC
from datetime import datetime as datetime_v250_learning
from datetime import timedelta
from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
from datetime import datetime as datetime_v260_learning
from datetime import timezone
from homeassistant.util import dt as dt_util
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock


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
    entry = entry_mock()
    data = MagicMock()
    data.mission_store = mission_store or MissionStore()
    data.maintenance_store = maintenance_store or MaintenanceStore()
    # Only the two fields that are actually read remain here. Four
    # siblings were removed with the F6a/F6b scaffolding -- the
    # fixture kept setting them long after nothing consumed them,
    # and MagicMock accepted every one without complaint.
    data.cleaning_speed_trend_value = "stable"
    data.battery_retention_value = 95.0
    data.roomba_reported_state = MagicMock(return_value={"bbrun": {"hr": 100}})
    entry.runtime_data = data
    entry.options = {"brush_hours": 150, "filter_hours": 150}
    return entry


def _make_hass():
    # `hass_mock()` already closes coroutines handed to
    # `async_create_task`. A local copy of that logic used to sit here
    # and overwrote the shared one with a plain function -- which works
    # for the closing, but loses `call_args`, and left this file's other
    # coroutines leaking because it only covered `async_create_task`.
    hass = hass_mock()
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
    # `hass` and the config entry both launch background work from this
    # entity -- the store save goes through one or the other depending
    # on the path taken.
    e.hass = hass_mock()
    e._config_entry = entry_mock()
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
    # v3.1.0 L9-BATTERY — explicit None (not an auto-generated MagicMock)
    # so _estimated_battery_eol's self-calibration gate correctly takes the
    # "no RobotProfileStore" fallback path rather than treating a bare
    # MagicMock as a usable (but nonsensical) store.
    e._config_entry.runtime_data.robot_profile_store = None
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
    hass = hass_mock()
    def _close_coro(*args, **kwargs):
        import asyncio as _asyncio
        for a in args:
            if _asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    entry = entry_mock()
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
    hass = hass_mock()
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


class TestMaintenanceStoreDueItems:
    """v3.4.3 FLEET-1 — due_items(), extracted from binary_sensor.py's
    RoombaMaintenanceDue._due_items() so the household REST endpoint can
    share the exact same logic."""

    def test_nothing_due_fresh_robot(self):
        store = MaintenanceStore()
        state = {"bbrun": {"hr": 10}}
        assert store.due_items(state, {}) == []

    def test_filter_due_at_threshold(self):
        store = MaintenanceStore()
        state = {"bbrun": {"hr": _F}}
        assert "filter" in store.due_items(state, {})

    def test_brush_key_for_vacuum(self):
        store = MaintenanceStore()
        state = {"bbrun": {"hr": _B}}
        due = store.due_items(state, {})
        assert "brush" in due
        assert "pad" not in due

    def test_pad_key_for_mop(self):
        """is_mop(state) — detectedPad present → 'pad' not 'brush'."""
        store = MaintenanceStore()
        state = {"bbrun": {"hr": _B}, "detectedPad": "wet"}
        due = store.due_items(state, {})
        assert "pad" in due
        assert "brush" not in due

    def test_custom_thresholds_from_options(self):
        store = MaintenanceStore()
        state = {"bbrun": {"hr": 5}}
        assert store.due_items(state, {"filter_threshold_hours": 5}) == ["filter"]

    def test_all_four_due_simultaneously_without_cloud_data(self):
        """A CLEAN BASE IS NOW PART OF THE PREMISE. This passed
        `{"bbrun": ...}` with no dock at all, which is a robot that has
        no bag -- so it asserted the false positive a tester reported on
        his Roomba 980 rather than the behaviour it names."""
        store = MaintenanceStore()
        state = {"bbrun": {"hr": _B}, "dock": {"fwVer": "1.2.3"}}
        due = store.due_items(state, {})
        assert set(due) == {"filter", "brush", "side_brush", "clean_base_bag"}

    def test_missing_bbrun_defaults_to_zero_hours(self):
        """No bbrun at all -> current_hr defaults to 0, nothing due yet
        (matches the pre-extraction binary_sensor.py behaviour)."""
        store = MaintenanceStore()
        assert store.due_items({}, {}) == []

    def test_filter_remaining_overdue_after_reset(self):
        """Past threshold, still clamped at 0."""
        store = MaintenanceStore()
        store.reset_filter(200)
        assert store.filter_remaining(500, 200) == 0

    def _cloud_part(self, part_id, *, count_used=0, count_remaining=100,
                     count_type="minutes", last_updated_ts=1700000000):
        """part_id alone determines the hydrated role (see part_role() /
        IROBOT_PART_ROLES): "35"=filter, "36"=side_brush, "37"=main_brush,
        "139"=clean_base_bag."""
        return {
            "part_id": part_id, "count_used": count_used,
            "count_remaining": count_remaining, "count_type": count_type,
            "last_updated_ts": last_updated_ts,
        }

    def test_side_brush_due_when_cloud_counter_exhausted(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("36", count_remaining=0)], 300,
        )
        assert "side_brush" in store.due_items({"bbrun": {"hr": 300}}, {})

    def test_side_brush_due_when_cloud_counter_negative(self):
        """A counter that has overshot zero (still not reset) must also read due."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("36", count_remaining=-30)], 300,
        )
        assert "side_brush" in store.due_items({"bbrun": {"hr": 300}}, {})

    def test_side_brush_not_due_with_positive_remaining(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("36", count_remaining=6000)], 300,
        )
        assert "side_brush" not in store.due_items({"bbrun": {"hr": 300}}, {})

    def test_side_brush_not_due_below_hardcoded_threshold_without_cloud_data(self):
        store = MaintenanceStore()
        assert "side_brush" not in store.due_items({"bbrun": {"hr": 50}}, {})

    def test_side_brush_due_at_hardcoded_threshold_without_cloud_data(self):
        store = MaintenanceStore()
        assert "side_brush" in store.due_items({"bbrun": {"hr": _S}}, {})

    def test_clean_base_bag_due_when_cloud_counter_exhausted(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("139", count_remaining=0)], 300,
        )
        assert "clean_base_bag" in store.due_items(
            {"bbrun": {"hr": 300}, "dock": {"fwVer": "1.2.3"}}, {}
        )

    def test_clean_base_bag_not_due_below_hardcoded_threshold_without_cloud_data(self):
        store = MaintenanceStore()
        assert "clean_base_bag" not in store.due_items(
            {"bbrun": {"hr": 10}, "dock": {"fwVer": "1.2.3"}}, {}
        )

    def test_clean_base_bag_due_at_hardcoded_threshold_without_cloud_data(self):
        store = MaintenanceStore()
        assert "clean_base_bag" in store.due_items(
            {"bbrun": {"hr": 30}, "dock": {"fwVer": "1.2.3"}}, {}
        )

    def test_cloud_exhausted_counter_overrides_local_threshold_for_filter(self):
        """Local elapsed hours alone would say "not due yet" (10h since
        reset, 60h threshold) but the cloud's own counter says otherwise —
        the cloud signal must win. The local reset is older than the
        cloud's own (2023-11), so the cloud knows of it."""
        store = MaintenanceStore()
        store.reset_filter(290)
        store.filter_reset_at = "2023-01-01T00:00:00+00:00"
        store.hydrate_from_cloud_parts(
            [self._cloud_part("35", count_remaining=0)], 300,
        )
        assert "filter" in store.due_items({"bbrun": {"hr": 300}}, {})

    def test_a_replacement_recorded_after_the_clouds_last_reset_stands(self):
        """4.5.0. The button was pressed, the push to iRobot did not
        arrive: the cloud still counts the old filter as used up. Until
        4.5.0 that kept the robot due until a restart; now the newer
        local reset decides, and the next cloud poll does not undo it."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("35", count_used=3120, count_remaining=0)], 300,
        )
        assert "filter" in store.due_items({"bbrun": {"hr": 300}}, {})
        store.reset_filter(300)
        assert "filter" not in store.due_items({"bbrun": {"hr": 301}}, {})
        store.hydrate_from_cloud_parts(
            [self._cloud_part("35", count_used=3120, count_remaining=0)], 302,
        )
        assert store.filter_reset_hr == 300
        assert "filter" not in store.due_items({"bbrun": {"hr": 302}}, {})

    def test_cloud_healthy_counter_overrides_local_threshold_for_filter(self):
        """Local elapsed hours alone would say "due" (past the 60h
        threshold with no reset recorded) but the cloud's own counter
        says there is plenty left — the cloud signal must win."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [self._cloud_part("35", count_remaining=6000)], 300,
        )
        assert "filter" not in store.due_items({"bbrun": {"hr": 300}}, {})


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
        e = _entity(battery_stats={"estCap": 1500}, vacuum_state={"batPct": 100},
                    battery_mah=2000)
        # baseline_estcap is None; record_estcap_if_needed sets it to 1500
        # then denominator = 1500 → 100% (first observation treated as full)
        assert _battery_capacity_retention(e) == 100.0

    def test_aftermarket_shows_above_100_before_baseline(self):
        # Aftermarket 2500 mAh in robot with OEM 2000 mAh profile
        # First observation: baseline set to 2500, denominator=2500 → 100%
        e = _entity(battery_stats={"estCap": 2500}, vacuum_state={"batPct": 100},
                    battery_mah=2000)
        assert _battery_capacity_retention(e) == 100.0

    def test_sets_baseline_on_first_call(self):
        # Baseline receives the converted mAh value (= raw for scale=1.0)
        e = _entity(battery_stats={"estCap": 2000}, vacuum_state={"batPct": 100},
                    battery_mah=2000)
        store = e._config_entry.runtime_data.maintenance_store
        assert store.baseline_estcap is None
        _battery_capacity_retention(e)
        assert store.baseline_estcap == 2000.0  # scale=1.0: mAh == raw

    @pytest.mark.parametrize("pct", [None, 40, 94])
    def test_no_baseline_from_a_pack_not_charged(self, pct):
        """4.4.1, @azrael: his old baseline, 436 mAh, came from a low
        charge. Until the pack is charged the OEM figure stands in."""
        state = {} if pct is None else {"batPct": pct}
        e = _entity(battery_stats={"estCap": 1500}, vacuum_state=state, battery_mah=2000)
        assert _battery_capacity_retention(e) == 75.0
        assert e._config_entry.runtime_data.maintenance_store.baseline_estcap is None

    def test_a_battery_reset_takes_a_new_baseline(self):
        """4.4.1, @azrael: 573 % after a new pack, for good -- the reset
        left the old pack's baseline in place."""
        e = _entity(battery_stats={"estCap": 2500}, vacuum_state={"batPct": 100},
                    battery_mah=2488)
        store = e._config_entry.runtime_data.maintenance_store
        store.baseline_estcap = 436.0
        assert _battery_capacity_retention(e) == pytest.approx(573.4, abs=0.1)
        store.reset_battery(1234)
        assert store.baseline_estcap is None
        assert _battery_capacity_retention(e) == 100.0
        assert store.baseline_estcap == 2500.0

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
        # Not from hour 0: a reset recorded at 0 is what the pre-4.3.0
        # buttons wrote on a j-series, and no interval starts there.
        ms = MaintenanceStore()
        ms.reset_filter(10)   # baseline
        ms.reset_filter(70)   # 60h interval
        ms.reset_filter(140)  # 70h interval  → median of [60, 70] = 65
        assert ms.learned_filter_hours == pytest.approx(65.0)

    def test_filter_remaining_uses_learned_hours(self):
        ms = MaintenanceStore()
        ms.reset_filter(10)
        ms.reset_filter(70)   # learned = 60h interval; filter_reset_hr = 70
        # 10h after last reset: 60 - 10 = 50h remaining
        assert ms.filter_remaining(current_hr=80, threshold=120) == 50
        # 60h after last reset: exactly at boundary → 0h remaining
        assert ms.filter_remaining(current_hr=130, threshold=120) == 0

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
        hass = hass_mock()
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


class TestMaintenanceColdStartBaseline:
    """v3.4.1 MAINTENANCE-COLD-START — field-confirmed (mdarocha, i3+, 412
    missions / 294h prior runtime, no reset ever recorded in this
    integration). The actual seeding logic lives in __init__.py's async
    setup flow (no existing test scaffolding exercises that directly —
    see test_const.py's MapCapabilityGatingNoLocalPose for the same
    situation on a different fix in this release). These tests cover
    the MaintenanceStore-level pieces the fix depends on: the new
    persisted flags default correctly, survive save/load, and stay
    backward compatible with pre-v3.4.1 stored data.
    """

    def test_baseline_seeded_flags_default_false(self):
        store = MaintenanceStore()
        assert store.filter_baseline_seeded is False
        assert store.brush_baseline_seeded is False

    @pytest.mark.asyncio
    async def test_baseline_seeded_flags_persist_through_save_load(self):
        ms = MaintenanceStore()
        ms.filter_reset_hr = 294
        ms.filter_baseline_seeded = True

        saved: dict = {}

        async def _save(data: dict) -> None:
            saved.update(data)

        async def _load() -> dict:
            return saved

        store_mock = MagicMock()
        store_mock.async_save = _save
        store_mock.async_load = _load
        hass = hass_mock()
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

        assert ms2.filter_reset_hr == 294
        assert ms2.filter_baseline_seeded is True
        assert ms2.brush_baseline_seeded is False  # untouched, still default

    @pytest.mark.asyncio
    async def test_pre_v342_data_without_seeded_flags_defaults_false(self):
        """A store saved before v3.4.1 has no *_baseline_seeded keys at
        all in its persisted dict — loading it must default both to
        False (never seeded), not crash."""
        ms = MaintenanceStore()
        ms.filter_reset_hr = 150
        ms.brush_reset_hr = 150

        # Simulate a pre-v3.4.1 persisted dict: no baseline_seeded keys.
        pre_v342_data = {
            "filter_reset_hr": 150,
            "brush_reset_hr": 150,
            "battery_reset_hr": 0,
            "filter_reset_history": [150],
            "brush_reset_history": [150],
        }

        async def _load() -> dict:
            return pre_v342_data

        store_mock = MagicMock()
        store_mock.async_load = _load
        hass = hass_mock()
        with patch(
            "custom_components.roomba_plus.maintenance_store.Store",
            return_value=store_mock,
        ):
            ms2 = MaintenanceStore()
            await ms2.async_load(hass, "e1")

        assert ms2.filter_reset_hr == 150
        assert ms2.filter_baseline_seeded is False
        assert ms2.brush_baseline_seeded is False
        # And this existing user's genuine reset history must be intact —
        # exactly what the seeding logic's second gate condition protects.
        assert ms2.filter_reset_history == [150]

class TestSymmetricLocalResetSlots:
    """All four maintenance roles go through the same local-slot mechanism
    (IROBOT_PART_ROLE_TO_STORE_SLOT / reset_baseline_for_role) — side_brush
    and clean_base_bag are symmetric with filter/main_brush, not a special
    cloud-only case."""

    def test_reset_side_brush_writes_its_own_slot(self):
        store = MaintenanceStore()
        store.reset_side_brush(150)
        assert store.side_brush_reset_hr == 150
        assert store.side_brush_reset_at is not None
        assert store.side_brush_reset_history == [150]

    def test_reset_clean_base_bag_writes_its_own_slot(self):
        store = MaintenanceStore()
        store.reset_clean_base_bag(250)
        assert store.clean_base_bag_reset_hr == 250
        assert store.clean_base_bag_reset_at is not None
        assert store.clean_base_bag_reset_history == [250]

    def test_reset_baseline_for_role_covers_all_four_roles(self):
        from custom_components.roomba_plus.const import (
            IROBOT_PART_ROLE_CLEAN_BASE_BAG,
            IROBOT_PART_ROLE_FILTER,
            IROBOT_PART_ROLE_MAIN_BRUSH,
            IROBOT_PART_ROLE_SIDE_BRUSH,
        )
        store = MaintenanceStore()
        store.reset_filter(10)
        store.reset_brush(20)
        store.reset_side_brush(30)
        store.reset_clean_base_bag(40)
        assert store.reset_baseline_for_role(IROBOT_PART_ROLE_FILTER)[0] == 10
        assert store.reset_baseline_for_role(IROBOT_PART_ROLE_MAIN_BRUSH)[0] == 20
        assert store.reset_baseline_for_role(IROBOT_PART_ROLE_SIDE_BRUSH)[0] == 30
        assert store.reset_baseline_for_role(IROBOT_PART_ROLE_CLEAN_BASE_BAG)[0] == 40

    def test_hydration_populates_local_slots_for_all_four_roles(self):
        """hydrate_from_cloud_parts() reconstructs reset_hr/reset_at for
        side_brush/clean_base_bag exactly as it already does for
        filter/main_brush — same code path, extended IROBOT_PART_ROLE_TO_STORE_SLOT."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([
            {"part_id": "35", "count_used": 600, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
            {"part_id": "36", "count_used": 1200, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
            {"part_id": "37", "count_used": 1800, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
            {"part_id": "139", "count_used": 2400, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
        ], 500)
        assert store.filter_reset_hr == 500 - 10
        assert store.brush_reset_hr == 500 - 30
        assert store.side_brush_reset_hr == 500 - 20
        assert store.clean_base_bag_reset_hr == 500 - 40
        for slot in ("filter", "brush", "side_brush", "clean_base_bag"):
            assert getattr(store, f"{slot}_reset_at") is not None

    def test_side_brush_and_clean_base_bag_slots_round_trip_through_save_load(self):
        store = MaintenanceStore()
        store.reset_side_brush(77)
        store.reset_clean_base_bag(88)
        data = {
            "side_brush_reset_hr": store.side_brush_reset_hr,
            "side_brush_reset_at": store.side_brush_reset_at,
            "side_brush_reset_history": store.side_brush_reset_history,
            "clean_base_bag_reset_hr": store.clean_base_bag_reset_hr,
            "clean_base_bag_reset_at": store.clean_base_bag_reset_at,
            "clean_base_bag_reset_history": store.clean_base_bag_reset_history,
        }
        store2 = MaintenanceStore()
        store2.side_brush_reset_hr = int(data["side_brush_reset_hr"])
        store2.side_brush_reset_at = data["side_brush_reset_at"]
        store2.side_brush_reset_history = list(data["side_brush_reset_history"])
        store2.clean_base_bag_reset_hr = int(data["clean_base_bag_reset_hr"])
        store2.clean_base_bag_reset_at = data["clean_base_bag_reset_at"]
        store2.clean_base_bag_reset_history = list(data["clean_base_bag_reset_history"])
        assert store2.side_brush_reset_hr == 77
        assert store2.clean_base_bag_reset_hr == 88

    @pytest.mark.asyncio
    async def test_async_save_load_round_trip_includes_all_four_roles(self):
        store = MaintenanceStore()
        store.reset_filter(10)
        store.reset_brush(20)
        store.reset_side_brush(30)
        store.reset_clean_base_bag(40)

        saved: dict = {}

        async def _save(data):
            saved.update(data)

        async def _load():
            return saved

        store_mock = MagicMock()
        store_mock.async_save = _save
        store_mock.async_load = _load
        hass = hass_mock()
        with patch(
            "custom_components.roomba_plus.maintenance_store.Store",
            return_value=store_mock,
        ):
            await store.async_save(hass, "e1")
            store2 = MaintenanceStore()
            await store2.async_load(hass, "e1")

        assert store2.side_brush_reset_hr == 30
        assert store2.clean_base_bag_reset_hr == 40


def _wear_entity(hr, maintenance_store, options=None):
    """Minimal fake IRobotEntity for exercising sensor_helpers' consumable
    wear-rate/days-until-due/max-hours functions directly."""
    entity = MagicMock()
    entity._config_entry.options = options or {}
    entity._config_entry.runtime_data.maintenance_store = maintenance_store
    entity._config_entry.runtime_data.mission_store = MissionStore()
    entity.run_stats = {"hr": hr}
    return entity


def _cloud_part(part_id, *, count_used, count_remaining,
                 count_type="minutes", days_ago=10, ts=None):
    """part_id determines the role (see part_role()). days_ago controls
    last_updated_ts so wear_rate_since_reset's 3-day minimum baseline is
    satisfied by default; pass ts=None explicitly via omitting last_updated_ts
    is not supported here — use _cloud_part_no_timestamp for that case."""
    import time
    epoch = ts if ts is not None else int(time.time()) - int(days_ago * 86400)
    return {
        "part_id": part_id, "count_used": count_used,
        "count_remaining": count_remaining, "count_type": count_type,
        "last_updated_ts": epoch,
    }


class TestConsumableWearRateAndDaysUntilDueAllFourRoles:
    """The four roles use the same role-parameterized helper functions."""

    def test_side_brush_wear_rate_available_with_valid_cloud_data(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_wear_rate
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [_cloud_part("36", count_used=600, count_remaining=3000)], 500,
        )
        rate = _consumable_wear_rate(_wear_entity(500, store), "side_brush")
        assert rate is not None and rate > 0

    def test_clean_base_bag_wear_rate_available_with_valid_cloud_data(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_wear_rate
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [_cloud_part("139", count_used=600, count_remaining=3000)], 500,
        )
        rate = _consumable_wear_rate(_wear_entity(500, store), "clean_base_bag")
        assert rate is not None and rate > 0

    def test_side_brush_wear_rate_unavailable_with_missing_timestamp(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_wear_rate
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [{"part_id": "36", "count_used": 600, "count_remaining": 3000,
              "count_type": "minutes"}],
            500,
        )
        assert _consumable_wear_rate(_wear_entity(500, store), "side_brush") is None

    def test_clean_base_bag_wear_rate_unavailable_with_non_minute_counter(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_wear_rate
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [_cloud_part("139", count_used=600, count_remaining=3000, count_type="cycles")],
            500,
        )
        assert _consumable_wear_rate(_wear_entity(500, store), "clean_base_bag") is None

    def test_side_brush_days_until_due_available_with_valid_cloud_data(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_days_until_due
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [_cloud_part("36", count_used=600, count_remaining=3000)], 500,
        )
        assert _consumable_days_until_due(
            _wear_entity(500, store), "side_brush"
        ) is not None

    def test_clean_base_bag_days_until_due_unavailable_without_wear_data(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_days_until_due
        assert _consumable_days_until_due(
            _wear_entity(500, MaintenanceStore()), "clean_base_bag"
        ) is None

    def test_side_brush_wear_rate_and_max_hours_survive_a_cloud_outage(self):
        from custom_components.roomba_plus.sensor_helpers import (
            _consumable_max_hours,
            _consumable_wear_rate,
        )
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [_cloud_part("36", count_used=600, count_remaining=3000)], 500,
        )
        entity = _wear_entity(500, store)
        assert _consumable_wear_rate(entity, "side_brush") is not None
        assert _consumable_max_hours(entity, "side_brush") == 60
        store.cloud_parts = {}
        assert _consumable_wear_rate(entity, "side_brush") is not None
        assert _consumable_max_hours(entity, "side_brush") == _S


class TestMaxHoursPerRole:
    def test_max_hours_uses_local_or_cloud_life_for_every_role(self):
        from custom_components.roomba_plus.sensor_helpers import _consumable_max_hours
        store = MaintenanceStore()
        assert _consumable_max_hours(_wear_entity(0, store), "filter") == _F
        store.reset_brush(10)
        store.reset_brush(110)
        store.reset_brush(230)
        assert _consumable_max_hours(_wear_entity(230, store), "main_brush") == 110
        assert _consumable_max_hours(_wear_entity(50, store), "side_brush") == _S
        store.hydrate_from_cloud_parts(
            [_cloud_part("139", count_used=1200, count_remaining=2400)], 500,
        )
        assert _consumable_max_hours(
            _wear_entity(500, store), "clean_base_bag"
        ) == 60


class TestPartsTheRobotDoesNotHave:
    """A tester's Roomba 980 (R980020) reported a Clean Base bag due.

    It charges on a plain dock. There is no bag. Every per-part entity
    was already suppressed for it by `has_clean_base` -- no
    `clean_base_status`, no wear-rate sensor, no reset button -- but
    `due_items()` iterated all four consumable roles regardless.

    The cloud never contradicted it, because there is no cloud record
    for a part the robot does not have: `_cloud_exhaustion_state()`
    returned None and `_is_due()` fell through to the local hour count,
    which counts RUNTIME rather than bag use. With the bag never reset
    (nothing to reset) and a 30-hour default, it went due at 30 hours
    and reported 75 overdue at his 105.

    The report also suggested gating `side_brush` the same way. That
    would be wrong: a 980 has a side brush, and gating it would stop
    legitimate reminders on every robot without a Clean Base.
    """

    #: The dock exactly as his diagnostics carry it.
    _NO_CLEAN_BASE = {"bbrun": {"hr": 105}, "dock": {"known": False}}
    _CLEAN_BASE = {"bbrun": {"hr": 105}, "dock": {"fwVer": "1.2.3"}}

    def test_no_bag_is_due_without_a_clean_base(self) -> None:
        store = MaintenanceStore()

        assert "clean_base_bag" not in store.due_items(self._NO_CLEAN_BASE, {})

    def test_the_bag_is_still_due_with_one(self) -> None:
        """The other half: gating must not silence the robots that do
        have a bag."""
        store = MaintenanceStore()

        assert "clean_base_bag" in store.due_items(self._CLEAN_BASE, {})

    def test_the_other_three_roles_are_untouched(self) -> None:
        """A 980 has a filter, a main brush and a side brush.

        At his 105 hours only the filter is past its threshold (60);
        the brushes sit at 200 and 150. The point is that the bag is
        gone from the list and nothing else changed with it.
        """
        store = MaintenanceStore()

        due = set(store.due_items(self._NO_CLEAN_BASE, {}))
        with_base = set(store.due_items(self._CLEAN_BASE, {}))

        assert due == {"filter"}
        assert with_base - due == {"clean_base_bag"}

    def test_only_the_bag_declares_itself_absent(self) -> None:
        """Pinned so a later role does not acquire the condition by
        being copied from the one above it."""
        from custom_components.roomba_plus.const import CONSUMABLE_ROLES

        gated = {
            role for role, spec in CONSUMABLE_ROLES.items()
            if spec.absent_when is not None
        }

        assert len(gated) == 1
        assert CONSUMABLE_ROLES[next(iter(gated))].slot == "clean_base_bag"


class TestTheSeedReachesEveryRole:
    """`maintenance_due` reported a healthy side brush as 2102 hours
    overdue on a 980 with 2252 hours on it (@azrael-129).

    THE NUMBER WAS THE ROBOT'S WHOLE LIFETIME. With the baseline never
    set, `current_hr - reset_hr` is the age of the robot, and the
    "overdue" figure is a function of install time rather than wear.

    Three gaps stacked, and none of them alone was visible:

      - cold-start seeding covered `filter` and `brush` and stopped
      - no reset service exists for `side_brush` or `clean_base_bag`
      - the reset button is only built when iRobot's cloud serves the
        part, which on this account it does not

    So there was no way to clear it from inside Home Assistant at all.

    THE REASON RECORDED FOR THE OMISSION WAS FALSE: "no CONF_*_HOURS
    default exists to seed against". Both defaults exist and
    `threshold_hours()` already falls back to them.

    WHY NO TEST CAUGHT IT: every fixture either serves cloud parts, so
    hydration writes the baseline, or uses a robot young enough that a
    missing baseline is invisible. The failure needs lifetime > 150 h
    AND no cloud parts AND no reset history -- a robot in service for
    months on an incomplete account, which is the common real install.
    """

    @staticmethod
    def _store():
        from custom_components.roomba_plus.maintenance_store import (
            MaintenanceStore,
        )

        return MaintenanceStore()

    def test_all_four_roles_have_a_seeded_flag(self) -> None:
        store = self._store()

        for slot in ("filter", "brush", "side_brush", "clean_base_bag"):
            assert hasattr(store, f"{slot}_baseline_seeded"), slot
            assert hasattr(store, f"{slot}_reset_hr"), slot

    def test_the_setup_seeds_the_two_that_were_skipped(self) -> None:
        import inspect

        import custom_components.roomba_plus as init

        source = inspect.getsource(init)

        assert '"side_brush", "clean_base_bag"' in source

    def test_the_false_reason_is_gone(self) -> None:
        """It justified the omission with a premise that was not true,
        and the next reader would have believed it."""
        import inspect

        from custom_components.roomba_plus import maintenance_store

        source = inspect.getsource(maintenance_store)

        assert "no CONF_*_HOURS default exists to seed against" not in source

    def test_the_default_it_denied_does_exist(self) -> None:
        from custom_components.roomba_plus.const import (
            DEFAULT_SIDE_BRUSH_HOURS,
        )

        # iRobot's own side-brush budget (4.3.2), read off an i3+'s counter
        assert DEFAULT_SIDE_BRUSH_HOURS == 157

    def test_hydration_overwrites_a_seed(self) -> None:
        """The seed is a local fallback and must yield to cloud truth --
        the whole safety argument for seeding at all."""
        import inspect

        from custom_components.roomba_plus.maintenance_store import (
            MaintenanceStore,
        )

        source = inspect.getsource(MaintenanceStore.hydrate_from_cloud_parts)

        assert 'setattr(self, f"{slot}_reset_hr", reset_hr)' in source


class TestSeedingSurvivesASlowMqttStart:
    """Seeding ran once, at setup, reading the robot's reported state
    directly. On a cold boot MQTT has often delivered nothing yet, so
    `bbrun.hr` is 0 and **all four** roles are skipped.

    And setup was the only place that seeded. A host whose MQTT is
    reliably slower than its startup therefore never seeded at all,
    while the same install seeded everything on a config-entry reload.

    @azrael-129 found this while reporting the side brush and called it
    the smaller half of his report. It is the larger one: the side-brush
    gap needed an account with no cloud parts, this one reaches filter
    and main brush too, on any robot.
    """

    def test_the_seed_runs_on_mission_messages(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)

        assert "_seed_maintenance_baselines(entry, reported)" in source

    def test_it_covers_all_four_roles(self) -> None:
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _seed_maintenance_baselines,
        )

        source = inspect.getsource(_seed_maintenance_baselines)

        for slot in ("filter", "brush", "side_brush", "clean_base_bag"):
            assert f'"{slot}"' in source, slot

    def test_it_falls_back_to_the_cached_state(self) -> None:
        """MQTT messages are deltas: one carrying `cleanMissionStatus`
        need not carry `bbrun`. Reading the message alone would make the
        seed depend on which fields happened to travel together."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _seed_maintenance_baselines,
        )

        source = inspect.getsource(_seed_maintenance_baselines)

        assert '_merged_top_level(entry, reported, "bbrun")' in source

    def test_zero_hours_seeds_nothing(self) -> None:
        """The original fault: seeding against hours that have not
        arrived would write a baseline of 0, which is the bug rather
        than the fix."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _seed_maintenance_baselines,
        )

        source = inspect.getsource(_seed_maintenance_baselines)

        assert "if current_hr <= 0:" in source

    def test_an_already_seeded_role_is_left_alone(self) -> None:
        """This runs on every mission message; it must not re-seed a
        baseline the user or the cloud has already set."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _seed_maintenance_baselines,
        )

        source = inspect.getsource(_seed_maintenance_baselines)

        assert '_baseline_seeded", True)' in source
        assert '_reset_history", None)' in source

    def test_the_save_is_scheduled_not_awaited(self) -> None:
        """It runs on the MQTT thread, where awaiting is not an option."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _seed_maintenance_baselines,
        )

        source = inspect.getsource(_seed_maintenance_baselines)

        assert "async_create_task" in source


# ── formerly tests/test_coverage_mid_gaps.py ────────────────────────────────────
#
# Mid-sized coverage gaps — quality scale, test-coverage (Silver).
#
# Error branches and fallbacks: bad cloud values, missing fields, foreign
# entities. Each test pins what the branch protects against.

def _store():
    from custom_components.roomba_plus.maintenance_store import MaintenanceStore

    return MaintenanceStore()


def _role():
    """A real role from the table, not a guessed name."""
    from custom_components.roomba_plus.const import CONSUMABLE_ROLES

    return next(r for r, spec in CONSUMABLE_ROLES.items() if spec.conf_key is not None)


class TestMaintenanceCloudValues:

    def test_epoch_conversion_rejects_garbage_and_zero(self):
        from custom_components.roomba_plus.maintenance_store import _iso_from_epoch

        assert _iso_from_epoch("abc") is None
        assert _iso_from_epoch(0) is None

    @pytest.mark.parametrize("record,erwartet", [
        ({"count_type": "minutes", "minutes_remaining": 600}, 10),   # older field name
        ({"count_type": "minutes"}, None),                           # neither field
        ({"count_type": "minutes", "count_remaining": "abc"}, None),  # garbage
    ])
    def test_remaining_hours(self, record, erwartet):
        s = _store()
        s.cloud_parts = {"p": {"role": "r", **record}}
        assert s.cloud_remaining_hours("r") == erwartet

    @pytest.mark.parametrize("record,erwartet", [
        ({"count_type": "minutes", "count_used": 60, "minutes_remaining": 540}, 10),
        ({"count_type": "minutes", "count_used": 60}, None),
        ({"count_type": "minutes", "count_used": "x", "count_remaining": 1}, None),
    ])
    def test_full_life_hours(self, record, erwartet):
        s = _store()
        s.cloud_parts = {"p": {"role": "r", **record}}
        assert s.cloud_full_life_hours("r") == erwartet

    @pytest.mark.parametrize("record,erwartet", [
        ({"count_type": "minutes", "count_used": 5, "minutes_remaining": 0}, (5, 0)),
        ({"count_type": "minutes", "count_used": 5}, None),
        ({"count_type": "minutes", "count_remaining": 3}, None),
        ({"count_type": "minutes", "count_used": 5, "count_remaining": "x"}, None),
        ({"count_type": "missions", "count_used": 5, "count_remaining": 3}, None),
    ])
    def test_cloud_minutes(self, record, erwartet):
        s = _store()
        s.cloud_parts = {"p": {"role": "r", **record}}
        assert s._cloud_minutes("r") == erwartet


class TestMaintenanceThresholds:

    def test_a_garbage_option_falls_back_to_the_default(self):
        from custom_components.roomba_plus.const import CONSUMABLE_ROLES

        role = _role()
        spec = CONSUMABLE_ROLES[role]
        assert _store().threshold_hours(role, {spec.conf_key: "soon"}) == spec.default_hours

    def test_an_unreadable_reset_hour_gives_the_full_budget(self):
        s = _store()
        s.filter_reset_hr = "corrupt"
        assert s._local_remaining("filter", 500, 60) == 60

    def test_learned_brush_hours_reads_the_main_brush(self, monkeypatch):
        s = _store()
        monkeypatch.setattr(s, "learned_hours", lambda role: 99 if role == "main_brush" else None)
        assert s.learned_brush_hours == 99


class TestMaintenanceHydration:
    """Cloud part records map onto local slots only when they are
    understood; anything else is recorded verbatim and nothing more."""

    def test_unusable_records_do_not_touch_a_slot(self, monkeypatch):
        from custom_components.roomba_plus import maintenance_store as ms

        role = _role()
        monkeypatch.setattr(ms, "part_role", lambda key: {"known": role, "odd": "no_such_role"}.get(key))
        s = _store()
        before = s.filter_reset_hr
        s.hydrate_from_cloud_parts([
            {"count_used": 60},                                   # no part_id
            {"part_id": "odd", "count_type": "minutes", "count_used": 60},
            {"part_id": "known", "count_type": "evacs", "count_used": 3},
            {"part_id": "known", "count_type": "minutes", "count_used": "x"},
        ], current_hr=500)
        assert "odd" in s.cloud_parts, "an unknown id is still recorded verbatim"
        assert s.filter_reset_hr == before


class TestMaintenanceLoad:

    def _with(self, monkeypatch, data):
        from custom_components.roomba_plus import maintenance_store as ms

        backing = MagicMock()
        backing.async_load = AsyncMock(return_value=data)
        monkeypatch.setattr(ms, "Store", lambda *a, **k: backing)
        return _store()

    @pytest.mark.asyncio
    async def test_a_newer_payload_is_not_misread(self, hass, monkeypatch, caplog):
        from custom_components.roomba_plus import maintenance_store as ms

        s = self._with(monkeypatch, {"payload_version": ms.PAYLOAD_VERSION + 1,
                                     "filter_reset_hr": 999})
        await s.async_load(hass, "e1")
        assert s.filter_reset_hr != 999
        assert "Not loading it" in caplog.text

    @pytest.mark.asyncio
    async def test_a_corrupt_payload_is_logged(self, hass, monkeypatch, caplog):
        s = self._with(monkeypatch, {"filter_reset_hr": "not a number"})
        await s.async_load(hass, "e1")
        assert "failed to load data" in caplog.text


# ── Due on a j-series (@msva17) ──────────────────────────────────────────
#
# The due check read `bbrun.hr`, 0 on a j7+/j9+, and found nothing due.

_J_SERIES_STATE = {
    "bbrun": {"nStuck": 12, "nPanics": 3},
    "runtimeStats": {"hr": 512, "min": 7, "sqft": 1400},
}


class TestDueOnAJSeries:
    def test_a_used_up_filter_is_due(self) -> None:
        """Was never due: the due check read 0 hours and found 60 left."""
        store = MaintenanceStore()
        store.filter_reset_hr = 400
        assert "filter" in store.due_items(_J_SERIES_STATE, {})

    def test_a_fresh_filter_is_not(self) -> None:
        store = MaintenanceStore()
        store.filter_reset_hr = 500
        assert "filter" not in store.due_items(_J_SERIES_STATE, {})


class TestLearnedLifespanIgnoresWhatIsNotALifespan:
    """@msva17, j9+, on 4.3.0: filter, edge brush and bag all read 1556 h,
    remaining and `max_hours` alike, right after their resets.

    Their histories held a reset at 0 h from the 4.2 buttons (see
    `lifetime_hours()`), and the first correct reset at 1556 h made an
    interval of the robot's whole life -- the whole median after two
    resets.
    """

    def test_an_interval_from_a_zero_hour_reset_is_not_learned(self) -> None:
        store = MaintenanceStore()
        store.reset_filter(0)
        store.reset_filter(1556)
        assert store.learned_filter_hours is None
        assert store.filter_remaining(current_hr=1556, threshold=60) == 60

    @pytest.mark.parametrize(
        ("slot", "role", "default"),
        [("side_brush", "side_brush", 150), ("clean_base_bag", "clean_base_bag", 30)],
    )
    def test_the_same_for_edge_brush_and_bag(self, slot, role, default) -> None:
        store = MaintenanceStore()
        getattr(store, f"reset_{slot}")(0)
        getattr(store, f"reset_{slot}")(1556)
        assert store.learned_hours(role) is None
        assert store.remaining_hours(role, 1556, default) == default

    def test_learning_resumes_with_the_next_real_interval(self) -> None:
        store = MaintenanceStore()
        for hours in (0, 1556, 1620):
            store.reset_filter(hours)
        assert store.learned_filter_hours == 64



class TestCareReminders:
    """4.3.2: calendar cleaning reminders from iRobot's care articles."""

    _T0 = datetime_v250_learning(2026, 10, 1, 12, 0, tzinfo=UTC)

    def _store(self, **kw):
        store = MaintenanceStore(**kw)
        store.care_since = self._T0.isoformat()
        return store

    def test_seeding_happens_once(self):
        store = MaintenanceStore()
        assert store.seed_care() is True
        first = store.care_since
        assert first is not None
        assert store.seed_care() is False
        assert store.care_since == first

    def test_seeding_fills_no_cleaning_date(self):
        """The "last cleaned" sensors must not claim a cleaning nobody did."""
        store = MaintenanceStore()
        store.seed_care()
        for slot in ("filter", "brushes", "side_brush", "wheel", "cliff_sensors", "contact", "bin"):
            assert getattr(store, f"{slot}_cleaned_at") is None

    def test_nothing_is_scheduled_before_seeding(self):
        assert MaintenanceStore().care_schedule(braava=False, pets=False) == []

    def test_intervals_without_pets(self):
        store = self._store()
        rows = {r["task"]: r for r in store.care_schedule(braava=False, pets=False, now=self._T0)}
        assert {k: r["interval_days"] for k, r in rows.items()} == {
            "filter": 7, "brushes": 7, "side_brush": 30, "wheel": 14,
            "cliff_sensors": 30, "contact": 30,
        }
        assert rows["filter"]["due"] == (self._T0 + timedelta(days=7)).isoformat()

    def test_pets_halve_filter_and_brushes_only(self):
        store = self._store()
        rows = {r["task"]: r for r in store.care_schedule(braava=False, pets=True, now=self._T0)}
        assert rows["filter"]["interval_days"] == 3.5
        assert rows["brushes"]["interval_days"] == 3.5
        assert rows["side_brush"]["interval_days"] == 30
        assert rows["wheel"]["interval_days"] == 14

    def test_a_braava_gets_cliff_sensors_and_contacts_only(self):
        tasks = [r["task"] for r in self._store().care_schedule(braava=True, pets=True, now=self._T0)]
        assert sorted(tasks) == ["cliff_sensors", "contact"]

    def test_sorted_earliest_first_and_overdue_flag(self):
        store = self._store(filter_cleaned_at=(self._T0 + timedelta(days=10)).isoformat())
        rows = store.care_schedule(braava=False, pets=False, now=self._T0 + timedelta(days=15))
        assert rows[0]["task"] == "brushes" and rows[0]["overdue"] is True
        assert [r["task"] for r in rows if r["overdue"]] == ["brushes", "wheel"]
        dues = [r["due"] for r in rows]
        assert dues == sorted(dues)

    def test_a_cleaning_from_before_the_reminders_does_not_count(self):
        """The wheels button exists since 2.7: a cleaning recorded in 2024
        must not make the wheels overdue on the day reminders start."""
        store = self._store(wheel_cleaned_at="2024-01-01T00:00:00+00:00")
        rows = {r["task"]: r for r in store.care_schedule(braava=False, pets=False, now=self._T0)}
        assert rows["wheel"]["overdue"] is False
        assert rows["wheel"]["due"] == (self._T0 + timedelta(days=14)).isoformat()
        assert rows["wheel"]["last_done"] == "2024-01-01T00:00:00+00:00"

    def test_switching_off_clears_the_start_and_keeps_the_cleanings(self):
        store = self._store(filter_cleaned_at=self._T0.isoformat())
        assert store.seed_care(False) is True
        assert store.care_since is None and store.filter_cleaned_at == self._T0.isoformat()
        assert store.seed_care(False) is False
        assert store.care_schedule(braava=False, pets=False) == []
        assert store.seed_care(True) is True and store.care_since is not None

    def test_a_recorded_cleaning_counts_from_then(self):
        store = self._store()
        later = self._T0 + timedelta(days=5)
        store.filter_cleaned_at = later.isoformat()
        rows = {r["task"]: r for r in store.care_schedule(braava=False, pets=False, now=later)}
        assert rows["filter"]["due"] == (later + timedelta(days=7)).isoformat()
        assert rows["filter"]["last_done"] == later.isoformat()

    def test_an_unparseable_date_falls_back_to_care_since(self):
        store = self._store(filter_cleaned_at="garbage")
        assert MaintenanceStore(filter_cleaned_at="2026-01-01T00:00:00+00:00").care_due(
            next(t for t in CARE_TASKS if t.slot == "filter"), False) is None
        rows = {r["task"]: r for r in store.care_schedule(braava=False, pets=False, now=self._T0)}
        assert rows["filter"]["due"] == (self._T0 + timedelta(days=7)).isoformat()

    def test_a_naive_date_is_read_in_the_local_zone(self):
        store = MaintenanceStore(care_since="2026-10-01T12:00:00")
        rows = store.care_schedule(braava=False, pets=False, now=self._T0)
        assert rows and datetime_v250_learning.fromisoformat(rows[0]["due"]).tzinfo is not None

    @pytest.mark.parametrize(("method", "field"), [
        ("reset_filter_cleaning", "filter_cleaned_at"),
        ("reset_brushes_cleaning", "brushes_cleaned_at"),
        ("reset_side_brush_cleaning", "side_brush_cleaned_at"),
        ("reset_cliff_sensors_cleaning", "cliff_sensors_cleaned_at"),
    ])
    def test_cleaning_resets_set_only_their_date(self, method, field):
        store = MaintenanceStore(filter_reset_hr=5)
        getattr(store, method)()
        assert getattr(store, field) is not None
        assert store.filter_reset_hr == 5 and store.filter_reset_history == []

    @pytest.mark.parametrize(("method", "field", "at"), [
        ("reset_filter", "filter_cleaned_at", "filter_reset_at"),
        ("reset_brush", "brushes_cleaned_at", "brush_reset_at"),
        ("reset_side_brush", "side_brush_cleaned_at", "side_brush_reset_at"),
    ])
    def test_a_new_part_is_a_clean_part(self, method, field, at):
        store = MaintenanceStore()
        getattr(store, method)(100)
        assert getattr(store, field) == getattr(store, at) is not None

    def test_a_pad_reset_records_no_brush_cleaning(self):
        store = MaintenanceStore()
        store.reset_pad(100)
        assert store.brushes_cleaned_at is None

    @pytest.mark.asyncio
    async def test_round_trip(self):
        saved = {}

        class _FakeStore:
            def __init__(self, *_a, **_k):
                pass

            async def async_save(self, data):
                saved.update(data)

            async def async_load(self):
                return dict(saved)

        store = MaintenanceStore()
        store.seed_care()
        store.reset_filter_cleaning()
        store.reset_brushes_cleaning()
        store.reset_side_brush_cleaning()
        store.reset_cliff_sensors_cleaning()
        with patch("custom_components.roomba_plus.maintenance_store.Store", _FakeStore):
            await store.async_save(MagicMock(), "e")
            loaded = MaintenanceStore()
            await loaded.async_load(MagicMock(), "e")
        for f in ("care_since", "filter_cleaned_at", "brushes_cleaned_at",
                  "side_brush_cleaned_at", "cliff_sensors_cleaned_at"):
            assert getattr(loaded, f) == getattr(store, f) is not None

    @pytest.mark.asyncio
    async def test_an_older_file_loads_without_care_dates(self):
        class _FakeStore:
            def __init__(self, *_a, **_k):
                pass

            async def async_load(self):
                return {"filter_reset_hr": 3}

        loaded = MaintenanceStore()
        with patch("custom_components.roomba_plus.maintenance_store.Store", _FakeStore):
            await loaded.async_load(MagicMock(), "e")
        assert loaded.care_since is None and loaded.filter_cleaned_at is None
        assert loaded.seed_care() is True



class TestSeriesProfileIsTheFallback:
    """4.3.2: cloud > learned > hour option > series profile > default."""

    def _store(self):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 60, "main_brush": 150}
        return store

    def test_the_profile_beats_the_default(self):
        store = self._store()
        assert store.threshold_hours("main_brush", {}) == 150
        assert store.interval_source("main_brush", {}) == "profile"

    def test_a_role_the_profile_lacks_takes_the_default(self):
        store = self._store()
        assert store.threshold_hours("side_brush", {}) == _S
        assert store.threshold_hours("clean_base_bag", {}) == _G
        assert store.interval_source("side_brush", {}) == "irobot_budget"

    def test_the_hour_option_beats_the_profile(self):
        store = self._store()
        assert store.threshold_hours("filter", {"filter_threshold_hours": 45}) == 45
        assert store.interval_source("filter", {"filter_threshold_hours": 45}) == "configured"

    def test_a_broken_option_falls_back_to_the_profile(self):
        assert self._store().threshold_hours("filter", {"filter_threshold_hours": "x"}) == 60

    def test_learned_beats_the_profile(self):
        store = self._store()
        store.brush_reset_history = [100, 300, 500]
        assert store.interval_source("main_brush", {}) == "learned"

    def test_without_a_profile_the_default(self):
        assert MaintenanceStore().threshold_hours("filter", {}) == _F

    def test_never_persisted(self):
        import inspect

        assert "profile_hours" not in inspect.getsource(MaintenanceStore.async_save)


# ── 4.5.0 — the maintenance model ────────────────────────────────────────────
#
# maintenance_model.py's tests live here: tests/ is at its file limit,
# and the model is built from this store.

from custom_components.roomba_plus import maintenance_model as mm  # noqa: E402

_NOW = datetime_v250_learning(2026, 10, 17, 12, 0, tzinfo=UTC)


def _980():
    return {"sku": "R980020", "bbrun": {"hr": 60}}


def _prime_part(part_id, *, used=None, left=None, counter=None, count_type="minutes",
                category="replacement", ts=None):
    return SimpleNamespace(
        part_id=part_id, count_used=used, count_remaining=left, counter=counter,
        count_type=count_type, counter_category=category, last_updated_ts=ts,
        reset_by="user", minutes_remaining=None,
    )


class TestMaintenanceLevels:

    @pytest.mark.parametrize("used,level", [
        (None, "ok"), (0.79, "ok"), (0.8, "soon"), (0.99, "soon"), (1.0, "due"), (1.4, "due"),
    ])
    def test_replace_levels_at_80_percent(self, used, level):
        assert mm.replace_level(used, None, _NOW, 0.8) == level

    def test_overdue_a_week_after_it_was_first_seen_due(self):
        assert mm.replace_level(1.0, _NOW - timedelta(days=6), _NOW, 0.8) == "due"
        assert mm.replace_level(1.0, _NOW - timedelta(days=7), _NOW, 0.8) == "overdue"

    @pytest.mark.parametrize("option,share", [("80", 0.8), ("60", 0.6), ("due", None), ("x", 0.8)])
    def test_the_soon_option(self, option, share):
        assert mm.soon_share({"maintenance_soon_at": option}) == share
        assert mm.replace_level(0.9, None, _NOW, share) == ("ok" if share is None else "soon")

    def test_cleaning_is_due_on_its_date_and_overdue_half_an_interval_later(self):
        due = _NOW
        assert mm.clean_level(due, 7, _NOW - timedelta(hours=1)) == "ok"
        assert mm.clean_level(due, 7, _NOW) == "due"
        assert mm.clean_level(due, 7, _NOW + timedelta(days=3.4)) == "due"
        assert mm.clean_level(due, 7, _NOW + timedelta(days=3.5)) == "overdue"

    def test_the_status_is_the_worst_entry_not_snoozed(self):
        items = [
            mm.MaintenanceItem("a", "replace", "filter", level="soon"),
            mm.MaintenanceItem("b", "replace", "bag", level="overdue",
                               snoozed_until=(_NOW + timedelta(days=1)).isoformat()),
            mm.MaintenanceItem("c", "replace", "battery",
                               level="due", snoozed_until=(_NOW - timedelta(days=1)).isoformat()),
        ]
        assert mm.overall_level(items, _NOW) == "due"
        assert mm.overall_level(items[:2], _NOW) == "soon"
        assert mm.overall_level([], _NOW) == "ok"


class TestClassicMaintenanceItems:

    def _items(self, store, state=None, options=None, **kw):
        return {i.id: i for i in mm.classic_items(
            store, state or _980(), options or {}, now=_NOW, **kw)}

    def test_a_980_counts_down_from_its_series_profile(self):
        """THE BUG: the remaining-hours sensor took the hour option or the
        global default (52 h) and ignored the series profile the due
        check used. A 980 at 60 h on a 100 h profile read 0 hours left
        and was not due."""
        store = MaintenanceStore()
        store.profile_hours = {"filter": 100}
        item = self._items(store)["replace.filter"]
        assert item.remaining == 40
        assert item.interval == 100 and item.interval_source == "profile"
        assert item.used == 0.6 and item.level == "ok"
        assert store.role_remaining_hours("filter", 60, {}) == 40

    def test_the_parts_a_980_has(self):
        items = self._items(MaintenanceStore())
        assert set(items) == {
            "replace.filter", "replace.main_brush", "replace.side_brush", "replace.battery",
        }
        assert items["replace.battery"].level == "ok"
        assert all(i.done == "local" for i in items.values())

    def test_a_braava_has_a_pad_and_neither_filter_nor_side_brush(self):
        items = self._items(MaintenanceStore(), state={"sku": "m611020", "bbrun": {"hr": 5}})
        assert set(items) == {"replace.pad", "replace.battery"}

    def test_a_clean_base_brings_the_bag(self):
        state = {**_980(), "dock": {"fwVer": "1"}}
        assert "replace.clean_base_bag" in self._items(MaintenanceStore(), state=state)

    def test_care_tasks_only_with_reminders(self):
        store = MaintenanceStore(care_since=(_NOW - timedelta(days=8)).isoformat())
        assert not any(k.startswith("clean.") for k in self._items(store))
        items = self._items(store, options={"care_reminders": True})
        assert items["clean.filter"].level == "due"
        assert items["clean.filter"].unit == "days"
        assert items["clean.side_brush"].interval == 30
        assert items["clean.side_brush"].level == "ok"

    def test_a_due_date_from_the_wear_rate(self):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 100}
        item = self._items(store, wear_rate=lambda *_: 2.0)["replace.filter"]
        assert item.due == (_NOW + timedelta(days=20)).isoformat()
        assert item.wear_rate == 2.0

    def test_the_catalogue_links_a_part_by_its_name(self):
        catalogue = {"9": {"part_name": "Main Brushes", "guide_url": "https://g", "buy_url": "https://b"}}
        item = self._items(MaintenanceStore(), catalogue=catalogue)["replace.main_brush"]
        assert (item.guide_url, item.buy_url) == ("https://g", "https://b")
        assert self._items(MaintenanceStore(), catalogue=catalogue)["replace.filter"].guide_url is None

    def test_a_cloud_counter_stays_live_between_polls(self):
        """THE BUG: the remaining hours stood at the figure of the last
        cloud read -- which, until 4.5.0, was the one at setup."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([{
            "part_id": "35", "count_type": "minutes", "count_used": 600,
            "count_remaining": 2400, "last_updated_ts": 1700000000,
        }], 100)
        item = self._items(store, state={"bbrun": {"hr": 110}})["replace.filter"]
        assert item.remaining == 30
        assert item.interval == 50 and item.interval_source == "cloud"
        assert item.used == 0.4
        assert item.part_id == "35"

    def test_with_a_cloud_counter_done_goes_to_both(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([{
            "part_id": "35", "count_type": "minutes", "count_used": 1, "count_remaining": 2,
        }], 10)
        items = self._items(store, has_cloud_parts=True)
        assert items["replace.filter"].done == "both"
        assert items["replace.main_brush"].done == "local"


class TestPrimeMaintenanceItems:

    def _items(self, parts, options=None, store=None, **kw):
        return {i.id: i for i in mm.prime_items(
            store or MaintenanceStore(), parts, options or {}, now=_NOW, **kw)}

    def test_roles_by_number_by_speaking_id_and_by_catalogue_name(self):
        items = self._items(
            {
                "67": _prime_part("67", used=10, left=90),
                "main_brush": _prime_part("main_brush", used=1, left=9),
                "202": _prime_part("202", used=0, left=0, count_type="pad_washes_used",
                                   category="maintenance"),
                "999": _prime_part("999", used=1, left=1, count_type="missions"),
            },
            catalogue={"202": {"part_name": "Mop Washing System Pad"}},
        )
        assert set(items) == {
            "replace.side_brush", "replace.main_brush", "clean.mop_washing_system",
            "replace.part_999",
        }
        assert items["clean.mop_washing_system"].part == "mop_washing_system"

    def test_the_apps_keyword_order(self):
        from custom_components.roomba_plus.prime_parts import prime_part_role

        assert prime_part_role("x", "Dock washing system filter") == "dock_washing_system"
        assert prime_part_role("x", "Pad wash bag") == "pad"
        assert prime_part_role("x", "Edge-Sweeping Brush") is None
        assert prime_part_role("212", None) is None

    def test_two_parts_sharing_a_role_keep_their_numbers(self):
        items = self._items({
            "72": _prime_part("72", used=1, left=9),
            "68": _prime_part("68", used=1, left=9),
        })
        assert set(items) == {"replace.part_72", "replace.part_68"}

    def test_counter_percent_decides_and_minutes_read_as_hours(self):
        item = self._items({"71": _prime_part("71", used=600, left=6000, counter=85)})["replace.main_brush"]
        assert item.used == 0.85 and item.level == "soon"
        assert (item.remaining, item.unit, item.interval) == (100, "hours", 110)
        assert item.done == "cloud" and item.interval_source == "cloud"

    def test_a_used_up_part_is_due_and_a_maintenance_part_never(self):
        items = self._items({
            "147": _prime_part("147", used=60, left=0, count_type="evacs"),
            "213": _prime_part("213", used=19, left=0, count_type="missions", category="maintenance"),
        })
        assert items["replace.bag"].level == "due"
        assert items["replace.bag"].unit == "evacuations"
        assert items["clean.sensor"].level == "ok"

    def test_parts_the_dock_cannot_have_are_left_out(self):
        assert self._items({"147": _prime_part("147", used=1, left=1)}, absent={"147"}) == {}

    def test_a_rate_from_the_last_reset(self):
        ts = int((_NOW - timedelta(days=10)).timestamp())
        item = self._items({"67": _prime_part("67", used=1200, left=600, ts=ts)})["replace.side_brush"]
        assert item.wear_rate == 2.0
        assert item.due == (_NOW + timedelta(days=5)).isoformat()
        assert item.last_done is not None

    def test_care_on_prime_every_two_weeks_without_what_the_robot_counts(self):
        store = MaintenanceStore(care_since=(_NOW - timedelta(days=1)).isoformat())
        items = self._items({}, options={"care_reminders": True}, store=store)
        assert items["clean.cliff_sensors"].interval == 14
        assert items["clean.side_brush"].interval == 14
        assert items["clean.contact"].interval == 14
        assert items["clean.filter"].interval == 7
        counted = self._items(
            {"213": _prime_part("213", used=1, left=1, category="maintenance")},
            options={"care_reminders": True}, store=store,
        )
        assert "clean.cliff_sensors" not in counted

    def test_a_battery_part(self):
        items = self._items({"b1": _prime_part("b1", used=100, left=400, count_type="battery")})
        assert items["replace.battery"].unit == "charge_cycles"


def _tracker(hass, *, store=None, state=None, options=None, prime=False, parts=None,
             robot=None, cloud=None):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.roomba_plus.const import DOMAIN
    from custom_components.roomba_plus.models import ConnectionType

    entry = MockConfigEntry(domain=DOMAIN, title="Robo", options=options or {})
    entry.add_to_hass(hass)
    coordinator = None
    if prime:
        coordinator = MagicMock()
        coordinator.data = parts or {}
        coordinator.async_refresh = AsyncMock()
    entry.runtime_data = SimpleNamespace(
        blid="BLID1",
        maintenance_store=store or MaintenanceStore(),
        connection_type=ConnectionType.CLOUD_ONLY if prime else ConnectionType.LOCAL_PUSH,
        roomba_reported_state=lambda: state if state is not None else _980(),
        mission_store=None,
        cloud_coordinator=cloud,
        parts_catalogue={},
        prime_parts_coordinator=coordinator,
        prime_status_coordinator=None,
        prime_robot=robot,
    )
    return mm.MaintenanceTracker(hass, entry)


def _listen(hass, kind):
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.roomba_plus.const import maintenance_event_signal

    got = []
    async_dispatcher_connect(
        hass, maintenance_event_signal("BLID1"),
        lambda event_type, payload: got.append((event_type, payload["id"])),
    )
    return got


class TestMaintenanceTracker:

    async def test_due_is_announced_once_and_overdue_a_week_later(self, hass):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 50}
        tracker = _tracker(hass, store=store)
        got = _listen(hass, "due")
        await tracker.async_refresh(_NOW)
        await hass.async_block_till_done()
        assert got == [("due", "replace.filter")]
        assert tracker.level == "due"
        await tracker.async_refresh(_NOW + timedelta(days=1))
        await hass.async_block_till_done()
        assert got == [("due", "replace.filter")], "not again on the next recompute"
        await tracker.async_refresh(_NOW + timedelta(days=7))
        await hass.async_block_till_done()
        assert got[-1] == ("overdue", "replace.filter")

    async def test_a_restart_does_not_announce_it_again(self, hass):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 50}
        await _tracker(hass, store=store).async_refresh(_NOW)
        got = _listen(hass, "due")
        await _tracker(hass, store=store).async_refresh(_NOW + timedelta(hours=1))
        await hass.async_block_till_done()
        assert got == []

    async def test_done_resets_announces_once_and_logs(self, hass):
        """A new filter is a clean filter: the cleaning is recorded too,
        and only the replacement is announced."""
        store = MaintenanceStore(care_since=(_NOW - timedelta(days=30)).isoformat())
        store.profile_hours = {"filter": 50}
        tracker = _tracker(hass, store=store, options={"care_reminders": True})
        await tracker.async_refresh()
        got = _listen(hass, "done")
        bus = []
        hass.bus.async_listen("roomba_plus_maintenance_reset", lambda e: bus.append(e.data))
        with patch(
            "custom_components.roomba_plus.maintenance_model._async_push_part_reset_to_cloud",
            AsyncMock(),
        ) as push:
            await tracker.async_done("replace.filter", via="action")
        await hass.async_block_till_done()
        assert store.filter_reset_hr == 60 and store.filter_cleaned_at is not None
        assert tracker.item("replace.filter").level == "ok"
        assert ("done", "replace.filter") in got
        assert ("done", "clean.filter") not in got
        assert store.maintenance_log[-1]["id"] == "replace.filter"
        assert store.maintenance_log[-1]["via"] == "action"
        assert bus[-1]["component"] == "filter"
        push.assert_awaited_once()

    async def test_only_cleaned_leaves_the_replacement_counter(self, hass):
        store = MaintenanceStore(care_since=_NOW.isoformat())
        store.filter_reset_hr = 7
        tracker = _tracker(hass, store=store, options={"care_reminders": True})
        await tracker.async_refresh()
        await tracker.async_done("replace.filter", "clean")
        assert store.filter_reset_hr == 7
        assert store.filter_cleaned_at is not None

    async def test_a_care_task_reported_as_its_part_replaced(self, hass):
        store = MaintenanceStore(care_since=_NOW.isoformat())
        tracker = _tracker(hass, store=store, options={"care_reminders": True})
        await tracker.async_refresh()
        with patch(
            "custom_components.roomba_plus.maintenance_model._async_push_part_reset_to_cloud",
            AsyncMock(),
        ):
            await tracker.async_done("clean.brushes", "replace")
        assert store.brush_reset_hr == 60

    async def test_what_cannot_be_done_is_refused(self, hass):
        from homeassistant.exceptions import ServiceValidationError

        store = MaintenanceStore(care_since=_NOW.isoformat())
        tracker = _tracker(hass, store=store, options={"care_reminders": True})
        await tracker.async_refresh()
        with pytest.raises(ServiceValidationError) as unknown:
            await tracker.async_done("replace.nothing")
        assert unknown.value.translation_key == "maintenance_item_unknown"
        for item_id, action in (("clean.wheel", "replace"), ("replace.battery", "clean")):
            with pytest.raises(ServiceValidationError) as refused:
                await tracker.async_done(item_id, action)
            assert refused.value.translation_key == "maintenance_action_unsupported"

    async def test_snooze_quiets_it_and_it_comes_back(self, hass):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 50}
        tracker = _tracker(hass, store=store)
        # The snooze and the level count from the wall clock, so the
        # whole test does. Starting at the fixed _NOW made the entry
        # "overdue" instead of "due" from 2026-10-21 on: a week after
        # _NOW had passed by the second refresh.
        await tracker.async_refresh(dt_util.now())
        got = _listen(hass, "snoozed")
        await tracker.async_snooze("replace.filter", 2)
        await hass.async_block_till_done()
        assert got == [("snoozed", "replace.filter")]
        assert tracker.level == "ok"
        later = dt_util.now() + timedelta(days=3)
        await tracker.async_refresh(later)
        await hass.async_block_till_done()
        assert got[-1] == ("due", "replace.filter")

    async def test_a_prime_part_is_reset_in_the_cloud(self, hass):
        robot = MagicMock()
        robot.reset_robot_parts = AsyncMock(return_value={"num_parts": 1})
        tracker = _tracker(hass, prime=True, robot=robot,
                           parts={"67": _prime_part("67", used=10, left=0)})
        await tracker.async_refresh()
        await tracker.async_done("replace.side_brush")
        robot.reset_robot_parts.assert_awaited_once_with(["67"])
        tracker.config_entry.runtime_data.prime_parts_coordinator.async_refresh.assert_awaited()

    @pytest.mark.parametrize("answer,key", [
        ({"num_parts": 0}, "maintenance_reset_not_applied"),
        ({}, "maintenance_reset_not_applied"),
        (RuntimeError("HTTP 500"), "maintenance_reset_failed"),
    ])
    async def test_a_prime_reset_the_cloud_did_not_apply_is_an_error(self, hass, answer, key):
        from homeassistant.exceptions import HomeAssistantError

        robot = MagicMock()
        if isinstance(answer, Exception):
            robot.reset_robot_parts = AsyncMock(side_effect=answer)
        else:
            robot.reset_robot_parts = AsyncMock(return_value=answer)
        tracker = _tracker(hass, prime=True, robot=robot,
                           parts={"67": _prime_part("67", used=10, left=0)})
        await tracker.async_refresh()
        with pytest.raises(HomeAssistantError) as err:
            await tracker.async_done("replace.side_brush")
        assert err.value.translation_key == key

    async def test_a_reset_in_the_iRobot_app_is_seen_as_done(self, hass):
        parts = {"67": _prime_part("67", used=10, left=0, ts=1700000000)}
        tracker = _tracker(hass, prime=True, parts=parts)
        await tracker.async_refresh()
        got = _listen(hass, "done")
        parts["67"] = _prime_part("67", used=0, left=100, ts=1700000000 + 86400)
        await tracker.async_refresh()
        await hass.async_block_till_done()
        assert got == [("done", "replace.side_brush")]
        assert tracker.config_entry.runtime_data.maintenance_store.maintenance_log[-1]["via"] == "other"

    async def test_it_follows_the_classic_cloud_poll(self, hass):
        cloud = MagicMock()
        cloud.parts = [{"part_id": "35", "count_type": "minutes",
                        "count_used": 3120, "count_remaining": 0}]
        cloud.async_add_listener = MagicMock(return_value=lambda: None)
        store = MaintenanceStore()
        tracker = _tracker(hass, store=store, cloud=cloud)
        tracker.async_start()
        listener = cloud.async_add_listener.call_args.args[0]
        listener()
        await hass.async_block_till_done()
        assert store.cloud_parts_hydrated_hr == 60
        assert tracker.item("replace.filter").level == "due"
        tracker.async_stop()
        assert tracker._unsubs == []

    async def test_diagnostics(self, hass):
        tracker = _tracker(hass)
        tracker.prime_items_now()
        diag = tracker.diagnostics()
        assert diag["level"] == "due", "a 980 at 60 h on the 52 h fallback"
        assert {i["id"] for i in diag["items"]} >= {"replace.filter"}
        assert diag["log"] == []


class TestMaintenanceStateIsKept:

    async def test_due_since_snooze_and_log_survive_a_restart(self, hass):
        store = MaintenanceStore()
        store.maintenance_state = {"replace.filter": {"due_since": _NOW.isoformat()}}
        store.maintenance_log = [{"id": "replace.filter"}] * 40
        store.cloud_parts_hydrated_hr = 12
        await store.async_save(hass, "E1")
        loaded = MaintenanceStore()
        await loaded.async_load(hass, "E1")
        assert loaded.maintenance_state == store.maintenance_state
        assert len(loaded.maintenance_log) == 30
        assert loaded.cloud_parts_hydrated_hr == 12

    def test_without_a_reading_at_hydration_the_local_baseline_decides(self):
        store = MaintenanceStore(filter_reset_hr=40)
        store.cloud_parts = {"35": {"role": "filter", "count_type": "minutes",
                                    "count_used": 6000, "count_remaining": 0}}
        assert store.used_hours("filter", 50) == 10
        assert store.role_usage("filter", 50, {})[0] == 10 / 100


class TestEntryFiguresOnThePartSensors:

    def _entity(self, tracker, sku="R980020"):
        entity = MagicMock()
        entity._config_entry.runtime_data.maintenance = tracker
        entity.vacuum_state = {"sku": sku}
        return entity

    def test_classic(self):
        from custom_components.roomba_plus.sensor_core import _maintenance_item_attributes

        item = mm.MaintenanceItem("replace.pad", "replace", "pad", level="soon", used=0.9,
                                  guide_url="https://g", buy_url="https://b")
        tracker = SimpleNamespace(item_for_part=lambda part: item if part == "pad" else None)
        attrs = _maintenance_item_attributes(self._entity(tracker, "m611020"), "main_brush")
        assert attrs["level"] == "soon" and attrs["buy_url"] == "https://b"
        assert _maintenance_item_attributes(self._entity(tracker), "main_brush") == {}
        assert _maintenance_item_attributes(self._entity(None), "filter") == {}

    def test_the_tracker_finds_an_entry_by_role_or_part_id(self, hass):
        tracker = _tracker(hass, prime=True, parts={
            "213": _prime_part("213", used=1, left=1, category="maintenance"),
            "67": _prime_part("67", used=1, left=1),
        })
        tracker.prime_items_now()
        assert tracker.item_for_part("side_brush").id == "replace.side_brush"
        assert tracker.item_for_part("213").id == "clean.sensor"
        assert tracker.item_for_part("nothing") is None


class TestErrorsPointToCareTasks:
    """4.5.0, E10 and E12: an error that points to a care task brings
    its guide into the list while it lasts; a dirt error makes the task
    due until it is done, with care reminders on."""

    def _state(self, error, cycle="clean", phase="stuck"):
        return {**_980(), "cleanMissionStatus": {"cycle": cycle, "phase": phase, "error": error}}

    def test_the_active_error(self):
        assert mm.active_error({"cycle": "clean", "phase": "stuck", "error": 2}) == 2
        assert mm.active_error({"cycle": "none", "phase": "charge", "error": 2}) is None
        assert mm.active_error({"cycle": "clean", "phase": "run", "error": 0}) is None
        assert mm.active_error({"cycle": "clean", "phase": "run", "error": "x"}) is None
        assert mm.active_error(None) is None

    def test_without_a_care_task_a_check_entry_while_it_lasts(self):
        store = MaintenanceStore()
        items = {i.id: i for i in mm.classic_items(store, self._state(14), {}, now=_NOW)}
        assert items["check.bin"].level == "due"
        assert items["check.bin"].error == 14
        assert items["check.bin"].done == "local"
        assert "check.bin" not in {
            i.id for i in mm.classic_items(store, self._state(14, "none", "charge"), {}, now=_NOW)
        }

    def test_the_entry_an_error_brings_carries_irobots_article(self, hass):
        """4.5.0b4: for the list's link and the event's notification; on
        no entry the error did not bring."""
        tracker = _tracker(hass, state=self._state(14))
        tracker.config_entry.runtime_data.help_catalogue = {"error14": "https://example.com/error-14"}
        items = {i.id: i for i in tracker.compute(_NOW)}
        assert items["check.bin"].help_url == "https://example.com/error-14"
        assert all(i.help_url is None for k, i in items.items() if k != "check.bin")

    def test_a_camera_refusal_has_nothing_to_record(self):
        """Error 68 no longer points to the camera (4.5.0b2): the Classic
        app says hold CLEAN 10 s, a reboot. A refusal for a dead camera
        (notReady 51) still does, and there is nothing to record."""
        state = {**_980(), "cleanMissionStatus": {"cycle": "none", "phase": "charge", "error": 0, "notReady": 51}}
        items = {i.id: i for i in mm.classic_items(MaintenanceStore(), state, {}, now=_NOW)}
        assert items["check.camera"].done == "none"
        assert not any(i.action == "check" for i in mm.classic_items(MaintenanceStore(), self._state(68), {}, now=_NOW))

    def test_the_bridge_follows_irobots_help_pages(self):
        """4.5.0b2: rebuilt from iRobot's pages for each code, not from our
        old action sentences."""
        assert mm.ERROR_CARE_TASKS_CLASSIC[8] == "filter"
        for code in (1, 3, 13):
            assert mm.ERROR_CARE_TASKS_CLASSIC[code] == "wheel"
        for code in (6, 25):
            assert mm.ERROR_CARE_TASKS_CLASSIC[code] == "cliff_sensors"
        assert mm.ERROR_CARE_TASKS_CLASSIC[121] == "contact"
        for code in (11, 20, 27, 38, 68, 78, 79, 102, 103):
            assert code not in mm.ERROR_CARE_TASKS_CLASSIC, code

    def test_a_blocked_path_does_not_send_anyone_to_the_camera(self):
        """4.5.0b4, checked and left out: 17 is "Path blocked". Eight of
        the ten steps on iRobot's page are about the room, the app names
        no part, and a robot without a camera would be told to clean one.
        iRobot's article comes with the error instead (help_url)."""
        assert 17 not in mm.ERROR_CARE_TASKS_CLASSIC
        assert not any(i.action == "check" for i in mm.classic_items(MaintenanceStore(), self._state(17), {}, now=_NOW))

    def test_with_the_task_its_own_entry_carries_the_error(self):
        """Never two entries for one task."""
        store = MaintenanceStore(care_since=_NOW.isoformat())
        items = mm.classic_items(store, self._state(4), {"care_reminders": True}, now=_NOW)
        wheel = [i for i in items if i.part == "wheel"]
        assert len(wheel) == 1 and wheel[0].id == "clean.wheel"
        assert wheel[0].level == "due" and wheel[0].error == 4

    def test_an_error_without_a_task_is_ignored_and_the_option_can_switch_it_off(self):
        store = MaintenanceStore()
        assert not any(i.action == "check" for i in mm.classic_items(store, self._state(99), {}, now=_NOW))
        assert not any(
            i.action == "check"
            for i in mm.classic_items(store, self._state(14), {"care_guide_on_error": "off"}, now=_NOW)
        )

    def test_prime_the_codes_irobots_prime_pages_name_a_part_for(self):
        """D3 (19.10.2026): 4/5, 14, 26, 104/105/119/121 and 517 join 2, 6,
        201, 1000, 1001 on the strength of iRobot's Prime help pages."""
        store = MaintenanceStore()
        assert {i.id for i in mm.prime_items(store, {}, {}, error=1001, now=_NOW)} == {"check.side_brush"}
        assert {i.id for i in mm.prime_items(store, {}, {}, error=26, now=_NOW)} == {"check.filter"}
        assert {i.id for i in mm.prime_items(store, {}, {}, error=104, now=_NOW)} == {"check.contact"}
        assert mm.prime_items(store, {}, {}, error=48, now=_NOW) == []
        counted = mm.prime_items(
            store, {"213": _prime_part("213", used=1, left=1, category="maintenance")}, {},
            error=201, now=_NOW,
        )
        assert [(i.id, i.error, i.level) for i in counted] == [("clean.sensor", 201, "due")]

    async def test_a_dirt_error_makes_the_task_due_until_it_is_done(self, hass):
        store = MaintenanceStore(care_since=_NOW.isoformat())
        state = self._state(2)
        tracker = _tracker(hass, store=store, state=state, options={"care_reminders": True})
        await tracker.async_refresh(_NOW)
        assert tracker.item("clean.brushes").level == "due"
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 2}
        await tracker.async_refresh(_NOW + timedelta(hours=1))
        brushes = tracker.item("clean.brushes")
        assert brushes.level == "due" and brushes.error is None, "stays due after the error"
        await tracker.async_done("clean.brushes")
        assert tracker.item("clean.brushes").level == "ok"

    async def test_a_wheel_error_goes_with_the_error(self, hass):
        store = MaintenanceStore(care_since=_NOW.isoformat())
        state = self._state(4)
        tracker = _tracker(hass, store=store, state=state, options={"care_reminders": True})
        await tracker.async_refresh(_NOW)
        assert tracker.item("clean.wheel").level == "due"
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 4}
        await tracker.async_refresh(_NOW + timedelta(hours=1))
        assert tracker.item("clean.wheel").level == "ok"

    async def test_stuck_near_a_cliff_goes_with_the_error(self, hass):
        """Classic 6 and 25 point to the cliff sensors (iRobot pages 8960,
        8974) but say the robot is stuck near an edge -- most often a real
        one. The guide shows; the cleaning is not made due (4.5.0b2)."""
        store = MaintenanceStore(care_since=_NOW.isoformat())
        state = self._state(6)
        tracker = _tracker(hass, store=store, state=state, options={"care_reminders": True})
        await tracker.async_refresh(_NOW)
        assert tracker.item("clean.cliff_sensors").error == 6
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 6}
        await tracker.async_refresh(_NOW + timedelta(hours=1))
        assert tracker.item("clean.cliff_sensors").level == "ok"
        assert "forced_due" not in store.maintenance_state.get("clean.cliff_sensors", {})

    async def test_done_while_the_error_lasts_keeps_it_out_until_it_returns(self, hass):
        state = self._state(7)
        tracker = _tracker(hass, state=state)
        got = _listen(hass, "x")
        await tracker.async_refresh()
        await tracker.async_done("check.wheel")
        await hass.async_block_till_done()
        assert tracker.item("check.wheel") is None
        assert ("done", "check.wheel") in got
        store = tracker.config_entry.runtime_data.maintenance_store
        assert store.maintenance_log[-1]["id"] == "check.wheel"
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 0}
        await tracker.async_refresh()
        state["cleanMissionStatus"] = {"cycle": "clean", "phase": "stuck", "error": 7}
        await tracker.async_refresh()
        assert tracker.item("check.wheel") is not None

    async def test_a_check_with_a_cleaning_records_it_with_reminders_on(self, hass):
        store = MaintenanceStore(care_since=_NOW.isoformat())
        tracker = _tracker(hass, store=store, state=self._state(14), options={"care_reminders": True})
        await tracker.async_refresh()
        await tracker.async_done("check.bin", "clean")
        assert store.bin_cleaned_at is not None
        assert tracker.item("check.bin") is None

    async def test_an_error_that_comes_back_soon_is_not_announced_again(self, hass):
        state = self._state(14)
        tracker = _tracker(hass, state=state)
        got = _listen(hass, "x")
        await tracker.async_refresh(_NOW)
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 0}
        await tracker.async_refresh(_NOW + timedelta(minutes=5))
        state["cleanMissionStatus"] = {"cycle": "clean", "phase": "stuck", "error": 14}
        await tracker.async_refresh(_NOW + timedelta(minutes=10))
        await hass.async_block_till_done()
        assert [g for g in got if g[1] == "check.bin"] == [("due", "check.bin")]
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 0}
        await tracker.async_refresh(_NOW + timedelta(hours=1))
        await tracker.async_refresh(_NOW + timedelta(hours=14))
        state["cleanMissionStatus"] = {"cycle": "clean", "phase": "stuck", "error": 14}
        await tracker.async_refresh(_NOW + timedelta(hours=15))
        await hass.async_block_till_done()
        assert [g for g in got if g[1] == "check.bin"] == [("due", "check.bin")] * 2

    async def test_the_prime_error_comes_from_its_shadow(self, hass):
        tracker = _tracker(hass, prime=True)
        tracker.config_entry.runtime_data.prime_status_coordinator = SimpleNamespace(
            data={"ro-currentstate": {"cleanMissionStatus": {"cycle": "clean", "phase": "stuck", "error": 2}}},
        )
        assert tracker.current_error() == 2
        tracker.config_entry.runtime_data.prime_status_coordinator = SimpleNamespace(data=None)
        assert tracker.current_error() is None


class TestWaitForTheCloudsFirstFullReport:
    """@msva17 (j9+, #200): right after a filter reset the cloud's counter
    came back without its remaining minutes for a while, and the
    integration fell back -- the filter read near zero and Maintenance
    due came on. An incomplete counter says nothing; the baseline waits
    for the cloud's first full report."""

    def _part(self, used, remaining, ts):
        return {"part_id": "46", "count_type": "minutes", "count_used": used,
                "count_remaining": remaining, "last_updated_ts": ts}

    def test_a_reset_is_not_undone_by_a_half_report(self):
        import time

        store = MaintenanceStore()
        store.profile_hours = {"filter": 52}
        store.reset_filter(300)
        store.hydrate_from_cloud_parts([self._part(3120, None, int(time.time()) + 5)], 301)
        assert store.filter_reset_hr == 300
        assert "filter" not in store.due_items({"runtimeStats": {"hr": 301}}, {})
        assert store.role_remaining_hours("filter", 301, {}) == 51

    def test_the_full_report_is_taken(self):
        import time

        store = MaintenanceStore()
        store.reset_filter(300)
        store.hydrate_from_cloud_parts([self._part(60, 3060, int(time.time()) + 5)], 302)
        assert store.filter_reset_hr == 301
        assert store.role_remaining_hours("filter", 302, {}) == 51


class TestForgetALearnedInterval:
    """@msva17 (#200): a learned interval can be wrong for reasons the
    read-side filter cannot see -- a reset pressed by mistake, a part
    swapped early -- and the only way back was editing the storage file."""

    def test_the_store_forgets_and_keeps_the_baseline(self):
        store = MaintenanceStore(filter_reset_hr=300)
        store.filter_reset_history = [100, 120, 300]
        assert store.interval_source("filter", {}) == "learned"
        assert store.forget_learned("filter") is True
        assert store.filter_reset_history == []
        assert store.filter_reset_hr == 300
        assert store.interval_source("filter", {}) == "irobot_budget"
        assert store.forget_learned("filter") is False

    async def test_the_tracker_for_one_part_or_all(self, hass):
        store = MaintenanceStore()
        store.filter_reset_history = [100, 200, 300]
        store.brush_reset_history = [10, 400, 900]
        tracker = _tracker(hass, store=store)
        await tracker.async_refresh()
        assert await tracker.async_forget_learned(["replace.filter"]) == ["replace.filter"]
        assert store.brush_reset_history == [10, 400, 900]
        assert await tracker.async_forget_learned() == ["replace.main_brush"]
        assert await tracker.async_forget_learned() == []

    async def test_a_braava_pad_and_what_cannot_be_forgotten(self, hass):
        from homeassistant.exceptions import ServiceValidationError

        store = MaintenanceStore()
        store.brush_reset_history = [10, 40, 90]
        tracker = _tracker(hass, store=store, state={"sku": "m611020", "bbrun": {"hr": 5}})
        await tracker.async_refresh()
        assert await tracker.async_forget_learned(["replace.pad"]) == ["replace.pad"]
        with pytest.raises(ServiceValidationError):
            await tracker.async_forget_learned(["replace.battery"])


class TestRodelerosFilter:
    """@rodelero1, i7: resets an hour apart taught the filter a life of
    1 h, and the filter fitted after it read 2 h left and due soon after."""

    def test_his_history_learns_nothing(self):
        store = MaintenanceStore()
        for hours in (300, 301, 305):
            store.reset_filter(hours)
        assert store.learned_filter_hours is None
        assert store.role_usage("filter", 305, {})[1:] == (52.0, 52.0, "irobot_budget")

    def test_a_short_gap_does_not_hide_the_real_ones(self):
        store = MaintenanceStore()
        for hours in (300, 301, 360, 421):
            store.reset_filter(hours)
        assert store.learned_filter_hours == 60

    def test_the_bound_follows_the_series(self):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 60}
        store.filter_reset_history = [100, 114]
        assert store.learned_filter_hours is None
        store.filter_reset_history = [100, 115]
        assert store.learned_filter_hours == 15


class TestTankdeersRestart:
    """@tankdeer, 600: the filter ran down to 59 h and read 60 again after
    a restart, because the robot does not send its hours again after a
    reconnect and 0 was counted as the meter."""

    async def test_the_last_reading_survives_a_restart(self, hass):
        store = MaintenanceStore(filter_reset_hr=290, filter_baseline_seeded=True)
        store.profile_hours = {"filter": 60}
        assert store.note_hours(291) is True
        assert store.note_hours(291) is False, "the same reading saves nothing"
        await store.async_save(hass, "e1")

        after = MaintenanceStore()
        await after.async_load(hass, "e1")
        after.profile_hours = {"filter": 60}
        assert after.role_remaining_hours("filter", 0, {}) == 59
        assert after.role_usage("filter", 0, {})[0] == pytest.approx(1 / 60)
        assert after.role_remaining_hours("filter", 292, {}) == 58

    def test_a_reset_without_a_reading_is_not_recorded_at_hour_0(self):
        store = MaintenanceStore(filter_reset_hr=200)
        store.note_hours(291)
        store.reset_filter(0)
        assert store.filter_reset_hr == 291
        assert store.filter_reset_history == [291]

    def test_a_lower_reading_is_used_as_it_is(self):
        """A firmware that resets the meter; the repair names it."""
        store = MaintenanceStore()
        store.note_hours(291)
        assert store.hours(5) == 5


class TestCareGuidesOnTheEntries:
    """4.5.0, a4: every entry names its care guide, the event carries
    the signed link, and the robot's error recomputes at once."""

    async def test_entries_name_their_guide_and_the_event_links_it(self, hass):
        from custom_components.roomba_plus.care_guides import _DATA_KEY, CareGuides
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        from custom_components.roomba_plus.const import maintenance_event_signal

        hass.data[_DATA_KEY] = CareGuides(
            b"k" * 32, {"en": {"replace_filter": {"title": "Replace the filter"}}}, {},
        )
        store = MaintenanceStore()
        store.profile_hours = {"filter": 50}
        tracker = _tracker(hass, store=store)
        payloads = []
        async_dispatcher_connect(
            hass, maintenance_event_signal("BLID1"), lambda _t, p: payloads.append(p),
        )
        await tracker.async_refresh(_NOW)
        await hass.async_block_till_done()
        assert tracker.item("replace.filter").guide_id == "replace_filter"
        assert tracker.item("replace.battery").guide_id == "replace_battery"
        entry_id = tracker.config_entry.entry_id
        assert payloads[0]["guide_id"] == "replace_filter"
        assert payloads[0]["guide_title"] == "Replace the filter"
        assert payloads[0]["guide"].startswith(
            f"/api/roomba_plus/care/{entry_id}/replace_filter?lang=en&exp="
        )

    def test_the_errors_care_task(self, hass):
        state = {**_980(), "cleanMissionStatus": {"cycle": "clean", "phase": "stuck", "error": 26}}
        assert _tracker(hass, state=state).care_cause_task() == "filter"
        assert _tracker(hass).care_cause_task() is None

    async def test_a_new_error_recomputes_at_once_and_only_then(self, hass):
        state = _980()
        tracker = _tracker(hass, state=state)
        scheduled = []
        tracker._schedule = lambda: scheduled.append(1)
        tracker._seen_error = None
        tracker._on_status()
        assert scheduled == []
        state["cleanMissionStatus"] = {"cycle": "clean", "phase": "stuck", "error": 14}
        tracker._on_status()
        tracker._on_status()
        assert scheduled == [1]

    async def test_a_classic_robots_messages_are_listened_to(self, hass):
        tracker = _tracker(hass)
        unsub = MagicMock()
        roomba = MagicMock()
        roomba.register_on_message_callback.return_value = unsub
        tracker.config_entry.runtime_data.roomba = roomba
        tracker.async_start()
        roomba.register_on_message_callback.assert_called_once_with(tracker._on_robot_message)
        tracker.async_stop()
        unsub.assert_called_once()
        await hass.async_block_till_done()


class TestMaintenanceRepairIssues:
    """4.5.0, D1 and D2: both off by default."""

    def _issues(self, hass):
        from homeassistant.helpers import issue_registry as ir

        return sorted(
            issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == "roomba_plus"
        )

    def _overdue_tracker(self, hass, **options):
        store = MaintenanceStore()
        store.profile_hours = {"filter": 50}
        return store, _tracker(hass, store=store, options=options)

    async def test_an_overdue_part_raises_one_issue_only_with_the_option(self, hass):
        store, tracker = self._overdue_tracker(hass)
        await tracker.async_refresh(_NOW)
        await tracker.async_refresh(_NOW + timedelta(days=7))
        assert tracker.item("replace.filter").level == "overdue"
        assert self._issues(hass) == []

        store, tracker = self._overdue_tracker(hass, maintenance_overdue_repairs=True)
        await tracker.async_refresh(_NOW)
        assert self._issues(hass) == [], "due is not overdue"
        await tracker.async_refresh(_NOW + timedelta(days=7))
        entry_id = tracker.config_entry.entry_id
        assert self._issues(hass) == [f"maintenance_overdue_{entry_id}_replace.filter"]

    async def test_snoozed_and_never_take_it_away_and_switching_off_forgets_never(self, hass):
        from homeassistant.helpers import issue_registry as ir

        store, tracker = self._overdue_tracker(hass, maintenance_overdue_repairs=True)
        await tracker.async_refresh(_NOW)
        await tracker.async_refresh(_NOW + timedelta(days=7))
        issue = ir.async_get(hass).async_get_issue(
            "roomba_plus", f"maintenance_overdue_{tracker.config_entry.entry_id}_replace.filter",
        )
        assert issue.is_fixable and issue.translation_key == "maintenance_part_overdue"
        assert issue.translation_placeholders["name"] == "Robo"
        assert issue.data == {
            "entry_id": tracker.config_entry.entry_id, "item_id": "replace.filter",
        }

        store.maintenance_state["replace.filter"]["snoozed_until"] = (
            _NOW + timedelta(days=14)
        ).isoformat()
        await tracker.async_refresh(_NOW + timedelta(days=8))
        assert self._issues(hass) == [], "snoozed"
        store.maintenance_state["replace.filter"].pop("snoozed_until")
        await tracker.async_refresh(_NOW + timedelta(days=8))
        assert len(self._issues(hass)) == 1

        await tracker.async_never_repair("replace.filter")
        assert store.maintenance_state["replace.filter"]["no_repair"] is True
        assert self._issues(hass) == []
        await tracker.async_refresh(_NOW + timedelta(days=8))
        assert self._issues(hass) == []

        hass.config_entries.async_update_entry(tracker.config_entry, options={})
        await tracker.async_refresh(_NOW + timedelta(days=8))
        assert "no_repair" not in store.maintenance_state["replace.filter"]

    async def test_an_error_issue_while_it_lasts_with_repairs_chosen(self, hass):
        state = {**_980(), "cleanMissionStatus": {"cycle": "clean", "phase": "stuck", "error": 14}}
        tracker = _tracker(hass, state=state, options={"care_guide_on_error": "repairs"})
        entry_id = tracker.config_entry.entry_id
        await tracker.async_refresh(_NOW)
        assert self._issues(hass) == [f"maintenance_error_care_{entry_id}_bin"]
        assert tracker.item("check.bin") is None, "the issue instead of the list item"

        await tracker.async_close_error_issue("bin")
        assert self._issues(hass) == []
        await tracker.async_refresh(_NOW + timedelta(minutes=1))
        assert self._issues(hass) == [], "closed until another error"

        state["cleanMissionStatus"] = {"cycle": "clean", "phase": "stuck", "error": 26}
        await tracker.async_refresh(_NOW + timedelta(minutes=2))
        assert self._issues(hass) == [f"maintenance_error_care_{entry_id}_filter"]
        state["cleanMissionStatus"] = {"cycle": "none", "phase": "charge", "error": 0}
        await tracker.async_refresh(_NOW + timedelta(minutes=3))
        assert self._issues(hass) == []

    async def test_no_error_issue_with_the_list_or_off(self, hass):
        state = {**_980(), "cleanMissionStatus": {"cycle": "clean", "phase": "stuck", "error": 14}}
        for option in ("list", "off"):
            tracker = _tracker(hass, state=state, options={"care_guide_on_error": option})
            await tracker.async_refresh(_NOW)
        assert self._issues(hass) == []

    async def test_cleaned_from_the_issue_keeps_it_away_while_the_error_lasts(self, hass):
        state = {**_980(), "cleanMissionStatus": {"cycle": "clean", "phase": "stuck", "error": 26}}
        store = MaintenanceStore(care_since=_NOW.isoformat())
        tracker = _tracker(
            hass, store=store, state=state,
            options={"care_guide_on_error": "repairs", "care_reminders": True},
        )
        await tracker.async_refresh(_NOW)
        assert len(self._issues(hass)) == 1
        await tracker.async_done("clean.filter", via="repairs")
        assert self._issues(hass) == []

    def test_what_only_cleaned_needs(self, hass):
        tracker = _tracker(hass)
        tracker._items = [
            mm.MaintenanceItem("replace.filter", "replace", "filter"),
            mm.MaintenanceItem("replace.clean_base_bag", "replace", "clean_base_bag"),
            mm.MaintenanceItem("check.camera", "check", "camera", done="none"),
        ]
        assert not tracker.can_mark_cleaned(tracker._items[0]), "no cleaning entry"
        tracker._items.append(mm.MaintenanceItem("clean.filter", "clean", "filter"))
        assert tracker.can_mark_cleaned(tracker._items[0])
        assert not tracker.can_mark_cleaned(tracker._items[1])
        assert not tracker.can_mark_cleaned(tracker._items[2])


class TestRefusalsAndAFullBinPointToCareTasks:
    """4.5.0 b2 (concept 5a, E11): a refusal to start or a full bin
    brings the guide like an error does -- the robot stands, and nothing
    says why."""

    def _state(self, not_ready=0, full=None, clean_base=False, error=0):
        state = {**_980(), "cleanMissionStatus": {
            "cycle": "none", "phase": "charge", "error": error, "notReady": not_ready,
        }}
        if full is not None:
            state["bin"] = {"present": True, "full": full}
        if clean_base:
            state["dock"] = {"fwVer": "1"}
        return state

    @pytest.mark.parametrize("wire,reason,task", [
        (1, "cliff", "cliff_sensors"), (6, "brush_stall", "brushes"),
        (7, "no_bin", "bin"), (16, "bin_full", "bin"), (51, "camera", "camera"),
    ])
    def test_a_refusal(self, wire, reason, task):
        assert mm.care_cause(self._state(not_ready=wire)) == reason
        assert mm.cause_task(reason, prime=False) == task

    def test_a_full_bin_only_without_a_clean_base(self):
        assert mm.care_cause(self._state(full=True)) == "bin_full"
        assert mm.care_cause(self._state(full=True, clean_base=True)) is None
        assert mm.care_cause(self._state(full=False)) is None
        assert mm.care_cause(self._state(not_ready=15)) is None, "low battery is no care task"

    def test_an_error_comes_first(self):
        state = self._state(not_ready=16)
        state["cleanMissionStatus"].update(cycle="clean", phase="stuck", error=26)
        assert mm.care_cause(state) == 26

    def test_the_check_entry_carries_the_reason_not_an_error(self):
        items = {i.id: i for i in mm.classic_items(
            MaintenanceStore(), self._state(full=True), {}, now=_NOW)}
        assert items["check.bin"].reason == "bin_full"
        assert items["check.bin"].error is None

    async def test_done_keeps_it_away_while_it_lasts_and_no_forced_due(self, hass):
        state = self._state(not_ready=6)
        store = MaintenanceStore(care_since=_NOW.isoformat())
        tracker = _tracker(hass, store=store, state=state, options={"care_reminders": True})
        await tracker.async_refresh(_NOW)
        brushes = tracker.item("clean.brushes")
        assert brushes.reason == "brush_stall" and brushes.level == "due"
        assert "forced_due" not in store.maintenance_state.get("clean.brushes", {}), (
            "a stuck brush is not a dirt error"
        )
        assert tracker.care_cause_task() == "brushes"
        await tracker.async_done("clean.brushes")
        assert store.maintenance_state["clean.brushes"]["error_dismissed"] == "brush_stall"
        assert tracker.item("clean.brushes").reason is None

    async def test_the_repair_issue_names_the_reason(self, hass):
        from homeassistant.helpers import issue_registry as ir

        tracker = _tracker(hass, state=self._state(full=True),
                           options={"care_guide_on_error": "repairs"})
        await tracker.async_refresh(_NOW)
        issue = ir.async_get(hass).async_get_issue(
            "roomba_plus", f"maintenance_error_care_{tracker.config_entry.entry_id}_bin",
        )
        assert issue.translation_placeholders["reason"] == "bin_full"
        assert issue.translation_placeholders["code"] == ""

    async def test_the_event_says_whether_only_cleaned_can_be_recorded(self, hass):
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        from custom_components.roomba_plus.const import maintenance_event_signal

        store = MaintenanceStore(care_since=(_NOW - timedelta(days=8)).isoformat())
        store.profile_hours = {"filter": 50, "main_brush": 50}
        tracker = _tracker(hass, store=store, options={"care_reminders": True})
        got = {}
        async_dispatcher_connect(
            hass, maintenance_event_signal("BLID1"), lambda _t, p: got.setdefault(p["id"], p),
        )
        await tracker.async_refresh(_NOW)
        await hass.async_block_till_done()
        assert got["replace.filter"]["can_clean"] is True
        assert got["clean.filter"]["can_clean"] is False
