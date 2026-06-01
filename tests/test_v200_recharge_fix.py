"""Regression tests for mid-mission recharge sensor on lewis firmware (i/s/j-series).

Bug: sensor.mission_recharge_minutes was always unavailable on i7/s9/j-series
     because lewis firmware sets rechrgM=0 and instead sends rechrgTm (Unix
     timestamp). The sensor only read rechrgM and returned None.

Fix: fall back to computing remaining minutes from rechrgTm - utcnow() when
     rechrgM=0. Same fix applied to mission_expire_minutes / expireTm.

Confirmed from field diagnostics (Bogdana, i755840, lewis+22.52.10):
  rechrgM=0, rechrgTm=1780150205, expireM=0, expireTm=1780150482
  phase=charge, cycle=clean  → binary_sensor ON, minutes sensor was unknown
"""
import datetime
import pytest
from unittest.mock import patch


def _utcnow_returning(ts: int):
    """Return a context manager that freezes dt_util.utcnow() to ts."""
    frozen = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return patch(
        "custom_components.roomba_plus.sensor.dt_util.utcnow",
        return_value=frozen,
    )


class TestRechargeminutesRemainingHelper:
    """_recharge_minutes_remaining: both firmware paths."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _recharge_minutes_remaining
        with _utcnow_returning(now_ts):
            return _recharge_minutes_remaining(mission)

    # ── 980/900-series path (rechrgM pre-computed) ────────────────────────────

    def test_980_uses_rechrgm_directly(self):
        assert self._call({"rechrgM": 15, "rechrgTm": 0}) == 15

    def test_980_returns_none_when_rechrgm_zero(self):
        """rechrgM=0 and no rechrgTm → not recharging."""
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    def test_980_rechrgm_takes_priority_over_rechrgTm(self):
        """When rechrgM > 0, use it — don't also compute from timestamp."""
        assert self._call({"rechrgM": 10, "rechrgTm": 1780150205}) == 10

    # ── lewis firmware path (rechrgTm Unix timestamp) ─────────────────────────

    def test_lewis_computes_from_rechrgTm(self):
        """rechrgM=0, rechrgTm set → compute remaining minutes from timestamp."""
        # rechrgTm=1780150205, now=1780150000 → 205 seconds → 3 minutes (rounded)
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780150000)
        assert result == 3

    def test_lewis_field_diagnostics_case(self):
        """Exact values from Bogdana diagnostics — 277 seconds remaining → 5 min."""
        # rechrgTm=1780150205, now=1780149928 (277s before) → 5 min
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780149928)
        assert result == 5

    def test_lewis_returns_none_when_rechrgTm_expired(self):
        """rechrgTm in the past → recharge done → return None."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_lewis_returns_minimum_one_minute(self):
        """< 30 seconds remaining rounds to 1 min, not 0."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150020}, now_ts=1780150000)
        assert result == 1

    def test_lewis_returns_none_when_rechrgTm_zero(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    def test_lewis_handles_missing_fields(self):
        assert self._call({}) is None

    def test_lewis_handles_none_values(self):
        assert self._call({"rechrgM": None, "rechrgTm": None}) is None


class TestExpireMinutesRemainingHelper:
    """_expire_minutes_remaining: both firmware paths."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _expire_minutes_remaining
        with _utcnow_returning(now_ts):
            return _expire_minutes_remaining(mission)

    def test_980_uses_expirem_directly(self):
        assert self._call({"expireM": 30, "expireTm": 0}) == 30

    def test_980_returns_none_when_zero(self):
        assert self._call({"expireM": 0, "expireTm": 0}) is None

    def test_lewis_computes_from_expireTm(self):
        # expireTm=1780150482, now=1780150000 → 482s → 8 min
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780150000)
        assert result == 8

    def test_lewis_field_diagnostics_case(self):
        """Exact expireTm from Bogdana diagnostics."""
        # expireTm=1780150482, now=1780149928 → 554s → 9 min
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780149928)
        assert result == 9

    def test_lewis_expired_returns_none(self):
        result = self._call({"expireM": 0, "expireTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_lewis_missing_fields(self):
        assert self._call({}) is None


class TestSensorDescriptionsUseHelpers:
    """Verify SENSORS tuple uses the new helper functions (not inline lambdas)."""

    def test_recharge_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_recharge_minutes"), None)
        assert desc is not None

    def test_expire_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_expire_minutes"), None)
        assert desc is not None

    def test_recharge_minutes_returns_value_from_rechrgTm(self):
        """End-to-end: sensor description value_fn uses helper correctly."""
        from custom_components.roomba_plus.sensor import SENSORS, _recharge_minutes_remaining

        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")

        # Verify the value_fn delegates to _recharge_minutes_remaining
        class _FakeEntity:
            clean_mission_status = {"rechrgM": 0, "rechrgTm": 1780150300}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 5   # 300s → 5 min

    def test_expire_minutes_returns_value_from_expireTm(self):
        from custom_components.roomba_plus.sensor import SENSORS

        desc = next(s for s in SENSORS if s.key == "mission_expire_minutes")

        class _FakeEntity:
            clean_mission_status = {"expireM": 0, "expireTm": 1780150600}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 10   # 600s → 10 min
