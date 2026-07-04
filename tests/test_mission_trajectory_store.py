"""Tests for MissionTrajectoryStore — bounded last-N-missions raw pose
history (v3.2.1). Data-collection scaffolding, see module docstring for
the "letzte 2-3 Missionen mit Linien" motivation.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.mission_trajectory_store import (
    MAX_MISSIONS,
    PAYLOAD_VERSION,
    MissionTrajectoryStore,
)


class TestInitialState:
    def test_empty_initially(self):
        store = MissionTrajectoryStore()
        assert store.missions == []
        assert store.mission_count == 0


class TestRecordMission:
    def test_records_one_mission(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (100.0, 100.0)])
        assert store.mission_count == 1
        assert store.missions[0]["mission_key"] == "m1"
        assert store.missions[0]["points"] == [[0.0, 0.0], [100.0, 100.0]]
        assert store.missions[0]["thetas"] == []

    def test_empty_points_skipped(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [])
        assert store.mission_count == 0

    def test_oldest_first_order(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0)])
        store.record_mission("m2", [(1.0, 1.0)])
        assert [m["mission_key"] for m in store.missions] == ["m1", "m2"]

    def test_bounded_at_max_missions(self):
        store = MissionTrajectoryStore()
        for i in range(MAX_MISSIONS + 5):
            store.record_mission(f"m{i}", [(float(i), 0.0)])
        assert store.mission_count == MAX_MISSIONS
        # oldest missions dropped first (FIFO)
        keys = [m["mission_key"] for m in store.missions]
        assert keys == [f"m{i}" for i in range(5, MAX_MISSIONS + 5)]

    def test_ended_at_stored(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0)], ended_at="2026-07-02T12:00:00")
        assert store.missions[0]["ended_at"] == "2026-07-02T12:00:00"


class TestThetaStorage:
    """v3.2.1 — thetas_deg: parallel list to points_mm, same index
    alignment, kept separate rather than widening points_mm's own tuple
    shape (would ripple through existing points-as-(x,y)-pairs usage).
    Prerequisite for Dock-Anchor-Korrektur v2 (rotation correction) and
    a future wall-follow curvature signal — see design doc.
    """

    def test_thetas_stored_alongside_points(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (100.0, 0.0)], thetas_deg=[0.0, 90.0])
        assert store.missions[0]["thetas"] == [0.0, 90.0]

    def test_thetas_omitted_defaults_empty(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (100.0, 0.0)])
        assert store.missions[0]["thetas"] == []

    def test_mismatched_length_thetas_dropped_not_erroring(self):
        """A caller bug (mismatched list lengths) must not corrupt data
        or raise — falls back to no theta for that mission rather than
        silently misaligning indices."""
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (100.0, 0.0)], thetas_deg=[0.0])
        assert store.missions[0]["thetas"] == []

    @pytest.mark.asyncio
    async def test_thetas_persist_across_save_load_roundtrip(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (100.0, 0.0)], thetas_deg=[0.0, 45.5])
        saved = {}
        async def fake_save(data):
            saved.update(data)
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_save(MagicMock(), "e1")
        assert saved["missions"][0]["thetas"] == [0.0, 45.5]

        store2 = MissionTrajectoryStore()
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock2):
            await store2.async_load(MagicMock(), "e1")
        assert store2.missions[0]["thetas"] == [0.0, 45.5]

    @pytest.mark.asyncio
    async def test_old_payload_without_thetas_loads_cleanly(self):
        """v3.2.1 — additive field: a payload saved before thetas existed
        simply has no 'thetas' key per mission."""
        store = MissionTrajectoryStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION,
            "missions": [{"mission_key": "m1", "ended_at": "", "points": [[0.0, 0.0]]}],
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.missions[0]["thetas"] == []


class TestLastN:
    def test_last_n_returns_most_recent(self):
        store = MissionTrajectoryStore()
        for i in range(5):
            store.record_mission(f"m{i}", [(float(i), 0.0)])
        last2 = store.last_n(2)
        assert [m["mission_key"] for m in last2] == ["m3", "m4"]

    def test_last_n_larger_than_available_returns_all(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0)])
        assert len(store.last_n(10)) == 1

    def test_last_n_zero_or_negative_returns_empty(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0)])
        assert store.last_n(0) == []
        assert store.last_n(-1) == []


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self):
        store = MissionTrajectoryStore()
        store.record_mission("m1", [(0.0, 0.0), (150.0, -75.0)], ended_at="t1")
        store.record_mission("m2", [(10.0, 10.0)], ended_at="t2")

        saved = {}
        async def fake_save(data):
            saved.update(data)
        store_mock = MagicMock()
        store_mock.async_save = fake_save
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_save(MagicMock(), "e1")
        assert saved["version"] == PAYLOAD_VERSION
        assert len(saved["missions"]) == 2

        store2 = MissionTrajectoryStore()
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock2):
            await store2.async_load(MagicMock(), "e1")
        assert store2.missions == store.missions

    @pytest.mark.asyncio
    async def test_load_no_data_leaves_store_empty(self):
        store = MissionTrajectoryStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=None)
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.missions == []

    @pytest.mark.asyncio
    async def test_wrong_version_discarded(self):
        store = MissionTrajectoryStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={"version": 999, "missions": [{"mission_key": "x", "points": [[0, 0]]}]})
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.missions == []

    @pytest.mark.asyncio
    async def test_corrupted_payload_resets_cleanly(self):
        store = MissionTrajectoryStore()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION,
            "missions": [{"mission_key": "m1", "points": [["bad", "data"]]}],
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.missions == []

    @pytest.mark.asyncio
    async def test_bound_preserved_across_load(self):
        """Loading more than MAX_MISSIONS worth of persisted data (e.g.
        after a MAX_MISSIONS config decrease) must not exceed the cap."""
        store = MissionTrajectoryStore()
        many = [
            {"mission_key": f"m{i}", "ended_at": "", "points": [[0.0, 0.0]]}
            for i in range(MAX_MISSIONS + 3)
        ]
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "version": PAYLOAD_VERSION, "missions": many,
        })
        with patch("homeassistant.helpers.storage.Store", return_value=store_mock):
            await store.async_load(MagicMock(), "e1")
        assert store.mission_count == MAX_MISSIONS
