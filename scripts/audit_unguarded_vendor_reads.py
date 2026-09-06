#!/usr/bin/env python3
"""Audit unguarded reads of vendor wire fields. Run on demand, not in CI.

WHAT IT ANSWERS. The vendor firmware schemas
(roombapy-prime/docs/internal/vendor_schemas_ruby_0_7_12.json) say which
fields the robot GUARANTEES on its local MQTT channel and which it
merely may send. This walks every module and asks: do we ever read a
NOT-required field with a bare subscript, and without testing for it
first?

WHY IT IS NOT A CI GUARD, which is the more useful thing to know.
It was written, refined three times, and found nothing:

    run 1  30 hits   subscript on any schema field, no flow analysis
    run 2  27 hits   guards recognised within the enclosing function
    run 3   5 hits   restricted to distinctive vendor keys (camelCase)
    manual  0 real   all five were `result["..."]` on a dict this code
                     had built a few lines earlier

Every hit at every stage was a false positive. The six checked by hand
in run 1 -- `reported["bbrun"]`, `reported["cleanMissionStatus"]`,
`nav_stats["reLc"]`, `state["mopReady"]`, `inner["pauses"]` -- each had
an `in` or `isinstance` test immediately above it.

A check that has never been able to fail does not belong in a pipeline.
It would pass every run, teach nobody anything, and cost a job slot. It
belongs here, where someone can run it after touching the read paths:

    python scripts/audit_unguarded_vendor_reads.py

WHAT IT CANNOT SEE, so nobody mistakes a clean run for a proof:

  * Flow analysis stops at the function boundary. A key tested in a
    caller and read in a callee reads as unguarded; a key tested on a
    DIFFERENT object reads as guarded. The second is the dangerous
    direction and this cannot tell them apart.
  * `.get("k")` counts as a guard, and it is not one against an
    explicit null -- a distinction this project has had to relearn at
    a dozen sites. This audit is about MISSING keys only.
  * The schemas are CLASSIC and local-channel. Cloud payloads are
    a different serialisation with different names, so a field absent
    from them means nothing about cloud code. That mistake has already
    been made once with this file; see its `_channel` note.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "roomba_plus"

#: Sits in the sibling library, which is where the vendor research lives.
SCHEMA_CANDIDATES = [
    ROOT.parent / "roombapy_prime_repo" / "docs" / "internal"
    / "vendor_schemas_ruby_0_7_12.json",
    ROOT / "docs" / "vendor_schemas_ruby_0_7_12.json",
]


def _distinctive(key: str) -> bool:
    """Whether a name is recognisably a vendor wire key.

    Without this the audit drowns: `id`, `status`, `time`, `min` and
    `path` are all in the schema AND all over our own persisted
    structures, and run 2 reported 22 hits in `from_dict()` methods
    reading storage files this integration wrote itself.

    Vendor keys on this protocol are overwhelmingly camelCase or carry a
    known prefix, so that is the filter. It is a heuristic, and it will
    miss an all-lowercase vendor key -- `bbrun` and `sqft` among them.
    Stated rather than hidden.
    """
    return bool(re.search(r"[a-z][A-Z]", key)) or (
        key.startswith(("bb", "pmap")) and len(key) > 4
    )


def _guarded_keys(fn: ast.AST) -> set[str]:
    """Keys this function tests before use, by any recognised means."""
    found: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        ):
            found.add(node.left.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def main() -> int:
    schema_path = next((p for p in SCHEMA_CANDIDATES if p.exists()), None)
    if schema_path is None:
        print(
            "Vendor schema file not found. It lives in the roombapy-prime "
            "repository, checked out beside this one:\n  "
            + "\n  ".join(str(p) for p in SCHEMA_CANDIDATES)
        )
        return 0  # Not a failure: the audit is optional by design.

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    properties = set(schema["properties"])
    required: set[str] = set()
    for names in schema["required"].values():
        required.update(names)

    findings: list[tuple[str, str, int, str]] = []
    read_anywhere: set[str] = set()

    for path in sorted(COMPONENT.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in properties
            ):
                read_anywhere.add(node.args[0].value)

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            guarded = _guarded_keys(fn)
            for node in ast.walk(fn):
                if not (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                    and isinstance(node.ctx, ast.Load)
                ):
                    continue
                key = node.slice.value
                if key in properties:
                    read_anywhere.add(key)
                if (
                    key in properties
                    and key not in required
                    and key not in guarded
                    and _distinctive(key)
                ):
                    findings.append((key, path.name, node.lineno, fn.name))

    print(f"Vendor schema: {schema_path.name}")
    print(f"  {len(properties)} properties, {len(required)} required somewhere")
    print(f"  {len(read_anywhere)} of them read by this integration\n")

    if findings:
        print(f"{len(findings)} unguarded read(s) of a not-required field:\n")
        for key, filename, line, fn in sorted(findings):
            print(f"  {key:20} {filename}:{line}  in {fn}()")
        print(
            "\nEach needs looking at by hand. Every hit found so far has been "
            "a read of a dict this code built itself -- check that before "
            "changing anything."
        )
    else:
        print("No unguarded reads of not-required vendor fields.")

    never_read = sorted(required - read_anywhere)
    print(f"\n{len(never_read)} required field(s) never read here:")
    print("  " + ", ".join(never_read))
    print(
        "\nMostly expected -- BLE tracking, provisioning, teleoperation and "
        "the j9 spray schedule are outside this integration. `coords` and "
        "`conType` are the ones worth noticing: they are the rrtp position "
        "request, which is on the roadmap rather than missing by accident."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
