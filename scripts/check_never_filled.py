"""An attribute created empty and never filled is a feature that does nothing.

FOUR OF THESE IN ONE DAY, all found by users rather than by tests:

  - `cleaned_in_room` was declared, read in the room-advance condition,
    reset after an advance -- and assigned True nowhere. The route it
    guarded was unreachable, so the room display never moved
    (@ScenicSystemsLLC).
  - `_zone_polygons` was initialised to {} and read by three separate
    passes -- bounding box, outlines, labels. Zones were not drawn at
    all (@chairstacker).
  - `prime_map_versions` did not exist: the value was read from the
    cloud in two places and stored in neither, so a diagnostics download
    could not show which map the robot was on -- and its absence was
    repeatedly misread as the robot not reporting one.
  - `last_known_map_id` was written only from a live reading, so a robot
    that had been docked since startup had nothing to fall back on.

WHAT THIS COVERS, honestly: the first two. `prime_map_versions` was a
value never stored anywhere, and `last_known_map_id` was written under a
condition that rarely held -- neither has a dead initialiser to find.
The first version of this file claimed all four and covered one.

EVERY ONE PASSED THE TEST SUITE. Tests check what a function does with
its input, not whether it is ever given one -- so an empty container
flows through every assertion without complaint.

WHAT COUNTS AS FILLING IT: a later assignment, an item assignment
(`self._x[k] = v`), or a mutating call (`append`, `update`,
`setdefault`, ...). Anything else means the initialiser is the only
value it will ever have.

This stands at zero. An entry in ACCEPTED is a decision that some
attribute is deliberately always empty -- which is worth having to
write down.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path("custom_components/roomba_plus")

#: Methods that put something into a container.
_MUTATORS = frozenset({
    "append", "extend", "add", "update", "setdefault",
    "insert", "pop", "discard", "clear", "remove",
})

#: {"module.py::Class.attribute": reason}. Empty is the goal.
ACCEPTED: dict[str, str] = {}


def _is_empty_literal(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return not node.elts
    return False


def _self_attr(node: ast.expr) -> str | None:
    """The attribute name in `self.<name>`, else None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _scan_class(cls: ast.ClassDef) -> list[str]:
    created_empty: set[str] = set()
    filled: set[str] = set()

    for node in ast.walk(cls):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value

        if target is not None:
            name = _self_attr(target)
            if name is not None:
                if value is not None and _is_empty_literal(value):
                    created_empty.add(name)
                else:
                    filled.add(name)

            # self._x[key] = value
            if isinstance(target, ast.Subscript):
                inner = _self_attr(target.value)
                if inner is not None:
                    filled.add(inner)

        # self._x.append(...) and friends
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _MUTATORS
        ):
            inner = _self_attr(node.func.value)
            if inner is not None:
                filled.add(inner)

    return sorted(created_empty - filled)


def _scan_closure(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Locals that are declared `nonlocal` and only ever given one value.

    THE MOST DAMAGING OF THE FOUR WAS NOT AN ATTRIBUTE.
    `cleaned_in_room` was a local in a callback factory, declared
    `nonlocal` in the inner handler, read in the room-advance condition,
    and assigned False -- and nothing else, anywhere. The condition that
    depended on it could not become true, so the route it guarded was
    dead.

    An attribute scan cannot see that, which is worth stating plainly:
    the first version of this check claimed the case and did not cover
    it.

    The signal is a variable listed in a `nonlocal` statement -- the
    author meant it to change across calls -- whose every assignment
    gives the same constant.
    """
    # SCANNED AT THE OUTER FUNCTION, not at each inner one.
    #
    # Sibling closures share these: one sets the handle, another clears
    # it. Reading each separately sees a single value in both and calls
    # a perfectly good variable dead -- which the first version of this
    # check did, on two real ones.
    declared: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Nonlocal):
            declared.update(node.names)
    if not declared:
        return []

    values: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        # A for-loop target genuinely varies.
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in declared:
                continue
            value = getattr(node, "value", None)
            if isinstance(node, (ast.For, ast.AsyncFor, ast.AugAssign)):
                values.setdefault(target.id, set()).add("<varies>")
            elif isinstance(value, ast.Constant):
                values.setdefault(target.id, set()).add(repr(value.value))
            else:
                values.setdefault(target.id, set()).add("<varies>")

    return sorted(
        name for name, seen in values.items()
        if len(seen) == 1 and "<varies>" not in seen
    )


def main() -> int:
    findings: list[str] = []
    scanned = 0

    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        _inner: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if child is not node and isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        _inner.add(id(child))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(node) in _inner:
                continue  # scanned as part of its enclosing function
            for name in _scan_closure(node):
                key = f"{path.name}::{node.name}() nonlocal {name}"
                if key not in ACCEPTED:
                    findings.append(f"  {key}")

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            scanned += 1
            for attribute in _scan_class(node):
                key = f"{path.name}::{node.name}.{attribute}"
                if key in ACCEPTED:
                    continue
                findings.append(f"  {key}")

    if not findings:
        print(
            f"OK: {scanned} classes, no attribute created empty and "
            f"never filled."
        )
        return 0

    print(f"\n{len(findings)} attribute(s) created empty and never filled:\n")
    print("\n".join(findings))
    print(
        "\nEvery read of these gets the empty initialiser, so whatever "
        "depends on\nthem does nothing -- silently, and with the tests "
        "still passing. Fill it,\nremove it, or record in ACCEPTED why "
        "it is deliberately always empty.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
