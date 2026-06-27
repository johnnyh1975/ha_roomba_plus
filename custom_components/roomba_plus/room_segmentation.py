"""room_segmentation.py — dependency-free distance-transform + watershed
room/door segmentation, built on GridStore's visited-cell grid.

Pipeline (mirrors the validated scipy/skimage prototype, now fully
self-contained):
  1. Exact Euclidean distance transform of the visited mask (edt.py)
  2. Gaussian smoothing of the distance field (smooth.py) — without
     this, single-cell jaggedness in real visited-cell data creates many
     spurious small-radius local maxima (verified: ~19 regions instead
     of ~5 on real data without smoothing).
  3. Local-maxima seed-finding with a minimum-separation radius (peaks.py)
  4. Marker-controlled watershed flooding from those seeds (watershed.py)
  5. Saddle-depth-based region merging to undo residual over-segmentation
     (merge.py) — the RoomsSeg-style "is this ridge actually a wall" check

Known precision gap: iRobot's RoomsSeg patent (US10310507B2) Algorithm 1
additionally classifies obstacle pixels as wall vs. clutter using bump/IR
obstacle position data, which we do not currently capture for EPHEMERAL
robots (GridStore only tracks visited/free cells, not obstacle positions).
That classification step is NOT implemented here.
"""
from __future__ import annotations
from dataclasses import dataclass

from .edt import distance_transform_edt
from .smooth import gaussian_blur
from .peaks import find_peaks
from .watershed import watershed
from .merge import merge_regions


@dataclass
class RoomSegmentationResult:
    rooms: dict[int, set[tuple[int, int]]]
    doors: list[dict]
    dist: dict[tuple[int, int], float]
    seeds: list[tuple[int, int]]


def segment_rooms(
    cells: dict[tuple[int, int], float],
    cell_mm: float = 150.0,
    smoothing_sigma: float = 1.0,
    min_distance_cells: float = 8.0,
    merge_ratio: float = 0.55,
) -> RoomSegmentationResult:
    if not cells:
        return RoomSegmentationResult(rooms={}, doors=[], dist={}, seeds=[])

    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x_min, x_max = min(xs) - 1, max(xs) + 1
    y_min, y_max = min(ys) - 1, max(ys) + 1
    w = x_max - x_min + 1
    h = y_max - y_min + 1

    mask_2d = [[False] * w for _ in range(h)]
    for (gx, gy) in cells:
        mask_2d[gy - y_min][gx - x_min] = True

    dist_2d = distance_transform_edt(mask_2d)
    dist_smooth_2d = gaussian_blur(dist_2d, sigma=smoothing_sigma)

    dist = {c: dist_2d[c[1] - y_min][c[0] - x_min] for c in cells}
    dist_smooth = {c: dist_smooth_2d[c[1] - y_min][c[0] - x_min] for c in cells}

    seeds_coords = find_peaks(dist_smooth, min_distance=min_distance_cells)
    if not seeds_coords:
        seeds_coords = [next(iter(cells))]
    seeds = {coord: i for i, coord in enumerate(seeds_coords)}

    mask_set = set(cells.keys())
    elevation = {c: -dist_smooth[c] for c in mask_set}
    labels = watershed(elevation, seeds, mask_set)

    merged_labels, _saddle_log = merge_regions(labels, dist_smooth, merge_ratio=merge_ratio)

    rooms: dict[int, set[tuple[int, int]]] = {}
    for coord, r in merged_labels.items():
        rooms.setdefault(r, set()).add(coord)

    final_ids = sorted(rooms.keys())
    neighbors8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    doors: list[dict] = []
    checked: set[tuple[int, int]] = set()
    for a in final_ids:
        for b in final_ids:
            if a >= b or (a, b) in checked:
                continue
            checked.add((a, b))
            best = None
            for coord in rooms[a]:
                x, y = coord
                for dx, dy in neighbors8:
                    nb = (x + dx, y + dy)
                    if merged_labels.get(nb) == b:
                        # Use dist_smooth (same field merge_regions() uses
                        # for its own boundary_saddle), not the raw dist.
                        # On raw dist, a/b's shared boundary commonly
                        # includes touch points that sit right at the
                        # edge of the visited mask (dist == 1.0) but are
                        # NOT the geometric doorway — any pair has a near
                        # 1-in-1 chance some such point exists somewhere
                        # along the border, and the min() over the whole
                        # boundary collapses to that floor every time
                        # (confirmed in the field: 9/9 doors in one real
                        # archive all measured exactly 1 cell). The
                        # Gaussian-smoothed field doesn't have that
                        # integer floor, so the true narrowest point of
                        # the connecting corridor is what actually wins.
                        v = min(dist_smooth[coord], dist_smooth[nb])
                        if best is None or v < best[0]:
                            best = (v, coord)
            if best is not None:
                doors.append({
                    "a": a, "b": b, "cell": best[1],
                    "saddle_mm": best[0] * cell_mm,
                })

    return RoomSegmentationResult(rooms=rooms, doors=doors, dist=dist, seeds=seeds_coords)
