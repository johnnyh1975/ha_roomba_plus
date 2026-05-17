"""Vacuum platform for Roomba+.

Implements the full iRobot vacuum hierarchy:
  IRobotVacuum          — base with all standard commands
  RoombaVacuum          — adds bin state attributes
  RoombaVacuumCarpetBoost — adds carpet boost / fan speed control
  BraavaJet             — adds mop behaviour and spray amount
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
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
    PHASE_TO_ACTIVITY,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

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

    async_add_entities([constructor(roomba, blid)])


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
    _attr_supported_features = SUPPORT_IROBOT
    _attr_available = True  # Always available so setup doesn't fail

    def __init__(self, roomba: Any, blid: str) -> None:
        """Initialise with roombapy Roomba object and BLID."""
        super().__init__(roomba, blid)
        # Vacuum is the primary entity — its unique_id IS the device identifier.
        self._attr_unique_id = self.robot_unique_id
        self._cap_position: bool = (
            self.vacuum_state.get("cap", {}).get("pose") == 1
        )

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
                now = dt_util.as_timestamp(dt_util.utcnow())
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

        The Roomba requires pmap_id and user_pmapv_id. If not supplied in
        params, we fall back to the first pmap found in master_state.
        """
        pmap_id: str | None = params.get("pmap_id")
        user_pmapv_id: str | None = params.get("user_pmapv_id")

        if not pmap_id:
            pmaps: list[dict] = self.vacuum_state.get("pmaps", [])
            if pmaps:
                first_pmap = pmaps[0]
                pmap_id = next(iter(first_pmap), None)
                user_pmapv_id = first_pmap.get(pmap_id) if pmap_id else None

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
    _attr_supported_features = SUPPORT_ROOMBA_CARPET_BOOST

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

    _attr_supported_features = SUPPORT_BRAAVA

    def __init__(self, roomba: Any, blid: str) -> None:
        """Initialise and build the fan speed list."""
        super().__init__(roomba, blid)
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
