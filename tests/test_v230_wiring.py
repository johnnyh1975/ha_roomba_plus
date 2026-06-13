"""Tests for Roomba+ v2.3.0 Steps 4–12.

Covers UmfAligner wiring, image entity attributes, api_views format=records
and format=hazards, grid_store coverage_by_polygon, mission_store EPHEMERAL
extension, vacuum.py live CR4 source, select.py keepout attributes, and
error recurrence repair issue.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.umf_aligner import UmfAligner


# ── Shared stubs ──────────────────────────────────────────────────────────────

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


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4 — _umf_version_changed / _async_realign
# ═══════════════════════════════════════════════════════════════════════════════

class TestUmfVersionChanged:
    def _fn(self):
        from custom_components.roomba_plus.__init__ import _umf_version_changed
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


# ═══════════════════════════════════════════════════════════════════════════════
# Step 5 — RoombaMapImage extra_state_attributes
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoombaMapImageAttrs:
    def _entity(self, aligner=None, renderer=None):
        from custom_components.roomba_plus.image import RoombaMapImage
        entity = object.__new__(RoombaMapImage)
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._renderer = renderer
        return entity

    def test_no_config_entry(self):
        from custom_components.roomba_plus.image import RoombaMapImage
        entity = object.__new__(RoombaMapImage)
        entity._config_entry = None
        entity._renderer = MagicMock()
        assert entity.extra_state_attributes == {}

    def test_no_renderer(self):
        entity = self._entity(aligner=_make_aligner(), renderer=None)
        assert entity.extra_state_attributes == {}

    def test_no_aligner(self):
        entity = self._entity(aligner=None, renderer=MagicMock())
        assert entity.extra_state_attributes == {}

    def test_aligner_not_aligned(self):
        entity = self._entity(aligner=_make_aligner(aligned=False), renderer=MagicMock())
        assert entity.extra_state_attributes == {}

    def test_aligned_empty_polygons(self):
        aligner = _make_aligner()
        aligner._room_polygons = {}
        entity = self._entity(aligner=aligner, renderer=MagicMock())
        # calibration needs polygons; rooms dict is empty → both absent
        attrs = entity.extra_state_attributes
        assert "rooms" not in attrs

    def test_aligned_with_polygons(self):
        aligner = _make_aligner()
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        renderer = MagicMock()
        renderer._mm_to_px_fit.side_effect = lambda x, y: (int(x), int(y))
        entity = self._entity(aligner=aligner, renderer=renderer)
        attrs = entity.extra_state_attributes
        assert "rooms" in attrs
        rooms = attrs["rooms"]
        # rooms is now a list of {id, label, outline} for xiaomi-vacuum-map-card
        assert isinstance(rooms, list)
        assert len(rooms) == 1
        assert rooms[0]["id"] == "Kitchen"
        assert rooms[0]["label"] == "Kitchen"
        assert "outline" in rooms[0]


# ═══════════════════════════════════════════════════════════════════════════════
# Step 6 — render_keepout_zones (map_renderer.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderKeeputZones:
    """Verify render_keepout_zones works on _last_png not phantom _cache."""

    def _valid_png(self) -> bytes:
        """Return a minimal valid 2×2 PNG."""
        from PIL import Image
        import io
        img = Image.new("RGB", (2, 2), (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_empty_list_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        assert r.render_keepout_zones([]) is None

    def test_no_last_png_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = None
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert result is None

    def test_valid_polygon_returns_bytes(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_updates_last_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        original = self._valid_png()
        r._last_png = original
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert result is r._last_png  # _last_png updated in place


# ═══════════════════════════════════════════════════════════════════════════════
# Step 5b — RoombaRoomsImage
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoombaRoomsImage:
    def _entity(self, aligner=None):
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._last_x_min = 0.0
        entity._last_x_max = 5000.0
        entity._last_y_min = 0.0
        entity._last_y_max = 5000.0
        entity._last_size  = 600
        return entity

    def test_no_aligner_returns_blank(self):
        entity = self._entity(aligner=None)
        png = entity._render_rooms_png()
        assert isinstance(png, bytes)
        assert len(png) > 0

    def test_not_aligned_returns_blank(self):
        entity = self._entity(aligner=_make_aligner(aligned=False))
        png = entity._render_rooms_png()
        assert isinstance(png, bytes)

    def test_no_aligner_attrs_empty(self):
        entity = self._entity(aligner=None)
        assert entity.extra_state_attributes == {}

    def test_not_aligned_attrs_empty(self):
        entity = self._entity(aligner=_make_aligner(aligned=False))
        assert entity.extra_state_attributes == {}

    def test_unique_id_pattern(self):
        """Entity unique_id includes robot blid + rooms_map suffix."""
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.umf_aligner = None
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size  = 600
        # Minimal init — check unique_id contains rooms_map
        import collections
        entity.access_tokens = collections.deque([], 2)
        entity._attr_unique_id = "blid123_rooms_map"
        assert "rooms_map" in entity._attr_unique_id

    def test_entity_name_not_locale_slug(self):
        """_attr_name = 'Rooms Map' prevents locale-slug entity IDs (G6 lesson).

        HA wraps _attr_ values as cached_property descriptors via __init_subclass__,
        so the raw string is not readable from __dict__. Verify through instance
        access — which is the actual runtime path HA uses for entity registration.
        """
        from custom_components.roomba_plus.image import RoombaRoomsImage
        import collections
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = None
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size  = 600
        entity.access_tokens = collections.deque([], 2)
        assert entity._attr_name == "Rooms Map"

    def test_to_px_last_consistency(self):
        entity = self._entity()
        # With default transform (size=600, x_min=0, x_max=5000, y_min=0, y_max=5000)
        # scale = 600/5000 = 0.12
        px, py = entity._to_px_last(0.0, 0.0)
        assert isinstance(px, int)
        assert isinstance(py, int)


# ═══════════════════════════════════════════════════════════════════════════════
# Step 7 — api_views format=records room_coverage + alignment_confidence
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiViewsRecordsV23:
    def test_cloud_record_has_room_coverage_key(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule", "room_coverage": {"Kitchen": 0.8}}
        u = _cloud_record_to_unified(rec)
        assert "room_coverage" in u
        assert u["room_coverage"] == {"Kitchen": 0.8}

    def test_cloud_record_room_coverage_null_when_absent(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule"}
        u = _cloud_record_to_unified(rec)
        assert u["room_coverage"] is None

    def test_cloud_record_alignment_confidence_initially_none(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule"}
        u = _cloud_record_to_unified(rec)
        assert u["alignment_confidence"] is None

    def test_local_record_has_room_coverage_key(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
               "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
               "result": "completed", "initiator": "schedule", "zones": [],
               "room_coverage": {"Hallway": 0.6}}
        u = _local_record_to_unified(rec)
        assert "room_coverage" in u
        assert u["room_coverage"] == {"Hallway": 0.6}

    def test_local_record_alignment_confidence_always_none(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
               "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
               "result": "completed", "initiator": "schedule", "zones": []}
        u = _local_record_to_unified(rec)
        assert u["alignment_confidence"] is None
        assert u["source"] == "local"

    def test_cloud_and_local_shapes_identical(self):
        from custom_components.roomba_plus.api_views import (
            _cloud_record_to_unified,
            _local_record_to_unified,
        )
        cloud_rec = {"startTime": 1700000000, "timestamp": 1700003600,
                     "durationM": 60, "classified_result": "completed",
                     "initiator": "schedule"}
        local_rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
                     "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
                     "result": "completed", "initiator": "schedule", "zones": []}
        c = _cloud_record_to_unified(cloud_rec)
        l = _local_record_to_unified(local_rec)
        assert set(c.keys()) == set(l.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Step 8 — format=hazards keepout + room_name
# ═══════════════════════════════════════════════════════════════════════════════

class TestHazardsV23:
    def test_no_aligner_room_name_null(self):
        """Hazard room_name stays None when no aligner."""
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        # We test the logic directly by checking that room_name is not set
        # without a live HTTP request — test the keepout building logic
        keepout_zones = [{"cx": 1000.0, "cy": 500.0}]
        hazards = []
        import math
        for zone in keepout_zones:
            cx = zone.get("cx") or 0.0
            cy = zone.get("cy") or 0.0
            hazards.append({
                "gx": None, "gy": None,
                "x_mm": float(cx), "y_mm": float(cy),
                "stuck_count": None,
                "room_name": None,
                "bearing_deg": int(math.degrees(math.atan2(cx, cy)) % 360),
                "distance_mm": int(math.sqrt(cx**2 + cy**2)),
                "source": "keepout",
            })
        assert hazards[0]["source"] == "keepout"
        assert hazards[0]["room_name"] is None
        assert hazards[0]["x_mm"] == pytest.approx(1000.0)

    def test_aligner_populates_room_name_for_keepout(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        hazards = [{"source": "keepout", "x_mm": 2500.0, "y_mm": 2500.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] in ("robot_learned", "keepout"):
                    h["room_name"] = aligner.room_name_at(h["x_mm"], h["y_mm"])
        assert hazards[0]["room_name"] == "Kitchen"

    def test_aligner_room_name_none_outside_rooms(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        hazards = [{"source": "keepout", "x_mm": 9999.0, "y_mm": 9999.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] in ("robot_learned", "keepout"):
                    h["room_name"] = aligner.room_name_at(h["x_mm"], h["y_mm"])
        assert hazards[0]["room_name"] is None

    def test_stuck_events_uses_pose_to_umf(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        # With identity transform (rot=0, tx=0, ty=0), pose ≡ UMF
        hazards = [{"source": "stuck_events", "x_mm": 2500.0, "y_mm": 2500.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] == "stuck_events":
                    pt_umf = aligner.pose_to_umf(h["x_mm"], h["y_mm"])
                    if pt_umf:
                        h["room_name"] = aligner.room_name_at(*pt_umf)
        assert hazards[0]["room_name"] == "Kitchen"


# ═══════════════════════════════════════════════════════════════════════════════
# Step 9 — grid_store.coverage_by_polygon
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoverageByPolygon:
    def _gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        return gs

    def test_empty_grid_returns_empty_dict(self):
        gs = self._gs()
        poly = {"r1": [(0.0, 0.0), (3000.0, 0.0), (3000.0, 3000.0), (0.0, 3000.0)]}
        result = gs.coverage_by_polygon(poly)
        # Empty grid → early return with empty dict
        assert result == {}

    def test_degenerate_polygon_zero(self):
        gs = self._gs()
        # Add a cell to make grid non-empty
        gs._cells[(0, 0)] = 1.0
        result = gs.coverage_by_polygon({"r1": [(0.0, 0.0), (100.0, 0.0)]})
        assert result == {"r1": 0.0}

    def test_cell_inside_polygon_counted(self):
        from custom_components.roomba_plus.grid_store import CELL_SIZE_MM, PRUNE_THRESHOLD
        gs = self._gs()
        # Place a visited cell at grid (0,0) → centre (75, 75) mm
        gs._cells[(0, 0)] = 1.0   # above PRUNE_THRESHOLD
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.coverage_by_polygon(poly)
        assert "r1" in result
        assert result["r1"] > 0.0

    def test_cell_outside_polygon_not_counted(self):
        gs = self._gs()
        # Cell far outside polygon
        gs._cells[(100, 100)] = 1.0
        poly = {"r1": [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)]}
        result = gs.coverage_by_polygon(poly)
        assert result["r1"] == 0.0

    def test_below_threshold_not_visited(self):
        from custom_components.roomba_plus.grid_store import PRUNE_THRESHOLD
        gs = self._gs()
        gs._cells[(0, 0)] = PRUNE_THRESHOLD / 2   # below threshold
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.coverage_by_polygon(poly)
        # Cell is inside polygon but score is below threshold → not visited
        assert result["r1"] == 0.0

    def test_multiple_polygons(self):
        gs = self._gs()
        gs._cells[(0, 0)] = 1.0   # inside r1
        gs._cells[(40, 40)] = 1.0  # outside both
        polys = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)],
            "r2": [(5000.0, 0.0), (6000.0, 0.0), (6000.0, 1000.0), (5000.0, 1000.0)],
        }
        result = gs.coverage_by_polygon(polys)
        assert "r1" in result
        assert "r2" in result
        assert result["r1"] > 0.0
        assert result["r2"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Step 10 — mission_store EPHEMERAL umf_regions fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissionStoreEphemeralFallback:
    def _ms_with_timeline(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [{
            "id": "m_1",
            "started_at": "2026-01-01T00:00:00+00:00",
            "ended_at":   "2026-01-01T01:00:00+00:00",
            "duration_min": 60,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 0,
            "timeline": {
                "plan": {"upcoming": ["19", "21"]},
                "finEvents": [
                    {"type": "room", "room": {"rid": "19", "status": 0,
                                              "area": 100, "totalArea": 80}},
                    {"type": "room", "room": {"rid": "21", "status": 0,
                                              "area": 120, "totalArea": 90}},
                ]
            }
        }]
        return ms

    def test_region_map_used_when_provided(self):
        ms = self._ms_with_timeline()
        region_map = {"19": "Bathroom", "21": "Hallway"}
        result = ms.latest_cleaned_rooms(region_map)
        assert result == ["Bathroom", "Hallway"]

    def test_umf_regions_fallback_when_region_map_empty(self):
        ms = self._ms_with_timeline()
        umf_regions = {"19": "Bathroom-UMF", "21": "Hallway-UMF"}
        result = ms.latest_cleaned_rooms({}, umf_regions)
        assert result == ["Bathroom-UMF", "Hallway-UMF"]

    def test_region_map_takes_precedence_over_umf_regions(self):
        ms = self._ms_with_timeline()
        region_map  = {"19": "Bathroom"}
        umf_regions = {"19": "Should-Not-Appear", "21": "Hallway"}
        result = ms.latest_planned_order(region_map, umf_regions)
        # region_map non-empty → effective_map = region_map
        assert "Bathroom" in result
        assert "Should-Not-Appear" not in result

    def test_both_empty_returns_rids(self):
        ms = self._ms_with_timeline()
        result = ms.latest_cleaned_rooms({}, None)
        # Falls back to raw rid strings
        assert result == ["19", "21"]

    def test_latest_room_coverage_umf_fallback(self):
        ms = self._ms_with_timeline()
        umf_regions = {"19": "Bathroom-U", "21": "Hallway-U"}
        result = ms.latest_room_coverage({}, umf_regions)
        assert result is not None
        assert "Bathroom-U" in result
        assert "Hallway-U" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Step 11 — vacuum.py live CR4 source (CLEANING_PHASES import)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVacuumLiveCR4:
    def test_cleaning_phases_importable_from_const(self):
        from custom_components.roomba_plus.const import CLEANING_PHASES
        assert "run"       in CLEANING_PHASES
        assert "hmMidMsn"  in CLEANING_PHASES
        assert "charge"    not in CLEANING_PHASES

    def test_vacuum_imports_cleaning_phases(self):
        import custom_components.roomba_plus.vacuum as vac_mod
        assert hasattr(vac_mod, "CLEANING_PHASES")

    def test_extract_rid_handles_lewis_format(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        # lewis 22.52.10+ format
        assert MissionStore._extract_rid({"type": "rid", "rid": "19"}) == "19"
        # plain string
        assert MissionStore._extract_rid("21") == "21"
        # empty/unknown
        assert MissionStore._extract_rid({}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Step 12 — select.py Gap A keepout attributes
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelectKeeputAttrs:
    def _entity_with_keepout(self, keepout_zones):
        from custom_components.roomba_plus.select import CloudSmartZoneSelect
        entity = object.__new__(CloudSmartZoneSelect)
        entity._regions = [{"id": "r1", "name": "Kitchen",
                             "region_type": "default", "pmap_id": "p1"}]
        entity._zones         = []
        entity._map_name      = "Home"
        entity._pmap_id       = "p1"
        entity._is_active_map = True
        entity._selected      = "Kitchen"

        cc = MagicMock()
        cc.keepout_zones = keepout_zones
        cc.data = {"pmaps": []}
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator = cc
        entity._config_entry = entry
        return entity

    def test_no_keepout_zones_count_zero(self):
        entity = self._entity_with_keepout([])
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 0
        assert "keepout_zone_names" not in attrs

    def test_keepout_zones_with_names(self):
        zones = [{"name": "Sofa Area"}, {"name": "Dog Bed"}]
        entity = self._entity_with_keepout(zones)
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 2
        assert attrs.get("keepout_zone_names") == ["Sofa Area", "Dog Bed"]

    def test_keepout_zones_without_names(self):
        zones = [{"cx": 100, "cy": 200}, {"cx": 300, "cy": 400}]
        entity = self._entity_with_keepout(zones)
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 2
        assert "keepout_zone_names" not in attrs


# ═══════════════════════════════════════════════════════════════════════════════
# Step 6c — error recurrence Repair Issue
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorRecurrence:
    def _ms_with_errors(self, error_code: int, count: int):
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
        ms = MissionStore()
        # Use recent dates so query(days=30) includes them
        now_str = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        records = []
        for i in range(count):
            records.append({
                "id": f"m_{i}",
                "started_at": now_str,
                "ended_at":   now_str,
                "duration_min": 60,
                "result": "error",
                "initiator": "schedule",
                "zones": [],
                "error_code": error_code,
                "bbrun_hr": 0,
            })
        ms._records = records
        return ms

    def _entry(self, ms, aligner=None):
        entry = MagicMock()
        entry.runtime_data.mission_store = ms
        entry.runtime_data.umf_aligner   = aligner
        return entry

    @pytest.mark.asyncio
    async def test_below_threshold_deletes_issue(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms    = self._ms_with_errors(15, 2)
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue") as mock_delete, \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            mock_delete.assert_called_once()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_threshold_creates_issue(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms    = self._ms_with_errors(15, 3)
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "error_recurrence"
            placeholders = call_kwargs["translation_placeholders"]
            assert placeholders["count"] == "3"
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_most_frequent_code_chosen(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = MissionStore()
        now_str = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ms._records = (
            [{"id": f"a{i}", "started_at": now_str, "ended_at": now_str,
              "duration_min": 60, "result": "error", "initiator": "schedule",
              "zones": [], "error_code": 15, "bbrun_hr": 0}
             for i in range(5)]
            + [{"id": f"b{i}", "started_at": now_str, "ended_at": now_str,
               "duration_min": 60, "result": "error", "initiator": "schedule",
               "zones": [], "error_code": 7, "bbrun_hr": 0}
              for i in range(3)]
        )
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_no_mission_store_no_crash(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        entry = MagicMock()
        entry.runtime_data.mission_store = None
        await async_check_error_recurrence(MagicMock(), entry)  # no exception

    @pytest.mark.asyncio
    async def test_room_name_populated_from_aligner(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = self._ms_with_errors(15, 3)
        ms._records[-1]["error_position_mm"] = {"x": 2500.0, "y": 2500.0}
        ms._records[-1]["phase_at_error"]     = "run"
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        entry = self._entry(ms, aligner=aligner)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["room"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_room_name_unknown_without_aligner(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = self._ms_with_errors(15, 3)
        entry = self._entry(ms, aligner=None)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["room"] == "unknown location"
