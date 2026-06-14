"""UMF spatial fusion aligner — Roomba+ v2.3.0 F8.

Aligns iRobot UMF floor plan coordinate space with pose-space (dock-relative mm)
using door-gap detection and Hungarian assignment against GeometryStore markers.

Coordinate system (Q6 resolved June 2026): UMF points2d values are in METRES.
All coordinates are multiplied by UMF_TO_MM = 1000.0 in _build_coord_lookup()
so that internal calculations and thresholds remain in mm throughout.
"""

from __future__ import annotations

import io
import logging
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .geometry_store import DoorMarker, GeometryStore

_LOGGER = logging.getLogger(__name__)

# ── Coordinate scale ──────────────────────────────────────────────────────────
# UMF points2d are in metres (Q6 resolved June 2026 from gap stats:
# max gap ~11.5 in raw UMF = ~11.5m, consistent with house dimensions).
# Applied once in _build_coord_lookup(); all thresholds below are in mm.
UMF_TO_MM: float = 1000.0

# ── Confidence thresholds (all in mm after UMF_TO_MM scaling) ─────────────────
_MIN_CONFIDENCE:          float = 0.70   # below: no room coverage, no auto-naming
_AUTO_CONFIRM_CONFIDENCE: float = 0.85   # at or above: auto-confirm zone names
_RESIDUAL_SCALE:          float = 500.0  # mm — residual > this → confidence ~0
_DOOR_MATCH_TOLERANCE:    float = 400.0  # mm — max UMF↔GS marker distance for pair
_DOOR_GAP_MIN:            float = 600.0  # mm — minimum door width
_DOOR_GAP_MAX:            float = 1200.0 # mm — maximum door width


# ── Public API ────────────────────────────────────────────────────────────────

class UmfAligner:
    """Align UMF floor plan space with pose space via door-gap matching.

    Usage::

        aligner = UmfAligner(points2d, regions, geometry_store, pmap_version_id)
        confidence = aligner.align()   # CPU-bound — call via async_add_executor_job
        if aligner.aligned:
            x_pose, y_pose = aligner.umf_to_pose(x_umf, y_umf)
    """

    def __init__(
        self,
        points2d: list[dict[str, Any]],
        regions: list[dict[str, Any]],
        geometry_store: GeometryStore,
        pmap_version_id: str = "",
    ) -> None:
        self._points2d      = points2d
        self._regions       = regions
        self._geometry_store = geometry_store
        self.pmap_version_id = pmap_version_id

        self._coord_lookup:   dict[str, tuple[float, float]] = {}
        self._room_polygons:  dict[str, list[tuple[float, float]]] = {}
        self._door_candidates: list[tuple[float, float]] = []
        self._transform:      tuple[float, float, float] | None = None  # (rot_rad, tx, ty)
        self._confidence:     float = 0.0
        self._aligned:        bool  = False
        # GS-SMART-UMF (v2.7.0): synthetic DoorMarker objects seeded from cloud
        # traversal data when local pose is unavailable (lewis firmware).
        # Set via set_bootstrap_markers(); used by align() when GS is empty.
        self._bootstrap_markers: list = []

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def aligned(self) -> bool:
        """True when confidence >= _MIN_CONFIDENCE after align()."""
        return self._aligned

    @property
    def confidence(self) -> float:
        """Alignment confidence score 0.0–1.0."""
        return self._confidence

    def set_bootstrap_markers(
        self, umf_positions: list[tuple[float, float]]
    ) -> None:
        """GS-SMART-UMF (v2.7.0) — seed synthetic markers from UMF-space positions.

        Used when local pose data is unavailable (lewis firmware 22.52.10+).
        Each position is a UMF-space (x_mm, y_mm) door location derived from
        cloud traversal events confirming the robot crossed known room boundaries.

        Requires ≥2 positions. The synthetic DoorMarkers have mission_count=3
        so they pass the ``mission_count >= 2`` filter in align().
        When these markers are set and GeometryStore is empty, align() performs
        UMF-space alignment (identity transform: UMF coords == pose coords).
        """
        if len(umf_positions) < 2:
            _LOGGER.debug(
                "UmfAligner.set_bootstrap_markers: need ≥2 positions, got %d — ignored",
                len(umf_positions),
            )
            return
        from .geometry_store import DoorMarker
        self._bootstrap_markers = [
            DoorMarker(id=f"bootstrap_{i}", cx=x, cy=y, mission_count=3)
            for i, (x, y) in enumerate(umf_positions)
        ]
        _LOGGER.info(
            "UmfAligner: %d bootstrap marker(s) set for pose-free alignment",
            len(self._bootstrap_markers),
        )

    @property
    def room_polygons_umf(self) -> dict[str, list[tuple[float, float]]]:
        """Per-room polygon vertices in UMF space. Empty until align() called."""
        return dict(self._room_polygons)

    # ── Public methods ────────────────────────────────────────────────────────

    def align(self) -> float:
        """Run full alignment pipeline. Returns confidence score 0.0–1.0.

        CPU-bound — call via hass.async_add_executor_job().

        Steps:
        1. Build coord lookup from points2d.
        2. Detect door-width gaps in ordered points2d sequence.
        3. Resolve room and keepout polygons.
        4. Collect GeometryStore door markers with mission_count >= 2.
        5. Hungarian assignment (>= 2 pairs required).
        6. Estimate rigid body transform (rotation + translation).
        7. Validate via room centroid residuals.
        """
        self._build_coord_lookup()
        self._detect_door_gaps()
        self._resolve_room_polygons()

        gs_markers = [
            m for m in self._geometry_store.door_markers
            if m.mission_count >= 2
        ]
        # GS-SMART-UMF (v2.7.0): fall back to bootstrap markers when GeometryStore
        # is empty — happens on lewis firmware where local MQTT pose data is absent.
        if not gs_markers and self._bootstrap_markers:
            gs_markers = self._bootstrap_markers
            _LOGGER.debug(
                "UmfAligner: using %d bootstrap marker(s) (no local GS data)",
                len(gs_markers),
            )
        if len(gs_markers) < 2 or len(self._door_candidates) < 2:
            _LOGGER.debug(
                "UmfAligner: insufficient data — %d GS markers, %d door candidates",
                len(gs_markers), len(self._door_candidates),
            )
            self._confidence = 0.0
            self._aligned    = False
            return 0.0

        pairs = self._hungarian_match(self._door_candidates, gs_markers)
        if len(pairs) < 2:
            _LOGGER.debug("UmfAligner: Hungarian match yielded < 2 pairs")
            self._confidence = 0.0
            self._aligned    = False
            return 0.0

        self._transform  = self._estimate_rigid_body(pairs)
        residual         = self._validate_transform(gs_markers)
        self._confidence = max(0.0, 1.0 - residual / _RESIDUAL_SCALE)
        self._aligned    = self._confidence >= _MIN_CONFIDENCE
        _LOGGER.info(
            "UmfAligner: confidence=%.2f aligned=%s residual=%.1f pairs=%d rooms=%d",
            self._confidence, self._aligned, residual, len(pairs),
            len(self._room_polygons),
        )
        return self._confidence

    def umf_to_pose(self, x_umf: float, y_umf: float) -> tuple[float, float] | None:
        """Transform UMF-space coordinates to pose-space (dock-relative mm).

        Returns None when not aligned.
        """
        if not self._aligned or self._transform is None:
            return None
        rot, tx, ty = self._transform
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        return (
            cos_r * x_umf - sin_r * y_umf + tx,
            sin_r * x_umf + cos_r * y_umf + ty,
        )

    def pose_to_umf(self, x_pose: float, y_pose: float) -> tuple[float, float] | None:
        """Inverse transform: pose-space mm → UMF-space.

        Returns None when not aligned.
        """
        if not self._aligned or self._transform is None:
            return None
        rot, tx, ty = self._transform
        dx, dy      = x_pose - tx, y_pose - ty
        cos_r, sin_r = math.cos(-rot), math.sin(-rot)
        return (
            cos_r * dx - sin_r * dy,
            sin_r * dx + cos_r * dy,
        )

    def room_name_at(self, x_umf: float, y_umf: float) -> str | None:
        """Return room name for a UMF-space point, or None if outside all rooms."""
        rid_map = self.rid_to_name()
        for rid, poly in self._room_polygons.items():
            if _point_in_polygon(x_umf, y_umf, poly):
                return rid_map.get(rid, rid)
        return None

    def rid_to_name(self) -> dict[str, str]:
        """Return {rid: name} from regions — used by EPHEMERAL CR4 path."""
        return {
            r["id"]: r.get("name", r["id"])
            for r in self._regions
            if r.get("id")
        }

    def keepout_polygon_umf(
        self, zone: dict[str, Any]
    ) -> list[tuple[float, float]] | None:
        """Resolve keepout zone geometry to polygon vertices in UMF space.

        Returns None when zone has no resolvable geometry (< 3 vertices).
        """
        geometry  = zone.get("geometry", {})
        ids_lists = geometry.get("ids", [])
        if not ids_lists:
            return None
        exterior_ids = ids_lists[0]
        vertices = [
            self._coord_lookup[pid]
            for pid in exterior_ids
            if pid in self._coord_lookup
        ]
        return vertices if len(vertices) >= 3 else None

    def calibration_points(
        self,
        mm_to_px_fn: Any,
    ) -> list[dict[str, Any]] | None:
        """Return 3 calibration point pairs for xiaomi-vacuum-map-card.

        Format: [{"vacuum": {"x": mm, "y": mm}, "map": {"x": px, "y": px}}, ...]

        Uses dock origin (0, 0) + two bounding-box extremes in pose space.
        mm_to_px_fn: callable(x_mm, y_mm) -> (px_x, px_y)

        Returns None when not aligned or no room polygons.
        """
        if not self._aligned or not self._room_polygons:
            return None

        all_pose: list[tuple[float, float]] = []
        for poly_umf in self._room_polygons.values():
            for pt in poly_umf:
                p = self.umf_to_pose(*pt)
                if p:
                    all_pose.append(p)
        if not all_pose:
            return None

        xs = [p[0] for p in all_pose]
        ys = [p[1] for p in all_pose]
        anchors_mm: list[tuple[float, float]] = [
            (0.0,    0.0),
            (min(xs), min(ys)),
            (max(xs), max(ys)),
        ]
        result = []
        for x_mm, y_mm in anchors_mm:
            px_x, px_y = mm_to_px_fn(x_mm, y_mm)
            result.append({
                "vacuum": {"x": x_mm, "y": y_mm},
                "map":    {"x": px_x,  "y": px_y},
            })
        return result

    # ── Private: build lookups ────────────────────────────────────────────────

    def _build_coord_lookup(self) -> None:
        """Build point_id → (x_mm, y_mm) lookup from points2d.

        UMF coordinates are in metres — multiply by UMF_TO_MM so all internal
        calculations and thresholds operate in mm throughout.
        """
        self._coord_lookup = {}
        for p in self._points2d:
            pid    = p.get("id")
            coords = p.get("coordinates", [])
            if pid and len(coords) >= 2:
                try:
                    self._coord_lookup[pid] = (
                        float(coords[0]) * UMF_TO_MM,
                        float(coords[1]) * UMF_TO_MM,
                    )
                except (TypeError, ValueError):
                    continue

    def _detect_door_gaps(self) -> None:
        """Detect door-width gaps in the ordered points2d sequence.

        Consecutive points whose Euclidean distance falls in
        [_DOOR_GAP_MIN, _DOOR_GAP_MAX] are door candidates.
        The midpoint of each gap is stored for Hungarian matching.

        NOTE: thresholds assume Q6 = mm. If Q6 resolves to metres,
        update _DOOR_GAP_MIN/_DOOR_GAP_MAX only — not this method.
        """
        self._door_candidates = []
        gaps: list[float] = []
        for i in range(len(self._points2d) - 1):
            c1 = self._coord_lookup.get(self._points2d[i].get("id", ""))
            c2 = self._coord_lookup.get(self._points2d[i + 1].get("id", ""))
            if c1 and c2:
                gap = math.dist(c1, c2)
                gaps.append(gap)
                if _DOOR_GAP_MIN <= gap <= _DOOR_GAP_MAX:
                    mid = ((c1[0] + c2[0]) / 2.0, (c1[1] + c2[1]) / 2.0)
                    self._door_candidates.append(mid)
        if gaps:
            _LOGGER.debug(
                "UmfAligner: gap stats — min=%.3f max=%.3f mean=%.3f "
                "candidates=%d (thresholds: %.0f–%.0f)",
                min(gaps), max(gaps), sum(gaps) / len(gaps),
                len(self._door_candidates),
                _DOOR_GAP_MIN, _DOOR_GAP_MAX,
            )

    def _resolve_room_polygons(self) -> None:
        """Resolve per-room polygon vertices using region geometry.ids.

        geometry.ids is a list of lists of string IDs referencing points2d.
        First sublist = exterior boundary. Rooms with < 3 vertices are skipped.
        """
        self._room_polygons = {}
        for region in self._regions:
            rid       = region.get("id")
            geometry  = region.get("geometry", {})
            ids_lists = geometry.get("ids", [])
            if not rid or not ids_lists:
                continue
            exterior_ids = ids_lists[0]
            vertices = [
                self._coord_lookup[pid]
                for pid in exterior_ids
                if pid in self._coord_lookup
            ]
            if len(vertices) >= 3:
                self._room_polygons[rid] = vertices

    # ── Private: matching and transform ──────────────────────────────────────

    def _hungarian_match(
        self,
        candidates: list[tuple[float, float]],
        markers: list[DoorMarker],
    ) -> list[tuple[tuple[float, float], DoorMarker]]:
        """Greedy minimum-distance matching between door candidates and GS markers.

        Pure greedy (not true Hungarian) — sufficient for the small N typical
        of residential floor plans (≤ 20 doors). Each candidate and each marker
        is used at most once. Pairs with distance > _DOOR_MATCH_TOLERANCE dropped.
        """
        remaining_markers = list(markers)
        pairs: list[tuple[tuple[float, float], DoorMarker]] = []

        for cand in candidates:
            if not remaining_markers:
                break
            # Find closest remaining marker
            best_marker = min(
                remaining_markers,
                key=lambda m: math.dist(cand, (m.cx, m.cy)),
            )
            dist = math.dist(cand, (best_marker.cx, best_marker.cy))
            if dist <= _DOOR_MATCH_TOLERANCE:
                pairs.append((cand, best_marker))
                remaining_markers.remove(best_marker)

        return pairs

    def _estimate_rigid_body(
        self,
        pairs: list[tuple[tuple[float, float], DoorMarker]],
    ) -> tuple[float, float, float]:
        """Estimate rotation + translation from matched point pairs (SVD-free).

        Uses the closed-form solution for 2D rigid body from N >= 2 correspondences.
        Returns (rotation_rad, tx, ty).

        Source pairs: (umf_point, pose_point via DoorMarker.cx/cy).
        """
        n = len(pairs)
        # Centroids
        cx_umf  = sum(c[0] for c, _ in pairs) / n
        cy_umf  = sum(c[1] for c, _ in pairs) / n
        cx_pose = sum(m.cx  for _, m in pairs) / n
        cy_pose = sum(m.cy  for _, m in pairs) / n

        # Cross-covariance
        sxx = sxy = syx = syy = 0.0
        for (ux, uy), m in pairs:
            dux, duy = ux - cx_umf, uy - cy_umf
            dpx, dpy = m.cx - cx_pose, m.cy - cy_pose
            sxx += dux * dpx
            sxy += dux * dpy
            syx += duy * dpx
            syy += duy * dpy

        rot = math.atan2(sxy - syx, sxx + syy)
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        tx = cx_pose - (cos_r * cx_umf - sin_r * cy_umf)
        ty = cy_pose - (sin_r * cx_umf + cos_r * cy_umf)
        return (rot, tx, ty)

    def _validate_transform(self, gs_markers: list | None = None) -> float:
        """Validate transform by measuring how well door candidates align with GS markers.

        Transforms each UMF door candidate to pose space using the just-estimated
        transform and measures mean distance to the nearest GS marker. A small
        residual means the UMF→pose alignment is accurate.

        Returns mean residual error in mm. Returns 0.0 when inputs are absent
        (caller interprets 0.0 as perfect alignment → confidence=1.0).

        NOTE: applies self._transform directly (bypass umf_to_pose which requires
        self._aligned=True, not yet set when this is called from align()).
        Previous implementation (v2.6.3) compared room centroids to GS markers
        which is geometrically incorrect — centroids are in the middle of rooms,
        far from door markers — so all residuals were unreasonably large.

        GS-SMART-UMF (v2.7.0): accepts optional gs_markers parameter so bootstrap
        markers (UMF-space) can be passed directly from align() without re-reading
        from GeometryStore (which may be empty on lewis firmware).
        """
        if gs_markers is None:
            gs_markers = self._geometry_store.door_markers
        if self._transform is None or not self._door_candidates:
            return 0.0
        if not gs_markers:
            return _RESIDUAL_SCALE / 2  # moderate penalty when no markers

        rot, tx, ty = self._transform
        cos_r, sin_r = math.cos(rot), math.sin(rot)

        residuals: list[float] = []
        for cx_umf, cy_umf in self._door_candidates:
            # Apply transform directly — same math as umf_to_pose without _aligned gate
            x_pose = cos_r * cx_umf - sin_r * cy_umf + tx
            y_pose = sin_r * cx_umf + cos_r * cy_umf + ty
            nearest = min(
                math.dist((x_pose, y_pose), (m.cx, m.cy))
                for m in gs_markers
            )
            residuals.append(nearest)

        return sum(residuals) / len(residuals) if residuals else 0.0


# ── Module-level utilities ────────────────────────────────────────────────────

def _point_in_polygon(
    x: float, y: float, polygon: list[tuple[float, float]]
) -> bool:
    """Ray-casting point-in-polygon test.

    Returns True when (x, y) is inside the polygon.
    No external geometry library required.
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
