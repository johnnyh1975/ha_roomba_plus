"""Which late imports guard a real cycle, and which are cargo cult.

WHY THIS RUNS BEFORE ANY MODULE IS SPLIT.

45 imports in this package sit inside functions with `# noqa: PLC0415`.
The assumption going in was that each one avoids a circular import, and
that they therefore map the places where the module boundaries are
wrong.

That assumption needed checking before `image.py` gets touched:
splitting a module while misreading its cycles moves the problem instead
of removing it.

WHAT IT CHECKS.

For every late project-internal import A -> B, whether B imports A back
at module level (including under TYPE_CHECKING). If it does not, there
is no cycle for the late import to be avoiding.

The answer is not a refactoring instruction. A late import can be
correct for other reasons -- an expensive module used on one code path,
an optional dependency, a platform module that should not load for the
other generation. This separates "guards a cycle" from "needs a
different reason", and prints the ones that need one.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Late imports that are deliberate for a reason other than cycles.
#:
#: Keyed "importer -> imported". Each needs a reason that is not "avoids
#: a circular import", because this script has established that they do
#: not.
NON_CYCLE_REASONS: dict[str, str] = {
    "image -> map_renderer": (
        "pulls in PIL through the renderer. Deferred so a Home Assistant "
        "instance with the map disabled does not pay for it at startup"
    ),
    "image -> prime_room_map": (
        "Prime-only path inside a module shared by both generations"
    ),
    "button -> button_prime": "Prime-only entities, built only on the Prime branch",
    "__init__ -> button_prime": "as button -> button_prime",
    "switch -> prime_schedule_switch": "Prime-only entities",
    "select -> select_prime": "Prime-only entities",
    "select -> prime_coordinator": "capability flags, read only on the Prime branch",
    "diagnostics -> prime_coordinator": "Prime-only diagnostics section",
    "services -> button_prime": "run_favorite is a Prime-only action",
    "services -> room_cleaning": (
        "the cleaning facade, reached only when a service actually runs"
    ),
    "vacuum -> room_cleaning": "as services -> room_cleaning",
    "device_tracker -> room_cleaning": "as services -> room_cleaning",
    "device_tracker -> area_resolver": (
        "only needed when an area is actually resolved, which is not on "
        "every state read"
    ),
    "prime_coordinator -> prime_mission_sync": (
        "the sync runs on one coordinator refresh in six hours"
    ),
    "room_cleaning -> prime_mission_sync": (
        "estimates are read once per mission start"
    ),
    "sensor -> sensor_rooms": (
        "the mission-progress entity, created on one branch only"
    ),
}


def _module_level_imports(tree: ast.AST, local: set[str]) -> set[str]:
    """Project imports at module level, including under TYPE_CHECKING."""
    found: set[str] = set()

    def _add(node: ast.ImportFrom) -> None:
        module = (node.module or "").lstrip(".").split(".")[0]
        if module in local:
            found.add(module)

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            _add(node)
        elif isinstance(node, ast.If):
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    _add(child)
    return found


def _late_imports(path: Path, local: set[str]) -> set[str]:
    source = path.read_text(encoding="utf-8")
    if "PLC0415" not in source:
        return set()
    lines = source.splitlines()
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if "PLC0415" not in lines[node.lineno - 1]:
            continue
        module = (node.module or "").lstrip(".").split(".")[0]
        if module in local:
            found.add(module)
    return found


def main() -> int:
    local = {p.stem for p in PACKAGE.glob("*.py")}
    top_level: dict[str, set[str]] = {}
    for path in PACKAGE.glob("*.py"):
        top_level[path.stem] = _module_level_imports(
            ast.parse(path.read_text(encoding="utf-8")), local
        )

    real_cycles: list[str] = []
    unexplained: list[str] = []
    explained = 0

    for path in sorted(PACKAGE.glob("*.py")):
        for target in sorted(_late_imports(path, local)):
            key = f"{path.stem} -> {target}"
            if path.stem in top_level.get(target, set()):
                real_cycles.append(key)
            elif key in NON_CYCLE_REASONS:
                explained += 1
            else:
                unexplained.append(key)

    print(
        f"Late project-internal imports: {len(real_cycles)} guard a real cycle, "
        f"{explained} have another documented reason, "
        f"{len(unexplained)} have none."
    )

    if real_cycles:
        print("\nGuarding a genuine circular import:\n")
        for entry in real_cycles:
            print(f"  {entry}")
        print(
            "\n  These mark boundaries that are actually wrong. A module split\n"
            "  should aim at these first."
        )

    if unexplained:
        print(f"\n{len(unexplained)} late import(s) with no stated reason:\n")
        for entry in unexplained:
            print(f"  {entry}")
        print(
            "\n  None of these avoids a cycle -- the imported module does not\n"
            "  import back. Either give a reason in NON_CYCLE_REASONS, or move\n"
            "  the import to the top of the file where it belongs."
        )
        return 1

    if not real_cycles:
        print(
            "\nNo late import in this package guards a real circular import.\n"
            "\n"
            "That is the finding this script was written to test, and it is\n"
            "the opposite of what was assumed. The module boundaries are not\n"
            "tangled; the late imports are load-time and branch-scoping\n"
            "decisions, which are different problems with different fixes.\n"
            "\n"
            "Consequence for the refactoring plan: the image.py split does not\n"
            "have to untangle anything first."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
