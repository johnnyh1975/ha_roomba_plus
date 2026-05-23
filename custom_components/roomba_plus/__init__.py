"""The Roomba+ integration — extends the HA Core Roomba integration.

Connects to Wi-Fi enabled iRobot Roomba vacuums via local MQTT (push-based,
no polling). Cloud features are optional and added in later phases.
"""
from __future__ import annotations

import asyncio
import contextlib
from functools import partial
import logging
from typing import Any

from roombapy import Roomba, RoombaConnectionError, RoombaFactory

import voluptuous as vol

from homeassistant import exceptions
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DELAY,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_ORDERED,
    ATTR_ROOM_NAME,
    CONF_BLID,
    CONF_CONTINUOUS,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_SMART_ZONE_DATA,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DOMAIN,
    LOCAL_PLATFORMS,
    ROOMBA_SESSION,
    SERVICE_CLEAN_ROOM,
    has_pose,
    has_smart_map,
)
from .maintenance_store import MaintenanceStore
from .map_renderer import MapRenderer, RendererConfig
from .models import MapCapability, RoombaConfigEntry, RoombaData
from .zone_store import ZoneStore
from .geometry_store import GeometryStore

_LOGGER = logging.getLogger(__name__)


# ── clean_room helpers ────────────────────────────────────────────────────────

def _resolve_pmapv_id(state: dict, pmap_id: str) -> str | None:
    """Return the current user_pmapv_id for pmap_id from live MQTT state.

    Always reads state.pmaps at call time so the value is never stale.
    iRobot updates user_pmapv_id whenever the map is edited or retrained;
    a cached value silently causes the robot to reject the command.
    """
    for pmap in state.get("pmaps", []):
        if pmap_id in pmap:
            return pmap[pmap_id]
    return None


def _resolve_rooms(
    zone_data: dict[str, dict],
    room_names: list[str],
    state: dict,
) -> list[tuple[str, str]]:
    """Resolve room names to (region_id, pmap_id) tuples.

    Args:
        zone_data: smart_zone_data from config_entry.options —
                   {region_id: {"name": str, "pmap_id": str}}
        room_names: user-supplied room names from the service call.
        state:      live robot state — used to resolve pmap_id when the stored
                    value is empty (e.g. zone was entered via manual entry before
                    any MQTT mission data arrived).

    Returns:
        Ordered list of (region_id, pmap_id) matching each room name.

    Raises:
        ServiceValidationError: if any name is unknown, if pmap_id cannot be
            resolved, or if the resolved rooms span more than one pmap_id.
    """
    # Build a case-insensitive name → (region_id, pmap_id) index.
    # Include all zones that have a name — pmap_id may be empty for manually
    # entered zones and will be resolved from live state below.
    index: dict[str, tuple[str, str]] = {
        meta["name"].casefold(): (rid, meta.get("pmap_id", ""))
        for rid, meta in zone_data.items()
        if meta.get("name")
    }

    resolved: list[tuple[str, str]] = []
    unknown: list[str] = []

    for name in room_names:
        match = index.get(name.casefold())
        if match is None:
            unknown.append(name)
        else:
            resolved.append(match)

    if unknown:
        raise ServiceValidationError(
            f"Unknown room(s): {', '.join(unknown)}. "
            f"Known rooms: {', '.join(meta['name'] for meta in zone_data.values() if meta.get('name'))}",
            translation_domain=DOMAIN,
            translation_key="rooms_not_found",
            translation_placeholders={"names": ", ".join(unknown)},
        )

    # Resolve empty pmap_ids from live state for zones entered via manual entry
    # (where pmap_id was not available at save time) or after a map retrain.
    #
    # Priority order — mirrors the rest980 recommendation and repairs.py:
    #   1. lastCommand.pmap_id — the map the robot last used for a region clean.
    #      Most reliable on multi-map robots: it is the map actually associated
    #      with the region IDs in flight, not an arbitrary list position.
    #   2. First pmap_id found across cleanSchedule2[].cmd.pmap_id — stable
    #      between sessions even when lastCommand has been overwritten by a
    #      full-home clean.
    #   3. pmaps[0] key — last resort only; on robots with a single map this is
    #      always correct, but on multi-map robots the list order is undefined
    #      and picking index 0 is the root cause of "Problem with the smart map".
    last = state.get("lastCommand", {})
    pmaps: list[dict] = state.get("pmaps", [])
    live_pmap_id: str = (
        last.get("pmap_id")
        or next(
            (
                cmd.get("cmd", {}).get("pmap_id")
                for cmd in state.get("cleanSchedule2", [])
                if cmd.get("cmd", {}).get("pmap_id")
            ),
            None,
        )
        or (next(iter(pmaps[0]), None) if pmaps else None)
        or ""
    )
    resolved = [
        (rid, pmap_id if pmap_id else live_pmap_id)
        for rid, pmap_id in resolved
    ]

    # Validate that all rooms share the same pmap (same floor).
    pmap_ids = {pmap_id for _, pmap_id in resolved}
    if "" in pmap_ids:
        raise ServiceValidationError(
            "Could not resolve map ID (pmap_id) for one or more rooms. "
            "Ensure the robot has reported its map state via MQTT.",
            translation_domain=DOMAIN,
            translation_key="pmap_not_resolved",
        )
    if len(pmap_ids) > 1:
        raise ServiceValidationError(
            "All rooms must be on the same floor (same pmap). "
            f"Got rooms from maps: {', '.join(pmap_ids)}",
            translation_domain=DOMAIN,
            translation_key="rooms_different_floors",
            translation_placeholders={"pmap_ids": ", ".join(pmap_ids)},
        )

    return resolved


async def _async_handle_clean_room(call: ServiceCall) -> None:
    """Handle the roomba_plus.clean_room service call.

    Resolves room names → region_ids, looks up the fresh user_pmapv_id from
    live MQTT state, and fires a region-targeted start command.

    Only works for MapCapability.SMART robots (i7 / s9 / j-series).
    """
    hass = call.hass
    entity_ids: list[str] = call.data["entity_id"]
    room_names: list[str] = (
        [call.data[ATTR_ROOM_NAME]]
        if isinstance(call.data[ATTR_ROOM_NAME], str)
        else call.data[ATTR_ROOM_NAME]
    )
    ordered: bool = call.data[ATTR_ORDERED]

    ent_reg = er.async_get(hass)

    for entity_id in entity_ids:
        entry = ent_reg.async_get(entity_id)
        if entry is None:
            raise ServiceValidationError(f"Entity {entity_id} not found")

        config_entry: RoombaConfigEntry | None = hass.config_entries.async_get_entry(
            entry.config_entry_id
        )
        if config_entry is None:
            raise ServiceValidationError(
                f"No config entry for {entity_id}"
            )

        data: RoombaData = config_entry.runtime_data

        if data.map_capability != MapCapability.SMART:
            raise ServiceValidationError(
                f"{entity_id} does not support Smart Map room cleaning. "
                "Only i7, s9, and j-series robots support this action.",
                translation_domain=DOMAIN,
                translation_key="not_smart_map",
            )

        zone_data: dict = config_entry.options.get(CONF_SMART_ZONE_DATA, {})
        if not zone_data:
            raise ServiceValidationError(
                "No rooms configured yet. Run a room-targeted clean via the "
                "iRobot app first, then assign names in the Roomba+ options.",
                translation_domain=DOMAIN,
                translation_key="no_rooms_configured",
            )

        # Read live state before resolving rooms — needed for pmap_id fallback.
        state = roomba_reported_state(data.roomba)

        # Guard: reject if the robot is currently updating its Smart Map.
        # notReady bit 6 (64) = map save/upload in progress. Sending a region
        # clean while this bit is set causes the robot to immediately report
        # error 224 (Smart Map localization failed) because it cannot localize
        # during a map update. The iRobot app waits for this bit to clear first.
        not_ready: int = state.get("cleanMissionStatus", {}).get("notReady", 0)
        if not_ready & 64:
            raise ServiceValidationError(
                "The robot is currently updating its Smart Map. "
                "Wait for the update to complete (readiness sensor shows 'Ready'), "
                "then try again.",
                translation_domain=DOMAIN,
                translation_key="map_updating",
            )

        # Resolve names → (region_id, pmap_id) — raises on unknown or cross-floor.
        resolved = _resolve_rooms(zone_data, room_names, state)

        pmap_id = resolved[0][1]  # all share one pmap_id (enforced by _resolve_rooms)

        # Always read user_pmapv_id from live MQTT state — never from cache.
        user_pmapv_id = _resolve_pmapv_id(state, pmap_id)
        if user_pmapv_id is None:
            _LOGGER.warning(
                "clean_room: pmap_id %s not found in live state for %s. "
                "The map may have been retrained. Re-run a mission via the app.",
                pmap_id, entity_id,
            )
            raise ServiceValidationError(
                f"Map {pmap_id} not found in robot state. "
                "The map may have been retrained — re-run a room mission via the app.",
                translation_domain=DOMAIN,
                translation_key="pmap_not_found",
            )

        params = {
            "ordered": 1 if ordered else 0,
            "pmap_id": pmap_id,
            "user_pmapv_id": user_pmapv_id,
            "regions": [
                # region_id is sent as string — confirmed working on lewis firmware.
                # Each region requires a "params" sub-object with cleaning pass
                # settings. Omitting it causes error 224 on older i7 firmware
                # (lewis+22.52.10) because the robot cannot determine pass config.
                {
                    "region_id": rid,
                    "type": "rid",
                    "params": {"noAutoPasses": False, "twoPass": False},
                }
                for rid, _ in resolved
            ],
        }

        _LOGGER.info(
            "clean_room: %s → regions=%s pmap=%s pmapv=%s",
            entity_id,
            [rid for rid, _ in resolved],
            pmap_id[:12],
            user_pmapv_id[:12] if user_pmapv_id else "none",
        )

        await hass.async_add_executor_job(
            data.roomba.send_command, "start", params
        )

    return {}


async def async_setup_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Set up Roomba+ from a config entry."""
    # Migrate options from data if this is a fresh entry (matches Core behaviour)
    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_CONTINUOUS: config_entry.data.get(CONF_CONTINUOUS, DEFAULT_CONTINUOUS),
                CONF_DELAY: config_entry.data.get(CONF_DELAY, DEFAULT_DELAY),
            },
        )

    # ── Data migration: backfill discovered_zone_ids ───────────────────────
    # v1.4.4.1 repair flow incorrectly drained discovered_zone_ids after
    # labelling zones, leaving it empty. The selector relies on this list as
    # its persistent source of truth and goes unavailable whenever lastCommand
    # is cleared (mission end, dock). Backfill from smart_zone_data keys so
    # existing installations self-heal without user intervention.
    _opts = config_entry.options
    _zone_data_keys = set(_opts.get(CONF_SMART_ZONE_DATA, {}).keys())
    _discovered = set(_opts.get("discovered_zone_ids", []))
    if _zone_data_keys and not _zone_data_keys.issubset(_discovered):
        _new_discovered = sorted(_discovered | _zone_data_keys)
        hass.config_entries.async_update_entry(
            config_entry,
            options={**_opts, "discovered_zone_ids": _new_discovered},
        )
        _LOGGER.info(
            "Roomba+: backfilled discovered_zone_ids with %s from smart_zone_data",
            sorted(_zone_data_keys - _discovered),
        )

    roomba = await hass.async_add_executor_job(
        partial(
            RoombaFactory.create_roomba,
            address=config_entry.data[CONF_HOST],
            blid=config_entry.data[CONF_BLID],
            password=config_entry.data[CONF_PASSWORD],
            continuous=config_entry.options[CONF_CONTINUOUS],
            delay=config_entry.options[CONF_DELAY],
        )
    )

    try:
        if not await async_connect_or_timeout(hass, roomba):
            return False
    except CannotConnect as err:
        raise exceptions.ConfigEntryNotReady(
            f"Cannot connect to Roomba at {config_entry.data[CONF_HOST]}"
        ) from err

    async def _async_disconnect_on_stop(event: Any) -> None:
        await async_disconnect_or_timeout(hass, roomba)

    config_entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_disconnect_on_stop
        )
    )

    # ── Detect map capability ──────────────────────────────────────────────
    state = roomba_reported_state(roomba)
    map_capability = MapCapability.NONE
    renderer: MapRenderer | None = None
    zone_store: ZoneStore | None = None
    geometry_store: GeometryStore | None = None

    map_enabled = config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)

    if has_pose(state) and map_enabled:
        if has_smart_map(state):
            map_capability = MapCapability.SMART
            _LOGGER.debug("Roomba+ map: SMART (persistent pmaps detected)")
        else:
            map_capability = MapCapability.EPHEMERAL
            _LOGGER.debug("Roomba+ map: EPHEMERAL (900-series pose, no pmaps)")

        if map_capability == MapCapability.EPHEMERAL:
            zone_store = ZoneStore()
            await zone_store.async_load(hass, config_entry.entry_id)
            geometry_store = GeometryStore()
            await geometry_store.async_load(hass, config_entry.entry_id)

        renderer = MapRenderer(
            RendererConfig(
                size_px=config_entry.options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                scale=config_entry.options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE),
            ),
            geometry_store=geometry_store,
            zone_store=zone_store,
        )
    else:
        _LOGGER.debug(
            "Roomba+ map: NONE (cap.pose=%s, map_enabled=%s)",
            state.get("cap", {}).get("pose"), map_enabled,
        )

    maintenance_store = MaintenanceStore()
    await maintenance_store.async_load(hass, config_entry.entry_id)

    config_entry.runtime_data = RoombaData(
        roomba=roomba,
        blid=config_entry.data[CONF_BLID],
        map_capability=map_capability,
        renderer=renderer,
        zone_store=zone_store,
        geometry_store=geometry_store,
        maintenance_store=maintenance_store,
    )

    # ── Platform setup ──────────────────────────────────────────────────────
    platforms = list(LOCAL_PLATFORMS)
    # Image entity is only meaningful for EPHEMERAL (900-series) robots, which
    # broadcast live pose updates via local MQTT. SMART map robots (i7/s9/j)
    # use cloud-based vSLAM and do not send pose deltas over local MQTT at all,
    # so the renderer never receives points and the map stays blank. Suppress
    # the image entity for SMART robots to avoid a misleading blank white map.
    if map_capability == MapCapability.EPHEMERAL:
        platforms.append(Platform.IMAGE)

    await hass.config_entries.async_forward_entry_setups(config_entry, platforms)

    # ── clean_room service ──────────────────────────────────────────────────
    # Register once on first entry setup; subsequent entries reuse the same
    # handler which dispatches by entity_id.
    if not hass.services.has_service(DOMAIN, SERVICE_CLEAN_ROOM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CLEAN_ROOM,
            _async_handle_clean_room,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): cv.entity_ids,
                    vol.Required(ATTR_ROOM_NAME): vol.Any(
                        cv.string,
                        vol.All(cv.ensure_list, [cv.string]),
                    ),
                    vol.Optional(ATTR_ORDERED, default=True): cv.boolean,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.debug("Registered %s.%s action", DOMAIN, SERVICE_CLEAN_ROOM)

    # Reload on options change (continuous/delay require reconnect)
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_change)
    )

    _LOGGER.info(
        "Roomba+ connected to %s (blid=%s)",
        config_entry.data[CONF_HOST],
        config_entry.data[CONF_BLID],
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Unload a config entry and disconnect from the Roomba."""
    data = config_entry.runtime_data
    platforms = list(LOCAL_PLATFORMS)
    if data.map_capability == MapCapability.EPHEMERAL:
        platforms.append(Platform.IMAGE)
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, platforms
    )
    if unload_ok:
        await async_disconnect_or_timeout(
            hass, roomba=config_entry.runtime_data.roomba
        )
        # Remove the domain service when the last entry is unloaded.
        if not hass.config_entries.async_entries(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_CLEAN_ROOM)
    return unload_ok


async def _async_reload_on_options_change(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> None:
    """Reload only when connection-relevant options change.

    continuous and delay are passed to RoombaFactory at setup time and
    require a full reconnect to take effect. Zone labels, map settings,
    and discovered_zone_ids do not affect the MQTT connection and must
    not trigger a reload — doing so disconnects the robot unnecessarily
    every time the user saves a zone name via the repair flow or options.
    """
    _CONNECTION_KEYS = {CONF_CONTINUOUS, CONF_DELAY}
    old_vals = {k: config_entry.data.get(k) for k in _CONNECTION_KEYS}
    new_vals = {k: config_entry.options.get(k) for k in _CONNECTION_KEYS}
    if old_vals != new_vals:
        await hass.config_entries.async_reload(config_entry.entry_id)


# ── Connection helpers ────────────────────────────────────────────────────────

async def async_connect_or_timeout(
    hass: HomeAssistant, roomba: Roomba
) -> dict[str, Any]:
    """Connect to the vacuum and wait for first state report.

    Returns dict with ROOMBA_SESSION and CONF_NAME on success.
    Raises CannotConnect on failure or timeout.
    """
    try:
        name: str | None = None
        async with asyncio.timeout(16):
            _LOGGER.debug("Connecting to Roomba")
            await hass.async_add_executor_job(roomba.connect)
            while not roomba.roomba_connected or name is None:
                name = roomba_reported_state(roomba).get("name")
                if name:
                    break
                await asyncio.sleep(1)
            # Wait briefly for the Roomba to send its full state dump.
            # After sending 'name', the robot typically follows with cap,
            # bbrun, bbmssn, cleanMissionStatus etc. within 1–2 seconds.
            # Without this, capability-gated sensors (carpet boost, dock,
            # mop) are filtered out because 'cap' is not yet in master_state.
            await asyncio.sleep(2)
            # For Smart Map robots, pmaps may arrive slightly later than cap.
            # Wait up to 6 additional seconds so capability detection at
            # async_setup_entry doesn't misclassify an i/s/j robot as NONE.
            cap = roomba_reported_state(roomba).get("cap", {})
            # cap.pmaps > 0 is the correct flag for Smart Map capability on
            # lewis/hazel/xavier firmware. cap.pmapUpload and cap.tflmsl are
            # only present on newer firmware builds and must not be relied on.
            if cap.get("pmaps", 0) > 0 or cap.get("maps", 0) > 1:
                for _ in range(6):
                    if roomba_reported_state(roomba).get("pmaps"):
                        break
                    await asyncio.sleep(1)
    except RoombaConnectionError as err:
        _LOGGER.debug("Connection error: %s", err)
        raise CannotConnect from err
    except TimeoutError as err:
        # Roomba loops if credentials are wrong — disconnect before raising
        await async_disconnect_or_timeout(hass, roomba)
        _LOGGER.debug("Connection timed out: %s", err)
        raise CannotConnect from err

    return {ROOMBA_SESSION: roomba, CONF_NAME: name}


async def async_disconnect_or_timeout(
    hass: HomeAssistant, roomba: Roomba
) -> None:
    """Disconnect from the vacuum with a 3 s safety timeout."""
    _LOGGER.debug("Disconnecting from Roomba")
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(3):
            await hass.async_add_executor_job(roomba.disconnect)


# ── State helpers (used across all platforms) ─────────────────────────────────

def roomba_reported_state(roomba: Roomba) -> dict[str, Any]:
    """Return the 'reported' sub-dict from master_state.

    This is the canonical state accessor used throughout the integration.
    Returns an empty dict when the robot has not yet published any state.
    """
    return roomba.master_state.get("state", {}).get("reported", {})


# ── Exceptions ────────────────────────────────────────────────────────────────

class CannotConnect(exceptions.HomeAssistantError):
    """Raised when a connection to the Roomba cannot be established."""
