"""Calendar platform for Roomba+.

v3.4.0 CAL — `calendar.roomba_*_schedule`: the robot's iRobot-app
cleaning schedule (cleanSchedule2 for i/s/j-series, legacy
cleanSchedule for 900/600-series) rendered as recurring HA Calendar
events. Read-only (reduced scope, Nutzersicht-Review Juli 2026) — no
create/update/delete support, and no separate mission-history calendar
(that would be a third rendering of data Logbook and the Card's
History tab already cover).

Always created, regardless of robot tier (CAL plan §3 decision):
scheduling is a software feature virtually every iRobot model supports,
unlike map-dependent platforms (image.py) that need real hardware
capability. An empty calendar (no schedule set yet) is normal,
well-supported HA behaviour, not an error state.

All parsing lives in schedule_parser.py, shared with sensor_core.py's
sensor.*_next_clean — see that module's docstring for why it's kept
separate from both platforms rather than one importing the other.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
import datetime as dt_stdlib

from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .schedule_parser import parse_schedule_occurrences

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

# How far ahead RoombaScheduleCalendar.event looks for "the next upcoming
# occurrence". A weekly-recurring schedule always has at least one hit
# within any 7-day window if any day is enabled, so 2 weeks is a
# comfortable margin without being an unbounded search.
_NEXT_EVENT_LOOKAHEAD = dt_stdlib.timedelta(weeks=2)

# v3.4.0 CAL plan §2.3 — no planned mission duration exists in either
# schedule format (only a start time). A fixed summary/description
# rather than an estimated one avoids implying a precision the data
# doesn't support (same reasoning as the ENERGY feature being cut for
# false precision).
_EVENT_SUMMARY = "Cleaning"
_EVENT_DESCRIPTION = (
    "Estimated start time from the robot's cleaning schedule. Duration "
    "is a placeholder — actual cleaning time varies by mission."
)


def _to_calendar_event(
    start: dt_stdlib.datetime, end: dt_stdlib.datetime,
) -> CalendarEvent:
    return CalendarEvent(
        start=start, end=end,
        summary=_EVENT_SUMMARY, description=_EVENT_DESCRIPTION,
    )


class RoombaScheduleCalendar(IRobotEntity, CalendarEntity):
    """Read-only calendar of the robot's own cleaning schedule."""

    _attr_translation_key = "schedule"

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_schedule"

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming scheduled cleaning, if any."""
        now = dt_util.now()
        occurrences = parse_schedule_occurrences(
            self.vacuum_state, now, now + _NEXT_EVENT_LOOKAHEAD
        )
        future = [(s, e) for s, e in occurrences if s > now]
        if not future:
            return None
        start, end = min(future, key=lambda o: o[0])
        return _to_calendar_event(start, end)

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt_stdlib.datetime,
        end_date: dt_stdlib.datetime,
    ) -> list[CalendarEvent]:
        """Return every scheduled occurrence within [start_date, end_date)."""
        occurrences = parse_schedule_occurrences(
            self.vacuum_state, start_date, end_date
        )
        return [_to_calendar_event(s, e) for s, e in occurrences]

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """Only refresh on messages that actually touch the schedule —
        same gate as the existing sensor.*_next_clean sensor."""
        return "cleanSchedule2" in new_state or "cleanSchedule" in new_state


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule calendar for this Roomba.

    Unconditional — no filter_fn/capability gate (CAL plan §3): every
    robot tier can have a schedule, and an empty calendar for one that
    doesn't yet is normal HA behaviour, not an error state.
    """
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    async_add_entities([RoombaScheduleCalendar(roomba, blid)])
