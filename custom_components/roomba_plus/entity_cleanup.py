"""Removing entities for things the robot no longer has.

WHY THIS EXISTS
---------------

Home Assistant registers an entity the first time it appears and keeps
the row forever. `restored: true` is what that looks like from the
outside: "I knew this once."

Nothing in this integration ever removed one. @ScenicSystemsLLC found
five stale favourite entities on each of three robots -- deleted in the
iRobot app, still listed in Home Assistant, needing manual removal. He
placed it correctly as the same class as the repair issues that
survived his reinstall.

Three platforms create entities from a list the cloud provides:
favourites, consumable parts, and the maintenance to-dos built from the
same parts.

WHAT MAKES THIS SAFE
--------------------

**Only ever from a list that arrived.** A robot that is offline reports
nothing, and concluding from silence that its entities are obsolete
would delete them on every disconnection.

So each caller passes the ids it actually received, and an empty list
removes nothing at all -- an empty response is indistinguishable from
a failed one, and the cost of guessing wrong is a user's dashboard.

The prefix scoping matters too: `_favorite_` removes only favourites,
`_prime_part_` only parts. A robot with no favourites must not lose its
sensors because the favourites list came back empty.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


@callback
def async_remove_stale_entities(
    hass: HomeAssistant,
    config_entry: Any,
    *,
    prefix: str,
    live_unique_ids: set[str],
) -> int:
    """Remove entities under `prefix` whose unique id is not live.

    `prefix` scopes the sweep to one kind of thing -- `_favorite_`,
    `_prime_part_`. `live_unique_ids` is what the cloud just reported.

    Returns the number removed, for the caller's log line.

    NOTHING IS REMOVED FOR AN EMPTY SET. A robot with no favourites and
    a favourites fetch that failed look identical from here, and the
    second must not delete the first's entities.
    """
    if not live_unique_ids:
        return 0

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(
        registry, config_entry.entry_id
    )

    removed = 0
    for entry in entries:
        unique_id = entry.unique_id or ""
        if prefix not in unique_id:
            continue
        if unique_id in live_unique_ids:
            continue
        _LOGGER.debug(
            "roomba_plus: removing %s -- %r is no longer reported",
            entry.entity_id, unique_id,
        )
        registry.async_remove(entry.entity_id)
        removed += 1

    if removed:
        _LOGGER.info(
            "roomba_plus: removed %d stale %s entit%s",
            removed, prefix.strip("_"), "y" if removed == 1 else "ies",
        )
    return removed
