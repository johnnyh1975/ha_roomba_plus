"""Writing a Classic robot's own cleaning schedule.

FIELD-CONFIRMED on a 900-series: the schedule was read, written back
unchanged, read again identical, and the iRobot app still showed it
afterwards. That last step is the one that counts -- this project has a
setting which accepts a write, reads back changed, and is ignored
entirely.
"""

import pytest

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
