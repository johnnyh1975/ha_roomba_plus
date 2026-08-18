"""The Roomba+ integration — extends the HA Core Roomba integration.

Connects to Wi-Fi enabled iRobot Roomba vacuums via local MQTT (push-based,
no polling). Cloud features are optional.

v2.0: __init__.py is now the thin setup/teardown shell. Business logic lives in:
  callbacks.py  — MQTT message handlers and mission recording
  services.py   — all service/action handlers and registration
"""
from __future__ import annotations

from collections.abc import Mapping

import asyncio
import contextlib
import dataclasses
from datetime import timedelta
from functools import partial
import logging
from typing import Any, Final

from roombapy import Roomba, RoombaConnectionError, RoombaFactory

from homeassistant import exceptions
from homeassistant.const import (
    CONF_DELAY,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .callbacks import (
    make_map_retrain_callback,
    make_map_updating_callback,
    make_mission_callback,
    make_mission_complete_callback,
    make_cloud_refresh_callback,
)
from .const import (
    ISSUE_TRACKER_URL,
    CONF_BLID,
    CONF_BLOCKING_SENSORS,
    CONF_CONNECTION_TYPE,
    CONF_CONTINUOUS,
    CONF_ENABLE_MAINTENANCE_LIST,
    CONF_ENABLE_SCHEDULE_CALENDAR,
    CONF_FLOOR,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRESENCE_SCHEDULING_ENABLED,
    CONF_DEMAND_CLEANING_ENABLED,
    CONF_SMART_ZONE_DATA,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_ENABLE_MAINTENANCE_LIST,
    DEFAULT_ENABLE_SCHEDULE_CALENDAR,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DOMAIN,
    LOCAL_PLATFORMS,
    ROOMBA_SESSION,
    get_robot_profile,
    has_pose,
    has_smart_map,
)
from .api_views import DailyDigestView, MissionHistoryView, HouseholdSummaryView, MissionHistoryImportView, ExplainMissionView, MissionPathView, MissionMapJsonView, MissionMapPngView
from .grid_store import GridStore
from .room_seg_store import RoomSegStore
from .mission_store import MissionStore
from .mission_archive import MissionArchive  # v2.8.0 ARC1
from .presence_manager import PresenceManager
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
from .dirt_threshold_manager import DirtThresholdManager
from .outline_store import OutlineStore
from .mission_trajectory_store import MissionTrajectoryStore
from .freeze_snapshot_store import FreezeSnapshotStore
from .maintenance_store import MaintenanceStore
from .robot_profile_store import RobotProfileStore  # v2.6 L4
from .mission_timer_store import MissionTimerStore  # v2.6 MP1
from .map_renderer import (
    MapRenderer,
    RendererConfig,
    ROBOT_DIAMETER_MM_900_SERIES,
    ROBOT_DIAMETER_MM_ISJ_SERIES,
    ROBOT_DIAMETER_MM_DEFAULT,
)
from .migrations import async_migrate_entry  # noqa: F401 -- re-exported for HA's own lookup
from .structural_failures import record_failure, record_success
from .models import ConnectionType, MapCapability, RoombaConfigEntry, RoombaData
from .services import async_register_services, async_remove_services
from .geometry_store import GeometryStore
from .prime_coordinator import (
    PrimeCoordinator,
    PrimePartsCoordinator,
    PrimeScheduleCoordinator,
    PrimeStatusCoordinator,
)
from ._prime_login_bridge import pop_pending_login

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from roombapy_prime import (
    AuthConnectionError,
    AuthCredentialsError,
    AuthError,
    AuthRateLimitedError,
    AuthSSLError,
    AuthTimeoutError,
    PrimeFactory,
)

_LOGGER = logging.getLogger(__name__)




# v2.3.0 F8 — UmfAligner re-alignment helpers ─────────────────────────────────

async def _async_seed_l5_from_archive(
    hass: Any,
    entry_id: str,
    mission_archive: "MissionArchive",
    robot_profile_store: "RobotProfileStore",
) -> None:
    """Seed per-room dirt index (L5) from full ARC1 archive history.

    L5-ARC (v2.8.0) — One-time bootstrap of room_dirt_index EMA over the
    complete cloud mission history, replacing the cold-start behaviour where
    L5 converges slowly from zero after a fresh install.

    Processes records oldest-to-newest so the EMA weights recent missions most
    heavily (same update rule as the incremental path).

    Guards:
      - Skips when room_dirt_index is already populated (avoid re-seeding).
      - Skips when archive initial load is not yet complete.
      - Skips when archive is empty.
    """
    if robot_profile_store.room_dirt_index:
        return  # already seeded — incremental path handles new missions
    if not mission_archive.initial_load_done:
        return  # archive back-fill still running; will be seeded next restart
    if mission_archive.record_count == 0:
        return

    from .const import SQFT_TO_M2

    seeded_count = 0
    for record in mission_archive.all_derived_oldest_first():
        rooms_completed: dict[str, Any] = record.get("rooms_completed") or {}
        for rid, data in rooms_completed.items():
            # Bug-hunt (v2.8.0): this function is awaited directly inside
            # async_setup_entry (not via hass.async_create_task) — an
            # uncaught exception here fails integration setup entirely,
            # not just this one feature. A corrupted/hand-edited persisted
            # archive record could have a non-dict value for a room entry
            # even though mission_archive.py's own writer always produces
            # dicts; defend at the read boundary rather than trusting the
            # storage file's shape forever.
            if not isinstance(data, dict):
                continue
            passes = int(data.get("passes") or 0)
            area_sqft = float(data.get("area") or 0)   # 'or 0' handles area=None
            area_m2 = area_sqft * SQFT_TO_M2
            if rid and passes > 0 and area_m2 > 0:
                robot_profile_store.update_room_dirt_index(rid, passes, area_m2)
                seeded_count += 1

    if robot_profile_store.room_dirt_index:
        await robot_profile_store.async_save(hass, entry_id)
        _LOGGER.info(
            "L5-ARC: seeded room_dirt_index for %d room(s) from %d archive "
            "mission(s) for entry %s",
            len(robot_profile_store.room_dirt_index),
            mission_archive.record_count,
            entry_id,
        )


async def _async_seed_l3_from_archive(
    mission_archive: "MissionArchive",
    mission_store: "MissionStore",
) -> None:
    """Seed MissionStore.archive_baseline from ARC1 full history.

    L3-ARC (v2.8.0) — Computes the statistical anomaly-detection baseline
    (duration mean/std, area mean/std, dirt p75) from the complete cloud
    mission history and injects it into MissionStore.archive_baseline.

    This allows consecutive_anomalous to detect anomalies even during the
    first weeks of use when the local MQTT store has < 20 missions.

    Guards:
      - Skips when archive initial load is not complete.
      - Skips when archive has < 20 completed missions (compute_archive_stats
        returns None).

    Not persisted — recomputed at each startup to reflect latest archive.
    """
    if not mission_archive.initial_load_done:
        return
    if mission_archive.record_count < 20:
        return

    from .mission_store import MissionStore

    baseline = MissionStore.compute_archive_stats(
        mission_archive.all_derived_oldest_first()
    )
    if baseline is not None:
        mission_store.archive_baseline = baseline
        _LOGGER.info(
            "L3-ARC: archive baseline set from %d record(s) — "
            "duration_mean=%.1f std=%.1f area_mean=%s dirt_p75=%s",
            mission_archive.record_count,
            baseline["duration_mean"],
            baseline["duration_std"],
            f"{baseline['area_mean']:.1f}" if baseline["area_mean"] else "n/a",
            f"{baseline['dirt_p75']:.1f}" if baseline["dirt_p75"] else "n/a",
        )


# ── SETUP-SPLIT Teil A (v3.0.0) ──────────────────────────────────────────────
# _SetupContext collects all local variables that span multiple phases of
# async_setup_entry so they can be passed cleanly between named phase functions.


def _connection_type(config_entry: RoombaConfigEntry) -> ConnectionType:
    """Reads ConnectionType from config_entry.data.

    Defaults to LOCAL_PUSH for every entry that predates this field --
    which, until V4/Prime onboarding exists in config_flow.py, is every
    entry that currently exists. No migration needed: additive field
    with a backward-compatible default, not a breaking schema change."""
    raw = config_entry.data.get(CONF_CONNECTION_TYPE, ConnectionType.LOCAL_PUSH.value)
    return ConnectionType(raw)


@dataclasses.dataclass
class _SetupContext:
    """Mutable accumulator for async_setup_entry phase functions.

    Each _phase_* function populates its subset of fields and reads from fields
    that prior phases have already set.  Prefer explicit field assignment over
    long parameter lists.
    """

    hass: HomeAssistant
    config_entry: RoombaConfigEntry
    # ── Phase 1: connection ───────────────────────────────────────────────────
    roomba: Any = None
    state: dict[str, Any] = dataclasses.field(default_factory=dict)
    # ── Phase 2: spatial stores ───────────────────────────────────────────────
    map_capability: MapCapability = dataclasses.field(
        default_factory=lambda: MapCapability.NONE
    )
    renderer: MapRenderer | None = None
    geometry_store: GeometryStore | None = None
    grid_store: GridStore | None = None
    room_seg_store: RoomSegStore | None = None
    # ── Phase 3: data stores ──────────────────────────────────────────────────
    maintenance_store: MaintenanceStore | None = None
    mission_store: MissionStore | None = None
    mission_archive: MissionArchive | None = None
    last_error_code: int | None = None
    last_error_at: str | None = None
    last_error_zone: str | None = None
    blocking_manager: BlockingManager | None = None
    presence_manager: PresenceManager | None = None
    # ── Phase 4: cloud + dependent stores ────────────────────────────────────
    cloud_coordinator: IrobotCloudCoordinator | None = None
    umf_aligner: Any = None
    outline_store: OutlineStore | None = None
    trajectory_store: MissionTrajectoryStore | None = None
    freeze_snapshot_store: FreezeSnapshotStore | None = None
    dirt_threshold_manager: DirtThresholdManager | None = None
    robot_profile_store: RobotProfileStore | None = None
    mission_timer_store: MissionTimerStore | None = None


async def _phase_connect(ctx: _SetupContext) -> bool:
    """Phase 1 — Migrate options, create Roomba, connect, register stop listener.

    Returns False when connection fails without raising.
    Raises ConfigEntryNotReady on persistent connectivity issues.
    Sets ctx.roomba and ctx.state.
    """
    hass = ctx.hass
    config_entry = ctx.config_entry

    # Migrate options from data if this is a fresh entry
    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_CONTINUOUS: config_entry.data.get(CONF_CONTINUOUS, DEFAULT_CONTINUOUS),
                CONF_DELAY: config_entry.data.get(CONF_DELAY, DEFAULT_DELAY),
            },
        )

    # ── Data migration: backfill discovered_zone_ids ───────────────────────
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

    ctx.roomba = roomba
    ctx.state = roomba_reported_state(roomba)
    return True


async def _phase_spatial(ctx: _SetupContext) -> None:
    """Phase 2 — Detect map capability; load spatial stores.

    Populates: map_capability, renderer, geometry_store, grid_store, room_seg_store.
    """
    hass = ctx.hass
    config_entry = ctx.config_entry
    state = ctx.state

    map_capability = MapCapability.NONE
    renderer: MapRenderer | None = None
    geometry_store: GeometryStore | None = None

    map_enabled = config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)

    # v3.4.1 MAP-CAP-NO-POSE: previously gated on has_pose(state) alone,
    # which requires cap.pose >= 1. Field-confirmed (mdarocha, i3+,
    # "daredevil" firmware): a robot can have real persistent maps
    # (smart_map.pmap_ids populated, has_smart_map(state) True) while its
    # `cap` object has no "pose" key at all — has_pose(state) then
    # returns False via the dict.get(..., 0) default, and map_capability
    # stayed NONE regardless of pmaps. This silently skipped
    # has_smart_map entirely (never even checked), which in turn skipped
    # cloud_coordinator creation (gated on map_capability != NONE,
    # further down this function) even with valid cloud credentials
    # configured — no map, and total_cleaned_area fell back to the
    # known-unreliable bbrun.sqft (see that sensor's own docstring)
    # instead of the cloud-backed MissionArchive.cumulative_sqft it's
    # supposed to prefer, since nothing was feeding the archive either.
    # Fixed by entering this block on EITHER signal — has_smart_map is
    # checked first and takes priority when both are present, unchanged
    # from before; has_pose alone still yields EPHEMERAL exactly as
    # before for 900-series robots with no persistent maps.
    if (has_pose(state) or has_smart_map(state)) and map_enabled:
        if has_smart_map(state):
            map_capability = MapCapability.SMART
            _LOGGER.debug("Roomba+ map: SMART (persistent pmaps detected)")
        else:
            map_capability = MapCapability.EPHEMERAL
            _LOGGER.debug("Roomba+ map: EPHEMERAL (900-series pose, no pmaps)")

        if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            geometry_store = GeometryStore()
            await geometry_store.async_load(hass, config_entry.entry_id)

        if map_capability == MapCapability.EPHEMERAL:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_900_SERIES
        elif map_capability == MapCapability.SMART:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_ISJ_SERIES
        else:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_DEFAULT

        renderer = MapRenderer(
            RendererConfig(
                size_px=config_entry.options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                scale=config_entry.options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE),
                robot_diameter_mm=_robot_diameter_mm,
            ),
            geometry_store=geometry_store,
        )
    else:
        _LOGGER.debug(
            "Roomba+ map: NONE (cap.pose=%s, pmaps=%s, map_enabled=%s)",
            state.get("cap", {}).get("pose"), state.get("pmaps"), map_enabled,
        )

    # F9 — GridStore
    grid_store: GridStore | None = None
    if map_capability != MapCapability.NONE and map_enabled:
        grid_store = GridStore()
        await grid_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ GridStore: loaded %d cell(s) for %s",
            grid_store.cell_count, config_entry.data[CONF_BLID],
        )

    # ROOM-SEG — RoomSegStore (EPHEMERAL only)
    room_seg_store: RoomSegStore | None = None
    if map_capability == MapCapability.EPHEMERAL and map_enabled:
        room_seg_store = RoomSegStore()
        await room_seg_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ RoomSegStore: loaded %d room(s) for %s",
            len(room_seg_store.rooms), config_entry.data[CONF_BLID],
        )

        if not room_seg_store.migrated_from_zonestore:
            if grid_store is not None and grid_store.cell_count > 0 and not room_seg_store.rooms:
                room_seg_store.maybe_recompute(grid_store.cells)
            from .legacy_zone_migration import async_load_legacy_zones
            legacy_zones = await async_load_legacy_zones(hass, config_entry.entry_id)
            if legacy_zones:
                _n_migrated = room_seg_store.migrate_from_zone_store(legacy_zones)
                _LOGGER.debug(
                    "Roomba+ RoomSegStore: migrated %d room name(s) from "
                    "legacy ZoneStore data for %s",
                    _n_migrated, config_entry.data[CONF_BLID],
                )
            else:
                room_seg_store.migrated_from_zonestore = True
            await room_seg_store.async_save(hass, config_entry.entry_id)

        # ROOM-SEG Stage 5 — late-attach to renderer.
        #
        # `renderer` is Optional: a robot with no map has none, and then
        # there is nothing to attach the store to.
        if renderer is not None:
            renderer._room_seg_store = room_seg_store

    ctx.map_capability = map_capability
    ctx.renderer = renderer
    ctx.geometry_store = geometry_store
    ctx.grid_store = grid_store
    ctx.room_seg_store = room_seg_store


async def _phase_data(ctx: _SetupContext) -> None:
    """Phase 3 — Load maintenance/mission stores, restore L3 state, create managers.

    Populates: maintenance_store, mission_store, mission_archive,
               last_error_code/at/zone, blocking_manager, presence_manager.
    """
    hass = ctx.hass
    config_entry = ctx.config_entry

    maintenance_store = MaintenanceStore()
    await maintenance_store.async_load(hass, config_entry.entry_id)

    # F4d — detect bbrun.hr firmware reset
    _state_for_bbrun = roomba_reported_state(ctx.roomba)
    _bbrun = _state_for_bbrun.get("bbrun", {})
    _runtime = _state_for_bbrun.get("runtimeStats", {})
    _current_hr = _bbrun.get("hr") or _runtime.get("hr") or 0

    # v3.4.1 MAINTENANCE-COLD-START: field-confirmed (mdarocha, i3+, 412
    # missions / 294h prior runtime, no reset ever recorded in this
    # integration). filter_reset_hr/brush_reset_hr default to 0 for a
    # brand-new store — correct for a genuinely new robot (current_hr
    # also ≈0), but wrong for one with substantial pre-existing runtime:
    # hours_since_reset then comes out as the robot's ENTIRE prior
    # lifetime, immediately exceeding any sane threshold and clamping to
    # a confidently-displayed "0h remaining" — reading as "urgently
    # overdue" for maintenance that, for all this integration actually
    # knows, may have been done yesterday via the official app it has no
    # visibility into. On first-ever load with the robot already
    # reporting real hours, seed both baselines to "now" — assume
    # maintenance is current as of whenever this integration starts
    # watching, not as of hour zero.
    #
    # Gated on BOTH the dedicated *_baseline_seeded flag AND an empty
    # *_reset_history — the flag alone is not enough, since it defaults
    # to False for every install that predates v3.4.1 (the flag did not
    # exist before), which would otherwise overwrite an EXISTING user's
    # genuine, real reset_hr from a real past reset with today's
    # current_hr. reset_history is the authoritative "has a real reset
    # ever happened" signal; the flag exists only to stop this block
    # from re-seeding reset_hr to the latest current_hr on every single
    # restart once seeding has legitimately happened (auto-seeding
    # deliberately does not add a reset_history entry, to avoid
    # polluting the self-calibrating wear-rate learning with a
    # synthetic, non-user-confirmed event) — reset_history staying empty
    # forever after a pure auto-seed would otherwise look identical, on
    # every subsequent load, to "never seeded yet".
    _seeded_this_load = False
    if (_current_hr > 0 and not maintenance_store.filter_baseline_seeded
            and not maintenance_store.filter_reset_history):
        maintenance_store.filter_reset_hr = _current_hr
        maintenance_store.filter_baseline_seeded = True
        _seeded_this_load = True
        _LOGGER.debug(
            "Roomba+ MaintenanceStore: seeded filter_reset_hr=%dh on first "
            "load (no prior reset history, robot already has runtime)",
            _current_hr,
        )
    if (_current_hr > 0 and not maintenance_store.brush_baseline_seeded
            and not maintenance_store.brush_reset_history):
        maintenance_store.brush_reset_hr = _current_hr
        maintenance_store.brush_baseline_seeded = True
        _seeded_this_load = True
        _LOGGER.debug(
            "Roomba+ MaintenanceStore: seeded brush_reset_hr=%dh on first "
            "load (no prior reset history, robot already has runtime)",
            _current_hr,
        )
    if _seeded_this_load:
        await maintenance_store.async_save(hass, config_entry.entry_id)

    if _current_hr > 0:
        from .repairs import async_check_bbrun_reset
        await async_check_bbrun_reset(hass, config_entry, maintenance_store, _current_hr)

    # Mission store
    mission_store = MissionStore()
    await mission_store.async_load(hass, config_entry.entry_id)

    robot_name = config_entry.title or "Roomba"
    hass.async_create_task(
        mission_store.async_backfill_statistics(
            hass, config_entry.entry_id, robot_name
        ),
        name="roomba_plus_statistics_backfill",
    )

    # Restore L3 last-error state from mission history
    last_error_code: int | None = None
    last_error_at: str | None = None
    last_error_zone: str | None = None
    _ERROR_RESULTS = frozenset({
        "error", "stuck", "stuck_and_resumed", "stuck_and_abandoned"
    })
    for _rec in reversed(mission_store.records):
        if _rec.get("result") in _ERROR_RESULTS and _rec.get("error_code"):
            last_error_code = _rec["error_code"]
            last_error_at   = _rec.get("ended_at")
            last_error_zone = (_rec.get("zones") or [None])[0]
            break

    # MissionArchive (same cloud-credentials gate as coordinator)
    mission_archive: MissionArchive | None = None
    if (ctx.map_capability != MapCapability.NONE
            and config_entry.data.get(CONF_IROBOT_USERNAME)
            and config_entry.data.get(CONF_IROBOT_PASSWORD)):
        mission_archive = MissionArchive()
        await mission_archive.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "MissionArchive: loaded %d record(s) for %s",
            mission_archive.record_count, config_entry.data[CONF_BLID],
        )

    # BlockingManager
    blocking_manager: BlockingManager | None = None
    if config_entry.options.get(CONF_BLOCKING_SENSORS):
        blocking_manager = BlockingManager(hass, config_entry)
        _LOGGER.debug(
            "Roomba+ blocking manager active — sensors: %s",
            config_entry.options[CONF_BLOCKING_SENSORS],
        )

    # PresenceManager
    presence_manager: PresenceManager | None = None
    if config_entry.options.get(CONF_PRESENCE_SCHEDULING_ENABLED):
        presence_manager = PresenceManager(hass, config_entry)
        _LOGGER.debug("Roomba+ presence manager active")

    ctx.maintenance_store = maintenance_store
    ctx.mission_store = mission_store
    ctx.mission_archive = mission_archive
    ctx.last_error_code = last_error_code
    ctx.last_error_at = last_error_at
    ctx.last_error_zone = last_error_zone
    ctx.blocking_manager = blocking_manager
    ctx.presence_manager = presence_manager


async def _phase_cloud(ctx: _SetupContext) -> None:
    """Phase 4 — Create cloud coordinator; load all cloud-dependent stores.

    Populates: cloud_coordinator, umf_aligner, outline_store, trajectory_store,
               freeze_snapshot_store, dirt_threshold_manager, robot_profile_store,
               mission_timer_store.
    """
    hass = ctx.hass
    config_entry = ctx.config_entry
    map_capability = ctx.map_capability

    irobot_username = config_entry.data.get(CONF_IROBOT_USERNAME)
    irobot_password = config_entry.data.get(CONF_IROBOT_PASSWORD)

    cloud_coordinator: IrobotCloudCoordinator | None = None
    if map_capability != MapCapability.NONE and irobot_username and irobot_password:
        has_pmaps = map_capability == MapCapability.SMART
        cloud_coordinator = IrobotCloudCoordinator(
            hass=hass,
            config_entry=config_entry,
            blid=config_entry.data[CONF_BLID],
            username=irobot_username,
            password=irobot_password,
            has_pmaps=has_pmaps,
            mission_store=ctx.mission_store,
            mission_archive=ctx.mission_archive,
        )
        cloud_coordinator.seed_pmap_id_from_local(ctx.state)
        try:
            await cloud_coordinator.async_config_entry_first_refresh()
            _LOGGER.info(
                "Roomba+ cloud: coordinator active for %s (%d pmap(s), mode=%s)",
                config_entry.data[CONF_BLID],
                len(cloud_coordinator.data.get("pmaps", [])),
                map_capability.value,
            )
            if cloud_coordinator.raw_records and ctx.mission_store is not None:
                _bf = ctx.mission_store.backfill_from_cloud(
                    cloud_coordinator.raw_records
                )
                if _bf.corrected or _bf.enriched:
                    await ctx.mission_store.async_save(hass, config_entry.entry_id)

                if ctx.grid_store is not None:
                    centroids = cloud_coordinator.observed_zone_centroids
                    if centroids:
                        seeded = ctx.grid_store.seed_from_observed_zones(centroids)
                        if seeded:
                            await ctx.grid_store.async_save(hass, config_entry.entry_id)
                            _LOGGER.debug(
                                "Roomba+: seeded %d GridStore cell(s) from UMF "
                                "observed_zones for %s",
                                seeded, config_entry.data[CONF_BLID],
                            )
        except exceptions.ConfigEntryAuthFailed:
            _LOGGER.warning(
                "Roomba+ cloud: authentication failed for %s — "
                "check iRobot credentials in integration options",
                config_entry.data[CONF_BLID],
            )
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Roomba+ cloud: initial fetch failed for %s — "
                "local operation unaffected, cloud features unavailable until retry",
                config_entry.data[CONF_BLID],
            )

    # OutlineStore (EPHEMERAL + map enabled)
    outline_store: OutlineStore | None = None
    if (map_capability == MapCapability.EPHEMERAL
            and config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)):
        outline_store = OutlineStore()
        await outline_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ OutlineStore: loaded %d points for %s",
            outline_store.contour_point_count, config_entry.data[CONF_BLID],
        )

    # v3.2.1 — MissionTrajectoryStore (EPHEMERAL + map enabled, same gate
    # as OutlineStore): bounded last-N-missions raw pose history. Data-
    # collection scaffolding, see mission_trajectory_store.py docstring.
    trajectory_store: MissionTrajectoryStore | None = None
    if (map_capability == MapCapability.EPHEMERAL
            and config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)):
        trajectory_store = MissionTrajectoryStore()
        await trajectory_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ MissionTrajectoryStore: loaded %d mission(s) for %s",
            trajectory_store.mission_count, config_entry.data[CONF_BLID],
        )

    # v3.2.1 — FreezeSnapshotStore (EPHEMERAL + map enabled, same gate):
    # periodic immutable RoomSeg+Outline backup, insurance against the
    # firmware pose-cutoff risk. See freeze_snapshot_store.py docstring.
    freeze_snapshot_store: FreezeSnapshotStore | None = None
    if (map_capability == MapCapability.EPHEMERAL
            and config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)):
        freeze_snapshot_store = FreezeSnapshotStore()
        await freeze_snapshot_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ FreezeSnapshotStore: loaded snapshot from %s for %s",
            freeze_snapshot_store.snapshotted_at or "(none yet)",
            config_entry.data[CONF_BLID],
        )

    # DirtThresholdManager (SMART + cloud + demand enabled)
    dirt_threshold_manager: DirtThresholdManager | None = None
    if (map_capability == MapCapability.SMART
            and cloud_coordinator is not None
            and config_entry.options.get(CONF_DEMAND_CLEANING_ENABLED, False)):
        dirt_threshold_manager = DirtThresholdManager(hass, config_entry)
        await dirt_threshold_manager.async_load(config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ DirtThresholdManager: active for %s", config_entry.data[CONF_BLID]
        )

    # RobotProfileStore (all tiers)
    robot_profile_store = RobotProfileStore()
    await robot_profile_store.async_load(hass, config_entry.entry_id)
    _LOGGER.debug("RobotProfileStore: loaded for %s", config_entry.data[CONF_BLID])

    # L5-ARC/L3-ARC archive seeding
    if ctx.mission_archive is not None and ctx.mission_archive.initial_load_done:
        try:
            await _async_seed_l5_from_archive(
                hass, config_entry.entry_id, ctx.mission_archive, robot_profile_store
            )
            if ctx.mission_store is not None:
                await _async_seed_l3_from_archive(
                    ctx.mission_archive, ctx.mission_store
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "L5-ARC/L3-ARC: archive seeding failed — continuing setup "
                "without archive-based baselines (will retry next restart)",
                exc_info=True,
            )

    # MissionTimerStore (SMART + cloud only)
    mission_timer_store: MissionTimerStore | None = None
    if map_capability == MapCapability.SMART and cloud_coordinator is not None:
        mission_timer_store = MissionTimerStore()
        await mission_timer_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug("MissionTimerStore: loaded for %s", config_entry.data[CONF_BLID])

    # UMF spatial fusion aligner
    umf_aligner: Any = None
    if cloud_coordinator is not None and ctx.geometry_store is not None:
        _points2d    = cloud_coordinator.umf_data.get("points2d")
        _umf_regions = cloud_coordinator.umf_data.get("regions") or []
        _regions     = _umf_regions or cloud_coordinator.regions
        if not _points2d:
            _LOGGER.debug(
                "Roomba+ UmfAligner: skipped for %s — no points2d in UMF data "
                "(umf_data keys: %s). rooms_map will show fallback until UMF "
                "geometry is available from the cloud.",
                config_entry.data[CONF_BLID],
                list(cloud_coordinator.umf_data.keys()),
            )
        elif not _regions:
            _LOGGER.debug(
                "Roomba+ UmfAligner: skipped for %s — no regions from cloud coordinator.",
                config_entry.data[CONF_BLID],
            )
        else:
            from .umf_aligner import UmfAligner
            _aligner = UmfAligner(
                points2d=_points2d,
                regions=_regions,
                geometry_store=ctx.geometry_store,
                pmap_version_id=cloud_coordinator.umf_data.get("version_id", ""),
            )
            _conf = await hass.async_add_executor_job(_aligner.align)
            umf_aligner = _aligner
            _LOGGER.info(
                "Roomba+ UmfAligner: confidence=%.2f aligned=%s for %s",
                _conf, _aligner.aligned, config_entry.data[CONF_BLID],
            )

    ctx.cloud_coordinator = cloud_coordinator
    ctx.umf_aligner = umf_aligner
    ctx.outline_store = outline_store
    ctx.trajectory_store = trajectory_store
    ctx.freeze_snapshot_store = freeze_snapshot_store
    ctx.dirt_threshold_manager = dirt_threshold_manager
    ctx.robot_profile_store = robot_profile_store
    ctx.mission_timer_store = mission_timer_store


def _build_runtime_data(ctx: _SetupContext) -> RoombaData:
    """Assemble RoombaData from the fully populated _SetupContext."""
    return RoombaData(
        roomba=ctx.roomba,
        blid=ctx.config_entry.data[CONF_BLID],
        map_capability=ctx.map_capability,
        renderer=ctx.renderer,
        geometry_store=ctx.geometry_store,
        maintenance_store=ctx.maintenance_store,
        cloud_coordinator=ctx.cloud_coordinator,
        blocking_manager=ctx.blocking_manager,
        mission_store=ctx.mission_store,
        last_error_code=ctx.last_error_code,
        last_error_at=ctx.last_error_at,
        last_error_zone=ctx.last_error_zone,
        grid_store=ctx.grid_store,
        room_seg_store=ctx.room_seg_store,
        floor_label=ctx.config_entry.options.get(CONF_FLOOR, ""),
        umf_aligner=ctx.umf_aligner,
        dirt_threshold_manager=ctx.dirt_threshold_manager,
        outline_store=ctx.outline_store,
        trajectory_store=ctx.trajectory_store,
        freeze_snapshot_store=ctx.freeze_snapshot_store,
        robot_profile=get_robot_profile(
            ctx.state.get("sku"),
            battery_type=ctx.state.get("batteryType"),
        ),
        robot_profile_store=ctx.robot_profile_store,
        mission_timer_store=ctx.mission_timer_store,
        mission_archive=ctx.mission_archive,
        hass_ref=ctx.hass,
    )


async def _phase_finalize(ctx: _SetupContext) -> None:
    """Phase 5 — Background tasks, platform setup, REST views, services, MQTT callbacks.

    Called after config_entry.runtime_data is set so platform entities can
    access it during setup.
    """
    hass = ctx.hass
    config_entry = ctx.config_entry
    roomba = ctx.roomba
    cloud_coordinator = ctx.cloud_coordinator

    # v3.5.0 Repairs redesign bug-hunt fix — clean up any Repair Issues left
    # permanently stuck from a pre-v3.5.0 install (see
    # async_cleanup_removed_repairs's docstring for the full rationale).
    async def _cleanup_removed_repairs() -> None:
        from .repairs import async_cleanup_removed_repairs
        removed = await async_cleanup_removed_repairs(hass)
        if removed:
            _LOGGER.debug(
                "Roomba+: cleaned up %d stale Repair Issue(s) from a "
                "pre-v3.5.0 install for %s",
                removed, config_entry.entry_id,
            )
    hass.async_create_task(
        _cleanup_removed_repairs(),
        name=f"roomba_plus_cleanup_removed_repairs_{config_entry.entry_id}",
    )

    # ARC1 — one-time paginated back-fill as background task
    if (ctx.mission_archive is not None
            and cloud_coordinator is not None
            and not ctx.mission_archive.initial_load_done):
        hass.async_create_task(
            ctx.mission_archive.async_initial_load(
                cloud_coordinator.api,
                config_entry.data[CONF_BLID],
                hass,
                config_entry.entry_id,
            ),
            name=f"roomba_plus_arc1_initial_load_{config_entry.entry_id}",
        )

    # B9 — late SKU resolve (980 may not send sku in first MQTT dump)
    if config_entry.runtime_data.robot_profile is None:
        def _set_robot_profile_on_sku(json_data: dict[str, Any]) -> None:
            if config_entry.runtime_data.robot_profile is not None:
                return
            reported = json_data.get("state", {}).get("reported", {})
            sku = reported.get("sku")
            if sku:
                profile = get_robot_profile(sku, reported.get("batteryType"))
                if profile is not None:
                    config_entry.runtime_data.robot_profile = profile
                    _LOGGER.debug(
                        "RobotProfile resolved late for SKU %s → %s mAh %s",
                        sku, profile.battery_mah, profile.battery_chemistry,
                    )
        roomba.register_on_message_callback(_set_robot_profile_on_sku)

    if ctx.presence_manager is not None:
        config_entry.runtime_data.presence_manager = ctx.presence_manager
        ctx.presence_manager.start()

    # Platform setup
    platforms = list(LOCAL_PLATFORMS)
    if ctx.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        if Platform.IMAGE not in platforms:
            platforms.append(Platform.IMAGE)
    if ctx.map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)
    platforms.extend(p for p in _optional_platforms(config_entry) if p not in platforms)

    # v3.2.1 — MQTT-watchdog stamp callback MUST be registered before the
    # platforms: entities register their on_message callbacks during setup,
    # and roombapy calls callbacks in registration order.  Registering this
    # first guarantees last_mqtt_message_ts is fresh before RoombaMqttStale
    # (or any other entity) evaluates the message that ended a silence.
    from .callbacks import make_mqtt_stamp_callback
    roomba.register_on_message_callback(make_mqtt_stamp_callback(config_entry))

    await hass.config_entries.async_forward_entry_setups(config_entry, platforms)

    # REST API views (registered once per HA instance)
    if not hass.data.get("_roomba_plus_view_registered"):
        hass.http.register_view(MissionHistoryView())
        hass.http.register_view(HouseholdSummaryView())
        hass.http.register_view(MissionHistoryImportView())
        hass.http.register_view(DailyDigestView())
        hass.http.register_view(ExplainMissionView())
        hass.http.register_view(MissionPathView())
        # v3.3.0 MISSION-MAP
        hass.http.register_view(MissionMapJsonView())
        hass.http.register_view(MissionMapPngView())
        hass.data["_roomba_plus_view_registered"] = True

    async_register_services(hass)

    # F22a — check for cloud-detected obstacle zones
    if cloud_coordinator is not None and ctx.grid_store is not None:
        from .repairs import async_check_observed_zones
        hass.async_create_task(
            async_check_observed_zones(hass, config_entry),
            name=f"roomba_plus_observed_zones_check_{config_entry.entry_id}",
        )

    # MQTT callbacks
    if ctx.map_capability == MapCapability.SMART:
        roomba.register_on_message_callback(
            make_map_updating_callback(hass, config_entry)
        )

    if cloud_coordinator is not None:
        roomba.register_on_message_callback(
            make_map_retrain_callback(hass, cloud_coordinator, config_entry)
        )
        roomba.register_on_message_callback(
            make_mission_complete_callback(hass, cloud_coordinator, config_entry)
        )
        config_entry.async_on_unload(
            cloud_coordinator.async_add_listener(
                make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
            )
        )

    _mission_cb = make_mission_callback(hass, config_entry)
    roomba.register_on_message_callback(_mission_cb)

    config_entry.async_on_unload(
        async_track_time_interval(
            hass,
            _mission_cb.recheck_stuck_end_state,
            timedelta(seconds=30),
        )
    )

    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_change)
    )


def _optional_platforms(config_entry: RoombaConfigEntry) -> list[Platform]:
    """The platforms the user has asked for, calendar and to-do list.

    Renamed from `_calendar_platform_if_enabled` when the second one
    arrived: a name that promises one thing and returns two is how a
    caller ends up adding only half of them.
    """
    """Returns [Platform.CALENDAR] unless the user has opted out via
    CONF_ENABLE_SCHEDULE_CALENDAR (default True -- see that constant's
    own docstring in const.py for why an opt-OUT, not opt-in).

    Deliberately reads ONLY config_entry.options -- unambiguous at
    every one of the four call sites this is used from (Classic
    setup/unload, Prime setup/unload), unlike map_capability, which
    setup's own transient `ctx` vs config_entry.runtime_data may or
    may not already agree on at the exact point each platforms list is
    built. Called identically from all four sites on purpose: this
    project has hit the exact bug of "conditional platform logic
    duplicated in multiple places, one of them never updated" more
    than once before (see PRIME_PLATFORMS's own docstring, const.py,
    for two real examples -- CLOUD_ONLY entities and then Platform.IMAGE
    itself were each, separately, missing from one spot). One small
    function, called from everywhere platforms lists are built, makes
    that whole bug class structurally impossible here."""
    platforms: list[Platform] = []
    if config_entry.options.get(
        CONF_ENABLE_SCHEDULE_CALENDAR, DEFAULT_ENABLE_SCHEDULE_CALENDAR
    ):
        platforms.append(Platform.CALENDAR)
    # THE SAME GATE FOR THE MAINTENANCE LIST, and for the same reason a
    # todo list takes a place in Home Assistant's navigation. That is the
    # user's space; an integration should be asked into it.
    if config_entry.options.get(
        CONF_ENABLE_MAINTENANCE_LIST, DEFAULT_ENABLE_MAINTENANCE_LIST
    ):
        platforms.append(Platform.TODO)
    return platforms


def _remove_calendar_entity_if_disabled(hass: HomeAssistant, config_entry: RoombaConfigEntry) -> None:
    """Explicit entity-registry cleanup when CONF_ENABLE_SCHEDULE_CALENDAR
    is off (this session). Unloading Platform.CALENDAR (because it's no
    longer in the platforms list) only stops the entity from being live
    -- it does NOT remove the entity registry's own record, which would
    otherwise linger forever as "unavailable" (never re-created, since
    setup no longer forwards this platform for this entry). Idempotent
    and safe to call on every unload regardless of whether the option
    just changed or was already off -- a no-op if there's nothing to
    remove."""
    if config_entry.options.get(CONF_ENABLE_SCHEDULE_CALENDAR, DEFAULT_ENABLE_SCHEDULE_CALENDAR):
        return
    from homeassistant.helpers import entity_registry as er

    entity_reg = er.async_get(hass)
    for entry in er.async_entries_for_config_entry(entity_reg, config_entry.entry_id):
        if entry.domain == "calendar":
            entity_reg.async_remove(entry.entity_id)


async def async_setup_entry(hass: HomeAssistant, config_entry: RoombaConfigEntry) -> bool:
    """Set up Roomba+ from a config entry.

    SETUP-SPLIT Teil A (v3.0.0) — pure orchestrator: delegates all work to
    named phase functions that each populate a shared _SetupContext.

    NEW (V4/Prime): CLOUD_ONLY entries take an entirely separate path
    (_async_setup_entry_prime()), not the phase pipeline below -- see
    ROOMBA_PLUS_VERSION_PLAN_v4_onwards.md for why (the phase functions
    have deep assumptions about a real, disconnectable local roomba
    object at many points beyond just RoombaData).

    Phase summary (LOCAL_PUSH only):
        1. _phase_connect    — options migration, Roomba creation, MQTT connection
        2. _phase_spatial    — map capability detection, spatial stores
        3. _phase_data       — mission/maintenance stores, L3 state, managers
        4. _phase_cloud      — cloud coordinator, UMF aligner, cloud-dependent stores
        5. _build_runtime_data — assemble and assign RoombaData
        6. _phase_finalize   — background tasks, platforms, REST views, callbacks
    """
    if _connection_type(config_entry) == ConnectionType.CLOUD_ONLY:
        return await _async_setup_entry_prime(hass, config_entry)

    ctx = _SetupContext(hass=hass, config_entry=config_entry)

    if not await _phase_connect(ctx):
        return False

    await _phase_spatial(ctx)
    await _phase_data(ctx)
    await _phase_cloud(ctx)

    config_entry.runtime_data = _build_runtime_data(ctx)

    await _phase_finalize(ctx)

    _LOGGER.info(
        "Roomba+ connected to %s (blid=%s)",
        config_entry.data[CONF_HOST],
        config_entry.data[CONF_BLID],
    )
    return True


def _async_note_unknown_sku(sku: str | None, blid: str) -> None:
    """Asks for a report about a robot whose SKU we do not recognise --
    once setup has actually succeeded.

    DELIBERATELY HERE AND NOT IN THE CONFIG FLOW. At discovery time all
    that exists is an SKU string, and whether the robot is really Prime
    is a guess. By this point it is a fact: the account-based setup
    worked, which only happens for a Prime-generation robot.

    That changes what the report is worth. A diagnostics download now
    exists and contains the capability flags, the dock capability flags
    and the shadow structure -- which is what actually makes a new model
    interesting. Every capability gap this project has found so far came
    from exactly that data, and each one surfaced only because a tester
    pasted raw output nobody had thought to ask for.

    Asking at the earlier moment would also have asked more people: an
    unrecognised SKU at discovery is sometimes just a Classic robot the
    table missed. Asking here means only genuinely new Prime models
    generate a request.
    """
    from roombapy_prime.auth import sku_generation  # noqa: PLC0415

    if sku_generation(sku) == "prime":
        return

    _LOGGER.warning(
        "roomba_plus: robot %s set up successfully with SKU %r, which this version does "
        "not recognise as a known model. Everything should work -- but the SKU table is "
        "used to tell robot generations apart, so an unknown one is worth adding. "
        "If you are willing: %s -- please attach a diagnostics download "
        "(Settings > Devices & Services > Roomba+ > three dots > Download diagnostics), "
        "which carries the capability data that makes a new model useful. It contains no "
        "credentials; the integration redacts those.",
        blid, sku or "not reported", _unknown_sku_issue_url(sku),
    )


def _unknown_sku_issue_url(sku: str | None) -> str:
    """A prefilled GitHub issue for an unrecognised SKU.

    Prefilled because "please open an issue" is a request most people
    decline, and those who accept still have to work out what to
    include -- so the reports that do arrive usually omit the one field
    that matters.

    The SKU identifies a product model, not a person or a household, so
    nothing here needs redacting. The BLID is deliberately absent: it
    identifies one specific robot, contributes nothing to classifying a
    model, and belongs to the user rather than in a public issue.
    """
    from urllib.parse import quote  # noqa: PLC0415

    body = (
        f"Roomba+ set up a robot with SKU `{sku or 'not reported'}` successfully via the "
        "iRobot account flow, but does not recognise that SKU as a known model.\n\n"
        "**What model is this robot?** (the name on the box or in the iRobot app)\n\n"
        "**Diagnostics download attached?** Settings > Devices & Services > Roomba+ > "
        "the three dots next to the robot > Download diagnostics. This carries the "
        "capability flags and shadow structure, which is what makes a new model useful "
        "to support properly. It contains no credentials.\n\n"
        "---\n"
        "_Reported from Roomba+. An SKU identifies a product model, not a person._\n"
    )
    title = f"Unrecognised SKU: {sku or 'not reported'}"
    return (
        f"{ISSUE_TRACKER_URL}/new"
        f"?title={quote(title)}&body={quote(body)}&labels={quote('sku-table')}"
    )


async def _async_fetch_prime_time_estimates(config_entry: RoombaConfigEntry) -> None:
    """Fetches the robot's own per-room and whole-mission estimates.

    Two things read them. The schedule calendar gives every occurrence a
    flat hour because a schedule says when it starts and never how long
    it takes -- a tester watched a scheduled mission finish in minutes
    while the entity stayed "On" for the rest of the hour. And
    `mission_progress` computes a percentage from elapsed time against
    per-room estimates, which on Prime it has never had.

    ONCE AT SETUP. These are predictions from the robot's own cleaning
    history; they shift over weeks. A request per refresh would be a
    cloud call for something that barely moves.

    Failures are swallowed. Both callers already work without estimates,
    just less well, and a setup that failed over a prediction would be a
    poor trade.
    """
    from roombapy_prime.models import TimeEstimates  # noqa: PLC0415

    robot = config_entry.runtime_data.prime_robot if config_entry.runtime_data else None
    if robot is None:
        return
    try:
        raw = await robot.get_time_estimates()
        record_success("time estimates")
        config_entry.runtime_data.prime_time_estimates = TimeEstimates.from_json(raw)
    except Exception:  # noqa: BLE001
        record_failure("time estimates", "reading per-room estimates")
        _LOGGER.debug(
            "roomba_plus: no time estimates for this robot -- the calendar keeps "
            "its flat one-hour window and mission progress stays unavailable, "
            "which is where both were before", exc_info=True,
        )


async def _async_setup_entry_prime(hass: HomeAssistant, config_entry: RoombaConfigEntry) -> bool:
    """Entirely separate setup path for CLOUD_ONLY (V4/Prime) entries.

    v4.0.0a0 MVP scope: cloud login, MQTT connect, PrimeCoordinator
    running, vacuum entity (start/pause/stop/dock/locate -- see
    vacuum.py's CLOUD_ONLY branches). Forwards only PRIME_PLATFORMS
    (currently just Platform.VACUUM) -- a connectivity/error sensor is
    planned but not yet built (sensor.py has no CLOUD_ONLY awareness at
    all), see ROOMBA_PLUS_VERSION_PLAN_v4_onwards.md's
    Implementierungs-Checkliste.

    NEW (this session, prompted by a real "onboarding is slow" field
    report): the very first time this runs for a freshly-created entry,
    HA calls it essentially immediately after config_flow finishes --
    which already ran a full login to validate credentials and list
    robots. Checks _prime_login_bridge for a still-fresh, single-use
    LoginResult from that same login before doing its own; on every
    later restart (no config flow involved, or the bridge missed for
    any reason) this is simply None and login proceeds exactly as
    before. See _prime_login_bridge.py's own docstring for the full
    reasoning and deliberately narrow risk profile.
    """
    blid = config_entry.data[CONF_BLID]
    username = config_entry.data[CONF_IROBOT_USERNAME]
    password = config_entry.data[CONF_IROBOT_PASSWORD]
    country_code = (hass.config.country or "US").upper()
    session = async_get_clientsession(hass)

    # NEW (this session, prompted by a real "onboarding is slow" field
    # report): if config_flow just ran a validation login for this exact
    # blid moments ago, reuse it instead of running the full Gigya+
    # iRobot chain a second time. Single-use (removed on read whether or
    # not it turns out to still be fresh) and short-TTL -- see
    # _prime_login_bridge.py's own docstring for the full reasoning. On
    # every later HA restart (no config flow involved), this simply
    # returns None and the login below proceeds exactly as it always
    # did before this existed.
    cached_login_result = pop_pending_login(blid)

    try:
        prime_robot = await PrimeFactory.create_prime_robot(
            session, username, password, country_code,
            blid=blid, auto_refresh=True, login_result=cached_login_result,
        )
    except AuthCredentialsError as exc:
        raise exceptions.ConfigEntryAuthFailed(
            f"V4/Prime cloud login rejected for {blid}: {exc}"
        ) from exc
    except (
        AuthRateLimitedError,
        AuthSSLError,
        AuthConnectionError,
        AuthTimeoutError,
        AuthError,
    ) as exc:
        raise exceptions.ConfigEntryNotReady(
            f"Could not log in to V4/Prime cloud for {blid}: {exc}"
        ) from exc

    coordinator = PrimeCoordinator(hass, config_entry, blid, prime_robot)
    # async_start() itself raises ConfigEntryNotReady on a connect-level
    # failure (ShadowSSLError/ShadowConnectionError/ShadowError) -- login
    # already succeeded above, so only connection-level issues remain
    # possible here. See PrimeCoordinator.async_start()'s own docstring.
    await coordinator.async_start()

    # NEW: battery/dock/bin/tank/pad status, from the eight named
    # shadows (a SEPARATE coordinator from the mission-timeline one
    # above -- see PrimeStatusCoordinator's own docstring for why).
    # Reuses the same, already-connected prime_robot -- does not open
    # a second MQTT connection.
    status_coordinator = PrimeStatusCoordinator(hass, config_entry, blid, prime_robot)
    await status_coordinator.async_start()

    # Consumable parts. Best-effort on purpose: this is enrichment, and
    # a robot whose parts endpoint is unreachable should still finish
    # setting up with everything else working. Same reasoning as the
    # household lookup below.
    schedule_coordinator = PrimeScheduleCoordinator(
        hass, prime_robot, blid, config_entry
    )

    parts_coordinator = PrimePartsCoordinator(hass, prime_robot, blid, config_entry)
    try:
        await parts_coordinator.async_config_entry_first_refresh()
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "roomba_plus: could not fetch consumable parts for %s -- continuing without "
            "them; the sensors will appear once a later refresh succeeds", blid,
        )

    # NEW: household_id, needed for get_schedules()/PrimeScheduleCalendar
    # (calendar.py). Best-effort deliberately -- get_household_id()'s own
    # response-shape handling is defensive but not yet confirmed against
    # every real account shape, and a schedule calendar showing "no data
    # yet" is a far better failure mode here than blocking the entire
    # V4/Prime setup (battery/vacuum/etc., all already working) over a
    # single optional feature.
    try:
        household_id = await prime_robot.get_household_id()
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "roomba_plus: could not resolve household_id for %s -- schedule "
            "calendar will show no data until this succeeds", blid, exc_info=True,
        )
        household_id = None

    # NEW: model/serial info for a correct DeviceInfo -- see
    # IRobotEntity.__init__'s own docstring for the bug this fixes (every
    # Prime entity's device page previously showed no model/serial/firmware
    # at all). Best-effort, same reasoning as household_id above -- a device
    # page missing model/serial is a far better failure mode than blocking
    # the entire V4/Prime setup over it.
    try:
        serial_info = await prime_robot.get_serial_number_data()
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "roomba_plus: could not resolve serial/model info for %s -- device "
            "page will show no model/serial until this succeeds", blid, exc_info=True,
        )
        serial_info = None

    _async_note_unknown_sku(getattr(serial_info, "sku", None), blid)

    # NEW (this session): BlockingManager was never instantiated for
    # Prime entries at all -- roomba_plus.smart_start's blocking-sensor
    # gate simply didn't exist for Prime, on top of the separate crash
    # this session also fixed (services.py's own data.roomba.start
    # fallback when no blocking sensors are configured). Same
    # conditional-creation rule as Classic: only when the user has
    # actually configured CONF_BLOCKING_SENSORS.
    blocking_manager: BlockingManager | None = None
    if config_entry.options.get(CONF_BLOCKING_SENSORS):
        blocking_manager = BlockingManager(hass, config_entry)
        _LOGGER.debug(
            "Roomba+ (Prime) blocking manager active — sensors: %s",
            config_entry.options[CONF_BLOCKING_SENSORS],
        )

    # MISSION STORE FOR PRIME (this session).
    #
    # Every store was left at None since v4.0.0a0, so around 30 sensor
    # lookups that read MissionStore -- mission statistics, dirt-spike
    # and excessive-recharge detection, rolling means, cleaning
    # intervals -- have been empty for Prime robots while the underlying
    # data sat available over REST the whole time.
    #
    # Only this one store is created. grid_store, outline_store,
    # trajectory_store and geometry_store all derive from pose data,
    # and how many pose samples a Prime robot actually delivers is still
    # unmeasured -- creating them would be building on an unknown.
    # MAINTENANCE STORE FOR PRIME (this session).
    #
    # Unlike every other store, this one holds NOTHING the robot
    # reports: it records when the USER last changed a filter, brush,
    # pad or battery. So it is generation-independent by nature, and the
    # reset service handler was already written without any Classic
    # assumption -- it only checked whether the store existed, and for
    # Prime it never did.
    prime_maintenance_store = MaintenanceStore()
    try:
        await prime_maintenance_store.async_load(hass, config_entry.entry_id)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Roomba+ Prime: could not load maintenance store; maintenance dates "
            "will start empty",
            exc_info=True,
        )

    # MISSION TIMER FOR PRIME (this session).
    #
    # Tracks a RUNNING mission -- elapsed time, time in the current
    # room, remaining estimate -- and everything it needs Prime reports:
    # a mission id, phase transitions, and get_time_estimates() for the
    # planned durations.
    #
    # This is the last store worth wiring. The remaining five
    # (geometry, grid, room_seg, outline, trajectory) all derive from
    # pose data, and freeze_snapshot_store exists solely to back THOSE
    # up against a firmware change that stops pose delivery -- so for a
    # robot that never delivered poses it has nothing to protect.
    # robot_profile_store is left out too: its useful half needs
    # per-room dirt and zone data Prime has no equivalent for.
    # ROBOT PROFILE STORE FOR PRIME (this session).
    #
    # Reversal of an earlier decision in the same session, worth
    # recording rather than quietly changing. It was skipped because half
    # of what it does -- coverage baselines and per-room dirt indices --
    # needs zone data Prime has no equivalent for.
    #
    # But the OTHER half, update_mission_stats(), works on plain mission
    # records: rolling means and standard deviations over a 30-day
    # window. Those records now exist for Prime, and the statistics were
    # verified to compute from them.
    #
    # It surfaced from a bug hunt: the sync path was already calling an
    # update function for this store, which returned immediately because
    # the store was None. Dead code that read as working functionality --
    # anyone reading its docstring would have believed profile
    # statistics were live.
    # SAVED FAVOURITES, read once at setup.
    #
    # They change only when somebody creates one in the iRobot app, so
    # re-reading on every coordinator update would be a cloud call per
    # battery percent. Exposed as a vacuum attribute and used by the
    # run_favorite service; the buttons read the same list.
    prime_favorites: list[dict[str, Any]] = []

    prime_profile_store = RobotProfileStore()
    try:
        await prime_profile_store.async_load(hass, config_entry.entry_id)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Roomba+ Prime: could not load robot profile store; mission "
            "statistics will start empty",
            exc_info=True,
        )

    prime_timer_store = MissionTimerStore()
    try:
        await prime_timer_store.async_load(hass, config_entry.entry_id)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Roomba+ Prime: could not load mission timer store; progress will "
            "start empty",
            exc_info=True,
        )

    prime_mission_store = MissionStore()
    try:
        await prime_mission_store.async_load(hass, config_entry.entry_id)
        # HA LONG-TERM STATISTICS, same as the Classic path does.
        #
        # Injects three external statistic series -- daily cleaned area,
        # daily mission duration, completion count -- so a statistics
        # graph card shows history rather than starting from today.
        #
        # Applies to Prime unmodified: the store's own docstring notes
        # that duration and completion count work for all robots, and
        # the area series needs `area_sqft`, which the Prime translation
        # maps from square_feet_covered.
        #
        # Fire-and-forget on purpose: it walks the whole history, and
        # setup must not wait for it. async_add_external_statistics is
        # idempotent, so a repeat on every restart is harmless.
        hass.async_create_task(
            prime_mission_store.async_backfill_statistics(
                hass, config_entry.entry_id, config_entry.title or "Roomba"
            ),
            name="roomba_plus_prime_statistics_backfill",
        )
    except Exception:  # noqa: BLE001
        # Mission history is enrichment, not a dependency. A corrupt or
        # unreadable store must not stop the robot from being set up --
        # an empty store degrades some sensors, a raised exception costs
        # the user their whole integration.
        _LOGGER.warning(
            "Roomba+ Prime: could not load mission history store; statistics "
            "sensors will start empty",
            exc_info=True,
        )

    config_entry.runtime_data = RoombaData(
        blid=blid,
        roomba=None,
        connection_type=ConnectionType.CLOUD_ONLY,
        prime_robot=prime_robot,
        prime_coordinator=coordinator,
        mission_store=prime_mission_store,
        maintenance_store=prime_maintenance_store,
        mission_timer_store=prime_timer_store,
        robot_profile_store=prime_profile_store,
        prime_favorites=prime_favorites,
        prime_status_coordinator=status_coordinator,
        prime_parts_coordinator=parts_coordinator,
        prime_schedule_coordinator=schedule_coordinator,
        prime_household_id=household_id,
        prime_serial_info=serial_info,
        blocking_manager=blocking_manager,
        hass_ref=hass,
    )

    async def _async_disconnect_on_stop(event: Any) -> None:
        await prime_robot.disconnect()

    config_entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_disconnect_on_stop)
    )

    # NEW (this session): Prime entries had NO options-change listener at
    # all -- changing ANY option (including CONF_ENABLE_SCHEDULE_CALENDAR)
    # silently did nothing until a manual reload. Classic has had this
    # since v2.x (see _async_reload_on_options_change() itself); Prime's
    # own setup path was simply never given the equivalent call.
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_change)
    )

    # FAVOURITES, read once now that runtime_data exists.
    #
    # Before the platforms load, because the button platform reads the
    # same list -- and after runtime_data is set, because the read needs
    # the robot object from it.
    try:
        from .button_prime import async_favorites_attribute  # noqa: PLC0415

        prime_favorites.extend(await async_favorites_attribute(config_entry))

        # AFTER the call, so a failure is not recorded as a success.
        record_success("favourite list (setup)")
    except Exception:  # noqa: BLE001
        record_failure("favourite list (setup)", "reading favourites at setup")
        _LOGGER.debug("Roomba+ Prime: could not read favorites", exc_info=True)

    # NOTHING CALLED THIS, AND TWO THINGS READ WHAT IT WRITES.
    #
    # `_async_fetch_prime_time_estimates` fills
    # `runtime_data.prime_time_estimates`; the calendar reads it to give
    # occurrences a real duration, and `mission_progress` needs it for a
    # denominator. Both got None for the whole life of the feature.
    #
    # @utkjmitch traced `mission_progress` reading `unknown` on a robot
    # with 50 imported missions and found the profile-store half of it
    # (`hass_ref`). This is the other half: the estimates the fallback
    # sits in front of were never fetched at all.
    #
    # Found by checking which private functions appear exactly once in
    # the source -- the same shape as `hass_ref` (read twice, written
    # never) and `_readable_part_name` (written, never called).
    await _async_fetch_prime_time_estimates(config_entry)

    # PRESENCE SCHEDULING FOR PRIME TOO.
    #
    # Six options -- the enable switch, the mode, the person entities and
    # two delays -- were offered in the dialog, stored, and read by
    # nobody on this path: `async_setup_entry` branches for CLOUD_ONLY
    # and the manager was built in the Classic function.
    #
    # A user could switch presence scheduling on, pick their household
    # and set two delays, and nothing would happen. No error, no message.
    # **They did not merely not get the feature; they believed they had
    # it**, and an automation built on it was silently inert.
    #
    # The manager itself is generation-aware now: Classic holds
    # `schedHold`, Prime disables the schedules, because Prime accepts a
    # write to `schedHold` and runs anyway (APK research 10: the field
    # has no consumer in the Prime app at all).
    if config_entry.options.get(CONF_PRESENCE_SCHEDULING_ENABLED):
        config_entry.runtime_data.presence_manager = PresenceManager(
            hass, config_entry
        )
        _LOGGER.debug("Roomba+ Prime: presence manager active")

    from .const import PRIME_PLATFORMS
    platforms = list(PRIME_PLATFORMS)
    platforms.extend(p for p in _optional_platforms(config_entry) if p not in platforms)
    await hass.config_entries.async_forward_entry_setups(config_entry, platforms)

    # THE FIRST MISSION-HISTORY SYNC BELONGS HERE, not on the parts
    # coordinator's first refresh.
    #
    # That refresh is awaited BEFORE runtime_data is assigned above, so
    # its sync attempt raised AttributeError every single time and was
    # filed at DEBUG by a best-effort except. The next attempt was that
    # coordinator's own interval -- SIX HOURS -- which is why clean
    # streak, last mission, last duration and area cleaned today read
    # "unknown" after every restart, and why they were reported as
    # permanently unknown: nobody watches a sensor for six hours
    # (@utkjmitch, who found it in a debug log and named the ordering).
    #
    # A task rather than an await: the history is a cloud round trip,
    # and a robot with no history is a perfectly normal answer. Neither
    # is worth delaying setup for.
    async def _first_mission_sync() -> None:
        from .prime_mission_sync import async_sync_prime_missions  # noqa: PLC0415

        try:
            count = await async_sync_prime_missions(config_entry)
            record_success("first mission sync")
            _LOGGER.debug(
                "roomba_plus: first mission history sync imported %s missions",
                count,
            )
        except Exception:  # noqa: BLE001
            record_failure("first mission sync", "the post-setup import")
            _LOGGER.debug(
                "roomba_plus: first mission history sync failed -- the "
                "mission-based sensors stay empty until the next scheduled "
                "sync, which is where they were before this ran",
                exc_info=True,
            )

    hass.async_create_task(
        _first_mission_sync(), name="roomba_plus_prime_first_mission_sync"
    )

    # DOMAIN ACTIONS ARE SHARED BY BOTH GENERATIONS, and Prime never
    # reached the line that registers them (PR #76, @jouwdan).
    #
    # The Classic path calls this in its final phase; the Prime path
    # returns 500 lines earlier and skipped it entirely. Every
    # `roomba_plus.*` service was therefore missing on a Prime-only
    # installation -- the services existed, the entities existed, and
    # nothing connected them.
    #
    # Safe to call twice: each service is guarded by `has_service()`, so
    # a household with one Classic and one Prime robot registers them
    # once.
    async_register_services(hass)

    _LOGGER.info(
        "Roomba+ (V4/Prime) connected to cloud for %s (blid=%s)", username, blid
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Unload a config entry and disconnect from the Roomba.

    NEW (V4/Prime): CLOUD_ONLY entries take a short, separate path --
    no local platforms list gating on map_capability, no mission-timer/
    presence-manager cleanup (neither exists for a CLOUD_ONLY entry) --
    just unloading PRIME_PLATFORMS and disconnecting the PrimeRobot.

    CORRECTED (this session): blocking_manager cleanup was missing from
    this list entirely -- BlockingManager itself is now instantiated
    for Prime too (see _async_setup_entry_prime()), so its queue must
    be cancelled here the same way Classic's own unload path already
    does, or a pending queued start (waiting for blocking sensors to
    clear) would keep running against a config entry that's mid-unload.
    """
    if config_entry.runtime_data.connection_type == ConnectionType.CLOUD_ONLY:
        from .const import PRIME_PLATFORMS
        platforms = list(PRIME_PLATFORMS)
        platforms.extend(p for p in _optional_platforms(config_entry) if p not in platforms)
        unload_ok = await hass.config_entries.async_unload_platforms(
            config_entry, platforms
        )
        if unload_ok:
            _remove_calendar_entity_if_disabled(hass, config_entry)
            bm = config_entry.runtime_data.blocking_manager
            if bm is not None:
                bm.cancel_queue()
            prime_robot = config_entry.runtime_data.prime_robot
            if prime_robot is not None:
                await prime_robot.disconnect()

        # FLUSH THE DEBOUNCED TIMER WRITE, same as the Classic path does.
        #
        # MissionTimerStore saves via async_delay_save, and Store's own
        # async_save cancels any pending delayed write. Without this a
        # RELOAD can have the OLD instance's delayed write land after the
        # NEW instance already loaded -- overwriting fresh state with
        # stale, which is worse than losing the update.
        #
        # Classic has done this since v3.3.0, found in its own bug hunt.
        # The Prime path was written later and did not inherit it: the
        # short CLOUD_ONLY branch returns before that code is reached.
        mts = config_entry.runtime_data.mission_timer_store
        if mts is not None:
            try:
                await mts.async_save(hass, config_entry.entry_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    # NOT INSTRUMENTED: runs once at teardown, where a
                    # failure has nowhere to be seen and no next attempt
                    # to compare against.
                    "Roomba+ Prime: mission timer flush on unload failed",
                    exc_info=True,
                )

        # SERVICES ARE REMOVED WITH THE LAST ENTRY, whichever generation
        # it is.
        #
        # The Classic path has done this since services existed; the
        # Prime branch returns before reaching it. So somebody whose only
        # robot was a Prime removed the integration and kept eighteen
        # registered actions pointing at nothing.
        #
        # Found by the generation-parity check on its first run, which is
        # what that check is for.
        if not hass.config_entries.async_entries(DOMAIN):
            async_remove_services(hass)
        return unload_ok

    data = config_entry.runtime_data
    platforms = list(LOCAL_PLATFORMS)
    if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        if Platform.IMAGE not in platforms:
            platforms.append(Platform.IMAGE)
    if data.map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)
    platforms.extend(p for p in _optional_platforms(config_entry) if p not in platforms)

    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, platforms
    )
    if unload_ok:
        _remove_calendar_entity_if_disabled(hass, config_entry)
        bm = config_entry.runtime_data.blocking_manager
        if bm is not None:
            bm.cancel_queue()

        pm = config_entry.runtime_data.presence_manager
        if pm is not None:
            pm.cancel()

        # v3.3.0 DELAY-SAVE (bug-hunt round 2) — flush the debounced
        # MissionTimerStore write on unload: Store.async_save also
        # cancels a pending async_delay_save timer, so a reload can no
        # longer have the OLD instance's stale delayed write land after
        # the NEW instance already loaded.
        mts = config_entry.runtime_data.mission_timer_store
        if mts is not None:
            try:
                await mts.async_save(hass, config_entry.entry_id)
            except Exception:  # noqa: BLE001 — unload must never fail on this
                _LOGGER.debug("MTS unload flush failed", exc_info=True)

        # `roomba` IS SET FROM HERE DOWN. The CLOUD_ONLY branch above
        # returns before reaching this code, so a Prime entry -- which
        # has no local robot -- never gets here. mypy cannot follow the
        # early return across a hundred lines.
        _roomba = config_entry.runtime_data.roomba
        if _roomba is not None:
            await async_disconnect_or_timeout(hass, roomba=_roomba)

        if not hass.config_entries.async_entries(DOMAIN):
            async_remove_services(hass)

    return unload_ok


async def _async_reload_on_options_change(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> None:
    """Reload when connection-relevant options change, OR when an
    option that affects the platforms list itself changes.

    Compares current data against current options for the tracked keys.
    When they differ, syncs data first so subsequent option changes do NOT
    re-trigger a reload (prevents false reconnect/reload on every options
    edit after the first tracked change).

    CONF_ENABLE_SCHEDULE_CALENDAR (this session) is tracked here for a
    DIFFERENT reason than CONF_CONTINUOUS/CONF_DELAY: it doesn't affect
    the actual Roomba/cloud connection, only which platforms
    _optional_platforms() returns -- but that list is only
    (re-)read at setup/unload time, so without a reload here, saving
    the option would silently do nothing until the user manually
    reloaded the integration. The data/options sync mechanism below is
    generic (just "did a tracked value change since the last reload"),
    so reusing it needs no new machinery -- only a bigger tracked-key
    set. Renamed from _CONNECTION_KEYS to _RELOAD_TRIGGER_KEYS to
    reflect that this set is no longer only about the connection."""
    _RELOAD_TRIGGER_KEYS = {CONF_CONTINUOUS, CONF_DELAY, CONF_ENABLE_SCHEDULE_CALENDAR}

    def _get(source: Mapping[str, Any], key: str) -> Any:
        # CONF_ENABLE_SCHEDULE_CALENDAR needs its default applied on BOTH
        # sides of the comparison -- unlike CONF_CONTINUOUS/CONF_DELAY
        # (always seeded into config_entry.data at initial entry
        # creation), this option has no such seeding, so an existing
        # entry's .data genuinely has no key for it at all yet. Reading
        # None from .data but the real default from .options would make
        # EVERY existing installation's first-ever options save (even an
        # unrelated one) look like a change and trigger a spurious extra
        # reload -- harmless, but avoidable.
        if key == CONF_ENABLE_SCHEDULE_CALENDAR:
            return source.get(key, DEFAULT_ENABLE_SCHEDULE_CALENDAR)
        return source.get(key)

    old_vals = {k: _get(config_entry.data, k) for k in _RELOAD_TRIGGER_KEYS}
    new_vals = {k: _get(config_entry.options, k) for k in _RELOAD_TRIGGER_KEYS}
    if old_vals != new_vals:
        # Sync data to match new options so the next options change starts from
        # a clean baseline and does not re-trigger an unintended reload.
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, **new_vals},
        )
        await hass.config_entries.async_reload(config_entry.entry_id)


# ── Connection helpers ────────────────────────────────────────────────────────

async def async_connect_or_timeout(
    hass: HomeAssistant, roomba: Roomba
) -> dict[str, Any]:
    """Connect to the vacuum and wait for first state report."""
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
            await asyncio.sleep(2)
            cap = roomba_reported_state(roomba).get("cap", {})
            if cap.get("pmaps", 0) > 0 or cap.get("maps", 0) > 1:
                for _ in range(6):
                    if roomba_reported_state(roomba).get("pmaps"):
                        break
                    await asyncio.sleep(1)
    except RoombaConnectionError as err:
        _LOGGER.debug("Connection error: %s", err)
        raise CannotConnect from err
    except TimeoutError as err:
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

def roomba_reported_state(roomba: Roomba | None) -> dict[str, Any]:
    """Return the 'reported' sub-dict from master_state.

    Uses ``or {}`` rather than a dict default so that an explicit JSON null
    (``{"state": null}``) — which a sparse or initial MQTT frame can produce —
    is coerced to an empty dict instead of raising AttributeError. A dict
    default (``.get("state", {})``) only guards against a *missing* key, not a
    present-but-null value.

    NEW (V4/Prime): roomba is None for CLOUD_ONLY entries -- returns {}
    rather than crashing, same reasoning as RoombaData.roomba_reported_state()
    (models.py): honest "no data available", not a fabricated guess. Every
    one of this function's 59+ call sites across the integration goes
    through here, so this single guard is what keeps all of them safe for
    a Prime entity without touching each call site individually.
    """
    if roomba is None:
        return {}
    return (roomba.master_state.get("state") or {}).get("reported") or {}


# ── Exceptions ────────────────────────────────────────────────────────────────

class CannotConnect(exceptions.HomeAssistantError):
    """Raised when a connection to the Roomba cannot be established."""


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, device_entry: Any
) -> bool:
    """Return whether a stale device can be removed from the device registry.

    THE NAME AND THE SIGNATURE WERE BOTH WRONG, and the symptom was the
    familiar one: nothing failed, the hook simply never ran. Home
    Assistant looks for `async_remove_config_entry_device` -- singular --
    and passes ONE device entry, not a list. Ours was plural and took a
    list, so Home Assistant found no hook at all and refused every
    removal with "Failed to remove device entry, rejected by
    integration".

    Anyone who replaced a robot and tried to delete the old device from
    the UI hit a refusal with no explanation, and nothing in our logs.

    Called by HA when the user requests removal of a device that is no longer
    associated with any entity in this config entry. For Roomba+, each config
    entry manages exactly one physical robot — there are no child devices or
    dynamically-discovered sub-devices, so any device presented for removal
    is safe to remove.
    """
    return True


# v3.4.0 bug-hunt finding (README/docs review) — Roomba+ persists 15 distinct
# hass.storage files across its stores (mission history, coverage grid,
# maintenance timers, robot profile, room segmentation, map render state,
# etc. — see each module's own STORAGE_KEY_PREFIX). Before this addition,
# only async_unload_entry existed: it tears down runtime state but a config
# entry's unload happens on every reload too, so it was never the right place
# to touch persisted files anyway. There was no async_remove_entry at all —
# HA's ONLY hook that fires specifically on permanent deletion (never on a
# reload) — meaning every one of these 15 files silently outlived the config
# entry that created them, contradicting the README/TROUBLESHOOTING claim
# that deletion "removes the config entry and all associated entities
# cleanly." This makes that claim actually true.
#
# (label, key_template) — key_template gets .format(entry_id=...). Version is
# always 1 for every store in this integration (confirmed against source);
# Store.async_remove() doesn't use the version for removal anyway, and
# already suppresses FileNotFoundError internally for a file that was never
# created (e.g. a 600-series robot has no GridStore data to begin with).
_STORAGE_KEYS_TO_REMOVE: Final[list[tuple[str, str]]] = [
    ("dirt_threshold_manager", "roomba_plus_dirt_threshold_{entry_id}"),
    ("freeze_snapshot_store", "roomba_plus_freeze_{entry_id}"),
    ("geometry_store", "roomba_plus_geometry_{entry_id}"),
    ("grid_store", "roomba_plus_grid_{entry_id}"),
    # legacy pre-migration zone data (LEGACY-ZONE-MIGRATION) — may not exist
    # on any install that was never on the old ZoneStore in the first place.
    ("legacy_zone_migration", "roomba_plus_zones_{entry_id}"),
    ("maintenance_store", "roomba_plus_maintenance_{entry_id}"),
    ("mission_archive", "roomba_plus_mission_archive_{entry_id}"),
    ("mission_store", "roomba_plus_missions_{entry_id}"),
    ("mission_timer_store", "roomba_plus_mission_timer_{entry_id}"),
    ("mission_trajectory_store", "roomba_plus_trajectories_{entry_id}"),
    ("outline_store", "roomba_plus_outline_{entry_id}"),
    ("robot_profile_store", "roomba_plus_robot_profile_{entry_id}"),
    ("room_seg_store", "roomba_plus_roomseg_{entry_id}"),
    ("image (map render state)", "roomba_plus_map_{entry_id}"),
    ("image (mission checkpoint)", "roomba_plus_map_checkpoint_{entry_id}"),
    # Prime map PNG, added this session with the map persistence.
    # Found by a bug hunt rather than a test: a new Store was created
    # without a matching entry here, so uninstalling would have left a
    # file behind containing a picture of someone's home.
    ("image (prime map)", "roomba_plus_prime_map_{entry_id}"),
]


async def async_remove_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> None:
    """Delete every persisted hass.storage file for this config entry.

    Runs after async_unload_entry, only on permanent deletion (HA never
    calls this on a reload). Each removal is independently guarded — one
    file failing to delete (permissions, unexpected I/O error; a missing
    file is already a safe no-op inside Store.async_remove() itself) must
    never prevent the remaining ones from being attempted.
    """
    from homeassistant.helpers.storage import Store

    entry_id = config_entry.entry_id
    removed = 0
    for label, key_template in _STORAGE_KEYS_TO_REMOVE:
        key = key_template.format(entry_id=entry_id)
        try:
            await Store(hass, 1, key).async_remove()
            removed += 1
        except Exception:  # noqa: BLE001 — one failure must not block the rest
            _LOGGER.warning(
                "Roomba+ removal: failed to delete storage for %s (key=%s)",
                label, key, exc_info=True,
            )

    _LOGGER.info(
        "Roomba+ removal: cleaned up %d/%d storage file(s) for entry %s",
        removed, len(_STORAGE_KEYS_TO_REMOVE), entry_id,
    )
