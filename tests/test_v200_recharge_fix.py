"""Regression tests for mid-mission recharge sensor — static value bug fix.

Bug (Thonno, i7+, v2.0.0):
  sensor.mission_recharge_minutes is populated with the correct initial value
  (e.g. 78 minutes) but does not decrement during the recharge period.
  The iRobot app shows the countdown correctly.

Root cause — two independent problems:

1. NO PERIODIC REFRESH (the actual cause of Thonno's i7 bug):
   During a mid-mission recharge the robot sends cleanMissionStatus once at
   recharge start, then goes silent until charging completes.  The sensor
   only updates on MQTT arrival, so even though rechrgTm is set correctly,
   HA never re-reads it.  The value freezes at the initial reading.
   Fix: 60-second async_track_time_interval tick in RoombaSensor for
   mission_recharge_minutes and mission_expire_minutes.

   Thonno's robot (i7, lewis firmware) sends rechrgM=0 and rechrgTm (Unix
   end-timestamp). The v2.0.0 helper already computed the correct initial
   value from rechrgTm, but without the tick it never decremented.

2. WRONG PRIORITY FOR 900-SERIES (defensive fix for other users):
   On 900/980-series robots, rechrgM is a static snapshot sent once at
   recharge start and never updated by subsequent MQTT messages.  rechrgTm
   is also sent and is the authoritative end-timestamp.  The old helper
   returned rechrgM directly when > 0, bypassing rechrgTm.
   Fix: always prefer rechrgTm (self-decrementing via now()); fall back to
   rechrgM only when rechrgTm is absent (very old firmware).

Same fix applied to mission_expire_minutes / expireTm.
"""
import datetime
import pytest
from unittest.mock import patch, MagicMock


def _utcnow_returning(ts: int):
    """Return a context manager that freezes dt_util.utcnow() to ts."""
    frozen = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return patch(
        "custom_components.roomba_plus.sensor.dt_util.utcnow",
        return_value=frozen,
    )


class TestRechargeMinutesRemainingHelper:
    """_recharge_minutes_remaining: timestamp-first logic for all firmware."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _recharge_minutes_remaining
        with _utcnow_returning(now_ts):
            return _recharge_minutes_remaining(mission)

    # ── i7 / lewis firmware path (Thonno's robot) ────────────────────────────
    # rechrgM=0, rechrgTm set — this was already handled in v2.0.0.
    # The freeze bug was caused by the missing periodic tick, not this function.

    def test_lewis_computes_from_rechrgTm(self):
        """i7 (lewis): rechrgM=0, rechrgTm set → compute remaining minutes."""
        # rechrgTm=1780150205, now=1780150000 → 205 seconds → 3 minutes (rounded)
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780150000)
        assert result == 3

    def test_lewis_field_diagnostics_case(self):
        """Exact values from Bogdana diagnostics (i755840) — 277s remaining → 5 min."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780149928)
        assert result == 5

    def test_lewis_returns_none_when_rechrgTm_in_past(self):
        """rechrgTm expired → recharge done → None."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_lewis_returns_minimum_one_minute(self):
        """< 30 seconds remaining rounds to 1 min, not 0."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150020}, now_ts=1780150000)
        assert result == 1

    def test_lewis_returns_none_when_rechrgTm_zero(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    # ── 900-series / rechrgTm-priority fix ───────────────────────────────────
    # On 900/980-series, rechrgM is a static snapshot; rechrgTm is authoritative.
    # The old code returned rechrgM directly, which never decremented.

    def test_900_prefers_rechrgTm_over_static_rechrgM(self):
        """900-series: rechrgTm is preferred; static rechrgM is ignored."""
        # rechrgTm=1780150600, now=1780150000 → 600s → 10 min
        # Old code would have returned rechrgM=78 (static, wrong)
        result = self._call({"rechrgM": 78, "rechrgTm": 1780150600}, now_ts=1780150000)
        assert result == 10

    def test_900_series_value_decrements_over_time(self):
        """Demonstrate that rechrgTm-based value decrements, rechrgM-based would not."""
        recharge_end_ts = 1780150000 + 78 * 60  # end = now + 78 min
        # At t=0: both approaches agree
        result_t0 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts}, now_ts=1780150000
        )
        assert result_t0 == 78
        # At t+30min: rechrgTm gives 48, old static rechrgM would give 78 (frozen)
        result_t30 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts},
            now_ts=1780150000 + 30 * 60,
        )
        assert result_t30 == 48  # correctly decremented

    # ── Fallback: very old firmware (rechrgTm absent) ─────────────────────────

    def test_fallback_to_rechrgM_when_rechrgTm_zero(self):
        """rechrgTm absent / zero → fall back to rechrgM (old firmware)."""
        result = self._call({"rechrgM": 15, "rechrgTm": 0})
        assert result == 15

    def test_both_zero_returns_none(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None

    def test_none_values(self):
        assert self._call({"rechrgM": None, "rechrgTm": None}) is None


class TestExpireMinutesRemainingHelper:
    """_expire_minutes_remaining: same timestamp-first logic."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _expire_minutes_remaining
        with _utcnow_returning(now_ts):
            return _expire_minutes_remaining(mission)

    def test_prefers_expireTm_over_expireM(self):
        result = self._call({"expireM": 30, "expireTm": 1780150600}, now_ts=1780150000)
        assert result == 10   # 600s → 10 min, not static expireM=30

    def test_lewis_computes_from_expireTm(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780150000)
        assert result == 8   # 482s → 8 min

    def test_lewis_field_diagnostics_case(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780149928)
        assert result == 9   # 554s → 9 min

    def test_expired_returns_none(self):
        result = self._call({"expireM": 30, "expireTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_fallback_to_expireM_when_expireTm_zero(self):
        result = self._call({"expireM": 30, "expireTm": 0})
        assert result == 30

    def test_both_zero_returns_none(self):
        assert self._call({"expireM": 0, "expireTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None


class TestSensorDescriptionsUseHelpers:
    """Verify SENSORS tuple delegates to the fixed helpers."""

    def test_recharge_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_recharge_minutes"), None)
        assert desc is not None

    def test_expire_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_expire_minutes"), None)
        assert desc is not None

    def test_recharge_sensor_lewis_computes_from_rechrgTm(self):
        """End-to-end lewis path: value_fn computes from rechrgTm (Thonno's i7)."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")

        class _FakeEntity:
            clean_mission_status = {"rechrgM": 0, "rechrgTm": 1780150300}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 5   # 300s → 5 min

    def test_recharge_sensor_900_prefers_rechrgTm(self):
        """End-to-end 900-series path: rechrgTm preferred over static rechrgM."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")

        class _FakeEntity:
            clean_mission_status = {"rechrgM": 78, "rechrgTm": 1780150600}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 10   # 600s → 10 min, not 78 (static rechrgM)

    def test_expire_sensor_decrements_via_expireTm(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_expire_minutes")

        class _FakeEntity:
            clean_mission_status = {"expireM": 30, "expireTm": 1780150600}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 10   # 600s → 10 min, not 30 (static expireM)


class TestRoombaSensorPeriodicTick:
    """RoombaSensor registers a 60-second tick for countdown sensors.

    This is the primary fix for Thonno's i7 bug: without the tick, the sensor
    value freezes after the initial MQTT push because the robot goes silent
    during charging.
    """

    def test_tick_sensors_constant_includes_recharge(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        assert "mission_recharge_minutes" in RoombaSensor._TICK_SENSORS

    def test_tick_sensors_constant_includes_expire(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        assert "mission_expire_minutes" in RoombaSensor._TICK_SENSORS

    def test_non_countdown_sensors_not_in_tick_set(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        for key in ("battery", "phase", "filter_remaining_hours", "mission_id"):
            assert key not in RoombaSensor._TICK_SENSORS

    @pytest.mark.asyncio
    async def test_async_will_remove_cancels_tick(self):
        """async_will_remove_from_hass cancels the tick and clears _unsub_tick."""
        from custom_components.roomba_plus.sensor import RoombaSensor, SENSORS

        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")
        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = desc

        cancelled = []
        sensor._unsub_tick = lambda: cancelled.append(True)

        await RoombaSensor.async_will_remove_from_hass(sensor)

        assert len(cancelled) == 1
        assert sensor._unsub_tick is None

    @pytest.mark.asyncio
    async def test_will_remove_is_safe_when_no_tick(self):
        """async_will_remove_from_hass is a no-op when _unsub_tick is None."""
        from custom_components.roomba_plus.sensor import RoombaSensor, SENSORS

        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")
        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = desc
        sensor._unsub_tick = None

        # Should not raise
        await RoombaSensor.async_will_remove_from_hass(sensor)
        assert sensor._unsub_tick is None
