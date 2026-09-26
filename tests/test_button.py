"""Tests for button.py — currently just ZoneCleanButton (ROOM-SEG Stage 3).

No test_button.py existed before this; button.py had zero test coverage
across the project. Scoped here to the one class touched by the
ZoneStore -> RoomSegStore swap, not a full audit of every button class.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests.conftest import robot_mock

from custom_components.roomba_plus.button import FavoriteButton, ZoneCleanButton
from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom
from types import SimpleNamespace
from custom_components.roomba_plus import button as btn
from custom_components.roomba_plus import room_cleaning
from custom_components.roomba_plus.const import MAP_UPDATING_NOT_READY
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.roomba_plus import button_prime
from custom_components.roomba_plus.const import DOMAIN
from custom_components.roomba_plus.models import ConnectionType
import json
from pathlib import Path
from custom_components.roomba_plus.button import CleanBaseBagResetButton
from custom_components.roomba_plus.button import SideBrushResetButton
from custom_components.roomba_plus.button import _cloud_part_reset_buttons
from custom_components.roomba_plus.const import IROBOT_PART_ROLE_CLEAN_BASE_BAG
from custom_components.roomba_plus.const import IROBOT_PART_ROLE_SIDE_BRUSH
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


def _make_button(room_seg_store):
    entity = ZoneCleanButton.__new__(ZoneCleanButton)
    config_entry = MagicMock()
    config_entry.runtime_data.room_seg_store = room_seg_store
    config_entry.data = {"blid": "test_blid"}
    entity._config_entry = config_entry
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock()
    entity.vacuum = robot_mock()

    # No selection made in tests below -- entity_registry lookup returns
    # nothing, so async_press falls back to the first confirmed room.
    fake_ent_reg = MagicMock()
    fake_ent_reg.async_get_entity_id.return_value = None
    import custom_components.roomba_plus.button as button_mod
    return entity, fake_ent_reg, button_mod


class TestZoneCleanButtonNoRooms:
    @pytest.mark.asyncio
    async def test_no_room_seg_store_logs_warning_and_returns(self, caplog):
        entity, _, _ = _make_button(None)
        with caplog.at_level("WARNING"):
            with pytest.raises(ServiceValidationError) as exc_info:
                await entity.async_press()
            assert exc_info.value.translation_key == "no_confirmed_room"
        assert "no rooms available" in caplog.text.lower()
        entity.vacuum.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_room_seg_store_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            with pytest.raises(ServiceValidationError) as exc_info:
                await entity.async_press()
            assert exc_info.value.translation_key == "no_confirmed_room"
        assert "no rooms available" in caplog.text.lower()
        entity.vacuum.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_confirmed_rooms_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="", confirmed=False)}
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            with pytest.raises(ServiceValidationError) as exc_info:
                await entity.async_press()
            assert exc_info.value.translation_key == "no_confirmed_room"
        assert "no confirmed rooms" in caplog.text.lower()
        entity.vacuum.send_command.assert_not_awaited()


class TestZoneCleanButtonStartsClean:
    @pytest.mark.asyncio
    async def test_confirmed_room_present_sends_start_command(self, monkeypatch):
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(
                id="room_1", name="Kitchen", confirmed=True,
                cells={(0, 0), (1, 0), (0, 1), (1, 1)},
            ),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)

        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        await entity.async_press()

        entity.vacuum.send_command.assert_called_once_with("start"
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_first_confirmed_room_without_selection(self, monkeypatch):
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True),
            "room_2": SegRoom(id="room_2", name="Bedroom", confirmed=True),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)
        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        # Should not raise even with no selected-zone state available.
        await entity.async_press()
        entity.vacuum.send_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logs_room_bbox_not_zone_attribute_names(self, monkeypatch, caplog):
        """Regression check for the ROOM-SEG Stage 3 swap: the log
        message must read room.bbox (a SegRoom property), not the old
        zone.x_min/y_min/x_max/y_max ZoneStore.Zone attributes."""
        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(
                id="room_1", name="Kitchen", confirmed=True,
                cells={(0, 0), (1, 0), (0, 1), (1, 1)},
            ),
        }
        entity, fake_ent_reg, button_mod = _make_button(rss)
        monkeypatch.setattr(
            "homeassistant.helpers.entity_registry.async_get",
            lambda hass: fake_ent_reg,
        )

        with caplog.at_level("INFO"):
            await entity.async_press()

        assert "Kitchen" in caplog.text
        assert "bbox" in caplog.text.lower()


class TestMaintenanceResetButtonCurrentHrNullRegression:
    """v3.4.2 NULL-REGRESSION — bbrun: null must not crash _current_hr(),
    same confirmed-real bug class as elsewhere in this codebase."""

    def test_explicit_null_bbrun_returns_zero(self):
        from custom_components.roomba_plus.button import FilterResetButton
        btn = object.__new__(FilterResetButton)
        btn.vacuum_state = {"bbrun": None}
        assert btn._current_hr() == 0


class TestDockButtonAvailability:
    """The rules come from the app's own res/raw availability specs.

    Until now this class had no `available` at all: every dock button was
    pressable whenever the capability existed. @chairstacker pressed Wash
    Pad with a tank removed, the robot spoke a complaint and the dock
    reported 671 -- the app would not have offered the button, because
    pw_state was not 601.
    """

    def _button(self, key, *, dock=None, cycle=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.button_prime import (
            PRIME_DOCK_COMMANDS,
            PrimeDockButton,
        )

        command = next(c for c in PRIME_DOCK_COMMANDS if c.key == key)
        button = PrimeDockButton.__new__(PrimeDockButton)
        button._command = command
        button._config_entry = MagicMock()
        state = SimpleNamespace(
            dock=SimpleNamespace(**dock) if dock is not None else None,
            mission=SimpleNamespace(cycle=cycle),
        )
        with patch.object(
            PrimeDockButton, "_current_state", new_callable=PropertyMock
        ) as current, patch.object(
            type(button).__mro__[1], "available", new_callable=PropertyMock
        ) as parent:
            current.return_value = state
            parent.return_value = True
            return button.available

    def test_wash_pad_only_when_the_dock_says_601(self):
        base = {"error": None, "pd_state": None}
        assert self._button("prime_wash_pad", dock={"pw_state": 601, **base}) is True
        # 671 is the state @chairstacker's dock reported with a tank out.
        assert self._button("prime_wash_pad", dock={"pw_state": 671, **base}) is False
        assert self._button("prime_wash_pad", dock={"pw_state": 602, **base}) is False

    def test_start_and_stop_drying_have_opposite_rules(self):
        """The one control where the app's rule inverts: stopping is
        offered while drying RUNS, starting while it does not."""
        base = {"error": None, "pw_state": None}
        assert self._button("prime_start_pad_dry", dock={"pd_state": 701, **base}) is True
        assert self._button("prime_stop_pad_dry", dock={"pd_state": 701, **base}) is False
        assert self._button("prime_stop_pad_dry", dock={"pd_state": 702, **base}) is True
        assert self._button("prime_start_pad_dry", dock={"pd_state": 702, **base}) is False

    def test_empty_bin_follows_the_evac_states(self):
        base = {"error": None, "pw_state": None, "pd_state": None}
        assert self._button("prime_empty_bin", dock={"state": 301, **base}) is True
        assert self._button("prime_empty_bin", dock={"state": 355, **base}) is True
        # 351-354 are the evac faults: bag missing, clog, seal, bag full.
        assert self._button("prime_empty_bin", dock={"state": 353, **base}) is False

    def test_a_dock_error_blocks_every_control(self):
        for key in ("prime_wash_pad", "prime_empty_bin", "prime_start_pad_dry"):
            assert self._button(
                key, dock={"error": 505, "pw_state": 601, "pd_state": 701, "state": 301}
            ) is False, key

    def test_a_running_mission_blocks_every_control(self):
        """The dock will not wash, dry or empty while the robot is out."""
        dock = {"error": None, "pw_state": 601, "pd_state": 701, "state": 301}
        for cycle in ("clean", "spot", "dock"):
            assert self._button("prime_wash_pad", dock=dock, cycle=cycle) is False, cycle
        assert self._button("prime_wash_pad", dock=dock, cycle="none") is True

    def test_unknown_means_available(self):
        """Deliberate. Taking function away from a working robot because
        a field is missing is the worse mistake, and this project has
        made it before by gating on a capability flag instead of on a
        field being present."""
        assert self._button("prime_wash_pad", dock=None) is True
        assert self._button(
            "prime_wash_pad", dock={"error": None, "pw_state": None}
        ) is True

    def test_an_int_enum_compares_like_its_value(self):
        """DockState is an IntEnum on the model and a plain int on older
        payloads. Both have to work."""
        from enum import IntEnum

        class FakeState(IntEnum):
            PAD_WASH_OKAY = 601

        assert self._button(
            "prime_wash_pad", dock={"error": None, "pw_state": FakeState.PAD_WASH_OKAY}
        ) is True


class TestClassicFavouritesAreFilteredToo:
    """@scenicsystemsllc (#80) has **three Classic robots** and saw the
    same favourites on all of them, including "Vacuum Everywhere" on a
    mop-only Braava.

    `/user/favorites` is an ACCOUNT endpoint — it returns every
    favourite in the household. The Classic setup built a button for
    each one on every robot.

    #80 was fixed once, in the Prime path. This one was never touched,
    so a fix that read as complete covered neither of the two paths
    that create his entities.
    """

    def test_the_classic_builder_filters(self):
        import inspect

        from custom_components.roomba_plus import button

        source = inspect.getsource(button.async_setup_entry)

        assert "_raw_favorite_is_for" in source, (
            "the Classic favourite loop must filter by robot -- "
            "/user/favorites is account-wide"
        )

    def test_both_builders_use_the_same_check(self):
        """One rule, two platforms. Two separate implementations is how
        #80 came to be fixed in one place and not the other."""
        import inspect

        from custom_components.roomba_plus import button, button_prime

        classic = inspect.getsource(button.async_setup_entry)
        prime = inspect.getsource(button_prime.build_prime_favorite_buttons)

        assert "_raw_favorite_is_for" in classic
        assert "_raw_favorite_is_for" in prime


# ============================================================================
# FAVOURITE BUTTONS
#
# Moved here from test_sensors.py (August 2026). This file's own header
# says button.py "had zero test coverage" -- it did have some, filed
# under sensors, where nobody looked.
#
# `_make_config_entry` and `_make_roomba` are COPIED, not moved: tests
# still in test_sensors.py use them. Two copies of a fixture beats a
# cross-file import between test modules.
# ============================================================================


def _make_config_entry(has_cloud: bool = False, favorites=None, pmaps=None):
    """Return a minimal mock config entry."""
    entry = MagicMock()
    entry.unique_id = "test_blid"
    entry.options = {}
    entry.data = {"blid": "test_blid"}

    cc = MagicMock()
    cc.data = {
        "pmaps": pmaps or [],
        "favorites": favorites or [],
        "mission_history": {},
    }
    cc.active_pmap_id = (pmaps[0].get("active_pmapv_details", {}).get("active_pmapv", {}).get("pmap_id") if pmaps else None)
    runtime = MagicMock()
    runtime.has_cloud = has_cloud
    runtime.cloud_coordinator = cc if has_cloud else None
    entry.runtime_data = runtime
    return entry


def _make_roomba():
    r = robot_mock()
    r.master_state = {"state": {"reported": {}}}
    return r


# The six CloudSmartZoneSelect classes that stood here moved to
# test_select.py (August 2026) -- they tested `select.py` from a file
# named for sensors. `_make_config_entry` and `_make_roomba` above stay:
# tests in this file still use them.



def _fav_button(favorite):
    entry = _make_config_entry(has_cloud=True)
    roomba = _make_roomba()
    return FavoriteButton(roomba, "test_blid", entry, favorite)


class TestFavoriteButton:
    def test_name_from_favorite(self):
        btn = _fav_button({"favorite_id": "f1", "name": "Morning clean", "commanddefs": []})
        assert btn._attr_name == "Morning clean"

    def test_unique_id_includes_favorite_id(self):
        btn = _fav_button({"favorite_id": "fav42", "name": "X", "commanddefs": []})
        assert "fav42" in btn._attr_unique_id

    def test_visible_by_default_when_not_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": False})
        assert btn._attr_entity_registry_enabled_default is True

    def test_disabled_by_default_when_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": True})
        assert btn._attr_entity_registry_enabled_default is False

    def test_default_enabled_when_hidden_missing(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert btn._attr_entity_registry_enabled_default is True

    def test_inherits_irobot_entity(self):
        """FavoriteButton must inherit IRobotEntity for correct device linkage."""
        from custom_components.roomba_plus.entity import IRobotEntity
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert isinstance(btn, IRobotEntity)

    def test_has_vacuum_attribute(self):
        """IRobotEntity provides .vacuum — used in async_press instead of config_entry."""
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert hasattr(btn, "vacuum")

    @pytest.mark.asyncio
    async def test_press_sends_command(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f1",
            "name": "Morning",
            "commanddefs": [{"command": "start", "pmap_id": "map1", "regions": [{"region_id": "3"}]}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        # ASKS THE ROBOT: the command goes straight to `send_command`
        # since roombapy 2.x, so there is no executor call to inspect
        # and no bound method in position zero any more.
        btn.vacuum.send_command.assert_awaited_once()
        command, params = btn.vacuum.send_command.await_args[0]
        assert command == "start"
        assert params["pmap_id"] == "map1"

    @pytest.mark.asyncio
    async def test_press_no_commanddefs_logs_warning(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "Empty", "commanddefs": []}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with patch("custom_components.roomba_plus.button._LOGGER") as mock_log:
            with pytest.raises(HomeAssistantError) as exc_info:
                await btn.async_press()
            assert exc_info.value.translation_key == "favorite_has_no_commands"

        mock_log.warning.assert_called_once()
        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_missing_commanddefs_key(self):
        """commanddefs key absent — should not raise, should warn."""
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "NoCmd"}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value.translation_key == "favorite_has_no_commands"

        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_multi_entry_commanddefs_logs_warning_and_uses_first(self):
        """Multi-entry commanddefs: warn about the dropped entries, still send entry 0."""
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f3",
            "name": "Multi",
            "commanddefs": [
                {"command": "start", "pmap_id": "map1", "regions": [{"region_id": "1"}]},
                {"command": "start", "pmap_id": "map1", "regions": [{"region_id": "2"}]},
            ],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with patch("custom_components.roomba_plus.button._LOGGER") as mock_log:
            await btn.async_press()

        mock_log.warning.assert_called_once()
        _command, params = btn.vacuum.send_command.await_args[0]
        assert params["regions"] == [{"region_id": "1"}]

    @pytest.mark.asyncio
    async def test_press_single_entry_commanddefs_no_warning(self):
        """Single-entry commanddefs: no warning should fire."""
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f4",
            "name": "Single",
            "commanddefs": [{"command": "start", "pmap_id": "map1"}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with patch("custom_components.roomba_plus.button._LOGGER") as mock_log:
            await btn.async_press()

        mock_log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_extracts_params_excluding_command_key(self):
        """All keys except 'command' become params."""
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f2",
            "name": "Kitchen",
            "commanddefs": [{"command": "start", "pmap_id": "p1", "ordered": 1}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        params = btn.vacuum.send_command.await_args[0][1]
        assert "command" not in params
        assert params["pmap_id"] == "p1"
        assert params["ordered"] == 1


# ── select.py: cloud vs MQTT routing in async_setup_entry ─────────────────────


# ── formerly tests/test_coverage_button.py ──────────────────────────────────────
#
# button.py — quality scale, test-coverage.
#
# The "clean zone" button finds the chosen zone among this robot's zone
# pickers, completes the map from the zone data or the last command, and
# starts. Whenever a piece is missing — no zone, the map mid-update, the
# zone's map gone after a retrain — it sends nothing and says why. Until
# 4.2.11 these returned silently: a press that did nothing, with no reason.

def _zone_button(monkeypatch, *, pickers=(), state=None, options=None, pmapv="v7"):
    from homeassistant.helpers import entity_platform as ep

    b = btn.SmartZoneButton.__new__(btn.SmartZoneButton)
    b.hass = MagicMock()
    b._blid = "B"
    b._config_entry = MagicMock()
    b._config_entry.options = options or {}
    b.vacuum = MagicMock(send_command=AsyncMock())
    b.vacuum_state = state or {}
    monkeypatch.setattr(btn.SmartZoneButton, "robot_unique_id", property(lambda _s: "roomba_B"))
    monkeypatch.setattr(ep, "async_get_platforms",
                        lambda _h, _d: [SimpleNamespace(entities={str(i): p for i, p in enumerate(pickers)})])
    monkeypatch.setattr(room_cleaning, "_resolve_pmapv_id", lambda _s, _p: pmapv)
    return b


def _picker(region, pmap=None, uid="roomba_B_smart_zone_select"):
    return SimpleNamespace(unique_id=uid, selected_region_id=region,
                           selected_pmap_info={"pmap_id": pmap} if pmap else {})


def _sent(b):
    cmd, params = b.vacuum.send_command.await_args.args
    assert cmd == "start"
    return params


class TestCleanZoneButton:

    @pytest.mark.asyncio
    async def test_the_chosen_zone_starts_on_its_map_with_the_robots_pass_setting(self, monkeypatch):
        b = _zone_button(monkeypatch, pickers=[_picker("3", "p1")], state={"noAutoPasses": True, "twoPass": True})
        await b.async_press()
        p = _sent(b)
        assert p["pmap_id"] == "p1"
        # NOT pinned: whether user_pmapv_id is sent. This button omits it for
        # a `rid` command, citing a clean_room comment that no longer exists;
        # room_cleaning.py sends it for rooms (field rule v2.7.0: omit only
        # for pure zone commands, error 224). Open question, needs a robot.
        assert p["regions"][0]["region_id"] == "3"
        assert p["regions"][0]["params"] == {"noAutoPasses": True, "twoPass": True}

    @pytest.mark.asyncio
    async def test_another_robots_picker_is_ignored(self, monkeypatch):
        b = _zone_button(monkeypatch, pickers=[_picker("9", "p9", uid="roomba_OTHER_select")],
                         state={"lastCommand": {"pmap_id": "p1", "regions": [{"region_id": "3"}]}})
        await b.async_press()
        assert _sent(b)["regions"][0]["region_id"] == "3"

    @pytest.mark.asyncio
    async def test_the_map_comes_from_the_zone_data_when_the_picker_has_none(self, monkeypatch):
        b = _zone_button(monkeypatch, pickers=[_picker("3")],
                         options={"smart_zone_data": {"3": {"pmap_id": "p2"}}})
        await b.async_press()
        assert _sent(b)["pmap_id"] == "p2"

    @pytest.mark.asyncio
    async def test_the_last_command_fills_what_is_missing(self, monkeypatch):
        b = _zone_button(monkeypatch, state={"lastCommand": {"pmap_id": "p1", "regions": [{"region_id": "5"}]}})
        await b.async_press()
        p = _sent(b)
        assert (p["pmap_id"], p["regions"][0]["region_id"]) == ("p1", "5")

    @pytest.mark.parametrize("state,pmapv,key", [
        ({}, "v7", "no_zone_selected"),                                     # nothing to go on
        ({"lastCommand": {"pmap_id": "p1", "regions": [{"region_id": "5"}]},
          "cleanMissionStatus": {"notReady": MAP_UPDATING_NOT_READY}}, "v7", "map_updating"),
    ], ids=["no_zone", "map_updating"])
    @pytest.mark.asyncio
    async def test_a_missing_piece_is_refused_with_a_reason_and_sends_nothing(self, monkeypatch, state, pmapv, key):
        """Until 4.2.11 these returned silently: the user pressed, nothing
        happened, nothing said why."""
        b = _zone_button(monkeypatch, state=state, pmapv=pmapv)
        with pytest.raises(ServiceValidationError) as exc_info:
            await b.async_press()
        assert exc_info.value.translation_key == key
        b.vacuum.send_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unresolvable_map_version_does_not_stop_the_press(self, monkeypatch):
        """lewis firmware reports no `pmaps` locally, so the version cannot
        be resolved when the last command named another map. The button
        refused with "the map no longer exists" about a map that did. The
        version is not sent anyway; the press goes out without it."""
        b = _zone_button(monkeypatch, pickers=[_picker("3", "p2")], pmapv=None,
                         state={"lastCommand": {"pmap_id": "p1", "regions": [{"region_id": "5"}]}})
        await b.async_press()
        p = _sent(b)
        assert p["pmap_id"] == "p2" and p["regions"][0]["region_id"] == "3"
        assert "user_pmapv_id" not in p


class TestCloudPartReset:

    def _b(self, *, record, result=None, error=None):
        b = btn._CloudPartResetButton.__new__(btn._CloudPartResetButton)
        b._cloud_role = next(iter(btn.IROBOT_PART_ROLE_TO_STORE_SLOT))
        slot = btn.IROBOT_PART_ROLE_TO_STORE_SLOT[b._cloud_role]
        store = MagicMock()
        store.cloud_part_by_role.return_value = record
        cc = MagicMock()
        cc.api.set_robot_part_counter = AsyncMock(side_effect=error, return_value=result or {})
        cc.async_request_refresh = AsyncMock()
        b._config_entry = MagicMock()
        b._config_entry.runtime_data.cloud_coordinator = cc
        b._config_entry.runtime_data.blid = "B"
        b._maintenance_store = lambda: store
        b._current_hr = lambda: 321
        b._save = AsyncMock()
        return b, store, cc, slot

    @pytest.mark.asyncio
    async def test_a_confirmed_cloud_reset_resets_the_local_counter_too(self):
        b, store, cc, slot = self._b(record={"part_id": "p7"}, result={"num_parts": 1})
        await b.async_press()
        cc.api.set_robot_part_counter.assert_awaited_once_with("B", "p7", 0)
        getattr(store, f"reset_{slot}").assert_called_once_with(321)
        b._save.assert_awaited_once()
        cc.async_request_refresh.assert_awaited_once()

    @pytest.mark.parametrize("kw,key", [
        ({"record": None}, "cloud_part_unknown"),                                   # cloud knows no such part
        ({"record": {"part_id": ""}}, "cloud_part_unknown"),                        # part without an id
        ({"record": {"part_id": "p7"}, "error": RuntimeError()}, "cloud_part_reset_failed"),
        ({"record": {"part_id": "p7"}, "result": {"num_parts": 0}}, "cloud_part_reset_not_confirmed"),
    ], ids=["no_part", "no_id", "cloud_error", "not_confirmed"])
    @pytest.mark.asyncio
    async def test_without_confirmation_the_local_counter_is_left(self, kw, key):
        b, store, _cc, slot = self._b(**kw)
        with pytest.raises(HomeAssistantError) as exc_info:
            await b.async_press()
        assert exc_info.value.translation_key == key
        getattr(store, f"reset_{slot}").assert_not_called()
        b._save.assert_not_awaited()


class TestRepeatLastMission:

    def _b(self, state, monkeypatch, fresh="v9"):
        b = btn.RepeatLastMissionButton.__new__(btn.RepeatLastMissionButton)
        b.vacuum = MagicMock(send_command=AsyncMock())
        b.vacuum_state = state
        b._config_entry = None
        monkeypatch.setattr(room_cleaning, "resolve_user_pmapv_id", lambda _s, _c, _p: fresh)
        return b

    @pytest.mark.asyncio
    async def test_the_last_command_is_repeated_with_a_fresh_map_version(self, monkeypatch):
        last = {"command": "start", "pmap_id": "p1", "user_pmapv_id": "v1",
                "regions": [{"region_id": "3"}], "ordered": 1, "robot_id": "ignored"}
        b = self._b({"lastCommand": last}, monkeypatch)
        await b.async_press()
        cmd, params = b.vacuum.send_command.await_args.args
        assert cmd == "start" and params["user_pmapv_id"] == "v9"
        assert "robot_id" not in params

    @pytest.mark.asyncio
    async def test_no_last_command_is_refused_and_sends_nothing(self, monkeypatch):
        b = self._b({}, monkeypatch)
        with pytest.raises(ServiceValidationError) as exc_info:
            await b.async_press()
        assert exc_info.value.translation_key == "no_last_mission"
        b.vacuum.send_command.assert_not_awaited()


class TestClassicButtonSetup:

    @pytest.mark.asyncio
    async def test_buttons_follow_the_state_and_favourites_only_this_robots(self, hass, monkeypatch):
        state = {"lastCommand": {"command": "start"}, "cap": {"pmaps": 1}, "pmaps": [{"p1": "v1"}]}
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        entry = MagicMock()
        entry.options = {}
        data = entry.runtime_data
        data.connection_type = ConnectionType.LOCAL_PUSH
        data.roomba, data.blid, data.has_cloud = roomba, "B", True
        data.cloud_coordinator.data = {"favorites": [
            {"favorite_id": "f1", "name": "Mine", "commanddefs": []},
            {"favorite_id": "f2", "name": "Other robot's", "commanddefs": []}]}
        monkeypatch.setattr(button_prime, "_raw_favorite_is_for", lambda fav, _b: fav["favorite_id"] == "f1")
        monkeypatch.setattr(btn, "has_smart_map", lambda _s: True, raising=False)
        from custom_components.roomba_plus import const, repairs

        monkeypatch.setattr(const, "has_smart_map", lambda _s: True)
        monkeypatch.setattr(repairs, "async_check_favorite_multi_command", AsyncMock())
        added = []
        await btn.async_setup_entry(hass, entry, lambda ents, *a, **k: added.extend(ents))
        await hass.async_block_till_done()
        names = [type(e).__name__ for e in added]
        assert "RepeatLastMissionButton" in names and "SmartZoneButton" in names
        assert names.count("FavoriteButton") == 1


class TestPrimeFavouriteSync:

    async def _setup(self, hass, monkeypatch, wanted_ids):
        entry = MockConfigEntry(domain=DOMAIN, data={"blid": "PB"})
        entry.add_to_hass(hass)
        coordinator = MagicMock(last_update_success=True)
        listeners = []
        coordinator.async_add_listener = lambda cb: listeners.append(cb) or (lambda: None)
        entry.runtime_data = SimpleNamespace(connection_type=ConnectionType.CLOUD_ONLY, blid="PB",
                                             prime_schedule_coordinator=coordinator)
        monkeypatch.setattr(button_prime, "async_build_prime_buttons", AsyncMock(return_value=[]))
        monkeypatch.setattr(button_prime, "build_prime_dock_buttons", lambda _e: [])
        from custom_components.roomba_plus import prime_coordinator

        monkeypatch.setattr(prime_coordinator, "add_prime_entities_when_available", lambda *a, **k: None)
        wanted = {"ids": list(wanted_ids)}
        monkeypatch.setattr(button_prime, "build_prime_favorite_buttons",
                            lambda _e: [SimpleNamespace(unique_id=f"roomba_plus_PB_favorite_{i}") for i in wanted["ids"]])
        monkeypatch.setattr(btn, "async_remove_stale_entities", lambda *a, **k: 0)
        added = []
        await btn.async_setup_entry(hass, entry, lambda ents, *a, **k: added.extend(ents))
        return entry, coordinator, listeners, wanted, added

    @pytest.mark.asyncio
    async def test_new_favourites_appear_and_vanished_ones_leave_the_registry(self, hass, monkeypatch):
        entry, coordinator, listeners, wanted, added = await self._setup(hass, monkeypatch, ["a"])
        reg = er.async_get(hass)
        stale = reg.async_get_or_create("button", DOMAIN, "roomba_plus_PB_favorite_old",
                                        config_entry=entry).entity_id
        wanted["ids"] = ["a", "b"]
        for cb in listeners:
            cb()
        assert {e.unique_id for e in added} >= {"roomba_plus_PB_favorite_a", "roomba_plus_PB_favorite_b"}
        assert reg.async_get(stale) is None

    @pytest.mark.asyncio
    async def test_after_a_failed_refresh_nothing_is_removed(self, hass, monkeypatch):
        """A failed cloud read returns no favourites; removing on that basis
        would delete every favourite button the user has."""
        entry, coordinator, listeners, wanted, _added = await self._setup(hass, monkeypatch, ["a"])
        reg = er.async_get(hass)
        kept = reg.async_get_or_create("button", DOMAIN, "roomba_plus_PB_favorite_old",
                                       config_entry=entry).entity_id
        coordinator.last_update_success = False
        wanted["ids"] = []
        for cb in listeners:
            cb()
        assert reg.async_get(kept) is not None


class TestMaintenanceResetButtons:

    @pytest.mark.asyncio
    async def test_the_pad_reset_records_the_runtime_and_fires_the_event(self, monkeypatch):
        from custom_components.roomba_plus import services

        fired = []
        monkeypatch.setattr(services, "_fire_maintenance_reset_event",
                            lambda _h, _e, part, hr: fired.append((part, hr)))
        b = btn.PadResetButton.__new__(btn.PadResetButton)
        store = MagicMock()
        b._maintenance_store = lambda: store
        b._current_hr = lambda: 222
        b._save = AsyncMock()
        b.hass, b._config_entry = MagicMock(), MagicMock()
        await b.async_press()
        store.reset_pad.assert_called_once_with(222)
        assert fired == [("pad", 222)]

    @pytest.mark.asyncio
    async def test_a_pad_reset_without_a_store_does_nothing(self):
        b = btn.PadResetButton.__new__(btn.PadResetButton)
        b._maintenance_store = lambda: None
        b._current_hr = lambda: 222
        b._save = AsyncMock()
        await b.async_press()
        b._save.assert_not_awaited()

    @pytest.mark.parametrize("store", [None, "store"])
    @pytest.mark.asyncio
    async def test_a_cleaning_task_reset(self, store):
        b = btn._CleaningTaskResetButton.__new__(btn._CleaningTaskResetButton)
        real = MagicMock() if store else None
        b._maintenance_store = lambda: real
        b._task = "bin"
        b._save = AsyncMock()
        b.entity_id = "button.robbie_bin_cleaned"
        if not store:
            with pytest.raises(HomeAssistantError) as exc_info:
                await b.async_press()
            assert exc_info.value.translation_key == "maintenance_store_unavailable"
        else:
            await b.async_press()
        if store:
            real.reset_bin_cleaning.assert_called_once()
            b._save.assert_awaited_once()
        else:
            b._save.assert_not_awaited()


class TestLearnedRoomButton:

    @pytest.mark.parametrize("rooms", [None, {}, {"r1": SimpleNamespace(confirmed=False)}])
    @pytest.mark.asyncio
    async def test_without_a_confirmed_room_nothing_happens(self, rooms):
        b = btn.ZoneCleanButton.__new__(btn.ZoneCleanButton)
        b._config_entry = MagicMock()
        b._config_entry.runtime_data.room_seg_store = None if rooms is None else SimpleNamespace(rooms=rooms)
        b.vacuum = MagicMock(send_command=AsyncMock())
        with pytest.raises(ServiceValidationError) as exc_info:
            await b.async_press()
        assert exc_info.value.translation_key == "no_confirmed_room"
        b.vacuum.send_command.assert_not_awaited()


class TestResetButtonsRefreshTheSensors:

    @pytest.mark.asyncio
    async def test_saving_signals_every_entity_of_the_robot(self, monkeypatch):
        """_save used to refresh only the button itself, so the sensors kept
        their old value until the next robot message."""
        from custom_components.roomba_plus.const import maintenance_changed_signal

        sent = []
        monkeypatch.setattr(btn, "async_dispatcher_send", lambda _h, signal: sent.append(signal))
        b = btn.FilterResetButton.__new__(btn.FilterResetButton)
        b._blid = "B"
        b.hass = MagicMock()
        b._config_entry = MagicMock()
        b._maintenance_store = lambda: MagicMock(async_save=AsyncMock())
        await b._save()
        assert sent == [maintenance_changed_signal("B")]


# ── formerly tests/test_button_cloud_parts.py ─────────────────────────
#
# Reset buttons for consumables that exist only as an iRobot cloud counter.
#
# The filter and main brushes have local MaintenanceStore slots; the edge
# brush and dust bag do not, so the cloud counter IS their state and these
# buttons write it directly. They are created only when the account actually
# reports the part, so a robot without a Clean Base gets no bag button
# rather than one that silently does nothing.
#
# Part payloads are a real capture from an i3+ — see
# tests/fixtures/irobot_parts_i3plus.json.

FIXTURES = Path(__file__).parent / "fixtures"


def _parts(exclude: set[str] | None = None) -> list[dict]:
    with open(FIXTURES / "irobot_parts_i3plus.json", encoding="utf-8") as f:
        parts = json.load(f)["parts"]
    return [p for p in parts if p["part_id"] not in (exclude or set())]


def _store(parts: list[dict] | None = None) -> MaintenanceStore:
    store = MaintenanceStore()
    store.hydrate_from_cloud_parts(_parts() if parts is None else parts, 300)
    store.async_save = AsyncMock()
    return store


def _entry(store: MaintenanceStore | None, *, cloud: bool = True) -> MagicMock:
    entry = MagicMock()
    data = entry.runtime_data
    data.blid = "BLID1"
    data.maintenance_store = store
    if cloud:
        data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            return_value={"num_parts": 1, "parts": [{"part_id": "36", "counter": 0}]}
        )
        data.cloud_coordinator.async_request_refresh = AsyncMock()
    else:
        data.cloud_coordinator = None
    return entry


def _robot(*, braava: bool = False, clean_base: bool = True) -> MagicMock:
    """A robot whose REPORTED STATE answers the capability questions.

    `_cloud_part_reset_buttons` now falls back to local capability when
    the cloud has no part record — a bare MagicMock would answer "yes"
    to every `.get()` and make that fallback untestable.
    """
    robot = MagicMock()
    state: dict = {"sku": "m612000" if braava else "i755840"}
    if clean_base:
        state["dock"] = {"fwVer": "1.0"}
    robot.master_state = {"state": {"reported": state}}
    return robot


def _button(cls, entry: MagicMock):
    button = cls.__new__(cls)
    button._config_entry = entry
    button._blid = "BLID1"          # set by the real constructor
    button.hass = MagicMock()
    button.vacuum = MagicMock()
    button.vacuum_state = {"bbrun": {"hr": 300}}
    button.schedule_update_ha_state = MagicMock()
    button._maintenance_store = lambda: entry.runtime_data.maintenance_store
    return button


class TestButtonCreationIsGatedOnTheReportedPart:
    def test_both_buttons_for_a_robot_reporting_both_parts(self):
        buttons = _cloud_part_reset_buttons(_robot(), "BLID1", _entry(_store()))
        assert {type(b).__name__ for b in buttons} == {
            "SideBrushResetButton",
            "CleanBaseBagResetButton",
        }

    def test_no_bag_button_when_the_robot_has_no_clean_base(self):
        """A dockless robot must not get a bag button."""
        store = _store(_parts(exclude={"139"}))
        buttons = _cloud_part_reset_buttons(
            _robot(clean_base=False), "BLID1", _entry(store)
        )
        assert [type(b).__name__ for b in buttons] == ["SideBrushResetButton"]

    def test_no_side_brush_button_when_that_part_is_absent(self):
        store = _store(_parts(exclude={"36"}))
        buttons = _cloud_part_reset_buttons(
            _robot(braava=True), "BLID1", _entry(store)
        )
        assert [type(b).__name__ for b in buttons] == ["CleanBaseBagResetButton"]

    def test_local_capability_carries_the_gate_without_cloud(self):
        """Classic robots never get consumable_parts, even with a working
        cloud connection. Gating on a cloud record meant they could never
        reset these two counters at all — so the gate falls back to what
        the reported state says the robot has."""
        buttons = _cloud_part_reset_buttons(
            _robot(), "BLID1", _entry(_store(), cloud=False)
        )
        assert {type(b).__name__ for b in buttons} == {
            "SideBrushResetButton",
            "CleanBaseBagResetButton",
        }

    def test_a_braava_still_gets_no_side_brush_button(self):
        """The gate got a fallback, not a hole."""
        buttons = _cloud_part_reset_buttons(
            _robot(braava=True, clean_base=False),
            "BLID1",
            _entry(MaintenanceStore(), cloud=False),
        )
        assert buttons == []

    def test_no_buttons_without_a_maintenance_store(self):
        assert _cloud_part_reset_buttons(_robot(), "BLID1", _entry(None)) == []

    def test_unhydrated_cloud_falls_back_to_local_capability(self):
        """Before the parts endpoint answers — or forever, on Classic —
        the buttons still appear for a robot that has the parts. Their
        press writes the local store, which is what the countdown reads
        when there is no cloud counter."""
        buttons = _cloud_part_reset_buttons(
            _robot(), "BLID1", _entry(MaintenanceStore())
        )
        assert {type(b).__name__ for b in buttons} == {
            "SideBrushResetButton",
            "CleanBaseBagResetButton",
        }


class TestUniqueIdsAndRoles:
    def test_side_brush_identity(self):
        button = _button(SideBrushResetButton, _entry(_store()))
        assert button._attr_translation_key == "reset_side_brush"

    def test_bag_identity(self):
        button = _button(CleanBaseBagResetButton, _entry(_store()))
        assert button._attr_translation_key == "reset_clean_base_bag"

    def test_neither_carries_a_cloud_role_any_more(self):
        """`_cloud_role` belonged to `_CloudPartResetButton`, which put
        the cloud write first and returned before touching the local
        store if it failed. Both buttons now derive from
        `_MaintenanceResetButton`: local write, then a best-effort cloud
        report — the order FilterResetButton has always used."""
        for cls in (SideBrushResetButton, CleanBaseBagResetButton):
            assert not hasattr(cls, "_cloud_role")


class TestPressWritesTheCloudCounter:
    @pytest.mark.asyncio
    async def test_side_brush_press_zeroes_its_own_part_id(self):
        """counter 0 is "this part is new" — the same write the official
        app performs when you confirm a replacement."""
        entry = _entry(_store())
        await _button(SideBrushResetButton, entry).async_press()

        api = entry.runtime_data.cloud_coordinator.api
        api.set_robot_part_counter.assert_awaited_once_with("BLID1", "36", 0)
        entry.runtime_data.cloud_coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bag_press_zeroes_its_own_part_id(self):
        entry = _entry(_store())
        await _button(CleanBaseBagResetButton, entry).async_press()
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter.assert_awaited_once_with(
            "BLID1", "139", 0
        )

    @pytest.mark.asyncio
    async def test_press_refreshes_so_the_sensor_updates_immediately(self, monkeypatch):
        """Every entity of the robot re-renders -- not just this button, which
        is what schedule_update_ha_state() used to refresh."""
        from custom_components.roomba_plus import button as button_mod
        from custom_components.roomba_plus.const import maintenance_changed_signal

        sent = []
        monkeypatch.setattr(button_mod, "async_dispatcher_send", lambda _h, signal, *a: sent.append(signal))
        entry = _entry(_store())
        button = _button(SideBrushResetButton, entry)
        await button.async_press()
        assert sent == [maintenance_changed_signal("BLID1")]

    @pytest.mark.asyncio
    async def test_side_brush_press_also_writes_local_store_state(self):
        """A successful press must leave a local baseline behind too, so
        wear-rate/days-until-due survive a later cloud outage instead of
        depending on the next successful poll to re-hydrate them."""
        entry = _entry(_store())
        store = entry.runtime_data.maintenance_store
        await _button(SideBrushResetButton, entry).async_press()

        assert store.side_brush_reset_hr == 300
        assert store.side_brush_reset_at is not None
        store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bag_press_also_writes_local_store_state(self):
        entry = _entry(_store())
        store = entry.runtime_data.maintenance_store
        await _button(CleanBaseBagResetButton, entry).async_press()

        assert store.clean_base_bag_reset_hr == 300
        assert store.clean_base_bag_reset_at is not None
        store.async_save.assert_awaited_once()


class TestPressFailureHandling:
    """A cloud outage must not cost the user their local reset.

    The old contract was the reverse: `_CloudPartResetButton` recorded
    the replacement with iRobot FIRST and returned before touching the
    local store if that failed, warning "could not record replacement".
    That made the whole feature unavailable to Classic robots, which
    never receive consumable_parts at all.

    Both buttons now follow FilterResetButton: write locally, save, then
    report to the cloud best-effort. `_async_push_part_reset_to_cloud`
    catches its own exceptions, so a press never raises either way.
    """

    @pytest.mark.asyncio
    async def test_cloud_outage_still_records_the_reset_locally(self):
        entry = _entry(_store())
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            side_effect=RuntimeError("cloud down")
        )
        store = entry.runtime_data.maintenance_store

        await _button(SideBrushResetButton, entry).async_press()

        assert store.side_brush_reset_hr == 300
        assert store.side_brush_reset_at is not None
        store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_press_never_raises_when_the_cloud_is_down(self):
        """A button press is a user action; an outage must not surface as
        a traceback."""
        entry = _entry(_store())
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            side_effect=RuntimeError("cloud down")
        )
        await _button(CleanBaseBagResetButton, entry).async_press()
