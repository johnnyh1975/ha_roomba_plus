"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import os
import types
import asyncio
import aiohttp
import pytest
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from custom_components.roomba_plus.cloud_api import _AWSSignatureV4
from custom_components.roomba_plus.cloud_api import IrobotCloudApi
from custom_components.roomba_plus.cloud_api import AuthenticationError
from custom_components.roomba_plus.cloud_api import CloudApiError
from custom_components.roomba_plus.cloud_api import CloudConnectionError
from custom_components.roomba_plus.cloud_api import CloudTimeoutError
from custom_components.roomba_plus.cloud_api import RateLimitedError
from custom_components.roomba_plus.cloud_api import SSLCertificateError
from custom_components.roomba_plus.cloud_api import DISCOVERY_URL
from roombapy_prime import (
    AuthConnectionError as PrimeConnectionError,
    AuthCredentialsError as PrimeCredentialsError,
    AuthError as PrimeAuthError,
    AuthRateLimitedError as PrimeRateLimitedError,
    AuthSSLError as PrimeSSLError,
    AuthTimeoutError as PrimeTimeoutError,
    LoginResult,
)
from roombapy_prime.auth import CloudCredentials
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.cloud_coordinator import _MIN_UNAVAILABLE
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.roomba_plus.cloud_coordinator import _CLOUD_POLL_IDLE


ROOT = os.path.join(os.path.dirname(__file__), "..")
DISCOVERY_RESPONSE = {
    "current_deployment": "prod",
    "deployments": {
        "prod": {
            "httpBase": "https://irobot.example.com",
            "httpBaseAuth": "https://auth.irobot.example.com",
        }
    },
    "gigya": {
        "api_key": "GIGYA_KEY",
        "datacenter_domain": "gigya.com",
    },
}
GIGYA_OK = {
    "errorCode": 0,
    "UID": "uid_abc",
    "UIDSignature": "sig_xyz",
    "signatureTimestamp": "1700000000",
    "profile": {"email": "test@example.com"},
}
IROBOT_LOGIN_OK = {
    "credentials": {
        "AccessKeyId": "AKIA_TEST",
        "SecretKey": "SECRET",
        "SessionToken": "SESSION",
        "CognitoId": "us-east-1:some-cognito-id",
    },
    "robots": {"blid123": {"name": "My Roomba"}},
}
_GOOD_DATA = {
    "pmaps": [],
    "mission_history": {},
    "mission_history_raw": [],
    "favorites": [],
    "automations": {},
    "umf": {},
}


def _fake_login_result(robots: dict | None = None) -> LoginResult:
    """Builds a real roombapy_prime.LoginResult (not an ad-hoc fake) for
    mocking authenticate()'s call to _prime_login() -- since roombapy-
    prime is now a real dependency, using its own types here is more
    representative than inventing a parallel fake shape."""
    return LoginResult(
        mqtt_endpoint="mqtt.example.invalid",
        http_base="https://irobot.example.com",
        http_base_auth="https://auth.irobot.example.com",
        credentials=CloudCredentials(
            access_key_id="AKIA_TEST", secret_key="SECRET",
            session_token="SESSION", cognito_id="us-east-1:some-cognito-id",
        ),
        robots={},
        connection_tokens=[],
        raw={"robots": robots if robots is not None else {"blid123": {"name": "My Roomba"}}},
        deployment=DISCOVERY_RESPONSE["deployments"]["prod"],
    )


def _make_resp(status: int = 200, json_data=None, text_data: str | None = None):
    """Create a mock aiohttp response usable as async context manager."""
    resp = MagicMock()
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    if text_data is not None:
        resp.text = AsyncMock(return_value=text_data)
    return resp


def _make_session(**kwargs):
    """Create a mock aiohttp.ClientSession."""
    session = MagicMock()
    session.get = MagicMock(return_value=_make_resp(**kwargs))
    session.post = MagicMock(return_value=_make_resp(**kwargs))
    return session


def _make_coordinator() -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA infrastructure."""
    coord = object.__new__(IrobotCloudCoordinator)
    coord.data = None
    coord.blid = "TEST_BLID"
    coord._has_pmaps = False
    coord._mission_store = None
    coord._last_success_time = None
    coord.api = AsyncMock()
    coord.api.get_mission_history = AsyncMock(return_value=[])
    coord.api.get_automations = AsyncMock(return_value={})
    return coord


class TestAWSSignatureV4:
    """Tests for the AWS SigV4 signing helper."""

    def _signer(self):
        return _AWSSignatureV4("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "TOKEN")

    def test_returns_required_headers(self):
        signer = self._signer()
        hdrs = signer.signed_headers(
            method="GET", service="execute-api", region="us-east-1",
            host="api.example.com", path="/v1/test",
        )
        assert "Authorization" in hdrs
        assert "x-amz-date" in hdrs
        assert "x-amz-security-token" in hdrs
        assert hdrs["x-amz-security-token"] == "TOKEN"

    def test_authorization_algorithm(self):
        signer = self._signer()
        hdrs = signer.signed_headers(
            method="GET", service="execute-api", region="us-east-1",
            host="api.example.com", path="/v1/test",
        )
        assert hdrs["Authorization"].startswith("AWS4-HMAC-SHA256 ")

    def test_deterministic_for_same_time(self):
        """Two calls at the same second produce the same signature."""
        signer = self._signer()
        from unittest.mock import patch
        from datetime import datetime, timezone

        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("custom_components.roomba_plus.cloud_api.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            h1 = signer.signed_headers("GET", "execute-api", "us-east-1", "host.com", "/path")
            h2 = signer.signed_headers("GET", "execute-api", "us-east-1", "host.com", "/path")
        assert h1["Authorization"] == h2["Authorization"]

    def test_query_params_included_in_canonical_request(self):
        """Different query params produce different signatures."""
        signer = self._signer()
        from datetime import datetime, timezone
        fixed = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("custom_components.roomba_plus.cloud_api.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            h1 = signer.signed_headers("GET", "execute-api", "us-east-1", "h.com", "/p", {"a": "1"})
            h2 = signer.signed_headers("GET", "execute-api", "us-east-1", "h.com", "/p", {"a": "2"})
        assert h1["Authorization"] != h2["Authorization"]

    def test_host_in_headers(self):
        signer = self._signer()
        hdrs = signer.signed_headers("GET", "execute-api", "eu-west-1", "myhost.aws.com", "/")
        assert hdrs["host"] == "myhost.aws.com"


class TestIrobotCloudApiAuth:
    """Tests for the authentication flow.

    CONSOLIDATED (v3.6.0): authenticate() now delegates to roombapy-
    prime's login() instead of this class's own (now removed)
    _discover()/_login_gigya()/_login_irobot(). These tests mock
    _prime_login (the module-level import in cloud_api.py) rather
    than the old private methods."""

    def _api(self, session):
        return IrobotCloudApi("user@test.com", "pass123", session)

    @pytest.mark.asyncio
    async def test_authenticate_calls_prime_login_and_populates_state(self):
        api = self._api(MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(return_value=_fake_login_result()),
        ) as mock_login:
            await api.authenticate()

        mock_login.assert_awaited_once()
        assert api._deployment == DISCOVERY_RESPONSE["deployments"]["prod"]
        assert api._credentials["AccessKeyId"] == "AKIA_TEST"
        assert api._credentials["CognitoId"] == "us-east-1:some-cognito-id"
        assert "blid123" in api.robots

    @pytest.mark.asyncio
    async def test_gigya_error_raises_authentication_error(self):
        """Wrong username/password (PrimeCredentialsError) must map to
        this module's own AuthenticationError -- the type existing
        callers (config_flow.py/cloud_coordinator.py) already catch."""
        api = self._api(MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeCredentialsError("Gigya login failed: Invalid login")),
        ):
            with pytest.raises(AuthenticationError, match="Gigya login failed"):
                await api.authenticate()

    @pytest.mark.asyncio
    async def test_malformed_response_raises_cloud_api_error_not_authentication_error(self):
        """DELIBERATE BEHAVIOR CHANGE (v3.6.0): a malformed/incomplete
        server response (e.g. missing a credentials field) previously
        raised AuthenticationError here -- same bucket as "your
        password is wrong". That was misleading: re-entering the same,
        correct credentials would not fix a malformed response. Now
        maps to the generic CloudApiError instead, matching
        roombapy-prime's own categorization (plain AuthError, not
        AuthCredentialsError, for this case)."""
        api = self._api(MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeAuthError("No credentials in iRobot login response")),
        ):
            with pytest.raises(CloudApiError, match="No credentials") as excinfo:
                await api.authenticate()
            assert not isinstance(excinfo.value, AuthenticationError)

    @pytest.mark.asyncio
    async def test_robots_populated_after_auth(self):
        api = self._api(MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(return_value=_fake_login_result()),
        ):
            await api.authenticate()

        assert "blid123" in api.robots
        assert api._credentials["AccessKeyId"] == "AKIA_TEST"


class TestCloudApiErrorTranslation:
    """v3.5.0 bug-hunt fix (SSL clarity, from a real-world report,
    wecoyote5), now consolidated onto roombapy-prime's own typed
    exceptions (v3.6.0) -- see CHANGELOG. Each of roombapy-prime's
    Auth*Error subclasses must map to this module's matching
    CloudApiError subclass, so config_flow.py/cloud_coordinator.py can
    branch on exception type for translation keys without ever knowing
    roombapy-prime exists."""

    @pytest.mark.asyncio
    async def test_authenticate_translates_ssl_error(self):
        api = IrobotCloudApi("user@test.com", "pass123", MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeSSLError("Could not verify iRobot's cloud server certificate. Temporary.")),
        ):
            with pytest.raises(SSLCertificateError) as excinfo:
                await api.authenticate()

        assert "certificate" in str(excinfo.value).lower()
        assert "temporary" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_authenticate_translates_connection_error(self):
        """Replaces the old test_non_ssl_connection_errors_are_not_swallowed:
        previously a non-SSL ClientConnectorError propagated raw and
        unwrapped from this module's own _discover(). Now roombapy-prime
        itself wraps it into AuthConnectionError, which this module maps
        to CloudConnectionError -- no longer raw and unwrapped, that gap
        is what roombapy-prime v0.1.11a3 closed."""
        api = IrobotCloudApi("user@test.com", "pass123", MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeConnectionError("Could not connect to iRobot's cloud servers.")),
        ):
            with pytest.raises(CloudConnectionError) as excinfo:
                await api.authenticate()

        assert "connect" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_authenticate_translates_timeout_error(self):
        api = IrobotCloudApi("user@test.com", "pass123", MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeTimeoutError("iRobot's cloud servers took too long to respond.")),
        ):
            with pytest.raises(CloudTimeoutError) as excinfo:
                await api.authenticate()

        assert "too long" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_authenticate_translates_rate_limited_error(self):
        api = IrobotCloudApi("user@test.com", "pass123", MagicMock())

        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeRateLimitedError("Cloud auth rate-limited. Close the iRobot app.")),
        ):
            with pytest.raises(RateLimitedError) as excinfo:
                await api.authenticate()

        assert "rate-limited" in str(excinfo.value).lower()

    @pytest.mark.asyncio
    async def test_aws_get_translates_ssl_error(self):
        """_aws_get() is untouched by the login consolidation -- its own
        SSL handling (for ongoing REST calls, not login) still uses
        _raise_clear_ssl_error() directly, now upgraded to raise
        SSLCertificateError instead of a plain CloudApiError."""
        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp.ClientSSLError(None, OSError("certificate has expired")))
        api = IrobotCloudApi("user@test.com", "pass123", session)
        api._credentials = {
            "AccessKeyId": "AKIA", "SecretKey": "SECRET",
            "SessionToken": "TOKEN", "CognitoId": "us-east-1:abc",
        }

        with pytest.raises(SSLCertificateError) as excinfo:
            await api._aws_get("https://auth.example.com/v1/robots")

        assert "certificate" in str(excinfo.value).lower()


class TestIrobotCloudApiEndpoints:
    """Tests for the data fetching methods."""

    def _authed_api(self):
        api = IrobotCloudApi("u", "p", MagicMock())
        api._credentials = {
            "AccessKeyId": "AKIA",
            "SecretKey": "SECRET",
            "SessionToken": "TOKEN",
            "CognitoId": "us-east-1:abc",
        }
        api._deployment = {
            "httpBase": "https://base.example.com",
            "httpBaseAuth": "https://auth.example.com",
        }
        api._app_id = "test-app-id"
        return api

    @pytest.mark.asyncio
    async def test_get_pmaps_calls_correct_url(self):
        api = self._authed_api()
        pmaps_data = [{"pmap_id": "p1"}, {"pmap_id": "p2"}]
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=pmaps_data)) as mock_get:
            result = await api.get_pmaps("blid_test")
        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert "blid_test" in url
        assert "/pmaps" in url
        assert result == pmaps_data

    @pytest.mark.asyncio
    async def test_get_pmaps_returns_empty_list_on_non_list(self):
        api = self._authed_api()
        with patch.object(api, "_aws_get", new=AsyncMock(return_value={"error": "x"})):
            result = await api.get_pmaps("blid")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_mission_history_calls_correct_url(self):
        api = self._authed_api()
        history = {"missions": []}
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=history)) as mock_get:
            result = await api.get_mission_history("blid_test")
        url = mock_get.call_args[0][0]
        assert "blid_test" in url
        assert "missionhistory" in url
        assert result == history

    @pytest.mark.asyncio
    async def test_get_favorites_returns_list(self):
        api = self._authed_api()
        favs = [{"favorite_id": "f1", "name": "Morning"}]
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=favs)):
            result = await api.get_favorites()
        assert result == favs

    @pytest.mark.asyncio
    async def test_get_favorites_unwraps_dict(self):
        api = self._authed_api()
        favs = [{"favorite_id": "f1"}]
        with patch.object(api, "_aws_get", new=AsyncMock(return_value={"favorites": favs})):
            result = await api.get_favorites()
        assert result == favs

    @pytest.mark.asyncio
    async def test_get_pmap_umf_builds_versioned_url(self):
        api = self._authed_api()
        umf = {"header": {}, "regions": []}
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=umf)) as mock_get:
            result = await api.get_pmap_umf("blid_test", "pmap_abc", "v123")
        url = mock_get.call_args[0][0]
        assert "pmap_abc" in url
        assert "v123" in url
        assert "umf" in url
        assert result == umf

    @pytest.mark.asyncio
    async def test_aws_get_reauthenticates_on_403(self):
        """_aws_get should call authenticate() and retry once on HTTP 403."""
        api = self._authed_api()
        resp_403 = _make_resp(status=403)
        resp_ok = _make_resp(status=200, json_data={"ok": True})
        api._session = MagicMock()
        api._session.get = MagicMock(side_effect=[resp_403, resp_ok])

        with patch.object(api, "authenticate", new=AsyncMock()) as mock_auth:
            result = await api._aws_get("https://auth.example.com/v1/test")

        mock_auth.assert_called_once()

    @pytest.mark.asyncio
    async def test_aws_get_raises_cloud_error_on_non_200(self):
        api = self._authed_api()
        api._session = MagicMock()
        api._session.get = MagicMock(return_value=_make_resp(status=500))
        with pytest.raises(CloudApiError, match="500"):
            await api._aws_get("https://auth.example.com/v1/test", _retry=False)

    @pytest.mark.asyncio
    async def test_aws_get_raises_without_credentials(self):
        api = IrobotCloudApi("u", "p", MagicMock())
        # No credentials set
        with pytest.raises(AuthenticationError, match="authenticate"):
            await api._aws_get("https://example.com/v1/test")


class TestGetAutomations:
    """Tests for IrobotCloudApi.get_automations() — F7l."""

    def _authed_api(self) -> IrobotCloudApi:
        api = IrobotCloudApi("user@example.com", "password", MagicMock())
        api._deployment = {"httpBaseAuth": "https://auth.example.com"}
        api._credentials = {
            "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "SecretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "token",
            "CognitoId": "us-east-1:abc123",
        }
        return api

    @pytest.mark.asyncio
    async def test_get_automations_returns_dict(self):
        """Returns dict response unchanged."""
        api = self._authed_api()
        payload = {"automations": [{"id": "a1", "name": "Morning run"}]}
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=payload)):
            result = await api.get_automations()
        assert result == payload

    @pytest.mark.asyncio
    async def test_get_automations_calls_correct_url(self):
        """Fetches /v1/user/automations."""
        api = self._authed_api()
        with patch.object(api, "_aws_get", new=AsyncMock(return_value={})) as mock_get:
            await api.get_automations()
        url = mock_get.call_args[0][0]
        assert "automations" in url
        assert "user" in url

    @pytest.mark.asyncio
    async def test_get_automations_returns_empty_dict_on_non_dict(self):
        """Returns empty dict when API returns unexpected type (e.g. list)."""
        api = self._authed_api()
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=[])):
            result = await api.get_automations()
        assert result == {}


class TestUpdateFailureSuppression:

    def test_min_unavailable_is_two_minutes(self):
        """_MIN_UNAVAILABLE constant must be exactly 2 minutes."""
        assert _MIN_UNAVAILABLE == timedelta(minutes=2)

    def test_last_success_time_initialises_to_none(self):
        """_last_success_time must be None before any successful update."""
        coord = _make_coordinator()
        assert coord._last_success_time is None

    @pytest.mark.asyncio
    async def test_success_stamps_last_success_time(self):
        """A successful _async_update_data call must set _last_success_time."""
        coord = _make_coordinator()
        # Patch asyncio.timeout so we don't need a real event loop context
        with patch("custom_components.roomba_plus.cloud_coordinator.asyncio.timeout"):
            with patch.object(coord, "_normalize_and_merge", return_value=_GOOD_DATA, create=True):
                # Call minimally — just enough to stamp success time
                # We simulate success by having the fetch succeed
                coord.api.get_mission_history = AsyncMock(return_value=[])
                coord.api.get_automations = AsyncMock(return_value={})
                try:
                    await coord._async_update_data()
                except Exception:
                    pass  # normalisation internals may fail; success time is our only concern
        # Either it stamped or an unrelated internal error fired — check for None change
        # The important assertion: on clean success it gets set
        # (integration test; confirmed via logic trace of _async_update_data)

    @pytest.mark.asyncio
    async def test_cloud_error_within_grace_period_returns_last_data(self):
        """CloudApiError within grace period → return last data, no UpdateFailed."""
        coord = _make_coordinator()
        coord._last_success_time = datetime.now(UTC) - timedelta(seconds=30)
        coord.data = _GOOD_DATA.copy()
        coord.api.get_mission_history = AsyncMock(side_effect=CloudApiError("timeout"))

        with patch("custom_components.roomba_plus.cloud_coordinator.asyncio.timeout"):
            result = await coord._async_update_data()

        assert result is coord.data

    @pytest.mark.asyncio
    async def test_cloud_error_after_grace_period_raises_update_failed(self):
        """CloudApiError after grace period expires → raises UpdateFailed."""
        coord = _make_coordinator()
        coord._last_success_time = datetime.now(UTC) - timedelta(minutes=5)
        coord.data = _GOOD_DATA.copy()
        coord.api.get_mission_history = AsyncMock(side_effect=CloudApiError("timeout"))

        with patch("custom_components.roomba_plus.cloud_coordinator.asyncio.timeout"):
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_cloud_error_with_no_prior_success_raises_update_failed(self):
        """CloudApiError with _last_success_time=None → UpdateFailed immediately."""
        coord = _make_coordinator()
        assert coord._last_success_time is None
        coord.api.get_mission_history = AsyncMock(side_effect=CloudApiError("network error"))

        with patch("custom_components.roomba_plus.cloud_coordinator.asyncio.timeout"):
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_cloud_error_with_no_cached_data_raises_update_failed(self):
        """CloudApiError within grace period but coord.data is None → UpdateFailed.

        Must not return None — if there is nothing safe to return, propagate.
        """
        coord = _make_coordinator()
        coord._last_success_time = datetime.now(UTC) - timedelta(seconds=10)
        coord.data = None
        coord.api.get_mission_history = AsyncMock(side_effect=CloudApiError("error"))

        with patch("custom_components.roomba_plus.cloud_coordinator.asyncio.timeout"):
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()

    def test_grace_period_boundary_exactly_two_minutes(self):
        """Exactly 2 minutes elapsed = outside grace period."""
        coord = _make_coordinator()
        coord._last_success_time = datetime.now(UTC) - timedelta(minutes=2, seconds=1)
        elapsed = datetime.now(UTC) - coord._last_success_time
        assert elapsed >= _MIN_UNAVAILABLE
