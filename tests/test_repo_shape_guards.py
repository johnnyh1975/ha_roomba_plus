"""Guards on the SHAPE of the repository, not on what it does.

Four small files lived here separately, each holding two to five tests
about how the code is arranged rather than how it behaves:

    manifest requirements       a space in a requirement string fails
                                hassfest and rejects the integration
    store encapsulation         cross-module code reaching into a
                                store's private attributes
    sensor module split         sensor.py is a facade over five domain
                                modules and must stay one
    structural instrumentation  record_success must sit after the call
                                that can fail, or the detection reports
                                a healthy path forever

They are together because they answer one question -- is the repository
still built the way it was meant to be -- and because four files of two
tests each cost more to find than one file of eleven.

What did NOT get merged in: anything that exercises behaviour. A guard
here should be readable without knowing what a Roomba is.
"""

from __future__ import annotations

# ── formerly tests/test_manifest_requirements.py ──────────────────────────────────


import json
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent.parent / "custom_components" / "roomba_plus" / "manifest.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_no_requirement_contains_a_space() -> None:
    """Mirrors hassfest's own [REQUIREMENTS] check directly -- a space
    anywhere in a requirements entry is rejected outright, regardless
    of where it appears."""
    manifest = _load_manifest()
    for requirement in manifest["requirements"]:
        assert " " not in requirement, (
            f'Requirement "{requirement}" contains a space -- this is exactly the error '
            "hassfest raised in CI once already. See this test's own module docstring."
        )


def test_roombapy_prime_requirement_is_pinned_to_a_tag() -> None:
    """A separate, earlier real gap in this project: the roombapy-prime
    requirement was unpinned for a while (installing whatever the
    default branch happened to be at install time), and separately,
    entirely absent from requirements-test-frozen.txt. This test only
    guards the pinning half directly checkable from manifest.json --
    it asserts an "@<something>" tag reference exists after the git
    URL, not that requirements-test-frozen.txt is in sync (a plain
    text file, not something with an obvious single source of truth
    to compare against automatically)."""
    manifest = _load_manifest()
    roombapy_prime_reqs = [r for r in manifest["requirements"] if r.startswith("roombapy-prime")]
    assert len(roombapy_prime_reqs) == 1, "expected exactly one roombapy-prime requirement entry"
    requirement = roombapy_prime_reqs[0]
    # AN INDEX PIN OR A GIT TAG, both are exact. The library went to
    # PyPI, so `roombapy-prime[map]==0.3.1` is now the normal form; the
    # git spelling stays valid for installing an unpublished
    # prerelease. What must never appear is an unpinned name, which
    # would let a Home Assistant install pick up a different version
    # from the one this release was tested against.
    assert "==" in requirement or "git+" in requirement, (
        "expected an exact pin: either `==<version>` or a git tag"
    )
    # The git URL itself always ends in ".git" -- a tag/ref pin, if present,
    # is a second "@" AFTER that, e.g. "....git@v0.1.11a6".
    if "git+" in requirement:
        assert ".git@" in requirement, (
            "a git requirement must name a tag, not a branch"
        )


# ── formerly tests/test_store_encapsulation_guard.py ──────────────────────────────


import ast
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "roomba_plus"
)

# Store-private names and the single module allowed to touch each.
# obj.<name> anywhere else in production code is a violation — the
# public replacements are noted for the error message.
_GUARDED: dict[str, tuple[str, str]] = {
    "_records": ("mission_store.py", "MissionStore.records / append_validated()"),
    "_record_ids": ("mission_store.py", "MissionStore.append_validated()"),
    "_extract_rid": ("mission_store.py", "MissionStore.extract_rid()"),
    "_stuck": ("grid_store.py", "GridStore.stuck_count() / stuck_pattern()"),
    "_furniture_dismissed_at": (
        "grid_store.py",
        "GridStore.furniture_dismissed_cells() / is_furniture_dismissed()",
    ),
    "_schedule_save": ("mission_timer_store.py", "MissionTimerStore.schedule_save()"),
    "_last_phase_ts": ("mission_timer_store.py", "MissionTimerStore.last_phase_ts"),
}


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        rule = _GUARDED.get(node.attr)
        if rule is None:
            continue
        owner_file, replacement = rule
        if path.name == owner_file:
            continue  # the owning module may use its own privates
        # self.<attr> in a foreign module is that module's OWN private
        # attribute (name coincidence), not a store access.
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            continue
        found.append(
            f"{path.name}:{node.lineno}: .{node.attr} — use {replacement}"
        )
    return found


class TestStoreEncapsulationGuard:
    def test_no_cross_module_store_private_access(self):
        violations: list[str] = []
        for path in sorted(COMPONENT_DIR.glob("*.py")):
            violations.extend(_violations_in(path))
        assert not violations, (
            "Cross-module access to store-private attributes "
            "(v3.3.0 STORE-ENCAP):\n" + "\n".join(violations)
        )

    def test_guard_actually_detects(self):
        """Self-test: the scanner must flag a synthetic violation —
        guards that can never fire are the v3.2.0 dispatch-bug lesson."""
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", dir=COMPONENT_DIR.parent, delete=False
        ) as fh:
            fh.write("x = data.mission_store._records\n")
            tmp = Path(fh.name)
        try:
            hits = _violations_in(tmp)
        finally:
            tmp.unlink()
        assert len(hits) == 1 and "._records" in hits[0]


# ── formerly tests/test_sensor_module_split.py ────────────────────────────────────


import importlib

import pytest


# name -> real home module (relative to custom_components.roomba_plus)
_FACADE_CONTRACT: dict[str, str] = {
    # sensor_core — descriptor pattern core
    "RoombaSensorDescription": "sensor_core",
    "RoombaSensor": "sensor_core",
    "SENSORS": "sensor_core",
    # sensor_cloud — cloud-derived sensors + their helpers
    "CloudHistorySensorDescription": "sensor_cloud",
    "CloudHistorySensor": "sensor_cloud",
    "CLOUD_HISTORY_SENSORS": "sensor_cloud",
    "RoombaCleaningPerformanceSensor": "sensor_cloud",
    "RoombaCleaningAnalytics30dSensor": "sensor_cloud",
    "RoombaWifiHealthSensor": "sensor_cloud",
    "RoombaEventCounts30dSensor": "sensor_cloud",
    "RoombaWifiLastChannelSensor": "sensor_cloud",
    "RoombaWifiChannelStabilitySensor": "sensor_cloud",
    "RoombaMissionsPerChargeSensor": "sensor_cloud",
    "RoombaHealthScoreTrendSensor": "sensor_cloud",
    "RoombaRobotHealthSensor": "sensor_cloud",
    "_channel_to_band": "sensor_cloud",
    "_mh_sqft_to_m2": "sensor_cloud",
    "_mh_total_minutes": "sensor_cloud",
    "_mh_total_missions": "sensor_cloud",
    "_raw_cleaning_speed": "sensor_cloud",
    "_raw_cleaning_speed_trend": "sensor_cloud",
    "_raw_cloud_last_error_attrs": "sensor_cloud",
    "_raw_cloud_last_error_code": "sensor_cloud",
    "_raw_cloud_last_error_time": "sensor_cloud",
    "_raw_completion_rate": "sensor_cloud",
    "_raw_dirt_density": "sensor_cloud",
    "_raw_dirt_events": "sensor_cloud",
    "_raw_evacuations": "sensor_cloud",
    "_raw_recharge_fraction": "sensor_cloud",
    "_raw_recharges": "sensor_cloud",
    # sensor_rooms — mission/room/zone sensors + their helpers
    "RoombaMissionProgress": "sensor_rooms",
    "RoombaDirtCorrelationSensor": "sensor_rooms",
    "RoombaRoomsOverdueSensor": "sensor_rooms",
    "RoombaRoomAccessibilityScoresSensor": "sensor_rooms",
    "RoombaRoomAreasSensor": "sensor_rooms",
    "RoombaRoomCleaningHistorySensor": "sensor_rooms",
    "RoombaLastMissionSummarySensor": "sensor_rooms",
    "RoombaEdgeCoverageSensor": "sensor_rooms",
    "RoombaLearningPercentageSensor": "sensor_rooms",
    "RoombaZoneSummarySensor": "sensor_rooms",
    "RoombaRelocalisationRateSensor": "sensor_rooms",
    "_compute_room_time_estimates": "sensor_rooms",
    "_get_planned_room_order": "sensor_rooms",
    "_id_to_display_name": "sensor_rooms",
    "_region_maps_for": "sensor_rooms",
    "_resolve_smart_tier_room_state": "sensor_rooms",
    # sensor_diagnostics — always-created diagnostic/meta sensors
    "RawStateSensor": "sensor_diagnostics",
    "RoombaFirmwareVersionSensor": "sensor_diagnostics",
    "RoombaIntegrationHealthSensor": "sensor_diagnostics",
    "RoombaOptimalCleanWindow": "sensor_diagnostics",
    "RoombaResetDiagnosticsSensor": "sensor_diagnostics",
    # sensor_helpers — descriptor value-functions
    "_area_cleaned_today": "sensor_helpers",
    "_battery_age_days": "sensor_helpers",
    "_battery_capacity_retention": "sensor_helpers",
    "_completion_rate_30d": "sensor_helpers",
    "_compute_integration_health": "sensor_helpers",
    "_estimated_battery_eol": "sensor_helpers",
    "_expire_minutes_remaining": "sensor_helpers",
    "_health_band": "sensor_helpers",
    "_integration_health_plain_status": "sensor_helpers",
    "_last_error_code_value": "sensor_helpers",
    "_last_mission_team_id": "sensor_helpers",
    "_mission_elapsed_value": "sensor_helpers",
    "_mission_store_last_started_at": "sensor_helpers",
    "_mission_store_value": "sensor_helpers",
    "_mop_behavior": "sensor_helpers",
    "_mop_clean_mode": "sensor_helpers",
    "_mop_tank_status": "sensor_helpers",
    "_next_likely_clean_window": "sensor_helpers",
    "_parse_netinfo_addr": "sensor_helpers",
    "_phase_value": "sensor_helpers",
    "_presence_opportunities": "sensor_helpers",
    "_presence_utilisation": "sensor_helpers",
    "_problem_zone_value": "sensor_helpers",
    "_raw_wifi_floor": "sensor_helpers",
    "_raw_wifi_quality_pct": "sensor_helpers",
    "_raw_wifi_stability": "sensor_helpers",
    "_recharge_minutes_remaining": "sensor_helpers",
    "_robot_health_plain_status": "sensor_helpers",
    "_total_energy_consumed_kwh": "sensor_helpers",
    "_ts_or_none": "sensor_helpers",
}


@pytest.mark.parametrize("name,real_module", sorted(_FACADE_CONTRACT.items()))
def test_facade_reexport_matches_real_module(name: str, real_module: str) -> None:
    """Every pre-split import path still resolves, to the identical object."""
    facade = importlib.import_module("custom_components.roomba_plus.sensor")
    home = importlib.import_module(f"custom_components.roomba_plus.{real_module}")

    assert hasattr(facade, name), (
        f"'{name}' is no longer importable from the sensor.py facade — "
        f"a re-export was likely dropped when moving it into {real_module}.py"
    )
    assert hasattr(home, name), (
        f"'{name}' is not defined in its expected home module "
        f"custom_components.roomba_plus.{real_module}"
    )

    facade_obj = getattr(facade, name)
    home_obj = getattr(home, name)
    assert facade_obj is home_obj, (
        f"'{name}' resolves to different objects via the facade vs. "
        f"{real_module} — this means it was duplicated instead of "
        f"re-exported, which silently breaks mock.patch(...) call sites "
        f"that target one path but not the other."
    )


def test_facade_contract_is_exhaustive_for_known_consumers() -> None:
    """Sanity check: every name this suite (and callbacks.py/device_tracker.py/
    repairs.py/services.py) is known to import from `.sensor` is covered by
    the contract above. If this fails after adding a new cross-module
    import, add the name (and its real home module) to _FACADE_CONTRACT.
    """
    known_extra_passthroughs = {"SensorDeviceClass", "SensorStateClass"}
    facade = importlib.import_module("custom_components.roomba_plus.sensor")
    for name in known_extra_passthroughs:
        assert hasattr(facade, name), f"expected HA passthrough '{name}' missing from facade"


# ── formerly tests/test_structural_instrumentation.py ─────────────────────────────

import ast
import pathlib

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from custom_components.roomba_plus import switch as sw
import datetime as _dt
from custom_components.roomba_plus import sensor_helpers as sh

_SRC = pathlib.Path("custom_components/roomba_plus")


def _modules():
    for path in sorted(_SRC.glob("*.py")):
        text = path.read_text()
        if "record_success" in text or "record_failure" in text:
            yield path, ast.parse(text)


def _calls_in(node, name):
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    ]


class TestSuccessIsRecordedAfterTheRiskyPart:
    def test_no_success_before_an_await_in_the_same_try(self):
        """The trap, stated precisely: inside a `try` that guards an
        `await`, a `record_success` on an earlier line has already fired
        by the time the await raises."""
        offenders = []
        for path, tree in _modules():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                successes = _calls_in(ast.Module(body=node.body, type_ignores=[]),
                                      "record_success")
                awaits = [n for n in ast.walk(
                    ast.Module(body=node.body, type_ignores=[])
                ) if isinstance(n, ast.Await)]
                if not successes or not awaits:
                    continue
                first_await = min(a.lineno for a in awaits)
                early = [s for s in successes if s.lineno < first_await]
                offenders.extend(
                    f"{path.name}:{s.lineno}" for s in early
                )

        assert not offenders, (
            "record_success runs before the await that can fail: "
            f"{offenders}"
        )


class TestEverySiteRecordsBothOutcomes:
    """Recording only failures reports a healthy path as broken after
    two slow cloud calls. Recording only successes is worse -- it means
    nothing is ever reported."""

    def _sites(self, func):
        found: dict[str, set[str]] = {}
        for path, tree in _modules():
            for call in _calls_in(tree, func):
                if call.args and isinstance(call.args[0], ast.Constant):
                    found.setdefault(str(call.args[0].value), set()).add(path.name)
        return found

    def test_no_site_only_records_failure(self):
        """A site whose success is never recorded escalates as soon as
        two consecutive attempts fail, however healthy it is."""
        failures = self._sites("record_failure")
        successes = self._sites("record_success")

        missing = sorted(set(failures) - set(successes))
        assert not missing, (
            "these sites record a failure and never a success, so a "
            f"transient outage would be reported as a defect: {missing}"
        )

    def test_no_site_only_records_success(self):
        successes = self._sites("record_success")
        failures = self._sites("record_failure")

        missing = sorted(set(successes) - set(failures))
        assert not missing, f"these sites can never report anything: {missing}"


class TestSiteNamesAreDistinct:
    def test_a_site_name_is_used_in_one_place_only(self):
        """Two code paths sharing a name means one path's success masks
        the other's failure -- silently, which is the failure mode this
        whole mechanism exists to end."""
        from collections import defaultdict

        seen = defaultdict(set)
        for path, tree in _modules():
            for func in ("record_failure", "record_success"):
                for call in _calls_in(tree, func):
                    if call.args and isinstance(call.args[0], ast.Constant):
                        seen[str(call.args[0].value)].add(path.name)

        shared = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
        assert not shared, f"site names used across modules: {shared}"


@pytest.mark.parametrize("skipped_reason", ["NOT INSTRUMENTED"])
class TestSkipsCarryTheirReason:
    def test_every_skip_says_why(self, skipped_reason):
        """A path left uninstrumented is a decision, and the next person
        reading it should find the reason rather than assume an
        oversight."""
        bare = []
        for path in _SRC.glob("*.py"):
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                if skipped_reason not in line:
                    continue
                window = "\n".join(lines[i:i + 6])
                if len(window.split()) < 12:
                    bare.append(f"{path.name}:{i + 1}")

        assert not bare, f"skips without a stated reason: {bare}"



# ── device removal hook ──────────────────────────────────────────────────

class TestHomeAssistantCanFindOurHooks:
    """A hook Home Assistant looks up by name is only reachable if the
    name matches exactly, and a mismatch fails the way everything else
    has failed this week: nothing errors, the hook simply never runs.

    `async_remove_config_entry_device` was written as `...devices`, and
    took a list where Home Assistant passes one entry. So Home Assistant
    found no hook and refused every device removal with "Failed to
    remove device entry, rejected by integration" -- with nothing in our
    logs, because our code was never reached.

    Anyone who replaced a robot and tried to delete the old device from
    the UI hit that refusal.
    """

    def test_the_removal_hook_has_the_name_home_assistant_looks_for(self):
        import custom_components.roomba_plus as init

        assert hasattr(init, "async_remove_config_entry_device")
        assert not hasattr(init, "async_remove_config_entry_devices")

    def test_it_takes_one_device_entry_not_a_list(self):
        """Home Assistant calls it with `(hass, config_entry,
        device_entry)`. A parameter named for a list is a sign the
        signature was written from a guess."""
        import inspect

        import custom_components.roomba_plus as init

        params = list(
            inspect.signature(init.async_remove_config_entry_device).parameters
        )
        assert params == ["hass", "config_entry", "device_entry"]

    @pytest.mark.asyncio
    async def test_the_current_robot_is_protected_a_stale_device_is_not(self, hass):
        """Home Assistant offers "Delete" on EVERY device of the entry once
        this hook exists, not only on devices without entities. The robot
        still configured must be refused; a replaced one may go."""
        from homeassistant.helpers import device_registry as dr
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        import custom_components.roomba_plus as init
        from custom_components.roomba_plus.const import DOMAIN

        entry = MockConfigEntry(domain=DOMAIN, data={"blid": "CURRENT"})
        entry.add_to_hass(hass)
        reg = dr.async_get(hass)
        current = reg.async_get_or_create(config_entry_id=entry.entry_id,
                                          identifiers={(DOMAIN, "roomba_plus_CURRENT")})
        replaced = reg.async_get_or_create(config_entry_id=entry.entry_id,
                                           identifiers={(DOMAIN, "roomba_plus_OLDROBOT")})
        assert await init.async_remove_config_entry_device(hass, entry, current) is False
        assert await init.async_remove_config_entry_device(hass, entry, replaced) is True

    @pytest.mark.asyncio
    async def test_an_entry_without_a_blid_protects_nothing(self, hass):
        """No current robot to identify: every device of the entry is stale."""
        from types import SimpleNamespace

        from homeassistant.helpers import device_registry as dr
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        import custom_components.roomba_plus as init
        from custom_components.roomba_plus.const import DOMAIN

        entry = MockConfigEntry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        entry.runtime_data = SimpleNamespace(blid=None)
        dev = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
                                                     identifiers={(DOMAIN, "roomba_plus_ANY")})
        assert await init.async_remove_config_entry_device(hass, entry, dev) is True


# ── formerly tests/test_coverage_mid_gaps_4.py ──────────────────────────────────
#
# Coverage gaps, fourth batch — quality scale, test-coverage.
#
# switch.py: the Classic setting switches only react to their own key, and
# the Prime switches read cloud shadows that may be absent. A switch whose
# source is missing shows unknown, never a guessed on/off.

class TestTimestampConversionsCatchOverflow:
    """`datetime.fromtimestamp` raises OverflowError for values beyond the
    platform's time_t — a corrupt timestamp from the robot or the cloud.
    Five conversions caught TypeError/ValueError/OSError and let this one
    through, so a bad value crashed the sensor update instead of reading
    as unknown. Found by the coverage work for the quality scale."""

    def test_the_helper_survives_an_overflow(self):
        assert sh._ts_or_none(10**20) is None

    def test_every_guarded_conversion_catches_overflow(self):
        import ast
        import pathlib

        luecken = []
        for f in sorted(pathlib.Path("custom_components/roomba_plus").glob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
                if "fromtimestamp" not in body and "utc_from_timestamp" not in body:
                    continue
                for h in node.handlers:
                    names = ast.unparse(h.type) if h.type else "BaseException"
                    if "Exception" in names or "OverflowError" in names:
                        continue
                    luecken.append(f"{f.name}:{h.lineno} except {names}")
        assert not luecken, f"timestamp conversions that let OverflowError through: {luecken}"


# ── formerly tests/test_guard_empty_string_is_none.py ───────────────────────────
#
# Guard: a variable set to "" must not then be tested with `is None`.
#
# The v20 → v21 migration set `new_eid = ""` and checked `new_eid is None`
# three times. None of the checks ever held; every ordinary entity reached
# `async_update_entity(eid, new_entity_id="")`, Home Assistant raised, and
# the migration aborted for anyone on schema 20 or older. The likely origin:
# a type-checker complaint about reusing a `str` name, silenced with "".
#
# Checked per function across the whole package.

PKG = pathlib.Path("custom_components/roomba_plus")


def _findings(source: str, filename: str) -> list[str]:
    tree = ast.parse(source)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        empty = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value == "":
                empty |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            if (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                    and isinstance(n.value, ast.Constant) and n.value.value == ""):
                empty.add(n.target.id)
        for n in ast.walk(fn):
            if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id in empty
                    and any(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops)
                    and any(isinstance(c, ast.Constant) and c.value is None for c in n.comparators)):
                out.append(f"{filename}:{n.lineno} {fn.name}: '{n.left.id}' is set to \"\" "
                           f"but tested with `is None`")
    return out


def test_no_empty_string_is_tested_with_is_none():
    findings = []
    for f in sorted(PKG.glob("*.py")):
        findings += _findings(f.read_text(encoding="utf-8"), f.name)
    assert not findings, findings


def test_the_guard_catches_the_original_fault():
    fault = (
        "def step(eids):\n"
        "    for eid in eids:\n"
        "        new_eid = ''\n"
        "        if new_eid is None and eid.endswith('_battery'):\n"
        "            new_eid = eid + '_level'\n"
    )
    assert _findings(fault, "fault.py")


# ── the package is imported as the package, never as __init__ ───────────────
#
# ``from custom_components.roomba_plus import __init__ as module`` does not
# return the package: without a prior import it returns the package's bound
# ``__init__`` method, and ``custom_components.roomba_plus.__init__`` as a
# dotted name loads a SECOND copy of __init__.py as its own module. Three
# test files depended on another file having loaded that copy first, so they
# failed when run alone, and a diagnostics patch landed on the copy instead
# of the code under test and patched nothing.

import re

_INIT_IMPORT = re.compile(r"roomba_plus\.__init__\b|import\s+__init__\b")


def _init_import_findings(source: str, filename: str) -> list[str]:
    return [
        f"{filename}:{no}: {line.strip()}"
        for no, line in enumerate(source.splitlines(), start=1)
        if _INIT_IMPORT.search(line) and not line.lstrip().startswith("#")
    ]


def test_no_test_imports_the_package_through_init() -> None:
    findings: list[str] = []
    for f in sorted(Path(__file__).parent.glob("*.py")):
        if f.name == Path(__file__).name:
            continue
        findings += _init_import_findings(f.read_text(encoding="utf-8"), f.name)
    assert not findings, findings


def test_the_init_import_guard_catches_the_original_faults() -> None:
    for fault in (
        "        from custom_components.roomba_plus import __init__ as module\n",
        "    from custom_components.roomba_plus.__init__ import _async_seed\n",
        '        "custom_components.roomba_plus.__init__.roomba_reported_state",\n',
    ):
        assert _init_import_findings(fault, "fault.py"), fault


# ── 4.2.13: every HA-scheduled callable runs on the event loop ───────────────
#
# Home Assistant decides WHERE to run a timer, event or dispatcher target
# from the callable itself: a coroutine function or a `@callback` runs on
# the event loop, anything else in the thread pool.
#
# The stuck-end recheck was a plain function handed to
# async_track_time_interval. It ran the whole mission state machine on a
# worker thread, and since 4.2.11 -- which dropped the thread bridges once
# MQTT arrived on the loop -- `entry.async_create_task` from there raised
# "is not the running loop" and the mission was never recorded (two
# missions on one i7+ in a day). The same shape sat in the Prime region
# sensors, calling async_add_entities from the pool.

import ast as _ast

_SCHEDULERS = {
    # name: position of the callable among the positional arguments
    "async_track_time_interval": 1,
    "async_call_later": 2,
    "async_track_time_change": 1,
    "async_track_utc_time_change": 1,
    "async_track_point_in_time": 1,
    "async_track_point_in_utc_time": 1,
    "async_track_state_change_event": 2,
    "async_dispatcher_connect": 2,
    "async_listen": 1,
    "async_listen_once": 1,
}

# Targets the AST cannot follow. Each is checked at runtime instead, in
# the test named next to it.
_FOLLOWED_AT_RUNTIME = {
    "_mission_cb.recheck_stuck_end_state":
        "test_the_stuck_end_recheck_runs_on_the_loop",
}


def _on_the_loop(fn: _ast.AST) -> bool:
    if isinstance(fn, _ast.AsyncFunctionDef):
        return True
    return isinstance(fn, _ast.FunctionDef) and any(
        (isinstance(d, _ast.Name) and d.id == "callback")
        or (isinstance(d, _ast.Attribute) and d.attr == "callback")
        for d in fn.decorator_list
    )


def off_loop_targets(source: str, filename: str) -> list[str]:
    """Scheduler calls whose target would run in the thread pool."""
    tree = _ast.parse(source, filename)
    defs: dict[str, list[_ast.AST]] = {}
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            defs.setdefault(node.name, []).append(node)
    problems: list[str] = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, _ast.Name) else getattr(func, "attr", "")
        position = _SCHEDULERS.get(name)
        if position is None or len(node.args) <= position:
            continue
        target = node.args[position]
        where = f"{filename}:{node.lineno} {name}"
        if (
            isinstance(target, _ast.Call)
            and isinstance(target.func, _ast.Name)
            and target.func.id == "callback"
        ):
            continue
        if isinstance(target, _ast.Lambda):
            problems.append(f"{where}: a lambda runs in the thread pool")
            continue
        if isinstance(target, _ast.Name) or (
            isinstance(target, _ast.Attribute)
            and isinstance(target.value, _ast.Name)
            and target.value.id == "self"
        ):
            target_name = target.id if isinstance(target, _ast.Name) else target.attr
            found = defs.get(target_name)
            if found and all(_on_the_loop(d) for d in found):
                continue
            if found:
                problems.append(f"{where}: {target_name} is neither async nor @callback")
                continue
            # Not defined in this module (an HA method such as
            # async_write_ha_state): HA's own methods are callbacks.
            if target_name.startswith("async_"):
                continue
            problems.append(f"{where}: {target_name} cannot be followed")
            continue
        text = _ast.unparse(target)
        if text not in _FOLLOWED_AT_RUNTIME:
            problems.append(f"{where}: {text} cannot be followed")
    return problems


def test_every_scheduled_target_runs_on_the_event_loop() -> None:
    root = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
    problems: list[str] = []
    for path in sorted(root.glob("*.py")):
        problems += off_loop_targets(path.read_text(encoding="utf-8"), path.name)
    assert problems == []


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    ("source", "found"),
    [
        ("def f(n): pass\nasync_track_time_interval(hass, f, t)", True),
        ("@callback\ndef f(n): pass\nasync_track_time_interval(hass, f, t)", False),
        ("async def f(n): pass\nasync_call_later(hass, 5, f)", False),
        ("async_track_time_interval(hass, lambda _: x(), t)", True),
        ("async_track_time_interval(hass, callback(lambda _: x()), t)", False),
        ("def g(): pass\nasync_dispatcher_connect(hass, SIG, g)", True),
        ("class A:\n def h(self, e): pass\n def s(self): self.hass.bus.async_listen('e', self.h)", True),
        ("async_track_time_interval(hass, cb.attr, t)", True),
        ("async_dispatcher_connect(hass, SIG, self.async_write_ha_state)", False),
    ],
)
def test_the_loop_guard_can_fail(source: str, found: bool) -> None:
    """Counter-check: each shape the guard exists for."""
    assert bool(off_loop_targets(source, "example.py")) is found


def test_every_runtime_followed_target_has_its_test() -> None:
    """An allowlist entry names the test that stands in for the guard;
    a renamed or deleted test would leave the target unchecked."""
    tests = Path(__file__).parent
    text = "".join(p.read_text(encoding="utf-8") for p in tests.glob("test_*.py"))
    for test_name in _FOLLOWED_AT_RUNTIME.values():
        assert f"def {test_name}(" in text, test_name


# ── the parse cache in conftest.py (4.3.0b2) ─────────────────────────────────


def test_the_parse_cache_is_scoped_to_our_own_calls() -> None:
    """Our calls share a tree; a call from anywhere else gets its own.
    pytest and coverage.py parse sources too, and pytest's assertion
    rewriter changes its tree in place."""
    import ast

    source = "x = 1  # the parse cache is scoped\n"
    assert ast.parse(source) is ast.parse(source)
    elsewhere = {"__name__": "elsewhere"}
    exec(compile("import ast\ndef p(s): return ast.parse(s)", "/elsewhere/mod.py", "exec"), elsewhere)
    assert elsewhere["p"](source) is not elsewhere["p"](source)


@_pytest.mark.parametrize(
    "change",
    [
        "tree.body[0].targets[0].id = 'y'",               # a field
        "tree.body[0].lineno = 99",                        # a position
        "tree.body[0].value.parent = tree.body[0]",        # an added attribute
        "tree.body.append(ast.Pass())",                    # a list changed in place
    ],
)
def test_a_changed_cached_tree_is_found(change: str) -> None:
    """Counter-check for the session check in conftest.py. The tree is
    taken out of the cache afterwards, or the check would fail the run
    it is part of."""
    import ast
    import uuid

    from tests import conftest

    source = f"x = 1  # {uuid.uuid4()}\n"
    tree = ast.parse(source)
    try:
        assert conftest._changed_cached_trees([source]) == []
        exec(change, {"tree": tree, "ast": ast})
        assert conftest._changed_cached_trees([source]) == [source.strip()[:80]]
    finally:
        del conftest._PARSED_TREES[source]
        del conftest._FINGERPRINTS[source]
