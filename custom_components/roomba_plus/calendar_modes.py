"""Recognising a cleaning mode in free text a user typed.

WHY KEYWORDS AND NOT THE TRANSLATION FILES. Home Assistant does not
translate calendar event summaries -- `Cleaning` is hardcoded English
and always has been -- so the label written back is English whatever the
user's language. Reading only English would make the recognition English
too, which would be a step backwards from the room matcher, where names
come from the robot and therefore work in every language.

So: recognised in eight languages, displayed in one. The asymmetry is
worth naming rather than hiding, and it follows the platform.

THE SET IS CLOSED AND TINY -- four modes -- which is what makes this
defensible. This does not interpret a sentence; it looks for words from
a list.

WHAT DECIDES BETWEEN THE TWO COMBINED MODES. A word for "then" between
the two verbs means vacuum-then-mop; both verbs without one means both
at once. That mirrors how the modes actually differ, and a user who
writes neither gets the simple single mode they named.
"""

from __future__ import annotations

import re
import unicodedata

#: `regions[].params.operatingMode`.
MODE_VACUUM = 2
MODE_MOP = 4
MODE_VACUUM_AND_MOP = 32
MODE_VACUUM_THEN_MOP = 512

#: Words meaning "vacuum", per language. Stems rather than full forms,
#: matched as prefixes, so "saugen" also catches "saugt" and
#: "aspiration" catches "aspirer" -- a user writing a conjugated verb
#: should not fall through to a different mode.
_VACUUM = (
    "vacuum", "vacuuming", "hoover",          # en
    "saug", "staubsaug",                      # de
    "aspir",                                  # fr, es, it, pt
    "stofzuig", "zuig",                       # nl
    "odkurz",                                 # pl
)

#: Words meaning "mop".
_MOP = (
    "mop", "mopping", "wet",                  # en
    "wisch", "feucht",                        # de
    "lav", "serpill",                         # fr, it, pt
    "freg", "trape",                          # es
    "dweil",                                  # nl
    "mopow", "mycie",                         # pl
    "esfreg",                                 # pt
)

#: Words meaning "then", which separate the sequential mode from the
#: simultaneous one.
_THEN = (
    "then", "after",                          # en
    "dann", "danach",                         # de
    "puis", "ensuite",                        # fr
    "luego", "despues",                       # es
    "poi", "quindi",                          # it
    "dan", "daarna",                          # nl
    "potem",                                  # pl
    "depois",                                 # pt
)


def _fold(text: str) -> list[str]:
    """Words, casefolded and stripped of accents.

    So "aspiration" and "aspirátion" meet, and a Polish or French user
    is not punished for typing their own language properly.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return [w for w in re.split(r"[^\w]+", plain.casefold()) if w]


def _has(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(word.startswith(stem) for word in words for stem in stems)


def match_mode(text: str) -> int | None:
    """The operating mode named in `text`, or None if none is.

    None means "leave it alone" -- the caller keeps whatever the
    schedule it derived from was using. Guessing a mode would change
    what the robot does to the floor on the strength of a word that
    might not have been meant that way.
    """
    words = _fold(text)
    if not words:
        return None

    vacuum = _has(words, _VACUUM)
    mop = _has(words, _MOP)
    if vacuum and mop:
        return MODE_VACUUM_THEN_MOP if _has(words, _THEN) else MODE_VACUUM_AND_MOP
    if vacuum:
        return MODE_VACUUM
    if mop:
        return MODE_MOP
    return None
