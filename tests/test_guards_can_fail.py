"""Each guard must go RED when its subject breaks.

Nineteen guard scripts run in CI. Eleven had a test; eight did not, so
nothing verified they could still fail. That matters more than it
sounds: a guard is written against a defect that existed once, and
nothing stops it drifting out of alignment with the code it watches. A
guard that quietly stopped matching looks exactly like a guard that
keeps finding nothing.

This review produced a live example of the same failure at the test
layer. `test_the_jump_covers_every_early_version` asserted that the
string `"3 <= current < 10"` appears in the migration source. It did —
inside a branch that versions 4 to 9 never entered, so entries on those
versions matched nothing, never advanced, and Home Assistant refused to
load them. The assertion was green throughout.

METHOD. Every guard resolves its subject through a module-level path
constant (`COMPONENT`, `ROOT`, `MANIFEST`, `BASELINE`). Each test below
points that constant at a temporary tree containing a deliberate
defect, then asserts `main()` returns non-zero. No test mutates the real
repository.

Two deliberate omissions: the guards are NOT asserted to return 0
against the real tree here — CI already runs them for that, and
duplicating it would make this file fail for reasons that have nothing
to do with whether a guard can fail.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import pathlib

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    """Import a guard script as a module, without executing main()."""
    spec = importlib.util.spec_from_file_location(
        f"_guard_{name}", _SCRIPTS / f"{name}.py"
    )
    assert spec and spec.loader, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestClientAttributeGuard:
    """An attribute read off the robot client that roombapy lacks.

    The real incident: `roomba_connected` was renamed to `connected`
    upstream and mypy saw nothing, because the client is typed `Any` on
    the entity base.
    """

    def test_it_reports_an_attribute_the_client_does_not_have(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_client_attributes")
        (tmp_path / "fake_entity.py").write_text(
            "def read(self):\n"
            "    return self.roomba.definitely_not_a_roombapy_attribute\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "COMPONENT", tmp_path)

        assert guard.main() == 1

    def test_it_passes_on_a_real_attribute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: a guard that always fails is equally useless."""
        guard = _load("check_client_attributes")
        (tmp_path / "fake_entity.py").write_text(
            "def read(self):\n    return self.roomba.connected\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "COMPONENT", tmp_path)

        assert guard.main() == 0


class TestCiPinGuard:
    """A workflow installing a different library version than the
    manifest pins — CI then tests a build nobody runs."""

    # A minimum counts as the pin (4.5.2): hassfest asks for
    # `roombapy>=2.0.2` once Home Assistant itself pins roombapy.
    @pytest.mark.parametrize("requirement", ["roombapy==2.0.2", "roombapy>=2.0.2"])
    def test_it_reports_a_workflow_that_disagrees_with_the_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requirement: str
    ) -> None:
        guard = _load("check_ci_pins_match_manifest")

        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"domain": "roomba_plus", "requirements": [requirement]}),
            encoding="utf-8",
        )
        workflows = tmp_path / "workflows"
        workflows.mkdir()
        (workflows / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n"
            "      - run: pip install roombapy==1.9.1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "MANIFEST", manifest)
        monkeypatch.setattr(guard, "WORKFLOWS", workflows)

        assert guard.main() == 1


class TestMarkdownLinkGuard:
    """A link in the docs pointing at a file or heading that is not there."""

    def test_it_reports_a_link_to_a_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_markdown_links")
        (tmp_path / "README.md").write_text(
            "See [the manual](docs/NOT_THERE.md).\n", encoding="utf-8"
        )
        monkeypatch.setattr(guard, "ROOT", tmp_path)

        assert guard.main() == 1


class TestDocNameGuard:
    """A name in the docs the code does not have, or a product link to a
    file the repository does not have (scripts/check_doc_names.py, 4.6.0).
    The docs review for 4.6.0 found about twenty such names by hand."""

    def test_it_reports_names_the_code_does_not_have(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_doc_names")
        (tmp_path / "docs").mkdir()
        (tmp_path / "README.md").write_text(
            "Call `roomba_plus.reset_everything`, listen for `roomba_plus_mission_done`,\n"
            "and show `image.<robot>_mapp`.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "ROOT", tmp_path)

        found = guard.problems()
        assert len(found) == 3, found

    def test_real_names_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_doc_names")
        (tmp_path / "docs").mkdir()
        (tmp_path / "README.md").write_text(
            "Call `roomba_plus.clean_room`, listen for `roomba_plus_mission_result`,\n"
            "and show `image.<robot>_map` or `event.{name}_command`.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "ROOT", tmp_path)

        assert guard.problems() == []

    def test_it_reports_a_menu_path_the_dialog_does_not_have(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The to-do list option is under Maintenance → Settings; the
        dashboard example put it straight under Maintenance."""
        guard = _load("check_doc_names")
        (tmp_path / "docs").mkdir()
        (tmp_path / "README.md").write_text(
            "Configure → Maintenance → Show maintenance as a to-do list\n"
            "Configure → Settings → *Cleaning reminders*\n"
            "Configure → Maintenance → Settings → *Cleaning reminders* and\n"
            "Configure → Connection settings → *Send home after a pause (minutes)*.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "ROOT", tmp_path)

        found = guard.problems()
        assert len(found) == 2, found
        assert "README.md:1" in found[0] and "README.md:2" in found[1]

    def test_it_reports_a_product_link_to_a_missing_doc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_doc_names")
        monkeypatch.setattr(guard, "ROOT", tmp_path)  # a repository without docs/

        found = guard.broken_product_links()
        assert any("docs/TROUBLESHOOTING.md" in f for f in found), found
        assert any("docs/care/de/README.md" in f for f in found), found


class TestBlueprintGuard:
    """`!input` inside a template: Home Assistant does not substitute it
    there, and the automation does not load. The guard replaced it with
    `True` and passed three such blueprints."""

    def _check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str) -> int:
        guard = _load("validate_blueprints")
        (tmp_path / "b.yaml").write_text(
            "blueprint:\n  name: b\n  domain: automation\n"
            "  input:\n    robot:\n      selector:\n        text:\n"
            "variables:\n  robot: !input robot\n"
            f"conditions:\n  - condition: template\n    value_template: '{template}'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "BLUEPRINTS_DIR", tmp_path)
        return guard.main()

    def test_it_reports_an_input_inside_a_template(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._check(tmp_path, monkeypatch, "{{ (!input robot) == 1 }}") == 1

    def test_the_variable_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._check(tmp_path, monkeypatch, "{{ robot == 1 }}") == 0


class TestRoombapyPrimePinGuard:
    """The manifest pin and the workflow pin must agree — the manifest is
    the only pin a user gets."""

    def test_it_reports_a_workflow_pinning_a_different_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_roombapy_prime_pin")

        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "domain": "roomba_plus",
                    "requirements": ["roombapy-prime[map]==0.4.0b2"],
                }
            ),
            encoding="utf-8",
        )
        workflows = tmp_path / "workflows"
        workflows.mkdir()
        (workflows / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n"
            "      - run: pip install roombapy-prime==0.2.9\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "MANIFEST_PATH", manifest)
        monkeypatch.setattr(guard, "WORKFLOW_DIR", workflows)

        assert guard.main() == 1


class TestGuardsWithoutANegativeControlHere:
    """Two scripts are deliberately not covered above. Recorded so the
    gap is a decision rather than an oversight."""

    def test_the_typing_baseline_guard_runs_mypy_itself(self) -> None:
        """`check_typing_baseline` shells out to mypy over the whole
        package. A negative control would have to run it too — about a
        minute per assertion, for a guard whose failure path is three
        lines of parsing a number out of mypy's output. CI runs the real
        thing on every push.
        """
        guard = _load("check_typing_baseline")
        assert guard.BASELINE == 0, (
            "the baseline moved off zero — if that is deliberate, this "
            "file's reasoning for skipping a negative control no longer "
            "holds, because the parsing branch is then live"
        )

    def test_the_vendor_read_audit_is_a_report_not_a_guard(self) -> None:
        """`audit_unguarded_vendor_reads` says so in its own first line:
        run on demand, not in CI. It has no failure path to control
        against — counting it among the guards was a miscount.
        """
        source = (_SCRIPTS / "audit_unguarded_vendor_reads.py").read_text(
            encoding="utf-8"
        )
        assert "return 1" not in source


class TestLateImportGuard:
    """A late import that guards no cycle is cargo cult — and each one
    hides a real cycle-guarding import in the noise."""

    def test_it_reports_a_late_import_with_no_cycle_to_avoid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_late_imports")

        # `alpha` imports `beta` late; `beta` does not import `alpha`
        # back, so there is no cycle for the late import to avoid.
        (tmp_path / "alpha.py").write_text(
            "def work():\n"
            "    from .beta import helper  # noqa: PLC0415\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        (tmp_path / "beta.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8"
        )
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NON_CYCLE_REASONS", {})

        assert guard.main() == 1

    def test_a_late_import_that_does_avoid_a_cycle_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction the guard exists to make."""
        guard = _load("check_late_imports")

        (tmp_path / "alpha.py").write_text(
            "def work():\n"
            "    from .beta import helper  # noqa: PLC0415\n"
            "    return helper()\n",
            encoding="utf-8",
        )
        (tmp_path / "beta.py").write_text(
            "from .alpha import work\n\n\ndef helper():\n    return work\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NON_CYCLE_REASONS", {})

        assert guard.main() == 0


class TestIssueIdGuard:
    """4.6.12 made four shared repair ids per robot; a copy with one of
    them shared again passed the whole suite (independent review, 28.11.)."""

    def _package(self, tmp_path: Path, issue_id: str) -> Path:
        (tmp_path / "repairs.py").write_text(
            "from homeassistant.helpers import issue_registry as ir\n\n\n"
            "def raise_it(hass, config_entry):\n"
            f"    issue_id = {issue_id}\n"
            "    ir.async_create_issue(hass, 'roomba_plus', issue_id, is_fixable=False)\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_it_reports_an_id_shared_by_every_robot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_issue_ids")
        monkeypatch.setattr(guard, "PACKAGE", self._package(tmp_path, '"dock_contact_health"'))
        monkeypatch.setattr(guard, "NOT_PER_ROBOT", {})
        assert guard.main() == 1

    def test_an_id_with_the_entry_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_issue_ids")
        monkeypatch.setattr(
            guard, "PACKAGE",
            self._package(tmp_path, 'f"dock_contact_health_{config_entry.entry_id}"'),
        )
        monkeypatch.setattr(guard, "NOT_PER_ROBOT", {})
        assert guard.main() == 0

    def test_a_listed_function_that_raises_nothing_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_issue_ids")
        monkeypatch.setattr(
            guard, "PACKAGE",
            self._package(tmp_path, 'f"x_{config_entry.entry_id}"'),
        )
        monkeypatch.setattr(guard, "NOT_PER_ROBOT", {"repairs.gone": "reason"})
        assert guard.main() == 1


class TestDelaySaveGuard:
    """Two delayed saves without a flush survived until the independent
    review of 28.11.; ARCHITECTURE said "nothing enforces it"."""

    _SAVE = (
        "class Keeper:\n"
        "    def touch(self):\n"
        "        self._store.async_delay_save(self._payload, 5)\n"
    )

    def test_it_reports_a_delayed_save_nobody_flushes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_delay_saves")
        (tmp_path / "keeper.py").write_text(self._SAVE, encoding="utf-8")
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NO_FLUSH_NEEDED", {})
        assert guard.main() == 1

    def test_a_flush_on_removal_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_delay_saves")
        (tmp_path / "keeper.py").write_text(
            self._SAVE
            + "    async def async_will_remove_from_hass(self):\n"
            "        await self._store.async_save(self._payload())\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NO_FLUSH_NEEDED", {})
        assert guard.main() == 0

    def test_a_flush_nobody_calls_is_no_flush(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_delay_saves")
        (tmp_path / "keeper.py").write_text(
            self._SAVE
            + "    async def async_flush(self):\n"
            "        await self._store.async_save(self._payload())\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NO_FLUSH_NEEDED", {})
        assert guard.main() == 1
        (tmp_path / "unload.py").write_text(
            "async def unload(keeper):\n    await keeper.async_flush()\n", encoding="utf-8"
        )
        assert guard.main() == 0


class TestLateImportGuardSeesIndirectCycles:
    """The guard saw only direct back-imports and missed `from . import
    name`, so a cycle through 33 modules with one helper in `__init__`
    as its hub looked like late imports without a reason (independent
    review, 28.11.)."""

    def test_a_cycle_through_a_third_module_is_a_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_late_imports")
        (tmp_path / "alpha.py").write_text(
            "def work():\n    from .beta import helper  # noqa: PLC0415\n    return helper()\n",
            encoding="utf-8",
        )
        (tmp_path / "beta.py").write_text("from .gamma import x\n\n\ndef helper():\n    return x\n", encoding="utf-8")
        (tmp_path / "gamma.py").write_text("from .alpha import work\n\nx = 1\n", encoding="utf-8")
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NON_CYCLE_REASONS", {})
        assert guard.main() == 0

    def test_an_import_from_the_package_is_an_import_of_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_late_imports")
        (tmp_path / "__init__.py").write_text("from .alpha import work\n\n\ndef helper():\n    return 1\n", encoding="utf-8")
        (tmp_path / "alpha.py").write_text(
            "def work():\n    from . import helper  # noqa: PLC0415\n    return helper()\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(guard, "PACKAGE", tmp_path)
        monkeypatch.setattr(guard, "NON_CYCLE_REASONS", {})
        assert guard.main() == 0
        (tmp_path / "__init__.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        assert guard.main() == 1


class TestServiceSchemaGuard:
    """A field documented in services.yaml but absent from the schema.

    The worst of the three ways a service field can break: HA renders it,
    the user fills it in, and validation refuses the call before any of
    our code runs — on a field we documented ourselves. It has happened
    three times, most recently `clean_zone` missing three at once
    (@theChef613).
    """

    def _tree(self, tmp_path: Path, *, schema_has_field: bool) -> Path:
        component = tmp_path / "roomba_plus"
        component.mkdir()
        (component / "services.yaml").write_text(
            "clean_room:\n"
            "  fields:\n"
            "    room_id:\n"
            "      required: true\n"
            "    two_pass:\n"
            "      required: false\n",
            encoding="utf-8",
        )
        felder = '"room_id": cv.string'
        if schema_has_field:
            felder += ', "two_pass": cv.boolean'
        (component / "services.py").write_text(
            "import voluptuous as vol\n"
            "import homeassistant.helpers.config_validation as cv\n"
            "\n"
            f"SCHEMA = vol.Schema({{{felder}}})\n"
            "\n"
            "def setup(hass):\n"
            '    hass.services.async_register("roomba_plus", "clean_room",\n'
            "                                 handler, schema=SCHEMA)\n",
            encoding="utf-8",
        )
        (component / "const.py").write_text("DOMAIN = 'roomba_plus'\n", encoding="utf-8")
        return component

    def test_it_reports_a_documented_field_the_schema_rejects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_service_schemas")
        component = self._tree(tmp_path, schema_has_field=False)
        monkeypatch.setattr(guard, "COMPONENT", component)
        monkeypatch.setattr(guard, "SERVICES_YAML", component / "services.yaml")
        monkeypatch.setattr(
            guard, "REGISTERING_MODULES", [component / "services.py"]
        )

        assert guard.main() == 1

    def test_it_passes_when_the_schema_accepts_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_service_schemas")
        component = self._tree(tmp_path, schema_has_field=True)
        monkeypatch.setattr(guard, "COMPONENT", component)
        monkeypatch.setattr(guard, "SERVICES_YAML", component / "services.yaml")
        monkeypatch.setattr(
            guard, "REGISTERING_MODULES", [component / "services.py"]
        )

        assert guard.main() == 0

    def test_it_reads_names_built_in_a_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reset actions are registered as f"reset_{part}" in a loop;
        the guard reported them as unreadable (4.6.0)."""
        guard = _load("check_service_schemas")
        component = tmp_path / "roomba_plus"
        component.mkdir()
        (component / "services.yaml").write_text(
            "reset_filter:\n  fields:\n    entity_id:\n      required: true\n"
            "reset_brush:\n  fields:\n    entity_id:\n      required: true\n",
            encoding="utf-8",
        )
        (component / "services.py").write_text(
            "import voluptuous as vol\n"
            "SCHEMA = vol.Schema({'entity_id': str})\n"
            "def setup(hass):\n"
            "    for _part in ('filter', 'brush'):\n"
            "        svc = f'reset_{_part}'\n"
            "        register_guarded(hass, 'roomba_plus', svc, handler, schema=SCHEMA)\n",
            encoding="utf-8",
        )
        (component / "const.py").write_text("DOMAIN = 'roomba_plus'\n", encoding="utf-8")
        monkeypatch.setattr(guard, "COMPONENT", component)
        monkeypatch.setattr(guard, "SERVICES_YAML", component / "services.yaml")
        monkeypatch.setattr(guard, "REGISTERING_MODULES", [component / "services.py"])

        assert guard.main() == 0


# ── formerly tests/test_guard_coverage_script.py ────────────────────────────────
#
# scripts/check_coverage_per_module.py -- the CI gate for 95 % per module.
#
# The rounded terminal report once let three modules through that were
# short (94.57 % prints as 95 %). The gate counts lines itself.

coverage_guard = _load("check_coverage_per_module")


def _xml(tmp_path, modules):
    classes = []
    for name, covered, total in modules:
        lines = "".join(f'<line number="{i}" hits="{1 if i <= covered else 0}"/>' for i in range(1, total + 1))
        classes.append(f'<class filename="{name}" branch-rate="0.5"><lines>{lines}</lines></class>')
    p = tmp_path / "coverage.xml"
    p.write_text(f'<coverage><packages><package><classes>{"".join(classes)}</classes>'
                 f'</package></packages></coverage>')
    return p


def test_a_module_the_rounded_report_shows_as_95_fails(tmp_path, capsys):
    """1893 of 2000 lines is 94.65 %: printed as 95 %, still short."""
    path = _xml(tmp_path, [("ok.py", 100, 100), ("short.py", 1893, 2000)])
    assert coverage_guard.main(["x", str(path)]) == 1
    out = capsys.readouterr().out
    assert "short.py" in out and "7 more line(s)" in out


def test_exactly_95_passes(tmp_path):
    assert coverage_guard.main(["x", str(_xml(tmp_path, [("edge.py", 95, 100)]))]) == 0


def test_a_missing_report_fails_when_named_and_passes_when_not(tmp_path, monkeypatch):
    assert coverage_guard.main(["x", str(tmp_path / "nope.xml")]) == 1
    monkeypatch.chdir(tmp_path)
    assert coverage_guard.main(["x"]) == 0


# ── a guard that skips in CI checks nothing ──────────────────────────────

class TestAMissingLibraryFailsInCI:
    """check_client_attributes and check_vendor_value_tables ran in a CI job
    that installed neither library. Both skipped and exited 0, so in CI
    they never checked anything. With CI set, a missing library now fails
    them; locally it still skips."""

    @pytest.mark.parametrize("script,blocked", [
        ("check_client_attributes", "roombapy"),
        ("check_vendor_value_tables", "roombapy_prime.vendor_reference"),
    ])
    def test_skips_locally_fails_in_ci(self, monkeypatch, script, blocked):
        guard = _load(script)
        monkeypatch.setitem(sys.modules, blocked, None)   # makes the import raise ImportError
        monkeypatch.delenv("CI", raising=False)
        assert guard.main() == 0
        monkeypatch.setenv("CI", "true")
        assert guard.main() == 1


# ── vendor values left out on purpose ───────────────────────────────────

class TestNotOfferedValues:
    """4.3.4: app 3.2.0 added `HeatType.deepHeat` and
    `ReturnByMode.smartMode`, which the app offers on no screen. They are
    left out of the pickers with a reason, and a reason that no longer
    applies is reported."""

    def test_the_real_tables_pass(self):
        guard = _load("check_vendor_value_tables")
        assert guard.main() == 0

    def test_without_the_reason_the_new_value_fails(self, monkeypatch, capsys):
        from custom_components.roomba_plus import select_prime

        guard = _load("check_vendor_value_tables")
        monkeypatch.setattr(select_prime, "VENDOR_VALUES_NOT_OFFERED", {})
        assert guard.main() == 1
        assert "only vendor: ['103']" in capsys.readouterr().err

    def test_a_stale_reason_fails(self, monkeypatch, capsys):
        from custom_components.roomba_plus import select_prime

        guard = _load("check_vendor_value_tables")
        stale = dict(select_prime.VENDOR_VALUES_NOT_OFFERED)
        stale["PAD_WASH_HEAT_LEVELS"] = {2: "offered after all"}
        monkeypatch.setattr(select_prime, "VENDOR_VALUES_NOT_OFFERED", stale)
        assert guard.main() == 1
        assert "stale" in capsys.readouterr().err


class TestSilentExceptGuard:
    """Seven silent broad handlers in the mission path until 4.7.0b1; a
    failure there was invisible in a log and in a replay alike."""

    def _module(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / "keeper.py").write_text(
            "def keep(x):\n    try:\n        return int(x)\n" + body, encoding="utf-8",
        )
        return tmp_path

    def test_it_reports_a_new_silent_broad_except(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_silent_excepts")
        monkeypatch.setattr(guard, "PACKAGE", self._module(
            tmp_path, "    except Exception:\n        return None\n"))
        monkeypatch.setattr(guard, "KNOWN", {})
        assert guard.main() == 1

    def test_a_logged_or_narrow_except_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_silent_excepts")
        monkeypatch.setattr(guard, "PACKAGE", self._module(
            tmp_path,
            "    except (TypeError, ValueError):\n        return None\n"
            "    except Exception:\n        log.debug('x', exc_info=True)\n",
        ))
        monkeypatch.setattr(guard, "KNOWN", {})
        assert guard.main() == 0

    def test_a_count_that_went_down_must_be_lowered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_silent_excepts")
        monkeypatch.setattr(guard, "PACKAGE", self._module(
            tmp_path, "    except ValueError:\n        return None\n"))
        monkeypatch.setattr(guard, "KNOWN", {"keeper.py": 1})
        assert guard.main() == 1


class TestTheMutationProbeStillApplies:
    """scripts/mutation_probe.py runs outside CI (4.7-3); this keeps it
    from rotting: a refactor that removes a mutation's source has to
    bring the mutation along."""

    def test_every_mutation_finds_its_source_once(self) -> None:
        probe = _load("mutation_probe")
        stale = {m.key: probe.applies(m) for m in probe.MUTATIONS if probe.applies(m) != 1}
        assert stale == {}

    def test_every_mutation_names_test_files_that_exist(self) -> None:
        probe = _load("mutation_probe")
        missing = [
            (m.key, t) for m in probe.MUTATIONS for t in m.tests
            if not (probe.ROOT / t).is_file()
        ]
        assert missing == []

    def test_keys_are_unique(self) -> None:
        probe = _load("mutation_probe")
        keys = [m.key for m in probe.MUTATIONS]
        assert len(keys) == len(set(keys))

