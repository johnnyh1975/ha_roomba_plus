"""Build the body of a GitHub release from its release-notes file.

    python scripts/build_release_body.py v4.5.1 > body.md

The notes file (`release-notes/<tag>.md`) stays what it is: written by
hand, the same text in the repository and on the release page. Around it
this adds what a reader of the release page wants at a glance, in the
shape of FaserF/ha-openwrt's releases:

- the channel next to the title (stable or beta);
- a callout when the notes mark something as **Breaking**;
- which parts of the repository changed since the previous release, in
  files (core, translations, tests, CI, docs);
- a table of release details: version, channel, date, the comparison
  with the previous release, what it requires, downloads;
- links to the documentation, the issue tracker and all releases.

FILES, NOT COMMITS. Releases reach GitHub as uploads through the web
interface, so a commit count says how many uploads there were, not how
much changed. The file count between the two tags is the honest number.

No risk rating either: a rating computed from file counts would look
like a judgement nobody made. What can go wrong is written in the notes
(the *Tested* section names what is not confirmed on a robot).

The previous release is the newest older tag of the same channel for a
stable release (4.5.1 compares with 4.5.0, not with 4.5.0b6), and the
newest older tag of any channel for a beta.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "johnnyh1975/ha_roomba_plus"
ROOT = Path(__file__).resolve().parent.parent

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
_PRE_ORDER = {"a": 0, "b": 1, "rc": 2}

#: (label, path prefixes), first match wins; the rest is "Other".
AREAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("🌐 Translations", ("custom_components/roomba_plus/translations/",
                        "custom_components/roomba_plus/strings.json")),
    ("🧩 Integration", ("custom_components/",)),
    ("🧪 Tests", ("tests/",)),
    ("⚙️ CI", (".github/", "scripts/")),
    ("📖 Docs", ("docs/", "release-notes/", "README.md", "AGENTS.md")),
    ("🗂️ Blueprints", ("blueprints/",)),
)


#: The order the areas are shown in.
DISPLAY_ORDER: tuple[str, ...] = (
    "🧩 Integration", "🌐 Translations", "🗂️ Blueprints", "🧪 Tests",
    "⚙️ CI", "📖 Docs", "🚀 Other",
)


def version_key(tag: str) -> tuple[int, int, int, int, int] | None:
    """Sort key for a release tag, None for anything else."""
    m = _TAG.match(tag)
    if m is None:
        return None
    major, minor, patch, pre, num = m.groups()
    # A stable release sorts after its own pre-releases.
    pre_rank = _PRE_ORDER[pre] if pre else 9
    return int(major), int(minor), int(patch), pre_rank, int(num or 0)


def is_prerelease(tag: str) -> bool:
    m = _TAG.match(tag)
    return bool(m and m.group(4))


def previous_tag(tag: str, tags: list[str]) -> str | None:
    """The release this one is compared with (see the module docstring)."""
    own = version_key(tag)
    if own is None:
        return None
    stable = not is_prerelease(tag)
    older = [
        t for t in tags
        if (k := version_key(t)) is not None and k < own
        and (not stable or not is_prerelease(t))
    ]
    return max(older, key=lambda t: version_key(t) or (0, 0, 0, 0, 0), default=None)


def area_counts(paths: list[str]) -> list[tuple[str, int]]:
    """Changed files per area, in AREAS order, areas without files left out."""
    counts: dict[str, int] = {}
    for path in paths:
        label = next(
            (name for name, prefixes in AREAS if path.startswith(prefixes)),
            "🚀 Other",
        )
        counts[label] = counts.get(label, 0) + 1
    return [(name, counts[name]) for name in DISPLAY_ORDER if name in counts]


def requirements() -> str:
    manifest = json.loads((ROOT / "custom_components/roomba_plus/manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    libs = ", ".join(r.replace("[map]", "") for r in manifest.get("requirements", []))
    return f"Home Assistant {hacs.get('homeassistant', '?')}+ · {libs}"


def build_body(
    tag: str,
    notes: str,
    *,
    previous: str | None,
    changed: list[str],
    released: datetime.date,
    requires: str,
) -> str:
    """The release body. Pure: everything it reads is passed in."""
    channel = "Beta" if is_prerelease(tag) else "Stable"
    colour = "orange" if channel == "Beta" else "brightgreen"
    badge = f"![{channel}](https://img.shields.io/badge/channel-{channel.lower()}-{colour})"

    lines = notes.rstrip("\n").split("\n")
    if lines and lines[0].startswith("# "):
        title, rest = f"{lines[0]} {badge}", lines[1:]
    else:
        title, rest = f"# Roomba+ {tag.removeprefix('v')} {badge}", lines
    while rest and not rest[0].strip():
        rest = rest[1:]

    head = [title, ""]
    if "**Breaking**" in notes:
        head += [
            "> [!WARNING]",
            "> **Contains a breaking change.** Read the part marked *Breaking*"
            " below before updating.",
            "",
        ]
    if previous is not None:
        areas = " · ".join(f"{name} {n}" for name, n in area_counts(changed)) or "no files"
        head += [
            "> [!NOTE]",
            f"> **Changed since {previous}:** {areas}",
            "",
        ]

    if previous is not None:
        compare = (
            f"[{previous}...{tag}](https://github.com/{REPO}/compare/{previous}...{tag})"
            f" · {len(changed)} files"
        )
    else:
        compare = "first release"
    downloads = (
        f"![this release](https://img.shields.io/github/downloads/{REPO}/{tag}/total)"
        f" ![all releases](https://img.shields.io/github/downloads/{REPO}/total)"
    )
    details = [
        "",
        "---",
        "",
        "## Release details",
        "",
        "| | |",
        "|---|---|",
        f"| **Version** | {tag.removeprefix('v')} |",
        f"| **Channel** | {channel} |",
        f"| **Released** | {released.isoformat()} |",
        f"| **Compared with** | {compare} |",
        f"| **Requires** | {requires} |",
        f"| **Downloads** | {downloads} |",
        "",
        f"*[Documentation](https://github.com/{REPO}#readme) ·"
        f" [Upgrade notes](https://github.com/{REPO}/blob/main/docs/UPGRADING.md) ·"
        f" [Report an issue](https://github.com/{REPO}/issues/new/choose) ·"
        f" [All releases](https://github.com/{REPO}/releases)*",
    ]
    return "\n".join(head + rest + details) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("tag")
    parser.add_argument("--ref", default="HEAD", help="the commit the release is built from")
    args = parser.parse_args(argv)

    notes_path = ROOT / "release-notes" / f"{args.tag}.md"
    if not notes_path.is_file():
        print(f"no {notes_path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    tags = _git("tag", "--list", "v*").split()
    previous = previous_tag(args.tag, tags)
    changed = (
        _git("diff", "--name-only", previous, args.ref).split() if previous else []
    )
    sys.stdout.write(build_body(
        args.tag,
        notes_path.read_text(encoding="utf-8"),
        previous=previous,
        changed=changed,
        released=datetime.datetime.now(datetime.UTC).date(),
        requires=requirements(),
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
