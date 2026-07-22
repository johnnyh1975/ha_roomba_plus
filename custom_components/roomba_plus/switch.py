"""Switch platform for Roomba+.

Binary on/off settings that map to set_preference() delta commands:

  EdgeCleanSwitch    — enable/disable edge cleaning along walls
  AlwaysFinishSwitch — continue cleaning even if bin is full (Clean Base models)
  ScheduleHoldSwitch — freeze the schedule without deleting it (e.g. during holidays)
  ChildLockSwitch    — lock the robot's physical control buttons
  EcoChargeSwitch    — enable/disable eco charging mode
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities."""
    data = config_entry.runtime_data

    # NEW (V4/Prime): separate path, same reasoning as binary_sensor.py's
    # own CLOUD_ONLY branch -- Prime data comes from PrimeStatusCoordinator's
    # named-shadow data, not roomba_reported_state()'s Classic shape.
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        if data.prime_status_coordinator is not None:
            async_add_entities([
                PrimeCarpetBoostSwitch(data.blid, config_entry),
            ])
        return

    roomba = data.roomba
    blid = data.blid
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

    # Child lock: present when childLock key exists in state
    if "childLock" in state:
        entities.append(ChildLockSwitch(roomba, blid))

    # Eco charge: present when ecoCharge key exists in state
    if "ecoCharge" in state:
        entities.append(EcoChargeSwitch(roomba, blid))

    # Gentle mode: present when gentle key exists in state (v3.4.3
    # GENTLE-MODE — confirmed stable across multiple i7 firmware
    # generations in real field data, analogous to EdgeCleanSwitch above)
    if "gentle" in state:
        entities.append(GentleModeSwitch(roomba, blid))

    async_add_entities(entities)


class EdgeCleanSwitch(IRobotEntity, SwitchEntity):
    """Switch that enables/disables cleaning along room edges and walls.

    The Roomba preference is called 'openOnly':
      openOnly=True  → edge cleaning OFF (robot avoids edges)
      openOnly=False → edge cleaning ON  (robot cleans edges)
    We invert this so the switch is ON when edge cleaning is active.
    """

    _attr_translation_key = "edge_clean"
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


class ChildLockSwitch(IRobotEntity, SwitchEntity):
    """Switch that locks the robot's physical control buttons.

    The Roomba preference is called 'childLock':
      childLock=True  -> physical buttons on the robot are locked
      childLock=False -> physical buttons work normally (default)

    Useful for households with kids or pets that might otherwise trigger
    the robot's onboard Clean button by accident.

    Only created on models that report this preference.
    """

    _attr_translation_key = "child_lock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_child_lock"

    @property
    def is_on(self) -> bool:
        """Return True when the physical buttons are locked."""
        return bool(self.vacuum_state.get("childLock", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the physical buttons."""
        _LOGGER.debug("ChildLock: turning ON (childLock=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "childLock", True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the physical buttons."""
        _LOGGER.debug("ChildLock: turning OFF (childLock=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "childLock", False
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "childLock" in new_state


class EcoChargeSwitch(IRobotEntity, SwitchEntity):
    """Switch that enables/disables the robot's eco charging mode.

    The Roomba preference is called 'ecoCharge':
      ecoCharge=True  -> eco charging active
      ecoCharge=False -> normal charging (default)

    Only created on models that report this preference.
    """

    _attr_translation_key = "eco_charge"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_eco_charge"

    @property
    def is_on(self) -> bool:
        """Return True when eco charging is active."""
        return bool(self.vacuum_state.get("ecoCharge", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable eco charging."""
        _LOGGER.debug("EcoCharge: turning ON (ecoCharge=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "ecoCharge", True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable eco charging."""
        _LOGGER.debug("EcoCharge: turning OFF (ecoCharge=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "ecoCharge", False
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "ecoCharge" in new_state


class GentleModeSwitch(IRobotEntity, SwitchEntity):
    """Switch that enables/disables the robot's gentle cleaning mode.

    The Roomba preference is called 'gentle':
      gentle=True  -> gentle mode active (reduced vacuum/brush aggressiveness)
      gentle=False -> normal cleaning (default)

    v3.4.3 GENTLE-MODE — confirmed stable across multiple i7 firmware
    generations in real field data (see CLASSIC_APK_ANALYSIS_FINDINGS.md),
    never implemented despite that stability. Same shape as EcoChargeSwitch
    above — a plain preference boolean, no inversion.

    Only created on models that report this preference.
    """

    _attr_translation_key = "gentle_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_gentle_mode"

    @property
    def is_on(self) -> bool:
        """Return True when gentle mode is active."""
        return bool(self.vacuum_state.get("gentle", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable gentle mode."""
        _LOGGER.debug("GentleMode: turning ON (gentle=True)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "gentle", True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable gentle mode."""
        _LOGGER.debug("GentleMode: turning OFF (gentle=False)")
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "gentle", False
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "gentle" in new_state


class PrimeCarpetBoostSwitch(IRobotEntity, SwitchEntity):
    """V4/Prime carpet boost toggle -- reads/writes RobotSettings.carpet_boost
    (wire key "carpetBoost") on the named shadow "rw-settings", via
    roombapy-prime's own set_setting()/PrimeStatusCoordinator.

    carpet_boost is a real, sensor-driven, real-time "boost suction
    when the robot detects carpet" feature (confirmed via iRobot's own
    public product documentation) -- NOT a three-way Auto/Performance/
    Eco selector (that concept, CarpetBoostSettings, is confirmed dead
    code in the app itself -- see that enum's own docstring in
    roombapy-prime's models/mission_control.py). This switch only
    toggles the feature on/off; the robot's own sensors decide when to
    actually apply the boost.

    WRITE MECHANISM CONFIRMED, EFFECT NOT YET CONFIRMED: the generic
    shadow-write this relies on (set_setting(), the same mechanism
    trigger_echo_via_shadow() already confirmed works at the transport
    level) is known to produce a real, accepted response -- but whether
    toggling THIS specific field actually changes the robot's real
    carpet-boost behavior hasn't been confirmed the way locate's own
    working mechanism eventually was. Treat a successful toggle here as
    "the write went through", not yet as "confirmed working" the way
    start/stop/dock/find are."""

    entity_description = SwitchEntityDescription(
        key="prime_carpet_boost",
        translation_key="prime_carpet_boost",
    )
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(self, roomba=None, blid=blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_carpet_boost"

    @property
    def _prime_robot(self):
        return self._config_entry.runtime_data.prime_robot

    @property
    def is_on(self) -> bool | None:
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None
        from roombapy_prime.models import RobotSettings

        return RobotSettings.from_json(raw).carpet_boost

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._prime_robot.set_setting("carpetBoost", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._prime_robot.set_setting("carpetBoost", False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(coordinator.async_add_listener(self.schedule_update_ha_state))
