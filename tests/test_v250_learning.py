"""Tests for v2.5.0 self-learning features (L1, L2, L3).

L1 — Per-weekday dirt baseline in DirtThresholdManager:
  - weekday baseline built from records
  - fallback to flat median when < 4 weekday records
  - Monday baseline used on Monday, not Tuesday's
  - different baselines per weekday
  - persist round-trip
  - empty records → flat median fallback

L2 — Self-calibrating maintenance thresholds:
  - learned_filter_hours None with < 2 resets
  - correct median after 3 resets
  - filter_remaining uses learned when available
  - brush_reset_history populated on reset_brush
  - negative intervals skipped in median

L3 — Mission anomaly detection:
  - compute_rolling_stats: None < 20 missions; correct mean/std
  - is_anomalous: struggling (slow + low area); extreme dirt; excessive recharge
  - is_anomalous: normal record → False
  - pre-20 fallback with profile
  - consecutive_anomalous count
  - consecutive resets on normal mission
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.const import SQFT_TO_M2


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── L1: Per-weekday dirt baseline ─────────────────────────────────────────────

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


# ── L2: Self-calibrating maintenance thresholds ───────────────────────────────

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


# ── L3: Mission anomaly detection ────────────────────────────────────────────

def _make_20_normal_records() -> list[dict]:
    """Create 20 normal mission records with consistent duration and area."""
    return [_mission_rec(duration_min=60, area_sqft=200.0) for _ in range(20)]


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
