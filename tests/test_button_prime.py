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


def _enabled(entities):
    """Entities a default Home Assistant install exposes."""
    return [
        entity
        for entity in entities
        if getattr(entity, "_attr_entity_registry_enabled_default", True)
    ]


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

        assert len(_enabled(entities)) == 3  # two favourites + locate

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

        assert len(_enabled(entities)) == 1  # locate only

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

        assert len(_enabled(entities)) == 1

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

        assert len(_enabled(await async_build_prime_buttons(_entry([_favorite("f1")]))) == 2

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

        assert len(_enabled(entities)) == 1
        assert not any(hasattr(e, "_favorite_id") for e in _enabled(entities))

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
    async def test_unsupported_dock_buttons_are_disabled(self):
        """A robot without a self-emptying base still reports its own
        capabilities happily -- the DOCK flags are what say whether a
        base is there."""
        from custom_components.roomba_plus.button_prime import (
            PrimeDockButton,
            async_build_prime_buttons,
        )

        entities = await async_build_prime_buttons(_entry(dock=False))

        dock_buttons = [e for e in entities if isinstance(e, PrimeDockButton)]
        assert len(dock_buttons) == 4
        assert not _enabled(dock_buttons)

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
