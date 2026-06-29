"""Tests for legacy_zone_migration.py -- the minimal read-only shim kept
for one-shot migration after ZoneStore's removal (ROOM-SEG Stage 6)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.legacy_zone_migration import (
    LegacyZone,
    async_load_legacy_zones,
)


def _old_payload(zones: list[dict]) -> dict:
    return {
        "version": 2,
        "next_id": len(zones) + 1,
        "gap_threshold_mm": 800.0,
        "scale_factor": 1.0,
        "zones": zones,
    }


class TestAsyncLoadLegacyZones:
    @pytest.mark.asyncio
    async def test_no_data_returns_empty_list(self):
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(return_value=None)
            result = await async_load_legacy_zones(MagicMock(), "entry1")
        assert result == []

    @pytest.mark.asyncio
    async def test_wrong_version_returns_empty_list(self):
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(
                return_value={"version": 1, "zones": [{"name": "x"}]}
            )
            result = await async_load_legacy_zones(MagicMock(), "entry1")
        assert result == []

    @pytest.mark.asyncio
    async def test_real_old_zone_data_parsed_correctly(self):
        payload = _old_payload([
            {
                "id": 1, "name": "Kitchen", "confirmed": True, "hidden": False,
                "x_min": -500.0, "x_max": 500.0, "y_min": -300.0, "y_max": 300.0,
                "confidence": 0.8, "observations": [],
            },
            {
                "id": 2, "name": "", "confirmed": False, "hidden": False,
                "x_min": 600.0, "x_max": 1200.0, "y_min": -300.0, "y_max": 300.0,
                "confidence": 0.2, "observations": [],
            },
        ])
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(return_value=payload)
            result = await async_load_legacy_zones(MagicMock(), "entry1")

        assert len(result) == 2
        assert result[0] == LegacyZone(
            name="Kitchen", confirmed=True, hidden=False,
            x_min=-500.0, x_max=500.0, y_min=-300.0, y_max=300.0,
        )

    @pytest.mark.asyncio
    async def test_missing_hidden_field_defaults_false(self):
        """Backward compat with even older data that predates the hidden field."""
        payload = _old_payload([
            {"id": 1, "name": "Kitchen", "confirmed": True,
             "x_min": 0.0, "x_max": 100.0, "y_min": 0.0, "y_max": 100.0},
        ])
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(return_value=payload)
            result = await async_load_legacy_zones(MagicMock(), "entry1")
        assert result[0].hidden is False

    @pytest.mark.asyncio
    async def test_malformed_zone_entry_returns_empty_list_not_raises(self):
        payload = _old_payload([{"id": 1, "name": "Broken"}])  # missing x_min etc.
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(return_value=payload)
            result = await async_load_legacy_zones(MagicMock(), "entry1")
        assert result == []

    @pytest.mark.asyncio
    async def test_result_compatible_with_migrate_from_zone_store(self):
        """End-to-end: the LegacyZone objects this module produces must
        work directly with RoomSegStore.migrate_from_zone_store(), which
        reads attributes via getattr() rather than importing LegacyZone."""
        from custom_components.roomba_plus.room_seg_store import RoomSegStore

        payload = _old_payload([
            {"id": 1, "name": "Kitchen", "confirmed": True, "hidden": False,
             "x_min": 0.0, "x_max": 900.0, "y_min": 0.0, "y_max": 900.0},
        ])
        with patch(
            "custom_components.roomba_plus.legacy_zone_migration.Store"
        ) as MockStore:
            MockStore.return_value.async_load = AsyncMock(return_value=payload)
            legacy_zones = await async_load_legacy_zones(MagicMock(), "entry1")

        store = RoomSegStore(min_distance_cells=3.0)
        cells = {(x, y): 1.0 for x in range(0, 6) for y in range(0, 6)}
        store.maybe_recompute(cells)
        migrated = store.migrate_from_zone_store(legacy_zones)
        assert migrated == 1
        assert any(r.name == "Kitchen" for r in store.rooms.values())
