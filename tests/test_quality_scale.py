"""The quality scale file makes claims about this integration.

Nothing verified them, and two were wrong: `migration` named config entry
version 13 while the flow was at 25, and `strict-typing` said done while
mypy had never been installed.
"""

import pytest


class TestTheQualityScaleFileIsTrue:
    def test_the_migration_comment_names_the_real_version(self):
        """Twelve migrations of drift, because nothing compared this
        against `config_flow.VERSION`."""
        import pathlib
        import re

        from custom_components.roomba_plus.config_flow import (
            RoombaPlusConfigFlow,
        )

        text = pathlib.Path(
            "custom_components/roomba_plus/quality_scale.yaml"
        ).read_text()
        # Cut at the next rule name, not at the next indented line --
        # every line of a comment block starts with two spaces.
        block = text[text.find("  migration:"):]
        end = re.search(r"\n  [a-z-]+:\n", block)
        block = block[:end.start()] if end else block
        claimed = {int(n) for n in re.findall(r"version[ *]+(\d+)", block)}

        assert RoombaPlusConfigFlow.VERSION in claimed, (
            f"quality_scale.yaml names {claimed or 'no version'}, "
            f"config flow is at {RoombaPlusConfigFlow.VERSION}"
        )

    def test_the_manifest_declares_the_scale(self):
        """The README badge said Gold while the manifest declared
        nothing — a claim in the place Home Assistant does not read,
        and silence in the place it does."""
        import json
        import pathlib

        manifest = json.loads(
            pathlib.Path(
                "custom_components/roomba_plus/manifest.json"
            ).read_text()
        )

        assert manifest.get("quality_scale") in {
            "bronze", "silver", "gold", "platinum",
        }

    def test_strict_typing_is_only_claimed_when_mypy_passes(self):
        """It said `done` this morning with mypy uninstalled. The status
        is allowed to say `done` now — but only while mypy actually
        passes, which is what this checks rather than the word."""
        import pathlib
        import subprocess
        import sys

        text = pathlib.Path(
            "custom_components/roomba_plus/quality_scale.yaml"
        ).read_text()
        block = text[text.find("  strict-typing:"):]
        end = block.find("\n  #")
        block = block[:end] if end > 0 else block

        if "status: todo" in block:
            return  # honest either way

        # THE TEST SUITE JOB DOES NOT INSTALL mypy — the Typing job
        # does. Without this, the guard failed in CI on
        # `No module named mypy` and read as a stale `done` claim,
        # which is precisely the thing it exists to distinguish from.
        pytest.importorskip("mypy", reason="mypy is checked by its own CI job")

        result = subprocess.run(
            [sys.executable, "-m", "mypy", "custom_components/roomba_plus"],
            capture_output=True, text=True,
        )

        assert "Success" in result.stdout, (
            f"quality_scale.yaml claims strict-typing is done, but mypy "
            f"reports: {result.stdout.strip().splitlines()[-1:]}"
        )


class TestTheManifestKeysAreOrderedAsHassfestWants:
    """`domain`, `name`, then alphabetical.

    Adding `quality_scale` after `iot_class` — where it reads naturally
    beside the other descriptive keys — broke this and failed Hassfest
    in CI. Hassfest only runs there, so nothing local caught it.
    """

    def test_domain_and_name_come_first(self):
        import json
        import pathlib

        keys = list(json.loads(
            pathlib.Path(
                "custom_components/roomba_plus/manifest.json"
            ).read_text()
        ))

        assert keys[:2] == ["domain", "name"]

    def test_everything_else_is_alphabetical(self):
        import json
        import pathlib

        keys = list(json.loads(
            pathlib.Path(
                "custom_components/roomba_plus/manifest.json"
            ).read_text()
        ))
        rest = keys[2:]

        assert rest == sorted(rest), (
            f"manifest keys after domain/name must be alphabetical -- "
            f"Hassfest refuses otherwise. Got: {rest}"
        )


class TestPrimeCodeDoesNotReadClassicSources:
    """Five bugs this week shared one shape: Prime code reading a source
    that is Classic-only or knows only part of the answer.

    The guard script is the durable version of that check. This runs it
    so the suite fails rather than only CI.
    """

    def test_the_guard_passes(self):
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "check_prime_sources.py"
        )

        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stdout


class TestRuntimeDataReadsAreReal:
    """`no_contact` shipped in a46 and could not fire on a single Prime
    robot, because the check read an attribute neither coordinator has.
    `getattr` returned None, a type guard turned that into "never
    stale", and the feature was inert on one generation while working
    on the other.

    Third instance of the shape this week, and the second to ship in the
    same release as a guard script written for it — `check_prime_sources`
    cannot see this one, because the object is right and only the
    attribute is imagined.
    """

    def test_the_guard_passes(self):
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "check_runtime_data_reads.py"
        )

        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stdout


class TestTheDocumentedStateListIsComplete:
    """@Thonno updated his dashboard from b1's release notes and found
    the list of values was not there — only `charging` was named, as an
    example of the breaking change.

    A breaking change to an entity's state is only actionable if every
    value is written down somewhere. A list that silently falls behind
    `strings.json` is worse than none, because it looks authoritative.
    """

    def test_every_state_appears_in_the_release_notes(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        states = json.loads(
            (root / "custom_components" / "roomba_plus" / "strings.json")
            .read_text(encoding="utf-8")
        )["entity"]["sensor"]["phase"]["state"]
        notes = (root / "release-notes" / "v4.0.0b2.md").read_text(
            encoding="utf-8"
        )

        missing = [k for k in states if f"`{k}`" not in notes]

        assert not missing, f"undocumented status values: {missing}"


class TestEveryMenuEntryHasALabel:
    """@Thonno found the sixth item in the options menu had no visible
    text. It opened the right submenu — only the label was missing.

    `room_schedule` was offered by `config_flow.py` and absent from
    `strings.json`, so Home Assistant had nothing to display and drew
    an empty row. The existing translation checks compare files against
    each other; nothing compared them against the code.
    """

    def test_no_menu_option_is_unnamed(self):
        import json
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        offered = set(
            re.findall(r'menu\.append\("([a-z_0-9]+)"\)',
                       (root / "config_flow.py").read_text(encoding="utf-8"))
        )
        named = set(
            json.loads((root / "strings.json").read_text(encoding="utf-8"))
            ["options"]["step"]["init"]["menu_options"]
        )

        assert not (offered - named), (
            f"menu entries with no label: {sorted(offered - named)}"
        )
