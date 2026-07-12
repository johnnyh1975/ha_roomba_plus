"""v3.4.0 TODO — tests for todo.py's RoombaMaintenanceTodo.

The underlying due-date computations (_filter_days_until_due()/
_brush_days_until_due()) and the unlabelled-zone condition
(unlabelled_zone_ids()) are already covered by their own test files
(test_sensors.py, test_zone_naming.py) — these tests patch them
directly at todo.py's own import point (same lesson as SENSOR-SPLIT:
a same-module caller resolves a name via ITS OWN import binding, so
the patch target must be custom_components.roomba_plus.todo.X, not
the function's original home module).
"""
from __future__ import annotations

import datetime

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

from homeassistant.components.todo import TodoItem, TodoItemStatus


def _make_todo(vacuum_state: dict | None = None):
    from custom_components.roomba_plus.todo import RoombaMaintenanceTodo

    todo = RoombaMaintenanceTodo.__new__(RoombaMaintenanceTodo)
    todo._blid = "TESTBLID"
    todo.vacuum_state = vacuum_state or {}
    todo.hass = MagicMock()
    todo._config_entry = MagicMock()
    todo._config_entry.entry_id = "test_entry"
    todo._config_entry.options = {}
    todo.async_write_ha_state = MagicMock()
    return todo


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_always_creates_exactly_one_entity(self):
        from custom_components.roomba_plus.todo import async_setup_entry

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        added: list = []

        await async_setup_entry(hass, config_entry, added.extend)

        assert len(added) == 1
        from custom_components.roomba_plus.todo import RoombaMaintenanceTodo
        assert isinstance(added[0], RoombaMaintenanceTodo)


class TestTodoItemsFilterAndBrush:
    def test_filter_and_brush_always_present(self):
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        uids = {i.uid for i in items}
        assert "filter_maintenance" in uids
        assert "brush_maintenance" in uids

    def test_brush_becomes_pad_for_mop_models(self):
        todo = _make_todo({"detectedPad": "wet"})  # is_mop() signal
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        uids = {i.uid for i in items}
        assert "pad_maintenance" in uids
        assert "brush_maintenance" not in uids

    @freeze_time("2026-07-06")  # Monday
    def test_due_date_computed_from_days_until_due(self):
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=10), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        filter_item = next(i for i in items if i.uid == "filter_maintenance")
        assert filter_item.due == datetime.date(2026, 7, 16)

    def test_no_due_date_when_days_until_due_unavailable(self):
        """Early in a robot's life, before a wear rate is established —
        the item still exists, just without a due field."""
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        filter_item = next(i for i in items if i.uid == "filter_maintenance")
        assert filter_item.due is None

    def test_items_start_needs_action(self):
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        assert all(i.status == TodoItemStatus.NEEDS_ACTION for i in items)


class TestTodoItemsReconfigureRooms:
    def test_present_when_unlabelled_zones_exist(self):
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=["7"]):
            items = todo.todo_items
        assert any(i.uid == "reconfigure_rooms" for i in items)

    def test_absent_when_no_unlabelled_zones(self):
        """Covers both 'all zones named' and 'EPHEMERAL tier, no zones
        at all' — unlabelled_zone_ids() returns [] in both cases."""
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=[]):
            items = todo.todo_items
        assert not any(i.uid == "reconfigure_rooms" for i in items)

    def test_has_no_due_date(self):
        todo = _make_todo()
        with patch("custom_components.roomba_plus.todo._filter_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo._brush_days_until_due", return_value=None), \
             patch("custom_components.roomba_plus.todo.unlabelled_zone_ids", return_value=["7"]):
            items = todo.todo_items
        item = next(i for i in items if i.uid == "reconfigure_rooms")
        assert item.due is None
        assert "wizard" in item.description


class TestAsyncUpdateTodoItem:
    @pytest.mark.asyncio
    async def test_marking_filter_complete_resets_and_fires_event(self):
        todo = _make_todo({"bbrun": {"hr": 150}})
        store = MagicMock()
        store.async_save = AsyncMock()
        todo._config_entry.runtime_data.maintenance_store = store

        with patch(
            "custom_components.roomba_plus.services._fire_maintenance_reset_event"
        ) as mock_fire:
            await todo.async_update_todo_item(
                TodoItem(summary="Replace filter", uid="filter_maintenance",
                         status=TodoItemStatus.COMPLETED)
            )

        store.reset_filter.assert_called_once_with(150)
        store.async_save.assert_awaited_once_with(todo.hass, "test_entry")
        mock_fire.assert_called_once_with(todo.hass, todo._config_entry, "filter", 150)
        todo.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_marking_brush_complete_resets_and_fires_event(self):
        todo = _make_todo({"bbrun": {"hr": 200}})
        store = MagicMock()
        store.async_save = AsyncMock()
        todo._config_entry.runtime_data.maintenance_store = store

        with patch(
            "custom_components.roomba_plus.services._fire_maintenance_reset_event"
        ) as mock_fire:
            await todo.async_update_todo_item(
                TodoItem(summary="Clean brush roll", uid="brush_maintenance",
                         status=TodoItemStatus.COMPLETED)
            )

        store.reset_brush.assert_called_once_with(200)
        mock_fire.assert_called_once_with(todo.hass, todo._config_entry, "brush", 200)

    @pytest.mark.asyncio
    async def test_marking_pad_complete_calls_reset_pad_not_reset_brush(self):
        """v3.4.0 bug-hunt fix: reset_pad() and reset_brush() write to the
        same store slot but log differently — the semantically correct
        method must be called for mop models, matching the existing
        roomba_plus.reset_pad service's convention."""
        todo = _make_todo({"bbrun": {"hr": 50}, "detectedPad": "wet"})
        store = MagicMock()
        store.async_save = AsyncMock()
        todo._config_entry.runtime_data.maintenance_store = store

        with patch(
            "custom_components.roomba_plus.services._fire_maintenance_reset_event"
        ) as mock_fire:
            await todo.async_update_todo_item(
                TodoItem(summary="Clean/replace pad", uid="pad_maintenance",
                         status=TodoItemStatus.COMPLETED)
            )

        store.reset_pad.assert_called_once_with(50)
        store.reset_brush.assert_not_called()
        mock_fire.assert_called_once_with(todo.hass, todo._config_entry, "pad", 50)

    @pytest.mark.asyncio
    async def test_marking_reconfigure_rooms_complete_has_no_side_effect(self):
        """Self-resolving item — completing it must not touch
        MaintenanceStore or fire any reset event."""
        todo = _make_todo()
        store = MagicMock()
        todo._config_entry.runtime_data.maintenance_store = store

        with patch(
            "custom_components.roomba_plus.services._fire_maintenance_reset_event"
        ) as mock_fire:
            await todo.async_update_todo_item(
                TodoItem(summary="Reconfigure rooms", uid="reconfigure_rooms",
                         status=TodoItemStatus.COMPLETED)
            )

        store.reset_filter.assert_not_called()
        store.reset_brush.assert_not_called()
        mock_fire.assert_not_called()
        todo.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_needs_action_status_is_a_noop(self):
        """Only COMPLETED transitions trigger anything — matches the
        module's own stated contract (no 'un-complete' action exists)."""
        todo = _make_todo({"bbrun": {"hr": 150}})
        store = MagicMock()
        todo._config_entry.runtime_data.maintenance_store = store

        await todo.async_update_todo_item(
            TodoItem(summary="Replace filter", uid="filter_maintenance",
                     status=TodoItemStatus.NEEDS_ACTION)
        )

        store.reset_filter.assert_not_called()
        todo.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_maintenance_store_does_not_crash(self):
        todo = _make_todo({"bbrun": {"hr": 150}})
        todo._config_entry.runtime_data.maintenance_store = None

        await todo.async_update_todo_item(
            TodoItem(summary="Replace filter", uid="filter_maintenance",
                     status=TodoItemStatus.COMPLETED)
        )  # must not raise
        todo.async_write_ha_state.assert_called_once()


class TestNewStateFilter:
    def test_true_for_bbrun_update(self):
        todo = _make_todo()
        assert todo.new_state_filter({"bbrun": {}}) is True


class TestCurrentHrNullRegression:
    """v3.4.2 NULL-REGRESSION — bbrun: null must not crash _current_hr(),
    same confirmed-real bug class as elsewhere in this codebase."""

    def test_explicit_null_bbrun_returns_zero(self):
        todo = _make_todo({"bbrun": None})
        assert todo._current_hr() == 0

    def test_true_for_clean_schedule2_update(self):
        todo = _make_todo()
        assert todo.new_state_filter({"cleanSchedule2": []}) is True

    def test_true_for_last_command_update(self):
        todo = _make_todo()
        assert todo.new_state_filter({"lastCommand": {}}) is True

    def test_false_for_unrelated_update(self):
        todo = _make_todo()
        assert todo.new_state_filter({"cleanMissionStatus": {}}) is False
