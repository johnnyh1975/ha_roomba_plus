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
    has_carpet_boost,
    has_smart_map,
)
from .entity import IRobotEntity
from .models import MapCapability, RoombaConfigEntry
from .zone_naming import collect_region_ids, unlabelled_zone_ids

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

_PAD_WET_OPTIONS: list[str] = ["1", "2", "3"]  # Braava wetness levels (disposable + reusable)

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

    # v3.2.1 REMOVED — ZoneSelect used to be created for EPHEMERAL
    # (900-series) robots too. Confirmed dead weight, not just
    # incomplete: its only consumer anywhere in this codebase was
    # ZoneCleanButton, which read the selection purely to log it, then
    # sent the exact same plain "start" command regardless of what was
    # selected (the 900-series MQTT API has no coordinate/region
    # targeting at all — confirmed independently via rest980's docs,
    # via this project's own mission_archive data showing pmap_id=None
    # on every EPHEMERAL mission ever recorded, and via ZoneCleanButton's
    # own implementation). No automation, template sensor, or other
    # entity in this integration reads zone_select's state either. A
    # selector with zero functional consumers and a button that ignores
    # it is worse than no control at all — it suggests targeted-room
    # cleaning is possible on hardware that architecturally cannot do
    # it. See ZoneCleanButton's removal below for the matching half of
    # this fix.
    #
    # SMART-tier robots (i/s/j/m with pmaps) are unaffected — see
    # SmartZoneSelect / CloudSmartZoneSelect below, which DO have a real
    # region-targeting command path.

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


# ── F-RB-6 (v3.0.0) — Descriptor pattern for simple MQTT-backed selects ──────
#
# Replaces CleaningPassesSelect, DisposablePadWetnessSelect,
# ReusablePadWetnessSelect, CarpetBoostSelect with a single generic class
# + four frozen dataclass descriptors.

from dataclasses import dataclass, field as _field
from typing import Callable, Coroutine
from homeassistant.components.select import SelectEntityDescription


@dataclass(frozen=True, kw_only=True)
class RoombaPlusSelectDescription(SelectEntityDescription):
    """Descriptor for a simple MQTT-backed select entity (F-RB-6)."""
    unique_id_suffix: str
    options: list[str]
    current_option_fn: Callable[[dict[str, Any]], str | None]
    select_fn: Callable[["SimpleRoombaSelect", str], Coroutine]
    state_filter_keys: tuple[str, ...]


class SimpleRoombaSelect(IRobotEntity, SelectEntity):
    """Generic MQTT-backed select — driven by RoombaPlusSelectDescription.

    Replaces the four separate simple select classes (F-RB-6, v3.0.0):
    CleaningPassesSelect, DisposablePadWetnessSelect,
    ReusablePadWetnessSelect, CarpetBoostSelect.
    """

    entity_description: RoombaPlusSelectDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, roomba: Any, blid: str, description: RoombaPlusSelectDescription) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._attr_options = list(description.options)
        self._attr_unique_id = f"{self.robot_unique_id}_{description.unique_id_suffix}"

    @property
    def current_option(self) -> str | None:
        return self.entity_description.current_option_fn(self.vacuum_state)

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.select_fn(self, option)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return any(k in new_state for k in self.entity_description.state_filter_keys)


# ── select_fn helpers (named coroutines — can't be lambdas) ──────────────────

async def _select_cleaning_passes(entity: SimpleRoombaSelect, option: str) -> None:
    prefs = _OPTION_TO_PREFS.get(option)
    if prefs is None:
        _LOGGER.error("CleaningPasses: unknown option %r", option)
        return
    no_auto, two_pass = prefs
    _LOGGER.debug(
        "CleaningPasses: option=%r → noAutoPasses=%s twoPass=%s", option, no_auto, two_pass
    )
    await entity.hass.async_add_executor_job(entity.vacuum.set_preference, "noAutoPasses", no_auto)
    await entity.hass.async_add_executor_job(entity.vacuum.set_preference, "twoPass", two_pass)


async def _select_disposable_wetness(entity: SimpleRoombaSelect, option: str) -> None:
    level = int(option)
    current = entity.vacuum_state.get("padWetness", {})
    await entity.hass.async_add_executor_job(
        entity.vacuum.set_preference,
        "padWetness",
        {"disposable": level, "reusable": current.get("reusable", level)},
    )


async def _select_reusable_wetness(entity: SimpleRoombaSelect, option: str) -> None:
    level = int(option)
    current = entity.vacuum_state.get("padWetness", {})
    await entity.hass.async_add_executor_job(
        entity.vacuum.set_preference,
        "padWetness",
        {"disposable": current.get("disposable", level), "reusable": level},
    )


async def _select_carpet_boost(entity: SimpleRoombaSelect, option: str) -> None:
    """v3.1.0 CARPET-BOOST-SLUG-FIX: case-insensitive so existing automations
    calling select.select_option with the old Capital-Case value ("Automatic")
    keep working after FAN_SPEEDS moved to lowercase slugs.
    """
    canonical = option.lower()
    if canonical not in FAN_SPEEDS:
        _LOGGER.error("CarpetBoostSelect: unknown option %r", option)
        return
    from homeassistant.helpers import entity_registry as er
    reg = er.async_get(entity.hass)
    vac_entry = reg.async_get_entity_id("vacuum", "roomba_plus", entity._blid)
    if vac_entry is None:
        _LOGGER.error("CarpetBoostSelect: no vacuum entity for blid=%s", entity._blid)
        return
    await entity.hass.services.async_call(
        "vacuum", "set_fan_speed",
        {"entity_id": vac_entry, "fan_speed": canonical},
        blocking=False,
    )


# ── Descriptor instances ──────────────────────────────────────────────────────

_CLEANING_PASSES_DESC = RoombaPlusSelectDescription(
    key="cleaning_passes",
    translation_key="cleaning_passes",
    name="Setting – Cleaning passes",
    unique_id_suffix="cleaning_passes",
    options=[OPT_AUTO, OPT_ONE, OPT_TWO],
    current_option_fn=lambda state: (
        OPT_TWO   if (state.get("noAutoPasses") and state.get("twoPass"))  else
        OPT_ONE   if (state.get("noAutoPasses") and not state.get("twoPass")) else
        OPT_AUTO  if (state.get("noAutoPasses") is not None) else None
    ),
    select_fn=_select_cleaning_passes,
    state_filter_keys=("noAutoPasses", "twoPass"),
)

_DISPOSABLE_PAD_DESC = RoombaPlusSelectDescription(
    key="disposable_pad_wetness",
    translation_key="disposable_pad_wetness",
    name="Disposable pad wetness",
    unique_id_suffix="disposable_pad_wetness",
    options=_PAD_WET_OPTIONS,
    current_option_fn=lambda state: (
        str(v) if (v := (state.get("padWetness") or {}).get("disposable")) is not None else None
    ),
    select_fn=_select_disposable_wetness,
    state_filter_keys=("padWetness",),
)

_REUSABLE_PAD_DESC = RoombaPlusSelectDescription(
    key="reusable_pad_wetness",
    translation_key="reusable_pad_wetness",
    name="Reusable pad wetness",
    unique_id_suffix="reusable_pad_wetness",
    options=_PAD_WET_OPTIONS,
    current_option_fn=lambda state: (
        str(v) if (v := (state.get("padWetness") or {}).get("reusable")) is not None else None
    ),
    select_fn=_select_reusable_wetness,
    state_filter_keys=("padWetness",),
)

_CARPET_BOOST_DESC = RoombaPlusSelectDescription(
    key="carpet_boost_select",
    translation_key="carpet_boost_select",
    name="Carpet boost",
    unique_id_suffix="carpet_boost_select",
    options=list(FAN_SPEEDS),
    current_option_fn=lambda state: (
        FAN_SPEED_AUTOMATIC if state.get("carpetBoost")
        else FAN_SPEED_PERFORMANCE if state.get("vacHigh")
        else FAN_SPEED_ECO if state.get("carpetBoost") is not None
        else None
    ),
    select_fn=_select_carpet_boost,
    state_filter_keys=("carpetBoost", "vacHigh"),
)


# ── Compatibility subclasses ──────────────────────────────────────────────────
# Thin subclasses keep the original class names so that async_setup_entry
# and existing tests continue to work without changes.

class CleaningPassesSelect(SimpleRoombaSelect):
    """Descriptor-backed CleaningPassesSelect (F-RB-6)."""
    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid, _CLEANING_PASSES_DESC)


class DisposablePadWetnessSelect(SimpleRoombaSelect):
    """Descriptor-backed DisposablePadWetnessSelect (F-RB-6)."""
    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid, _DISPOSABLE_PAD_DESC)


class ReusablePadWetnessSelect(SimpleRoombaSelect):
    """Descriptor-backed ReusablePadWetnessSelect (F-RB-6)."""
    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid, _REUSABLE_PAD_DESC)


class CarpetBoostSelect(SimpleRoombaSelect):
    """Descriptor-backed CarpetBoostSelect (F-RB-6)."""
    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid, _CARPET_BOOST_DESC)





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
        """Back-compat wrapper (v3.4.0 TODO extraction) — see
        zone_naming.py::collect_region_ids() for the actual logic,
        shared with the new todo.py platform."""
        return collect_region_ids(self.vacuum_state, self._config_entry.options)

    def _unlabelled_region_ids(self) -> list[str]:
        """Back-compat wrapper — see zone_naming.py::unlabelled_zone_ids()."""
        return unlabelled_zone_ids(self.vacuum_state, self._config_entry.options)

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
        except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
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


