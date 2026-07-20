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

STILL NOT A COMPLETE STATE FACADE: mission/timeline/report gives room/
mission progress (which room, area, timestamps), not battery level or
docking boolean state (RobotStatusV2 -- battery_level/is_charging/
is_robot_on_dock) remains a separate, unconfirmed structure. See
vacuum.py's activity property for how the confirmed event types are
used to approximate a VacuumActivity, and its own caveats about which
mappings are live-confirmed vs. only historically-confirmed.
"""
from __future__ import annotations

import logging

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
