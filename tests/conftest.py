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
    a test imports config_flow.py directly (the REST980-MIGRATE tests, now in
    test_config_flow.py) — every
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

    # Speed only, no behaviour: see each function.
    _parse_each_source_once()
    _scan_new_fixtures_only()


import pytest  # noqa: E402  (after pytest_configure, which needs none of it)


# ── Test-suite speed (4.3.0b2) ─────────────────────────────────────────────
#
# The suite took nearly four minutes on GitHub. Two costs had nothing to
# do with what the tests check; both are removed here, the rest by
# running the suite on every CPU (`-n auto` in the workflows).

#: Directories whose own `ast.parse` calls may share one tree per source.
_PARSE_CACHE_ROOTS = ("tests", "scripts")

#: {source: tree} handed out by the cached `ast.parse`, and
#: {source: fingerprint of the tree as parsed}. Module level,
#: so the session check below can see them.
_PARSED_TREES: dict = {}
_FINGERPRINTS: dict = {}


def _tree_fingerprint(tree) -> bytes:
    """Every field, position and attribute of every node. A pickle
    carries each node's whole `__dict__`, so an attribute added later
    (a parent link) changes it as much as a changed field does. About
    ten times cheaper than comparing `ast.dump` output."""
    import hashlib
    import pickle

    return hashlib.blake2b(pickle.dumps(tree, protocol=5)).digest()


def _parse_each_source_once() -> None:
    """The guard tests parsed the same files about 6500 times.

    Measured on the full suite: 6506 `ast.parse` calls, 364 distinct
    sources, 23 s of a 99 s run. Each guard reads the whole package and
    parses it again, and there are dozens of guards.

    A tree is cached per source text, and ONLY FOR CALLS MADE FROM
    tests/ AND scripts/. Everyone else gets a fresh tree: pytest and
    coverage.py parse sources too (pytest's assertion rewriter even
    changes its tree in place), and none of that was checked for
    whether it may share.

    The trees handed out are shared, so our own code must not change
    them. `_cached_trees_are_unchanged` below fails the run if one did.
    """
    import ast
    import os
    import sys

    if getattr(ast.parse, "_roomba_plus_cached", False):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    roots = tuple(
        os.path.join(os.path.dirname(here), name) + os.sep for name in _PARSE_CACHE_ROOTS
    )
    parse = ast.parse
    trees = _PARSED_TREES

    def cached_parse(source, filename="<unknown>", mode="exec", *args, **kwargs):
        caller = sys._getframe(1).f_code.co_filename
        if (
            args
            or set(kwargs) - {"filename"}
            or mode != "exec"
            or not isinstance(source, (str, bytes))
            or not os.path.abspath(caller).startswith(roots)
        ):
            return parse(source, filename, mode, *args, **kwargs)
        tree = trees.get(source)
        if tree is None:
            tree = trees[source] = parse(source, kwargs.get("filename", filename), mode)
            _FINGERPRINTS[source] = _tree_fingerprint(tree)
        return tree

    cached_parse._roomba_plus_cached = True  # type: ignore[attr-defined]
    ast.parse = cached_parse


def _changed_cached_trees(only: "list | None" = None) -> list[str]:
    """Cached trees that no longer match their source: a node changed
    (fields or positions), or an attribute was added to one (the usual
    way to add parent links). The first lines of each, for the report.
    `only` limits the check to those sources."""
    changed = []
    for source, tree in list(_PARSED_TREES.items()):
        if only is not None and source not in only:
            continue
        if _tree_fingerprint(tree) != _FINGERPRINTS.get(source):
            text = source.decode(errors="replace") if isinstance(source, bytes) else source
            changed.append(text.strip().splitlines()[0][:80] if text.strip() else "<empty>")
    return changed


@pytest.fixture(autouse=True, scope="session")
def _cached_trees_are_unchanged():
    """At the end of the run (of each xdist worker): every tree handed
    out by the cached `ast.parse` still matches its source. A test that
    changed one would have changed it for every later test parsing the
    same file. Reported as an error at teardown, which fails the run."""
    yield
    changed = _changed_cached_trees()
    assert not changed, (
        "A test or script changed a tree from ast.parse, which conftest.py "
        f"shares between all callers. Parse a private copy instead. First lines: {changed}"
    )


def _scan_new_fixtures_only() -> None:
    """pytest-asyncio 0.26 looked at every fixture once per test.

    Before collecting each test function it walks every fixture pytest
    knows -- Home Assistant's plugin brings hundreds -- and asks of each
    whether it is async. A synchronous fixture is never marked as done,
    so it is asked again for the next test: 7.9 million checks for
    8500 tests, 15 of the 25 s that collection took on the HA 2025.5
    job. pytest-asyncio 1.x no longer does this; HA 2025.5 pins 0.26.

    The scan only has something to do after new fixtures were
    registered, so it is skipped while the number of fixtures is
    unchanged. Registering is the only way pytest changes that number.
    Only 0.26 is patched: the wrapped function is internal, and this
    is the version checked against.
    """
    try:
        import pytest_asyncio
        from pytest_asyncio import plugin
    except ImportError:
        return
    if not str(getattr(pytest_asyncio, "__version__", "")).startswith("0.26."):
        return
    scan = getattr(plugin, "_preprocess_async_fixtures", None)
    if scan is None or getattr(scan, "_roomba_plus_cached", False):
        return
    seen: dict[int, int] = {}

    def scan_when_new(collector, processed_fixturedefs):
        manager = collector.config.pluginmanager.get_plugin("funcmanage")
        known = getattr(manager, "_arg2fixturedefs", None)
        if known is None:
            return scan(collector, processed_fixturedefs)
        count = sum(map(len, known.values()))
        if seen.get(id(manager)) == count:
            return None
        scan(collector, processed_fixturedefs)
        seen[id(manager)] = count
        return None

    scan_when_new._roomba_plus_cached = True  # type: ignore[attr-defined]
    plugin._preprocess_async_fixtures = scan_when_new


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


#: Where the hand-made hass stubs keep their stores: one directory per
#: process, so parallel workers (`-n auto`) do not share files.
TEST_CONFIG_DIR = __import__("os").path.join(
    __import__("tempfile").gettempdir(), f"roomba_plus_test_{__import__('os').getpid()}"
)


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


def entry_mock(schedule_on: object = None, **attrs: object) -> "MagicMock":
    """A config-entry stand-in that does not leak unawaited coroutines.

    Pass `schedule_on=hass` when the test needs the coroutine to actually
    RUN -- `async_create_task` then schedules it on `hass.loop`, and the
    test drains it as before with
    `hass.loop.run_until_complete(asyncio.sleep(0))`.

    Without it the coroutine is closed instead, which is right for tests
    that only care that scheduling happened. Getting this backwards is
    silent in one direction and loud in the other: a closed coroutine
    makes an assertion on its side effects fail with an empty list, which
    reads like the production code not scheduling at all.

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

    def _schedule(*args: object, **kwargs: object) -> MagicMock:
        # A real loop only. `hass_mock()` has a MagicMock `.loop`, which
        # would accept ensure_future() and then never run anything, leaving
        # the coroutine unawaited -- the exact warning this helper exists
        # to prevent. Tests that want the coroutine to RUN must pass a hass
        # carrying a genuine event loop.
        loop = getattr(schedule_on, "loop", None)
        if not isinstance(loop, asyncio.AbstractEventLoop):
            loop = None
        for arg in args:
            if asyncio.iscoroutine(arg):
                if loop is not None:
                    asyncio.ensure_future(arg, loop=loop)
                else:
                    arg.close()
        return MagicMock()

    _handler = _schedule if schedule_on is not None else _close_coroutines
    mock.async_create_background_task = MagicMock(side_effect=_handler)
    mock.async_create_task = MagicMock(side_effect=_handler)
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



@pytest.fixture(autouse=True)
def _isolate_mission_timer_store(monkeypatch: pytest.MonkeyPatch):
    """Every test gets a mock HA Store behind MissionTimerStore.

    WHY HERE AND NOT IN ONE TEST FILE. `schedule_save` used to reach the
    store through `hass.loop.call_soon_threadsafe`, and every test with a
    MagicMock `hass` got silent isolation from it for free: the mock loop
    recorded the save and never ran it. Once that obsolete hand-off was
    removed, the save ran for real — and not only in
    test_mission_timer_store.py. test_edge_cases.py drives the mission
    callbacks, which save through the same path, and failed with a
    MagicMock-against-MagicMock comparison inside HA's Store.

    So the seam was never local to one file. It belongs here.

    Async-safe on purpose: MissionTimerStore.async_load awaits
    `Store.async_load()`, and a bare MagicMock there would hand back
    something unawaitable. Tests that inject `mts._ha_store` directly
    still take precedence, because `_get_ha_store` only builds a Store
    when none is cached.
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.roomba_plus import mission_timer_store as _mts

    def _fake_store(*_args, **_kwargs):
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        store.async_save = AsyncMock()
        return store

    monkeypatch.setattr(_mts, "Store", _fake_store)


# ── Home Assistant 2026 semantics for OptionsFlow.config_entry ────────────
#
# 2025.5 (the local and minimum version) still returns `self._config_entry`
# when a test sets it -- a compatibility path marked "to be removed in
# 2025.12". 2026 removed it: `config_entry` is looked up through `handler`
# on hass, and there is no setter. Tests that only set `_config_entry` pass
# here and fail in the 2026 CI job, which is how four of them went unseen.
# Every run now resolves the entry the way 2026 does.

@pytest.fixture(autouse=True)
def _options_flow_config_entry_as_in_ha_2026(monkeypatch):
    from homeassistant import config_entries as _ce

    def _get(self):
        if self.hass is None:
            raise ValueError("The config entry is not available during initialisation")
        return self.hass.config_entries.async_get_known_entry(self._config_entry_id)

    monkeypatch.setattr(_ce.OptionsFlow, "config_entry", property(_get))
