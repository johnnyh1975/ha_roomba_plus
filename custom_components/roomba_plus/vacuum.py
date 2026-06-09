"""Vacuum platform for Roomba+.

Implements the full iRobot vacuum hierarchy:
  IRobotVacuum          — base with all standard commands
  RoombaVacuum          — adds bin state attributes
  RoombaVacuumCarpetBoost — adds carpet boost / fan speed control
  BraavaJet             — adds mop behaviour and spray amount
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
# Segment is imported locally in async_get_segments — requires HA 2026.3.
# hacs.json pins homeassistant >= 2026.3.0 so this is always safe in production.
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import METRIC_SYSTEM

from . import roomba_reported_state
from .const import (
    ATTR_BIN_FULL,
    has_carpet_boost,
    is_mop,
    ATTR_BIN_PRESENT,
    ATTR_CLEANED_AREA,
    ATTR_CLEANING_TIME,
    ATTR_DETECTED_PAD,
    ATTR_ERROR,
    ATTR_ERROR_CODE,
    ATTR_LID_CLOSED,
    ATTR_PAD_WETNESS,
    ATTR_POSITION,
    ATTR_SOFTWARE_VERSION,
    ATTR_TANK_LEVEL,
    ATTR_TANK_PRESENT,
    BRAAVA_MOP_BEHAVIORS,
    BRAAVA_SPRAY_AMOUNT,
    CLEANING_PHASES,           # v2.3.0 Step 11 — moved from image.py
    FAN_SPEED_AUTOMATIC,
    FAN_SPEED_ECO,
    FAN_SPEED_PERFORMANCE,
    FAN_SPEEDS,
    MOP_DEEP,
    MOP_EXTENDED,
    MOP_STANDARD,
    OVERLAP_DEEP,
    OVERLAP_EXTENDED,
    OVERLAP_STANDARD,
    CONF_FLOOR,
    DOMAIN,
    PHASE_TO_ACTIVITY,
)
from .entity import IRobotEntity
from .models import MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

SUPPORT_IROBOT = (
    VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.SEND_COMMAND
    | VacuumEntityFeature.START
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.LOCATE
)

SUPPORT_ROOMBA_CARPET_BOOST = SUPPORT_IROBOT | VacuumEntityFeature.FAN_SPEED
SUPPORT_BRAAVA = SUPPORT_IROBOT | VacuumEntityFeature.FAN_SPEED


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacuum entity, choosing the right class for the device."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)
    capabilities = state.get("cap", {})

    # Determine device class using capability helpers.
    # is_mop() detects Braava by presence of 'detectedPad' in state.
    # has_carpet_boost() handles both 900-series (top-level key, absent from cap{})
    # and i/s/j-series (cap.carpetBoost == 1) correctly.
    constructor: type[IRobotVacuum]
    if is_mop(state):
        constructor = BraavaJet
    elif has_carpet_boost(state):
        constructor = RoombaVacuumCarpetBoost
    else:
        constructor = RoombaVacuum

    async_add_entities([constructor(roomba, blid, config_entry)])


class IRobotVacuum(IRobotEntity, StateVacuumEntity):
    """Base vacuum entity for all iRobot robots in Roomba+.

    Handles:
    - Activity state mapping from cleanMissionStatus phase/cycle
    - Standard commands: start, stop, pause, return_home, locate, send_command
    - Position attribute (when cap.pose == 1)
    - Error attributes
    - Cleaning time and area during active missions
    """

    _attr_name = None
    _attr_available = True  # Always available so setup doesn't fail

    def __init__(self, roomba: Any, blid: str, config_entry: "RoombaConfigEntry | None" = None) -> None:
        """Initialise with roombapy Roomba object and BLID."""
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        # Vacuum is the primary entity — its unique_id IS the device identifier.
        self._attr_unique_id = self.robot_unique_id
        self._cap_position: bool = (
            self.vacuum_state.get("cap", {}).get("pose") == 1
        )

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Extend parent setup to register cloud coordinator listener.

        F-I15 (v2.4.0): IRobotVacuum is not a CoordinatorEntity, so the HA
        framework does not call _handle_coordinator_update automatically.
        We register manually so segment change-detection fires after each
        cloud refresh (map retrain → async_create_segments_issue).
        """
        await super().async_added_to_hass()
        if self._config_entry is not None:
            cc = self._config_entry.runtime_data.cloud_coordinator
            if cc is not None:
                self.async_on_remove(
                    cc.async_add_listener(self._handle_coordinator_update)
                )

    # ── Feature flags ────────────────────────────────────────────────────────

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Return supported features, adding CLEAN_AREA for SMART robots with cloud.

        F-I15 (HA 2026.3): CLEAN_AREA is gated on:
          - MapCapability.SMART (i/s/j/m-series only — stable region IDs)
          - cloud coordinator active and has data
          - not a Braava mop (Braava uses padWetness Select, not room segments)
        """
        flags = SUPPORT_IROBOT
        data = (
            self._config_entry.runtime_data
            if self._config_entry is not None
            else None
        )
        if (
            data is not None
            and data.map_capability == MapCapability.SMART
            and data.has_cloud
            and not is_mop(self.vacuum_state)
            and hasattr(VacuumEntityFeature, "CLEAN_AREA")  # HA 2026.3+ only; silently absent on older
        ):
            flags |= VacuumEntityFeature.CLEAN_AREA
        return flags

    # ── Activity ──────────────────────────────────────────────────────────

    @property
    def activity(self) -> VacuumActivity:
        """Map the current cleanMissionStatus phase to a VacuumActivity."""
        status = self.vacuum_state.get("cleanMissionStatus", {})
        cycle = status.get("cycle")
        phase = status.get("phase", "")

        try:
            activity = PHASE_TO_ACTIVITY[phase]
        except KeyError:
            _LOGGER.warning("Unknown Roomba phase: %r — reporting ERROR", phase)
            return VacuumActivity.ERROR

        # If a cycle is active but we appear idle/docked, we are actually paused
        if cycle != "none" and activity in (
            VacuumActivity.IDLE,
            VacuumActivity.DOCKED,
        ):
            activity = VacuumActivity.PAUSED

        return activity

    # ── Extra attributes ──────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes.

        All values are JSON-serialisable primitives.
        Datetime objects are converted to ISO-8601 strings.
        """
        state = self.vacuum_state
        attrs: dict[str, Any] = {
            ATTR_SOFTWARE_VERSION: state.get("softwareVer"),
            "status": self.vacuum.current_state,
        }

        # Cleaning progress (only while actively cleaning)
        if self.activity == VacuumActivity.CLEANING:
            cleaning_time, cleaned_area = self._get_cleaning_status(state)
            attrs[ATTR_CLEANING_TIME] = cleaning_time
            attrs[ATTR_CLEANED_AREA] = cleaned_area

        # Error info
        if self.vacuum.error_code:
            attrs[ATTR_ERROR] = self.vacuum.error_message
            attrs[ATTR_ERROR_CODE] = self.vacuum.error_code

        # Position (models with cap.pose == 1)
        if self._cap_position:
            pos_state = state.get("pose", {})
            pos_x = pos_state.get("point", {}).get("x")
            pos_y = pos_state.get("point", {}).get("y")
            theta = pos_state.get("theta")
            if all(v is not None for v in (pos_x, pos_y, theta)):
                attrs[ATTR_POSITION] = f"({pos_x}, {pos_y}, {theta})"
            else:
                attrs[ATTR_POSITION] = None

        # v1.7.0 — mid-mission attributes consumed by Lovelace card (v1.8).
        # Available on all robots; None for 600-series (no sqft) and when docked.
        mission = state.get("cleanMissionStatus", {})
        # mission_elapsed_min: use mssnM when available; fall back to wall-clock
        # elapsed same as cleaning_time (lewis firmware reports mssnM=0 mid-mission).
        _mssn_m = mission.get("mssnM")
        if not _mssn_m:
            _start_ts = mission.get("mssnStrtTm")
            if _start_ts and mission.get("phase", "") in CLEANING_PHASES:
                import datetime as _dt
                _now = dt_util.now(_dt.timezone.utc).timestamp()
                if _now > _start_ts:
                    _mssn_m = int((_now - _start_ts) // 60)
        attrs["mission_elapsed_min"] = _mssn_m if _mssn_m else None
        attrs["mission_area_sqft"]   = mission.get("sqft")      # int | None, 600=None

        # v1.9.3 — mission phase intelligence attributes
        # Allows dashboards to distinguish mid-mission recharge from user-pause
        # and to show time-remaining without needing separate sensor entities.
        cycle = mission.get("cycle", "none")
        phase = mission.get("phase", "")
        attrs["mid_mission_recharge"] = (
            phase == "charge" and cycle != "none"
        )
        recharge_m = mission.get("rechrgM", 0)
        attrs["recharge_minutes_remaining"] = recharge_m if recharge_m else None
        expire_m = mission.get("expireM", 0)
        attrs["expire_minutes_remaining"] = expire_m if expire_m else None
        attrs["mission_id"] = mission.get("missionId") or None

        # v2.3.0 Step 11 — Live source for planned_room_order / mission_destination
        # during active mission. Two sources tried in order:
        #   1. cleanMissionStatus.cmd.regions — present on some firmware variants
        #      when mission is started via the robot API directly.
        #   2. lastCommand.regions — confirmed present on lewis 22.52.10 when
        #      mission is started via roomba_plus.clean_room (localApp initiator).
        #      cleanMissionStatus.cmd is absent on lewis during active mission.
        # The MissionStore CR4 block below must NOT overwrite these values during
        # an active mission — it only has post-mission timeline data.
        # last_cleaned_rooms and room_coverage are definitionally post-mission.
        if (
            phase in CLEANING_PHASES
            and self._config_entry is not None
            and self._config_entry.runtime_data.has_cloud
            and self._config_entry.runtime_data.cloud_coordinator is not None
        ):
            _live = self._config_entry.runtime_data
            _live_region_map = {
                r["id"]: r["name"]
                for r in _live.cloud_coordinator.regions
                if r.get("id")
            }
            # Try cleanMissionStatus.cmd.regions first, fall back to lastCommand.regions
            _cmd_regions = (
                self.vacuum_state
                .get("cleanMissionStatus", {})
                .get("cmd", {})
                .get("regions", [])
            ) or (
                self.vacuum_state
                .get("lastCommand", {})
                .get("regions", [])
            )
            if _cmd_regions and _live_region_map:
                from .mission_store import MissionStore as _MS
                _rids = [_MS._extract_rid(r) for r in _cmd_regions]
                _rids = [r for r in _rids if r]
                if _rids:
                    _names = [_live_region_map.get(rid, rid) for rid in _rids]
                    attrs["planned_room_order"]  = _names
                    attrs["mission_destination"] = _names[-1]

        # v2.2.0 CR4 — timeline-derived mission attributes (SMART + EPHEMERAL).
        # Populated when a merged timeline field exists in the most recent
        # MissionStore record. Overwrites live source values when available.
        if (
            self._config_entry is not None
            and self._config_entry.runtime_data.mission_store is not None
        ):
            _data = self._config_entry.runtime_data

            # Build region_map from coordinator (SMART path)
            region_map: dict[str, str] = {}
            if (
                _data.has_cloud
                and _data.cloud_coordinator is not None
            ):
                region_map = {
                    r["id"]: r["name"]
                    for r in _data.cloud_coordinator.regions
                    if r.get("id")
                }

            # EPHEMERAL fallback (v2.3.0 Step 10 — Q7 gate)
            # When region_map is empty and UmfAligner is aligned, use its rid→name map.
            umf_regions: dict[str, str] | None = None
            if not region_map and _data.umf_aligner and _data.umf_aligner.aligned:
                umf_regions = _data.umf_aligner.rid_to_name()

            if region_map or umf_regions:
                attrs["last_cleaned_rooms"] = _data.mission_store.latest_cleaned_rooms(region_map, umf_regions)
                attrs["room_coverage"]      = _data.mission_store.latest_room_coverage(region_map, umf_regions)
                # planned_room_order and mission_destination: only update from
                # MissionStore when not in an active cleaning phase. During a
                # mission the live source (lastCommand/cmd.regions) is authoritative;
                # MissionStore only has the previous mission's timeline at this point
                # and would overwrite the live values with stale data.
                if phase not in CLEANING_PHASES:
                    attrs["planned_room_order"]  = _data.mission_store.latest_planned_order(region_map, umf_regions)
                    attrs["mission_destination"] = _data.mission_store.latest_mission_destination(region_map, umf_regions)

        return attrs

    def _get_cleaning_status(
        self, state: dict[str, Any]
    ) -> tuple[int, int]:
        """Return (cleaning_time_minutes, cleaned_area) for the current mission."""
        mission = state.get("cleanMissionStatus", {})
        if not mission:
            return 0, 0

        cleaning_time: int = mission.get("mssnM", 0)
        if not cleaning_time:
            start_ts = mission.get("mssnStrtTm")
            if start_ts:
                now = dt_util.now(datetime.timezone.utc).timestamp()
                if now > start_ts:
                    cleaning_time = int((now - start_ts) // 60)

        cleaned_area: int = mission.get("sqft", 0)
        if cleaned_area and self.hass.config.units is METRIC_SYSTEM:
            cleaned_area = round(cleaned_area * 0.0929)

        return cleaning_time, cleaned_area

    # ── Commands ──────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start or resume cleaning."""
        if self.activity == VacuumActivity.PAUSED:
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, "resume"
            )
        else:
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, "start"
            )

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum cleaner."""
        await self.hass.async_add_executor_job(self.vacuum.send_command, "stop")

    async def async_pause(self) -> None:
        """Pause the cleaning cycle."""
        await self.hass.async_add_executor_job(self.vacuum.send_command, "pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to its dock.

        When cleaning: pauses and waits up to 10 s for confirmation before
        sending dock. If the pause is not confirmed in time, sends stop first
        so the robot is in a defined state before the dock command.
        When already docked or idle: sends dock directly (no-op on robot side).
        """
        if self.activity == VacuumActivity.CLEANING:
            await self.async_pause()
            for _ in range(10):
                if self.activity == VacuumActivity.PAUSED:
                    break
                await asyncio.sleep(1)
            else:
                # Pause not confirmed — stop first for a clean state transition
                await self.hass.async_add_executor_job(
                    self.vacuum.send_command, "stop"
                )
                await asyncio.sleep(1)
        await self.hass.async_add_executor_job(self.vacuum.send_command, "dock")

    async def async_locate(self, **kwargs: Any) -> None:
        """Play a sound to locate the robot."""
        await self.hass.async_add_executor_job(self.vacuum.send_command, "find")

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a raw command to the vacuum.

        Supports region cleaning via extended params:
            command="start", params={"regions": [...], "pmap_id": "..."}
        """
        _LOGGER.debug("send_command %s params=%s", command, params)

        if command == "start" and isinstance(params, dict) and "regions" in params:
            region_cmd = self._build_region_command(params)
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, "start", region_cmd
            )
        else:
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, command, params or {}
            )

    def _build_region_command(self, params: dict[str, Any]) -> dict[str, Any]:
        """Build the region-cleaning payload for send_command.

        Resolves pmap_id and user_pmapv_id. user_pmapv_id is always read from
        live state.pmaps via _resolve_pmapv_id so it is never stale after a
        map retrain. Falls back to the first pmap in state if pmap_id is absent.
        """
        from .services import _resolve_pmapv_id

        pmap_id: str | None = params.get("pmap_id")
        user_pmapv_id: str | None = params.get("user_pmapv_id")

        pmaps: list[dict] = self.vacuum_state.get("pmaps", [])

        if not pmap_id and pmaps:
            first_pmap = pmaps[0]
            pmap_id = next(iter(first_pmap), None)

        # Always refresh user_pmapv_id from live state — override any supplied value.
        if pmap_id:
            fresh = _resolve_pmapv_id(self.vacuum_state, pmap_id)
            if fresh:
                user_pmapv_id = fresh
            else:
                _LOGGER.warning(
                    "_build_region_command: pmap %s not in live state.pmaps — "
                    "map may have been retrained",
                    pmap_id,
                )
                # Fall back to whatever was supplied (may be stale)
                if not user_pmapv_id and pmaps:
                    first_pmap = pmaps[0]
                    user_pmapv_id = first_pmap.get(pmap_id)

        regions = params.get("regions", [])
        normalised_regions = []
        for region in regions:
            if isinstance(region, dict):
                normalised_regions.append(region)
            elif str(region).isdigit():
                normalised_regions.append({"region_id": str(region), "type": "rid"})

        return {
            "ordered": 1,
            "pmap_id": pmap_id,
            "user_pmapv_id": user_pmapv_id,
            "regions": normalised_regions,
        }

    # ── CLEAN_AREA (F-I15, HA 2026.3) ───────────────────────────────────────

    async def async_get_segments(self) -> list:
        """Return regions as Segment objects for the CLEAN_AREA mapping UI.

        Called when the user opens the area↔segment mapping in HA.
        No async_refresh() needed — last coordinator poll data is current enough.
        Gate: only reachable when CLEAN_AREA is in supported_features (SMART + cloud + HA 2026.3+).
        """
        # Defensive: Segment requires HA 2026.3+; guarded by hasattr in supported_features.
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            return []
        data = self._config_entry.runtime_data if self._config_entry else None
        if not data or not data.has_cloud or data.cloud_coordinator is None:
            return []
        active_pmap_id = data.cloud_coordinator.active_pmap_id
        if not active_pmap_id:
            # Coordinator has not yet fetched pmap data — returning segments with a
            # None prefix would create IDs that never match in async_clean_segments.
            _LOGGER.debug(
                "async_get_segments: active_pmap_id not yet available — returning empty"
            )
            return []
        floor_label = (
            self._config_entry.options.get(CONF_FLOOR) or None
            if self._config_entry else None
        )
        return [
            Segment(
                id=f"{active_pmap_id}_{region['id']}",
                name=region.get("name", region["id"]),
                group=floor_label,
            )
            for region in data.cloud_coordinator.regions
            if region.get("id")
        ]

    async def async_clean_segments(
        self, segment_ids: list[str], **kwargs: Any
    ) -> None:
        """Start a region-cleaning mission for the given segment IDs.

        F-I15: kwargs (including repeat) silently ignored — removed from spec Oct 2025.
        twoPass is read from the robot's live noAutoPasses/twoPass state (CleaningPassesSelect).
        Segments from other pmaps are silently ignored; raises ServiceValidationError
        if none of the supplied IDs match the current map (F-RB-2).
        """
        data = self._config_entry.runtime_data if self._config_entry else None
        if not data or not data.has_cloud or data.cloud_coordinator is None:
            return

        active_pmap_id = data.cloud_coordinator.active_pmap_id
        region_ids: list[str] = []
        for seg_id in segment_ids:
            pmap_id, _, region_id = seg_id.partition("_")
            if pmap_id == active_pmap_id:
                region_ids.append(region_id)

        if not region_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_valid_segments",
            )

        two_pass = self._get_two_pass()
        regions = [
            {
                "region_id": rid,
                "type": "rid",
                "params": {"noAutoPasses": False, "twoPass": two_pass},
            }
            for rid in region_ids
        ]

        from .services import _resolve_pmapv_id
        user_pmapv_id = _resolve_pmapv_id(self.vacuum_state, active_pmap_id)
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference,
            "cleanMissionStatus",
            {
                "command": "start",
                "pmap_id": active_pmap_id,
                "user_pmapv_id": user_pmapv_id,
                "regions": regions,
                "ordered": 1,
            },
        )
        # F-RB-1: immediate state update after segment clean command
        await data.cloud_coordinator.async_refresh()

    def _get_two_pass(self) -> bool:
        """Read twoPass preference from live robot state.

        Mirrors what CleaningPassesSelect reads — no entity lookup needed.
        Returns False when the preference is absent (Auto/One-pass modes).
        """
        return bool(self.vacuum_state.get("twoPass", False))

    def _handle_coordinator_update(self) -> None:
        """Standard HA coordinator callback — checks for segment changes.

        F-I15 change-detection: if the region set has changed since the user
        last mapped areas, raise a Repair Issue prompting re-mapping.
        Suppressed when last_seen_segments is None (never configured).
        """
        # No super() call needed — IRobotEntity is not a CoordinatorEntity.
        # The listener is registered in async_added_to_hass; HA does not call
        # this method automatically (no CoordinatorEntity in MRO).

        data = self._config_entry.runtime_data if self._config_entry else None
        if not data or not data.has_cloud or data.cloud_coordinator is None:
            return

        last_seen = self.last_seen_segments
        if last_seen is None:
            return  # never configured — suppress Repair Issue

        active_pmap = data.cloud_coordinator.active_pmap_id
        current_ids = {
            f"{active_pmap}_{r['id']}"
            for r in data.cloud_coordinator.regions
            if r.get("id")
        }
        if current_ids != {seg.id for seg in last_seen}:
            self.async_create_segments_issue()

    # ── Push updates ──────────────────────────────────────────────────────

    def on_message(self, json_data: dict[str, Any]) -> None:
        """Handle state updates from the Roomba MQTT broker."""
        state = json_data.get("state", {}).get("reported", {})
        if self.new_state_filter(state):
            _LOGGER.debug("Vacuum state update: %s", list(state.keys()))
            self.vacuum_state = roomba_reported_state(self.vacuum)
            self.schedule_update_ha_state()


class RoombaVacuum(IRobotVacuum):
    """Roomba without carpet boost — adds bin state to attributes."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes including bin state."""
        attrs = super().extra_state_attributes
        bin_raw = self.vacuum_state.get("bin", {})
        if bin_raw.get("present") is not None:
            attrs[ATTR_BIN_PRESENT] = bin_raw["present"]
        if bin_raw.get("full") is not None:
            attrs[ATTR_BIN_FULL] = bin_raw["full"]
        return attrs


class RoombaVacuumCarpetBoost(RoombaVacuum):
    """Roomba with carpet boost — exposes fan speed control."""

    _attr_fan_speed_list = FAN_SPEEDS

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Add FAN_SPEED to the base feature set (Option B)."""
        return super().supported_features | VacuumEntityFeature.FAN_SPEED


    @property
    def fan_speed(self) -> str | None:
        """Return current fan speed: Automatic / Performance / Eco."""
        carpet_boost = self.vacuum_state.get("carpetBoost")
        high_perf = self.vacuum_state.get("vacHigh")
        if carpet_boost is None or high_perf is None:
            return None
        if carpet_boost:
            return FAN_SPEED_AUTOMATIC
        if high_perf:
            return FAN_SPEED_PERFORMANCE
        return FAN_SPEED_ECO

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set fan speed by sending two delta preferences to the Roomba."""
        canonical = fan_speed.capitalize()
        if canonical not in FAN_SPEEDS:
            _LOGGER.error("Unknown fan speed: %s", fan_speed)
            return

        carpet_boost: bool
        high_perf: bool

        if canonical == FAN_SPEED_AUTOMATIC:
            carpet_boost, high_perf = True, False
        elif canonical == FAN_SPEED_PERFORMANCE:
            carpet_boost, high_perf = False, True
        else:  # Eco
            carpet_boost, high_perf = False, False

        # set_preference sends a delta command; these cannot be batched
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "carpetBoost", str(carpet_boost)
        )
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "vacHigh", str(high_perf)
        )


class BraavaJet(IRobotVacuum):
    """Braava Jet mopping robot.

    Exposes mop behaviour (Standard / Deep / Extended) and spray amount (1–3)
    through the fan_speed interface as "<Behaviour>-<SprayAmount>".
    """

    @property
    def supported_features(self) -> VacuumEntityFeature:
        """Braava uses FAN_SPEED for mop mode — never CLEAN_AREA (Option B).

        is_mop() guard in the parent property already excludes Braava from
        CLEAN_AREA, but we override cleanly to add FAN_SPEED.
        """
        return SUPPORT_IROBOT | VacuumEntityFeature.FAN_SPEED


    def __init__(self, roomba: Any, blid: str, config_entry: "RoombaConfigEntry | None" = None) -> None:
        """Initialise and build the fan speed list."""
        super().__init__(roomba, blid, config_entry)
        self._attr_fan_speed_list = [
            f"{behaviour}-{spray}"
            for behaviour in BRAAVA_MOP_BEHAVIORS
            for spray in BRAAVA_SPRAY_AMOUNT
        ]

    @property
    def fan_speed(self) -> str | None:
        """Return current mop mode as '<Behaviour>-<SprayAmount>'."""
        rank_overlap = self.vacuum_state.get("rankOverlap")
        behaviour_map = {
            OVERLAP_STANDARD: MOP_STANDARD,
            OVERLAP_DEEP: MOP_DEEP,
            OVERLAP_EXTENDED: MOP_EXTENDED,
        }
        behaviour = behaviour_map.get(rank_overlap)
        pad_wetness = self.vacuum_state.get("padWetness", {})
        spray_value = pad_wetness.get("disposable")
        if behaviour is None or spray_value is None:
            return None
        return f"{behaviour}-{spray_value}"

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set mop behaviour and spray amount."""
        try:
            behaviour_str, spray_str = fan_speed.split("-", 1)
            spray = int(spray_str)
        except (ValueError, IndexError):
            _LOGGER.error(
                "Invalid fan speed format %r — expected '<Behaviour>-<Amount>'",
                fan_speed,
            )
            return

        behaviour = behaviour_str.capitalize()
        if behaviour not in BRAAVA_MOP_BEHAVIORS:
            _LOGGER.error("Unknown mop behaviour: %s", behaviour)
            return
        if spray not in BRAAVA_SPRAY_AMOUNT:
            _LOGGER.error("Invalid spray amount: %d", spray)
            return

        overlap_map = {
            MOP_STANDARD: OVERLAP_STANDARD,
            MOP_DEEP: OVERLAP_DEEP,
            MOP_EXTENDED: OVERLAP_EXTENDED,
        }
        overlap = overlap_map[behaviour]

        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "rankOverlap", overlap
        )
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference,
            "padWetness",
            {"disposable": spray, "reusable": spray},
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return Braava-specific state attributes."""
        attrs = super().extra_state_attributes
        state = self.vacuum_state

        attrs[ATTR_DETECTED_PAD] = state.get("detectedPad")
        mop_ready = state.get("mopReady", {})
        attrs[ATTR_LID_CLOSED] = mop_ready.get("lidClosed")
        attrs[ATTR_TANK_PRESENT] = mop_ready.get("tankPresent") or state.get(
            "tankPresent"
        )
        attrs[ATTR_TANK_LEVEL] = state.get("tankLvl")

        bin_raw = state.get("bin", {})
        if bin_raw.get("present") is not None:
            attrs[ATTR_BIN_PRESENT] = bin_raw["present"]
        if bin_raw.get("full") is not None:
            attrs[ATTR_BIN_FULL] = bin_raw["full"]

        return attrs
