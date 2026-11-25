"""Connect attempts that do not complete close their socket (#265).

A Classic robot takes one local connection at a time. A socket left
behind by a failed or abandoned attempt is that connection, and every
later attempt then times out too.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import aiomqtt
import pytest
import pytest_asyncio
from roombapy import roomba as roomba_module

from custom_components.roomba_plus import mqtt_session


class _SilentBroker:
    """A broker that accepts the connection and never answers CONNECT.

    That is what a robot looks like while something else holds its one
    local connection.
    """

    def __init__(self) -> None:
        self.port = 0
        self.accepted = 0
        self.connections: set[asyncio.StreamWriter] = set()

    async def hold(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.accepted += 1
        self.connections.add(writer)
        try:
            while await reader.read(1024):
                pass
        finally:
            self.connections.discard(writer)
            writer.close()

    async def all_closed(self) -> bool:
        """Whether every accepted connection is gone within a few seconds."""
        try:
            async with asyncio.timeout(3):
                while self.connections:
                    await asyncio.sleep(0.05)
        except TimeoutError:
            return False
        return self.accepted > 0


@pytest_asyncio.fixture
async def silent_broker(socket_enabled: None) -> AsyncIterator[_SilentBroker]:
    """Serve a broker that never answers, on a free local port.

    `socket_enabled` lifts the test harness's socket block for this one
    loopback listener: the bug is about real sockets.
    """
    broker = _SilentBroker()
    server = await asyncio.start_server(broker.hold, "127.0.0.1", 0)
    broker.port = server.sockets[0].getsockname()[1]
    yield broker
    server.close()
    for writer in list(broker.connections):
        writer.close()
    await server.wait_closed()


@pytest.fixture
def roombapy_aiomqtt() -> Iterator[None]:
    """Start from roombapy's own aiomqtt, and put back what was there.

    Setup tests elsewhere in the suite install the view for real, so the
    state a test finds depends on what ran before it.
    """
    before = roomba_module.aiomqtt
    roomba_module.aiomqtt = aiomqtt
    yield
    roomba_module.aiomqtt = before


async def _enter(port: int, *, connack_wait: float) -> None:
    async with mqtt_session.ClosingClient(
        "127.0.0.1", port, timeout=connack_wait
    ):
        pytest.fail("the silent broker answered CONNECT")


async def test_a_connect_that_times_out_closes_its_socket(
    silent_broker: _SilentBroker,
) -> None:
    """aiomqtt's own CONNACK timeout leaves the socket open; this does not."""
    with pytest.raises(aiomqtt.MqttError, match="timed out"):
        await _enter(silent_broker.port, connack_wait=0.5)

    assert await silent_broker.all_closed()


async def test_a_connect_the_caller_gives_up_on_closes_its_socket(
    silent_broker: _SilentBroker,
) -> None:
    """Setup and the flow bound the connect; giving up must free the robot.

    The caller returns at once; the attempt it abandoned is closed as soon
    as aiomqtt's own timeout ends it.
    """
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.3):
            await _enter(silent_broker.port, connack_wait=1)

    assert await silent_broker.all_closed()
    assert not mqtt_session._ABANDONED


@pytest.mark.usefixtures("roombapy_aiomqtt")
def test_roombapy_builds_the_closing_client() -> None:
    """The supervisor's `aiomqtt.Client(...)` must reach the closing one."""
    assert mqtt_session.ensure_installed()
    assert mqtt_session.ensure_installed()  # idempotent

    assert roomba_module.aiomqtt.Client is mqtt_session.ClosingClient
    # Everything else stays aiomqtt's, or the supervisor's
    # `except aiomqtt.MqttError` would stop matching.
    assert roomba_module.aiomqtt.MqttError is aiomqtt.MqttError


@pytest.mark.usefixtures("roombapy_aiomqtt")
def test_a_roombapy_with_the_fix_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pschmitt/roombapy#631 makes this unnecessary; it must step aside."""
    monkeypatch.setattr(
        roomba_module, "_open_session", object(), raising=False
    )

    assert not mqtt_session.ensure_installed()
    assert roomba_module.aiomqtt is aiomqtt


@pytest.mark.usefixtures("roombapy_aiomqtt")
def test_an_unknown_layout_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Something other than aiomqtt in that place is not replaced."""
    stranger = object()
    monkeypatch.setattr(roomba_module, "aiomqtt", stranger)

    assert not mqtt_session.ensure_installed()
    assert roomba_module.aiomqtt is stranger
