"""One iRobot cloud login per account (cloud_account.py, 4.3).

Until 4.2 every config entry logged in on its own: three robots on one
account ran the full Gigya + iRobot login three times, within a second
of each other on every restart. These tests hold the registry to what
replaces that -- one login, shared, released by the last entry out, and
a failure answering the others instead of each trying again.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roombapy_prime import AuthCredentialsError, AuthTimeoutError, CloudErrorReason

from custom_components.roomba_plus import cloud_account
from custom_components.roomba_plus.cloud_account import (
    DATA_KEY,
    FAILURE_REUSE_SECONDS,
    OFFER_TTL_SECONDS,
    PRIME_TOKEN_MARGIN_SECONDS,
    async_acquire,
    async_login,
    async_offer,
    async_release,
)

ROBOT_PASSWORD = ":1:1234567890:RobotSecretValue"


def _account(expires_in: float | None = 3600.0) -> MagicMock:
    account = MagicMock(name="CloudAccount")
    account.login_result.raw = {"robots": {
        "BLID1": {"name": "Rosie", "sku": "R980040", "password": ROBOT_PASSWORD},
        "BLID2": {"name": "Mopsy", "sku": "G185020", "password": ROBOT_PASSWORD},
    }}
    token = MagicMock()
    token.seconds_until_expiry.return_value = expires_in
    account.login_result.token_for_blid.return_value = token
    account.relogin = AsyncMock()
    return account


class _Clock:
    """time.monotonic() as the test says."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(cloud_account.time, "monotonic", c)
    return c


@pytest.fixture
def login():
    """CloudAccount.login(), counted. Each call returns a new account."""
    mock = AsyncMock(side_effect=lambda *a, **k: _account())
    with patch.object(cloud_account.CloudAccount, "login", new=mock), \
         patch.object(cloud_account, "async_get_clientsession", return_value=MagicMock()):
        yield mock


@pytest.fixture
def hass_():
    hass = MagicMock()
    hass.data = {}
    hass.config.country = "de"
    return hass


class TestOneLoginPerAccount:

    @pytest.mark.asyncio
    async def test_two_entries_share_one_login(self, hass_, login):
        a = await async_acquire(hass_, "E1", "user@example.com", "pw")
        b = await async_acquire(hass_, "E2", "user@example.com", "pw")
        assert a is b
        assert login.await_count == 1

    @pytest.mark.asyncio
    async def test_entries_starting_together_wait_for_one_login(self, hass_, clock):
        """A restart starts every entry within a second. They must queue
        on the one login, not each run their own."""
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_login(*_a, **_k):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _account()

        with patch.object(cloud_account.CloudAccount, "login", new=slow_login), \
             patch.object(cloud_account, "async_get_clientsession", return_value=MagicMock()):
            tasks = [
                asyncio.ensure_future(async_acquire(hass_, f"E{i}", "user@example.com", "pw"))
                for i in range(3)
            ]
            await started.wait()
            release.set()
            accounts = await asyncio.gather(*tasks)

        assert calls == 1
        assert accounts[0] is accounts[1] is accounts[2]

    @pytest.mark.asyncio
    async def test_the_same_address_typed_differently_is_the_same_account(self, hass_, login):
        """iRobot's login ignores case in the address; so does the key."""
        await async_acquire(hass_, "E1", "User@Example.com", "pw")
        await async_acquire(hass_, "E2", " user@example.com", "pw")
        assert login.await_count == 1

    @pytest.mark.asyncio
    async def test_a_different_password_is_not_shared(self, hass_, login):
        """An entry with an outdated password must fail its OWN login and
        ask for reauthentication -- not ride on another entry's session."""
        a = await async_acquire(hass_, "E1", "user@example.com", "new")
        b = await async_acquire(hass_, "E2", "user@example.com", "old")
        assert a is not b
        assert login.await_count == 2

    @pytest.mark.asyncio
    async def test_the_login_uses_home_assistants_country_and_an_ios_app_id(self, hass_, login):
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        args, kwargs = login.await_args
        assert args[1:] == ("user@example.com", "pw", "DE")
        assert kwargs["app_id"].startswith("IOS-")

    @pytest.mark.asyncio
    async def test_the_registry_holds_no_readable_password(self, hass_, login):
        await async_acquire(hass_, "E1", "user@example.com", "hunter2")
        assert "hunter2" not in repr(list(hass_.data[DATA_KEY]))


class TestRelease:

    @pytest.mark.asyncio
    async def test_the_account_stays_while_one_entry_holds_it(self, hass_, login):
        a = await async_acquire(hass_, "E1", "user@example.com", "pw")
        await async_acquire(hass_, "E2", "user@example.com", "pw")
        async_release(hass_, "E1")
        assert await async_acquire(hass_, "E3", "user@example.com", "pw") is a
        assert login.await_count == 1

    @pytest.mark.asyncio
    async def test_the_last_entry_out_drops_it(self, hass_, login):
        a = await async_acquire(hass_, "E1", "user@example.com", "pw")
        async_release(hass_, "E1")
        assert hass_.data[DATA_KEY] == {}
        b = await async_acquire(hass_, "E1", "user@example.com", "pw")
        assert b is not a
        assert login.await_count == 2

    @pytest.mark.asyncio
    async def test_releasing_an_unrelated_entry_keeps_an_offered_login(self, hass_, login, clock):
        """An entry of ANOTHER account unloading while a new entry is
        being created must not throw the new entry's login away."""
        await async_acquire(hass_, "OTHER", "other@example.com", "pw")
        offered = _account()
        async_offer(hass_, offered, "user@example.com", "pw")
        async_release(hass_, "OTHER")
        assert await async_acquire(hass_, "E1", "user@example.com", "pw") is offered
        assert login.await_count == 1  # OTHER's only

    @pytest.mark.asyncio
    async def test_a_release_during_another_entrys_acquire_keeps_the_account(self, hass_, clock):
        """E2 is inside acquire (renewing its token) when E1, the only
        holder, unloads. The account must not be dropped from under E2,
        or the next entry logs in again."""
        offered = _account(expires_in=1)
        gate = asyncio.Event()

        async def slow_relogin():
            await gate.wait()

        offered.relogin = AsyncMock(side_effect=slow_relogin)
        async_offer(hass_, offered, "user@example.com", "pw")
        clock.now += OFFER_TTL_SECONDS + 1          # the offer itself is long gone
        slot = next(iter(hass_.data[DATA_KEY].values()))
        slot.entries.add("E1")
        task = asyncio.ensure_future(
            async_acquire(hass_, "E2", "user@example.com", "pw", mqtt_blid="BLID2")
        )
        await asyncio.sleep(0)
        async_release(hass_, "E1")
        gate.set()
        assert await task is offered
        assert hass_.data[DATA_KEY][next(iter(hass_.data[DATA_KEY]))].account is offered

    def test_a_slot_changing_hands_is_not_pruned(self, hass_, clock):
        """asyncio.Lock.locked() is False for the moment the lock passes
        to the next waiter (verified on Python 3.14). A prune in that
        moment -- another entry unloading -- must not delete the slot the
        waiter is about to use, or its entry lands in a slot nobody can
        find and the next entry logs in a second time.

        The moment itself cannot be staged reliably with asyncio's
        scheduler, so this pins the rule: a slot with a coroutine inside
        async_acquire() survives a prune even with its lock free."""
        async_offer(hass_, _account(), "user@example.com", "pw")
        clock.now += OFFER_TTL_SECONDS + 1      # nothing else keeps it
        slot = next(iter(hass_.data[DATA_KEY].values()))
        slot.busy = 1                           # a waiter between lock owners
        assert not slot.lock.locked()
        async_release(hass_, "UNRELATED")
        assert slot in hass_.data[DATA_KEY].values()
        slot.busy = 0
        async_release(hass_, "UNRELATED")
        assert hass_.data[DATA_KEY] == {}

    def test_releasing_an_entry_that_holds_nothing_is_harmless(self, hass_):
        async_release(hass_, "NEVER")
        assert hass_.data[DATA_KEY] == {}


class TestOfferFromTheConfigFlow:

    @pytest.mark.asyncio
    async def test_the_new_entry_takes_the_flows_login(self, hass_, login, clock):
        offered = _account()
        async_offer(hass_, offered, "user@example.com", "pw")
        assert await async_acquire(hass_, "E1", "user@example.com", "pw") is offered
        login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_abandoned_offer_expires(self, hass_, login, clock):
        async_offer(hass_, _account(), "user@example.com", "pw")
        clock.now += OFFER_TTL_SECONDS + 1
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        login.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_offer_is_the_login_for_every_entry_that_starts_next(self, hass_, login, clock):
        """Adding a second Prime robot an hour after the first: the
        first entry's login is older than an hour, and its token would
        have to be renewed. The flow's login is the newest there is.
        The entry holding the older one keeps it."""
        older = await async_acquire(hass_, "E1", "user@example.com", "pw")
        newer = _account()
        async_offer(hass_, newer, "user@example.com", "pw")
        assert await async_acquire(hass_, "E2", "user@example.com", "pw") is newer
        assert login.await_count == 1
        older.relogin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reauth_with_the_same_password_is_one_login(self, hass_, login, clock):
        """The entry still holds the account when the flow offers, then
        reloads: its release must not throw the offered login away, or
        setup logs in again seconds after the flow did -- the worst case
        being a locked account, where the user re-enters the same
        password."""
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        offered = _account()
        async_offer(hass_, offered, "user@example.com", "pw")
        async_release(hass_, "E1")          # the reload unloads ...
        assert await async_acquire(hass_, "E1", "user@example.com", "pw") is offered
        assert login.await_count == 1       # ... and sets up again

    @pytest.mark.asyncio
    async def test_a_reauth_offer_clears_a_remembered_failure(self, hass_, clock):
        """The user has just typed working credentials; the entry must not
        be answered with the failure from before."""
        failing = AsyncMock(side_effect=AuthCredentialsError("wrong"))
        with patch.object(cloud_account.CloudAccount, "login", new=failing), \
             patch.object(cloud_account, "async_get_clientsession", return_value=MagicMock()):
            with pytest.raises(AuthCredentialsError):
                await async_acquire(hass_, "E1", "user@example.com", "pw")
        offered = _account()
        async_offer(hass_, offered, "user@example.com", "pw")
        assert await async_acquire(hass_, "E1", "user@example.com", "pw") is offered

    @pytest.mark.asyncio
    async def test_the_flows_own_login_bypasses_the_registry(self, hass_, login):
        """The flow checks what the user typed; reusing anything would
        check nothing."""
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        await async_login(hass_, "user@example.com", "pw")
        assert login.await_count == 2


class TestAFailureAnswersForAMoment:
    """Against a locked account every attempt extends the lock. Three
    entries starting together must make ONE attempt, not three."""

    @pytest.mark.asyncio
    async def test_the_next_entry_gets_the_same_failure_without_a_login(self, hass_, clock):
        locked = AuthCredentialsError("locked", reason=CloudErrorReason.ACCOUNT_LOCKED)
        failing = AsyncMock(side_effect=locked)
        with patch.object(cloud_account.CloudAccount, "login", new=failing), \
             patch.object(cloud_account, "async_get_clientsession", return_value=MagicMock()):
            with pytest.raises(AuthCredentialsError):
                await async_acquire(hass_, "E1", "user@example.com", "pw")
            clock.now += FAILURE_REUSE_SECONDS / 2
            with pytest.raises(AuthCredentialsError) as excinfo:
                await async_acquire(hass_, "E2", "user@example.com", "pw")
        assert excinfo.value.reason is CloudErrorReason.ACCOUNT_LOCKED
        assert failing.await_count == 1

    @pytest.mark.asyncio
    async def test_after_the_moment_it_tries_again(self, hass_, clock):
        failing = AsyncMock(side_effect=[AuthTimeoutError("slow"), _account()])
        with patch.object(cloud_account.CloudAccount, "login", new=failing), \
             patch.object(cloud_account, "async_get_clientsession", return_value=MagicMock()):
            with pytest.raises(AuthTimeoutError):
                await async_acquire(hass_, "E1", "user@example.com", "pw")
            clock.now += FAILURE_REUSE_SECONDS + 1
            await async_acquire(hass_, "E1", "user@example.com", "pw")
        assert failing.await_count == 2


class TestPrimeGetsAFreshToken:
    """A Prime robot opens MQTT with the token of the shared login. One
    taken hours after the login may be about to expire, and a robot
    connecting with it would fail at once."""

    @pytest.mark.asyncio
    async def test_a_fresh_token_is_handed_over_as_it_is(self, hass_, clock):
        offered = _account(expires_in=PRIME_TOKEN_MARGIN_SECONDS * 3)
        async_offer(hass_, offered, "user@example.com", "pw")
        await async_acquire(hass_, "E1", "user@example.com", "pw", mqtt_blid="BLID2")
        offered.login_result.token_for_blid.assert_called_with("BLID2")
        offered.relogin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_token_near_expiry_is_renewed(self, hass_, clock):
        offered = _account(expires_in=PRIME_TOKEN_MARGIN_SECONDS - 1)
        async_offer(hass_, offered, "user@example.com", "pw")
        await async_acquire(hass_, "E1", "user@example.com", "pw", mqtt_blid="BLID2")
        offered.relogin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_robots_starting_together_share_the_renewal(self, hass_, clock):
        offered = _account(expires_in=PRIME_TOKEN_MARGIN_SECONDS - 1)

        async def relogin():
            offered.login_result.token_for_blid.return_value.seconds_until_expiry.return_value = 3600.0

        offered.relogin = AsyncMock(side_effect=relogin)
        async_offer(hass_, offered, "user@example.com", "pw")
        await asyncio.gather(
            async_acquire(hass_, "E1", "user@example.com", "pw", mqtt_blid="BLID2"),
            async_acquire(hass_, "E2", "user@example.com", "pw", mqtt_blid="BLID3"),
        )
        offered.relogin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_token_without_expiry_is_judged_by_the_logins_age(self, hass_, clock):
        """Every real fixture carries an expiry; one without must not
        force a relogin right after a fresh login, and must not be
        trusted forever either."""
        offered = _account(expires_in=None)
        async_offer(hass_, offered, "user@example.com", "pw")
        await async_acquire(hass_, "E1", "user@example.com", "pw", mqtt_blid="BLID2")
        offered.relogin.assert_not_awaited()
        clock.now += PRIME_TOKEN_MARGIN_SECONDS + 1
        await async_acquire(hass_, "E2", "user@example.com", "pw", mqtt_blid="BLID2")
        offered.relogin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offering_the_held_login_again_does_not_make_it_young(self, hass_, clock):
        """The config flow reads a robot from the login an entry holds
        and offers that same login to the entry it creates. That must
        not reset its age, or a token without an expiry an hour old
        would count as fresh."""
        held = _account(expires_in=None)
        async_offer(hass_, held, "user@example.com", "pw")
        await async_acquire(hass_, "E0", "user@example.com", "pw")   # an entry holds it
        clock.now += PRIME_TOKEN_MARGIN_SECONDS + 1
        async_offer(hass_, held, "user@example.com", "pw")   # the flow's hand-over
        await async_acquire(hass_, "E1", "user@example.com", "pw", mqtt_blid="BLID2")
        held.relogin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failed_renewal_answers_for_the_others(self, hass_, clock):
        """The Prime path renews more often than it logs in -- any
        entry starting an hour after the login does. A locked account
        must get ONE attempt from it, not one per robot and retry."""
        offered = _account(expires_in=1)
        locked = AuthCredentialsError("locked", reason=CloudErrorReason.ACCOUNT_LOCKED)
        offered.relogin = AsyncMock(side_effect=locked)
        async_offer(hass_, offered, "user@example.com", "pw")
        for entry in ("E1", "E2"):
            with pytest.raises(AuthCredentialsError):
                await async_acquire(hass_, entry, "user@example.com", "pw", mqtt_blid="BLID2")
        offered.relogin.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_classic_entry_never_renews(self, hass_, clock):
        """Its REST client relogs on 403 by itself."""
        offered = _account(expires_in=1)
        async_offer(hass_, offered, "user@example.com", "pw")
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        offered.relogin.assert_not_awaited()


class TestCredentialsNeverReachTheLog:
    """A debug line once printed the whole first robot record from the
    cloud, which carries that robot's cloud PASSWORD in plaintext.
    Anyone who turned on debug logging wrote a live credential into
    their log -- and into every log they attached to an issue
    (@ScenicSystemsLLC found it and did not paste the value).

    The line moved from cloud_api.py to cloud_account.py in 4.3, and the
    rule moved with it: field names, never values."""

    @pytest.mark.asyncio
    async def test_the_robots_line_names_fields_not_values(self, hass_, login, caplog):
        caplog.set_level(logging.DEBUG, logger=cloud_account.__name__)
        await async_acquire(hass_, "E1", "user@example.com", "pw")
        assert ROBOT_PASSWORD not in caplog.text
        assert "pw" not in [r.getMessage() for r in caplog.records]
        # And it still says which fields came back -- the point of the line.
        assert "first_robot_fields=['name', 'password', 'sku']" in caplog.text
        assert "2 robot(s)" in caplog.text

    def test_no_log_call_passes_a_whole_robot_record(self) -> None:
        """The shape to keep out: handing a log call an entire record
        from the cloud rather than the specific field wanted."""
        import ast
        import pathlib

        offenders = []
        for path in pathlib.Path("custom_components/roomba_plus").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_LOGGER"
                ):
                    continue
                for arg in node.args[1:]:
                    rendered = ast.unparse(arg).strip()
                    if (
                        ".values()" in rendered and "robots" in rendered
                        or rendered in ("robots", "self.login_result", "account.login_result")
                        or rendered.endswith("login_result.raw")
                    ):
                        offenders.append(f"{path.name}:{node.lineno} {rendered}")

        assert not offenders, "whole cloud records reaching the log: " + ", ".join(
            offenders
        )

    def test_the_rule_catches_the_original_line(self) -> None:
        """The check above must fail on the line that leaked."""
        import ast

        leaked = '_LOGGER.debug("first_robot=%s", next(iter(robots.values())))'
        call = ast.parse(leaked).body[0].value
        rendered = ast.unparse(call.args[1]).strip()
        assert ".values()" in rendered and "robots" in rendered


class TestDiagnostics:
    """What a beta report about shared logins needs -- and nothing that
    identifies the account."""

    @pytest.mark.asyncio
    async def test_an_entry_sees_its_share(self, hass_, login, clock):
        from custom_components.roomba_plus.cloud_account import async_diagnostics

        await async_acquire(hass_, "E1", "user@example.com", "hunter2")
        await async_acquire(hass_, "E2", "user@example.com", "hunter2")
        clock.now += 42
        diag = async_diagnostics(hass_, "E1")
        assert diag == {
            "shares_login": True,
            "entries_sharing": 2,
            "login_age_s": 42,
            "robots_on_account": 2,
            "acquire_in_progress": 0,
            "recent_failure_reason": None,
            "recent_failure_age_s": None,
        }
        text = repr(diag)
        for secret in ("user@example.com", "hunter2", *hass_.data[DATA_KEY]):
            assert secret not in text

    def test_an_entry_without_a_share_says_so(self, hass_):
        from custom_components.roomba_plus.cloud_account import async_diagnostics

        assert async_diagnostics(hass_, "NOPE") == {"shares_login": False}

    def test_both_diagnostics_payloads_carry_it(self):
        """Classic and Prime build their downloads separately."""
        import pathlib

        source = pathlib.Path("custom_components/roomba_plus/diagnostics.py").read_text()
        assert source.count('"cloud_account": cloud_account_diagnostics(hass, config_entry.entry_id)') == 2
