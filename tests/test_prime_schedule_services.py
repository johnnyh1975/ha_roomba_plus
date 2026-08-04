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
