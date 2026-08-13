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
from .structural_failures import record_failure, record_success

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
        record_success("schedule read")
    except Exception:  # noqa: BLE001
        record_failure("schedule read", "get_schedules()")
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


#: How many room names a label carries before it gets a "+n" tail.
#: Two is enough to tell schedules apart and short enough to read in a
#: list; a seven-room schedule would otherwise produce a label nobody
#: can scan.
_MAX_ROOMS_IN_LABEL = 2


def _schedule_region_ids(options: Any) -> list[str]:
    """The region ids a schedule cleans, in the order it cleans them.

    BOTH SHAPES ARE ACCEPTED, and that is not defensiveness -- it is a
    bug this function had. On the wire each entry is
    `{"command": {... "regions": [...]}}`, but ScheduleOptions.from_json
    UNWRAPS it (`commands=[c.get("command", c) for c in ...]`), so a
    parsed schedule holds the inner dict directly. The first version
    looked only for the wrapper, found nothing on every real schedule,
    and silently fell back to the bare start time.

    It was caught by running a real tester's three schedules through it
    -- the hand-built test fixtures used the wire shape, which is
    exactly the mistake tests/prime_fixtures.py exists to prevent, made
    inside a test rather than in the code under test.

    Regions carry `region_id` plus a `type` of "rid" (room), "zid"
    (zone) or "tid" (temporary/ad-hoc). An earlier version of this note
    said "furniture" — that came from `RegionType.TID` in the library,
    which carried the wrong value for months. It is "tid"
    (`IrobotRegionType`, app 3.0.0). Nothing here read the type, so the
    error stayed in prose; it would have misled the next person to.
    Everything is read defensively beyond that: these are raw dicts, and
    this runs inside a coordinator listener where an exception would
    take the whole switch platform with it.
    """
    ids: list[str] = []
    for entry in getattr(options, "commands", None) or []:
        if not isinstance(entry, dict):
            continue
        command = entry.get("command") if isinstance(entry.get("command"), dict) else entry
        if not isinstance(command, dict):
            continue
        for region in command.get("regions") or []:
            if not isinstance(region, dict):
                continue
            region_id = region.get("region_id")
            if region_id is not None and str(region_id) not in ids:
                ids.append(str(region_id))
    return ids


def _schedule_days(options: Any, weekday_names: dict[int, str] | None = None) -> str:
    """The weekday part of a label, e.g. "Mon-Thu" or "Fri".

    TRANSLATED, and that is the whole reason this takes a lookup rather
    than formatting the days itself. Hard-coding English abbreviations
    would be wrong for seven of the eight languages this integration
    ships, and `calendar.day_abbr` follows the process locale rather
    than the one configured in Home Assistant.

    So the names come from our own strings.json (`common.weekday_0` ..
    `weekday_6`, iRobot's numbering: 0 = Sunday), resolved once per
    entity in async_added_to_hass.

    Ranges are collapsed only when the days are genuinely consecutive
    in iRobot's own order -- [1,2,3,4] becomes "Mon-Thu", while
    [1,3,5] stays "Mon, Wed, Fri". Sorted first because the order is
    not stable between reads: the same untouched schedule came back as
    [4,3,1,2], then [1,2,4,3], then [3,1,2,4].
    """
    start = getattr(options, "start", None)
    raw = getattr(start, "day", None) if start else None
    # isinstance rather than truthiness: this runs inside a coordinator
    # listener, and anything that is not a real mapping of real strings
    # has to degrade to "no days" instead of raising and taking the
    # switch platform with it.
    if not raw or not isinstance(weekday_names, dict):
        return ""
    try:
        days = sorted({int(d) for d in raw})
    except (TypeError, ValueError):
        return ""
    labels = [weekday_names.get(d) for d in days]
    if not all(isinstance(label, str) and label for label in labels):
        return ""
    if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{labels[0]}-{labels[-1]}"
    return ", ".join(labels)


def _schedule_label(
    options: Any,
    room_names: dict[str, str] | None = None,
    weekday_names: dict[int, str] | None = None,
) -> str:
    """A label that tells one schedule from another.

    THE NAME FIELD CANNOT. The iRobot app calls every schedule it
    creates "Regular Schedule" -- nine of them across two accounts, all
    identical -- so a household with six gets six identically named
    switches (@chairstacker, a20). APK analysis then established that
    `name` is a hard-coded default with no user relation: the app shows
    no name anywhere, and offers no field to set one.

    So the label is built the way the app's own list is: from the ROOMS
    a schedule cleans, plus its start time.

        "Kitchen, Bathroom 09:00"
        "Kitchen, Bathroom +3 09:00"      (five rooms)
        "09:00"                            (no rooms resolved)

    Room names rather than the app's "2 Rooms, Whole House": that
    phrasing would leave two schedules covering two rooms each equally
    indistinguishable, which is the problem this exists to solve. It
    also needs no plural forms in eight languages, and no answer to
    where "Whole House" comes from -- a string the APK analysis could
    not find as a resource at all.

    Falls back to the start time alone when no name resolves, and to the
    raw `name` when there is no start time either. A bare region id
    ("Zone 13") is deliberately NOT used as a filler: it looks like
    information and is not, and the time already separates schedules
    better than a number would.

    The schedule id stays the basis for `suggested_object_id`, so a
    label that changes when rooms get renamed still cannot rename the
    entity out from under an automation.
    """
    start = getattr(options, "start", None)
    hour = getattr(start, "hour", None) if start else None
    stamp = (
        f"{int(hour):02d}:{int(getattr(start, 'min', 0) or 0):02d}"
        if hour is not None else ""
    )

    # THE DAYS ARE NOT OPTIONAL DECORATION. Two of one tester's three
    # schedules clean the SAME rooms at the SAME time and differ only
    # in which days they run -- rooms plus time left them identical, so
    # the label that was supposed to solve six-identical-switches
    # produced two of its own.
    days = _schedule_days(options, weekday_names)

    named = [
        name for region_id in _schedule_region_ids(options)
        if (name := (room_names or {}).get(region_id))
    ]
    if named:
        shown = ", ".join(named[:_MAX_ROOMS_IN_LABEL])
        if len(named) > _MAX_ROOMS_IN_LABEL:
            shown = f"{shown} +{len(named) - _MAX_ROOMS_IN_LABEL}"
        return " ".join(part for part in (shown, days, stamp) if part)

    if days or stamp:
        return " ".join(part for part in (days, stamp) if part)
    return (getattr(options, "name", None) or "").strip()


def build_prime_schedule_switches(
    config_entry: RoombaConfigEntry,
    containers: list[tuple[str, list[Any]]],
    room_names: dict[str, str] | None = None,
    weekday_names: dict[int, str] | None = None,
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
            # QUIET HOURS ARRIVE IN THIS LIST TOO, and they are not
            # cleaning schedules.
            #
            # @DaRealGuGu set Do Not Disturb in the OLD iRobot app and
            # two switches appeared in Home Assistant for his PRIME
            # robot -- matching the quiet-hours times, and shown nowhere
            # in the Roomba app. Deleting the quiet hours made them go
            # away again.
            #
            # So the household schedule endpoint carries more than
            # cleaning schedules, and we were rendering all of it. A
            # switch for a quiet-hours entry is worse than a missing
            # one: toggling it writes the whole container back, so a
            # user "turning off a schedule" would have been rewriting
            # their quiet hours from whatever we managed to parse.
            #
            # THE DISCRIMINATOR IS `end`, NOT A TYPE FIELD. A cleaning
            # schedule says when to start; quiet hours are an interval
            # and carry both ends. The app's own HouseholdScheduleType
            # enum exists but lives in native code and has never been
            # read, so this is the shape rather than the label -- and if
            # a cleaning schedule with an end time ever turns up, this
            # is the line that will be wrong.
            if options.end is not None or options.end_commands:
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
                _schedule_label(options, room_names, weekday_names),
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
                    self._refresh_label(schedule.options)
                    return
        # The read succeeded and this schedule was not in it: deleted in
        # the app. Unknown, not off.
        self._attr_is_on = None

    def _refresh_label(self, options: Any) -> None:
        """Keeps the displayed name in step with the schedule.

        The label used to be computed once, in __init__, and never
        again -- so a schedule renamed, re-timed or re-roomed kept its
        old label until the entity was rebuilt. Nobody noticed while the
        only way to change a schedule was the iRobot app, which does not
        offer a rename field at all.

        It became visible the moment someone built a service that CAN
        rename one (@utkjmitch, #49): the data updated and the cosmetics
        lagged.

        The unique id is untouched by this, and so is the entity id --
        both come from the schedule id. Only what the user reads changes,
        which is the point: a label that describes rooms and times has to
        follow when the rooms and times do.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_schedule_coordinator", None
        )
        label = _schedule_label(
            options,
            getattr(coordinator, "room_names", None),
            getattr(coordinator, "weekday_names", None),
        )
        if label and label != self._attr_translation_placeholders.get("schedule"):
            self._attr_translation_placeholders = {"schedule": label}

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
