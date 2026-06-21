"""Unit tests for RoombaSensor._next_from_schedule2 / _next_from_schedule_v1.

v2.9.0 — REWRITTEN to call the REAL methods via a minimal RoombaSensor
instance (constructed with __new__, bypassing __init__ — no HA/roombapy
needed), instead of hand-copied "replica" functions. The replicas had
already drifted from the real implementation: they took `now` as an
explicit parameter, while the real methods compute it internally via
dt_util.now() — a signature mismatch that would have made the replica
tests pass even if the real method broke. Time is now controlled via
pytest_freezer's `freezer` fixture (already an installed plugin) instead.

Day mapping (Roomba): 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
Day mapping (Python weekday): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
Conversion: py_wd = (roomba_day - 1) % 7
"""
from __future__ import annotations

import datetime

import pytest


def _sensor():
    """Minimal RoombaSensor instance — no HA/roombapy setup needed since
    _next_from_schedule2/_next_from_schedule_v1 only touch dt_util.now()
    and their own parameters, nothing else on self."""
    from custom_components.roomba_plus.sensor import RoombaSensor
    return RoombaSensor.__new__(RoombaSensor)


def _next_monday_at(hour: int, minute: int = 0) -> datetime.datetime:
    """A fixed, well-known Monday (2024-01-01) at the given time, for
    freezing "now" to a specific weekday/time combination."""
    return datetime.datetime(2024, 1, 1, hour, minute, tzinfo=datetime.timezone.utc)


def _on_weekday(weekday_py: int, hour: int, minute: int = 0) -> datetime.datetime:
    """Return a datetime on a specific Python weekday and time, anchored
    to the same fixed Monday as _next_monday_at for consistency."""
    anchor = _next_monday_at(0, 0)  # Monday 2024-01-01
    days = (weekday_py - anchor.weekday()) % 7
    return anchor + datetime.timedelta(days=days, hours=hour, minutes=minute)


# ── Tests: cleanSchedule2 ─────────────────────────────────────────────────────

class TestNextFromSchedule2:
    def test_single_enabled_entry_today_in_future(self, freezer):
        """Entry on Monday at 09:00, now is Monday 08:00 → today at 09:00."""
        freezer.move_to(_on_weekday(0, 8, 0))  # Monday 08:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]  # 1=Mon
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9
        assert result.minute == 0

    def test_single_enabled_entry_today_in_past(self, freezer):
        """Entry on Monday at 07:00, now is Monday 08:00 → next Monday at 07:00."""
        now = _on_weekday(0, 8, 0)
        freezer.move_to(now)
        entries = [{"enabled": True, "start": {"hour": 7, "min": 0, "day": [1]}}]
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 0
        assert (result - now).days == 6

    def test_disabled_entry_ignored(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        entries = [{"enabled": False, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]
        result = _sensor()._next_from_schedule2(entries)
        assert result is None

    def test_multiple_days_returns_nearest(self, freezer):
        """Entry on Mon and Wed, now is Mon 10:00 → next is Wed."""
        freezer.move_to(_on_weekday(0, 10, 0))  # Monday 10:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]  # Mon, Wed
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 2  # Wednesday

    def test_multiple_entries_returns_nearest(self, freezer):
        freezer.move_to(_on_weekday(2, 8, 0))  # Wednesday 08:00
        entries = [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [5]}},  # Fri
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [4]}},  # Thu
        ]
        result = _sensor()._next_from_schedule2(entries)
        assert result.weekday() == 3  # Thursday

    def test_sunday_day_zero_conversion(self, freezer):
        """Roomba day 0 = Sunday = Python weekday 6."""
        freezer.move_to(_on_weekday(5, 8, 0))  # Saturday 08:00
        entries = [{"enabled": True, "start": {"hour": 10, "min": 0, "day": [0]}}]  # Sun
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_entries(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        assert _sensor()._next_from_schedule2([]) is None

    def test_exact_match_time_is_past(self, freezer):
        """If now == schedule time exactly, it should roll to next week."""
        now = _on_weekday(0, 9, 0)  # Monday 09:00 exactly
        freezer.move_to(now)
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]
        result = _sensor()._next_from_schedule2(entries)
        # candidate == now → not > now → rolls to next week
        assert result is not None
        assert (result - now).days == 7


# ── Tests: legacy cleanSchedule ───────────────────────────────────────────────

class TestNextFromScheduleV1:
    def test_single_day_in_future(self, freezer):
        """Schedule runs Monday 09:00, now is Monday 08:00."""
        freezer.move_to(_on_weekday(0, 8, 0))
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9

    def test_single_day_in_past(self, freezer):
        now = _on_weekday(0, 10, 0)  # Monday 10:00
        freezer.move_to(now)
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 0
        assert (result - now).days == 6  # next week

    def test_all_none_returns_none(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        schedule = {
            "cycle": ["none", "none", "none", "none", "none", "none", "none"],
            "h":     [0, 0, 0, 0, 0, 0, 0],
            "m":     [0, 0, 0, 0, 0, 0, 0],
        }
        assert _sensor()._next_from_schedule_v1(schedule) is None

    def test_multiple_days_nearest_selected(self, freezer):
        """Mon and Fri scheduled, now is Wed 08:00 → Fri."""
        freezer.move_to(_on_weekday(2, 8, 0))  # Wednesday
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "start", "none"],
            "h":     [0,      9,       0,      0,      0,      9,       0],
            "m":     [0,      0,       0,      0,      0,      0,       0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 4  # Friday

    def test_sunday_index_zero(self, freezer):
        """Index 0 in cleanSchedule = Sunday = Python weekday 6."""
        freezer.move_to(_on_weekday(5, 8, 0))  # Saturday
        schedule = {
            "cycle": ["start", "none", "none", "none", "none", "none", "none"],
            "h":     [10,      0,      0,      0,      0,      0,      0],
            "m":     [0,       0,      0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_schedule(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        assert _sensor()._next_from_schedule_v1({}) is None
