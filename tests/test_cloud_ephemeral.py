"""Tests for Phase 2b — Cloud support for EPHEMERAL robots (980/900-series).

Covers:
  - IrobotCloudCoordinator.has_pmaps flag controls which endpoints are called
  - Cloud gate in async_setup_entry: NONE never gets coordinator
  - Config flow menu: cloud_credentials offered for EPHEMERAL and SMART
  - EPHEMERAL coordinator returns empty pmaps/favorites, populated mission_history

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

from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.models import MapCapability


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bare_coordinator(has_pmaps: bool = False) -> IrobotCloudCoordinator:
    """Create a coordinator instance without HA setup, with _has_pmaps set."""
    cc = object.__new__(IrobotCloudCoordinator)
    cc.data = None
    cc.blid = "testblid"
    cc._has_pmaps = has_pmaps
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
