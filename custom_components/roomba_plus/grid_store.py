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

# v3.2.0 FURNITURE — rolling per-cell coverage-history bitmask, bit 0 =
# most recent mission. Recent-absence window (bits 0..2) must be all-zero
# for a cell to be a candidate; the established window (bits 3..22, the
# 20 missions before that) needs at least _FURNITURE_ESTABLISHED_MIN_HITS
# set — not all 20, so a robot that occasionally misses a cell for
# unrelated reasons (mid-mission stop, brief obstacle) doesn't reset the
# "this used to be reliably covered" signal.
_FURNITURE_RECENT_ABSENT       = 3
_FURNITURE_ESTABLISHED_WINDOW  = 20
_FURNITURE_ESTABLISHED_MIN_HITS = 18
_FURNITURE_WINDOW_BITS = _FURNITURE_RECENT_ABSENT + _FURNITURE_ESTABLISHED_WINDOW
_FURNITURE_HISTORY_MASK = (1 << _FURNITURE_WINDOW_BITS) - 1
_FURNITURE_RECENT_MASK = (1 << _FURNITURE_RECENT_ABSENT) - 1
_FURNITURE_ESTABLISHED_MASK = (
    ((1 << _FURNITURE_ESTABLISHED_WINDOW) - 1) << _FURNITURE_RECENT_ABSENT
)


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
        # v3.2.0 FURNITURE — rolling per-cell coverage-history bitmask
        # (bit 0 = most recent mission) + a companion age counter so a
        # freshly-tracked cell (fewer than _FURNITURE_WINDOW_BITS missions
        # of history) isn't mistaken for "was covered, now isn't" — the
        # zero-padding from before the cell was ever tracked would
        # otherwise look identical to genuine absence.
        self._coverage_history: dict[tuple[int, int], int] = {}
        self._coverage_history_age: dict[tuple[int, int], int] = {}
        # v3.2.0 FURNITURE — when a cell's Repair Issue has been dismissed
        # by the user, records WHEN (HA's own issue registry tracks THAT
        # it was dismissed via dismissed_version, but not WHEN — see
        # repairs.py's async_check_furniture_change). ISO timestamp
        # string, keyed by cell. Cleared once 30 days have passed, at
        # which point the issue is allowed to fire again if the
        # candidate condition still holds.
        # v3.2.1 DUAL-GRID (structure inference) — a SECOND, independent
        # cell-weight dict, updated with the exact centre cell of each pose
        # point only, never disk-filled. Same EMA decay/prune mechanics as
        # self._cells, entirely separate accumulation.
        #
        # Rationale: self._cells is disk-filled (v2.9.0, robot_radius_mm
        # sweep) — correct for coverage tracking (it IS the real swept
        # area) but wrong as segmentation input: a sweep radius wide
        # enough to reach a nearby interior wall from both sides erases
        # the wall from the visited-cell footprint entirely. Confirmed on
        # real field data: a single mission's centre-only trace produced
        # 315 thin-separator (candidate-wall) cells; the SAME mission
        # disk-filled produced only 75 — the disk-fill erases ~76% of the
        # structural signal before segmentation ever sees it.
        #
        # Deliberately NOT yet consumed by room_segmentation.py — this is
        # data-collection scaffolding. GridStore.cells (disk-filled) stays
        # the sole segmentation input until structure_cells has enough
        # accumulated history to validate against, per the honest
        # end-to-end evaluation this idea still needs (proxy-metric only
        # so far, from a single mission).
        self._furniture_dismissed_at: dict[tuple[int, int], str] = {}
        self._structure_cells: dict[tuple[int, int], float] = {}
        # v3.4.0 GS-SMART-COVERAGE — monotonic per-robot high-water mark of
        # the highest nMssn (robot lifetime mission counter) already fed
        # into this GridStore, from EITHER the live path (image.py, real
        # pose) or the cloud-backfill path (callbacks.py, UMF-derived
        # pose for pose-less lewis-firmware robots). Shared between both
        # paths specifically to prevent double-counting a mission that
        # the live path already processed — see record_processed_nmssn().
        self._last_processed_nmssn: int = 0

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
            raw_cells = data.get("cells") or {}
            self._cells = {
                (int(k.split(",")[0]), int(k.split(",")[1])): float(v)
                for k, v in raw_cells.items()
            }
            raw_stuck = data.get("stuck") or {}
            for k, v in raw_stuck.items():
                cell = (int(k.split(",")[0]), int(k.split(",")[1]))
                if isinstance(v, (int, float)):
                    # v1 format: plain integer count — migrate to v2 struct
                    self._stuck[cell] = {"count": int(v), "times": []}
                elif isinstance(v, dict):
                    # v2 format: structured dict with count + times list
                    self._stuck[cell] = {
                        "count": int(v.get("count", 0)),
                        "times": [list(t) for t in (v.get("times") or [])],
                    }
            _LOGGER.debug(
                "GridStore: loaded %d cell(s), %d stuck cell(s) for %s",
                len(self._cells), len(self._stuck), entry_id,
            )
            # v3.2.0 FURNITURE — additive field, no PAYLOAD_VERSION bump
            # needed: a payload saved before this existed simply has no
            # "coverage_history" key, which is indistinguishable from (and
            # exactly as safe as) a fresh cold start for this specific
            # tracker — unlike the v1->v2 pose-units case above, old data
            # here isn't wrong, just incomplete.
            raw_history = data.get("coverage_history") or {}
            self._coverage_history = {
                (int(k.split(",")[0]), int(k.split(",")[1])): int(v)
                for k, v in raw_history.items()
            }
            raw_age = data.get("coverage_history_age") or {}
            self._coverage_history_age = {
                (int(k.split(",")[0]), int(k.split(",")[1])): int(v)
                for k, v in raw_age.items()
            }
            raw_dismissed = data.get("furniture_dismissed_at") or {}
            self._furniture_dismissed_at = {
                (int(k.split(",")[0]), int(k.split(",")[1])): str(v)
                for k, v in raw_dismissed.items()
            }
            # v3.2.1 DUAL-GRID — additive field, same no-version-bump
            # rationale as the FURNITURE fields above: a payload saved
            # before this existed simply has no "structure_cells" key,
            # indistinguishable from a fresh cold start for this tracker.
            raw_structure = data.get("structure_cells") or {}
            self._structure_cells = {
                (int(k.split(",")[0]), int(k.split(",")[1])): float(v)
                for k, v in raw_structure.items()
            }
            # v3.4.0 GS-SMART-COVERAGE — additive field, same no-version-bump
            # rationale as FURNITURE/DUAL-GRID above: absent on any payload
            # saved before this existed, indistinguishable from a fresh
            # cold start (watermark 0 — every historical mission is then a
            # legitimate backfill candidate, which is the correct behaviour
            # for a robot upgrading onto this feature for the first time).
            self._last_processed_nmssn = int(data.get("last_processed_nmssn", 0) or 0)
        except (KeyError, ValueError, IndexError, TypeError, AttributeError) as exc:
            _LOGGER.warning("GridStore: failed to load — %s; starting empty", exc)
            self._cells = {}
            self._stuck = {}
            self._coverage_history = {}
            self._coverage_history_age = {}
            self._furniture_dismissed_at = {}
            self._structure_cells = {}
            self._last_processed_nmssn = 0

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
            "coverage_history": {
                f"{gx},{gy}": bm for (gx, gy), bm in self._coverage_history.items()
            },
            "coverage_history_age": {
                f"{gx},{gy}": age for (gx, gy), age in self._coverage_history_age.items()
            },
            "furniture_dismissed_at": {
                f"{gx},{gy}": ts for (gx, gy), ts in self._furniture_dismissed_at.items()
            },
            "structure_cells": {
                f"{gx},{gy}": w for (gx, gy), w in self._structure_cells.items()
            },
            "last_processed_nmssn": self._last_processed_nmssn,
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

        # v3.2.1 DUAL-GRID — same decay/prune/increment mechanics as
        # self._cells above, but always centre-only, independent of
        # robot_radius_mm. See the field docstring in __init__ for why
        # this needs to be a wholly separate accumulator, not derivable
        # from self._cells after the fact (disk-fill is lossy).
        centre_touched = {_mm_to_cell(x, y) for x, y in pose_points}
        structure_prune = [
            cell for cell, weight in self._structure_cells.items()
            if weight * DECAY < PRUNE_THRESHOLD
        ]
        for cell in structure_prune:
            del self._structure_cells[cell]
        for cell in self._structure_cells:
            self._structure_cells[cell] *= DECAY
        for cell in centre_touched:
            self._structure_cells[cell] = min(
                1.0, self._structure_cells.get(cell, 0.0) + VISIT_INCREMENT
            )

        # v3.2.0 FURNITURE — shift every tracked cell's coverage-history
        # bitmask by one mission, same "applies to every tracked cell
        # regardless of whether THIS mission went anywhere near it"
        # characteristic as the EMA decay above (step 1) — a partial-home
        # clean will shift in a 0 for untouched rooms' cells exactly like
        # it already decays their EMA score. Known trade-off: a robot
        # doing frequent single-room quick-cleans could reach the
        # "3 consecutive misses" threshold for other rooms sooner than a
        # robot doing mostly full-home cleans. Not specially guarded
        # against here, matching the existing EMA precedent.
        self._update_coverage_history(touched)

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

    @property
    def last_processed_nmssn(self) -> int:
        """Highest robot-lifetime mission counter (nMssn) already fed into
        this GridStore, from either the live or cloud-backfill path."""
        return self._last_processed_nmssn

    def record_processed_nmssn(self, nmssn: Any) -> None:
        """v3.4.0 GS-SMART-COVERAGE — advance the shared watermark.

        Called by BOTH the live path (image.py, after a real-pose
        update_from_mission() call) and the cloud-backfill path
        (callbacks.py, after a UMF-derived one) so that whichever path
        processes a given mission first "claims" it — the other path's
        candidate filter (nMssn > last_processed_nmssn) then skips it,
        preventing the same mission from being fed into the EMA/stuck
        pipeline twice.

        Monotonic: never moves backwards, even if called with an older
        value (e.g. cloud records arriving slightly out of order).
        Silently ignores None/non-numeric input — the caller may not
        always have a valid nMssn (e.g. very old firmware, or a mission
        record that failed cloud merge), and this must never raise.
        """
        if nmssn is None:
            return
        try:
            n = int(nmssn)
        except (TypeError, ValueError):
            return
        if n > self._last_processed_nmssn:
            self._last_processed_nmssn = n

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
    def structure_cells(self) -> dict[tuple[int, int], float]:
        """Read-only snapshot of the DUAL-GRID centre-only cell weights.

        v3.2.1 — data-collection scaffolding for a room-segmentation
        input candidate; not yet consumed anywhere. See the field
        docstring in __init__ for the disk-fill-vs-structure rationale.
        """
        return dict(self._structure_cells)

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

    def stuck_clusters(
        self,
        threshold: int = STUCK_HOTSPOT_THRESHOLD,
        min_cluster_size: int = 2,
    ) -> list[dict[str, Any]]:
        """v3.2.0 STUCK-HOTSPOT — group adjacent hotspot cells (8-connectivity)
        into clusters, one physical obstacle typically spanning multiple
        150mm cells.

        Reinterpreted from the original "cluster stuck-rate > 40%" spec
        wording — GridStore doesn't track missions-that-passed-nearby, so
        a genuine rate isn't computable. Two or more independently
        hotspot-qualifying adjacent cells is itself a stronger signal than
        a single outlier cell would be (physically near-impossible to be
        coincidence at 150mm cell size — that's roughly furniture-leg
        scale), so the min_cluster_size floor does real work here rather
        than being an arbitrary substitute.

        coverage_impact_pp: cluster's mean EMA coverage weight minus the
        mean EMA weight of the cells immediately surrounding it (each
        cluster cell's 8-neighbours that aren't themselves in the
        cluster) — in percentage points. A meaningfully NEGATIVE value
        (cluster covered far less than its own surroundings) is the
        honestly-computable stand-in for "coverage impact": None when
        there's no surrounding-cell data to compare against yet.

        Returns clusters sorted by total stuck count, descending. Each:
        {"cells": [(gx,gy), ...], "stuck_count": int, "x_mm": float,
         "y_mm": float, "coverage_impact_pp": float | None}
        """
        hotspot_cells = {
            cell for cell, v in self._stuck.items() if v["count"] >= threshold
        }
        if not hotspot_cells:
            return []

        visited: set[tuple[int, int]] = set()
        clusters: list[dict[str, Any]] = []

        for start in hotspot_cells:
            if start in visited:
                continue
            # Flood-fill via BFS over 8-connected hotspot neighbours
            group = {start}
            frontier = [start]
            visited.add(start)
            while frontier:
                gx, gy = frontier.pop()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        neighbour = (gx + dx, gy + dy)
                        if neighbour in hotspot_cells and neighbour not in visited:
                            visited.add(neighbour)
                            group.add(neighbour)
                            frontier.append(neighbour)

            if len(group) < min_cluster_size:
                continue

            total_stuck = sum(self._stuck[cell]["count"] for cell in group)
            xs, ys = zip(*(_cell_to_mm(*cell) for cell in group))
            centroid_x, centroid_y = sum(xs) / len(xs), sum(ys) / len(ys)

            surrounding: set[tuple[int, int]] = set()
            for gx, gy in group:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        neighbour = (gx + dx, gy + dy)
                        if neighbour not in group:
                            surrounding.add(neighbour)

            coverage_impact_pp: float | None = None
            surrounding_weights = [
                self._cells[c] for c in surrounding if c in self._cells
            ]
            if surrounding_weights:
                cluster_mean = sum(
                    self._cells.get(c, 0.0) for c in group
                ) / len(group)
                surrounding_mean = sum(surrounding_weights) / len(surrounding_weights)
                coverage_impact_pp = (cluster_mean - surrounding_mean) * 100.0

            clusters.append({
                "cells": sorted(group),
                "stuck_count": total_stuck,
                "x_mm": centroid_x,
                "y_mm": centroid_y,
                "coverage_impact_pp": coverage_impact_pp,
            })

        return sorted(clusters, key=lambda c: c["stuck_count"], reverse=True)

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

    def stuck_by_polygon(
        self,
        polygons_pose: dict[str, list[tuple[float, float]]],
    ) -> dict[str, int]:
        """v3.2.0 ROOM-ACCESS — return per-room stuck-event count from
        polygon intersection.

        Same bounding-box + ray-casting point-in-polygon approach as
        coverage_by_polygon(), applied to self._stuck instead of
        self._cells. Returns raw counts (not a rate/fraction) — the
        caller (RobotProfileStore's room-accessibility scoring) decides
        how to normalise against room size / visited-cell count; this
        method stays a plain data provider, same division of
        responsibility as stuck_event_count() (also a raw int, no scoring
        built in).

        polygons_pose: {rid: [(x_mm, y_mm), ...]} — vertices in pose space (mm).
        Returns {rid: count}. Empty dict when no stuck events recorded at all.
        """
        result: dict[str, int] = {}
        if not self._stuck:
            return result

        for rid, polygon in polygons_pose.items():
            if len(polygon) < 3:
                result[rid] = 0
                continue
            half = CELL_SIZE_MM / 2
            min_x = min(p[0] for p in polygon) - half
            max_x = max(p[0] for p in polygon) + half
            min_y = min(p[1] for p in polygon) - half
            max_y = max(p[1] for p in polygon) + half

            count = 0
            for (gx, gy), v in self._stuck.items():
                cx, cy = _cell_to_mm(gx, gy)
                if not (min_x <= cx <= max_x and min_y <= cy <= max_y):
                    continue
                if not _point_in_polygon_grid(cx, cy, polygon):
                    continue
                count += v.get("count", 0)
            result[rid] = count
        return result

    def _update_coverage_history(self, touched: set[tuple[int, int]]) -> None:
        """v3.2.0 FURNITURE — shift every tracked cell's rolling coverage
        bitmask by one mission (bit 0 = most recent), setting bit 0 when
        the cell is in `touched` this mission. Tracks every cell that
        either has existing history OR was touched this mission — bounded
        by the same visited-cell set the EMA/stuck tracking already uses,
        not the full grid.

        A cell whose bitmask decays to 0 (no history left in the tracked
        window at all) is dropped from _coverage_history entirely, same
        pruning spirit as EMA's PRUNE_THRESHOLD cleanup — keeps the dict
        from retaining cells with nothing left to say.
        """
        cells_to_update = set(self._coverage_history.keys()) | touched
        for cell in cells_to_update:
            bitmask = self._coverage_history.get(cell, 0)
            bitmask = (bitmask << 1) & _FURNITURE_HISTORY_MASK
            if cell in touched:
                bitmask |= 1
            age = self._coverage_history_age.get(cell, 0)
            # Cap the age counter — it only needs to reach
            # _FURNITURE_WINDOW_BITS to unlock "established" eligibility;
            # let it keep incrementing a little past that for headroom,
            # but there's no reason to let it grow unbounded forever.
            self._coverage_history_age[cell] = min(age + 1, _FURNITURE_WINDOW_BITS * 2)

            if bitmask == 0:
                self._coverage_history.pop(cell, None)
                self._coverage_history_age.pop(cell, None)
            else:
                self._coverage_history[cell] = bitmask

    def furniture_candidates(self) -> list[dict[str, Any]]:
        """v3.2.0 FURNITURE — cells that were reliably covered for a long
        stretch and have now been absent for _FURNITURE_RECENT_ABSENT
        consecutive missions — a candidate signature for new furniture or
        another obstacle now blocking that spot.

        Returns a list of {"cell": (gx, gy), "x_mm": float, "y_mm": float}
        dicts, one per candidate cell. Empty list when nothing qualifies
        (the common case — most cells are either still being covered, or
        never had enough history to judge either way).
        """
        results: list[dict[str, Any]] = []
        for cell, bitmask in self._coverage_history.items():
            if self._coverage_history_age.get(cell, 0) < _FURNITURE_WINDOW_BITS:
                continue  # not enough history yet to distinguish from "never tracked"
            if bitmask & _FURNITURE_RECENT_MASK != 0:
                continue  # covered at least once in the recent window — not "gone"
            established_hits = bin(bitmask & _FURNITURE_ESTABLISHED_MASK).count("1")
            if established_hits < _FURNITURE_ESTABLISHED_MIN_HITS:
                continue  # wasn't reliably covered before either — nothing changed
            x_mm, y_mm = _cell_to_mm(*cell)
            results.append({"cell": cell, "x_mm": x_mm, "y_mm": y_mm})
        return results

    def furniture_readiness(self) -> dict[str, Any]:
        """v3.2.0 UX fix — progress indicator for FURNITURE's learning
        phase, so a fresh install shows something more informative than
        a silently empty binary_sensor.*_layout_change_detected for the
        first _FURNITURE_WINDOW_BITS (23) missions, with no way to tell
        "still learning" apart from "nothing to report, everything's
        fine" (the two states currently look identical, which was
        exactly the gap this method exists to close).

        most_mature_cell_age is the highest _coverage_history_age value
        across all currently-tracked cells — a proxy for "how far along
        is the most-established part of the floor plan", since readiness
        is inherently per-cell, not a single robot-wide count the way
        L10's day-based history is.

        Returns {"cells_tracked": int, "most_mature_cell_age": int,
        "missions_until_first_ready": int}.
        """
        if not self._coverage_history_age:
            return {
                "cells_tracked": 0,
                "most_mature_cell_age": 0,
                "missions_until_first_ready": _FURNITURE_WINDOW_BITS,
            }
        most_mature = min(
            max(self._coverage_history_age.values()), _FURNITURE_WINDOW_BITS
        )
        return {
            "cells_tracked": len(self._coverage_history_age),
            "most_mature_cell_age": most_mature,
            "missions_until_first_ready": max(0, _FURNITURE_WINDOW_BITS - most_mature),
        }

    def furniture_dismiss_suppressed(
        self, cell: tuple[int, int], now_iso: str, suppress_days: int = 30,
    ) -> bool:
        """v3.2.0 FURNITURE — True if this cell's Repair Issue was
        dismissed less than `suppress_days` ago (GridStore stays
        HA-free — caller passes now_iso as a plain ISO string, same
        pattern as stuck_wh elsewhere in this file).

        Parses both timestamps as plain date-only comparisons (YYYY-MM-DD
        prefix) to avoid needing a datetime/timezone dependency in this
        module — a day's worth of imprecision at the suppression boundary
        doesn't matter for a 30-day window.
        """
        dismissed_at = self._furniture_dismissed_at.get(cell)
        if dismissed_at is None:
            return False
        try:
            from datetime import date
            d_dismissed = date.fromisoformat(dismissed_at[:10])
            d_now = date.fromisoformat(now_iso[:10])
        except ValueError:
            return False
        return (d_now - d_dismissed).days < suppress_days

    def record_furniture_dismissed(self, cell: tuple[int, int], now_iso: str) -> None:
        """Record that this cell's issue was just observed as dismissed."""
        self._furniture_dismissed_at[cell] = now_iso

    def furniture_dismissed_cells(self) -> tuple[tuple[int, int], ...]:
        """Snapshot of all cells with an active dismiss record.

        v3.3.0 STORE-ENCAP — returned as a tuple so callers can safely
        iterate while calling clear_furniture_dismissed() (repairs.py
        auto-resolve loop previously copied the private dict's keys)."""
        return tuple(self._furniture_dismissed_at.keys())

    def is_furniture_dismissed(self, cell: tuple[int, int]) -> bool:
        """True when a dismiss record exists for the cell, regardless of
        the 30-day window (window logic lives in
        furniture_dismiss_suppressed). v3.3.0 STORE-ENCAP."""
        return cell in self._furniture_dismissed_at

    def stuck_count(self, cell: tuple[int, int]) -> int:
        """Stuck-event count for a cell, 0 when unknown.

        v3.3.0 STORE-ENCAP — tolerant of the pre-v2.7.0 legacy plain-count
        format that async_load may still hold for hand-edited stores."""
        entry = self._stuck.get(cell)
        if entry is None:
            return 0
        if isinstance(entry, dict):
            try:
                return int(entry.get("count", 0))
            except (TypeError, ValueError):
                return 0
        try:
            return int(entry)
        except (TypeError, ValueError):
            return 0

    def clear_furniture_dismissed(self, cell: tuple[int, int]) -> None:
        """Clear a cell's dismiss record — called once the 30-day window
        has elapsed, allowing the issue to fire again if still a
        candidate."""
        self._furniture_dismissed_at.pop(cell, None)

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
