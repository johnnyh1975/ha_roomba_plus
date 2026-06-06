"""Map renderer for Roomba+ — converts pose data to a PNG camera image.

Works for all map-capable robots (900, i, s, j, m series) because they all
report position in the same format:
  pose.point.x / pose.point.y  in mm, origin = dock (0, 0)
  pose.theta                   in degrees, counter-clockwise positive

Pillow is a Home Assistant Core dependency (Pillow==12.1.1 in requirements.txt)
so no manifest.json entry is needed.

Rendering pipeline (5 layers, bottom to top):
  1. Background — plain floor colour
  2. Cleaned area — circle per pose point (radius = half cleaning width)
  3. Path overlay — polyline connecting pose points
  4. Markers — stuck positions (triangle), dock (square)
  5. Robot icon — filled circle with direction arrow

Coordinate system:
  Roomba mm → pixel:  px = cx + x_mm / scale
                       py = cy - y_mm / scale   (image Y grows downward)
  Dock is always at image centre (cx, cy).

Persistence:
  dump_state() / restore_state() serialise the renderer's internal state
  (pose points, stuck positions, last heading) to a plain dict so that
  image.py can persist it across HA restarts via hass.storage.
  The cached PNG is NOT persisted — it is re-rendered on first async_image()
  call after restore, which is fast (<5 ms).
"""
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from .geometry_store import GeometryStore
    from .zone_store import ZoneStore

_LOGGER = logging.getLogger(__name__)

# ── Colours (RGBA) ────────────────────────────────────────────────────────────
BG_COLOUR       = (255, 255, 255, 255)  # White background (floor)
FLOOR_BORDER    = (220, 220, 220, 255)  # Light grey — subtle canvas border
CLEANED_COLOUR  = (173, 216, 230, 255)  # Light blue — cleaned area
PATH_COLOUR     = ( 30, 100, 200, 255)  # Deep blue — travel path
DOCK_COLOUR     = ( 80, 180,  80, 255)  # Green — dock station marker
ROBOT_COLOUR    = ( 30, 100, 200, 255)  # Blue — robot position
ARROW_COLOUR    = (255, 255, 255, 255)  # White — direction arrow on robot
STUCK_COLOUR    = (220,  60,  60, 255)  # Red — stuck event marker

# ── Geometry layer colours (RGBA) ─────────────────────────────────────────────
SUGGEST_OUTLINE  = (180, 180, 180, 180)  # Light grey — zone outline suggestion
SUGGEST_LABEL    = (160, 160, 160, 220)  # Grey — zone name text
DOOR_MARKER      = ( 24,  95, 165, 200)  # Blue — inferred door crossing marker
WALL_FILL        = ( 74,  85, 104, 255)  # Dark grey — user wall
WALL_CENTRE      = (255, 255, 255,  60)  # White tint — wall depth highlight
DOOR_FILL        = ( 24,  95, 165, 200)  # Blue dashed — user door gap line
DOOR_ARC_FILL    = ( 24,  95, 165,  35)  # Blue transparent — door swing arc
DOOR_ARC_OUTLINE = ( 24,  95, 165, 120)  # Blue — door swing arc border
OBSTACLE_FILL    = (186, 117,  23,  45)  # Amber — obstacle area fill
OBSTACLE_OUTLINE = (186, 117,  23, 190)  # Amber — obstacle border and hatch

# ── Rendering constants ───────────────────────────────────────────────────────
DOCK_HALF       = 6    # px half-side of dock square
ROBOT_RADIUS    = 8    # px robot circle radius
ARROW_LENGTH    = 16   # px direction arrow length
STUCK_RADIUS    = 7    # px stuck triangle circumradius
PATH_WIDTH      = 3    # px path line width

# ── Geometry layer constants ───────────────────────────────────────────────────
WALL_WIDTH         = 6    # px user wall stroke width
WALL_CENTRE_WIDTH  = 2    # px wall centre highlight width
DOOR_MARKER_RADIUS = 5    # px inferred door crossing marker radius
OBSTACLE_HATCH_GAP = 8    # px obstacle hatching line spacing
SUGGEST_DASH       = (6, 4)  # px on/off for dashed suggestion outlines

# ── Storage version — bump when dump_state() format changes ──────────────────
_STATE_VERSION  = 1


@dataclass
class RendererConfig:
    """User-configurable rendering parameters from Options Flow."""

    size_px: int   = 600     # Canvas size (square)
    scale: float   = 10.0    # mm per pixel (600px @ 10mm/px → 6m × 6m)
    persist: bool  = True    # Keep last frame between missions
    auto_fit: bool = True    # Scale and centre map to fill canvas
    fit_margin: int = 40     # Pixel margin on each side when auto-fitting


class MapRenderer:
    """Converts accumulated pose points into a PNG byte string.

    Thread-safety: all methods are called from the HA event loop via
    schedule_update_ha_state(). PIL operations are synchronous and fast
    enough (<5 ms for a typical mission) that executor offload is not needed.

    Usage:
        renderer = MapRenderer(RendererConfig())
        renderer.add_pose(x_mm, y_mm, theta_deg)
        renderer.mark_stuck()          # call when bbrun.nStuck increments
        png_bytes = renderer.render()

    Persistence (called by image.py):
        state = renderer.dump_state()          # after mission end
        renderer.restore_state(state)          # after HA restart
    """

    def __init__(
        self,
        config: RendererConfig,
        geometry_store: GeometryStore | None = None,
        zone_store: ZoneStore | None = None,
    ) -> None:
        self._cfg = config
        self._geometry_store = geometry_store
        self._zone_store = zone_store
        self._points: list[tuple[int, int]] = []      # pixel coordinates
        self._stuck_px: list[tuple[int, int]] = []    # pixel coordinates
        self._robot_px: tuple[int, int] | None = None # current robot position
        self._theta: float = 0.0                      # current heading (degrees)
        self._last_png: bytes | None = None           # cached output (not persisted)
        # Auto-fit state — recomputed at the start of each render().
        self._fit_scale: float = self._cfg.scale
        self._fit_cx: int = self._cfg.size_px // 2
        self._fit_cy: int = self._cfg.size_px // 2

    # ── Public API ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all points for a new mission."""
        self._points.clear()
        self._stuck_px.clear()
        self._robot_px = None
        self._theta = 0.0
        if not self._cfg.persist:
            self._last_png = None
        _LOGGER.debug("MapRenderer: mission reset")

    # Maximum plausible displacement between two pose updates in mm.
    # Roomba top speed is ~300 mm/s; pose updates arrive every 1-5 s.
    # 500 mm covers normal movement. Anything larger is a bogus jump
    # (stuck-recovery relocalisation, manual lift, firmware artifact)
    # and would draw a line across the entire map if not rejected.
    # Derived from NickWaterton/Roomba980-Python max_distance = 500.
    _MAX_POSE_JUMP_MM: float = 500.0

    def add_pose(self, x_mm: float, y_mm: float, theta_deg: float) -> None:
        """Record a new pose point from the MQTT state update.

        roombapy convention: co_ords["x"] = pose_point_y (swapped axes).
        Callers pass already-corrected x_mm / y_mm relative to dock origin.
        Ignores (0, 0, any_theta) at mission start to avoid bogus dock point.
        Rejects jumps > _MAX_POSE_JUMP_MM from the previous point — these
        indicate stuck-recovery relocalisation or firmware bogus updates and
        would otherwise draw a line across the entire map.
        """
        if x_mm == 0.0 and y_mm == 0.0 and not self._points:
            return  # First point at dock — skip

        # Reject implausibly large jumps from the previous pose point.
        if self._points:
            prev_px, prev_py = self._points[-1]
            prev_x_mm = (prev_px - self._fit_cx) * self._fit_scale
            prev_y_mm = (self._fit_cy - prev_py) * self._fit_scale
            jump_mm = math.hypot(x_mm - prev_x_mm, y_mm - prev_y_mm)
            if jump_mm > self._MAX_POSE_JUMP_MM:
                _LOGGER.debug(
                    "MapRenderer: rejecting bogus pose jump %.0f mm "
                    "(%.0f, %.0f) -> (%.0f, %.0f)",
                    jump_mm, prev_x_mm, prev_y_mm, x_mm, y_mm,
                )
                return

        px, py = self._mm_to_px(x_mm, y_mm)
        self._points.append((px, py))
        self._robot_px = (px, py)
        self._theta = theta_deg

    def mark_stuck(self) -> None:
        """Record a stuck event at the current robot position."""
        if self._robot_px:
            self._stuck_px.append(self._robot_px)

    # Minimum content span in mm. Prevents extreme zoom when only a few
    # points are present (e.g. mission just started).
    _MIN_FIT_CONTENT_MM: float = 500.0

    def _compute_fit(self) -> tuple[float, float, float, float, int, int]:
        """Compute pixel-space transform for auto-fit rendering.

        Returns (fit_ratio, tx, ty, new_scale, fit_cx, fit_cy) where:
          fit_ratio        — multiply stored pixel coords by this to scale content
          tx, ty           — translate after scaling so content is centred
          new_scale        — mm-per-pixel scale for geometry layers (_mm_to_px)
          fit_cx, fit_cy   — canvas pixel position of the dock (0,0 mm)

        No instance state is mutated — callers apply all returned values.
        Falls back to identity transform when no points or auto_fit is off.
        """
        size = self._cfg.size_px
        orig_cx = orig_cy = size // 2
        identity = (1.0, 0.0, 0.0, self._cfg.scale, orig_cx, orig_cy)
        if not self._cfg.auto_fit or not self._points:
            return identity

        # Build bounding box from all content: path, stuck, robot.
        all_px = list(self._points)
        all_px.extend(self._stuck_px)
        if self._robot_px:
            all_px.append(self._robot_px)

        xs = [p[0] for p in all_px]
        ys = [p[1] for p in all_px]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # Enforce a minimum content span in pixel space to prevent extreme
        # zoom when only a few points exist (mission just started).
        min_content_px = self._MIN_FIT_CONTENT_MM / self._cfg.scale
        if (max_x - min_x) < min_content_px and (max_y - min_y) < min_content_px:
            return identity

        content_w = max(max_x - min_x, 1)
        content_h = max(max_y - min_y, 1)

        available = size - 2 * self._cfg.fit_margin
        if available < 10:
            return identity

        # Uniform scale so the larger axis fills the available area.
        fit_ratio = available / max(content_w, content_h)

        # Translation to centre the scaled content on the canvas.
        scaled_cx = (min_x + max_x) / 2 * fit_ratio
        scaled_cy = (min_y + max_y) / 2 * fit_ratio
        tx = size / 2 - scaled_cx
        ty = size / 2 - scaled_cy

        # Derive mm/px scale for geometry layers.
        new_scale = self._cfg.scale / fit_ratio

        # Dock position: (0,0 mm) was at orig_cx/cy in original pixel space.
        fit_cx = int(orig_cx * fit_ratio + tx)
        fit_cy = int(orig_cy * fit_ratio + ty)

        return fit_ratio, tx, ty, new_scale, fit_cx, fit_cy

    def render(self) -> bytes:
        """Render all layers to PNG and return bytes.

        Returns the last cached frame if no new points have been added
        and a cached frame exists (avoids redundant re-renders).
        """
        if not self._points and self._last_png:
            return self._last_png

        # Compute auto-fit transform once per render.
        # All fit state is set atomically here — _compute_fit has no side effects.
        fit_ratio, tx, ty, new_scale, fit_cx, fit_cy = self._compute_fit()
        self._fit_scale = new_scale
        self._fit_cx = fit_cx
        self._fit_cy = fit_cy

        def _fit_px(px: int, py: int) -> tuple[int, int]:
            """Transform a stored pixel coordinate to the fit canvas space."""
            return (int(px * fit_ratio + tx), int(py * fit_ratio + ty))


        size = self._cfg.size_px
        img = Image.new("RGBA", (size, size), BG_COLOUR)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size - 1, size - 1], outline=FLOOR_BORDER, width=2)

        # Layer 1.5 — inference suggestions (zone outlines + door markers).
        # Suppressed once user geometry exists, so the two never overlap.
        self._draw_inference_suggestions(draw)

        # Layer 1.6 — user-authored geometry (walls, doors, obstacles).
        self._draw_user_geometry(draw)

        if self._points:
            self._draw_cleaned_area(draw, _fit_px)
            self._draw_path(draw, _fit_px)

        for sx, sy in self._stuck_px:
            fsx, fsy = _fit_px(sx, sy)
            self._draw_triangle(draw, fsx, fsy, STUCK_RADIUS, STUCK_COLOUR)

        dock_px = _fit_px(size // 2, size // 2)
        dcx, dcy = dock_px
        draw.ellipse(
            [dcx - DOCK_HALF, dcy - DOCK_HALF, dcx + DOCK_HALF, dcy + DOCK_HALF],
            fill=DOCK_COLOUR,
            outline=(30, 120, 30, 255),
            width=2,
        )

        if self._robot_px:
            rx, ry = _fit_px(*self._robot_px)
            draw.ellipse(
                [rx - ROBOT_RADIUS, ry - ROBOT_RADIUS,
                 rx + ROBOT_RADIUS, ry + ROBOT_RADIUS],
                fill=ROBOT_COLOUR,
            )
            angle_rad = math.radians(self._theta)
            ex = rx + int(ARROW_LENGTH * math.cos(angle_rad))
            ey = ry - int(ARROW_LENGTH * math.sin(angle_rad))
            draw.line([rx, ry, ex, ey], fill=ARROW_COLOUR, width=3)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self._last_png = buf.getvalue()
        return self._last_png

    # ── Persistence ───────────────────────────────────────────────────────────

    def dump_state(self) -> dict[str, Any]:
        """Serialise renderer state to a JSON-safe dict for hass.storage.

        Only the pose points, stuck positions, last heading, and robot position
        are persisted. The cached PNG is intentionally excluded — it will be
        re-rendered from the persisted points on the first async_image() call
        after restore, which takes <5 ms and avoids storing large binary blobs
        in hass.storage.

        The _STATE_VERSION field allows future migrations if the format changes.
        """
        return {
            "version": _STATE_VERSION,
            "points": list(self._points),           # list[tuple[int, int]]
            "stuck_px": list(self._stuck_px),       # list[tuple[int, int]]
            "robot_px": list(self._robot_px) if self._robot_px else None,
            "theta": self._theta,
        }

    def restore_state(self, state: dict[str, Any]) -> bool:
        """Restore renderer state from a previously dumped dict.

        Returns True if restore succeeded, False if the state is incompatible
        (e.g. wrong version, missing keys) so the caller can log a warning
        and continue with a blank renderer rather than crashing.

        The cached PNG is intentionally not restored — it will be re-rendered
        on the first render() call.
        """
        try:
            version = state.get("version", 0)
            if version != _STATE_VERSION:
                _LOGGER.warning(
                    "MapRenderer: stored state version %d != current %d, skipping restore",
                    version, _STATE_VERSION,
                )
                return False

            self._points = [tuple(p) for p in state["points"]]
            self._stuck_px = [tuple(p) for p in state["stuck_px"]]
            robot_px = state.get("robot_px")
            self._robot_px = tuple(robot_px) if robot_px else None
            self._theta = float(state.get("theta", 0.0))
            self._last_png = None  # will be re-rendered on demand

            _LOGGER.debug(
                "MapRenderer: restored %d points, %d stuck events",
                len(self._points), len(self._stuck_px),
            )
            return True

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("MapRenderer: restore_state failed: %s", exc)
            return False

    # ── Read-only properties ──────────────────────────────────────────────────

    @property
    def has_data(self) -> bool:
        """Return True if any pose points have been recorded."""
        return bool(self._points)

    @property
    def point_count(self) -> int:
        """Return number of recorded pose points."""
        return len(self._points)

    def render_keepout_zones(
        self,
        keepout_polygons_px: list[list[tuple[int, int]]],
    ) -> bytes | None:
        """v2.3.0 Step 6 — Overlay keep-out zone polygons as semi-transparent red fills.

        Must be called AFTER render() so _last_png is populated.
        Composites the overlay onto _last_png in-memory and updates _last_png
        so the caller can use the returned bytes directly without another render().

        Returns updated PNG bytes, or None when _last_png is absent or list empty.
        Called from image.py async_image(); caller uses the return value directly.
        """
        if not keepout_polygons_px or self._last_png is None:
            return None
        import io as _io
        from PIL import Image as PILImage, ImageDraw
        base    = PILImage.open(_io.BytesIO(self._last_png)).convert("RGBA")
        overlay = PILImage.new("RGBA", base.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)
        for poly in keepout_polygons_px:
            if len(poly) >= 3:
                draw.polygon(poly, fill=(255, 0, 0, 80))
        composite     = PILImage.alpha_composite(base, overlay).convert("RGB")
        buf           = _io.BytesIO()
        composite.save(buf, format="PNG")
        self._last_png = buf.getvalue()
        return self._last_png

    @property
    def points_mm(self) -> list[tuple[float, float]]:
        """Return pose points in mm (for ZoneStore gap segmentation)."""
        cx = cy = self._cfg.size_px // 2
        scale = self._cfg.scale
        return [
            ((px - cx) * scale, (cy - py) * scale)
            for px, py in self._points
        ]


    def diagnostic_info(self) -> dict:
        """Return a diagnostics-safe summary with no private attribute access.

        Called by diagnostics.py so it never needs to reach into _cfg, _last_png
        or _stuck_px directly.
        """
        return {
            "size_px": self._cfg.size_px,
            "scale_mm_per_px": self._cfg.scale,
            "persist": self._cfg.persist,
            "point_count": len(self._points),
            "has_cached_image": self._last_png is not None,
            "stuck_event_count": len(self._stuck_px),
        }

    # ── Geometry layers ───────────────────────────────────────────────────────

    def _draw_inference_suggestions(self, draw: ImageDraw.ImageDraw) -> None:
        """Layer 1.5 — dashed zone outlines and door crossing markers.

        Draws the zone bounding boxes from ZoneStore (expanded by wall_offset_mm)
        as dashed grey rectangles, and each DoorMarker as a small filled circle.
        Suppressed entirely when user geometry already exists — the two layers
        must never overlap to avoid confusion.
        """
        if self._zone_store is None:
            return
        if self._geometry_store is not None and self._geometry_store.has_user_geometry:
            return  # user has confirmed layout — suggestions no longer shown

        # Zone outline rectangles (dashed)
        offset = self._geometry_store.wall_offset_mm if self._geometry_store else 200
        for zone in self._zone_store.zones:
            x1, y1 = self._mm_to_px(zone.x_min - offset, zone.y_max + offset)
            x2, y2 = self._mm_to_px(zone.x_max + offset, zone.y_min - offset)
            self._draw_dashed_rect(draw, x1, y1, x2, y2, SUGGEST_OUTLINE, SUGGEST_DASH)
            # Zone name label at bbox centroid
            if zone.name:
                lx, ly = self._mm_to_px(
                    (zone.x_min + zone.x_max) / 2,
                    (zone.y_min + zone.y_max) / 2,
                )
                draw.text((lx, ly), zone.name, fill=SUGGEST_LABEL, anchor="mm")

        # Door crossing markers (filled circles)
        if self._geometry_store:
            for marker in self._geometry_store.door_markers:
                if marker.mission_count < 2:
                    continue  # only show markers seen in ≥2 missions
                mx, my = self._mm_to_px(marker.cx, marker.cy)
                r = DOOR_MARKER_RADIUS
                draw.ellipse(
                    [mx - r, my - r, mx + r, my + r],
                    fill=DOOR_MARKER,
                    outline=(255, 255, 255, 200),
                    width=1,
                )

    def _draw_user_geometry(self, draw: ImageDraw.ImageDraw) -> None:
        """Layer 1.6 — user-authored walls, doors, and obstacle rectangles.

        Walls are solid dark-grey lines with a faint white centre highlight.
        Doors are a dashed blue gap line plus a filled quarter-circle swing arc.
        Obstacles are amber-filled rectangles with dashed border and hatching.
        All elements render beneath the cleaned-area layer so the cleaning path
        remains the dominant visual.
        """
        if self._geometry_store is None:
            return

        for wall in self._geometry_store.walls:
            x1, y1 = self._mm_to_px(wall.x1, wall.y1)
            x2, y2 = self._mm_to_px(wall.x2, wall.y2)
            draw.line([x1, y1, x2, y2], fill=WALL_FILL, width=WALL_WIDTH)
            draw.line([x1, y1, x2, y2], fill=WALL_CENTRE, width=WALL_CENTRE_WIDTH)
            if wall.label:
                mx, my = (x1 + x2) // 2, (y1 + y2) // 2
                draw.text((mx, my - 8), wall.label, fill=WALL_FILL, anchor="mm")

        for door in self._geometry_store.doors:
            cx, cy = self._mm_to_px(door.cx, door.cy)
            half_w_px = max(1, int(door.width_mm / 2 / self._cfg.scale))
            theta_rad = math.radians(door.theta_deg)
            # Gap line (dashed) along door orientation
            dx = int(half_w_px * math.cos(theta_rad))
            dy = int(half_w_px * math.sin(theta_rad))
            self._draw_dashed_line(
                draw, cx - dx, cy + dy, cx + dx, cy - dy,
                DOOR_FILL, (6, 4), width=3,
            )
            # Swing arc — quarter circle from gap end, radius = door width
            self._draw_door_arc(draw, cx - dx, cy + dy, half_w_px * 2, door.theta_deg)
            if door.label:
                draw.text((cx, cy - 10), door.label, fill=DOOR_FILL, anchor="mm")

        for obs in self._geometry_store.obstacles:
            x1, y1 = self._mm_to_px(obs.x, obs.y + obs.h)   # top-left in image
            x2, y2 = self._mm_to_px(obs.x + obs.w, obs.y)   # bottom-right in image
            # Clamp to canvas
            size = self._cfg.size_px
            x1, x2 = sorted([max(0, min(size - 1, x1)), max(0, min(size - 1, x2))])
            y1, y2 = sorted([max(0, min(size - 1, y1)), max(0, min(size - 1, y2))])
            if x2 <= x1 or y2 <= y1:
                continue
            draw.rectangle([x1, y1, x2, y2], fill=OBSTACLE_FILL)
            self._draw_dashed_rect(draw, x1, y1, x2, y2, OBSTACLE_OUTLINE, (5, 3))
            self._draw_hatch(draw, x1, y1, x2, y2, OBSTACLE_OUTLINE)
            if obs.label:
                lx, ly = (x1 + x2) // 2, (y1 + y2) // 2
                draw.text((lx, ly), obs.label, fill=OBSTACLE_OUTLINE, anchor="mm")

    # ── Geometry drawing primitives ───────────────────────────────────────────

    @staticmethod
    def _draw_dashed_line(
        draw: ImageDraw.ImageDraw,
        x1: int, y1: int, x2: int, y2: int,
        colour: tuple,
        dash: tuple[int, int] = (6, 4),
        width: int = 1,
    ) -> None:
        """Draw a dashed line from (x1,y1) to (x2,y2)."""
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1:
            return
        on, off = dash
        step = on + off
        steps = max(1, int(length / step))
        for i in range(steps + 1):
            t_start = i * step / length
            t_end = min(1.0, (i * step + on) / length)
            if t_start >= 1.0:
                break
            sx = int(x1 + (x2 - x1) * t_start)
            sy = int(y1 + (y2 - y1) * t_start)
            ex = int(x1 + (x2 - x1) * t_end)
            ey = int(y1 + (y2 - y1) * t_end)
            draw.line([sx, sy, ex, ey], fill=colour, width=width)

    @classmethod
    def _draw_dashed_rect(
        cls,
        draw: ImageDraw.ImageDraw,
        x1: int, y1: int, x2: int, y2: int,
        colour: tuple,
        dash: tuple[int, int] = (6, 4),
    ) -> None:
        """Draw a dashed rectangle by drawing four dashed sides."""
        cls._draw_dashed_line(draw, x1, y1, x2, y1, colour, dash)  # top
        cls._draw_dashed_line(draw, x2, y1, x2, y2, colour, dash)  # right
        cls._draw_dashed_line(draw, x2, y2, x1, y2, colour, dash)  # bottom
        cls._draw_dashed_line(draw, x1, y2, x1, y1, colour, dash)  # left

    def _draw_door_arc(
        self,
        draw: ImageDraw.ImageDraw,
        hinge_px: int, hinge_py: int,
        radius_px: int,
        theta_deg: float,
    ) -> None:
        """Draw a quarter-circle door swing arc.

        The arc starts at theta_deg and sweeps 90° counter-clockwise.
        Filled with DOOR_ARC_FILL and outlined with DOOR_ARC_OUTLINE.
        Drawn as a polygon of short line segments for broad PIL compatibility.
        """
        steps = max(8, radius_px // 2)
        start_rad = math.radians(theta_deg)
        end_rad = start_rad + math.pi / 2
        pts = [(hinge_px, hinge_py)]
        for i in range(steps + 1):
            a = start_rad + (end_rad - start_rad) * i / steps
            pts.append((
                hinge_px + int(radius_px * math.cos(a)),
                hinge_py - int(radius_px * math.sin(a)),
            ))
        pts.append((hinge_px, hinge_py))
        if len(pts) >= 3:
            draw.polygon(pts, fill=DOOR_ARC_FILL, outline=DOOR_ARC_OUTLINE)

    @staticmethod
    def _draw_hatch(
        draw: ImageDraw.ImageDraw,
        x1: int, y1: int, x2: int, y2: int,
        colour: tuple,
    ) -> None:
        """Draw diagonal hatching lines inside a rectangle."""
        w, h = x2 - x1, y2 - y1
        span = w + h
        step = OBSTACLE_HATCH_GAP
        for offset in range(0, span, step):
            # Diagonal from top-left area to bottom-right area
            sx = x1 + offset
            sy = y1
            ex = x1
            ey = y1 + offset
            # Clamp to rectangle
            if sx > x2:
                ey += sx - x2
                sx = x2
            if ey > y2:
                sx -= ey - y2
                ey = y2
            if sx >= x1 and ey <= y2 and sx <= x2 and ey >= y1:
                draw.line([sx, sy, ex, ey], fill=colour, width=1)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _mm_to_px(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """Convert mm dock-relative coordinates to canvas pixel coordinates.

        Uses _fit_scale and _fit_cx/cy which are set by _compute_fit() at
        the start of each render() call. Outside of render() (e.g. in
        add_pose()) the default cfg scale and centre are used.
        """
        return (
            int(self._fit_cx + x_mm / self._fit_scale),
            int(self._fit_cy - y_mm / self._fit_scale),
        )

    def _draw_cleaned_area(
        self, draw: ImageDraw.ImageDraw,
        fit_px: Callable[[int, int], tuple[int, int]],
    ) -> None:
        """Draw a filled circle per pose point to approximate cleaned area."""
        # Radius in original pixel space (150mm / cfg.scale), then scaled
        # by fit_ratio so it matches the zoomed content size.
        # This keeps the cleaned area proportional at all zoom levels.
        base_r_px = max(4, int(150 / self._cfg.scale))
        r = max(4, int(base_r_px * (self._cfg.scale / self._fit_scale)))
        fitted = [fit_px(px, py) for px, py in self._points]
        for px, py in self._interpolated(fitted, max_gap_px=r):
            draw.ellipse([px - r, py - r, px + r, py + r], fill=CLEANED_COLOUR)

    def _draw_path(
        self, draw: ImageDraw.ImageDraw,
        fit_px: Callable[[int, int], tuple[int, int]],
    ) -> None:
        """Draw the travel path polyline, clipped to canvas bounds."""
        if len(self._points) < 2:
            return
        size = self._cfg.size_px
        clipped = [
            (max(0, min(size - 1, px)), max(0, min(size - 1, py)))
            for px, py in (fit_px(px, py) for px, py in self._points)
        ]
        draw.line(clipped, fill=PATH_COLOUR, width=PATH_WIDTH)

    @staticmethod
    def _interpolated(
        points: list[tuple[int, int]], max_gap_px: int
    ) -> list[tuple[int, int]]:
        """Insert intermediate points where gaps exceed max_gap_px."""
        if not points:
            return []
        result: list[tuple[int, int]] = [points[0]]
        for i in range(1, len(points)):
            p1, p2 = points[i - 1], points[i]
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if dist > max_gap_px:
                steps = int(dist / max_gap_px)
                for s in range(1, steps):
                    result.append((
                        int(p1[0] + (p2[0] - p1[0]) * s / steps),
                        int(p1[1] + (p2[1] - p1[1]) * s / steps),
                    ))
            result.append(p2)
        return result

    @staticmethod
    def _draw_triangle(
        draw: ImageDraw.ImageDraw,
        cx: int, cy: int,
        r: int,
        colour: tuple[int, int, int, int],
    ) -> None:
        """Draw a downward-pointing equilateral triangle centred at (cx, cy)."""
        pts = [
            (cx, cy + r),
            (cx - int(r * 0.866), cy - r // 2),
            (cx + int(r * 0.866), cy - r // 2),
        ]
        draw.polygon(pts, fill=colour)
