#!/usr/bin/env python3
"""Fail when any module's LINE coverage is below 95 %, counted exactly.

The quality scale's test-coverage rule asks for more than 95 % in every
module. The terminal report rounds: 94.6 % prints as 95 %, and counting
from that report once passed three modules that were short. This reads
the per-line hits from coverage.xml and compares covered / statements.

Branch coverage is printed per module for information, never failed on:
plenty of partial branches are defensive and should not get a
manufactured test.

Usage:
    python scripts/check_coverage_per_module.py coverage.xml
Without an argument it looks for ./coverage.xml and, when there is none,
reports that it had nothing to check and passes -- so it can sit among
the other check_*.py scripts that run without a coverage run. CI passes
the path explicitly, and then a missing file is an error.
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

THRESHOLD = 0.95


def per_module(path: pathlib.Path) -> list[tuple[str, int, int, float | None]]:
    """(file, covered lines, statements, branch rate or None) per module."""
    rows = []
    for cls in ET.parse(path).getroot().iter("class"):
        lines = list(cls.iter("line"))
        total = len(lines)
        covered = sum(1 for ln in lines if int(ln.get("hits", "0")) > 0)
        branch = cls.get("branch-rate")
        rows.append((cls.get("filename", "?"), covered, total,
                     float(branch) if branch is not None else None))
    return rows


def main(argv: list[str]) -> int:
    explicit = len(argv) > 1
    path = pathlib.Path(argv[1] if explicit else "coverage.xml")
    if not path.exists():
        if explicit:
            print(f"::error::{path} not found -- run the tests with --cov-report=xml first")
            return 1
        print("check_coverage_per_module: no coverage.xml here, nothing to check")
        return 0
    rows = per_module(path)
    short = [(f, c, t) for f, c, t, _b in rows if t and c / t < THRESHOLD]
    for f, c, t, b in sorted(rows):
        mark = "  SHORT" if t and c / t < THRESHOLD else ""
        branch = f"  branches {b:.0%}" if b is not None else ""
        print(f"{c / t if t else 1:7.2%}  {f}{branch}{mark}")
    if short:
        for f, c, t in short:
            need = int(t * THRESHOLD + 0.999999) - c
            print(f"::error file={f}::line coverage {c}/{t} = {c / t:.2%}, "
                  f"below 95 % -- {need} more line(s) needed")
        return 1
    print(f"all {len(rows)} modules at or above 95 % line coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
