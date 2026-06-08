"""Tests for umf_aligner.py — Roomba+ v2.3.0 Step 3.

Covers all UmfAligner methods with synthetic data so no live UMF or
GeometryStore is required. Execution is synchronous (no async needed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus.umf_aligner import (
    UmfAligner,
    _point_in_polygon,
    _DOOR_GAP_MIN,
    _DOOR_GAP_MAX,
    _DOOR_MATCH_TOLERANCE,
    _MIN_CONFIDENCE,
)


# ── Helpers / stubs ───────────────────────────────────────────────────────────

@dataclass
class _FakeMarker:
    cx: float
    cy: float
    mission_count: int = 5
    id: str = "m1"


def _make_gs(markers: list[_FakeMarker]) -> MagicMock:
    gs = MagicMock()
    gs.door_markers = markers
    return gs


def _square_points2d(
    x0: float = 0, y0: float = 0, size: float = 5.0,
    id_prefix: str = "p",
) -> list[dict]:
    """Four corners of a square in points2d format (coordinates in metres)."""
    corners = [
        (x0,         y0),
        (x0 + size,  y0),
        (x0 + size,  y0 + size),
        (x0,         y0 + size),
    ]
    return [
        {"id": f"{id_prefix}{i}", "coordinates": [x, y]}
        for i, (x, y) in enumerate(corners)
    ]


def _square_region(rid: str, id_prefix: str = "p") -> dict:
    return {
        "id": rid,
        "name": f"Room {rid}",
        "geometry": {"ids": [[f"{id_prefix}0", f"{id_prefix}1",
                               f"{id_prefix}2", f"{id_prefix}3"]]},
    }


def _aligner_with_door(
    door_x: float = 800, door_y: float = 0,
    marker_cx: float = 800, marker_cy: float = 0,
    n_markers: int = 2,
) -> tuple[UmfAligner, list[_FakeMarker]]:
    """Aligner pre-loaded with a door gap and matching GS markers."""
    # gap and coordinates in metres (UMF units) — _build_coord_lookup multiplies
    # by UMF_TO_MM=1000 so thresholds (_DOOR_GAP_MIN/_DOOR_GAP_MAX in mm) apply correctly.
    gap_m = (_DOOR_GAP_MIN + _DOOR_GAP_MAX) / 2 / 1000.0  # 0.9m (= 900mm after scaling)
    points2d = [
        {"id": "a", "coordinates": [door_x - gap_m / 2, door_y]},
        {"id": "b", "coordinates": [door_x + gap_m / 2, door_y]},
        {"id": "c", "coordinates": [door_x - gap_m / 2 + 5.0, door_y + 5.0]},
        {"id": "d", "coordinates": [door_x + gap_m / 2 + 5.0, door_y + 5.0]},
    ]
    regions = [
        {
            "id": "r1",
            "name": "Room 1",
            "geometry": {"ids": [["a", "b", "c", "d"]]},
        }
    ]
    markers = [
        _FakeMarker(cx=marker_cx, cy=marker_cy, mission_count=5, id=f"m{i}")
        for i in range(n_markers)
    ]
    gs = _make_gs(markers)
    return UmfAligner(points2d, regions, gs), markers


# ═══════════════════════════════════════════════════════════════════════════════
# _point_in_polygon
# ═══════════════════════════════════════════════════════════════════════════════

class TestPointInPolygon:
    def _square(self):
        return [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def test_inside_centre(self):
        assert _point_in_polygon(5, 5, self._square()) is True

    def test_outside_right(self):
        assert _point_in_polygon(15, 5, self._square()) is False

    def test_outside_above(self):
        assert _point_in_polygon(5, 15, self._square()) is False

    def test_outside_below(self):
        assert _point_in_polygon(5, -1, self._square()) is False

    def test_outside_left(self):
        assert _point_in_polygon(-1, 5, self._square()) is False

    def test_corner_outside(self):
        # Corners are edge cases; we just want no crash
        _point_in_polygon(0, 0, self._square())

    def test_triangle(self):
        tri = [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]
        assert _point_in_polygon(5, 4, tri) is True
        assert _point_in_polygon(5, 11, tri) is False

    def test_two_vertex_polygon_no_crash(self):
        # Degenerate polygon — should not raise
        _point_in_polygon(0, 0, [(0.0, 0.0), (1.0, 0.0)])


# ═══════════════════════════════════════════════════════════════════════════════
# _build_coord_lookup
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildCoordLookup:
    def _make(self, points2d, regions=None):
        gs = _make_gs([])
        a = UmfAligner(points2d, regions or [], gs)
        a._build_coord_lookup()
        return a

    def test_empty_list(self):
        a = self._make([])
        assert a._coord_lookup == {}

    def test_normal_case(self):
        pts = [{"id": "p0", "coordinates": [100.0, 200.0]}]
        a = self._make(pts)
        assert a._coord_lookup == {"p0": (100000.0, 200000.0)}  # 100.0m * UMF_TO_MM

    def test_missing_id_skipped(self):
        pts = [{"coordinates": [1, 2]}]
        a = self._make(pts)
        assert a._coord_lookup == {}

    def test_malformed_coordinates_skipped(self):
        pts = [{"id": "p0", "coordinates": ["bad", "data"]}]
        a = self._make(pts)
        assert a._coord_lookup == {}

    def test_short_coordinates_skipped(self):
        pts = [{"id": "p0", "coordinates": [1]}]
        a = self._make(pts)
        assert a._coord_lookup == {}

    def test_multiple_points(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [1000, 0]},
        ]
        a = self._make(pts)
        assert len(a._coord_lookup) == 2
        assert a._coord_lookup["b"] == (1000000.0, 0.0)  # 1000.0m * UMF_TO_MM

    def test_integer_coordinates_converted(self):
        pts = [{"id": "x", "coordinates": [5, 10]}]
        a = self._make(pts)
        x, y = a._coord_lookup["x"]
        assert isinstance(x, float)
        assert isinstance(y, float)


# ═══════════════════════════════════════════════════════════════════════════════
# _detect_door_gaps
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectDoorGaps:
    def _run(self, points2d):
        gs = _make_gs([])
        a = UmfAligner(points2d, [], gs)
        a._build_coord_lookup()
        a._detect_door_gaps()
        return a

    def test_no_gap_empty(self):
        a = self._run([])
        assert a._door_candidates == []

    def test_gap_too_small(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [0.1, 0]},  # 0.1m = 100mm < _DOOR_GAP_MIN
        ]
        a = self._run(pts)
        assert a._door_candidates == []

    def test_gap_too_large(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [2.0, 0]},  # 2.0m = 2000mm > _DOOR_GAP_MAX
        ]
        a = self._run(pts)
        assert a._door_candidates == []

    def test_gap_at_minimum(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [_DOOR_GAP_MIN / 1000.0, 0]},  # metres
        ]
        a = self._run(pts)
        assert len(a._door_candidates) == 1

    def test_gap_at_maximum(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [_DOOR_GAP_MAX / 1000.0, 0]},  # metres
        ]
        a = self._run(pts)
        assert len(a._door_candidates) == 1

    def test_midpoint_correct(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [0.9, 0]},  # 0.9m = 900mm after UMF_TO_MM
        ]
        a = self._run(pts)
        assert len(a._door_candidates) == 1
        assert a._door_candidates[0] == pytest.approx((450.0, 0.0))

    def test_multiple_gaps(self):
        gap = 0.9  # metres (= 900mm after UMF_TO_MM)
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [gap, 0]},
            {"id": "c", "coordinates": [gap + 10.0, 0]},  # no gap here (too large)
            {"id": "d", "coordinates": [gap + 10.0 + gap, 0]},
        ]
        a = self._run(pts)
        assert len(a._door_candidates) == 2

    def test_missing_id_in_lookup_skipped(self):
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"coordinates": [0.9, 0]},  # no id
        ]
        a = self._run(pts)
        assert a._door_candidates == []

    def test_ordering_matters(self):
        # Gap only detected between consecutive, not non-consecutive points
        pts = [
            {"id": "a", "coordinates": [0, 0]},
            {"id": "b", "coordinates": [5.0, 0]},    # 5m -> 5000mm, too large
            {"id": "c", "coordinates": [5.9, 0]},    # 0.9m = 900mm gap with b
        ]
        a = self._run(pts)
        assert len(a._door_candidates) == 1
        assert a._door_candidates[0] == pytest.approx((5450.0, 0.0))


# ═══════════════════════════════════════════════════════════════════════════════
# _resolve_room_polygons
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveRoomPolygons:
    def _run(self, points2d, regions):
        gs = _make_gs([])
        a = UmfAligner(points2d, regions, gs)
        a._build_coord_lookup()
        a._resolve_room_polygons()
        return a

    def test_empty_regions(self):
        a = self._run(_square_points2d(), [])
        assert a._room_polygons == {}

    def test_missing_geometry(self):
        a = self._run(_square_points2d(), [{"id": "r1", "name": "X"}])
        assert a._room_polygons == {}

    def test_empty_ids_list(self):
        a = self._run(
            _square_points2d(),
            [{"id": "r1", "geometry": {"ids": []}}],
        )
        assert a._room_polygons == {}

    def test_fewer_than_3_vertices_skipped(self):
        pts = [
            {"id": "p0", "coordinates": [0, 0]},
            {"id": "p1", "coordinates": [1000, 0]},
        ]
        region = {"id": "r1", "geometry": {"ids": [["p0", "p1"]]}}
        a = self._run(pts, [region])
        assert a._room_polygons == {}

    def test_normal_square(self):
        a = self._run(_square_points2d(), [_square_region("r1")])
        assert "r1" in a._room_polygons
        assert len(a._room_polygons["r1"]) == 4

    def test_multiple_rooms(self):
        pts = _square_points2d(id_prefix="a") + _square_points2d(
            x0=6000, id_prefix="b"
        )
        regions = [_square_region("r1", "a"), _square_region("r2", "b")]
        a = self._run(pts, regions)
        assert set(a._room_polygons.keys()) == {"r1", "r2"}

    def test_unknown_point_ids_skipped(self):
        region = {"id": "r1", "geometry": {"ids": [["p0", "p1", "UNKNOWN", "p3"]]}}
        a = self._run(_square_points2d(), [region])
        # p0, p1, p3 resolve → 3 vertices → polygon accepted
        assert "r1" in a._room_polygons
        assert len(a._room_polygons["r1"]) == 3

    def test_uses_first_ids_list_only(self):
        pts = _square_points2d()
        region = {
            "id": "r1",
            "geometry": {
                "ids": [
                    ["p0", "p1", "p2", "p3"],  # exterior — used
                    ["p0", "p1"],               # interior ring — ignored
                ]
            },
        }
        a = self._run(pts, [region])
        assert len(a._room_polygons["r1"]) == 4


# ═══════════════════════════════════════════════════════════════════════════════
# _hungarian_match
# ═══════════════════════════════════════════════════════════════════════════════

class TestHungarianMatch:
    def _aligner(self, markers):
        gs = _make_gs(markers)
        return UmfAligner([], [], gs)

    def test_no_candidates(self):
        a = self._aligner([_FakeMarker(cx=0, cy=0)])
        pairs = a._hungarian_match([], a._geometry_store.door_markers)
        assert pairs == []

    def test_no_markers(self):
        a = self._aligner([])
        pairs = a._hungarian_match([(100.0, 100.0)], [])
        assert pairs == []

    def test_exact_match(self):
        markers = [_FakeMarker(cx=450.0, cy=0.0), _FakeMarker(cx=5450.0, cy=0.0)]
        a = self._aligner(markers)
        candidates = [(450.0, 0.0), (5450.0, 0.0)]
        pairs = a._hungarian_match(candidates, markers)
        assert len(pairs) == 2

    def test_distance_cutoff(self):
        markers = [_FakeMarker(cx=5000.0, cy=0.0)]  # far from candidate
        a = self._aligner(markers)
        pairs = a._hungarian_match([(0.0, 0.0)], markers)
        assert pairs == []

    def test_each_marker_used_once(self):
        marker = _FakeMarker(cx=0.0, cy=0.0)
        a = self._aligner([marker])
        # Two candidates close to same marker
        candidates = [(10.0, 0.0), (20.0, 0.0)]
        pairs = a._hungarian_match(candidates, [marker])
        assert len(pairs) == 1

    def test_3_pairs(self):
        positions = [(0.0, 0.0), (1000.0, 0.0), (2000.0, 0.0)]
        markers = [_FakeMarker(cx=x, cy=y) for x, y in positions]
        a = self._aligner(markers)
        candidates = [(5.0, 5.0), (1005.0, 5.0), (2005.0, 5.0)]
        pairs = a._hungarian_match(candidates, markers)
        assert len(pairs) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# _estimate_rigid_body
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateRigidBody:
    def _aligner(self):
        return UmfAligner([], [], _make_gs([]))

    def _pairs(self, umf_pts, pose_pts):
        return [
            (u, _FakeMarker(cx=p[0], cy=p[1]))
            for u, p in zip(umf_pts, pose_pts)
        ]

    def test_pure_translation(self):
        umf   = [(0.0, 0.0), (1000.0, 0.0), (0.0, 1000.0)]
        pose  = [(500.0, 500.0), (1500.0, 500.0), (500.0, 1500.0)]
        rot, tx, ty = self._aligner()._estimate_rigid_body(self._pairs(umf, pose))
        assert rot == pytest.approx(0.0, abs=1e-6)
        assert tx  == pytest.approx(500.0, abs=0.5)
        assert ty  == pytest.approx(500.0, abs=0.5)

    def test_pure_rotation_90(self):
        # UMF point (1000, 0) → pose (0, 1000) = 90° CCW rotation around origin
        umf  = [(1000.0, 0.0), (0.0, 1000.0)]
        pose = [(0.0, 1000.0), (-1000.0, 0.0)]
        rot, tx, ty = self._aligner()._estimate_rigid_body(self._pairs(umf, pose))
        assert rot == pytest.approx(math.pi / 2, abs=0.01)

    def test_minimum_2_pairs(self):
        # Should not raise with exactly 2 pairs
        umf  = [(0.0, 0.0), (1000.0, 0.0)]
        pose = [(100.0, 0.0), (1100.0, 0.0)]
        rot, tx, ty = self._aligner()._estimate_rigid_body(self._pairs(umf, pose))
        assert isinstance(rot, float)


# ═══════════════════════════════════════════════════════════════════════════════
# umf_to_pose / pose_to_umf
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransforms:
    def _aligned_aligner(self, rot=0.0, tx=500.0, ty=500.0):
        a = UmfAligner([], [], _make_gs([]))
        a._transform  = (rot, tx, ty)
        a._aligned    = True
        a._confidence = 0.9
        return a

    def test_umf_to_pose_not_aligned(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.umf_to_pose(0, 0) is None

    def test_pose_to_umf_not_aligned(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.pose_to_umf(0, 0) is None

    def test_pure_translation_roundtrip(self):
        a = self._aligned_aligner(rot=0.0, tx=1000.0, ty=2000.0)
        umf_pt = (3000.0, 4000.0)
        pose_pt = a.umf_to_pose(*umf_pt)
        assert pose_pt is not None
        assert pose_pt == pytest.approx((4000.0, 6000.0), abs=0.01)
        recovered = a.pose_to_umf(*pose_pt)
        assert recovered is not None
        assert recovered == pytest.approx(umf_pt, abs=0.01)

    def test_rotation_roundtrip(self):
        a = self._aligned_aligner(rot=math.pi / 4, tx=0.0, ty=0.0)
        umf_pt  = (1000.0, 0.0)
        pose_pt = a.umf_to_pose(*umf_pt)
        assert pose_pt is not None
        recovered = a.pose_to_umf(*pose_pt)
        assert recovered is not None
        assert recovered == pytest.approx(umf_pt, abs=0.01)

    def test_rotation_and_translation_roundtrip(self):
        a = self._aligned_aligner(rot=0.3, tx=150.0, ty=-200.0)
        for umf_pt in [(0.0, 0.0), (5000.0, 3000.0), (-500.0, 2000.0)]:
            pose_pt   = a.umf_to_pose(*umf_pt)
            recovered = a.pose_to_umf(*pose_pt)
            assert recovered == pytest.approx(umf_pt, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# room_name_at
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoomNameAt:
    def _aligner_with_rooms(self):
        a = UmfAligner(
            _square_points2d(size=5.0),
            [_square_region("r1")],
            _make_gs([]),
        )
        a._build_coord_lookup()
        a._resolve_room_polygons()
        a._transform  = (0.0, 0.0, 0.0)
        a._aligned    = True
        a._confidence = 0.9
        return a

    def test_point_inside_room(self):
        a = self._aligner_with_rooms()
        name = a.room_name_at(2500, 2500)
        assert name == "Room r1"

    def test_point_outside_all_rooms(self):
        a = self._aligner_with_rooms()
        assert a.room_name_at(9999, 9999) is None

    def test_not_aligned_returns_none(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.room_name_at(0, 0) is None

    def test_fallback_to_rid_when_no_name(self):
        a = UmfAligner(
            _square_points2d(size=5.0),
            [{"id": "r99", "geometry": {"ids": [["p0", "p1", "p2", "p3"]]}}],
            _make_gs([]),
        )
        a._build_coord_lookup()
        a._resolve_room_polygons()
        a._transform  = (0.0, 0.0, 0.0)
        a._aligned    = True
        a._confidence = 0.9
        name = a.room_name_at(2500, 2500)
        assert name == "r99"


# ═══════════════════════════════════════════════════════════════════════════════
# rid_to_name
# ═══════════════════════════════════════════════════════════════════════════════

class TestRidToName:
    def test_empty_regions(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.rid_to_name() == {}

    def test_normal(self):
        regions = [
            {"id": "1", "name": "Kitchen"},
            {"id": "2", "name": "Hallway"},
        ]
        a = UmfAligner([], regions, _make_gs([]))
        assert a.rid_to_name() == {"1": "Kitchen", "2": "Hallway"}

    def test_missing_id_skipped(self):
        regions = [{"name": "Ghost"}]
        a = UmfAligner([], regions, _make_gs([]))
        assert a.rid_to_name() == {}

    def test_missing_name_falls_back_to_rid(self):
        regions = [{"id": "r5"}]
        a = UmfAligner([], regions, _make_gs([]))
        assert a.rid_to_name() == {"r5": "r5"}


# ═══════════════════════════════════════════════════════════════════════════════
# keepout_polygon_umf
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeeputPolygonUmf:
    def _aligner(self):
        pts = _square_points2d()
        a = UmfAligner(pts, [], _make_gs([]))
        a._build_coord_lookup()
        return a

    def test_no_geometry_key(self):
        a = self._aligner()
        assert a.keepout_polygon_umf({}) is None

    def test_empty_ids_list(self):
        a = self._aligner()
        assert a.keepout_polygon_umf({"geometry": {"ids": []}}) is None

    def test_fewer_than_3_vertices(self):
        a = self._aligner()
        zone = {"geometry": {"ids": [["p0", "p1"]]}}
        assert a.keepout_polygon_umf(zone) is None

    def test_normal_square(self):
        a = self._aligner()
        zone = {"geometry": {"ids": [["p0", "p1", "p2", "p3"]]}}
        poly = a.keepout_polygon_umf(zone)
        assert poly is not None
        assert len(poly) == 4

    def test_unknown_point_ids_reduce_count(self):
        a = self._aligner()
        zone = {"geometry": {"ids": [["p0", "UNKNOWN", "p2", "p3"]]}}
        poly = a.keepout_polygon_umf(zone)
        # 3 known points → accepted
        assert poly is not None
        assert len(poly) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# calibration_points
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalibrationPoints:
    def _aligned_with_rooms(self):
        a = UmfAligner(
            _square_points2d(size=5.0),
            [_square_region("r1")],
            _make_gs([]),
        )
        a._build_coord_lookup()
        a._resolve_room_polygons()
        a._transform  = (0.0, 0.0, 0.0)
        a._aligned    = True
        a._confidence = 0.9
        return a

    def _identity_px(self, x_mm, y_mm):
        return (int(x_mm), int(y_mm))

    def test_not_aligned_returns_none(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.calibration_points(self._identity_px) is None

    def test_no_room_polygons_returns_none(self):
        a = UmfAligner([], [], _make_gs([]))
        a._aligned    = True
        a._confidence = 0.9
        assert a.calibration_points(self._identity_px) is None

    def test_returns_3_points(self):
        a = self._aligned_with_rooms()
        cal = a.calibration_points(self._identity_px)
        assert cal is not None
        assert len(cal) == 3

    def test_structure(self):
        a = self._aligned_with_rooms()
        cal = a.calibration_points(self._identity_px)
        for entry in cal:
            assert "vacuum" in entry
            assert "map" in entry
            assert "x" in entry["vacuum"]
            assert "y" in entry["vacuum"]
            assert "x" in entry["map"]
            assert "y" in entry["map"]

    def test_dock_is_first_point(self):
        a = self._aligned_with_rooms()
        cal = a.calibration_points(self._identity_px)
        assert cal[0]["vacuum"]["x"] == pytest.approx(0.0, abs=0.1)
        assert cal[0]["vacuum"]["y"] == pytest.approx(0.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# align() — integration-level
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlign:
    def _make_two_door_aligner(self):
        """Aligner with 2 door gaps at y=0, matching 2 GS markers at same positions."""
        # UMF coordinates in metres; markers in pose-space mm (unchanged).
        # After UMF_TO_MM: door1=800mm, door2=5800mm, gap=900mm
        gap_m = 0.9          # metres (= 900mm)
        door1_x_m, door2_x_m = 0.8, 5.8  # metres
        door1_x, door2_x = 800.0, 5800.0  # mm — for GS markers (pose-space)
        points2d = [
            {"id": "a", "coordinates": [door1_x_m - gap_m / 2, 0]},
            {"id": "b", "coordinates": [door1_x_m + gap_m / 2, 0]},  # door 1 gap
            {"id": "c", "coordinates": [2.0, 5.0]},
            {"id": "d", "coordinates": [door2_x_m - gap_m / 2, 0]},
            {"id": "e", "coordinates": [door2_x_m + gap_m / 2, 0]},  # door 2 gap
        ]
        regions = [
            {
                "id": "r1",
                "name": "Room 1",
                "geometry": {"ids": [["a", "b", "c"]]},
            }
        ]
        markers = [
            _FakeMarker(cx=door1_x, cy=0.0, mission_count=5),
            _FakeMarker(cx=door2_x, cy=0.0, mission_count=5),
        ]
        gs = _make_gs(markers)
        return UmfAligner(points2d, regions, gs)

    def test_insufficient_gs_markers_returns_0(self):
        gs = _make_gs([_FakeMarker(cx=0, cy=0)])  # only 1
        a  = UmfAligner([], [], gs)
        assert a.align() == 0.0
        assert a.aligned is False

    def test_insufficient_door_candidates_returns_0(self):
        markers = [_FakeMarker(cx=0, cy=0), _FakeMarker(cx=1000, cy=0)]
        gs = _make_gs(markers)
        # No door gaps in empty points2d
        a = UmfAligner([], [], gs)
        assert a.align() == 0.0
        assert a.aligned is False

    def test_successful_alignment(self):
        a = self._make_two_door_aligner()
        conf = a.align()
        assert conf > 0.0
        assert a.confidence == pytest.approx(conf)

    def test_alignment_sets_transform(self):
        a = self._make_two_door_aligner()
        a.align()
        assert a._transform is not None
        assert len(a._transform) == 3

    def test_pmap_version_id_stored(self):
        gs = _make_gs([])
        a  = UmfAligner([], [], gs, pmap_version_id="v42")
        assert a.pmap_version_id == "v42"

    def test_properties_before_align(self):
        a = UmfAligner([], [], _make_gs([]))
        assert a.aligned is False
        assert a.confidence == 0.0
        assert a.room_polygons_umf == {}
