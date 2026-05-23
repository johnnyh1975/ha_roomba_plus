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
from .const import CLEAN_MODE_LABELS, DOMAIN
from .entity import IRobotEntity
from .const import has_smart_map
from .models import MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

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

    # Zone select: only for EPHEMERAL (900-series with detected zones)
    if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
        entities.append(ZoneSelect(roomba, blid, config_entry))

    # Smart Zone select: for Smart Map robots (i/s/j/m) with pmaps.
    # When cloud is active, cloud-sourced selects replace the repair-flow
    # based SmartZoneSelect — the repair issue is suppressed in that case.
    if has_smart_map(state):
        if data.has_cloud:
            # Cloud-sourced: one select per pmap, entities know their region/zone list.
            cc = data.cloud_coordinator
            for pmap in cc.data.get("pmaps", []):  # type: ignore[union-attr]
                details = pmap.get("active_pmapv_details", {})
                pmap_id = details.get("active_pmapv", {}).get("pmap_id", "")
                map_name = details.get("map_header", {}).get("name", "Map")
                regions = details.get("regions", [])
                zones = details.get("zones", [])
                if regions or zones:
                    entities.append(
                        CloudSmartZoneSelect(
                            roomba, blid, config_entry,
                            pmap_id=pmap_id,
                            map_name=map_name,
                            regions=regions,
                            zones=zones,
                        )
                    )
        else:
            # MQTT-only fallback: names come from the repair flow / options.
            entities.append(SmartZoneSelect(roomba, blid, config_entry))

    async_add_entities(entities)


class CleaningPassesSelect(IRobotEntity, SelectEntity):
    """Select entity for cleaning pass mode (Auto / One / Two passes).

    Maps to the noAutoPasses + twoPass preference pair.
    """

    _attr_translation_key = "cleaning_passes"
    _attr_icon = "mdi:replay"
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
    """Select entity for choosing which detected zone to clean next.

    Only created for EPHEMERAL map robots (900-series) after at least one
    zone has been detected and confirmed by the user. The option list is
    rebuilt whenever the ZoneStore changes.

    Selecting a zone does not immediately start cleaning — the user presses
    the ZoneCleanButton (button.py) to trigger the actual mission.

    Inherits from IRobotEntity for correct DeviceInfo (multi-Roomba safe).
    """

    _attr_translation_key = "zone_select"
    _attr_icon = "mdi:map-marker-outline"
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
    def _zone_store(self):
        return self._config_entry.runtime_data.zone_store

    @property
    def options(self) -> list[str]:
        """Return confirmed zone names as options."""
        if not self._zone_store:
            return []
        return [z.name for z in self._zone_store.zones if z.confirmed]

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
    _attr_icon = "mdi:map-marker-outline"
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
                rid = region.get("region_id")
                if rid is not None:
                    region_ids.add(str(rid))

        # From lastCommand
        last = self.vacuum_state.get("lastCommand", {})
        for region in (last.get("regions") or []):
            rid = region.get("region_id")
            if rid is not None:
                region_ids.add(str(rid))

        # From persisted discovered_zone_ids — survives MQTT disconnection.
        persisted = self._config_entry.options.get("discovered_zone_ids", [])
        region_ids.update(persisted)

        return sorted(region_ids, key=lambda x: x.zfill(4))

    def _unlabelled_region_ids(self) -> list[str]:
        """Return region_ids that have no user-assigned label yet.

        Checks smart_zone_data first (new storage), then falls back to
        smart_zone_labels (legacy) so existing installs aren't re-prompted.
        """
        zone_data: dict = self._config_entry.options.get("smart_zone_data", {})
        labels: dict = self._config_entry.options.get("smart_zone_labels", {})
        named = set(zone_data) | set(labels)
        return [rid for rid in self._collect_region_ids() if rid not in named]

    def _region_label(self, region_id: str) -> str:
        """Return user-assigned name or auto-generated label.

        Reads smart_zone_data first (written by the new config_flow step),
        falls back to the older flat smart_zone_labels dict for entries
        created before this version.
        """
        zone_data: dict = self._config_entry.options.get("smart_zone_data", {})
        if region_id in zone_data:
            return zone_data[region_id].get("name", f"Zone {region_id}")
        labels: dict = self._config_entry.options.get("smart_zone_labels", {})
        return labels.get(region_id, f"Zone {region_id}")

    # ── SelectEntity interface ────────────────────────────────────────────────

    @property
    def options(self) -> list[str]:
        """Return labelled options list."""
        return [self._region_label(rid) for rid in self._collect_region_ids()]

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
        unlabelled = new_options["discovered_zone_ids"]

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            "smart_zones_need_naming",
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

        ir.async_delete_issue(self.hass, DOMAIN, "smart_zones_need_naming")
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

    _attr_icon = "mdi:map-marker-outline"
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
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._pmap_id = pmap_id
        self._map_name = map_name
        self._regions = regions   # list of {id, name, region_type}
        self._zones = zones       # list of {id, name, zone_type}
        self._selected: str | None = None

        # unique_id includes pmap_id so multi-floor robots get separate entities
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_zone_{pmap_id}"
        self._attr_translation_key = "cloud_smart_zone_select"

    # ── Option list ───────────────────────────────────────────────────────────

    def _all_items(self) -> list[dict[str, Any]]:
        """Return combined regions + zones, each with id/name/pmap_id."""
        items = []
        for r in self._regions:
            name = r.get("name") or f"Zone {r.get('id', '?')}"
            items.append({"id": str(r.get("id", "")), "name": name, "pmap_id": self._pmap_id})
        for z in self._zones:
            name = z.get("name") or f"Zone {z.get('id', '?')}"
            items.append({"id": str(z.get("id", "")), "name": name, "pmap_id": self._pmap_id})
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
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._all_items()
        selected = next(
            (i for i in items if i["name"] == self._selected), items[0] if items else {}
        )
        return {
            "map_name": self._map_name,
            "pmap_id": self._pmap_id,
            "region_id": selected.get("id"),
            "region_count": len(self._regions),
            "zone_count": len(self._zones),
            "source": "cloud",
        }

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Cloud entity doesn't update from MQTT — coordinator handles refresh.
        return False
