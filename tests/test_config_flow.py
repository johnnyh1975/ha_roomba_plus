"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.

NOTE — ROOM-SEG Stage 4 classes added below (TestBuildZoneIndexOptions-
Ephemeral onward) import custom_components.roomba_plus.config_flow,
which itself imports homeassistant.helpers.service_info.dhcp and
.zeroconf. Both are missing from this sandbox's pinned HA version (same
pre-existing gap documented for test_rest980_migrate.py since v2.9.1 —
"uncollectable in sandbox ... but passes in real env"). Verified locally
with temporary stub modules before shipping (all 16 pass); verify again
in a real HA environment before release, same as test_rest980_migrate.py.
"""


from __future__ import annotations



import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest

# MODULE SCOPE, not inside the test: the `hass` fixture rebuilds the
# custom-integration module space, so an import in a test body that uses
# it no longer resolves.
from custom_components.roomba_plus.const import (
    CONF_BLID, CONF_IROBOT_PASSWORD, CONF_IROBOT_USERNAME,
        )


class TestCF2PmapResolution:
    """CF2: pmap_id resolved before the 'elif not current_pmap_id' validation."""

    def test_pmap_resolution_happens_before_validation(self):
        """Verify pmap_id is available at Phase 2 validation time."""
        # Simulate Phase 2 data flow: pmap is in lastCommand
        state = {
            "lastCommand": {"pmap_id": "ABC123", "command": "start"},
            "cleanSchedule2": [],
            "pmaps": [],
        }
        # Resolution logic (copied from config_flow fix)
        current_pmap_id = ""
        last = state.get("lastCommand", {})
        if last.get("pmap_id"):
            current_pmap_id = last["pmap_id"]

        assert current_pmap_id == "ABC123"

    def test_pmap_fallback_to_schedule2(self):
        state = {
            "lastCommand": {},
            "cleanSchedule2": [{"cmd": {"pmap_id": "SCHED1", "regions": []}}],
            "pmaps": [],
        }
        current_pmap_id = ""
        last = state.get("lastCommand", {})
        if last.get("pmap_id"):
            current_pmap_id = last["pmap_id"]
        else:
            for entry in state.get("cleanSchedule2", []):
                if entry.get("cmd", {}).get("pmap_id"):
                    current_pmap_id = entry["cmd"]["pmap_id"]
                    break
        assert current_pmap_id == "SCHED1"


class TestI1OptionsReload:
    """I1: _async_reload_on_options_change syncs data to prevent false retriggers."""

    def test_reload_triggered_on_connection_change(self):
        data = {"continuous": True, "delay": 1}
        options = {"continuous": False, "delay": 1}  # changed
        connection_keys = {"continuous", "delay"}
        old = {k: data.get(k) for k in connection_keys}
        new = {k: options.get(k) for k in connection_keys}
        assert old != new  # reload triggered

    def test_no_reload_after_sync(self):
        """After syncing data with new options, next options change doesn't reload."""
        # Simulate post-sync state: data updated to match options
        data = {"continuous": False, "delay": 1}  # synced
        options = {"continuous": False, "delay": 1, "blocking_sensors": ["x"]}
        connection_keys = {"continuous", "delay"}
        old = {k: data.get(k) for k in connection_keys}
        new = {k: options.get(k) for k in connection_keys}
        assert old == new  # no reload


def _make_options_flow(room_seg_store=None):
    """ROOM-SEG Stage 4 -- minimal RoombaPlusOptionsFlow test double.

    Exercises the REAL methods (not a logic-mirror copy like the classes
    above) -- this catches a wrong attribute name or wrong store
    reference the way the rest of this file's re-implemented-inline style
    cannot. Same __new__-bypass pattern used throughout this project for
    HA entity/flow classes (see test_image.py, test_select.py).
    """
    from custom_components.roomba_plus.config_flow import RoombaPlusOptionsFlow
    from custom_components.roomba_plus.models import MapCapability

    flow = RoombaPlusOptionsFlow.__new__(RoombaPlusOptionsFlow)
    flow._pending_zone_edits = {}
    config_entry = MagicMock()
    config_entry.runtime_data.map_capability = MapCapability.EPHEMERAL
    config_entry.runtime_data.room_seg_store = room_seg_store
    config_entry.options = {}
    config_entry.entry_id = "test_entry"
    flow._config_entry = config_entry
    try:
        flow._config_entry = config_entry
    except (RuntimeError, AttributeError):
        # HA deprecated direct assignment, then removed the setter
        # entirely (2026.x raises AttributeError rather than
        # RuntimeError). `_config_entry` above is what the flow reads,
        # so both are survivable.
        pass
    flow.hass = MagicMock()
    flow.hass.async_create_task = MagicMock()
    # HA 2026.x: `config_entry` resolves through `_config_entry_id`,
    # which is `self.handler`, then looks the entry up on hass. Setting
    # the attribute directly stopped working -- the setter is gone -- so
    # the flow has to be given the same two things a real one gets.
    flow.handler = config_entry.entry_id
    flow.hass.config_entries.async_get_known_entry.return_value = config_entry
    return flow


class TestBuildZoneIndexOptionsEphemeral:
    def test_lists_each_room_with_name(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True),
            "room_2": SegRoom(id="room_2", name="Bedroom", confirmed=True),
        }
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        values = {o["value"] for o in opts}
        assert values == {"room_1", "room_2"}

    def test_unconfirmed_room_tagged(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=False)}
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert "unconfirmed" in opts[0]["label"]

    def test_hidden_room_tagged(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True, hidden=True)}
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert "hidden" in opts[0]["label"]

    def test_pending_edit_overrides_displayed_name(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        flow._pending_zone_edits = {"room_1": {"display_name": "Office"}}
        data = flow._config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert opts[0]["label"].startswith("Office")

    def test_no_room_seg_store_returns_empty(self):
        flow = _make_options_flow(None)
        data = flow._config_entry.runtime_data
        assert flow._build_zone_index_options(data, {}) == []


class TestResolveCurrentZoneNameEphemeral:
    def test_known_room_returns_its_name(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_name("room_1", data, {}) == "Kitchen"

    def test_unknown_room_id_falls_back_to_generic_label(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore

        rss = RoomSegStore()
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_name("room_99", data, {}) == "Zone room_99"


class TestResolveCurrentZoneHiddenEphemeral:
    def test_hidden_room_returns_true(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", hidden=True)}
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_hidden("room_1", data, {}) is True

    def test_visible_room_returns_false(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", hidden=False)}
        flow = _make_options_flow(rss)
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_hidden("room_1", data, {}) is False


class TestSaveZoneEditsAtomicEphemeral:
    def test_rename_edit_applies_to_room_seg_store(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="", confirmed=False)}
        flow = _make_options_flow(rss)
        flow._pending_zone_edits = {"room_1": {"display_name": "Kitchen"}}
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        flow._save_zone_edits_atomic()

        assert rss.rooms["room_1"].name == "Kitchen"
        assert rss.rooms["room_1"].confirmed is True
        flow.hass.async_create_task.assert_called_once()

    def test_hide_edit_does_not_crash_on_string_room_id(self):
        """Regression check: SegRoom.id is a string ('room_1') -- the old
        ZoneStore code path did int(zone_id_str), which would raise
        ValueError on a string id like this one."""
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        flow._pending_zone_edits = {"room_1": {"hidden": True}}
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        flow._save_zone_edits_atomic()  # must not raise

        assert rss.rooms["room_1"].hidden is True

    def test_pending_edits_cleared_after_save(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen")}
        flow = _make_options_flow(rss)
        flow._pending_zone_edits = {"room_1": {"display_name": "Office"}}
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        flow._save_zone_edits_atomic()

        assert flow._pending_zone_edits == {}


class TestAsyncStepZonesEphemeral:
    @pytest.mark.asyncio
    async def test_no_unconfirmed_rooms_skips_form(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_zones(None)

        flow.async_create_entry.assert_called_once()
        assert "show_form" not in str(result)

    @pytest.mark.asyncio
    async def test_shows_form_with_one_field_per_unconfirmed_room(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=False),
            "room_2": SegRoom(id="room_2", name="Bedroom", confirmed=False),
        }
        flow = _make_options_flow(rss)
        flow.async_show_form = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_zones(None)

        flow.async_show_form.assert_called_once()
        schema_keys = {str(k) for k in result["data_schema"].schema.keys()}
        assert any("room_1" in k for k in schema_keys)
        assert any("room_2" in k for k in schema_keys)

    @pytest.mark.asyncio
    async def test_submitting_names_renames_rooms(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="", confirmed=False)}
        flow = _make_options_flow(rss)
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        await flow.async_step_zones({"zone_room_1": "Kitchen"})

        assert rss.rooms["room_1"].name == "Kitchen"
        assert rss.rooms["room_1"].confirmed is True

    @pytest.mark.asyncio
    async def test_no_room_seg_store_closes_silently(self):
        flow = _make_options_flow(None)
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_zones(None)

        flow.async_create_entry.assert_called_once()


# ── v3.5.0 bug-hunt — the reauth flow (async_step_reauth/reauth_confirm) ──────
#
# cloud_coordinator.py's _async_setup()/_async_update_data() already raise
# ConfigEntryAuthFailed on a bad cloud login (pre-dates v3.5.0), which calls
# config_entry.async_start_reauth() -> this flow's async_step_reauth. That
# method didn't exist anywhere in this file until v3.5.0's cloud_stale split
# (repairs.py) started explicitly relying on it instead of a custom Repair
# Issue for the auth-failure case — this was the missing other half of that
# fix, found by reviewing config_flow.py rather than assuming it already
# existed.

def _make_reauth_flow(reauth_entry_data=None):
    """Bare-construct RoombaPlusConfigFlow with just enough wired up for the
    reauth steps: hass, minimal FlowHandler attributes (context/flow_id/
    handler — needed by HA's own async_abort/async_show_form, which
    object.__new__ bypasses the normal __init__ for), and _get_reauth_entry()
    short-circuited directly rather than threading through HA's real
    context/source machinery."""
    from custom_components.roomba_plus.config_flow import RoombaPlusConfigFlow
    from custom_components.roomba_plus.const import CONF_BLID

    flow = object.__new__(RoombaPlusConfigFlow)
    flow.hass = MagicMock()
    flow.context = {}
    flow.flow_id = "test_flow_id"
    flow.handler = "roomba_plus"
    reauth_entry = MagicMock()
    reauth_entry.data = reauth_entry_data or {CONF_BLID: "31B8091051311850"}
    flow._get_reauth_entry = MagicMock(return_value=reauth_entry)
    return flow, reauth_entry


class TestReauthEntryPoint:
    @pytest.mark.asyncio
    async def test_reauth_routes_straight_to_confirm_form(self):
        """async_step_reauth is HA's entry point; it must not show its own
        form — it routes straight to reauth_confirm."""
        from custom_components.roomba_plus.const import CONF_BLID
        flow, _entry = _make_reauth_flow()
        result = await flow.async_step_reauth({CONF_BLID: "31B8091051311850"})
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"


class TestReauthConfirmForm:
    @pytest.mark.asyncio
    async def test_initial_call_shows_form_prefilled_with_current_username(self):
        from custom_components.roomba_plus.const import CONF_BLID, CONF_IROBOT_USERNAME
        flow, _entry = _make_reauth_flow(reauth_entry_data={
            CONF_BLID: "31B8091051311850",
            CONF_IROBOT_USERNAME: "old@example.com",
        })
        result = await flow.async_step_reauth_confirm()
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_valid_credentials_update_entry_and_abort(self):
        # PATCHES HA'S OWN METHOD RATHER THAN SATISFYING IT. In HA
        # 2026.x `async_update_reload_and_abort` looks the entry up in
        # the real registry, so a MagicMock entry raises `UnknownEntry`
        # -- and building a genuine one would mean standing up a
        # full instance to test one branch of our own code.
        #
        # What this test is actually about is that the reauth step calls
        # UPDATE-and-reload rather than create, and with which arguments.
        # Patching the method records exactly that.
        """Successful reauth must update the EXISTING entry (never create a
        new one) and reload it — the actual point of using
        async_update_reload_and_abort() over async_create_entry()."""
        flow, reauth_entry = _make_reauth_flow(reauth_entry_data={
            CONF_BLID: "31B8091051311850",
            CONF_IROBOT_USERNAME: "old@example.com",
            CONF_IROBOT_PASSWORD: "old_password",
        })
        mock_api = MagicMock()
        mock_api.authenticate = AsyncMock()
        with patch.object(
            type(flow), "async_update_reload_and_abort",
            return_value={"type": "abort", "reason": "reauth_successful"},
        ) as mock_update, patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi",
            return_value=mock_api,
        ), patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=MagicMock(),
        ):
            result = await flow.async_step_reauth_confirm({
                CONF_IROBOT_USERNAME: "new@example.com",
                CONF_IROBOT_PASSWORD: "new_password",
            })

        mock_api.authenticate.assert_awaited_once()
        assert result["type"] == "abort"
        # WHAT THIS TEST IS ABOUT: the reauth step must UPDATE the
        # existing entry rather than create a new one, and pass the new
        # credentials through.
        #
        # It used to assert on `async_update_entry` and
        # `async_schedule_reload` -- HA's internals, one layer below the
        # call we make. That worked until 2026.x made
        # `async_update_reload_and_abort` look the entry up in the real
        # registry, at which point a MagicMock entry raised
        # `UnknownEntry` before reaching either.
        #
        # Asserting on our own call instead is both more honest and
        # stable across HA versions: whether it delegates to
        # `async_update_entry` is not our contract.
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs["data"][CONF_IROBOT_USERNAME] == (
            "new@example.com"
        )
        assert call_kwargs["data"][CONF_IROBOT_PASSWORD] == (
            "new_password"
        )

    @pytest.mark.asyncio
    async def test_invalid_credentials_show_error_not_abort(self):
        from custom_components.roomba_plus.cloud_api import AuthenticationError
        flow, _entry = _make_reauth_flow()
        mock_api = MagicMock()
        mock_api.authenticate = AsyncMock(side_effect=AuthenticationError("bad creds"))
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi",
            return_value=mock_api,
        ), patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=MagicMock(),
        ):
            result = await flow.async_step_reauth_confirm({
                "irobot_username": "wrong@example.com",
                "irobot_password": "wrong_password",
            })
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_cloud_credentials"}
        flow.hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_cloud_unreachable_shows_cannot_connect_error(self):
        from custom_components.roomba_plus.cloud_api import CloudApiError
        flow, _entry = _make_reauth_flow()
        mock_api = MagicMock()
        mock_api.authenticate = AsyncMock(side_effect=CloudApiError("timeout"))
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi",
            return_value=mock_api,
        ), patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            return_value=MagicMock(),
        ):
            result = await flow.async_step_reauth_confirm({
                "irobot_username": "user@example.com",
                "irobot_password": "password",
            })
        assert result["errors"] == {"base": "cannot_connect"}
        flow.hass.config_entries.async_update_entry.assert_not_called()


class TestAsyncStepSettingsBranchesByConnectionType:
    """NEW (this session) -- Prime (CLOUD_ONLY) entries used to land on
    the SAME settings form as Classic, showing fields that mean
    nothing for Prime at all (map size/scale, correlation entities --
    all Classic-only rendering concepts). Now branches: Prime gets its
    own minimal form."""

    @pytest.mark.asyncio
    async def test_prime_shows_only_the_calendar_toggle(self):
        from custom_components.roomba_plus.models import ConnectionType

        flow = _make_options_flow()
        flow._config_entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        flow.async_show_form = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_settings(None)

        schema_keys = {str(k) for k in result["data_schema"].schema.keys()}
        assert any("enable_schedule_calendar" in k for k in schema_keys)
        assert not any("map_size_px" in k for k in schema_keys)
        assert not any("correlation_entities" in k for k in schema_keys)

    @pytest.mark.asyncio
    async def test_classic_shows_existing_fields_plus_the_calendar_toggle(self):
        flow = _make_options_flow()
        flow.async_show_form = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_settings(None)

        schema_keys = {str(k) for k in result["data_schema"].schema.keys()}
        assert any("map_size_px" in k for k in schema_keys)
        assert any("enable_schedule_calendar" in k for k in schema_keys)

    @pytest.mark.asyncio
    async def test_prime_settings_save_writes_calendar_option(self):
        from custom_components.roomba_plus.models import ConnectionType

        flow = _make_options_flow()
        flow._config_entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        await flow.async_step_settings({"enable_schedule_calendar": False})

        flow.async_create_entry.assert_called_once()
        saved = flow.async_create_entry.call_args.kwargs["data"]
        assert saved["enable_schedule_calendar"] is False


class TestValidateInput:
    """`validate_input` is the gatekeeper for manual setup, and was
    entirely untested.

    Its error paths matter more than its happy path: a failure here
    stops a user at the very first screen, before they have any entity
    to inspect or log to read. Both of the ways it can fail --
    unreachable device and a robot that never reports its state --
    surface to the user as the same single error, so the code has to
    map them correctly or the message is a lie."""

    def _data(self):
        from homeassistant.const import CONF_DELAY, CONF_HOST, CONF_PASSWORD

        from custom_components.roomba_plus.const import CONF_BLID

        return {
            CONF_HOST: "192.168.1.50",
            CONF_BLID: "TESTBLID",
            CONF_PASSWORD: "secret",
            CONF_DELAY: 1,
        }

    @pytest.mark.asyncio
    async def test_a_successful_connection_returns_name_and_session(self):
        from custom_components.roomba_plus import config_flow
        from homeassistant.const import CONF_HOST

        from custom_components.roomba_plus.const import ROOMBA_SESSION

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())

        with patch.object(config_flow, "async_connect_or_timeout",
                          AsyncMock(return_value={ROOMBA_SESSION: "sess", "name": "Rosie"})), \
             patch.object(config_flow, "async_disconnect_or_timeout", AsyncMock()):
            result = await config_flow.validate_input(hass, self._data())

        assert result["name"] == "Rosie"
        assert result[CONF_HOST] == "192.168.1.50"

    @pytest.mark.asyncio
    async def test_an_unreachable_robot_propagates_cannot_connect(self):
        """The user typed a wrong address, or the robot is asleep. This
        must reach the flow as CannotConnect so it can show the
        'cannot connect' message rather than a traceback."""
        from custom_components.roomba_plus import config_flow
        # From the PACKAGE, not from .__init__ -- importing the latter
        # explicitly creates a second module object, so its CannotConnect
        # is a different class than the one config_flow catches.
        from custom_components.roomba_plus import CannotConnect

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())

        with patch.object(config_flow, "async_connect_or_timeout",
                          AsyncMock(side_effect=CannotConnect)), \
             pytest.raises(CannotConnect):
            await config_flow.validate_input(hass, self._data())

    @pytest.mark.asyncio
    async def test_it_always_disconnects_after_a_successful_probe(self):
        """The probe opens a real connection. Leaving it open would hold
        the robot's single local slot, and the robot only accepts one --
        the next thing the user does would then fail for a reason with
        no visible connection to this step."""
        from custom_components.roomba_plus import config_flow
        from custom_components.roomba_plus.const import ROOMBA_SESSION

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())
        disconnect = AsyncMock()

        with patch.object(config_flow, "async_connect_or_timeout",
                          AsyncMock(return_value={ROOMBA_SESSION: "s", "name": "R"})), \
             patch.object(config_flow, "async_disconnect_or_timeout", disconnect):
            await config_flow.validate_input(hass, self._data())

        disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_password_is_not_returned(self):
        """Whatever this returns ends up in the config entry and in
        diagnostics downloads. The password is already stored
        separately; echoing it here would duplicate a secret into a
        second place for no benefit."""
        from custom_components.roomba_plus import config_flow
        from homeassistant.const import CONF_PASSWORD

        from custom_components.roomba_plus.const import ROOMBA_SESSION

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())

        with patch.object(config_flow, "async_connect_or_timeout",
                          AsyncMock(return_value={ROOMBA_SESSION: "s", "name": "R"})), \
             patch.object(config_flow, "async_disconnect_or_timeout", AsyncMock()):
            result = await config_flow.validate_input(hass, self._data())

        assert CONF_PASSWORD not in result


class TestCloudCredentialsStep:
    """The optional iRobot cloud login. Four distinct failure modes,
    each with its own message, none previously tested.

    This is where the distinctions earn their keep: "wrong password",
    "you have been rate-limited", "your system clock or certificates
    are off" and "the network is down" call for four different actions
    from the user. Collapsing any of them into a generic "cannot
    connect" sends someone re-typing a password that was correct.

    Also worth guarding: this step is OPTIONAL. Leaving it blank has to
    create the entry, because cloud access only adds enrichment -- a
    robot works without it, and a user who cannot log in must still end
    up with a working integration."""

    def _flow(self):
        from custom_components.roomba_plus.config_flow import RoombaPlusConfigFlow

        flow = object.__new__(RoombaPlusConfigFlow)
        flow.hass = MagicMock()
        flow.hass.config.country = "DE"
        flow.name = "Rosie"
        flow._pending_config = {"blid": "B"}
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        return flow

    async def _submit(self, flow, exc=None):
        from custom_components.roomba_plus import config_flow
        from custom_components.roomba_plus.const import (
            CONF_IROBOT_PASSWORD, CONF_IROBOT_USERNAME,
        )

        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=exc)
        with patch.object(config_flow, "IrobotCloudApi", return_value=api), \
             patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", MagicMock()):
            return await flow.async_step_cloud_credentials({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "pw",
            })

    @pytest.mark.asyncio
    async def test_a_wrong_password_says_so_specifically(self):
        from custom_components.roomba_plus.cloud_api import AuthenticationError

        flow = self._flow()

        await self._submit(flow, AuthenticationError())

        assert flow.async_show_form.call_args.kwargs["errors"] == {
            "base": "invalid_cloud_credentials"
        }

    @pytest.mark.asyncio
    async def test_rate_limiting_is_not_reported_as_bad_credentials(self):
        """THE distinction that matters most here: iRobot's auth has
        been rate-limiting aggressively since late 2024. Telling that
        user their password is wrong sends them changing a working
        password, which makes things worse."""
        from custom_components.roomba_plus.cloud_api import RateLimitedError

        flow = self._flow()

        await self._submit(flow, RateLimitedError())

        assert flow.async_show_form.call_args.kwargs["errors"] == {
            "base": "cloud_rate_limited"
        }

    @pytest.mark.asyncio
    async def test_a_certificate_problem_gets_its_own_message(self):
        """Local trust-store problems look like auth failures and are
        not -- the fix is on the user's machine, not in their account."""
        from custom_components.roomba_plus.cloud_api import SSLCertificateError

        flow = self._flow()

        await self._submit(flow, SSLCertificateError())

        assert flow.async_show_form.call_args.kwargs["errors"] == {
            "base": "cloud_ssl_certificate_error"
        }

    @pytest.mark.asyncio
    async def test_any_other_api_failure_falls_back_to_cannot_connect(self):
        from custom_components.roomba_plus.cloud_api import CloudApiError

        flow = self._flow()

        await self._submit(flow, CloudApiError())

        assert flow.async_show_form.call_args.kwargs["errors"] == {"base": "cannot_connect"}

    @pytest.mark.asyncio
    async def test_valid_credentials_create_the_entry(self):
        flow = self._flow()

        await self._submit(flow, exc=None)

        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_leaving_it_blank_still_creates_the_entry(self):
        """Cloud access is enrichment, not a requirement. A user who
        skips this must still end up with a working robot."""
        from custom_components.roomba_plus.const import (
            CONF_IROBOT_PASSWORD, CONF_IROBOT_USERNAME,
        )

        flow = self._flow()

        await flow.async_step_cloud_credentials({
            CONF_IROBOT_USERNAME: "", CONF_IROBOT_PASSWORD: "",
        })

        flow.async_create_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_failed_login_does_not_store_the_credentials(self):
        """Storing credentials that are known not to work would make
        every later cloud call fail for a reason nobody can see."""
        from custom_components.roomba_plus.cloud_api import AuthenticationError
        from custom_components.roomba_plus.const import CONF_IROBOT_USERNAME

        flow = self._flow()

        await self._submit(flow, AuthenticationError())

        flow.async_create_entry.assert_not_called()
        assert CONF_IROBOT_USERNAME not in flow._pending_config


class TestLinkStep:
    """The pairing step: the user holds HOME until the robot beeps, and
    the integration reads the password straight off the device.

    Its two fallbacks to manual entry are the interesting part. This is
    the step people actually get stuck on -- the timing window is short,
    older firmware behaves differently, and some networks block the
    port entirely. Falling back cleanly is the difference between "type
    your password here instead" and a dead end."""

    def _flow(self, *, name=None):
        from custom_components.roomba_plus.config_flow import RoombaPlusConfigFlow

        flow = object.__new__(RoombaPlusConfigFlow)
        flow.hass = MagicMock()
        flow.host = "192.168.1.50"
        flow.blid = "TESTBLID"
        flow.name = name
        flow._pending_config = {}
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        flow.async_step_link_manual = AsyncMock(return_value={"type": "manual"})
        flow.async_step_cloud_credentials = AsyncMock(return_value={"type": "cloud"})
        return flow

    async def _run(self, flow, *, password="pw", raises=None, validate=None):
        from custom_components.roomba_plus import config_flow

        flow.hass.async_add_executor_job = AsyncMock(
            side_effect=raises, return_value=password
        )
        with patch.object(config_flow, "RoombaPassword", MagicMock()), \
             patch.object(config_flow, "validate_input",
                          AsyncMock(**(validate or {"return_value": {"name": "Rosie"}}))):
            return await flow.async_step_link({})

    @pytest.mark.asyncio
    async def test_an_unreachable_port_falls_back_to_manual_entry(self):
        """Some networks block the password port outright. A dead end
        here would strand the user with no way forward at all."""
        flow = self._flow()

        result = await self._run(flow, raises=OSError("connection refused"))

        assert result == {"type": "manual"}

    @pytest.mark.asyncio
    async def test_an_empty_password_also_falls_back(self):
        """The robot answers but returns nothing -- typically the HOME
        button was not held long enough, or was held too long. Same
        recovery, different cause."""
        flow = self._flow()

        result = await self._run(flow, password="")

        assert result == {"type": "manual"}

    @pytest.mark.asyncio
    async def test_a_successful_pairing_moves_on_to_the_cloud_step(self):
        flow = self._flow(name="Rosie")

        result = await self._run(flow)

        assert result == {"type": "cloud"}

    @pytest.mark.asyncio
    async def test_the_password_is_carried_into_the_pending_config(self):
        flow = self._flow(name="Rosie")

        await self._run(flow, password="secret-from-robot")

        assert flow._pending_config["password"] == "secret-from-robot"

    @pytest.mark.asyncio
    async def test_a_robot_that_never_reports_its_name_aborts_clearly(self):
        """Distinct from the fallbacks above: the password worked, so
        manual entry would not help. Aborting with a reason beats
        looping the user back to a step that cannot succeed."""
        # From the PACKAGE, not from .__init__ -- importing the latter
        # explicitly creates a second module object, so its CannotConnect
        # is a different class than the one config_flow catches.
        from custom_components.roomba_plus import CannotConnect

        flow = self._flow(name=None)

        result = await self._run(flow, validate={"side_effect": CannotConnect})

        assert result == {"type": "abort"}
        assert flow.async_abort.call_args.kwargs["reason"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_a_known_name_skips_the_extra_probe(self):
        """Discovery already supplies the name. Probing again would open
        a second connection to a robot that only has one slot."""
        from custom_components.roomba_plus import config_flow

        flow = self._flow(name="Already Known")
        flow.hass.async_add_executor_job = AsyncMock(return_value="pw")

        with patch.object(config_flow, "RoombaPassword", MagicMock()), \
             patch.object(config_flow, "validate_input", AsyncMock()) as validate:
            await flow.async_step_link({})

        validate.assert_not_awaited()


class TestBlockingSensorPickerOffersHelperDomains:
    """The picker and the manager must agree on which domains count.

    @chairstacker could not select `input_boolean.vacation_mode` as a
    blocker even though the manager would have handled it: the check
    reads `state.state` and never cared about the domain, but the
    picker was pinned to `binary_sensor`. A picker that offers less
    than the manager accepts is an artificial limit.
    """

    @pytest.mark.asyncio
    async def test_the_picker_accepts_more_than_binary_sensor(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.const import CONF_BLOCKING_SENSORS

        flow = _make_options_flow()
        flow.async_show_form = MagicMock(side_effect=lambda **kw: kw)

        result = await flow.async_step_blocking_sensors(None)

        selector_config = next(
            value.config
            for key, value in result["data_schema"].schema.items()
            if CONF_BLOCKING_SENSORS in str(key)
        )
        domains = selector_config["domain"]

        assert "binary_sensor" in domains
        assert "input_boolean" in domains, (
            "a house-state toggle is the case this was widened for"
        )


class TestRegionSensorsOptionIsOfferedOnBothTiers:
    """The per-region entities are opt-in, and the opt-in has to exist
    on both generations.

    Giving Prime users a switch and Classic users none -- for identical
    data, from the same store -- is the split this project keeps having
    to unpick later. Checked by asking both schemas, because a source
    check passes whether or not the field is in the form.
    """

    @pytest.mark.asyncio
    async def test_both_settings_forms_offer_it(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.const import CONF_REGION_SENSORS
        from custom_components.roomba_plus.models import ConnectionType

        for connection_type in (ConnectionType.CLOUD_ONLY, ConnectionType.LOCAL_PUSH):
            flow = _make_options_flow()
            flow._config_entry.runtime_data.connection_type = connection_type
            flow.async_show_form = MagicMock(side_effect=lambda **kw: kw)

            result = await flow.async_step_settings(None)
            keys = {str(k) for k in result["data_schema"].schema}

            assert any(CONF_REGION_SENSORS in k for k in keys), (
                f"{connection_type} settings form does not offer "
                f"{CONF_REGION_SENSORS}"
            )

    def test_it_defaults_to_off(self):
        """@dduff617's four maps would otherwise mean dozens of
        entities nobody asked for."""
        from custom_components.roomba_plus.const import DEFAULT_REGION_SENSORS

        assert DEFAULT_REGION_SENSORS is False
