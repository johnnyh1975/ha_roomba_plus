"""One iRobot cloud login per account, shared by every robot on it.

UNTIL 4.3 EVERY CONFIG ENTRY LOGGED IN ON ITS OWN. A household with
three robots on one account ran the full Gigya + iRobot login three
times, and a Home Assistant restart fired all three within a second.
iRobot limits how many sessions an account may hold and locks an
account after too many attempts; @jpatchMC's account locked with two
Prime robots, @ScenicSystemsLLC lost the rooms of all three of his
Classic robots at once after a restart. Neither was proven to be the
login multiplier, but the multiplier made both more likely, and the
login response already lists every robot on the account.

WHAT IS SHARED. roombapy-prime's CloudAccount is one login plus the
REST clients it hands out; the clients relog through the account, so a
403 seen by two robots at once costs one login, not two. This module
keeps one CloudAccount per set of credentials for as long as a config
entry uses it:

  * async_acquire() -- the account for these credentials, logging in
    only if no entry holds it yet. Entries that start together wait for
    one login instead of each running their own.
  * async_release() -- an entry is done with it (unload). The last one
    out drops the account.
  * async_offer() -- the config flow has just logged in to validate the
    credentials; the entry it creates takes that login instead of
    running a second one moments later. This replaces the single-use
    blid bridge (_prime_login_bridge.py) that did the same for Prime
    only.

KEYED BY USERNAME, PASSWORD AND COUNTRY. Two entries with the same
account but different passwords (one of them outdated) do not share: the
outdated one fails its own login and asks for reauthentication, as it
did before. The key is a hash, so hass.data never holds the password in
a readable form; the CloudAccount itself keeps it for relogin, exactly
as every client did before.

A FAILED LOGIN ANSWERS FOR A MOMENT. When the account is locked, three
entries starting together must not make three attempts against it.
A failure is handed to anyone asking for the same credentials within
FAILURE_REUSE_SECONDS instead of a new attempt.

WHAT IS NOT SHARED YET (4.3.0b1). A Prime robot takes its FIRST login
from the account, and then goes its own way: PrimeRobot keeps its own
relogin for the MQTT token. That moves onto the account in roombapy-prime
0.5.0.

IN MEMORY ONLY. Nothing here is written to disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from roombapy_prime import CloudAccount, CloudError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: hass.data key of the registry.
DATA_KEY = f"{DOMAIN}_cloud_accounts"

#: How long an account handed over by the config flow waits for an
#: entry to take it. The entry is created seconds after the flow's
#: login; this only bounds a flow that was abandoned halfway.
OFFER_TTL_SECONDS = 600.0

#: How long a failed login answers for the same credentials.
FAILURE_REUSE_SECONDS = 30.0

#: A Prime robot needs an MQTT token with at least this much life left.
#: Its own refresh loop relogs five minutes before expiry; a token
#: handed over closer to that than this is replaced first.
PRIME_TOKEN_MARGIN_SECONDS = 600.0


def cloud_country(hass: HomeAssistant) -> str:
    """The country iRobot's endpoint discovery is asked for.

    iRobot splits its backend by region, and the wrong one answers with
    empty data for EU accounts. Home Assistant's own country setting is
    the best guess; "US" when none is set."""
    return (hass.config.country or "US").upper()


def new_app_id() -> str:
    """The app id of one login: the iOS app's form. Classic's mission
    history sends it again with every request, so the account keeps it."""
    return f"IOS-{uuid.uuid4()}"


def _key(username: str, password: str, country: str) -> str:
    raw = "\0".join((username.strip().casefold(), password, country.upper()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class _Slot:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    account: CloudAccount | None = None
    entries: set[str] = field(default_factory=set)
    #: Coroutines inside async_acquire() for this slot, waiting or
    #: working. asyncio.Lock.locked() is False for the moment the lock
    #: passes from one waiter to the next, so it cannot tell whether
    #: somebody is about to use the slot; this can.
    busy: int = 0
    offered_at: float | None = None
    #: When the account last logged in, as far as this module knows --
    #: for a token that carries no expiry.
    login_at: float = 0.0
    failure: CloudError | None = None
    failed_at: float = 0.0

    def recent_failure(self, now: float) -> CloudError | None:
        if self.failure is not None and now - self.failed_at < FAILURE_REUSE_SECONDS:
            return self.failure
        return None

    def offered_recently(self, now: float) -> bool:
        return self.offered_at is not None and now - self.offered_at < OFFER_TTL_SECONDS


def _slots(hass: HomeAssistant) -> dict[str, _Slot]:
    slots: dict[str, _Slot] = hass.data.setdefault(DATA_KEY, {})
    return slots


def _prune(slots: dict[str, _Slot], now: float) -> None:
    """Drops accounts nobody holds: offered and never taken, or left
    behind by the last entry. A slot someone is using or waiting on
    stays, and so does a failure that still answers for others."""
    for key in list(slots):
        slot = slots[key]
        if slot.entries or slot.busy or slot.lock.locked():
            continue
        if not slot.offered_recently(now) and slot.recent_failure(now) is None:
            del slots[key]


async def async_login(
    hass: HomeAssistant, username: str, password: str
) -> CloudAccount:
    """A fresh login, outside the registry -- for the config flow, which
    must check the credentials the user just typed rather than reuse
    anything. Raises the library's CloudError subclasses."""
    account = await CloudAccount.login(
        async_get_clientsession(hass),
        username,
        password,
        cloud_country(hass),
        app_id=new_app_id(),
    )
    _log_login(account)
    return account


@callback
def async_peek(
    hass: HomeAssistant, username: str, password: str
) -> CloudAccount | None:
    """The account an entry or the config flow already holds for these
    credentials, without logging in -- None when there is none. For the
    config flow, to read a robot's details from a login that exists
    anyway."""
    slot = _slots(hass).get(_key(username, password, cloud_country(hass)))
    return slot.account if slot is not None else None


@callback
def async_offer(
    hass: HomeAssistant, account: CloudAccount, username: str, password: str
) -> None:
    """Hands a login the config flow has just made to the entry that is
    about to start with these credentials.

    ALWAYS TAKEN, even when an entry already holds an account for these
    credentials: the flow's login is the newest there is. Entries that
    hold the older one keep it -- their clients relog on their own -- and
    every entry that starts from now on gets this one. The cases that
    need it: a reauthentication or an options save with the SAME
    password, where the entry reloads and would otherwise log in again
    seconds after the flow did; and a second Prime robot added an hour
    after the first, whose token in the older login has expired."""
    now = time.monotonic()
    slots = _slots(hass)
    _prune(slots, now)
    slot = slots.setdefault(_key(username, password, cloud_country(hass)), _Slot())
    if slot.account is not account:
        # A login the slot already holds (the config flow read a robot
        # from it) keeps its age -- the age decides whether a token
        # without an expiry is renewed before a Prime robot uses it.
        slot.account = account
        slot.login_at = now
    slot.offered_at = now
    slot.failure = None


async def async_acquire(
    hass: HomeAssistant,
    entry_id: str,
    username: str,
    password: str,
    *,
    mqtt_blid: str | None = None,
) -> CloudAccount:
    """The account for these credentials, shared with every other entry
    that holds it. Logs in only when nobody does.

    `mqtt_blid`: a Prime robot about to open MQTT with this login's
    token. Its token must have PRIME_TOKEN_MARGIN_SECONDS left, or the
    account logs in again first -- once, for every robot waiting.

    Raises the library's CloudError subclasses. The caller releases with
    async_release() when its entry unloads."""
    slots = _slots(hass)
    _prune(slots, time.monotonic())
    slot = slots.setdefault(_key(username, password, cloud_country(hass)), _Slot())
    slot.busy += 1
    try:
        async with slot.lock:
            account = await _async_account(hass, slot, entry_id, username, password)
            if mqtt_blid is not None:
                await _async_fresh_for_mqtt(slot, account, mqtt_blid)
            slot.entries.add(entry_id)
            slot.offered_at = None
            return account
    finally:
        slot.busy -= 1


async def _async_account(
    hass: HomeAssistant, slot: _Slot, entry_id: str, username: str, password: str
) -> CloudAccount:
    """The slot's account, logging in if it has none. Called under the
    slot's lock."""
    if slot.account is not None:
        others = len(slot.entries - {entry_id})
        _LOGGER.debug(
            "iRobot cloud: %s shares the login of %d other entr%s",
            entry_id, others, "y" if others == 1 else "ies",
        )
        return slot.account
    _raise_recent_failure(slot, entry_id)
    try:
        account = await CloudAccount.login(
            async_get_clientsession(hass),
            username,
            password,
            cloud_country(hass),
            app_id=new_app_id(),
        )
    except CloudError as exc:
        _remember_failure(slot, exc)
        raise
    slot.account = account
    slot.login_at = time.monotonic()
    slot.failure = None
    _log_login(account)
    return account


async def _async_fresh_for_mqtt(slot: _Slot, account: CloudAccount, blid: str) -> None:
    """Logs the account in again when its MQTT token for `blid` is about
    to expire. Called under the slot's lock, so robots starting together
    share the one new login.

    A token without an expiry is judged by the login's age instead, as
    far as this module knows it. A relogin that fails answers for the
    others just like a failed login: against a locked account, every
    attempt extends the lock."""
    remaining = account.login_result.token_for_blid(blid).seconds_until_expiry()
    if remaining is None:
        age = time.monotonic() - slot.login_at
        if age < PRIME_TOKEN_MARGIN_SECONDS:
            return
        why = f"no expiry, login {age:.0f} s old"
    elif remaining >= PRIME_TOKEN_MARGIN_SECONDS:
        return
    else:
        why = f"{remaining:.0f} s left"
    _raise_recent_failure(slot, blid)
    _LOGGER.debug("iRobot cloud: MQTT token for %s: %s; logging in again", blid, why)
    try:
        await account.relogin()
    except CloudError as exc:
        _remember_failure(slot, exc)
        raise
    slot.login_at = time.monotonic()
    slot.failure = None


def _raise_recent_failure(slot: _Slot, who: str) -> None:
    failure = slot.recent_failure(time.monotonic())
    if failure is None:
        return
    _LOGGER.debug(
        "iRobot cloud: login for %s failed %.0f s ago (%s); not trying again yet",
        who, time.monotonic() - slot.failed_at, failure.reason,
    )
    # A fresh traceback each time: the same exception object is raised
    # for every entry that asks, and would otherwise collect them all.
    raise failure.with_traceback(None)


def _remember_failure(slot: _Slot, exc: CloudError) -> None:
    slot.failure = exc
    slot.failed_at = time.monotonic()


@callback
def async_release(hass: HomeAssistant, entry_id: str) -> None:
    """The entry no longer uses its account. The last entry out drops it
    and the next one to start logs in again -- unless somebody is about
    to take it, or the config flow has just handed it over."""
    now = time.monotonic()
    slots = _slots(hass)
    for slot in slots.values():
        if entry_id not in slot.entries:
            continue
        slot.entries.discard(entry_id)
        if not slot.entries and not slot.busy and not slot.offered_recently(now):
            slot.account = None
    _prune(slots, now)


@callback
def async_diagnostics(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """This entry's share of its account, for the diagnostics download.

    WHAT A BETA REPORT ABOUT SHARED LOGINS NEEDS, AND NOTHING THAT
    IDENTIFIES THE ACCOUNT: no address, no password, and not the slot's
    hash either -- with the address known, a hash of the password is
    something to guess against."""
    now = time.monotonic()
    for slot in _slots(hass).values():
        if entry_id not in slot.entries:
            continue
        failure = slot.recent_failure(now)
        robots = _robot_count(slot.account)
        return {
            "shares_login": True,
            "entries_sharing": len(slot.entries),
            "login_age_s": round(now - slot.login_at) if slot.login_at else None,
            "robots_on_account": robots,
            "acquire_in_progress": slot.busy,
            "recent_failure_reason": str(failure.reason) if failure is not None else None,
            "recent_failure_age_s": round(now - slot.failed_at) if failure is not None else None,
        }
    return {"shares_login": False}


def _robot_count(account: CloudAccount | None) -> int | None:
    if account is None:
        return None
    robots: Any = account.login_result.raw.get("robots")
    return len(robots) if isinstance(robots, dict) else None


def _log_login(account: CloudAccount) -> None:
    """Which robots and which fields came back -- THE FIELD NAMES, NEVER
    THE VALUES. A robot record carries the robot's cloud password in
    plaintext; this line once printed a whole one, and debug logs get
    attached to issues (@ScenicSystemsLLC spotted it and did not paste
    the value)."""
    robots: Any = account.login_result.raw.get("robots", {})
    if not isinstance(robots, dict):
        robots = {}
    _first: Any = next(iter(robots.values()), {})
    _LOGGER.info("iRobot cloud: logged in, %d robot(s) on the account", len(robots))
    _LOGGER.debug(
        "iRobot cloud: robots -- keys=%s  first_robot_fields=%s",
        sorted(robots),
        sorted(_first) if isinstance(_first, dict) else type(_first).__name__,
    )
