"""Config flow for the Roomba+ integration."""
from __future__ import annotations

from collections.abc import Mapping

import asyncio
import logging
from typing import Any

# GUARDED, SO A WRONG LIBRARY SAYS SO.
#
# These are module-level, so an ImportError here stops Home Assistant
# loading the config flow at all and the user gets "Config flow could
# not be loaded: Invalid handler specified" -- which names nothing and
# suggests nothing.
#
# @bandit254 saw exactly that, tried three different Roomba+ versions
# looking for one that worked, and concluded the smaller releases must
# ship fewer dependencies. They do not: 4.2.0 and 4.2.9 pin the same
# `roombapy==2.0.2` and import the same class on the same line. The
# real fault was the installed library, which the message never
# mentioned.
#
# With the import guarded, the flow loads and can say what is wrong.
try:
    from roombapy import RoombaClient, RoombaInfo
    from roombapy.discovery import RoombaDiscovery
    from roombapy.getpassword import RoombaPassword

    ROOMBAPY_IMPORT_ERROR: str | None = None
except ImportError as _exc:  # pragma: no cover - depends on environment
    RoombaClient = RoombaInfo = RoombaDiscovery = RoombaPassword = None  # type: ignore[assignment,misc]
    ROOMBAPY_IMPORT_ERROR = str(_exc)
from roombapy_prime import CloudAccount, CloudError
import voluptuous as vol
from homeassistant.helpers.selector import (
    EntitySelector as SelectorEntitySelector,
    EntitySelectorConfig as SelectorEntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.const import CONF_DELAY, CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import CannotConnect, async_connect_or_timeout, async_disconnect_or_timeout, roomba_reported_state
from . import cloud_errors
from .cloud_account import async_login, async_offer, async_peek
from .const import (
    CONF_CORRELATION_ENTITIES,
    CONF_ROOM_SCHEDULE,
    ROOM_SCHEDULE_INTERVALS,
    ROOM_SCHEDULE_LEARNED,
    CONF_AWAY_DELAY_MIN,
    CONF_BLID,
    CONF_BLOCKING_BEHAVIOR,
    CONF_BLOCKING_SENSORS,
    CONF_BLOCKING_TIMEOUT_MIN,
    CONF_CLEAN_DELAY_MIN,
    CONF_CONNECTION_TYPE,
    CONF_CONTINUOUS,
    CONF_DEMAND_CLEANING_ENABLED,
    CONF_DEMAND_MULTIPLIER,
    CONF_ENABLE_MAINTENANCE_LIST,
    CONF_ENABLE_SCHEDULE_CALENDAR,
    CONF_FLOOR,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRIME_FAVORITE_BUTTONS,
    CONF_MAP_CLEAN_ZONES,
    CONF_MAP_KEEPOUT_ZONES,
    CONF_MAP_NOMOP_ZONES,
    CONF_MAP_ROOM_LABELS,
    CONF_REGION_SENSORS,
    DEFAULT_REGION_SENSORS,
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
    DEFAULT_ENABLE_MAINTENANCE_LIST,
    DEFAULT_ENABLE_SCHEDULE_CALENDAR,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DEFAULT_PRIME_FAVORITE_BUTTONS,
    DEFAULT_MAP_ROOM_LABELS,
    DEFAULT_MAP_ZONES,
    DEFAULT_PRESENCE_MODE,
    DOMAIN,
    ROOMBA_SESSION,
    extract_region_id,
    has_smart_map,
)
from .dirt_threshold_manager import TRIGGER_MULTIPLIER_DEFAULT
from .models import ConnectionType, MapCapability, RoombaConfigEntry
from .room_seg_store import RoomSegStore

_LOGGER = logging.getLogger(__name__)


async def _async_check_cloud_login(
    hass: HomeAssistant, username: str, password: str
) -> tuple[CloudAccount | None, str | None]:
    """Logs in with what the user typed: the account, or the form error
    that says why not.

    ONE ERROR PER CAUSE (4.3). The form showed four errors for every way
    a login can fail, and one of them -- `cannot_connect` -- told the user
    to check the ROBOT, which a cloud login never touches. The library
    now names the cause (CloudError.reason), and each cause has its own
    text in the form (cloud_errors.flow_error())."""
    try:
        account = await async_login(hass, username, password)
    except CloudError as exc:
        _LOGGER.debug("iRobot cloud login failed: %s (%s)", exc.reason, exc)
        return None, cloud_errors.flow_error(exc)
    return account, None


@callback
def _async_update_account_siblings(
    hass: HomeAssistant, source: RoombaConfigEntry, username: str, password: str
) -> int:
    """Gives the new credentials of one entry to every other entry that
    had the same old ones, and reloads them (4.3).

    ONE PASSWORD CHANGE, ONE PROMPT. Since the robots of an account
    share a login, a password changed in the iRobot app makes all of
    them fail together, and each asked for the new password on its own:
    three robots, three prompts, the same password typed three times.
    The reload also ends the reauthentication the others were waiting
    in.

    ONLY A NEW PASSWORD FOR THE SAME ACCOUNT. A different address is a
    different account: this robot moved to another household's account,
    or was set up on the wrong one, and the others keep working on
    theirs. Only a changed password is passed on -- an account has one
    password, so the others cannot still be working with the old one.

    ONLY WHERE THE OLD CREDENTIALS WERE THE SAME. An entry with the same
    address and a different password is left alone -- it may be working
    with a password this one never had. Clearing the credentials (cloud
    off for one robot) is not passed on either: that is a choice per
    robot, and this is only called with new credentials.

    Called before the source entry itself is updated, while its data
    still holds the old credentials. Returns how many entries followed.
    """
    old_username = source.data.get(CONF_IROBOT_USERNAME)
    old_password = source.data.get(CONF_IROBOT_PASSWORD)
    if not (isinstance(old_username, str) and isinstance(old_password, str)
            and old_username and old_password):
        return 0
    old_key = old_username.strip().casefold()
    if username.strip().casefold() != old_key or old_password == password:
        return 0
    followed = 0
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == source.entry_id or entry.source == SOURCE_IGNORE:
            continue
        their_username = entry.data.get(CONF_IROBOT_USERNAME)
        if not isinstance(their_username, str) or their_username.strip().casefold() != old_key:
            continue
        if entry.data.get(CONF_IROBOT_PASSWORD) != old_password:
            continue
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_IROBOT_USERNAME: username, CONF_IROBOT_PASSWORD: password},
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)
        followed += 1
    if followed:
        _LOGGER.info(
            "iRobot account credentials changed: %d other robot(s) of the "
            "same account follow", followed,
        )
    return followed


def _robots_of(account: CloudAccount | None) -> dict[str, Any]:
    """The login response's raw robot records, keyed by BLID: name, sku
    and the robot's local password. Empty for anything unexpected."""
    if account is None:
        return {}
    robots = account.login_result.raw.get("robots")
    return robots if isinstance(robots, dict) else {}


#: The two fixed choices of the known_account step, next to the accounts.
_OTHER_ACCOUNT = "__other__"
_NO_ACCOUNT = "__none__"

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

# NEW (V4/Prime onboarding). Distinct from `None` (which already means
# "Add manually" in async_step_user()'s dropdown) -- a third sentinel for
# "set up via iRobot cloud account instead".
_CLOUD_ACCOUNT_SENTINEL = "__cloud_account__"


# V4/Prime-generation SKU prefixes -- CORRECTED (this session, parallel
# native-analysis track, decompiled com/irobot/core/SkuUtils.java): the
# earlier single-LETTER check below (just "g"/"n") was genuinely unsafe,
# SKU table MOVED TO roombapy-prime (this session): it is protocol
# knowledge, needed by the diagnostic tools as much as by this
# integration, and two copies would drift. See that function's own
# comment for why the prefix is three characters rather than one --
# a single letter genuinely misclassifies real devices.
def _is_prime_sku(sku: str | None) -> bool:
    """True for V4/Prime-generation SKUs. Thin wrapper kept so this
    module's own call sites and tests stay unchanged."""
    from roombapy_prime.auth import is_prime_sku  # noqa: PLC0415

    return bool(is_prime_sku(sku))


def _log_unknown_skus(devices: list[Any]) -> None:
    """Tells the user what to do now. Deliberately does NOT ask them to
    report anything yet.

    Excluding a device silently would be the worst outcome of filtering
    on a positive Classic match: the robot is on the network, visible in
    the iRobot app, and Home Assistant behaves as though it were not
    there — which reads as a broken integration rather than an unknown
    model.

    But asking for a report HERE would ask at the wrong moment. All that
    is known at discovery time is an SKU string; whether the robot is
    actually Prime is a guess. Setting it up via the iRobot account
    settles that question and produces a diagnostics download containing
    the capability flags and shadow structure — which is what makes a
    new model worth reporting at all.

    So the report is requested after setup succeeds (see
    __init__.py::_async_note_unknown_sku), where the issue carries a
    fact instead of a hypothesis.
    """
    if not devices:
        return
    listing = ", ".join(
        f"{getattr(d, 'robot_name', None) or 'unnamed'} (SKU {d.sku or 'not reported'})"
        for d in devices
    )
    _LOGGER.warning(
        "roomba_plus: %d robot(s) on this network have an SKU this version does not "
        "recognise: %s. They are not offered for local setup, because an unrecognised "
        "SKU is most likely a Prime-generation model — those cannot be set up locally. "
        "Use 'Set up with my iRobot account' instead.",
        len(devices), listing,
    )


def _is_classic_sku(sku: str | None) -> bool:
    """True for Classic-generation SKUs. NOT the inverse of
    _is_prime_sku() -- there is a third answer, and it matters.

    Both returning False means the SKU is UNKNOWN, and what to do with
    unknown changed once the tables were completed.

    The old local-discovery filter was `not _is_prime_sku(...)`, which
    lumps unknown in with Classic. That was defensible while the
    Classic table only held SkuUtils.java's one-default-SKU-per-platform
    list and missed most of the retail range -- four of five real test
    robots fell outside it, so excluding unknowns would have locked out
    working hardware.

    That is no longer the situation. Classic is a CLOSED generation:
    iRobot has moved to the Prime line, so the 20 prefixes now in that
    table are effectively the whole set. Prime, by contrast, keeps
    gaining models. An SKU nobody recognises today is therefore far more
    likely to be a new Prime robot than a missed Classic one -- and
    showing it in the local setup list produces exactly the failure this
    step already documents: a plausible-looking choice that cannot work,
    failing later at password retrieval with a message about the HOME
    button that has nothing to do with the cause.
    """
    from roombapy_prime.auth import is_classic_sku  # noqa: PLC0415

    return bool(is_classic_sku(sku))


# ── Input validation ──────────────────────────────────────────────────────────

async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate credentials by attempting a real connection.

    Returns dict containing the robot name and session on success.
    Raises CannotConnect when the device is unreachable or credentials fail.
    """
    # See the note in __init__.py: roombapy 2.x dropped the factory, and
    # `continuous`/`delay` with it. This call passed `continuous=True`
    # explicitly, which is now the only behaviour there is.
    roomba = RoombaClient(
        data[CONF_HOST],
        data[CONF_BLID],
        data[CONF_PASSWORD],
    )

    info = await async_connect_or_timeout(hass, roomba)
    await async_disconnect_or_timeout(hass, roomba)

    return {
        ROOMBA_SESSION: info[ROOMBA_SESSION],
        CONF_NAME: info[CONF_NAME],
        CONF_HOST: data[CONF_HOST],
    }


# ── REST980-MIGRATE (v2.9.0) ───────────────────────────────────────────────────
# Migration helper for users switching from ia74/roomba_rest980. Reads room
# names from that integration's own select.* entities (read-only access to
# the state machine — no write interaction with the foreign integration) and
# pre-fills our smart_zone_labels/smart_zone_data options, so the user
# doesn't have to re-discover and manually re-type every room name through
# our own naming Repair Issue workflow.

REST980_DOMAIN = "roomba_rest980"


def _resolve_current_pmap_id(state: dict[str, Any]) -> str:
    """Best-effort current pmap_id from live local MQTT state.

    Same priority order used by the existing smart_zones naming step
    (lastCommand > cleanSchedule2 > first entry in state.pmaps) — not
    extracted into a shared helper there to avoid touching working code
    outside this feature's scope; reused here for the new migration step.
    """
    last = state.get("lastCommand", {})
    if last.get("pmap_id"):
        return str(last["pmap_id"])
    for entry in state.get("cleanSchedule2", []):
        cmd = entry.get("cmd", {})
        if cmd.get("pmap_id"):
            return str(cmd["pmap_id"])
    pmaps: list[dict[str, Any]] = state.get("pmaps", [])
    if pmaps:
        return next(iter(pmaps[0]), "")
    return ""


def _discover_rest980_rooms(hass: HomeAssistant) -> dict[str, str]:
    """Read room names from an existing roomba_rest980 installation.

    Returns {region_id: name}. roomba_rest980's CleanRoomPasses select
    entities expose a `room_data` attribute containing the raw cloud
    region/zone dict — {"id": region_id, "name": ..., "region_type"/"zone_type": ...}.
    pmap_id is NOT exposed there (it's a private attribute on the rest980
    entity, never written to state) — callers must resolve pmap_id themselves
    from their own live state, same as any other newly-discovered room.

    Pure read access to the state machine and entity registry — never writes
    to or calls services on the foreign integration.

    AN EMPTY RESULT USUALLY MEANS THE ENTITIES WERE NEVER CREATED, not
    that the user has no rooms. It is worth knowing which, because the
    two look identical from here and only one is fixable.

    `CleanRoomPasses` is a plain `SelectEntity` in roomba_rest980, not a
    coordinator-backed one, and it sets `room_data` once in `__init__`
    from data its own setup fetched off the rest980 server. So:

      * once the entities exist, stopping the rest980 container does NOT
        take them away. Nothing re-reads the server, nothing marks them
        unavailable, and this function keeps working. That is what makes
        the documented migration order possible at all -- the container
        has to be stopped before Roomba+ can take the robot's single
        local connection.

      * but a Home Assistant restart with the container stopped is
        fatal to them. roomba_rest980's `async_setup_entry` awaits
        `async_config_entry_first_refresh()` against
        `{base_url}/api/local/info/state`; with nothing listening, the
        config entry fails to load, `select.async_setup_entry` never
        runs, and no `CleanRoomPasses` is ever constructed.

    Hence the ordering in README's migration section: install through
    HACS and restart FIRST, while the container is still running, then
    stop it. A user who restarts between stopping the container and
    running the import will land here with an empty dict and no
    indication why.

    Not defended against in code, because there is nothing to defend:
    the entities are absent, and this function cannot tell an account
    with no rooms from one whose rooms it can no longer see.
    """
    rooms: dict[str, str] = {}
    rest980_entries = hass.config_entries.async_entries(REST980_DOMAIN)
    if not rest980_entries:
        return rooms

    ent_reg = er.async_get(hass)
    for entry in rest980_entries:
        for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if entity.domain != "select":
                continue
            state = hass.states.get(entity.entity_id)
            if state is None:
                continue
            room_data = state.attributes.get("room_data")
            if not isinstance(room_data, dict):
                continue
            rid = room_data.get("id")
            name = room_data.get("name")
            if rid and name:
                rooms[str(rid)] = str(name)
    return rooms


# ── Config Flow ───────────────────────────────────────────────────────────────

class RoombaPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Roomba+.

    Supports auto-discovery via DHCP and Zeroconf, push-button linking,
    and full manual fallback with explicit password entry.
    """

    VERSION = 25

    # Class-level defaults as well as instance ones: several tests build
    # the flow with object.__new__() and set only what they use.
    #: Which path the known_account step continues: "account" (robot
    #: picker) or "local" (a robot paired over the network).
    _known_account_for: str = "account"
    #: The user chose "another account": ask for credentials instead.
    _skip_known_account: bool = False
    #: The known account preselected in the known_account step.
    _preferred_account: str | None = None
    #: A known account was already tried for this robot's password.
    _tried_account_link: bool = False
    #: What an integration_discovery flow was started with.
    _discovered: dict[str, Any] = {}
    #: The robot password in _pending_config came from an account and
    #: has not been tried on the robot yet.
    _account_password_unverified: bool = False
    #: A login error to show on the account form when it first appears.
    _account_error: str | None = None

    def __init__(self) -> None:
        """Initialise the flow."""
        self.name: str | None = None
        self.blid: str = ""
        self.host: str | None = None
        self.discovered_robots: dict[str, RoombaInfo] = {}
        self._pending_config: dict[str, Any] = {}
        # NEW (V4/Prime onboarding): populated by async_step_prime_account(),
        # consumed by async_step_prime_robot_picker() and beyond.
        self._prime_account_username: str = ""
        self._prime_account_password: str = ""
        self._prime_account_robots: dict[str, Any] = {}
        self._prime_selected_blid: str | None = None
        # The account this flow's own login produced. Handed to the entry
        # it creates (cloud_account.async_offer), so setup does not log
        # in a second time moments later.
        self._prime_account: CloudAccount | None = None
        #: Logins made during this flow, by casefolded username -- so one
        #: login serves every step that needs it.
        self._flow_accounts: dict[str, CloudAccount] = {}
        self._discovered = {}

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
            # `or ""` as well as the default: `.get(key, default)`
            # returns an explicit None unchanged, and only a missing key
            # reaches the default.
            flow_unique_id = progress["context"].get("unique_id") or ""
            # A flow with no robot yet -- "Add integration" sitting at
            # the account or robot-list step -- is not a shorter BLID.
            # Every string starts with "", so without this a robot
            # showing up on the network aborted the user's own setup.
            if not flow_unique_id:
                continue
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

        Shows discovered robots (if any), "Add manually", and — NEW
        (V4/Prime) — "Set up with my iRobot account", always visible
        regardless of local scan results.

        BUG FIX (this session): previously, zero discovered local
        devices fell straight through to async_step_manual() -- a dead
        end for a V4/Prime-only owner, since their robot never appears
        via local broadcast at all (no local channel exists). The
        cloud-account option is now shown unconditionally, not just
        appended to the dropdown when local devices happen to exist.

        REAL FIELD BUG FOUND AND FIXED (this session, chairstacker,
        with screenshots): V4/Prime robots DO still respond to the
        same local UDP discovery broadcast Classic robots use, even
        though they cannot actually be set up or controlled that way
        -- roombapy's own RoombaInfo.sku field is exactly what's
        needed to tell them apart (see _is_prime_sku() above), but
        this step wasn't using it at all. Two real consequences this
        fixes:

        1. A Prime device used to appear in the "Select robot" dropdown
           the same as a Classic one, showing its name and IP -- an
           entirely plausible-looking but non-functional choice.
           Picking it led into the local-link flow, which cannot work
           for this generation of robot at all. Now filtered out of
           the dropdown entirely -- the "Set up with my iRobot
           account" option (already labeled as being for exactly this
           case) remains the only way to add one.
        2. WORSE: if a Prime robot triggers a genuine HA-native zeroconf/
           DHCP discovery (self.host gets pre-set before this step even
           runs), the OLD code took a fast path straight into the
           local-link flow with no form shown at all -- the user never
           even got a choice, unlike the dropdown case above. Now
           redirects straight to the cloud-account flow instead in
           that specific case, since the SKU already confirms which
           path is actually needed -- no reason to show a form asking
           again when the answer is already known.
        """
        # THE LIBRARY, BEFORE ANYTHING ELSE.
        #
        # Nothing below can work if the wrong `roombapy` is installed,
        # and without this the user only ever saw "Invalid handler
        # specified" from Home Assistant, with the real reason buried
        # in a traceback they had to go find.
        if ROOMBAPY_IMPORT_ERROR is not None:
            return self.async_abort(
                reason="wrong_roombapy",
                description_placeholders={"error": ROOMBAPY_IMPORT_ERROR},
            )

        if user_input is not None:
            chosen = user_input.get(CONF_HOST)
            if chosen == _CLOUD_ACCOUNT_SENTINEL:
                return await self.async_step_prime_account()
            if not chosen:
                return await self.async_step_manual()
            if chosen in self.discovered_robots:
                self.host = chosen
                return await self._async_start_link()

        already_configured = self._async_current_ids(False)
        devices = await _async_discover_roombas(self.hass, self.host)

        if devices:
            self.discovered_robots = {
                device.ip: device
                for device in devices
                # POSITIVE match, not the absence of a Prime match --
                # see _is_classic_sku() for why that flipped.
                if device.blid not in already_configured and _is_classic_sku(device.sku)
            }

            # Excluding a device silently is the worst outcome of that
            # flip: the robot is on the network, the user can see it in
            # the iRobot app, and Home Assistant simply pretends it is
            # not there. That looks like a broken integration.
            #
            # An unrecognised SKU is most likely a Prime model newer
            # than our table -- in which case account-based setup will
            # work and local setup cannot. So say that, and ask for the
            # one detail that lets the table be fixed.
            self._unknown_sku_devices = [
                device
                for device in devices
                if device.blid not in already_configured
                and not _is_classic_sku(device.sku)
                and not _is_prime_sku(device.sku)
            ]
            _log_unknown_skus(self._unknown_sku_devices)

        if self.host and self.host in self.discovered_robots:
            self.context["title_placeholders"] = {
                "host": self.host,
                "name": self.discovered_robots[self.host].robot_name,
            }
            return await self._async_start_link()

        # A Prime device reached this step with self.host already set
        # (a genuine zeroconf/DHCP discovery, not a manual "Add
        # Integration" click) -- discovered_robots above deliberately
        # excludes it, so the check right above this comment won't
        # fast-path it, but it's still real and still needs somewhere
        # to go. Send it straight to the cloud-account flow rather
        # than falling through to a form -- the SKU already answered
        # the question the form would otherwise be asking.
        if self.host and devices:
            matching = next((d for d in devices if d.ip == self.host), None)
            if matching is not None and _is_prime_sku(matching.sku):
                return await self.async_step_prime_account()

        # THE ACCOUNT COMES FIRST (4.3). It works for every robot, finds
        # all of them on the account at once (the others are then
        # announced as discovered), and needs no button on the robot.
        # Local pairing stays for anyone who wants no cloud at all.
        #
        # AND IT IS ALSO THE FALLBACK. Its old wording read "for newer
        # models with no local setup, e.g. Combo" -- which told an i7
        # owner it was not for him, when it is exactly what he needs if
        # the password cannot be fetched automatically. A forum user
        # with two i7s read that and ran a third-party script to extract
        # his password by hand.
        hosts: dict[str | None, str] = {
            _CLOUD_ACCOUNT_SENTINEL: (
                "Set up with my iRobot account (recommended -- works for any "
                "robot, finds all robots on the account, no button to press)"
            ),
            **{
                device.ip: f"{device.robot_name} ({device.ip})"
                for device in devices
                if device.blid not in already_configured and not _is_prime_sku(device.sku)
            },
            None: "Add manually (I know my robot's local IP)",
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

    # ── V4/Prime cloud-account onboarding ────────────────────────────────────
    #
    # Reached from async_step_user()'s "Set up with my iRobot account" option
    # -- for owners whose robot has no local channel at all (V4/Prime). The
    # SAME login also discovers any Classic robots on the account, which are
    # completed via the existing local-network path below (blid+password
    # already known from this login, only the local IP still needs
    # resolving) rather than shown only informationally.

    async def async_step_prime_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud account login.

        The same login and error mapping as async_step_cloud_credentials
        (_async_check_cloud_login). The login response's raw "robots"
        dict carries sku and password per robot, everything
        async_step_prime_robot_picker() needs.
        """
        if user_input is None and not self._skip_known_account and self._known_accounts():
            self._known_account_for = "account"
            return await self.async_step_known_account()

        errors: dict[str, str] = {}
        if user_input is None and self._account_error:
            errors["base"] = self._account_error
            self._account_error = None

        if user_input is not None:
            username = user_input.get(CONF_IROBOT_USERNAME, "").strip()
            password = user_input.get(CONF_IROBOT_PASSWORD, "").strip()
            if not username or not password:
                # Unlike async_step_cloud_credentials (where both fields
                # are genuinely optional -- "leave empty to skip cloud
                # features"), this step has no valid skip path: cloud
                # login is the entire point of it. Bug-hunt round found
                # this silently reshowed the form with no explanation on
                # a blank submission -- voluptuous's Required only
                # enforces the KEY being present, not the value being
                # non-empty, so this path is reachable in practice.
                errors["base"] = "cloud_credentials_rejected"
            elif username and password:
                account, error = await self._async_login(username, password)
                if account is None:
                    errors["base"] = error or "cloud_unknown"
                else:
                    self._use_account(username, password, account)
                    return await self._async_continue_with_account()

        return self.async_show_form(
            step_id="prime_account",
            data_schema=vol.Schema({
                vol.Required(CONF_IROBOT_USERNAME, default=""): str,
                vol.Required(CONF_IROBOT_PASSWORD, default=""): str,
            }),
            errors=errors,
        )

    async def async_step_prime_robot_picker(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Splits the account's robots into V4/Prime (ready to set up
        directly) and Classic (completed via the existing local
        network, see async_step_prime_classic_ip()) by SKU. Already-
        configured blids are filtered out.

        ONE ROBOT PER FLOW RUN -- Home Assistant's rule: a flow creates
        one entry. The account's OTHER robots are not left to a second
        "Add integration" run any more (4.3): once this one is created,
        each of them is announced as discovered (_announce_other_robots),
        one click each.

        Robots already set up are named in the description rather than
        dropped silently, so a robot missing from the list does not look
        like one the account lacks.
        """
        already_configured = self._async_current_ids(False)
        candidates = {
            blid: info
            for blid, info in self._prime_account_robots.items()
            if blid not in already_configured
        }

        if not candidates:
            return self.async_abort(reason="no_new_robots_found")

        if user_input is not None:
            self._prime_selected_blid = user_input[CONF_BLID]
            # Defensive: candidates is recomputed fresh on every call from
            # _async_current_ids() -- if another flow completed for this
            # exact blid in the window between showing this form and
            # submitting it (unlikely, but not impossible), it would no
            # longer be in candidates here. Abort cleanly rather than a
            # raw KeyError.
            info = candidates.get(self._prime_selected_blid)
            if info is None:
                return self.async_abort(reason="already_configured")
            if _is_prime_sku(info.get("sku")):
                return await self._async_create_prime_entry(self._prime_selected_blid, info)
            return await self.async_step_prime_classic_ip()

        choices = {
            blid: (
                f"{info.get('name') or blid} — "
                f"{'V4/Prime' if _is_prime_sku(info.get('sku')) else 'Classic'} "
                f"({info.get('sku') or '?'})"
            )
            for blid, info in candidates.items()
        }
        configured = [
            str(info.get("name") or blid)
            for blid, info in self._prime_account_robots.items()
            if blid in already_configured and isinstance(info, dict)
        ]
        return self.async_show_form(
            step_id="prime_robot_picker",
            data_schema=vol.Schema({vol.Required(CONF_BLID): vol.In(choices)}),
            description_placeholders={"configured": ", ".join(configured) or "–"},
        )

    # ── One account, several robots (4.3) ──────────────────────────────────

    def _known_accounts(self) -> dict[str, tuple[str, str]]:
        """The iRobot accounts entries of this integration already use,
        by casefolded username: (username, password).

        Where two entries of one account disagree on the password (one
        still has an old one), the loaded entry's wins -- its password
        is the one that currently works."""
        known: dict[str, tuple[str, str]] = {}
        for entry in self._async_current_entries(include_ignore=False):
            username = entry.data.get(CONF_IROBOT_USERNAME)
            password = entry.data.get(CONF_IROBOT_PASSWORD)
            if not (isinstance(username, str) and isinstance(password, str)
                    and username and password):
                continue
            key = username.strip().casefold()
            if key not in known or entry.state is ConfigEntryState.LOADED:
                known[key] = (username, password)
        return known

    async def _async_login(
        self, username: str, password: str
    ) -> tuple[CloudAccount | None, str | None]:
        """_async_check_cloud_login(), once per account and flow: a step
        that needs the account again takes the login an earlier step of
        this flow made."""
        if "_flow_accounts" not in self.__dict__:   # a flow built by hand in a test
            self._flow_accounts = {}
        # By password too: a user who picks "another account" and types
        # the same address with a different password must get a login
        # with THAT password, not the one an earlier step made.
        key = f"{username.strip().casefold()}\0{password}"
        cached = self._flow_accounts.get(key)
        if cached is not None:
            return cached, None
        account, error = await _async_check_cloud_login(self.hass, username, password)
        if account is not None:
            self._flow_accounts[key] = account
        return account, error

    def _use_account(self, username: str, password: str, account: CloudAccount) -> None:
        """The account the rest of the flow works with."""
        self._prime_account_username = username
        self._prime_account_password = password
        self._prime_account_robots = _robots_of(account)
        self._prime_account = account

    async def async_step_known_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """An iRobot account another entry already uses, instead of typing
        it again for every robot (4.3).

        Choosing it still logs in: only a fresh login lists a robot added
        to the account since the other entry was set up, and it checks
        the stored password still works. What it saves is the typing.

        Two paths lead here. From "Set up with my iRobot account" the
        choice leads to the robot list. After pairing a robot over the
        network it replaces the optional credentials form, and "no cloud"
        stays a choice.
        """
        known = self._known_accounts()
        local = self._known_account_for == "local"
        errors: dict[str, str] = {}

        if user_input is not None:
            if local and self._account_password_unverified:
                refused = await self._async_verify_account_password()
                if refused is not None:
                    return refused
            choice = str(user_input.get("account", ""))
            if local and choice == _NO_ACCOUNT:
                return self._async_create_local_entry(None, None, None)
            creds = known.get(choice.strip().casefold())
            if creds is None:
                self._skip_known_account = True
                if local:
                    return await self.async_step_cloud_credentials()
                return await self.async_step_prime_account()
            username, password = creds
            account, error = await self._async_login(username, password)
            if account is None:
                errors["base"] = error or "cloud_unknown"
            elif local:
                return self._async_create_local_entry(username, password, account)
            else:
                self._use_account(username, password, account)
                return await self._async_continue_with_account()

        options = [SelectOptionDict(value=u, label=u) for u, _p in known.values()]
        options.append(SelectOptionDict(value=_OTHER_ACCOUNT, label=_OTHER_ACCOUNT))
        if local:
            options.append(SelectOptionDict(value=_NO_ACCOUNT, label=_NO_ACCOUNT))
        preferred = (self._preferred_account or "").strip().casefold()
        default = known[preferred][0] if preferred in known else options[0]["value"]
        return self.async_show_form(
            step_id="known_account",
            data_schema=vol.Schema({
                vol.Required("account", default=default): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.LIST,
                        translation_key="known_account",
                    )
                ),
            }),
            errors=errors,
        )

    @callback
    def _announce_other_robots(
        self, account: CloudAccount | None, username: str, created_blid: str
    ) -> None:
        """Every other robot on the account that is not set up yet
        appears under "Discovered", one click each (4.3).

        Home Assistant lets one flow create one entry, so a multiple
        choice cannot add three robots at once; a discovery per robot is
        its own way of saying "there are more". A robot the user ignored
        stays ignored: Home Assistant keeps that as a configured id.

        The discovery carries no secret -- only the BLID, what to show,
        and which account. The password comes from that account's entry
        when the user adds it, however much later that is.

        Not from a discovery flow itself, which would announce the same
        robots again."""
        if account is None or self.source == SOURCE_INTEGRATION_DISCOVERY:
            return
        taken = self._async_current_ids(include_ignore=True)
        for blid, info in _robots_of(account).items():
            if blid == created_blid or blid in taken or not isinstance(info, dict):
                continue
            discovery_flow.async_create_flow(
                self.hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    CONF_BLID: blid,
                    CONF_NAME: str(info.get("name") or blid),
                    "sku": str(info.get("sku") or ""),
                    CONF_IROBOT_USERNAME: username,
                },
            )

    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> ConfigFlowResult:
        """Another robot on an account just set up -- see
        _announce_other_robots()."""
        blid = str(discovery_info[CONF_BLID])
        await self.async_set_unique_id(blid)
        self._abort_if_unique_id_configured()
        self._discovered = dict(discovery_info)
        self.blid = blid
        self.name = str(discovery_info.get(CONF_NAME) or blid)
        self.context["title_placeholders"] = {CONF_NAME: self.name}
        return await self.async_step_integration_discovery_confirm()

    async def async_step_integration_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One click: add this robot of the account.

        The password is the one of an entry that uses the account now,
        not the one at discovery time. If no entry uses it any more, the
        flow asks for the account like a fresh setup would."""
        errors: dict[str, str] = {}
        username = str(self._discovered.get(CONF_IROBOT_USERNAME) or "")
        if user_input is not None:
            creds = self._known_accounts().get(username.strip().casefold())
            if creds is None:
                self._skip_known_account = True
                return await self.async_step_prime_account()
            username, password = creds
            # The login an entry holds anyway lists this robot: it was
            # announced from it. Only a robot missing there needs a login.
            account: CloudAccount | None = async_peek(self.hass, username, password)
            if self.blid not in _robots_of(account):
                account, error = await self._async_login(username, password)
                if account is None:
                    # NOT the same button again: every click would try
                    # the same stored password, and against a locked
                    # account every attempt extends the lock. Ask for
                    # the account instead, saying why.
                    self._skip_known_account = True
                    self._account_error = error or "cloud_unknown"
                    return await self.async_step_prime_account()
            self._use_account(username, password, account)
            return await self._async_add_discovered()

        sku = str(self._discovered.get("sku") or "")
        self._set_confirm_only()
        return self.async_show_form(
            step_id="integration_discovery_confirm",
            errors=errors,
            description_placeholders={
                CONF_NAME: self.name or self.blid,
                "model": sku or "?",
                "account": username,
            },
        )

    async def _async_continue_with_account(self) -> ConfigFlowResult:
        """Where a flow goes once it has an account: the robot list -- or,
        for a flow started for one robot of the account, that robot.

        A discovery flow that had to ask for the account must not offer
        the whole list: picking another robot there would add that one
        under this robot's card, and the robot the card was for would
        not be announced again."""
        if self.source == SOURCE_INTEGRATION_DISCOVERY:
            return await self._async_add_discovered()
        return await self.async_step_prime_robot_picker()

    async def _async_add_discovered(self) -> ConfigFlowResult:
        """Adds the robot this discovery flow is for, with the account
        the flow now has."""
        info = self._prime_account_robots.get(self.blid)
        if not isinstance(info, dict):
            return self.async_abort(reason="robot_not_on_account")
        self._prime_selected_blid = self.blid
        if _is_prime_sku(info.get("sku")):
            return await self._async_create_prime_entry(self.blid, info)
        return await self.async_step_prime_classic_ip()

    async def _async_verify_account_password(self) -> ConfigFlowResult | None:
        """Tries the password the account gave on the robot -- once the
        user has answered, not when the robot was merely found.

        None when the robot accepts it. Otherwise the HOME button step,
        saying why it appears after all."""
        self._account_password_unverified = False
        try:
            result = await validate_input(self.hass, self._pending_config)
        except CannotConnect:
            _LOGGER.debug(
                "Robot %s did not accept the password from the iRobot "
                "account; falling back to the HOME button", self.blid,
            )
            self._pending_config = {}
            return self.async_show_form(
                step_id="link",
                description_placeholders={CONF_NAME: self.name or self.blid},
                errors={"base": "account_password_refused"},
            )
        self.name = self.name or result[CONF_NAME]
        return None

    async def _async_link_via_known_account(self) -> ConfigFlowResult | None:
        """A robot found on the network whose BLID is on an account this
        integration already knows needs no HOME button (4.3): the login
        response carries every robot's local password.

        NOTHING GOES OVER THE NETWORK HERE. This runs as soon as Home
        Assistant finds a robot -- a DHCP discovery shows its first form
        without anyone clicking -- so it only reads the logins that
        loaded entries already hold. No cloud login (against an account
        with a stale stored password, every discovery after every
        restart would be another failed attempt), and no connection to
        the robot. The password is tried on the robot once the user
        answers (_async_verify_account_password).

        A robot added to the account after the entries logged in is not
        in their logins; it gets the HOME button, as before, or the
        account path, which logs in fresh."""
        self._tried_account_link = True
        for username, password in self._known_accounts().values():
            info = _robots_of(async_peek(self.hass, username, password)).get(self.blid)
            robot_password = info.get("password") if isinstance(info, dict) else None
            if not robot_password:
                continue
            self._pending_config = {
                CONF_HOST: self.host,
                CONF_BLID: self.blid,
                CONF_PASSWORD: robot_password,
                **DEFAULT_OPTIONS,
            }
            self._account_password_unverified = True
            self._preferred_account = username
            return await self.async_step_cloud_credentials()
        return None

    @callback
    def _async_create_local_entry(
        self,
        username: str | None,
        password: str | None,
        account: CloudAccount | None,
    ) -> ConfigFlowResult:
        """The entry of a robot paired over the network, with or without
        an account. With one, the login goes to the entry and the
        account's other robots are announced."""
        config = dict(self._pending_config)
        if username and password:
            config[CONF_IROBOT_USERNAME] = username
            config[CONF_IROBOT_PASSWORD] = password
            if account is not None:
                async_offer(self.hass, account, username, password)
                self._announce_other_robots(account, username, str(config.get(CONF_BLID, "")))
        return self.async_create_entry(title=self.name or "Roomba+", data=config)

    @callback
    def _offer_account(self) -> None:
        """Hands this flow's login to the entry it is about to create."""
        if self._prime_account is not None:
            async_offer(
                self.hass, self._prime_account,
                self._prime_account_username, self._prime_account_password,
            )

    async def _async_create_prime_entry(
        self, blid: str, info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Creates a CLOUD_ONLY config entry directly -- login already
        succeeded in async_step_prime_account(), no further
        connectivity check needed here. Any actual connection-level
        failure (MQTT unreachable etc.) surfaces at real setup time via
        PrimeCoordinator.async_start()'s own ConfigEntryNotReady
        mapping, not here.

        Also hands this flow's login to the entry (_offer_account()),
        which HA sets up moments after this returns -- so setup takes
        the login instead of running a second one.
        """
        await self.async_set_unique_id(blid, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        self._offer_account()
        self._announce_other_robots(self._prime_account, self._prime_account_username, blid)
        return self.async_create_entry(
            title=info.get("name") or blid,
            data={
                CONF_CONNECTION_TYPE: ConnectionType.CLOUD_ONLY.value,
                CONF_BLID: blid,
                CONF_IROBOT_USERNAME: self._prime_account_username,
                CONF_IROBOT_PASSWORD: self._prime_account_password,
            },
        )

    async def async_step_prime_classic_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Classic robot found via cloud login -- blid+password already
        known (the account's raw robot entry), only the local IP is
        still missing. Tries the existing local-network scan first,
        matched by blid; if not found (robot offline, or broadcast
        discovery unreliable), asks for the IP manually instead of
        failing outright -- blid+password are already known either way,
        unlike the manual/link path which needs the physical
        push-button dance specifically because it does NOT know the
        password yet.
        """
        assert self._prime_selected_blid is not None
        blid = self._prime_selected_blid
        info = self._prime_account_robots[blid]
        password = info.get("password")
        errors: dict[str, str] = {}

        await self.async_set_unique_id(blid, raise_on_progress=False)
        self._abort_if_unique_id_configured()

        if not password:
            # Defensive -- should always be present for a real robot
            # entry, but fail loudly rather than silently proceeding
            # with a None password that would just fail to connect.
            return self.async_abort(reason="prime_classic_password_missing")

        if user_input is not None:
            host = user_input[CONF_HOST]
        else:
            devices = await _async_discover_roombas(self.hass, None)
            matched = next((d for d in devices if d.blid == blid), None)
            if matched is None:
                return self.async_show_form(
                    step_id="prime_classic_ip",
                    data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                    description_placeholders={CONF_NAME: info.get("name") or blid},
                )
            host = matched.ip

        config = {
            CONF_HOST: host,
            CONF_BLID: blid,
            CONF_PASSWORD: password,
            **DEFAULT_OPTIONS,
        }
        try:
            result = await validate_input(self.hass, config)
        except CannotConnect:
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="prime_classic_ip",
                data_schema=vol.Schema({vol.Required(CONF_HOST, default=host): str}),
                errors=errors,
                description_placeholders={CONF_NAME: info.get("name") or blid},
            )

        self.blid = blid
        self.name = result[CONF_NAME]
        # THE ACCOUNT STAYS WITH THE ROBOT (4.3). A separate step asked
        # whether to keep the credentials for cloud features, defaulting
        # to yes -- one more click on every Classic robot added from the
        # account, for an answer the user already gave by choosing the
        # account. Cloud features can still be turned off in the options.
        self._pending_config = config
        return self._async_create_local_entry(
            self._prime_account_username, self._prime_account_password, self._prime_account
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to press and hold the HOME button to retrieve the password.

        Not when an account this integration already knows lists the
        robot: then its password comes from there (4.3)."""
        if user_input is None:
            if not self._tried_account_link and self._known_accounts():
                linked = await self._async_link_via_known_account()
                if linked is not None:
                    return linked
            return self.async_show_form(
                step_id="link",
                description_placeholders={CONF_NAME: self.name or self.blid},
            )

        assert self.host
        roomba_pw = RoombaPassword(self.host)

        try:
            password = await roomba_pw.get_password()
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

        if user_input is None and not self._skip_known_account and self._known_accounts():
            self._known_account_for = "local"
            return await self.async_step_known_account()

        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input.get(CONF_IROBOT_USERNAME, "").strip()
            password = user_input.get(CONF_IROBOT_PASSWORD, "").strip()
            if not (username and password):
                return self._async_create_local_entry(None, None, None)
            # Validate credentials before storing
            account, error = await self._async_login(username, password)
            if account is None:
                errors["base"] = error or "cloud_unknown"
            else:
                return self._async_create_local_entry(username, password, account)

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

    # ── Reauth flow (v3.5.0) ───────────────────────────────────────────────────
    #
    # Bug-hunt fix, config_flow.py review: cloud_coordinator.py's
    # _async_setup()/_async_update_data() already raise ConfigEntryAuthFailed
    # on a bad cloud login (pre-dates v3.5.0) — which calls
    # config_entry.async_start_reauth(), which starts THIS flow at
    # async_step_reauth. That method didn't exist anywhere in this file, so
    # every auth failure since that mechanism was added would have silently
    # gone nowhere: no guided flow, just a config entry stuck in an error
    # state with no path back for the user except removing and re-adding the
    # whole integration. v3.5.0's cloud_stale split (repairs.py) explicitly
    # relies on this native reauth flow instead of a custom Repair Issue for
    # the auth-failure case — so this was the missing other half of that fix.
    #
    # Reuses the same validate-before-save pattern already established for
    # cloud credentials elsewhere in this file (async_step_cloud_credentials
    # above, and RoombaPlusOptionsFlow's version) rather than introducing a
    # third, slightly different implementation of the same check.

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Entry point HA calls when ConfigEntryAuthFailed is raised.

        entry_data is the failing entry's current data — nothing to do
        with it here beyond routing to the actual form, since
        async_step_reauth_confirm reads the live entry via
        self._get_reauth_entry() instead of this snapshot.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect and validate new iRobot cloud credentials, then save
        and reload the existing entry — never creates a new one."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        current_user = reauth_entry.data.get(CONF_IROBOT_USERNAME, "")

        if user_input is not None:
            username = user_input.get(CONF_IROBOT_USERNAME, "").strip()
            password = user_input.get(CONF_IROBOT_PASSWORD, "").strip()
            account, error = await _async_check_cloud_login(
                self.hass, username, password
            )
            if account is None:
                errors["base"] = error or "cloud_unknown"
            else:
                # The entry reloads with these credentials and takes this
                # login instead of making another.
                async_offer(self.hass, account, username, password)
                _async_update_account_siblings(
                    self.hass, reauth_entry, username, password
                )
                new_data = dict(reauth_entry.data)
                new_data[CONF_IROBOT_USERNAME] = username
                new_data[CONF_IROBOT_PASSWORD] = password
                return self.async_update_reload_and_abort(
                    reauth_entry, data=new_data,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_IROBOT_USERNAME, default=current_user): str,
                vol.Required(CONF_IROBOT_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "name": reauth_entry.data.get(CONF_BLID, ""),
                "current_user": current_user or "not configured",
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
        # v3.3.0 ROOM-SCHED — per-room cleaning frequency (SMART + cloud:
        # named cloud regions are the only stable config keys)
        if data.map_capability == MapCapability.SMART and data.has_cloud:
            menu.append("room_schedule")

        # ── 🗺  Map ────────────────────────────────────────────────────────
        if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("map_management")
        # CF4: single "rooms" entry routes internally to zones/smart_zones
        if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            menu.append("rooms")

        # REST980-MIGRATE (v2.9.0): only offered when there's something to
        # migrate — a roomba_rest980 installation actually present, and a
        # Smart Map robot (the only tier with named-room cleaning here).
        if (
            data.map_capability == MapCapability.SMART
            and self.hass.config_entries.async_entries(REST980_DOMAIN)
        ):
            menu.append("rest980_migrate")

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

    async def async_step_rest980_migrate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """REST980-MIGRATE (v2.9.0) — import room names from roomba_rest980.

        Read-only: discovers room names from the foreign integration's own
        select entities, fills in any of our smart_zone_labels/smart_zone_data
        entries that are still missing. Never overwrites a name the user has
        already assigned through our own naming flow.
        """
        discovered = _discover_rest980_rooms(self.hass)
        existing_labels: dict[str, Any] = self.config_entry.options.get(
            "smart_zone_labels", {}
        )
        new_rooms = {
            rid: name for rid, name in discovered.items() if rid not in existing_labels
        }

        if user_input is not None:
            if not user_input.get("confirm_import", False) or not new_rooms:
                return self.async_create_entry(title="", data=self.config_entry.options)

            state = roomba_reported_state(self.config_entry.runtime_data.roomba)
            current_pmap_id = _resolve_current_pmap_id(state)

            new_labels = dict(existing_labels)
            new_zone_data: dict[str, Any] = dict(
                self.config_entry.options.get("smart_zone_data", {})
            )
            for rid, name in new_rooms.items():
                new_labels[rid] = name
                new_zone_data[rid] = {"name": name, "pmap_id": current_pmap_id}

            new_options = dict(self.config_entry.options)
            new_options["smart_zone_labels"] = new_labels
            new_options["smart_zone_data"] = new_zone_data
            return self.async_create_entry(
                title="", data=new_options,
                description_placeholders={"room_count": str(len(new_rooms))},
            )

        if not discovered:
            return self.async_abort(reason="no_rest980_rooms_found")
        if not new_rooms:
            return self.async_abort(reason="rest980_rooms_already_imported")

        return self.async_show_form(
            step_id="rest980_migrate",
            data_schema=vol.Schema({
                vol.Required("confirm_import", default=True): bool,
            }),
            description_placeholders={
                "room_count": str(len(new_rooms)),
                "room_names": ", ".join(sorted(new_rooms.values())),
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connection and map settings form.

        BRANCHES ON connection_type (this session): a CLOUD_ONLY (Prime)
        entry used to land on this SAME form as Classic, showing fields
        that mean nothing for Prime at all (CONF_MAP_SIZE_PX/CONF_MAP_SCALE/
        CONF_CORRELATION_ENTITIES -- all Classic-only rendering/correlation
        concepts). Prime now gets its own, deliberately minimal form
        instead -- today that's just CONF_ENABLE_SCHEDULE_CALENDAR, the
        only cross-tier preference that exists so far (see that constant's
        own docstring, const.py, for why it defaults to True)."""
        options = self.config_entry.options

        if self.config_entry.runtime_data.connection_type == ConnectionType.CLOUD_ONLY:
            if user_input is not None:
                updated = dict(options)
                updated.update(user_input)
                return self.async_create_entry(title="", data=updated)
            return self.async_show_form(
                step_id="settings",
                data_schema=vol.Schema(
                    {
                        vol.Optional(
                            CONF_ENABLE_SCHEDULE_CALENDAR,
                            default=options.get(
                                CONF_ENABLE_SCHEDULE_CALENDAR, DEFAULT_ENABLE_SCHEDULE_CALENDAR
                            ),
                        ): bool,
                        # Room labels drawn INTO the map image.
                        #
                        # Off by default, which reads backwards until you
                        # know what Classic does: it removed its own
                        # labels in v2.7.3 because the
                        # xiaomi-vacuum-map-card draws its own overlay
                        # from the `rooms` attribute, and having both
                        # doubles them up.
                        #
                        # So the default suits the card user, and this
                        # option exists for everyone else -- a plain
                        # picture-entity card shows an image and nothing
                        # else, so for them the names have to be in the
                        # picture or they do not exist.
                        # One button per saved favourite, on by default.
                        #
                        # They are the only route that needs no setup --
                        # tappable right after install, and usable by
                        # voice, which a service call is not. They are
                        # also the only one costing an entity each, so
                        # somebody with fifteen favourites can turn them
                        # off and use the `favorites` attribute and the
                        # run_favorite service instead.
                        vol.Optional(
                            CONF_PRIME_FAVORITE_BUTTONS,
                            default=options.get(
                                CONF_PRIME_FAVORITE_BUTTONS,
                                DEFAULT_PRIME_FAVORITE_BUTTONS,
                            ),
                        ): bool,
                        vol.Optional(
                            CONF_ENABLE_MAINTENANCE_LIST,
                            default=options.get(
                                CONF_ENABLE_MAINTENANCE_LIST,
                                DEFAULT_ENABLE_MAINTENANCE_LIST,
                            ),
                        ): bool,
                        vol.Optional(
                            CONF_REGION_SENSORS,
                            default=options.get(
                                CONF_REGION_SENSORS, DEFAULT_REGION_SENSORS
                            ),
                        ): bool,
                        vol.Optional(
                            CONF_MAP_ROOM_LABELS,
                            default=options.get(
                                CONF_MAP_ROOM_LABELS,
                                DEFAULT_MAP_ROOM_LABELS,
                            ),
                        ): bool,
                        # THREE TICK BOXES, ALL OFF BY DEFAULT.
                        # @chairstacker's own design: his map in the app
                        # is already busy, and permanent zones would do
                        # the same here. Separate boxes because the three
                        # answer different questions -- where the robot
                        # should go, where it must not, and where it must
                        # not mop.
                        vol.Optional(
                            CONF_MAP_CLEAN_ZONES,
                            default=options.get(
                                CONF_MAP_CLEAN_ZONES, DEFAULT_MAP_ZONES
                            ),
                        ): bool,
                        vol.Optional(
                            CONF_MAP_KEEPOUT_ZONES,
                            default=options.get(
                                CONF_MAP_KEEPOUT_ZONES, DEFAULT_MAP_ZONES
                            ),
                        ): bool,
                        vol.Optional(
                            CONF_MAP_NOMOP_ZONES,
                            default=options.get(
                                CONF_MAP_NOMOP_ZONES, DEFAULT_MAP_ZONES
                            ),
                        ): bool,
                    }
                ),
            )

        if user_input is not None:
            updated = dict(self.config_entry.options)
            updated.update(user_input)
            return self.async_create_entry(title="", data=updated)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    # CONTINUOUS AND DELAY ARE GONE FROM THIS FORM (4.2).
                    #
                    # roombapy 2.x keeps one supervised connection and
                    # reconnects on its own -- the behaviour `continuous:
                    # true` used to select, and what this form's own
                    # description already recommended. There is no
                    # polling mode left to ask for, so offering the
                    # choice would be offering something nothing reads.
                    #
                    # The stored keys are left in the config entry
                    # rather than migrated away: a value nothing reads is
                    # harmless, and a migration whose only effect is to
                    # delete two numbers is a risk with no upside.
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
                    # Room names drawn INTO the map image. Same option
                    # the Prime form offers: this is a preference about
                    # maps, not about robot generations.
                    #
                    # Off by default, which reads backwards until you
                    # know that v2.7.3 removed these labels on purpose --
                    # the xiaomi-vacuum-map-card draws its own overlay
                    # from the `rooms` attribute, and both at once
                    # doubles them up.
                    vol.Optional(
                        CONF_REGION_SENSORS,
                        default=options.get(
                            CONF_REGION_SENSORS, DEFAULT_REGION_SENSORS
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_MAP_ROOM_LABELS,
                        default=options.get(
                            CONF_MAP_ROOM_LABELS,
                            DEFAULT_MAP_ROOM_LABELS,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_FLOOR,
                        default=options.get(CONF_FLOOR, ""),
                    ): str,
                    # v3.3.0 CROSS-CORR — opt-in: external sensors whose
                    # mission-start values get correlated with dirt counts
                    vol.Optional(
                        CONF_CORRELATION_ENTITIES,
                        default=options.get(CONF_CORRELATION_ENTITIES, []),
                    ): SelectorEntitySelector(
                        SelectorEntitySelectorConfig(
                            domain="sensor",
                            multiple=True,
                        )
                    ),
                    vol.Optional(
                        CONF_ENABLE_SCHEDULE_CALENDAR,
                        default=options.get(
                            CONF_ENABLE_SCHEDULE_CALENDAR, DEFAULT_ENABLE_SCHEDULE_CALENDAR
                        ),
                    ): bool,
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
                        # NOT JUST binary_sensor.
                        #
                        # @chairstacker wants to block missions on a
                        # "house state" -- vacation or guest mode --
                        # which in Home Assistant is an `input_boolean`,
                        # not a sensor. The check behind this reads
                        # `state.state` against {"on", "home", "true"}
                        # and never cared which domain produced it; the
                        # restriction was in this picker alone.
                        #
                        # `schedule` and `person`/`device_tracker` come
                        # along for the same reason: they report the
                        # same states, and "block while I am home" or
                        # "block outside these hours" are the same
                        # question as "block while this is on".
                        domain=[
                            "binary_sensor",
                            "input_boolean",
                            "schedule",
                            "person",
                            "device_tracker",
                        ],
                        multiple=True,
                    )
                ),
                vol.Optional(
                    CONF_BLOCKING_BEHAVIOR,
                    default=current.get(CONF_BLOCKING_BEHAVIOR, DEFAULT_BLOCKING_BEHAVIOR),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        # Labels come from strings.json -> selector.blocking_behavior,
                        # so they are translated like the rest of the form.
                        options=["abort", "queue"],
                        translation_key="blocking_behavior",
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
                        # Labels come from strings.json -> selector.presence_mode.
                        options=["away_only", "always_ask"],
                        translation_key="presence_mode",
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

    def _build_zone_index_options(self, data: Any, options: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Build selector option list for the map_management index step."""

        opts: list[dict[str, Any]] = []

        # ROOM-SEG Stage 4 — EPHEMERAL branch backed by RoomSegStore, not
        # ZoneStore (the gap heuristic proved unreliable — see
        # ROOM_SEGMENTATION_NOTES.md). SMART branch below is untouched.
        if data.map_capability == MapCapability.EPHEMERAL and data.room_seg_store:
            for room in data.room_seg_store.rooms.values():
                pending = self._pending_zone_edits.get(str(room.id), {})
                name = pending.get("display_name") or room.name
                hidden = pending.get("hidden", room.hidden)
                tags = []
                if hidden:
                    tags.append("hidden")
                if not room.confirmed:
                    tags.append("unconfirmed")
                if str(room.id) in self._pending_zone_edits:
                    tags.append("*")
                label = name + (f" [{', '.join(tags)}]" if tags else "")
                opts.append({"value": str(room.id), "label": label})

        elif data.map_capability == MapCapability.SMART:
            aliases: dict[str, Any] = options.get(CONF_SMART_ZONE_ALIASES, {})
            hidden_ids: list[Any] = options.get(CONF_SMART_ZONE_HIDDEN, [])
            zone_data: dict[str, Any] = options.get("smart_zone_data", {})
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
                tags = []
                if hidden:
                    tags.append("hidden")
                if rid in aliases:
                    tags.append("aliased")
                if rid in self._pending_zone_edits:
                    tags.append("*")
                label = base_name + (f" [{', '.join(tags)}]" if tags else "")
                opts.append({"value": rid, "label": label})

        return opts

    def _resolve_current_zone_name(self, zone_id: str, data: Any, options: Mapping[str, Any]) -> str:
        """Resolve the best current display name for zone_id."""

        if data.map_capability == MapCapability.EPHEMERAL and data.room_seg_store:
            room = data.room_seg_store.rooms.get(zone_id)
            if room is not None:
                return str(room.name)
            return f"Zone {zone_id}"

        aliases: dict[str, Any] = options.get(CONF_SMART_ZONE_ALIASES, {})
        if zone_id in aliases:
            return str(aliases[zone_id])
        zone_data: dict[str, Any] = options.get("smart_zone_data", {})
        if zone_id in zone_data:
            return zone_data[zone_id].get("name") or f"Zone {zone_id}"
        if data.has_cloud:
            for r in data.cloud_coordinator.regions:
                if str(r.get("id")) == zone_id:
                    return r.get("name") or f"Zone {zone_id}"
        return f"Zone {zone_id}"

    def _resolve_current_zone_hidden(self, zone_id: str, data: Any, options: Mapping[str, Any]) -> bool:
        """Return the current hidden state for zone_id."""

        if data.map_capability == MapCapability.EPHEMERAL and data.room_seg_store:
            room = data.room_seg_store.rooms.get(zone_id)
            if room is not None:
                return bool(room.hidden)
            return False
        return zone_id in options.get(CONF_SMART_ZONE_HIDDEN, [])

    def _save_zone_edits_atomic(self) -> ConfigFlowResult:
        """Apply all pending zone edits atomically in a single options write."""

        data = self.config_entry.runtime_data
        options = dict(self.config_entry.options)

        if data.map_capability == MapCapability.EPHEMERAL and data.room_seg_store:
            # ROOM-SEG Stage 4 — RoomSegStore.SegRoom.id is already a string
            # ("room_1", ...), unlike ZoneStore.Zone.id which was an int.
            # No int(zone_id_str) cast here — that would raise on a string
            # id like "room_1" if it were still present from the old code.
            for room_id, edit in self._pending_zone_edits.items():
                if edit.get("hidden"):
                    data.room_seg_store.hide_room(room_id)
                else:
                    data.room_seg_store.unhide_room(room_id)
                    name = edit.get("display_name", "").strip()
                    if name:
                        data.room_seg_store.rename_room(room_id, name)
            self.hass.async_create_task(
                data.room_seg_store.async_save(self.hass, self.config_entry.entry_id),
                name="roomba_plus_room_seg_store_save",
            )
        else:
            # SMART: alias layer in options
            aliases: dict[str, Any] = dict(options.get(CONF_SMART_ZONE_ALIASES, {}))
            hidden: list[Any] = list(options.get(CONF_SMART_ZONE_HIDDEN, []))
            zone_data: dict[str, Any] = options.get("smart_zone_data", {})

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

        Dynamically generates one text field per unconfirmed room.

        ROOM-SEG Stage 4 — backed by RoomSegStore, not ZoneStore (the gap
        heuristic proved unreliable — see ROOM_SEGMENTATION_NOTES.md).
        """

        data = self.config_entry.runtime_data
        if data.map_capability != MapCapability.EPHEMERAL or not data.room_seg_store:
            return self.async_create_entry(title="", data=self.config_entry.options)

        room_seg_store: RoomSegStore = data.room_seg_store
        unconfirmed = room_seg_store.unconfirmed_rooms

        if user_input is not None:
            for room in unconfirmed:
                name = user_input.get(f"zone_{room.id}", "").strip()
                if name:
                    room_seg_store.rename_room(room.id, name)
            # Persist
            self.hass.async_create_task(
                room_seg_store.async_save(self.hass, self.config_entry.entry_id)
            )
            return self.async_create_entry(title="", data=self.config_entry.options)

        if not unconfirmed:
            return self.async_create_entry(title="", data=self.config_entry.options)

        schema = vol.Schema({
            vol.Optional(f"zone_{r.id}", default=r.name): str
            for r in unconfirmed
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

        # THREE SOURCES, NOT ONE.
        #
        # This read only `cleanSchedule2`, so a robot whose owner has
        # never built a schedule WITH ROOMS offered nothing to name --
        # the step opened and immediately reported itself finished
        # (@connormxy). His robot knows its twelve rooms perfectly well;
        # they simply are not in a schedule.
        #
        # `lastCommand` holds the regions of the most recent clean, and
        # the cloud coordinator holds the full list. Either is a better
        # answer than an empty screen.
        region_ids: set[str] = set()

        last = state.get("lastCommand")
        if isinstance(last, dict):
            for region in last.get("regions") or []:
                if isinstance(region, dict):
                    rid = region.get("region_id") or region.get("id")
                    if rid:
                        region_ids.add(str(rid))

        data = getattr(self.config_entry, "runtime_data", None)
        coordinator = getattr(data, "cloud_coordinator", None)
        for region in getattr(coordinator, "regions", None) or []:
            rid = region.get("id") if isinstance(region, dict) else None
            if rid:
                region_ids.add(str(rid))

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

        existing_labels: dict[str, Any] = self.config_entry.options.get(
            "smart_zone_labels", {}
        )
        unlabelled = sorted(rid for rid in region_ids if rid not in existing_labels)

        if user_input is not None:
            new_labels = dict(existing_labels)
            new_zone_data: dict[str, Any] = dict(
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
                pmaps: list[dict[str, Any]] = state.get("pmaps", [])
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
            # SAY WHY, rather than reporting success for doing nothing.
            #
            # This step opened and immediately announced itself finished
            # (@connormxy), which reads as "done" when it means "found
            # nothing". The two cases below are different problems and
            # deserve different sentences.
            return self.async_abort(
                reason="no_rooms_to_name" if not region_ids
                else "all_rooms_named"
            )

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
        existing_labels: dict[str, Any] = self.config_entry.options.get("smart_zone_labels", {})
        existing_zone_data: dict[str, Any] = self.config_entry.options.get("smart_zone_data", {})

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
                pmaps: list[dict[str, Any]] = state.get("pmaps", [])
                if pmaps:
                    current_pmap_id = next(iter(pmaps[0]), "")

            # BOTH ERRORS RETURN THE FORM. `no_valid_ids` used to be set and
            # then fall through to the save: a user who typed "3: Kitchen"
            # instead of "3=Kitchen" saw the step close as if it had
            # worked, with nothing saved and the error never shown. Only
            # `pmap_not_resolved` returned. Found by the config-flow
            # coverage work for the quality scale.
            if not parsed or not current_pmap_id:
                errors["zone_names"] = (
                    "no_valid_ids" if not parsed else "pmap_not_resolved"
                )
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

    async def async_step_cloud_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or update iRobot cloud credentials.

        CF2-FULL (v2.7.0): form collects credentials only; connection test
        runs in async_step_test_cloud_connection before saving so the user
        gets a specific error (one per cause, cloud_<reason>)
        rather than a silent post-reload failure.

        Saves credentials to config_entry.data (not options), then triggers a
        reload so the cloud coordinator is re-initialised with the new values.
        Clearing both fields removes the credentials and disables cloud features.
        """
        errors: dict[str, str] = getattr(self, "_cloud_cred_errors", {})
        self._cloud_cred_errors: dict[str, str] = {}
        current_user = self.config_entry.data.get(CONF_IROBOT_USERNAME, "")

        if user_input is not None:
            # Store pending credentials and route to the test step.
            # Clearing both fields skips the test and goes straight to save.
            self._pending_cloud_creds = {
                "username": user_input.get(CONF_IROBOT_USERNAME, "").strip(),
                "password": user_input.get(CONF_IROBOT_PASSWORD, "").strip(),
            }
            return await self.async_step_test_cloud_connection()

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

    async def async_step_test_cloud_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """CF2-FULL (v2.7.0) — test cloud connection before saving credentials.

        Validates the pending credentials stored by async_step_cloud_credentials.
        Returns to the credentials form with a specific error on failure:
          cloud_<reason> — one per cause (cloud_errors.flow_error())
        On success (or when credentials were cleared): saves and reloads.
        """
        pending = getattr(self, "_pending_cloud_creds", {})
        username = pending.get("username", "")
        password = pending.get("password", "")

        new_data = dict(self.config_entry.data)

        if username and password:
            account, error = await _async_check_cloud_login(
                self.hass, username, password
            )
            if account is None:
                self._cloud_cred_errors = {"base": error or "cloud_unknown"}
                return await self.async_step_cloud_credentials()
            async_offer(self.hass, account, username, password)
            _async_update_account_siblings(
                self.hass, self.config_entry, username, password
            )
            new_data[CONF_IROBOT_USERNAME] = username
            new_data[CONF_IROBOT_PASSWORD] = password
        else:
            # Credentials cleared — disable cloud without testing
            new_data.pop(CONF_IROBOT_USERNAME, None)
            new_data.pop(CONF_IROBOT_PASSWORD, None)

        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data
        )
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data=self.config_entry.options)

    # ── F13 / F11 — Demand cleaning configuration ─────────────────────────────

    async def async_step_room_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """v3.3.0 ROOM-SCHED — per-room cleaning frequency.

        Gate: SMART + cloud (same as demand_cleaning) — named cloud
        regions are the only stable config keys. One SelectSelector per
        room; "learned" (default) keeps the self-calibrated interval
        from COVERAGE-FREQ. Rooms that vanished from the cloud map are
        silently filtered on save (no orphan config).
        """
        data = self.config_entry.runtime_data
        if (
            not hasattr(data, "map_capability")
            or data.map_capability.value != "smart"
            or not data.has_cloud
            or data.cloud_coordinator is None
        ):
            return self.async_abort(reason="room_schedule_not_supported")

        room_names = sorted(
            r["name"]
            for r in (data.cloud_coordinator.regions or [])
            if r.get("name")
        )
        if not room_names:
            return self.async_abort(reason="room_schedule_no_rooms")

        if user_input is not None:
            updated = dict(self.config_entry.options)
            schedule = {
                room: freq
                for room, freq in user_input.items()
                if room in room_names                      # orphan filter
                and freq in ROOM_SCHEDULE_INTERVALS        # "learned" drops out
            }
            updated[CONF_ROOM_SCHEDULE] = schedule
            return self.async_create_entry(title="", data=updated)

        current = self.config_entry.options.get(CONF_ROOM_SCHEDULE) or {}
        freq_options = [ROOM_SCHEDULE_LEARNED, *ROOM_SCHEDULE_INTERVALS]
        schema = vol.Schema({
            vol.Optional(
                room,
                default=current.get(room, ROOM_SCHEDULE_LEARNED),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=freq_options,
                    translation_key="room_schedule_frequency",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            for room in room_names
        })
        return self.async_show_form(step_id="room_schedule", data_schema=schema)

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
    """Create a RoombaDiscovery instance.

    NO LONGER CAPPED, because there is nothing to cap. roombapy 1.x
    exposed `amount_of_broadcasted_messages`, the number of UDP
    broadcasts it sent before giving up, and this set it to
    MAX_NUM_DEVICES_TO_DISCOVER -- which conflated "how many probes" with
    "how many robots", and had been that way long enough that nobody
    noticed the two are unrelated.

    2.x replaces it with a plain `timeout` on `get_all()`, which is the
    honest control: discovery listens for that long and returns whatever
    answered. A household with more robots than the old cap now finds
    all of them.
    """
    return RoombaDiscovery()


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
                    device = await discovery.get(host)
                    if device:
                        discovered.add(device)
                else:
                    discovered = await discovery.get_all()
            except OSError:
                await asyncio.sleep(ROOMBA_WAKE_TIME * attempt)
                continue
            else:
                for device in discovered:
                    if device.ip not in discovered_hosts:
                        discovered_hosts.add(device.ip)
                        devices.append(device)
            finally:
                # `server_socket` was the 1.x internal; 2.x owns its
                # transport and closes it here. Still in `finally`, for
                # the same reason as before -- a discovery that raises
                # must not leave the socket bound, or the next attempt
                # in this loop fails on the port.
                await discovery.aclose()

        if host and host in discovered_hosts:
            return devices

        await asyncio.sleep(ROOMBA_WAKE_TIME)

    return devices
