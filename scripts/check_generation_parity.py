"""Reports what one generation branch does that the other does not.

WHY THIS EXISTS.

Twenty modules branch on ConnectionType.CLOUD_ONLY, and nearly all of
them return early from the Prime side. Anything added to the Classic
path afterwards is not inherited, and nothing flags it.

Five gaps of exactly that shape were found in a single session:

  - MissionTimerStore was not flushed on unload for Prime, so a reload
    could overwrite fresh state with a stale delayed write
  - long-term statistics were never backfilled for Prime
  - the maintenance reset recorded an hour meter of 0, making every
    interval since a reset read as the robot's entire lifetime
  - Platform.SELECT was added to LOCAL_PLATFORMS instead of
    PRIME_PLATFORMS, so working code was unreachable
  - Platform.BUTTON, the same mistake ten minutes later

Each was found by asking, by hand, "what does the other branch do here
that this one does not?". This asks it mechanically.

WHAT IT DOES NOT DO.

It does not decide whether a difference is a bug. Most are correct --
Prime has no MQTT stream, no pose data, no local connection, so plenty
of Classic-only calls are exactly right. It compares call sets and
requires that every difference be listed as deliberate.

The point is not to eliminate differences. It is to make an
UNEXAMINED difference impossible: a new Classic-only call either gets
an entry here, or the check fails.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Calls that appear on the Classic side of a branch and deliberately
#: not on the Prime side, keyed by "module.py::function".
#:
#: Every entry needs a reason. "Prime does not have this" is a reason;
#: "not needed" is not, because it is what somebody would write while
#: overlooking a real gap.
#:
#: An unlisted difference fails the check. That is the whole mechanism:
#: it forces a decision rather than allowing an omission.
DELIBERATE: dict[str, dict[str, str]] = {
    "__init__.py::async_unload_entry": {
        "async_disconnect_or_timeout": (
            "closes the local MQTT connection. Prime disconnects its own "
            "PrimeRobot in its branch; there is no local socket to close"
        ),
        "cancel": "cancels Classic-only background tasks",
        "append": "builds the Classic platform list",
        "_remove_calendar_entity_if_disabled": (
            "the Prime branch unloads PRIME_PLATFORMS, which already "
            "excludes CALENDAR when the option is off"
        ),
        "async_unload_platforms": (
            "both branches call it, with different platform lists -- the "
            "AST sees only the Classic call site"
        ),
    },
    "vacuum.py::async_start": {
        "async_add_executor_job": (
            "Classic's roombapy is synchronous and needs the executor; "
            "roombapy-prime is async throughout"
        ),
    },
    "vacuum.py::async_return_to_base": {
        "async_add_executor_job": (
            "Classic's roombapy is synchronous and needs the executor"
        ),
        "async_pause": (
            "Classic needs an explicit pause before docking; the Prime "
            "dock command handles it"
        ),
        "range": "part of the Classic retry loop",
        "sleep": "part of the Classic retry loop",
    },
    "vacuum.py::_async_send_verb": {
        "async_add_executor_job": (
            "Classic's roombapy is synchronous and needs the executor"
        ),
    },
    "vacuum.py::activity": {
        "warning": "logs an unmapped Classic MQTT phase; Prime maps its own",
    },
    "device_tracker.py::_resolve_room": {
        "_resolve_smart_tier_room": "map_capability is NONE for Prime by design",
        "_resolve_ephemeral_tier_room": (
            "map_capability is NONE for Prime, so neither tier branch applies"
        ),
    },
    "device_tracker.py::_async_area_for": {
        "getattr": "reads cloud_coordinator.regions, which Prime has no equivalent for",
        "debug": "logs the Classic-side resolution failure",
    },
    "room_cleaning.py::async_get_room_cleaning_backend": {
        "ClassicRoomCleaning": "the branch exists to pick a backend per generation",
        "_classic_has_room_data": (
            "checks Classic cloud regions; the Prime branch has its own check"
        ),
    },
    "config_flow.py::async_step_settings": {
        "All": "Classic's form has numeric fields with ranges; Prime's does not",
        "Coerce": (
            "part of the numeric map-scale field, which Prime has no use for"
        ),
        "Range": (
            "bounds the Classic map size field, absent from the Prime form"
        ),
        "float": (
            "coerces the Classic map scale, a rendering concept Prime lacks"
        ),
        "SelectorEntitySelector": "correlation entities are a Classic concept",
        "SelectorEntitySelectorConfig": (
            "configures the correlation-entity picker, a Classic-only feature"
        ),
    },
    "blocking_manager.py::_do_start": {
        "async_add_executor_job": "Classic roombapy is synchronous",
        "async_call": "Classic-only service dispatch",
        "async_get": "Classic entity registry lookup",
        "async_get_entity_id": (
            "resolves a Classic entity for the blocking manager's own lookup"
        ),
        "info": (
            "logs the Classic start path; the Prime branch logs its own"
        ),
        "send_simple_command": (
            "Classic's own command path; Prime's locate button calls it "
            "from button_prime.py"
        ),
    },
}


def _calls_in(node: ast.AST) -> set[str]:
    """Function names called anywhere under a node.

    Attribute calls collapse to the attribute name -- `store.async_save`
    becomes `async_save`. Deliberate: the receiver differs between
    branches far more often than the operation does, and comparing
    `data.mission_timer_store.async_save` against
    `config_entry.runtime_data.mission_timer_store.async_save` would
    report a difference that is not one.
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        if isinstance(target, ast.Name):
            found.add(target.id)
        elif isinstance(target, ast.Attribute):
            found.add(target.attr)
    return found


def _enclosing_function(tree: ast.AST, lineno: int) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                return node.name
    return None


def _branch_bodies(tree: ast.AST) -> list[tuple[str, ast.If]]:
    """Every `if ... CLOUD_ONLY ...` statement, with its function name."""
    out: list[tuple[str, ast.If]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        try:
            test = ast.unparse(node.test)
        except Exception:  # noqa: BLE001
            continue
        if "CLOUD_ONLY" not in test:
            continue
        name = _enclosing_function(tree, node.lineno)
        if name:
            out.append((name, node))
    return out


def _classic_tail(function: ast.AST, branch: ast.If) -> set[str]:
    """Calls AFTER an early-returning Prime branch.

    This is the shape that produces the gaps: the Prime branch returns,
    and everything below it is Classic-only by accident rather than by
    decision.
    """
    end = branch.end_lineno or branch.lineno
    tail: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if node.lineno <= end:
            continue
        target = node.func
        if isinstance(target, ast.Name):
            tail.add(target.id)
        elif isinstance(target, ast.Attribute):
            tail.add(target.attr)
    return tail


def _returns_early(branch: ast.If) -> bool:
    return any(isinstance(n, ast.Return) for n in ast.walk(branch))


#: Functions where a difference is the POINT, not a risk.
#:
#: A platform's async_setup_entry exists to create different entities per
#: generation -- reporting that Classic creates a sensor Prime does not
#: is noise, and 89 of the first run's 141 findings were exactly that.
#:
#: What remains are the functions where both generations should do the
#: SAME housekeeping and one of them quietly does not: unload, teardown,
#: migration, diagnostics. Every gap found by hand in this project lived
#: in one of those.
#:
#: Matched as a suffix so `async_setup_entry` covers every platform.
_DIFFERENCE_IS_EXPECTED: tuple[str, ...] = (
    "async_setup_entry",
    "async_get_config_entry_diagnostics",
)


def _difference_expected(func_name: str) -> bool:
    return any(func_name.endswith(suffix) for suffix in _DIFFERENCE_IS_EXPECTED)


def main() -> int:
    findings: list[str] = []
    checked = 0
    skipped = 0

    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "CLOUD_ONLY" not in source:
            continue
        tree = ast.parse(source)

        for func_name, branch in _branch_bodies(tree):
            if _difference_expected(func_name):
                skipped += 1
                continue

            if not _returns_early(branch):
                # No early return means both paths continue into the
                # same tail -- nothing can be silently skipped.
                continue

            function = next(
                (
                    n
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == func_name
                ),
                None,
            )
            if function is None:
                continue

            checked += 1
            key = f"{path.name}::{func_name}"
            prime_calls = _calls_in(branch)
            classic_only = _classic_tail(function, branch) - prime_calls
            allowed = set(DELIBERATE.get(key, {}))

            for call in sorted(classic_only - allowed):
                findings.append(f"  {key}\n      Classic calls {call!r}, Prime does not")

    print(
        f"Checked {checked} early-returning CLOUD_ONLY branch(es); "
        f"skipped {skipped} where a difference is the point."
    )

    if not findings:
        print("OK: every generation difference is listed as deliberate.")
        return 0

    print(f"\n{len(findings)} unexamined difference(s):\n")
    print("\n".join(findings))
    print(
        "\nEach of these is either a real gap or a deliberate difference.\n"
        "Add deliberate ones to DELIBERATE in this script WITH A REASON.\n"
        "\n"
        "This check exists because five gaps of exactly this shape shipped\n"
        "in one session -- an unflushed store, a missing statistics\n"
        "backfill, an hour meter reading zero, and twice a platform added\n"
        "to the wrong list."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
