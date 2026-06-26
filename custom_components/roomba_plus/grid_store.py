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

# v2.9.0 — split following the outline_store.py precedent (v2.8.2 bug-hunt).
# _HA_STORE_VERSION is homeassistant.helpers.storage.Store()'s OWN version
# argument and must NEVER change without a real _async_migrate_func — the
# base Store class's default migrate function raises NotImplementedError,
# which would crash async_setup_entry for every existing installation on
# upgrade (confirmed in the field, v2.8.2). PAYLOAD_VERSION is our own
# application-level marker, checked in async_load() below — free to bump
# whenever the payload's *meaning* changes, even though its on-disk shape
# doesn't (this bump: cells/stuck were accumulated from pose data that was
# 10x too small everywhere, confirmed 2026-06-19 — see POSE_POINT_CM_TO_MM
# in const.py. Old cells are spatially WRONG, not just stale, so they are
# discarded on load rather than left to slowly decay out over ~14 missions).
_HA_STORE_VERSION      = 1
PAYLOAD_VERSION        = 2

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


def _disk_filled_cells(
    pose_points: list[tuple[float, float]],
    radius_mm: float,
) -> set[tuple[int, int]]:
    """v2.9.0 (DISK-FILL) — every cell within radius_mm of ANY pose point.

    Marks the robot's actual swept footprint, not just the single cell its
    chassis centre happened to be in. Module-level (not a GridStore method)
    since it only needs the radius and point list — pure geometry, no
    instance state, easy to unit-test in isolation.

    Distance check uses each candidate cell's CENTRE point against the pose
    point, matching how a circle of radius_mm centred on the robot would
    actually cover the cell grid (not just bounding-box overlap, which
    would over-include corner cells the circle doesn't actually reach).
    """
    touched: set[tuple[int, int]] = set()
    cell_radius = int(radius_mm // CELL_SIZE_MM) + 1
    for x_mm, y_mm in pose_points:
        cx, cy = _mm_to_cell(x_mm, y_mm)
        # The cell containing the point itself is always touched, even if
        # the point sits near a corner of that cell (far from its centre)
        # and radius_mm is small enough that the centre-distance check
        # below would otherwise miss it. The robot is physically inside
        # this cell regardless of where exactly within it.
        touched.add((cx, cy))
        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                gx, gy = cx + dx, cy + dy
                cell_centre_x = gx * CELL_SIZE_MM + CELL_SIZE_MM / 2
                cell_centre_y = gy * CELL_SIZE_MM + CELL_SIZE_MM / 2
                if (cell_centre_x - x_mm) ** 2 + (cell_centre_y - y_mm) ** 2 <= radius_mm ** 2:
                    touched.add((gx, gy))
    return touched


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
        # L7 (v2.7.0): _stuck extended from plain count to structured dict.
        # Format: {(gx, gy): {"count": int, "times": [(weekday, hour), ...]}}
        # "times" accumulates (weekday, hour) of each stuck event for pattern detection.
        # Backward-compatible: async_load migrates plain-int v1 values to {"count": N, "times": []}.
        self._stuck: dict[tuple[int, int], dict] = {}
        # P3: edge_coverage_ratio cache keyed by edge_depth_mm — invalidated when
        # _cells changes. Keyed dict (not scalar) so multiple edge_depth values
        # can coexist safely if future callers use non-default parameters.
        self._edge_ratio_cache: dict[float, float] = {}

    # ── Persistence ───────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        """Load persisted grid from hass.storage.

        v2.9.0 — discards payloads from before the pose-units fix (no
        "version" field, or an older PAYLOAD_VERSION): those cells were
        built from pose coordinates that were 10x too small, so they are
        spatially wrong, not just stale. Starting empty and re-accumulating
        from corrected pose data is safer than keeping misplaced cells
        around to slowly decay out over many missions.
        """
        from homeassistant.helpers.storage import Store
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("GridStore: no persisted data for %s", entry_id)
            return
        if data.get("version") != PAYLOAD_VERSION:
            _LOGGER.warning(
                "GridStore: discarding pre-units-fix grid data for %s "
                "(stored cells were built from pose coordinates that were "
                "10x too small) — starting empty", entry_id,
            )
            return
        try:
            raw_cells = data.get("cells", {})
            self._cells = {
                (int(k.split(",")[0]), int(k.split(",")[1])): float(v)
                for k, v in raw_cells.items()
            }
            raw_stuck = data.get("stuck", {})
            for k, v in raw_stuck.items():
                cell = (int(k.split(",")[0]), int(k.split(",")[1]))
                if isinstance(v, (int, float)):
                    # v1 format: plain integer count — migrate to v2 struct
                    self._stuck[cell] = {"count": int(v), "times": []}
                elif isinstance(v, dict):
                    # v2 format: structured dict with count + times list
                    self._stuck[cell] = {
                        "count": int(v.get("count", 0)),
                        "times": [list(t) for t in v.get("times", [])],
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
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "version": PAYLOAD_VERSION,
            "cells": {f"{gx},{gy}": w for (gx, gy), w in self._cells.items()},
            "stuck": {
                f"{gx},{gy}": {"count": v["count"], "times": v["times"]}
                for (gx, gy), v in self._stuck.items()
            },
        })

    # ── Write ──────────────────────────────────────────────────────────────────

    def update_from_mission(
        self,
        pose_points: list[tuple[float, float]],
        stuck_points: list[tuple[float, float]],
        stuck_wh: tuple[int, int] | None = None,
        robot_radius_mm: float | None = None,
    ) -> None:
        """Apply EMA decay to all cells, then add visit increments for this mission.

        Args:
            pose_points:  List of (x_mm, y_mm) robot positions during the mission.
            stuck_points: List of (x_mm, y_mm) positions where robot was stuck.
            stuck_wh:     Optional (weekday, hour) of mission start in HA local time.
                          Passed by the caller (image.py) which already has dt_util;
                          grid_store.py remains HA-free per its design contract.
            robot_radius_mm: v2.9.0 (DISK-FILL) — when given, each pose point marks
                          every cell within this radius of the point as visited,
                          not just the single cell containing the exact point.
                          Caller (image.py) passes the robot's real chassis radius
                          (robot_diameter_mm / 2 from the renderer config it already
                          has, kept HA-free here by taking a plain float — see
                          rationale below). None preserves the old single-cell
                          behaviour (used by tests that don't care about this).

                          Rationale: GridStore previously marked only the exact
                          cell containing each pose sample — the robot's pose is
                          its chassis CENTRE, not a single point of contact, so
                          this undercounted real swept floor area. Confirmed via
                          real data (June 2026): the raw single-cell trace doesn't
                          even form one connected blob under 8-connectivity (median
                          inter-sample step ~67mm vs. CELL_SIZE_MM=150mm — many
                          consecutive samples skip whole cells), and any coverage
                          fraction derived from it (edge_coverage_ratio,
                          coverage_by_polygon — used in the "Kitchen 75% · Hallway
                          60%" mission-history display) was systematically too low.
                          Disk-filling each sample to the robot's actual footprint
                          fixes both, independent of any future room-segmentation
                          work this was originally investigated for.
        """
        # 1. Decay all existing cells; prune below threshold
        # P3: cells are about to change — invalidate the edge ratio cache
        self._edge_ratio_cache.clear()
        to_prune = [
            cell for cell, weight in self._cells.items()
            if weight * DECAY < PRUNE_THRESHOLD
        ]
        for cell in to_prune:
            del self._cells[cell]
        for cell in self._cells:
            self._cells[cell] *= DECAY

        # 2. Add visit increments for visited cells
        # v2.9.0 (DISK-FILL): collect the FULL set of cells touched by this
        # mission first (deduplicated across all pose points), then apply
        # VISIT_INCREMENT once per touched cell — preserves the existing
        # "one increment per mission per cell" semantics even though a
        # single cell is now very likely touched by MANY overlapping
        # pose-point disks, not just one exact point.
        if robot_radius_mm is not None and robot_radius_mm > 0:
            touched = _disk_filled_cells(pose_points, robot_radius_mm)
        else:
            touched = {_mm_to_cell(x, y) for x, y in pose_points}
        for cell in touched:
            self._cells[cell] = min(1.0, self._cells.get(cell, 0.0) + VISIT_INCREMENT)

        # 3. Record stuck events with optional time context
        for x_mm, y_mm in stuck_points:
            cell = _mm_to_cell(x_mm, y_mm)
            if cell not in self._stuck:
                self._stuck[cell] = {"count": 0, "times": []}
            self._stuck[cell]["count"] += 1
            if stuck_wh is not None:
                times_list = self._stuck[cell]["times"]
                times_list.append(list(stuck_wh))
                # Cap times list at 200 entries to prevent unbounded growth
                if len(times_list) > 200:
                    self._stuck[cell]["times"] = times_list[-200:]

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
                self._stuck[cell] = {"count": STUCK_HOTSPOT_THRESHOLD, "times": []}
                seeded += 1
        return seeded

    # ── Read ───────────────────────────────────────────────────────────────────

    @property
    def cell_count(self) -> int:
        """Number of active (non-pruned) cells."""
        return len(self._cells)

    @property
    def cells(self) -> dict[tuple[int, int], float]:
        """Read-only snapshot of (gx, gy) -> EMA weight for active cells.

        ROOM-SEG — exposed for room_seg_store.py's segmentation pipeline,
        which needs the actual visited-cell set, not just a count. Returns
        a shallow copy: callers must not rely on seeing live updates, and
        GridStore's own internal mutation is never affected by what a
        caller does with the returned dict.
        """
        return dict(self._cells)

    @property
    def stuck_event_count(self) -> int:
        """Total stuck events recorded."""
        return sum(v["count"] for v in self._stuck.values())

    def bounding_box_mm(self) -> tuple[float, float, float, float] | None:
        """Return (x_min, x_max, y_min, y_max) in mm, or None if no cells."""
        if not self._cells:
            return None
        xs = [_cell_to_mm(gx, gy)[0] for gx, gy in self._cells]
        ys = [_cell_to_mm(gx, gy)[1] for gx, gy in self._cells]
        return min(xs), max(xs), min(ys), max(ys)

    def edge_coverage_ratio(
        self, edge_depth_mm: float = 300.0
    ) -> float | None:
        """F12d — Return ratio of edge cells to total cells.

        Edge cells are those within `edge_depth_mm` of any side of the
        bounding box. A low ratio with high total coverage indicates
        the robot is over-cleaning the centre and under-covering edges.

        Returns None when fewer than 10 cells exist (insufficient data), or
        when the bounding box itself is too small for the edge/centre
        distinction to be meaningful — v2.8.2: a robot confined to e.g. a
        1m x 1m patch (heavily fragmented exploration, or genuinely a tiny
        space) would otherwise have *every* cell within edge_depth_mm of
        some side, producing a near-1.0 ratio that looks like "excellent
        edge coverage" but is really just "there is no centre region to
        compare against". Requiring each bbox dimension to exceed
        4 * edge_depth_mm guarantees a real interior exists.
        P3: result is cached by edge_depth_mm and invalidated by update_from_mission;
        safe to call repeatedly during a mission without re-computing on every pose
        update. Keyed by edge_depth_mm so different callers with different parameters
        each get their own correct cached value.
        """
        # P3: return cached result when available (invalidated by update_from_mission)
        cached = self._edge_ratio_cache.get(edge_depth_mm)
        if cached is not None:
            return cached
        if len(self._cells) < 10:
            return None
        bbox = self.bounding_box_mm()
        if bbox is None:
            return None
        x_min, x_max, y_min, y_max = bbox
        # v2.8.2 — bbox too small for the edge/centre distinction to mean
        # anything (see docstring). Mirrors the "insufficient data" None
        # return above rather than caching a misleading number.
        _min_span = 4.0 * edge_depth_mm
        if (x_max - x_min) < _min_span or (y_max - y_min) < _min_span:
            return None
        edge_count = 0
        for (gx, gy) in self._cells:
            x_mm, y_mm = _cell_to_mm(gx, gy)
            if (
                x_mm - x_min <= edge_depth_mm
                or x_max - x_mm <= edge_depth_mm
                or y_mm - y_min <= edge_depth_mm
                or y_max - y_mm <= edge_depth_mm
            ):
                edge_count += 1
        result = round(edge_count / len(self._cells), 4)
        self._edge_ratio_cache[edge_depth_mm] = result
        return result

    def hotspots(
        self, threshold: int = STUCK_HOTSPOT_THRESHOLD
    ) -> list[dict[str, Any]]:
        """Return stuck hotspot cells as dicts for the REST API hazards endpoint.

        Each dict contains gx, gy, x_mm, y_mm, stuck_count, bearing_deg,
        distance_mm. room_name is always None — populated by UmfAligner in v2.3.
        """
        result = []
        for (gx, gy), v in self._stuck.items():
            count = v["count"]
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

    def stuck_pattern(
        self,
        threshold: int = 8,
        dominant_pct: float = 0.60,
    ) -> dict[tuple[int, int], tuple[int, int]] | None:
        """L7 — return cells with a dominant (weekday, hour) stuck pattern.

        Returns {cell: (weekday, hour)} for cells where:
          - count >= threshold (default 8 stucks)
          - ≥ dominant_pct (default 60%) of time entries share the same slot

        Returns None when no such pattern exists.
        Cells with no time data (migrated from v1 or seeded) are skipped.
        """
        from collections import Counter

        patterns: dict[tuple[int, int], tuple[int, int]] = {}
        for cell, v in self._stuck.items():
            if v["count"] < threshold:
                continue
            times = v.get("times", [])
            if not times:
                continue  # no time data — cannot determine pattern
            slot_counts: Counter = Counter(tuple(t) for t in times)
            (most_common_slot, most_common_count) = slot_counts.most_common(1)[0]
            # Use count (not len(times)) as denominator so seeded/migrated
            # entries with sparse time data don't inflate the percentage.
            denom = max(len(times), v["count"])
            if most_common_count / denom >= dominant_pct:
                patterns[cell] = most_common_slot

        return patterns if patterns else None

    def coverage_by_polygon(
        self,
        polygons_pose: dict[str, list[tuple[float, float]]],
    ) -> dict[str, float]:
        """v2.3.0 Step 9 — Return per-room coverage fraction from polygon intersection.

        For each room polygon, counts grid cells with EMA score > PRUNE_THRESHOLD
        whose centre falls inside the polygon. Fraction = visited / total_in_polygon.

        Uses ray-casting point-in-polygon — no external geometry library.

        polygons_pose: {rid: [(x_mm, y_mm), ...]} — vertices in pose space (mm).
        Returns {rid: fraction} where fraction ∈ [0.0, 1.0].
        Empty dict when no cells or all polygons are empty.

        v2.9.0 — DENOMINATOR FIX. Previously iterated only over
        self._cells (cells we have ANY recorded weight for) when counting
        `total`, instead of every geometrically possible cell within the
        polygon. Since pruned cells (score <= PRUNE_THRESHOLD) are deleted
        from self._cells entirely — never left behind with a low score —
        virtually every entry that DOES exist already satisfies
        `score > PRUNE_THRESHOLD` by construction. That made `visited`
        and `total` nearly identical regardless of how much of the room
        was actually swept, so the fraction reported ~100% even for a
        barely-explored corner of a large room. Fixed: `total` now comes
        from iterating every grid cell whose centre falls within the
        polygon's bounding box (a pure geometric count, independent of
        self._cells), while `visited` still only counts those AMONG them
        that self._cells actually has recorded with sufficient weight.
        """
        result: dict[str, float] = {}
        if not self._cells:
            return result

        for rid, polygon in polygons_pose.items():
            if len(polygon) < 3:
                result[rid] = 0.0
                continue
            # Bounding box for fast pre-filter (include half-cell margin)
            half = CELL_SIZE_MM / 2
            min_x = min(p[0] for p in polygon) - half
            max_x = max(p[0] for p in polygon) + half
            min_y = min(p[1] for p in polygon) - half
            max_y = max(p[1] for p in polygon) + half

            gx_min = int(min_x // CELL_SIZE_MM)
            gx_max = int(max_x // CELL_SIZE_MM)
            gy_min = int(min_y // CELL_SIZE_MM)
            gy_max = int(max_y // CELL_SIZE_MM)

            total = 0
            visited = 0
            for gx in range(gx_min, gx_max + 1):
                for gy in range(gy_min, gy_max + 1):
                    cx, cy = _cell_to_mm(gx, gy)
                    if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
                        continue
                    if not _point_in_polygon_grid(cx, cy, polygon):
                        continue
                    total += 1
                    score = self._cells.get((gx, gy))
                    if score is not None and score > PRUNE_THRESHOLD:
                        visited += 1
            result[rid] = visited / total if total > 0 else 0.0
        return result

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
            cell for cell, v in self._stuck.items()
            if v["count"] >= STUCK_HOTSPOT_THRESHOLD
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


def _point_in_polygon_grid(
    x: float, y: float, polygon: list[tuple[float, float]]
) -> bool:
    """Ray-casting point-in-polygon test for grid cell centres.

    Module-level (not on GridStore) so umf_aligner.py can import the same
    algorithm independently without cross-import. Returns True when inside.
    """
    n      = len(polygon)
    inside = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if ((qy > y) != (py > y)) and (
            x < (px - qx) * (y - qy) / (py - qy) + qx
        ):
            inside = not inside
        px, py = qx, qy
    return inside
