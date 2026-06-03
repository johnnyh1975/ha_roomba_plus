"""Tests for v2.0 cloud raw sensors.

Covers all six new CloudRawSensor value functions:
  - recent_completion_rate
  - recent_recharges
  - recent_evacuations
  - recent_dirt_events
  - recent_error_code
  - recent_error_time

Tests use the value functions directly — no HA entity lifecycle needed.
"""
import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────

def _rec(done="done", done_raw="done", pause_id=0, chrgs=0, evacs=0,
         dirt=0, timestamp=1700000000, classified=None):
    """Build a minimal raw record dict with classified_result pre-computed."""
    r = {
        "done":      done,
        "done_raw":  done_raw,
        "pauseId":   pause_id,
        "chrgs":     chrgs,
        "evacs":     evacs,
        "dirt":      dirt,
        "timestamp": timestamp,
    }
    if classified is None:
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        classified = classify_mission_result(r)
    r["classified_result"] = classified
    return r


# ── recent_completion_rate ────────────────────────────────────────────────────

class TestRecentCompletionRate:

    def test_all_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 4
        assert _raw_completion_rate(records) == 100.0

    def test_half_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 2 + [_rec(done="stuck", pause_id=17)] * 2
        assert _raw_completion_rate(records) == 50.0

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        assert _raw_completion_rate([]) is None

    def test_rounded_to_one_decimal(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        # 2 of 3 = 66.666...% → 66.7
        records = [_rec(done="done")] * 2 + [_rec(done="stuck")]
        assert _raw_completion_rate(records) == 66.7

    def test_zero_percent(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="stuck")] * 3
        assert _raw_completion_rate(records) == 0.0


# ── recent_recharges ──────────────────────────────────────────────────────────

class TestRecentRecharges:

    def test_sums_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=2), _rec(chrgs=1), _rec(chrgs=0)]
        assert _raw_recharges(records) == 3

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        assert _raw_recharges([]) is None

    def test_zero_recharges(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=0)] * 5
        assert _raw_recharges(records) == 0

    def test_handles_missing_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [{"done": "done", "classified_result": "completed"}]
        assert _raw_recharges(records) == 0


# ── recent_evacuations ────────────────────────────────────────────────────────

class TestRecentEvacuations:

    def test_sums_evacs(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=1), _rec(evacs=3), _rec(evacs=0)]
        assert _raw_evacuations(records) == 4

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        assert _raw_evacuations([]) is None

    def test_zero_evacuations(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=0)] * 3
        assert _raw_evacuations(records) == 0


# ── recent_dirt_events ────────────────────────────────────────────────────────

class TestRecentDirtEvents:

    def test_sums_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=5), _rec(dirt=10), _rec(dirt=2)]
        assert _raw_dirt_events(records) == 17

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        assert _raw_dirt_events([]) is None

    def test_zero_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=0)] * 4
        assert _raw_dirt_events(records) == 0


# ── recent_error_code ─────────────────────────────────────────────────────

class TestCloudLastErrorCode:

    def test_returns_pause_id_from_first_error(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="done"),                          # completed — skip
            _rec(done="stuck", pause_id=17),            # error_17 — match
            _rec(done="stuck", pause_id=18),            # older error — not used
        ]
        assert _raw_cloud_last_error_code(records) == 17

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_code(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        assert _raw_cloud_last_error_code([]) is None

    def test_stuck_with_pause_id_zero_returns_none(self):
        """stuck + pauseId=0 → classified as 'stuck', no specific code."""
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=0)]
        # classified_result = "stuck", pause_id=0 → None
        assert _raw_cloud_last_error_code(records) is None

    def test_error_224_smart_map(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=224)]
        assert _raw_cloud_last_error_code(records) == 224

    def test_skips_cancelled_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="cncl", done_raw="usrEnd"),       # cancelled_by_user — skip
            _rec(done="cncl"),                           # cancelled — skip
            _rec(done="stuck", pause_id=6),             # error_6 — match
        ]
        assert _raw_cloud_last_error_code(records) == 6


# ── recent_error_time ─────────────────────────────────────────────────────

class TestCloudLastErrorTime:

    def test_returns_datetime_from_timestamp(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="stuck", pause_id=17, timestamp=1700000000)]
        result = _raw_cloud_last_error_time(records)
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.year == 2023
        assert result.tzinfo == datetime.timezone.utc

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="done", timestamp=1700000000)] * 3
        assert _raw_cloud_last_error_time(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        assert _raw_cloud_last_error_time([]) is None

    def test_uses_most_recent_error(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [
            _rec(done="stuck", pause_id=17, timestamp=1700010000),
            _rec(done="stuck", pause_id=18, timestamp=1700000000),
        ]
        result = _raw_cloud_last_error_time(records)
        expected = datetime.datetime.fromtimestamp(1700010000, tz=datetime.timezone.utc)
        assert result == expected


# ── cloud_last_error attributes ───────────────────────────────────────────────

class TestCloudLastErrorAttrs:

    def test_returns_catalogue_fields(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=17)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs["error_code"] == 17
        assert attrs["source"] == "cloud_pauseId"
        assert "label" in attrs
        assert "description" in attrs
        assert "action" in attrs

    def test_empty_when_no_errors(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_attrs(records) == {}

    def test_error_code_none_for_stuck_no_pause_id(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=0)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs.get("error_code") is None
