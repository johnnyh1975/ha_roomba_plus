"""Tests for trajectory_segments — splitting a mission at its breaks."""

from __future__ import annotations

from custom_components.roomba_plus.trajectory_segments import (
    Segment,
    describe,
    split_into_segments,
)


class TestSeveralBreaksInOneMission:
    """The case the shipped dock-anchor correction could not express.

    `picked_up` was a boolean: one lift, one offset, applied to the whole
    buffered stretch. Two lifts leave two independent offsets, and a
    single shift is then as wrong for the second stretch as the linear
    ramp it replaced was for the first.

    `MapRenderer._breaks` was always a set. The correction is what
    assumed there could only be one.
    """

    @staticmethod
    def _trail(n: int) -> list[tuple[float, float]]:
        return [(float(i * 100), 0.0) for i in range(n)]

    def test_two_breaks_give_three_segments(self):
        segments = split_into_segments(self._trail(30), breaks={10, 20})

        assert [s.length for s in segments] == [10, 10, 10]

    def test_each_segment_carries_its_own_points(self):
        """Callers correct segments independently, so a segment that
        does not know its own points would be useless."""
        segments = split_into_segments(self._trail(30), breaks={10, 20})

        assert segments[0].points[0] == (0.0, 0.0)
        assert segments[1].points[0] == (1000.0, 0.0)
        assert segments[2].points[0] == (2000.0, 0.0)

    def test_indices_line_up_with_the_original_list(self):
        """Thetas and timestamps are parallel lists. A segment that
        reported copies without indices would force every caller to
        re-derive the alignment."""
        points = self._trail(30)
        segments = split_into_segments(points, breaks={10, 20})

        for s in segments:
            assert points[s.start : s.end] == s.points

    def test_no_breaks_is_one_segment(self):
        segments = split_into_segments(self._trail(30), breaks=set())

        assert len(segments) == 1
        assert segments[0].length == 30

    def test_none_behaves_like_no_breaks(self):
        assert split_into_segments(self._trail(5), breaks=None) == (
            split_into_segments(self._trail(5), breaks=set())
        )


class TestBoundaryCases:
    """Stale or nonsensical indices must not produce empty segments.

    `_breaks` survives across a `replace_range` and is filtered there,
    but a caller reading a persisted mission could hand over anything.
    An empty segment would divide by zero in a correction weight.
    """

    def test_a_break_at_zero_is_ignored(self):
        """The first point cannot be discontinuous with a predecessor
        it does not have."""
        segments = split_into_segments([(0.0, 0.0), (1.0, 0.0)], breaks={0})

        assert len(segments) == 1

    def test_an_index_past_the_end_is_ignored(self):
        segments = split_into_segments([(0.0, 0.0), (1.0, 0.0)], breaks={99})

        assert len(segments) == 1

    def test_an_empty_trail_gives_no_segments(self):
        assert split_into_segments([], breaks={1}) == []

    def test_no_segment_is_ever_empty(self):
        """A zero-length segment would divide by zero wherever a
        correction is spread across one."""
        segments = split_into_segments(
            [(float(i), 0.0) for i in range(5)], breaks={1, 2, 3, 4}
        )

        assert all(s.length > 0 for s in segments)


class TestSegmentMeasures:
    """Path length and extent answer different questions.

    A segment is anchorable by grid overlap only if it covers area;
    matching two coverage patterns needs something to match on. Path
    length alone does not say that -- a robot meandering one small room
    travels far inside a small box.
    """

    def test_path_and_extent_differ_for_a_meander(self):
        # Back and forth in a 1 m strip: long path, small extent.
        points = []
        for i in range(20):
            points.append((0.0 if i % 2 else 1000.0, float(i * 10)))
        seg = Segment(start=0, end=len(points), points=points)

        assert seg.path_mm > 15000.0
        assert seg.extent_mm[0] == 1000.0

    def test_a_single_point_has_no_path(self):
        seg = Segment(start=0, end=1, points=[(5.0, 5.0)])

        assert seg.path_mm == 0.0
        assert seg.extent_mm == (0.0, 0.0)

    def test_describe_is_stable_and_short(self):
        """This ends up in logs and tester pastes, where a fixed shape
        compares more easily than prose."""
        segments = split_into_segments(
            [(float(i * 100), 0.0) for i in range(30)], breaks={10}
        )

        text = describe(segments)

        assert text.startswith("2 segment(s):")

    def test_describe_handles_nothing(self):
        assert describe([]) == "no segments"
