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
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SQFT_TO_M2

_LOGGER = logging.getLogger(__name__)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the correct fix flow for a given issue_id."""
    _NAMING_PREFIX = "smart_zones_need_naming_"
    if issue_id.startswith(_NAMING_PREFIX):
        # Issue ID encodes the entry_id so multi-robot setups open the correct
        # fix flow. Format: "smart_zones_need_naming_{entry_id}"
        entry_id = issue_id[len(_NAMING_PREFIX):]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            # Fallback for legacy issues created before v2.6.4 (no entry_id suffix)
            entries = hass.config_entries.async_entries(DOMAIN)
            entry = entries[0] if entries else None
        return SmartZoneNamingRepairFlow(entry)

    if issue_id == "smart_zones_need_naming":
        # Legacy issue ID created before v2.6.4 — fall back to first entry
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
            from .const import extract_region_id
            runtime = self._config_entry.runtime_data
            if not (runtime and runtime.roomba):
                return []
            state = roomba_reported_state(runtime.roomba)
            for entry in state.get("cleanSchedule2", []):
                for region in (entry.get("cmd", {}).get("regions") or []):
                    rid = extract_region_id(region)
                    if rid:
                        region_ids.add(rid)
            last = state.get("lastCommand", {})
            for region in (last.get("regions") or []):
                rid = extract_region_id(region)
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


async def async_check_observed_zones(
    hass: HomeAssistant,
    entry: "RoombaConfigEntry",
) -> None:
    """F22a — fire Repair Issue when cloud has obstacle zones but GridStore is empty.

    Prompts the user to run cleaning missions so GridStore can accumulate local
    stuck-event data and confirm the cloud-detected obstacles. Issue is dismissed
    when stuck events exist in GridStore (local data has caught up).

    Does not re-fire if the issue is already present or dismissed.
    """
    data = entry.runtime_data

    if data.cloud_coordinator is None:
        return
    if not data.cloud_coordinator.observed_zone_centroids:
        return  # no observed zones in UMF
    if data.grid_store is None:
        return

    if data.grid_store.stuck_event_count > 0:
        # Local data exists — dismiss the issue if present
        ir.async_delete_issue(hass, DOMAIN, "observed_zones_detected")
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        "observed_zones_detected",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="observed_zones_detected",
        translation_placeholders={
            "zone_count": str(len(data.cloud_coordinator.observed_zone_centroids)),
        },
    )


# v2.3.0 Step 6c — F8b error recurrence Repair Issue ─────────────────────────

async def async_check_error_recurrence(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """F8b — fire Repair Issue when the same numeric error code recurs >=3 times in 30 days.

    Issue body includes the error label, occurrence count, cleaning phase at the
    most recent occurrence, room name from UmfAligner (when confidence ≥ 0.70),
    and the recommended action from ERROR_CATALOGUE.

    Auto-resolves: the issue is deleted when the recurrence count drops below 3
    (e.g. after records age out of the 30-day window on the next check).

    v2.8.2: prefer MissionArchive (ARC1) over the local MissionStore when
    available. Confirmed against live field data — a 3-week recurring
    error_216 cluster never produced a local MissionStore record at all (the
    mission failed before the local "had a cleaning phase" gate that creates
    one), but was fully visible in the cloud-derived archive. The two sources
    are not merged: ARC1 generally is a superset for cloud-connected robots,
    and merging would double-count missions present in both.

    v2.8.2 bug-hunt fix: the archive-preference gate checks the *30-day
    window itself*, not just whether the archive has ever had any records.
    A robot whose archive has months of old history but whose cloud
    coordinator has been unreachable for the last 30 days specifically would
    otherwise report "no recent archive records -> no failures" even while
    the local MissionStore (which doesn't depend on cloud connectivity at
    all) keeps recording a genuine recurring failure during that exact gap —
    silently masking the one situation this whole archive-preference change
    was meant to catch more of, not less.
    Cancellation-result recurrence (no numeric code) is handled separately by
    async_check_cancellation_recurrence — these were previously invisible to
    this check entirely, since it only ever looked at `error_code`.
    """
    data = entry.runtime_data
    archive = getattr(data, "mission_archive", None)
    ms = data.mission_store

    error_counts: dict[int, int] = {}
    records: list[dict] = []
    newest_first = False

    if archive is not None:
        archive_records = archive.recent_derived(days=30)
        if archive_records:
            records = archive_records
            newest_first = True

    if not records:
        if ms is None:
            return
        records = ms.query(days=30)
        newest_first = False

    if newest_first:
        for r in records:
            code = r.get("pause_id")
            if code:
                error_counts[code] = error_counts.get(code, 0) + 1
    else:
        for r in records:
            code = r.get("error_code")
            if code:
                error_counts[code] = error_counts.get(code, 0) + 1

    worst_code = max(error_counts, key=lambda c: error_counts[c], default=None)
    if worst_code is None or error_counts[worst_code] < 3:
        ir.async_delete_issue(hass, DOMAIN, "error_recurrence")
        return

    from .const import ERROR_CATALOGUE
    catalogue_entry = ERROR_CATALOGUE.get(worst_code, {})
    label  = catalogue_entry.get("label",  f"Error {worst_code}")
    action = catalogue_entry.get("action", "")

    # Most recent record with this error code. ARC1's recent_derived() is
    # newest-first already; MissionStore.query() is oldest-first, so it
    # still needs reversing — same as before this change.
    key = "pause_id" if newest_first else "error_code"
    ordered = records if newest_first else reversed(records)
    recent = next((r for r in ordered if r.get(key) == worst_code), {})
    phase_at_error = recent.get("phase_at_error") or "unknown"

    # Room name from UmfAligner when aligned. error_position_mm is a
    # MissionStore-only field (F8b) — ARC1 records never have it, so this
    # naturally degrades to "unknown location" for archive-sourced data.
    room_name: str = "unknown location"
    aligner = data.umf_aligner
    pos     = recent.get("error_position_mm")
    if aligner and aligner.aligned and isinstance(pos, dict):
        pt_umf = aligner.pose_to_umf(
            float(pos.get("x", 0)), float(pos.get("y", 0))
        )
        if pt_umf:
            rn = aligner.room_name_at(*pt_umf)
            if rn:
                room_name = rn

    ir.async_create_issue(
        hass,
        DOMAIN,
        "error_recurrence",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="error_recurrence",
        translation_placeholders={
            "error_code": str(worst_code),
            "label":      label,
            "count":      str(error_counts[worst_code]),
            "phase":      phase_at_error,
            "room":       room_name,
            "action":     action,
        },
    )


async def async_check_cancellation_recurrence(
    hass: HomeAssistant,
    entry: Any,
) -> None:
    """v2.8.2 — fire Repair Issue when missions are repeatedly cancelled.

    Separate from async_check_error_recurrence because cancelled /
    cancelled_by_user results carry no numeric error_code at all — they were
    completely invisible to any existing recurrence check. Confirmed against
    live field data: 15 of 35 archived missions (43%) over ~6 months were
    cancelled or cancelled_by_user, including a suspicious recurring pattern
    of ~89-minute cancellations with no run_min/sqft recorded — never
    surfaced anywhere because no check counted this result class at all.

    Reads MissionArchive (ARC1) when available (same rationale as
    async_check_error_recurrence — some cancelled-before-real-start missions
    may never reach the local MissionStore), falling back to MissionStore.

    v2.8.2 bug-hunt fix: falls back to MissionStore when the archive's
    30-day window is empty, not just when the archive has never had any
    records at all — see async_check_error_recurrence for the full
    rationale (a cloud sync gap during exactly the trailing 30 days must
    not silently hide a real local cancellation pattern during that gap).

    Auto-resolves below the 3-in-30-days threshold, same as error_recurrence.
    """
    data = entry.runtime_data
    archive = getattr(data, "mission_archive", None)
    ms = data.mission_store

    records: list[dict] = []
    if archive is not None:
        archive_records = archive.recent_derived(days=30)
        if archive_records:
            records = archive_records

    if not records:
        if ms is None:
            return
        records = ms.query(days=30)

    counts: dict[str, int] = {}
    for r in records:
        result = r.get("result")
        if result in ("cancelled", "cancelled_by_user"):
            counts[result] = counts.get(result, 0) + 1

    total = sum(counts.values())
    if total < 3:
        ir.async_delete_issue(hass, DOMAIN, "cancellation_recurrence")
        return

    by_user = counts.get("cancelled_by_user", 0)
    other   = counts.get("cancelled", 0)

    ir.async_create_issue(
        hass,
        DOMAIN,
        "cancellation_recurrence",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="cancellation_recurrence",
        translation_placeholders={
            "count":         str(total),
            "by_user_count": str(by_user),
            "other_count":   str(other),
        },
    )


async def async_check_schedule_optimisation(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
) -> None:
    """F12c — raise a Repair Issue when high-dirt days lack a scheduled clean.

    Fires when ≥ 2 consecutive weekdays have relative_to_baseline > 1.8
    and no clean was recorded on those days by the schedule.

    Gate: PresenceManager active + cloud records + baseline established.
    """
    data = config_entry.runtime_data
    if data.presence_manager is None:
        return
    if not data.has_cloud or data.cloud_coordinator is None:
        return
    if data.mission_store is None:
        return

    import statistics as _stat
    from datetime import timedelta

    # P4: per-day dirt density now cached on coordinator; read directly.
    daily_density = data.cloud_coordinator.daily_dirt_density
    if len(daily_density) < 5:
        return
    baseline = _stat.median(daily_density.values())
    if not baseline:
        return

    THRESHOLD = 1.8   # relative_to_baseline trigger level
    MIN_CONSECUTIVE = 2

    # Build set of days with a completed clean in last 30 days
    by_day = data.mission_store.query_by_day(30)
    clean_days: set = {
        day for day, summary in by_day.items()
        if summary.completed > 0
    }

    # Bug B fix: use HA timezone so day strings match daily_density keys
    # (which use dt_util.as_local after the P4 fix) and clean_days
    # (which use dt_util.as_local via query_by_day).
    today = dt_util.now().date()
    consecutive_high_no_clean = 0
    trigger_days: list[str] = []

    for offset in range(1, 31):
        day = today - timedelta(days=offset)
        # P4: use cached coordinator density, not summary.dirt_density
        day_density = daily_density.get(day.isoformat())
        if day_density is None:
            consecutive_high_no_clean = 0
            trigger_days = []
            continue
        relative = day_density / baseline
        if relative > THRESHOLD and day not in clean_days:
            consecutive_high_no_clean += 1
            trigger_days.append(day.isoformat())
        else:
            consecutive_high_no_clean = 0
            trigger_days = []

        if consecutive_high_no_clean >= MIN_CONSECUTIVE:
            ir.async_create_issue(
                hass,
                DOMAIN,
                "schedule_suboptimal",
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="schedule_suboptimal",
                translation_placeholders={
                    "days": ", ".join(reversed(trigger_days[:MIN_CONSECUTIVE])),
                    "threshold": str(THRESHOLD),
                },
            )
            return

    # No issue — clear any stale one
    ir.async_delete_issue(hass, DOMAIN, "schedule_suboptimal")


# ── L7 (v2.7.0) — Stuck pattern time-correlation ─────────────────────────────

_WEEKDAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


async def async_check_stuck_pattern(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
) -> None:
    """L7 — fire Repair Issue when a stuck cell has a dominant time pattern.

    Fires when GridStore.stuck_pattern() identifies ≥1 cell where the robot
    gets stuck more than 60% of the time in the same (weekday, hour) slot,
    with ≥8 total stucks in that cell.

    Issue is per config-entry so multi-robot households surface each robot
    independently. Auto-clears when no pattern is detected.
    """
    data = config_entry.runtime_data
    gs = data.grid_store
    if gs is None:
        ir.async_delete_issue(hass, DOMAIN, f"stuck_pattern_{config_entry.entry_id}")
        return

    patterns = gs.stuck_pattern()
    if not patterns:
        ir.async_delete_issue(hass, DOMAIN, f"stuck_pattern_{config_entry.entry_id}")
        return

    # Find the cell with the most stuck events that has a pattern
    worst_cell = max(patterns.keys(), key=lambda c: gs._stuck[c]["count"])
    weekday, hour = patterns[worst_cell]

    # Resolve room name from UmfAligner when aligned
    room_name = "an unknown location"
    aligner = data.umf_aligner
    if aligner is not None and aligner.aligned:
        from .grid_store import _cell_to_mm
        x_mm, y_mm = _cell_to_mm(*worst_cell)
        pt_umf = aligner.pose_to_umf(x_mm, y_mm)
        if pt_umf is not None:
            rn = aligner.room_name_at(*pt_umf)
            if rn:
                room_name = rn

    # Build human-readable time description
    weekday_name = _WEEKDAY_NAMES[weekday % 7]
    if 5 <= hour < 12:
        time_desc = f"{weekday_name} mornings"
    elif 12 <= hour < 17:
        time_desc = f"{weekday_name} afternoons"
    elif 17 <= hour < 21:
        time_desc = f"{weekday_name} evenings"
    else:
        time_desc = f"{weekday_name}s around {hour:02d}:00"

    ir.async_create_issue(
        hass,
        DOMAIN,
        f"stuck_pattern_{config_entry.entry_id}",
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stuck_pattern",
        translation_placeholders={
            "room": room_name,
            "time": time_desc,
        },
    )
    _LOGGER.debug(
        "L7: stuck pattern detected at cell %s — %s",
        worst_cell, time_desc,
    )


async def async_check_mission_anomaly(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
) -> None:
    """L3 — Fire or clear the mission_anomaly Repair Issue.

    Fires when the last 2 consecutive missions are statistically anomalous
    (see MissionStore.consecutive_anomalous). Clears automatically once
    missions return to normal.
    """
    data = config_entry.runtime_data
    if data.mission_store is None:
        return

    consecutive = data.mission_store.consecutive_anomalous
    _LOGGER.debug(
        "mission anomaly check: consecutive_anomalous=%d for %s",
        consecutive, config_entry.entry_id,
    )

    if consecutive >= 2:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"mission_anomaly_{config_entry.entry_id}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="mission_anomaly",
            translation_placeholders={"count": str(consecutive)},
        )
    else:
        ir.async_delete_issue(
            hass, DOMAIN, f"mission_anomaly_{config_entry.entry_id}"
        )


async def async_check_smberr(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
) -> None:
    """SMBERR — Fire or clear the smberr_high Repair Issue.

    bbchg.smberr counts SMBus communication errors between the main board and
    the battery BMS chip. High counts correlate with dock contact degradation
    or an aging battery pack. Threshold: >10 000 errors.

    Confirmed field data (June 2026):
      i7+ (7-year battery, nLithF=30): smberr=50 432  → issue fires
      i8+ (3.5-year battery, nLithF=0): smberr=0      → no issue

    Gate: bbchg.smberr key present (i/s-series and 9-series bbchg variants both
    checked — 9-series bbchg has a different schema without smberr, so the key
    presence check is the natural gate).
    """
    data = config_entry.runtime_data
    vacuum_state = (data.vacuum.master_state.get("state") or {}).get("reported") or {}
    bbchg = vacuum_state.get("bbchg", {}) or {}

    if "smberr" not in bbchg:
        return  # Field absent — old firmware / 9-series without smberr

    # Bug-hunt: bare value used directly in a numeric comparison below
    # crashed with TypeError on a non-numeric firmware value (the same
    # class of bug fixed for DOCK-HEALTH's nChatters/nKnockoffs/nAborts —
    # this function predates that fix, v2.7.1, and was missed at the time).
    smberr: int = _safe_int_repairs(bbchg.get("smberr"))
    issue_id = f"smberr_high_{config_entry.entry_id}"
    _SMBERR_THRESHOLD = 10_000

    _LOGGER.debug(
        "smberr check: smberr=%d (threshold=%d) for %s",
        smberr, _SMBERR_THRESHOLD, config_entry.entry_id,
    )

    if smberr > _SMBERR_THRESHOLD:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="smberr_high",
            translation_placeholders={"count": f"{smberr:,}"},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _safe_int_repairs(val: Any, default: int = 0) -> int:
    """Convert val to int, returning default on TypeError/ValueError.

    Bug-hunt (v2.8.0): bbchg.nChatters/nKnockoffs/nAborts have been observed
    arriving as non-numeric strings on some firmware variants — bare int()
    crashed async_check_dock_health, silently disabling the dock-health
    Repair Issue for affected robots (hass.async_create_task swallows the
    exception into the HA log, so the failure was invisible to the user).
    """
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# DOCK-HEALTH thresholds (v2.8.0) — conservative heuristics pending COMM-A calibration.
_DOCK_CHATTERS_THRESHOLD = 100   # contact bounce events
_DOCK_KNOCKOFFS_THRESHOLD = 10   # unintended undocking events
_DOCK_ABORTS_THRESHOLD = 20      # aborted charging sessions


async def async_check_dock_health(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
) -> None:
    """DOCK-HEALTH (v2.8.0) — Fire or clear the dock_contact_health Repair Issue.

    bbchg contains three dock-contact health counters:
      nChatters  — contact bounce events (threshold: > 100)
      nKnockoffs — unintended undocking (threshold: > 10)
      nAborts    — aborted charging sessions (threshold: > 20)

    The issue fires when ANY counter exceeds its threshold and reports all
    affected metrics.  Auto-resolves when all counters drop below thresholds.

    Gate: at least one of the three fields must be present in bbchg.
    This is distinct from smberr (SMBus errors) which indicates battery chip
    communication faults rather than physical dock contact wear.
    """
    data = config_entry.runtime_data
    vacuum_state = (data.vacuum.master_state.get("state") or {}).get("reported") or {}
    bbchg = vacuum_state.get("bbchg", {}) or {}

    # Gate: at least one dock health field present
    if not any(k in bbchg for k in ("nChatters", "nKnockoffs", "nAborts")):
        return

    chatters: int = _safe_int_repairs(bbchg.get("nChatters"))
    knockoffs: int = _safe_int_repairs(bbchg.get("nKnockoffs"))
    aborts: int = _safe_int_repairs(bbchg.get("nAborts"))
    issue_id = f"dock_contact_health_{config_entry.entry_id}"

    _LOGGER.debug(
        "dock_health check: chatters=%d knockoffs=%d aborts=%d for %s",
        chatters, knockoffs, aborts, config_entry.entry_id,
    )

    exceeded = (
        chatters > _DOCK_CHATTERS_THRESHOLD
        or knockoffs > _DOCK_KNOCKOFFS_THRESHOLD
        or aborts > _DOCK_ABORTS_THRESHOLD
    )

    if exceeded:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="dock_contact_health",
            translation_placeholders={
                "chatters": str(chatters),
                "knockoffs": str(knockoffs),
                "aborts": str(aborts),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


# ── v2.8.3 — Connectivity & Monitoring Repair Issues ─────────────────────────

async def async_check_cloud_stale(
    hass: HomeAssistant,
    config_entry: "RoombaConfigEntry",
    cloud_coordinator: Any,
) -> None:
    """CLOUD-STALE (v2.8.3) — fire or clear the cloud_stale Repair Issue.

    Fires when the cloud coordinator has not had a successful refresh for
    CLOUD_STALE_MINUTES (60 min) — i.e. at least two consecutive coordinator
    intervals have failed.  Auto-resolves on the next successful refresh.

    Called from _on_cloud_refresh_complete in __init__.py, which fires after
    every coordinator update regardless of success, so this check runs both
    when updates succeed (to clear the issue) and when they fail (to raise it).

    Distinct from WIFI-CLOUD-HEALTH (robot-side cloud disconnect) — this
    issue represents HA failing to fetch data, regardless of whether the robot
    itself can still reach iRobot servers.
    """
    from datetime import timedelta
    from .const import CLOUD_STALE_MINUTES

    issue_id = f"cloud_stale_{config_entry.entry_id}"
    last_success = getattr(cloud_coordinator, "last_success_time", None)

    if last_success is None:
        # No successful fetch yet in this HA session — not an error (startup).
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    age_minutes = (dt_util.utcnow() - last_success).total_seconds() / 60.0

    if age_minutes > CLOUD_STALE_MINUTES:
        _LOGGER.warning(
            "Roomba+: cloud coordinator for %s has not refreshed successfully "
            "for %.0f min (threshold %d min) — raising cloud_stale issue",
            config_entry.entry_id, age_minutes, CLOUD_STALE_MINUTES,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="cloud_stale",
            translation_placeholders={"minutes": str(int(age_minutes))},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
