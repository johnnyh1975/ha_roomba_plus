"""Writing a Classic robot's own cleaning schedule.

FIELD-CONFIRMED on a 900-series: the schedule was read, written back
unchanged, read again identical, and the iRobot app still showed it
afterwards. That last step is the one that counts -- this project has a
setting which accepts a write, reads back changed, and is ignored
entirely.
"""

import pytest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
import datetime
from custom_components.roomba_plus.classic_schedule_write import MODERN_KEY
from custom_components.roomba_plus.classic_schedule_write import ScheduleFormatError
from custom_components.roomba_plus.classic_schedule_write import schedule2_entry
from custom_components.roomba_plus.classic_schedule_write import schedule2_with_entry
from custom_components.roomba_plus.classic_schedule_write import schedule2_without_day
from custom_components.roomba_plus.schedule_parser import occurrences_from_schedule2

#: The real schedule from that robot: Thursday and Friday at 09:00,
#: Sunday at 10:30 and Saturday at 09:30, the rest off.
_REAL = {
    "cycle": ["none", "none", "none", "none", "start", "start", "none"],
    "h": [10, 9, 9, 9, 9, 9, 9],
    "m": [30, 0, 0, 0, 0, 0, 30],
}


class TestTheKeyComesFromTheRobot:
    """Two keys are in play across firmware generations. Writing under
    the wrong one would not replace the schedule, it would create a
    second, competing one."""

    def _key(self, reported):
        from custom_components.roomba_plus.classic_schedule_write import (
            schedule_key,
        )

        return schedule_key(reported)

    def test_the_legacy_key_is_recognised(self):
        """The 900-series robot this was confirmed on uses it."""
        assert self._key({"cleanSchedule": _REAL}) == "cleanSchedule"

    def test_the_modern_key_is_recognised(self):
        assert self._key({"cleanSchedule2": []}) == "cleanSchedule2"

    def test_a_robot_reporting_both_gets_the_modern_one(self):
        """That is the one its app maintains."""
        assert self._key(
            {"cleanSchedule": _REAL, "cleanSchedule2": []}
        ) == "cleanSchedule2"

    def test_a_robot_reporting_neither_gets_nothing(self):
        assert self._key({"batPct": 50}) is None


class TestSettingADay:
    def _set(self, current=None, **kwargs):
        from custom_components.roomba_plus.classic_schedule_write import (
            legacy_with_entry,
        )

        return legacy_with_entry(_REAL if current is None else current, **kwargs)

    def test_a_new_day_is_switched_on(self):
        result = self._set(weekday=2, hour=14, minute=15)

        assert result["cycle"][2] == "start"
        assert (result["h"][2], result["m"][2]) == (14, 15)

    def test_the_other_days_are_untouched(self):
        result = self._set(weekday=2, hour=14, minute=15)

        assert result["cycle"][4:6] == ["start", "start"]
        assert result["h"][0] == 10

    def test_a_day_that_already_has_one_is_replaced(self):
        """The format holds a single entry per weekday and has nowhere to
        put a second, so this is the only thing "create" can mean. Refusing
        would leave no way to change a day's time at all."""
        result = self._set(weekday=4, hour=6, minute=45)

        assert (result["h"][4], result["m"][4]) == (6, 45)
        assert result["cycle"].count("start") == 2

    def test_a_robot_with_no_schedule_yet_can_get_one(self):
        """Empty arrays are padded rather than rejected -- otherwise the
        first schedule would be the one case that cannot be created."""
        result = self._set({}, weekday=1, hour=8, minute=0)

        assert result["cycle"] == [
            "none", "start", "none", "none", "none", "none", "none"
        ]
        assert len(result["h"]) == 7

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"weekday": 7, "hour": 9, "minute": 0},
            {"weekday": -1, "hour": 9, "minute": 0},
            {"weekday": 0, "hour": 24, "minute": 0},
            {"weekday": 0, "hour": 9, "minute": 60},
        ],
    )
    def test_impossible_values_are_refused(self, kwargs):
        from custom_components.roomba_plus.classic_schedule_write import (
            ScheduleFormatError,
        )

        with pytest.raises(ScheduleFormatError):
            self._set(**kwargs)


class TestClearingADay:
    def _clear(self, weekday):
        from custom_components.roomba_plus.classic_schedule_write import (
            legacy_without_day,
        )

        return legacy_without_day(_REAL, weekday)

    def test_the_day_is_switched_off(self):
        assert self._clear(4)["cycle"][4] == "none"

    def test_its_time_is_kept(self):
        """A day set to "none" does not run whatever the time says, and
        keeping it means someone re-enabling that day gets their old
        setting back instead of midnight."""
        result = self._clear(0)

        assert (result["h"][0], result["m"][0]) == (10, 30)

    def test_the_other_days_survive(self):
        assert self._clear(4)["cycle"][5] == "start"


class TestWhatTheFormatCannotHold:
    """Refused rather than approximated, because approximating would put
    a robot on the floor at a time or in a room nobody chose."""

    def _reject(self, **kwargs):
        from custom_components.roomba_plus.classic_schedule_write import (
            reject_unsupported,
        )

        base = {"frequency": None, "rooms": None, "name": None}
        base.update(kwargs)
        return reject_unsupported(**base)

    def test_weekly_is_accepted(self):
        self._reject(frequency="WEEKLY")
        self._reject(frequency=None)

    @pytest.mark.parametrize("frequency", ["ONCE", "BI_WEEKLY", "MONTHLY", "DAILY"])
    def test_every_other_frequency_is_refused(self, frequency):
        from custom_components.roomba_plus.classic_schedule_write import (
            ScheduleFormatError,
        )

        with pytest.raises(ScheduleFormatError):
            self._reject(frequency=frequency)

    def test_rooms_are_refused(self):
        """A scheduled mission on this format always cleans everywhere."""
        from custom_components.roomba_plus.classic_schedule_write import (
            ScheduleFormatError,
        )

        with pytest.raises(ScheduleFormatError, match="no room selection"):
            self._reject(rooms=["Kitchen"])

    def test_a_name_is_dropped_rather_than_refused(self):
        """It changes nothing about what the robot does, so refusing over
        it would be pedantry -- unlike a frequency or a room, which change
        when and where the robot cleans."""
        self._reject(name="Morning clean")


# ── formerly tests/test_coverage_small_gaps.py ──────────────────────────────────
#
# Small gaps in eight modules — quality scale, test-coverage (Silver).
#
# Mostly error branches and edge cases: a malformed input must not crash,
# must not return something wrong, and where the code logs, it must log.
# Each test pins what the branch is for, not only that it ran.

class TestClassicScheduleWrite:

    @pytest.mark.parametrize("fn", ["legacy_without_day", "schedule2_without_day"])
    def test_a_weekday_outside_the_week_is_refused(self, fn):
        from custom_components.roomba_plus import classic_schedule_write as csw

        with pytest.raises(csw.ScheduleFormatError):
            getattr(csw, fn)({} if fn.startswith("legacy") else [], 7)

    def test_an_entry_with_a_bad_weekday_is_refused(self):
        from custom_components.roomba_plus import classic_schedule_write as csw

        with pytest.raises(csw.ScheduleFormatError):
            csw.schedule2_entry(weekdays=[9], hour=9, minute=0)

    def test_foreign_entries_are_kept_untouched(self):
        """Anything the robot holds that is not ours to change stays."""
        from custom_components.roomba_plus import classic_schedule_write as csw

        current = ["opaque", {"start": {"day": [2]}}]
        out = csw.schedule2_with_entry(current, weekday=1, hour=9, minute=0)
        assert out[0] == "opaque" and out[1] == {"start": {"day": [2]}}
        out = csw.schedule2_without_day(current, 1)
        assert out == current

    def test_an_entry_only_for_the_replaced_day_is_dropped(self):
        from custom_components.roomba_plus import classic_schedule_write as csw

        current = [{"start": {"day": [1], "hour": 7, "min": 0}}]
        out = csw.schedule2_with_entry(current, weekday=1, hour=9, minute=30)
        assert len(out) == 1
        assert out[0]["start"]["hour"] == 9


# ── formerly tests/test_schedule2_write.py ────────────────────────────
#
# Writing the modern schedule format.
#
# WHY THIS FILE EXISTS. The calendar wrote a LEGACY-shaped object under
# the MODERN key on every i/s/j robot, and nothing caught it: the tests
# all read, none wrote and read back. The firmware rejects the type
# outright (`'cleanSchedule2' is NOT of type: ARRAY`), so the schedule
# silently never changed -- and a rejected write looks exactly like an
# unchanged schedule to a user.
#
# The proof needed no robot: feed a real entry through the writer and
# back through this project's own parser, and the count goes from one to
# zero. That round trip is the test that was missing, and it is the first
# one below.

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
