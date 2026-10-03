"""The coverage heatmap's published frame matches what is drawn (I10).

The image is square and a house is not. The bounding box of the cells
does not say where a millimetre lands, so a card that stretched it over
the image misplaced its pins on every non-square floor plan.
"""
from __future__ import annotations

import io

from PIL import Image

from custom_components.roomba_plus.grid_store import (
    CELL_SIZE_MM,
    STUCK_HOTSPOT_THRESHOLD,
    GridStore,
    _cell_to_mm,
)


def _store(cells, hotspot):
    store = GridStore()
    for cell in cells:
        store._cells[cell] = 0.5
    store._stuck[hotspot] = {"count": STUCK_HOTSPOT_THRESHOLD}
    return store


def _red_pixels(png):
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    return [
        (x, y)
        for y in range(img.height) for x in range(img.width)
        if img.getpixel((x, y))[:3] == (220, 50, 50)
    ]


def test_a_known_point_lands_where_the_extent_says():
    # Twelve cells wide, two tall: far from square.
    cells = [(gx, gy) for gx in range(12) for gy in range(2)]
    hotspot = (9, 0)
    store = _store(cells, hotspot)
    size = 400

    extent = store.render_extent_mm(size)
    png = store.render_heatmap(size)

    scale = size / (extent["x_max"] - extent["x_min"])
    x_mm, y_mm = _cell_to_mm(*hotspot)
    expected = (
        int((x_mm - extent["x_min"]) * scale),
        int((extent["y_max"] - y_mm) * scale),
    )
    red = _red_pixels(png)
    assert red, "the hotspot is drawn"
    assert min(red) == expected, "its top-left pixel is where the extent says"


def test_the_extent_is_square_and_not_the_bounding_box():
    cells = [(gx, gy) for gx in range(12) for gy in range(2)]
    store = _store(cells, (0, 0))
    extent = store.render_extent_mm(400)
    bbox = store.bounding_box_mm()

    width = extent["x_max"] - extent["x_min"]
    height = extent["y_max"] - extent["y_min"]
    assert width == height == (bbox[1] - bbox[0]) + CELL_SIZE_MM
    assert extent["y_max"] == bbox[3]
    assert extent["y_min"] < bbox[2], "the shorter side is padded, not stretched"


def test_nothing_before_the_first_cell():
    assert GridStore().render_extent_mm() is None


def test_the_coverage_image_publishes_it():
    from custom_components.roomba_plus.image import RoombaCoverageImage

    image = RoombaCoverageImage.__new__(RoombaCoverageImage)
    image._grid_store = _store([(0, 0), (5, 0)], (0, 0))
    image._attr_image_last_updated = None
    attrs = image.extra_state_attributes
    assert attrs["render_extent_mm"] == image._grid_store.render_extent_mm()
    assert attrs["render_extent_mm"]["size_px"] == 400.0
