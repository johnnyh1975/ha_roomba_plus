"""Tests for todo.py's RoombaMaintenanceTodo (Classic maintenance list).

4.3.2 rebuilt the list: one replacement item per part the robot has,
the calendar cleaning items from CARE_TASKS, and the room-naming status
item. Items not due are listed as done with their due date.
"""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.components.todo import TodoItem, TodoItemStatus

from custom_components.roomba_plus.maintenance_store import MaintenanceStore

_DAYS = "custom_components.roomba_plus.todo._consumable_days_until_due"
_ZONES = "custom_components.roomba_plus.todo.unlabelled_zone_ids"


def _make_todo(vacuum_state: dict | None = None, store: object | None = "real", **options):
    from custom_components.roomba_plus.todo import RoombaMaintenanceTodo

    todo = RoombaMaintenanceTodo.__new__(RoombaMaintenanceTodo)
    todo._blid = "TESTBLID"
    todo.vacuum_state = vacuum_state if vacuum_state is not None else {"sku": "R980020"}
    todo.hass = MagicMock()
    todo._config_entry = MagicMock()
    todo._config_entry.entry_id = "test_entry"
    todo._config_entry.options = options
    # No iRobot account unless a test adds one: a MagicMock coordinator
    # with MagicMock data would take the account path everywhere.
    todo._config_entry.runtime_data.cloud_coordinator = None
    if store == "real":
        store = MaintenanceStore()
        store.care_since = "2026-07-01T12:00:00+00:00"
    todo._config_entry.runtime_data.maintenance_store = store
    todo.async_write_ha_state = MagicMock()
    return todo


def _items(todo, days=None, zones=()):
    with patch(_DAYS, return_value=days), patch(_ZONES, return_value=list(zones)):
        return {i.uid: i for i in todo.todo_items}


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_always_creates_exactly_one_entity(self):
        from custom_components.roomba_plus.todo import RoombaMaintenanceTodo, async_setup_entry

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        added: list = []

        await async_setup_entry(hass, config_entry, added.extend)

        assert len(added) == 1
        assert isinstance(added[0], RoombaMaintenanceTodo)


class TestReplacementItems:
    def test_a_980_without_clean_base_has_three(self):
        uids = set(_items(_make_todo()))
        assert {"filter_maintenance", "brush_maintenance", "side_brush_maintenance"} <= uids
        assert "clean_base_bag_maintenance" not in uids

    def test_a_clean_base_adds_the_bag(self):
        todo = _make_todo({"sku": "i355640", "dock": {"known": True}})
        with patch("custom_components.roomba_plus.const.has_clean_base", return_value=True):
            uids = set(_items(todo))
        assert "clean_base_bag_maintenance" in uids

    def test_a_braava_has_only_the_pad(self):
        uids = set(_items(_make_todo({"sku": "m611020", "detectedPad": "wet"})))
        assert "pad_maintenance" in uids
        assert not {"filter_maintenance", "brush_maintenance", "side_brush_maintenance"} & uids

    def test_the_brush_item_says_replace(self):
        """It always reset the replacement counter, and read "Clean brush
        roll" -- so weekly cleaning kept the replacement from coming due."""
        item = _items(_make_todo())["brush_maintenance"]
        assert item.summary == "Replace the main brushes"

    def test_not_due_is_listed_as_done(self):
        assert _items(_make_todo())["filter_maintenance"].status == TodoItemStatus.COMPLETED

    def test_due_by_the_shared_counter_needs_action(self):
        store = MaintenanceStore()
        store.care_since = "2026-07-01T12:00:00+00:00"
        todo = _make_todo({"sku": "R980020", "bbrun": {"hr": 500}}, store=store)
        items = _items(todo)
        assert items["filter_maintenance"].status == TodoItemStatus.NEEDS_ACTION

    @freeze_time("2026-07-06")
    def test_due_date_from_the_wear_estimate(self):
        assert _items(_make_todo(), days=10)["filter_maintenance"].due == datetime.date(2026, 7, 16)

    def test_no_due_date_without_an_estimate(self):
        assert _items(_make_todo(), days=None)["filter_maintenance"].due is None

    def test_no_store_no_maintenance_items(self):
        assert set(_items(_make_todo(store=None))) == set()


class TestCareItems:
    def test_off_by_default(self):
        assert not [u for u in _items(_make_todo()) if u.startswith("care_")]

    @freeze_time("2026-07-03 10:00:00+00:00")
    def test_a_980_gets_all_six_not_yet_due(self):
        items = _items(_make_todo(care_reminders=True))
        care = {u: i for u, i in items.items() if u.startswith("care_")}
        assert set(care) == {"care_filter", "care_brushes", "care_side_brush",
                             "care_wheel", "care_cliff_sensors", "care_contact"}
        assert all(i.status == TodoItemStatus.COMPLETED for i in care.values())
        assert care["care_filter"].summary == "Clean the filter"
        assert care["care_filter"].description == "Every 7 days, as iRobot recommends."

    @freeze_time("2026-07-09 10:00:00+00:00")
    def test_due_on_its_day(self):
        items = _items(_make_todo(care_reminders=True))
        assert items["care_filter"].status == TodoItemStatus.NEEDS_ACTION
        assert items["care_side_brush"].status == TodoItemStatus.COMPLETED

    @freeze_time("2026-07-05 10:00:00+00:00")
    def test_pets_bring_filter_and_brushes_forward(self):
        items = _items(_make_todo(care_reminders=True, pets_in_household=True))
        assert items["care_filter"].status == TodoItemStatus.NEEDS_ACTION
        assert items["care_brushes"].status == TodoItemStatus.NEEDS_ACTION
        assert items["care_wheel"].status == TodoItemStatus.COMPLETED
        assert items["care_filter"].description == "Every 3.5 days, as iRobot recommends."

    def test_a_braava_gets_sensors_and_contacts(self):
        uids = {u for u in _items(_make_todo({"sku": "m611020"}, care_reminders=True))
                if u.startswith("care_")}
        assert uids == {"care_cliff_sensors", "care_contact"}

    def test_translated_wording_wins(self):
        todo = _make_todo(care_reminders=True)
        todo._wording = {"clean_filter": "Filter reinigen", "every_days": "Alle {days} Tage."}
        item = _items(todo)["care_filter"]
        assert item.summary == "Filter reinigen"
        assert item.description == "Alle 7 Tage."


class TestWording:
    @pytest.mark.asyncio
    async def test_loaded_from_the_selector_table(self):
        todo = _make_todo()
        todo.hass.config.language = "de"
        table = {
            "component.roomba_plus.selector.maintenance_item.options.clean_filter": "Filter reinigen",
            "component.roomba_plus.selector.other.options.x": "nope",
        }
        with patch("homeassistant.helpers.translation.async_get_translations",
                   AsyncMock(return_value=table)):
            await todo._async_load_wording()
        assert todo._wording == {"clean_filter": "Filter reinigen"}

    @pytest.mark.asyncio
    async def test_a_failed_read_keeps_english(self):
        todo = _make_todo()
        todo._wording = {}
        with patch("homeassistant.helpers.translation.async_get_translations",
                   AsyncMock(side_effect=RuntimeError)):
            await todo._async_load_wording()
        assert todo._text("clean_filter") == "Clean the filter"

    def test_every_key_has_a_translation_and_an_english_fallback(self):
        import json
        from pathlib import Path

        from custom_components.roomba_plus.todo import _ENGLISH

        strings = json.loads(Path("custom_components/roomba_plus/strings.json").read_text())
        assert strings["selector"]["maintenance_item"]["options"] == _ENGLISH

    @pytest.mark.asyncio
    async def test_added_to_hass_loads_wording_and_ticks_at_midnight(self):
        from custom_components.roomba_plus import todo as todo_mod

        todo = _make_todo()
        todo.async_on_remove = MagicMock()
        tracked = {}

        def _track(_hass, cb, **kw):
            tracked.update(kw, cb=cb)
            return lambda: None

        with patch.object(todo_mod.IRobotEntity, "async_added_to_hass", AsyncMock()), \
             patch.object(todo_mod, "async_track_time_change", _track), \
             patch.object(type(todo), "_async_load_wording", AsyncMock()) as load:
            await todo.async_added_to_hass()
        load.assert_awaited_once()
        assert (tracked["hour"], tracked["minute"]) == (0, 0)
        tracked["cb"](None)
        todo.async_write_ha_state.assert_called_once()


class TestReconfigureRooms:
    def test_present_when_unlabelled_zones_exist(self):
        item = _items(_make_todo(), zones=["7"])["reconfigure_rooms"]
        assert item.due is None
        assert "Rooms & zones" in item.description
        assert item.summary == "Name the rooms"

    def test_absent_when_no_unlabelled_zones(self):
        assert "reconfigure_rooms" not in _items(_make_todo())

    def _with_account(self, todo, unnamed):
        coordinator = MagicMock()
        coordinator.data = {"pmaps": []}
        todo._config_entry.runtime_data.cloud_coordinator = coordinator
        return patch(
            "custom_components.roomba_plus.todo.smart_rooms_to_name",
            return_value=unnamed,
        )

    def test_with_an_account_the_rooms_on_the_map_decide(self):
        """@liblit's i7: every room named in his account, and the item
        stood open because the robot's region ids had no name in Roomba+."""
        todo = _make_todo()
        with self._with_account(todo, []):
            assert "reconfigure_rooms" not in _items(todo, zones=["20", "21"])

    def test_with_an_account_an_unnamed_room_on_the_map_opens_it(self):
        todo = _make_todo()
        with self._with_account(todo, ["7"]):
            assert "reconfigure_rooms" in _items(todo)


def _store_mock():
    store = MagicMock()
    store.async_save = AsyncMock()
    return store


class TestTickingOff:
    @pytest.mark.parametrize(("uid", "method", "part"), [
        ("filter_maintenance", "reset_filter", "filter"),
        ("brush_maintenance", "reset_brush", "brush"),
        ("side_brush_maintenance", "reset_side_brush", "side_brush"),
        ("clean_base_bag_maintenance", "reset_clean_base_bag", "clean_base_bag"),
        ("pad_maintenance", "reset_pad", "pad"),
    ])
    @pytest.mark.asyncio
    async def test_a_replacement_resets_saves_fires_and_tells_the_cloud(self, uid, method, part):
        store = _store_mock()
        todo = _make_todo({"bbrun": {"hr": 150}}, store=store)
        with patch("custom_components.roomba_plus.todo._fire_maintenance_reset_event") as fire, \
             patch("custom_components.roomba_plus.todo._async_push_part_reset_to_cloud",
                   AsyncMock()) as push, \
             patch("custom_components.roomba_plus.todo.async_dispatcher_send") as send:
            await todo.async_update_todo_item(TodoItem(uid=uid, status=TodoItemStatus.COMPLETED))
        getattr(store, method).assert_called_once_with(150)
        store.async_save.assert_awaited_once_with(todo.hass, "test_entry")
        fire.assert_called_once_with(todo.hass, todo._config_entry, part, 150)
        push.assert_awaited_once_with(todo._config_entry, todo._config_entry.runtime_data, part)
        send.assert_called_once()
        todo.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_cleaning_moves_no_counter(self):
        store = MaintenanceStore(brush_reset_hr=40)
        store.async_save = AsyncMock()
        todo = _make_todo(store=store)
        with patch("custom_components.roomba_plus.todo._fire_maintenance_reset_event") as fire, \
             patch("custom_components.roomba_plus.todo.async_dispatcher_send"):
            await todo.async_update_todo_item(
                TodoItem(uid="care_brushes", status=TodoItemStatus.COMPLETED))
        assert store.brushes_cleaned_at is not None
        assert store.brush_reset_hr == 40 and store.brush_reset_history == []
        fire.assert_called_once_with(todo.hass, todo._config_entry, "brushes", None)

    @pytest.mark.asyncio
    async def test_an_unknown_care_task_does_nothing(self):
        store = _store_mock()
        del store.reset_bogus_cleaning
        todo = _make_todo(store=store)
        await todo.async_update_todo_item(TodoItem(uid="care_bogus", status=TodoItemStatus.COMPLETED))
        store.async_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unknown_uid_does_nothing(self):
        store = _store_mock()
        todo = _make_todo(store=store)
        await todo.async_update_todo_item(TodoItem(uid="whatever", status=TodoItemStatus.COMPLETED))
        store.async_save.assert_not_awaited()
        todo.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconfigure_rooms_has_no_side_effect(self):
        store = _store_mock()
        todo = _make_todo(store=store)
        await todo.async_update_todo_item(
            TodoItem(uid="reconfigure_rooms", status=TodoItemStatus.COMPLETED))
        store.async_save.assert_not_awaited()
        todo.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_needs_action_is_a_noop(self):
        store = _store_mock()
        todo = _make_todo({"bbrun": {"hr": 150}}, store=store)
        await todo.async_update_todo_item(
            TodoItem(uid="filter_maintenance", status=TodoItemStatus.NEEDS_ACTION))
        store.reset_filter.assert_not_called()
        todo.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_store_does_not_crash(self):
        todo = _make_todo({"bbrun": {"hr": 150}}, store=None)
        await todo.async_update_todo_item(
            TodoItem(uid="filter_maintenance", status=TodoItemStatus.COMPLETED))
        todo.async_write_ha_state.assert_called_once()


class TestNewStateFilter:
    @pytest.mark.parametrize(("update", "expected"), [
        ({"bbrun": {}}, True),
        ({"cleanSchedule2": []}, True),
        ({"lastCommand": {}}, True),
        ({"cleanMissionStatus": {}}, False),
    ])
    def test_gate(self, update, expected):
        assert _make_todo().new_state_filter(update) is expected


class TestCurrentHrNullRegression:
    def test_explicit_null_bbrun_returns_zero(self):
        assert _make_todo({"bbrun": None})._current_hr() == 0
