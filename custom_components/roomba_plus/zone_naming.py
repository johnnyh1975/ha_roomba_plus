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

from collections.abc import Mapping

from typing import Any

from .const import CONF_SMART_ZONE_HIDDEN, extract_region_id


def collect_region_ids(
    vacuum_state: Mapping[str, Any], options: Mapping[str, Any],
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
    vacuum_state: Mapping[str, Any], options: Mapping[str, Any],
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


def resolve_zone_name(
    region_id: str,
    aliases: dict[str, str],
    cloud_name: str | None,
    local_name: str | None,
    labels: dict[str, str],
) -> str:
    """5-level priority chain for SMART robot zone display names.

    Priority:
      1. aliases[region_id]   — user's local alias (overrides everything)
      2. cloud_name           — authoritative name from cloud coordinator
      3. local_name           — from smart_zone_data (manually entered)
      4. labels[region_id]    — legacy smart_zone_labels fallback
      5. f"Zone {region_id}"  — auto-generated placeholder
    """
    return (
        aliases.get(region_id)
        or cloud_name
        or local_name
        or labels.get(region_id)
        or f"Zone {region_id}"
    )


def map_room_label(
    region_id: str, cloud_name: str | None, options: Mapping[str, Any]
) -> str:
    """The text the rooms map writes into a room.

    THE SAME NAME AS EVERYWHERE ELSE: the user's alias, then the
    account's name, then the one typed into the naming notice. Only the
    last step differs from resolve_zone_name(): an unnamed room shows its
    bare number, not "Zone 21" -- the naming notice lists zones by number,
    and the map is where you find out which room that number is (the
    Prime rooms map has done this since 4.2.10, a tester).
    """
    from .const import CONF_SMART_ZONE_ALIASES

    aliases = options.get(CONF_SMART_ZONE_ALIASES) or {}
    zone_data = options.get("smart_zone_data") or {}
    labels = options.get("smart_zone_labels") or {}
    entry = zone_data.get(region_id)
    local_name = entry.get("name") if isinstance(entry, dict) else None
    return (
        (aliases.get(region_id) if isinstance(aliases, dict) else None)
        or cloud_name
        or local_name
        or (labels.get(region_id) if isinstance(labels, dict) else None)
        or region_id
    )


def room_display_name(
    region_id: str, cloud_name: str | None, options: Mapping[str, Any]
) -> str:
    """The name a room carries in the map attributes: the select's name.

    THE ROOM SELECT AND THE MAP NAME A ROOM THE SAME WAY (4.2.20, I12).
    The map's `rooms` attribute used the account's name or the bare id,
    the select the user's alias first -- so a renamed room was one thing
    in the list and another on the map, and the card could not match a
    tap on the map to an entry in the list. Same chain as
    resolve_zone_name(), "Zone N" included; the picture's own label is
    map_room_label() and differs only in showing a bare number.

    `cloud_name` equal to the id, or empty, is no name: the aligner
    falls back to the id for a region without one.
    """
    from .const import CONF_SMART_ZONE_ALIASES

    aliases = options.get(CONF_SMART_ZONE_ALIASES) or {}
    zone_data = options.get("smart_zone_data") or {}
    labels = options.get("smart_zone_labels") or {}
    entry = zone_data.get(region_id) if isinstance(zone_data, dict) else None
    local_name = entry.get("name") if isinstance(entry, dict) else None
    return resolve_zone_name(
        region_id,
        aliases if isinstance(aliases, dict) else {},
        cloud_name if cloud_name and cloud_name != region_id else None,
        local_name,
        labels if isinstance(labels, dict) else {},
    )
