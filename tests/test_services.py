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
