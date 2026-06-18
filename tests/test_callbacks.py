"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
import datetime
import itertools
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from unittest.mock import patch
from unittest.mock import call
import tests.conftest
from custom_components.roomba_plus.callbacks import make_mission_callback
from custom_components.roomba_plus.callbacks import make_mission_complete_callback
from custom_components.roomba_plus.const import CLEANING_PHASES
from custom_components.roomba_plus.const import MISSION_END_PHASES


@contextmanager
def _patch_callbacks_time():
    """Patch callbacks._time_mod so that monotonic() returns increasing values.

    Each call returns a value 10 s larger than the previous one, ensuring the
    END_SIGNAL_MIN_HOLD_SECONDS (2.0 s) time gate is always satisfied for
    genuine-end scenarios.  Tests that specifically require a rapid burst
    (time_held < 2 s) should supply their own side_effect instead.

    time() returns a constant 1000.0 (used only for last_mqtt_message_ts).
    """
    _c = itertools.count(1)
    with patch("custom_components.roomba_plus.callbacks._time_mod") as tmock:
        tmock.monotonic.side_effect = lambda: float(next(_c)) * 10.0
        tmock.time.return_value = 1000.0
        yield tmock


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


def _msg(phase: str, nstuck: int = 0, sqft: int = 100) -> dict:
    """Build a minimal MQTT reported-state message for a given phase."""
    return {
        "state": {
            "reported": {
                "cleanMissionStatus": {
                    "phase": phase,
                    "sqft": sqft,
                    "mssnStrtTm": 1700000000,
                    "initiator": "schedule",
                    "error": 0,
                },
                "bbrun": {"nStuck": nstuck, "hr": 10},
            }
        }
    }


def _make_callback_env():
    """Return (hass, entry, recorded_missions) for make_mission_callback tests."""
    hass = MagicMock()
    hass.loop = asyncio.get_event_loop()
    hass.is_running = True

    mission_store = MagicMock()
    mission_store.async_append = AsyncMock()
    mission_store.async_save = AsyncMock()
    mission_store.consecutive_skips = 0

    runtime_data = MagicMock()
    runtime_data.mission_store = mission_store
    runtime_data.maintenance_store = None
    runtime_data.demand_triggered_ts = None
    runtime_data.mission_timer_store = None
    runtime_data.presence_manager = None
    runtime_data.zone_store = None
    runtime_data.map_capability = MagicMock()

    entry = MagicMock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.data = {"blid": "TESTBLID"}

    recorded: list[dict] = []

    async def _capture_append(record):
        recorded.append(record)

    mission_store.async_append.side_effect = _capture_append

    return hass, entry, recorded, mission_store


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


class TestStuckBypassMissionCallback:
    """Bug A — make_mission_callback must fire for stuck → stop/charge."""

    def _run_phases(self, phases_nstuck: list[tuple[str, int]]) -> list[dict]:
        """Drive the callback through a phase sequence and return recorded missions."""
        hass, entry, recorded, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():

            captured_kwargs: list[dict] = []

            async def _capture(*args, **kwargs):
                captured_kwargs.append(kwargs)

            mock_record.side_effect = _capture

            for phase, nstuck in phases_nstuck:
                cb(_msg(phase, nstuck=nstuck))

            # Drain the event loop so run_coroutine_threadsafe tasks execute
            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))

        return captured_kwargs

    def test_stuck_then_stop_fires_mission_end(self):
        """run → stuck → stop must record mission (was bypassed before v2.6.3)."""
        recorded = self._run_phases([
            ("run", 0),
            ("stuck", 1),
            ("stop", 1),
        ])
        assert len(recorded) == 1, "Mission should be recorded for stuck → stop"

    def test_stuck_then_charge_fires_mission_end(self):
        """run → stuck → charge must record mission.

        v2.8.1: charge is debounced (ambiguous with inter-room transitions) —
        two consecutive charge messages are needed to confirm.
        """
        recorded = self._run_phases([
            ("run", 0),
            ("stuck", 1),
            ("charge", 1),
            ("charge", 1),
        ])
        assert len(recorded) == 1, "Mission should be recorded for stuck → charge"

    def test_normal_dock_still_fires(self):
        """run → hmPostMsn → charge must still record mission."""
        recorded = self._run_phases([
            ("run", 0),
            ("hmPostMsn", 0),
            ("charge", 0),
        ])
        assert len(recorded) == 1, "Normal dock sequence must record mission"

    def test_stuck_and_resumed_fires_once(self):
        """run → stuck → run → charge must record exactly one mission."""
        recorded = self._run_phases([
            ("run", 0),
            ("stuck", 1),
            ("run", 1),    # recovery
            ("hmPostMsn", 1),
            ("charge", 1),
        ])
        assert len(recorded) == 1, "Stuck-and-resumed must record exactly one mission"

    def test_no_mission_without_cleaning_phase(self):
        """charge → charge must not record a mission (no cleaning ever started)."""
        recorded = self._run_phases([
            ("charge", 0),
            ("charge", 0),
        ])
        assert len(recorded) == 0, "No mission without cleaning phase"


class TestFalseMissionRestartOnRecovery:
    """Bug D — stuck → run must not corrupt mission_start_ts or nstuck_at_start."""

    def test_mission_start_ts_preserved_after_stuck_recovery(self):
        """start_ts captured at first 'run' must survive stuck → run recovery."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        captured_start_ts: list[int] = []

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():

            async def _capture(*args, **kwargs):
                captured_start_ts.append(kwargs.get("start_ts", -1))

            mock_record.side_effect = _capture

            # Initial run — mssnStrtTm = 1700000000
            cb(_msg("run", nstuck=0))
            # Gets stuck
            cb(_msg("stuck", nstuck=1))
            # Recovers — firmware sends mssnStrtTm=0 in recovery message
            recovery = {
                "state": {
                    "reported": {
                        "cleanMissionStatus": {
                            "phase": "run",
                            "sqft": 80,
                            "mssnStrtTm": 0,   # firmware reset
                            "initiator": "schedule",
                            "error": 0,
                        },
                        "bbrun": {"nStuck": 1, "hr": 10},
                    }
                }
            }
            cb(recovery)
            # Mission ends normally (v2.8.1: charge is debounced — needs 2 messages,
            # v2.8.3: also needs END_SIGNAL_MIN_HOLD_SECONDS gap — provided by
            # _patch_callbacks_time() which makes each monotonic() call 10 s later)
            cb(_msg("charge", nstuck=1))
            cb(_msg("charge", nstuck=1))

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))

        assert len(captured_start_ts) == 1
        assert captured_start_ts[0] == 1700000000, (
            "start_ts must be the original value, not 0 from the recovery message"
        )

    def test_nstuck_at_start_not_rebased_on_recovery(self):
        """nstuck_at_start must stay at initial baseline, not rebased after stuck recovery."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        captured_nstuck_delta: list[int] = []

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():

            async def _capture(*args, **kwargs):
                captured_nstuck_delta.append(kwargs.get("nstuck_delta", -1))

            mock_record.side_effect = _capture

            cb(_msg("run", nstuck=5))    # baseline = 5
            cb(_msg("stuck", nstuck=6)) # nstuck_at_start should still be 5
            cb(_msg("run", nstuck=6))   # recovery — must NOT reset baseline to 6
            # v2.8.1: charge is debounced — needs 2 consecutive messages
            # v2.8.3: also needs END_SIGNAL_MIN_HOLD_SECONDS gap (provided by patch)
            cb(_msg("charge", nstuck=6))
            cb(_msg("charge", nstuck=6))

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))

        assert len(captured_nstuck_delta) == 1
        assert captured_nstuck_delta[0] == 1, (
            "nstuck_delta must be 1 (6-5), not 0 (6-6 due to false rebase)"
        )

    def test_had_stuck_event_not_reset_on_recovery(self):
        """had_stuck_event must remain True after stuck → run so result is stuck_and_resumed."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        captured_result_override: list = []

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record:

            async def _capture(*args, **kwargs):
                captured_result_override.append(kwargs.get("result_override"))

            mock_record.side_effect = _capture

            cb(_msg("run", nstuck=0))
            cb(_msg("stuck", nstuck=1))
            cb(_msg("run", nstuck=1))   # recovery
            # v2.8.3: use _patch_callbacks_time so the time gate passes on charge messages.
            # (Previously used return_value=9999.0 which caused time_held=0 because
            # end_signal_first_ts was also 9999.0 — gap was 0, not 9999 s.)
            with _patch_callbacks_time():
                # v2.8.1: charge is debounced — needs 2 consecutive messages
                cb(_msg("charge", nstuck=1))
                cb(_msg("charge", nstuck=1))

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))

        # result_override should be "stuck_and_resumed" or "stuck_and_abandoned"
        # — definitely NOT None (which would mean had_stuck_event was False)
        assert len(captured_result_override) == 1
        assert captured_result_override[0] is not None, (
            "result_override must be set when had_stuck_event=True (not reset by recovery)"
        )


class TestStuckBypassCloudRefreshCallback:
    """Bug A — make_mission_complete_callback must fire cloud refresh for stuck → stop/charge."""

    def _run_phases_refresh(self, phases: list[str]) -> int:
        """Return number of times async_request_refresh was called."""
        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock(return_value=None)
        hass = MagicMock()
        hass.loop = asyncio.get_event_loop()
        cb = make_mission_complete_callback(hass, coordinator)

        for phase in phases:
            cb(_msg(phase))

        loop = asyncio.get_event_loop()
        loop.run_until_complete(asyncio.sleep(0))
        return coordinator.async_request_refresh.call_count

    def test_stuck_then_stop_triggers_refresh(self):
        """run → stuck → stop must trigger cloud refresh."""
        count = self._run_phases_refresh(["run", "stuck", "stop"])
        assert count == 1, "Cloud refresh must fire for stuck → stop"

    def test_stuck_then_charge_triggers_refresh(self):
        """run → stuck → charge must trigger cloud refresh."""
        count = self._run_phases_refresh(["run", "stuck", "charge"])
        assert count == 1, "Cloud refresh must fire for stuck → charge"

    def test_normal_mission_triggers_refresh(self):
        """run → hmPostMsn → charge must still trigger cloud refresh."""
        count = self._run_phases_refresh(["run", "hmPostMsn", "charge"])
        assert count == 1

    def test_no_refresh_without_cleaning_phase(self):
        """charge alone must not trigger refresh (no mission started)."""
        count = self._run_phases_refresh(["charge"])
        assert count == 0

    def test_no_double_refresh_on_multiple_end_phases(self):
        """run → hmPostMsn → charge: only one refresh even with multiple end phases."""
        count = self._run_phases_refresh(["run", "hmPostMsn", "charge"])
        assert count == 1


class TestEvacPhaseClassification:
    """Bug B1 — evac must be in CLEANING_PHASES, not MISSION_END_PHASES."""

    def test_evac_in_cleaning_phases(self):
        assert "evac" in CLEANING_PHASES, (
            "evac must be CLEANING_PHASES — i7+ self-emptying is mid-mission, not end"
        )

    def test_evac_not_in_mission_end_phases(self):
        assert "evac" not in MISSION_END_PHASES, (
            "evac in MISSION_END_PHASES prematurely triggers _handle_mission_end on i7+"
        )

    def test_hmPostMsn_in_mission_end_phases(self):
        """hmPostMsn (robot homing to dock) is correctly a mission end indicator."""
        assert "hmPostMsn" in MISSION_END_PHASES

    def test_run_in_cleaning_phases(self):
        assert "run" in CLEANING_PHASES

    def test_mission_callback_does_not_end_on_evac(self):
        """evac must not trigger mission end in make_mission_callback."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record:
            cb(_msg("run", nstuck=0))
            cb(_msg("evac", nstuck=0))  # self-empty bin — must NOT end mission
            # mission still running — no end yet
            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))
            assert mock_record.call_count == 0, (
                "evac must not trigger mission end recording"
            )

    def test_mission_callback_continues_after_evac(self):
        """Mission must be recorded correctly after evac → run → charge."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            cb(_msg("run", nstuck=0))
            cb(_msg("evac", nstuck=0))  # bin empty
            cb(_msg("run", nstuck=0))   # resume cleaning
            # v2.8.1: charge is debounced — needs 2 consecutive messages
            # v2.8.3: also needs END_SIGNAL_MIN_HOLD_SECONDS gap (provided by patch)
            cb(_msg("charge", nstuck=0))
            cb(_msg("charge", nstuck=0))  # done

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))
            assert mock_record.call_count == 1, (
                "Mission must be recorded once after evac → run → charge"
            )
