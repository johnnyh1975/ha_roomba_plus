"""Buttons for Prime robots: saved favourites, and locate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _favorite(fav_id="f1", name="Evening", commands=None, deleted=False, hidden=False):
    favorite = MagicMock(
        favorite_id=fav_id,
        command_defs=commands if commands is not None else [MagicMock()],
        is_deleted=deleted,
        is_hidden=hidden,
    )
    favorite.name = name
    return favorite


def _entry(favorites=None, raises=False, dock=False):
    """Dock buttons are off by default in these fixtures.

    They are counted separately in TestDockButtons. Leaving them on here
    would make every favourite-count assertion depend on how many dock
    commands exist, which is a different question."""
    entry = MagicMock()
    entry.runtime_data.blid = "BLID"
    entry.runtime_data.prime_status_coordinator.data = {
        "ro-currentstate": {
            "dock": {"cap": {"evac": 0, "pw": 0, "pd": 0}}
        }
    } if not dock else {
        "ro-currentstate": {
            "dock": {"cap": {"evac": 1, "pw": 1, "pd": 2}}
        }
    }
    # Buttons build from the list setup already read, not from a second
    # cloud call -- so the fixture has to provide both shapes.
    entry.runtime_data.prime_favorites = [
        {"id": str(f.favorite_id), "name": f.name}
        for f in (favorites or [])
        if not f.is_deleted and not f.is_hidden and f.command_defs
    ]
    entry.options = {}
    # Buttons are built from the list setup already fetched, not from a
    # second cloud read -- so the fixture has to supply both.
    entry.runtime_data.prime_favorites = [
        {"id": str(f.favorite_id), "name": f.name}
        for f in (favorites or [])
        if not f.is_deleted and not f.is_hidden
    ]
    robot = entry.runtime_data.prime_robot
    if raises:
        robot.get_favorites = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        robot.get_favorites = AsyncMock(return_value=favorites or [])
    robot.send_routine_command_via_cmd_topic = AsyncMock()
    robot.send_simple_command = AsyncMock()
    return entry


class TestFavouriteButtons:
    """A favourite is a stored routine -- "clean the kitchen and hall on
    deep, twice" -- and pressing it runs that.

    A button rather than a select entry, because there is no state to
    hold: a select would imply the robot is currently "on" one of them."""

    @pytest.mark.asyncio
    async def test_one_button_per_favourite_plus_locate(self):
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(
            _entry([_favorite("f1"), _favorite("f2")])
        )

        assert len(entities) == 3  # two favourites + locate

    @pytest.mark.asyncio
    async def test_pressing_sends_every_command_in_order(self):
        """The model allows a list, and "vacuum then mop" is exactly the
        kind of routine people save."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        first, second = MagicMock(name="cmd1"), MagicMock(name="cmd2")
        entry = _entry([_favorite("f1", commands=[first, second])])

        buttons = await async_build_prime_buttons(entry)
        await next(b for b in buttons if hasattr(b, "_favorite_id")).async_press()

        sent = [
            c.args[0]
            for c in entry.runtime_data.prime_robot
            .send_routine_command_via_cmd_topic.await_args_list
        ]
        assert sent == [first, second]

    @pytest.mark.asyncio
    async def test_deleted_and_hidden_favourites_are_skipped(self):
        """They stay in the payload. Creating buttons for them would
        offer routines the app no longer shows."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(_entry([
            _favorite("f1", deleted=True),
            _favorite("f2", hidden=True),
        ]))

        assert len(entities) == 1  # locate only

    @pytest.mark.asyncio
    async def test_a_favourite_with_no_commands_warns_rather_than_silently_failing(self):
        """The button exists -- it is built from the attribute list,
        which does not know about commands. Pressing it reports the
        problem instead of doing nothing quietly."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entry = _entry([_favorite("f1", commands=[])])
        buttons = await async_build_prime_buttons(entry)
        button = next(b for b in buttons if hasattr(b, "_favorite_id"))

        await button.async_press()

        entry.runtime_data.prime_robot.send_routine_command_via_cmd_topic \
            .assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_slug_is_keyed_on_the_id(self):
        """A favourite renamed in the app must not break an automation,
        and one deleted must not shift every button after it onto a
        different routine."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        buttons = await async_build_prime_buttons(_entry([_favorite("abc", "Renamed")]))
        button = next(b for b in buttons if hasattr(b, "_favorite_id"))

        assert button.suggested_object_id == "favorite_abc"

    @pytest.mark.asyncio
    async def test_no_favourites_still_yields_locate(self):
        """Locate needs none, so an empty or failed list should not cost
        it."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(_entry([]))

        assert len(entities) == 1

    @pytest.mark.asyncio
    async def test_buttons_do_not_re_read_the_favourites(self):
        """ONE cloud read, not three. Setup fetched the list for the
        vacuum attribute; fetching it again here meant two requests for
        the same data within a second, and a third on every press."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entry = _entry([_favorite("f1")])
        await async_build_prime_buttons(entry)

        entry.runtime_data.prime_robot.get_favorites.assert_not_awaited()


class TestLocateButton:
    @pytest.mark.asyncio
    async def test_pressing_sends_find(self):
        from custom_components.roomba_plus.button_prime import PrimeLocateButton

        entry = _entry()
        await PrimeLocateButton("BLID", entry).async_press()

        entry.runtime_data.prime_robot.send_simple_command.assert_awaited_once_with(
            "find"
        )

    def test_no_button_exists_without_a_confirmed_command(self):
        """Classic offers evacuate, power off, sleep, spot clean and map
        training. `find` is the one with a confirmed Prime equivalent.

        The others are absent rather than non-functional -- no command
        has been identified, and a button that does nothing when pressed
        is worse than one that is not there."""
        import inspect

        from custom_components.roomba_plus import button_prime

        source = inspect.getsource(button_prime)

        for absent in ("evac", "dock", "spot", "train", "poweroff"):
            assert f'send_simple_command("{absent}' not in source


class TestFavoritesAttributeAndService:
    """Three routes to the same favourites, each answering a case the
    others cannot.

    Buttons need no setup and work by voice. The `favorites` attribute
    costs no entity and covers automations that iterate, templates, and
    the xiaomi-vacuum-map-card menu, which reads attributes. The
    run_favorite service keys on the ID.

    THE ID IS THE POINT. A name is what the user typed in the iRobot app
    and can change there at any time; an automation keyed on it breaks
    silently. A button or a select could only ever offer the name."""

    @pytest.mark.asyncio
    async def test_the_attribute_carries_id_and_name(self):
        from custom_components.roomba_plus.button_prime import (
            async_favorites_attribute,
        )

        result = await async_favorites_attribute(
            _entry([_favorite("f1", "Evening")])
        )

        assert result == [{"id": "f1", "name": "Evening"}]

    @pytest.mark.asyncio
    async def test_deleted_favourites_are_not_listed(self):
        from custom_components.roomba_plus.button_prime import (
            async_favorites_attribute,
        )

        result = await async_favorites_attribute(
            _entry([_favorite("f1", deleted=True), _favorite("f2", hidden=True)])
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_running_by_id_sends_the_commands(self):
        from custom_components.roomba_plus.button_prime import async_run_favorite

        command = MagicMock()
        entry = _entry([_favorite("f1", commands=[command])])

        assert await async_run_favorite(entry, "f1") is True
        entry.runtime_data.prime_robot.send_routine_command_via_cmd_topic \
            .assert_awaited_once_with(command)

    @pytest.mark.asyncio
    async def test_an_unknown_id_reports_failure(self):
        """Rather than silently doing nothing. The service turns this
        into an error the user sees -- a favourite that quietly does not
        run is the failure this whole feature was shaped to avoid."""
        from custom_components.roomba_plus.button_prime import async_run_favorite

        assert await async_run_favorite(_entry([_favorite("f1")]), "f9") is False

    @pytest.mark.asyncio
    async def test_a_favourite_with_no_commands_reports_failure(self):
        from custom_components.roomba_plus.button_prime import async_run_favorite

        assert await async_run_favorite(_entry([_favorite("f1", commands=[])]), "f1") is False


class TestFavoriteButtonsAreOptional:
    """On by default, because they are the only route needing no setup.

    Off is for someone with many favourites who drives everything from
    automations: fifteen favourites means fifteen entities on the device
    page, and the attribute plus the service cover that person entirely."""

    @pytest.mark.asyncio
    async def test_buttons_are_created_by_default(self):
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        assert len(await async_build_prime_buttons(_entry([_favorite("f1")]))) == 2

    @pytest.mark.asyncio
    async def test_the_option_suppresses_only_the_favourites(self):
        """Locate stays. It is one entity and has no alternative route --
        no attribute lists it, no service calls it."""
        from custom_components.roomba_plus.button_prime import (
            async_build_prime_buttons,
        )

        entry = _entry([_favorite("f1"), _favorite("f2")])
        entry.options = {"prime_favorite_buttons": False}

        entities = await async_build_prime_buttons(entry)

        assert len(entities) == 1
        assert not any(hasattr(e, "_favorite_id") for e in entities)

    def test_the_default_is_on(self):
        from custom_components.roomba_plus.const import (
            DEFAULT_PRIME_FAVORITE_BUTTONS,
        )

        assert DEFAULT_PRIME_FAVORITE_BUTTONS is True


class TestDockButtons:
    """The three dock controls the iRobot app shows.

    Requested by @chairstacker, who screenshotted the app's dock panel:
    Empty Bin, Wash mop / rinse dock, Stop mop dry.

    The wire strings were then confirmed from CommandType's @SerialName
    annotations -- the same source as `find`, which already works. Not
    guessed, which matters: a command the robot accepts and ignores is
    worse than an absent button, and that is why `schedHold` never
    shipped."""

    def test_the_commands_are_the_confirmed_ones(self):
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        assert {c.command for c in PRIME_DOCK_COMMANDS} == {
            "evac", "washpad", "stoppaddry", "drypad",
        }

    def test_drying_can_be_started(self):
        """REVERSED after @DaRealGuGu explained the cost.

        `drypad` was excluded because the app does not offer it. But
        stopping the drying leaves no way to restart it except another
        full wash -- a tank of water for nothing.

        The original reasoning conflated two things: guessing at a
        COMMAND is what this project refuses to do, while offering a
        WORKFLOW the app does not is a separate question. `drypad` is a
        confirmed wire string from the same enum as the three a tester
        has now pressed successfully."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        assert "drypad" in {c.command for c in PRIME_DOCK_COMMANDS}

    def test_the_read_and_plumbing_commands_stay_out(self):
        """`flushsluice`, `flrefill` and `querydock` are in the same
        enum. Nobody has asked for them, none has an effect a user could
        verify, and querydock is a read dressed as a command."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        commands = {c.command for c in PRIME_DOCK_COMMANDS}
        for absent in ("flushsluice", "flrefill", "querydock"):
            assert absent not in commands

    def test_washing_has_no_stop_button(self):
        """There is no `stopwashpad` in the enum at all, so washing
        evidently runs to completion. `evac`/`stopevac` and
        `drypad`/`stoppaddry` do come in pairs."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        assert "stopwashpad" not in {c.command for c in PRIME_DOCK_COMMANDS}

    @pytest.mark.asyncio
    async def test_buttons_appear_when_the_dock_supports_them(self):
        from custom_components.roomba_plus.button_prime import (
            PrimeDockButton,
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(_entry(dock=True))

        assert sum(isinstance(e, PrimeDockButton) for e in entities) == 4

    @pytest.mark.asyncio
    async def test_a_dock_that_cannot_do_something_gets_no_button(self):
        """A robot without a self-emptying base still reports its own
        capabilities happily -- the DOCK flags are what say whether a
        base is there."""
        from custom_components.roomba_plus.button_prime import (
            PrimeDockButton,
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(_entry(dock=False))

        assert not any(isinstance(e, PrimeDockButton) for e in entities)

    @pytest.mark.asyncio
    async def test_unknown_capabilities_still_get_buttons(self):
        """Only an explicit 0 means absent. A robot that has not reported
        its dock yet must not silently lose the controls."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.button_prime import (
            PrimeDockButton,
            async_build_prime_buttons,
        )

        entry = _entry()
        entry.runtime_data.prime_status_coordinator.data = None

        entities = await async_build_prime_buttons(entry)

        assert sum(isinstance(e, PrimeDockButton) for e in entities) == 4

    @pytest.mark.asyncio
    async def test_pressing_sends_the_wire_string(self):
        from custom_components.roomba_plus.button_prime import (
            PrimeDockButton,
            async_build_prime_buttons,
        )

        entry = _entry(dock=True)
        buttons = await async_build_prime_buttons(entry)
        empty = next(
            b for b in buttons
            if isinstance(b, PrimeDockButton) and b._command.command == "evac"
        )

        await empty.async_press()

        entry.runtime_data.prime_robot.send_simple_command.assert_awaited_once_with(
            "evac"
        )

    def test_all_three_are_translated_everywhere(self):
        import json
        from pathlib import Path

        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        base = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for locale_file in sorted((base / "translations").glob("*.json")):
            buttons = json.loads(locale_file.read_text(encoding="utf-8"))["entity"]["button"]
            for command in PRIME_DOCK_COMMANDS:
                assert command.key in buttons, f"{locale_file.name}: {command.key}"


def _enabled(entities):
    """Entities a default Home Assistant install exposes."""
    return [
        entity
        for entity in entities
        if getattr(entity, "_attr_entity_registry_enabled_default", True)
    ]


class TestFavouriteButtonsFollowTheList:
    """Buttons were built exactly once, at setup. A favourite created in
    the iRobot app never got one, and a deleted favourite left its
    button behind pointing at an id the server no longer knows.

    This is @DaRealGuGu's schedule-switch problem with a different
    entity type, and it reuses that fix's two lessons: match on the id
    rather than the content, and read the registry rather than a set of
    what this session added.
    """

    @staticmethod
    def _entry(favourites):
        from unittest.mock import MagicMock

        entry = MagicMock()
        entry.runtime_data.blid = "BLID123"
        entry.runtime_data.prime_favorites = favourites
        return entry

    def test_a_button_per_favourite(self):
        from custom_components.roomba_plus.button_prime import (
            build_prime_favorite_buttons,
        )

        buttons = build_prime_favorite_buttons(
            self._entry([{"id": "a1", "name": "Kitchen"}, {"id": "b2"}])
        )

        assert len(buttons) == 2

    def test_an_entry_without_an_id_is_skipped(self):
        """It cannot be run and cannot be identified — a button for it
        would be dead, and its unique_id would collide with the next
        such entry."""
        from custom_components.roomba_plus.button_prime import (
            build_prime_favorite_buttons,
        )

        buttons = build_prime_favorite_buttons(
            self._entry([{"name": "no id"}, {"id": "", "name": "empty"}])
        )

        assert buttons == []

    def test_the_list_is_re_read_on_each_call(self):
        """The whole point: the builder reads `prime_favorites` rather
        than capturing it, so the coordinator refreshing that list
        changes what this produces."""
        from custom_components.roomba_plus.button_prime import (
            build_prime_favorite_buttons,
        )

        favourites = [{"id": "a1"}]
        entry = self._entry(favourites)
        assert len(build_prime_favorite_buttons(entry)) == 1

        favourites.append({"id": "b2"})
        assert len(build_prime_favorite_buttons(entry)) == 2

        favourites.clear()
        assert build_prime_favorite_buttons(entry) == []

    def test_the_refresh_mutates_the_list_in_place(self):
        """Entities were handed this exact list object at setup.
        Rebinding the attribute would leave every button reading the old
        one — which is invisible in tests that only check the
        coordinator."""
        import inspect

        from custom_components.roomba_plus import prime_coordinator

        source = inspect.getsource(
            prime_coordinator.PrimeScheduleCoordinator._async_refresh_favourites
        )

        assert "current.clear()" in source
        assert "current.extend(" in source
        assert "prime_favorites =" not in source

    def test_favourites_have_no_push_channel(self):
        """Recorded because it is the reason for the design. All nine
        named shadows were checked and none carries favourites — they
        are a cloud REST resource, and the robot never learns that one
        changed."""
        import inspect

        from custom_components.roomba_plus import prime_coordinator

        source = inspect.getsource(
            prime_coordinator.PrimeScheduleCoordinator._async_refresh_favourites
        )

        assert "no push channel" in source.lower()


class TestEveryFavouriteButtonIsRegisterable:
    """Home Assistant will not register an entity without a unique id.
    It lives in the state machine and not the registry: it cannot be
    renamed, hidden, assigned to an area, or referred to reliably. The
    UI calls that "Unmanageable".

    `PrimeFavoriteButton` never set one, and no test noticed across
    5445 of them.

    @chairstacker chased it for three rounds as a leftover registry
    entry. His last report ruled that out: the entity tracks his
    favourites exactly — appears when he creates one, updates when he
    adds another, disappears when he deletes it. A stale entry does none
    of those. It was never stale; it was never registered.

    @ratpic83's log carried the other half from a different robot:
    "attempts to attach a device to an entity without a unique ID".
    """

    def test_the_button_has_a_unique_id(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.button_prime import (
            PrimeFavoriteButton,
        )

        entry = MagicMock()
        button = PrimeFavoriteButton(
            "BLID123", entry, "8a106edbf128112254cd182814b426bd", "Test_03 UR"
        )

        assert button.unique_id
        assert "8a106edbf128112254cd182814b426bd" in button.unique_id

    def test_two_favourites_do_not_collide(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.button_prime import (
            PrimeFavoriteButton,
        )

        entry = MagicMock()
        first = PrimeFavoriteButton("BLID123", entry, "5ae5e78c", "Kitchen")
        second = PrimeFavoriteButton("BLID123", entry, "8a106edb", "UR")

        assert first.unique_id != second.unique_id

    def test_renaming_in_the_app_does_not_move_the_entity(self):
        """The id is the favourite's own, not its position or name — a
        favourite renamed in the iRobot app must not break an
        automation."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.button_prime import (
            PrimeFavoriteButton,
        )

        entry = MagicMock()
        before = PrimeFavoriteButton("BLID123", entry, "5ae5e78c", "Kitchen")
        after = PrimeFavoriteButton("BLID123", entry, "5ae5e78c", "Küche")

        assert before.unique_id == after.unique_id


class TestNoPrimeEntityShipsWithoutAUniqueId:
    """The census that would have caught the favourite button.

    A missing unique id produces no error, no warning at the entity, and
    no failing test — only an "Unmanageable" row a user has to notice
    and report, which took three rounds.
    """

    def test_every_prime_entity_class_sets_one(self):
        import ast
        import pathlib

        base = pathlib.Path("custom_components/roomba_plus")
        offenders: list[str] = []

        for path in base.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not node.name.startswith("Prime"):
                    continue
                # An EntityDescription describes an entity and is not
                # one. It carries a `key`, and the entity built from it
                # is what needs the unique id.
                if node.name.endswith("Description"):
                    continue
                # ENTITIES ONLY. The first version matched on the name
                # and flagged nine coordinators, dataclasses and entity
                # DESCRIPTIONS -- none of which HA registers, and none
                # of which should carry a unique id. A test that fires
                # on nine false positives is one somebody switches off.
                bases = {
                    getattr(b, "id", None) or getattr(b, "attr", None)
                    for b in node.bases
                }
                if not any(
                    base and ("Entity" in base or base.startswith("Prime"))
                    for base in bases
                ):
                    continue
                source = ast.get_source_segment(path.read_text(), node) or ""
                if "_attr_unique_id" in source or "unique_id" in source:
                    continue
                # A base class that concrete subclasses complete is
                # fine; those are named with a leading underscore.
                if node.name.startswith("_"):
                    continue
                offenders.append(f"{path.name}::{node.name}")

        assert not offenders, (
            f"Prime entity classes with no unique_id: {offenders}. "
            "Home Assistant will not register these, so they arrive as "
            "'Unmanageable' and cannot be renamed, hidden or assigned "
            "to an area."
        )
