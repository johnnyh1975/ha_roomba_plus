"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import math
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from unittest.mock import MagicMock
import pytest
from custom_components.roomba_plus.umf_aligner import UmfAligner
from custom_components.roomba_plus.umf_aligner import _point_in_polygon
from custom_components.roomba_plus.umf_aligner import _DOOR_GAP_MIN
from custom_components.roomba_plus.umf_aligner import _DOOR_GAP_MAX
from custom_components.roomba_plus.umf_aligner import _DOOR_MATCH_TOLERANCE
from custom_components.roomba_plus.umf_aligner import _MIN_CONFIDENCE
from unittest.mock import AsyncMock
from unittest.mock import patch
import asyncio


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


@dataclass
class _FakeMarker:
    cx: float
    cy: float
    mission_count: int = 5
    id: str = "m1"


def _make_aligner(aligned: bool = True, confidence: float = 0.85) -> UmfAligner:
    """Return a minimal UmfAligner with controlled aligned/confidence state."""
    a = UmfAligner([], [], MagicMock())
    a._aligned    = aligned
    a._confidence = confidence
    a._transform  = (0.0, 0.0, 0.0)
    a.pmap_version_id = "v1"
    return a


def _make_runtime_data(
    *,
    aligner: UmfAligner | None = None,
    has_cloud: bool = True,
    regions: list | None = None,
    keepout_zones: list | None = None,
    mission_store=None,
    grid_store=None,
    map_capability=None,
    geometry_store=None,
):
    data = MagicMock()
    data.umf_aligner    = aligner
    data.has_cloud      = has_cloud
    data.mission_store  = mission_store
    data.grid_store     = grid_store
    data.geometry_store = geometry_store

    cc = MagicMock()
    cc.regions      = regions or []
    cc.keepout_zones = keepout_zones or []
    cc.observed_zone_centroids = []
    cc.last_update_success = True
    data.cloud_coordinator = cc if has_cloud else None

    if map_capability is not None:
        data.map_capability = map_capability

    return data


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


class TestCalibrationPoints:
    def _aligned_with_rooms(self):
        # Rooms offset from dock origin (corner-docked robot scenario — S9+/i7+
        # against a wall). UMF coordinates: x0=1.0 m, y0=0.5 m, size=5.0 m.
        # Pose-space bounding box: (1000, 500)–(6000, 5500) mm.
        # min(xs)=1000, min(ys)=500 → dock origin (0, 0) cannot be an anchor.
        a = UmfAligner(
            _square_points2d(x0=1.0, y0=0.5, size=5.0),
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

    def test_anchors_are_bounding_box_corners(self):
        """All 3 calibration anchors must be bounding-box corners, not dock origin.

        v2.7.2: dock origin (0, 0) was replaced by (max_x, min_y) to ensure
        all anchors map inside the rendered image for corner-docked robots.
        """
        a = self._aligned_with_rooms()
        cal = a.calibration_points(self._identity_px)
        vacuums = [(c["vacuum"]["x"], c["vacuum"]["y"]) for c in cal]
        # Dock origin must NOT be an anchor — it maps outside the image
        # when rooms extend only in one direction from the dock.
        assert (0.0, 0.0) not in [(round(x, 1), round(y, 1)) for x, y in vacuums]

    def test_all_anchors_map_inside_image_for_corner_docked_robot(self):
        """For a corner-docked robot all calibration anchor pixels must be ≥ 0.

        S9+ scenario: rooms at x=[200,2000] mm, y=[150,1800] mm.
        Dock at (0,0) would map to negative pixels under the old code.
        """
        import math
        # Build an aligner whose rooms are entirely in positive quadrant
        # (dock in corner — all room vertices have x > 0 and y > 0).
        size_m = 1.8          # UMF room square: 1.8 × 1.8 metres
        offset_m = 0.2        # room starts 200 mm from dock in UMF space
        points2d = [
            {"id": "a", "coordinates": [offset_m,            offset_m           ]},
            {"id": "b", "coordinates": [offset_m + size_m,   offset_m           ]},
            {"id": "c", "coordinates": [offset_m + size_m,   offset_m + size_m  ]},
            {"id": "d", "coordinates": [offset_m,            offset_m + size_m  ]},
        ]
        regions = [{"id": "r1", "name": "Room 1",
                    "geometry": {"ids": [["a", "b", "c", "d"]]}}]
        a = UmfAligner(points2d, regions, _make_gs([]))
        a._build_coord_lookup()
        a._resolve_room_polygons()
        # Identity transform: pose coords == UMF-mm coords
        a._transform  = (0.0, 0.0, 0.0)
        a._aligned    = True
        a._confidence = 0.9
        cal = a.calibration_points(self._identity_px)
        assert cal is not None
        for entry in cal:
            px_x = entry["map"]["x"]
            px_y = entry["map"]["y"]
            assert px_x >= 0, f"anchor pixel x={px_x} outside image (should be ≥ 0)"
            assert px_y >= 0, f"anchor pixel y={px_y} outside image (should be ≥ 0)"


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


class TestUmfVersionChanged:
    def _fn(self):
        from custom_components.roomba_plus.callbacks import _umf_version_changed
        return _umf_version_changed

    def test_no_version_in_coordinator(self):
        coord = MagicMock()
        coord.umf_data = {}
        entry = MagicMock()
        assert self._fn()(coord, entry) is False

    def test_same_version_no_change(self):
        coord = MagicMock()
        coord.umf_data = {"version_id": "v1"}
        entry = MagicMock()
        entry.runtime_data.umf_aligner = _make_aligner()
        assert self._fn()(coord, entry) is False

    def test_different_version_returns_true(self):
        coord = MagicMock()
        coord.umf_data = {"version_id": "v2"}
        entry = MagicMock()
        entry.runtime_data.umf_aligner = _make_aligner()
        assert self._fn()(coord, entry) is True

    def test_no_aligner_returns_true(self):
        coord = MagicMock()
        coord.umf_data = {"version_id": "v1"}
        entry = MagicMock()
        entry.runtime_data.umf_aligner = None
        assert self._fn()(coord, entry) is True


class TestU1ValidateTransform:
    """U1: _validate_transform validates door candidates after transform."""

    def test_perfect_alignment_gives_zero_residual(self):
        """When transform is identity and candidates match markers exactly,
        residual should be 0 → confidence = 1.0."""
        import math
        from custom_components.roomba_plus.umf_aligner import UmfAligner

        # Build geometry: door at x=1000mm, marker at same position
        points2d = [
            {"id": "a", "coordinates": [0.5, 0.0]},  # 500mm
            {"id": "b", "coordinates": [1.5, 0.0]},  # 1500mm — gap midpoint=1000mm
        ]

        class _Marker:
            def __init__(self, cx, cy):
                self.cx, self.cy = cx, cy
                self.mission_count = 3
                self.id = "m1"

        class _GS:
            def __init__(self):
                self.door_markers = [_Marker(1000.0, 0.0)]

        a = UmfAligner.__new__(UmfAligner)
        a._geometry_store = _GS()
        a._door_candidates = [(1000.0, 0.0)]  # already in mm
        a._transform = (0.0, 0.0, 0.0)        # identity: rot=0, tx=0, ty=0
        a._room_polygons = {}
        a._confidence = 0.0
        a._aligned = False

        residual = a._validate_transform()
        assert residual == pytest.approx(0.0, abs=1.0)

    def test_poor_alignment_gives_large_residual(self):
        """When candidates are far from markers, residual > RESIDUAL_SCALE."""
        from custom_components.roomba_plus.umf_aligner import UmfAligner, _RESIDUAL_SCALE

        class _Marker:
            def __init__(self, cx, cy):
                self.cx, self.cy = cx, cy
                self.mission_count = 3
                self.id = "m1"

        class _GS:
            def __init__(self):
                self.door_markers = [_Marker(0.0, 0.0)]

        a = UmfAligner.__new__(UmfAligner)
        a._geometry_store = _GS()
        a._door_candidates = [(5000.0, 0.0)]  # 5m away from marker
        a._transform = (0.0, 0.0, 0.0)        # identity
        a._room_polygons = {}
        a._confidence = 0.0
        a._aligned = False

        residual = a._validate_transform()
        assert residual > _RESIDUAL_SCALE  # → confidence will be 0


# ═══════════════════════════════════════════════════════════════════════
# ROOM-SIZE (v2.9.1) — room_areas_m2 property and its shoelace helper.
# Moved here from sensor.py after the design review concluded these
# belong as a CloudSmartZoneSelect attribute (region_areas_m2), not as
# separate per-room sensor entities — UmfAligner is the natural shared
# home for the computation either way.
# ═══════════════════════════════════════════════════════════════════════

from custom_components.roomba_plus.umf_aligner import _polygon_area_m2


class TestPolygonAreaM2Helper:
    """Shoelace-formula area helper, independent of pose alignment."""

    def test_rectangle_4x5_m(self):
        # 4m x 5m room, vertices in mm (room_polygons_umf's unit).
        verts = [(0, 0), (4000, 0), (4000, 5000), (0, 5000)]
        assert _polygon_area_m2(verts) == 20.0

    def test_triangle(self):
        verts = [(0, 0), (6000, 0), (0, 4000)]
        assert _polygon_area_m2(verts) == 12.0

    def test_winding_order_does_not_matter(self):
        cw = [(0, 0), (4000, 0), (4000, 5000), (0, 5000)]
        ccw = list(reversed(cw))
        assert _polygon_area_m2(cw) == _polygon_area_m2(ccw)

    def test_fewer_than_3_vertices_returns_none(self):
        assert _polygon_area_m2([(0, 0), (1000, 1000)]) is None
        assert _polygon_area_m2([]) is None

    def test_rounds_to_one_decimal(self):
        # 3.333m x 1m = 3.333 m^2 -> rounds to 3.3
        verts = [(0, 0), (3333, 0), (3333, 1000), (0, 1000)]
        assert _polygon_area_m2(verts) == 3.3


class TestRoomAreasM2Property:
    """UmfAligner.room_areas_m2 — uses room_polygons_umf regardless of
    whether pose alignment (`aligned`) succeeded."""

    def _aligner_with_one_room(self, size_m=5.0):
        a = UmfAligner(
            _square_points2d(size=size_m),
            [_square_region("r1")],
            _make_gs([]),
        )
        a._build_coord_lookup()
        a._resolve_room_polygons()
        return a

    def test_single_room_area(self):
        a = self._aligner_with_one_room(size_m=5.0)
        assert a.room_areas_m2 == {"r1": 25.0}

    def test_available_even_when_not_aligned(self):
        """_resolve_room_polygons() runs before the alignment step, so
        room_areas_m2 must be populated even though align() was never
        called (and `aligned` therefore stays False)."""
        a = self._aligner_with_one_room(size_m=5.0)
        assert a.aligned is False
        assert a.room_areas_m2 == {"r1": 25.0}

    def test_multiple_rooms(self):
        points = _square_points2d(size=4.0, id_prefix="a") + _square_points2d(
            x0=100, size=6.0, id_prefix="b"
        )
        regions = [
            _square_region("kitchen", id_prefix="a"),
            _square_region("hallway", id_prefix="b"),
        ]
        a = UmfAligner(points, regions, _make_gs([]))
        a._build_coord_lookup()
        a._resolve_room_polygons()
        assert a.room_areas_m2 == {"kitchen": 16.0, "hallway": 36.0}

    def test_no_regions_returns_empty_dict(self):
        a = UmfAligner([], [], _make_gs([]))
        a._build_coord_lookup()
        a._resolve_room_polygons()
        assert a.room_areas_m2 == {}

    def test_degenerate_region_omitted(self):
        """A region whose geometry resolves to < 3 vertices contributes
        no entry (mirrors _resolve_room_polygons()'s own >= 3 guard)."""
        points = [
            {"id": "p0", "coordinates": [0, 0]},
            {"id": "p1", "coordinates": [1, 1]},
        ]
        regions = [{"id": "thin", "geometry": {"ids": [["p0", "p1"]]}}]
        a = UmfAligner(points, regions, _make_gs([]))
        a._build_coord_lookup()
        a._resolve_room_polygons()
        assert a.room_areas_m2 == {}



# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 REVIEW-REMAINDER — thread-safety: snapshot semantics on rebuild
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewRemainderRebindSemantics:
    """The bootstrap path (GS-SMART-UMF) re-runs align() on the published
    aligner while the paho-MQTT render thread may iterate coord lookup /
    room polygons. Both rebuilds must publish via a single rebind so a
    concurrent reader always holds a complete dict — never one that
    changes size under iteration (MQTT-thread-death consequence class)."""

    def _aligner(self):
        points = [
            {"id": f"p{i}", "coordinates": [float(i), float(i)]}
            for i in range(4)
        ]
        regions = [{
            "id": "r1",
            "geometry": {"ids": [["p0", "p1", "p2", "p3"]]},
        }]
        return UmfAligner(
            points2d=points, regions=regions,
            geometry_store=None, pmap_version_id="v1",
        )

    def test_coord_lookup_rebuild_is_rebind_not_inplace(self):
        a = self._aligner()
        a._build_coord_lookup()
        old_ref = a._coord_lookup
        old_snapshot = dict(old_ref)
        a._build_coord_lookup()
        # A reader holding old_ref keeps a complete, unchanged dict —
        # the rebuild must NOT have mutated it in place.
        assert a._coord_lookup is not old_ref
        assert old_ref == old_snapshot

    def test_room_polygons_rebuild_is_rebind_not_inplace(self):
        a = self._aligner()
        a._build_coord_lookup()
        a._resolve_room_polygons()
        old_ref = a._room_polygons
        old_snapshot = {k: list(v) for k, v in old_ref.items()}
        a._resolve_room_polygons()
        assert a._room_polygons is not old_ref
        assert {k: list(v) for k, v in old_ref.items()} == old_snapshot
        assert "r1" in a._room_polygons  # rebuild produced the same content
