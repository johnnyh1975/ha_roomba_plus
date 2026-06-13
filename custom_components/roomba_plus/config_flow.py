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

from . import CannotConnect, async_connect_or_timeout, async_disconnect_or_timeout, roomba_reported_state
from .cloud_api import AuthenticationError, CloudApiError, IrobotCloudApi
from .const import (
    CONF_AWAY_DELAY_MIN,
    CONF_BLID,
    CONF_BLOCKING_BEHAVIOR,
    CONF_BLOCKING_SENSORS,
    CONF_BLOCKING_TIMEOUT_MIN,
    CONF_CLEAN_DELAY_MIN,
    CONF_CONTINUOUS,
    CONF_DEMAND_CLEANING_ENABLED,
    CONF_DEMAND_MULTIPLIER,
    CONF_FLOOR,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRESENCE_ENTITIES,
    CONF_PRESENCE_MODE,
    CONF_PRESENCE_SCHEDULING_ENABLED,
    CONF_SMART_ZONE_ALIASES,
    CONF_SMART_ZONE_HIDDEN,
    DEFAULT_AWAY_DELAY_MIN,
    DEFAULT_BLOCKING_BEHAVIOR,
    DEFAULT_BLOCKING_TIMEOUT_MIN,
    DEFAULT_CLEAN_DELAY_MIN,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DEFAULT_PRESENCE_MODE,
    DOMAIN,
    ROOMBA_SESSION,
    extract_region_id,
    has_smart_map,
)
from .dirt_threshold_manager import TRIGGER_MULTIPLIER_DEFAULT
from .models import MapCapability, RoombaConfigEntry
from .zone_store import ZoneStore

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

    VERSION = 22

    def __init__(self) -> None:
        """Initialise the flow."""
        self.name: str | None = None
        self.blid: str = ""
        self.host: str | None = None
        self.discovered_robots: dict[str, RoombaInfo] = {}
        self._pending_config: dict[str, Any] = {}

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
        self._pending_config = config
        return await self.async_step_cloud_credentials()

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
                self.name = info[CONF_NAME]
                self._pending_config = config
                return await self.async_step_cloud_credentials()

        return self.async_show_form(
            step_id="link_manual",
            description_placeholders={AUTH_HELP_URL_KEY: AUTH_HELP_URL_VALUE},
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_cloud_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional iRobot account credentials for cloud features.

        Skipping leaves cloud_coordinator disabled — all local MQTT
        functionality continues to work normally.
        """

        errors: dict[str, str] = {}

        if user_input is not None:
            config = dict(self._pending_config)
            username = user_input.get(CONF_IROBOT_USERNAME, "").strip()
            password = user_input.get(CONF_IROBOT_PASSWORD, "").strip()
            if username and password:
                # Validate credentials before storing
                from homeassistant.helpers.aiohttp_client import async_get_clientsession
                api = IrobotCloudApi(username, password, async_get_clientsession(self.hass))
                try:
                    await api.authenticate()
                except AuthenticationError:
                    errors["base"] = "cloud_auth_failed"
                except CloudApiError:
                    errors["base"] = "cloud_unavailable"
                else:
                    config[CONF_IROBOT_USERNAME] = username
                    config[CONF_IROBOT_PASSWORD] = password
            if not errors:
                return self.async_create_entry(title=self.name, data=config)

        return self.async_show_form(
            step_id="cloud_credentials",
            data_schema=vol.Schema({
                vol.Optional(CONF_IROBOT_USERNAME, default=""): str,
                vol.Optional(CONF_IROBOT_PASSWORD, default=""): str,
            }),
            errors=errors,
            description_placeholders={},
        )


# ── Options Flow ──────────────────────────────────────────────────────────────


    # ── Reconfiguration flow ──────────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to change host or password without removing the entry.

        Validates the new connection before applying. BLID must match the stored
        robot — pointing to a different robot requires a new config entry.
        """
        errors: dict[str, str] = {}
        current = self._get_reconfigure_entry()

        if user_input is not None:
            new_host     = user_input[CONF_HOST].strip()
            new_password = user_input[CONF_PASSWORD].strip()

            config = {
                **current.data,
                CONF_HOST:     new_host,
                CONF_PASSWORD: new_password,
                **DEFAULT_OPTIONS,
            }
            try:
                await validate_input(self.hass, config)
            except CannotConnect:
                errors["base"] = "cannot_connect"

            if not errors:
                self.hass.config_entries.async_update_entry(
                    current,
                    data={**current.data, CONF_HOST: new_host, CONF_PASSWORD: new_password},
                )
                await self.hass.config_entries.async_reload(current.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=current.data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PASSWORD, default=""): str,
            }),
            errors=errors,
            description_placeholders={
                "name": current.data.get(CONF_BLID, ""),
            },
        )

class RoombaPlusOptionsFlow(OptionsFlow):
    """Handle Roomba+ options (connection settings)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Options menu — grouped by Connection / Scheduling / Map.

        CF1 (v2.6.0): three logical sections replace the previous flat list.
        CF4: zones and smart_zones are unified under a single "rooms" entry.
        """

        # Guard: runtime_data only exists after a successful async_setup_entry.
        if not hasattr(self.config_entry, "runtime_data") or self.config_entry.runtime_data is None:
            return self.async_abort(reason="integration_not_loaded")
        state = roomba_reported_state(self.config_entry.runtime_data.roomba)
        data = self.config_entry.runtime_data

        # ── ⚙  Connection ──────────────────────────────────────────────────
        menu: list[str] = ["settings"]
        if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("cloud_credentials")

        # ── 🗓  Scheduling ─────────────────────────────────────────────────
        menu.append("blocking_sensors")
        if "schedHold" in state:
            menu.append("presence_scheduling")
        if data.map_capability == MapCapability.SMART and data.has_cloud:
            menu.append("demand_cleaning")

        # ── 🗺  Map ────────────────────────────────────────────────────────
        if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("map_management")
        # CF4: single "rooms" entry routes internally to zones/smart_zones
        if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("rooms")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={},
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """CF4 — Route to zones or smart_zones based on map_capability."""
        data = self.config_entry.runtime_data
        if data.map_capability == MapCapability.SMART:
            return await self.async_step_smart_zones(user_input)
        return await self.async_step_zones(user_input)

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
                        default=float(options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE)),
                    ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                    vol.Optional(
                        CONF_FLOOR,
                        default=options.get(CONF_FLOOR, ""),
                    ): str,
                }
            ),
        )

    # ── v1.7.0 L5 — Blocking sensors configuration ───────────────────────────

    async def async_step_blocking_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure blocking sensors for the smart_start service."""
        from homeassistant.helpers import selector


        if user_input is not None:
            updated = dict(self.config_entry.options)
            updated[CONF_BLOCKING_SENSORS] = user_input.get(CONF_BLOCKING_SENSORS, [])
            updated[CONF_BLOCKING_BEHAVIOR] = user_input.get(CONF_BLOCKING_BEHAVIOR, DEFAULT_BLOCKING_BEHAVIOR)
            updated[CONF_BLOCKING_TIMEOUT_MIN] = int(user_input.get(CONF_BLOCKING_TIMEOUT_MIN, DEFAULT_BLOCKING_TIMEOUT_MIN))
            return self.async_create_entry(title="", data=updated)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="blocking_sensors",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_BLOCKING_SENSORS,
                    default=current.get(CONF_BLOCKING_SENSORS, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="binary_sensor",
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_BLOCKING_BEHAVIOR,
                    default=current.get(CONF_BLOCKING_BEHAVIOR, DEFAULT_BLOCKING_BEHAVIOR),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "abort", "label": "Abort start"},
                            {"value": "queue", "label": "Queue and wait"},
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_BLOCKING_TIMEOUT_MIN,
                    default=int(current.get(CONF_BLOCKING_TIMEOUT_MIN, DEFAULT_BLOCKING_TIMEOUT_MIN)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=120, step=5,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }),
        )

    # ── v1.8.0 L6 — Presence-Aware Scheduling ────────────────────────────────

    async def async_step_presence_scheduling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure presence-aware scheduling."""
        from homeassistant.helpers import selector

        if user_input is not None:
            updated = dict(self.config_entry.options)
            updated[CONF_PRESENCE_SCHEDULING_ENABLED] = user_input.get(
                CONF_PRESENCE_SCHEDULING_ENABLED, False
            )
            updated[CONF_PRESENCE_ENTITIES] = user_input.get(CONF_PRESENCE_ENTITIES, [])
            updated[CONF_PRESENCE_MODE] = user_input.get(
                CONF_PRESENCE_MODE, DEFAULT_PRESENCE_MODE
            )
            updated[CONF_AWAY_DELAY_MIN] = int(
                user_input.get(CONF_AWAY_DELAY_MIN, DEFAULT_AWAY_DELAY_MIN)
            )
            updated[CONF_CLEAN_DELAY_MIN] = int(
                user_input.get(CONF_CLEAN_DELAY_MIN, DEFAULT_CLEAN_DELAY_MIN)
            )
            return self.async_create_entry(title="", data=updated)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="presence_scheduling",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_PRESENCE_SCHEDULING_ENABLED,
                    default=current.get(CONF_PRESENCE_SCHEDULING_ENABLED, False),
                ): bool,
                vol.Optional(
                    CONF_PRESENCE_ENTITIES,
                    default=current.get(CONF_PRESENCE_ENTITIES, []),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person", multiple=True)
                ),
                vol.Optional(
                    CONF_PRESENCE_MODE,
                    default=current.get(CONF_PRESENCE_MODE, DEFAULT_PRESENCE_MODE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "away_only",  "label": "Unfreeze when all away"},
                            {"value": "always_ask", "label": "Fire event (manual control)"},
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_AWAY_DELAY_MIN,
                    default=int(current.get(CONF_AWAY_DELAY_MIN, DEFAULT_AWAY_DELAY_MIN)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    CONF_CLEAN_DELAY_MIN,
                    default=int(current.get(CONF_CLEAN_DELAY_MIN, DEFAULT_CLEAN_DELAY_MIN)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=30, step=1,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }),
        )

    # ── v1.7.0 L7 — Zone Management UI ───────────────────────────────────────

    async def async_step_map_management(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zone management index step — shows all zones with current state.

        Submitting with a zone selected → edit step.
        Submitting blank selection → save all edits and close.
        """
        from homeassistant.helpers import selector


        if not hasattr(self, "_pending_zone_edits"):
            self._pending_zone_edits: dict[str, dict[str, Any]] = {}

        data = self.config_entry.runtime_data
        options = self.config_entry.options

        if user_input is not None:
            selected = user_input.get("selected_zone", "")
            if not selected:
                # Blank = save all pending edits atomically
                return self._save_zone_edits_atomic()
            self._editing_zone_id = selected
            return await self.async_step_map_management_edit()

        # Build options list for the selector
        zone_options = self._build_zone_index_options(data, options)
        if not zone_options:
            return self.async_create_entry(title="", data=options)

        # Build description placeholder summarising zone states
        summary_lines = []
        for opt in zone_options:
            summary_lines.append(opt["label"])
        description_placeholders = {
            "zone_summary": "\n".join(summary_lines[:20]),
        }

        return self.async_show_form(
            step_id="map_management",
            data_schema=vol.Schema({
                vol.Optional("selected_zone", default=""): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": "", "label": "─── Save and close ───"}] + zone_options,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }),
            description_placeholders=description_placeholders,
            last_step=False,
        )

    async def async_step_map_management_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zone management edit step — rename or hide a single zone.

        Returns to the index step on submit.
        """
        zone_id = getattr(self, "_editing_zone_id", "")
        data = self.config_entry.runtime_data
        options = self.config_entry.options
        current_edit = self._pending_zone_edits.get(zone_id, {})

        if user_input is not None:
            # Accumulate edit — NOT saved yet (atomic save in index step)
            self._pending_zone_edits[zone_id] = {
                "display_name": user_input.get("display_name", ""),
                "hidden": bool(user_input.get("hidden", False)),
            }
            return await self.async_step_map_management()

        # Resolve current display name and hidden state
        current_name = self._resolve_current_zone_name(zone_id, data, options)
        current_hidden = self._resolve_current_zone_hidden(zone_id, data, options)

        return self.async_show_form(
            step_id="map_management_edit",
            data_schema=vol.Schema({
                vol.Optional(
                    "display_name",
                    default=current_edit.get("display_name", current_name),
                ): str,
                vol.Optional(
                    "hidden",
                    default=current_edit.get("hidden", current_hidden),
                ): bool,
            }),
            description_placeholders={"zone_name": current_name},
            last_step=False,
        )

    # ── L7 helpers ────────────────────────────────────────────────────────────

    def _build_zone_index_options(self, data: Any, options: dict) -> list[dict]:
        """Build selector option list for the map_management index step."""

        opts: list[dict] = []

        if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
            for zone in data.zone_store.zones:
                pending = self._pending_zone_edits.get(str(zone.id), {})
                name = pending.get("display_name") or zone.name
                hidden = pending.get("hidden", zone.hidden)
                tags: list[str] = []
                if hidden:
                    tags.append("hidden")
                if not zone.confirmed:
                    tags.append("unconfirmed")
                if str(zone.id) in self._pending_zone_edits:
                    tags.append("*")
                label = name + (f" [{', '.join(tags)}]" if tags else "")
                opts.append({"value": str(zone.id), "label": label})

        elif data.map_capability == MapCapability.SMART:
            aliases: dict = options.get(CONF_SMART_ZONE_ALIASES, {})
            hidden_ids: list = options.get(CONF_SMART_ZONE_HIDDEN, [])
            zone_data: dict = options.get("smart_zone_data", {})
            region_ids: set[str] = set(zone_data.keys())
            if data.has_cloud:
                for r in data.cloud_coordinator.regions:
                    if r.get("id"):
                        region_ids.add(str(r["id"]))
            for rid in sorted(region_ids):
                pending = self._pending_zone_edits.get(rid, {})
                cloud_name = next(
                    (r["name"] for r in (data.cloud_coordinator.regions if data.has_cloud else [])
                     if str(r.get("id")) == rid and r.get("name")), None
                )
                base_name = (
                    pending.get("display_name")
                    or aliases.get(rid)
                    or cloud_name
                    or zone_data.get(rid, {}).get("name")
                    or f"Zone {rid}"
                )
                hidden = pending.get("hidden", rid in hidden_ids)
                tags: list[str] = []
                if hidden:
                    tags.append("hidden")
                if rid in aliases:
                    tags.append("aliased")
                if rid in self._pending_zone_edits:
                    tags.append("*")
                label = base_name + (f" [{', '.join(tags)}]" if tags else "")
                opts.append({"value": rid, "label": label})

        return opts

    def _resolve_current_zone_name(self, zone_id: str, data: Any, options: dict) -> str:
        """Resolve the best current display name for zone_id."""

        if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
            for zone in data.zone_store.zones:
                if str(zone.id) == zone_id:
                    return zone.name
            return f"Zone {zone_id}"

        aliases: dict = options.get(CONF_SMART_ZONE_ALIASES, {})
        if zone_id in aliases:
            return aliases[zone_id]
        zone_data: dict = options.get("smart_zone_data", {})
        if zone_id in zone_data:
            return zone_data[zone_id].get("name") or f"Zone {zone_id}"
        if data.has_cloud:
            for r in data.cloud_coordinator.regions:
                if str(r.get("id")) == zone_id:
                    return r.get("name") or f"Zone {zone_id}"
        return f"Zone {zone_id}"

    def _resolve_current_zone_hidden(self, zone_id: str, data: Any, options: dict) -> bool:
        """Return the current hidden state for zone_id."""

        if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
            for zone in data.zone_store.zones:
                if str(zone.id) == zone_id:
                    return zone.hidden
            return False
        return zone_id in options.get(CONF_SMART_ZONE_HIDDEN, [])

    def _save_zone_edits_atomic(self) -> ConfigFlowResult:
        """Apply all pending zone edits atomically in a single options write."""

        data = self.config_entry.runtime_data
        options = dict(self.config_entry.options)

        if data.map_capability == MapCapability.EPHEMERAL and data.zone_store:
            for zone_id_str, edit in self._pending_zone_edits.items():
                zone_id = int(zone_id_str)
                if edit.get("hidden"):
                    data.zone_store.hide_zone(zone_id)
                else:
                    data.zone_store.unhide_zone(zone_id)
                    name = edit.get("display_name", "").strip()
                    if name:
                        data.zone_store.rename_zone(zone_id, name)
            self.hass.async_create_task(
                data.zone_store.async_save(self.hass, self.config_entry.entry_id),
                name="roomba_plus_zone_store_save",
            )
        else:
            # SMART: alias layer in options
            aliases: dict = dict(options.get(CONF_SMART_ZONE_ALIASES, {}))
            hidden: list = list(options.get(CONF_SMART_ZONE_HIDDEN, []))
            zone_data: dict = options.get("smart_zone_data", {})

            for region_id, edit in self._pending_zone_edits.items():
                display_name = edit.get("display_name", "").strip()
                # Resolve cloud name for alias-clear-on-match logic
                cloud_name: str | None = None
                if data.has_cloud:
                    for r in data.cloud_coordinator.regions:
                        if str(r.get("id")) == region_id:
                            cloud_name = r.get("name")
                            break
                if not cloud_name:
                    cloud_name = zone_data.get(region_id, {}).get("name")

                # Alias-clear-on-match: delete alias when name equals cloud name
                # to prevent shadowing future cloud renames.
                if display_name and display_name != cloud_name:
                    aliases[region_id] = display_name
                elif region_id in aliases:
                    del aliases[region_id]

                if edit.get("hidden"):
                    if region_id not in hidden:
                        hidden.append(region_id)
                else:
                    if region_id in hidden:
                        hidden.remove(region_id)

            options[CONF_SMART_ZONE_ALIASES] = aliases
            options[CONF_SMART_ZONE_HIDDEN] = hidden

        self._pending_zone_edits = {}
        return self.async_create_entry(title="", data=options)

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Zone naming step — triggered by the Repair Issue after mission end.

        Dynamically generates one text field per unconfirmed zone.
        """

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

        state = roomba_reported_state(self.config_entry.runtime_data.roomba)
        if not has_smart_map(state):
            return self.async_create_entry(title="", data=self.config_entry.options)

        # Collect all known region_ids from local state
        region_ids: set[str] = set()
        for entry in state.get("cleanSchedule2", []):
            for region in entry.get("cmd", {}).get("regions", []):
                rid = extract_region_id(region)
                if rid:
                    region_ids.add(rid)
        last = state.get("lastCommand", {})
        for region in (last.get("regions") or []):
            rid = extract_region_id(region)
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
            # Phase 1 submitted — parse IDs and store them for phase 2.
            raw = user_input["region_ids"]
            pending = [r.strip() for r in raw.replace(",", " ").split() if r.strip()]
            if not pending:
                errors["region_ids"] = "no_valid_ids"
            else:
                # Store pending IDs so phase 2 can read them on re-entry.
                self._pending_zone_ids = pending
                default_text = "\n".join(
                    f"{rid}={existing_labels.get(rid, '')}" for rid in pending
                )
                return self.async_show_form(
                    step_id="smart_zones_manual",
                    data_schema=vol.Schema(
                        {vol.Required("zone_names", default=default_text): str}
                    ),
                    description_placeholders={
                        "zone_ids": ", ".join(pending),
                        "zone_count": str(len(pending)),
                    },
                    last_step=True,
                )

        elif user_input is not None and "zone_names" in user_input:
            # Phase 2 submitted — parse textarea and save.
            # Format: one "id=Name" line per zone; blank or malformed lines skipped.
            raw = user_input["zone_names"].strip()
            parsed: dict[str, str] = {}
            for line in raw.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                rid_part, _, name_part = line.partition("=")
                rid = rid_part.strip()
                name = name_part.strip()
                if rid and name:
                    parsed[rid] = name

            # Resolve pmap_id FIRST — used by both the validation check and the save.
            # Priority: lastCommand → cleanSchedule2 → pmaps[0]
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

            if not parsed:
                errors["zone_names"] = "no_valid_ids"
                pending = getattr(self, "_pending_zone_ids", [])
            elif not current_pmap_id:
                errors["zone_names"] = "pmap_not_resolved"
                pending = getattr(self, "_pending_zone_ids", [])
                default_text = "\n".join(f"{rid}=" for rid in pending)
                return self.async_show_form(
                    step_id="smart_zones_manual",
                    data_schema=vol.Schema(
                        {vol.Required("zone_names", default=default_text): str}
                    ),
                    description_placeholders={
                        "zone_ids": ", ".join(pending),
                        "zone_count": str(len(pending)),
                    },
                    errors=errors,
                    last_step=True,
                )

            new_labels = dict(existing_labels)
            new_zone_data = dict(existing_zone_data)
            new_discovered = list(self.config_entry.options.get("discovered_zone_ids", []))

            for rid, name in parsed.items():
                new_labels[rid] = name
                new_zone_data[rid] = {"name": name, "pmap_id": current_pmap_id}
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

    async def async_step_cloud_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or update iRobot cloud credentials.

        Saves credentials to config_entry.data (not options), then triggers a
        reload so the cloud coordinator is re-initialised with the new values.
        Clearing both fields removes the credentials and disables cloud features.
        """

        errors: dict[str, str] = {}
        current_user = self.config_entry.data.get(CONF_IROBOT_USERNAME, "")

        if user_input is not None:
            username = user_input.get(CONF_IROBOT_USERNAME, "").strip()
            password = user_input.get(CONF_IROBOT_PASSWORD, "").strip()

            new_data = dict(self.config_entry.data)

            if username and password:
                # Validate before storing
                from homeassistant.helpers.aiohttp_client import async_get_clientsession
                api = IrobotCloudApi(username, password, async_get_clientsession(self.hass))
                try:
                    await api.authenticate()
                except AuthenticationError:
                    errors["base"] = "cloud_auth_failed"
                except CloudApiError:
                    errors["base"] = "cloud_unavailable"
                else:
                    new_data[CONF_IROBOT_USERNAME] = username
                    new_data[CONF_IROBOT_PASSWORD] = password
            else:
                # Clear credentials — disables cloud
                new_data.pop(CONF_IROBOT_USERNAME, None)
                new_data.pop(CONF_IROBOT_PASSWORD, None)

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                return self.async_create_entry(title="", data=self.config_entry.options)

        return self.async_show_form(
            step_id="cloud_credentials",
            data_schema=vol.Schema({
                vol.Optional(CONF_IROBOT_USERNAME, default=current_user): str,
                vol.Optional(CONF_IROBOT_PASSWORD, default=""): str,
            }),
            errors=errors,
            description_placeholders={
                "current_user": current_user or "not configured",
                # CF3 (v2.6.0): contextual scope label so SMART and EPHEMERAL
                # users understand what cloud credentials unlock for their robot.
                "cloud_scope": (
                    "room maps and analytics"
                    if getattr(self.config_entry.runtime_data, "map_capability", None)
                    and self.config_entry.runtime_data.map_capability.value == "smart"
                    else "analytics only"
                ),
            },
        )

    # ── F13 / F11 — Demand cleaning configuration ─────────────────────────────

    async def async_step_demand_cleaning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """F11 — Configure dirt-threshold demand cleaning.

        CF2 (v2.6.0): returns async_abort with a clear reason when the robot
        does not meet the gate conditions, instead of silently omitting the step.
        Gate: SMART + cloud only.
        Options stored: demand_cleaning_enabled (bool), demand_clean_multiplier (float).
        """
        data = self.config_entry.runtime_data
        if (
            not hasattr(data, "map_capability")
            or data.map_capability.value != "smart"
            or not data.has_cloud
        ):
            return self.async_abort(reason="demand_cleaning_not_supported")
        if user_input is not None:
            updated = dict(self.config_entry.options)
            updated[CONF_DEMAND_CLEANING_ENABLED] = user_input.get(
                CONF_DEMAND_CLEANING_ENABLED, False
            )
            updated[CONF_DEMAND_MULTIPLIER] = float(user_input.get(
                CONF_DEMAND_MULTIPLIER, TRIGGER_MULTIPLIER_DEFAULT
            ))
            return self.async_create_entry(title="", data=updated)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="demand_cleaning",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEMAND_CLEANING_ENABLED,
                        default=options.get(CONF_DEMAND_CLEANING_ENABLED, False),
                    ): bool,
                    vol.Optional(
                        CONF_DEMAND_MULTIPLIER,
                        default=float(options.get(
                            CONF_DEMAND_MULTIPLIER, TRIGGER_MULTIPLIER_DEFAULT
                        )),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=1.1, max=5.0),
                    ),
                }
            ),
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
