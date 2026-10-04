"""Geometry shared by the spatial stores.

ONE DEFINITION PER SHAPE. `point_in_polygon` previously existed twice,
in grid_store and umf_aligner, byte-identical down to the AST. Both
decide whether a robot position falls inside a room outline; two copies
of that means two chances for a fix to land in one and not the other,
with the symptom being a room that counts coverage differently
depending on which store asked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import POSE_POINT_CM_TO_MM


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


def raw_pose_mm_to_map(x_mm: float, y_mm: float) -> tuple[float, float]:
    """A robot position, in millimetres, in the frame every map is drawn in.

    THE AXES ARE SWAPPED, and this is the one place that says so. The
    firmware's `pose.point.x` runs along the maps' y axis and the other
    way round -- roombapy's own threaded client swapped them for that
    reason, and the cleaning path, the coverage grid, the door markers,
    the UMF aligner and the stuck pins all use the swapped frame.

    The robot's own reports are not swapped: the tracker's `x_mm`/`y_mm`,
    the vacuum's `position` and the `error_position_mm` stored with a
    mission carry the firmware's order. Anything that puts one of those
    on a map, or hands it to the aligner, goes through here (I11 of the
    card plan; `repairs.py` named the wrong room for a recurring error
    because it did not).
    """
    return y_mm, x_mm


def pose_point_to_map_mm(point: Mapping[str, Any]) -> tuple[float, float]:
    """The firmware's `pose.point` (centimetres) in map millimetres."""
    return raw_pose_mm_to_map(
        float(point.get("x", 0)) * POSE_POINT_CM_TO_MM,
        float(point.get("y", 0)) * POSE_POINT_CM_TO_MM,
    )
