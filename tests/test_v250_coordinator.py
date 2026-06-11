"""Tests for v2.5.0 cloud coordinator improvements (P4, TE1, IA74-PMAP).

Covers:
  P4  — daily_dirt_density cached on coordinator:
        populated after successful update, empty on init, consumed correctly
  TE1 — _parse_time_estimates: two-pass, one-pass, low-confidence filtered, empty list
  IA74-PMAP — seed_pmap_id_from_local: seeds from pmaps[0], active_pmap_id fallback
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus.cloud_coordinator import (
    IrobotCloudCoordinator,
    _compute_daily_dirt_density,
    _parse_time_estimates,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_coordinator() -> IrobotCloudCoordinator:
    """Build a coordinator with minimal mocks, patching the aiohttp session."""
    hass = MagicMock()
    hass.config.country = "US"
    entry = MagicMock()
    with patch(
        "custom_components.roomba_plus.cloud_coordinator.async_get_clientsession",
        return_value=MagicMock(),
    ):
        coord = IrobotCloudCoordinator(
            hass=hass,
            config_entry=entry,
            blid="test_blid",
            username="user@test.com",
            password="secret",
            has_pmaps=True,
            mission_store=None,
        )
    return coord


def _raw_record(dirt: float, sqft: float, ts: int = 1748786400) -> dict:
    """Minimal raw cloud record with dirt, sqft, startTime."""
    return {"dirt": dirt, "sqft": sqft, "startTime": ts}


# ── P4: daily_dirt_density cache ──────────────────────────────────────────────

class TestDailyDirtDensityCache:
    def test_empty_on_init(self):
        """daily_dirt_density must be an empty dict on a fresh coordinator."""
        coord = _make_coordinator()
        assert coord.daily_dirt_density == {}

    def test_compute_from_records(self):
        """_compute_daily_dirt_density groups by date and returns median per day."""
        # 2026-06-01 00:00:00 UTC → timestamp 1748736000
        ts_day1 = 1748736000
        # 2026-06-02 00:00:00 UTC → timestamp 1748822400
        ts_day2 = 1748822400
        records = [
            _raw_record(10.0, 100.0, ts_day1),   # day1: density = 10/(100*0.09290304)
            _raw_record(20.0, 100.0, ts_day1),   # day1: second record
            _raw_record(15.0, 100.0, ts_day2),   # day2
        ]
        result = _compute_daily_dirt_density(records)
        assert len(result) == 2
        # day1 median of [10/(100*SQFT_TO_M2), 20/(100*SQFT_TO_M2)]
        from custom_components.roomba_plus.const import SQFT_TO_M2
        expected_d1 = ((10 / (100 * SQFT_TO_M2)) + (20 / (100 * SQFT_TO_M2))) / 2
        day1_key = next(k for k in result if "01" in k)
        assert abs(result[day1_key] - expected_d1) < 0.01

    def test_empty_records_returns_empty(self):
        """Empty record list must return empty dict."""
        assert _compute_daily_dirt_density([]) == {}

    def test_records_without_dirt_skipped(self):
        """Records with no dirt field must be silently skipped."""
        records = [{"sqft": 100.0, "startTime": 1748736000}]
        assert _compute_daily_dirt_density(records) == {}


# ── TE1: _parse_time_estimates ────────────────────────────────────────────────

class TestParseTimeEstimates:
    def test_two_pass_entry_parsed(self):
        raw = [
            {
                "unit": "seconds",
                "estimate": 2639,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": True},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["two_pass_sec"] == 2639
        assert result["one_pass_sec"] is None

    def test_one_pass_entry_parsed(self):
        raw = [
            {
                "unit": "seconds",
                "estimate": 1319,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["one_pass_sec"] == 1319
        assert result["two_pass_sec"] is None

    def test_low_confidence_entry_filtered(self):
        """Entries with confidence != GOOD_CONFIDENCE must be excluded."""
        raw = [
            {
                "unit": "seconds",
                "estimate": 999,
                "confidence": "LOW_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["one_pass_sec"] is None
        assert result["two_pass_sec"] is None

    def test_empty_list_returns_both_none(self):
        """Auto mode has no entries — both keys must be None."""
        result = _parse_time_estimates([])
        assert result == {"one_pass_sec": None, "two_pass_sec": None}

    def test_non_list_input_returns_both_none(self):
        """Unexpected API shape (string, dict, None) must return both None, not raise."""
        assert _parse_time_estimates(None) == {"one_pass_sec": None, "two_pass_sec": None}
        assert _parse_time_estimates("bad") == {"one_pass_sec": None, "two_pass_sec": None}
        assert _parse_time_estimates({}) == {"one_pass_sec": None, "two_pass_sec": None}

    def test_both_passes_present(self):
        """Both one-pass and two-pass entries can be present simultaneously."""
        raw = [
            {
                "unit": "seconds",
                "estimate": 2639,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": True},
            },
            {
                "unit": "seconds",
                "estimate": 1319,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            },
        ]
        result = _parse_time_estimates(raw)
        assert result["two_pass_sec"] == 2639
        assert result["one_pass_sec"] == 1319


# ── IA74-PMAP: local pmap seeding ────────────────────────────────────────────

class TestPmapLocalSeed:
    def test_seed_sets_seeded_pmap_id(self):
        """seed_pmap_id_from_local must set _seeded_pmap_id from pmaps[0]."""
        coord = _make_coordinator()
        coord.data = None   # no cloud data yet
        reported_state = {"pmaps": [{"2Bly_kGURy6OcUVTX7FN3w": "ABC_v1"}]}
        coord.seed_pmap_id_from_local(reported_state)
        assert coord._seeded_pmap_id == "2Bly_kGURy6OcUVTX7FN3w"

    def test_active_pmap_id_returns_seed_when_data_none(self):
        """active_pmap_id must return _seeded_pmap_id when coordinator data is None."""
        coord = _make_coordinator()
        coord.data = None
        coord._seeded_pmap_id = "seeded_pmap_abc"
        assert coord.active_pmap_id == "seeded_pmap_abc"

    def test_active_pmap_id_prefers_cloud_data_over_seed(self):
        """When cloud data is present, active_pmap_id must use it, not the seed."""
        coord = _make_coordinator()
        coord._seeded_pmap_id = "old_seed"
        coord.data = {
            "pmaps": [{
                "active_pmapv_details": {
                    "active_pmapv": {"pmap_id": "real_cloud_pmap"},
                    "regions": [],
                }
            }],
            "mission_history_raw": [],
        }
        assert coord.active_pmap_id == "real_cloud_pmap"

    def test_seed_skipped_when_cloud_data_present(self):
        """seed_pmap_id_from_local must not overwrite when data is already set."""
        coord = _make_coordinator()
        coord.data = {"pmaps": [], "mission_history_raw": []}
        coord._seeded_pmap_id = "existing_seed"
        coord.seed_pmap_id_from_local({"pmaps": [{"new_pmap": "v1"}]})
        # Seed must not have changed
        assert coord._seeded_pmap_id == "existing_seed"

    def test_seed_handles_missing_pmaps_gracefully(self):
        """seed_pmap_id_from_local must not raise when pmaps is absent."""
        coord = _make_coordinator()
        coord.data = None
        coord.seed_pmap_id_from_local({})   # no pmaps key
        assert coord._seeded_pmap_id is None
