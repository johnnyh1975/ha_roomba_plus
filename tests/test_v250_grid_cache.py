"""Tests for v2.5.0 GridStore edge_coverage_ratio cache (P3).

Covers:
  - Cache is None on fresh instance
  - Cache hit: second call returns same object without recomputing
  - Cache invalidated after update_from_mission
  - None returned (and not cached) when < 10 cells
"""
from __future__ import annotations

import pytest

from custom_components.roomba_plus.grid_store import GridStore, CELL_SIZE_MM


def _make_grid_with_cells(n: int) -> GridStore:
    """Build a GridStore pre-populated with n cells spread across a 2D grid."""
    gs = GridStore()
    # Place cells in a grid pattern so edge/interior distinction is meaningful
    side = max(4, int(n ** 0.5) + 1)
    count = 0
    for row in range(side):
        for col in range(side):
            if count >= n:
                break
            x_mm = col * CELL_SIZE_MM * 2.0
            y_mm = row * CELL_SIZE_MM * 2.0
            gs._cells[(_cell_key(col), _cell_key(row))] = 0.5
            count += 1
    return gs


def _cell_key(i: int) -> int:
    return i


class TestGridStoreEdgeRatioCache:
    def test_cache_empty_on_fresh_instance(self):
        """Cache dict must be empty before any computation."""
        gs = GridStore()
        assert gs._edge_ratio_cache == {}

    def test_cache_populated_after_first_call(self):
        """After the first call with enough cells, cache must be keyed by edge_depth_mm."""
        gs = _make_grid_with_cells(16)
        result = gs.edge_coverage_ratio()
        assert result is not None
        assert 300.0 in gs._edge_ratio_cache
        assert gs._edge_ratio_cache[300.0] == result

    def test_cache_hit_returns_same_value(self):
        """Repeated calls with the same edge_depth_mm must return the same result."""
        gs = _make_grid_with_cells(16)
        first = gs.edge_coverage_ratio()
        second = gs.edge_coverage_ratio()
        assert first == second
        assert first is not None

    def test_different_depth_parameters_cached_independently(self):
        """Different edge_depth_mm values must not share cache entries."""
        gs = _make_grid_with_cells(16)
        r300 = gs.edge_coverage_ratio(edge_depth_mm=300.0)
        r500 = gs.edge_coverage_ratio(edge_depth_mm=500.0)
        assert 300.0 in gs._edge_ratio_cache
        assert 500.0 in gs._edge_ratio_cache
        # Wider edge depth must yield >= ratio (more cells qualify as edge)
        assert r500 >= r300

    def test_cache_invalidated_by_update_from_mission(self):
        """update_from_mission must clear the entire cache dict."""
        gs = _make_grid_with_cells(16)
        gs.edge_coverage_ratio()
        gs.edge_coverage_ratio(edge_depth_mm=500.0)
        assert len(gs._edge_ratio_cache) == 2

        gs.update_from_mission([(500.0 + i * 150.0, 500.0) for i in range(5)], [])

        assert gs._edge_ratio_cache == {}, "Cache must be empty after update_from_mission"

    def test_none_returned_and_not_cached_when_insufficient_cells(self):
        """With < 10 cells, result is None and must not enter the cache."""
        gs = _make_grid_with_cells(5)
        result = gs.edge_coverage_ratio()
        assert result is None
        assert gs._edge_ratio_cache == {}, "None result must not be cached"

    def test_cache_recomputed_after_invalidation(self):
        """After cache invalidation, next call recomputes and re-caches."""
        gs = _make_grid_with_cells(16)
        gs.edge_coverage_ratio()
        gs.update_from_mission([], [])
        assert gs._edge_ratio_cache == {}

        second = gs.edge_coverage_ratio()
        assert second is not None
        assert 300.0 in gs._edge_ratio_cache
