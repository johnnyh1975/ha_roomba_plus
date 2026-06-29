"""Tests for REST980-MIGRATE (v2.9.0) — migration helper from roomba_rest980."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from custom_components.roomba_plus.config_flow import (
    REST980_DOMAIN,
    RoombaPlusOptionsFlow,
    _discover_rest980_rooms,
    _resolve_current_pmap_id,
)
from custom_components.roomba_plus.models import MapCapability


# ── _resolve_current_pmap_id ─────────────────────────────────────────────────

class TestResolveCurrentPmapId:
    def test_prefers_last_command(self):
        state = {
            "lastCommand": {"pmap_id": "map_last"},
            "cleanSchedule2": [{"cmd": {"pmap_id": "map_sched"}}],
            "pmaps": [{"map_pmaps": "ts"}],
        }
        assert _resolve_current_pmap_id(state) == "map_last"

    def test_falls_back_to_clean_schedule2(self):
        state = {
            "lastCommand": {},
            "cleanSchedule2": [{"cmd": {"pmap_id": "map_sched"}}],
            "pmaps": [{"map_pmaps": "ts"}],
        }
        assert _resolve_current_pmap_id(state) == "map_sched"

    def test_falls_back_to_pmaps(self):
        state = {"lastCommand": {}, "cleanSchedule2": [], "pmaps": [{"map_pmaps": "ts"}]}
        assert _resolve_current_pmap_id(state) == "map_pmaps"

    def test_empty_state_returns_empty_string(self):
        assert _resolve_current_pmap_id({}) == ""


# ── _discover_rest980_rooms ───────────────────────────────────────────────────

def _make_hass_with_rest980(entities: list[tuple[str, dict]] | None = None,
                             rest980_entries: list | None = None):
    """entities: list of (entity_id, room_data_dict_or_None)."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = (
        rest980_entries if rest980_entries is not None else [MagicMock(entry_id="r980_1")]
    )

    entity_entries = []
    states = {}
    for entity_id, room_data in (entities or []):
        domain = entity_id.split(".")[0]
        ent = MagicMock()
        ent.entity_id = entity_id
        ent.domain = domain
        entity_entries.append(ent)
        st = MagicMock()
        st.attributes = {"room_data": room_data} if room_data is not None else {}
        states[entity_id] = st

    hass.states.get.side_effect = lambda eid: states.get(eid)
    return hass, entity_entries


class TestDiscoverRest980Rooms:
    def test_no_rest980_entries_returns_empty(self):
        hass, _ = _make_hass_with_rest980(rest980_entries=[])
        with patch("custom_components.roomba_plus.config_flow.er.async_get") as mock_er:
            result = _discover_rest980_rooms(hass)
        assert result == {}
        mock_er.assert_not_called()

    def test_select_entities_with_room_data_are_collected(self):
        entities = [
            ("select.clean_kitchen", {"id": "3", "name": "Kitchen"}),
            ("select.clean_hallway", {"id": "5", "name": "Hallway"}),
        ]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get") as mock_er:
            mock_er.return_value = MagicMock()
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {"3": "Kitchen", "5": "Hallway"}

    def test_non_select_domain_entities_ignored(self):
        entities = [
            ("button.fav_morning", {"id": "9", "name": "Should not appear"}),
            ("select.clean_kitchen", {"id": "3", "name": "Kitchen"}),
        ]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get"):
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {"3": "Kitchen"}

    def test_select_entity_without_room_data_attribute_ignored(self):
        entities = [("select.clean_unknown", None)]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get"):
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {}


# ── async_step_rest980_migrate ─────────────────────────────────────────────────

def _make_flow(discovered_rooms: dict, existing_labels: dict | None = None,
                state: dict | None = None):
    flow = object.__new__(RoombaPlusOptionsFlow)
    hass = MagicMock()
    flow.hass = hass

    config_entry = MagicMock()
    config_entry.options = {"smart_zone_labels": existing_labels or {}}
    config_entry.runtime_data.roomba = MagicMock()
    flow._config_entry = config_entry

    flow._discovered = discovered_rooms  # stashed for patch target below
    flow._state = state or {"lastCommand": {"pmap_id": "map_a"}}
    return flow, config_entry


class TestRest980MigrateStep:
    @pytest.mark.asyncio
    async def test_aborts_when_no_rooms_discovered(self):
        flow, _ = _make_flow(discovered_rooms={})
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "abort"
        assert result["reason"] == "no_rest980_rooms_found"

    @pytest.mark.asyncio
    async def test_aborts_when_all_rooms_already_labelled(self):
        flow, _ = _make_flow(
            discovered_rooms={"3": "Kitchen"},
            existing_labels={"3": "Kitchen (manually renamed)"},
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen"},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "abort"
        assert result["reason"] == "rest980_rooms_already_imported"

    @pytest.mark.asyncio
    async def test_shows_form_with_new_rooms(self):
        flow, _ = _make_flow(discovered_rooms={"3": "Kitchen", "5": "Hallway"})
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen", "5": "Hallway"},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "form"
        assert result["step_id"] == "rest980_migrate"

    @pytest.mark.asyncio
    async def test_confirm_import_writes_merged_options_without_clobbering_existing(self):
        flow, config_entry = _make_flow(
            discovered_rooms={"3": "Kitchen", "5": "Hallway"},
            existing_labels={"3": "Kitchen (manually renamed)"},  # must NOT be overwritten
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen", "5": "Hallway"},
        ), patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={"lastCommand": {"pmap_id": "map_a"}},
        ):
            result = await flow.async_step_rest980_migrate({"confirm_import": True})

        assert result["type"] == "create_entry"
        new_labels = result["data"]["smart_zone_labels"]
        assert new_labels["3"] == "Kitchen (manually renamed)"  # untouched
        assert new_labels["5"] == "Hallway"                     # newly imported
        assert result["data"]["smart_zone_data"]["5"] == {"name": "Hallway", "pmap_id": "map_a"}

    @pytest.mark.asyncio
    async def test_declining_confirmation_makes_no_changes(self):
        flow, config_entry = _make_flow(
            discovered_rooms={"3": "Kitchen"}, existing_labels={},
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen"},
        ):
            result = await flow.async_step_rest980_migrate({"confirm_import": False})

        assert result["type"] == "create_entry"
        assert result["data"] == config_entry.options  # unchanged


# ── Menu visibility ───────────────────────────────────────────────────────────

class TestRest980MigrateMenuVisibility:
    @pytest.mark.asyncio
    async def test_menu_includes_rest980_migrate_when_smart_and_detected(self):
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [MagicMock()]  # rest980 present
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.SMART
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_menu_omits_rest980_migrate_when_not_detected(self):
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = []  # nothing detected
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.SMART
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" not in result["menu_options"]

    @pytest.mark.asyncio
    async def test_menu_omits_rest980_migrate_for_ephemeral_robots(self):
        """EPHEMERAL robots have no smart_zone_data concept — migration target
        doesn't apply even if roomba_rest980 happens to be installed."""
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [MagicMock()]
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.EPHEMERAL
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" not in result["menu_options"]
