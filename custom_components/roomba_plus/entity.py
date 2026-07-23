"""Base entity class for Roomba+ integration."""
from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_CONNECTIONS
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import roomba_reported_state
from .const import DOMAIN

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


class IRobotEntity(Entity):
    """Base class for all iRobot entities in Roomba+.

    Provides:
    - Device info construction from master_state
    - Convenience properties for commonly accessed state sub-dicts
    - MQTT callback registration via roombapy
    - Proper unique_id and has_entity_name wiring

    All entity updates are driven by the MQTT push callback from roombapy —
    there is no polling. Subclasses override on_message() and/or
    new_state_filter() to react only to relevant state changes.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, roomba: Any, blid: str, config_entry: "RoombaConfigEntry | None" = None) -> None:
        """Initialise the entity with the roombapy Roomba object and BLID.

        config_entry is optional and, before this session, was never
        actually used here at all -- every Prime entity passes
        roomba=None (there is no roombapy Roomba object for a
        cloud-only device) and stored its OWN config_entry separately,
        AFTER calling this __init__, meaning this method always built
        DeviceInfo from roomba_reported_state(None) == {} for every
        single Prime entity.

        REAL BUG THIS FIXES (found during an architecture review, not
        a field report -- no test or entity behavior surfaced it,
        since every individual Prime entity's OWN sensor/switch state
        looked completely correct; only the DEVICE PAGE itself, a
        separate part of the HA UI few of this project's own tests
        ever touch, was affected): every Prime robot's device showed
        up in Settings -> Devices with a generic "Roomba XXXX" name
        (last 4 chars of the BLID, from _resolve_name({}, blid)'s own
        fallback), no model, no serial number, and no firmware version
        -- despite PrimeFirmwareVersionSensor and others already
        showing the SAME underlying data correctly as individual
        sensors. The device-level info and the sensor-level info come
        from entirely separate code paths, and only the sensor one was
        ever fixed.

        Now, when config_entry is provided and roomba is None (the
        Prime case), builds DeviceInfo from three sources instead:
          - name: config_entry.title, which has ALWAYS correctly held
            the real robot name (or blid fallback) since this
            project's very first Prime release -- config_flow.py's own
            _async_create_prime_entry() sets this at onboarding time,
            for every entry, old or new, so no migration is needed for
            already-configured installs.
          - model/model_id/serial_number: config_entry.runtime_data.
            prime_serial_info (RobotSerialInfo, from
            get_serial_number_data() -- best-effort, fetched once
            during setup; None for any entry where that fetch failed
            or hasn't happened yet, same graceful-degradation
            reasoning as prime_household_id).
          - sw_version: config_entry.runtime_data.
            prime_status_coordinator.data's own "rw-software" shadow
            content (already flowing for every Prime entity's other
            sensors; no new fetch needed here).
        """
        self.vacuum = roomba
        self._blid = blid
        self.vacuum_state = roomba_reported_state(roomba)

        if roomba is None and config_entry is not None:
            self._attr_device_info = self._build_prime_device_info(blid, config_entry)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, self.robot_unique_id)},
                serial_number=(
                    (self.vacuum_state.get("hwPartsRev") or {}).get("navSerialNo")
                ),
                manufacturer="iRobot",
                model=self.vacuum_state.get("sku"),
                model_id=self.vacuum_state.get("sku"),   # IA74-MI: full SKU in device registry
                name=self._resolve_name(self.vacuum_state, blid),
                sw_version=self.vacuum_state.get("softwareVer"),
                hw_version=str(self.vacuum_state.get("hardwareRev", "")),
            )

        # Add MAC address connection if available.
        # NOTE: do NOT let the MAC become the device name — HA picks up the
        # DeviceInfo name field, not the connection tuple, so this is safe as
        # long as `name` above is set correctly.
        mac_address: str | None = (
            (self.vacuum_state.get("hwPartsRev") or {}).get("wlan0HwAddr")
            or self.vacuum_state.get("mac")
        )
        if mac_address:
            self._attr_device_info[ATTR_CONNECTIONS] = {
                (dr.CONNECTION_NETWORK_MAC, mac_address)
            }

    def _build_prime_device_info(self, blid: str, config_entry: "RoombaConfigEntry") -> DeviceInfo:
        """See __init__'s own docstring for the full reasoning. Every
        field here is best-effort/optional -- a still-generic name or
        missing model/serial is a real, visible gap, but never a
        reason to fail entity setup outright."""
        data = config_entry.runtime_data
        serial_info = getattr(data, "prime_serial_info", None)
        status_coordinator = getattr(data, "prime_status_coordinator", None)
        coordinator_data = getattr(status_coordinator, "data", None) or {}
        software_shadow = coordinator_data.get("rw-software") or {}

        return DeviceInfo(
            identifiers={(DOMAIN, self.robot_unique_id)},
            serial_number=getattr(serial_info, "serial_number", None),
            manufacturer="iRobot",
            model=getattr(serial_info, "sku", None) or getattr(serial_info, "family", None),
            model_id=getattr(serial_info, "sku", None),
            name=config_entry.title or f"Roomba {blid[-4:]}",
            sw_version=software_shadow.get("softwareVer"),
        )

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def robot_unique_id(self) -> str:
        """Stable device identifier based on BLID."""
        return f"roomba_plus_{self._blid}"

    @property
    def suggested_object_id(self) -> str | None:
        """Return a locale-independent entity_id suffix for all subclasses.

        HA derives the entity_id at first registration from slugify(entity.name),
        which is the *translated* name in the user's locale.  On a DE-locale
        install this produces German slugs (e.g. 'akkualter' instead of
        'battery_age_days'), making entity_ids installation-locale-dependent and
        impossible to document or reference reliably.

        All Roomba+ entities follow the pattern:
            _attr_unique_id = f"{self.robot_unique_id}_{english_key}"

        This property strips the robot-specific prefix and returns the English
        key, which HA then uses as the suggested entity_id suffix regardless of
        the user's locale.  Subclasses that use EntityDescription should
        override this to return ``self.entity_description.key`` directly.

        NAMING CONVENTION (enforced by test_locale_slug_guard):
        - NEVER set ``_attr_name`` alongside ``_attr_translation_key`` on a class.
        - ALWAYS set ``_attr_unique_id = f"{self.robot_unique_id}_{english_key}"``.
        - The English key must match the translation file key exactly.
        """
        uid: str | None = getattr(self, "_attr_unique_id", None)
        if uid:
            prefix = f"{self.robot_unique_id}_"
            if uid.startswith(prefix):
                return uid[len(prefix):]
        return None

    # ── State sub-dict properties ─────────────────────────────────────────────

    @property
    def run_stats(self) -> dict[str, Any]:
        """Lifetime run statistics — merged from runtimeStats (i/s/j) and bbrun (900-series).

        On i/s/j-series (lewis firmware) hr, sqft, and min live in the separate
        runtimeStats MQTT key. On 900-series they appear in bbrun directly.
        Merging both sources with runtimeStats taking priority gives a single
        consistent interface for all sensor code regardless of firmware variant.

        Event counters (nPanics, nCliffsF, nScrubs, nStuck etc.) come exclusively
        from bbrun and are unaffected by the merge.
        """
        bbrun = self.vacuum_state.get("bbrun", {})
        runtime = self.vacuum_state.get("runtimeStats", {})
        return {**bbrun, **runtime}  # runtimeStats wins on key collision (hr, sqft, min)

    @property
    def mission_stats(self) -> dict[str, Any]:
        """Lifetime mission statistics (bbmssn)."""
        return self.vacuum_state.get("bbmssn", {})

    @property
    def nav_stats(self) -> dict[str, Any]:
        """Navigation subsystem statistics (bbnav).

        Available on 9-series (980/960/900) firmware. Contains map tracking
        quality (aMtrack) and landmark counts (nGoodLmrks) used by L9-MAP.
        Returns empty dict on i/s/j-series firmware where bbnav is absent.
        """
        return self.vacuum_state.get("bbnav") or {}

    @property
    def battery_stats(self) -> dict[str, Any]:
        """Battery charge cycle statistics (bbchg3)."""
        return self.vacuum_state.get("bbchg3", {})

    @property
    def dock_stats(self) -> dict[str, Any]:
        """Dock charging session statistics (bbchg).

        Distinct from bbchg3 (battery cycle statistics).
        Contains dock contact health counters:
          nChatters  — contact bounce events (worn contacts)
          nKnockoffs — unintended undocking events
          nAborts    — aborted charging sessions
          smberr     — SMBus communication errors (already used by SMBERR repair)
        """
        return self.vacuum_state.get("bbchg") or {}

    @property
    def clean_mission_status(self) -> dict[str, Any]:
        """Current mission status."""
        return self.vacuum_state.get("cleanMissionStatus", {})

    @property
    def tank_level(self) -> int | None:
        """Tank fill level (Braava only)."""
        return self.vacuum_state.get("tankLvl")

    @property
    def dock_tank_level(self) -> int | None:
        """Dock tank fill level."""
        return (self.vacuum_state.get("dock") or {}).get("tankLvl")

    @property
    def last_mission(self) -> Any | None:
        """Start time of the current or last mission from live MQTT state, or None.

        Returns None when mssnStrtTm is 0 (robot on dock, 900-series firmware).
        The last_mission sensor uses MissionStore instead of this property to
        avoid the permanent-None problem on 900-series — this property is kept
        for other uses (e.g. mission duration display during active missions).
        """
        ts = self.clean_mission_status.get("mssnStrtTm")
        if ts is None or ts == 0:
            return None
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)

    # ── Push update wiring ────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register the MQTT message callback and fix the device name.

        At __init__ time the vacuum_state snapshot may be sparse (the robot
        hasn't finished its full state dump yet). By the time async_added_to_hass
        runs, the 2 s wait in async_connect_or_timeout has passed and the full
        state is available.

        Critically: updating _attr_device_info alone is NOT enough to rename
        the device — HA only reads DeviceInfo on the first registry write.
        Subsequent renames must go through dr.async_update_device() directly.

        BUG FOUND (bug-hunt round, V4/Prime): self.vacuum is None for a
        CLOUD_ONLY entity -- this method is called unconditionally by HA
        for every entity built on IRobotEntity, so a Prime vacuum entity
        crashed immediately on being added to Home Assistant (AttributeError
        on None.register_on_message_callback), the very first time HA's own
        entity lifecycle touched it. Guarded below; the vacuum_state refresh
        is skipped too since roomba_reported_state(None) would just
        re-derive the same {} __init__ already set.
        """
        if self.vacuum is not None:
            self.vacuum.register_on_message_callback(self.on_message)
            # Refresh snapshot — full state available now
            self.vacuum_state = roomba_reported_state(self.vacuum)
        # Patch DeviceInfo and the live DeviceRegistry entry
        await self._async_update_device_name()
        # Force a state write so sensors don't show 'unavailable' on first render
        self.schedule_update_ha_state()

    async def _async_update_device_name(self) -> None:
        """Resolve the robot's name and write it to the DeviceRegistry.

        HA stores the device name in the DeviceRegistry, not in DeviceInfo
        after the first setup. The only way to rename an already-registered
        device is to call dr.async_update_device(). Without this call the
        device keeps whatever name HA stored at first-setup time — which may
        be the MAC address if vacuum_state['name'] was empty at that point.

        Fallback chain:
          1. vacuum_state['name'] — user-assigned name from the iRobot app
          2. 'Roomba {blid[-4:]}' — BLID suffix for unnamed robots
        """
        name = self._resolve_name(self.vacuum_state, self._blid)

        # 1. Keep _attr_device_info in sync (used when HA re-registers)
        self._attr_device_info = DeviceInfo(
            **{**self._attr_device_info, "name": name}
        )

        # 2. Patch the live DeviceRegistry entry so the UI updates immediately
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(
            identifiers={(DOMAIN, self.robot_unique_id)}
        )
        if device is None:
            _LOGGER.debug(
                "IRobotEntity: device not yet in registry, skipping name patch"
            )
            return

        if device.name_by_user:
            # User has manually renamed the device — respect their choice silently.
            return

        if device.name == name:
            return  # Nothing to do

        registry.async_update_device(device.id, name=name)
        _LOGGER.debug("IRobotEntity: device name updated to %r", name)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_name(state: dict[str, Any], blid: str) -> str:
        """Return the best available device name for the given state snapshot."""
        return state.get("name", "").strip() or f"Roomba {blid[-4:]}"

    # ── State filter / message handler ────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """Return True if this state update is relevant to this entity.

        Default implementation: ignore pure WiFi signal updates (single-key
        messages that only contain 'signal'). Subclasses override to be more
        selective and avoid unnecessary HA state writes.
        """
        return len(new_state) > 1 or "signal" not in new_state

    def on_message(self, json_data: dict[str, Any]) -> None:
        """Handle an incoming MQTT message from the Roomba.

        Refreshes the local vacuum_state snapshot and schedules a HA state
        write if new_state_filter() returns True.

        Disabled entities are skipped entirely — HA raises a warning if
        schedule_update_ha_state() is called on a disabled entity, and there
        is no point updating state that will not be written to the state machine.
        """
        if not self.enabled:
            return
        state = json_data.get("state", {}).get("reported", {})
        if self.new_state_filter(state):
            self.vacuum_state = roomba_reported_state(self.vacuum)
            self.schedule_update_ha_state()
