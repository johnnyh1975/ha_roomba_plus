"""Enabling and disabling Prime cleaning schedules."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest


@dataclass
class _Options:
    name: str = "Weekdays"
    enabled: bool = True
    deleted: bool = False


@dataclass
class _Schedule:
    schedule_id: str = "s1"
    options: _Options | None = field(default_factory=_Options)


@dataclass
class _Container:
    household_schedule_id: str = "c1"
    schedules: list = field(default_factory=list)


def _entry(containers=None, raises=False):
    entry = MagicMock()
    entry.runtime_data.blid = "BLID"
    entry.runtime_data.prime_household_id = "HH"
    robot = entry.runtime_data.prime_robot
    if raises:
        robot.get_schedules = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        robot.get_schedules = AsyncMock(
            return_value=MagicMock(household_schedules=containers or [])
        )
    robot.update_schedules = AsyncMock()
    return entry


class TestReadingScheduleContainers:
    """`get_schedules()` returns a two-level structure, and the write
    endpoint addresses the OUTER level.

    The calendar flattens straight to the inner schedules because
    occurrences are all it needs -- which throws away the
    household_schedule_id that update_schedules() requires. So this
    reads the structure itself rather than reusing that view."""

    @pytest.mark.asyncio
    async def test_the_container_id_is_preserved(self):
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        entry = _entry([_Container("c1", [_Schedule("s1")])])

        containers = await async_read_schedule_containers(entry)

        assert containers[0][0] == "c1"
        assert containers[0][1][0].schedule_id == "s1"

    @pytest.mark.asyncio
    async def test_a_failing_call_yields_nothing(self):
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        assert await async_read_schedule_containers(_entry(raises=True)) == []

    @pytest.mark.asyncio
    async def test_no_household_id_yields_nothing(self):
        """Reachable early in setup, before household resolution."""
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        entry = _entry([_Container()])
        entry.runtime_data.prime_household_id = None

        assert await async_read_schedule_containers(entry) == []


class TestTogglingASchedule:
    """update_schedules() takes the COMPLETE list for a container, so
    this is a forced read-modify-write.

    Sending only the changed schedule would delete every other one --
    the same shape as set_virtual_wall, where a partial list silently
    removes the zones it omits."""

    def _switch(self, entry, container="c1", schedule="s1"):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        switch = PrimeScheduleSwitch(entry, container, schedule, "Weekdays")
        switch.async_write_ha_state = MagicMock()
        return switch

    @pytest.mark.asyncio
    async def test_turning_off_writes_the_whole_list(self):
        """Two schedules in, two schedules out -- the untouched one must
        survive."""
        entry = _entry([_Container("c1", [
            _Schedule("s1", _Options(enabled=True)),
            _Schedule("s2", _Options("Saturday", enabled=True)),
        ])])

        await self._switch(entry).async_turn_off()

        sent = entry.runtime_data.prime_robot.update_schedules.await_args.args[2]
        assert len(sent) == 2
        assert {s.schedule_id: s.options.enabled for s in sent} == {
            "s1": False, "s2": True,
        }

    @pytest.mark.asyncio
    async def test_turning_on_sets_the_flag(self):
        entry = _entry([_Container("c1", [_Schedule("s1", _Options(enabled=False))])])

        await self._switch(entry).async_turn_on()

        sent = entry.runtime_data.prime_robot.update_schedules.await_args.args[2]
        assert sent[0].options.enabled is True

    @pytest.mark.asyncio
    async def test_the_container_id_is_passed_not_guessed(self):
        entry = _entry([_Container("c-real", [_Schedule("s1")])])

        await self._switch(entry, container="c-real").async_turn_off()

        assert entry.runtime_data.prime_robot.update_schedules.await_args.args[1] == (
            "c-real"
        )

    @pytest.mark.asyncio
    async def test_a_schedule_deleted_since_setup_is_not_written(self):
        """Writing the list without it would delete it a second time,
        and writing it back unchanged is pointless."""
        entry = _entry([_Container("c1", [_Schedule("s2")])])

        await self._switch(entry, schedule="s1").async_turn_off()

        entry.runtime_data.prime_robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_schedule_without_options_is_left_alone(self):
        """Inventing an options object would write defaults for every
        other field of that schedule -- its name, days and commands."""
        entry = _entry([_Container("c1", [_Schedule("s1", None)])])

        await self._switch(entry).async_turn_off()

        entry.runtime_data.prime_robot.update_schedules.assert_not_awaited()


class TestSwitchState:
    def _switch(self, entry):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        return PrimeScheduleSwitch(entry, "c1", "s1", "Weekdays")

    @pytest.mark.asyncio
    async def test_the_state_is_read_from_the_robot(self):
        entry = _entry([_Container("c1", [_Schedule("s1", _Options(enabled=False))])])
        switch = self._switch(entry)

        await switch.async_update()

        assert switch.is_on is False

    def test_an_unread_switch_is_unavailable_not_off(self):
        """Rendering unknown as off would have someone believe their
        cleaning schedule was disabled."""
        switch = self._switch(_entry())

        assert switch.available is False

    @pytest.mark.asyncio
    async def test_it_becomes_available_once_read(self):
        entry = _entry([_Container("c1", [_Schedule("s1", _Options(enabled=True))])])
        switch = self._switch(entry)

        await switch.async_update()

        assert switch.available is True

    def test_the_unique_id_is_keyed_on_the_schedule_id(self):
        """Not on list position: a schedule deleted in the app shifts
        every index after it, and an index-keyed switch would silently
        start controlling a different routine."""
        switch = self._switch(_entry())

        assert switch.unique_id.endswith("_schedule_s1")


class TestNamingIsTranslated:
    """The schedule NAME comes from the iRobot app and cannot be
    translated. The word "Schedule" can be, and a first draft here set
    _attr_name to the bare name -- leaving an entity called just
    "Weekdays" with no indication of what it controls.

    The fallback was worse: an unnamed schedule got
    f"Schedule {id}" in hard-coded English, which is exactly what a
    translation file is for."""

    def _switch(self, name="Weekdays", schedule_id="s1"):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        return PrimeScheduleSwitch(_entry(), "c1", schedule_id, name)

    def test_a_translation_key_is_used(self):
        switch = self._switch()

        assert switch.translation_key == "prime_schedule"

    def test_the_user_name_is_a_placeholder(self):
        """So the prefix translates and the name passes through."""
        switch = self._switch("Salle à manger")

        assert switch.translation_placeholders == {"schedule": "Salle à manger"}

    def test_an_unnamed_schedule_falls_back_to_its_id(self):
        """Rather than to an English string. The prefix still translates,
        so a German user sees "Zeitplan – s1" and not "Schedule s1"."""
        switch = self._switch(name="", schedule_id="s7")

        assert switch.translation_placeholders == {"schedule": "s7"}

    def test_the_entity_id_slug_is_locale_independent(self):
        """has_entity_name plus a translation_key makes HA derive the
        entity_id from the TRANSLATED name -- different ids per language
        on first registration. This project has hit that before."""
        assert self._switch(schedule_id="s3").suggested_object_id == "schedule_s3"

    def test_the_slug_does_not_follow_the_schedule_name(self):
        """Renaming a routine in the iRobot app must not rename the
        entity out from under an automation."""
        assert self._switch("Weekdays", "s1").suggested_object_id == (
            self._switch("Renamed entirely", "s1").suggested_object_id
        )

    def test_every_locale_has_the_key(self):
        """A translation_key with no entry renders as the raw key."""
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        for locale_file in sorted((base / "translations").glob("*.json")):
            switches = json.loads(locale_file.read_text(encoding="utf-8"))["entity"]["switch"]
            assert "prime_schedule" in switches, locale_file.name
            assert "{schedule}" in switches["prime_schedule"]["name"], locale_file.name


class TestScheduleSwitchesDoNotPoll:
    """SwitchEntity polls every 30 seconds by default, and async_update
    here is a cloud round trip.

    Three schedules would mean roughly 8,600 requests a day for data that
    changes when somebody edits a schedule in the iRobot app -- which is
    to say almost never. The state is read when the entity is added and
    again after this integration writes it.

    Found by asking what each new entity costs per day, not by anything
    failing."""

    def test_polling_is_off(self):
        """Asserted on the source rather than the attribute: in this HA
        version _attr_should_poll is a property on the base class, so
        reading it back through the class gives the descriptor rather
        than the value."""
        import inspect

        from custom_components.roomba_plus import prime_schedule_switch

        source = inspect.getsource(prime_schedule_switch.PrimeScheduleSwitch)

        assert "_attr_should_poll = False" in source

    def test_the_state_is_still_read_when_added(self):
        """Turning polling off must not leave the switch permanently
        unavailable -- async_added_to_hass does the initial read."""
        import inspect

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        source = inspect.getsource(PrimeScheduleSwitch.async_added_to_hass)

        assert "async_update" in source

    def test_writing_refreshes_the_state(self):
        """The other half: after this integration changes the flag, the
        UI must reflect it without waiting for a reload."""
        import inspect

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        source = inspect.getsource(PrimeScheduleSwitch._async_set_enabled)

        assert "async_write_ha_state" in source
