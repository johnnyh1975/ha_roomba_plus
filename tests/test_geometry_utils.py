"""Tests for geometry_utils.py — the shapes and frames the stores share."""

from __future__ import annotations

import ast
from pathlib import Path

from custom_components.roomba_plus.geometry_utils import (
    pose_point_to_map_mm,
    raw_pose_mm_to_map,
)

_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"


def test_raw_pose_mm_to_map_swaps_the_axes():
    assert raw_pose_mm_to_map(500.0, 3000.0) == (3000.0, 500.0)


def test_pose_point_to_map_mm_converts_and_swaps():
    # Firmware centimetres, x then y; maps take millimetres, y then x.
    assert pose_point_to_map_mm({"x": 120, "y": 45}) == (450.0, 1200.0)


def test_pose_point_missing_axis_reads_as_zero():
    assert pose_point_to_map_mm({"x": 12}) == (0.0, 120.0)


def _reads_pose_point_axes(path: Path) -> list[int]:
    """Lines that read `.get("x")`/`.get("y")` off a name called `point`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "point"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in ("x", "y")
        ):
            lines.append(node.lineno)
    return lines


def test_the_maps_do_not_convert_pose_on_their_own():
    """ONE SWAP. image.py fed the maps with its own two lines until
    4.2.20; a second copy is how the tracker came to disagree with them.
    The map pipeline reads the pose point through the shared helper only."""
    assert _reads_pose_point_axes(_PKG / "image.py") == []
