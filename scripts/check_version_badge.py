#!/usr/bin/env python3
"""README version badge vs. manifest.json — consistency check.

Directly motivated by a real drift found during this project's own
v3.4.2 work: the README's version badge stayed at "3.4.0" through two
subsequent releases before being noticed and fixed by hand. This is a
DIFFERENT check from release.yml's "manifest.json version matches git
tag" step — that one only runs at release time, against the tag; this
one runs on every push to main, against the actual current README text,
so drift can't sit unnoticed between releases the way it did before.

Exit 0 = badge matches manifest.json. Exit 1 = mismatch, printed to stdout.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "custom_components" / "roomba_plus" / "manifest.json"
README_PATH = ROOT / "README.md"

BADGE_PATTERN = re.compile(r"!\[Version\]\(https://img\.shields\.io/badge/Version-([\d.]+)-brightgreen\.svg\)")


def main() -> int:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest_version = json.load(f)["version"]

    with open(README_PATH, encoding="utf-8") as f:
        readme_text = f.read()

    match = BADGE_PATTERN.search(readme_text)
    if not match:
        print("::error::Could not find the Version badge in README.md — did its format change? "
              "Update BADGE_PATTERN in this script to match if so.")
        return 1

    badge_version = match.group(1)
    if badge_version != manifest_version:
        print(f"::error::README.md's version badge says {badge_version}, but "
              f"manifest.json says {manifest_version}. Update the badge.")
        return 1

    print(f"OK: README badge and manifest.json both say {manifest_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
