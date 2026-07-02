"""Tests for the switch platform (coverage bug-hunt — was 0% covered).

Covers the three config switches and especially their INVERTED logic:
EdgeClean and AlwaysFinish both negate the underlying robot preference
(openOnly / binPause), which is the most bug-prone part. Also covers
async_setup_entry gating (which switches appear for which robots) and the
turn_on/turn_off → set_preference command mapping.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.switch import (
    EdgeCleanSwitch,
    AlwaysFinishSwitch,
    ScheduleHoldSwitch,
    ChildLockSwitch,
    EcoChargeSwitch,
    async_setup_entry,
)


def _make(cls, state):
    """Build a switch instance bypassing __init__, with vacuum_state set."""
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": state}}
    s = cls.__new__(cls)
    s.vacuum = roomba
    s.vacuum_state = state
    s._blid = "test_blid"
    s.hass = MagicMock()
    s.hass.async_add_executor_job = AsyncMock()
    return s


# ── EdgeCleanSwitch: ON when openOnly is False (inverted) ────────────────────

class TestEdgeCleanSwitch:
    def test_on_when_openonly_false(self):
        s = _make(EdgeCleanSwitch, {"openOnly": False})
        assert s.is_on is True

    def test_off_when_openonly_true(self):
        s = _make(EdgeCleanSwitch, {"openOnly": True})
        assert s.is_on is False

    def test_default_on_when_key_missing(self):
        # .get("openOnly", False) → not False → True (edge clean on by default)
        s = _make(EdgeCleanSwitch, {})
        assert s.is_on is True

    @pytest.mark.asyncio
    async def test_turn_on_sends_openonly_false(self):
        s = _make(EdgeCleanSwitch, {"openOnly": True})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "openOnly", False
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_openonly_true(self):
        s = _make(EdgeCleanSwitch, {"openOnly": False})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "openOnly", True
        )

    def test_new_state_filter(self):
        s = _make(EdgeCleanSwitch, {})
        assert s.new_state_filter({"openOnly": True}) is True
        assert s.new_state_filter({"other": 1}) is False


# ── AlwaysFinishSwitch: ON when binPause is False (inverted) ─────────────────

class TestAlwaysFinishSwitch:
    def test_on_when_binpause_false(self):
        s = _make(AlwaysFinishSwitch, {"binPause": False})
        assert s.is_on is True

    def test_off_when_binpause_true(self):
        s = _make(AlwaysFinishSwitch, {"binPause": True})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        # .get("binPause", True) → not True → False (pause by default)
        s = _make(AlwaysFinishSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_binpause_false(self):
        s = _make(AlwaysFinishSwitch, {"binPause": True})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "binPause", False
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_binpause_true(self):
        s = _make(AlwaysFinishSwitch, {"binPause": False})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "binPause", True
        )


# ── ScheduleHoldSwitch: ON when schedHold is True (NOT inverted) ─────────────

class TestScheduleHoldSwitch:
    def test_on_when_schedhold_true(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": True})
        assert s.is_on is True

    def test_off_when_schedhold_false(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(ScheduleHoldSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_schedhold_true(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "schedHold", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_schedhold_false(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "schedHold", False
        )


# ── ChildLockSwitch: ON when childLock is True (NOT inverted) ────────────────

class TestChildLockSwitch:
    def test_on_when_childlock_true(self):
        s = _make(ChildLockSwitch, {"childLock": True})
        assert s.is_on is True

    def test_off_when_childlock_false(self):
        s = _make(ChildLockSwitch, {"childLock": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(ChildLockSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_childlock_true(self):
        s = _make(ChildLockSwitch, {"childLock": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "childLock", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_childlock_false(self):
        s = _make(ChildLockSwitch, {"childLock": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "childLock", False
        )


# ── EcoChargeSwitch: ON when ecoCharge is True (NOT inverted) ────────────────

class TestEcoChargeSwitch:
    def test_on_when_ecocharge_true(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": True})
        assert s.is_on is True

    def test_off_when_ecocharge_false(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(EcoChargeSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_ecocharge_true(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "ecoCharge", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_ecocharge_false(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "ecoCharge", False
        )


# ── async_setup_entry: gating by capability key presence ────────────────────

class TestSwitchSetupGating:
    def _setup(self, reported_state):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": reported_state}}
        entry = MagicMock()
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "blid123"
        added: list = []

        def _add(entities):
            added.extend(entities)

        hass = MagicMock()
        # Stub IRobotEntity.__init__ so the switch constructors don't try to
        # build DeviceInfo / read robot_unique_id from a MagicMock.
        with patch("custom_components.roomba_plus.switch.IRobotEntity.__init__",
                   return_value=None), \
             patch("custom_components.roomba_plus.entity.IRobotEntity.robot_unique_id",
                   new_callable=lambda: property(lambda self: "uid")):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                async_setup_entry(hass, entry, _add)
            )
        return added

    def test_no_switches_when_no_keys(self):
        added = self._setup({})
        assert added == []

    def test_only_edge_clean_when_openonly_present(self):
        added = self._setup({"openOnly": False})
        assert len(added) == 1
        assert isinstance(added[0], EdgeCleanSwitch)

    def test_all_five_when_all_keys_present(self):
        added = self._setup({
            "openOnly": False,
            "binPause": True,
            "schedHold": False,
            "childLock": False,
            "ecoCharge": False,
        })
        types = {type(e) for e in added}
        assert types == {
            EdgeCleanSwitch,
            AlwaysFinishSwitch,
            ScheduleHoldSwitch,
            ChildLockSwitch,
            EcoChargeSwitch,
        }

    def test_clean_base_model_gets_always_finish(self):
        added = self._setup({"binPause": True})
        assert len(added) == 1
        assert isinstance(added[0], AlwaysFinishSwitch)

    def test_only_child_lock_when_childlock_present(self):
        added = self._setup({"childLock": True})
        assert len(added) == 1
        assert isinstance(added[0], ChildLockSwitch)

    def test_only_eco_charge_when_ecocharge_present(self):
        added = self._setup({"ecoCharge": False})
        assert len(added) == 1
        assert isinstance(added[0], EcoChargeSwitch)
