"""Tests for button.py — currently just ZoneCleanButton (ROOM-SEG Stage 3).

No test_button.py existed before this; button.py had zero test coverage
across the project. Scoped here to the one class touched by the
ZoneStore -> RoomSegStore swap, not a full audit of every button class.
"""
from unittest.mock import MagicMock, AsyncMock

import pytest

from custom_components.roomba_plus.button import ZoneCleanButton
from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom


def _make_button(room_seg_store):
    entity = ZoneCleanButton.__new__(ZoneCleanButton)
    config_entry = MagicMock()
    config_entry.runtime_data.room_seg_store = room_seg_store
    config_entry.data = {"blid": "test_blid"}
    entity._config_entry = config_entry
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock()
    entity.vacuum = MagicMock()

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
            await entity.async_press()
        assert "no rooms available" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_room_seg_store_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            await entity.async_press()
        assert "no rooms available" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_confirmed_rooms_logs_warning_and_returns(self, caplog):
        rss = RoomSegStore()
        rss.rooms = {"room_1": SegRoom(id="room_1", name="", confirmed=False)}
        entity, _, _ = _make_button(rss)
        with caplog.at_level("WARNING"):
            await entity.async_press()
        assert "no confirmed rooms" in caplog.text.lower()
        entity.hass.async_add_executor_job.assert_not_called()


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

        entity.hass.async_add_executor_job.assert_called_once_with(
            entity.vacuum.send_command, "start"
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
        entity.hass.async_add_executor_job.assert_called_once()

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
