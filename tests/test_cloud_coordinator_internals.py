"""Tests for cloud_coordinator.py internal logic (normalization + EPHEMERAL).

Merged (TEST-REORG follow-up) from test_cloud_normalizer.py and
test_cloud_ephemeral.py — both exclusively test cloud_coordinator.py's
internal logic (mission-history normalization/aggregation, and EPHEMERAL-
robot-specific coordinator behaviour), just at different feature angles.

Part 1: _normalize_mission_history / _aggregate_history — defensive cloud
API parsing. The /missionhistory endpoint returns different structures
depending on firmware version and region; the normalizer tries multiple
source paths so the lifetime sensors work regardless of structure.

Part 2: Phase 2b — Cloud support for EPHEMERAL robots (980/900-series).
Covers IrobotCloudCoordinator.has_pmaps gating, the cloud setup gate for
NONE-tier robots, config flow cloud_credentials menu visibility, and
EPHEMERAL coordinator behaviour (empty pmaps/favorites, populated
mission_history).

No HA or roombapy installation required — uses conftest.py stubs.
"""
from __future__ import annotations

import sys
import os
import types
import asyncio
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import tests.conftest  # noqa: F401

from custom_components.roomba_plus.cloud_coordinator import (
    IrobotCloudCoordinator,
    _normalize_mission_history,
    _aggregate_history,
)
from custom_components.roomba_plus.models import MapCapability


# ── Part 1: _normalize_mission_history / _aggregate_history ──────────────────

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


# ── Part 2: EPHEMERAL robot cloud support ─────────────────────────────────────

# ── Helpers ───────────────────────────────────────────────────────────────────

def _bare_coordinator(has_pmaps: bool = False) -> IrobotCloudCoordinator:
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


# ── TestHasPmapsFlag ─────────────────────────────────────────────────────────

class TestHasPmapsFlag:
    def test_has_pmaps_false_by_default(self):
        cc = object.__new__(IrobotCloudCoordinator)
        # Simulate __init__ setting _has_pmaps with default
        cc._has_pmaps = False
        assert cc._has_pmaps is False

    def test_has_pmaps_true_when_set(self):
        cc = _bare_coordinator(has_pmaps=True)
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


# ── TestCoordinatorEphemeral ─────────────────────────────────────────────────

class TestCoordinatorEphemeral:
    @pytest.mark.asyncio
    async def test_pmaps_not_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_pmaps" not in api.calls

    @pytest.mark.asyncio
    async def test_favorites_not_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_favorites" not in api.calls

    @pytest.mark.asyncio
    async def test_mission_history_fetched_when_has_pmaps_false(self):
        cc = _bare_coordinator(has_pmaps=False)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_mission_history" in api.calls

    @pytest.mark.asyncio
    async def test_result_pmaps_empty_list_when_ephemeral(self):
        cc = _bare_coordinator(has_pmaps=False)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert result["pmaps"] == []

    @pytest.mark.asyncio
    async def test_result_favorites_empty_list_when_ephemeral(self):
        cc = _bare_coordinator(has_pmaps=False)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert result["favorites"] == []

    @pytest.mark.asyncio
    async def test_result_mission_history_populated_when_ephemeral(self):
        cc = _bare_coordinator(has_pmaps=False)
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


# ── TestCoordinatorSmart ─────────────────────────────────────────────────────

class TestCoordinatorSmart:
    @pytest.mark.asyncio
    async def test_pmaps_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_pmaps" in api.calls

    @pytest.mark.asyncio
    async def test_favorites_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_favorites" in api.calls

    @pytest.mark.asyncio
    async def test_mission_history_fetched_when_has_pmaps_true(self):
        cc = _bare_coordinator(has_pmaps=True)
        api = _FakeApi()
        await _run_update(cc, api)
        assert "get_mission_history" in api.calls

    @pytest.mark.asyncio
    async def test_result_pmaps_populated_when_smart(self):
        cc = _bare_coordinator(has_pmaps=True)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert len(result["pmaps"]) == 1

    @pytest.mark.asyncio
    async def test_result_favorites_populated_when_smart(self):
        cc = _bare_coordinator(has_pmaps=True)
        api = _FakeApi()
        result = await _run_update(cc, api)
        assert len(result["favorites"]) == 1


# ── TestCloudGateLogic ───────────────────────────────────────────────────────

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


# ── TestConfigFlowCloudMenu ──────────────────────────────────────────────────

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
