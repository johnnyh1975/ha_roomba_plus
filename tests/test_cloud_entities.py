"""Unit tests for cloud-sourced entities: CloudSmartZoneSelect and FavoriteButton.

Tests cover:
  - CloudSmartZoneSelect option list, current_option, selected_region_id
  - CloudSmartZoneSelect extra_state_attributes
  - CloudSmartZoneSelect multi-pmap / multi-region scenarios
  - FavoriteButton command extraction and dispatch
  - FavoriteButton hidden flag → entity_registry_enabled_default
  - select.py async_setup_entry routing (cloud vs MQTT-fallback path)
  - SmartZoneSelect._async_raise_naming_issue suppression when cloud active

No HA or roombapy installation required — uses stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from custom_components.roomba_plus.select import CloudSmartZoneSelect
from custom_components.roomba_plus.button import FavoriteButton


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config_entry(has_cloud: bool = False, favorites=None, pmaps=None):
    """Return a minimal mock config entry."""
    entry = MagicMock()
    entry.unique_id = "test_blid"
    entry.options = {}
    entry.data = {"blid": "test_blid"}

    cc = MagicMock()
    cc.data = {
        "pmaps": pmaps or [],
        "favorites": favorites or [],
        "mission_history": {},
    }
    cc.active_pmap_id = (pmaps[0].get("active_pmapv_details", {}).get("active_pmapv", {}).get("pmap_id") if pmaps else None)
    runtime = MagicMock()
    runtime.has_cloud = has_cloud
    runtime.cloud_coordinator = cc if has_cloud else None
    entry.runtime_data = runtime
    return entry


def _make_roomba():
    r = MagicMock()
    r.master_state = {"state": {"reported": {}}}
    return r


def _zone_select(regions=None, zones=None, pmap_id="map1", map_name="Ground floor"):
    entry = _make_config_entry()
    roomba = _make_roomba()
    return CloudSmartZoneSelect(
        roomba, "test_blid", entry,
        pmap_id=pmap_id,
        map_name=map_name,
        regions=regions or [],
        zones=zones or [],
    )


# ── CloudSmartZoneSelect — option list ────────────────────────────────────────

class TestCloudSmartZoneSelectOptions:
    def test_empty_when_no_regions_or_zones(self):
        sel = _zone_select()
        assert sel.options == []

    def test_single_region(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen", "region_type": "kitchen"}])
        assert sel.options == ["Kitchen"]

    def test_multiple_regions(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen", "region_type": "kitchen"},
            {"id": "5", "name": "Hallway", "region_type": "hallway"},
        ])
        assert sel.options == ["Kitchen", "Hallway"]

    def test_zones_appended_after_regions(self):
        sel = _zone_select(
            regions=[{"id": "3", "name": "Kitchen", "region_type": "kitchen"}],
            zones=[{"id": "z1", "name": "Sofa area", "zone_type": "furniture"}],
        )
        assert sel.options == ["Kitchen", "Sofa area"]

    def test_fallback_name_when_missing(self):
        sel = _zone_select(regions=[{"id": "7"}])
        assert sel.options == ["Zone 7"]

    def test_fallback_name_empty_string(self):
        sel = _zone_select(regions=[{"id": "9", "name": ""}])
        assert sel.options == ["Zone 9"]

    def test_available_when_regions_present(self):
        sel = _zone_select(regions=[{"id": "1", "name": "Room"}])
        assert sel.available is True

    def test_not_available_when_empty(self):
        sel = _zone_select()
        assert sel.available is False


# ── CloudSmartZoneSelect — current_option ─────────────────────────────────────

class TestCloudSmartZoneSelectCurrentOption:
    def test_defaults_to_first_option(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        assert sel.current_option == "Kitchen"

    def test_none_when_no_options(self):
        sel = _zone_select()
        assert sel.current_option is None

    def test_selected_persists(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        sel._selected = "Hallway"
        assert sel.current_option == "Hallway"

    def test_invalid_selection_resets_to_first(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen"}])
        sel._selected = "Nonexistent"
        assert sel.current_option == "Kitchen"


# ── CloudSmartZoneSelect — selected_region_id ─────────────────────────────────

class TestCloudSmartZoneSelectRegionId:
    def test_returns_id_for_selected_name(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        sel._selected = "Hallway"
        assert sel.selected_region_id == "5"

    def test_falls_back_to_first_when_no_selection(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen"}])
        assert sel.selected_region_id == "3"

    def test_none_when_no_regions(self):
        sel = _zone_select()
        assert sel.selected_region_id is None

    def test_zone_id_returned_when_zone_selected(self):
        sel = _zone_select(
            regions=[{"id": "3", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Sofa area"}],
        )
        sel._selected = "Sofa area"
        assert sel.selected_region_id == "z1"

    def test_selected_pmap_info_contains_pmap_id(self):
        sel = _zone_select(pmap_id="abc123", regions=[{"id": "1", "name": "R"}])
        info = sel.selected_pmap_info
        assert info["pmap_id"] == "abc123"

    def test_selected_pmap_info_pmapv_empty(self):
        """user_pmapv_id is intentionally empty — SmartZoneButton resolves it live."""
        sel = _zone_select(regions=[{"id": "1", "name": "R"}])
        assert sel.selected_pmap_info["user_pmapv_id"] == ""


# ── CloudSmartZoneSelect — extra_state_attributes ─────────────────────────────

class TestCloudSmartZoneSelectAttributes:
    def test_attributes_keys(self):
        sel = _zone_select(
            pmap_id="map1", map_name="Ground floor",
            regions=[{"id": "3", "name": "Kitchen"}],
        )
        attrs = sel.extra_state_attributes
        assert attrs["pmap_id"] == "map1"
        assert attrs["map_name"] == "Ground floor"
        assert attrs["region_id"] == "3"
        assert attrs["region_count"] == 1
        assert attrs["zone_count"] == 0
        assert attrs["source"] == "cloud"

    def test_zone_count_correct(self):
        sel = _zone_select(
            regions=[{"id": "1", "name": "A"}],
            zones=[{"id": "z1", "name": "B"}, {"id": "z2", "name": "C"}],
        )
        assert sel.extra_state_attributes["zone_count"] == 2

    def test_unique_id_includes_pmap_id(self):
        sel = _zone_select(pmap_id="xyz789", regions=[{"id": "1", "name": "R"}])
        assert "xyz789" in sel._attr_unique_id


class TestCloudSmartZoneSelectActiveInactive:
    """Tests for active vs inactive map distinction."""

    def test_active_map_enabled_by_default(self):
        sel = _zone_select(regions=[{"id": "1", "name": "R"}])
        # is_active_map defaults to True
        assert sel._attr_entity_registry_enabled_default is True

    def test_inactive_map_disabled_by_default(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert sel._attr_entity_registry_enabled_default is False

    def test_inactive_map_name_gets_suffix(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert "(inactive)" in sel._map_name

    def test_active_map_name_unchanged(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="active_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=True,
        )
        assert sel._map_name == "Ground floor"

    def test_is_active_map_in_attributes(self):
        sel = _zone_select(regions=[{"id": "1", "name": "Kitchen"}])
        assert "is_active_map" in sel.extra_state_attributes
        assert sel.extra_state_attributes["is_active_map"] is True

    def test_inactive_in_attributes(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert sel.extra_state_attributes["is_active_map"] is False

    def test_is_active_map_flag_stored(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="p1", map_name="Home",
            regions=[{"id": "1", "name": "R"}],
            zones=[],
            is_active_map=False,
        )
        assert sel._is_active_map is False


# ── CloudSmartZoneSelect — no_state_filter ────────────────────────────────────

class TestCloudSmartZoneSelectNoMqttUpdate:
    def test_new_state_filter_always_false(self):
        """Cloud entity must not react to MQTT messages."""
        sel = _zone_select(regions=[{"id": "1", "name": "Room"}])
        assert sel.new_state_filter({"cleanSchedule2": [{}]}) is False
        assert sel.new_state_filter({"lastCommand": {}}) is False
        assert sel.new_state_filter({}) is False


# ── FavoriteButton ─────────────────────────────────────────────────────────────

def _fav_button(favorite):
    entry = _make_config_entry(has_cloud=True)
    roomba = _make_roomba()
    return FavoriteButton(roomba, "test_blid", entry, favorite)


class TestFavoriteButton:
    def test_name_from_favorite(self):
        btn = _fav_button({"favorite_id": "f1", "name": "Morning clean", "commanddefs": []})
        assert btn._attr_name == "Morning clean"

    def test_unique_id_includes_favorite_id(self):
        btn = _fav_button({"favorite_id": "fav42", "name": "X", "commanddefs": []})
        assert "fav42" in btn._attr_unique_id

    def test_visible_by_default_when_not_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": False})
        assert btn._attr_entity_registry_enabled_default is True

    def test_disabled_by_default_when_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": True})
        assert btn._attr_entity_registry_enabled_default is False

    def test_default_enabled_when_hidden_missing(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert btn._attr_entity_registry_enabled_default is True

    def test_inherits_irobot_entity(self):
        """FavoriteButton must inherit IRobotEntity for correct device linkage."""
        from custom_components.roomba_plus.entity import IRobotEntity
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert isinstance(btn, IRobotEntity)

    def test_has_vacuum_attribute(self):
        """IRobotEntity provides .vacuum — used in async_press instead of config_entry."""
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert hasattr(btn, "vacuum")

    @pytest.mark.asyncio
    async def test_press_sends_command(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f1",
            "name": "Morning",
            "commanddefs": [{"command": "start", "pmap_id": "map1", "regions": [{"region_id": "3"}]}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        btn.hass.async_add_executor_job.assert_called_once()
        args = btn.hass.async_add_executor_job.call_args[0]
        # args[0] is the bound method, args[1] is command, args[2] is params
        assert args[1] == "start"
        assert args[2]["pmap_id"] == "map1"

    @pytest.mark.asyncio
    async def test_press_no_commanddefs_logs_warning(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "Empty", "commanddefs": []}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with patch("custom_components.roomba_plus.button._LOGGER") as mock_log:
            await btn.async_press()

        mock_log.warning.assert_called_once()
        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_missing_commanddefs_key(self):
        """commanddefs key absent — should not raise, should warn."""
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "NoCmd"}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()  # must not raise

        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_extracts_params_excluding_command_key(self):
        """All keys except 'command' become params."""
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f2",
            "name": "Kitchen",
            "commanddefs": [{"command": "start", "pmap_id": "p1", "ordered": 1}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        params = btn.hass.async_add_executor_job.call_args[0][2]
        assert "command" not in params
        assert params["pmap_id"] == "p1"
        assert params["ordered"] == 1


# ── select.py: cloud vs MQTT routing in async_setup_entry ─────────────────────

class TestSelectSetupEntryRouting:
    """Verify that async_setup_entry creates the right entity type."""

    def _cloud_pmap(self):
        return {
            "active_pmapv_details": {
                "active_pmapv": {"pmap_id": "map1"},
                "map_header": {"name": "Home"},
                "regions": [{"id": "3", "name": "Kitchen", "region_type": "kitchen"}],
                "zones": [],
            }
        }

    @pytest.mark.asyncio
    async def test_cloud_active_creates_cloud_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect, SmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        state = {"pmaps": [{"map1": "v1"}]}
        entry = _make_config_entry(has_cloud=True, pmaps=[self._cloud_pmap()])
        entry.runtime_data.map_capability = MapCapability.SMART
        # active_pmap_id matches the pmap in _cloud_pmap()
        entry.runtime_data.cloud_coordinator.active_pmap_id = "map1"

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        mqtt_selects = [e for e in created if isinstance(e, SmartZoneSelect)]
        assert len(cloud_selects) == 1, f"Expected 1 CloudSmartZoneSelect, got {created}"
        assert len(mqtt_selects) == 0
        # Active map must be enabled
        assert cloud_selects[0]._attr_entity_registry_enabled_default is True
        assert cloud_selects[0]._is_active_map is True

    @pytest.mark.asyncio
    async def test_inactive_pmap_creates_disabled_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        active = self._cloud_pmap()  # pmap_id = "map1"
        inactive = {
            "active_pmapv_details": {
                "active_pmapv": {"pmap_id": "old_map"},
                "map_header": {"name": "Old Home"},
                "regions": [{"id": "9", "name": "Garage", "region_type": "garage"}],
                "zones": [],
            }
        }
        state = {"pmaps": [{"map1": "v1"}, {"old_map": "v2"}]}
        entry = _make_config_entry(has_cloud=True, pmaps=[active, inactive])
        entry.runtime_data.map_capability = MapCapability.SMART
        entry.runtime_data.cloud_coordinator.active_pmap_id = "map1"

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        assert len(cloud_selects) == 2

        active_sel = next(e for e in cloud_selects if e._pmap_id == "map1")
        inactive_sel = next(e for e in cloud_selects if e._pmap_id == "old_map")

        assert active_sel._attr_entity_registry_enabled_default is True
        assert inactive_sel._attr_entity_registry_enabled_default is False
        assert "(inactive)" in inactive_sel._map_name

    @pytest.mark.asyncio
    async def test_no_cloud_creates_mqtt_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect, SmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        state = {"pmaps": [{"map1": "v1"}]}
        entry = _make_config_entry(has_cloud=False)
        entry.runtime_data.map_capability = MapCapability.SMART

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        mqtt_selects = [e for e in created if isinstance(e, SmartZoneSelect)]
        assert len(cloud_selects) == 0
        assert len(mqtt_selects) == 1, f"Expected 1 SmartZoneSelect, got {created}"

    @pytest.mark.asyncio
    async def test_cloud_active_suppresses_repair_issue(self):
        """SmartZoneSelect._async_raise_naming_issue must not create issue when cloud active."""
        from custom_components.roomba_plus.select import SmartZoneSelect
        from homeassistant.helpers import issue_registry as ir

        entry = _make_config_entry(has_cloud=True)
        sel = SmartZoneSelect.__new__(SmartZoneSelect)
        sel._config_entry = entry
        sel._known_unlabelled = set()

        original_create = ir.async_create_issue
        calls = []
        ir.async_create_issue = lambda *a, **kw: calls.append((a, kw))
        try:
            await sel._async_raise_naming_issue(["3", "5"])
        finally:
            ir.async_create_issue = original_create

        assert calls == [], "async_create_issue should not be called when cloud is active"
