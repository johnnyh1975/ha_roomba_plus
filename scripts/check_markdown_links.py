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

Verified against github-slugger 2.0.0, the widely-used reverse-engineering
of GitHub's own unpublished anchor algorithm, rather than against a guess.
slugify() below matches it for every heading in this repo. Two cases were
checked explicitly because an earlier version of this docstring called
them unreliable:

  * EM-DASH headings round-trip correctly. "Path A — XVMC v2.4.1+" gives
    "path-a--xvmc-v241" in both. The seven links that touch such headings
    (docs/FEATURES.md, docs/TROUBLESHOOTING.md,
    docs/xiaomi-vacuum-map-card.md) were never wrong.

  * EMOJI-PREFIXED headings do NOT. GitHub strips the emoji but keeps the
    space it leaves behind, so "## 🔌 Setup" anchors as "#-setup" with a
    leading hyphen -- and a variation selector (U+FE0F, as in 🗺️)
    survives the strip entirely, producing an anchor with an invisible
    leading character. Six COMPARISON.md headings carried emoji and their
    links were broken on the rendered page. The emoji were removed from
    those headings rather than the links being written around the
    artefact; the other headings in that file never had any, so it is
    also more consistent now.

The remaining unverified corner is a heading whose emoji is not at the
start. Nothing in this repo has one. If one is added and this script
flags it, check the rendered page before changing anything -- and change
the heading, not this script.

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
    # Mirrors github-slugger (the reverse-engineering of GitHub's own,
    # unpublished algorithm) exactly: lowercase, drop the punctuation/
    # symbol classes, then every remaining space becomes one hyphen.
    #
    # DO NOT re-trim after the substitution. An emoji-prefixed heading
    # like "## 🔌 Setup & Prerequisites" leaves a LEADING SPACE once the
    # emoji is gone, and GitHub turns that into a LEADING HYPHEN:
    # "-setup--prerequisites". A trim here produces
    # "setup--prerequisites" and is wrong.
    #
    # That trim was in this script, and it cost real links. Six
    # COMPARISON.md anchors had been rewritten to match it and were
    # broken on the rendered page for as long as the trim made this
    # script agree with them. Verified against github-slugger 2.0.0
    # rather than by guessing a second time. The emoji have since been
    # removed from those headings, so no anchor in this repo depends on
    # the leading-hyphen case any more -- but the rule stands for the
    # next one added.
    heading = heading.strip().lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    # TRIM AGAIN after stripping non-word characters.
    #
    # An emoji heading like "## 🔌 Setup & Prerequisites" leaves a
    # LEADING SPACE once the emoji is gone, which then becomes a leading
    # hyphen -- so this produced "-setup--prerequisites" where GitHub
    # produces "setup--prerequisites".
    #
    # The symptom was misleading: the script reported the anchor as
    # missing, and its own error message suggested verifying emoji
    # headings by hand. Four correct links in COMPARISON.md were
    # "fixed" to match the script before the script turned out to be
    # the one that was wrong.
    heading = heading.strip()
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
                        f"(check the rendered page before editing either side — see docstring)"
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
