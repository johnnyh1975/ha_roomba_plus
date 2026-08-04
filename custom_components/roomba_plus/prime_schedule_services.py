"""Create, update and delete Prime cleaning schedules from Home Assistant.

CONTRIBUTED BY @utkjmitch (issue #49), built and field-tested on a real
Y351020 household -- three full create/update/delete cycles before it
was offered. Taken close to as written; the changes made on integration
are marked ON INTEGRATION in the comments. It adds the
write half of what prime_schedule_switch.py reads: the switches sync and
toggle schedules; these services create, reshape and remove them.

WHAT IT REUSES, DELIBERATELY. All three services go through
async_read_schedule_containers() and _container_lock() from
prime_schedule_switch.py — the same read-modify-write-under-lock that
toggling has used since the a18 lost-update fix. No new write pattern is
introduced; the blast radius of any write stays one container.

CREATE DERIVES FROM AN EXISTING SCHEDULE, exactly as the upstream CLI's
schedule_create_delete does and for the same reason: a schedule built
from literals says nothing about which robot or what to do, and the
server 500s on it (the b7–b9 saga). A household with no schedules at all
cannot create one here — the honest limitation, stated in the error.

SERVER-ASSIGNED FIELDS ARE OMITTED, never null: schedule_id,
created_time, is_smart_clean_fav (b9, field-confirmed).

PER-ROOM padWetness IS WRITABLE because a field capture proved the
server stores it: regions[].params.padWetness={"padPlate": N} sent on a
disabled schedule came back exactly as sent (Y351020, 2026-08-03,
5/5 regions). Without that capture this parameter would be gated off —
the a20 AutoWash lesson is that this server also accepts-and-ignores.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .prime_schedule_switch import (
    _container_lock,
    async_read_schedule_containers,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CREATE_SCHEDULE = "create_schedule"
SERVICE_UPDATE_SCHEDULE = "update_schedule"
SERVICE_DELETE_SCHEDULE = "delete_schedule"

#: iRobot's own numbering, confirmed live: 0 = Sunday.
_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

_FREQUENCIES = ("WEEKLY", "BI_WEEKLY", "MONTHLY", "ONCE")

_CREATE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("name"): cv.string,
    vol.Required("days"): vol.All(cv.ensure_list, [vol.In(_WEEKDAYS)]),
    vol.Required("time"): cv.time,
    vol.Optional("frequency", default="WEEKLY"): vol.In(_FREQUENCIES),
    vol.Optional("rooms"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("pad_wetness"): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    vol.Optional("enabled", default=True): cv.boolean,
})

_UPDATE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("name"): cv.string,
    vol.Optional("days"): vol.All(cv.ensure_list, [vol.In(_WEEKDAYS)]),
    vol.Optional("time"): cv.time,
    vol.Optional("frequency"): vol.In(_FREQUENCIES),
    vol.Optional("rooms"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("pad_wetness"): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    vol.Optional("enabled"): cv.boolean,
})

_DELETE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})


def _prime_entry_for(hass: HomeAssistant, entity_id: str):
    """The Prime config entry behind an entity, or a legible error."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or not entry.config_entry_id:
        raise ServiceValidationError(f"{entity_id} is not a roomba_plus entity")
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None or config_entry.domain != DOMAIN:
        raise ServiceValidationError(f"{entity_id} does not belong to {DOMAIN}")
    data = config_entry.runtime_data
    if getattr(data, "prime_robot", None) is None or not getattr(
        data, "prime_household_id", None
    ):
        raise ServiceValidationError(
            f"{entity_id} is not a Prime (cloud/V4) robot — these services "
            "manage iRobot cloud schedules and need one"
        )
    return config_entry, entry


def _schedule_id_from(entry: er.RegistryEntry, blid: str) -> str:
    """The schedule id a switch entity carries in its unique_id."""
    prefix = f"{blid}_schedule_"
    unique_id = str(entry.unique_id or "")
    if not unique_id.startswith(prefix):
        raise ServiceValidationError(
            f"{entry.entity_id} is not a schedule switch — target the "
            "schedule's own switch entity"
        )
    return unique_id[len(prefix):]


def _regions_of(command: Any) -> list[dict]:
    if not isinstance(command, dict):
        return []
    inner = command.get("command") if isinstance(command.get("command"), dict) else command
    regions = inner.get("regions") if isinstance(inner, dict) else None
    return [r for r in regions if isinstance(r, dict)] if isinstance(regions, list) else []


def _resolve_rooms(
    config_entry, rooms: list[str], containers: list
) -> list[dict]:
    """Room names or region ids → region entries.

    Names resolve through the schedule coordinator's own room_names map
    (region_id → display name) — the same source the switch labels use,
    so whatever a label shows is accepted here. Raw region ids pass
    through for rooms the map hasn't named.
    """
    coordinator = getattr(
        config_entry.runtime_data, "prime_schedule_coordinator", None
    )
    room_names: dict[str, str] = dict(getattr(coordinator, "room_names", None) or {})
    by_name = {name.strip().casefold(): rid for rid, name in room_names.items()}

    # Region entries already known to this household's schedules keep
    # their stored shape (type, params) rather than being re-invented.
    known: dict[str, dict] = {}
    for _cid, schedules in containers:
        for schedule in schedules:
            for command in getattr(schedule.options, "commands", None) or []:
                for region in _regions_of(command):
                    rid = str(region.get("region_id", ""))
                    if rid and rid not in known:
                        known[rid] = region

    resolved: list[dict] = []
    unknown: list[str] = []
    for room in rooms:
        rid = by_name.get(room.strip().casefold()) or (
            room if str(room) in known or str(room).isdigit() else None
        )
        if rid is None:
            unknown.append(room)
            continue
        import copy  # noqa: PLC0415

        resolved.append(
            copy.deepcopy(known.get(str(rid))) or {"region_id": str(rid), "type": "rid"}
        )
    if unknown:
        raise ServiceValidationError(
            f"Unknown room(s): {', '.join(unknown)}. Known: "
            f"{', '.join(sorted(room_names.values())) or '(no named rooms yet)'}"
        )
    return resolved


def _apply_wetness(commands: list, wetness: int) -> list:
    import copy  # noqa: PLC0415

    updated = copy.deepcopy([c for c in commands if isinstance(c, dict)])
    for command in updated:
        for region in _regions_of(command):
            region.setdefault("params", {})["padWetness"] = {"padPlate": wetness}
    return updated


def _set_regions(commands: list, regions: list[dict]) -> list:
    """Template commands with their regions replaced by the requested ones."""
    import copy  # noqa: PLC0415

    updated = copy.deepcopy([c for c in commands if isinstance(c, dict)])
    for command in updated:
        inner = command.get("command") if isinstance(command.get("command"), dict) else command
        if isinstance(inner, dict) and "regions" in inner:
            inner["regions"] = copy.deepcopy(regions)
    return updated


def _reshaped_options(template_options: Any, call_data: dict, containers: list,
                      config_entry) -> Any:
    """A ScheduleOptions carrying the requested changes on top of a template.

    Works for create (template = an existing schedule, all required
    fields present in call_data) and update (template = the schedule
    itself, only provided fields change).
    """
    from roombapy_prime.models.schedules_dnd import (  # noqa: PLC0415
        ScheduleFrequency,
        ScheduleTime,
    )

    changes: dict[str, Any] = {}
    if "name" in call_data:
        changes["name"] = call_data["name"]
    if "enabled" in call_data:
        changes["enabled"] = call_data["enabled"]
    if "frequency" in call_data:
        changes["frequency"] = ScheduleFrequency(call_data["frequency"])

    if "days" in call_data or "time" in call_data:
        start = getattr(template_options, "start", None)
        days = (
            sorted({_WEEKDAYS[d] for d in call_data["days"]})
            if "days" in call_data
            else list(getattr(start, "day", None) or [])
        )
        if "time" in call_data:
            hour, minute = call_data["time"].hour, call_data["time"].minute
        else:
            hour = getattr(start, "hour", None)
            minute = getattr(start, "min", None)
        if hour is None:
            raise ServiceValidationError("A time is required (template has none)")
        changes["start"] = ScheduleTime(day=days, hour=int(hour), min=int(minute or 0))

    commands = list(getattr(template_options, "commands", None) or [])
    if "rooms" in call_data:
        regions = _resolve_rooms(config_entry, call_data["rooms"], containers)
        commands = _set_regions(commands, regions)
    if "pad_wetness" in call_data:
        commands = _apply_wetness(commands, call_data["pad_wetness"])
    changes["commands"] = commands

    # Server-assigned; must be omitted on write (b9, field-confirmed).
    changes["created_time"] = None
    return replace(template_options, **changes)


async def _refresh(config_entry) -> None:
    """The user sees the result now, not at the next 15-minute tick."""
    coordinator = getattr(
        config_entry.runtime_data, "prime_schedule_coordinator", None
    )
    if coordinator is not None:
        await coordinator.async_request_refresh()


async def _read_containers_or_error(config_entry) -> list:
    containers = await async_read_schedule_containers(config_entry)
    if containers is None:
        raise ServiceValidationError(
            "Could not read schedules from the cloud — nothing was written"
        )
    return containers


async def _async_create(hass: HomeAssistant, call: ServiceCall) -> dict:
    config_entry, _entry = _prime_entry_for(hass, call.data["entity_id"])
    data = config_entry.runtime_data

    containers = await _read_containers_or_error(config_entry)
    template = None
    for _cid, schedules in containers:
        for schedule in schedules:
            if getattr(schedule.options, "enabled", None) is not None:
                template = schedule.options
                break
        if template:
            break
    if template is None:
        raise ServiceValidationError(
            "This household has no existing schedule to derive from — "
            "create one in the iRobot app first (a schedule built from "
            "literals is refused by the server; deriving is the only "
            "field-proven path)"
        )

    options = _reshaped_options(template, dict(call.data), containers, config_entry)
    if "name" not in call.data:
        options = replace(options, name=f"HA {call.data['time'].strftime('%H:%M')}")

    response = await data.prime_robot.create_schedules(
        data.prime_household_id, [options]
    )
    await _refresh(config_entry)
    created_id = (response or {}).get("household_schedule_id")
    _LOGGER.info("roomba_plus: created schedule %s", created_id)
    return {"household_schedule_id": created_id}


async def _async_update(hass: HomeAssistant, call: ServiceCall) -> dict:
    config_entry, entry = _prime_entry_for(hass, call.data["entity_id"])
    data = config_entry.runtime_data
    schedule_id = _schedule_id_from(entry, data.blid)

    # Same lock discipline as toggling: the read and the write stay
    # inseparable per container.
    containers = await _read_containers_or_error(config_entry)
    container_id = next(
        (
            cid for cid, schedules in containers
            if any(s.schedule_id == schedule_id for s in schedules)
        ),
        None,
    )
    if container_id is None:
        raise ServiceValidationError(
            f"Schedule {schedule_id} no longer exists on the robot"
        )

    async with _container_lock(config_entry.entry_id, container_id):
        containers = await _read_containers_or_error(config_entry)
        for cid, schedules in containers:
            if cid != container_id:
                continue
            updated: list[Any] = []
            found = False
            for schedule in schedules:
                if schedule.schedule_id == schedule_id:
                    options = _reshaped_options(
                        schedule.options, dict(call.data), containers, config_entry
                    )
                    updated.append(replace(schedule, options=options))
                    found = True
                else:
                    updated.append(schedule)
            if not found:
                raise ServiceValidationError(
                    f"Schedule {schedule_id} vanished between read and write"
                )
            await data.prime_robot.update_schedules(
                data.prime_household_id, container_id, updated
            )
            await _refresh(config_entry)
            return {"updated": schedule_id}
    raise ServiceValidationError(f"Container for {schedule_id} not found")


async def _async_delete(hass: HomeAssistant, call: ServiceCall) -> dict:
    config_entry, entry = _prime_entry_for(hass, call.data["entity_id"])
    data = config_entry.runtime_data
    schedule_id = _schedule_id_from(entry, data.blid)

    containers = await _read_containers_or_error(config_entry)
    target = next(
        (
            (cid, schedules) for cid, schedules in containers
            if any(s.schedule_id == schedule_id for s in schedules)
        ),
        None,
    )
    if target is None:
        raise ServiceValidationError(
            f"Schedule {schedule_id} no longer exists on the robot"
        )
    container_id, _schedules = target

    async with _container_lock(config_entry.entry_id, container_id):
        # CHANGED ON INTEGRATION: the sole-occupant decision is made from
        # a read taken INSIDE the lock.
        #
        # It used the pre-lock read, and that is the one place in this
        # file where the read and the write came apart. A schedule added
        # to the container between the two would have been deleted along
        # with the requested one, because the whole container goes when
        # it looks like a single occupant.
        #
        # Vanishingly unlikely -- every container observed so far holds
        # exactly one schedule -- and it is the same lost-update shape the
        # a18 fix exists for. Update already re-read inside the lock; this
        # brings delete in line.
        fresh = await _read_containers_or_error(config_entry)
        current = [
            s
            for cid, sched in fresh
            if cid == container_id
            for s in sched
        ]
        if not any(s.schedule_id == schedule_id for s in current):
            raise ServiceValidationError(
                f"Schedule {schedule_id} vanished between read and write"
            )
        if len(current) == 1:
            # Sole occupant: the per-container DELETE endpoint — the
            # field-proven path (the CLI's create/delete round-trip).
            await data.prime_robot.delete_schedule(
                data.prime_household_id, container_id
            )
        else:
            # Shared container: rewrite it without this schedule. Never
            # seen in the field (containers observed 1:1 so far), handled
            # so the first multi-schedule container isn't the first bug.
            remaining = [s for s in current if s.schedule_id != schedule_id]
            await data.prime_robot.update_schedules(
                data.prime_household_id, container_id, remaining
            )
    await _refresh(config_entry)
    _LOGGER.info("roomba_plus: deleted schedule %s", schedule_id)
    return {"deleted": schedule_id}


def async_register_prime_schedule_services(hass: HomeAssistant) -> None:
    """Registered from async_register_services(), removed with the rest."""

    async def handle_create(call: ServiceCall) -> dict:
        return await _async_create(hass, call)

    async def handle_update(call: ServiceCall) -> dict:
        return await _async_update(hass, call)

    async def handle_delete(call: ServiceCall) -> dict:
        return await _async_delete(hass, call)

    for name, handler, schema in (
        (SERVICE_CREATE_SCHEDULE, handle_create, _CREATE_SCHEMA),
        (SERVICE_UPDATE_SCHEDULE, handle_update, _UPDATE_SCHEMA),
        (SERVICE_DELETE_SCHEDULE, handle_delete, _DELETE_SCHEMA),
    ):
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                handler,
                schema=schema,
                supports_response=SupportsResponse.OPTIONAL,
            )
            _LOGGER.debug("Registered %s.%s action", DOMAIN, name)
