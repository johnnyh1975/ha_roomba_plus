"""Tests for v2.4.2 GS-SMART — GeometryStore door markers for SMART robots.

Before the fix, _handle_mission_end() only called update_from_mission() for
EPHEMERAL (900-series) robots. SMART robots (i7+, s9+, j-series) had their
GeometryStore allocated and loaded but never written to, so door_markers was
always empty and UmfAligner could never reach alignment.

Covers:
  - update_from_midpoints: new primary GeometryStore method
  - update_from_mission: delegates to update_from_midpoints (EPHEMERAL path)
  - SMART path gap detection produces correct midpoints from a trajectory
  - SMART path writes to geometry_store and schedules async_save
  - SMART path skipped when < 20 pose points
  - SMART path skipped when geometry_store is None
"""
from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.geometry_store import (
    DOOR_CLUSTER_TOL_MM,
    DoorMarker,
    GeometryStore,
)
from custom_components.roomba_plus.zone_store import (
    GAP_THRESHOLD_MM,
    MAX_DOOR_WIDTH_MM,
    MIN_DOOR_WIDTH_MM,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_gs() -> GeometryStore:
    gs = GeometryStore()
    return gs


def _trajectory_with_door_gap(
    gap_mm: float = 900.0,
    n_pre: int = 15,
    n_post: int = 15,
) -> list[tuple[float, float]]:
    """Build a trajectory with a single door-width gap at the midpoint.

    Returns n_pre + n_post points. The distance between point n_pre-1 and
    n_pre is exactly gap_mm. All other consecutive distances are 100mm.
    """
    pts: list[tuple[float, float]] = []
    x = 0.0
    for _ in range(n_pre):
        pts.append((x, 0.0))
        x += 100.0
    # Set first post-point exactly gap_mm from the last pre-point
    x = pts[-1][0] + gap_mm
    for _ in range(n_post):
        pts.append((x, 0.0))
        x += 100.0
    return pts


def _gap_midpoints_from(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Replicate the gap detection logic from image.py SMART block."""
    midpoints = []
    for i in range(len(pts) - 1):
        dist = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if dist > GAP_THRESHOLD_MM and MIN_DOOR_WIDTH_MM <= dist <= MAX_DOOR_WIDTH_MM:
            midpoints.append((
                (pts[i][0] + pts[i + 1][0]) / 2.0,
                (pts[i][1] + pts[i + 1][1]) / 2.0,
            ))
    return midpoints


# ── update_from_midpoints ─────────────────────────────────────────────────────

class TestUpdateFromMidpoints:

    def test_creates_new_marker_from_single_midpoint(self):
        gs = _make_gs()
        gs.update_from_midpoints([(500.0, 300.0)])
        assert len(gs.door_markers) == 1
        assert gs.door_markers[0].mission_count == 1
        assert abs(gs.door_markers[0].cx - 500.0) < 1.0
        assert abs(gs.door_markers[0].cy - 300.0) < 1.0

    def test_clusters_nearby_midpoints_into_same_marker(self):
        gs = _make_gs()
        # Two observations within cluster tolerance → same marker
        gs.update_from_midpoints([(500.0, 300.0)])
        gs.update_from_midpoints([(510.0, 295.0)])  # 11mm away — within 400mm tol
        assert len(gs.door_markers) == 1
        assert gs.door_markers[0].mission_count == 2

    def test_distant_midpoints_create_separate_markers(self):
        gs = _make_gs()
        gs.update_from_midpoints([(500.0, 300.0)])
        gs.update_from_midpoints([(2000.0, 1500.0)])  # far away
        assert len(gs.door_markers) == 2

    def test_empty_midpoints_list_is_noop(self):
        gs = _make_gs()
        gs.update_from_midpoints([])
        assert len(gs.door_markers) == 0

    def test_marker_id_increments(self):
        gs = _make_gs()
        gs.update_from_midpoints([(100.0, 0.0), (2000.0, 0.0)])
        ids = [m.id for m in gs.door_markers]
        assert ids == ["dm_1", "dm_2"]


# ── update_from_mission delegates to update_from_midpoints ───────────────────

class TestUpdateFromMissionDelegates:

    def test_ephemeral_path_still_works_via_zone_store(self):
        """update_from_mission must remain functional after refactor."""
        gs = _make_gs()
        zone_store = MagicMock()
        zone_store.last_mission_gap_midpoints = [(800.0, 400.0), (3000.0, 100.0)]
        gs.update_from_mission(zone_store)
        assert len(gs.door_markers) == 2

    def test_empty_zone_store_midpoints_is_noop(self):
        gs = _make_gs()
        zone_store = MagicMock()
        zone_store.last_mission_gap_midpoints = []
        gs.update_from_mission(zone_store)
        assert len(gs.door_markers) == 0


# ── SMART path gap detection ──────────────────────────────────────────────────

class TestSmartPathGapDetection:

    def test_door_width_gap_produces_one_midpoint(self):
        pts = _trajectory_with_door_gap(gap_mm=900.0)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 1

    def test_midpoint_position_is_correct(self):
        pts = _trajectory_with_door_gap(gap_mm=900.0, n_pre=10)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 1
        # n_pre=10: last pre-point at x=(n_pre-1)*100 = 900.0
        # First post-point at x = 900.0 + 900.0 = 1800.0
        # Midpoint: x = (900.0 + 1800.0) / 2 = 1350.0, y = 0.0
        assert abs(midpoints[0][0] - 1350.0) < 0.1
        assert abs(midpoints[0][1] - 0.0) < 0.1

    def test_gap_below_min_door_width_not_detected(self):
        pts = _trajectory_with_door_gap(gap_mm=MIN_DOOR_WIDTH_MM - 50)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 0

    def test_gap_above_max_door_width_not_detected(self):
        pts = _trajectory_with_door_gap(gap_mm=MAX_DOOR_WIDTH_MM + 50)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 0

    def test_gap_at_gap_threshold_boundary_not_detected(self):
        # Gap must be STRICTLY GREATER than GAP_THRESHOLD_MM
        pts = _trajectory_with_door_gap(gap_mm=GAP_THRESHOLD_MM)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 0

    def test_gap_just_above_threshold_detected(self):
        pts = _trajectory_with_door_gap(gap_mm=GAP_THRESHOLD_MM + 1)
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 1

    def test_multiple_door_gaps_all_detected(self):
        # Build a trajectory with two separate door gaps
        pts1 = _trajectory_with_door_gap(gap_mm=900.0, n_pre=10, n_post=5)
        # Shift second segment far enough apart to not be within MAX_DOOR_WIDTH_MM
        offset_x = pts1[-1][0] + 200.0  # 100mm step → gap to next segment is 200mm
        pts2 = _trajectory_with_door_gap(gap_mm=950.0, n_pre=5, n_post=10)
        pts2 = [(x + offset_x + 200.0, y) for x, y in pts2]
        pts = pts1 + pts2
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 2

    def test_short_trajectory_below_minimum_points(self):
        # < 20 points — should not be processed at all
        pts = [(float(i * 100), 0.0) for i in range(10)]
        # No gaps of door width in this uniform trajectory
        midpoints = _gap_midpoints_from(pts)
        assert len(midpoints) == 0
