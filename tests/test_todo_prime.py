"""The maintenance list for a Prime robot.

Classic derives everything from one number -- lifetime running hours --
and thresholds of ours, guessed from iRobot's published intervals. A
Prime robot reports each part with how much it has used and how much is
left, in whatever unit that part is counted in.

So this list has no thresholds of its own and cannot be wrong about one.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _part(**kwargs):
    base = {
        "count_type": "minutes", "count_used": 40, "count_remaining": 20,
        "counter_category": "replacement",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _entity(parts):
    from custom_components.roomba_plus.todo_prime import PrimeMaintenanceTodo

    entity = object.__new__(PrimeMaintenanceTodo)
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(
        prime_parts_coordinator=SimpleNamespace(data=parts)
    )
    entity._config_entry = entry
    return entity


class TestTheRobotDecidesWhatIsDue:
    """`count_remaining` reaching zero is the robot saying so. There is
    no threshold here to get wrong."""

    def _status(self, **kwargs):
        from homeassistant.components.todo import TodoItemStatus

        items = _entity({"212": _part(**kwargs)}).todo_items
        return items[0].status, TodoItemStatus

    def test_a_part_with_life_left_is_not_due(self):
        status, cls = self._status(count_remaining=20)

        assert status == cls.COMPLETED

    def test_a_part_at_zero_is_due(self):
        status, cls = self._status(count_remaining=0)

        assert status == cls.NEEDS_ACTION

    def test_a_part_past_zero_is_due(self):
        status, cls = self._status(count_remaining=-5)

        assert status == cls.NEEDS_ACTION

    def test_a_part_with_no_estimate_is_not_due(self):
        """The safer direction: a list that nags about a part nobody can
        act on gets ignored wholesale, and with it the items that
        mattered."""
        status, cls = self._status(count_remaining=None)

        assert status == cls.COMPLETED


class TestTheWordingFollowsTheCategory:
    """The app branches on `counter_category` and picks a different
    string per category. Two verbs, not one."""

    def _summary(self, category):
        return _entity({"212": _part(counter_category=category)}).todo_items[0].summary

    def test_maintenance_asks_to_clean(self):
        assert self._summary("maintenance").startswith("Clean")

    def test_replacement_asks_to_replace(self):
        assert self._summary("replacement").startswith("Replace")

    def test_an_unknown_category_asks_to_check(self):
        """Neither verb is a safe guess, and "Check" claims nothing."""
        assert self._summary("somethingNew").startswith("Check")
        assert self._summary(None).startswith("Check")


class TestTheDescriptionCarriesWhatIsLeft:
    def _description(self, **kwargs):
        return _entity({"212": _part(**kwargs)}).todo_items[0].description

    def test_the_unit_comes_from_the_count_type(self):
        assert self._description(count_type="evacs", count_remaining=7) == (
            "7 evacuations remaining"
        )

    def test_minutes_are_presented_as_hours(self):
        """The robot counts filter life in minutes and the app shows
        hours."""
        assert "hours" in self._description(count_type="minutes")

    def test_an_unknown_unit_still_says_the_number(self):
        assert self._description(count_type="furlongs") == "20 remaining"

    def test_without_a_remaining_count_it_reports_usage(self):
        """No estimate rather than a computed one -- a part whose
        remaining count the robot does not report is not a part at
        zero."""
        text = self._description(count_remaining=None, count_used=99)

        assert text is not None and "99" in text


class TestUnknownPartsShowTheirId:
    def test_an_unrecognised_part_is_named_by_id(self):
        """Ugly and honest. Inventing "Part 999" would put a label on
        screen that matches nothing in the iRobot app."""
        summary = _entity({"999": _part()}).todo_items[0].summary

        assert "999" in summary


class TestTheListIsReadOnly:
    """The robot owns whether a part is fresh, and it only learns that
    from a reset performed on the robot or in the iRobot app. Ticking an
    item off here would record a claim the robot does not share, and the
    next refresh would silently undo it."""

    def test_no_update_feature_is_advertised(self):
        """Read off an instance -- the base class turns
        `_attr_supported_features` into a property, so the class
        attribute is not the value."""
        from homeassistant.components.todo import TodoListEntityFeature

        entity = _entity({})

        assert not (
            entity.supported_features & TodoListEntityFeature.UPDATE_TODO_ITEM
        )


class TestAnEmptyOrAbsentCoordinator:
    def test_no_data_yet_gives_no_items(self):
        assert _entity(None).todo_items is None

    def test_no_parts_gives_an_empty_list(self):
        assert _entity({}).todo_items == []

    @pytest.mark.asyncio
    async def test_a_robot_without_prime_gets_no_entity(self):
        from custom_components.roomba_plus.todo_prime import (
            async_setup_prime_todo,
        )

        added: list = []
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_robot=None, blid="B")
        await async_setup_prime_todo(
            MagicMock(), entry, lambda e: added.extend(e)
        )

        assert added == []

    @pytest.mark.asyncio
    async def test_the_entity_appears_before_the_parts_do(self):
        """An entity that appears and disappears is worse than one that
        is briefly empty -- an automation pointing at a vanished entity
        fails for a reason unrelated to automations."""
        from custom_components.roomba_plus.todo_prime import (
            async_setup_prime_todo,
        )

        added: list = []
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_robot=object(), blid="B",
            prime_parts_coordinator=SimpleNamespace(data=None),
        )
        await async_setup_prime_todo(
            MagicMock(), entry, lambda e: added.extend(e)
        )

        assert len(added) == 1
