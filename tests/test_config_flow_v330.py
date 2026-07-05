"""v3.3.0 bug-hunt round 4 — the two NEW options-flow steps.

Deliberately a separate file: the legacy test_config_flow.py is
environment-excluded from the standard run, which left every new flow
step untested. These tests construct the flow handler bare
(object.__new__) with light mocks, avoiding the legacy fixtures.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from custom_components.roomba_plus.config_flow import RoombaPlusOptionsFlow
from custom_components.roomba_plus.const import (
    CONF_CORRELATION_ENTITIES,
    CONF_ROOM_SCHEDULE,
)


def _flow(options=None, regions=None, capability="smart", has_cloud=True):
    flow = object.__new__(RoombaPlusOptionsFlow)
    entry = MagicMock()
    entry.options = options or {}
    data = entry.runtime_data
    data.map_capability.value = capability
    data.has_cloud = has_cloud
    if not has_cloud:
        data.cloud_coordinator = None
    else:
        data.cloud_coordinator.regions = regions if regions is not None else [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
        ]
    # OptionsFlow.config_entry is a property in recent HA — each test
    # patches it at class level via patch.object(..., new=entry).
    return flow, entry


class TestRoomScheduleStep:
    def _make(self, **kw):
        flow, entry = _flow(**kw)
        # Patch the property at class level per-test via context manager
        return flow, entry

    @pytest.mark.asyncio
    async def test_form_shows_selector_per_room_with_current_defaults(self):
        flow, entry = self._make(
            options={CONF_ROOM_SCHEDULE: {"Kitchen": "daily"}}
        )
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "form"
        schema_keys = {k.schema: k.default() for k in result["data_schema"].schema}
        assert schema_keys == {"Hall": "learned", "Kitchen": "daily"}

    @pytest.mark.asyncio
    async def test_save_filters_orphans_and_learned(self):
        """Orphan filter: a configured room that vanished from the cloud
        map is dropped on save; 'learned' entries are not stored."""
        flow, entry = self._make(
            options={CONF_ROOM_SCHEDULE: {"Ghost": "weekly"}}
        )
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(
                {"Kitchen": "every_2_days", "Hall": "learned",
                 "Ghost": "weekly"}
            )
        assert result["type"].value == "create_entry"
        assert result["data"][CONF_ROOM_SCHEDULE] == {
            "Kitchen": "every_2_days"
        }

    @pytest.mark.asyncio
    async def test_gate_aborts_without_smart_cloud(self):
        flow, entry = self._make(capability="ephemeral", has_cloud=False)
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "abort"
        assert result["reason"] == "room_schedule_not_supported"

    @pytest.mark.asyncio
    async def test_abort_without_named_rooms(self):
        flow, entry = self._make(regions=[])
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "abort"
        assert result["reason"] == "room_schedule_no_rooms"


class TestSettingsCorrelationField:
    @pytest.mark.asyncio
    async def test_settings_schema_contains_correlation_entities(self):
        flow, entry = _flow(options={CONF_CORRELATION_ENTITIES: ["sensor.h"]})
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_settings(None)
        assert result["type"].value == "form"
        keys = {k.schema: k.default() for k in result["data_schema"].schema}
        assert keys[CONF_CORRELATION_ENTITIES] == ["sensor.h"]

    @pytest.mark.asyncio
    async def test_settings_save_persists_correlation_entities(self):
        flow, entry = _flow()
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_settings(
                {CONF_CORRELATION_ENTITIES: ["sensor.humidity"]}
            )
        assert result["type"].value == "create_entry"
        assert result["data"][CONF_CORRELATION_ENTITIES] == ["sensor.humidity"]
