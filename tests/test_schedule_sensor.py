"""Unit tests for ScheduleSensor._calc_next_clean logic.

Extracts the pure schedule-calculation methods and tests them
in isolation — no HA, no roombapy required.

Day mapping (Roomba): 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
Day mapping (Python weekday): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
Conversion: py_wd = (roomba_day - 1) % 7
"""
import datetime
import pytest

# Extract the pure calculation functions without importing HA
# by copying the logic directly (avoids mocking the entire HA stack)
import datetime as dt_stdlib

def _next_from_schedule2(entries: list, now: datetime.datetime):
    """Replica of RoombaSensor._next_from_schedule2."""
    candidates = []
    for entry in entries:
        if not entry.get("enabled", False):
            continue
        start = entry.get("start", {})
        hour = start.get("hour", 0)
        minute = start.get("min", 0)
        for roomba_day in start.get("day", []):
            py_wd = (roomba_day - 1) % 7
            days_ahead = (py_wd - now.weekday()) % 7
            candidate = now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            ) + dt_stdlib.timedelta(days=days_ahead)
            if candidate <= now:
                candidate += dt_stdlib.timedelta(days=7)
            candidates.append(candidate)
    return min(candidates) if candidates else None


def _next_from_schedule_v1(schedule: dict, now: datetime.datetime):
    """Replica of RoombaSensor._next_from_schedule_v1."""
    cycle = schedule.get("cycle", [])
    hours = schedule.get("h", [])
    mins  = schedule.get("m", [])
    candidates = []
    for i, (cyc, h, m) in enumerate(zip(cycle, hours, mins)):
        if cyc != "start":
            continue
        py_wd = (i - 1) % 7
        days_ahead = (py_wd - now.weekday()) % 7
        candidate = now.replace(
            hour=h, minute=m, second=0, microsecond=0
        ) + dt_stdlib.timedelta(days=days_ahead)
        if candidate <= now:
            candidate += dt_stdlib.timedelta(days=7)
        candidates.append(candidate)
    return min(candidates) if candidates else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_now(weekday_py: int, hour: int, minute: int = 0) -> datetime.datetime:
    """Return a fake 'now' on a specific Python weekday and time."""
    # Find next Monday (weekday 0) from a fixed anchor
    anchor = datetime.datetime(2024, 1, 1, 0, 0)  # Monday
    days = (weekday_py - anchor.weekday()) % 7
    return anchor + datetime.timedelta(days=days, hours=hour, minutes=minute)


# ── Tests: cleanSchedule2 ─────────────────────────────────────────────────────

class TestNextFromSchedule2:
    def test_single_enabled_entry_today_in_future(self):
        """Entry on Monday at 09:00, now is Monday 08:00 → today at 09:00."""
        now = _make_now(0, 8, 0)  # Monday 08:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]  # 1=Mon
        result = _next_from_schedule2(entries, now)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9
        assert result.minute == 0

    def test_single_enabled_entry_today_in_past(self):
        """Entry on Monday at 07:00, now is Monday 08:00 → next Monday at 07:00."""
        now = _make_now(0, 8, 0)
        entries = [{"enabled": True, "start": {"hour": 7, "min": 0, "day": [1]}}]
        result = _next_from_schedule2(entries, now)
        assert result is not None
        assert result.weekday() == 0
        assert result.hour == 7
        # Should be 7 days ahead (next week)
        assert (result - now).days == 6

    def test_disabled_entry_ignored(self):
        entries = [{"enabled": False, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]
        now = _make_now(0, 8, 0)
        result = _next_from_schedule2(entries, now)
        assert result is None

    def test_multiple_days_returns_nearest(self):
        """Entry on Mon and Wed, now is Mon 10:00 → next is Wed."""
        now = _make_now(0, 10, 0)  # Monday 10:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]  # Mon, Wed
        result = _next_from_schedule2(entries, now)
        assert result is not None
        assert result.weekday() == 2  # Wednesday

    def test_multiple_entries_returns_nearest(self):
        now = _make_now(2, 8, 0)  # Wednesday 08:00
        entries = [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [5]}},  # Fri
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [4]}},  # Thu
        ]
        result = _next_from_schedule2(entries, now)
        assert result.weekday() == 3  # Thursday

    def test_sunday_day_zero_conversion(self):
        """Roomba day 0 = Sunday = Python weekday 6."""
        now = _make_now(5, 8, 0)  # Saturday 08:00
        entries = [{"enabled": True, "start": {"hour": 10, "min": 0, "day": [0]}}]  # Sun
        result = _next_from_schedule2(entries, now)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_entries(self):
        now = _make_now(0, 8, 0)
        assert _next_from_schedule2([], now) is None

    def test_exact_match_time_is_past(self):
        """If now == schedule time exactly, it should roll to next week."""
        now = _make_now(0, 9, 0)  # Monday 09:00 exactly
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]
        result = _next_from_schedule2(entries, now)
        # candidate == now → not > now → rolls to next week
        assert result is not None
        assert (result - now).days == 7


# ── Tests: legacy cleanSchedule ───────────────────────────────────────────────

class TestNextFromScheduleV1:
    def test_single_day_in_future(self):
        """Schedule runs Monday 09:00, now is Monday 08:00."""
        now = _make_now(0, 8, 0)
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _next_from_schedule_v1(schedule, now)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9

    def test_single_day_in_past(self):
        now = _make_now(0, 10, 0)  # Monday 10:00
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _next_from_schedule_v1(schedule, now)
        assert result is not None
        assert result.weekday() == 0
        assert (result - now).days == 6  # next week

    def test_all_none_returns_none(self):
        now = _make_now(0, 8, 0)
        schedule = {
            "cycle": ["none", "none", "none", "none", "none", "none", "none"],
            "h":     [0, 0, 0, 0, 0, 0, 0],
            "m":     [0, 0, 0, 0, 0, 0, 0],
        }
        assert _next_from_schedule_v1(schedule, now) is None

    def test_multiple_days_nearest_selected(self):
        """Mon and Fri scheduled, now is Wed 08:00 → Fri."""
        now = _make_now(2, 8, 0)  # Wednesday
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "start", "none"],
            "h":     [0,      9,       0,      0,      0,      9,       0],
            "m":     [0,      0,       0,      0,      0,      0,       0],
        }
        result = _next_from_schedule_v1(schedule, now)
        assert result is not None
        assert result.weekday() == 4  # Friday

    def test_sunday_index_zero(self):
        """Index 0 in cleanSchedule = Sunday = Python weekday 6."""
        now = _make_now(5, 8, 0)  # Saturday
        schedule = {
            "cycle": ["start", "none", "none", "none", "none", "none", "none"],
            "h":     [10,      0,      0,      0,      0,      0,      0],
            "m":     [0,       0,      0,      0,      0,      0,      0],
        }
        result = _next_from_schedule_v1(schedule, now)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_schedule(self):
        now = _make_now(0, 8, 0)
        assert _next_from_schedule_v1({}, now) is None
