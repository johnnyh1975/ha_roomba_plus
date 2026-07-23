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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.prime_coordinator import (
    PrimeCoordinator,
    PrimeStatusCoordinator,
    _deep_merge_reported,
)
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
            # This generator is meant to run forever in real operation --
            # ending on its own is itself anomalous and now triggers the
            # coordinator's own outer retry loop. Terminate the test
            # cleanly via cancellation rather than letting it retry forever.
            raise asyncio.CancelledError

        prime_robot.watch_mission_timeline = MagicMock(return_value=fake_watch_mission_timeline())

        with pytest.raises(asyncio.CancelledError):
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

        with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_mission_timeline()

        assert coordinator.last_update_success is False
        assert isinstance(coordinator.last_exception, RuntimeError)

    @pytest.mark.asyncio
    async def test_retries_after_unexpected_exception_instead_of_dying_permanently(self) -> None:
        """CONFIRMED NECESSARY (real field report, chairstacker): the
        vacuum entity's own activity got stuck on "Cleaning" long after
        the robot had actually finished and docked -- this coordinator
        previously had no recovery path if anything unexpected
        interrupted its own watch task, exactly matching that report.
        Now retries instead of dying permanently for the rest of the
        session."""
        coordinator, _config_entry, prime_robot = _make_coordinator()
        call_count = 0

        def fake_watch_mission_timeline():
            nonlocal call_count
            call_count += 1

            async def _gen():
                if call_count == 1:
                    raise RuntimeError("simulated unexpected failure")
                    yield  # pragma: no cover -- unreachable, makes this an async generator
                yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "start", "ts": 1}]})
                raise asyncio.CancelledError

            return _gen()

        prime_robot.watch_mission_timeline = fake_watch_mission_timeline

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_mission_timeline()

        assert call_count == 2
        assert coordinator.data.mission_id == "m1"


def _make_status_coordinator() -> tuple[PrimeStatusCoordinator, MagicMock, MagicMock]:
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    prime_robot = MagicMock()
    prime_robot.connect = AsyncMock()
    coordinator = PrimeStatusCoordinator(
        hass, config_entry, blid="BLID123", prime_robot=prime_robot,
    )
    return coordinator, config_entry, prime_robot


class TestAsyncWatchStatusUpdatesRetryBehavior:
    """_async_watch_status_updates()'s own outer retry loop -- added
    after a real field report (chairstacker): sensors got stuck at
    stale/Unknown values after some activity, fixed only by a full HA
    restart, not just reloading the integration. roombapy-prime's own
    watch_named_shadows_updates() already retries connection drops
    internally with unbounded retries -- this outer loop is a second,
    coordinator-level safety net for anything else that could end the
    generator unexpectedly, so a single unexpected error degrades to
    "retry after a delay" rather than "permanently dead until Home
    Assistant itself restarts"."""

    @pytest.mark.asyncio
    async def test_retries_after_unexpected_exception_instead_of_dying_permanently(self) -> None:
        coordinator, _config_entry, prime_robot = _make_status_coordinator()
        call_count = 0

        async def fake_watch_named_shadows_updates():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated unexpected failure")
            yield ShadowResponse(
                topic="$aws/things/BLID123/shadow/name/ro-currentstate/update/accepted",
                payload={"state": {"reported": {"batPct": 72}}},
            )
            # Stop the loop after one successful iteration so the test itself terminates --
            # a real run would keep going forever, which is exactly the point being tested.
            raise asyncio.CancelledError

        prime_robot.watch_named_shadows_updates = fake_watch_named_shadows_updates

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_status_updates()

        # The key assertion: the generator was called a SECOND time after the first
        # one raised -- proving this is a real retry, not a permanent give-up.
        assert call_count == 2
        assert coordinator.data == {"ro-currentstate": {"batPct": 72}}


class TestDeepMergeReported:
    """_deep_merge_reported() -- CONFIRMED NECESSARY from a real field
    report with screenshots (chairstacker): starting a mission caused
    detected_pad/dock_status/firmware_version/runtime/pad_dry/pad_wash
    to ALL go "Unknown" simultaneously, while suction_level (a
    different named shadow, rw-settings, which received no update
    during that window) correctly kept its real value the whole time
    -- proving AWS's own "update/accepted" messages only echo back
    the fields that changed, not the shadow's full state, and a plain
    dict replacement was discarding everything else."""

    def test_preserves_existing_fields_not_mentioned_in_the_update(self):
        """Reproduces the exact reported scenario: a full ro-currentstate
        seed, then a mission-start update containing only
        cleanMissionStatus -- every other field must survive."""
        existing = {
            "batPct": 100,
            "detectedPad": "padPlate",
            "dock": {"state": 301, "pwState": 601, "pdState": 701},
            "runtimeStats": {"hr": 24, "min": 0},
        }
        partial_update = {"cleanMissionStatus": {"phase": "reloc"}}

        result = _deep_merge_reported(existing, partial_update)

        assert result["batPct"] == 100
        assert result["detectedPad"] == "padPlate"
        assert result["dock"] == {"state": 301, "pwState": 601, "pdState": 701}
        assert result["runtimeStats"] == {"hr": 24, "min": 0}
        assert result["cleanMissionStatus"] == {"phase": "reloc"}

    def test_merges_nested_dicts_recursively_not_just_top_level(self):
        """A partial update to a NESTED field (e.g. only dock.state
        changing) must not wipe out sibling fields within that same
        nested object (dock.pwState/pdState)."""
        existing = {"dock": {"state": 301, "pwState": 601, "pdState": 701}}
        partial_update = {"dock": {"state": 302}}

        result = _deep_merge_reported(existing, partial_update)

        assert result["dock"] == {"state": 302, "pwState": 601, "pdState": 701}

    def test_does_not_mutate_the_original_existing_dict(self):
        existing = {"batPct": 100}

        _deep_merge_reported(existing, {"detectedPad": "padPlate"})

        assert existing == {"batPct": 100}


class TestAsyncWatchStatusUpdatesMergeBehavior:
    @pytest.mark.asyncio
    async def test_partial_update_preserves_previously_seeded_fields(self) -> None:
        """End-to-end version of the same bug, through the actual
        coordinator method: seed with a full ro-currentstate payload,
        then feed a partial update -- the previously-seeded fields
        must survive in coordinator.data afterward."""
        coordinator, _config_entry, prime_robot = _make_status_coordinator()
        coordinator.async_set_updated_data({
            "ro-currentstate": {"batPct": 100, "detectedPad": "padPlate"},
            "rw-settings": {"suctionLevel": 3},
        })

        async def fake_watch():
            yield ShadowResponse(
                topic="$aws/things/BLID123/shadow/name/ro-currentstate/update/accepted",
                payload={"state": {"reported": {"cleanMissionStatus": {"phase": "reloc"}}}},
            )
            raise asyncio.CancelledError

        prime_robot.watch_named_shadows_updates = fake_watch

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_watch_status_updates()

        assert coordinator.data["ro-currentstate"]["batPct"] == 100
        assert coordinator.data["ro-currentstate"]["detectedPad"] == "padPlate"
        assert coordinator.data["ro-currentstate"]["cleanMissionStatus"] == {"phase": "reloc"}
        # the untouched shadow must survive completely unaffected.
        assert coordinator.data["rw-settings"] == {"suctionLevel": 3}
