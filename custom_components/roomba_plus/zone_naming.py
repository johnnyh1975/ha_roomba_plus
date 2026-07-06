"""Zone-naming helpers for the Roomba+ integration.

v3.4.0 TODO — extracted from select.py's SmartZoneSelect
(_collect_region_ids()/_unlabelled_region_ids()) so the new todo.py
platform can determine "are there unnamed zones?" without importing
select.py — same coupling-avoidance principle as schedule_parser.py
for CAL (one new platform module should not import from another).
SmartZoneSelect keeps working via thin wrapper methods delegating here;
behaviour unchanged, same precedence smart_zones_need_naming already
uses (smart_zone_data checked before legacy smart_zone_labels).

Pure functions taking plain dicts (vacuum_state, options) — no HA
imports beyond what const.py itself already needs.
"""
from __future__ import annotations

from typing import Any

from .const import CONF_SMART_ZONE_HIDDEN, extract_region_id


def collect_region_ids(
    vacuum_state: dict[str, Any], options: dict[str, Any],
) -> list[str]:
    """All known region_ids from live vacuum_state (cleanSchedule2,
    lastCommand) merged with persisted discovered_zone_ids from config
    entry options — the latter survive MQTT disconnection (e.g. when
    the iRobot app takes over the local connection).

    v3.4.0 bug-hunt fix: every field here is untrusted MQTT/options
    data. A malformed cleanSchedule2 entry (not a dict), a non-list
    "regions" value, or an options key holding an explicit None
    (dict.get(key, default) does NOT guard against that — only against
    a missing key, a pitfall already documented elsewhere in this
    project) used to raise AttributeError/TypeError here.
    """
    region_ids: set[str] = set()

    for entry in vacuum_state.get("cleanSchedule2") or []:
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("cmd") or {}
        if not isinstance(cmd, dict):
            continue
        regions = cmd.get("regions")
        if not isinstance(regions, list):
            continue
        for region in regions:
            rid = extract_region_id(region)
            if rid:
                region_ids.add(rid)

    last = vacuum_state.get("lastCommand") or {}
    if isinstance(last, dict):
        regions = last.get("regions")
        if isinstance(regions, list):
            for region in regions:
                rid = extract_region_id(region)
                if rid:
                    region_ids.add(rid)

    # explicit `or []`, not .get(key, []) — see docstring above.
    persisted = options.get("discovered_zone_ids") or []
    if isinstance(persisted, list):
        region_ids.update(str(p) for p in persisted if p)

    return sorted(region_ids, key=lambda x: x.zfill(4))


def unlabelled_zone_ids(
    vacuum_state: dict[str, Any], options: dict[str, Any],
) -> list[str]:
    """Region_ids with no user-assigned label yet, excluding hidden
    ones. Checks smart_zone_data first (new storage), falls back to
    smart_zone_labels (legacy) so existing installs aren't re-prompted
    — same precedence the smart_zones_need_naming repair issue uses.

    v3.4.0 bug-hunt fix: options.get(key, {}) does not guard against
    an explicit None value stored under that key (only a missing key)
    — set(None) raises TypeError. `or {}`/`or []` catches both cases.
    """
    zone_data = options.get("smart_zone_data") or {}
    labels = options.get("smart_zone_labels") or {}
    hidden_ids = options.get(CONF_SMART_ZONE_HIDDEN) or []
    named = set(zone_data) | set(labels)
    return [
        rid for rid in collect_region_ids(vacuum_state, options)
        if rid not in named and rid not in hidden_ids
    ]
