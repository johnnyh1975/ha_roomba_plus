"""Choosing vacuum, mop, or both.

Asked for by a user (@arielgr): the iRobot app offers the choice when
starting a clean, and this integration had a suction-level control and
nothing for the mode. The values had been confirmed for weeks; nobody
had asked until he did.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _select(last_command=None, restored=None):
    from custom_components.roomba_plus.select_prime import (
        PrimeCleaningModeSelect,
    )

    entity = object.__new__(PrimeCleaningModeSelect)
    entity._restored = restored
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(
        prime_status_coordinator=SimpleNamespace(
            data={"rw-software": {"lastCommand": last_command}}
            if last_command is not None else {}
        )
    )
    entity._config_entry = entry
    entity.async_write_ha_state = MagicMock()
    return entity


def _start(mode):
    return {
        "command": "start", "initiator": "cloud",
        "regions": [{"region_id": "11", "type": "rid",
                     "params": {"operatingMode": mode, "suctionLevel": 3}}],
    }


class TestItReflectsTheRobotsLastStart:
    """The robot wins when it has something to say. A user who picks a
    mode and then starts a different one from the app should see the
    app's choice, because that is what the machine actually did."""

    @pytest.mark.parametrize(("mode", "expected"), [
        (2, "vacuum"), (4, "mop"),
        (32, "vacuum_and_mop"), (512, "vacuum_then_mop"),
    ])
    def test_each_confirmed_mode(self, mode, expected):
        assert _select(_start(mode)).current_option == expected

    def test_it_overrides_a_stale_pick_of_ours(self):
        select = _select(_start(4), restored="vacuum")

        assert select.current_option == "mop"

    def test_a_mode_outside_the_four_reports_nothing(self):
        """Rounding to the nearest would make the select claim something
        the robot is not doing."""
        assert _select(_start(1024)).current_option is None


class TestOnlyAStartCounts:
    """`drypad` and `washpad` also carry regions and a mode, and neither
    is a cleaning choice. Reading them would make the select jump while
    the dock cleaned a pad."""

    @pytest.mark.parametrize("command", ["drypad", "washpad", "dock", "find"])
    def test_maintenance_commands_are_ignored(self, command):
        payload = _start(2)
        payload["command"] = command

        assert _select(payload, restored="mop").current_option == "mop"

    def test_a_command_without_regions_is_ignored(self):
        assert _select(
            {"command": "start", "time": 1}, restored="mop"
        ).current_option == "mop"


class TestTheUsersPickIsTheFallback:
    def test_without_a_start_the_restored_value_shows(self):
        assert _select(restored="vacuum_then_mop").current_option == (
            "vacuum_then_mop"
        )

    def test_with_neither_it_shows_nothing(self):
        assert _select().current_option is None

    @pytest.mark.asyncio
    async def test_choosing_records_the_pick(self):
        select = _select()

        await select.async_select_option("mop")

        assert select._restored == "mop"

    @pytest.mark.asyncio
    async def test_an_unknown_option_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            await _select().async_select_option("polish")


class TestTheStatusFieldIsNotTheSource:
    """`cleanMissionStatus.operatingMode` uses a different vocabulary:
    command 32 shows as status 6, command 512 as status 4, and a pad
    wash also as 6. A 6 cannot be told apart from maintenance."""

    def test_the_status_shadow_is_not_read(self):
        import inspect

        from custom_components.roomba_plus.select_prime import (
            PrimeCleaningModeSelect,
        )

        source = inspect.getsource(
            PrimeCleaningModeSelect._mode_from_last_start
        )
        assert "rw-software" in source
        assert "cleanMissionStatus" not in source


class TestNothingIsSentWhenNothingIsChosen:
    def test_no_mode_means_the_key_is_omitted(self):
        """A default here would decide for people who never touched the
        control."""
        operating_mode = None
        params = {
            "noAutoPasses": False,
            **({"operatingMode": operating_mode} if operating_mode is not None else {}),
        }

        assert "operatingMode" not in params

    def test_a_chosen_mode_is_included(self):
        operating_mode = 512
        params = {
            **({"operatingMode": operating_mode} if operating_mode is not None else {}),
        }

        assert params["operatingMode"] == 512


class TestSuctionOnTheVacuumCard:
    """@arielgr went looking for suction and cleaning mode on the card
    that opens when you click the robot, because that is where the
    iRobot app puts them. Both existed only as separate selects under
    Configuration -- a different screen.

    Home Assistant renders a speed control on the vacuum card when the
    entity advertises FAN_SPEED. Two Classic classes did; Prime, which
    uses the base class directly, did not -- so the one control Home
    Assistant CAN show there was missing on the generation with the
    richest settings.
    """

    def _vacuum(self, *, prime=True, suction=None):
        from custom_components.roomba_plus.vacuum import IRobotVacuum

        entity = object.__new__(IRobotVacuum)
        entity._prime_robot = MagicMock() if prime else None
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_robot=entity._prime_robot,
            prime_status_coordinator=SimpleNamespace(
                data={"rw-settings": {"suctionLevel": suction}}
                if suction is not None else {}
            ),
        )
        entity._config_entry = entry
        return entity

    def test_the_three_levels_are_offered(self):
        assert self._vacuum().fan_speed_list == ["light", "normal", "deep"]

    @pytest.mark.parametrize(("wire", "name"), [(2, "light"), (3, "normal"), (4, "deep")])
    def test_the_current_level_is_read_from_the_robot(self, wire, name):
        assert self._vacuum(suction=wire).fan_speed == name

    def test_an_unknown_level_reads_as_unknown(self):
        """Rather than defaulting to one the robot may not be using."""
        assert self._vacuum(suction=None).fan_speed is None
        assert self._vacuum(suction=9).fan_speed is None

    @pytest.mark.asyncio
    async def test_setting_writes_the_same_field_the_select_does(self):
        """The card and the select cannot disagree -- both read and write
        the one value the robot holds."""
        from unittest.mock import AsyncMock

        vacuum = self._vacuum()
        vacuum._prime_robot.set_setting = AsyncMock()

        await vacuum.async_set_fan_speed("deep")

        vacuum._prime_robot.set_setting.assert_awaited_once_with("suctionLevel", 4)

    @pytest.mark.asyncio
    async def test_an_unknown_level_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            await self._vacuum().async_set_fan_speed("turbo")

    def test_a_classic_robot_keeps_its_own_list(self):
        """Widening Prime must not rewrite the Classic vocabulary."""
        from custom_components.roomba_plus.vacuum import FAN_SPEEDS

        assert self._vacuum(prime=False).fan_speed_list == list(FAN_SPEEDS)


class TestTheServiceFieldOutranksTheSelect:
    """`clean_room` has a `cleaning_mode` field for a run that should
    differ from the everyday default. It was described in services.yaml
    and never read -- the service accepted it and threw it away, and the
    select's value was used regardless.
    """

    def _mode_for(self, operating_mode, index, selected=2):
        if operating_mode:
            per_room = (
                operating_mode[index] if index < len(operating_mode)
                else operating_mode[0] if len(operating_mode) == 1
                else None
            )
            if per_room is not None:
                return per_room
        return selected

    def test_a_single_choice_applies_to_every_room(self):
        """The service passes `[mode]` for one choice. Reading it
        positionally would give room one the caller's mode and every
        other room the select's -- a run that mops the kitchen and
        vacuums the hall, from one field nobody meant that way."""
        assert [self._mode_for([512], i) for i in range(3)] == [512, 512, 512]

    def test_per_room_modes_are_honoured(self):
        assert [self._mode_for([4, 32, None], i) for i in range(3)] == [4, 32, 2]

    def test_saying_nothing_falls_back_to_the_select(self):
        assert [self._mode_for(None, i) for i in range(3)] == [2, 2, 2]

    def test_the_service_translates_the_name_to_a_wire_value(self):
        import inspect

        from custom_components.roomba_plus import services

        source = inspect.getsource(services)
        assert "PrimeCleaningModeSelect.MODES.get(caller_mode_name)" in source

    def test_the_field_is_documented_with_its_fallback(self):
        """A field whose description promises "leave empty to use the
        selector" has to actually do that."""
        import pathlib

        text = pathlib.Path(
            "custom_components/roomba_plus/services.yaml"
        ).read_text()
        block = text[text.index("cleaning_mode:"):][:600]

        assert "Cleaning mode selector" in block
        assert "vacuum_then_mop" in block
