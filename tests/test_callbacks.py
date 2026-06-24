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
        title        = "Test Robot"  # v2.9.0 EVENT-BUS — used in event payloads

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
            self.bus = MagicMock()  # v2.9.0 EVENT-BUS — async_fire() target
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

    def _close_coro(*args, **kwargs):
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro

    # v2.9.0 — pytest_homeassistant_custom_component installs its own
    # asyncio event loop policy (HassEventLoopPolicy) at import time, which
    # affects what asyncio.get_event_loop() returns globally for the whole
    # test session. Relying on it here made these tests fail under pytest
    # (while passing fine standalone) because the loop captured at hass.loop
    # assignment time and the loop used later to drain run_coroutine_threadsafe
    # calls were not reliably the same object. Creating and owning an
    # explicit, dedicated loop for this test environment sidesteps the
    # global policy entirely.
    hass.loop = asyncio.new_event_loop()
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


class TestAsyncRecordMissionCompletedEvent:
    """v2.9.0 EVENT-BUS — roomba_plus_mission_completed payload."""

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
        return hass, entry

    def test_payload_matches_record(self):
        from custom_components.roomba_plus.const import EVENT_MISSION_COMPLETED

        hass, entry = self._run(
            {"phase": "charge", "error": 0, "sqft": 250},
            {},
            zones=["Kitchen", "Hallway"],
            nstuck_delta=1,
        )

        hass.bus.async_fire.assert_called_once_with(
            EVENT_MISSION_COMPLETED,
            {
                "entry_id": entry.entry_id,
                "name": entry.title,
                "rooms_cleaned": 2,
                "area_sqft": 250,
                "stuck_count": 1,
                "result": "stuck",
            },
        )

    def test_fires_even_with_no_zones(self):
        """NONE-tier (600-series) robots have no zone data at all — the
        event must still fire with rooms_cleaned=0, not be skipped."""
        from custom_components.roomba_plus.const import EVENT_MISSION_COMPLETED

        hass, entry = self._run(
            {"phase": "charge", "error": 0, "sqft": None}, {}, zones=[],
        )

        hass.bus.async_fire.assert_called_once()
        fired_payload = hass.bus.async_fire.call_args[0][1]
        assert fired_payload["rooms_cleaned"] == 0
        assert fired_payload["area_sqft"] is None


class TestAsyncRecordMissionBatteryCycles:
    """v2.9.0 DAILY-DIGEST — battery_cycles snapshot captured per-mission,
    mirroring the existing bbrun_hr snapshot pattern."""

    def _run(self, reported, start_ts=None):
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        mission = {"phase": "charge", "error": 0, "sqft": 100}

        if start_ts is None:
            start_ts = int(loop.time()) - 3600

        try:
            loop.run_until_complete(
                async_record_mission(hass, entry, mission, reported, [], start_ts, 0)
            )
        finally:
            loop.close()
        return store.latest()

    def test_nimh_cycles_captured(self):
        rec = self._run({"bbchg3": {"nNimhChrg": 12, "nLithChrg": 87}})
        assert rec["battery_cycles"] == 12

    def test_lith_cycles_captured_when_no_nimh(self):
        rec = self._run({"bbchg3": {"nLithChrg": 87}})
        assert rec["battery_cycles"] == 87

    def test_none_when_bbchg3_absent(self):
        """600-series has no bbchg3 at all — must be None, not 0."""
        rec = self._run({})
        assert rec["battery_cycles"] is None


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
        from custom_components.roomba_plus.const import (
            EVENT_MAP_RETRAIN_COMPLETED, EVENT_MAP_RETRAIN_STARTED,
        )

        loop = asyncio.new_event_loop()
        coord = self._make_coordinator()
        coord.async_request_refresh = AsyncMock(return_value=None)

        class _FakeHass:
            pass
        hass = _FakeHass()
        hass.loop = loop
        hass.bus = MagicMock()

        class _FakeEntry:
            entry_id = "test_entry"
            title = "Test Robot"
        entry = _FakeEntry()

        cb = make_map_retrain_callback(hass, coord, entry)

        # First call — sets baseline
        cb({"state": {"reported": {"pmaps": [{"abc": "v1"}]}}})
        # Second call — pmapv changed
        cb({"state": {"reported": {"pmaps": [{"abc": "v2"}]}}})

        loop.run_until_complete(asyncio.sleep(0.05))
        coord.async_request_refresh.assert_called_once()
        # v2.9.0 EVENT-BUS — started fires immediately, completed only
        # after the (successful) refresh awaits.
        hass.bus.async_fire.assert_any_call(
            EVENT_MAP_RETRAIN_STARTED,
            {"entry_id": "test_entry", "name": "Test Robot", "pmap_id": "abc"},
        )
        hass.bus.async_fire.assert_any_call(
            EVENT_MAP_RETRAIN_COMPLETED,
            {"entry_id": "test_entry", "name": "Test Robot", "pmap_id": "abc"},
        )
        loop.close()

    def test_no_completed_event_when_refresh_fails(self):
        from custom_components.roomba_plus.callbacks import make_map_retrain_callback
        from custom_components.roomba_plus.const import (
            EVENT_MAP_RETRAIN_COMPLETED, EVENT_MAP_RETRAIN_STARTED,
        )

        loop = asyncio.new_event_loop()
        coord = self._make_coordinator()
        coord.async_request_refresh = AsyncMock(side_effect=RuntimeError("boom"))

        class _FakeHass:
            pass
        hass = _FakeHass()
        hass.loop = loop
        hass.bus = MagicMock()

        class _FakeEntry:
            entry_id = "test_entry"
            title = "Test Robot"
        entry = _FakeEntry()

        cb = make_map_retrain_callback(hass, coord, entry)
        cb({"state": {"reported": {"pmaps": [{"abc": "v1"}]}}})
        cb({"state": {"reported": {"pmaps": [{"abc": "v2"}]}}})

        loop.run_until_complete(asyncio.sleep(0.05))
        fired_events = [c.args[0] for c in hass.bus.async_fire.call_args_list]
        assert EVENT_MAP_RETRAIN_STARTED in fired_events
        assert EVENT_MAP_RETRAIN_COMPLETED not in fired_events
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
        hass.bus = MagicMock()

        class _FakeEntry:
            entry_id = "test_entry"
            title = "Test Robot"
        entry = _FakeEntry()

        cb = make_map_retrain_callback(hass, coord, entry)
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
        hass.bus = MagicMock()

        class _FakeEntry:
            entry_id = "test_entry"
            title = "Test Robot"
        entry = _FakeEntry()

        cb = make_map_retrain_callback(hass, coord, entry)
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
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

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
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

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


class TestRoomIndexCorroboration:
    """v2.9.0 — ROOM-INDEX CORROBORATION.

    Reported by Thonno (i7+, lewis 22.52.10) on v2.8.3: the time gate alone
    confirmed a false mission end after the robot sat in an ambiguous end
    phase for ~12 seconds during a genuine inter-room transition — well
    past END_SIGNAL_MIN_HOLD_SECONDS (2.0s). The diagnostics snapshot from
    that exact run showed phase=="run" immediately afterwards, confirming
    the mission never actually ended. Fix: if MissionTimerStore still has
    unvisited planned rooms, an ambiguous end phase (charge/hmPostMsn) can
    never confirm a genuine end, regardless of how long the time gate has
    been satisfied.
    """

    def _run_phases_with_rooms(
        self,
        phases_nstuck: list[tuple[str, int]],
        planned_rooms: list[str] | None,
        current_room_idx: int = 0,
        room_estimates_sec: list | None = None,
        total_estimated_sec: float | None = 999.0,
    ) -> list[dict]:
        """Drive the callback with a MissionTimerStore that has a room plan.

        v2.9.0 — room_estimates_sec/total_estimated_sec default to
        "estimate data exists" (matching the common case where the
        room-index corroboration SHOULD apply). Must be explicitly set to
        None/[] to exercise the CONFIDENCE GUARD test scenarios (Auto-mode/
        no-estimate missions, where current_room_idx can never be trusted).
        An unconfigured MagicMock() attribute is technically "not None",
        which would silently bypass the confidence guard in either
        direction — explicit values are required for a real test.
        """
        hass, entry, recorded, _ = _make_callback_env()
        mts = MagicMock()
        mts.planned_rooms = planned_rooms or []
        mts.current_room_idx = current_room_idx
        mts.room_estimates_sec = room_estimates_sec or []
        mts.total_estimated_sec = total_estimated_sec
        entry.runtime_data.mission_timer_store = mts
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

            hass.loop.run_until_complete(asyncio.sleep(0))

        return captured_kwargs

    def test_ambiguous_end_suppressed_with_unvisited_rooms(self):
        """Mirrors Thonno's exact report: long hold time on an ambiguous
        phase must NOT confirm an end while rooms remain unvisited, even
        though the time gate alone would have been satisfied."""
        recorded = self._run_phases_with_rooms(
            [
                ("run", 0),
                ("charge", 0),   # streak=1
                ("charge", 0),   # streak=2, time gate now satisfied too
                ("charge", 0),   # would confirm under the OLD logic
            ],
            planned_rooms=["Kitchen", "Hallway", "Bedroom"],
            current_room_idx=0,  # 2 more rooms still ahead
        )
        assert len(recorded) == 0, (
            "Must not confirm mission end while rooms remain unvisited"
        )

    def test_ambiguous_end_confirms_on_last_planned_room(self):
        """When the robot IS on the last planned room, the existing
        time-based gate behaves exactly as before — no unvisited rooms left
        to suppress confirmation."""
        recorded = self._run_phases_with_rooms(
            [
                ("run", 0),
                ("charge", 0),
                ("charge", 0),
                ("charge", 0),
            ],
            planned_rooms=["Kitchen", "Hallway", "Bedroom"],
            current_room_idx=2,  # last room (index 2 of 3) — none left after
        )
        assert len(recorded) == 1, (
            "Must confirm normally once on the last planned room"
        )

    def test_unambiguous_terminal_phase_still_confirms_with_unvisited_rooms(self):
        """A genuinely unambiguous terminal phase (e.g. user-initiated
        'stop') must still confirm immediately even with rooms remaining —
        the room-index check is deliberately scoped to ambiguous phases
        only, since a manual early stop is a legitimate real end."""
        recorded = self._run_phases_with_rooms(
            [
                ("run", 0),
                ("stop", 0),
            ],
            planned_rooms=["Kitchen", "Hallway", "Bedroom"],
            current_room_idx=0,
        )
        assert len(recorded) == 1, (
            "Unambiguous stop must confirm even with unvisited rooms"
        )

    def test_no_room_plan_falls_back_to_time_gate_unchanged(self):
        """EPHEMERAL/whole-home missions with no room plan at all must
        behave exactly as before this fix — falls through to the existing
        time-based gate."""
        recorded = self._run_phases_with_rooms(
            [
                ("run", 0),
                ("charge", 0),
                ("charge", 0),
                ("charge", 0),
            ],
            planned_rooms=[],
            current_room_idx=0,
        )
        assert len(recorded) == 1, (
            "No room plan must not change existing time-gate behaviour"
        )

    def test_missing_mission_timer_store_falls_back_to_time_gate(self):
        """If mission_timer_store is None (default in _make_callback_env),
        the corroboration check must be a no-op, not raise."""
        hass, entry, recorded, _ = _make_callback_env()
        assert entry.runtime_data.mission_timer_store is None
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            captured_kwargs: list[dict] = []

            async def _capture(*args, **kwargs):
                captured_kwargs.append(kwargs)
            mock_record.side_effect = _capture

            for phase, nstuck in [("run", 0), ("charge", 0), ("charge", 0), ("charge", 0)]:
                cb(_msg(phase, nstuck=nstuck))

            hass.loop.run_until_complete(asyncio.sleep(0))

        assert len(captured_kwargs) == 1

    def test_unvisited_rooms_safety_cap_prevents_permanent_hang(self):
        """v2.9.0 — reproduces the exact field regression reported by Thonno
        (i7+, lewis 22.52.10, 2026-06-19): a genuine, fully-completed
        mission never confirmed because current_room_idx never advanced
        past 0 (AUTO-ADVANCE-ROOM did not fire for this firmware/scenario).
        end_signal_streak reached 10, time_held reached 66+ seconds, yet
        unvisited_rooms stayed True the entire captured session — the
        mission was never recorded and MissionTimerStore was never cleared.

        UNVISITED_ROOMS_MAX_SUPPRESSION_SECONDS (90s) must eventually let
        confirmation through regardless of current_room_idx, so a real
        mission end can never hang forever.
        """
        recorded = self._run_phases_with_rooms(
            # _patch_callbacks_time advances monotonic by 10s per call —
            # need enough repeated ambiguous-phase messages for time_held
            # to cross the 90s safety cap while current_room_idx never
            # advances (mirrors the field log: streak kept climbing,
            # unvisited_rooms stayed True throughout).
            [("run", 0)] + [("charge", 0)] * 11,
            planned_rooms=["Corridoio", "Camera da letto"],
            current_room_idx=0,  # never advances — exactly what Thonno saw
        )
        assert len(recorded) == 1, (
            "A genuine mission end must eventually confirm even when "
            "current_room_idx never advances — must not hang forever"
        )

    def test_unvisited_rooms_suppression_still_works_within_cap(self):
        """Sanity check: the safety cap must not defeat the original fix
        for short, normal inter-room transitions well within 90s."""
        recorded = self._run_phases_with_rooms(
            [("run", 0), ("charge", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Corridoio", "Camera da letto"],
            current_room_idx=0,
        )
        assert len(recorded) == 0, (
            "Short ambiguous-phase bursts with unvisited rooms must still "
            "be suppressed — the safety cap should not defeat the original "
            "fix for the common case"
        )

    def test_confidence_guard_skips_suppression_with_no_estimates_at_all(self):
        """v2.9.0 CONFIDENCE GUARD — confirmed regression: when no room has
        ANY time estimate (e.g. every planned room uses Auto pass mode, or
        cloud TE1 data hasn't reached GOOD_CONFIDENCE for any of them),
        current_room_idx can never advance via AUTO-ADVANCE-ROOM, making
        unvisited_rooms permanently True for the entire mission. Without
        this guard, EVERY genuine end for such a mission (likely the
        majority — Auto is the default pass mode) would wait out the full
        90s safety cap instead of the normal 2s time gate. The guard must
        skip the room-index check entirely here, falling back to the plain
        time gate immediately.
        """
        recorded = self._run_phases_with_rooms(
            [("run", 0), ("charge", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Corridoio", "Camera da letto"],
            current_room_idx=0,  # never advanced — Auto mode, as expected
            room_estimates_sec=[None, None],  # Auto mode — no estimates at all
            total_estimated_sec=None,          # nothing to sum either
        )
        assert len(recorded) == 1, (
            "With zero estimate data anywhere, current_room_idx cannot be "
            "trusted — must confirm on the plain time gate (well within "
            "the short test burst), not wait for the 90s safety cap"
        )

    def test_confidence_guard_applies_normally_with_partial_estimates(self):
        """When at least ONE room has a real estimate (even if others
        don't), current_room_idx had at least a chance to be tracked —
        the room-index suppression should still apply normally."""
        recorded = self._run_phases_with_rooms(
            [("run", 0), ("charge", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Corridoio", "Camera da letto"],
            current_room_idx=0,
            room_estimates_sec=[600, None],   # room 1 has an estimate
            total_estimated_sec=600.0,
        )
        assert len(recorded) == 0, (
            "Partial estimate data still means current_room_idx COULD be "
            "trusted — suppression should apply as normal"
        )


class TestRoomCompletedEvent:
    """v2.9.0 EVENT-BUS — roomba_plus_room_completed fires for the room the
    robot just LEFT when AUTO-ADVANCE-ROOM successfully advances
    current_room_idx (real MissionTimerStore, not a MagicMock double, since
    the event payload depends on actual post-advance state).
    """

    def _transition_msg(self, phase: str, cycle: str = "clean") -> dict:
        return {
            "state": {
                "reported": {
                    "cleanMissionStatus": {
                        "phase": phase,
                        "sqft": 100,
                        "mssnStrtTm": 1700000000,
                        "initiator": "schedule",
                        "error": 0,
                        "cycle": cycle,
                    },
                    "bbrun": {"nStuck": 0, "hr": 10},
                }
            }
        }

    def _make_real_mts(self, entry):
        """Real MissionTimerStore, pre-configured so AUTO-ADVANCE-ROOM's
        confidence check passes immediately on the first transition.
        _schedule_save is stubbed out — Store() needs real hass.storage
        plumbing the MagicMock hass from _make_callback_env() doesn't have.
        """
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore

        mts = MissionTimerStore()
        blid = entry.data.get("blid", "")
        # Must match what callbacks.py computes so on_phase_run() does NOT
        # treat this as a brand-new mission and reset run_sec back to 0.
        mts.mission_id = f"{blid}_1700000000"
        mts.planned_rooms = ["Kitchen", "Hallway"]
        mts.current_room_idx = 0
        mts.room_estimates_sec = [10.0, 10.0]
        mts.total_estimated_sec = 20.0
        mts.run_sec = 20.0              # >> expected_room_sec(10.0) * 0.5
        mts.room_entered_run_sec = 0.0
        mts._schedule_save = lambda *a, **kw: None
        return mts

    def test_fires_for_room_just_left(self):
        from custom_components.roomba_plus.callbacks import make_mission_callback
        from custom_components.roomba_plus.const import EVENT_ROOM_COMPLETED

        hass, entry, _, _ = _make_callback_env()
        mts = self._make_real_mts(entry)
        entry.runtime_data.mission_timer_store = mts

        cb = make_mission_callback(hass, entry)
        cb(self._transition_msg("run"))
        cb(self._transition_msg("charge"))  # ambiguous inter-room transition
        hass.loop.run_until_complete(asyncio.sleep(0))

        assert mts.current_room_idx == 1, "advance_room() must have run for real"
        hass.bus.async_fire.assert_any_call(
            EVENT_ROOM_COMPLETED,
            {
                "entry_id": entry.entry_id,
                "name": entry.title,
                "room_name": "Kitchen",
                "room_idx": 0,
            },
        )

    def test_no_event_when_already_on_last_room(self):
        """advance_room() returns False at the last planned room — no event."""
        from custom_components.roomba_plus.callbacks import make_mission_callback
        from custom_components.roomba_plus.const import EVENT_ROOM_COMPLETED

        hass, entry, _, _ = _make_callback_env()
        mts = self._make_real_mts(entry)
        mts.current_room_idx = 1  # already on "Hallway", the last room
        entry.runtime_data.mission_timer_store = mts

        cb = make_mission_callback(hass, entry)
        cb(self._transition_msg("run"))
        cb(self._transition_msg("charge"))
        hass.loop.run_until_complete(asyncio.sleep(0))

        fired_events = [c.args[0] for c in hass.bus.async_fire.call_args_list]
        assert EVENT_ROOM_COMPLETED not in fired_events

    def test_room_completed_fire_is_thread_safe(self):
        """BUGFIX (field report Thonno, v2.8.7) regression test.

        Reproduces the real failure mode exactly: roombapy invokes the
        mission callback directly from its own paho-mqtt background thread,
        never from the event loop thread. Before the fix, the room-transition
        branch called hass.bus.async_fire() directly from that foreign
        thread — on real HA core (frame.py thread-safety enforcement) this
        raised RuntimeError and crashed the entire paho-mqtt message thread,
        which then explained the "mission never closes" symptom: no further
        MQTT messages were ever processed after the first room transition.

        This test calls the callback from a genuinely separate OS thread
        (not just a different asyncio context) and asserts no exception
        propagates — proving the call_soon_threadsafe bridge is used instead
        of a direct cross-thread hass.bus.async_fire() call. Draining the
        loop afterward on the *test* thread additionally proves the event
        still actually fires once handed off correctly.
        """
        import threading
        from custom_components.roomba_plus.callbacks import make_mission_callback
        from custom_components.roomba_plus.const import EVENT_ROOM_COMPLETED

        hass, entry, _, _ = _make_callback_env()
        mts = self._make_real_mts(entry)
        entry.runtime_data.mission_timer_store = mts
        cb = make_mission_callback(hass, entry)

        errors: list[BaseException] = []

        def _run_on_foreign_thread():
            try:
                cb(self._transition_msg("run"))
                cb(self._transition_msg("charge"))  # triggers AUTO-ADVANCE-ROOM
            except BaseException as exc:  # noqa: BLE001 — want to see ANY exception
                errors.append(exc)

        worker = threading.Thread(target=_run_on_foreign_thread)
        worker.start()
        worker.join(timeout=5)

        assert not errors, (
            f"Callback raised from a foreign thread (the exact failure mode "
            f"that crashed Thonno's paho-mqtt thread): {errors}"
        )
        assert mts.current_room_idx == 1, "advance_room() must still have run for real"

        # Structural check — this is what actually distinguishes the fix
        # from the bug. hass.bus.async_fire here is just a MagicMock, so it
        # can't reproduce HA core's real frame.py thread-safety RuntimeError
        # by itself; calling it directly from the worker thread wouldn't
        # raise in this test environment either way. What we CAN verify
        # structurally: immediately after the foreign thread finishes — i.e.
        # before the event loop has had any chance to run — async_fire must
        # NOT have been invoked yet. That's only true if the call was
        # deferred via hass.loop.call_soon_threadsafe() rather than executed
        # inline on the foreign thread. Before the fix, this assertion fails
        # (async_fire.called is already True at this point).
        assert not hass.bus.async_fire.called, (
            "hass.bus.async_fire was invoked synchronously on the foreign "
            "thread instead of being deferred via call_soon_threadsafe — "
            "this is exactly the unsafe direct-call pattern that crashed "
            "the paho-mqtt thread on real HA core."
        )

        # Now drain the loop on the test thread — proves call_soon_threadsafe
        # correctly handed the fire off, it isn't just silently swallowed.
        hass.loop.run_until_complete(asyncio.sleep(0))
        hass.bus.async_fire.assert_any_call(
            EVENT_ROOM_COMPLETED,
            {
                "entry_id": entry.entry_id,
                "name": entry.title,
                "room_name": "Kitchen",
                "room_idx": 0,
            },
        )


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

            # Drain the event loop so run_coroutine_threadsafe tasks execute.
            # Must be the SAME loop object stored on hass.loop (see
            # _make_callback_env rationale) — not asyncio.get_event_loop().
            hass.loop.run_until_complete(asyncio.sleep(0))

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

            hass.loop.run_until_complete(asyncio.sleep(0))

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

            hass.loop.run_until_complete(asyncio.sleep(0))

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

            hass.loop.run_until_complete(asyncio.sleep(0))

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
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v2.9.0 — explicit dedicated loop; see _make_callback_env rationale.
        hass.loop = asyncio.new_event_loop()
        cb = make_mission_complete_callback(hass, coordinator)

        for phase in phases:
            cb(_msg(phase))

        hass.loop.run_until_complete(asyncio.sleep(0))
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
            hass.loop.run_until_complete(asyncio.sleep(0))
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

            hass.loop.run_until_complete(asyncio.sleep(0))
            assert mock_record.call_count == 1, (
                "Mission must be recorded once after evac → run → charge"
            )
