"""Diagnostics support for Roomba+.

Provides structured debug output for bug reports without leaking credentials.
Accessible via Settings → Devices & Services → Roomba+ → Download diagnostics.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import roomba_reported_state
from .const import DIAG_REDACT_KEYS, DOMAIN
from .models import MapCapability, RoombaConfigEntry

_CLOUD_REDACT = DIAG_REDACT_KEYS | {"irobot_username", "irobot_password"}


def _cloud_diag(data: Any) -> dict[str, Any]:
    """Return cloud coordinator diagnostics (no credentials)."""
    cc = data.cloud_coordinator
    if cc is None:
        return {"enabled": False}
    result: dict[str, Any] = {
        "enabled": True,
        "last_update_success": cc.last_update_success,
        "last_exception": str(cc.last_exception) if cc.last_exception else None,
    }
    if cc.data:
        result["pmap_count"] = len(cc.data.get("pmaps", []))
        result["favorite_count"] = len(cc.data.get("favorites", []))
        result["active_pmap_id"] = cc.active_pmap_id
        result["region_count"] = len(cc.regions)
        result["zone_count"] = len(cc.zones)
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Sensitive keys (BLID, password, credentials) are redacted.
    The output is structured for easy triage of connectivity, map, and zone issues.
    """
    data = config_entry.runtime_data
    roomba = data.roomba
    state = roomba_reported_state(roomba)

    # Check whether the Core roomba integration is also active (conflict warning)
    core_roomba_active = any(
        e.domain == "roomba"
        for e in hass.config_entries.async_entries()
        if e.state.value == "loaded"
    )

    # ── Map subsystem ──────────────────────────────────────────────────────────
    map_diag: dict[str, Any] = {
        "capability": data.map_capability.value,
    }
    if data.renderer is not None:
        map_diag["renderer"] = data.renderer.diagnostic_info()

    # ── Zone subsystem ─────────────────────────────────────────────────────────
    zone_diag: dict[str, Any] = {"available": data.zone_store is not None}
    if data.zone_store is not None:
        zone_diag.update(data.zone_store.diagnostic_info())

    diag: dict[str, Any] = {
        "integration": DOMAIN,
        "version": config_entry.version,
        "title": config_entry.title,

        # Config and options with sensitive values redacted
        "config": async_redact_data(dict(config_entry.data), _CLOUD_REDACT),
        "options": async_redact_data(dict(config_entry.options), _CLOUD_REDACT),

        # Connection state
        "connection": {
            "connected": roomba.roomba_connected,
            "current_state": roomba.current_state,
            "client_error": roomba.client_error,
            "continuous": roomba.continuous,
            "delay": roomba.delay,
        },

        # Error state
        "error": {
            "error_code": roomba.error_code,
            "error_message": roomba.error_message,
        },

        # Device identity (non-sensitive capability / version info)
        "device": {
            "sku": state.get("sku"),
            "software_version": state.get("softwareVer"),
            "hardware_revision": state.get("hardwareRev"),
            "battery_type": state.get("batteryType"),
            "capabilities": state.get("cap", {}),
        },

        # Current mission status
        "mission": state.get("cleanMissionStatus", {}),

        # Smart Map state — critical for diagnosing region-clean failures.
        # pmap_ids shows which maps the robot has stored (pmapv values redacted
        # as they are session tokens). lastCommand shows the most recent command
        # type and region_id so pmap resolution can be verified without needing
        # the full HA log.
        "smart_map": {
            "map_upload_allowed": state.get("mapUploadAllowed"),
            "pmap_learning_allowed": state.get("pmapLearningAllowed"),
            "not_ready_raw": state.get("cleanMissionStatus", {}).get("notReady"),
            "pmap_ids": [
                next(iter(p)) for p in state.get("pmaps", []) if p
            ],
            "last_command_summary": {
                "command": state.get("lastCommand", {}).get("command"),
                "pmap_id": state.get("lastCommand", {}).get("pmap_id"),
                "user_pmapv_id": state.get("lastCommand", {}).get("user_pmapv_id"),
                "initiator": state.get("lastCommand", {}).get("initiator"),
                "region_ids": [
                    r.get("region_id")
                    for r in (state.get("lastCommand", {}).get("regions") or [])
                ],
            },
            # cleanSchedule2 stores scheduled/recent app-initiated region cleans.
            # Shows the exact pmap_id and user_pmapv_id the app used — useful for
            # verifying that our resolved values match what works.
            "clean_schedule2_pmaps": [
                {
                    "pmap_id": entry.get("cmd", {}).get("pmap_id"),
                    "user_pmapv_id": entry.get("cmd", {}).get("user_pmapv_id"),
                    "region_ids": [
                        r.get("region_id")
                        for r in (entry.get("cmd", {}).get("regions") or [])
                    ],
                }
                for entry in state.get("cleanSchedule2", [])
                if entry.get("cmd", {}).get("pmap_id")
            ],
        },

        # Lifetime statistics (useful for maintenance sensor debugging)
        "lifetime_stats": {
            "bbrun": state.get("bbrun", {}),
            "bbmssn": state.get("bbmssn", {}),
            "bbchg3": state.get("bbchg3", {}),
        },

        # Last known position
        "position": state.get("pose"),

        # Bin / dock state
        "bin": state.get("bin"),
        "dock": state.get("dock"),

        # Map and zone subsystem
        "map": map_diag,
        "zones": zone_diag,

        # Cloud coordinator status
        "cloud": _cloud_diag(data),

        # All top-level keys in master_state (for debugging unknown models)
        "master_state_keys": sorted(state.keys()),

        # Conflict warning
        "warnings": {
            "core_roomba_integration_also_active": core_roomba_active,
        },
    }

    return diag
