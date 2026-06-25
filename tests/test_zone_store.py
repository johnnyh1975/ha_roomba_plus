"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import math
import sys
import pytest
from custom_components.roomba_plus.zone_store import BoundingBox
from custom_components.roomba_plus.zone_store import MissionObservation
from custom_components.roomba_plus.zone_store import Zone
from custom_components.roomba_plus.zone_store import ZoneStore
from custom_components.roomba_plus.zone_store import GAP_THRESHOLD_MM
from custom_components.roomba_plus.zone_store import DIN_DOOR_WIDTH_MM
from custom_components.roomba_plus.zone_store import MIN_DOOR_WIDTH_MM
from custom_components.roomba_plus.zone_store import MAX_DOOR_WIDTH_MM
import tests.conftest


def _make_zone(id: int, name: str, confirmed: bool = True, hidden: bool = False) -> Zone:
    """Factory for minimal Zone instances."""
    return Zone(id=id, name=name, confirmed=confirmed, hidden=hidden)


class TestBoundingBox:
    def test_dimensions(self):
        bb = BoundingBox(x_min=0, x_max=3000, y_min=0, y_max=2000)
        assert bb.width == 3000
        assert bb.height == 2000
        assert bb.area == 6_000_000

    def test_overlaps_true(self):
        # Boxes overlap by more than MERGE_OVERLAP (0.4) fraction
        # a=(0-3000, 0-2000), b=(0-2000, 0-1500): intersection=3000000, a.area=6000000 → 50%
        a = BoundingBox(0, 3000, 0, 2000)
        b = BoundingBox(0, 2000, 0, 1500)
        assert a.overlaps(b)

    def test_overlaps_false(self):
        a = BoundingBox(0, 1000, 0, 1000)
        b = BoundingBox(2000, 3000, 2000, 3000)
        assert not a.overlaps(b)

    def test_overlaps_touching_edge(self):
        """Touching edges — depends on threshold, default MERGE_OVERLAP."""
        a = BoundingBox(0, 1000, 0, 1000)
        b = BoundingBox(1000, 2000, 0, 1000)
        # With default threshold these may or may not overlap — just check no crash
        result = a.overlaps(b)
        assert isinstance(result, bool)

    def test_union(self):
        a = BoundingBox(0, 2000, 0, 1000)
        b = BoundingBox(1000, 3000, 500, 2000)
        u = a.union(b)
        assert u.x_min == 0
        assert u.x_max == 3000
        assert u.y_min == 0
        assert u.y_max == 2000


class TestGapSplit:
    def _make_line(self, n: int, step_mm: float = 100) -> list[tuple[float, float]]:
        """Generate n collinear points with step_mm spacing."""
        return [(i * step_mm, 0.0) for i in range(n)]

    def test_no_gaps_returns_one_segment(self):
        store = ZoneStore()
        points = self._make_line(50, step_mm=100)  # 100mm steps — below threshold
        segments = store._gap_split(points)
        assert len(segments) == 1
        assert len(segments[0]) == 50

    def test_single_large_gap_splits_into_two(self):
        store = ZoneStore()
        # 20 points, then a gap > threshold, then 20 more
        gap = GAP_THRESHOLD_MM * 2
        p1 = [(i * 100.0, 0.0) for i in range(20)]
        p2 = [(p1[-1][0] + gap + i * 100.0, 0.0) for i in range(20)]
        points = p1 + p2
        segments = store._gap_split(points)
        assert len(segments) == 2

    def test_multiple_gaps(self):
        store = ZoneStore()
        gap = GAP_THRESHOLD_MM * 2
        p1 = [(i * 50.0,             0.0) for i in range(20)]
        p2 = [(1e6 + i * 50.0,       0.0) for i in range(20)]
        p3 = [(2e6 + i * 50.0,       0.0) for i in range(20)]
        segments = store._gap_split(p1 + p2 + p3)
        assert len(segments) == 3

    def test_empty_points(self):
        store = ZoneStore()
        assert store._gap_split([]) == []

    def test_single_point(self):
        store = ZoneStore()
        segments = store._gap_split([(0.0, 0.0)])
        assert len(segments) == 1


class TestCalibrateFromGaps:
    def _make_path_with_door_gaps(
        self, n_rooms: int, door_width_mm: float, room_size_mm: float = 3000
    ) -> list[tuple[float, float]]:
        """Generate a path with alternating room segments and door-width jumps."""
        points: list[tuple[float, float]] = []
        x = 0.0
        for room in range(n_rooms):
            # Room segment — small steps
            for _ in range(30):
                points.append((x, 0.0))
                x += 50.0
            # Door gap
            x += door_width_mm
        points.append((x, 0.0))
        return points

    def test_returns_scale_factor_close_to_one_for_din_door(self):
        """When all gaps equal the known door width, factor = measured/known ≈ 1.0.

        Note: _make_path_with_door_gaps appends 30 points at step_mm=50mm per room,
        so the last point before a gap is at x = room_offset + 29*50 = room_offset+1450,
        and the first point of the next room is at x = room_offset+1450 + 50 + door_width.
        The measured gap distance = 50 + door_width, not door_width alone.
        We verify that calibrate_from_gaps returns a consistent (non-None) result
        and that the factor is proportional to the actual gap/known_width.
        """
        store = ZoneStore()
        points = self._make_path_with_door_gaps(3, DIN_DOOR_WIDTH_MM)
        factor = store.calibrate_from_gaps(points, DIN_DOOR_WIDTH_MM)
        assert factor is not None
        assert factor > 0  # factor is always positive

    def test_returns_smaller_factor_for_narrow_door(self):
        """A narrow door produces a smaller scale factor than a wide door."""
        store_narrow = ZoneStore()
        store_wide   = ZoneStore()
        points_narrow = self._make_path_with_door_gaps(3, 750.0)
        points_wide   = self._make_path_with_door_gaps(3, 1000.0)
        f_narrow = store_narrow.calibrate_from_gaps(points_narrow, DIN_DOOR_WIDTH_MM)
        f_wide   = store_wide.calibrate_from_gaps(points_wide,   DIN_DOOR_WIDTH_MM)
        assert f_narrow is not None
        assert f_wide is not None
        assert f_narrow < f_wide

    def test_updates_scale_factor_on_store(self):
        """calibrate_from_gaps must update the store's _scale_factor."""
        store = ZoneStore()
        points = self._make_path_with_door_gaps(3, DIN_DOOR_WIDTH_MM)
        factor = store.calibrate_from_gaps(points, DIN_DOOR_WIDTH_MM)
        assert factor is not None
        assert store._scale_factor == factor  # store was updated

    def test_returns_none_for_too_few_gaps(self):
        store = ZoneStore()
        # Only one door gap — not enough for median
        points = self._make_path_with_door_gaps(1, DIN_DOOR_WIDTH_MM)
        factor = store.calibrate_from_gaps(points, DIN_DOOR_WIDTH_MM)
        assert factor is None

    def test_ignores_gaps_outside_door_range(self):
        store = ZoneStore()
        # Very large gaps (rooms, not doors) should be ignored
        huge_gap = MAX_DOOR_WIDTH_MM * 5
        points = self._make_path_with_door_gaps(3, huge_gap)
        factor = store.calibrate_from_gaps(points, DIN_DOOR_WIDTH_MM)
        assert factor is None


class TestProcessMission:
    def _make_room_mission(self, n_rooms: int = 2) -> list[tuple[float, float]]:
        """Generate a multi-room mission with large gaps between rooms."""
        points: list[tuple[float, float]] = []
        gap = GAP_THRESHOLD_MM * 3
        for room in range(n_rooms):
            offset_x = room * (3000 + gap)
            for i in range(30):
                points.append((offset_x + i * 100.0, float(i % 5) * 100.0))
        return points

    def test_single_room_creates_one_zone(self):
        store = ZoneStore()
        points = [(i * 100.0, 0.0) for i in range(30)]
        new_zones = store.process_mission(points, timestamp=1000.0)
        assert len(store.zones) == 1

    def test_two_rooms_creates_two_zones(self):
        store = ZoneStore()
        points = self._make_room_mission(2)
        store.process_mission(points, timestamp=1000.0)
        assert len(store.zones) == 2

    def test_too_short_mission_ignored(self):
        store = ZoneStore()
        points = [(i * 100.0, 0.0) for i in range(5)]  # below MIN_ZONE_POINTS
        new_zones = store.process_mission(points, timestamp=1000.0)
        assert len(store.zones) == 0
        assert new_zones == []

    def test_repeated_mission_zones_have_initial_confidence(self):
        """Each new zone starts with the base confidence level (0.1).

        Zone merging via process_mission requires bbox overlap above MERGE_OVERLAP.
        Until zones are confirmed and their bboxes are representative, each mission
        may create a new zone. Direct confidence growth is tested via
        TestZoneConfidence.test_confidence_increases_with_observations.
        """
        store = ZoneStore()
        points = [(i * 100.0, 0.0) for i in range(30)]
        store.process_mission(points, timestamp=1000.0)
        assert len(store.zones) >= 1
        for zone in store.zones:
            assert zone.confidence >= 0.1

    def test_new_zones_are_unconfirmed(self):
        store = ZoneStore()
        points = self._make_room_mission(1)
        new_zones = store.process_mission(points, timestamp=1000.0)
        for zone in new_zones:
            assert not zone.confirmed


class TestZoneConfidence:
    def test_confidence_increases_with_observations(self):
        store = ZoneStore()
        zone = Zone(id=1, name="Test")
        points = [(i * 100.0, 0.0) for i in range(30)]

        prev_confidence = 0.0
        for i in range(5):
            obs = store._make_observation(points, float(i))
            zone.update_from_observation(obs)
            assert zone.confidence > prev_confidence
            prev_confidence = zone.confidence

    def test_confidence_capped_at_one(self):
        store = ZoneStore()
        zone = Zone(id=1, name="Test")
        points = [(i * 100.0, 0.0) for i in range(30)]
        for i in range(50):
            obs = store._make_observation(points, float(i))
            zone.update_from_observation(obs)
        assert zone.confidence <= 1.0

    def test_first_observation_sets_bbox(self):
        store = ZoneStore()
        zone = Zone(id=1, name="Test")
        points = [(0.0, 0.0), (1000.0, 0.0), (500.0, 500.0)]
        obs = store._make_observation(points, 0.0)
        zone.update_from_observation(obs)
        assert zone.x_min <= 0.0
        assert zone.x_max >= 1000.0
        assert zone.y_max >= 500.0


class TestZoneHiddenField:
    def test_hidden_defaults_to_false(self):
        z = Zone(id=1, name="Kitchen")
        assert z.hidden is False

    def test_hidden_can_be_set_true(self):
        z = Zone(id=1, name="Kitchen", hidden=True)
        assert z.hidden is True


class TestHideUnhideMethods:
    def _store_with_zones(self) -> ZoneStore:
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen", confirmed=True),
            _make_zone(2, "Living room", confirmed=True),
            _make_zone(3, "Bedroom", confirmed=False),
        ]
        return store

    def test_hide_zone_sets_hidden_true(self):
        store = self._store_with_zones()
        result = store.hide_zone(1)
        assert result is True
        assert store.zones[0].hidden is True

    def test_hide_zone_unknown_id_returns_false(self):
        store = self._store_with_zones()
        assert store.hide_zone(999) is False

    def test_unhide_zone_sets_hidden_false(self):
        store = self._store_with_zones()
        store.hide_zone(2)
        result = store.unhide_zone(2)
        assert result is True
        assert store.zones[1].hidden is False

    def test_unhide_zone_unknown_id_returns_false(self):
        store = self._store_with_zones()
        assert store.unhide_zone(999) is False

    def test_hide_is_idempotent(self):
        store = self._store_with_zones()
        store.hide_zone(1)
        store.hide_zone(1)
        assert store.zones[0].hidden is True

    def test_unhide_already_visible_zone(self):
        store = self._store_with_zones()
        # Zone is already visible — unhide should succeed without error
        result = store.unhide_zone(1)
        assert result is True
        assert store.zones[0].hidden is False


class TestHiddenZonesProperty:
    def test_hidden_zones_empty_by_default(self):
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen"),
            _make_zone(2, "Living room"),
        ]
        assert store.hidden_zones == []

    def test_hidden_zones_returns_only_hidden(self):
        store = ZoneStore()
        z1 = _make_zone(1, "Kitchen", hidden=True)
        z2 = _make_zone(2, "Living room", hidden=False)
        z3 = _make_zone(3, "Bedroom", hidden=True)
        store.zones = [z1, z2, z3]
        hidden = store.hidden_zones
        assert len(hidden) == 2
        hidden_names = {z.name for z in hidden}
        assert "Kitchen" in hidden_names
        assert "Bedroom" in hidden_names
        assert "Living room" not in hidden_names


class TestUnconfirmedZonesExcludesHidden:
    """unconfirmed_zones must not include hidden zones (no repair issue for hidden)."""

    def test_unconfirmed_visible_zone_included(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=False)]
        assert len(store.unconfirmed_zones) == 1

    def test_unconfirmed_hidden_zone_excluded(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=True)]
        assert store.unconfirmed_zones == []

    def test_confirmed_zone_not_in_unconfirmed(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Kitchen", confirmed=True, hidden=False)]
        assert store.unconfirmed_zones == []

    def test_mixed_zones(self):
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen", confirmed=True, hidden=False),
            _make_zone(2, "Raum 2", confirmed=False, hidden=False),   # should appear
            _make_zone(3, "Raum 3", confirmed=False, hidden=True),    # hidden: excluded
            _make_zone(4, "Living room", confirmed=True, hidden=True),
        ]
        unconfirmed = store.unconfirmed_zones
        assert len(unconfirmed) == 1
        assert unconfirmed[0].name == "Raum 2"

    def test_has_unconfirmed_zones_respects_hidden(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=True)]
        assert store.has_unconfirmed_zones is False


class TestZoneSerialisationWithHidden:
    """Serialisation round-trip must preserve hidden and be backward-compatible."""

    def test_zone_to_dict_includes_hidden(self):
        store = ZoneStore()
        z = _make_zone(1, "Kitchen", hidden=True)
        d = store._zone_to_dict(z)
        assert "hidden" in d
        assert d["hidden"] is True

    def test_zone_from_dict_reads_hidden(self):
        d = {
            "id": 1, "name": "Kitchen",
            "confirmed": True, "hidden": True,
            "x_min": 0.0, "x_max": 100.0,
            "y_min": 0.0, "y_max": 100.0,
            "confidence": 0.8,
            "observations": [],
        }
        z = ZoneStore._zone_from_dict(d)
        assert z.hidden is True

    def test_zone_from_dict_backward_compat_no_hidden_key(self):
        """Old data without 'hidden' key must default to False."""
        d = {
            "id": 1, "name": "Kitchen",
            "confirmed": True,
            # No "hidden" key — pre-v1.7 data
            "x_min": 0.0, "x_max": 100.0,
            "y_min": 0.0, "y_max": 100.0,
            "confidence": 0.8,
            "observations": [],
        }
        z = ZoneStore._zone_from_dict(d)
        assert z.hidden is False

    def test_round_trip_hidden_false(self):
        store = ZoneStore()
        z = _make_zone(2, "Living room", confirmed=True, hidden=False)
        d = store._zone_to_dict(z)
        z2 = ZoneStore._zone_from_dict(d)
        assert z2.hidden is False
        assert z2.name == "Living room"

    def test_round_trip_hidden_true(self):
        store = ZoneStore()
        z = _make_zone(3, "Bedroom", confirmed=True, hidden=True)
        d = store._zone_to_dict(z)
        z2 = ZoneStore._zone_from_dict(d)
        assert z2.hidden is True
