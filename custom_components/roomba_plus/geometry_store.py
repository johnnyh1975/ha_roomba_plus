"""Geometry Store for Roomba+ — door-crossing markers and user-authored room geometry.

Only used for EPHEMERAL map capability (900-series robots like the Roomba 980).

Design principles
-----------------
* The inference engine produces only what trajectory data reliably supports:
    - DoorMarker: gap-crossing midpoint clusters across missions.
    - Zone outline suggestions: room bounding boxes from RoomSegStore,
      expanded by wall_offset_mm — offered to the editor as starting
      shapes, never stored as walls themselves.
* User-authored geometry (UserWall, UserDoor, UserObstacle) is the only
  authoritative source. Once written via apply_user_edit(), inference never
  overwrites it.
* Drift is tracked via a bounded recent_drifts_mm window (v3.1.0 DRIFT-AUTO).
    When the window's mean crosses _RECENT_DRIFT_THRESHOLD_MM (>= _DRIFT_MIN_SAMPLES
    samples) a Repair Issue fires; the caller (image.py) handles issue
    creation. The issue self-clears via drift_recovered() once the mean
    drops back under the hysteresis recovery threshold — no manual
    reconfirm required, though reset_drift() remains available for that.
    cumulative_drift_mm is retained as a lifetime diagnostics value only.

Storage key: roomba_plus_geometry_{entry_id}
Storage version: 1
"""
from __future__ import annotations

import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_geometry"

# v2.9.0 CRITICAL FIX — this module was using a single STORAGE_VERSION
# constant for BOTH homeassistant.helpers.storage.Store()'s own version
# argument AND as its own application-level payload marker (checked via
# data.get("version", 0) != STORAGE_VERSION below). This is the exact
# dual-purpose pattern that caused the v2.8.2 production crash in
# outline_store.py: bumping the combined constant to discard an
# incompatible payload also changes what Store() expects on disk, and the
# base Store class's default _async_migrate_func is `raise
# NotImplementedError` — crashing async_setup_entry for every existing
# installation. This module never actually NEEDED to bump its version
# before now, so the bug was latent rather than triggered. It is needed
# now: door markers/walls/obstacles were computed from pose coordinates
# 10x too small (POSE_POINT_CM_TO_MM fix, const.py) and must be discarded,
# not silently kept around as spatially wrong geometry. Split exactly per
# the outline_store.py precedent — _HA_STORE_VERSION pinned forever,
# PAYLOAD_VERSION free to bump.
_HA_STORE_VERSION  = 1
PAYLOAD_VERSION    = 2

# ── Clustering ────────────────────────────────────────────────────────────────
DOOR_CLUSTER_TOL_MM = 400   # two midpoints within 400 mm → same crossing

# ── Suggestion expansion ──────────────────────────────────────────────────────
DEFAULT_WALL_OFFSET_MM = 200

# ── Drift ─────────────────────────────────────────────────────────────────────
DEFAULT_DRIFT_THRESHOLD_MM = 300.0

# v3.1.0 DRIFT-AUTO — gleitendes Fenster statt monotoner Lebenszeit-Summe.
# cumulative_drift_mm wächst zwangsläufig über jeden Schwellwert nach genug
# normalen Missionen (50-100mm Pro-Mission-Drift ist bei VSLAM erwartbar) —
# das Repair Issue feuerte dauerhaft als false-positive bei jedem
# EPHEMERAL-Roboter mit ausreichend Historie. Das Fenster-Mittel der letzten
# N Missionen ist die aussagekräftige Metrik ("driftet JETZT", nicht "hat
# jemals gedriftet"). cumulative_drift_mm bleibt als Diagnostics-Wert.
_DRIFT_WINDOW                = 10
_DRIFT_MIN_SAMPLES           = 3
_RECENT_DRIFT_THRESHOLD_MM   = 250.0
_RECENT_DRIFT_RECOVERY_MM    = 150.0   # Hysterese: ~60% des Trigger-Schwellwerts

# ── Observation history cap ───────────────────────────────────────────────────
MAX_MARKER_OBSERVATIONS = 20


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DoorMarker:
    """Inferred door-crossing position accumulated across missions.

    Never written by the user editor. Promoted to UserDoor by the Options
    Flow step when the user confirms a crossing as a real door.
    """

    id: str
    cx: float
    cy: float
    label: str = ""
    mission_count: int = 0
    observations: list[list[float]] = field(default_factory=list)
    # observations stored as [[x, y], ...] — plain lists for JSON round-trip.

    def update(self, cx: float, cy: float) -> None:
        """Add an observation and recompute median centroid."""
        self.observations.append([cx, cy])
        if len(self.observations) > MAX_MARKER_OBSERVATIONS:
            self.observations = self.observations[-MAX_MARKER_OBSERVATIONS:]
        self.cx = statistics.median(p[0] for p in self.observations)
        self.cy = statistics.median(p[1] for p in self.observations)
        self.mission_count = len(self.observations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cx": self.cx,
            "cy": self.cy,
            "label": self.label,
            "mission_count": self.mission_count,
            "observations": self.observations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DoorMarker:
        return cls(
            id=d["id"],
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            label=d.get("label", ""),
            mission_count=int(d.get("mission_count", 0)),
            observations=[[float(p[0]), float(p[1])] for p in d.get("observations", [])],
        )


@dataclass
class UserWall:
    """User-authored wall segment, stored in mm dock-relative coordinates."""

    id: str
    x1: float
    y1: float
    x2: float
    y2: float
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x1": self.x1, "y1": self.y1,
            "x2": self.x2, "y2": self.y2,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserWall:
        return cls(
            id=d["id"],
            x1=float(d["x1"]), y1=float(d["y1"]),
            x2=float(d["x2"]), y2=float(d["y2"]),
            label=d.get("label", ""),
        )


@dataclass
class UserDoor:
    """User-authored (or inference-promoted) door, stored in mm dock-relative."""

    id: str
    cx: float
    cy: float
    width_mm: float
    theta_deg: float
    label: str = ""
    from_inference: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cx": self.cx, "cy": self.cy,
            "width_mm": self.width_mm,
            "theta_deg": self.theta_deg,
            "label": self.label,
            "from_inference": self.from_inference,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserDoor:
        return cls(
            id=d["id"],
            cx=float(d["cx"]), cy=float(d["cy"]),
            width_mm=float(d["width_mm"]),
            theta_deg=float(d["theta_deg"]),
            label=d.get("label", ""),
            from_inference=bool(d.get("from_inference", False)),
        )


@dataclass
class UserObstacle:
    """User-authored blocking area (furniture, no-go zone), stored in mm."""

    id: str
    x: float
    y: float
    w: float
    h: float
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": self.x, "y": self.y,
            "w": self.w, "h": self.h,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserObstacle:
        return cls(
            id=d["id"],
            x=float(d["x"]), y=float(d["y"]),
            w=float(d["w"]), h=float(d["h"]),
            label=d.get("label", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
# GeometryStore
# ─────────────────────────────────────────────────────────────────────────────

class GeometryStore:
    """Manages door-crossing markers (inference) and user-authored geometry.

    Lifecycle::

        store = GeometryStore()
        await store.async_load(hass, entry_id)

        # After each mission (called from image.py _handle_mission_end):
        store.update_from_room_seg_store(room_seg_store)
        exceeded = store.record_drift(dx, dy)   # from image.py's _check_dock_drift()
        await store.async_save(hass, entry_id)

        # From Options Flow / service handler:
        store.apply_user_edit(payload_dict)
        store.reset_drift()
        await store.async_save(hass, entry_id)
    """

    def __init__(self) -> None:
        self.door_markers: list[DoorMarker] = []
        self.walls: list[UserWall] = []
        self.doors: list[UserDoor] = []
        self.obstacles: list[UserObstacle] = []
        self.zone_labels: dict[str, str] = {}
        self.wall_offset_mm: int = DEFAULT_WALL_OFFSET_MM
        self.cumulative_drift_mm: float = 0.0
        # v3.1.0 DRIFT-AUTO — bounded, most-recent-last window of per-mission
        # drift magnitudes. Drives the Repair Issue decision; cumulative_drift_mm
        # is retained purely as a lifetime diagnostics value.
        self.recent_drifts_mm: list[float] = []
        self._next_marker_id: int = 1

    # ── Persistence ───────────────────────────────────────────────────────────

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        """Load persisted geometry from hass.storage.

        Silently starts clean on missing data or version mismatch — the store
        accumulates from the next mission rather than crashing.
        """
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("GeometryStore: no persisted geometry for %s", entry_id)
            return
        if data.get("version", 0) != PAYLOAD_VERSION:
            _LOGGER.warning(
                "GeometryStore: incompatible storage version %s for %s, starting clean",
                data.get("version"), entry_id,
            )
            return
        try:
            self._restore_from_dict(data)
            _LOGGER.debug(
                "GeometryStore: loaded %d markers, %d walls, %d doors, %d obstacles for %s",
                len(self.door_markers), len(self.walls), len(self.doors),
                len(self.obstacles), entry_id,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("GeometryStore: failed to load geometry for %s: %s", entry_id, exc)
            # Reset to clean state — don't leave partially loaded data
            self.__init__()

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        """Persist current geometry to hass.storage."""
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save(self._to_dict())
        _LOGGER.debug(
            "GeometryStore: saved %d markers, %d walls for %s",
            len(self.door_markers), len(self.walls), entry_id,
        )

    # ── Mission processing ────────────────────────────────────────────────────

    def update_from_midpoints(self, midpoints: list[tuple[float, float]]) -> None:
        """Cluster gap midpoints into DoorMarkers.

        Primary write path for both EPHEMERAL (called via update_from_mission)
        and SMART (called directly from image.py _handle_mission_end).
        """
        for cx, cy in midpoints:
            existing = self._find_close_marker(cx, cy)
            if existing is not None:
                existing.update(cx, cy)
                _LOGGER.debug(
                    "GeometryStore: updated marker %s → (%.0f, %.0f) count=%d",
                    existing.id, existing.cx, existing.cy, existing.mission_count,
                )
            else:
                marker_id = f"dm_{self._next_marker_id}"
                self._next_marker_id += 1
                marker = DoorMarker(id=marker_id, cx=cx, cy=cy)
                marker.update(cx, cy)
                self.door_markers.append(marker)
                _LOGGER.info(
                    "GeometryStore: new door marker %s at (%.0f, %.0f)",
                    marker_id, cx, cy,
                )

    def update_from_room_seg_store(self, room_seg_store: Any) -> None:
        """EPHEMERAL path — sync door_markers 1:1 from RoomSegStore.doors.

        Deliberately bypasses update_from_midpoints()'s own spatial-
        proximity clustering (_find_close_marker / DOOR_CLUSTER_TOL_MM):
        RoomSegStore's doors are already identity-stable by room-pair
        (see room_seg_store.py's _match_doors), which is more precise
        than re-clustering by raw distance — going through the midpoint
        path here would re-introduce exactly the imprecision this data
        doesn't have. Each SegDoor's own median-of-recent-observations
        (cx, cy) — the same robustness-to-door-angle-variation mechanism
        as DoorMarker.update() — carries over directly.

        Replaces door_markers wholesale each call rather than appending:
        RoomSegStore.doors is already the complete, deduplicated current
        set, unlike the incremental per-mission midpoint stream
        update_from_midpoints() was designed for.
        """
        existing_by_id = {m.id: m for m in self.door_markers}
        synced: list[DoorMarker] = []
        for seg_door in room_seg_store.doors:
            gm_id = f"rs_{seg_door.id}"  # namespaced — can't collide with dm_N ids
            existing = existing_by_id.get(gm_id)
            if existing is not None:
                existing.cx, existing.cy = seg_door.cx, seg_door.cy
                existing.mission_count = len(seg_door.observations)
                existing.observations = [list(p) for p in seg_door.observations]
                synced.append(existing)
            else:
                marker = DoorMarker(
                    id=gm_id, cx=seg_door.cx, cy=seg_door.cy,
                    mission_count=len(seg_door.observations),
                    observations=[list(p) for p in seg_door.observations],
                )
                synced.append(marker)
                _LOGGER.info(
                    "GeometryStore: new door marker %s (from RoomSegStore) at (%.0f, %.0f)",
                    gm_id, marker.cx, marker.cy,
                )
        self.door_markers = synced

    def record_drift(self, dx: float, dy: float) -> bool:
        """Accumulate drift magnitude. Returns True if the recent-window mean
        exceeds the trigger threshold.

        The caller (image.py) fires the geometry_drifted Repair Issue on True.
        Only non-zero drift vectors are accumulated — (0.0, 0.0) from
        check_dock_drift() when no drift is detected is ignored.

        v3.1.0 DRIFT-AUTO: cumulative_drift_mm is still tracked (lifetime
        diagnostics value) but no longer drives the Repair decision — that
        now comes from the bounded recent_drifts_mm window's mean, which
        reflects current drift severity rather than lifetime total.
        """
        magnitude = math.hypot(dx, dy)
        if magnitude < 1.0:
            return False
        self.cumulative_drift_mm += magnitude

        self.recent_drifts_mm.append(magnitude)
        self.recent_drifts_mm = self.recent_drifts_mm[-_DRIFT_WINDOW:]

        _LOGGER.debug(
            "GeometryStore: drift %.0f mm recorded, cumulative %.0f mm, window=%s",
            magnitude, self.cumulative_drift_mm, self.recent_drifts_mm,
        )

        if len(self.recent_drifts_mm) < _DRIFT_MIN_SAMPLES:
            return False
        mean = sum(self.recent_drifts_mm) / len(self.recent_drifts_mm)
        return mean >= _RECENT_DRIFT_THRESHOLD_MM

    def drift_recovered(self) -> bool:
        """True when the recent-window mean has dropped back below the
        recovery threshold (hysteresis: trigger at 250mm, recover at 150mm,
        preventing flapping right at the boundary).

        Returns False (not recovered) when there aren't yet enough samples
        to judge — an empty or sparse window after migration should not be
        treated as "recovered" in a way that suppresses a genuine future
        trigger, nor does it fire a delete on a window that hasn't formed yet.
        """
        if len(self.recent_drifts_mm) < _DRIFT_MIN_SAMPLES:
            return False
        mean = sum(self.recent_drifts_mm) / len(self.recent_drifts_mm)
        return mean < _RECENT_DRIFT_RECOVERY_MM

    def reset_drift(self) -> None:
        """Reset both cumulative and recent-window drift after user re-confirms
        layout. Manual reconfirm remains available, but is no longer the only
        way to clear the Repair Issue — drift_recovered() also auto-clears it.
        """
        _LOGGER.debug(
            "GeometryStore: drift reset (was %.0f mm cumulative, window=%s)",
            self.cumulative_drift_mm, self.recent_drifts_mm,
        )
        self.cumulative_drift_mm = 0.0
        self.recent_drifts_mm = []

    # ── User editing ──────────────────────────────────────────────────────────

    def apply_user_edit(self, payload: dict[str, Any]) -> None:
        """Replace user geometry lists atomically from a validated payload.

        Does NOT touch door_markers — those are inference-only.
        Assigns stable IDs to any element missing one.
        """
        self.walls = [
            UserWall.from_dict(self._ensure_id(w)) for w in payload.get("walls", [])
        ]
        self.doors = [
            UserDoor.from_dict(self._ensure_id(d)) for d in payload.get("doors", [])
        ]
        self.obstacles = [
            UserObstacle.from_dict(self._ensure_id(o)) for o in payload.get("obstacles", [])
        ]
        if "zone_labels" in payload:
            self.zone_labels = {str(k): str(v) for k, v in payload["zone_labels"].items()}
        if "wall_offset_mm" in payload:
            self.wall_offset_mm = int(payload["wall_offset_mm"])
        _LOGGER.debug(
            "GeometryStore: user edit applied — %d walls, %d doors, %d obstacles",
            len(self.walls), len(self.doors), len(self.obstacles),
        )

    # ── Renderer support ──────────────────────────────────────────────────────

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def has_user_geometry(self) -> bool:
        """True when any user-authored geometry list is non-empty."""
        return bool(self.walls or self.doors or self.obstacles)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def diagnostic_info(self) -> dict[str, Any]:
        """Return a diagnostics-safe summary.

        Accesses only public attributes — safe to call from diagnostics.py.
        """
        return {
            "door_marker_count": len(self.door_markers),
            "wall_count": len(self.walls),
            "door_count": len(self.doors),
            "obstacle_count": len(self.obstacles),
            "zone_label_count": len(self.zone_labels),
            "wall_offset_mm": self.wall_offset_mm,
            "cumulative_drift_mm": round(self.cumulative_drift_mm, 1),
            "has_user_geometry": self.has_user_geometry,
            "door_markers": [
                {"id": m.id, "cx": round(m.cx), "cy": round(m.cy),
                 "mission_count": m.mission_count, "label": m.label}
                for m in self.door_markers
            ],
        }

    def _restore_from_dict(self, data: dict) -> None:
        """Restore store state from a previously serialised dict.

        Called by async_load after version check. Separated to allow direct
        testing of deserialisation without mocking hass.
        """
        self._next_marker_id = int(data.get("next_marker_id", 1))
        self.cumulative_drift_mm = float(data.get("cumulative_drift_mm", 0.0))
        # v3.1.0 DRIFT-AUTO — additive field, defaults to empty for any store
        # persisted before this feature (PAYLOAD_VERSION deliberately NOT
        # bumped: this is an additive field, not a structural break, and a
        # version bump here would discard door_markers/walls/doors/obstacles
        # for every existing installation, which is far more destructive than
        # the problem being fixed). An empty window after migration means no
        # immediate Repair fires even if cumulative_drift_mm is high — the
        # window rebuilds itself over the next few missions, which is the
        # desired self-healing behaviour for stale lifetime-drift state.
        raw_recent = data.get("recent_drifts_mm", [])
        self.recent_drifts_mm = [float(v) for v in raw_recent][-_DRIFT_WINDOW:]
        self.wall_offset_mm = int(data.get("wall_offset_mm", DEFAULT_WALL_OFFSET_MM))
        self.zone_labels = {str(k): str(v) for k, v in data.get("zone_labels", {}).items()}
        self.door_markers = [
            DoorMarker.from_dict(m) for m in data.get("door_markers", [])
        ]
        self.walls = [UserWall.from_dict(w) for w in data.get("walls", [])]
        self.doors = [UserDoor.from_dict(d) for d in data.get("doors", [])]
        self.obstacles = [UserObstacle.from_dict(o) for o in data.get("obstacles", [])]

    # ── Serialisation ─────────────────────────────────────────────────────────

    def _to_dict(self) -> dict[str, Any]:
        return {
            "version": PAYLOAD_VERSION,
            "next_marker_id": self._next_marker_id,
            "cumulative_drift_mm": self.cumulative_drift_mm,
            "recent_drifts_mm": self.recent_drifts_mm,
            "wall_offset_mm": self.wall_offset_mm,
            "zone_labels": self.zone_labels,
            "door_markers": [m.to_dict() for m in self.door_markers],
            "walls": [w.to_dict() for w in self.walls],
            "doors": [d.to_dict() for d in self.doors],
            "obstacles": [o.to_dict() for o in self.obstacles],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _find_close_marker(self, cx: float, cy: float) -> DoorMarker | None:
        """Return the closest existing DoorMarker within DOOR_CLUSTER_TOL_MM."""
        best: DoorMarker | None = None
        best_dist = DOOR_CLUSTER_TOL_MM
        for marker in self.door_markers:
            dist = math.hypot(cx - marker.cx, cy - marker.cy)
            if dist < best_dist:
                best_dist = dist
                best = marker
        return best

    @staticmethod
    def _ensure_id(element: dict[str, Any]) -> dict[str, Any]:
        """Return element dict with a stable id, generating one if absent."""
        if not element.get("id"):
            element = {**element, "id": uuid.uuid4().hex[:8]}
        return element
