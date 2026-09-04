"""How many cloud requests each entity costs per day.

WHY THIS IS A TEST AND NOT A REVIEW STEP.

Two bugs in one session came from asking "what does this cost per day?"
rather than from anything failing:

  - the schedule switches read from the cloud in `async_update`, and
    SwitchEntity polls every 30 seconds by default. Three schedules
    meant roughly 8,600 requests a day for data that changes when
    somebody edits a schedule in the iRobot app.
  - the Prime room map called `get_map_metadata()` twice per refresh,
    for identical data, while the comment beside the second call claimed
    it reused the first.

Both were functionally correct. Every test passed. Nothing in the code
looked wrong, and neither would ever have failed on its own -- the
symptom is a rate limit or a slow account, months later, on somebody
else's robot.

WHAT IT CHECKS.

Two properties, both static, both cheap:

  1. An entity that polls must not make a cloud call in async_update.
     Polling plus a network call is the expensive combination; either
     alone is fine.
  2. A refresh path must not call the same cloud method twice.

Static analysis rather than a simulated day: a fake clock would have to
model HA's scheduler, and the two real bugs are both visible in the
source. A cheap check that runs is worth more than an accurate one that
does not.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components" / "roomba_plus"

#: Methods on the robot objects that cross the network.
#:
#: Listed rather than inferred: `get_state` is a cloud call and
#: `get_polygons` is not, and no naming convention separates them.
_CLOUD_CALLS: frozenset[str] = frozenset({
    "get_schedules", "update_schedules", "create_schedules", "delete_schedule",
    "get_map_metadata", "get_active_map_versions", "get_map_geojson_link",
    "download_map_bundle", "get_favorites", "create_favorite",
    "update_favorite", "delete_favorite", "order_favorite",
    "get_mission_history", "get_time_estimates", "get_dnd_settings",
    "set_dnd_settings", "get_consumables", "get_user_households",
    "set_setting", "edit_map", "set_map_name", "set_map_orientation",
    "get_notifications", "get_robot_serial_data",
})

#: Entities that poll AND make cloud calls, with a reason.
#:
#: Empty by design. An entry here is a claim that the cost is worth it,
#: and should state the interval and the resulting daily figure.
_POLLING_EXCEPTIONS: dict[str, str] = {
    "calendar.py::PrimeScheduleCalendar": (
        "SCAN_INTERVAL is 15 minutes rather than HA's 30 seconds, so two "
        "cloud calls per update comes to 192 requests a day instead of "
        "5,760. A calendar genuinely has to poll -- there is no event for "
        "'somebody edited a schedule in the iRobot app' -- so the interval "
        "is the lever, not the polling itself."
    ),
}


def _cloud_wrappers(tree: ast.AST) -> set[str]:
    """Module-level functions that make a cloud call.

    ONE HOP, not a full call graph. The real bug this misses without it:
    the schedule switch's async_update calls
    `async_read_schedule_containers()`, which calls `get_schedules()`.
    Looking only at the method body sees no cloud call and reports the
    entity as free.

    Deliberately not recursive. Two hops would need cycle handling and
    cross-module resolution for a pattern that has not appeared; one hop
    covers the observed shape, and a check that is wrong in an
    understood way beats one that is wrong in an unknown way.
    """
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            name = target.attr if isinstance(target, ast.Attribute) else None
            if name in _CLOUD_CALLS:
                wrappers.add(node.name)
                break
    return wrappers


def _cloud_calls_in(node: ast.AST, wrappers: frozenset[str] = frozenset()) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        if isinstance(target, ast.Attribute):
            name = target.attr
        elif isinstance(target, ast.Name):
            name = target.id
        else:
            continue
        if name in _CLOUD_CALLS or name in wrappers:
            found.append((name, child.lineno))
    return found


def _disables_polling(cls: ast.ClassDef) -> bool:
    for node in cls.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_attr_should_poll":
                return isinstance(node.value, ast.Constant) and node.value.value is False
    return False


def _method(cls: ast.ClassDef, name: str) -> ast.AST | None:
    return next((m for m in cls.body if getattr(m, "name", None) == name), None)


def main() -> int:
    polling_findings: list[str] = []
    duplicate_findings: list[str] = []
    checked = 0

    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(call in source for call in _CLOUD_CALLS):
            continue
        tree = ast.parse(source)
        wrappers = frozenset(_cloud_wrappers(tree))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            # 1. Polling entity making cloud calls in async_update.
            update = _method(node, "async_update")
            if update is not None and not _disables_polling(node):
                calls = _cloud_calls_in(update, wrappers)
                key = f"{path.name}::{node.name}"
                # An exception has to state its interval and the daily
                # figure. "It is fine" is not an entry; a number is.
                if calls and key not in _POLLING_EXCEPTIONS:
                    names = ", ".join(sorted({c for c, _ in calls}))
                    polling_findings.append(
                        f"  {key}\n"
                        f"      polls by default AND calls {names} in async_update\n"
                        f"      -> roughly 2,880 requests per entity per day"
                    )

            # 2. The same cloud method twice in one method body.
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                checked += 1
                seen: dict[str, int] = {}
                for name, lineno in _cloud_calls_in(member):
                    if name in seen:
                        duplicate_findings.append(
                            f"  {path.name}::{node.name}.{member.name}\n"
                            f"      calls {name!r} twice "
                            f"(lines {seen[name]} and {lineno})"
                        )
                    else:
                        seen[name] = lineno

    print(f"Checked {checked} method(s) for cloud request cost.")

    if not polling_findings and not duplicate_findings:
        print("OK: no polling entity makes cloud calls, no duplicated requests.")
        return 0

    if polling_findings:
        print(f"\n{len(polling_findings)} polling entity/entities making cloud calls:\n")
        print("\n".join(polling_findings))
        print(
            "\n  Either set _attr_should_poll = False and refresh on an event,\n"
            "  or add an entry to _POLLING_EXCEPTIONS stating the interval and\n"
            "  the daily figure it produces."
        )

    if duplicate_findings:
        print(f"\n{len(duplicate_findings)} duplicated cloud request(s):\n")
        print("\n".join(duplicate_findings))
        print("\n  Fetch once and pass the result, rather than asking twice.")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
