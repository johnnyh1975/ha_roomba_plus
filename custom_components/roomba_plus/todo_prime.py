"""The maintenance list for a Prime robot.

WHY THIS IS NOT THE CLASSIC LIST WITH A DIFFERENT SOURCE. Classic
derives everything from one number -- `bbrun.hr`, lifetime running hours
-- and a local store remembering when each part was last reset. Every
threshold is ours, guessed from iRobot's published intervals.

A Prime robot reports its parts individually, each with how much it has
used and how much is left, in whatever unit that part is counted in:
hours for a filter, missions for a brush, evacuations for a bag, pad
washes for a pad. **The robot does the arithmetic; we only present it.**

So this list has no thresholds of its own, and cannot be wrong about
one. What it can be wrong about is a part it does not recognise, and for
that it shows the part id rather than inventing a name.

WHAT "DONE" MEANS HERE, and it is worth being explicit. A maintenance
item is not a task somebody completes in Home Assistant -- the robot
decides when a part is fresh again, and it only learns that from a reset
performed on the robot or in the iRobot app. So items are read-only:
ticking one off in Home Assistant would record a claim the robot does
not share, and the next refresh would silently undo it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import IRobotEntity
from .sensor_prime import _KNOWN_PARTS
from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: `counter_category` decides the wording, because the app does the same:
#: it branches on RobotHealthCounterCategory and picks a different string
#: per category. `maintenance` means clean or service it; `replacement`
#: means fit a new one. Two verbs, not one.
_ACTION_BY_CATEGORY: dict[str, str] = {
    "maintenance": "Clean",
    "replacement": "Replace",
}

#: Units as the robot counts them, worded as **iRobot words them**.
#:
#: The app's own strings differ from the obvious translation of the enum
#: name, and the app is what the user reads beside this list:
#:
#:     evacsUnit           "Dust Collection Left"   not "evacuations"
#:     missionsUnit        "Tasks Left"             not "missions"
#:     comboMissionsUnit   "Routines Left"
#:     hoursUnit           "Hours Left"
#:
#: Somebody comparing the two should see the same words. A list saying
#: "60 evacuations remaining" beside an app saying "Dust Collection
#: Left" reads as two different measurements of two different things.
_UNITS: dict[str, str] = {
    "minutes": "hours",
    "missions": "tasks",
    "combo_missions": "routines",
    "evacs": "dust collections",
    "pad_washes_used": "pad washes",
    "battery": "charge cycles",
    "sqft": "ft²",
}



#: Part id -> the dock capability flag that has to be present for it to
#: mean anything. From `capabilityFromKey`, app 3.0.0.
_PART_REQUIRES_DOCK_CAP: dict[str, str] = {
    "147": "evac",   # dust bag -- needs an evacuating dock
    "202": "pw",     # pad wash cleaning
    "212": "pw",     # pad wash replacement
}


def _parts_the_robot_cannot_have(config_entry: Any) -> set[str]:
    """Parts whose dock capability the robot reports as absent.

    Explicit denial only. `dock.cap.evac == 0` means "this dock does not
    evacuate"; a missing block means the robot said nothing, and
    silence is not a denial.
    """
    data = getattr(config_entry, "runtime_data", None)
    coordinator = getattr(data, "prime_status_coordinator", None)
    shadows = getattr(coordinator, "data", None)
    if not isinstance(shadows, dict):
        return set()
    caps: dict[str, Any] = {}
    for body in shadows.values():
        dock = body.get("dock") if isinstance(body, dict) else None
        if isinstance(dock, dict) and isinstance(dock.get("cap"), dict):
            caps.update(dock["cap"])
    if not caps:
        return set()
    return {
        part_id
        for part_id, flag in _PART_REQUIRES_DOCK_CAP.items()
        if caps.get(flag) == 0
    }


def _readable_part_name(hass: Any, part_id: str) -> str:
    """The part's display name, or its id if we have none.

    Reads the same translation the part sensors use, so the list and the
    sensors agree. Falls back to the bare id rather than to a key --
    "Replace 68" is a question somebody can ask; "Replace
    prime_part_edge_brush" looks like a bug because it is one.
    """
    key = _KNOWN_PARTS.get(part_id)
    if not isinstance(key, str):
        return part_id
    try:
        from homeassistant.helpers.translation import (  # noqa: PLC0415
            async_get_cached_translations,
        )

        cached = async_get_cached_translations(hass, hass.config.language, "entity")
        name = cached.get(f"component.roomba_plus.entity.sensor.{key}.name")
    except Exception:  # noqa: BLE001
        name = None
    return name or key.replace("prime_part_", "").replace("_", " ").title()

def _describe(part: Any, name: str) -> tuple[str, str | None]:
    """An item's summary and description.

    The summary carries the verb and the part; the description carries
    what is left. Splitting them this way means a list read at a glance
    says WHAT to do, and a list read carefully says WHEN.
    """
    category = str(getattr(part, "counter_category", "") or "").lower()
    action = _ACTION_BY_CATEGORY.get(category, "Check")
    summary = f"{action} {name}"

    remaining = getattr(part, "count_remaining", None)
    used = getattr(part, "count_used", None)
    unit = _UNITS.get(str(getattr(part, "count_type", "") or "").lower())

    if remaining is None:
        # No estimate rather than a computed one. A part whose remaining
        # count the robot does not report is not a part at zero.
        return summary, (
            f"{used} {unit} used" if used is not None and unit else None
        )
    if unit:
        return summary, f"{remaining} {unit} remaining"
    return summary, f"{remaining} remaining"


def _needs_attention(part: Any) -> bool:
    """Whether the robot says this part is due.

    ZERO MEANS TWO OPPOSITE THINGS, depending on the category.

    On a `replacement` part it means used up. On a `maintenance` part it
    means **just done** -- the counter resets when the job is performed,
    so a freshly washed pad reads zero and needs nothing.

    @DaRealGuGu's robot made that plain. Two parts count the same 90 pad
    washes:

        212  replacement  count_remaining 210   the pad itself
        202  maintenance  count_remaining 0     the wash

    The pad has 210 washes of life left; the wash has zero *since the
    last one*. Reading both the same way put an item on his list while
    the iRobot app showed nothing due and the robot's own light ring was
    clear.

    The category was already being read -- for the verb, "Clean" versus
    "Replace". It decides this too, and did not.

    **HOW FAR THIS IS ESTABLISHED:** one account, one robot, and the app
    agreeing. Whether a `maintenance` counter ever climbs to signal a job
    that IS due is unknown, so nothing here treats it as due. Being quiet
    about a real job is recoverable; nagging about a clean pad is how a
    list gets ignored, and the items that mattered go with it.
    """
    category = str(getattr(part, "counter_category", "") or "").lower()
    if category == "maintenance":
        return False
    remaining = getattr(part, "count_remaining", None)
    return isinstance(remaining, (int, float)) and remaining <= 0


class PrimeMaintenanceTodo(IRobotEntity, TodoListEntity):
    """One item per consumable the robot reports."""

    _attr_translation_key = "maintenance"
    #: NO UPDATE_TODO_ITEM. See the module docstring: the robot owns
    #: whether a part is fresh, and a tick here would be a claim it does
    #: not share.
    _attr_supported_features = TodoListEntityFeature(0)

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_maintenance"

    @property
    def suggested_object_id(self) -> str:
        return "maintenance"

    @property
    def todo_items(self) -> list[TodoItem] | None:
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_parts_coordinator", None
        )
        parts = getattr(coordinator, "data", None)
        if not isinstance(parts, dict):
            return None

        items: list[TodoItem] = []
        absent = _parts_the_robot_cannot_have(self._config_entry)
        for part_id, part in sorted(parts.items()):
            # A DUST BAG ON A ROBOT WITH NO DOCK.
            #
            # @utkjmitch's dockless Combo 104 was told it had "60
            # evacuations remaining" for a bag it does not have, in a
            # station it does not own. The cloud reports the part
            # regardless of the hardware; the list should not repeat it.
            #
            # Only parts whose capability the robot explicitly denies are
            # hidden. A missing flag hides nothing -- a robot that did
            # not answer is not a robot without the part, and a
            # maintenance item wrongly hidden is worse than one wrongly
            # shown.
            if str(part_id) in absent:
                continue
            known = _KNOWN_PARTS.get(str(part_id))
            # The part id when the name is unknown -- ugly and honest.
            # Inventing "Part 202" would put a label on screen that
            # matches nothing in the iRobot app.
            name = known if isinstance(known, str) else str(part_id)
            summary, description = _describe(part, name)
            items.append(TodoItem(
                uid=f"part_{part_id}",
                summary=summary,
                description=description,
                status=(
                    TodoItemStatus.NEEDS_ACTION if _needs_attention(part)
                    else TodoItemStatus.COMPLETED
                ),
            ))
        return items


async def async_setup_prime_todo(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Adds the list, unconditionally.

    Not gated on the parts having arrived: a robot whose coordinator has
    not answered yet gets an empty list rather than no entity, and an
    entity that appears and disappears is worse than one that is briefly
    empty -- an automation pointing at a vanished entity fails for a
    reason unrelated to automations.
    """
    data = config_entry.runtime_data
    if getattr(data, "prime_robot", None) is None:
        return
    async_add_entities([PrimeMaintenanceTodo(data.blid, config_entry)])
