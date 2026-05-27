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
    ATTR_OVERRIDE_BLOCKING,
    ATTR_ROOM_NAME,
    ATTR_ROOMS,
    CONF_BLID,
    CONF_BLOCKING_SENSORS,
    CONF_CONTINUOUS,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
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
    SERVICE_RESET_BATTERY,
    SERVICE_RESET_BRUSH,
    SERVICE_RESET_FILTER,
    SERVICE_RESET_PAD,
    SERVICE_SMART_START,
    has_pose,
    has_smart_map,
)
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
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
    cloud_pmap_id: str | None = None,
) -> list[tuple[str, str]]:
    """Resolve room names to (region_id, pmap_id) tuples.

    Args:
        zone_data:      smart_zone_data from config_entry.options —
                        {region_id: {"name": str, "pmap_id": str}}
        room_names:     user-supplied room names from the service call.
        state:          live robot state — used to resolve pmap_id when the stored
                        value is empty (MQTT fallback).
        cloud_pmap_id:  authoritative pmap_id from the cloud coordinator.
                        When present this is preferred over the MQTT cascade for
                        zones whose stored pmap_id is empty.

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

    # Resolve empty pmap_ids for zones whose stored value is empty
    # (manual entry before MQTT data arrived, or after a map retrain).
    #
    # Priority order:
    #   0. cloud_pmap_id — authoritative value from /pmaps endpoint. Always
    #      current; immune to stale MQTT state. Used when the cloud coordinator
    #      is active.
    #   1. lastCommand.pmap_id — most recent MQTT value; reliable on single-map
    #      robots and correct immediately after a region clean.
    #   2. cleanSchedule2[].cmd.pmap_id — stable between sessions even when
    #      lastCommand has been overwritten by a full-home clean.
    #   3. pmaps[0] key — last resort; correct on single-map robots but
    #      undefined order on multi-map robots.
    last = state.get("lastCommand", {})
    pmaps: list[dict] = state.get("pmaps", [])
    fallback_pmap_id: str = (
        cloud_pmap_id
        or last.get("pmap_id")
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
        (rid, pmap_id if pmap_id else fallback_pmap_id)
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

        # When cloud is active, supplement (or replace) zone_data with cloud
        # regions. Cloud is authoritative — if the same region_id exists in
        # both, the cloud name wins. This means clean_room works by room name
        # even when the user has never gone through the repair naming flow.
        if data.has_cloud:
            cc = data.cloud_coordinator  # type: ignore[union-attr]
            cloud_zone_data: dict = {
                str(r["id"]): {
                    "name": r["name"],
                    "pmap_id": r["pmap_id"],
                }
                for r in cc.regions
                if r.get("id") and r.get("name")
            }
            # Cloud zones (custom zones, not rooms) also addressable by name
            cloud_zone_data.update({
                str(z["id"]): {
                    "name": z["name"],
                    "pmap_id": z["pmap_id"],
                }
                for z in cc.zones
                if z.get("id") and z.get("name")
            })
            # Merge: cloud wins on conflict, local-only entries preserved
            zone_data = {**zone_data, **cloud_zone_data}

        if not zone_data:
            raise ServiceValidationError(
                "No rooms configured yet. Run a room-targeted clean via the "
                "iRobot app first, then assign names in the Roomba+ options.",
                translation_domain=DOMAIN,
                translation_key="no_rooms_configured",
            )

        # Read live state before resolving rooms — needed for pmap_id fallback.
        state = roomba_reported_state(data.roomba)

        # Cloud pmap_id takes priority over the MQTT cascade when available.
        cloud_pmap_id: str | None = None
        if data.has_cloud:
            cloud_pmap_id = data.cloud_coordinator.active_pmap_id  # type: ignore[union-attr]
            if cloud_pmap_id:
                _LOGGER.debug(
                    "clean_room: using cloud pmap_id %s for %s",
                    cloud_pmap_id[:12], entity_id,
                )

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
        resolved = _resolve_rooms(zone_data, room_names, state, cloud_pmap_id)

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

        # user_pmapv_id is intentionally omitted from the payload.
        # Sending it requires the value to exactly match the robot's current
        # internal map version. If MQTT state is even slightly stale the robot
        # rejects the command with error 224. Omitting it tells the robot to
        # use whichever pmapv it currently has — the same approach used by
        # ia74/roomba_rest980, which works on older lewis firmware.
        # The resolved value is logged for diagnostic comparison only.
        params = {
            "ordered": 1 if ordered else 0,
            "pmap_id": pmap_id,
            "regions": [
                # region_id as string — confirmed correct on lewis firmware.
                # params sub-object required on older firmware; omitting it
                # causes error 224 because the robot cannot determine pass config.
                {
                    "region_id": rid,
                    "type": "rid",
                    "params": {"noAutoPasses": False, "twoPass": False},
                }
                for rid, _ in resolved
            ],
        }

        _LOGGER.info(
            "clean_room: %s → regions=%s pmap=%s pmapv=%s (not sent)",
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

    # ── v1.7.0 L5 — Blocking manager ─────────────────────────────────────────
    blocking_manager: BlockingManager | None = None
    if config_entry.options.get(CONF_BLOCKING_SENSORS):
        blocking_manager = BlockingManager(hass, config_entry)
        _LOGGER.debug(
            "Roomba+ blocking manager active — sensors: %s",
            config_entry.options[CONF_BLOCKING_SENSORS],
        )

    # ── Cloud coordinator (SMART robots + credentials only) ────────────────
    cloud_coordinator: IrobotCloudCoordinator | None = None
    irobot_username = config_entry.data.get(CONF_IROBOT_USERNAME)
    irobot_password = config_entry.data.get(CONF_IROBOT_PASSWORD)

    if map_capability == MapCapability.SMART and irobot_username and irobot_password:
        cloud_coordinator = IrobotCloudCoordinator(
            hass=hass,
            config_entry=config_entry,
            blid=config_entry.data[CONF_BLID],
            username=irobot_username,
            password=irobot_password,
        )
        try:
            await cloud_coordinator.async_config_entry_first_refresh()
            _LOGGER.info(
                "Roomba+ cloud: coordinator active for %s (%d pmap(s))",
                config_entry.data[CONF_BLID],
                len(cloud_coordinator.data.get("pmaps", [])),
            )
        except Exception:  # noqa: BLE001
            # Cloud failure must never prevent local MQTT from working.
            # Log and continue — the coordinator will retry on its interval.
            _LOGGER.warning(
                "Roomba+ cloud: initial fetch failed for %s — "
                "local operation unaffected, cloud features unavailable until retry",
                config_entry.data[CONF_BLID],
            )

    config_entry.runtime_data = RoombaData(
        roomba=roomba,
        blid=config_entry.data[CONF_BLID],
        map_capability=map_capability,
        renderer=renderer,
        zone_store=zone_store,
        geometry_store=geometry_store,
        maintenance_store=maintenance_store,
        cloud_coordinator=cloud_coordinator,
        blocking_manager=blocking_manager,
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

    # Cloud platforms are only meaningful for SMART robots with credentials.
    # select.py and button.py gate their cloud entities on data.has_cloud
    # internally, so forwarding is safe even when the coordinator is None —
    # but we skip the forward entirely for non-SMART robots to avoid creating
    # empty platform registrations.
    if map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)

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

    # ── v1.7.0 L2 — Maintenance reset services ───────────────────────────────
    # Named services callable from automations. Buttons call the same store
    # methods via the UI — both paths are valid and non-redundant.

    def _resolve_entry_from_entity(entity_id_call: str) -> RoombaConfigEntry | None:
        """Return the config entry for a given vacuum entity_id."""
        ent_reg = er.async_get(hass)
        entry_reg = ent_reg.async_get(entity_id_call)
        if entry_reg is None:
            return None
        return hass.config_entries.async_get_entry(entry_reg.config_entry_id)

    async def _handle_reset_service(call: ServiceCall, part: str) -> None:
        entity_ids: list[str] = call.data.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        for eid in entity_ids:
            cfg = _resolve_entry_from_entity(eid)
            if cfg is None:
                _LOGGER.warning("reset_%s: config entry not found for %s", part, eid)
                continue
            data: RoombaData = cfg.runtime_data
            if data.maintenance_store is None:
                raise ServiceValidationError(
                    f"Maintenance store not available for {eid}",
                    translation_domain=DOMAIN,
                    translation_key="maintenance_store_unavailable",
                )
            state = data.roomba_reported_state()
            current_hr: int = state.get("bbrun", {}).get("hr", 0)
            getattr(data.maintenance_store, f"reset_{part}")(current_hr)
            await data.maintenance_store.async_save(hass, cfg.entry_id)
            _LOGGER.info("reset_%s: executed for %s at %dh", part, eid, current_hr)

    _RESET_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})
    for _part in ("filter", "brush", "battery", "pad"):
        _p = _part
        if not hass.services.has_service(DOMAIN, f"reset_{_p}"):
            hass.services.async_register(
                DOMAIN,
                f"reset_{_p}",
                lambda call, p=_p: _handle_reset_service(call, p),
                schema=_RESET_SCHEMA,
            )

    # ── v1.7.0 L5 — smart_start service ───────────────────────────────────────
    if not hass.services.has_service(DOMAIN, SERVICE_SMART_START):
        async def _handle_smart_start(call: ServiceCall) -> None:
            """Handle roomba_plus.smart_start with blocking-sensor gate."""
            entity_ids: list[str] = call.data.get("entity_id", [])
            if isinstance(entity_ids, str):
                entity_ids = [entity_ids]
            rooms: list[str] | None = call.data.get(ATTR_ROOMS)
            override: bool = call.data.get(ATTR_OVERRIDE_BLOCKING, False)

            for eid in entity_ids:
                cfg = _resolve_entry_from_entity(eid)
                if cfg is None:
                    raise ServiceValidationError(
                        f"Entity {eid} not found",
                        translation_domain=DOMAIN,
                        translation_key="entity_not_found",
                    )
                data: RoombaData = cfg.runtime_data
                if rooms and data.map_capability != MapCapability.SMART:
                    raise ServiceValidationError(
                        f"{eid} does not support room targeting — "
                        "only i7, s9, and j-series robots support this.",
                        translation_domain=DOMAIN,
                        translation_key="not_smart_map",
                    )
                if data.blocking_manager is not None:
                    await data.blocking_manager.check_and_start(rooms, override)
                elif rooms:
                    # No blocking config — delegate directly to clean_room
                    await hass.services.async_call(
                        DOMAIN,
                        SERVICE_CLEAN_ROOM,
                        {"entity_id": eid, ATTR_ROOM_NAME: rooms},
                        blocking=True,
                    )
                else:
                    await hass.async_add_executor_job(data.roomba.start)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SMART_START,
            _handle_smart_start,
            schema=vol.Schema({
                vol.Required("entity_id"): cv.entity_ids,
                vol.Optional(ATTR_ROOMS): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional(ATTR_OVERRIDE_BLOCKING, default=False): cv.boolean,
            }),
        )

    # Reload on options change (continuous/delay require reconnect)
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_change)
    )

    # ── Map retrain detector ────────────────────────────────────────────────
    # iRobot increments user_pmapv_id in the MQTT state whenever the user
    # retrains or edits a Smart Map. This is the earliest signal that pmap
    # data has changed — earlier than any cloud /pmaps poll would notice.
    # When we see a new value, schedule an immediate cloud coordinator refresh
    # so region names and pmap_id stay in sync without waiting 24 hours.
    if cloud_coordinator is not None:
        _last_pmapv: dict[str, str] = {}   # pmap_id → last seen pmapv_id

        def _on_roomba_message(json_data: dict[str, Any]) -> None:
            reported = json_data.get("state", {}).get("reported", {})
            pmaps: list[dict] = reported.get("pmaps", [])
            changed = False
            for pmap_entry in pmaps:
                for pid, pmapv in pmap_entry.items():
                    if _last_pmapv.get(pid) not in (None, pmapv):
                        _LOGGER.info(
                            "Roomba+ cloud: user_pmapv_id changed for pmap %s "
                            "(%s → %s) — triggering cloud refresh",
                            pid[:12], _last_pmapv[pid][:12], pmapv[:12],
                        )
                        changed = True
                    _last_pmapv[pid] = pmapv
            if changed:
                hass.async_create_task(
                    cloud_coordinator.async_request_refresh()
                )

        config_entry.runtime_data.roomba.register_on_message_callback(
            _on_roomba_message
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
    # Must exactly mirror async_setup_entry platform forwarding.
    # CLOUD_PLATFORMS are forwarded for all SMART robots (credentials or not),
    # so they must also be unloaded for all SMART robots.
    if data.map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, platforms
    )
    if unload_ok:
        # Cancel any pending blocking queue before disconnecting
        bm = config_entry.runtime_data.blocking_manager
        if bm is not None:
            bm.cancel_queue()

        await async_disconnect_or_timeout(
            hass, roomba=config_entry.runtime_data.roomba
        )
        # Remove domain services when the last entry is unloaded.
        if not hass.config_entries.async_entries(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_CLEAN_ROOM)
            for _svc in (SERVICE_SMART_START, "reset_filter", "reset_brush",
                         "reset_battery", "reset_pad"):
                if hass.services.has_service(DOMAIN, _svc):
                    hass.services.async_remove(DOMAIN, _svc)
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
