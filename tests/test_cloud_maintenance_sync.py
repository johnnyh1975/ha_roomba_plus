"""Cloud-synced maintenance consumables.

The existing filter/brush sensors read iRobot's own per-part counters when
the account serves them, and fall back to the local runtime-hours estimate
when it does not. Parts with no local equivalent (side brush, Clean Base
bag) get their own cloud-only sensors. Replacements recorded here are
pushed back to iRobot so the app and this integration agree.

Every payload here is a real capture from an i3+ (sku i355640, firmware
daredevil+2.6.0) — see tests/fixtures/irobot_parts_i3plus.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.cloud_api import IrobotCloudApi
from custom_components.roomba_plus.const import (
    IROBOT_PART_ROLE_CLEAN_BASE_BAG,
    IROBOT_PART_ROLE_FILTER,
    IROBOT_PART_ROLE_MAIN_BRUSH,
    IROBOT_PART_ROLE_SIDE_BRUSH,
    part_role,
)
from custom_components.roomba_plus.maintenance_store import MaintenanceStore

FIXTURES = Path(__file__).parent / "fixtures"


def _parts() -> list[dict]:
    with open(FIXTURES / "irobot_parts_i3plus.json", encoding="utf-8") as f:
        return json.load(f)["parts"]


def _hydrated(current_hr: int = 300) -> MaintenanceStore:
    store = MaintenanceStore()
    store.hydrate_from_cloud_parts(_parts(), current_hr)
    return store


class TestRoleLookup:
    def test_every_fixture_part_resolves_to_a_role(self):
        roles = {part_role(p["part_id"]) for p in _parts()}
        assert roles == {
            IROBOT_PART_ROLE_FILTER,
            IROBOT_PART_ROLE_SIDE_BRUSH,
            IROBOT_PART_ROLE_MAIN_BRUSH,
            IROBOT_PART_ROLE_CLEAN_BASE_BAG,
        }

    def test_side_brush_is_part_36(self):
        """Field-confirmed, not inferred: @mdarocha replaced the side brush
        and confirmed it in iRobot's app, and 36 is the id that reset."""
        assert part_role("36") == IROBOT_PART_ROLE_SIDE_BRUSH


class TestCloudRemainingHours:
    @pytest.mark.parametrize(
        ("role", "expected_hours"),
        [
            (IROBOT_PART_ROLE_CLEAN_BASE_BAG, 12),   # part 139: 720 min
            (IROBOT_PART_ROLE_FILTER, 30),           # part 35: 1800 min
            (IROBOT_PART_ROLE_SIDE_BRUSH, 99),       # part 36: 5940 min
            (IROBOT_PART_ROLE_MAIN_BRUSH, 18),       # part 37: 1080 min
        ],
    )
    def test_converts_cloud_minutes_to_hours(self, role, expected_hours):
        assert _hydrated().cloud_remaining_hours(role) == expected_hours

    def test_none_for_role_the_robot_does_not_report(self):
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts(
            [p for p in _parts() if p["part_id"] != "36"], 300
        )
        assert store.cloud_remaining_hours(IROBOT_PART_ROLE_SIDE_BRUSH) is None

    def test_none_before_any_hydration(self):
        assert MaintenanceStore().cloud_remaining_hours(
            IROBOT_PART_ROLE_FILTER
        ) is None

    def test_none_when_counter_is_not_minute_based(self):
        """A non-minute counter cannot be divided into hours, so it must
        report nothing rather than a wrong number."""
        odd = dict(_parts()[0])
        odd["count_type"] = "missions"
        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([odd], 300)
        assert store.cloud_remaining_hours(part_role(odd["part_id"])) is None

    def test_part_lookup_returns_the_record(self):
        rec = _hydrated().cloud_part_by_role(IROBOT_PART_ROLE_FILTER)
        assert rec is not None
        assert rec["part_id"] == "35"
        assert rec["role"] == IROBOT_PART_ROLE_FILTER


class TestSetRobotPartCounter:
    """The write contract, confirmed live against the API.

    Static analysis of the app suggested PUT with a flat {"part_id": ...}
    body; both are wrong. PUT/PATCH are unrouted (403), and the flat body
    is accepted with 200 but silently applies nothing (num_parts 0).
    """

    def _api(self) -> tuple[IrobotCloudApi, AsyncMock]:
        api = IrobotCloudApi.__new__(IrobotCloudApi)
        api._deployment = {"httpBaseAuth": "https://auth3.example.invalid"}
        posted = AsyncMock(return_value={"num_parts": 1, "parts": [
            {"part_id": "35", "counter": 0}]})
        api._aws_post = posted  # type: ignore[method-assign]
        return api, posted

    @pytest.mark.asyncio
    async def test_posts_the_array_form_with_counter(self):
        api, posted = self._api()
        await api.set_robot_part_counter("BLID1", "35", 0)

        url, body = posted.await_args.args
        assert url == "https://auth3.example.invalid/v1/robots/BLID1/parts"
        # The array form is the only one the API applies.
        assert body == {"parts": [{"part_id": "35", "counter": 0}]}

    @pytest.mark.asyncio
    async def test_defaults_to_zero_meaning_brand_new(self):
        api, posted = self._api()
        await api.set_robot_part_counter("BLID1", "35")
        assert posted.await_args.args[1]["parts"][0]["counter"] == 0

    @pytest.mark.asyncio
    async def test_coerces_part_id_and_counter_types(self):
        api, posted = self._api()
        await api.set_robot_part_counter("BLID1", 35, True)  # type: ignore[arg-type]
        entry = posted.await_args.args[1]["parts"][0]
        assert entry["part_id"] == "35"
        assert entry["counter"] == 1

    @pytest.mark.asyncio
    async def test_non_dict_response_becomes_empty_dict(self):
        api, posted = self._api()
        posted.return_value = ["unexpected"]
        assert await api.set_robot_part_counter("BLID1", "35") == {}


class TestSigV4SignsTheBody:
    def test_body_is_included_in_the_payload_hash(self):
        """A POST body must be hashed into the signature; signing an empty
        payload while sending a real one yields a 403 that reads like an
        auth failure."""
        from custom_components.roomba_plus.cloud_api import _AWSSignatureV4

        signer = _AWSSignatureV4("AKIA", "secret", "token")
        common = {
            "method": "POST", "service": "execute-api", "region": "us-east-1",
            "host": "auth3.example.invalid", "path": "/v1/robots/B/parts",
        }
        empty = signer.signed_headers(**common)
        with_body = signer.signed_headers(**common, payload='{"parts":[]}')
        assert empty["Authorization"] != with_body["Authorization"]


class TestPushResetToCloud:
    def _data(self, store: MaintenanceStore | None, *, cloud: bool = True):
        data = MagicMock()
        data.blid = "BLID1"
        data.maintenance_store = store
        if cloud:
            data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
                return_value={"num_parts": 1}
            )
            data.cloud_coordinator.async_request_refresh = AsyncMock()
        else:
            data.cloud_coordinator = None
        return data

    @pytest.mark.asyncio
    async def test_filter_reset_pushes_the_mapped_part_id(self):
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated())
        await _async_push_part_reset_to_cloud(MagicMock(), data, "filter")

        data.cloud_coordinator.api.set_robot_part_counter.assert_awaited_once_with(
            "BLID1", "35", 0
        )
        data.cloud_coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_brush_reset_maps_to_the_main_brushes(self):
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated())
        await _async_push_part_reset_to_cloud(MagicMock(), data, "brush")
        data.cloud_coordinator.api.set_robot_part_counter.assert_awaited_once_with(
            "BLID1", "37", 0
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("part", ["battery", "pad"])
    async def test_parts_with_no_cloud_counter_are_skipped(self, part):
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated())
        await _async_push_part_reset_to_cloud(MagicMock(), data, part)
        data.cloud_coordinator.api.set_robot_part_counter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_cloud_configured_is_a_silent_no_op(self):
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated(), cloud=False)
        await _async_push_part_reset_to_cloud(MagicMock(), data, "filter")  # must not raise

    @pytest.mark.asyncio
    async def test_unhydrated_store_is_a_no_op(self):
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(MaintenanceStore())
        await _async_push_part_reset_to_cloud(MagicMock(), data, "filter")
        data.cloud_coordinator.api.set_robot_part_counter.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cloud_failure_does_not_propagate(self):
        """The local reset is already saved by the caller, so a cloud
        outage must not turn a successful user action into an error."""
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated())
        data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            side_effect=RuntimeError("cloud down")
        )
        await _async_push_part_reset_to_cloud(MagicMock(), data, "filter")
        data.cloud_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepted_but_applied_nothing_is_treated_as_failure(self):
        """num_parts 0 is a 200 that changed nothing — it must not be
        reported as a successful reset, and must not trigger a refresh."""
        from custom_components.roomba_plus.services import (
            _async_push_part_reset_to_cloud,
        )
        data = self._data(_hydrated())
        data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            return_value={"num_parts": 0, "parts": {}}
        )
        await _async_push_part_reset_to_cloud(MagicMock(), data, "filter")
        data.cloud_coordinator.async_request_refresh.assert_not_awaited()


class TestSensorsPreferCloud:
    def _sensor(self, key, store, run_stats=None, options=None):
        from custom_components.roomba_plus.sensor_core import SENSORS, RoombaSensor

        desc = next(d for d in SENSORS if d.key == key)
        sensor = RoombaSensor.__new__(RoombaSensor)
        sensor.entity_description = desc
        entry = MagicMock()
        entry.options = options or {}
        entry.runtime_data.maintenance_store = store
        sensor._config_entry = entry
        sensor._attr_run_stats = run_stats or {}
        type(sensor).run_stats = property(lambda s: run_stats or {})
        return sensor

    def test_filter_sensor_reports_the_cloud_hours(self):
        assert self._sensor("filter_remaining_hours", _hydrated()).native_value == 30

    def test_brush_sensor_reports_the_main_brush_cloud_hours(self):
        assert self._sensor("brush_remaining_hours", _hydrated()).native_value == 18

    def test_side_brush_sensor_is_cloud_only(self):
        assert self._sensor(
            "part_edge_brush", _hydrated()
        ).native_value == 99

    def test_bag_sensor_is_cloud_only(self):
        assert self._sensor(
            "part_dirt_bag", _hydrated()
        ).native_value == 12

    def test_cloud_only_sensors_report_none_without_cloud_data(self):
        empty = MaintenanceStore()
        for key in ("part_edge_brush", "part_dirt_bag"):
            assert self._sensor(key, empty).native_value is None

    def test_filter_falls_back_to_local_estimate_without_cloud(self):
        """Robots whose account serves no parts data must keep the
        behaviour they had before any of this existed."""
        store = MaintenanceStore()
        store.filter_reset_hr = 10
        value = self._sensor(
            "filter_remaining_hours", store,
            run_stats={"hr": 30}, options={"filter_threshold_hours": 60},
        ).native_value
        assert value == 40  # 60 - (30 - 10)
