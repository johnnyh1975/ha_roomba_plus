"""Tests for segment_anchoring — placing a segment by pattern overlap."""

from __future__ import annotations

import math

from custom_components.roomba_plus.segment_anchoring import (
    CELL_SIZE_MM,
    anchor_segment,
)


def _meander(width_cells: int, height_cells: int, ox: float = 0.0, oy: float = 0.0):
    """A boustrophedon sweep — the shape a coverage run makes."""
    points = []
    for row in range(height_cells):
        xs = range(width_cells) if row % 2 == 0 else range(width_cells - 1, -1, -1)
        for col in xs:
            points.append((ox + col * CELL_SIZE_MM, oy + row * CELL_SIZE_MM))
    return points


def _distinctive(ox: float = 0.0, oy: float = 0.0):
    """An L-shaped run, which is what a real home produces.

    A FILLED RECTANGLE CANNOT LOCATE ITSELF, and the first version of
    these tests used one. Shift it a few cells and it still overlaps
    almost as well, so the ambiguity check refuses it -- correctly. That
    refusal looked like a bug until the same check placed an L-shape
    exactly and unambiguously.

    Real coverage is irregular: rooms meet at corners, furniture leaves
    holes, walls turn. The irregularity is what makes a pattern
    matchable, and a test on a perfect rectangle measures the one case
    where the method genuinely cannot work.
    """
    return _meander(14, 5, ox, oy) + _meander(5, 14, ox, oy)


def _cells(points):
    return {
        (int(math.floor(x / CELL_SIZE_MM)), int(math.floor(y / CELL_SIZE_MM)))
        for x, y in points
    }


class TestFindingTheOffset:
    """The case this exists for: a segment holding real coverage that
    the dock measurement cannot reach, because it lies before a break."""

    def test_a_pure_translation_is_recovered(self):
        known = _cells(_distinctive())
        displaced = _distinctive(ox=5 * CELL_SIZE_MM, oy=3 * CELL_SIZE_MM)

        placement = anchor_segment(displaced, known)

        assert placement is not None
        assert placement.dx_mm == -5 * CELL_SIZE_MM
        assert placement.dy_mm == -3 * CELL_SIZE_MM

    def test_an_exact_overlay_needs_no_shift(self):
        known = _cells(_distinctive())

        placement = anchor_segment(_distinctive(), known)

        assert placement is not None
        assert (placement.dx_mm, placement.dy_mm) == (0.0, 0.0)

    def test_the_result_reports_how_well_it_matched(self):
        """A caller deciding whether to trust a placement needs the
        evidence, not just the answer."""
        known = _cells(_distinctive())

        placement = anchor_segment(_distinctive(), known)

        assert placement is not None
        assert placement.overlap_cells > 0
        assert placement.margin > 1.0


class TestRefusingRatherThanGuessing:
    """A wrong placement folds the map onto itself and no later mission
    repairs it. Refusing costs only that a segment stays out of the grid
    -- it still shows in the live view, where uncertainty is honest.

    These four are the point of the module. If any of them started
    returning a Placement, the module would be actively dangerous.
    """

    def test_a_tiny_segment_is_refused(self):
        """Two blobs overlap somewhere almost regardless of where they
        belong, so a small pattern identifies nothing."""
        known = _cells(_meander(12, 12))

        assert anchor_segment(_meander(2, 2), known) is None

    def test_a_featureless_repeat_is_refused(self):
        """A plain corridor fits equally well at many offsets along its
        own length. Picking one would be a coin toss."""
        known = _cells(_meander(30, 1))
        segment = _meander(8, 1)

        placement = anchor_segment(segment, known)

        assert placement is None, "a straight run cannot locate itself"

    def test_an_empty_reference_is_refused(self):
        assert anchor_segment(_meander(10, 10), set()) is None

    def test_a_single_point_is_refused(self):
        known = _cells(_meander(12, 12))

        assert anchor_segment([(0.0, 0.0)], known) is None


class TestNoneIsNotZero:
    """The distinction a caller must not blur.

    `None` means "not established", never "no offset needed". Reading it
    as zero would write the segment into the grid at whatever offset it
    happened to carry -- the exact outcome this module exists to
    prevent.
    """

    def test_a_refusal_is_distinguishable_from_a_zero_placement(self):
        known = _cells(_distinctive())

        refused = anchor_segment(_meander(2, 2), known)
        zero = anchor_segment(_distinctive(), known)

        assert refused is None
        assert zero is not None
        assert (zero.dx_mm, zero.dy_mm) == (0.0, 0.0)


class TestSearchBounds:
    """A robot that was picked up is set down in the same or an
    adjoining room. A wider search does not merely cost time -- it
    admits more false candidates for the ambiguity check to weigh.
    """

    def test_a_displacement_beyond_the_search_radius_is_refused(self):
        known = _cells(_distinctive())
        far = _distinctive(ox=100 * CELL_SIZE_MM, oy=100 * CELL_SIZE_MM)

        assert anchor_segment(far, known, search_radius_cells=5) is None

    def test_a_narrower_search_still_finds_a_near_offset(self):
        known = _cells(_distinctive())
        near = _distinctive(ox=2 * CELL_SIZE_MM)

        placement = anchor_segment(near, known, search_radius_cells=5)

        assert placement is not None
        assert placement.dx_mm == -2 * CELL_SIZE_MM


class TestAmbiguityIsRefusedEvenWhenExact:
    """The finding that corrected these tests, kept as a check.

    A filled rectangle laid exactly over itself is a PERFECT match and
    is still refused, because a few cells' shift scores almost as well.
    The method has not identified anything there, and saying so is the
    whole safety argument.

    This looked like a bug when the first version of these tests used
    rectangles throughout. It is the behaviour working.
    """

    def test_a_rectangle_cannot_locate_itself(self):
        known = _cells(_meander(12, 12))

        assert anchor_segment(_meander(12, 12), known) is None

    def test_an_irregular_shape_can(self):
        """Same size, same overlap, different answer -- the difference
        is entirely the shape's distinctiveness."""
        known = _cells(_distinctive())

        placement = anchor_segment(_distinctive(), known)

        assert placement is not None
