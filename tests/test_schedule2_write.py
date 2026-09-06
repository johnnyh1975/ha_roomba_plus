"""Writing the modern schedule format.

WHY THIS FILE EXISTS. The calendar wrote a LEGACY-shaped object under
the MODERN key on every i/s/j robot, and nothing caught it: the tests
all read, none wrote and read back. The firmware rejects the type
outright (`'cleanSchedule2' is NOT of type: ARRAY`), so the schedule
silently never changed -- and a rejected write looks exactly like an
unchanged schedule to a user.

The proof needed no robot: feed a real entry through the writer and
back through this project's own parser, and the count goes from one to
zero. That round trip is the test that was missing, and it is the first
one below.
"""
from __future__ import annotations

import datetime

import pytest

from custom_components.roomba_plus.classic_schedule_write import (
    MODERN_KEY,
    ScheduleFormatError,
    schedule2_entry,
    schedule2_with_entry,
    schedule2_without_day,
)
from custom_components.roomba_plus.schedule_parser import (
    occurrences_from_schedule2,
)

_REAL_ENTRY = {
    "enabled": True,
    "type": 0,
    "start": {"day": [6], "hour": 7, "min": 55},
    "cmd": {"command": "start"},
}
_WEEK_START = datetime.datetime(2026, 9, 1)
_WEEK_END = datetime.datetime(2026, 9, 8)


def _occurrences(schedule):
    return occurrences_from_schedule2(schedule, _WEEK_START, _WEEK_END)


class TestTheRoundTripThatWasMissing:
    def test_what_we_write_is_what_we_can_read(self) -> None:
        """The bug in one assertion.

        The old writer produced an object; the parser wants an array,
        and found nothing in it. Anything that survives this cannot be
        the wrong shape.
        """
        written = schedule2_with_entry([_REAL_ENTRY], weekday=2, hour=9, minute=30)

        assert isinstance(written, list), f"{MODERN_KEY} is an array"
        assert len(_occurrences(written)) == 2, (
            f"both cleanings must be readable back, got {written!r}"
        )

    def test_an_existing_entry_survives_a_new_one(self) -> None:
        """A write replaces the entire list -- the scheduler stores it as
        one object and reloads it whole, and `[]` clears everything. An
        entry we drop is an entry the user loses."""
        written = schedule2_with_entry([_REAL_ENTRY], weekday=2, hour=9, minute=30)

        assert _REAL_ENTRY in written


class TestEntriesWeDoNotUnderstand:
    def test_a_multi_day_entry_keeps_its_other_days(self) -> None:
        """The iRobot app can put several days in one entry, and the
        firmware allows it. Setting one of those days must not delete
        the rest."""
        multi = {
            "enabled": True,
            "type": 0,
            "start": {"day": [1, 3, 5], "hour": 6, "min": 0},
            "cmd": {"command": "start"},
        }
        written = schedule2_with_entry([multi], weekday=3, hour=9, minute=30)

        survivor = next(e for e in written if e["start"]["day"] != [3])
        assert survivor["start"]["day"] == [1, 5]

    def test_an_unrecognisable_entry_is_carried_through(self) -> None:
        """A robot may hold something this code does not model. Dropping
        it to make our shape fit would delete a user's schedule."""
        odd = {"enabled": True, "type": 0, "cmd": {"command": "train"}}
        written = schedule2_with_entry([odd], weekday=2, hour=9, minute=30)

        assert odd in written


class TestWhatTheFirmwareRequires:
    def test_every_required_field_is_present(self) -> None:
        """Each of these has its own rejection message in the scheduler,
        and an entry missing any of them fails validation with a device
        log line and nothing on the wire."""
        entry = schedule2_entry(weekdays=[0], hour=7, minute=55)

        assert set(entry) == {"enabled", "type", "start", "cmd"}
        assert set(entry["start"]) == {"day", "hour", "min"}
        assert entry["cmd"] == {"command": "start"}

    def test_type_is_zero_because_nothing_else_works(self) -> None:
        """`type` selects the command FORMAT rather than a recurrence.
        0 is the `cmd` object form; the `cmdStr` branch answers
        "cmdStr is not supported" and a third value gets "Unknown
        schedule type"."""
        assert schedule2_entry(weekdays=[0], hour=7, minute=55)["type"] == 0

    def test_a_day_list_is_never_empty(self) -> None:
        """`'Day' array is empty` is its own rejection."""
        with pytest.raises(ScheduleFormatError):
            schedule2_entry(weekdays=[], hour=7, minute=55)

    @pytest.mark.parametrize(("hour", "minute"), [(24, 0), (-1, 0), (0, 60)])
    def test_times_outside_the_robot_ranges_are_refused(
        self, hour: int, minute: int
    ) -> None:
        """`[0-23]` and `[0-59]`, refused here rather than at the robot
        where the refusal is invisible."""
        with pytest.raises(ScheduleFormatError):
            schedule2_entry(weekdays=[0], hour=hour, minute=minute)

    def test_the_twenty_second_entry_is_refused(self) -> None:
        """The scheduler refuses an array longer than 21 outright, in the
        validation path before the schedule is taken -- so the whole
        write very likely goes, not just the surplus entry. Raising here
        turns a silent loss into a message."""
        # DAYS 0-5 ONLY, deliberately. A first version used `d % 7`,
        # so three of the 21 entries carried day 6 and were replaced
        # rather than added -- 19 entries, no overflow, and a test that
        # passed for the wrong reason.
        full = [
            schedule2_entry(weekdays=[d % 6], hour=h, minute=0)
            for d, h in zip(range(21), range(21), strict=True)
        ]
        with pytest.raises(ScheduleFormatError, match="21"):
            schedule2_with_entry(full, weekday=6, hour=23, minute=0)


class TestRemoval:
    def test_removing_a_day_removes_its_entry(self) -> None:
        written = schedule2_with_entry([_REAL_ENTRY], weekday=2, hour=9, minute=30)

        assert len(_occurrences(schedule2_without_day(written, 2))) == 1

    def test_removing_one_day_of_several_keeps_the_others(self) -> None:
        multi = {
            "enabled": True,
            "type": 0,
            "start": {"day": [1, 3, 5], "hour": 6, "min": 0},
            "cmd": {"command": "start"},
        }
        remaining = schedule2_without_day([multi], 3)

        assert remaining[0]["start"]["day"] == [1, 5]


class TestTheCalendarPicksTheRightFormat:
    """The branch that was actually broken.

    The builders above can be perfect and the calendar still write the
    wrong one -- which is precisely what happened. Reverting the branch
    in `calendar.py` leaves every test in this file passing unless
    something exercises the branch itself, so these call it directly.

    Unbound, with `self` as None: the methods use only their arguments,
    and a full entity would need a Home Assistant instance to test one
    `if`.
    """

    def test_a_modern_robot_gets_an_array(self) -> None:
        from custom_components.roomba_plus.calendar import RoombaScheduleCalendar

        result = RoombaScheduleCalendar._with_entry(
            None, MODERN_KEY, [_REAL_ENTRY], weekday=2, hour=9, minute=30
        )

        assert isinstance(result, list), (
            f"{MODERN_KEY} holds an array; writing an object is rejected by "
            "the robot with 'is NOT of type: ARRAY'"
        )
        assert len(_occurrences(result)) == 2

    def test_a_legacy_robot_still_gets_an_object(self) -> None:
        """The negative control for the branch: the format that was
        already field-confirmed on a 900-series must not move."""
        from custom_components.roomba_plus.calendar import RoombaScheduleCalendar

        result = RoombaScheduleCalendar._with_entry(
            None, "cleanSchedule", {}, weekday=2, hour=9, minute=30
        )

        assert isinstance(result, dict)
        assert result["cycle"][2] == "start"

    def test_removal_branches_the_same_way(self) -> None:
        from custom_components.roomba_plus.calendar import RoombaScheduleCalendar

        modern = RoombaScheduleCalendar._without_day(
            None, MODERN_KEY, [_REAL_ENTRY], 6
        )
        legacy = RoombaScheduleCalendar._without_day(
            None, "cleanSchedule", {"cycle": ["start"] * 7, "h": [7] * 7, "m": [0] * 7}, 6
        )

        assert modern == []
        assert legacy["cycle"][6] == "none"
