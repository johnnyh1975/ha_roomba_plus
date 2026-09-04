"""Time estimates on the Prime path.

Two things went without them. The schedule calendar gave every
occurrence a flat hour, so a scheduled mission that finished in minutes
left the entity "On" for the rest of the hour (@chairstacker). And
`mission_progress` read "unknown" on every Prime robot, because the
per-room estimates it divides by come from the Classic cloud
coordinator, which Prime does not have (@DaRealGuGu).

One endpoint answers both. Its shape was unknown until an APK pass --
the only test in the library used `{"minutes": 30}`, invented; the real
key is `estimate` with the unit beside it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_RESPONSE = {
    "robot_id": "BLID",
    "time_estimates": [
        {"unit": "minute", "estimate": 84, "confidence": "GOOD_CONFIDENCE",
         "params": {}},
    ],
    "pmaps": [{
        "pmap_id": "M1",
        "regions": [
            {"region_id": "5", "time_estimates": [
                {"unit": "minute", "estimate": 18,
                 "confidence": "GOOD_CONFIDENCE", "params": {}}]},
            {"region_id": "7", "time_estimates": [
                {"unit": "minute", "estimate": 12,
                 "confidence": "GOOD_CONFIDENCE", "params": {}}]},
            {"region_id": "9", "time_estimates": [
                {"unit": "minute", "estimate": 30,
                 "confidence": "POOR_CONFIDENCE", "params": {}}]},
        ],
    }],
}


def _entry(*, response=_RESPONSE, rooms=None):
    from roombapy_prime.models import TimeEstimates

    entry = MagicMock()
    # `rooms if rooms is not None`, not `rooms or` -- an empty mapping
    # is a case worth testing and `or` would quietly swap in the
    # default, which is how this helper first hid the very assertion it
    # was written for.
    coordinator = SimpleNamespace(
        room_names=(
            {"5": "Kitchen", "7": "Hallway", "9": "Study"}
            if rooms is None else rooms
        )
    )
    entry.runtime_data = SimpleNamespace(
        cloud_coordinator=None,
        prime_time_estimates=(
            TimeEstimates.from_json(response) if response is not None else None
        ),
        prime_schedule_coordinator=coordinator,
    )
    return entry


class TestMissionProgressGetsEstimates:
    def _estimates(self, order, **kwargs):
        from custom_components.roomba_plus.sensor_rooms import (
            _compute_room_time_estimates,
        )

        return _compute_room_time_estimates(_entry(**kwargs), order)

    def test_rooms_are_matched_by_name(self):
        """The planned order is names, the estimates are keyed by region
        id. The schedule coordinator already keeps that mapping for the
        switch labels, so this reuses it rather than building a second
        one that could disagree."""
        assert self._estimates(["Kitchen", "Hallway"]) == [18 * 60, 12 * 60]

    def test_matching_ignores_case(self):
        assert self._estimates(["kitchen"]) == [18 * 60]

    def test_a_poorly_estimated_room_contributes_nothing(self):
        """A percentage built on a poor estimate looks exactly as
        authoritative as one built on a good estimate."""
        assert self._estimates(["Study"]) == [None]

    def test_an_unknown_room_contributes_nothing(self):
        assert self._estimates(["Cellar"]) == [None]

    def test_no_estimates_at_all_is_the_old_behaviour(self):
        assert self._estimates(["Kitchen"], response=None) == [None]

    def test_a_robot_with_no_room_names_yields_nothing(self):
        assert self._estimates(["Kitchen"], rooms={}) == [None]


class TestCalendarEventDuration:
    """A schedule says when it starts and never how long it takes."""

    def _calendar(self, *, response=_RESPONSE):
        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        cal._config_entry = _entry(response=response)
        return cal

    def _end(self, occurrence, **kwargs):
        return self._calendar(**kwargs)._estimated_end(occurrence)

    def test_named_rooms_are_summed(self):
        import datetime as dt

        start = dt.datetime(2026, 8, 4, 9, 0, tzinfo=dt.UTC)
        flat = start + dt.timedelta(hours=1)

        end = self._end((start, flat, ["5", "7"]))

        assert end == start + dt.timedelta(minutes=30)

    def test_a_whole_house_schedule_keeps_the_flat_hour(self):
        """NO WHOLE-MISSION FIGURE EXISTS. The app simulator's response
        had one and the real response does not, so this was built
        against a shape no robot returns.

        Summing every region would be a different number from what the
        robot does -- the rooms are not the whole floor. A poor estimate
        rather than a wrong one."""
        import datetime as dt

        start = dt.datetime(2026, 8, 4, 9, 0, tzinfo=dt.UTC)
        flat = start + dt.timedelta(hours=1)

        assert self._end((start, flat, [])) == flat

    def test_one_unknown_room_discards_the_whole_sum(self):
        """A partial total would be confidently short, and an event that
        ends too early is worse than one that ends too late: it reports
        "no mission running" while the robot is still working."""
        import datetime as dt

        start = dt.datetime(2026, 8, 4, 9, 0, tzinfo=dt.UTC)
        flat = start + dt.timedelta(hours=1)

        assert self._end((start, flat, ["5", "999"])) == flat

    def test_without_estimates_the_flat_hour_stands(self):
        import datetime as dt

        start = dt.datetime(2026, 8, 4, 9, 0, tzinfo=dt.UTC)
        flat = start + dt.timedelta(hours=1)

        assert self._end((start, flat, ["5"]), response=None) == flat


class TestTheRobotOverridesTheEstimate:
    """An estimate is about the future; a robot on its dock is about the
    present, and the present wins."""

    def _stopped(self, phase):
        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        cal = object.__new__(PrimeScheduleCalendar)
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator.data = {
            "ro-currentstate": {"cleanMissionStatus": {"phase": phase}}
        }
        cal._config_entry = entry
        return cal._robot_has_stopped()

    @pytest.mark.parametrize("phase", ["charge", "stop", "hmPostMsn", "hmUsrDock"])
    def test_resting_phases_end_the_event(self, phase):
        assert self._stopped(phase) is True

    @pytest.mark.parametrize("phase", ["run", "evac", "hmMidMsn", "pause"])
    def test_working_phases_do_not(self, phase):
        assert self._stopped(phase) is False

    def test_an_unknown_phase_leaves_the_window_standing(self):
        """Ending an event on a phase nobody has catalogued would be
        worse than ending it late."""
        assert self._stopped("somethingNew") is False
        assert self._stopped(None) is False


class TestTheScheduleSModeSelectsTheEstimate:
    """Every region carries dozens of estimates, one per parameter
    combination. Taking the first would quote the duration of a mode the
    schedule does not run -- @DaRealGuGu's first entry is
    `operatingMode 512` while his robot last ran `4`."""

    def _end(self, mode):
        import datetime as dt
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from roombapy_prime.models import TimeEstimates

        from custom_components.roomba_plus.calendar import PrimeScheduleCalendar

        response = {
            "smart_maps": [{
                "smart_map_id": "M1",
                "areas": [{"area_id": "10", "area_type": "region", "estimates": [
                    {"value": 3120, "unit": "seconds",
                     "params": {"operatingMode": 512}},
                    {"value": 600, "unit": "seconds",
                     "params": {"operatingMode": 4}},
                ]}],
            }],
        }
        cal = object.__new__(PrimeScheduleCalendar)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_time_estimates=TimeEstimates.from_json(response)
        )
        cal._config_entry = entry
        start = dt.datetime(2026, 8, 4, 9, 0, tzinfo=dt.UTC)
        flat = start + dt.timedelta(hours=1)
        occurrence = (start, flat, ["10"], None, "S1", mode)
        return (cal._estimated_end(occurrence) - start).total_seconds()

    def test_the_mopping_estimate_is_used_for_a_mopping_schedule(self):
        assert self._end(4) == 600

    def test_the_vacuum_then_mop_estimate_for_that_mode(self):
        assert self._end(512) == 3120

    def test_a_mode_with_no_estimate_falls_back_to_the_flat_hour(self):
        """Rather than quoting some other mode's duration."""
        assert self._end(32) == 3600

    def test_without_a_mode_any_estimate_will_do(self):
        assert self._end(None) in (600, 3120)
