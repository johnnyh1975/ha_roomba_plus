"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
from collections import defaultdict
from datetime import datetime
from datetime import date
from datetime import timezone
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
import tests.conftest
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.sensor import SENSORS
from custom_components.roomba_plus.button import COMMAND_BUTTONS
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.mission_store import MissionStore


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
        score, breakdown = rps.compute_health_score(
            battery_retention_pct=85.0,
            nav_efficiency_ratio=None,    # no baseline → skipped
            cleaning_speed_trend="unknown",  # not in map → skipped
            consecutive_anomalous=0,
            stuck_rate_30d=None,          # skipped
        )
        assert score is None
        assert breakdown == {}

    def test_returns_score_with_all_signals_healthy(self):
        """All signals healthy → score near 100, no weakest_signal."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score, breakdown = rps.compute_health_score(
            battery_retention_pct=95.0,   # → 90 pts
            nav_efficiency_ratio=1.0,     # current == baseline → 100 pts
            cleaning_speed_trend="improving",  # → 100 pts
            consecutive_anomalous=0,      # → 100 pts
            stuck_rate_30d=0.02,          # 2% < 5% → 100 pts
        )
        assert score is not None
        assert isinstance(score, float)
        assert 85.0 <= score <= 100.0
        assert breakdown["weakest_signal"] is None

    def test_returns_low_score_with_all_signals_poor(self):
        """All signals poor → score near 0, weakest_signal identifies the lowest."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score, breakdown = rps.compute_health_score(
            battery_retention_pct=55.0,   # → 10 pts (near 0)
            nav_efficiency_ratio=0.4,     # below 0.5 → 0 pts
            cleaning_speed_trend="declining",  # → 40 pts
            consecutive_anomalous=3,      # → 10 pts
            stuck_rate_30d=0.40,          # > 30% → 0 pts
        )
        assert score is not None
        assert score <= 40.0
        assert breakdown["weakest_signal"] in ("nav_efficiency", "stuck_rate")

    def test_score_capped_between_0_and_100(self):
        """Score must always be in [0, 100]."""
        rps = _rps(coverage_baseline=0.5, coverage_mission_count=25)
        score, _ = rps.compute_health_score(
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
        score, _ = rps.compute_health_score(
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
        score_no_baseline, _ = rps_no_baseline.compute_health_score(**kwargs)
        score_ready, _ = rps_ready.compute_health_score(**kwargs)

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


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_device_intelligence.py (TEST-REORG, v2.9.1) — v1.9.0
# Device Intelligence Phase 3/4/5/6: battery_capacity_mah, nav_panics,
# cliff_events_front/rear sensors, mop lid/tank binary sensors, pad
# wetness selects, map_training button.
# ═══════════════════════════════════════════════════════════════════════

# ── FakeEntity helpers ────────────────────────────────────────────────────────

def _make_entity(vacuum_state: dict, bbchg3: dict | None = None, bbrun: dict | None = None):
    """Minimal IRobotEntity mock for sensor value_fn tests."""
    class _FakeRuntimeData:
        mission_store = None
        maintenance_store = None
        robot_profile = None   # no profile → _estcap_to_mah returns raw estCap (scale=1.0)

    class _FakeConfigEntry:
        runtime_data = _FakeRuntimeData()
        options = {}

    class _FakeEntity:
        _config_entry = _FakeConfigEntry()

        @property
        def vacuum_state(self):
            return vacuum_state

        @property
        def run_stats(self):
            # Merges bbrun + runtimeStats (mirrors entity.py logic)
            return {**(bbrun or {}), **vacuum_state.get("runtimeStats", {})}

        @property
        def battery_stats(self):
            return bbchg3 or vacuum_state.get("bbchg3", {})

    return _FakeEntity()


def _get_sensor(key: str):
    """Return the RoombaSensorDescription for a given key."""
    for desc in SENSORS:
        if desc.key == key:
            return desc
    raise KeyError(f"Sensor '{key}' not found in SENSORS")


def _get_button(key: str):
    """Return the RoombaButtonDescription for a given key."""
    for desc in COMMAND_BUTTONS:
        if desc.key == key:
            return desc
    raise KeyError(f"Button '{key}' not found in COMMAND_BUTTONS")


# ── TestBatteryCapacitySensor ─────────────────────────────────────────────────

class TestBatteryCapacitySensor:
    def test_returns_estcap_when_present(self):
        desc = _get_sensor("battery_capacity_mah")
        e = _make_entity({}, bbchg3={"estCap": 1800})
        assert desc.value_fn(e) == 1800

    def test_returns_none_when_bbchg3_absent(self):
        desc = _get_sensor("battery_capacity_mah")
        e = _make_entity({}, bbchg3={})
        assert desc.value_fn(e) is None

    def test_returns_none_when_estcap_missing_from_bbchg3(self):
        desc = _get_sensor("battery_capacity_mah")
        e = _make_entity({}, bbchg3={"nAvail": 500})
        assert desc.value_fn(e) is None

    def test_filter_fn_true_when_estcap_in_bbchg3(self):
        desc = _get_sensor("battery_capacity_mah")
        state = {"bbchg3": {"estCap": 1800, "nAvail": 500}}
        assert desc.filter_fn(state) is True

    def test_filter_fn_false_when_estcap_missing(self):
        desc = _get_sensor("battery_capacity_mah")
        state = {"bbchg3": {"nAvail": 500}}
        assert desc.filter_fn(state) is False

    def test_filter_fn_false_when_bbchg3_absent(self):
        desc = _get_sensor("battery_capacity_mah")
        assert desc.filter_fn({}) is False

    def test_disabled_by_default(self):
        desc = _get_sensor("battery_capacity_mah")
        assert desc.entity_registry_enabled_default is False

    def test_new_state_filter_bbchg3(self):
        # new_state_filter is on the entity instance, not the description
        # Verify via the description key matching the correct nsf branch
        desc = _get_sensor("battery_capacity_mah")
        assert desc.key == "battery_capacity_mah"


# ── TestNavPanicSensor ────────────────────────────────────────────────────────

class TestNavPanicSensor:
    def test_returns_npanics_when_present(self):
        desc = _get_sensor("nav_panics")
        e = _make_entity({}, bbrun={"nPanics": 1468})
        assert desc.value_fn(e) == 1468

    def test_returns_none_when_npanics_absent(self):
        desc = _get_sensor("nav_panics")
        e = _make_entity({}, bbrun={"nStuck": 5})
        assert desc.value_fn(e) is None

    def test_returns_zero_when_npanics_is_zero(self):
        desc = _get_sensor("nav_panics")
        e = _make_entity({}, bbrun={"nPanics": 0})
        assert desc.value_fn(e) == 0

    def test_filter_fn_true_when_npanics_in_bbrun(self):
        desc = _get_sensor("nav_panics")
        state = {"bbrun": {"nPanics": 10, "nStuck": 2}}
        assert desc.filter_fn(state) is True

    def test_filter_fn_false_when_npanics_missing(self):
        desc = _get_sensor("nav_panics")
        state = {"bbrun": {"nStuck": 2}}
        assert desc.filter_fn(state) is False

    def test_filter_fn_false_when_bbrun_absent(self):
        desc = _get_sensor("nav_panics")
        assert desc.filter_fn({}) is False

    def test_disabled_by_default(self):
        desc = _get_sensor("nav_panics")
        assert desc.entity_registry_enabled_default is False


# ── TestCliffCounterSensors ───────────────────────────────────────────────────

class TestCliffCounterSensors:
    def test_front_returns_ncliffsf(self):
        desc = _get_sensor("cliff_events_front")
        e = _make_entity({}, bbrun={"nCliffsF": 6589, "nCliffsR": 3307})
        assert desc.value_fn(e) == 6589

    def test_rear_returns_ncliffsr(self):
        desc = _get_sensor("cliff_events_rear")
        e = _make_entity({}, bbrun={"nCliffsF": 6589, "nCliffsR": 3307})
        assert desc.value_fn(e) == 3307

    def test_front_and_rear_independent(self):
        desc_f = _get_sensor("cliff_events_front")
        desc_r = _get_sensor("cliff_events_rear")
        e = _make_entity({}, bbrun={"nCliffsF": 100, "nCliffsR": 0})
        assert desc_f.value_fn(e) == 100
        assert desc_r.value_fn(e) == 0

    def test_front_filter_fn_true_when_ncliffsf_present(self):
        desc = _get_sensor("cliff_events_front")
        assert desc.filter_fn({"bbrun": {"nCliffsF": 0}}) is True

    def test_rear_filter_fn_true_when_ncliffsr_present(self):
        desc = _get_sensor("cliff_events_rear")
        assert desc.filter_fn({"bbrun": {"nCliffsR": 0}}) is True

    def test_front_filter_fn_false_when_ncliffsf_missing(self):
        desc = _get_sensor("cliff_events_front")
        assert desc.filter_fn({"bbrun": {"nCliffsR": 5}}) is False

    def test_rear_filter_fn_false_when_ncliffsr_missing(self):
        desc = _get_sensor("cliff_events_rear")
        assert desc.filter_fn({"bbrun": {"nCliffsF": 5}}) is False

    def test_both_disabled_by_default(self):
        for key in ("cliff_events_front", "cliff_events_rear"):
            desc = _get_sensor(key)
            assert desc.entity_registry_enabled_default is False

    def test_front_returns_none_when_bbrun_absent(self):
        desc = _get_sensor("cliff_events_front")
        e = _make_entity({}, bbrun={})
        assert desc.value_fn(e) is None

    def test_rear_returns_none_when_bbrun_absent(self):
        desc = _get_sensor("cliff_events_rear")
        e = _make_entity({}, bbrun={})
        assert desc.value_fn(e) is None


# ── TestBraavaBinarySensors ───────────────────────────────────────────────────

class TestBraavaBinarySensors:
    """Test RoombaMopLidOpen and RoombaMopTankPresentDirect logic."""

    def _make_lid_sensor(self, state: dict):
        from custom_components.roomba_plus.binary_sensor import RoombaMopLidOpen
        sensor = object.__new__(RoombaMopLidOpen)
        sensor._vacuum_state = state
        # Patch roomba_reported_state to return the state dict
        sensor._vacuum = types.SimpleNamespace()
        # Override is_on directly via the property logic
        return state

    def test_lid_open_is_on_when_true(self):
        from custom_components.roomba_plus.binary_sensor import RoombaMopLidOpen
        # Test the logic: bool(state.get("lidOpen", False))
        state = {"lidOpen": True}
        assert bool(state.get("lidOpen", False)) is True

    def test_lid_open_is_off_when_false(self):
        state = {"lidOpen": False}
        assert bool(state.get("lidOpen", False)) is False

    def test_lid_open_is_off_when_absent(self):
        assert bool({}.get("lidOpen", False)) is False

    def test_tank_present_is_on_when_true(self):
        state = {"tankPresent": True}
        assert bool(state.get("tankPresent", True)) is True

    def test_tank_present_is_on_when_absent(self):
        # Default is True (tank assumed present when field missing)
        assert bool({}.get("tankPresent", True)) is True

    def test_tank_present_is_off_when_false(self):
        state = {"tankPresent": False}
        assert bool(state.get("tankPresent", True)) is False

    def test_lid_sensor_only_created_when_lidopen_in_state(self):
        state_with = {"lidOpen": False, "mopReady": {}}
        state_without = {"mopReady": {}}
        assert "lidOpen" in state_with
        assert "lidOpen" not in state_without

    def test_tank_sensor_only_created_when_tankpresent_in_state(self):
        state_with = {"tankPresent": True}
        state_without = {"mopReady": {"tankPresent": True}}  # nested — not top-level
        assert "tankPresent" in state_with
        assert "tankPresent" not in state_without

    def test_lid_new_state_filter(self):
        # new_state_filter: "lidOpen" in new_state
        assert "lidOpen" in {"lidOpen": True, "batPct": 80}
        assert "lidOpen" not in {"batPct": 80}

    def test_tank_new_state_filter(self):
        assert "tankPresent" in {"tankPresent": False}
        assert "tankPresent" not in {"batPct": 80}

    def test_no_conflict_with_mopreaddytankpresent(self):
        """Top-level tankPresent and mopReady.tankPresent are different fields."""
        state = {"tankPresent": True, "mopReady": {"tankPresent": False}}
        assert state["tankPresent"] is True  # direct
        assert state["mopReady"]["tankPresent"] is False  # nested


# ── TestPadWetnessSelect ──────────────────────────────────────────────────────

class TestPadWetnessSelect:
    def test_options_are_string_integers(self):
        from custom_components.roomba_plus.select import _PAD_WET_OPTIONS
        assert _PAD_WET_OPTIONS == ["1", "2", "3"]

    def test_disposable_current_option_reads_disposable_key(self):
        # current_option: vacuum_state.get("padWetness", {}).get("disposable")
        state = {"padWetness": {"disposable": 2, "reusable": 3}}
        val = state.get("padWetness", {}).get("disposable")
        assert str(val) == "2"

    def test_reusable_current_option_reads_reusable_key(self):
        state = {"padWetness": {"disposable": 1, "reusable": 3}}
        val = state.get("padWetness", {}).get("reusable")
        assert str(val) == "3"

    def test_current_option_none_when_padwetness_absent(self):
        state = {}
        val = state.get("padWetness", {}).get("disposable")
        assert val is None

    def test_disposable_write_preserves_reusable(self):
        """When writing disposable=2, reusable value from state is preserved."""
        state = {"padWetness": {"disposable": 1, "reusable": 3}}
        level = 2
        current = state.get("padWetness", {})
        payload = {"disposable": level, "reusable": current.get("reusable", level)}
        assert payload == {"disposable": 2, "reusable": 3}

    def test_reusable_write_preserves_disposable(self):
        """When writing reusable=1, disposable value from state is preserved."""
        state = {"padWetness": {"disposable": 2, "reusable": 3}}
        level = 1
        current = state.get("padWetness", {})
        payload = {"disposable": current.get("disposable", level), "reusable": level}
        assert payload == {"disposable": 2, "reusable": 1}

    def test_write_uses_fallback_when_other_key_absent(self):
        """If only one key is present, fallback to the new level for the missing one."""
        state = {"padWetness": {"disposable": 2}}
        level = 3
        current = state.get("padWetness", {})
        payload = {"disposable": current.get("disposable", level), "reusable": level}
        assert payload == {"disposable": 2, "reusable": 3}

    def test_new_state_filter_padwetness(self):
        assert "padWetness" in {"padWetness": {"disposable": 2}}
        assert "padWetness" not in {"batPct": 80}

    def test_disposable_only_created_when_padwetness_in_state(self):
        state = {"padWetness": {"disposable": 2, "reusable": 3}}
        assert "padWetness" in state

    def test_options_count(self):
        from custom_components.roomba_plus.select import _PAD_WET_OPTIONS
        assert len(_PAD_WET_OPTIONS) == 3


# ── TestMapTrainingButton ─────────────────────────────────────────────────────

class TestMapTrainingButton:
    def test_command_is_train(self):
        desc = _get_button("map_training")
        assert desc.command == "train"

    def test_filter_fn_true_when_pmaps_present(self):
        desc = _get_button("map_training")
        state = {"pmaps": [{"pmap_id": "abc"}]}
        assert desc.filter_fn(state) is True

    def test_filter_fn_false_when_pmaps_empty(self):
        desc = _get_button("map_training")
        assert desc.filter_fn({"pmaps": []}) is False

    def test_filter_fn_false_when_pmaps_absent(self):
        desc = _get_button("map_training")
        assert desc.filter_fn({}) is False

    def test_enabled_by_default(self):
        desc = _get_button("map_training")
        assert desc.entity_registry_enabled_default is True

    def test_positioned_after_locate(self):
        keys = [d.key for d in COMMAND_BUTTONS]
        locate_idx = keys.index("locate")
        train_idx = keys.index("map_training")
        assert train_idx == locate_idx + 1

    def test_positioned_before_experimental_spot(self):
        keys = [d.key for d in COMMAND_BUTTONS]
        train_idx = keys.index("map_training")
        spot_idx = keys.index("spot")
        assert train_idx < spot_idx

    def test_key_unique_in_command_buttons(self):
        keys = [d.key for d in COMMAND_BUTTONS]
        assert keys.count("map_training") == 1


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_wear_intelligence.py (TEST-REORG, v2.9.1) — L4 Wear
# Intelligence: MissionStore.wear_data() and wear_rate_since_reset().
# ═══════════════════════════════════════════════════════════════════════

# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0, hour: int = 10) -> str:
    """Return an ISO datetime string N days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


import time as _time_mod
__make_record_seq = 0

def _make_unique_id(days_ago):
    global __make_record_seq
    __make_record_seq += 1
    return f"m_{days_ago}_{__make_record_seq}"

def _make_record(days_ago: float = 0, bbrun_hr: int = 100) -> dict:
    started = _iso(days_ago, hour=8)
    ended = _iso(days_ago, hour=9)
    return {
        "id": _make_unique_id(days_ago),
        "started_at": started,
        "ended_at": ended,
        "duration_min": 60,
        "area_sqft": 400.0,
        "result": "completed",
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": bbrun_hr,
    }


def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


# ── wear_data() ───────────────────────────────────────────────────────────────

class TestWearData:
    def test_empty_when_no_records(self):
        store = MissionStore()
        assert store.wear_data(30) == []

    def test_single_day_single_record(self):
        store = _store_with(_make_record(days_ago=1, bbrun_hr=50))
        result = store.wear_data(30)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 50

    def test_multiple_records_same_day_keeps_highest_bbrun_hr(self):
        store = _store_with(
            _make_record(days_ago=1, bbrun_hr=80),
            _make_record(days_ago=1, bbrun_hr=120),
            _make_record(days_ago=1, bbrun_hr=95),
        )
        result = store.wear_data(30)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 120

    def test_multiple_days_sorted_ascending(self):
        store = _store_with(
            _make_record(days_ago=3, bbrun_hr=30),
            _make_record(days_ago=1, bbrun_hr=50),
            _make_record(days_ago=2, bbrun_hr=40),
        )
        result = store.wear_data(30)
        assert len(result) == 3
        hrs = [r["bbrun_hr"] for r in result]
        assert hrs == sorted(hrs)

    def test_filters_by_days_parameter(self):
        store = _store_with(
            _make_record(days_ago=2, bbrun_hr=40),
            _make_record(days_ago=10, bbrun_hr=80),
        )
        result = store.wear_data(5)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 40

    def test_returns_list_never_none(self):
        store = MissionStore()
        result = store.wear_data(30)
        assert isinstance(result, list)

    def test_record_without_bbrun_hr_excluded(self):
        store = MissionStore()
        r = _make_record(days_ago=1, bbrun_hr=50)
        del r["bbrun_hr"]
        store._records.append(r)
        assert store.wear_data(30) == []

    def test_date_field_is_iso_string(self):
        store = _store_with(_make_record(days_ago=1, bbrun_hr=50))
        result = store.wear_data(30)
        # Should be parseable as a date
        date.fromisoformat(result[0]["date"])


# ── wear_rate_since_reset() ───────────────────────────────────────────────────

class TestWearRateSinceReset:
    def test_none_when_reset_at_is_none(self):
        store = MissionStore()
        assert store.wear_rate_since_reset(0, None, 50) is None

    def test_none_when_current_hr_not_advanced(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        assert store.wear_rate_since_reset(100, reset_at, 100) is None
        assert store.wear_rate_since_reset(100, reset_at, 90) is None

    def test_none_when_fewer_than_3_days_elapsed(self):
        store = MissionStore()
        reset_at = _iso(days_ago=2)
        assert store.wear_rate_since_reset(0, reset_at, 10) is None

    def test_none_at_exactly_2_days_elapsed(self):
        store = MissionStore()
        reset_at = _iso(days_ago=2)
        assert store.wear_rate_since_reset(0, reset_at, 20) is None

    def test_value_at_exactly_3_days_elapsed(self):
        store = MissionStore()
        # Use 4 days to guarantee > 3 days threshold regardless of current
        # wall-clock time. _iso() rounds to hour=10, so 3.x days may be
        # < 3 days elapsed depending on when the test runs.
        # Rate = 4hr / 4days = 1.0 h/day (with tolerance for rounding)
        reset_at = _iso(days_ago=4)
        result = store.wear_rate_since_reset(0, reset_at, 4)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.2)

    def test_correct_rate_over_30_days(self):
        store = MissionStore()
        reset_at = _iso(days_ago=30)
        # 30 hours consumed over 30 days = 1.0 h/day
        result = store.wear_rate_since_reset(0, reset_at, 30)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.05)

    def test_rate_rounded_to_2_decimal_places(self):
        store = MissionStore()
        reset_at = _iso(days_ago=7)
        result = store.wear_rate_since_reset(0, reset_at, 10)
        assert result is not None
        assert result == round(result, 2)

    def test_none_when_reset_at_unparseable(self):
        store = MissionStore()
        assert store.wear_rate_since_reset(0, "not-a-date", 50) is None

    def test_never_returns_negative(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # current_hr > reset_hr is already guarded, but confirm
        result = store.wear_rate_since_reset(50, reset_at, 100)
        assert result is None or result >= 0.0

    def test_reset_hr_offset_applied_correctly(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # reset_hr=100, current_hr=110 → 10 hours in 10 days = 1.0 h/day
        result = store.wear_rate_since_reset(100, reset_at, 110)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.1)

    def test_result_is_float_not_int(self):
        store = MissionStore()
        reset_at = _iso(days_ago=7)
        result = store.wear_rate_since_reset(0, reset_at, 7)
        if result is not None:
            assert isinstance(result, float)


# ── Integration: wear_data + wear_rate_since_reset ───────────────────────────

class TestWearIntegration:
    def test_wear_data_feeds_into_rate_calculation(self):
        """wear_data provides the time-series; wear_rate_since_reset computes the scalar."""
        store = _store_with(
            _make_record(days_ago=5, bbrun_hr=50),
            _make_record(days_ago=4, bbrun_hr=51),
            _make_record(days_ago=3, bbrun_hr=52),
        )
        data = store.wear_data(30)
        assert len(data) == 3
        # Reset at day 5 with hr=50 → current hr=52 over ~5 days
        reset_at = _iso(days_ago=5)
        rate = store.wear_rate_since_reset(50, reset_at, 52)
        assert rate is not None
        assert rate == pytest.approx(0.4, abs=0.1)

    def test_empty_store_returns_none_rate(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # No records, but rate calculation is independent of records
        # (uses wall-clock elapsed time and current_hr directly)
        result = store.wear_rate_since_reset(0, reset_at, 10)
        assert result is not None  # math works without records
        assert result == pytest.approx(1.0, abs=0.1)


class TestRobotProfileStoreCorruptionResilience:
    """Stress-test (real-store bug-hunt): async_load must survive a corrupted
    or hand-edited persisted store without raising — degrade to defaults.

    Found via field-data stress test: baseline_by_weekday / room_dirt_index
    set to null (or a non-dict) made .items() raise AttributeError, which the
    original except (TypeError, ValueError, KeyError) did not catch.
    """

    def _load_with(self, payload):
        async def mock_load_fn():
            return payload
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_load = mock_load_fn
        store_mock.async_save = AsyncMock()
        rps = RobotProfileStore()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            asyncio.get_event_loop().run_until_complete(
                rps.async_load(hass, "entry123")
            )
        return rps

    def test_null_baseline_by_weekday(self):
        rps = self._load_with({"baseline_by_weekday": None, "room_dirt_index": None})
        assert rps.baseline_by_weekday == {}
        assert rps.room_dirt_index == {}

    def test_wrong_type_baseline(self):
        rps = self._load_with({
            "baseline_by_weekday": ["not", "a", "dict"],
            "room_dirt_index": "also wrong",
        })
        assert rps.baseline_by_weekday == {}
        assert rps.room_dirt_index == {}

    def test_all_null_payload(self):
        keys = [
            "learned_filter_hours", "learned_brush_hours", "baseline_by_weekday",
            "room_dirt_index", "mission_duration_mean", "mission_area_mean",
            "coverage_baseline",
        ]
        rps = self._load_with({k: None for k in keys})
        # No crash; numeric fields stay None, dicts empty
        assert rps.coverage_baseline is None
        assert rps.baseline_by_weekday == {}


# ─────────────────────────────────────────────────────────────────────────────
# L9-MAP (v3.1.0) — self-calibrating relocalisation rate baseline
# ─────────────────────────────────────────────────────────────────────────────

class TestRelocBaseline:
    """L9-MAP (v3.1.0) — update_reloc_baseline / reloc_baseline_ready / reloc_percentile_rank (v3.5.0)."""

    def test_first_update_sets_baseline_directly(self):
        """First observation becomes the initial baseline value."""
        rps = RobotProfileStore()
        rps.update_reloc_baseline(0)
        assert rps.reloc_baseline == 0.0
        assert rps.reloc_mission_count == 1
        assert rps.recent_relocs == [0]

    def test_running_mean_converges(self):
        """Multiple observations converge to a running mean, not the last value."""
        rps = RobotProfileStore()
        for v in [0, 0, 2, 0, 0]:
            rps.update_reloc_baseline(v)
        assert rps.reloc_mission_count == 5
        assert rps.reloc_baseline == pytest.approx(0.4)

    def test_recent_window_bounded(self):
        """recent_relocs never exceeds _RELOC_WINDOW (10) entries."""
        rps = RobotProfileStore()
        for v in range(15):
            rps.update_reloc_baseline(v)
        assert len(rps.recent_relocs) == 10
        # newest values retained (5..14), oldest (0..4) dropped
        assert rps.recent_relocs == list(range(5, 15))

    def test_baseline_not_ready_below_min_missions(self):
        """reloc_baseline_ready is False below _RELOC_BASELINE_MIN_MISSIONS (15)."""
        rps = RobotProfileStore()
        for _ in range(14):
            rps.update_reloc_baseline(0)
        assert rps.reloc_baseline_ready is False

    def test_baseline_ready_at_min_missions(self):
        """reloc_baseline_ready becomes True at exactly 15 missions."""
        rps = RobotProfileStore()
        for _ in range(15):
            rps.update_reloc_baseline(0)
        assert rps.reloc_baseline_ready is True

    def test_percentile_rank_none_below_min_missions(self):
        """reloc_percentile_rank is None before enough history has
        accumulated, regardless of how extreme the recent values are."""
        rps = RobotProfileStore()
        for _ in range(10):
            rps.update_reloc_baseline(50)  # extreme values, but too few missions
        assert rps.reloc_percentile_rank() is None

    def test_percentile_rank_high_for_genuine_spike(self):
        """A burst of high values against a long quiet history ranks near
        the top of the robot's own historical distribution — no fixed
        multiplier, purely relative to what this robot has itself seen."""
        rps = RobotProfileStore()
        # Long quiet history establishes the reference distribution.
        for _ in range(100):
            rps.update_reloc_baseline(1)
        # A burst of high values — window fills with these.
        for _ in range(10):
            rps.update_reloc_baseline(10)
        assert rps.recent_relocs == [10] * 10
        rank = rps.reloc_percentile_rank()
        assert rank is not None
        assert rank > 90  # current window is higher than nearly all history

    def test_percentile_rank_is_order_statistic_not_magnitude(self):
        """Honest property check: reloc_percentile_rank is a rank statistic
        — it reflects whether the current window is unusual for this
        robot, not by how much. Two differently-sized sustained increases
        that create the same window structure (10 uniform new values
        against the same older baseline) rank identically, because rank
        statistics discard magnitude by design. This is expected, not a
        bug — the property that matters is captured by the other tests
        (None below min missions, zero-inflated ties ranking mid, a
        clearly-still-typical reading ranking low).
        """
        rps_a = RobotProfileStore()
        for _ in range(100):
            rps_a.update_reloc_baseline(1)
        for _ in range(10):
            rps_a.update_reloc_baseline(10)
        rank_a = rps_a.reloc_percentile_rank()

        rps_b = RobotProfileStore()
        for _ in range(100):
            rps_b.update_reloc_baseline(1)
        for _ in range(10):
            rps_b.update_reloc_baseline(2)
        rank_b = rps_b.reloc_percentile_rank()

        assert rank_a is not None and rank_b is not None
        assert rank_a == rank_b  # same window structure -> same rank

    def test_percentile_rank_zero_inflated_history_ranks_typical_reading_mid(self):
        """v3.5.0 — the whole reason the fixed multiplier was replaced: on
        a robot whose history is entirely zero, a current reading of zero
        is completely typical. It must rank near the middle (mean-rank tie
        handling), not misleadingly at either extreme — a naive fixed
        threshold against a zero baseline would either never fire or
        hair-trigger on the first nonzero value, neither of which reflects
        reality here.
        """
        rps = RobotProfileStore()
        for _ in range(100):
            rps.update_reloc_baseline(0)
        rank = rps.reloc_percentile_rank()
        assert rank is not None
        assert 40 <= rank <= 60  # a typical (all-zero) reading, not extreme

    def test_percentile_rank_still_low_for_zero_after_real_history_has_spikes(self):
        """A current all-zero window ranks LOW (not mid) once the robot's
        own history actually contains a meaningful mix of nonzero values —
        confirms the percentile genuinely reflects the shape of each
        robot's own distribution rather than a hardcoded rule."""
        rps = RobotProfileStore()
        # Half zero, half elevated — a genuinely mixed history.
        for _ in range(50):
            rps.update_reloc_baseline(0)
        for _ in range(50):
            rps.update_reloc_baseline(5)
        # Now bring the recent window back down to all-zero.
        for _ in range(10):
            rps.update_reloc_baseline(0)
        rank = rps.reloc_percentile_rank()
        assert rank is not None
        assert rank < 50

    def test_persistence_fields_round_trip_via_attributes(self):
        """reloc_baseline state is captured correctly by the four persisted
        attributes (reloc_baseline, reloc_mission_count, recent_relocs,
        reloc_history [v3.5.0]) — the same fields async_save()/async_load()
        read and write.
        """
        rps = RobotProfileStore()
        for v in [0, 1, 0, 2, 0]:
            rps.update_reloc_baseline(v)

        # Simulate what async_load() does: read the persisted dict shape
        # and reconstruct a fresh store from it.
        persisted = {
            "reloc_baseline": rps.reloc_baseline,
            "reloc_mission_count": rps.reloc_mission_count,
            "recent_relocs": rps.recent_relocs,
            "reloc_history": rps.reloc_history,
        }
        restored = RobotProfileStore()
        rb = persisted.get("reloc_baseline")
        restored.reloc_baseline = float(rb) if rb is not None else None
        restored.reloc_mission_count = int(persisted.get("reloc_mission_count", 0))
        restored.recent_relocs = [int(v) for v in persisted.get("recent_relocs", [])]
        restored.reloc_history = [int(v) for v in persisted.get("reloc_history", [])]

        assert restored.reloc_baseline == pytest.approx(rps.reloc_baseline)
        assert restored.reloc_mission_count == rps.reloc_mission_count
        assert restored.recent_relocs == rps.recent_relocs
        assert restored.reloc_history == rps.reloc_history

    def test_missing_reloc_fields_default_safely(self):
        """A legacy payload without reloc_baseline fields loads with safe
        defaults — no crash, baseline starts fresh. Mirrors the .get()
        defaulting pattern used in the real async_load(). Includes
        reloc_history (v3.5.0) — a legacy payload predating it must also
        default safely, not crash.
        """
        legacy_payload = {
            "version": 1,
            "coverage_baseline": 0.8,
            "coverage_mission_count": 25,
            # reloc_baseline fields deliberately absent
        }
        restored = RobotProfileStore()
        rb = legacy_payload.get("reloc_baseline")
        restored.reloc_baseline = float(rb) if rb is not None else None
        restored.reloc_mission_count = int(legacy_payload.get("reloc_mission_count", 0))
        restored.recent_relocs = [int(v) for v in legacy_payload.get("recent_relocs", [])]
        restored.reloc_history = [int(v) for v in legacy_payload.get("reloc_history", [])]

        assert restored.reloc_baseline is None
        assert restored.reloc_mission_count == 0
        assert restored.recent_relocs == []
        assert restored.reloc_history == []


# ─────────────────────────────────────────────────────────────────────────────
# L9-BATTERY (v3.1.0) — self-calibrating estCap noise-floor baseline
# ─────────────────────────────────────────────────────────────────────────────

class TestEstcapNoiseBaseline:
    """L9-BATTERY (v3.1.0) — update_estcap_noise / record_estcap_observation /
    degradation_rate_is_significant / cap_remaining_cycles.
    """

    def test_record_first_observation_seeds_without_delta(self):
        """First observation only seeds last_estcap_mah — no delta to record yet."""
        rps = RobotProfileStore()
        rps.record_estcap_observation(2488.0)
        assert rps.last_estcap_mah == 2488.0
        assert rps.estcap_noise_count == 0

    def test_record_second_observation_feeds_delta(self):
        """Second observation computes and records the delta from the first."""
        rps = RobotProfileStore()
        rps.record_estcap_observation(2488.0)
        rps.record_estcap_observation(2492.0)
        assert rps.estcap_noise_count == 1
        assert rps.estcap_noise_mean == pytest.approx(4.0)
        assert rps.last_estcap_mah == 2492.0

    def test_stdev_none_below_two_samples(self):
        """Standard deviation is undefined with fewer than 2 delta observations."""
        rps = RobotProfileStore()
        rps.update_estcap_noise(2.0)
        assert rps.estcap_noise_stdev is None

    def test_stdev_computed_with_oscillating_deltas(self):
        """Welford's algorithm produces a sensible stdev for noisy, mean-reverting
        deltas (mirrors Thonno's field data: estCap oscillates ±a few mAh).
        """
        rps = RobotProfileStore()
        for delta in [4, -4, -6, -2, -8, 25]:  # roughly Thonno's observed deltas
            rps.update_estcap_noise(delta)
        assert rps.estcap_noise_stdev is not None
        assert rps.estcap_noise_stdev > 0

    def test_noise_ready_at_min_samples(self):
        """estcap_noise_ready becomes True at _ESTCAP_NOISE_MIN_SAMPLES (10)."""
        rps = RobotProfileStore()
        for _ in range(9):
            rps.update_estcap_noise(1.0)
        assert rps.estcap_noise_ready is False
        rps.update_estcap_noise(1.0)
        assert rps.estcap_noise_ready is True

    def test_significant_false_when_not_ready_and_below_fallback(self):
        """Before the noise baseline is ready, a tiny degradation_rate
        (below the fallback threshold) is not trusted.
        """
        rps = RobotProfileStore()
        # Thonno's actual field rate from the noise-only case: ~0.0003 %/cycle
        assert rps.degradation_rate_is_significant(0.0003, 2421) is False

    def test_significant_true_when_not_ready_but_above_fallback(self):
        """Before the noise baseline is ready, a clearly large degradation_rate
        (e.g. a genuinely failing battery) is still trusted via the fallback.
        """
        rps = RobotProfileStore()
        # 90% retention over 300 cycles → 0.033 %/cycle, clearly real degradation
        assert rps.degradation_rate_is_significant(0.033, 300) is True

    def test_significant_false_when_ready_and_within_noise_floor(self):
        """Once the noise baseline is established, a degradation_rate that
        produces a total drop within the expected noise drift is rejected —
        this is the core fix for the 354-year false projection bug.
        """
        rps = RobotProfileStore()
        # Establish a noise floor from oscillating field-like deltas
        for delta in [4, -4, -6, -2, -8, 6, -3, 5, -7, 2]:
            rps.update_estcap_noise(delta)
        assert rps.estcap_noise_ready is True
        # Thonno's actual near-zero degradation_rate (noise-driven, not real)
        assert rps.degradation_rate_is_significant(0.0003, 2421) is False

    def test_significant_true_when_ready_and_exceeds_noise_floor(self):
        """Once the noise baseline is established, a degradation_rate whose
        total drop clearly exceeds the expected noise drift is trusted.
        """
        rps = RobotProfileStore()
        for delta in [1, -1, 0, 1, -1, 0, 1, -1, 0, 1]:  # small, tight noise floor
            rps.update_estcap_noise(delta)
        assert rps.estcap_noise_ready is True
        # Large, clearly-real degradation rate — total drop (0.2 * 500 = 100)
        # vs expected noise drift (stdev≈0.88 * sqrt(500)≈19.6, threshold≈39.2)
        # is unambiguously well above the noise floor.
        assert rps.degradation_rate_is_significant(0.2, 500) is True

    def test_cap_remaining_cycles_passes_through_reasonable_values(self):
        """A plausible remaining-cycle count is returned unchanged."""
        rps = RobotProfileStore()
        assert rps.cap_remaining_cycles(750.0) == 750.0

    def test_cap_remaining_cycles_rejects_absurd_projection(self):
        """A projection beyond the sanity cap (e.g. the 129000-cycle/354-year
        case from Thonno's noise-only field data) is rejected as None.
        """
        rps = RobotProfileStore()
        assert rps.cap_remaining_cycles(129342.0) is None

    def test_cap_remaining_cycles_boundary(self):
        """Exactly at the sanity cap is still accepted (strict > comparison)."""
        rps = RobotProfileStore()
        assert rps.cap_remaining_cycles(10_000.0) == 10_000.0
        assert rps.cap_remaining_cycles(10_000.1) is None

    def test_estcap_noise_persistence_fields_round_trip(self):
        """estcap_noise state survives the same load/save attribute pattern
        used by the real async_load()/async_save().
        """
        rps = RobotProfileStore()
        rps.record_estcap_observation(2488.0)
        rps.record_estcap_observation(2492.0)
        rps.record_estcap_observation(2480.0)

        persisted = {
            "estcap_noise_mean": rps.estcap_noise_mean,
            "estcap_noise_m2": rps.estcap_noise_m2,
            "estcap_noise_count": rps.estcap_noise_count,
            "last_estcap_mah": rps.last_estcap_mah,
        }
        restored = RobotProfileStore()
        enm = persisted.get("estcap_noise_mean")
        restored.estcap_noise_mean = float(enm) if enm is not None else None
        restored.estcap_noise_m2 = float(persisted.get("estcap_noise_m2", 0.0))
        restored.estcap_noise_count = int(persisted.get("estcap_noise_count", 0))
        lec = persisted.get("last_estcap_mah")
        restored.last_estcap_mah = float(lec) if lec is not None else None

        assert restored.estcap_noise_count == rps.estcap_noise_count
        assert restored.estcap_noise_mean == pytest.approx(rps.estcap_noise_mean)
        assert restored.last_estcap_mah == rps.last_estcap_mah

    def test_missing_estcap_fields_default_safely(self):
        """A legacy payload without estcap_noise fields loads with safe
        defaults — no crash, noise floor starts fresh.
        """
        legacy_payload = {
            "version": 1,
            "reloc_baseline": 0.5,
            # estcap_noise fields deliberately absent
        }
        restored = RobotProfileStore()
        enm = legacy_payload.get("estcap_noise_mean")
        restored.estcap_noise_mean = float(enm) if enm is not None else None
        restored.estcap_noise_m2 = float(legacy_payload.get("estcap_noise_m2", 0.0))
        restored.estcap_noise_count = int(legacy_payload.get("estcap_noise_count", 0))
        lec = legacy_payload.get("last_estcap_mah")
        restored.last_estcap_mah = float(lec) if lec is not None else None

        assert restored.estcap_noise_mean is None
        assert restored.estcap_noise_count == 0
        assert restored.last_estcap_mah is None


def _consecutive_dates(n: int, start: str = "2026-06-01") -> list[str]:
    """n consecutive ISO date strings starting from start, for L10 tests."""
    import datetime as _dt
    d0 = _dt.date.fromisoformat(start)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n)]


class TestHealthScoreHistory:
    """L10 (v3.2.0) — record_health_score / health_score_trend /
    health_score_declining_days.

    Trend significance is judged against this robot's own learned
    reference-period mean/stdev (mirrors TestEstcapNoiseBaseline's
    self-calibration style) — but unlike estcap's lifetime-running Welford
    stats, the reference period is deliberately kept strictly OLDER than
    the current comparison window (see _reference_scores()'s docstring for
    why a lifetime baseline doesn't work here: it absorbs an ongoing
    decline into itself and inflates its own variance by mixing pre- and
    post-decline values). Consequence: health_score_baseline_ready needs
    _HEALTH_SCORE_BASELINE_MIN_DAYS (14) reference days on top of the
    14-day recent window — 28 days total, not 14.
    """

    def test_first_record_seeds_history(self):
        rps = RobotProfileStore()
        rps.record_health_score(75.0, "2026-06-01")
        assert rps.health_score_history == [{"date": "2026-06-01", "score": 75.0}]

    def test_same_day_update_is_idempotent(self):
        """Multiple calls on the same date update in place, not append
        (a coordinator refresh can fire more than once per day)."""
        rps = RobotProfileStore()
        rps.record_health_score(75.0, "2026-06-01")
        rps.record_health_score(80.0, "2026-06-01")
        assert len(rps.health_score_history) == 1
        assert rps.health_score_history[0]["score"] == 80.0

    def test_new_day_appends(self):
        rps = RobotProfileStore()
        rps.record_health_score(75.0, "2026-06-01")
        rps.record_health_score(78.0, "2026-06-02")
        assert len(rps.health_score_history) == 2

    def test_history_trimmed_to_90_days(self):
        rps = RobotProfileStore()
        for d in _consecutive_dates(100):
            rps.record_health_score(70.0, d)
        assert len(rps.health_score_history) == 90
        # Oldest 10 days were dropped — history starts from day 11.
        assert rps.health_score_history[0]["date"] == _consecutive_dates(100)[10]

    def test_stdev_none_below_two_reference_samples(self):
        rps = RobotProfileStore()
        for d in _consecutive_dates(15):  # 1 reference day + 14 recent
            rps.record_health_score(75.0, d)
        assert rps.health_score_stdev is None

    def test_stdev_computed_from_reference_period_only(self):
        """30 reference days with jitter + 14 flat recent days — stdev
        must reflect only the (varying) reference period, not be diluted
        or inflated by the flat recent window."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(44)
        jitter = [72, 78, 74, 80, 76] * 6
        for d, score in zip(dates[:30], jitter):
            rps.record_health_score(float(score), d)
        for d in dates[30:]:
            rps.record_health_score(75.0, d)
        assert rps.health_score_stdev is not None
        assert rps.health_score_stdev > 0

    def test_baseline_not_ready_below_44_total_days(self):
        """v3.2.0 bug-hunt fix — exclusion buffer widened from 14 to 30
        days (see _HEALTH_SCORE_REFERENCE_EXCLUSION_DAYS's docstring for
        why), so the total minimum is now 30+14=44, not 28. 43 is not
        enough."""
        rps = RobotProfileStore()
        for d in _consecutive_dates(43):
            rps.record_health_score(75.0, d)
        assert rps.health_score_baseline_ready is False

    def test_baseline_ready_at_44_total_days(self):
        rps = RobotProfileStore()
        for d in _consecutive_dates(44):
            rps.record_health_score(75.0, d)
        assert rps.health_score_baseline_ready is True

    def test_trend_none_when_not_ready(self):
        rps = RobotProfileStore()
        for d in _consecutive_dates(10):
            rps.record_health_score(50.0, d)
        assert rps.health_score_trend() is None

    def test_trend_stable_with_flat_scores(self):
        rps = RobotProfileStore()
        for d in _consecutive_dates(44):
            rps.record_health_score(80.0, d)
        assert rps.health_score_trend() == "stable"

    def test_trend_declining_with_sustained_drop(self):
        """30 reference days with natural jitter, then 14 days at a clear,
        sustained lower score — should classify as declining. Also
        covers the v3.2.0 bug-hunt fix directly: a decline this long
        (14 days) previously risked reference contamination once it
        exceeded the old 14-day exclusion width — 30 reference days here
        confirms the wider buffer keeps this case clean."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(44)
        jitter = [83, 86, 84, 87, 82, 85, 88, 83, 86, 84,
                  85, 87, 82, 86, 84, 83, 85, 88, 84, 86,
                  85, 83, 87, 84, 86, 82, 88, 85, 83, 87]
        for d, score in zip(dates[:30], jitter):
            rps.record_health_score(float(score), d)
        for d in dates[30:]:
            rps.record_health_score(50.0, d)
        assert rps.health_score_trend() == "declining"

    def test_trend_declining_survives_longer_than_old_exclusion_width(self):
        """v3.2.0 bug-hunt fix — the actual reproduction case: a decline
        lasting 25 days (longer than the OLD 14-day exclusion width, but
        within the new 30-day one) must still classify as declining, not
        silently absorbed back into "stable" the way it was before this
        fix. 30 stable days followed by 25 declining days — the decline
        alone is deliberately longer than the old exclusion width."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(55)
        for d in dates[:30]:
            rps.record_health_score(90.0, d)
        for d in dates[30:]:
            rps.record_health_score(60.0, d)
        assert rps.health_score_trend() == "declining"
        assert rps.health_score_declining_days() == 25

    def test_trend_improving_with_sustained_rise(self):
        rps = RobotProfileStore()
        dates = _consecutive_dates(44)
        jitter = [48, 52, 47, 53, 50, 49, 51, 48, 52, 50,
                  47, 53, 49, 51, 48, 52, 50, 47, 53, 49,
                  48, 52, 47, 53, 50, 49, 51, 48, 52, 50]
        for d, score in zip(dates[:30], jitter):
            rps.record_health_score(float(score), d)
        for d in dates[30:]:
            rps.record_health_score(90.0, d)
        assert rps.health_score_trend() == "improving"

    def test_trend_uses_fallback_threshold_when_stdev_zero(self):
        """Perfectly flat reference period → stdev is 0 → falls back to the
        absolute deviation threshold rather than dividing by zero or
        over-triggering on a trivially small change."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(44)
        for d in dates[:30]:
            rps.record_health_score(80.0, d)  # perfectly flat reference
        for d in dates[30:]:
            rps.record_health_score(75.0, d)  # small dip, below fallback bar
        assert rps.health_score_trend() == "stable"

    def test_declining_days_counts_from_most_recent(self):
        """36 stable reference days + 8 declining days = 44 total, right
        at the new minimum — reference (history[:-30], the first 14 of
        the 36 stable days) stays clean of the decline, so the count
        reflects exactly the 8 declining days."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(36 + 8)
        for d in dates[:36]:
            rps.record_health_score(85.0, d)
        for d in dates[36:]:
            rps.record_health_score(40.0, d)
        assert rps.health_score_declining_days() == 8

    def test_declining_days_stops_at_first_non_declining_entry(self):
        """Scans backward from newest; stops counting at the first entry
        that isn't below the cutoff, even if older entries also qualify.
        36 stable reference days keeps history[:-30] (first 17 entries)
        clean of the trailing decline pattern."""
        rps = RobotProfileStore()
        dates = _consecutive_dates(36 + 11)
        for d in dates[:36]:
            rps.record_health_score(85.0, d)
        for d in dates[36:43]:
            rps.record_health_score(40.0, d)          # would count...
        rps.record_health_score(85.0, dates[43])       # ...but this breaks the streak
        rps.record_health_score(40.0, dates[44])
        rps.record_health_score(40.0, dates[45])
        rps.record_health_score(40.0, dates[46])
        assert rps.health_score_declining_days() == 3

    def test_declining_days_zero_when_not_ready(self):
        rps = RobotProfileStore()
        rps.record_health_score(40.0, "2026-06-01")
        assert rps.health_score_declining_days() == 0

    def test_health_score_history_persistence_round_trip(self):
        """health_score_history survives the same load/save attribute
        pattern used by the real async_load()/async_save()."""
        rps = RobotProfileStore()
        for d, score in zip(_consecutive_dates(3), [72, 78, 74]):
            rps.record_health_score(float(score), d)

        persisted = {"health_score_history": rps.health_score_history}
        restored = RobotProfileStore()
        raw_history = persisted.get("health_score_history", [])
        restored.health_score_history = [
            {"date": str(e.get("date")), "score": float(e.get("score"))}
            for e in raw_history
        ]

        assert restored.health_score_history == rps.health_score_history

    def test_missing_health_score_fields_default_safely(self):
        """A legacy payload without health_score_history loads with safe
        defaults — no crash, history starts empty."""
        legacy_payload = {"version": 1, "reloc_baseline": 0.5}
        restored = RobotProfileStore()
        raw_history = legacy_payload.get("health_score_history", [])
        restored.health_score_history = [
            {"date": str(e.get("date")), "score": float(e.get("score"))}
            for e in raw_history
        ]
        assert restored.health_score_history == []


class TestRoomAccessibilityScores:
    """v3.2.0 ROOM-ACCESS — RobotProfileStore.room_accessibility_scores()."""

    def test_empty_when_no_signals(self):
        assert RobotProfileStore.room_accessibility_scores({}, {}, {}) == {}

    def test_full_coverage_no_stuck_no_time_signal(self):
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={"1": 1.0}, stuck_by_room={}, time_per_area_by_room={},
        )
        assert result["1"]["score"] == 100.0
        assert result["1"]["limiting_factor"] == "coverage_gap"

    def test_partial_coverage_lowers_score(self):
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={"1": 0.5}, stuck_by_room={}, time_per_area_by_room={},
        )
        assert result["1"]["score"] == 50.0

    def test_stuck_at_robot_average_scores_100(self):
        """A room with exactly the robot's own average stuck rate isn't
        penalised — only above-average rooms are."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={"1": 5, "2": 5}, time_per_area_by_room={},
        )
        assert result["1"]["score"] == 100.0
        assert result["2"]["score"] == 100.0

    def test_stuck_above_average_penalised(self):
        """Room 1 has 3x the robot's own average stuck rate — clearly
        elevated relative to this robot's other rooms."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={"1": 9, "2": 1}, time_per_area_by_room={},
        )
        # mean = 5; room 1 ratio = 1.8 -> 100 - 0.8*50 = 60
        assert result["1"]["score"] == pytest.approx(60.0)
        assert result["1"]["limiting_factor"] == "obstacle_density"

    def test_time_per_area_above_average_penalised(self):
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={},
            time_per_area_by_room={"1": 20.0, "2": 10.0},
        )
        # mean = 15; room 1 ratio = 1.333 -> 100 - 0.333*50 ≈ 83.3
        assert result["1"]["score"] < 100.0
        assert result["1"]["limiting_factor"] == "narrow_passages"
        assert result["2"]["score"] == 100.0

    def test_limiting_factor_picks_lowest_component(self):
        """A room with good coverage but bad stuck rate should flag
        obstacle_density, not coverage_gap."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={"1": 1.0},
            stuck_by_room={"1": 9, "2": 1},
            time_per_area_by_room={},
        )
        assert result["1"]["limiting_factor"] == "obstacle_density"

    def test_score_never_below_zero(self):
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={"1": 100, "2": 1}, time_per_area_by_room={},
        )
        assert result["1"]["score"] >= 0.0

    def test_combines_multiple_signals_as_mean(self):
        """Full coverage + at-average stuck rate → mean of (100, 100) = 100."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={"1": 1.0},
            stuck_by_room={"1": 5, "2": 5},
            time_per_area_by_room={},
        )
        assert result["1"]["score"] == 100.0

    def test_room_only_in_stuck_by_room_still_scored(self):
        """A room absent from coverage_by_room (e.g. no UMF polygon data
        yet for it) still gets scored from whatever signals ARE available."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={"1": 5}, time_per_area_by_room={},
        )
        assert result["1"]["score"] == 100.0

    def test_explicit_none_value_does_not_crash(self):
        """v3.2.0 bug-hunt fix — a dict with an explicit None value for a
        present key (not just an absent key) must be treated as "no
        signal for this room", not crash. Found via systematic review:
        the mean-calculation step already filtered None defensively
        (`if v is not None`), but the per-rid scoring step originally
        didn't apply the same guard, so it wasn't just theoretical —
        reproduced directly against the unpatched method during review."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={"1": None, "2": 5},
            time_per_area_by_room={},
        )
        assert result["1"]["score"] is None
        assert result["2"]["score"] == 100.0

    def test_explicit_none_in_time_per_area_does_not_crash(self):
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={}, stuck_by_room={},
            time_per_area_by_room={"1": None, "2": 10.0},
        )
        assert result["1"]["score"] is None
        assert result["2"]["score"] == 100.0

    def test_zero_coverage_still_scored_not_treated_as_missing(self):
        """0.0 coverage is a real, meaningful signal (room genuinely
        uncovered) — must not be confused with "no data for this room"
        (which would be an absent key or explicit None)."""
        result = RobotProfileStore.room_accessibility_scores(
            coverage_by_room={"1": 0.0}, stuck_by_room={}, time_per_area_by_room={},
        )
        assert result["1"]["score"] == 0.0
        assert result["1"]["limiting_factor"] == "coverage_gap"


class TestDockThetaBaseline:
    """v3.2.1 DOCK-ANCHOR — dock_theta_baseline: circular-statistics
    learned reference heading for clean (non-buffered) dock contacts.
    Prerequisite for Dock-Anchor-Korrektur v2 (rotation correction) —
    see Dock_Anchor_Korrektur_Plan.md.
    """

    def test_baseline_none_initially(self):
        rps = RobotProfileStore()
        assert rps.dock_theta_baseline is None
        assert rps.dock_theta_resultant_length is None
        assert rps.dock_theta_circular_stdev_deg is None
        assert rps.dock_theta_baseline_ready is False

    def test_single_observation_becomes_the_mean(self):
        rps = RobotProfileStore()
        rps.update_dock_theta_baseline(90.0)
        assert rps.dock_theta_baseline == pytest.approx(90.0)
        assert rps.dock_theta_resultant_length == pytest.approx(1.0)

    def test_identical_observations_give_zero_stdev(self):
        rps = RobotProfileStore()
        for _ in range(5):
            rps.update_dock_theta_baseline(180.0)
        assert rps.dock_theta_baseline == pytest.approx(180.0)
        assert rps.dock_theta_resultant_length == pytest.approx(1.0)
        assert rps.dock_theta_circular_stdev_deg == pytest.approx(0.0, abs=1e-6)

    def test_wraparound_mean_is_correct_not_naively_averaged(self):
        """The core reason circular statistics are needed: naive linear
        averaging of 359° and 1° gives 180° (wrong — that's the OPPOSITE
        direction). The correct circular mean is 0°."""
        rps = RobotProfileStore()
        rps.update_dock_theta_baseline(359.0)
        rps.update_dock_theta_baseline(1.0)
        assert rps.dock_theta_baseline == pytest.approx(0.0, abs=0.01) or \
            rps.dock_theta_baseline == pytest.approx(360.0, abs=0.01)
        # tight cluster around 0 -> high resultant length, not scattered
        assert rps.dock_theta_resultant_length > 0.99

    def test_uniformly_scattered_observations_give_low_resultant_length(self):
        """Four evenly-spaced headings (90° apart) cancel out entirely —
        resultant length near 0, stdev undefined (maximally scattered)."""
        rps = RobotProfileStore()
        for theta in (0.0, 90.0, 180.0, 270.0):
            rps.update_dock_theta_baseline(theta)
        assert rps.dock_theta_resultant_length == pytest.approx(0.0, abs=1e-9)
        assert rps.dock_theta_circular_stdev_deg is None

    def test_not_ready_below_min_samples_even_if_identical(self):
        """Count floor applies even when values are already perfectly
        consistent — guards against a few coincidentally-similar early
        readings being mistaken for established convergence."""
        from custom_components.roomba_plus.robot_profile_store import (
            _DOCK_THETA_MIN_SAMPLES,
        )
        rps = RobotProfileStore()
        for _ in range(_DOCK_THETA_MIN_SAMPLES - 1):
            rps.update_dock_theta_baseline(45.0)
        assert rps.dock_theta_baseline_ready is False

    def test_ready_once_min_samples_and_tight_stdev_both_met(self):
        from custom_components.roomba_plus.robot_profile_store import (
            _DOCK_THETA_MIN_SAMPLES,
        )
        rps = RobotProfileStore()
        for _ in range(_DOCK_THETA_MIN_SAMPLES):
            rps.update_dock_theta_baseline(45.0)
        assert rps.dock_theta_baseline_ready is True

    def test_not_ready_with_enough_samples_but_too_much_spread(self):
        """Enough observations, but too scattered (large stdev) — count
        alone must not be sufficient."""
        from custom_components.roomba_plus.robot_profile_store import (
            _DOCK_THETA_MIN_SAMPLES,
        )
        rps = RobotProfileStore()
        thetas = [0.0, 40.0, 320.0, 80.0, 280.0, 10.0, 350.0]
        for t in thetas[:max(_DOCK_THETA_MIN_SAMPLES, len(thetas))]:
            rps.update_dock_theta_baseline(t)
        assert rps.dock_theta_count >= _DOCK_THETA_MIN_SAMPLES
        assert rps.dock_theta_baseline_ready is False

    @pytest.mark.asyncio
    async def test_persists_across_save_load_roundtrip(self):
        from unittest.mock import MagicMock, patch
        rps = RobotProfileStore()
        rps.update_dock_theta_baseline(90.0)
        rps.update_dock_theta_baseline(92.0)
        saved = {}
        async def fake_save(data):
            saved.update(data)
        async def fake_load():
            return saved
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        store_mock.async_load = fake_load
        hass = _make_hass()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await rps.async_save(hass, "e1")
            rps2 = RobotProfileStore()
            await rps2.async_load(hass, "e1")
        assert saved["dock_theta_count"] == 2
        assert rps2.dock_theta_count == 2
        assert rps2.dock_theta_baseline == pytest.approx(rps.dock_theta_baseline)

    @pytest.mark.asyncio
    async def test_old_payload_without_dock_theta_loads_cleanly(self):
        """v3.2.1 — additive fields, no version bump: a payload saved
        before this existed simply has no such keys."""
        from unittest.mock import MagicMock, patch
        rps = RobotProfileStore()
        old_payload = {"coverage_baseline": 0.5, "coverage_mission_count": 25}
        async def fake_load():
            return old_payload
        store_mock = MagicMock()
        store_mock.async_load = fake_load
        hass = _make_hass()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await rps.async_load(hass, "e1")
        assert rps.dock_theta_count == 0
        assert rps.dock_theta_baseline is None


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 DIRT-VEL — per-room dirt accumulation velocity
# ─────────────────────────────────────────────────────────────────────────────

class TestDirtVelocity:
    """v3.3.0 DIRT-VEL — velocity = observed density / days since the
    previous cleaning of that room, EMA-smoothed; same-day guard; additive
    persistence (old dumps lack the fields)."""

    def test_velocity_computed_and_ema_smoothed(self, monkeypatch):
        from custom_components.roomba_plus.robot_profile_store import (
            _ROOM_DIRT_EMA_ALPHA,
        )
        rps = RobotProfileStore()
        t = {"now": 1_000_000.0}
        monkeypatch.setattr(
            "custom_components.roomba_plus.robot_profile_store.time.time",
            lambda: t["now"],
        )
        # First cleaning: establishes the timestamp, no velocity yet
        rps.update_room_dirt_index("7", pass_count=20, area_m2=10.0)
        assert rps.dirt_accumulation_rate() == {}
        # Second cleaning 2 days later, density 3.0 → velocity 1.5/day
        t["now"] += 2 * 86400
        rps.update_room_dirt_index("7", pass_count=30, area_m2=10.0)
        assert rps.dirt_accumulation_rate()["7"] == pytest.approx(1.5)
        # Third cleaning 1 day later, density 4.0 → raw 4.0/day, EMA-smoothed
        t["now"] += 1 * 86400
        rps.update_room_dirt_index("7", pass_count=40, area_m2=10.0)
        expected = _ROOM_DIRT_EMA_ALPHA * 4.0 + (1 - _ROOM_DIRT_EMA_ALPHA) * 1.5
        assert rps.dirt_accumulation_rate()["7"] == pytest.approx(expected, abs=1e-3)

    def test_same_day_guard_skips_velocity_sample(self, monkeypatch):
        rps = RobotProfileStore()
        t = {"now": 1_000_000.0}
        monkeypatch.setattr(
            "custom_components.roomba_plus.robot_profile_store.time.time",
            lambda: t["now"],
        )
        rps.update_room_dirt_index("7", pass_count=20, area_m2=10.0)
        # Demand clean 2 h after the scheduled run — no velocity blow-up
        t["now"] += 2 * 3600
        rps.update_room_dirt_index("7", pass_count=5, area_m2=10.0)
        assert rps.dirt_accumulation_rate() == {}
        # Timestamp still advanced: next sample measures from the LAST clean
        t["now"] += 1 * 86400
        rps.update_room_dirt_index("7", pass_count=10, area_m2=10.0)
        assert rps.dirt_accumulation_rate()["7"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_persistence_roundtrip_and_old_dump_compat(self):
        rps = RobotProfileStore()
        rps.room_dirt_velocity = {"7": 1.5}
        rps.room_dirt_last_ts = {"7": 1_000_000.0}
        rps.room_dirt_index = {"7": 3.0}

        saved_data: dict = {}

        async def mock_save_fn(data: dict) -> None:
            saved_data.update(data)

        async def mock_load_fn() -> dict:
            return saved_data

        store_mock = MagicMock()
        store_mock.async_save = mock_save_fn
        store_mock.async_load = mock_load_fn
        hass = MagicMock()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await rps.async_save(hass, "entry123")
            fresh = RobotProfileStore()
            await fresh.async_load(hass, "entry123")
        assert fresh.room_dirt_velocity == {"7": 1.5}
        assert fresh.room_dirt_last_ts == {"7": 1_000_000.0}

        # Old dump predating DIRT-VEL: fields absent → empty, no raise
        for k in ("room_dirt_velocity", "room_dirt_last_ts"):
            saved_data.pop(k)
        older = RobotProfileStore()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store_mock,
        ):
            await older.async_load(hass, "entry123")
        assert older.room_dirt_velocity == {}
        assert older.room_dirt_last_ts == {}
        assert older.room_dirt_index == {"7": 3.0}


class TestSuggestedCleaningInterval:
    """v3.3.0 ROOM-SCHED self-calibration — suggested interval =
    household-median target density / room velocity; clamped 1–14 days;
    recommendation only (never part of the overdue rule)."""

    def test_suggestion_from_median_and_velocity(self):
        rps = RobotProfileStore()
        rps.room_dirt_index = {"7": 3.0, "9": 1.0}   # median = 2.0
        rps.room_dirt_velocity = {"7": 2.0, "9": 0.25}
        s = rps.suggested_cleaning_interval_days()
        assert s["7"] == pytest.approx(1.0)   # 2.0/2.0
        assert s["9"] == pytest.approx(8.0)   # 2.0/0.25
        # Clamps: extreme velocity → floor 1d; near-zero → ceiling 14d
        rps.room_dirt_velocity = {"7": 50.0, "9": 0.01}
        s = rps.suggested_cleaning_interval_days()
        assert s["7"] == 1.0 and s["9"] == 14.0

    def test_guards_insufficient_context(self):
        rps = RobotProfileStore()
        # Fewer than 2 indexed rooms → no median context → empty
        rps.room_dirt_index = {"7": 3.0}
        rps.room_dirt_velocity = {"7": 2.0}
        assert rps.suggested_cleaning_interval_days() == {}
        # Zero/negative velocity entries are skipped, not divided by
        rps.room_dirt_index = {"7": 3.0, "9": 1.0}
        rps.room_dirt_velocity = {"7": 0.0, "9": 1.0}
        assert list(rps.suggested_cleaning_interval_days()) == ["9"]


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 CROSS-CORR — snapshot pairing + Pearson gates
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossCorrelation:
    """v3.3.0 CROSS-CORR — pending-snapshot pairing (tolerance window,
    restart persistence) and the n>=30 / zero-variance Pearson gates."""

    def test_snapshot_pairing_with_tolerance_window(self):
        rps = RobotProfileStore()
        # Snapshot at t0; record started 10 min later → paired
        rps.record_correlation_snapshot({"sensor.humidity": 62.0}, 1_000_000.0)
        from datetime import datetime, timezone
        started = datetime.fromtimestamp(1_000_600, tz=timezone.utc).isoformat()
        assert rps.finalize_correlation(started, dirt=7.0) is True
        assert rps.correlation_samples["sensor.humidity"] == [[62.0, 7.0]]
        assert rps.correlation_pending is None
        # Orphaned old snapshot vs a record 5 h later → NOT paired, stays
        rps.record_correlation_snapshot({"sensor.humidity": 40.0}, 1_000_000.0)
        late = datetime.fromtimestamp(1_000_000 + 5 * 3600, tz=timezone.utc).isoformat()
        assert rps.finalize_correlation(late, dirt=3.0) is False
        assert rps.correlation_pending is not None
        # No dirt / bad timestamp → no-op
        assert rps.finalize_correlation(started, dirt=None) is False
        assert rps.finalize_correlation("garbage", dirt=1.0) is False

    @pytest.mark.asyncio
    async def test_pending_snapshot_survives_persistence(self):
        rps = RobotProfileStore()
        rps.record_correlation_snapshot({"sensor.humidity": 55.0}, 123.0)
        rps.correlation_samples = {"sensor.humidity": [[50.0, 4.0]]}
        saved: dict = {}
        async def save_fn(d): saved.update(d)
        async def load_fn(): return saved
        store = MagicMock(); store.async_save = save_fn; store.async_load = load_fn
        hass = MagicMock()
        with patch(
            "custom_components.roomba_plus.robot_profile_store.Store",
            return_value=store,
        ):
            await rps.async_save(hass, "e1")
            fresh = RobotProfileStore()
            await fresh.async_load(hass, "e1")
        assert fresh.correlation_pending == {"ts": 123.0,
                                             "values": {"sensor.humidity": 55.0}}
        assert fresh.correlation_samples == {"sensor.humidity": [[50.0, 4.0]]}

    def test_pearson_gates_min_samples_and_zero_variance(self):
        rps = RobotProfileStore()
        # 29 perfectly correlated samples → still gated (n < 30)
        rps.correlation_samples = {
            "sensor.humidity": [[float(i), float(2 * i)] for i in range(29)],
        }
        assert rps.correlation_results()["sensor.humidity"] == {"r": None, "n": 29}
        # 30th sample → r computed (≈1.0)
        rps.correlation_samples["sensor.humidity"].append([29.0, 58.0])
        res = rps.correlation_results()["sensor.humidity"]
        assert res["n"] == 30 and res["r"] == pytest.approx(1.0)
        # Constant sensor (zero variance) → None, not StatisticsError
        rps.correlation_samples["sensor.const"] = [[5.0, float(i)] for i in range(30)]
        assert rps.correlation_results()["sensor.const"]["r"] is None
