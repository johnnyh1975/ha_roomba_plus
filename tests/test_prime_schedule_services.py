"""The three schedule write services (#49, @utkjmitch).

These write real schedules to real robots, and the read-modify-write
trap has caught this project twice: `update_schedules()` replaces the
whole container, so a list built from a stale or partial read deletes
schedules rather than changing one.

The services shipped with no tests of their own -- the only coverage was
a line asserting the names were registered. What follows is aimed at the
places where being wrong is expensive: the lock discipline, the
sole-occupant decision, and never writing from a read that failed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _options(schedule_id="S1", *, name="Regular Schedule", enabled=True,
             regions=("11", "13")):
    from roombapy_prime.models.schedules_dnd import HouseholdSchedule

    return HouseholdSchedule.from_json({
        "schedule_id": schedule_id,
        "options": {
            "name": name, "enabled": enabled, "frequency": "WEEKLY",
            "robot_id": "BLID",
            "start": {"day": [1], "hour": 9, "min": 0},
            "commands": [{"command": {
                "command": "start", "robot_id": "BLID", "ordered": 1,
                "select_all": False, "params": {"routine_modified": True},
                "regions": [
                    {"type": "rid", "region_id": r, "params": {
                        "operatingMode": 2, "suctionLevel": 3,
                        "twoPass": False, "padWetness": {"padPlate": 1},
                    }}
                    for r in regions
                ],
            }}],
        },
    })


class _Harness:
    """Wires a config entry, an entity registry entry and a robot."""

    def __init__(self, containers, *, read_fails=False):
        self.robot = AsyncMock()
        self.containers = containers
        self.read_fails = read_fails
        self.reads = 0

        data = SimpleNamespace(
            blid="BLID", prime_robot=self.robot,
            prime_household_id="HH", prime_schedule_coordinator=MagicMock(),
        )
        data.prime_schedule_coordinator.async_request_refresh = AsyncMock()
        self.entry = MagicMock()
        self.entry.entry_id = "E1"
        self.entry.runtime_data = data

    async def _read(self, _entry):
        self.reads += 1
        return None if self.read_fails else self.containers

    def patches(self, module, schedule_id="S1"):
        registry_entry = SimpleNamespace(
            entity_id="switch.x", unique_id=f"BLID_schedule_{schedule_id}"
        )
        return (
            patch.object(module, "_prime_entry_for",
                         return_value=(self.entry, registry_entry)),
            patch.object(module, "async_read_schedule_containers",
                         side_effect=self._read),
        )


async def _call(handler, harness, module, data, schedule_id="S1"):
    a, b = harness.patches(module, schedule_id)
    with a, b:
        return await handler(MagicMock(), SimpleNamespace(data=data))


class TestDeleteChoosesTheRightPath:
    """A container holding one schedule is deleted outright; a shared one
    is rewritten without it. Getting that backwards deletes somebody
    else's schedule.
    """

    @pytest.mark.asyncio
    async def test_a_sole_occupant_deletes_the_container(self):
        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([("C1", [_options("S1")])])

        await _call(m._async_delete, h, m, {"entity_id": "switch.x"})

        h.robot.delete_schedule.assert_awaited_once_with("HH", "C1")
        h.robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_shared_container_is_rewritten_without_it(self):
        """Never seen in the field -- every container observed holds one
        schedule. Built so the first multi-schedule container is not also
        the first bug."""
        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([("C1", [_options("S1"), _options("S2")])])

        await _call(m._async_delete, h, m, {"entity_id": "switch.x"})

        h.robot.delete_schedule.assert_not_awaited()
        _hh, _cid, remaining = h.robot.update_schedules.await_args.args
        assert [s.schedule_id for s in remaining] == ["S2"]

    @pytest.mark.asyncio
    async def test_the_decision_is_made_from_a_read_inside_the_lock(self):
        """The pre-lock read is not the one that decides. A schedule
        added between the two reads would otherwise be deleted along
        with the requested one, because the whole container goes when it
        looks like a single occupant.

        Two reads is the assertion: one to find the container, one under
        the lock to decide."""
        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([("C1", [_options("S1")])])

        await _call(m._async_delete, h, m, {"entity_id": "switch.x"})

        assert h.reads == 2

    @pytest.mark.asyncio
    async def test_a_schedule_that_vanished_is_not_written_around(self):
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([("C1", [_options("OTHER")])])

        with pytest.raises(ServiceValidationError):
            await _call(m._async_delete, h, m, {"entity_id": "switch.x"})
        h.robot.delete_schedule.assert_not_awaited()


class TestNothingIsWrittenFromAFailedRead:
    """`update_schedules()` replaces the whole container. A list built
    from a read that failed is not a smaller list -- it is a deletion.
    """

    @pytest.mark.asyncio
    async def test_delete_refuses(self):
        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([], read_fails=True)

        with pytest.raises(Exception):  # noqa: B017, PT011
            await _call(m._async_delete, h, m, {"entity_id": "switch.x"})
        h.robot.delete_schedule.assert_not_awaited()
        h.robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_refuses(self):
        from custom_components.roomba_plus import prime_schedule_services as m

        h = _Harness([], read_fails=True)

        with pytest.raises(Exception):  # noqa: B017, PT011
            await _call(m._async_update, h, m,
                        {"entity_id": "switch.x", "enabled": False})
        h.robot.update_schedules.assert_not_awaited()


class TestTheEntityMustBeAScheduleSwitch:
    """The schedule id comes out of the switch's unique_id. Pointed at a
    vacuum or a sensor, the service has to say so rather than build a
    request around a meaningless id."""

    def test_a_foreign_unique_id_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus.prime_schedule_services import (
            _schedule_id_from,
        )

        entry = SimpleNamespace(entity_id="switch.other", unique_id="BLID_child_lock")

        with pytest.raises(ServiceValidationError):
            _schedule_id_from(entry, "BLID")

    def test_a_schedule_switch_yields_its_id(self):
        from custom_components.roomba_plus.prime_schedule_services import (
            _schedule_id_from,
        )

        entry = SimpleNamespace(entity_id="switch.s", unique_id="BLID_schedule_hh_x_s")

        assert _schedule_id_from(entry, "BLID") == "hh_x_s"

    def test_another_robots_switch_is_refused(self):
        """The prefix carries the blid, so a second robot in the same
        entry cannot have its schedules edited through the first."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus.prime_schedule_services import (
            _schedule_id_from,
        )

        entry = SimpleNamespace(entity_id="switch.s", unique_id="OTHER_schedule_x")

        with pytest.raises(ServiceValidationError):
            _schedule_id_from(entry, "BLID")


class TestRegionsAreReadThroughEitherShape:
    """`ScheduleOptions.from_json` unwraps the command envelope, so a
    parsed schedule holds the inner dict while the wire carries the
    wrapper. Reading only one of them is a mistake this project has
    already made once, in the switch labels."""

    def _regions(self, command):
        from custom_components.roomba_plus.prime_schedule_services import _regions_of

        return _regions_of(command)

    def test_the_wire_shape(self):
        wrapped = {"command": {"regions": [{"region_id": "11"}]}}
        assert self._regions(wrapped) == [{"region_id": "11"}]

    def test_the_parsed_shape(self):
        assert self._regions({"regions": [{"region_id": "13"}]}) == [
            {"region_id": "13"}
        ]

    def test_nonsense_yields_nothing_rather_than_raising(self):
        for value in (None, "x", 7, {}, {"command": "x"}, {"regions": "x"}):
            assert self._regions(value) == []


class TestFourSilentSignatureBugs:
    """Helpers below build a real update call, so these test what the
    code DOES rather than how it is spelled."""

    def _run_update(self, room_ids=None, lock=None):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from roombapy_prime.models.schedules_dnd import HouseholdSchedule

        from custom_components.roomba_plus import prime_schedule_services as m

        parsed = HouseholdSchedule.from_json({
            "schedule_id": "S1",
            "options": {
                "name": "x", "enabled": True, "frequency": "WEEKLY",
                "start": {"day": [1], "hour": 9, "min": 0},
                "commands": [{"command": {"command": "start", "regions": [
                    {"type": "rid", "region_id": "11", "params": {}}]}}],
            },
        })
        entry = MagicMock()
        entry.entry_id = "E1"
        robot = AsyncMock()
        entry.runtime_data = SimpleNamespace(
            prime_robot=robot, prime_household_id="HH",
            prime_schedule_coordinator=MagicMock(),
        )
        entry.runtime_data.prime_schedule_coordinator.async_request_refresh = (
            AsyncMock()
        )
        patches = [
            patch.object(m, "async_read_schedule_containers",
                         AsyncMock(return_value=[("C1", [parsed])])),
        ]
        if lock is not None:
            patches.append(patch.object(m, "_container_lock", lock))
        for p_ in patches:
            p_.start()
        try:
            asyncio.run(m.async_update_schedule_from_calendar(
                MagicMock(), entry, "S1",
                name="x", weekday=1, hour=10, minute=0,
                frequency="WEEKLY", room_ids=room_ids or [], note="",
            ))
        finally:
            for p_ in patches:
                p_.stop()
        return robot

    """All four found by @utkjmitch from overnight debug logs, and all
    four the same species: a call signature or an attribute changed in
    one layer and not the other, invisible because the failure output
    matches the boring case.

    No history. No save button. No progress. Each read as "nothing to
    report" and each failed on EVERY call, not occasionally.
    """

    def test_the_history_sync_passes_the_blid(self):
        """`get_mission_history()` requires it, and the call never
        supplied one -- so the wrapper reported "imported 0 missions",
        which reads exactly like a robot with no history.

        The comment beside this call already documents the previous life
        of the same bug: a required `days` argument that made every sync
        fail silently since the feature shipped. Fixing that one moved
        the TypeError up a line rather than ending it."""
        import inspect

        from custom_components.roomba_plus import prime_mission_sync

        # BEHAVIOUR, NOT SPELLING. An earlier version of this asserted
        # that the source contained `get_mission_history(robot.blid)`,
        # which breaks on reformatting and proves nothing about what the
        # call does. Calling it and looking at the argument does.
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus import prime_mission_sync

        robot = AsyncMock()
        robot.blid = "BLID"
        robot.get_mission_history.return_value = []

        asyncio.run(prime_mission_sync._async_sync_locked(
            MagicMock(), robot, MagicMock()
        ))

        robot.get_mission_history.assert_awaited_once_with("BLID")

    def test_the_status_coordinator_owns_the_trail_id(self):
        """`_note_phase_for_timer` lives on PrimeStatusCoordinator and
        touches `self._trail_mission_id`, which was only ever initialised
        in PrimeCoordinator -- a different class it does not inherit
        from.

        So the whole phase-update block raised on its first line, hourly,
        on every install: the trail clearing, the observed dock position
        and the mission timer's on_phase_run all never ran."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator,
        )

        coordinator = object.__new__(PrimeStatusCoordinator)
        PrimeStatusCoordinator.__init__(
            coordinator, MagicMock(), MagicMock(), "BLID", MagicMock()
        )

        assert coordinator._trail_mission_id is None

    def test_the_calendar_update_locks_a_container(self):
        """`_container_lock()` takes an entry AND a container id. The
        calendar glue passed only the entry, so every edit raised
        TypeError -- the handler existed, was reachable once events
        carried a uid, and could not complete."""
        import inspect

        from custom_components.roomba_plus import prime_schedule_services

        # The lock's ARGUMENTS, observed. Asserting the source text
        # would pass on a call that never runs.
        seen = {}

        def _lock(config_entry, container_id):
            import contextlib

            seen["container"] = container_id
            return contextlib.nullcontext()

        self._run_update(lock=_lock)

        assert seen["container"] == "C1"

    def test_an_edit_without_rooms_does_not_ask_to_resolve_none(self):
        """The key's presence is what triggers the room rewrite, so
        setting it to None asked the resolver to iterate nothing. The
        create path already only set it when non-empty."""
        import inspect

        from custom_components.roomba_plus import prime_schedule_services

        # Observed: an edit with no rooms must not ask the resolver to
        # iterate None. Checking the source for `if room_ids:` would
        # pass on a rewrite that reintroduced the bug differently.
        from unittest.mock import patch

        from custom_components.roomba_plus import prime_schedule_services

        with patch.object(
            prime_schedule_services, "_resolve_rooms"
        ) as resolve:
            self._run_update(room_ids=[])

        resolve.assert_not_called()


class TestAnEditKeepsTheWholeSeriesDays:
    """Home Assistant hands us one occurrence. @DaRealGuGu edited the
    Monday event of a Mon/Tue/Wed schedule and it became **Monday-only**
    — the series collapsed to whichever day he had clicked.

    Same principle as the frequency fix one layer up: an edit that says
    nothing about recurrence must not change it, **and the day list IS
    recurrence.**
    """

    def _days(self, existing, weekday=0, explicit=None):
        from custom_components.roomba_plus.prime_schedule_services import (
            _days_for_update,
        )

        return _days_for_update(existing, weekday, explicit)

    def test_the_series_keeps_all_its_days(self):
        """His case: Mon/Tue/Wed edited from the Monday occurrence.

        **THE ROBOT COUNTS FROM SUNDAY**, so Mon/Tue/Wed is `[1, 2, 3]`.
        These tests were written with `[0, 1, 2]` and passed, because
        the table under test carried the same off-by-one — which is how
        his schedule came back as Tue/Wed/Thu with every test green."""
        assert self._days([1, 2, 3], weekday=1) == ["mon", "tue", "wed"]

    def test_editing_a_wednesday_does_not_move_the_series(self):
        assert self._days([1, 2, 3], weekday=3) == ["mon", "tue", "wed"]

    def test_an_explicit_rule_wins(self):
        """A user who actually changed the recurrence gets what they
        asked for."""
        assert self._days([1, 2, 3], weekday=1, explicit=[5, 6]) == ["fri", "sat"]

    def test_a_schedule_with_no_days_falls_back_to_the_occurrence(self):
        """What a brand-new entry looks like."""
        assert self._days(None, weekday=4) == ["thu"]
        assert self._days([], weekday=4) == ["thu"]

    def test_an_unusable_day_does_not_lose_the_edit(self):
        """Losing one day of a series is recoverable; losing the edit is
        not."""
        assert self._days([1, 99, 3], weekday=1) == ["mon", "wed"]
        assert self._days([99], weekday=6) == ["sat"]


class TestOneWeekdayNumberingOnly:
    """This module had **two** weekday tables disagreeing by one.
    `_WEEKDAYS` counted from Sunday, as the robot does; the write table
    counted from Monday.

    @DaRealGuGu's Mon/Tue/Wed schedule came back as Tue/Wed/Thu after an
    edit — every day shifted by exactly one, confirmed in the iRobot app
    and in his diagnostics (`days: [2, 3, 4]`).

    **The second table was only reached once schedule editing existed**,
    which is why it survived until an edit was possible. And the tests
    written for that path carried the same assumption, so they agreed
    with it.
    """

    def test_the_two_tables_are_one(self):
        """Derived rather than written twice, so they cannot drift
        apart again."""
        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAYS,
            _WEEKDAY_TO_WIRE,
        )

        assert _WEEKDAY_TO_WIRE == {v: k for k, v in _WEEKDAYS.items()}

    def test_sunday_is_zero(self):
        """The robot's numbering, not Python's."""
        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAY_TO_WIRE,
        )

        assert _WEEKDAY_TO_WIRE[0] == "sun"
        assert _WEEKDAY_TO_WIRE[1] == "mon"

    def test_his_series_round_trips(self):
        """Mon/Tue/Wed in, Mon/Tue/Wed out."""
        from custom_components.roomba_plus.prime_schedule_services import (
            _WEEKDAYS,
            _days_for_update,
        )

        stored = [_WEEKDAYS[d] for d in ("mon", "tue", "wed")]

        assert _days_for_update(stored, _WEEKDAYS["mon"], None) == [
            "mon", "tue", "wed"
        ]

    def test_the_calendar_already_converted_correctly(self):
        """`(weekday() + 1) % 7` turns Python's Monday-zero into the
        robot's Sunday-zero. The calendar was right all along; only the
        write table disagreed with it."""
        import inspect

        from custom_components.roomba_plus import calendar as cal

        assert "(local.weekday() + 1) % 7" in inspect.getsource(cal)
