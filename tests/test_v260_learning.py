"""Tests for v2.6.0 new features.

Covers:
  - RobotProfileStore (L4): load/save/reset/update_room_dirt_index/update_coverage_baseline
  - MissionTimerStore (MP1): on_phase_run accumulation, clear, mission_id switch
  - ALG1: record_clean_event stores (datetime, was_all_away) tuple
  - ALG1: presence_windows() scores by away fraction, not EMA
  - ALG2: window_is_today property
  - ALG3: gate_blocked() on DirtThresholdManager
  - MS1: demand_triggered_ts type guard (isinstance float)
  - IA74-LP: learning_percentage property on coordinator
  - IA74-ZONE: zone_counts property on coordinator
  - IA74-MAINT: wheel/contact/bin fields in MaintenanceStore
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


# ── L4 — RobotProfileStore ────────────────────────────────────────────────────

class TestRobotProfileStore:

    def test_update_room_dirt_index_first_observation(self):
        """First observation seeds the EMA directly."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", pass_count=2, area_m2=10.0)
        assert "19" in rps.room_dirt_index
        assert abs(rps.room_dirt_index["19"] - 0.2) < 0.001

    def test_update_room_dirt_index_ema(self):
        """Subsequent observations apply EMA (α=0.2)."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", pass_count=2, area_m2=10.0)   # seed → 0.2
        rps.update_room_dirt_index("19", pass_count=4, area_m2=10.0)   # 0.2 × 0.2 + 0.8 × 0.4 → 0.36
        result = rps.room_dirt_index["19"]
        assert abs(result - 0.24) < 0.01  # α=0.2*0.4 + 0.8*0.2 = 0.24

    def test_update_room_dirt_index_zero_area_skipped(self):
        """Zero area rooms are silently skipped."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", pass_count=2, area_m2=0.0)
        assert "19" not in rps.room_dirt_index

    def test_room_dirt_relative_single_room_empty(self):
        """Fewer than 2 rooms → empty dict (no relative baseline possible)."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", pass_count=2, area_m2=10.0)
        assert rps.room_dirt_relative() == {}

    def test_room_dirt_relative_two_rooms(self):
        """Two rooms: relative scores sum to 2.0 (both around 1.0)."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", pass_count=2, area_m2=10.0)  # 0.2
        rps.update_room_dirt_index("21", pass_count=4, area_m2=10.0)  # 0.4
        rel = rps.room_dirt_relative()
        assert set(rel.keys()) == {"19", "21"}
        assert abs(sum(rel.values()) - 2.0) < 0.01

    def test_update_coverage_baseline_initial(self):
        """First call seeds the baseline."""
        rps = RobotProfileStore()
        rps.update_coverage_baseline(0.75)
        assert rps.coverage_baseline == 0.75
        assert rps.coverage_mission_count == 1

    def test_update_coverage_baseline_running_mean(self):
        """Running mean converges toward input values."""
        rps = RobotProfileStore()
        for _ in range(10):
            rps.update_coverage_baseline(0.80)
        assert abs(rps.coverage_baseline - 0.80) < 0.001
        assert rps.coverage_mission_count == 10

    def test_coverage_baseline_not_ready_below_threshold(self):
        """coverage_baseline_ready is False until 20 missions."""
        rps = RobotProfileStore()
        for i in range(19):
            rps.update_coverage_baseline(0.75)
        assert not rps.coverage_baseline_ready

    def test_coverage_baseline_ready_at_threshold(self):
        """coverage_baseline_ready is True at exactly 20 missions."""
        rps = RobotProfileStore()
        for _ in range(20):
            rps.update_coverage_baseline(0.75)
        assert rps.coverage_baseline_ready

    async def test_async_reset_clears_all_fields(self):
        """reset() wipes every learned field and saves."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", 2, 10.0)
        rps.update_coverage_baseline(0.8)
        rps.learned_filter_hours = 60.0

        hass = _make_hass()
        with patch.object(rps, "async_save", new_callable=AsyncMock) as mock_save:
            await rps.async_reset(hass, "entry123")

        assert rps.room_dirt_index == {}
        assert rps.coverage_baseline is None
        assert rps.learned_filter_hours is None
        mock_save.assert_awaited_once()

    async def test_load_save_roundtrip(self):
        """Data survives a save→load cycle."""
        rps = RobotProfileStore()
        rps.update_room_dirt_index("19", 2, 10.0)
        rps.update_coverage_baseline(0.75)
        rps.learned_filter_hours = 50.0

        saved_data: dict = {}

        async def mock_save_fn(data: dict) -> None:
            saved_data.update(data)

        async def mock_load_fn() -> dict:
            return saved_data

        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = mock_save_fn
        store_mock.async_load = mock_load_fn

        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await rps.async_save(hass, "entry123")
            rps2 = RobotProfileStore()
            await rps2.async_load(hass, "entry123")

        assert abs(rps2.room_dirt_index.get("19", 0) - rps.room_dirt_index["19"]) < 0.001
        assert rps2.coverage_baseline == 0.75
        assert rps2.learned_filter_hours == 50.0


# ── MP1 — MissionTimerStore ───────────────────────────────────────────────────

class TestMissionTimerStore:

    def test_first_phase_run_initialises_mission(self):
        """First on_phase_run for a new mission_id resets and sets up."""
        mts = MissionTimerStore()
        hass = _make_hass()
        mts.on_phase_run("m_111", hass, "entry1")
        assert mts.mission_id == "m_111"
        assert mts.run_sec == 0  # no delta yet — just initialised

    def test_accumulates_delta_on_consecutive_calls(self):
        """Consecutive phase-run calls accumulate elapsed seconds."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 0
        mts._last_phase_ts = 90.0   # pre-set so delta=10 when monotonic()=100
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_timer_store.time") as mock_time:
            mock_time.monotonic.return_value = 100.0   # now=100, delta=100-90=10
            mts.on_phase_run("m_111", hass, "entry1")
        assert mts.run_sec == 10

    def test_large_gap_clamped(self):
        """Gaps > 120 s (HA restart, recharge) are not accumulated."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts._last_phase_ts = 0.0
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_timer_store.time") as mock_time:
            mock_time.monotonic.return_value = 500.0
            mts._last_phase_ts = 100.0  # 400 s gap → clamped
            mts.on_phase_run("m_111", hass, "entry1")
        assert mts.run_sec == 0

    def test_new_mission_id_resets_counter(self):
        """Different mission_id triggers a full reset."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 300
        hass = _make_hass()
        mts.on_phase_run("m_222", hass, "entry1")
        assert mts.mission_id == "m_222"
        assert mts.run_sec == 0

    def test_on_phase_other_resets_timestamp(self):
        """on_phase_other() resets _last_phase_ts to prevent gap accumulation."""
        mts = MissionTimerStore()
        mts._last_phase_ts = 100.0
        mts.on_phase_other()
        assert mts._last_phase_ts == 0.0

    def test_clear_resets_all_fields(self):
        """clear() wipes mission_id, run_sec, and timestamp."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 200
        mts._last_phase_ts = 50.0
        hass = _make_hass()
        mts.clear(hass, "entry1")
        assert mts.mission_id is None
        assert mts.run_sec == 0
        assert mts._last_phase_ts == 0.0


# ── ALG1 — presence_windows away tracking ─────────────────────────────────────

class TestPresenceWindowsALG1:

    def _make_manager(self, person_ids: list[str], states: dict[str, str]):
        """Build a PresenceManager with mocked hass.states."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        from custom_components.roomba_plus.const import CONF_PRESENCE_ENTITIES

        entry = MagicMock()
        entry.options = {CONF_PRESENCE_ENTITIES: person_ids}
        entry.runtime_data = MagicMock()

        hass = MagicMock()
        hass.states.get = lambda eid: (
            MagicMock(state=states.get(eid, "home")) if eid in states else None
        )
        return PresenceManager(hass, entry)

    def test_record_clean_event_stores_tuple(self):
        """record_clean_event stores (datetime, was_all_away) tuples."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        dt = _utcnow()
        pm.record_clean_event(dt)
        all_events = [item for items in pm._clean_events.values() for item in items]
        assert len(all_events) == 1
        item = all_events[0]
        assert isinstance(item, tuple)
        assert isinstance(item[0], datetime)
        assert item[1] is True  # person was away

    def test_record_clean_event_away_false_when_home(self):
        """was_all_away=False when a person is home."""
        pm = self._make_manager(["person.alice"], {"person.alice": "home"})
        pm.record_clean_event(_utcnow())
        events = [i for items in pm._clean_events.values() for i in items]
        assert events[0][1] is False

    def test_presence_windows_scores_away_fraction(self):
        """presence_windows returns away_count/total_count per slot."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        # Seed 5 events: 3 away, 2 home — all in same slot
        now = _utcnow()
        slot_dt = now.replace(hour=9)
        from homeassistant.util import dt as dt_util
        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            mock_dt.as_local.return_value = slot_dt
            mock_dt.now.return_value = now
            for away in [True, True, True, False, False]:
                pm._clean_events[(slot_dt.weekday(), 9)].append((slot_dt, away))

        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            windows = pm.presence_windows()

        # Should have one slot with score 3/5 = 0.6
        assert len(windows) == 1
        score = list(windows.values())[0]
        assert abs(score - 0.6) < 0.01

    def test_presence_windows_slot_with_fewer_than_3_excluded(self):
        """Slots with < 3 recent events are excluded from windows."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        now = _utcnow()
        # Only 2 events — should be excluded
        slot_dt = now.replace(hour=10)
        for _ in range(2):
            pm._clean_events[(slot_dt.weekday(), 10)].append((slot_dt, True))

        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            windows = pm.presence_windows()

        assert windows == {}

    def test_presence_windows_empty_below_5_total(self):
        """Returns {} when fewer than 5 total events recorded."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        now = _utcnow()
        for _ in range(4):
            pm._clean_events[(0, 9)].append((_utcnow(), True))
        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            result = pm.presence_windows()
        assert result == {}


# ── ALG2 — window_is_today ────────────────────────────────────────────────────

class TestWindowIsToday:

    def test_window_is_today_true(self):
        """window_is_today returns True when preferred_window is today."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = MagicMock(spec=PresenceManager)
        today = datetime.now().weekday()
        pm.preferred_window.return_value = (today, 10)
        pm.window_is_today = PresenceManager.window_is_today.fget(pm)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util"
        ) as mock_dt:
            mock_dt.now.return_value = MagicMock(weekday=lambda: today)
            result = PresenceManager.window_is_today.fget(pm)
        assert result is True

    def test_window_is_today_false_when_none(self):
        """window_is_today returns False when no preferred window."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = MagicMock(spec=PresenceManager)
        pm.preferred_window.return_value = None
        result = PresenceManager.window_is_today.fget(pm)
        assert result is False


# ── ALG3 — gate_blocked() ─────────────────────────────────────────────────────

class TestGateBlocked:

    def _make_dtm(self, options: dict, state_overrides: dict | None = None):
        from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
        entry = MagicMock()
        entry.options = {"demand_cleaning_enabled": True, **options}
        data = MagicMock()
        reported = {"cleanMissionStatus": {"cycle": "none"}, **(state_overrides or {})}
        data.roomba_reported_state.return_value = reported
        data.presence_manager = None
        data.blocking_manager = None
        entry.runtime_data = data
        dtm = DirtThresholdManager.__new__(DirtThresholdManager)
        dtm._entry = entry
        dtm._hass = MagicMock()
        return dtm

    def test_gate_blocked_false_when_all_clear(self):
        dtm = self._make_dtm({})
        blocked, reason = dtm.gate_blocked()
        assert blocked is False
        assert reason == ""

    def test_gate_blocked_true_when_disabled(self):
        dtm = self._make_dtm({})
        dtm._entry.options["demand_cleaning_enabled"] = False
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert reason == "demand_cleaning_disabled"

    def test_gate_blocked_true_when_robot_busy(self):
        dtm = self._make_dtm(
            {},
            {"cleanMissionStatus": {"cycle": "quick"}}
        )
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert "robot_busy" in reason

    def test_gate_blocked_true_when_blocking_manager_queued(self):
        dtm = self._make_dtm({})
        dtm._entry.runtime_data.blocking_manager = MagicMock()
        dtm._entry.runtime_data.blocking_manager.is_queued = True
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert reason == "blocking_sensor_queued"


# ── MS1 — demand_triggered_ts type guard ──────────────────────────────────────

class TestMS1TypeGuard:

    def test_non_float_demand_triggered_ts_not_used(self):
        """MagicMock demand_triggered_ts must not trigger the demand override."""
        import inspect
        from custom_components.roomba_plus import callbacks
        src = inspect.getsource(callbacks)
        # The guard must be isinstance check, not just truthiness
        assert "isinstance(_demand_ts, float)" in src, (
            "MS1: demand_triggered_ts check must use isinstance(float) "
            "to avoid MagicMock comparison errors in tests"
        )


# ── IA74-LP — learning_percentage ────────────────────────────────────────────

class TestLearningPercentage:

    def _make_coordinator_with_pmap(self, pct: int | None) -> MagicMock:
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = MagicMock(spec=IrobotCloudCoordinator)
        map_header = {} if pct is None else {"learning_percentage": pct}
        cc.data = {
            "pmaps": [{
                "active_pmapv_details": {
                    "active_pmapv": {"pmap_id": "abc123"},
                    "map_header": map_header,
                }
            }]
        }
        # Use real property
        cc.learning_percentage = IrobotCloudCoordinator.learning_percentage.fget(cc)
        return cc

    def test_learning_percentage_returns_value(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = {
            "pmaps": [{
                "active_pmapv_details": {
                    "map_header": {"learning_percentage": 87},
                }
            }]
        }
        assert cc.learning_percentage == 87

    def test_learning_percentage_none_when_absent(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = {"pmaps": [{"active_pmapv_details": {"map_header": {}}}]}
        assert cc.learning_percentage is None

    def test_learning_percentage_none_when_no_data(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = None
        assert cc.learning_percentage is None


# ── IA74-ZONE — zone_counts ───────────────────────────────────────────────────

class TestZoneCounts:

    def test_zone_counts_returns_all_categories(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCC:
            zones = [{"id": "z1"}, {"id": "z2"}]
            keepout_zones = [{"id": "k1"}]
            observed_zone_centroids = []

        counts = IrobotCloudCoordinator.zone_counts.fget(_FakeCC())
        assert counts == {"clean": 2, "keepout": 1, "observed": 0}

    def test_zone_counts_empty_when_no_zones(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCC:
            zones = []
            keepout_zones = []
            observed_zone_centroids = []

        counts = IrobotCloudCoordinator.zone_counts.fget(_FakeCC())
        assert counts == {"clean": 0, "keepout": 0, "observed": 0}


# ── IA74-MAINT — new inspect fields ──────────────────────────────────────────

class TestIA74Maint:

    def test_new_fields_default_none(self):
        ms = MaintenanceStore()
        assert ms.wheel_cleaned_at is None
        assert ms.contact_cleaned_at is None
        assert ms.bin_cleaned_at is None

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
