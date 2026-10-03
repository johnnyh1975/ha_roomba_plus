"""Which Prime parts are due -- the rule, without the to-do platform.

Split out of `todo_prime.py` (4.2.19) so that the household API can ask
the same question. `todo_prime` imports the entity base, which imports
the package root, which imports `api_views`: the API could not import
the rule from where it lived without a cycle. Plain functions over the
runtime data, no Home Assistant imports.
"""
from __future__ import annotations

from typing import Any


#: Part id -> the dock capability flag that has to be present for it to
#: mean anything. From `capabilityFromKey`, app 3.0.0.
_PART_REQUIRES_DOCK_CAP: dict[str, str] = {
    "147": "evac",   # dust bag -- needs an evacuating dock
    "202": "pw",     # pad wash cleaning
    "212": "pw",     # pad wash replacement
}


def parts_the_robot_cannot_have(config_entry: Any) -> set[str]:
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


def needs_attention(part: Any) -> bool:
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


def prime_parts_due(config_entry: Any) -> list[str]:
    """Ids of the parts a Prime robot reports as due, sorted.

    THE MAINTENANCE LIST'S OWN RULE, so every place that asks "is
    maintenance due" on Prime gets the list's answer: `needs_attention`
    for what counts as due, minus the parts the robot's dock says it does
    not have. The household API read Classic's local hour counters for
    this, which a Prime robot never reports -- so a Prime robot was never
    due there, whatever its parts said.
    """
    coordinator = getattr(
        getattr(config_entry, "runtime_data", None), "prime_parts_coordinator", None
    )
    parts = getattr(coordinator, "data", None)
    if not isinstance(parts, dict):
        return []
    absent = parts_the_robot_cannot_have(config_entry)
    return sorted(
        str(part_id)
        for part_id, part in parts.items()
        if str(part_id) not in absent and needs_attention(part)
    )
