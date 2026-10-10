"""Live position for Classic robots that publish none (4.3).

The stream asks the robot where it is while it moves and the shadow
carries no `pose`, hands each answer to its listeners, and gives up for
the rest of a mission when asking is pointless.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from roombapy import RobotPosition, RrtpUnsupportedError

from custom_components.roomba_plus import live_position as lp
from custom_components.roomba_plus.live_position import LivePositionStream


class _Robot:
    """Enough of RoombaClient: a shadow, callbacks and a position stream."""

    def __init__(self) -> None:
        self.master_state: dict[str, Any] = {"state": {"reported": {}}}
        self.callbacks: list[Any] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.streams_opened = 0
        self.streams_closed = 0
        #: roombapy 2.0.2's per-connection verdict: set when a stream
        #: ends in RrtpUnsupportedError, after which every stream is
        #: refused without asking until a reconnect clears it.
        self._position_unsupported = False
        self.refused = 0

    def register_on_message_callback(self, cb: Any) -> Any:
        self.callbacks.append(cb)
        return lambda: self.callbacks.remove(cb)

    async def watch_position(self) -> Any:
        if getattr(self, "_position_unsupported", False):
            self.refused += 1
            raise RrtpUnsupportedError("did not answer earlier in this connection")
        self.streams_opened += 1
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, RrtpUnsupportedError):
                    self._position_unsupported = True
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self.streams_closed += 1

    def report(self, *, phase: str, cycle: str = "clean", pose: Any = None) -> None:
        reported: dict[str, Any] = {
            "cleanMissionStatus": {"phase": phase, "cycle": cycle}
        }
        if pose is not None:
            reported["pose"] = pose
        self.master_state = {"state": {"reported": reported}}
        for cb in list(self.callbacks):
            cb({"state": {"reported": reported}})


def _pos(x: float = 1.0, y: float = 2.0, source: str = "request") -> RobotPosition:
    return RobotPosition(
        x=x, y=y, theta=0.5, timestamp=1, source=source,  # type: ignore[arg-type]
        pmap_id="p" if source == "request" else None,
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _stream(hass: Any, robot: _Robot, enabled: list[bool] | None = None) -> LivePositionStream:
    flag = enabled if enabled is not None else [True]
    stream = LivePositionStream(hass, robot, enabled=lambda: flag[0])
    stream.start()
    return stream


class TestWhenItAsks:
    @pytest.mark.asyncio
    async def test_no_stream_without_a_mission(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="charge", cycle="none")
        await _settle()
        assert robot.streams_opened == 0
        assert stream.status == lp.STATUS_IDLE
        stream.stop()

    @pytest.mark.asyncio
    async def test_positions_reach_listeners_while_cleaning(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        seen: list[RobotPosition] = []
        stream.add_listener(seen.append)

        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos(1.0, 2.0))
        await _settle()

        assert robot.streams_opened == 1
        assert [p.x for p in seen] == [1.0]
        assert stream.latest is not None and stream.latest.y == 2.0
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_never_for_a_robot_that_publishes_its_pose(self, hass: Any) -> None:
        """A 900-series: its own pose is free and already drawn."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run", pose={"point": {"x": 1, "y": 2}, "theta": 0})
        await _settle()
        assert robot.streams_opened == 0
        assert stream.status == lp.STATUS_POSE_IN_SHADOW
        stream.stop()

    @pytest.mark.asyncio
    async def test_stops_when_the_robot_docks(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.report(phase="charge", cycle="none")
        await _settle()
        assert robot.streams_closed == 1
        assert stream.status == lp.STATUS_IDLE
        stream.stop()

    @pytest.mark.asyncio
    async def test_the_option_is_read_on_every_message(self, hass: Any) -> None:
        """Switching it on takes effect without a reload, as the other map
        options do."""
        robot = _Robot()
        flag = [False]
        stream = _stream(hass, robot, flag)
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 0
        assert stream.status == lp.STATUS_OFF

        flag[0] = True
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 1

        flag[0] = False
        robot.report(phase="run")
        await _settle()
        assert robot.streams_closed == 1
        stream.stop()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_and_closes_the_stream(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        stream.stop()
        await _settle()
        assert robot.callbacks == []
        assert robot.streams_closed == 1


class TestWhenItGivesUp:
    @pytest.mark.asyncio
    async def test_unsupported_is_not_asked_again_this_mission(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(RrtpUnsupportedError("silent"))
        await _settle()
        assert stream.status == lp.STATUS_UNSUPPORTED

        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 1

        # The next mission asks again: the robot may have been updated.
        robot.report(phase="charge", cycle="none")
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 2
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_no_fix_within_the_deadline(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Braava jet m6 answers without a fix for a whole run; the
        library's silence detection never fires for that."""
        monkeypatch.setattr(lp, "NO_FIX_GIVE_UP_S", 0.01)
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await asyncio.sleep(0.05)
        await _settle()
        assert stream.status == lp.STATUS_NO_FIX
        assert robot.streams_closed == 1
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 1
        stream.stop()

    @pytest.mark.asyncio
    async def test_the_deadline_is_for_the_first_position_only(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lp, "NO_FIX_GIVE_UP_S", 0.01)
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await asyncio.sleep(0.05)
        await _settle()
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_a_shadow_position_ends_it_and_is_not_handed_on(
        self, hass: Any
    ) -> None:
        """roombapy converts shadow poses as millimetres; this integration
        measured centimetres. The map reads the shadow itself."""
        robot = _Robot()
        stream = _stream(hass, robot)
        seen: list[RobotPosition] = []
        stream.add_listener(seen.append)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos(source="shadow"))
        await _settle()
        assert seen == []
        assert stream.latest is None
        assert stream.status == lp.STATUS_POSE_IN_SHADOW
        stream.stop()

    @pytest.mark.asyncio
    async def test_an_error_restarts_on_the_next_message_then_gives_up(
        self, hass: Any
    ) -> None:
        """A dropped connection ends the stream; the robot's next message
        starts it again -- a bounded number of times."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        for _ in range(lp.MAX_RESTARTS_PER_MISSION):
            robot.queue.put_nowait(ConnectionError("gone"))
            await _settle()
            robot.report(phase="run")
            await _settle()
        assert robot.streams_opened == lp.MAX_RESTARTS_PER_MISSION
        assert stream.status == lp.STATUS_ERROR
        assert stream.last_error == "ConnectionError"
        stream.stop()

    @pytest.mark.asyncio
    async def test_a_failing_listener_does_not_end_the_stream(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        seen: list[RobotPosition] = []

        def _broken(_p: RobotPosition) -> None:
            raise RuntimeError("consumer bug")

        stream.add_listener(_broken)
        stream.add_listener(seen.append)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        robot.queue.put_nowait(_pos(3.0))
        await _settle()
        assert [p.x for p in seen] == [1.0, 3.0]
        stream.stop()
        await _settle()


class TestARobotThatHasAnsweredStalls:
    """4.3.4, @pk-1966: an i7+ answered for eight minutes, then left three
    requests unanswered for a minute and answered them all at once. The
    library's "does not implement rrtp" is a stall for a robot that has
    answered before."""

    @pytest.fixture(autouse=True)
    def _short_pauses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lp, "STALL_RETRY_DELAYS_S", (0.02, 0.04))

    async def _stall(self, robot: _Robot) -> None:
        robot.queue.put_nowait(RrtpUnsupportedError("silent"))
        await _settle()

    @pytest.mark.asyncio
    async def test_it_asks_again_after_a_pause(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        seen: list[RobotPosition] = []
        stream.add_listener(seen.append)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos(1.0))
        await _settle()
        await self._stall(robot)

        assert stream.status == lp.STATUS_STALLED
        assert stream.diagnostics()["given_up_this_mission"] is False
        # Not during the pause, however often the robot reports.
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 1

        await asyncio.sleep(0.05)
        await _settle()
        assert robot.streams_opened == 2
        assert robot.refused == 0
        robot.queue.put_nowait(_pos(5.0))
        await _settle()
        assert [p.x for p in seen] == [1.0, 5.0]
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_a_robot_that_never_answered_is_still_given_up_on(
        self, hass: Any
    ) -> None:
        """An i3 on daredevil: 2,875 requests, no reply."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        await self._stall(robot)
        await asyncio.sleep(0.05)
        robot.report(phase="run")
        await _settle()
        assert stream.status == lp.STATUS_UNSUPPORTED
        assert robot.streams_opened == 1
        stream.stop()

    @pytest.mark.asyncio
    async def test_the_pauses_grow_and_the_stalls_are_bounded(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lp, "MAX_STALLS_PER_MISSION", 2)
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()

        await self._stall(robot)
        await asyncio.sleep(0.03)
        await _settle()
        assert robot.streams_opened == 2
        await self._stall(robot)
        # The second pause is the longer one.
        await asyncio.sleep(0.03)
        await _settle()
        assert robot.streams_opened == 2
        await asyncio.sleep(0.03)
        await _settle()
        assert robot.streams_opened == 3

        await self._stall(robot)
        assert stream.status == lp.STATUS_STALLED
        assert stream.diagnostics()["given_up_this_mission"] is True
        await asyncio.sleep(0.1)
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 3
        stream.stop()

    @pytest.mark.asyncio
    async def test_the_end_of_the_mission_cancels_the_pause(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()
        await self._stall(robot)
        robot.report(phase="charge", cycle="none")
        await _settle()
        assert stream.status == lp.STATUS_IDLE
        assert stream.diagnostics()["stalls_this_mission"] == 0
        await asyncio.sleep(0.05)
        await _settle()
        assert robot.streams_opened == 1
        stream.stop()

    @pytest.mark.asyncio
    async def test_a_pause_ending_at_a_recharge_leaves_it_idle(
        self, hass: Any
    ) -> None:
        """Back in the dock mid-mission when the pause ends: nothing is
        asked, and nothing is stalled either."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()
        await self._stall(robot)
        robot.report(phase="charge", cycle="clean")
        await asyncio.sleep(0.05)
        await _settle()
        assert stream.status == lp.STATUS_IDLE
        assert robot.streams_opened == 1
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 2
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_stop_cancels_the_pause(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()
        await self._stall(robot)
        stream.stop()
        await asyncio.sleep(0.05)
        await _settle()
        assert robot.streams_opened == 1
        assert stream.diagnostics()["waiting_to_ask_again"] is False

    @pytest.mark.asyncio
    async def test_switching_it_off_cancels_the_pause(self, hass: Any) -> None:
        robot = _Robot()
        flag = [True]
        stream = _stream(hass, robot, flag)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()
        await self._stall(robot)
        flag[0] = False
        robot.report(phase="run")
        await _settle()
        assert stream.diagnostics()["waiting_to_ask_again"] is False
        assert stream.status == lp.STATUS_OFF
        stream.stop()


class TestTheLibrarysVerdictDoesNotOutliveTheMission:
    """roombapy keeps "does not answer" until the connection drops and
    refuses every stream without asking. The next mission meant to ask
    again, and got the refusal."""

    @pytest.mark.asyncio
    async def test_the_next_mission_asks_the_robot(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(RrtpUnsupportedError("silent"))
        await _settle()
        assert robot._position_unsupported is True

        robot.report(phase="charge", cycle="none")
        robot.report(phase="run")
        await _settle()
        assert robot.refused == 0
        assert robot.streams_opened == 2
        robot.queue.put_nowait(_pos())
        await _settle()
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_a_client_without_the_attribute_is_left_alone(
        self, hass: Any
    ) -> None:
        robot = _Robot()
        del robot._position_unsupported
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        assert not hasattr(robot, "_position_unsupported")
        assert robot.streams_opened == 1
        assert stream.last_error is None
        stream.stop()
        await _settle()


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_counts_and_status_without_coordinates(self, hass: Any) -> None:
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos(12.345, 67.891))
        await _settle()

        diag = stream.diagnostics()
        assert diag["positions_received"] == 1
        assert diag["status"] == lp.STATUS_STREAMING
        assert diag["has_map_id"] is True
        assert "12.345" not in repr(diag) and "67.891" not in repr(diag)
        stream.stop()
        await _settle()


class TestReviewFindings:
    """Found in the independent review of the first version."""

    @pytest.mark.asyncio
    async def test_cancelled_and_wanted_back_in_one_turn_restarts(self, hass: Any) -> None:
        """lewis sends brief `charge` bursts between rooms. Two messages in
        one loop turn cancelled the stream and found the dying task still
        "running", so nothing restarted it."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        robot.report(phase="charge", cycle="none")
        robot.report(phase="run")
        await _settle()
        assert robot.streams_opened == 2
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_a_stream_that_cannot_open_is_counted(self, hass: Any) -> None:
        robot = _Robot()

        def _broken() -> Any:
            raise AttributeError("no watch_position on this client")

        robot.watch_position = _broken  # type: ignore[method-assign]
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        assert stream.last_error == "AttributeError"
        assert stream.status == lp.STATUS_IDLE
        stream.stop()

    @pytest.mark.asyncio
    async def test_drops_after_good_positions_do_not_use_up_the_budget(
        self, hass: Any
    ) -> None:
        """Flaky Wi-Fi on a long mission: each drop follows a working
        stream, so the live path must not be lost for the rest of it."""
        robot = _Robot()
        stream = _stream(hass, robot)
        robot.report(phase="run")
        await _settle()
        for _ in range(lp.MAX_RESTARTS_PER_MISSION + 2):
            robot.queue.put_nowait(_pos())
            robot.queue.put_nowait(ConnectionError("blip"))
            await _settle()
            robot.report(phase="run")
            await _settle()
        assert stream.status == lp.STATUS_STREAMING
        stream.stop()
        await _settle()

    @pytest.mark.asyncio
    async def test_switched_off_forgets_the_last_position(self, hass: Any) -> None:
        robot = _Robot()
        flag = [True]
        stream = _stream(hass, robot, flag)
        robot.report(phase="run")
        await _settle()
        robot.queue.put_nowait(_pos())
        await _settle()
        assert stream.latest is not None
        flag[0] = False
        robot.report(phase="run")
        await _settle()
        assert stream.latest is None
        stream.stop()
