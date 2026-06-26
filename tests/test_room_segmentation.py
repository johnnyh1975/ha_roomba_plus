"""Tests for the dependency-free room_segmentation pipeline.

Run with: python3 -m pytest test_room_segmentation.py -v
(no project/HA dependencies — these run standalone in this dev folder)
"""
import math
import pytest
from custom_components.roomba_plus.edt import distance_transform_edt
from custom_components.roomba_plus.smooth import gaussian_blur
from custom_components.roomba_plus.peaks import find_peaks
from custom_components.roomba_plus.watershed import watershed
from custom_components.roomba_plus.merge import merge_regions
from custom_components.roomba_plus.room_segmentation import segment_rooms


def _rect_cells(x0, x1, y0, y1):
    return {(x, y): 1.0 for x in range(x0, x1) for y in range(y0, y1)}


class TestEDT:
    def test_rectangle_peak_at_center(self):
        mask = [[False]*7 for _ in range(7)]
        for y in range(1, 6):
            for x in range(1, 6):
                mask[y][x] = True
        dist = distance_transform_edt(mask)
        # center of a 5x5 interior block, 1 cell from border on all sides
        assert dist[3][3] == pytest.approx(math.sqrt(2) * 0 + 2.0, abs=0.01) or dist[3][3] >= 1.9
        # cells adjacent to the False border get distance 1
        assert dist[1][1] == pytest.approx(1.0, abs=0.01)

    def test_false_cell_is_zero(self):
        mask = [[True, False], [True, True]]
        dist = distance_transform_edt(mask)
        assert dist[0][1] == 0.0


class TestPeaks:
    def test_single_peak_found(self):
        values = {(x, y): -((x-5)**2 + (y-5)**2) for x in range(11) for y in range(11)}
        peaks = find_peaks(values, min_distance=2)
        assert peaks[0] == (5, 5)

    def test_two_separated_peaks_both_found(self):
        values = {}
        for x in range(20):
            for y in range(10):
                values[(x, y)] = -((x-3)**2 + (y-3)**2)
        for x in range(20):
            for y in range(10):
                v2 = -((x-15)**2 + (y-5)**2)
                if v2 > values.get((x, y), -1e9):
                    values[(x, y)] = v2
        peaks = set(find_peaks(values, min_distance=3))
        assert (3, 3) in peaks
        assert (15, 5) in peaks
        assert len(peaks) == 2

    def test_close_peaks_only_one_survives(self):
        # Two adjacent single-cell bumps closer than min_distance: only
        # the higher one should survive.
        values = {(x, y): 0.0 for x in range(10) for y in range(10)}
        values[(5, 5)] = 10.0
        values[(6, 5)] = 9.0
        peaks = find_peaks(values, min_distance=3)
        assert (5, 5) in peaks
        assert (6, 5) not in peaks


class TestWatershed:
    def test_two_rooms_split_by_narrow_corridor(self):
        # Two 5x5 rooms connected by a 1-cell-wide, 3-cell-long corridor.
        cells = _rect_cells(0, 5, 0, 5)
        cells.update(_rect_cells(8, 13, 0, 5))
        for x in range(5, 8):
            cells[(x, 2)] = 1.0
        xs = [c[0] for c in cells]; ys = [c[1] for c in cells]
        x_min, x_max = min(xs)-1, max(xs)+1
        y_min, y_max = min(ys)-1, max(ys)+1
        mask_2d = [[False]*(x_max-x_min+1) for _ in range(y_max-y_min+1)]
        for (gx, gy) in cells:
            mask_2d[gy-y_min][gx-x_min] = True
        dist_2d = distance_transform_edt(mask_2d)
        dist = {c: dist_2d[c[1]-y_min][c[0]-x_min] for c in cells}
        seeds_coords = find_peaks(dist, min_distance=3)
        assert len(seeds_coords) == 2
        seeds = {c: i for i, c in enumerate(seeds_coords)}
        labels = watershed({c: -dist[c] for c in cells}, seeds, set(cells))
        assert labels[(2, 2)] != labels[(10, 2)]  # the two room centers differ
        assert len(labels) == len(cells)  # every cell got a label


class TestMergeRegions:
    def test_shallow_saddle_gets_merged(self):
        # One big blob, artificially split into two labels by a fake
        # boundary down the middle -- the "saddle" between them (deep in
        # the blob interior) should NOT be a real constriction, so they
        # must merge back into one region.
        cells = _rect_cells(0, 10, 0, 10)
        xs = [c[0] for c in cells]; ys = [c[1] for c in cells]
        mask_2d = [[True]*10 for _ in range(10)]
        dist_2d = distance_transform_edt(mask_2d)
        dist = {c: dist_2d[c[1]][c[0]] for c in cells}
        labels = {c: (0 if c[0] < 5 else 1) for c in cells}
        merged, _ = merge_regions(labels, dist, merge_ratio=0.55)
        assert len(set(merged.values())) == 1

    def test_genuine_narrow_corridor_not_merged(self):
        cells = _rect_cells(0, 5, 0, 5)
        cells.update(_rect_cells(8, 13, 0, 5))
        for x in range(5, 8):
            cells[(x, 2)] = 1.0
        xs = [c[0] for c in cells]; ys = [c[1] for c in cells]
        x_min, x_max = min(xs)-1, max(xs)+1
        y_min, y_max = min(ys)-1, max(ys)+1
        mask_2d = [[False]*(x_max-x_min+1) for _ in range(y_max-y_min+1)]
        for (gx, gy) in cells:
            mask_2d[gy-y_min][gx-x_min] = True
        dist_2d = distance_transform_edt(mask_2d)
        dist = {c: dist_2d[c[1]-y_min][c[0]-x_min] for c in cells}
        labels = {c: (0 if c[0] < 6 else 1) for c in cells}
        merged, log = merge_regions(labels, dist, merge_ratio=0.55)
        assert len(set(merged.values())) == 2


class TestSegmentRoomsEndToEnd:
    def test_two_rooms_one_door(self):
        cells = _rect_cells(0, 6, 0, 6)
        cells.update(_rect_cells(9, 15, 0, 6))
        for x in range(6, 9):
            cells[(x, 3)] = 1.0
        result = segment_rooms(cells, min_distance_cells=3.0)
        assert len(result.rooms) == 2
        assert len(result.doors) == 1
        door = result.doors[0]
        assert 6 <= door["cell"][0] <= 9  # door sits in the connecting corridor

    def test_empty_input(self):
        result = segment_rooms({})
        assert result.rooms == {}
        assert result.doors == []

    def test_single_cell_input_one_room_no_doors(self):
        result = segment_rooms({(0, 0): 1.0})
        assert len(result.rooms) == 1
        assert result.doors == []

    def test_real_grid_data_gives_five_stable_rooms(self):
        import json
        import os
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "sample_grid_980_og.json"
        )
        grid = json.load(open(fixture_path))["data"]
        cells = {}
        for k, w in grid["cells"].items():
            gx, gy = map(int, k.split(","))
            cells[(gx, gy)] = w
        result = segment_rooms(cells, min_distance_cells=8.0)
        assert len(result.rooms) == 5
        sizes = sorted((len(c) for c in result.rooms.values()), reverse=True)
        assert sizes == [669, 420, 347, 308, 264]
