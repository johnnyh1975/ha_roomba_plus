"""No new `except Exception` that swallows an error without a word.

WHY.

A broad `except` that only passes, continues or returns keeps a failure
out of every log, the debug capture included. A mission recording
replays the same code, so the failure stays silent in the replay too:
nothing in a report can point at it. The review of 28.11. named the four
modules of the mission path; 4.7.0b1 gave their seven silent broad
handlers a debug line naming the robot (plan 4.7-10).

A RATCHET, NOT A CLEAN-UP (guideline 10). The rest of the package keeps
the count it has in `KNOWN`; a module may only go down. A narrow
`except (TypeError, ValueError)` around a conversion is not counted:
that is a value that is not a number, not a failure.

WHAT COUNTS AS SILENT. A handler for `Exception`, `BaseException` or
anything (bare `except:`) whose body is only `pass`, `continue`, or a
`return` of nothing or a constant.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Silent broad handlers each module still has (28.11.). Lower a number
#: when you give one a log line; a module at zero leaves the table.
KNOWN: dict[str, int] = {
    "calendar.py": 1,
    "command_record.py": 1,
    "command_watch.py": 1,
    "image.py": 1,
    "mission_queries.py": 1,
    "presence_manager.py": 1,
    "prime_coordinator.py": 1,
    "repairs.py": 2,
    "select.py": 4,
    "sensor_helpers.py": 1,
}

_BROAD = {"Exception", "BaseException"}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in _BROAD for t in types)


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return all(
        isinstance(stmt, (ast.Pass, ast.Continue))
        or (isinstance(stmt, ast.Return)
            and (stmt.value is None or isinstance(stmt.value, ast.Constant)))
        for stmt in handler.body
    )


def silent_handlers(path: Path) -> list[int]:
    """Line numbers of the silent broad handlers in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_silent(node)
    )


def problems(package: Path) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for path in sorted(package.glob("*.py")):
        lines = silent_handlers(path)
        allowed = KNOWN.get(path.name, 0)
        if lines or path.name in KNOWN:
            seen.add(path.name)
        if len(lines) > allowed:
            where = ", ".join(str(n) for n in lines)
            found.append(
                f"{path.name}: {len(lines)} silent broad except (lines {where}), "
                f"{allowed} allowed"
            )
        elif len(lines) < allowed:
            found.append(
                f"{path.name}: {len(lines)} silent broad except, KNOWN says {allowed}; "
                "lower it"
            )
    found.extend(
        f"KNOWN lists {name}, which does not exist any more"
        for name in sorted(set(KNOWN) - seen)
    )
    return found


def main() -> int:
    found = problems(PACKAGE)
    if not found:
        print("No new silent broad except.")
        return 0
    print("Silent broad except handlers:\n")
    for line in found:
        print(f"  {line}")
    print(
        "\nA broad except that only passes, continues or returns hides the"
        "\nfailure from every log and every replay. Log it at debug with the"
        "\nrobot's name (robot_log.robot_logger), catch the narrow exception"
        "\nyou expect, or -- for a count that went down -- lower KNOWN."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
