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
from datetime import datetime as dt_datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import CLEANING_PHASES, DOMAIN, MISSION_END_PHASES, REGION_TYPE_ICONS
from .entity import IRobotEntity
from .grid_store import GridStore, CELL_SIZE_MM, DECAY, VISIT_INCREMENT
from .map_renderer import MapRenderer
from .models import MapCapability, RoombaConfigEntry
from .zone_store import GAP_THRESHOLD_MM, MAX_DOOR_WIDTH_MM, MIN_DOOR_WIDTH_MM, ZoneStore

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

# CLEANING_PHASES and MISSION_END_PHASES moved to const.py (v2.3.0 Step 1)

_MAP_STORAGE_VERSION = 1

# v2.6.3 E — dispatcher signal fired by RoombaMapImage after GridStore update.
# RoombaCoverageImage listens to bump image_last_updated so the frontend re-fetches.
_SIGNAL_COVERAGE_UPDATED = "roomba_plus_coverage_updated_{}"


async def _async_send_coverage_signal(hass: HomeAssistant, entry_id: str) -> None:
    """Fire the coverage-updated dispatcher signal on the HA event loop."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(hass, _SIGNAL_COVERAGE_UPDATED.format(entry_id))


def _map_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_{entry_id}"


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
            zone_store=data.zone_store,
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
    _attr_name            = "Cleaning Map"   # G6: locale-independent entity_id slug
    _attr_entity_category = None
    _attr_content_type = "image/png"

    def __init__(
        self,
        roomba: Any,
        blid: str,
        renderer: MapRenderer | None,
        zone_store: ZoneStore | None,
        map_capability: MapCapability,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)

        # Manually initialize ImageEntity internals that require hass.
        # async_update_token() is called in async_added_to_hass.
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)

        self._renderer = renderer
        self._zone_store = zone_store
        self._map_capability = map_capability
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map"

        # Mission tracking
        self._last_phase: str = ""
        self._last_stuck_count: int = 0
        self._mission_points: list[tuple[float, float]] = []
        self._stuck_mission_points: list[tuple[float, float]] = []
        # v2.6.3 A+D — True once robot enters CLEANING_PHASES in this mission.
        # Replaces last_phase-in-CLEANING_PHASES guard; fixes stuck-bypass and
        # false mission-restart on stuck → run recovery.
        self._had_cleaning_phase: bool = False

        # Initial timestamp so frontend knows an image exists from the start
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register MQTT callback, restore persisted map state, generate token."""
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # Restore last mission's map from hass.storage (if any)
        await self._async_restore_map_state()

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
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

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
            self.vacuum_state.get("cleanMissionStatus", {}).get("phase", "")
        )

        # Phase transitions
        if current_phase != self._last_phase:
            # v2.6.3 D — guard with _had_cleaning_phase so stuck → run (recovery)
            # does NOT reset the renderer mid-mission.
            if current_phase in CLEANING_PHASES and not self._had_cleaning_phase:
                self._had_cleaning_phase = True
                if self._renderer:
                    self._renderer.reset()
                    self._mission_points = []
                    self._stuck_mission_points = []
                    self._mission_start_ts: str | None = dt_util.now().isoformat()
                    _LOGGER.debug("Map: mission started, renderer reset")

            # v2.6.3 A — use _had_cleaning_phase so stuck → stop/charge
            # (stuck_and_abandoned) correctly triggers _handle_mission_end().
            if current_phase in MISSION_END_PHASES and self._had_cleaning_phase:
                self._had_cleaning_phase = False
                self._handle_mission_end(current_phase)

            self._last_phase = current_phase

        # Pose update — process regardless of phase so the map and direction
        # vector stay live even when the robot is stuck, returning, or
        # between phases.  Renderer reset (mission-start) and _handle_mission_end()
        # remain gated on phase transitions.
        if "pose" in state and self._renderer:
            self._handle_pose(state["pose"])

        # Stuck detection
        if "bbrun" in state and self._renderer:
            stuck = self.vacuum_state.get("bbrun", {}).get("nStuck", 0)
            if stuck > self._last_stuck_count:
                self._renderer.mark_stuck()
                # Record stuck position in mm for GridStore
                if self._mission_points:
                    self._stuck_mission_points.append(self._mission_points[-1])
            self._last_stuck_count = stuck

        self.schedule_update_ha_state()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_pose(self, pose: dict[str, Any]) -> None:
        """Add pose point and signal frontend to re-fetch image."""
        point = pose.get("point", {})
        x = float(point.get("x", 0))
        y = float(point.get("y", 0))
        theta = float(pose.get("theta", 0))
        if self._renderer:
            self._renderer.add_pose(x, y, theta)
        self._mission_points.append((x, y))
        self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)

    def _handle_mission_end(self, ending_phase: str = "") -> None:
        # Called from roombapy's paho-MQTT thread — NOT the HA event loop.
        # hass.async_create_task() is not thread-safe and raises RuntimeError
        # on recent HA versions when called from a foreign thread.
        # All coroutine scheduling must go through asyncio.run_coroutine_threadsafe().
        if not self._mission_points:
            return

        loop = self.hass.loop

        # v2.6.3 B5 — only run drift detection when the robot actually returned
        # to the dock.  For stuck-and-abandoned (ending_phase="stop") or user-
        # abort missions, _mission_points[-1] is the stuck position, not the dock.
        # Passing those coordinates to check_dock_drift() produced spurious large
        # drift vectors and inflated cumulative_drift_mm to >900 mm.
        _dock_return = ending_phase in {"charge", "hmPostMsn"}

        if (self._map_capability == MapCapability.EPHEMERAL
                and self._zone_store
                and len(self._mission_points) >= 20):
            # Compute drift once — used by both ZoneStore log and GeometryStore.
            drift_vector = (0.0, 0.0)
            if _dock_return:
                drift_vector = self._zone_store.check_dock_drift(self._mission_points[-1])
            if drift_vector != (0.0, 0.0):
                _LOGGER.info("Map: drift %.0f,%.0f mm", *drift_vector)

            ts = dt_util.now(datetime.timezone.utc).timestamp()
            new_zones = self._zone_store.process_mission(self._mission_points, ts)
            if new_zones:
                asyncio.run_coroutine_threadsafe(self._trigger_zone_issue(), loop)
            asyncio.run_coroutine_threadsafe(
                self._zone_store.async_save(self.hass, self._config_entry.entry_id),
                loop,
            )

            # Update geometry store from this mission's gap midpoints.
            # Must run after process_mission() so last_mission_gap_midpoints is set.
            data = self._config_entry.runtime_data
            if data.geometry_store:
                data.geometry_store.update_from_mission(self._zone_store)
                if drift_vector != (0.0, 0.0):
                    threshold_exceeded = data.geometry_store.record_drift(*drift_vector)
                    if threshold_exceeded:
                        asyncio.run_coroutine_threadsafe(
                            self._trigger_drift_issue_enriched(*drift_vector), loop
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

        # F-EPHEMERAL — Extract and accumulate room outline
        # v2.6.3 B4 — render_for_outline() renders without the FLOOR_BORDER
        # rectangle that previously dominated contour extraction.
        if (
            self._renderer
            and self._renderer.has_data
            and self._config_entry is not None
        ):
            _outline_store = getattr(
                self._config_entry.runtime_data, "outline_store", None
            )
            if _outline_store is not None:
                _png = self._renderer.render_for_outline()
                if _png is not None:
                    asyncio.run_coroutine_threadsafe(
                        _outline_store.async_update_from_png(
                            _png, self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )

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
                )
                asyncio.run_coroutine_threadsafe(
                    _gdata.grid_store.async_save(
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
                from homeassistant.helpers.dispatcher import async_dispatcher_send
                _eid = self._config_entry.entry_id
                asyncio.run_coroutine_threadsafe(
                    _async_send_coverage_signal(self.hass, _eid),
                    loop,
                )

        self._mission_points = []
        self._stuck_mission_points = []
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

    async def _trigger_zone_issue(self) -> None:
        from homeassistant.components import repairs as ir
        ir.async_create_issue(
            self.hass, DOMAIN, "zones_need_naming",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="zones_need_naming",
        )

    async def _trigger_drift_issue(self) -> None:
        from homeassistant.components import repairs as ir
        ir.async_create_issue(
            self.hass, DOMAIN, "geometry_drifted",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="geometry_drifted",
        )

    async def _trigger_drift_issue_enriched(self, dx: float, dy: float) -> None:
        """F6d -- fire the drift Repair Issue with bearing/magnitude enrichment."""
        from .repairs import async_enrich_drift_issue
        await async_enrich_drift_issue(self.hass, self._config_entry, dx=dx, dy=dy)

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
    _attr_name            = "Coverage Map"   # G6: locale-independent entity_id slug
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
    _attr_name            = "Rooms Map"
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
                "RoombaRoomsImage: aligner not yet aligned — rendering in UMF space "
                "(alignment_pending=True, fallback calibration active)"
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
        rid_to_name = aligner.rid_to_name()

        for rid, poly_umf in polygons_umf.items():
            resolved = resolve_poly(poly_umf)
            if not resolved:
                continue
            poly_px = [to_px(x, y) for x, y in resolved]
            draw.polygon(poly_px, outline=(100, 149, 237), fill=(45, 55, 72))
            cx = int(sum(p[0] for p in poly_px) / len(poly_px))
            cy = int(sum(p[1] for p in poly_px) / len(poly_px))
            label = rid_to_name.get(rid, rid)
            if not aligned:
                label = f"{label} *"  # asterisk signals fallback mode to user
            draw.text((cx, cy), label, fill=(200, 200, 200))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

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
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

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
