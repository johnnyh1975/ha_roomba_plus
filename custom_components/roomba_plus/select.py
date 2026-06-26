"""Select platform for Roomba+.

Dropdown settings that map to set_preference() delta commands:

  CleaningPassesSelect — Auto / One pass / Two passes
                         via noAutoPasses + twoPass preferences

Only created when the robot supports multi-pass (cap.multiPass present).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .const import (
    CLEAN_MODE_LABELS,
    DOMAIN,
    FAN_SPEED_AUTOMATIC,
    FAN_SPEED_ECO,
    FAN_SPEED_PERFORMANCE,
    FAN_SPEEDS,
    extract_region_id,
    has_carpet_boost,
    has_smart_map,
)
from .entity import IRobotEntity
from .models import MapCapability, RoombaConfigEntry

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



_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

# Option labels — must match CLEAN_MODE_LABELS values
OPT_AUTO = CLEAN_MODE_LABELS["auto"]    # "Auto"
OPT_ONE  = CLEAN_MODE_LABELS["one"]     # "One pass"
OPT_TWO  = CLEAN_MODE_LABELS["two"]     # "Two passes"

# Preference payloads per option
# noAutoPasses=False → auto decide; True → manual control
# twoPass=False → one pass; True → two passes
_OPTION_TO_PREFS: dict[str, tuple[bool, bool]] = {
    OPT_AUTO: (False, False),
    OPT_ONE:  (True,  False),
    OPT_TWO:  (True,  True),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up select entities."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)
    data = config_entry.runtime_data

    entities = []

    # Cleaning passes: present when noAutoPasses is in state
    if "noAutoPasses" in state:
        entities.append(CleaningPassesSelect(roomba, blid))

    # v2.2.0 — Carpet boost select (card fix P2)
    if has_carpet_boost(state):
        entities.append(CarpetBoostSelect(roomba, blid))

    # Zone select: only for EPHEMERAL (900-series with detected rooms).
    # ROOM-SEG Stage 3 — backed by RoomSegStore now, not ZoneStore (the
    # gap heuristic proved unreliable; same unique_id/entity_id kept for
    # a seamless transition, see ROOM_SEGMENTATION_NOTES.md).
    if data.map_capability == MapCapability.EPHEMERAL and data.room_seg_store:
        entities.append(ZoneSelect(roomba, blid, config_entry))

    # Smart Zone select: for Smart Map robots (i/s/j/m) with pmaps.
    # When cloud is active, cloud-sourced selects replace the repair-flow
    # based SmartZoneSelect — the repair issue is suppressed in that case.
    if has_smart_map(state):
        if data.has_cloud:
            cc = data.cloud_coordinator
            active_pmap_id = cc.active_pmap_id  # type: ignore[union-attr]
            for pmap in cc.data.get("pmaps", []):  # type: ignore[union-attr]
                details = pmap.get("active_pmapv_details", {})
                pmap_id = details.get("active_pmapv", {}).get("pmap_id", "")
                map_name = details.get("map_header", {}).get("name", "Map")
                regions = details.get("regions", [])
                zones = details.get("zones", [])
                is_active = (pmap_id == active_pmap_id)
                if regions or zones:
                    entities.append(
                        CloudSmartZoneSelect(
                            roomba, blid, config_entry,
                            pmap_id=pmap_id,
                            map_name=map_name,
                            regions=regions,
                            zones=zones,
                            is_active_map=is_active,
                        )
                    )
        else:
            entities.append(SmartZoneSelect(roomba, blid, config_entry))

    # v1.9.0 — Braava Pad Wetness selects
    if "padWetness" in state:
        entities.append(DisposablePadWetnessSelect(roomba, blid))
        entities.append(ReusablePadWetnessSelect(roomba, blid))

    async_add_entities(entities)


class CleaningPassesSelect(IRobotEntity, SelectEntity):
    """Select entity for cleaning pass mode (Auto / One / Two passes).

    Maps to the noAutoPasses + twoPass preference pair.
    """

    _attr_translation_key = "cleaning_passes"
    _attr_name = "Setting – Cleaning passes"  # G6: locale-independent entity_id slug
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [OPT_AUTO, OPT_ONE, OPT_TWO]

    def __init__(self, roomba, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_cleaning_passes"

    @property
    def current_option(self) -> str | None:
        """Return the current pass mode."""
        no_auto = self.vacuum_state.get("noAutoPasses")
        two_pass = self.vacuum_state.get("twoPass")
        if no_auto is None or two_pass is None:
            return None
        if no_auto and two_pass:
            return OPT_TWO
        if no_auto and not two_pass:
            return OPT_ONE
        return OPT_AUTO

    async def async_select_option(self, option: str) -> None:
        """Apply a cleaning pass mode by sending two delta preferences."""
        prefs = _OPTION_TO_PREFS.get(option)
        if prefs is None:
            _LOGGER.error("CleaningPasses: unknown option %r", option)
            return

        no_auto, two_pass = prefs
        _LOGGER.debug(
            "CleaningPasses: setting option=%r → noAutoPasses=%s twoPass=%s",
            option, no_auto, two_pass,
        )
        # Each set_preference sends a separate delta — order matters:
        # set noAutoPasses first, then twoPass
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "noAutoPasses", no_auto
        )
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference, "twoPass", two_pass
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "noAutoPasses" in new_state or "twoPass" in new_state


class ZoneSelect(IRobotEntity, SelectEntity):
    """Select entity for choosing which detected room to clean next.

    Only created for EPHEMERAL map robots (900-series) after at least one
    room has been detected and confirmed by the user. The option list is
    rebuilt whenever RoomSegStore changes.

    Selecting a room does not immediately start cleaning — the user presses
    the ZoneCleanButton (button.py) to trigger the actual mission.

    ROOM-SEG Stage 3 — backed by RoomSegStore's watershed-based room
    segmentation, not ZoneStore's gap-heuristic zones (the latter proved
    unreliable in practice — see ROOM_SEGMENTATION_NOTES.md). unique_id/
    entity_id/translation_key are all unchanged from the ZoneStore-backed
    version, so existing dashboards and automations keep working without
    any user-visible reconfiguration.

    Inherits from IRobotEntity for correct DeviceInfo (multi-Roomba safe).
    """

    _attr_translation_key = "zone_select"
    _attr_name = "Select zone"  # G6: locale-independent entity_id slug
    _attr_entity_category = None   # primary control → Steuerelemente

    def __init__(
        self,
        roomba,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)
        self._config_entry = config_entry
        self._selected: str | None = None
        self._attr_unique_id = f"{self.robot_unique_id}_zone_select"

    @property
    def _room_seg_store(self) -> Any:
        return self._config_entry.runtime_data.room_seg_store

    @property
    def options(self) -> list[str]:
        """Return confirmed, non-hidden room names as options.

        Hidden rooms are excluded so they don't appear in selectors or
        trigger the clean_zone automation surface.
        """
        if not self._room_seg_store:
            return []
        return [
            r.name for r in self._room_seg_store.rooms.values()
            if r.confirmed and not r.hidden
        ]

    @property
    def current_option(self) -> str | None:
        """Return currently selected zone, reset if no longer valid."""
        if self._selected not in self.options:
            self._selected = self.options[0] if self.options else None
        return self._selected

    async def async_select_option(self, option: str) -> None:
        self._selected = option
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return bool(self.options)


class SmartZoneSelect(IRobotEntity, SelectEntity):
    """Zone selector for Smart Map robots (i/s/j/m-series).

    Collects known region_ids from two local sources:
      1. cleanSchedule2 — regions used in scheduled missions
      2. lastCommand    — region used in the last mission

    Region names are not available locally (they live in cloud pmaps).
    User-assigned names are stored in the config entry options under
    'smart_zone_labels' (dict mapping region_id → label).

    When new region_ids are discovered that have no user label yet, a
    HA Repair Issue ('smart_zones_need_naming') is raised. The fix flow
    opens the Options Flow async_step_smart_zones step where the user
    can assign names. The issue is automatically dismissed once all
    known region_ids have labels.

    Selecting a zone and pressing the companion SmartZoneCleanButton
    (button.py) starts a targeted clean of that region.
    """

    _attr_translation_key = "smart_zone_select"
    _attr_name = "Select Smart Map zone"  # G6: locale-independent entity_id slug
    _attr_entity_category = None   # primary control → Steuerelemente

    def __init__(
        self,
        roomba,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_smart_zone_select"
        self._selected: str | None = None
        # Track which region_ids we have already raised an issue for so we
        # don't fire async_create_issue on every subsequent MQTT message.
        self._known_unlabelled: set[str] = set()

    async def async_added_to_hass(self) -> None:
        """Run initial unlabelled check once the entity is registered.

        on_message() only fires on MQTT delta updates, never for the
        initial state loaded at startup. Region IDs already in
        cleanSchedule2 at startup would not trigger the Repair Issue
        until the robot sends a cleanSchedule2 delta. This check
        ensures the issue fires on every HA startup when needed.
        """
        await super().async_added_to_hass()
        unlabelled = set(self._unlabelled_region_ids())
        if unlabelled:
            self._known_unlabelled = unlabelled
            _LOGGER.info(
                "SmartZoneSelect: %d unlabelled region_id(s) at startup: %s",
                len(unlabelled),
                sorted(unlabelled),
            )
            await self._async_raise_naming_issue(sorted(unlabelled))

    # ── Region ID collection ──────────────────────────────────────────────────

    def _collect_region_ids(self) -> list[str]:
        """Collect all known region_ids from local state and persisted options.

        Reads live vacuum_state first (cleanSchedule2, lastCommand), then
        merges with discovered_zone_ids from config entry options so that
        previously seen IDs remain visible even after MQTT connection is lost
        (e.g. when the iRobot app takes over the local connection).
        """
        region_ids: set[str] = set()

        # From cleanSchedule2
        for entry in self.vacuum_state.get("cleanSchedule2", []):
            cmd = entry.get("cmd", {})
            for region in cmd.get("regions", []):
                rid = extract_region_id(region)
                if rid:
                    region_ids.add(rid)

        # From lastCommand
        last = self.vacuum_state.get("lastCommand", {})
        for region in (last.get("regions") or []):
            rid = extract_region_id(region)
            if rid:
                region_ids.add(rid)

        # From persisted discovered_zone_ids — survives MQTT disconnection.
        persisted = self._config_entry.options.get("discovered_zone_ids", [])
        region_ids.update(persisted)

        return sorted(region_ids, key=lambda x: x.zfill(4))

    def _unlabelled_region_ids(self) -> list[str]:
        """Return region_ids that have no user-assigned label yet.

        Excludes hidden zone IDs — no repair issue should fire for hidden zones.
        Checks smart_zone_data first (new storage), then falls back to
        smart_zone_labels (legacy) so existing installs aren't re-prompted.
        """
        from .const import CONF_SMART_ZONE_HIDDEN
        options = self._config_entry.options
        zone_data: dict = options.get("smart_zone_data", {})
        labels: dict = options.get("smart_zone_labels", {})
        hidden_ids: list = options.get(CONF_SMART_ZONE_HIDDEN, [])
        named = set(zone_data) | set(labels)
        return [
            rid for rid in self._collect_region_ids()
            if rid not in named and rid not in hidden_ids
        ]

    def _region_label(self, region_id: str) -> str:
        """Return display name using 5-level priority chain (v1.7.0 L7).

        1. User alias (CONF_SMART_ZONE_ALIASES)
        2. Cloud name (not available in SmartZoneSelect — cloud path uses CloudSmartZoneSelect)
        3. smart_zone_data name (manual entry)
        4. smart_zone_labels (legacy fallback)
        5. Auto-generated "Zone {id}"
        """
        from .const import CONF_SMART_ZONE_ALIASES
        options = self._config_entry.options
        aliases: dict = options.get(CONF_SMART_ZONE_ALIASES, {})
        zone_data: dict = options.get("smart_zone_data", {})
        labels: dict = options.get("smart_zone_labels", {})
        local_name = zone_data.get(region_id, {}).get("name") if region_id in zone_data else None
        return resolve_zone_name(region_id, aliases, None, local_name, labels)

    # ── SelectEntity interface ────────────────────────────────────────────────

    @property
    def options(self) -> list[str]:
        """Return labelled options list, excluding hidden zones."""
        from .const import CONF_SMART_ZONE_HIDDEN
        hidden_ids: list = self._config_entry.options.get(CONF_SMART_ZONE_HIDDEN, [])
        return [
            self._region_label(rid)
            for rid in self._collect_region_ids()
            if rid not in hidden_ids
        ]

    @property
    def current_option(self) -> str | None:
        if not self.options:
            return None
        if self._selected not in self.options:
            self._selected = self.options[0]
        return self._selected

    @property
    def region_ids(self) -> list[str]:
        """Return raw region_ids in same order as options."""
        return self._collect_region_ids()

    @property
    def selected_region_id(self) -> str | None:
        """Return the region_id for the currently selected option."""
        ids = self._collect_region_ids()
        labels = [self._region_label(rid) for rid in ids]
        if self._selected in labels:
            idx = labels.index(self._selected)
            return ids[idx]
        return ids[0] if ids else None

    @property
    def selected_pmap_info(self) -> dict:
        """Return pmap_id and user_pmapv_id from the most recent known source."""
        # Try lastCommand first (most recent)
        last = self.vacuum_state.get("lastCommand", {})
        if last.get("pmap_id"):
            return {
                "pmap_id": last["pmap_id"],
                "user_pmapv_id": last.get("user_pmapv_id", ""),
            }
        # Fall back to cleanSchedule2
        for entry in self.vacuum_state.get("cleanSchedule2", []):
            cmd = entry.get("cmd", {})
            if cmd.get("pmap_id"):
                return {
                    "pmap_id": cmd["pmap_id"],
                    "user_pmapv_id": cmd.get("user_pmapv_id", ""),
                }
        return {}

    async def async_select_option(self, option: str) -> None:
        self._selected = option
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return bool(self._collect_region_ids())

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict) -> bool:
        return "cleanSchedule2" in new_state or "lastCommand" in new_state

    def on_message(self, json_data: dict[str, Any]) -> None:
        """Handle MQTT update — check for newly discovered region_ids.

        When new region_ids appear that have no user label yet, a HA Repair
        Issue is raised to prompt the user to name them. The issue is only
        created when the set of unlabelled IDs actually grows, so it fires
        at most once per newly discovered region rather than on every message.
        """
        state = json_data.get("state", {}).get("reported", {})
        if not self.new_state_filter(state):
            return

        self.vacuum_state = roomba_reported_state(self.vacuum)
        self.schedule_update_ha_state()

        # Check for newly unlabelled region_ids
        unlabelled = set(self._unlabelled_region_ids())
        new_unlabelled = unlabelled - self._known_unlabelled
        if new_unlabelled:
            self._known_unlabelled = unlabelled
            _LOGGER.info(
                "SmartZoneSelect: %d new unlabelled region_id(s) discovered: %s",
                len(new_unlabelled),
                sorted(new_unlabelled),
            )
            # Capture the IDs NOW while vacuum_state is fresh — by the time
            # the async task runs the MQTT connection may have dropped and
            # vacuum_state may no longer contain the regions.
            captured = sorted(new_unlabelled)
            self.hass.loop.call_soon_threadsafe(
                lambda ids=captured: self.hass.async_create_task(
                    self._async_raise_naming_issue(ids)
                )
            )

        # Dismiss issue when all region_ids have been labelled
        elif self._known_unlabelled and not unlabelled:
            self._known_unlabelled = set()
            self.hass.loop.call_soon_threadsafe(
                lambda: self.hass.async_create_task(
                    self._async_dismiss_naming_issue()
                )
            )

    async def _async_raise_naming_issue(self, region_ids: list[str]) -> None:
        """Create (or update) the smart_zones_need_naming Repair Issue.

        Suppressed when the cloud coordinator is active — the cloud provides
        authoritative region names so the manual naming flow is not needed.
        """
        from homeassistant.helpers import issue_registry as ir

        if not region_ids:
            return

        # If cloud is active, names come from cloud pmaps — no repair needed.
        if self._config_entry.runtime_data.has_cloud:
            _LOGGER.debug(
                "SmartZoneSelect: cloud active — suppressing naming repair issue"
            )
            return

        # Persist discovered IDs to options so the repair fix flow can
        # read them even when live MQTT state no longer has regions.
        new_options = dict(self._config_entry.options)
        existing_ids = set(new_options.get("discovered_zone_ids", []))
        new_options["discovered_zone_ids"] = sorted(existing_ids | set(region_ids))
        self.hass.config_entries.async_update_entry(
            self._config_entry, options=new_options
        )
        # Exclude hidden zone IDs from the repair issue — users have explicitly
        # chosen to hide these zones and should not be prompted to name them.
        from .const import CONF_SMART_ZONE_HIDDEN
        hidden_ids: set = set(new_options.get(CONF_SMART_ZONE_HIDDEN, []))
        unlabelled = [
            rid for rid in new_options["discovered_zone_ids"]
            if rid not in hidden_ids
        ]
        if not unlabelled:
            return  # All remaining zones are hidden — no issue needed

        # Issue ID includes entry_id so multi-robot setups open the correct fix flow.
        issue_id = f"smart_zones_need_naming_{self._config_entry.entry_id}"
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="smart_zones_need_naming",
            translation_placeholders={
                "zone_count": str(len(unlabelled)),
                "zone_ids": ", ".join(unlabelled),
            },
        )
        _LOGGER.debug(
            "SmartZoneSelect: repair issue raised for %d region_id(s)",
            len(unlabelled),
        )

    async def _async_dismiss_naming_issue(self) -> None:
        """Dismiss the smart_zones_need_naming issue once all IDs are labelled."""
        from homeassistant.helpers import issue_registry as ir

        issue_id = f"smart_zones_need_naming_{self._config_entry.entry_id}"
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        _LOGGER.debug("SmartZoneSelect: repair issue dismissed — all zones labelled")


class CloudSmartZoneSelect(IRobotEntity, SelectEntity):
    """Zone selector for Smart Map robots populated from the iRobot cloud.

    Replaces SmartZoneSelect when cloud credentials are configured.
    Regions and zones come from the /pmaps UMF endpoint — names, types, and
    pmap_id are authoritative and require no manual naming by the user.

    One entity is created per pmap (floor). On robots with a single map this
    means one entity named "Select zone — <MapName>". Multi-floor robots get
    one per floor with the map name disambiguating them.

    Because this entity's data comes from the cloud coordinator (not MQTT
    push), it updates when the coordinator refreshes (map retrain detection
    or the daily background poll), not on every MQTT message.

    The companion SmartZoneButton reads selected_region_id / selected_pmap_id
    from this entity — the interface is identical to SmartZoneSelect so the
    button requires no changes.
    """

    _attr_entity_category = None   # primary control — visible by default

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
        *,
        pmap_id: str,
        map_name: str,
        regions: list[dict[str, Any]],
        zones: list[dict[str, Any]],
        is_active_map: bool = True,
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._pmap_id = pmap_id
        self._regions = regions   # list of {id, name, region_type}
        self._zones = zones       # list of {id, name, zone_type}
        self._is_active_map = is_active_map
        self._selected: str | None = None

        self._attr_unique_id = f"{self.robot_unique_id}_cloud_zone_{pmap_id}"
        self._attr_translation_key = "cloud_smart_zone_select"
        self._attr_name = "Select zone (cloud)"  # G6: locale-independent entity_id slug

        # Inactive maps: disabled by default, name suffixed so users know why.
        # Active map: enabled by default, name unchanged.
        self._map_name = map_name if is_active_map else f"{map_name} (inactive)"
        self._attr_entity_registry_enabled_default = is_active_map

    # ── Option list ───────────────────────────────────────────────────────────

    def _all_items(self) -> list[dict[str, Any]]:
        """Return combined regions + zones with alias and hidden-filter applied (v1.7.0 L7).

        Name resolution uses 5-level priority: alias > cloud > local > labels > auto.
        Hidden region IDs are excluded entirely.
        """
        from .const import CONF_SMART_ZONE_ALIASES, CONF_SMART_ZONE_HIDDEN
        options = self._config_entry.options
        aliases: dict = options.get(CONF_SMART_ZONE_ALIASES, {})
        hidden_ids: list = options.get(CONF_SMART_ZONE_HIDDEN, [])
        labels: dict = options.get("smart_zone_labels", {})
        zone_data: dict = options.get("smart_zone_data", {})

        items = []
        for r in self._regions:
            rid = str(r.get("id", ""))
            if rid in hidden_ids:
                continue
            cloud_name = r.get("name")
            local_name = zone_data.get(rid, {}).get("name") if rid in zone_data else None
            name = resolve_zone_name(rid, aliases, cloud_name, local_name, labels)
            items.append({"id": rid, "name": name, "pmap_id": self._pmap_id})
        for z in self._zones:
            zid = str(z.get("id", ""))
            if zid in hidden_ids:
                continue
            cloud_name = z.get("name")
            local_name = zone_data.get(zid, {}).get("name") if zid in zone_data else None
            name = resolve_zone_name(zid, aliases, cloud_name, local_name, labels)
            items.append({"id": zid, "name": name, "pmap_id": self._pmap_id})
        return items

    @property
    def options(self) -> list[str]:
        return [item["name"] for item in self._all_items()]

    @property
    def current_option(self) -> str | None:
        opts = self.options
        if not opts:
            return None
        if self._selected not in opts:
            self._selected = opts[0]
        return self._selected

    async def async_select_option(self, option: str) -> None:
        self._selected = option
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return bool(self.options)

    # ── Data used by SmartZoneButton ──────────────────────────────────────────

    @property
    def selected_region_id(self) -> str | None:
        """Return the region/zone id for the currently selected option."""
        for item in self._all_items():
            if item["name"] == self._selected:
                return item["id"]
        items = self._all_items()
        return items[0]["id"] if items else None

    @property
    def selected_pmap_info(self) -> dict[str, str]:
        """Return {pmap_id, user_pmapv_id} — compatible with SmartZoneSelect."""
        # user_pmapv_id is intentionally left empty here: SmartZoneButton
        # always re-reads it from live MQTT state via _resolve_pmapv_id.
        return {"pmap_id": self._pmap_id, "user_pmapv_id": ""}

    # ── Extra attributes ──────────────────────────────────────────────────────

    @property
    def icon(self) -> str:
        """F7g -- dynamic icon based on selected zone region_type."""
        from .const import REGION_TYPE_ICONS
        current = self._selected
        for r in self._regions:
            if r.get("name") == current or str(r.get("id")) == current:
                region_type = r.get("region_type", "default")
                return REGION_TYPE_ICONS.get(region_type, REGION_TYPE_ICONS["default"])
        return REGION_TYPE_ICONS["default"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        from .const import REGION_TYPE_ICONS
        items = self._all_items()
        selected = next(
            (i for i in items if i["name"] == self._selected), items[0] if items else {}
        )

        # F7g -- region_icons: maps each zone display name to its MDI icon
        region_icons: dict[str, str] = {}
        for r in self._regions:
            region_type = r.get("region_type", "default")
            icon = REGION_TYPE_ICONS.get(region_type, REGION_TYPE_ICONS["default"])
            name = next(
                (i["name"] for i in items if str(i.get("id")) == str(r.get("id"))),
                r.get("name", ""),
            )
            if name:
                region_icons[name] = icon

        # F7g -- learning_percentage from map_header
        learning_pct = None
        try:
            cc = self._config_entry.runtime_data.cloud_coordinator
            if cc and cc.data:
                for pmap in cc.data.get("pmaps", []):
                    details = pmap.get("active_pmapv_details", {})
                    if details.get("active_pmapv", {}).get("pmap_id") == self._pmap_id:
                        learning_pct = details.get("map_header", {}).get("learning_percentage")
                        break
        except Exception:
            pass

        # ROOM-SIZE (v2.9.1) -- region_areas_m2: maps each zone display name
        # to its floor area in m^2, from UmfAligner.room_areas_m2. Only
        # populated for whichever pmap UmfAligner was actually built for
        # (the active map at config-entry setup, see __init__.py) -- other
        # floors' CloudSmartZoneSelect instances simply get an empty dict,
        # same graceful-degradation pattern as region_icons/learning_pct.
        region_areas_m2: dict[str, float] = {}
        try:
            aligner = self._config_entry.runtime_data.umf_aligner
            if aligner is not None:
                areas_by_rid = aligner.room_areas_m2
                for r in self._regions:
                    rid = str(r.get("id", ""))
                    if rid not in areas_by_rid:
                        continue
                    name = next(
                        (i["name"] for i in items if str(i.get("id")) == rid),
                        r.get("name", ""),
                    )
                    if name:
                        region_areas_m2[name] = areas_by_rid[rid]
        except Exception:
            pass

        attrs = {
            "map_name": self._map_name,
            "pmap_id": self._pmap_id,
            "region_id": selected.get("id"),
            "region_count": len(self._regions),
            "zone_count": len(self._zones),
            "source": "cloud",
            "is_active_map": self._is_active_map,
            "region_icons": region_icons,
        }
        if learning_pct is not None:
            attrs["learning_percentage"] = learning_pct
        if region_areas_m2:
            attrs["region_areas_m2"] = region_areas_m2

        # v2.3.0 Gap A (Amendment 4 v2.2 backfill) — keepout zone visibility
        try:
            cc = self._config_entry.runtime_data.cloud_coordinator
            if cc:
                keepout = cc.keepout_zones
                attrs["keepout_zone_count"] = len(keepout)
                names = [z.get("name") for z in keepout if z.get("name")]
                if names:
                    attrs["keepout_zone_names"] = names
        except Exception:  # noqa: BLE001
            pass

        return attrs

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Cloud entity doesn't update from MQTT — coordinator handles refresh.
        return False


# ── v1.9.0 — Braava Pad Wetness ───────────────────────────────────────────────

_PAD_WET_OPTIONS: list[str] = ["1", "2", "3"]


class DisposablePadWetnessSelect(IRobotEntity, SelectEntity):
    """Select entity: disposable pad wetness level for Braava (1–3).

    Reads padWetness.disposable from MQTT state.
    Writes via set_preference('padWetness', {disposable: level}).

    When writing, the current value of the other key (reusable) is preserved
    by reading it from the live MQTT state — never blindly overwritten.

    Options are the iRobot-internal integers as strings ("1", "2", "3") so
    that translation via state-keys in strings.json works correctly.

    Only created when 'padWetness' dict is present in the initial state.
    """

    _attr_translation_key = "disposable_pad_wetness"
    _attr_name = "Disposable pad wetness"  # G6: locale-independent entity_id slug
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _PAD_WET_OPTIONS

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_disposable_pad_wetness"

    @property
    def current_option(self) -> str | None:
        val = self.vacuum_state.get("padWetness", {}).get("disposable")
        return str(val) if val is not None else None

    async def async_select_option(self, option: str) -> None:
        level = int(option)
        current = self.vacuum_state.get("padWetness", {})
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference,
            "padWetness",
            {"disposable": level, "reusable": current.get("reusable", level)},
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "padWetness" in new_state


class ReusablePadWetnessSelect(IRobotEntity, SelectEntity):
    """Select entity: reusable pad wetness level for Braava (1–3).

    Reads padWetness.reusable from MQTT state.
    Writes via set_preference('padWetness', {reusable: level}).

    When writing, the current value of disposable is preserved by reading
    it from the live MQTT state — never blindly overwritten.

    Only created when 'padWetness' dict is present in the initial state.
    """

    _attr_translation_key = "reusable_pad_wetness"
    _attr_name = "Reusable pad wetness"  # G6: locale-independent entity_id slug
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _PAD_WET_OPTIONS

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_reusable_pad_wetness"

    @property
    def current_option(self) -> str | None:
        val = self.vacuum_state.get("padWetness", {}).get("reusable")
        return str(val) if val is not None else None

    async def async_select_option(self, option: str) -> None:
        level = int(option)
        current = self.vacuum_state.get("padWetness", {})
        await self.hass.async_add_executor_job(
            self.vacuum.set_preference,
            "padWetness",
            {"disposable": current.get("disposable", level), "reusable": level},
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "padWetness" in new_state


class CarpetBoostSelect(IRobotEntity, SelectEntity):
    """Carpet boost mode select — wraps vacuum.set_fan_speed for card control.

    Card fix P2 — select.*_carpet_boost_select.

    Reads carpet boost state from carpetBoost/vacHigh flags (same logic as the
    carpet_boost_mode sensor). Writes by calling vacuum.set_fan_speed via the
    HA service layer — keeping all delta-command logic in RoombaVacuumCarpetBoost.

    Gate: registered only when has_carpet_boost(state) is True.
    """

    _attr_translation_key = "carpet_boost_select"
    _attr_name            = "Carpet boost"   # G6: locale-independent entity_id slug
    _attr_options = FAN_SPEEDS          # ["Automatic", "Eco", "Performance"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_carpet_boost_select"

    @property
    def current_option(self) -> str | None:
        carpet_boost = self.vacuum_state.get("carpetBoost")
        vac_high     = self.vacuum_state.get("vacHigh")
        if carpet_boost is None or vac_high is None:
            return None
        if carpet_boost:
            return FAN_SPEED_AUTOMATIC
        if vac_high:
            return FAN_SPEED_PERFORMANCE
        return FAN_SPEED_ECO

    async def async_select_option(self, option: str) -> None:
        """Set carpet boost mode via vacuum.set_fan_speed service."""
        if option not in FAN_SPEEDS:
            import logging
            logging.getLogger(__name__).error(
                "CarpetBoostSelect: unknown option %r", option
            )
            return
        from homeassistant.helpers import entity_registry as er
        reg = er.async_get(self.hass)
        # Find the vacuum entity for this device (unique_id == blid)
        vac_entry = reg.async_get_entity_id("vacuum", "roomba_plus", self._blid)
        if vac_entry is None:
            import logging
            logging.getLogger(__name__).error(
                "CarpetBoostSelect: no vacuum entity found for blid=%s", self._blid
            )
            return
        await self.hass.services.async_call(
            "vacuum", "set_fan_speed",
            {"entity_id": vac_entry, "fan_speed": option},
            blocking=False,
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "carpetBoost" in new_state or "vacHigh" in new_state
