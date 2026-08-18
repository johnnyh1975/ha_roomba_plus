#!/usr/bin/env python3
"""Fails when the mypy error count goes up.

WHY A RATCHET RATHER THAN A GATE.

`strict-typing` sat in quality_scale.yaml as `done` while mypy had never
been installed. Requiring zero today would mean 565 errors between here
and any green build, so nobody would run it and the claim would go stale
again -- exactly how it got there the first time.

A ratchet is checkable from the first day. The number may fall and may
not rise, and the file it is written in is the one thing that has to be
edited deliberately.

Two error codes are disabled by name in mypy.ini rather than deferred as
flags. They go back on when this reaches zero; that is the exit
condition.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: Errors reported by `mypy custom_components/roomba_plus` under the
#: settings in mypy.ini. Lower this when you fix things. Raising it
#: should be an argued decision, not a convenience.
BASELINE = 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "custom_components/roomba_plus"],
        cwd=root, capture_output=True, text=True,
    )
    match = re.search(r"Found (\d+) errors?", result.stdout)
    if not match:
        if "Success" in result.stdout:
            if BASELINE == 0:
                print("OK: mypy is clean.")
                return 0
            print(f"mypy: clean. Lower BASELINE from {BASELINE} to 0.")
            return 1
        print("Could not read a count from mypy:")
        print(result.stdout[-800:] or result.stderr[-800:])
        return 1

    found = int(match.group(1))
    if found > BASELINE:
        print(f"mypy: {found} errors, baseline is {BASELINE} — up by {found - BASELINE}.")
        return 1
    if found < BASELINE:
        print(
            f"mypy: {found} errors, baseline is {BASELINE}. "
            f"Lower BASELINE to {found} in this file."
        )
        return 1
    print(f"OK: mypy reports {found} errors, matching the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
