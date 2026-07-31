"""Buttons for Prime robots: saved favourites, and locate.

WHY FAVOURITES ARE BUTTONS AND NOT A SELECT.

A favourite is a stored routine -- "clean the kitchen and hall on deep,
twice" -- and pressing it runs that. There is no state to hold and
nothing to choose between; a select would imply the robot is currently
"on" one of them, which it is not.

One button per favourite also means an automation can name the one it
wants, and a dashboard can show only the ones that matter.

WHAT ELSE THE PLATFORM COULD HOLD, AND DOES NOT.

Classic offers evacuate, power off, sleep, spot clean and map training.
Prime has a confirmed equivalent for exactly one of them: `find`, via
send_simple_command. The rest are not "not built yet" -- no command has
been identified for them, and a button that does nothing when pressed is
worse than an absent one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
from .entity import IRobotEntity

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


class PrimeFavoriteButton(IRobotEntity, ButtonEntity):
    """Runs one saved favourite.

    Identified by favorite_id rather than by position or name: a
    favourite renamed in the iRobot app must not break an automation, and
    one deleted must not shift every button after it onto a different
    routine.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        favorite_id: str,
        name: str,
    ) -> None:
        IRobotEntity.__init__(self, None, blid)
        self._config_entry = config_entry
        self._favorite_id = favorite_id
        self._attr_translation_key = "prime_favorite"
        self._attr_translation_placeholders = {"favorite": name or favorite_id}

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug, keyed on the id.

        has_entity_name plus a translation_key otherwise has HA derive
        the entity_id from the TRANSLATED name -- different ids per
        language on first registration, and a rename in the app would
        move it again.
        """
        return f"favorite_{self._favorite_id}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"favorite_id": self._favorite_id}

    async def async_press(self) -> None:
        """Runs the favourite, re-reading its commands first.

        NOT CACHED AT SETUP. A favourite edited in the iRobot app should
        run as edited, and Home Assistant may not have reloaded since.
        The read costs one request per press, which is the right trade
        against running a routine the user changed weeks ago.

        Shares the service's implementation, so a button press and a
        run_favorite call cannot diverge.
        """
        if not await async_run_favorite(self._config_entry, self._favorite_id):
            _LOGGER.warning(
                "roomba_plus: favorite %s could not be run -- deleted in the "
                "iRobot app, or carrying no commands",
                self._favorite_id,
            )


class PrimeLocateButton(IRobotEntity, ButtonEntity):
    """Makes the robot announce where it is.

    `find` is the one simple command confirmed for Prime. Classic's other
    buttons -- evacuate, power off, spot clean, map training -- have no
    identified Prime equivalent, so they are absent rather than
    non-functional.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "prime_locate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(self, None, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_locate"

    @property
    def suggested_object_id(self) -> str:
        return "locate"

    async def async_press(self) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is not None:
            await robot.send_simple_command("find")


async def async_build_prime_buttons(
    config_entry: RoombaConfigEntry,
) -> list[ButtonEntity]:
    """One button per favourite, plus locate.

    Favourites are read once at setup. They change rarely -- someone has
    to create one in the app -- and re-reading on every coordinator
    update would be a cloud call per battery percent.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    if robot is None:
        return []

    entities: list[ButtonEntity] = [PrimeLocateButton(data.blid, config_entry)]

    # Locate is always offered; favourite buttons are optional.
    #
    # They are the only route that needs no setup -- tappable right
    # after install, and usable by voice, which a service call is not.
    # They are also the only one costing an entity each, which is why
    # somebody with fifteen favourites can turn them off and use the
    # `favorites` attribute and run_favorite service instead.
    if not config_entry.options.get(
        CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
    ):
        return entities

    # ONE CLOUD READ, not three.
    #
    # Setup already fetched the favourites into runtime_data for the
    # vacuum attribute. Fetching them again here would mean two requests
    # for the same list within a second of each other, and a third every
    # time run_favorite is called.
    #
    # The commands are re-read per press instead, which is the right
    # place for a fresh look: a favourite edited in the app between
    # setup and the press should run as edited.
    for favorite in getattr(data, "prime_favorites", None) or []:
        entities.append(PrimeFavoriteButton(
            data.blid,
            config_entry,
            str(favorite["id"]),
            favorite.get("name") or "",
        ))

    return entities


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
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: could not read favorites", exc_info=True)
        return []

    return [
        {
            "id": str(getattr(f, "favorite_id", "")),
            "name": getattr(f, "name", "") or "",
        }
        for f in favorites or []
        if getattr(f, "favorite_id", None)
        and not getattr(f, "is_deleted", False)
        and not getattr(f, "is_hidden", False)
    ]


async def async_run_favorite(
    config_entry: RoombaConfigEntry, favorite_id: str
) -> bool:
    """Runs one favourite by ID. Returns whether anything was sent.

    BY ID, not by name. A name is what the user typed in the iRobot app
    and can change there at any time; an automation keyed on it breaks
    silently when it does.
    """
    robot = getattr(config_entry.runtime_data, "prime_robot", None)
    if robot is None:
        return False

    try:
        favorites = await robot.get_favorites()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: could not read favorites", exc_info=True)
        return False

    for favorite in favorites or []:
        if str(getattr(favorite, "favorite_id", "")) != str(favorite_id):
            continue
        commands = list(getattr(favorite, "command_defs", None) or [])
        if not commands:
            return False
        for command in commands:
            await robot.send_routine_command_via_cmd_topic(command)
        return True
    return False
