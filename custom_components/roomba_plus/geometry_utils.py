"""Geometry shared by the spatial stores.

ONE DEFINITION PER SHAPE. `point_in_polygon` previously existed twice,
in grid_store and umf_aligner, byte-identical down to the AST. Both
decide whether a robot position falls inside a room outline; two copies
of that means two chances for a fix to land in one and not the other,
with the symptom being a room that counts coverage differently
depending on which store asked.
"""

from __future__ import annotations


def point_in_polygon(
    x: float, y: float, polygon: list[tuple[float, float]]
) -> bool:
    """Ray-casting point-in-polygon test.

    Returns True when (x, y) is inside the polygon.
    No external geometry library required.
    """
    inside = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if ((qy > y) != (py > y)) and (
            x < (px - qx) * (y - qy) / (py - qy) + qx
        ):
            inside = not inside
        px, py = qx, qy
    return inside
