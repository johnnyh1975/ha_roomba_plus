"""Reset buttons for consumables that exist only as an iRobot cloud counter.

The filter and main brushes have local MaintenanceStore slots; the edge
brush and dust bag do not, so the cloud counter IS their state and these
buttons write it directly. They are created only when the account actually
reports the part, so a robot without a Clean Base gets no bag button
rather than one that silently does nothing.

Part payloads are a real capture from an i3+ — see
tests/fixtures/irobot_parts_i3plus.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.button import (
    CleanBaseBagResetButton,
    SideBrushResetButton,
    _cloud_part_reset_buttons,
)
from custom_components.roomba_plus.const import (
    IROBOT_PART_ROLE_CLEAN_BASE_BAG,
    IROBOT_PART_ROLE_SIDE_BRUSH,
)
from custom_components.roomba_plus.maintenance_store import MaintenanceStore

FIXTURES = Path(__file__).parent / "fixtures"


def _parts(exclude: set[str] | None = None) -> list[dict]:
    with open(FIXTURES / "irobot_parts_i3plus.json", encoding="utf-8") as f:
        parts = json.load(f)["parts"]
    return [p for p in parts if p["part_id"] not in (exclude or set())]


def _store(parts: list[dict] | None = None) -> MaintenanceStore:
    store = MaintenanceStore()
    store.hydrate_from_cloud_parts(_parts() if parts is None else parts, 300)
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


def _button(cls, entry: MagicMock):
    button = cls.__new__(cls)
    button._config_entry = entry
    button.hass = MagicMock()
    button.vacuum = MagicMock()
    button.schedule_update_ha_state = MagicMock()
    button._maintenance_store = lambda: entry.runtime_data.maintenance_store
    return button


class TestButtonCreationIsGatedOnTheReportedPart:
    def test_both_buttons_for_a_robot_reporting_both_parts(self):
        buttons = _cloud_part_reset_buttons(MagicMock(), "BLID1", _entry(_store()))
        assert {type(b).__name__ for b in buttons} == {
            "SideBrushResetButton",
            "CleanBaseBagResetButton",
        }

    def test_no_bag_button_when_the_robot_has_no_clean_base(self):
        """A dockless robot must not get a bag button."""
        store = _store(_parts(exclude={"139"}))
        buttons = _cloud_part_reset_buttons(MagicMock(), "BLID1", _entry(store))
        assert [type(b).__name__ for b in buttons] == ["SideBrushResetButton"]

    def test_no_side_brush_button_when_that_part_is_absent(self):
        store = _store(_parts(exclude={"36"}))
        buttons = _cloud_part_reset_buttons(MagicMock(), "BLID1", _entry(store))
        assert [type(b).__name__ for b in buttons] == ["CleanBaseBagResetButton"]

    def test_no_buttons_without_cloud(self):
        assert _cloud_part_reset_buttons(
            MagicMock(), "BLID1", _entry(_store(), cloud=False)
        ) == []

    def test_no_buttons_without_a_maintenance_store(self):
        assert _cloud_part_reset_buttons(MagicMock(), "BLID1", _entry(None)) == []

    def test_no_buttons_before_hydration(self):
        """An account that serves no parts data yields nothing, rather than
        buttons whose press could not do anything."""
        assert _cloud_part_reset_buttons(
            MagicMock(), "BLID1", _entry(MaintenanceStore())
        ) == []


class TestUniqueIdsAndRoles:
    def test_side_brush_identity(self):
        button = _button(SideBrushResetButton, _entry(_store()))
        assert button._cloud_role == IROBOT_PART_ROLE_SIDE_BRUSH
        assert button._attr_translation_key == "reset_side_brush"

    def test_bag_identity(self):
        button = _button(CleanBaseBagResetButton, _entry(_store()))
        assert button._cloud_role == IROBOT_PART_ROLE_CLEAN_BASE_BAG
        assert button._attr_translation_key == "reset_clean_base_bag"


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
    async def test_press_refreshes_so_the_sensor_updates_immediately(self):
        entry = _entry(_store())
        button = _button(SideBrushResetButton, entry)
        await button.async_press()
        button.schedule_update_ha_state.assert_called_once()


class TestPressFailureHandling:
    @pytest.mark.asyncio
    async def test_cloud_error_is_logged_not_raised(self, caplog):
        """A press is a user action; an outage must not surface as a
        traceback, and must not claim success."""
        entry = _entry(_store())
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            side_effect=RuntimeError("cloud down")
        )
        with caplog.at_level("WARNING"):
            await _button(SideBrushResetButton, entry).async_press()

        assert "could not record replacement" in caplog.text.lower()
        entry.runtime_data.cloud_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_applied_nothing_is_treated_as_failure(self, caplog):
        """num_parts 0 is a 200 that changed nothing — the flat-body shape
        fails exactly this way, so it must not read as success."""
        entry = _entry(_store())
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter = AsyncMock(
            return_value={"num_parts": 0, "parts": {}}
        )
        with caplog.at_level("WARNING"):
            await _button(SideBrushResetButton, entry).async_press()

        assert "applied no part" in caplog.text.lower()
        entry.runtime_data.cloud_coordinator.async_request_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_press_without_cloud_is_a_silent_no_op(self):
        entry = _entry(_store(), cloud=False)
        await _button(SideBrushResetButton, entry).async_press()  # must not raise

    @pytest.mark.asyncio
    async def test_press_with_no_record_for_the_role_does_nothing(self):
        """Guards the window where the part vanishes from cloud data
        between entity creation and a press."""
        entry = _entry(_store(_parts(exclude={"36"})))
        await _button(SideBrushResetButton, entry).async_press()
        entry.runtime_data.cloud_coordinator.api.set_robot_part_counter.assert_not_awaited()
