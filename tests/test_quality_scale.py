"""The quality scale file makes claims about this integration.

Nothing verified them, and two were wrong: `migration` named config entry
version 13 while the flow was at 25, and `strict-typing` said done while
mypy had never been installed.
"""


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
