"""Every delayed save has a flush that runs when its owner goes.

WHY.

`Store.async_delay_save` coalesces a burst of writes into one, some
seconds or minutes later. On a reload the old object's pending write can
then land after the new object has loaded -- overwriting fresh state with
stale, which is worse than losing the update. `Store.async_save` writes
now and cancels the pending timer, so calling it on unload or removal
closes the race.

`docs/ARCHITECTURE.md` recorded this as a known weakness with "nothing
enforces it": the mission timer got its flush in v3.3.0, the Prime
unload path and the Prime map each copied the delayed save without it,
and the independent review of 28.11. found two more (the path-turn
counts, five minutes; the Prime live bundle, five seconds).

WHAT IT CHECKS.

For every class that calls `async_delay_save`: the class has a method
that calls `async_save`, and that method is either the entity's
`async_will_remove_from_hass` or is called from somewhere else in the
package (the unload). A class whose delayed write is safe to lose goes
on `NO_FLUSH_NEEDED` with the reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Classes whose delayed write needs no flush, keyed "module.Class".
NO_FLUSH_NEEDED: dict[str, str] = {}


def _calls(node: ast.AST, attr: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
        for n in ast.walk(node)
    )


def _called_names(package: Path) -> dict[str, set[str]]:
    """Method name -> the modules that call `<something>.<name>(`."""
    found: dict[str, set[str]] = {}
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                found.setdefault(node.func.attr, set()).add(path.stem)
    return found


def problems(package: Path) -> list[str]:
    calls = _called_names(package)
    found: list[str] = []
    seen_listed: set[str] = set()
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if not _calls(cls, "async_delay_save"):
                continue
            key = f"{path.stem}.{cls.name}"
            if key in NO_FLUSH_NEEDED:
                seen_listed.add(key)
                continue
            flushes = [
                m for m in cls.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _calls(m, "async_save")
            ]
            reached = [
                m.name for m in flushes
                if m.name == "async_will_remove_from_hass"
                or (calls.get(m.name, set()) - {path.stem})
            ]
            if not reached:
                names = ", ".join(m.name for m in flushes) or "none"
                found.append(
                    f"{path.name}:{cls.lineno} {cls.name} saves delayed; "
                    f"methods that write now: {names}; none runs on unload or removal"
                )
    found.extend(
        f"NO_FLUSH_NEEDED lists {key}, which saves nothing delayed any more"
        for key in sorted(set(NO_FLUSH_NEEDED) - seen_listed)
    )
    return found


def main() -> int:
    found = problems(PACKAGE)
    if not found:
        print("Every delayed save has a flush on unload or removal.")
        return 0
    print("Delayed saves without a flush:\n")
    for line in found:
        print(f"  {line}")
    print(
        "\nA reload can land the old object's pending write after the new one"
        "\nloaded. Add a method that calls Store.async_save and run it from"
        "\nasync_will_remove_from_hass or the unload, or list the class in"
        "\nNO_FLUSH_NEEDED with the reason its write may be lost."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
