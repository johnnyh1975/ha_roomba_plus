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
from typing import Any

from PIL import Image, ImageDraw

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

# ── Rendering constants ───────────────────────────────────────────────────────
DOCK_HALF       = 6    # px half-side of dock square
ROBOT_RADIUS    = 8    # px robot circle radius
ARROW_LENGTH    = 16   # px direction arrow length
STUCK_RADIUS    = 7    # px stuck triangle circumradius
PATH_WIDTH      = 3    # px path line width

# ── Storage version — bump when dump_state() format changes ──────────────────
_STATE_VERSION  = 1


@dataclass
class RendererConfig:
    """User-configurable rendering parameters from Options Flow."""

    size_px: int   = 600     # Canvas size (square)
    scale: float   = 10.0    # mm per pixel (600px @ 10mm/px → 6m × 6m)
    persist: bool  = True    # Keep last frame between missions


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

    def __init__(self, config: RendererConfig) -> None:
        self._cfg = config
        self._points: list[tuple[int, int]] = []      # pixel coordinates
        self._stuck_px: list[tuple[int, int]] = []    # pixel coordinates
        self._robot_px: tuple[int, int] | None = None # current robot position
        self._theta: float = 0.0                      # current heading (degrees)
        self._last_png: bytes | None = None           # cached output (not persisted)

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

    def add_pose(self, x_mm: float, y_mm: float, theta_deg: float) -> None:
        """Record a new pose point from the MQTT state update.

        roombapy convention: co_ords["x"] = pose_point_y (swapped axes).
        Callers pass already-corrected x_mm / y_mm relative to dock origin.
        Ignores (0, 0, any_theta) at mission start to avoid bogus dock point.
        """
        if x_mm == 0.0 and y_mm == 0.0 and not self._points:
            return  # First point at dock — skip

        px, py = self._mm_to_px(x_mm, y_mm)
        self._points.append((px, py))
        self._robot_px = (px, py)
        self._theta = theta_deg

    def mark_stuck(self) -> None:
        """Record a stuck event at the current robot position."""
        if self._robot_px:
            self._stuck_px.append(self._robot_px)

    def render(self) -> bytes:
        """Render all layers to PNG and return bytes.

        Returns the last cached frame if no new points have been added
        and a cached frame exists (avoids redundant re-renders).
        """
        if not self._points and self._last_png:
            return self._last_png

        size = self._cfg.size_px
        img = Image.new("RGBA", (size, size), BG_COLOUR)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size - 1, size - 1], outline=FLOOR_BORDER, width=2)

        if self._points:
            self._draw_cleaned_area(draw)
            self._draw_path(draw)

        for sx, sy in self._stuck_px:
            self._draw_triangle(draw, sx, sy, STUCK_RADIUS, STUCK_COLOUR)

        cx = cy = size // 2
        draw.ellipse(
            [cx - DOCK_HALF, cy - DOCK_HALF, cx + DOCK_HALF, cy + DOCK_HALF],
            fill=DOCK_COLOUR,
            outline=(30, 120, 30, 255),
            width=2,
        )

        if self._robot_px:
            rx, ry = self._robot_px
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

    # ── Private helpers ───────────────────────────────────────────────────────

    def _mm_to_px(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        cx = cy = self._cfg.size_px // 2
        return (
            int(cx + x_mm / self._cfg.scale),
            int(cy - y_mm / self._cfg.scale),
        )

    def _draw_cleaned_area(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw a filled circle per pose point to approximate cleaned area."""
        r = max(4, int(150 / self._cfg.scale))
        for px, py in self._interpolated(self._points, max_gap_px=r):
            draw.ellipse([px - r, py - r, px + r, py + r], fill=CLEANED_COLOUR)

    def _draw_path(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the travel path polyline, clipped to canvas bounds."""
        if len(self._points) < 2:
            return
        size = self._cfg.size_px
        clipped = [
            (max(0, min(size - 1, px)), max(0, min(size - 1, py)))
            for px, py in self._points
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
