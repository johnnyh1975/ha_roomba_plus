"""Unit tests for experimental command buttons (spot / quick / sleep / power_off).

Verifies:
  - All four buttons are defined in COMMAND_BUTTONS
  - All four are disabled by default (entity_registry_enabled_default=False)
  - filter_fn correctly gates on absence of pmaps (EPHEMERAL robots only)
  - Commands sent to the robot match the protocol strings
  - Standard buttons (evac, locate) are still enabled by default
  - RoombaCommandButton propagates entity_registry_enabled_default from description

No HA or roombapy installation required — uses stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from custom_components.roomba_plus.button import (
    COMMAND_BUTTONS,
    RoombaButtonDescription,
    RoombaCommandButton,
)

# ── Shared state fixtures ─────────────────────────────────────────────────────

# 980-style state: pose present, no pmaps (EPHEMERAL)
STATE_980 = {
    "cap": {"pose": 1, "carpetBoost": 1},
    "carpetBoost": True,
    "vacHigh": False,
}

# i7-style state: pmaps present (SMART)
STATE_I7 = {
    "cap": {"pose": 1, "pmaps": 3},
    "pmaps": [{"abc123": "v20240101"}],
}

# 600-series: no pose, no pmaps (NONE)
STATE_600 = {}


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_button(key: str) -> RoombaButtonDescription:
    for btn in COMMAND_BUTTONS:
        if btn.key == key:
            return btn
    raise KeyError(f"Button '{key}' not found in COMMAND_BUTTONS")


def _make_button_entity(key: str) -> RoombaCommandButton:
    desc = _get_button(key)
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    return RoombaCommandButton(roomba, "test_blid", desc)


# ── Presence in COMMAND_BUTTONS ────────────────────────────────────────────────

class TestExperimentalButtonsPresent:
    def test_spot_defined(self):
        _get_button("spot")  # raises if missing

    def test_quick_defined(self):
        _get_button("quick")

    def test_sleep_defined(self):
        _get_button("sleep")

    def test_power_off_defined(self):
        _get_button("power_off")

    def test_total_button_count(self):
        """Ensure we have the expected number of buttons total (2 standard + 4 experimental)."""
        assert len(COMMAND_BUTTONS) == 7  # +1 map_training (v1.9.0)


# ── Disabled by default ────────────────────────────────────────────────────────

class TestExperimentalButtonsDisabledByDefault:
    def test_spot_disabled(self):
        assert _get_button("spot").entity_registry_enabled_default is False

    def test_quick_disabled(self):
        assert _get_button("quick").entity_registry_enabled_default is False

    def test_sleep_disabled(self):
        assert _get_button("sleep").entity_registry_enabled_default is False

    def test_power_off_disabled(self):
        assert _get_button("power_off").entity_registry_enabled_default is False

    def test_evac_enabled(self):
        """Standard evac button must remain enabled by default."""
        assert _get_button("evac").entity_registry_enabled_default is True

    def test_locate_enabled(self):
        """Standard locate button must remain enabled by default."""
        assert _get_button("locate").entity_registry_enabled_default is True


# ── entity_registry_enabled_default propagation ────────────────────────────────

class TestEntityRegistryEnabledPropagation:
    def test_experimental_entity_disabled(self):
        entity = _make_button_entity("spot")
        assert entity._attr_entity_registry_enabled_default is False

    def test_standard_entity_enabled(self):
        entity = _make_button_entity("locate")
        assert entity._attr_entity_registry_enabled_default is True


# ── filter_fn gating ──────────────────────────────────────────────────────────

class TestExperimentalButtonFilterFn:
    """filter_fn should return truthy for 980 (no pmaps) and falsy for i7 (pmaps present)."""

    def _passes(self, key: str, state: dict) -> bool:
        btn = _get_button(key)
        if btn.filter_fn is None:
            return True
        return bool(btn.filter_fn(state))

    def test_spot_passes_for_980(self):
        assert self._passes("spot", STATE_980) is True

    def test_spot_blocked_for_i7(self):
        assert self._passes("spot", STATE_I7) is False

    def test_quick_passes_for_980(self):
        assert self._passes("quick", STATE_980) is True

    def test_quick_blocked_for_i7(self):
        assert self._passes("quick", STATE_I7) is False

    def test_sleep_passes_for_980(self):
        assert self._passes("sleep", STATE_980) is True

    def test_sleep_blocked_for_i7(self):
        assert self._passes("sleep", STATE_I7) is False

    def test_power_off_passes_for_980(self):
        assert self._passes("power_off", STATE_980) is True

    def test_power_off_blocked_for_i7(self):
        assert self._passes("power_off", STATE_I7) is False

    def test_spot_passes_for_600(self):
        """600-series has no pmaps either — filter passes, entity is created."""
        assert self._passes("spot", STATE_600) is True

    def test_locate_always_passes(self):
        """locate has no filter_fn — always created."""
        assert self._passes("locate", STATE_I7) is True
        assert self._passes("locate", STATE_980) is True


# ── Command strings ────────────────────────────────────────────────────────────

class TestExperimentalButtonCommands:
    def test_spot_command_string(self):
        assert _get_button("spot").command == "spot"

    def test_quick_command_string(self):
        assert _get_button("quick").command == "quick"

    def test_sleep_command_string(self):
        assert _get_button("sleep").command == "sleep"

    def test_power_off_command_string(self):
        """iRobot protocol uses 'off', not 'power_off'."""
        assert _get_button("power_off").command == "off"


# ── async_press sends correct command ────────────────────────────────────────

class TestExperimentalButtonPress:
    @pytest.mark.asyncio
    async def test_spot_press_sends_spot(self):
        entity = _make_button_entity("spot")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "spot"

    @pytest.mark.asyncio
    async def test_quick_press_sends_quick(self):
        entity = _make_button_entity("quick")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "quick"

    @pytest.mark.asyncio
    async def test_sleep_press_sends_sleep(self):
        entity = _make_button_entity("sleep")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "sleep"

    @pytest.mark.asyncio
    async def test_power_off_press_sends_off(self):
        """power_off button must send 'off' to the robot, not 'power_off'."""
        entity = _make_button_entity("power_off")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "off"


# ── Translation keys ──────────────────────────────────────────────────────────

class TestExperimentalButtonTranslationKeys:
    def test_spot_translation_key(self):
        assert _get_button("spot").translation_key == "spot"

    def test_quick_translation_key(self):
        assert _get_button("quick").translation_key == "quick"

    def test_sleep_translation_key(self):
        assert _get_button("sleep").translation_key == "sleep"

    def test_power_off_translation_key(self):
        assert _get_button("power_off").translation_key == "power_off"
