"""The Prime favourites list, outside the button platform (4.5.0).

`prime_coordinator` reads the list when the account's favourites change,
and `button_prime` builds buttons from it -- and `button_prime` imports
`prime_coordinator`. With the list in `button_prime`, the coordinator
could only reach it through a late import inside a method: an import
cycle held apart by where the import stood. Here it imports nothing that
imports either of them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .structural_failures import record_failure, record_success

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


def _raw_favorite_is_for(favorite: dict[str, Any], blid: str) -> bool:
    """Whether a raw favourite dict belongs to this robot.

    Same contract as `_favorite_is_for`, reading the wire keys: a
    favourite whose command defs carry no `robot_id` at all is kept,
    because only an explicit non-matching id excludes.

    Held to the same rule deliberately -- one behaviour, two shapes,
    and the parsed path is what the attribute uses.
    """
    attributed = [
        str(command.get("robot_id") or "")
        for command in (favorite.get("commanddefs")
                        or favorite.get("command_defs") or [])
        if isinstance(command, dict)
    ]
    attributed = [value for value in attributed if value]
    if not attributed:
        return True
    return blid in attributed


#: The (unusable, total) favourite counts last warned about, by config
#: entry -- see async_favorites_attribute().
_UNUSABLE_WARNED: dict[str, tuple[int, int]] = {}


def _is_internal_favorite_name(name: str) -> bool:
    """Whether a favourite name is a firmware-internal id rather than
    something a user named in the app.

    Confirmed case: `_mission_init_config_9A37307A20804F0CABE9B6011B82DDBE`.
    Every such name starts with an underscore, and no favourite in this
    project's fixtures or reports ever has -- iRobot's own app does not
    let a user start a routine name that way -- so the check is the
    general prefix rather than a match on that one string.
    """
    return name.startswith("_")


def _favorite_is_for(favorite: Any, blid: str) -> bool:
    """Whether this account-level favourite belongs to this robot.

    `GET /v1/user/favorites` IS ACCOUNT-LEVEL, NOT PER ROBOT, and every
    config entry was building a button for every favourite on the
    account. A two-robot household got both robots' favourites listed
    under each of them.

    Reported by @scenicsystemsllc, whose severity analysis is the part
    worth keeping: pressing a mislabelled button commands the robot it
    is displayed under -- correctly, over that robot's own connection --
    but with a payload built from a DIFFERENT robot's map. Usually that
    no-ops or errors. In the worst case a region id happens to be valid
    on the receiving robot's map too, and it cleans somewhere nobody
    asked for.

    THE FAVOURITE SAYS WHICH ROBOT IT IS FOR. Each `commanddefs` entry
    carries `robot_id` -- `RoutineCommand.asset_id` here, same field,
    and `mission_control.py` writes it back out under the wire name. So
    this is a direct match rather than the region-id heuristic the
    report suggested: comparing against a robot's pmap would also work
    but infers from geometry what the record states outright.

    FAILS OPEN. A favourite whose command defs carry no robot id at all
    is kept, because a favourite nobody can attribute is better shown
    under every robot than hidden from all of them -- the same contract
    the capability gates use. Only an explicit, non-matching id
    excludes.
    """
    commands = getattr(favorite, "command_defs", None) or []
    attributed = [
        str(getattr(command, "asset_id", "") or "")
        for command in commands
    ]
    attributed = [value for value in attributed if value]
    if not attributed:
        return True
    return blid in attributed


async def async_favorites_attribute(
    config_entry: RoombaConfigEntry,
) -> list[dict[str, Any]]:
    """The favourites list, for the vacuum entity's attributes.

    Costs no entity and answers the cases buttons cannot: an automation
    that iterates, a template that lists them, and the
    xiaomi-vacuum-map-card's menu, which reads attributes.

    Carries the ID alongside the name deliberately. An automation
    written against the ID survives a rename in the iRobot app; one
    written against the name does not, and the name is the only thing a
    button or a select could offer.
    """
    robot = getattr(config_entry.runtime_data, "prime_robot", None)
    if robot is None:
        return []
    try:
        favorites = await robot.get_favorites()
        record_success("favourite list")
    except Exception:  # noqa: BLE001
        # A key-name mismatch dropped every favourite for weeks while
        # this reported an empty list. An empty account and a broken
        # parser looked identical.
        record_failure("favourite list", "reading favourites")
        _LOGGER.debug("roomba_plus: could not read favorites", exc_info=True)
        return []

    kept: list[dict[str, Any]] = []
    unusable = 0
    expected: dict[str, int] = {}
    for f in favorites or []:
        name = getattr(f, "name", "") or ""
        if not getattr(f, "favorite_id", None):
            unusable += 1
            continue
        reason = (
            "deleted" if getattr(f, "is_deleted", False)
            else "hidden" if getattr(f, "is_hidden", False)
            else "internal" if _is_internal_favorite_name(name)
            else "other robot" if not _favorite_is_for(f, config_entry.runtime_data.blid)
            else None
        )
        if reason is not None:
            expected[reason] = expected.get(reason, 0) + 1
            continue
        kept.append({"id": str(f.favorite_id), "name": name})

    # SAY WHAT WAS DROPPED, because silence here is what made this bug
    # unfindable for weeks.
    #
    # A favourite with no parseable id fails the first condition and
    # vanishes. Deleted and hidden ones are meant to vanish. All three
    # produced the same visible result -- no buttons -- and the only
    # difference between "your account has none" and "seven arrived and
    # none had an id" was invisible.
    #
    # @chairstacker has seven favourites and saw no buttons across
    # several releases. Each fix along the way was plausible and none of
    # them could be confirmed or ruled out, because the log said nothing
    # either way.
    #
    # ONLY THE UNEXPECTED, AND ONCE (4.5.4b3, @Baby-scarf). The warning
    # counted every drop, and the list is read every fifteen minutes: on
    # an account with two Prime robots each robot warned about the
    # other's favourite, 22 times in five hours, for a favourite in
    # perfect order. A favourite without an id is the parser question the
    # warning exists for; it is a warning, once per robot until the count
    # changes. Deleted, hidden, internal and the other robots' are the
    # filter working, and are logged at debug.
    total = len(favorites or [])
    if expected:
        _LOGGER.debug(
            "Roomba+ Prime: favourites not offered here, as intended: %s",
            ", ".join(f"{count} {why}" for why, count in sorted(expected.items())),
        )
    entry_id = getattr(config_entry, "entry_id", "")
    if unusable and _UNUSABLE_WARNED.get(entry_id) != (unusable, total):
        _LOGGER.warning(
            "Roomba+ Prime: %d of %d favourite(s) carry no usable id and were "
            "not offered as buttons. Enable debug logging for roombapy_prime "
            "to see which",
            unusable,
            total,
        )
    _UNUSABLE_WARNED[entry_id] = (unusable, total)
    return kept
