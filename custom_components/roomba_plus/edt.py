"""Dependency-free exact squared Euclidean distance transform.

Felzenszwalb & Huttenlocher (2004), "Distance Transforms of Sampled
Functions" — the standard O(n) per-dimension lower-envelope-of-parabolas
algorithm. Two 1D passes (columns then rows) give the exact 2D squared
EDT for a binary image.
"""
from __future__ import annotations
import math


def _dt_1d(f: list[float]) -> list[float]:
    """Exact 1D squared distance transform of f via lower envelope."""
    n = len(f)
    d = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -math.inf
    z[1] = math.inf
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = math.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def distance_transform_edt(mask: list[list[bool]]) -> list[list[float]]:
    """For each True cell, exact Euclidean distance to the nearest False
    cell (or grid edge, treated as False). False cells get distance 0.

    mask[y][x] indexing (row-major), matching numpy array convention.
    """
    h = len(mask)
    w = len(mask[0]) if h else 0
    INF = 1e18
    f = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            f[y][x] = INF if mask[y][x] else 0.0

    # Pass 1: along columns (each column independently)
    for x in range(w):
        col = [f[y][x] for y in range(h)]
        col = _dt_1d(col)
        for y in range(h):
            f[y][x] = col[y]

    # Pass 2: along rows
    for y in range(h):
        row = _dt_1d(f[y])
        f[y] = row

    return [[math.sqrt(v) for v in row] for row in f]
