"""Pytest configuration for Roomba+ tests.

Applies compatibility shims for HA API differences between test environments
and ensures the integration is importable regardless of installed HA version.
"""


def pytest_configure(config):
    """Apply HA version compatibility shims before any test module is imported.

    Shim 1 (existing): AddConfigEntryEntitiesCallback — HA 2024.4.
    Shim 2 (v2.4.0): Segment dataclass — HA 2026.3 vacuum.clean_area.
    Shim 3 (v2.4.0): VacuumEntityFeature.CLEAN_AREA — HA 2026.3.

    When to remove shims 2 & 3: once CI is pinned to pytest-homeassistant-
    custom-component built against HA 2026.3+.
    """
    import dataclasses
    import enum
    import importlib

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


import pytest
from unittest.mock import patch as _patch


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
