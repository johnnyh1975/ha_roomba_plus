"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from custom_components.roomba_plus.dirt_threshold_manager import MIN_GAP_HOURS
from custom_components.roomba_plus.dirt_threshold_manager import MIN_RECORDS
from custom_components.roomba_plus.dirt_threshold_manager import TRIGGER_MULTIPLIER_DEFAULT
from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
from custom_components.roomba_plus.dirt_threshold_manager import _compute_dirt_density
from custom_components.roomba_plus.const import CONF_DEMAND_CLEANING_ENABLED
from custom_components.roomba_plus.const import CONF_DEMAND_MULTIPLIER
from custom_components.roomba_plus.models import RoombaData
from custom_components.roomba_plus.models import MapCapability
import statistics
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.const import SQFT_TO_M2


def _make_record(dirt: float, sqft: float) -> dict:
    """Make a minimal cloud record with dirt and sqft."""
    return {"dirt": dirt, "sqft": sqft, "runM": 20, "durationM": 25}


def _records(pairs: list[tuple[float, float]]) -> list[dict]:
    """Build a list of records from (dirt, sqft) pairs."""
    return [_make_record(d, s) for d, s in pairs]


def _make_manager(options: dict | None = None) -> DirtThresholdManager:
    """Build a DirtThresholdManager with minimal mocking."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = options if options is not None else {CONF_DEMAND_CLEANING_ENABLED: True}
    entry.entry_id = "test_entry"
    entry.runtime_data = MagicMock()
    entry.runtime_data.roomba_reported_state.return_value = {
        "cleanMissionStatus": {"cycle": "none"}
    }
    entry.runtime_data.blocking_manager = None
    entry.runtime_data.presence_manager = None
    mgr = DirtThresholdManager(hass, entry)
    return mgr


def _make_dtm() -> DirtThresholdManager:
    hass = MagicMock()
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
        "started_at":   datetime.fromtimestamp(ts, tz=UTC).isoformat(),
        "ended_at":     datetime.fromtimestamp(ts + duration_min * 60, tz=UTC).isoformat(),
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
    today = datetime.now(UTC)
    # Find the Monday of this week, then offset to the desired weekday
    days_since_monday = today.weekday()  # 0 if today is Monday
    this_monday = today - timedelta(days=days_since_monday)
    target = this_monday + timedelta(days=weekday) - timedelta(weeks=weeks_back)
    return int(target.timestamp())


def _make_20_normal_records() -> list[dict]:
    """Create 20 normal mission records with consistent duration and area."""
    return [_mission_rec(duration_min=60, area_sqft=200.0) for _ in range(20)]


class TestComputeDirtDensity:

    def test_basic_calculation(self):
        # 10 dirt events over 100 sqft = 100*0.0929 m² = 9.29 m²
        # density = 10 / 9.29 ≈ 1.076
        density = _compute_dirt_density({"dirt": 10, "sqft": 100})
        assert density is not None
        assert abs(density - 10 / (100 * 0.0929)) < 0.001

    def test_none_when_dirt_missing(self):
        assert _compute_dirt_density({"sqft": 100}) is None

    def test_none_when_sqft_missing(self):
        assert _compute_dirt_density({"dirt": 10}) is None

    def test_none_when_sqft_zero(self):
        assert _compute_dirt_density({"dirt": 10, "sqft": 0}) is None

    def test_none_when_sqft_none(self):
        assert _compute_dirt_density({"dirt": 10, "sqft": None}) is None

    def test_zero_dirt_returns_zero(self):
        density = _compute_dirt_density({"dirt": 0, "sqft": 100})
        assert density == 0.0


class TestComputeBaseline:

    def test_returns_median_of_records(self):
        mgr = _make_manager()
        # 5 records with identical density → median = that density
        recs = _records([(5, 100)] * 5)
        baseline = mgr.compute_baseline(recs)
        assert baseline is not None
        assert abs(baseline - _compute_dirt_density({"dirt": 5, "sqft": 100})) < 0.001

    def test_none_when_fewer_than_min_records(self):
        mgr = _make_manager()
        recs = _records([(5, 100)] * (MIN_RECORDS - 1))
        assert mgr.compute_baseline(recs) is None

    def test_exactly_min_records_returns_value(self):
        mgr = _make_manager()
        recs = _records([(5, 100)] * MIN_RECORDS)
        assert mgr.compute_baseline(recs) is not None

    def test_skips_records_without_dirt_field(self):
        """Records missing dirt/sqft should not count toward MIN_RECORDS."""
        mgr = _make_manager()
        # 4 good + 10 unusable = total 14 records but only 4 valid
        recs = _records([(5, 100)] * 4) + [{"runM": 10}] * 10
        assert mgr.compute_baseline(recs) is None  # only 4 valid < MIN_RECORDS=5

    def test_median_is_correct_with_odd_count(self):
        mgr = _make_manager()
        # densities: 1, 2, 3, 4, 5 → median = 3 (in density units)
        recs = _records([(d, 107.64) for d in [1, 2, 3, 4, 5]])
        # 107.64 sqft ≈ 10 m² → density = d / 10
        baseline = mgr.compute_baseline(recs)
        assert baseline is not None
        assert abs(baseline - 3 / 10) < 0.01


class TestShouldTrigger:

    def test_false_when_no_records(self):
        mgr = _make_manager()
        triggered, _ = mgr.should_trigger([])
        assert not triggered

    def test_false_when_insufficient_records_for_baseline(self):
        mgr = _make_manager()
        recs = _records([(5, 100)] * (MIN_RECORDS - 1))
        triggered, reason = mgr.should_trigger(recs)
        assert not triggered
        assert "insufficient" in reason

    def test_false_when_density_below_threshold(self):
        mgr = _make_manager()
        # baseline ≈ 1.0 (normalized), current ≈ 1.0 → below threshold of 1.5×
        recs = _records([(5, 100)] * MIN_RECORDS)
        triggered, _ = mgr.should_trigger(recs)
        assert not triggered

    def test_true_when_density_above_threshold(self):
        mgr = _make_manager()
        # 5 baseline records at density X, 1 recent record at 2×X
        baseline_recs = _records([(5, 100)] * MIN_RECORDS)
        # Most recent record with 2× the density
        hot_record = _make_record(10, 100)  # 2× dirt events, same area
        recs = [hot_record] + baseline_recs
        triggered, reason = mgr.should_trigger(recs)
        assert triggered
        assert "density" in reason

    def test_false_within_min_gap(self):
        mgr = _make_manager()
        # Set last trigger to 1 hour ago (within MIN_GAP_HOURS)
        mgr._last_trigger_time = datetime.now(UTC) - timedelta(hours=1)
        baseline_recs = _records([(5, 100)] * MIN_RECORDS)
        hot_record = _make_record(10, 100)
        recs = [hot_record] + baseline_recs
        triggered, reason = mgr.should_trigger(recs)
        assert not triggered
        assert "gap" in reason

    def test_true_after_min_gap_elapsed(self):
        mgr = _make_manager()
        # Set last trigger to MIN_GAP_HOURS + 1h ago
        mgr._last_trigger_time = datetime.now(UTC) - timedelta(hours=MIN_GAP_HOURS + 1)
        baseline_recs = _records([(5, 100)] * MIN_RECORDS)
        hot_record = _make_record(10, 100)
        recs = [hot_record] + baseline_recs
        triggered, _ = mgr.should_trigger(recs)
        assert triggered

    def test_custom_multiplier_respected(self):
        mgr = _make_manager()
        baseline_recs = _records([(5, 100)] * MIN_RECORDS)
        # Current density ≈ 1.2× baseline — above 1.0 but below default 1.5×
        hot_record = _make_record(6, 100)
        recs = [hot_record] + baseline_recs
        # With default 1.5× multiplier → should NOT trigger
        triggered_default, _ = mgr.should_trigger(recs)
        # With 1.1× multiplier → SHOULD trigger
        triggered_low, _ = mgr.should_trigger(recs, multiplier=1.1)
        assert not triggered_default
        assert triggered_low

    def test_false_when_baseline_zero(self):
        mgr = _make_manager()
        recs = _records([(0, 100)] * MIN_RECORDS)
        triggered, reason = mgr.should_trigger(recs)
        assert not triggered
        assert "zero" in reason


class TestConstants:

    def test_min_records_is_five(self):
        assert MIN_RECORDS == 5

    def test_min_gap_hours_is_six(self):
        assert MIN_GAP_HOURS == 6

    def test_default_multiplier_is_1_5(self):
        assert TRIGGER_MULTIPLIER_DEFAULT == 1.5

    def test_conf_demand_cleaning_enabled_defined(self):
        assert CONF_DEMAND_CLEANING_ENABLED == "demand_cleaning_enabled"

    def test_conf_demand_multiplier_defined(self):
        assert CONF_DEMAND_MULTIPLIER == "demand_clean_multiplier"


class TestRoombaDataField:

    def test_dirt_threshold_manager_field_exists(self):
        """RoombaData must have dirt_threshold_manager defaulting to None."""
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(RoombaData)}
        assert "dirt_threshold_manager" in fields
        # Default must be None
        assert fields["dirt_threshold_manager"].default is None


class TestEnabledProperty:

    def test_enabled_true_when_option_set(self):
        mgr = _make_manager(options={CONF_DEMAND_CLEANING_ENABLED: True})
        assert mgr.enabled is True

    def test_enabled_false_by_default(self):
        mgr = _make_manager(options={})
        assert mgr.enabled is False

    def test_enabled_false_when_explicitly_false(self):
        mgr = _make_manager(options={CONF_DEMAND_CLEANING_ENABLED: False})
        assert mgr.enabled is False


class TestWeekdayBaseline:
    def test_weekday_baseline_built_from_records(self):
        """After ≥4 records on a weekday, baseline_by_weekday must be populated."""
        dtm = _make_dtm()
        # 5 Monday records spread across 5 consecutive weeks
        records = [
            _cloud_rec(10.0, 100.0, _recent_weekday_ts(weekday=0, weeks_back=i+1))
            for i in range(5)
        ]
        dtm._update_weekday_baseline(records, weekday=0)
        assert 0 in dtm._baseline_by_weekday, f"Baseline not set. Records: {[r['startTime'] for r in records]}"
        assert dtm._baseline_by_weekday[0] > 0

    def test_fallback_to_flat_when_insufficient_weekday_records(self):
        """With < 4 records for a weekday, weekday baseline must not be set."""
        dtm = _make_dtm()
        records = [
            _cloud_rec(10.0, 100.0, _recent_weekday_ts(weekday=0, weeks_back=i+1))
            for i in range(3)
        ]
        dtm._update_weekday_baseline(records, weekday=0)
        assert 0 not in dtm._baseline_by_weekday, "Should not set baseline with < 4 records"

    def test_monday_baseline_independent_of_tuesday(self):
        """Monday's baseline must not affect Tuesday."""
        dtm = _make_dtm()
        monday_records = [
            _cloud_rec(20.0, 100.0, _recent_weekday_ts(weekday=0, weeks_back=i+1))
            for i in range(5)
        ]
        dtm._update_weekday_baseline(monday_records, weekday=0)
        assert 0 in dtm._baseline_by_weekday
        assert 1 not in dtm._baseline_by_weekday, "Tuesday must have no baseline"

    def test_different_baselines_per_weekday(self):
        """Monday and Tuesday may have different baselines based on their records."""
        dtm = _make_dtm()
        mon_records = [
            _cloud_rec(10.0, 100.0, _recent_weekday_ts(weekday=0, weeks_back=i+1))
            for i in range(5)
        ]
        tue_records = [
            _cloud_rec(40.0, 100.0, _recent_weekday_ts(weekday=1, weeks_back=i+1))
            for i in range(5)
        ]
        all_records = mon_records + tue_records
        dtm._update_weekday_baseline(all_records, weekday=0)
        dtm._update_weekday_baseline(all_records, weekday=1)
        assert 0 in dtm._baseline_by_weekday
        assert 1 in dtm._baseline_by_weekday
        assert dtm._baseline_by_weekday[0] < dtm._baseline_by_weekday[1]

    @pytest.mark.asyncio
    async def test_persist_round_trip(self):
        """Weekday baselines must survive async_save / async_load cycle."""
        dtm = _make_dtm()
        dtm._baseline_by_weekday = {0: 1.23, 3: 2.45}
        saved_data: dict = {}

        mock_store = AsyncMock()
        async def fake_save(data):
            saved_data.update(data)
        async def fake_load():
            return saved_data

        mock_store.async_save.side_effect = fake_save
        mock_store.async_load.side_effect = fake_load

        with patch.object(dtm, "_get_store", return_value=mock_store):
            await dtm.async_save("test_entry")
            dtm2 = _make_dtm()
            with patch.object(dtm2, "_get_store", return_value=mock_store):
                await dtm2.async_load("test_entry")

        assert dtm2._baseline_by_weekday.get(0) == pytest.approx(1.23)
        assert dtm2._baseline_by_weekday.get(3) == pytest.approx(2.45)

    def test_empty_records_no_baseline_set(self):
        """Empty record list must not set any weekday baseline."""
        dtm = _make_dtm()
        dtm._update_weekday_baseline([], weekday=0)
        assert not dtm._baseline_by_weekday


class TestMissionAnomalyDetection:
    def test_rolling_stats_none_with_fewer_than_20(self):
        store = _make_store_with_records([_mission_rec() for _ in range(15)])
        assert store.compute_rolling_stats() is None

    def test_rolling_stats_correct_mean(self):
        records = [_mission_rec(duration_min=60) for _ in range(20)]
        store = _make_store_with_records(records)
        stats = store.compute_rolling_stats()
        assert stats is not None
        assert stats["duration_mean"] == pytest.approx(60.0)

    def test_is_anomalous_struggling_signature(self):
        """duration >> mean AND area << mean_area should flag anomaly."""
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": 200.0,    "area_std": 10.0,
            "recharge_mean": 0.0,  "dirt_p75": None,
        }
        # duration=85 > 60+2×5=70 ✓ AND area=175 < 200-10=190 ✓
        record = _mission_rec(duration_min=85, area_sqft=175.0)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats) is True

    def test_is_anomalous_extreme_dirt(self):
        """dirt > p75 × 2.5 should flag anomaly."""
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": 200.0,    "area_std": 10.0,
            "recharge_mean": 0.0,  "dirt_p75": 20.0,
        }
        record = _mission_rec(duration_min=60, area_sqft=200.0, dirt=55)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats) is True

    def test_is_anomalous_excessive_recharge(self):
        """recharge_min > recharge_mean + 120 should flag anomaly."""
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": 200.0,    "area_std": 10.0,
            "recharge_mean": 30.0, "dirt_p75": None,
        }
        record = _mission_rec(duration_min=60, area_sqft=200.0, recharge_min=160)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats) is True

    def test_is_anomalous_false_for_normal_record(self):
        """Normal record must not be flagged as anomalous."""
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": 200.0,    "area_std": 10.0,
            "recharge_mean": 0.0,  "dirt_p75": 20.0,
        }
        record = _mission_rec(duration_min=62, area_sqft=198.0, dirt=15)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats) is False

    def test_is_anomalous_pre_20_fallback_with_profile(self):
        """Profile fallback fires when area_mean is None (no area stats available)."""
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": None,     "area_std": 0.0,
            "recharge_mean": 0.0,  "dirt_p75": None,
        }
        profile = MagicMock()
        profile.typical_coverage_sqft = 1000
        # area=50 sqft → 4.6 m² < 1000×SQFT_TO_M2×0.4=37.2 m² → flagged
        record = _mission_rec(duration_min=60, area_sqft=50.0)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats, profile=profile) is True

    def test_is_anomalous_pre_20_fallback_not_triggered_with_area_stats(self):
        """Profile fallback must NOT fire when area_mean is available (20+ missions).

        Bug 3 fix: a legitimate partial room clean (50 sqft on a 1000 sqft robot)
        must not be flagged as anomalous when full statistical baseline exists.
        """
        stats = {
            "duration_mean": 60.0, "duration_std": 5.0,
            "area_mean": 200.0,    "area_std": 20.0,   # full baseline available
            "recharge_mean": 0.0,  "dirt_p75": None,
        }
        profile = MagicMock()
        profile.typical_coverage_sqft = 1000
        # area=50 sqft would trigger the fallback, but area_mean is set → fallback skipped
        record = _mission_rec(duration_min=62, area_sqft=50.0)
        store = _make_store_with_records([])
        assert store.is_anomalous(record, stats, profile=profile) is False

    def test_consecutive_anomalous_count(self):
        """consecutive_anomalous must return 2 when last 2 records are anomalous."""
        normal = [_mission_rec(duration_min=60, area_sqft=200.0) for _ in range(20)]
        # Add 2 anomalous records at the end: very long duration, tiny area
        anomalous = [_mission_rec(duration_min=200, area_sqft=30.0) for _ in range(2)]
        store = _make_store_with_records(normal + anomalous)
        count = store.consecutive_anomalous
        assert count >= 2

    def test_consecutive_anomalous_zero_on_normal_tail(self):
        """A normal last mission must reset consecutive_anomalous to 0."""
        records = [_mission_rec(duration_min=60, area_sqft=200.0) for _ in range(22)]
        store = _make_store_with_records(records)
        assert store.consecutive_anomalous == 0
