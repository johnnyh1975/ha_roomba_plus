"""Every repair issue about one robot carries that robot's entry id.

WHY.

Until 4.6.12 four repair issues had one id for every robot: the runtime
counter reset, the cloud's obstacle zones, the new-rooms notice and the
layout-change areas. With two robots, one robot's check cleared or
replaced the other's notice, and the layout check deleted the areas the
other robot had found -- they were "no longer a candidate" on a map they
never came from. Nothing failed: each test built one robot.

After the fix, the independent review of 28.11. put one of the ids back
to a shared one on a copy of the repository, and the whole suite stayed
green. A rule nobody checks comes back.

WHAT IT CHECKS.

Every call to `async_create_issue` in the package. The issue id -- the
third positional argument or `issue_id=` -- passes when its expression
mentions `entry_id`, directly or through the local names it is built
from (`issue_id = f"{prefix}{x}"`, `prefix = some_prefix(entry.entry_id)`).
An issue that is about no single robot goes on `NOT_PER_ROBOT` with the
reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Issues that are about no single robot, keyed "module.function".
NOT_PER_ROBOT: dict[str, str] = {
    "repairs.async_check_core_roomba_conflict": (
        "About the installation, not a robot: Home Assistant's own Roomba "
        "integration or a wrong roombapy is loaded. One notice for all, "
        "and the robots it concerns are named in the text."
    ),
    "repairs.async_check_favorite_multi_command": (
        "Keyed by the favourite's own id, which iRobot gives once per "
        "account and favourite; the favourite belongs to the account, not "
        "to one of the robots on it."
    ),
}

_MAX_DEPTH = 4


def _issue_id_arg(call: ast.Call) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == "issue_id":
            return keyword.value
    return call.args[2] if len(call.args) >= 3 else None


def _assignments(func: ast.AST) -> dict[str, list[ast.expr]]:
    found: dict[str, list[ast.expr]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found.setdefault(node.target.id, []).append(node.value)
    return found


def _mentions_entry(expr: ast.expr, names: dict[str, list[ast.expr]], depth: int = 0) -> bool:
    if "entry_id" in ast.unparse(expr):
        return True
    if depth >= _MAX_DEPTH:
        return False
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in names:
            if any(_mentions_entry(v, names, depth + 1) for v in names[node.id]):
                return True
    return False


def _is_create_issue(call: ast.Call) -> bool:
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name == "async_create_issue"


def problems(package: Path) -> list[str]:
    found: list[str] = []
    listed_seen: set[str] = set()
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = _assignments(func)
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and _is_create_issue(node)):
                    continue
                arg = _issue_id_arg(node)
                if arg is None:
                    continue
                key = f"{path.stem}.{func.name}"
                if key in NOT_PER_ROBOT:
                    listed_seen.add(key)
                    continue
                if _mentions_entry(arg, names):
                    continue
                found.append(f"{path.name}:{node.lineno} {key}: {ast.unparse(arg)}")
    found.extend(
        f"NOT_PER_ROBOT lists {key}, which raises no issue any more"
        for key in sorted(set(NOT_PER_ROBOT) - listed_seen)
    )
    return found


def main() -> int:
    found = problems(PACKAGE)
    if not found:
        print("Every repair issue about a robot carries its entry id.")
        return 0
    print("Repair issues whose id does not carry the robot's entry id:\n")
    for line in found:
        print(f"  {line}")
    print(
        "\nWith two robots such an issue is shared: one robot's check clears or"
        "\nreplaces the other's. Put the entry id into the id, or add the"
        "\nfunction to NOT_PER_ROBOT with the reason it is about no single robot."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
