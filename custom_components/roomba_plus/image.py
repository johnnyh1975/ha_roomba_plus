"""Image platform for Roomba+ — live cleaning map as ImageEntity.

ImageEntity is the correct HA platform for periodically-updated still images.
Unlike Camera, it renders inline in the frontend popup without streaming.

Key behaviour per HA ImageEntity docs:
  - async_image() returns bytes on demand (called by frontend)
  - image_last_updated must be bumped when new image data is available
  - Frontend re-fetches async_image() whenever state changes
  - access_tokens deque must be initialized and async_update_token() called
    once hass is available (in async_added_to_hass)

Mission lifecycle:
  Phase 'run'         -> MapRenderer.reset(), accumulate pose points
  Pose updates        -> MapRenderer.add_pose(), bump image_last_updated
  bbrun.nStuck rises  -> MapRenderer.mark_stuck()
  Phase 'charge' etc  -> ZoneStore.process_mission() (EPHEMERAL only)
                      -> renderer.dump_state() saved to hass.storage

Persistence:
  After every mission end the renderer state (pose points, stuck positions,
  heading) is written to hass.storage under the key
  'roomba_plus_map_{entry_id}'. On async_added_to_hass the stored state
  is restored so the last mission's map survives an HA restart.

  The cached PNG is not stored — it is re-rendered from the persisted points
  on the first async_image() call, which takes <5 ms.
"""
from __future__ import annotations

import asyncio
import collections
import datetime
import io
import logging
import math
import time as _time_mod
from datetime import datetime as dt_datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import (
    CLEANING_PHASES,
    DOMAIN,
    END_SIGNAL_DEBOUNCE_COUNT,
    END_SIGNAL_MIN_HOLD_SECONDS,
    GAP_THRESHOLD_MM,
    MAX_DOOR_WIDTH_MM,
    MIN_DOOR_WIDTH_MM,
    MISSION_END_PHASES,
    POSE_POINT_CM_TO_MM,
    REGION_TYPE_ICONS,
    ROOM_TRANSITION_CANDIDATE_PHASES,
)
from .entity import IRobotEntity
from .grid_store import GridStore, CELL_SIZE_MM, DECAY, VISIT_INCREMENT
from .map_renderer import MapRenderer
from .models import MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

# ROOM-PALETTE (v2.9.0) — rotating per-room fill colours for _render_rooms_png().
# Muted/desaturated tones chosen to read clearly against the dark (30,30,30)
# canvas background while staying visually distinct from each other and from
# the fixed outline colour (100,149,237). 8 entries — rotates via index % 8.
ROOM_FILL_PALETTE: list[tuple[int, int, int]] = [
    (61, 74, 94),    # slate blue   (close to the old single uniform fill)
    (74, 94, 61),    # olive green
    (94, 61, 74),    # muted maroon
    (94, 86, 61),    # warm ochre
    (61, 94, 91),    # teal
    (86, 61, 94),    # muted purple
    (94, 75, 61),    # burnt orange
    (61, 79, 94),    # steel blue
]

# CLEANING_PHASES and MISSION_END_PHASES moved to const.py (v2.3.0 Step 1)

_MAP_STORAGE_VERSION = 1

# v2.8.2 — mission-in-progress checkpoint. Separate storage key/version from
# _MAP_STORAGE_VERSION (the renderer's "last completed mission" snapshot)
# because this one represents a possibly-incomplete, still-in-flight mission
# and has a different lifecycle: written on every stuck event, consumed
# (resumed or salvaged) exactly once on the first MQTT message after
# startup, and deleted once a mission reaches a normal end. See
# RoombaMapImage._consume_pending_checkpoint() / _salvage_orphaned_checkpoint().
_MISSION_CHECKPOINT_STORAGE_VERSION = 1


def _mission_checkpoint_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_checkpoint_{entry_id}"


# v2.6.3 E — dispatcher signal fired by RoombaMapImage after GridStore update.
# RoombaCoverageImage listens to bump image_last_updated so the frontend re-fetches.
_SIGNAL_COVERAGE_UPDATED = "roomba_plus_coverage_updated_{}"


async def _async_send_coverage_signal(hass: HomeAssistant, entry_id: str) -> None:
    """Fire the coverage-updated dispatcher signal on the HA event loop."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(hass, _SIGNAL_COVERAGE_UPDATED.format(entry_id))


def _map_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_{entry_id}"


def _room_slug(name: str) -> str:
    """Return an ASCII-safe slug suitable for use as an XVMC predefined_selection id.

    XVMC validates id values and rejects non-ASCII characters (e.g. German umlauts,
    Italian accents).  This helper performs NFD decomposition to strip combining
    diacritics, then replaces any remaining non-alphanumeric characters with
    underscores and collapses runs.

    Examples:
        "Küche"        → "kuche"
        "Büro"         → "buro"
        "Bad & Küche"  → "bad_kuche"
        "Living Room"  → "living_room"
    """
    import unicodedata
    import re as _re
    nfd = unicodedata.normalize("NFD", name)
    ascii_only = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    slug = _re.sub(r"[^a-zA-Z0-9]+", "_", ascii_only).strip("_").lower()
    return _re.sub(r"_+", "_", slug) or "room"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up map image entities — only if robot reports pose data."""
    data = config_entry.runtime_data

    if data.map_capability == MapCapability.NONE:
        _LOGGER.debug("Roomba+ image: skipped — no pose capability")
        return

    entities: list[Any] = [
        RoombaMapImage(
            roomba=data.roomba,
            blid=data.blid,
            renderer=data.renderer,
            map_capability=data.map_capability,
            config_entry=config_entry,
        )
    ]

    # F9 — coverage heatmap entity (all pose-capable robots with GridStore)
    if data.grid_store is not None:
        entities.append(
            RoombaCoverageImage(
                roomba=data.roomba,
                blid=data.blid,
                grid_store=data.grid_store,
                config_entry=config_entry,
            )
        )

    # v2.3.2 — room layout entity for xiaomi-vacuum-map-card.
    # Extended from SMART-only to include EPHEMERAL when UmfAligner is present:
    # 900-series robots (e.g. 980) have cloud UMF geometry and a functioning
    # aligner but were excluded by the SMART gate despite having all required data.
    if data.map_capability == MapCapability.SMART or (
        data.map_capability == MapCapability.EPHEMERAL
        and data.umf_aligner is not None
    ):
        entities.append(
            RoombaRoomsImage(
                roomba=data.roomba,
                blid=data.blid,
                config_entry=config_entry,
            )
        )

    async_add_entities(entities)


def _check_dock_drift(final_position_mm: tuple[float, float]) -> tuple[float, float]:
    """Detect coordinate drift by comparing final position to dock origin.

    The Roomba always returns to the dock (0,0) after a successful mission.
    If final_position_mm significantly differs from origin, this is drift.
    Returns a (dx, dy) correction offset; (0,0) if within threshold.

    ROOM-SEG Stage 6 — relocated from ZoneStore.check_dock_drift() (deleted
    along with the rest of ZoneStore). Always was a pure function of its
    one argument with no dependency on ZoneStore's own state; only ever
    called from this module, so it lives here now rather than anywhere
    that needs a ZoneStore instance just to reach it.
    """
    dx, dy = final_position_mm
    threshold = 300.0  # mm — 30 cm drift is detectable
    if abs(dx) > threshold or abs(dy) > threshold:
        _LOGGER.debug(
            "Map: dock drift detected — final pos (%.0f, %.0f), threshold %.0f mm",
            dx, dy, threshold,
        )
        return (-dx, -dy)
    return (0.0, 0.0)


def _compute_dock_correction(
    measured_final_pos: tuple[float, float],
    measured_final_theta: float,
    dock_theta_baseline: float | None,
) -> tuple[float, float, float]:
    """v3.2.1 DOCK-ANCHOR — compute the (dx, dy, rotation_rad) correction
    that maps measured_final_pos/theta onto the known-true dock state
    (position always (0,0); heading dock_theta_baseline if available).

    Automatic v1→v2 upgrade, no manual switch: dock_theta_baseline is
    None until RobotProfileStore.dock_theta_baseline_ready — until then
    this returns rotation_rad=0.0 (pure translation, same as the
    existing _check_dock_drift, just restructured to also carry a
    rotation component once available). See Dock_Anchor_Korrektur_Plan.md
    for why rotation cannot safely start from an unvalidated first
    theta observation.
    """
    dx, dy = -measured_final_pos[0], -measured_final_pos[1]
    if dock_theta_baseline is None:
        return (dx, dy, 0.0)
    rotation_rad = math.radians(dock_theta_baseline - measured_final_theta)
    # Rotation is applied to segment points *before* translation elsewhere
    # (see _apply_dock_correction) — the translation component here must
    # be computed against the ALREADY-ROTATED final position, not the raw
    # measured one, or the two corrections would fight each other.
    cos_r, sin_r = math.cos(rotation_rad), math.sin(rotation_rad)
    mx, my = measured_final_pos
    rotated_x = mx * cos_r - my * sin_r
    rotated_y = mx * sin_r + my * cos_r
    return (-rotated_x, -rotated_y, rotation_rad)


def _apply_dock_correction(
    point: tuple[float, float], dx: float, dy: float, rotation_rad: float,
) -> tuple[float, float]:
    """Apply one (dx, dy, rotation_rad) correction to a single point —
    rotate around the origin first, then translate. Order matters: see
    _compute_dock_correction's docstring."""
    x, y = point
    if rotation_rad:
        cos_r, sin_r = math.cos(rotation_rad), math.sin(rotation_rad)
        x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
    return (x + dx, y + dy)


def _interpolate_and_correct_segment(
    points: list[tuple[float, float]],
    dx: float, dy: float, rotation_rad: float,
) -> list[tuple[float, float]]:
    """v3.2.1 DOCK-ANCHOR (4c) — distribute a dock-verified correction
    proportionally across a buffered segment instead of applying it
    uniformly or only to the last point.

    Rationale: drift accumulated since a stuck event is assumed to grow
    gradually (odometry/vSLAM error compounding over time), not appear
    in one jump right before the dock — so weight 0 at the FIRST
    buffered point (still anchored to the last trusted pre-stuck
    position) growing linearly to weight 1 (the full measured
    correction) at the LAST buffered point (right before dock contact).

    Internal accepted jumps within the segment (see MapRenderer.add_pose
    return value) are intentionally NOT treated as separate interpolation
    breakpoints in this first version — see Dock_Anchor_Korrektur_Plan.md
    4c: confidence-weighting for jump-adjacent sub-segments was
    deliberately deferred pending real field validation, not implemented
    speculatively ahead of evidence that simple linear interpolation
    isn't good enough.
    """
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [_apply_dock_correction(points[0], dx, dy, rotation_rad)]
    out = []
    for i, p in enumerate(points):
        weight = i / (n - 1)
        out.append(_apply_dock_correction(p, dx * weight, dy * weight, rotation_rad * weight))
    return out


class RoombaMapImage(IRobotEntity, ImageEntity):
    """Live cleaning map as an ImageEntity.

    The image updates on every MQTT pose message. image_last_updated is
    bumped after each new pose point so the frontend re-fetches the PNG.

    access_tokens is initialized manually here because ImageEntity.__init__
    requires hass which is not yet available at entity creation time.
    async_update_token() is called in async_added_to_hass once hass is set.

    Map state (pose points, stuck markers, heading) is persisted to
    hass.storage after each mission end and restored after HA restarts.
    """

    _attr_translation_key = "map"
    _attr_entity_category = None
    _attr_content_type = "image/png"

    def __init__(
        self,
        roomba: Any,
        blid: str,
        renderer: MapRenderer | None,
        map_capability: MapCapability,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)

        # Manually initialize ImageEntity internals that require hass.
        # async_update_token() is called in async_added_to_hass.
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)

        self._renderer = renderer
        self._map_capability = map_capability
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map"

        # Mission tracking
        self._last_phase: str = ""
        self._last_stuck_count: int = 0
        self._mission_points: list[tuple[float, float]] = []
        # v3.2.1 — parallel theta list, same index alignment as
        # self._mission_points (mission_thetas[i] is the heading for
        # mission_points[i]). Kept SEPARATE rather than widening
        # _mission_points itself to (x,y,theta): that list is consumed
        # in many places as strict (x,y) pairs (GridStore.
        # update_from_mission, _check_dock_drift's final-position check,
        # etc.) — changing its shape would ripple through all of them.
        # Additive, no behaviour change: existing consumers are
        # untouched, new consumers (MissionTrajectoryStore, future
        # Dock-Anchor-Korrektur rotation math) read this list alongside.
        self._mission_thetas: list[float] = []
        self._stuck_mission_points: list[tuple[float, float]] = []
        # v3.2.1 DOCK-ANCHOR — replaces the old binary
        # _room_data_frozen_after_stuck with a proper buffer + state flag.
        # Field-confirmed rationale: a stuck event is exactly the moment a
        # human is most likely to have physically lifted and repositioned
        # the robot to free it — vSLAM's continuous camera-landmark
        # tracking breaks the instant the robot leaves the floor, and
        # after being set back down (possibly at a slightly different
        # heading than before), the pose stream may resume reporting
        # self-consistent-looking but subtly MISALIGNED positions relative
        # to everything recorded before the stuck event. A robot's own
        # motor-driven self-recovery (backing out, trying another angle)
        # does NOT necessarily break this — but we can't reliably tell the
        # two apart from the data alone, so the conservative rule applies
        # to every stuck event: only the DOCK gives a precise, independent
        # re-anchor (IR/contact-based, not vSLAM-dependent).
        #
        # While _dock_anchor_buffering is True, new pose points go into
        # _pending_segment_points/_thetas instead of _mission_points/
        # _mission_thetas. On a confirmed dock contact (see
        # _dock_contact_streak below), the buffered segment is corrected
        # (see _compute_dock_correction/_interpolate_and_correct_segment)
        # and merged INTO _mission_points/_mission_thetas — replacing the
        # old "freeze then discard at next mission start" behaviour with
        # "freeze then retroactively correct and keep, where possible".
        # Never touches self._renderer.add_pose() — the live-map visual
        # still shows the full path live (useful for troubleshooting);
        # only the GridStore/RoomSegStore/OutlineStore-feeding
        # _mission_points is affected, corrected in place once resolved.
        # See Dock_Anchor_Korrektur_Plan.md for the full design.
        self._dock_anchor_buffering: bool = False
        self._pending_segment_points: list[tuple[float, float]] = []
        self._pending_segment_thetas: list[float] = []
        # v3.2.1 DOCK-ANCHOR — index into _mission_points/_mission_thetas
        # marking the start of the segment since the LAST confirmed dock
        # contact (or mission start, index 0, if none yet this mission).
        # Used by Fall B (a clean recharge-and-resume, no buffering) to
        # know how much of _mission_points to correct — everything since
        # this index, not the whole mission.
        self._last_dock_anchor_index: int = 0
        # v3.2.1 DOCK-ANCHOR — separate debounce counter from
        # _end_signal_streak (below). "Confirmed at the dock" fires on
        # ANY sustained charge/hmPostMsn phase, whether the mission is
        # ending (Fall A/B end-of-mission) or just recharging mid-mission
        # (Fall B, mission continues) — unlike _end_signal_streak, this
        # doesn't need the extra END_SIGNAL_MIN_HOLD_SECONDS grace period
        # (that grace period exists to decide "is this really the END",
        # a question this mechanism doesn't need answered first).
        self._dock_contact_streak: int = 0
        # v3.2.1 DOCK-ANCHOR — field-confirmed gap in the FIRST version of
        # this mechanism: a rapid ~21ms firmware burst reporting
        # charge/hmPostMsn during a normal inter-room transition (the
        # EXACT scenario the existing END-DEBOUNCE mechanism's
        # END_SIGNAL_MIN_HOLD_SECONDS hold-time exists to filter out)
        # would satisfy a pure count-based streak threshold just as
        # easily as a genuine dock contact — count alone doesn't
        # distinguish "sustained" from "coincidentally happened twice
        # fast." Can't reuse _end_signal_first_ts/_end_signal_streak
        # directly: that mechanism deliberately RESETS its own streak for
        # exactly the Fall-B scenario this needs to catch (cycle=clean +
        # phase=charge, i.e. _looks_like_end=False) — needs independent
        # tracking, not shared state.
        self._dock_contact_first_ts: float = 0.0
        # v2.6.3 A+D — True once robot enters CLEANING_PHASES in this mission.
        # Replaces last_phase-in-CLEANING_PHASES guard; fixes stuck-bypass and
        # false mission-restart on stuck → run recovery.
        self._had_cleaning_phase: bool = False
        # v2.8.1 (END-DEBOUNCE) — consecutive-message counter, mirrors the
        # same mechanism in callbacks.py. See _on_message for details.
        self._end_signal_streak: int = 0
        # v2.8.3 — monotonic timestamp when the current streak started.
        # Mirrors callbacks.py end_signal_first_ts — see that module for the
        # full rationale.  Required in both places because image.py's
        # mission-end detection is independent of callbacks.py (it feeds
        # ZoneStore/GeometryStore/GridStore/OutlineStore).
        self._end_signal_first_ts: float = 0.0
        # v2.8.2 — mission-in-progress checkpoint state. mssn_strt_tm
        # identifies "is this still the same mission" across an HA restart
        # (same field callbacks.py already uses for this purpose — robust
        # because 980/900-series firmware does NOT reset it mid-mission,
        # unlike at mission end). _pending_checkpoint holds whatever was
        # loaded from storage at startup until the first MQTT message
        # resolves it (resume or salvage) — see _consume_pending_checkpoint().
        self._mission_checkpoint_mssn_strt_tm: int = 0
        self._pending_checkpoint: dict[str, Any] | None = None

        # Initial timestamp so frontend knows an image exists from the start
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register MQTT callback, restore persisted map state, generate token."""
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # Restore last mission's map from hass.storage (if any)
        await self._async_restore_map_state()
        # v2.8.2 — load (but do not yet apply) a mission-in-progress
        # checkpoint, if one exists. The first live MQTT message decides
        # whether to resume it or salvage it — see _consume_pending_checkpoint().
        await self._async_load_pending_checkpoint()

    # ── ImageEntity interface ─────────────────────────────────────────────────

    async def async_image(self) -> bytes | None:
        """Return current map as PNG bytes. Always returns a valid image."""
        if self._renderer is None:
            return self._blank_image()
        png = await self.hass.async_add_executor_job(self._renderer.render)

        # v2.3.0 Step 6 — keepout zone overlay (Amendment 4)
        if self._config_entry is not None:
            _data = self._config_entry.runtime_data
            aligner = _data.umf_aligner
            if (
                aligner and aligner.aligned
                and _data.cloud_coordinator is not None
            ):
                keepout_raw = _data.cloud_coordinator.keepout_zones
                if keepout_raw:
                    polys_px: list[list[tuple[int, int]]] = []
                    for zone in keepout_raw:
                        poly_umf = aligner.keepout_polygon_umf(zone)
                        if not poly_umf:
                            continue
                        poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                        if not all(p is not None for p in poly_pose):
                            continue
                        polys_px.append(
                            [self._renderer._mm_to_px_fit(x, y) for x, y in poly_pose]
                        )
                    if polys_px:
                        overlay_png = await self.hass.async_add_executor_job(
                            self._renderer.render_keepout_zones, polys_px
                        )
                        if overlay_png is not None:
                            png = overlay_png

                # ZONE-OVERLAY (v3.0.0) — robot-observed obstacle zones as orange circles.
                # Gate: same as keepout (aligner aligned) — observed_zone_centroids are in
                # UMF space and require the aligner for pose conversion.
                # Available: any robot with active cloud coordinator (SMART + EPHEMERAL
                # with cloud credentials) — not limited to has_pmaps.
                centroids = _data.cloud_coordinator.observed_zone_centroids if _data.cloud_coordinator else []
                if centroids:
                    _OBSERVED_RADIUS_MM = 200  # approx obstacle circle radius in mm
                    circles_px: list[tuple[int, int, int]] = []
                    for c in centroids:
                        pose_xy = aligner.umf_to_pose(c["x"], c["y"])
                        if pose_xy is None:
                            continue
                        cx_px, cy_px = self._renderer._mm_to_px_fit(*pose_xy)
                        r_px = max(3, round(_OBSERVED_RADIUS_MM / self._renderer._fit_scale))
                        circles_px.append((int(cx_px), int(cy_px), r_px))
                    if circles_px:
                        overlay_png = await self.hass.async_add_executor_job(
                            self._renderer.render_observed_zones, circles_px
                        )
                        if overlay_png is not None:
                            png = overlay_png

        # F-EPHEMERAL — Room outline overlay (EPHEMERAL, mission_count >= 2)
        if self._config_entry is not None:
            _edata = self._config_entry.runtime_data
            _outline_store = getattr(_edata, "outline_store", None)
            if (
                _outline_store is not None
                and _outline_store.ready
                and self._renderer is not None
            ):
                from .models import MapCapability
                if _edata.map_capability == MapCapability.EPHEMERAL:
                    outline_png = await self.hass.async_add_executor_job(
                        self._renderer.render_room_outline,
                        _outline_store.contour_points,
                    )
                    if outline_png is not None:
                        png = outline_png
        return png

    # v2.3.0 Step 5 — calibration + rooms for xiaomi-vacuum-map-card
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and room polygon data for xiaomi-vacuum-map-card.

        Both attributes require UmfAligner confidence >= 0.70 and a renderer
        that has completed at least one render() call (so _mm_to_px() is valid).
        Returns empty dict when no aligner, not aligned, or no renderer.
        """
        attrs: dict[str, Any] = {}
        if self._config_entry is None or self._renderer is None:
            return attrs
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None or not aligner.aligned:
            return attrs

        # calibration — three anchor point pairs for xiaomi-vacuum-map-card
        # v2.6.3 B1: _mm_to_px_fit gives fit-adjusted pixels matching displayed image
        # XVMC (v2.7.0): calibration_points key enables calibration_source: { camera: true }
        cal = aligner.calibration_points(self._renderer._mm_to_px_fit)
        if cal:
            attrs["calibration_points"] = cal

        # rooms — dict {name: {outline:[[x,y],...], name, icon, x, y}}
        # XVMC (v2.7.0): dict keyed by display name; outline uses [x,y] arrays.
        cc = data.cloud_coordinator
        rid_to_type = (
            {r["id"]: r.get("region_type", "default") for r in cc.regions}
            if cc is not None else {}
        )
        rid_to_name = aligner.rid_to_name()
        rooms: dict[str, dict[str, Any]] = {}
        for rid, poly_umf in aligner.room_polygons_umf.items():
            poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
            # Bug 6 fix: guard against empty polygon (vacuous all() on [])
            if not poly_pose or not all(p is not None for p in poly_pose):
                continue
            room_name = rid_to_name.get(rid, rid)
            # XVMC-COORDS: outline and centroid in pose-space mm (not pixels).
            # XVMC applies calibration (pose mm → display px) itself.
            cx = sum(x for x, _ in poly_pose) / len(poly_pose)
            cy = sum(y for _, y in poly_pose) / len(poly_pose)
            icon = REGION_TYPE_ICONS.get(
                rid_to_type.get(rid, "default"), REGION_TYPE_ICONS["default"]
            )
            rooms[room_name] = {
                "outline": [[x, y] for x, y in poly_pose],
                "name":    room_name,
                "room_id": _room_slug(room_name),  # v2.7.3: ASCII slug for XVMC id
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

        # ZONE-OVERLAY (v3.3.1) + F24 — mirrors RoombaRoomsImage's identical
        # block for parity ("Both map entities expose calibration_points and
        # rooms attributes", docs/FEATURES.md). This class is always in
        # aligned mode by this point (early-returned above if not
        # aligner.aligned), so no extra gate is needed here.
        # zones — UMF-space source (observed_zone_centroids, keepout_zones),
        # genuinely needs the aligner transform, unlike door_markers/
        # furniture_candidates below.
        if cc is not None:
            zones: list[dict[str, Any]] = []
            for centroid in cc.observed_zone_centroids:
                pose_xy = aligner.umf_to_pose(centroid["x"], centroid["y"])
                if pose_xy is None:
                    continue
                zones.append({
                    "type": "observed",
                    "x":    pose_xy[0],
                    "y":    pose_xy[1],
                })
            for zone in cc.keepout_zones:
                poly_umf = aligner.keepout_polygon_umf(zone)
                if not poly_umf:
                    continue
                poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                if not poly_pose or not all(p is not None for p in poly_pose):
                    continue
                zones.append({
                    "type":    "keepout",
                    "polygon": [[x, y] for x, y in poly_pose],
                })
            if zones:
                attrs["zones"] = zones

        # door_markers — already pose-space mm (collected directly from
        # self._mission_points / RoomSegStore.doors, never through UMF) —
        # exposed as-is, NOT through umf_to_pose(). Known caveat: markers
        # accumulate across missions and are not re-corrected by
        # GeometryStore.record_drift()/drift_recovered() (those only track
        # drift magnitude for the Repair Issue), so a marker's median
        # position can lag behind a large drift correction between
        # missions — same open-ended caveat class as observed_zone
        # centroids' Q6 note, not treated as a blocker.
        geometry_store = getattr(data, "geometry_store", None)
        if geometry_store is not None and geometry_store.door_markers:
            attrs["door_markers"] = [
                {
                    "id":            m.id,
                    "cx":            m.cx,
                    "cy":            m.cy,
                    "label":         m.label,
                    "mission_count": m.mission_count,
                }
                for m in geometry_store.door_markers
            ]

        # F24 — furniture shadow candidates. GridStore.furniture_
        # candidates()'s x_mm/y_mm come from _cell_to_mm(), the same
        # pose-space family hotspots()/format=hazards already documents —
        # no transform needed, exposed as-is.
        grid_store = getattr(data, "grid_store", None)
        if grid_store is not None:
            candidates = grid_store.furniture_candidates()
            if candidates:
                attrs["furniture_candidates"] = [
                    {"x_mm": c["x_mm"], "y_mm": c["y_mm"]} for c in candidates
                ]

        return attrs

    # ── Push-update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return (
            "pose" in new_state
            or "cleanMissionStatus" in new_state
            or "bbrun" in new_state
        )

    def on_message(self, json_data: dict[str, Any]) -> None:
        """Process MQTT update — feed pose to renderer, bump image timestamp."""
        state = json_data.get("state", {}).get("reported", {})
        if not self.new_state_filter(state):
            return

        self.vacuum_state = roomba_reported_state(self.vacuum)
        current_phase = (
            (self.vacuum_state.get("cleanMissionStatus") or {}).get("phase", "")
        )

        # v2.8.2 — resolve any checkpoint loaded at startup against this,
        # the first live MQTT message since restart. Must run before the
        # phase-transition block below: if this resumes a still-ongoing
        # mission, _had_cleaning_phase is set True here, which correctly
        # makes the "mission started" reset below a no-op for this message.
        if self._pending_checkpoint is not None:
            self._consume_pending_checkpoint()

        # Phase transitions
        if current_phase != self._last_phase:
            # v2.6.3 D — guard with _had_cleaning_phase so stuck → run (recovery)
            # does NOT reset the renderer mid-mission.
            if current_phase in CLEANING_PHASES and not self._had_cleaning_phase:
                self._had_cleaning_phase = True
                if self._renderer:
                    self._renderer.reset()
                    self._mission_points = []
                    self._mission_thetas = []
                    self._stuck_mission_points = []
                    # v3.2.1 DOCK-ANCHOR — a new mission starting means any
                    # still-buffered segment from the PREVIOUS mission never
                    # got a dock-contact confirmation (stuck_and_abandoned,
                    # see Dock_Anchor_Korrektur_Plan.md Abschnitt 5) — it is
                    # discarded here exactly as the old flag-based version
                    # discarded it, just via clearing the buffer instead of
                    # flipping a boolean.
                    self._dock_anchor_buffering = False
                    self._pending_segment_points = []
                    self._pending_segment_thetas = []
                    self._last_dock_anchor_index = 0
                    self._dock_contact_streak = 0
                    self._dock_contact_first_ts = 0.0
                    self._mission_start_ts: str | None = dt_util.now().isoformat()
                    # v2.8.2 — cached the same way callbacks.py caches it:
                    # needed so a later checkpoint (saved on a stuck event)
                    # can be matched against the live mission on restart.
                    self._mission_checkpoint_mssn_strt_tm = (
                        (self.vacuum_state.get("cleanMissionStatus") or {}).get("mssnStrtTm") or 0
                    )
                    _LOGGER.debug("Map: mission started, renderer reset")

            self._last_phase = current_phase

        # v2.8.1 (END-DEBOUNCE): mirrors the fix in callbacks.py's
        # _on_mission_message. Before this fix, a single transient MQTT
        # message momentarily reporting an ambiguous phase (charge/hmPostMsn)
        # — without even a cycle check, unlike callbacks.py's pre-v2.8.1
        # state — was enough to fire _handle_mission_end() mid-mission. That
        # call clears self._mission_points (line 556 below) and feeds the
        # partial trajectory-so-far into ZoneStore, GeometryStore, GridStore,
        # and OutlineStore. The next genuine "run" message then wipes
        # self._mission_points again via the mission-start reset above —
        # fragmenting one continuous multi-room mission into several small,
        # disconnected pieces, none of which individually shows the gap
        # needed to ever split a zone or register a door marker. This is the
        # same root cause confirmed (and fixed) in callbacks.py for the
        # MissionTimerStore progress-reset regression; this is the matching
        # fix for the map/zone/geometry/grid/outline side, which had no
        # protection at all (not even the v2.8.0 cycle-only guard).
        #
        # Deliberately evaluated on every message that actually carries a
        # cleanMissionStatus update — NOT folded into the "phase transitions"
        # edge-trigger above. A real "stays in charge for two consecutive
        # messages" sequence has the same current_phase value both times, so
        # an edge-triggered (`current_phase != self._last_phase`) check would
        # only ever see ONE transition and could never count two consecutive
        # confirmations. Restricting to "cleanMissionStatus" in state (the
        # raw per-message delta, not the merged self.vacuum_state) instead of
        # running on every on_message() call avoids over-counting against
        # pose-only/bbrun-only updates that don't represent a new mission
        # status reading at all.
        if "cleanMissionStatus" in state:
            _cycle = (self.vacuum_state.get("cleanMissionStatus") or {}).get("cycle", "")
            _is_inter_room_transition = _cycle in ("clean", "quick")
            _looks_like_end = (
                current_phase in MISSION_END_PHASES and not _is_inter_room_transition
            )
            _ambiguous_end_phase = current_phase in ROOM_TRANSITION_CANDIDATE_PHASES
            # v3.2.1 DOCK-ANCHOR — captured BEFORE the mission-end block
            # below can reset self._had_cleaning_phase to False. Without
            # this, a message that BOTH confirms mission-end AND is the
            # dock-contact-confirming message would see
            # self._had_cleaning_phase already flipped False and silently
            # skip dock-contact detection for that message.
            _was_in_cleaning_phase_this_message = self._had_cleaning_phase

            if self._had_cleaning_phase:
                if not _looks_like_end:
                    self._end_signal_streak = 0
                    self._end_signal_first_ts = 0.0
                elif _ambiguous_end_phase:
                    if self._end_signal_streak == 0:
                        self._end_signal_first_ts = _time_mod.monotonic()
                    self._end_signal_streak += 1
                else:
                    # Unambiguous terminal phase (stop) — confirm immediately.
                    self._end_signal_streak = END_SIGNAL_DEBOUNCE_COUNT

            # v3.2.1 DOCK-ANCHOR — MUST run before the mission-end block
            # below, not after (this was a real bug in the first version
            # of this feature, caught before shipping): _handle_mission_end()
            # calls grid_store.update_from_mission(self._mission_points, ...)
            # — a SINGLE, one-shot feed of GridStore/RoomSegStore/
            # OutlineStore. If the dock-anchor correction ran AFTER that
            # call (as it did in the first version), the most important
            # case this whole mechanism exists for — a stuck-buffered
            # segment resolving exactly at the mission's final dock
            # contact — would have its correction applied only to the
            # live map, never reaching the stores at all for that
            # mission's contribution. Running first here guarantees
            # _mission_points already reflects the correction by the
            # time _handle_mission_end() reads it.
            #
            # v3.2.1 DOCK-ANCHOR — separate, simpler debounce than
            # _end_signal_streak: "confirmed at the dock" fires on ANY
            # SUSTAINED charge/hmPostMsn phase, whether the mission is
            # ending (Fall A/B, handled above too) or just a mid-mission
            # recharge (Fall B only, mission continues —
            # _handle_mission_end() is NOT called for this case, so
            # without this block Fall B would never be detected at all).
            #
            # Field-confirmed gap in the first version of this block: it
            # originally skipped the hold-time check, reasoning that
            # END_SIGNAL_MIN_HOLD_SECONDS only exists to decide "is this
            # really the END." Wrong — a real regression test
            # (TestImageEndDebounceV281, the exact ~21ms lewis-firmware
            # burst scenario) showed the hold-time ALSO filters out
            # transient firmware glitches reporting charge/hmPostMsn
            # during a normal room transition, which is not a dock
            # contact at all. Count alone can't tell "sustained" from
            # "coincidentally happened twice fast" — the hold-time is
            # required for both purposes, not just the first.
            if (
                self._map_capability == MapCapability.EPHEMERAL
                and current_phase in ROOM_TRANSITION_CANDIDATE_PHASES
                and _was_in_cleaning_phase_this_message
            ):
                if self._dock_contact_streak >= 0:
                    if self._dock_contact_streak == 0:
                        self._dock_contact_first_ts = _time_mod.monotonic()
                    self._dock_contact_streak += 1
                    if (
                        self._dock_contact_streak >= END_SIGNAL_DEBOUNCE_COUNT
                        and (_time_mod.monotonic() - self._dock_contact_first_ts)
                        >= END_SIGNAL_MIN_HOLD_SECONDS
                    ):
                        self._handle_dock_contact_confirmed()
                        # Sentinel -1: already handled this contact episode;
                        # re-arms to 0 only once phase leaves the contact
                        # set (see the else-branch below) — otherwise every
                        # subsequent message while simply parked charging
                        # would re-fire the (harmless but wasteful) handler.
                        self._dock_contact_streak = -1
            else:
                self._dock_contact_streak = 0
                self._dock_contact_first_ts = 0.0

            # v2.6.3 A — use _had_cleaning_phase so stuck → stop/charge
            # (stuck_and_abandoned) correctly triggers _handle_mission_end().
            if (
                current_phase in MISSION_END_PHASES
                and self._had_cleaning_phase
                and _looks_like_end
                and self._end_signal_streak >= END_SIGNAL_DEBOUNCE_COUNT
                and (
                    not _ambiguous_end_phase
                    or (
                        _time_mod.monotonic() - self._end_signal_first_ts
                        >= END_SIGNAL_MIN_HOLD_SECONDS
                    )
                )
            ):
                self._had_cleaning_phase = False
                self._end_signal_streak = 0
                self._end_signal_first_ts = 0.0
                self._handle_mission_end(current_phase)

        # Pose update — process regardless of phase so the map and direction
        # vector stay live even when the robot is stuck, returning, or
        # between phases.  Renderer reset (mission-start) and _handle_mission_end()
        # remain gated on phase transitions.
        if "pose" in state and self._renderer:
            self._handle_pose(state["pose"])

        # Stuck detection
        if "bbrun" in state and self._renderer:
            stuck = (self.vacuum_state.get("bbrun") or {}).get("nStuck", 0) or 0
            if stuck > self._last_stuck_count:
                self._renderer.mark_stuck()
                # Record stuck position in mm for GridStore
                if self._mission_points:
                    self._stuck_mission_points.append(self._mission_points[-1])
                # v3.2.1 DOCK-ANCHOR — EPHEMERAL only, matching the old
                # _check_dock_drift block's own established scoping
                # (field-confirmed gap: this check was originally
                # missing entirely). SMART robots get authoritative room
                # data from the cloud's own persistent, self-correcting
                # map — GridStore/RoomSegStore/OutlineStore (the actual
                # beneficiaries of this correction) are themselves
                # EPHEMERAL-only constructs, so buffering/correcting a
                # SMART robot's local _mission_points would fix data
                # nothing downstream consumes, while still doing
                # unnecessary live-map replace_range() work and feeding
                # dock_theta_baseline/geometry_store.record_drift() for
                # a robot whose vSLAM-continuity story is different
                # (persistent cloud map, not a fresh-per-mission local
                # reconstruction).
                if self._map_capability == MapCapability.EPHEMERAL:
                    # v3.2.1 DOCK-ANCHOR — enter BUFFERING for the rest of
                    # this mission (or until a confirmed dock contact, see
                    # _handle_dock_contact_confirmed). See
                    # Dock_Anchor_Korrektur_Plan.md for the full rationale
                    # (vSLAM continuity risk after a likely pickup).
                    self._dock_anchor_buffering = True
                # v2.8.2 — checkpoint the in-progress mission. A stuck event
                # is exactly the moment a mission is most at risk of never
                # reaching a clean end (HA restart, manual intervention) —
                # see _async_save_mission_checkpoint() docstring.
                if self._config_entry is not None and self._had_cleaning_phase:
                    asyncio.run_coroutine_threadsafe(
                        self._async_save_mission_checkpoint(), self.hass.loop
                    )
            self._last_stuck_count = stuck

        self.schedule_update_ha_state()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_pose(self, pose: dict[str, Any]) -> None:
        """Add pose point and signal frontend to re-fetch image.

        v2.9.0 — firmware reports pose.point.x/y in CENTIMETRES, not
        millimetres (confirmed from real field data; see POSE_POINT_CM_TO_MM
        in const.py for the full rationale). Converted here, at the single
        point this value first enters the system, so every downstream
        consumer (MapRenderer, self._mission_points -> GridStore/ZoneStore/
        OutlineStore) receives genuine millimetres and needs no changes.

        v3.2.1 DOCK-ANCHOR — while self._dock_anchor_buffering is True (a
        stuck event occurred and no confirmed dock contact has resolved
        it yet), points go into _pending_segment_points/_thetas instead
        of _mission_points/_mission_thetas — buffered for retroactive
        correction (see _handle_dock_contact_confirmed), not discarded
        outright as the old flag-based version did. The live MapRenderer
        visual is deliberately NOT frozen — add_pose() still runs
        unconditionally, so the on-screen path keeps showing what
        actually happened (troubleshooting value) until the buffered
        segment is corrected and merged in place.
        """
        point = pose.get("point", {})
        # v3.2.1 AXIS-SWAP FIX — roombapy's own source (roomba.py) does
        # `pose_point_x -> co_ords["y"]`, `pose_point_y -> co_ords["x"]`
        # ("# x and y are reversed..."), matching add_pose()'s own
        # long-standing docstring claim ("roombapy convention: co_ords['x']
        # = pose_point_y"). This code never actually applied that swap —
        # confirmed, independent bug from the raw firmware fields to this
        # single entry point. Tested in isolation (visualised trajectory)
        # and confirmed NOT to be the explanation for the "live map
        # doesn't match real room layout" symptom investigated this
        # session — that was the vSLAM continuity loss after stuck
        # events, fixed separately via the Dock-Anchor-Korrektur
        # mechanism. Fixed here anyway because it is a real, independently
        # confirmed discrepancy from the documented convention, not
        # because it explains that symptom.
        #
        # BREAKING DISCONTINUITY, not a silent one-line fix: every
        # downstream consumer of x/y (MapRenderer, GridStore, RoomSegStore,
        # OutlineStore, MissionTrajectoryStore) has been accumulating data
        # under the OLD (unswapped) axis meaning. Data recorded before
        # this fix and data recorded after it do NOT represent the same
        # physical directions — old and new history will not spatially
        # align if mixed. Combined with the already-recommended "fresh
        # start" for GridStore/RoomSegStore (stuck-event contamination
        # predating the Dock-Anchor-Korrektur, see that plan) rather than
        # attempting a coordinate-transform migration of old data.
        x = float(point.get("y", 0)) * POSE_POINT_CM_TO_MM
        y = float(point.get("x", 0)) * POSE_POINT_CM_TO_MM
        theta = float(pose.get("theta", 0))
        # v3.2.1 DOCK-ANCHOR — the very FIRST pose reading of a mission
        # (x=y=0, robot still literally on the dock before departure) is
        # arguably the CLEANEST possible dock_theta_baseline sample: the
        # robot is certainly at the dock, certainly stationary, and this
        # is before any stuck event or disturbance could have occurred
        # this mission — better grounds for "clean" than even a Fall B
        # recharge-return sample. MapRenderer.add_pose() already skips
        # this exact point for its own (unrelated) reasons, discarding
        # the theta entirely; captured here instead, roughly doubling
        # the sampling rate for dock_theta_baseline maturation (once at
        # start, once at end/recharge, per mission).
        if (
            self._map_capability == MapCapability.EPHEMERAL
            and x == 0.0 and y == 0.0
            and not self._dock_anchor_buffering
            and not self._mission_points
        ):
            if self._config_entry is not None:
                data = getattr(self._config_entry, "runtime_data", None)
                robot_profile_store = getattr(data, "robot_profile_store", None) if data else None
                if robot_profile_store is not None:
                    robot_profile_store.update_dock_theta_baseline(theta)
        if self._renderer:
            # v3.2.1 DOCK-ANCHOR — return value (accepted-jump flag) not
            # yet consumed here: confidence-weighting by internal jump
            # position was deliberately deferred (see
            # Dock_Anchor_Korrektur_Plan.md 4c) pending real field
            # validation that simple linear interpolation isn't enough.
            self._renderer.add_pose(x, y, theta)
        if self._dock_anchor_buffering:
            self._pending_segment_points.append((x, y))
            self._pending_segment_thetas.append(theta)
        else:
            self._mission_points.append((x, y))
            self._mission_thetas.append(theta)
        self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)

    def _handle_dock_contact_confirmed(self) -> None:
        """v3.2.1 DOCK-ANCHOR — fires once per confirmed dock contact
        (debounced in _on_message), whether the mission is ending or
        just recharging mid-mission (Fall B). See
        Dock_Anchor_Korrektur_Plan.md for the full design.

        Fall A (self._dock_anchor_buffering True): a stuck event
        happened earlier this mission and has not yet been resolved.
        The buffered segment is corrected (interpolated, see
        _interpolate_and_correct_segment) and merged into
        _mission_points/_mission_thetas — rescued instead of discarded.
        dock_theta_baseline is NOT fed from this contact: it followed a
        disturbance, not a clean docking (see RobotProfileStore.
        update_dock_theta_baseline's docstring).

        Fall B (not buffering): a normal, undisturbed dock contact
        (recharge-and-resume, or a clean mission end). No buffering
        needed — directly correct the segment since the last dock
        anchor. This IS a clean contact, so it feeds
        dock_theta_baseline.

        Live-map correction (MapRenderer.replace_range) is a best-effort
        approximation: MapRenderer's own point list can be shorter than
        _mission_points/_pending_segment_points (it silently drops
        implausible-jump points that image.py's unfiltered pose stream
        still recorded) — there is no guaranteed 1:1 index
        correspondence between the two. Replacing MapRenderer's last N
        points (N = corrected segment length) is therefore an
        approximation, not an exact replay; acceptable because rejected
        jumps are rare and the live map is a visual aid, not a data
        source GridStore/RoomSeg/Outline depend on.
        """
        # v3.2.1 DOCK-ANCHOR — defensive belt-and-suspenders: the caller
        # (the dock-contact debounce block) already gates on EPHEMERAL,
        # so this should never actually be reached for a SMART robot in
        # practice — kept anyway so a future refactor that calls this
        # method from a new call site can't silently reintroduce the
        # SMART-robot gap fixed here (see the buffering-entry gate for
        # the full rationale).
        if self._map_capability != MapCapability.EPHEMERAL:
            return
        robot_profile_store = None
        if self._config_entry is not None:
            data = getattr(self._config_entry, "runtime_data", None)
            robot_profile_store = getattr(data, "robot_profile_store", None) if data else None

        dock_theta_baseline = None
        if robot_profile_store is not None and robot_profile_store.dock_theta_baseline_ready:
            dock_theta_baseline = robot_profile_store.dock_theta_baseline

        if self._dock_anchor_buffering:
            segment = self._pending_segment_points
            thetas = self._pending_segment_thetas
            is_clean_contact = False
        else:
            segment = self._mission_points[self._last_dock_anchor_index:]
            thetas = self._mission_thetas[self._last_dock_anchor_index:]
            is_clean_contact = True

        if segment:
            measured_final_pos = segment[-1]
            measured_final_theta = thetas[-1] if thetas else 0.0
            dx, dy, rotation_rad = _compute_dock_correction(
                measured_final_pos, measured_final_theta, dock_theta_baseline,
            )
            corrected_points = _interpolate_and_correct_segment(segment, dx, dy, rotation_rad)
            rotation_deg = math.degrees(rotation_rad)
            n = len(thetas)
            corrected_thetas = [
                (t + rotation_deg * (i / (n - 1) if n > 1 else 1.0)) % 360.0
                for i, t in enumerate(thetas)
            ]

            if self._dock_anchor_buffering:
                self._mission_points.extend(corrected_points)
                self._mission_thetas.extend(corrected_thetas)
            else:
                self._mission_points[self._last_dock_anchor_index:] = corrected_points
                self._mission_thetas[self._last_dock_anchor_index:] = corrected_thetas

            if self._renderer is not None:
                start_index = max(0, self._renderer.point_count - len(segment))
                self._renderer.replace_range(start_index, corrected_points)

            if is_clean_contact and robot_profile_store is not None:
                robot_profile_store.update_dock_theta_baseline(measured_final_theta)

            # v3.2.1 DOCK-ANCHOR — consolidates the old, disconnected
            # _check_dock_drift()-only diagnostic (pure logging, no
            # correction applied) into this single place that now both
            # detects AND corrects. GeometryStore.record_drift() keeps
            # its existing Repair-Issue-triggering behaviour, fed from
            # the SAME correction vector this method just applied.
            if self._config_entry is not None:
                data = getattr(self._config_entry, "runtime_data", None)
                geometry_store = getattr(data, "geometry_store", None) if data else None
                if geometry_store is not None and (dx, dy) != (0.0, 0.0):
                    threshold_exceeded = geometry_store.record_drift(dx, dy)
                    if threshold_exceeded:
                        # v3.2.1 field-fix — self.hass.loop, not
                        # asyncio.get_event_loop(): this callback runs on
                        # roombapy's paho-MQTT thread (see
                        # _handle_mission_end's own "loop = self.hass.loop"
                        # a few lines below for the established pattern),
                        # not the HA event loop thread — get_event_loop()
                        # there is not guaranteed to return the same loop
                        # HA actually runs on.
                        asyncio.run_coroutine_threadsafe(
                            self._trigger_drift_issue_enriched(dx, dy), self.hass.loop,
                        )
                    # v3.2.1 field-fix — this save call was missing
                    # entirely in the first version: the old
                    # _check_dock_drift block always persisted
                    # geometry_store after recording a drift sample, and
                    # this new mechanism must too, or a HA restart right
                    # after a correction would silently lose the
                    # updated cumulative_drift_mm/recent_drifts_mm.
                    asyncio.run_coroutine_threadsafe(
                        geometry_store.async_save(self.hass, self._config_entry.entry_id),
                        self.hass.loop,
                    )

        self._dock_anchor_buffering = False
        self._pending_segment_points = []
        self._pending_segment_thetas = []
        self._last_dock_anchor_index = len(self._mission_points)
        # v3.2.1 DOCK-ANCHOR — checkpoint right after a successful
        # resolution too, not just at the stuck event that started
        # buffering. Without this, an HA restart between a correction
        # and the NEXT stuck event would restore the STALE pre-
        # resolution checkpoint — reverting _dock_anchor_buffering back
        # to True with the original, now-superseded pending segment,
        # and losing whatever _mission_points accumulated afterward.
        if self._config_entry is not None and self._had_cleaning_phase:
            asyncio.run_coroutine_threadsafe(
                self._async_save_mission_checkpoint(), self.hass.loop
            )

    def _handle_mission_end(self, ending_phase: str = "") -> None:
        # Called from roombapy's paho-MQTT thread — NOT the HA event loop.
        # hass.async_create_task() is not thread-safe and raises RuntimeError
        # on recent HA versions when called from a foreign thread.
        # All coroutine scheduling must go through asyncio.run_coroutine_threadsafe().
        loop = self.hass.loop

        # v2.8.2 bug-hunt fix — checkpoint clearing must happen unconditionally,
        # before the "nothing to process" early-return below. A checkpoint can
        # legitimately have empty mission_points (e.g. a stuck event fired
        # before any pose message had ever arrived this mission), and
        # _salvage_orphaned_checkpoint() loads exactly that into
        # self._mission_points before calling this method. With the clear
        # call previously placed after the early-return, that specific
        # checkpoint would never be deleted — it would be reloaded and
        # re-"salvaged" (a no-op) on every subsequent HA restart forever.
        # Store.async_remove() is a safe no-op when nothing is persisted, so
        # this is harmless on the (overwhelmingly common) normal-end path
        # where no checkpoint exists at all.
        if self._config_entry is not None:
            asyncio.run_coroutine_threadsafe(
                self._async_clear_mission_checkpoint(), loop
            )

        if not self._mission_points:
            return

        # v3.2.1 DOCK-ANCHOR — CONSOLIDATED (previously a KNOWN
        # REDUNDANCY, see Dock_Anchor_Korrektur_Plan.md Abschnitt 7
        # Punkt 1). This block used to independently recompute a drift
        # vector via _check_dock_drift() and call record_drift() —
        # exactly what _handle_dock_contact_confirmed() now does, and
        # (since the ordering fix) already does BEFORE this method runs.
        # By the time we reach here, self._mission_points[-1] is already
        # corrected (pulled to ~(0,0)) whenever a dock-anchor correction
        # applied — recomputing drift on that already-corrected point
        # would almost always find nothing (redundant at best). Detection
        # + correction + Repair-Issue-triggering is now solely
        # _handle_dock_contact_confirmed()'s responsibility. What remains
        # genuinely independent — and is kept — is the periodic
        # self-healing check below: it reads geometry_store's own
        # accumulated history, not anything this block itself computes.
        _dock_return = ending_phase in {"charge", "hmPostMsn"}

        if (self._map_capability == MapCapability.EPHEMERAL
                and _dock_return
                and len(self._mission_points) >= 20):
            data = self._config_entry.runtime_data
            if data.geometry_store:
                # v3.1.0 DRIFT-AUTO — self-healing check, independent of
                # this mission's own drift (already handled elsewhere,
                # see above). Recovery uses a lower hysteresis threshold
                # than the trigger, so the issue doesn't flap right at
                # the boundary.
                if data.geometry_store.drift_recovered():
                    asyncio.run_coroutine_threadsafe(
                        self._clear_drift_issue(), loop
                    )
                asyncio.run_coroutine_threadsafe(
                    data.geometry_store.async_save(self.hass, self._config_entry.entry_id),
                    loop,
                )

        # v2.4.2 GS-SMART — accumulate door-crossing markers for SMART robots.
        # SMART robots have no ZoneStore, so gap detection runs directly on
        # the accumulated pose trajectory using the same constants as ZoneStore.
        # Must be an elif so the EPHEMERAL block above (which already calls
        # update_from_midpoints via update_from_mission) does not double-write.
        elif (
            self._map_capability == MapCapability.SMART
            and self._config_entry is not None
            and len(self._mission_points) >= 20
        ):
            _data = self._config_entry.runtime_data
            if _data.geometry_store:
                _midpoints: list[tuple[float, float]] = []
                _pts = self._mission_points
                for _i in range(len(_pts) - 1):
                    _dist = math.hypot(
                        _pts[_i + 1][0] - _pts[_i][0],
                        _pts[_i + 1][1] - _pts[_i][1],
                    )
                    if _dist > GAP_THRESHOLD_MM and MIN_DOOR_WIDTH_MM <= _dist <= MAX_DOOR_WIDTH_MM:
                        _midpoints.append((
                            (_pts[_i][0] + _pts[_i + 1][0]) / 2.0,
                            (_pts[_i][1] + _pts[_i + 1][1]) / 2.0,
                        ))
                _LOGGER.debug(
                    "Map: SMART path — %d door gap midpoint(s) from %d pose points",
                    len(_midpoints), len(self._mission_points),
                )
                if _midpoints:
                    _data.geometry_store.update_from_midpoints(_midpoints)
                    asyncio.run_coroutine_threadsafe(
                        _data.geometry_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )

        # Persist renderer state so the map survives an HA restart
        if self._renderer and self._renderer.has_data:
            asyncio.run_coroutine_threadsafe(self._async_save_map_state(), loop)

        # F-EPHEMERAL — Room outline recompute moved AFTER the GridStore
        # update below (v3.2.1 redesign): it now derives directly from
        # GridStore.cells, so it must run once that store already
        # includes this just-finished mission's cells. See the block
        # after "Update GridStore for coverage heatmap".

        # Update GridStore for coverage heatmap (all pose-capable robots)
        if self._config_entry is not None and self._mission_points:
            _gdata = self._config_entry.runtime_data
            if _gdata.grid_store is not None:
                # L7 (v2.7.0): compute local (weekday, hour) from mission start here
                # so grid_store.py stays HA-free (no homeassistant imports).
                _stuck_wh: tuple[int, int] | None = None
                _start_ts = getattr(self, "_mission_start_ts", None)
                if _start_ts:
                    try:
                        _parsed = dt_util.parse_datetime(_start_ts)
                        if _parsed is not None:
                            _local = dt_util.as_local(_parsed)
                            _stuck_wh = (_local.weekday(), _local.hour)
                    except Exception:  # noqa: BLE001
                        pass
                _gdata.grid_store.update_from_mission(
                    self._mission_points,
                    self._stuck_mission_points,
                    stuck_wh=_stuck_wh,
                    # v2.9.0 (DISK-FILL) — mark each pose point's actual
                    # swept footprint, not just its single centre cell.
                    # _cfg.robot_diameter_mm is already set correctly per
                    # robot tier (see __init__.py's map_capability-based
                    # selection) — grid_store.py stays HA-free by taking
                    # a plain float here rather than importing the
                    # tier-detection logic itself.
                    robot_radius_mm=self._renderer._cfg.robot_diameter_mm / 2,
                )
                # v3.4.0 GS-SMART-COVERAGE — stamp this mission's nMssn as
                # "already fed into GridStore via the live path". Shared
                # watermark with the cloud-backfill path (callbacks.py):
                # whichever path processes a mission first claims it here,
                # so the other path's candidate filter skips it — this is
                # the actual real-pose robots' path, so it runs (and
                # therefore claims the mission) BEFORE any cloud refresh
                # would otherwise re-process the same mission from UMF
                # data. mission_stats comes from IRobotEntity (entity.py).
                _gdata.grid_store.record_processed_nmssn(
                    self.mission_stats.get("nMssn")
                )
                asyncio.run_coroutine_threadsafe(
                    _gdata.grid_store.async_save(
                        self.hass, self._config_entry.entry_id
                    ),
                    loop,
                )

                # v3.2.1 FIELD FIX — outline recompute's PURE, synchronous
                # half runs HERE, right after GridStore has this mission's
                # cells, unconditionally (matches the prior always-run
                # behaviour, not gated on room-seg's _recomputed below).
                # Moved out of the async-only path further down: anything
                # reading outline_store.contour_points later in THIS same
                # mission (the freeze-snapshot block below, inside the
                # room-seg branch) needs the fresh value available
                # immediately, not after an async_recompute() coroutine
                # merely gets SCHEDULED via run_coroutine_threadsafe — a
                # scheduled coroutine is not a completed one. Field-
                # confirmed gap: the very first FreezeSnapshotStore
                # snapshot captured outline_points=0 for exactly this
                # reason. Persistence (async_save) still happens further
                # down, unchanged in spirit — see recompute_sync()'s
                # docstring in outline_store.py for the full rationale.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.outline_store is not None
                ):
                    _gdata.outline_store.recompute_sync(_gdata.grid_store.cells)

                # ROOM-SEG — recompute room/door segmentation from the
                # just-updated GridStore. EPHEMERAL only (SMART robots get
                # authoritative room data from the cloud already). Runs
                # synchronously on THIS thread, not the event loop — this
                # whole method is already off-loop (see the
                # asyncio.run_coroutine_threadsafe calls throughout), so
                # the CPU-bound segmentation work here doesn't block HA.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.room_seg_store is not None
                ):
                    _unconfirmed_before = len(_gdata.room_seg_store.unconfirmed_rooms)
                    _recomputed = _gdata.room_seg_store.maybe_recompute(
                        _gdata.grid_store.cells
                    )
                    if _recomputed:
                        # ROOM-SEG — fire the same naming-wizard Repair Issue
                        # ZoneStore used to trigger, only when the count of
                        # unconfirmed rooms actually grew (mirrors `if
                        # new_zones:` above — a fresh genuinely-new room was
                        # found, not just an existing unconfirmed one
                        # persisting across this recompute).
                        if len(_gdata.room_seg_store.unconfirmed_rooms) > _unconfirmed_before:
                            asyncio.run_coroutine_threadsafe(self._trigger_zone_issue(), loop)
                        asyncio.run_coroutine_threadsafe(
                            _gdata.room_seg_store.async_save(
                                self.hass, self._config_entry.entry_id
                            ),
                            loop,
                        )
                        # ROOM-SEG — sync GeometryStore's door_markers from
                        # the just-recomputed RoomSegStore.doors, replacing
                        # the old zone_store-fed update_from_mission() path
                        # (gap heuristic, unreliable — see
                        # ROOM_SEGMENTATION_NOTES.md). Only when rooms
                        # actually changed this mission, same gating as the
                        # recompute itself above.
                        if _gdata.geometry_store is not None:
                            _gdata.geometry_store.update_from_room_seg_store(
                                _gdata.room_seg_store
                            )
                            asyncio.run_coroutine_threadsafe(
                                _gdata.geometry_store.async_save(
                                    self.hass, self._config_entry.entry_id
                                ),
                                loop,
                            )

                        # v3.2.1 — FreezeSnapshotStore: count this
                        # successful recompute, and if the interval is due,
                        # capture the current RoomSeg + Outline state into
                        # the immutable backup. Uses whatever
                        # outline_store.contour_points currently holds —
                        # doesn't need to be perfectly in sync with the
                        # async outline recompute below, "good enough" is
                        # the point of a periodic insurance snapshot, not
                        # a live mirror. See freeze_snapshot_store.py
                        # docstring for the firmware-cutoff rationale.
                        if _gdata.freeze_snapshot_store is not None:
                            _gdata.freeze_snapshot_store.note_recompute()
                            if _gdata.freeze_snapshot_store.due():
                                _outline_pts = (
                                    _gdata.outline_store.contour_points
                                    if _gdata.outline_store is not None
                                    else []
                                )
                                _gdata.freeze_snapshot_store.snapshot(
                                    [r.to_dict() for r in _gdata.room_seg_store.rooms.values()],
                                    [d.to_dict() for d in _gdata.room_seg_store.doors],
                                    _outline_pts,
                                    dt_util.now().isoformat(),
                                )
                                asyncio.run_coroutine_threadsafe(
                                    _gdata.freeze_snapshot_store.async_save(
                                        self.hass, self._config_entry.entry_id
                                    ),
                                    loop,
                                )

                # F-EPHEMERAL — Room outline (v3.2.1 redesign): recompute
                # from the same just-updated GridStore.cells room-seg reads
                # above, unconditionally (not gated on _recomputed — the
                # outline is a cheap pure dict pass, unlike room-seg's
                # watershed pipeline, so there's no cost reason to skip it
                # on missions where segmentation itself didn't change).
                # v3.2.1 — persistence only: the actual recompute (contour
                # points + mission_count) already happened synchronously
                # right after the GridStore update above, unconditionally,
                # so this just needs to save that already-current state —
                # NOT call async_recompute() again, which would recompute
                # (harmless) but ALSO increment mission_count a second
                # time for the same mission (not harmless: would silently
                # double-count mission_count against MIN_MISSIONS_TO_SHOW).
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.outline_store is not None
                ):
                    asyncio.run_coroutine_threadsafe(
                        _gdata.outline_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )

                # v3.2.1 — MissionTrajectoryStore: record this mission's raw
                # pose points (same self._mission_points GridStore just
                # consumed above) into the bounded last-N-missions window,
                # BEFORE they're cleared below. Same EPHEMERAL gate as
                # OutlineStore — see mission_trajectory_store.py docstring.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.trajectory_store is not None
                ):
                    _mission_key = str(
                        getattr(self, "_mission_start_ts", "") or ""
                    )
                    _gdata.trajectory_store.record_mission(
                        _mission_key, self._mission_points,
                        thetas_deg=self._mission_thetas,
                    )
                    asyncio.run_coroutine_threadsafe(
                        _gdata.trajectory_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )
                _LOGGER.debug(
                    "GridStore: updated from mission — %d pose pts, %d stuck pts",
                    len(self._mission_points), len(self._stuck_mission_points),
                )
                # v2.6.3 E — notify RoombaCoverageImage so it bumps its
                # image_last_updated timestamp and the frontend re-fetches.
                _eid = self._config_entry.entry_id
                asyncio.run_coroutine_threadsafe(
                    _async_send_coverage_signal(self.hass, _eid),
                    loop,
                )

        self._mission_points = []
        self._mission_thetas = []
        self._stuck_mission_points = []
        # v3.2.1 DOCK-ANCHOR — mission has now genuinely ended; any
        # still-buffered segment (stuck_and_abandoned, no dock contact
        # ever confirmed) is discarded here, same as the mission-start
        # reset does for the next mission. Harmless to reset both places
        # — whichever fires first for a given mission wins, the other is
        # a no-op on already-empty state.
        self._dock_anchor_buffering = False
        self._pending_segment_points = []
        self._pending_segment_thetas = []
        self._last_dock_anchor_index = 0
        self._dock_contact_streak = 0
        self._dock_contact_first_ts = 0.0
        self._mission_start_ts = None

    async def _async_save_map_state(self) -> None:
        """Write renderer state to hass.storage after mission end."""
        if not self._renderer:
            return
        store = Store(
            self.hass,
            _MAP_STORAGE_VERSION,
            _map_storage_key(self._config_entry.entry_id),
        )
        await store.async_save(self._renderer.dump_state())
        _LOGGER.debug(
            "Map: saved %d points to storage", self._renderer.point_count
        )

    async def _async_restore_map_state(self) -> None:
        """Load renderer state from hass.storage on startup.

        If no stored state exists, or if it is incompatible, the renderer
        starts blank — nothing crashes, the user just sees an empty map until
        the next mission completes.
        """
        if not self._renderer:
            return
        store = Store(
            self.hass,
            _MAP_STORAGE_VERSION,
            _map_storage_key(self._config_entry.entry_id),
        )
        try:
            data = await store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Map: failed to load stored state: %s", exc)
            return

        if not data:
            _LOGGER.debug("Map: no stored state found")
            return

        if self._renderer.restore_state(data):
            # Bump image_last_updated so the frontend fetches the restored image
            self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
            _LOGGER.debug(
                "Map: restored %d points from storage",
                self._renderer.point_count,
            )
        else:
            _LOGGER.warning("Map: stored state was incompatible, starting blank")

    # ── Mission checkpoint (v2.8.2) ──────────────────────────────────────────
    #
    # Distinct from the renderer's _async_save_map_state()/_async_restore_map_state()
    # above, which only ever runs at a clean mission end. These four methods
    # protect against the case that matters most for a robot with a high
    # mission-failure rate: a mission that gets stuck and never reaches a
    # clean end (HA restart, manual intervention) before that happens.

    async def _async_load_pending_checkpoint(self) -> None:
        """Load a mission checkpoint (if any) at startup.

        Does not apply it yet — self._pending_checkpoint is only resolved
        (resumed or salvaged) once the first live MQTT message arrives and
        we know the robot's current mssnStrtTm/phase. See
        _consume_pending_checkpoint().
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        try:
            data = await store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Map: failed to load mission checkpoint: %s", exc)
            return
        if data:
            self._pending_checkpoint = data
            _LOGGER.debug(
                "Map: loaded pending mission checkpoint — %d pose pt(s)",
                len(data.get("mission_points", [])),
            )

    def _consume_pending_checkpoint(self) -> None:
        """Resolve self._pending_checkpoint against the first live message.

        Called at most once per entity lifetime — always sets
        self._pending_checkpoint back to None, whichever branch is taken,
        so a checkpoint is either resumed seamlessly (no extra
        process_mission()-style call) or salvaged exactly once. Never both,
        which would double-count the same trajectory data.

        Same mission still running (mssnStrtTm matches) -> resume: restore
        _mission_points/_stuck_mission_points/_mission_start_ts/
        _last_stuck_count and the renderer state, set
        _had_cleaning_phase=True so the normal "mission started" reset
        below this call becomes a no-op for this message.

        v2.8.2 bug-hunt fix — deliberately does NOT also require
        current_phase to be an actively-cleaning phase. mssnStrtTm matching
        already proves this is the same physical mission (980/900-series
        firmware does not reset it mid-mission); requiring CLEANING_PHASES
        on top of that meant landing on an ordinary inter-room transition
        blip (current_phase == "charge"/"hmPostMsn", not yet confirmed as a
        genuine end) as the very first post-restart message would wrongly
        treat a still-running mission as ended and salvage it — fragmenting
        one continuous mission into an orphaned piece plus a fresh restart,
        exactly the kind of fragmentation this whole feature exists to
        prevent. Resuming unconditionally on a mssnStrtTm match instead lets
        the normal phase-transition / END-DEBOUNCE logic below — which runs
        immediately after, against this same message — correctly decide
        what to do with whatever phase we're actually in: keep going if
        still cleaning, or end correctly (with the real ending_phase, e.g.
        for accurate dock-return/drift detection) if it turns out the
        mission genuinely did conclude while HA was down.

        Otherwise (different mission already started, or mssnStrtTm absent
        from either side) -> orphaned -> salvage once through the same
        store-feeding logic a normal mission end uses, so the data isn't
        silently lost.
        """
        checkpoint = self._pending_checkpoint
        self._pending_checkpoint = None
        if checkpoint is None:
            return

        live_mssn_strt_tm = (
            (self.vacuum_state.get("cleanMissionStatus") or {}).get("mssnStrtTm") or 0
        )
        checkpoint_mssn_strt_tm = checkpoint.get("mssn_strt_tm") or 0

        same_mission_still_active = (
            bool(live_mssn_strt_tm)
            and bool(checkpoint_mssn_strt_tm)
            and live_mssn_strt_tm == checkpoint_mssn_strt_tm
        )

        if same_mission_still_active:
            self._mission_points = list(checkpoint.get("mission_points", []))
            self._mission_thetas = list(checkpoint.get("mission_thetas", []))
            self._stuck_mission_points = list(checkpoint.get("stuck_mission_points", []))
            self._mission_start_ts = checkpoint.get("mission_start_ts")
            self._mission_checkpoint_mssn_strt_tm = live_mssn_strt_tm
            self._had_cleaning_phase = True
            # v2.8.2 bug-hunt fix — see _async_save_mission_checkpoint()
            # docstring for why this must be restored, not left at the
            # post-__init__ default of 0.
            self._last_stuck_count = checkpoint.get("last_stuck_count", 0)
            # v3.2.1 DOCK-ANCHOR — restore the full buffering state, not
            # just a boolean: a stuck event before the HA restart must
            # resume with its buffered segment intact, not silently lose
            # it (which discarding it here would do — worse than the old
            # flag-only version, which at least didn't have data to lose).
            self._dock_anchor_buffering = checkpoint.get("dock_anchor_buffering", False)
            self._pending_segment_points = list(checkpoint.get("pending_segment_points", []))
            self._pending_segment_thetas = list(checkpoint.get("pending_segment_thetas", []))
            self._last_dock_anchor_index = checkpoint.get("last_dock_anchor_index", 0)
            renderer_state = checkpoint.get("renderer_state")
            if self._renderer is not None and renderer_state:
                self._renderer.restore_state(renderer_state)
            _LOGGER.debug(
                "Map: resumed mission from checkpoint after restart — "
                "%d pose pt(s), %d stuck pt(s)",
                len(self._mission_points), len(self._stuck_mission_points),
            )
        else:
            _LOGGER.debug(
                "Map: checkpoint orphaned (mission ended or changed while "
                "HA was down) — salvaging %d pose pt(s)",
                len(checkpoint.get("mission_points", [])),
            )
            self._salvage_orphaned_checkpoint(checkpoint)

    def _salvage_orphaned_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Process an orphaned checkpoint exactly once via _handle_mission_end().

        Reuses _handle_mission_end() directly by temporarily loading the
        checkpoint's data into self._mission_points etc. — safe because at
        this point (entity just started, before the current message's own
        phase-transition handling has run) those attributes are still at
        their fresh __init__ defaults. _handle_mission_end() always clears
        them back to empty/None at the end and also deletes the now
        -consumed checkpoint file, so no explicit cleanup is needed here.

        ending_phase="" — matching the existing stuck-and-abandoned
        ("stop") case, since we don't actually know whether this mission
        made it back to the dock before HA went down. _dock_return inside
        _handle_mission_end() is False for any phase outside
        {"charge", "hmPostMsn"}, so drift detection is correctly skipped.
        """
        self._mission_points = list(checkpoint.get("mission_points", []))
        self._mission_thetas = list(checkpoint.get("mission_thetas", []))
        self._stuck_mission_points = list(checkpoint.get("stuck_mission_points", []))
        self._mission_start_ts = checkpoint.get("mission_start_ts")
        renderer_state = checkpoint.get("renderer_state")
        if self._renderer is not None and renderer_state:
            self._renderer.restore_state(renderer_state)
        self._handle_mission_end(ending_phase="")

    async def _async_save_mission_checkpoint(self) -> None:
        """Persist the in-progress mission so a stuck-then-interrupted
        mission doesn't silently lose its accumulated exploration data.

        Idempotent — safe to call repeatedly during the same mission (e.g.
        on every stuck event); each call overwrites the previous checkpoint
        for this config entry in place. Does NOT feed ZoneStore/GeometryStore
        /GridStore/OutlineStore — those still only run once, at a genuine
        mission end (normal or salvaged), to avoid double-counting.
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        await store.async_save({
            "mssn_strt_tm": self._mission_checkpoint_mssn_strt_tm,
            "mission_points": list(self._mission_points),
            "mission_thetas": list(self._mission_thetas),
            "stuck_mission_points": list(self._stuck_mission_points),
            "mission_start_ts": self._mission_start_ts,
            "renderer_state": self._renderer.dump_state() if self._renderer else None,
            # v2.8.2 bug-hunt fix — without this, a resumed mission would
            # see _last_stuck_count reset to its __init__ default of 0 (the
            # whole entity object is recreated on HA restart), so the very
            # next bbrun message with the robot's already-known nStuck count
            # would look like a brand-new stuck event (n > 0) and append a
            # spurious duplicate marker to _stuck_mission_points.
            "last_stuck_count": self._last_stuck_count,
            # v3.2.1 DOCK-ANCHOR — without this, a resumed mission after
            # an HA restart would lose an in-progress buffered segment
            # entirely (worse than the old flag-only version, which at
            # least had no data to lose) — a stuck event followed
            # immediately by an HA restart would silently discard
            # everything recorded since, instead of resuming buffering
            # and still being able to correct it at the next dock
            # contact.
            "dock_anchor_buffering": self._dock_anchor_buffering,
            "pending_segment_points": list(self._pending_segment_points),
            "pending_segment_thetas": list(self._pending_segment_thetas),
            "last_dock_anchor_index": self._last_dock_anchor_index,
        })
        _LOGGER.debug(
            "Map: saved mission checkpoint — %d pose pt(s), %d stuck pt(s)",
            len(self._mission_points), len(self._stuck_mission_points),
        )

    async def _async_clear_mission_checkpoint(self) -> None:
        """Delete the mission checkpoint — it is now redundant.

        Called from _handle_mission_end(), which covers both a normal
        mission end (the authoritative, complete processing just ran) and
        a salvage call (the checkpoint was just consumed and fed through
        the same method).
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        await store.async_remove()

    async def _trigger_zone_issue(self) -> None:
        from homeassistant.components import repairs as ir
        ir.async_create_issue(
            self.hass, DOMAIN, "zones_need_naming",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="zones_need_naming",
        )

    async def _trigger_drift_issue_enriched(self, dx: float, dy: float) -> None:
        """F6d -- fire the map_drift_detected event with bearing/magnitude
        enrichment. v3.5.0 Repairs redesign: demoted from Repair Issue to
        event — DRIFT-AUTO's own self-healing design already treats this as
        transient (see drift_recovered() below), which fits an event/
        Logbook model better than a persistent, must-dismiss Repair."""
        from .repairs import async_enrich_drift_issue
        await async_enrich_drift_issue(self.hass, self._config_entry, dx=dx, dy=dy)

    async def _clear_drift_issue(self) -> None:
        """v3.1.0 DRIFT-AUTO — self-healing: re-arm the map_drift_detected
        event once the recent drift window's mean has dropped back under
        the recovery threshold, so the next fresh occurrence fires again.
        """
        from .repairs import _disarm
        _disarm(self._config_entry.entry_id, "map_drift_detected")

    @staticmethod
    def _blank_image() -> bytes:
        from PIL import Image
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class RoombaCoverageImage(IRobotEntity, ImageEntity):
    """GridStore occupancy grid heatmap — updated at mission end.

    F9 — renders the EMA-weighted GridStore as a PNG heatmap.
    Dark blue = high EMA (frequently visited), light = rarely visited,
    red overlay = stuck hotspot cells.

    EMA diagnostic attributes are exposed during the v2.2 validation period
    to allow users and developers to verify constants are appropriate for their
    cleaning frequency.

    Gate: registered only when data.grid_store is not None (controlled by
    __init__.py — only for map_capability != NONE with map enabled).
    """

    _attr_translation_key = "coverage_map"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_content_type = "image/png"

    def __init__(
        self,
        roomba: Any,
        blid: str,
        grid_store: GridStore,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)
        self._cache: bytes | None = None
        self.access_tokens: collections.deque = collections.deque([], 2)

        self._grid_store = grid_store
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_coverage_map"
        self._attr_image_last_updated: dt_datetime = dt_util.now(
            datetime.timezone.utc
        )

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # v2.6.3 E — listen for GridStore update signal from RoombaMapImage.
        # RoombaMapImage fires the signal after every successful mission end so
        # the frontend knows to re-fetch the coverage image.
        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        from homeassistant.core import callback

        @callback
        def _on_gridstore_updated() -> None:
            self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
            self._cache = None
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                _SIGNAL_COVERAGE_UPDATED.format(self._config_entry.entry_id),
                _on_gridstore_updated,
            )
        )

    async def async_image(self) -> bytes | None:
        rendered = await self.hass.async_add_executor_job(
            self._grid_store.render_heatmap
        )
        if rendered is None:
            return self._blank_image()
        return rendered

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """EMA diagnostic attributes — exposed during v2.2 validation period."""
        bbox = self._grid_store.bounding_box_mm()
        return {
            "cell_size_mm":      CELL_SIZE_MM,
            "decay":             DECAY,
            "visit_increment":   VISIT_INCREMENT,
            "cell_count":        self._grid_store.cell_count,
            "stuck_event_count": self._grid_store.stuck_event_count,
            "x_min_mm":          bbox[0] if bbox else None,
            "x_max_mm":          bbox[1] if bbox else None,
            "y_min_mm":          bbox[2] if bbox else None,
            "y_max_mm":          bbox[3] if bbox else None,
            "last_mission_end":  self._attr_image_last_updated.isoformat()
                                 if self._attr_image_last_updated else None,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state

    def on_message(self, json_data: dict[str, Any]) -> None:
        """React to MQTT state changes.

        GridStore updates and image_last_updated bumps are handled via the
        _SIGNAL_COVERAGE_UPDATED dispatcher signal (fired by RoombaMapImage
        after each mission end). This callback only triggers HA state refresh
        so the entity stays responsive to phase changes on the dashboard.
        """
        state = json_data.get("state", {}).get("reported", {})
        if not self.new_state_filter(state):
            return
        self.vacuum_state = roomba_reported_state(self.vacuum)
        self.schedule_update_ha_state()

    @staticmethod
    def _blank_image() -> bytes:
        """Return a transparent 400×400 PNG when no grid data exists yet."""
        try:
            from PIL import Image
            img = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
        except ImportError:
            # Pillow absent — return minimal valid PNG (1×1 transparent)
            import base64
            return base64.b64decode(
                b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
                b"AABjkB6QAAAABJRU5ErkJggg=="
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


# v2.3.0 Step 5b — Issue #14 ──────────────────────────────────────────────────

class RoombaRoomsImage(IRobotEntity, ImageEntity):
    """Static room-layout image for xiaomi-vacuum-map-card room selection.

    Renders UmfAligner room polygons onto a dark canvas using Pillow directly —
    no MapRenderer dependency. calibration and rooms attributes use the same
    local to_px() transform as the render so pixel coordinates are consistent.

    Distinct from RoombaMapImage (cleaning history + keepout overlay).
    Preferred source for xiaomi-vacuum-map-card configuration.
    """

    _attr_content_type    = "image/png"
    _attr_translation_key = "rooms_map"
    _attr_entity_category = None

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_rooms_map"
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

        # Persisted transform parameters for calibration_points consistency
        self._last_x_min: float = 0.0
        self._last_x_max: float = 1.0
        self._last_y_min: float = 0.0
        self._last_y_max: float = 1.0
        self._last_size:  int   = 600
        # Guard: do not expose calibration/rooms until at least one render has
        # set the transform parameters correctly (avoids wrong coords at startup).
        self._rendered_once: bool = False

        # ZONE-LAYER-CACHE (v2.9.0): room polygons only change on map retrain
        # (new pmap_version_id) or alignment-state transitions — re-running
        # the full PIL render on every async_image() call (every frontend
        # poll/refresh) was wasted work the overwhelming majority of the time.
        # Cache key captures everything that affects the rendered output;
        # _last_x_min/_max/_y_min/_y_max/_last_size are restored from the
        # cache entry too, since other code (calibration_points, _to_px_last)
        # depends on them matching whatever PNG was actually returned.
        self._room_render_cache_key: tuple[Any, ...] | None = None
        self._room_render_cache: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # Prime the render immediately on startup so the image and attributes
        # are ready before the frontend first requests them.
        # Aligned path: calibration + rooms attributes populated.
        # Fallback path: UMF-space render visible even before alignment.
        data = self._config_entry.runtime_data
        if data.umf_aligner and data.umf_aligner.room_polygons_umf:
            await self.hass.async_add_executor_job(self._render_rooms_png)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # Cloud entity — no MQTT updates

    async def async_image(self) -> bytes | None:
        """Render room polygons from UmfAligner onto a dark canvas."""
        return await self.hass.async_add_executor_job(self._render_rooms_png)

    def _render_rooms_png(self) -> bytes:
        """CPU-bound render — called via async_add_executor_job.

        Two rendering modes:
        - Aligned (aligner.aligned=True): polygons in pose-space coordinates.
          calibration/rooms attributes are populated. Full xiaomi-card support.
        - Fallback (room_polygons_umf present but not aligned): polygons rendered
          directly in UMF-space coordinates. Image is visible immediately after
          install without requiring missions. calibration/rooms attributes are
          NOT set in this mode — xiaomi-card alignment pending. The image shows
          correct room shapes but may be rotated/mirrored vs. robot orientation.
          Once alignment succeeds (after 2+ missions), the aligned path takes over.
        """
        if self._config_entry is None:
            return self._blank_png()
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if not aligner:
            return self._blank_png()

        polygons_umf = aligner.room_polygons_umf
        if not polygons_umf:
            return self._blank_png()

        aligned = aligner.aligned

        # ZONE-LAYER-CACHE (v2.9.0): room polygons are identical between
        # calls unless the map was retrained (pmap_version_id changes) or
        # alignment state flipped (fallback → aligned after enough missions).
        # Restore both the cached PNG and the transform parameters it was
        # computed with — calibration_points/_to_px_last depend on them
        # matching the returned image exactly.
        cache_key = (aligner.pmap_version_id, aligned)
        # Known limitation: this assumes umf_to_pose()'s rotation/translation
        # is stable for a given pmap_version_id once aligned=True is reached.
        # If a later alignment run meaningfully refines the transform for the
        # same map (not currently expected to happen, but not structurally
        # prevented either), the cached image would be stale until the next
        # map retrain changes pmap_version_id. Matches the scope agreed for
        # ZONE-LAYER-CACHE: invalidate on map retrain, not on every render.
        if (
            self._room_render_cache_key == cache_key
            and self._room_render_cache is not None
        ):
            cached = self._room_render_cache
            self._last_x_min = cached["x_min"]
            self._last_x_max = cached["x_max"]
            self._last_y_min = cached["y_min"]
            self._last_y_max = cached["y_max"]
            self._last_size  = cached["size"]
            if aligned:
                self._rendered_once = True
            else:
                self._rendered_fallback = True
            return cached["png"]



        if aligned:
            # Pose-space path: transform UMF → pose coordinates
            all_coords: list[tuple[float, float]] = []
            for poly_umf in polygons_umf.values():
                for pt in poly_umf:
                    p = aligner.umf_to_pose(*pt)
                    if p:
                        all_coords.append(p)

            def resolve_poly(poly_umf: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
                pts = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                return pts if all(p is not None for p in pts) else None  # type: ignore[return-value]
        else:
            # Fallback: render directly in UMF-space coordinates
            _LOGGER.debug(
                "RoombaRoomsImage: pose alignment pending — rendering in UMF space "
                "(alignment_pending=True, fallback calibration active and accurate)"
            )
            all_coords = [
                pt for poly in polygons_umf.values() for pt in poly
            ]

            def resolve_poly(poly_umf: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
                return poly_umf if len(poly_umf) >= 3 else None

        if not all_coords:
            return self._blank_png()

        margin = 50.0
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        x_min = min(xs) - margin
        x_max = max(xs) + margin
        y_min = min(ys) - margin
        y_max = max(ys) + margin
        size  = 600
        scale = size / max(x_max - x_min, y_max - y_min, 1.0)

        # Store transform for _to_px_last consistency — both aligned and fallback.
        # In fallback mode these are UMF-space values; in aligned mode pose-space.
        # _to_px_last uses whichever was set last, which always matches the
        # coordinate space of the most recent render.
        self._last_x_min = x_min
        self._last_x_max = x_max
        self._last_y_min = y_min
        self._last_y_max = y_max
        self._last_size  = size
        if aligned:
            self._rendered_once = True
        else:
            self._rendered_fallback = True

        def to_px(x: float, y: float) -> tuple[int, int]:
            return (
                int((x - x_min) * scale),
                int(size - (y - y_min) * scale),  # y-flip: HA map convention
            )

        from PIL import Image, ImageDraw
        img  = Image.new("RGB", (size, size), (30, 30, 30))
        draw = ImageDraw.Draw(img)
        # v2.7.3: rid_to_name() lookup removed — labels are no longer drawn
        # into the PNG (XVMC card renders its own from predefined_selections).

        # ROOM-PALETTE (v2.9.0) — rotating per-room fill instead of a single
        # uniform colour, so adjacent rooms are visually distinguishable even
        # without the XVMC card's own room-name overlay. Outline stays fixed
        # (matches existing card highlight colour); only fill rotates.
        # Muted tones chosen to read clearly against the dark (30,30,30) canvas.
        for idx, (rid, poly_umf) in enumerate(polygons_umf.items()):
            resolved = resolve_poly(poly_umf)
            if not resolved:
                continue
            poly_px = [to_px(x, y) for x, y in resolved]
            fill = ROOM_FILL_PALETTE[idx % len(ROOM_FILL_PALETTE)]
            draw.polygon(poly_px, outline=(100, 149, 237), fill=fill)
            # v2.7.3: labels removed from PNG — XVMC card renders its own
            # labels from predefined_selections.label.text; drawing them here
            # produced duplicate overlapping labels in the card (veronoicc #2).

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # ZONE-LAYER-CACHE (v2.9.0): store for the next call.
        self._room_render_cache_key = cache_key
        self._room_render_cache = {
            "png": png_bytes,
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "size": size,
        }
        return png_bytes

    def _to_px_last(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """Reproduce to_px() using persisted transform for attribute consistency."""
        scale = self._last_size / max(
            self._last_x_max - self._last_x_min,
            self._last_y_max - self._last_y_min,
            1.0,
        )
        return (
            int((x_mm - self._last_x_min) * scale),
            int(self._last_size - (y_mm - self._last_y_min) * scale),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and room polygon data for xiaomi-vacuum-map-card.

        Uses the same local to_px() as _render_rooms_png() so pixel coordinates
        in attributes match the rendered image exactly.

        Aligned mode: calibration + rooms attributes populated for xiaomi-card.
        Fallback mode (not yet aligned): only alignment_pending=True exposed.
          The image is visible but calibration/rooms are withheld because the
          UMF→pose transform is unknown — pixel coords would be meaningless.
        """
        attrs: dict[str, Any] = {}
        if self._config_entry is None:
            return attrs
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if not aligner:
            return attrs

        polygons_umf = aligner.room_polygons_umf
        if not polygons_umf:
            return attrs

        aligned  = aligner.aligned
        rendered = (
            getattr(self, "_rendered_once", False)      # aligned render done
            or getattr(self, "_rendered_fallback", False)  # fallback render done
        )
        if not rendered:
            return attrs

        if aligned:
            attrs["alignment_pending"] = False
        else:
            # Fallback mode: image is in UMF-space, calibration uses UMF coords.
            # Works with calibration_source: camera: true — the card reads our
            # calibration attribute directly and does not use robot pose coords.
            attrs["alignment_pending"] = True

        # calibration — 3 anchor points mapping vacuum coords → image pixels.
        # Aligned: vacuum coords are pose-space mm (dock-relative).
        # Fallback: vacuum coords are UMF-space units — consistent with the
        #           rendered image so calibration_source: camera: true works.
        all_coords = [pt for poly in polygons_umf.values() for pt in poly]
        if all_coords:
            xs = [c[0] for c in all_coords]
            ys = [c[1] for c in all_coords]
            if aligned:
                # Pose-space anchors via aligner transform
                cal = aligner.calibration_points(self._to_px_last)
                if cal:
                    attrs["calibration_points"] = cal  # XVMC (v2.7.0): renamed
            else:
                # UMF-space anchors — three corners of polygon bounding box.
                # Use actual min/max corners so all three points are within the
                # rendered image area and the card can calibrate correctly.
                anchors = [
                    (min(xs), min(ys)),
                    (max(xs), min(ys)),
                    (min(xs), max(ys)),
                ]
                attrs["calibration_points"] = [  # XVMC (v2.7.0): renamed
                    {
                        "vacuum": {"x": x, "y": y},
                        "map":    {"x": px, "y": py},
                    }
                    for x, y in anchors
                    for px, py in [self._to_px_last(x, y)]
                ]

        # rooms — dict {name: {outline:[[x,y],...], name, icon, x, y}}
        # XVMC (v2.7.0): dict keyed by display name; outline uses [x,y] arrays.
        # In fallback mode polygon vertices are in UMF-space — consistent with
        # the fallback calibration so the card overlays them correctly.
        cc = self._config_entry.runtime_data.cloud_coordinator
        rid_to_type = (
            {r["id"]: r.get("region_type", "default") for r in cc.regions}
            if cc is not None else {}
        )
        rid_to_name = aligner.rid_to_name()
        rooms: dict[str, dict[str, Any]] = {}
        for rid, poly_umf in polygons_umf.items():
            if aligned:
                poly_coords = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                if not all(p is not None for p in poly_coords):
                    continue
            else:
                poly_coords = poly_umf  # type: ignore[assignment]
            if not poly_coords:  # Bug 6 fix: guard against empty polygon
                continue
            room_name = rid_to_name.get(rid, rid)
            # XVMC-COORDS: outline and centroid in vacuum mm (pose or UMF space).
            # XVMC applies calibration (vacuum mm → display px) itself.
            cx = sum(x for x, _ in poly_coords) / len(poly_coords)
            cy = sum(y for _, y in poly_coords) / len(poly_coords)
            icon = REGION_TYPE_ICONS.get(
                rid_to_type.get(rid, "default"), REGION_TYPE_ICONS["default"]
            )
            rooms[room_name] = {
                "outline": [[x, y] for x, y in poly_coords],
                "name":    room_name,
                "room_id": _room_slug(room_name),  # v2.7.3: ASCII slug for XVMC id
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

        # ZONE-OVERLAY (v3.3.1) + F24 — only meaningful in aligned mode:
        # zones/door_markers/furniture_candidates are all pose-space (or
        # transformed to pose-space), which only matches the rendered image
        # when aligned=True. In fallback mode the image is UMF-space and
        # these would be spatially wrong if shown, so they're withheld
        # entirely (same reasoning as `rooms`'s aligned/fallback split above).
        if aligned:
            # zones — UMF-space source (observed_zone_centroids, keepout_zones),
            # genuinely needs the aligner transform, unlike door_markers/
            # furniture_candidates below.
            if cc is not None:
                zones: list[dict[str, Any]] = []
                for centroid in cc.observed_zone_centroids:
                    pose_xy = aligner.umf_to_pose(centroid["x"], centroid["y"])
                    if pose_xy is None:
                        continue
                    zones.append({
                        "type": "observed",
                        "x":    pose_xy[0],
                        "y":    pose_xy[1],
                    })
                for zone in cc.keepout_zones:
                    poly_umf = aligner.keepout_polygon_umf(zone)
                    if not poly_umf:
                        continue
                    poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                    if not poly_pose or not all(p is not None for p in poly_pose):
                        continue
                    zones.append({
                        "type":    "keepout",
                        "polygon": [[x, y] for x, y in poly_pose],
                    })
                if zones:
                    attrs["zones"] = zones

            # door_markers — already pose-space mm (collected directly from
            # self._mission_points / RoomSegStore.doors, never through UMF)
            # — exposed as-is, NOT through umf_to_pose(). Known caveat:
            # markers accumulate across missions and are not re-corrected by
            # GeometryStore.record_drift()/drift_recovered() (those only
            # track drift magnitude for the Repair Issue), so a marker's
            # median position can lag behind a large drift correction
            # between missions — same open-ended caveat class as
            # observed_zone centroids' Q6 note, not treated as a blocker.
            geometry_store = getattr(data, "geometry_store", None)
            if geometry_store is not None and geometry_store.door_markers:
                attrs["door_markers"] = [
                    {
                        "id":            m.id,
                        "cx":            m.cx,
                        "cy":            m.cy,
                        "label":         m.label,
                        "mission_count": m.mission_count,
                    }
                    for m in geometry_store.door_markers
                ]

            # F24 — furniture shadow candidates. GridStore.furniture_
            # candidates()'s x_mm/y_mm come from _cell_to_mm(), the same
            # pose-space family hotspots()/format=hazards already
            # documents — no transform needed, exposed as-is.
            grid_store = getattr(data, "grid_store", None)
            if grid_store is not None:
                candidates = grid_store.furniture_candidates()
                if candidates:
                    attrs["furniture_candidates"] = [
                        {"x_mm": c["x_mm"], "y_mm": c["y_mm"]} for c in candidates
                    ]

        return attrs

    @staticmethod
    def _blank_png() -> bytes:
        """Return a dark 600×600 PNG placeholder."""
        try:
            from PIL import Image
            img = Image.new("RGB", (600, 600), (30, 30, 30))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            import base64
            return base64.b64decode(
                b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
                b"AABjkB6QAAAABJRU5ErkJggg=="
            )
