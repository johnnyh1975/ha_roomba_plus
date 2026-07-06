"""v3.4.0 bug-hunt fix — tests for async_remove_entry().

Found during the README/docs review: __init__.py had async_unload_entry
(runs on every reload too) but no async_remove_entry (HA's hook that
fires ONLY on permanent config-entry deletion) — meaning none of the
15 hass.storage files this integration persists were ever actually
deleted when a user removed the integration, contradicting the
TROUBLESHOOTING.md claim that deletion "removes... cleanly".

These tests verify every expected storage key gets a removal attempt
with the correct entry_id substituted, and that one failing removal
doesn't prevent the rest from being attempted.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import (
    _STORAGE_KEYS_TO_REMOVE,
    async_remove_entry,
)


def _make_entry(entry_id: str = "test_entry_123"):
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


class TestStorageKeyRegistry:
    """Guard test — a store added to the integration in the future
    without a matching entry here would silently leak its storage file
    on every removal, same class of bug this whole fix addresses."""

    def test_expected_key_count(self):
        """15 stores confirmed against source at implementation time —
        13 STORAGE_KEY_PREFIX modules + image.py's 2 own keys."""
        assert len(_STORAGE_KEYS_TO_REMOVE) == 15

    def test_no_duplicate_keys(self):
        templates = [k for _, k in _STORAGE_KEYS_TO_REMOVE]
        assert len(templates) == len(set(templates))

    def test_every_template_takes_entry_id(self):
        for label, template in _STORAGE_KEYS_TO_REMOVE:
            assert "{entry_id}" in template, f"{label}: missing entry_id placeholder"

    @pytest.mark.parametrize("expected_key", [
        "roomba_plus_dirt_threshold_{entry_id}",
        "roomba_plus_freeze_{entry_id}",
        "roomba_plus_geometry_{entry_id}",
        "roomba_plus_grid_{entry_id}",
        "roomba_plus_zones_{entry_id}",
        "roomba_plus_maintenance_{entry_id}",
        "roomba_plus_mission_archive_{entry_id}",
        "roomba_plus_missions_{entry_id}",
        "roomba_plus_mission_timer_{entry_id}",
        "roomba_plus_trajectories_{entry_id}",
        "roomba_plus_outline_{entry_id}",
        "roomba_plus_robot_profile_{entry_id}",
        "roomba_plus_roomseg_{entry_id}",
        "roomba_plus_map_{entry_id}",
        "roomba_plus_map_checkpoint_{entry_id}",
    ])
    def test_each_known_store_key_present(self, expected_key):
        templates = [k for _, k in _STORAGE_KEYS_TO_REMOVE]
        assert expected_key in templates


class TestAsyncRemoveEntry:
    @pytest.mark.asyncio
    async def test_removes_every_storage_key_with_correct_entry_id(self):
        entry = _make_entry("abc123")
        hass = MagicMock()
        created_stores = []

        def _fake_store(hass_arg, version, key):
            store = MagicMock()
            store.async_remove = AsyncMock()
            created_stores.append((version, key, store))
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)

        assert len(created_stores) == len(_STORAGE_KEYS_TO_REMOVE)
        created_keys = {key for _, key, _ in created_stores}
        expected_keys = {
            template.format(entry_id="abc123")
            for _, template in _STORAGE_KEYS_TO_REMOVE
        }
        assert created_keys == expected_keys
        for _, _, store in created_stores:
            store.async_remove.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_stores_use_version_1(self):
        """Confirmed against source: every store in this integration
        declares HA-store version 1. Store.async_remove() doesn't
        actually use the version for anything, but using the wrong one
        would be a needless inconsistency."""
        entry = _make_entry()
        hass = MagicMock()
        versions = []

        def _fake_store(hass_arg, version, key):
            versions.append(version)
            store = MagicMock()
            store.async_remove = AsyncMock()
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)

        assert all(v == 1 for v in versions)

    @pytest.mark.asyncio
    async def test_one_failing_removal_does_not_block_the_rest(self):
        """The core resilience property — a permissions error or
        unexpected I/O failure on ONE file must not leave the other 14
        untouched."""
        entry = _make_entry()
        hass = MagicMock()
        call_count = 0

        def _fake_store(hass_arg, version, key):
            nonlocal call_count
            call_count += 1
            store = MagicMock()
            if call_count == 3:
                store.async_remove = AsyncMock(side_effect=OSError("disk error"))
            else:
                store.async_remove = AsyncMock()
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)  # must not raise

        assert call_count == len(_STORAGE_KEYS_TO_REMOVE)

    @pytest.mark.asyncio
    async def test_missing_file_is_not_treated_as_a_failure(self):
        """Store.async_remove() already suppresses FileNotFoundError
        internally (verified against HA source) — a robot tier that
        never created a given store (e.g. a 600-series robot has no
        GridStore data) must not log a warning for every such key."""
        entry = _make_entry()
        hass = MagicMock()

        def _fake_store(hass_arg, version, key):
            store = MagicMock()
            store.async_remove = AsyncMock()  # succeeds silently, as real Store does
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store), \
             patch("custom_components.roomba_plus._LOGGER") as mock_logger:
            await async_remove_entry(hass, entry)

        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_stores_attempted_regardless_of_robot_tier(self):
        """No gating by map_capability or any other runtime_data field —
        deletion cleans up every possible file unconditionally, since a
        config entry being removed may have switched tiers over its
        lifetime (e.g. a firmware update) and could have leftover data
        from an earlier tier."""
        entry = _make_entry()
        hass = MagicMock()
        # Deliberately do NOT set up entry.runtime_data — if the
        # implementation ever started gating on it, this test would
        # fail with an AttributeError on a MagicMock-default access
        # that wasn't anticipated, surfacing the coupling immediately.
        del entry.runtime_data

        with patch("homeassistant.helpers.storage.Store") as mock_store_cls:
            mock_store_cls.return_value.async_remove = AsyncMock()
            await async_remove_entry(hass, entry)  # must not raise

        assert mock_store_cls.call_count == len(_STORAGE_KEYS_TO_REMOVE)
