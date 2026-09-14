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
from tests.conftest import entry_mock

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

    entry = entry_mock()
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
        entry = entry_mock()
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
        entry = entry_mock()
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


class TestTheGateGetsALowerBoundNotAForecast:
    """Two consumers read the same estimates and need opposite things.

    A DISPLAYED figure -- a progress percentage, minutes remaining --
    built on an unconfident estimate looks exactly as authoritative as
    one built on a good estimate. `_compute_room_time_estimates()`
    returns None there on purpose, and a test above pins that.

    A GATE needs the opposite. "Has enough time passed in this room"
    has a useful answer even from a poor estimate, and refusing to
    answer means refusing the advance.

    @ScenicSystemsLLC's run fell down the second hole: with no confident
    estimate, the check fell back to a whole-house mission average
    divided by the rooms in that mission -- 10.7 hours, so 5.3 hours per
    room, a threshold his 17-minute mission could never cross.

    EACH REGION HOLDS ONE ESTIMATE PER SET OF CLEANING PARAMETERS. In
    Auto pass mode the robot picks a set mid-mission from the dirt it
    finds, so none can be chosen in advance. Averaging them is wrong --
    the vendor's own sample gives eighteen minutes under one set and
    thirty-four under another. The shortest is the point past which
    "enough time" can be true at all.
    """

    @staticmethod
    def _lower_bound(by_region, planned, rooms=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_rooms import (
            shortest_plausible_room_seconds,
        )

        entry = MagicMock()
        entry.runtime_data.prime_time_estimates = SimpleNamespace(
            by_region=by_region
        )
        entry.runtime_data.prime_schedule_coordinator = SimpleNamespace(
            room_names=rooms if rooms is not None else {"14": "Kitchen"}
        )
        return shortest_plausible_room_seconds(entry, planned)

    @staticmethod
    def _est(seconds, confident=True):
        from types import SimpleNamespace

        return SimpleNamespace(seconds=seconds, is_confident=confident)

    def test_the_shortest_of_several_parameter_sets_wins(self) -> None:
        """The vendor's own eighteen-versus-thirty-four sample."""
        out = self._lower_bound(
            {"14": [self._est(34 * 60), self._est(18 * 60)]}, ["Kitchen"]
        )

        assert out == [18 * 60]

    def test_an_unconfident_estimate_still_counts_here(self) -> None:
        """This is the whole difference from the displayed path."""
        out = self._lower_bound(
            {"14": [self._est(900, confident=False)]}, ["Kitchen"]
        )

        assert out == [900]

    def test_a_room_with_no_estimate_yields_none(self) -> None:
        assert self._lower_bound({"14": []}, ["Kitchen"]) == [None]

    def test_an_unknown_room_yields_none(self) -> None:
        assert self._lower_bound({"14": [self._est(900)]}, ["Cellar"]) == [None]

    def test_a_region_id_matches_directly(self) -> None:
        """Ids are the primary key; names are the fallback."""
        assert self._lower_bound({"14": [self._est(900)]}, ["14"]) == [900]

    def test_no_estimates_at_all_is_not_an_error(self) -> None:
        for empty in ({}, None):
            assert self._lower_bound(empty, ["Kitchen"]) == [None]

    def test_it_beats_the_whole_house_average_it_replaces(self) -> None:
        """His numbers: 640.4 min across the house, two rooms in the
        mission. The old path demanded 160 minutes in a room before it
        would accept a change."""
        old_per_room_threshold = (640.4 * 60 / 2) * 0.5
        new_per_room_threshold = 3184 * 0.5

        assert new_per_room_threshold < old_per_room_threshold / 5


class TestRememberedEstimatesSurviveTheCloudGoingQuiet:
    """The time-estimates response is fetched once at setup and held in
    memory. @ScenicSystemsLLC had `GOOD_CONFIDENCE` figures for his
    rooms in the morning and none by the evening -- same robot, same
    rooms, same day. The morning's numbers were used and discarded.

    KEYED BY ROOM **AND** PARAMETER SET. The cloud holds one estimate
    per set of cleaning parameters and they differ by up to a factor of
    two for the same room. A cache keyed by room alone would hand back
    the two-pass figure for a one-pass mission and nothing would say so.
    """

    @staticmethod
    def _entry(by_region, cache=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        entry = MagicMock()
        entry.runtime_data.prime_time_estimates = SimpleNamespace(
            by_region=by_region
        )
        entry.runtime_data.robot_profile_store = SimpleNamespace(
            room_estimate_cache=cache if cache is not None else {}
        )
        return entry

    @staticmethod
    def _est(seconds, params, confident=True):
        from types import SimpleNamespace

        return SimpleNamespace(
            seconds=seconds, is_confident=confident, params=params
        )

    def test_a_confident_estimate_is_remembered(self) -> None:
        from custom_components.roomba_plus.sensor_rooms import (
            remember_confident_estimates,
        )

        entry = self._entry({"14": [self._est(1080, {"twoPass": False})]})

        assert remember_confident_estimates(entry) == 1
        assert entry.runtime_data.robot_profile_store.room_estimate_cache

    def test_the_two_parameter_sets_are_kept_apart(self) -> None:
        """The eighteen-versus-thirty-four case. One slot each."""
        from custom_components.roomba_plus.sensor_rooms import (
            remember_confident_estimates,
        )

        entry = self._entry(
            {
                "14": [
                    self._est(18 * 60, {"twoPass": False}),
                    self._est(34 * 60, {"twoPass": True}),
                ]
            }
        )
        remember_confident_estimates(entry)
        cache = entry.runtime_data.robot_profile_store.room_estimate_cache

        assert len(cache) == 2
        assert sorted(cache.values()) == [18 * 60, 34 * 60]

    def test_an_unconfident_estimate_is_not_remembered(self) -> None:
        """Caching a figure the cloud does not trust would make it
        permanent."""
        from custom_components.roomba_plus.sensor_rooms import (
            remember_confident_estimates,
        )

        entry = self._entry(
            {"14": [self._est(900, {"twoPass": False}, confident=False)]}
        )

        assert remember_confident_estimates(entry) == 0

    def test_reading_back_gives_the_shortest_remembered_set(self) -> None:
        """Auto pass mode cannot say which set applies, and a gate wants
        the point past which 'enough time' can be true at all."""
        from custom_components.roomba_plus.sensor_rooms import (
            cached_room_seconds,
        )

        entry = self._entry({}, cache={"14|twoPass=False": 1080.0,
                                       "14|twoPass=True": 2040.0})

        assert cached_room_seconds(entry, "14") == 1080.0

    def test_another_room_is_not_matched_by_prefix(self) -> None:
        """Region "1" must not collide with region "14"."""
        from custom_components.roomba_plus.sensor_rooms import (
            cached_room_seconds,
        )

        entry = self._entry({}, cache={"14|twoPass=False": 1080.0})

        assert cached_room_seconds(entry, "1") is None

    def test_an_empty_cache_is_not_an_error(self) -> None:
        from custom_components.roomba_plus.sensor_rooms import (
            cached_room_seconds,
        )

        assert cached_room_seconds(self._entry({}), "14") is None
