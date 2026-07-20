"""Tests for custom_components.roomba_plus.prime_coordinator.

NEW (V4/Prime implementation). Constructs a REAL PrimeCoordinator via its
actual __init__ (not the object.__new__() bypass used elsewhere in this
test suite for IrobotCloudCoordinator) -- DataUpdateCoordinator.__init__
itself does no real hass/event-loop interaction beyond storing
references and building a Debouncer, so a MagicMock() hass is safe here,
and this way async_set_updated_data()/async_set_update_error() (which
touch several DataUpdateCoordinator internals) work against a properly
initialized instance instead of a partially-stubbed one.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.prime_coordinator import PrimeCoordinator
from roombapy_prime import ShadowConnectionError, ShadowError, ShadowSSLError
from roombapy_prime.mqtt_client import ShadowResponse


def _make_coordinator() -> tuple[PrimeCoordinator, MagicMock, MagicMock]:
    hass = MagicMock()
    config_entry = MagicMock()
    # async_create_background_task() receives the coroutine as an argument
    # but (being a MagicMock) never awaits/schedules it -- close it
    # explicitly here so tests that don't care about its actual execution
    # (test_connects_and_starts_background_task below) don't leak an
    # "coroutine was never awaited" warning.
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    prime_robot = MagicMock()
    prime_robot.connect = AsyncMock()
    coordinator = PrimeCoordinator(
        hass, config_entry, blid="BLID123", prime_robot=prime_robot,
    )
    return coordinator, config_entry, prime_robot


class TestAsyncStart:
    """async_start(): connects, then starts the background
    watch_mission_timeline() consumer. IMPORTANT (see the method's own
    docstring): login already happened earlier via
    PrimeFactory.create_prime_robot() -- connect() here only opens the
    MQTT connection with an already-valid token, so only connection-level
    failures (Shadow*Error) are possible at this step, never a
    credentials problem."""

    @pytest.mark.asyncio
    async def test_connects_and_starts_background_task(self) -> None:
        coordinator, config_entry, prime_robot = _make_coordinator()

        await coordinator.async_start()

        prime_robot.connect.assert_awaited_once()
        config_entry.async_create_background_task.assert_called_once()
        call = config_entry.async_create_background_task.call_args
        assert call.args[0] is coordinator.hass
        assert "BLID123" in call.kwargs.get("name", "")

    @pytest.mark.asyncio
    async def test_translates_ssl_error_to_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowSSLError("cert problem"))

        with pytest.raises(ConfigEntryNotReady, match="BLID123"):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_translates_connection_error_to_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowConnectionError("dns failure"))

        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_translates_generic_shadow_error_to_not_ready(self) -> None:
        """Plain ShadowError (e.g. "connect timed out") must also map to
        ConfigEntryNotReady, not propagate as a raw library exception --
        it's the base class, not just its two typed subclasses."""
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowError("connect timed out after 10.0s"))

        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_unrelated_exception_is_not_swallowed(self) -> None:
        """A genuinely unexpected error type (not part of the Shadow*Error
        family) must propagate as-is -- this method only translates the
        specific, known connection-failure categories."""
        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=RuntimeError("something else entirely"))

        with pytest.raises(RuntimeError, match="something else entirely"):
            await coordinator.async_start()


class TestAsyncWatchMissionTimeline:
    """_async_watch_mission_timeline(): consumes watch_mission_timeline(),
    forwards every delta (parsed into a MissionTimelineReport) via
    async_set_updated_data(). watch_mission_timeline() itself
    (roombapy-prime v0.1.11a3+) reconnects transparently across drops
    with its own backoff -- this loop does not need its own retry logic
    on top.

    UPDATE (this session): switched from watch_state() after a live
    capture (chairstacker) proved the shadow delta channel never
    carries mission status at all -- see the module docstring."""

    @pytest.mark.asyncio
    async def test_forwards_deltas_via_async_set_updated_data(self) -> None:
        coordinator, _config_entry, prime_robot = _make_coordinator()

        async def fake_watch_mission_timeline():
            yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "start", "ts": 1}]})
            yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "room", "ts": 2, "room": {"rid": "11"}}]})

        prime_robot.watch_mission_timeline = MagicMock(return_value=fake_watch_mission_timeline())

        await coordinator._async_watch_mission_timeline()

        from roombapy_prime.models import MissionTimelineReport

        assert isinstance(coordinator.data, MissionTimelineReport)
        assert coordinator.data.mission_id == "m1"
        assert coordinator.data.event[0].event_type == "room"
        assert coordinator.last_update_success is True

    @pytest.mark.asyncio
    async def test_unexpected_exception_calls_set_update_error(self) -> None:
        """watch_mission_timeline() is designed to never raise in normal
        operation (it retries forever internally) -- if it somehow still
        does, that must surface via async_set_update_error(), not
        disappear as silently-stale data."""
        coordinator, _config_entry, prime_robot = _make_coordinator()

        async def fake_watch_mission_timeline():
            if False:
                yield  # pragma: no cover -- makes this an async generator
            raise RuntimeError("watch_mission_timeline died unexpectedly")

        prime_robot.watch_mission_timeline = MagicMock(return_value=fake_watch_mission_timeline())

        await coordinator._async_watch_mission_timeline()

        assert coordinator.last_update_success is False
        assert isinstance(coordinator.last_exception, RuntimeError)
