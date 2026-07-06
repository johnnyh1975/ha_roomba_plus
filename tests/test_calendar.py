"""v3.4.0 CAL — tests for calendar.py's RoombaScheduleCalendar."""
from __future__ import annotations

import datetime

from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time


def _make_calendar(vacuum_state: dict | None = None):
    """Minimal RoombaScheduleCalendar — bypasses IRobotEntity.__init__
    (no roombapy/device-registry setup needed for these tests), same
    pattern as other platform test files in this suite."""
    from custom_components.roomba_plus.calendar import RoombaScheduleCalendar

    cal = RoombaScheduleCalendar.__new__(RoombaScheduleCalendar)
    cal._blid = "TESTBLID"
    cal.vacuum_state = vacuum_state or {}
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
