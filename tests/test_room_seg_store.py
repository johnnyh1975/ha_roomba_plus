"""Tests for RoomSegStore — identity-stable room/door matching across
recomputations, recompute gating, and persistence round-trip."""
from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom


def _rect(x0, x1, y0, y1):
    return {(x, y): 1.0 for x in range(x0, x1) for y in range(y0, y1)}


def _two_room_grid():
    cells = _rect(0, 6, 0, 6)
    cells.update(_rect(9, 15, 0, 6))
    for x in range(6, 9):
        cells[(x, 3)] = 1.0
    return cells


def _store(**kwargs):
    """Test helper: small min_distance_cells so the tiny synthetic grids
    in this file (6x6 rooms) actually produce multiple seeds -- production
    code uses the validated default of 8.0 (see ROOM_SEGMENTATION_NOTES.md
    and tests/test_room_segmentation.py's real-data regression test)."""
    kwargs.setdefault("min_distance_cells", 3.0)
    return RoomSegStore(**kwargs)



class TestMaybeRecompute:
    def test_first_call_always_recomputes(self):
        store = _store()
        ran = store.maybe_recompute(_two_room_grid())
        assert ran is True
        assert len(store.rooms) == 2

    def test_empty_cells_does_not_recompute(self):
        store = _store()
        ran = store.maybe_recompute({})
        assert ran is False
        assert store.rooms == {}

    def test_small_growth_skips_recompute(self):
        store = _store()
        cells = _two_room_grid()
        store.maybe_recompute(cells)
        first_room_ids = set(store.rooms.keys())

        # add fewer than MIN_NEW_CELLS_TO_RECOMPUTE new cells
        cells2 = dict(cells)
        cells2[(20, 20)] = 1.0
        ran = store.maybe_recompute(cells2)
        assert ran is False
        assert set(store.rooms.keys()) == first_room_ids

    def test_large_growth_triggers_recompute(self):
        store = _store()
        cells = _two_room_grid()
        store.maybe_recompute(cells)

        cells2 = dict(cells)
        cells2.update(_rect(20, 30, 20, 30))  # 100 new cells, well over threshold
        ran = store.maybe_recompute(cells2)
        assert ran is True
        assert len(store.rooms) == 3


class TestIdentityMatchingAcrossRecomputes:
    def test_name_and_confirmation_survive_recompute(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.rooms) == 2

        room_ids = list(store.rooms.keys())
        store.rename_room(room_ids[0], "Wohnzimmer")
        store.confirm_room(room_ids[0])

        # Recompute with a slightly grown but largely-overlapping grid —
        # the room should be RECOGNIZED as the same room, not recreated.
        cells = _two_room_grid()
        cells.update(_rect(0, 6, 6, 8))  # grow room 1 a bit (still mostly overlapping)
        cells.update(_rect(30, 40, 30, 40))  # unrelated new room far away, forces recompute
        store.maybe_recompute(cells)

        named_room = store.rooms[room_ids[0]]
        assert named_room.name == "Wohnzimmer"
        assert named_room.confirmed is True

    def test_unmatched_existing_room_is_kept_not_deleted(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        store.rename_room(room_ids[0], "Kueche")

        # Recompute with a COMPLETELY disjoint grid (simulating a mission
        # that only covered a totally different area) -- the old named
        # room must still be present afterward, not silently dropped.
        disjoint_cells = _rect(100, 110, 100, 106)
        disjoint_cells.update(_rect(113, 119, 100, 106))
        for x in range(106, 109):
            disjoint_cells[(x, 103)] = 1.0
        store.maybe_recompute(disjoint_cells)

        assert room_ids[0] in store.rooms
        assert store.rooms[room_ids[0]].name == "Kueche"

    def test_door_room_references_use_matched_persisted_ids(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.doors) == 1
        door = store.doors[0]
        assert door.room_a in store.rooms
        assert door.room_b in store.rooms

    def test_door_identity_stable_across_recompute(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        first_door_id = store.doors[0].id

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))
        store.maybe_recompute(cells)

        assert any(d.id == first_door_id for d in store.doors)

    def test_unmatched_existing_door_is_kept_not_deleted(self):
        """Mirrors test_unmatched_existing_room_is_kept_not_deleted, for
        doors. GridStore decays/prunes low-traffic cells every mission —
        a narrow, infrequently-crossed doorway's connecting cells can
        legitimately drop out of the visited-cell set for one recompute
        even while both rooms it connects are still very much there and
        still get re-matched to their existing persisted ids. The door
        itself (and its observations history) must not be silently
        deleted just because that one recompute didn't re-detect a
        connection between them."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.doors) == 1
        door_id = store.doors[0].id
        room_ids = set(store.rooms.keys())

        # Same two room blobs, corridor cells (6,3)/(7,3)/(8,3) removed —
        # rooms are temporarily disconnected in this recompute's input.
        # An unrelated, far-away rect is added purely so the growth gate
        # in maybe_recompute() actually triggers a recompute (mirrors a
        # realistic mission: most of the home grows/stays stable while
        # one narrow corridor happens to decay out).
        cells = _rect(0, 6, 0, 6)
        cells.update(_rect(9, 15, 0, 6))
        cells.update(_rect(30, 40, 30, 40))
        store.maybe_recompute(cells)

        # Both original rooms still matched to their existing ids...
        assert room_ids <= set(store.rooms.keys())
        # ...yet the original door must still be present, identity intact,
        # even though this round found no door between that exact pair.
        assert any(d.id == door_id for d in store.doors), (
            "door must be preserved when its corridor temporarily drops "
            "out of the visited-cell set, even though it wasn't "
            "re-detected this round"
        )


class TestUnconfirmedRooms:
    def test_unconfirmed_room_included(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.unconfirmed_rooms) == 2
        assert store.has_unconfirmed_rooms is True

    def test_confirmed_room_excluded(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_id = next(iter(store.rooms))
        store.confirm_room(room_id)
        assert len(store.unconfirmed_rooms) == 1
        assert store.has_unconfirmed_rooms is True

    def test_hidden_unconfirmed_room_excluded(self):
        """Mirrors ZoneStore: no repair issue should fire for a hidden room,
        even if it's also unconfirmed."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        for room_id in store.rooms:
            store.hide_room(room_id)
        assert store.unconfirmed_rooms == []
        assert store.has_unconfirmed_rooms is False

    def test_no_rooms_means_no_unconfirmed(self):
        store = _store()
        assert store.unconfirmed_rooms == []
        assert store.has_unconfirmed_rooms is False

    def test_all_confirmed_means_no_unconfirmed(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        for room_id in store.rooms:
            store.confirm_room(room_id)
        assert store.has_unconfirmed_rooms is False


class TestMigrateFromZoneStore:
    class _FakeZone:
        def __init__(self, x_min, x_max, y_min, y_max, name, confirmed=True, hidden=False):
            self.x_min, self.x_max = x_min, x_max
            self.y_min, self.y_max = y_min, y_max
            self.name = name
            self.confirmed = confirmed
            self.hidden = hidden

    def test_confirmed_zone_overlapping_room_migrates_name(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        # room 1 occupies cell x:[0,5] y:[0,5] -> mm x:[0,900] y:[0,900]
        zone = self._FakeZone(x_min=-50, x_max=950, y_min=-50, y_max=950, name="Kueche")
        migrated = store.migrate_from_zone_store([zone])
        assert migrated == 1
        matched = [r for r in store.rooms.values() if r.name == "Kueche"]
        assert len(matched) == 1
        assert matched[0].confirmed is True

    def test_unconfirmed_zone_is_skipped(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        zone = self._FakeZone(0, 900, 0, 900, "Kueche", confirmed=False)
        migrated = store.migrate_from_zone_store([zone])
        assert migrated == 0
        assert all(r.name == "" for r in store.rooms.values())

    def test_poorly_overlapping_zone_does_not_migrate(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        # Zone bbox only grazes a tiny corner of any room -> below threshold.
        zone = self._FakeZone(x_min=800, x_max=820, y_min=800, y_max=820, name="Nirgendwo")
        migrated = store.migrate_from_zone_store([zone])
        assert migrated == 0

    def test_migration_only_runs_once(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        zone = self._FakeZone(0, 900, 0, 900, "Kueche")
        first = store.migrate_from_zone_store([zone])
        assert first == 1

        # Rename it ourselves afterward, then try to migrate again with a
        # DIFFERENT zone name -- must NOT silently overwrite our edit.
        room_id = next(r.id for r in store.rooms.values() if r.name == "Kueche")
        store.rename_room(room_id, "Buero")
        zone2 = self._FakeZone(0, 900, 0, 900, "Kueche neu")
        second = store.migrate_from_zone_store([zone2])
        assert second == 0
        assert store.rooms[room_id].name == "Buero"

    def test_multiple_zones_match_different_rooms(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        # room 2 occupies cell x:[9,14] y:[0,5] -> mm x:[1350,2250] y:[0,900]
        zone_a = self._FakeZone(0, 900, 0, 900, "Kueche")
        zone_b = self._FakeZone(1350, 2250, 0, 900, "Flur")
        migrated = store.migrate_from_zone_store([zone_a, zone_b])
        assert migrated == 2
        names = {r.name for r in store.rooms.values()}
        assert names == {"Kueche", "Flur"}


class TestSegDoorMedianSmoothing:
    def test_single_observation_sets_cx_cy(self):
        from custom_components.roomba_plus.room_seg_store import SegDoor
        door = SegDoor(id="d1", room_a="r1", room_b="r2", cell=(0, 0), saddle_mm=150)
        door.update_position((10, 4))
        assert door.cx == 10 * 150.0
        assert door.cy == 4 * 150.0
        assert len(door.observations) == 1

    def test_median_resists_a_single_outlier(self):
        """Mirrors DoorMarker.update()'s exact robustness property: a
        door swinging to a different angle for ONE mission must not drag
        the tracked position to that outlier -- the median should stay
        close to the consistent cluster of prior observations."""
        from custom_components.roomba_plus.room_seg_store import SegDoor
        door = SegDoor(id="d1", room_a="r1", room_b="r2", cell=(0, 0), saddle_mm=150)
        for _ in range(5):
            door.update_position((10, 4))  # consistent crossing point
        door.update_position((40, 4))  # one outlier (door wide open this time)
        assert door.cx == 10 * 150.0  # median unaffected by the single outlier
        assert door.cy == 4 * 150.0

    def test_observation_cap_keeps_only_recent(self):
        from custom_components.roomba_plus.room_seg_store import (
            SegDoor, MAX_DOOR_OBSERVATIONS,
        )
        door = SegDoor(id="d1", room_a="r1", room_b="r2", cell=(0, 0), saddle_mm=150)
        for i in range(MAX_DOOR_OBSERVATIONS + 10):
            door.update_position((i, 0))
        assert len(door.observations) == MAX_DOOR_OBSERVATIONS
        assert door.observations[0] == [10 * 150.0, 0.0]  # oldest 10 dropped

    def test_door_position_persists_through_recompute_via_store(self):
        """End-to-end: doors found across multiple recomputes accumulate
        observations rather than resetting each time."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.doors) == 1
        first_obs_count = len(store.doors[0].observations)

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))  # force a recompute
        store.maybe_recompute(cells)

        assert len(store.doors) == 1
        assert len(store.doors[0].observations) >= first_obs_count


class TestUserEdits:
    def test_rename_unknown_room_returns_false(self):
        store = _store()
        assert store.rename_room("nonexistent", "X") is False

    def test_confirm_unknown_room_returns_false(self):
        store = _store()
        assert store.confirm_room("nonexistent") is False

    def test_rename_room_also_confirms(self):
        """Mirrors ZoneStore.rename_zone's exact behaviour."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_id = next(iter(store.rooms))
        assert store.rooms[room_id].confirmed is False
        store.rename_room(room_id, "  Kueche  ")
        assert store.rooms[room_id].name == "Kueche"  # stripped, like rename_zone
        assert store.rooms[room_id].confirmed is True

    def test_hide_and_unhide_room(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_id = next(iter(store.rooms))
        assert store.hide_room(room_id) is True
        assert store.rooms[room_id].hidden is True
        assert store.unhide_room(room_id) is True
        assert store.rooms[room_id].hidden is False

    def test_hide_unknown_room_returns_false(self):
        store = _store()
        assert store.hide_room("nonexistent") is False
        assert store.unhide_room("nonexistent") is False


class TestConfidenceAndBbox:
    def test_confidence_zero_for_new_room(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room = next(iter(store.rooms.values()))
        assert room.confidence == 0.0

    def test_confidence_grows_with_matched_recomputes(self):
        store = _store()
        cells = _two_room_grid()
        store.maybe_recompute(cells)

        for i in range(3):
            cells.update(_rect(50, 60, 50 + i * 10, 56 + i * 10))  # disjoint growth, forces recompute
            store.maybe_recompute(cells)

        # At least one of the two original rooms must have been
        # recognized as "the same room" across these recomputes.
        assert any(r.recompute_count >= 1 for r in store.rooms.values())
        best = max(store.rooms.values(), key=lambda r: r.recompute_count)
        assert best.confidence > 0.0

    def test_bbox_matches_actual_cell_extent(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room = next(iter(store.rooms.values()))
        x_min, x_max, y_min, y_max = room.bbox
        xs = [c[0] for c in room.cells]
        ys = [c[1] for c in room.cells]
        assert x_min == min(xs) * 150.0
        assert y_max == (max(ys) + 1) * 150.0

    def test_bbox_zero_for_empty_room(self):
        room = SegRoom(id="x")
        assert room.bbox == (0.0, 0.0, 0.0, 0.0)


class TestDiagnosticInfo:
    def test_diagnostic_info_shape(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        info = store.diagnostic_info()
        assert info["room_count"] == 2
        assert info["door_count"] == 1
        assert len(info["rooms"]) == 2
        assert all("area_m2" in r for r in info["rooms"])


class TestPersistenceRoundTrip:
    @pytest.mark.asyncio
    async def test_save_then_load_restores_state(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_id = list(store.rooms.keys())[0]
        store.rename_room(room_id, "Flur")
        store.confirm_room(room_id)
        store.migrate_from_zone_store([])  # exercise the one-shot flag too

        saved_payload = {}

        async def fake_save(data):
            saved_payload.update(data)

        with patch("custom_components.roomba_plus.room_seg_store.Store") as MockStore:
            instance = MockStore.return_value
            instance.async_save = fake_save
            instance.async_load = MagicMock()
            await store.async_save(MagicMock(), "entry123")

        loaded = _store()
        with patch("custom_components.roomba_plus.room_seg_store.Store") as MockStore:
            instance = MockStore.return_value

            async def fake_load():
                return saved_payload

            instance.async_load = fake_load
            await loaded.async_load(MagicMock(), "entry123")

        assert set(loaded.rooms.keys()) == set(store.rooms.keys())
        assert loaded.rooms[room_id].name == "Flur"
        assert loaded.rooms[room_id].confirmed is True
        assert loaded.last_cell_count == store.last_cell_count
        assert len(loaded.doors) == len(store.doors)
        assert loaded.migrated_from_zonestore is True

    @pytest.mark.asyncio
    async def test_load_with_no_data_starts_clean(self):
        store = _store()
        with patch("custom_components.roomba_plus.room_seg_store.Store") as MockStore:
            instance = MockStore.return_value

            async def fake_load():
                return None

            instance.async_load = fake_load
            await store.async_load(MagicMock(), "entry123")
        assert store.rooms == {}

    @pytest.mark.asyncio
    async def test_load_with_wrong_version_starts_clean(self):
        store = _store()
        with patch("custom_components.roomba_plus.room_seg_store.Store") as MockStore:
            instance = MockStore.return_value

            async def fake_load():
                return {"version": 999, "rooms": [{"id": "x", "cells": []}]}

            instance.async_load = fake_load
            await store.async_load(MagicMock(), "entry123")
        assert store.rooms == {}


# ── Merged from test_room_segmentation.py (TEST-REORG v3.0.0) ─────────────────
import math
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

    def test_door_saddle_mm_reflects_corridor_width_not_one_cell_floor(self):
        """A door's saddle_mm must scale with the actual corridor it
        connects through. Two otherwise-identical room pairs, one joined
        by a 1-cell-wide corridor and one by a 3-cell-wide corridor,
        must NOT report the same saddle_mm — the old (raw-dist, min-over-
        boundary) implementation reported exactly 150.0mm (1 cell) for
        both, since any boundary touch point at the mask edge wins
        regardless of true corridor width."""
        narrow = _rect_cells(0, 6, 0, 6)
        narrow.update(_rect_cells(9, 15, 0, 6))
        for x in range(6, 9):
            narrow[(x, 3)] = 1.0
        narrow_result = segment_rooms(narrow, min_distance_cells=3.0)
        assert len(narrow_result.doors) == 1

        wide = _rect_cells(0, 6, 0, 6)
        wide.update(_rect_cells(9, 15, 0, 6))
        for x in range(6, 9):
            for y in (2, 3, 4):
                wide[(x, y)] = 1.0
        wide_result = segment_rooms(wide, min_distance_cells=3.0)
        assert len(wide_result.doors) == 1

        narrow_saddle = narrow_result.doors[0]["saddle_mm"]
        wide_saddle = wide_result.doors[0]["saddle_mm"]
        assert wide_saddle > narrow_saddle, (
            f"wide corridor ({wide_saddle}mm) must measure wider than "
            f"narrow corridor ({narrow_saddle}mm)"
        )

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

    def test_door_saddle_mm_does_not_collapse_to_one_cell_floor(self):
        """Field bug (v2.10.0/2.10.1): every door's saddle_mm landed on
        exactly 1 cell * cell_mm (150.0mm) regardless of the actual
        doorway geometry, because the door-finding loop took min() of
        the RAW (unsmoothed) distance transform over the entire shared
        boundary between two rooms — and that boundary almost always
        contains at least one touch point sitting right at the edge of
        the visited mask (dist == 1.0 exactly), unrelated to where the
        real doorway is. merge_regions() already solves the same
        "min over boundary" problem correctly by operating on the
        Gaussian-smoothed distance field instead — this fixture
        (real anonymised grid data from a field archive) is the exact
        shape that triggered the bug: confirmed all 5 doors collapsed to
        precisely 150.0 before the fix.
        """
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

        saddles = [d["saddle_mm"] for d in result.doors]
        assert len(saddles) == 5
        assert len(set(saddles)) > 1, (
            "doors must not all collapse to the same raw-distance floor "
            f"value — got {saddles}"
        )
        # None should sit exactly on the 1-cell floor (150.0mm at the
        # cell_mm=150 default) — that exact value is the symptom, not a
        # coincidence a real, varied set of doorways would produce.
        assert all(s != 150.0 for s in saddles), saddles

