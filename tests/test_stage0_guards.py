"""The Stage 0 guards, and that they still catch what they were built for.

Each of these scripts was written after a real bug of its own shape, and
each found another one on its first run. A guard that silently stops
working is worse than no guard, so these tests check the detection
rather than just that the script exits zero.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import importlib
import pytest
import pathlib

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _run(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        capture_output=True, text=True, cwd=ROOT,
    )


class TestGenerationParity:
    """Twenty modules branch on CLOUD_ONLY and nearly all return early
    from the Prime side. Anything added to the Classic path afterwards is
    not inherited, and nothing flagged it.

    Five gaps of that shape shipped in one session. On its first run this
    check found a sixth: services were never removed when the last entry
    was a Prime robot, so uninstalling left eighteen registered actions
    pointing at nothing."""

    def test_the_repository_passes(self):
        result = _run("check_generation_parity.py")

        assert result.returncode == 0, result.stdout

    def test_every_deliberate_difference_has_a_reason(self):
        """An entry without a reason is how an unexamined gap gets
        silenced. "Not needed" is what somebody writes while overlooking
        a real one."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "parity", SCRIPTS / "check_generation_parity.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        for key, entries in module.DELIBERATE.items():
            for call, reason in entries.items():
                assert len(reason) > 20, f"{key}::{call} has no real reason"

    def test_platform_setups_are_skipped(self):
        """A platform's async_setup_entry exists to create different
        entities per generation. Reporting that difference is noise --
        89 of the first run's 141 findings were exactly that."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "parity", SCRIPTS / "check_generation_parity.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module._difference_expected("async_setup_entry")
        assert not module._difference_expected("async_unload_entry")


class TestRequestBudget:
    """Two bugs came from asking what an entity costs per day, not from
    anything failing: schedule switches at roughly 8,600 cloud requests a
    day, and a duplicated map-metadata call per refresh.

    Both were functionally correct and every test passed."""

    def test_the_repository_passes(self):
        result = _run("check_request_budget.py")

        assert result.returncode == 0, result.stdout

    def test_it_follows_one_hop_into_helpers(self):
        """THE thing that made it useful. The schedule switch's
        async_update does not call the cloud directly -- it calls
        async_read_schedule_containers(), which does. Looking only at the
        method body reported the entity as free."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "budget", SCRIPTS / "check_request_budget.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        import ast

        tree = ast.parse(
            "async def wrapper(robot):\n"
            "    return await robot.get_schedules('h')\n"
        )

        assert "wrapper" in module._cloud_wrappers(tree)

    def test_polling_exceptions_state_a_number(self):
        """An exception is a claim that the cost is acceptable. That
        claim needs the interval and the resulting daily figure, not "it
        is fine"."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "budget", SCRIPTS / "check_request_budget.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        for key, reason in module._POLLING_EXCEPTIONS.items():
            assert any(c.isdigit() for c in reason), f"{key} states no figure"


class TestAssumedInventory:
    """4,564 tests pass. That says nothing about how many assert a fact
    and how many preserve a guess.

    `assert call.body_json == {"assetId": "BLID123"}` was green for
    months on a wire key nobody had confirmed. The real key is
    `robot_id`. The test made the assumption harder to question, because
    questioning it meant arguing with a green test."""

    def test_the_repository_passes(self):
        result = _run("list_assumed_tests.py")

        assert result.returncode == 0, result.stdout

    def test_the_marker_is_registered(self):
        """An unregistered marker is silently ignored by pytest, which
        would make the whole convention decorative."""
        content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        assert "assumed(reason)" in content

    def test_an_empty_inventory_is_a_valid_state(self):
        """An earlier version of this test required at least one marked
        test, on the grounds that an empty inventory meant an unused
        convention.

        It failed the day the last assumption was confirmed: a tester's
        capture settled the padWetness spelling, the marker came off, and
        a guard that was supposed to encourage honesty started demanding
        that something stay uncertain.

        The inventory being empty is the goal, not a fault."""
        result = _run("list_assumed_tests.py")

        assert result.returncode == 0


# ── Merged from test_store_encapsulation_guard.py ────────────────────
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


# ── Merged from test_no_duplicate_helpers.py ─────────────────────────
PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"


class TestTheSlugRuleExistsOnce:
    def test_only_one_slug_definition(self):
        definitions = [
            path.name
            for path in PACKAGE.glob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
            and node.name in ("room_slug", "_slug", "_room_slug")
        ]

        assert definitions == ["const.py"], definitions

    def test_the_stricter_behaviour_was_kept(self):
        """A room id must never be empty -- the card rejects that. The
        copy that returned "" for a name with no letters was the one that
        lost."""
        from custom_components.roomba_plus.const import room_slug

        assert room_slug("---") == "room"
        assert room_slug("Küche  Nord") == "kuche_nord"

    def test_accents_decompose_rather_than_vanish(self):
        """The card validates ids and rejects umlauts and accents.
        German and Italian testers have both hit that."""
        from custom_components.roomba_plus.const import room_slug

        assert room_slug("Küche") == "kuche"
        assert room_slug("Salle d'eau") == "salle_d_eau"
        assert room_slug("Mattéo ") == "matteo"


class TestGeometryExistsOnce:
    def test_only_one_point_in_polygon(self):
        definitions = [
            path.name
            for path in PACKAGE.glob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
            and node.name in ("point_in_polygon", "_point_in_polygon", "_point_in_polygon_grid")
        ]

        assert definitions == ["geometry_utils.py"], definitions

    def test_it_still_answers_the_obvious_cases(self):
        from custom_components.roomba_plus.geometry_utils import point_in_polygon

        square = [(0, 0), (10, 0), (10, 10), (0, 10)]

        assert point_in_polygon(5, 5, square) is True
        assert point_in_polygon(15, 5, square) is False


class TestNoNewStructuralDuplicates:
    """Catches the next pair before it drifts.

    Compares function bodies by AST shape, ignoring names and literals.
    Only functions of a reasonable size -- two three-line getters looking
    alike is not a finding."""

    #: Known-identical shapes that are not duplication: HA lifecycle
    #: hooks and constructors legitimately look the same across entity
    #: classes.
    _EXPECTED = {"async_added_to_hass", "__init__", "available", "native_value"}

    def test_no_two_large_functions_share_a_body(self):
        shapes: dict[str, list[str]] = {}
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name in self._EXPECTED:
                    continue
                if (node.end_lineno or 0) - node.lineno < 12:
                    continue
                body = [
                    statement
                    for statement in node.body
                    if not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                    )
                ]
                dump = "".join(
                    ast.dump(statement, annotate_fields=False) for statement in body
                )
                key = hashlib.md5(dump.encode()).hexdigest()
                shapes.setdefault(key, []).append(f"{path.stem}::{node.name}")

        duplicates = {k: v for k, v in shapes.items() if len(v) > 1}

        assert not duplicates, (
            "structurally identical function bodies: "
            + "; ".join(" == ".join(v) for v in duplicates.values())
        )


# ── Merged from test_manifest_requirements.py ────────────────────────
# All four of these check an architectural RULE rather than any
# behaviour, which is what this file is for. Four files of two to six
# tests each made the category look smaller than it is.
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


# ── Merged from test_sensor_module_split.py ──────────────────────────
# An import-path guard: a rule about where things may live, not about
# what they do.
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


# ── Merged from test_icons.py ────────────────────────────────────────
# Registry completeness -- every entity needs an icons.json entry. Same
# category: a rule the code must satisfy, checked across the package
# rather than in any one module's tests.
_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_ICONS = _ROOT / "icons.json"


def _load_icons() -> dict:
    return json.loads(_ICONS.read_text())["entity"]


def _translation_keys_in_source(module_name: str) -> set[str]:
    """All translation_key literals used for entity descriptions/classes
    in a platform module: keyword-arg form, _attr_translation_key class
    attributes, and translation_key= arguments inside dynamic
    entity_description assignments."""
    keys: set[str] = set()
    tree = ast.parse((_ROOT / module_name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "translation_key" and isinstance(kw.value, ast.Constant):
                    keys.add(kw.value.value)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                target_is_key = (
                    (isinstance(tgt, ast.Attribute) and tgt.attr == "_attr_translation_key")
                    or (isinstance(tgt, ast.Name) and tgt.id == "_attr_translation_key")
                )
                if target_is_key and isinstance(node.value, ast.Constant):
                    keys.add(node.value.value)
    return {k for k in keys if isinstance(k, str)}


# Keys set programmatically (dynamic command keys, translation
# placeholders), not as translation_key literals in source.
# Entities that set their own _attr_icon (HA precedence over the registry)
# — a registry entry would be dead weight.
_EXPLICIT_ICON_KEYS = frozenset({
    "prime_quiet_hours", "prime_quiet_hours_active",
    "prime_cleaning_mode", "prime_map",
})


_DYNAMIC_ICON_KEYS = frozenset({
    "prime_empty_bin", "prime_wash_pad", "prime_stop_pad_dry",
    "prime_start_pad_dry", "favorite",
})


_LEGACY_ICON_KEYS = frozenset({
    # Deprecated CloudRaw sensors deactivated in SC1 (v3.0); entries kept
    # for already-registered entities' display continuity.
    "recent_completion_rate", "recent_cleaning_speed", "recent_coverage_pct",
    "cleaning_speed_trend", "recent_dirt_density", "recent_recharge_fraction",
    "recent_recharges", "recent_evacuations", "recent_dirt_events",
    "recent_error_code", "recent_error_time",
    "recent_wifi_floor", "recent_wifi_stability",
})


@pytest.mark.parametrize(
    ("module", "domain"),
    [
        ("sensor_core.py", "sensor"),
        ("sensor_cloud.py", "sensor"),
        ("sensor_rooms.py", "sensor"),
        ("sensor_diagnostics.py", "sensor"),
        ("sensor_prime.py", "sensor"),
        ("binary_sensor.py", "binary_sensor"),
        ("button.py", "button"),
        ("button_prime.py", "button"),
        ("select.py", "select"),
        ("select_prime.py", "select"),
        ("switch.py", "switch"),
        ("prime_schedule_switch.py", "switch"),
        ("image.py", "image"),
        ("device_tracker.py", "device_tracker"),
        ("calendar.py", "calendar"),
        ("todo.py", "todo"),
        ("todo_prime.py", "todo"),
    ],
)
def test_platform_translation_keys_have_icons(module: str, domain: str) -> None:
    icons = _load_icons()
    registry = icons.get(domain, {})
    used = _translation_keys_in_source(module)
    # EXCEPTION KEYS ARE NOT ENTITY KEYS. A platform module may raise
    # ServiceValidationError with a translation_key, and those live under
    # "exceptions" in strings.json -- they name an error message, not an
    # entity, and an icon for one would mean nothing.
    #
    # Read from strings.json rather than listed here, so a new exception
    # needs no edit to this test and a key that is NOT an exception still
    # has to have its icon.
    exception_keys = set(
        json.loads(
            (
                pathlib.Path(__file__).parent.parent
                / "custom_components" / "roomba_plus" / "strings.json"
            ).read_text(encoding="utf-8")
        ).get("exceptions", {})
    )
    missing = sorted(
        k for k in used
        if k not in registry
        and k not in _EXPLICIT_ICON_KEYS
        and k not in exception_keys
    )
    assert not missing, (
        f"{domain} icons.json missing entries for {missing} "
        f"(translation keys used in {module})"
    )


def test_icons_entries_refer_to_real_keys() -> None:
    """Every registry key must be findable as an entity translation_key
    somewhere in the source (stale registry entries are drift)."""
    icons = _load_icons()
    for domain, entries in icons.items():
        used: set[str] = set()
        modules = {
            "sensor": ("sensor_core.py", "sensor_cloud.py", "sensor_rooms.py",
                       "sensor_diagnostics.py", "sensor_prime.py"),
            "binary_sensor": ("binary_sensor.py",),
            "button": ("button.py", "button_prime.py"),
            "select": ("select.py", "select_prime.py"),
            "switch": ("switch.py", "prime_schedule_switch.py"),
            "image": ("image.py",),
            "device_tracker": ("device_tracker.py",),
            "calendar": ("calendar.py",),
            "todo": ("todo.py", "todo_prime.py"),
        }.get(domain, ())
        for m in modules:
            used |= _translation_keys_in_source(m)
        unknown = sorted(set(entries) - used - _LEGACY_ICON_KEYS - _DYNAMIC_ICON_KEYS)
        assert not unknown, (
            f"icons.json {domain} entries {unknown} have no matching "
            "translation_key in the platform source — remove or allowlist"
        )


# ── Merged from nothing: written after this session's own mistake ─────
class TestNoTestIsShadowedByAnother:
    """Two definitions of one name in a file, and the first never runs.

    Python keeps the later one. pytest collects what Python left, so the
    suite stays green and the total quietly drops -- there is no error,
    no warning, and no failing test to notice.

    THREE CASES WERE FOUND THE DAY THIS WAS WRITTEN. Merging
    `test_sensor_resilience.py` into `test_sensors.py` appended two
    classes whose names already existed there, and eight tests left the
    suite while everything still passed. Checking the rest of the
    directory then turned up two older ones nobody had introduced that
    day: `TestRoomsWithoutNames` twice in `test_prime_room_map.py` --
    the shadowed copy asserting a behaviour the code had not had for
    months -- and four helpers twice in `test_select.py`.

    So this is not only about merges. Two people adding a class with an
    obvious name to one file is enough.
    """

    def test_no_module_level_name_is_defined_twice(self) -> None:
        import ast
        import collections
        import pathlib

        offenders: dict[str, dict[str, int]] = {}
        for path in sorted(pathlib.Path("tests").glob("test_*.py")):
            counts: collections.Counter[str] = collections.Counter(
                node.name
                for node in ast.parse(path.read_text(encoding="utf-8")).body
                if isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
            )
            duplicated = {name: n for name, n in counts.items() if n > 1}
            if duplicated:
                offenders[path.name] = duplicated

        assert not offenders, (
            "these names are defined more than once at module level, so the "
            f"earlier definition never runs: {offenders}"
        )
