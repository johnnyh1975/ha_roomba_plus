#!/usr/bin/env python3
"""Translation completeness check — strings.json vs. every translations/*.json.

Compares the flattened key set of strings.json (the authoritative schema
source) against each shipped translation file. Reports:
  - keys present in a translation but missing from strings.json (schema
    drift — the class of bug found in v3.4.2: entity.calendar.schedule.name
    and entity.todo.maintenance.name existed in every translation file but
    were never added to strings.json when calendar.py/todo.py shipped in
    v3.4.0)
  - keys present in strings.json but missing from a translation (an
    incomplete translation)

Deliberately does NOT fail on the empty-stub asymmetry already known and
accepted (entity.sensor.recent_wifi_floor/stability.state_attributes: {}
exists in strings.json as a placeholder but is omitted, harmlessly, from
the built translation files) — see the explicit ALLOWED_STRINGS_ONLY set
below. Add to that set only for a verified-harmless case like this one,
with a comment explaining why; anything else missing is a real gap.

Exit code 0 = all translations complete (module to the allowed exceptions
above). Exit code 1 = at least one real gap found, printed to stdout.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
STRINGS_PATH = BASE_DIR / "strings.json"
TRANSLATIONS_DIR = BASE_DIR / "translations"

# Keys allowed to exist in strings.json but be absent from a shipped
# translation file — verified harmless (empty {} stubs that carry no
# actual translatable content either way). See module docstring.
ALLOWED_STRINGS_ONLY: set[str] = {
    "entity.sensor.recent_wifi_floor.state_attributes",
    "entity.sensor.recent_wifi_stability.state_attributes",
}


def flatten_keys(d: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(d, dict):
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            keys.add(path)
            keys |= flatten_keys(v, path)
    return keys


def main() -> int:
    if not STRINGS_PATH.exists():
        print(f"::error::{STRINGS_PATH} not found")
        return 1

    with open(STRINGS_PATH, encoding="utf-8") as f:
        strings_keys = flatten_keys(json.load(f))

    translation_files = sorted(TRANSLATIONS_DIR.glob("*.json"))
    if not translation_files:
        print(f"::error::No translation files found in {TRANSLATIONS_DIR}")
        return 1

    had_problems = False

    for path in translation_files:
        lang = path.stem
        with open(path, encoding="utf-8") as f:
            lang_keys = flatten_keys(json.load(f))

        missing_in_lang = sorted(
            strings_keys - lang_keys - ALLOWED_STRINGS_ONLY
        )
        extra_in_lang = sorted(lang_keys - strings_keys)

        if missing_in_lang:
            had_problems = True
            print(f"::error::translations/{lang}.json is missing {len(missing_in_lang)} key(s) present in strings.json:")
            for k in missing_in_lang:
                print(f"    {k}")

        if extra_in_lang:
            had_problems = True
            print(f"::error::translations/{lang}.json has {len(extra_in_lang)} key(s) not in strings.json (strings.json is stale — add them there too):")
            for k in extra_in_lang:
                print(f"    {k}")

        if not missing_in_lang and not extra_in_lang:
            print(f"OK: translations/{lang}.json matches strings.json ({len(lang_keys)} keys)")

    return 1 if had_problems else 0


#: Home Assistant's own rule for translation keys, from hassfest.
#:
#: Applied here because hassfest runs in the HACS validation workflow --
#: after a release is cut. A camelCase state key (`padPlate`) passed
#: every local check, every one of 4,610 tests, and failed in CI.
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]*[a-z0-9]$|^[a-z0-9]$")

#: Levels whose keys are HA identifiers rather than free-form names.
#:
#: Only leaf dictionaries under `state` and `data` are constrained:
#: `entity.sensor.<key>.state.<STATE>` and
#: `config.step.<step>.data.<FIELD>`. The names above them are HA's own
#: and already valid.
_CONSTRAINED_PARENTS = ("state", "data")


def _check_key_syntax(node: object, path: tuple[str, ...]) -> list[str]:
    """Every key under a constrained parent must match HA's pattern."""
    problems: list[str] = []
    if not isinstance(node, dict):
        return problems
    constrained = bool(path) and path[-1] in _CONSTRAINED_PARENTS
    for key, value in node.items():
        if constrained and not _KEY_PATTERN.match(key):
            problems.append(f"{'.'.join((*path, key))}: {key!r}")
        problems.extend(_check_key_syntax(value, (*path, key)))
    return problems


def _report_key_syntax() -> int:
    failures: list[str] = []
    for path in [STRINGS_PATH, *sorted(TRANSLATIONS_DIR.glob("*.json"))]:
        data = json.loads(path.read_text(encoding="utf-8"))
        for problem in _check_key_syntax(data, ()):
            failures.append(f"  {path.name}: {problem}")

    if not failures:
        return 0

    print(f"\n{len(failures)} translation key(s) Home Assistant will reject:\n")
    print("\n".join(failures))
    print(
        "\n  Keys must be [a-z0-9-_]+ and may not start or end with a hyphen\n"
        "  or underscore. Wire values are frequently camelCase -- map them to\n"
        "  slugs in the entity rather than using them as keys."
    )
    return 1


if __name__ == "__main__":
    # BOTH run, always.  short-circuits:
    # a clean key-count check returns 0, which is falsy, so the syntax
    # check never ran -- verified by reintroducing the camelCase key that
    # broke CI and watching this script pass.
    _counts = main()
    _syntax = _report_key_syntax()
    sys.exit(_counts or _syntax)
