"""The Roomba+ integration — extends the HA Core Roomba integration.

Connects to Wi-Fi enabled iRobot Roomba vacuums via local MQTT (push-based,
no polling). Cloud features are optional.

v2.0: __init__.py is now the thin setup/teardown shell. Business logic lives in:
  callbacks.py  — MQTT message handlers and mission recording
  services.py   — all service/action handlers and registration
"""
from __future__ import annotations

import asyncio
import contextlib
from functools import partial
import logging
from typing import Any

from roombapy import Roomba, RoombaConnectionError, RoombaFactory

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
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .callbacks import make_map_retrain_callback, make_mission_callback, make_mission_complete_callback
from .const import (
    CLOUD_PLATFORMS,
    CONF_BLID,
    CONF_BLOCKING_SENSORS,
    CONF_CONTINUOUS,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRESENCE_SCHEDULING_ENABLED,
    CONF_SMART_ZONE_DATA,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DOMAIN,
    LOCAL_PLATFORMS,
    ROOMBA_SESSION,
    has_pose,
    has_smart_map,
)
from .api_views import MissionHistoryView
from .mission_store import MissionStore
from .presence_manager import PresenceManager
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
from .maintenance_store import MaintenanceStore
from .map_renderer import MapRenderer, RendererConfig
from .models import MapCapability, RoombaConfigEntry, RoombaData
from .services import async_register_services, async_remove_services
from .zone_store import ZoneStore
from .geometry_store import GeometryStore

_LOGGER = logging.getLogger(__name__)


# ── Package-level helper ──────────────────────────────────────────────────────

def roomba_reported_state(roomba: Any) -> dict[str, Any]:
    """Return the reported state dict from a Roomba instance's master_state.

    This is the canonical, package-level accessor used by all entity modules.
    It mirrors RoombaData.roomba_reported_state() but accepts the bare Roomba
    object so it can be called before RoombaData is fully constructed and in
    module-level helper functions that receive only the roomba object.
    """
    return roomba.master_state.get("state", {}).get("reported", {})


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


async def async_connect_or_timeout(
    hass: HomeAssistant, roomba: Any
) -> dict[str, Any]:
    """Connect to vacuum and wait for first state push (up to 10 s)."""
    import asyncio as _asyncio

    try:
        name = None
        async with _asyncio.timeout(10):
            _LOGGER.debug("Roomba+: initialising connection to vacuum")
            await hass.async_add_executor_job(roomba.connect)
            while not roomba.roomba_connected or name is None:
                name = roomba_reported_state(roomba).get("name", None)
                if name:
                    break
                await _asyncio.sleep(1)
    except RoombaConnectionError as err:
        _LOGGER.debug("Roomba+: connection error: %s", err)
        raise CannotConnect from err
    except TimeoutError as err:
        await async_disconnect_or_timeout(hass, roomba)
        _LOGGER.debug("Roomba+: connection timed out: %s", err)
        raise CannotConnect from err

    return {ROOMBA_SESSION: roomba, CONF_NAME: name}


async def async_disconnect_or_timeout(hass: HomeAssistant, roomba: Any) -> None:
    """Disconnect from vacuum (up to 3 s)."""
    import asyncio as _asyncio

    _LOGGER.debug("Roomba+: disconnecting vacuum")
    with contextlib.suppress(TimeoutError):
        async with _asyncio.timeout(3):
            await hass.async_add_executor_job(roomba.disconnect)


# ── Entry setup / teardown ────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: RoombaConfigEntry) -> bool:
    """Set up a Roomba+ config entry.

    1. Connect to the robot via local MQTT (roombapy).
    2. Detect capability tier (NONE / EPHEMERAL / SMART).
    3. Instantiate all optional subsystems (MapRenderer, ZoneStore,
       GeometryStore, MaintenanceStore, MissionStore, BlockingManager,
       PresenceManager, IrobotCloudCoordinator).
    4. Register MQTT callbacks.
    5. Forward platform setups (LOCAL_PLATFORMS + CLOUD_PLATFORMS when
       applicable).
    6. Register domain services (idempotent).
    7. Register the REST history API view.
    """
    from .models import MapCapability, RoombaData

    blid       = entry.data[CONF_BLID]
    host       = entry.data[CONF_HOST]
    password   = entry.data[CONF_PASSWORD]
    continuous = entry.options.get(CONF_CONTINUOUS, DEFAULT_CONTINUOUS)
    delay      = entry.options.get(CONF_DELAY, DEFAULT_DELAY)

    # ── 1. Connect to robot ────────────────────────────────────────────────────
    try:
        roomba: Any = await hass.async_add_executor_job(
            partial(
                RoombaFactory.create_roomba,
                address=host,
                blid=blid,
                password=password,
                continuous=continuous,
                delay=delay,
            )
        )
    except RoombaConnectionError as err:
        raise exceptions.ConfigEntryNotReady(
            f"Roomba+: cannot connect to {host}: {err}"
        ) from err

    # Wait for the first MQTT push so entities have initial state.
    await hass.async_add_executor_job(roomba.connect)

    # ── 2. Capability detection ────────────────────────────────────────────────
    state = roomba_reported_state(roomba)
    if has_smart_map(state):
        map_capability = MapCapability.SMART
    elif has_pose(state):
        map_capability = MapCapability.EPHEMERAL
    else:
        map_capability = MapCapability.NONE

    # ── 3. Optional subsystems ─────────────────────────────────────────────────

    # ZoneStore / GeometryStore — load first so MapRenderer can reference them
    zone_store = None
    geometry_store = None
    if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        zone_store = ZoneStore()
        await zone_store.async_load(hass, entry.entry_id)
        geometry_store = GeometryStore()

    # MapRenderer (EPHEMERAL + SMART only, when map is enabled in options)
    renderer = None
    if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        if entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED):
            renderer = MapRenderer(
                RendererConfig(
                    size_px=entry.options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                    scale=float(entry.options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE)),
                ),
                geometry_store=geometry_store,
                zone_store=zone_store,
            )

    # MaintenanceStore (all capability tiers)
    maintenance_store = MaintenanceStore()
    await maintenance_store.async_load(hass, entry.entry_id)

    # MissionStore (all tiers — L1)
    mission_store = MissionStore()
    await mission_store.async_load(hass, entry.entry_id)

    # Seed L3 error state from the most recent error/stuck MissionStore record.
    # We iterate backwards so a completed mission after the last error doesn't
    # clear the persisted sensor values on restart.
    last_error_code: int | None = None
    last_error_at: str | None = None
    last_error_zone: str | None = None
    for _rec in reversed(mission_store._records):
        if _rec.get("result") in ("error", "stuck",
                                  "stuck_and_abandoned", "stuck_and_resumed"):
            last_error_code = _rec.get("error_code")
            last_error_at   = _rec.get("ended_at")
            last_error_zone = (_rec.get("zones") or [None])[0]
            break

    # BlockingManager (L5 — when CONF_BLOCKING_SENSORS is configured)
    blocking_manager = None
    blocking_sensors: list[str] = entry.options.get(CONF_BLOCKING_SENSORS, [])
    if blocking_sensors:
        blocking_manager = BlockingManager(hass=hass, config_entry=entry)

    # PresenceManager (L6 — when presence scheduling is enabled)
    presence_manager = None
    if entry.options.get(CONF_PRESENCE_SCHEDULING_ENABLED, False):
        presence_manager = PresenceManager(hass=hass, config_entry=entry)

    # IrobotCloudCoordinator (SMART robots with stored cloud credentials)
    cloud_coordinator = None
    username = entry.data.get(CONF_IROBOT_USERNAME, "")
    password_cloud = entry.data.get(CONF_IROBOT_PASSWORD, "")
    if map_capability == MapCapability.SMART and username and password_cloud:
        has_pmaps = bool(roomba_reported_state(roomba).get("pmaps"))
        cloud_coordinator = IrobotCloudCoordinator(
            hass=hass,
            config_entry=entry,
            blid=blid,
            username=username,
            password=password_cloud,
            has_pmaps=has_pmaps,
        )
        with contextlib.suppress(Exception):
            await cloud_coordinator.async_config_entry_first_refresh()

    # ── 4. Build RoombaData and store in runtime_data ─────────────────────────
    data = RoombaData(
        roomba=roomba,
        blid=blid,
        map_capability=map_capability,
        renderer=renderer,
        zone_store=zone_store,
        geometry_store=geometry_store,
        maintenance_store=maintenance_store,
        mission_store=mission_store,
        cloud_coordinator=cloud_coordinator,
        blocking_manager=blocking_manager,
        presence_manager=presence_manager,
        last_error_code=last_error_code,
        last_error_at=last_error_at,
        last_error_zone=last_error_zone,
    )
    entry.runtime_data = data

    # ── 5. Register MQTT callbacks ─────────────────────────────────────────────
    mission_cb = make_mission_callback(hass, entry)
    roomba.register_on_message_callback(mission_cb)

    if cloud_coordinator is not None:
        complete_cb = make_mission_complete_callback(hass, cloud_coordinator)
        roomba.register_on_message_callback(complete_cb)

        retrain_cb = make_map_retrain_callback(hass, cloud_coordinator)
        roomba.register_on_message_callback(retrain_cb)

    # ── 6. Platform setup ──────────────────────────────────────────────────────
    await hass.config_entries.async_forward_entry_setups(entry, LOCAL_PLATFORMS)

    if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        if renderer is not None:
            hass.config_entries.async_forward_entry_setup(entry, Platform.IMAGE)

    if cloud_coordinator is not None:
        await hass.config_entries.async_forward_entry_setups(
            entry, CLOUD_PLATFORMS
        )

    # ── 7. Domain services (idempotent) ────────────────────────────────────────
    async_register_services(hass)

    # ── 8. REST history API ────────────────────────────────────────────────────
    hass.http.register_view(MissionHistoryView())

    # ── 9. HA stop listener ────────────────────────────────────────────────────
    async def _async_stop(event: Any) -> None:  # noqa: ANN401
        await hass.async_add_executor_job(roomba.disconnect)

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: RoombaConfigEntry
) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: RoombaConfigEntry
) -> bool:
    """Unload a Roomba+ config entry."""
    data: Any = entry.runtime_data

    # Unload platforms
    platforms_to_unload = list(LOCAL_PLATFORMS)
    if data.cloud_coordinator is not None:
        platforms_to_unload.extend(CLOUD_PLATFORMS)
    if data.renderer is not None:
        platforms_to_unload.append(Platform.IMAGE)

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, platforms_to_unload
    )

    if unload_ok:
        # Cancel subsystems that hold async tasks
        if data.blocking_manager is not None:
            await data.blocking_manager.cancel_queue()
        if data.presence_manager is not None:
            data.presence_manager.cancel()

        # Disconnect the robot
        with contextlib.suppress(Exception):
            await hass.async_add_executor_job(data.roomba.disconnect)

    # Remove services only when this is the last entry
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        async_remove_services(hass)

    return unload_ok


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Migrate config entry to the current version.

    Version history:
      1 → 2 (v2.0):   Cloud coordinator now stores raw mission records alongside
                      aggregates. A marker key is added to options so the coordinator
                      knows to persist raw_records on its next fetch. All existing
                      user data (zone names, maintenance baselines, blocking/presence
                      config) is preserved unchanged. MissionStore hass.storage data
                      is unaffected — it is keyed by entry_id, not entry version.
      2 → 3 (v2.1.0): MaintenanceStore gains baseline_estcap and consecutive_skips.
      3 → 4 (v2.1.1): Entity unique_ids normalised — 37 entities renamed from
                      German slugs to English slugs in the entity registry so that
                      automations, history, and the Lovelace card are unaffected.
    """
    current = config_entry.version
    _LOGGER.info(
        "Roomba+: migrating config entry %s from version %d",
        config_entry.entry_id, current,
    )

    if current == 1:
        # v1 → v2: mark that raw cloud records should be stored.
        # No existing data is removed or altered.
        new_options = dict(config_entry.options)
        new_options.setdefault("cloud_raw_records_version", 1)
        hass.config_entries.async_update_entry(
            config_entry,
            options=new_options,
            version=2,
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 2 (raw cloud records enabled)",
            config_entry.entry_id,
        )
        current = 2

    if current == 2:
        # v2 → v3 (v2.1.0): add baseline_estcap and consecutive_skips to
        # MaintenanceStore storage so F5d and F6g have correct defaults on
        # first load.  MaintenanceStore.async_load() already handles missing
        # keys gracefully via .get() — this migration adds the keys explicitly
        # so the storage file reflects the current schema.
        from homeassistant.helpers.storage import Store as _Store
        _store = _Store(
            hass, 1,
            f"roomba_plus_maintenance_{config_entry.entry_id}"
        )
        _data = await _store.async_load() or {}
        _data.setdefault("baseline_estcap", None)
        _data.setdefault("consecutive_skips", 0)
        await _store.async_save(_data)
        hass.config_entries.async_update_entry(
            config_entry,
            version=3,
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 3 "
            "(baseline_estcap + consecutive_skips added to MaintenanceStore)",
            config_entry.entry_id,
        )
        current = 3

    if current == 3:
        # v3 → v4: normalise entity_ids to English slugs.
        #
        # Pre-v1.8.3 versions generated entity_id from the translated display
        # name rather than the fixed English translation_key, producing German
        # (or other language) entity_ids on non-English HA installations.
        # unique_ids were always English — only entity_ids are affected.
        #
        # Generic: works for any device name and any HA language.
        # Device slug is discovered from the vacuum entity (no suffix in uid).
        # Each entity is renamed to platform.device_slug_translation_key.
        # Safe on clean installs — all correct means 0 renames.
        from homeassistant.helpers import entity_registry as er

        _UID_TO_KEY: dict[tuple[str, bool], tuple[str, str]] = {
            ("phase",               False): ("sensor",        "phase"),
            ("error",               False): ("sensor",        "error"),
            ("readiness",           False): ("sensor",        "readiness"),
            ("job_initiator",       False): ("sensor",        "job_initiator"),
            ("clean_mode",          False): ("sensor",        "clean_mode"),
            ("carpet_boost_mode",   False): ("sensor",        "carpet_boost_mode"),
            ("filter_remaining_hours", False): ("sensor",     "filter_remaining_hours"),
            ("brush_remaining_hours", False): ("sensor",      "brush_remaining_hours"),
            ("battery_cycles",      False): ("sensor",        "battery_cycles"),
            ("total_missions",      False): ("sensor",        "total_missions"),
            ("successful_missions", False): ("sensor",        "successful_missions"),
            ("canceled_missions",   False): ("sensor",        "canceled_missions"),
            ("failed_missions",     False): ("sensor",        "failed_missions"),
            ("total_cleaning_time", False): ("sensor",        "total_cleaning_time"),
            ("average_mission_time", False): ("sensor",       "average_mission_time"),
            ("total_cleaned_area",  False): ("sensor",        "total_cleaned_area"),
            ("last_mission",        False): ("sensor",        "last_mission"),
            ("scrubs_count",        False): ("sensor",        "scrubs_count"),
            ("rssi",                False): ("sensor",        "rssi"),
            ("snr",                 False): ("sensor",        "snr"),
            ("signal_noise",        False): ("sensor",        "signal_noise"),
            ("ip_address",          False): ("sensor",        "ip_address"),
            ("nav_quality",         False): ("sensor",        "nav_quality"),
            ("mission_start_time",  False): ("sensor",        "mission_start_time"),
            ("mission_elapsed_time", False): ("sensor",       "mission_elapsed_time"),
            ("mission_recharge_time", False): ("sensor",      "mission_recharge_time"),
            ("mission_expire_time", False): ("sensor",        "mission_expire_time"),
            ("mission_recharge_minutes", False): ("sensor",   "mission_recharge_minutes"),
            ("mission_expire_minutes", False): ("sensor",     "mission_expire_minutes"),
            ("mission_id",          False): ("sensor",        "mission_id"),
            ("next_clean",          False): ("sensor",        "next_clean"),
            ("clean_base_status",   False): ("sensor",        "clean_base_status"),
            ("dock_tank_level",     False): ("sensor",        "dock_tank_level"),
            ("tank_level",          False): ("sensor",        "tank_level"),
            ("mop_pad",             False): ("sensor",        "mop_pad"),
            ("mop_behavior",        False): ("sensor",        "mop_behavior"),
            ("mop_tank_level",      False): ("sensor",        "mop_tank_level"),
            ("filter_last_replaced", False): ("sensor",       "filter_last_replaced"),
            ("brush_last_replaced", False): ("sensor",        "brush_last_replaced"),
            ("pad_last_replaced",   False): ("sensor",        "pad_last_replaced"),
            ("battery_last_replaced", False): ("sensor",      "battery_last_replaced"),
            ("clean_streak",        False): ("sensor",        "clean_streak"),
            ("missions_last_30d",   False): ("sensor",        "missions_last_30d"),
            ("completion_rate_30d", False): ("sensor",        "completion_rate_30d"),
            ("area_cleaned_today",  False): ("sensor",        "area_cleaned_today"),
            ("last_mission_result", False): ("sensor",        "last_mission_result"),
            ("last_mission_duration", False): ("sensor",      "last_mission_duration"),
            ("last_error_code",     False): ("sensor",        "last_error_code"),
            ("last_error_at",       False): ("sensor",        "last_error_at"),
            ("last_error_zone",     False): ("sensor",        "last_error_zone"),
            ("stuck_count_30d",     False): ("sensor",        "stuck_count_30d"),
            ("problem_zone",        False): ("sensor",        "problem_zone"),
            ("presence_clean_opportunities_7d", False): ("sensor", "presence_clean_opportunities_7d"),
            ("presence_clean_utilisation_7d", False): ("sensor", "presence_clean_utilisation_7d"),
            ("next_likely_clean_window", False): ("sensor",   "next_likely_clean_window"),
            ("filter_wear_rate",    False): ("sensor",        "filter_wear_rate"),
            ("brush_wear_rate",     False): ("sensor",        "brush_wear_rate"),
            ("pad_wear_rate",       False): ("sensor",        "pad_wear_rate"),
            ("filter_days_until_due", False): ("sensor",      "filter_days_until_due"),
            ("brush_days_until_due", False): ("sensor",       "brush_days_until_due"),
            ("pad_days_until_due",  False): ("sensor",        "pad_days_until_due"),
            ("battery_capacity_mah", False): ("sensor",       "battery_capacity_mah"),
            ("nav_panics",          False): ("sensor",        "nav_panics"),
            ("cliff_events_front",  False): ("sensor",        "cliff_events_front"),
            ("cliff_events_rear",   False): ("sensor",        "cliff_events_rear"),
            ("battery_capacity_retention", False): ("sensor", "battery_capacity_retention"),
            ("estimated_battery_eol", False): ("sensor",      "estimated_battery_eol"),
            ("consecutive_clean_skips", False): ("sensor",    "consecutive_clean_skips"),
            ("raw_state",           False): ("sensor",        "raw_state"),
            ("battery",             False): ("sensor",        "battery_level"),
            ("lifetime_area",       True):  ("sensor",        "lifetime_area"),
            ("lifetime_time",       True):  ("sensor",        "lifetime_time"),
            ("lifetime_missions",   True):  ("sensor",        "lifetime_missions"),
            ("recent_completion_rate", True): ("sensor",      "recent_completion_rate"),
            ("recent_recharges",    True):  ("sensor",        "recent_recharges"),
            ("recent_evacuations",  True):  ("sensor",        "recent_evacuations"),
            ("recent_dirt_events",  True):  ("sensor",        "recent_dirt_events"),
            ("recent_error_code",   True):  ("sensor",        "recent_error_code"),
            ("recent_error_time",   True):  ("sensor",        "recent_error_time"),
            ("recent_wifi_floor",   True):  ("sensor",        "recent_wifi_floor"),
            ("recent_wifi_stability", True): ("sensor",       "recent_wifi_stability"),
            ("recent_cleaning_speed", True): ("sensor",       "recent_cleaning_speed"),
            ("recent_dirt_density", True):  ("sensor",        "recent_dirt_density"),
            ("recent_recharge_fraction", True): ("sensor",    "recent_recharge_fraction"),
            ("cleaning_speed_trend", True): ("sensor",        "cleaning_speed_trend"),
            ("recent_coverage_pct", True):  ("sensor",        "recent_coverage_pct"),
            ("bin_full",            False): ("binary_sensor", "bin_full"),
            ("bin_present",         False): ("binary_sensor", "bin_present"),
            ("connected",           False): ("binary_sensor", "connected"),
            ("mop_ready",           False): ("binary_sensor", "mop_ready"),
            ("mop_tank_present",    False): ("binary_sensor", "mop_tank_present"),
            ("mop_lid_closed",      False): ("binary_sensor", "mop_lid_closed"),
            ("map_saving",          False): ("binary_sensor", "map_saving"),
            ("maintenance_due",     False): ("binary_sensor", "maintenance_due"),
            ("start_blocked",       False): ("binary_sensor", "start_blocked"),
            ("schedule_hold_active", False): ("binary_sensor","schedule_hold_active"),
            ("mop_lid_open",        False): ("binary_sensor", "mop_lid_open"),
            ("mop_tank_present_direct", False): ("binary_sensor","mop_tank_present_direct"),
            ("mid_mission_recharge", False): ("binary_sensor","mid_mission_recharge"),
            ("evac",                False): ("button",        "evac"),
            ("locate",              False): ("button",        "locate"),
            ("map_training",        False): ("button",        "map_training"),
            ("spot",                False): ("button",        "spot"),
            ("quick",               False): ("button",        "quick"),
            ("sleep",               False): ("button",        "sleep"),
            ("power_off",           False): ("button",        "power_off"),
            ("reset_filter",        False): ("button",        "reset_filter"),
            ("reset_brush",         False): ("button",        "reset_brush"),
            ("reset_battery",       False): ("button",        "reset_battery"),
            ("clean_zone",          False): ("button",        "clean_zone"),
            ("repeat_mission",      False): ("button",        "repeat_mission"),
            ("clean_smart_zone",    False): ("button",        "clean_smart_zone"),
            ("cleaning_passes",     False): ("select",        "cleaning_passes"),
            ("zone_select",         False): ("select",        "zone_select"),
            ("smart_zone_select",   False): ("select",        "smart_zone_select"),
            ("disposable_pad_wetness", False): ("select",     "disposable_pad_wetness"),
            ("reusable_pad_wetness", False): ("select",       "reusable_pad_wetness"),
            ("edge_clean",          False): ("switch",        "edge_clean"),
            ("always_finish",       False): ("switch",        "always_finish"),
            ("schedule_hold",       False): ("switch",        "schedule_hold"),
            ("map",                 False): ("image",         "map"),
        }

        entity_reg = er.async_get(hass)
        blid = config_entry.data["blid"]
        bare_prefix = f"roomba_plus_{blid}_"
        cloud_prefix = f"roomba_plus_{blid}_cloud_"
        vacuum_uid = f"roomba_plus_{blid}"

        # entity_reg.entities may not be populated when the registry store has
        # not been loaded yet (e.g. during early startup or in test stubs).
        # Guard gracefully — fresh installs have nothing to rename anyway.
        all_entities = getattr(entity_reg, "entities", None)
        renamed = 0

        if all_entities is not None:
            vacuum_entry = next(
                (e for e in all_entities.values()
                 if e.unique_id == vacuum_uid and e.platform == DOMAIN),
                None,
            )
            if vacuum_entry is None:
                _LOGGER.warning(
                    "Roomba+: entity normalisation skipped — vacuum entity not found"
                )
            else:
                device_slug = vacuum_entry.entity_id[len("vacuum."):]
                needs_temp: list[tuple[str, str]] = []

                for entry in list(all_entities.values()):
                    if entry.platform != DOMAIN:
                        continue
                    uid = entry.unique_id
                    if uid.startswith(cloud_prefix):
                        key_suffix, is_cloud = uid[len(cloud_prefix):], True
                    elif uid.startswith(bare_prefix):
                        key_suffix, is_cloud = uid[len(bare_prefix):], False
                    elif uid == vacuum_uid:
                        expected_vac = f"vacuum.{device_slug}"
                        if entry.entity_id != expected_vac and entity_reg.async_get(expected_vac) is None:
                            entity_reg.async_update_entity(entry.entity_id, new_entity_id=expected_vac)
                            renamed += 1
                        continue
                    else:
                        continue

                    lookup = (key_suffix, is_cloud)
                    if lookup not in _UID_TO_KEY:
                        continue
                    platform, translation_key = _UID_TO_KEY[lookup]
                    expected_eid = f"{platform}.{device_slug}_{translation_key}"
                    current_eid = entry.entity_id
                    if current_eid == expected_eid:
                        continue

                    existing = entity_reg.async_get(expected_eid)
                    if existing is None:
                        entity_reg.async_update_entity(current_eid, new_entity_id=expected_eid)
                        renamed += 1
                        _LOGGER.debug("Roomba+: renamed %s → %s", current_eid, expected_eid)
                    elif existing.unique_id == uid:
                        entity_reg.async_remove(current_eid)
                        renamed += 1
                        _LOGGER.debug("Roomba+: removed duplicate %s", current_eid)
                    else:
                        temp_eid = f"{current_eid}_mig_tmp"
                        entity_reg.async_update_entity(current_eid, new_entity_id=temp_eid)
                        needs_temp.append((temp_eid, expected_eid))

                for temp_eid, final_eid in needs_temp:
                    existing = entity_reg.async_get(final_eid)
                    if existing is None:
                        entity_reg.async_update_entity(temp_eid, new_entity_id=final_eid)
                        renamed += 1
                        _LOGGER.debug("Roomba+: resolved %s → %s", temp_eid, final_eid)
                    else:
                        entity_reg.async_remove(temp_eid)
                        renamed += 1

        _LOGGER.info(
            "Roomba+: migrated entry %s to version 4 "
            "(%d entities normalised to English slugs)",
            config_entry.entry_id, renamed,
        )

        hass.config_entries.async_update_entry(config_entry, version=4)
        current = 4

    if current == config_entry.version:
        _LOGGER.debug(
            "Roomba+: config entry %s already at version %d — no migration needed",
            config_entry.entry_id, current,
        )

    return True