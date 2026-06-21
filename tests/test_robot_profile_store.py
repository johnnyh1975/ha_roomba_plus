"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
from collections import defaultdict
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
import tests.conftest
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_archive import MissionArchive


SQFT_TO_M2 = 0.092903  # from const.py


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


def _rps(coverage_baseline=None, coverage_mission_count=0) -> RobotProfileStore:
    rps = RobotProfileStore()
    rps.coverage_baseline = coverage_baseline
    rps.coverage_mission_count = coverage_mission_count
    return rps


def _make_archive(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    """Build a MissionArchive with pre-populated derived records."""
    archive = MissionArchive()
    # Insert oldest-first so newest ends up at index 0 (archive is newest-first)
    for rec in records:
        archive._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            archive._archived_nmssns.add(int(n))
            if int(n) > archive._last_nMssn:
                archive._last_nMssn = int(n)
    archive._initial_load_done = initial_load_done
    return archive


def _derived(
    n_mssn: int,
    rooms: dict | None = None,
) -> dict:
    """Build a minimal derived record for testing."""
    return {
        "nMssn": n_mssn,
        "result": "completed",
        "rooms_completed": rooms or {},
    }


def _make_rps() -> RobotProfileStore:
    return RobotProfileStore()


def _make_hass_v280_l5_arc() -> MagicMock:
    return MagicMock()


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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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


class TestLifetimeSqftStaleness:
    """v2.9.0 (J) — STALENESS TRACKING. Field-confirmed (980 9-series +
    aftermarket NiMH battery): the robot's onboard lifetime sqft counter
    (bbrun.sqft / runtimeStats.sqft) can remain frozen for weeks while the
    robot keeps actively cleaning and every OTHER bbrun.* counter keeps
    incrementing normally. We cannot make the firmware send fresher data,
    but we can detect and surface "this number hasn't changed in N days".
    """

    def test_first_observation_always_counts_as_changed(self):
        """A fresh install (lifetime_sqft_last_value is None) must get an
        initial timestamp immediately, not wait for a second reading."""
        rps = RobotProfileStore()
        assert rps.update_lifetime_sqft_tracking(1000.0) is True
        assert rps.lifetime_sqft_last_value == 1000.0
        assert rps.lifetime_sqft_last_changed_at is not None

    def test_same_value_again_does_not_update_timestamp(self):
        """The whole point of this feature: an unchanged reading must leave
        the OLD timestamp in place — that growing age IS the signal."""
        rps = RobotProfileStore()
        rps.update_lifetime_sqft_tracking(1000.0)
        first_ts = rps.lifetime_sqft_last_changed_at

        changed = rps.update_lifetime_sqft_tracking(1000.0)

        assert changed is False
        assert rps.lifetime_sqft_last_changed_at == first_ts, (
            "Timestamp must NOT advance when the value hasn't actually "
            "changed — this is the staleness signal itself"
        )

    def test_different_value_updates_timestamp(self):
        rps = RobotProfileStore()
        rps.update_lifetime_sqft_tracking(1000.0)
        first_ts = rps.lifetime_sqft_last_changed_at

        import time
        time.sleep(0.01)
        changed = rps.update_lifetime_sqft_tracking(1050.0)

        assert changed is True
        assert rps.lifetime_sqft_last_value == 1050.0
        assert rps.lifetime_sqft_last_changed_at != first_ts

    def test_days_unchanged_none_before_first_observation(self):
        rps = RobotProfileStore()
        assert rps.lifetime_sqft_days_unchanged is None

    def test_days_unchanged_near_zero_immediately_after_update(self):
        rps = RobotProfileStore()
        rps.update_lifetime_sqft_tracking(1000.0)
        days = rps.lifetime_sqft_days_unchanged
        assert days is not None and days < 0.01

    def test_days_unchanged_reflects_an_old_timestamp(self):
        """Simulates a value frozen for 21 days — the exact field-reported
        symptom (180 m² unchanged for multiple weeks)."""
        rps = RobotProfileStore()
        from homeassistant.util import dt as dt_util
        import datetime as _dt
        old_ts = dt_util.now() - _dt.timedelta(days=21)
        rps.lifetime_sqft_last_value = 1000.0
        rps.lifetime_sqft_last_changed_at = old_ts.isoformat()

        days = rps.lifetime_sqft_days_unchanged
        assert days is not None and 20.9 < days < 21.1

    def test_days_unchanged_handles_malformed_timestamp_gracefully(self):
        rps = RobotProfileStore()
        rps.lifetime_sqft_last_changed_at = "not-a-real-timestamp"
        assert rps.lifetime_sqft_days_unchanged is None

    @pytest.mark.asyncio
    async def test_staleness_fields_persist_across_save_load(self):
        rps = RobotProfileStore()
        rps.update_lifetime_sqft_tracking(1234.0)
        saved_ts = rps.lifetime_sqft_last_changed_at

        saved_data: dict = {}

        async def mock_save_fn(data: dict) -> None:
            saved_data.update(data)

        async def mock_load_fn() -> dict:
            return saved_data

        hass = MagicMock()
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

        assert rps2.lifetime_sqft_last_value == 1234.0
        assert rps2.lifetime_sqft_last_changed_at == saved_ts

    @pytest.mark.asyncio
    async def test_async_reset_clears_staleness_fields(self):
        rps = RobotProfileStore()
        rps.update_lifetime_sqft_tracking(1234.0)

        saved_data: dict = {}

        async def mock_save_fn(data: dict) -> None:
            saved_data.update(data)

        hass = MagicMock()
        store_mock = MagicMock()
        store_mock.async_save = mock_save_fn

        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await rps.async_reset(hass, "entry123")

        assert rps.lifetime_sqft_last_value is None
        assert rps.lifetime_sqft_last_changed_at is None


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


class TestComputeHealthScore:

    def test_returns_none_when_fewer_than_3_signals(self):
        """With only battery + anomaly (2 signals), score is None."""
        rps = _rps()  # no coverage baseline → nav signal missing
        # Only battery (1) + anomaly (always counted) = 2 signals
        score = rps.compute_health_score(
            battery_retention_pct=85.0,
            nav_efficiency_ratio=None,    # no baseline → skipped
            cleaning_speed_trend="unknown",  # not in map → skipped
            consecutive_anomalous=0,
            stuck_rate_30d=None,          # skipped
        )
        assert score is None

    def test_returns_score_with_all_signals_healthy(self):
        """All signals healthy → score near 100."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=95.0,   # → 90 pts
            nav_efficiency_ratio=1.0,     # current == baseline → 100 pts
            cleaning_speed_trend="improving",  # → 100 pts
            consecutive_anomalous=0,      # → 100 pts
            stuck_rate_30d=0.02,          # 2% < 5% → 100 pts
        )
        assert score is not None
        assert isinstance(score, float)
        assert 85.0 <= score <= 100.0

    def test_returns_low_score_with_all_signals_poor(self):
        """All signals poor → score near 0."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=55.0,   # → 10 pts (near 0)
            nav_efficiency_ratio=0.4,     # below 0.5 → 0 pts
            cleaning_speed_trend="declining",  # → 40 pts
            consecutive_anomalous=3,      # → 10 pts
            stuck_rate_30d=0.40,          # > 30% → 0 pts
        )
        assert score is not None
        assert score <= 40.0

    def test_score_capped_between_0_and_100(self):
        """Score must always be in [0, 100]."""
        rps = _rps(coverage_baseline=0.5, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=200.0,  # extreme value
            nav_efficiency_ratio=5.0,     # extreme value
            cleaning_speed_trend="improving",
            consecutive_anomalous=0,
            stuck_rate_30d=-0.5,          # extreme negative
        )
        assert score is not None
        assert 0.0 <= score <= 100.0

    def test_partial_signals_renormalise_weights(self):
        """With nav signal missing, remaining weights are renormalised."""
        rps = _rps()  # no coverage baseline → nav skipped
        # Provide battery + trend + anomaly + stuck (4 of 5) = enough
        score = rps.compute_health_score(
            battery_retention_pct=80.0,
            nav_efficiency_ratio=None,    # skipped
            cleaning_speed_trend="stable",
            consecutive_anomalous=0,
            stuck_rate_30d=0.05,
        )
        assert score is not None
        assert 0.0 <= score <= 100.0

    def test_nav_signal_requires_baseline_ready(self):
        """Nav signal only counted when coverage_baseline_ready is True."""
        rps_no_baseline = _rps(coverage_baseline=0.7, coverage_mission_count=5)
        rps_ready = _rps(coverage_baseline=0.7, coverage_mission_count=25)

        kwargs = dict(
            battery_retention_pct=80.0,
            nav_efficiency_ratio=1.0,
            cleaning_speed_trend="stable",
            consecutive_anomalous=0,
            stuck_rate_30d=0.05,
        )
        score_no_baseline = rps_no_baseline.compute_health_score(**kwargs)
        score_ready = rps_ready.compute_health_score(**kwargs)

        # Both should produce a score (nav just gets skipped vs included)
        assert score_no_baseline is not None
        assert score_ready is not None
        # With perfect nav signal included, ready score should be ≥ no-baseline score
        assert score_ready >= score_no_baseline


class TestAllDerivedOldestFirst:
    def test_reverses_newest_first_order(self):
        archive = _make_archive([
            _derived(1),
            _derived(2),
            _derived(3),
        ])
        # Archive stores newest at index 0: [3, 2, 1]
        assert archive._derived[0]["nMssn"] == 3
        # oldest_first should return [1, 2, 3]
        oldest = archive.all_derived_oldest_first()
        assert [r["nMssn"] for r in oldest] == [1, 2, 3]

    def test_empty_archive(self):
        archive = MissionArchive()
        assert archive.all_derived_oldest_first() == []

    def test_single_record(self):
        archive = _make_archive([_derived(5)])
        result = archive.all_derived_oldest_first()
        assert len(result) == 1
        assert result[0]["nMssn"] == 5
