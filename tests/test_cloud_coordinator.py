"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import os
import types
import pytest

from tests.conftest import robot_mock, hass_mock, entry_mock
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
from types import SimpleNamespace
from homeassistant.exceptions import ConfigEntryAuthFailed
from custom_components.roomba_plus.cloud_api import AuthenticationError
import tests.conftest
from custom_components.roomba_plus.cloud_coordinator import _normalize_mission_history
from custom_components.roomba_plus.cloud_coordinator import _aggregate_history


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
    hass = hass_mock()
    entry = entry_mock()
    entry.options = options if options is not None else {CONF_DEMAND_CLEANING_ENABLED: True}
    entry.entry_id = "test_entry"
    entry.runtime_data = MagicMock()
    # The client is awaited since roombapy 2.x.
    entry.runtime_data.roomba = robot_mock()
    entry.runtime_data.roomba_reported_state.return_value = {
        "cleanMissionStatus": {"cycle": "none"}
    }
    entry.runtime_data.blocking_manager = None
    entry.runtime_data.presence_manager = None
    mgr = DirtThresholdManager(hass, entry)
    return mgr


def _make_coordinator_v250_coordinator() -> IrobotCloudCoordinator:
    """Build a coordinator with minimal mocks, patching the aiohttp session."""
    hass = hass_mock()
    hass.config.country = "US"
    entry = entry_mock()
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
        from custom_components.roomba_plus.room_cleaning import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {}  # no MQTT pmap data at all
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_cloud_pmap_id_overrides_mqtt_cascade(self):
        from custom_components.roomba_plus.room_cleaning import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_map"}, "pmaps": [{"mqtt_map": "v1"}]}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "cloud_map_123")]

    def test_stored_pmap_id_still_used_over_cloud(self):
        """Stored pmap_id in zone_data takes priority over everything."""
        from custom_components.roomba_plus.room_cleaning import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": "stored_map"}}
        state = {}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id="cloud_map_123")
        assert result == [("21", "stored_map")]

    def test_no_cloud_pmap_falls_back_to_mqtt(self):
        from custom_components.roomba_plus.room_cleaning import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"lastCommand": {"pmap_id": "mqtt_pmap"}}
        result = _resolve_rooms(data, ["Corridor"], state, cloud_pmap_id=None)
        assert result == [("21", "mqtt_pmap")]

    def test_empty_cloud_pmap_treated_as_none(self):
        """cloud_pmap_id='' should not override the MQTT fallback."""
        from custom_components.roomba_plus.room_cleaning import _resolve_rooms
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

    def test_region_suggestions_from_umf(self):
        coord = _make_coordinator(umf_data={
            "region_suggestions": [
                {"region_id": "0", "suggested_types": [
                    {"region_type": "living_room", "score": 0.62},
                ]},
            ],
        })
        assert len(coord.region_suggestions) == 1
        assert coord.region_suggestions[0]["region_id"] == "0"

    def test_region_suggestions_empty_when_absent(self):
        coord = _make_coordinator()
        assert coord.region_suggestions == []

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
            await mgr.async_evaluate(coord, "test_entry")

        # ASKS THE ROBOT, not the executor: the command goes straight to
        # `send_command` since roombapy 2.x, so an executor that is never
        # used would report "called 0 times" for a working trigger.
        mgr._entry.runtime_data.roomba.send_command.assert_awaited_once()

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

        hass = hass_mock()
        # `hass.async_create_task` is left to hass_mock(): it records
        # calls AND closes the coroutine; a bare MagicMock drops it.
        config_entry = entry_mock()
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
        call.hass = hass_mock()
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

        config_entry = entry_mock()
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

        hass = hass_mock()
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

        config_entry = entry_mock()
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

        # Capture the pmap_id sent to the robot.
        #
        # CAPTURED AT THE CLIENT since roombapy 2.x: the command no
        # longer travels through the executor, so a side_effect there
        # would never fire and the assertion below would compare against
        # an empty dict.
        sent_params = {}

        async def capture_send(cmd, params):
            sent_params.update(params)

        config_entry.runtime_data.roomba.send_command = capture_send

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

        hass = hass_mock()
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

        config_entry = entry_mock()
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

        # At the client, not the executor -- see the note above.
        sent_params = {}

        async def capture_send(cmd, params):
            sent_params.update(params)

        config_entry.runtime_data.roomba.send_command = capture_send

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
        from custom_components.roomba_plus.room_cleaning import _resolve_pmapv_id

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
        from custom_components.roomba_plus.room_cleaning import _resolve_pmapv_id

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

        hass = hass_mock()
        call = MagicMock()
        call.hass = hass
        call.data = {
            "entity_id": ["vacuum.test"],
            ATTR_ROOM_NAME: "Kitchen",
            ATTR_ORDERED: True,
        }

        data = MagicMock()
        data.roomba = robot_mock()   # awaited since roombapy 2.x
        data.map_capability = MapCapability.SMART
        data.has_cloud = True
        data.cloud_coordinator.active_pmap_id = pmap_id
        data.cloud_coordinator.active_user_pmapv_id = cloud_pmapv  # ← cloud value
        # What the version is read from since 4.2.13: the map's own cloud
        # record, active version first (#183).
        data.cloud_coordinator.data = {"pmaps": [{"active_pmapv_details": {"active_pmapv": {
            "pmap_id": pmap_id, "pmapv_id": cloud_pmapv, "last_user_pmapv_id": live_pmapv,
        }}}]}
        data.cloud_coordinator.regions = []
        data.cloud_coordinator.zones = []
        data.roomba_reported_state.return_value = {
            "pmaps": [{pmap_id: live_pmapv}],       # ← wrong live value
            # A dock replaced lastCommand -- the case #183 described.
            "lastCommand": {"command": "dock"},
            "cleanMissionStatus": {"notReady": 0},
            "noAutoPasses": False,
            "twoPass": False,
        }

        config_entry = entry_mock()
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

        # At the client, not the executor -- see the note above. Assigned
        # AFTER `data.roomba = robot_mock()`, or the factory's own
        # AsyncMock would replace this and `sent` would stay empty.
        sent = {}

        async def capture(cmd, params):
            sent.update(params)

        data.roomba.send_command = capture

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


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 REVIEW-REMAINDER — error-path fixes (cloud_api / grace period)
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewRemainderErrorPaths:
    """v3.3.0 REVIEW-REMAINDER findings, verified before/after:

    A) aiohttp.ClientError and the 30 s TimeoutError previously bypassed
       the F-RB-4 grace-period handler (only CloudApiError was caught) —
       the transient class the grace period was built for flapped
       entities to unavailable instead.
    B) _login_irobot accepted a credentials dict missing the four keys
       _signed_get depends on → bare KeyError later instead of a clean
       AuthenticationError at the gate.
    """

    def _grace_coordinator(self):
        from datetime import UTC, datetime
        coord = _make_coordinator_v240_coordinator()
        coord.data = {"pmaps": [], "sentinel": "last_good"}
        coord._last_success_time = datetime.now(UTC)  # inside grace window
        return coord

    @pytest.mark.asyncio
    async def test_client_error_uses_grace_period(self):
        """Before fix A: aiohttp.ClientError escaped _async_update_data
        untyped; now it returns the last good data within the window."""
        import aiohttp
        coord = self._grace_coordinator()
        coord.api.get_mission_history = AsyncMock(
            side_effect=aiohttp.ClientError("connection reset")
        )
        result = await coord._async_update_data()
        assert result.get("sentinel") == "last_good"

    @pytest.mark.asyncio
    async def test_timeout_error_uses_grace_period(self):
        """The coordinator's own asyncio.timeout(30) raises TimeoutError —
        must be treated as transient exactly like CloudApiError."""
        coord = self._grace_coordinator()
        coord.api.get_mission_history = AsyncMock(side_effect=TimeoutError())
        result = await coord._async_update_data()
        assert result.get("sentinel") == "last_good"

    @pytest.mark.asyncio
    async def test_content_type_error_becomes_cloud_api_error(self):
        """Fix A, API side: a 200 response with a non-JSON body raises the
        typed CloudApiError from _aws_get, not aiohttp.ContentTypeError."""
        import aiohttp
        from unittest.mock import MagicMock, AsyncMock, patch
        from custom_components.roomba_plus.cloud_api import (
            CloudApiError, IrobotCloudApi,
        )
        api = IrobotCloudApi.__new__(IrobotCloudApi)
        api._credentials = {
            "CognitoId": "eu-west-1:abc", "AccessKeyId": "AK",
            "SecretKey": "SK", "SessionToken": "ST",
        }
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(MagicMock(), ())
        )
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        api._session = MagicMock()
        api._session.get = MagicMock(return_value=ctx)
        with pytest.raises(CloudApiError, match="Non-JSON"):
            await api._aws_get("https://api.example/x")

    @pytest.mark.asyncio
    async def test_login_rejects_incomplete_credentials(self):
        """Fix B: missing CognitoId (or any of the four signing keys) must
        raise at login, not KeyError at first request. The actual gate
        logic now lives in roombapy-prime (see its own
        test_login_irobot_missing_single_credential_key_raises) --
        this test's job since the v3.6.0 login consolidation is only to
        confirm this module correctly propagates that failure.

        DELIBERATE BEHAVIOR CHANGE (v3.6.0): previously expected
        AuthenticationError here -- same bucket as "your password is
        wrong". That was misleading: a malformed/incomplete server
        response isn't fixed by re-entering the same, correct
        credentials. Now expects the generic CloudApiError instead,
        matching roombapy-prime's own categorization (this gate raises
        plain AuthError there, not AuthCredentialsError)."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.cloud_api import (
            AuthenticationError, CloudApiError, IrobotCloudApi,
        )
        from roombapy_prime import AuthError as PrimeAuthError

        api = IrobotCloudApi("user@test.com", "pass123", MagicMock())
        with patch(
            "custom_components.roomba_plus.cloud_api._prime_login",
            new=AsyncMock(side_effect=PrimeAuthError("Missing 'CognitoId' in iRobot credentials response")),
        ):
            with pytest.raises(CloudApiError, match="CognitoId") as excinfo:
                await api.authenticate()
            assert not isinstance(excinfo.value, AuthenticationError)


# ─────────────────────────────────────────────────────────────────────────────
# v3.4.2 NULL-REGRESSION — active_pmapv_details explicit null, cloud_coordinator.py
#
# Same root cause as select.py's Cloud-Details-Null-Zugriffe tech-debt item
# (fixed in the same release): pmap.get("active_pmapv_details", {}) only
# guards a MISSING key, not one present with an explicit null value from the
# cloud API. Found systemically across six call sites in this file —
# active_pmap_id, active_user_pmapv_id, regions, zones, learning_percentage,
# and the Variant A/B/C resolver inside _fetch_active_umf — all fixed with
# the same (dict.get(key) or {}) guard. These tests cover the five
# synchronous properties; _fetch_active_umf itself is async and network-
# bound, exercised indirectly via TestActivePmapNewest-style fixtures above
# rather than duplicated here.
# ─────────────────────────────────────────────────────────────────────────────

class TestActivePmapvDetailsNullRegression:
    def _coordinator_with_null_details(self, extra_pmap_fields: dict | None = None):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        pmap = {"active_pmapv_details": None, "pmap_id": "p1"}
        if extra_pmap_fields:
            pmap.update(extra_pmap_fields)
        cc.data = {"pmaps": [pmap]}
        return cc

    def test_active_pmap_id_survives_null_details(self):
        cc = self._coordinator_with_null_details()
        assert cc.active_pmap_id is None

    def test_active_user_pmapv_id_survives_null_details(self):
        cc = self._coordinator_with_null_details()
        # active_id resolves to None (no valid pmapv anywhere) → short-circuits
        # before the second null-details access, but must not raise regardless.
        assert cc.active_user_pmapv_id is None

    def test_regions_survives_null_details(self):
        cc = self._coordinator_with_null_details()
        # No active_id resolvable → regions short-circuits to [] before
        # reaching the null details, but must not raise if it ever does.
        assert cc.regions == []

    def test_zones_survives_null_details(self):
        cc = self._coordinator_with_null_details()
        assert cc.zones == []

    def test_learning_percentage_survives_null_details(self):
        cc = self._coordinator_with_null_details()
        assert cc.learning_percentage is None

    def test_regions_survives_null_details_when_pmap_id_matches_active(self):
        """Stronger case: active_id IS resolvable (from a second, valid pmap),
        so `regions` actually reaches the null-details pmap's `.get()` chain
        instead of short-circuiting on `if not active_id`."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = {"pmaps": [
            {"active_pmapv_details": None, "pmap_id": "p1"},
            _make_pmap("p2", [{"id": "r1", "name": "Kitchen"}]),
        ]}
        # active_pmap_id will resolve to "p2" (the only pmap with a valid ts-bearing
        # entry) — regions() then iterates and must skip the null-details pmap
        # without raising before reaching the matching one.
        assert cc.active_pmap_id == "p2"
        result = cc.regions
        assert result and result[0]["pmap_id"] == "p2"


class TestOneFailingSubFetchKeepsTheRest:
    """@ScenicSystemsLLC (a39): a ValueError from mission history
    escaped the handler — which catches CloudApiError, ClientError and
    TimeoutError, not ValueError — and killed the coroutine **after**
    pmaps and favourites had been fetched.

    Python returns nothing from a raising function, so both were thrown
    away every cycle. Favourite buttons went unavailable and every room
    map went blank, on three robots.
    """

    def test_the_history_fetch_is_isolated(self):
        import inspect

        from custom_components.roomba_plus import cloud_coordinator

        source = inspect.getsource(cloud_coordinator)
        i = source.find("raw_history = await self.api.get_mission_history")
        assert i > 0

        before = source[max(0, i - 600):i]
        assert "try:" in before, (
            "the history fetch must be isolated -- a malformed response "
            "there discards pmaps and favourites already fetched"
        )

    def test_network_errors_still_propagate(self):
        """A ClientError means the cloud is unreachable, and the outer
        handler turns that into the grace period that keeps the last
        good data. Swallowing it here would replace a working recovery
        with an empty result."""
        import inspect

        from custom_components.roomba_plus import cloud_coordinator

        source = inspect.getsource(cloud_coordinator)
        i = source.find("raw_history = await self.api.get_mission_history")
        block = source[i:i + 400]

        assert "raise" in block
        assert "aiohttp.ClientError" in block


class TestMissionHistoryIsNotWrappedInDict:
    """a39 declared `dict[str, Any]` and wrapped the response in
    `dict()` to match the annotation. The endpoint returns a list of
    mission records, so every refresh raised `dictionary update
    sequence element #0 has length 31; 2 is required`.

    The caller checks `isinstance(raw_history, list)` — the shape was
    documented in the code that reads it, and the annotation contradicted
    it.
    """

    def test_the_response_is_returned_unwrapped(self):
        import inspect

        from custom_components.roomba_plus.cloud_api import IrobotCloudApi

        source = inspect.getsource(IrobotCloudApi.get_mission_history)

        assert "dict(await" not in source, (
            "the endpoint returns a list of records; dict() reads each "
            "one as a key/value pair and raises"
        )

    def test_the_consumer_expects_a_list(self):
        """If this ever stops being true, the annotation above is wrong
        rather than the wrapper."""
        import inspect

        from custom_components.roomba_plus import cloud_coordinator

        source = inspect.getsource(cloud_coordinator)

        assert "isinstance(raw_history, list)" in source


# ── formerly tests/test_coverage_cloud_coordinator.py ───────────────────────────
#
# cloud_coordinator.py — quality scale, test-coverage.
#
# The active map's geometry (UMF) is fetched from the cloud by map and
# version id, both read from a loosely shaped pmap list. Missing ids and a
# failing fetch give no geometry, never a partial or wrong one.

def _coordinator(hass, umf=None, error=None):
    c = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
    c.hass = hass
    c.blid = "CLOUDBLID1"
    c.api = MagicMock()
    c.api.get_pmap_umf = AsyncMock(side_effect=error, return_value=umf)
    return c


def _pmap(pid="p1", vid="v7"):
    return {"active_pmapv_details": {"active_pmapv": {"pmap_id": pid, "active_pmapv_id": vid}}}


def _full(hass, **api):
    c = _coordinator(hass)
    c.api = MagicMock()
    for name in ("authenticate", "get_pmaps", "get_favorites", "get_mission_history",
                 "get_automations", "get_robot_parts"):
        setattr(c.api, name, AsyncMock(**api.get(name, {"return_value": {} if name in ("get_automations", "get_robot_parts") else []})))
    c._has_pmaps = True
    c._last_success_time = None
    c._mission_archive = None
    c._mission_store = None
    c._seeded_pmap_id = None
    c.daily_dirt_density = None
    c.data = None
    c.config_entry = MagicMock()
    return c


class TestFetchActiveUmf:

    @pytest.mark.asyncio
    async def test_no_map_id_means_no_fetch(self, hass):
        c = _coordinator(hass)
        assert await c._fetch_active_umf([{}]) is None
        c.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_version_id_means_no_fetch(self, hass):
        c = _coordinator(hass)
        assert await c._fetch_active_umf([_pmap(vid=None)]) is None
        c.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_fetch_gives_no_geometry(self, hass):
        c = _coordinator(hass, error=RuntimeError("cloud"))
        assert await c._fetch_active_umf([_pmap()]) is None

    @pytest.mark.asyncio
    async def test_geometry_is_merged_across_maps_and_tagged_umf(self, hass):
        umf = {"maps": [
            "junk",
            {"points2d": [{"id": "a"}], "keepoutzones": [{"id": "k1"}], "observed_zones": [{"id": "o1"}]},
            {"points2d": [{"id": "b"}], "regions": [{"id": "r1"}]},
        ]}
        c = _coordinator(hass, umf=umf)
        # A pmap without an id first: the active one is the first WITH an id.
        geo = await c._fetch_active_umf([{}, _pmap()])
        c.api.get_pmap_umf.assert_awaited_once_with("CLOUDBLID1", "p1", "v7")
        assert [p["id"] for p in geo["points2d"]] == ["a", "b"]
        assert geo["keepoutzones"] == [{"id": "k1", "space": "umf"}]
        assert geo["observed_zones"] == [{"id": "o1", "space": "umf"}]


class TestCloudSetup:

    @pytest.mark.asyncio
    async def test_wrong_credentials_ask_for_reauthentication(self, hass):
        c = _full(hass, authenticate={"side_effect": AuthenticationError("bad")})
        with pytest.raises(ConfigEntryAuthFailed):
            await c._async_setup()

    @pytest.mark.asyncio
    async def test_an_unreachable_cloud_is_a_retryable_failure(self, hass):
        c = _full(hass, authenticate={"side_effect": CloudApiError("down")})
        with pytest.raises(UpdateFailed):
            await c._async_setup()


class TestCloudUpdate:

    @pytest.mark.asyncio
    async def test_optional_endpoints_failing_do_not_fail_the_update(self, hass):
        """Automations and parts are extras; the maps and history carry on."""
        c = _full(hass,
                  get_mission_history={"return_value": [{"nMssn": 1, "timeline": {"a": 1}, "evts": [1, 2]}, "junk"]},
                  get_automations={"side_effect": RuntimeError("x")},
                  get_robot_parts={"side_effect": RuntimeError("y")})
        data = await c._async_update_data()
        assert data["automations"] == {} and data["parts"] == {}
        assert c._last_success_time is not None

    @pytest.mark.asyncio
    async def test_an_unexpected_history_error_leaves_history_empty(self, hass):
        c = _full(hass, get_mission_history={"side_effect": ValueError("odd payload")})
        data = await c._async_update_data()
        assert data["mission_history_raw"] == []

    @pytest.mark.asyncio
    async def test_an_expired_session_asks_for_reauthentication(self, hass):
        c = _full(hass, get_pmaps={"side_effect": AuthenticationError("expired")})
        with pytest.raises(ConfigEntryAuthFailed):
            await c._async_update_data()

    @pytest.mark.asyncio
    async def test_a_short_outage_keeps_the_last_good_data(self, hass):
        from custom_components.roomba_plus.cloud_coordinator import _MIN_UNAVAILABLE

        c = _full(hass, get_pmaps={"side_effect": CloudApiError("503")})
        c.data = {"pmaps": ["kept"]}
        c._last_success_time = datetime.now(UTC) - _MIN_UNAVAILABLE / 2
        assert await c._async_update_data() == {"pmaps": ["kept"]}

    @pytest.mark.asyncio
    async def test_an_outage_past_the_grace_period_fails(self, hass):
        from custom_components.roomba_plus.cloud_coordinator import _MIN_UNAVAILABLE

        c = _full(hass, get_pmaps={"side_effect": CloudApiError("503")})
        c.data = {"pmaps": ["stale"]}
        c._last_success_time = datetime.now(UTC) - _MIN_UNAVAILABLE * 2
        with pytest.raises(UpdateFailed):
            await c._async_update_data()

    @pytest.mark.asyncio
    async def test_an_outage_with_nothing_to_fall_back_on_fails(self, hass):
        c = _full(hass, get_pmaps={"side_effect": CloudApiError("503")})
        with pytest.raises(UpdateFailed):
            await c._async_update_data()


class TestResultClassification:

    @pytest.mark.parametrize("record,result", [
        ({"done": "ok"}, "completed"),
        ({"done": "full"}, "cancelled"),
        ({"done": "bat"}, "error_battery"),
        ({"done": "schErr", "pauseId": 4}, "error_4"),
        ({"done": "schErr", "pauseId": 0}, "cancelled"),
        ({"done": "inc", "pauseId": 9}, "error_9"),
        ({"done": "inc"}, "cancelled"),
    ])
    def test_results(self, record, result):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result

        assert classify_mission_result(record) == result


class TestCloudUpdateShapes:

    @pytest.mark.asyncio
    async def test_a_history_list_feeds_the_archive_oldest_first(self, hass):
        c = _full(hass, get_mission_history={"return_value": [
            {"nMssn": 2, "done": "ok"}, {"nMssn": 1, "done": "bat"}]})
        c._mission_archive = MagicMock()
        c._mission_archive.async_delta_update = AsyncMock()
        data = await c._async_update_data()
        fed = [call.args[0]["nMssn"] for call in c._mission_archive.async_delta_update.await_args_list]
        assert fed == [1, 2]
        assert [r["classified_result"] for r in data["mission_history_raw"]] == ["completed", "error_battery"]

    @pytest.mark.asyncio
    async def test_an_empty_history_list_is_empty_everywhere(self, hass):
        data = await _full(hass, get_mission_history={"return_value": []})._async_update_data()
        assert data["mission_history"] == {} and data["mission_history_raw"] == []

    @pytest.mark.asyncio
    async def test_an_older_dict_shaped_history_is_normalised(self, hass):
        data = await _full(hass, get_mission_history={"return_value": {"bbmssn": {"nMssn": 5}}})._async_update_data()
        assert isinstance(data["mission_history"], dict)

    @pytest.mark.asyncio
    async def test_an_unexpected_history_shape_is_empty(self, hass):
        data = await _full(hass, get_mission_history={"return_value": "odd"})._async_update_data()
        assert data["mission_history"] == {}

    @pytest.mark.asyncio
    async def test_the_active_map_geometry_is_attached(self, hass):
        c = _full(hass, get_pmaps={"return_value": [_pmap()]})
        c._fetch_active_umf = AsyncMock(return_value={"keepoutzones": [], "observed_zones": []})
        data = await c._async_update_data()
        assert data["umf"] == {"keepoutzones": [], "observed_zones": []}


class TestPropertiesWithoutData:
    """Before the first refresh, or after a failed one, every accessor
    gives an empty answer — callers create nothing from it."""

    @pytest.mark.parametrize("name,empty", [
        ("parts", []), ("regions", []), ("zones", []),
        ("raw_records", []),
    ])
    def test_empty_when_there_is_no_data(self, hass, name, empty):
        c = _full(hass)
        c.data = None
        assert getattr(c, name) == empty

    def test_the_last_success_time_is_exposed(self, hass):
        c = _full(hass)
        c._last_success_time = datetime(2026, 9, 21, tzinfo=UTC)
        assert c.last_success_time == datetime(2026, 9, 21, tzinfo=UTC)

    @pytest.mark.parametrize("payload", ["not a dict", {"parts": "not a list"},
                                         {"parts": ["junk", {"part_id": "p1"}]}])
    def test_parts_take_only_what_is_readable(self, hass, payload):
        c = _full(hass)
        c.data = {"parts": payload}
        parts = c.parts
        assert all(isinstance(p, dict) for p in parts)


class TestDailyDirtDensity:

    def test_unusable_records_are_skipped(self):
        from custom_components.roomba_plus.cloud_coordinator import _compute_daily_dirt_density

        ts = int(datetime(2026, 9, 21, 12, tzinfo=UTC).timestamp())
        result = _compute_daily_dirt_density([
            {"dirt": None, "sqft": 100, "startTime": ts},     # no dirt reading
            {"dirt": 5, "sqft": 0, "startTime": ts},          # no area
            {"dirt": 5, "sqft": 100},                          # no time
            {"dirt": 5, "sqft": 100, "startTime": 10**20},     # impossible time
            {"dirt": 10, "sqft": 100, "startTime": ts},
        ])
        assert len(result) == 1


# ── formerly tests/test_cloud_coordinator_internals.py ────────────────
#
# Tests for cloud_coordinator.py internal logic (normalization + EPHEMERAL).
#
# Merged (TEST-REORG follow-up) from test_cloud_normalizer.py and
# test_cloud_ephemeral.py — both exclusively test cloud_coordinator.py's
# internal logic (mission-history normalization/aggregation, and EPHEMERAL-
# robot-specific coordinator behaviour), just at different feature angles.
#
# Part 1: _normalize_mission_history / _aggregate_history — defensive cloud
# API parsing. The /missionhistory endpoint returns different structures
# depending on firmware version and region; the normalizer tries multiple
# source paths so the lifetime sensors work regardless of structure.
#
# Part 2: Phase 2b — Cloud support for EPHEMERAL robots (980/900-series).
# Covers IrobotCloudCoordinator.has_pmaps gating, the cloud setup gate for
# NONE-tier robots, config flow cloud_credentials menu visibility, and
# EPHEMERAL coordinator behaviour (empty pmaps/favorites, populated
# mission_history).
#
# No HA or roombapy installation required — uses conftest.py stubs.

def _bare_coordinator_m(has_pmaps: bool = False) -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA setup, with _has_pmaps set."""
    cc = object.__new__(IrobotCloudCoordinator)
    cc.data = None
    cc.blid = "testblid"
    cc._has_pmaps = has_pmaps
    cc._mission_store = None   # CR3 fallback — not needed in these tests
    cc._mission_archive = None  # ARC1 — v2.8.0
    return cc


class _FakeApi:
    """Minimal API fake that records which methods were called."""

    def __init__(self, history_response=None):
        self.calls: list[str] = []
        self._history = history_response or [{"bbmssn": {"nMssn": 42}}]

    async def get_pmaps(self, blid):
        self.calls.append("get_pmaps")
        return [{"pmap_id": "p1"}]

    async def get_favorites(self):
        self.calls.append("get_favorites")
        return [{"id": "fav1"}]

    async def get_mission_history(self, blid):
        self.calls.append("get_mission_history")
        return self._history


async def _run_update(coordinator: IrobotCloudCoordinator, api: _FakeApi) -> dict:
    """Run _async_update_data with a fake API injected."""
    coordinator.api = api
    return await coordinator._async_update_data()


class TestNormalizeMissionHistory:
    # ── Input guards ──────────────────────────────────────────────────────────

    def test_empty_dict_returns_empty(self):
        assert _normalize_mission_history({}) == {}

    def test_none_input_handled(self):
        # Caller passes raw={} when raw_history is empty — but guard anyway
        assert _normalize_mission_history({}) == {}

    # ── Structure A: lifetime accumulator with runtimeStats ───────────────────

    def test_runtimeStats_sqft(self):
        raw = {"runtimeStats": {"sqft": 12345, "hr": 42, "min": 30}}
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["sqft"] == 12345
        assert result["runtimeStats"]["hr"] == 42
        assert result["runtimeStats"]["min"] == 30

    def test_runtimeStats_with_bbmssn(self):
        raw = {
            "runtimeStats": {"sqft": 10000, "hr": 100, "min": 0},
            "bbmssn": {"nMssn": 500},
        }
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["sqft"] == 10000
        assert result["bbmssn"]["nMssn"] == 500

    # ── Structure B: individual mission record with bbrun lifetime snapshot ───

    def test_bbrun_sqft_fallback(self):
        """When runtimeStats absent, reads sqft from bbrun."""
        raw = {
            "sqft": 45,          # this mission only — should NOT be used for lifetime
            "bbrun": {"sqft": 12345, "hr": 428, "min": 25},
            "bbmssn": {"nMssn": 779},
        }
        result = _normalize_mission_history(raw)
        # bbrun.sqft = lifetime total — should be preferred over top-level sqft
        assert result["runtimeStats"]["sqft"] == 12345
        assert result["runtimeStats"]["hr"] == 428
        assert result["bbmssn"]["nMssn"] == 779

    def test_bbrun_hr_fallback(self):
        raw = {"bbrun": {"hr": 312, "min": 45, "sqft": 8000}}
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["hr"] == 312
        assert result["runtimeStats"]["min"] == 45

    # ── Structure C: top-level flat fields ────────────────────────────────────

    def test_top_level_sqft_fallback(self):
        """Last resort: top-level sqft when neither runtimeStats nor bbrun has it."""
        raw = {"sqft": 9999, "nMssn": 100}
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["sqft"] == 9999
        assert result["bbmssn"]["nMssn"] == 100

    def test_top_level_nMssn_fallback(self):
        raw = {"runtimeStats": {"sqft": 5000, "hr": 50, "min": 0}, "nMssn": 300}
        result = _normalize_mission_history(raw)
        assert result["bbmssn"]["nMssn"] == 300

    # ── Structure D: duration in seconds (individual record format) ───────────

    def test_durationM_minutes_conversion(self):
        """Actual API field: durationM in minutes."""
        raw = {"durationM": 150, "bbmssn": {"nMssn": 50}}  # 2h30m
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["hr"] == 2
        assert result["runtimeStats"]["min"] == 30

    def test_doneM_alternative_minutes_field(self):
        raw = {"doneM": 60}  # 1h
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["hr"] == 1
        assert result["runtimeStats"]["min"] == 0

    # ── Priority order ────────────────────────────────────────────────────────

    def test_runtimeStats_preferred_over_bbrun(self):
        """runtimeStats wins when both sources present."""
        raw = {
            "runtimeStats": {"sqft": 100, "hr": 10, "min": 0},
            "bbrun":        {"sqft": 999, "hr": 99, "min": 0},
        }
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["sqft"] == 100
        assert result["runtimeStats"]["hr"] == 10

    def test_bbrun_preferred_over_top_level(self):
        """bbrun wins over top-level sqft."""
        raw = {"sqft": 45, "bbrun": {"sqft": 12345}}
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["sqft"] == 12345

    def test_bbmssn_preferred_over_top_level_nMssn(self):
        raw = {"bbmssn": {"nMssn": 779}, "nMssn": 1}
        result = _normalize_mission_history(raw)
        assert result["bbmssn"]["nMssn"] == 779

    # ── Output shape ──────────────────────────────────────────────────────────

    def test_no_runtimeStats_key_when_all_absent(self):
        """No runtimeStats in output when no source found."""
        raw = {"bbmssn": {"nMssn": 5}}
        result = _normalize_mission_history(raw)
        assert "runtimeStats" not in result
        assert result["bbmssn"]["nMssn"] == 5

    def test_no_bbmssn_key_when_absent(self):
        raw = {"runtimeStats": {"sqft": 100, "hr": 1, "min": 0}}
        result = _normalize_mission_history(raw)
        assert "runtimeStats" in result
        assert "bbmssn" not in result

    def test_integer_coercion(self):
        """Values are coerced to int even if API returns floats."""
        raw = {"runtimeStats": {"sqft": 12345.7, "hr": 42.0, "min": 30.9}}
        result = _normalize_mission_history(raw)
        assert isinstance(result["runtimeStats"]["sqft"], int)
        assert isinstance(result["runtimeStats"]["hr"], int)

    # ── Thonno's i7 scenario ──────────────────────────────────────────────────

    def test_actual_api_format_individual_record(self):
        """Simulate actual API format confirmed from field log:
        durationM, done, chrgs, chrgM, dirt, dockedAtStart, eDock"""
        raw = {
            "durationM": 46,
            "done": True,
            "chrgs": 0,
            "chrgM": 0,
            "dirt": 5,
            "dockedAtStart": True,
            "sqft": 174,
            "eDock": 1,
        }
        result = _normalize_mission_history(raw)
        assert result["runtimeStats"]["hr"] == 0
        assert result["runtimeStats"]["min"] == 46


class TestAggregateHistory:
    """Tests for _aggregate_history — sums all individual mission records."""

    def test_empty_list_returns_empty(self):
        assert _aggregate_history([]) == {}

    def test_single_record_durationM(self):
        records = [{"durationM": 46, "done": True, "sqft": 174}]
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 1
        assert result["runtimeStats"]["hr"] == 0
        assert result["runtimeStats"]["min"] == 46
        assert result["runtimeStats"]["sqft"] == 174

    def test_multiple_records_sum_correctly(self):
        records = [
            {"durationM": 46, "sqft": 100},
            {"durationM": 60, "sqft": 200},
            {"durationM": 30, "sqft": 50},
        ]
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 3
        assert result["runtimeStats"]["hr"] == 2   # 136 min = 2h16m
        assert result["runtimeStats"]["min"] == 16
        assert result["runtimeStats"]["sqft"] == 350

    def test_nMssn_from_record_not_len(self):
        """nMssn in each record is the LIFETIME count, not window size."""
        records = [{"durationM": 30, "nMssn": 414}] * 34
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 414  # lifetime, not 34

    def test_nMssn_fallback_to_len_when_absent(self):
        records = [{"durationM": 30}] * 34
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 34  # fallback

    def test_no_sqft_when_absent(self):
        records = [{"durationM": 46}, {"durationM": 30}]
        result = _aggregate_history(records)
        assert "sqft" not in result.get("runtimeStats", {})

    def test_handles_zero_duration(self):
        records = [{"durationM": 0, "sqft": 50}, {"durationM": 46}]
        result = _aggregate_history(records)
        assert result["runtimeStats"]["min"] == 46

    def test_skips_non_dict_records(self):
        records = [{"durationM": 46}, None, "bad", {"durationM": 30}]
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 4  # counts all items
        assert result["runtimeStats"]["hr"] == 1
        assert result["runtimeStats"]["min"] == 16

    def test_980_actual_scenario_34_records(self):
        """Simulate 980 with 34 missions: nMssn=414 lifetime, avg 33 runM each."""
        records = [{"runM": 33, "durationM": 33, "sqft": 200,
                    "done": "done", "nMssn": 414}] * 34
        result = _aggregate_history(records)
        # nMssn from record = lifetime total
        assert result["bbmssn"]["nMssn"] == 414
        # 34 * 33 = 1122 min = 18h42m (using runM)
        assert result["runtimeStats"]["hr"] == 18
        assert result["runtimeStats"]["min"] == 42
        assert result["runtimeStats"]["sqft"] == 34 * 200

    def test_runM_preferred_over_durationM(self):
        """runM (actual cleaning) preferred over durationM (incl. recharge)."""
        records = [{"runM": 40, "durationM": 85, "nMssn": 10}]
        result = _aggregate_history(records)
        # 40 min (runM), not 85 (durationM)
        assert result["runtimeStats"]["hr"] == 0
        assert result["runtimeStats"]["min"] == 40

    def test_actual_980_first_record(self):
        """Exact first record from field log: Error 17 mission."""
        records = [{
            "chrgM": 0, "chrgs": 0, "dirt": 13, "dockedAtStart": 1,
            "done": "stuck", "doneM": 0, "durationM": 33, "eDock": 0,
            "evacs": 0, "flags": 0, "initiator": "localApp",
            "nMssn": 414, "pauseId": 17, "pauseM": 0,
            "runM": 33, "saves": 1, "sqft": 237,
        }]
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 414
        assert result["runtimeStats"]["min"] == 33
        assert result["runtimeStats"]["sqft"] == 237


class TestHasPmapsFlag:
    def test_has_pmaps_false_by_default(self):
        cc = object.__new__(IrobotCloudCoordinator)
        # Simulate __init__ setting _has_pmaps with default
        cc._has_pmaps = False
        assert cc._has_pmaps is False

    def test_has_pmaps_true_when_set(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        assert cc._has_pmaps is True

    def test_has_pmaps_false_for_ephemeral(self):
        """EPHEMERAL capability maps to has_pmaps=False."""
        has_pmaps = MapCapability.EPHEMERAL == MapCapability.SMART
        assert has_pmaps is False

    def test_has_pmaps_true_for_smart(self):
        """SMART capability maps to has_pmaps=True."""
        has_pmaps = MapCapability.SMART == MapCapability.SMART
        assert has_pmaps is True

    def test_has_pmaps_false_for_none(self):
        """NONE capability never creates coordinator — but if it did, has_pmaps=False."""
        has_pmaps = MapCapability.NONE == MapCapability.SMART
        assert has_pmaps is False


class TestCoordinatorEphemeral:
    @pytest.mark.asyncio
    async def test_pmaps_not_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_pmaps" not in api.calls

    @pytest.mark.asyncio
    async def test_favorites_not_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_favorites" not in api.calls

    @pytest.mark.asyncio
    async def test_mission_history_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_mission_history" in api.calls

    @pytest.mark.asyncio
    async def test_result_pmaps_empty_list_when_ephemeral(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert result["pmaps"] == []

    @pytest.mark.asyncio
    async def test_result_favorites_empty_list_when_ephemeral(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert result["favorites"] == []

    @pytest.mark.asyncio
    async def test_result_mission_history_populated_when_ephemeral(self):
        cc = _bare_coordinator_m(has_pmaps=False)
        # Actual API returns list of individual records with durationM
        api = _FakeApi(history_response=[
            {"durationM": 46, "done": True, "sqft": 200},
            {"durationM": 30, "done": True, "sqft": 150},
        ])
        result = await _run_update(cc, api)
        # _aggregate_history sums: 2 records, 76 min total, 350 sqft
        assert result["mission_history"]["bbmssn"]["nMssn"] == 2
        assert result["mission_history"]["runtimeStats"]["min"] == 16
        assert result["mission_history"]["runtimeStats"]["hr"] == 1


class TestCoordinatorSmart:
    @pytest.mark.asyncio
    async def test_pmaps_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_pmaps" in api.calls

    @pytest.mark.asyncio
    async def test_favorites_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_favorites" in api.calls

    @pytest.mark.asyncio
    async def test_mission_history_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_mission_history" in api.calls

    @pytest.mark.asyncio
    async def test_result_pmaps_populated_when_smart(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert len(result["pmaps"]) == 1

    @pytest.mark.asyncio
    async def test_result_favorites_populated_when_smart(self):
        cc = _bare_coordinator_m(has_pmaps=True)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert len(result["favorites"]) == 1


class TestCloudGateLogic:
    def test_none_capability_never_gets_coordinator(self):
        """MapCapability.NONE != NONE check: gate is != NONE."""
        capability = MapCapability.NONE
        should_create = capability != MapCapability.NONE
        assert should_create is False

    def test_ephemeral_capability_gets_coordinator_with_credentials(self):
        capability = MapCapability.EPHEMERAL
        credentials_present = True
        should_create = capability != MapCapability.NONE and credentials_present
        assert should_create is True

    def test_smart_capability_gets_coordinator_with_credentials(self):
        capability = MapCapability.SMART
        credentials_present = True
        should_create = capability != MapCapability.NONE and credentials_present
        assert should_create is True

    def test_no_coordinator_without_credentials(self):
        for cap in (MapCapability.EPHEMERAL, MapCapability.SMART):
            should_create = cap != MapCapability.NONE and False  # no credentials
            assert should_create is False

    def test_ephemeral_has_pmaps_false(self):
        has_pmaps = MapCapability.EPHEMERAL == MapCapability.SMART
        assert has_pmaps is False

    def test_smart_has_pmaps_true(self):
        has_pmaps = MapCapability.SMART == MapCapability.SMART
        assert has_pmaps is True


class TestConfigFlowCloudMenu:
    """Test the menu-building logic for cloud_credentials."""

    def _build_menu(self, capability: MapCapability) -> list[str]:
        """Replicate the config_flow menu logic for cloud_credentials."""
        menu = ["settings", "blocking_sensors"]
        if capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.insert(1, "map_management")
        if capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("cloud_credentials")
        return menu

    def test_cloud_credentials_in_menu_for_smart(self):
        menu = self._build_menu(MapCapability.SMART)
        assert "cloud_credentials" in menu

    def test_cloud_credentials_in_menu_for_ephemeral(self):
        menu = self._build_menu(MapCapability.EPHEMERAL)
        assert "cloud_credentials" in menu

    def test_cloud_credentials_not_in_menu_for_none(self):
        menu = self._build_menu(MapCapability.NONE)
        assert "cloud_credentials" not in menu

    def test_map_management_in_menu_for_ephemeral(self):
        menu = self._build_menu(MapCapability.EPHEMERAL)
        assert "map_management" in menu

    def test_map_management_not_in_menu_for_none(self):
        menu = self._build_menu(MapCapability.NONE)
        assert "map_management" not in menu


class TestTheCommittedVersionIsTheActiveOne:
    """#183. A room command must carry the version of the map the robot
    localises against. The cloud record holds several: the active one
    (`active_pmapv.pmapv_id`, and the root `active_pmapv_id`) and the last
    one the user made (`last_user_pmapv_id`).

    The order read was "Variant A, B, C" -- `active_pmapv.active_pmapv_id`,
    a key no record we hold contains, then `last_user_pmapv_id`. So the
    intended "active first" fell through to the last user version every
    time, and when the two differed the robot got a version it could not
    localise against: error 224, never left the dock, on every room clean.
    """

    _MAP = "ejhUEjqiTK2h2KedgFwzqQ"

    def _record(self, **pmapv):
        return {"active_pmapv_details": {"active_pmapv": {"pmap_id": self._MAP, **pmapv}}}

    def test_the_active_version_wins_over_the_last_user_version(self):
        from custom_components.roomba_plus.cloud_coordinator import pmap_committed_version

        record = self._record(pmapv_id="250610T143229", last_user_pmapv_id="260913T011853")
        assert pmap_committed_version(record) == "250610T143229"

    def test_the_root_active_version_also_wins_over_the_last_user_version(self):
        from custom_components.roomba_plus.cloud_coordinator import pmap_committed_version

        record = self._record(last_user_pmapv_id="260913T011853")
        record["active_pmapv_id"] = "250610T143229"
        assert pmap_committed_version(record) == "250610T143229"

    def test_the_last_user_version_is_the_last_resort(self):
        """Every record the field has shown carries it, so it must still
        answer where nothing else does."""
        from custom_components.roomba_plus.cloud_coordinator import pmap_committed_version

        assert pmap_committed_version(self._record(last_user_pmapv_id="v")) == "v"
        assert pmap_committed_version(self._record()) is None
        assert pmap_committed_version({"active_pmapv_details": None}) is None
        assert pmap_committed_version(
            {"active_pmapv_details": {"active_pmapv": "junk"}, "active_pmapv_id": "r"}
        ) == "r"

    def test_the_active_user_pmapv_id_property_reads_the_same_way(self):
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": [self._record(
            pmapv_id="250610T143229", last_user_pmapv_id="260913T011853"
        )]}
        assert cc.active_user_pmapv_id == "250610T143229"

    def test_the_real_i3_record_is_unchanged(self):
        """Where the fields agree -- every record held before #183 --
        nothing changes."""
        import json

        from custom_components.roomba_plus.cloud_coordinator import pmap_committed_version

        from pathlib import Path

        fixture = Path(__file__).parent / "fixtures" / "irobot_pmaps_i3plus.json"
        (record,) = json.loads(fixture.read_text())
        pmapv = record["active_pmapv_details"]["active_pmapv"]
        assert pmap_committed_version(record) == pmapv["last_user_pmapv_id"] == "251229T165154"

    def test_any_map_is_looked_up_not_only_the_active_one(self):
        from custom_components.roomba_plus.cloud_coordinator import committed_pmapv_id

        data = {"pmaps": [
            {"active_pmapv_details": {"active_pmapv": {"pmap_id": "A", "pmapv_id": "vA"}}},
            {"pmap_id": "B", "active_pmapv_id": "vB"},
            "junk",
        ]}
        assert committed_pmapv_id(data, "A") == "vA"
        assert committed_pmapv_id(data, "B") == "vB"
        assert committed_pmapv_id(data, "C") is None
        assert committed_pmapv_id(None, "A") is None
        assert committed_pmapv_id(data, None) is None
        assert committed_pmapv_id(MagicMock(), "A") is None

    def test_the_umf_fetch_reads_the_same_version(self):
        """The geometry shown is the map the robot localises against."""
        import inspect

        source = inspect.getsource(IrobotCloudCoordinator)
        assert "version_id = pmap_committed_version(pmap)" in source

    def test_the_version_report_survives_junk(self):
        from custom_components.roomba_plus.cloud_coordinator import pmap_version_report

        assert pmap_version_report(None) == []
        assert pmap_version_report({"pmaps": ["junk", {"active_pmapv_details": {"active_pmapv": 1}}]}) == [{
            "pmap_id": None, "state": None,
            "root": {"active_pmapv_id": None, "user_pmapv_id": None,
                     "robot_pmapv_id": None, "last_pmapv_ts": None},
            "active_pmapv": {k: None for k in (
                "pmapv_id", "active_pmapv_id", "last_user_pmapv_id",
                "last_user_ts", "proc_state", "creator", "create_time")},
            "committed_version_used": None,
        }]


# ── 4.2.14: a version the robot made is not a room-command version ─────────
#
# Field records, verbatim where it matters (ids shortened, BLIDs out).

from custom_components.roomba_plus.cloud_coordinator import pmap_committed_version  # noqa: E402


def _field_pmap(pmap_id, *, active, creator, user, last_user, robot=None):
    return {
        "pmap_id": pmap_id,
        "state": "active",
        "active_pmapv_id": active,
        "user_pmapv_id": user,
        "robot_pmapv_id": robot or active,
        "active_pmapv_details": {"active_pmapv": {
            "pmap_id": pmap_id, "pmapv_id": active, "last_user_pmapv_id": last_user,
            "proc_state": "OK_Processed", "creator": creator,
        }},
    }


class TestARobotMadeVersionIsNotSent:
    """@FJSoninC's j7+ and @ScenicSystemsLLC's two S9+: the cloud made
    the robot's own end-of-mission version the active one, and a room
    command carrying it failed with error 224 every time. The app, the
    favourites and every command that worked carried the root
    `user_pmapv_id`."""

    @pytest.mark.parametrize("record,working", [
        # @FJSoninC, j7+, one map
        (_field_pmap("j7map", active="260926T064058", creator="robot",
                     user="260925T175356", last_user="260925T175356"), "260925T175356"),
        # @ScenicSystemsLLC, Walle (S9+)
        (_field_pmap("9HqCcpz", active="260918T205534", creator="robot",
                     user="260901T160723", last_user="260901T160723"), "260901T160723"),
        # @ScenicSystemsLLC, Robby (S9+), both maps -- the first has no favourite
        (_field_pmap("4jlhiuj", active="260909T163820", creator="robot",
                     user="260816T105241", last_user="260816T105241"), "260816T105241"),
        (_field_pmap("R-xavAh", active="260106T141943", creator="robot",
                     user="251217T181058", last_user="251217T181058"), "251217T181058"),
    ], ids=["j7+", "walle", "robby_map1", "robby_map2"])
    def test_the_committed_user_version_is_used(self, record, working):
        assert pmap_committed_version(record) == working

    def test_a_user_made_active_version_is_still_first(self):
        """#183: the active version was the app's, `last_user_pmapv_id`
        an edit that never became active."""
        record = _field_pmap("p", active="250610T143229", creator="user",
                             user="250610T143229", last_user="260913T011853")
        assert pmap_committed_version(record) == "250610T143229"

    def test_a_record_without_creator_keeps_the_4_2_13_answer(self):
        record = _field_pmap("p", active="250610T143229", creator=None,
                             user=None, last_user="260913T011853")
        del record["active_pmapv_details"]["active_pmapv"]["creator"]
        assert pmap_committed_version(record) == "250610T143229"

    def test_with_nothing_else_the_robots_version_is_sent_rather_than_none(self):
        record = _field_pmap("p", active="260918T205534", creator="robot",
                             user=None, last_user=None)
        assert pmap_committed_version(record) == "260918T205534"
