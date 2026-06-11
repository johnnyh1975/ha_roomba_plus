"""Unit tests for RoombaMapSavingStatus binary sensor.

Tests cover:
  - is_on when notReady bit 6 is set (map saving in progress)
  - is_off when notReady is 0 or bit 6 is clear
  - is_off when cleanMissionStatus is absent
  - Other notReady bits do not affect the sensor
  - extra_state_attributes exposes full bitmask
  - new_state_filter triggers only on cleanMissionStatus changes
  - Sensor created only for Smart Map robots
  - Sensor not created for non-Smart Map robots

No HA or roombapy installation required — uses stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from custom_components.roomba_plus.binary_sensor import (
    RoombaMapSavingStatus,
    _NOT_READY_MAP_SAVING,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sensor(not_ready: int = 0) -> RoombaMapSavingStatus:
    roomba = MagicMock()
    roomba.master_state = {
        "state": {
            "reported": {
                "cleanMissionStatus": {"notReady": not_ready},
                "pmaps": [{"abc": "v1"}],
            }
        }
    }
    return RoombaMapSavingStatus(roomba, "test_blid")


# ── Constant ──────────────────────────────────────────────────────────────────

class TestNotReadyConstant:
    def test_value_is_64(self):
        assert _NOT_READY_MAP_SAVING == 64


# ── is_on ─────────────────────────────────────────────────────────────────────

class TestMapSavingIsOn:
    def test_on_when_bit_6_set(self):
        sensor = _make_sensor(not_ready=64)
        assert sensor.is_on is True

    def test_off_when_not_ready_is_zero(self):
        sensor = _make_sensor(not_ready=0)
        assert sensor.is_on is False

    def test_off_when_cleanmissionstatus_absent(self):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        sensor = RoombaMapSavingStatus(roomba, "blid")
        assert sensor.is_on is False

    def test_on_when_bit_6_combined_with_others(self):
        """bit 6 set alongside other bits — still ON."""
        sensor = _make_sensor(not_ready=64 | 1 | 4)
        assert sensor.is_on is True

    def test_off_when_other_bits_set_but_not_bit_6(self):
        """bit 1 + bit 2 + bit 5 — no map saving."""
        sensor = _make_sensor(not_ready=1 | 2 | 32)
        assert sensor.is_on is False

    def test_off_when_not_ready_is_none(self):
        roomba = MagicMock()
        roomba.master_state = {
            "state": {"reported": {"cleanMissionStatus": {"notReady": None}}}
        }
        sensor = RoombaMapSavingStatus(roomba, "blid")
        # None treated as 0 via `or 0` guard — sensor must return False
        assert sensor.is_on is False

    def test_bitmask_values(self):
        """Exhaustive check: only multiples of 64 within reasonable range trigger ON."""
        sensor = _make_sensor(not_ready=0)
        for v in range(256):
            roomba = MagicMock()
            roomba.master_state = {
                "state": {"reported": {"cleanMissionStatus": {"notReady": v}}}
            }
            sensor2 = RoombaMapSavingStatus(roomba, "blid")
            expected = bool(v & 64)
            assert sensor2.is_on == expected, f"Failed for notReady={v}"


# ── extra_state_attributes ────────────────────────────────────────────────────

class TestMapSavingAttributes:
    def test_exposes_bitmask(self):
        sensor = _make_sensor(not_ready=64)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 64

    def test_zero_bitmask_when_idle(self):
        sensor = _make_sensor(not_ready=0)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 0

    def test_combined_bitmask_preserved(self):
        sensor = _make_sensor(not_ready=65)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 65


# ── new_state_filter ──────────────────────────────────────────────────────────

class TestMapSavingStateFilter:
    def test_triggers_on_cleanmissionstatus(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"cleanMissionStatus": {"notReady": 64}}) is True

    def test_ignores_other_fields(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"bin": {"full": True}}) is False
        assert sensor.new_state_filter({"pose": {"x": 1}}) is False
        assert sensor.new_state_filter({}) is False

    def test_triggers_when_combined_with_other_fields(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"cleanMissionStatus": {}, "bin": {}}) is True


# ── Entity metadata ───────────────────────────────────────────────────────────

class TestMapSavingMetadata:
    def test_unique_id(self):
        sensor = _make_sensor()
        assert "map_saving" in sensor._attr_unique_id

    def test_translation_key(self):
        sensor = _make_sensor()
        assert sensor.entity_description.translation_key == "map_saving"

    def test_device_class_update(self):
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        sensor = _make_sensor()
        assert sensor._attr_device_class == BinarySensorDeviceClass.UPDATE

    def test_entity_category_diagnostic(self):
        from homeassistant.const import EntityCategory
        sensor = _make_sensor()
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC


# ── async_setup_entry routing ─────────────────────────────────────────────────

class TestMapSavingSetupEntry:
    @pytest.mark.asyncio
    async def test_created_for_smart_map_robot(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        state = {"pmaps": [{"abc": "v1"}], "cleanMissionStatus": {"notReady": 0}}
        entry = MagicMock()
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        roomba.roomba_connected = True
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(bs_mod, "roomba_reported_state", return_value=state):
            with patch.object(bs_mod, "has_smart_map", return_value=True):
                await bs_mod.async_setup_entry(MagicMock(), entry, sync_add)

        map_saving = [e for e in created if isinstance(e, RoombaMapSavingStatus)]
        assert len(map_saving) == 1

    @pytest.mark.asyncio
    async def test_not_created_for_non_smart_map_robot(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        state = {}
        entry = MagicMock()
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        roomba.roomba_connected = True
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(bs_mod, "roomba_reported_state", return_value=state):
            with patch.object(bs_mod, "has_smart_map", return_value=False):
                await bs_mod.async_setup_entry(MagicMock(), entry, sync_add)

        map_saving = [e for e in created if isinstance(e, RoombaMapSavingStatus)]
        assert len(map_saving) == 0


# ── Automation scenario ───────────────────────────────────────────────────────

class TestMapSavingAutomationScenario:
    """Realistic sequence: map save starts, then completes."""

    def _sensor_with_state(self, not_ready: int) -> RoombaMapSavingStatus:
        return _make_sensor(not_ready)

    def test_sequence_off_on_off(self):
        """Robot idle → map saving → map save complete."""
        idle   = self._sensor_with_state(0)
        saving = self._sensor_with_state(64)
        done   = self._sensor_with_state(0)

        assert idle.is_on is False
        assert saving.is_on is True
        assert done.is_on is False

    def test_combined_with_other_not_ready_bits(self):
        """Map saving combined with 'new map' bit (1) — still ON."""
        sensor = self._sensor_with_state(64 | 1)
        assert sensor.is_on is True
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 65
