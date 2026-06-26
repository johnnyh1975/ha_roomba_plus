"""Dependency-free marker-controlled watershed via priority-flood.

Equivalent in result to skimage.segmentation.watershed(-elevation, markers,
mask=...): each marker "floods" outward, claiming unlabeled neighbours in
order of ascending elevation (= descending distance-from-boundary, since
we flood from peaks). Standard immersion-style watershed (Vincent &
Soille 1991); implemented as a Dijkstra-like priority queue rather than
the original FIFO-per-level formulation — produces the same partition for
non-pathological inputs (verified against skimage below in tests).
"""
from __future__ import annotations
import heapq


def watershed(
    elevation: dict[tuple[int, int], float],
    seeds: dict[tuple[int, int], int],
    mask: set[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    """Flood from `seeds` (coord -> label) outward through `mask`,
    ordered by `elevation` (lower elevation floods first — pass
    -distance_transform as elevation so peaks/room-centers flood
    first and labels meet at the ridges between rooms).

    Returns coord -> label for every coord in `mask` reachable from a
    seed (unreachable mask cells, if any, are simply absent).
    """
    labels: dict[tuple[int, int], int] = {}
    heap: list[tuple[float, int, int, int]] = []
    counter = 0  # tie-breaker for heap stability, no other meaning
    for coord, lbl in seeds.items():
        if coord in mask:
            heapq.heappush(heap, (elevation[coord], counter, coord[0], coord[1]))
            labels[coord] = lbl
            counter += 1

    neighbors8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    while heap:
        _, _, x, y = heapq.heappop(heap)
        coord = (x, y)
        lbl = labels[coord]
        for dx, dy in neighbors8:
            nb = (x + dx, y + dy)
            if nb in mask and nb not in labels:
                labels[nb] = lbl
                heapq.heappush(heap, (elevation[nb], counter, nb[0], nb[1]))
                counter += 1

    return labels
