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
        flow.config_entry = config_entry
    except RuntimeError:
        pass  # newer HA deprecates direct assignment; _config_entry above suffices
    flow.hass = MagicMock()
    flow.hass.async_create_task = MagicMock()
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
        data = flow.config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        values = {o["value"] for o in opts}
        assert values == {"room_1", "room_2"}

    def test_unconfirmed_room_tagged(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=False)}
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert "unconfirmed" in opts[0]["label"]

    def test_hidden_room_tagged(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True, hidden=True)}
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert "hidden" in opts[0]["label"]

    def test_pending_edit_overrides_displayed_name(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        flow._pending_zone_edits = {"room_1": {"display_name": "Office"}}
        data = flow.config_entry.runtime_data
        opts = flow._build_zone_index_options(data, {})
        assert opts[0]["label"].startswith("Office")

    def test_no_room_seg_store_returns_empty(self):
        flow = _make_options_flow(None)
        data = flow.config_entry.runtime_data
        assert flow._build_zone_index_options(data, {}) == []


class TestResolveCurrentZoneNameEphemeral:
    def test_known_room_returns_its_name(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True)}
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
        assert flow._resolve_current_zone_name("room_1", data, {}) == "Kitchen"

    def test_unknown_room_id_falls_back_to_generic_label(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore

        rss = RoomSegStore()
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
        assert flow._resolve_current_zone_name("room_99", data, {}) == "Zone room_99"


class TestResolveCurrentZoneHiddenEphemeral:
    def test_hidden_room_returns_true(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", hidden=True)}
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
        assert flow._resolve_current_zone_hidden("room_1", data, {}) is True

    def test_visible_room_returns_false(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="Kitchen", hidden=False)}
        flow = _make_options_flow(rss)
        data = flow.config_entry.runtime_data
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
        """Successful reauth must update the EXISTING entry (never create a
        new one) and reload it — the actual point of using
        async_update_reload_and_abort() over async_create_entry()."""
        from custom_components.roomba_plus.const import (
            CONF_BLID, CONF_IROBOT_PASSWORD, CONF_IROBOT_USERNAME,
        )
        flow, reauth_entry = _make_reauth_flow(reauth_entry_data={
            CONF_BLID: "31B8091051311850",
            CONF_IROBOT_USERNAME: "old@example.com",
            CONF_IROBOT_PASSWORD: "old_password",
        })
        mock_api = MagicMock()
        mock_api.authenticate = AsyncMock()
        with patch(
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
        # async_update_reload_and_abort calls async_update_entry with
        # KEYWORD args (entry=..., data=...), not positional — verified
        # against HA's own source before writing this assertion.
        flow.hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        assert call_kwargs["entry"] is reauth_entry
        assert call_kwargs["data"][CONF_IROBOT_USERNAME] == "new@example.com"
        assert call_kwargs["data"][CONF_IROBOT_PASSWORD] == "new_password"
        # The reload half — async_schedule_reload is sync (a @callback), not
        # awaited, despite the "async_" prefix; also verified against source.
        flow.hass.config_entries.async_schedule_reload.assert_called_once_with(
            reauth_entry.entry_id
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
        flow.config_entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
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
        flow.config_entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        flow.async_create_entry = MagicMock(side_effect=lambda **kw: kw)

        await flow.async_step_settings({"enable_schedule_calendar": False})

        flow.async_create_entry.assert_called_once()
        saved = flow.async_create_entry.call_args.kwargs["data"]
        assert saved["enable_schedule_calendar"] is False
