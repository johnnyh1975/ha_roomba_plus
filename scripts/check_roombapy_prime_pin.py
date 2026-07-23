#!/usr/bin/env python3
"""roombapy-prime version pin — consistency check across all three references.

Directly motivated by real, repeated drift found during this project's own
work: manifest.json and the two CI workflow files (release.yml/validate.yml)
each carry their own literal `@v0.1.11aXX` pin string for the roombapy-prime
git dependency. These drifted out of sync from each other more than once
(a version bump updated one or two of the three, but not all three), and once
led to CI silently testing against a stale roombapy-prime commit for an
entire session before being noticed.

Exit 0 = all three references agree. Exit 1 = mismatch, printed to stdout.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "custom_components" / "roomba_plus" / "manifest.json"
RELEASE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
VALIDATE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "validate.yml"

PIN_PATTERN = re.compile(
    r"roombapy-prime(?:\[map\])?@git\+https://github\.com/johnnyh1975/roombapy-prime\.git@(v[\d.]+a\d+)"
)


def _extract_pin(text: str, source_name: str) -> str:
    match = PIN_PATTERN.search(text)
    if match is None:
        print(f"ERROR: no roombapy-prime git pin found in {source_name}")
        sys.exit(1)
    return match.group(1)


def main() -> int:
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    # Sanity: confirm this is actually valid JSON, not just pattern-matched text.
    json.loads(manifest_text)
    manifest_pin = _extract_pin(manifest_text, str(MANIFEST_PATH))

    release_pin = _extract_pin(RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8"), str(RELEASE_WORKFLOW_PATH))
    validate_pin = _extract_pin(VALIDATE_WORKFLOW_PATH.read_text(encoding="utf-8"), str(VALIDATE_WORKFLOW_PATH))

    pins = {
        "manifest.json": manifest_pin,
        "release.yml": release_pin,
        "validate.yml": validate_pin,
    }
    unique_pins = set(pins.values())

    if len(unique_pins) == 1:
        print(f"OK: all three roombapy-prime pin references agree ({manifest_pin}).")
        return 0

    print("MISMATCH: roombapy-prime pin references disagree:")
    for source, pin in pins.items():
        print(f"  {source}: {pin}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
