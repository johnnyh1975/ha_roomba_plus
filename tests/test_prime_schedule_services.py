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
from homeassistant.exceptions import ServiceValidationError
from custom_components.roomba_plus import prime_schedule_services as pss
import datetime as dt
import copy
import json
import pathlib


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


class TestASingleDayScheduleMovesWithTheEdit:
    """@chairstacker (#71, twice): he still could not change a weekday
    in a40.

    The preservation rule is right for a Mon/Wed/Fri series — editing
    one occurrence must not drop the others. But Home Assistant sends
    no BYDAY when you simply move an entry to another day, so the
    stored days won every time and the move was discarded.

    With exactly one stored day there is no series to protect.
    """

    @staticmethod
    def _days(existing, weekday, explicit=None):
        from custom_components.roomba_plus.prime_schedule_services import (
            _days_for_update,
        )

        return _days_for_update(existing, weekday, explicit)

    def test_one_stored_day_follows_the_edit(self):
        #  takes a Sunday-based wire index, matching
        # what the calendar passes after its own conversion.
        # Stored Monday (1), edited to Wednesday (3).
        assert self._days([1], 3) == ["wed"]

    def test_a_series_is_still_preserved(self):
        """Mon/Wed/Fri stays Mon/Wed/Fri when one occurrence moves."""
        assert set(self._days([1, 3, 5], 3)) == {"mon", "wed", "fri"}

    def test_an_explicit_recurrence_still_wins(self):
        assert self._days([1, 3, 5], 3, explicit=[0]) == ["sun"]

    def test_no_stored_days_falls_back_to_the_edit(self):
        assert self._days([], 3) == ["wed"]


class TestReshapedOptions:
    """`_reshaped_options` lays the requested changes over a template.

    It serves both create (template = an existing schedule) and update
    (template = the schedule being changed), so every field is optional
    and absence must mean "leave alone" rather than "clear". A branch
    that treats a missing key as an empty value silently wipes a setting
    the user never mentioned.
    """

    def _template(self, **kw):
        from roombapy_prime.models.schedules_dnd import (
            ScheduleFrequency,
            ScheduleOptions,
            ScheduleTime,
        )

        basis = {
            "name": "Morning",
            "enabled": True,
            "frequency": ScheduleFrequency.WEEKLY,
            # `day` is a LIST of weekday numbers, not one day.
            "start": ScheduleTime(day=[1], hour=9, min=30),
        }
        basis.update(kw)
        return ScheduleOptions(**basis)

    def _reshape(self, call_data, template=None):
        from custom_components.roomba_plus.prime_schedule_services import (
            _reshaped_options,
        )

        return _reshaped_options(
            template or self._template(), call_data, [], MagicMock()
        )

    def test_an_absent_key_leaves_the_field_alone(self):
        """The whole contract in one assertion."""
        result = self._reshape({"name": "Evening"})

        assert result.name == "Evening"
        assert result.enabled is True
        assert result.start.hour == 9

    def test_the_name_is_replaced_when_given(self):
        assert self._reshape({"name": "Evening"}).name == "Evening"

    def test_disabling_is_not_the_same_as_omitting(self):
        """`enabled: False` must land — a falsy value is still a value."""
        assert self._reshape({"enabled": False}).enabled is False

    def test_a_time_change_keeps_the_day(self):
        """`time` arrives as a datetime.time from the service schema,
        not as a string — voluptuous has already parsed it."""
        import datetime

        result = self._reshape({"time": datetime.time(7, 15)})

        assert result.start.hour == 7
        assert result.start.min == 15
        assert result.start.day == [1]

    def test_a_day_change_keeps_the_time(self):
        result = self._reshape({"days": ["tue"]})

        assert result.start.hour == 9
        assert result.start.min == 30


class TestDaysForUpdate:
    """`_days_for_update` decides which weekdays a schedule keeps.

    THREE RULES, AND THEY FIGHT EACH OTHER. An explicit recurrence wins.
    A multi-day schedule keeps its days, because editing one occurrence
    of a Mon/Wed/Fri clean must not silently drop the other two. But a
    SINGLE-day schedule moves with the edit — @chairstacker reported
    twice (#71) that he still could not change a weekday, because the
    preservation rule that is right for three days is wrong for one.
    """

    def _days(self, existing, weekday, explicit=None):
        from custom_components.roomba_plus.prime_schedule_services import (
            _days_for_update,
        )

        return _days_for_update(existing, weekday, explicit)

    def test_an_explicit_recurrence_wins(self):
        """`explicit` carries weekday NUMBERS — _WEEKDAY_TO_WIRE maps
        numbers to wire names, not the other way round."""
        assert self._days([1, 3, 5], 2, [6]) == self._days([], 5, [6])

    def test_a_multi_day_schedule_keeps_its_days(self):
        """Moving Wednesday's clean must not cancel Monday's."""
        result = self._days([1, 3, 5], 2)
        assert len(result) == 3

    def test_a_single_day_schedule_moves_with_the_edit(self):
        """@chairstacker's case. The rule above, applied here, is what
        made a weekday change impossible."""
        result = self._days([1], 4)
        assert len(result) == 1
        assert result != self._days([1], 1)

    def test_a_schedule_with_no_days_takes_the_edited_weekday(self):
        """What a brand-new entry looks like."""
        assert len(self._days([], 3)) == 1


class TestResolveRooms:
    """`_resolve_rooms` turns what a user typed into region entries.

    Names resolve through the coordinator's own `room_names` map — the
    same source the schedule switches are labelled from, so whatever a
    label shows is accepted here. Raw region ids pass through for rooms
    the map has not named.

    AN UNKNOWN NAME MUST RAISE. Dropping it silently would schedule a
    clean of the rooms it did recognise, and the user would believe the
    one they cared about was included.
    """

    def _entry(self, room_names=None):
        entry = MagicMock()
        coordinator = MagicMock()
        coordinator.room_names = room_names or {}
        entry.runtime_data.prime_schedule_coordinator = coordinator
        return entry

    def _resolve(self, rooms, *, room_names=None, containers=None):
        from custom_components.roomba_plus.prime_schedule_services import (
            _resolve_rooms,
        )

        return _resolve_rooms(
            self._entry(room_names), rooms, containers or []
        )

    def test_a_known_name_resolves_to_its_region(self):
        result = self._resolve(["Kitchen"], room_names={"3": "Kitchen"})
        assert result and str(result[0].get("region_id")) == "3"

    def test_the_match_ignores_case_and_padding(self):
        """The label a user reads may not be what they type."""
        result = self._resolve(["  kitchen "], room_names={"3": "Kitchen"})
        assert result and str(result[0].get("region_id")) == "3"

    def test_a_raw_region_id_passes_through(self):
        """Rooms the map has never named are addressable by number."""
        result = self._resolve(["7"], room_names={})
        assert result and str(result[0].get("region_id")) == "7"

    def test_an_unknown_name_raises_rather_than_being_dropped(self):
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            self._resolve(["Conservatory"], room_names={"3": "Kitchen"})


# ── formerly tests/test_coverage_prime_schedule_services.py ─────────────────────
#
# prime_schedule_services.py — quality scale, test-coverage.
#
# Creating a Prime schedule from the calendar derives from an existing
# schedule, because the API needs full options; with none to derive from
# there is a clear message. Deleting the last schedule of a container
# deletes the container instead of writing an empty list. Real parsed
# schedules throughout (tests.test_prime_schedule_services._options).

def _entry(containers):
    robot = MagicMock()
    robot.create_schedules = AsyncMock()
    robot.update_schedules = AsyncMock()
    robot.delete_schedule = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data = SimpleNamespace(prime_robot=robot, prime_household_id="h1",
                                         prime_room_names={"11": "Kitchen", "13": "Hall"},
                                         prime_schedule_coordinator=None)
    return entry, robot


@pytest.fixture(autouse=False)
def _io(monkeypatch):
    monkeypatch.setattr(pss, "_refresh", AsyncMock())


def _containers(monkeypatch, containers):
    monkeypatch.setattr(pss, "_read_containers_or_error", AsyncMock(return_value=containers))


def _call_m(**data):
    return SimpleNamespace(data={"entity_id": "switch.robbie_schedule", **data})


def _wire(monkeypatch, containers_seq):
    entry, robot = _entry(None)
    entry.runtime_data.blid = "BLID"
    monkeypatch.setattr(pss, "_prime_entry_for", lambda _h, _e: (entry, SimpleNamespace()))
    monkeypatch.setattr(pss, "_schedule_id_from", lambda _e, _b: "S1")
    reads = iter(containers_seq)
    monkeypatch.setattr(pss, "_read_containers_or_error", AsyncMock(side_effect=lambda _e: next(reads)))
    return entry, robot


@pytest.mark.usefixtures('_io')
class TestCreateFromCalendar:

    @pytest.mark.asyncio
    async def test_a_new_schedule_is_derived_from_an_existing_one(self, hass, monkeypatch):
        _containers(monkeypatch, [("c1", [_options("S1")])])
        entry, robot = _entry(None)
        await pss.async_create_schedule_from_calendar(
            hass, entry, name="Morning", weekday=1, hour=7, minute=30,
            frequency="WEEKLY", room_ids=["11"], note=None)
        household, options = robot.create_schedules.await_args.args
        assert household == "h1" and len(options) == 1
        assert options[0].name == "Morning"

    @pytest.mark.asyncio
    async def test_without_a_name_it_gets_one_from_its_time(self, hass, monkeypatch):
        _containers(monkeypatch, [("c1", [_options("S1")])])
        entry, robot = _entry(None)
        await pss.async_create_schedule_from_calendar(
            hass, entry, name=None, weekday=1, hour=7, minute=5, frequency="WEEKLY", room_ids=None, note=None)
        assert robot.create_schedules.await_args.args[1][0].name == "HA 07:05"

    @pytest.mark.asyncio
    async def test_no_schedule_to_derive_from_is_a_clear_error(self, hass, monkeypatch):
        _containers(monkeypatch, [])
        entry, robot = _entry(None)
        with pytest.raises(ServiceValidationError, match="no existing schedule"):
            await pss.async_create_schedule_from_calendar(
                hass, entry, name="x", weekday=1, hour=7, minute=0, frequency="WEEKLY", room_ids=None, note=None)
        robot.create_schedules.assert_not_awaited()


@pytest.mark.usefixtures('_io')
class TestDeleteById:

    @pytest.mark.asyncio
    async def test_the_last_schedule_of_a_container_deletes_the_container(self, hass, monkeypatch):
        _containers(monkeypatch, [("c1", [_options("S1")])])
        entry, robot = _entry(None)
        await pss.async_delete_schedule_by_id(hass, entry, "S1")
        robot.delete_schedule.assert_awaited_once_with("h1", "c1")
        robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_schedules_in_the_container_are_kept(self, hass, monkeypatch):
        _containers(monkeypatch, [("c1", [_options("S1"), _options("S2")])])
        entry, robot = _entry(None)
        await pss.async_delete_schedule_by_id(hass, entry, "S1")
        _h, cid, remaining = robot.update_schedules.await_args.args
        assert cid == "c1" and [s.schedule_id for s in remaining] == ["S2"]

    @pytest.mark.asyncio
    async def test_a_schedule_already_gone_is_a_clear_error(self, hass, monkeypatch):
        _containers(monkeypatch, [("c1", [_options("S1")])])
        entry, robot = _entry(None)
        with pytest.raises(ServiceValidationError, match="no longer exists"):
            await pss.async_delete_schedule_by_id(hass, entry, "S9")
        robot.delete_schedule.assert_not_awaited()


@pytest.mark.usefixtures('_io')
class TestPrimeEntryFor:

    def _hass(self, reg_entry, config_entry):
        from homeassistant.helpers import entity_registry as er

        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = config_entry
        return hass, reg_entry

    @pytest.mark.parametrize("case", ["unknown", "foreign", "classic"])
    def test_only_a_prime_robot_is_accepted(self, monkeypatch, case):
        from homeassistant.helpers import entity_registry as er

        reg_entry = None if case == "unknown" else SimpleNamespace(config_entry_id="e1")
        cfg = SimpleNamespace(domain="hue" if case == "foreign" else pss.DOMAIN,
                              runtime_data=SimpleNamespace(prime_robot=None, prime_household_id=None))
        monkeypatch.setattr(er, "async_get", lambda _h: MagicMock(async_get=lambda _e: reg_entry))
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = cfg
        with pytest.raises(ServiceValidationError):
            pss._prime_entry_for(hass, "vacuum.robbie")


@pytest.mark.usefixtures('_io')
class TestCreateService:

    @pytest.mark.asyncio
    async def test_it_returns_the_new_id_and_names_it_by_time(self, hass, monkeypatch):
        entry, robot = _wire(monkeypatch, [[("c1", [_options("S1")])]])
        robot.create_schedules = AsyncMock(return_value={"household_schedule_id": "c9"})
        result = await pss._async_create(hass, _call_m(days=["mon"], time=dt.time(8, 15)))
        assert result == {"household_schedule_id": "c9"}
        assert robot.create_schedules.await_args.args[1][0].name == "HA 08:15"

    @pytest.mark.asyncio
    async def test_no_template_is_a_clear_error(self, hass, monkeypatch):
        _wire(monkeypatch, [[]])
        with pytest.raises(ServiceValidationError, match="no existing schedule"):
            await pss._async_create(hass, _call_m(days=["mon"], time=dt.time(8, 0)))


@pytest.mark.usefixtures('_io')
class TestUpdateService:

    @pytest.mark.asyncio
    async def test_only_the_target_schedule_changes(self, hass, monkeypatch):
        both = [("c1", [_options("S1"), _options("S2", name="Evening")])]
        entry, robot = _wire(monkeypatch, [both, both])
        result = await pss._async_update(hass, _call_m(name="Renamed"))
        assert result == {"updated": "S1"}
        _h, cid, written = robot.update_schedules.await_args.args
        names = {s.schedule_id: s.options.name for s in written}
        assert names == {"S1": "Renamed", "S2": "Evening"}

    @pytest.mark.asyncio
    async def test_a_schedule_gone_before_the_first_read(self, hass, monkeypatch):
        _wire(monkeypatch, [[("c1", [_options("S2")])]])
        with pytest.raises(ServiceValidationError, match="no longer exists"):
            await pss._async_update(hass, _call_m(name="x"))

    @pytest.mark.asyncio
    async def test_a_schedule_that_vanished_between_reads_is_not_overwritten(self, hass, monkeypatch):
        """Another client deleted it while we held the old list: writing
        that list back would resurrect or clobber schedules."""
        entry, robot = _wire(monkeypatch, [[("c1", [_options("S1")])], [("c1", [_options("S2")])]])
        with pytest.raises(ServiceValidationError, match="vanished"):
            await pss._async_update(hass, _call_m(name="x"))
        robot.update_schedules.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_container_gone_between_reads(self, hass, monkeypatch):
        entry, robot = _wire(monkeypatch, [[("c1", [_options("S1")])], [("c2", [_options("S1")])]])
        with pytest.raises(ServiceValidationError, match="not found"):
            await pss._async_update(hass, _call_m(name="x"))
        robot.update_schedules.assert_not_awaited()


@pytest.mark.usefixtures('_io')
class TestCommandShaping:

    def _cmds(self):
        return [{"command": {"regions": [{"region_id": "11", "params": {}}]}},
                {"regions": [{"region_id": "13"}]},      # flat form
                "junk"]

    def test_regions_are_replaced_on_a_copy(self):
        original = self._cmds()
        out = pss._set_regions(original, [{"region_id": "99"}])
        assert [c.get("command", c)["regions"][0]["region_id"] for c in out] == ["99", "99"]
        assert original[0]["command"]["regions"][0]["region_id"] == "11", "the template is untouched"
        assert len(out) == 2, "what is not a command is dropped"

    def test_wetness_is_set_on_every_region_of_a_copy(self):
        original = self._cmds()
        out = pss._apply_wetness(original, 3)
        plates = [r["params"]["padWetness"]["padPlate"] for c in out for r in pss._regions_of(c)]
        assert plates == [3, 3]
        assert "padWetness" not in original[0]["command"]["regions"][0]["params"]


@pytest.mark.usefixtures('_io')
class TestResolveRoomsCoverage:

    def _entry(self):
        entry = MagicMock()
        entry.runtime_data.prime_schedule_coordinator = SimpleNamespace(
            room_names={"11": "Kitchen", "13": "Hall"})
        return entry

    def test_rooms_by_name_ignoring_case_and_by_id(self):
        containers = [("c1", [_options("S1", regions=("11", "13"))])]
        out = pss._resolve_rooms(self._entry(), [" kitchen ", "13"], containers)
        assert [r["region_id"] for r in out] == ["11", "13"]

    def test_an_unknown_room_is_a_clear_error(self):
        containers = [("c1", [_options("S1", regions=("11",))])]
        with pytest.raises(ServiceValidationError):
            pss._resolve_rooms(self._entry(), ["Attic"], containers)


@pytest.mark.usefixtures('_io')
class TestOneTemplatePath:

    @pytest.mark.asyncio
    async def test_an_empty_name_gets_the_default_like_a_missing_one(self, hass, monkeypatch):
        """The action and the calendar shared their template logic as two
        copies, with different ideas of a missing name. One path now."""
        entry, robot = _wire(monkeypatch, [[("c1", [_options("S1")])]])
        robot.create_schedules = AsyncMock(return_value={"household_schedule_id": "c9"})
        await pss._async_create(hass, _call_m(days=["mon"], time=dt.time(9, 40), name=""))
        assert robot.create_schedules.await_args.args[1][0].name == "HA 09:40"


# ── formerly tests/test_schedule_create_shape.py ──────────────────────
#
# The confirmed shape of a schedule-create payload, pinned.
#
# WHERE IT COMES FROM. @utkjmitch created a schedule through the library,
# read it back and deleted it -- three full cycles on a real account
# (issue #49). This fixture is what crossed the wire, with ids redacted
# and nothing else altered.
#
# WHY IT IS PINNED RATHER THAN DESCRIBED. Getting this shape wrong cost
# four field rounds against an HTTP 500 with no field named: `initiator`
# was ruled out, `is_smart_clean_fav` was ruled out, `created_time` was
# dropped, and the actual cause turned out to be a missing `options`
# level. A payload that once worked is worth more than any description of
# one.
#
# WHAT IT PROVES BEYOND THE ENVELOPE. Per-region `padWetness` is stored,
# which was open: this server accepts-and-ignores elsewhere (`schedHold`),
# so a 200 proves nothing on its own. He wrote 1 where the global setting
# is 3 and the stored per-region value was 2 -- a value distinctive enough
# that the read-back cannot be confused with what was already there. That
# is the method this project asks for and it was followed without being
# asked.

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "schedule_create_confirmed.json"


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text())


class TestOurModelRoundTripsTheConfirmedPayload:
    def test_nothing_is_lost_or_added(self):
        """The one property that matters for building a create service:
        what we parse and re-serialise has to equal what the server
        accepted."""
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        sent = _payload()["sent"]
        assert ScheduleOptions.from_json(sent).to_json() == sent

    def test_the_wrapper_key_and_the_verb_share_a_name(self):
        """A trap worth pinning. `commands` entries arrive wrapped as
        `{"command": {...}}`, and the object inside ALSO has a `command`
        key -- holding the verb `"start"`.

        So after unwrapping, `commands[0]["command"]` still exists and is
        a string. Anything deciding "is this wrapped?" has to check the
        TYPE, not the presence of the key. `_schedule_region_ids` reads
        the wrapper form and the unwrapped form for exactly this reason,
        and got it wrong the first time -- room labels silently fell back
        to bare times for a whole release.
        """
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        options = ScheduleOptions.from_json(_payload()["sent"])
        inner = options.commands[0]

        assert inner["command"] == "start"
        assert isinstance(options.to_json()["commands"][0]["command"], dict)


class TestWhatTheServerSuppliesItself:
    def test_the_fields_we_must_not_send(self):
        """Each of these was once suspected of causing the 500.
        `created_time` really is server-assigned -- the response carried
        a fresh stamp, not the copied one."""
        assert set(_payload()["server_added"]) == {
            "created_time", "is_smart_clean_fav", "schedule_id"
        }

    def test_the_schedule_id_is_derived_from_the_container(self):
        """Third independent confirmation: container id plus a
        four-character suffix."""
        data = _payload()

        assert data["schedule_id_returned"].startswith(data["container_id"] + "_")


class TestPerRegionWetnessIsStorable:
    """Open until this run. The AutoWash work established that regions
    override the global setting, but not whether a per-region write
    sticks -- and this server accepts-and-ignores in at least one other
    place.
    """

    def _regions(self) -> list[dict]:
        return _payload()["sent"]["commands"][0]["command"]["regions"]

    def test_every_region_carries_its_own_wetness(self):
        regions = self._regions()

        assert len(regions) == 5
        for region in regions:
            assert region["params"]["padWetness"] == {"padPlate": 1}

    def test_the_value_was_chosen_to_be_distinguishable(self):
        """1 against a global of 3 and a stored per-region 2. A round
        number matching what was already there would have proved
        nothing."""
        assert {r["params"]["padWetness"]["padPlate"] for r in self._regions()} == {1}

    def test_the_region_parameter_set(self):
        """Four keys, and this is the full confirmed set -- anything a
        service offers beyond them would be invented."""
        for region in self._regions():
            assert set(region["params"]) == {
                "operatingMode", "suctionLevel", "twoPass", "padWetness"
            }


class TestTheCommandLevelFields:
    def _command(self) -> dict:
        return _payload()["sent"]["commands"][0]["command"]

    def test_only_routine_modified_is_passed_as_a_command_param(self):
        """Matches the app's own `onlyUserModifiableParams()`, which
        keeps exactly this one key. Everything else about the job lives
        on the regions."""
        assert self._command()["params"] == {"routine_modified": True}

    def test_the_map_is_named_twice_and_both_are_needed(self):
        command = self._command()

        assert command["p2map_id"]
        assert command["user_p2mapv_id"]

    def test_select_all_is_false_when_regions_are_listed(self):
        command = self._command()

        assert command["select_all"] is False
        assert command["regions"]
