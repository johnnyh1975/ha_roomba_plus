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

        from .const import CONF_SMART_ZONE_HIDDEN
        hidden_ids: set = set(opts.get(CONF_SMART_ZONE_HIDDEN, []))
        unlabelled = [
            rid for rid in discovered
            if rid not in named and rid not in hidden_ids
        ]

        if not unlabelled:
            # Everything already labelled — dismiss and close.
            self._dismiss()
            return self.async_create_entry(data={})

        errors: dict[str, str] = {}

        if user_input is not None:
            # Parse "id=Name" entries from the textarea.
            #
            # Two delimiter styles are accepted so the form is forgiving
            # regardless of how the browser renders the pre-filled value:
            #
            #   Newline-separated (canonical, one entry per line):
            #     1=Cucina
            #     17=CabinaArmadio
            #
            #   Comma-separated (fallback, what users type when the textarea
            #   visually shows all IDs on one line):
            #     1=Cucina,17=CabinaArmadio,19=Bagno
            #
            # Strategy: if the raw input contains at least one "," that sits
            # between two "id=..." tokens (i.e. the pattern ",digits="), split
            # on commas first.  Otherwise split on newlines.  This lets names
            # contain commas (e.g. "Living room, open plan") without breaking.
            raw: str = user_input.get("zones", "").strip()
            parsed: dict[str, str] = {}

            import re as _re
            # Detect comma-as-delimiter: a comma followed by digits then "="
            _comma_delim = _re.compile(r",\s*\d")
            if _comma_delim.search(raw):
                # Split on commas that precede a digit= token.
                # Use a lookahead so the delimiter comma is consumed but the
                # digit that follows is kept as part of the next token.
                tokens = _re.split(r",(?=\s*\d)", raw)
            else:
                tokens = raw.splitlines()

            for token in tokens:
                token = token.strip()
                if not token:
                    continue
                if "=" not in token:
                    _LOGGER.warning(
                        "SmartZoneNamingRepairFlow: skipping malformed token %r "
                        "(expected 'id=Name' format)",
                        token,
                    )
                    continue
                rid_part, _, name_part = token.partition("=")
                rid = rid_part.strip()
                name = name_part.strip()
                if rid in unlabelled and name:
                    parsed[rid] = name

            # Resolve pmap_id from live state using the same priority order
            # as _resolve_rooms in __init__.py:
            #   1. lastCommand.pmap_id (canonical for multi-map robots)
            #   2. cleanSchedule2[].cmd.pmap_id
            #   3. pmaps[0] key as last resort
            # Must be resolved before the parsed/pmap_id checks below so
            # that pmap_id is always bound (fixes UnboundLocalError).
            from . import roomba_reported_state  # noqa: PLC0415
            _state: dict = {}
            try:
                runtime = self._config_entry.runtime_data
                if runtime and runtime.roomba:
                    _state = roomba_reported_state(runtime.roomba)
            except Exception:  # noqa: BLE001
                pass

            _last = _state.get("lastCommand", {})
            _pmaps: list[dict] = _state.get("pmaps", [])
            pmap_id: str = (
                _last.get("pmap_id")
                or next(
                    (
                        cmd.get("cmd", {}).get("pmap_id")
                        for cmd in _state.get("cleanSchedule2", [])
                        if cmd.get("cmd", {}).get("pmap_id")
                    ),
                    None,
                )
                or (next(iter(_pmaps[0]), None) if _pmaps else "")
                or ""
            )

            if not parsed:
                errors["zones"] = "no_valid_entries"
            elif not pmap_id:
                errors["zones"] = "pmap_not_resolved"
            else:
                new_labels = dict(opts.get("smart_zone_labels", {}))
                new_zone_data = dict(opts.get("smart_zone_data", {}))

                for rid, name in parsed.items():
                    new_labels[rid] = name
                    new_zone_data[rid] = {"name": name, "pmap_id": pmap_id}

                # Keep discovered_zone_ids intact — it is the permanent registry
                # that populates the Smart Map Zone selector even after MQTT
                # state no longer contains the regions. Unlabelled filtering is
                # handled separately by _unlabelled_region_ids() in select.py.
                new_opts = dict(opts)
                new_opts["smart_zone_labels"] = new_labels
                new_opts["smart_zone_data"] = new_zone_data
                new_opts["discovered_zone_ids"] = discovered
                self.hass.config_entries.async_update_entry(
                    self._config_entry, options=new_opts
                )
                _LOGGER.info(
                    "SmartZoneNamingRepairFlow: saved labels for %d zone(s): %s",
                    len(parsed),
                    list(parsed.keys()),
                )
                self._dismiss()
                return self.async_create_entry(data={})

        # Build the default textarea value: one "id=" stub per unlabelled zone,
        # separated by newlines so each zone starts on its own line.
        #
        # The HA repair frontend renders a <textarea> for `str` schema fields.
        # Python's "\n".join() produces a string with real newline characters
        # which the browser preserves correctly in a textarea — each zone ID
        # appears on its own line and the user fills in the name after "=".
        #
        # Historical note: an earlier version used ", ".join() which caused all
        # IDs to appear on a single line (e.g. "1=17=19=") and prompted users
        # to enter comma-separated input. The parser now accepts both formats
        # for backwards compatibility, but the canonical pre-fill is newlines.
        default_text = "\n".join(f"{rid}=" for rid in unlabelled)

        schema = vol.Schema(
            {vol.Required("zones", default=default_text): str}
        )
        return self.async_show_form(
            step_id="init",  # MUST be "init" — HA repair frontend requirement
            data_schema=schema,
            errors=errors,
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
                    if rid is not None:
                        region_ids.add(str(rid))
            last = state.get("lastCommand", {})
            for region in (last.get("regions") or []):
                rid = region.get("region_id")
                if rid is not None:
                    region_ids.add(str(rid))
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


# ── F4d -- bbrun.hr firmware reset detection ──────────────────────────────────

async def async_check_bbrun_reset(
    hass: HomeAssistant,
    config_entry: Any,
    maintenance_store: Any,
    current_hr: int,
) -> None:
    """Fire a Repair Issue when bbrun.hr is lower than stored reset baselines.

    F4d -- a firmware update can silently reset the bbrun.hr lifetime counter.
    When current_hr < any stored reset_hr, the remaining-hours calculations
    are wrong.  This issue is non-fixable (no automated recovery) -- the user
    must manually reset all consumables via the Reset buttons.
    """
    affected: list[str] = []
    if current_hr < maintenance_store.filter_reset_hr:
        affected.append("filter")
    if current_hr < maintenance_store.brush_reset_hr:
        affected.append("brush")
    if current_hr < maintenance_store.battery_reset_hr:
        affected.append("battery")

    if not affected:
        # No reset detected -- clear any stale issue from a previous check
        ir.async_delete_issue(hass, DOMAIN, "maintenance_baselines_reset")
        return

    parts_str = ", ".join(affected)
    _LOGGER.warning(
        "Roomba+: bbrun.hr (%d h) is lower than stored reset baselines "
        "for: %s -- firmware update may have reset the runtime counter. "
        "Remaining-hours values are unreliable until parts are manually reset.",
        current_hr,
        parts_str,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "maintenance_baselines_reset",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="maintenance_baselines_reset",
        translation_placeholders={"parts": parts_str},
    )


# ── F6 — Performance & Behavioral Repair Issues ───────────────────────────────

async def async_check_performance_degradation(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """F6a — fire Repair Issue when cleaning speed declines for 3+ consecutive updates.

    Reads cleaning_speed_trend from RoombaData and increments a counter stored
    there.  Issue fires when counter reaches 3.  Suppressed 14 days after dismiss.
    """
    data = entry.runtime_data
    trend = data.cleaning_speed_trend_value
    dirt_rising = data.dirt_density_rising

    if trend == "declining":
        data.consecutive_declining_speed += 1
    else:
        data.consecutive_declining_speed = 0

    # Cap counter at 10 to prevent unbounded growth and log spam after issue fires
    data.consecutive_declining_speed = min(data.consecutive_declining_speed, 10)

    if data.consecutive_declining_speed < 3:
        return

    store = data.maintenance_store
    cause = "unknown"
    if store:
        from .const import CONF_BRUSH_HOURS, CONF_FILTER_HOURS
        current_hr = data.roomba_reported_state().get("bbrun", {}).get("hr", 0)
        brush_rem = store.brush_remaining(
            current_hr,
            entry.options.get(CONF_BRUSH_HOURS, 150),
        )
        filter_rem = store.filter_remaining(
            current_hr,
            entry.options.get(CONF_FILTER_HOURS, 150),
        )
        if brush_rem < 40:
            cause = "brush_wear"
        elif filter_rem < 20:
            cause = "filter_clog"
        elif dirt_rising:
            cause = "environment_change"

    ir.async_create_issue(
        hass,
        DOMAIN,
        "performance_degradation",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="performance_degradation",
        translation_placeholders={"cause": cause},
    )
    _LOGGER.warning(
        "Roomba+: performance degradation detected (cause=%s, "
        "declining for %d consecutive updates)",
        cause, data.consecutive_declining_speed,
    )


async def async_check_battery_recharge(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """F6b — fire Repair Issue when recharge fraction is high and capacity is low."""
    data = entry.runtime_data
    recharge_pct = data.recharge_fraction_value
    retention_pct = data.battery_retention_value

    if recharge_pct is not None and recharge_pct > 15 \
            and retention_pct is not None and retention_pct < 75:
        data.consecutive_battery_warn += 1
    else:
        data.consecutive_battery_warn = 0

    data.consecutive_battery_warn = min(data.consecutive_battery_warn, 10)

    if data.consecutive_battery_warn < 3:
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        "battery_recharge_high",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="battery_recharge_high",
    )


async def async_check_mixed_schedule(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """F6e — fire Repair Issue when both HA schedule and iRobot app starts detected."""
    data = entry.runtime_data
    store = data.mission_store
    if store is None:
        return

    records = store.query(30)
    if len(records) < 10:
        return

    total = len(records)
    schedule_count = sum(1 for r in records if r.get("initiator") == "schedule")
    app_count = sum(1 for r in records if r.get("initiator") in ("rmtApp", "localApp"))

    schedule_pct = schedule_count / total * 100
    app_pct = app_count / total * 100

    if schedule_pct > 20 and app_pct > 20:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "mixed_schedule",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="mixed_schedule",
            translation_placeholders={
                "schedule_pct": str(round(schedule_pct)),
                "app_pct": str(round(app_pct)),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "mixed_schedule")


async def async_check_accident_detection(
    hass: HomeAssistant,
    entry: Any,
    records: list,
) -> None:
    """F6f — fire alert when cloud records show a floor accident signature.

    Signature: dirt_events/sqft > 3× the 90-day 95th-percentile AND
    duration_min < 20 (robot stopped early).  No pet mode config required.
    """
    if not records or len(records) < 5:
        return

    # Compute 95th-percentile density baseline from 90-day window
    densities = []
    for r in records:
        dirt = r.get("dirt")
        sqft = r.get("sqft")
        if dirt is not None and sqft and float(sqft) > 0:
            densities.append(float(dirt) / float(sqft))

    if len(densities) < 5:
        return

    densities_sorted = sorted(densities)
    p95_idx = int(len(densities_sorted) * 0.95)
    p95 = densities_sorted[p95_idx]

    # Check the most recent record only
    recent = records[0]
    recent_dirt = recent.get("dirt")
    recent_sqft = recent.get("sqft")
    recent_dur = recent.get("durationM") or recent.get("duration_min", 60)

    if recent_dirt is None or not recent_sqft or float(recent_sqft) == 0:
        return

    recent_density = float(recent_dirt) / float(recent_sqft)
    if recent_density > 3 * p95 and float(recent_dur) < 20:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "accident_detected",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="accident_detected",
        )
        _LOGGER.warning(
            "Roomba+: possible floor accident detected — "
            "dirt density %.2f × baseline (%.2f), mission duration %s min",
            recent_density / p95 if p95 > 0 else 0,
            p95,
            recent_dur,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "accident_detected")


async def async_check_consecutive_skips(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """F6g — fire Repair Issue when cleaning has been skipped 3+ consecutive times."""
    data = entry.runtime_data
    store = data.maintenance_store
    if store is None:
        return

    skips = store.consecutive_skips
    if skips >= 3:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "consecutive_skips",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="consecutive_skips",
            translation_placeholders={"count": str(skips)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "consecutive_skips")


async def async_enrich_drift_issue(
    hass: HomeAssistant,
    entry: Any,
    dx: float,
    dy: float,
) -> None:
    """F6d — enrich the existing drift Repair Issue with bearing and magnitude.

    Called by geometry_store when a new drift event is recorded.
    Adds bearing (compass degrees), magnitude (cm), and trend to the issue data.
    """
    import math
    magnitude_cm = round(math.sqrt(dx ** 2 + dy ** 2) / 10, 1)  # mm → cm
    bearing_deg = round((math.degrees(math.atan2(dx, dy)) + 360) % 360)

    _LOGGER.debug(
        "Roomba+: drift enrichment — bearing=%d° magnitude=%.1f cm",
        bearing_deg, magnitude_cm,
    )

    ir.async_create_issue(
        hass,
        DOMAIN,
        "map_drift_detected",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="map_drift_detected",
        translation_placeholders={
            "bearing": str(bearing_deg),
            "magnitude_cm": str(magnitude_cm),
        },
    )
