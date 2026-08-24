"""Tests for the iRobot cloud consumable-parts feature (i3+ migration path).

Covers /v1/robots/{blid}/parts end to end:
  - cloud_api.IrobotCloudApi.get_robot_parts
  - cloud_coordinator.IrobotCloudCoordinator.parts / _async_update_data
  - const.IROBOT_PART_ROLES / IROBOT_PART_ROLE_TO_STORE_SLOT / part_role
  - maintenance_store.MaintenanceStore.hydrate_from_cloud_parts / _iso_from_epoch
    and persistence of the new cloud_parts / cloud_parts_hydrated_at fields
  - sensor_cloud.CloudPartSensor / build_cloud_part_sensors

Uses the real, live-captured /v1/robots/{blid}/parts response for an i3+
(sku i355640, firmware daredevil+2.6.0) in tests/fixtures/irobot_parts_i3plus.json.
That robot has no `pose` key in `cap` (const.has_pose() is False) but does have
persistent maps (const.has_smart_map() is True) -- both features under test are
gated on exactly that distinction, though the gate itself lives in sensor.py's
async_setup_entry, not in the units tested here.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.cloud_api import IrobotCloudApi
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.const import (
    IROBOT_PART_ROLE_CLEAN_BASE_BAG,
    IROBOT_PART_ROLE_FILTER,
    IROBOT_PART_ROLE_MAIN_BRUSH,
    IROBOT_PART_ROLE_SIDE_BRUSH,
    part_role,
)
from custom_components.roomba_plus.maintenance_store import MaintenanceStore, _iso_from_epoch
from custom_components.roomba_plus.sensor_cloud import CloudPartSensor, build_cloud_part_sensors


_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "irobot_parts_i3plus.json"


def _parts_payload() -> dict:
    return json.loads(_FIXTURE.read_text())


_PARTS_PAYLOAD = _parts_payload()
_PARTS = _PARTS_PAYLOAD["parts"]


def _part(part_id: str) -> dict:
    """Return the fixture record for one part_id, unmodified."""
    return next(p for p in _PARTS if p["part_id"] == part_id)


def _make_roomba() -> MagicMock:
    r = MagicMock()
    r.master_state = {"state": {"reported": {}}}
    return r


def _make_coordinator(parts: list[dict]) -> MagicMock:
    """Build a coordinator double shaped like a healthy, refreshed coordinator."""
    coordinator = MagicMock()
    coordinator.parts = parts
    coordinator.last_update_success = True
    coordinator.data = {"parts": {"parts": parts}}
    return coordinator


# ─────────────────────────────────────────────────────────────────────────────
# cloud_api.IrobotCloudApi.get_robot_parts
# ─────────────────────────────────────────────────────────────────────────────


class TestGetRobotParts:
    def _authed_api(self) -> IrobotCloudApi:
        api = IrobotCloudApi("user@example.com", "password", MagicMock())
        api._deployment = {"httpBaseAuth": "https://auth.example.com"}
        api._credentials = {
            "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "SecretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "token",
            "CognitoId": "us-east-1:abc123",
        }
        return api

    @pytest.mark.asyncio
    async def test_builds_exact_url_and_returns_parsed_dict(self):
        api = self._authed_api()
        with patch.object(
            api, "_aws_get", new=AsyncMock(return_value=_PARTS_PAYLOAD)
        ) as mock_get:
            result = await api.get_robot_parts("blid_test")

        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        assert url == "https://auth.example.com/v1/robots/blid_test/parts"
        assert result == _PARTS_PAYLOAD

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_response", [[], None, "oops"])
    async def test_returns_empty_dict_for_non_dict_response(self, bad_response):
        api = self._authed_api()
        with patch.object(api, "_aws_get", new=AsyncMock(return_value=bad_response)):
            result = await api.get_robot_parts("blid_test")
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# cloud_coordinator.IrobotCloudCoordinator.parts
# ─────────────────────────────────────────────────────────────────────────────


class TestCoordinatorPartsProperty:
    def _coord(self, data) -> IrobotCloudCoordinator:
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = data
        return cc

    def test_returns_the_four_fixture_parts(self):
        cc = self._coord({"parts": {"parts": _PARTS}})
        assert cc.parts == _PARTS
        assert len(cc.parts) == 4

    def test_empty_when_data_is_none(self):
        cc = self._coord(None)
        assert cc.parts == []

    def test_empty_when_parts_key_missing(self):
        cc = self._coord({"pmaps": []})
        assert cc.parts == []

    def test_empty_when_parts_payload_not_a_dict(self):
        cc = self._coord({"parts": ["not", "a", "dict"]})
        assert cc.parts == []

    def test_empty_when_inner_parts_not_a_list(self):
        cc = self._coord({"parts": {"parts": "not-a-list"}})
        assert cc.parts == []

    def test_filters_entries_lacking_part_id(self):
        parts = list(_PARTS) + [{"counter": 5, "count_type": "minutes"}]
        cc = self._coord({"parts": {"parts": parts}})
        assert cc.parts == _PARTS


class TestCoordinatorUpdateToleratesPartsFailure:
    """One failing sub-fetch (the parts endpoint) must not discard the rest
    of that cycle's result -- same reasoning already proven for mission
    history in TestOneFailingSubFetchKeepsTheRest (test_cloud_coordinator.py)."""

    class _FakeApi:
        async def get_mission_history(self, blid):
            return [{"done": "done", "nMssn": 5, "sqft": 100, "runM": 10}]

        async def get_robot_parts(self, blid):
            raise RuntimeError("parts endpoint unavailable for this robot")

    def _bare_coordinator(self) -> IrobotCloudCoordinator:
        cc = object.__new__(IrobotCloudCoordinator)
        cc.data = None
        cc.blid = "testblid"
        cc._has_pmaps = False
        cc._mission_store = None
        cc._mission_archive = None
        return cc

    @pytest.mark.asyncio
    async def test_parts_ends_up_empty_but_other_keys_survive(self):
        cc = self._bare_coordinator()
        cc.api = self._FakeApi()

        result = await cc._async_update_data()

        assert result["parts"] == {}
        assert result["mission_history"]["bbmssn"]["nMssn"] == 5
        assert result["mission_history_raw"][0]["classified_result"] == "completed"
        assert result["pmaps"] == []
        assert result["favorites"] == []


# ─────────────────────────────────────────────────────────────────────────────
# const.part_role / IROBOT_PART_ROLES
# ─────────────────────────────────────────────────────────────────────────────


class TestPartRole:
    @pytest.mark.parametrize(
        "part_id,expected_role",
        [
            ("35", IROBOT_PART_ROLE_FILTER),
            ("36", IROBOT_PART_ROLE_SIDE_BRUSH),
            ("37", IROBOT_PART_ROLE_MAIN_BRUSH),
            ("139", IROBOT_PART_ROLE_CLEAN_BASE_BAG),
        ],
    )
    def test_known_ids_resolve_to_their_role(self, part_id, expected_role):
        assert part_role(part_id) == expected_role

    def test_unknown_id_returns_none(self):
        assert part_role("999") is None

    def test_none_returns_none(self):
        assert part_role(None) is None

    def test_int_typed_id_still_matches(self):
        assert part_role(35) == IROBOT_PART_ROLE_FILTER

    def test_whitespace_padded_id_is_normalised(self):
        assert part_role(" 35 ") == IROBOT_PART_ROLE_FILTER


# ─────────────────────────────────────────────────────────────────────────────
# maintenance_store._iso_from_epoch
# ─────────────────────────────────────────────────────────────────────────────


class TestIsoFromEpoch:
    def test_valid_epoch_round_trips(self):
        ts = _part("35")["last_updated_ts"]  # 1780665041
        iso = _iso_from_epoch(ts)
        assert iso is not None
        parsed = datetime.datetime.fromisoformat(iso)
        assert int(parsed.timestamp()) == ts

    @pytest.mark.parametrize("bad", [None, "abc", 0, -5])
    def test_invalid_values_return_none(self, bad):
        assert _iso_from_epoch(bad) is None


# ─────────────────────────────────────────────────────────────────────────────
# maintenance_store.MaintenanceStore.hydrate_from_cloud_parts
# ─────────────────────────────────────────────────────────────────────────────


class TestHydrateFromCloudPartsWithFixture:
    """current_hr=300h against the real 4-part i3+ fixture."""

    def test_first_call_returns_true_and_records_all_four_parts_with_roles(self):
        store = MaintenanceStore()
        changed = store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        assert changed is True
        assert set(store.cloud_parts.keys()) == {"139", "35", "36", "37"}
        assert store.cloud_parts["35"]["role"] == IROBOT_PART_ROLE_FILTER
        assert store.cloud_parts["36"]["role"] == IROBOT_PART_ROLE_SIDE_BRUSH
        assert store.cloud_parts["37"]["role"] == IROBOT_PART_ROLE_MAIN_BRUSH
        assert store.cloud_parts["139"]["role"] == IROBOT_PART_ROLE_CLEAN_BASE_BAG

    def test_filter_and_brush_reset_hr_computed_from_count_used(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        # part 35 (filter): count_used=1344 -> 1344 // 60 = 22h used
        assert store.filter_reset_hr == 300 - 1344 // 60
        # part 37 (main_brush -> "brush" slot): count_used=17662 -> 294h used
        assert store.brush_reset_hr == 300 - 17662 // 60
        assert store.filter_reset_hr == 278
        assert store.brush_reset_hr == 6

    def test_filter_reset_at_set_but_brush_reset_at_left_untouched(self):
        store = MaintenanceStore()
        assert store.brush_reset_at is None  # sanity: default before hydration
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        # part 35 carries a last_updated_ts -> filter_reset_at gets stamped.
        assert store.filter_reset_at is not None
        # part 37 (main_brush) carries NO last_updated_ts in the fixture ->
        # the brush slot's *_reset_at must be left exactly as it was.
        assert store.brush_reset_at is None

    def test_side_brush_and_clean_base_bag_write_no_legacy_slot(self):
        """Only filter/main_brush map onto legacy store slots; side_brush
        and clean_base_bag have no such slot at all."""
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        assert not hasattr(store, "side_brush_reset_hr")
        assert not hasattr(store, "clean_base_bag_reset_hr")
        # The only two legacy hour slots that exist reflect filter/main_brush only.
        assert store.filter_reset_hr == 278
        assert store.brush_reset_hr == 6

    def test_reset_history_lists_remain_empty(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        assert store.filter_reset_history == []
        assert store.brush_reset_history == []

    def test_baseline_seeded_flags_become_true(self):
        store = MaintenanceStore()
        assert store.filter_baseline_seeded is False
        assert store.brush_baseline_seeded is False
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        assert store.filter_baseline_seeded is True
        assert store.brush_baseline_seeded is True

    def test_idempotent_second_identical_call_returns_false(self):
        store = MaintenanceStore()
        assert store.hydrate_from_cloud_parts(_PARTS, current_hr=300) is True
        assert store.hydrate_from_cloud_parts(_PARTS, current_hr=300) is False

    def test_cloud_parts_hydrated_at_gets_stamped(self):
        store = MaintenanceStore()
        assert store.cloud_parts_hydrated_at is None
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)
        assert store.cloud_parts_hydrated_at is not None


class TestHydrateFromCloudPartsEdgeCases:
    def test_non_minutes_count_type_recorded_but_slot_untouched(self):
        """A hypothetical non-minutes counter on a filter-role id must still
        be recorded verbatim in cloud_parts, but must not write filter_reset_hr."""
        store = MaintenanceStore()
        odd_part = dict(_part("35"))
        odd_part["count_type"] = "missions"

        changed = store.hydrate_from_cloud_parts([odd_part], current_hr=300)

        assert changed is True
        assert store.cloud_parts["35"]["role"] == IROBOT_PART_ROLE_FILTER
        assert store.cloud_parts["35"]["count_type"] == "missions"
        assert store.filter_reset_hr == 0
        assert store.filter_baseline_seeded is False

    def test_non_numeric_count_used_recorded_without_raising(self):
        store = MaintenanceStore()
        odd_part = dict(_part("35"))
        odd_part["count_used"] = "not-a-number"

        changed = store.hydrate_from_cloud_parts([odd_part], current_hr=300)  # must not raise

        assert changed is True
        assert store.cloud_parts["35"]["count_used"] == "not-a-number"
        assert store.filter_reset_hr == 0
        assert store.filter_baseline_seeded is False

    def test_absent_count_used_recorded_without_raising(self):
        store = MaintenanceStore()
        odd_part = dict(_part("35"))
        del odd_part["count_used"]

        store.hydrate_from_cloud_parts([odd_part], current_hr=300)  # must not raise

        assert store.cloud_parts["35"]["count_used"] is None
        assert store.filter_reset_hr == 0

    def test_current_hr_below_used_hours_clamps_to_zero_not_negative(self):
        store = MaintenanceStore()
        # part 37 (main_brush): count_used=17662 -> 294h used, well above 100h.
        store.hydrate_from_cloud_parts([_part("37")], current_hr=100)
        assert store.brush_reset_hr == 0


# ─────────────────────────────────────────────────────────────────────────────
# MaintenanceStore persistence round trip (cloud_parts / cloud_parts_hydrated_at)
# ─────────────────────────────────────────────────────────────────────────────


class TestCloudPartsPersistenceRoundTrip:
    @pytest.mark.asyncio
    async def test_save_then_fresh_load_restores_cloud_parts(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(_PARTS, current_hr=300)

        saved: dict = {}

        async def fake_save(data):
            saved.update(data)

        store_mock = AsyncMock()
        store_mock.async_save = fake_save
        with patch(
            "custom_components.roomba_plus.maintenance_store.Store",
            return_value=store_mock,
        ):
            await store.async_save(MagicMock(), "entry1")

        assert saved["cloud_parts"] == store.cloud_parts
        assert saved["cloud_parts_hydrated_at"] == store.cloud_parts_hydrated_at

        store2 = MaintenanceStore()
        store_mock2 = AsyncMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch(
            "custom_components.roomba_plus.maintenance_store.Store",
            return_value=store_mock2,
        ):
            await store2.async_load(MagicMock(), "entry1")

        assert store2.cloud_parts == store.cloud_parts
        assert store2.cloud_parts_hydrated_at == store.cloud_parts_hydrated_at
        assert store2.filter_reset_hr == store.filter_reset_hr
        assert store2.brush_reset_hr == store.brush_reset_hr
        assert store2.filter_reset_at == store.filter_reset_at
        assert store2.brush_reset_at == store.brush_reset_at


# ─────────────────────────────────────────────────────────────────────────────
# sensor_cloud.CloudPartSensor
# ─────────────────────────────────────────────────────────────────────────────


class TestCloudPartSensorNativeValue:
    @pytest.mark.parametrize(
        "part_id,expected_hours",
        [("139", 12.0), ("35", 30.0), ("36", 99.0), ("37", 18.0)],
    )
    def test_native_value_matches_fixture_hours(self, part_id, expected_hours):
        coordinator = _make_coordinator(_PARTS)
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, part_id, part_role(part_id)
        )
        assert sensor.native_value == expected_hours

    def test_native_value_none_when_count_type_not_minutes(self):
        percent_part = dict(_part("35"))
        percent_part["count_type"] = "percent"
        coordinator = _make_coordinator([percent_part])
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, "35", part_role("35")
        )
        assert sensor.native_value is None


class TestCloudPartSensorIdentity:
    _EXPECTED_TRANSLATION_KEYS = {
        "35": "part_filter",
        "36": "part_side_brush",
        "37": "part_main_brush",
        "139": "part_clean_base_bag",
    }

    def test_unique_id_and_translation_key_per_fixture_part(self):
        coordinator = _make_coordinator(_PARTS)
        for part_id, expected_key in self._EXPECTED_TRANSLATION_KEYS.items():
            sensor = CloudPartSensor(
                _make_roomba(), "blid_test", coordinator, part_id, part_role(part_id)
            )
            assert sensor._attr_unique_id == f"roomba_plus_blid_test_cloud_part_{part_id}"
            assert sensor._attr_translation_key == expected_key

    def test_unknown_part_id_gets_generic_key_with_placeholder(self):
        unknown_part = {
            "part_id": "999",
            "counter": 10,
            "count_type": "minutes",
            "count_remaining": 120,
            "count_used": 60,
            "last_updated_ts": 1700000000,
            "counter_category": "replacement",
            "reset_by": "user",
        }
        coordinator = _make_coordinator([unknown_part])
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, "999", part_role("999")
        )

        assert sensor._attr_translation_key == "part_generic"
        assert sensor._attr_translation_placeholders == {"part_id": "999"}

        attrs = sensor.extra_state_attributes
        assert attrs["role_source"] == "unmapped"
        assert attrs["role"] == "unknown"


class TestCloudPartSensorAttributes:
    def test_budget_hours_and_percent_used(self):
        coordinator = _make_coordinator(_PARTS)
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, "35", part_role("35")
        )
        attrs = sensor.extra_state_attributes
        # part 35: count_used=1344, count_remaining=1800 -> (1344+1800)/60 = 52.4
        assert attrs["budget_hours"] == 52.4
        assert attrs["percent_used"] == 43  # fixture "counter" field
        assert attrs["part_id"] == "35"
        assert attrs["role"] == IROBOT_PART_ROLE_FILTER
        assert attrs["role_source"] == "inferred"
        assert attrs["source"] == "irobot_cloud_parts"

    def test_part_37_never_reset_reports_none_last_replaced(self):
        coordinator = _make_coordinator(_PARTS)
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, "37", part_role("37")
        )
        attrs = sensor.extra_state_attributes
        assert attrs["last_replaced"] is None
        assert attrs["never_reset"] is True

    def test_part_with_timestamp_has_no_never_reset_flag(self):
        coordinator = _make_coordinator(_PARTS)
        sensor = CloudPartSensor(
            _make_roomba(), "blid_test", coordinator, "35", part_role("35")
        )
        attrs = sensor.extra_state_attributes
        assert attrs["last_replaced"] is not None
        assert "never_reset" not in attrs


class TestBuildCloudPartSensors:
    def test_returns_one_sensor_per_fixture_part(self):
        coordinator = _make_coordinator(_PARTS)
        sensors = build_cloud_part_sensors(_make_roomba(), "blid_test", coordinator)

        assert len(sensors) == 4
        assert {s._part_id for s in sensors} == {"139", "35", "36", "37"}

    def test_empty_coordinator_returns_empty_list(self):
        coordinator = _make_coordinator([])
        sensors = build_cloud_part_sensors(_make_roomba(), "blid_test", coordinator)
        assert sensors == []

    def test_skips_entries_without_part_id(self):
        parts = list(_PARTS) + [{"counter": 1, "count_type": "minutes"}]
        coordinator = _make_coordinator(parts)
        sensors = build_cloud_part_sensors(_make_roomba(), "blid_test", coordinator)
        assert len(sensors) == 4
