"""V4/Prime cloud coordinator for Roomba+.

Native async push coordinator for V4/Prime (cloud-only, ConnectionType.
CLOUD_ONLY) robots -- see ROOMBA_PLUS_VERSION_PLAN_v4_onwards.md for the
full architecture context and the reasoning behind this design (in
particular, why this is NOT the same shape as IrobotCloudCoordinator's
polling model, and why an earlier "make PrimeRobot look like a classic
Roomba object" design was considered and rejected).

Unlike IrobotCloudCoordinator (cloud_coordinator.py), this is NOT a
polling coordinator: there is no update_interval, no
_async_update_data(). Data arrives via roombapy-prime's
PrimeRobot.watch_mission_timeline() -- an AsyncIterator that yields on
every real mission-timeline event, reconnecting transparently across
connection drops with its own exponential backoff (roombapy-prime
v0.1.11a3+). This coordinator only needs to consume it and forward each
report via async_set_updated_data() -- it does not need its own
reconnect/retry logic layered on top.

UPDATE (roombapy-prime v0.1.11a5): this coordinator used to consume
watch_state() (the classic shadow delta channel) instead -- switched
after a live capture (chairstacker) proved the shadow's reported state
is byte-identical whether the robot is idle or actively cleaning. Live
mission status genuinely does not flow through that channel at all;
mission/timeline/report is the confirmed source instead. coordinator.data
is now a parsed MissionTimelineReport (roombapy_prime.models), not a raw
dict -- the event schema is confirmed live (see MissionTimelineReport's
own docstring), so there's no reason to keep it untranslated the way
the old shadow-delta placeholder was.

STILL NOT A COMPLETE STATE FACADE ON ITS OWN: mission/timeline/report
gives room/mission progress (which room, area, timestamps), not
battery level or docking boolean state -- see vacuum.py's activity
property for how the confirmed event types are used to approximate a
VacuumActivity, and its own caveats about which mappings are
live-confirmed vs. only historically-confirmed. Battery/dock/bin/tank
status IS confirmed and modeled now, from a DIFFERENT named-shadow
source -- see PrimeStatusCoordinator below, a separate coordinator for
exactly that data, not folded into this one.
"""
from __future__ import annotations

from datetime import timedelta

import time as _time

import asyncio
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from roombapy_prime import (
    PrimeRobot,
    ShadowConnectionError,
    ShadowError,
    ShadowSSLError,
)
from roombapy_prime.models import MissionTimelineReport

_LOGGER = logging.getLogger(__name__)


class PrimeCoordinator(DataUpdateCoordinator[MissionTimelineReport]):
    """Push coordinator for a single V4/Prime robot.

    coordinator.data: the most recently received MissionTimelineReport
    (roombapy_prime.models) -- room/mission progress, confirmed live
    (see that class's own docstring for the full evidence trail). NOT
    battery level or docking state -- see module docstring.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        blid: str,
        prime_robot: PrimeRobot,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"iRobot Prime ({blid})",
            config_entry=config_entry,
            update_interval=None,  # push, not polled -- see module docstring
        )
        self.blid = blid
        self.prime_robot = prime_robot
        #: Which mission the collected trail positions belong to. A new
        #: id clears them; the same id surviving a pause does not.
        self._trail_mission_id: str | None = None

    async def async_start(self) -> None:
        """Connects and starts the background watch_mission_timeline() consumer.

        Call once during entry setup, before the entry is considered
        ready. Mirrors async_config_entry_first_refresh()'s role for a
        poll-based coordinator, but there is no "first refresh" in the
        polling sense for a push source -- this only confirms the MQTT
        connection itself succeeds.

        IMPORTANT: login (where credential failures would surface, e.g.
        AuthCredentialsError) already happened earlier, via
        PrimeFactory.create_prime_robot() -- that's a separate phase
        (not yet built; see the version plan's Implementierungs-
        Checkliste, "_phase_connect_prime()") that constructs the
        PrimeRobot this coordinator receives already logged in.
        connect() here only opens the MQTT connection with an
        already-valid token, so the only failures possible at this
        point are connection-level (ShadowSSLError, ShadowConnectionError,
        or plain ShadowError for "connect failed"/"connect timed out") --
        never a credentials problem. All of them map to
        ConfigEntryNotReady, HA's standard "try again later" signal;
        there is no ConfigEntryAuthFailed case at this specific step.
        """
        try:
            await self.prime_robot.connect()
        except (ShadowSSLError, ShadowConnectionError, ShadowError) as exc:
            raise ConfigEntryNotReady(
                f"Could not connect to V4/Prime robot {self.blid}: {exc}"
            ) from exc

        self.config_entry.async_create_background_task(
            self.hass,
            self._async_watch_mission_timeline(),
            name=f"roomba_plus_prime_watch_{self.blid}",
        )

    async def _async_watch_mission_timeline(self) -> None:
        """Runs for the lifetime of the config entry -- cancelled
        automatically by HA's own config-entry unload machinery
        (ConfigEntry._async_process_on_unload cancels every task
        started via async_create_background_task), not by anything in
        this class itself.

        watch_mission_timeline() itself reconnects transparently across
        connection drops, with its own exponential backoff (see
        roombapy-prime v0.1.11a3+) -- this loop only forwards whatever
        it yields; it does not itself need reconnect/retry logic for a
        plain connection drop.

        DEFENSIVE OUTER RETRY LOOP (added alongside the same fix in
        PrimeStatusCoordinator._async_watch_status_updates() -- a real
        field report, chairstacker, showed the vacuum entity's own
        activity stuck on "Cleaning" long after the robot had actually
        finished and returned to dock, at the same time and likely the
        same underlying cause as that other coordinator's own sensors
        going stale/Unknown. This method previously had the exact same
        gap: a single unexpected exception (even though
        watch_mission_timeline() is "designed to retry forever
        internally" per its own docstring) would end this task
        PERMANENTLY for the rest of the session, silently freezing
        activity/extra_state_attributes at whatever the last-received
        event happened to be -- exactly matching what was reported.
        Now degrades to "retry after a short delay" instead -- for
        BOTH an exception AND the generator simply ending on its own
        (also anomalous: watch_mission_timeline() is meant to run
        forever, so either case gets the same backoff-and-retry
        treatment, never an immediate, undelayed re-call)."""
        backoff = 5.0
        while True:
            try:
                async for delta in self.prime_robot.watch_mission_timeline():
                    # Feeds the same freshness signal Classic robots use.
                    #
                    # GAP FOUND IN THE FIELD (DaRealGuGu): Prime never
                    # touched last_mqtt_message_ts at all -- it is written
                    # only from callbacks.py, which is a Classic-only code
                    # path. Every staleness check built on it therefore
                    # sat permanently silent for Prime robots.
                    #
                    # His symptom is exactly what that allows: the mission
                    # event sensor froze on "fin" and the dock status
                    # stopped moving, both at once, while the integration
                    # reported itself perfectly healthy. Two independent
                    # sensors stopping together points at the stream, not
                    # at either sensor -- and nothing was watching the
                    # stream.
                    #
                    # A watch that stops DELIVERING without ever RAISING
                    # is invisible to the error path above: the generator
                    # simply never yields again. A timestamp is the only
                    # thing that distinguishes "quiet because nothing is
                    # happening" from "quiet because the stream died".
                    self.config_entry.runtime_data.last_mqtt_message_ts = _time.time()
                    report = MissionTimelineReport.from_json(delta.payload)
                    self.async_set_updated_data(report)
                    self._request_parts_refresh_on_mission_end(report)
                    backoff = 5.0  # a live update means things are healthy again
                _LOGGER.warning(
                    "roomba_plus: V4/Prime watch_mission_timeline() for %s ended without "
                    "an exception (unexpected -- it's meant to run forever) -- retrying in %.0fs",
                    self.blid, backoff,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception(
                    "roomba_plus: V4/Prime watch_mission_timeline() for %s ended "
                    "unexpectedly -- retrying in %.0fs (this is the coordinator's own "
                    "outer safety net; the library itself already retries connection "
                    "drops internally, so reaching this suggests something else went wrong)",
                    self.blid, backoff,
                )
                self.async_set_update_error(exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)
    def _request_parts_refresh_on_mission_end(self, report: Any) -> None:
        """Refreshes the consumable parts once a mission finishes.

        WHY THIS IS NEEDED. Consumable counters only move when the robot
        cleans, and the parts coordinator polls every six hours. So a
        mission that ended at 06:08 showed unchanged filter and brush
        hours until the next poll -- @chairstacker saw his four
        maintenance sensors update at 12:14, when he restarted Home
        Assistant, and nothing at all at six o'clock.
        
        Six hours of stale data on values that changed once, at a moment
        we are told about. Polling faster would be the wrong fix: it
        would be a cloud request every few minutes for data that moves
        twice a day.

        The mission-end event is the exact trigger. Requested rather than
        awaited, because this runs inside the shadow stream and a cloud
        round trip must not stall it.
        """
        try:
            # THE FIELD NAMES ARE `event` AND `fin_events`, checked
            # against the model rather than assumed. A first draft used
            # `events`, which does not exist -- getattr would have
            # returned None silently and this would never have fired,
            # producing exactly the symptom it was written to fix.
            #
            # `fin_events` is checked first: the library separates final
            # events out, so its presence is the most direct signal.
            # `event` is the general list and may carry the "fin" entry
            # instead, depending on how the delta arrived.
            #
            # "fin" specifically, not "evac": a self-emptying base can
            # empty mid-mission and the robot carries on afterwards.
            if getattr(report, "fin_events", None):
                pass
            elif any(
                str(getattr(entry, "event_type", "") or "").lower() == "fin"
                for entry in (getattr(report, "event", None) or [])
            ):
                pass
            else:
                return
            parts = getattr(
                self.config_entry.runtime_data, "prime_parts_coordinator", None
            )
            if parts is None:
                return
            self.hass.async_create_task(
                parts.async_request_refresh(),
                name=f"roomba_plus_parts_after_mission_{self.blid}",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: parts refresh after mission end failed", exc_info=True
            )



def _deep_merge_reported(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """CONFIRMED NECESSARY (real field report, chairstacker, with
    screenshots showing the exact failure): AWS IoT Device Shadow's
    own "update/accepted" messages echo back ONLY the fields that
    were actually part of that specific update -- NOT the shadow's
    full current reported state (well-documented AWS behavior, and
    directly confirmed by this exact bug: starting a mission caused
    "ro-currentstate" to receive a small update, presumably just its
    own mission-status sub-fields, and every OTHER already-known
    ro-currentstate field -- detected_pad, dock, runtime_stats, and
    more -- simultaneously went to Unknown; meanwhile rw-settings,
    which received no update at all during that window, correctly
    kept showing its real, seeded value throughout, proving the
    connection/push mechanism itself was fine the whole time).

    A plain dict replacement (this function's own predecessor) treats
    each update as if it were the complete state, discarding every
    previously-known field the new update doesn't happen to mention.
    This merges recursively instead -- new keys/values are added or
    overwritten at whatever depth they appear at, but any existing
    key NOT present in the new update is preserved untouched, at
    every level of nesting (not just the shadow's own top level).

    SECOND REAL BUG FOUND AND FIXED IN THIS SAME FUNCTION (chairstacker,
    a later report): battery specifically still went to "Unknown"
    immediately on mission start, even with the fix above in place --
    the exact same "explicit null vs missing key" class of bug this
    project has hit 12+ times elsewhere (dict.get(key, default) only
    guards a MISSING key, not a PRESENT key whose value is explicitly
    null). A mission-start update apparently includes "batPct": null
    explicitly, rather than omitting the key -- likely the shadow's own
    way of saying "no fresh reading yet for this field in THIS
    particular update", not "this value is now genuinely unknown". The
    loop below now treats an explicit null the same as a missing key
    when a real existing value is already present -- new information
    always wins, but null is never treated as new information capable
    of clobbering a real one.

    CONSIDERED, NOT ACTED ON (no confirmed field evidence either way,
    unlike the null case above): the same "no new info in this update"
    signal COULD in principle also show up as an empty list rather
    than an explicit null for a list-typed field (this project's own
    models confirm exactly one such field, CurrentStateShadow.p2maps)
    -- an empty list overwriting a previously non-empty one would lose
    data the same way null did for batPct. Deliberately NOT guarded
    against here: unlike null, an empty list is also a genuinely
    plausible REAL value (e.g. "no pending maps right now"), so
    guarding it without confirmed evidence risks the opposite mistake
    -- refusing to accept a real, legitimate emptying. If p2maps (or
    any other list field) is ever observed reverting to a stale,
    non-empty value the way battery did, this is the first place to
    look."""
    result = dict(existing)
    for key, new_value in new.items():
        existing_value = result.get(key)
        if new_value is None and existing_value is not None:
            continue  # explicit null must not clobber a real, already-known value
        if isinstance(existing_value, dict) and isinstance(new_value, dict):
            result[key] = _deep_merge_reported(existing_value, new_value)
        else:
            result[key] = new_value
    return result


class PrimeStatusCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Push coordinator for ALL eight named shadows at once -- battery/
    dock/bin/tank/pad status (ro-currentstate), connection status
    (rw-constatus), schedule (rw-schedule), software (rw-software),
    stats (ro-stats), services (ro-services), config info
    (ro-configinfo), and settings (rw-settings). Separate from
    PrimeCoordinator above deliberately: that one is built specifically
    around watch_mission_timeline()'s own confirmed event schema, and
    mixing a second, differently-shaped data source into it would mean
    every listener has to know which kind of update just arrived.

    coordinator.data: dict[str, dict[str, Any]] -- shadow name (e.g.
    "ro-currentstate") -> that shadow's raw reported-state dict.
    Deliberately raw, not pre-parsed into roombapy_prime's own typed
    models here -- individual sensor entities call e.g.
    CurrentStateShadow.from_json(coordinator.data.get("ro-currentstate", {}))
    themselves when reading their own value, the same way
    PrimeMissionEventSensor already reads PrimeCoordinator's own
    MissionTimelineReport. Kept generic and shadow-agnostic here so
    adding a sensor for a DIFFERENT shadow later (e.g. ro-stats'
    lifetime battery-cycle counters) never needs this coordinator
    itself touched again -- only the seed-fetch list below, if a new
    shadow is added to it.

    Seeded via ALL EIGHT NAMED shadows fetched once at startup (not just
    ro-currentstate) specifically BECAUSE watch_named_shadows_updates()
    is a single "+" wildcard covering every named shadow already --
    the push side receives updates for all of them regardless, so
    seeding only one and discarding the rest of what the SAME
    subscription delivers would be wasteful and would need redoing
    the moment a second shadow's data becomes a sensor.

    NOT a polling coordinator (update_interval=None), but unlike
    PrimeCoordinator, this data source needs an explicit seed:
    watch_named_shadows_updates() only delivers updates going forward
    (nothing arrives until a shadow's reported state next changes), so
    async_start() does the seed-fetch first -- this coordinator would
    otherwise report no data at all until the first live change on
    each shadow, which could be a long wait for a slowly-changing
    value like battery percentage.

    watch_named_shadows_updates() itself is a genuinely new,
    NOT-YET-LIVE-TESTED mechanism as of this writing (see its own
    docstring in roombapy-prime) -- reasoned and safety-checked (a
    single-level "+" wildcard on update/accepted, confirmed distinct
    from the multi-level "#" wildcard already ruled out elsewhere),
    but the FIRST real confirmation that named shadows push updates
    via this specific channel is still pending. If it turns out they
    don't push updates this way in practice, this coordinator would
    silently stay at its seed values forever, with no error -- worth
    watching for in practice, not yet confirmed either way.
    """

    # The eight named shadows this project has confirmed exist and
    # modeled the content of (see roombapy-prime's own
    # verify_named_shadows.py KNOWN_SHADOWS/CANDIDATE_SHADOWS for the
    # same list) -- NOT the classic/unnamed shadow (see
    # CLASSIC_SHADOW_KEY below for that one).
    NAMED_SHADOWS: Final[tuple[str, ...]] = (
        "rw-settings", "rw-constatus", "rw-schedule", "rw-software",
        "ro-currentstate", "ro-stats", "ro-services", "ro-configinfo",
    )

    # THE UNNAMED SHADOW. Read with get_state() rather than
    # get_named_shadow(), which is the whole difference: a Prime robot
    # has nine shadows, eight named and one without a name.
    #
    # THE KEY IS CALLED "classic" AND THAT NAME IS MISLEADING. It
    # describes the shadow's FORMAT -- the same shape a Classic robot's
    # state object has -- not the robot. This is a V4/Prime shadow on a
    # V4/Prime robot, and nothing here touches Classic hardware.
    #
    # Kept as-is because the string appears in every diagnostics
    # download testers have sent, and renaming it would make older
    # reports harder to compare against newer ones. The confusion is
    # real: it prompted "why classic shadows? we are on prime robots".
    #
    # It carries CapabilityFlags (cap) and
    # DockCapabilities (nested under ro-currentstate.dock.cap instead),
    # the only per-device hardware-capability data found anywhere in
    # this project so far (see ClassicShadowState's own docstring,
    # roombapy-prime's models/robot_info.py). SEED-ONLY, deliberately
    # NOT added to _async_watch_status_updates()'s push loop below:
    # hardware capabilities don't change at runtime, so a single fetch
    # is sufficient forever, and this shadow's own push behavior is
    # separately unconfirmed (see this class's own docstring above) --
    # no reason to depend on an unconfirmed mechanism for data that
    # doesn't need live updates anyway.
    CLASSIC_SHADOW_KEY: Final[str] = "classic"

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        blid: str,
        prime_robot: PrimeRobot,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"iRobot Prime status ({blid})",
            config_entry=config_entry,
            update_interval=None,  # push, not polled -- see class docstring
        )
        self.blid = blid
        self.prime_robot = prime_robot

    async def async_start(self) -> None:
        """Seeds initial data by fetching ALL eight named shadows once,
        then starts the background watch_named_shadows_updates()
        consumer.

        Call once during entry setup, after PrimeCoordinator.async_start()
        (which already opened the shared MQTT connection) -- this does
        NOT call prime_robot.connect() itself, reusing the existing
        connection rather than opening a second one.

        Each shadow is fetched independently -- one timing out (e.g. a
        genuinely tier-dependent shadow this specific device doesn't
        support) does not prevent the others from seeding successfully,
        logged as a warning rather than aborting setup entirely. Only
        raises ConfigEntryNotReady if EVERY shadow fails, since that
        specifically suggests a real connectivity problem rather than
        per-shadow tier differences.
        """
        seeded: dict[str, dict[str, Any]] = {}
        last_exc: Exception | None = None
        for name in self.NAMED_SHADOWS:
            try:
                response = await self.prime_robot.get_named_shadow(name)
            except (ShadowSSLError, ShadowConnectionError, ShadowError) as exc:
                _LOGGER.warning(
                    "roomba_plus: could not seed named shadow %r for %s: %s", name, self.blid, exc
                )
                last_exc = exc
                continue
            seeded[name] = (response.payload or {}).get("state", {}).get("reported", {})

        if not seeded and last_exc is not None:
            raise ConfigEntryNotReady(
                f"Could not fetch any named shadow for V4/Prime robot {self.blid}: {last_exc}"
            )

        # NEW (this session): one-time seed of the classic/unnamed shadow
        # (see CLASSIC_SHADOW_KEY's own docstring above) -- deliberately
        # NOT counted towards the "every shadow failed" check above,
        # since the eight named shadows are the ones this coordinator's
        # own ConfigEntryNotReady guard was built around; this one
        # failing shouldn't block setup on its own.
        try:
            state_response = await self.prime_robot.get_state()
            seeded[self.CLASSIC_SHADOW_KEY] = (state_response.payload or {}).get("state", {}).get("reported", {})
        except (ShadowSSLError, ShadowConnectionError, ShadowError) as exc:
            _LOGGER.warning(
                "roomba_plus: could not seed classic/unnamed shadow for %s: %s", self.blid, exc
            )

        self.async_set_updated_data(seeded)

        self.config_entry.async_create_background_task(
            self.hass,
            self._async_watch_status_updates(),
            name=f"roomba_plus_prime_status_watch_{self.blid}",
        )

    async def _async_watch_status_updates(self) -> None:
        """Runs for the lifetime of the config entry. Parses each
        incoming response's own .topic to determine which named shadow
        it belongs to (the wildcard resolves to the real shadow name
        in the actual message), and RECURSIVELY merges just the fields
        present in that update into the shadow's own already-known
        data (see _deep_merge_reported()'s own docstring for why a
        plain replace was a real, confirmed bug) -- every other
        shadow, and every field within THIS shadow not mentioned in
        the update, is left untouched.

        DEFENSIVE OUTER RETRY LOOP (added after a real field report --
        chairstacker: sensors got stuck reporting stale/Unknown values
        after some activity, and only a full HA restart -- not just
        reloading the integration -- fixed it): roombapy-prime's own
        watch_named_shadows_updates() already reconnects transparently
        across connection drops with unbounded retries (see its own
        docstring) -- a plain connection drop should never reach here
        at all. This outer loop exists as a second, coordinator-level
        safety net for anything ELSE that could end the inner
        generator unexpectedly (a bug in this method's own parsing
        below, an edge case the library's reconnect logic doesn't
        cover, etc.) -- so a single unexpected error degrades to
        "retry after a short delay" rather than "this coordinator is
        now permanently dead until Home Assistant itself restarts".
        Exact root cause of the original field report not confirmed
        from this fix alone -- this is a resilience improvement
        regardless of what specifically caused it."""
        backoff = 5.0
        while True:
            try:
                async for response in self.prime_robot.watch_named_shadows_updates():
                    shadow_name = None
                    for name in self.NAMED_SHADOWS:
                        if response.topic.endswith(f"/shadow/name/{name}/update/accepted"):
                            shadow_name = name
                            break
                    if shadow_name is None:
                        continue
                    reported = (response.payload or {}).get("state", {}).get("reported", {})
                    if not reported:
                        continue
                    updated = dict(self.data or {})
                    updated[shadow_name] = _deep_merge_reported(updated.get(shadow_name) or {}, reported)
                    self.config_entry.runtime_data.last_mqtt_message_ts = _time.time()
                    self._note_phase_for_timer(updated)
                    self.async_set_updated_data(updated)
                    backoff = 5.0  # a live update means things are healthy again
                _LOGGER.warning(
                    "roomba_plus: V4/Prime watch_named_shadows_updates() for %s ended without "
                    "an exception (unexpected -- it's meant to run forever) -- retrying in %.0fs",
                    self.blid, backoff,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception(
                    "roomba_plus: V4/Prime watch_named_shadows_updates() for %s ended "
                    "unexpectedly -- retrying in %.0fs (this is the coordinator's own outer "
                    "safety net; the library itself already retries connection drops "
                    "internally, so reaching this suggests something else went wrong)",
                    self.blid, backoff,
                )
                self.async_set_update_error(exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)
    def _note_phase_for_timer(self, shadows: dict[str, Any]) -> None:
        """Feeds phase transitions to MissionTimerStore.

        The store tracks a RUNNING mission -- elapsed time, time in the
        current room, remaining estimate -- and it only works if
        something tells it when running starts and stops. Classic does
        that from its MQTT phase handler; this is the Prime equivalent.
        
        Creating the store without this would have been the same mistake
        as filling MissionStore while no sensor read it: a store that
        exists, is persisted, and stays empty forever.

        Best-effort throughout: a progress estimate is not worth taking
        down the shadow stream for.
        """
        try:
            data = self.config_entry.runtime_data
            store = getattr(data, "mission_timer_store", None)
            if store is None:
                return
            status = (
                (shadows.get("ro-currentstate") or {}).get("cleanMissionStatus") or {}
            )
            phase = status.get("phase")
            if phase is None:
                return
            if phase == "run":
                mission_id = str(status.get("nMssn") or status.get("mssnM") or "prime")
                # A NEW MISSION CLEARS THE TRAIL.
                #
                # Positions only ever accumulated before: the path from
                # last night's clean stayed on the map underneath
                # tonight's, and after a week the map was a solid block.
                # Bounded by count, which is not the same as bounded by
                # mission.
                #
                # Keyed on the mission id changing rather than on the
                # phase, because a robot that pauses and resumes re-enters
                # "run" with the same mission and should keep its trail.
                if mission_id != self._trail_mission_id:
                    self._trail_mission_id = mission_id
                    positions = getattr(
                        self.config_entry.runtime_data, "prime_positions", None
                    )
                    if positions is not None:
                        positions.clear()
                store.on_phase_run(mission_id, self.hass, self.config_entry.entry_id)
            else:
                # Anything that is not "run" ends the running segment --
                # including charge, evac and stuck. The store flushes the
                # elapsed delta rather than discarding it.
                store.on_phase_other(self.hass, self.config_entry.entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("roomba_plus: mission timer phase update failed", exc_info=True)



def get_prime_capability_flags(config_entry: Any) -> tuple[Any | None, Any | None]:
    """Returns (CapabilityFlags, DockCapabilities) for a CLOUD_ONLY
    config entry -- (None, None) if genuinely unavailable (coordinator
    not yet seeded, classic shadow fetch failed at startup, etc.).

    NEW (this session) -- used to gate entities on real, per-device
    hardware capability (see const.py's own existing precedent for
    Classic: cap.get("carpetBoost")/cap.get("pose")/cap.get("maps")),
    previously entirely absent for Prime entities.

    IMPORTANT CONTRACT: None means "unknown", NOT "absent". Callers
    should default to CREATING an entity when either return value is
    None -- only an explicit 0 (confirmed negative pattern this
    session: binFullDetect/matter both read 0 in a real capture from a
    device that otherwise has almost every other capability, proving 0
    genuinely means "not present" for this cap object) should suppress
    an entity. Failing open on a transient fetch hiccup is the safer
    default -- a missing entity reads as "this integration is broken"
    to a user, while an entity that doesn't quite apply is a much
    smaller problem."""
    coordinator = config_entry.runtime_data.prime_status_coordinator
    if coordinator is None or coordinator.data is None:
        return None, None

    from roombapy_prime.models import ClassicShadowState, CurrentStateShadow

    classic_raw = coordinator.data.get(PrimeStatusCoordinator.CLASSIC_SHADOW_KEY)
    cap = ClassicShadowState.from_json(classic_raw).cap if classic_raw else None

    dock_cap = None
    current_state_raw = coordinator.data.get("ro-currentstate")
    if current_state_raw:
        current_state = CurrentStateShadow.from_json(current_state_raw)
        if current_state.dock is not None:
            dock_cap = current_state.dock.cap

    return cap, dock_cap


class PrimePartsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """V4/Prime consumable parts -- filter, brushes, pads, dirt bag.

    THE ONE POLLING COORDINATOR HERE, and deliberately so. The other
    two are push-only because their data arrives over MQTT. Parts come
    from a REST endpoint that nothing pushes, so this has to ask.

    It asks rarely. Consumables move on a scale of hours of runtime; a
    filter does not change between one minute and the next, and polling
    a cloud API hard for a number that shifts a few times a day would
    be rude to a service this project does not own.

    WHY THIS EXISTS AT ALL (chairstacker): he asked for the app's
    consumables view in Home Assistant and attached a screenshot. The
    data turned out to be already reachable -- the library has
    supported this endpoint for a while and the integration simply
    never called it. That is a gap on our side rather than a protocol
    limitation.

    NOTE ON WHAT THIS IS NOT: Classic robots compute consumable wear
    themselves, in maintenance_store.py, because the robot reports
    nothing about it -- including learning your real replacement
    interval after a couple of resets. None of that applies here. Prime
    robots are simply told the answer by the cloud, so this coordinator
    stores what it is given and does no arithmetic of its own.
    """

    #: Parts change on the scale of cleaning hours. Six hours is
    #: frequent enough that a user never sees a badly stale number and
    #: infrequent enough to be a good citizen against someone else's API.
    UPDATE_INTERVAL = timedelta(hours=6)

    def __init__(
        self,
        hass: HomeAssistant,
        prime_robot: Any,
        blid: str,
        config_entry: ConfigEntry,
    ) -> None:
        self.prime_robot = prime_robot
        self.blid = blid
        super().__init__(
            hass,
            _LOGGER,
            # Matches how the other two coordinators in this file name
            # themselves -- no import of DOMAIN, which this module has
            # deliberately stayed free of.
            name=f"roomba_plus_prime_parts_{blid}",
            update_interval=self.UPDATE_INTERVAL,
            config_entry=config_entry,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Returns {part_id: RobotPart}, and syncs mission history.

        MISSION HISTORY RIDES ALONG HERE rather than getting its own
        coordinator. Both are REST reads on the same six-hour rhythm,
        both describe things that change once or twice a day, and a
        second timer for the same cadence is a second thing to reason
        about for no gain.

        The sync is best-effort and never affects this coordinator's
        result: parts sensors must not go unavailable because the
        mission endpoint had a bad day.

        Keyed by part_id because that is what the sensors are built
        from, and because the set of parts differs by model -- a
        vacuum-only robot has no mop pads, a robot without a
        self-emptying base has no dirt bag. Discovering them rather
        than hard-coding a list is the only thing that works across
        hardware nobody here owns.
        """
        try:
            info = await self.prime_robot.get_robot_parts()
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"could not fetch consumable parts: {exc}") from exc

        parts = {
            p.part_id: p for p in (getattr(info, "parts", None) or []) if p.part_id
        }

        # Best-effort, and deliberately after `parts` is computed: a
        # failure here must not cost the parts sensors their data.
        if self.config_entry is not None:
            try:
                from .prime_mission_sync import (  # noqa: PLC0415
                    async_sync_prime_missions,
                )

                await async_sync_prime_missions(self.config_entry)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "roomba_plus: mission history sync failed", exc_info=True
                )

        return parts
