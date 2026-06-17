"""Base entity class for Roomba+ integration."""
from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.const import ATTR_CONNECTIONS
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import DOMAIN

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

    def __init__(self, roomba: Any, blid: str) -> None:
        """Initialise the entity with the roombapy Roomba object and BLID."""
        self.vacuum = roomba
        self._blid = blid
        self.vacuum_state = roomba_reported_state(roomba)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.robot_unique_id)},
            serial_number=(
                self.vacuum_state.get("hwPartsRev", {}).get("navSerialNo")
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
            self.vacuum_state.get("hwPartsRev", {}).get("wlan0HwAddr")
            or self.vacuum_state.get("mac")
        )
        if mac_address:
            self._attr_device_info[ATTR_CONNECTIONS] = {
                (dr.CONNECTION_NETWORK_MAC, mac_address)
            }

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def robot_unique_id(self) -> str:
        """Stable device identifier based on BLID."""
        return f"roomba_plus_{self._blid}"

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
        return self.vacuum_state.get("dock", {}).get("tankLvl")

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
        """
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
