"""Recognising room names in free text a user typed.

WHY THIS IS NOT FREE-TEXT PARSING. Home Assistant's calendar dialog
offers a title, a description and times -- there is no field for rooms.
But the set of rooms is CLOSED and exact: the same map that labels the
schedule switches. So this does not guess what a room might be; it
checks a phrase against a known list.

That distinction is what makes it defensible. A parser inventing room
names from prose would be brittle in a place where being wrong means
cleaning the wrong part of somebody's home.

TWO RULES KEEP IT HONEST, and both exist because the failure they
prevent is silent:

  - WHOLE WORDS ONLY. A room called "Hall" must not match inside
    "Hallway". Substring matching would clean a room the user never
    named, and they would find out by hearing it.
  - AMBIGUITY REFUSES rather than picks. If two rooms both match the
    same words, the caller is told; it does not toss a coin.

And whatever is recognised is written back into the event, so the user
sees what was understood at the place they typed it. That is the
difference between a silent guess and a visible suggestion.
"""

from __future__ import annotations

import re
import unicodedata


def _normalise(text: str) -> str:
    """Case-folded, accent-stripped, punctuation reduced to spaces.

    Accents go because a user typing "salle a manger" for a room named
    "Salle à manger" means that room, and refusing on a missing accent
    would be pedantry rather than caution -- the set is still closed, so
    nothing new can be matched by relaxing this.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]+", " ", stripped.casefold())


def _tokens(text: str) -> list[str]:
    return _normalise(text).split()


class AmbiguousRoomError(ValueError):
    """Two or more rooms match the same words.

    Its own type because the caller must not treat it as "no rooms
    found": one means clean everywhere, the other means ask the user.
    """

    def __init__(self, phrase: str, candidates: list[str]) -> None:
        self.phrase = phrase
        self.candidates = candidates
        super().__init__(
            f"'{phrase}' matches more than one room: {', '.join(sorted(candidates))}"
        )


def match_rooms(text: str, room_names: dict[str, str]) -> list[str]:
    """Region ids for every room named in `text`, in the order given.

    Returns an empty list when nothing is recognised, which the caller
    reads as "clean everywhere" -- the common case, and the one a user
    gets by typing an ordinary title like "Tuesday clean".

    Raises AmbiguousRoomError when one phrase could be two rooms.

    ORDER FOLLOWS THE TEXT, not the map. Someone writing "kitchen then
    hallway" has expressed a sequence, and the schedule can carry one.
    """
    if not text or not room_names:
        return []

    words = _tokens(text)
    if not words:
        return []

    # Longest room names first: "Living Room" must win over a room
    # called "Room", and checking short names first would consume the
    # word and leave the longer name unmatchable.
    candidates = sorted(
        ((rid, _tokens(name)) for rid, name in room_names.items() if name),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    found: list[tuple[int, str]] = []
    consumed: set[int] = set()

    for rid, name_words in candidates:
        if not name_words:
            continue
        span = len(name_words)
        for start in range(len(words) - span + 1):
            if any(i in consumed for i in range(start, start + span)):
                continue
            if words[start:start + span] != name_words:
                continue
            # Whole words only. The slice comparison already enforces
            # this -- "hall" cannot equal "hallway" as a token -- which
            # is exactly why matching runs on tokens rather than on the
            # raw string.
            others = [
                other for other, other_words in candidates
                if other != rid and other_words == name_words
            ]
            if others:
                raise AmbiguousRoomError(
                    " ".join(name_words), [rid, *others]
                )
            found.append((start, rid))
            consumed.update(range(start, start + span))
            break

    return [rid for _position, rid in sorted(found)]


def describe_match(
    room_ids: list[str], room_names: dict[str, str]
) -> str:
    """What was understood, for writing back into the event.

    Empty ids give the everywhere-text rather than an empty string: a
    user who typed a plain title should see that their schedule covers
    the whole home, not a blank where an explanation belongs.
    """
    if not room_ids:
        return "Cleans the whole home."
    names = [room_names.get(rid) or rid for rid in room_ids]
    return "Cleans: " + ", ".join(names)
