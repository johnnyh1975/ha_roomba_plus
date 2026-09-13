"""Reports what one room-cleaning command path does that another does not.

WHY THIS EXISTS.

There are four ways to tell a robot to clean a region:

    PrimeRoomCleaning.clean_rooms       service, Prime
    PrimeRoomCleaning.clean_segments    button/selector, Prime
    ClassicRoomCleaning.clean_rooms     service, Classic
    ClassicRoomCleaning.clean_segments  button/selector, Classic

They grew from two directions. The segment pair came from Home
Assistant's vacuum platform, where the user picks from a list we built;
the room pair is this project's own service, where the user types a
name. The name-resolution difference is genuine. Everything else --
which map a region is on, whether a zone must be sent differently,
whether the robot is even on that floor -- is the same question asked
four times.

Nothing kept the four answers in step, and they drifted:

  - the wrong-floor warning was added to two paths, then a third, and
    was missing from the fourth until somebody asked
  - the zone prefix was handled in three of four; `clean_zone` carried
    the mirror-image fault for a release
  - the capability filter reached the dropdown but not a service call

Each was found by hand, one at a time. This script asks the question
once.

WHAT IT IS NOT. It does not require all four to do the same thing --
some absences are correct, and those are recorded below with the reason.
It fails only on an absence nobody has written down.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Concern -> the marker that shows a path addresses it.
CONCERNS: dict[str, str] = {
    "wrong-floor warning": "where_the_robot_is()",
    "zone prefix": "ZID_PREFIX",
    "map-updating guard": "_raise_if_map_updating",
}

#: (concern, path) pairs that are absent ON PURPOSE, with the reason.
#: Anything not listed here and not present is reported.
ACCEPTED: dict[tuple[str, str], str] = {
    ("wrong-floor warning", "Prime.clean_segments"):
        "delegates to Prime.clean_rooms, which carries it",
    ("zone prefix", "Classic.clean_rooms"):
        "Classic segments carry their map in the id; the prefix is "
        "split in clean_segments before this is reached",
    ("map-updating guard", "Prime.clean_segments"):
        "delegates to Prime.clean_rooms, which carries it",
    ("map-updating guard", "Classic.clean_segments"):
        "the Classic guard lives in the send path shared with "
        "clean_rooms rather than at the entry point",
}


def _method_sources() -> dict[str, str]:
    """The four command paths, read from source rather than imported.

    IMPORTING WOULD NEED THE ROBOT LIBRARIES. This guard runs in the
    Stage 0 job, which installs nothing -- it exists to catch shape
    problems before anything heavier runs. An `import` here made it
    fail with `No module named 'roombapy'`, which is a broken guard
    rather than a finding.

    `check_generation_parity.py` next door reads the tree with `ast`
    for the same reason.
    """
    tree = ast.parse(
        Path("custom_components/roomba_plus/room_cleaning.py").read_text(
            encoding="utf-8"
        )
    )
    wanted = {
        "PrimeRoomCleaning": "Prime",
        "ClassicRoomCleaning": "Classic",
    }
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in wanted:
            continue
        for item in node.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name in ("clean_rooms", "clean_segments")
            ):
                out[f"{wanted[node.name]}.{item.name}"] = ast.unparse(item)
    return out


def main() -> int:
    sources = _method_sources()

    missing_paths = [
        name
        for name in (
            "Prime.clean_rooms", "Prime.clean_segments",
            "Classic.clean_rooms", "Classic.clean_segments",
        )
        if name not in sources
    ]
    if missing_paths:
        print(
            "Could not find these command paths in room_cleaning.py: "
            + ", ".join(missing_paths)
            + "\nEither they were renamed or moved -- update this guard."
        )
        return 1

    findings: list[str] = []
    for concern, marker in CONCERNS.items():
        for name, source in sources.items():
            if marker in source:
                continue
            reason = ACCEPTED.get((concern, name))
            if reason is None:
                findings.append(
                    f"  {name} does not address '{concern}' "
                    f"(looked for {marker!r})"
                )

    stale = [
        f"  {name} addresses '{concern}' but is listed as accepted absence"
        for (concern, name), _ in ACCEPTED.items()
        if CONCERNS.get(concern, "\0") in sources.get(name, "")
    ]

    if not findings and not stale:
        print(
            f"OK: {len(CONCERNS)} concern(s) across {len(sources)} command "
            f"paths, {len(ACCEPTED)} documented absence(s)."
        )
        return 0

    if findings:
        print(f"\n{len(findings)} unexamined gap(s):\n")
        print("\n".join(findings))
    if stale:
        print(f"\n{len(stale)} stale entry(ies) in ACCEPTED:\n")
        print("\n".join(stale))
    print(
        "\nEither add the concern to that path, or record why it does not\n"
        "belong there in ACCEPTED above. A gap nobody has written down is\n"
        "how the wrong-floor warning went missing from one path for a\n"
        "release.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
