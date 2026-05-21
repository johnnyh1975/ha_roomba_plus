"""Repair fix flows for Roomba+.

HA calls async_create_fix_flow() when the user clicks Fix on a Repair Issue
that has is_fixable=True. The fix flow step_id MUST be "init" — the HA
repair frontend only routes form submissions for the "init" step. Any other
step_id causes the dialog to close immediately as "Problem resolved".

Zone IDs are read from config entry options["discovered_zone_ids"] rather
than from live robot state, because by the time the user clicks Fix the
robot's MQTT state may no longer contain regions (e.g. after a full clean
or a return-to-base mission).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the correct fix flow for a given issue_id."""
    if issue_id == "smart_zones_need_naming":
        # Always use the first DOMAIN entry — single-robot setups only
        # have one. Do NOT filter on discovered_zone_ids here: that key
        # may not yet be written when the user clicks Fix (race between
        # async_update_entry and the repair dialog opening).
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = entries[0] if entries else None
        return SmartZoneNamingRepairFlow(entry)

    # Generic fallback for unknown issues.
    return ConfirmRepairFlow()


class SmartZoneNamingRepairFlow(RepairsFlow):
    """Fix flow for naming newly discovered Smart Map zones.

    step_id is always "init" — HA repair frontend requirement.
    """

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        """Show zone naming form or process submitted names."""
        if self._config_entry is None:
            return self.async_create_entry(data={})

        opts = self._config_entry.options
        named = (
            set(opts.get("smart_zone_data", {}))
            | set(opts.get("smart_zone_labels", {}))
        )

        # Primary source: persisted discovered_zone_ids.
        # Fallback: read directly from live robot state in case
        # async_update_entry had not yet flushed when the dialog opened.
        discovered: list[str] = list(opts.get("discovered_zone_ids", []))
        if not discovered:
            discovered = self._collect_from_live_state()

        unlabelled = [rid for rid in discovered if rid not in named]

        if not unlabelled:
            # Everything already labelled — dismiss and close.
            self._dismiss()
            return self.async_create_entry(data={})

        if user_input is not None:
            new_labels = dict(opts.get("smart_zone_labels", {}))
            new_zone_data = dict(opts.get("smart_zone_data", {}))

            # Resolve pmap_id from live state if available.
            from . import roomba_reported_state
            state = {}
            try:
                runtime = self._config_entry.runtime_data
                if runtime and runtime.roomba:
                    state = roomba_reported_state(runtime.roomba)
            except Exception:  # noqa: BLE001
                pass

            last = state.get("lastCommand", {})
            pmaps: list[dict] = state.get("pmaps", [])
            pmap_id = (
                last.get("pmap_id")
                or next(
                    (cmd.get("cmd", {}).get("pmap_id")
                     for cmd in state.get("cleanSchedule2", [])
                     if cmd.get("cmd", {}).get("pmap_id")),
                    None,
                )
                or (next(iter(pmaps[0]), None) if pmaps else "")
                or ""
            )

            for rid in unlabelled:
                label = user_input.get(f"zone_{rid}", "").strip()
                if label:
                    new_labels[rid] = label
                    new_zone_data[rid] = {"name": label, "pmap_id": pmap_id}

            # Remove labelled IDs from discovered_zone_ids.
            new_discovered = [r for r in discovered if r not in unlabelled]

            new_opts = dict(opts)
            new_opts["smart_zone_labels"] = new_labels
            new_opts["smart_zone_data"] = new_zone_data
            new_opts["discovered_zone_ids"] = new_discovered
            self.hass.config_entries.async_update_entry(
                self._config_entry, options=new_opts
            )
            _LOGGER.info(
                "SmartZoneNamingRepairFlow: saved labels for %d zone(s)",
                len(new_labels),
            )
            self._dismiss()
            return self.async_create_entry(data={})

        # Show the naming form.
        schema = vol.Schema({
            vol.Optional(f"zone_{rid}", default=f"Zone {rid}"): str
            for rid in unlabelled
        })
        return self.async_show_form(
            step_id="init",  # MUST be "init" — HA repair frontend requirement
            data_schema=schema,
            description_placeholders={
                "zone_count": str(len(unlabelled)),
                "zone_ids": ", ".join(unlabelled),
            },
        )

    def _collect_from_live_state(self) -> list[str]:
        """Read region IDs directly from live robot state.

        Fallback when discovered_zone_ids has not yet been persisted.
        Reads cleanSchedule2 and lastCommand from the roomba entity.
        """
        region_ids: set[str] = set()
        try:
            from . import roomba_reported_state
            runtime = self._config_entry.runtime_data
            if not (runtime and runtime.roomba):
                return []
            state = roomba_reported_state(runtime.roomba)
            for entry in state.get("cleanSchedule2", []):
                for region in (entry.get("cmd", {}).get("regions") or []):
                    rid = region.get("region_id")
                    if rid:
                        region_ids.add(rid)
            last = state.get("lastCommand", {})
            for region in (last.get("regions") or []):
                rid = region.get("region_id")
                if rid:
                    region_ids.add(rid)
        except Exception:  # noqa: BLE001
            pass
        return sorted(region_ids)

    def _dismiss(self) -> None:
        """Delete the repair issue."""
        ir.async_delete_issue(self.hass, DOMAIN, "smart_zones_need_naming")


class ConfirmRepairFlow(RepairsFlow):
    """Generic confirm-only fix flow for issues with no form."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="init")
