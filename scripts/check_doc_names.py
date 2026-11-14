#!/usr/bin/env python3
"""Names in the documentation must exist in the code (4.6.0).

A review of the docs for 4.6.0 found about twenty names that did not:
entity ids that were never created (`image.<robot>_cleaning_map` for a
map whose id is `image.<robot>_map`), an action that never existed
(`roomba_plus.reset_battery`, a button), Prime entities without their
`prime_` prefix. Nothing caught them, because the link check only follows
links. This does the same for the names a user copies into an automation:

- actions, `roomba_plus.<name>`: must be in services.yaml;
- events, `roomba_plus_<name>`: must be a name the code fires or uses;
- entity ids, `<platform>.<robot>_<suffix>`: the suffix must be an
  entity id suffix the code assigns. `<robot>` is the placeholder the
  docs use: `{name}`, `<robot>`, `*`, or the example robot `roomba`.

Also: every file the integration or a blueprint links to on GitHub
(`blob/main/<path>`: the care guides in each language, the
troubleshooting page, the blueprints' own source) must exist, so that a
reorganised doc folder cannot leave a link in the product pointing at
nothing.

And every menu path, `Configure → <entry> → <sub entry or option>`: each
step must be one the Configure dialog has (strings.json), so a renamed
or moved option cannot leave the docs pointing at nothing.

Checked: README.md and every page under docs/ but the care guides. Not
checked: docs/UPGRADING.md and the release notes, which record names as
they were, and docs/ARCHITECTURE.md, which names code. A suffix written with a placeholder of its own
(`floor_plan_{map}`) is matched by its fixed part.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "roomba_plus"
SKIP = {"UPGRADING.md", "ARCHITECTURE.md"}
PLATFORMS = (
    "sensor|binary_sensor|switch|select|button|number|event|image|calendar|todo"
    "|device_tracker|vacuum|time|update|text"
)
ROBOT = r"(?:\{name\}|\{prefix\}|<robot>|<name>|\*|roomba|robot|robbie|your_robot)"
#: Not entity ids although they look like one.
FILE_SUFFIXES = {"zip", "yaml", "json", "py", "md", "png"}
#: Ids an installation from an older version still has, which the docs
#: name on purpose ("an older installation may call it ...").
LEGACY_SUFFIXES = {"cleaning_map", "rooms_cleaning_map"}


def code_names() -> tuple[set[str], set[str], set[str]]:
    """(actions, events and other domain-prefixed names, entity id suffixes)."""
    actions = set(yaml.safe_load((CC / "services.yaml").read_text(encoding="utf-8")))
    prefixed: set[str] = set()
    suffixes: set[str] = set()
    for py in CC.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        prefixed |= set(re.findall(r'"(roomba_plus_[a-z0-9_]+)', text))
        prefixed |= {"roomba_plus_" + n for n in re.findall(r'\{DOMAIN\}_([a-z0-9_]+)', text)}
        suffixes |= set(re.findall(r"robot_unique_id\}_([a-z0-9_]+)", text))
        suffixes |= set(re.findall(r'\bkey="([a-z0-9_]+)"', text))
        suffixes |= set(re.findall(r'unique_id_suffix="([a-z0-9_]+)"', text))
        # event.py: ("mission_completed", EVENT_MISSION_COMPLETED, ...) and
        # ("command", {...}) for the entity that listens to several events
        suffixes |= set(re.findall(r'\("([a-z0-9_]+)",\s*(?:EVENT_|\{)', text))
        for body in re.findall(r"def suggested_object_id\(self\)[^:]*:(.*?)(?=\n    def |\n    @|\nclass |\Z)",
                               text, re.S):
            suffixes |= set(re.findall(r'"([a-z0-9_]+)"', body))
    # The generated entity reference (scripts/build_reference.py) knows
    # the ids the code builds at run time: per part, room, area, stream.
    # Its own test keeps it current.
    reference = ROOT / "docs" / "reference" / "entities.md"
    if reference.exists():
        for suffix in re.findall(r"`[a-z_]+\.<robot>_([a-z0-9_]+(?:<[a-z ]+>)?)`",
                                 reference.read_text(encoding="utf-8")):
            suffixes.add(re.sub(r"<[a-z ]+>", "{x}", suffix))
    # Blueprint file names, named in the docs without .yaml.
    prefixed |= {p.stem for p in (ROOT / "blueprints" / "automation").glob("*.yaml")}
    return actions, prefixed, suffixes | LEGACY_SUFFIXES


#: Words after "Configure →" that are not a menu entry: the dialog's
#: own buttons and the menu of an option step that only leads on.
MENU_WORDS: set[str] = set()


def _clean(segment: str) -> str:
    return segment.strip().strip("*").strip()


def menu_paths() -> tuple[dict[str, dict[str, set[str]]], set[str]]:
    """{menu entry: {sub entry or option: set()}} and all step titles,
    from strings.json, as the Configure dialog shows them."""
    import json  # noqa: PLC0415

    steps = json.loads((CC / "strings.json").read_text(encoding="utf-8"))["options"]["step"]
    tree: dict[str, dict[str, set[str]]] = {}
    for key, label in steps["init"]["menu_options"].items():
        step = steps.get(key, {})
        children = dict.fromkeys(
            [*(step.get("menu_options") or {}).values(), *(step.get("data") or {}).values()],
        )
        for sub_key, sub_label in (step.get("menu_options") or {}).items():
            children[sub_label] = None
            tree.setdefault(f"{label} → {sub_label}", {}).update(
                dict.fromkeys((steps.get(sub_key, {}).get("data") or {}).values())
            )
        tree.setdefault(label, {}).update(children)  # type: ignore[arg-type]
    titles = {step.get("title", "") for step in steps.values()}
    return tree, titles


_MENU = re.compile(r"Configure\s*→\s*((?:\*{1,2}[^*]+\*{1,2}|[^→.,;:)(|*\n]+)"
                   r"(?:\s*→\s*(?:\*{1,2}[^*]+\*{1,2}|[^→.,;:)(|*\n]+))*)")


def menu_problems(line: str) -> list[str]:
    """A menu path written in the docs that the dialog does not have."""
    tree, titles = menu_paths()
    found = []
    for match in _MENU.finditer(line):
        parts = [_clean(p) for p in match.group(1).split("→") if _clean(p)]
        if not parts:
            continue
        first = parts[0]
        entries = {k for k in tree if "→" not in k}
        if not any(first == e or first.startswith(e) for e in entries):
            found.append(f"menu path 'Configure → {first}' -- no such entry in the Configure menu")
            continue
        entry = next(e for e in sorted(entries, key=len, reverse=True) if first.startswith(e))
        node = entry
        for part in parts[1:]:
            children = tree.get(node, {})
            if not any(part == c or c.startswith(part) or part.startswith(c) for c in children):
                found.append(f"menu path '... {node} → {part}' -- '{part}' is not in '{node}'")
                break
            child = next(c for c in children if part == c or c.startswith(part) or part.startswith(c))
            node = f"{node} → {child}"
    return found


def problems() -> list[str]:
    actions, prefixed, suffixes = code_names()
    found: list[str] = []
    docs = [ROOT / "README.md", *sorted(
        p for p in (ROOT / "docs").rglob("*.md")
        if p.relative_to(ROOT / "docs").parts[0] != "care"
    )]
    entity = re.compile(rf"\b({PLATFORMS})\.{ROBOT}_([a-z0-9_{{}}]+(?:<[a-z ]+>)?)")
    for doc in docs:
        if doc.name in SKIP:
            continue
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{doc.relative_to(ROOT)}:{n}"
            for m in re.finditer(r"\broomba_plus\.([a-z0-9_]+)", line):
                name = m.group(1)
                if name not in actions and name not in FILE_SUFFIXES:
                    found.append(f"{where}: action roomba_plus.{name} is not in services.yaml")
            for m in re.finditer(r"\b(roomba_plus_[a-z0-9_]+)", line):
                name = m.group(1)
                if name.endswith("_") or name in prefixed or name == "roomba_plus_card":
                    continue
                if any(p.startswith(name) for p in prefixed):
                    continue  # a store or file name cut where an id follows
                found.append(f"{where}: {name} is not a name the code uses")
            found.extend(f"{where}: {p}" for p in menu_problems(line))
            for m in entity.finditer(line):
                # `<area id>` is a placeholder as `{area}` is.
                suffix = re.sub(r"<[a-z ]+>", "{x}", m.group(2))
                fixed = suffix.split("{")[0].rstrip("_")
                if suffix in suffixes or suffix in FILE_SUFFIXES:
                    continue
                if "{" in suffix and any(s.startswith(fixed) for s in suffixes):
                    continue
                found.append(f"{where}: {m.group(0)} -- no entity gets the id suffix '{suffix}'")
    return found


REPO_URL = "https://github.com/johnnyh1975/ha_roomba_plus/blob/main/"


def broken_product_links() -> list[str]:
    """Files the code and the blueprints link to that are not in the repo."""
    languages = sorted(p.stem for p in (CC / "translations").glob("*.json"))
    found = []
    sources = [*CC.glob("*.py"), *(ROOT / "blueprints" / "automation").glob("*.yaml")]
    for source in sources:
        for path in re.findall(re.escape(REPO_URL) + r"([A-Za-z0-9_./{}-]+)", source.read_text(encoding="utf-8")):
            path = path.split("#")[0].rstrip(".")
            for target in ([path.replace("{lang}", lang) for lang in languages] if "{lang}" in path else [path]):
                if not (ROOT / target).exists():
                    found.append(f"{source.name}: links to {target}, which is not in the repository")
    return found


def main() -> int:
    found = problems() + broken_product_links()
    if found:
        print(f"::error::{len(found)} name(s) in the docs the code does not have, or links from the code to no file:")
        for f in found:
            print("    " + f)
        return 1
    print("OK: every action, event and entity id in the docs exists in the code, "
          "and every file the code links to exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
