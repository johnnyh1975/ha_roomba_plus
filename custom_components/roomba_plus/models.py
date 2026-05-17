"""Data models for the Roomba+ integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from roombapy import Roomba
    from .maintenance_store import MaintenanceStore
    from .map_renderer import MapRenderer
    from .zone_store import ZoneStore


class MapCapability(Enum):
    """Map rendering capability level of a robot.

    NONE:      600-series bump-and-run — no pose data, no map.
    EPHEMERAL: 900-series VSLAM — pose data available, map is per-mission only,
               no persistent pmaps. ZoneStore accumulates zones across missions.
    SMART:     i/s/j/m-series — pose data + persistent pmaps/regions. ZoneStore
               disabled (regions come from cloud pmaps in Phase 3).
    """

    NONE = "none"
    EPHEMERAL = "ephemeral"
    SMART = "smart"


@dataclass
class RoombaData:
    """Runtime data stored in config_entry.runtime_data."""

    roomba: Roomba
    blid: str
    map_capability: MapCapability = MapCapability.NONE
    renderer: MapRenderer | None = None
    zone_store: ZoneStore | None = None
    maintenance_store: MaintenanceStore | None = None

    def roomba_reported_state(self) -> dict[str, Any]:
        """Return the reported state dict from master_state."""
        return self.roomba.master_state.get("state", {}).get("reported", {})


# Typed config entry — gives full IDE type safety throughout the integration
type RoombaConfigEntry = ConfigEntry[RoombaData]
