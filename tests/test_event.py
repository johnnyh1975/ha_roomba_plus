"""Event entities for mission end, room end and stuck (4.2.19).

A non-admin user cannot subscribe to the integration's bus events, so a
card opened by one never heard a mission end (I4 of the card plan). The
event entities carry the same moments as ordinary state.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus import event as event_mod
from custom_components.roomba_plus.const import (
    EVENT_MISSION_COMPLETED,
    EVENT_ROOM_COMPLETED,
    EVENT_STUCK,
)
from custom_components.roomba_plus.models import ConnectionType
from tests.conftest import entry_mock


def _entry(connection_type=ConnectionType.LOCAL_PUSH, entry_id="E1", maintenance=None):
    entry = entry_mock()
    entry.entry_id = entry_id
    entry.runtime_data.connection_type = connection_type
    entry.runtime_data.blid = "BLID1"
    entry.runtime_data.roomba = None
    entry.runtime_data.maintenance = maintenance
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
        assert set(created) == {"mission_completed", "room_completed", "stuck", "command", "mission_result"}

    @pytest.mark.asyncio
    async def test_prime_gets_the_same_moments(self):
        """`stuck` too since 4.6.0: a Prime robot reports the phase, and
        PrimeStatusCoordinator fires the event (test_prime_coordinator)."""
        created = await _created(_entry(ConnectionType.CLOUD_ONLY))
        assert set(created) == {
            "mission_completed", "room_completed", "stuck", "command", "mission_result",
        }

    @pytest.mark.asyncio
    async def test_ids_and_types(self):
        created = await _created(_entry())
        for key, entity in created.items():
            assert entity.unique_id == f"roomba_plus_BLID1_{key}"
            assert entity.suggested_object_id == key
            if key == "command":
                assert entity.event_types == [
                    "not_delivered", "state_frozen", "start_blocked", "start_timeout",
                ]
            elif key == "mission_result":
                assert entity.event_types == [
                    "completed", "completed_with_skips", "cancelled",
                    "stuck_abandoned", "error",
                ]
            else:
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


class TestTheMaintenanceEvent:
    """4.5.0: one entity, four event types, the entry as its data."""

    @pytest.mark.asyncio
    async def test_both_generations_get_it_with_maintenance(self):
        for kind in (ConnectionType.LOCAL_PUSH, ConnectionType.CLOUD_ONLY):
            created = await _created(_entry(kind, maintenance=object()))
            entity = created["maintenance"]
            assert entity.unique_id == "roomba_plus_BLID1_maintenance"
            assert entity.event_types == ["due", "overdue", "done", "snoozed"]

    @pytest.mark.asyncio
    async def test_none_without_maintenance(self):
        assert "maintenance" not in await _created(_entry())

    @pytest.mark.asyncio
    async def test_it_carries_the_entry_it_is_sent(self):
        entity = (await _created(_entry(maintenance=object())))["maintenance"]
        entity.async_write_ha_state = MagicMock()
        entity._on_maintenance("due", {"entry_id": "E1", "id": "replace.filter", "level": "due"})
        assert entity.state_attributes == {
            "event_type": "due", "entry_id": "E1", "id": "replace.filter", "level": "due",
        }
        entity.async_write_ha_state.assert_called_once()
        assert entity.new_state_filter({"batPct": 5}) is False

    @pytest.mark.asyncio
    async def test_it_listens_once_added(self):
        from custom_components.roomba_plus.const import maintenance_event_signal

        entity = (await _created(_entry(maintenance=object())))["maintenance"]
        entity.hass = MagicMock()
        entity.async_on_remove = MagicMock()
        connected = []
        with patch.object(
            event_mod, "async_dispatcher_connect",
            lambda hass, signal, target: connected.append((signal, target)),
        ), patch.object(
            event_mod.IRobotEntity, "async_added_to_hass", _noop_method,
        ):
            await entity.async_added_to_hass()
        assert (maintenance_event_signal("BLID1"), entity._on_maintenance) in connected


async def _noop_method(self):
    return None


class TestTheMaintenanceBlueprint:
    """blueprints/automation/roomba_plus_maintenance.yaml (4.5.0, b1),
    run in Home Assistant against the maintenance event's attributes."""

    async def _automation(self, hass, **extra_input):
        import shutil
        from pathlib import Path

        from homeassistant.setup import async_setup_component
        from pytest_homeassistant_custom_component.common import async_mock_service

        source = Path(__file__).parent.parent / "blueprints" / "automation" / "roomba_plus_maintenance.yaml"
        target = Path(hass.config.path("blueprints", "automation", "roomba_plus", source.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        sent = async_mock_service(hass, "notify", "phone")
        done = async_mock_service(hass, "roomba_plus", "maintenance_done")
        later = async_mock_service(hass, "roomba_plus", "maintenance_snooze")
        hass.states.async_set("event.robo_maintenance", "2026-10-17T10:00:00+00:00", {})
        assert await async_setup_component(hass, "automation", {"automation": {
            "use_blueprint": {"path": f"roomba_plus/{source.name}", "input": {
                "maintenance_event": "event.robo_maintenance",
                "vacuum_entity": "vacuum.robo",
                "notify_action": [{"action": "notify.phone", "data": {
                    "title": "{{ notification_title }}",
                    "message": "{{ notification_message }}",
                    "data": {"url": "{{ notification_url }}",
                             "actions": "{{ notification_actions }}"},
                }}],
                # Any time, unless a test says otherwise.
                "send_from": "00:00:00",
                "send_until": "00:00:00",
                **extra_input,
            }},
        }})
        await hass.async_block_till_done()
        return sent, done, later

    def _fire(self, hass, when, **attrs):
        payload = {"event_type": "due", "id": "replace.filter", "name": "Robo",
                   "guide": "/api/roomba_plus/care/E1/replace_filter?sig=x",
                   "guide_title": "Replace the filter", "error": None, **attrs}
        hass.states.async_set("event.robo_maintenance", when, payload)

    async def test_due_is_sent_with_the_guide_and_two_buttons(self, hass):
        sent, _done, _later = await self._automation(hass)
        self._fire(hass, "2026-10-17T11:00:00+00:00")
        await hass.async_block_till_done()
        assert len(sent) == 1
        data = sent[0].data
        assert data["title"] == "Robo"
        assert data["message"] == "Replace the filter is due"
        assert data["data"]["url"] == "/api/roomba_plus/care/E1/replace_filter?sig=x"
        assert [a["action"] for a in data["data"]["actions"]] == [
            "ROOMBA_PLUS_DONE|vacuum.robo|replace.filter|replace",
            "ROOMBA_PLUS_LATER|vacuum.robo|replace.filter",
            "URI",
        ]
        assert [a["title"] for a in data["data"]["actions"]] == [
            "Replaced", "Remind me later", "How to do it",
        ]
        assert data["data"]["actions"][2]["uri"] == "/api/roomba_plus/care/E1/replace_filter?sig=x"

    async def test_the_buttons_by_kind_of_entry_three_at_most(self, hass):
        sent, _done, _later = await self._automation(hass)
        self._fire(hass, "2026-10-17T11:00:00+00:00", can_clean=True)
        self._fire(hass, "2026-10-17T12:00:00+00:00", id="clean.wheel", guide_title="Clean the wheels")
        self._fire(hass, "2026-10-17T13:00:00+00:00", id="check.bin", guide=None)
        await hass.async_block_till_done()
        titles = [[a["title"] for a in c.data["data"]["actions"]] for c in sent]
        assert titles == [
            ["Replaced", "Only cleaned", "Remind me later"],
            ["Cleaned", "Remind me later", "How to do it"],
            ["Cleaned", "Remind me later"],
        ]
        assert sent[0].data["data"]["actions"][1]["action"] == (
            "ROOMBA_PLUS_DONE|vacuum.robo|replace.filter|clean"
        )

    @pytest.mark.parametrize("hour,sent_now", [(23, False), (7, False), (12, True)])
    async def test_only_within_the_hours(self, hass, freezer, hour, sent_now):
        """Outside 08:00-21:00 the run waits for 08:00 (Home Assistant's
        own wait_for_trigger); within, it sends at once."""
        import asyncio
        from datetime import datetime

        from homeassistant.util import dt as dt_util

        freezer.move_to(datetime(2026, 10, 17, hour, 30, tzinfo=dt_util.DEFAULT_TIME_ZONE))
        sent, _done, _later = await self._automation(
            hass, send_from="08:00:00", send_until="21:00:00",
        )
        self._fire(hass, "2026-10-17T21:30:00+00:00")
        # A waiting run would hold block_till_done: let the loop turn.
        for _ in range(30):
            await asyncio.sleep(0)
        assert (len(sent) == 1) is sent_now
        # Stop a waiting run, so the test can end.
        await hass.services.async_call(
            "automation", "turn_off", {"entity_id": "all"}, blocking=True,
        )

    async def test_an_error_and_the_wording_and_what_is_not_chosen(self, hass):
        sent, _done, _later = await self._automation(
            hass, due_text="ist fällig", error_text="Fehler",
        )
        self._fire(hass, "2026-10-17T11:00:00+00:00", id="check.bin", error=14,
                   guide_title="Behälter leeren")
        self._fire(hass, "2026-10-17T12:00:00+00:00", event_type="done")
        await hass.async_block_till_done()
        assert [c.data["message"] for c in sent] == ["Fehler 14: Behälter leeren ist fällig"]

    async def test_without_a_guide_the_entry_names_itself(self, hass):
        sent, _done, _later = await self._automation(hass)
        self._fire(hass, "2026-10-17T11:00:00+00:00", event_type="overdue",
                   id="replace.part_202", guide=None, guide_title=None)
        await hass.async_block_till_done()
        assert sent[0].data["message"] == "replace.part_202 is overdue"
        assert sent[0].data["data"]["url"] == ""

    async def test_the_buttons_mark_done_or_snooze_this_robot_only(self, hass):
        _sent, done, later = await self._automation(hass, snooze_days=3)
        bus = hass.bus
        bus.async_fire("mobile_app_notification_action",
                       {"action": "ROOMBA_PLUS_DONE|vacuum.robo|replace.filter|clean"})
        bus.async_fire("mobile_app_notification_action",
                       {"action": "ROOMBA_PLUS_DONE|vacuum.robo|clean.wheel"})
        bus.async_fire("mobile_app_notification_action",
                       {"action": "ROOMBA_PLUS_LATER|vacuum.robo|clean.wheel"})
        bus.async_fire("mobile_app_notification_action",
                       {"action": "ROOMBA_PLUS_DONE|vacuum.other|replace.filter"})
        bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
        await hass.async_block_till_done()
        assert [(c.data["entity_id"], c.data["item"], c.data.get("action")) for c in done] == [
            (["vacuum.robo"], "replace.filter", "clean"),
            (["vacuum.robo"], "clean.wheel", None),
        ]
        assert [(c.data["item"], c.data["days"]) for c in later] == [("clean.wheel", 3)]

    async def test_the_default_is_a_persistent_notification_with_the_link(self, hass):
        import shutil
        from pathlib import Path

        from homeassistant.setup import async_setup_component

        source = Path(__file__).parent.parent / "blueprints" / "automation" / "roomba_plus_maintenance.yaml"
        target = Path(hass.config.path("blueprints", "automation", "roomba_plus", source.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        hass.states.async_set("event.robo_maintenance", "2026-10-17T10:00:00+00:00", {})
        assert await async_setup_component(hass, "persistent_notification", {})
        assert await async_setup_component(hass, "automation", {"automation": {
            "use_blueprint": {"path": f"roomba_plus/{source.name}", "input": {
                "maintenance_event": "event.robo_maintenance",
                "vacuum_entity": "vacuum.robo",
                "send_from": "00:00:00",
                "send_until": "00:00:00",
            }},
        }})
        self._fire(hass, "2026-10-17T11:00:00+00:00")
        await hass.async_block_till_done()
        from homeassistant.components import persistent_notification as pn

        notes = list(pn._async_get_or_create_notifications(hass).values())
        assert len(notes) == 1
        assert notes[0]["title"] == "Robo"
        assert notes[0]["message"] == (
            "Replace the filter is due\n\n"
            "[How to do it](/api/roomba_plus/care/E1/replace_filter?sig=x)"
        )

    async def test_irobots_article_for_the_error_is_linked_too(self, hass):
        """4.5.0b4: an entry an error brought carries iRobot's article for
        the error; the notification links it below our guide, and the
        variable is there for one of your own."""
        import shutil
        from pathlib import Path

        from homeassistant.setup import async_setup_component

        source = Path(__file__).parent.parent / "blueprints" / "automation" / "roomba_plus_maintenance.yaml"
        target = Path(hass.config.path("blueprints", "automation", "roomba_plus", source.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        hass.states.async_set("event.robo_maintenance", "2026-10-17T10:00:00+00:00", {})
        assert await async_setup_component(hass, "persistent_notification", {})
        assert await async_setup_component(hass, "automation", {"automation": {
            "use_blueprint": {"path": f"roomba_plus/{source.name}", "input": {
                "maintenance_event": "event.robo_maintenance",
                "vacuum_entity": "vacuum.robo",
                "send_from": "00:00:00",
                "send_until": "00:00:00",
            }},
        }})
        self._fire(hass, "2026-10-17T11:00:00+00:00", id="check.bin", error=14,
                   guide_title="Check the bin", help_url="https://example.com/error-14")
        await hass.async_block_till_done()
        from homeassistant.components import persistent_notification as pn

        notes = list(pn._async_get_or_create_notifications(hass).values())
        assert notes[0]["message"] == (
            "Error 14: Check the bin is due\n\n"
            "[How to do it](/api/roomba_plus/care/E1/replace_filter?sig=x)\n\n"
            "[iRobot's article on the error](https://example.com/error-14)"
        )

    async def test_the_article_is_a_variable_for_your_own_action(self, hass):
        sent, _done, _later = await self._automation(hass, notify_action=[
            {"action": "notify.phone", "data": {"message": "{{ notification_help_url }}"}},
        ])
        self._fire(hass, "2026-10-17T11:00:00+00:00", help_url="https://example.com/a")
        self._fire(hass, "2026-10-17T12:00:00+00:00")
        await hass.async_block_till_done()
        assert [c.data["message"] for c in sent] == ["https://example.com/a", ""]


class TestTheCommandEntityFollowsBothBusEvents:
    """4.6.0: a command taken and not acted on, and a state that stopped
    following the robot, on one entity of both generations."""

    @staticmethod
    def _event(event_type, data):
        return SimpleNamespace(event_type=event_type, data=data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("prime", [False, True])
    @pytest.mark.parametrize(("bus_event", "event_type"), [
        ("roomba_plus_command_not_delivered", "not_delivered"),
        ("roomba_plus_state_frozen", "state_frozen"),
    ])
    async def test_this_robots_event_sets_its_type(self, prime, bus_event, event_type):
        connection = ConnectionType.CLOUD_ONLY if prime else ConnectionType.LOCAL_PUSH
        entity = (await _created(_entry(connection)))["command"]
        entity.async_write_ha_state = MagicMock()
        entity._on_bus_event(self._event(bus_event, {"entry_id": "E1", "command": "start"}))
        assert entity.state is not None
        assert entity.state_attributes["event_type"] == event_type
        assert entity.state_attributes["command"] == "start"

    @pytest.mark.asyncio
    async def test_another_robots_event_is_ignored(self):
        entity = (await _created(_entry()))["command"]
        entity.async_write_ha_state = MagicMock()
        entity._on_bus_event(self._event(
            "roomba_plus_command_not_delivered", {"entry_id": "OTHER"},
        ))
        assert entity.state is None

    @pytest.mark.asyncio
    async def test_it_listens_to_both_and_ignores_others(self):
        entity = (await _created(_entry()))["command"]
        entity.hass = MagicMock()
        entity.async_on_remove = MagicMock()
        with patch.object(event_mod.IRobotEntity, "async_added_to_hass", return_value=None):
            await entity.async_added_to_hass()
        listened = {c.args[0] for c in entity.hass.bus.async_listen.call_args_list}
        assert listened == {
            "roomba_plus_command_not_delivered", "roomba_plus_state_frozen",
            "roomba_plus_start_blocked", "roomba_plus_start_timeout",
        }
        entity.async_write_ha_state = MagicMock()
        entity._on_bus_event(self._event("roomba_plus_unrelated", {"entry_id": "E1"}))
        assert entity.state is None
        assert entity.new_state_filter({}) is False


class TestTheMissionResultEntity:
    """4.6.0: how a mission ended, by type, on both generations."""

    @staticmethod
    def _event(data):
        return SimpleNamespace(event_type="roomba_plus_mission_result", data=data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("prime", [False, True])
    async def test_the_type_becomes_the_event_type(self, prime):
        connection = ConnectionType.CLOUD_ONLY if prime else ConnectionType.LOCAL_PUSH
        entity = (await _created(_entry(connection)))["mission_result"]
        entity.async_write_ha_state = MagicMock()
        entity._on_result(self._event({
            "entry_id": "E1", "type": "completed_with_skips", "skipped_rooms": ["Hall"],
        }))
        assert entity.state_attributes["event_type"] == "completed_with_skips"
        assert entity.state_attributes["skipped_rooms"] == ["Hall"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("data", [
        {"entry_id": "OTHER", "type": "completed"},
        {"entry_id": "E1", "type": "exploded"},
    ])
    async def test_another_robot_or_an_unknown_type_is_ignored(self, data):
        entity = (await _created(_entry()))["mission_result"]
        entity.async_write_ha_state = MagicMock()
        entity._on_result(self._event(data))
        assert entity.state is None

    @pytest.mark.asyncio
    async def test_it_listens_on_the_bus(self):
        entity = (await _created(_entry()))["mission_result"]
        entity.hass = MagicMock()
        entity.async_on_remove = MagicMock()
        with patch.object(event_mod.IRobotEntity, "async_added_to_hass", return_value=None):
            await entity.async_added_to_hass()
        entity.hass.bus.async_listen.assert_called_once_with(
            "roomba_plus_mission_result", entity._on_result,
        )
        assert entity.new_state_filter({}) is False

