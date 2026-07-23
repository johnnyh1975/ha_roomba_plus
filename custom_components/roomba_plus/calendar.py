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

REAL UX GAP FOUND AND FIXED (later session): every event previously
showed a bare "Cleaning" summary regardless of tier, even though
SMART-tier (i/s/j-series) cleanSchedule2 entries carry a region
reference (cmd.regions) the SAME way SmartZoneSelect/zone_naming.py
already use to discover known zones — this data was always present,
just discarded here since this module originally only extracted time
occurrences. EPHEMERAL-tier (legacy cleanSchedule, 900/600-series)
genuinely has no region concept at all (no persistent map), so those
robots keep the plain "Cleaning" summary — not a remaining gap, a
correct reflection of what that tier can express. SMART-tier entries
that don't happen to reference a region (e.g. an explicit "whole
house" entry) also keep the plain summary, for the same reason.
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
from .models import ConnectionType, RoombaConfigEntry
from .schedule_parser import parse_schedule_occurrences_with_regions, parse_prime_schedule_occurrences
from .select import resolve_zone_name

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


def _event_summary(zone_labels: list[str]) -> str:
    """Bare "Cleaning" for whole-house (no zone_labels at all, whether
    because this tier has no region concept or this specific entry
    doesn't reference one) or "Cleaning: {label}" for one/more
    specific zones."""
    if not zone_labels:
        return _EVENT_SUMMARY
    return f"{_EVENT_SUMMARY}: {', '.join(zone_labels)}"


def _to_calendar_event(
    start: dt_stdlib.datetime, end: dt_stdlib.datetime, zone_labels: list[str],
) -> CalendarEvent:
    return CalendarEvent(
        start=start, end=end,
        summary=_event_summary(zone_labels), description=_EVENT_DESCRIPTION,
    )


class RoombaScheduleCalendar(IRobotEntity, CalendarEntity):
    """Read-only calendar of the robot's own cleaning schedule."""

    _attr_translation_key = "schedule"

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_schedule"

    def _zone_labels(self, region_ids: list[str]) -> list[str]:
        """Resolves region_ids into display names via the SAME
        priority chain SmartZoneSelect uses (resolve_zone_name(),
        select.py) -- no cloud_name here (this entity has no cloud
        coordinator access of its own), so falls to local_name/
        labels/auto-generated "Zone {id}", same as that entity's own
        non-cloud fallback path."""
        options = self._config_entry.options if self._config_entry is not None else {}
        from .const import CONF_SMART_ZONE_ALIASES
        aliases: dict = options.get(CONF_SMART_ZONE_ALIASES, {})
        zone_data: dict = options.get("smart_zone_data", {})
        labels: dict = options.get("smart_zone_labels", {})
        return [
            resolve_zone_name(
                rid, aliases, None, zone_data.get(rid, {}).get("name"), labels,
            )
            for rid in region_ids
        ]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming scheduled cleaning, if any."""
        now = dt_util.now()
        occurrences = parse_schedule_occurrences_with_regions(
            self.vacuum_state, now, now + _NEXT_EVENT_LOOKAHEAD
        )
        future = [(s, e, r) for s, e, r in occurrences if s > now]
        if not future:
            return None
        start, end, region_ids = min(future, key=lambda o: o[0])
        return _to_calendar_event(start, end, self._zone_labels(region_ids))

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt_stdlib.datetime,
        end_date: dt_stdlib.datetime,
    ) -> list[CalendarEvent]:
        """Return every scheduled occurrence within [start_date, end_date)."""
        occurrences = parse_schedule_occurrences_with_regions(
            self.vacuum_state, start_date, end_date
        )
        return [
            _to_calendar_event(s, e, self._zone_labels(region_ids))
            for s, e, region_ids in occurrences
        ]

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """Only refresh on messages that actually touch the schedule —
        same gate as the existing sensor.*_next_clean sensor."""
        return "cleanSchedule2" in new_state or "cleanSchedule" in new_state


class PrimeScheduleCalendar(IRobotEntity, CalendarEntity):
    """V4/Prime's own equivalent of RoombaScheduleCalendar, reading
    get_schedules() (REST) instead of a local vacuum_state dict.

    DELIBERATELY NOT reading the "rw-schedule" named shadow
    PrimeStatusCoordinator already watches -- that shadow's own
    content (ScheduleShadow, roombapy-prime's models/robot_info.py) is
    a DIFFERENT, more awkward representation (a raw cleanSchedule2
    array with each entry's own cmd as a STRING-serialized blob) than
    the already-confirmed, cleanly-typed HouseholdSchedule/
    ScheduleOptions from get_schedules() -- the same model this
    project's own verify-schedule-write already uses successfully.
    Simpler to fetch on demand here than to add string-blob parsing
    for a shadow that isn't otherwise needed for this feature.

    No push channel exists for schedule changes (unlike the shadow-
    based sensors) -- HA's own periodic polling (async_update(),
    default interval) is what keeps this current instead. Schedules
    change rarely, so this is a reasonable fit for polling rather than
    push.
    """

    _attr_translation_key = "schedule"

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(self, roomba=None, blid=blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_schedule"
        self._cached_occurrences: list[tuple[Any, Any, list[str], str | None]] = []
        self._cached_room_names: dict[str, str] = {}

    async def _fetch_room_names(self) -> dict[str, str]:
        """Best-effort: an empty result just means region_ids show up
        unresolved (e.g. "Zone 23") rather than a real name -- not
        treated as an error, since the schedule data itself (the main
        point of this entity) doesn't depend on it."""
        from roombapy_prime.models import build_room_name_map, parse_active_map_versions

        prime_robot = self._config_entry.runtime_data.prime_robot
        try:
            raw = await prime_robot.get_active_map_versions()
            return build_room_name_map(parse_active_map_versions(raw), blid=self._blid)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: could not resolve room names for %s's schedule calendar",
                self._blid, exc_info=True,
            )
            return {}

    async def _fetch_occurrences(
        self, start: dt_stdlib.datetime, end: dt_stdlib.datetime,
    ) -> list[tuple[dt_stdlib.datetime, dt_stdlib.datetime, list[str], str | None]]:
        """REAL BUG FOUND AND FIXED (caught before any real device
        test): get_schedules()'s real response shape is
        SchedulesResponse.household_schedules -> list[SchedulesList],
        each with its OWN .schedules -> list[dict] (raw dicts, NOT
        HouseholdSchedule instances -- SchedulesList's own docstring
        confirms this). An earlier version of this method read a
        "response.schedules" attribute that doesn't exist at all, and
        would have needed to parse each raw dict via
        HouseholdSchedule.from_json() regardless -- this would have
        silently returned zero occurrences for every real account,
        never raising, so nothing would have surfaced this without a
        real test."""
        household_id = self._config_entry.runtime_data.prime_household_id
        if household_id is None:
            return []
        prime_robot = self._config_entry.runtime_data.prime_robot
        try:
            response = await prime_robot.get_schedules(household_id)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "roomba_plus: get_schedules() failed for %s -- schedule calendar "
                "will show no data until this succeeds", self._blid, exc_info=True,
            )
            return []

        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        schedules = [
            HouseholdSchedule.from_json(raw)
            for schedules_list in (getattr(response, "household_schedules", None) or [])
            for raw in (getattr(schedules_list, "schedules", None) or [])
        ]
        return parse_prime_schedule_occurrences(schedules, start, end)

    def _zone_labels(self, region_ids: list[str]) -> list[str]:
        return [self._cached_room_names.get(rid, f"Zone {rid}") for rid in region_ids]

    async def async_update(self) -> None:
        """HA's own periodic polling refreshes the cache `event` reads
        from -- see this class's own docstring for why polling (not
        push) is the right fit here."""
        now = dt_util.now()
        self._cached_room_names = await self._fetch_room_names()
        self._cached_occurrences = await self._fetch_occurrences(now, now + _NEXT_EVENT_LOOKAHEAD)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        future = [o for o in self._cached_occurrences if o[0] > now]
        if not future:
            return None
        start, end, region_ids, name = min(future, key=lambda o: o[0])
        return _to_calendar_event(start, end, self._zone_labels(region_ids))

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt_stdlib.datetime,
        end_date: dt_stdlib.datetime,
    ) -> list[CalendarEvent]:
        room_names = await self._fetch_room_names()
        occurrences = await self._fetch_occurrences(start_date, end_date)
        return [
            _to_calendar_event(
                s, e, [room_names.get(rid, f"Zone {rid}") for rid in region_ids],
            )
            for s, e, region_ids, _name in occurrences
        ]


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
    data = config_entry.runtime_data

    if data.connection_type is ConnectionType.CLOUD_ONLY:
        async_add_entities([PrimeScheduleCalendar(data.blid, config_entry)])
        return

    roomba = data.roomba
    blid = data.blid
    async_add_entities([RoombaScheduleCalendar(roomba, blid, config_entry)])
