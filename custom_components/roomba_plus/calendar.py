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

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
import datetime as dt_stdlib

from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry
from .schedule_parser import (
    DEFAULT_EVENT_DURATION,
    parse_schedule_occurrences_with_regions,
    parse_prime_schedule_occurrences,
)
from .select import resolve_zone_name

_LOGGER = logging.getLogger(__name__)

#: How often HA asks the calendar entities to update.
#:
#: Home Assistant's default is 30 seconds, and PrimeScheduleCalendar
#: makes TWO cloud calls per update -- schedules and room names. That is
#: about 5,760 requests a day for data that changes when somebody edits
#: a schedule in the iRobot app.
#:
#: Fifteen minutes instead. A schedule edited in the app appears within
#: a quarter of an hour rather than within thirty seconds, which nobody
#: is watching for, and the daily figure drops to 192.
#:
#: Found by the request-budget check, not by anything failing -- the
#: entity worked correctly the whole time.
#: Applies to the CLASSIC calendar only. Prime's is driven by
#: PrimeScheduleCoordinator, which runs on its own fifteen-minute cycle;
#: a module-level SCAN_INTERVAL has no effect on a non-polling entity,
#: and this one looked for two releases as though it did.
SCAN_INTERVAL = dt_stdlib.timedelta(minutes=15)
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
        """The occurrence happening right now, or else the next one.

        THE SAME BUG THE PRIME CALENDAR HAD, fixed here a release later
        (issue #23, @chairstacker). Home Assistant derives a calendar
        entity's on/off state from whether `event` returns something
        covering `now`, so a version that only ever looked at
        `start > now` reported "Off" for the ENTIRE duration of an
        active, schedule-triggered mission -- while the same schedule
        was plainly visible in the calendar view.

        It was fixed on PrimeScheduleCalendar and not here. The reporter
        has a Prime robot, so his symptom went away; every Classic user
        kept it. Third time in this project that a repair reached one
        of two sibling classes -- see the config_entry fix and the
        dict-versus-attribute reads.

        TWO CHANGES, not one. Checking for an ongoing occurrence is
        useless while the search window still starts at `now`: an
        occurrence that began ten minutes ago is simply not in the
        list. The window now reaches back by DEFAULT_EVENT_DURATION,
        exactly as the Prime version does.
        """
        now = dt_util.now()
        occurrences = parse_schedule_occurrences_with_regions(
            self.vacuum_state, now - DEFAULT_EVENT_DURATION,
            now + _NEXT_EVENT_LOOKAHEAD,
        )
        ongoing = [(s, e, r) for s, e, r in occurrences if s <= now < e]
        if ongoing:
            start, end, region_ids = min(ongoing, key=lambda o: o[0])
            return _to_calendar_event(start, end, self._zone_labels(region_ids))
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


#: Home Assistant recurrence rules this robot can actually express.
#:
#: The dialog allows far more than a Roomba does -- "every third Tuesday
#: until October" is a perfectly good RRULE and there is no schedule
#: frequency for it. Those are REFUSED rather than rounded down to
#: weekly: a rule quietly turned into something else is the failure this
#: project has spent the week chasing in other forms, and it would be
#: worse here because the robot would then run on days nobody chose.
_RRULE_TO_FREQUENCY: dict[str, str] = {
    "FREQ=WEEKLY": "WEEKLY",
    "FREQ=WEEKLY;INTERVAL=2": "BI_WEEKLY",
    "FREQ=MONTHLY": "MONTHLY",
}


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

    DRIVEN BY PrimeScheduleCoordinator, and that is a correction of a
    bug that made this entity permanently useless.

    It used to rely on "HA's own periodic polling (async_update(),
    default interval)". HA never called it. IRobotEntity sets
    `_attr_should_poll = False` -- correct for Classic, whose entities
    are driven by roombapy's MQTT push -- and this class inherits it.
    async_update() is the ONLY thing that fills _cached_occurrences, so
    the list stayed empty for the entity's whole life, `event` always
    returned None, and Home Assistant reported `off` forever.

    WHY IT SURVIVED SO LONG: the calendar VIEW was never broken.
    async_get_events() fetches directly with no cache, so the schedules
    were plainly visible while the entity state said off. That is
    exactly how it was reported (issue #23, @chairstacker), and the fix
    made at the time -- checking for an ongoing occurrence, widening the
    fetch window -- was correct and could not possibly take effect,
    because it reads a cache nobody fills.

    The module-level SCAN_INTERVAL is dead for the same reason: it
    applies to polling entities only. It looks like it does something.

    So this now subscribes to the coordinator that already fetches
    schedules every fifteen minutes for the schedule switches. One
    fetch for the account, and the lesson from the "Returning to dock"
    bug applied again: an entity has to subscribe to every coordinator
    that supplies its data.
    """

    _attr_translation_key = "schedule"

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(self, roomba=None, blid=blid, config_entry=config_entry)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_schedule"
        self._cached_occurrences: list[tuple[Any, Any, list[str], str | None]] = []
        self._cached_room_names: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        if coordinator is None:
            # No coordinator means no schedules to show. Refreshing once
            # would put a snapshot on screen that then never moves,
            # which is worse than an empty calendar.
            return

        self.async_on_remove(
            coordinator.async_add_listener(self._handle_coordinator_update)
        )

        # AND THE STATUS COORDINATOR, because this entity's on/off
        # depends on what the robot is DOING, not only on which
        # schedules exist.
        #
        # The schedule coordinator runs every fifteen minutes, which is
        # fine for something that changes when somebody edits it. The
        # phase check added in a25 reads the status coordinator -- and
        # nobody was listening to it, so the state could only move on
        # the fifteen-minute tick.
        #
        # @chairstacker's capture shows exactly that: a mission ran
        # 18:20 to 18:28, and the entity switched on at 18:28:23 and off
        # at 18:43:23. Fifteen minutes apart to the second, both edges
        # late. The logic was right and hung off a source that could not
        # reach it.
        status = getattr(
            self._config_entry.runtime_data, "prime_status_coordinator", None
        )
        if status is not None:
            self.async_on_remove(
                status.async_add_listener(self._handle_coordinator_update)
            )

        # A FAILED FIRST READ MUST COST THE CONTENT, NOT THE ENTITY.
        #
        # An exception raised here propagates out of async_added_to_hass
        # and Home Assistant does not add the entity at all -- so a
        # single malformed schedule would leave the user with no
        # calendar rather than an empty one, and no obvious way to tell
        # which happened.
        #
        # Not hypothetical: parse_prime_schedule_occurrences reads
        # values straight off the wire, and a null inside `commands`
        # crashed the schedule parser earlier in this same release.
        # The next refresh fills it in.
        try:
            await self.async_update()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: first schedule read failed for %s's calendar -- "
                "starting empty, the next refresh will fill it", self._blid,
                exc_info=True,
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """The coordinator refreshed; recompute from what it holds.

        Scheduled rather than awaited: a coordinator listener is
        synchronous, and the occurrence window has to be recomputed
        against the CURRENT time, not the time of the last refresh.
        """
        self.hass.async_create_task(self._async_recompute())

    async def _async_recompute(self) -> None:
        """Same reasoning as the first read: a refresh that raises must
        not take the entity down, and it runs in a background task where
        an exception would only surface as a stray traceback."""
        try:
            await self.async_update()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: schedule recompute failed for %s -- keeping the "
                "previous occurrences", self._blid, exc_info=True,
            )
            return
        self.async_write_ha_state()

    async def _fetch_room_names(self) -> dict[str, str]:
        """Room names from the shared coordinator, not a call of our own.

        This used to fetch get_active_map_versions() itself on every
        update, so the account made two cloud calls where one would do
        -- the schedule coordinator was already fetching the same map
        for the schedule switches, which need the names synchronously
        and cannot await anything.

        Still best-effort: an empty result means region ids show up
        unresolved rather than named, which is not an error. The
        schedule data this entity exists for does not depend on it.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        return dict(getattr(coordinator, "room_names", None) or {})

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
        real test.

        NOW READS THE SHARED COORDINATOR rather than calling
        get_schedules() itself. This calendar and the schedule switches
        want the same account-wide answer on the same fifteen-minute
        rhythm; two timers against one endpoint is one timer too many,
        and the switches needed a refresh source anyway (see
        PrimeScheduleCoordinator's docstring for the field report that
        forced that).

        The parsing note above still holds and is why the coordinator
        returns parsed HouseholdSchedule objects: SchedulesList.schedules
        is list[dict], and reading attributes off those raw dicts is a
        mistake this project has now made in four separate places.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        if coordinator is None or not coordinator.data:
            return []
        schedules = [
            schedule
            for _container_id, container in coordinator.data
            for schedule in container
        ]
        return parse_prime_schedule_occurrences(schedules, start, end)

    def _zone_labels(self, region_ids: list[str]) -> list[str]:
        return [self._cached_room_names.get(rid, f"Zone {rid}") for rid in region_ids]

    async def async_update(self) -> None:
        """HA's own periodic polling refreshes the cache `event` reads
        from -- see this class's own docstring for why polling (not
        push) is the right fit here.

        REAL BUG FOUND AND FIXED (this session, chairstacker: a
        schedule-triggered mission was actively running, but this
        calendar showed "Off" throughout): fetching from exactly `now`
        meant an occurrence that had ALREADY started today was pushed
        a full week ahead by _weekly_occurrences()'s own "if first <
        start: first += 7 days" logic (schedule_parser.py) -- it was
        never even in the returned occurrence list at all, not just
        filtered out afterwards. Fetching from `now - DEFAULT_EVENT_DURATION`
        instead means an occurrence that started up to one placeholder-
        duration ago is still included, so `event` (below) has a chance
        to recognize it as ongoing. Doesn't fully solve missions that
        run longer than DEFAULT_EVENT_DURATION (60 min) -- there's no
        real mission-duration data to fall back on here, only the
        existing fixed placeholder; see schedule_parser.py's own notes
        on that limitation."""
        now = dt_util.now()
        self._cached_room_names = await self._fetch_room_names()
        self._cached_occurrences = await self._fetch_occurrences(
            now - DEFAULT_EVENT_DURATION, now + _NEXT_EVENT_LOOKAHEAD
        )

    def _estimated_end(self, occurrence: tuple) -> Any:
        """When this occurrence is expected to finish.

        The parser gives every occurrence a flat hour, because a schedule
        says when it starts and never how long it takes. The robot does
        know: `/v1/time-estimates` returns a prediction per room and one
        for a whole mission, from its own cleaning history.

        So a schedule naming rooms gets the sum of those rooms, and one
        cleaning everywhere gets the whole-mission figure. Neither is
        exact -- they are predictions -- which is why the phase check
        below still overrides them: an estimate is about the future, a
        robot on its dock is about the present.

        The flat hour remains the fallback, for a robot that offers no
        estimates and for rooms it has not learned yet. It is a poor
        estimate rather than a wrong one, and it is what this did
        before.
        """
        start, end = occurrence[0], occurrence[1]
        region_ids = occurrence[2] if len(occurrence) > 2 else None
        estimates = getattr(
            self._config_entry.runtime_data, "prime_time_estimates", None
        )
        # Checked by SHAPE, not for None. runtime_data is a MagicMock
        # under test and something unexpected in the field, and either
        # sails past `is None` and then fails on arithmetic. What this
        # needs is a mapping of regions; anything else is no estimate.
        if not isinstance(getattr(estimates, "by_region", None), dict):
            return end

        from roombapy_prime.models import TimeEstimates  # noqa: PLC0415

        seconds = 0.0
        if region_ids:
            for rid in region_ids:
                best = TimeEstimates.best(estimates.by_region.get(str(rid)) or [])
                # ONE UNKNOWN ROOM DISCARDS THE WHOLE SUM. A partial
                # total would be confidently short, and an event that
                # ends too early is worse than one that ends too late:
                # it reports "no mission running" while the robot is
                # still working.
                if best is None or best.seconds is None:
                    return end
                seconds += best.seconds
        else:
            best = TimeEstimates.best(estimates.mission)
            if best is None or best.seconds is None:
                return end
            seconds = best.seconds

        if seconds <= 0:
            return end
        return start + dt_util.dt.timedelta(seconds=seconds)

    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
    )

    async def async_create_event(self, **kwargs: Any) -> None:
        """Creates a schedule from Home Assistant's own calendar dialog.

        WHY THIS EXISTS ALONGSIDE THE SERVICE. Nobody finds Developer
        Tools -> Actions; everybody finds a calendar with a plus button.
        The service is for people who already know what they want. This
        is how somebody learns the feature exists at all -- and the
        calendar already SHOWS schedules, so a user seeing one there
        reasonably expects to be able to add one.

        WHAT THE DIALOG CANNOT EXPRESS, and how each is handled:

          - THE END TIME. A schedule says when it starts and never how
            long it takes. Home Assistant insists on an end; it is
            ignored. Harmless, and the alternative is refusing every
            creation over a field the robot has no concept of.
          - ROOMS. No field for them, so the summary is read against the
            robot's own room list -- see calendar_rooms. Nothing
            recognised means the whole home, which is the common case
            and the one a plain title gives you.
          - WETNESS. Left out. Pulling a number out of prose is a
            different proposition from matching a name against a closed
            list, and it is the rarer setting.
          - RECURRENCE beyond what the robot has. Refused, not rounded.

        WHAT WAS UNDERSTOOD BECOMES THE EVENT, by a route worth being
        precise about: events are not stored, they are regenerated from
        the schedule on every read. So the summary a user sees afterwards
        is built from the room labels the robot itself uses, and their
        own wording is gone entirely.

        Someone typing "kuche und flur" gets back "Küche, Flur" -- a
        mis-read shows up at the place they typed it, without anything
        having to remember what they wrote.
        """
        from .calendar_rooms import (  # noqa: PLC0415
            AmbiguousRoomError,
            describe_match,
            match_rooms,
        )
        from .prime_schedule_services import async_create_schedule_from_calendar  # noqa: PLC0415

        start = kwargs.get("dtstart")
        if start is None:
            raise ServiceValidationError("A start time is required.")
        if not isinstance(start, dt_stdlib.datetime):
            raise ServiceValidationError(
                "All-day events cannot become schedules -- a robot needs a "
                "time of day to start at."
            )

        frequency = self._frequency_from_rrule(kwargs.get("rrule"))
        local_start = dt_util.as_local(start)
        summary = kwargs.get("summary") or ""

        try:
            room_ids = match_rooms(summary, self._room_names())
        except AmbiguousRoomError as err:
            raise ServiceValidationError(str(err)) from err

        await async_create_schedule_from_calendar(
            self.hass,
            self._config_entry,
            name=summary or None,
            weekday=local_start.weekday(),
            hour=local_start.hour,
            minute=local_start.minute,
            frequency=frequency,
            room_ids=room_ids,
            note=describe_match(room_ids, self._room_names()),
        )

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Rewrites a schedule from an edited calendar event.

        WITHOUT THIS, EDITING IS DELETE-AND-RECREATE. Home Assistant only
        offers a save button when the entity advertises UPDATE_EVENT, so
        a user opening a schedule to shift it by an hour previously had
        no way to do it -- which works, and which nobody expects.

        The edit replaces rather than patches, because that is what the
        dialog hands back: the whole event, including the title and
        description the user is looking at. Those are not their own
        words: events are regenerated from the schedule, so what the
        dialog opens with is already the canonical room labels.

        Which is what makes editing safe. "Küche, Flur" parses back to
        the same two rooms, so changing only the time does not silently
        drop the room selection -- the failure mode this would otherwise
        have.

        RECURRENCE EDITS OF A SINGLE OCCURRENCE ARE REFUSED. A robot
        schedule has no notion of "this one only": every occurrence comes
        from the same rule. Accepting the edit and applying it to all of
        them would change days the user did not choose.
        """
        from .calendar_rooms import (  # noqa: PLC0415
            AmbiguousRoomError,
            describe_match,
            match_rooms,
        )
        from .prime_schedule_services import (  # noqa: PLC0415
            async_update_schedule_from_calendar,
        )

        if recurrence_range == "THISEVENT":
            raise ServiceValidationError(
                "A robot schedule has no single occurrence to change -- every "
                "run comes from the same rule. Choose to edit all events, or "
                "delete this schedule and create another."
            )

        start = event.get("dtstart")
        if start is None:
            raise ServiceValidationError("A start time is required.")
        if not isinstance(start, dt_stdlib.datetime):
            raise ServiceValidationError(
                "All-day events cannot become schedules -- a robot needs a "
                "time of day to start at."
            )

        frequency = self._frequency_from_rrule(event.get("rrule"))
        local_start = dt_util.as_local(start)
        summary = event.get("summary") or ""
        # Both fields, same as create: a user types the rooms wherever it
        # suits them, and neither field is labelled for it.
        text = f"{summary} {event.get('description') or ''}"
        try:
            room_ids = match_rooms(text, self._room_names())
        except AmbiguousRoomError as err:
            raise ServiceValidationError(str(err)) from err

        await async_update_schedule_from_calendar(
            self.hass,
            self._config_entry,
            uid,
            name=summary or None,
            weekday=local_start.weekday(),
            hour=local_start.hour,
            minute=local_start.minute,
            frequency=frequency,
            room_ids=room_ids,
            note=describe_match(room_ids, self._room_names()),
        )

    async def async_delete_event(
        self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        """Deletes the schedule behind an event.

        The half that translates without loss: click, delete, gone. No
        fields, no assumptions, and it closes a real gap -- removing a
        schedule otherwise means finding its switch and calling a
        service.

        `recurrence_id` and `recurrence_range` are accepted and ignored:
        a robot schedule has no individual occurrences to delete, so
        deleting "this one" and "all of them" are the same act. Saying
        so is better than pretending to support a distinction that does
        not exist.
        """
        from .prime_schedule_services import async_delete_schedule_by_id  # noqa: PLC0415

        await async_delete_schedule_by_id(self.hass, self._config_entry, uid)

    @staticmethod
    def _frequency_from_rrule(rrule: str | None) -> str:
        """The robot frequency for a Home Assistant recurrence rule.

        A missing rule means ONCE -- a single-occurrence event is exactly
        what a one-off schedule is, and the server deletes a fired one by
        itself within minutes (confirmed in the field), so nothing is
        left behind.

        Anything the robot cannot express is refused. Rounding "every
        third Tuesday" down to weekly would run the robot on days nobody
        chose, and the user would have no way to tell from the calendar
        that it had happened.
        """
        if not rrule:
            return "ONCE"
        normalised = ";".join(
            part.strip().upper() for part in str(rrule).split(";") if part.strip()
        )
        # BYDAY is redundant here: the weekday comes from the start time,
        # and a rule naming the same day adds nothing to refuse over.
        normalised = ";".join(
            p for p in normalised.split(";") if not p.startswith("BYDAY=")
        )
        frequency = _RRULE_TO_FREQUENCY.get(normalised)
        if frequency is None:
            raise ServiceValidationError(
                f"This robot cannot express '{rrule}'. It supports weekly, "
                "every two weeks, monthly, and one-off schedules. For anything "
                "else, use the roomba_plus.create_schedule action."
            )
        return frequency

    def _room_names(self) -> dict[str, str]:
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        names = getattr(coordinator, "room_names", None)
        return names if isinstance(names, dict) else {}

    def _robot_has_stopped(self) -> bool:
        """Whether the robot is demonstrably not cleaning right now.

        Deliberately narrow: True only when the phase is one of the
        settled resting states. Anything else -- unknown, missing, a
        phase nobody has catalogued -- returns False and leaves the
        estimated window standing, because ending an event on a phase we
        do not understand would be worse than ending it late.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_status_coordinator", None
        )
        raw = (coordinator.data or {}).get("ro-currentstate") if coordinator else None
        if raw is None:
            return False
        status = raw.get("cleanMissionStatus") or {}
        phase = status.get("phase")
        # THE CYCLE DECIDES, NOT THE PHASE ALONE.
        #
        # A robot docks DURING a mission -- to wash its pad, to recharge
        # and resume -- and phase alone cannot tell that apart from
        # finishing. @chairstacker foresaw it before it was built, and
        # his own capture from two days earlier proves it:
        #
        #     phase=padWash  cycle=clean    mid-mission
        #     phase=charge   cycle=none     finished
        #
        # So a running cycle keeps the event open whatever the phase
        # says. Anything other than an explicitly idle cycle counts as
        # running: an unknown value should not end an event, for the
        # same reason an unknown phase does not.
        cycle = status.get("cycle")
        if cycle is not None and str(cycle) not in ("none", ""):
            return False
        mission = None
        del mission
        return phase in ("charge", "stop", "hmPostMsn", "hmUsrDock")

    @property
    def event(self) -> CalendarEvent | None:
        """REAL BUG FOUND AND FIXED (this session, chairstacker, see
        async_update()'s own docstring for the fetch-side half of this
        fix): this used to only ever consider occurrences with
        start > now, so a currently-ongoing occurrence (start in the
        past, end still ahead) could never be returned -- HA's own
        calendar "on/off" state comes directly from whether `event`
        returns something covering `now`, so this meant the entity
        showed "Off" for the ENTIRE duration of an active,
        schedule-triggered mission. Now checks for an ongoing occurrence
        (start <= now < end) first, falling back to the next upcoming
        one only if nothing is currently ongoing."""
        now = dt_util.now()
        # A SCHEDULED EVENT ENDS WHEN THE ROBOT STOPS, not when an
        # invented window runs out.
        #
        # A schedule says when it starts and never how long it takes, so
        # occurrences are given a flat hour. The robot is not obliged to
        # take one: @chairstacker ran a scheduled mission that finished
        # in minutes and watched this entity stay "On" long after the
        # robot had docked.
        #
        # An estimate is a guess about the future; a robot sitting on its
        # dock is an observation about the present, and the observation
        # wins. Only ONGOING occurrences are cut short this way -- an
        # upcoming one has not started, so a parked robot says nothing
        # about it.
        if self._robot_has_stopped():
            ongoing = []
        else:
            ongoing = [
                o for o in self._cached_occurrences
                if o[0] <= now < self._estimated_end(o)
            ]
        if ongoing:
            start, end, region_ids, name = min(ongoing, key=lambda o: o[0])
            return _to_calendar_event(start, end, self._zone_labels(region_ids))
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
