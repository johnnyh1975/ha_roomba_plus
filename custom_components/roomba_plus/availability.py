"""Local availability of a Classic robot, with a short grace period.

WHY THIS EXISTS. @mdarocha: the robot ran its battery flat and dropped off
the network. Roomba+ noticed — the Connected sensor went off within a
minute — and then reported the robot as cleaning, ready, at 1 % battery
and 99 % through its mission for three and a half hours, until it came
back. No entity went unavailable, because none was ever told to:
`IRobotEntity` never overrode `available`, so Home Assistant's default of
"always available" applied to every one of them. Only the Connected
sensor read the connection at all.

WHAT IS UNAVAILABLE. Only LIVE state — what the robot is doing and
reporting right now: the vacuum, phase, readiness, battery, signal,
tracker, mission progress and the active mission's times. History,
counters, configuration and the last mission keep their values, because
those are still true while the robot is away. Each entity opts in with
`_live_state = True`; see IRobotEntity.

WHY A GRACE PERIOD. roombapy reconnects on its own after a Wi-Fi blip.
Marking a hundred entities unavailable for a few seconds on every blip
would fill the history with gaps and trip automations for nothing. The
Connected sensor still reports the drop immediately; the live entities
follow only if it lasts LOCAL_UNAVAILABLE_GRACE_SEC.

CLASSIC ONLY. Prime robots are cloud-only and have no local connection;
their entities pass `roomba=None` and are never affected.

THE SIGNAL CARRIES THE STATE, because many Classic entities are
constructed without a config entry and cannot reach runtime data. A
module-level registry keyed by BLID answers the question for an entity
added while the robot is already away.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from roombapy_prime.models import ConnectionStatusShadow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: How long the local connection may be down before live entities go
#: unavailable. Long enough to ride out a Wi-Fi blip and roombapy's own
#: reconnect; short against the hours a flat battery lasts.
LOCAL_UNAVAILABLE_GRACE_SEC = 60

_SIGNAL = f"{DOMAIN}_local_availability_{{blid}}"

_AVAILABLE: dict[str, bool] = {}


def local_availability_signal(blid: str) -> str:
    """Dispatcher signal name for one robot."""
    return _SIGNAL.format(blid=blid)


def is_locally_available(blid: str) -> bool:
    """Whether the robot's live state may be shown as current.

    True until a disconnect has lasted the grace period. Unknown robots
    — nothing registered — count as available, so an entity is never
    made unavailable by the absence of a watcher.
    """
    return _AVAILABLE.get(blid, True)


class LocalAvailabilityWatcher:
    """Turns roombapy's connection transitions into entity availability."""

    def __init__(self, hass: HomeAssistant, roomba: Any, blid: str) -> None:
        self._hass = hass
        self._roomba = roomba
        self._blid = blid
        self._cancel_timer: Callable[[], None] | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._stopped = False

    def start(self) -> None:
        """Subscribe to the client's connection-state transitions."""
        _AVAILABLE[self._blid] = True
        register = getattr(self._roomba, "register_on_connection_state_callback", None)
        if register is None:
            # A roombapy without it cannot tell us; leave everything
            # available rather than guess.
            _LOGGER.debug(
                "Roomba+: client has no connection-state callback; "
                "live entities will not follow the local connection"
            )
            return
        self._unsubscribe = register(self._on_connection_state)

    @callback
    def stop(self) -> None:
        """Detach and forget this robot. Safe to call more than once."""
        self._stopped = True
        self._cancel_pending()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        _AVAILABLE.pop(self._blid, None)

    @callback
    def _on_connection_state(self, state: str, _error: str | None = None) -> None:
        """roombapy calls this on the event loop, on every transition."""
        if self._stopped:
            return
        if state == "connected":
            self._cancel_pending()
            if not _AVAILABLE.get(self._blid, True):
                _LOGGER.info("Roomba+: %s reconnected; live state is current again", self._blid)
                self._set(True)
            return
        # Disconnected: start the grace period once; a second disconnect
        # notice while it runs must not restart it.
        if self._cancel_timer is None and _AVAILABLE.get(self._blid, True):
            self._cancel_timer = async_call_later(
                self._hass, LOCAL_UNAVAILABLE_GRACE_SEC, self._grace_expired
            )

    @callback
    def _grace_expired(self, _now: Any) -> None:
        self._cancel_timer = None
        if self._stopped or self._currently_connected():
            return
        _LOGGER.info(
            "Roomba+: %s unreachable for %ss; marking live state unavailable",
            self._blid, LOCAL_UNAVAILABLE_GRACE_SEC,
        )
        self._set(False)

    def _currently_connected(self) -> bool:
        """Checked when the grace period ends: still disconnected?"""
        return bool(getattr(self._roomba, "connected", False))

    @callback
    def _cancel_pending(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _set(self, available: bool) -> None:
        _AVAILABLE[self._blid] = available
        async_dispatcher_send(self._hass, local_availability_signal(self._blid), available)


class PrimeAvailabilityWatcher(LocalAvailabilityWatcher):
    """The same grace period, signal and logging, fed from Prime's signals.

    QUALITY SCALE, entity-unavailable and log-when-unavailable, for Prime.
    Until 4.2.11 only Classic followed its connection; a Prime robot that
    went offline kept showing its last state, as @mdarocha's Classic did.

    Prime has no local MQTT client. Its live state is current when BOTH
    hold:
      - this integration's own connection works — the mission-timeline
        watch, `prime_coordinator.last_update_success`;
      - the robot reports itself connected to the iRobot cloud — the
        `rw-constatus` shadow, via `prime_status_coordinator`.
    Either failing makes the state stale. The second alone is not enough:
    if our own connection drops, no messages arrive, and `rw-constatus`
    would keep saying "connected" for ever.

    Unknown is not a failure: with no `rw-constatus` received yet, the
    robot counts as connected, so absence of data never hides entities.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        blid: str,
        prime_coordinator: Any,
        status_coordinator: Any,
    ) -> None:
        super().__init__(hass, None, blid)
        self._prime = prime_coordinator
        self._status = status_coordinator
        self._listeners: list[Callable[[], None]] = []

    def start(self) -> None:
        """Listen to both coordinators and take the current reading."""
        _AVAILABLE[self._blid] = True
        for coordinator in (self._prime, self._status):
            if coordinator is not None:
                self._listeners.append(coordinator.async_add_listener(self._evaluate))
        self._evaluate()

    @callback
    def stop(self) -> None:
        for remove in self._listeners:
            remove()
        self._listeners.clear()
        super().stop()

    def _currently_connected(self) -> bool:
        if self._prime is not None and not getattr(self._prime, "last_update_success", True):
            return False
        data = getattr(self._status, "data", None) if self._status is not None else None
        raw = data.get("rw-constatus") if isinstance(data, dict) else None
        if raw is None:
            return True
        try:
            connected = ConnectionStatusShadow.from_json(raw).connected
        except (TypeError, ValueError, KeyError, AttributeError):
            return True
        # `from_json` does not raise on unreadable data — it returns
        # connected=None. `bool(None)` read that as DISCONNECTED, so a
        # shadow without the field would have hidden every live entity
        # after the grace period. Only an explicit False is an outage.
        return connected is not False

    @callback
    def _evaluate(self) -> None:
        self._on_connection_state(
            "connected" if self._currently_connected() else "disconnected"
        )
