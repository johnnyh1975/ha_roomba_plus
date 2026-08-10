import pytest
"""Which rooms a Prime robot considers dirty.

Classic derives dirt density from cloud mission records and builds a
weekday baseline from at least four of them. A Prime robot reports the
answer directly, and the response carries its own threshold.
"""

from types import SimpleNamespace


def _region(rid, score, unfinished=None):
    return SimpleNamespace(
        region_id=rid, clean_score=score, mission_last_unfinished=unfinished
    )


def _response(regions, ranges=None):
    return SimpleNamespace(
        clean_score_ranges=ranges if ranges is not None else [0.7],
        clean_scores=[SimpleNamespace(regions=regions)],
    )


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
