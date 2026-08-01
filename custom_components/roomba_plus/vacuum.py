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
# On HA < 2026.3, VacuumEntityFeature.CLEAN_AREA is absent, so supported_features
# never sets the flag and HA never calls async_get_segments(). The try/except
# ImportError below is defensive depth only; the hasattr guard is the primary gate.
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
    ATTR_POSITION,
    POSE_POINT_CM_TO_MM,
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
    MISSION_EVENT_TYPE_TO_ACTIVITY,
    PHASE_TO_ACTIVITY,
    SQFT_TO_M2,
)
from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry

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
        super().__init__(roomba, blid, config_entry)
        self._config_entry = config_entry
        # NEW (V4/Prime): read connection type / prime_robot from
        # runtime_data when available. Defaults (LOCAL_PUSH/None)
        # preserve exact existing behavior for any caller that
        # constructs an entity without a config_entry (some existing
        # unit tests do this directly).
        if config_entry is not None:
            self._connection_type = config_entry.runtime_data.connection_type
            self._prime_robot = config_entry.runtime_data.prime_robot
        else:
            self._connection_type = ConnectionType.LOCAL_PUSH
            self._prime_robot = None
        # Vacuum is the primary entity — its unique_id IS the device identifier.
        self._attr_unique_id = self.robot_unique_id
        self._cap_position: bool = (
            (self.vacuum_state.get("cap") or {}).get("pose") == 1
        )
        # v3.5.0 — SEGMENT-DEBOUNCE (dixi83 field report): consecutive
        # coordinator refreshes with a segment mismatch, used to debounce
        # _handle_coordinator_update()'s call into HA's native remap flow.
        # See that method for the full rationale.
        self._segment_mismatch_streak: int = 0

    @property
    def suggested_object_id(self) -> str | None:
        """Override: vacuum is the primary entity, entity_id = device name only.

        Its unique_id equals robot_unique_id with no suffix, so the IRobotEntity
        base implementation already returns None here (prefix never matches).
        This explicit override documents the intent and guards against future
        changes to the base prefix-strip logic silently appending a suffix.
        """
        return None

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Extend parent setup to register cloud/prime coordinator listeners.

        F-I15 (v2.4.0): IRobotVacuum is not a CoordinatorEntity, so the HA
        framework does not call _handle_coordinator_update automatically.
        We register manually so segment change-detection fires after each
        cloud refresh (map retrain → async_create_segments_issue).

        NEW (V4/Prime): same manual-registration pattern for
        prime_coordinator -- pushes a new MissionTimelineReport on every
        real mission event, which should re-render activity/
        extra_state_attributes. A separate, much simpler callback
        (_handle_prime_coordinator_update) than the cloud one above --
        just a state re-render, no segment-mismatch logic applies here."""
        await super().async_added_to_hass()
        if self._config_entry is not None:
            cc = self._config_entry.runtime_data.cloud_coordinator
            if cc is not None:
                self.async_on_remove(
                    cc.async_add_listener(self._handle_coordinator_update)
                )
            pc = self._config_entry.runtime_data.prime_coordinator
            if pc is not None:
                self.async_on_remove(
                    pc.async_add_listener(self._handle_prime_coordinator_update)
                )
            # THE STATUS COORDINATOR TOO -- this was missing, and it is
            # where activity actually comes from.
            #
            # ROOT CAUSE of a field report (DaRealGuGu) that survived two
            # attempted fixes. His vacuum showed "Returning to dock" while
            # the robot sat on it, and his diagnostics download showed
            # phase="charge", cycle="none" -- data that maps cleanly to
            # DOCKED. The data was right the whole time; nothing ever
            # asked the entity to look at it again.
            #
            # activity reads cleanMissionStatus.phase from THIS
            # coordinator. Subscribing only to the mission-event one
            # meant the vacuum re-rendered when an event arrived and
            # never when the phase changed -- so a phase that settled to
            # "charge" after the last event was simply never displayed.
            #
            # Both earlier attempts (a9, a11) corrected the mapping
            # instead, which was the wrong layer: no mapping fix can
            # help an entity that is not re-reading its source.
            sc = self._config_entry.runtime_data.prime_status_coordinator
            if sc is not None:
                self.async_on_remove(
                    sc.async_add_listener(self._handle_prime_coordinator_update)
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
        # ASKS THE BACKEND (this session), not map_capability.
        #
        # The service can clean rooms on Prime robots now, but this
        # property still said it could not -- so the capability worked
        # while the UI never offered it. Advertising and doing must
        # agree; a feature reachable only by hand-writing a service call
        # is barely a feature.
        #
        # is_mop stays: a Braava targets rooms through padWetness rather
        # than region segments, which is a device difference rather than
        # a generation one.
        from .room_cleaning import async_get_room_cleaning_backend  # noqa: PLC0415

        if (
            self._config_entry is not None
            and async_get_room_cleaning_backend(self._config_entry) is not None
            and not is_mop(self.vacuum_state)
            and hasattr(VacuumEntityFeature, "CLEAN_AREA")  # HA 2026.3+ only; silently absent on older
        ):
            flags |= VacuumEntityFeature.CLEAN_AREA
        return flags

    # ── Activity ──────────────────────────────────────────────────────────

    #: Mission-status operatingMode -> what the robot is doing now.
    #:
    #: CONFIRMED across four captures from one Combo (@DaRealGuGu),
    #: including one taken during the mopping half of a scheduled
    #: vacuum-then-mop run:
    #:
    #:     docked          2   vacuum
    #:     combo running   6   2|4, vacuuming and mopping together
    #:     mopping half    4   mop only
    #:
    #: NOT THE COMMAND NUMBERS. A command asks for 512 (vacuum then mop)
    #: or 32 (combined), and neither ever appears here. Reading one
    #: table against the other is what made 6 look impossible for two
    #: days.
    #:
    #: Anything outside these three is left unreported rather than
    #: guessed: the bitmask may carry values nobody has seen.
    _PRIME_CLEANING_MODES: dict[int, str] = {
        2: "vacuuming",
        4: "mopping",
        6: "vacuuming_and_mopping",
    }

    def _prime_cleaning_mode(self) -> str | None:
        """Vacuuming, mopping, or both -- while a mission is running.

        Only during a mission. A docked robot still reports a mode, and
        it describes the last or next job rather than anything happening
        now -- reporting that would be worse than reporting nothing.
        """
        data = getattr(self._config_entry, "runtime_data", None)
        if data is None or data.connection_type is not ConnectionType.CLOUD_ONLY:
            return None

        coordinator = getattr(data, "prime_status_coordinator", None)
        shadows = getattr(coordinator, "data", None) or {}
        status = (shadows.get("ro-currentstate") or {}).get("cleanMissionStatus") or {}

        if status.get("cycle") in (None, "none"):
            return None
        return self._PRIME_CLEANING_MODES.get(status.get("operatingMode"))

    def _prime_dock_activity(self) -> str | None:
        """What the dock is doing right now, or None if nothing.

        Reads the same two fields the dedicated pad-wash and pad-dry
        sensors use -- this is a convenience view of them on the entity
        people actually look at, not a new source of truth.

        Returns a stable slug rather than a translated string: an
        attribute is consumed by templates and automations, and a value
        that changes with the user's language breaks both.
        """
        data = getattr(self._config_entry, "runtime_data", None)
        if data is None or data.connection_type is not ConnectionType.CLOUD_ONLY:
            return None

        coordinator = getattr(data, "prime_status_coordinator", None)
        shadows = getattr(coordinator, "data", None) or {}
        dock = (shadows.get("ro-currentstate") or {}).get("dock") or {}

        try:
            from roombapy_prime.models.robot_info import DockState  # noqa: PLC0415

            # IN PROGRESS ONLY. The "okay" states mean the dock is idle
            # and well, which the vacuum's own "docked" already says.
            in_progress = {
                DockState.PAD_WASH_IN_PROGRESS: "pad_washing",
                DockState.PAD_DRY_IN_PROGRESS: "pad_drying",
                DockState.DOCK_EVACUATION_IN_PROGRESS: "evacuating",
            }
            for raw in (dock.get("pwState"), dock.get("pdState"), dock.get("state")):
                if raw is None:
                    continue
                try:
                    activity = in_progress.get(DockState(raw))
                except ValueError:
                    continue
                if activity is not None:
                    return activity
        except ImportError:
            return None
        return None

    @property
    def activity(self) -> VacuumActivity:
        """Map the current cleanMissionStatus phase to a VacuumActivity.

        NEW (V4/Prime): for CLOUD_ONLY entries, derives activity from
        the latest mission/timeline/report event type instead -- see
        MISSION_EVENT_TYPE_TO_ACTIVITY's own docstring (const.py) for
        the confidence breakdown per event type. Falls back to IDLE if
        no coordinator data exists yet, or the event type isn't in the
        known set -- deliberately NOT the classic path's ERROR fallback
        below: an unrecognized mission-timeline event type here is far
        more likely to be one of the several known-but-not-yet-mapped
        types (see MissionTimelineEvent's 20 sub-event types) than a
        genuine fault, unlike the classic phase map, where an
        unrecognized phase really would be unexpected. Still NOT a
        complete state facade -- battery level / a direct docked
        boolean (RobotStatusV2) remain unconfirmed; this only
        approximates activity from mission events.
        """
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            # NEW (this session, prompted by a real field report,
            # chairstacker): the mission-timeline event type alone
            # CANNOT express "heading home". His own activity log shows
            # why -- "travel" fires both for room-to-room travel
            # mid-mission AND for the final trip back to the dock, so a
            # completed mission still reported CLEANING all the way
            # home, only flipping once "evac" arrived (by which point
            # the robot was already AT the dock being emptied, so even
            # that RETURNING was both late and, strictly, wrong).
            #
            # cleanMissionStatus.phase (ro-currentstate) is CONFIRMED
            # LIVE for Prime (chairstacker's own real payload, see
            # CleanMissionStatus's docstring in roombapy-prime) and
            # draws exactly the distinction the event stream can't:
            # hmPostMsn (heading home, mission done) vs. hmMidMsn
            # (heading home to recharge, mission continues) vs. run.
            # Reuses Classic's own long-proven PHASE_TO_ACTIVITY map
            # rather than inventing a second, parallel mapping.
            #
            # Falls back to the event-type map when phase is absent or
            # unrecognized -- the event stream stays the safety net,
            # since phase living in a shadow means it could in
            # principle lag or be missing on some firmware, which the
            # event stream would not be.
            status_coordinator = (
                self._config_entry.runtime_data.prime_status_coordinator
                if self._config_entry is not None else None
            )
            # Read once, OUTSIDE the branch below. The event branch
            # further down needs the same cycle value, and scoping it to
            # the phase branch is what let a9's fix cover only half the
            # problem.
            mission_status: dict[str, Any] = {}
            if status_coordinator is not None and status_coordinator.data:
                current_state = status_coordinator.data.get("ro-currentstate") or {}
                mission_status = current_state.get("cleanMissionStatus") or {}
                phase = mission_status.get("phase")
                if phase in PHASE_TO_ACTIVITY:
                    activity = PHASE_TO_ACTIVITY[phase]
                    if (
                        activity == VacuumActivity.RETURNING
                        and self._prime_cycle_is_idle()
                    ):
                        return VacuumActivity.DOCKED
                    return activity
            coordinator = (
                self._config_entry.runtime_data.prime_coordinator
                if self._config_entry is not None else None
            )
            report = coordinator.data if coordinator is not None else None
            if report is None or not report.event:
                return VacuumActivity.IDLE
            activity = MISSION_EVENT_TYPE_TO_ACTIVITY.get(
                report.event[0].event_type, VacuumActivity.IDLE
            )
            # THE SAME CORROBORATION AS THE PHASE BRANCH ABOVE, which it
            # was missing (this session, second field report from the
            # same tester).
            #
            # a9 added the cycle check to the phase branch only. He
            # still saw "Returning to dock" on a robot sitting at its
            # dock -- because when the phase is unmapped or the status
            # shadow has not been seeded, control falls through to HERE,
            # where nothing checked anything.
            #
            # The likely event is "evac": MISSION_EVENT_TYPE_TO_ACTIVITY
            # maps it to RETURNING on the reasoning that a self-emptying
            # base can evac MID-mission, so it is not reliably "docked".
            # That reasoning holds for the mission, not for the robot's
            # position -- during evac the robot has ARRIVED at the dock,
            # mid-mission or not. HA's DOCKED means "at the dock", not
            # "mission over".
            #
            # Left as a corroborated correction rather than remapping
            # evac outright: the mapping's own justification is sound
            # for the mid-mission case, and cycle == "none" is exactly
            # what distinguishes that case from this one.
            if activity == VacuumActivity.RETURNING and self._prime_cycle_is_idle():
                return VacuumActivity.DOCKED
            return activity

        status = self.vacuum_state.get("cleanMissionStatus") or {}
        # Default to "none" so a missing/sparse cleanMissionStatus (cycle absent
        # → None) is treated as "no active cycle". Without this, `None != "none"`
        # is True and the override below would flip a freshly-connected idle or
        # docked robot to PAUSED before its first full status frame arrives.
        cycle = status.get("cycle") or "none"
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

        BUG FOUND (bug-hunt round, V4/Prime): self.vacuum.current_state/
        error_code/error_message crashed for a CLOUD_ONLY entity
        (self.vacuum is None) -- and unlike async_added_to_hass() (called
        once at setup), this property is evaluated on every state write,
        so this would have crashed immediately and repeatedly, not just
        once. "status"/error attrs are None for CLOUD_ONLY -- honest "no
        data available" (no master_state-shaped translation exists yet,
        see RobotStatusV2 blocker), not a fabricated guess.
        """
        state = self.vacuum_state
        attrs: dict[str, Any] = {
            ATTR_SOFTWARE_VERSION: state.get("softwareVer"),
            "status": self.vacuum.current_state if self.vacuum is not None else None,
        }

        # Cleaning progress (only while actively cleaning)
        if self.activity == VacuumActivity.CLEANING:
            cleaning_time, cleaned_area = self._get_cleaning_status(state)
            attrs[ATTR_CLEANING_TIME] = cleaning_time
            attrs[ATTR_CLEANED_AREA] = cleaned_area

        # Error info
        if self.vacuum is not None and self.vacuum.error_code:
            attrs[ATTR_ERROR] = self.vacuum.error_message
            attrs[ATTR_ERROR_CODE] = self.vacuum.error_code

        # Position (models with cap.pose == 1)
        if self._cap_position:
            pos_state = state.get("pose") or {}
            pos_x_raw = (pos_state.get("point") or {}).get("x")
            pos_y_raw = (pos_state.get("point") or {}).get("y")
            theta = pos_state.get("theta")
            if all(v is not None for v in (pos_x_raw, pos_y_raw, theta)):
                # v2.9.0 — firmware reports cm, not mm. See
                # POSE_POINT_CM_TO_MM in const.py.
                pos_x = pos_x_raw * POSE_POINT_CM_TO_MM
                pos_y = pos_y_raw * POSE_POINT_CM_TO_MM
                attrs[ATTR_POSITION] = f"({pos_x}, {pos_y}, {theta})"
            else:
                attrs[ATTR_POSITION] = None

        # v1.7.0 — mid-mission attributes consumed by Lovelace card (v1.8).
        # Available on all robots; None for 600-series (no sqft) and when docked.
        mission = state.get("cleanMissionStatus") or {}
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
                (
                    (self.vacuum_state.get("cleanMissionStatus") or {})
                    .get("cmd") or {}
                ).get("regions", [])
            ) or (
                (self.vacuum_state.get("lastCommand") or {})
                .get("regions", [])
            )
            if _cmd_regions and _live_region_map:
                from .mission_store import MissionStore as _MS
                _rids = [_MS.extract_rid(r) for r in _cmd_regions]
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

        # NEW (V4/Prime). Informational room/mission-progress attributes
        # from the confirmed mission/timeline/report channel -- see
        # activity's own docstring and PrimeCoordinator's module
        # docstring for the full evidence trail. Deliberately does NOT
        # attempt battery level or a docked boolean (RobotStatusV2
        # remains unconfirmed) -- only what this specific channel
        # actually confirms. Reuses the SAME "mission_id" key the
        # classic path above already populates (from a different
        # source) rather than inventing a parallel name, since it's the
        # same real-world concept either way.
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            coordinator = (
                self._config_entry.runtime_data.prime_coordinator
                if self._config_entry is not None else None
            )
            report = coordinator.data if coordinator is not None else None
            if report is not None:
                attrs["mission_id"] = report.mission_id
                if report.event:
                    current = report.event[0]
                    attrs["mission_event_type"] = current.event_type
                    room = current.room or current.travel
                    if room is not None:
                        attrs["current_room_id"] = room.region_id
                    if current.room is not None:
                        attrs["current_room_area"] = current.room.area
                        attrs["current_room_pass_count"] = current.room.pass_count


        # SAVED FAVOURITES, id and name. Prime only -- Classic has no
        # equivalent concept.
        #
        # Costs no entity, and covers what buttons cannot: automations
        # that iterate, templates that list, and the
        # xiaomi-vacuum-map-card menu, which reads attributes.
        #
        # The ID is here on purpose. An automation written against it
        # survives a rename in the iRobot app; one written against the
        # name does not -- and a name is all a button or a select could
        # offer.
        favorites = getattr(
            self._config_entry.runtime_data, "prime_favorites", None
        )
        if favorites:
            attrs["favorites"] = favorites

        # WHAT THE DOCK IS DOING, alongside the vacuum's own state.
        #
        # Requested by @DaRealGuGu: a robot having its pad washed shows
        # as "docked", which is true and unhelpful -- the dock is busy
        # and he wants to see that on the main entity.
        #
        # AN ATTRIBUTE, NOT A STATE. Home Assistant's VacuumActivity has
        # exactly six members -- cleaning, docked, idle, paused,
        # returning, error -- and a vacuum entity reporting anything
        # else is broken, not extended. So "pad washing" cannot be a
        # state no matter how much it deserves to be one.
        #
        # As an attribute it drives a template sensor, a card's
        # secondary line, or an automation condition, which is what he
        # actually wants it for.
        dock_activity = self._prime_dock_activity()
        if dock_activity is not None:
            attrs["dock_activity"] = dock_activity

        # WHAT THE ROBOT IS DOING, when a mission is running.
        #
        # `cycle` stays "clean" for a whole vacuum-then-mop job, so this
        # is the only way to tell the two halves apart. Same reasoning
        # as dock_activity above: an attribute, because VacuumActivity
        # has six members and none of them is "mopping".
        cleaning_mode = self._prime_cleaning_mode()
        if cleaning_mode is not None:
            attrs["cleaning_mode"] = cleaning_mode
        return attrs

    def _get_cleaning_status(
        self, state: dict[str, Any]
    ) -> tuple[int, int]:
        """Return (cleaning_time_minutes, cleaned_area) for the current mission."""
        mission = state.get("cleanMissionStatus") or {}
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
            cleaned_area = round(cleaned_area * SQFT_TO_M2)

        return cleaning_time, cleaned_area

    # ── Commands ──────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start or resume cleaning.

        NEW (V4/Prime): always sends "start" -- self.activity isn't
        reliable yet for Prime (no master_state-shaped translation
        exists, see ConnectionType's docstring / the RobotStatusV2
        blocker in ROOMBA_PLUS_VERSION_PLAN_v4_onwards.md), so trying
        to detect PAUSED first the way the classic path does would
        never resolve correctly. send_simple_command() is confirmed
        live-working (roombapy-prime README's confidence table).
        """
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            await self._prime_robot.send_simple_command("start")
            return
        if self.activity == VacuumActivity.PAUSED:
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, "resume"
            )
        else:
            await self.hass.async_add_executor_job(
                self.vacuum.send_command, "start"
            )

    def _prime_cycle_is_idle(self) -> bool:
        """True when the robot reports no active cleaning cycle.

        Used to corroborate a "returning" reading. Returns False when
        the status shadow is unavailable -- unknown must not be read as
        idle, or a genuine trip back to the dock would be reported as
        docked.
        """
        coordinator = (
            self._config_entry.runtime_data.prime_status_coordinator
            if self._config_entry is not None else None
        )
        if coordinator is None or not coordinator.data:
            return False
        current_state = coordinator.data.get("ro-currentstate") or {}
        mission_status = current_state.get("cleanMissionStatus") or {}
        if "cycle" not in mission_status:
            return False
        return (mission_status.get("cycle") or "none") == "none"

    async def _async_send_verb(self, verb: str) -> None:
        """Sends one simple command verb over whichever transport this
        robot uses.

        EXTRACTED (this session). Four methods -- start, stop, pause and
        locate -- each carried their own identical copy of this branch:

            if CLOUD_ONLY:
                await self._prime_robot.send_simple_command(verb)
                return
            await self.hass.async_add_executor_job(self.vacuum.send_command, verb)

        Four copies of one decision is four chances to update three of
        them. That is not hypothetical here: a fix belonging in one of
        these branches has already been put in the wrong one once.

        NOTE ON WHY THIS IS NOT A SUBCLASS. An earlier architecture note
        proposed an IRobotVacuumPrime subclass for exactly this problem.
        That was wrong, and the reason is worth recording so nobody
        tries it again: the entity class is chosen by DEVICE CAPABILITY
        (BraavaJet / RoombaVacuumCarpetBoost / RoombaVacuum), and
        connection type is orthogonal to that. A Prime robot can be any
        of the three, so subclassing would need BraavaJetPrime,
        RoombaVacuumCarpetBoostPrime and so on -- a combinatorial
        explosion to remove one if-statement.

        Only the four uniform verbs go through here. return_to_base and
        send_command keep their own branches because their two paths
        genuinely differ in behaviour, not just in transport."""
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            await self._prime_robot.send_simple_command(verb)
            return
        await self.hass.async_add_executor_job(self.vacuum.send_command, verb)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum cleaner."""
        await self._async_send_verb("stop")

    async def async_pause(self) -> None:
        """Pause the cleaning cycle."""
        await self._async_send_verb("pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Return the vacuum to its dock.

        When cleaning: pauses and waits up to 10 s for confirmation before
        sending dock. If the pause is not confirmed in time, sends stop first
        so the robot is in a defined state before the dock command.
        When already docked or idle: sends dock directly (no-op on robot side).

        NEW (V4/Prime): sends "dock" directly, skipping the pause-then-
        wait dance above entirely -- self.activity isn't reliable for
        Prime yet (same reasoning as async_start()), so waiting for it
        to report PAUSED would never resolve, only ever hit the 10 s
        timeout every time.
        """
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            await self._prime_robot.send_simple_command("dock")
            return
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
        """Play a sound to locate the robot.

        RESOLVED (jayjay, real device test): send_simple_command("find")
        is CONFIRMED WORKING -- a genuine, audible chime with no robot
        movement. This is the third mechanism tried for this feature;
        the two earlier ones below were both tried live and confirmed
        NOT working, kept here as historical record, not active code.

        FIRST HYPOTHESIS -- DISPROVEN (chairstacker, v4.0.0a0 field
        test): poll_echo_value(), a dedicated REST endpoint for this
        exact feature (CONFIRMED from base_roomba_config.json + native
        SetRoombaEchoAwsIotSerializer analysis) -- did not actually
        make the robot chime, even though the equivalent action works
        fine from the real app.

        SECOND HYPOTHESIS -- ALSO DISPROVEN (chairstacker, real device
        test): the app's own command config names this feature's
        underlying command "SetEchoCommand" -- a shadow WRITE, not a
        REST POST, so roombapy-prime's
        PrimeRobot.trigger_echo_via_shadow() was tried as an
        alternative -- writing to the "echo" field of the named
        "rw-constatus" shadow. The write itself succeeded (a genuine,
        accepted shadow update/delta came back), but the robot did NOT
        chime. Not wired in here -- it doesn't work either.

        THIRD, CONFIRMED-WORKING MECHANISM: send_simple_command("find")
        -- the exact same cmd-topic transport already confirmed for
        start/pause/stop/resume/dock, just a different verb. See
        roombapy-prime's own send_simple_command() docstring for the
        full evidence trail (native analysis tracing the real app's
        own locate button through MissionUIServiceCommand.
        FindLocateRobotRunAction to this exact CommandType.FIND value).
        """
        await self._async_send_verb("find")

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a raw command to the vacuum.

        Supports region cleaning via extended params:
            command="start", params={"regions": [...], "pmap_id": "..."}

        NEW (V4/Prime): not supported yet for CLOUD_ONLY entries -- raises
        a clear ServiceValidationError rather than crashing on
        self.vacuum being None. Region-aware commands specifically are
        explicitly out of v4.0.0a0's scope (send_simple_command() has
        no known way to specify regions/zones at all -- see its
        docstring in roombapy-prime), and even a plain passthrough
        command has no confirmed-safe generic path the way
        send_simple_command()'s narrow, tested verb set does.
        """
        if self._connection_type is ConnectionType.CLOUD_ONLY:
            raise ServiceValidationError(
                "send_command is not yet supported for V4/Prime robots -- "
                "use the standard vacuum actions (start/pause/stop/"
                "return_to_base/locate) instead."
            )
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
        from .room_cleaning import _resolve_pmapv_id  # moved there with the Classic send path

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
        """The rooms HA can map to areas. Delegates to the backend."""
        from .room_cleaning import (  # noqa: PLC0415
            async_get_room_cleaning_backend as _get_backend,
        )

        backend = _get_backend(self._config_entry, self.hass)
        if backend is None:
            return []
        return await backend.get_segments()

    async def async_clean_area(self, cleaning_area_ids: list[str]) -> None:
        """Handle vacuum.clean_area — VacuumEntityFeature.CLEAN_AREA (HA 2026.3+).

        HA resolves the user-configured area → segment mapping from device
        settings and passes the matching vacuum segment IDs here in
        ``{pmap_id}_{region_id}`` format.  Forward directly to
        async_clean_segments() which applies the active-pmap filter and
        sends the command to the robot.

        Note: this method was missing in v2.6.0 / v2.6.1, causing the
        "Start cleaning" button in HA's Clean Area UI to silently do nothing.
        Reported by ronluna (GitHub issue #15).
        """
        await self.async_clean_segments(cleaning_area_ids)

    async def async_clean_segments(
        self, segment_ids: list[str], **kwargs: Any
    ) -> None:
        """Start a segment-cleaning mission. Delegates to the backend.

        Both generations now go through RoomCleaningBackend, so the id
        format is agreed inside one class per generation rather than
        across two files. kwargs (including repeat) are ignored --
        removed from the HA spec in Oct 2025.
        """
        from .room_cleaning import (  # noqa: PLC0415
            async_get_room_cleaning_backend as _get_backend,
        )

        backend = _get_backend(self._config_entry, self.hass)
        if backend is None:
            return
        await backend.clean_segments(segment_ids)

    def _get_two_pass(self) -> bool:
        """Read twoPass preference from live robot state.

        Mirrors what CleaningPassesSelect reads — no entity lookup needed.
        Returns False when the preference is absent (Auto/One-pass modes).
        """
        return bool(self.vacuum_state.get("twoPass", False))

    # v3.5.0 — SEGMENT-DEBOUNCE (dixi83 field report): the number of
    # consecutive coordinator refreshes a segment mismatch must persist
    # before triggering HA's native "map vacuum segments to areas" remap
    # flow. Mirrors this codebase's established window+hysteresis pattern
    # (DRIFT-AUTO, MAP-RETRAIN-WF) for exactly the same reason: a single
    # snapshot comparison fires on any one-refresh blip (a transient
    # region-ID/pmap-ID inconsistency on iRobot's own cloud side, not a
    # genuine map retrain), and the native remap flow is disruptive enough
    # (a modal the user must act on) that it should only trigger for a
    # change that actually sticks around.
    _SEGMENT_MISMATCH_DEBOUNCE = 3

    def _handle_coordinator_update(self) -> None:
        """Standard HA coordinator callback — checks for segment changes.

        F-I15 change-detection: if the region set has changed since the user
        last mapped areas, raise a Repair Issue prompting re-mapping.
        Suppressed when last_seen_segments is None (never configured).

        v3.5.0 SEGMENT-DEBOUNCE: requires the mismatch to persist across
        _SEGMENT_MISMATCH_DEBOUNCE consecutive refreshes before actually
        triggering the remap flow — see _segment_mismatch_streak above.
        Previously fired on the very first mismatched refresh, which meant
        any transient inconsistency in iRobot's own pmap_id/region_id
        assignment (not a real retrain) reopened the same disruptive native
        prompt repeatedly. A genuine retrain still triggers reliably; it
        just needs a few consecutive confirmations first, not a fresh
        rebuild the map from scratch.
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
        if not active_pmap:
            # Coordinator not yet fetched — avoid false positive Repair Issue
            return
        current_ids = {
            f"{active_pmap}_{r['id']}"
            for r in data.cloud_coordinator.regions
            if r.get("id")
        }
        if current_ids != {seg.id for seg in last_seen}:
            self._segment_mismatch_streak += 1
            if self._segment_mismatch_streak >= self._SEGMENT_MISMATCH_DEBOUNCE:
                self.async_create_segments_issue()
        else:
            self._segment_mismatch_streak = 0

    def _handle_prime_coordinator_update(self) -> None:
        """NEW (V4/Prime). Registered as prime_coordinator's listener in
        async_added_to_hass() -- fires on every new MissionTimelineReport
        (a real mission event pushed via mission/timeline/report). Unlike
        _handle_coordinator_update() above, no segment-mismatch logic
        applies here; this just needs to re-render activity/
        extra_state_attributes against the newly-arrived report."""
        self.schedule_update_ha_state()

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
        bin_raw = self.vacuum_state.get("bin") or {}
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
        """Set fan speed by sending two delta preferences to the Roomba.

        v3.1.0 CARPET-BOOST-SLUG-FIX: FAN_SPEEDS values changed from
        Capital-Case ("Automatic") to lowercase slugs ("automatic") to
        satisfy HA's translation_key requirements. Matching is
        case-insensitive so existing automations that still call
        vacuum.set_fan_speed with the old Capital-Case value keep working.
        """
        canonical = fan_speed.lower()
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
        pad_wetness = self.vacuum_state.get("padWetness") or {}
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
        mop_ready = state.get("mopReady") or {}
        attrs[ATTR_LID_CLOSED] = mop_ready.get("lidClosed")
        attrs[ATTR_TANK_PRESENT] = mop_ready.get("tankPresent") or state.get(
            "tankPresent"
        )
        attrs[ATTR_TANK_LEVEL] = state.get("tankLvl")

        bin_raw = state.get("bin") or {}
        if bin_raw.get("present") is not None:
            attrs[ATTR_BIN_PRESENT] = bin_raw["present"]
        if bin_raw.get("full") is not None:
            attrs[ATTR_BIN_FULL] = bin_raw["full"]

        return attrs
