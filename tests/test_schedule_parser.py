"""v3.4.0 CAL — schedule_parser.py: range-query schedule parsing.

Extracted from sensor_core.py's _next_from_schedule2()/
_next_from_schedule_v1() (single-next-occurrence, relative to now) and
generalised to return ALL occurrences within an arbitrary [start, end)
range. Those original single-occurrence semantics are still covered by
test_sensors.py's TestNextFromSchedule2/TestNextFromScheduleV1 classes
(via sensor_core.py's back-compat wrapper methods) — this file covers
the new range behaviour specifically.
"""
from __future__ import annotations

import datetime

from custom_components.roomba_plus.schedule_parser import (
    DEFAULT_EVENT_DURATION,
    occurrences_from_schedule2,
    occurrences_from_schedule_v1,
    parse_schedule_occurrences,
)


def _monday(hour: int = 0, minute: int = 0) -> datetime.datetime:
    """A fixed, well-known Monday (2024-01-01), for anchoring tests to
    a specific weekday/time combination without needing freezegun —
    every function here takes start/end explicitly, no implicit now()."""
    return datetime.datetime(2024, 1, 1, hour, minute, tzinfo=datetime.timezone.utc)


def _on_weekday(weekday_py: int, hour: int = 0, minute: int = 0) -> datetime.datetime:
    anchor = _monday()
    days = (weekday_py - anchor.weekday()) % 7
    return anchor + datetime.timedelta(days=days, hours=hour, minutes=minute)


class TestOccurrencesFromSchedule2Range:
    def test_single_week_range_single_entry(self):
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]  # Mon
        start = _on_weekday(0, 0, 0)  # Monday 00:00
        end = start + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule2(entries, start, end)
        assert len(occ) == 1
        assert occ[0] == _on_weekday(0, 9, 0)

    def test_two_week_range_returns_two_occurrences(self):
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=2)
        occ = occurrences_from_schedule2(entries, start, end)
        assert len(occ) == 2
        assert occ[1] - occ[0] == datetime.timedelta(days=7)

    def test_multiple_days_in_one_entry(self):
        """A single entry can target several days (day: [1, 5] = Mon+Fri)."""
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1, 5]}}]
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule2(entries, start, end)
        weekdays = sorted(o.weekday() for o in occ)
        assert weekdays == [0, 4]  # Monday, Friday

    def test_multiple_entries_different_times(self):
        """Two separate entries, different days AND times — the real
        cleanSchedule2 shape allows this (not just one shared time)."""
        entries = [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}},   # Mon 09:00
            {"enabled": True, "start": {"hour": 14, "min": 30, "day": [3]}},  # Wed 14:30
        ]
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule2(entries, start, end)
        assert len(occ) == 2
        hours = sorted((o.weekday(), o.hour, o.minute) for o in occ)
        assert hours == [(0, 9, 0), (2, 14, 30)]

    def test_disabled_entry_excluded(self):
        entries = [{"enabled": False, "start": {"hour": 9, "min": 0, "day": [1]}}]
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert occurrences_from_schedule2(entries, start, end) == []

    def test_empty_entries_returns_empty(self):
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert occurrences_from_schedule2([], start, end) == []

    def test_start_boundary_is_inclusive(self):
        """A schedule occurrence exactly AT start is included — this
        differs deliberately from the old single-occurrence semantics
        (which excluded an exact 'now' match); range queries use the
        conventional half-open [start, end) interval instead."""
        exact = _on_weekday(1, 9, 0)  # Tuesday 09:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [2]}}]
        end = exact + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule2(entries, exact, end)
        assert exact in occ

    def test_end_boundary_is_exclusive(self):
        exact = _on_weekday(1, 9, 0)
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [2]}}]
        occ = occurrences_from_schedule2(entries, exact - datetime.timedelta(days=1), exact)
        assert occ == []

    def test_malformed_entries_are_skipped(self):
        entries = ["not_a_dict", {"enabled": True}]  # missing "start" entirely
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert occurrences_from_schedule2(entries, start, end) == []


class TestOccurrencesFromScheduleV1Range:
    def test_sunday_index_zero(self):
        """Index 0 in cleanSchedule = Sunday = Python weekday 6 — same
        convention as the pre-extraction sensor_core.py code."""
        schedule = {
            "cycle": ["start", "none", "none", "none", "none", "none", "none"],
            "h": [10, 0, 0, 0, 0, 0, 0],
            "m": [0, 0, 0, 0, 0, 0, 0],
        }
        start = _on_weekday(5, 8, 0)  # Saturday
        end = start + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule_v1(schedule, start, end)
        assert len(occ) == 1
        assert occ[0].weekday() == 6  # Sunday

    def test_multiple_days_both_returned_in_range(self):
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "start", "none"],
            "h": [0, 9, 0, 0, 0, 9, 0],
            "m": [0, 0, 0, 0, 0, 0, 0],
        }
        start = _on_weekday(0, 0, 0)  # Monday
        end = start + datetime.timedelta(weeks=1)
        occ = occurrences_from_schedule_v1(schedule, start, end)
        weekdays = sorted(o.weekday() for o in occ)
        assert weekdays == [0, 4]  # Monday, Friday

    def test_all_none_returns_empty(self):
        schedule = {
            "cycle": ["none"] * 7, "h": [0] * 7, "m": [0] * 7,
        }
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert occurrences_from_schedule_v1(schedule, start, end) == []

    def test_empty_schedule_dict_returns_empty(self):
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert occurrences_from_schedule_v1({}, start, end) == []


class TestParseScheduleOccurrences:
    """The public aggregator — cleanSchedule2 vs. legacy cleanSchedule
    precedence, and the (start, end) tuple shape with the duration
    placeholder."""

    def test_prefers_schedule2_when_both_present(self):
        """Same precedence as the pre-extraction _calc_next_clean():
        cleanSchedule2 wins if non-empty, legacy cleanSchedule is
        ignored even if also present."""
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}},
            ],
            "cleanSchedule": {
                "cycle": ["none", "none", "none", "none", "none", "none", "start"],
                "h": [0] * 6 + [20], "m": [0] * 7,
            },
        }
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = parse_schedule_occurrences(state, start, end)
        assert len(occ) == 1
        assert occ[0][0].hour == 9  # from cleanSchedule2, not the 20:00 legacy entry

    def test_falls_back_to_legacy_when_schedule2_empty(self):
        state = {
            "cleanSchedule2": [],
            "cleanSchedule": {
                "cycle": ["none", "start", "none", "none", "none", "none", "none"],
                "h": [0, 9, 0, 0, 0, 0, 0], "m": [0] * 7,
            },
        }
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = parse_schedule_occurrences(state, start, end)
        assert len(occ) == 1
        assert occ[0][0].hour == 9

    def test_neither_present_returns_empty(self):
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        assert parse_schedule_occurrences({}, start, end) == []

    def test_result_is_sorted(self):
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [5]}},  # Fri
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}},  # Mon
            ],
        }
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = parse_schedule_occurrences(state, start, end)
        starts = [s for s, _e in occ]
        assert starts == sorted(starts)

    def test_end_uses_default_duration_placeholder(self):
        state = {
            "cleanSchedule2": [
                {"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}},
            ],
        }
        start = _on_weekday(0, 0, 0)
        end = start + datetime.timedelta(weeks=1)
        occ = parse_schedule_occurrences(state, start, end)
        event_start, event_end = occ[0]
        assert event_end - event_start == DEFAULT_EVENT_DURATION


class TestMalformedInputResilience:
    """v3.4.0 bug hunt — cleanSchedule2/cleanSchedule fields come
    straight from MQTT, untrusted, and vary across firmware (the exact
    concern behind the CAL plan's Feldverifikations-Gate). Before this
    fix, a non-numeric day/hour/minute raised TypeError from inside
    _weekly_occurrences(), crashing the ENTIRE parse — not just the one
    malformed entry. Every case here must return cleanly, and a
    malformed entry must not suppress a valid sibling in the same
    schedule."""

    def _range(self):
        start = _on_weekday(0, 0, 0)
        return start, start + datetime.timedelta(weeks=1)

    def test_day_is_a_string_not_a_list(self):
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": "monday"}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_day_list_contains_non_numeric_value(self):
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": ["x"]}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_hour_is_a_string(self):
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": "nine", "min": 0, "day": [1]}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_minute_is_a_string(self):
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": 9, "min": "zero", "day": [1]}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_hour_out_of_valid_range(self):
        """A valid int (25) that datetime.replace() itself rejects —
        distinct failure mode from a non-numeric value."""
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": 25, "min": 0, "day": [1]}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_minute_out_of_valid_range(self):
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": 9, "min": 90, "day": [1]}},
        ]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_cleanschedule2_is_a_malformed_dict_not_a_list(self):
        start, end = self._range()
        state = {"cleanSchedule2": {"garbage": "data"}}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_entries_are_not_dicts(self):
        start, end = self._range()
        state = {"cleanSchedule2": ["garbage", 123, None]}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_legacy_hour_is_a_string(self):
        start, end = self._range()
        state = {"cleanSchedule": {
            "cycle": ["start"] * 7, "h": ["x"] * 7, "m": [0] * 7,
        }}
        assert parse_schedule_occurrences(state, start, end) == []

    def test_malformed_entry_does_not_suppress_a_valid_sibling(self):
        """The core resilience property: one bad entry in a schedule
        must not silently swallow the others."""
        start, end = self._range()
        state = {"cleanSchedule2": [
            {"enabled": True, "start": {"hour": "x", "min": 0, "day": [1]}},
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [3]}},  # Wed
        ]}
        occ = parse_schedule_occurrences(state, start, end)
        assert len(occ) == 1
        assert occ[0][0].weekday() == 2  # Wednesday
