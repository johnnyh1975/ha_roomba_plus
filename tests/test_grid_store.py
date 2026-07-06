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
from custom_components.roomba_plus.grid_store import PAYLOAD_VERSION
from custom_components.roomba_plus.grid_store import _mm_to_cell
from custom_components.roomba_plus.grid_store import _cell_to_mm
from custom_components.roomba_plus.grid_store import _bearing_deg
from custom_components.roomba_plus.grid_store import _distance_mm
from custom_components.roomba_plus.grid_store import _FURNITURE_WINDOW_BITS
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


class TestFurnitureCandidates:
    """v3.2.0 FURNITURE — rolling coverage-history bitmask + furniture_candidates()."""

    def test_no_candidates_with_no_history(self):
        gs = GridStore()
        assert gs.furniture_candidates() == []

    def test_no_candidate_when_still_being_covered(self):
        """20 missions of consistent coverage, still being covered — not
        a candidate even though the "established" bar is easily met."""
        gs = GridStore()
        cell_point = (75, 75)
        for _ in range(25):
            gs.update_from_mission([cell_point], [])
        assert gs.furniture_candidates() == []

    def test_candidate_after_established_then_absent(self):
        """20 missions covered, then 3 consecutive missions absent —
        exactly the FURNITURE signature."""
        gs = GridStore()
        cell_point = (75, 75)
        other_point = (10000, 10000)   # keeps other cells "touched" so
                                        # this isn't a zero-mission-length edge case
        for _ in range(20):
            gs.update_from_mission([cell_point], [])
        for _ in range(3):
            gs.update_from_mission([other_point], [])
        candidates = gs.furniture_candidates()
        cells = [c["cell"] for c in candidates]
        assert _mm_to_cell(*cell_point) in cells

    def test_not_a_candidate_below_min_hits(self):
        """Established window needs >= 18/20 hits — 15/20 doesn't qualify."""
        gs = GridStore()
        cell_point = (75, 75)
        other_point = (10000, 10000)
        pattern = [True] * 15 + [False] * 5   # 15 hits out of 20
        for hit in pattern:
            gs.update_from_mission([cell_point] if hit else [], [])
        for _ in range(3):
            gs.update_from_mission([other_point], [])
        candidates = gs.furniture_candidates()
        cells = [c["cell"] for c in candidates]
        assert _mm_to_cell(*cell_point) not in cells

    def test_not_a_candidate_when_recently_covered_once(self):
        """Established, but covered at least once in the last 3 missions
        — not "gone", so not a candidate."""
        gs = GridStore()
        cell_point = (75, 75)
        for _ in range(20):
            gs.update_from_mission([cell_point], [])
        gs.update_from_mission([], [])
        gs.update_from_mission([cell_point], [])   # covered again mid-recent-window
        gs.update_from_mission([], [])
        candidates = gs.furniture_candidates()
        cells = [c["cell"] for c in candidates]
        assert _mm_to_cell(*cell_point) not in cells

    def test_insufficient_history_not_a_false_positive(self):
        """Fewer than _FURNITURE_WINDOW_BITS missions of total history —
        must NOT be flagged just because the old, never-tracked bits look
        like zeros. This is the fresh-install case."""
        from custom_components.roomba_plus.grid_store import _FURNITURE_WINDOW_BITS
        gs = GridStore()
        cell_point = (75, 75)
        other_point = (10000, 10000)
        # Only 5 missions covered, then 3 absent — total history is 8,
        # well short of _FURNITURE_WINDOW_BITS (23).
        for _ in range(5):
            gs.update_from_mission([cell_point], [])
        for _ in range(3):
            gs.update_from_mission([other_point], [])
        assert 8 < _FURNITURE_WINDOW_BITS
        candidates = gs.furniture_candidates()
        cells = [c["cell"] for c in candidates]
        assert _mm_to_cell(*cell_point) not in cells

    def test_bitmask_pruned_when_all_zero(self):
        """A cell with no history left in the tracked window at all is
        dropped from _coverage_history entirely, not kept around as 0."""
        gs = GridStore()
        cell_point = (75, 75)
        other_point = (10000, 10000)
        gs.update_from_mission([cell_point], [])
        cell = _mm_to_cell(*cell_point)
        assert cell in gs._coverage_history
        # Shift it out entirely with enough "not touched" missions
        from custom_components.roomba_plus.grid_store import _FURNITURE_WINDOW_BITS
        for _ in range(_FURNITURE_WINDOW_BITS):
            gs.update_from_mission([other_point], [])
        assert cell not in gs._coverage_history

    def test_candidate_returns_mm_coordinates(self):
        gs = GridStore()
        cell_point = (75, 75)
        other_point = (10000, 10000)
        for _ in range(20):
            gs.update_from_mission([cell_point], [])
        for _ in range(3):
            gs.update_from_mission([other_point], [])
        candidates = gs.furniture_candidates()
        match = next(c for c in candidates if c["cell"] == _mm_to_cell(*cell_point))
        assert "x_mm" in match and "y_mm" in match


class TestFurnitureReadiness:
    """v3.2.0 UX fix — GridStore.furniture_readiness(), so a fresh
    install doesn't show an identical, unexplained empty state for
    "still learning" vs "nothing to report"."""

    def test_zero_when_never_tracked(self):
        gs = GridStore()
        result = gs.furniture_readiness()
        assert result["cells_tracked"] == 0
        assert result["most_mature_cell_age"] == 0
        assert result["missions_until_first_ready"] == _FURNITURE_WINDOW_BITS

    def test_progress_after_a_few_missions(self):
        gs = GridStore()
        for _ in range(5):
            gs.update_from_mission([(75, 75)], [])
        result = gs.furniture_readiness()
        assert result["cells_tracked"] >= 1
        assert result["most_mature_cell_age"] == 5
        assert result["missions_until_first_ready"] == _FURNITURE_WINDOW_BITS - 5

    def test_ready_after_enough_missions(self):
        gs = GridStore()
        for _ in range(_FURNITURE_WINDOW_BITS):
            gs.update_from_mission([(75, 75)], [])
        result = gs.furniture_readiness()
        assert result["most_mature_cell_age"] == _FURNITURE_WINDOW_BITS
        assert result["missions_until_first_ready"] == 0

    def test_age_capped_at_window_size_even_with_more_missions(self):
        """A cell tracked for far longer than the window still reports
        age capped at the window size, not an ever-growing number."""
        gs = GridStore()
        for _ in range(_FURNITURE_WINDOW_BITS + 20):
            gs.update_from_mission([(75, 75)], [])
        result = gs.furniture_readiness()
        assert result["most_mature_cell_age"] == _FURNITURE_WINDOW_BITS

    def test_reports_the_most_mature_cell_among_several(self):
        gs = GridStore()
        # Cell A tracked for 10 missions, cell B for 3 — only touch A
        # for the first 7, then both for the last 3.
        for _ in range(7):
            gs.update_from_mission([(75, 75)], [])
        for _ in range(3):
            gs.update_from_mission([(75, 75), (10000, 10000)], [])
        result = gs.furniture_readiness()
        assert result["most_mature_cell_age"] == 10
        assert result["cells_tracked"] == 2


class TestFurnitureDismissTracking:
    """v3.2.0 FURNITURE — dismiss-suppression state helpers."""

    def test_not_suppressed_when_never_dismissed(self):
        gs = GridStore()
        assert gs.furniture_dismiss_suppressed((1, 1), "2026-07-01") is False

    def test_suppressed_immediately_after_dismiss(self):
        gs = GridStore()
        gs.record_furniture_dismissed((1, 1), "2026-07-01")
        assert gs.furniture_dismiss_suppressed((1, 1), "2026-07-01") is True

    def test_still_suppressed_within_30_days(self):
        gs = GridStore()
        gs.record_furniture_dismissed((1, 1), "2026-07-01")
        assert gs.furniture_dismiss_suppressed((1, 1), "2026-07-20") is True

    def test_no_longer_suppressed_after_30_days(self):
        gs = GridStore()
        gs.record_furniture_dismissed((1, 1), "2026-06-01")
        assert gs.furniture_dismiss_suppressed((1, 1), "2026-07-02") is False

    def test_clear_removes_suppression(self):
        gs = GridStore()
        gs.record_furniture_dismissed((1, 1), "2026-07-01")
        gs.clear_furniture_dismissed((1, 1))
        assert gs.furniture_dismiss_suppressed((1, 1), "2026-07-01") is False

    def test_suppression_is_per_cell(self):
        gs = GridStore()
        gs.record_furniture_dismissed((1, 1), "2026-07-01")
        assert gs.furniture_dismiss_suppressed((2, 2), "2026-07-01") is False


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


class TestStuckClusters:
    """v3.2.0 STUCK-HOTSPOT — GridStore.stuck_clusters()."""

    def test_empty_when_no_hotspots(self):
        gs = GridStore()
        assert gs.stuck_clusters() == []

    def test_single_isolated_cell_not_a_cluster(self):
        """A lone hotspot cell with no adjacent hotspot neighbour doesn't
        meet the min_cluster_size=2 floor."""
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        assert gs.stuck_clusters() == []

    def test_two_adjacent_cells_form_a_cluster(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        clusters = gs.stuck_clusters()
        assert len(clusters) == 1
        assert len(clusters[0]["cells"]) == 2

    def test_diagonal_adjacency_counts(self):
        """8-connectivity — diagonal neighbours also merge into one cluster."""
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 11)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        clusters = gs.stuck_clusters()
        assert len(clusters) == 1

    def test_distant_cells_form_separate_clusters(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(50, 50)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(51, 50)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        clusters = gs.stuck_clusters()
        assert len(clusters) == 2

    def test_stuck_count_summed_across_cluster(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": 5, "times": []}
        gs._stuck[(11, 10)] = {"count": 7, "times": []}
        clusters = gs.stuck_clusters()
        assert clusters[0]["stuck_count"] == 12

    def test_sorted_by_stuck_count_descending(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": 3, "times": []}
        gs._stuck[(11, 10)] = {"count": 3, "times": []}
        gs._stuck[(50, 50)] = {"count": 10, "times": []}
        gs._stuck[(51, 50)] = {"count": 10, "times": []}
        clusters = gs.stuck_clusters()
        assert clusters[0]["stuck_count"] == 20
        assert clusters[1]["stuck_count"] == 6

    def test_coverage_impact_none_without_surrounding_data(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        clusters = gs.stuck_clusters()
        assert clusters[0]["coverage_impact_pp"] is None

    def test_coverage_impact_negative_when_cluster_under_covered(self):
        """Cluster cells have low EMA weight, surrounding cells have high
        weight — the honest, computable "impact" signal."""
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._cells[(10, 10)] = 0.1
        gs._cells[(11, 10)] = 0.1
        # Surrounding cells well-covered
        for dx, dy in [(-1, 0), (0, -1), (0, 1), (1, 1), (12, 10), (9, 10)]:
            gs._cells[(10 + dx, 10 + dy)] = 0.9
        clusters = gs.stuck_clusters()
        impact = clusters[0]["coverage_impact_pp"]
        assert impact is not None
        assert impact < 0

    def test_custom_min_cluster_size(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        assert gs.stuck_clusters(min_cluster_size=1) != []
        assert gs.stuck_clusters(min_cluster_size=2) == []

    def test_centroid_is_mean_of_member_cells(self):
        gs = GridStore()
        gs._stuck[(10, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        gs._stuck[(11, 10)] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
        clusters = gs.stuck_clusters()
        x1, y1 = _cell_to_mm(10, 10)
        x2, y2 = _cell_to_mm(11, 10)
        assert clusters[0]["x_mm"] == pytest.approx((x1 + x2) / 2)
        assert clusters[0]["y_mm"] == pytest.approx((y1 + y2) / 2)


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
    async def test_coverage_history_roundtrip(self):
        """v3.2.0 FURNITURE — coverage_history/coverage_history_age
        survive save/load."""
        gs = GridStore()
        gs._coverage_history[(1, 2)] = 0b1010101
        gs._coverage_history_age[(1, 2)] = 23

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

        assert gs2._coverage_history == {(1, 2): 0b1010101}
        assert gs2._coverage_history_age == {(1, 2): 23}

    @pytest.mark.asyncio
    async def test_coverage_history_missing_from_old_payload_defaults_empty(self):
        """A payload saved before FURNITURE existed has no
        coverage_history key at all — must load as empty, not error."""
        gs = GridStore()
        hass = MagicMock()
        store_mock = AsyncMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION, "cells": {}, "stuck": {},
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(hass, "test_entry")
        assert gs._coverage_history == {}
        assert gs._coverage_history_age == {}

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

    @pytest.mark.asyncio
    async def test_last_processed_nmssn_roundtrip(self):
        """v3.4.0 GS-SMART-COVERAGE — watermark survives save/load."""
        gs = GridStore()
        gs.record_processed_nmssn(42)

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

        assert gs2.last_processed_nmssn == 42

    @pytest.mark.asyncio
    async def test_last_processed_nmssn_missing_from_old_payload_defaults_zero(self):
        """A payload saved before GS-SMART-COVERAGE existed has no
        last_processed_nmssn key at all — must load as 0, not error."""
        gs = GridStore()
        hass = MagicMock()
        store_mock = AsyncMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION, "cells": {}, "stuck": {},
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(hass, "test_entry")
        assert gs.last_processed_nmssn == 0

    @pytest.mark.asyncio
    async def test_last_processed_nmssn_reset_on_corrupted_load(self):
        gs = GridStore()
        gs.record_processed_nmssn(99)
        hass = MagicMock()
        store_mock = AsyncMock()
        # Passes the version check, then fails while parsing "cells"
        # (no comma to split on) — exercises the actual exception-handler
        # reset path, not the earlier version-mismatch bail-out.
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION, "cells": {"bad_key_no_comma": 0.5},
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(hass, "test_entry")
        assert gs.last_processed_nmssn == 0


class TestRecordProcessedNmssn:
    """v3.4.0 GS-SMART-COVERAGE — shared watermark, written by both the
    live path (image.py) and the cloud-backfill path (callbacks.py) so
    neither re-processes a mission the other already handled."""

    def test_starts_at_zero(self):
        assert GridStore().last_processed_nmssn == 0

    def test_advances_on_higher_value(self):
        gs = GridStore()
        gs.record_processed_nmssn(10)
        assert gs.last_processed_nmssn == 10
        gs.record_processed_nmssn(15)
        assert gs.last_processed_nmssn == 15

    def test_never_moves_backwards(self):
        gs = GridStore()
        gs.record_processed_nmssn(15)
        gs.record_processed_nmssn(10)  # e.g. an out-of-order cloud record
        assert gs.last_processed_nmssn == 15

    def test_equal_value_is_a_noop(self):
        gs = GridStore()
        gs.record_processed_nmssn(10)
        gs.record_processed_nmssn(10)
        assert gs.last_processed_nmssn == 10

    def test_none_is_ignored(self):
        gs = GridStore()
        gs.record_processed_nmssn(10)
        gs.record_processed_nmssn(None)
        assert gs.last_processed_nmssn == 10

    def test_non_numeric_is_ignored_not_raised(self):
        gs = GridStore()
        gs.record_processed_nmssn("not_a_number")
        assert gs.last_processed_nmssn == 0

    def test_numeric_string_is_accepted(self):
        """Cloud/MQTT fields often arrive as strings — same tolerance as
        the rest of the codebase's _safe_int-style handling."""
        gs = GridStore()
        gs.record_processed_nmssn("42")
        assert gs.last_processed_nmssn == 42

    def test_live_and_cloud_path_share_one_watermark(self):
        """The exact double-counting scenario from the GS-SMART-COVERAGE
        plan §2: whichever path processes a mission first should make
        the other path's candidate filter skip it."""
        gs = GridStore()
        # Live path (image.py) processes mission nMssn=50 first.
        gs.record_processed_nmssn(50)
        # Cloud-backfill path's candidate filter for the SAME mission:
        candidate_nmssn = 50
        assert not (candidate_nmssn > gs.last_processed_nmssn)  # correctly skipped


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


class TestDiskFilledCells:
    """v2.9.0 (DISK-FILL) — _disk_filled_cells() pure-geometry helper.

    Each pose point's actual swept footprint (robot chassis radius), not
    just the single cell its centre happens to land in. Confirmed via real
    data (June 2026) that the old single-cell marking left the trace too
    sparse to even form one connected blob under 8-connectivity, and
    undercounted both edge_coverage_ratio and coverage_by_polygon.
    """

    def test_single_point_covers_multiple_cells_for_realistic_radius(self):
        from custom_components.roomba_plus.grid_store import (
            _disk_filled_cells, CELL_SIZE_MM,
        )
        # Robot radius ~176mm (353mm diameter / 2) is larger than one
        # 150mm cell — a single pose point must touch more than one cell.
        result = _disk_filled_cells([(1000.0, 1000.0)], radius_mm=176.0)
        assert len(result) > 1

    def test_radius_zero_still_returns_centre_cell_at_minimum(self):
        from custom_components.roomba_plus.grid_store import _disk_filled_cells
        result = _disk_filled_cells([(1000.0, 1000.0)], radius_mm=1.0)
        assert (6, 6) in result  # 1000 // 150 = 6

    def test_distance_check_excludes_far_corner_cells(self):
        """Disk-fill must use a real circular distance check, not just a
        bounding-box square — a corner cell at the edge of the bbox but
        outside the actual circle radius must NOT be included."""
        from custom_components.roomba_plus.grid_store import _disk_filled_cells
        # A small radius (just over half a cell) should NOT reach the
        # diagonal corner cells of its bounding box.
        result = _disk_filled_cells([(0.0, 0.0)], radius_mm=80.0)
        # Cell (-1,-1)'s centre is at (-75,-75), distance = sqrt(75²+75²)
        # ≈ 106mm > 80mm radius — must be excluded.
        assert (-1, -1) not in result

    def test_dense_consecutive_points_form_one_connected_blob(self):
        """The whole point of disk-fill: a realistic dense pose trail
        (median step ~67mm, matching real captured data) must form ONE
        connected component under 8-connectivity, unlike the old
        single-cell marking (confirmed empirically to fragment into 19+
        pieces on real data)."""
        from custom_components.roomba_plus.grid_store import _disk_filled_cells
        from collections import deque

        # Simulate a dense, winding path — 67mm steps, gentle turns.
        points = []
        x, y = 0.0, 0.0
        import math as _m
        for i in range(100):
            angle = i * 0.15
            x += 67 * _m.cos(angle)
            y += 67 * _m.sin(angle)
            points.append((x, y))

        touched = _disk_filled_cells(points, radius_mm=176.0)

        # 8-connectivity flood fill from one cell must reach all of them.
        start = next(iter(touched))
        visited = {start}
        q = deque([start])
        while q:
            cx, cy = q.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nb = (cx + dx, cy + dy)
                    if nb in touched and nb not in visited:
                        visited.add(nb)
                        q.append(nb)
        assert visited == touched, (
            f"Disk-filled trace must be one connected blob — "
            f"{len(touched) - len(visited)} cell(s) unreachable"
        )


class TestUpdateFromMissionDiskFill:
    """v2.9.0 (DISK-FILL) — update_from_mission()'s robot_radius_mm parameter."""

    def _make_gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_none_radius_preserves_old_single_cell_behaviour(self):
        """Backward compatibility: omitting robot_radius_mm must behave
        exactly as before — existing callers/tests that don't pass it
        must see no change."""
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [])
        assert len(gs._cells) == 1
        assert (6, 6) in gs._cells

    def test_with_radius_marks_more_cells_than_single_point(self):
        gs_old = self._make_gs()
        gs_old.update_from_mission([(1000.0, 1000.0)], [])

        gs_new = self._make_gs()
        gs_new.update_from_mission([(1000.0, 1000.0)], [], robot_radius_mm=176.0)

        assert len(gs_new._cells) > len(gs_old._cells)

    def test_increment_applied_once_per_cell_despite_overlapping_disks(self):
        """Two adjacent pose points whose disks both touch the same cell
        must not double-increment that cell within a single mission —
        preserves the existing 'one VISIT_INCREMENT per mission per cell'
        semantics, just with disk-based touching instead of single-point."""
        from custom_components.roomba_plus.grid_store import VISIT_INCREMENT
        gs = self._make_gs()
        # Two points 30mm apart — their 176mm-radius disks heavily overlap.
        gs.update_from_mission(
            [(1000.0, 1000.0), (1030.0, 1000.0)], [], robot_radius_mm=176.0,
        )
        # Every touched cell must have EXACTLY one increment's worth of
        # weight (mission starts from an empty store), never more.
        for weight in gs._cells.values():
            assert weight == VISIT_INCREMENT


class TestDualGridStructureCells:
    """v3.2.1 DUAL-GRID — structure_cells: a second, centre-only cell-weight
    accumulator, independent of robot_radius_mm disk-fill. Data-collection
    scaffolding for a future room-segmentation input; not yet consumed
    anywhere, so these tests only cover the accumulator itself."""

    def _make_gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_initial_state_empty(self):
        gs = self._make_gs()
        assert gs.structure_cells == {}

    def test_centre_only_regardless_of_robot_radius(self):
        """structure_cells must mark ONLY the exact centre cell, even when
        robot_radius_mm makes self.cells (disk-filled) mark many more —
        this is the entire point of the dual accumulator."""
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [], robot_radius_mm=500.0)
        assert len(gs.structure_cells) == 1
        assert (6, 6) in gs.structure_cells
        assert len(gs.cells) > 1, "sanity: disk-fill must actually be wider here"

    def test_populated_even_when_robot_radius_is_none(self):
        """structure_cells accumulates independent of whether the caller
        passes robot_radius_mm at all — it never disk-fills, so there's
        no reason for it to depend on that parameter's presence."""
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [])
        assert (6, 6) in gs.structure_cells

    def test_decays_independently_of_cells(self):
        """A cell present in both accumulators must decay on its own
        weight in EACH dict — the two must not share state."""
        from custom_components.roomba_plus.grid_store import DECAY
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [], robot_radius_mm=500.0)
        w_cells_before = gs.cells[(6, 6)]
        w_struct_before = gs.structure_cells[(6, 6)]
        # Second mission far away — decays both dicts' existing entries,
        # doesn't touch (6,6) in either.
        gs.update_from_mission([(9000.0, 9000.0)], [], robot_radius_mm=500.0)
        assert gs.cells[(6, 6)] == pytest.approx(w_cells_before * DECAY)
        assert gs.structure_cells[(6, 6)] == pytest.approx(w_struct_before * DECAY)

    def test_prunes_below_threshold_independently(self):
        """A cell can be pruned from structure_cells while still present
        in cells (disk-filled neighbours keep cells populated longer) —
        the two prune independently, no shared prune list."""
        from custom_components.roomba_plus.grid_store import PRUNE_THRESHOLD
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [], robot_radius_mm=500.0)
        gs._structure_cells[(6, 6)] = PRUNE_THRESHOLD / 2  # force below threshold
        gs.update_from_mission([(9000.0, 9000.0)], [], robot_radius_mm=500.0)
        assert (6, 6) not in gs.structure_cells

    @pytest.mark.asyncio
    async def test_persists_across_save_load_roundtrip(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        gs = self._make_gs()
        gs.update_from_mission([(1000.0, 1000.0)], [], robot_radius_mm=500.0)
        saved = {}
        async def fake_save(data):
            saved.update(data)
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_save(MagicMock(), "e1")
        assert "structure_cells" in saved
        assert saved["structure_cells"] == {"6,6": pytest.approx(0.30)}

        gs2 = self._make_gs()
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock2):
            await gs2.async_load(MagicMock(), "e1")
        assert gs2.structure_cells == gs.structure_cells

    @pytest.mark.asyncio
    async def test_old_payload_without_structure_cells_loads_cleanly(self):
        """v3.2.1 — additive field, no PAYLOAD_VERSION bump (same
        precedent as the v3.2.0 FURNITURE fields): a payload saved before
        this existed has no 'structure_cells' key at all and must load
        as an empty dict, not raise."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.grid_store import PAYLOAD_VERSION
        gs = self._make_gs()
        old_payload = {
            "version": PAYLOAD_VERSION,
            "cells": {"1,1": 0.5},
            "stuck": {},
        }
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=old_payload)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await gs.async_load(MagicMock(), "e1")
        assert gs.structure_cells == {}
        assert gs.cells == {(1, 1): 0.5}


class TestCoverageAccuracyWithDiskFill:
    """v2.9.0 (DISK-FILL) — confirms the fix actually improves the two
    real, user-visible calculations that depend on GridStore cell density:
    coverage_by_polygon() (per-room % in mission history) and
    edge_coverage_ratio() (F12d over-cleaning-centre diagnostic).
    """

    def _make_gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_coverage_by_polygon_higher_with_disk_fill(self):
        """A sparse single-cell-per-point trace through a room undercounts
        coverage; disk-filling the same path should report a higher (more
        accurate) fraction for the same physical sweep."""
        import math as _m

        # A short, tight loop confined to roughly the first third of a
        # large 6000x6000mm room — deliberately leaves most of the
        # polygon untouched by either variant, so there's room for a
        # real difference between the two coverage fractions to show up
        # (a path that already saturates both variants to 100% can't
        # demonstrate the fix).
        points = []
        x, y = 600.0, 600.0
        for i in range(40):
            angle = i * 0.3
            x += 67 * _m.cos(angle)
            y += 67 * _m.sin(angle)
            points.append((x, y))

        polygon = {"room1": [(0.0, 0.0), (6000.0, 0.0), (6000.0, 6000.0), (0.0, 6000.0)]}

        gs_old = self._make_gs()
        gs_old.update_from_mission(points, [])
        frac_old = gs_old.coverage_by_polygon(polygon)["room1"]

        gs_new = self._make_gs()
        gs_new.update_from_mission(points, [], robot_radius_mm=176.0)
        frac_new = gs_new.coverage_by_polygon(polygon)["room1"]

        assert frac_new > frac_old, (
            f"Disk-fill should report higher coverage for the same sweep "
            f"(old={frac_old:.2f}, new={frac_new:.2f})"
        )


class TestGridStoreCorruptionResilience:
    """Stress-test (real-store bug-hunt): async_load must survive corrupted
    persisted data with a valid version (so it passes the version gate and
    reaches deserialization) without raising.

    Found via field-data stress test: cells=null → .items() AttributeError;
    cells={"x,y": null} → float(None) TypeError. The original except
    (KeyError, ValueError, IndexError) caught neither.
    """
    from custom_components.roomba_plus.grid_store import PAYLOAD_VERSION as _PV

    def _load_with(self, payload):
        async def mock_load_fn():
            return payload
        store_mock = MagicMock()
        store_mock.async_load = mock_load_fn
        store_mock.async_save = AsyncMock()
        gs = GridStore()
        hass = MagicMock()
        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=store_mock,
        ):
            import asyncio
            asyncio.get_event_loop().run_until_complete(gs.async_load(hass, "e1"))
        return gs

    def test_null_cells_with_valid_version(self):
        gs = self._load_with({"version": self._PV, "cells": None, "stuck": None})
        assert gs._cells == {}
        assert gs._stuck == {}

    def test_null_cell_value(self):
        gs = self._load_with({
            "version": self._PV,
            "cells": {"1,2": None, "3,4": 5.0},
            "stuck": {},
        })
        # Corrupt entry must not crash the whole load — starts empty on error
        assert isinstance(gs._cells, dict)

    def test_null_times_in_stuck(self):
        gs = self._load_with({
            "version": self._PV,
            "cells": {},
            "stuck": {"1,1": {"count": 2, "times": None}},
        })
        assert isinstance(gs._stuck, dict)


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 STORE-ENCAP — GridStore accessor contracts
# ─────────────────────────────────────────────────────────────────────────────

class TestStoreEncapGridAccessors:
    """v3.3.0 STORE-ENCAP — furniture_dismissed_cells /
    is_furniture_dismissed / stuck_count close the gaps in the existing
    public furniture/stuck API so repairs.py needs no private access."""

    def test_furniture_dismiss_accessors(self):
        gs = GridStore()
        assert gs.furniture_dismissed_cells() == ()
        assert gs.is_furniture_dismissed((3, 4)) is False
        gs.record_furniture_dismissed((3, 4), "2026-07-01T10:00:00+00:00")
        gs.record_furniture_dismissed((-2, 7), "2026-07-02T10:00:00+00:00")
        assert set(gs.furniture_dismissed_cells()) == {(3, 4), (-2, 7)}
        assert gs.is_furniture_dismissed((3, 4)) is True
        # Snapshot semantics: safe to clear while iterating the result
        for cell in gs.furniture_dismissed_cells():
            gs.clear_furniture_dismissed(cell)
        assert gs.furniture_dismissed_cells() == ()

    def test_stuck_count_structured_legacy_and_unknown(self):
        gs = GridStore()
        assert gs.stuck_count((0, 0)) == 0
        gs._stuck[(1, 1)] = {"count": 4, "times": []}   # v2.7.0+ structured
        gs._stuck[(2, 2)] = 7                            # legacy plain count
        gs._stuck[(3, 3)] = {"times": []}                # degraded: no count key
        assert gs.stuck_count((1, 1)) == 4
        assert gs.stuck_count((2, 2)) == 7
        assert gs.stuck_count((3, 3)) == 0
        assert gs.stuck_count((9, 9)) == 0
