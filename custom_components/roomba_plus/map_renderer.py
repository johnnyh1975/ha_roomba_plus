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
import time as _time_mod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from .geometry_store import GeometryStore
    from .room_seg_store import RoomSegStore

_LOGGER = logging.getLogger(__name__)

# ── MAP-FONT (v2.9.0) — embedded TTF instead of PIL's tiny bitmap default ────
# DejaVu Sans, Bitstream Vera License (see fonts/LICENSE.txt) — freely
# redistributable, no attribution requirement beyond keeping the license file.
_FONT_PATH = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the bundled DejaVu Sans font at the given pixel size.

    Falls back to PIL's bitmap default if the font file is somehow missing
    (e.g. a packaging error) so rendering never hard-crashes over a label font.
    """
    try:
        return ImageFont.truetype(str(_FONT_PATH), size)
    except OSError:
        _LOGGER.warning(
            "MAP-FONT: could not load bundled font at %s — falling back to "
            "PIL default bitmap font", _FONT_PATH,
        )
        return ImageFont.load_default()


# Pre-loaded at module import so each render call doesn't re-read the font
# file from disk. 12px for compact labels (door/obstacle), 13px default.
LABEL_FONT      = _load_font(13)
LABEL_FONT_SMALL = _load_font(12)

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

# ── Robot footprint (real chassis diameter, mm) ───────────────────────────────
# pose.point.x/y is the robot's own reported navigation centre — the geometric
# centre of the (round) chassis. The path line therefore traces that centre,
# not an edge. The "cleaned area" circle drawn at each pose point should
# match the real chassis radius so the rendered swept area corresponds to
# the robot's actual physical footprint, not an arbitrary cleaning-width guess.
# 600/900-series (round, incl. test robot 980): 13.9 in = 353 mm.
# i/s/j-series (round, slightly slimmer chassis): 13.34 in = 339 mm.
# Braava (m6/jet, non-round wedge shape) is not represented well by a circle
# at all — falls back to the generic default below.
ROBOT_DIAMETER_MM_900_SERIES = 353
ROBOT_DIAMETER_MM_ISJ_SERIES = 339
ROBOT_DIAMETER_MM_DEFAULT    = 340   # generic fallback when model tier unknown

# ── Rendering constants ───────────────────────────────────────────────────────
# v2.9.0 — DOCK_HALF/ROBOT_RADIUS/STUCK_RADIUS/PATH_WIDTH used to be fixed
# pixel sizes, never adjusted for the auto_fit zoom factor. Only the cleaned-
# area circles (_draw_cleaned_area) scaled with zoom — causing the path,
# dock marker, robot icon, and stuck triangle to visually drift relative to
# the cleaned-area blob at different zoom levels (confirmed from a real
# field-data screenshot: at one zoom level the path was ~17x thinner than
# the cleaned-area circle, and the dock marker nearly invisible against it).
#
# First attempt at the fix made dock/robot icons literally robot_diameter_mm
# wide (same as the cleaned-area footprint circle) — confirmed WRONG against
# real field data (2026-06-19): on a tightly-confined mission (~0.7m x 0.75m
# span, robot stuck/oscillating), a literal-diameter dock/robot circle
# swallowed most of the canvas even though the cleaned-area shape itself
# correctly traced the real room layout at that scale. Dock and robot-
# position are map MARKERS — like a pin on a map — not footprint paint;
# only the cleaned-area circles (actual swept coverage) should be literally
# to-scale. Markers now use a deliberately smaller, fixed real-world
# reference size, decoupled from cfg.robot_diameter_mm, but still
# zoom-scaled via _mm_radius_px() so they stay visible (not 6px-fixed)
# without dominating the canvas.
ARROW_LENGTH            = 16   # px direction arrow length — kept fixed (small UI element)
ROBOT_ICON_DIAMETER_MM  = 160  # current-position marker — a recognisable dot, not a footprint
DOCK_ICON_DIAMETER_MM   = 160  # dock marker — same marker convention as the robot icon
STUCK_DIAMETER_MM       = 140  # stuck-event marker — slightly smaller, visually distinct
PATH_WIDTH_MM           = 60   # real-world width of the travel-path line
DOCK_MIN_PX             = 3
ROBOT_MIN_PX            = 3
STUCK_MIN_PX            = 3
PATH_MIN_PX             = 2

# ── Geometry layer constants ───────────────────────────────────────────────────
WALL_WIDTH         = 6    # px user wall stroke width
WALL_CENTRE_WIDTH  = 2    # px wall centre highlight width
DOOR_MARKER_RADIUS = 5    # px inferred door crossing marker radius
OBSTACLE_HATCH_GAP = 8    # px obstacle hatching line spacing
SUGGEST_DASH       = (6, 4)  # px on/off for dashed suggestion outlines

# ── Storage version — bump when dump_state() format changes ──────────────────
_STATE_VERSION  = 1

# v3.2.1 LANDMARK-LOG — a sustained pose jump accepted after
# _MAX_CONSECUTIVE_REJECTED_JUMPS rejections corresponds, per the 980's
# documented vSLAM sensor-fusion pipeline, to either a genuine move or a
# camera-landmark relocalisation correction. Logging WHERE these land
# (not just counting them, as before) is data-collection scaffolding for
# a future "landmark cluster" structural signal — never consumed
# anywhere yet. Capped, not unbounded: this is meant to reveal spatial
# clustering across many missions, not to be a full history.
MAX_ACCEPTED_JUMP_LOG = 500


@dataclass
class RendererConfig:
    """User-configurable rendering parameters from Options Flow."""

    size_px: int   = 600     # Canvas size (square)
    scale: float   = 10.0    # mm per pixel (600px @ 10mm/px → 6m × 6m)
    persist: bool  = True    # Keep last frame between missions
    auto_fit: bool = True    # Scale and centre map to fill canvas
    fit_margin: int = 40     # Pixel margin on each side when auto-fitting
    robot_diameter_mm: int = ROBOT_DIAMETER_MM_DEFAULT  # Real chassis diameter


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
        room_seg_store: "RoomSegStore | None" = None,
    ) -> None:
        self._cfg = config
        self._geometry_store = geometry_store
        self._room_seg_store = room_seg_store
        self._points: list[tuple[int, int]] = []      # pixel coordinates
        self._stuck_px: list[tuple[int, int]] = []    # pixel coordinates
        self._robot_px: tuple[int, int] | None = None # current robot position
        self._theta: float = 0.0                      # current heading (degrees)
        self._last_png: bytes | None = None           # cached output (not persisted)
        # Auto-fit state — recomputed at the start of each render().
        self._fit_scale: float = self._cfg.scale
        self._fit_cx: int = self._cfg.size_px // 2
        self._fit_cy: int = self._cfg.size_px // 2
        # v2.9.0 — see _MAX_POSE_JUMP_MM rationale below. Counts consecutive
        # rejected jumps so a real, sustained move isn't permanently stranded
        # behind one stale anchor point.
        self._consecutive_rejected_jumps: int = 0
        # v3.2.1 LANDMARK-LOG — deliberately NOT cleared in reset(): the
        # whole point is cross-mission accumulation, unlike self._points
        # which is intentionally wiped every mission (see reset()).
        # Widened to include theta_deg: a position jump WITH an
        # unexplained heading jump is much stronger evidence of a genuine
        # pickup/relocalisation event than position alone (see the
        # Dock-Anchor-Korrektur design doc) — feeds both the future
        # landmark-cluster idea and a potential stuck-independent
        # detector for exactly this scenario.
        self._accepted_jump_log: list[tuple[float, float, float, float]] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all points for a new mission."""
        self._points.clear()
        self._stuck_px.clear()
        self._robot_px = None
        self._theta = 0.0
        self._consecutive_rejected_jumps = 0
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

    # v2.9.0 — CASCADING REJECTION BUG (confirmed from real field data,
    # 2026-06-19): rejecting a jump returns without appending the new point,
    # so self._points[-1] stays the same OLD anchor for the *next* call too.
    # If the robot genuinely moved away (long hallway traverse, relocalising
    # after a charge/stuck recovery, a sparse MQTT update gap), every
    # subsequent real position also reads as "too far from that stale
    # anchor" — rejection cascades and never recovers on its own. A real
    # 980 OG checkpoint mid-mission showed exactly this: 1413 points all
    # stranded within a ~0.7m x 0.7m pocket despite a 106 m² home, because
    # one early rejected jump froze the anchor and every later legitimate
    # pose got compared against it forever.
    #
    # Fix: track consecutive rejections. A single rejected jump still gets
    # filtered (catches the momentary firmware glitches this guard was
    # built for). But once jumps keep being "too large" several times in a
    # row, that's not a glitch — it is the robot, accept the move and
    # re-anchor. This bounds the worst case to a short gap in the path
    # instead of an indefinite, often mission-long, rejection cascade.
    _MAX_CONSECUTIVE_REJECTED_JUMPS: int = 2

    def add_pose(self, x_mm: float, y_mm: float, theta_deg: float) -> bool:
        """Record a new pose point from the MQTT state update.

        roombapy convention: co_ords["x"] = pose_point_y (swapped axes).
        Callers pass already-corrected x_mm / y_mm relative to dock origin
        — true since v3.2.1's axis-swap fix in image.py::_handle_pose()
        (this docstring line predates that fix; the swap was documented
        here as an assumption long before it was actually implemented at
        the call site — confirmed via roombapy's own source, see
        _handle_pose()'s comment for the full rationale).
        Ignores (0, 0, any_theta) at mission start to avoid bogus dock point.
        Rejects jumps > _MAX_POSE_JUMP_MM from the previous point — these
        indicate stuck-recovery relocalisation or firmware bogus updates and
        would otherwise draw a line across the entire map. See
        _MAX_CONSECUTIVE_REJECTED_JUMPS above: this rejection self-heals
        after a short streak instead of cascading indefinitely.

        Returns True if THIS call was an accepted sustained jump (added to
        accepted_jump_log) — v3.2.1 DOCK-ANCHOR: callers use this to mark
        interpolation waypoints within a buffered post-stuck segment (see
        Dock_Anchor_Korrektur_Plan.md, 4c). False for a normal point, a
        rejected jump, or the skipped dock-start point.
        """
        if x_mm == 0.0 and y_mm == 0.0 and not self._points:
            return False  # First point at dock — skip

        # Reject implausibly large jumps from the previous pose point.
        # v2.6.3 B1 — use cfg constants (not _fit_scale/_fit_cx/cy) for the
        # inverse transform.  _fit_* values change after every render() call;
        # using them here caused the inverse to produce wildly wrong mm values
        # after auto-fit activated, rejecting up to 99% of legitimate poses.
        was_accepted_jump = False
        if self._points:
            prev_px, prev_py = self._points[-1]
            orig_cx = orig_cy = self._cfg.size_px // 2
            prev_x_mm = (prev_px - orig_cx) * self._cfg.scale
            prev_y_mm = (orig_cy - prev_py) * self._cfg.scale
            jump_mm = math.hypot(x_mm - prev_x_mm, y_mm - prev_y_mm)
            if (
                jump_mm > self._MAX_POSE_JUMP_MM
                and self._consecutive_rejected_jumps < self._MAX_CONSECUTIVE_REJECTED_JUMPS
            ):
                self._consecutive_rejected_jumps += 1
                _LOGGER.debug(
                    "MapRenderer: rejecting bogus pose jump %.0f mm "
                    "(%.0f, %.0f) -> (%.0f, %.0f) [streak=%d]",
                    jump_mm, prev_x_mm, prev_y_mm, x_mm, y_mm,
                    self._consecutive_rejected_jumps,
                )
                return False
            if jump_mm > self._MAX_POSE_JUMP_MM:
                _LOGGER.info(
                    "MapRenderer: accepting sustained pose jump %.0f mm "
                    "after %d consecutive rejections — treating as a real "
                    "move/relocalisation, not a glitch",
                    jump_mm, self._consecutive_rejected_jumps,
                )
                self._accepted_jump_log.append((x_mm, y_mm, theta_deg, _time_mod.time()))
                if len(self._accepted_jump_log) > MAX_ACCEPTED_JUMP_LOG:
                    self._accepted_jump_log = self._accepted_jump_log[-MAX_ACCEPTED_JUMP_LOG:]
                was_accepted_jump = True

        self._consecutive_rejected_jumps = 0
        px, py = self._mm_to_px(x_mm, y_mm)
        self._points.append((px, py))
        self._robot_px = (px, py)
        self._theta = theta_deg
        return was_accepted_jump

    def replace_range(
        self, start_index: int, corrected_points_mm: list[tuple[float, float]],
    ) -> None:
        """v3.2.1 DOCK-ANCHOR — retroactively replace an already-rendered
        range of points with corrected (x_mm, y_mm) values, then force a
        re-render.

        Needed because self._renderer.add_pose() is called per pose
        message in real time (unlike GridStore/RoomSeg/Outline, which are
        only fed once at mission end) — a buffered, potentially-distorted
        segment is already drawn and cached by the time a dock-anchor
        correction is computed. This does NOT clear or reset anything
        outside [start_index:] — points before the buffered segment stay
        untouched, exactly as they were.

        start_index: index into self._points where the correction begins
        (the first point of the buffered segment). Silently clamped/no-op
        if out of range (e.g. a reset() happened in between) rather than
        raising — a stale correction arriving late should not crash the
        live map.
        """
        if start_index < 0 or start_index > len(self._points):
            _LOGGER.debug(
                "MapRenderer.replace_range: start_index %d out of range "
                "for %d points — ignoring (renderer state changed since "
                "the correction was computed)",
                start_index, len(self._points),
            )
            return
        new_px_points = [self._mm_to_px(x, y) for x, y in corrected_points_mm]
        self._points = self._points[:start_index] + new_px_points
        if new_px_points:
            self._robot_px = new_px_points[-1]
        self._last_png = None  # force re-render; the cache reflects stale data

    def mark_stuck(self) -> None:
        """Record a stuck event at the current robot position."""
        if self._robot_px:
            self._stuck_px.append(self._robot_px)

    # Minimum content span in mm. Prevents extreme zoom when only a few
    # points are present (e.g. mission just started).
    _MIN_FIT_CONTENT_MM: float = 500.0

    # v2.9.0 — the robot footprint circle (cfg.robot_diameter_mm) must never
    # dominate the canvas just because the pose path happens to stay within
    # a small area (e.g. robot stuck/spinning in one spot for a long time —
    # confirmed from real field data: 1413 pose points spanning only ~0.7m,
    # last_stuck_count=165). _MIN_FIT_CONTENT_MM alone doesn't catch this,
    # since it's a flat mm value unrelated to the robot's own size. The
    # effective minimum content span is now whichever is larger: the flat
    # floor above, or robot_diameter_mm × this multiplier — so the robot's
    # own diameter is at most 1 / _MIN_FIT_CONTENT_ROBOT_MULTIPLIER of the
    # visible canvas width, regardless of how small the real path is.
    _MIN_FIT_CONTENT_ROBOT_MULTIPLIER: float = 4.0

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

        # v2.9.0 — clamp the content span used for the fit calculation to at
        # least this effective minimum, rather than the old binary "below
        # 500mm → no zoom at all, above → zoom to exactly fit" cliff. The
        # clamp keeps the robot footprint circle proportionally reasonable
        # at every zoom level, including the small-area/stuck-robot case.
        min_content_mm = max(
            self._MIN_FIT_CONTENT_MM,
            self._cfg.robot_diameter_mm * self._MIN_FIT_CONTENT_ROBOT_MULTIPLIER,
        )
        min_content_px = min_content_mm / self._cfg.scale

        content_w = max(max_x - min_x, min_content_px)
        content_h = max(max_y - min_y, min_content_px)

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
            stuck_r = self._mm_radius_px(STUCK_DIAMETER_MM / 2, min_px=STUCK_MIN_PX)
            self._draw_triangle(draw, fsx, fsy, stuck_r, STUCK_COLOUR)

        # v2.9.0 — robot-footprint reference circle around the dock, in
        # addition to the small marker dot. Filled, semi-transparent green
        # (not solid) — clearly reads as "the dock zone" while still letting
        # the cleaned-area/path detail underneath show through. This is a
        # scale reference ("this much space if the robot were sitting right
        # here"), not a coverage indicator — kept visually distinct from the
        # solid CLEANED_COLOUR circles.
        #
        # PIL's ImageDraw overwrites destination pixels (alpha channel
        # included) rather than alpha-compositing onto existing content —
        # drawing a semi-transparent fill directly with `draw.ellipse(...)`
        # would erase whatever was underneath instead of blending with it.
        # Drawing onto a separate fully-transparent overlay and compositing
        # with Image.alpha_composite() is the correct way to get a real
        # blend. img is reassigned, so `draw` must be rebound afterwards —
        # every draw call below this point uses the new `draw`.
        dock_footprint_r = self._mm_radius_px(
            self._cfg.robot_diameter_mm / 2, min_px=DOCK_MIN_PX
        )
        dock_px = _fit_px(size // 2, size // 2)
        dcx, dcy = dock_px
        dock_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(dock_overlay)
        overlay_draw.ellipse(
            [dcx - dock_footprint_r, dcy - dock_footprint_r,
             dcx + dock_footprint_r, dcy + dock_footprint_r],
            fill=(80, 180, 80, 90),
            outline=(30, 120, 30, 200),
            width=2,
        )
        img = Image.alpha_composite(img, dock_overlay)
        draw = ImageDraw.Draw(img)

        dock_r = self._mm_radius_px(DOCK_ICON_DIAMETER_MM / 2, min_px=DOCK_MIN_PX)
        draw.ellipse(
            [dcx - dock_r, dcy - dock_r, dcx + dock_r, dcy + dock_r],
            fill=DOCK_COLOUR,
            outline=(30, 120, 30, 255),
            width=2,
        )

        if self._robot_px:
            # v2.9.0 — robot icon uses the smaller marker-convention size
            # (ROBOT_ICON_DIAMETER_MM), not the literal chassis diameter.
            # It marks "robot is here" like a pin, distinct from the
            # to-scale cleaned-area footprint circles along its trail.
            robot_r = self._mm_radius_px(ROBOT_ICON_DIAMETER_MM / 2, min_px=ROBOT_MIN_PX)
            rx, ry = _fit_px(*self._robot_px)
            draw.ellipse(
                [rx - robot_r, ry - robot_r,
                 rx + robot_r, ry + robot_r],
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
            # v3.2.1 LANDMARK-LOG — additive field, no _STATE_VERSION
            # bump needed (same precedent as GridStore's FURNITURE/
            # DUAL-GRID fields): a state dump saved before this existed
            # simply has no "accepted_jump_log" key.
            "accepted_jump_log": [list(j) for j in self._accepted_jump_log],
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
            # v3.2.1 LANDMARK-LOG — .get() with [] default: old dumps
            # simply predate this field, not an error. Also handles the
            # OLDER 3-element shape (x,y,timestamp), from before theta_deg
            # was added: those entries get a placeholder theta=0.0 rather
            # than being dropped or raising.
            self._accepted_jump_log = [
                (float(j[0]), float(j[1]), float(j[2]), float(j[3]))
                if len(j) >= 4 else
                (float(j[0]), float(j[1]), 0.0, float(j[2]))
                for j in state.get("accepted_jump_log", [])
            ]

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
    def accepted_jump_log(self) -> list[tuple[float, float, float, float]]:
        """Read-only snapshot of (x_mm, y_mm, theta_deg, unix_time) for
        every sustained pose jump accepted after the reject-streak (see
        MAX_ACCEPTED_JUMP_LOG docstring). Data-collection scaffolding;
        not yet consumed anywhere.
        """
        return list(self._accepted_jump_log)

    # v3.2.1 REMOVED — render_for_outline() and the whole fixed-window
    # PNG-extraction pipeline it fed (extract_contour_from_png,
    # _merge_contours in outline_store.py) are gone. The room outline is
    # now derived directly from GridStore's unbounded cell dict
    # (outline_store.compute_boundary_points_mm) instead of a 6x6m PNG
    # render — see outline_store.py PAYLOAD_VERSION 4 for the full
    # rationale (field-confirmed: 57% of a real OG's grid fell outside
    # the fixed window and could never appear in the outline).

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
        so the caller can use the returned bytes directly without another render().</p>

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

    def render_observed_zones(
        self,
        circles_px: list[tuple[int, int, int]],
    ) -> bytes | None:
        """v3.0.0 ZONE-OVERLAY — Overlay robot-observed obstacle zones as orange circles.

        Mirrors render_keepout_zones() compositing pattern.
        Colour: orange (255, 140, 0, 100) — distinct from keepout red and outline grey.
        Shape: filled circles (draw.ellipse) — observed_zone_centroids carry x/y/radius,
        not full polygon paths like UMF keepout zones.

        Args:
            circles_px: list of (cx_px, cy_px, radius_px) tuples in pixel coordinates.

        Must be called AFTER render() so _last_png is populated.
        Returns updated PNG bytes, or None when _last_png is absent or list empty.
        """
        if not circles_px or self._last_png is None:
            return None
        import io as _io
        from PIL import Image as PILImage, ImageDraw
        base    = PILImage.open(_io.BytesIO(self._last_png)).convert("RGBA")
        overlay = PILImage.new("RGBA", base.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)
        for cx, cy, r in circles_px:
            bbox = [cx - r, cy - r, cx + r, cy + r]
            draw.ellipse(bbox, fill=(255, 140, 0, 100))
        composite     = PILImage.alpha_composite(base, overlay).convert("RGB")
        buf           = _io.BytesIO()
        composite.save(buf, format="PNG")
        self._last_png = buf.getvalue()
        return self._last_png

    def render_room_outline(
        self,
        contour_points_mm: list[tuple[float, float]],
    ) -> bytes | None:
        """F-EPHEMERAL — Overlay accumulated room outline on _last_png.

        v3.2.1 REDESIGN — contour_points_mm are now real-world millimetre
        coordinates (GridStore boundary-cell centres — see
        outline_store.compute_boundary_points_mm), not pre-rendered pixels
        from a fixed 6x6m canvas. This fixes two field-confirmed bugs at
        once, both stemming from the old PNG-based pipeline:

          1. OFFSET — contour pixels were stored in a FIXED identity px
             space (render_for_outline(), v2.8.2) but composited onto an
             auto-fitted _last_png. On a real 980 OG this displaced the
             entire outline ~2.4 m from the cleaning path (fit_ratio 0.667,
             dock fitted to (332,137) vs. outline anchored at (300,300)).
          2. CLIPPING — the fixed 600x600px / 10mm-per-px canvas is a hard
             6x6m window around the dock. On the same OG, 57% of the
             house's known GridStore footprint (1603/2798 cells) fell
             outside that window and could never appear in the outline,
             however many missions accumulated.

        mm coordinates are converted with _mm_to_px_fit() — the SAME
        transform every other geometry overlay (walls, doors, zones) uses
        — so the outline is always pixel-aligned with the current
        auto-fitted render and is never window-clipped, since GridStore
        itself is unbounded.

        Mirrors render_keepout_zones() compositing pattern.
        Colour: grey (180, 180, 180, 140) — visible but not distracting.
        Returns new PNG bytes, or None when _last_png is None or no points.
        """
        if not contour_points_mm or self._last_png is None:
            return None
        import io as _io
        from PIL import Image as PILImage, ImageDraw
        base    = PILImage.open(_io.BytesIO(self._last_png)).convert("RGBA")
        overlay = PILImage.new("RGBA", base.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)
        for (x_mm, y_mm) in contour_points_mm:
            fx, fy = self._mm_to_px_fit(x_mm, y_mm)
            # Draw a 2×2 pixel dot per contour point
            if 0 <= fx < base.width and 0 <= fy < base.height:
                draw.rectangle([fx, fy, fx + 1, fy + 1], fill=(180, 180, 180, 140))
        composite     = PILImage.alpha_composite(base, overlay).convert("RGB")
        buf           = _io.BytesIO()
        composite.save(buf, format="PNG")
        self._last_png = buf.getvalue()
        return self._last_png

    @property
    def points_mm(self) -> list[tuple[float, float]]:
        """Return pose points in mm (used by diagnostics.py for
        last_mission_trajectory_mm)."""
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
        """Layer 1.5 — dashed room outlines and door crossing markers.

        Draws room bounding boxes from RoomSegStore (expanded by
        wall_offset_mm) as dashed grey rectangles, and each DoorMarker as a
        small filled circle. Suppressed entirely when user geometry already
        exists — the two layers must never overlap to avoid confusion.

        ROOM-SEG Stage 5 — room outlines backed by RoomSegStore, not
        ZoneStore (the gap heuristic proved unreliable — see
        ROOM_SEGMENTATION_NOTES.md). The door-marker drawing below was
        ALREADY unaffected by this swap: GeometryStore.door_markers is fed
        from RoomSegStore.doors since the earlier door-marker-source
        change (update_from_room_seg_store), so this method's marker loop
        needed no changes at all.
        """
        if self._room_seg_store is None:
            return
        if self._geometry_store is not None and self._geometry_store.has_user_geometry:
            return  # user has confirmed layout — suggestions no longer shown

        # Room outline rectangles (dashed)
        offset = self._geometry_store.wall_offset_mm if self._geometry_store else 200
        for room in self._room_seg_store.rooms.values():
            if room.hidden:
                continue
            x_min, x_max, y_min, y_max = room.bbox
            x1, y1 = self._mm_to_px_fit(x_min - offset, y_max + offset)
            x2, y2 = self._mm_to_px_fit(x_max + offset, y_min - offset)
            self._draw_dashed_rect(draw, x1, y1, x2, y2, SUGGEST_OUTLINE, SUGGEST_DASH)
            # Room name label at bbox centroid
            if room.name:
                lx, ly = self._mm_to_px_fit(
                    (x_min + x_max) / 2,
                    (y_min + y_max) / 2,
                )
                draw.text((lx, ly), room.name, fill=SUGGEST_LABEL, anchor="mm", font=LABEL_FONT)

        # Door crossing markers (filled circles)
        if self._geometry_store:
            for marker in self._geometry_store.door_markers:
                if marker.mission_count < 2:
                    continue  # only show markers seen in ≥2 missions
                mx, my = self._mm_to_px_fit(marker.cx, marker.cy)
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
            x1, y1 = self._mm_to_px_fit(wall.x1, wall.y1)
            x2, y2 = self._mm_to_px_fit(wall.x2, wall.y2)
            draw.line([x1, y1, x2, y2], fill=WALL_FILL, width=WALL_WIDTH)
            draw.line([x1, y1, x2, y2], fill=WALL_CENTRE, width=WALL_CENTRE_WIDTH)
            if wall.label:
                mx, my = (x1 + x2) // 2, (y1 + y2) // 2
                draw.text((mx, my - 8), wall.label, fill=WALL_FILL, anchor="mm", font=LABEL_FONT_SMALL)

        for door in self._geometry_store.doors:
            cx, cy = self._mm_to_px_fit(door.cx, door.cy)
            half_w_px = max(1, int(door.width_mm / 2 / self._fit_scale))
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
                draw.text((cx, cy - 10), door.label, fill=DOOR_FILL, anchor="mm", font=LABEL_FONT_SMALL)

        for obs in self._geometry_store.obstacles:
            x1, y1 = self._mm_to_px_fit(obs.x, obs.y + obs.h)   # top-left in image
            x2, y2 = self._mm_to_px_fit(obs.x + obs.w, obs.y)   # bottom-right in image
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
                draw.text((lx, ly), obs.label, fill=OBSTACLE_OUTLINE, anchor="mm", font=LABEL_FONT_SMALL)

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
        """Convert mm dock-relative coordinates to INITIAL canvas pixel space.

        v2.6.3 B1/B2 — always uses the fixed cfg.scale and canvas centre so
        that all stored _points are in a CONSISTENT coordinate space regardless
        of when render() was last called.  This eliminates the jump-detection
        false-reject caused by the previous implementation using the fit-adjusted
        _fit_scale/_fit_cx/cy which change after every render() call.

        Geometry layers drawn inside render() call _mm_to_px_fit() instead,
        which uses the fit-adjusted parameters so overlay elements stay aligned
        with the auto-zoomed content.
        """
        cx = cy = self._cfg.size_px // 2
        return (
            int(cx + x_mm / self._cfg.scale),
            int(cy - y_mm / self._cfg.scale),
        )

    def _mm_to_px_fit(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """Convert mm to FIT-ADJUSTED canvas pixels.

        Only valid inside render() after _compute_fit() has been called for the
        current frame.  Used by geometry overlay layers (zones, walls, doors,
        obstacles, door markers) so they stay spatially aligned with the
        auto-zoomed cleaning path.
        """
        return (
            int(self._fit_cx + x_mm / self._fit_scale),
            int(self._fit_cy - y_mm / self._fit_scale),
        )

    def _mm_radius_px(self, mm: float, min_px: int = 2) -> int:
        """Convert a real-world mm radius/width to zoom-correct pixels.

        Mathematically: base_r_px * (cfg.scale / fit_scale)
                      = (mm / cfg.scale) * (cfg.scale / fit_scale)
                      = mm / fit_scale

        cfg.scale cancels out — this is the single source of truth for
        converting any real-world mm size to on-canvas pixels, used by
        every marker (cleaned-area circles, path width, dock, robot icon,
        stuck triangle) so they all scale together under auto_fit zoom.
        """
        return max(min_px, int(mm / self._fit_scale))

    def _draw_cleaned_area(
        self, draw: ImageDraw.ImageDraw,
        fit_px: Callable[[int, int], tuple[int, int]],  # noqa: F821 — TYPE_CHECKING forward reference, pyflakes/ruff scope limitation
    ) -> None:
        """Draw a filled circle per pose point to approximate cleaned area.

        pose.point.x/y is the robot's own reported navigation centre — the
        path line traces that centre, not an edge. The circle radius is
        therefore the real chassis radius (cfg.robot_diameter_mm / 2), so the
        rendered footprint matches the robot's actual physical size at each
        point along its path, rather than an arbitrary cleaning-width guess.
        """
        r = self._mm_radius_px(self._cfg.robot_diameter_mm / 2, min_px=2)
        fitted = [fit_px(px, py) for px, py in self._points]
        for px, py in self._interpolated(fitted, max_gap_px=r):
            draw.ellipse([px - r, py - r, px + r, py + r], fill=CLEANED_COLOUR)

    def _draw_path(
        self, draw: ImageDraw.ImageDraw,
        fit_px: Callable[[int, int], tuple[int, int]],  # noqa: F821 — TYPE_CHECKING forward reference, pyflakes/ruff scope limitation
    ) -> None:
        """Draw the travel path polyline, clipped to canvas bounds.

        v2.9.0: line width now derives from PATH_WIDTH_MM via the same
        zoom-correct conversion as the cleaned-area circles (_mm_radius_px),
        instead of a fixed pixel constant. Previously the path stayed a
        constant 6px regardless of auto_fit zoom level, while the cleaned-
        area circles scaled with it — causing the two layers to visually
        drift apart at different zoom levels (e.g. path looking too thin
        relative to the cleaned-area blob on a zoomed-in map).
        """
        if len(self._points) < 2:
            return
        size = self._cfg.size_px
        width_px = self._mm_radius_px(PATH_WIDTH_MM, min_px=PATH_MIN_PX)
        clipped = [
            (max(0, min(size - 1, px)), max(0, min(size - 1, py)))
            for px, py in (fit_px(px, py) for px, py in self._points)
        ]
        draw.line(clipped, fill=PATH_COLOUR, width=width_px)

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
