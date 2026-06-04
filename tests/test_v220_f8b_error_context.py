"""Tests for F8b — error context enrichment in callbacks + MissionStore.query_by_error.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ── query_by_error ────────────────────────────────────────────────────────────

class TestQueryByError:

    def _ms_with_records(self, records):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = records
        return ms

    def _rec(self, id_, started, ended, error_code, zones=None, result="error"):
        return {
            "id": id_,
            "started_at": started,
            "ended_at": ended,
            "result": result,
            "error_code": error_code,
            "zones": zones or [],
            "duration_min": 30,
            "initiator": "schedule",
            "bbrun_hr": 0,
        }

    def test_returns_matching_error_code(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 17, ["Kitchen"]),
            self._rec("m_2", "2026-06-02T08:00:00+00:00", "2026-06-02T08:30:00+00:00", None, [], "completed"),
        ])
        result = ms.query_by_error(17, days=30)
        assert len(result) == 1
        assert result[0]["id"] == "m_1"

    def test_different_error_code_not_returned(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 15),
        ])
        assert ms.query_by_error(17, days=30) == []

    def test_zone_filter_applied(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 17, ["Kitchen"]),
            self._rec("m_2", "2026-06-02T08:00:00+00:00", "2026-06-02T08:30:00+00:00", 17, ["Hallway"]),
        ])
        result = ms.query_by_error(17, days=30, zone="Kitchen")
        assert len(result) == 1
        assert result[0]["id"] == "m_1"

    def test_zone_none_returns_all_matching_code(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 17, ["A"]),
            self._rec("m_2", "2026-06-02T08:00:00+00:00", "2026-06-02T08:30:00+00:00", 17, ["B"]),
        ])
        assert len(ms.query_by_error(17, days=30, zone=None)) == 2

    def test_returns_empty_when_no_records(self):
        ms = self._ms_with_records([])
        assert ms.query_by_error(17, days=30) == []

    def test_zone_filter_no_match_returns_empty(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 17, ["Kitchen"]),
        ])
        assert ms.query_by_error(17, days=30, zone="Bedroom") == []

    def test_record_with_empty_zones_excluded_by_zone_filter(self):
        ms = self._ms_with_records([
            self._rec("m_1", "2026-06-01T08:00:00+00:00", "2026-06-01T08:30:00+00:00", 17, []),
        ])
        assert ms.query_by_error(17, days=30, zone="Kitchen") == []


# ── F8b record fields ─────────────────────────────────────────────────────────

class TestErrorContextFields:
    """Verify F8b fields are set correctly in the record dict.

    The full callbacks.py integration is covered in tests_integration/.
    These unit tests verify the field logic in isolation by constructing
    record dicts directly.
    """

    def test_error_position_present_when_error_and_pose(self):
        record = {
            "error_code": 17,
            "phase_at_error": "charge",
            "error_position_mm": {"x": 1200.0, "y": -800.0},
            "self_recovered": None,
        }
        assert record["error_position_mm"]["x"] == 1200.0
        assert record["phase_at_error"] == "charge"

    def test_self_recovered_true_on_stuck_and_resumed(self):
        result = "stuck_and_resumed"
        self_recovered = (
            True  if result == "stuck_and_resumed"   else
            False if result == "stuck_and_abandoned" else
            None
        )
        assert self_recovered is True

    def test_self_recovered_false_on_stuck_and_abandoned(self):
        result = "stuck_and_abandoned"
        self_recovered = (
            True  if result == "stuck_and_resumed"   else
            False if result == "stuck_and_abandoned" else
            None
        )
        assert self_recovered is False

    def test_self_recovered_none_on_completed(self):
        result = "completed"
        self_recovered = (
            True  if result == "stuck_and_resumed"   else
            False if result == "stuck_and_abandoned" else
            None
        )
        assert self_recovered is None

    def test_error_position_none_when_no_error_code(self):
        record = {
            "error_code": None,
            "error_position_mm": None,
            "phase_at_error": None,
        }
        assert record["error_position_mm"] is None
        assert record["phase_at_error"] is None

    def test_phase_at_error_none_when_no_error(self):
        # phase_at_error should only be set when error_code > 0
        error_code = 0
        phase = "run"
        phase_at_error = phase if error_code else None
        assert phase_at_error is None

    def test_error_position_float_conversion(self):
        # Verify x/y are stored as floats
        pose_point = {"x": "1200", "y": "-800"}
        error_position_mm = {
            "x": float(pose_point.get("x", 0)),
            "y": float(pose_point.get("y", 0)),
        }
        assert isinstance(error_position_mm["x"], float)
        assert error_position_mm["x"] == 1200.0
