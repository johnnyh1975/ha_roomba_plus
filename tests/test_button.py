"""Tests for button.py — currently just ZoneCleanButton (ROOM-SEG Stage 3).

No test_button.py existed before this; button.py had zero test coverage
across the project. Scoped here to the one class touched by the
ZoneStore -> RoomSegStore swap, not a full audit of every button class.
"""
from unittest.mock import MagicMock, AsyncMock

import pytest

from custom_components.roomba_plus.button import ZoneCleanButton
from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom


def _make_button(room_seg_store):
    entity = ZoneCleanButton.__new__(ZoneCleanButton)
    config_entry = MagicMock()
    config_entry.runtime_data.room_seg_store = room_seg_store
    config_entry.data = {"blid": "test_blid"}
    entity._config_entry = config_entry
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock()
    entity.vacuum = MagicMock()

    # No selection made in tests below -- entity_registry lookup returns
    # nothing, so async_press falls back to the first confirmed room.
    fake_ent_reg = MagicMock()
    fake_ent_reg.async_get_entity_id.return_value = None
    import custom_components.roomba_plus.button as button_mod
    return entity, fake_ent_reg, button_mod


class TestZoneCleanButtonNoRooms:
    @pytest.mark.asyncio
    async def test_no_room_seg_store_logs_warning_and_returns(self, caplog):
        entity, _, _ = _make_button(None)
        with caplog.at_level("WARNING"):
            await entity.async_press()
        assert "no rooms available" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_room_seg_store_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            await entity.async_press()
        assert "no rooms available" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_confirmed_rooms_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="", confirmed=False)}
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            await entity.async_press()
        assert "no confirmed rooms" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()


class TestZoneCleanButtonStartsClean:
    @pytest.mark.asyncio
    async def test_confirmed_room_present_sends_start_command(self, monkeypatch):
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(
                id="room_1", name="Kitchen", confirmed=True,
                cells={(0, 0), (1, 0), (0, 1), (1, 1)},
            ),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)

        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        await entity.async_press()

        entity.hass.async_add_executor_job.assert_called_once_with(
            entity.vacuum.send_command, "start"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_first_confirmed_room_without_selection(self, monkeypatch):
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True),
            "room_2": SegRoom(id="room_2", name="Bedroom", confirmed=True),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)
        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        # Should not raise even with no selected-zone state available.
        await entity.async_press()
        entity.hass.async_add_executor_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_room_bbox_not_zone_attribute_names(self, monkeypatch, caplog):
        """Regression check for the ROOM-SEG Stage 3 swap: the log
        message must read room.bbox (a SegRoom property), not the old
        zone.x_min/y_min/x_max/y_max ZoneStore.Zone attributes."""
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(
                id="room_1", name="Kitchen", confirmed=True,
                cells={(0, 0), (1, 0), (0, 1), (1, 1)},
            ),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)
        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        with caplog.at_level("INFO"):
            await entity.async_press()

        assert "Kitchen" in caplog.text
        assert "bbox" in caplog.text.lower()
