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
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def _async_add_prime_schedule_switches(
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One switch per schedule the robot actually has.

    Awaited during setup rather than created blind: the names come from
    the robot, and an entity with a placeholder name that later changes
    would leave a stale entity_id behind.

    A failure here adds no switches and is not fatal. Schedules are one
    feature among many, and taking down the whole switch platform for
    them would cost the user carpet boost and child lock too.
    """
    from .prime_schedule_switch import (  # noqa: PLC0415
        build_prime_schedule_switches,
    )

    coordinator = getattr(
        config_entry.runtime_data, "prime_schedule_coordinator", None
    )
    if coordinator is None:
        return

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:  # noqa: BLE001
        # Best-effort: a failure adds no switches now and the coordinator
        # will try again on its own cycle, at which point the listener
        # below adds them.
        _LOGGER.debug("roomba_plus: could not read schedules", exc_info=True)

    known: set[str] = set()

    @callback
    def _sync_entities() -> None:
        """Adds switches for schedules that appeared since the last run.

        WHY THIS IS NOT A ONE-OFF (@DaRealGuGu, a19 field test). a19
        added a coordinator so that a schedule toggled in the iRobot app
        reaches Home Assistant without a reload. It refreshes the STATE
        of switches that already exist, and entities were still built
        exactly once, here, at setup.

        His four-row test matrix showed what that leaves:

            create a schedule in the app  -> no switch until reload
            delete a schedule in the app -> switch goes unavailable and
                                            then stays forever

        MATCHED BY schedule_id, NEVER BY CONTENT. The order of
        `start.day` is not stable between two reads of the same
        unchanged schedule -- [4,3,1,2], then [1,2,4,3], then [3,1,2,4]
        across his captures. Comparing content would report a change on
        every refresh.
        """
        new_entities = [
            switch
            for switch in build_prime_schedule_switches(
                config_entry, coordinator.data or []
            )
            if switch.unique_id not in known
        ]
        if not new_entities:
            return
        known.update(str(switch.unique_id) for switch in new_entities)
        async_add_entities(new_entities)

    _sync_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_sync_entities))


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
            from .prime_coordinator import get_prime_capability_flags

            cap, _dock_cap = get_prime_capability_flags(config_entry)
            # NEW (this session): capability-gated -- see
            # get_prime_capability_flags()'s own docstring for the
            # "None means unknown, only explicit 0 means absent" contract.
            if cap is None or cap.carpet_boost != 0:
                async_add_entities([
                    PrimeCarpetBoostSwitch(data.blid, config_entry),
                ])

        # SETTING SWITCHES. Capability-gated on the same "None means
        # unknown, only explicit 0 means absent" contract the carpet
        # boost switch uses -- a robot that has not reported its
        # capabilities yet should get the switch, not lose it.
        setting_entities = [
            PrimeSettingSwitch(data.blid, config_entry, description)
            for description in PRIME_SETTING_SWITCHES
            if description.cap_attr is None
            or cap is None
            or getattr(cap, description.cap_attr, None) != 0
        ]
        if setting_entities:
            async_add_entities(setting_entities)

        # SCHEDULE SWITCHES, one per schedule on the robot.
        #
        # Reading schedules has worked since v4.0.0a5 (the calendar);
        # writing was field-confirmed twice and has sat in the version
        # plan as "confirmed, not wired" since. This is that wiring.
        #
        # Discovered rather than fixed: how many schedules exist, and
        # what they are called, is the user's business. A fixed set would
        # be wrong for everyone.
        await _async_add_prime_schedule_switches(
            config_entry, async_add_entities
        )
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
        IRobotEntity.__init__(self, roomba=None, blid=blid, config_entry=config_entry)
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

@dataclass(frozen=True, kw_only=True)
class PrimeSettingSwitchDescription(SwitchEntityDescription):
    """One rw-settings boolean, exposed as a switch."""

    #: The wire key set_setting() takes.
    wire_key: str
    #: The RobotSettings attribute the value is read back from. Named
    #: separately because the two differ: swScrub/scrub, langs2/
    #: languages_raw and others were renamed in the model, and assuming
    #: they match has produced false "field missing" reports before.
    model_attr: str
    #: cap flag that must not be explicitly 0. None means "always
    #: offer" -- see get_prime_capability_flags()'s own contract:
    #: unknown is not absent, only an explicit 0 is.
    cap_attr: str | None = None


#: Settings confirmed writable AND read-back on real hardware.
#:
#: The project rule is to never build on unconfirmed field names, and
#: the write-path test status records these four as write ✅ /
#: read-back ✅ -- meaning the robot echoed the new value, which is the
#: proof that it accepted it. childLock additionally has a confirmed
#: PHYSICAL effect: the robot announced it audibly.
#:
#: DELIBERATELY ABSENT: schedHold. Write and read-back both succeed and
#: the robot ignores it entirely -- a switch the robot accepts and does
#: nothing about is worse than no switch, because the UI would lie.
PRIME_SETTING_SWITCHES: tuple[PrimeSettingSwitchDescription, ...] = (
    PrimeSettingSwitchDescription(
        key="prime_child_lock",
        translation_key="prime_child_lock",
        wire_key="childLock",
        model_attr="child_lock",
        entity_category=EntityCategory.CONFIG,
    ),
    # NO CAPABILITY GATE, and that is correct rather than an omission.
    #
    # A tester reported "I'm pretty sure Eco Charging does not exist on
    # my device", which read like a missing check. An APK pass settled
    # it: the app's Capability enum has 113 entries -- SuctionLevel,
    # MultiCleaningPasses, PadPlateWetnessLevel, PadWashing, ChildLock
    # and so on -- and NOT ONE of them mentions Eco, Charge or Charging.
    #
    # The app cannot gate this setting either. It shows it to everyone,
    # and a robot without the feature ignores the write. Adding a check
    # here would mean inventing a capability the vendor does not model.
    PrimeSettingSwitchDescription(
        key="prime_eco_charge",
        translation_key="prime_eco_charge",
        wire_key="ecoCharge",
        model_attr="eco_charge",
        entity_category=EntityCategory.CONFIG,
    ),
    # THE ONLY AutoWash SETTING THAT IS SAFE TO OFFER, and the reason is
    # worth writing down because six sibling settings sit right next to
    # it in the same shadow and are NOT here.
    #
    # A tester asked for controls over the app's "Mop AutoWash Settings"
    # -- pad wash frequency and mop dry duration. The data is all in
    # rw-settings: padDryAllowed, padDryDur, padWashAllowed,
    # pwAreaInterval, pwTimeInterval, pwReturn, autoevacFreq.
    #
    # An APK pass found the obstacle. Of 37 Set*Command classes exactly
    # one covers this area: SetPadDryAllowCommand. Everything else goes
    # through SetAllRoombaPreferences with a RoombaPreferencesCommandValue
    # whose constructor takes TEN booleans -- the app writes these
    # settings as a BUNDLE. Which fields travel together is decided in
    # getPreferenceJsonString(), whose field names live in BSS constants
    # and are not statically readable.
    #
    # So a control that writes one of the bundled fields alone might be
    # accepted and do nothing. That is not hypothetical here: schedHold
    # is taken by the server and ignored.
    #
    # padDryAllowed has its own command, so it is written the way the
    # app writes it. The other six stay out until a field capture shows
    # whether one slider moves one field or several.
    PrimeSettingSwitchDescription(
        key="prime_pad_dry_allowed",
        translation_key="prime_pad_dry_allowed",
        wire_key="padDryAllowed",
        model_attr="pad_dry_allowed",
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSettingSwitchDescription(
        key="prime_two_pass",
        translation_key="prime_two_pass",
        wire_key="noAutoPasses",
        model_attr="no_auto_passes",
        cap_attr="multi_pass",
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSettingSwitchDescription(
        key="prime_vac_high",
        translation_key="prime_vac_high",
        wire_key="vacHigh",
        model_attr="vac_high",
        cap_attr="suction_lvl",
        entity_category=EntityCategory.CONFIG,
    ),
)


class PrimeSettingSwitch(IRobotEntity, SwitchEntity):
    """A boolean on the rw-settings shadow.

    Same mechanism PrimeCarpetBoostSwitch uses -- set_setting() to write,
    PrimeStatusCoordinator to read back -- generalised over a description
    rather than copied four times. Copying it would have meant four
    places to fix when the shadow name or the read path changes.
    """

    _attr_has_entity_name = True
    entity_description: PrimeSettingSwitchDescription

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        description: PrimeSettingSwitchDescription,
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug. has_entity_name plus translation_key
        otherwise makes HA derive the entity_id from the TRANSLATED name,
        producing different ids per language on first registration."""
        return self.entity_description.key

    @property
    def is_on(self) -> bool | None:
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None
        from roombapy_prime.models import RobotSettings  # noqa: PLC0415

        return getattr(
            RobotSettings.from_json(raw), self.entity_description.model_attr, None
        )

    @property
    def available(self) -> bool:
        """Unknown is not off.

        A setting whose value has never been read must not render as
        disabled -- someone would toggle it "on" and either write a value
        that was already set, or believe child lock was off when it was
        on.
        """
        return super().available and self.is_on is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        await robot.set_setting(self.entity_description.wire_key, value)
        # Optimistic, then corrected by the coordinator: the shadow
        # delta arrives within a second or two, and leaving the UI on the
        # old value until then reads as a failed command.
        self._attr_is_on = value
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self.async_write_ha_state)
            )

