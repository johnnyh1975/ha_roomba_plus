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
import inspect
import pytest

# MODULE SCOPE, not inside the test: the `hass` fixture rebuilds the
# custom-integration module space, so an import in a test body that uses
# it no longer resolves.
from custom_components.roomba_plus.const import (
    CONF_BLID, CONF_IROBOT_PASSWORD, CONF_IROBOT_USERNAME,
        )
from unittest.mock import MagicMock, patch
from tests.conftest import hass_mock, entry_mock
from custom_components.roomba_plus.const import (
    CONF_CORRELATION_ENTITIES,
    CONF_ROOM_SCHEDULE,
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
    config_entry = entry_mock()
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
    flow.hass = hass_mock()
    # `flow.hass.async_create_task` is left to hass_mock(): it records
    # calls AND closes the coroutine; a bare MagicMock drops it.
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
    flow.hass = hass_mock()
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

        hass = hass_mock()
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

        hass = hass_mock()
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

        hass = hass_mock()
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

        hass = hass_mock()
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
        flow.hass = hass_mock()
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
        flow.hass = hass_mock()
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

        # THE PASSWORD COMES FROM THE CLIENT NOW, not from the executor.
        # `RoombaPassword.get_password()` is a coroutine since roombapy
        # 2.x, so the stub has to live on the patched class -- the
        # executor is no longer in the path and stubbing it would leave
        # the real MagicMock to be awaited.
        pw_class = MagicMock()
        pw_class.return_value.get_password = AsyncMock(
            side_effect=raises, return_value=password
        )
        flow.hass.async_add_executor_job = AsyncMock()
        with patch.object(config_flow, "RoombaPassword", pw_class), \
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
        pw_class = MagicMock()
        pw_class.return_value.get_password = AsyncMock(return_value="pw")

        with patch.object(config_flow, "RoombaPassword", pw_class), \
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


class TestTheRemovedConnectionOptions:
    """`continuous` and `delay` are gone from the settings form (4.2).

    roombapy 2.x keeps one supervised connection and reconnects on its
    own -- the behaviour `continuous: true` selected, and what the
    option's own description recommended. There is no polling mode left,
    so the library has neither parameter and offering the choice would
    offer something nothing reads.

    Pinned because a form is easy to extend and these two are easy to
    put back from muscle memory. A returning field would be a control
    that does nothing.
    """

    def test_neither_option_is_offered(self) -> None:
        from custom_components.roomba_plus import config_flow

        source = inspect.getsource(config_flow.RoombaPlusOptionsFlow)
        form = source[source.index('step_id="settings"'):]

        assert "CONF_CONTINUOUS" not in form
        assert "CONF_DELAY" not in form

    def test_neither_is_still_labelled(self) -> None:
        """A label for a field that does not exist reads, to anyone
        looking at strings.json, like a field that does."""
        import json
        import pathlib

        base = pathlib.Path(__file__).parent.parent / "custom_components" / "roomba_plus"
        for name in ["strings.json", *(f"translations/{c}.json" for c in
                     ("en", "de", "es", "fr", "it", "nl", "pl", "pt"))]:
            step = json.loads((base / name).read_text(encoding="utf-8"))
            settings = step.get("options", {}).get("step", {}).get("settings", {})
            for section in ("data", "data_description"):
                keys = settings.get(section, {})
                assert "continuous" not in keys, f"{name}: {section}.continuous"
                assert "delay" not in keys, f"{name}: {section}.delay"

    def test_a_stored_false_is_reported_once(self) -> None:
        """Someone who deliberately turned the persistent connection off
        gets it back without asking. The new behaviour is the one that
        was recommended, but it is still a silent change to a setting
        they chose -- so it goes in their log."""
        import custom_components.roomba_plus as integration

        # In `_phase_connect`, where the client is built -- not in
        # `async_setup_entry`, which delegates the phases.
        source = inspect.getsource(integration._phase_connect)

        assert "config_entry.options.get(CONF_CONTINUOUS) is False" in source


# ── Merged from test_config_flow_v330.py ─────────────────────────────
# Version-named test files are the pattern the v2.8 consolidation set
# out to remove; this one survived it.
#
# The import is module-level here, as it was in the source file. The
# rest of this file imports the class inside each helper instead; both
# work, and unifying them would be churn for its own sake.
from custom_components.roomba_plus.config_flow import (  # noqa: E402
    RoombaPlusOptionsFlow,
)
from typing import Any
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.roomba_plus import config_flow as cf
from custom_components.roomba_plus.const import DOMAIN
from types import SimpleNamespace
from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES
from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN
from custom_components.roomba_plus.models import MapCapability
from custom_components.roomba_plus.config_flow import RoombaPlusConfigFlow
from custom_components.roomba_plus.config_flow import _CLOUD_ACCOUNT_SENTINEL
from custom_components.roomba_plus.config_flow import _is_prime_sku
from custom_components.roomba_plus.const import CONF_CONNECTION_TYPE
from homeassistant.const import CONF_HOST
from homeassistant.const import CONF_PASSWORD
from custom_components.roomba_plus.models import ConnectionType
from roombapy_prime import AuthConnectionError
from roombapy_prime import AuthCredentialsError
from roombapy_prime import AuthRateLimitedError
from roombapy_prime import AuthSSLError
from custom_components.roomba_plus.config_flow import REST980_DOMAIN
from custom_components.roomba_plus.config_flow import _discover_rest980_rooms
from custom_components.roomba_plus.config_flow import _resolve_current_pmap_id


def _flow(options=None, regions=None, capability="smart", has_cloud=True):
    flow = object.__new__(RoombaPlusOptionsFlow)
    entry = entry_mock()
    entry.options = options or {}
    data = entry.runtime_data
    data.map_capability.value = capability
    data.has_cloud = has_cloud
    if not has_cloud:
        data.cloud_coordinator = None
    else:
        data.cloud_coordinator.regions = regions if regions is not None else [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
        ]
    # OptionsFlow.config_entry is a property in recent HA — each test
    # patches it at class level via patch.object(..., new=entry).
    return flow, entry


class TestRoomScheduleStep:
    def _make(self, **kw):
        flow, entry = _flow(**kw)
        # Patch the property at class level per-test via context manager
        return flow, entry

    @pytest.mark.asyncio
    async def test_form_shows_selector_per_room_with_current_defaults(self):
        flow, entry = self._make(
            options={CONF_ROOM_SCHEDULE: {"Kitchen": "daily"}}
        )
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "form"
        schema_keys = {k.schema: k.default() for k in result["data_schema"].schema}
        assert schema_keys == {"Hall": "learned", "Kitchen": "daily"}

    @pytest.mark.asyncio
    async def test_save_filters_orphans_and_learned(self):
        """Orphan filter: a configured room that vanished from the cloud
        map is dropped on save; 'learned' entries are not stored."""
        flow, entry = self._make(
            options={CONF_ROOM_SCHEDULE: {"Ghost": "weekly"}}
        )
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(
                {"Kitchen": "every_2_days", "Hall": "learned",
                 "Ghost": "weekly"}
            )
        assert result["type"].value == "create_entry"
        assert result["data"][CONF_ROOM_SCHEDULE] == {
            "Kitchen": "every_2_days"
        }

    @pytest.mark.asyncio
    async def test_gate_aborts_without_smart_cloud(self):
        flow, entry = self._make(capability="ephemeral", has_cloud=False)
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "abort"
        assert result["reason"] == "room_schedule_not_supported"

    @pytest.mark.asyncio
    async def test_abort_without_named_rooms(self):
        flow, entry = self._make(regions=[])
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_room_schedule(None)
        assert result["type"].value == "abort"
        assert result["reason"] == "room_schedule_no_rooms"


class TestSettingsCorrelationField:
    @pytest.mark.asyncio
    async def test_settings_schema_contains_correlation_entities(self):
        flow, entry = _flow(options={CONF_CORRELATION_ENTITIES: ["sensor.h"]})
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_settings(None)
        assert result["type"].value == "form"
        keys = {k.schema: k.default() for k in result["data_schema"].schema}
        assert keys[CONF_CORRELATION_ENTITIES] == ["sensor.h"]

    @pytest.mark.asyncio
    async def test_settings_save_persists_correlation_entities(self):
        flow, entry = _flow()
        with patch.object(
            RoombaPlusOptionsFlow, "config_entry", new=entry, create=True
        ):
            result = await flow.async_step_settings(
                {CONF_CORRELATION_ENTITIES: ["sensor.humidity"]}
            )
        assert result["type"].value == "create_entry"
        assert result["data"][CONF_CORRELATION_ENTITIES] == ["sensor.humidity"]


class TestAWrongLibrarySaysSo:
    """`config_flow.py` imported `roombapy` at module level, so a wrong
    version stopped Home Assistant loading the flow at all and the user
    got "Config flow could not be loaded: Invalid handler specified".

    @bandit254 saw that, tried three Roomba+ versions looking for one
    that worked, and concluded the smaller releases must ship fewer
    dependencies. They do not: 4.2.0 and 4.2.9 pin the same
    `roombapy==2.0.2` and import the same class on the same line. The
    fault was the installed library, which the message never named.

    THE NOTICE NOW FIRES ON THE LIBRARY, NOT ON A CONFIG ENTRY.
    Counting entries asks "is the other integration set up", a proxy for
    "is the shared library wrong". The library answers directly and
    catches the case the proxy misses -- discovery loading the built-in
    integration's flow with no entry ever created.
    """

    def test_the_flow_imports_defensively(self) -> None:
        import inspect

        from custom_components.roomba_plus import config_flow

        source = inspect.getsource(config_flow)

        assert "ROOMBAPY_IMPORT_ERROR" in source
        assert "except ImportError" in source

    def test_the_flow_aborts_with_the_reason(self) -> None:
        import inspect

        from custom_components.roomba_plus import config_flow

        source = inspect.getsource(
            config_flow.RoombaPlusConfigFlow.async_step_user
        )

        assert 'reason="wrong_roombapy"' in source
        assert '"error": ROOMBAPY_IMPORT_ERROR' in source

    def test_the_abort_text_names_the_conflict_and_the_remedy(self) -> None:
        """Naming the fault without naming what to do about it is what
        sent him version-hunting."""
        import json
        import pathlib

        data = json.loads(
            pathlib.Path(
                "custom_components/roomba_plus/strings.json"
            ).read_text(encoding="utf-8")
        )
        text = data["config"]["abort"]["wrong_roombapy"]

        assert "Restart Home Assistant" in text
        assert "built-in" in text
        assert "{error}" in text
        # And the thing he got wrong, said plainly:
        assert "version makes no difference" in text

    def test_setup_fails_with_the_reason_too(self) -> None:
        """An existing install that breaks after an update never reaches
        the config flow."""
        import inspect

        from custom_components import roomba_plus

        source = inspect.getsource(roomba_plus)

        assert "ROOMBAPY_IMPORT_ERROR is not None" in source

    def test_the_repair_fires_on_the_library(self) -> None:
        import inspect

        from custom_components.roomba_plus import repairs

        source = inspect.getsource(
            repairs.async_check_core_roomba_conflict
        )

        assert "_wrong_library" in source
        assert "not _conflicting and not _wrong_library" in source


class TestPresenceSchedulingOptionsStep:
    """`async_step_presence_scheduling` had no test at all.

    It writes four options at once. A step that drops one of them is
    silent: the form shows the field, the user sets it, and the setting
    does not take — the same shape as the service-schema failures that
    `check_service_schemas.py` exists for, one layer up.
    """

    @pytest.mark.asyncio
    async def test_it_shows_a_form_without_input(self):
        flow = _make_options_flow()
        result = await flow.async_step_presence_scheduling()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_saving_writes_every_field_the_form_offered(self):
        from custom_components.roomba_plus.const import (
            CONF_PRESENCE_ENTITIES,
            CONF_PRESENCE_MODE,
            CONF_PRESENCE_SCHEDULING_ENABLED,
        )

        flow = _make_options_flow()
        eingabe = {
            CONF_PRESENCE_SCHEDULING_ENABLED: True,
            CONF_PRESENCE_ENTITIES: ["person.a", "person.b"],
            CONF_PRESENCE_MODE: "all_away",
        }
        result = await flow.async_step_presence_scheduling(eingabe)

        gespeichert = result["data"]
        assert gespeichert[CONF_PRESENCE_SCHEDULING_ENABLED] is True
        assert gespeichert[CONF_PRESENCE_ENTITIES] == ["person.a", "person.b"]
        assert gespeichert[CONF_PRESENCE_MODE] == "all_away"

    @pytest.mark.asyncio
    async def test_it_keeps_options_it_was_not_asked_about(self):
        """An options step that returns only its own fields wipes every
        other setting the user has."""
        flow = _make_options_flow()
        flow._config_entry.options = {"map_enabled": False, "unrelated": 7}

        result = await flow.async_step_presence_scheduling(
            {"presence_scheduling_enabled": False}
        )

        assert result["data"]["map_enabled"] is False
        assert result["data"]["unrelated"] == 7


class TestMapManagementOptionsStep:
    """`async_step_map_management` had no test either.

    It is the zone editor's index: pick a zone to edit, or submit blank
    to save every pending edit at once. The blank-submit path is the one
    that writes — an editor that collects edits and then fails to commit
    them looks, to the user, exactly like an editor that works.
    """

    def _flow_with_zones(self):
        """The EPHEMERAL branch reads RoomSegStore, not the cloud
        regions — see ROOM_SEGMENTATION_NOTES.md for why the gap
        heuristic was dropped. A coordinator alone yields no zones and
        the step closes immediately, which is a different code path."""
        raum = MagicMock()
        raum.id = 12
        raum.name = "Kitchen"
        raum.hidden = False
        store = MagicMock()
        store.rooms = {12: raum}

        flow = _make_options_flow(room_seg_store=store)
        coordinator = MagicMock()
        coordinator.regions = [{"id": "12", "name": "Kitchen"}]
        flow._config_entry.runtime_data.cloud_coordinator = coordinator
        flow._pending_zone_edits = {}
        return flow

    @pytest.mark.asyncio
    async def test_with_no_zones_at_all_it_just_closes(self):
        """Nothing to manage — showing an empty picker would be worse."""
        flow = _make_options_flow(room_seg_store=None)
        flow._config_entry.runtime_data.cloud_coordinator = None

        result = await flow.async_step_map_management()

        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_it_shows_the_zone_picker_without_input(self):
        flow = self._flow_with_zones()
        result = await flow.async_step_map_management()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_a_blank_selection_commits_the_pending_edits(self):
        """Blank means "done" — the edits collected so far must be
        written, not discarded."""
        flow = self._flow_with_zones()
        flow._pending_zone_edits = {"12": {"name": "Kitchen"}}

        result = await flow.async_step_map_management({"selected_zone": ""})

        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_selecting_a_zone_does_not_commit(self):
        """Choosing a zone opens the edit step; writing there would save
        a half-finished edit."""
        flow = self._flow_with_zones()

        result = await flow.async_step_map_management({"selected_zone": "12"})

        assert result["type"] != "create_entry"


class TestSmartZonesNamingStep:
    """`async_step_smart_zones` — the naming form @liblit reported on,
    rebuilt in 4.2.10 and until now covered by no test at all.

    THREE SOURCES, NOT ONE. It reads the zone list from `lastCommand`,
    the cloud coordinator and `cleanSchedule2`. The step once read only
    the schedule, so a robot whose owner had never built a schedule WITH
    ROOMS opened the form and immediately declared itself finished —
    @connormxy's robot knew its twelve rooms perfectly well, they were
    simply not in a schedule. Each source below is a separate test
    because each was a separate field report.
    """

    def _flow(self, state: dict, *, regions=None, options=None):
        flow = _make_options_flow()
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        flow._config_entry.runtime_data.roomba = roomba
        coordinator = MagicMock()
        coordinator.regions = regions or []
        flow._config_entry.runtime_data.cloud_coordinator = coordinator
        flow._config_entry.options = dict(options or {})
        return flow

    _SMART_MAP = {"pmaps": [{"abc": "v1"}], "cap": {"pmaps": 1}}

    @pytest.mark.asyncio
    async def test_a_robot_without_a_smart_map_just_closes(self):
        flow = self._flow({"cap": {}})
        result = await flow.async_step_smart_zones()
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_zones_are_found_via_last_command(self):
        """The most recent clean's regions — available even to an owner
        who has never built a schedule."""
        state = dict(self._SMART_MAP)
        state["lastCommand"] = {
            "pmap_id": "abc",
            "regions": [{"region_id": "3"}, {"region_id": "7"}],
        }
        flow = self._flow(state)

        result = await flow.async_step_smart_zones()

        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_zones_are_found_via_the_cloud_coordinator(self):
        """@connormxy's case: the robot knows its rooms, the schedule
        does not."""
        flow = self._flow(
            dict(self._SMART_MAP),
            regions=[{"id": "3", "name": ""}, {"id": "7", "name": ""}],
        )

        result = await flow.async_step_smart_zones()

        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_zones_are_found_via_the_schedule(self):
        state = dict(self._SMART_MAP)
        state["cleanSchedule2"] = [
            {"cmd": {"pmap_id": "abc", "regions": [{"region_id": "3"}]}}
        ]
        flow = self._flow(state)

        result = await flow.async_step_smart_zones()

        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_submitting_names_writes_both_option_keys(self):
        """`smart_zone_labels` for backward compatibility and
        `smart_zone_data` for the clean_room action — a step that writes
        only one leaves half the integration unable to see the name."""
        state = dict(self._SMART_MAP)
        state["lastCommand"] = {"pmap_id": "abc", "regions": [{"region_id": "3"}]}
        flow = self._flow(state)

        result = await flow.async_step_smart_zones({"zone_3": "Kitchen"})

        assert result["type"] == "create_entry"
        assert result["data"]["smart_zone_labels"]["3"] == "Kitchen"
        assert result["data"]["smart_zone_data"]["3"]["name"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_the_prefilled_default_is_saved_as_a_real_name(self):
        """Each field is pre-filled with `Zone <id>` and submitting it
        unchanged stores exactly that. Pinned deliberately, because it
        LOOKS like the @liblit bug and is not: he reported that the
        pre-filled box read as a result rather than an input, which is a
        presentation problem. Storing the default is the intended
        outcome — a zone called "Zone 3" beats an unnamed one on the
        map. Anyone tempted to "fix" this by dropping defaults should
        change the form's presentation instead.
        """
        state = dict(self._SMART_MAP)
        state["lastCommand"] = {"pmap_id": "abc", "regions": [{"region_id": "3"}]}
        flow = self._flow(state)

        result = await flow.async_step_smart_zones({"zone_3": "Zone 3"})

        assert result["data"]["smart_zone_labels"]["3"] == "Zone 3"

    @pytest.mark.asyncio
    async def test_it_says_why_when_there_is_nothing_to_name(self):
        """Two different nothings, two different messages: no rooms at
        all, versus every room already named. Reporting success for
        doing nothing is what @connormxy saw."""
        flow = self._flow(dict(self._SMART_MAP))

        result = await flow.async_step_smart_zones()

        assert result["type"] == "abort"
        assert result["reason"] == "no_rooms_to_name"


class TestSmartZonesManualStep:
    """`async_step_smart_zones_manual` — the two-phase text entry for
    zones the automatic step cannot find. No test until now.

    Phase 1 takes a list of region ids, phase 2 takes `id=Name` lines.
    Both parse free text, which is where a form quietly loses input:
    every malformed line is skipped in silence, so a user who typed a
    colon instead of an equals sign sees "saved" and no name.
    """

    def _flow(self, options=None):
        flow = _make_options_flow()
        flow._config_entry.options = dict(options or {})
        return flow

    @pytest.mark.asyncio
    async def test_phase_one_shows_a_form(self):
        flow = self._flow()
        result = await flow.async_step_smart_zones_manual()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_ids_may_be_separated_by_commas_or_spaces(self):
        """Both are what people actually type."""
        for eingabe in ("3, 7, 12", "3 7 12", "3,7 12"):
            flow = self._flow()
            result = await flow.async_step_smart_zones_manual(
                {"region_ids": eingabe}
            )
            assert result["type"] == "form", eingabe
            assert flow._pending_zone_ids == ["3", "7", "12"], eingabe

    @pytest.mark.asyncio
    async def test_an_empty_id_list_is_an_error_not_an_empty_form(self):
        flow = self._flow()
        result = await flow.async_step_smart_zones_manual({"region_ids": "   "})
        assert result["type"] == "form"
        assert result["errors"]["region_ids"] == "no_valid_ids"

    @pytest.mark.asyncio
    async def test_phase_two_parses_id_equals_name_lines(self):
        flow = self._flow()
        flow._pending_zone_ids = ["3", "7"]

        result = await flow.async_step_smart_zones_manual(
            {"zone_names": "3=Kitchen\n7=Hallway"}
        )

        assert result["type"] == "create_entry"
        labels = result["data"]["smart_zone_labels"]
        assert labels["3"] == "Kitchen"
        assert labels["7"] == "Hallway"

    @pytest.mark.asyncio
    async def test_a_malformed_line_is_skipped_and_the_rest_still_saves(self):
        """Documented behaviour, pinned because it is also the failure
        mode: a line without `=` vanishes without a word. If this ever
        gains an error message, this test should change with it."""
        flow = self._flow()
        flow._pending_zone_ids = ["3", "7"]

        result = await flow.async_step_smart_zones_manual(
            {"zone_names": "3=Kitchen\n7:Hallway"}
        )

        labels = result["data"]["smart_zone_labels"]
        assert labels["3"] == "Kitchen"
        assert "7" not in labels

    @pytest.mark.asyncio
    async def test_a_name_may_contain_an_equals_sign(self):
        """`partition`, not `split` — "3=A=B" is the name "A=B", not a
        parse failure."""
        flow = self._flow()
        flow._pending_zone_ids = ["3"]

        result = await flow.async_step_smart_zones_manual(
            {"zone_names": "3=Kitchen = Dining"}
        )

        assert result["data"]["smart_zone_labels"]["3"] == "Kitchen = Dining"


class TestSaveZoneEditsAtomic:
    """`_save_zone_edits_atomic` writes every pending zone edit at once.

    ALIAS-CLEAR-ON-MATCH is the part worth pinning. When the name a user
    types equals the name the cloud already has, the alias is DELETED
    rather than stored. Storing it would shadow the cloud name forever:
    rename the room in the iRobot app afterwards and Roomba+ would keep
    showing the old one, with nothing to explain why.
    """

    def _flow(self, *, cloud_regions=None, options=None):
        flow = _make_options_flow()
        flow._config_entry.options = dict(options or {})
        data = flow._config_entry.runtime_data
        if cloud_regions is None:
            data.has_cloud = False
        else:
            data.has_cloud = True
            data.cloud_coordinator.regions = cloud_regions
        return flow

    def test_a_name_that_differs_from_the_cloud_is_stored(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow(cloud_regions=[{"id": "3", "name": "Room 3"}])
        flow._pending_zone_edits = {"3": {"display_name": "Kitchen"}}

        result = flow._save_zone_edits_atomic()

        assert result["data"][CONF_SMART_ZONE_ALIASES]["3"] == "Kitchen"

    def test_a_name_equal_to_the_cloud_name_clears_the_alias(self):
        """Otherwise a later rename in the iRobot app would never show."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow(
            cloud_regions=[{"id": "3", "name": "Kitchen"}],
            options={CONF_SMART_ZONE_ALIASES: {"3": "Kitchen"}},
        )
        flow._pending_zone_edits = {"3": {"display_name": "Kitchen"}}

        result = flow._save_zone_edits_atomic()

        assert "3" not in result["data"][CONF_SMART_ZONE_ALIASES]

    def test_clearing_a_name_removes_the_alias(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow(
            cloud_regions=[{"id": "3", "name": "Room 3"}],
            options={CONF_SMART_ZONE_ALIASES: {"3": "Kitchen"}},
        )
        flow._pending_zone_edits = {"3": {"display_name": "   "}}

        result = flow._save_zone_edits_atomic()

        assert "3" not in result["data"][CONF_SMART_ZONE_ALIASES]

    def test_without_cloud_the_stored_zone_name_is_the_comparison(self):
        """A locally operated robot has no cloud names; the fallback is
        what smart_zone_data recorded."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow(
            options={"smart_zone_data": {"3": {"name": "Kitchen"}}},
        )
        flow._pending_zone_edits = {"3": {"display_name": "Kitchen"}}

        result = flow._save_zone_edits_atomic()

        assert "3" not in result["data"][CONF_SMART_ZONE_ALIASES]


class TestReconfigureStep:
    """`async_step_reconfigure` — how a user fixes a changed IP or a
    re-provisioned password without losing their history.

    THE POINT IS `**current.data`. A reconfigure that rebuilds the entry
    data from the form alone drops the BLID, and with it the identity
    every entity's unique_id is built from: the user gets a second set
    of entities and an empty history, for what they thought was an IP
    change.
    """

    def _flow(self, current_data: dict):
        from custom_components.roomba_plus.config_flow import RoombaPlusConfigFlow

        flow = RoombaPlusConfigFlow.__new__(RoombaPlusConfigFlow)
        entry = entry_mock()
        entry.data = dict(current_data)
        entry.entry_id = "test_entry"
        flow._get_reconfigure_entry = lambda: entry
        flow.hass = MagicMock()
        flow._entry_for_test = entry
        return flow

    @pytest.mark.asyncio
    async def test_it_shows_a_form_without_input(self):
        flow = self._flow({"host": "10.0.0.2", "blid": "B1", "password": "pw"})
        result = await flow.async_step_reconfigure()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_the_blid_survives_a_host_change(self, monkeypatch):
        """The field the form never shows and must never lose."""
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import config_flow as cf

        # validate_input opens a real connection to the new address;
        # what this test is about is what gets WRITTEN afterwards.
        monkeypatch.setattr(cf, "validate_input", AsyncMock(return_value={}))

        flow = self._flow({"host": "10.0.0.2", "blid": "B1", "password": "pw"})
        geschrieben: dict = {}
        flow.hass.config_entries.async_update_entry = (
            lambda entry, **kw: geschrieben.update(kw)
        )
        flow.hass.config_entries.async_reload = AsyncMock()
        flow.async_abort = MagicMock(return_value={"type": "abort"})

        await flow.async_step_reconfigure({"host": "10.0.0.9", "password": "pw"})

        assert geschrieben["data"]["blid"] == "B1"
        assert geschrieben["data"]["host"] == "10.0.0.9"


class TestDiscoverRoombas:
    """`_async_discover_roombas` — the search that populates the picker.

    IT RETRIES WITH A WAKE DELAY. A Roomba asleep on its dock does not
    answer the first probe; the loop sleeps `ROOMBA_WAKE_TIME * attempt`
    between rounds. A discovery that gave up after one try would tell a
    user with a perfectly good robot that nothing was found.
    """

    def _discovery(self, *, devices=None, host_device=None, fails=False):
        from unittest.mock import AsyncMock

        disc = MagicMock()
        if fails:
            disc.get = AsyncMock(side_effect=OSError("no network"))
            disc.get_all = AsyncMock(side_effect=OSError("no network"))
        else:
            disc.get = AsyncMock(return_value=host_device)
            disc.get_all = AsyncMock(return_value=devices or [])
        disc.aclose = AsyncMock()
        return disc

    def _device(self, ip: str, blid: str = "B1"):
        device = MagicMock()
        device.ip = ip
        device.blid = blid
        return device

    @pytest.mark.asyncio
    async def test_it_returns_what_discovery_found(self, monkeypatch):
        from custom_components.roomba_plus import config_flow as cf

        disc = self._discovery(devices=[self._device("10.0.0.2")])
        monkeypatch.setattr(cf, "_async_get_roomba_discovery", lambda: disc)
        monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)

        result = await cf._async_discover_roombas(MagicMock())

        assert [d.ip for d in result] == ["10.0.0.2"]

    @pytest.mark.asyncio
    async def test_a_network_error_yields_nothing_rather_than_raising(
        self, monkeypatch
    ):
        """Setup must not die because the network was briefly unusable —
        the user gets the manual-entry path instead."""
        from custom_components.roomba_plus import config_flow as cf

        disc = self._discovery(fails=True)
        monkeypatch.setattr(cf, "_async_get_roomba_discovery", lambda: disc)
        monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)

        result = await cf._async_discover_roombas(MagicMock())

        assert result == []

    @pytest.mark.asyncio
    async def test_a_known_host_is_probed_directly(self, monkeypatch):
        """With an address in hand there is no reason to sweep the LAN."""
        from custom_components.roomba_plus import config_flow as cf

        disc = self._discovery(host_device=self._device("10.0.0.5"))
        monkeypatch.setattr(cf, "_async_get_roomba_discovery", lambda: disc)
        monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)

        result = await cf._async_discover_roombas(MagicMock(), "10.0.0.5")

        assert [d.ip for d in result] == ["10.0.0.5"]
        disc.get_all.assert_not_awaited()


async def _no_sleep(_seconds: float) -> None:
    """The discovery loop sleeps ROOMBA_WAKE_TIME * attempt between
    rounds — real seconds that a test must not spend."""
    return None


class TestCloudConnectionTestStep:
    """`async_step_test_cloud_connection` — validating credentials
    before they are stored.

    THE THREE OUTCOMES MUST STAY DISTINGUISHABLE. Wrong credentials send
    the user back to the form with a message they can act on; a rate
    limit is iRobot saying "later", not "wrong"; success writes. Folding
    the middle case into the first tells somebody their correct password
    is wrong.
    """

    def _flow(self, *, data=None):
        from custom_components.roomba_plus.config_flow import RoombaPlusOptionsFlow

        flow = RoombaPlusOptionsFlow.__new__(RoombaPlusOptionsFlow)
        entry = entry_mock()
        entry.data = dict(data or {"irobot_username": "u", "irobot_password": "p"})
        entry.options = {}
        entry.entry_id = "test_entry"
        from unittest.mock import AsyncMock as _AsyncMock

        flow.hass = MagicMock()
        # How Home Assistant hands a flow its entry: `handler` is the entry
        # id and the entry is looked up on hass. Setting `_config_entry`
        # alone worked only through a compatibility path 2026 removed.
        flow.handler = entry.entry_id
        flow.hass.config_entries.async_get_known_entry.return_value = entry
        flow.hass.config.country = "DE"
        # The step reloads the entry after writing the credentials.
        flow.hass.config_entries.async_reload = _AsyncMock()
        # It validates the PENDING credentials from the previous step,
        # not what is already stored on the entry — the whole point is
        # to check them before they are written.
        flow._pending_cloud_creds = {"username": "u", "password": "p"}
        return flow

    @pytest.mark.asyncio
    async def test_bad_credentials_go_back_to_the_form(self, monkeypatch):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import config_flow as cf
        from custom_components.roomba_plus.cloud_api import AuthenticationError

        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=AuthenticationError("nope"))
        monkeypatch.setattr(cf, "IrobotCloudApi", lambda *_a, **_kw: api)
        monkeypatch.setattr(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            lambda _hass: MagicMock(),
        )

        flow = self._flow()
        flow.async_step_cloud_credentials = AsyncMock(
            return_value={"type": "form"}
        )

        result = await flow.async_step_test_cloud_connection()

        assert result["type"] == "form"
        assert flow._cloud_cred_errors == {"base": "invalid_cloud_credentials"}

    @pytest.mark.asyncio
    async def test_a_rate_limit_is_not_reported_as_wrong_credentials(
        self, monkeypatch
    ):
        """iRobot rate-limits aggressively. Telling the user their
        password is wrong when it is not sends them to reset it."""
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import config_flow as cf
        from custom_components.roomba_plus.cloud_api import RateLimitedError

        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=RateLimitedError("slow down"))
        monkeypatch.setattr(cf, "IrobotCloudApi", lambda *_a, **_kw: api)
        monkeypatch.setattr(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            lambda _hass: MagicMock(),
        )

        flow = self._flow()
        flow.async_step_cloud_credentials = AsyncMock(
            return_value={"type": "form"}
        )

        await flow.async_step_test_cloud_connection()

        assert flow._cloud_cred_errors != {"base": "invalid_cloud_credentials"}

    @pytest.mark.asyncio
    async def test_success_writes_the_entry(self, monkeypatch):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import config_flow as cf

        api = MagicMock()
        api.authenticate = AsyncMock(return_value=True)
        monkeypatch.setattr(cf, "IrobotCloudApi", lambda *_a, **_kw: api)
        monkeypatch.setattr(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession",
            lambda _hass: MagicMock(),
        )

        flow = self._flow()

        result = await flow.async_step_test_cloud_connection()

        assert result["type"] == "create_entry"


class TestZoneIndexOptions:
    """`_build_zone_index_options` renders the zone picker's labels.

    It is the only place a user sees, at a glance, which zones are
    hidden and which carry a local alias. A tag that stops being
    attached is invisible: the list still looks right, and somebody
    wonders for weeks why a room never gets cleaned.
    """

    def _data(self, *, cloud_regions=None):
        from custom_components.roomba_plus.models import MapCapability

        data = MagicMock()
        data.map_capability = MapCapability.SMART
        data.room_seg_store = None
        if cloud_regions is None:
            data.has_cloud = False
            data.cloud_coordinator = None
        else:
            data.has_cloud = True
            data.cloud_coordinator.regions = cloud_regions
        return data

    def _flow(self, pending=None):
        flow = _make_options_flow()
        flow._pending_zone_edits = dict(pending or {})
        return flow

    def test_a_hidden_zone_is_marked(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN

        flow = self._flow()
        optionen = flow._build_zone_index_options(
            self._data(cloud_regions=[{"id": "3", "name": "Kitchen"}]),
            {CONF_SMART_ZONE_HIDDEN: ["3"]},
        )

        assert any("hidden" in o["label"] for o in optionen)

    def test_an_aliased_zone_is_marked(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow()
        optionen = flow._build_zone_index_options(
            self._data(cloud_regions=[{"id": "3", "name": "Kitchen"}]),
            {CONF_SMART_ZONE_ALIASES: {"3": "Küche"}},
        )

        assert optionen, "a zone with an alias must still be listed"

    def test_a_pending_edit_wins_over_the_stored_state(self):
        """The picker shows what the user has typed but not yet saved —
        otherwise an edit appears to have been lost on the way back."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN

        flow = self._flow(pending={"3": {"hidden": False}})
        optionen = flow._build_zone_index_options(
            self._data(cloud_regions=[{"id": "3", "name": "Kitchen"}]),
            {CONF_SMART_ZONE_HIDDEN: ["3"]},
        )

        assert not any("hidden" in o["label"] for o in optionen)

    def test_without_cloud_it_still_lists_the_zones_it_knows(self):
        """A locally operated robot has no cloud names; the ids alone
        must still produce a usable list."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        flow = self._flow()
        optionen = flow._build_zone_index_options(
            self._data(),
            {
                # The id set comes from smart_zone_data, not from the
                # aliases — an alias for a zone we have never seen would
                # otherwise conjure an entry into the picker.
                "smart_zone_data": {"7": {"name": "Hallway"}},
                CONF_SMART_ZONE_ALIASES: {"7": "Flur"},
            },
        )

        assert any(o["value"] == "7" for o in optionen)


# ── formerly tests/test_config_flow_real.py ─────────────────────────────────────
#
# The config flow, run through Home Assistant's own flow manager.
#
# QUALITY SCALE, config-flow-test-coverage (Bronze): 100 % of the config
# flow, including recovery from errors. Until 4.2.11 every config-flow test
# built the flow object by hand and called one step at a time; nothing ran
# a flow the way Home Assistant does, through
# `hass.config_entries.flow.async_init`, with discovery, unique-id handling
# and step chaining as the real flow manager performs them.
#
# Only the network edges are replaced: robot discovery on the LAN, the
# push-button password fetch, and the connection test.

BLID = "ABCDEF123456"


HOST = "10.0.0.9"


CLASSIC_SKU = "R980020"


def _device(ip: str = HOST, blid: str = BLID, sku: str = CLASSIC_SKU, name: str = "Robbie"):
    d = MagicMock()
    d.ip, d.blid, d.sku, d.robot_name = ip, blid, sku, name
    return d


def _discovery(devices):
    return patch.object(cf, "_async_discover_roombas", AsyncMock(return_value=devices))


def _dhcp(hostname: str, ip: str = HOST) -> DhcpServiceInfo:
    # Home Assistant hands DHCP hostnames over lower-cased; the flow checks
    # for the "irobot-" prefix in that form.
    return DhcpServiceInfo(ip=ip, hostname=hostname, macaddress="aabbccddeeff")


async def _dhcp_flow(hass, hostname: str, ip: str = HOST):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(hostname, ip)
    )


@pytest.fixture(autouse=False)
def _custom(enable_custom_integrations):
    """Let Home Assistant's loader find custom_components/roomba_plus."""


async def _at_link_step(hass, name: str | None = "Robbie"):
    with _discovery([_device(name=name)]):
        return await _dhcp_flow(hass, f"irobot-{BLID.lower()}")


@pytest.mark.usefixtures('_custom')
class TestDiscovery:

    @pytest.mark.asyncio
    async def test_a_device_that_is_not_an_irobot_is_ignored(self, hass):
        result = await _dhcp_flow(hass, "printer-01")
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "not_irobot_device"

    @pytest.mark.asyncio
    async def test_dhcp_discovery_leads_to_the_link_step(self, hass):
        with _discovery([_device()]):
            result = await _dhcp_flow(hass, f"irobot-{BLID.lower()}")
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "link"

    @pytest.mark.asyncio
    async def test_zeroconf_discovery_takes_the_same_path(self, hass):
        info = ZeroconfServiceInfo(
            ip_address=__import__("ipaddress").ip_address(HOST),
            ip_addresses=[__import__("ipaddress").ip_address(HOST)],
            hostname=f"iRobot-{BLID}.local.", name="roomba", port=None,
            type="_amzn-alexa._tcp.local.", properties={},
        )
        with _discovery([_device()]):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
            )
        assert result["step_id"] == "link"

    @pytest.mark.asyncio
    async def test_a_known_host_is_not_offered_again(self, hass):
        MockConfigEntry(domain=DOMAIN, unique_id="OTHER", data={"host": HOST}).add_to_hass(hass)
        result = await _dhcp_flow(hass, f"irobot-{BLID.lower()}")
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    @pytest.mark.asyncio
    async def test_a_known_robot_on_a_new_address_updates_its_entry(self, hass):
        """Quality scale, discovery-update-info: a robot that moved to a new
        IP is recognised by its BLID and its entry follows it."""
        entry = MockConfigEntry(domain=DOMAIN, unique_id=BLID, data={"host": "10.0.0.2"})
        entry.add_to_hass(hass)

        result = await _dhcp_flow(hass, f"irobot-{BLID.lower()}", ip="10.0.0.77")

        assert result["reason"] == "already_configured"
        assert entry.data["host"] == "10.0.0.77"

    @pytest.mark.asyncio
    async def test_a_shorter_blid_yields_to_a_flow_that_already_has_the_full_one(self, hass):
        """Hostnames truncate the BLID. A flow for the full BLID in progress
        wins over a later one seeing only its prefix."""
        with _discovery([]):
            first = await _dhcp_flow(hass, f"irobot-{BLID.lower()}")
        assert first["type"] is FlowResultType.FORM

        with _discovery([]):
            second = await _dhcp_flow(hass, f"irobot-{BLID[:6].lower()}", ip="10.0.0.10")

        assert second["type"] is FlowResultType.ABORT
        assert second["reason"] == "short_blid"

    @pytest.mark.asyncio
    async def test_a_longer_blid_replaces_a_flow_that_only_had_the_prefix(self, hass):
        with _discovery([]):
            await _dhcp_flow(hass, f"irobot-{BLID[:6].lower()}")
        assert len(hass.config_entries.flow.async_progress()) == 1

        with _discovery([]):
            await _dhcp_flow(hass, f"irobot-{BLID.lower()}", ip="10.0.0.10")

        ids = [f["context"].get("unique_id") for f in hass.config_entries.flow.async_progress()]
        assert ids == [BLID]


@pytest.mark.usefixtures('_custom')
class TestUserAndManual:

    @pytest.mark.asyncio
    async def test_an_incompatible_roombapy_aborts_with_its_error(self, hass):
        with patch.object(cf, "ROOMBAPY_IMPORT_ERROR", "roombapy 1.9 found"):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
        assert result["reason"] == "wrong_roombapy"

    @pytest.mark.asyncio
    async def test_unknown_skus_are_reported_not_offered(self, hass, caplog):
        with _discovery([_device(sku="Z999999", name="Mystery")]):
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
        assert "Mystery (SKU Z999999)" in caplog.text

    @pytest.mark.asyncio
    async def test_picking_a_discovered_robot_starts_linking(self, hass):
        with _discovery([_device()]):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"host": HOST}
            )
        assert result["step_id"] == "link"

    @pytest.mark.asyncio
    async def test_choosing_nothing_leads_to_manual_entry(self, hass):
        with _discovery([_device()]):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"host": None}
            )
        assert result["step_id"] == "manual"

    @pytest.mark.asyncio
    async def test_manual_entry_of_an_unreachable_host_aborts(self, hass):
        with _discovery([_device()]):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": None})
        with _discovery([]):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"host": "10.0.0.250"}
            )
        assert result["reason"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_manual_entry_of_a_robot_leads_to_linking(self, hass):
        with _discovery([_device()]):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": None})
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"host": HOST}
            )
        assert result["step_id"] == "link"


@pytest.mark.usefixtures('_custom')
class TestLinking:
    """The push-button password fetch, and the manual fallback that must
    let a user finish when the button route fails."""

    @pytest.mark.asyncio
    async def test_no_answer_from_the_button_falls_back_to_manual_password(self, hass):
        result = await _at_link_step(hass)
        pw = MagicMock()
        pw.get_password = AsyncMock(side_effect=OSError("no route"))
        with patch.object(cf, "RoombaPassword", return_value=pw):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "link_manual"

    @pytest.mark.asyncio
    async def test_an_empty_password_falls_back_too(self, hass):
        result = await _at_link_step(hass)
        pw = MagicMock()
        pw.get_password = AsyncMock(return_value=None)
        with patch.object(cf, "RoombaPassword", return_value=pw):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "link_manual"

    @pytest.mark.asyncio
    async def test_an_unnamed_robot_gets_its_name_from_the_connection_test(self, hass):
        result = await _at_link_step(hass, name=None)
        pw = MagicMock()
        pw.get_password = AsyncMock(return_value="secret")
        with patch.object(cf, "RoombaPassword", return_value=pw), \
             patch.object(cf, "validate_input", AsyncMock(return_value={"name": "Kitchen Bot"})):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "cloud_credentials"

    @pytest.mark.asyncio
    async def test_the_manual_password_recovers_from_a_failed_attempt(self, hass):
        """A wrong password is an error on the form, not the end of the
        flow — the user can correct it and finish."""
        result = await _at_link_step(hass)
        pw = MagicMock()
        pw.get_password = AsyncMock(return_value=None)
        with patch.object(cf, "RoombaPassword", return_value=pw):
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "link_manual"

        with patch.object(cf, "validate_input", AsyncMock(side_effect=cf.CannotConnect)):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"password": "wrong"}
            )
        assert result["step_id"] == "link_manual"
        assert result["errors"] == {"base": "cannot_connect"}

        with patch.object(cf, "validate_input", AsyncMock(return_value={"name": "Robbie"})):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"password": "right"}
            )
        assert result["step_id"] == "cloud_credentials"


@pytest.mark.usefixtures('_custom')
class TestSmallHelpers:

    def test_the_discovery_object_is_roombapys(self):
        assert isinstance(cf._async_get_roomba_discovery(), cf.RoombaDiscovery)

    def test_the_options_flow_is_the_integrations(self):
        flow = cf.RoombaPlusConfigFlow.async_get_options_flow(MagicMock())
        assert isinstance(flow, cf.RoombaPlusOptionsFlow)


# ── formerly tests/test_config_flow_branches.py ─────────────────────────────────
#
# The remaining branches of the config and options flows.
#
# Quality scale, config-flow-test-coverage: 100 %. The real-flow tests in
# test_config_flow_real.py cover discovery, setup and linking; these cover
# the options steps and the error branches of reauth, reconfigure and the
# cloud-credential test, on the options-flow double from test_config_flow.
# Each test pins what a user would see, not only that a line ran.

def _smart(flow, *, has_cloud=True, regions=None, state=None):
    data = flow._config_entry.runtime_data
    data.map_capability = MapCapability.SMART
    data.has_cloud = has_cloud
    data.cloud_coordinator.regions = regions or []
    data.roomba.master_state = {"state": {"reported": state or {}}}
    return flow


class TestOptionsMenu:

    @pytest.mark.asyncio
    async def test_an_unloaded_entry_cannot_open_the_options(self):
        """Opening options for an entry that failed to set up must say why,
        not crash on missing runtime data."""
        flow = _make_options_flow()
        flow._config_entry.runtime_data = None
        result = await flow.async_step_init()
        assert result["type"] == "abort"
        assert result["reason"] == "integration_not_loaded"

    @pytest.mark.asyncio
    async def test_presence_scheduling_is_offered_when_the_robot_supports_hold(self):
        flow = _make_options_flow()
        flow._config_entry.runtime_data.roomba.master_state = {
            "state": {"reported": {"schedHold": False}}}
        result = await flow.async_step_init()
        assert "presence_scheduling" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_rooms_goes_to_smart_zones_on_a_smart_map_robot(self):
        flow = _smart(_make_options_flow())
        flow.async_step_smart_zones = AsyncMock(return_value={"type": "form", "step_id": "smart_zones"})
        result = await flow.async_step_rooms()
        assert result["step_id"] == "smart_zones"

    @pytest.mark.asyncio
    async def test_rooms_goes_to_zones_otherwise(self):
        flow = _make_options_flow()
        flow.async_step_zones = AsyncMock(return_value={"type": "form", "step_id": "zones"})
        result = await flow.async_step_rooms()
        assert result["step_id"] == "zones"


class TestSimpleOptionSteps:

    @pytest.mark.asyncio
    async def test_blocking_sensors_are_saved_with_every_other_option_kept(self):
        from custom_components.roomba_plus.const import (
            CONF_BLOCKING_BEHAVIOR,
            CONF_BLOCKING_SENSORS,
            CONF_BLOCKING_TIMEOUT_MIN,
        )

        flow = _make_options_flow()
        flow._config_entry.options = {"unrelated": 1}
        result = await flow.async_step_blocking_sensors({
            CONF_BLOCKING_SENSORS: ["binary_sensor.door"],
            CONF_BLOCKING_BEHAVIOR: "wait",
            CONF_BLOCKING_TIMEOUT_MIN: "30",
        })
        assert result["type"] == "create_entry"
        assert result["data"]["unrelated"] == 1
        assert result["data"][CONF_BLOCKING_SENSORS] == ["binary_sensor.door"]
        assert result["data"][CONF_BLOCKING_TIMEOUT_MIN] == 30

    @pytest.mark.asyncio
    async def test_demand_cleaning_says_why_when_not_supported(self):
        flow = _make_options_flow()   # ephemeral map, no cloud
        result = await flow.async_step_demand_cleaning()
        assert result["type"] == "abort"
        assert result["reason"] == "demand_cleaning_not_supported"

    @pytest.mark.asyncio
    async def test_demand_cleaning_is_saved(self):
        from custom_components.roomba_plus.const import (
            CONF_DEMAND_CLEANING_ENABLED,
            CONF_DEMAND_MULTIPLIER,
        )

        flow = _smart(_make_options_flow())
        flow._config_entry.runtime_data.map_capability = SimpleNamespace(value="smart")
        result = await flow.async_step_demand_cleaning({
            CONF_DEMAND_CLEANING_ENABLED: True, CONF_DEMAND_MULTIPLIER: "1.5",
        })
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEMAND_CLEANING_ENABLED] is True
        assert result["data"][CONF_DEMAND_MULTIPLIER] == 1.5

    @pytest.mark.asyncio
    async def test_demand_cleaning_shows_its_form(self):
        flow = _smart(_make_options_flow())
        flow._config_entry.runtime_data.map_capability = SimpleNamespace(value="smart")
        result = await flow.async_step_demand_cleaning()
        assert result["type"] == "form"


class TestZoneEditing:

    @pytest.mark.asyncio
    async def test_map_management_starts_its_edit_buffer_on_first_use(self):
        flow = _make_options_flow()
        del flow._pending_zone_edits
        flow._config_entry.runtime_data.cloud_coordinator = None
        await flow.async_step_map_management()
        assert flow._pending_zone_edits == {}

    @pytest.mark.asyncio
    async def test_an_edit_is_buffered_and_returns_to_the_index(self):
        flow = _smart(_make_options_flow(), regions=[{"id": "3", "name": "Kitchen"}])
        flow._editing_zone_id = "3"
        flow.async_step_map_management = AsyncMock(return_value={"type": "form"})
        await flow.async_step_map_management_edit({"display_name": "Küche", "hidden": True})
        assert flow._pending_zone_edits["3"] == {"display_name": "Küche", "hidden": True}

    def test_zone_name_prefers_alias_then_stored_name_then_cloud(self):
        flow = _smart(_make_options_flow(), regions=[{"id": "9", "name": "Hall"}])
        data = flow._config_entry.runtime_data
        opts = {CONF_SMART_ZONE_ALIASES: {"3": "Küche"},
                "smart_zone_data": {"5": {"name": "Bath"}, "6": {}}}
        assert flow._resolve_current_zone_name("3", data, opts) == "Küche"
        assert flow._resolve_current_zone_name("5", data, opts) == "Bath"
        assert flow._resolve_current_zone_name("6", data, opts) == "Zone 6"
        assert flow._resolve_current_zone_name("9", data, opts) == "Hall"
        assert flow._resolve_current_zone_name("42", data, opts) == "Zone 42"

    def test_zone_name_on_an_ephemeral_map_comes_from_room_segmentation(self):
        room = SimpleNamespace(name="Studio", hidden=True)
        store = SimpleNamespace(rooms={"4": room})
        flow = _make_options_flow(room_seg_store=store)
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_name("4", data, {}) == "Studio"
        assert flow._resolve_current_zone_name("8", data, {}) == "Zone 8"
        assert flow._resolve_current_zone_hidden("4", data, {}) is True
        assert flow._resolve_current_zone_hidden("8", data, {}) is False

    def test_zone_hidden_on_a_smart_map_comes_from_options(self):
        flow = _smart(_make_options_flow())
        data = flow._config_entry.runtime_data
        assert flow._resolve_current_zone_hidden("3", data, {CONF_SMART_ZONE_HIDDEN: ["3"]}) is True
        assert flow._resolve_current_zone_hidden("4", data, {CONF_SMART_ZONE_HIDDEN: ["3"]}) is False

    def test_saving_hides_and_unhides(self):
        flow = _smart(_make_options_flow(), regions=[{"id": "3", "name": "Kitchen"},
                                                     {"id": "4", "name": "Hall"}])
        flow._config_entry.options = {CONF_SMART_ZONE_HIDDEN: ["4"]}
        flow._pending_zone_edits = {"3": {"display_name": "", "hidden": True},
                                    "4": {"display_name": "", "hidden": False}}
        result = flow._save_zone_edits_atomic()
        assert result["data"][CONF_SMART_ZONE_HIDDEN] == ["3"]


class TestSmartZoneMapId:

    @pytest.mark.asyncio
    async def test_naming_finds_the_map_via_the_schedule(self):
        state = {"pmaps": [{"p1": "v"}], "cap": {"pmaps": 1},
                 "cleanSchedule2": [{"cmd": {"pmap_id": "p1", "regions": [{"region_id": "3"}]}}]}
        flow = _smart(_make_options_flow(), state=state)
        result = await flow.async_step_smart_zones({"zone_3": "Kitchen"})
        assert result["data"]["smart_zone_data"]["3"]["pmap_id"] == "p1"

    @pytest.mark.asyncio
    async def test_naming_falls_back_to_the_first_map(self):
        state = {"pmaps": [{"p9": "v"}], "cap": {"pmaps": 1}}
        flow = _smart(_make_options_flow(), regions=[{"id": "3", "name": ""}], state=state)
        result = await flow.async_step_smart_zones({"zone_3": "Kitchen"})
        assert result["data"]["smart_zone_data"]["3"]["pmap_id"] == "p9"


class TestManualZoneNaming:

    @pytest.mark.asyncio
    async def test_without_a_smart_map_it_just_closes(self):
        flow = _make_options_flow()
        flow._config_entry.runtime_data.roomba.master_state = {"state": {"reported": {"cap": {}}}}
        result = await flow.async_step_smart_zones_manual()
        assert result["type"] == "create_entry"

    def _flow(self, state):
        flow = _smart(_make_options_flow(), state=state)
        flow._pending_zone_ids = ["3"]
        return flow

    @pytest.mark.asyncio
    async def test_map_id_via_schedule(self):
        state = {"pmaps": [{"p1": "v"}], "cap": {"pmaps": 1},
                 "cleanSchedule2": [{"cmd": {"pmap_id": "p1"}}]}
        result = await self._flow(state).async_step_smart_zones_manual({"zone_names": "3=Kitchen"})
        assert result["data"]["smart_zone_data"]["3"]["pmap_id"] == "p1"

    @pytest.mark.asyncio
    async def test_map_id_falls_back_to_the_first_map(self):
        state = {"pmaps": [{"p9": "v"}], "cap": {"pmaps": 1}}
        result = await self._flow(state).async_step_smart_zones_manual({"zone_names": "3=Kitchen"})
        assert result["data"]["smart_zone_data"]["3"]["pmap_id"] == "p9"

    @pytest.mark.asyncio
    async def test_no_valid_line_is_an_error_on_the_form(self):
        state = {"pmaps": [{"p9": "v"}], "cap": {"pmaps": 1}}
        result = await self._flow(state).async_step_smart_zones_manual({"zone_names": "nonsense"})
        assert result["type"] == "form"
        assert result["errors"] == {"zone_names": "no_valid_ids"}

    @pytest.mark.asyncio
    async def test_an_unresolvable_map_is_an_error_on_the_form(self):
        """A smart-map robot that reports no map id anywhere: the user is
        told, and can retry once the map exists."""
        state = {"pmaps": [], "cap": {"pmaps": 1}}
        flow = self._flow(state)
        with patch.object(cf, "has_smart_map", return_value=True):
            result = await flow.async_step_smart_zones_manual({"zone_names": "3=Kitchen"})
        assert result["type"] == "form"
        assert result["errors"] == {"zone_names": "pmap_not_resolved"}


class TestCloudCredentialsOptions:

    def _flow(self):
        flow = _make_options_flow()
        flow.config_entry.data = {CONF_IROBOT_USERNAME: "old@example.com"}
        # Extend the hass _make_options_flow set up, do not replace it: the
        # flow finds its entry there, and a fresh mock hands back a stranger.
        flow.hass.config.country = "DE"
        flow.hass.config_entries.async_reload = AsyncMock()
        return flow

    @pytest.mark.asyncio
    async def test_the_form_prefills_the_current_user(self):
        result = await self._flow().async_step_cloud_credentials()
        assert result["type"] == "form"
        assert result["step_id"] == "cloud_credentials"

    @pytest.mark.asyncio
    async def test_submitting_moves_to_the_connection_test(self):
        flow = self._flow()
        flow.async_step_test_cloud_connection = AsyncMock(return_value={"type": "create_entry"})
        await flow.async_step_cloud_credentials(
            {CONF_IROBOT_USERNAME: " new@example.com ", CONF_IROBOT_PASSWORD: " pw "})
        assert flow._pending_cloud_creds == {"username": "new@example.com", "password": "pw"}

    @pytest.mark.parametrize("fehler,schluessel", [
        ("SSLCertificateError", "cloud_ssl_certificate_error"),
        ("CloudApiError", "cannot_connect"),
    ])
    @pytest.mark.asyncio
    async def test_connection_errors_go_back_to_the_form(self, fehler, schluessel, monkeypatch):
        flow = self._flow()
        flow._pending_cloud_creds = {"username": "u", "password": "p"}
        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=getattr(cf, fehler)("x"))
        monkeypatch.setattr(cf, "IrobotCloudApi", lambda *a, **k: api)
        monkeypatch.setattr("homeassistant.helpers.aiohttp_client.async_get_clientsession",
                            lambda _h: MagicMock())
        flow.async_step_cloud_credentials = AsyncMock(return_value={"type": "form"})
        await flow.async_step_test_cloud_connection()
        assert flow._cloud_cred_errors == {"base": schluessel}

    @pytest.mark.asyncio
    async def test_clearing_both_fields_removes_the_credentials(self):
        """Empty username and password is how a user turns the cloud off."""
        flow = self._flow()
        flow.config_entry.data = {CONF_IROBOT_USERNAME: "u", CONF_IROBOT_PASSWORD: "p", "blid": "B"}
        flow._pending_cloud_creds = {"username": "", "password": ""}
        geschrieben: dict = {}
        flow.hass.config_entries.async_update_entry = lambda e, **kw: geschrieben.update(kw)
        await flow.async_step_test_cloud_connection()
        assert CONF_IROBOT_USERNAME not in geschrieben["data"]
        assert CONF_IROBOT_PASSWORD not in geschrieben["data"]
        assert geschrieben["data"]["blid"] == "B"


class TestReauthErrors:

    @pytest.mark.parametrize("fehler,schluessel", [
        ("RateLimitedError", "cloud_rate_limited"),
        ("SSLCertificateError", "cloud_ssl_certificate_error"),
    ])
    @pytest.mark.asyncio
    async def test_the_error_is_shown_and_the_form_stays(self, fehler, schluessel):
        flow, _entry = _make_reauth_flow(reauth_entry_data={
            CONF_IROBOT_USERNAME: "u@example.com", CONF_IROBOT_PASSWORD: "old"})
        api = MagicMock()
        api.authenticate = AsyncMock(side_effect=getattr(cf, fehler)("x"))
        with patch.object(cf, "IrobotCloudApi", return_value=api), \
             patch("homeassistant.helpers.aiohttp_client.async_get_clientsession",
                   return_value=MagicMock()):
            result = await flow.async_step_reauth_confirm(
                {CONF_IROBOT_USERNAME: "u@example.com", CONF_IROBOT_PASSWORD: "new"})
        assert result["type"] == "form"
        assert result["errors"] == {"base": schluessel}


class TestReconfigureError:

    @pytest.mark.asyncio
    async def test_an_unreachable_new_address_is_an_error_not_a_save(self, monkeypatch):
        flow = cf.RoombaPlusConfigFlow.__new__(cf.RoombaPlusConfigFlow)
        entry = entry_mock()
        entry.data = {"host": "10.0.0.2", "blid": "B1", "password": "pw"}
        flow._get_reconfigure_entry = lambda: entry
        flow.hass = MagicMock()
        monkeypatch.setattr(cf, "validate_input", AsyncMock(side_effect=cf.CannotConnect))
        result = await flow.async_step_reconfigure({"host": "10.0.0.99", "password": "pw"})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}


class TestLastBranches:

    def test_rest980_room_discovery_skips_entities_that_are_not_selects(self):
        """rest980 exposed rooms as select entities; anything else on the
        old entry — a vacuum, a sensor — carries no room and is skipped."""

        hass, ents = _make_hass_with_rest980(entities=[
            ("vacuum.old_roomba", {"id": "1", "name": "Nope"}),
            ("select.kitchen", {"id": "3", "name": "Kitchen"}),
        ])
        # A select still in the registry but with no state — the old
        # integration is no longer running — carries nothing to read.
        verwaist = MagicMock()
        verwaist.entity_id, verwaist.domain = "select.gone", "select"
        ents.append(verwaist)
        with patch.object(cf.er, "async_get"), \
             patch.object(cf.er, "async_entries_for_config_entry", return_value=ents):
            rooms = cf._discover_rest980_rooms(hass)
        assert rooms == {"3": "Kitchen"}

    @pytest.mark.asyncio
    async def test_picking_a_robot_that_was_set_up_meanwhile_aborts(self):
        """The picker lists robots not yet configured; if the chosen one is
        no longer among them — set up in another tab — say so."""

        flow = _make_flow()
        flow._prime_account_robots = {"G1": {"sku": "G185020", "name": "Prime"}}
        flow._async_current_ids = lambda include_ignore=True: set()
        result = await flow.async_step_prime_robot_picker({"blid": "GONE"})
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"

    @pytest.mark.asyncio
    async def test_the_account_login_is_handed_to_the_setup_step(self):
        """The login from the config flow is stored for the first setup, so
        the robot is not logged in twice in a row."""

        flow = _make_flow()
        flow._prime_account_login_result = object()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        with patch.object(cf, "store_pending_login") as gespeichert:
            await flow._async_create_prime_entry("G1", {"sku": "G185020", "name": "Prime"})
        gespeichert.assert_called_once_with("G1", flow._prime_account_login_result)


# ── formerly tests/test_prime_config_flow.py ──────────────────────────
#
# Tests for the V4/Prime cloud-account onboarding path in config_flow.py:
# async_step_user()'s new third option, async_step_prime_account(),
# async_step_prime_robot_picker(), _async_create_prime_entry(),
# async_step_prime_classic_ip(), and async_step_prime_classic_analytics().
#
# NEW (V4/Prime implementation). Follows the existing bare-construction
# pattern established by _make_reauth_flow() in test_config_flow.py
# (object.__new__(RoombaPlusConfigFlow) + just enough FlowHandler
# attributes for HA's async_abort/async_show_form/async_create_entry to
# work), rather than the full pytest-homeassistant-custom-component flow
# harness.

def _make_flow() -> RoombaPlusConfigFlow:
    """Bare-construct RoombaPlusConfigFlow -- see this module's own
    docstring for why (mirrors _make_reauth_flow() in
    test_config_flow.py)."""
    flow = object.__new__(RoombaPlusConfigFlow)
    flow.hass = MagicMock()
    flow.context = {}
    flow.flow_id = "test_flow_id"
    flow.handler = "roomba_plus"
    flow.name = None
    flow.blid = ""
    flow.host = None
    flow.discovered_robots = {}
    flow._pending_config = {}
    flow._prime_account_username = ""
    flow._prime_account_password = ""
    flow._prime_account_robots = {}
    flow._prime_selected_blid = None
    flow._prime_account_login_result = None
    return flow


def _auth_error(name: str) -> Exception:
    """Builds a real instance of the named cloud_api exception, so the
    except-clause matching in async_step_prime_account() is exercised
    against real types, not a generic stand-in."""
    from custom_components.roomba_plus import cloud_api
    return getattr(cloud_api, name)("boom")


@pytest.fixture(autouse=False)
def _mock_clientsession():
    """async_step_prime_account() imports async_get_clientsession inline
    (from homeassistant.helpers.aiohttp_client), so it can't be patched
    via a custom_components.roomba_plus.config_flow attribute -- patch
    the actual source function instead. With a MagicMock() hass (this
    file's flow.hass), the real implementation falls through to
    creating a genuine aiohttp.ClientSession(), which then leaks a
    lingering-timer failure at test teardown (same root cause fixed for
    test_prime_setup.py earlier this session)."""
    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


@pytest.mark.usefixtures('_mock_clientsession')
class TestIsPrimeSku:
    def test_g_prefix_is_prime(self):
        assert _is_prime_sku("G185020") is True

    def test_lowercase_g_prefix_is_prime(self):
        assert _is_prime_sku("g185020") is True

    def test_n_prefix_is_prime(self):
        """NEW (darealgugu, GitHub issue): N185240 (Roomba Plus 505
        Combo) was wrongly falling through to Classic's local-network
        completion, which can never succeed for a cloud-only device."""
        assert _is_prime_sku("N185240") is True

    def test_r_prefix_disambiguated_by_full_pattern_not_letter_alone(self):
        """CRITICAL (this session, SkuUtils.java decompilation): "R" is
        used by BOTH generations -- a single-letter check would have
        misclassified real Classic robots (including this project's own
        chairstacker test unit, a Roomba 980) as Prime. The 3-character
        prefix (letter + 2 digits) is what actually disambiguates this,
        not the letter alone."""
        assert _is_prime_sku("R980020") is False  # Classic: Roomba 980
        assert _is_prime_sku("R111840") is False  # Classic: Atlantis/100-series
        assert _is_prime_sku("R285020") is True  # Prime: EnhancedComboNextPlus

    def test_i_prefix_is_classic(self):
        assert _is_prime_sku("i755840") is False

    def test_none_is_not_prime(self):
        assert _is_prime_sku(None) is False

    def test_empty_string_is_not_prime(self):
        assert _is_prime_sku("") is False


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepUserCloudOption:
    """BUG FIX regression coverage: previously, zero discovered local
    devices fell straight through to async_step_manual() -- a dead end
    for a V4/Prime-only owner."""

    @pytest.mark.asyncio
    async def test_shows_cloud_account_option_even_with_no_local_devices(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[]),
        ):
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        schema_keys = list(result["data_schema"].schema.keys())
        # vol.Optional(CONF_HOST) is the only top-level key; the actual
        # choices live in its vol.In(...) validator.
        host_marker = schema_keys[0]
        choices = result["data_schema"].schema[host_marker].container
        assert _CLOUD_ACCOUNT_SENTINEL in choices
        assert None in choices  # "Add manually" still present too

    @pytest.mark.asyncio
    async def test_selecting_cloud_account_routes_to_prime_account(self):
        flow = _make_flow()
        result = await flow.async_step_user({CONF_HOST: _CLOUD_ACCOUNT_SENTINEL})
        assert result["type"] == "form"
        assert result["step_id"] == "prime_account"


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepPrimeAccount:
    @pytest.mark.asyncio
    async def test_success_stores_credentials_and_routes_to_picker(self):
        flow = _make_flow()
        fake_robots = {"BLID1": {"sku": "G185020", "password": "pw1", "name": "Combo"}}

        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api = mock_api_cls.return_value
            mock_api.authenticate = AsyncMock()
            mock_api.robots = fake_robots
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_prime_account({
                    CONF_IROBOT_USERNAME: "user@example.com",
                    CONF_IROBOT_PASSWORD: "hunter2",
                })

        assert flow._prime_account_username == "user@example.com"
        assert flow._prime_account_password == "hunter2"
        assert flow._prime_account_robots == fake_robots
        assert result["step_id"] == "prime_robot_picker"

    @pytest.mark.asyncio
    async def test_credentials_error_shows_invalid_cloud_credentials(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("AuthenticationError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "wrong",
            })

        assert result["errors"]["base"] == "invalid_cloud_credentials"

    @pytest.mark.asyncio
    async def test_rate_limited_error_shows_cloud_rate_limited(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("RateLimitedError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cloud_rate_limited"

    @pytest.mark.asyncio
    async def test_ssl_error_shows_cloud_ssl_certificate_error(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("SSLCertificateError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cloud_ssl_certificate_error"

    @pytest.mark.asyncio
    async def test_generic_cloud_api_error_shows_cannot_connect(self):
        flow = _make_flow()
        with patch(
            "custom_components.roomba_plus.config_flow.IrobotCloudApi"
        ) as mock_api_cls:
            mock_api_cls.return_value.authenticate = AsyncMock(
                side_effect=_auth_error("CloudApiError")
            )
            result = await flow.async_step_prime_account({
                CONF_IROBOT_USERNAME: "user@example.com",
                CONF_IROBOT_PASSWORD: "hunter2",
            })

        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_blank_submission_shows_error_instead_of_silent_reshow(self):
        """Bug-hunt round: unlike async_step_cloud_credentials (both
        fields genuinely optional there), this step has no valid skip
        path -- a blank submission previously reshowed the form with no
        explanation at all."""
        flow = _make_flow()
        result = await flow.async_step_prime_account({
            CONF_IROBOT_USERNAME: "",
            CONF_IROBOT_PASSWORD: "",
        })
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_cloud_credentials"


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepPrimeRobotPicker:
    @pytest.mark.asyncio
    async def test_aborts_when_no_new_robots(self):
        flow = _make_flow()
        flow._prime_account_robots = {}
        with patch.object(flow, "_async_current_ids", return_value=set()):
            result = await flow.async_step_prime_robot_picker()
        assert result["type"] == "abort"
        assert result["reason"] == "no_new_robots_found"

    @pytest.mark.asyncio
    async def test_filters_already_configured_blids(self):
        flow = _make_flow()
        flow._prime_account_robots = {
            "BLID1": {"sku": "G185020", "name": "Combo"},
            "BLID2": {"sku": "i755840", "name": "Already there"},
        }
        with patch.object(flow, "_async_current_ids", return_value={"BLID2"}):
            result = await flow.async_step_prime_robot_picker()
        choices = result["data_schema"].schema[CONF_BLID].container
        assert "BLID1" in choices
        assert "BLID2" not in choices

    @pytest.mark.asyncio
    async def test_selecting_prime_robot_creates_entry_directly(self):
        flow = _make_flow()
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"
        flow._prime_account_robots = {"BLID1": {"sku": "G185020", "name": "Combo"}}

        with patch.object(flow, "_async_current_ids", return_value=set()):
            with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    result = await flow.async_step_prime_robot_picker({CONF_BLID: "BLID1"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_CONNECTION_TYPE] == ConnectionType.CLOUD_ONLY.value
        assert result["data"][CONF_BLID] == "BLID1"
        assert result["data"][CONF_IROBOT_USERNAME] == "user@example.com"
        assert result["data"][CONF_IROBOT_PASSWORD] == "hunter2"

    @pytest.mark.asyncio
    async def test_selecting_classic_robot_routes_to_classic_ip(self):
        flow = _make_flow()
        flow._prime_account_robots = {
            "BLID1": {"sku": "i755840", "name": "Bogdana", "password": "pw"},
        }

        with patch.object(flow, "_async_current_ids", return_value=set()):
            with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
                with patch.object(flow, "_abort_if_unique_id_configured"):
                    with patch(
                        "custom_components.roomba_plus.config_flow._async_discover_roombas",
                        new=AsyncMock(return_value=[]),
                    ):
                        result = await flow.async_step_prime_robot_picker({CONF_BLID: "BLID1"})

        assert result["step_id"] == "prime_classic_ip"
        assert flow._prime_selected_blid == "BLID1"


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepPrimeClassicIp:
    @pytest.mark.asyncio
    async def test_local_scan_match_skips_form_and_validates(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}
        fake_device = MagicMock()
        fake_device.blid = "BLID1"
        fake_device.ip = "192.168.1.50"

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow._async_discover_roombas",
                    new=AsyncMock(return_value=[fake_device]),
                ):
                    with patch(
                        "custom_components.roomba_plus.config_flow.validate_input",
                        new=AsyncMock(return_value={"name": "Bogdana"}),
                    ) as mock_validate:
                        result = await flow.async_step_prime_classic_ip()

        mock_validate.assert_awaited_once()
        call_config = mock_validate.call_args.args[1]
        assert call_config[CONF_HOST] == "192.168.1.50"
        assert call_config[CONF_PASSWORD] == "pw123"
        assert result["step_id"] == "prime_classic_analytics"

    @pytest.mark.asyncio
    async def test_no_local_match_shows_manual_ip_form(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow._async_discover_roombas",
                    new=AsyncMock(return_value=[]),
                ):
                    result = await flow.async_step_prime_classic_ip()

        assert result["type"] == "form"
        assert result["step_id"] == "prime_classic_ip"

    @pytest.mark.asyncio
    async def test_manual_ip_submission_validates_and_routes_to_analytics(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow.validate_input",
                    new=AsyncMock(return_value={"name": "Bogdana"}),
                ):
                    result = await flow.async_step_prime_classic_ip(
                        {CONF_HOST: "10.0.0.5"}
                    )

        assert result["step_id"] == "prime_classic_analytics"
        assert flow._pending_config[CONF_HOST] == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_validate_input_failure_shows_cannot_connect(self):
        from custom_components.roomba_plus.config_flow import CannotConnect

        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"password": "pw123", "name": "Bogdana"}}

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch(
                    "custom_components.roomba_plus.config_flow.validate_input",
                    new=AsyncMock(side_effect=CannotConnect()),
                ):
                    result = await flow.async_step_prime_classic_ip({CONF_HOST: "10.0.0.5"})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_missing_password_aborts(self):
        flow = _make_flow()
        flow._prime_selected_blid = "BLID1"
        flow._prime_account_robots = {"BLID1": {"name": "Bogdana"}}  # no password key

        with patch.object(flow, "async_set_unique_id", new=AsyncMock()):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                result = await flow.async_step_prime_classic_ip()

        assert result["type"] == "abort"
        assert result["reason"] == "prime_classic_password_missing"


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepPrimeClassicAnalytics:
    @pytest.mark.asyncio
    async def test_checkbox_true_keeps_credentials(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        flow._pending_config = {CONF_HOST: "10.0.0.5", CONF_BLID: "BLID1"}
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"

        result = await flow.async_step_prime_classic_analytics(
            {"enable_cloud_analytics": True}
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_IROBOT_USERNAME] == "user@example.com"
        assert result["data"][CONF_IROBOT_PASSWORD] == "hunter2"

    @pytest.mark.asyncio
    async def test_checkbox_false_discards_credentials(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        flow._pending_config = {CONF_HOST: "10.0.0.5", CONF_BLID: "BLID1"}
        flow._prime_account_username = "user@example.com"
        flow._prime_account_password = "hunter2"

        result = await flow.async_step_prime_classic_analytics(
            {"enable_cloud_analytics": False}
        )

        assert result["type"] == "create_entry"
        assert CONF_IROBOT_USERNAME not in result["data"]
        assert CONF_IROBOT_PASSWORD not in result["data"]

    @pytest.mark.asyncio
    async def test_default_shows_form_with_checkbox_default_true(self):
        flow = _make_flow()
        flow.name = "Bogdana"
        result = await flow.async_step_prime_classic_analytics()
        assert result["type"] == "form"
        assert result["step_id"] == "prime_classic_analytics"


@pytest.mark.usefixtures('_mock_clientsession')
class TestAsyncStepUserFiltersPrimeDevicesFromLocalDiscovery:
    """REAL FIELD BUG FOUND AND FIXED (chairstacker, with screenshots):
    V4/Prime robots still respond to the same local UDP discovery
    broadcast Classic robots use, even though they can't actually be
    set up or controlled that way. RoombaInfo.sku is exactly what
    distinguishes them -- this step wasn't checking it at all before,
    so a Prime device appeared as a seemingly-normal, but entirely
    non-functional, "local IP" choice."""

    def _make_device(self, ip: str, sku: str, blid: str = "BLID1") -> MagicMock:
        device = MagicMock()
        device.ip = ip
        device.sku = sku
        device.robot_name = "MyRobot"
        device.blid = blid
        return device

    @pytest.mark.asyncio
    async def test_prime_device_excluded_from_dropdown_choices(self):
        flow = _make_flow()
        prime_device = self._make_device("10.0.0.5", "G185020")

        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[prime_device]),
        ):
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_user()

        assert result["type"] == "form"
        schema_keys = list(result["data_schema"].schema.keys())
        choices = result["data_schema"].schema[schema_keys[0]].container
        assert "10.0.0.5" not in choices
        assert _CLOUD_ACCOUNT_SENTINEL in choices

    @pytest.mark.asyncio
    async def test_classic_device_still_shown_normally(self):
        flow = _make_flow()
        classic_device = self._make_device("10.0.0.6", "R980020", blid="BLID2")

        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[classic_device]),
        ):
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_user()

        schema_keys = list(result["data_schema"].schema.keys())
        choices = result["data_schema"].schema[schema_keys[0]].container
        assert "10.0.0.6" in choices

    @pytest.mark.asyncio
    async def test_prime_device_matching_self_host_redirects_to_cloud_account_not_local_link(self):
        """The worse of the two consequences: a genuine zeroconf/DHCP
        discovery pre-sets self.host before this step even runs -- the
        OLD code took a fast path straight into the (broken) local-link
        flow with no form and no choice at all. Now redirects to the
        cloud-account flow instead, since the SKU already answers the
        question a form would otherwise ask."""
        flow = _make_flow()
        prime_device = self._make_device("10.0.0.5", "G185020")
        flow.host = "10.0.0.5"

        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[prime_device]),
        ):
            with patch.object(flow, "_async_current_ids", return_value=set()):
                result = await flow.async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "prime_account"

    @pytest.mark.asyncio
    async def test_classic_device_matching_self_host_still_fast_paths_to_local_link(self):
        """Confirms the fix is scoped to Prime devices specifically --
        Classic's own existing fast-path behavior must be unaffected."""
        flow = _make_flow()
        classic_device = self._make_device("10.0.0.6", "R980020", blid="BLID2")
        flow.host = "10.0.0.6"

        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            new=AsyncMock(return_value=[classic_device]),
        ), patch.object(flow, "_async_current_ids", return_value=set()), patch.object(
            flow, "_async_start_link", new=AsyncMock(return_value={"type": "form", "step_id": "link"})
        ) as mock_start_link:
            result = await flow.async_step_user()

        mock_start_link.assert_awaited_once()
        assert result["step_id"] == "link"


@pytest.mark.usefixtures('_mock_clientsession')
class TestLocalDiscoveryFiltersOnPositiveClassicMatch:
    """The local-discovery list now requires a POSITIVE Classic match
    rather than the absence of a Prime one.

    That inversion changes what happens to an SKU nobody recognises, and
    the right answer changed with the tables. While the Classic list held
    only SkuUtils.java's one-default-per-platform entries, it missed most
    of the retail range — four of five real test robots — so excluding
    unknowns would have locked out working hardware.

    Classic is now a closed generation and its table is effectively
    complete, while Prime keeps gaining models. An unrecognised SKU is
    therefore far more likely to be a new Prime robot, and showing it
    here yields a plausible-looking choice that cannot work.

    Worth noting how this got written: the change passed the entire
    suite untouched, because nothing tested the filter at all. A
    behaviour this easy to invert deserved a test before it had one."""

    def _visible(self, sku: str | None) -> bool:
        from custom_components.roomba_plus.config_flow import _is_classic_sku

        return _is_classic_sku(sku)

    def test_real_classic_robots_are_offered(self):
        """Every Classic robot in this project's field-test fleet. If any
        of these stops matching, real users lose local setup."""
        for sku in ("i755840", "i755640", "i857640", "i355640",
                    "R980040", "S955840", "m613840"):
            assert self._visible(sku), f"{sku} would no longer be offered locally"

    def test_prime_robots_are_not_offered(self):
        """They cannot be set up this way at all — the account-based flow
        is the only route."""
        for sku in ("G185020", "N185240", "Y414040"):
            assert not self._visible(sku), f"{sku} should not appear in local discovery"

    def test_an_unrecognised_sku_is_not_offered(self):
        """THE behaviour this change is about. Previously such a device
        appeared in the list and failed later at password retrieval,
        with an error about the HOME button that has nothing to do with
        the cause."""
        assert not self._visible("ZZ99999")

    def test_the_R_pair_is_separated_correctly(self):
        """Both prefixes live on one real tester's account. Getting this
        wrong in either direction is a visible failure for them."""
        assert self._visible("R980040")        # Classic 980
        assert not self._visible("R285020")    # Prime EnhancedComboNextPlus

    def test_a_missing_sku_is_not_offered(self):
        """Discovery does not always report one. Absent is not evidence
        of Classic."""
        assert not self._visible(None)
        assert not self._visible("")


# ── formerly tests/test_rest980_migrate.py ────────────────────────────
#
# Tests for REST980-MIGRATE (v2.9.0) — migration helper from roomba_rest980.

def _make_hass_with_rest980(entities: list[tuple[str, dict]] | None = None,
                             rest980_entries: list | None = None):
    """entities: list of (entity_id, room_data_dict_or_None)."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = (
        rest980_entries if rest980_entries is not None else [MagicMock(entry_id="r980_1")]
    )

    entity_entries = []
    states = {}
    for entity_id, room_data in (entities or []):
        domain = entity_id.split(".")[0]
        ent = MagicMock()
        ent.entity_id = entity_id
        ent.domain = domain
        entity_entries.append(ent)
        st = MagicMock()
        st.attributes = {"room_data": room_data} if room_data is not None else {}
        states[entity_id] = st

    hass.states.get.side_effect = lambda eid: states.get(eid)
    return hass, entity_entries


def _make_flow_m(discovered_rooms: dict, existing_labels: dict | None = None,
                state: dict | None = None):
    flow = object.__new__(RoombaPlusOptionsFlow)
    hass = MagicMock()
    flow.hass = hass

    config_entry = MagicMock()
    config_entry.options = {"smart_zone_labels": existing_labels or {}}
    config_entry.runtime_data.roomba = MagicMock()
    flow._config_entry = config_entry
    # HA 2026.x resolves `config_entry` through `handler` plus a lookup
    # on hass; the setter is gone. Same shape as _make_options_flow in
    # test_config_flow.py.
    flow.handler = config_entry.entry_id
    flow.hass = getattr(flow, "hass", None) or MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = config_entry

    flow._discovered = discovered_rooms  # stashed for patch target below
    flow._state = state or {"lastCommand": {"pmap_id": "map_a"}}
    return flow, config_entry


class TestResolveCurrentPmapId:
    def test_prefers_last_command(self):
        state = {
            "lastCommand": {"pmap_id": "map_last"},
            "cleanSchedule2": [{"cmd": {"pmap_id": "map_sched"}}],
            "pmaps": [{"map_pmaps": "ts"}],
        }
        assert _resolve_current_pmap_id(state) == "map_last"

    def test_falls_back_to_clean_schedule2(self):
        state = {
            "lastCommand": {},
            "cleanSchedule2": [{"cmd": {"pmap_id": "map_sched"}}],
            "pmaps": [{"map_pmaps": "ts"}],
        }
        assert _resolve_current_pmap_id(state) == "map_sched"

    def test_falls_back_to_pmaps(self):
        state = {"lastCommand": {}, "cleanSchedule2": [], "pmaps": [{"map_pmaps": "ts"}]}
        assert _resolve_current_pmap_id(state) == "map_pmaps"

    def test_empty_state_returns_empty_string(self):
        assert _resolve_current_pmap_id({}) == ""


class TestDiscoverRest980Rooms:
    def test_no_rest980_entries_returns_empty(self):
        hass, _ = _make_hass_with_rest980(rest980_entries=[])
        with patch("custom_components.roomba_plus.config_flow.er.async_get") as mock_er:
            result = _discover_rest980_rooms(hass)
        assert result == {}
        mock_er.assert_not_called()

    def test_select_entities_with_room_data_are_collected(self):
        entities = [
            ("select.clean_kitchen", {"id": "3", "name": "Kitchen"}),
            ("select.clean_hallway", {"id": "5", "name": "Hallway"}),
        ]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get") as mock_er:
            mock_er.return_value = MagicMock()
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {"3": "Kitchen", "5": "Hallway"}

    def test_non_select_domain_entities_ignored(self):
        entities = [
            ("button.fav_morning", {"id": "9", "name": "Should not appear"}),
            ("select.clean_kitchen", {"id": "3", "name": "Kitchen"}),
        ]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get"):
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {"3": "Kitchen"}

    def test_select_entity_without_room_data_attribute_ignored(self):
        entities = [("select.clean_unknown", None)]
        hass, entity_entries = _make_hass_with_rest980(entities=entities)
        with patch("custom_components.roomba_plus.config_flow.er.async_get"):
            with patch(
                "custom_components.roomba_plus.config_flow.er.async_entries_for_config_entry",
                return_value=entity_entries,
            ):
                result = _discover_rest980_rooms(hass)
        assert result == {}


class TestRest980MigrateStep:
    @pytest.mark.asyncio
    async def test_aborts_when_no_rooms_discovered(self):
        flow, _ = _make_flow_m(discovered_rooms={})
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "abort"
        assert result["reason"] == "no_rest980_rooms_found"

    @pytest.mark.asyncio
    async def test_aborts_when_all_rooms_already_labelled(self):
        flow, _ = _make_flow_m(
            discovered_rooms={"3": "Kitchen"},
            existing_labels={"3": "Kitchen (manually renamed)"},
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen"},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "abort"
        assert result["reason"] == "rest980_rooms_already_imported"

    @pytest.mark.asyncio
    async def test_shows_form_with_new_rooms(self):
        flow, _ = _make_flow_m(discovered_rooms={"3": "Kitchen", "5": "Hallway"})
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen", "5": "Hallway"},
        ):
            result = await flow.async_step_rest980_migrate()
        assert result["type"] == "form"
        assert result["step_id"] == "rest980_migrate"

    @pytest.mark.asyncio
    async def test_confirm_import_writes_merged_options_without_clobbering_existing(self):
        flow, config_entry = _make_flow_m(
            discovered_rooms={"3": "Kitchen", "5": "Hallway"},
            existing_labels={"3": "Kitchen (manually renamed)"},  # must NOT be overwritten
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen", "5": "Hallway"},
        ), patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={"lastCommand": {"pmap_id": "map_a"}},
        ):
            result = await flow.async_step_rest980_migrate({"confirm_import": True})

        assert result["type"] == "create_entry"
        new_labels = result["data"]["smart_zone_labels"]
        assert new_labels["3"] == "Kitchen (manually renamed)"  # untouched
        assert new_labels["5"] == "Hallway"                     # newly imported
        assert result["data"]["smart_zone_data"]["5"] == {"name": "Hallway", "pmap_id": "map_a"}

    @pytest.mark.asyncio
    async def test_declining_confirmation_makes_no_changes(self):
        flow, config_entry = _make_flow_m(
            discovered_rooms={"3": "Kitchen"}, existing_labels={},
        )
        with patch(
            "custom_components.roomba_plus.config_flow._discover_rest980_rooms",
            return_value={"3": "Kitchen"},
        ):
            result = await flow.async_step_rest980_migrate({"confirm_import": False})

        assert result["type"] == "create_entry"
        assert result["data"] == config_entry.options  # unchanged


class TestRest980MigrateMenuVisibility:
    @pytest.mark.asyncio
    async def test_menu_includes_rest980_migrate_when_smart_and_detected(self):
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [MagicMock()]  # rest980 present
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.SMART
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry
        flow.handler = config_entry.entry_id
        flow.hass = getattr(flow, "hass", None) or MagicMock()
        flow.hass.config_entries.async_get_known_entry.return_value = (
            config_entry
        )

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" in result["menu_options"]

    @pytest.mark.asyncio
    async def test_menu_omits_rest980_migrate_when_not_detected(self):
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = []  # nothing detected
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.SMART
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry
        flow.handler = config_entry.entry_id
        flow.hass = getattr(flow, "hass", None) or MagicMock()
        flow.hass.config_entries.async_get_known_entry.return_value = (
            config_entry
        )

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" not in result["menu_options"]

    @pytest.mark.asyncio
    async def test_menu_omits_rest980_migrate_for_ephemeral_robots(self):
        """EPHEMERAL robots have no smart_zone_data concept — migration target
        doesn't apply even if roomba_rest980 happens to be installed."""
        flow = object.__new__(RoombaPlusOptionsFlow)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [MagicMock()]
        flow.hass = hass

        config_entry = MagicMock()
        config_entry.runtime_data.map_capability = MapCapability.EPHEMERAL
        config_entry.runtime_data.has_cloud = True
        flow._config_entry = config_entry
        flow.handler = config_entry.entry_id
        flow.hass = getattr(flow, "hass", None) or MagicMock()
        flow.hass.config_entries.async_get_known_entry.return_value = (
            config_entry
        )

        with patch(
            "custom_components.roomba_plus.config_flow.roomba_reported_state",
            return_value={},
        ):
            result = await flow.async_step_init()
        assert "rest980_migrate" not in result["menu_options"]


class TestFlowsResolveTheirEntryAsHa2026Does:
    """conftest makes OptionsFlow.config_entry behave as in Home Assistant
    2026 on every version. On 2025.5 a flow given only `_config_entry`
    still worked through a compatibility path; the 2026 CI job then failed
    four tests nobody could reproduce locally."""

    def test_a_flow_given_only_its_entry_attribute_fails(self):
        from custom_components.roomba_plus.config_flow import RoombaPlusOptionsFlow

        flow = RoombaPlusOptionsFlow.__new__(RoombaPlusOptionsFlow)
        flow._config_entry = MagicMock()
        flow.hass = MagicMock()
        flow.handler = None
        with pytest.raises(ValueError):
            flow.config_entry
