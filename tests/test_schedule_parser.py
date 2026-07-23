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
    parse_prime_schedule_occurrences,
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


class TestParsePrimeScheduleOccurrences:
    """parse_prime_schedule_occurrences() -- Prime's own equivalent,
    reading roombapy_prime HouseholdSchedule objects instead of a
    local vacuum_state dict."""

    def _range(self):
        return _monday(), _monday() + datetime.timedelta(days=7)

    def _make_schedule(
        self, *, day, hour=8, minute=0, frequency=None, enabled=True, deleted=False,
        commands=None, name=None, after=None,
    ):
        from roombapy_prime.models.schedules_dnd import (
            HouseholdSchedule, ScheduleFrequency, ScheduleOptions, ScheduleTime,
        )

        return HouseholdSchedule(
            schedule_id="hs1",
            options=ScheduleOptions(
                name=name,
                enabled=enabled,
                deleted=deleted,
                frequency=frequency or ScheduleFrequency.WEEKLY,
                start=ScheduleTime(day=day, hour=hour, min=minute),
                commands=commands,
                after=after,
            ),
        )

    def test_weekly_schedule_with_region_produces_occurrence(self):
        start, end = self._range()
        schedule = self._make_schedule(
            day=[2], hour=8, minute=0, name="Kitchen",
            commands=[{"regions": [{"region_id": "23", "type": "rid"}]}],
        )

        result = parse_prime_schedule_occurrences([schedule], start, end)

        assert len(result) == 1
        occ_start, occ_end, region_ids, name = result[0]
        assert occ_start.weekday() == 1  # Tuesday (roomba day 2, 0=Sunday convention)
        assert occ_end == occ_start + DEFAULT_EVENT_DURATION
        assert region_ids == ["23"]
        assert name == "Kitchen"

    def test_schedule_with_no_regions_returns_empty_region_list(self):
        start, end = self._range()
        schedule = self._make_schedule(day=[2], commands=None)

        result = parse_prime_schedule_occurrences([schedule], start, end)

        assert len(result) == 1
        assert result[0][2] == []

    def test_disabled_schedule_produces_no_occurrences(self):
        start, end = self._range()
        schedule = self._make_schedule(day=[2], enabled=False)

        assert parse_prime_schedule_occurrences([schedule], start, end) == []

    def test_deleted_schedule_produces_no_occurrences(self):
        start, end = self._range()
        schedule = self._make_schedule(day=[2], deleted=True)

        assert parse_prime_schedule_occurrences([schedule], start, end) == []

    def test_non_weekly_frequencies_are_all_skipped_for_now(self):
        """DECISION (this session): even though a reasoned hypothesis
        exists for BI_WEEKLY specifically (options.after as a cadence
        anchor -- see _biweekly_occurrences()'s own docstring, and that
        function's own direct tests below, which still pass and remain
        ready to wire back in), it is NOT wired into this function for
        now. Verifying any such hypothesis needs a real device test
        that waits days/weeks to observe whether the robot actually
        fires as expected -- a materially different, higher risk
        profile than this project's other staged tests (immediate,
        observable effect). Combined with a real decompilation finding
        that BI_WEEKLY/MONTHLY are referenced nowhere in the app's own
        code at all (suggesting these may be legacy-only), the decision
        for now is to skip all three non-weekly frequencies rather than
        ship an unverified guess -- even with an anchor present."""
        from roombapy_prime.models.schedules_dnd import ScheduleDateEntry, ScheduleFrequency

        start, end = self._range()
        anchor = ScheduleDateEntry(year=2024, month=1, day_of_month=2)
        for freq in (ScheduleFrequency.BI_WEEKLY, ScheduleFrequency.MONTHLY, ScheduleFrequency.ONCE):
            with_anchor = self._make_schedule(day=[2], frequency=freq, after=anchor)
            without_anchor = self._make_schedule(day=[2], frequency=freq, after=None)
            assert parse_prime_schedule_occurrences([with_anchor], start, end) == []
            assert parse_prime_schedule_occurrences([without_anchor], start, end) == []

    def test_multiple_schedules_sorted_by_time(self):
        start, end = self._range()
        later = self._make_schedule(day=[4], hour=18, name="Later")
        earlier = self._make_schedule(day=[2], hour=8, name="Earlier")

        result = parse_prime_schedule_occurrences([later, earlier], start, end)

        assert [name for *_rest, name in result] == ["Earlier", "Later"]


class TestBiweeklyOccurrencesDirect:
    """_biweekly_occurrences() -- direct unit tests for the core
    cadence math, separate from parse_prime_schedule_occurrences()'s
    own higher-level tests above."""

    def test_occurrences_stay_aligned_to_the_anchor_not_just_any_matching_weekday(self):
        """The key property that distinguishes this from weekly: an
        occurrence must be an exact multiple of 14 days from the
        anchor, not just "any Tuesday" -- this is what a naive
        "resend as weekly" implementation would get wrong."""
        from custom_components.roomba_plus.schedule_parser import _biweekly_occurrences

        anchor = datetime.date(2026, 1, 6)  # a Tuesday
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=28)

        result = _biweekly_occurrences(2, 8, 0, anchor, start, end)

        for occurrence in result:
            days_since_anchor = (occurrence.date() - anchor).days
            assert days_since_anchor % 14 == 0

    def test_malformed_day_hour_minute_returns_empty_not_raises(self):
        from custom_components.roomba_plus.schedule_parser import _biweekly_occurrences

        anchor = datetime.date(2026, 1, 6)
        start = datetime.datetime(2026, 7, 20, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=28)

        assert _biweekly_occurrences("not-a-number", 8, 0, anchor, start, end) == []
