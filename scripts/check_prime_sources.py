"""Prime code must not read Classic-only sources.

WHY THIS EXISTS.

Five bugs this week shared one shape: a Prime code path reading a
source that either does not exist on a Prime entry, or exists but
carries only part of the answer.

  - region sensors read `cloud_coordinator.regions_by_pmap` -- a Prime
    entry has no `cloud_coordinator`, so the option created nothing at
    all and a tester ticked it, ran a mission, reloaded, and got no
    entities
  - `prime_coordinator._room_name()` reads the same, so the room name
    in its own event is always None
  - the device tracker's name cache came from `available_rooms()`,
    which reads `rooms_metadata` and therefore knows rooms but not
    zones
  - `PrimeRoomCleaning.get_segments()` builds from the same, so zones
    could never be mapped to a Home Assistant area
  - the cloud-coverage fallback gated on `cap.pose`, which lewis
    firmware sets on robots that never send a pose

The pattern is not carelessness in each case. It is that a better
source appeared later -- `prime_room_names`, which holds rooms and
zones together -- and the places already reading the older one were
never revisited. Each looked correct in isolation; each returned a
subset or nothing at all.

WHAT THIS CHECKS.

Any file whose name starts with `prime_`, plus the Prime branches this
list names explicitly, must not read `cloud_coordinator` or use
`available_rooms()` as a name source. Exemptions carry a reason, in the
same spirit as `check_late_imports.py`: a rule with no way to fail is
decoration, and a rule with no way to be excused gets deleted the first
time it is inconvenient.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"

#: Reading these from Prime code is the bug this exists to catch.
_CLASSIC_ONLY = ("cloud_coordinator",)

#: Prime branches that live in shared files. Checked by function name,
#: because the file itself serves both generations.
_PRIME_FUNCTIONS = {
    "room_cleaning.py": ("PrimeRoomCleaning",),
    "sensor.py": ("_sync_region_sensors",),
}

#: Reasons, not silence. A bare allow-list stops being read.
_ALLOWED: dict[str, str] = {
    "prime_room_map.py:cloud_coordinator": (
        "reads it to MERGE Classic-side names in, on a household that "
        "has both generations -- an addition, not the only source"
    ),
}


def _prime_files() -> list[Path]:
    return sorted(INTEGRATION.glob("prime_*.py"))


def _offences_in(path: Path) -> list[str]:
    """Attribute reads of a Classic-only source, with line numbers."""
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value if node.value in _CLASSIC_ONLY else None
        if name in _CLASSIC_ONLY:
            key = f"{path.name}:{name}"
            if key in _ALLOWED:
                continue
            found.append(f"  {path.name}:{node.lineno} reads `{name}`")
    return found


def main() -> int:
    problems: list[str] = []
    for path in _prime_files():
        problems.extend(_offences_in(path))

    if problems:
        print("Prime code reading a Classic-only source:")
        print("\n".join(sorted(set(problems))))
        print(
            "\nA Prime config entry has no `cloud_coordinator`. Region "
            "names live in `runtime_data.prime_room_names`, which holds "
            "rooms AND zones -- `available_rooms()` reads "
            "`rooms_metadata` and knows rooms only.\n"
            "\nIf a read is deliberate, add it to _ALLOWED with a reason."
        )
        return 1

    print(
        f"OK: {len(_prime_files())} prime_*.py file(s) checked, "
        f"{len(_ALLOWED)} deliberate read(s) documented."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
