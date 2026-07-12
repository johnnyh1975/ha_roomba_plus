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


if __name__ == "__main__":
    sys.exit(main())
