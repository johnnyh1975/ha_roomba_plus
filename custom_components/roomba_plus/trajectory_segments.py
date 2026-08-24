"""Splits a mission's pose trail into continuously-tracked segments.

WHY THIS EXISTS.

A mission is not one trajectory. Every discontinuity — a relocalisation,
or a robot picked up and set down somewhere else — starts a stretch of
poses in a frame whose relationship to the previous one is unknown.
Treating the whole mission as one coordinate system is what smears an
accumulated grid.

AND THERE CAN BE SEVERAL PER MISSION. `MapRenderer._breaks` is a set, not
a flag, and the dock-anchor correction shipped assuming exactly one:
`picked_up` shifted an entire buffered stretch by a single offset, which
is right for one lift and wrong for two. A segment is the unit that makes
the general case expressible.

WHAT A SEGMENT IS.

An unbroken run of poses. Within it the frame is consistent, so
distances, interpolation and stamping along the path are all meaningful.
ACROSS segments none of that holds without first establishing how the two
frames relate.

WHAT THIS MODULE DOES NOT DO.

It does not anchor anything. Deciding where a segment sits in absolute
terms is a separate problem with separate evidence — the dock, overlap
with already-anchored coverage, or nothing at all. This module only says
where the boundaries are, because every one of those approaches needs
that first.

HA-free on purpose, like `grid_store` and `room_segmentation`: pure
geometry over plain lists, so it can be exercised from a script against
a backup without a running Home Assistant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Segment:
    """One continuously-tracked run of poses.

    `start` and `end` are indices into the mission's point list, with
    `end` exclusive, so `points[seg.start:seg.end]` is the run itself.
    Keeping indices rather than copies means callers can line the
    segment up against parallel lists — thetas, timestamps — without
    re-deriving anything.
    """

    start: int
    end: int
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def path_mm(self) -> float:
        """Distance travelled along the run.

        Not the same as the extent below: a robot meandering a small room
        covers a long path in a small area, and the two numbers answer
        different questions about whether a segment is anchorable.
        """
        return sum(
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(self.points, self.points[1:], strict=False)
        )

    @property
    def extent_mm(self) -> tuple[float, float]:
        """Bounding-box width and height of the run.

        The measure that matters for grid-overlap anchoring: matching two
        coverage patterns needs area, and a segment confined to a corridor
        offers little to match on however long its path.
        """
        if not self.points:
            return (0.0, 0.0)
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (max(xs) - min(xs), max(ys) - min(ys))


def split_into_segments(
    points: list[tuple[float, float]],
    breaks: set[int] | None = None,
) -> list[Segment]:
    """Cut `points` at every break index.

    A break index marks a point that does NOT connect to the one before
    it, so it begins a new segment rather than ending the previous one.
    That convention matches `MapRenderer._breaks`, which records the
    index of the pose that arrived after the discontinuity.

    Indices outside the list and a break at 0 are ignored: the first
    point cannot be discontinuous with a predecessor it does not have,
    and a stale index from a reset should not produce an empty segment.
    """
    if not points:
        return []
    cuts = sorted(
        i for i in (breaks or set()) if 0 < i < len(points)
    )
    bounds = [0, *cuts, len(points)]
    return [
        Segment(start=a, end=b, points=points[a:b])
        for a, b in zip(bounds, bounds[1:], strict=False)
        if b > a
    ]


def describe(segments: list[Segment]) -> str:
    """A one-line summary, for diagnostics and the rebuild script.

    Deliberately terse and stable: this ends up in logs and in a
    tester's paste, where a fixed shape is easier to compare across
    reports than prose.
    """
    if not segments:
        return "no segments"
    parts = [
        f"{s.length}pts/{s.path_mm / 1000:.1f}m"
        for s in segments
    ]
    return f"{len(segments)} segment(s): " + ", ".join(parts)
