"""OutlineStore — accumulated room boundary for EPHEMERAL robots (F-EPHEMERAL, v2.4.0).

900-series robots have no persistent Cloud map — every mission starts fresh.
This module derives a room boundary from GridStore's accumulated visited-cell
grid and persists the result for restart-continuity.

Gate: EPHEMERAL robots only. SMART robots use UmfAligner room polygons.

v3.2.1 REDESIGN — replaced the original per-mission PNG edge-detection +
EMA-blend pipeline (extract_contour_from_png / _merge_contours, v2.4.0–v3.2.0)
with direct boundary-cell extraction from GridStore.cells. Two field-confirmed
bugs drove this:

  1. OFFSET — contour pixels were extracted from a render in a FIXED
     identity px space (render_for_outline(), v2.8.2) but composited onto
     an auto-fitted live-map render at display time. On a real 980 OG this
     displaced the entire outline ~2.4 m from the cleaning path.
  2. CLIPPING — the fixed 600x600px / 10mm-per-px render canvas is a hard
     6x6m window around the dock. On the same OG, 57% of the house's known
     GridStore footprint (1603/2798 cells) fell outside that window and
     could never appear in the outline, however many missions accumulated.

GridStore.cells is already unbounded (a plain dict, no canvas), already
accumulates across missions, and is already the input room_segmentation.py
uses for EPHEMERAL room detection — deriving the outline from the same
source means it can never clip and is trivially consistent with whatever
the house's explored footprint actually is. The derivation is also now a
deterministic pure function of the CURRENT grid rather than an EMA blend of
noisy per-mission PNG edges, so there is no accumulated-artifact drift to
correct for: every recompute reflects exactly what GridStore currently
holds, no more and no less.

Design:
  - compute_boundary_points_mm() runs synchronously (Python dict, no PIL) —
    called at mission end from image.py, AFTER GridStore.update_from_mission()
    so the just-finished mission's cells are already included.
  - Storage key: roomba_plus_outline_{entry_id}.
  - ready property: mission_count >= MIN_MISSIONS_TO_SHOW.
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX   = "roomba_plus_outline"

# v2.8.3 CRITICAL FIX — these are two genuinely different things and must
# never be conflated again:
#
#   _HA_STORE_VERSION  — passed to homeassistant.helpers.storage.Store()'s
#                         constructor. This is HA's OWN file-format version.
#                         If it doesn't match what's already on disk, HA's
#                         Store calls self._async_migrate_func(old_version,
#                         old_minor_version, old_data) BEFORE we ever see the
#                         data — and the base Store class's default
#                         _async_migrate_func simply `raise NotImplementedError`.
#                         We never implemented one. Bumping this constant
#                         crashed async_setup_entry outright for every
#                         existing installation (confirmed in the field,
#                         v2.8.2). This value must stay 1 forever unless a
#                         real _async_migrate_func is written.
#
#   PAYLOAD_VERSION    — our OWN application-level marker, embedded inside
#                         the dict we hand to store.async_save()/get back
#                         from store.async_load(). Checking this ourselves
#                         in async_load() (see below) is exactly the right
#                         way to discard an old, incompatible payload shape —
#                         it just must never be passed to Store()'s own
#                         constructor, which is what went wrong here.
#
# v2.8.2 bumped what was then a single combined constant 1 -> 2 specifically
# to discard contour_points accumulated from incompatible per-mission
# auto-fit renders.
#
# v2.9.0 bumped PAYLOAD_VERSION again, 2 -> 3: pose.point.x/y were confirmed
# 10x too small everywhere (cm reported, treated as mm). Every accumulated
# contour_points entry was extracted from PNGs rendered at the wrong scale.
#
# v3.2.1 bumps PAYLOAD_VERSION a third time, 3 -> 4, for the redesign above:
# contour_points changes MEANING from a list of (int, int) PIXELS in a fixed
# 600x600 render canvas to a list of (float, float) real-world MILLIMETRES
# (GridStore boundary-cell centres). Old pixel-space contours would silently
# render as if they were 10mm-per-unit mm coordinates — a 600px-wide contour
# would claim to span 6 metres correctly by coincidence (the old canvas WAS
# 10mm/px), but every point would be individually wrong (window-clipped,
# offset, EMA-blurred). Must be discarded, not reinterpreted.
_HA_STORE_VERSION    = 1
PAYLOAD_VERSION      = 4

# Don't show outline until we have at least this many missions
MIN_MISSIONS_TO_SHOW = 2
# Minimum boundary points needed for a meaningful outline. v3.2.1 — this
# used to gate PIXEL count on a dense edge-detected image (hundreds to
# thousands typical); it now gates boundary-CELL count, which is far
# sparser (a modest single floor's perimeter is commonly 60-150 cells at
# 150mm/cell) but structurally one point per cell of actual open perimeter,
# not per rendered pixel — 50 remains a reasonable "not just a doorway
# sliver" floor for either metric.
MIN_CONTOUR_POINTS   = 50

# Cell size must match GridStore.CELL_MM — kept as a separate constant
# rather than importing GridStore here, following the CELL_MM precedent in
# room_seg_store.py (avoids a store-to-store import for one float).
CELL_MM = 150.0


def compute_boundary_points_mm(
    cells: dict[tuple[int, int], Any],
    cell_mm: float = CELL_MM,
) -> list[tuple[float, float]]:
    """Derive room-boundary points from GridStore's visited-cell grid.

    A visited cell is a BOUNDARY cell if at least one of its 4 orthogonal
    neighbours is NOT visited (4-connectivity, not 8: an 8-connectivity
    check would miss thin single-cell-wide corridor walls where only the
    orthogonal neighbour, not the diagonal, is unvisited — under-counting
    exactly the narrow-passage boundaries a doorway/corridor outline most
    needs to show).

    Returns each boundary cell's CENTRE in real-world dock-relative
    millimetres — (gx*cell_mm + cell_mm/2, gy*cell_mm + cell_mm/2) — ready
    for MapRenderer._mm_to_px_fit(). Deliberately NOT a traced polygon:
    a scattered point cloud composites identically to the pixel-dot
    approach the renderer already used for the old PNG-extracted contour,
    with none of the ordering/winding complexity a real polygon trace
    would add, for a diagnostic overlay that only needs to suggest wall
    position, not render a filled shape.

    Pure function, no I/O — safe to call synchronously from the event loop
    even for a few thousand cells (single dict pass + neighbour lookups).
    """
    if not cells:
        return []
    boundary: list[tuple[float, float]] = []
    for (gx, gy) in cells:
        if (
            (gx + 1, gy) not in cells
            or (gx - 1, gy) not in cells
            or (gx, gy + 1) not in cells
            or (gx, gy - 1) not in cells
        ):
            boundary.append((gx * cell_mm + cell_mm / 2, gy * cell_mm + cell_mm / 2))
    return boundary


class OutlineStore:
    """Accumulates room boundary outline from GridStore's visited-cell grid.

    Lifecycle:
      - Instantiate in async_setup_entry for EPHEMERAL robots with map_enabled.
      - Call async_load() immediately after.
      - Store in entry.runtime_data.outline_store.
      - Call async_recompute() at mission end (image.py _handle_mission_end),
        AFTER GridStore.update_from_mission() so the just-finished mission's
        cells are included.
      - render_room_outline() reads contour_points (now real-world mm) from
        this store.
    """

    def __init__(self) -> None:
        self._mission_count: int = 0
        self._contour_points: list[tuple[float, float]] = []
        # P2: Store is stateless — construct once and reuse across load/save calls
        self._store: Any = None

    def _get_store(self, hass: Any, entry_id: str) -> Any:
        """Return the cached Store, creating it on first call."""
        if self._store is None:
            from homeassistant.helpers.storage import Store
            self._store = Store(
                hass,
                _HA_STORE_VERSION,
                f"{STORAGE_KEY_PREFIX}_{entry_id}",
                private=True,
            )
        return self._store

    # ── Persistence ────────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        """Load persisted outline state from hass.storage."""
        store = self._get_store(hass, entry_id)
        data = await store.async_load()
        if data and isinstance(data, dict) and data.get("version") == PAYLOAD_VERSION:
            try:
                self._mission_count = int(data.get("mission_count") or 0)
                raw = data.get("contour_points") or []
                self._contour_points = [
                    (float(p[0]), float(p[1]))
                    for p in raw
                    if isinstance(p, (list, tuple)) and len(p) == 2
                ]
            except (TypeError, ValueError, KeyError, AttributeError, IndexError) as exc:
                _LOGGER.warning(
                    "OutlineStore: failed to load for %s — %s; starting empty",
                    entry_id, exc,
                )
                self._mission_count = 0
                self._contour_points = []
        _LOGGER.debug(
            "OutlineStore: loaded mission_count=%d contour_points=%d",
            self._mission_count, len(self._contour_points),
        )

    async def async_save(self, hass: Any, entry_id: str) -> None:
        """Persist outline state to hass.storage."""
        store = self._get_store(hass, entry_id)
        await store.async_save({
            "version": PAYLOAD_VERSION,
            "mission_count": self._mission_count,
            "contour_points": list(self._contour_points),
        })

    # ── Update ─────────────────────────────────────────────────────────────────

    def recompute_sync(self, cells: dict[tuple[int, int], Any]) -> None:
        """Pure, synchronous half of async_recompute — updates
        self._contour_points/_mission_count immediately, no I/O.

        v3.2.1 FIELD FIX — extracted so a caller needing the FRESH
        contour right away (e.g. FreezeSnapshotStore, which must not
        read a stale/previous-mission contour) can call this directly
        before scheduling persistence, instead of only being able to
        reach the up-to-date value after an async_recompute() coroutine
        — scheduled via run_coroutine_threadsafe from a sync callback
        thread — has actually finished running. Confirmed in the field:
        the very first FreezeSnapshotStore snapshot captured
        outline_points=0 because it read contour_points before this
        mission's async_recompute had executed at all (fires on the
        first-ever recompute, due() being unconditionally True with no
        prior snapshot — and the outline recompute call in image.py
        sits in a separate, LATER code block).

        compute_boundary_points_mm() is cheap pure Python (confirmed:
        a single dict pass over a few thousand cells) — safe to call
        twice per mission (once here, once inside async_recompute for
        persistence) rather than restructure the async ordering itself.
        """
        if not cells:
            return
        try:
            self._contour_points = compute_boundary_points_mm(cells)
            self._mission_count += 1
        except Exception:  # noqa: BLE001
            _LOGGER.exception("OutlineStore: unexpected error in recompute_sync")

    async def async_recompute(
        self,
        cells: dict[tuple[int, int], Any],
        hass: Any,
        entry_id: str,
    ) -> None:
        """Recompute the boundary from GridStore's current cell grid and persist.

        v3.2.1 — replaces async_update_from_png(). Deterministic pure
        recompute (no EMA merge): GridStore.cells already accumulates
        every mission's visited cells with no window and no decay, so the
        boundary derived from it at any point in time already reflects
        the full accumulated history — blending against a prior contour
        would only reintroduce the artifacts (offset, clipping) this
        redesign removes.

        Recomputes via recompute_sync() again even if a caller already
        called it this mission (see that method's docstring) — cheap,
        and keeps this method's own behaviour/contract unchanged for
        every existing caller.
        """
        if not cells:
            return
        try:
            self.recompute_sync(cells)
            await self.async_save(hass, entry_id)
            _LOGGER.debug(
                "OutlineStore: recomputed mission_count=%d contour_points=%d",
                self._mission_count, len(self._contour_points),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("OutlineStore: unexpected error in async_recompute")

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """True when enough missions have accumulated to show the outline."""
        return (
            self._mission_count >= MIN_MISSIONS_TO_SHOW
            and len(self._contour_points) >= MIN_CONTOUR_POINTS
        )

    @property
    def mission_count(self) -> int:
        """Number of missions that have contributed to the outline."""
        return self._mission_count

    @property
    def contour_points(self) -> list[tuple[float, float]]:
        """Accumulated room boundary as (x_mm, y_mm) dock-relative points."""
        return self._contour_points

    @property
    def contour_point_count(self) -> int:
        """Number of contour points (for diagnostics)."""
        return len(self._contour_points)
