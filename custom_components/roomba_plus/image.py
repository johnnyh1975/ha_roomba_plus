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

import collections
import datetime
import io
import logging
from datetime import datetime as dt_datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import DOMAIN
from .entity import IRobotEntity
from .map_renderer import MapRenderer
from .models import MapCapability, RoombaConfigEntry
from .zone_store import ZoneStore

_LOGGER = logging.getLogger(__name__)

_CLEANING_PHASES    = {"run", "hmMidMsn"}
_MISSION_END_PHASES = {"charge", "hmPostMsn", "stop", "evac"}

_MAP_STORAGE_VERSION = 1


def _map_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_{entry_id}"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up map image entity — only if robot reports pose data."""
    data = config_entry.runtime_data

    if data.map_capability == MapCapability.NONE:
        _LOGGER.debug("Roomba+ image: skipped — no pose capability")
        return

    async_add_entities([
        RoombaMapImage(
            roomba=data.roomba,
            blid=data.blid,
            renderer=data.renderer,
            zone_store=data.zone_store,
            map_capability=data.map_capability,
            config_entry=config_entry,
        )
    ])


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
        return await self.hass.async_add_executor_job(self._renderer.render)

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
            if (current_phase in _CLEANING_PHASES
                    and self._last_phase not in _CLEANING_PHASES):
                if self._renderer:
                    self._renderer.reset()
                    self._mission_points = []
                    _LOGGER.debug("Map: mission started, renderer reset")

            if (current_phase in _MISSION_END_PHASES
                    and self._last_phase in _CLEANING_PHASES):
                self._handle_mission_end()

            self._last_phase = current_phase

        # Pose update
        if "pose" in state and self._renderer and current_phase in _CLEANING_PHASES:
            self._handle_pose(state["pose"])

        # Stuck detection
        if "bbrun" in state and self._renderer:
            stuck = self.vacuum_state.get("bbrun", {}).get("nStuck", 0)
            if stuck > self._last_stuck_count:
                self._renderer.mark_stuck()
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

    def _handle_mission_end(self) -> None:
        if not self._mission_points:
            return

        if (self._map_capability == MapCapability.EPHEMERAL
                and self._zone_store
                and len(self._mission_points) >= 20):
            # Compute drift once — used by both ZoneStore log and GeometryStore.
            drift_vector = self._zone_store.check_dock_drift(self._mission_points[-1])
            if drift_vector != (0.0, 0.0):
                _LOGGER.info("Map: drift %.0f,%.0f mm", *drift_vector)

            ts = dt_util.now(datetime.timezone.utc).timestamp()
            new_zones = self._zone_store.process_mission(self._mission_points, ts)
            if new_zones:
                self.hass.async_create_task(self._trigger_zone_issue())
            self.hass.async_create_task(
                self._zone_store.async_save(self.hass, self._config_entry.entry_id)
            )

            # Update geometry store from this mission's gap midpoints.
            # Must run after process_mission() so last_mission_gap_midpoints is set.
            data = self._config_entry.runtime_data
            if data.geometry_store:
                data.geometry_store.update_from_mission(self._zone_store)
                if drift_vector != (0.0, 0.0):
                    threshold_exceeded = data.geometry_store.record_drift(*drift_vector)
                    if threshold_exceeded:
                        self.hass.async_create_task(self._trigger_drift_issue())
                self.hass.async_create_task(
                    data.geometry_store.async_save(self.hass, self._config_entry.entry_id)
                )

        # Persist renderer state so the map survives an HA restart
        if self._renderer and self._renderer.has_data:
            self.hass.async_create_task(self._async_save_map_state())

        self._mission_points = []

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

    @staticmethod
    def _blank_image() -> bytes:
        from PIL import Image
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
