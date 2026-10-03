"""Event entities for mission end, room end and stuck (4.2.19).

A non-admin user cannot subscribe to the integration's bus events, so a
card opened by one never heard a mission end (I4 of the card plan). The
event entities carry the same moments as ordinary state.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus import event as event_mod
from custom_components.roomba_plus.const import (
    EVENT_MISSION_COMPLETED,
    EVENT_ROOM_COMPLETED,
    EVENT_STUCK,
)
from custom_components.roomba_plus.models import ConnectionType
from tests.conftest import entry_mock


def _entry(connection_type=ConnectionType.LOCAL_PUSH, entry_id="E1"):
    entry = entry_mock()
    entry.entry_id = entry_id
    entry.runtime_data.connection_type = connection_type
    entry.runtime_data.blid = "BLID1"
    entry.runtime_data.roomba = None
    return entry


async def _created(entry):
    created = []
    await event_mod.async_setup_entry(
        MagicMock(), entry, lambda ents, **kw: created.extend(ents)
    )
    return {e._attr_translation_key: e for e in created}


class TestWhichEntitiesExist:
    @pytest.mark.asyncio
    async def test_classic_gets_all_three(self):
        created = await _created(_entry())
        assert set(created) == {"mission_completed", "room_completed", "stuck"}

    @pytest.mark.asyncio
    async def test_prime_gets_no_stuck_entity_it_could_never_fire(self):
        created = await _created(_entry(ConnectionType.CLOUD_ONLY))
        assert set(created) == {"mission_completed", "room_completed"}

    @pytest.mark.asyncio
    async def test_ids_and_types(self):
        created = await _created(_entry())
        for key, entity in created.items():
            assert entity.unique_id == f"roomba_plus_BLID1_{key}"
            assert entity.suggested_object_id == key
            assert entity.event_types == [key]


class TestTheyFollowTheBus:
    @staticmethod
    def _event(data):
        return SimpleNamespace(data=data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("key", "bus_event"),
        [
            ("mission_completed", EVENT_MISSION_COMPLETED),
            ("room_completed", EVENT_ROOM_COMPLETED),
            ("stuck", EVENT_STUCK),
        ],
    )
    async def test_this_robots_event_sets_state_and_attributes(self, key, bus_event):
        entity = (await _created(_entry()))[key]
        assert entity._bus_event == bus_event
        entity.async_write_ha_state = MagicMock()
        assert entity.state is None

        entity._on_bus_event(self._event({"entry_id": "E1", "result": "completed"}))

        assert entity.state is not None
        assert entity.state_attributes == {
            "event_type": key, "entry_id": "E1", "result": "completed",
        }
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_another_robots_event_is_ignored(self):
        entity = (await _created(_entry()))["mission_completed"]
        entity.async_write_ha_state = MagicMock()

        entity._on_bus_event(self._event({"entry_id": "OTHER"}))
        entity._on_bus_event(self._event({}))

        assert entity.state is None
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_robot_message_never_writes_state(self):
        entity = (await _created(_entry()))["stuck"]
        assert entity.new_state_filter({"batPct": 50, "cleanMissionStatus": {}}) is False

    @pytest.mark.asyncio
    async def test_it_listens_once_added(self):
        entity = (await _created(_entry()))["room_completed"]
        entity.hass = MagicMock()
        entity.async_on_remove = MagicMock()
        await event_mod.RoombaMomentEvent.async_added_to_hass(entity)
        entity.hass.bus.async_listen.assert_any_call(
            EVENT_ROOM_COMPLETED, entity._on_bus_event
        )
