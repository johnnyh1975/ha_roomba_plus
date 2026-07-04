"""Tests for RoomSegStore — identity-stable room/door matching across
recomputations, recompute gating, and persistence round-trip."""
from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus.room_seg_store import (
    RoomSegStore,
    SegRoom,
    SegDoor,
    STALE_ABSORPTION_RATIO,
    DOOR_MERGE_DISTANCE_MM,
)


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


class TestStaleRoomAbsorptionCleanup:
    """v3.2.1 — a persisted room whose entire (or near-entire) former
    territory is now claimed by a DIFFERENT, currently-matched room must
    be deleted, not kept forever like an ordinary "not detected this
    round" miss. Field-confirmed: a 122-cell phantom room 100% contained
    inside a 521-cell live room, complete with 3 doors pointing into the
    interior of its absorber.
    """

    def test_fully_absorbed_room_is_deleted(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        phantom_id = room_ids[0]

        # Manually seed a phantom room whose cells are a PURE SUBSET of
        # the other persisted room's cells — simulates the field scenario
        # where an earlier partial-grid recompute created a small sliver
        # region that a later, more-complete recompute's cluster fully
        # subsumes.
        live_id = room_ids[1]
        subset_cells = set(list(store.rooms[live_id].cells)[:5])
        store.rooms["room_phantom"] = SegRoom(id="room_phantom", cells=subset_cells)

        # Recompute against a grid that reproduces the SAME two clusters
        # (phantom's cells are invisible to segment_rooms — they were
        # never actually part of the input grid — so segment_rooms will
        # naturally re-match live_id's full cluster onto both live_id AND
        # the phantom's now-subset cells, absorbing the phantom).
        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))  # forces the growth gate
        store.last_cell_count = 0
        store._recompute(cells)

        assert "room_phantom" not in store.rooms

    def test_room_absorbed_across_multiple_rooms_is_still_deleted(self):
        """v3.2.1 FIELD FIX — a room whose cells are split across SEVERAL
        matched rooms, none individually reaching STALE_ABSORPTION_RATIO,
        must still be deleted if the UNION reaches it. Field-confirmed
        post-3.2.1: a real recompute produced a room 100% claimed
        elsewhere but split 72.7% / 10.0% / 17.2% across three different
        matched rooms — the original single-absorber `any(...)` check
        missed this because no single absorber crossed 80%, even though
        the room was, in aggregate, exactly as stale as a single-absorber
        case would be.
        """
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        live_a, live_b = room_ids[0], room_ids[1]

        # Split a phantom room's cells across BOTH live rooms, disjoint
        # halves, neither alone reaching 80% but the union reaching 100%.
        cells_a = set(list(store.rooms[live_a].cells)[:4])
        cells_b = set(list(store.rooms[live_b].cells)[:4])
        store.rooms["room_split_phantom"] = SegRoom(
            id="room_split_phantom", cells=cells_a | cells_b
        )

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))
        store.last_cell_count = 0
        store._recompute(cells)

        assert "room_split_phantom" not in store.rooms, (
            "union of overlaps across multiple absorbers must count "
            "toward STALE_ABSORPTION_RATIO, not just the single largest one"
        )

    def test_union_avoids_double_counting_if_absorbers_overlap_each_other(self):
        """Defensive: even if two matched rooms' cell sets were somehow
        not perfectly disjoint (shouldn't happen post-recompute, but the
        union-based fix must not double-count a shared cell as if it
        were 200% absorbed)."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        live_a, live_b = room_ids[0], room_ids[1]

        # Force an artificial overlap between the two "matched" rooms
        # themselves, then check a phantom whose 4 cells are the exact
        # same 4 cells shared by both.
        shared = set(list(store.rooms[live_a].cells)[:4])
        store.rooms[live_b].cells |= shared  # artificial overlap
        store.rooms["room_tiny_phantom"] = SegRoom(id="room_tiny_phantom", cells=set(shared))

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))
        store.last_cell_count = 0
        store._recompute(cells)

        # union(shared, shared) == shared == 100% of room_tiny_phantom's
        # cells -> still correctly deleted, not miscounted as >100%.
        assert "room_tiny_phantom" not in store.rooms


        """Below STALE_ABSORPTION_RATIO — must NOT be deleted, preserving
        the existing decay-tolerant behaviour for genuine boundary
        wobble (only a small fringe shared, not near-total absorption)."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        live_id = room_ids[1]

        # Only ~20% of this room's cells overlap the live room — well
        # below the 0.8 threshold.
        live_cells = list(store.rooms[live_id].cells)
        overlap_cells = set(live_cells[:2])
        disjoint_cells = {(200 + i, 200) for i in range(8)}
        store.rooms["room_mostly_elsewhere"] = SegRoom(
            id="room_mostly_elsewhere", cells=overlap_cells | disjoint_cells
        )

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))
        store.last_cell_count = 0
        store._recompute(cells)

        assert "room_mostly_elsewhere" in store.rooms

    def test_absorbed_rooms_doors_are_dropped(self):
        """A door referencing a room deleted by stale-absorption cleanup
        must be dropped in the SAME recompute — no separate cascade step,
        it rides the existing "both rooms still exist" door-preservation
        guard in _match_doors."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = list(store.rooms.keys())
        live_id = room_ids[1]
        subset_cells = set(list(store.rooms[live_id].cells)[:5])
        store.rooms["room_phantom"] = SegRoom(id="room_phantom", cells=subset_cells)
        store.doors.append(SegDoor(
            id="door_phantom", room_a="room_phantom", room_b=room_ids[0],
            cell=(0, 0), saddle_mm=150.0, cx=0.0, cy=0.0,
        ))

        cells = _two_room_grid()
        cells.update(_rect(30, 40, 30, 40))
        store.last_cell_count = 0
        store._recompute(cells)

        assert "room_phantom" not in store.rooms
        assert not any(
            "room_phantom" in (d.room_a, d.room_b) for d in store.doors
        ), "door referencing the deleted phantom room must be dropped"

    def test_stale_absorption_ratio_is_point_eight(self):
        assert STALE_ABSORPTION_RATIO == 0.8


class TestDoorDistanceGate:
    """v3.2.1 — two door observations for the same room-pair that are
    genuinely far apart (open-plan layout, two real openings) must
    produce TWO SegDoors, not one wrongly-averaged position. Field-
    confirmed: two observations 2348mm apart for one pair, 1956mm for
    another, both collapsing into a single door median before this fix.
    """

    def test_close_observations_merge_into_one_door(self):
        """Two crossings for the same pair well within
        DOOR_MERGE_DISTANCE_MM must stay a single door (existing
        median-smoothing behaviour, unaffected by the gate)."""
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store.doors) == 1
        door_id = store.doors[0].id

        # Corridor shifts by 1 cell (150mm) — well under the 800mm gate.
        cells = _rect(0, 6, 0, 6)
        cells.update(_rect(9, 15, 0, 6))
        for x in range(6, 9):
            cells[(x, 2)] = 1.0
        cells.update(_rect(30, 40, 30, 40))
        store.maybe_recompute(cells)

        assert len(store.doors) == 1
        assert store.doors[0].id == door_id

    def test_far_observation_creates_second_door_for_same_pair(self):
        """A new crossing detected far from the existing door of that
        pair must produce an ADDITIONAL door, not overwrite the first.

        Drives _match_doors() directly with a synthetic segmentation
        result rather than through the full watershed pipeline — a real
        two-corridor floor plan large enough to reliably avoid spurious
        extra watershed seeds would make this test fragile to unrelated
        tuning changes in room_segmentation.py; the distance-gate logic
        itself only needs label_to_id + result.doors as documented.
        """
        store = RoomSegStore(min_distance_cells=3.0)
        store.rooms["room_1"] = SegRoom(id="room_1", cells={(0, 0)})
        store.rooms["room_2"] = SegRoom(id="room_2", cells={(1, 0)})

        class _FirstCrossing:
            rooms: dict = {}
            doors = [{"a": 10, "b": 20, "cell": (0, 2), "saddle_mm": 100.0}]

        store._match_doors(_FirstCrossing(), {10: "room_1", 20: "room_2"})
        assert len(store.doors) == 1
        first_door = store.doors[0]

        class _SecondCrossingFarAway:
            rooms: dict = {}
            doors = [{"a": 10, "b": 20, "cell": (0, 30), "saddle_mm": 90.0}]

        # y=30 vs y=2 at CELL_MM=150 → 4200mm apart, well past the 800mm gate.
        store._match_doors(_SecondCrossingFarAway(), {10: "room_1", 20: "room_2"})

        assert len(store.doors) == 2
        assert any(d.id == first_door.id for d in store.doors), (
            "original door must be preserved, not replaced"
        )
        pairs = {tuple(sorted((d.room_a, d.room_b))) for d in store.doors}
        assert len(pairs) == 1, "both doors must connect the SAME room pair"

    def test_closest_existing_door_wins_when_multiple_present(self):
        """With two existing doors for a pair, a new detection must merge
        into whichever one it's actually closest to, not always the
        first in insertion order."""
        store = RoomSegStore(min_distance_cells=3.0)
        store.rooms["room_1"] = SegRoom(id="room_1", cells={(0, 0)})
        store.rooms["room_2"] = SegRoom(id="room_2", cells={(1, 0)})
        near_door = SegDoor(
            id="door_near", room_a="room_1", room_b="room_2",
            cell=(0, 0), saddle_mm=100.0, cx=0.0, cy=0.0,
            observations=[[0.0, 0.0]],
        )
        far_door = SegDoor(
            id="door_far", room_a="room_1", room_b="room_2",
            cell=(0, 0), saddle_mm=100.0, cx=5000.0, cy=5000.0,
            observations=[[5000.0, 5000.0]],
        )
        store.doors = [near_door, far_door]

        class _FakeResult:
            rooms = {}
            doors = [{
                "a": 10, "b": 20, "cell": (1, 1),  # 150,150mm — near near_door
                "saddle_mm": 90.0,
            }]

        label_to_id = {10: "room_1", 20: "room_2"}
        store._match_doors(_FakeResult(), label_to_id)

        assert len(store.doors) == 2
        updated_near = next(d for d in store.doors if d.id == "door_near")
        untouched_far = next(d for d in store.doors if d.id == "door_far")
        assert updated_near.cell == (1, 1)
        assert untouched_far.cx == 5000.0, "far door must not have been touched"

    def test_door_merge_distance_is_800mm(self):
        assert DOOR_MERGE_DISTANCE_MM == 800.0


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



@pytest.mark.skipif(
    not any(
        __import__("pathlib").Path(p).exists()
        for p in [
            "tests/fixtures/roomba_plus_grid_01KRRVYR4T1MPSYM7ACKA5XCBX.dms",
            "/mnt/user-data/uploads/roomba_plus_grid_01KRRVYR4T1MPSYM7ACKA5XCBX.dms",
        ]
    ),
    reason="Real-data fixture (.dms) not available in this environment",
)
class TestStaleRoomAndDoorFixesAgainstRealField:
    """Real-data regression (field-store bug-hunt, 2026-07-02): a real 980
    OG's persisted GridStore, frozen as a fixture, reproduced BOTH v3.2.1
    RoomSeg bugs at once — a 122-cell phantom room_7 100% absorbed into
    room_5, three doors pointing into it, and two door-pairs whose two
    observations were 2348mm / 1956mm apart yet averaged into one
    position. This test drives the real pipeline against that exact grid
    and asserts the pathology is gone, not just the synthetic unit cases
    above.

    Frozen snapshot in tests/fixtures/ takes priority over the
    /mnt/user-data/uploads fallback, same pattern as
    TestMissionClassificationAgainstRealRecords in test_callbacks.py.
    """
    import json as _json
    from pathlib import Path as _Path

    _GRID_PATHS = [
        _Path("tests/fixtures/roomba_plus_grid_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
        _Path("/mnt/user-data/uploads/roomba_plus_grid_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
    ]
    _GRID_FILE = next((p for p in _GRID_PATHS if p.exists()), None)
    _GRID_DATA = _json.load(open(_GRID_FILE)) if _GRID_FILE else {"data": {"cells": {}}}
    _CELLS = {
        tuple(int(n) for n in k.split(",")): v
        for k, v in _GRID_DATA.get("data", _GRID_DATA).get("cells", {}).items()
    }

    _ROOMSEG_PATHS = [
        _Path("tests/fixtures/roomba_plus_roomseg_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
        _Path("/mnt/user-data/uploads/roomba_plus_roomseg_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
    ]
    _ROOMSEG_FILE = next((p for p in _ROOMSEG_PATHS if p.exists()), None)
    _ROOMSEG_DATA = (
        _json.load(open(_ROOMSEG_FILE)) if _ROOMSEG_FILE else {"data": {"rooms": [], "doors": []}}
    ).get("data", {})

    def _seeded_store(self):
        """Reconstruct a RoomSegStore exactly as it was persisted in the
        field — phantom room_7, duplicate-observation doors and all —
        so the test exercises the CLEANUP path, not a from-scratch
        recompute (which was already shown never to produce room_7 in
        the first place)."""
        store = RoomSegStore()
        for r in self._ROOMSEG_DATA.get("rooms", []):
            store.rooms[r["id"]] = SegRoom(
                id=r["id"], cells=set(tuple(c) for c in r["cells"]),
                name=r.get("name", ""), confirmed=r.get("confirmed", False),
            )
        for d in self._ROOMSEG_DATA.get("doors", []):
            store.doors.append(SegDoor(
                id=d["id"], room_a=d["room_a"], room_b=d["room_b"],
                cell=tuple(d["cell"]), saddle_mm=d["saddle_mm"],
                cx=d["cx"], cy=d["cy"], observations=d["observations"],
            ))
        room_ns = [int(r["id"].split("_")[1]) for r in self._ROOMSEG_DATA.get("rooms", [])]
        door_ns = [int(d["id"].split("_")[1]) for d in self._ROOMSEG_DATA.get("doors", [])]
        store._next_room_n = max(room_ns, default=0) + 1
        store._next_door_n = max(door_ns, default=0) + 1
        store.last_cell_count = 0  # force a recompute
        return store

    def test_field_fixture_has_the_known_phantom_room(self):
        """Sanity check on the fixture itself — if this fails, the
        fixture no longer reproduces the bug this test guards against."""
        assert any(r["id"] == "room_7" for r in self._ROOMSEG_DATA.get("rooms", []))

    def test_phantom_room_removed_after_recompute(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        assert "room_7" not in store.rooms

    def test_no_overlapping_cells_after_recompute(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        seen: dict[tuple[int, int], str] = {}
        overlaps = 0
        for rid, room in store.rooms.items():
            for c in room.cells:
                if c in seen:
                    overlaps += 1
                seen[c] = rid
        assert overlaps == 0

    def test_no_door_references_deleted_phantom_room(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        assert not any(
            "room_7" in (d.room_a, d.room_b) for d in store.doors
        )

    def test_far_apart_door_pairs_split_into_two_doors(self):
        """The two field-confirmed suspect pairs (room_2/room_5 at
        2348mm apart, room_3/room_5 at 1956mm) must each end up as TWO
        distinct doors after the fix, not one averaged position."""
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        pair_counts: dict[tuple[str, str], int] = {}
        for d in store.doors:
            key = tuple(sorted((d.room_a, d.room_b)))
            pair_counts[key] = pair_counts.get(key, 0) + 1
        multi_door_pairs = {k: v for k, v in pair_counts.items() if v > 1}
        assert multi_door_pairs, (
            "expected at least one room-pair to split into multiple doors "
            f"on the real field grid; got counts {pair_counts}"
        )


class TestMultiAbsorberStaleCleanupAgainstRealField:
    """Real-data regression (field-store bug-hunt, 2026-07-03, SECOND
    snapshot — one mission after 3.2.1 shipped): the single-absorber
    stale-cleanup fix from the first snapshot had a genuine gap. A real
    post-3.2.1 recompute produced `room_6`, 100% claimed elsewhere but
    split 72.7% / 10.0% / 17.2% across THREE matched rooms — no single
    absorber crossed STALE_ABSORPTION_RATIO (0.8), so the original
    `any(...)`-based check left it stranded (209 overlapping cells
    total). Drives the real pipeline against this second snapshot and
    asserts the union-based fix catches it.

    Frozen snapshot in tests/fixtures/, same priority pattern as
    TestStaleRoomAndDoorFixesAgainstRealField above.
    """
    import json as _json
    from pathlib import Path as _Path

    _GRID_FILE = _Path("tests/fixtures/roomba_plus_grid_02_01KRRVYR4T1MPSYM7ACKA5XCBX.dms")
    _GRID_DATA = _json.load(open(_GRID_FILE)) if _GRID_FILE.exists() else {"data": {"cells": {}}}
    _CELLS = {
        tuple(int(n) for n in k.split(",")): v
        for k, v in _GRID_DATA.get("data", _GRID_DATA).get("cells", {}).items()
    }

    _ROOMSEG_FILE = _Path("tests/fixtures/roomba_plus_roomseg_02_01KRRVYR4T1MPSYM7ACKA5XCBX.dms")
    _ROOMSEG_DATA = (
        _json.load(open(_ROOMSEG_FILE)) if _ROOMSEG_FILE.exists() else {"data": {"rooms": [], "doors": []}}
    ).get("data", {})

    def _seeded_store(self):
        store = RoomSegStore()
        for r in self._ROOMSEG_DATA.get("rooms", []):
            store.rooms[r["id"]] = SegRoom(
                id=r["id"], cells=set(tuple(c) for c in r["cells"]),
                name=r.get("name", ""), confirmed=r.get("confirmed", False),
            )
        for d in self._ROOMSEG_DATA.get("doors", []):
            store.doors.append(SegDoor(
                id=d["id"], room_a=d["room_a"], room_b=d["room_b"],
                cell=tuple(d["cell"]), saddle_mm=d["saddle_mm"],
                cx=d["cx"], cy=d["cy"], observations=d["observations"],
            ))
        room_ns = [int(r["id"].split("_")[1]) for r in self._ROOMSEG_DATA.get("rooms", [])]
        door_ns = [int(d["id"].split("_")[1]) for d in self._ROOMSEG_DATA.get("doors", [])]
        store._next_room_n = max(room_ns, default=0) + 1
        store._next_door_n = max(door_ns, default=0) + 1
        store.last_cell_count = 0
        return store

    def test_field_fixture_has_the_known_multi_absorbed_room(self):
        """Sanity check on the fixture itself."""
        assert any(r["id"] == "room_6" for r in self._ROOMSEG_DATA.get("rooms", []))

    def test_multi_absorbed_room_removed_after_recompute(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        assert "room_6" not in store.rooms

    def test_no_overlapping_cells_after_recompute(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        seen: dict[tuple[int, int], str] = {}
        overlaps = 0
        for rid, room in store.rooms.items():
            for c in room.cells:
                if c in seen:
                    overlaps += 1
                seen[c] = rid
        assert overlaps == 0, f"expected 0 overlapping cells, found {overlaps}"

    def test_no_door_references_deleted_room(self):
        store = self._seeded_store()
        store.maybe_recompute(self._CELLS)
        assert not any(
            "room_6" in (d.room_a, d.room_b) for d in store.doors
        )


class TestBoundaryHistory:
    """v3.2.1 BOUNDARY-HISTORY — rolling room-pair adjacency window.
    Simulated end-to-end testing (BFS-growth approximation against real
    field data) showed no measurable effect over the 3.2.1 baseline —
    kept as scaffolding for re-evaluation once genuine multi-week
    recompute history exists, not because a benefit is expected today.
    """

    def test_initial_state_empty(self):
        store = _store()
        assert list(store._boundary_history) == []

    def test_recompute_appends_one_entry(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        assert len(store._boundary_history) == 1

    def test_entry_contains_the_current_door_pair(self):
        store = _store()
        store.maybe_recompute(_two_room_grid())
        room_ids = sorted(store.rooms.keys())
        entry = store._boundary_history[-1]
        assert [list(sorted(room_ids))] == entry or entry == [sorted(room_ids)]

    def test_window_caps_at_max_length(self):
        from custom_components.roomba_plus.room_seg_store import MAX_BOUNDARY_HISTORY
        store = _store()
        cells = _two_room_grid()
        for i in range(MAX_BOUNDARY_HISTORY + 5):
            cells.update(_rect(30 + i, 33 + i, 30, 33))  # forces growth gate each time
            store.last_cell_count = 0
            store._recompute(cells)
        assert len(store._boundary_history) == MAX_BOUNDARY_HISTORY

    def test_stability_is_1_when_history_empty(self):
        store = _store()
        assert store._boundary_stability("room_1", "room_2") == 1.0

    def test_stability_reflects_hit_fraction(self):
        store = _store()
        store._boundary_history.append([["room_1", "room_2"]])
        store._boundary_history.append([["room_1", "room_2"]])
        store._boundary_history.append([["room_3", "room_4"]])
        assert store._boundary_stability("room_1", "room_2") == pytest.approx(2 / 3)
        assert store._boundary_stability("room_5", "room_6") == 0.0

    @pytest.mark.asyncio
    async def test_persists_across_save_load_roundtrip(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        store = _store()
        store.maybe_recompute(_two_room_grid())
        saved = {}
        async def fake_save(data):
            saved.update(data)
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        with patch("custom_components.roomba_plus.room_seg_store.Store", return_value=store_mock):
            await store.async_save(MagicMock(), "e1")
        assert "boundary_history" in saved
        assert len(saved["boundary_history"]) == 1

        store2 = _store()
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch("custom_components.roomba_plus.room_seg_store.Store", return_value=store_mock2):
            await store2.async_load(MagicMock(), "e1")
        assert list(store2._boundary_history) == list(store._boundary_history)

    @pytest.mark.asyncio
    async def test_old_payload_without_boundary_history_loads_cleanly(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.room_seg_store import PAYLOAD_VERSION
        store = _store()
        old_payload = {"version": PAYLOAD_VERSION, "rooms": [], "doors": []}
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=old_payload)
        with patch("custom_components.roomba_plus.room_seg_store.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert list(store._boundary_history) == []
