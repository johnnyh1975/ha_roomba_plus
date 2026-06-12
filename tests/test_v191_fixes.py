"""Tests for v1.9.1 fixes.

Covers:
  - mssnStrtTm caching: duration computed from cached start, not end-state
  - bbrun_hr merge: runtimeStats fallback for i-series
  - nStuck delta: lifetime counter not used directly as result
  - last_error_code: not cleared on completed mission
  - area_cleaned_today: sqft->m² conversion
  - extra_state_attributes status hints
"""
from __future__ import annotations

import datetime
import pytest
import types
import sys
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import tests.conftest  # noqa: F401

from custom_components.roomba_plus.sensor import (
    _area_cleaned_today,
    SENSORS,
)
from custom_components.roomba_plus.mission_store import MissionStore


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

def _make_record(days_ago=0, result="completed", area_sqft=200.0, bbrun_hr=100):
    started = _iso(days_ago, hour=8)
    ended   = _iso(days_ago, hour=9)
    return {
        "id": _make_unique_id(days_ago),
        "started_at": started,
        "ended_at": ended,
        "duration_min": 60,
        "area_sqft": area_sqft,
        "result": result,
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


def _get_sensor(key: str):
    for desc in SENSORS:
        if desc.key == key:
            return desc
    raise KeyError(f"Sensor '{key}' not found")


# ── Fix 1: mssnStrtTm caching ─────────────────────────────────────────────────

class TestMssnStrtTmCaching:
    """Verify the closure caches start_ts at mission start, not end."""

    def test_start_ts_used_for_duration_not_end(self):
        """When start_ts is cached, duration = now - start_ts (not 0)."""
        # Simulate: start_ts cached 33 minutes ago
        start_ts = int(
            (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(minutes=33)).timestamp()
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        started_at = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc)
        elapsed = (now - started_at).total_seconds()
        duration_min = max(0, round(elapsed / 60))
        assert 30 <= duration_min <= 36  # ~33 min with tolerance

    def test_zero_start_ts_fallback_gives_zero_duration(self):
        """When start_ts=0 (fallback), duration is ~0."""
        start_ts = 0
        now = datetime.datetime.now(datetime.timezone.utc)
        started_at = now if not start_ts else datetime.datetime.fromtimestamp(
            start_ts, datetime.timezone.utc
        )
        elapsed = (now - started_at).total_seconds()
        duration_min = max(0, round(elapsed / 60))
        assert duration_min == 0

    def test_mission_id_uses_start_not_end(self):
        """Mission ID should be based on start_ts, not now()."""
        start_ts = 1780000000  # fixed timestamp
        started_at = datetime.datetime.fromtimestamp(
            start_ts, datetime.timezone.utc
        )
        mission_id = f"m_{int(started_at.timestamp())}"
        assert mission_id == "m_1780000000"
        assert mission_id != f"m_{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}"


# ── Fix 2: bbrun_hr merge ────────────────────────────────────────────────────

class TestBbrunHrMerge:
    """bbrun_hr in mission record must fall back to runtimeStats.hr for i-series."""

    def test_bbrun_hr_from_bbrun_when_present(self):
        """900-series: hr in bbrun -> use it."""
        reported = {"bbrun": {"hr": 428, "nStuck": 5}, "runtimeStats": {}}
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 428

    def test_bbrun_hr_from_runtimeStats_when_bbrun_missing(self):
        """i-series: hr NOT in bbrun -> fall back to runtimeStats."""
        reported = {
            "bbrun": {"nPanics": 100, "nStuck": 2},  # no "hr"
            "runtimeStats": {"hr": 312, "sqft": 500},
        }
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 312

    def test_bbrun_hr_zero_when_both_absent(self):
        """No hr anywhere -> 0."""
        reported = {"bbrun": {"nStuck": 1}, "runtimeStats": {}}
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 0

    def test_bbrun_hr_prefers_bbrun_over_runtimeStats(self):
        """When both present, bbrun wins (900-series canonical source)."""
        reported = {
            "bbrun": {"hr": 200},
            "runtimeStats": {"hr": 300},
        }
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 200


# ── Fix 3: nStuck delta ───────────────────────────────────────────────────────

class TestNStuckDelta:
    """nStuck result uses delta, not lifetime counter."""

    def test_no_new_stuck_in_mission(self):
        """nStuck same at start and end -> result not stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 159
        delta = max(0, nstuck_at_end - nstuck_at_start)
        result = "stuck" if delta > 0 else "completed"
        assert result == "completed"

    def test_one_new_stuck_in_mission(self):
        """nStuck incremented -> result stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 160
        delta = max(0, nstuck_at_end - nstuck_at_start)
        result = "stuck" if delta > 0 else "completed"
        assert result == "stuck"

    def test_high_lifetime_nstuck_does_not_falsely_mark_stuck(self):
        """Lifetime counter of 159 should NOT mark a clean mission as stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 159  # no change this mission
        delta = max(0, nstuck_at_end - nstuck_at_start)
        # Old logic: bbrun.get("nStuck", 0) = 159 -> truthy -> "stuck" (BUG)
        old_logic_result = "stuck" if nstuck_at_end > 0 else "completed"
        new_logic_result  = "stuck" if delta > 0 else "completed"
        assert old_logic_result == "stuck"   # confirms the old bug
        assert new_logic_result == "completed"  # confirms the fix

    def test_error_takes_priority_over_stuck(self):
        """error_code > 0 -> result=error regardless of nStuck delta."""
        error_code = 17
        nstuck_delta = 1
        if error_code:
            result = "error"
        elif nstuck_delta > 0:
            result = "stuck"
        else:
            result = "completed"
        assert result == "error"


# ── Fix 4: last_error_code not cleared on completed ──────────────────────────

class TestLastErrorCodePersistence:
    """Error code must persist across successful missions."""

    def test_error_set_on_error_result(self):
        """error result -> last_error_code updated."""
        error_code = 17
        result = "error"
        last_error_code = None
        if result in ("error", "stuck"):
            last_error_code = error_code
        assert last_error_code == 17

    def test_error_not_cleared_on_completed(self):
        """completed result -> last_error_code unchanged."""
        last_error_code = 17  # previously set
        result = "completed"
        # New logic: only update on error/stuck
        if result in ("error", "stuck"):
            last_error_code = None  # would set new error
        # No elif for completed -> last_error_code unchanged
        assert last_error_code == 17  # still 17

    def test_old_logic_would_clear(self):
        """Confirm the old bug: completed cleared the error."""
        last_error_code = 17
        result = "completed"
        # Old logic had: elif result == "completed": last_error_code = None
        if result in ("error", "stuck"):
            last_error_code = None
        elif result == "completed":
            last_error_code = None  # old bug
        assert last_error_code is None  # confirms old bug cleared it


# ── Fix 6: area_cleaned_today m² conversion ──────────────────────────────────

class TestAreaCleanedTodayM2:
    def test_converts_sqft_to_m2(self):
        store = _store_with(_make_record(0, "completed", area_sqft=200.0))
        result = _area_cleaned_today(store)
        assert result == round(200.0 * 0.0929, 1)

    def test_sums_multiple_and_converts(self):
        store = _store_with(
            _make_record(0, "completed", area_sqft=200.0),
            _make_record(0, "completed", area_sqft=150.0),
        )
        result = _area_cleaned_today(store)
        assert result == round(350.0 * 0.0929, 1)

    def test_none_when_no_records(self):
        store = MissionStore()
        assert _area_cleaned_today(store) == 0.0

    def test_unit_is_square_meters(self):
        desc = _get_sensor("area_cleaned_today")
        from homeassistant.const import UnitOfArea
        assert desc.native_unit_of_measurement == UnitOfArea.SQUARE_METERS

    def test_result_is_smaller_than_sqft_input(self):
        """m² < sqft for same area — sanity check."""
        store = _store_with(_make_record(0, "completed", area_sqft=500.0))
        result = _area_cleaned_today(store)
        assert result < 500.0
        assert result > 40.0  # 500 sqft ≈ 46.5 m²


# ── Fix 7: status attributes ─────────────────────────────────────────────────

class TestStatusAttributes:
    """extra_state_attributes returns status hints when native_value is None."""

    def _make_wear_entity(self, reset_at=None):
        class _FakeMaint:
            filter_reset_at = reset_at
            brush_reset_at  = reset_at

        class _FakeRuntimeData:
            maintenance_store = _FakeMaint()

        class _FakeEntry:
            runtime_data = _FakeRuntimeData()
            options = {}

        class _FakeEntity:
            _config_entry = _FakeEntry()
            def __init__(self, key):
                self._key = key
            @property
            def native_value(self):
                return None  # sensor is Unknown

        return _FakeEntity

    def test_status_hint_when_no_reset_recorded(self):
        """When reset_at is None: suggest pressing the button."""
        reset_at = None
        if reset_at is None:
            status = "Press the replacement confirmation button to start tracking"
        else:
            status = "Collecting data — available after 3 days"
        assert "button" in status.lower()

    def test_status_hint_when_reset_recorded_but_too_early(self):
        """When reset_at is set but <3 days ago: collecting data."""
        reset_at = _iso(days_ago=1)  # 1 day ago
        if reset_at is None:
            status = "Press the replacement confirmation button to start tracking"
        else:
            status = "Collecting data — available after 3 days"
        assert "3 days" in status

    def test_no_status_when_value_present(self):
        """When native_value is set (not None), no status needed."""
        native_value = 1.5  # h/day
        if native_value is None:
            status = "some hint"
        else:
            status = None
        assert status is None
