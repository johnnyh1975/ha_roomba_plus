"""Tests for the V4/Prime setup/unload path in __init__.py:
_connection_type(), _async_setup_entry_prime(), and the CLOUD_ONLY
branch in async_unload_entry().

NEW (V4/Prime implementation). No existing test file covers
async_setup_entry()/async_unload_entry() or the phase functions
directly anywhere in this suite (confirmed before writing this file) --
those are tested indirectly, through the platform tests that construct
RoombaData by hand. This file establishes its own pattern for the new
CLOUD_ONLY path specifically, since it's a genuinely separate code path
from the existing 4-phase pipeline.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import (
    _async_setup_entry_prime,
    _connection_type,
    async_unload_entry,
)
from custom_components.roomba_plus.const import (
    CONF_BLID,
    CONF_CONNECTION_TYPE,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
)
from custom_components.roomba_plus.models import ConnectionType, RoombaData
from roombapy_prime import (
    AuthConnectionError,
    AuthCredentialsError,
    AuthRateLimitedError,
)


def _make_hass_and_entry() -> tuple[MagicMock, MagicMock]:
    hass = MagicMock()
    hass.config.country = "US"
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    config_entry = MagicMock()
    config_entry.data = {
        CONF_CONNECTION_TYPE: ConnectionType.CLOUD_ONLY.value,
        CONF_BLID: "BLID123",
        CONF_IROBOT_USERNAME: "user@example.com",
        CONF_IROBOT_PASSWORD: "hunter2",
    }
    # Mirrors _make_coordinator()'s pattern in test_prime_coordinator.py --
    # avoids leaking an "coroutine was never awaited" warning for the
    # background task PrimeCoordinator.async_start() schedules.
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    return hass, config_entry


@pytest.fixture(autouse=True)
def _mock_clientsession():
    """async_get_clientsession(hass) with a plain MagicMock() hass falls
    through to creating a REAL aiohttp.ClientSession() (HA's real
    implementation checks hass.data, which a bare MagicMock doesn't
    behave like a dict for) -- leaks an unclosed-session warning/error
    in every test here, none of which make real network calls anyway
    (PrimeFactory.create_prime_robot is always mocked). Patched for
    every test in this file rather than per-test."""
    with patch(
        "custom_components.roomba_plus.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


class TestConnectionType:
    def test_defaults_to_local_push_when_absent(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {}
        assert _connection_type(config_entry) is ConnectionType.LOCAL_PUSH

    def test_reads_cloud_only_from_data(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {CONF_CONNECTION_TYPE: "cloud_only"}
        assert _connection_type(config_entry) is ConnectionType.CLOUD_ONLY

    def test_reads_local_push_explicitly(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {CONF_CONNECTION_TYPE: "local_push"}
        assert _connection_type(config_entry) is ConnectionType.LOCAL_PUSH


class TestAsyncSetupEntryPrime:
    """v4.0.0a0 MVP scope: login, MQTT connect, PrimeCoordinator running.
    Deliberately forwards NO platforms yet -- see the function's own
    docstring for why (vacuum.py would crash on a roomba=None entry)."""

    @pytest.mark.asyncio
    async def test_success_path_sets_runtime_data(self) -> None:
        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ) as mock_create:
            result = await _async_setup_entry_prime(hass, config_entry)

        assert result is True
        mock_create.assert_awaited_once()
        call = mock_create.call_args
        assert call.args[1] == "user@example.com"
        assert call.args[2] == "hunter2"
        assert call.args[3] == "US"
        assert call.kwargs["blid"] == "BLID123"
        assert call.kwargs["auto_refresh"] is True

        runtime_data: RoombaData = config_entry.runtime_data
        assert runtime_data.blid == "BLID123"
        assert runtime_data.roomba is None
        assert runtime_data.connection_type is ConnectionType.CLOUD_ONLY
        assert runtime_data.prime_robot is fake_prime_robot
        assert runtime_data.prime_coordinator is not None
        fake_prime_robot.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_credentials_error_raises_config_entry_auth_failed(self) -> None:
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthCredentialsError("wrong password")),
        ):
            with pytest.raises(ConfigEntryAuthFailed, match="BLID123"):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_rate_limited_error_raises_config_entry_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthRateLimitedError("close the app")),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_connection_error_raises_config_entry_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthConnectionError("dns failure")),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_mqtt_connect_failure_raises_config_entry_not_ready(self) -> None:
        """Login succeeds, but PrimeCoordinator.async_start()'s own MQTT
        connect() fails -- must still map to ConfigEntryNotReady, via
        PrimeCoordinator's own translation, not this function's."""
        from homeassistant.exceptions import ConfigEntryNotReady
        from roombapy_prime import ShadowConnectionError

        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock(side_effect=ShadowConnectionError("mqtt unreachable"))

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)


class TestAsyncUnloadEntryCloudOnly:
    @pytest.mark.asyncio
    async def test_disconnects_prime_robot_and_forwards_no_platforms(self) -> None:
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        config_entry = MagicMock()
        fake_prime_robot = MagicMock()
        fake_prime_robot.disconnect = AsyncMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123",
            roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY,
            prime_robot=fake_prime_robot,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is True
        fake_prime_robot.disconnect.assert_awaited_once()
        hass.config_entries.async_unload_platforms.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_missing_prime_robot_gracefully(self) -> None:
        """Defensive: if setup failed before prime_robot was ever set
        (shouldn't happen given _async_setup_entry_prime()'s ordering,
        but this guards against a future refactor introducing that
        gap), unload must not crash."""
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        config_entry = MagicMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123", roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY, prime_robot=None,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_does_not_disconnect_when_platform_unload_fails(self) -> None:
        """Mirrors the classic path's own convention (see the LOCAL_PUSH
        branch just below this one): only disconnect/cleanup if platform
        unloading actually succeeded."""
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        config_entry = MagicMock()
        fake_prime_robot = MagicMock()
        fake_prime_robot.disconnect = AsyncMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123", roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY, prime_robot=fake_prime_robot,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is False
        fake_prime_robot.disconnect.assert_not_called()
