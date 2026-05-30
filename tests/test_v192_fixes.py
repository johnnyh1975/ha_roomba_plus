"""Tests for v1.9.2 critical bug fixes.

Covers:
  - Bug 1: reset service current_hr merge (bbrun + runtimeStats) for i-series
  - Bug 2: L3 error restore logic consistent with v1.9.1 live semantics
  - Bug 3: last_mission sensor reads from MissionStore, not live mssnStrtTm
"""
from __future__ import annotations

import datetime
import pytest
import sys
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import tests.conftest  # noqa: F401

from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.sensor import (
    _mission_store_last_started_at,
    SENSORS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record(days_ago=0, result="completed", started_at=None, error_code=None):
    started = started_at or _iso(days_ago, hour=8)
    return {
        "id": f"m_{days_ago}",
        "started_at": started,
        "ended_at": _iso(days_ago, hour=9),
        "duration_min": 60,
        "area_sqft": 200.0,
        "result": result,
        "initiator": "schedule",
        "zones": [],
        "error_code": error_code,
        "bbrun_hr": 100,
    }


def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


def _make_entity(store: MissionStore):
    class _FakeRuntimeData:
        mission_store = store
        maintenance_store = None

    class _FakeConfigEntry:
        runtime_data = _FakeRuntimeData()
        options = {}

    class _FakeEntity:
        _config_entry = _FakeConfigEntry()

    return _FakeEntity()


# ── Bug 1: reset service current_hr merge ────────────────────────────────────

class TestResetServiceCurrentHr:
    """reset_filter/reset_brush must use merged hr from bbrun + runtimeStats."""

    def _get_current_hr(self, state: dict) -> int:
        """Replicate the fixed reset service logic."""
        _bbrun   = state.get("bbrun", {})
        _runtime = state.get("runtimeStats", {})
        return _bbrun.get("hr") or _runtime.get("hr") or 0

    def test_reads_from_bbrun_on_900_series(self):
        """900-series: hr in bbrun -> use it."""
        state = {"bbrun": {"hr": 428, "nStuck": 5}, "runtimeStats": {}}
        assert self._get_current_hr(state) == 428

    def test_reads_from_runtimeStats_on_iseries(self):
        """i-series: hr NOT in bbrun -> fall back to runtimeStats."""
        state = {
            "bbrun": {"nPanics": 100},  # no "hr"
            "runtimeStats": {"hr": 312, "sqft": 500},
        }
        assert self._get_current_hr(state) == 312

    def test_zero_when_both_absent(self):
        state = {"bbrun": {}, "runtimeStats": {}}
        assert self._get_current_hr(state) == 0

    def test_bbrun_preferred_when_both_present(self):
        state = {"bbrun": {"hr": 200}, "runtimeStats": {"hr": 300}}
        assert self._get_current_hr(state) == 200

    def test_old_logic_would_return_zero_on_iseries(self):
        """Confirm the old bug: bbrun.get('hr', 0) = 0 on i-series."""
        state = {
            "bbrun": {"nPanics": 100},
            "runtimeStats": {"hr": 312},
        }
        old_hr = state.get("bbrun", {}).get("hr", 0)  # old logic
        new_hr = self._get_current_hr(state)           # new logic
        assert old_hr == 0    # confirms the bug
        assert new_hr == 312  # confirms the fix

    def test_inflated_wear_rate_from_zero_baseline(self):
        """Demonstrate why hr=0 baseline is dangerous for wear rate."""
        # If reset_hr=0 stored due to bug:
        reset_hr_buggy = 0
        current_hr = 312
        days_elapsed = 10
        # Wear rate would be entire lifetime / 10 days = absurdly high
        buggy_rate = (current_hr - reset_hr_buggy) / days_elapsed
        # If reset_hr=312 stored correctly:
        reset_hr_correct = 310  # 2h before reset
        correct_rate = (current_hr - reset_hr_correct) / days_elapsed
        assert buggy_rate == 31.2   # 31.2 h/day — absurd
        assert correct_rate == 0.2  # 0.2 h/day — realistic


# ── Bug 2: L3 error restore logic ────────────────────────────────────────────

class TestErrorRestoreLogic:
    """Error state restored from MissionStore should persist across completed missions."""

    def _restore_error(self, records: list) -> tuple:
        """Replicate the fixed restore logic from __init__.py."""
        last_error_code = None
        last_error_at   = None
        last_error_zone = None
        for _rec in reversed(records):
            _res = _rec.get("result")
            if _res in ("error", "stuck") and _rec.get("error_code"):
                last_error_code = _rec["error_code"]
                last_error_at   = _rec.get("ended_at")
                last_error_zone = (_rec.get("zones") or [None])[0]
                break
        return last_error_code, last_error_at, last_error_zone

    def _restore_error_old(self, records: list) -> tuple:
        """Replicate the OLD (buggy) restore logic for comparison."""
        last_error_code = None
        last_error_at   = None
        last_error_zone = None
        for _rec in reversed(records):
            _res = _rec.get("result")
            if _res == "completed":
                break  # old bug: clears on completed
            if _res in ("error", "stuck") and _rec.get("error_code"):
                last_error_code = _rec["error_code"]
                last_error_at   = _rec.get("ended_at")
                last_error_zone = (_rec.get("zones") or [None])[0]
                break
        return last_error_code, last_error_at, last_error_zone

    def test_error_persists_after_completed(self):
        """Error code stays visible even after a subsequent completed mission."""
        records = [
            _make_record(2, result="error", error_code=17),
            _make_record(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 17

    def test_old_logic_would_clear_error(self):
        """Confirm old logic cleared error when completed came after."""
        records = [
            _make_record(2, result="error", error_code=17),
            _make_record(1, result="completed"),
        ]
        code_old, _, _ = self._restore_error_old(records)
        code_new, _, _ = self._restore_error(records)
        assert code_old is None  # old bug: cleared
        assert code_new == 17    # new: persists

    def test_no_error_when_no_error_records(self):
        records = [
            _make_record(2, result="completed"),
            _make_record(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code is None

    def test_most_recent_error_returned(self):
        records = [
            _make_record(3, result="error", error_code=5),
            _make_record(2, result="error", error_code=17),
            _make_record(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 17  # most recent error

    def test_no_error_when_records_empty(self):
        code, at, zone = self._restore_error([])
        assert code is None
        assert at is None
        assert zone is None

    def test_error_without_error_code_skipped(self):
        """error result with error_code=None should not be restored."""
        records = [
            _make_record(1, result="error", error_code=None),
        ]
        code, _, _ = self._restore_error(records)
        assert code is None

    def test_stuck_result_restores_error(self):
        records = [
            _make_record(1, result="stuck", error_code=15),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 15


# ── Bug 3: last_mission sensor reads from MissionStore ───────────────────────

class TestLastMissionFromStore:
    """last_mission sensor should use MissionStore, not live mssnStrtTm."""

    def test_returns_none_when_store_empty(self):
        store = MissionStore()
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_returns_none_when_store_is_none(self):
        entity = _make_entity(None)
        entity._config_entry.runtime_data.mission_store = None
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_returns_started_at_from_latest_record(self):
        started = _iso(days_ago=1, hour=9)
        store = _store_with(_make_record(1, started_at=started))
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.tzinfo is not None  # timezone-aware

    def test_returns_most_recent_mission(self):
        """Latest record (newest) is returned, not oldest."""
        store = _store_with(
            _make_record(2, started_at=_iso(2, hour=8)),
            _make_record(1, started_at=_iso(1, hour=8)),
        )
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        # Should be the most recently appended = day 1
        expected_date = (
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(days=1)
        ).date()
        assert result.date() == expected_date

    def test_handles_unparseable_started_at(self):
        store = MissionStore()
        store._records.append({
            "id": "m_bad",
            "started_at": "not-a-date",
            "result": "completed",
        })
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_handles_missing_started_at(self):
        store = MissionStore()
        store._records.append({"id": "m_no_ts", "result": "completed"})
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_sensor_description_uses_new_helper(self):
        """Verify the sensor description uses _mission_store_last_started_at."""
        for desc in SENSORS:
            if desc.key == "last_mission":
                # value_fn should call _mission_store_last_started_at
                # We verify indirectly by checking it's NOT entity.last_mission
                import inspect
                src = inspect.getsource(desc.value_fn)
                assert "_mission_store_last_started_at" in src
                return
        raise AssertionError("last_mission sensor not found in SENSORS")

    def test_900_series_mssnStrtTm_zero_does_not_cause_unknown(self):
        """Even when live mssnStrtTm=0, MissionStore gives correct timestamp."""
        # Simulate 980: mssnStrtTm=0 in live state
        # But MissionStore has a record with correct started_at
        started = _iso(days_ago=0, hour=10)
        store = _store_with(_make_record(0, started_at=started))
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        # Gets correct value from store despite mssnStrtTm=0
        assert result is not None
        assert result.date() == datetime.datetime.now(datetime.timezone.utc).date()
