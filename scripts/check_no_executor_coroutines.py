#!/usr/bin/env python3
"""Fail if a roombapy coroutine is handed to `async_add_executor_job`.

WHY THIS EXISTS, AND WHY NEITHER TESTS NOR MYPY COVER IT.

roombapy 2.x made `send_command`, `set_preference`, `connect`,
`disconnect`, `RoombaDiscovery.get`/`get_all` and
`RoombaPassword.get_password` coroutines. Passing one to
`hass.async_add_executor_job()` is accepted everywhere and does nothing:
the executor calls the function in a worker thread, gets a coroutine
object back, and drops it. **The command never reaches the robot.**

Nothing catches it:

  * **mypy does not.** `async_add_executor_job(target: Callable[..., T])`
    binds T to `Coroutine[...]` and type-checks cleanly. During the 4.2
    migration it flagged 8 of 46 such calls -- the 8 that were bare, not
    the 38 inside an executor.
  * **The test suite does not.** Every test mocks the robot, and a
    MagicMock answers a coroutine call exactly as happily as a plain
    one. All 6197 tests passed against a build where 46 commands could
    not work.

So the only thing standing between this mistake and a user is this
script. It reads the source rather than running anything, which makes it
fast and total: `set_preference` appears under four different receiver
spellings and `send_command` under five, and a grep for one of them
finds a fraction.

Run it directly, or let CI do it:

    python scripts/check_no_executor_coroutines.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"

#: Methods that are coroutines in roombapy 2.x.
#:
#: Named rather than detected, because the receiver is a runtime object
#: this script never sees -- `self.vacuum`, `data.roomba`,
#: `entity.vacuum`, `roomba_pw` and so on all reach the same library.
#:
#: `get` and `get_all` are RoombaDiscovery's. They are common words and
#: could match something unrelated; the false positive is a nuisance and
#: the false negative is a robot that never gets discovered, so they
#: stay in and anything genuinely unrelated goes in ALLOWED below.
#: Kept complete by checking the library rather than memory:
#:
#:     python -c "import inspect, roombapy; [print(n, [m for m in dir(c)
#:       if inspect.iscoroutinefunction(getattr(c, m, None))])
#:       for n in roombapy.__all__ if inspect.isclass(c := getattr(roombapy, n))]"
#:
#: `get_position` and `aclose` were missing from a first version of this
#: list, written from memory of the migration rather than from that
#: command. Neither is used yet -- `get_position` is the rrtp reader and
#: is on the roadmap -- so nothing was broken, but a guard with a
#: hand-remembered list guards only what its author remembered.
ROOMBAPY_COROUTINES = frozenset({
    "aclose",
    "connect",
    "disconnect",
    "get",
    "get_all",
    "get_password",
    "get_position",
    "send_command",
    "set_preference",
    "set_preferences",
})

#: (file, line-independent receiver text) pairs that are NOT roombapy.
#:
#: Empty, and meant to stay that way. An entry here is a claim that some
#: other object happens to share a method name with the robot client --
#: worth writing down explicitly rather than loosening the match.
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _offenders() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in sorted(COMPONENT.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "async_add_executor_job"
                and node.args
            ):
                continue
            target = node.args[0]
            # `partial(fn, ...)` hides the callee one level down.
            if (
                isinstance(target, ast.Call)
                and isinstance(target.func, ast.Name)
                and target.func.id == "partial"
                and target.args
            ):
                target = target.args[0]
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr not in ROOMBAPY_COROUTINES:
                continue
            text = ast.unparse(target)
            if (path.name, text) in ALLOWED:
                continue
            found.append((path.name, node.lineno, text))
    return found


def main() -> int:
    offenders = _offenders()
    if not offenders:
        print(
            "OK: no roombapy coroutine is handed to async_add_executor_job."
        )
        return 0

    print(
        f"{len(offenders)} call(s) run a roombapy coroutine in an executor.\n"
        "Each of these silently does nothing -- the thread gets a coroutine "
        "object back and drops it, and the robot never sees the command:\n"
    )
    for filename, line, text in offenders:
        print(f"  {filename}:{line}  {text}")
    print(
        "\nReplace `await hass.async_add_executor_job(x.method, a, b)` with "
        "`await x.method(a, b)`.\n"
        "\nIf one of these is genuinely not the robot client -- some other "
        "object with a method of the same name -- add it to ALLOWED with "
        "that reasoning, rather than narrowing the match."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
