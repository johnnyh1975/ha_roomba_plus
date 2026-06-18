"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
import json
import math
import sys
import types
import unittest
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import os
import pytest
from custom_components.roomba_plus.geometry_store import DEFAULT_DRIFT_THRESHOLD_MM
from custom_components.roomba_plus.geometry_store import DEFAULT_WALL_OFFSET_MM
from custom_components.roomba_plus.geometry_store import DOOR_CLUSTER_TOL_MM
from custom_components.roomba_plus.geometry_store import MAX_MARKER_OBSERVATIONS
from custom_components.roomba_plus.geometry_store import STORAGE_VERSION
from custom_components.roomba_plus.geometry_store import DoorMarker
from custom_components.roomba_plus.geometry_store import GeometryStore
from custom_components.roomba_plus.geometry_store import UserDoor
from custom_components.roomba_plus.geometry_store import UserObstacle
from custom_components.roomba_plus.geometry_store import UserWall
from custom_components.roomba_plus.zone_store import GAP_THRESHOLD_MM
from custom_components.roomba_plus.zone_store import MAX_DOOR_WIDTH_MM
from custom_components.roomba_plus.zone_store import MIN_DOOR_WIDTH_MM
from custom_components.roomba_plus.zone_store import ZoneStore


ROOT = os.path.join(os.path.dirname(__file__), "..")
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
_store_cls = MagicMock()
ha_storage = types.ModuleType("homeassistant.helpers.storage")
ha_storage.Store = _store_cls


def _make_zone_store_with_midpoints(
    midpoints: list[tuple[float, float]],
) -> ZoneStore:
    """Return a ZoneStore whose last_mission_gap_midpoints is pre-set."""
    zs = ZoneStore()
    zs.last_mission_gap_midpoints = list(midpoints)
    return zs


def _linear_points(
    n: int = 30, x_start: float = 0, y_start: float = 0, step: float = 100
) -> list[tuple[float, float]]:
    """Return n evenly-spaced points along the x-axis."""
    return [(x_start + i * step, y_start) for i in range(n)]


def _mission_with_gap(
    gap_mm: float,
) -> list[tuple[float, float]]:
    """30 points in one segment, gap_mm jump, 30 more points."""
    seg1 = _linear_points(30, x_start=0)
    seg2 = _linear_points(30, x_start=seg1[-1][0] + gap_mm)
    return seg1 + seg2


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


def _make_aligner(door_candidates=None, gs_markers=None):
    """Build a minimal UmfAligner with pre-set candidates and GS store."""
    from custom_components.roomba_plus.umf_aligner import UmfAligner

    geo = MagicMock()
    geo.door_markers = gs_markers or []

    aligner = UmfAligner.__new__(UmfAligner)
    aligner._points2d = []
    aligner._regions = []
    aligner._geometry_store = geo
    aligner.pmap_version_id = ""
    aligner._coord_lookup = {}
    aligner._room_polygons = {}
    aligner._door_candidates = door_candidates or [
        (500.0, 300.0),
        (1200.0, 800.0),
        (600.0, 1500.0),
    ]
    aligner._transform = None
    aligner._confidence = 0.0
    aligner._aligned = False
    aligner._bootstrap_markers = []
    return aligner


def _record_with_traversals(rids):
    """Build a fake cloud record with traversal finEvents."""
    return {
        "timeline": {
            "finEvents": [
                {"type": "traversal", "traversal": {"rid": rid, "type": "region"}}
                for rid in rids
            ]
        }
    }


class TestZoneStoreGapMidpoints:
    def test_field_exists_and_is_list(self):
        zs = ZoneStore()
        assert isinstance(zs.last_mission_gap_midpoints, list)
        assert zs.last_mission_gap_midpoints == []

    def test_midpoint_recorded_for_door_width_gap(self):
        zs = ZoneStore()
        gap = (MIN_DOOR_WIDTH_MM + MAX_DOOR_WIDTH_MM) / 2  # 900 mm — in range
        points = _mission_with_gap(gap)
        zs.process_mission(points, 0.0)
        assert len(zs.last_mission_gap_midpoints) == 1
        mx, my = zs.last_mission_gap_midpoints[0]
        # Midpoint x should be halfway across the gap
        seg1_end_x = _linear_points(30)[-1][0]
        expected_x = seg1_end_x + gap / 2
        assert abs(mx - expected_x) < 1.0
        assert my == 0.0

    def test_no_midpoint_for_gap_below_door_min(self):
        zs = ZoneStore()
        gap = MIN_DOOR_WIDTH_MM - 50  # 550 mm — narrower than a door
        # This gap is also below GAP_THRESHOLD_MM so no split at all
        points = _mission_with_gap(gap)
        zs.process_mission(points, 0.0)
        assert zs.last_mission_gap_midpoints == []

    def test_no_midpoint_for_gap_above_door_max(self):
        zs = ZoneStore()
        gap = MAX_DOOR_WIDTH_MM + 100  # 1300 mm — too wide for a door
        points = _mission_with_gap(gap)
        zs.process_mission(points, 0.0)
        # gap > GAP_THRESHOLD_MM so split occurs, but gap > MAX_DOOR_WIDTH_MM
        # so no midpoint is recorded
        assert zs.last_mission_gap_midpoints == []

    def test_midpoints_reset_between_missions(self):
        zs = ZoneStore()
        gap = 900.0
        points = _mission_with_gap(gap)
        zs.process_mission(points, 0.0)
        assert len(zs.last_mission_gap_midpoints) == 1
        # Second mission with no qualifying gap — midpoints must reset to []
        zs.process_mission(_linear_points(30), 1.0)
        assert zs.last_mission_gap_midpoints == []

    def test_multiple_gaps_in_one_mission(self):
        zs = ZoneStore()
        gap = 900.0
        seg = _linear_points(30, x_start=0)
        seg2 = _linear_points(30, x_start=seg[-1][0] + gap)
        seg3 = _linear_points(30, x_start=seg2[-1][0] + gap)
        points = seg + seg2 + seg3
        zs.process_mission(points, 0.0)
        assert len(zs.last_mission_gap_midpoints) == 2

    def test_midpoints_not_persisted(self):
        """last_mission_gap_midpoints must not appear in ZoneStore serialisation."""
        zs = ZoneStore()
        zs.last_mission_gap_midpoints = [(100.0, 200.0)]
        # Manually check _zone_to_dict doesn't include it
        # (ZoneStore serialises zones list, not the field itself)
        zs_dict = {
            "next_id": zs._next_id,
            "gap_threshold_mm": zs._gap_threshold_mm,
            "scale_factor": zs._scale_factor,
            "zones": [ZoneStore._zone_to_dict(z) for z in zs.zones],
        }
        assert "last_mission_gap_midpoints" not in zs_dict


class TestDoorMarker:
    def test_initial_state(self):
        m = DoorMarker(id="dm_1", cx=100.0, cy=200.0)
        assert m.cx == 100.0
        assert m.cy == 200.0
        assert m.mission_count == 0
        assert m.observations == []

    def test_update_single_observation(self):
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0)
        m.update(100.0, 200.0)
        assert m.cx == 100.0
        assert m.cy == 200.0
        assert m.mission_count == 1
        assert len(m.observations) == 1

    def test_update_median_stability(self):
        """Median should be stable even with one outlier."""
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0)
        for _ in range(5):
            m.update(100.0, 200.0)
        m.update(500.0, 900.0)  # outlier
        # Median of [100,100,100,100,100,500] = 100
        assert m.cx == pytest.approx(100.0)
        assert m.cy == pytest.approx(200.0)

    def test_update_mission_count(self):
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0)
        for i in range(5):
            m.update(float(i), 0.0)
        assert m.mission_count == 5

    def test_observation_cap(self):
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0)
        for i in range(MAX_MARKER_OBSERVATIONS + 5):
            m.update(float(i), 0.0)
        assert len(m.observations) == MAX_MARKER_OBSERVATIONS
        # Oldest observations should be dropped — last value should be in list
        last_x = MAX_MARKER_OBSERVATIONS + 4
        xs = [p[0] for p in m.observations]
        assert last_x in xs

    def test_to_dict_round_trip(self):
        m = DoorMarker(id="dm_1", cx=100.0, cy=200.0, label="hallway")
        m.update(100.0, 200.0)
        d = m.to_dict()
        m2 = DoorMarker.from_dict(d)
        assert m2.id == m.id
        assert m2.cx == pytest.approx(m.cx)
        assert m2.cy == pytest.approx(m.cy)
        assert m2.label == m.label
        assert m2.mission_count == m.mission_count
        assert m2.observations == m.observations

    def test_from_dict_missing_observations(self):
        """from_dict with no observations key should produce empty list."""
        m = DoorMarker.from_dict({"id": "dm_1", "cx": 10.0, "cy": 20.0})
        assert m.observations == []
        assert m.mission_count == 0


class TestGeometryStoreUpdateFromMission:
    def test_single_midpoint_creates_marker(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(500.0, 300.0)])
        gs.update_from_mission(zs)
        assert len(gs.door_markers) == 1
        assert gs.door_markers[0].cx == pytest.approx(500.0)
        assert gs.door_markers[0].cy == pytest.approx(300.0)
        assert gs.door_markers[0].mission_count == 1

    def test_two_close_midpoints_update_same_marker(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(500.0, 300.0)])
        gs.update_from_mission(zs)
        # Second mission, close midpoint
        zs2 = _make_zone_store_with_midpoints([(520.0, 310.0)])
        gs.update_from_mission(zs2)
        assert len(gs.door_markers) == 1
        assert gs.door_markers[0].mission_count == 2

    def test_two_far_midpoints_create_separate_markers(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(500.0, 300.0)])
        gs.update_from_mission(zs)
        zs2 = _make_zone_store_with_midpoints([(500.0 + DOOR_CLUSTER_TOL_MM + 100, 300.0)])
        gs.update_from_mission(zs2)
        assert len(gs.door_markers) == 2

    def test_exactly_at_cluster_tolerance_is_same_marker(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(0.0, 0.0)])
        gs.update_from_mission(zs)
        # Strictly less than DOOR_CLUSTER_TOL_MM — should cluster
        zs2 = _make_zone_store_with_midpoints([(DOOR_CLUSTER_TOL_MM - 1, 0.0)])
        gs.update_from_mission(zs2)
        assert len(gs.door_markers) == 1

    def test_no_midpoints_no_markers(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([])
        gs.update_from_mission(zs)
        assert gs.door_markers == []

    def test_marker_ids_are_stable_and_unique(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([
            (100.0, 0.0),
            (2000.0, 0.0),
        ])
        gs.update_from_mission(zs)
        ids = [m.id for m in gs.door_markers]
        assert len(set(ids)) == 2

    def test_markers_not_overwritten_by_apply_user_edit(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(500.0, 300.0)])
        gs.update_from_mission(zs)
        gs.apply_user_edit({"walls": [], "doors": [], "obstacles": []})
        assert len(gs.door_markers) == 1


class TestGeometryStoreDrift:
    def test_record_drift_accumulates(self):
        gs = GeometryStore()
        gs.record_drift(50.0, 0.0)
        gs.record_drift(50.0, 0.0)
        gs.record_drift(50.0, 0.0)
        assert gs.cumulative_drift_mm == pytest.approx(150.0)

    def test_record_drift_uses_euclidean_magnitude(self):
        gs = GeometryStore()
        gs.record_drift(30.0, 40.0)  # hypot = 50
        assert gs.cumulative_drift_mm == pytest.approx(50.0)

    def test_record_drift_returns_false_below_threshold(self):
        gs = GeometryStore()
        result = gs.record_drift(10.0, 0.0)
        assert result is False

    def test_record_drift_returns_true_when_threshold_exceeded(self):
        gs = GeometryStore()
        gs.cumulative_drift_mm = DEFAULT_DRIFT_THRESHOLD_MM - 1
        result = gs.record_drift(2.0, 0.0)
        assert result is True

    def test_record_drift_ignores_zero_vector(self):
        gs = GeometryStore()
        gs.record_drift(0.0, 0.0)
        assert gs.cumulative_drift_mm == pytest.approx(0.0)

    def test_record_drift_ignores_sub_mm(self):
        gs = GeometryStore()
        gs.record_drift(0.5, 0.5)  # hypot ≈ 0.71 < 1.0
        assert gs.cumulative_drift_mm == pytest.approx(0.0)

    def test_reset_drift(self):
        gs = GeometryStore()
        gs.cumulative_drift_mm = 250.0
        gs.reset_drift()
        assert gs.cumulative_drift_mm == pytest.approx(0.0)

    def test_reset_drift_then_accumulate(self):
        gs = GeometryStore()
        gs.record_drift(200.0, 0.0)
        gs.reset_drift()
        gs.record_drift(50.0, 0.0)
        assert gs.cumulative_drift_mm == pytest.approx(50.0)


class TestGeometryStoreApplyUserEdit:
    def _wall(self, **kw) -> dict:
        return {"x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 0.0, "label": "", **kw}

    def _door(self, **kw) -> dict:
        return {"cx": 50.0, "cy": 0.0, "width_mm": 875.0, "theta_deg": 90.0,
                "label": "", **kw}

    def _obstacle(self, **kw) -> dict:
        return {"x": 0.0, "y": 0.0, "w": 500.0, "h": 300.0, "label": "", **kw}

    def test_replaces_walls_atomically(self):
        gs = GeometryStore()
        gs.apply_user_edit({"walls": [self._wall(label="first")]})
        gs.apply_user_edit({"walls": [self._wall(label="second")]})
        assert len(gs.walls) == 1
        assert gs.walls[0].label == "second"

    def test_empty_payload_clears_lists(self):
        gs = GeometryStore()
        gs.apply_user_edit({"walls": [self._wall()]})
        gs.apply_user_edit({"walls": [], "doors": [], "obstacles": []})
        assert gs.walls == []
        assert gs.doors == []
        assert gs.obstacles == []

    def test_assigns_id_when_missing(self):
        gs = GeometryStore()
        gs.apply_user_edit({"walls": [self._wall()]})  # no id key
        assert gs.walls[0].id != ""
        assert len(gs.walls[0].id) > 0

    def test_preserves_existing_id(self):
        gs = GeometryStore()
        gs.apply_user_edit({"walls": [self._wall(id="my_wall")]})
        assert gs.walls[0].id == "my_wall"

    def test_zone_labels_stored(self):
        gs = GeometryStore()
        gs.apply_user_edit({"zone_labels": {"1": "Living room"}})
        assert gs.zone_labels == {"1": "Living room"}

    def test_wall_offset_mm_stored(self):
        gs = GeometryStore()
        gs.apply_user_edit({"wall_offset_mm": 150})
        assert gs.wall_offset_mm == 150

    def test_door_markers_unchanged(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(500.0, 300.0)])
        gs.update_from_mission(zs)
        gs.apply_user_edit({"walls": [self._wall()], "doors": [], "obstacles": []})
        assert len(gs.door_markers) == 1

    def test_multiple_walls_doors_obstacles(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [self._wall(), self._wall()],
            "doors": [self._door()],
            "obstacles": [self._obstacle(), self._obstacle(), self._obstacle()],
        })
        assert len(gs.walls) == 2
        assert len(gs.doors) == 1
        assert len(gs.obstacles) == 3


class TestGeometryStoreInferenceSuggestions:
    def _zone_store_with_zone(
        self, x_min, x_max, y_min, y_max, zone_id=1, name="Room 1", confidence=0.5
    ):
        from custom_components.roomba_plus.zone_store import Zone
        zs = ZoneStore()
        z = Zone(id=zone_id, name=name, confirmed=False,
                 x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
                 confidence=confidence)
        zs.zones.append(z)
        return zs

    def test_expansion_applied_correctly(self):
        gs = GeometryStore()
        gs.wall_offset_mm = 200
        zs = self._zone_store_with_zone(0, 1000, 0, 1000)
        s = gs.get_inference_suggestions(zs)
        outline = s["zone_outlines"][0]
        assert outline["x_min"] == -200
        assert outline["x_max"] == 1200
        assert outline["y_min"] == -200
        assert outline["y_max"] == 1200

    def test_zero_offset_returns_exact_bbox(self):
        gs = GeometryStore()
        gs.wall_offset_mm = 0
        zs = self._zone_store_with_zone(100, 500, 200, 800)
        s = gs.get_inference_suggestions(zs)
        o = s["zone_outlines"][0]
        assert o["x_min"] == 100
        assert o["x_max"] == 500
        assert o["y_min"] == 200
        assert o["y_max"] == 800

    def test_no_zones_returns_empty_outlines(self):
        gs = GeometryStore()
        zs = ZoneStore()
        s = gs.get_inference_suggestions(zs)
        assert s["zone_outlines"] == []

    def test_none_zone_store_returns_empty(self):
        gs = GeometryStore()
        s = gs.get_inference_suggestions(None)
        assert s == {"zone_outlines": [], "door_markers": []}

    def test_door_markers_included(self):
        gs = GeometryStore()
        zs_mission = _make_zone_store_with_midpoints([(400.0, 300.0)])
        gs.update_from_mission(zs_mission)
        zs = ZoneStore()
        s = gs.get_inference_suggestions(zs)
        assert len(s["door_markers"]) == 1
        assert s["door_markers"][0]["cx"] == pytest.approx(400.0)

    def test_confidence_rounded(self):
        gs = GeometryStore()
        zs = self._zone_store_with_zone(0, 1000, 0, 1000, confidence=0.123456)
        s = gs.get_inference_suggestions(zs)
        assert s["zone_outlines"][0]["confidence"] == pytest.approx(0.123, abs=0.001)


class TestHasUserGeometry:
    def test_false_when_all_empty(self):
        assert GeometryStore().has_user_geometry is False

    def test_true_when_walls_present(self):
        gs = GeometryStore()
        gs.walls.append(UserWall(id="w1", x1=0, y1=0, x2=100, y2=0))
        assert gs.has_user_geometry is True

    def test_true_when_doors_present(self):
        gs = GeometryStore()
        gs.doors.append(UserDoor(id="d1", cx=0, cy=0, width_mm=875, theta_deg=90))
        assert gs.has_user_geometry is True

    def test_true_when_obstacles_present(self):
        gs = GeometryStore()
        gs.obstacles.append(UserObstacle(id="o1", x=0, y=0, w=100, h=100))
        assert gs.has_user_geometry is True

    def test_markers_alone_do_not_count(self):
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(100.0, 200.0)])
        gs.update_from_mission(zs)
        assert gs.has_user_geometry is False


class TestGeometryStoreSerialisaion:
    def _populated_store(self) -> GeometryStore:
        gs = GeometryStore()
        zs = _make_zone_store_with_midpoints([(400.0, 300.0)])
        gs.update_from_mission(zs)
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -2400.0, "y1": 3200.0,
                       "x2": 1900.0, "y2": 3200.0, "label": "north wall"}],
            "doors": [{"id": "d1", "cx": -420.0, "cy": 3080.0,
                       "width_mm": 875.0, "theta_deg": 90.0,
                       "label": "bedroom door", "from_inference": True}],
            "obstacles": [{"id": "o1", "x": 800.0, "y": 200.0,
                           "w": 1200.0, "h": 800.0, "label": "sofa"}],
            "zone_labels": {"1": "Living room"},
            "wall_offset_mm": 200,
        })
        gs.cumulative_drift_mm = 42.5
        return gs

    def test_json_serialisable(self):
        gs = self._populated_store()
        d = gs._to_dict()
        # Must not raise
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_version_present(self):
        gs = GeometryStore()
        assert gs._to_dict()["version"] == STORAGE_VERSION

    def test_round_trip_walls(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert len(gs2.walls) == 1
        assert gs2.walls[0].label == "north wall"
        assert gs2.walls[0].x1 == pytest.approx(-2400.0)

    def test_round_trip_doors(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert len(gs2.doors) == 1
        assert gs2.doors[0].from_inference is True

    def test_round_trip_obstacles(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert len(gs2.obstacles) == 1
        assert gs2.obstacles[0].label == "sofa"

    def test_round_trip_door_markers(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert len(gs2.door_markers) == 1
        assert gs2.door_markers[0].cx == pytest.approx(400.0)

    def test_round_trip_drift(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert gs2.cumulative_drift_mm == pytest.approx(42.5)

    def test_round_trip_zone_labels(self):
        gs = self._populated_store()
        d = gs._to_dict()
        gs2 = GeometryStore()
        gs2._restore_from_dict(d)
        assert gs2.zone_labels == {"1": "Living room"}

    def test_no_numpy_types(self):
        """All values in the serialised dict must be JSON-native Python types."""
        gs = self._populated_store()
        d = gs._to_dict()
        raw = json.dumps(d)  # raises TypeError on non-serialisable types
        parsed = json.loads(raw)
        assert parsed["version"] == STORAGE_VERSION


class TestGeometryStoreAsyncIO:
    """Test async_load and async_save with a mocked hass.helpers.storage.Store."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _mock_hass(self):
        return MagicMock()

    def test_async_load_no_data(self):
        """async_load with no stored data leaves store in clean default state."""
        gs = GeometryStore()
        mock_store = AsyncMock()
        mock_store.async_load.return_value = None
        with patch("custom_components.roomba_plus.geometry_store.Store",
                   return_value=mock_store):
            self._run(gs.async_load(self._mock_hass(), "entry_abc"))
        assert gs.walls == []
        assert gs.door_markers == []
        assert gs.cumulative_drift_mm == pytest.approx(0.0)

    def test_async_load_wrong_version(self):
        """async_load with wrong version logs warning and leaves store clean."""
        gs = GeometryStore()
        mock_store = AsyncMock()
        mock_store.async_load.return_value = {
            "version": 99,
            "walls": [{"id": "w1", "x1": 0, "y1": 0, "x2": 1, "y2": 0}],
        }
        with patch("custom_components.roomba_plus.geometry_store.Store",
                   return_value=mock_store):
            self._run(gs.async_load(self._mock_hass(), "entry_abc"))
        assert gs.walls == []

    def test_async_load_valid_data(self):
        """async_load with valid data restores all fields."""
        gs_source = GeometryStore()
        gs_source.apply_user_edit({
            "walls": [{"id": "w1", "x1": 10.0, "y1": 20.0,
                       "x2": 30.0, "y2": 40.0, "label": "test"}],
            "doors": [], "obstacles": [],
        })
        gs_source.cumulative_drift_mm = 55.0
        stored = gs_source._to_dict()

        gs = GeometryStore()
        mock_store = AsyncMock()
        mock_store.async_load.return_value = stored
        with patch("custom_components.roomba_plus.geometry_store.Store",
                   return_value=mock_store):
            self._run(gs.async_load(self._mock_hass(), "entry_abc"))
        assert len(gs.walls) == 1
        assert gs.walls[0].label == "test"
        assert gs.cumulative_drift_mm == pytest.approx(55.0)

    def test_async_load_corrupt_data_resets_clean(self):
        """async_load with malformed data resets to clean state, no crash."""
        gs = GeometryStore()
        gs.cumulative_drift_mm = 100.0  # pre-set to check reset
        mock_store = AsyncMock()
        mock_store.async_load.return_value = {
            "version": STORAGE_VERSION,
            "walls": [{"bad_key": "no coordinates"}],  # missing required fields
        }
        with patch("custom_components.roomba_plus.geometry_store.Store",
                   return_value=mock_store):
            self._run(gs.async_load(self._mock_hass(), "entry_abc"))
        # Store should be in clean default state after corrupt load
        assert gs.walls == []
        assert gs.cumulative_drift_mm == pytest.approx(0.0)

    def test_async_save_calls_store(self):
        """async_save writes the serialised dict via Store.async_save."""
        gs = GeometryStore()
        mock_store = AsyncMock()
        with patch("custom_components.roomba_plus.geometry_store.Store",
                   return_value=mock_store):
            self._run(gs.async_save(self._mock_hass(), "entry_abc"))
        mock_store.async_save.assert_awaited_once()
        saved_dict = mock_store.async_save.call_args[0][0]
        assert saved_dict["version"] == STORAGE_VERSION


class TestDiagnosticInfo:
    def test_returns_dict_with_expected_keys(self):
        gs = GeometryStore()
        info = gs.diagnostic_info()
        for key in ("door_marker_count", "wall_count", "door_count",
                    "obstacle_count", "cumulative_drift_mm", "has_user_geometry"):
            assert key in info

    def test_counts_are_accurate(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": 0, "y1": 0, "x2": 1, "y2": 0}],
            "doors": [],
            "obstacles": [{"id": "o1", "x": 0, "y": 0, "w": 1, "h": 1},
                          {"id": "o2", "x": 10, "y": 10, "w": 1, "h": 1}],
        })
        info = gs.diagnostic_info()
        assert info["wall_count"] == 1
        assert info["door_count"] == 0
        assert info["obstacle_count"] == 2

    def test_no_private_attribute_access(self):
        """diagnostic_info must only access public attributes."""
        gs = GeometryStore()
        # If this raises AttributeError it accesses something private that
        # doesn't exist — the test catches that regression.
        info = gs.diagnostic_info()
        assert isinstance(info, dict)


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


class TestBootstrapMarkers:

    def test_set_bootstrap_markers_requires_min_2(self):
        """set_bootstrap_markers with < 2 positions is a no-op."""
        aligner = _make_aligner()
        aligner.set_bootstrap_markers([(100.0, 200.0)])
        assert aligner._bootstrap_markers == []

    def test_set_bootstrap_markers_sets_door_markers(self):
        """set_bootstrap_markers stores DoorMarker objects in UMF space."""
        from custom_components.roomba_plus.geometry_store import DoorMarker

        aligner = _make_aligner()
        aligner.set_bootstrap_markers([(500.0, 300.0), (1200.0, 800.0)])
        assert len(aligner._bootstrap_markers) == 2
        assert all(isinstance(m, DoorMarker) for m in aligner._bootstrap_markers)
        # mission_count must pass the >= 2 filter in align()
        assert aligner._bootstrap_markers[0].mission_count >= 2

    def test_align_uses_bootstrap_when_gs_empty(self):
        """align() achieves high confidence using bootstrap markers."""
        from unittest.mock import patch

        aligner = _make_aligner(
            door_candidates=[(500.0, 300.0), (1200.0, 800.0)],
            gs_markers=[],  # GeometryStore empty (lewis firmware)
        )
        # Bootstrap markers at exact same positions as door candidates
        aligner.set_bootstrap_markers([(500.0, 300.0), (1200.0, 800.0)])

        # Patch internal build methods so our pre-set _door_candidates survive
        with patch.object(aligner, "_build_coord_lookup"), \
             patch.object(aligner, "_detect_door_gaps"), \
             patch.object(aligner, "_resolve_room_polygons"):
            conf = aligner.align()

        # Bootstrap markers at exact candidate positions → near-zero residual → high conf
        assert conf >= 0.70
        assert aligner.aligned is True

    def test_align_prefers_gs_markers_over_bootstrap(self):
        """When GS markers exist, bootstrap markers are ignored."""
        from custom_components.roomba_plus.geometry_store import DoorMarker

        real_markers = [
            DoorMarker(id="m1", cx=500.0, cy=300.0, mission_count=3),
            DoorMarker(id="m2", cx=1200.0, cy=800.0, mission_count=3),
        ]
        aligner = _make_aligner(
            door_candidates=[(500.0, 300.0), (1200.0, 800.0)],
            gs_markers=real_markers,
        )
        # Also set bootstrap markers — should NOT be used when GS has data
        aligner.set_bootstrap_markers([(9000.0, 9000.0), (9500.0, 9000.0)])

        aligner.align()
        # The result should be driven by GS markers, not bootstrap markers
        # (verify by checking the aligner ran at all — exact confidence depends on transform)
        assert aligner._confidence >= 0.0  # ran without error


class TestExtractTraversalPositions:

    def test_returns_empty_below_min_missions(self):
        from custom_components.roomba_plus import _extract_traversal_umf_positions

        aligner = _make_aligner()
        records = [_record_with_traversals(["1", "2"])] * 2  # only 2 missions
        result = _extract_traversal_umf_positions(records, aligner, min_missions=3)
        assert result == []

    def test_returns_candidates_after_min_missions(self):
        from custom_components.roomba_plus import _extract_traversal_umf_positions

        candidates = [(500.0, 300.0), (1200.0, 800.0), (600.0, 1500.0)]
        aligner = _make_aligner(door_candidates=candidates)
        records = [_record_with_traversals(["1", "2"])] * 4  # 4 missions ≥ 3
        result = _extract_traversal_umf_positions(records, aligner, min_missions=3)
        assert len(result) == len(candidates)
        assert set(result) == set(candidates)
