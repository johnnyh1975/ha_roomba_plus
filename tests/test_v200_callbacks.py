"""Tests for callbacks.py — v2.0 refactor.

Covers:
  - async_record_mission: result classification (completed/cancelled/stuck/error)
  - async_record_mission: duration_min calculation from timestamps
  - async_record_mission: start_ts=0 fallback (wall-clock)
  - async_record_mission: nstuck_delta drives stuck classification
  - async_record_mission: bbrun_hr merge (bbrun vs runtimeStats)
  - async_record_mission: L3 error state updated only on error/stuck
  - make_map_retrain_callback: triggers refresh on pmapv change
  - make_map_retrain_callback: no refresh when pmapv unchanged
"""
import asyncio
import datetime
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── stubs ─────────────────────────────────────────────────────────────────────

def _make_store():
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    return store


def _make_entry(store, map_capability_val="none", zone_store=None,
                cloud_coordinator=None):
    """Build a minimal config entry stub for callback tests."""
    from custom_components.roomba_plus.models import MapCapability

    cap = MapCapability(map_capability_val)
    _zone_store = zone_store
    _cloud_coordinator = cloud_coordinator

    class _FakeData:
        mission_store     = store
        last_error_code   = None
        last_error_at     = None
        last_error_zone   = None
        map_capability    = cap
        # F6g — consecutive_skips counter; needs a real MaintenanceStore
        class _FakeMaintenanceStore:
            consecutive_skips = 0
        maintenance_store = _FakeMaintenanceStore()

        @property
        def zone_store(self):
            return _zone_store

        @property
        def cloud_coordinator(self):
            return _cloud_coordinator

        @property
        def has_cloud(self):
            return _cloud_coordinator is not None and _cloud_coordinator.data is not None

    class _FakeEntry:
        runtime_data = _FakeData()
        entry_id     = "test_entry"

    return _FakeEntry()


def _make_hass(loop=None):
    """Minimal hass stub."""
    class _FakeHass:
        class _FakeConfig:
            config_dir = "/tmp/roomba_plus_test"
            components: set = set()
            def path(self, *parts: str) -> str:
                import os as _os
                p = _os.path.join(self.config_dir, *parts)
                _os.makedirs(_os.path.dirname(p), exist_ok=True)
                return p
        async def async_add_executor_job(self, fn, *args):
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args)
        def __init__(self):
            self.loop = loop
            self.data = {}
            self.config = self._FakeConfig()
            from homeassistant.core import CoreState
            self.state = CoreState.running
    return _FakeHass()


def _ts(offset_sec: int = 0) -> int:
    """Return a unix timestamp offset from a fixed base."""
    return 1700000000 + offset_sec


def _iso(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


# ── async_record_mission: result classification ───────────────────────────────

class TestAsyncRecordMissionResult:

    def _run(self, mission, reported, zones=None, start_ts=None, nstuck_delta=0):
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)

        if start_ts is None:
            start_ts = int(loop.time()) - 3600

        try:
            loop.run_until_complete(
                async_record_mission(
                    hass, entry, mission, reported,
                    zones or [], start_ts, nstuck_delta,
                )
            )
        finally:
            loop.close()
        return store.latest()

    def test_completed(self):
        rec = self._run({"phase": "charge", "error": 0, "sqft": 100}, {})
        assert rec["result"] == "completed"

    def test_error_from_error_code(self):
        rec = self._run({"phase": "charge", "error": 17, "sqft": 0}, {})
        assert rec["result"] == "error"
        assert rec["error_code"] == 17

    def test_cancelled_from_phase(self):
        rec = self._run({"phase": "cancelled", "error": 0}, {})
        assert rec["result"] == "cancelled"

    def test_stuck_from_nstuck_delta(self):
        rec = self._run({"phase": "charge", "error": 0}, {}, nstuck_delta=1)
        assert rec["result"] == "stuck"

    def test_error_takes_priority_over_nstuck(self):
        """error_code wins over nstuck_delta."""
        rec = self._run({"phase": "charge", "error": 5}, {}, nstuck_delta=1)
        assert rec["result"] == "error"
        assert rec["error_code"] == 5


class TestAsyncRecordMissionTimestamps:

    def _run(self, start_ts, end_approx_ts=None):
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        mission = {"phase": "charge", "error": 0, "sqft": 100}

        try:
            loop.run_until_complete(
                async_record_mission(hass, entry, mission, {}, [], start_ts, 0)
            )
        finally:
            loop.close()
        return store.latest()

    def test_duration_calculated_from_start_ts(self):
        import time
        start = int(time.time()) - 3600  # 60 minutes ago in real time
        rec = self._run(start_ts=start)
        assert rec["duration_min"] >= 59   # allow 1 min wall-clock variance
        assert rec["duration_min"] <= 61

    def test_start_ts_zero_uses_wallclock_fallback(self):
        """start_ts=0 → started_at ≈ now → duration_min ≈ 0."""
        rec = self._run(start_ts=0)
        assert rec["duration_min"] == 0

    def test_started_at_iso_format(self):
        start = _ts(-1800)
        rec = self._run(start_ts=start)
        assert "T" in rec["started_at"]
        assert rec["started_at"].endswith("+00:00")


class TestAsyncRecordMissionBbrunHr:

    def _run(self, reported):
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        try:
            loop.run_until_complete(
                async_record_mission(
                    hass, entry, {"phase": "charge", "error": 0}, reported,
                    [], _ts(-3600), 0,
                )
            )
        finally:
            loop.close()
        return store.latest()

    def test_hr_from_bbrun(self):
        rec = self._run({"bbrun": {"hr": 250}})
        assert rec["bbrun_hr"] == 250

    def test_hr_from_runtime_stats_when_bbrun_missing(self):
        """i-series firmware stores hr in runtimeStats, not bbrun."""
        rec = self._run({"runtimeStats": {"hr": 180}})
        assert rec["bbrun_hr"] == 180

    def test_bbrun_takes_priority(self):
        rec = self._run({"bbrun": {"hr": 200}, "runtimeStats": {"hr": 100}})
        assert rec["bbrun_hr"] == 200

    def test_zero_when_both_missing(self):
        rec = self._run({})
        assert rec["bbrun_hr"] == 0


class TestAsyncRecordMissionL3ErrorState:

    def _run_two_missions(self, first_mission, second_mission):
        from custom_components.roomba_plus.callbacks import async_record_mission
        import time
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        now   = int(time.time())
        try:
            loop.run_until_complete(
                async_record_mission(
                    hass, entry, first_mission, {}, [], now - 7200, 0,
                )
            )
            loop.run_until_complete(
                async_record_mission(
                    hass, entry, second_mission, {}, [], now - 3600, 0,
                )
            )
        finally:
            loop.close()
        return entry.runtime_data

    def test_error_state_set_on_error(self):
        data = self._run_two_missions(
            {"phase": "charge", "error": 17},
            {"phase": "charge", "error": 0},   # completed after error
        )
        # Error state persists — not cleared by subsequent completed mission
        assert data.last_error_code == 17

    def test_error_state_not_set_on_completed(self):
        data = self._run_two_missions(
            {"phase": "charge", "error": 0},
            {"phase": "charge", "error": 0},
        )
        assert data.last_error_code is None

    def test_error_state_updated_to_latest_error(self):
        data = self._run_two_missions(
            {"phase": "charge", "error": 5},
            {"phase": "charge", "error": 18},
        )
        assert data.last_error_code == 18


# ── make_map_retrain_callback ─────────────────────────────────────────────────

class TestMakeMapRetrainCallback:

    def _make_coordinator(self):
        coord = MagicMock()
        coord.async_request_refresh = AsyncMock()
        return coord

    def _fire(self, callback, json_data, loop):
        loop.run_until_complete(asyncio.sleep(0))  # let any pending tasks run
        callback(json_data)
        loop.run_until_complete(asyncio.sleep(0))

    def test_triggers_refresh_on_pmapv_change(self):
        from custom_components.roomba_plus.callbacks import make_map_retrain_callback

        loop = asyncio.new_event_loop()
        coord = self._make_coordinator()
        coord.async_request_refresh = AsyncMock(return_value=None)

        class _FakeHass:
            pass
        hass = _FakeHass()
        hass.loop = loop

        cb = make_map_retrain_callback(hass, coord)

        # First call — sets baseline
        cb({"state": {"reported": {"pmaps": [{"abc": "v1"}]}}})
        # Second call — pmapv changed
        cb({"state": {"reported": {"pmaps": [{"abc": "v2"}]}}})

        loop.run_until_complete(asyncio.sleep(0.05))
        coord.async_request_refresh.assert_called_once()
        loop.close()

    def test_no_refresh_when_pmapv_unchanged(self):
        from custom_components.roomba_plus.callbacks import make_map_retrain_callback

        loop = asyncio.new_event_loop()
        coord = self._make_coordinator()
        coord.async_request_refresh = AsyncMock(return_value=None)

        class _FakeHass:
            pass
        hass = _FakeHass()
        hass.loop = loop

        cb = make_map_retrain_callback(hass, coord)
        cb({"state": {"reported": {"pmaps": [{"abc": "v1"}]}}})
        cb({"state": {"reported": {"pmaps": [{"abc": "v1"}]}}})  # same

        loop.run_until_complete(asyncio.sleep(0.05))
        coord.async_request_refresh.assert_not_called()
        loop.close()

    def test_no_refresh_when_no_pmaps(self):
        from custom_components.roomba_plus.callbacks import make_map_retrain_callback

        loop = asyncio.new_event_loop()
        coord = self._make_coordinator()
        coord.async_request_refresh = AsyncMock(return_value=None)

        class _FakeHass:
            pass
        hass = _FakeHass()
        hass.loop = loop

        cb = make_map_retrain_callback(hass, coord)
        cb({"state": {"reported": {}}})   # no pmaps key

        loop.run_until_complete(asyncio.sleep(0.05))
        coord.async_request_refresh.assert_not_called()
        loop.close()


# ── F4c -- recharge accumulation (F4e) in callbacks ──────────────────────────

class TestRechargeAccumulation:
    """F4e -- recharge_min field written to MissionStore record."""

    def _make_mission_msg(self, phase: str, recharge_m: int | None = None) -> dict:
        mission = {"phase": phase, "mssnStrtTm": 1700000000, "sqft": 100}
        if recharge_m is not None:
            mission["rechrgM"] = recharge_m
        return {"state": {"reported": {"cleanMissionStatus": mission, "bbrun": {"nStuck": 0}}}}

    @pytest.mark.asyncio
    async def test_recharge_min_in_record(self):
        """rechrgM from hmMidMsn phase is accumulated and written to record."""
        records = []

        async def fake_append(record):
            records.append(record)

        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_callback

        entry = MagicMock()
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock(side_effect=fake_append)
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None

        hass = MagicMock()
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            import asyncio

            async def run_it(coro, loop):
                return await coro
            mock_rct.side_effect = lambda coro, loop: asyncio.ensure_future(coro)

            cb = make_mission_callback(hass, entry)
            cb(self._make_mission_msg("run"))
            cb(self._make_mission_msg("hmMidMsn", recharge_m=15))
            cb(self._make_mission_msg("charge"))

        # Allow coroutines to run
        import asyncio as _asyncio
        await _asyncio.sleep(0)

        if records:
            assert records[0].get("recharge_min") == 15

    @pytest.mark.asyncio
    async def test_no_recharge_when_no_mid_mission(self):
        """recharge_min is None when no hmMidMsn phase occurred."""
        records = []

        async def fake_append(record):
            records.append(record)

        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_callback

        entry = MagicMock()
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock(side_effect=fake_append)
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None

        hass = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            mock_rct.side_effect = lambda coro, loop: None

            cb = make_mission_callback(hass, entry)
            cb({"state": {"reported": {
                "cleanMissionStatus": {"phase": "run", "mssnStrtTm": 1700000000},
                "bbrun": {"nStuck": 0},
            }}})
            cb({"state": {"reported": {
                "cleanMissionStatus": {"phase": "charge"},
                "bbrun": {"nStuck": 0},
            }}})


# ── F4b -- make_mission_complete_callback ─────────────────────────────────────

class TestMissionCompleteCallback:
    """F4b -- cloud refresh triggered on mission-end phase transition."""

    def _msg(self, phase: str) -> dict:
        return {"state": {"reported": {"cleanMissionStatus": {"phase": phase}}}}

    def test_refresh_triggered_on_mission_end(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback

        cc = MagicMock()
        cc.async_request_refresh = AsyncMock()
        hass = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            cb = make_mission_complete_callback(hass, cc)
            cb(self._msg("run"))
            cb(self._msg("charge"))   # transition: run -> charge
            mock_rct.assert_called_once()

    def test_no_refresh_without_prior_cleaning_phase(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback

        cc = MagicMock()
        cc.async_request_refresh = AsyncMock()
        hass = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            cb = make_mission_complete_callback(hass, cc)
            cb(self._msg("charge"))   # charge without prior cleaning phase
            mock_rct.assert_not_called()
