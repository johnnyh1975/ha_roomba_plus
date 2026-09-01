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
    "const.CLEAN_BASE_STATUS_SLUGS": (
        "Twelve dock state codes. `DockMode` names dock TYPES, not "
        "states, and no vendor enum covers the 3xx state space. "
        "(Renamed from CLEAN_BASE_LABELS when the values became "
        "translation slugs.)"
    ),
    "const.MOP_BEHAVIOR_SLUGS": (
        "Four mop rank values. The vendor's RankOverlap names three "
        "DEEP_CLEAN-style constants over a different space. "
        "(Renamed from MOP_RANK_LABELS when the values became "
        "translation slugs.)"
    ),
    "sensor_prime._FIELD_OBSERVED_DOCK_STATE_SLUGS": (
        "A single field-observed code kept apart from the spec table on "
        "purpose -- observed and undocumented is a different claim from "
        "documented. (Renamed from _FIELD_OBSERVED_DOCK_STATES when the "
        "values became translation slugs.)"
    ),
    "select_prime.PAD_WETNESS_LEVELS": (
        "NO SOURCE ANYWHERE, and this is the only value set in that file "
        "without one. The key `padWetness.padPlate`, its dot notation and "
        "its gate `cap.ppWetLvl` are all confirmed; the RANGE appears in "
        "no vendor enum, no settings-key type, no locale string and no "
        "capability table. Four is the highest value anyone has seen "
        "(@chairstacker), and `_wetness_options()` widens the set to "
        "include whatever the robot reports so a guessed ceiling cannot "
        "hide a real value. Replace this entry the moment a source turns "
        "up."
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


#: Labels that differ from the vendor's member name ON PURPOSE.
#:
#: Each needs a reason, and "it reads better" is not one. The bar is
#: that the label says the same thing the vendor's name says -- usually
#: because it matches what the APP shows a user, which is a better
#: source than a Kotlin identifier.
DELIBERATE_LABELS: dict[str, str] = {
    "select_prime.AUTOEVAC_FREQUENCIES[0]": (
        "`evClean` is one clean; @chairstacker's app renders this as "
        "'After Every Routine' and the label follows the app."
    ),
    "select_prime.AUTOEVAC_FREQUENCIES[4]": (
        "`evBackHome` in plain words. Same event, no claim added."
    ),
}


def _member_for(vendor_name: str, value: object) -> str | None:
    """The vendor's member name for a wire value."""
    from roombapy_prime.vendor_reference import enum_values  # noqa: PLC0415

    for member, wire in (enum_values(vendor_name) or {}).items():
        if str(wire) == str(value):
            return member
    return None


def _label_matches(option: str, member: str) -> bool:
    """Whether our option string is recognisably the vendor's name.

    Deliberately loose. `refillAndRoom` -> `refill_and_room` must pass,
    and so must a label that reorders or expands the words -- the point
    is to catch an INVENTED word, not to enforce a spelling.
    """
    def words(text: str) -> set[str]:
        out, current = set(), ""
        for char in text:
            if char in "_-. ":
                if current:
                    out.add(current.lower())
                current = ""
            elif char.isupper() and current:
                out.add(current.lower())
                current = char
            else:
                current += char
        if current:
            out.add(current.lower())
        return {w for w in out if len(w) > 2}

    # A NUMERIC LABEL IS THE VALUE ITSELF and always honest: `10` for
    # `ev10`, `6_hours` for `six`. Nobody is misled by a number.
    if any(char.isdigit() for char in option):
        return True

    ours, theirs = words(option), words(member)
    if ours & theirs:
        return True
    # Substring either way, for `evRoom` -> `after_each_room` style
    # expansions where the vendor's word is embedded rather than equal.
    flat_ours = option.replace("_", "").lower()
    flat_theirs = member.replace("_", "").lower()
    return flat_theirs in flat_ours or flat_ours in flat_theirs or not theirs


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
            continue

        # THE VALUES WERE NEVER THE PROBLEM. `PAD_WASH_RETURN_MODES` had
        # all six of `ReturnByMode`'s values, exactly right, and labelled
        # 100/101/102 "standard | medium | high" while the vendor calls
        # them `mission`, `refill` and `refillAndRoom`. This check passed
        # it every time.
        #
        # A wrong LABEL is worse than a wrong value, because a wrong
        # value gets rejected by the robot and a wrong label gets
        # believed. @ratpic83 read those three beside a dock reporting
        # `pw: 3`, reported that the heat levels were confirmed, then
        # retracted his own correct observation as "noise" after finding
        # no heat control in the app.
        #
        # So: our option string must be derivable from the vendor's
        # member name. Not identical -- `refillAndRoom` becomes
        # `refill_and_room` and that is right -- but recognisably the
        # same word, which "standard" for `mission` is not.
        for value, option in tables[name].items():
            member = _member_for(vendor_name, value)
            if member is None:
                continue
            if f"{name}[{value}]" in DELIBERATE_LABELS:
                continue
            if not _label_matches(str(option), member):
                problems.append(
                    f"{name}[{value}] is labelled {option!r} but "
                    f"{vendor_name} calls it {member!r} -- a label the "
                    f"vendor does not use is one a tester will believe"
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
