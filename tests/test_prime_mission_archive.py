"""Tests for prime_mission_archive.py (4.6.0, A4)."""

from __future__ import annotations

import pytest

from custom_components.roomba_plus.prime_mission_archive import (
    MAX_RECORDS,
    PrimeMissionArchive,
)


def _rec(i: int, **extra):
    return {"id": f"p_{i}", "nMssn": i, "ended_at": f"2026-01-01T00:{i % 60:02d}:{i // 60:02d}+00:00", **extra}


class TestAdd:
    def test_new_records_kept_oldest_first(self):
        archive = PrimeMissionArchive()
        assert archive.add([_rec(2), _rec(1)]) == 2
        assert [r["id"] for r in archive.records] == ["p_1", "p_2"]

    def test_a_kept_record_takes_only_what_it_lacked(self):
        archive = PrimeMissionArchive()
        archive.add([_rec(1, initiator="schedule")])
        assert archive.add([_rec(1, initiator="other", scope="rooms")]) == 1
        assert archive.records[0]["initiator"] == "schedule"
        assert archive.records[0]["scope"] == "rooms"
        assert archive.add([_rec(1)]) == 0

    def test_invalid_records_are_ignored(self):
        archive = PrimeMissionArchive()
        assert archive.add([{}, {"id": ""}, "x"]) == 0  # type: ignore[list-item]

    def test_capped_dropping_the_oldest(self):
        archive = PrimeMissionArchive()
        archive.add([_rec(i) for i in range(MAX_RECORDS + 5)])
        assert archive.record_count == MAX_RECORDS
        assert archive.find_by_nMssn(0) is None
        assert archive.find_by_nMssn(MAX_RECORDS + 4) is not None


class TestPersistence:
    @pytest.mark.asyncio
    async def test_saved_and_loaded(self, hass):
        archive = PrimeMissionArchive()
        archive.add([_rec(1)])
        await archive.async_save(hass, "E1")
        again = PrimeMissionArchive()
        await again.async_load(hass, "E1")
        assert again.records == archive.records

    @pytest.mark.asyncio
    async def test_a_missing_file_is_empty(self, hass):
        archive = PrimeMissionArchive()
        await archive.async_load(hass, "nothing")
        assert archive.records == []
