"""Maintenance Store for Roomba+ — persists part-reset timestamps.

When a user replaces the filter, brushes, or battery, they press the
corresponding Reset button. This store records the bbrun.hr value at that
moment. The remaining-life sensors then compute:

    remaining = max(0, threshold - (current_hr - reset_hr))

Without a reset, reset_hr defaults to 0, so the initial calculation
uses the robot's full lifetime hours — which is correct for a robot that
was never tracked before. After the first reset, the counter restarts.

Storage is per config-entry in hass.storage so it survives HA restarts
and integration reloads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_maintenance"
STORAGE_VERSION    = 1


@dataclass
class MaintenanceStore:
    """Stores the bbrun.hr value at the time each part was last reset.

    All values in hours (integer, matching bbrun.hr).
    Default 0 = never reset = use full lifetime hours in remaining calculation.
    """

    filter_reset_hr: int = 0    # bbrun.hr when filter was last replaced
    brush_reset_hr: int  = 0    # bbrun.hr when brushes were last replaced
    battery_reset_hr: int = 0   # bbrun.hr when battery was last replaced (for cycle tracking)

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        """Load persisted reset values from hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("MaintenanceStore: no persisted data for %s", entry_id)
            return
        try:
            self.filter_reset_hr  = int(data.get("filter_reset_hr",  0))
            self.brush_reset_hr   = int(data.get("brush_reset_hr",   0))
            self.battery_reset_hr = int(data.get("battery_reset_hr", 0))
            _LOGGER.debug(
                "MaintenanceStore: loaded — filter_reset=%dh brush_reset=%dh",
                self.filter_reset_hr, self.brush_reset_hr,
            )
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("MaintenanceStore: failed to load data: %s", exc)

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        """Persist current reset values to hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "filter_reset_hr":  self.filter_reset_hr,
            "brush_reset_hr":   self.brush_reset_hr,
            "battery_reset_hr": self.battery_reset_hr,
        })

    def reset_filter(self, current_hr: int) -> None:
        """Record current bbrun.hr as filter replacement point."""
        self.filter_reset_hr = current_hr
        _LOGGER.info("MaintenanceStore: filter reset at %dh", current_hr)

    def reset_brush(self, current_hr: int) -> None:
        """Record current bbrun.hr as brush replacement point."""
        self.brush_reset_hr = current_hr
        _LOGGER.info("MaintenanceStore: brush reset at %dh", current_hr)

    def reset_battery(self, current_hr: int) -> None:
        """Record current bbrun.hr as battery replacement point."""
        self.battery_reset_hr = current_hr
        _LOGGER.info("MaintenanceStore: battery reset at %dh", current_hr)

    def filter_remaining(self, current_hr: int, threshold: int) -> int:
        """Hours remaining until next filter replacement."""
        hours_since_reset = current_hr - self.filter_reset_hr
        return max(0, threshold - hours_since_reset)

    def brush_remaining(self, current_hr: int, threshold: int) -> int:
        """Hours remaining until next brush replacement."""
        hours_since_reset = current_hr - self.brush_reset_hr
        return max(0, threshold - hours_since_reset)
