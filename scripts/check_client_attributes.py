#!/usr/bin/env python3
"""Fail if we read an attribute roombapy's client does not have.

WHY MYPY CANNOT DO THIS. `IRobotEntity.__init__` takes `roomba: Any`,
and every entity keeps it as `self.vacuum`. `Any` silences the type
checker completely, so `self.vacuum.whatever_you_like` passes -- across
all 25 entity modules.

That blind spot cost two real bugs in the 4.2 migration, both found by
hand after mypy reported a clean run:

  * `self.vacuum.roomba_connected` in binary_sensor.py -- renamed to
    `connected` in roombapy 2.x. The connectivity sensor would have
    raised AttributeError on every state read.
  * `roomba.client_error` in diagnostics.py -- replaced by `error_code`
    and `error_message`. Every diagnostics download would have failed.

Neither is visible to the test suite either: the robot is mocked, and a
MagicMock answers any attribute at all.

The check is simple enough to state in one line: for every attribute
read off something named like the client, ask the real class whether it
has one.

    python scripts/check_client_attributes.py

WHAT IT CANNOT SEE. The receiver is matched by NAME -- `self.vacuum`,
`data.roomba`, `self._roomba` -- because the real object only exists at
runtime. Something reached through an alias this does not recognise
goes unchecked, and a mock attribute assigned in a test is invisible
either way. It narrows the blind spot rather than closing it.
"""
from __future__ import annotations

import ast
import pathlib
import sys

COMPONENT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "roomba_plus"
)

#: Attribute chains that hold a roombapy client at runtime.
RECEIVER_SUFFIXES = (".vacuum", ".roomba", "._roomba")
RECEIVER_NAMES = frozenset({"roomba", "vacuum"})

#: Names read off the client that are not roombapy's.
#:
#: Empty, and an entry here should be rare: it claims that something
#: named like the client is not the client.
ALLOWED: frozenset[str] = frozenset()


def main() -> int:
    try:
        from roombapy import RoombaClient
    except ImportError:
        print("roombapy is not installed; skipping the client attribute check.")
        return 0

    known = {name for name in dir(RoombaClient) if not name.startswith("__")}
    findings: dict[str, tuple[str, int]] = {}

    for path in sorted(COMPONENT.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            base = ast.unparse(node.value)
            is_client = base.endswith(RECEIVER_SUFFIXES) or base in RECEIVER_NAMES
            if not is_client:
                continue
            if node.attr.startswith("_") or node.attr in known:
                continue
            if node.attr in ALLOWED:
                continue
            findings.setdefault(f"{base}.{node.attr}", (path.name, node.lineno))

    if not findings:
        print(
            f"OK: every attribute read off the robot client exists on "
            f"RoombaClient ({len(known)} public names)."
        )
        return 0

    print(
        f"{len(findings)} attribute(s) read off the client that "
        f"RoombaClient does not have:\n"
    )
    for expression, (filename, line) in sorted(findings.items()):
        print(f"  {filename}:{line}  {expression}")
    print(
        "\nEach raises AttributeError against a real robot, and neither "
        "mypy nor the tests can see it: the client is typed `Any` on the "
        "entity base, and a mocked robot answers any attribute.\n"
        "\nCheck what roombapy calls it now. If the receiver is genuinely "
        "not the client, add the name to ALLOWED with that reasoning."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
