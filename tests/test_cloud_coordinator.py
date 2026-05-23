"""Unit tests for cloud_coordinator.py.

Tests:
  - IrobotCloudCoordinator.active_pmap_id  — extracts pmap_id from nested data
  - IrobotCloudCoordinator.regions         — flattens regions across pmaps
  - IrobotCloudCoordinator.zones           — flattens zones across pmaps
  - has_cloud property on RoombaData       — True only when data present

No HA or roombapy installation required — uses the stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import types
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.models import RoombaData, MapCapability


# ── Shared pmap fixture ────────────────────────────────────────────────────────

def _make_pmap(pmap_id: str, regions: list[dict], zones: list[dict] | None = None) -> dict:
    return {
        "active_pmapv_details": {
            "active_pmapv": {"pmap_id": pmap_id},
            "map_header": {"id": pmap_id, "name": f"Map {pmap_id}"},
            "regions": regions,
            "zones": zones or [],
        },
        "pmap_id": pmap_id,
        "active_pmapv_id": "v1",
    }


def _make_data(pmaps=None, favorites=None, history=None) -> dict:
    return {
        "pmaps": pmaps or [],
        "favorites": favorites or [],
        "mission_history": history or {},
    }


# ── Helpers that use coordinator.data directly ─────────────────────────────────
# We test the property logic by constructing a bare coordinator object
# (bypassing HA) and injecting .data manually.

def _bare_coordinator() -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA setup."""
    cc = object.__new__(IrobotCloudCoordinator)
    cc.data = None
    cc.blid = "testblid"
    return cc


class TestActivePmapId:
    def test_none_when_no_data(self):
        cc = _bare_coordinator()
        assert cc.active_pmap_id is None

    def test_none_when_empty_pmaps(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[])
        assert cc.active_pmap_id is None

    def test_returns_first_pmap_id(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[_make_pmap("abc123", [])])
        assert cc.active_pmap_id == "abc123"

    def test_returns_first_even_with_multiple(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("first_map", []),
            _make_pmap("second_map", []),
        ])
        assert cc.active_pmap_id == "first_map"

    def test_none_when_pmap_id_missing_in_details(self):
        cc = _bare_coordinator()
        cc.data = {"pmaps": [{"active_pmapv_details": {"active_pmapv": {}}}]}
        assert cc.active_pmap_id is None


class TestRegions:
    def test_empty_when_no_data(self):
        cc = _bare_coordinator()
        assert cc.regions == []

    def test_empty_when_no_pmaps(self):
        cc = _bare_coordinator()
        cc.data = _make_data()
        assert cc.regions == []

    def test_single_region(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("map1", [{"id": "3", "name": "Kitchen", "region_type": "kitchen"}])
        ])
        regions = cc.regions
        assert len(regions) == 1
        assert regions[0]["id"] == "3"
        assert regions[0]["name"] == "Kitchen"
        assert regions[0]["pmap_id"] == "map1"
        assert regions[0]["region_type"] == "kitchen"

    def test_regions_from_multiple_pmaps_flattened(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("map1", [{"id": "1", "name": "Hall", "region_type": "hallway"}]),
            _make_pmap("map2", [{"id": "2", "name": "Bed", "region_type": "bedroom"}]),
        ])
        regions = cc.regions
        assert len(regions) == 2
        assert {r["pmap_id"] for r in regions} == {"map1", "map2"}

    def test_region_default_type_when_missing(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("m1", [{"id": "5", "name": "Lounge"}])  # no region_type
        ])
        assert cc.regions[0]["region_type"] == "default"

    def test_multiple_regions_same_pmap(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("map1", [
                {"id": "1", "name": "A", "region_type": "kitchen"},
                {"id": "2", "name": "B", "region_type": "bedroom"},
                {"id": "3", "name": "C", "region_type": "bathroom"},
            ])
        ])
        assert len(cc.regions) == 3
        assert all(r["pmap_id"] == "map1" for r in cc.regions)


class TestZones:
    def test_empty_when_no_data(self):
        cc = _bare_coordinator()
        assert cc.zones == []

    def test_empty_when_no_zones_in_pmap(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[_make_pmap("m1", [])])
        assert cc.zones == []

    def test_single_zone(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("m1", [], zones=[{"id": "z1", "name": "Sofa area", "zone_type": "furniture"}])
        ])
        zones = cc.zones
        assert len(zones) == 1
        assert zones[0]["id"] == "z1"
        assert zones[0]["name"] == "Sofa area"
        assert zones[0]["pmap_id"] == "m1"
        assert zones[0]["zone_type"] == "furniture"

    def test_zone_default_type(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("m1", [], zones=[{"id": "z1", "name": "Area"}])
        ])
        assert cc.zones[0]["zone_type"] == "default"

    def test_zones_from_multiple_pmaps(self):
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("m1", [], zones=[{"id": "z1", "name": "A", "zone_type": "furniture"}]),
            _make_pmap("m2", [], zones=[{"id": "z2", "name": "B", "zone_type": "furniture"}]),
        ])
        assert len(cc.zones) == 2


# ── RoombaData.has_cloud ───────────────────────────────────────────────────────

class TestRoombaDataHasCloud:
    """Tests for the RoombaData.has_cloud convenience property."""

    def _roomba_data(self, cloud_coordinator=None):
        rd = object.__new__(RoombaData)
        rd.cloud_coordinator = cloud_coordinator
        return rd

    def test_false_when_no_coordinator(self):
        rd = self._roomba_data(None)
        assert rd.has_cloud is False

    def test_false_when_coordinator_has_no_data(self):
        cc = _bare_coordinator()
        cc.data = None
        rd = self._roomba_data(cc)
        assert rd.has_cloud is False

    def test_true_when_coordinator_has_data(self):
        cc = _bare_coordinator()
        cc.data = _make_data()
        rd = self._roomba_data(cc)
        assert rd.has_cloud is True


# ── _resolve_rooms with cloud_pmap_id ─────────────────────────────────────────

class TestResolveRoomsWithCloudPmapId:
    """Verify that cloud_pmap_id takes priority over the MQTT cascade."""

    def test_cloud_pmap_id_used_for_empty_stored_pmap(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {}  # no MQTT pmap data at all
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_cloud_pmap_id_overrides_mqtt_cascade(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_map"}, "pmaps": [{"mqtt_map": "v1"}]}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_stored_pmap_id_still_used_over_cloud(self):
        """Stored pmap_id in zone_data takes priority over everything."""
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": "stored_map"}}
        state = {}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "stored_map")]

    def test_no_cloud_pmap_falls_back_to_mqtt(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_pmap"}}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id=None)
        assert result == [("21", "mqtt_pmap")]

    def test_empty_cloud_pmap_treated_as_none(self):
        """cloud_pmap_id='' should not override the MQTT fallback."""
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_pmap"}}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="")
        # empty string is falsy — falls through to MQTT cascade
        assert result == [("21", "mqtt_pmap")]
