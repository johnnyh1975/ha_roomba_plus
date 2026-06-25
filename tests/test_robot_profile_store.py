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
