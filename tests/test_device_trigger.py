"""v2.9.0 TRIGGER+ — device_trigger.py tests.

No tests existed for this file before TRIGGER+ (verified: zero references
to device_trigger anywhere in tests/ prior to this file). Coverage here
focuses on the 6 new v2.9.0 trigger types; the pre-existing ones
(cleaning_started, stuck, etc.) are exercised only via async_get_triggers()
smoke coverage, since reproducing their full state_trigger integration
behaviour would require the real-hass machinery this test suite
deliberately avoids (see conftest.py / mission_timer_store test comments
on async_test_home_assistant() event-loop corruption).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus.const import DOMAIN
from custom_components.roomba_plus.device_trigger import (
    TRIGGER_FIRMWARE_UPDATED,
    TRIGGER_HEALTH_SCORE_DROP,
    TRIGGER_MAINTENANCE_DUE,
    TRIGGER_MAP_RETRAIN_COMPLETED,
    TRIGGER_MAP_RETRAIN_STARTED,
    TRIGGER_ROOM_COMPLETED,
    TRIGGER_TYPES,
    _entry_id_for_device,
    async_attach_trigger,
    async_get_triggers,
)


def _make_device(identifiers=None, primary_config_entry="entry_abc", config_entries=None):
    device = MagicMock()
    device.identifiers = identifiers or {(DOMAIN, "blid123")}
    device.primary_config_entry = primary_config_entry
    device.config_entries = config_entries or {primary_config_entry}
    return device


def _make_hass_with_device(device):
    hass = MagicMock()
    dev_reg = MagicMock()
    dev_reg.async_get.return_value = device
    with patch(
        "custom_components.roomba_plus.device_trigger.dr.async_get",
        return_value=dev_reg,
    ):
        yield hass


def _trigger_info(trigger_id: str = "trig1") -> dict:
    return {
        "trigger_data": {"id": trigger_id, "idx": "0", "alias": None},
        "variables": {},
    }


class TestEntryIdForDevice:
    def test_returns_primary_config_entry(self):
        device = _make_device(primary_config_entry="entry_abc")
        dev_reg = MagicMock()
        dev_reg.async_get.return_value = device
        with patch(
            "custom_components.roomba_plus.device_trigger.dr.async_get",
            return_value=dev_reg,
        ):
            assert _entry_id_for_device(MagicMock(), "dev1") == "entry_abc"

    def test_falls_back_to_config_entries_set(self):
        device = _make_device(primary_config_entry=None, config_entries={"entry_xyz"})
        dev_reg = MagicMock()
        dev_reg.async_get.return_value = device
        with patch(
            "custom_components.roomba_plus.device_trigger.dr.async_get",
            return_value=dev_reg,
        ):
            assert _entry_id_for_device(MagicMock(), "dev1") == "entry_xyz"

    def test_returns_none_when_device_missing(self):
        dev_reg = MagicMock()
        dev_reg.async_get.return_value = None
        with patch(
            "custom_components.roomba_plus.device_trigger.dr.async_get",
            return_value=dev_reg,
        ):
            assert _entry_id_for_device(MagicMock(), "dev1") is None


class TestAsyncGetTriggers:
    @pytest.mark.asyncio
    async def test_v290_triggers_included(self):
        device = _make_device()
        dev_reg = MagicMock()
        dev_reg.async_get.return_value = device
        with patch(
            "custom_components.roomba_plus.device_trigger.dr.async_get",
            return_value=dev_reg,
        ):
            triggers = await async_get_triggers(MagicMock(), "dev1")
        types = {t["type"] for t in triggers}
        assert {
            TRIGGER_ROOM_COMPLETED, TRIGGER_MAINTENANCE_DUE,
            TRIGGER_HEALTH_SCORE_DROP, TRIGGER_MAP_RETRAIN_STARTED,
            TRIGGER_MAP_RETRAIN_COMPLETED, TRIGGER_FIRMWARE_UPDATED,
        }.issubset(types)

    @pytest.mark.asyncio
    async def test_non_roomba_device_returns_empty(self):
        device = _make_device(identifiers={("other_domain", "x")})
        dev_reg = MagicMock()
        dev_reg.async_get.return_value = device
        with patch(
            "custom_components.roomba_plus.device_trigger.dr.async_get",
            return_value=dev_reg,
        ):
            triggers = await async_get_triggers(MagicMock(), "dev1")
        assert triggers == []

    def test_all_v290_types_are_registered(self):
        assert TRIGGER_ROOM_COMPLETED in TRIGGER_TYPES
        assert TRIGGER_MAINTENANCE_DUE in TRIGGER_TYPES
        assert TRIGGER_HEALTH_SCORE_DROP in TRIGGER_TYPES
        assert TRIGGER_MAP_RETRAIN_STARTED in TRIGGER_TYPES
        assert TRIGGER_MAP_RETRAIN_COMPLETED in TRIGGER_TYPES
        assert TRIGGER_FIRMWARE_UPDATED in TRIGGER_TYPES


class TestStateBasedV290Triggers:
    """maintenance_due / firmware_updated delegate to state_trigger, same
    shape as the pre-existing bin_full trigger."""

    @pytest.mark.asyncio
    async def test_maintenance_due_uses_correct_entity_and_state(self):
        from custom_components.roomba_plus import device_trigger as dt_mod

        with patch.object(dt_mod, "_find_entity", return_value="binary_sensor.x_maintenance_due"), \
             patch.object(dt_mod.state_trigger, "async_attach_trigger") as mock_attach:
            mock_attach.return_value = lambda: None
            await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_MAINTENANCE_DUE},
                MagicMock(),
                _trigger_info(),
            )

        called_config = mock_attach.call_args[0][1]
        assert called_config["entity_id"] == ["binary_sensor.x_maintenance_due"]
        assert called_config["to"] == "on"

    @pytest.mark.asyncio
    async def test_firmware_updated_uses_correct_entity_and_state(self):
        from custom_components.roomba_plus import device_trigger as dt_mod

        with patch.object(dt_mod, "_find_entity", return_value="binary_sensor.x_firmware_updated"), \
             patch.object(dt_mod.state_trigger, "async_attach_trigger") as mock_attach:
            mock_attach.return_value = lambda: None
            await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_FIRMWARE_UPDATED},
                MagicMock(),
                _trigger_info(),
            )

        called_config = mock_attach.call_args[0][1]
        assert called_config["entity_id"] == ["binary_sensor.x_firmware_updated"]
        assert called_config["to"] == "on"

    @pytest.mark.asyncio
    async def test_maintenance_due_no_entity_returns_noop(self):
        from custom_components.roomba_plus import device_trigger as dt_mod

        with patch.object(dt_mod, "_find_entity", return_value=None):
            detach = await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_MAINTENANCE_DUE},
                MagicMock(),
                _trigger_info(),
            )
        assert detach() is None  # no-op detach callable, doesn't raise


class TestEventBasedV290Triggers:
    """room_completed / map_retrain_started / map_retrain_completed
    delegate to HA's built-in event trigger platform with an
    event_data={"entry_id": ...} exact-match filter."""

    @pytest.mark.asyncio
    async def test_room_completed_filters_by_entry_id(self):
        from custom_components.roomba_plus import device_trigger as dt_mod
        from custom_components.roomba_plus.const import EVENT_ROOM_COMPLETED

        with patch.object(dt_mod, "_entry_id_for_device", return_value="entry_abc"), \
             patch.object(dt_mod.event_trigger, "async_attach_trigger") as mock_attach:
            mock_attach.return_value = lambda: None
            await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_ROOM_COMPLETED},
                MagicMock(),
                _trigger_info(),
            )

        called_config = mock_attach.call_args[0][1]
        assert [t.template for t in called_config["event_type"]] == [EVENT_ROOM_COMPLETED]
        assert called_config["event_data"] == {"entry_id": "entry_abc"}

    @pytest.mark.asyncio
    async def test_map_retrain_started_filters_by_entry_id(self):
        from custom_components.roomba_plus import device_trigger as dt_mod
        from custom_components.roomba_plus.const import EVENT_MAP_RETRAIN_STARTED

        with patch.object(dt_mod, "_entry_id_for_device", return_value="entry_abc"), \
             patch.object(dt_mod.event_trigger, "async_attach_trigger") as mock_attach:
            mock_attach.return_value = lambda: None
            await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_MAP_RETRAIN_STARTED},
                MagicMock(),
                _trigger_info(),
            )

        called_config = mock_attach.call_args[0][1]
        assert [t.template for t in called_config["event_type"]] == [EVENT_MAP_RETRAIN_STARTED]

    @pytest.mark.asyncio
    async def test_no_entry_id_returns_noop(self):
        from custom_components.roomba_plus import device_trigger as dt_mod

        with patch.object(dt_mod, "_entry_id_for_device", return_value=None):
            detach = await async_attach_trigger(
                MagicMock(),
                {"device_id": "dev1", "type": TRIGGER_ROOM_COMPLETED},
                MagicMock(),
                _trigger_info(),
            )
        assert detach() is None


class TestHealthScoreDropTrigger:
    """v2.9.0 — custom listener (not the event trigger platform) since it
    needs the ordinal "did it get worse" check exact-match can't express.
    """

    async def _attach(self, hass, entry_id="entry_abc"):
        from custom_components.roomba_plus import device_trigger as dt_mod

        action = MagicMock()
        with patch.object(dt_mod, "_entry_id_for_device", return_value=entry_id):
            await async_attach_trigger(
                hass,
                {"device_id": "dev1", "type": TRIGGER_HEALTH_SCORE_DROP},
                action,
                _trigger_info(),
            )
        # Capture the listener registered on hass.bus.async_listen
        assert hass.bus.async_listen.call_count == 1
        registered_event_type, listener = hass.bus.async_listen.call_args[0][:2]
        return action, registered_event_type, listener

    @pytest.mark.asyncio
    async def test_fires_action_on_genuine_drop(self):
        from custom_components.roomba_plus.const import EVENT_HEALTH_CHANGE

        hass = MagicMock()
        action, event_type, listener = await self._attach(hass)
        assert event_type == EVENT_HEALTH_CHANGE

        event = MagicMock()
        event.data = {
            "entry_id": "entry_abc", "band": "critical",
            "previous_band": "healthy", "score": 30, "previous_score": 90,
        }
        listener(event)

        assert hass.async_run_hass_job.call_count == 1

    @pytest.mark.asyncio
    async def test_no_action_when_band_improves(self):
        hass = MagicMock()
        _, _, listener = await self._attach(hass)

        event = MagicMock()
        event.data = {
            "entry_id": "entry_abc", "band": "healthy",
            "previous_band": "critical", "score": 90, "previous_score": 30,
        }
        listener(event)

        hass.async_run_hass_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_action_when_band_unchanged(self):
        hass = MagicMock()
        _, _, listener = await self._attach(hass)

        event = MagicMock()
        event.data = {
            "entry_id": "entry_abc", "band": "healthy",
            "previous_band": "healthy", "score": 95, "previous_score": 90,
        }
        listener(event)

        hass.async_run_hass_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_events_for_other_robots(self):
        """Multi-robot install — a health_change event for a DIFFERENT
        entry_id must not fire this device's trigger."""
        hass = MagicMock()
        _, _, listener = await self._attach(hass, entry_id="entry_abc")

        event = MagicMock()
        event.data = {
            "entry_id": "some_other_robot", "band": "critical",
            "previous_band": "healthy", "score": 10, "previous_score": 90,
        }
        listener(event)

        hass.async_run_hass_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_entry_id_returns_noop(self):
        from custom_components.roomba_plus import device_trigger as dt_mod

        hass = MagicMock()
        with patch.object(dt_mod, "_entry_id_for_device", return_value=None):
            detach = await async_attach_trigger(
                hass,
                {"device_id": "dev1", "type": TRIGGER_HEALTH_SCORE_DROP},
                MagicMock(),
                _trigger_info(),
            )
        assert detach() is None
        hass.bus.async_listen.assert_not_called()
