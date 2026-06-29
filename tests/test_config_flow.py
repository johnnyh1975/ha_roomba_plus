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
