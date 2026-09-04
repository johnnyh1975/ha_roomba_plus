"""One definition per shape, checked rather than trusted.

Two helpers existed twice in this package. Both were found by comparing
function bodies structurally across all modules, not by anyone noticing:

  - `point_in_polygon`, in grid_store and umf_aligner, byte-identical
    down to the AST. Both decide whether a robot position falls inside a
    room outline.
  - the room-name slug, in image and room_cleaning. Those two had
    already DRIFTED: one collapsed repeated underscores and fell back to
    "room" for a name with no letters, the other did neither.

The slug pair is the instructive one. The map entity publishes room ids
as slugs for the xiaomi-vacuum-map-card, and the card sends them back to
clean_room, which resolves them against room names. Two rules that
differ by one character mean a room that can be tapped and not cleaned
-- and the docstring of one of them said exactly that, while the other
copy quietly disagreed with it.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"


class TestTheSlugRuleExistsOnce:
    def test_only_one_slug_definition(self):
        definitions = [
            path.name
            for path in PACKAGE.glob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
            and node.name in ("room_slug", "_slug", "_room_slug")
        ]

        assert definitions == ["const.py"], definitions

    def test_the_stricter_behaviour_was_kept(self):
        """A room id must never be empty -- the card rejects that. The
        copy that returned "" for a name with no letters was the one that
        lost."""
        from custom_components.roomba_plus.const import room_slug

        assert room_slug("---") == "room"
        assert room_slug("Küche  Nord") == "kuche_nord"

    def test_accents_decompose_rather_than_vanish(self):
        """The card validates ids and rejects umlauts and accents.
        German and Italian testers have both hit that."""
        from custom_components.roomba_plus.const import room_slug

        assert room_slug("Küche") == "kuche"
        assert room_slug("Salle d'eau") == "salle_d_eau"
        assert room_slug("Mattéo ") == "matteo"


class TestGeometryExistsOnce:
    def test_only_one_point_in_polygon(self):
        definitions = [
            path.name
            for path in PACKAGE.glob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)
            and node.name in ("point_in_polygon", "_point_in_polygon", "_point_in_polygon_grid")
        ]

        assert definitions == ["geometry_utils.py"], definitions

    def test_it_still_answers_the_obvious_cases(self):
        from custom_components.roomba_plus.geometry_utils import point_in_polygon

        square = [(0, 0), (10, 0), (10, 10), (0, 10)]

        assert point_in_polygon(5, 5, square) is True
        assert point_in_polygon(15, 5, square) is False


class TestNoNewStructuralDuplicates:
    """Catches the next pair before it drifts.

    Compares function bodies by AST shape, ignoring names and literals.
    Only functions of a reasonable size -- two three-line getters looking
    alike is not a finding."""

    #: Known-identical shapes that are not duplication: HA lifecycle
    #: hooks and constructors legitimately look the same across entity
    #: classes.
    _EXPECTED = {"async_added_to_hass", "__init__", "available", "native_value"}

    def test_no_two_large_functions_share_a_body(self):
        shapes: dict[str, list[str]] = {}
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name in self._EXPECTED:
                    continue
                if (node.end_lineno or 0) - node.lineno < 12:
                    continue
                body = [
                    statement
                    for statement in node.body
                    if not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                    )
                ]
                dump = "".join(
                    ast.dump(statement, annotate_fields=False) for statement in body
                )
                key = hashlib.md5(dump.encode()).hexdigest()
                shapes.setdefault(key, []).append(f"{path.stem}::{node.name}")

        duplicates = {k: v for k, v in shapes.items() if len(v) > 1}

        assert not duplicates, (
            "structurally identical function bodies: "
            + "; ".join(" == ".join(v) for v in duplicates.values())
        )
