"""v3.4.0 CAL — tests for calendar.py's RoombaScheduleCalendar."""
from __future__ import annotations

import datetime

from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time


def _make_calendar(vacuum_state: dict | None = None, config_entry=None):
    """Minimal RoombaScheduleCalendar — bypasses IRobotEntity.__init__
    (no roombapy/device-registry setup needed for these tests), same
    pattern as other platform test files in this suite."""
    from custom_components.roomba_plus.calendar import RoombaScheduleCalendar

    cal = RoombaScheduleCalendar.__new__(RoombaScheduleCalendar)
    cal._blid = "TESTBLID"
    cal.vacuum_state = vacuum_state or {}
    if config_entry is None:
        config_entry = MagicMock()
        config_entry.options = {}
    cal._config_entry = config_entry
    return cal


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_always_creates_exactly_one_entity(self):
        """CAL plan §3 decision: unconditional, no capability gate —
        unlike image.py's Platform.IMAGE."""
        from custom_components.roomba_plus.calendar import async_setup_entry

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        added: list = []

        def _capture(entities):
            added.extend(entities)

        await async_setup_entry(hass, config_entry, _capture)

        assert len(added) == 1
        from custom_components.roomba_plus.calendar import RoombaScheduleCalendar
        assert isinstance(added[0], RoombaScheduleCalendar)


class TestUniqueId:
    def test_unique_id_follows_convention(self):
        cal = _make_calendar()
        cal._attr_unique_id = f"{cal.robot_unique_id}_schedule"
        assert cal._attr_unique_id == "roomba_plus_TESTBLID_schedule"


class TestEventProperty:
    def test_no_schedule_returns_none(self):
        cal = _make_calendar({})
        assert cal.event is None

    def test_returns_next_upcoming_event(self):
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [2]}},  # Tue
            ],
        }
        cal = _make_calendar(state)
        with freeze_time("2026-07-06 08:00:00"):  # Monday 2026-07-06, 08:00 UTC
            event = cal.event
        assert event is not None
        assert event.start.weekday() == 1  # Tuesday
        assert event.start.hour == 9
        assert event.summary == "Cleaning"
        assert "Estimated" in event.description

    def test_a_running_occurrence_is_the_event(self):
        """THE BUG FROM ISSUE #23, and this test used to assert it.

        It read: "an occurrence exactly at (or before) now is not 'the
        next one'", borrowing sensor.*_next_clean's strictly-future
        semantics. That is right for a "next clean" sensor and wrong
        for a calendar: Home Assistant derives a calendar entity's
        on/off state from whether `event` covers `now`, so the strict
        rule made the entity report "Off" for the whole duration of a
        running, schedule-triggered mission.

        Reported on a Prime robot, fixed there, and left here for a
        release -- so every Classic user kept the symptom while the
        reporter's went away.
        """
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 8, "min": 0, "day": [2]}},  # Tue 08:00
            ],
        }
        cal = _make_calendar(state)

        with freeze_time("2026-07-07 08:30:00"):  # half an hour in
            event = cal.event

        assert event is not None
        assert event.start == datetime.datetime(
            2026, 7, 7, 8, 0, tzinfo=datetime.timezone.utc
        )

    def test_the_next_one_is_returned_when_nothing_is_running(self):
        """The other half: outside any occurrence, the upcoming one."""
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 8, "min": 0, "day": [2]}},
            ],
        }
        cal = _make_calendar(state)

        with freeze_time("2026-07-07 10:00:00"):  # an hour after it ended
            event = cal.event

        assert event is not None
        assert event.start > datetime.datetime(
            2026, 7, 7, 8, 0, tzinfo=datetime.timezone.utc
        )

    def test_disabled_schedule_entries_produce_no_event(self):
        state = {
            "cleanSchedule2": [
                {"enabled": False, "start": {"hour": 9, "min": 0, "day": [2]}},
            ],
        }
        cal = _make_calendar(state)
        with freeze_time("2026-07-06 08:00:00"):
            assert cal.event is None


class TestAsyncGetEvents:
    @pytest.mark.asyncio
    async def test_returns_all_occurrences_in_range(self):
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [2, 5]}},  # Tue+Fri
            ],
        }
        cal = _make_calendar(state)
        start = datetime.datetime(2026, 7, 6, 0, 0, tzinfo=datetime.timezone.utc)  # Monday
        end = start + datetime.timedelta(weeks=1)
        events = await cal.async_get_events(MagicMock(), start, end)
        assert len(events) == 2
        weekdays = sorted(e.start.weekday() for e in events)
        assert weekdays == [1, 4]  # Tuesday, Friday

    @pytest.mark.asyncio
    async def test_empty_schedule_returns_empty_list(self):
        cal = _make_calendar({})
        start = datetime.datetime(2026, 7, 6, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(weeks=1)
        events = await cal.async_get_events(MagicMock(), start, end)
        assert events == []

    @pytest.mark.asyncio
    async def test_events_have_placeholder_duration(self):
        from custom_components.roomba_plus.schedule_parser import (
            DEFAULT_EVENT_DURATION,
        )
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [2]}},
            ],
        }
        cal = _make_calendar(state)
        start = datetime.datetime(2026, 7, 6, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(weeks=1)
        events = await cal.async_get_events(MagicMock(), start, end)
        assert events[0].end - events[0].start == DEFAULT_EVENT_DURATION

    @pytest.mark.asyncio
    async def test_prefers_schedule2_over_legacy(self):
        """Same precedence as sensor.*_next_clean and schedule_parser.py's
        parse_schedule_occurrences()."""
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [2]}},
            ],
            "cleanSchedule": {
                "cycle": ["none"] * 7, "h": [0] * 7, "m": [0] * 7,
            },
        }
        cal = _make_calendar(state)
        start = datetime.datetime(2026, 7, 6, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(weeks=1)
        events = await cal.async_get_events(MagicMock(), start, end)
        assert len(events) == 1
        assert events[0].start.hour == 9


class TestNewStateFilter:
    def test_true_for_schedule2_update(self):
        cal = _make_calendar()
        assert cal.new_state_filter({"cleanSchedule2": []}) is True

    def test_true_for_legacy_schedule_update(self):
        cal = _make_calendar()
        assert cal.new_state_filter({"cleanSchedule": {}}) is True

    def test_false_for_unrelated_update(self):
        cal = _make_calendar()
        assert cal.new_state_filter({"cleanMissionStatus": {}}) is False


class TestZoneLabelsInSummary:
    """REAL UX GAP FOUND AND FIXED: cleanSchedule2 entries carry a
    region reference (cmd.regions) the same way SmartZoneSelect already
    uses to discover known zones -- this was always present but
    discarded here, showing a bare "Cleaning" for every event
    regardless of which zone (if any) it actually targets."""

    @pytest.mark.asyncio
    async def test_smart_tier_entry_with_region_shows_zone_label(self):
        """SMART tier (i/s/j-series): a schedule entry referencing a
        region_id must resolve to its user-assigned label in the
        event summary."""
        state = {
            "cleanSchedule2": [
                {
                    "enabled": True,
                    "start": {"hour": 8, "min": 0, "day": [1]},
                    "cmd": {"regions": [{"region_id": "23", "type": "rid"}]},
                },
            ],
        }
        config_entry = MagicMock()
        config_entry.options = {"smart_zone_labels": {"23": "Kitchen"}}
        cal = _make_calendar(state, config_entry=config_entry)
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)  # a Monday
        end = start + datetime.timedelta(days=1)

        events = await cal.async_get_events(MagicMock(), start, end)

        assert len(events) == 1
        assert events[0].summary == "Cleaning: Kitchen"

    @pytest.mark.asyncio
    async def test_smart_tier_entry_without_region_shows_plain_summary(self):
        """A SMART-tier entry with no region reference at all means
        "whole house" -- must NOT show a zone label just because the
        tier is capable of having one."""
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}},
            ],
        }
        cal = _make_calendar(state)
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=1)

        events = await cal.async_get_events(MagicMock(), start, end)

        assert len(events) == 1
        assert events[0].summary == "Cleaning"

    @pytest.mark.asyncio
    async def test_ephemeral_tier_legacy_schedule_never_shows_zone_label(self):
        """EPHEMERAL tier (legacy cleanSchedule, 900/600-series) has no
        region concept at all (no persistent map) -- must always show
        the plain summary, never attempt zone resolution."""
        state = {
            "cleanSchedule": {"cycle": ["start"] * 7, "h": [8] * 7, "m": [0] * 7},
        }
        config_entry = MagicMock()
        config_entry.options = {"smart_zone_labels": {"23": "Kitchen"}}  # irrelevant here
        cal = _make_calendar(state, config_entry=config_entry)
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=1)

        events = await cal.async_get_events(MagicMock(), start, end)

        assert len(events) == 1
        assert events[0].summary == "Cleaning"

    @pytest.mark.asyncio
    async def test_unlabelled_region_falls_back_to_auto_generated_name(self):
        """A region_id with no user-assigned label yet still shows
        SOMETHING useful (matching SmartZoneSelect's own "Zone {id}"
        fallback), rather than silently omitting it."""
        state = {
            "cleanSchedule2": [
                {
                    "enabled": True,
                    "start": {"hour": 8, "min": 0, "day": [1]},
                    "cmd": {"regions": [{"region_id": "99", "type": "rid"}]},
                },
            ],
        }
        cal = _make_calendar(state)  # no labels configured at all
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=1)

        events = await cal.async_get_events(MagicMock(), start, end)

        assert len(events) == 1
        assert events[0].summary == "Cleaning: Zone 99"

    @pytest.mark.asyncio
    async def test_multiple_regions_in_one_entry_shows_all_labels(self):
        """cmd.regions is a list -- a single schedule entry CAN target
        more than one zone at once. All of them must appear in the
        summary, not just the first."""
        state = {
            "cleanSchedule2": [
                {
                    "enabled": True,
                    "start": {"hour": 8, "min": 0, "day": [1]},
                    "cmd": {"regions": [
                        {"region_id": "23", "type": "rid"},
                        {"region_id": "24", "type": "rid"},
                    ]},
                },
            ],
        }
        config_entry = MagicMock()
        config_entry.options = {
            "smart_zone_labels": {"23": "Kitchen", "24": "Living Room"},
        }
        cal = _make_calendar(state, config_entry=config_entry)
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=1)

        events = await cal.async_get_events(MagicMock(), start, end)

        assert len(events) == 1
        assert events[0].summary == "Cleaning: Kitchen, Living Room"


def _make_prime_calendar(prime_household_id="hh1", prime_robot=None,
                         schedule_containers=None):
    """Minimal PrimeScheduleCalendar — bypasses IRobotEntity.__init__,
    same pattern as _make_calendar() above.

    `schedule_containers` feeds PrimeScheduleCoordinator.data, which is
    where this calendar now reads its schedules from. It used to call
    get_schedules() itself; the switches needed a refresh source anyway,
    and two timers against one endpoint for one account was one too
    many. Coordinator data is parsed HouseholdSchedule objects, not the
    raw dicts the library returns."""
    from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

    cal = PrimeScheduleCalendar.__new__(PrimeScheduleCalendar)
    cal._blid = "TESTBLID"
    config_entry = MagicMock()
    config_entry.runtime_data.prime_household_id = prime_household_id
    config_entry.runtime_data.prime_robot = prime_robot or MagicMock()
    coordinator = MagicMock()
    coordinator.data = schedule_containers
    config_entry.runtime_data.prime_schedule_coordinator = coordinator
    cal._config_entry = config_entry
    cal._cached_occurrences = []
    cal._cached_room_names = {}
    return cal


class TestPrimeScheduleCalendarFetchOccurrences:
    @pytest.mark.asyncio
    async def test_no_household_id_returns_empty_without_calling_get_schedules(self):
        """No household_id resolved yet (e.g. get_household_id() failed
        during setup) -- must degrade to "no data", never attempt the
        call with None."""
        cal = _make_prime_calendar(prime_household_id=None)

        result = await cal._fetch_occurrences(
            datetime.datetime(2026, 7, 20), datetime.datetime(2026, 7, 27)
        )

        assert result == []
        cal._config_entry.runtime_data.prime_robot.get_schedules.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_schedules_failure_returns_empty_not_raises(self):
        prime_robot = MagicMock()
        prime_robot.get_schedules = AsyncMock(side_effect=RuntimeError("simulated"))
        cal = _make_prime_calendar(prime_robot=prime_robot)

        result = await cal._fetch_occurrences(
            datetime.datetime(2026, 7, 20), datetime.datetime(2026, 7, 27)
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_successful_fetch_parses_real_schedules(self):
        """Uses the REAL confirmed response shape: SchedulesResponse.
        household_schedules -> list[SchedulesList], each with its own
        .schedules -> list[dict] (raw dicts, parsed here via
        HouseholdSchedule.from_json() -- not already-parsed
        HouseholdSchedule instances, which an earlier, incorrect
        version of both this test and the code it exercises assumed)."""
        schedule_raw = {
            "schedule_id": "hs1",
            "options": {
                "name": "Kitchen",
                "enabled": True,
                "deleted": False,
                "frequency": "WEEKLY",
                "start": {"day": [2], "hour": 8, "min": 0},
                "commands": [{"regions": [{"region_id": "23", "type": "rid"}]}],
            },
        }
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        prime_robot = MagicMock()
        cal = _make_prime_calendar(
            prime_robot=prime_robot,
            schedule_containers=[("hs1", [HouseholdSchedule.from_json(schedule_raw)])],
        )

        result = await cal._fetch_occurrences(
            datetime.datetime(2026, 7, 20), datetime.datetime(2026, 7, 27)
        )

        assert len(result) == 1
        # The calendar must not make its own cloud call any more: one
        # fetch for the account, read by the calendar and every schedule
        # switch alike.
        prime_robot.get_schedules.assert_not_called()


class TestPrimeScheduleCalendarRoomNames:
    @pytest.mark.asyncio
    async def test_get_active_map_versions_failure_returns_empty_not_raises(self):
        prime_robot = MagicMock()
        prime_robot.get_active_map_versions = AsyncMock(side_effect=RuntimeError("simulated"))
        cal = _make_prime_calendar(prime_robot=prime_robot)

        result = await cal._fetch_room_names()

        assert result == {}


class TestPrimeScheduleCalendarAsyncUpdate:
    @pytest.mark.asyncio
    @freeze_time("2026-07-23 16:05:00")
    async def test_fetches_with_start_shifted_back_by_default_event_duration(self):
        """REAL BUG (this session, chairstacker) -- see async_update()'s
        own docstring. Fetching from exactly `now` meant an occurrence
        that already started today was pushed a full week ahead by
        _weekly_occurrences()'s own logic, never even entering the
        returned list. Confirms the fetch window's start is shifted
        back, not just that `event` handles ongoing occurrences once
        they're already in the cache (covered by TestPrimeScheduleCalendarEvent)."""
        from custom_components.roomba_plus.schedule_parser import DEFAULT_EVENT_DURATION

        cal = _make_prime_calendar()
        cal._fetch_room_names = AsyncMock(return_value={})
        cal._fetch_occurrences = AsyncMock(return_value=[])

        await cal.async_update()

        called_start, called_end = cal._fetch_occurrences.call_args.args
        expected_start = datetime.datetime(2026, 7, 23, 16, 5, tzinfo=datetime.timezone.utc) - DEFAULT_EVENT_DURATION
        assert called_start == expected_start


class TestPrimeScheduleCalendarEvent:
    def test_event_returns_none_when_no_future_occurrences_cached(self):
        cal = _make_prime_calendar()
        cal._cached_occurrences = []

        assert cal.event is None

    @freeze_time("2026-07-20 00:00:00")
    def test_event_uses_zone_label_from_cached_room_names(self):
        cal = _make_prime_calendar()
        future = datetime.datetime(2026, 7, 21, 8, 0, tzinfo=datetime.timezone.utc)
        cal._cached_occurrences = [
            (future, future + datetime.timedelta(hours=1), ["23"], "Kitchen schedule"),
        ]
        cal._cached_room_names = {"23": "Kitchen"}

        event = cal.event

        assert event is not None
        assert event.summary == "Cleaning: Kitchen"

    @freeze_time("2026-07-20 00:00:00")
    def test_event_falls_back_to_zone_id_label_when_unnamed(self):
        cal = _make_prime_calendar()
        future = datetime.datetime(2026, 7, 21, 8, 0, tzinfo=datetime.timezone.utc)
        cal._cached_occurrences = [
            (future, future + datetime.timedelta(hours=1), ["99"], None),
        ]
        cal._cached_room_names = {}

        event = cal.event

        assert event is not None
        assert event.summary == "Cleaning: Zone 99"

    @freeze_time("2026-07-23 16:05:00")
    def test_event_returns_ongoing_occurrence_not_just_future_ones(self):
        """REAL BUG (this session, chairstacker): a schedule-triggered
        mission was actively running (started 16:05, 15 minutes prior),
        but this calendar showed "Off" throughout -- event() used to
        only ever look for start > now, so an already-started
        occurrence was invisible to it regardless of whether it was
        still ongoing."""
        cal = _make_prime_calendar()
        started = datetime.datetime(2026, 7, 23, 15, 50, tzinfo=datetime.timezone.utc)
        cal._cached_occurrences = [
            (started, started + datetime.timedelta(hours=1), ["5"], "Living room clean"),
        ]
        cal._cached_room_names = {"5": "Living Room"}

        event = cal.event

        assert event is not None
        assert event.summary == "Cleaning: Living Room"

    @freeze_time("2026-07-23 16:05:00")
    def test_event_prefers_ongoing_occurrence_over_a_later_future_one(self):
        cal = _make_prime_calendar()
        started = datetime.datetime(2026, 7, 23, 15, 50, tzinfo=datetime.timezone.utc)
        later = datetime.datetime(2026, 7, 24, 8, 0, tzinfo=datetime.timezone.utc)
        cal._cached_occurrences = [
            (started, started + datetime.timedelta(hours=1), ["5"], "Ongoing"),
            (later, later + datetime.timedelta(hours=1), ["9"], "Tomorrow"),
        ]
        cal._cached_room_names = {}

        event = cal.event

        assert event is not None
        assert event.summary == "Cleaning: Zone 5"

    @freeze_time("2026-07-23 17:30:00")
    def test_event_falls_back_to_future_once_ongoing_occurrence_has_ended(self):
        cal = _make_prime_calendar()
        ended = datetime.datetime(2026, 7, 23, 15, 50, tzinfo=datetime.timezone.utc)
        later = datetime.datetime(2026, 7, 24, 8, 0, tzinfo=datetime.timezone.utc)
        cal._cached_occurrences = [
            (ended, ended + datetime.timedelta(hours=1), ["5"], "Already over"),
            (later, later + datetime.timedelta(hours=1), ["9"], "Tomorrow"),
        ]
        cal._cached_room_names = {}

        event = cal.event

        assert event is not None
        assert event.summary == "Cleaning: Zone 9"


class TestAsyncSetupEntryRoutesByConnectionType:
    @pytest.mark.asyncio
    async def test_cloud_only_creates_prime_schedule_calendar(self):
        from custom_components.roomba_plus.calendar import async_setup_entry
        from custom_components.roomba_plus.models import ConnectionType

        config_entry = MagicMock()
        config_entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        config_entry.runtime_data.blid = "BLID123"
        added = []

        await async_setup_entry(MagicMock(), config_entry, lambda entities, **kw: added.extend(entities))

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar
        assert len(added) == 1
        assert isinstance(added[0], PrimeScheduleCalendar)


class TestThePrimeCalendarIsDrivenByTheCoordinator:
    """The reason the entity said "off" for its entire existence.

    IRobotEntity sets `_attr_should_poll = False` -- right for Classic,
    whose entities are driven by roombapy's MQTT push -- and this class
    inherits it. `async_update()` is the only thing that fills
    `_cached_occurrences`, and Home Assistant never called it. The list
    stayed empty, `event` returned None, and the state was permanently
    off.

    THE CALENDAR VIEW WAS NEVER BROKEN, which is why it survived:
    `async_get_events()` fetches directly with no cache, so the
    schedules were visible on screen the whole time. Reported exactly
    that way (issue #23) -- "the same schedule was plainly visible in
    the calendar view".

    The fix made then -- check for an ongoing occurrence, widen the
    fetch window -- was correct and could not take effect, because it
    reads a cache nobody fills. Two releases of a fix that ran on empty
    input.
    """

    def _calendar(self, containers, occurrences=None):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        entry = MagicMock()
        entry.runtime_data.blid = "BLID"
        entry.runtime_data.prime_household_id = "HH"
        coordinator = MagicMock()
        coordinator.data = containers
        listeners: list = []
        coordinator.async_add_listener = MagicMock(
            side_effect=lambda cb: listeners.append(cb) or (lambda: None)
        )
        entry.runtime_data.prime_schedule_coordinator = coordinator

        cal = PrimeScheduleCalendar.__new__(PrimeScheduleCalendar)
        cal._blid = "BLID"
        cal._config_entry = entry
        cal._cached_occurrences = []
        cal._cached_room_names = {}
        cal._fetch_room_names = AsyncMock(return_value={})
        return cal, coordinator, listeners

    @pytest.mark.asyncio
    async def test_the_occurrence_cache_is_filled_from_the_coordinator(self):
        """The whole bug in one assertion: this list used to stay empty
        forever."""
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        schedule = HouseholdSchedule.from_json({
            "schedule_id": "s1",
            "options": {
                "enabled": True, "name": "W", "frequency": "WEEKLY",
                "start": {"day": [1], "hour": 9, "min": 0},
            },
        })
        cal, _coordinator, _listeners = self._calendar([("c1", [schedule])])

        await cal.async_update()

        assert cal._cached_occurrences

    @pytest.mark.asyncio
    async def test_it_does_not_poll(self):
        """Not a regression to guard against -- a fact to pin down. If
        someone ever sets this to True, `async_update` starts making
        cloud calls from a polling entity, which
        scripts/check_request_budget.py forbids and should."""
        cal, _c, _l = self._calendar([])

        assert cal.should_poll is False

    @pytest.mark.asyncio
    async def test_a_refresh_recomputes_and_writes_state(self):
        from unittest.mock import AsyncMock, MagicMock

        cal, _coordinator, listeners = self._calendar([])
        cal.hass = MagicMock()
        cal.async_write_ha_state = MagicMock()
        cal._async_recompute = AsyncMock()

        cal._handle_coordinator_update()

        cal.hass.async_create_task.assert_called_once()
        # The task is created, not awaited here -- close the coroutine
        # so the test does not leave one unawaited.
        cal.hass.async_create_task.call_args.args[0].close()


class TestAFailedReadDoesNotCostTheCalendarEntity:
    """Found in the a21 bug hunt.

    An exception in `async_update()` propagated out of
    `async_added_to_hass`, so Home Assistant would not add the entity at
    all -- a single malformed schedule would leave the user with no
    calendar rather than an empty one.

    Not hypothetical: a null inside `commands` crashed the schedule
    parser earlier in this same release.
    """

    def _calendar(self, update_effect=None):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        entry = MagicMock()
        entry.runtime_data.blid = "B"
        coordinator = MagicMock()
        coordinator.data = []
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        entry.runtime_data.prime_schedule_coordinator = coordinator

        cal = PrimeScheduleCalendar.__new__(PrimeScheduleCalendar)
        cal._blid = "B"
        cal._config_entry = entry
        cal._cached_occurrences = []
        cal._cached_room_names = {}
        cal.async_on_remove = MagicMock()
        cal.async_write_ha_state = MagicMock()
        cal.async_update = AsyncMock(side_effect=update_effect)
        return cal

    @pytest.mark.asyncio
    async def test_the_entity_is_still_added_when_the_first_read_fails(self):
        from unittest.mock import AsyncMock, patch

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = self._calendar(update_effect=RuntimeError("cloud down"))

        with patch.object(PrimeScheduleCalendar.__mro__[1],
                          "async_added_to_hass", new=AsyncMock()):
            await cal.async_added_to_hass()  # must not raise

        assert cal._cached_occurrences == []

    @pytest.mark.asyncio
    async def test_a_failed_recompute_keeps_the_previous_occurrences(self):
        """It runs in a background task, where an exception would only
        surface as a stray traceback."""
        cal = self._calendar(update_effect=RuntimeError("boom"))
        cal._cached_occurrences = ["previous"]

        await cal._async_recompute()

        assert cal._cached_occurrences == ["previous"]
        cal.async_write_ha_state.assert_not_called()


class TestCalendarEventsCanBeEdited:
    """Without UPDATE_EVENT, Home Assistant shows no save button, so
    editing a schedule meant deleting it and creating another -- which
    works, and which nobody expects.

    The edit REPLACES rather than patches, because that is what the
    dialog hands back: the whole event, including the title and
    description the user is looking at. Those are the ones written back
    after the last edit, which is why round-tripping them matters.
    """

    def _calendar(self, rooms=None):
        import datetime as dt_stdlib
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        cal.hass = MagicMock()
        cal._config_entry = MagicMock()
        cal._room_names = lambda: (
            {"13": "Küche", "10": "Flur"} if rooms is None else rooms
        )
        cal._sent = AsyncMock()
        del dt_stdlib
        return cal

    async def _update(self, cal, event, uid="S1", **kwargs):
        from unittest.mock import patch

        from custom_components.roomba_plus import prime_schedule_services as svc

        with patch.object(
            svc, "async_update_schedule_from_calendar", cal._sent
        ):
            await cal.async_update_event(uid, event, **kwargs)

    def _event(self, **kwargs):
        import datetime as dt

        base = {
            "summary": "Küche, Flur",
            "dtstart": dt.datetime(2026, 8, 4, 9, 30, tzinfo=dt.UTC),
            "rrule": "FREQ=WEEKLY",
        }
        base.update(kwargs)
        return base

    def test_the_entity_advertises_editing(self):
        from homeassistant.components.calendar import CalendarEntityFeature

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        # A cached_property, so neither a plain attribute nor one with
        # `.fget` -- read it off an instance.
        features = self._calendar().supported_features

        assert features & CalendarEntityFeature.UPDATE_EVENT
        assert features & CalendarEntityFeature.CREATE_EVENT
        assert features & CalendarEntityFeature.DELETE_EVENT

    @pytest.mark.asyncio
    async def test_rooms_survive_a_time_change(self):
        """The failure mode this design exists to avoid: a user who
        changes only the hour must not silently lose their rooms."""
        cal = self._calendar()

        await self._update(cal, self._event())

        assert cal._sent.await_args.kwargs["room_ids"] == ["13", "10"]

    @pytest.mark.asyncio
    async def test_rooms_are_read_from_the_description_too(self):
        cal = self._calendar()

        await self._update(cal, self._event(summary="Morning", description="Flur"))

        assert cal._sent.await_args.kwargs["room_ids"] == ["10"]

    @pytest.mark.asyncio
    async def test_the_new_time_is_passed_through(self):
        cal = self._calendar()

        await self._update(cal, self._event())

        kwargs = cal._sent.await_args.kwargs
        assert (kwargs["hour"], kwargs["minute"]) == (9, 30)

    @pytest.mark.asyncio
    async def test_editing_one_occurrence_is_refused(self):
        """A robot schedule has no notion of "this one only": every run
        comes from the same rule. Applying such an edit to all of them
        would change days the user did not choose."""
        from homeassistant.exceptions import ServiceValidationError

        cal = self._calendar()

        with pytest.raises(ServiceValidationError, match="single occurrence"):
            await self._update(cal, self._event(), recurrence_range="THISEVENT")
        cal._sent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unsupported_recurrence_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        cal = self._calendar()

        with pytest.raises(ServiceValidationError):
            await self._update(cal, self._event(rrule="FREQ=DAILY"))
        cal._sent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_all_day_edit_is_refused(self):
        import datetime as dt

        from homeassistant.exceptions import ServiceValidationError

        cal = self._calendar()

        with pytest.raises(ServiceValidationError):
            await self._update(cal, self._event(dtstart=dt.date(2026, 8, 4)))


class TestTheEventShowsWhatWasUnderstood:
    """Events are not stored, they are regenerated from the schedule on
    every read. So a user who types "kuche und flur" sees "Küche, Flur"
    afterwards, and their own wording is gone entirely -- a mis-read
    shows up at the place they typed it, without anything having to
    remember what they wrote.

    That is also what makes editing safe: the dialog opens on the
    canonical labels, which parse back to the same rooms.
    """

    _ROOMS = {"13": "Küche", "10": "Flur"}

    def test_the_summary_is_built_from_room_labels(self):
        """Read off the produced event, not out of the source: a summary
        assertion that matches a call expression passes on any spelling
        and fails on a reformat."""
        import datetime as dt

        from custom_components.roomba_plus.calendar import _to_calendar_event

        start = dt.datetime(2026, 8, 6, 9, 0, tzinfo=dt.UTC)
        event = _to_calendar_event(start, start, ["Küche", "Flur"])

        assert "Küche" in event.summary
        assert "Flur" in event.summary

    def test_typed_text_and_canonical_text_give_the_same_rooms(self):
        """The round trip that editing depends on."""
        from custom_components.roomba_plus.calendar_rooms import (
            describe_match,
            match_rooms,
        )

        typed = match_rooms("kuche und flur", self._ROOMS)
        canonical = describe_match(typed, self._ROOMS)

        assert match_rooms(canonical, self._ROOMS) == typed

    def test_a_whole_house_schedule_stays_whole_house(self):
        """Nothing recognised must not become something recognised on
        the way back."""
        from custom_components.roomba_plus.calendar_rooms import (
            describe_match,
            match_rooms,
        )

        empty = match_rooms("Tuesday clean", self._ROOMS)
        canonical = describe_match(empty, self._ROOMS)

        assert empty == []
        assert match_rooms(canonical, self._ROOMS) == []


class TestTheCalendarFollowsTheRobotNotOnlyTheSchedule:
    """Two coordinators, and both matter.

    The schedule coordinator says WHICH schedules exist and runs every
    fifteen minutes, which is fine for something that changes only when
    somebody edits it. The status coordinator says what the robot is
    DOING, and this entity's on/off depends on it.

    Listening to the schedule coordinator alone meant the phase check
    read a value nobody was watching. @chairstacker's capture shows it:
    a mission ran 18:20 to 18:28, and the entity switched on at 18:28:23
    and off at 18:43:23 -- fifteen minutes apart to the second, both
    edges late.
    """

    def _added(self, *, status=True, schedule=True):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        listened = []

        def _coordinator(tag):
            coordinator = MagicMock()
            coordinator.async_add_listener.side_effect = (
                lambda cb: listened.append(tag) or (lambda: None)
            )
            return coordinator

        cal._config_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                prime_schedule_coordinator=_coordinator("schedule") if schedule else None,
                prime_status_coordinator=_coordinator("status") if status else None,
            )
        )
        cal.async_on_remove = MagicMock()
        with patch.object(
            type(cal).__mro__[1], "async_added_to_hass", new=AsyncMock()
        ):
            asyncio.run(PrimeScheduleCalendar.async_added_to_hass(cal))
        return listened

    def test_both_coordinators_are_listened_to(self):
        assert set(self._added()) == {"schedule", "status"}

    def test_a_missing_status_coordinator_does_not_stop_the_rest(self):
        """The schedule half still works; only the phase check goes
        blind, which is where this was before."""
        assert self._added(status=False) == ["schedule"]

    def test_without_schedules_there_is_nothing_to_watch_for(self):
        """No schedule coordinator means no occurrences to show, so
        following the robot's phase would answer a question nobody
        asked. The early return is correct and this pins it."""
        assert self._added(schedule=False) == []


class TestAnInterimDockDoesNotEndTheEvent:
    """A robot docks DURING a mission -- to wash its pad, to recharge and
    resume -- and the phase alone cannot tell that apart from finishing.

    @chairstacker foresaw this before it was built, and his own capture
    from two days earlier proves it:

        phase=padWash  cycle=clean    mid-mission
        phase=charge   cycle=none     finished
    """

    def _stopped(self, phase, cycle):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator.data = {
            "ro-currentstate": {
                "cleanMissionStatus": {"phase": phase, "cycle": cycle}
            }
        }
        cal._config_entry = entry
        return cal._robot_has_stopped()

    def test_a_pad_wash_mid_mission_keeps_the_event_open(self):
        """His exact capture."""
        assert self._stopped("padWash", "clean") is False

    def test_charging_mid_mission_keeps_it_open_too(self):
        """A robot that returns to recharge and resumes is still on the
        same mission -- the case that would otherwise look most like
        finishing."""
        assert self._stopped("charge", "clean") is False

    def test_an_idle_cycle_on_the_dock_ends_it(self):
        assert self._stopped("charge", "none") is True

    def test_an_unknown_cycle_does_not_end_it(self):
        """For the same reason an unknown phase does not: ending an
        event on a value nobody has catalogued is worse than ending it
        late."""
        assert self._stopped("charge", "somethingNew") is False

    def test_without_a_cycle_the_phase_still_decides(self):
        """Older payloads, and robots that do not report it."""
        assert self._stopped("charge", None) is True
        assert self._stopped("run", None) is False


class TestEventsCarryAnIdentity:
    """WITHOUT A UID, DELETE AND EDIT CANNOT BE CALLED AT ALL.

    Home Assistant passes the uid back when a user acts on an event, and
    an event without one has nothing to pass -- so both handlers looked
    complete and were unreachable. It went unnoticed because creating
    needs no uid, and creating was the only direction anyone exercised:
    the delete tests ran through the services rather than the calendar.
    """

    def test_the_event_builder_accepts_one(self):
        import datetime as dt

        from custom_components.roomba_plus.calendar import _to_calendar_event

        start = dt.datetime(2026, 8, 5, 9, 0, tzinfo=dt.UTC)
        event = _to_calendar_event(start, start, ["Kitchen"], uid="S1")

        assert event.uid == "S1"

    def test_without_one_it_is_none_rather_than_invented(self):
        import datetime as dt

        from custom_components.roomba_plus.calendar import _to_calendar_event

        start = dt.datetime(2026, 8, 5, 9, 0, tzinfo=dt.UTC)

        assert _to_calendar_event(start, start, []).uid is None


class TestClassicIdentityIsTheWeekday:
    """In a format with one entry per day there is nothing else to
    identify with."""

    def _round_trip(self, weekday):
        from custom_components.roomba_plus.calendar import (
            _classic_uid,
            _weekday_from_uid,
        )

        return _weekday_from_uid(_classic_uid(weekday))

    def test_every_weekday_survives_the_round_trip(self):
        assert [self._round_trip(d) for d in range(7)] == list(range(7))

    def test_a_prime_schedule_id_is_not_mistaken_for_one(self):
        """Prime uids are schedule ids and must not be read as weekdays,
        or a delete would take the wrong entry."""
        from custom_components.roomba_plus.calendar import _weekday_from_uid

        assert _weekday_from_uid("hh_abc_s_AE3C") is None
        assert _weekday_from_uid(None) is None
        assert _weekday_from_uid("weekday-") is None

    def test_a_weekday_outside_the_week_is_refused(self):
        from custom_components.roomba_plus.calendar import _weekday_from_uid

        assert _weekday_from_uid("weekday-7") is None
        assert _weekday_from_uid("weekday-x") is None


class TestPrimeOccurrencesCarryTheScheduleId:
    def test_the_parser_appends_it(self):
        import inspect

        from custom_components.roomba_plus import schedule_parser

        source = inspect.getsource(
            schedule_parser.parse_prime_schedule_occurrences
        )
        assert 'getattr(schedule, "schedule_id", None)' in source

    def test_reading_it_tolerates_the_shorter_classic_tuple(self):
        """Classic occurrences have four fields, Prime five. Widening one
        must not break the other."""
        from custom_components.roomba_plus.calendar import _uid_of

        assert _uid_of((1, 2, [], "name")) is None
        assert _uid_of((1, 2, [], "name", "S1")) == "S1"


class TestTheEventSaysWhatTheMissionWillDo:
    """The iRobot app shows both -- "En profondeur, Aspiration + lavage"
    above the room list -- and a schedule that mops is a different thing
    from one that vacuums over the same rooms.
    """

    def _summary(self, mode=None, rooms=None):
        from custom_components.roomba_plus.calendar import _event_summary

        return _event_summary(rooms if rooms is not None else ["Küche"], mode)

    def test_the_mode_appears_with_the_rooms(self):
        assert self._summary("vacuum, then mop") == (
            "Cleaning (vacuum, then mop): Küche"
        )

    def test_a_whole_house_schedule_still_gets_it(self):
        assert self._summary("mop", rooms=[]) == "Cleaning (mop)"

    def test_without_a_mode_nothing_changes(self):
        """The Classic path carries none, and neither does a Prime
        schedule whose command has no regions."""
        assert self._summary() == "Cleaning: Küche"


class TestOperatingModeLabels:
    """A bitmask in name only here: every value seen in a schedule
    command is a single mode, and the app shows one phrase per mission
    rather than a combination."""

    def _label(self, value):
        from custom_components.roomba_plus.calendar import _mode_label

        return _mode_label(value)

    def test_the_values_seen_in_the_field(self):
        """512 is what @DaRealGuGu's schedule carried, and the app called
        it "Aspiration + lavage"."""
        assert self._label(512) == "vacuum, then mop"
        assert self._label(2) == "vacuum"
        assert self._label(4) == "mop"
        assert self._label(32) == "vacuum and mop together"

    def test_an_unrecognised_mode_is_left_out(self):
        """Silence reads as "we did not recognise this". A guessed label
        would be a wrong claim about what the robot is going to do."""
        assert self._label(1024) is None
        assert self._label(None) is None
        assert self._label("mop") is None


class TestTheCalendarViewIsActuallyFilled:
    """`async_get_events` is what fills the calendar VIEW, and it
    unpacked the occurrence tuple into a fixed four names.

    That tuple has grown twice -- a schedule id so events could carry a
    uid, and an operating mode alongside it -- and the unpack raised
    ValueError on every call. An exception here produces an EMPTY
    calendar rather than a visible error: @chairstacker's entity was
    present, enabled and listed, with nothing in it.

    Every existing test passed throughout, because none of them called
    this method. The `event` property was widened at the time and this
    was not, which is why the state moved and the view did not.
    """

    async def _events(self, occurrences):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        cal._fetch_room_names = AsyncMock(return_value={"11": "Kitchen"})
        cal._fetch_occurrences = AsyncMock(return_value=occurrences)
        cal._config_entry = MagicMock()
        import datetime as dt

        return await PrimeScheduleCalendar.async_get_events(
            cal, MagicMock(),
            dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
        )

    def _occurrence(self, *extra):
        import datetime as dt

        start = dt.datetime(2026, 8, 6, 9, 0, tzinfo=dt.UTC)
        return (start, start + dt.timedelta(hours=1), ["11"], "Regular", *extra)

    @pytest.mark.asyncio
    async def test_the_current_six_field_tuple_produces_an_event(self):
        events = await self._events([self._occurrence("S1", 512)])

        assert len(events) == 1
        assert events[0].uid == "S1"
        assert "Kitchen" in events[0].summary

    @pytest.mark.asyncio
    async def test_a_shorter_tuple_still_works(self):
        """Classic occurrences are shorter, and widening one path must
        not break the other."""
        events = await self._events([self._occurrence()])

        assert len(events) == 1
        assert events[0].uid is None

    @pytest.mark.asyncio
    async def test_an_empty_schedule_gives_an_empty_calendar(self):
        assert await self._events([]) == []

    @pytest.mark.asyncio
    async def test_every_occurrence_becomes_an_event(self):
        """The count is the thing: an exception here loses all of them at
        once, which looks like a robot with no schedules."""
        events = await self._events([self._occurrence("S1", 2)] * 4)

        assert len(events) == 4


class TestAnEditThatSaysNothingAboutRecurrenceKeepsIt:
    """Home Assistant's edit dialog sends only what the user touched.
    Move a weekly schedule by an hour and no `rrule` comes with it --
    and `_frequency_from_rrule(None)` answers ONCE, which is right for a
    NEW event and destroys an existing one.

    @DaRealGuGu shifted a Mon/Tue/Wed schedule to 21:00 and watched every
    occurrence but one disappear, in Home Assistant and in the iRobot
    app. **The write succeeded; it wrote the wrong thing.**
    """

    def _calendar(self, existing="WEEKLY"):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        schedule = SimpleNamespace(
            schedule_id="S1",
            options=SimpleNamespace(frequency=existing),
        )
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_schedule_coordinator=SimpleNamespace(
                data=[("C1", [schedule])]
            )
        )
        cal._config_entry = entry
        return cal

    def test_an_edit_without_an_rrule_reports_the_existing_frequency(self):
        assert self._calendar("WEEKLY")._existing_frequency("S1") == "WEEKLY"

    def test_an_unknown_schedule_reports_nothing(self):
        """None tells the service to leave the field alone, which is what
        an edit that never mentioned recurrence should do."""
        assert self._calendar()._existing_frequency("SOMETHING_ELSE") is None

    def test_the_handler_only_derives_when_an_rrule_is_present(self):
        import inspect

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        source = inspect.getsource(PrimeScheduleCalendar.async_update_event)

        assert 'if event.get("rrule"):' in source
        assert "_existing_frequency(uid)" in source


class TestCalendarSummariesUseEveryNameSource:
    """@utkjmitch's four-map account labels its rooms correctly on the
    rooms map — his own `room1`–`room4` — while the **same rooms** appear
    as `Zone 10`–`Zone 14` in calendar summaries.

    Two name sources: the map bundle knows names the schedule
    coordinator does not, and only the coordinator was being read here.

    The bundle fills gaps **behind** the coordinator rather than over it:
    the coordinator reads the schedule, which is where a name a user set
    for scheduling would live.
    """

    def _names(self, coordinator_names, bundle_names):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        entity = object.__new__(PrimeScheduleCalendar)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_room_names=bundle_names)
        entity._config_entry = entry
        coordinator = SimpleNamespace(room_names=coordinator_names)
        return PrimeScheduleCalendar._fetch_room_names.__wrapped__(
            entity, coordinator
        ) if hasattr(
            PrimeScheduleCalendar._fetch_room_names, "__wrapped__"
        ) else None

    def test_the_bundle_names_are_read(self):
        """Pinned at the source level: the calendar consults
        `prime_room_names`, which the room map fills."""
        import inspect

        from custom_components.roomba_plus import calendar as cal

        source = inspect.getsource(cal)

        assert 'prime_room_names' in source
        assert "fills the gaps rather than winning them" in source

    def test_the_room_map_stores_what_it_parsed(self):
        """One fetch, two consumers — rather than the calendar making its
        own call for names the map already has."""
        import inspect

        from custom_components.roomba_plus import prime_room_map

        source = inspect.getsource(prime_room_map)

        assert "runtime.prime_room_names = existing" in source

    def test_the_runtime_field_exists(self):
        """The last three times a field like this was used it did not
        exist, and the attribute read as None for ever."""
        import dataclasses

        from custom_components.roomba_plus.models import RoombaData

        names = {f.name for f in dataclasses.fields(RoombaData)}

        assert "prime_room_names" in names


class TestRoomNamesComeFromEveryMap:
    """The a31 fix healed exactly half of this.

    `prime_room_names` is filled when a floor plan is **built**, and only
    the map image builds one — for the map it is showing. On @utkjmitch's
    four-map account, rooms named on the displayed map came through
    (`room1`–`room3`) while rooms a schedule referenced on another map
    stayed `Zone 13`, `Zone 14`.

    So a schedule spanning maps got half its rooms named and half
    numbered.
    """

    def _names(self, per_map, backend=True):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        entity = object.__new__(PrimeScheduleCalendar)
        entity._config_entry = MagicMock()
        entity.hass = MagicMock()

        fake_backend = MagicMock()
        fake_backend._all_map_ids = AsyncMock(return_value=list(per_map))

        async def _polys(_entry, map_id):
            return {}, per_map.get(map_id, {}), {}

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=fake_backend if backend else None,
        ), patch(
            "custom_components.roomba_plus.prime_room_map."
            "async_build_prime_room_polygons",
            side_effect=_polys,
        ):
            return asyncio.run(entity._names_from_all_maps())

    def test_rooms_on_a_second_map_get_their_names(self):
        names = self._names({
            "M1": {"11": "Kitchen"},
            "M2": {"13": "Study", "14": "Loft"},
        })

        assert names["13"] == "Study"
        assert names["14"] == "Loft"

    def test_the_first_map_wins_a_shared_id(self):
        """Room ids are per map, so a collision is possible. Taking the
        first is arbitrary but stable, and beats alternating."""
        names = self._names({"M1": {"11": "Kitchen"}, "M2": {"11": "Garage"}})

        assert names["11"] == "Kitchen"

    def test_no_backend_means_zone_numbers(self):
        """Which is what they already were."""
        assert self._names({"M1": {"11": "Kitchen"}}, backend=False) == {}

    def test_a_map_that_will_not_load_is_skipped(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        entity = object.__new__(PrimeScheduleCalendar)
        entity._config_entry = MagicMock()
        entity.hass = MagicMock()
        fake_backend = MagicMock()
        fake_backend._all_map_ids = AsyncMock(return_value=["M1", "M2"])

        async def _polys(_entry, map_id):
            if map_id == "M1":
                raise RuntimeError("boom")
            return {}, {"14": "Loft"}, {}

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=fake_backend,
        ), patch(
            "custom_components.roomba_plus.prime_room_map."
            "async_build_prime_room_polygons",
            side_effect=_polys,
        ):
            assert asyncio.run(entity._names_from_all_maps()) == {"14": "Loft"}


class TestTheWeekdayBasisMatchesTheWire:
    """@chairstacker (#71): a Monday entry created in the HA calendar
    landed on Sunday, in both Home Assistant and the iRobot app.

    `datetime.weekday()` is Mon=0..Sun=6. The wire table is
    `sun: 0, mon: 1, ...`. Passing the raw value shifted every day back
    by one.

    Third time this conversion has gone wrong here: @DaRealGuGu's edit
    shifted *forward* by one on a30, from a second table that counted
    from Monday.
    """

    def test_the_wire_table_still_starts_at_sunday(self):
        """If this ever changes, the conversion below is wrong rather
        than the call sites."""
        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAYS,
        )

        assert _WEEKDAYS["sun"] == 0
        assert _WEEKDAYS["mon"] == 1

    def test_every_calendar_call_converts_the_basis(self):
        import inspect
        import re

        from custom_components.roomba_plus import calendar as cal

        source = inspect.getsource(cal)
        raw = re.findall(r"weekday=local_start\.weekday\(\)", source)

        assert not raw, (
            "a calendar call passes Python's Monday-based weekday "
            "straight to a Sunday-based wire table -- every day lands "
            "one early"
        )

    def test_monday_converts_to_the_wire_value_for_monday(self):
        import datetime

        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAYS,
        )

        monday = datetime.datetime(2026, 8, 17)      # a Monday
        assert monday.weekday() == 0

        assert (monday.weekday() + 1) % 7 == _WEEKDAYS["mon"]

    def test_sunday_wraps_to_zero(self):
        import datetime

        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAYS,
        )

        sunday = datetime.datetime(2026, 8, 16)
        assert sunday.weekday() == 6

        assert (sunday.weekday() + 1) % 7 == _WEEKDAYS["sun"]


class TestZonesCanBeNamedInACalendarEvent:
    """@chairstacker (#71) typed a zone name — "office" — and got a
    schedule for Kitchen.

    `room_names` on the schedule coordinator is built from active map
    versions, which carry rooms. Zones live in the bundle's
    `cleanZones` layer and reach `prime_room_names` on the runtime
    data. The calendar read only the first, so a zone name could never
    match — and an unmatched name used to inherit another schedule's
    target silently.
    """

    @staticmethod
    def _entity(coordinator_rooms, runtime_names):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.calendar import (
            PrimeScheduleCalendar,
        )

        entity = PrimeScheduleCalendar.__new__(PrimeScheduleCalendar)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_schedule_coordinator=SimpleNamespace(
                room_names=coordinator_rooms
            ),
            prime_room_names=runtime_names,
        )
        entity._config_entry = entry
        return entity

    def test_a_zone_is_in_the_name_list(self):
        entity = self._entity({"16": "Kitchen"}, {"z4": "Office"})

        assert entity._room_names() == {"16": "Kitchen", "z4": "Office"}

    def test_a_room_wins_a_name_collision(self):
        """A schedule targets rooms natively, so on a shared name the
        room is the safer answer."""
        entity = self._entity({"16": "Den"}, {"16": "Something else"})

        assert entity._room_names()["16"] == "Den"

    def test_missing_runtime_names_are_harmless(self):
        entity = self._entity({"16": "Kitchen"}, None)

        assert entity._room_names() == {"16": "Kitchen"}


class TestEditingChangesMoreThanTheTime:
    """@chairstacker (#71): editing a calendar entry in Home Assistant
    changed the time and nothing else — not the summary, not the day.

    Two separate causes, both "the value was accepted and never sent".
    """

    def test_the_name_reaches_the_payload(self):
        import inspect

        from custom_components.roomba_plus import prime_schedule_services

        source = inspect.getsource(
            prime_schedule_services.async_update_schedule_from_calendar
        )

        assert 'call_data["name"] = name' in source, (
            "the function takes `name` and _reshaped_options handles "
            "call_data['name'] -- the two were never connected"
        )

    def test_an_empty_summary_does_not_blank_the_name(self):
        """An edit that leaves the summary alone must not wipe the
        schedule's name."""
        import inspect

        from custom_components.roomba_plus import prime_schedule_services

        source = inspect.getsource(
            prime_schedule_services.async_update_schedule_from_calendar
        )
        i = source.find('call_data["name"] = name')

        assert "if name:" in source[max(0, i - 200):i]


class TestBydayIsReadOnAnEdit:
    """`_days_for_update` keeps the schedule's existing days unless an
    explicit recurrence names others — right, because editing one
    occurrence of a repeating schedule must not drop the rest.

    But nothing ever passed those explicit days, so the preservation
    path was the only path and the weekday could not be changed.
    """

    def test_byday_becomes_python_weekdays(self):
        from custom_components.roomba_plus.calendar import (
            _weekdays_from_rrule,
        )

        assert _weekdays_from_rrule("FREQ=WEEKLY;BYDAY=MO,WE") == [0, 2]
        assert _weekdays_from_rrule("FREQ=WEEKLY;BYDAY=SU") == [6]

    def test_no_byday_means_keep_what_is_stored(self):
        """None, not an empty list — an empty list would replace the
        schedule's days with nothing."""
        from custom_components.roomba_plus.calendar import (
            _weekdays_from_rrule,
        )

        assert _weekdays_from_rrule("FREQ=WEEKLY") is None
        assert _weekdays_from_rrule(None) is None

    def test_a_prefixed_byday_still_parses(self):
        """`BYDAY=2TU` is the second Tuesday. The frequency check
        refuses those separately; this must not crash on one."""
        from custom_components.roomba_plus.calendar import (
            _weekdays_from_rrule,
        )

        assert _weekdays_from_rrule("FREQ=MONTHLY;BYDAY=2TU") == [1]

    def test_the_edit_path_passes_them_on(self):
        import inspect

        from custom_components.roomba_plus.calendar import (
            PrimeScheduleCalendar,
        )

        source = inspect.getsource(PrimeScheduleCalendar.async_update_event)

        assert "explicit_days=explicit_days" in source
