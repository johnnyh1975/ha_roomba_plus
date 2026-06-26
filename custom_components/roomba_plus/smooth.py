"""Dependency-free separable Gaussian blur for a dense 2D float array.

Same role as scipy.ndimage.gaussian_filter(sigma=...): without this,
single-cell jaggedness in the raw visited-cell footprint creates many
spurious small-radius local maxima in the distance transform, causing
severe over-segmentation (verified: ~19 regions instead of ~5 on real
data without smoothing, see room_segmentation dev notes).
"""
from __future__ import annotations
import math


def _kernel_1d(sigma: float, radius: int) -> list[float]:
    k = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(-radius, radius + 1)]
    s = sum(k)
    return [v / s for v in k]


def gaussian_blur(grid: list[list[float]], sigma: float = 1.0) -> list[list[float]]:
    """Separable Gaussian blur, edge-clamped (replicate border values
    past the array edge — same convention scipy uses by default with
    mode='nearest', chosen here to avoid darkening the distance-transform
    peaks near the grid's own bounding-box edge)."""
    radius = max(1, int(4.0 * sigma + 0.5))
    kernel = _kernel_1d(sigma, radius)
    h = len(grid)
    w = len(grid[0]) if h else 0

    # horizontal pass
    tmp = [[0.0] * w for _ in range(h)]
    for y in range(h):
        row = grid[y]
        for x in range(w):
            acc = 0.0
            for i, kv in enumerate(kernel):
                xx = x + i - radius
                xx = 0 if xx < 0 else (w - 1 if xx >= w else xx)
                acc += kv * row[xx]
            tmp[y][x] = acc

    # vertical pass
    out = [[0.0] * w for _ in range(h)]
    for x in range(w):
        col = [tmp[y][x] for y in range(h)]
        for y in range(h):
            acc = 0.0
            for i, kv in enumerate(kernel):
                yy = y + i - radius
                yy = 0 if yy < 0 else (h - 1 if yy >= h else yy)
                acc += kv * col[yy]
            out[y][x] = acc

    return out
