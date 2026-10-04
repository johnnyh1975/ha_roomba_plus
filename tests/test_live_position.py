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

    def register_on_message_callback(self, cb: Any) -> Any:
        self.callbacks.append(cb)
        return lambda: self.callbacks.remove(cb)

    async def watch_position(self) -> Any:
        self.streams_opened += 1
        try:
            while True:
                item = await self.queue.get()
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
