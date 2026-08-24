"""Places an unanchored segment by matching its coverage against known coverage.

WHY THIS EXISTS.

The dock verifies the position of one segment: the one that ends at the
dock. Everything before a discontinuity is unverified by that
measurement, so `_interpolate_and_correct_segment` deliberately leaves
it alone. Those segments hold real coverage that currently cannot enter
the accumulated grid, because writing them at an unknown offset is how a
grid gets smeared permanently.

THE APPROACH.

Take the cells a segment covers, slide and turn that pattern against
cells already known to be correct, and keep the placement where the two
overlap most. No feature has to be recognised and no correspondence has
to be guessed — the whole pattern carries the fit, which is why this is
the safer of the two anchoring methods.

WHAT IT REFUSES TO DO.

A wrong placement folds the map onto itself, and no later mission
repairs that. So this returns `None` rather than a doubtful answer in
three cases, each checked separately below: too little overlap to mean
anything, a winner no better than its runner-up, and a segment too small
to carry a pattern at all.

Refusing is cheap. An unanchored segment still shows in the live view,
where it is honest about position being uncertain; it simply does not
enter the grid. A wrong anchor corrupts data that nothing else will fix.

WHAT IT IS NOT.

A simplified landmark alignment of the sort the robot runs internally,
with a handful of cells instead of thousands of visual features. Worth
saying plainly: this reconstructs coarsely what the firmware does not
export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Mirrors grid_store.CELL_SIZE_MM.
CELL_SIZE_MM = 150.0

#: How far a placement is searched, in cells. A robot that was picked up
#: is normally set down in the same or an adjoining room, not across the
#: house -- and a wider search does not merely cost time, it admits more
#: false candidates for the ambiguity check to reject.
SEARCH_RADIUS_CELLS = 20

#: Rotations tried, in degrees. Coarse on purpose: a finer sweep
#: multiplies both cost and the number of near-equal candidates, and the
#: grid's own 150 mm resolution puts a floor on how much precision is
#: meaningful anyway.
ROTATIONS_DEG = (0, 15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180,
                 195, 210, 225, 240, 255, 270, 285, 300, 315, 330, 345)

#: Below this many overlapping cells a match means nothing -- two small
#: blobs overlap somewhere almost regardless of where they belong.
MIN_OVERLAP_CELLS = 40

#: The winner must beat the runner-up by this factor. A pattern that
#: fits two places nearly equally has not identified either of them, and
#: picking one would be a coin toss with the map as the stake.
MIN_MARGIN = 1.25


@dataclass(frozen=True)
class Placement:
    """Where a segment belongs, and how well that was established."""

    dx_mm: float
    dy_mm: float
    rotation_deg: float
    overlap_cells: int
    margin: float


def _cells_of(points: list[tuple[float, float]]) -> set[tuple[int, int]]:
    return {
        (int(math.floor(x / CELL_SIZE_MM)), int(math.floor(y / CELL_SIZE_MM)))
        for x, y in points
    }


def _rotated(
    points: list[tuple[float, float]], degrees: float
) -> list[tuple[float, float]]:
    """Turn about the segment's own centroid.

    About the centroid, not the origin: rotating about the dock would
    couple the turn to how far away the segment happens to lie, so the
    same angle would displace a distant segment much further and the
    translation search would have to cover that too.
    """
    if degrees == 0:
        return points
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    rad = math.radians(degrees)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return [
        (
            cx + (x - cx) * cos_r - (y - cy) * sin_r,
            cy + (x - cx) * sin_r + (y - cy) * cos_r,
        )
        for x, y in points
    ]


def anchor_segment(
    segment_points: list[tuple[float, float]],
    known_cells: set[tuple[int, int]],
    *,
    search_radius_cells: int = SEARCH_RADIUS_CELLS,
    min_overlap_cells: int = MIN_OVERLAP_CELLS,
    min_margin: float = MIN_MARGIN,
) -> Placement | None:
    """Best placement of `segment_points` against `known_cells`, or None.

    `None` means "not established", never "no offset". A caller must not
    read it as zero: an unanchored segment stays out of the grid, and
    treating it as already-correct would put it in at whatever offset it
    happened to have.
    """
    if len(segment_points) < 2 or not known_cells:
        return None

    scores: list[tuple[int, float, float, float]] = []
    for deg in ROTATIONS_DEG:
        turned = _rotated(segment_points, deg)
        base = _cells_of(turned)
        if not base:
            continue
        for gx in range(-search_radius_cells, search_radius_cells + 1):
            for gy in range(-search_radius_cells, search_radius_cells + 1):
                shifted = {(cx + gx, cy + gy) for cx, cy in base}
                overlap = len(shifted & known_cells)
                if overlap:
                    scores.append(
                        (overlap, gx * CELL_SIZE_MM, gy * CELL_SIZE_MM, float(deg))
                    )

    if not scores:
        return None
    scores.sort(key=lambda s: -s[0])
    best = scores[0]
    if best[0] < min_overlap_cells:
        return None

    # The runner-up has to be a genuinely DIFFERENT placement. Neighbouring
    # offsets of the true one score almost as well by construction, and
    # comparing against those would reject every correct answer.
    runner_up = next(
        (
            s
            for s in scores[1:]
            if math.hypot(s[1] - best[1], s[2] - best[2]) > 3 * CELL_SIZE_MM
            or abs(s[3] - best[3]) > 30
        ),
        None,
    )
    margin = best[0] / runner_up[0] if runner_up and runner_up[0] else float("inf")
    if margin < min_margin:
        return None

    return Placement(
        dx_mm=best[1],
        dy_mm=best[2],
        rotation_deg=best[3],
        overlap_cells=best[0],
        margin=margin,
    )
