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

    def test_async_dependency_is_only_claimed_when_the_library_is_async(self):
        """It said `done` once with the comment "roombapy is async-capable
        via paho-MQTT thread", which is what the rule exists to exclude.
        It was corrected to `todo` against a count, and back to `done`
        against another one — so the claim is checked the same way it was
        broken, rather than trusted.

        Two things have to hold. The pinned library must actually define
        coroutines, and this integration must not be putting them back in
        a thread: a fully async dependency driven through
        `async_add_executor_job()` is the same failure wearing a
        different pin.
        """
        import inspect
        import pathlib

        text = pathlib.Path(
            "custom_components/roomba_plus/quality_scale.yaml"
        ).read_text()
        block = text[text.find("  async-dependency:"):]
        end = block.find("\n  #")
        block = block[:end] if end > 0 else block

        if "status: todo" in block:
            return  # honest either way

        from roombapy import RoombaClient

        coroutines = sorted(
            name for name in dir(RoombaClient)
            if not name.startswith("_")
            and inspect.iscoroutinefunction(getattr(RoombaClient, name, None))
        )

        assert {"connect", "disconnect", "send_command", "set_preference"} <= set(
            coroutines
        ), (
            "quality_scale.yaml claims async-dependency is done, but the "
            f"pinned roombapy exposes these coroutines: {coroutines}"
        )

        # AND WE MUST BE AWAITING THEM. The guard script is the real
        # check; this asserts that it is passing, so the scale cannot
        # claim `done` while a command is being run in a worker thread.
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/check_no_executor_coroutines.py"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            "quality_scale.yaml claims async-dependency is done, but a "
            f"roombapy coroutine is still run in an executor:\n{result.stdout}"
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


class TestTheScaleMatchesTheRules:
    """The manifest claims a tier; `quality_scale.yaml` records rule by
    rule whether it is earned. Nothing tied the two together, so the
    claim could drift in either direction -- and this file already
    carries the scar from one of those: `strict-typing` said `done`
    before anyone had run mypy, and the first real run reported 565
    errors and eleven runtime bugs.

    So the tier is checked here against the rule file, and strict-typing
    has its own check above that runs the type checker rather than
    reading the word `done`.

    Platinum is claimed only once every rule reads `done` -- and the
    typing rule has its own separate check above that actually runs the
    type checker.
    """

    @staticmethod
    def _rules():
        import yaml

        data = yaml.safe_load(
            open(
                "custom_components/roomba_plus/quality_scale.yaml",
                encoding="utf-8",
            )
        )
        return data.get("rules", data)

    @staticmethod
    def _claimed():
        import json

        return json.load(
            open(
                "custom_components/roomba_plus/manifest.json", encoding="utf-8"
            )
        ).get("quality_scale")

    def test_platinum_needs_every_rule_done(self):
        if self._claimed() != "platinum":
            return

        unfinished = [
            name
            for name, rule in self._rules().items()
            if (rule.get("status") if isinstance(rule, dict) else rule)
            not in ("done", "exempt")
        ]

        assert not unfinished, (
            "manifest says platinum, but these rules are not done: "
            + ", ".join(sorted(unfinished))
        )

    def test_the_three_platinum_rules_are_present_and_done(self):
        """`strict-typing`, `async-dependency` and `inject-websession`
        are what separate platinum from gold. A file missing one of them
        would pass the check above by having nothing to fail."""
        if self._claimed() != "platinum":
            return

        rules = self._rules()
        for name in ("strict-typing", "async-dependency", "inject-websession"):
            assert name in rules, f"{name} is not recorded at all"
            rule = rules[name]
            status = rule.get("status") if isinstance(rule, dict) else rule
            assert status == "done", f"{name} is {status!r}"

    def test_every_rule_carries_a_reason(self):
        """A bare status records a decision nobody can check later.

        The bar is a reason, not a length: `integration-owner` is
        satisfied by "codeowners set in manifest", and padding that out
        would make the file worse. What this rejects is an empty one.
        """
        silent = [
            name
            for name, rule in self._rules().items()
            if isinstance(rule, dict)
            and not str(rule.get("comment", "")).strip()
        ]

        assert not silent, "rules claiming a status with no reasoning: " + (
            ", ".join(sorted(silent))
        )


class TestTheBadgeMatchesTheManifest:
    """The manifest said `platinum` and the README badge still said Gold,
    in three places -- one of which named every Platinum rule as met
    while claiming the Gold tier in the same sentence.

    `check_version_badge.py` next door catches exactly this shape for the
    version number, and was written because the two drifted. The quality
    tier is the same kind of claim in the same file and had no such
    check.
    """

    @staticmethod
    def _claimed() -> str:
        import json

        return json.load(
            open(
                "custom_components/roomba_plus/manifest.json", encoding="utf-8"
            )
        )["quality_scale"]

    def test_the_readme_badge_says_the_same_tier(self) -> None:
        import pathlib

        readme = pathlib.Path("README.md").read_text(encoding="utf-8")
        tier = self._claimed()

        assert f"Quality%20Scale-{tier.capitalize()}-" in readme, (
            f"manifest says {tier}, badge does not"
        )

    def test_no_user_facing_file_names_another_tier(self) -> None:
        """Prose drifts more quietly than a badge does."""
        import pathlib
        import re

        tier = self._claimed()
        others = {"bronze", "silver", "gold", "platinum"} - {tier}
        offenders = []
        for name in ("README.md", "docs/COMPARISON.md"):
            text = pathlib.Path(name).read_text(encoding="utf-8")
            for line in text.splitlines():
                low = line.lower()
                # Only lines making a claim ABOUT THIS integration --
                # the comparison table names other tiers for the other
                # integrations on purpose.
                if "quality" not in low and "-quality" not in low:
                    continue
                for other in others:
                    if re.search(rf"\b{other}\b", low) and tier not in low:
                        offenders.append(f"{name}: {line.strip()[:70]}")

        assert not offenders, "stale tier claims: " + " | ".join(offenders)
