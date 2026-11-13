"""Commands the robot took and did not act on, for both generations (4.6.0).

TWO SIGNATURES, BOTH SEEN IN THE FIELD.

1. NOT DELIVERED. The command is accepted -- into `lastCommand` on
   Classic, acknowledged by the broker on Prime -- and no mission opens.
   Classic: @Young9898's i3+ (a stale map version, a region start away
   from the dock, a false cliff reading). Prime: @ecovictor75's and
   @leonel11fcp's Combo 105 (4.5.3), on a deployment the command never
   reached; state and battery kept arriving as if nothing were wrong.
   Until 4.6.0 Classic logged a warning and Prime was not watched at all:
   the 4.5.2 check read the local state, which a Prime robot does not
   have, and took the empty answer for "no mission". Prime reports the
   same `cleanMissionStatus` in its `ro-currentstate` shadow, and that is
   what is read here.

2. STATE FROZEN. @utkjmitch's Y351020 ignored every command for 61
   hours while its cloud document stood at `phase: run`, `error: 48` and
   the battery charged (roombapy-prime, `send_simple_command`). A robot
   cannot clean and charge at once; a document that says so has stopped
   following the robot, and only a power cycle cleared it. A check that
   asks "did a mission open" reads such a document as running, so this
   is a second, separate watch on the state itself.

Both are reported as an event on the bus and on `event.<robot>_command`,
so a user without admin rights sees them too (the reason the event
entities exist, 4.2.19).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .const import EVENT_COMMAND_NOT_DELIVERED, EVENT_STATE_FROZEN

_LOGGER = logging.getLogger(__name__)

def _is_prime(data: Any) -> bool:
    from .models import ConnectionType  # noqa: PLC0415

    return getattr(data, "connection_type", None) is ConnectionType.CLOUD_ONLY


def mission_status(entry: Any) -> dict[str, Any] | None:
    """The robot's mission status in the Classic shape, from either generation.

    Classic reads its local state; Prime the `ro-currentstate` shadow the
    status coordinator holds. `batPct` comes along for the frozen-state
    check. None when there is nothing to read yet, or reading failed: a
    caller must then say nothing rather than report a missing mission.
    """
    data = getattr(entry, "runtime_data", None)
    if data is None:
        return None
    try:
        if getattr(data, "roomba", None) is not None:
            from . import roomba_reported_state  # noqa: PLC0415

            reported = roomba_reported_state(data.roomba)
            status = dict(reported.get("cleanMissionStatus") or {})
            status["batPct"] = reported.get("batPct")
            return status
        if _is_prime(data):
            # The raw shadow: Prime's `cleanMissionStatus` carries the
            # Classic wire names (roombapy-prime `CleanMissionStatus`).
            coordinator = getattr(data, "prime_status_coordinator", None)
            shadows = getattr(coordinator, "data", None)
            current = shadows.get("ro-currentstate") if isinstance(shadows, dict) else None
            if not isinstance(current, dict):
                return None
            cms = current.get("cleanMissionStatus")
            if not isinstance(cms, dict):
                return None
            status = dict(cms)
            status["batPct"] = current.get("batPct")
            return status
    except Exception:  # noqa: BLE001 - best effort, see docstring
        return None
    return None


def cycle_is_open(status: dict[str, Any] | None) -> bool:
    """Whether a mission is open: any cycle but `none` (and not empty)."""
    if not status:
        return False
    return (status.get("cycle") or "none") != "none"


def fire_not_delivered(
    hass: Any, entry: Any, what: str, status: dict[str, Any] | None,
    *, waited_sec: float, resent: bool,
) -> None:
    """The bus event for a command the robot took and did not act on."""
    status = status or {}
    hass.bus.async_fire(EVENT_COMMAND_NOT_DELIVERED, {
        "entry_id": entry.entry_id,
        "name": getattr(entry, "title", None),
        "command": what,
        "phase": status.get("phase"),
        "not_ready": status.get("notReady"),
        "error": status.get("error"),
        "waited_sec": round(waited_sec),
        "resent": resent,
    })


#: A robot cannot clean and charge at once. How far the battery has to
#: climb in `run` before the state counts as frozen, and how long the
#: phase has to have stood: a percentage point of jitter is normal, three
#: over ten minutes is a robot on its dock.
FROZEN_BATTERY_RISE = 3
FROZEN_MIN_SECONDS = 600.0


@dataclass
class FrozenStateDetector:
    """Watches one robot's phase and battery for `run` while charging."""

    run_since: float | None = None
    lowest_battery: int | None = None
    reported: bool = False

    def feed(self, phase: Any, battery: Any, now: float | None = None) -> dict[str, Any] | None:
        """Take one observation; the details once, when it turns frozen."""
        now = time.time() if now is None else now
        if phase != "run":
            self.run_since = None
            self.lowest_battery = None
            self.reported = False
            return None
        if self.run_since is None:
            self.run_since = now
        if not isinstance(battery, int) or isinstance(battery, bool):
            return None
        if self.lowest_battery is None or battery < self.lowest_battery:
            self.lowest_battery = battery
        if (
            not self.reported
            and battery - self.lowest_battery >= FROZEN_BATTERY_RISE
            and now - self.run_since >= FROZEN_MIN_SECONDS
        ):
            self.reported = True
            return {
                "minutes_in_run": round((now - self.run_since) / 60),
                "battery_from": self.lowest_battery,
                "battery_to": battery,
            }
        return None


def note_state(hass: Any, entry: Any, status: dict[str, Any] | None) -> None:
    """Feed one status to the robot's frozen-state detector; report once."""
    data = getattr(entry, "runtime_data", None)
    detector = getattr(data, "frozen_state", None)
    if detector is None or not status:
        return
    found = detector.feed(status.get("phase"), status.get("batPct"))
    if found is None:
        return
    _LOGGER.warning(
        "%s: the robot's state has said 'running' for %d minutes while its "
        "battery charged from %s %% to %s %%. A robot cannot do both: the "
        "state has stopped following the robot, and commands are likely to "
        "be ignored until it is restarted (power off and on).",
        getattr(entry, "title", "robot"),
        found["minutes_in_run"], found["battery_from"], found["battery_to"],
    )
    hass.bus.async_fire(EVENT_STATE_FROZEN, {
        "entry_id": entry.entry_id,
        "name": getattr(entry, "title", None),
        "phase": status.get("phase"),
        "error": status.get("error"),
        **found,
    })


def frozen(entry: Any) -> bool:
    """Whether the robot's state is frozen right now (no resend into it)."""
    detector = getattr(getattr(entry, "runtime_data", None), "frozen_state", None)
    return bool(getattr(detector, "reported", False))


def make_classic_state_callback(hass: Any, entry: Any) -> Any:
    """The Classic feed: every local message, read against the full state."""

    def _on_message(_json_data: dict[str, Any]) -> None:
        status = mission_status(entry)
        if status is None:
            return
        # roombapy 2 calls back on the event loop.
        note_state(hass, entry, status)

    return _on_message


def note_prime_shadows(hass: Any, entry: Any, shadows: dict[str, Any]) -> None:
    """The Prime feed: one shadow update, before it reaches the coordinator."""
    current = shadows.get("ro-currentstate") if isinstance(shadows, dict) else None
    if not isinstance(current, dict):
        return
    status = dict(current.get("cleanMissionStatus") or {})
    status["batPct"] = current.get("batPct")
    note_state(hass, entry, status)


async def sleep(seconds: float) -> None:
    """`asyncio.sleep`, one place for tests to replace."""
    await asyncio.sleep(seconds)
