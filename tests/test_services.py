"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_entity_registry_entry(config_entry_id: str) -> MagicMock:
    e = MagicMock()
    e.config_entry_id = config_entry_id
    return e


def _make_config_entry(entry_id="entry1", title="Test Robot", maintenance_store=None):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.runtime_data.maintenance_store = maintenance_store or MagicMock()
    entry.runtime_data.maintenance_store.async_save = AsyncMock()
    entry.runtime_data.roomba_reported_state.return_value = {
        "bbrun": {"hr": 42}, "runtimeStats": {},
    }
    return entry


class TestServicesRegistration:
    """async_register_services and async_remove_services behave correctly."""

    def _make_hass(self):
        """Minimal hass stub tracking registered/removed services."""
        registered = {}

        class _Services:
            def has_service(self, domain, name):
                return (domain, name) in registered

            def async_register(self, domain, name, handler, schema=None,
                               supports_response=None):
                registered[(domain, name)] = handler

            def async_remove(self, domain, name):
                registered.pop((domain, name), None)

        class _FakeHass:
            services = _Services()

        return _FakeHass(), registered

    def test_registers_all_eleven_services(self):
        from custom_components.roomba_plus.services import async_register_services
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)

        expected = {
            (DOMAIN, "clean_room"),
            (DOMAIN, "smart_start"),
            (DOMAIN, "clean_sequence"),
            (DOMAIN, "reset_filter"),
            (DOMAIN, "reset_brush"),
            (DOMAIN, "reset_battery"),
            (DOMAIN, "reset_pad"),
            # IA74-MAINT (v2.7.0)
            (DOMAIN, "reset_wheel_cleaning"),
            (DOMAIN, "reset_contact_cleaning"),
            (DOMAIN, "reset_bin_cleaning"),
            (DOMAIN, "reset_robot_profile"),
            # ADVANCE-ROOM-V2 (v2.8.0)
            (DOMAIN, "advance_room"),
        }
        assert expected == set(registered.keys())

    def test_register_is_idempotent(self):
        """Calling register twice does not duplicate or error."""
        from custom_components.roomba_plus.services import async_register_services
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)
        first_handler = registered[(DOMAIN, "clean_room")]
        async_register_services(hass)
        # Handler not replaced on second call
        assert registered[(DOMAIN, "clean_room")] is first_handler
        assert len(registered) == 12

    def test_removes_all_eleven_services(self):
        from custom_components.roomba_plus.services import (
            async_register_services,
            async_remove_services,
        )
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)
        assert len(registered) == 12

        async_remove_services(hass)
        assert len(registered) == 0

    def test_remove_is_safe_when_not_registered(self):
        """async_remove_services does not raise when services are absent."""
        from custom_components.roomba_plus.services import async_remove_services
        hass, registered = self._make_hass()
        async_remove_services(hass)   # should not raise
        assert len(registered) == 0


class TestConfCleanDelayMin:
    def test_constant_defined(self):
        from custom_components.roomba_plus.const import CONF_CLEAN_DELAY_MIN
        assert CONF_CLEAN_DELAY_MIN == "clean_delay_min"

    def test_default_is_zero(self):
        from custom_components.roomba_plus.const import DEFAULT_CLEAN_DELAY_MIN
        assert DEFAULT_CLEAN_DELAY_MIN == 0


class TestCleanSequenceSchema:
    def test_schema_requires_entity_id(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({"target_entity_id": "vacuum.b"})

    def test_schema_requires_target_entity_id(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({"entity_id": "vacuum.a"})

    def test_schema_defaults_require_completed_true(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
        })
        assert result["require_completed"] is True

    def test_schema_defaults_delay_zero(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
        })
        assert result["delay_minutes"] == 0

    def test_schema_accepts_require_completed_false(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "require_completed": False,
        })
        assert result["require_completed"] is False

    def test_schema_accepts_delay_minutes(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "delay_minutes": 15,
        })
        assert result["delay_minutes"] == 15

    def test_delay_clamped_at_60(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({
                "entity_id": "vacuum.a",
                "target_entity_id": "vacuum.b",
                "delay_minutes": 61,
            })

    def test_delay_min_is_zero(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "delay_minutes": 0,
        })
        assert result["delay_minutes"] == 0


class TestCleanSequenceConstant:
    def test_service_constant_defined(self):
        from custom_components.roomba_plus.const import SERVICE_CLEAN_SEQUENCE
        assert SERVICE_CLEAN_SEQUENCE == "clean_sequence"

    def test_service_in_services_module(self):
        # Verify the handler and schema are importable
        from custom_components.roomba_plus.services import (
            _CLEAN_SEQUENCE_SCHEMA,
            async_handle_clean_sequence,
        )
        assert callable(async_handle_clean_sequence)
        assert _CLEAN_SEQUENCE_SCHEMA is not None


class TestFireMaintenanceResetEvent:
    """v2.9.0 LOGBOOK — _fire_maintenance_reset_event() shared helper."""

    def test_payload_with_hours(self):
        from custom_components.roomba_plus.services import _fire_maintenance_reset_event
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        hass = MagicMock()
        entry = _make_config_entry(entry_id="e1", title="Roomba 980")

        _fire_maintenance_reset_event(hass, entry, "filter", 142)

        hass.bus.async_fire.assert_called_once_with(
            EVENT_MAINTENANCE_RESET,
            {"entry_id": "e1", "name": "Roomba 980", "component": "filter", "hours": 142},
        )

    def test_payload_without_hours(self):
        """Calendar-based inspect resets (wheel/contact/bin) have no hr baseline."""
        from custom_components.roomba_plus.services import _fire_maintenance_reset_event

        hass = MagicMock()
        entry = _make_config_entry()

        _fire_maintenance_reset_event(hass, entry, "wheel", None)

        payload = hass.bus.async_fire.call_args[0][1]
        assert payload["hours"] is None
        assert payload["component"] == "wheel"


class TestHandleResetServiceFiresEvent:
    """v2.9.0 LOGBOOK — _handle_reset_service() fires maintenance_reset."""

    async def _run(self, part="filter"):
        from custom_components.roomba_plus.services import _handle_reset_service
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        hass = MagicMock()
        entry = _make_config_entry(entry_id="e1", title="Roomba 980")
        hass.config_entries.async_get_entry.return_value = entry

        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry("e1")
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        ), patch(
            "custom_components.roomba_plus.services._async_signal_entities",
        ):
            call = MagicMock()
            call.data = {"entity_id": ["sensor.x_filter_remaining_hours"]}
            await _handle_reset_service(hass, call, part)

        return hass, entry, EVENT_MAINTENANCE_RESET

    @pytest.mark.asyncio
    async def test_fires_event_with_current_hr(self):
        hass, entry, event = await self._run("filter")
        hass.bus.async_fire.assert_called_once_with(
            event,
            {"entry_id": "e1", "name": "Roomba 980", "component": "filter", "hours": 42},
        )

    @pytest.mark.asyncio
    async def test_works_for_each_resettable_part(self):
        for part in ("filter", "brush", "battery", "pad"):
            hass, _, event = await self._run(part)
            payload = hass.bus.async_fire.call_args[0][1]
            assert payload["component"] == part


class TestHandleInspectResetServiceFiresEvent:
    """v2.9.0 LOGBOOK — _handle_inspect_reset_service() fires
    maintenance_reset with hours=None (no hr-counter baseline for
    calendar-based wheel/contact/bin cleaning resets)."""

    @pytest.mark.asyncio
    async def test_fires_event_with_hours_none(self):
        from custom_components.roomba_plus.services import _handle_inspect_reset_service
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        hass = MagicMock()
        entry = _make_config_entry(entry_id="e1", title="Roomba 980")
        hass.config_entries.async_get_entry.return_value = entry

        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry("e1")
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        ), patch(
            "custom_components.roomba_plus.services._async_signal_entities",
        ):
            call = MagicMock()
            call.data = {"entity_id": ["sensor.x_wheel_last_cleaned"]}
            await _handle_inspect_reset_service(hass, call, "wheel")

        hass.bus.async_fire.assert_called_once_with(
            EVENT_MAINTENANCE_RESET,
            {"entry_id": "e1", "name": "Roomba 980", "component": "wheel", "hours": None},
        )


# ── CLEAN-ROOM-PER-ROOM-PASSES (v2.9.0) ──────────────────────────────────────

class TestCleanRoomTwoPassSchema:
    """Bugfix found while implementing CLEAN-ROOM-PER-ROOM-PASSES: two_pass
    was documented in services.yaml and read by the handler, but missing
    from the registered voluptuous schema entirely — any caller going
    through real schema validation (YAML automations, Developer Tools UI)
    was rejected with 'extra keys not allowed @ data[\"two_pass\"]'.
    """

    def _captured_schema(self):
        """Register services against a hass stub that records the real
        schema objects, so we can validate payloads against them directly."""
        from custom_components.roomba_plus.services import async_register_services
        from custom_components.roomba_plus.const import DOMAIN

        schemas = {}

        class _Services:
            def has_service(self, domain, name):
                return (domain, name) in schemas

            def async_register(self, domain, name, handler, schema=None,
                               supports_response=None):
                schemas[(domain, name)] = schema

        class _FakeHass:
            services = _Services()

        async_register_services(_FakeHass())
        return schemas[(DOMAIN, "clean_room")]

    def test_two_pass_accepted_by_real_schema(self):
        schema = self._captured_schema()
        # Must not raise — this is the exact payload shape services.yaml
        # has always documented as valid.
        result = schema({
            "entity_id": "vacuum.test",
            "room_name": "Kitchen",
            "two_pass": True,
        })
        assert result["two_pass"] is True

    def test_room_passes_accepted_by_real_schema(self):
        schema = self._captured_schema()
        result = schema({
            "entity_id": "vacuum.test",
            "room_passes": [
                {"name": "Kitchen", "two_pass": True},
                {"name": "Hallway"},
            ],
        })
        assert result["room_passes"][0]["name"] == "Kitchen"
        assert result["room_passes"][0]["two_pass"] is True
        assert "two_pass" not in result["room_passes"][1]

    def test_room_name_no_longer_required_at_schema_level(self):
        """room_passes is now a valid alternative to room_name."""
        schema = self._captured_schema()
        result = schema({
            "entity_id": "vacuum.test",
            "room_passes": [{"name": "Kitchen"}],
        })
        assert "room_name" not in result


def _make_clean_room_call(hass, entity_id="vacuum.test", **data):
    """Build a minimal ServiceCall-like object for async_handle_clean_room."""
    call = MagicMock()
    call.hass = hass
    call.data = {"entity_id": [entity_id], "ordered": True, **data}
    return call


def _make_smart_config_entry(*, zone_data, two_pass_state=False, global_two_pass=None):
    from custom_components.roomba_plus.models import MapCapability

    config_entry = MagicMock()
    config_entry.options = {"smart_zone_data": zone_data}
    data = config_entry.runtime_data
    data.map_capability = MapCapability.SMART
    data.has_cloud = False
    data.roomba_reported_state.return_value = {
        "lastCommand": {"pmap_id": "map_a", "user_pmapv_id": "ts1"},
        "cleanMissionStatus": {"notReady": 0},
        "noAutoPasses": False,
        "twoPass": two_pass_state,
    }
    data.roomba.send_command = MagicMock()
    return config_entry


class TestCleanRoomPerRoomPasses:
    """End-to-end coverage for the room_passes per-room two_pass resolution,
    exercised against the real async_handle_clean_room handler."""

    ZONE_DATA = {
        "3": {"name": "Kitchen", "pmap_id": "map_a"},
        "5": {"name": "Hallway", "pmap_id": "map_a"},
    }

    def _make_hass(self, config_entry):
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry

        async def _run_executor(func, *args):
            return func(*args)
        hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)
        return hass

    @pytest.mark.asyncio
    async def test_room_name_and_room_passes_conflict_raises(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        hass = MagicMock()
        call = _make_clean_room_call(
            hass, room_name="Kitchen",
            room_passes=[{"name": "Hallway"}],
        )
        with pytest.raises(Exception) as exc_info:
            await async_handle_clean_room(call)
        assert "room_name_and_room_passes_conflict" in str(
            getattr(exc_info.value, "translation_key", "")
        ) or "room_name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_neither_room_name_nor_room_passes_raises(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        hass = MagicMock()
        call = _make_clean_room_call(hass)
        with pytest.raises(Exception) as exc_info:
            await async_handle_clean_room(call)
        assert "room_name_or_room_passes_required" in str(
            getattr(exc_info.value, "translation_key", "")
        ) or "room_name" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_per_room_two_pass_applied_independently(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        config_entry = _make_smart_config_entry(zone_data=self.ZONE_DATA, two_pass_state=False)
        hass = self._make_hass(config_entry)
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        call = _make_clean_room_call(
            hass,
            room_passes=[
                {"name": "Kitchen", "two_pass": True},
                {"name": "Hallway"},
            ],
        )
        with patch("custom_components.roomba_plus.services.er.async_get") as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_clean_room(call)

        sent_params = config_entry.runtime_data.roomba.send_command.call_args[0][1]
        regions_by_id = {r["region_id"]: r for r in sent_params["regions"]}
        assert regions_by_id["3"]["params"]["twoPass"] is True   # Kitchen — explicit override
        assert regions_by_id["5"]["params"]["twoPass"] is False  # Hallway — falls back to robot state

    @pytest.mark.asyncio
    async def test_global_two_pass_fallback_when_room_omits_override(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        config_entry = _make_smart_config_entry(zone_data=self.ZONE_DATA, two_pass_state=False)
        hass = self._make_hass(config_entry)
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        call = _make_clean_room_call(
            hass,
            two_pass=True,  # global override
            room_passes=[{"name": "Kitchen"}, {"name": "Hallway"}],
        )
        with patch("custom_components.roomba_plus.services.er.async_get") as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_clean_room(call)

        sent_params = config_entry.runtime_data.roomba.send_command.call_args[0][1]
        for region in sent_params["regions"]:
            assert region["params"]["twoPass"] is True

    @pytest.mark.asyncio
    async def test_per_room_override_takes_priority_over_global(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        config_entry = _make_smart_config_entry(zone_data=self.ZONE_DATA, two_pass_state=False)
        hass = self._make_hass(config_entry)
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        call = _make_clean_room_call(
            hass,
            two_pass=True,  # global override says True...
            room_passes=[{"name": "Kitchen", "two_pass": False}],  # ...but Kitchen explicitly says False
        )
        with patch("custom_components.roomba_plus.services.er.async_get") as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_clean_room(call)

        sent_params = config_entry.runtime_data.roomba.send_command.call_args[0][1]
        assert sent_params["regions"][0]["params"]["twoPass"] is False


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_clean_room_action.py (TEST-REORG, v2.9.1) — tests for
# the roomba_plus.clean_room action helpers (_resolve_pmapv_id,
# _resolve_rooms incl. v1.4.4 empty-pmap_id/cross-floor handling, and the
# command-params shape). Note: the original docstring said these helpers
# live in __init__.py — they have since moved to services.py, which is
# exactly why this file now lives here instead of test_init_wiring.py.
# ═══════════════════════════════════════════════════════════════════════

# ── Local reference implementations ──────────────────────────────────────────
# These mirror the production helpers so we can test logic in isolation.
# The TestResolveRoomsProduction class tests the real implementation directly.

class ServiceValidationError(Exception):
    def __init__(self, message="", translation_domain=None,
                 translation_key=None, translation_placeholders=None):
        super().__init__(message)
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


def _resolve_pmapv_id_ref(state: dict, pmap_id: str):
    for pmap in state.get("pmaps", []):
        if pmap_id in pmap:
            return pmap[pmap_id]
    return None


def _resolve_rooms_ref(zone_data: dict, room_names: list, state: dict = None):
    """Reference implementation matching __init__.py as of v1.4.4."""
    state = state or {}
    index = {
        meta["name"].casefold(): (rid, meta.get("pmap_id", ""))
        for rid, meta in zone_data.items()
        if meta.get("name")
    }
    resolved = []
    unknown = []
    for name in room_names:
        match = index.get(name.casefold())
        if match is None:
            unknown.append(name)
        else:
            resolved.append(match)
    if unknown:
        raise ServiceValidationError(
            f"Unknown room(s): {', '.join(unknown)}",
            translation_key="rooms_not_found",
            translation_placeholders={"names": ", ".join(unknown)},
        )
    live_pmap_id = next(
        (next(iter(p)) for p in state.get("pmaps", []) if p), ""
    )
    resolved = [
        (rid, pmap_id if pmap_id else live_pmap_id)
        for rid, pmap_id in resolved
    ]
    pmap_ids = {p for _, p in resolved}
    if "" in pmap_ids:
        raise ServiceValidationError(
            "Could not resolve pmap_id",
            translation_key="pmap_not_resolved",
        )
    if len(pmap_ids) > 1:
        raise ServiceValidationError(
            "Rooms span multiple floors",
            translation_key="rooms_different_floors",
            translation_placeholders={"pmap_ids": ", ".join(pmap_ids)},
        )
    return resolved


# ── Shared fixtures ───────────────────────────────────────────────────────────

ZONE_DATA_SINGLE_FLOOR = {
    "3": {"name": "Kitchen",  "pmap_id": "map_ground_floor"},
    "5": {"name": "Hallway",  "pmap_id": "map_ground_floor"},
    "7": {"name": "Office",   "pmap_id": "map_ground_floor"},
}

ZONE_DATA_MULTI_FLOOR = {
    "3": {"name": "Kitchen",  "pmap_id": "map_ground_floor"},
    "9": {"name": "Bedroom",  "pmap_id": "map_first_floor"},
}

STATE_WITH_PMAP = {"pmaps": [{"map_ground_floor": "220101T120000"}]}


# ─────────────────────────────────────────────────────────────────────────────
# 1. _resolve_pmapv_id
# ─────────────────────────────────────────────────────────────────────────────

class TestResolvePmapvId:
    """Tests for the live pmap freshness resolver."""

    def test_found_single_pmap(self):
        state = {"pmaps": [{"abc123": "220101T120000"}]}
        assert _resolve_pmapv_id_ref(state, "abc123") == "220101T120000"

    def test_found_among_multiple_pmaps(self):
        state = {"pmaps": [{"floor1": "ts1"}, {"floor2": "ts2"}]}
        assert _resolve_pmapv_id_ref(state, "floor2") == "ts2"

    def test_not_found_returns_none(self):
        state = {"pmaps": [{"abc123": "ts1"}]}
        assert _resolve_pmapv_id_ref(state, "does_not_exist") is None

    def test_empty_pmaps_returns_none(self):
        assert _resolve_pmapv_id_ref({"pmaps": []}, "abc123") is None

    def test_missing_pmaps_key_returns_none(self):
        assert _resolve_pmapv_id_ref({}, "abc123") is None

    def test_always_reads_latest_value(self):
        """Simulates a map retrain: pmapv_id changes, resolver reflects it."""
        state_v1 = {"pmaps": [{"abc123": "220101T120000"}]}
        state_v2 = {"pmaps": [{"abc123": "230601T090000"}]}
        assert _resolve_pmapv_id_ref(state_v1, "abc123") == "220101T120000"
        assert _resolve_pmapv_id_ref(state_v2, "abc123") == "230601T090000"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _resolve_rooms — reference implementation (pre-v1.4.4 behaviour)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRooms:
    """Core resolver tests — zones with stored pmap_id (normal path)."""

    def test_single_room(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor")]

    def test_multi_room_same_floor(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Hallway"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor"), ("5", "map_ground_floor")]

    def test_order_preserved(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Office", "Kitchen"], STATE_WITH_PMAP)
        assert result == [("7", "map_ground_floor"), ("3", "map_ground_floor")]

    def test_case_insensitive(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["kitchen"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor")]

    def test_mixed_case(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["KITCHEN", "halLWAY"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor"), ("5", "map_ground_floor")]

    def test_unknown_room_raises(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Bathroom"], STATE_WITH_PMAP)
        assert exc_info.value.translation_key == "rooms_not_found"
        assert "Bathroom" in exc_info.value.translation_placeholders["names"]

    def test_partial_unknown_raises(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Nonexistent"], STATE_WITH_PMAP)
        assert "Nonexistent" in exc_info.value.translation_placeholders["names"]

    def test_multiple_unknowns_all_reported(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["X", "Y"], STATE_WITH_PMAP)
        names = exc_info.value.translation_placeholders["names"]
        assert "X" in names and "Y" in names

    def test_cross_floor_raises(self):
        state = {"pmaps": [{"map_ground_floor": "ts1"}, {"map_first_floor": "ts2"}]}
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_MULTI_FLOOR, ["Kitchen", "Bedroom"], state)
        assert exc_info.value.translation_key == "rooms_different_floors"
        placeholders = exc_info.value.translation_placeholders["pmap_ids"]
        assert "map_ground_floor" in placeholders
        assert "map_first_floor" in placeholders

    def test_empty_zone_data_raises(self):
        with pytest.raises(ServiceValidationError):
            _resolve_rooms_ref({}, ["Kitchen"], STATE_WITH_PMAP)

    def test_empty_room_list_returns_empty(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, [], STATE_WITH_PMAP)
        assert result == []

    def test_regions_payload_shape(self):
        """Verify the regions list shape expected by the MQTT command."""
        resolved = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Hallway"], STATE_WITH_PMAP)
        regions = [{"region_id": rid, "type": "rid"} for rid, _ in resolved]
        assert regions == [
            {"region_id": "3", "type": "rid"},
            {"region_id": "5", "type": "rid"},
        ]

    def test_zone_missing_name_excluded(self):
        """Entries without a name are excluded from the index."""
        data = {
            "3": {"name": "Kitchen", "pmap_id": "map_a"},
            "4": {"pmap_id": "map_a"},   # no name — excluded
        }
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms_ref(data, ["Kitchen"], state)
        assert result == [("3", "map_a")]

    def test_zone_with_name_but_no_pmap_resolved_from_state(self):
        """Entry with name but no pmap_id: resolved from state.pmaps (v1.4.4)."""
        data = {"5": {"name": "Office"}}   # no pmap_id key at all
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms_ref(data, ["Office"], state)
        assert result == [("5", "map_a")]


# ─────────────────────────────────────────────────────────────────────────────
# 3. _resolve_rooms — empty pmap_id fallback (v1.4.4 Alt 1 manual entry)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoomsEmptyPmapId:
    """Tests for zones entered via manual entry with empty pmap_id."""

    def _zone(self, pmap_id: str = "") -> dict:
        return {"21": {"name": "Corridor", "pmap_id": pmap_id}}

    def _state(self, pmap_id: str = "abc123") -> dict:
        return {"pmaps": [{pmap_id: "v42"}]}

    def test_empty_pmap_resolved_from_live_state(self):
        """Zone with pmap_id='' gets pmap from state.pmaps at call time."""
        result = _resolve_rooms_ref(self._zone(""), ["Corridor"], self._state("abc123"))
        assert result == [("21", "abc123")]

    def test_stored_pmap_preferred_over_live(self):
        """Zone with stored pmap_id uses it — live state is not consulted."""
        result = _resolve_rooms_ref(self._zone("stored_pmap"), ["Corridor"], self._state("live_pmap"))
        assert result == [("21", "stored_pmap")]

    def test_empty_pmap_and_empty_state_raises(self):
        """Empty pmap_id + no state.pmaps → cannot resolve → raises."""
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(self._zone(""), ["Corridor"], {})
        assert exc_info.value.translation_key == "pmap_not_resolved"

    def test_empty_pmap_and_empty_pmap_list_raises(self):
        """Empty pmap_id + empty pmaps list → raises."""
        with pytest.raises(ServiceValidationError):
            _resolve_rooms_ref(self._zone(""), ["Corridor"], {"pmaps": []})

    def test_case_insensitive_with_empty_pmap(self):
        """Case-insensitivity works even when pmap_id must be resolved."""
        result = _resolve_rooms_ref(self._zone(""), ["CORRIDOR"], self._state())
        assert result[0][0] == "21"

    def test_unknown_room_raises_even_with_empty_pmap(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(self._zone(""), ["Bedroom"], self._state())
        assert exc_info.value.translation_key == "rooms_not_found"

    def test_multiple_rooms_all_empty_pmap_resolved_consistently(self):
        """Multiple zones with empty pmap all resolve to the same live pmap."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},
            "22": {"name": "Kitchen",  "pmap_id": ""},
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms_ref(data, ["Corridor", "Kitchen"], state)
        assert result == [("21", "abc123"), ("22", "abc123")]

    def test_mixed_empty_and_stored_resolves_to_same_pmap(self):
        """Zone with empty pmap alongside zone with stored pmap.
        Empty one resolves to live value — both end up on same pmap → no cross-floor error."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},        # manual entry
            "22": {"name": "Kitchen",  "pmap_id": "abc123"},  # from MQTT
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms_ref(data, ["Corridor", "Kitchen"], state)
        assert result[0] == ("21", "abc123")
        assert result[1] == ("22", "abc123")

    def test_mixed_empty_and_different_stored_raises_cross_floor(self):
        """Empty pmap resolves to live pmap. If stored pmap differs → cross-floor error."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},              # resolves to abc123
            "22": {"name": "Bedroom",  "pmap_id": "other_floor"},   # different pmap
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(data, ["Corridor", "Bedroom"], state)
        assert exc_info.value.translation_key == "rooms_different_floors"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _resolve_rooms — production implementation (imports from __init__)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoomsProduction:
    """Runs a subset of tests against the real _resolve_rooms in __init__.py.

    This ensures the reference implementation in this test file stays in sync
    with the production code. If these tests fail but the reference tests pass,
    the production code has diverged from the spec.
    """

    def test_stored_pmap_used(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"3": {"name": "Kitchen", "pmap_id": "map_a"}}
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms(data, ["Kitchen"], state)
        assert result == [("3", "map_a")]

    def test_empty_pmap_resolved_from_state(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms(data, ["Corridor"], state)
        assert result == [("21", "abc123")]

    def test_empty_pmap_no_state_raises(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Corridor"], {})

    def test_unknown_room_raises(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {"3": {"name": "Kitchen", "pmap_id": "map_a"}}
        state = {"pmaps": [{"map_a": "ts1"}]}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Bathroom"], state)

    def test_mixed_pmap_resolves(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},
            "22": {"name": "Kitchen",  "pmap_id": "abc123"},
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms(data, ["Corridor", "Kitchen"], state)
        assert result[0] == ("21", "abc123")
        assert result[1] == ("22", "abc123")

    def test_cross_floor_raises(self):
        from custom_components.roomba_plus.services import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {
            "3": {"name": "Kitchen", "pmap_id": "floor1"},
            "9": {"name": "Bedroom", "pmap_id": "floor2"},
        }
        state = {"pmaps": [{"floor1": "ts1"}, {"floor2": "ts2"}]}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Kitchen", "Bedroom"], state)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Command params shape
# ─────────────────────────────────────────────────────────────────────────────

class TestCommandParams:
    """Verify the params dict passed to send_command is correctly formed."""

    def _build_params(self, resolved, pmap_id, user_pmapv_id, ordered=True):
        return {
            "ordered": 1 if ordered else 0,
            "pmap_id": pmap_id,
            "user_pmapv_id": user_pmapv_id,
            "regions": [
                {"region_id": rid, "type": "rid"}
                for rid, _ in resolved
            ],
        }

    def test_single_room_params(self):
        resolved = [("3", "map_a")]
        params = self._build_params(resolved, "map_a", "ts_fresh")
        assert params["pmap_id"] == "map_a"
        assert params["user_pmapv_id"] == "ts_fresh"
        assert params["ordered"] == 1
        assert params["regions"] == [{"region_id": "3", "type": "rid"}]

    def test_multi_room_params(self):
        resolved = [("3", "map_a"), ("5", "map_a")]
        params = self._build_params(resolved, "map_a", "ts_fresh")
        assert len(params["regions"]) == 2
        assert params["regions"][0] == {"region_id": "3", "type": "rid"}
        assert params["regions"][1] == {"region_id": "5", "type": "rid"}

    def test_ordered_false_sends_zero(self):
        resolved = [("3", "map_a"), ("5", "map_a")]
        params = self._build_params(resolved, "map_a", "ts", ordered=False)
        assert params["ordered"] == 0

    def test_ordered_true_sends_one(self):
        resolved = [("3", "map_a")]
        params = self._build_params(resolved, "map_a", "ts", ordered=True)
        assert params["ordered"] == 1

    def test_pmap_id_from_first_resolved_tuple(self):
        """pmap_id in params comes from the resolved tuple, not a separate lookup."""
        resolved = [("3", "map_ground"), ("5", "map_ground")]
        pmap_id = resolved[0][1]
        params = self._build_params(resolved, pmap_id, "ts")
        assert params["pmap_id"] == "map_ground"

    def test_regions_type_is_rid(self):
        """The iRobot API requires type='rid' for region targeting."""
        resolved = [("21", "map_a")]
        params = self._build_params(resolved, "map_a", "ts")
        assert params["regions"][0]["type"] == "rid"

    def test_user_pmapv_id_not_cached(self):
        """Simulate a map retrain: user_pmapv_id refreshed from live state."""
        state_v1 = {"pmaps": [{"map_a": "ts_old"}]}
        state_v2 = {"pmaps": [{"map_a": "ts_new"}]}
        assert _resolve_pmapv_id_ref(state_v1, "map_a") == "ts_old"
        assert _resolve_pmapv_id_ref(state_v2, "map_a") == "ts_new"
