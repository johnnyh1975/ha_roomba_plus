"""legacy_zone_migration.py — minimal, read-only loader for the old
ZoneStore storage format, kept ONLY so existing installations can migrate
their confirmed room names into RoomSegStore once (see
room_seg_store.py::migrate_from_zone_store and ROOM_SEGMENTATION_NOTES.md
Stage 6).

This is NOT the old ZoneStore. There is no gap-detection, no
process_mission, no calibrate_from_gaps, no mutation methods (rename/
hide/unhide), no async_save. It does exactly one thing: read whatever a
pre-ROOM-SEG installation already saved to hass.storage under the old
"roomba_plus_zones_{entry_id}" key, and hand back the handful of fields
migrate_from_zone_store() actually needs (name, confirmed, hidden, and
the bounding box). Once every installation has gone through this
one-shot migration, this module can be deleted too — it has no other
purpose and nothing else in the integration imports it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

# Must match the OLD zone_store.py exactly, or this can't read installations
# that saved data under the old module's key/version.
_LEGACY_STORAGE_KEY_PREFIX = "roomba_plus_zones"
_LEGACY_HA_STORE_VERSION = 1
_LEGACY_PAYLOAD_VERSION = 2


@dataclass
class LegacyZone:
    """Just enough of the old Zone dataclass for migration purposes —
    matches the attribute names migrate_from_zone_store() reads via
    getattr(), so no changes were needed there."""

    name: str
    confirmed: bool
    hidden: bool
    x_min: float
    x_max: float
    y_min: float
    y_max: float


async def async_load_legacy_zones(
    hass: HomeAssistant, entry_id: str
) -> list[LegacyZone]:
    """Read old ZoneStore data for one-shot migration. Returns an empty
    list if nothing was ever saved, the payload version doesn't match
    (same "discard incompatible old data" gate the original ZoneStore
    used), or the file is otherwise unreadable — migration simply finds
    nothing to migrate in any of those cases, rather than raising.
    """
    store = Store(
        hass, _LEGACY_HA_STORE_VERSION, f"{_LEGACY_STORAGE_KEY_PREFIX}_{entry_id}"
    )
    data: dict | None = await store.async_load()
    if not data:
        _LOGGER.debug("legacy_zone_migration: no old ZoneStore data for %s", entry_id)
        return []
    if data.get("version") != _LEGACY_PAYLOAD_VERSION:
        _LOGGER.debug(
            "legacy_zone_migration: old ZoneStore data for %s has incompatible "
            "version %s (expected %s) — nothing to migrate",
            entry_id, data.get("version"), _LEGACY_PAYLOAD_VERSION,
        )
        return []
    try:
        zones = [
            LegacyZone(
                name=z["name"],
                confirmed=z.get("confirmed", False),
                hidden=z.get("hidden", False),
                x_min=z["x_min"], x_max=z["x_max"],
                y_min=z["y_min"], y_max=z["y_max"],
            )
            for z in data.get("zones", [])
        ]
        _LOGGER.debug(
            "legacy_zone_migration: read %d old zone(s) for %s", len(zones), entry_id
        )
        return zones
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "legacy_zone_migration: failed to read old ZoneStore data for %s: %s",
            entry_id, exc,
        )
        return []
