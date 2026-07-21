"""Short-lived, single-use bridge for a V4/Prime LoginResult from the
config flow's validation login to the immediate first async_setup_entry()
call for that same entry.

WHY THIS EXISTS: prompted by a real "onboarding is slow" field report
(chairstacker). The config flow (async_step_prime_account) already runs
a full Gigya+iRobot login to validate credentials and list the
account's robots. Moments later, HA calls async_setup_entry() for the
newly-created entry, which -- independently, and correctly so, since it
must also work standalone on every later HA restart with no config-flow
object involved at all -- runs the exact same login chain again from
scratch. During this one specific handoff (config flow just finished,
setup starts immediately after), that second login is fully redundant.

RISK, DELIBERATELY KEPT NARROW (see the project's own version-plan
discussion for the full reasoning): a general, longer-lived credential
cache was considered and rejected as not worth its added complexity --
it would only ever help this same one-time handoff, for a broader
security surface. What's here instead:
  - In-memory only, never persisted to disk (unlike config_entry.data,
    which does get written to .storage/).
  - Keyed by blid specifically (not username), matching the one thing
    both sides of the handoff (config_flow.py, __init__.py) agree on
    at the point this matters -- the robot being set up.
  - SINGLE-USE: pop_pending_login() removes the entry on read, whether
    or not it turns out to still be fresh. A second async_setup_entry()
    call for the same blid (e.g. a reload) always does its own fresh
    login -- this cache only ever bridges the one handoff it was built
    for, never anything after.
  - Short TTL (60s) as a safety bound, not the primary mechanism --
    single-use already prevents reuse across restarts; the TTL only
    guards against an unusually slow handoff making a borderline-stale
    result look falsely fresh.

If the cache misses (expired, never stored, or this is a genuine later
restart with no config flow involved at all), the caller simply proceeds
with its own fresh login -- identical behavior to before this existed.
"""
from __future__ import annotations

import time

from roombapy_prime import LoginResult

_TTL_SECONDS = 60.0

_pending_logins: dict[str, tuple[float, LoginResult]] = {}


def store_pending_login(blid: str, login_result: LoginResult) -> None:
    """Called by config_flow.py right after picking a robot, with the
    LoginResult its own validation login already obtained. Overwrites
    any existing entry for this blid without complaint -- if a second
    config flow run somehow raced for the exact same robot, the newer
    result is the more useful one to keep."""
    _pending_logins[blid] = (time.monotonic(), login_result)


def pop_pending_login(blid: str) -> LoginResult | None:
    """Called by __init__.py's _async_setup_entry_prime() before doing
    its own login. Returns None (triggering a normal fresh login) if
    nothing was cached for this blid, or if what was cached has aged
    past _TTL_SECONDS. Removes the entry either way -- see the module
    docstring's SINGLE-USE point."""
    entry = _pending_logins.pop(blid, None)
    if entry is None:
        return None
    stored_at, login_result = entry
    if time.monotonic() - stored_at > _TTL_SECONDS:
        return None
    return login_result
