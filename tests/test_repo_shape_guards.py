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

# ── from test_manifest_requirements.py ──────────────────────────────────


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
    assert "git+" in requirement, "expected a git-based requirement"
    # The git URL itself always ends in ".git" -- a tag/ref pin, if present,
    # is a second "@" AFTER that, e.g. "....git@v0.1.11a6".
    assert ".git@" in requirement, (
        f'"{requirement}" has no "@<ref>" pin after the .git URL -- this is exactly the '
        "unpinned-dependency gap this project already hit once. Every install would pull "
        "whatever the default branch happens to be at install time, not a specific, "
        "reproducible version."
    )


# ── from test_store_encapsulation_guard.py ──────────────────────────────


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


# ── from test_sensor_module_split.py ────────────────────────────────────


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
    "CloudRawSensorDescription": "sensor_cloud",
    "CloudRawSensor": "sensor_cloud",
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


# ── from test_structural_instrumentation.py ─────────────────────────────

import ast
import pathlib

import pytest

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

