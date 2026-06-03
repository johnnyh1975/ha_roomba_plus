"""Unit tests for cloud_api.py.

Tests the pure-Python helpers in cloud_api.py:
  - _AWSSignatureV4.signed_headers  — correct header set, deterministic sig
  - IrobotCloudApi auth flow        — correct URL/payload construction
  - IrobotCloudApi data endpoints   — correct URL building and param passing
  - Error propagation               — AuthenticationError vs CloudApiError

No real network calls — all aiohttp is mocked via pytest-asyncio + unittest.mock.
No HA or roombapy installation required — uses the stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import types
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


# ── Stub aiohttp for test environment ─────────────────────────────────────────

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


# ── Import under test ──────────────────────────────────────────────────────────

from custom_components.roomba_plus.cloud_api import (
    _AWSSignatureV4,
    IrobotCloudApi,
    AuthenticationError,
    CloudApiError,
    DISCOVERY_URL,
)


# ── _AWSSignatureV4 ────────────────────────────────────────────────────────────

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


# ── IrobotCloudApi — authentication ───────────────────────────────────────────

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


class TestIrobotCloudApiAuth:
    """Tests for the authentication flow."""

    def _api(self, session):
        return IrobotCloudApi("user@test.com", "pass123", session)

    @pytest.mark.asyncio
    async def test_authenticate_calls_discovery(self):
        session = _make_session(json_data=DISCOVERY_RESPONSE)
        session.post = MagicMock(side_effect=[
            _make_resp(json_data=GIGYA_OK, text_data="{}"),
            _make_resp(json_data=IROBOT_LOGIN_OK, text_data="{}"),
        ])
        session.get = MagicMock(side_effect=[
            _make_resp(json_data=DISCOVERY_RESPONSE),
        ])
        api = self._api(session)

        # Patch _login_gigya and _login_irobot to avoid real HTTP in unit test
        api._discovery_done = False
        with patch.object(api, "_login_gigya", new=AsyncMock(return_value=("uid", "sig", "ts"))):
            with patch.object(api, "_login_irobot", new=AsyncMock()):
                await api.authenticate()

        # _discover must have been called
        assert api._deployment  # set by _discover

    @pytest.mark.asyncio
    async def test_gigya_error_raises_authentication_error(self):
        error_resp = {"errorCode": 400, "errorMessage": "Invalid credentials"}
        import json as _json
        session = MagicMock()
        session.get = MagicMock(return_value=_make_resp(json_data=DISCOVERY_RESPONSE))
        session.post = MagicMock(return_value=_make_resp(
            text_data=_json.dumps(error_resp),
            json_data=error_resp,
        ))
        api = self._api(session)
        api._deployment = DISCOVERY_RESPONSE["deployments"]["prod"]
        api._deployment["gigya"] = DISCOVERY_RESPONSE["gigya"]

        import json
        with pytest.raises(AuthenticationError, match="Gigya login failed"):
            await api._login_gigya(DISCOVERY_RESPONSE["gigya"], "KEY")

    @pytest.mark.asyncio
    async def test_irobot_login_missing_credentials_raises(self):
        import json
        bad_resp = {"robots": {}}  # no 'credentials' key
        api = self._api(MagicMock())
        api._deployment = DISCOVERY_RESPONSE["deployments"]["prod"]
        session_post = _make_resp(json_data=bad_resp, text_data=json.dumps(bad_resp))
        api._session = MagicMock()
        api._session.post = MagicMock(return_value=session_post)

        with pytest.raises(AuthenticationError, match="No credentials"):
            await api._login_irobot("uid", "sig", "ts")

    @pytest.mark.asyncio
    async def test_robots_populated_after_auth(self):
        import json
        api = self._api(MagicMock())
        api._deployment = DISCOVERY_RESPONSE["deployments"]["prod"]
        ok_resp = _make_resp(json_data=IROBOT_LOGIN_OK, text_data=json.dumps(IROBOT_LOGIN_OK))
        api._session = MagicMock()
        api._session.post = MagicMock(return_value=ok_resp)

        await api._login_irobot("uid", "sig", "ts")
        assert "blid123" in api.robots
        assert api._credentials["AccessKeyId"] == "AKIA_TEST"


# ── IrobotCloudApi — data endpoints ───────────────────────────────────────────

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
