"""Attributes read off `runtime_data` must be attributes it has.

WHY THIS EXISTS.

`no_contact` shipped in a46 and could not fire on a single Prime robot,
because the check read `coordinator.last_message_ts` — an attribute
neither coordinator has. What both of them write is
`runtime_data.last_mqtt_message_ts`, which diagnostics, the binary
sensors and the Classic mirror of that same check all read correctly.

`getattr(obj, "phantom", None)` returns None and the code carries on.
A type guard added for safety then turned None into "never stale", so
the feature was silently inert on one generation while working on the
other, in the release that introduced it. @utkjmitch found it by
reading the source after his robot kept saying `stuck`.

That is the third instance of the shape this week — `zone_layers` read
off a dict, `pd_state` read as `state`, and now this — and the second
one shipped in the same release as a guard script and a release-notes
section naming five others. `check_prime_sources.py` cannot see it: the
object is right, only the attribute is imagined.

WHAT THIS CHECKS.

Every `getattr(x, "name", ...)` and `x.name` where `x` is or comes from
`runtime_data` must name a field the runtime data class actually
declares. A default value is not a licence to invent an attribute; it
is what makes an invented one invisible.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

INTEGRATION = (
    Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
)

#: Names that look like runtime data in a local variable.
_RUNTIME_ALIASES = {"runtime_data", "data", "_data", "runtime", "_runtime"}

#: Attributes read off runtime data that are not declared on it, with a
#: reason. As in `check_late_imports.py`: a rule with no way to be
#: excused gets deleted the first time it is inconvenient.
_ALLOWED: dict[str, str] = {}


def _declared_fields() -> set[str]:
    """Field names on the runtime data dataclass, from models.py."""
    tree = ast.parse((INTEGRATION / "models.py").read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # The runtime data class is `RoombaData`; `Context` catches the
        # setup-time shape that carries the same fields.
        if node.name not in ("RoombaData",) and "Context" not in node.name:
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(
                item.target, ast.Name
            ):
                fields.add(item.target.id)
            elif isinstance(item, ast.FunctionDef):
                fields.add(item.name)
    return fields


def _phantom_reads(path: Path, declared: set[str]) -> list[str]:
    """`getattr(<runtime-ish>, "name", ...)` where name is not declared."""
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            continue

        target, name = node.args[0], node.args[1].value

        # Only reads whose object is visibly runtime data. A name we
        # cannot resolve is left alone rather than guessed at -- a
        # guard that fires on things it does not understand gets
        # switched off.
        base = None
        if isinstance(target, ast.Name):
            base = target.id
        elif isinstance(target, ast.Attribute):
            base = target.attr
        if base not in _RUNTIME_ALIASES:
            continue

        if name in declared or f"{path.name}:{name}" in _ALLOWED:
            continue
        found.append(f"  {path.name}:{node.lineno} reads `{name}`")
    return found


def main() -> int:
    declared = _declared_fields()
    if not declared:
        print("Could not read runtime data fields from models.py.")
        return 1

    problems: list[str] = []
    for path in sorted(INTEGRATION.glob("*.py")):
        if path.name == "models.py":
            continue
        problems.extend(_phantom_reads(path, declared))

    if problems:
        print("Attributes read off runtime data that it does not declare:")
        print("\n".join(sorted(set(problems))))
        print(
            "\n`getattr(x, 'phantom', None)` returns None and the code "
            "carries on. That is how `no_contact` shipped inert on every "
            "Prime robot in the release that added it.\n"
            "\nIf a read is deliberate, add it to _ALLOWED with a reason."
        )
        return 1

    print(
        f"OK: runtime data reads checked against "
        f"{len(declared)} declared field(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
