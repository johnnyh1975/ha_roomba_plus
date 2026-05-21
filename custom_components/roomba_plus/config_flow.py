"""Config flow for the Roomba+ integration."""
from __future__ import annotations

import asyncio
from functools import partial
import logging
from typing import Any

from roombapy import RoombaFactory, RoombaInfo
from roombapy.discovery import RoombaDiscovery
from roombapy.getpassword import RoombaPassword
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_DELAY, CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import CannotConnect, async_connect_or_timeout, async_disconnect_or_timeout
from .const import (
    CONF_BLID,
    CONF_CONTINUOUS,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DOMAIN,
    ROOMBA_SESSION,
)
from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

# ── Discovery constants ───────────────────────────────────────────────────────
ROOMBA_DISCOVERY_LOCK = "roomba_plus_discovery_lock"
ALL_ATTEMPTS = 2
HOST_ATTEMPTS = 6
ROOMBA_WAKE_TIME = 6
MAX_NUM_DEVICES_TO_DISCOVER = 25

AUTH_HELP_URL_KEY = "auth_help_url"
AUTH_HELP_URL_VALUE = (
    "https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials"
)

DEFAULT_OPTIONS = {CONF_CONTINUOUS: DEFAULT_CONTINUOUS, CONF_DELAY: DEFAULT_DELAY}


# ── Input validation ──────────────────────────────────────────────────────────

async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate credentials by attempting a real connection.

    Returns dict containing the robot name and session on success.
    Raises CannotConnect when the device is unreachable or credentials fail.
    """
    roomba = await hass.async_add_executor_job(
        partial(
            RoombaFactory.create_roomba,
            address=data[CONF_HOST],
            blid=data[CONF_BLID],
            password=data[CONF_PASSWORD],
            continuous=True,
            delay=data[CONF_DELAY],
        )
    )

    info = await async_connect_or_timeout(hass, roomba)
    await async_disconnect_or_timeout(hass, roomba)

    return {
        ROOMBA_SESSION: info[ROOMBA_SESSION],
        CONF_NAME: info[CONF_NAME],
        CONF_HOST: data[CONF_HOST],
    }


# ── Config Flow ───────────────────────────────────────────────────────────────

class RoombaPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roomba+.

    Supports auto-discovery via DHCP and Zeroconf, push-button linking,
    and full manual fallback with explicit password entry.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self.name: str | None = None
        self.blid: str = ""
        self.host: str | None = None
        self.discovered_robots: dict[str, RoombaInfo] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: RoombaConfigEntry,
    ) -> RoombaPlusOptionsFlow:
        """Return the options flow handler for this config entry."""
        return RoombaPlusOptionsFlow()

    # ── Discovery entry points ─────────────────────────────────────────────

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle Zeroconf discovery."""
        return await self._async_step_discovery(
            discovery_info.host,
            discovery_info.hostname.lower().removesuffix(".local."),
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle DHCP discovery."""
        return await self._async_step_discovery(
            discovery_info.ip, discovery_info.hostname
        )

    async def _async_step_discovery(
        self, ip_address: str, hostname: str
    ) -> ConfigFlowResult:
        """Shared handler for DHCP and Zeroconf discovery."""
        self._async_abort_entries_match({CONF_HOST: ip_address})

        if not hostname.startswith(("irobot-", "roomba-")):
            return self.async_abort(reason="not_irobot_device")

        self.host = ip_address
        self.blid = _async_blid_from_hostname(hostname)
        await self.async_set_unique_id(self.blid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: ip_address})

        # Guard against duplicate flows with truncated hostnames
        for progress in self._async_in_progress():
            flow_unique_id = progress["context"].get("unique_id", "")
            if flow_unique_id.startswith(self.blid):
                return self.async_abort(reason="short_blid")
            if self.blid.startswith(flow_unique_id):
                self.hass.config_entries.flow.async_abort(progress["flow_id"])

        self.context["title_placeholders"] = {
            "host": self.host,
            "name": self.blid,
        }
        return await self.async_step_user()

    # ── User-facing steps ──────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial UI step.

        Shows discovered robots (if any) or falls through to manual entry.
        """
        if user_input is not None:
            if not user_input.get(CONF_HOST):
                return await self.async_step_manual()

            if user_input[CONF_HOST] in self.discovered_robots:
                self.host = user_input[CONF_HOST]
                return await self._async_start_link()

        already_configured = self._async_current_ids(False)
        devices = await _async_discover_roombas(self.hass, self.host)

        if devices:
            self.discovered_robots = {
                device.ip: device
                for device in devices
                if device.blid not in already_configured
            }

        if self.host and self.host in self.discovered_robots:
            self.context["title_placeholders"] = {
                "host": self.host,
                "name": self.discovered_robots[self.host].robot_name,
            }
            return await self._async_start_link()

        if not self.discovered_robots:
            return await self.async_step_manual()

        hosts: dict[str | None, str] = {
            **{
                device.ip: f"{device.robot_name} ({device.ip})"
                for device in devices
                if device.blid not in already_configured
            },
            None: "Add manually",
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_HOST): vol.In(hosts)}),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual host entry (no auto-discovery)."""
        if user_input is None:
            return self.async_show_form(
                step_id="manual",
                description_placeholders={AUTH_HELP_URL_KEY: AUTH_HELP_URL_VALUE},
                data_schema=vol.Schema(
                    {vol.Required(CONF_HOST, default=self.host): str}
                ),
            )

        self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
        self.host = user_input[CONF_HOST]

        devices = await _async_discover_roombas(self.hass, self.host)
        if not devices:
            return self.async_abort(reason="cannot_connect")

        self.blid = devices[0].blid
        self.name = devices[0].robot_name

        await self.async_set_unique_id(self.blid, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return await self.async_step_link()

    async def _async_start_link(self) -> ConfigFlowResult:
        """Start push-button linking from a discovered robot."""
        assert self.host
        device = self.discovered_robots[self.host]
        self.blid = device.blid
        self.name = device.robot_name
        await self.async_set_unique_id(self.blid, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return await self.async_step_link()

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to press and hold the HOME button to retrieve the password."""
        if user_input is None:
            return self.async_show_form(
                step_id="link",
                description_placeholders={CONF_NAME: self.name or self.blid},
            )

        assert self.host
        roomba_pw = RoombaPassword(self.host)

        try:
            password = await self.hass.async_add_executor_job(roomba_pw.get_password)
        except OSError:
            return await self.async_step_link_manual()

        if not password:
            return await self.async_step_link_manual()

        config = {
            CONF_HOST: self.host,
            CONF_BLID: self.blid,
            CONF_PASSWORD: password,
            **DEFAULT_OPTIONS,
        }

        if not self.name:
            try:
                info = await validate_input(self.hass, config)
            except CannotConnect:
                return self.async_abort(reason="cannot_connect")
            self.name = info[CONF_NAME]

        assert self.name
        return self.async_create_entry(title=self.name, data=config)

    async def async_step_link_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow manually entering the password when push-button link fails."""
        errors: dict[str, str] = {}

        if user_input is not None:
            config = {
                CONF_HOST: self.host,
                CONF_BLID: self.blid,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                **DEFAULT_OPTIONS,
            }
            try:
                info = await validate_input(self.hass, config)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=info[CONF_NAME], data=config)

        return self.async_show_form(
            step_id="link_manual",
            description_placeholders={AUTH_HELP_URL_KEY: AUTH_HELP_URL_VALUE},
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


# ── Options Flow ──────────────────────────────────────────────────────────────

class RoombaPlusOptionsFlow(OptionsFlow):
    """Handle Roomba+ options (connection settings)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options menu — connection settings or Smart Map zone entry."""
        from .const import has_smart_map
        from . import roomba_reported_state

        state = roomba_reported_state(self.config_entry.runtime_data.roomba)

        if has_smart_map(state):
            # Smart Map robots get a menu: connection settings or manual zone entry.
            return self.async_show_menu(
                step_id="init",
                menu_options=["settings", "smart_zones_manual"],
            )

        # Non-Smart-Map robots go directly to settings form.
        return await self.async_step_settings(user_input)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connection and map settings form."""
        if user_input is not None:
            updated = dict(self.config_entry.options)
            updated.update(user_input)
            return self.async_create_entry(title="", data=updated)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_CONTINUOUS,
                        default=options.get(CONF_CONTINUOUS, DEFAULT_CONTINUOUS),
                    ): bool,
                    vol.Optional(
                        CONF_DELAY,
                        default=options.get(CONF_DELAY, DEFAULT_DELAY),
                    ): int,
                    vol.Optional(
                        CONF_MAP_ENABLED,
                        default=options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED),
                    ): bool,
                    vol.Optional(
                        CONF_MAP_SIZE_PX,
                        default=options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                    ): vol.All(int, vol.Range(min=400, max=1200)),
                    vol.Optional(
                        CONF_MAP_SCALE,
                        default=options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE),
                    ): vol.All(float, vol.Range(min=5.0, max=30.0)),
                }
            ),
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zone naming step — triggered by the Repair Issue after mission end.

        Dynamically generates one text field per unconfirmed zone.
        """
        from .zone_store import ZoneStore
        from .models import MapCapability

        data = self.config_entry.runtime_data
        if data.map_capability != MapCapability.EPHEMERAL or not data.zone_store:
            return self.async_create_entry(title="", data=self.config_entry.options)

        zone_store: ZoneStore = data.zone_store
        unconfirmed = zone_store.unconfirmed_zones

        if user_input is not None:
            for zone in unconfirmed:
                name = user_input.get(f"zone_{zone.id}", "").strip()
                if name:
                    zone_store.rename_zone(zone.id, name)
            # Persist
            self.hass.async_create_task(
                zone_store.async_save(self.hass, self.config_entry.entry_id)
            )
            return self.async_create_entry(title="", data=self.config_entry.options)

        if not unconfirmed:
            return self.async_create_entry(title="", data=self.config_entry.options)

        schema = vol.Schema({
            vol.Optional(f"zone_{z.id}", default=z.name): str
            for z in unconfirmed
        })
        return self.async_show_form(
            step_id="zones",
            data_schema=schema,
            description_placeholders={"zone_count": str(len(unconfirmed))},
        )


    async def async_step_smart_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Smart Map zone naming step — triggered by the Repair Issue.

        Dynamically generates one text field per unlabelled region_id.
        Saves user-assigned names to config_entry.options["smart_zone_labels"].
        """
        from .const import has_smart_map

        from . import roomba_reported_state
        state = roomba_reported_state(self.config_entry.runtime_data.roomba)
        if not has_smart_map(state):
            return self.async_create_entry(title="", data=self.config_entry.options)

        # Collect all known region_ids from local state
        region_ids: set[str] = set()
        for entry in state.get("cleanSchedule2", []):
            for region in entry.get("cmd", {}).get("regions", []):
                rid = region.get("region_id")
                if rid:
                    region_ids.add(rid)
        last = state.get("lastCommand", {})
        for region in (last.get("regions") or []):
            rid = region.get("region_id")
            if rid:
                region_ids.add(rid)

        existing_labels: dict = self.config_entry.options.get(
            "smart_zone_labels", {}
        )
        unlabelled = sorted(rid for rid in region_ids if rid not in existing_labels)

        if user_input is not None:
            new_labels = dict(existing_labels)
            new_zone_data: dict = dict(
                self.config_entry.options.get("smart_zone_data", {})
            )

            # Capture pmap_id from live state at naming time.
            # Priority: lastCommand > cleanSchedule2 > first entry in pmaps.
            # The pmaps fallback covers the case where the user has only done
            # full-home cleans so lastCommand contains no pmap_id, but the
            # robot still reports its map ID in state.pmaps.
            current_pmap_id: str = ""
            last = state.get("lastCommand", {})
            if last.get("pmap_id"):
                current_pmap_id = last["pmap_id"]
            else:
                for entry in state.get("cleanSchedule2", []):
                    cmd = entry.get("cmd", {})
                    if cmd.get("pmap_id"):
                        current_pmap_id = cmd["pmap_id"]
                        break
            if not current_pmap_id:
                pmaps: list[dict] = state.get("pmaps", [])
                if pmaps:
                    current_pmap_id = next(iter(pmaps[0]), "")

            for rid in unlabelled:
                label = user_input.get(f"zone_{rid}", "").strip()
                if label:
                    new_labels[rid] = label
                    # Build per-region pmap_id: prefer a region-specific match
                    # from lastCommand if available, otherwise use current_pmap_id.
                    pmap_for_rid = current_pmap_id
                    if last.get("pmap_id") and any(
                        r.get("region_id") == rid
                        for r in (last.get("regions") or [])
                    ):
                        pmap_for_rid = last["pmap_id"]
                    new_zone_data[rid] = {
                        "name": label,
                        "pmap_id": pmap_for_rid,
                    }

            new_options = dict(self.config_entry.options)
            # Write both keys: smart_zone_labels for backward compat,
            # smart_zone_data for the clean_room action.
            new_options["smart_zone_labels"] = new_labels
            new_options["smart_zone_data"] = new_zone_data
            return self.async_create_entry(title="", data=new_options)

        if not unlabelled:
            return self.async_create_entry(title="", data=self.config_entry.options)

        schema = vol.Schema({
            vol.Optional(f"zone_{rid}", default=f"Zone {rid}"): str
            for rid in unlabelled
        })
        return self.async_show_form(
            step_id="smart_zones",
            data_schema=schema,
            description_placeholders={
                "zone_count": str(len(unlabelled)),
                "zone_ids": ", ".join(unlabelled),
            },
        )

    async def async_step_smart_zones_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual Smart Map zone entry — breaks the bootstrap circular dependency.

        When HA has never seen a room-specific clean via MQTT it has no region
        IDs and cannot populate the Smart Map select or use clean_room. This
        step lets the user enter region IDs and names directly from the iRobot
        app or the HA diagnostics dump, without requiring a connected MQTT session
        that includes lastCommand with regions.

        The user enters:
          - region_ids: comma-separated list of region ID strings (e.g. "5,12,7")
          - one name field per entered region ID (generated on re-entry)

        pmap_id is resolved automatically from live state.pmaps so the user
        does not need to find it manually.
        """
        from .const import has_smart_map
        from . import roomba_reported_state

        state = roomba_reported_state(self.config_entry.runtime_data.roomba)
        if not has_smart_map(state):
            return self.async_create_entry(title="", data=self.config_entry.options)

        errors: dict[str, str] = {}
        existing_labels: dict = self.config_entry.options.get("smart_zone_labels", {})
        existing_zone_data: dict = self.config_entry.options.get("smart_zone_data", {})

        # Two-phase flow:
        # Phase 1 — user enters comma-separated region IDs ("region_ids" key present)
        # Phase 2 — user names each ID (only "name_*" keys present)
        # Phases are distinguished by key presence, not value, to avoid ambiguity.

        if user_input is not None and "region_ids" in user_input:
            # Phase 1 submitted
            raw = user_input["region_ids"]
            pending = [r.strip() for r in raw.replace(",", " ").split() if r.strip()]
            if not pending:
                errors["region_ids"] = "no_valid_ids"
            else:
                # Advance to phase 2 — show one name field per ID
                schema = vol.Schema({
                    vol.Optional(f"name_{rid}", default=existing_labels.get(rid, f"Zone {rid}")): str
                    for rid in pending
                })
                return self.async_show_form(
                    step_id="smart_zones_manual",
                    data_schema=schema,
                    description_placeholders={
                        "zone_ids": ", ".join(pending),
                        "zone_count": str(len(pending)),
                    },
                    last_step=True,
                )

        elif user_input is not None and any(k.startswith("name_") for k in user_input):
            # Phase 2 submitted — resolve pmap_id and save
            current_pmap_id = ""
            last = state.get("lastCommand", {})
            if last.get("pmap_id"):
                current_pmap_id = last["pmap_id"]
            else:
                for entry in state.get("cleanSchedule2", []):
                    if entry.get("cmd", {}).get("pmap_id"):
                        current_pmap_id = entry["cmd"]["pmap_id"]
                        break
            if not current_pmap_id:
                pmaps: list[dict] = state.get("pmaps", [])
                if pmaps:
                    current_pmap_id = next(iter(pmaps[0]), "")

            new_labels = dict(existing_labels)
            new_zone_data = dict(existing_zone_data)
            new_discovered = list(self.config_entry.options.get("discovered_zone_ids", []))

            for key, label in user_input.items():
                if key.startswith("name_"):
                    rid = key[len("name_"):]
                    label = label.strip()
                    if label:
                        new_labels[rid] = label
                        new_zone_data[rid] = {"name": label, "pmap_id": current_pmap_id}
                        if rid not in new_discovered:
                            new_discovered.append(rid)

            new_options = dict(self.config_entry.options)
            new_options["smart_zone_labels"] = new_labels
            new_options["smart_zone_data"] = new_zone_data
            new_options["discovered_zone_ids"] = sorted(new_discovered)
            return self.async_create_entry(title="", data=new_options)

        # Phase 1 form — enter region IDs
        return self.async_show_form(
            step_id="smart_zones_manual",
            data_schema=vol.Schema({
                vol.Required("region_ids"): str,
            }),
            description_placeholders={},
            errors=errors,
            last_step=False,
        )

    async def async_step_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Door-width calibration — adjusts map scale from measured door-gap widths."""
        from .models import MapCapability

        data = self.config_entry.runtime_data
        if data.map_capability == MapCapability.NONE or not data.zone_store:
            return self.async_create_entry(title="", data=self.config_entry.options)

        zone_store = data.zone_store
        renderer = data.renderer

        # Use the last known mission points from the renderer
        points_mm = renderer.points_mm if renderer else []
        measured = None
        if points_mm:
            measured = zone_store.calibrate_from_gaps(points_mm)

        if user_input is not None:
            known_width = float(user_input.get("door_width_mm", 875))
            if points_mm:
                zone_store.calibrate_from_gaps(points_mm, known_width)
            # Apply scale to renderer config
            new_options = dict(self.config_entry.options)
            new_options[CONF_MAP_SCALE] = zone_store._scale_factor * DEFAULT_MAP_SCALE
            return self.async_create_entry(title="", data=new_options)

        placeholders = {
            "measured_cm": (
                f"{measured * zone_store._scale_factor / 10:.0f}"
                if measured else "—"
            ),
        }
        return self.async_show_form(
            step_id="calibration",
            data_schema=vol.Schema({
                vol.Optional("door_width_mm", default=875): vol.In(
                    {875: "Standard (875 mm, DIN 18101)",
                     750: "Narrow (750 mm)",
                     1000: "Wide (1000 mm)"}
                ),
            }),
            description_placeholders=placeholders,
        )


# ── Discovery helpers ─────────────────────────────────────────────────────────

@callback
def _async_get_roomba_discovery() -> RoombaDiscovery:
    """Create a RoombaDiscovery instance capped at MAX_NUM_DEVICES_TO_DISCOVER."""
    discovery = RoombaDiscovery()
    discovery.amount_of_broadcasted_messages = MAX_NUM_DEVICES_TO_DISCOVER
    return discovery


@callback
def _async_blid_from_hostname(hostname: str) -> str:
    """Extract the BLID from a discovery hostname like 'roomba-XXYYZZ'."""
    return hostname.split("-")[1].split(".", maxsplit=1)[0].upper()


async def _async_discover_roombas(
    hass: HomeAssistant, host: str | None = None
) -> list[RoombaInfo]:
    """Discover Roomba devices on the local network.

    When host is given, targets that specific IP; otherwise broadcasts.
    Uses a per-hass lock to avoid concurrent discovery floods.
    """
    discovered_hosts: set[str] = set()
    devices: list[RoombaInfo] = []
    discover_lock: asyncio.Lock = hass.data.setdefault(
        ROOMBA_DISCOVERY_LOCK, asyncio.Lock()
    )
    discover_attempts = HOST_ATTEMPTS if host else ALL_ATTEMPTS

    for attempt in range(discover_attempts + 1):
        async with discover_lock:
            discovery = _async_get_roomba_discovery()
            discovered: set[RoombaInfo] = set()
            try:
                if host:
                    device = await hass.async_add_executor_job(discovery.get, host)
                    if device:
                        discovered.add(device)
                else:
                    discovered = await hass.async_add_executor_job(discovery.get_all)
            except OSError:
                await asyncio.sleep(ROOMBA_WAKE_TIME * attempt)
                continue
            else:
                for device in discovered:
                    if device.ip not in discovered_hosts:
                        discovered_hosts.add(device.ip)
                        devices.append(device)
            finally:
                discovery.server_socket.close()

        if host and host in discovered_hosts:
            return devices

        await asyncio.sleep(ROOMBA_WAKE_TIME)

    return devices
