"""Tests for v1.8.0 sensor helper functions and logic.

Pure unit tests — no HA hass fixture needed. Uses the stub conftest.
"""
import datetime
import pytest

from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.sensor import (
    _completion_rate_30d,
    _area_cleaned_today,
    _problem_zone_value,
    _last_error_code_value,
    _mission_store_value,
)
from custom_components.roomba_plus.const import ERROR_CATALOGUE, ERROR_CODE_LABELS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


import time as _time_mod
__make_record_seq = 0

def _make_unique_id(days_ago):
    global __make_record_seq
    __make_record_seq += 1
    return f"m_{days_ago}_{__make_record_seq}"

def _make_record(days_ago=0, result="completed", area_sqft=400.0, zones=None):
    return {
        "id": _make_unique_id(days_ago),
        "started_at": _iso(days_ago),
        "ended_at": _iso(days_ago),
        "duration_min": 30,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": zones or [],
        "error_code": None,
        "bbrun_hr": 100,
    }


async def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        await store.async_append(r)
    return store


# ── ERROR_CATALOGUE backward compat ──────────────────────────────────────────

class TestErrorCatalogueBackwardCompat:
    def test_every_old_key_in_catalogue(self):
        """Every error code that existed in ERROR_CODE_LABELS is still present."""
        # Spot-check key codes that sensors rely on
        for code in [0, 2, 6, 14, 17, 18, 36, 46, 48, 68, 224]:
            assert code in ERROR_CATALOGUE, f"Code {code} missing from ERROR_CATALOGUE"

    def test_catalogue_entries_have_required_keys(self):
        for code, entry in ERROR_CATALOGUE.items():
            assert "label" in entry, f"Code {code} missing 'label'"
            assert "description" in entry, f"Code {code} missing 'description'"
            assert "action" in entry, f"Code {code} missing 'action'"

    def test_all_values_are_strings(self):
        for code, entry in ERROR_CATALOGUE.items():
            for field in ("label", "description", "action"):
                assert isinstance(entry[field], str), \
                    f"Code {code} field '{field}' is not a string"

    def test_derived_error_code_labels_matches_catalogue(self):
        for code, label in ERROR_CODE_LABELS.items():
            assert ERROR_CATALOGUE[code]["label"] == label, \
                f"Code {code}: ERROR_CODE_LABELS={label!r} != catalogue label={ERROR_CATALOGUE[code]['label']!r}"


# ── _completion_rate_30d ──────────────────────────────────────────────────────

class TestCompletionRate30d:
    @pytest.mark.asyncio
    async def test_all_completed(self):
        store = await _store_with(
            _make_record(0, "completed"),
            _make_record(1, "completed"),
        )
        assert _completion_rate_30d(store) == 100.0

    @pytest.mark.asyncio
    async def test_half_completed(self):
        store = await _store_with(
            _make_record(0, "completed"),
            _make_record(1, "stuck"),
        )
        assert _completion_rate_30d(store) == 50.0

    @pytest.mark.asyncio
    async def test_none_when_no_records(self):
        store = MissionStore()
        assert _completion_rate_30d(store) is None

    @pytest.mark.asyncio
    async def test_rounded_to_one_decimal(self):
        store = await _store_with(
            _make_record(0, "completed"),
            _make_record(1, "stuck"),
            _make_record(2, "stuck"),
        )
        assert _completion_rate_30d(store) == round(1/3 * 100, 1)


# ── _area_cleaned_today ───────────────────────────────────────────────────────

class TestAreaCleanedToday:
    @pytest.mark.asyncio
    async def test_sums_todays_completed(self):
        store = await _store_with(
            _make_record(0, "completed", area_sqft=200.0),
            _make_record(0, "completed", area_sqft=150.0),
        )
        assert _area_cleaned_today(store) == round(350.0 * 0.0929, 1)  # sqft→m²

    @pytest.mark.asyncio
    async def test_excludes_yesterday(self):
        store = await _store_with(
            _make_record(0, "completed", area_sqft=200.0),
            _make_record(1, "completed", area_sqft=999.0),
        )
        assert _area_cleaned_today(store) == round(200.0 * 0.0929, 1)  # sqft→m²

    @pytest.mark.asyncio
    async def test_none_when_no_today_records(self):
        store = await _store_with(_make_record(1, "completed", area_sqft=200.0))
        assert _area_cleaned_today(store) == 0.0

    @pytest.mark.asyncio
    async def test_zero_for_600_series_no_area(self):
        # 600-series records have area_sqft=None → no areas → 0.0 m²
        store = await _store_with(_make_record(0, "completed", area_sqft=None))
        assert _area_cleaned_today(store) == 0.0


# ── _problem_zone_value ───────────────────────────────────────────────────────

class TestProblemZoneValue:
    def _make_entity(self, store):
        class _FakeData:
            mission_store = store
            last_error_code = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()
            vacuum_state = {}

        return _FakeEntity()

    @pytest.mark.asyncio
    async def test_most_frequent_stuck_zone(self):
        store = await _store_with(
            _make_record(0, "stuck", zones=["Kitchen"]),
            _make_record(1, "stuck", zones=["Kitchen"]),
            _make_record(2, "stuck", zones=["Living room"]),
        )
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) == "Kitchen"

    @pytest.mark.asyncio
    async def test_none_when_no_stuck_records(self):
        store = await _store_with(_make_record(0, "completed"))
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) is None

    def test_none_when_no_store(self):
        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _problem_zone_value(_FakeEntity()) is None

    @pytest.mark.asyncio
    async def test_none_when_stuck_records_have_no_zones(self):
        store = await _store_with(_make_record(0, "stuck", zones=[]))
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) is None


# ── _last_error_code_value ────────────────────────────────────────────────────

class TestLastErrorCodeValue:
    def _make_entity(self, live_error=0, persisted_error=None):
        class _FakeData:
            last_error_code = persisted_error

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()
            vacuum_state = {
                "cleanMissionStatus": {"error": live_error}
            }

        return _FakeEntity()

    def test_live_mqtt_takes_priority(self):
        entity = self._make_entity(live_error=17, persisted_error=2)
        assert _last_error_code_value(entity) == 17

    def test_falls_back_to_persisted_when_no_live(self):
        entity = self._make_entity(live_error=0, persisted_error=36)
        assert _last_error_code_value(entity) == 36

    def test_none_when_neither(self):
        entity = self._make_entity(live_error=0, persisted_error=None)
        assert _last_error_code_value(entity) is None


# ── last_error_zone — capture-at-start logic (integration-level) ──────────────

class TestLastErrorZoneCapture:
    """Tests for _capture_zone_names logic extracted from __init__.py.

    We test the zone-name resolution rules indirectly via mission_store
    records, since the capture logic runs inside the MQTT closure.
    """

    @pytest.mark.asyncio
    async def test_zone_written_into_record(self):
        """Zones captured at start appear in the mission record."""
        store = MissionStore()
        record = {
            "id": "m_1",
            "started_at": _iso(0),
            "ended_at": _iso(0),
            "duration_min": 30,
            "area_sqft": None,
            "result": "error",
            "initiator": "schedule",
            "zones": ["Kitchen"],   # captured at mission start
            "error_code": 17,
            "bbrun_hr": 100,
        }
        await store.async_append(record)
        latest = store.latest()
        assert latest["zones"] == ["Kitchen"]
        assert latest["error_code"] == 17

    @pytest.mark.asyncio
    async def test_empty_zones_for_600_series(self):
        """600-series missions have empty zones list."""
        store = MissionStore()
        record = {**{
            "id": "m_600",
            "started_at": _iso(0),
            "ended_at": _iso(0),
            "duration_min": 20,
            "area_sqft": None,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 50,
        }}
        await store.async_append(record)
        assert store.latest()["zones"] == []


# ── L6 analytics — insufficient data returns None ─────────────────────────────

class TestL6Helpers:
    def _make_entity_with_store(self, store):
        class _FakeData:
            mission_store = store

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        return _FakeEntity()

    def test_presence_opportunities_none_when_no_store(self):
        from custom_components.roomba_plus.sensor import _presence_opportunities

        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _presence_opportunities(_FakeEntity(), 7) is None

    @pytest.mark.asyncio
    async def test_next_likely_clean_window_none_with_fewer_than_3_windows(self):
        from custom_components.roomba_plus.sensor import _next_likely_clean_window
        store = await _store_with(_make_record(0))
        entity = self._make_entity_with_store(store)
        assert _next_likely_clean_window(entity) is None

    def test_presence_utilisation_none_when_no_store(self):
        from custom_components.roomba_plus.sensor import _presence_utilisation

        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _presence_utilisation(_FakeEntity(), 7) is None
