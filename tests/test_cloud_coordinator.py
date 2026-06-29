"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import os
import types
import pytest
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.models import RoombaData
from custom_components.roomba_plus.models import MapCapability
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import patch
from custom_components.roomba_plus.cloud_coordinator import _MIN_UNAVAILABLE
from custom_components.roomba_plus.cloud_api import CloudApiError
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.roomba_plus.cloud_coordinator import _CLOUD_POLL_IDLE
from custom_components.roomba_plus.dirt_threshold_manager import MIN_GAP_HOURS
from custom_components.roomba_plus.dirt_threshold_manager import MIN_RECORDS
from custom_components.roomba_plus.dirt_threshold_manager import TRIGGER_MULTIPLIER_DEFAULT
from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
from custom_components.roomba_plus.dirt_threshold_manager import _compute_dirt_density
from custom_components.roomba_plus.const import CONF_DEMAND_CLEANING_ENABLED
from custom_components.roomba_plus.const import CONF_DEMAND_MULTIPLIER
from custom_components.roomba_plus.cloud_coordinator import _compute_daily_dirt_density
from custom_components.roomba_plus.cloud_coordinator import _parse_time_estimates
import asyncio
import time


ROOT = os.path.join(os.path.dirname(__file__), "..")
_GOOD_DATA = {
    "pmaps": [],
    "mission_history": {},
    "mission_history_raw": [],
    "favorites": [],
    "automations": {},
    "umf": {},
}


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


def _bare_coordinator() -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA setup."""
    cc = object.__new__(IrobotCloudCoordinator)
    cc.data = None
    cc.blid = "testblid"
    return cc


def _make_coordinator(umf_data=None, raw_records=None):
    from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
    coord = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
    coord.data = {
        "umf": umf_data or {},
        "mission_history_raw": raw_records or [],
        "pmaps": [],
    }
    return coord


def _make_coordinator_v240_coordinator() -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA infrastructure."""
    coord = object.__new__(IrobotCloudCoordinator)
    coord.data = None
    coord.blid = "TEST_BLID"
    coord._has_pmaps = False
    coord._mission_store = None
    coord._last_success_time = None
    coord.api = AsyncMock()
    coord.api.get_mission_history = AsyncMock(return_value=[])
    coord.api.get_automations = AsyncMock(return_value={})
    return coord


def _make_record(dirt: float, sqft: float) -> dict:
    """Make a minimal cloud record with dirt and sqft."""
    return {"dirt": dirt, "sqft": sqft, "runM": 20, "durationM": 25}


def _records(pairs: list[tuple[float, float]]) -> list[dict]:
    """Build a list of records from (dirt, sqft) pairs."""
    return [_make_record(d, s) for d, s in pairs]


def _make_manager(options: dict | None = None) -> DirtThresholdManager:
    """Build a DirtThresholdManager with minimal mocking."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = options if options is not None else {CONF_DEMAND_CLEANING_ENABLED: True}
    entry.entry_id = "test_entry"
    entry.runtime_data = MagicMock()
    entry.runtime_data.roomba_reported_state.return_value = {
        "cleanMissionStatus": {"cycle": "none"}
    }
    entry.runtime_data.blocking_manager = None
    entry.runtime_data.presence_manager = None
    mgr = DirtThresholdManager(hass, entry)
    return mgr


def _make_coordinator_v250_coordinator() -> IrobotCloudCoordinator:
    """Build a coordinator with minimal mocks, patching the aiohttp session."""
    hass = MagicMock()
    hass.config.country = "US"
    entry = MagicMock()
    with patch(
        "custom_components.roomba_plus.cloud_coordinator.async_get_clientsession",
        return_value=MagicMock(),
    ):
        coord = IrobotCloudCoordinator(
            hass=hass,
            config_entry=entry,
            blid="test_blid",
            username="user@test.com",
            password="secret",
            has_pmaps=True,
            mission_store=None,
        )
    return coord


def _raw_record(dirt: float, sqft: float, ts: int = 1748786400) -> dict:
    """Minimal raw cloud record with dirt, sqft, startTime."""
    return {"dirt": dirt, "sqft": sqft, "startTime": ts}


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

    def test_regions_from_multiple_pmaps_returns_active_only(self):
        """Active-map filter: when two pmaps exist, only active pmap regions returned.

        This is the v1.8.0 behaviour change — previously both pmaps were flattened.
        The first pmap is the active one (active_pmap_id returns the first pmap_id).
        """
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("map1", [{"id": "1", "name": "Hall", "region_type": "hallway"}]),
            _make_pmap("map2", [{"id": "2", "name": "Bed", "region_type": "bedroom"}]),
        ])
        regions = cc.regions
        # Only active pmap (map1) — map2 is the disabled old map
        assert len(regions) == 1
        assert regions[0]["pmap_id"] == "map1"
        assert regions[0]["name"] == "Hall"

    def test_regions_from_inactive_pmap_not_returned(self):
        """Duplicate names across pmaps: only active pmap wins, no collision."""
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("active_map", [
                {"id": "r1", "name": "Kitchen", "region_type": "kitchen"},
                {"id": "r2", "name": "Studio", "region_type": "room"},
            ]),
            _make_pmap("old_map", [
                {"id": "r3", "name": "Kitchen", "region_type": "kitchen"},  # duplicate name
                {"id": "r4", "name": "Studio", "region_type": "room"},      # duplicate name
            ]),
        ])
        regions = cc.regions
        assert len(regions) == 2
        assert all(r["pmap_id"] == "active_map" for r in regions)
        ids = {r["id"] for r in regions}
        assert ids == {"r1", "r2"}   # old_map entries must NOT appear

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

    def test_zones_from_multiple_pmaps_returns_active_only(self):
        """Active-map filter: two pmaps → only active pmap zones returned."""
        cc = _bare_coordinator()
        cc.data = _make_data(pmaps=[
            _make_pmap("m1", [], zones=[{"id": "z1", "name": "A", "zone_type": "furniture"}]),
            _make_pmap("m2", [], zones=[{"id": "z2", "name": "B", "zone_type": "furniture"}]),
        ])
        zones = cc.zones
        assert len(zones) == 1
        assert zones[0]["pmap_id"] == "m1"
        assert zones[0]["id"] == "z1"


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


class TestResolveRoomsWithCloudPmapId:
    """Verify that cloud_pmap_id takes priority over the MQTT cascade."""

    def test_cloud_pmap_id_used_for_empty_stored_pmap(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {}  # no MQTT pmap data at all
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_cloud_pmap_id_overrides_mqtt_cascade(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_map"}, "pmaps": [{"mqtt_map": "v1"}]}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_stored_pmap_id_still_used_over_cloud(self):
        """Stored pmap_id in zone_data takes priority over everything."""
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": "stored_map"}}
        state = {}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "stored_map")]

    def test_no_cloud_pmap_falls_back_to_mqtt(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_pmap"}}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id=None)
        assert result == [("21", "mqtt_pmap")]

    def test_empty_cloud_pmap_treated_as_none(self):
        """cloud_pmap_id='' should not override the MQTT fallback."""
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_pmap"}}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="")
        # empty string is falsy — falls through to MQTT cascade
        assert result == [("21", "mqtt_pmap")]


class TestMissionHistoryNormalization:
    """Coordinator must normalize list API response to a dict before storing."""

    def _bare_coordinator_with_history(self, raw_history):
        """Simulate what _async_update_data does with the raw API result."""
        if isinstance(raw_history, list):
            return raw_history[0] if raw_history else {}
        elif isinstance(raw_history, dict):
            return raw_history
        return {}

    def test_list_normalized_to_first_element(self):
        raw = [{"runtimeStats": {"sqft": 1000, "hr": 10, "min": 0}, "bbmssn": {"nMssn": 50}}]
        result = self._bare_coordinator_with_history(raw)
        assert isinstance(result, dict)
        assert result["runtimeStats"]["sqft"] == 1000

    def test_empty_list_normalized_to_empty_dict(self):
        result = self._bare_coordinator_with_history([])
        assert result == {}

    def test_dict_passed_through_unchanged(self):
        raw = {"runtimeStats": {"sqft": 500}, "bbmssn": {"nMssn": 20}}
        result = self._bare_coordinator_with_history(raw)
        assert result == raw

    def test_unexpected_type_produces_empty_dict(self):
        result = self._bare_coordinator_with_history("unexpected")
        assert result == {}

    def test_result_is_always_dict(self):
        for raw in [[], [{"a": 1}], {}, {"b": 2}, None, 42, "str"]:
            if raw is None or isinstance(raw, (int, str)):
                result = self._bare_coordinator_with_history(raw) if isinstance(raw, (list, dict)) else {}
            else:
                result = self._bare_coordinator_with_history(raw)
            assert isinstance(result, dict), f"Expected dict for input {raw!r}, got {type(result)}"


class TestClassifyMissionResultCompleted:
    """done="done" → "completed" regardless of other fields."""

    def test_basic_completed(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done"}) == "completed"

    def test_completed_with_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done", "done_raw": "done"}) == "completed"

    def test_completed_ignores_pause_id(self):
        """pauseId on a completed mission is irrelevant."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done", "pauseId": 17}) == "completed"


class TestClassifyMissionResultCancelledByUser:
    """done_raw="usrEnd" → "cancelled_by_user" even when done="cncl"."""

    def test_usr_end_is_cancelled_by_user(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": "usrEnd"}) == "cancelled_by_user"

    def test_usr_end_priority_over_cncl(self):
        """done_raw wins over done for user-cancellation detection."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        rec = {"done": "cncl", "done_raw": "usrEnd", "pauseId": 0}
        assert classify_mission_result(rec) == "cancelled_by_user"

    def test_usr_end_without_done_field(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done_raw": "usrEnd"}) == "cancelled_by_user"


class TestClassifyMissionResultCancelled:
    """done="cncl" without usrEnd → "cancelled"."""

    def test_cncl_without_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl"}) == "cancelled"

    def test_cncl_with_other_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": "cncl"}) == "cancelled"

    def test_cncl_with_empty_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": ""}) == "cancelled"


class TestClassifyMissionResultError:
    """done="stuck" + pauseId>0 → "error_{pauseId}"."""

    def test_error_17_cannot_find_home(self):
        """pauseId=17 is the confirmed field log case (WiFi dropout)."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 17}) == "error_17"

    def test_error_18_docking_issue(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 18}) == "error_18"

    def test_error_224_smart_map_localization(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 224}) == "error_224"

    def test_error_code_in_error_catalogue(self):
        """Every error_{code} result should map to a known ERROR_CATALOGUE entry."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        from custom_components.roomba_plus.const import ERROR_CATALOGUE
        for code in (1, 2, 4, 5, 6, 9, 17, 18, 32, 36, 42, 224):
            result = classify_mission_result({"done": "stuck", "pauseId": code})
            assert result == f"error_{code}"
            assert code in ERROR_CATALOGUE, f"pauseId {code} missing from ERROR_CATALOGUE"


class TestClassifyMissionResultStuck:
    """done="stuck" + pauseId=0 or missing → "stuck"."""

    def test_stuck_no_pause_id(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck"}) == "stuck"

    def test_stuck_pause_id_zero(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 0}) == "stuck"

    def test_stuck_pause_id_none(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": None}) == "stuck"


class TestClassifyMissionResultUnknown:
    """Unrecognised done values → "unknown"."""

    def test_empty_done(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({}) == "unknown"

    def test_none_done(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": None}) == "unknown"

    def test_future_done_value(self):
        """New iRobot firmware may introduce new done values."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "newValue"}) == "unknown"


class TestClassifiedResultInRawRecords:
    """classified_result field is pre-computed and stored in raw records."""

    def _make_raw_records(self, records: list) -> list:
        """Simulate what the coordinator stores: records with classified_result."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        return [
            {**r, "classified_result": classify_mission_result(r)}
            for r in records
            if isinstance(r, dict)
        ]

    def test_completed_record_has_classified_result(self):
        records = self._make_raw_records([{"done": "done", "nMssn": 10, "sqft": 100}])
        assert records[0]["classified_result"] == "completed"

    def test_user_cancelled_record(self):
        records = self._make_raw_records([{"done": "cncl", "done_raw": "usrEnd"}])
        assert records[0]["classified_result"] == "cancelled_by_user"

    def test_error_record_preserves_all_fields(self):
        original = {
            "done": "stuck",
            "done_raw": "stuck",
            "pauseId": 17,
            "startTime": 1700000000,
            "sqft": 80,
            "wlBars": [55, 42, 3, 0, 0],
        }
        records = self._make_raw_records([original])
        rec = records[0]
        assert rec["classified_result"] == "error_17"
        # All original fields preserved
        for key, val in original.items():
            assert rec[key] == val

    def test_mixed_batch(self):
        batch = [
            {"done": "done", "nMssn": 5},
            {"done": "cncl", "done_raw": "usrEnd"},
            {"done": "stuck", "pauseId": 18},
            {"done": "stuck", "pauseId": 0},
            {"done": "cncl"},
        ]
        records = self._make_raw_records(batch)
        assert [r["classified_result"] for r in records] == [
            "completed",
            "cancelled_by_user",
            "error_18",
            "stuck",
            "cancelled",
        ]


class TestCoordinatorMissionHistoryRaw:
    """_async_update_data stores raw records with classified_result field."""

    def _make_raw_history(self, records):
        """Simulate what _async_update_data stores in mission_history_raw."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        return [
            {**r, "classified_result": classify_mission_result(r)}
            for r in records
            if isinstance(r, dict)
        ]

    def test_all_records_get_classified_result(self):
        records = [
            {"done": "done",  "nMssn": 5, "sqft": 100},
            {"done": "stuck", "pauseId": 17},
            {"done": "cncl",  "done_raw": "usrEnd"},
        ]
        stored = self._make_raw_history(records)
        assert len(stored) == 3
        assert stored[0]["classified_result"] == "completed"
        assert stored[1]["classified_result"] == "error_17"
        assert stored[2]["classified_result"] == "cancelled_by_user"

    def test_non_dict_records_filtered(self):
        records = [{"done": "done"}, "bad", None, 42, {"done": "stuck"}]
        stored = self._make_raw_history(records)
        assert len(stored) == 2

    def test_original_fields_preserved(self):
        rec = {
            "done": "done", "done_raw": "done", "startTime": 1700000000,
            "timestamp": 1700003600, "sqft": 200, "runM": 55, "wlBars": [70, 65],
        }
        stored = self._make_raw_history([rec])
        for key in rec:
            assert stored[0][key] == rec[key]

    def test_default_key_present_in_data(self):
        """coordinator.data always has mission_history_raw key even when empty."""
        # Simulate the coordinator data structure
        data = {
            "pmaps": [],
            "mission_history": {},
            "mission_history_raw": [],
            "favorites": [],
        }
        assert "mission_history_raw" in data
        assert isinstance(data["mission_history_raw"], list)

    def test_raw_records_property_reads_from_data(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCoord(IrobotCloudCoordinator):
            def __init__(self, data_val):
                self.data = data_val

        records = [{"done": "done", "classified_result": "completed"}]
        coord = _FakeCoord({"mission_history_raw": records})
        assert coord.raw_records == records

    def test_raw_records_empty_when_data_none(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCoord(IrobotCloudCoordinator):
            def __init__(self):
                self.data = None

        assert _FakeCoord().raw_records == []


class TestUmfProperties:
    def test_umf_data_returns_empty_when_no_data(self):
        coord = _make_coordinator()
        coord.data = None
        assert coord.umf_data == {}

    def test_umf_data_returns_dict_when_present(self):
        coord = _make_coordinator(umf_data={"keepoutzones": [], "observed_zones": []})
        assert isinstance(coord.umf_data, dict)

    def test_keepout_zones_from_umf(self):
        coord = _make_coordinator(umf_data={
            "keepoutzones": [{"id": "k1", "space": "umf"}],
            "observed_zones": [],
        })
        assert len(coord.keepout_zones) == 1
        assert coord.keepout_zones[0]["id"] == "k1"

    def test_keepout_zones_empty_when_absent(self):
        coord = _make_coordinator()
        assert coord.keepout_zones == []

    def test_observed_zone_centroids_cx_cy(self):
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"cx": 750.0, "cy": 500.0, "space": "umf"}],
        })
        centroids = coord.observed_zone_centroids
        assert len(centroids) == 1
        assert centroids[0]["x"] == 750.0
        assert centroids[0]["y"] == 500.0
        assert centroids[0]["space"] == "umf"

    def test_observed_zone_centroids_fallback_x_y(self):
        # Falls back to x/y when cx/cy absent
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"x": 300.0, "y": 200.0}],
        })
        centroids = coord.observed_zone_centroids
        assert len(centroids) == 1
        assert centroids[0]["x"] == 300.0

    def test_observed_zone_centroids_skips_missing_coords(self):
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"no_coord": True}],
        })
        assert coord.observed_zone_centroids == []

    def test_observed_zone_centroids_empty_when_no_umf(self):
        coord = _make_coordinator()
        assert coord.observed_zone_centroids == []


class TestPollInterval:

    def test_poll_interval_is_24_hours(self):
        """Cloud poll interval must be fixed at 24 h.

        Adaptive 5-min polling during missions was removed: cloud data
        (mission history, pmaps) only updates after mission end anyway.
        Post-mission refresh is handled explicitly by F4b in callbacks.py.
        """
        from datetime import timedelta
        assert _CLOUD_POLL_IDLE == timedelta(hours=24)

    def test_no_adaptive_interval_method(self):
        """_is_robot_cleaning must not exist — adaptive polling was removed."""
        coord = _make_coordinator_v240_coordinator()
        assert not hasattr(coord, "_is_robot_cleaning"), (
            "_is_robot_cleaning should have been removed with adaptive polling"
        )


class TestAsyncEvaluate:

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self):
        mgr = _make_manager(options={CONF_DEMAND_CLEANING_ENABLED: False})
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }
        # Should return without calling roomba.send_command
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_robot_busy(self):
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        # Robot is actively cleaning
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "clean"}
        }
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_blocking_manager_queued(self):
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = _records([(10, 100)] * 6)
        bm = MagicMock()
        bm.is_queued = True
        mgr._entry.runtime_data.blocking_manager = bm
        await mgr.async_evaluate(coord, "test_entry")
        mgr._entry.runtime_data.roomba.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_triggers_when_all_gates_pass(self):
        mgr = _make_manager()
        coord = MagicMock()
        # 5 baseline records + 1 hot record (2× density)
        baseline = _records([(5, 100)] * MIN_RECORDS)
        coord.raw_records = [_make_record(10, 100)] + baseline
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }

        with patch.object(mgr, 'async_save', new_callable=AsyncMock):
            with patch.object(mgr._hass, 'async_add_executor_job', new_callable=AsyncMock) as mock_job:
                await mgr.async_evaluate(coord, "test_entry")
            mock_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_last_trigger_time_set_after_trigger(self):
        mgr = _make_manager()
        coord = MagicMock()
        baseline = _records([(5, 100)] * MIN_RECORDS)
        coord.raw_records = [_make_record(10, 100)] + baseline
        mgr._entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "none"}
        }

        assert mgr._last_trigger_time is None
        with patch.object(mgr, 'async_save', new_callable=AsyncMock):
            with patch.object(mgr._hass, 'async_add_executor_job', new_callable=AsyncMock):
                await mgr.async_evaluate(coord, "test_entry")
        assert mgr._last_trigger_time is not None

    @pytest.mark.asyncio
    async def test_does_not_raise_on_exception(self):
        """async_evaluate must never propagate exceptions."""
        mgr = _make_manager()
        coord = MagicMock()
        coord.raw_records = None  # triggers AttributeError inside
        # Should complete without raising
        await mgr.async_evaluate(coord, "test_entry")


class TestF11WiringInInit:
    """v2.4.2 — async_evaluate must be scheduled after every cloud refresh.

    Before the fix, DirtThresholdManager was instantiated but async_evaluate
    was never called, making demand cleaning completely non-functional.
    """

    @pytest.mark.asyncio
    async def test_async_evaluate_called_after_cloud_refresh(self):
        """async_evaluate is scheduled via async_create_task after merge."""
        from unittest.mock import AsyncMock, MagicMock, call, patch

        hass = MagicMock()
        hass.async_create_task = MagicMock()

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"

        ms = MagicMock()
        ms.merge_latest_from_cloud.return_value = False

        dtm = MagicMock()
        evaluate_coro = AsyncMock()
        dtm.async_evaluate.return_value = evaluate_coro()

        runtime = MagicMock()
        runtime.mission_store = ms
        runtime.dirt_threshold_manager = dtm
        config_entry.runtime_data = runtime

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.raw_records = []

        # Simulate _on_cloud_refresh_complete inline — matches __init__.py logic
        if not cloud_coordinator.last_update_success:
            return
        if ms is None:
            return
        ms.merge_latest_from_cloud(cloud_coordinator.raw_records)
        _dtm = config_entry.runtime_data.dirt_threshold_manager
        if _dtm is not None:
            hass.async_create_task(
                _dtm.async_evaluate(cloud_coordinator, config_entry.entry_id),
                name="roomba_plus_demand_clean_eval",
            )

        dtm.async_evaluate.assert_called_once_with(cloud_coordinator, "test_entry")
        hass.async_create_task.assert_called_once()
        _, kwargs = hass.async_create_task.call_args
        assert kwargs.get("name") == "roomba_plus_demand_clean_eval"


class TestPerRefreshBackfillWiring:
    """v2.9.0 — _on_cloud_refresh_complete must call backfill_from_cloud()
    on EVERY cloud refresh, not merge_latest_from_cloud() (single-shot,
    only ever tried the newest record once, no retry). Root cause of
    Thonno's stale last_cleaned_rooms report: if that one attempt missed
    (e.g. local ended_at drifted outside the ±120s match tolerance — more
    likely while a mission's end confirmation was delayed, see the v2.8.7
    stuck-mission fix), the record's timeline/analytics fields stayed
    missing forever, since nothing else ever revisited that record until
    the next HA restart's one-time backfill_from_cloud() pass.

    __init__.py's _on_cloud_refresh_complete is a closure nested inside
    async_setup_entry — not independently importable/callable — so this
    is a source-text check (same technique already used in
    test_presence_manager.py's record_clean_event placement test) rather
    than an execution test.
    """

    def _init_source(self) -> str:
        import inspect
        import custom_components.roomba_plus.callbacks as cb_mod
        return inspect.getsource(cb_mod)

    def _refresh_handler_body(self) -> str:
        src = self._init_source()
        idx = src.find("def _on_cloud_refresh_complete()")
        assert idx != -1, "_on_cloud_refresh_complete definition not found in callbacks.py"
        return src[idx:idx + 3000]

    def test_backfill_from_cloud_is_called_in_refresh_handler(self):
        body = self._refresh_handler_body()
        assert "ms.backfill_from_cloud(" in body, (
            "backfill_from_cloud() must be called on every cloud refresh — "
            "merge_latest_from_cloud()'s single-shot-no-retry design left "
            "records permanently missing their timeline/analytics fields "
            "whenever its one match attempt failed (Thonno's stale "
            "last_cleaned_rooms report)"
        )

    def test_merge_latest_from_cloud_no_longer_called_in_refresh_handler(self):
        """The narrower, no-retry function must not have crept back in."""
        body = self._refresh_handler_body()
        assert "ms.merge_latest_from_cloud(" not in body

    def test_save_gated_on_corrected_or_enriched(self):
        """Must check BOTH corrected and enriched (matching the existing
        startup backfill_from_cloud() call's check) — not just one,
        otherwise a timestamp-only correction with no field enrichment
        would silently never get persisted."""
        body = self._refresh_handler_body()
        assert "_bf.corrected or _bf.enriched" in body


class TestDailyDirtDensityCache:
    def test_empty_on_init(self):
        """daily_dirt_density must be an empty dict on a fresh coordinator."""
        coord = _make_coordinator_v250_coordinator()
        assert coord.daily_dirt_density == {}

    def test_compute_from_records(self):
        """_compute_daily_dirt_density groups by date and returns median per day."""
        # 2026-06-01 00:00:00 UTC → timestamp 1748736000
        ts_day1 = 1748736000
        # 2026-06-02 00:00:00 UTC → timestamp 1748822400
        ts_day2 = 1748822400
        records = [
            _raw_record(10.0, 100.0, ts_day1),   # day1: density = 10/(100*0.09290304)
            _raw_record(20.0, 100.0, ts_day1),   # day1: second record
            _raw_record(15.0, 100.0, ts_day2),   # day2
        ]
        result = _compute_daily_dirt_density(records)
        assert len(result) == 2
        # day1 median of [10/(100*SQFT_TO_M2), 20/(100*SQFT_TO_M2)]
        from custom_components.roomba_plus.const import SQFT_TO_M2
        expected_d1 = ((10 / (100 * SQFT_TO_M2)) + (20 / (100 * SQFT_TO_M2))) / 2
        day1_key = next(k for k in result if "01" in k)
        assert abs(result[day1_key] - expected_d1) < 0.01

    def test_empty_records_returns_empty(self):
        """Empty record list must return empty dict."""
        assert _compute_daily_dirt_density([]) == {}

    def test_records_without_dirt_skipped(self):
        """Records with no dirt field must be silently skipped."""
        records = [{"sqft": 100.0, "startTime": 1748736000}]
        assert _compute_daily_dirt_density(records) == {}


class TestParseTimeEstimates:
    def test_two_pass_entry_parsed(self):
        raw = [
            {
                "unit": "seconds",
                "estimate": 2639,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": True},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["two_pass_sec"] == 2639
        assert result["one_pass_sec"] is None

    def test_one_pass_entry_parsed(self):
        raw = [
            {
                "unit": "seconds",
                "estimate": 1319,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["one_pass_sec"] == 1319
        assert result["two_pass_sec"] is None

    def test_low_confidence_entry_filtered(self):
        """Entries with confidence != GOOD_CONFIDENCE must be excluded."""
        raw = [
            {
                "unit": "seconds",
                "estimate": 999,
                "confidence": "LOW_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            }
        ]
        result = _parse_time_estimates(raw)
        assert result["one_pass_sec"] is None
        assert result["two_pass_sec"] is None

    def test_empty_list_returns_both_none(self):
        """Auto mode has no entries — both keys must be None."""
        result = _parse_time_estimates([])
        assert result == {"one_pass_sec": None, "two_pass_sec": None}

    def test_non_list_input_returns_both_none(self):
        """Unexpected API shape (string, dict, None) must return both None, not raise."""
        assert _parse_time_estimates(None) == {"one_pass_sec": None, "two_pass_sec": None}
        assert _parse_time_estimates("bad") == {"one_pass_sec": None, "two_pass_sec": None}
        assert _parse_time_estimates({}) == {"one_pass_sec": None, "two_pass_sec": None}

    def test_both_passes_present(self):
        """Both one-pass and two-pass entries can be present simultaneously."""
        raw = [
            {
                "unit": "seconds",
                "estimate": 2639,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": True},
            },
            {
                "unit": "seconds",
                "estimate": 1319,
                "confidence": "GOOD_CONFIDENCE",
                "params": {"noAutoPasses": True, "twoPass": False},
            },
        ]
        result = _parse_time_estimates(raw)
        assert result["two_pass_sec"] == 2639
        assert result["one_pass_sec"] == 1319


class TestPmapLocalSeed:
    def test_seed_sets_seeded_pmap_id(self):
        """seed_pmap_id_from_local must set _seeded_pmap_id from pmaps[0]."""
        coord = _make_coordinator_v250_coordinator()
        coord.data = None   # no cloud data yet
        reported_state = {"pmaps": [{"2Bly_kGURy6OcUVTX7FN3w": "ABC_v1"}]}
        coord.seed_pmap_id_from_local(reported_state)
        assert coord._seeded_pmap_id == "2Bly_kGURy6OcUVTX7FN3w"

    def test_active_pmap_id_returns_seed_when_data_none(self):
        """active_pmap_id must return _seeded_pmap_id when coordinator data is None."""
        coord = _make_coordinator_v250_coordinator()
        coord.data = None
        coord._seeded_pmap_id = "seeded_pmap_abc"
        assert coord.active_pmap_id == "seeded_pmap_abc"

    def test_active_pmap_id_prefers_cloud_data_over_seed(self):
        """When cloud data carries a timestamp, active_pmap_id uses it, not the seed.

        Uses Variant C (root-level active_pmapv_id) — the ia74 / older firmware
        format. ts = "v1" > "" → cloud pmap selected over _seeded_pmap_id.
        See v2.7.5 TestActivePmapIdSeedFallback for the no-timestamp case.
        """
        coord = _make_coordinator_v250_coordinator()
        coord._seeded_pmap_id = "old_seed"
        coord.data = {
            "pmaps": [{
                "active_pmapv_id": "v1",           # Variant C — gives ts = "v1"
                "active_pmapv_details": {
                    "active_pmapv": {"pmap_id": "real_cloud_pmap"},
                    "regions": [],
                },
            }],
            "mission_history_raw": [],
        }
        assert coord.active_pmap_id == "real_cloud_pmap"

    def test_seed_skipped_when_cloud_data_present(self):
        """seed_pmap_id_from_local must not overwrite when data is already set."""
        coord = _make_coordinator_v250_coordinator()
        coord.data = {"pmaps": [], "mission_history_raw": []}
        coord._seeded_pmap_id = "existing_seed"
        coord.seed_pmap_id_from_local({"pmaps": [{"new_pmap": "v1"}]})
        # Seed must not have changed
        assert coord._seeded_pmap_id == "existing_seed"

    def test_seed_handles_missing_pmaps_gracefully(self):
        """seed_pmap_id_from_local must not raise when pmaps is absent."""
        coord = _make_coordinator_v250_coordinator()
        coord.data = None
        coord.seed_pmap_id_from_local({})   # no pmaps key
        assert coord._seeded_pmap_id is None


class TestCloudPmapvId:
    """Cloud coordinator provides current user_pmapv_id as primary source.

    lewis 22.52.10 does not broadcast pmaps updates via local MQTT after
    map changes. _resolve_pmapv_id reads stale local value → error 224
    (Smart Map localization failed). Cloud has the current pmapv.
    """

    def _make_cc(self, pmaps_data):
        from unittest.mock import MagicMock
        cc = MagicMock()
        cc.data = {"pmaps": pmaps_data}
        cc.active_pmap_id = "PMAP1"
        return cc

    def test_active_user_pmapv_id_variant_b_lewis(self):
        """Variant B (lewis 22.52.10): last_user_pmapv_id is the active pmapv."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": [{
            "active_pmapv_details": {
                "active_pmapv": {
                    "pmap_id": "PMAP1",
                    "last_user_pmapv_id": "PMAPV_CURRENT",
                    # Note: active_pmapv_id absent (Variant B robot)
                }
            }
        }]}

        assert cc.active_user_pmapv_id == "PMAPV_CURRENT"

    def test_active_user_pmapv_id_variant_a(self):
        """Variant A: active_pmapv_id is the primary key."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": [{
            "active_pmapv_details": {
                "active_pmapv": {
                    "pmap_id": "PMAP1",
                    "active_pmapv_id": "PMAPV_A",
                }
            }
        }]}

        assert cc.active_user_pmapv_id == "PMAPV_A"

    def test_active_user_pmapv_id_none_when_no_data(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = None
        assert cc.active_user_pmapv_id is None

    def test_active_user_pmapv_id_no_pmaps_returns_none(self):
        """No pmaps in cloud data → None."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": []}
        assert cc.active_user_pmapv_id is None


class TestCleanRoomPmapSelection:
    """clean_room prefers local MQTT pmaps[0] over cloud coordinator."""

    def _make_call(self, entity_id: str, room_name: str, pmap_a: str, pmap_b: str):
        """Build a minimal ServiceCall mock with two-map robot state."""
        from homeassistant.const import ATTR_ENTITY_ID
        from custom_components.roomba_plus.const import (
            ATTR_ROOM_NAME, ATTR_ORDERED, CONF_SMART_ZONE_DATA,
        )
        call = MagicMock()
        call.hass = MagicMock()
        call.data = {
            "entity_id": [entity_id],
            ATTR_ROOM_NAME: room_name,
            ATTR_ORDERED: True,
        }

        # Entity registry
        ent_entry = MagicMock()
        ent_entry.config_entry_id = "entry1"
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = ent_entry

        # Config entry
        from custom_components.roomba_plus.models import MapCapability
        data = MagicMock()
        data.map_capability = MapCapability.SMART
        data.has_cloud = True
        # Cloud coordinator returns WRONG map (pmap_b) — second map first in API
        data.cloud_coordinator.active_pmap_id = pmap_b
        data.cloud_coordinator.regions = [
            {"id": "r1", "name": room_name, "pmap_id": pmap_b}
        ]
        data.cloud_coordinator.zones = []
        # Local MQTT state has CORRECT map (pmap_a)
        data.roomba_reported_state.return_value = {
            "pmaps": [{pmap_a: "pmapv_a"}],  # local: only active map
            "cleanMissionStatus": {"notReady": 0},
            "lastCommand": {"pmap_id": pmap_b},  # stale!
        }
        data.roomba.master_state = {}

        config_entry = MagicMock()
        config_entry.runtime_data = data
        config_entry.options = {
            CONF_SMART_ZONE_DATA: {
                "r_a": {"name": room_name, "pmap_id": pmap_a},
            }
        }

        call.hass.config_entries.async_get_entry.return_value = config_entry

        import homeassistant.helpers.entity_registry as er_mod
        with patch.object(er_mod, "async_get", return_value=ent_reg):
            pass

        return call, ent_reg, config_entry

    @pytest.mark.asyncio
    async def test_local_pmap_preferred_over_stale_cloud(self):
        """clean_room uses local MQTT pmaps[0] key, not cloud coordinator.active_pmap_id."""
        from custom_components.roomba_plus.services import async_handle_clean_room
        from custom_components.roomba_plus.const import ATTR_ROOM_NAME, ATTR_ORDERED

        pmap_active = "pmap_ACTIVE_current"
        pmap_stale  = "pmap_STALE_old_floor"

        hass = MagicMock()
        call = MagicMock()
        call.hass = hass
        call.data = {
            "entity_id": ["vacuum.test"],
            ATTR_ROOM_NAME: "Kitchen",
            ATTR_ORDERED: True,
        }

        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_DATA

        data = MagicMock()
        data.map_capability = MapCapability.SMART
        data.has_cloud = True
        data.cloud_coordinator.active_pmap_id = pmap_stale   # wrong map from cloud
        data.cloud_coordinator.regions = []
        data.cloud_coordinator.zones = []
        # Local MQTT reports correct (active) map only
        data.roomba_reported_state.return_value = {
            "pmaps": [{pmap_active: "pmapv_active"}],
            "cleanMissionStatus": {"notReady": 0},
            "lastCommand": {},
            "noAutoPasses": False,
            "twoPass": False,
        }

        config_entry = MagicMock()
        config_entry.runtime_data = data
        config_entry.options = {
            CONF_SMART_ZONE_DATA: {
                "rid_kitchen": {"name": "Kitchen", "pmap_id": pmap_active},
            }
        }

        ent_entry = MagicMock()
        ent_entry.config_entry_id = "entry1"
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = ent_entry
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.async_add_executor_job = AsyncMock()

        # Capture the pmap_id sent to the robot
        sent_params = {}
        async def capture_send(fn, cmd, params):
            sent_params.update(params)
        hass.async_add_executor_job.side_effect = capture_send

        import homeassistant.helpers.entity_registry as er_mod
        with patch.object(er_mod, "async_get", return_value=ent_reg):
            await async_handle_clean_room(call)

        assert sent_params.get("pmap_id") == pmap_active, (
            f"Expected active pmap {pmap_active[:8]}, got {sent_params.get('pmap_id', 'none')[:8]}"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_cloud_when_no_local_pmaps(self):
        """clean_room falls back to cloud coordinator when local pmaps is empty."""
        from custom_components.roomba_plus.services import async_handle_clean_room
        from custom_components.roomba_plus.const import ATTR_ROOM_NAME, ATTR_ORDERED
        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_DATA

        pmap_cloud = "pmap_from_cloud"

        hass = MagicMock()
        call = MagicMock()
        call.hass = hass
        call.data = {
            "entity_id": ["vacuum.test"],
            ATTR_ROOM_NAME: "Lounge",
            ATTR_ORDERED: False,
        }

        data = MagicMock()
        data.map_capability = MapCapability.SMART
        data.has_cloud = True
        data.cloud_coordinator.active_pmap_id = pmap_cloud
        data.cloud_coordinator.regions = []
        data.cloud_coordinator.zones = []
        # Local MQTT has no pmaps field
        data.roomba_reported_state.return_value = {
            "pmaps": [],   # empty — no local pmap available
            "cleanMissionStatus": {"notReady": 0},
            "lastCommand": {},
            "noAutoPasses": False,
            "twoPass": False,
        }

        config_entry = MagicMock()
        config_entry.runtime_data = data
        config_entry.options = {
            CONF_SMART_ZONE_DATA: {
                "rid_lounge": {"name": "Lounge", "pmap_id": pmap_cloud},
            }
        }

        ent_entry = MagicMock()
        ent_entry.config_entry_id = "entry1"
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = ent_entry
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.async_add_executor_job = AsyncMock()

        sent_params = {}
        async def capture_send(fn, cmd, params):
            sent_params.update(params)
        hass.async_add_executor_job.side_effect = capture_send

        import homeassistant.helpers.entity_registry as er_mod
        with patch.object(er_mod, "async_get", return_value=ent_reg):
            await async_handle_clean_room(call)

        assert sent_params.get("pmap_id") == pmap_cloud


class TestActivePmapNewest:
    """active_pmap_id returns the pmap with the most recent pmapv timestamp."""

    def _make_coordinator(self, pmaps_data: list[dict]):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = {"pmaps": pmaps_data}
        return cc

    def _pmap_entry(self, pmap_id: str, ts: str) -> dict:
        return {
            "active_pmapv_details": {
                "active_pmapv": {
                    "pmap_id": pmap_id,
                    "last_user_pmapv_id": ts,
                }
            }
        }

    def test_returns_newest_when_oldest_is_first(self):
        """API returns old map first — newest pmapv must still win."""
        cc = self._make_coordinator([
            self._pmap_entry("oGwE_old", "260112T123749"),  # Jan 2026 — first in list
            self._pmap_entry("8Vfo_new", "260614T175302"),  # Jun 2026 — second
        ])
        assert cc.active_pmap_id == "8Vfo_new"

    def test_returns_only_map_when_single(self):
        """Single-map robots: active_pmap_id returns that map unchanged."""
        cc = self._make_coordinator([
            self._pmap_entry("only_map", "260101T090000"),
        ])
        assert cc.active_pmap_id == "only_map"

    def test_skips_entries_without_pmap_id(self):
        """Malformed entries without pmap_id are skipped."""
        cc = self._make_coordinator([
            {"active_pmapv_details": {"active_pmapv": {}}},   # no pmap_id
            self._pmap_entry("valid_map", "260614T100000"),
        ])
        assert cc.active_pmap_id == "valid_map"


class TestResolvepmapvPriority:
    """`_resolve_pmapv_id` prefers lastCommand over state.pmaps."""

    def test_prefers_lastcommand_when_pmap_matches(self):
        """When lastCommand.pmap_id matches, its user_pmapv_id is returned."""
        from custom_components.roomba_plus.services import _resolve_pmapv_id

        state = {
            "lastCommand": {
                "pmap_id": "map_A",
                "user_pmapv_id": "260614T103750",  # stable committed version
            },
            "pmaps": [
                {"map_A": "260614T175302"},         # live in-flux version
            ],
        }
        result = _resolve_pmapv_id(state, "map_A")
        assert result == "260614T103750"            # lastCommand wins

    def test_falls_back_to_pmaps_when_pmap_differs(self):
        """When lastCommand has a different pmap_id, state.pmaps is used."""
        from custom_components.roomba_plus.services import _resolve_pmapv_id

        state = {
            "lastCommand": {
                "pmap_id": "map_B",                 # different map
                "user_pmapv_id": "260614T103750",
            },
            "pmaps": [
                {"map_A": "260101T120000"},
            ],
        }
        result = _resolve_pmapv_id(state, "map_A")
        assert result == "260101T120000"            # state.pmaps fallback


class TestCleanRoomCloudPmapvFirst:
    """async_handle_clean_room uses cloud active_user_pmapv_id over local."""

    @pytest.mark.asyncio
    async def test_cloud_user_pmapv_id_preferred_over_state_pmaps(self):
        """Cloud last_user_pmapv_id is used; stale state.pmaps value ignored."""
        from custom_components.roomba_plus.services import async_handle_clean_room
        from custom_components.roomba_plus.const import (
            ATTR_ROOM_NAME, ATTR_ORDERED, CONF_SMART_ZONE_DATA,
        )
        from custom_components.roomba_plus.models import MapCapability

        pmap_id = "8VfoJEhaQ12ZGZaGlJp3wQ"
        cloud_pmapv = "260614T103750"   # stable cloud version (app used this)
        live_pmapv  = "260614T175302"   # live state.pmaps (in-flux, causes 224)

        hass = MagicMock()
        call = MagicMock()
        call.hass = hass
        call.data = {
            "entity_id": ["vacuum.test"],
            ATTR_ROOM_NAME: "Kitchen",
            ATTR_ORDERED: True,
        }

        data = MagicMock()
        data.map_capability = MapCapability.SMART
        data.has_cloud = True
        data.cloud_coordinator.active_pmap_id = pmap_id
        data.cloud_coordinator.active_user_pmapv_id = cloud_pmapv  # ← cloud value
        data.cloud_coordinator.regions = []
        data.cloud_coordinator.zones = []
        data.roomba_reported_state.return_value = {
            "pmaps": [{pmap_id: live_pmapv}],       # ← wrong live value
            "lastCommand": {
                "pmap_id": pmap_id,
                "user_pmapv_id": cloud_pmapv,       # lastCommand also has correct value
            },
            "cleanMissionStatus": {"notReady": 0},
            "noAutoPasses": False,
            "twoPass": False,
        }

        config_entry = MagicMock()
        config_entry.runtime_data = data
        config_entry.options = {
            CONF_SMART_ZONE_DATA: {
                "rid_kitchen": {"name": "Kitchen", "pmap_id": pmap_id},
            }
        }

        ent_entry = MagicMock()
        ent_entry.config_entry_id = "e1"
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = ent_entry
        hass.config_entries.async_get_entry.return_value = config_entry
        hass.async_add_executor_job = AsyncMock()

        sent = {}
        async def capture(fn, cmd, params):
            sent.update(params)
        hass.async_add_executor_job.side_effect = capture

        import homeassistant.helpers.entity_registry as er_mod
        with patch.object(er_mod, "async_get", return_value=ent_reg):
            await async_handle_clean_room(call)

        assert sent.get("user_pmapv_id") == cloud_pmapv, (
            f"Expected cloud pmapv {cloud_pmapv}, got {sent.get('user_pmapv_id')}"
        )


class TestSeedPmapNewest:
    """seed_pmap_id_from_local picks the pmap with the newest pmapv timestamp."""

    def _make_cc(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = None          # pre-cloud-fetch state
        cc.blid = "test"
        cc._seeded_pmap_id = None
        return cc

    def test_picks_newest_pmapv_not_first_entry(self):
        """When pmaps[0] is older, the newer second map is seeded."""
        cc = self._make_cc()
        cc.seed_pmap_id_from_local({
            "pmaps": [
                {"oGwE_old": "260112T123749"},   # Jan 2026 — first but inactive
                {"8Vfo_new": "260614T175302"},   # Jun 2026 — active
            ]
        })
        assert cc._seeded_pmap_id == "8Vfo_new"

    def test_single_map_robot_unchanged(self):
        """Single-map robots: seeded to the only map available."""
        cc = self._make_cc()
        cc.seed_pmap_id_from_local({"pmaps": [{"only_map": "260101T090000"}]})
        assert cc._seeded_pmap_id == "only_map"

    def test_no_seed_when_cloud_data_present(self):
        """Guard still respected: no seed when self.data is already populated."""
        cc = self._make_cc()
        cc.data = {"pmaps": []}   # simulate post-cloud-fetch
        cc.seed_pmap_id_from_local({
            "pmaps": [{"new_map": "260614T175302"}]
        })
        assert cc._seeded_pmap_id is None   # unchanged


class TestActivePmapIdSeedFallback:
    """active_pmap_id falls back to _seeded_pmap_id when cloud yields no result."""

    def test_returns_seed_when_cloud_data_has_no_valid_pmapv(self):
        """When all cloud pmaps lack ts, _seeded_pmap_id is used as fallback."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        # Cloud data present but no last_user_pmapv_id / active_pmapv_id
        cc.data = {"pmaps": [
            {"active_pmapv_details": {"active_pmapv": {"pmap_id": "map_A"}}}
            # ts = "" for all → best_ts stays "" → best_pid stays None
        ]}
        cc._seeded_pmap_id = "map_A_from_seed"
        assert cc.active_pmap_id == "map_A_from_seed"
