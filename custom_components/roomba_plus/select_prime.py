"""Multi-step Prime settings, as select entities.

WHY SELECTS AND NOT MORE SWITCHES.

The four setting switches cover the booleans -- child lock, eco charging,
two-pass, extra suction. Suction LEVEL and pad wetness are graduated, and
a switch cannot express three or four steps.

It also lights up something in the xiaomi-vacuum-map-card: its menu icons
read a select entity's `options` attribute and offer them as a map-side
menu. So a select is not just the right entity type, it is the one that
card knows how to use.

WHAT IS OFFERED AND WHAT IS NOT.

`suctionLevel` is offered. Real values 2, 3 and 4 appear in two testers'
per-room operating-mode defaults, mapped to the profiles the app calls
light, normal and deep. 1 and 0 have never been observed, so they are not
offered: a level the robot rejects would fail silently, and a level that
means something else entirely would be worse.

`padWetness` is NOT offered, and that is the more interesting decision.
It is not a single value -- the model is three separate levels, one per
pad type (`disposable`, `pad_plate`, `reusable`). Which one applies
depends on the pad currently fitted, and the robot decides that. Exposing
one select that writes "the wetness" would mean guessing which of the
three the user meant.

Classic sidesteps this by offering two selects, one per pad type, because
its wire format has two separate fields. Prime's has three and a
fitted-pad concept on top. Until somebody establishes how the robot picks
between them, a select here would be a control whose effect depends on
hardware state the user cannot see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import IRobotEntity

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Suction levels, keyed by the value the robot uses.
#:
#: CONFIRMED from two independent captures of `operating_mode_defaults`,
#: where each room stores a suctionLevel alongside a profile name:
#:
#:     2 -> "light"    3 -> "normal"    4 -> "deep"
#:
#: The option strings are the profile names rather than the numbers,
#: because that is what the iRobot app shows and what a user recognises.
#: Automations should target them by name for the same reason.
SUCTION_LEVELS: dict[int, str] = {2: "light", 3: "normal", 4: "deep"}


@dataclass(frozen=True, kw_only=True)
class PrimeSelectDescription(SelectEntityDescription):
    """One graduated rw-settings value."""

    #: The wire key set_setting() takes.
    wire_key: str
    #: The RobotSettings attribute the value reads back from. Named
    #: separately because the two differ, and assuming they match has
    #: produced false "field missing" reports in this project before.
    model_attr: str
    #: Robot value -> option string.
    values: dict[int, str]
    #: cap flag that must not be explicitly 0. None means always offer --
    #: unknown is not absent, only an explicit 0 is.
    cap_attr: str | None = None


PRIME_SELECTS: tuple[PrimeSelectDescription, ...] = (
    PrimeSelectDescription(
        key="prime_suction_level",
        translation_key="prime_suction_level",
        wire_key="suctionLevel",
        model_attr="suction_level",
        values=SUCTION_LEVELS,
        cap_attr="suction_lvl",
        entity_category=EntityCategory.CONFIG,
    ),
)


class PrimeSettingSelect(IRobotEntity, SelectEntity):
    """A graduated setting on the rw-settings shadow.

    Same mechanism as the setting switches: set_setting() to write,
    PrimeStatusCoordinator to read back.
    """

    _attr_has_entity_name = True
    entity_description: PrimeSelectDescription

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        description: PrimeSelectDescription,
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"
        self._attr_options = list(description.values.values())

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug. has_entity_name plus a
        translation_key otherwise has HA derive the entity_id from the
        TRANSLATED name, producing different ids per language."""
        return self.entity_description.key

    @property
    def current_option(self) -> str | None:
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None

        from roombapy_prime.models import RobotSettings  # noqa: PLC0415

        value = getattr(
            RobotSettings.from_json(raw), self.entity_description.model_attr, None
        )
        if value is None:
            return None
        # A value outside the known set is reported as None rather than
        # guessed at. The robot may support levels nobody has observed,
        # and showing "normal" for an unknown number would be a lie the
        # user cannot detect.
        return self.entity_description.values.get(int(value))

    @property
    def available(self) -> bool:
        """Unknown is not a default.

        A setting whose value has never been read must not render as
        whatever happens to be first in the list -- someone would see
        "light" and believe that is what the robot is set to.
        """
        return super().available and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        wire_value = next(
            (v for v, name in self.entity_description.values.items() if name == option),
            None,
        )
        if wire_value is None:
            _LOGGER.warning(
                "roomba_plus: %r is not a known option for %s",
                option, self.entity_description.key,
            )
            return
        await robot.set_setting(self.entity_description.wire_key, wire_value)
        # Optimistic, then corrected by the coordinator: the shadow delta
        # arrives within a second or two, and leaving the UI on the old
        # value until then reads as a failed command.
        self._attr_current_option = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self.async_write_ha_state)
            )


class PrimeMapSelect(IRobotEntity, RestoreEntity, SelectEntity):
    """Which of several maps the Rooms Map image shows.

    WHY A SELECT AND NOT ONE IMAGE PER MAP. The other design looks better
    on paper -- two dashboards could show different floors -- and the
    person who would live with it asked for this one instead
    (@chairstacker, issue #45). He has two maps, uses one constantly and
    the other rarely, and an entity per map gives no way to keep the rare
    one off a dashboard at all.

    A user with two equally-used floors would answer differently. Nobody
    is asking for that, and the design can change when somebody does.

    "Follow the robot" IS THE DEFAULT and stays the default. A user who
    never touches this sees exactly what they saw before it existed: the
    map the robot reports standing on, falling back to the first.

    ONE ENTITY EVEN WITH ONE MAP. Gating it on map count would make the
    entity appear and disappear as maps are added and removed in the app,
    which is worse than a select with a single option -- an automation
    referencing a vanished entity fails loudly for a reason that has
    nothing to do with automations.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "prime_map"
    _attr_icon = "mdi:layers-outline"

    #: Not a map id, so it cannot collide with one.
    FOLLOW_ROBOT = "follow_robot"

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        # Same call shape as PrimeSettingSelect: roomba=None, because a
        # Prime robot has no roombapy object behind it.
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_map"
        self._names: dict[str, str] = {}

    @property
    def suggested_object_id(self) -> str:
        return "prime_map"

    @property
    def options(self) -> list[str]:
        """Map names, with "follow the robot" first.

        Names rather than ids: `hh_..._1752720067` tells a user nothing.
        A map with no name falls back to its id, which is ugly and
        honest -- inventing "Map 2" would put a label on screen that
        matches nothing in the iRobot app.
        """
        return [self.FOLLOW_ROBOT, *self._names.values()]

    @property
    def current_option(self) -> str | None:
        selected = getattr(
            self._config_entry.runtime_data, "prime_selected_map_id", None
        )
        if selected is None:
            return self.FOLLOW_ROBOT
        # A map deleted in the app leaves a selection pointing at
        # nothing. Falling back to following the robot is better than
        # reporting an option that is not in the list, which Home
        # Assistant logs as an error on every state write.
        return self._names.get(selected, self.FOLLOW_ROBOT)

    async def async_select_option(self, option: str) -> None:
        data = self._config_entry.runtime_data
        if option == self.FOLLOW_ROBOT:
            data.prime_selected_map_id = None
        else:
            match = next(
                (mid for mid, name in self._names.items() if name == option), None
            )
            if match is None:
                raise ServiceValidationError(
                    f"{option} is not one of this account's maps"
                )
            data.prime_selected_map_id = match

        # The image caches its render against a map id, so it notices the
        # change on the next request without being told. Writing state
        # here is what moves the select itself.
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # RESTORED, NOT PERSISTED IN OPTIONS. Writing the choice into the
        # config entry would reload the integration on every change,
        # which is a heavy price for a dropdown.
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            self._restore = last.state
        await self._async_load_names()

    async def _async_load_names(self) -> None:
        """Map ids to display names, once per setup.

        Map names change when somebody renames one in the app, which is
        rare enough that a cloud call per image request would be the
        wrong trade -- the same reasoning the rooms map itself uses for
        its version check.
        """
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        try:
            versions = await robot.get_active_map_versions()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: could not read map names for %s -- the select "
                "will offer only 'follow the robot'", self._blid, exc_info=True,
            )
            return

        names: dict[str, str] = {}
        for entry in versions or []:
            if not isinstance(entry, dict):
                continue
            map_id = entry.get("p2map_id")
            if not map_id:
                continue
            names[str(map_id)] = str(entry.get("name") or map_id)
        self._names = names

        restore = getattr(self, "_restore", None)
        if restore and restore != self.FOLLOW_ROBOT:
            match = next((m for m, n in names.items() if n == restore), None)
            if match is not None:
                self._config_entry.runtime_data.prime_selected_map_id = match
        self.async_write_ha_state()
