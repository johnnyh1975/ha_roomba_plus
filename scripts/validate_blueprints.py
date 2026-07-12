#!/usr/bin/env python3
"""Validate every blueprint YAML in blueprints/automation/.

Two checks, both cheap and both things a broken blueprint would otherwise
only surface as a confusing "invalid blueprint" error in someone's actual
Home Assistant instance after import:

  1. The file is well-formed YAML, including its `!input xxx` tags (which
     plain yaml.safe_load doesn't know how to parse on its own).
  2. Every Jinja2 template string in the blueprint parses as valid Jinja —
     doesn't catch every runtime error (undefined variables in a template
     are only caught when HA actually renders it against real trigger
     data), but does catch syntax errors before a person ever imports it.

`!input xxx` markers that appear *inside* a template string (not as a YAML
tag on their own line) are a Home Assistant blueprint-specific substitution
mechanism, not real Jinja syntax — they're replaced textually with `True`
before parsing, purely so the Jinja parser has something syntactically
valid to check. This mirrors exactly the manual verification done when
roomba_plus_notifications.yaml was first built.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment

BLUEPRINTS_DIR = Path(__file__).resolve().parent.parent / "blueprints" / "automation"


class _HALoader(yaml.SafeLoader):
    pass


def _input_constructor(loader, node):
    return "__INPUT__"


_HALoader.add_constructor("!input", _input_constructor)


def _strip_input_markers(s: str) -> str:
    return re.sub(r"!input\s+\w+", "True", s)


def _collect_template_strings(obj) -> list[str]:
    """Walk the parsed YAML looking for anything that looks like a Jinja
    template (contains {{ or {%), since blueprints don't mark template
    strings with a distinct type — they're just strings that happen to
    contain Jinja syntax, same as any HA YAML."""
    found: list[str] = []
    if isinstance(obj, str):
        if "{{" in obj or "{%" in obj:
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_collect_template_strings(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_template_strings(item))
    return found


def validate_blueprint(path: Path) -> list[str]:
    """Return a list of problem descriptions; empty list = valid."""
    problems: list[str] = []

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_HALoader)
    except yaml.YAMLError as exc:
        return [f"YAML parse error: {exc}"]

    if not isinstance(data, dict) or "blueprint" not in data:
        return ["Missing top-level 'blueprint:' key"]

    env = Environment()
    for tmpl in _collect_template_strings(data):
        cleaned = _strip_input_markers(tmpl)
        try:
            env.parse(cleaned)
        except Exception as exc:  # noqa: BLE001 — jinja2.TemplateSyntaxError et al.
            snippet = tmpl.strip().splitlines()[0][:80]
            problems.append(f"Jinja syntax error in template starting '{snippet}...': {exc}")

    return problems


def main() -> int:
    if not BLUEPRINTS_DIR.exists():
        print(f"No {BLUEPRINTS_DIR} directory — nothing to validate.")
        return 0

    files = sorted(BLUEPRINTS_DIR.glob("*.yaml"))
    if not files:
        print(f"No .yaml files found in {BLUEPRINTS_DIR}.")
        return 0

    had_problems = False
    for path in files:
        problems = validate_blueprint(path)
        if problems:
            had_problems = True
            print(f"::error::{path.name} has {len(problems)} problem(s):")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"OK: {path.name}")

    return 1 if had_problems else 0


if __name__ == "__main__":
    sys.exit(main())
