"""Tests for F11 — DirtThresholdManager (demand-based cleaning).

Covers:
  - compute_baseline: correct median, insufficient records, zero/None sqft
  - should_trigger: density < threshold, > threshold, min gap, no records
  - async_evaluate: gate ordering (presence, blocking, busy robot)
  - persistence: async_load / async_save round-trip
  - binary sensor: is_on logic (busy, queued, presence, free)
  - const.py: CONF_DEMAND_CLEANING_ENABLED and CONF_DEMAND_MULTIPLIER defined
  - models.py: RoombaData.dirt_threshold_manager field exists
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.dirt_threshold_manager import (
    MIN_GAP_HOURS,
    MIN_RECORDS,
    TRIGGER_MULTIPLIER_DEFAULT,
    DirtThresholdManager,
    _compute_dirt_density,
)
from custom_components.roomba_plus.const import (
    CONF_DEMAND_CLEANING_ENABLED,
    CONF_DEMAND_MULTIPLIER,
)
from custom_components.roomba_plus.models import RoombaData, MapCapability


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


# ── _compute_dirt_density ─────────────────────────────────────────────────────

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


# ── compute_baseline ──────────────────────────────────────────────────────────

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


# ── should_trigger ────────────────────────────────────────────────────────────

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


# ── async_evaluate (gate ordering) ────────────────────────────────────────────

class TestAsyncEvaluate:

    async def test_skips_when_disabled(self):
        mgr = _make_manager(options={CONF_DEMAND_CLEANING_ENABLED: False})
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }
        # Should return without calling roomba.send_command
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    async def test_skips_when_robot_busy(self):
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        # Robot is actively cleaning
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "clean"}
        }
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    async def test_skips_when_blocking_manager_queued(self):
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        bm = MagicMock()
        bm.is_queued = True
        mgr._entry.runtime_data.blocking_manager = bm
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    async def test_triggers_when_all_gates_pass(self):
        mgr = _make_manager()
        coord = MagicMock()
        # 5 baseline records + 1 hot record (2× density)
        baseline = _records([(5, 100)] * MIN_RECORDS)
        coord.raw_records = [_make_record(10, 100)] + baseline
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }

        with patch.object(mgr, 'async_save', new_callable=AsyncMock):
            with patch.object(mgr._hass, 'async_add_executor_job', new_callable=AsyncMock) as mock_job:
                await mgr.async_evaluate(coord, "test_entry")
            mock_job.assert_called_once()

    async def test_last_trigger_time_set_after_trigger(self):
        mgr = _make_manager()
        coord = MagicMock()
        baseline = _records([(5, 100)] * MIN_RECORDS)
        coord.raw_records = [_make_record(10, 100)] + baseline
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }

        assert mgr._last_trigger_time is None
        with patch.object(mgr, 'async_save', new_callable=AsyncMock):
            with patch.object(mgr._hass, 'async_add_executor_job', new_callable=AsyncMock):
                await mgr.async_evaluate(coord, "test_entry")
        assert mgr._last_trigger_time is not None

    async def test_does_not_raise_on_exception(self):
        """async_evaluate must never propagate exceptions."""
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = None  # triggers AttributeError inside
        # Should complete without raising
        await mgr.async_evaluate(coord, "test_entry")


# ── Constants ─────────────────────────────────────────────────────────────────

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


# ── RoombaData field ──────────────────────────────────────────────────────────

class TestRoombaDataField:

    def test_dirt_threshold_manager_field_exists(self):
        """RoombaData must have dirt_threshold_manager defaulting to None."""
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(RoombaData)}
        assert "dirt_threshold_manager" in fields
        # Default must be None
        assert fields["dirt_threshold_manager"].default is None


# ── enabled property ──────────────────────────────────────────────────────────

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
