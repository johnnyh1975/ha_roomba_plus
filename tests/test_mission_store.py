"""Tests for MissionStore — append-only mission history log.

Pure unit tests — no HA hass fixture, no mocks beyond the dt_util stub
provided by conftest.py.
"""
import datetime
import pytest

from custom_components.roomba_plus.mission_store import (
    MissionStore,
    DaySummary,
    MissionWindow,
    MAX_RECORDS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0, hour: int = 10) -> str:
    """Return an ISO datetime string N days in the past."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record(
    days_ago: float = 0,
    result: str = "completed",
    duration_min: int = 30,
    area_sqft: float | None = 400.0,
    zones: list | None = None,
    error_code: int | None = None,
) -> dict:
    started = _iso(days_ago, hour=8)
    ended = _iso(days_ago, hour=9)
    return {
        "id": f"m_{days_ago}",
        "started_at": started,
        "ended_at": ended,
        "duration_min": duration_min,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": zones or [],
        "error_code": error_code,
        "bbrun_hr": 100,
    }


# ── Default / empty state ─────────────────────────────────────────────────────

class TestDefaultState:
    def test_latest_returns_none_when_empty(self):
        store = MissionStore()
        assert store.latest() is None

    def test_query_returns_empty_list(self):
        store = MissionStore()
        assert store.query(30) == []

    def test_clean_streak_zero_when_empty(self):
        store = MissionStore()
        assert store.clean_streak() == 0

    def test_presence_windows_empty_when_no_records(self):
        store = MissionStore()
        assert store.presence_windows(7) == []

    def test_query_by_day_empty(self):
        store = MissionStore()
        assert store.query_by_day(30) == {}


# ── async_append ─────────────────────────────────────────────────────────────

class TestAsyncAppend:
    @pytest.mark.asyncio
    async def test_append_stores_record(self):
        store = MissionStore()
        r = _make_record()
        await store.async_append(r)
        assert store.latest() == r

    @pytest.mark.asyncio
    async def test_append_multiple(self):
        store = MissionStore()
        for i in range(5):
            await store.async_append(_make_record(days_ago=i))
        assert len(store.query(365)) == 5

    @pytest.mark.asyncio
    async def test_trim_to_max_records(self):
        store = MissionStore()
        for i in range(MAX_RECORDS + 10):
            await store.async_append(_make_record(days_ago=i * 0.1))
        assert len(store.query(365)) == MAX_RECORDS

    @pytest.mark.asyncio
    async def test_trim_keeps_newest(self):
        store = MissionStore()
        # Append oldest first, newest last
        for i in range(MAX_RECORDS + 5):
            r = _make_record(days_ago=(MAX_RECORDS + 5 - i) * 0.01)
            r["id"] = f"m_{i}"
            await store.async_append(r)
        # After trim, latest should be the last-appended
        assert store.latest()["id"] == f"m_{MAX_RECORDS + 4}"


# ── query ─────────────────────────────────────────────────────────────────────

class TestQuery:
    @pytest.mark.asyncio
    async def test_filters_by_days(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))   # within 1 day
        await store.async_append(_make_record(days_ago=5))   # within 7 days
        await store.async_append(_make_record(days_ago=10))  # outside 7 days
        assert len(store.query(7)) == 2

    @pytest.mark.asyncio
    async def test_filters_by_result(self):
        store = MissionStore()
        await store.async_append(_make_record(result="completed"))
        await store.async_append(_make_record(result="stuck"))
        await store.async_append(_make_record(result="error"))
        assert len(store.query(30, result="completed")) == 1
        assert len(store.query(30, result="stuck")) == 1
        assert len(store.query(30)) == 3

    @pytest.mark.asyncio
    async def test_returns_sorted_ascending(self):
        store = MissionStore()
        for i in [3, 1, 2]:
            await store.async_append(_make_record(days_ago=i))
        records = store.query(30)
        started = [r["started_at"] for r in records]
        assert started == sorted(started)


# ── query_by_day ──────────────────────────────────────────────────────────────

class TestQueryByDay:
    @pytest.mark.asyncio
    async def test_groups_by_date(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))
        await store.async_append(_make_record(days_ago=1))
        by_day = store.query_by_day(7)
        assert len(by_day) == 2

    @pytest.mark.asyncio
    async def test_day_summary_totals(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        today_summary = list(by_day.values())[0]
        assert today_summary.total == 2
        assert today_summary.completed == 1
        assert today_summary.stuck == 1

    @pytest.mark.asyncio
    async def test_dominant_result_error_wins(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="error"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].result == "error"

    @pytest.mark.asyncio
    async def test_dominant_result_stuck_over_completed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].result == "stuck"

    @pytest.mark.asyncio
    async def test_area_sqft_summed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, area_sqft=200.0))
        await store.async_append(_make_record(days_ago=0, area_sqft=300.0))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].area_sqft == 500.0

    @pytest.mark.asyncio
    async def test_area_sqft_none_for_600_series(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, area_sqft=None))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].area_sqft is None


# ── clean_streak ──────────────────────────────────────────────────────────────

class TestCleanStreak:
    @pytest.mark.asyncio
    async def test_streak_today(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        assert store.clean_streak() == 1

    @pytest.mark.asyncio
    async def test_streak_multiple_consecutive_days(self):
        store = MissionStore()
        for i in range(3):
            await store.async_append(_make_record(days_ago=i, result="completed"))
        assert store.clean_streak() == 3

    @pytest.mark.asyncio
    async def test_streak_resets_on_gap(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        # day 1 missing — gap
        await store.async_append(_make_record(days_ago=2, result="completed"))
        assert store.clean_streak() == 1  # only today counts

    @pytest.mark.asyncio
    async def test_streak_ignores_non_completed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        assert store.clean_streak() == 0

    @pytest.mark.asyncio
    async def test_streak_zero_when_no_today(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=1, result="completed"))
        assert store.clean_streak() == 0


# ── presence_windows ──────────────────────────────────────────────────────────

class TestPresenceWindows:
    @pytest.mark.asyncio
    async def test_empty_when_fewer_than_2_records(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))
        assert store.presence_windows(7) == []

    @pytest.mark.asyncio
    async def test_window_detected_between_missions(self):
        store = MissionStore()
        # mission 1 ended yesterday at 9:00, mission 2 starts today at 8:00
        # — there is a ~23hr window between them
        r1 = _make_record(days_ago=1)
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0)
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert len(windows) == 1
        assert windows[0].duration_min > 0

    @pytest.mark.asyncio
    async def test_resulted_in_clean_true_for_completed_start(self):
        store = MissionStore()
        r1 = _make_record(days_ago=1, result="completed")
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0, result="completed")
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert len(windows) == 1
        assert windows[0].resulted_in_clean is True

    @pytest.mark.asyncio
    async def test_resulted_in_clean_false_for_stuck(self):
        store = MissionStore()
        r1 = _make_record(days_ago=1, result="completed")
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0, result="stuck")
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert windows[0].resulted_in_clean is False


# ── Serialisation round-trip ──────────────────────────────────────────────────

class TestSerialisation:
    @pytest.mark.asyncio
    async def test_round_trip_preserves_records(self):
        """Records survive save → load cycle (via raw dict, not actual storage)."""
        store = MissionStore()
        records = [_make_record(days_ago=i) for i in range(5)]
        for r in records:
            await store.async_append(r)

        # Simulate save/load by extracting and reloading internal state
        saved = list(store._records)
        store2 = MissionStore()
        store2._records = saved

        assert len(store2.query(365)) == 5
        assert store2.latest()["id"] == records[-1]["id"]

    @pytest.mark.asyncio
    async def test_load_with_missing_fields_defaults_safely(self):
        """Records missing optional fields don't crash query methods."""
        store = MissionStore()
        minimal = {
            "id": "m_minimal",
            "started_at": _iso(0),
            "ended_at": _iso(0),
            "result": "completed",
        }
        store._records = [minimal]
        # Should not raise
        records = store.query(30)
        assert len(records) == 1
        assert store.clean_streak() == 1
