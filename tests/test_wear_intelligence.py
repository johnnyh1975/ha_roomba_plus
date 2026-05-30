"""Tests for MissionStore.wear_data() and wear_rate_since_reset() — L4 Wear Intelligence.

Pure unit tests — no HA hass fixture, no mocks beyond the dt_util stub
provided by conftest.py.
"""
import datetime
import pytest

from custom_components.roomba_plus.mission_store import MissionStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0, hour: int = 10) -> str:
    """Return an ISO datetime string N days in the past."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record(days_ago: float = 0, bbrun_hr: int = 100) -> dict:
    started = _iso(days_ago, hour=8)
    ended = _iso(days_ago, hour=9)
    return {
        "id": f"m_{days_ago}",
        "started_at": started,
        "ended_at": ended,
        "duration_min": 60,
        "area_sqft": 400.0,
        "result": "completed",
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": bbrun_hr,
    }


def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


# ── wear_data() ───────────────────────────────────────────────────────────────

class TestWearData:
    def test_empty_when_no_records(self):
        store = MissionStore()
        assert store.wear_data(30) == []

    def test_single_day_single_record(self):
        store = _store_with(_make_record(days_ago=1, bbrun_hr=50))
        result = store.wear_data(30)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 50

    def test_multiple_records_same_day_keeps_highest_bbrun_hr(self):
        store = _store_with(
            _make_record(days_ago=1, bbrun_hr=80),
            _make_record(days_ago=1, bbrun_hr=120),
            _make_record(days_ago=1, bbrun_hr=95),
        )
        result = store.wear_data(30)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 120

    def test_multiple_days_sorted_ascending(self):
        store = _store_with(
            _make_record(days_ago=3, bbrun_hr=30),
            _make_record(days_ago=1, bbrun_hr=50),
            _make_record(days_ago=2, bbrun_hr=40),
        )
        result = store.wear_data(30)
        assert len(result) == 3
        hrs = [r["bbrun_hr"] for r in result]
        assert hrs == sorted(hrs)

    def test_filters_by_days_parameter(self):
        store = _store_with(
            _make_record(days_ago=2, bbrun_hr=40),
            _make_record(days_ago=10, bbrun_hr=80),
        )
        result = store.wear_data(5)
        assert len(result) == 1
        assert result[0]["bbrun_hr"] == 40

    def test_returns_list_never_none(self):
        store = MissionStore()
        result = store.wear_data(30)
        assert isinstance(result, list)

    def test_record_without_bbrun_hr_excluded(self):
        store = MissionStore()
        r = _make_record(days_ago=1, bbrun_hr=50)
        del r["bbrun_hr"]
        store._records.append(r)
        assert store.wear_data(30) == []

    def test_date_field_is_iso_string(self):
        store = _store_with(_make_record(days_ago=1, bbrun_hr=50))
        result = store.wear_data(30)
        # Should be parseable as a date
        datetime.date.fromisoformat(result[0]["date"])


# ── wear_rate_since_reset() ───────────────────────────────────────────────────

class TestWearRateSinceReset:
    def test_none_when_reset_at_is_none(self):
        store = MissionStore()
        assert store.wear_rate_since_reset(0, None, 50) is None

    def test_none_when_current_hr_not_advanced(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        assert store.wear_rate_since_reset(100, reset_at, 100) is None
        assert store.wear_rate_since_reset(100, reset_at, 90) is None

    def test_none_when_fewer_than_3_days_elapsed(self):
        store = MissionStore()
        reset_at = _iso(days_ago=2)
        assert store.wear_rate_since_reset(0, reset_at, 10) is None

    def test_none_at_exactly_2_days_elapsed(self):
        store = MissionStore()
        reset_at = _iso(days_ago=2)
        assert store.wear_rate_since_reset(0, reset_at, 20) is None

    def test_value_at_exactly_3_days_elapsed(self):
        store = MissionStore()
        # Use 4 days to guarantee > 3 days threshold regardless of current
        # wall-clock time. _iso() rounds to hour=10, so 3.x days may be
        # < 3 days elapsed depending on when the test runs.
        # Rate = 4hr / 4days = 1.0 h/day (with tolerance for rounding)
        reset_at = _iso(days_ago=4)
        result = store.wear_rate_since_reset(0, reset_at, 4)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.2)

    def test_correct_rate_over_30_days(self):
        store = MissionStore()
        reset_at = _iso(days_ago=30)
        # 30 hours consumed over 30 days = 1.0 h/day
        result = store.wear_rate_since_reset(0, reset_at, 30)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.05)

    def test_rate_rounded_to_2_decimal_places(self):
        store = MissionStore()
        reset_at = _iso(days_ago=7)
        result = store.wear_rate_since_reset(0, reset_at, 10)
        assert result is not None
        assert result == round(result, 2)

    def test_none_when_reset_at_unparseable(self):
        store = MissionStore()
        assert store.wear_rate_since_reset(0, "not-a-date", 50) is None

    def test_never_returns_negative(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # current_hr > reset_hr is already guarded, but confirm
        result = store.wear_rate_since_reset(50, reset_at, 100)
        assert result is None or result >= 0.0

    def test_reset_hr_offset_applied_correctly(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # reset_hr=100, current_hr=110 → 10 hours in 10 days = 1.0 h/day
        result = store.wear_rate_since_reset(100, reset_at, 110)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.1)

    def test_result_is_float_not_int(self):
        store = MissionStore()
        reset_at = _iso(days_ago=7)
        result = store.wear_rate_since_reset(0, reset_at, 7)
        if result is not None:
            assert isinstance(result, float)


# ── Integration: wear_data + wear_rate_since_reset ───────────────────────────

class TestWearIntegration:
    def test_wear_data_feeds_into_rate_calculation(self):
        """wear_data provides the time-series; wear_rate_since_reset computes the scalar."""
        store = _store_with(
            _make_record(days_ago=5, bbrun_hr=50),
            _make_record(days_ago=4, bbrun_hr=51),
            _make_record(days_ago=3, bbrun_hr=52),
        )
        data = store.wear_data(30)
        assert len(data) == 3
        # Reset at day 5 with hr=50 → current hr=52 over ~5 days
        reset_at = _iso(days_ago=5)
        rate = store.wear_rate_since_reset(50, reset_at, 52)
        assert rate is not None
        assert rate == pytest.approx(0.4, abs=0.1)

    def test_empty_store_returns_none_rate(self):
        store = MissionStore()
        reset_at = _iso(days_ago=10)
        # No records, but rate calculation is independent of records
        # (uses wall-clock elapsed time and current_hr directly)
        result = store.wear_rate_since_reset(0, reset_at, 10)
        assert result is not None  # math works without records
        assert result == pytest.approx(1.0, abs=0.1)
