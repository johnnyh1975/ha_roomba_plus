#!/usr/bin/env python3
"""The blueprints shipped in the integration are the repository's (4.6.0).

HACS installs `custom_components/roomba_plus/` and nothing else, so the
blueprints the integration copies into Home Assistant at startup
(blueprint_install.py) live there too. The repository's
`blueprints/automation/` stays the source the import links in the docs
point at. This keeps the two identical: same files, same bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "blueprints" / "automation"
SHIPPED = ROOT / "custom_components" / "roomba_plus" / "blueprints"


def main() -> int:
    source = {p.name: p.read_bytes() for p in SOURCE.glob("*.yaml")}
    shipped = {p.name: p.read_bytes() for p in SHIPPED.glob("*.yaml")}
    problems = [f"missing in the integration: {n}" for n in sorted(set(source) - set(shipped))]
    problems += [f"not in blueprints/automation: {n}" for n in sorted(set(shipped) - set(source))]
    problems += [
        f"differs from blueprints/automation: {n}"
        for n in sorted(set(source) & set(shipped)) if source[n] != shipped[n]
    ]
    if problems:
        print("The shipped blueprints are not the repository's:")
        for line in problems:
            print(f"  {line}")
        print("Copy blueprints/automation/*.yaml to custom_components/roomba_plus/blueprints/.")
        return 1
    print(f"OK: {len(source)} blueprint(s) shipped, identical to blueprints/automation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
