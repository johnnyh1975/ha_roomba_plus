"""L7 (v2.7.0) — Stuck pattern time-correlation tests.

Tests for GridStore format migration, time recording, and pattern detection.
"""
import pytest
from custom_components.roomba_plus.grid_store import GridStore


def _store_with_stuck(cell_data: dict) -> GridStore:
    """Create a GridStore with pre-populated _stuck dict."""
    gs = GridStore()
    gs._stuck = cell_data
    return gs


class TestGridStoreL7Format:
    """_stuck dict uses new structured format."""

    def test_new_store_has_empty_stuck(self):
        gs = GridStore()
        assert gs._stuck == {}

    def test_update_from_mission_records_count(self):
        gs = GridStore()
        gs.update_from_mission(
            pose_points=[(0.0, 0.0)],
            stuck_points=[(150.0, 150.0)],
        )
        assert len(gs._stuck) == 1
        cell = list(gs._stuck.keys())[0]
        assert gs._stuck[cell]["count"] == 1
        assert gs._stuck[cell]["times"] == []  # no started_at provided

    def test_update_from_mission_records_time_when_started_at_given(self):
        gs = GridStore()
        # Monday = weekday 0, hour 9 — caller (image.py) computes this from the
        # mission start timestamp and passes the tuple directly (Bug 1 fix v2.7.0)
        gs.update_from_mission(
            pose_points=[(0.0, 0.0)],
            stuck_points=[(150.0, 150.0)],
            stuck_wh=(0, 9),  # (Monday, 09:xx)
        )
        cell = list(gs._stuck.keys())[0]
        assert gs._stuck[cell]["count"] == 1
        times = gs._stuck[cell]["times"]
        assert len(times) == 1
        weekday, hour = times[0]
        assert weekday == 0   # Monday
        assert hour == 9

    def test_stuck_event_count_sums_counts(self):
        gs = _store_with_stuck({
            (0, 0): {"count": 3, "times": []},
            (1, 1): {"count": 5, "times": []},
        })
        assert gs.stuck_event_count == 8

    def test_async_load_migrates_v1_int_format(self):
        """async_load converts plain-int v1 values to v2 dict format."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        gs = GridStore()
        v1_data = {
            "cells": {"0,0": 0.5},
            "stuck": {"1,1": 3, "2,2": 7},  # v1 plain int format
        }
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value=v1_data)

        with patch("homeassistant.helpers.storage.Store", return_value=mock_store):
            asyncio.get_event_loop().run_until_complete(
                gs.async_load(MagicMock(), "test_entry")
            )

        assert gs._stuck[(1, 1)] == {"count": 3, "times": []}
        assert gs._stuck[(2, 2)] == {"count": 7, "times": []}


class TestStuckPattern:
    """GridStore.stuck_pattern() pattern detection."""

    def test_no_pattern_with_insufficient_stucks(self):
        gs = _store_with_stuck({
            (0, 0): {"count": 5, "times": [[0, 9]] * 5},  # below threshold of 8
        })
        assert gs.stuck_pattern(threshold=8) is None

    def test_pattern_detected_with_dominant_slot(self):
        # 10 stucks, 8 on Monday morning (weekday=0, hour=9) → 80% > 60%
        gs = _store_with_stuck({
            (0, 0): {
                "count": 10,
                "times": [[0, 9]] * 8 + [[2, 14], [4, 16]],
            }
        })
        result = gs.stuck_pattern(threshold=8, dominant_pct=0.60)
        assert result is not None
        assert (0, 0) in result
        assert result[(0, 0)] == (0, 9)

    def test_no_pattern_when_times_spread_evenly(self):
        # 10 stucks spread across different slots — no dominant slot
        gs = _store_with_stuck({
            (0, 0): {
                "count": 10,
                "times": [[i % 7, i % 24] for i in range(10)],
            }
        })
        result = gs.stuck_pattern(threshold=8, dominant_pct=0.60)
        assert result is None

    def test_no_pattern_when_no_time_data(self):
        # count >= threshold but times list is empty (migrated from v1)
        gs = _store_with_stuck({
            (0, 0): {"count": 10, "times": []},
        })
        assert gs.stuck_pattern(threshold=8) is None
