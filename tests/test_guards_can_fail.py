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

    def test_it_reports_a_workflow_that_disagrees_with_the_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = _load("check_ci_pins_match_manifest")

        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps({"domain": "roomba_plus", "requirements": ["roombapy==2.0.2"]}),
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
