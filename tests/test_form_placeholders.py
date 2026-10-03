"""Every form passes every placeholder its text uses.

Home Assistant renders a step's title and description with the
`description_placeholders` the flow passes. A placeholder the text
names and the code does not pass is not left blank: the frontend shows
`Translation [formatjs Error: MISSING_VALUE] The intl string context
variable "robot" was not provided ...` in place of the instructions.

@liblit hit exactly that in 4.2.18, on both "Rooms & zones" steps. A
4.2.5 rewrite had put the repair dialog's text, which names `{robot}`,
into two options steps whose code never passed it. Nothing compared
the two, and no test rendered the text.

Static, from the source: a call whose placeholders are not a literal
dict is skipped, since only running it could tell what it passes.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_PKG = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_STRINGS = json.loads((_PKG / "strings.json").read_text(encoding="utf-8"))
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _section(cls: ast.ClassDef) -> str | None:
    bases = {ast.unparse(b) for b in cls.bases}
    if any("OptionsFlow" in b for b in bases):
        return "options"
    if any("ConfigFlow" in b for b in bases):
        return "config"
    return None


def _calls():
    """(file, line, section, step_id, passed keys) per show_form call."""
    found = []
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            section = _section(cls)
            if section is None:
                continue
            for call in ast.walk(cls):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "async_show_form"
                ):
                    continue
                kw = {k.arg: k.value for k in call.keywords}
                step = kw.get("step_id")
                if not isinstance(step, ast.Constant):
                    continue
                ph = kw.get("description_placeholders")
                if ph is None:
                    passed: set[str] | None = set()
                elif isinstance(ph, ast.Dict) and all(
                    isinstance(k, ast.Constant) for k in ph.keys
                ):
                    passed = {k.value for k in ph.keys}  # type: ignore[union-attr]
                else:
                    passed = None
                if passed is not None:
                    found.append(
                        (path.name, call.lineno, section, step.value, passed)
                    )
    return found


_CALLS = _calls()


def test_the_scan_finds_the_forms():
    """A scan that finds nothing passes everything."""
    assert len(_CALLS) >= 15
    assert any(c[3] == "smart_zones" for c in _CALLS)
    assert any(c[3] == "zones" for c in _CALLS)


@pytest.mark.parametrize(
    ("source", "line", "section", "step", "passed"),
    _CALLS,
    ids=[f"{c[0]}:{c[1]}:{c[3]}" for c in _CALLS],
)
def test_every_placeholder_is_passed(source, line, section, step, passed):
    texts = _STRINGS.get(section, {}).get("step", {}).get(step, {})
    used = set()
    for field in ("title", "description"):
        used |= set(_PLACEHOLDER.findall(texts.get(field, "")))
    missing = used - passed
    assert not missing, (
        f"{source}:{line} shows {section} step '{step}' without "
        f"{sorted(missing)}, which its text uses"
    )
