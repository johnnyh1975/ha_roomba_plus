"""Enabling and disabling Prime cleaning schedules from Home Assistant.

WHY THIS IS A SWITCH PER SCHEDULE.

The robot holds a list of named schedules -- "Weekdays", "Saturday
deep clean" -- each with its own `enabled` flag. One switch per schedule
mirrors that exactly, and it means an automation can turn off just the
weekday routine while away without touching the rest.

A single "schedules enabled" switch was the obvious alternative and is
wrong: it would have to invent a meaning for the mixed state, and
turning it back on could not know which schedules had been off before.

WHAT WAS ALREADY THERE AND WHAT WAS MISSING.

Reading works: PrimeScheduleCalendar has shown schedule occurrences
since v4.0.0a5. Writing was confirmed in the field twice
(@chairstacker) and has sat in the version plan as "confirmed, not
wired" since. This is that wiring.

THE ID THE CALENDAR THROWS AWAY.

`get_schedules()` returns a two-level structure: a list of
SchedulesList, each carrying a `household_schedule_id` and the
schedules inside it. The calendar flattens straight to the inner
schedules because occurrences are all it needs.

`update_schedules()` requires that outer id -- it addresses the
container, not an individual schedule. So this reads the structure
itself rather than reusing the calendar's flattened view, and a
read-modify-write is unavoidable: the endpoint takes the whole list.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.components.switch import SwitchEntity

from .entity import IRobotEntity

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: One lock per (config entry, schedule container).
#:
#: FOUND IN THE a18 BUG HUNT. Toggling is a read-modify-write of the
#: WHOLE container, because update_schedules() replaces the list. Two
#: switches in the same container toggled at once both read the same
#: state and both write it back with only their own change -- so the
#: second write silently reverts the first, while Home Assistant shows
#: both as changed.
#:
#: Not a rare interleaving: `switch.turn_off` against a group, or an
#: automation disabling several schedules, does exactly this. Reproduced
#: with two concurrent turn_off calls and 10ms of read latency.
#:
#: Keyed per container rather than one global lock: two containers have
#: no shared state to lose, and serialising them would make a
#: multi-container account slower for no reason. Keyed by entry_id so a
#: second robot never waits on the first.
_CONTAINER_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _container_lock(entry_id: str, container_id: str) -> asyncio.Lock:
    return _CONTAINER_LOCKS.setdefault((entry_id, container_id), asyncio.Lock())


async def async_read_schedule_containers(
    config_entry: RoombaConfigEntry,
) -> list[tuple[str, list[Any]]] | None:
    """Schedule containers as (household_schedule_id, schedules).

    Returns the outer structure rather than a flat schedule list,
    because the write endpoint addresses the container.

    THE SCHEDULES ARE PARSED HERE, and that is the whole point of this
    function existing rather than each caller reading the response.

    `SchedulesList.schedules` is `list[dict]` -- raw dicts, not
    HouseholdSchedule instances, as its own docstring in roombapy-prime
    says. This used to hand those dicts straight to its callers, and
    every one of them read `.schedule_id` / `.options` off them. On a
    dict that returns None, so:

      - switch.py skipped every schedule -> NO SWITCH WAS EVER CREATED,
        on any account, for the entire life of this feature
      - async_update read enabled=False for whatever it found
      - _async_set_enabled would have passed dicts to update_schedules(),
        which does `[s.to_json() for s in schedules]` -> AttributeError

    Three broken paths, one cause. calendar.py already parses (its own
    docstring records finding the same bug), so this brings the two
    readers of the same endpoint into agreement instead of leaving one
    right and one wrong.

    RETURNS None WHEN THE READ FAILED, [] WHEN IT SUCCEEDED AND FOUND
    NOTHING. Found in the a18 bug hunt: these used to be the same value,
    so a caller could not tell "this schedule was deleted in the app"
    from "the cloud call did not come back". async_update needs exactly
    that difference -- one means the switch should go unavailable, the
    other means keep showing what was last known and try again.

    Collapsing a failure into an empty result is the recurring shape of
    every bug this feature has had.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    household_id = getattr(data, "prime_household_id", None)
    if robot is None or not household_id:
        return None

    try:
        response = await robot.get_schedules(household_id)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: get_schedules() failed", exc_info=True)
        return None

    from roombapy_prime.models.schedules_dnd import (  # noqa: PLC0415
        HouseholdSchedule,
    )

    containers: list[tuple[str, list[Any]]] = []
    for container in getattr(response, "household_schedules", None) or []:
        container_id = getattr(container, "household_schedule_id", None)
        if not container_id:
            continue
        containers.append((
            container_id,
            [
                HouseholdSchedule.from_json(raw)
                for raw in (getattr(container, "schedules", None) or [])
                if isinstance(raw, dict)
            ],
        ))
    return containers


def build_prime_schedule_switches(
    config_entry: RoombaConfigEntry,
    containers: list[tuple[str, list[Any]]],
) -> list[PrimeScheduleSwitch]:
    """One switch per schedule that can meaningfully carry one.

    Lives here rather than in switch.py because the platform now calls
    it on every coordinator refresh, not only at setup -- schedules
    appear and disappear in the iRobot app, and the entity list has to
    follow. See switch.py's _sync_entities for what that costs.
    """
    switches: list[PrimeScheduleSwitch] = []
    for container_id, schedules in containers:
        for schedule in schedules:
            if not schedule.schedule_id:
                continue
            options = schedule.options
            # A deleted schedule stays in the payload with deleted=True.
            # Creating a switch for it would offer control over
            # something the app no longer shows.
            #
            # Field note: a schedule deleted in the app simply VANISHES
            # from the response rather than arriving with deleted=True,
            # so this guard is correct but has never fired in practice.
            # Real deletions are handled by the entity going unavailable
            # when _apply cannot find its schedule any more.
            if options.deleted:
                continue
            # NO SWITCH FOR A SCHEDULE WHOSE STATE THE SERVER DID NOT
            # SEND. Toggling writes the WHOLE container back, so
            # offering a switch for a schedule we know nothing about
            # means re-serialising it from defaults -- writing over
            # settings the user has and we never saw. `enabled is None`
            # is the honest form of that question: False is a real
            # answer and gets a switch, None means unanswered.
            if options.enabled is None:
                continue
            switches.append(PrimeScheduleSwitch(
                config_entry, container_id, str(schedule.schedule_id),
                options.name or "",
            ))
    return switches


class PrimeScheduleSwitch(IRobotEntity, SwitchEntity):
    """One robot schedule, on or off.

    Identified by its schedule_id rather than its position in the list:
    a schedule deleted in the iRobot app shifts every index after it,
    and an index-keyed switch would silently start controlling a
    different routine.

    INHERITS IRobotEntity FOR DeviceInfo. Found in the a18 bug hunt:
    this was the only Prime entity in the integration that did not, so
    it carried no device_info at all -- the switches would have appeared
    outside the robot's device page, and with has_entity_name and no
    device to prefix them, two robots' schedules would have been
    indistinguishable in the entity list.

    Invisible until now for the same reason everything else about this
    class was: no instance was ever created.
    """

    _attr_has_entity_name = True

    #: NO POLLING. SwitchEntity polls every 30 seconds by default, and
    #: async_update here is a cloud round trip -- three schedules would
    #: mean roughly 8,600 requests a day for data that changes when
    #: somebody edits a schedule in the iRobot app, which is to say
    #: almost never.
    #:
    #: STILL FALSE, BUT FOR A DIFFERENT REASON NOW. This used to mean
    #: "read once at creation, once after our own write, and otherwise
    #: never" -- and the comment here justified that as "the right trade
    #: for a setting nobody watches change".
    #:
    #: That was wrong, and a field test showed how (chairstacker):
    #: switch the automations off in the iRobot app before a holiday,
    #: and a Home Assistant automation using this switch as a CONDITION
    #: goes on acting on a value that has been false since the last
    #: restart. A switch entity is machine-readable state, not
    #: decoration, so stale is a correctness problem rather than a
    #: cosmetic one -- and a silent one.
    #:
    #: Polling is still off because the refresh belongs to
    #: PrimeScheduleCoordinator, not to each entity: six schedules would
    #: otherwise mean six cloud calls per cycle for one account-wide
    #: answer, which scripts/check_request_budget.py forbids and should.
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: RoombaConfigEntry,
        container_id: str,
        schedule_id: str,
        name: str,
    ) -> None:
        IRobotEntity.__init__(
            self,
            roomba=None,
            blid=config_entry.runtime_data.blid,
            config_entry=config_entry,
        )
        self._config_entry = config_entry
        self._container_id = container_id
        self._schedule_id = schedule_id
        # TRANSLATED PREFIX, user-supplied name substituted in.
        #
        # The same pattern the consumable sensors use ("Maintenance –
        # {part}"). The schedule NAME comes from the iRobot app and
        # cannot be translated -- but "Schedule" can be, and a first
        # draft here set _attr_name to the bare name, leaving an entity
        # called just "Weekdays" with no indication of what it controls.
        #
        # The fallback matters too: an unnamed schedule got
        # f"Schedule {id}" in hard-coded English, which is precisely the
        # kind of string a translation file exists for.
        self._attr_translation_key = "prime_schedule"
        self._attr_translation_placeholders = {
            "schedule": name or schedule_id
        }
        self._attr_unique_id = (
            f"{config_entry.runtime_data.blid}_schedule_{schedule_id}"
        )
        self._attr_is_on: bool | None = None

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug.

        has_entity_name plus a translation_key makes HA derive the
        entity_id from the TRANSLATED name, which produces different ids
        per language on first registration. Pinning it here keeps
        automations portable -- a trap this project has hit before.

        Keyed on schedule_id rather than the schedule's name: renaming a
        routine in the iRobot app must not rename the entity out from
        under an automation.
        """
        return f"schedule_{self._schedule_id}"

    @property
    def available(self) -> bool:
        """Unknown state is not off.

        A schedule whose flag has never been read must not render as
        disabled -- someone would turn it "on" and write a value that was
        already set, or worse, believe their cleaning schedule was off.
        """
        return self._attr_is_on is not None

    async def async_added_to_hass(self) -> None:
        # IRobotEntity's own async_added_to_hass registers the roombapy
        # callback and refreshes the device name; it is guarded for
        # roomba=None (Prime), so it is safe and must not be skipped --
        # skipping it is what would leave the device name generic.
        await super().async_added_to_hass()

        # SUBSCRIBE TO THE COORDINATOR THAT CARRIES THIS ENTITY'S DATA.
        # Missing exactly this subscription is what left the vacuum
        # entity showing "Returning to dock" after a mission had ended,
        # and it is what left these switches frozen at whatever they read
        # when they were created.
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self._handle_coordinator_update)
            )
            if coordinator.data is not None:
                self._apply(coordinator.data)
                return
        await self.async_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        coordinator = self._config_entry.runtime_data.prime_schedule_coordinator
        if coordinator.data is not None:
            self._apply(coordinator.data)
            self.async_write_ha_state()

    def _apply(self, containers: list[tuple[str, list[Any]]]) -> None:
        """Sets is_on from a container list somebody else fetched.

        None stays None -- `available` reads exactly this to decide
        whether the flag has ever been read, and bool(None) would render
        an unknown schedule as off.
        """
        for container_id, schedules in containers:
            if container_id != self._container_id:
                continue
            for schedule in schedules:
                if schedule.schedule_id == self._schedule_id:
                    self._attr_is_on = schedule.options.enabled
                    return
        # The read succeeded and this schedule was not in it: deleted in
        # the app. Unknown, not off.
        self._attr_is_on = None

    async def async_update(self) -> None:
        """Re-reads the enabled flag for this one schedule.

        A schedule deleted in the iRobot app leaves this switch behind.
        Found in the a18 bug hunt: it used to keep reporting whatever it
        last read, so a deleted schedule went on showing "on"
        indefinitely, and pressing it did nothing but log a warning.
        Now the state goes unknown, which `available` renders as
        unavailable -- visibly not a working control.

        A FAILED read is deliberately not the same thing: it keeps the
        last known state rather than making the entity flicker out on
        every cloud hiccup.
        """
        containers = await async_read_schedule_containers(self._config_entry)
        if containers is None:
            # A failed read keeps the last known state rather than making
            # the entity flicker out on every cloud hiccup.
            return
        self._apply(containers)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        """Read, modify one flag, write the whole container back.

        READ-MODIFY-WRITE IS FORCED, not chosen: update_schedules() takes
        the complete schedule list for a container. Sending only the
        changed schedule would delete every other one -- the same shape
        as set_virtual_wall, where a partial list silently removes the
        zones it omits.
        """
        from dataclasses import replace  # noqa: PLC0415

        data = self._config_entry.runtime_data
        robot = data.prime_robot
        household_id = data.prime_household_id
        if robot is None or not household_id:
            return

        # THE READ AND THE WRITE MUST NOT BE SEPARATED. See
        # _CONTAINER_LOCKS for the lost update this prevents. The lock is
        # held across both, so the second caller re-reads state that
        # already includes the first one's change.
        async with _container_lock(
            self._config_entry.entry_id, self._container_id
        ):
            await self._async_set_enabled_locked(robot, household_id, enabled, replace)

    async def _async_set_enabled_locked(
        self, robot: Any, household_id: str, enabled: bool, replace: Any
    ) -> None:
        containers = await async_read_schedule_containers(self._config_entry)
        if containers is None:
            # Never write a container list built from a read that failed:
            # update_schedules() replaces the whole list, so a partial or
            # stale one deletes schedules.
            _LOGGER.warning(
                "roomba_plus: could not read schedules before writing -- "
                "schedule %s not changed", self._schedule_id,
            )
            return
        for container_id, schedules in containers:
            if container_id != self._container_id:
                continue

            found = False
            updated: list[Any] = []
            for schedule in schedules:
                if schedule.schedule_id == self._schedule_id:
                    options = schedule.options
                    if options.enabled is None:
                        # Nothing to toggle, and inventing an options
                        # object would write defaults for every other
                        # field of this schedule.
                        return
                    updated.append(replace(schedule, options=replace(
                        options, enabled=enabled
                    )))
                    found = True
                else:
                    updated.append(schedule)

            if not found:
                # Deleted in the app since this switch was created.
                # Writing the list back unchanged would be pointless, and
                # writing without it would delete it a second time.
                _LOGGER.warning(
                    "roomba_plus: schedule %s no longer exists on the robot",
                    self._schedule_id,
                )
                return

            await robot.update_schedules(household_id, container_id, updated)
            self._attr_is_on = enabled
            self.async_write_ha_state()
            return
