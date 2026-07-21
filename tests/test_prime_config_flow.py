"""Tests for the V4/Prime cloud-account onboarding path in config_flow.py:
async_step_user()'s new third option, async_step_prime_account(),
async_step_prime_robot_picker(), _async_create_prime_entry(),
async_step_prime_classic_ip(), and async_step_prime_classic_analytics().

NEW (V4/Prime implementation). Follows the existing bare-construction
pattern established by _make_reauth_flow() in test_config_flow.py
(object.__new__(RoombaPlusConfigFlow) + just enough FlowHandler
attributes for HA's async_abort/async_show_form/async_create_entry to
work), rather than the full pytest-homeassistant-custom-component flow
harness.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.config_flow import (
    RoombaPlusConfigFlow,
    _CLOUD_ACCOUNT_SENTINEL,
    _is_prime_sku,
)
from custom_components.roomba_plus.const import (
    CONF_BLID,
    CONF_CONNECTION_TYPE,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from custom_components.roomba_plus.models import ConnectionType
from roombapy_prime import AuthConnectionError, AuthCredentialsError, AuthRateLimitedError, AuthSSLError


def _make_flow() -> RoombaPlusConfigFlow:
    """Bare-construct RoombaPlusConfigFlow -- see this module's own
    docstring for why (mirrors _make_reauth_flow() in
    test_config_flow.py)."""
    flow = object.__new__(RoombaPlusConfigFlow)
    flow.hass = MagicMock()
    flow.context = {}
    flow.flow_id = "test_flow_id"
    flow.handler = "roomba_plus"
    flow.name = None
    flow.blid = ""
    flow.host = None
    flow.discovered_robots = {}
    flow._pending_config = {}
    flow._prime_account_username = ""
    flow._prime_account_password = ""
    flow._prime_account_robots = {}
    flow._prime_selected_blid = None
    flow._prime_account_login_result = None
    return flow


class TestIsPrimeSku:
    def test_g_prefix_is_prime(self):
        assert _is_prime_sku("G185020") is True

    def test_lowercase_g_prefix_is_prime(self):
        assert _is_prime_sku("g185020") is True

    def test_i_prefix_is_classic(self):
        assert _is_prime_sku("i755840") is False

    def test_none_is_not_prime(self):
        assert _is_prime_sku(None) is False

    def test_empty_string_is_not_prime(self):
        assert _is_prime_sku("") is False


class TestAsyncStepUserCloudOption:
    """BUG FIX regression coverage: previously, zero discovered local
    devices fell straight through to async_step_manual() -- a dead end
    for a V4/Prime-only owner."""

    @pytest.mark.asyncio
    async def test_shows_cloud_account_option_even_with_no_local_devices(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[]),
        ):
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        schema_keys = list(result["data_schema"].schema.keys())
        # vol.Optional(CONF_HOST) is the only top-level key; the actual
        # choices live in its vol.In(...) validator.
        host_marker = schema_keys[0]
        choices = result["data_schema"].schema[host_marker].container
        assert _CLOUD_ACCOUNT_SENTINEL in choices
        assert None in choices  # "Add manually" still present too

    @pytest.mark.asyncio
    async def test_selecting_cloud_account_routes_to_prime_account(self):
        flow = _make_flow()
        result = await flow.async_step_user({CONF_HOST: _CLOUD_ACCOUNT_SENTINEL})
        assert result["type"] == "form"
        assert result["step_id"] == "prime_account"


class TestAsyncStepPrimeAccount:
    @pytest.mark.asyncio
    async def test_success_stores_credentials_and_routes_to_picker(self):
        flow = _make_flow()
        fake_robots = {"BLID1": {"sku": "G185020", "password": "pw1", "name": "Combo"}}

        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api = mock_api_cls.return_value
            mock_api.authenticate = AsyncMock()
            mock_api.robots = fake_robots
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_prime_account({
                    CONF_IROBOT_USERNAME: "user@example.com",
                    CONF_IROBOT_PASSWORD: "hunter2",
                })

        assert flow._prime_account_username == "user@example.com"
        assert flow._prime_account_password == "hunter2"
        assert flow._prime_account_robots == fake_robots
        assert result["step_id"] == "prime_robot_picker"

    @pytest.mark.asyncio
    async def test_credentials_error_shows_invalid_cloud_credentials(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("AuthenticationError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "wrong",
            })

        assert result["errors"]["base"] == "invalid_cloud_credentials"

    @pytest.mark.asyncio
    async def test_rate_limited_error_shows_cloud_rate_limited(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("RateLimitedError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cloud_rate_limited"

    @pytest.mark.asyncio
    async def test_ssl_error_shows_cloud_ssl_certificate_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("SSLCertificateError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cloud_ssl_certificate_error"

    @pytest.mark.asyncio
    async def test_generic_cloud_api_error_shows_cannot_connect(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("CloudApiError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_blank_submission_shows_error_instead_of_silent_reshow(self):
        """Bug-hunt round: unlike async_step_cloud_credentials (both
        fields genuinely optional there), this step has no valid skip
        path -- a blank submission previously reshowed the form with no
        explanation at all."""
        flow = _make_flow()
        result = await flow.async_step_prime_account({
            CONF_IROBOT_USERNAME: "",
            CONF_IROBOT_PASSWORD: "",
        })
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_cloud_credentials"


def _auth_error(name: str) -> Exception:
    """Builds a real instance of the named cloud_api exception, so the
    except-clause matching in async_step_prime_account() is exercised
    against real types, not a generic stand-in."""
    from custom_components.roomba_plus import cloud_api
    return getattr(cloud_api, name)("boom")


@pytest.fixture(autouse=True)
def _mock_clientsession():
    """async_step_prime_account() imports async_get_clientsession inline
    (from homeassistant.helpers.aiohttp_client), so it can't be patched
    via a custom_components.roomba_plus.config_flow attribute -- patch
    the actual source function instead. With a MagicMock() hass (this
    file's flow.hass), the real implementation falls through to
    creating a genuine aiohttp.ClientSession(), which then leaks a
    lingering-timer failure at test teardown (same root cause fixed for
    test_prime_setup.py earlier this session)."""
    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


class TestAsyncStepPrimeRobotPicker:
    @pytest.mark.asyncio
    async def test_aborts_when_no_new_robots(self):
        flow = _make_flow()
        flow._prime_account_robots = {}
        with patch.object(flow, "_async_current_ids", return_value=set()):
            result = await flow.async_step_prime_robot_picker()
        assert result["type"] == "abort"
        assert result["reason"] == "no_new_robots_found"

    @pytest.mark.asyncio
    async def test_filters_already_configured_blids(self):
        flow = _make_flow()
        flow._prime_account_robots = {
            "BLID1": {"sku": "G185020", "name": "Combo"},
            "BLID2": {"sku": "i755840", "name": "Already there"},
        }
        with patch.object(flow, "_async_current_ids", return_value={"BLID2"}):
            result = await flow.async_step_prime_robot_picker()
        choices = result["data_schema"].schema[CONF_BLID].container
        assert "BLID1" in choices
        assert "BLID2" not in choices

    @pytest.mark.asyncio
    async def test_selecting_prime_robot_creates_entry_directly(self):
        flow = _make_flow()
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"
        flow._prime_account_robots = {"BLID1": {"sku": "G185020", "name": "Combo"}}

        with patch.object(flow, "_async_current_ids", return_value=set()):
            with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    result = await flow.async_step_prime_robot_picker({CONF_BLID: "BLID1"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_CONNECTION_TYPE] == ConnectionType.CLOUD_ONLY.value
        assert result["data"][CONF_BLID] == "BLID1"
        assert result["data"][CONF_IROBOT_USERNAME] == "user@example.com"
        assert result["data"][CONF_IROBOT_PASSWORD] == "hunter2"

    @pytest.mark.asyncio
    async def test_selecting_classic_robot_routes_to_classic_ip(self):
        flow = _make_flow()
        flow._prime_account_robots = {
            "BLID1": {"sku": "i755840", "name": "Bogdana", "password": "pw"},
        }

        with patch.object(flow, "_async_current_ids", return_value=set()):
            with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    with patch(
                        "custom_components.roomba_plus.config_flow._async_discover_roombas",
                        new=AsyncMock(return_value=[]),
                    ):
                        result = await flow.async_step_prime_robot_picker({CONF_BLID: "BLID1"})

        assert result["step_id"] == "prime_classic_ip"
        assert flow._prime_selected_blid == "BLID1"


class TestAsyncStepPrimeClassicIp:
    @pytest.mark.asyncio
    async def test_local_scan_match_skips_form_and_validates(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}
        fake_device = MagicMock()
        fake_device.blid = "BLID1"
        fake_device.ip = "192.168.1.50"

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow._async_discover_roombas",
                    new=AsyncMock(return_value=[fake_device]),
                ):
                    with patch(
                        "custom_components.roomba_plus.config_flow.validate_input",
                        new=AsyncMock(return_value={"name": "Bogdana"}),
                    ) as mock_validate:
                        result = await flow.async_step_prime_classic_ip()

        mock_validate.assert_awaited_once()
        call_config = mock_validate.call_args.args[1]
        assert call_config[CONF_HOST] == "192.168.1.50"
        assert call_config[CONF_PASSWORD] == "pw123"
        assert result["step_id"] == "prime_classic_analytics"

    @pytest.mark.asyncio
    async def test_no_local_match_shows_manual_ip_form(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow._async_discover_roombas",
                    new=AsyncMock(return_value=[]),
                ):
                    result = await flow.async_step_prime_classic_ip()

        assert result["type"] == "form"
        assert result["step_id"] == "prime_classic_ip"

    @pytest.mark.asyncio
    async def test_manual_ip_submission_validates_and_routes_to_analytics(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow.validate_input",
                    new=AsyncMock(return_value={"name": "Bogdana"}),
                ):
                    result = await flow.async_step_prime_classic_ip(
                        {CONF_HOST: "10.0.0.5"}
                    )

        assert result["step_id"] == "prime_classic_analytics"
        assert flow._pending_config[CONF_HOST] == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_validate_input_failure_shows_cannot_connect(self):
        from custom_components.roomba_plus.config_flow import CannotConnect

        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow.validate_input",
                    new=AsyncMock(side_effect=CannotConnect()),
                ):
                    result = await flow.async_step_prime_classic_ip({CONF_HOST: "10.0.0.5"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_missing_password_aborts(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"name": "Bogdana"}}  # no password key

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                result = await flow.async_step_prime_classic_ip()

        assert result["type"] == "abort"
        assert result["reason"] == "prime_classic_password_missing"


class TestAsyncStepPrimeClassicAnalytics:
    @pytest.mark.asyncio
    async def test_checkbox_true_keeps_credentials(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        flow._pending_config = {CONF_HOST: "10.0.0.5", CONF_BLID: "BLID1"}
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"

        result = await flow.async_step_prime_classic_analytics(
            {"enable_cloud_analytics": True}
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_IROBOT_USERNAME] == "user@example.com"
        assert result["data"][CONF_IROBOT_PASSWORD] == "hunter2"

    @pytest.mark.asyncio
    async def test_checkbox_false_discards_credentials(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        flow._pending_config = {CONF_HOST: "10.0.0.5", CONF_BLID: "BLID1"}
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"

        result = await flow.async_step_prime_classic_analytics(
            {"enable_cloud_analytics": False}
        )

        assert result["type"] == "create_entry"
        assert CONF_IROBOT_USERNAME not in result["data"]
        assert CONF_IROBOT_PASSWORD not in result["data"]

    @pytest.mark.asyncio
    async def test_default_shows_form_with_checkbox_default_true(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        result = await flow.async_step_prime_classic_analytics()
        assert result["type"] == "form"
        assert result["step_id"] == "prime_classic_analytics"
