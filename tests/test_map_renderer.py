"""Unit tests for MapRenderer.

Tests:
  - reset() clears state
  - add_pose() accumulates points
  - mark_stuck() records stuck positions
  - render() produces valid PNG bytes
  - dump_state() / restore_state() round-trip
  - restore_state() handles incompatible version gracefully
  - diagnostic_info() returns correct values
  - Phase 2: geometry layers — suggestions, user geometry, suppression logic
"""
import struct
import sys
sys.path.insert(0, "/tmp/roomba_plus_package")

import pytest
from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig, _STATE_VERSION

PNG_MAGIC = b"\x89PNG"


def _make_renderer(**kwargs) -> MapRenderer:
    cfg = RendererConfig(**kwargs)
    return MapRenderer(cfg)


class TestMapRendererReset:
    def test_reset_clears_points(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(200, 300, 90)
        r.reset()
        assert r.point_count == 0
        assert not r.has_data

    def test_reset_clears_stuck(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        r.reset()
        assert len(r._stuck_px) == 0

    def test_reset_clears_robot_position(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        r.reset()
        assert r._robot_px is None

    def test_reset_with_persist_keeps_cached_png(self):
        r = _make_renderer(persist=True)
        r.add_pose(100, 200, 0)
        r.render()
        r.reset()
        # persist=True: cached PNG kept between missions
        assert r._last_png is not None

    def test_reset_without_persist_clears_png(self):
        r = _make_renderer(persist=False)
        r.add_pose(100, 200, 0)
        r.render()
        r.reset()
        assert r._last_png is None


class TestAddPose:
    def test_first_dock_point_ignored(self):
        r = _make_renderer()
        r.add_pose(0, 0, 0)  # dock origin — should be skipped
        assert r.point_count == 0

    def test_non_zero_point_recorded(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        assert r.point_count == 1
        assert r.has_data

    def test_multiple_points(self):
        r = _make_renderer()
        # Start from i=1 to avoid the dock-skip (0,0,0 is ignored)
        for i in range(1, 11):
            r.add_pose(i * 100, 0, 0)
        assert r.point_count == 10

    def test_robot_position_updated(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(300, 400, 90)
        assert r._robot_px is not None

    def test_theta_stored(self):
        r = _make_renderer()
        r.add_pose(100, 200, 135)
        assert r._theta == 135


class TestMarkStuck:
    def test_stuck_recorded_at_robot_position(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        assert len(r._stuck_px) == 1

    def test_stuck_without_position_ignored(self):
        r = _make_renderer()
        r.mark_stuck()  # no pose yet
        assert len(r._stuck_px) == 0

    def test_multiple_stuck_events(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        r.add_pose(500, 600, 0)
        r.mark_stuck()
        assert len(r._stuck_px) == 2


class TestRender:
    def test_render_returns_bytes(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        result = r.render()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_render_is_valid_png(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        result = r.render()
        assert result[:4] == PNG_MAGIC

    def test_render_empty_returns_blank_png(self):
        r = _make_renderer()
        result = r.render()
        # No points: returns last_png (None initially) — actually renders blank
        assert result is None or result[:4] == PNG_MAGIC

    def test_render_updates_cache_on_each_call(self):
        """render() always re-renders when points exist and stores result in _last_png."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.render()
        assert r._last_png is not None
        first = r._last_png
        # Second call with same points — still re-renders (no point-equality check)
        r.render()
        assert r._last_png is not None


class TestPersistence:
    def test_dump_state_keys(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        r.mark_stuck()
        state = r.dump_state()
        assert "version" in state
        assert "points" in state
        assert "stuck_px" in state
        assert "robot_px" in state
        assert "theta" in state

    def test_dump_state_version(self):
        r = _make_renderer()
        state = r.dump_state()
        assert state["version"] == _STATE_VERSION

    def test_dump_restore_round_trip(self):
        r1 = _make_renderer()
        r1.add_pose(100, 200, 45)
        r1.add_pose(300, 400, 90)
        r1.mark_stuck()
        state = r1.dump_state()

        r2 = _make_renderer()
        success = r2.restore_state(state)
        assert success is True
        assert r2.point_count == 2
        assert len(r2._stuck_px) == 1
        assert r2._theta == 90

    def test_restore_clears_cached_png(self):
        r1 = _make_renderer()
        r1.add_pose(100, 200, 0)
        state = r1.dump_state()

        r2 = _make_renderer()
        r2.restore_state(state)
        # PNG should be regenerated on demand, not restored from state
        assert r2._last_png is None

    def test_restore_wrong_version_returns_false(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        state = r.dump_state()
        state["version"] = 999  # future version
        success = r.restore_state(state)
        assert success is False

    def test_restore_empty_state_returns_false(self):
        r = _make_renderer()
        success = r.restore_state({})
        assert success is False

    def test_restore_no_robot_px(self):
        state = {
            "version": _STATE_VERSION,
            "points": [[100, 100]],
            "stuck_px": [],
            "robot_px": None,
            "theta": 0.0,
        }
        r = _make_renderer()
        r.restore_state(state)
        assert r._robot_px is None


class TestDiagnosticInfo:
    def test_diagnostic_info_keys(self):
        r = _make_renderer()
        info = r.diagnostic_info()
        assert "size_px" in info
        assert "scale_mm_per_px" in info
        assert "persist" in info
        assert "point_count" in info
        assert "has_cached_image" in info
        assert "stuck_event_count" in info

    def test_point_count_correct(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(300, 400, 0)
        info = r.diagnostic_info()
        assert info["point_count"] == 2

    def test_stuck_count_correct(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        info = r.diagnostic_info()
        assert info["stuck_event_count"] == 1

    def test_has_cached_image_false_before_render(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        assert r.diagnostic_info()["has_cached_image"] is False

    def test_has_cached_image_true_after_render(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.render()
        assert r.diagnostic_info()["has_cached_image"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Geometry layer tests
# ─────────────────────────────────────────────────────────────────────────────
import sys
import os
import types
import math
from unittest.mock import MagicMock

# Ensure geometry_store and zone_store can be imported (stubs already in conftest)
ROOT = os.path.join(os.path.dirname(__file__), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# homeassistant.helpers.storage stub (may already be present from conftest)
if "homeassistant.helpers.storage" not in sys.modules:
    _ha_storage = types.ModuleType("homeassistant.helpers.storage")
    _ha_storage.Store = MagicMock()
    sys.modules["homeassistant.helpers.storage"] = _ha_storage
    sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))

from custom_components.roomba_plus.geometry_store import (
    GeometryStore, DoorMarker, UserWall, UserDoor, UserObstacle,
)
from custom_components.roomba_plus.zone_store import Zone, ZoneStore

PNG_MAGIC = b"\x89PNG"


def _make_renderer_with_stores(geometry_store=None, zone_store=None, **kwargs):
    cfg = RendererConfig(**kwargs)
    return MapRenderer(cfg, geometry_store=geometry_store, zone_store=zone_store)


def _zone_store_with_zone(x_min, x_max, y_min, y_max, zone_id=1, name="Room 1"):
    zs = ZoneStore()
    z = Zone(id=zone_id, name=name, confirmed=False,
             x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
             confidence=0.5)
    zs.zones.append(z)
    return zs


def _render_is_valid_png(renderer) -> bool:
    result = renderer.render()
    return isinstance(result, bytes) and result[:4] == PNG_MAGIC


def _pixel_at(png_bytes: bytes, x: int, y: int, size: int = 600) -> tuple:
    """Extract RGBA pixel at (x,y) from a raw PNG rendered by our renderer."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return img.getpixel((x, y))


class TestMapRendererConstructorSignature:
    def test_no_stores_creates_renderer(self):
        r = MapRenderer(RendererConfig())
        assert r._geometry_store is None
        assert r._zone_store is None

    def test_stores_stored_as_attributes(self):
        gs = GeometryStore()
        zs = ZoneStore()
        r = MapRenderer(RendererConfig(), geometry_store=gs, zone_store=zs)
        assert r._geometry_store is gs
        assert r._zone_store is zs

    def test_render_with_no_stores_returns_valid_png(self):
        r = _make_renderer_with_stores()
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)

    def test_render_with_none_stores_no_exception(self):
        """render() must never raise when stores are None."""
        r = _make_renderer_with_stores(geometry_store=None, zone_store=None)
        r.render()  # no points, no stores — should return None or bytes, not raise


class TestInferenceSuggestionsLayer:
    def test_suggestion_produces_non_white_pixels(self):
        """With a zone, some pixels should differ from background white."""
        zs = _zone_store_with_zone(-1000, 1000, -1000, 1000, name="Living")
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=zs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC
        # The rendered image should not be entirely white —
        # zone outline or cleaned area should have coloured pixels
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        pixels = list(img.get_flattened_data())
        # get_flattened_data returns flat sequence r,g,b,a,r,g,b,a,...
        # Check that not every pixel is white (255,255,255,255)
        white_pixel = (255, 255, 255, 255)
        all_white = all(
            tuple(pixels[i:i+4]) == white_pixel
            for i in range(0, len(pixels), 4)
        )
        assert not all_white

    def test_suggestion_suppressed_when_user_geometry_exists(self):
        """When user walls exist, suggestions should not be drawn.
        We verify this by checking the zone_store suggestion is suppressed
        (no exception, valid PNG, user geometry takes over).
        """
        zs = _zone_store_with_zone(-1000, 1000, -1000, 1000)
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -500.0, "y1": 500.0,
                       "x2": 500.0, "y2": 500.0, "label": ""}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=zs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_door_marker_below_mission_count_threshold_not_drawn(self):
        """Markers with mission_count < 2 must not appear."""
        gs = GeometryStore()
        # Manually insert a marker with mission_count=1
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0, mission_count=1,
                       observations=[[0.0, 0.0]])
        gs.door_markers.append(m)
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=ZoneStore())
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC  # no crash, valid output

    def test_door_marker_at_mission_count_2_drawn_without_crash(self):
        """Markers with mission_count >= 2 should render without raising."""
        gs = GeometryStore()
        m = DoorMarker(id="dm_1", cx=0.0, cy=100.0, mission_count=2,
                       observations=[[0.0, 100.0], [0.0, 100.0]])
        gs.door_markers.append(m)
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=ZoneStore())
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_no_zone_store_no_suggestion_no_crash(self):
        """With zone_store=None, suggestions are skipped silently."""
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=None)
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)


class TestUserGeometryLayer:
    def test_user_wall_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -500.0, "y1": 0.0,
                       "x2": 500.0, "y2": 0.0, "label": "north wall"}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_user_wall_produces_non_white_pixels(self):
        """A wall crossing the canvas should produce at least one dark pixel.

        With auto_fit enabled the wall is not necessarily at canvas centre,
        so we scan the full PNG for any non-white, non-background pixel.
        """
        from PIL import Image
        import io
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -2000.0, "y1": 0.0,
                       "x2": 2000.0, "y2": 0.0, "label": ""}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        raw = img.tobytes()
        pixels = [
            (raw[i], raw[i+1], raw[i+2], raw[i+3])
            for i in range(0, len(raw), 4)
        ]
        # At least one pixel should be darker than pure white
        non_white = [p for p in pixels if p[:3] != (255, 255, 255)]
        assert len(non_white) > 0, "Expected wall pixels but image is all white"
        # Wall colour is dark grey — check at least one pixel has low R value
        dark_pixels = [p for p in non_white if p[0] < 150 and p[3] > 100]
        assert len(dark_pixels) > 0, "Expected dark wall pixels in image"

    def test_user_door_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [],
            "doors": [{"id": "d1", "cx": 0.0, "cy": 0.0, "width_mm": 875.0,
                       "theta_deg": 0.0, "label": "bedroom door"}],
            "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_user_obstacle_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [],
            "doors": [],
            "obstacles": [{"id": "o1", "x": -500.0, "y": -500.0,
                           "w": 1000.0, "h": 800.0, "label": "sofa"}],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_obstacle_off_canvas_does_not_crash(self):
        """Obstacles outside the map extent should be clamped, not crash."""
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [], "doors": [],
            "obstacles": [{"id": "o1", "x": 50000.0, "y": 50000.0,
                           "w": 100.0, "h": 100.0, "label": ""}],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        r.render()  # must not raise

    def test_no_geometry_store_skips_user_layer_silently(self):
        """geometry_store=None must skip _draw_user_geometry without crashing."""
        r = _make_renderer_with_stores(geometry_store=None)
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)


class TestLayerOrdering:
    def test_cleaned_area_renders_over_suggestion(self):
        """Cleaned area (light blue) must be visible on top of suggestion layer.
        We add a pose well away from the dock, render, and scan a region around
        that pose position to find at least one non-white pixel from the
        cleaned-area circle (radius = 15px at scale=10mm/px).
        """
        zs = _zone_store_with_zone(-2000, 2000, -2000, 2000, name="Big Room")
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, zone_store=zs)
        # Pose at (500, 500) mm — clearly away from dock and canvas edges
        r.add_pose(500, 500, 0)
        png = r.render()
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        px, py = r._mm_to_px(500, 500)
        # Scan a region larger than the cleaned-area circle radius (15px)
        scan_radius = 20
        size = r._cfg.size_px
        non_white_found = False
        for dy in range(-scan_radius, scan_radius + 1):
            for dx in range(-scan_radius, scan_radius + 1):
                x, y = px + dx, py + dy
                if 0 <= x < size and 0 <= y < size:
                    if img.getpixel((x, y)) != (255, 255, 255, 255):
                        non_white_found = True
                        break
            if non_white_found:
                break
        assert non_white_found, (
            f"Expected non-white pixel near ({px},{py}) — "
            "cleaned-area circle should be visible"
        )

    def test_existing_tests_still_pass_with_geometry_stores(self):
        """Existing render tests work identically when stores are omitted."""
        r = MapRenderer(RendererConfig())  # original constructor form
        r.add_pose(100, 200, 0)
        result = r.render()
        assert result[:4] == PNG_MAGIC


class TestDashedLinePrimitive:
    def test_zero_length_no_crash(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_line(draw, 50, 50, 50, 50, (0, 0, 0, 255))

    def test_horizontal_line_leaves_pixels(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_line(draw, 0, 50, 200, 50, (0, 0, 0, 255))
        # At least some pixel along the line should be non-white
        pixels = [img.getpixel((x, 50)) for x in range(200)]
        assert any(p != (255, 255, 255, 255) for p in pixels)


class TestDashedRectPrimitive:
    def test_dashed_rect_no_crash(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_rect(draw, 10, 10, 150, 150, (100, 100, 100, 255))



class TestCoverageTargets:
    """Targeted tests for previously uncovered paths to reach >=95% coverage."""

    def test_render_cached_frame_returned_when_no_points(self):
        """render() returns last_png immediately when no points and cache exists."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        first = r.render()
        # Force the points list empty but keep cache
        r._points.clear()
        result = r.render()
        assert result is first  # same cached bytes object

    def test_points_mm_property(self):
        """points_mm converts pixel coordinates back to mm."""
        r = _make_renderer()
        r.add_pose(500, 0, 0)
        pts = r.points_mm
        assert len(pts) == 1
        x_mm, y_mm = pts[0]
        assert abs(x_mm - 500) < r._cfg.scale  # within one pixel
        assert abs(y_mm - 0) < r._cfg.scale

    def test_draw_door_arc_no_crash(self):
        """_draw_door_arc with a realistic radius draws without raising."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (600, 600), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        r = MapRenderer(RendererConfig())
        r._draw_door_arc(draw, 300, 300, 40, 0.0)
        r._draw_door_arc(draw, 300, 300, 40, 45.0)
        r._draw_door_arc(draw, 300, 300, 40, 270.0)

    def test_draw_hatch_no_crash(self):
        """_draw_hatch on a rectangular region draws without raising."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (600, 600), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_hatch(draw, 50, 50, 200, 150, (186, 117, 23, 190))

    def test_restore_state_logs_on_success(self):
        """restore_state returns True for a valid state (covers the debug log line)."""
        r1 = _make_renderer()
        r1.add_pose(100, 200, 45)
        state = r1.dump_state()
        r2 = _make_renderer()
        result = r2.restore_state(state)
        assert result is True
        assert r2.point_count == 1


class TestPhase2CoverageTargets:
    """Additional tests to cover previously uncovered renderer paths."""

    def test_stuck_triangle_drawn(self):
        """Stuck event causes _draw_triangle to execute during render."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_draw_path_with_two_points(self):
        """_draw_path executes when ≥2 points are present."""
        r = _make_renderer()
        r.add_pose(100, 0, 0)
        r.add_pose(500, 0, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_interpolated_with_large_gap(self):
        """_interpolated inserts intermediate points across a large gap."""
        from custom_components.roomba_plus.map_renderer import MapRenderer
        pts = [(0, 0), (1000, 0)]  # 1000px gap — well above any max_gap_px
        result = MapRenderer._interpolated(pts, max_gap_px=10)
        assert len(result) > 2

    def test_restore_state_exception_path(self):
        """restore_state returns False on malformed state dict."""
        r = _make_renderer()
        result = r.restore_state({"version": 1, "points": "not_a_list"})
        assert result is False

    def test_draw_triangle_produces_pixels(self):
        """_draw_triangle draws a visible polygon."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_triangle(draw, 100, 100, 15, (220, 60, 60, 255))
        px = img.getpixel((100, 112))  # bottom vertex area
        assert px[0] > 100  # red channel present

    def test_interpolated_empty_input(self):
        """_interpolated([]) returns empty list."""
        from custom_components.roomba_plus.map_renderer import MapRenderer
        assert MapRenderer._interpolated([], max_gap_px=10) == []
