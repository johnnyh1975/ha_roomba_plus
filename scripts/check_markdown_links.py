#!/usr/bin/env python3
"""Relative markdown link checker for README.md + docs/*.md.

Checks only relative links (../README.md, FEATURES.md, FEATURES.md#anchor)
— the ones that can actually break from an edit in this repo. External
(http://...) links are out of scope: checking those needs a network call
per link and belongs to a tool like lychee if ever wanted, not this
script.

Verifies two things per link:
  1. The target file exists (relative to the linking file's own
     directory).
  2. If the link has a #fragment, that fragment matches an actual heading
     in the target file, using a reimplementation of GitHub's heading-
     anchor rules (lowercase, strip most punctuation, each space -> one
     hyphen, un-collapsed).

KNOWN LIMITATION, found and left honest rather than "fixed" with an
unverified guess: headings containing emoji or an em-dash sometimes don't
round-trip through this script's slugify() the way GitHub's real
(unpublished, reverse-engineered) algorithm handles those specific
Unicode codepoints. This was caught on this repo's own docs — several
flagged links involve emoji-prefixed headings (docs/COMPARISON.md) or an
em-dash (docs/FEATURES.md, docs/xiaomi-vacuum-map-card.md) where this
script can't be fully sure GitHub agrees. Confirmed reliable for the
common case: a missing file, or a plain-ASCII heading that was renamed
or never existed (caught a real instance of the latter in this repo:
docs/FEATURES.md linked to "#migration", a heading that doesn't exist in
that file at all). When in doubt on a flagged non-ASCII heading, do a
30-second manual click-check on the actual rendered GitHub page rather
than trusting this script's verdict blindly.

Exit 0 = no problems found. Exit 1 = at least one problem (see output).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


def slugify(heading: str) -> str:
    heading = heading.strip().lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    heading = re.sub(r" ", "-", heading)
    return heading


def heading_slugs(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {slugify(h) for h in HEADING_PATTERN.findall(text)}


def files_to_check() -> list[Path]:
    files = [ROOT / "README.md"]
    docs_dir = ROOT / "docs"
    if docs_dir.exists():
        files.extend(sorted(docs_dir.glob("*.md")))
    return files


def main() -> int:
    problems: list[str] = []

    for md_file in files_to_check():
        text = md_file.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            link_text, target = match.groups()

            if target.startswith(("http://", "https://", "mailto:")):
                continue

            file_part, _, fragment = target.partition("#")

            if file_part:
                resolved = (md_file.parent / file_part).resolve()
                if not resolved.exists():
                    problems.append(
                        f"{md_file.relative_to(ROOT)}: link '[{link_text}]({target})' "
                        f"-> target file does not exist: {file_part}"
                    )
                    continue
            else:
                resolved = md_file

            if fragment and resolved.suffix == ".md":
                slugs = heading_slugs(resolved)
                if slugify(fragment) not in slugs:
                    problems.append(
                        f"{md_file.relative_to(ROOT)}: link '[{link_text}]({target})' "
                        f"-> no heading matching '#{fragment}' in {resolved.relative_to(ROOT)} "
                        f"(if that heading has an emoji or em-dash, verify by hand — see docstring)"
                    )

    if problems:
        print(f"::error::{len(problems)} link(s) needing attention:")
        for p in problems:
            print(f"    {p}")
        return 1

    print("OK: all relative markdown links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
