"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import json
from pathlib import Path

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

    def test_registers_all_expected_services(self):
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
            # v3.2.0 ANOMALY-EXPLAIN
            (DOMAIN, "explain_mission"),
            # v3.3.0 ROOM-SCHED
            (DOMAIN, "clean_overdue_rooms"),
            # v3.3.0 SMART-ORDER
            (DOMAIN, "auto_clean_dirty_rooms"),
            # v3.5.0 FULL-BACKUP
            (DOMAIN, "create_backup"),
            (DOMAIN, "restore_backup"),
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
        assert len(registered) == 17

    def test_removes_all_registered_services(self):
        from custom_components.roomba_plus.services import (
            async_register_services,
            async_remove_services,
        )
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)
        assert len(registered) == 17

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


class TestHandleSmartStartConnectionTypeBranching:
    """NEW (this session) -- async_handle_smart_start() used to
    unconditionally call data.roomba.start whenever no blocking_manager
    was configured for the entry, regardless of tier -- a real crash
    (AttributeError) for any Prime entry with no CONF_BLOCKING_SENSORS
    set, which is the common case (blocking sensors are opt-in)."""

    def _make_call(self, hass, rooms=None, override=False):
        call = MagicMock()
        call.hass = hass
        call.data = {"entity_id": ["vacuum.test"]}
        if rooms is not None:
            call.data["rooms"] = rooms
        if override:
            call.data["override_blocking"] = override
        return call

    def _patch_entity_registry(self, config_entry_id="entry1"):
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry(config_entry_id)
        return patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        )

    @pytest.mark.asyncio
    async def test_prime_no_blocking_manager_calls_send_simple_command(self):
        """THE crash fix itself."""
        from custom_components.roomba_plus.services import async_handle_smart_start
        from custom_components.roomba_plus.models import ConnectionType

        hass = MagicMock()
        entry = _make_config_entry(entry_id="entry1")
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blocking_manager = None
        entry.runtime_data.prime_robot = AsyncMock()
        hass.config_entries.async_get_entry.return_value = entry

        with self._patch_entity_registry("entry1"):
            await async_handle_smart_start(self._make_call(hass))

        entry.runtime_data.prime_robot.send_simple_command.assert_awaited_once_with("start")

    @pytest.mark.asyncio
    async def test_classic_no_blocking_manager_still_calls_roomba_start(self):
        """Unaffected by the fix -- the pre-existing Classic path."""
        from custom_components.roomba_plus.services import async_handle_smart_start
        from custom_components.roomba_plus.models import ConnectionType

        hass = MagicMock()
        entry = _make_config_entry(entry_id="entry1")
        entry.runtime_data.connection_type = ConnectionType.LOCAL_PUSH
        entry.runtime_data.blocking_manager = None
        started = []
        entry.runtime_data.roomba.start = lambda: started.append(True)
        hass.config_entries.async_get_entry.return_value = entry
        hass.async_add_executor_job = AsyncMock(side_effect=lambda fn: fn())

        with self._patch_entity_registry("entry1"):
            await async_handle_smart_start(self._make_call(hass))

        assert started == [True]

    @pytest.mark.asyncio
    async def test_prime_with_rooms_raises_honest_error_not_a_crash(self):
        """The room-targeted case: a clear, honest error instead of
        either a crash or the misleading "not_smart_map" message."""
        from custom_components.roomba_plus.services import async_handle_smart_start
        from custom_components.roomba_plus.models import ConnectionType
        from homeassistant.exceptions import ServiceValidationError

        hass = MagicMock()
        entry = _make_config_entry(entry_id="entry1")
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        hass.config_entries.async_get_entry.return_value = entry

        with self._patch_entity_registry("entry1"):
            with pytest.raises(ServiceValidationError) as exc_info:
                await async_handle_smart_start(self._make_call(hass, rooms=["Kitchen"]))

        assert exc_info.value.translation_key == "prime_rooms_not_supported"

    @pytest.mark.asyncio
    async def test_blocking_manager_present_delegates_regardless_of_tier(self):
        """Untouched by this session's fix -- confirms the existing
        blocking_manager delegation path still works exactly as before."""
        from custom_components.roomba_plus.services import async_handle_smart_start
        from custom_components.roomba_plus.models import ConnectionType

        hass = MagicMock()
        entry = _make_config_entry(entry_id="entry1")
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blocking_manager = AsyncMock()
        hass.config_entries.async_get_entry.return_value = entry

        with self._patch_entity_registry("entry1"):
            await async_handle_smart_start(self._make_call(hass, override=True))

        entry.runtime_data.blocking_manager.check_and_start.assert_awaited_once_with(None, True)


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
    async def test_empty_room_passes_raises_no_rooms_resolved(self):
        """room_passes=[] passes the 'exactly one of' checks but yields no rooms.

        Bug-hunt regression: an empty array is not None, so it slips past both
        'provide either' guards; room_names becomes [] and _resolve_rooms
        returns [] without raising (it only raises on *unknown* names). The
        explicit guard must raise no_rooms_resolved instead of IndexError at
        resolved[0][1].
        """
        from custom_components.roomba_plus.services import async_handle_clean_room

        config_entry = _make_smart_config_entry(zone_data=self.ZONE_DATA, two_pass_state=False)
        hass = self._make_hass(config_entry)
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        call = _make_clean_room_call(hass, room_passes=[])
        with patch("custom_components.roomba_plus.services.er.async_get") as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            with pytest.raises(Exception) as exc_info:
                await async_handle_clean_room(call)
        # Must be the guarded validation error, NOT an IndexError
        assert not isinstance(exc_info.value, IndexError)
        assert "no_rooms_resolved" in str(
            getattr(exc_info.value, "translation_key", "")
        ) or "room" in str(exc_info.value).lower()

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

class TestCleanRoomSmartGateMessage:
    """The non-SMART rejection must name the Braava jet m6 and point at the
    real condition (a finalized Smart Map), not just an i7/s9/j model list.

    Regression guard for the misleading message that omitted the m6 — the exact
    model a field user (KingAnt) reported with. SMART capability is detected
    from persistent pmaps, not a hardcoded model list, so a finalized m6 map
    qualifies and the user-facing text must reflect that.
    """

    def _make_non_smart_call(self, hass, capability):
        from custom_components.roomba_plus.models import MapCapability

        config_entry = MagicMock()
        config_entry.options = {"smart_zone_data": {}}
        data = config_entry.runtime_data
        data.map_capability = capability
        hass.config_entries.async_get_entry.return_value = config_entry

        call = MagicMock()
        call.hass = hass
        call.data = {"entity_id": ["vacuum.test"], "room_name": "Kitchen",
                     "ordered": True}
        return call

    @pytest.mark.asyncio
    @pytest.mark.parametrize("capability_name", ["NONE", "EPHEMERAL"])
    async def test_message_mentions_m6_and_finalized_map(self, capability_name):
        from custom_components.roomba_plus.services import async_handle_clean_room
        from custom_components.roomba_plus.models import MapCapability

        capability = getattr(MapCapability, capability_name)
        hass = MagicMock()
        call = self._make_non_smart_call(hass, capability)

        with pytest.raises(Exception) as exc_info:
            await async_handle_clean_room(call)

        msg = str(exc_info.value)
        # The corrected message names the m6 and the real precondition.
        assert "m6" in msg, f"message should mention Braava jet m6: {msg!r}"
        assert "Smart Map" in msg
        # And keeps the stable translation key for localized clients.
        assert getattr(exc_info.value, "translation_key", "") == "not_smart_map"


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


def _mission_record(
    id="m_1", duration_min=60, area_sqft=200.0, recharge_min=0, dirt=10,
    npicks_delta=0, error_code=None,
):
    from datetime import datetime, timezone
    return {
        "id": id, "duration_min": duration_min, "area_sqft": area_sqft,
        "recharge_min": recharge_min, "dirt": dirt,
        "npicks_delta": npicks_delta, "error_code": error_code,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result": "completed",
    }


def _make_mission_store(records):
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    store._records = records
    return store


class TestExplainMission:
    """v3.2.0 ANOMALY-EXPLAIN — async_handle_explain_mission."""

    def _make_call(self, entity_id="sensor.x_health_score", mission_id=None):
        call = MagicMock()
        data = {"entity_id": entity_id}
        if mission_id is not None:
            data["mission_id"] = mission_id
        call.data = data
        return call

    async def _run(self, mission_store, mission_id=None,
                    entity_id="sensor.x_health_score"):
        from custom_components.roomba_plus.services import async_handle_explain_mission
        entry = _make_config_entry(entry_id="e1")
        entry.runtime_data.mission_store = mission_store
        call = self._make_call(entity_id=entity_id, mission_id=mission_id)
        call.hass.config_entries.async_get_entry.return_value = entry

        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry("e1")
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        ):
            return await async_handle_explain_mission(call)

    @pytest.mark.asyncio
    async def test_entity_not_found_raises(self):
        from custom_components.roomba_plus.services import async_handle_explain_mission
        from homeassistant.exceptions import ServiceValidationError
        call = self._make_call()
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
        ) as mock_er:
            mock_er.return_value.async_get.return_value = None
            with pytest.raises(ServiceValidationError):
                await async_handle_explain_mission(call)

    @pytest.mark.asyncio
    async def test_no_mission_store_raises(self):
        from homeassistant.exceptions import ServiceValidationError
        with pytest.raises(ServiceValidationError):
            await self._run(mission_store=None)

    @pytest.mark.asyncio
    async def test_mission_id_not_found_raises(self):
        from homeassistant.exceptions import ServiceValidationError
        store = _make_mission_store([_mission_record(id="m_1")])
        with pytest.raises(ServiceValidationError):
            await self._run(store, mission_id="m_nonexistent")

    @pytest.mark.asyncio
    async def test_defaults_to_latest_when_no_mission_id(self):
        store = _make_mission_store([
            _mission_record(id="m_1"), _mission_record(id="m_2"),
        ])
        result = await self._run(store)
        assert result["mission_id"] == "m_2"

    @pytest.mark.asyncio
    async def test_explicit_mission_id_used(self):
        store = _make_mission_store([
            _mission_record(id="m_1"), _mission_record(id="m_2"),
        ])
        result = await self._run(store, mission_id="m_1")
        assert result["mission_id"] == "m_1"

    @pytest.mark.asyncio
    async def test_cloud_only_id_resolves_via_raw_records(self):
        """v3.3.1 bug-hunt fix — the service must resolve "c_{ts}" ids the
        same way the REST view (ExplainMissionView) does; previously only
        the REST view got this capability, contradicting the contract's
        "both delegate to the same logic" claim."""
        from custom_components.roomba_plus.services import async_handle_explain_mission
        store = _make_mission_store([])  # no local records
        entry = _make_config_entry(entry_id="e1")
        entry.runtime_data.mission_store = store
        entry.runtime_data.cloud_coordinator.raw_records = [
            {"startTime": 1700000000, "timestamp": 1700003600,
             "durationM": 42, "sqft": 300.0, "dirt": 5},
        ]
        call = self._make_call(mission_id="c_1700000000")
        call.hass.config_entries.async_get_entry.return_value = entry
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry("e1")
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        ):
            result = await async_handle_explain_mission(call)
        assert result["mission_id"] == "c_1700000000"

    @pytest.mark.asyncio
    async def test_cloud_only_id_no_match_raises(self):
        from custom_components.roomba_plus.services import async_handle_explain_mission
        from homeassistant.exceptions import ServiceValidationError
        store = _make_mission_store([])
        entry = _make_config_entry(entry_id="e1")
        entry.runtime_data.mission_store = store
        entry.runtime_data.cloud_coordinator.raw_records = []
        call = self._make_call(mission_id="c_9999999999")
        call.hass.config_entries.async_get_entry.return_value = entry
        ent_reg = MagicMock()
        ent_reg.async_get.return_value = _make_entity_registry_entry("e1")
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=ent_reg,
        ):
            with pytest.raises(ServiceValidationError):
                await async_handle_explain_mission(call)

    @pytest.mark.asyncio
    async def test_not_anomalous_when_stats_unavailable(self):
        """Fewer than 20 missions and no archive baseline → stats is None
        → anomaly_reason gracefully skipped, not an error."""
        store = _make_mission_store([_mission_record()])
        result = await self._run(store)
        assert result["is_anomalous"] is False
        assert result["anomaly_reason"] is None
        assert result["recommended_action"] is None

    @pytest.mark.asyncio
    async def test_anomaly_reason_and_recommendation_populated(self):
        """20 normal missions establish stats, the 21st is a clear
        obstacle_or_blockage case — full round-trip through real
        compute_rolling_stats(), not a stubbed stats dict."""
        normal = [
            _mission_record(id=f"m_{i}", duration_min=60, area_sqft=200.0)
            for i in range(20)
        ]
        anomalous = _mission_record(id="m_last", duration_min=200, area_sqft=30.0)
        store = _make_mission_store(normal + [anomalous])
        result = await self._run(store, mission_id="m_last")
        assert result["is_anomalous"] is True
        assert result["anomaly_reason"] == "obstacle_or_blockage"
        assert result["recommended_action"] is not None

    @pytest.mark.asyncio
    async def test_robot_lifted_true_when_npicks_delta_positive(self):
        store = _make_mission_store([_mission_record(npicks_delta=1)])
        result = await self._run(store)
        assert result["robot_lifted"] is True

    @pytest.mark.asyncio
    async def test_robot_lifted_false_when_npicks_delta_zero(self):
        store = _make_mission_store([_mission_record(npicks_delta=0)])
        result = await self._run(store)
        assert result["robot_lifted"] is False

    @pytest.mark.asyncio
    async def test_robot_lifted_false_when_npicks_delta_missing(self):
        """Older records recorded before ANOMALY-EXPLAIN shipped won't have
        this field at all — must default safely to False, not crash."""
        record = _mission_record()
        del record["npicks_delta"]
        store = _make_mission_store([record])
        result = await self._run(store)
        assert result["robot_lifted"] is False

    @pytest.mark.asyncio
    async def test_error_code_passed_through(self):
        store = _make_mission_store([_mission_record(error_code=17)])
        result = await self._run(store)
        assert result["error_code"] == 17

    @pytest.mark.asyncio
    async def test_error_code_and_anomaly_reason_are_independent(self):
        """A mission can have both an error_code AND an anomaly_reason —
        neither should suppress the other."""
        normal = [
            _mission_record(id=f"m_{i}", duration_min=60, area_sqft=200.0)
            for i in range(20)
        ]
        both = _mission_record(
            id="m_last", duration_min=200, area_sqft=30.0, error_code=224,
        )
        store = _make_mission_store(normal + [both])
        result = await self._run(store, mission_id="m_last")
        assert result["anomaly_reason"] == "obstacle_or_blockage"
        assert result["error_code"] == 224


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 ROOM-SCHED — clean_overdue_rooms service
# ─────────────────────────────────────────────────────────────────────────────

from custom_components.roomba_plus.models import MapCapability


class TestCleanOverdueRooms:
    """v3.3.0 ROOM-SCHED — SMART-only guard, no-op semantics, worst-first
    delegation to clean_room (shared merge rule with the sensor)."""

    def _entry(self, tier, records=None, options=None):
        from custom_components.roomba_plus.mission_store import MissionStore
        entry = MagicMock()
        entry.options = options or {}
        data = entry.runtime_data
        data.map_capability = tier
        ms = MissionStore()
        ms._records = records or []
        data.mission_store = ms
        data.has_cloud = True
        data.cloud_coordinator.regions = [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
        ]
        # v3.3.0 SMART-ORDER routing — explicit None: an auto-MagicMock
        # aligner would engage the route optimizer with mock garbage.
        data.umf_aligner = None
        return entry

    def _call(self, hass, entry, **extra):
        hass.config_entries.async_get_entry.return_value = entry
        call = MagicMock()
        call.hass = hass
        call.data = {"entity_id": ["vacuum.test"], **extra}
        return call

    @staticmethod
    def _rec(i, ended, rids):
        return {"id": f"m_{i}", "ended_at": ended,
                "timeline": {"finEvents": [
                    {"type": "room", "room": {"rid": r, "status": 0}}
                    for r in rids]}}

    @pytest.mark.asyncio
    async def test_ephemeral_raises_clear_error(self):
        from custom_components.roomba_plus.services import (
            async_handle_clean_overdue_rooms,
        )
        entry = self._entry(MapCapability.EPHEMERAL)
        hass = MagicMock()
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            with pytest.raises(Exception) as exc:
                await async_handle_clean_overdue_rooms(call)
        assert "not_smart_map" in str(getattr(exc.value, "translation_key", ""))
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_nothing_overdue_is_noop_not_error(self):
        from custom_components.roomba_plus.services import (
            async_handle_clean_overdue_rooms,
        )
        # Single visit per room → insufficient_data → never overdue
        entry = self._entry(MapCapability.SMART, records=[
            self._rec(1, "2026-07-03T10:00:00+00:00", ["7", "9"]),
        ])
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            await async_handle_clean_overdue_rooms(call)  # must not raise
        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_overdue_rooms_delegated_worst_first(self):
        from custom_components.roomba_plus.services import (
            async_handle_clean_overdue_rooms,
        )
        # Kitchen: 4-day cadence, 12d since last → factor 3 (configured daily→12)
        # Hall: regular 2-day cadence, 5d since last
        recs = []
        for i, d in enumerate(["06-10", "06-14", "06-18", "06-22"]):
            recs.append(self._rec(i, f"2026-{d}T10:00:00+00:00", ["7"]))
        for i, d in enumerate(["06-23", "06-25", "06-27", "06-29"]):
            recs.append(self._rec(10 + i, f"2026-{d}T10:00:00+00:00", ["9"]))
        entry = self._entry(
            MapCapability.SMART, records=recs,
            options={"room_schedule": {"Kitchen": "daily", "Hall": "every_2_days"}},
        )
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m, \
             patch("custom_components.roomba_plus.services.dt_util") as dt_m:
            from homeassistant.util import dt as real_dt
            dt_m.now.return_value = real_dt.parse_datetime(
                "2026-07-04T10:00:00+00:00"
            )
            er_m.return_value.async_get.return_value = ent
            await async_handle_clean_overdue_rooms(call)
        hass.services.async_call.assert_awaited_once()
        args = hass.services.async_call.call_args
        payload = args[0][2]
        # Worst-first: Kitchen (factor 12) before Hall (factor 2.5)
        assert payload["room_name"] == ["Kitchen", "Hall"]

    @pytest.mark.asyncio
    async def test_max_rooms_caps_worst_first(self):
        from custom_components.roomba_plus.services import (
            async_handle_clean_overdue_rooms,
        )
        recs = []
        for i, d in enumerate(["06-10", "06-14", "06-18", "06-22"]):
            recs.append(self._rec(i, f"2026-{d}T10:00:00+00:00", ["7"]))
        for i, d in enumerate(["06-23", "06-25", "06-27", "06-29"]):
            recs.append(self._rec(10 + i, f"2026-{d}T10:00:00+00:00", ["9"]))
        entry = self._entry(
            MapCapability.SMART, records=recs,
            options={"room_schedule": {"Kitchen": "daily", "Hall": "daily"}},
        )
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        call = self._call(hass, entry, max_rooms=1)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m, \
             patch("custom_components.roomba_plus.services.dt_util") as dt_m:
            from homeassistant.util import dt as real_dt
            dt_m.now.return_value = real_dt.parse_datetime(
                "2026-07-04T10:00:00+00:00"
            )
            er_m.return_value.async_get.return_value = ent
            await async_handle_clean_overdue_rooms(call)
        payload = hass.services.async_call.call_args[0][2]
        assert payload["room_name"] == ["Kitchen"]


class TestAutoCleanDirtyRooms:
    """v3.3.0 SMART-ORDER — trust gate, whole-house fallback, dirtiest-
    first ordering, below-average exclusion."""

    def _entry(self, records, dirt_index=None, velocity=None):
        from custom_components.roomba_plus.mission_store import MissionStore
        from custom_components.roomba_plus.robot_profile_store import (
            RobotProfileStore,
        )
        entry = MagicMock()
        entry.options = {}
        data = entry.runtime_data
        data.map_capability = MapCapability.SMART
        ms = MissionStore(); ms._records = records
        data.mission_store = ms
        rps = RobotProfileStore()
        rps.room_dirt_index = dirt_index or {}
        data.robot_profile_store = rps
        data.has_cloud = True
        data.cloud_coordinator.regions = [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
        ]
        # v3.3.0 SMART-ORDER routing — explicit None: an auto-MagicMock
        # aligner would engage the route optimizer with mock garbage.
        data.umf_aligner = None
        return entry

    @staticmethod
    def _recs(rid, n):
        return [{"id": f"m_{rid}_{i}", "ended_at": f"2026-06-{10+i:02d}T10:00:00+00:00",
                 "timeline": {"finEvents": [
                     {"type": "room", "room": {"rid": rid, "status": 0}}]}}
                for i in range(n)]

    def _call(self, hass, entry, **extra):
        hass.config_entries.async_get_entry.return_value = entry
        call = MagicMock(); call.hass = hass
        call.data = {"entity_id": ["vacuum.test"], **extra}
        return call

    @pytest.mark.asyncio
    async def test_ephemeral_raises(self):
        from custom_components.roomba_plus.services import (
            async_handle_auto_clean_dirty_rooms,
        )
        entry = self._entry([])
        entry.runtime_data.map_capability = MapCapability.EPHEMERAL
        hass = MagicMock()
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            with pytest.raises(Exception) as exc:
                await async_handle_auto_clean_dirty_rooms(call)
        assert "not_smart_map" in str(getattr(exc.value, "translation_key", ""))

    @pytest.mark.asyncio
    async def test_thin_data_falls_back_to_whole_house(self):
        """Kitchen is the dirtiest room but has only 5 recorded cleanings
        — below the 10-mission trust gate → whole-house start, no
        room-targeted call."""
        from custom_components.roomba_plus.services import (
            async_handle_auto_clean_dirty_rooms,
        )
        entry = self._entry(
            self._recs("7", 5) + self._recs("9", 5),
            dirt_index={"7": 4.0, "9": 1.0},
        )
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        async def _run(func, *a): return func(*a)
        hass.async_add_executor_job = AsyncMock(side_effect=_run)
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            await async_handle_auto_clean_dirty_rooms(call)
        entry.runtime_data.roomba.start.assert_called_once()
        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dirty_rooms_targeted_dirtiest_first(self):
        from custom_components.roomba_plus.services import (
            async_handle_auto_clean_dirty_rooms,
        )
        # Both rooms above average is impossible with 2 rooms unless equal;
        # use 3 rooms: two above average, one below.
        entry = self._entry(
            self._recs("7", 12) + self._recs("9", 12) + self._recs("4", 12),
            dirt_index={"7": 4.0, "9": 3.0, "4": 0.5},
        )
        entry.runtime_data.cloud_coordinator.regions = [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
            {"id": "4", "name": "Bedroom"},
        ]
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        call = self._call(hass, entry)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            await async_handle_auto_clean_dirty_rooms(call)
        payload = hass.services.async_call.call_args[0][2]
        # avg index = 2.5 → Kitchen (4.0) and Hall (3.0) qualify, desc order;
        # Bedroom (0.5, below average) excluded despite 12 visits
        assert payload["room_name"] == ["Kitchen", "Hall"]
        entry.runtime_data.roomba.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_rooms_caps_dirtiest_first(self):
        from custom_components.roomba_plus.services import (
            async_handle_auto_clean_dirty_rooms,
        )
        entry = self._entry(
            self._recs("7", 12) + self._recs("9", 12) + self._recs("4", 12),
            dirt_index={"7": 4.0, "9": 3.0, "4": 0.5},
        )
        entry.runtime_data.cloud_coordinator.regions = [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
            {"id": "4", "name": "Bedroom"},
        ]
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        call = self._call(hass, entry, max_rooms=1)
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m:
            er_m.return_value.async_get.return_value = ent
            await async_handle_auto_clean_dirty_rooms(call)
        assert hass.services.async_call.call_args[0][2]["room_name"] == ["Kitchen"]


class TestRouteOptimizeOrder:
    """v3.3.0 SMART-ORDER routing — greedy NN from the dock over UMF room
    centroids; selection stays dirt-/overdue-driven, only the ORDER of
    the selected set changes; graceful fallback without alignment."""

    def _aligner(self, centroids, dock=(0.0, 0.0)):
        a = MagicMock()
        a.aligned = True
        a.room_centroids_umf.return_value = centroids
        a.pose_to_umf.return_value = dock
        return a

    def test_nearest_neighbour_from_dock(self):
        from custom_components.roomba_plus.services import _route_optimize_order
        data = MagicMock()
        # Dock at (0,0); Hall nearest, then Kitchen, then Bedroom —
        # input order is dirt-sorted the other way around.
        data.umf_aligner = self._aligner({
            "7": (9000.0, 0.0),    # Kitchen — far
            "9": (1000.0, 0.0),    # Hall — nearest to dock
            "4": (10000.0, 500.0), # Bedroom — next to Kitchen
        })
        region_map = {"7": "Kitchen", "9": "Hall", "4": "Bedroom"}
        out = _route_optimize_order(
            data, ["Bedroom", "Kitchen", "Hall"], region_map
        )
        assert out == ["Hall", "Kitchen", "Bedroom"]

    def test_fallback_keeps_input_order(self):
        from custom_components.roomba_plus.services import _route_optimize_order
        region_map = {"7": "Kitchen", "9": "Hall"}
        # No aligner
        data = MagicMock(); data.umf_aligner = None
        assert _route_optimize_order(data, ["Kitchen", "Hall"], region_map) == [
            "Kitchen", "Hall"]
        # Aligner present but not aligned
        data.umf_aligner = MagicMock(); data.umf_aligner.aligned = False
        assert _route_optimize_order(data, ["Kitchen", "Hall"], region_map) == [
            "Kitchen", "Hall"]
        # Aligned but dock unmappable
        data.umf_aligner = self._aligner({"7": (1.0, 1.0), "9": (2.0, 2.0)},
                                         dock=None)
        assert _route_optimize_order(data, ["Kitchen", "Hall"], region_map) == [
            "Kitchen", "Hall"]

    def test_rooms_without_centroid_appended_in_order(self):
        from custom_components.roomba_plus.services import _route_optimize_order
        data = MagicMock()
        data.umf_aligner = self._aligner({
            "7": (5000.0, 0.0), "9": (1000.0, 0.0),
        })
        region_map = {"7": "Kitchen", "9": "Hall", "4": "Bedroom"}
        out = _route_optimize_order(
            data, ["Bedroom", "Kitchen", "Hall"], region_map
        )
        # Bedroom has no centroid → keeps its slot at the END; the two
        # positioned rooms are NN-ordered from the dock.
        assert out == ["Hall", "Kitchen", "Bedroom"]


class TestSeamSensorService:
    """Bug-hunt round 4 — sensor and service must resolve room names
    identically, incl. the UMF fallback when cloud regions are empty."""

    @pytest.mark.asyncio
    async def test_overdue_service_uses_umf_fallback_like_sensor(self):
        from custom_components.roomba_plus.services import (
            async_handle_clean_overdue_rooms,
        )
        from custom_components.roomba_plus.mission_store import MissionStore
        entry = MagicMock()
        entry.options = {"room_schedule": {"Kitchen": "daily"}}
        data = entry.runtime_data
        data.map_capability = MapCapability.SMART
        ms = MissionStore()
        # Kitchen (rid 7): 4 visits, 5 days since last → overdue at daily
        ms._records = [
            {"id": f"m_{i}", "ended_at": f"2026-06-{20+i*2:02d}T10:00:00+00:00",
             "timeline": {"finEvents": [
                 {"type": "room", "room": {"rid": "7", "status": 0}}]}}
            for i in range(4)
        ]
        data.mission_store = ms
        # Cloud regions EMPTY (degraded window) — UMF aligner resolves
        data.has_cloud = True
        data.cloud_coordinator.regions = []
        data.umf_aligner.aligned = True
        data.umf_aligner.rid_to_name.return_value = {"7": "Kitchen"}
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        hass.config_entries.async_get_entry.return_value = entry
        call = MagicMock(); call.hass = hass
        call.data = {"entity_id": ["vacuum.test"]}
        ent = MagicMock(); ent.config_entry_id = "ce1"
        with patch("custom_components.roomba_plus.services.er.async_get") as er_m, \
             patch("custom_components.roomba_plus.services.dt_util") as dt_m:
            from homeassistant.util import dt as real_dt
            dt_m.now.return_value = real_dt.parse_datetime(
                "2026-07-04T10:00:00+00:00")
            er_m.return_value.async_get.return_value = ent
            await async_handle_clean_overdue_rooms(call)
        # Without the fix: merged rooms keyed by raw rid, config key
        # "Kitchen" never matches → learned/insufficient → no-op.
        hass.services.async_call.assert_awaited_once()
        assert hass.services.async_call.call_args[0][2]["room_name"] == ["Kitchen"]


# ── v3.5.0 FULL-BACKUP ────────────────────────────────────────────────────────


def _make_backup_hass(tmp_path):
    """A hass double whose async_add_executor_job really runs the blocking
    fn against a real (tmp_path-backed) filesystem — the ZIP read/write
    logic is exercised for real, not mocked away."""
    hass = MagicMock()
    hass.config.path.side_effect = lambda name: str(tmp_path / name)

    async def _run_executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)
    return hass


def _make_backup_config_entry(entry_id="e1", blid="ABC123"):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.runtime_data.blid = blid
    entry.runtime_data.map_capability.value = "EPHEMERAL"
    return entry


def _wire_entity_lookup(hass, config_entry):
    ent = MagicMock()
    ent.config_entry_id = config_entry.entry_id
    er_patcher = patch(
        "custom_components.roomba_plus.services.er.async_get",
        return_value=MagicMock(async_get=MagicMock(return_value=ent)),
    )
    hass.config_entries.async_get_entry.return_value = config_entry
    return er_patcher


class TestCreateBackup:
    @pytest.mark.asyncio
    async def test_writes_zip_with_available_stores(self, tmp_path):
        from custom_components.roomba_plus.services import async_handle_create_backup

        hass = _make_backup_hass(tmp_path)
        entry = _make_backup_config_entry()

        store_data = {
            "roomba_plus_missions_e1": {"records": [1, 2, 3]},
            "roomba_plus_grid_e1": {"cells": {}},
        }

        async def fake_load(self):
            return store_data.get(self.key)

        with _wire_entity_lookup(hass, entry), patch(
            "custom_components.roomba_plus.services.Store.async_load", fake_load
        ):
            call = MagicMock()
            call.data = {"entity_id": "vacuum.test"}
            result = await async_handle_create_backup(hass, call)

        assert "mission_store" in result["included_stores"]
        assert "grid_store" in result["included_stores"]
        assert "maintenance_store" in result["excluded_stores_no_data"]
        assert Path(result["path"]).is_file()

        import zipfile
        with zipfile.ZipFile(result["path"]) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "roomba_plus_missions.json" in names
            assert "roomba_plus_maintenance.json" not in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["robot"]["blid"] == "ABC123"

    @pytest.mark.asyncio
    async def test_unknown_entity_raises(self, tmp_path):
        from custom_components.roomba_plus.services import async_handle_create_backup
        from homeassistant.exceptions import ServiceValidationError

        hass = _make_backup_hass(tmp_path)
        with patch(
            "custom_components.roomba_plus.services.er.async_get",
            return_value=MagicMock(async_get=MagicMock(return_value=None)),
        ):
            call = MagicMock()
            call.data = {"entity_id": "vacuum.nonexistent"}
            with pytest.raises(ServiceValidationError):
                await async_handle_create_backup(hass, call)


class TestRestoreBackup:
    @pytest.mark.asyncio
    async def test_restores_and_reloads_live_stores(self, tmp_path):
        from custom_components.roomba_plus.services import (
            async_handle_create_backup,
            async_handle_restore_backup,
        )

        hass = _make_backup_hass(tmp_path)
        entry = _make_backup_config_entry()

        store_data = {"roomba_plus_missions_e1": {"records": [1, 2, 3]}}

        async def fake_load(self):
            return store_data.get(self.key)

        saved: dict[str, dict] = {}

        async def fake_save(self, data):
            saved[self.key] = data

        with _wire_entity_lookup(hass, entry), patch(
            "custom_components.roomba_plus.services.Store.async_load", fake_load
        ), patch(
            "custom_components.roomba_plus.services.Store.async_save", fake_save
        ):
            create_call = MagicMock()
            create_call.data = {"entity_id": "vacuum.test"}
            created = await async_handle_create_backup(hass, create_call)

            entry.runtime_data.mission_store = MagicMock()
            entry.runtime_data.mission_store.async_load = AsyncMock()

            restore_call = MagicMock()
            restore_call.data = {
                "entity_id": "vacuum.test",
                "path": created["path"],
            }
            result = await async_handle_restore_backup(hass, restore_call)

        assert "mission_store" in result["restored_stores"]
        assert "roomba_plus_missions_e1" in saved
        assert saved["roomba_plus_missions_e1"] == {"records": [1, 2, 3]}
        entry.runtime_data.mission_store.async_load.assert_awaited_once_with(
            hass, "e1"
        )

    @pytest.mark.asyncio
    async def test_missing_file_raises(self, tmp_path):
        from custom_components.roomba_plus.services import async_handle_restore_backup
        from homeassistant.exceptions import HomeAssistantError

        hass = _make_backup_hass(tmp_path)
        entry = _make_backup_config_entry()
        with _wire_entity_lookup(hass, entry):
            call = MagicMock()
            call.data = {
                "entity_id": "vacuum.test",
                "path": str(tmp_path / "does_not_exist.zip"),
            }
            with pytest.raises(HomeAssistantError):
                await async_handle_restore_backup(hass, call)

    @pytest.mark.asyncio
    async def test_corrupt_zip_raises(self, tmp_path):
        from custom_components.roomba_plus.services import async_handle_restore_backup
        from homeassistant.exceptions import HomeAssistantError

        bad_zip = tmp_path / "corrupt.zip"
        bad_zip.write_bytes(b"not actually a zip file")

        hass = _make_backup_hass(tmp_path)
        entry = _make_backup_config_entry()
        with _wire_entity_lookup(hass, entry):
            call = MagicMock()
            call.data = {"entity_id": "vacuum.test", "path": str(bad_zip)}
            with pytest.raises(HomeAssistantError):
                await async_handle_restore_backup(hass, call)
