#!/usr/bin/env python3
"""Fail if a workflow installs a different version than the manifest pins.

`manifest.json` is the only pin that reaches a user; the versions named
in the workflows decide only what CI tests against. When they drift, CI
goes green against a library nobody runs.

That is not hypothetical. Through the 4.2 async migration the manifest
said `roombapy==2.0.1` while `validate.yml` still installed 1.9.1 -- so
the whole rewrite would have been tested against the library it was
migrating away from, with all 6,197 tests passing and 46 commands
unable to reach a robot. It was caught by reading the workflow, not by
anything failing.

Checks every `name==version` for the packages the manifest pins.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "roomba_plus" / "manifest.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def _pins(text: str) -> dict[str, str]:
    """{package: version} from `name[extra]==version` occurrences."""
    found: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9][\w.]*)", text):
        found.setdefault(match.group(1).lower(), match.group(2))
    return found


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = _pins("\n".join(manifest.get("requirements", [])))
    if not wanted:
        print("manifest.json pins nothing; nothing to compare.")
        return 0

    problems: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if "pip install" not in line and "==" not in line:
                continue
            for package, version in _pins(line).items():
                if package in wanted and version != wanted[package]:
                    problems.append(
                        f"  {path.name}:{line_no}  {package}=={version} "
                        f"(manifest says {wanted[package]})"
                    )

    if problems:
        print(
            "CI installs versions the manifest does not pin:\n"
            + "\n".join(problems)
            + "\n\nThe manifest is what users get. A workflow that installs "
            "something else tests a build nobody runs -- and passes while "
            "doing it."
        )
        return 1

    print(
        "OK: every workflow pin matches manifest.json ("
        + ", ".join(f"{k}=={v}" for k, v in sorted(wanted.items()))
        + ")."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
