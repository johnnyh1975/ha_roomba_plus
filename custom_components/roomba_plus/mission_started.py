"""A mission began, as one event (4.6.0, B2).

Until 4.6.0 a start was only visible as the vacuum's state flipping to
`cleaning` -- which it also does on a resume, on a recharge leg and,
on lewis firmware, between rooms. An automation that wanted "the robot
started a run of the kitchen from the schedule" had nothing to listen
to.

`roomba_plus_mission_started` fires once per mission, when Roomba+
recognises the start, on both generations, and
`event.<robot>_mission_started` mirrors it:

- `initiator`: who started it, as the robot says (`rmtApp`, `localApp`,
  `schedule`, `demand` for Roomba+'s own demand clean, ...). Classic
  reads it from the mission; Prime from the last command, and only when
  that command is recent -- an old command says nothing about a start
  the robot's own schedule made, and None is then the honest answer;
- `scope`: `rooms` or `whole_home`; None where it is not known;
- `rooms`: the rooms asked for, by name; empty for a whole-home run;
- `areas`: the Home Assistant area of each room, in the same order, or
  None for a room with no area (area_resolver.async_area_for_room);
- `mission_number`: the robot's own counter (`nMssn`), where it has one.

NOT FOR THE FIRST UPDATE AFTER SETUP: a mission already running then
began before Home Assistant could tell anyone. Both callers already
guard that for the presence history they feed at the same moment.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .area_resolver import async_area_for_room
from .const import EVENT_MISSION_STARTED

_LOGGER = logging.getLogger(__name__)

#: How old the Prime last command may be and still describe the start.
#: Generous on purpose: the cloud can take most of a minute to report
#: the new cycle (command_watch, the Prime grace), and a schedule start
#: issues no command at all, so a stale command must not be taken for it.
PRIME_COMMAND_MAX_AGE_SEC = 300


@callback
def fire_mission_started(
    hass: HomeAssistant,
    entry: Any,
    *,
    initiator: Any,
    scope: Any,
    rooms: list[str] | None,
    mission_number: Any = None,
) -> None:
    """Fire `roomba_plus_mission_started` for this robot."""
    names = [str(r) for r in rooms or [] if r]
    hass.bus.async_fire(EVENT_MISSION_STARTED, {
        "entry_id": entry.entry_id,
        "name": getattr(entry, "title", None),
        "initiator": str(initiator) if initiator else None,
        "scope": str(scope) if scope else None,
        "rooms": names,
        "areas": [async_area_for_room(hass, entry, room) for room in names],
        "mission_number": mission_number,
        "started_at": dt_util.utcnow().isoformat(timespec="seconds"),
    })


def prime_start_details(
    data: Any, now: float | None = None
) -> tuple[str | None, str | None, list[str]]:
    """(initiator, scope, rooms) of a Prime mission starting now.

    From the robot's last command (prime_coordinator.prime_last_command),
    which carries `initiator` and, for a room start, the regions. A
    command older than PRIME_COMMAND_MAX_AGE_SEC, or one without a time,
    describes some earlier start: then nothing is claimed.
    """
    from .prime_coordinator import prime_last_command  # noqa: PLC0415

    command = prime_last_command(data) or {}
    sent = command.get("time")
    now = time.time() if now is None else now
    if (
        not isinstance(sent, (int, float)) or isinstance(sent, bool)
        or not 0 <= now - float(sent) <= PRIME_COMMAND_MAX_AGE_SEC
    ):
        return None, None, []
    regions = [
        r for r in command.get("regions") or [] if isinstance(r, dict)
    ]
    names = getattr(data, "prime_room_names", None) or {}
    rooms: list[str] = []
    for region in regions:
        region_id = str(region.get("region_id") or region.get("id") or "")
        label = region.get("region_name") or names.get(region_id) or region_id
        if label:
            rooms.append(str(label))
    initiator = command.get("initiator")
    return (
        str(initiator) if initiator else None,
        "rooms" if rooms else "whole_home",
        rooms,
    )
