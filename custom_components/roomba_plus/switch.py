"""Switch platform for Roomba+.

Binary on/off settings that map to set_preference() delta commands:

  EdgeCleanSwitch    — enable/disable edge cleaning along walls
  AlwaysFinishSwitch — continue cleaning even if bin is full (Clean Base models)
  ScheduleHoldSwitch — freeze the schedule without deleting it (e.g. during holidays)
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .entity import IRobotEntity
from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)

    entities: list[IRobotEntity] = []

    # Edge clean: present when openOnly key exists in state
    if "openOnly" in state:
        entities.append(EdgeCleanSwitch(roomba, blid))

    # Always finish: present when binPause key exists in state
    # (Clean Base models that support auto-evacuation mid-mission)
    if "binPause" in state:
        entities.append(AlwaysFinishSwitch(roomba, blid))

    # Schedule hold: present when schedHold key exists in state
    if "schedHold" in state:
        entities.append(ScheduleHoldSwitch(roomba, blid))

    async_add_entities(entities)


class EdgeCleanSwitch(IRobotEntity, SwitchEntity):
    """Switch that enables/disables cleaning along room edges and walls.

    The Roomba preference is called 'openOnly':
      openOnly=True  → edge cleaning OFF (robot avoids edges)
      openOnly=False → edge cleaning ON  (robot cleans edges)
    We invert this so the switch is ON when edge cleaning is active.
    """

    _attr_translation_key = "edge_clean"
    _attr_icon = "mdi:border-outside"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_edge_clean"

    @property
    def is_on(self) -> bool:
        """Return True when edge cleaning is enabled (openOnly is False)."""
        return not self.vacuum_state.get("openOnly", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable edge cleaning."""
        _LOGGER.debug("EdgeClean: turning ON (openOnly=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "openOnly", False
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable edge cleaning."""
        _LOGGER.debug("EdgeClean: turning OFF (openOnly=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "openOnly", True
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "openOnly" in new_state


class AlwaysFinishSwitch(IRobotEntity, SwitchEntity):
    """Switch that controls whether the Roomba finishes its mission when the bin is full.

    The Roomba preference is called 'binPause':
      binPause=True  -> robot PAUSES when bin is full (default without Clean Base)
      binPause=False -> robot CONTINUES (Clean Base empties the bin mid-mission)

    When ON (AlwaysFinish active), binPause=False — the robot never pauses for
    a full bin because the Clean Base will evacuate it automatically.

    Only created on models that report this preference (Clean Base models).
    """

    _attr_translation_key = "always_finish"
    _attr_icon = "mdi:flag-checkered"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_always_finish"

    @property
    def is_on(self) -> bool:
        """Return True when the robot will not pause for a full bin."""
        # binPause=False means the robot keeps going -> switch is ON
        return not self.vacuum_state.get("binPause", True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable always-finish mode (binPause=False)."""
        _LOGGER.debug("AlwaysFinish: turning ON (binPause=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "binPause", False
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable always-finish mode (binPause=True — pause when bin is full)."""
        _LOGGER.debug("AlwaysFinish: turning OFF (binPause=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "binPause", True
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "binPause" in new_state


class ScheduleHoldSwitch(IRobotEntity, SwitchEntity):
    """Switch that freezes the cleaning schedule without deleting it.

    The Roomba preference is called 'schedHold':
      schedHold=True  -> schedule is frozen (no automatic cleans)
      schedHold=False -> schedule is active (normal operation)

    Useful for holidays, having guests, or temporary situations where
    automatic cleaning should be suppressed without losing the schedule.

    Only created on models that report this preference.
    """

    _attr_translation_key = "schedule_hold"
    _attr_icon = "mdi:calendar-lock-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_schedule_hold"

    @property
    def is_on(self) -> bool:
        """Return True when the schedule is frozen."""
        return bool(self.vacuum_state.get("schedHold", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Freeze the schedule."""
        _LOGGER.debug("ScheduleHold: turning ON (schedHold=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "schedHold", True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unfreeze the schedule."""
        _LOGGER.debug("ScheduleHold: turning OFF (schedHold=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "schedHold", False
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "schedHold" in new_state
