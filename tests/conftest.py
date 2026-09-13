"""Pytest configuration for Roomba+ tests.

Applies compatibility shims for HA API differences between test environments
and ensures the integration is importable regardless of installed HA version.
"""


def pytest_configure(config):
    """Apply HA version compatibility shims before any test module is imported.

    Shim 1 (existing): AddConfigEntryEntitiesCallback — HA 2024.4.
    Shim 2 (v2.4.0): Segment dataclass — HA 2026.3 vacuum.clean_area.
    Shim 3 (v2.4.0): VacuumEntityFeature.CLEAN_AREA — HA 2026.3.
    Shim 4 (v2.9.0, REST980-MIGRATE): helpers.service_info.dhcp/zeroconf —
    these submodules were introduced after HA 2025.1.4, the newest version
    installable in a Python 3.12 test environment (everything past 2025.1.4
    requires Python ≥3.13). config_flow.py imports DhcpServiceInfo/
    ZeroconfServiceInfo from there; on 2025.1.4 the same classes still exist,
    just under homeassistant.components.dhcp/zeroconf. Needed the first time
    a test imports config_flow.py directly (test_rest980_migrate.py) — every
    prior config_flow test used a hand-copied reference implementation
    instead, which is exactly the kind of drift risk this shim avoids going
    forward.

    When to remove shims 2, 3 & 4: once CI is pinned to pytest-homeassistant-
    custom-component built against a matching newer HA version.
    """
    import dataclasses
    import enum
    import importlib
    import sys
    import types

    # Shim 1: AddConfigEntryEntitiesCallback
    try:
        ep = importlib.import_module("homeassistant.helpers.entity_platform")
        if not hasattr(ep, "AddConfigEntryEntitiesCallback"):
            ep.AddConfigEntryEntitiesCallback = getattr(
                ep, "AddEntitiesCallback", None
            )
    except ImportError:
        pass

    # Shim 2 + 3: vacuum.clean_area symbols (HA 2026.3)
    try:
        vacuum_mod = importlib.import_module("homeassistant.components.vacuum")

        if not hasattr(vacuum_mod, "Segment"):
            @dataclasses.dataclass(slots=True)
            class Segment:
                id: str
                name: str
                group: str | None = None
            vacuum_mod.Segment = Segment

        feature_cls = getattr(vacuum_mod, "VacuumEntityFeature", None)
        if feature_cls is not None and not hasattr(feature_cls, "CLEAN_AREA"):
            existing_values = [m.value for m in feature_cls]
            next_bit = max(existing_values) << 1 if existing_values else 1
            new_cls = enum.IntFlag(
                "VacuumEntityFeature",
                [(m.name, m.value) for m in feature_cls] + [("CLEAN_AREA", next_bit)],
            )
            vacuum_mod.VacuumEntityFeature = new_cls
    except ImportError:
        pass

    # Shim 4: helpers.service_info.dhcp / .zeroconf
    for sub, source_mod, cls_name in (
        ("dhcp", "homeassistant.components.dhcp", "DhcpServiceInfo"),
        ("zeroconf", "homeassistant.components.zeroconf", "ZeroconfServiceInfo"),
    ):
        full_name = f"homeassistant.helpers.service_info.{sub}"
        if full_name in sys.modules:
            continue
        try:
            source = importlib.import_module(source_mod)
            cls = getattr(source, cls_name)
        except (ImportError, AttributeError):
            continue
        shim_mod = types.ModuleType(full_name)
        setattr(shim_mod, cls_name, cls)
        sys.modules[full_name] = shim_mod


import pytest
from unittest.mock import MagicMock  # noqa: F401  (used in annotations)


# v2.9.0 — REMOVED the autouse _close_mts_threadsafe_coroutines fixture that
# used to live here. It patched
# "custom_components.roomba_plus.mission_timer_store.asyncio.run_coroutine_threadsafe"
# to silence a "coroutine was never awaited" warning from
# MissionTimerStore._schedule_save() in tests that don't otherwise care about
# it. The patch target string looked module-scoped, but `mission_timer_store
# .asyncio` IS the same global `asyncio` module object every other module
# also imports (Python does not copy modules per-importer) — so this was
# actually a GLOBAL patch of asyncio.run_coroutine_threadsafe for every
# single test in the entire session (autouse=True), replacing it with
# `lambda coro, loop: coro.close()` (which discards the coroutine and
# returns None instead of a real Future).
#
# This silently broke every OTHER test relying on real
# run_coroutine_threadsafe scheduling — including the entire
# make_mission_callback() path in callbacks.py (async_record_mission
# scheduling). Misdiagnosed for a long time as a pytest-asyncio /
# HassEventLoopPolicy / pytest-socket self-pipe interaction (none of which
# was the actual cause — confirmed by a minimal diagnostic test showing
# asyncio.run_coroutine_threadsafe was a bare MagicMock mid-test, with no
# patch() call active in that test file at all).
#
# test_mission_timer_store.py already patches this locally, correctly
# scoped with `with patch(...)`, wherever it actually needs to (see e.g.
# its own "callbacks.asyncio.run_coroutine_threadsafe" patches) — so no
# functionality is lost by removing the global version. Any other test
# that newly trips the original warning should patch it locally too,
# scoped to just that test, the same way.


# pytest 9 + pytest-homeassistant-custom-component: the plugin ships an
# async autouse fixture (`enable_event_loop_debug`) that synchronous
# tests cannot consume. In pytest 9 that stopped being a warning and
# became an error, so every sync test in the suite errored at collection.
#
# Overriding it with a synchronous no-op keeps the plugin's other
# fixtures available. Async tests that genuinely want loop debugging are
# unaffected -- none here do.
@pytest.fixture(autouse=True)
def enable_event_loop_debug():  # noqa: PT004
    """Neutralise the plugin's async autouse fixture. See note above."""
    return None


def robot_mock(**attrs: object) -> "MagicMock":
    """A stand-in for the local robot client, with async methods async.

    WHY A HELPER RATHER THAN `MagicMock()` AT EACH SITE. roombapy 2.x
    made `send_command`, `set_preference`, `connect` and `disconnect`
    coroutines. A MagicMock answers a coroutine call with another
    MagicMock, and awaiting that raises `TypeError: object MagicMock
    can't be used in 'await' expression` -- so a test fails on the await
    rather than on whatever it was checking.

    Worth knowing what this does NOT protect against, because the 4.2
    migration turned on it: mocking hid the opposite mistake completely.
    The suite passed 6197 tests against code that handed 46 coroutines
    to `hass.async_add_executor_job()`, where each one ran in a thread,
    returned a coroutine object nobody awaited, and never reached the
    robot. A MagicMock accepts that as happily as it accepts the correct
    call. `scripts/check_no_executor_coroutines.py` is what catches it;
    this helper only keeps the tests honest about the await.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock = MagicMock()
    for coroutine in (
        "send_command",
        "set_preference",
        "set_preferences",
        "connect",
        "disconnect",
    ):
        setattr(mock, coroutine, AsyncMock())
    for name, value in attrs.items():
        setattr(mock, name, value)
    return mock


def hass_mock(**attrs: object) -> "MagicMock":
    """A stand-in for `hass` that does not leak unawaited coroutines.

    `hass.async_create_task(coro)` is the correct way for production code
    to launch work from a synchronous callback. Against a bare MagicMock
    the coroutine is created, never scheduled and then garbage-collected,
    which Python reports as::

        RuntimeWarning: coroutine 'async_check_cloud_stale' was never awaited

    41 of those came out of one CI run. All of them were this, and none
    was a bug in the integration -- but a warning that is always there is
    a warning nobody reads, and a real unawaited coroutine would arrive
    looking exactly the same.

    `test_callbacks.py` has closed its coroutines this way for a while;
    this is that helper where every test can reach it.
    """
    import asyncio
    from unittest.mock import MagicMock

    mock = MagicMock()

    def _close_coroutines(*args: object, **kwargs: object) -> MagicMock:
        for arg in args:
            if asyncio.iscoroutine(arg):
                arg.close()
        return MagicMock()

    # A MagicMock WITH a side effect, not a plain function: ten tests
    # assert on `hass.async_create_task.call_args`, and replacing the
    # attribute outright takes that away. The side effect closes the
    # coroutine; the mock still records the call.
    mock.async_create_task = MagicMock(side_effect=_close_coroutines)
    mock.async_create_background_task = MagicMock(side_effect=_close_coroutines)

    # FOUR WAYS IN, not one. Home Assistant's own
    # `Entity.schedule_update_ha_state()` reaches the loop through
    # `hass.create_task` or `hass.loop.call_soon_threadsafe`, and any
    # entity calling it against a bare mock leaks
    # `Entity.async_update_ha_state`. That was six of the sixteen
    # warnings left after the first pass, and none of them was visible
    # from the test that reported it -- the warning surfaces whenever
    # the garbage collector gets round to it, which is usually some
    # later test in an unrelated file.
    #
    # `python -X tracemalloc=8` is what turned the reported name into
    # the actual line.
    mock.create_task = MagicMock(side_effect=_close_coroutines)
    mock.add_job = MagicMock(side_effect=_close_coroutines)
    mock.loop.call_soon_threadsafe = MagicMock(side_effect=_close_coroutines)

    for name, value in attrs.items():
        setattr(mock, name, value)
    return mock


def entry_mock(**attrs: object) -> "MagicMock":
    """A config-entry stand-in that does not leak unawaited coroutines.

    The sibling of `hass_mock()`, for the other half of the same
    problem: production also launches background work through the config
    entry (`entry.async_create_background_task(hass, coro, name=...)`),
    not only through `hass`. A bare MagicMock there leaves the coroutine
    unscheduled and Python reports it as never awaited.

    Same construction: a MagicMock with a side effect, so tests asserting
    on `entry.async_create_background_task.call_args` keep working.
    """
    import asyncio
    from unittest.mock import MagicMock

    mock = MagicMock()

    def _close_coroutines(*args: object, **kwargs: object) -> MagicMock:
        for arg in args:
            if asyncio.iscoroutine(arg):
                arg.close()
        return MagicMock()

    mock.async_create_background_task = MagicMock(side_effect=_close_coroutines)
    mock.async_create_task = MagicMock(side_effect=_close_coroutines)
    for name, value in attrs.items():
        setattr(mock, name, value)
    return mock


@pytest.fixture(autouse=True)
def _close_coroutines_handed_to_a_mock_loop():
    """Stop `run_coroutine_threadsafe` leaking against a mocked loop.

    THE ONE ROUTE THE MOCK HELPERS CANNOT COVER. Production hands some
    work to the loop directly::

        asyncio.run_coroutine_threadsafe(self._async_save(), self.hass.loop)

    The coroutine is captured in a closure, not passed to any method of
    the loop, so setting `hass.loop.call_soon_threadsafe` to close its
    arguments -- which is what `hass_mock()` does, and which handles
    `Entity.schedule_update_ha_state` -- never sees it. Against a
    MagicMock loop the scheduling callback simply never runs, and the
    coroutine is collected unawaited.

    A real loop in every affected test would be the other answer, and a
    much larger change: these tests are deliberately synchronous.

    AUTOUSE, because the leak is not the test's fault and the fix has
    nothing to teach a reader of that test. A real loop is left alone,
    so nothing that works today changes.
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    real = asyncio.run_coroutine_threadsafe

    def _guarded(coro, loop):
        if isinstance(loop, MagicMock):
            coro.close()
            return MagicMock()
        return real(coro, loop)

    with patch("asyncio.run_coroutine_threadsafe", _guarded):
        yield
