"""Tests for FreezeSnapshotStore — periodic immutable RoomSeg+Outline
backup against the firmware pose-cutoff risk (v3.2.1).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.freeze_snapshot_store import (
    INTERVAL_RECOMPUTES,
    PAYLOAD_VERSION,
    FreezeSnapshotStore,
)


class TestInitialState:
    def test_empty_initially(self):
        store = FreezeSnapshotStore()
        assert store.rooms == []
        assert store.doors == []
        assert store.outline_points == []
        assert store.has_snapshot is False

    def test_due_immediately_when_never_snapshotted(self):
        store = FreezeSnapshotStore()
        assert store.due() is True


class TestNoteRecomputeAndDue:
    def test_not_due_before_interval_elapses(self):
        store = FreezeSnapshotStore()
        store.snapshot([], [], [], "t0")
        for _ in range(INTERVAL_RECOMPUTES - 1):
            store.note_recompute()
        assert store.due() is False

    def test_due_once_interval_elapses(self):
        store = FreezeSnapshotStore()
        store.snapshot([], [], [], "t0")
        for _ in range(INTERVAL_RECOMPUTES):
            store.note_recompute()
        assert store.due() is True

    def test_counter_resets_after_snapshot(self):
        store = FreezeSnapshotStore()
        store.snapshot([], [], [], "t0")
        for _ in range(INTERVAL_RECOMPUTES):
            store.note_recompute()
        assert store.due() is True
        store.snapshot([{"id": "room_1"}], [], [], "t1")
        assert store.due() is False


class TestSnapshot:
    def test_stores_rooms_doors_outline(self):
        store = FreezeSnapshotStore()
        rooms = [{"id": "room_1", "cells": [[0, 0]]}]
        doors = [{"id": "door_1", "room_a": "room_1", "room_b": "room_2"}]
        outline = [(100.0, 200.0), (300.0, 400.0)]
        store.snapshot(rooms, doors, outline, "2026-07-02T12:00:00")
        assert store.rooms == rooms
        assert store.doors == doors
        assert store.outline_points == [[100.0, 200.0], [300.0, 400.0]]
        assert store.snapshotted_at == "2026-07-02T12:00:00"
        assert store.has_snapshot is True

    def test_overwrites_previous_snapshot(self):
        store = FreezeSnapshotStore()
        store.snapshot([{"id": "room_1"}], [], [], "t0")
        store.snapshot([{"id": "room_2"}], [], [], "t1")
        assert store.rooms == [{"id": "room_2"}]
        assert store.snapshotted_at == "t1"

    def test_returned_lists_are_copies_not_live_references(self):
        store = FreezeSnapshotStore()
        rooms = [{"id": "room_1"}]
        store.snapshot(rooms, [], [], "t0")
        rooms.append({"id": "room_2"})
        assert store.rooms == [{"id": "room_1"}]


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self):
        store = FreezeSnapshotStore()
        store.snapshot(
            [{"id": "room_1", "cells": [[0, 0], [1, 0]]}],
            [{"id": "door_1", "room_a": "room_1", "room_b": "room_2"}],
            [(10.0, 20.0)],
            "2026-07-02T12:00:00",
        )
        saved = {}
        async def fake_save(data):
            saved.update(data)
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_save(MagicMock(), "e1")
        assert saved["version"] == PAYLOAD_VERSION

        store2 = FreezeSnapshotStore()
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock2):
            await store2.async_load(MagicMock(), "e1")
        assert store2.rooms == store.rooms
        assert store2.doors == store.doors
        assert store2.outline_points == store.outline_points
        assert store2.snapshotted_at == store.snapshotted_at

    @pytest.mark.asyncio
    async def test_load_no_data_leaves_store_empty(self):
        store = FreezeSnapshotStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=None)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.has_snapshot is False

    @pytest.mark.asyncio
    async def test_wrong_version_discarded(self):
        store = FreezeSnapshotStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={"version": 999, "rooms": [{"id": "x"}]})
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.rooms == []

    @pytest.mark.asyncio
    async def test_corrupted_outline_points_resets_cleanly(self):
        store = FreezeSnapshotStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION,
            "rooms": [], "doors": [],
            "outline_points": [["bad", "data"]],
            "snapshotted_at": "t0",
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.rooms == []
        assert store.outline_points == []
        assert store.has_snapshot is False
