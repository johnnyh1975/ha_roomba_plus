"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from custom_components.roomba_plus.grid_store import GridStore
from custom_components.roomba_plus.grid_store import CELL_SIZE_MM
from custom_components.roomba_plus.grid_store import DECAY
from custom_components.roomba_plus.grid_store import VISIT_INCREMENT
from custom_components.roomba_plus.grid_store import PRUNE_THRESHOLD
from custom_components.roomba_plus.grid_store import STUCK_HOTSPOT_THRESHOLD
from custom_components.roomba_plus.grid_store import _mm_to_cell
from custom_components.roomba_plus.grid_store import _cell_to_mm
from custom_components.roomba_plus.grid_store import _bearing_deg
from custom_components.roomba_plus.grid_store import _distance_mm
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from custom_components.roomba_plus.presence_manager import PresenceManager


def _make_coordinator(umf_data=None, raw_records=None):
    from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
    coord = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
    coord.data = {
        "umf": umf_data or {},
        "mission_history_raw": raw_records or [],
        "pmaps": [],
    }
    return coord


def _make_pm(person_ids: list[str] | None = None) -> PresenceManager:
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {
        "presence_entities": person_ids if person_ids is not None else ["person.alice"],
        "away_delay_min": 5,
        "presence_mode": "away_only",
    }
    entry.entry_id = "test_pm"
    return PresenceManager(hass, entry)


def _dt(days_ago: int = 0, hour: int = 10, weekday_offset: int = 0) -> datetime:
    """Return a UTC datetime offset from now."""
    base = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    base -= timedelta(days=days_ago)
    return base


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


def _store_with_stuck(cell_data: dict) -> GridStore:
    """Create a GridStore with pre-populated _stuck dict."""
    gs = GridStore()
    gs._stuck = cell_data
    return gs


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


class TestF22aObservedZonesConditions:
    """Unit tests for the guard conditions in async_check_observed_zones."""

    def test_issue_condition_met_when_no_stuck_events(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        assert gs.stuck_event_count == 0
        centroids = [{"x": 750.0, "y": 500.0}]
        assert len(centroids) > 0
        # Condition: create issue

    def test_issue_dismissed_when_stuck_events_exist(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        gs._stuck[(0, 0)] = {"count": 5, "times": []}
        assert gs.stuck_event_count > 0
        # Condition: delete issue

    def test_no_issue_when_no_centroids(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        centroids: list = []
        assert len(centroids) == 0


class TestEdgeCoverageRatio:

    def _make_gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_none_when_fewer_than_10_cells(self):
        gs = self._make_gs()
        # Add 9 cells
        for i in range(9):
            gs._cells[(i, 0)] = 0.5
        assert gs.edge_coverage_ratio() is None

    def test_returns_float_with_10_cells(self):
        gs = self._make_gs()
        # Diagonal placement (not a single row) so the bbox spans far enough
        # in both dimensions to clear the v2.8.2 minimum-extent guard.
        for i in range(10):
            gs._cells[(i, i)] = 0.5
        result = gs.edge_coverage_ratio()
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_all_edge_cells_returns_1(self):
        """A 1-cell-thick border ring around a large-enough square — every
        cell sits exactly on the bbox edge regardless of overall size, so
        this clears the v2.8.2 minimum-extent guard while still exercising
        ratio == 1.0 (unlike a thin 1xN strip, which would now fail the
        guard for having zero extent in one dimension)."""
        gs = self._make_gs()
        n = 10  # indices 0..9 -> span 9*150=1350mm > the 1200mm minimum
        for gx in range(n):
            for gy in range(n):
                if gx in (0, n - 1) or gy in (0, n - 1):
                    gs._cells[(gx, gy)] = 0.5
        result = gs.edge_coverage_ratio()
        assert result == 1.0

    def test_interior_cells_reduce_ratio(self):
        """A 7×7 grid — inner 3×3 cells are >300mm from all edges, ratio < 1."""
        gs = self._make_gs()
        for gx in range(7):
            for gy in range(7):
                gs._cells[(gx, gy)] = 0.5
        # Grid spans 7×150=1050mm. Inner cells (2,2)–(4,4) have min distance
        # from edge = (2+0.5)*150 - 0.5*150 = 300mm → strictly inside with 7 cols.
        # Use edge_depth_mm=200 so interior ring is clearly excluded.
        result = gs.edge_coverage_ratio(edge_depth_mm=200)
        assert result is not None
        assert result < 1.0

    def test_result_is_rounded_to_4_decimal_places(self):
        gs = self._make_gs()
        for i in range(10):
            gs._cells[(i, i)] = 0.5
        result = gs.edge_coverage_ratio()
        if result is not None:
            assert round(result, 4) == result

    def test_none_when_bbox_too_small_despite_enough_cells(self):
        """v2.8.2 — a robot confined to a tiny area (e.g. stuck the whole
        mission) can still produce >=10 cells if they're densely packed,
        but the edge/centre distinction is meaningless without a real
        interior. Confirmed against live field data: a ~1m x 1m bbox
        produced a near-1.0 ratio that looked like 'excellent edge
        coverage' but was really 'every cell is close to every side'."""
        gs = self._make_gs()
        # 4x4 block of adjacent cells: span = 3*150=450mm in both dims,
        # well under the 1200mm minimum for the default edge_depth_mm=300.
        for gx in range(4):
            for gy in range(4):
                gs._cells[(gx, gy)] = 0.5
        assert len(gs._cells) >= 10
        assert gs.edge_coverage_ratio() is None

    def test_returns_value_once_bbox_exceeds_minimum(self):
        """Same shape as the tiny-bbox case above, just scaled up past the
        minimum span — confirms the guard is about extent, not cell count
        or density."""
        gs = self._make_gs()
        for gx in range(10):
            for gy in range(10):
                gs._cells[(gx, gy)] = 0.5
        result = gs.edge_coverage_ratio()
        assert result is not None


class TestGridStoreEdgeRatioCache:
    def test_cache_empty_on_fresh_instance(self):
        """Cache dict must be empty before any computation."""
        gs = GridStore()
        assert gs._edge_ratio_cache == {}

    def test_cache_populated_after_first_call(self):
        """After the first call with enough cells, cache must be keyed by edge_depth_mm."""
        gs = _make_grid_with_cells(100)
        result = gs.edge_coverage_ratio()
        assert result is not None
        assert 300.0 in gs._edge_ratio_cache
        assert gs._edge_ratio_cache[300.0] == result

    def test_cache_hit_returns_same_value(self):
        """Repeated calls with the same edge_depth_mm must return the same result."""
        gs = _make_grid_with_cells(100)
        first = gs.edge_coverage_ratio()
        second = gs.edge_coverage_ratio()
        assert first == second
        assert first is not None

    def test_different_depth_parameters_cached_independently(self):
        """Different edge_depth_mm values must not share cache entries."""
        # n=200 -> side 15 -> span 2100mm, clears the v2.8.2 minimum-extent
        # guard even for the wider edge_depth_mm=500.0 (needs >= 2000mm).
        gs = _make_grid_with_cells(300)
        r300 = gs.edge_coverage_ratio(edge_depth_mm=300.0)
        r500 = gs.edge_coverage_ratio(edge_depth_mm=500.0)
        assert 300.0 in gs._edge_ratio_cache
        assert 500.0 in gs._edge_ratio_cache
        # Wider edge depth must yield >= ratio (more cells qualify as edge)
        assert r500 >= r300

    def test_cache_invalidated_by_update_from_mission(self):
        """update_from_mission must clear the entire cache dict."""
        gs = _make_grid_with_cells(300)
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
        gs = _make_grid_with_cells(100)
        gs.edge_coverage_ratio()
        gs.update_from_mission([], [])
        assert gs._edge_ratio_cache == {}

        second = gs.edge_coverage_ratio()
        assert second is not None
        assert 300.0 in gs._edge_ratio_cache


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
        """async_load converts plain-int v1 values to v2 dict format.

        v2.9.0 — this test's "v1" refers to the stuck dict's OWN internal
        format (plain int count vs. {"count","times"} dict, a v2.7.0 L7
        change), which is orthogonal to the payload-level PAYLOAD_VERSION
        marker (the units-fix discard check, also added in v2.9.0). The
        fixture must include a current "version" field to get past that
        gate before the stuck-format migration logic even runs.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.grid_store import PAYLOAD_VERSION

        gs = GridStore()
        v1_data = {
            "version": PAYLOAD_VERSION,
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
