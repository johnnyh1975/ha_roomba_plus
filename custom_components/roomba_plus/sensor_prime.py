"""V4/Prime (CLOUD_ONLY) sensors for the Roomba+ sensor platform.

The first CLOUD_ONLY-aware sensors this platform ever had.
sensor.py's existing SENSORS/RoombaSensor machinery (sensor_core.py) is
deeply tied to roomba_reported_state()'s Classic shape (dozens of
filter_fn/value-function callables never audited against roomba=None)
-- rather than risk that large, untested surface, these are
DELIBERATELY separate, minimal entity classes, mirroring the same
"separate CLOUD_ONLY path" pattern already established for vacuum.py
and _async_setup_entry_prime().

All entities pass roomba=None into IRobotEntity.__init__() -- already
confirmed safe (roomba_reported_state(None) returns {}), the same
pattern the CLOUD_ONLY vacuum entity already relies on.

TWO DATA SOURCES, TWO GROUPS OF SENSORS: PrimeMissionEventSensor/
PrimeConnectionHealthSensor read PrimeCoordinator's MissionTimelineReport
(mission/timeline/report push data). PrimeBatterySensor/
PrimeDetectedPadSensor/PrimeRuntimeHoursSensor read
PrimeStatusCoordinator's CurrentStateShadow (the named shadow
"ro-currentstate") -- this is what RESOLVES the earlier "no battery/
dock data" gap: the underlying search that used to be described here
as unconfirmed (RobotStatusV2) is a separate, different structure that
genuinely never appears anywhere; the actual battery/dock/bin/tank
data lives in ro-currentstate instead, confirmed live (chairstacker)
with real values, not guessed at. See prime_coordinator.py's own
docstring for the full evidence trail. Bin/tank presence are
BinarySensorEntity, not SensorEntity -- see binary_sensor.py instead,
matching where their Classic equivalents already live.
"""
from __future__ import annotations

import time

from typing import Any, Final

from datetime import datetime

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfTime

from roombapy_prime.models.mission_history import FaultScene

from .prime_dirt import unfinished_missions
from .const import (
    ERROR_CODE_LABELS,
    CLEANING_PHASES,
    DOCK_TASK_PHASES,
    JOB_INITIATOR_LABELS,
    PRIME_BLOCKING_FAULTS,
    PRIME_ERROR_SEVERITY,
    READINESS_STATE_LABELS,
    get_localized_error_entry,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .prime_coordinator import prime_last_command

#: RE-EXPORTED ON PURPOSE. `binary_sensor.py` imports this helper from
#: here; it lives in this module because the Prime error sensor is its
#: main user. Naming it says the sharing is deliberate.
__all__ = ["get_localized_error_entry"]



class PrimeMissionEventSensor(IRobotEntity, SensorEntity):
    """Current mission-timeline event type, with room-progress attributes.

    native_value: the raw event_type string (e.g. "start"/"reloc"/
    "travel"/"room"/"pause"/"charge"/...) from the most recent
    MissionTimelineReport -- deliberately the raw string, not translated
    into a VacuumActivity here (that translation lives in vacuum.py's
    own activity property; this sensor is the untranslated, diagnostic
    view of the same underlying data, useful for automations that want
    to react to a SPECIFIC event type vacuum.py's activity mapping
    collapses together, e.g. distinguishing "reloc" from "travel" even
    though both currently map to CLEANING).

    extra_state_attributes: mission_id, and current_room_id/area/
    pass_count when the current event is room/travel-shaped -- same
    data vacuum.py's own extra_state_attributes already exposes, kept
    consistent between both rather than diverging.
    """

    entity_description = SensorEntityDescription(
        key="prime_mission_event",
        translation_key="prime_mission_event",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_mission_event"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _report(self) -> Any | None:
        pc = (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )
        return pc.data if pc is not None else None

    @property
    def native_value(self) -> str | None:
        report = self._report
        if report is None or not report.event:
            return None
        return str(report.event[0].event_type)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        report = self._report
        if report is None or not report.event:
            return {}
        attrs: dict[str, Any] = {"mission_id": report.mission_id}
        current = report.event[0]
        room = current.room or current.travel
        if room is not None:
            attrs["current_room_id"] = room.region_id

            # AND ITS NAME, which this attribute never carried.
            #
            # The timeline report has ids and nothing else, so an
            # automation reading "which room is being cleaned" got a
            # number. The device tracker resolves the same ids against
            # `prime_room_names` and shows a name; this sensor did not,
            # so the two disagreed about the same robot in the same
            # moment.
            #
            # `prime_room_names` holds rooms and zones together, which
            # matters here: a zone-targeted mission has never had a
            # name to show anywhere.
            #
            # The id stays alongside. It is what a command takes, and
            # dropping it would break anything already using it.
            _names = getattr(
                self._config_entry.runtime_data, "prime_room_names", None
            ) or {}
            _name = _names.get(str(room.region_id))
            if _name:
                attrs["current_room"] = _name

        if current.room is not None:
            attrs["current_room_area"] = current.room.area
            attrs["current_room_pass_count"] = current.room.pass_count

        # ROOMS THE LAST RUN LEFT UNDONE, and which mission left them.
        #
        # @chairstacker reported a mission that failed on a blocked door
        # and left no trace anywhere: the robot came back, the history
        # showed a completed entry, and nothing said a room had been
        # skipped.
        #
        # `mission_last_unfinished` has carried the answer all along,
        # and it is an object rather than a flag -- so this can say
        # "room 11, mission 61" rather than "something was unfinished".
        # With the mission number an automation can tell a room still
        # waiting from one picked up on the next pass.
        unfinished = self._unfinished_rooms()
        if unfinished:
            attrs["unfinished_rooms"] = unfinished
        return attrs

    def _unfinished_rooms(self) -> dict[str, Any]:
        """Best-effort: an attribute that cannot be built is left out
        rather than taking the sensor down with it."""
        data = self._config_entry.runtime_data
        scores = getattr(data, "prime_clean_scores", None)
        if not scores:
            return {}
        try:
            found = unfinished_missions(scores)
        except Exception:  # noqa: BLE001
            # Silent on purpose: this module has no logger, and an
            # attribute that cannot be built is not worth adding one for
            # -- the sensor's own state is unaffected.
            return {}
        names = getattr(data, "prime_room_names", None) or {}
        return {
            names.get(rid, rid): info.get("mission_number")
            for rid, info in found.items()
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        pc = (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )
        if pc is not None:
            self.async_on_remove(pc.async_add_listener(self.schedule_update_ha_state))


class PrimeConnectionHealthSensor(IRobotEntity, SensorEntity):
    """Whether the mission/timeline/report push connection is currently
    healthy -- our OWN connection state, not anything about the robot
    itself. Deliberately simple (a plain "ok"/"error" string) rather than
    the elaborate 0-100 scored health concept RoombaIntegrationHealthSensor
    (sensor_diagnostics.py) uses for the classic path -- that scoring
    combines several classic-only signals (Repair Issues, MissionArchive
    freshness) that don't apply here; reusing its shape would mean
    fabricating a score from a single boolean. If Prime health tracking
    grows more signals later, revisit unifying with that pattern then.

    native_value: "ok" if the coordinator's last update succeeded (or no
    update has happened yet -- not itself an error), "error" if
    watch_mission_timeline() raised (see PrimeCoordinator's own
    async_set_update_error() call).
    """

    entity_description = SensorEntityDescription(
        key="prime_connection_health",
        translation_key="prime_connection_health",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_connection_health"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _coordinator(self) -> Any | None:
        return (
            self._config_entry.runtime_data.prime_coordinator
            if self._config_entry is not None else None
        )

    @property
    def native_value(self) -> str:
        coordinator = self._coordinator
        if coordinator is None or coordinator.last_update_success:
            return "ok"
        return "error"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self._coordinator
        if coordinator is None or coordinator.last_exception is None:
            return {}
        return {"last_error": str(coordinator.last_exception)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        pc = self._coordinator
        if pc is not None:
            self.async_on_remove(pc.async_add_listener(self.schedule_update_ha_state))


class _PrimeCurrentStateSensorBase(IRobotEntity, SensorEntity):
    """Shared base for V4/Prime sensors reading from
    PrimeStatusCoordinator's "ro-currentstate" data. See
    prime_coordinator.py's own docstring for the coordinator itself,
    and binary_sensor.py's _PrimeStatusSensorBase for the
    BinarySensorEntity-flavored counterpart of this same pattern
    (bin/tank presence live there instead, matching where their
    Classic equivalents already live)."""

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _current_state(self) -> Any:
        from roombapy_prime.models import CurrentStateShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-currentstate")
        if raw is None:
            return None
        return CurrentStateShadow.from_json(raw)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class PrimeBatterySensor(_PrimeCurrentStateSensorBase):
    """V4/Prime battery percentage -- the actual resolution of this
    whole project's multi-session battery-status search. Reads
    CurrentStateShadow.bat_pct (confirmed live, chairstacker: a plain
    int, 0-100, e.g. 72). Same key/device_class/unit as the Classic
    "battery" sensor (sensor_core.py's own SENSORS tuple) so both
    present identically to the user regardless of connection type."""

    entity_description = SensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )
    _attr_entity_category = None

    def __init__(
        self, blid: str, config_entry: RoombaConfigEntry, *, disabled: bool = False
    ) -> None:
        super().__init__(blid, config_entry)
        self._attr_entity_registry_enabled_default = not disabled
        self._attr_unique_id = f"{self.robot_unique_id}_battery"

    @property
    def native_value(self) -> int | None:
        state = self._current_state
        return state.bat_pct if state is not None else None


class PrimeCleaningModeSensor(_PrimeCurrentStateSensorBase):
    """Vacuuming, mopping, or both -- while a mission is running.

    `cycle` stays "clean" for an entire vacuum-then-mop job, so nothing
    in the vacuum entity's own state distinguishes the two halves. This
    reads `cleanMissionStatus.operatingMode`, which does.

    CONFIRMED across four captures from one Combo (@DaRealGuGu),
    including one taken during the mopping half of a scheduled
    vacuum-then-mop run -- the capture that had been missing:

        docked          2   vacuum
        combo running   6   2|4, both engaged together
        mopping half    4   mop only

    WHY A SENSOR RATHER THAN A VACUUM STATE. VacuumActivity has exactly
    six members and none of them is "mopping". A vacuum entity reporting
    anything else is broken rather than extended, so the distinction has
    to live somewhere else. The same value is also on the vacuum as a
    `cleaning_mode` attribute, for templates that want it there.

    THE COMMAND USES THE SAME FIELD NAME FOR DIFFERENT NUMBERS -- 512
    asks for vacuum-then-mop, 32 for a combined run -- and neither
    appears in the status. Reading one table against the other made 6
    look impossible for two days, which is why anything outside the
    three confirmed values is reported as unknown rather than guessed.
    """

    #: Wire value -> Home Assistant state slug.
    #:
    #: Slugs because HA requires [a-z0-9-_]+ for translated ENUM states.
    _MODES: dict[int, str] = {
        2: "vacuuming",
        4: "mopping",
        6: "vacuuming_and_mopping",
    }

    entity_description = SensorEntityDescription(
        key="prime_cleaning_mode",
        translation_key="prime_cleaning_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["vacuuming", "mopping", "vacuuming_and_mopping"],
    )

    def __init__(
        self, blid: str, config_entry: RoombaConfigEntry, *, disabled: bool = False
    ) -> None:
        super().__init__(blid, config_entry)
        self._attr_entity_registry_enabled_default = not disabled
        self._attr_unique_id = f"{self.robot_unique_id}_prime_cleaning_mode"

    @property
    def suggested_object_id(self) -> str:
        return "cleaning_mode"

    @property
    def native_value(self) -> str | None:
        """None while docked, on purpose.

        A docked robot still reports a mode, and it describes the last
        or the next job rather than anything happening now. Showing that
        as the current activity is exactly the misreading that made an
        earlier look at this field conclude it never moves.
        """
        state = self._current_state
        status = getattr(state, "clean_mission_status", None) if state else None
        if status is None:
            return None
        cycle = getattr(status, "cycle", None)
        if cycle in (None, "none"):
            return None
        return self._MODES.get(getattr(status, "operating_mode", None) or 0)


class PrimeDetectedPadSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime detected mop pad type. Reads
    RESOLVED 31 July 2026 (@chairstacker), and the doubt recorded here
    before was wrong. He ran the sequence deliberately:

        both pads fitted    -> padPlate
        left pad removed    -> NoPad
        left pad refitted   -> padPlate
        right pad removed   -> NoPad
        right pad refitted  -> padPlate

    The sensor tracks pad presence correctly. `padPlate` is the value
    for "a pad is detected", not a report about the mounting plate.

    An earlier tester saw `padPlate` on two missions and believed one of
    them had no pad fitted -- which produced the wrong conclusion here.
    Two data points from separate accounts beat one, and a deliberate
    sequence beats both.

    WHAT REMAINS is the wording: `padPlate` and `NoPad` are wire values,
    not something to show a user. Translated below -- but the chosen
    words carry two assumptions that are NOT established:

    1. WHICH pad is missing cannot be known -- CLOSED, negative.

       @chairstacker's robot takes two pads, and removing either one
       flipped the sensor to NoPad while the other stayed mounted. An
       APK pass then confirmed the app cannot do better: there is no
       left/right field in the shadow, no count constant, the readiness
       values are all global (NoPad, InvalidPad, PadDetectionTimeout),
       and every pad error string is generic --

           "Mop pad missing"
           "%s's pad is missing."

       -- while the same app IS specific about other parts ("Dock: pad
       washing roller missing", "The Clean Base bag is missing"). It
       phrases precisely when it has the information. For pads it does
       not have it.

       So "All pads fitted" / "Pad missing" is exactly what the app
       itself shows. Nothing to change, and nothing to look for.

    2. TYPES EXIST, this robot just never reports them -- options list
       widened accordingly.

       The app carries six type-specific strings ("Reusable Wet Mopping
       Pad attached", "Single-Use Damp Sweeping Pad attached", ...),
       matching the PadCategory wire values. A pad-plate robot reports
       `padPlate` and no type; a robot that takes a pad directly
       presumably reports the type instead.

       Nobody has sent a capture from such a robot, so all seven values
       are listed and translated rather than waiting for one -- an ENUM
       sensor renders an unlisted value as a raw string.

    CurrentStateShadow.detected_pad directly (confirmed live,
    chairstacker: a plain string, e.g. "padPlate") -- the raw reported
    value, not translated into a friendlier label, since the full set
    of possible values isn't confirmed yet (see that field's own
    docstring).

    IT SENSES THE CARRIER PLATE, NOT THE CLOTH. Established by the one
    experiment that could establish it -- the same robot, same SKU,
    reading `noPad` in one capture and `padPlate` in the next, with
    nothing about a mop pad changed in between. What changed was that
    the plastic mop PLATE had been clicked back in, with no cloth on it
    (@utkjmitch, Y351020, a20).

    So `padPlate` means "plate fitted" and reads that way with a bare
    plate. ANYTHING GATING MOP BEHAVIOUR ON THIS FIELD IS GATING ON THE
    WRONG THING: it cannot say whether there is a cloth that could be
    washed, dried or worn out.

    Two before-and-after captures on one unit beat any number of
    single-state captures from different robots, which is why this was
    settled by an accident during a rescue rather than by the pad-fitted
    capture that had been asked for."""

    entity_description = SensorEntityDescription(
        key="prime_detected_pad",
        translation_key="prime_detected_pad",
        # ENUM so Home Assistant translates the state. Without it the
        # user sees the wire values -- `padPlate` and `NoPad` -- which
        # are neither English nor descriptive, as @chairstacker pointed
        # out after confirming the sensor itself works correctly.
        device_class=SensorDeviceClass.ENUM,
        # ALL SEVEN RobotPadCategory VALUES, not just the two this
        # project has observed.
        #
        # An ENUM sensor shows a value outside its options list as a raw
        # string, so a robot reporting `reusableWet` would display
        # "reusableWet" to the user.
        #
        # APK analysis found six type-specific UI strings ("Reusable Wet
        # Mopping Pad attached", "Single-Use Damp Sweeping Pad
        # attached", ...), so the types are real. Robots with a pad
        # plate report `padPlate` and never a type; robots that take a
        # pad directly presumably report the type instead. Nobody has
        # sent a capture from one of those.
        #
        # BOTH SPELLINGS OF "no pad" are listed. The library's enum says
        # `noPad`; a real robot reported `NoPad`. Until a capture settles
        # which the wire uses, accepting one and rendering the other raw
        # is the avoidable failure.
        # SLUGS, not wire values -- HA requires [a-z0-9-_]+ here and
        # rejects camelCase at validation time. See _PAD_STATE_SLUGS.
        options=[
            "pad_plate",
            "no_pad",
            "disp_dry",
            "disp_wet",
            "reusable_dry",
            "reusable_wet",
            "invalid",
        ],
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_detected_pad"

    @property
    def native_value(self) -> str | None:
        """The wire value as a Home Assistant state slug.

        HA REQUIRES `[a-z0-9-_]+` for translated ENUM states, and the
        wire values are camelCase -- `padPlate`, `dispDry`, `reusableWet`.
        Publishing them directly fails hassfest outright, which is how
        this was caught.

        The mapping also settles a spelling question: one robot reported
        `NoPad`, the library's enum says `noPad`, and nobody knows which
        the wire actually uses. Both map to `no_pad`, so it stops
        mattering.

        An unmapped value falls through as-is rather than being dropped.
        It renders as a raw string, which is ugly and visible -- better
        than a sensor that silently reads "unknown" on a robot reporting
        something new.
        """
        state = self._current_state
        raw = state.detected_pad if state is not None else None
        if raw is None:
            return None
        return _PAD_STATE_SLUGS.get(str(raw), str(raw))


#: Wire value -> Home Assistant state slug.
#:
#: All seven RobotPadCategory values, plus both observed spellings of
#: "no pad" collapsing onto one slug.
_PAD_STATE_SLUGS: dict[str, str] = {
    "padPlate": "pad_plate",
    "NoPad": "no_pad",
    "noPad": "no_pad",
    "dispDry": "disp_dry",
    "dispWet": "disp_wet",
    "reusableDry": "reusable_dry",
    "reusableWet": "reusable_wet",
    "invalid": "invalid",
}


#: Dock state codes seen in the field that DockState does not contain.
#:
#: Kept separate from the enum on purpose. DockState's 84 members come
#: from APK analysis and are verified as a set; mixing a field-observed
#: value into it would blur a clean provenance boundary and make the
#: next person unable to tell which values were decompiled and which
#: were inferred from a robot's behaviour.
#:
#: 671 -- @chairstacker, Combo 405, dock fwVer 20. Controlled before and
#: after: with the clean water tank removed, dock.pwState read 671; the
#: moment it went back in, 601 (PAD_WASH_OKAY). dock.state (301),
#: dock.error (0) and pdState (701) did not move either way, so this one
#: field carries the whole signal. Also seen earlier with the tank
#: EMPTY rather than missing, hence the deliberately broad wording --
#: naming it "empty" would send someone to refill a tank that is not
#: fitted.
#:
#: NOT IN THE DockState ENUM, whose pad-wash family stops at 669
#: (PAD_WASH_PAD_ACTUATOR_STALL_ERROR). DockStateImpl carries a
#: "Unknown dock state %d" fallback, which is why the sensor had
#: nothing to show.
#:
#: THREE DIFFERENT 671s EXIST. This comment said two, and that was
#: already a correction of an earlier "671 does not exist in the APK".
#: The full picture:
#:
#:   dock.pwState 671   this state -- pad wash blocked, field-observed
#:   C671               a CONNECTION error in res/raw; 59 of 67 guides
#:                      in that catalogue share one generic WiFi text
#:   Error 671          a genuine Prime mission error code, article
#:                      70671 in iRobot's own help catalogue
#:
#: Three numbering spaces, one number. Every round of this project that
#: got 671 wrong got it wrong by asking "same number?" instead of "same
#: field?" -- and each answer looked complete until the next source
#: turned up.
#:
#: The label below stands on its own evidence: two controlled
#: before-and-after observations on a real dock, which remain the only
#: source describing THIS state.
_FIELD_OBSERVED_DOCK_STATES: Final[dict[int, str]] = {
    671: "Pad wash not possible (check tanks)",
}


def _dock_state_label(raw_value: Any) -> str | None:
    """Formats a DockState enum member (or its raw int, if the value
    isn't one of the 86 confirmed members) into a readable label --
    e.g. DOCK_READY -> "Dock ready". Not run through HA's own
    device_class=ENUM/translated-options machinery: DockState has 86
    members, mostly rarely-seen *_ERROR states -- translating all of
    them in all 8 languages would be a disproportionate effort for
    values a real user will almost never see, the same reasoning
    already applied to PrimeDetectedPadSensor above."""
    from roombapy_prime.models.robot_info import DockState

    if raw_value is None:
        return None
    try:
        member = DockState(raw_value)
    except ValueError:
        # Field-observed codes before the bare fallback: "Unknown (671)"
        # is what a tester actually saw for a condition this project can
        # name.
        known = _FIELD_OBSERVED_DOCK_STATES.get(raw_value)
        return known if known else f"Unknown ({raw_value})"
    return str(member.name.replace("_", " ").capitalize())


class PrimeDockStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime dock status. Reads CurrentStateShadow.dock.state
    (confirmed live, chairstacker: 301 -> DockState.DOCK_READY) --
    see DockState's own docstring in roombapy-prime for the full,
    86-value confirmed enum this is drawn from."""

    entity_description = SensorEntityDescription(
        key="prime_dock_status",
        translation_key="prime_dock_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_dock_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None

        # A DOCK THE ROBOT DOES NOT KNOW REPORTS NO STATE, and saying
        # so beats reading "unknown" forever.
        #
        # @utkjmitch has reported this since a32. His dock block is the
        # whole of `{"fwVer": "", "known": false, "error": 0}` -- no
        # `state`, no `cap`, nothing. @chairstacker's, for contrast:
        #
        #     {"cap": {"evac": 1, "pd": 2, "pw": 1, "pwo": 1},
        #      "state": 301, "pdState": 701, "pwState": 601,
        #      "known": true, "fwVer": "20"}
        #
        # That comparison answers the question @utkjmitch left open --
        # he asked whether some other robot carries a `cap` object where
        # his carries nothing, because that would mean `known: false` is
        # about IDENTITY rather than capability. It does, and it is:
        # his dock is mute rather than passive. The robot does not
        # recognise it, so it reports no capability fields at all.
        #
        # The sensor was right to have nothing to say. It just did not
        # say which nothing.
        if state.dock.state is None:
            # Plain text, like every other value this sensor returns
            # -- "Pad wash okay", "Unknown (671)". It has no translated
            # state list.
            return (
                "Not reported by this dock"
                if state.dock.known is False
                else None
            )

        return _dock_state_label(state.dock.state)


class PrimePadWashStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime pad wash status. Reads CurrentStateShadow.dock.pw_state
    (confirmed live, chairstacker: 601 -> DockState.PAD_WASH_OKAY)."""

    entity_description = SensorEntityDescription(
        key="prime_pad_wash_status",
        translation_key="prime_pad_wash_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_pad_wash_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return _dock_state_label(state.dock.pw_state)


class PrimeDockTankLevelSensor(_PrimeCurrentStateSensorBase):
    """Clean water tank level in the dock, for docks that report one.

    GATED ON THE FIELD BEING PRESENT, not on a capability flag, and that
    is a deliberate choice rather than laziness.

    Two docks, one capture each: fwVer 24 with dock.cap.pd 3 sends
    tankLvl 100; fwVer 20 with pd 2 never sends the key at all -- not
    even while a pad wash was failing for lack of water. Two variables
    differ at once, so the field cannot say which governs, and the APK
    cannot either: pd/pw/pwo are not literals, the mapping lives in a
    runtime-filled map<string, DockCapability>, and DockCapability is
    purely categorical with no notion of a level 2 or 3.

    Gating on presence means the tester whose dock stays silent gets no
    entity rather than one reading "unknown" forever. Same reasoning as
    the pad-wash and pad-dry sensors after the Max 705 report.

    `gwTankLvl` (grey water) gets no sensor: the literal exists in the
    app's native library but its role could not be established, and it
    appears in no capture from either dock.
    """

    entity_description = SensorEntityDescription(
        key="prime_dock_tank_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_name = "Dock clean water tank"
        self._attr_unique_id = f"{self.robot_unique_id}_prime_dock_tank_level"

    @property
    def native_value(self) -> int | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return int(state.dock.tank_lvl)


class PrimePadDryStatusSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime pad dry status. Reads CurrentStateShadow.dock.pd_state
    (confirmed live, chairstacker: 701 -> DockState.PAD_DRY_OKAY)."""

    entity_description = SensorEntityDescription(
        key="prime_pad_dry_status",
        translation_key="prime_pad_dry_status",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_pad_dry_status"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None or state.dock is None:
            return None
        return _dock_state_label(state.dock.pd_state)


class PrimeSuctionLevelSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime configured suction level. Reads RobotSettings.suction_level
    from the named shadow "rw-settings" -- a SEPARATE data source from
    the other sensors on this page (ro-currentstate), same pattern as
    PrimeFirmwareVersionSensor's own rw-software read. SuctionLevel is
    fully confirmed (5 values: Invalid/Low/Medium/High/Turbo, see that
    enum's own docstring in roombapy-prime) -- properly modeled as a
    real device_class=ENUM sensor with translated states, unlike the
    dock-status sensors above (which have too many rarely-seen values
    for that to be worth the translation effort)."""

    entity_description = SensorEntityDescription(
        key="prime_suction_level",
        translation_key="prime_suction_level",
        device_class=SensorDeviceClass.ENUM,
        options=["invalid", "low", "medium", "high", "turbo"],
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_suction_level"

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import RobotSettings
        from roombapy_prime.models.mission_control import SuctionLevel

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None
        settings = RobotSettings.from_json(raw)
        if settings.suction_level is None:
            return None
        try:
            return str(SuctionLevel(settings.suction_level).name.lower())
        except ValueError:
            return None


class PrimeRuntimeHoursSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime lifetime runtime hours. Reads
    CurrentStateShadow.runtime_stats.hours (confirmed live,
    chairstacker: 44) -- minutes exposed as an extra_state_attribute
    rather than a separate entity, since it's a sub-component of the
    same lifetime-runtime figure, not an independent measurement."""

    entity_description = SensorEntityDescription(
        key="prime_runtime_hours",
        translation_key="prime_runtime_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_runtime_hours"

    @property
    def native_value(self) -> int | None:
        state = self._current_state
        if state is None or state.runtime_stats is None:
            return None
        return int(state.runtime_stats.hours)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._current_state
        if state is None or state.runtime_stats is None:
            return {}
        return {"minutes": state.runtime_stats.minutes}


class PrimeFirmwareVersionSensor(IRobotEntity, SensorEntity):
    """V4/Prime firmware version -- read from the named shadow
    "rw-software" (SoftwareStatusShadow.software_version), confirmed
    live (chairstacker) as a plain string via Ghidra decompilation of
    the app's own constructor signature (type-tag 3). A separate data
    source from the "ro-currentstate"-backed sensors above -- see
    prime_coordinator.py's own docstring: PrimeStatusCoordinator seeds
    and watches ALL eight named shadows, not just ro-currentstate."""

    entity_description = SensorEntityDescription(
        key="prime_firmware_version",
        translation_key="prime_firmware_version",
        entity_registry_enabled_default=True,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_firmware_version"

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import SoftwareStatusShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-software")
        if raw is None:
            return None
        return str(SoftwareStatusShadow.from_json(raw).software_version)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class _PrimeStatsSensorBase(IRobotEntity, SensorEntity):
    """Shared base for V4/Prime sensors reading from
    PrimeStatusCoordinator's "ro-stats" data (StatsShadow) -- all
    confirmed with REAL VALUES this session (chairstacker's
    raw_shadows.json capture), unlike when this shadow was first
    modeled with key names only. See roombapy-prime's own
    models/robot_info.py::StatsShadow for the full evidence trail,
    including the internal-consistency checks that confirm these are
    genuine lifetime counters, not arbitrary numbers (BbMssnStats's
    counters sum exactly; BbSysStats's hour count matches the
    device's registration age)."""

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def _stats(self) -> Any:
        from roombapy_prime.models import StatsShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-stats")
        if raw is None:
            return None
        return StatsShadow.from_json(raw)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


class PrimeTotalMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime mission count. Reuses Classic's OWN
    translation_key ("total_missions") rather than a new Prime-specific
    one -- StatsShadow.bbmssn.n_mssn is confirmed (this session) to be
    the exact same field (nMssn) Classic's own equivalent sensor reads,
    just via a different transport (cloud shadow vs local MQTT). Real
    value seen: 276, cross-validated against ro-currentstate's own
    cleanMissionStatus.nMssn from the SAME capture.

    COUNTER SEMANTICS, clarified by a real field observation
    (chairstacker, v4.0.0a6): this total does NOT always equal
    successful + canceled + failed. It matches exactly when the robot
    is IDLE (confirmed: 247 + 25 + 4 = 276 in the capture above), but
    is one HIGHER while a mission is in progress -- n_mssn increments
    when a mission STARTS, whereas the three outcome counters only
    increment once it ENDS with a known result. The in-flight mission
    is therefore counted in the total but not yet in any outcome
    bucket. This is the robot's own counter behavior, faithfully
    reported (no arithmetic happens on our side) -- NOT an off-by-one
    on this integration's part. Worth remembering before treating the
    sum as an invariant anywhere: it holds at rest, not during a
    mission."""

    entity_description = SensorEntityDescription(
        key="prime_total_missions",
        translation_key="total_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_total_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return int(stats.bbmssn.n_mssn)


class PrimeSuccessfulMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime successful-mission count. Reuses Classic's own
    "successful_missions" translation_key -- see PrimeTotalMissionsSensor's
    own docstring for the field-equivalence evidence. Real value seen: 247."""

    entity_description = SensorEntityDescription(
        key="prime_successful_missions",
        translation_key="successful_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_successful_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return int(stats.bbmssn.n_mssn_ok)


class PrimeCanceledMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime canceled-mission count. Reuses Classic's own
    "canceled_missions" translation_key. Real value seen: 25."""

    entity_description = SensorEntityDescription(
        key="prime_canceled_missions",
        translation_key="canceled_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_canceled_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return int(stats.bbmssn.n_mssn_canceled)


class PrimeFailedMissionsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime failed-mission count. Reuses Classic's own
    "failed_missions" translation_key. Real value seen: 4."""

    entity_description = SensorEntityDescription(
        key="prime_failed_missions",
        translation_key="failed_missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_failed_missions"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbmssn is None:
            return None
        return int(stats.bbmssn.n_mssn_failed)


class PrimeChargeCyclesOkSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime successful charge-cycle count
    (StatsShadow.bbchg.n_chg_ok). NEW translation key -- NOT the same
    concept as Classic's own "battery_cycles" sensor, which depends on
    nLithChrg/nNimhChrg (fields absent entirely in the one real Prime
    capture seen so far, see BbChg3Stats's own docstring) -- this reads
    a genuinely different sub-field (bbchg, not bbchg3) that Classic's
    own bbchg sensors don't surface at all (Classic's own bbchg holds
    dock-contact-health counters -- nChatters/nKnockoffs/nAborts --
    not charge-success/failure counts). Real value seen: 561."""

    entity_description = SensorEntityDescription(
        key="prime_charge_cycles_ok",
        translation_key="prime_charge_cycles_ok",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_charge_cycles_ok"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbchg is None:
            return None
        return int(stats.bbchg.n_chg_ok)


class PrimeChargeCyclesErrorSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime failed charge-cycle count
    (StatsShadow.bbchg.n_chg_err). See PrimeChargeCyclesOkSensor's own
    docstring for why this is a new translation key, not a Classic
    reuse. Real value seen: 0."""

    entity_description = SensorEntityDescription(
        key="prime_charge_cycles_error",
        translation_key="prime_charge_cycles_error",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_charge_cycles_error"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbchg is None:
            return None
        return int(stats.bbchg.n_chg_err)


class PrimeSystemUptimeSensor(_PrimeStatsSensorBase):
    """V4/Prime powered-on hours (StatsShadow.bbsys.hours). No Classic
    equivalent -- genuinely new for Prime.

    CONFIRMED as POWERED-ON time rather than time since registration,
    by two field accounts at opposite ends of the range: one robot
    rarely switched off showed a 14-hour gap against wall-clock time,
    another that had been unplugged for months showed a 5579-hour gap.
    Both match what their owners recalled. See BbSysStats's own
    docstring for the full comparison.

    THEREFORE: do not label or describe this as device age or "time
    since you got the robot". On a robot that has spent months
    unplugged the two differ by more than half, and a user reading it
    as age would be badly misled."""

    entity_description = SensorEntityDescription(
        key="prime_system_uptime",
        translation_key="prime_system_uptime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_system_uptime"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbsys is None:
            return None
        return int(stats.bbsys.hours)


class PrimeNavigationResetsSensor(_PrimeStatsSensorBase):
    """V4/Prime lifetime navigation-reset count
    (StatsShadow.bbrstinfo.n_nav_rst). NEW translation key, DELIBERATELY
    not reusing Classic's own "reset_diagnostics" key: that sensor's
    own native_value is nSafRst (safety-triggered resets), a DIFFERENT
    primary field than the one confirmed for Prime so far (nNavRst --
    nSafRst/nMobRst/safCauses were all absent entirely in the one real
    capture seen). Reusing the same key/wording would imply this shows
    the same metric Classic's does, which isn't confirmed. Real value
    seen: 22."""

    entity_description = SensorEntityDescription(
        key="prime_navigation_resets",
        translation_key="prime_navigation_resets",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_navigation_resets"

    @property
    def native_value(self) -> int | None:
        stats = self._stats
        if stats is None or stats.bbrstinfo is None:
            return None
        return int(stats.bbrstinfo.n_nav_rst)


class PrimeSerialNumberSensor(IRobotEntity, SensorEntity):
    """V4/Prime serial number -- read from the named shadow
    "ro-configinfo" (ConfigInfoShadow.hw_parts_rev.nav_serial_no),
    confirmed live (chairstacker) as a real value
    ("G185020H250311N105749", matching the device's own SKU prefix
    G185020). A separate data source from the ro-stats-backed sensors
    above -- same reasoning as PrimeFirmwareVersionSensor's own
    docstring (rw-software): PrimeStatusCoordinator seeds/watches ALL
    eight named shadows independently."""

    entity_description = SensorEntityDescription(
        key="prime_serial_number",
        translation_key="prime_serial_number",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_serial_number"

    @property
    def suggested_object_id(self) -> str:
        return self.entity_description.key

    @property
    def native_value(self) -> str | None:
        from roombapy_prime.models import ConfigInfoShadow

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-configinfo")
        if raw is None:
            return None
        config_info = ConfigInfoShadow.from_json(raw)
        if config_info.hw_parts_rev is None:
            return None
        return config_info.hw_parts_rev.nav_serial_no or None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))


def _blocking_faults(status: Any) -> dict[int, frozenset[str]]:
    """The start-blocking faults currently reported, with what each still allows.

    READS BOTH FIELDS. `error` carries a fault the robot hit; a readiness
    REFUSAL leaves `error` at 0 and puts its reasons in `cond_not_ready`
    instead. The four blocking codes can arrive either way, so checking
    one of the two would miss the case the check exists for.

    Returns an empty mapping when nothing blocks -- callers should treat
    that as "no opinion", not as "everything is possible", because this
    only knows about four codes out of a hundred and twelve.
    """
    if status is None:
        return {}
    codes: set[int] = set()
    error = getattr(status, "error", None)
    if isinstance(error, int) and error:
        codes.add(error)
    for reason in getattr(status, "cond_not_ready", None) or []:
        if isinstance(reason, int):
            codes.add(reason)
        elif isinstance(reason, str) and reason.isdigit():
            codes.add(int(reason))
    return {c: PRIME_BLOCKING_FAULTS[c] for c in codes if c in PRIME_BLOCKING_FAULTS}


class PrimeErrorSensor(_PrimeCurrentStateSensorBase):
    """V4/Prime error label, read from
    CurrentStateShadow.clean_mission_status.error (CONFIRMED LIVE for
    Prime, chairstacker's own ro-currentstate payload).

    REPORTS THE CODE, NOT A LABEL, and that is a correction rather
    than an omission.

    This class used to state that "the codes are the same product-wide
    catalogue, only the transport differs". That was an assumption
    written as a fact, and it does not hold up:

      - A field capture contradicts the label outright (@utkjmitch,
        Y351020): `error: 46` with `phase: "stuck"` on a robot
        physically confirmed stuck, at 55% battery. ERROR_CODE_LABELS
        renders 46 as "Low battery".
      - iRobot's own help catalogue gives Prime and Classic DIFFERENT
        articles for the same code. Of 16 codes present in both the i7
        and the Combo 405 catalogue, all 16 point elsewhere:

              code 2   i7 8957   Prime 10531
              code 46  i7 8974   Prime 10546
              code 671 --        Prime 70671

        Between two CLASSIC models, 4 of 21 shared codes are
        article-identical. Between Classic and Prime: none.

    A CORRECTION TO THIS CLASS'S OWN EARLIER REASONING. It said "APK
    analysis found no error-code table in the app at all, not for Prime,
    not for Classic". True of the APK, and wrong as a conclusion: the
    table exists, it just lives in the service rather than the app.
    `GET /v2/help/{lang}/{country}/{sku}` returns a full Prime-specific
    catalogue for G185020 -- 43 Error, 21 Charging Error, 15
    Start-Refuse entries, no authentication.

    So the decision was right and the argument was not. The article
    split above is the better evidence and it is a vendor's own, rather
    than one field capture: iRobot itself treats these as separate
    namespaces.

    ERROR_CODE_LABELS remains community knowledge from rest980,
    validated by Classic users over years -- real evidence, for Classic.

    The reasoning is the same one already applied to consumable parts
    202 and 212 a few screens below: a wrong label gets believed, a
    number invites a question. The Classic reading stays reachable as
    an attribute rather than being thrown away -- it is probably right
    more often than not, it just must not be asserted.

    INHERITS CLASSIC'S HARD-WON STALE-ERROR SUPPRESSION, deliberately
    rather than reading the field raw: cleanMissionStatus.error
    PERSISTS across missions -- the firmware does not reset it to 0
    when the robot docks after a failure (see _error_value()'s own
    docstring in sensor_helpers.py, where Classic learned this). A
    naive Prime sensor would therefore show a long-finished error
    indefinitely while the robot sits charging. Same suppression rule:
    when there's no active or queued mission (cycle "none") and the
    phase indicates rest, report "None".

    ALSO EXPOSES not_ready / cond_not_ready as attributes (this
    session): per the parallel APK research, a readiness-based START
    REFUSAL (ResolvedMissionStatus 7/8/12/13) surfaces through those
    two fields rather than through `error` -- so a robot that refused
    to start would leave `error` at 0 while cond_not_ready carries the
    actual reasons. Keeping them visible here means that case is
    diagnosable from the entity itself, not only from a CLI script."""

    entity_description = SensorEntityDescription(
        key="prime_error",
        translation_key="error",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_error"

    @property
    def _mission_status(self) -> Any:
        state = self._current_state
        return None if state is None else state.clean_mission_status

    @property
    def native_value(self) -> str | None:
        status = self._mission_status
        if status is None:
            return None
        # Same rule Classic uses -- see this class's own docstring.
        if (status.cycle or "none") == "none" and (status.phase or "") in ("charge", "stop", "idle", ""):
            return "None"
        code = status.error or 0
        if code == 0:
            return "None"
        return f"Error {code}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._mission_status
        if status is None:
            return {}
        attrs: dict[str, Any] = {
            "error_code": status.error,
            "not_ready": status.not_ready,
            "cond_not_ready": status.cond_not_ready,
        }
        # The Classic reading, offered and labelled as unconfirmed. It
        # is probably right more often than not; it is simply not
        # established for this firmware generation, and one field
        # capture contradicts it. Naming the attribute for its own
        # uncertainty means nobody builds an automation on it by
        # accident.
        guess = ERROR_CODE_LABELS.get(status.error or 0)
        if guess:
            attrs["classic_label_unconfirmed"] = guess

        # SEVERITY IS VENDOR DATA, unlike the label above.
        #
        # This sensor shows a raw number on purpose: iRobot gives Prime
        # and Classic different help articles for the same code, so no
        # text of ours would be sourced. A severity bucket is not a
        # label -- it is iRobot's own classification, from the app's
        # `error_allowed_modes` config, and it answers the one question
        # a bare number cannot: is this serious.
        #
        # `partially_operable` is the practical half. 144 of 171 codes
        # allow nothing; the ones that do are the robot's own "I can
        # still work around this" -- 671 (pad wash blocked) reads 5,
        # which fits the dock's own "switched to vacuum only".
        #
        # The bitmask is passed through undecoded. Bin-full reads 3 and
        # pad-wash-blocked reads 5, which rules out the obvious
        # vacuum/mop reading, and inventing a bit layout to print a
        # prettier attribute is how this project has been wrong before.
        severity = PRIME_ERROR_SEVERITY.get(status.error or 0)
        if severity is not None:
            bucket, allowed_modes = severity
            attrs["severity"] = bucket
            attrs["allowed_modes"] = allowed_modes
            attrs["partially_operable"] = allowed_modes != 0

        # THE VENDOR'S OWN TEXT, AS ATTRIBUTES.
        #
        # The state stays a raw code by this sensor's own argument
        # above -- but that argument predates `vendor_errors.py`, whose
        # catalogue was extracted from the **Prime** app's locale files.
        # "No text of ours would be sourced" stopped being true the
        # moment iRobot's arrived.
        #
        # For error 48 it reads "An obstacle blocked the entrance to a
        # room", which on one field robot explained 93 timeline error
        # events and every incomplete mission in its archive
        # (@utkjmitch, who also wrote this patch).
        #
        # Attributes rather than state, so nothing built on the code
        # breaks and the ENUM question never arises.
        # Attributes are readable before the entity is added to hass --
        # the tests construct it bare -- so fall back to English rather
        # than crash. Resolved unconditionally because the blocking
        # block below needs it too, and that one fires on a readiness
        # refusal where `error` is 0.
        language = (
            getattr(getattr(self, "hass", None), "config", None)
            and self.hass.config.language
        ) or "en"
        if status.error:
            entry = get_localized_error_entry(int(status.error), language)
            if entry.get("label"):
                attrs["error_title"] = entry["label"]
            if entry.get("description"):
                attrs["error_description"] = entry["description"]

        # WHAT THE ROBOT WOULD STILL ACCEPT.
        #
        # Four codes stop a mission before it starts, and the app checks
        # exactly those four (`blockFault`, app 3.0.0). Three of them do
        # not mean "broken" -- they mean "one half of what you asked is
        # impossible right now":
        #
        #   287  pad plate fitted    -> mop works, vacuum does not
        #   290  pad plate missing   -> vacuum works, mop does not
        #   234  no cloth on the plate -> vacuum works, mop does not
        #   286  robot off the floor -> neither
        #
        # Until now the integration held all four texts and drew no
        # conclusion from any of them. A user sending a vacuum command
        # against 287 got a command that left, was refused, and produced
        # no explanation an automation could read.
        #
        # BOTH SOURCES ARE CONSULTED, because a readiness refusal does
        # not set `error`. `cond_not_ready` carries the reasons in that
        # case -- documented in this class's own docstring and never
        # used for anything.
        # NOTHING HERE BLOCKS ANYTHING, and that is deliberate.
        #
        # This reports; it does not gate. The app takes the other route
        # and greys out every dock control once a task begins -- so a
        # drying cycle it started cannot be stopped there, which
        # @chairstacker calls the big drawback of the new UI. Copying
        # that would remove capabilities that demonstrably work.
        #
        # So a vacuum command against 287 still goes out. What changes
        # is that the robot's refusal is now explainable in advance,
        # in words and in a form an automation can branch on.
        blocking = _blocking_faults(status)
        if blocking:
            available = set().union(*blocking.values())
            attrs["blocked_modes"] = sorted({"vacuum", "mop"} - available)
            attrs["available_modes"] = sorted(available)
            attrs["blocking_faults"] = sorted(blocking)

            # THE MESSAGE, and it needs its own lookup.
            #
            # `error_title` above only fills when `status.error` is set.
            # A readiness REFUSAL leaves error at 0 and puts its codes in
            # `cond_not_ready` -- which is exactly the case this block
            # exists for, and it would have produced mode lists with no
            # words beside them.
            texts = [
                t
                for t in (
                    get_localized_error_entry(code, language).get("label")
                    for code in sorted(blocking)
                )
                if t
            ]
            if texts:
                attrs["blocked_reason"] = " · ".join(texts)

        # WHICH TASK THE FAULT HAPPENED DURING.
        #
        # The same code means different things per running task, which
        # is why the app resolves a fault against a SCENE rather than
        # showing one text per code. A stall during `padWash` is a dock
        # problem; the same stall during `cleanTask` is a robot problem.
        #
        # The robot never sends a scene -- `getFaultScene({cmStatus,
        # command})` computes it, and roombapy-prime carries the five
        # rules that are fully specified. The other seven scenes have no
        # stated condition, so `scene_for()` returns None rather than
        # falling back to the documented default: a plausible wrong task
        # name on a real error message is worse than none.
        if status.error:
            scene = FaultScene.scene_for(
                command=str(
                    prime_last_command(self._config_entry.runtime_data).get("command")
                    or ""
                )
                or None,
                cycle=getattr(status, "cycle", None),
                phase=getattr(status, "phase", None),
            )
            if scene is not None:
                attrs["fault_scene"] = scene.name.lower()
        return attrs


#: Part id -> (display name, unit, whether the raw value is minutes).
#:
#: The API identifies consumables by NUMBER, not by name -- a sensor
#: called "Consumable - 67" is accurate and useless. Both @DaRealGuGu
#: and @chairstacker independently matched their numbers against the
#: iRobot app and arrived at the same list, which is why these names
#: are here rather than guessed.
#:
#: THE MINUTES FLAG IS THE IMPORTANT PART. For the three time-based
#: parts the API reports MINUTES while the app displays HOURS:
#: 5100 -> 85 h, 17580 -> 293 h, 1980 -> 33 h. All three divide evenly
#: by 60 on two separate accounts, which is about as clear as this
#: evidence gets. Showing 5100 unitless next to an app saying "85
#: heures restantes" is not a naming problem, it is a wrong number.
#:
#: 202 and 212 are deliberately absent. Neither tester could find them
#: in the app (values seen: 0 and 165, and 268 elsewhere). Naming them
#: on a guess would be worse than leaving them numeric -- a wrong label
#: gets believed, a number invites a question.
#:
#: APK ANALYSIS CANNOT SUPPLY THEM, and that is now established rather
#: than assumed. The app has no compiled id->name table at all: it
#: fetches a parts CATALOGUE from the server (`MaintenancePartV2`, with
#: partId, partName, cleanInterval, replaceInterval, guideUrl, buyUrl)
#: and joins it to the counter data for display.
#:
#: So the names for 67 and 71 could never have come from the code
#: either -- they came from screenshots, and that is the only route this
#: project has used.
#:
#: THE PROPER FIX is that catalogue endpoint. `/v1/robots/{blid}/parts`
#: returns counters only; RobotPart has part_id, count_type, counters
#: and no name field. Whatever serves MaintenancePartV2 is a different
#: call and is not identified.
#:
#: A PLAUSIBLE READING EXISTS AND IS NOT USED: 202 carries
#: `count_type: pad_washes_used` with category `maintenance`, 212 the
#: same counter with category `replacement`, and the app has UI strings
#: for a pad washing roller ("Remove pad washing roller", "Clean the pad
#: washing basin"). Clean-it versus replace-it on one roller fits
#: perfectly. It is still a deduction from two hints, and a maintenance
#: label that is wrong sends someone to replace the wrong part.
#:
#: THE NUMBER-MATCHING ROUTE HAS BEEN TRIED AND FAILED HERE. It named
#: part 213 outright, but @DaRealGuGu reports no matching values in his
#: app at all for 202 and 212 -- so they are not in the maintenance list
#: the app shows.
#:
#: THAT ABSENCE IS ITSELF EVIDENCE, and it points the same way as
#: everything else:
#:
#:   - the app's maintenance list covers ROBOT parts -- mop pad, cliff
#:     sensors, edge brush, multi-surface brush, filter
#:   - a pad washing roller sits in the DOCK, and the app's strings for
#:     it are dock instructions: "Remove pad washing roller", "Clean the
#:     pad washing basin"
#:   - both testers' docks report pad washing capability (pw=1)
#:
#: AND THE NUMBERS AGREE ACROSS TWO ACCOUNTS. Thresholds are identical:
#:
#:     202  used +  remaining =  50 washes   maintenance
#:     212  used +  remaining = 300 washes   replacement
#:
#: 35+15 on one robot, 208+(-158) and 208+92 on the other. One counter,
#: clean it every 50 washes, replace it every 300. That is exactly what
#: a washable roller needs.
#:
#: NAMED FOR WHAT IS PROVEN, NOT FOR THE PART.
#:
#: The physical component stays ambiguous, and the app's own strings are
#: why: it names TWO dock parts, a "pad washing roller" and a "pad
#: washing basin". One part with two thresholds and two parts with one
#: each fit the data equally well:
#:
#:     one part:  clean the roller every 50, replace it every 300
#:     two parts: clean the basin every 50, replace the roller every 300
#:
#: Calling 202 "pad washing roller cleaning" would send someone to scrub
#: the wrong component.
#:
#: WHAT IS NOT AMBIGUOUS is everything else: the counter is
#: `pad_washes_used` on both accounts, the thresholds are 50 and 300 on
#: both, and the categories are `maintenance` and `replacement`. So the
#: labels say pad washing, and say which action is due, and stop there.
#:
#: That is strictly better than a bare number -- "Pad washing, cleaning
#: due" tells a user something actionable -- and it claims nothing the
#: data does not carry.
#: Each known part gets its OWN translation key rather than being
#: substituted into a generic one. A placeholder cannot be translated:
#: "Consommable - Edge sweeping brush" is worse than plain English,
#: because it looks like a translation that failed halfway.
#: Part id -> translation key. NAMES ONLY -- the unit comes from the
#: server's own count_type, not from this table.
#:
#: An earlier version carried units and a "value is in minutes" flag
#: here, inferred by comparing sensor values against app screenshots.
#: A diagnostics download then showed count_type outright:
#:
#:     67, 71, 72  -> "minutes"          (app displays hours)
#:     147         -> "evacs"
#:     148         -> "combo_missions"
#:     202, 212    -> "pad_washes_used"
#:
#: The inference was right, and hardcoding it was still wrong: the
#: server states this per part, so a hardcoded table would silently
#: disagree the moment a robot reports something else.
#: THE PART ID SPACE IS PER-SKU, NOT UNIVERSAL.
#:
#: @utkjmitch's robot numbers the same three parts **149 / 69 / 68**
#: where the ids already in this table were **148 / 71 / 72**. Same
#: part, different number, on hardware from the same generation --
#: which is why both sets appear below rather than one replacing the
#: other.
#:
#: He named his by reading the iRobot app's robot-health screen beside
#: the ids: "Washable mop pad, 8 routines" against 149, "Multi-surface
#: brush, 179 hrs" against 69. 68 is by elimination -- the only
#: remaining part on a robot the app warned needed a new filter -- and
#: he flagged it as the weakest of the three himself.
#:
#: So an unrecognised id is expected rather than exceptional, and the
#: fallback that shows the bare number is doing real work. A part
#: labelled with an invented name would be worse than one labelled
#: with a number the owner can quote at iRobot support.
_KNOWN_PARTS: dict[str, str] = {
    "67": "prime_part_edge_brush",
    "71": "prime_part_multi_surface_brush",
    "72": "prime_part_filter",
    "147": "prime_part_dirt_bag",
    "148": "prime_part_mop_pads",
    # 69, 149 AND 68 -- @utkjmitch's robot, by @arielgr's value-match
    # method against the app's "robot health" screen:
    #
    #     Washable mop pad      8 routines   -> 149
    #     Multi-surface brush   179 hrs      -> 69
    #     Dirt disposal bag     60 evacs     -> 147   (already named)
    #
    # plus two warnings carrying no numbers -- "replace the edge
    # sweeping brush" and "replace the filter" -- against the only two
    # parts that robot reports as due: 67, already named, and 68.
    #
    # THE ID SPACE IS NOT UNIVERSAL. This robot numbers the same three
    # parts 149 / 69 / 68 where the entries above have 148 / 71 / 72 --
    # no more shared across models than the rw-settings key set turned
    # out to be. So these are additions, not corrections.
    "69": "prime_part_multi_surface_brush",
    "149": "prime_part_mop_pads",
    # 68 IS THE WEAKEST OF THE THREE: named by elimination rather than
    # by a value match, because the app's filter warning carries no
    # number to line up against. A second robot reporting 68 with a
    # readable count would settle it.
    "68": "prime_part_filter",
    # 213 CONFIRMED BY VALUE MATCH (@arielgr, Roomba 115).
    #
    # He put the app's maintenance list beside Home Assistant's and the
    # numbers lined up in order:
    #
    #     Washable Mop Pad      14 routines   = part 148
    #     Cliff Sensors         19 routines   = part 213  <- this one
    #     Edge Sweeping Brush   92 hr         = part 67
    #     Multi-Surface Brush   300 hr        = part 71
    #
    # Four values agreeing in sequence is not coincidence. This is the
    # method that works for unnamed part ids, and it needs nothing but
    # two screenshots -- worth remembering for 202 and 212, which are
    # still unnamed because nobody has found them in the app at all.
    "213": "prime_part_cliff_sensors",
    # See the block above: named for the counter and the action, not for
    # the physical part, because the app names two candidate dock parts
    # and nothing distinguishes them.
    "202": "prime_part_pad_wash_cleaning",
    "212": "prime_part_pad_wash_replacement",
    # 202 and 212 both report count_type "pad_washes_used" and differ
    # only by category (maintenance vs replacement). Two testers saw
    # them and neither could find either in the app, so they stay
    # numeric -- a made-up label gets believed, a bare number invites a
    # question.
    #
    # APP 3.0.0 NAMES PARTS INSTEAD OF NUMBERING THEM.
    #
    # Where 2.2.4 used the numeric ids above, 3.0.0 uses speaking
    # `part_id` values: main_brush, side_brush, filter, bag, battery,
    # pad, sensor, dock_washing_system, mop_washing_system.
    #
    # A server that starts sending those would fall straight through
    # this lookup to `return part_id`, and the list would read "Replace
    # main_brush" -- exactly the bug `_readable_part_name`'s own
    # docstring says it exists to avoid. Mapped here so both vocabularies
    # resolve to the same translation.
    #
    # THE PAIRINGS ARE BY MEANING, and each is unambiguous:
    # side_brush is the edge sweeping brush (67), main_brush the
    # multi-surface brush (71), bag the dirt disposal bag (147), pad the
    # mop pads (148), sensor the cliff sensors (213).
    "main_brush": "prime_part_multi_surface_brush",
    "side_brush": "prime_part_edge_brush",
    "filter": "prime_part_filter",
    "bag": "prime_part_dirt_bag",
    "pad": "prime_part_mop_pads",
    "sensor": "prime_part_cliff_sensors",
    # NOT MAPPED TO 202/212, THOUGH THE TEMPTATION IS OBVIOUS.
    #
    # 3.0.0 has exactly two washing-system parts, and this integration
    # has exactly two unnamed pad-wash counters. That is suggestive and
    # it is not evidence: nothing says which of 202/212 is the dock's
    # system and which is the mop's, and the pairing would be a coin
    # flip printed as a fact.
    #
    # They get their own keys instead, so a robot reporting the named
    # form is readable without deciding the numeric question.
    #
    # THESE THREE HAVE NO TRANSLATION ENTRY and do not need one:
    # `_readable_part_name` falls back to the key with its prefix
    # stripped and title-cased, giving "Dock Washing System" and "Mop
    # Washing System" -- already the words iRobot uses. Adding eight
    # locale entries to restate that would be churn.
    "dock_washing_system": "prime_part_dock_washing_system",
    "mop_washing_system": "prime_part_mop_washing_system",
    # `battery` has no numeric counterpart here at all -- no capture has
    # shown a battery part in the maintenance list.
    "battery": "prime_part_battery",
}


#: Maps the server's own count_type to a display unit.
#:
#: Values taken from a real capture (chairstacker's app screenshot plus
#: the endpoint's own response): "hr" for the filter and both brushes,
#: routines for mop pads, evacuations for the dirt disposal bag.
#:
#: Anything unrecognised falls through to no unit rather than being
#: forced into hours -- a wrong unit on a number is worse than none,
#: because it invites arithmetic that does not hold.
_PART_COUNT_UNITS: dict[str, str | None] = {
    "hr": UnitOfTime.HOURS,
    "hours": UnitOfTime.HOURS,
    "routines": "routines",
    "evacs": "evacuations",
    "evacuations": "evacuations",
    "missions": "missions",
    # From a real diagnostics download (DaRealGuGu, a11):
    "minutes": UnitOfTime.HOURS,   # converted in native_value
    "combo_missions": "routines",
    "pad_washes_used": "pad washes",
    # THE LAST TWO OF THE SEVEN, from the app's own RobotHealthCountType
    # enum -- neither has appeared in any catalogue response yet.
    #
    # Added anyway, because the cost of being ready is one line each and
    # the cost of not being is a sensor that shows a bare number when a
    # robot finally reports one. The enum is complete: Minutes,
    # Missions, ComboMissions, Evacs, Battery, PadWashesUsed, Sqft --
    # so this table now covers every type the app knows.
    "battery": "charge cycles",
    "sqft": "ft²",
    # BOTH SPELLINGS OF ONE VALUE, and it is the vendor's inconsistency
    # again. `asset_health_enum` in app 3.0.0 lists `padWashesUsed` AND
    # `pad_washes_used` side by side -- only the snake_case form was
    # here, so a robot reporting the camelCase one showed a bare number.
    #
    # Exactly the shape of the `reusablewet`/`reusableWet` bug in
    # ZONE_TYPE_ICONS' neighbour table, found the same way: by reading
    # the vendor's own list rather than the one capture we happened to
    # have.
    "padWashesUsed": "pad washes",
}


def part_count_in_display_unit(part: Any, count: int | None) -> int | None:
    """A part count expressed in the unit the label above names.

    THE TABLE RENAMES A UNIT AND THE VALUE HAS TO FOLLOW. `minutes` is
    displayed as hours, so a count in minutes has to be divided before
    it is shown beside that word. Every other count type is displayed in
    the unit the robot already counts in and passes through untouched.

    Shared because it was not. The sensor divided; the maintenance list
    looked up the same table for the same part and printed the raw
    number beside the renamed unit, so one robot showed `179 h` on
    `sensor.*_prime_part_69` and "10740 hours remaining" on its
    maintenance list at the same moment -- the same value, one of them
    sixty times too large (@utkjmitch, part 69, confirmed against the
    iRobot app's "Multi-surface brush 179 hrs").

    A number and the word it is labelled with cannot drift apart if
    both come from here.
    """
    if count is None:
        return None
    if (getattr(part, "count_type", "") or "").lower() == "minutes":
        # Minutes on the wire, hours in the app -- 5100 -> 85 h,
        # confirmed on two accounts and then by count_type itself.
        return round(count / 60)
    return count


class PrimeConsumablePartSensor(IRobotEntity, SensorEntity):
    """One V4/Prime consumable: filter, a brush, mop pads, dirt bag.

    Created dynamically per part the robot actually reports, rather
    than from a fixed list. The set differs by model -- a vacuum-only
    robot has no mop pads, a robot without a self-emptying base has no
    dirt bag -- and hard-coding it would either invent entities nobody
    has or miss ones on hardware nobody here owns.

    DIFFERENT IN KIND FROM THE CLASSIC MAINTENANCE SENSORS. Those
    compute wear themselves in maintenance_store.py, because a Classic
    robot reports nothing about it -- including learning the owner's
    real replacement interval after a couple of resets, and taking a
    user-configured threshold. None of that applies here: the cloud
    simply states the remaining count, so this sensor reports it and
    does no arithmetic.

    Which is also why the threshold options in this integration's
    config flow must not be offered for Prime robots. They would change
    nothing.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, blid: str, part_id: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._part_id = part_id
        self.entity_description = SensorEntityDescription(
            key=f"prime_part_{part_id}",
            translation_key="prime_consumable_part",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        known = _KNOWN_PARTS.get(part_id)
        if known:
            self.entity_description = SensorEntityDescription(
                key=f"prime_part_{part_id}",
                translation_key=known,
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        else:
            # Unknown part: keep the number visible. 202 and 212 have no
            # confirmed name, and a made-up label gets believed while a
            # bare number invites someone to check their own app.
            self._attr_translation_placeholders = {"part": part_id}
        self._attr_unique_id = f"{self.robot_unique_id}_prime_part_{part_id}"

    @property
    def suggested_object_id(self) -> str:
        return f"prime_part_{self._part_id}"

    @property
    def _part(self) -> Any:
        coordinator = getattr(self._config_entry.runtime_data, "prime_parts_coordinator", None)
        if coordinator is None or not coordinator.data:
            return None
        return coordinator.data.get(self._part_id)

    @property
    def available(self) -> bool:
        return super().available and self._part is not None

    @property
    def native_value(self) -> int | None:
        part = self._part
        if part is None:
            return None
        return part_count_in_display_unit(part, part.count_remaining)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Taken from the server per part, not fixed.

        The same robot reports hours for its filter and routines for
        its mop pads. A single hard-coded unit would be wrong for most
        of them.
        """
        part = self._part
        if part is None:
            return None
        return _PART_COUNT_UNITS.get((part.count_type or "").lower())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        part = self._part
        if part is None:
            return {}
        return {
            "part_id": part.part_id,
            "count_type": part.count_type,
            "count_used": part.count_used,
            "minutes_remaining": part.minutes_remaining,
            # Kept visible for unknown parts: 202 and 212 have no name
            # yet, and the raw value is the only thing that lets someone
            # match them against their own app.
            "raw_count_remaining": part.count_remaining,
            "category": part.counter_category,
        }


class PrimePhaseSensor(_PrimeCurrentStateSensorBase):
    """What the robot is doing right now.

    THE MOST BASIC STATE SENSOR THERE IS, and Prime did not have it.
    Classic has had `phase` since the beginning; the Prime branch builds
    its own shorter list and this was never on it.

    Somebody wanting an automation on "the robot is cleaning" goes
    looking for this, does not find it, and concludes they have missed
    something. Found by comparing every Classic `value_fn` against a
    real Prime shadow rather than by anyone reporting it -- which is
    itself the point: a missing sensor generates no error and no
    complaint, only a person quietly giving up.

    RAW, NOT TRANSLATED. `charge`, `run`, `stuck`, `evac`, `padWash` and
    the rest are the robot's own words and are what every existing
    automation and template in this ecosystem matches on. Prettifying
    them here would break templates people copied from Classic
    documentation.
    """

    entity_description = SensorEntityDescription(
        key="prime_phase",
        translation_key="phase",
        device_class=SensorDeviceClass.ENUM,
    )

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_phase"

    #: How long a `run` must have been running before a rising battery
    #: may read as `stale`. Ten minutes is noise against the 61-hour
    #: case this exists for, and far longer than any observed charge
    #: tail.
    _STALE_GRACE_SEC: Final = 600

    def _battery_rising(self) -> bool:
        """Whether the battery has climbed since the last reading.

        Best-effort and deliberately conservative: it takes two samples
        and a real increase, so a robot that briefly reports a higher
        percentage mid-mission does not get called stale on one blip.
        """
        state = self._current_state
        level = getattr(state, "bat_pct", None) if state else None
        if not isinstance(level, (int, float)):
            return False
        previous = getattr(self, "_last_battery", None)
        self._last_battery = level
        return isinstance(previous, (int, float)) and level > previous

    @property
    def options(self) -> list[str]:
        """Every phase confirmed in a field capture, plus the ones the
        mission-status model names.

        An ENUM sensor must declare its options or Home Assistant logs a
        warning for each unlisted value it sees. A phase outside this
        list reports as unknown rather than breaking the entity -- see
        `native_value`.
        """
        # THE VENDOR'S OWN `Phase` ENUM, app 3.0.0, fifteen values.
        #
        # A first version of this list was assembled from field captures
        # and guessed at two -- `pause` and `new` -- which are not in the
        # enum at all, and missed five that are: `stop`, `hmUsrChrg`,
        # `chargingError`, `mapUpd` and `refill`.
        #
        # A phase outside `options` makes Home Assistant reject the value
        # and the entity goes unavailable, so a missing one is not a
        # cosmetic gap: a robot returning to charge mid-mission would
        # have taken the sensor down.
        #
        # The two guessed values are kept. They cost nothing if the robot
        # never sends them, and removing them on the strength of one
        # app version would be trading a confirmed list for a complete
        # one -- Classic robots reach this code too.
        return [
            "stop", "charge", "run", "stuck",
            "hmPostMsn", "hmMidMsn", "hmUsrDock", "hmUsrChrg",
            "chargingError", "mapUpd", "evac", "refill",
            "padWash", "padDry",
            # Not in the 3.0.0 enum; seen in older captures.
            "pause", "new",
            # Not a robot phase at all: this integration's own reading
            # that the document has stopped tracking reality. See
            # `native_value`.
            "stale",
        ]

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        status = None if state is None else state.clean_mission_status
        phase = getattr(status, "phase", None)
        if phase is None:
            return None
        text = str(phase)

        # A ROBOT WHOSE BATTERY IS RISING IS NOT CLEANING.
        #
        # @utkjmitch's Y351020 errored mid-mission on a Saturday and the
        # shadow froze at `{phase: "run", error: 48}` for **61 hours**.
        # The robot kept updating `batPct` — 75 up to 96, so it was alive
        # and talking — and never wrote a terminal phase.
        #
        # What that cost: two daily schedules silently skipped, the
        # vacuum entity showing "cleaning" for two and a half days, and
        # **every cloud command swallowed** — stop, start, dock and find,
        # each broker-confirmed, none with any effect. iRobot's own app
        # was equally fooled and could not end its own phantom mission.
        # Only a power cycle cleared it.
        #
        # This does not unfreeze anything; nothing here can. It stops
        # this sensor from repeating the lie, which is what a caller
        # builds automations on. The vacuum entity and the app keep
        # their own view.
        #
        # THE TEST IS THE ONE HE PROPOSED: charging and running are
        # mutually exclusive, and the robot itself supplies both numbers.
        #
        # AND THIS READING IS ACTIONABLE, not just informative. In that
        # state every command is swallowed -- start, stop, dock and find,
        # each broker-confirmed, none with any effect. So `stale` is the
        # answer to "why did my automation's start do nothing", and the
        # answer is a power cycle rather than a bug report.
        # BUT NOT IN THE FIRST MINUTES OF A RUN.
        #
        # A recharge-and-resume enters `run` while the battery reports
        # are still climbing from the charge -- genuinely running AND
        # genuinely rising, the one case where the mutual-exclusion test
        # lies. Field-captured on this detector's first morning
        # (@utkjmitch, Y351020): `charge` -> `run` at 37%, next reports
        # 37 -> 40, and the sensor read `stale` twice before settling.
        #
        # The state this names is cured by a power cycle, so a false
        # positive pages somebody for a healthy robot. The real freeze
        # rises for HOURS -- waiting out the first ten minutes of a run
        # costs detection nothing.
        now = time.monotonic()
        if text == "run" and getattr(self, "_last_phase", None) != "run":
            self._run_since = now
        self._last_phase = text
        if text == "run":
            # ASKED EVERY TIME, ACTED ON ONLY AFTER THE GRACE WINDOW.
            #
            # `_battery_rising()` keeps its own previous reading, so
            # skipping it during the grace period would leave the
            # history empty and the first post-grace reading would have
            # nothing to compare against.
            rising = self._battery_rising()
            run_since = getattr(self, "_run_since", None)
            grace_over = (
                run_since is not None
                and (now - run_since) >= self._STALE_GRACE_SEC
            )
            if rising and grace_over:
                return "stale"

        # AN UNLISTED PHASE READS AS UNKNOWN, not as itself. Home
        # Assistant rejects an ENUM value outside `options`, and the
        # entity would go unavailable rather than show one odd word --
        # losing the ninety-nine phases that do work to display the one
        # that does not.
        return text if text in self.options else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which of the three phase categories this one is in.

        THE THIRD CATEGORY IS WHY THIS EXISTS. `CLEANING_PHASES` and
        `MISSION_END_PHASES` between them leave `padWash`, `padDry` and
        `refill` in neither -- and the vendor's `isCleanDockTask` names
        exactly those three as a DOCK task, a state that is neither
        cleaning nor an ending.

        A combo robot with `pwReturn: 2` washes its pad mid-mission, so
        an automation gating on "is it cleaning" goes false while the
        robot is plainly working. `dock_task` says why, without changing
        what the phase sets mean.
        """
        state = self._current_state
        status = None if state is None else state.clean_mission_status
        phase = getattr(status, "phase", None)
        if phase is None:
            return {}
        return {
            "dock_task": str(phase) in DOCK_TASK_PHASES,
            "cleaning": str(phase) in CLEANING_PHASES,
        }


class PrimeReadinessSensor(_PrimeCurrentStateSensorBase):
    """Why the robot will not start, or that it will.

    `notReady` is a code, not a flag: zero means ready, and each other
    value names a specific obstacle -- bin full, tank empty, pad
    missing. Classic renders those through READINESS_STATE_LABELS, and
    the same table applies here because the codes come from the same
    mission-status structure.

    WHY THIS MATTERS MORE ON PRIME than the label suggests: a Prime
    robot refuses a start silently. The command is accepted, nothing
    happens, and this sensor is the only place that says why.
    """

    entity_description = SensorEntityDescription(
        key="prime_readiness",
        translation_key="readiness",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_prime_readiness"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        status = None if state is None else state.clean_mission_status
        code = getattr(status, "not_ready", None)
        if code is None:
            return None
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            return None
        # THE CODE WHEN THERE IS NO LABEL, rather than nothing. An
        # unmapped reason a user can quote in a report is worth more
        # than a blank sensor -- @connormxy's error 236 is exactly the
        # case, and it went unnamed because nothing displayed it.
        return READINESS_STATE_LABELS.get(code_int) or f"Unknown ({code_int})"


class PrimeJobInitiatorSensor(_PrimeCurrentStateSensorBase):
    """Who started the current or most recent mission.

    PARITY WITH CLASSIC, AND DELIBERATELY THE SAME FIELD. Classic's
    "Started by" reads `cleanMissionStatus.initiator`, and so does this.
    Reading a different field under the same name would give two
    generations two meanings for one sensor, which is the shape that
    made `Quiet hours` and `Quiet hours active` unreadable until they
    were renamed.

    THERE IS A SECOND INITIATOR AND IT IS NOT THIS ONE.
    `rw-software.lastCommand` carries its own, and @chairstacker's robot
    shows the two disagreeing at the same moment:

        cleanMissionStatus.initiator   "cloud"    started the mission
        lastCommand.initiator          "rmtApp"   sent `stoppaddry`

    Different questions, both real. The last command is exposed as
    attributes here rather than as a second entity: "Started by" and
    "Last command by" side by side in a list would be two names one
    letter apart in meaning, and a user comparing them would be right to
    be confused about which one answers "why is it running".

    THE LABEL TABLE IS SHARED WITH CLASSIC. It knew six values and fell
    through to "None" for the rest -- the same answer as "no information
    at all" -- until the vendor's own enum was read against it. All 25
    are named now, and a test asserts the table covers the enum.
    """

    _attr_has_entity_name = True

    entity_description = SensorEntityDescription(
        key="job_initiator",
        translation_key="job_initiator",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:account-question",
    )

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_job_initiator"

    @property
    def native_value(self) -> str | None:
        state = self._current_state
        if state is None:
            return None
        status = state.clean_mission_status
        raw = getattr(status, "initiator", None)
        if not raw:
            return None
        return JOB_INITIATOR_LABELS.get(str(raw), str(raw))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The last command, which is a different question.

        Raw initiator alongside the label: an automation branching on
        this should not have to reverse a translation, and the label
        table is for reading rather than matching.
        """
        state = self._current_state
        attrs: dict[str, Any] = {}
        if state is not None:
            raw = getattr(state.clean_mission_status, "initiator", None)
            if raw:
                attrs["initiator"] = str(raw)

        last = prime_last_command(self._config_entry.runtime_data)
        if last:
            command = last.get("command")
            initiator = last.get("initiator")
            if command:
                attrs["last_command"] = str(command)
            if initiator:
                attrs["last_command_by"] = JOB_INITIATOR_LABELS.get(
                    str(initiator), str(initiator)
                )
                attrs["last_command_initiator"] = str(initiator)
            if last.get("time"):
                attrs["last_command_time"] = last["time"]
        return attrs


class PrimeRegionLastCleanedSensor(IRobotEntity, SensorEntity):
    """When one room or zone was last cleaned. One entity per region.

    @chairstacker asked for exactly this: "a date/time entity that
    stores this value for each room and cleaning zone automatically and
    I don't have to build helpers for each room/cleaning zone". It fits
    how he uses the integration -- HA observes and reacts, the iRobot
    app stays in charge of running the robot.

    KEYED BY MAP AND REGION, NOT BY NAME. A name-keyed entity loses its
    history the moment somebody renames a room in the app, and a region
    id alone is not unique across maps -- @dduff617's four-map account
    has ids that repeat, and merging them would attribute one floor's
    clean to another floor's room.

    The state is the completion time of the most recent mission that
    finished this region, read from the mission store's own history.
    Nothing is stored here that is not already stored there.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        pmap_id: str,
        region_id: str,
        region_name: str,
    ) -> None:
        super().__init__(None, blid, config_entry)
        self._config_entry = config_entry
        self._pmap_id = pmap_id
        self._region_id = region_id
        # A TRANSLATED NAME, NOT AN ENGLISH SUFFIX. The room name comes
        # from the user's own iRobot account, so it is already in their
        # language; hard-coding "last cleaned" beside it would produce
        # "Küche last cleaned" for every non-English user. The
        # placeholder keeps the room name dynamic while the wording
        # around it follows the locale, same as the favourite buttons.
        self._attr_translation_key = "region_last_cleaned"
        self._attr_translation_placeholders = {"region": region_name}
        self._attr_unique_id = (
            f"{self.robot_unique_id}_last_cleaned_{pmap_id}_{region_id}"
        )

    @property
    def native_value(self) -> datetime | None:
        store = getattr(self._config_entry.runtime_data, "mission_store", None)
        if store is None:
            return None
        history = store.region_last_cleaned()
        # Qualified form first; the bare id is the fallback for records
        # that carry no map (older entries, EPHEMERAL tier).
        raw = history.get(f"{self._pmap_id}/{self._region_id}") or history.get(
            self._region_id
        )
        if not raw:
            return None
        return dt_util.parse_datetime(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The map this region is on, so two rooms sharing a name on
        different floors can be told apart in the UI."""
        return {"region_id": self._region_id, "pmap_id": self._pmap_id}

    async def async_added_to_hass(self) -> None:
        """Follow the coordinator that drives the history sync.

        WITHOUT THIS THE SENSOR READS ONCE AND NEVER AGAIN. It has no
        coordinator of its own -- it reads the mission store, which
        `prime_mission_sync` rewrites, and that sync is scheduled by
        the status coordinator (`_schedule_mission_history_sync`).
        Listening there is what turns a start-up snapshot into a value
        that moves when a mission finishes.

        Every other Prime sensor subscribes the same way; one that
        does not looks identical in the UI right up until the state
        should have changed.
        """
        await super().async_added_to_hass()
        coordinator = (
            self._config_entry.runtime_data.prime_status_coordinator
            if self._config_entry is not None else None
        )
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self.schedule_update_ha_state)
            )
