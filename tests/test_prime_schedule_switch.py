"""Enabling and disabling Prime cleaning schedules.

THESE FIXTURES BUILD THEIR DATA THROUGH THE LIBRARY, NOT BY HAND, AND
THAT IS THE POINT OF THIS FILE.

An earlier version declared its own `_Schedule` dataclass with
`schedule_id` and `options` as ATTRIBUTES. The library does not return
that: `SchedulesList.schedules` is `list[dict]`, as its own docstring
says. Every test here passed against a shape no server has ever sent,
while the feature created zero switches for every real user for its
entire life.

The test did not merely miss the bug. It recorded the wrong shape as
correct, so anyone checking "is this covered?" got yes.

So the fixtures below start from real server JSON and go through
`SchedulesResponse.from_json()` -- the same call the integration makes.
If the library's shape changes, these fail, which is what a test is
for.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from roombapy_prime.models.schedules_dnd import SchedulesResponse


def _schedule_json(schedule_id="s1", name="Weekdays", enabled=True, deleted=False,
                   with_options=True):
    """One schedule exactly as the server sends it."""
    entry = {"schedule_id": schedule_id}
    if with_options:
        entry["options"] = {
            "name": name, "enabled": enabled, "deleted": deleted,
            "frequency": "WEEKLY", "start": {"day": [1], "hour": 9, "min": 0},
        }
    return entry


def _response(containers):
    """containers: list of (household_schedule_id, [schedule json, ...])."""
    return SchedulesResponse.from_json({"household_schedules": [
        {"household_schedule_id": container_id, "schedules": list(schedules)}
        for container_id, schedules in containers
    ]})


def _entry(containers=None, raises=False):
    entry = MagicMock()
    entry.runtime_data.blid = "BLID"
    entry.runtime_data.prime_household_id = "HH"
    robot = entry.runtime_data.prime_robot
    if raises:
        robot.get_schedules = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        robot.get_schedules = AsyncMock(
            return_value=_response(containers or [])
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

        entry = _entry([("c1", [_schedule_json("s1")])])

        containers = await async_read_schedule_containers(entry)

        assert containers[0][0] == "c1"
        assert containers[0][1][0].schedule_id == "s1"

    @pytest.mark.asyncio
    async def test_a_failing_call_is_distinguishable_from_an_empty_one(self):
        """None, not [] -- see TestAFailedReadIsNotAnEmptyOne below for
        what depended on telling the two apart."""
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        assert await async_read_schedule_containers(_entry(raises=True)) is None

    @pytest.mark.asyncio
    async def test_no_household_id_yields_nothing(self):
        """Reachable early in setup, before household resolution."""
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        entry = _entry([("c1", [])])
        entry.runtime_data.prime_household_id = None

        # None: without a household id nothing was asked, so "no
        # schedules" would be a claim this never checked.
        assert await async_read_schedule_containers(entry) is None


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
        entry = _entry([("c1", [
            _schedule_json("s1", enabled=True),
            _schedule_json("s2", name="Saturday", enabled=True),
        ])])

        await self._switch(entry).async_turn_off()

        sent = entry.runtime_data.prime_robot.update_schedules.await_args.args[2]
        assert len(sent) == 2
        assert {s.schedule_id: s.options.enabled for s in sent} == {
            "s1": False, "s2": True,
        }

    @pytest.mark.asyncio
    async def test_turning_on_sets_the_flag(self):
        entry = _entry([("c1", [_schedule_json("s1", enabled=False)])])

        await self._switch(entry).async_turn_on()

        sent = entry.runtime_data.prime_robot.update_schedules.await_args.args[2]
        assert sent[0].options.enabled is True

    @pytest.mark.asyncio
    async def test_the_container_id_is_passed_not_guessed(self):
        entry = _entry([("c-real", [_schedule_json("s1")])])

        await self._switch(entry, container="c-real").async_turn_off()

        assert entry.runtime_data.prime_robot.update_schedules.await_args.args[1] == (
            "c-real"
        )

    @pytest.mark.asyncio
    async def test_a_schedule_deleted_since_setup_is_not_written(self):
        """Writing the list without it would delete it a second time,
        and writing it back unchanged is pointless."""
        entry = _entry([("c1", [_schedule_json("s2")])])

        await self._switch(entry, schedule="s1").async_turn_off()

        entry.runtime_data.prime_robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_schedule_whose_state_was_never_sent_is_left_alone(self):
        """Inventing an options object would write defaults for every
        other field of that schedule -- its name, days and commands."""
        entry = _entry([("c1", [_schedule_json("s1", with_options=False)])])

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
        entry = _entry([("c1", [_schedule_json("s1", enabled=False)])])
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
        entry = _entry([("c1", [_schedule_json("s1", enabled=True)])])
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

    @pytest.mark.asyncio
    async def test_writing_refreshes_the_state(self):
        """The other half: after this integration changes the flag, the
        UI must reflect it without waiting for a reload.

        Asserted on behaviour, not on the source text. The previous
        version grepped `_async_set_enabled` for "async_write_ha_state"
        and broke the moment that body moved into the locked helper --
        a test that tracks where code lives rather than what it does.
        """
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        entry = _entry([("c1", [_schedule_json("s1", enabled=True)])])
        switch = PrimeScheduleSwitch(entry, "c1", "s1", "Weekdays")
        switch.async_write_ha_state = MagicMock()

        await switch.async_turn_off()

        assert switch._attr_is_on is False
        switch.async_write_ha_state.assert_called_once()


class TestTheSchedulesAreParsedNotReadOffDicts:
    """The bug this whole file's fixtures were rewritten for.

    `SchedulesList.schedules` is `list[dict]`. `async_read_schedule_containers`
    used to pass those dicts straight through, and every caller read
    `.schedule_id` / `.options` off them -- which returns None on a dict.

    Three consequences, one cause:
      - switch.py skipped every schedule, so NO SWITCH WAS EVER CREATED
        for any user, for the entire life of this feature
      - async_update read the enabled flag as False
      - the write path would have handed dicts to update_schedules(),
        which does `[s.to_json() for s in schedules]` -> AttributeError

    None of it raised. The feature was simply absent, and its tests were
    green against a shape no server sends.
    """

    @pytest.mark.asyncio
    async def test_containers_carry_parsed_schedules(self):
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        containers = await async_read_schedule_containers(
            _entry([("c1", [_schedule_json("s1", name="Weekdays")])])
        )

        schedule = containers[0][1][0]
        assert isinstance(schedule, HouseholdSchedule)
        assert schedule.options.name == "Weekdays"

    @pytest.mark.asyncio
    async def test_the_switch_platform_creates_one_switch_per_schedule(self):
        """The end of the chain, and the thing a user would have noticed:
        entities. Asserted through switch.py's own setup, not through the
        reader, because the reader was never the visible symptom."""
        from custom_components.roomba_plus import switch as switch_module
        from custom_components.roomba_plus.models import ConnectionType

        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY

        created: list = []
        await switch_module.async_setup_entry(MagicMock(), entry, created.extend)

        names = [type(e).__name__ for e in created]
        assert names.count("PrimeScheduleSwitch") == 2

    @pytest.mark.asyncio
    async def test_writing_back_produces_objects_update_schedules_can_serialise(self):
        """update_schedules() calls .to_json() on every element. Dicts
        raise there -- after the request has already been decided on, so
        the failure would land mid-write rather than at read time."""
        entry = _entry([("c1", [
            _schedule_json("s1"), _schedule_json("s2", name="Saturday"),
        ])])

        await self._turn_off(entry)

        sent = entry.runtime_data.prime_robot.update_schedules.await_args.args[2]
        assert [s.to_json()["schedule_id"] for s in sent] == ["s1", "s2"]
        assert sent[0].to_json()["options"]["enabled"] is False
        assert sent[1].to_json()["options"]["enabled"] is True

    async def _turn_off(self, entry):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        switch = PrimeScheduleSwitch(entry, "c1", "s1", "Weekdays")
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_off()


class TestAFailedReadIsNotAnEmptyOne:
    """Found in the a18 bug hunt.

    `async_read_schedule_containers` returned [] both when the cloud call
    failed and when it succeeded with nothing to report. Two very
    different situations, one value -- the recurring shape of every bug
    this feature has had.

    The consequences were real:
      - a schedule deleted in the app left its switch showing "on"
        forever, available and doing nothing when pressed
      - a write could have been built on a list that was never read
    """

    def _switch(self, entry):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        switch = PrimeScheduleSwitch(entry, "c1", "s1", "Weekdays")
        switch.async_write_ha_state = MagicMock()
        switch._attr_is_on = True
        return switch

    @pytest.mark.asyncio
    async def test_a_failed_read_is_reported_as_failure_not_emptiness(self):
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        assert await async_read_schedule_containers(_entry(raises=True)) is None

    @pytest.mark.asyncio
    async def test_an_empty_account_is_reported_as_empty(self):
        from custom_components.roomba_plus.prime_schedule_switch import (
            async_read_schedule_containers,
        )

        assert await async_read_schedule_containers(_entry([])) == []

    @pytest.mark.asyncio
    async def test_a_deleted_schedule_makes_its_switch_unavailable(self):
        """The read succeeded and this schedule was not in it. Showing
        the last known state would keep offering a control for something
        that no longer exists."""
        switch = self._switch(_entry([("c1", [_schedule_json("other")])]))

        await switch.async_update()

        assert switch._attr_is_on is None
        assert switch.available is False

    @pytest.mark.asyncio
    async def test_a_failed_read_keeps_the_last_known_state(self):
        """Deliberately different: a cloud hiccup must not flicker the
        entity out."""
        switch = self._switch(_entry(raises=True))

        await switch.async_update()

        assert switch._attr_is_on is True
        assert switch.available is True

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_the_read_before_it_failed(self):
        """update_schedules() replaces the whole list, so writing one
        built from a failed read deletes schedules."""
        entry = _entry(raises=True)

        await self._switch(entry).async_turn_off()

        entry.runtime_data.prime_robot.update_schedules.assert_not_awaited()


class TestTheSwitchBelongsToTheRobotDevice:
    """Found in the a18 bug hunt: PrimeScheduleSwitch was the only Prime
    entity in the integration that did not inherit IRobotEntity, so it
    had no device_info. The switches would have shown up outside the
    robot's device page, and with has_entity_name and no device to
    prefix them, two robots' schedules would have been
    indistinguishable.

    Nothing surfaced it because no instance was ever created.
    """

    def _switch(self):
        from tests import prime_fixtures

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        entry = prime_fixtures.cloud_only_config_entry()
        return PrimeScheduleSwitch(entry, "HS-1", "S-1", "Weekdays")

    def test_it_carries_device_info(self):
        assert self._switch().device_info is not None

    def test_it_uses_the_same_device_as_every_other_prime_entity(self):
        """Identifiers, specifically -- a different one would put these
        switches on a device of their own."""
        from custom_components.roomba_plus.const import DOMAIN

        switch = self._switch()

        assert switch.device_info["identifiers"] == {
            (DOMAIN, f"roomba_plus_{switch._blid}")
        }


class TestConcurrentTogglesDoNotOverwriteEachOther:
    """Found in the a18 bug hunt.

    Toggling is a read-modify-write of the WHOLE container --
    update_schedules() replaces the list, so there is no way to send one
    schedule alone. Two switches in the same container toggled at once
    both read the same state and both wrote it back carrying only their
    own change, so the second write silently reverted the first while
    Home Assistant showed both as changed.

    Not an exotic interleaving: `switch.turn_off` against a group, or an
    automation disabling several schedules, does exactly this.
    """

    def _setup(self):
        from unittest.mock import AsyncMock

        from roombapy_prime.models.schedules_dnd import SchedulesResponse

        state = {"s1": True, "s2": True}
        writes: list[dict] = []

        async def slow_read(_household_id):
            import asyncio

            await asyncio.sleep(0.01)  # cloud latency is what opens the window
            return SchedulesResponse.from_json({"household_schedules": [{
                "household_schedule_id": "c1",
                "schedules": [
                    {"schedule_id": key, "options": {"enabled": value, "name": key}}
                    for key, value in state.items()
                ],
            }]})

        async def record(_household_id, _container_id, schedules):
            for schedule in schedules:
                state[schedule.schedule_id] = schedule.options.enabled
            writes.append(dict(state))

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.runtime_data.blid = "BLID"
        entry.runtime_data.prime_household_id = "HH"
        entry.runtime_data.prime_robot.get_schedules = AsyncMock(side_effect=slow_read)
        entry.runtime_data.prime_robot.update_schedules = AsyncMock(side_effect=record)
        return entry, state, writes

    def _switch(self, entry, schedule_id):
        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        switch = PrimeScheduleSwitch(entry, "c1", schedule_id, schedule_id)
        switch.async_write_ha_state = MagicMock()
        return switch

    @pytest.mark.asyncio
    async def test_both_changes_survive(self):
        import asyncio

        entry, state, writes = self._setup()

        await asyncio.gather(
            self._switch(entry, "s1").async_turn_off(),
            self._switch(entry, "s2").async_turn_off(),
        )

        assert state == {"s1": False, "s2": False}
        # The second write must have been built on the first one's result,
        # not on the state both read at the start.
        assert writes[-1] == {"s1": False, "s2": False}


class TestTheEntityListFollowsTheScheduleList:
    """@DaRealGuGu's four-row test matrix, a19.

    a19 added a coordinator so a schedule toggled in the iRobot app
    reaches Home Assistant without a reload. It refreshes the STATE of
    switches that already exist -- and entities were still built exactly
    once, at setup. So:

        create a schedule in the app  -> no switch until a reload
        delete a schedule in the app -> switch goes unavailable, then
                                        stays there forever

    His diagnostics proved the read side was never the problem: the file
    he took at the moment HA showed the old value already contained the
    new one. The staleness was entirely in the entity list.
    """

    def _entry_and_add(self, containers):
        from unittest.mock import MagicMock

        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        coordinator = entry.runtime_data.prime_schedule_coordinator
        coordinator.data = containers
        listeners: list = []
        coordinator.async_add_listener = MagicMock(
            side_effect=lambda cb: listeners.append(cb) or (lambda: None)
        )
        created: list = []
        return entry, coordinator, listeners, created

    async def _setup(self, entry, created):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus import switch as switch_module

        await switch_module.async_setup_entry(MagicMock(), entry, created.extend)

    @staticmethod
    def _schedules(created):
        """The switch platform also builds carpet boost and settings
        switches; only the schedule ones are under test here."""
        return [e for e in created if type(e).__name__ == "PrimeScheduleSwitch"]

    @pytest.mark.asyncio
    async def test_a_schedule_added_in_the_app_gets_a_switch(self):
        entry, coordinator, listeners, created = self._entry_and_add(
            [("c1", [_parsed("s1")])]
        )
        await self._setup(entry, created)
        assert len(self._schedules(created)) == 1

        # The app gains a schedule; the coordinator picks it up on its
        # next cycle and notifies.
        coordinator.data = [("c1", [_parsed("s1"), _parsed("s2")])]
        for callback in listeners:
            callback()

        assert [e._schedule_id for e in self._schedules(created)] == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_an_unchanged_refresh_adds_nothing(self):
        """Matched by schedule_id, never by content. The order of
        start.day is not stable between two reads of the same unchanged
        schedule -- comparing content would add duplicates on every
        refresh."""
        entry, coordinator, listeners, created = self._entry_and_add(
            [("c1", [_parsed("s1", days=[4, 3, 1, 2])])]
        )
        await self._setup(entry, created)

        coordinator.data = [("c1", [_parsed("s1", days=[1, 2, 4, 3])])]
        for callback in listeners:
            callback()

        assert len(self._schedules(created)) == 1

    @pytest.mark.asyncio
    async def test_a_failed_first_refresh_still_wires_the_listener(self):
        """A cloud hiccup at startup must not cost the user their
        switches until the next restart -- the next successful refresh
        has to be able to add them."""
        from unittest.mock import AsyncMock

        entry, coordinator, listeners, created = self._entry_and_add([])
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=RuntimeError("cloud down")
        )
        await self._setup(entry, created)
        assert self._schedules(created) == []

        coordinator.data = [("c1", [_parsed("s1")])]
        for callback in listeners:
            callback()

        assert len(self._schedules(created)) == 1


def _parsed(schedule_id, days=None, enabled=True):
    from roombapy_prime.models.schedules_dnd import HouseholdSchedule

    return HouseholdSchedule.from_json(_schedule_json(
        schedule_id, enabled=enabled,
    ) | {"options": {
        "name": schedule_id, "enabled": enabled, "deleted": False,
        "frequency": "WEEKLY",
        "start": {"day": days or [1], "hour": 9, "min": 0},
    }})


class TestVanishedSchedulesLoseTheirSwitch:
    """a20 claimed this and only shipped half of it.

    Its release notes said a deleted schedule's switch "will disappear".
    Only the additive half had been written: `_sync_entities` compared
    against a set of known ids and added what was missing, and never
    looked the other way. @DaRealGuGu tested it and reported three
    switches still sitting there greyed out well past the refresh
    interval -- which was exactly right.

    The additive half did work: a schedule created in the app got its
    switch on the next cycle. So the feature looked like it worked from
    one direction.
    """

    def _setup(self, containers, ok=True):
        from unittest.mock import MagicMock

        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        coordinator = entry.runtime_data.prime_schedule_coordinator
        coordinator.data = containers
        coordinator.last_update_success = ok
        listeners: list = []
        coordinator.async_add_listener = MagicMock(
            side_effect=lambda cb: listeners.append(cb) or (lambda: None)
        )
        return entry, coordinator, listeners

    async def _run(self, entry, registry):
        """Returns the created list AND a live patch context.

        The entity registry lookup happens when a listener fires, not
        during setup, so the patch has to outlive async_setup_entry --
        an earlier version of this test let it expire and saw no
        removals for that reason rather than a real one.
        """
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus import switch as switch_module

        created: list = []
        patcher = self._patch_registry(registry)
        patcher.start()
        await switch_module.async_setup_entry(MagicMock(), entry, created.extend)
        return created, patcher

    def addfinalizer(self, patcher):
        self._patchers.append(patcher)

    _patchers: list = []

    def teardown_method(self):
        while self._patchers:
            self._patchers.pop().stop()

    def _registry(self, existing=()):
        """A registry holding entries, because that is what the removal
        reads now.

        The first version of this test faked `async_get_entity_id`, which
        matched an implementation that only knew about entities the
        current session had added -- and that was exactly the bug: an
        orphan from a previous run was invisible to it.
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from homeassistant.const import Platform

        registry = MagicMock()
        registry._entries = [
            SimpleNamespace(domain=Platform.SWITCH, unique_id=uid, entity_id=f"switch.{uid}")
            for uid in existing
        ]
        return registry

    @staticmethod
    def _patch_registry(registry):
        from unittest.mock import patch

        from custom_components.roomba_plus import switch as switch_module

        return patch.multiple(
            switch_module.er,
            async_get=lambda _hass: registry,
            async_entries_for_config_entry=lambda _reg, _eid: registry._entries,
        )

    @pytest.mark.asyncio
    async def test_a_schedule_deleted_in_the_app_loses_its_switch(self):
        entry, coordinator, listeners = self._setup(
            [("c1", [_parsed("s1"), _parsed("s2")])]
        )
        blid = entry.runtime_data.blid
        registry = self._registry(
            [f"{blid}_schedule_s1", f"{blid}_schedule_s2"]
        )
        _, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)

        coordinator.data = [("c1", [_parsed("s1")])]
        for callback in listeners:
            callback()

        removed = [call.args[0] for call in registry.async_remove.call_args_list]
        assert len(removed) == 1
        assert "s2" in removed[0]

    @pytest.mark.asyncio
    async def test_an_orphan_from_a_previous_run_is_removed(self):
        """THE BUG a21 SHIPPED. Removal compared against a set of ids
        this session had added, so after a restart the difference was
        empty by construction and an entity whose schedule vanished while
        Home Assistant was down could never be reached.

        @DaRealGuGu had four of them still sitting there on a21, with
        release notes saying they would go.
        """
        entry, _coordinator, _listeners = self._setup([("c1", [_parsed("s1")])])
        blid = entry.runtime_data.blid
        registry = self._registry(
            [f"{blid}_schedule_s1", f"{blid}_schedule_GONE"]
        )

        _, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)

        removed = [call.args[0] for call in registry.async_remove.call_args_list]
        assert removed == [f"switch.{blid}_schedule_GONE"]

    @pytest.mark.asyncio
    async def test_other_switches_are_left_alone(self):
        """The registry holds every switch this entry owns. Only the ones
        carrying this robot's schedule prefix are ours to remove."""
        entry, _coordinator, _listeners = self._setup([("c1", [_parsed("s1")])])
        blid = entry.runtime_data.blid
        registry = self._registry([
            f"{blid}_schedule_s1",
            f"{blid}_child_lock",
            "OTHERBLID_schedule_s9",
        ])

        _, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)

        registry.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_nothing_is_removed_after_a_failed_refresh(self):
        """The worst thing this file could do. A failed refresh leaves
        the coordinator's data at its last good value, so in practice
        nothing would vanish -- but tying deletion to a flag that says
        "this data is current" is the difference between a rule and a
        coincidence."""
        entry, coordinator, listeners = self._setup(
            [("c1", [_parsed("s1"), _parsed("s2")])]
        )
        registry = self._registry()
        _, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)

        coordinator.data = []
        coordinator.last_update_success = False
        for callback in listeners:
            callback()

        registry.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unchanged_refresh_removes_nothing(self):
        """The day order is not stable between reads, so a content
        comparison would churn the entity list on every cycle."""
        entry, coordinator, listeners = self._setup(
            [("c1", [_parsed("s1", days=[4, 3, 1, 2])])]
        )
        registry = self._registry()
        created, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)
        before = len(created)

        coordinator.data = [("c1", [_parsed("s1", days=[1, 2, 4, 3])])]
        for callback in listeners:
            callback()

        registry.async_remove.assert_not_called()
        assert len(created) == before

    @pytest.mark.asyncio
    async def test_a_schedule_can_come_back(self):
        """Removed and then re-added must produce a switch again --
        otherwise the id set would remember a schedule the user
        recreated and silently skip it.

        The registry entry stays in place while the schedule vanishes:
        that is the real sequence. Removing it from the fake first would
        skip the very code path that prunes the id set."""
        entry, coordinator, listeners = self._setup([("c1", [_parsed("s1")])])
        registry = self._registry([f"{entry.runtime_data.blid}_schedule_s1"])
        created, patcher = await self._run(entry, registry)
        self.addfinalizer(patcher)

        coordinator.data = []
        for callback in listeners:
            callback()
        coordinator.data = [("c1", [_parsed("s1")])]
        for callback in listeners:
            callback()

        schedules = [e for e in created if type(e).__name__ == "PrimeScheduleSwitch"]
        assert len(schedules) == 2


class TestSwitchLabelsTellSchedulesApart:
    """@chairstacker's a20 screenshot: six switches, all reading
    "Schedule - Regular Schedule".

    The `name` field cannot distinguish them, and APK analysis
    established why: it is a hard-coded default with no user relation.
    The app shows no name anywhere and offers no field to set one. Nine
    schedules across two accounts all read "Regular Schedule"; the only
    different value came from our own CLI call.

    So the label is built the way the app's own list is -- from the
    rooms a schedule cleans -- plus the start time.
    """

    _NAMES = {"13": "Kitchen", "10": "Bathroom", "12": "Hallway",
              "11": "Living room", "14": "Study"}

    def _label(self, regions=(), room_names=None, **kwargs):
        """Built THROUGH the parser, not by hand.

        An earlier version constructed ScheduleOptions directly with the
        wire shape `{"command": {"regions": [...]}}`. ScheduleOptions
        .from_json() unwraps that, so a parsed schedule holds the inner
        dict -- and the code under test only understood the wrapper. The
        hand-built fixture agreed with the bug and every test passed
        while no real schedule resolved a single room name.

        Exactly what tests/prime_fixtures.py exists to prevent, made
        inside a test instead of in the code.
        """
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus.prime_schedule_switch import (
            _schedule_label,
        )

        options: dict = {"name": kwargs.get("name")}
        start = kwargs.get("start")
        if start is not None:
            options["start"] = start
        if regions:
            options["commands"] = [{"command": {"regions": [
                {"region_id": r, "type": "rid"} for r in regions
            ]}}]
        if kwargs.get("commands") is not None:
            options["commands"] = kwargs["commands"]
        parsed = HouseholdSchedule.from_json({"schedule_id": "x", "options": options})
        return _schedule_label(
            parsed.options, self._NAMES if room_names is None else room_names
        )

    def test_the_rooms_are_the_label(self):
        assert self._label(
            regions=["13", "10"], name="Regular Schedule",
            start={"day": [1], "hour": 9, "min": 0},
        ) == "Kitchen, Bathroom 09:00"

    def test_two_schedules_covering_different_rooms_read_differently(self):
        """The whole point. Under the old label both of these were
        "Regular Schedule 09:00"."""
        first = self._label(regions=["13", "10"],
                            start={"day": [1], "hour": 9, "min": 0})
        second = self._label(regions=["12", "11"],
                             start={"day": [5], "hour": 9, "min": 0})

        assert first != second

    def test_a_long_room_list_is_cut_short(self):
        """Seven rooms would produce a label nobody can scan in a
        list."""
        assert self._label(
            regions=["13", "10", "12", "11", "14"],
            start={"day": [1], "hour": 15, "min": 45},
        ) == "Kitchen, Bathroom +3 15:45"

    def test_an_unresolvable_region_falls_back_to_the_time(self):
        """A bare "Zone 99" looks like information and is not. The time
        separates schedules better than a region number does."""
        assert self._label(
            regions=["99"], start={"day": [1], "hour": 6, "min": 5}
        ) == "06:05"

    def test_no_rooms_at_all_falls_back_to_the_time(self):
        assert self._label(
            name="Regular Schedule", start={"day": [1], "hour": 7, "min": 30}
        ) == "07:30"

    def test_no_start_time_falls_back_to_the_name(self):
        assert self._label(name="Regular Schedule") == "Regular Schedule"

    def test_a_malformed_command_block_does_not_raise(self):
        """These are raw dicts off the wire, read inside a coordinator
        listener -- an exception here would take the whole switch
        platform down."""
        for commands in ([{"command": "not-a-dict"}],
                         [{"command": {"regions": ["x", 3]}}], [None]):
            assert self._label(
                commands=commands, start={"day": [1], "hour": 9, "min": 0}
            ) == "09:00"

    def test_the_object_id_still_comes_from_the_schedule_id(self):
        """Renaming a routine in the app must not rename the entity out
        from under an automation -- so the label may change freely while
        the slug does not."""
        from tests import prime_fixtures

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        entry = prime_fixtures.cloud_only_config_entry()
        switch = PrimeScheduleSwitch(entry, "c1", "S-1", "Regular Schedule 15:45")

        assert switch.suggested_object_id == "schedule_S-1"


class TestWeekdaysInTheLabel:
    """Rooms plus time was not enough, and a real account proved it.

    Two of @DaRealGuGu's three schedules clean the SAME four rooms at
    the SAME time and differ only in which days they run -- so the
    room-based label produced two identical entries, reintroducing the
    problem it was built to solve.
    """

    _ROOMS = {"13": "Kitchen", "10": "Bathroom", "12": "Hallway",
              "11": "Living room"}
    _DAYS = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
             5: "Fri", 6: "Sat"}

    _DEFAULT = object()

    def _label(self, days, hour, minute, regions, weekday_names=_DEFAULT):
        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus.prime_schedule_switch import (
            _schedule_label,
        )

        parsed = HouseholdSchedule.from_json({"schedule_id": "x", "options": {
            "name": "Regular Schedule",
            "start": {"day": days, "hour": hour, "min": minute},
            "commands": [{"command": {"regions": [
                {"region_id": r, "type": "rid"} for r in regions
            ]}}],
        }})
        # A sentinel rather than None: None is itself one of the broken
        # inputs under test, and defaulting on it made that case quietly
        # exercise the real table instead.
        return _schedule_label(
            parsed.options, self._ROOMS,
            self._DAYS if weekday_names is self._DEFAULT else weekday_names,
        )

    def test_his_two_colliding_schedules_now_differ(self):
        friday = self._label([5], 9, 0, ["13", "10", "12", "11"])
        weekdays = self._label([3, 1, 2, 4], 9, 0, ["13", "10", "12", "11"])

        assert friday != weekdays
        assert friday == "Kitchen, Bathroom +2 Fri 09:00"
        assert weekdays == "Kitchen, Bathroom +2 Mon-Thu 09:00"

    def test_consecutive_days_collapse_into_a_range(self):
        """Sorted first: the day order is not stable between reads --
        the same untouched schedule came back as [4,3,1,2], then
        [1,2,4,3], then [3,1,2,4]."""
        assert self._label([4, 3, 1, 2], 9, 0, ["13"]) == "Kitchen Mon-Thu 09:00"

    def test_non_consecutive_days_are_listed(self):
        assert self._label([1, 3, 5], 9, 0, ["13"]) == "Kitchen Mon, Wed, Fri 09:00"

    def test_two_days_are_listed_rather_than_ranged(self):
        """"Mon-Tue" is longer than "Mon, Tue" and reads as a span the
        user did not set."""
        assert self._label([1, 2], 9, 0, ["13"]) == "Kitchen Mon, Tue 09:00"

    def test_missing_translations_degrade_to_rooms_and_time(self):
        """A failed translation lookup must cost the days, not the
        entity."""
        assert self._label([5], 9, 0, ["13"], weekday_names={}) == "Kitchen 09:00"

    def test_a_lookup_that_is_not_a_mapping_does_not_raise(self):
        """This runs inside a coordinator listener -- raising here takes
        the whole switch platform down."""
        for broken in (None, "nonsense", {1: 7}, {1: ""}):
            assert self._label([1], 9, 0, ["13"], weekday_names=broken) \
                == "Kitchen 09:00"


class TestTheLabelFollowsTheSchedule:
    """The label was computed once, in __init__, and never again.

    Invisible while the only way to change a schedule was the iRobot
    app, which offers no rename field. It surfaced the moment someone
    built a service that CAN rename one (@utkjmitch, #49): the data
    updated and the cosmetics lagged.
    """

    _ROOMS = {"13": "Kitchen", "10": "Bathroom"}
    _DAYS = {1: "Mon", 2: "Tue", 5: "Fri"}

    def _switch_and_apply(self, first, second):
        from unittest.mock import MagicMock

        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        def parsed(options):
            return HouseholdSchedule.from_json({"schedule_id": "S1", "options": options})

        entry = MagicMock()
        entry.runtime_data.blid = "B"
        coordinator = entry.runtime_data.prime_schedule_coordinator
        coordinator.room_names = self._ROOMS
        coordinator.weekday_names = self._DAYS

        switch = PrimeScheduleSwitch(entry, "c1", "S1", "initial")
        switch._apply([("c1", [parsed(first)])])
        before = switch._attr_translation_placeholders["schedule"]
        switch._apply([("c1", [parsed(second)])])
        return before, switch._attr_translation_placeholders["schedule"]

    def test_a_retimed_schedule_gets_a_new_label(self):
        before, after = self._switch_and_apply(
            {"enabled": True, "start": {"day": [5], "hour": 9, "min": 0}},
            {"enabled": True, "start": {"day": [5], "hour": 15, "min": 30}},
        )

        assert before == "Fri 09:00"
        assert after == "Fri 15:30"

    def test_a_renamed_schedule_gets_a_new_label(self):
        _before, after = self._switch_and_apply(
            {"enabled": True, "name": "old", "start": {"day": [1], "hour": 6, "min": 0}},
            {"enabled": True, "name": "new", "start": {"day": [1], "hour": 6, "min": 0}},
        )

        assert after == "Mon 06:00"

    def test_changed_rooms_change_the_label(self):
        rooms = [{"region_id": "13", "type": "rid"}]
        both = [*rooms, {"region_id": "10", "type": "rid"}]
        before, after = self._switch_and_apply(
            {"enabled": True, "start": {"day": [1], "hour": 9, "min": 0},
             "commands": [{"command": {"regions": rooms}}]},
            {"enabled": True, "start": {"day": [1], "hour": 9, "min": 0},
             "commands": [{"command": {"regions": both}}]},
        )

        assert before == "Kitchen Mon 09:00"
        assert after == "Kitchen, Bathroom Mon 09:00"

    def test_the_unique_id_never_moves(self):
        """A label that follows the schedule must not drag the entity id
        with it -- automations point at the entity."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_schedule_switch import (
            PrimeScheduleSwitch,
        )

        entry = MagicMock()
        entry.runtime_data.blid = "B"
        switch = PrimeScheduleSwitch(entry, "c1", "S1", "whatever")
        before = switch.unique_id

        switch._refresh_label(type("O", (), {"name": "x", "start": None, "commands": []})())

        assert switch.unique_id == before
        assert switch.suggested_object_id == "schedule_S1"


class TestQuietHoursAreNotCleaningSchedules:
    """They arrive in the same list, and we rendered all of it.

    @DaRealGuGu set Do Not Disturb in the OLD iRobot app and two
    switches appeared in Home Assistant for his PRIME robot -- matching
    the quiet-hours times, shown nowhere in the Roomba app. Deleting the
    quiet hours made them go away again.

    A switch for one is worse than a missing switch: toggling writes the
    whole container back, so a user "turning off a schedule" would have
    been rewriting their quiet hours from whatever we managed to parse.
    """

    def _switches(self, options):
        from unittest.mock import MagicMock

        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus.prime_schedule_switch import (
            build_prime_schedule_switches,
        )

        entry = MagicMock()
        entry.runtime_data.blid = "BLID"
        parsed = HouseholdSchedule.from_json({"schedule_id": "S1", "options": options})
        return build_prime_schedule_switches(entry, [("C1", [parsed])])

    _CLEAN = {
        "enabled": True, "frequency": "WEEKLY",
        "start": {"day": [1], "hour": 9, "min": 0},
        "commands": [{"command": {"command": "start"}}],
    }

    def test_a_cleaning_schedule_still_gets_a_switch(self):
        assert len(self._switches(self._CLEAN)) == 1

    def test_an_interval_does_not(self):
        """Quiet hours are an interval and carry both ends; a cleaning
        schedule only says when to start."""
        quiet = {
            "enabled": True, "frequency": "WEEKLY",
            "start": {"day": [1, 2, 3, 4, 5], "hour": 6, "min": 15},
            "end": {"day": [1, 2, 3, 4, 5], "hour": 9, "min": 55},
        }

        assert self._switches(quiet) == []

    def test_end_commands_alone_are_enough_to_exclude_it(self):
        entry = {**self._CLEAN, "end_commands": [{"command": {"command": "stop"}}]}

        assert self._switches(entry) == []

    def test_the_discriminator_is_the_shape_not_a_label(self):
        """The app's own HouseholdScheduleType enum exists but lives in
        native code and has never been read. If a cleaning schedule with
        an end time ever turns up, this is the line that will be wrong --
        pinned here so that shows as a failure rather than as a missing
        switch somebody has to notice.
        """
        import inspect

        from custom_components.roomba_plus import prime_schedule_switch

        source = inspect.getsource(prime_schedule_switch.build_prime_schedule_switches)
        assert "options.end is not None or options.end_commands" in source
