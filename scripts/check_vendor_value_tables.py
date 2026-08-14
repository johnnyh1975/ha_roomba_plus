#!/usr/bin/env python3
"""The integration's own value tables, checked against the vendor's.

The library has the same guard. This one exists because the integration
carries value sets the library does not: option lists for pickers,
capability level rules, phase and cycle labels. Those are where the
vendor's enums reach a user, and they were built by hand from a research
document -- which is exactly the process that produced `off/low/high`
for an enum the vendor calls `noHeat/defaultHeat/highHeat`.

The reference data ships with roombapy-prime, so this check needs the
library installed. It reads the same extract the library checks against,
which is the point: one source, two consumers, no second copy to drift.

WHAT IT ASKS, identical in shape to the library's guard:

  1. Declared mapping present -> do the values match?
  2. No mapping -> is the absence declared, with a reason?

The second question is the one that matters. `PAD_DRY_DURATIONS` passed
review as "no vendor enum exists" while `DryDurType` sat in the extract.
Nothing caught it because nothing asked.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Integration table -> vendor enum whose VALUES it must match.
CHECKED: dict[str, str] = {
    "select_prime.PAD_DRY_DURATIONS": "DryDurType",
    "select_prime.PAD_WASH_HEAT_LEVELS": "HeatType",
    "select_prime.PAD_WASH_AREA_INTERVALS": "ReturnByArea",
    "select_prime.PAD_WASH_TIME_INTERVALS": "ReturnByTime",
    "select_prime.PAD_WASH_RETURN_MODES": "ReturnByMode",
    "select_prime.AUTOEVAC_FREQUENCIES": "ClearFreqType",
}

#: Capability level tables -> the vendor enum whose values are the
#: LEVELS they key on. Checked on keys rather than values.
CHECKED_LEVELS: dict[str, str] = {
    "select_prime._AUTOEVAC_LEVELS": "CapAutoEvac",
    "select_prime._PAD_WASH_HEAT_LEVELS_BY_CAP": "DockPadWashingType",
}

#: Tables with no vendor enum, each with its reason.
NO_VENDOR_ENUM: dict[str, str] = {
    "select_prime.SUCTION_LEVELS": (
        "Confirmed from two real captures of operating_mode_defaults, "
        "where each room stores a suctionLevel beside a profile name. "
        "The vendor's CleanWindSuction is a Picea enum for a different "
        "device class and does not apply."
    ),
    "const.PHASE_LABELS": (
        "Display strings keyed on MissionPhase. The keys are checked "
        "against the vendor enum by tests/test_prime_setup.py; the "
        "values are English prose and have no vendor counterpart."
    ),
    "const.CYCLE_LABELS": (
        "Display strings keyed on MissionCycle, same as PHASE_LABELS."
    ),
    "sensor_prime._PART_COUNT_UNITS": (
        "Display units keyed on asset_health_enum's count types. The "
        "keys come from the vendor; the units are prose."
    ),
    "sensor_prime._KNOWN_PARTS": (
        "Maps both the numeric part ids and app 3.0.0's speaking "
        "part_id values to translation keys. Not a value set."
    ),
    # --- ERROR AND STATUS CODES: iRobot's status spec, not the app ---
    #
    # Checked against the extract by value set: none matches any vendor
    # enum. The app looks these up in a resource table rather than
    # modelling them, which is why `DeviceFault` lists 113 codes and no
    # enum names them.
    "const.ERROR_CATALOGUE": (
        "iRobot's own error texts, extracted from the app's string "
        "resources. Verified complete: 112 codes, matching the vendor's "
        "112 texts exactly."
    ),
    "const.ERROR_CODE_LABELS": "Short labels over the same code space.",
    "sensor_prime.ERROR_CODE_LABELS": "Re-export of const.ERROR_CODE_LABELS.",
    "const.PRIME_ERROR_SEVERITY": (
        "Severity and allowed-modes pairs from iRobot's status spec. "
        "The allowed_modes bits are deliberately NOT decoded -- three "
        "separate codes rule out the obvious reading, most recently "
        "287 and 290, whose meanings would require the values to be "
        "swapped."
    ),
    "sensor_prime.PRIME_ERROR_SEVERITY": "Re-export of the same table.",
    "const.READINESS_STATE_LABELS": (
        "73 numeric readiness codes from the status spec. App 3.0.0 "
        "has no enum over this space; its own readiness handling reads "
        "`precheck.readiness` instead."
    ),
    "sensor_prime.READINESS_STATE_LABELS": "Re-export of the same table.",
    "const.CLEAN_BASE_LABELS": (
        "Twelve dock state codes. `DockMode` names dock TYPES, not "
        "states, and no vendor enum covers the 3xx state space."
    ),
    "sensor_prime._FIELD_OBSERVED_DOCK_STATES": (
        "A single field-observed code kept apart from the spec table on "
        "purpose -- observed and undocumented is a different claim from "
        "documented."
    ),
    "const.MOP_RANK_LABELS": (
        "Four mop rank values. The vendor's RankOverlap names three "
        "DEEP_CLEAN-style constants over a different space, and its "
        "wireValues map is empty."
    ),
    "const.PRIME_BLOCKING_FAULTS": (
        "The four codes `blockFault` checks before a mission starts, "
        "mapped to what each one still allows. The CODES are the "
        "vendor's -- 234/286/287/290, all four in DeviceFault and in "
        "the error catalogue. The MODE SETS are read from the vendor's "
        "own message texts (\"Unable to vacuum: remove Pad Plate\" -> "
        "mop only) and are an interpretation, not a table iRobot "
        "publishes. No vendor enum expresses \"which modes remain\"."
    ),
    "sensor_prime.PRIME_BLOCKING_FAULTS": "Re-export of the const table.",
    "const.BIN_LABELS": "Two display strings over a boolean.",
    "const.YES_NO_LABELS": "Two display strings over a boolean.",
}


def _tables() -> dict[str, dict]:
    from custom_components.roomba_plus import const, select_prime, sensor_prime

    modules = {
        "select_prime": select_prime,
        "const": const,
        "sensor_prime": sensor_prime,
    }
    found: dict[str, dict] = {}
    for mod_name, module in modules.items():
        for attr, value in vars(module).items():
            if not isinstance(value, dict) or not value:
                continue
            if not attr.replace("_", "").isupper():
                continue
            found[f"{mod_name}.{attr}"] = value
    return found


def main() -> int:
    try:
        from roombapy_prime.vendor_reference import has_enum, wire_values
    except ImportError:
        print(
            "SKIPPED: roombapy-prime is not installed, so the vendor "
            "extract is unavailable.",
            file=sys.stderr,
        )
        return 0

    tables = _tables()
    problems: list[str] = []

    for name, vendor_name in CHECKED.items():
        if name not in tables:
            problems.append(f"{name}: declared but no longer exists")
            continue
        if not has_enum(vendor_name):
            problems.append(f"{name}: declared against unknown {vendor_name}")
            continue
        ours = {str(k) for k in tables[name]}
        theirs = {str(v) for v in wire_values(vendor_name)}
        if ours != theirs:
            problems.append(
                f"{name} disagrees with {vendor_name}\n"
                f"      only ours:   {sorted(ours - theirs)}\n"
                f"      only vendor: {sorted(theirs - ours)}"
            )

    for name, vendor_name in CHECKED_LEVELS.items():
        if name not in tables:
            problems.append(f"{name}: declared but no longer exists")
            continue
        ours = {str(k) for k in tables[name]}
        theirs = {str(v) for v in wire_values(vendor_name)}
        if ours != theirs:
            problems.append(
                f"{name} does not cover every {vendor_name} level\n"
                f"      covered: {sorted(ours)}\n"
                f"      vendor:  {sorted(theirs)}"
            )

    declared = set(CHECKED) | set(CHECKED_LEVELS) | set(NO_VENDOR_ENUM)
    for name in sorted(set(tables) - declared):
        table = tables[name]
        if not all(isinstance(k, int) for k in table):
            continue
        problems.append(
            f"{name}: an integer-keyed table with no vendor enum declared "
            f"and no reason given. The vendor names enums by concept, not "
            f"by field -- padDryDur is DryDurType, pwHeat is HeatType. "
            f"Searching for the field name is not a search."
        )

    for name in sorted(declared - set(tables)):
        problems.append(f"{name}: listed here but no longer exists")

    if problems:
        print("Integration value-table check FAILED:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(CHECKED)} table(s) match their vendor enum, "
        f"{len(CHECKED_LEVELS)} capability level table(s) complete, "
        f"{len(NO_VENDOR_ENUM)} documented as having none."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
