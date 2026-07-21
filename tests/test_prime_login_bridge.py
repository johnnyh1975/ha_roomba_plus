"""Tests for _prime_login_bridge.py -- the short-lived, single-use
bridge that lets async_setup_entry_prime() reuse config_flow's
validation login instead of running a fully redundant second one.

Deliberately has NO Home Assistant test dependencies (unlike most of
this test suite) -- the module itself only imports `time` and
roombapy_prime.LoginResult, so this file doesn't hit the known
sandbox collection gaps documented in test_config_flow.py's own
docstring (missing homeassistant.helpers.service_info.dhcp/zeroconf).
"""
from __future__ import annotations

from roombapy_prime import LoginResult
from roombapy_prime.auth import CloudCredentials

from custom_components.roomba_plus import _prime_login_bridge


def _fake_login_result() -> LoginResult:
    return LoginResult(
        mqtt_endpoint="mqtt.example.invalid",
        http_base="https://http-base.example.invalid",
        http_base_auth="https://http-base-auth.example.invalid",
        credentials=CloudCredentials(
            access_key_id="ak", secret_key="sk", session_token="st", cognito_id="us-east-1:0",
        ),
        robots={"BLID123": {"sku": "i755640"}},
        connection_tokens=[],
        raw={},
        irbt_topic_prefix="irbt-fake-prefix",
    )


def setup_function() -> None:
    """Each test starts with a clean slate -- this module holds
    process-global state (_pending_logins), same reason
    roombapy-prime's own discovery-cache tests needed an autouse
    clearing fixture."""
    _prime_login_bridge._pending_logins.clear()


def test_pop_returns_none_when_nothing_stored() -> None:
    assert _prime_login_bridge.pop_pending_login("BLID123") is None


def test_stored_login_is_returned_by_pop() -> None:
    result = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID123", result)

    popped = _prime_login_bridge.pop_pending_login("BLID123")

    assert popped is result


def test_pop_is_single_use() -> None:
    """The core safety property this bridge relies on: a second pop for
    the same blid must not return the same (or any) result again, even
    though the first pop happened well within the TTL. A later
    async_setup_entry_prime() call for the same blid (e.g. a reload)
    must always do its own fresh login."""
    result = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID123", result)

    first = _prime_login_bridge.pop_pending_login("BLID123")
    second = _prime_login_bridge.pop_pending_login("BLID123")

    assert first is result
    assert second is None


def test_different_blids_are_independent() -> None:
    result_a = _fake_login_result()
    result_b = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID_A", result_a)
    _prime_login_bridge.store_pending_login("BLID_B", result_b)

    assert _prime_login_bridge.pop_pending_login("BLID_A") is result_a
    assert _prime_login_bridge.pop_pending_login("BLID_B") is result_b


def test_expired_entry_returns_none_but_is_still_removed(monkeypatch) -> None:
    """The TTL is a safety bound, not the primary mechanism (single-use
    already prevents reuse across restarts) -- but an unusually slow
    handoff (well past 60s) should still fall back to a fresh login
    rather than handing back a stale result."""
    fake_now = [1000.0]
    monkeypatch.setattr(_prime_login_bridge.time, "monotonic", lambda: fake_now[0])

    result = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID123", result)

    fake_now[0] += _prime_login_bridge._TTL_SECONDS + 1
    popped = _prime_login_bridge.pop_pending_login("BLID123")

    assert popped is None
    # Also removed, not left behind for a hypothetical later pop to find
    # (single-use applies to expired entries too, not just fresh ones).
    assert "BLID123" not in _prime_login_bridge._pending_logins


def test_entry_within_ttl_is_still_returned(monkeypatch) -> None:
    fake_now = [1000.0]
    monkeypatch.setattr(_prime_login_bridge.time, "monotonic", lambda: fake_now[0])

    result = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID123", result)

    fake_now[0] += _prime_login_bridge._TTL_SECONDS - 1
    popped = _prime_login_bridge.pop_pending_login("BLID123")

    assert popped is result


def test_storing_again_for_same_blid_overwrites_without_error() -> None:
    """Documented, deliberate behavior (see store_pending_login()'s own
    docstring): if a second config flow run somehow raced for the
    exact same robot, the newer result should win, not raise or get
    silently ignored."""
    first_result = _fake_login_result()
    second_result = _fake_login_result()
    _prime_login_bridge.store_pending_login("BLID123", first_result)
    _prime_login_bridge.store_pending_login("BLID123", second_result)

    popped = _prime_login_bridge.pop_pending_login("BLID123")

    assert popped is second_result
