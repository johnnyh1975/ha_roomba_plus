"""Button platform for Roomba+.

Two categories of buttons:

COMMAND BUTTONS — send a direct command to the robot:
  EvacButton      — trigger bin evacuation (Clean Base models only)
  LocateButton    — play find-me tone

EXPERIMENTAL COMMAND BUTTONS — disabled by default, enabled manually:
  SpotButton      — spot clean (small area around current position)
  QuickButton     — quick/shorter full-floor clean
  SleepButton     — send robot to low-power sleep
  OffButton       — power robot off completely

  These commands are confirmed present in the iRobot firmware protocol
  (documented in dorita980 issue #39 via Android app reverse engineering)
  but their exact behaviour across all firmware versions and models has
  not been fully verified. They are gated on MapCapability.EPHEMERAL
  (900-series) where they are known to work. Disabled by default so
  users must explicitly enable them — the standard HA pattern for
  features that are real but not yet fully validated.

MAINTENANCE RESET BUTTONS — record the current bbrun.hr as the new
  start point for remaining-life calculations:
  FilterResetButton  — mark filter as replaced
  BrushResetButton   — mark brushes as replaced
  BatteryResetButton — mark battery as replaced

Reset buttons are always created (every robot has a filter and brushes).
The reset value is persisted in hass.storage via MaintenanceStore.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .entity import IRobotEntity
from .models import MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


# ── Command buttons ───────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class RoombaButtonDescription(ButtonEntityDescription):
    """Command button: sends a direct command to the robot."""
    command: str
    filter_fn: Any = None  # callable(state) -> bool | None = always create
    entity_registry_enabled_default: bool = True  # False = experimental


COMMAND_BUTTONS: tuple[RoombaButtonDescription, ...] = (
    RoombaButtonDescription(
        key="evac",
        translation_key="evac",
        name="Action – Empty bin",
        entity_category=EntityCategory.CONFIG,
        command="evac",
        filter_fn=lambda s: s.get("cap", {}).get("dockComm") == 1,
    ),
    RoombaButtonDescription(
        key="locate",
        translation_key="locate",
        name="Action – Find robot",
        entity_category=EntityCategory.CONFIG,
        command="find",
        filter_fn=None,
    ),
    # ── v1.9.0 — Map Training Run ─────────────────────────────────────────────
    # Confirmed in dorita980 v2. Triggers a mapping survey without cleaning.
    # Only meaningful on SMART robots (i/s/j-series) that support persistent
    # pmaps — 980/EPHEMERAL don't benefit from a training run.
    # Enabled by default: intentional action, no side effects, clearly labelled.
    RoombaButtonDescription(
        key="map_training",
        translation_key="map_training",
        name="Action – Start map training",
        entity_category=EntityCategory.CONFIG,
        command="train",
        filter_fn=lambda s: bool(s.get("pmaps")),
        entity_registry_enabled_default=True,
    ),
    # ── Experimental — disabled by default ────────────────────────────────────
    # Confirmed in iRobot firmware protocol (dorita980 issue #39).
    # Gated on EPHEMERAL (900-series) where behaviour is known.
    # Enable manually via Settings → Devices & Services → Roomba+ → entity.
    RoombaButtonDescription(
        key="spot",
        translation_key="spot",
        name="Spot clean (experimental)",
        entity_category=EntityCategory.CONFIG,
        command="spot",
        filter_fn=lambda s: not s.get("pmaps"),  # EPHEMERAL: no persistent pmaps
        entity_registry_enabled_default=False,
    ),
    RoombaButtonDescription(
        key="quick",
        translation_key="quick",
        name="Quick clean (experimental)",
        entity_category=EntityCategory.CONFIG,
        command="quick",
        filter_fn=lambda s: not s.get("pmaps"),
        entity_registry_enabled_default=False,
    ),
    RoombaButtonDescription(
        key="sleep",
        translation_key="sleep",
        name="Sleep (experimental)",
        entity_category=EntityCategory.CONFIG,
        command="sleep",
        filter_fn=lambda s: not s.get("pmaps"),
        entity_registry_enabled_default=False,
    ),
    RoombaButtonDescription(
        key="power_off",
        translation_key="power_off",
        name="Power off (experimental)",
        entity_category=EntityCategory.CONFIG,
        command="off",
        filter_fn=lambda s: not s.get("pmaps"),
        entity_registry_enabled_default=False,
    ),
)


# ── Setup ─────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all button entities for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)

    entities: list[IRobotEntity] = []

    # Command buttons (capability-gated)
    entities.extend(
        RoombaCommandButton(roomba, blid, desc)
        for desc in COMMAND_BUTTONS
        if desc.filter_fn is None or desc.filter_fn(state)
    )

    # Maintenance reset buttons (always present)
    entities.extend([
        FilterResetButton(roomba, blid, config_entry),
        BrushResetButton(roomba, blid, config_entry),
        BatteryResetButton(roomba, blid, config_entry),
    ])

    # Zone clean button: EPHEMERAL only, appears after first zone detected
    data = config_entry.runtime_data
    if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
        entities.append(ZoneCleanButton(roomba, blid, config_entry))

    # Repeat last mission: whenever lastCommand is present in state
    if state.get("lastCommand"):
        entities.append(RepeatLastMissionButton(roomba, blid))

    # Smart zone button: for Smart Map robots (i/s/j/m-series)
    from .const import has_smart_map
    if has_smart_map(state):
        entities.append(SmartZoneButton(roomba, blid, config_entry))

    # Favorite buttons: one per saved iRobot routine, cloud robots only
    data = config_entry.runtime_data
    if data.has_cloud:
        favorites = data.cloud_coordinator.data.get("favorites", [])  # type: ignore[union-attr]
        for fav in favorites:
            entities.append(FavoriteButton(data.roomba, data.blid, config_entry, fav))

    async_add_entities(entities)


# ── Command button entity ─────────────────────────────────────────────────────

class RoombaCommandButton(IRobotEntity, ButtonEntity):
    """One-shot button that sends a direct command to the robot."""

    entity_description: RoombaButtonDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        description: RoombaButtonDescription,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"
        self._attr_entity_registry_enabled_default = description.entity_registry_enabled_default

    async def async_press(self) -> None:
        _LOGGER.debug("CommandButton: %s → %s",
                      self.entity_description.key,
                      self.entity_description.command)
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, self.entity_description.command
        )


# ── Maintenance reset button base ─────────────────────────────────────────────

class _MaintenanceResetButton(IRobotEntity, ButtonEntity):
    """Base class for maintenance reset buttons.

    On press: reads current bbrun.hr, calls the appropriate reset method
    on MaintenanceStore, then persists to hass.storage. The sensor entities
    will update on the next bbrun MQTT message.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry

    def _current_hr(self) -> int:
        """Return current bbrun.hr (lifetime operating hours)."""
        return self.vacuum_state.get("bbrun", {}).get("hr", 0)

    def _maintenance_store(self) -> Any:
        """Return the MaintenanceStore from runtime_data."""
        return self._config_entry.runtime_data.maintenance_store

    async def _save(self) -> None:
        """Persist the updated MaintenanceStore to hass.storage."""
        store = self._maintenance_store()
        if store:
            await store.async_save(self.hass, self._config_entry.entry_id)
        # Force sensor refresh so remaining hours update immediately
        self.schedule_update_ha_state()


class FilterResetButton(_MaintenanceResetButton):
    """Button: mark filter as replaced → restart filter-life countdown."""

    _attr_translation_key = "reset_filter"
    _attr_name            = "Reset filter"        # G6: locale-independent entity_id slug

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_filter"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("FilterResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_filter(hr)
            await self._save()


class BrushResetButton(_MaintenanceResetButton):
    """Button: mark brushes as replaced → restart brush-life countdown."""

    _attr_translation_key = "reset_brush"
    _attr_name            = "Reset brush"         # G6: locale-independent entity_id slug

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_brush"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("BrushResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_brush(hr)
            await self._save()


class BatteryResetButton(_MaintenanceResetButton):
    """Button: mark battery as replaced → restart battery-hour tracking."""

    _attr_translation_key = "reset_battery"
    _attr_name            = "Reset battery"       # G6: locale-independent entity_id slug

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_battery"

    async def async_press(self) -> None:
        hr = self._current_hr()
        _LOGGER.info("BatteryResetButton: reset at %dh", hr)
        store = self._maintenance_store()
        if store:
            store.reset_battery(hr)
            await self._save()


class ZoneCleanButton(_MaintenanceResetButton):
    """Button: start a clean with focus on the zone selected in ZoneSelect.

    For 900-series robots (EPHEMERAL capability), the MQTT API does not
    support coordinate-based navigation or region targeting. This button
    sends a standard start command from the robot's current position.

    The practical effect: if the robot is already docked and the user has
    selected a zone, the robot will start its normal coverage clean. The
    zone selection is informational — it does not steer the robot.

    The button is intentionally kept because the zone infrastructure
    (ZoneStore, ZoneSelect) is still useful for zone visualization,
    zone-aware automations, and the future serial OI navigation path.
    """

    _attr_translation_key = "clean_zone"
    _attr_name            = "Clean zone"          # G6: locale-independent entity_id slug
    _attr_entity_category = None   # visible by default — primary action

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_zone"

    async def async_press(self) -> None:
        """Start a clean. Zone selection is informational on 900-series robots."""
        zone_store = self._config_entry.runtime_data.zone_store
        if not zone_store or not zone_store.zones:
            _LOGGER.warning("ZoneCleanButton: no zones available yet")
            return

        confirmed = [z for z in zone_store.zones if z.confirmed]
        if not confirmed:
            _LOGGER.warning("ZoneCleanButton: no confirmed zones yet — run more missions")
            return

        # Find selected zone name via ZoneSelect entity state
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)
        blid = self._config_entry.data.get("blid", "")
        select_uid = f"roomba_plus_{blid}_zone_select"
        select_entry = ent_reg.async_get_entity_id("select", "roomba_plus", select_uid)

        selected_name: str | None = None
        if select_entry:
            state = self.hass.states.get(select_entry)
            if state:
                selected_name = state.state

        zone = next(
            (z for z in confirmed if z.name == selected_name),
            confirmed[0],
        )

        _LOGGER.info(
            "ZoneCleanButton: starting clean (900-series — no coordinate targeting). "
            "Selected zone: '%s' bbox %.0f,%.0f – %.0f,%.0f mm",
            zone.name, zone.x_min, zone.y_min, zone.x_max, zone.y_max,
        )
        # 900-series: send plain start — robot navigates using its own logic.
        # The zone parameter is noted in the log but cannot be passed to the robot.
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, "start"
        )


class RepeatLastMissionButton(IRobotEntity, ButtonEntity):
    """Button: repeat the last cleaning mission.

    Reads lastCommand from the robot state and resends it verbatim.
    For Smart Map robots (i/s/j): this repeats the exact same zone(s),
    including pmap_id, regions, and team_id.
    For 900-series or simple robots: sends a plain start command.

    No cloud access needed — lastCommand is part of the local MQTT state.
    """

    _attr_translation_key = "repeat_mission"
    _attr_name            = "Repeat last mission" # G6: locale-independent entity_id slug
    _attr_entity_category = None   # primary action → Steuerelemente

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_repeat_mission"

    async def async_press(self) -> None:
        last = self.vacuum_state.get("lastCommand", {})
        if not last:
            _LOGGER.warning("RepeatLastMission: no lastCommand in state")
            return

        command = last.get("command", "start")

        # Build params from lastCommand — include Smart Map fields if present
        params: dict = {}
        for key in ("pmap_id", "user_pmapv_id", "regions", "ordered", "params"):
            if key in last:
                params[key] = last[key]

        # If a pmap_id is present, refresh user_pmapv_id from live state.pmaps
        # to avoid silent failures after a map retrain.
        if params.get("pmap_id"):
            from .services import _resolve_pmapv_id
            fresh = _resolve_pmapv_id(self.vacuum_state, params["pmap_id"])
            if fresh:
                params["user_pmapv_id"] = fresh
            else:
                _LOGGER.warning(
                    "RepeatLastMission: pmap %s not in live state — map may have been retrained",
                    params["pmap_id"],
                )

        _LOGGER.info(
            "RepeatLastMission: sending %s params=%s", command, params or "(none)"
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, command, params or {}
        )

    def new_state_filter(self, new_state: dict) -> bool:
        return "lastCommand" in new_state


class SmartZoneButton(IRobotEntity, ButtonEntity):
    """Button: clean the zone selected in SmartZoneSelect.

    Builds a start command with pmap_id + region_id from the local state.
    No cloud access needed — all data comes from cleanSchedule2 / lastCommand.
    """

    _attr_translation_key = "clean_smart_zone"
    _attr_name            = "Clean Smart Map zone" # G6: locale-independent entity_id slug
    _attr_entity_category = None   # primary action — visible by default

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_clean_smart_zone"
        self._config_entry = config_entry

    async def async_press(self) -> None:
        """Find SmartZoneSelect state and start targeted clean.

        Uses the HA entity_platform helper to look up the SmartZoneSelect
        entity object directly — no hass.data hacks. Falls back to reading
        pmap/region from lastCommand if the select entity is not found.

        user_pmapv_id is always read from live state.pmaps at press time,
        never from lastCommand, to avoid stale-map silent failures.
        """
        from homeassistant.helpers import entity_platform as ep
        from .services import _resolve_pmapv_id

        region_id: str | None = None
        pmap_id: str | None = None

        # Walk all entity platforms registered under this domain to find
        # the zone select entity for this specific robot. We check for both:
        #   - SmartZoneSelect  uid = "{robot_unique_id}_smart_zone_select"
        #   - CloudSmartZoneSelect uid = "{robot_unique_id}_cloud_zone_{pmap_id}"
        # Rather than hardcoding uids, we match on robot_unique_id prefix and
        # presence of selected_region_id — works for both entity types.
        prefix = self.robot_unique_id
        for platform in ep.async_get_platforms(self.hass, "roomba_plus"):
            for entity in platform.entities.values():
                uid = getattr(entity, "unique_id", "") or ""
                if not uid.startswith(prefix):
                    continue
                if not hasattr(entity, "selected_region_id"):
                    continue
                # For CloudSmartZoneSelect, prefer the active map's entity.
                # active_map attr is set in v1.5.1+ — fall back gracefully.
                is_active = getattr(entity, "_is_active_map", True)
                if is_active and entity.selected_region_id:
                    region_id = entity.selected_region_id
                    # Also read pmap_id directly from cloud entity if available
                    pmap_info = getattr(entity, "selected_pmap_info", {})
                    if pmap_info.get("pmap_id"):
                        pmap_id = pmap_info["pmap_id"]
                    break
            if region_id:
                break

        # pmap_id resolution priority:
        #   1. Read directly from CloudSmartZoneSelect.selected_pmap_info (above)
        #   2. Stored in smart_zone_data options (MQTT-only path)
        #   3. lastCommand fallback (least reliable)
        if region_id and not pmap_id:
            zone_data: dict = self._config_entry.options.get("smart_zone_data", {})
            pmap_id = zone_data.get(str(region_id), {}).get("pmap_id") or None

        # Fallback: extract pmap/region from lastCommand when smart_zone_data
        # has no pmap_id (e.g. zone entered before fix) or entity lookup failed.
        if not region_id or not pmap_id:
            last = self.vacuum_state.get("lastCommand", {})
            if not pmap_id:
                pmap_id = last.get("pmap_id")
            if not region_id:
                regions = last.get("regions", [])
                if regions:
                    from .const import extract_region_id
                    region_id = extract_region_id(regions[0]) or None

        if not region_id or not pmap_id:
            _LOGGER.warning(
                "SmartZoneButton: no region/pmap available — run a zone mission first"
            )
            return

        # Guard: reject if the robot is currently updating its Smart Map.
        # notReady bit 6 (64) = map save/upload in progress — same guard as
        # the clean_room service action.
        not_ready: int = self.vacuum_state.get(
            "cleanMissionStatus", {}
        ).get("notReady", 0)
        if not_ready & 64:
            _LOGGER.warning(
                "SmartZoneButton: robot is updating Smart Map (notReady=%d) — "
                "wait for map update to complete before starting a zone clean",
                not_ready,
            )
            return

        # Always resolve user_pmapv_id from live state.pmaps — never cached.
        user_pmapv_id = _resolve_pmapv_id(self.vacuum_state, pmap_id)
        if not user_pmapv_id:
            _LOGGER.warning(
                "SmartZoneButton: pmap %s not found in live state — map may have been retrained",
                pmap_id,
            )
            return

        # Read cleaning pass mode from live robot state (same source as CleaningPassesSelect)
        _no_auto = bool(self.vacuum_state.get("noAutoPasses", False))
        _two_pass = bool(self.vacuum_state.get("twoPass", False))

        # user_pmapv_id intentionally omitted — see clean_room handler comment.
        params = {
            "pmap_id": pmap_id,
            "regions": [
                {
                    "region_id": str(region_id),
                    "type": "rid",
                    "params": {"noAutoPasses": _no_auto, "twoPass": _two_pass},
                }
            ],
            "ordered": 1,
        }
        _LOGGER.info(
            "SmartZoneButton: cleaning region %s on map %s",
            region_id, pmap_id[:12],
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command, "start", params
        )


class FavoriteButton(IRobotEntity, ButtonEntity):
    """Button that triggers a saved iRobot cleaning favorite.

    Favorites are multi-step cleaning routines defined in the iRobot app
    (e.g. "Monday morning: kitchen + hallway"). They are fetched from the
    cloud coordinator via /user/favorites and each contains a pre-built
    command payload that can be sent verbatim via send_command("start", ...).

    Inherits IRobotEntity so device_info is provided automatically and the
    button appears under the correct robot device — not as a phantom unnamed
    device entry.

    One entity per favorite. Hidden favorites (data["hidden"] == True) are
    disabled in the entity registry by default but can be enabled manually.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
        favorite: dict[str, Any],
    ) -> None:
        super().__init__(roomba, blid)
        fav_id: str = favorite.get("favorite_id", "")
        fav_name: str = favorite.get("name", fav_id)

        self._config_entry = config_entry
        self._favorite = favorite
        self._attr_name = fav_name
        self._attr_unique_id = f"{self.robot_unique_id}_fav_{fav_id}"
        self._attr_entity_registry_enabled_default = not favorite.get("hidden", False)

    async def async_press(self) -> None:
        """Send the favorite's command payload to the robot via local MQTT."""
        command_defs = self._favorite.get("commanddefs") or []
        if not command_defs:
            _LOGGER.warning(
                "FavoriteButton '%s': no commanddefs in favorite payload",
                self._attr_name,
            )
            return

        # commanddefs[0] is the primary command — mirrors roomba_rest980 behaviour.
        cmd = command_defs[0]
        command = cmd.get("command", "start")
        params = {k: v for k, v in cmd.items() if k != "command"}

        _LOGGER.info(
            "FavoriteButton: firing favorite '%s' → %s params=%s",
            self._attr_name, command, params or "(none)",
        )
        await self.hass.async_add_executor_job(
            self.vacuum.send_command,
            command,
            params,
        )
