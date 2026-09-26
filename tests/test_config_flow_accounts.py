"""Setting up several robots of one iRobot account (4.3).

Until 4.2 every robot was its own setup from scratch: the account's
credentials typed again each time, one robot picked per run, and on the
local path the HOME button pressed on every robot. These tests run the
flow through Home Assistant's own flow manager and hold it to what
replaces that:

  * an account another entry already uses is offered, not asked for
  * after one robot, the account's other robots appear as discovered,
    one click each, carrying no secret
  * a robot found on the network whose BLID is on a known account needs
    no HOME button: its password comes from the account
  * a Classic robot added from the account keeps the account without
    a separate question

Only the network edges are replaced: the cloud login, robot discovery
on the LAN, the connection test, and the entry's own setup.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from roombapy_prime import AuthCredentialsError

from custom_components.roomba_plus import config_flow as cf
from custom_components.roomba_plus.const import (
    CONF_BLID,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    DOMAIN,
)

USER = "owner@example.com"
PW = "hunter2"
PRIME = "G1BLID00000001"
CLASSIC = "C1BLID00000001"
CLASSIC_SET_UP = "C2BLID00000001"
HOST = "10.0.0.21"

ROBOTS = {
    PRIME: {"name": "Mopsy", "sku": "G185020", "password": ":1:prime"},
    CLASSIC: {"name": "Rosie", "sku": "i755840", "password": ":1:classic"},
    CLASSIC_SET_UP: {"name": "Otto", "sku": "R980020", "password": ":1:otto"},
}


def _account(robots: dict | None = None) -> MagicMock:
    account = MagicMock(name="CloudAccount")
    account.login_result.raw = {"robots": dict(ROBOTS if robots is None else robots)}
    return account


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations):
    """Let Home Assistant's loader find custom_components/roomba_plus,
    and keep a created entry from actually starting."""
    with patch("custom_components.roomba_plus.async_setup_entry", return_value=True):
        yield


@pytest.fixture
def login():
    """The cloud login, counted. Returns an account listing ROBOTS."""
    mock = AsyncMock(side_effect=lambda *_a, **_k: _account())
    with patch.object(cf, "async_login", new=mock):
        yield mock


def _known_entry(hass, *, blid: str = CLASSIC_SET_UP, password: str = PW,
                 state: ConfigEntryState | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=blid, title=ROBOTS.get(blid, {}).get("name", blid),
        data={CONF_BLID: blid, "host": "10.0.0.99", "password": "x",
              CONF_IROBOT_USERNAME: USER, CONF_IROBOT_PASSWORD: password},
    )
    if state is not None:
        entry.mock_state(hass, state)
    entry.add_to_hass(hass)
    return entry


def _discoveries(hass) -> dict[str, dict]:
    return {
        f["context"]["unique_id"]: f
        for f in hass.config_entries.flow.async_progress()
        if f["context"]["source"] == config_entries.SOURCE_INTEGRATION_DISCOVERY
    }


async def _account_path(hass):
    with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": cf._CLOUD_ACCOUNT_SENTINEL}
    )


def _option_values(result) -> list[str]:
    selector = result["data_schema"].schema["account"]
    return [o["value"] for o in selector.config["options"]]


class TestTheAccountComesFirst:

    @pytest.mark.asyncio
    async def test_it_is_the_first_choice(self, hass):
        with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[])):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
        choices = list(result["data_schema"].schema["host"].container)
        assert choices[0] == cf._CLOUD_ACCOUNT_SENTINEL
        assert "recommended" in result["data_schema"].schema["host"].container[choices[0]]


class TestAKnownAccountIsOffered:

    @pytest.mark.asyncio
    async def test_the_account_path_offers_it_instead_of_asking(self, hass, login):
        _known_entry(hass)
        result = await _account_path(hass)
        assert result["step_id"] == "known_account"
        assert _option_values(result) == [USER, "__other__"]   # no "no cloud" here

    @pytest.mark.asyncio
    async def test_choosing_it_logs_in_once_and_lists_the_robots(self, hass, login):
        """Still a login -- only a fresh one lists a robot added since,
        and it proves the stored password still works."""
        _known_entry(hass)
        result = await _account_path(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account": USER}
        )
        assert result["step_id"] == "prime_robot_picker"
        login.assert_awaited_once_with(hass, USER, PW)
        choices = result["data_schema"].schema[CONF_BLID].container
        assert set(choices) == {PRIME, CLASSIC}
        # The one already set up is named, not silently missing.
        assert result["description_placeholders"]["configured"] == "Otto"

    @pytest.mark.asyncio
    async def test_another_account_is_still_possible(self, hass, login):
        _known_entry(hass)
        result = await _account_path(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account": "__other__"}
        )
        assert result["step_id"] == "prime_account"
        login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_stored_password_that_stopped_working_says_so(self, hass):
        _known_entry(hass)
        result = await _account_path(hass)
        with patch.object(cf, "async_login", AsyncMock(side_effect=AuthCredentialsError("no"))):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"account": USER}
            )
        assert result["step_id"] == "known_account"
        assert result["errors"] == {"base": "cloud_credentials_rejected"}

    def test_the_loaded_entrys_password_wins(self, hass):
        """Two entries of one account, one with an outdated password: the
        loaded entry's is the one that currently works."""
        _known_entry(hass, blid="A", password="old", state=ConfigEntryState.SETUP_ERROR)
        _known_entry(hass, blid="B", password="current", state=ConfigEntryState.LOADED)
        _known_entry(hass, blid="C", password="old", state=ConfigEntryState.SETUP_ERROR)
        flow = cf.RoombaPlusConfigFlow()
        flow.hass = hass
        flow.handler = DOMAIN
        assert flow._known_accounts() == {USER.casefold(): (USER, "current")}


class TestTheOtherRobotsAreAnnounced:

    async def _add_prime(self, hass):
        result = await _account_path(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account": USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BLID: PRIME}
        )
        await hass.async_block_till_done()
        return result

    @pytest.mark.asyncio
    async def test_after_one_robot_the_rest_appear_as_discovered(self, hass, login):
        _known_entry(hass)
        result = await self._add_prime(hass)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        found = _discoveries(hass)
        assert set(found) == {CLASSIC}           # not the one added, not the one set up
        assert found[CLASSIC]["context"]["title_placeholders"] == {"name": "Rosie"}

    @pytest.mark.asyncio
    async def test_a_discovery_carries_no_secret(self, hass, login):
        _known_entry(hass)
        with patch.object(cf.discovery_flow, "async_create_flow") as create:
            await self._add_prime(hass)
        data = create.call_args.kwargs["data"]
        assert set(data) == {CONF_BLID, "name", "sku", CONF_IROBOT_USERNAME}
        assert PW not in json.dumps(data) and ":1:classic" not in json.dumps(data)

    @pytest.mark.asyncio
    async def test_an_ignored_robot_stays_ignored(self, hass, login):
        _known_entry(hass)
        MockConfigEntry(
            domain=DOMAIN, unique_id=CLASSIC, source=config_entries.SOURCE_IGNORE
        ).add_to_hass(hass)
        with patch.object(cf.discovery_flow, "async_create_flow") as create:
            await self._add_prime(hass)
        # Not even started: Home Assistant would abort it, but a flow
        # started only to be aborted is noise in the log.
        create.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_click_adds_a_classic_robot_with_the_account(self, hass, login):
        """The login the new Prime entry holds lists the robot, so the
        click needs no second login; the IP comes from the network scan."""
        _known_entry(hass)
        await self._add_prime(hass)
        flow_id = _discoveries(hass)[CLASSIC]["flow_id"]
        form = await hass.config_entries.flow.async_configure(flow_id)
        assert form["step_id"] == "integration_discovery_confirm"
        assert form["description_placeholders"] == {
            "name": "Rosie", "model": "i755840", "account": USER,
        }

        device = MagicMock(blid=CLASSIC, ip=HOST)
        with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[device])), \
             patch.object(cf, "validate_input", AsyncMock(return_value={"name": "Rosie"})):
            result = await hass.config_entries.flow.async_configure(flow_id, {})

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["host"] == HOST
        assert result["data"]["password"] == ":1:classic"
        assert result["data"][CONF_IROBOT_USERNAME] == USER
        assert login.await_count == 1            # the picker's, nothing more

    @pytest.mark.asyncio
    async def test_a_discovered_prime_robot_is_added_without_announcing_again(self, hass, monkeypatch):
        """A discovery flow must not announce the account's robots once
        more -- here a third robot, announced already by the first setup."""
        _known_entry(hass)
        robots = {**ROBOTS, "X3BLID00000001": {"name": "Third", "sku": "G185020"}}
        login = AsyncMock(side_effect=lambda *_a, **_k: _account(robots))
        monkeypatch.setattr(cf, "async_login", login)
        # Set up the Classic robot from the account, which announces Prime.
        result = await _account_path(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"account": USER}
        )
        device = MagicMock(blid=CLASSIC, ip=HOST)
        with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[device])), \
             patch.object(cf, "validate_input", AsyncMock(return_value={"name": "Rosie"})):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_BLID: CLASSIC}
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        # No separate "keep the credentials?" question any more.
        assert result["data"][CONF_IROBOT_USERNAME] == USER
        await hass.async_block_till_done()
        flow_id = _discoveries(hass)[PRIME]["flow_id"]

        assert "X3BLID00000001" in _discoveries(hass)

        with patch.object(cf.discovery_flow, "async_create_flow") as again:
            await hass.config_entries.flow.async_configure(flow_id)
            result = await hass.config_entries.flow.async_configure(flow_id, {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_BLID] == PRIME
        again.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_the_account_any_more_it_asks_for_it(self, hass, login):
        """The entries that used the account were removed before the
        click: ask for the account like a fresh setup."""
        entry = _known_entry(hass)
        await self._add_prime(hass)
        flow_id = _discoveries(hass)[CLASSIC]["flow_id"]
        for e in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(e.entry_id)
        del entry
        await hass.config_entries.flow.async_configure(flow_id)
        result = await hass.config_entries.flow.async_configure(flow_id, {})
        assert result["step_id"] == "prime_account"

    @pytest.mark.asyncio
    async def test_a_robot_that_left_the_account_is_not_added(self, hass):
        _known_entry(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={CONF_BLID: CLASSIC, "name": "Rosie", "sku": "i755840",
                  CONF_IROBOT_USERNAME: USER},
        )
        assert result["step_id"] == "integration_discovery_confirm"
        without = {k: v for k, v in ROBOTS.items() if k != CLASSIC}
        with patch.object(cf, "async_login", AsyncMock(return_value=_account(without))):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "robot_not_on_account"


def _dhcp(blid: str = CLASSIC) -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=HOST, hostname=f"irobot-{blid.lower()}", macaddress="aabbccddeeff")


class TestNoHomeButtonForAKnownAccount:
    """A DHCP discovery runs to its first form without anyone clicking,
    so the account's part of it must not touch the network: it reads
    the login a loaded entry holds, and tries the password on the robot
    only once the user answers."""

    def _held(self, hass, robots: dict | None = None) -> MagicMock:
        """The login a loaded entry of the account holds."""
        from custom_components.roomba_plus import cloud_account

        account = _account(robots)
        cloud_account.async_offer(hass, account, USER, PW)
        return account

    async def _dhcp_flow(self, hass, blid: str = CLASSIC):
        device = MagicMock(blid=blid, ip=HOST, sku="i755840", robot_name="Rosie")
        with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[device])), \
             patch.object(cf, "validate_input", AsyncMock()) as validate, \
             patch.object(cf, "RoombaPassword") as button:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(blid)
            )
        validate.assert_not_awaited()      # no connection to the robot yet
        return result, button

    async def _answer(self, hass, result, choice: str, validate=None):
        with patch.object(cf, "validate_input",
                          validate or AsyncMock(return_value={"name": "Rosie"})):
            return await hass.config_entries.flow.async_configure(
                result["flow_id"], {"account": choice}
            )

    @pytest.mark.asyncio
    async def test_the_password_comes_from_the_account(self, hass, login):
        _known_entry(hass)
        self._held(hass)
        result, button = await self._dhcp_flow(hass)
        button.assert_not_called()
        login.assert_not_awaited()         # nothing over the network yet
        assert result["step_id"] == "known_account"
        assert _option_values(result) == [USER, "__other__", "__none__"]

        result = await self._answer(hass, result, USER)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["password"] == ":1:classic"
        assert result["data"][CONF_IROBOT_USERNAME] == USER
        assert login.await_count == 1      # choosing the account checks it

    @pytest.mark.asyncio
    async def test_no_cloud_is_still_a_choice(self, hass, login):
        _known_entry(hass)
        self._held(hass)
        result, _button = await self._dhcp_flow(hass)
        result = await self._answer(hass, result, "__none__")
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert CONF_IROBOT_USERNAME not in result["data"]
        assert result["data"]["password"] == ":1:classic"
        login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_another_account_leads_to_the_credentials_form(self, hass, login):
        _known_entry(hass)
        self._held(hass)
        result, _button = await self._dhcp_flow(hass)
        result = await self._answer(hass, result, "__other__")
        assert result["step_id"] == "cloud_credentials"

    @pytest.mark.asyncio
    async def test_a_password_the_robot_refuses_leads_to_the_button_saying_why(self, hass, login):
        _known_entry(hass)
        self._held(hass)
        result, _button = await self._dhcp_flow(hass)
        result = await self._answer(
            hass, result, USER, validate=AsyncMock(side_effect=cf.CannotConnect)
        )
        assert result["step_id"] == "link"
        assert result["errors"] == {"base": "account_password_refused"}

    @pytest.mark.asyncio
    async def test_a_robot_not_in_the_held_login_needs_the_button(self, hass, login):
        _known_entry(hass)
        self._held(hass)
        result, _button = await self._dhcp_flow(hass, blid="UNKNOWNBLID001")
        assert result["step_id"] == "link"
        login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_account_no_loaded_entry_holds_is_not_logged_in_to(self, hass, login):
        """Its stored password may be the stale one. Logging in on every
        discovery, after every restart, would be one failed attempt more
        against the account each time."""
        _known_entry(hass, state=ConfigEntryState.SETUP_ERROR)
        result, _button = await self._dhcp_flow(hass)
        assert result["step_id"] == "link"
        login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_known_account_nothing_changes(self, hass, login):
        result, _button = await self._dhcp_flow(hass)
        assert result["step_id"] == "link"
        login.assert_not_awaited()


class TestReviewFindings:
    """Found by an independent review of this flow before release."""

    @pytest.mark.asyncio
    async def test_a_typed_password_is_checked_even_after_a_login_of_the_same_address(self, hass, login):
        """The per-flow login cache was keyed by address alone: "another
        account" with the same address and a typo got the earlier login
        back, and the typo was stored."""
        flow = cf.RoombaPlusConfigFlow()
        flow.hass = hass
        await flow._async_login(USER, PW)
        await flow._async_login(USER, "typo")
        await flow._async_login(USER, PW)
        assert [c.args[1:] for c in login.await_args_list] == [(USER, PW), (USER, "typo")]

    @pytest.mark.asyncio
    async def test_a_stale_password_on_the_discovered_card_asks_for_the_account(self, hass):
        """The card has no password field. Clicking it again and again
        would only repeat a refused login against the account."""
        _known_entry(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={CONF_BLID: CLASSIC, "name": "Rosie", "sku": "i755840",
                  CONF_IROBOT_USERNAME: USER},
        )
        with patch.object(cf, "async_login", AsyncMock(side_effect=AuthCredentialsError("no"))):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "prime_account"
        assert result["errors"] == {"base": "cloud_credentials_rejected"}

    @pytest.mark.asyncio
    async def test_the_account_form_of_a_card_adds_that_robot_not_a_list(self, hass):
        """Offering the whole list there let the user add another robot
        under this robot's card; the robot the card was for was then
        never announced again."""
        _known_entry(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={CONF_BLID: PRIME, "name": "Mopsy", "sku": "G185020",
                  CONF_IROBOT_USERNAME: USER},
        )
        with patch.object(cf, "async_login", AsyncMock(side_effect=AuthCredentialsError("no"))):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        with patch.object(cf, "async_login", AsyncMock(return_value=_account())):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_IROBOT_USERNAME: USER, CONF_IROBOT_PASSWORD: "new"},
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_BLID] == PRIME
        assert result["data"][CONF_IROBOT_PASSWORD] == "new"

    @pytest.mark.asyncio
    async def test_a_robot_found_on_the_network_does_not_end_the_users_setup(self, hass, login):
        """A flow that has no robot yet has no unique id, and every BLID
        "starts with" the empty string -- so a DHCP discovery aborted
        whatever "Add integration" the user had open. Pre-existing, but
        the account path, now the default, sits without a robot longer."""
        _known_entry(hass)
        users = await _account_path(hass)
        assert users["step_id"] == "known_account"
        device = MagicMock(blid=CLASSIC, ip=HOST, sku="i755840", robot_name="Rosie")
        with patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=[device])):
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp()
            )
        flows = {f["flow_id"] for f in hass.config_entries.flow.async_progress()}
        assert users["flow_id"] in flows


@pytest.mark.parametrize("lang", ["de", "en", "es", "fr", "it", "nl", "pl", "pt"])
def test_the_account_choice_is_translated(lang):
    """The two fixed choices sit next to e-mail addresses, so the
    selector's options are built at runtime and the generic selector
    check cannot see them."""
    data = json.loads(pathlib.Path(
        f"custom_components/roomba_plus/translations/{lang}.json").read_text(encoding="utf-8"))
    assert set(data["selector"]["known_account"]["options"]) == {"__other__", "__none__"}
    step = data["config"]["step"]["integration_discovery_confirm"]
    assert {"{name}", "{model}", "{account}"} <= set(
        __import__("re").findall(r"\{\w+\}", step["description"]))
    assert "{configured}" in data["config"]["step"]["prime_robot_picker"]["description"]


class TestOnePasswordChangeForTheWholeAccount:
    """A password changed in the iRobot app makes every robot of the
    account fail together. Entering the new one once must be enough."""

    def _entries(self, hass):
        same = [_known_entry(hass, blid=b, password="old") for b in ("R1", "R2", "R3")]
        other_pw = _known_entry(hass, blid="R4", password="different")
        other_user = MockConfigEntry(
            domain=DOMAIN, unique_id="R5",
            data={CONF_BLID: "R5", CONF_IROBOT_USERNAME: "someone@else.com",
                  CONF_IROBOT_PASSWORD: "old"},
        )
        other_user.add_to_hass(hass)
        return same, other_pw, other_user

    def test_the_others_with_the_same_old_password_follow(self, hass):
        same, other_pw, other_user = self._entries(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            followed = cf._async_update_account_siblings(hass, same[0], USER, "new")
        assert followed == 2
        assert [e.data[CONF_IROBOT_PASSWORD] for e in same] == ["old", "new", "new"]
        assert {c.args[0] for c in reload.call_args_list} == {same[1].entry_id, same[2].entry_id}
        # A different password may be one that works; another account is another account.
        assert other_pw.data[CONF_IROBOT_PASSWORD] == "different"
        assert other_user.data[CONF_IROBOT_PASSWORD] == "old"

    def test_nothing_changes_without_a_change(self, hass):
        same, _other_pw, _other_user = self._entries(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            assert cf._async_update_account_siblings(hass, same[0], USER, "old") == 0
        reload.assert_not_called()

    def test_another_account_is_not_passed_on(self, hass):
        """Found in review: moving one robot to another household's
        account switched every robot of the old one along with it."""
        same, _other_pw, _other_user = self._entries(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            assert cf._async_update_account_siblings(hass, same[0], "new@owner.com", "new") == 0
        reload.assert_not_called()
        assert [e.data[CONF_IROBOT_USERNAME] for e in same[1:]] == [USER, USER]

    def test_the_address_typed_in_other_letters_is_no_change(self, hass):
        same, _other_pw, _other_user = self._entries(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            assert cf._async_update_account_siblings(hass, same[0], USER.upper(), "old") == 0
        reload.assert_not_called()

    def test_an_entry_that_had_no_account_passes_nothing_on(self, hass):
        self._entries(hass)
        local = MockConfigEntry(domain=DOMAIN, unique_id="L1", data={CONF_BLID: "L1"})
        local.add_to_hass(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            assert cf._async_update_account_siblings(hass, local, USER, "new") == 0
        reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_reauthentication_of_one_robot_updates_the_others(self, hass):
        same, other_pw, _other_user = self._entries(hass)
        result = await same[0].start_reauth_flow(hass)
        with patch.object(cf, "async_login", AsyncMock(return_value=_account())), \
             patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_IROBOT_USERNAME: USER, CONF_IROBOT_PASSWORD: "new"},
            )
        assert result["reason"] == "reauth_successful"
        assert [e.data[CONF_IROBOT_PASSWORD] for e in same] == ["new", "new", "new"]
        assert other_pw.data[CONF_IROBOT_PASSWORD] == "different"
        reloaded = {c.args[0] for c in reload.call_args_list}
        assert {same[1].entry_id, same[2].entry_id} <= reloaded
