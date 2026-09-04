import pytest
"""Which rooms a Prime robot considers dirty.

Classic derives dirt density from cloud mission records and builds a
weekday baseline from at least four of them. A Prime robot reports the
answer directly, and the response carries its own threshold.
"""

from types import SimpleNamespace


def _region(rid, score, unfinished=None, traffic=None, last_cleaned=None):
    """`SmartCleanRegionDto`, with the three fields we did not read:
    `high_traffic_enum`, `mission_last_cleaned` and the response's own
    `error`."""
    return {
        "region_id": rid,
        "clean_score": score,
        "updated_ts": 1786000000,
        "last_updated_by": "batch_decay",
        "high_traffic_enum": traffic,
        "mission_last_cleaned": last_cleaned,
        "mission_last_unfinished": unfinished,
        "smart_clean_prefs": {},
    }


def _response(regions, ranges=None):
    """THE VENDOR'S OWN SHAPE. `CleanScoreDto` carries `regions`
    directly; the `clean_scores` level an earlier version of these
    fixtures used appears nowhere in iRobot's model.

    That mistake passed every test, because the fixture was written to
    match the invented reader -- the same failure as the ONCE anchor
    earlier the same day."""
    return {
        "p2map_id": "M1",
        "error": None,
        "mission_last_processed": 62,
        "clean_score_ranges": ranges if ranges is not None else [0.7],
        "regions": regions,
    }


class TestTheThresholdComesFromTheServer:
    """Classic needs a multiplier because its baseline is our own
    arithmetic. Here the number to beat arrives in the same response as
    the values."""

    def _dirty(self, regions, ranges=None):
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        return dirty_rooms(_response(regions, ranges))

    def test_a_room_past_the_threshold_counts(self):
        """@jouwdan's oldest room: untouched for twenty missions."""
        assert self._dirty([_region("12", 0.6973)], ranges=[0.6]) == [
            ("12", 0.6973)
        ]

    def test_a_room_below_it_does_not(self):
        assert self._dirty([_region("12", 0.6973)]) == []

    def test_a_freshly_cleaned_room_never_counts(self):
        """Rooms cleaned by the newest mission read exactly zero."""
        assert self._dirty([_region("14", 0.0)]) == []

    def test_the_servers_own_number_is_used_not_ours(self):
        low = self._dirty([_region("12", 0.5)], ranges=[0.4])
        high = self._dirty([_region("12", 0.5)], ranges=[0.9])

        assert low and not high

    def test_a_missing_range_falls_back_rather_than_failing(self):
        """Observed on every capture, so this is a floor, not a default
        -- and it sits at the top of the observed range, because erring
        towards "not dirty" costs a delayed clean while erring the other
        way sends a robot into a room somebody is sitting in."""
        assert self._dirty([_region("12", 0.75)], ranges=[]) == [("12", 0.75)]
        assert self._dirty([_region("12", 0.5)], ranges=None) == []

    def test_an_unreadable_range_does_not_lower_the_bar(self):
        assert self._dirty([_region("12", 0.5)], ranges=["nonsense"]) == []


class TestTheDirtiestComesFirst:
    """So a caller that only wants one room gets the room that most
    needs it."""

    def test_ordering(self):
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        result = dirty_rooms(_response([
            _region("10", 0.72), _region("12", 0.95), _region("11", 0.80),
        ]))

        assert [rid for rid, _ in result] == ["12", "11", "10"]


class TestNothingUsableMeansNothingToDo:
    """Empty is the same answer as "nothing is dirty" to a caller, and
    deliberately so: both mean do nothing."""

    def _dirty(self, response):
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        return dirty_rooms(response)

    def test_no_response(self):
        assert self._dirty(None) == []

    def test_no_regions(self):
        assert self._dirty(_response([])) == []

    def test_a_region_without_a_score(self):
        assert self._dirty(_response([_region("12", None)])) == []

    def test_a_region_without_an_id(self):
        assert self._dirty(_response([_region(None, 0.9)])) == []


class TestUnfinishedRoomsAreASeparateQuestion:
    """Nothing else answers it. @chairstacker reported a mission that
    failed on a blocked door and left no trace anywhere."""

    def test_a_room_left_unfinished_is_listed(self):
        from custom_components.roomba_plus.prime_dirt import unfinished_rooms

        result = unfinished_rooms(_response([
            _region("10", 0.1, unfinished={"nMssn": 62}),
            _region("11", 0.9),
        ]))

        assert result == ["10"]

    def test_dirtiness_and_unfinishedness_do_not_imply_each_other(self):
        """A room can be spotless and unfinished, or filthy and
        completed."""
        from custom_components.roomba_plus.prime_dirt import (
            dirty_rooms,
            unfinished_rooms,
        )

        response = _response([
            _region("10", 0.0, unfinished={"nMssn": 62}),
            _region("11", 0.95),
        ])

        assert unfinished_rooms(response) == ["10"]
        assert [rid for rid, _ in dirty_rooms(response)] == ["11"]


class TestThePrimeEvaluationPath:
    """The gates are the same — presence, blocking sensors and the enable
    switch are questions about the household, not about the robot's
    generation. What differs is everything before and after them.

    Classic derives dirt density from cloud records, builds a weekday
    baseline from at least four, and compares with a user-set multiplier:
    three layers of inference. Then it starts a **whole** clean, because
    a density averaged over a mission cannot say which room was dirty.
    """

    def _manager(self, *, blocked=False, scores=None, selected="M1"):
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.dirt_threshold_manager import (
            DirtThresholdManager,
        )

        mgr = object.__new__(DirtThresholdManager)
        robot = AsyncMock()
        robot.get_clean_score_raw.return_value = scores
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_robot=robot, prime_selected_map_id=selected
        )
        mgr._entry = entry
        mgr._hass = MagicMock()
        mgr._last_trigger_time = None
        mgr.gate_blocked = MagicMock(
            return_value=(blocked, "not_all_away" if blocked else "")
        )
        mgr.async_save = AsyncMock()
        backend = AsyncMock()
        return mgr, backend, robot, patch(
            "custom_components.roomba_plus.dirt_threshold_manager."
            "async_get_room_cleaning_backend",
            return_value=backend,
        )

    async def _run(self, **kwargs):
        mgr, backend, robot, patched = self._manager(**kwargs)
        with patched:
            await mgr.async_evaluate_prime("E1")
        return backend, robot

    @pytest.mark.asyncio
    async def test_the_dirtiest_room_is_cleaned(self):
        backend, _ = await self._run(scores=_response([
            _region("10", 0.75), _region("12", 0.95),
        ]))

        backend.clean_rooms.assert_awaited_once_with(["12"])

    @pytest.mark.asyncio
    async def test_only_one_room_per_evaluation(self):
        """Sending the robot to everything above the line would be a
        whole-house clean by another name on a bad week, and the point of
        a per-room score is to do less than that."""
        backend, _ = await self._run(scores=_response([
            _region("10", 0.75), _region("11", 0.80), _region("12", 0.95),
        ]))

        assert len(backend.clean_rooms.await_args.args[0]) == 1

    @pytest.mark.asyncio
    async def test_a_blocked_gate_stops_everything(self):
        """Somebody is home. The robot is not asked, let alone sent."""
        backend, robot = await self._run(blocked=True)

        backend.clean_rooms.assert_not_awaited()
        robot.get_clean_score_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_dirty_means_nothing_started(self):
        backend, _ = await self._run(scores=_response([_region("10", 0.1)]))

        backend.clean_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_map_the_score_is_not_even_requested(self):
        from unittest.mock import AsyncMock

        mgr, backend, robot, patched = self._manager(selected=None)
        backend._current_map_id = AsyncMock(return_value=None)
        with patched:
            await mgr.async_evaluate_prime("E1")

        robot.get_clean_score_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_robots_current_map_is_used_when_none_is_selected(self):
        from unittest.mock import AsyncMock

        mgr, backend, robot, patched = self._manager(
            selected=None, scores=_response([_region("12", 0.95)])
        )
        backend._current_map_id = AsyncMock(return_value="M-CURRENT")
        with patched:
            await mgr.async_evaluate_prime("E1")

        robot.get_clean_score_raw.assert_awaited_once_with("M-CURRENT")


class TestTheVendorsOwnResponseShape:
    """`CleanScoreDto` carries `regions` DIRECTLY. A first version of
    this module looked for a `clean_scores` level that appears nowhere
    in iRobot's model — **and every test passed, because the fixtures
    were written to match the invented shape.**

    The same failure as the ONCE anchor earlier the same day: test and
    code agreed with each other and neither agreed with the vendor.
    """

    #: Verbatim from the v3.0.0 CleanScoreDto / SmartCleanRegionDto
    #: serialisers.
    VENDOR = {
        "p2map_id": "M1", "error": None, "smart_clean_id": "S1",
        "active_p2mapv_id": "V1", "user_p2mapv_id": "V1",
        "mission_last_processed": 62,
        "regions": [
            {"region_id": "11", "clean_score": 0.85, "updated_ts": 1,
             "last_updated_by": "x", "high_traffic_enum": "HIGH",
             "mission_last_cleaned": 60, "mission_last_unfinished": None,
             "smart_clean_prefs": {}},
            {"region_id": "12", "clean_score": 0.1, "updated_ts": 1,
             "last_updated_by": "y", "high_traffic_enum": "LOW",
             "mission_last_cleaned": 62,
             "mission_last_unfinished": {"nMssn": 61},
             "smart_clean_prefs": {}},
        ],
    }

    def test_a_real_response_yields_the_dirty_room(self):
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        assert dirty_rooms(self.VENDOR) == [("11", 0.85)]

    def test_a_real_response_yields_the_unfinished_room(self):
        from custom_components.roomba_plus.prime_dirt import unfinished_rooms

        assert unfinished_rooms(self.VENDOR) == ["12"]

    def test_the_nested_form_is_still_accepted(self):
        """A multi-map account may return one document per map, and
        tolerating both costs one line."""
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        nested = {"clean_scores": [{"regions": self.VENDOR["regions"]}]}

        assert dirty_rooms(nested) == [("11", 0.85)]


class TestTheFieldsWeWereNotReading:
    """`SmartCleanRegionDto` carries more than a score, and only the
    score was being read.

    A room can be clean because nobody walks through it, or clean
    because it was done an hour ago. The score alone does not tell those
    apart; `high_traffic_enum` and `mission_last_cleaned` do.
    """

    def test_traffic_banding_is_exposed(self):
        from custom_components.roomba_plus.prime_dirt import room_details

        details = room_details(_response([
            _region("11", 0.9, traffic="HIGH", last_cleaned=60),
        ]))

        assert details["11"]["high_traffic"] == "HIGH"

    def test_the_last_cleaning_mission_is_exposed(self):
        from custom_components.roomba_plus.prime_dirt import room_details

        details = room_details(_response([_region("11", 0.9, last_cleaned=60)]))

        assert details["11"]["last_cleaned_mission"] == 60

    def test_a_room_with_nothing_reported_still_appears(self):
        """An entry with empty fields is information; a missing entry is
        indistinguishable from a room that does not exist."""
        from custom_components.roomba_plus.prime_dirt import room_details

        details = room_details(_response([_region("11", None)]))

        assert "11" in details


class TestAnErrorInTheResponseIsNotSilence:
    """A cloud that answers with an error object rather than an HTTP
    failure looks like a successful call returning no dirty rooms --
    which is exactly the shape of "nothing needs cleaning"."""

    def test_a_structured_error_is_read(self):
        from custom_components.roomba_plus.prime_dirt import response_error

        raw = _response([])
        raw["error"] = {"description": "map not ready"}

        assert response_error(raw) == "map not ready"

    def test_a_plain_string_error_is_read(self):
        from custom_components.roomba_plus.prime_dirt import response_error

        raw = _response([])
        raw["error"] = "boom"

        assert response_error(raw) == "boom"

    def test_no_error_reads_as_none(self):
        from custom_components.roomba_plus.prime_dirt import response_error

        assert response_error(_response([])) is None


class TestBothResponseShapesAreAccepted:
    def test_regions_directly_on_the_document(self):
        """The vendor's shape."""
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        assert dirty_rooms(_response([_region("11", 0.9)])) == [("11", 0.9)]

    def test_regions_nested_under_clean_scores(self):
        """Tolerated because a multi-map account may return one document
        per map, and accepting both costs one line."""
        from custom_components.roomba_plus.prime_dirt import dirty_rooms

        nested = {
            "clean_score_ranges": [0.7],
            "clean_scores": [{"regions": [_region("11", 0.9)]}],
        }

        assert dirty_rooms(nested) == [("11", 0.9)]


class TestUnfinishedIsAnObjectNotAFlag:
    """`mission_last_unfinished` is structured:
    `CleanScoreDto$SmartCleanMissionInfoDto` declares `startTime`,
    `nMssn` and `missionId`. This module read it for truthiness alone.

    With the mission number a caller can say "the kitchen was left
    undone by mission 61, and 62 has since run" — the difference
    between a room still waiting and one picked up on the next pass.
    """

    def _info(self, regions):
        from custom_components.roomba_plus.prime_dirt import unfinished_missions

        return unfinished_missions(_response(regions))

    def test_the_mission_number_is_available(self):
        info = self._info([
            _region("11", 0.1, unfinished={
                "missionId": "M1", "nMssn": 61, "startTime": 1786000000,
            }),
        ])

        assert info["11"]["mission_number"] == 61
        assert info["11"]["mission_id"] == "M1"

    def test_a_finished_room_is_absent(self):
        assert self._info([_region("11", 0.1)]) == {}

    def test_the_boolean_view_still_works(self):
        """`unfinished_rooms` stays — a caller that only wants the list
        should not have to unpack objects."""
        from custom_components.roomba_plus.prime_dirt import unfinished_rooms

        regions = [_region("11", 0.1, unfinished={"nMssn": 61})]

        assert unfinished_rooms(_response(regions)) == ["11"]

    def test_a_bare_truthy_value_does_not_crash(self):
        """Older firmware may send something simpler than the DTO."""
        info = self._info([_region("11", 0.1, unfinished=True)])

        assert info["11"]["mission_number"] is None


class TestMissionInfoFieldsAreObjects:
    """`mission_last_cleaned` and `mission_last_processed` are
    `SmartCleanMissionInfoDto` objects — `startTime`, `nMssn`,
    `missionId` — exactly like `mission_last_unfinished`.

    This module labelled the first as if it were already a number, so a
    caller reading `last_cleaned_mission` got a dict where an integer
    was promised. Found by comparing declared **types**, not key names:
    the wire-key list says nothing about them.
    """

    def _detail(self, value):
        from custom_components.roomba_plus.prime_dirt import room_details

        regions = [_region("11", 0.5)]
        regions[0]["mission_last_cleaned"] = value
        return room_details(_response(regions))["11"]["last_cleaned_mission"]

    def test_the_object_yields_its_mission_number(self):
        assert self._detail({"nMssn": 60, "missionId": "M1"}) == 60

    def test_a_plain_number_is_accepted(self):
        """No capture has shown one, and rejecting it would trade a
        working value for a tidy type."""
        assert self._detail(60) == 60

    def test_nothing_reported_is_none(self):
        assert self._detail(None) is None

    def test_an_object_without_a_number_is_none_not_a_dict(self):
        """The promise is an integer; half an object is worse than
        nothing because it looks like a value."""
        assert self._detail({"missionId": "M1"}) is None


class TestPerRoomPreferences:
    """`smart_clean_prefs` is typed `RegionParamsDTO` in `CleanScoreDto`
    — the same eight keys a region command carries: `operatingMode`,
    `suctionLevel`, `padWetness`, `twoPass` and the rest.

    That makes it the server's record of "always mop the kitchen": a
    per-room preference this integration has no other way to see, and
    which explains why a room can clean differently from the robot's
    global settings.

    Empty on every capture so far, so nothing is built on it — it is
    carried so a robot that does use it is not silently ignored.
    """

    def _prefs(self, value):
        from custom_components.roomba_plus.prime_dirt import room_details

        regions = [_region("11", 0.5)]
        regions[0]["smart_clean_prefs"] = value
        return room_details(_response(regions))["11"]["preferences"]

    def test_a_rooms_own_settings_are_kept(self):
        assert self._prefs({"operatingMode": 4, "padWetness": 2}) == {
            "operatingMode": 4, "padWetness": 2
        }

    def test_an_empty_block_reads_as_none(self):
        """Which is what every capture so far shows. An empty dict and
        no preferences are the same thing to a caller, and None says so
        without them having to check the length."""
        assert self._prefs({}) is None

    def test_absent_reads_as_none(self):
        assert self._prefs(None) is None
