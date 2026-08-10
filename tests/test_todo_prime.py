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


class TestZeroMeansTwoOppositeThings:
    """On a `replacement` part, zero means used up. On a `maintenance`
    part it means **just done** — the counter resets when the job is
    performed, so a freshly washed pad reads zero and needs nothing.

    @DaRealGuGu's robot made that plain. Two parts count the same 90 pad
    washes:

        212  replacement  count_remaining 210   the pad itself
        202  maintenance  count_remaining 0     the wash

    Reading both the same way put an item on his list while the iRobot
    app showed nothing due and the robot's light ring was clear.
    """

    def _status(self, part_id):
        from homeassistant.components.todo import TodoItemStatus

        #: His actual parts, verbatim from the diagnostics.
        real = {
            "202": _part(count_type="pad_washes_used", count_used=90,
                         count_remaining=0, counter_category="maintenance"),
            "212": _part(count_type="pad_washes_used", count_used=90,
                         count_remaining=210, counter_category="replacement"),
            "148": _part(count_type="combo_missions", count_used=7,
                         count_remaining=23, counter_category="replacement"),
        }
        items = _entity(real).todo_items
        item = next(i for i in items if i.uid == f"part_{part_id}")
        return item.status, TodoItemStatus

    def test_the_wash_counter_at_zero_is_not_due(self):
        """This is the one that appeared on his list and should not
        have."""
        status, cls = self._status("202")

        assert status == cls.COMPLETED

    def test_the_pad_with_life_left_is_not_due(self):
        status, cls = self._status("212")

        assert status == cls.COMPLETED

    def test_his_whole_robot_produces_no_due_items(self):
        """The app showed nothing due; so should we."""
        from homeassistant.components.todo import TodoItemStatus

        real = {
            "202": _part(count_remaining=0, counter_category="maintenance"),
            "212": _part(count_remaining=210, counter_category="replacement"),
            "67": _part(count_remaining=4620, counter_category="replacement"),
            "147": _part(count_remaining=60, counter_category="replacement"),
        }
        items = _entity(real).todo_items

        assert not [
            i for i in items if i.status == TodoItemStatus.NEEDS_ACTION
        ]

    def test_a_replacement_part_at_zero_is_still_due(self):
        """The fix must not silence the case the list exists for."""
        from homeassistant.components.todo import TodoItemStatus

        spent = {"67": _part(count_remaining=0, counter_category="replacement")}
        items = _entity(spent).todo_items

        assert items[0].status == TodoItemStatus.NEEDS_ACTION

    def test_a_maintenance_part_is_never_due_whatever_the_count(self):
        """Deliberate, and only as far as one account and one robot
        establish. Being quiet about a real job is recoverable; nagging
        about a clean pad is how a list gets ignored."""
        from custom_components.roomba_plus.todo_prime import _needs_attention

        for remaining in (0, -5, 12, None):
            assert _needs_attention(
                _part(count_remaining=remaining, counter_category="maintenance")
            ) is False


class TestTheListIsAskedForRatherThanImposed:
    """@chairstacker found a to-do list in his sidebar he had not asked
    for -- **the second time something appeared there uninvited.**

    A to-do list is not a quiet entity: Home Assistant gives it a place
    in the navigation, which is the user's space rather than ours. So it
    is gated on an option, and unlike the calendar it defaults to OFF.

    The calendar defaults on because it existed before there was an
    option, and turning it off would have taken it from people using it.
    This one has no such history.
    """

    def _platforms(self, options):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus import _optional_platforms

        entry = MagicMock()
        entry.options = options
        return _optional_platforms(entry)

    def test_off_by_default(self):
        from homeassistant.const import Platform

        assert Platform.TODO not in self._platforms({})

    def test_present_when_asked_for(self):
        from homeassistant.const import Platform

        assert Platform.TODO in self._platforms({"enable_maintenance_list": True})

    def test_turning_it_off_removes_it(self):
        from homeassistant.const import Platform

        assert Platform.TODO not in self._platforms(
            {"enable_maintenance_list": False}
        )

    def test_it_does_not_disturb_the_calendar(self):
        """The two options are independent; enabling one must not carry
        the other in."""
        from homeassistant.const import Platform

        platforms = self._platforms({"enable_maintenance_list": True})

        assert Platform.CALENDAR in platforms

    def test_the_option_is_labelled_in_every_locale(self):
        import json
        import pathlib

        base = pathlib.Path("custom_components/roomba_plus/translations")
        for path in base.glob("*.json"):
            data = json.loads(path.read_text())
            labels = [
                step.get("data", {}).get("enable_maintenance_list")
                for step in data.get("options", {}).get("step", {}).values()
                if isinstance(step, dict)
            ]
            assert any(labels), path.name


class TestTheOptionAppliesToBothGenerations:
    """The list arrived on Classic without an option and on Prime with
    one. That would have meant answering @chairstacker's complaint for
    one robot and not the other — and his point, that a to-do list takes
    a place in the sidebar and should be asked for, says nothing about
    which robot it is.
    """

    def test_neither_platform_list_carries_it_unconditionally(self):
        import re
        import pathlib

        source = pathlib.Path(
            "custom_components/roomba_plus/const.py"
        ).read_text()
        for name in ("LOCAL_PLATFORMS", "PRIME_PLATFORMS"):
            match = re.search(rf"{name}[^=]*=\s*\[(.*?)\]", source, re.S)
            # Only actual entries -- the comment inside the list explains
            # why TODO left it, and matching that would fail the test for
            # its own explanation.
            entries = [
                line.strip() for line in match.group(1).splitlines()
                if line.strip().startswith("Platform.")
            ]
            assert "Platform.TODO," not in entries, name

    def test_one_option_governs_both(self):
        """A second constant would drift: two switches for one idea is
        how the calendar and the list ended up different in the first
        place."""
        import inspect

        from custom_components.roomba_plus import _optional_platforms

        source = inspect.getsource(_optional_platforms)

        assert source.count("CONF_ENABLE_MAINTENANCE_LIST") == 1
