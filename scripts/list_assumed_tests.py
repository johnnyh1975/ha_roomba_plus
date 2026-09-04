"""Lists the tests that assert on values nobody has confirmed.

WHY.

4,564 tests pass. That number says nothing about how many of them
assert a fact and how many preserve a guess.

One concrete case: `assert call.body_json == {"assetId": "BLID123"}`
was green for months. `assetId` was a placeholder from when the wire key
was unknown; the real key is `robot_id`, found by tracing a native
format string. Nothing about the test looked wrong -- plausible value,
clear name, passing.

Worse than useless: it made the assumption HARDER to question, because
questioning it meant arguing with a green test.

WHAT THIS DOES.

Prints every test marked `@pytest.mark.assumed`, with its reason. Not a
gate -- an inventory. When a tester confirms a format, this says which
tests were guessing at it.

It also fails if a marker carries no reason, because "unconfirmed"
without saying what would settle it is how a marker becomes decoration.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def _marker_reason(decorator: ast.expr) -> str | None:
    """The reason string from @pytest.mark.assumed("..."), if any."""
    if not isinstance(decorator, ast.Call):
        return None
    target = decorator.func
    if not isinstance(target, ast.Attribute) or target.attr != "assumed":
        return None
    for arg in decorator.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return ""


def _is_bare_assumed(decorator: ast.expr) -> bool:
    """@pytest.mark.assumed with no parentheses at all."""
    return isinstance(decorator, ast.Attribute) and decorator.attr == "assumed"


def main() -> int:
    entries: list[tuple[str, str, str]] = []
    missing_reason: list[str] = []

    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if _is_bare_assumed(decorator):
                    missing_reason.append(f"{path.name}::{node.name}")
                    continue
                reason = _marker_reason(decorator)
                if reason is None:
                    continue
                if not reason.strip():
                    missing_reason.append(f"{path.name}::{node.name}")
                    continue
                entries.append((path.name, node.name, reason))

    if missing_reason:
        print("These @pytest.mark.assumed markers carry no reason:\n")
        for entry in missing_reason:
            print(f"  {entry}")
        print(
            "\nA marker without a reason says a test is uncertain and not what\n"
            "would settle it, which is how a marker becomes decoration."
        )
        return 1

    if not entries:
        print("No tests are marked as asserting on unconfirmed values.")
        print(
            "\nThat is either good news or an unused marker. If a wire format\n"
            "in this codebase is still a guess, the test asserting on it\n"
            "should say so."
        )
        return 0

    print(f"{len(entries)} test(s) assert on values that are not field-confirmed:\n")
    current = ""
    for filename, test_name, reason in entries:
        if filename != current:
            print(f"  {filename}")
            current = filename
        print(f"    {test_name}")
        print(f"      {reason}")
    print(
        "\nThese pass, and should. The list exists so a field report that\n"
        "confirms a format can be traced back to the tests that guessed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
