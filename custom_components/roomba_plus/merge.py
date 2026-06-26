"""Region-adjacency saddle-depth merge — same RoomsSeg-style "merge
regions whose dividing ridge isn't deep enough to be a real wall" rule
prototyped earlier, factored out as a standalone, dependency-free step."""
from __future__ import annotations


def merge_regions(
    labels: dict[tuple[int, int], int],
    dist: dict[tuple[int, int], float],
    merge_ratio: float = 0.55,
) -> tuple[dict[tuple[int, int], int], list[tuple[int, int, float]]]:
    """Merge adjacent regions whose boundary saddle depth is not
    sufficiently low relative to the smaller region's own peak distance.

    Returns (merged_labels, saddle_log) where saddle_log is a list of
    (region_a, region_b, saddle_value) for every adjacent pair checked
    BEFORE merging (region ids refer to the ORIGINAL label ids).
    """
    region_ids = sorted(set(labels.values()))
    region_cells: dict[int, list[tuple[int, int]]] = {r: [] for r in region_ids}
    for coord, r in labels.items():
        region_cells[r].append(coord)
    region_peak = {r: max(dist[c] for c in cells) for r, cells in region_cells.items()}

    neighbors8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    def boundary_saddle(a: int, b: int) -> float | None:
        best = None
        for coord, r in labels.items():
            if r != a:
                continue
            x, y = coord
            for dx, dy in neighbors8:
                nb = (x + dx, y + dy)
                if labels.get(nb) == b:
                    v = min(dist[coord], dist[nb])
                    if best is None or v < best:
                        best = v
        return best

    parent = {r: r for r in region_ids}

    def find(r: int) -> int:
        while parent[r] != r:
            r = parent[r]
        return r

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    saddle_log: list[tuple[int, int, float]] = []
    checked: set[tuple[int, int]] = set()
    for a in region_ids:
        for b in region_ids:
            if a >= b or (a, b) in checked:
                continue
            checked.add((a, b))
            saddle = boundary_saddle(a, b)
            if saddle is None:
                continue
            saddle_log.append((a, b, saddle))
            smaller_peak = min(region_peak[a], region_peak[b])
            ratio = saddle / smaller_peak if smaller_peak > 0 else 1.0
            if ratio >= merge_ratio:
                union(a, b)

    merged = {coord: find(r) for coord, r in labels.items()}
    return merged, saddle_log
