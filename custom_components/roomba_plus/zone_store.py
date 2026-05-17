"""Zone Store for Roomba+ — gap-segmentation and persistent zone management.

Only used for EPHEMERAL map capability (900-series robots like the Roomba 980).
Smart-map robots (i/s/j) get their zones from cloud pmaps in Phase 3.

Algorithm overview:
  1. After each mission, process_mission() receives pose points in mm.
  2. Gap segmentation splits the trajectory wherever consecutive points are
     more than GAP_THRESHOLD_MM apart (= robot crossed a doorway).
  3. Each segment becomes a Zone candidate if it has enough points.
  4. Existing zones with overlapping bounding boxes are merged or updated
     using exponentially-weighted moving average (decay=0.85) so that recent
     missions dominate over older, potentially drifted observations.
  5. Zones and their mission histories are persisted in hass.storage so they
     survive HA restarts.
  6. Calibration: measured door-gap widths are compared to the DIN standard
     (875 mm) to derive a scale correction factor for the map renderer.

Zone naming:
  Newly detected zones get automatic names ("Raum 1", "Raum 2", …).
  The user can rename them via the Options-Flow async_step_zones step,
  triggered automatically by a HA Repair Issue after the first mission.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_zones"
STORAGE_VERSION    = 1

# ── Segmentation parameters ───────────────────────────────────────────────────
GAP_THRESHOLD_MM  = 800    # > 80 cm gap → doorway crossing
MIN_ZONE_POINTS   = 20     # segments shorter than this are noise
MERGE_OVERLAP     = 0.40   # bbox overlap fraction to trigger merge

# ── EMA drift compensation ────────────────────────────────────────────────────
EMA_DECAY         = 0.85   # weight of older missions decays by 0.85 per mission

# ── Calibration ───────────────────────────────────────────────────────────────
DIN_DOOR_WIDTH_MM = 875.0  # DIN 18101 standard interior door leaf width
MIN_DOOR_WIDTH_MM = 600.0  # Narrower → likely furniture gap, not a door
MAX_DOOR_WIDTH_MM = 1200.0 # Wider → likely open archway


@dataclass
class BoundingBox:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def area(self) -> float:
        return self.width * self.height

    def overlaps(self, other: BoundingBox, threshold: float = MERGE_OVERLAP) -> bool:
        """Return True if intersection area / min(area) > threshold."""
        ix = max(0, min(self.x_max, other.x_max) - max(self.x_min, other.x_min))
        iy = max(0, min(self.y_max, other.y_max) - max(self.y_min, other.y_min))
        inter = ix * iy
        min_area = min(self.area, other.area)
        if min_area <= 0:
            return False
        return (inter / min_area) > threshold

    def union(self, other: BoundingBox) -> BoundingBox:
        return BoundingBox(
            x_min=min(self.x_min, other.x_min),
            x_max=max(self.x_max, other.x_max),
            y_min=min(self.y_min, other.y_min),
            y_max=max(self.y_max, other.y_max),
        )


@dataclass
class MissionObservation:
    """Single-mission bounding-box observation for EMA weighting."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    point_count: int
    timestamp: float  # Unix timestamp


@dataclass
class Zone:
    """A detected room zone with EMA-weighted bounding box.

    id:           Stable integer identifier.
    name:         User-assigned or auto-generated name.
    confirmed:    True after user has named/confirmed via Options-Flow.
    bbox:         Current EMA-weighted bounding box (mm, dock-relative).
    observations: Per-mission raw bounding boxes (max 20 kept).
    confidence:   0.0–1.0, grows with number of confirming missions.
    """

    id: int
    name: str
    confirmed: bool = False
    x_min: float = 0.0
    x_max: float = 0.0
    y_min: float = 0.0
    y_max: float = 0.0
    confidence: float = 0.0
    observations: list[MissionObservation] = field(default_factory=list)

    @property
    def bbox(self) -> BoundingBox:
        return BoundingBox(self.x_min, self.x_max, self.y_min, self.y_max)

    def update_from_observation(self, obs: MissionObservation) -> None:
        """Add an observation and recompute the EMA-weighted bounding box."""
        self.observations.append(obs)
        # Keep at most 20 missions
        if len(self.observations) > 20:
            self.observations = self.observations[-20:]
        self._recompute_ema()
        self.confidence = min(1.0, len(self.observations) / 10.0)

    def _recompute_ema(self) -> None:
        """Recompute bounding box using exponentially weighted mission history.

        Newer observations receive weight EMA_DECAY^0 = 1.0;
        each older observation is multiplied by EMA_DECAY again.
        Point count is used as an additional weight so longer missions
        have more influence than short partial cleans.
        """
        n = len(self.observations)
        if not n:
            return

        total_w = x_min_w = x_max_w = y_min_w = y_max_w = 0.0
        for age, obs in enumerate(reversed(self.observations)):
            w = (EMA_DECAY ** age) * obs.point_count
            total_w  += w
            x_min_w  += obs.x_min * w
            x_max_w  += obs.x_max * w
            y_min_w  += obs.y_min * w
            y_max_w  += obs.y_max * w

        if total_w > 0:
            self.x_min = x_min_w / total_w
            self.x_max = x_max_w / total_w
            self.y_min = y_min_w / total_w
            self.y_max = y_max_w / total_w


class ZoneStore:
    """Manages zone lifecycle: detection, persistence, calibration, naming.

    Lifecycle:
      zone_store = ZoneStore()
      await zone_store.async_load(hass, entry_id)

      # After each mission:
      new_zones = zone_store.process_mission(points_mm, timestamp)
      await zone_store.async_save(hass, entry_id)

      # Calibration (from Options-Flow):
      scale_factor = zone_store.calibrate_from_gaps(points_mm)

      # Zone naming (from Options-Flow):
      zone_store.rename_zone(zone_id, new_name)
      await zone_store.async_save(hass, entry_id)
    """

    def __init__(self) -> None:
        self.zones: list[Zone] = []
        self._next_id: int = 1
        self._gap_threshold_mm: float = GAP_THRESHOLD_MM
        self._scale_factor: float = 1.0   # calibration correction

    # ── Persistence ───────────────────────────────────────────────────────────

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        """Load persisted zones from hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("ZoneStore: no persisted zones for %s", entry_id)
            return
        try:
            self._next_id = data.get("next_id", 1)
            self._gap_threshold_mm = data.get("gap_threshold_mm", GAP_THRESHOLD_MM)
            self._scale_factor = data.get("scale_factor", 1.0)
            self.zones = [
                self._zone_from_dict(z) for z in data.get("zones", [])
            ]
            _LOGGER.debug(
                "ZoneStore: loaded %d zones for %s", len(self.zones), entry_id
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("ZoneStore: failed to load zones: %s", exc)
            self.zones = []

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        """Persist current zones to hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "next_id": self._next_id,
            "gap_threshold_mm": self._gap_threshold_mm,
            "scale_factor": self._scale_factor,
            "zones": [self._zone_to_dict(z) for z in self.zones],
        })

    # ── Mission processing ────────────────────────────────────────────────────

    def process_mission(
        self,
        points_mm: list[tuple[float, float]],
        timestamp: float,
    ) -> list[Zone]:
        """Segment a completed mission into zones and update the store.

        Returns the list of zones that are newly detected (not yet confirmed).
        """
        if len(points_mm) < MIN_ZONE_POINTS:
            _LOGGER.debug("ZoneStore: mission too short (%d points), skipping", len(points_mm))
            return []

        segments = self._gap_split(points_mm)
        _LOGGER.debug("ZoneStore: mission %d points → %d segments", len(points_mm), len(segments))

        for segment in segments:
            if len(segment) < MIN_ZONE_POINTS:
                continue
            obs = self._make_observation(segment, timestamp)
            matched = self._find_matching_zone(obs)
            if matched:
                matched.update_from_observation(obs)
            else:
                new_zone = self._create_zone(obs)
                self.zones.append(new_zone)
                _LOGGER.info(
                    "ZoneStore: new zone detected — id=%d bbox=(%.0f,%.0f)-(%.0f,%.0f)",
                    new_zone.id, new_zone.x_min, new_zone.y_min,
                    new_zone.x_max, new_zone.y_max,
                )

        return [z for z in self.zones if not z.confirmed]

    def check_dock_drift(
        self, final_position_mm: tuple[float, float]
    ) -> tuple[float, float]:
        """Detect coordinate drift by comparing final position to dock origin.

        The Roomba always returns to the dock (0,0) after a successful mission.
        If final_position_mm significantly differs from origin, this is drift.
        Returns an (dx, dy) correction offset; (0,0) if within threshold.
        """
        dx, dy = final_position_mm
        threshold = 300.0  # mm — 30 cm drift is detectable
        if abs(dx) > threshold or abs(dy) > threshold:
            _LOGGER.debug(
                "ZoneStore: dock drift detected — final pos (%.0f, %.0f), "
                "threshold %.0f mm", dx, dy, threshold
            )
            return (-dx, -dy)
        return (0.0, 0.0)

    # ── Calibration ───────────────────────────────────────────────────────────

    def calibrate_from_gaps(
        self,
        points_mm: list[tuple[float, float]],
        known_door_width_mm: float = DIN_DOOR_WIDTH_MM,
    ) -> float | None:
        """Derive a scale correction factor from door-gap widths.

        Finds all point-to-point jumps in the plausible door-width range
        (MIN_DOOR_WIDTH_MM – MAX_DOOR_WIDTH_MM), takes their median as the
        measured door width, and returns measured / known as the scale factor.

        Returns None if fewer than 2 plausible door crossings are found.
        """
        gaps: list[float] = []
        for i in range(1, len(points_mm)):
            p1, p2 = points_mm[i - 1], points_mm[i]
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if MIN_DOOR_WIDTH_MM < dist < MAX_DOOR_WIDTH_MM:
                gaps.append(dist)

        if len(gaps) < 2:
            _LOGGER.debug("ZoneStore: calibration needs ≥2 door gaps, found %d", len(gaps))
            return None

        measured = statistics.median(gaps)
        factor = measured / known_door_width_mm
        self._scale_factor = factor
        # Also update gap threshold proportionally
        self._gap_threshold_mm = GAP_THRESHOLD_MM * factor
        _LOGGER.info(
            "ZoneStore: calibrated — measured door %.0f mm, known %.0f mm, "
            "scale factor %.3f", measured, known_door_width_mm, factor
        )
        return factor

    # ── Zone management ───────────────────────────────────────────────────────

    def rename_zone(self, zone_id: int, name: str) -> bool:
        """Rename a zone and mark it confirmed. Returns True if found."""
        for zone in self.zones:
            if zone.id == zone_id:
                zone.name = name.strip()
                zone.confirmed = True
                return True
        return False

    @property
    def unconfirmed_zones(self) -> list[Zone]:
        """Zones that have been detected but not yet named by the user."""
        return [z for z in self.zones if not z.confirmed]

    @property
    def has_unconfirmed_zones(self) -> bool:
        return bool(self.unconfirmed_zones)

    @property
    def gap_threshold_mm(self) -> float:
        return self._gap_threshold_mm

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _gap_split(
        self, points: list[tuple[float, float]]
    ) -> list[list[tuple[float, float]]]:
        """Split trajectory at gaps larger than gap_threshold_mm."""
        if not points:
            return []
        segments: list[list[tuple[float, float]]] = [[points[0]]]
        for i in range(1, len(points)):
            p1, p2 = points[i - 1], points[i]
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if dist > self._gap_threshold_mm:
                segments.append([])
            segments[-1].append(p2)
        return [s for s in segments if s]

    @staticmethod
    def _make_observation(
        segment: list[tuple[float, float]], timestamp: float
    ) -> MissionObservation:
        xs = [p[0] for p in segment]
        ys = [p[1] for p in segment]
        return MissionObservation(
            x_min=min(xs), x_max=max(xs),
            y_min=min(ys), y_max=max(ys),
            point_count=len(segment),
            timestamp=timestamp,
        )

    def _find_matching_zone(self, obs: MissionObservation) -> Zone | None:
        """Find an existing zone whose bbox overlaps significantly with obs."""
        obs_bbox = BoundingBox(obs.x_min, obs.x_max, obs.y_min, obs.y_max)
        best: Zone | None = None
        best_overlap: float = 0.0
        for zone in self.zones:
            if zone.bbox.overlaps(obs_bbox, MERGE_OVERLAP):
                inter_w = max(0, min(zone.x_max, obs.x_max) - max(zone.x_min, obs.x_min))
                inter_h = max(0, min(zone.y_max, obs.y_max) - max(zone.y_min, obs.y_min))
                overlap = inter_w * inter_h
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = zone
        return best

    def _create_zone(self, obs: MissionObservation) -> Zone:
        zone_id = self._next_id
        self._next_id += 1
        # Start with an empty zone and run update_from_observation so that
        # _recompute_ema() is called immediately. This ensures the first zone's
        # bounding box is EMA-weighted from the start, consistent with all
        # subsequent observations.
        zone = Zone(
            id=zone_id,
            name=f"Room {zone_id}",
        )
        zone.update_from_observation(obs)
        return zone


    def diagnostic_info(self) -> dict:
        """Return a diagnostics-safe summary with no private attribute access.

        Called by diagnostics.py so it never needs to reach into _scale_factor
        or _next_id directly.
        """
        return {
            "gap_threshold_mm": self._gap_threshold_mm,
            "scale_factor": self._scale_factor,
            "zone_count": len(self.zones),
            "unconfirmed_zone_count": len(self.unconfirmed_zones),
            "zones": [
                {
                    "id": z.id,
                    "name": z.name,
                    "confirmed": z.confirmed,
                    "confidence": round(z.confidence, 3),
                    "observation_count": len(z.observations),
                    "bbox_mm": {
                        "x_min": round(z.x_min),
                        "x_max": round(z.x_max),
                        "y_min": round(z.y_min),
                        "y_max": round(z.y_max),
                    },
                }
                for z in self.zones
            ],
        }

    # ── Serialisation helpers ─────────────────────────────────────────────────

    @staticmethod
    def _zone_to_dict(zone: Zone) -> dict[str, Any]:
        return {
            "id": zone.id,
            "name": zone.name,
            "confirmed": zone.confirmed,
            "x_min": zone.x_min,
            "x_max": zone.x_max,
            "y_min": zone.y_min,
            "y_max": zone.y_max,
            "confidence": zone.confidence,
            "observations": [
                {
                    "x_min": o.x_min, "x_max": o.x_max,
                    "y_min": o.y_min, "y_max": o.y_max,
                    "point_count": o.point_count,
                    "timestamp": o.timestamp,
                }
                for o in zone.observations
            ],
        }

    @staticmethod
    def _zone_from_dict(d: dict[str, Any]) -> Zone:
        observations = [
            MissionObservation(
                x_min=o["x_min"], x_max=o["x_max"],
                y_min=o["y_min"], y_max=o["y_max"],
                point_count=o["point_count"],
                timestamp=o["timestamp"],
            )
            for o in d.get("observations", [])
        ]
        return Zone(
            id=d["id"],
            name=d["name"],
            confirmed=d.get("confirmed", False),
            x_min=d["x_min"], x_max=d["x_max"],
            y_min=d["y_min"], y_max=d["y_max"],
            confidence=d.get("confidence", 0.0),
            observations=observations,
        )
