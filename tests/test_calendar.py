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

    def test_currently_running_slot_not_returned_as_next(self):
        """Matches sensor.*_next_clean's existing 'strictly future' semantics
        — an occurrence exactly at (or before) now is not 'the next one'."""
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 8, "min": 0, "day": [2]}},  # Tue 08:00
            ],
        }
        cal = _make_calendar(state)
        with freeze_time("2026-07-07 08:00:00"):  # exactly Tuesday 08:00
            event = cal.event
        # Next occurrence must be a week later, not "now" itself.
        assert event is not None
        assert event.start > datetime.datetime(2026, 7, 7, 8, 0, tzinfo=datetime.timezone.utc)

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


def _make_prime_calendar(prime_household_id="hh1", prime_robot=None):
    """Minimal PrimeScheduleCalendar — bypasses IRobotEntity.__init__,
    same pattern as _make_calendar() above."""
    from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

    cal = PrimeScheduleCalendar.__new__(PrimeScheduleCalendar)
    cal._blid = "TESTBLID"
    config_entry = MagicMock()
    config_entry.runtime_data.prime_household_id = prime_household_id
    config_entry.runtime_data.prime_robot = prime_robot or MagicMock()
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
        prime_robot = MagicMock()
        response = MagicMock()
        schedules_list = MagicMock()
        schedules_list.schedules = [schedule_raw]
        response.household_schedules = [schedules_list]
        prime_robot.get_schedules = AsyncMock(return_value=response)
        cal = _make_prime_calendar(prime_robot=prime_robot)

        result = await cal._fetch_occurrences(
            datetime.datetime(2026, 7, 20), datetime.datetime(2026, 7, 27)
        )

        assert len(result) == 1
        prime_robot.get_schedules.assert_awaited_once_with("hh1")


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
