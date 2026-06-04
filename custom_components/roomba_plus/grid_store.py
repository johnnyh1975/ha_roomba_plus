"""GridStore — EMA-weighted occupancy grid for Roomba+.

Sparse dict mapping (gx, gy) integer grid cells to float EMA weights.
Cell size: 150 mm. Coordinates are dock-relative (dock at origin).

EMA constants (validated during v2.2 with diagnostic attributes):
  DECAY           = 0.85  — weight of existing cell on each new mission
  VISIT_INCREMENT = 0.30  — weight added when robot visits a cell
  PRUNE_THRESHOLD = 0.05  — cells below this are removed from the dict

Stuck cells are tracked separately as a count dict {(gx, gy): int}.
A cell is a "stuck hotspot" when stuck_count >= STUCK_HOTSPOT_THRESHOLD.

Lifecycle: instantiate → async_load() → update per mission end → async_save().
No HA dependencies at import time beyond hass.storage — fully unit-testable.
"""
from __future__ import annotations

import logging
import math
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX     = "roomba_plus_grid"
STORAGE_VERSION        = 1

CELL_SIZE_MM           = 150
DECAY                  = 0.85
VISIT_INCREMENT        = 0.30
PRUNE_THRESHOLD        = 0.05
STUCK_HOTSPOT_THRESHOLD = 3


def _mm_to_cell(x_mm: float, y_mm: float) -> tuple[int, int]:
    """Convert dock-relative mm coordinates to integer grid cell indices."""
    return (
        int(math.floor(x_mm / CELL_SIZE_MM)),
        int(math.floor(y_mm / CELL_SIZE_MM)),
    )


def _cell_to_mm(gx: int, gy: int) -> tuple[float, float]:
    """Return the centre of a grid cell in dock-relative mm."""
    return (
        (gx + 0.5) * CELL_SIZE_MM,
        (gy + 0.5) * CELL_SIZE_MM,
    )


def _bearing_deg(x_mm: float, y_mm: float) -> int:
    """Return compass bearing from dock (0 = up/north in map space)."""
    angle = math.degrees(math.atan2(x_mm, y_mm))
    return int(angle % 360)


def _distance_mm(x_mm: float, y_mm: float) -> int:
    """Return Euclidean distance from dock in mm."""
    return int(math.sqrt(x_mm ** 2 + y_mm ** 2))


class GridStore:
    """EMA-weighted occupancy grid.

    Lifecycle (must be respected by callers):
      - Instantiate in async_setup_entry.
      - Call async_load() immediately after.
      - Store in entry.runtime_data.grid_store.
      - Call update_from_mission() at each mission end (from image.py or callbacks).
      - Call async_save() after update_from_mission().
    """

    def __init__(self) -> None:
        self._cells: dict[tuple[int, int], float] = {}   # (gx, gy) → EMA weight
        self._stuck: dict[tuple[int, int], int]   = {}   # (gx, gy) → stuck count

    # ── Persistence ───────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        """Load persisted grid from hass.storage."""
        from homeassistant.helpers.storage import Store
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("GridStore: no persisted data for %s", entry_id)
            return
        try:
            raw_cells = data.get("cells", {})
            self._cells = {
                (int(k.split(",")[0]), int(k.split(",")[1])): float(v)
                for k, v in raw_cells.items()
            }
            raw_stuck = data.get("stuck", {})
            self._stuck = {
                (int(k.split(",")[0]), int(k.split(",")[1])): int(v)
                for k, v in raw_stuck.items()
            }
            _LOGGER.debug(
                "GridStore: loaded %d cell(s), %d stuck cell(s) for %s",
                len(self._cells), len(self._stuck), entry_id,
            )
        except (KeyError, ValueError, IndexError) as exc:
            _LOGGER.warning("GridStore: failed to load — %s; starting empty", exc)
            self._cells = {}
            self._stuck = {}

    async def async_save(self, hass: Any, entry_id: str) -> None:
        """Persist current grid to hass.storage."""
        from homeassistant.helpers.storage import Store
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "cells": {f"{gx},{gy}": w for (gx, gy), w in self._cells.items()},
            "stuck": {f"{gx},{gy}": c for (gx, gy), c in self._stuck.items()},
        })

    # ── Write ──────────────────────────────────────────────────────────────────

    def update_from_mission(
        self,
        pose_points: list[tuple[float, float]],
        stuck_points: list[tuple[float, float]],
    ) -> None:
        """Apply EMA decay to all cells, then add visit increments for this mission.

        Args:
            pose_points:  List of (x_mm, y_mm) robot positions during the mission.
            stuck_points: List of (x_mm, y_mm) positions where robot was stuck.
        """
        # 1. Decay all existing cells; prune below threshold
        to_prune = [
            cell for cell, weight in self._cells.items()
            if weight * DECAY < PRUNE_THRESHOLD
        ]
        for cell in to_prune:
            del self._cells[cell]
        for cell in self._cells:
            self._cells[cell] *= DECAY

        # 2. Add visit increments for visited cells
        for x_mm, y_mm in pose_points:
            cell = _mm_to_cell(x_mm, y_mm)
            self._cells[cell] = min(1.0, self._cells.get(cell, 0.0) + VISIT_INCREMENT)

        # 3. Record stuck events
        for x_mm, y_mm in stuck_points:
            cell = _mm_to_cell(x_mm, y_mm)
            self._stuck[cell] = self._stuck.get(cell, 0) + 1

        _LOGGER.debug(
            "GridStore: update complete — %d cell(s), %d stuck cell(s)",
            len(self._cells), len(self._stuck),
        )

    def seed_from_observed_zones(
        self, centroids: list[dict[str, Any]]
    ) -> int:
        """Seed stuck cells from cloud-observed obstacle centroids.

        F22a prerequisite — primes stuck hotspot locations from cloud UMF
        observed_zones before local GridStore has accumulated data.
        Only writes cells not already in self._stuck (no overwrite).

        Args:
            centroids: List of dicts with keys 'x' and 'y' (UMF units,
                       stored with space='umf' tag — not used for pose-space
                       rendering until Q6 coordinate units confirmed in v2.3).

        Returns:
            Count of new stuck cells seeded.
        """
        seeded = 0
        for c in centroids:
            x = c.get("x")
            y = c.get("y")
            if x is None or y is None:
                continue
            try:
                cell = _mm_to_cell(float(x), float(y))
            except (TypeError, ValueError):
                continue
            if cell not in self._stuck:
                self._stuck[cell] = STUCK_HOTSPOT_THRESHOLD
                seeded += 1
        return seeded

    # ── Read ───────────────────────────────────────────────────────────────────

    @property
    def cell_count(self) -> int:
        """Number of active (non-pruned) cells."""
        return len(self._cells)

    @property
    def stuck_event_count(self) -> int:
        """Total stuck events recorded."""
        return sum(self._stuck.values())

    def bounding_box_mm(self) -> tuple[float, float, float, float] | None:
        """Return (x_min, x_max, y_min, y_max) in mm, or None if no cells."""
        if not self._cells:
            return None
        xs = [_cell_to_mm(gx, gy)[0] for gx, gy in self._cells]
        ys = [_cell_to_mm(gx, gy)[1] for gx, gy in self._cells]
        return min(xs), max(xs), min(ys), max(ys)

    def hotspots(
        self, threshold: int = STUCK_HOTSPOT_THRESHOLD
    ) -> list[dict[str, Any]]:
        """Return stuck hotspot cells as dicts for the REST API hazards endpoint.

        Each dict contains gx, gy, x_mm, y_mm, stuck_count, bearing_deg,
        distance_mm. room_name is always None — populated by UmfAligner in v2.3.
        """
        result = []
        for (gx, gy), count in self._stuck.items():
            if count < threshold:
                continue
            x_mm, y_mm = _cell_to_mm(gx, gy)
            result.append({
                "gx":          gx,
                "gy":          gy,
                "x_mm":        x_mm,
                "y_mm":        y_mm,
                "stuck_count": count,
                "room_name":   None,   # populated in v2.3 via UmfAligner
                "bearing_deg": _bearing_deg(x_mm, y_mm),
                "distance_mm": _distance_mm(x_mm, y_mm),
                "source":      "stuck_events",
            })
        return sorted(result, key=lambda h: h["stuck_count"], reverse=True)

    def render_heatmap(self, size_px: int = 400) -> bytes | None:
        """Render the occupancy grid as a PNG heatmap.

        Returns None when no cells exist (before first mission).
        Called from RoombaCoverageImage.async_image().

        Colour mapping:
          High EMA weight → dark blue (frequently visited)
          Low EMA weight  → light blue (rarely visited)
          Stuck hotspot   → red overlay
        """
        if not self._cells:
            return None
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            _LOGGER.warning("GridStore: Pillow not available — cannot render heatmap")
            return None

        import io
        bbox = self.bounding_box_mm()
        if bbox is None:
            return None
        x_min, x_max, y_min, y_max = bbox
        span_x = max(x_max - x_min, CELL_SIZE_MM)
        span_y = max(y_max - y_min, CELL_SIZE_MM)
        scale = size_px / max(span_x, span_y)

        img = Image.new("RGBA", (size_px, size_px), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)

        hotspot_cells = {
            cell for cell, c in self._stuck.items()
            if c >= STUCK_HOTSPOT_THRESHOLD
        }

        for (gx, gy), weight in self._cells.items():
            x_mm, y_mm = _cell_to_mm(gx, gy)
            px = int((x_mm - x_min) * scale)
            py = int((y_mm - y_min) * scale)
            cell_px = max(2, int(CELL_SIZE_MM * scale))
            if (gx, gy) in hotspot_cells:
                colour: tuple[int, int, int, int] = (220, 50, 50, 220)
            else:
                blue = int(255 * weight)
                r = int(30 * (1 - weight))
                g = int(100 * (1 - weight))
                colour = (r, g, blue, 200)
            draw.rectangle(
                [px, py, px + cell_px - 1, py + cell_px - 1],
                fill=colour,
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
