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

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_read_schedule_containers(
    config_entry: RoombaConfigEntry,
) -> list[tuple[str, list[Any]]]:
    """Schedule containers as (household_schedule_id, schedules).

    Returns the outer structure rather than a flat schedule list,
    because the write endpoint addresses the container.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    household_id = getattr(data, "prime_household_id", None)
    if robot is None or not household_id:
        return []

    try:
        response = await robot.get_schedules(household_id)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: get_schedules() failed", exc_info=True)
        return []

    return [
        (container.household_schedule_id, list(container.schedules or []))
        for container in (getattr(response, "household_schedules", None) or [])
        if getattr(container, "household_schedule_id", None)
    ]


class PrimeScheduleSwitch(SwitchEntity):
    """One robot schedule, on or off.

    Identified by its schedule_id rather than its position in the list:
    a schedule deleted in the iRobot app shifts every index after it,
    and an index-keyed switch would silently start controlling a
    different routine.
    """

    _attr_has_entity_name = True

    #: NO POLLING. SwitchEntity polls every 30 seconds by default, and
    #: async_update here is a cloud round trip -- three schedules would
    #: mean roughly 8,600 requests a day for data that changes when
    #: somebody edits a schedule in the iRobot app, which is to say
    #: almost never.
    #:
    #: The state is read once when the entity is added, and again after
    #: this integration writes it. A schedule toggled in the app shows
    #: up on the next reload rather than within 30 seconds, which is the
    #: right trade for a setting nobody watches change.
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: RoombaConfigEntry,
        container_id: str,
        schedule_id: str,
        name: str,
    ) -> None:
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
        await self.async_update()

    async def async_update(self) -> None:
        """Re-reads the enabled flag for this one schedule."""
        for container_id, schedules in await async_read_schedule_containers(
            self._config_entry
        ):
            if container_id != self._container_id:
                continue
            for schedule in schedules:
                if getattr(schedule, "schedule_id", None) != self._schedule_id:
                    continue
                options = getattr(schedule, "options", None)
                self._attr_is_on = bool(getattr(options, "enabled", False))
                return

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

        containers = await async_read_schedule_containers(self._config_entry)
        for container_id, schedules in containers:
            if container_id != self._container_id:
                continue

            found = False
            updated: list[Any] = []
            for schedule in schedules:
                if getattr(schedule, "schedule_id", None) == self._schedule_id:
                    options = getattr(schedule, "options", None)
                    if options is None:
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
