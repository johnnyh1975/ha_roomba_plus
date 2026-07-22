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

import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
        it yields; it does not need its own reconnect/retry logic on
        top. If it ever DOES raise (not expected in normal operation,
        since watch_mission_timeline() is designed to retry forever
        internally), that's a genuinely unexpected failure -- logged,
        and surfaced to entities via async_set_update_error() rather
        than left as silently stale data.
        """
        try:
            async for delta in self.prime_robot.watch_mission_timeline():
                self.async_set_updated_data(MissionTimelineReport.from_json(delta.payload))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "roomba_plus: V4/Prime watch_mission_timeline() for %s ended unexpectedly", self.blid
            )
            self.async_set_update_error(exc)


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

    Seeded via ALL EIGHT shadows fetched once at startup (not just
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
    # same list) -- NOT the classic/unnamed shadow, which has its own,
    # separate get_state()-based path elsewhere and no confirmed
    # update/accepted-style push story of its own yet.
    NAMED_SHADOWS: Final[tuple[str, ...]] = (
        "rw-settings", "rw-constatus", "rw-schedule", "rw-software",
        "ro-currentstate", "ro-stats", "ro-services", "ro-configinfo",
    )

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
        in the actual message), and merges just that shadow's new
        reported data into self.data -- every other shadow's
        already-known data is left untouched."""
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
                updated[shadow_name] = reported
                self.async_set_updated_data(updated)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "roomba_plus: V4/Prime watch_named_shadows_updates() for %s ended unexpectedly", self.blid
            )
            self.async_set_update_error(exc)
