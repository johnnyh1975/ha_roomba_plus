#!/usr/bin/env python3
"""roombapy-prime version pin — consistency check across every reference.

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
WORKFLOW_DIR = ROOT / ".github" / "workflows"

#: Matches alpha (aN), beta (bN), release candidate (rcN) and plain
#: version pins.
#:
#: WIDENED 30 July 2026, when roombapy-prime reached 0.2.0b1 and this
#: script reported "no pin found" for a pin that was right there. The
#: original pattern ended in `a\d+`, which was correct while the library
#: had only ever shipped alphas -- and became a false negative the day
#: that stopped being true.
#:
#: Worth noting the failure mode: it did not say "the pin looks wrong",
#: it said the pin was ABSENT. A pattern that cannot match reports the
#: same thing as a missing line, and the two need different fixes.
#: AND AGAIN, for the same reason. The pattern matched a git URL, which
#: was the only way to depend on this library until it went to PyPI.
#: The day the manifest said `roombapy-prime[map]==0.3.1` instead, the
#: pattern reported the pin as ABSENT rather than as changed -- exactly
#: the failure mode described above, one release later.
#:
#: Matches both spellings now: an index pin, and the git URL, since a
#: prerelease may still be installed that way while it is unpublished.
PIN_PATTERN = re.compile(
    r"roombapy-prime(?:\[map\])?"
    r"(?:"
    r"==([\d.]+(?:a\d+|b\d+|rc\d+)?)"
    r"|"
    r"@git\+https://github\.com/johnnyh1975/"
    r"roombapy-prime\.git@v([\d.]+(?:a\d+|b\d+|rc\d+)?)"
    r")"
)


def _extract_pins(text: str, source_name: str) -> list[str]:
    """Every pin in the text, not just the first.

    WIDENED from a single `.search()` per file. Two things made that too
    narrow, one of them long-standing and one introduced later:

      * validate.yml has always installed roombapy-prime in more than one
        job (the pinned-HA suite and the newer-HA suite). Only the first
        was ever compared, so a bump that updated one job and missed the
        other passed this guard -- exactly the drift the script exists
        to catch.

      * hacs/hassfest/typing were moved out of validate.yml into their
        own workflows so each could carry a README badge. typing.yml
        installs roombapy-prime too, and a two-file allowlist could not
        see it at all.

    So: enumerate the workflow directory instead of naming files, and
    collect every match in each. A file that mentions the library but
    carries no recognisable pin is still an error -- a pattern that
    cannot match reports the same thing as a missing line.
    """
    pins = [m.group(1) for m in PIN_PATTERN.finditer(text)]
    if not pins and "roombapy-prime" in text:
        print(f"ERROR: {source_name} installs roombapy-prime but no pin was recognised")
        sys.exit(1)
    return pins


def main() -> int:
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    # Sanity: confirm this is actually valid JSON, not just pattern-matched text.
    json.loads(manifest_text)
    manifest_pins = _extract_pins(manifest_text, str(MANIFEST_PATH))
    if not manifest_pins:
        print(f"ERROR: no roombapy-prime pin found in {MANIFEST_PATH}")
        return 1

    pins: dict[str, str] = {"manifest.json": manifest_pins[0]}

    for workflow in sorted(WORKFLOW_DIR.glob("*.yml")):
        found = _extract_pins(workflow.read_text(encoding="utf-8"), str(workflow))
        for index, pin in enumerate(found, start=1):
            label = workflow.name if len(found) == 1 else f"{workflow.name} (#{index})"
            pins[label] = pin

    unique_pins = set(pins.values())

    if len(unique_pins) == 1:
        print(
            f"OK: all {len(pins)} roombapy-prime pin references agree "
            f"({manifest_pins[0]})."
        )
        return 0

    print("MISMATCH: roombapy-prime pin references disagree:")
    for source, pin in pins.items():
        print(f"  {source}: {pin}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
