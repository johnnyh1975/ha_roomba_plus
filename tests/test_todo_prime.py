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

    def test_the_unit_uses_irobots_own_wording(self):
        """`evacsUnit` in the app reads "Dust Collection Left", not
        "evacuations" -- and the app is what the user has open beside
        this list. Two different words for one measurement read as two
        different measurements."""
        assert self._description(count_type="evacs", count_remaining=7) == (
            "7 dust collections remaining"
        )

    def test_missions_are_tasks(self):
        """`missionsUnit` reads "Tasks Left". The enum name is
        `missions`; the user-facing word is not."""
        assert "tasks" in self._description(
            count_type="missions", count_remaining=4
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
        # Counted in evacuations so this says what it means to say: the
        # fallback exists, in a unit that needs no conversion. It used
        # the fixture's default `minutes` and asserted the raw number,
        # which quietly required the usage line to be un-converted --
        # exactly the bug @utkjmitch found on the remaining line.
        text = self._description(
            count_type="evacs", count_remaining=None, count_used=99
        )

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


class TestPartNamesUseIRobotsOwnWords:
    """The part names in app 3.0.0 live in its locale files, in 25
    languages. Ours were written here.

    APK research 9 concluded the names had to be invented because the
    catalogue endpoint did not carry them. That was right for 2.2.4 —
    and 3.0.0 moved them into the app, so the real names are now
    readable.

    A user comparing the two screens should see the same words.
    """

    VENDOR = {
        "en": {
            "prime_part_filter": "High-Efficiency Filter",
            "prime_part_dirt_bag": "Dust Bag",
            "prime_part_multi_surface_brush": "Main Brush(es)",
        },
        "de": {
            "prime_part_filter": "Hocheffizienz-Filter",
            "prime_part_dirt_bag": "Staubbeutel",
            "prime_part_multi_surface_brush": "Hauptbürste(n)",
        },
    }

    def _sensor_names(self, locale):
        import json
        import pathlib

        data = json.loads(
            (pathlib.Path("custom_components/roomba_plus/translations")
             / f"{locale}.json").read_text()
        )
        return data["entity"]["sensor"]

    def test_english_carries_the_app_wording(self):
        """PREFIX KEPT, WORD TAKEN. Classic names every consumable
        "Maintenance – <part>", and a second vocabulary for the same
        concept in the same entity list reads as two integrations.

        So the grouping stays ours and the part name becomes iRobot's:
        "Maintenance – High-Efficiency Filter"."""
        names = self._sensor_names("en")

        for key, expected in self.VENDOR["en"].items():
            assert names[key]["name"].endswith(expected), key
            assert names[key]["name"].startswith("Maintenance"), key

    def test_german_carries_the_app_wording(self):
        names = self._sensor_names("de")

        for key, expected in self.VENDOR["de"].items():
            assert names[key]["name"].endswith(expected), key

    def test_every_locale_names_every_mapped_part(self):
        """A part named in one language and not another is worse than
        one named in none: the gap only shows for users of that
        language."""
        for locale in ("en", "de", "es", "fr", "it", "nl", "pl", "pt"):
            names = self._sensor_names(locale)
            for key in self.VENDOR["en"]:
                assert names.get(key, {}).get("name"), f"{locale}/{key}"

    def test_parts_the_vendor_does_not_name_keep_ours(self):
        """`edge_brush` and `cliff_sensors` have no counterpart in the
        app's list. Leaving them is right; inventing a mapping to a
        vendor name that means something else would be worse than our
        own wording."""
        names = self._sensor_names("en")

        assert names["prime_part_edge_brush"]["name"]
        assert names["prime_part_cliff_sensors"]["name"]


class TestPartNamesAreReadableInTheList:
    """@utkjmitch's maintenance list read:

        Replace prime_part_dirt_bag   completed  60 evacuations remaining
        Replace 68                    needs_action  0 hours remaining

    **A translation key is not a name.** `_KNOWN_PARTS` maps ids to
    translation keys, and Home Assistant does not translate to-do item
    summaries — they are plain text. The readable name was already
    loaded for the sensors; it just was not being looked up.

    On this list the wording was the bug and the judgement was sound:
    the two due items agreed with the iRobot app.
    """

    def _name(self, part_id, cached=None):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.todo_prime import _readable_part_name

        hass = MagicMock()
        hass.config.language = "en"
        with patch(
            "homeassistant.helpers.translation.async_get_cached_translations",
            return_value=cached or {},
        ):
            return _readable_part_name(hass, part_id)

    def test_a_translated_name_is_used(self):
        name = self._name("72", {
            "component.roomba_plus.entity.sensor.prime_part_filter.name":
                "Maintenance – High-Efficiency Filter",
        })

        assert name == "Maintenance – High-Efficiency Filter"

    def test_an_untranslated_key_still_reads_as_words(self):
        """Never as a raw key. "Dirt Bag" is wrong in style, not in
        meaning; `prime_part_dirt_bag` is just a bug on screen."""
        name = self._name("147")

        assert "prime_part" not in name
        assert name == "Dirt Bag"

    def test_an_unknown_id_falls_back_to_its_number(self):
        """Ugly and honest: a number somebody can quote beats a name we
        invented.

        This used 68, which was unknown when the test was written.
        @utkjmitch has since named 149, 69 and 68 by matching the app's
        robot-health screen -- the first two by value, the third by
        elimination -- so they are in the table now and this needs an id
        that is genuinely absent."""
        assert self._name("999") == "999"

    def test_a_broken_translation_lookup_does_not_break_the_list(self):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.todo_prime import _readable_part_name

        hass = MagicMock()
        hass.config.language = "en"
        with patch(
            "homeassistant.helpers.translation.async_get_cached_translations",
            side_effect=RuntimeError("boom"),
        ):
            assert _readable_part_name(hass, "147") == "Dirt Bag"


class TestPartsTheRobotCannotHaveAreHidden:
    """@utkjmitch's **dockless** Combo 104 was told it had "60
    evacuations remaining" for a dust bag it does not have, in a station
    it does not own. The cloud reports the part regardless of the
    hardware.

    **Explicit denial only.** `dock.cap.evac == 0` means "this dock does
    not evacuate"; a missing block means the robot said nothing, and a
    maintenance item wrongly hidden is worse than one wrongly shown.
    """

    def _absent(self, dock_caps):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.todo_prime import (
            _parts_the_robot_cannot_have,
        )

        entry = MagicMock()
        shadows = (
            {"ro-currentstate": {"dock": {"cap": dock_caps}}}
            if dock_caps is not None else None
        )
        entry.runtime_data = SimpleNamespace(
            prime_status_coordinator=SimpleNamespace(data=shadows)
        )
        return _parts_the_robot_cannot_have(entry)

    def test_a_dockless_robot_loses_its_dust_bag(self):
        assert "147" in self._absent({"evac": 0, "pw": 0})

    def test_a_docked_robot_keeps_it(self):
        assert "147" not in self._absent({"evac": 1})

    def test_silence_hides_nothing(self):
        """A robot that did not answer is not a robot without the part."""
        assert self._absent(None) == set()
        assert self._absent({}) == set()

    def test_a_missing_flag_hides_nothing(self):
        """Only a reported zero counts. An absent key is not a denial."""
        assert self._absent({"pw": 1}) == set()

    def test_pad_wash_parts_follow_the_wash_capability(self):
        absent = self._absent({"pw": 0})

        assert {"202", "212"} <= absent


class TestTheListActuallyUsesTheReadableName:
    """`_readable_part_name()` was written for a30 and **never wired to
    the line that builds the summary.** @utkjmitch reported raw keys on
    a30, the helper was added, and he reported the same raw keys on a31
    — with the unit wording changed, proving the list was being rebuilt
    and simply not using it.

    The existing tests exercised the helper directly, so they passed
    while the list ignored it. This one goes through the entity.
    """

    def _summaries(self, parts, cached=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.todo_prime import PrimeMaintenanceTodo

        entity = object.__new__(PrimeMaintenanceTodo)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_parts_coordinator=SimpleNamespace(data=parts),
            prime_status_coordinator=SimpleNamespace(data=None),
        )
        entity._config_entry = entry
        entity.hass = MagicMock()
        entity.hass.config.language = "en"
        with patch(
            "homeassistant.helpers.translation.async_get_cached_translations",
            return_value=cached or {},
        ):
            return [item.summary for item in entity.todo_items or []]

    def _part(self, count_type="evacs", remaining=60):
        from types import SimpleNamespace

        return SimpleNamespace(
            counter_category="replacement", count_type=count_type,
            count_remaining=remaining, count_used=0,
        )

    def test_no_summary_contains_a_translation_key(self):
        """The bug exactly: "Replace prime_part_dirt_bag" on screen."""
        summaries = self._summaries({"147": self._part()})

        assert not any("prime_part" in s for s in summaries), summaries

    def test_a_translated_name_reaches_the_summary(self):
        summaries = self._summaries(
            {"147": self._part()},
            cached={
                "component.roomba_plus.entity.sensor.prime_part_dirt_bag.name":
                    "Maintenance – Dust Bag",
            },
        )

        assert summaries == ["Replace Maintenance – Dust Bag"]

    def test_an_untranslated_key_still_reads_as_words(self):
        assert self._summaries({"147": self._part()}) == ["Replace Dirt Bag"]

    def test_an_unknown_id_keeps_its_number(self):
        """A number the user can quote beats a name we invented.

        Was 68, which @utkjmitch has since named by elimination against
        his app's filter warning. The principle is unchanged, so this
        moved to an id nobody has reported -- if 999 ever turns up in
        `_KNOWN_PARTS`, move it again rather than deleting the test."""
        assert self._summaries({"999": self._part()}) == ["Replace 999"]
