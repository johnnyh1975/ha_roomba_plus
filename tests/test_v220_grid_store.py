"""Tests for GridStore — EMA occupancy grid, hotspots, persistence.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.roomba_plus.grid_store import (
    GridStore,
    CELL_SIZE_MM,
    DECAY,
    VISIT_INCREMENT,
    PRUNE_THRESHOLD,
    STUCK_HOTSPOT_THRESHOLD,
    _mm_to_cell,
    _cell_to_mm,
    _bearing_deg,
    _distance_mm,
)


# ── coordinate helpers ────────────────────────────────────────────────────────

class TestCellConversions:
    def test_mm_to_cell_origin(self):
        assert _mm_to_cell(0, 0) == (0, 0)

    def test_mm_to_cell_positive(self):
        assert _mm_to_cell(151, 0) == (1, 0)

    def test_mm_to_cell_negative(self):
        assert _mm_to_cell(-1, -1) == (-1, -1)

    def test_mm_to_cell_boundary(self):
        # Exactly at cell boundary → falls into next cell
        assert _mm_to_cell(float(CELL_SIZE_MM), 0) == (1, 0)

    def test_cell_to_mm_returns_centre(self):
        x, y = _cell_to_mm(0, 0)
        assert x == CELL_SIZE_MM / 2
        assert y == CELL_SIZE_MM / 2

    def test_cell_to_mm_negative(self):
        x, y = _cell_to_mm(-1, -1)
        assert x == -CELL_SIZE_MM / 2
        assert y == -CELL_SIZE_MM / 2

    def test_bearing_north(self):
        # x=0, y>0 → bearing 0 (straight up)
        assert _bearing_deg(0, 100) == 0

    def test_bearing_east(self):
        # x>0, y=0 → bearing 90
        assert _bearing_deg(100, 0) == 90

    def test_bearing_south(self):
        # x=0, y<0 → bearing 180
        assert _bearing_deg(0, -100) == 180

    def test_distance_zero(self):
        assert _distance_mm(0, 0) == 0

    def test_distance_3_4_5(self):
        assert _distance_mm(300, 400) == 500


# ── EMA update mechanics ──────────────────────────────────────────────────────

class TestEmaUpdate:
    def test_visit_adds_weight(self):
        gs = GridStore()
        gs.update_from_mission([(75, 75)], [])
        cell = _mm_to_cell(75, 75)
        assert gs._cells[cell] == pytest.approx(VISIT_INCREMENT)

    def test_existing_cells_decay(self):
        gs = GridStore()
        cell = (0, 0)
        gs._cells[cell] = 1.0
        gs.update_from_mission([], [])
        assert gs._cells.get(cell, 0) == pytest.approx(DECAY)

    def test_prune_below_threshold(self):
        gs = GridStore()
        cell = (5, 5)
        # Set so that after decay it falls below threshold
        gs._cells[cell] = PRUNE_THRESHOLD / DECAY * 0.9
        gs.update_from_mission([], [])
        assert cell not in gs._cells

    def test_survive_above_threshold(self):
        gs = GridStore()
        cell = (5, 5)
        gs._cells[cell] = PRUNE_THRESHOLD / DECAY * 1.1
        gs.update_from_mission([], [])
        assert cell in gs._cells

    def test_weight_capped_at_1(self):
        gs = GridStore()
        cell = _mm_to_cell(75, 75)
        gs._cells[cell] = 0.95
        gs.update_from_mission([(75, 75)], [])
        assert gs._cells[cell] <= 1.0

    def test_stuck_recorded(self):
        gs = GridStore()
        gs.update_from_mission([], [(75, 75)])
        cell = _mm_to_cell(75, 75)
        assert gs._stuck[cell]["count"] == 1

    def test_stuck_accumulates_across_missions(self):
        gs = GridStore()
        for _ in range(3):
            gs.update_from_mission([], [(75, 75)])
        cell = _mm_to_cell(75, 75)
        assert gs._stuck[cell]["count"] == 3

    def test_multiple_pose_points_in_one_mission(self):
        gs = GridStore()
        gs.update_from_mission([(75, 75), (225, 75)], [])
        assert len(gs._cells) == 2

    def test_same_cell_visited_twice_still_capped(self):
        gs = GridStore()
        # Two points in the same cell — result should still be ≤ 1.0
        gs.update_from_mission([(10, 10), (20, 20)], [])
        cell = _mm_to_cell(10, 10)
        assert gs._cells.get(cell, 0) <= 1.0


# ── hotspot detection ─────────────────────────────────────────────────────────

class TestHotspots:
    def test_below_threshold_not_returned(self):
        gs = GridStore()
        cell = _mm_to_cell(75, 75)
        gs._stuck[cell] = {"count": STUCK_HOTSPOT_THRESHOLD - 1, "times": []}
        assert gs.hotspots() == []

    def test_at_threshold_returned(self):
        gs = GridStore()
        cell = _mm_to_cell(750, 750)
        gs._stuck[cell] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        result = gs.hotspots()
        assert len(result) == 1
        assert result[0]["stuck_count"] == STUCK_HOTSPOT_THRESHOLD
        assert result[0]["source"] == "stuck_events"
        assert result[0]["room_name"] is None

    def test_sorted_by_count_descending(self):
        gs = GridStore()
        gs._stuck[_mm_to_cell(75, 75)]   = {"count": 5, "times": []}
        gs._stuck[_mm_to_cell(750, 750)] = {"count": 10, "times": []}
        result = gs.hotspots()
        assert result[0]["stuck_count"] == 10
        assert result[1]["stuck_count"] == 5

    def test_bearing_and_distance_present(self):
        gs = GridStore()
        cell = _mm_to_cell(300, 400)
        gs._stuck[cell] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        result = gs.hotspots()
        assert "bearing_deg" in result[0]
        assert "distance_mm" in result[0]

    def test_gx_gy_in_result(self):
        gs = GridStore()
        cell = _mm_to_cell(300, 0)
        gs._stuck[cell] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        result = gs.hotspots()
        assert result[0]["gx"] == cell[0]
        assert result[0]["gy"] == cell[1]

    def test_custom_threshold(self):
        gs = GridStore()
        cell = _mm_to_cell(75, 75)
        gs._stuck[cell] = {"count": 2, "times": []}
        assert gs.hotspots(threshold=2) != []
        assert gs.hotspots(threshold=3) == []


# ── seed_from_observed_zones ──────────────────────────────────────────────────

class TestSeedFromObservedZones:
    def test_seeds_new_cells(self):
        gs = GridStore()
        n = gs.seed_from_observed_zones([{"x": 750.0, "y": 750.0}])
        assert n == 1
        cell = _mm_to_cell(750.0, 750.0)
        assert gs._stuck[cell]["count"] == STUCK_HOTSPOT_THRESHOLD

    def test_does_not_overwrite_existing(self):
        gs = GridStore()
        cell = _mm_to_cell(750.0, 750.0)
        gs._stuck[cell] = {"count": 99, "times": []}
        gs.seed_from_observed_zones([{"x": 750.0, "y": 750.0}])
        assert gs._stuck[cell]["count"] == 99

    def test_skips_missing_x(self):
        gs = GridStore()
        n = gs.seed_from_observed_zones([{"x": None, "y": 750.0}])
        assert n == 0

    def test_skips_missing_y(self):
        gs = GridStore()
        n = gs.seed_from_observed_zones([{"y": 750.0}])
        assert n == 0

    def test_multiple_centroids(self):
        gs = GridStore()
        n = gs.seed_from_observed_zones([
            {"x": 150.0, "y": 150.0},
            {"x": 450.0, "y": 450.0},
        ])
        assert n == 2

    def test_returns_count_of_seeded(self):
        gs = GridStore()
        # One new, one already present
        cell = _mm_to_cell(150.0, 150.0)
        gs._stuck[cell] = {"count": 5, "times": []}
        n = gs.seed_from_observed_zones([
            {"x": 150.0, "y": 150.0},
            {"x": 450.0, "y": 450.0},
        ])
        assert n == 1  # only the second was seeded


# ── diagnostic properties ─────────────────────────────────────────────────────

class TestDiagnosticProperties:
    def test_cell_count(self):
        gs = GridStore()
        gs._cells[(0, 0)] = 0.5
        gs._cells[(1, 1)] = 0.3
        assert gs.cell_count == 2

    def test_stuck_event_count(self):
        gs = GridStore()
        gs._stuck[(0, 0)] = {"count": 3, "times": []}
        gs._stuck[(1, 1)] = {"count": 7, "times": []}
        assert gs.stuck_event_count == 10

    def test_cell_count_zero_initial(self):
        assert GridStore().cell_count == 0

    def test_stuck_event_count_zero_initial(self):
        assert GridStore().stuck_event_count == 0


# ── bounding box ──────────────────────────────────────────────────────────────

class TestBoundingBox:
    def test_returns_none_when_empty(self):
        assert GridStore().bounding_box_mm() is None

    def test_single_cell(self):
        gs = GridStore()
        gs._cells[(0, 0)] = 0.5
        bbox = gs.bounding_box_mm()
        assert bbox is not None
        x_min, x_max, y_min, y_max = bbox
        assert x_min <= x_max
        assert y_min <= y_max

    def test_two_cells_span(self):
        gs = GridStore()
        gs._cells[(0, 0)] = 0.5
        gs._cells[(2, 2)] = 0.3
        x_min, x_max, y_min, y_max = gs.bounding_box_mm()
        assert x_max > x_min
        assert y_max > y_min


# ── persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self):
        gs = GridStore()
        gs._cells[(1, 2)] = 0.6
        gs._stuck[(3, 4)] = {"count": 5, "times": []}

        saved_data: dict = {}
        hass = MagicMock()
        store_mock = AsyncMock()

        async def _capture_save(data):
            saved_data.update(data)

        store_mock.async_save = _capture_save
        store_mock.async_load = AsyncMock(return_value=None)

        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_save(hass, "test_entry")

        gs2 = GridStore()
        store_mock.async_load = AsyncMock(return_value=saved_data)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs2.async_load(hass, "test_entry")

        assert gs2._cells == {(1, 2): pytest.approx(0.6)}
        assert gs2._stuck == {(3, 4): {"count": 5, "times": []}}

    @pytest.mark.asyncio
    async def test_load_empty_starts_blank(self):
        gs = GridStore()
        hass = MagicMock()
        store_mock = AsyncMock()
        store_mock.async_load = AsyncMock(return_value=None)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(hass, "test_entry")
        assert gs._cells == {}
        assert gs._stuck == {}

    @pytest.mark.asyncio
    async def test_load_corrupted_data_starts_blank(self):
        gs = GridStore()
        hass = MagicMock()
        store_mock = AsyncMock()
        store_mock.async_load = AsyncMock(return_value={"cells": {"bad_key": 0.5}})
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(hass, "test_entry")
        # Should have caught the error and started empty
        assert isinstance(gs._cells, dict)
