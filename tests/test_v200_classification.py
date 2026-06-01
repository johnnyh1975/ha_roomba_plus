"""Tests for v2.0 done_raw classification and pauseId mapping.

Covers classify_mission_result() — the canonical cloud result classifier.

Classification scheme:
  done="done"                         → "completed"
  done_raw="usrEnd"                   → "cancelled_by_user"
  done="cncl" (not usrEnd)            → "cancelled"
  done="stuck" + pauseId>0            → "error_{pauseId}"
  done="stuck" + pauseId=0            → "stuck"
  unknown done value                  → "unknown"
"""
import pytest


class TestClassifyMissionResultCompleted:
    """done="done" → "completed" regardless of other fields."""

    def test_basic_completed(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done"}) == "completed"

    def test_completed_with_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done", "done_raw": "done"}) == "completed"

    def test_completed_ignores_pause_id(self):
        """pauseId on a completed mission is irrelevant."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "done", "pauseId": 17}) == "completed"


class TestClassifyMissionResultCancelledByUser:
    """done_raw="usrEnd" → "cancelled_by_user" even when done="cncl"."""

    def test_usr_end_is_cancelled_by_user(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": "usrEnd"}) == "cancelled_by_user"

    def test_usr_end_priority_over_cncl(self):
        """done_raw wins over done for user-cancellation detection."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        rec = {"done": "cncl", "done_raw": "usrEnd", "pauseId": 0}
        assert classify_mission_result(rec) == "cancelled_by_user"

    def test_usr_end_without_done_field(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done_raw": "usrEnd"}) == "cancelled_by_user"


class TestClassifyMissionResultCancelled:
    """done="cncl" without usrEnd → "cancelled"."""

    def test_cncl_without_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl"}) == "cancelled"

    def test_cncl_with_other_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": "cncl"}) == "cancelled"

    def test_cncl_with_empty_done_raw(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "cncl", "done_raw": ""}) == "cancelled"


class TestClassifyMissionResultError:
    """done="stuck" + pauseId>0 → "error_{pauseId}"."""

    def test_error_17_cannot_find_home(self):
        """pauseId=17 is the confirmed field log case (WiFi dropout)."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 17}) == "error_17"

    def test_error_18_docking_issue(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 18}) == "error_18"

    def test_error_224_smart_map_localization(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 224}) == "error_224"

    def test_error_code_in_error_catalogue(self):
        """Every error_{code} result should map to a known ERROR_CATALOGUE entry."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        from custom_components.roomba_plus.const import ERROR_CATALOGUE
        for code in (1, 2, 4, 5, 6, 9, 17, 18, 32, 36, 42, 224):
            result = classify_mission_result({"done": "stuck", "pauseId": code})
            assert result == f"error_{code}"
            assert code in ERROR_CATALOGUE, f"pauseId {code} missing from ERROR_CATALOGUE"


class TestClassifyMissionResultStuck:
    """done="stuck" + pauseId=0 or missing → "stuck"."""

    def test_stuck_no_pause_id(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck"}) == "stuck"

    def test_stuck_pause_id_zero(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": 0}) == "stuck"

    def test_stuck_pause_id_none(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "stuck", "pauseId": None}) == "stuck"


class TestClassifyMissionResultUnknown:
    """Unrecognised done values → "unknown"."""

    def test_empty_done(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({}) == "unknown"

    def test_none_done(self):
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": None}) == "unknown"

    def test_future_done_value(self):
        """New iRobot firmware may introduce new done values."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        assert classify_mission_result({"done": "newValue"}) == "unknown"


class TestClassifiedResultInRawRecords:
    """classified_result field is pre-computed and stored in raw records."""

    def _make_raw_records(self, records: list) -> list:
        """Simulate what the coordinator stores: records with classified_result."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        return [
            {**r, "classified_result": classify_mission_result(r)}
            for r in records
            if isinstance(r, dict)
        ]

    def test_completed_record_has_classified_result(self):
        records = self._make_raw_records([{"done": "done", "nMssn": 10, "sqft": 100}])
        assert records[0]["classified_result"] == "completed"

    def test_user_cancelled_record(self):
        records = self._make_raw_records([{"done": "cncl", "done_raw": "usrEnd"}])
        assert records[0]["classified_result"] == "cancelled_by_user"

    def test_error_record_preserves_all_fields(self):
        original = {
            "done": "stuck",
            "done_raw": "stuck",
            "pauseId": 17,
            "startTime": 1700000000,
            "sqft": 80,
            "wlBars": [55, 42, 3, 0, 0],
        }
        records = self._make_raw_records([original])
        rec = records[0]
        assert rec["classified_result"] == "error_17"
        # All original fields preserved
        for key, val in original.items():
            assert rec[key] == val

    def test_mixed_batch(self):
        batch = [
            {"done": "done", "nMssn": 5},
            {"done": "cncl", "done_raw": "usrEnd"},
            {"done": "stuck", "pauseId": 18},
            {"done": "stuck", "pauseId": 0},
            {"done": "cncl"},
        ]
        records = self._make_raw_records(batch)
        assert [r["classified_result"] for r in records] == [
            "completed",
            "cancelled_by_user",
            "error_18",
            "stuck",
            "cancelled",
        ]
