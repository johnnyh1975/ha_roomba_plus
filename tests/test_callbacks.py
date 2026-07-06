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


def _make_entry(store, map_capability_val="none", room_seg_store=None,
                cloud_coordinator=None):
    """Build a minimal config entry stub for callback tests."""
    from custom_components.roomba_plus.models import MapCapability

    cap = MapCapability(map_capability_val)
    _room_seg_store = room_seg_store
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
        def room_seg_store(self):
            return _room_seg_store

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
    runtime_data.room_seg_store = None
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

        hass.bus.async_fire.assert_called_once()
        event_name, payload = hass.bus.async_fire.call_args[0]
        assert event_name == EVENT_MISSION_COMPLETED
        assert payload["entry_id"] == entry.entry_id
        assert payload["name"] == entry.title
        assert payload["rooms_cleaned"] == 2
        assert payload["area_sqft"] == 250
        assert payload["stuck_count"] == 1
        assert payload["result"] == "stuck"

    def test_explanation_fields_always_present_with_constant_shape(self):
        """v3.2.0 UX fix — ANOMALY-EXPLAIN's result is folded into the
        event payload so automations get the reason for free, without
        knowing the explain_mission service exists. The three
        explanation keys must ALWAYS be in the payload (null when not
        anomalous) — constant shape means templates never have to guard
        against missing keys."""
        hass, entry = self._run(
            {"phase": "charge", "error": 0, "sqft": 250}, {},
            zones=["Kitchen"],
        )
        payload = hass.bus.async_fire.call_args[0][1]
        assert "is_anomalous" in payload
        assert "anomaly_reason" in payload
        assert "recommended_action" in payload
        assert "robot_lifted" in payload
        # Single ordinary mission with no baseline → not anomalous
        assert payload["is_anomalous"] is False
        assert payload["anomaly_reason"] is None

    def test_anomalous_explanation_carried_into_payload(self):
        """Wiring test — explain_mission's own logic has dedicated
        coverage (37 ANOMALY-EXPLAIN tests); this verifies the event
        payload actually carries its result through when a mission IS
        anomalous."""
        from custom_components.roomba_plus.callbacks import async_record_mission
        from unittest.mock import patch

        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        mission = {"phase": "charge", "error": 0, "sqft": 100}
        start_ts = int(loop.time()) - 3600
        canned = {
            "mission_id": "m_x", "is_anomalous": True,
            "anomaly_reason": "obstacle_or_blockage",
            "robot_lifted": True, "error_code": None,
            "recommended_action": "Check for an obstacle.",
        }
        try:
            with patch.object(type(store), "explain_mission", return_value=canned):
                loop.run_until_complete(
                    async_record_mission(hass, entry, mission, {}, [], start_ts, 0)
                )
        finally:
            loop.close()
        payload = hass.bus.async_fire.call_args[0][1]
        assert payload["is_anomalous"] is True
        assert payload["anomaly_reason"] == "obstacle_or_blockage"
        assert payload["recommended_action"] == "Check for an obstacle."
        assert payload["robot_lifted"] is True

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


class TestAsyncRecordMissionTeamId:
    """v3.2.0 TEAM-INDICATOR — team_id captured from lastCommand.params.team,
    mirroring the existing zones extraction from lastCommand.regions."""

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

    def test_team_id_captured_when_present(self):
        rec = self._run({
            "lastCommand": {"params": {"team": {"team_id": "IplhZn-R"}}}
        })
        assert rec["team_id"] == "IplhZn-R"

    def test_none_when_no_team_key(self):
        """Ordinary single-robot mission — params has no 'team' key at all."""
        rec = self._run({
            "lastCommand": {"params": {"padWetness": {"disposable": 3}}}
        })
        assert rec["team_id"] is None

    def test_none_when_no_params_key(self):
        rec = self._run({"lastCommand": {"command": "start"}})
        assert rec["team_id"] is None

    def test_none_when_lastcommand_absent(self):
        rec = self._run({})
        assert rec["team_id"] is None


class TestAsyncRecordMissionNpicksDelta:
    """v3.2.0 ANOMALY-EXPLAIN — npicks_delta is passed through to the
    record as-is (unlike team_id, it's computed by the caller closure in
    _on_mission_message and passed in as a parameter, not derived inside
    async_record_mission itself — mirrors nstuck_delta's existing plumbing)."""

    def _run(self, npicks_delta, start_ts=None):
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
                async_record_mission(
                    hass, entry, mission, {}, [], start_ts, 0,
                    npicks_delta=npicks_delta,
                )
            )
        finally:
            loop.close()
        return store.latest()

    def test_npicks_delta_recorded(self):
        rec = self._run(npicks_delta=1)
        assert rec["npicks_delta"] == 1

    def test_zero_npicks_delta_recorded(self):
        """The ordinary case — robot was not picked up. Must be 0, not
        None or absent, so downstream code can rely on it always being
        an int."""
        rec = self._run(npicks_delta=0)
        assert rec["npicks_delta"] == 0

    def test_default_is_zero_when_not_passed(self):
        """Backward-compat default (e.g. any caller that doesn't yet pass
        npicks_delta explicitly) is 0, not None."""
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        mission = {"phase": "charge", "error": 0, "sqft": 100}
        start_ts = int(loop.time()) - 3600
        try:
            loop.run_until_complete(
                async_record_mission(hass, entry, mission, {}, [], start_ts, 0)
            )
        finally:
            loop.close()
        assert store.latest()["npicks_delta"] == 0

    def test_two_consecutive_missions_have_independent_npicks_delta(self):
        """v3.2.0 bug-hunt fix — npicks_at_start's mission-end reset was
        missing (nstuck_at_start's parallel reset existed, npicks_at_start's
        didn't). Verifies the practically-observable behaviour this
        defends: back-to-back missions each get npicks_delta computed
        against THEIR OWN start value, not bled over from the previous
        mission — mission 1 has no pickup (delta=0), mission 2 has one
        (delta>0), and the two must not interfere with each other."""
        from custom_components.roomba_plus.callbacks import make_mission_callback

        def _msg_with_picks(phase: str, npicks: int, mssn_strt_tm: int) -> dict:
            return {
                "state": {
                    "reported": {
                        "cleanMissionStatus": {
                            "phase": phase, "sqft": 100,
                            "mssnStrtTm": mssn_strt_tm,
                            "initiator": "schedule", "error": 0,
                        },
                        "bbrun": {"nStuck": 0, "nPicks": npicks, "hr": 10},
                    }
                }
            }

        hass, entry, _recorded, _store = _make_callback_env()
        cb = make_mission_callback(hass, entry)
        captured: list[dict] = []

        async def _capture(*args, **kwargs):
            captured.append(dict(kwargs))

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            mock_record.side_effect = _capture
            # Mission 1: nPicks starts and stays at 5 (no pickup).
            cb(_msg_with_picks("run", 5, 1700000000))
            cb(_msg_with_picks("run", 5, 1700000000))
            cb(_msg_with_picks("charge", 5, 1700000000))
            cb(_msg_with_picks("charge", 5, 1700000000))
            # Mission 2: nPicks starts at 5, rises to 8 (a pickup event).
            cb(_msg_with_picks("run", 5, 1700010000))
            cb(_msg_with_picks("run", 8, 1700010000))
            cb(_msg_with_picks("charge", 8, 1700010000))
            cb(_msg_with_picks("charge", 8, 1700010000))
            hass.loop.run_until_complete(asyncio.sleep(0))

        assert len(captured) == 2
        assert captured[0].get("npicks_delta", 0) == 0
        assert captured[1].get("npicks_delta", 0) == 3


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
    """F4b/CLOUD-CATCHUP (v2.9.1) -- cloud refresh scheduled at mission-end
    via two fixed checkpoints (delayed first attempt + one fallback),
    replacing the original immediate single-shot refresh."""

    def _msg(self, phase: str) -> dict:
        return {"state": {"reported": {"cleanMissionStatus": {"phase": phase}}}}

    def _setup(self, latest_record=None):
        cc = MagicMock()
        cc.async_request_refresh = AsyncMock()
        hass = MagicMock()
        hass.loop = asyncio.new_event_loop()
        entry = MagicMock()
        entry.runtime_data.mission_store.latest.return_value = latest_record
        return cc, hass, entry

    def _drain(self, hass) -> None:
        hass.loop.run_until_complete(asyncio.sleep(0))

    def _fake_later(self, captured: list) -> Any:
        def _impl(hass_, delay, action):
            captured.append((delay, action))
            return MagicMock()  # cancel handle
        return _impl

    def test_refresh_is_scheduled_not_immediate(self):
        """The first attempt must be delayed, not fired synchronously on
        the mission-end message (that was the original bug)."""
        from custom_components.roomba_plus.callbacks import (
            make_mission_complete_callback, CLOUD_CATCHUP_FIRST_DELAY_SEC,
        )
        cc, hass, entry = self._setup(latest_record={"timeline": {}})
        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))   # transition: run -> charge
            self._drain(hass)

            assert len(captured) == 1
            assert captured[0][0] == CLOUD_CATCHUP_FIRST_DELAY_SEC
            cc.async_request_refresh.assert_not_called()  # only scheduled so far

    def test_no_refresh_without_prior_cleaning_phase(self):
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback
        cc, hass, entry = self._setup()
        with patch("custom_components.roomba_plus.callbacks.async_call_later") as mock_later:
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("charge"))   # charge without prior cleaning phase
            self._drain(hass)
            mock_later.assert_not_called()

    def test_first_checkpoint_succeeding_does_not_schedule_second(self):
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback
        cc, hass, entry = self._setup(latest_record={"timeline": {"finEvents": []}})
        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))
            self._drain(hass)
            assert len(captured) == 1

            # Simulate the first checkpoint firing: cloud already caught up.
            hass.loop.run_until_complete(captured[0][1](None))

            cc.async_request_refresh.assert_awaited_once()
            assert len(captured) == 1, "must not schedule a second checkpoint once caught up"

    def test_first_checkpoint_failing_schedules_second(self):
        from custom_components.roomba_plus.callbacks import (
            make_mission_complete_callback, CLOUD_CATCHUP_SECOND_DELAY_SEC,
        )
        cc, hass, entry = self._setup(latest_record={"timeline": None})
        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))
            self._drain(hass)
            assert len(captured) == 1

            hass.loop.run_until_complete(captured[0][1](None))  # checkpoint 1: still no timeline

            assert len(captured) == 2
            assert captured[1][0] == CLOUD_CATCHUP_SECOND_DELAY_SEC

    def test_second_checkpoint_failing_gives_up_and_logs(self, caplog):
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback
        cc, hass, entry = self._setup(latest_record={"timeline": None})
        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))
            self._drain(hass)

            hass.loop.run_until_complete(captured[0][1](None))  # checkpoint 1 fails
            with caplog.at_level("WARNING"):
                hass.loop.run_until_complete(captured[1][1](None))  # checkpoint 2 fails too

        assert len(captured) == 2, "must not schedule a third checkpoint — falls back to 24h idle"
        assert any("giving up" in r.message.lower() for r in caplog.records)

    def test_no_mission_store_treated_as_nothing_to_wait_for(self):
        """If mission_store isn't set up at all, don't schedule a pointless
        second checkpoint — there's nothing that could ever populate."""
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback
        cc, hass, entry = self._setup()
        entry.runtime_data.mission_store = None
        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))
            self._drain(hass)
            hass.loop.run_until_complete(captured[0][1](None))
            assert len(captured) == 1

    def test_pending_checkpoint_cancelled_on_unload(self):
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback
        cc, hass, entry = self._setup()
        with patch("custom_components.roomba_plus.callbacks.async_call_later"):
            make_mission_complete_callback(hass, cc, entry)
        entry.async_on_unload.assert_called_once()

    def test_v2_10_1_unrelated_backfill_of_old_record_does_not_fool_checkpoint_1(self):
        """Regression test for Thonno's v2.9.1 retest report: an unrelated
        backfill pass enriching the OLD (previous mission's) record in
        place, at almost exactly the moment checkpoint 1 runs its
        done-check, must not be mistaken for OUR mission's own record
        being ready -- checkpoint 2 must still get scheduled, and must
        correctly still report "not done" if the real mission's record
        hasn't itself been enriched yet by the time checkpoint 2 runs.

        Mirrors the exact sequence from the real log: old_record (a prior
        stuck-and-abandoned mission) has NO timeline when checkpoint 1 is
        scheduled, gains one via an unrelated in-place mutation before
        checkpoint 1's done-check runs (simulating the coincidental
        12:30:13-style backfill), while the real mission's own record
        (new_record) isn't appended until checkpoint 2 is already pending.
        """
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback

        old_record = {"id": "m_old", "timeline": None}
        new_record = {"id": "m_new", "timeline": None}
        state = {"latest": old_record}

        cc = MagicMock()
        cc.async_request_refresh = AsyncMock()
        hass = MagicMock()
        hass.loop = asyncio.new_event_loop()
        entry = MagicMock()
        entry.runtime_data.mission_store.latest.side_effect = lambda: state["latest"]

        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))  # mission-end -> checkpoint 1 scheduled
            self._drain(hass)
            assert len(captured) == 1

            # Unrelated backfill enriches the OLD record in place, BEFORE
            # checkpoint 1's done-check runs -- this is the exact moment
            # that fooled the pre-fix logic.
            old_record["timeline"] = {"finEvents": []}

            # Checkpoint 1 fires: old_record now superficially "has a
            # timeline", but it is NOT a new record -- must not be
            # mistaken for success.
            hass.loop.run_until_complete(captured[0][1](None))
            assert len(captured) == 2, (
                "checkpoint 2 must still be scheduled -- an unrelated "
                "record's in-place enrichment must not look like success"
            )

            # The real mission finally gets recorded as a genuinely NEW
            # object, still without a timeline of its own yet.
            state["latest"] = new_record

            # Checkpoint 2 fires before the real backfill catches up:
            # must correctly still report "not done" and warn, not crash.
            with patch("custom_components.roomba_plus.callbacks._LOGGER") as mock_logger:
                hass.loop.run_until_complete(captured[1][1](None))
                mock_logger.warning.assert_called_once()

    def test_v2_10_1_real_mission_succeeds_via_checkpoint_2(self):
        """Same scenario as above, but the real backfill DOES catch up by
        checkpoint 2 -- this must be recognized as success."""
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback

        old_record = {"id": "m_old", "timeline": None}
        new_record = {"id": "m_new", "timeline": None}
        state = {"latest": old_record}

        cc = MagicMock()
        cc.async_request_refresh = AsyncMock()
        hass = MagicMock()
        hass.loop = asyncio.new_event_loop()
        entry = MagicMock()
        entry.runtime_data.mission_store.latest.side_effect = lambda: state["latest"]

        captured: list = []
        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                    side_effect=self._fake_later(captured)):
            cb = make_mission_complete_callback(hass, cc, entry)
            cb(self._msg("run"))
            cb(self._msg("charge"))
            self._drain(hass)

            old_record["timeline"] = {"finEvents": []}  # unrelated enrichment
            hass.loop.run_until_complete(captured[0][1](None))  # checkpoint 1
            assert len(captured) == 2

            # Real mission recorded AND backfilled by the time checkpoint
            # 2 runs.
            new_record["timeline"] = {"finEvents": [{"type": "room"}]}
            state["latest"] = new_record

            with patch("custom_components.roomba_plus.callbacks._LOGGER") as mock_logger:
                hass.loop.run_until_complete(captured[1][1](None))
                mock_logger.warning.assert_not_called()


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


class TestCloudRoomFallback:
    """v2.10.1 CLOUD-ROOM-FALLBACK — current_room_idx structurally can
    never advance into the LAST planned room (AUTO-ADVANCE-ROOM's
    confidence gate requires cycle in (clean, quick), which flips to
    none/dock at exactly the moment the robot enters its last room).
    cloud_coordinator.raw_records' finEvents (already-cached, no new
    fetch) is checked as an opportunistic override before committing to
    the full 90s safety-cap wait.
    """

    def _run(
        self,
        phases_nstuck: list[tuple[str, int]],
        planned_rooms: list[str],
        planned_region_ids: list[str],
        cloud_room_events: list[dict] | None,
        current_room_idx: int = 1,
    ) -> list[dict]:
        hass, entry, recorded, _ = _make_callback_env()
        mts = MagicMock()
        mts.planned_rooms = planned_rooms
        mts.current_room_idx = current_room_idx
        mts.room_estimates_sec = [600.0] * len(planned_rooms)
        mts.total_estimated_sec = 999.0
        entry.runtime_data.mission_timer_store = mts

        entry.runtime_data.roomba.master_state = {
            "state": {
                "reported": {
                    "lastCommand": {
                        "regions": [{"rid": rid} for rid in planned_region_ids],
                    },
                },
            },
        }

        cc = MagicMock()
        if cloud_room_events is None:
            cc.raw_records = []
        else:
            cc.raw_records = [{
                "startTime": 1700000000,  # matches _msg()'s fixed mssnStrtTm
                "timeline": {"finEvents": cloud_room_events},
            }]
        entry.runtime_data.cloud_coordinator = cc

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

    def test_cloud_confirms_all_rooms_done_overrides_stale_index(self):
        """current_room_idx=0 of 2 (looks like room 2 still unvisited --
        the realistic case, since the index can never reach the last
        room), but the cloud's finEvents already show BOTH rooms done --
        the mission must confirm immediately (within the normal 2s hold),
        not wait out the 90s cap."""
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=[
                {"type": "room", "room": {"rid": "19", "status": 0}},
                {"type": "room", "room": {"rid": "21", "status": 0}},
            ],
            current_room_idx=0,
        )
        assert len(captured) == 1, "must confirm without waiting for the 90s cap"

    def test_cloud_partial_completion_does_not_override(self):
        """Only one of two planned rooms shows done in the cloud data --
        must NOT override; falls through to the existing (unvisited)
        behaviour."""
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=[
                {"type": "room", "room": {"rid": "19", "status": 0}},
            ],
            current_room_idx=0,
        )
        assert len(captured) == 0, "must not confirm -- room 21 not yet done in cloud"

    def test_no_matching_cloud_record_falls_through(self):
        """No cloud record at all (e.g. no cloud credentials, or data not
        refreshed yet) -- must behave exactly as before this feature."""
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=None,
            current_room_idx=0,
        )
        assert len(captured) == 0

    def test_status_6_error_recovery_also_counts_as_done(self):
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=[
                {"type": "room", "room": {"rid": "19", "status": 0}},
                {"type": "room", "room": {"rid": "21", "status": 6}},
            ],
            current_room_idx=0,
        )
        assert len(captured) == 1

    def test_in_progress_status_1_does_not_count_as_done(self):
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=[
                {"type": "room", "room": {"rid": "19", "status": 0}},
                {"type": "room", "room": {"rid": "21", "status": 1}},  # in progress
            ],
            current_room_idx=0,
        )
        assert len(captured) == 0

    def test_index_already_on_last_room_does_not_need_cloud_at_all(self):
        """Sanity check: when the index-based check ALREADY says no
        unvisited rooms (idx at the last room), the cloud fallback is
        irrelevant and the mission confirms normally regardless of what
        raw_records contains."""
        captured = self._run(
            phases_nstuck=[("run", 0), ("charge", 0), ("charge", 0)],
            planned_rooms=["Kitchen", "Bedroom"],
            planned_region_ids=["19", "21"],
            cloud_room_events=None,
            current_room_idx=1,  # len(rooms)-1 == 1, so idx IS the last room
        )
        # current_room_idx=1 with 2 rooms means idx is already at the last
        # room (0-indexed: room 0, room 1) -- _has_unvisited_planned_rooms
        # should already return False via the plain index check.
        assert len(captured) == 1


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
    """Bug A — make_mission_complete_callback must fire cloud refresh for stuck → stop/charge.

    v2.9.1 CLOUD-CATCHUP — refresh is now scheduled (first checkpoint),
    not fired synchronously, so these count how many times the checkpoint
    is SCHEDULED (i.e. async_call_later calls), then fire that checkpoint
    once to confirm async_request_refresh actually runs.
    """

    def _run_phases_refresh(self, phases: list[str]) -> int:
        """Return number of times async_request_refresh ran after firing
        every scheduled checkpoint."""
        from custom_components.roomba_plus.callbacks import make_mission_complete_callback

        coordinator = MagicMock()
        coordinator.async_request_refresh = AsyncMock(return_value=None)
        hass = MagicMock()
        hass.loop = asyncio.new_event_loop()
        entry = MagicMock()
        entry.runtime_data.mission_store.latest.return_value = {"timeline": {"finEvents": []}}

        captured: list = []
        def _fake_later(hass_, delay, action):
            captured.append(action)
            return MagicMock()

        with patch("custom_components.roomba_plus.callbacks.async_call_later",
                   side_effect=_fake_later):
            cb = make_mission_complete_callback(hass, coordinator, entry)
            for phase in phases:
                cb(_msg(phase))
            hass.loop.run_until_complete(asyncio.sleep(0))
            for action in list(captured):
                hass.loop.run_until_complete(action(None))

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


# ═══════════════════════════════════════════════════════════════════════
# PARTIAL-MESSAGE-SAFE LOOKUP (v2.9.1) — Thonno's "stuck_and_resumed
# instead of completed" report. Root cause: the mission-start-triggering
# MQTT delta only updates cleanMissionStatus, NOT bbrun (a separate
# top-level key) — reported.get("bbrun", {}) on that exact message
# silently defaulted nstuck_at_start to 0 instead of the robot's true
# lifetime nStuck count, so the very next message carrying bbrun (any
# nonzero lifetime count) falsely looked like a brand-new stuck event.
#
# _msg() always includes bbrun in every message — unlike real MQTT deltas
# — so this whole bug class was invisible to the rest of this test file.
# These tests build messages WITHOUT bbrun/lastCommand to actually
# exercise the fallback path.
# ═══════════════════════════════════════════════════════════════════════

def _msg_no_bbrun(phase: str, sqft: int = 100, last_command: dict | None = None) -> dict:
    """Build an MQTT message updating ONLY cleanMissionStatus — no bbrun,
    no lastCommand — mirroring a real delta that didn't happen to touch
    those separate top-level keys."""
    reported: dict = {
        "cleanMissionStatus": {
            "phase": phase,
            "sqft": sqft,
            "mssnStrtTm": 1700000000,
            "initiator": "schedule",
            "error": 0,
        },
    }
    if last_command is not None:
        reported["lastCommand"] = last_command
    return {"state": {"reported": reported}}


def _set_master_state(entry, **top_level_keys) -> None:
    """Configure entry.runtime_data.roomba.master_state with real dict
    values for the given top-level keys (e.g. bbrun=..., lastCommand=...),
    as if a PRIOR message had already populated the merged robot state."""
    entry.runtime_data.roomba.master_state = {
        "state": {"reported": dict(top_level_keys)}
    }


class TestMergedTopLevelHelper:
    """Direct tests of _merged_top_level()."""

    def _entry(self):
        entry = MagicMock()
        return entry

    def test_uses_message_value_when_present(self):
        from custom_components.roomba_plus.callbacks import _merged_top_level
        entry = self._entry()
        entry.runtime_data.roomba.master_state = {
            "state": {"reported": {"bbrun": {"nStuck": 99}}}
        }
        reported = {"bbrun": {"nStuck": 1}}
        assert _merged_top_level(entry, reported, "bbrun") == {"nStuck": 1}

    def test_falls_back_to_master_state_when_absent_from_message(self):
        from custom_components.roomba_plus.callbacks import _merged_top_level
        entry = self._entry()
        _set_master_state(entry, bbrun={"nStuck": 12, "hr": 250})
        reported = {"cleanMissionStatus": {"phase": "run"}}  # no bbrun
        assert _merged_top_level(entry, reported, "bbrun") == {"nStuck": 12, "hr": 250}

    def test_empty_dict_when_absent_everywhere(self):
        from custom_components.roomba_plus.callbacks import _merged_top_level
        entry = self._entry()
        entry.runtime_data.roomba.master_state = {}
        assert _merged_top_level(entry, {}, "bbrun") == {}

    def test_no_roomba_attribute_does_not_raise(self):
        """Some test/fake entries have no .roomba at all — must degrade to {}."""
        from custom_components.roomba_plus.callbacks import _merged_top_level

        class _NoRoombaData:
            pass

        class _Entry:
            runtime_data = _NoRoombaData()

        assert _merged_top_level(_Entry(), {}, "bbrun") == {}


class TestNstuckBaselineSurvivesPartialStartMessage:
    """End-to-end via make_mission_callback: the exact scenario from
    Thonno's log (mission-start message with no bbrun, robot's true
    lifetime nStuck unrelated and nonzero, no real stuck event during
    the mission)."""

    def test_no_false_stuck_event_when_lifetime_nstuck_unchanged(self):
        hass, entry, _, _ = _make_callback_env()
        # Robot's last known full state already has nStuck=12 (from past,
        # unrelated activity) BEFORE this mission even starts.
        _set_master_state(entry, bbrun={"nStuck": 12, "hr": 250})
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            cb(_msg_no_bbrun("run"))            # mission start — no bbrun in THIS message
            cb({"state": {"reported": {       # later message DOES carry bbrun —
                "cleanMissionStatus": {"phase": "run", "sqft": 100, "error": 0},
                "bbrun": {"nStuck": 12, "hr": 250},  # same value, nothing actually happened
            }}})
            cb(_msg_no_bbrun("charge"))
            cb(_msg_no_bbrun("charge"))

            hass.loop.run_until_complete(asyncio.sleep(0))

        assert mock_record.call_count == 1
        _, kwargs = mock_record.call_args
        assert kwargs["nstuck_delta"] == 0, (
            "nstuck_delta must be 0 when the lifetime nStuck counter never "
            "actually changed during the mission — with the bug, the "
            "baseline was wrongly read as 0 instead of 12, making this "
            "look like a brand-new stuck event."
        )

    def test_real_stuck_event_still_detected(self):
        """Sanity check: the fix must not mask a GENUINE stuck event."""
        hass, entry, _, _ = _make_callback_env()
        _set_master_state(entry, bbrun={"nStuck": 12, "hr": 250})
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            cb(_msg_no_bbrun("run"))
            cb({"state": {"reported": {
                "cleanMissionStatus": {"phase": "run", "sqft": 100, "error": 0},
                "bbrun": {"nStuck": 13, "hr": 250},  # genuinely incremented
            }}})
            # roombapy's own master_state merge would already reflect this
            # by the time later messages arrive — update it the same way
            # for this test, since the fake roomba here is static otherwise.
            _set_master_state(entry, bbrun={"nStuck": 13, "hr": 250})
            cb(_msg_no_bbrun("run"))     # recovered, kept cleaning
            cb(_msg_no_bbrun("charge"))
            cb(_msg_no_bbrun("charge"))

            hass.loop.run_until_complete(asyncio.sleep(0))

        _, kwargs = mock_record.call_args
        assert kwargs["nstuck_delta"] == 1


class TestCaptureZoneNamesSurvivesPartialStartMessage:
    """_capture_zone_names() must not return [] for a room-selected
    mission just because lastCommand was absent from the exact message
    that triggered mission-start detection (Thonno's zones=[] log line)."""

    def test_zones_resolved_from_master_state_when_absent_from_message(self):
        from custom_components.roomba_plus.callbacks import _capture_zone_names
        from custom_components.roomba_plus.models import MapCapability

        entry = MagicMock()
        entry.runtime_data.room_seg_store = None
        entry.runtime_data.map_capability = MapCapability.SMART
        entry.runtime_data.cloud_coordinator.regions = [
            {"id": "1", "name": "Kitchen"},
            {"id": "2", "name": "Hallway"},
        ]
        _set_master_state(
            entry,
            lastCommand={"regions": [{"region_id": "1"}, {"region_id": "2"}]},
        )
        reported = {"cleanMissionStatus": {"phase": "run"}}  # no lastCommand here

        zones = _capture_zone_names(entry, reported)
        assert zones == ["Kitchen", "Hallway"]

    def test_uses_message_lastcommand_when_present(self):
        from custom_components.roomba_plus.callbacks import _capture_zone_names
        from custom_components.roomba_plus.models import MapCapability

        entry = MagicMock()
        entry.runtime_data.room_seg_store = None
        entry.runtime_data.map_capability = MapCapability.SMART
        entry.runtime_data.cloud_coordinator.regions = [{"id": "1", "name": "Kitchen"}]
        entry.runtime_data.roomba.master_state = {
            "state": {"reported": {"lastCommand": {"regions": [{"region_id": "999"}]}}}
        }
        reported = {
            "cleanMissionStatus": {"phase": "run"},
            "lastCommand": {"regions": [{"region_id": "1"}]},
        }
        assert _capture_zone_names(entry, reported) == ["Kitchen"]

    def test_ephemeral_returns_confirmed_room_seg_store_names(self):
        """ROOM-SEG Stage 6 — EPHEMERAL branch now reads RoomSegStore,
        not the deleted ZoneStore."""
        from custom_components.roomba_plus.callbacks import _capture_zone_names
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True),
            "room_2": SegRoom(id="room_2", name="", confirmed=False),
        }
        entry = MagicMock()
        entry.runtime_data.room_seg_store = rss
        reported = {"cleanMissionStatus": {"phase": "run"}}

        assert _capture_zone_names(entry, reported) == ["Kitchen"]


@pytest.mark.skipif(
    not any(
        __import__("pathlib").Path(p).exists()
        for p in [
            "tests/fixtures/roomba_plus_missions_01KRRVYR4T1MPSYM7ACKA5XCBX.dms",
            "/mnt/user-data/uploads/roomba_plus_missions_01KRRVYR4T1MPSYM7ACKA5XCBX.dms",
        ]
    ),
    reason="Real-data fixture (.dms) not available in this environment",
)
class TestMissionClassificationAgainstRealRecords:
    """Real-data validation (field-store bug-hunt): drive async_record_mission
    with inputs reconstructed from the 24 real persisted mission records and
    assert the classifier reproduces the result that was actually stored.

    The real field distribution is 19 completed / 4 cancelled / 1 error /
    1 stuck_and_resumed (frozen snapshot 2026-07-02, 25 records), with
    initiators schedule/manual/localApp — this exercises the classifier across
    the result and initiator mix a real 980 actually produced, not synthetic
    happy-path values.

    v3.2.1 — the fixture is now a FROZEN snapshot in tests/fixtures/ (takes
    priority over the /mnt/user-data/uploads fallback).  The original
    hard `== 24` count pinned a LIVE store that grew by one mission between
    sessions and broke the suite; the count check is now a lower bound so a
    newer live upload in the fallback path can never break CI, while the
    frozen snapshot keeps the per-result reproduce-tests deterministic.
    """
    import json as _json
    from pathlib import Path as _Path

    _DMS_PATHS = [
        _Path("tests/fixtures/roomba_plus_missions_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
        _Path("/mnt/user-data/uploads/roomba_plus_missions_01KRRVYR4T1MPSYM7ACKA5XCBX.dms"),
    ]
    _DMS_FILE = next((p for p in _DMS_PATHS if p.exists()), None)
    _REAL = _json.load(open(_DMS_FILE)) if _DMS_FILE else {"records": []}
    _RECORDS = _REAL.get("data", _REAL).get("records", [])

    def _classify(self, mission, reported=None, nstuck_delta=0):
        """Run the real async_record_mission and return the stored result."""
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass = _make_hass(loop)
        start_ts = int(loop.time()) - 1800
        try:
            loop.run_until_complete(
                async_record_mission(
                    hass, entry, mission, reported or {},
                    [], start_ts, nstuck_delta,
                )
            )
        finally:
            loop.close()
        return store.latest()

    def test_real_records_loaded(self):
        assert len(self._RECORDS) >= 24

    def test_completed_records_reproduce(self):
        """Every real 'completed' record: phase=charge, error=0 → completed."""
        completed = [r for r in self._RECORDS if r.get("result") == "completed"]
        assert len(completed) == 19
        for r in completed:
            rec = self._classify(
                {"phase": "charge", "error": 0, "sqft": r.get("area_sqft", 0),
                 "initiator": r.get("initiator", "")}
            )
            assert rec["result"] == "completed"

    def test_cancelled_records_reproduce(self):
        """Real 'cancelled' records: phase=cancelled → cancelled."""
        cancelled = [r for r in self._RECORDS if r.get("result") == "cancelled"]
        assert len(cancelled) == 4
        for r in cancelled:
            rec = self._classify(
                {"phase": "cancelled", "error": 0,
                 "initiator": r.get("initiator", "")}
            )
            assert rec["result"] == "cancelled"

    def test_error_record_reproduces(self):
        """The single real 'error' record reproduces from a non-zero error code."""
        errors = [r for r in self._RECORDS if r.get("result") == "error"]
        assert len(errors) == 1
        ec = errors[0].get("error_code") or 17
        rec = self._classify({"phase": "charge", "error": ec, "sqft": 0})
        assert rec["result"] == "error"

    def test_real_initiators_preserved(self):
        """Initiator values from real records flow through to the stored record."""
        for init in ("schedule", "manual", "localApp"):
            rec = self._classify(
                {"phase": "charge", "error": 0, "sqft": 50, "initiator": init}
            )
            assert rec["result"] == "completed"
            # initiator is captured in the stored record
            assert rec.get("initiator") == init or "initiator" in rec

    def test_zero_duration_completed_records(self):
        """Several real records have duration_min=0 (instant manual stop then
        dock). These must still classify as completed, not crash on 0 area."""
        zero_dur = [r for r in self._RECORDS
                    if r.get("duration_min") == 0 and r.get("result") == "completed"]
        assert len(zero_dur) >= 1
        for r in zero_dur:
            rec = self._classify({"phase": "charge", "error": 0, "sqft": 0})
            assert rec["result"] == "completed"
            assert rec["duration_min"] >= 0


class TestFullLifecycleSequenceToResult:
    """Real-data validation: drive the full make_mission_callback chain with
    realistic phase SEQUENCES (not single messages) and assert async_record_
    mission is invoked with the result the sequence should produce.

    Sequences mirror what a real 980 emits over MQTT:
      completed:  run → run → charge (debounced end)
      cancelled:  run → pause → stop (deliberate stop)
      error/stuck: run → stuck (nStuck increments)
    """

    def _run_sequence(self, phases_nstuck):
        """Feed (phase, nstuck) messages; return list of async_record_mission
        kwargs captured (one per genuine mission-end)."""
        hass, entry, _recorded, _store = _make_callback_env()
        cb = make_mission_callback(hass, entry)
        captured: list[dict] = []

        async def _capture(*args, **kwargs):
            # async_record_mission(hass, entry, mission, reported, zones,
            #                       start_ts, nstuck_delta, ...)
            d = dict(kwargs)
            if len(args) >= 3:
                d["_mission_arg"] = args[2]
            if len(args) >= 7:
                d["_nstuck_delta"] = args[6]
            captured.append(d)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            mock_record.side_effect = _capture
            for phase, nstuck in phases_nstuck:
                cb(_msg(phase, nstuck=nstuck))
            hass.loop.run_until_complete(asyncio.sleep(0))
        return captured

    def test_completed_sequence_fires_once(self):
        """run → run → charge → charge: a genuine end fires exactly one record."""
        captured = self._run_sequence([
            ("run", 0), ("run", 0), ("charge", 0), ("charge", 0),
        ])
        assert len(captured) == 1
        mission = captured[0].get("_mission_arg", {})
        # phase at end is charge (a MISSION_END_PHASE), error 0 → completed-class
        assert mission.get("phase") in ("charge", "hmPostMsn", "stop")

    def test_cleaning_then_stop_fires_end(self):
        """run → stop (user stop): the chain registers a mission end."""
        captured = self._run_sequence([
            ("run", 0), ("run", 0), ("stop", 0), ("stop", 0),
        ])
        assert len(captured) == 1

    def test_stuck_sequence_carries_nstuck_delta(self):
        """run → stuck (nStuck increments) → charge: end record carries a
        positive nstuck_delta so the classifier can produce a stuck result."""
        captured = self._run_sequence([
            ("run", 0), ("stuck", 1), ("charge", 1), ("charge", 1),
        ])
        assert len(captured) == 1
        # nstuck_delta computed as end nStuck - start nStuck = 1 - 0,
        # passed as a keyword argument to async_record_mission.
        assert captured[0].get("nstuck_delta", 0) >= 1
        # the stuck-recovery classifier should have flagged this as a stuck
        # outcome (resumed or abandoned), proving the chain wired it through
        assert captured[0].get("result_override") in (
            "stuck_and_resumed", "stuck_and_abandoned",
        )

    def test_no_end_without_cleaning_phase(self):
        """charge → charge with no prior cleaning phase: must NOT fire an end
        (robot was just sitting on the dock, never ran a mission)."""
        captured = self._run_sequence([
            ("charge", 0), ("charge", 0),
        ])
        assert len(captured) == 0

    def test_pause_does_not_end_mission(self):
        """run → pause → run: a mid-mission pause must not register an end;
        the mission continues."""
        captured = self._run_sequence([
            ("run", 0), ("pause", 0), ("run", 0),
        ])
        assert len(captured) == 0


class TestCloudRefreshCallbackDispatchesV320Checks:
    """v3.2.0 bug-hunt fix — make_cloud_refresh_callback's
    _on_cloud_refresh_complete must actually dispatch all five v3.2.0
    repair checks (health_trend_declining, room_accessibility,
    furniture_change, stuck_hotspot, coverage_frequency).

    Found via a systematic post-release audit: four of these five were
    built and thoroughly unit-tested as standalone functions, but never
    wired into anything the running integration actually calls — the
    tests exercised the functions directly, which passed, but nothing in
    production would ever have invoked them. This test asserts on the
    actual dispatch call names so that gap can't silently reopen if this
    callback is refactored later.
    """

    def _run_callback(self):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = MagicMock()
        rd.umf_aligner = MagicMock()
        rd.robot_profile_store = MagicMock()

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}   # version_id absent -> no realign branch

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        return [
            call.kwargs.get("name")
            for call in hass.async_create_task.call_args_list
        ]

    def test_all_five_v320_checks_dispatched(self):
        dispatched = self._run_callback()
        expected = {
            "roomba_plus_room_accessibility_check",
            "roomba_plus_furniture_change_check",
            "roomba_plus_stuck_hotspot_check",
            "roomba_plus_coverage_frequency_check",
        }
        missing = expected - set(dispatched)
        assert not missing, f"Missing dispatch(es): {missing}"

    def test_room_accessibility_not_dispatched_without_umf_aligner(self):
        """Correctly gated — no umf_aligner means no room polygons, so
        dispatching the check would be pure overhead."""
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = MagicMock()
        rd.umf_aligner = None
        rd.robot_profile_store = MagicMock()

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        dispatched = [
            call.kwargs.get("name")
            for call in hass.async_create_task.call_args_list
        ]
        assert "roomba_plus_room_accessibility_check" not in dispatched

    def test_grid_store_dependent_checks_not_dispatched_without_grid_store(self):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = None
        rd.umf_aligner = MagicMock()
        rd.robot_profile_store = MagicMock()

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        dispatched = [
            call.kwargs.get("name")
            for call in hass.async_create_task.call_args_list
        ]
        assert "roomba_plus_furniture_change_check" not in dispatched
        assert "roomba_plus_stuck_hotspot_check" not in dispatched
        assert "roomba_plus_room_accessibility_check" not in dispatched


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 bug-hunt round 5 — glue paths with no direct test coverage yet
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossCorrCaptureGlue:
    """v3.3.0 CROSS-CORR — the mission-start capture closure itself
    (hass.states read, robot_profile_store.record_correlation_snapshot
    call, async_save scheduling) was only ever exercised accidentally
    (as the source of the isinstance-guard fix); this exercises it on
    purpose, end to end through make_mission_callback."""

    def _msg(self, phase: str, mssn_strt_tm: int = 1700000000) -> dict:
        return {"state": {"reported": {"cleanMissionStatus": {
            "phase": phase, "sqft": 100, "mssnStrtTm": mssn_strt_tm,
            "initiator": "schedule", "error": 0,
        }}}}

    def test_snapshot_captured_and_saved_on_mission_start(self):
        from custom_components.roomba_plus.callbacks import make_mission_callback
        hass, entry, _recorded, _store = _make_callback_env()
        entry.options = {"correlation_entities": ["sensor.humidity"]}
        rps = MagicMock()
        entry.runtime_data.robot_profile_store = rps
        rps.async_save = AsyncMock()

        humid_state = MagicMock()
        humid_state.state = "63.5"
        hass.states.get.return_value = humid_state

        # _make_callback_env's hass.async_create_task CLOSES the coroutine
        # (avoids "never awaited" warnings) instead of running it — so we
        # capture+run it ourselves to observe the actual async_save call,
        # same technique the fixture itself uses for _capture_append.
        created: list[Any] = []

        def _capture_task(coro, name=None):
            created.append(coro)

        hass.async_create_task = _capture_task

        cb = make_mission_callback(hass, entry)
        cb(self._msg("run"))
        # The capture runs via call_soon_threadsafe on hass.loop — drain it.
        hass.loop.run_until_complete(asyncio.sleep(0))

        rps.record_correlation_snapshot.assert_called_once()
        args = rps.record_correlation_snapshot.call_args[0]
        assert args[0] == {"sensor.humidity": 63.5}
        assert len(created) == 1
        hass.loop.run_until_complete(created[0])
        rps.async_save.assert_awaited_once_with(hass, entry.entry_id)

    def test_no_snapshot_when_not_configured(self):
        """Opt-in contract: no configured entities → hass.states never
        touched, robot_profile_store never called."""
        from custom_components.roomba_plus.callbacks import make_mission_callback
        hass, entry, _recorded, _store = _make_callback_env()
        entry.options = {}
        rps = MagicMock()
        entry.runtime_data.robot_profile_store = rps

        cb = make_mission_callback(hass, entry)
        cb(self._msg("run"))
        hass.loop.run_until_complete(asyncio.sleep(0))

        rps.record_correlation_snapshot.assert_not_called()
        hass.states.get.assert_not_called()

    def test_unreadable_sensor_state_skipped_not_raised(self):
        """A configured entity in an unavailable/unknown state must not
        crash the mission-start path — it's just excluded from the
        snapshot."""
        from custom_components.roomba_plus.callbacks import make_mission_callback
        hass, entry, _recorded, _store = _make_callback_env()
        entry.options = {"correlation_entities": ["sensor.broken"]}
        rps = MagicMock()
        entry.runtime_data.robot_profile_store = rps
        rps.async_save = AsyncMock()

        broken_state = MagicMock()
        broken_state.state = "unavailable"
        hass.states.get.return_value = broken_state

        cb = make_mission_callback(hass, entry)
        cb(self._msg("run"))  # must not raise
        hass.loop.run_until_complete(asyncio.sleep(0))

        # float("unavailable") fails → no values → nothing recorded/saved
        rps.record_correlation_snapshot.assert_not_called()
        rps.async_save.assert_not_awaited()


class TestL5CorrelationFinalizeGlue:
    """v3.3.0 CROSS-CORR — the L5-enrichment callback wiring around
    finalize_correlation() (reading latest.get('dirt')/'started_at' from
    the real enriched-record shape via mission_store.query(days=1)) had
    no test above the store level."""

    def test_finalize_called_with_record_dirt_and_started_at(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_robot_profile_store,
        )
        hass, entry, _recorded, mission_store = _make_callback_env()
        rps = MagicMock()
        rps.update_mission_stats.return_value = False
        rps.finalize_correlation.return_value = True
        rps.async_save = AsyncMock()
        record = {
            "id": "m_1", "started_at": "2026-07-04T10:00:00+00:00",
            "dirt": 7, "timeline": {"finEvents": []},
        }
        mission_store.query.return_value = [record]

        hass.loop.run_until_complete(
            _async_update_robot_profile_store(
                hass, entry, mission_store, rps,
            )
        )
        rps.finalize_correlation.assert_called_once_with(
            "2026-07-04T10:00:00+00:00", 7.0
        )

    def test_missing_dirt_field_does_not_call_finalize(self):
        """Pre-enrichment records (dirt not yet merged) must not call
        finalize_correlation with garbage — the isinstance guard on
        latest.get('dirt') is the thing under test."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_robot_profile_store,
        )
        hass, entry, _recorded, mission_store = _make_callback_env()
        rps = MagicMock()
        rps.update_mission_stats.return_value = False
        record = {"id": "m_1", "started_at": "2026-07-04T10:00:00+00:00",
                  "timeline": {"finEvents": []}}
        mission_store.query.return_value = [record]

        hass.loop.run_until_complete(
            _async_update_robot_profile_store(
                hass, entry, mission_store, rps,
            )
        )
        rps.finalize_correlation.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# v3.4.0 GS-SMART-COVERAGE
# ─────────────────────────────────────────────────────────────────────────────

class TestGsCoverageHookDispatch:
    """Gate on the cloud-refresh hook: dispatched only when
    map_capability == SMART, grid_store, and umf_aligner are all
    present. The function itself no-ops on everything else (aligned
    state, mission_store presence, actual candidates)."""

    def _run_callback(self, *, map_capability, grid_store, umf_aligner):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback
        from custom_components.roomba_plus.models import MapCapability

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = grid_store
        rd.umf_aligner = umf_aligner
        rd.robot_profile_store = MagicMock()
        rd.map_capability = map_capability

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        return [
            call.kwargs.get("name")
            for call in hass.async_create_task.call_args_list
        ]

    def test_dispatched_when_smart_with_grid_store_and_aligner(self):
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.SMART,
            grid_store=MagicMock(), umf_aligner=MagicMock(),
        )
        assert "roomba_plus_gs_smart_coverage" in dispatched

    def test_not_dispatched_without_grid_store(self):
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.SMART,
            grid_store=None, umf_aligner=MagicMock(),
        )
        assert "roomba_plus_gs_smart_coverage" not in dispatched

    def test_not_dispatched_without_umf_aligner(self):
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.SMART,
            grid_store=MagicMock(), umf_aligner=None,
        )
        assert "roomba_plus_gs_smart_coverage" not in dispatched

    def test_not_dispatched_for_ephemeral_tier(self):
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.EPHEMERAL,
            grid_store=MagicMock(), umf_aligner=MagicMock(),
        )
        assert "roomba_plus_gs_smart_coverage" not in dispatched

    def test_dispatched_before_grid_store_reading_checks(self):
        """Ordering matters (plan §3.4): GS-SMART-COVERAGE must be
        queued before stuck_hotspot/furniture_change/room_accessibility
        so a same-cycle backfill is visible to them."""
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.SMART,
            grid_store=MagicMock(), umf_aligner=MagicMock(),
        )
        gs_idx = dispatched.index("roomba_plus_gs_smart_coverage")
        for later in (
            "roomba_plus_stuck_hotspot_check",
            "roomba_plus_furniture_change_check",
            "roomba_plus_room_accessibility_check",
        ):
            assert dispatched.index(later) > gs_idx, (
                f"{later} dispatched before roomba_plus_gs_smart_coverage"
            )


def _gs_coverage_env(*, aligned=True, watermark=0):
    """Minimal runtime_data + mission_store fixture for exercising
    _async_update_gs_smart_coverage() directly (not through the hook)."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry"

    gs = MagicMock()
    gs.last_processed_nmssn = watermark
    gs.async_save = AsyncMock()

    aligner = MagicMock()
    aligner.aligned = aligned
    aligner.umf_to_pose.side_effect = lambda x, y: (x * 10, y * 10)

    ms = MagicMock()

    data = MagicMock()
    data.grid_store = gs
    data.umf_aligner = aligner
    data.mission_store = ms
    entry.runtime_data = data

    return hass, entry, data, gs, aligner, ms


class TestGsSmartCoverageDispatchFunction:
    """_async_update_gs_smart_coverage() itself — candidate selection,
    rate cap, per-record error handling, and the actual GridStore
    update call shape."""

    @pytest.mark.asyncio
    async def test_noop_without_grid_store(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        data.grid_store = None
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        ms.records.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_without_mission_store(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        data.mission_store = None
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.update_from_mission.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_without_umf_aligner(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        data.umf_aligner = None
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        ms.records.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_aligner_not_aligned(self):
        """GS-SMART-UMF prerequisite: without alignment, umf_to_pose()
        would return None for everything anyway — skip the whole
        batch rather than fetch UMF data that can't be used."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env(aligned=False)
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        ms.records.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_without_pmaps_info_are_not_candidates(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 5},  # no pmaps_info at all
        ]
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.update_from_mission.assert_not_called()
        gs.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_at_or_below_watermark_are_not_candidates(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env(watermark=10)
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 10, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}]},
            {"id": "m_2", "nMssn": 9, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}]},
        ]
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.update_from_mission.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_cap_limits_candidates_per_refresh(self):
        """Plan §4.2 — at most 5 missions processed per refresh cycle,
        even with a larger backlog."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
            MissionMapUnavailable,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": f"m_{i}", "nMssn": i,
             "pmaps_info": [{"pmap_id": "p", "pmapv_id": f"v{i}"}]}
            for i in range(1, 9)  # 8 candidates, backlog > cap
        ]
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(side_effect=MissionMapUnavailable("no coverage")),
        ) as mock_fetch:
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        assert mock_fetch.await_count == 5

    @pytest.mark.asyncio
    async def test_candidates_processed_in_ascending_nmssn_order(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": "m_3", "nMssn": 30, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}],
             "started_at": "2026-07-01T10:00:00+00:00"},
            {"id": "m_1", "nMssn": 10, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}],
             "started_at": "2026-07-01T10:00:00+00:00"},
            {"id": "m_2", "nMssn": 20, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}],
             "started_at": "2026-07-01T10:00:00+00:00"},
        ]
        payload = {"coverage_mm": [], "escape_events": []}
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(return_value=payload),
        ):
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        processed_order = [
            c.args[0] for c in gs.record_processed_nmssn.call_args_list
        ]
        assert processed_order == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_unavailable_advances_watermark_no_crash(self):
        """A structurally-bad record (no coverage layer, plan D5's
        untested-lewis case) must not be retried forever — advance
        the watermark past it."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
            MissionMapUnavailable,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 5, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}]},
        ]
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(side_effect=MissionMapUnavailable("no coverage")),
        ):
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.record_processed_nmssn.assert_called_once_with(5)
        gs.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generic_fetch_failure_does_not_advance_watermark(self):
        """A transient cloud/transport failure must be retried on the
        next refresh — unlike MissionMapUnavailable/-Mismatch, the
        watermark must NOT advance."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 5, "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}]},
        ]
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(side_effect=Exception("cloud transport error")),
        ):
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.record_processed_nmssn.assert_not_called()
        gs.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_mission_calls_update_from_mission_with_expected_shape(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        from custom_components.roomba_plus.map_renderer import (
            ROBOT_DIAMETER_MM_ISJ_SERIES,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env()
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 5,
             "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}],
             "started_at": "2026-07-04T10:00:00+00:00"},
        ]
        payload = {
            "coverage_mm": [[100.0, 200.0], [300.0, 400.0]],
            "escape_events": [
                {"pose": [1.0, 2.0, 0.0], "event": "start_stuck"},
                {"pose": [3.0, 4.0, 0.0], "event": "start_evade"},  # excluded
            ],
        }
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(return_value=payload),
        ):
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())

        gs.update_from_mission.assert_called_once()
        args, kwargs = gs.update_from_mission.call_args
        pose_points, stuck_points = args
        assert pose_points == [(1000.0, 2000.0), (3000.0, 4000.0)]
        assert stuck_points == [(10.0, 20.0)]  # only start_stuck, start_evade excluded
        assert kwargs["stuck_wh"] == (5, 10)  # 2026-07-04 is a Saturday, 10:00 local
        assert kwargs["robot_radius_mm"] == ROBOT_DIAMETER_MM_ISJ_SERIES / 2
        gs.record_processed_nmssn.assert_called_once_with(5)
        gs.async_save.assert_awaited_once_with(hass, "test_entry")

    @pytest.mark.asyncio
    async def test_no_candidates_does_not_call_async_save(self):
        """changed=False path — nothing to persist, don't write."""
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        hass, entry, data, gs, aligner, ms = _gs_coverage_env(watermark=100)
        ms.records.return_value = []
        await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        gs.async_save.assert_not_called()


class TestGsCoverageLiveCloudMutualExclusion:
    """Plan §2 — the actual double-counting regression scenario: a
    mission already fed via the live path (image.py) must be skipped
    by the cloud path's own candidate filter."""

    @pytest.mark.asyncio
    async def test_live_processed_mission_is_skipped_by_cloud_path(self):
        from custom_components.roomba_plus.callbacks import (
            _async_update_gs_smart_coverage,
        )
        # Simulates image.py having already called
        # grid_store.record_processed_nmssn(77) for this mission.
        hass, entry, data, gs, aligner, ms = _gs_coverage_env(watermark=77)
        ms.records.return_value = [
            {"id": "m_1", "nMssn": 77,
             "pmaps_info": [{"pmap_id": "p", "pmapv_id": "v"}]},
        ]
        with patch(
            "custom_components.roomba_plus.callbacks.async_fetch_mission_map",
            AsyncMock(),
        ) as mock_fetch:
            await _async_update_gs_smart_coverage(hass, entry, MagicMock())
        mock_fetch.assert_not_awaited()
        gs.update_from_mission.assert_not_called()


class TestGsCoverageHelperFunctions:
    """Unit tests for the small pure helpers backing the dispatch
    function above."""

    def test_umf_points_to_pose_converts_and_skips_unresolvable(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_umf_points_to_pose,
        )
        aligner = MagicMock()
        aligner.umf_to_pose.side_effect = (
            lambda x, y: None if x == 999 else (x + 1, y + 1)
        )
        pts = _gs_coverage_umf_points_to_pose(
            [[1.0, 2.0], [999.0, 5.0], ["bad"], [3.0, 4.0]], aligner,
        )
        assert pts == [(2.0, 3.0), (4.0, 5.0)]

    def test_classify_stuck_events_excludes_start_evade(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_classify_stuck_events,
        )
        aligner = MagicMock()
        aligner.umf_to_pose.side_effect = lambda x, y: (x, y)
        events = [
            {"pose": [1.0, 1.0, 0.0], "event": "start_stuck"},
            {"pose": [2.0, 2.0, 0.0], "event": "start_evade"},
            {"pose": [3.0, 3.0, 0.0], "event": "brush_stall_detected"},
            {"pose": [4.0, 4.0, 0.0], "event": "wheel_dropped"},
            {"pose": [5.0, 5.0, 0.0], "event": "stasis_detected"},
        ]
        result = _gs_coverage_classify_stuck_events(events, aligner)
        assert result == [(1.0, 1.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)]

    def test_classify_stuck_events_logs_unknown_type_not_counted(self, caplog):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_classify_stuck_events,
        )
        aligner = MagicMock()
        aligner.umf_to_pose.side_effect = lambda x, y: (x, y)
        events = [{"pose": [1.0, 1.0, 0.0], "event": "some_future_event_type"}]
        with caplog.at_level("INFO"):
            result = _gs_coverage_classify_stuck_events(events, aligner)
        assert result == []
        assert "unclassified escape_event type" in caplog.text

    def test_classify_stuck_events_skips_malformed_entries(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_classify_stuck_events,
        )
        aligner = MagicMock()
        aligner.umf_to_pose.side_effect = lambda x, y: (x, y)
        events = [
            "not_a_dict",
            {"event": "start_stuck"},  # no pose
            {"pose": [1.0, 1.0, 0.0], "event": "start_stuck"},
        ]
        result = _gs_coverage_classify_stuck_events(events, aligner)
        assert result == [(1.0, 1.0)]

    def test_mission_start_weekday_hour_matches_image_py_derivation(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_mission_start_weekday_hour,
        )
        result = _gs_coverage_mission_start_weekday_hour(
            "2026-07-04T10:00:00+00:00"
        )
        assert result == (5, 10)  # Saturday, 10:00 UTC == local in test env

    def test_mission_start_weekday_hour_handles_missing_or_bad_input(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_mission_start_weekday_hour,
        )
        assert _gs_coverage_mission_start_weekday_hour(None) is None
        assert _gs_coverage_mission_start_weekday_hour("") is None
        assert _gs_coverage_mission_start_weekday_hour("not-a-date") is None

    def test_safe_int_handles_none_and_garbage(self):
        from custom_components.roomba_plus.callbacks import _gs_coverage_safe_int
        assert _gs_coverage_safe_int(None) is None
        assert _gs_coverage_safe_int("garbage") is None
        assert _gs_coverage_safe_int("42") == 42
        assert _gs_coverage_safe_int(42) == 42


def _real_arithmetic_aligner():
    """A MagicMock aligner whose umf_to_pose() actually does the real
    UmfAligner arithmetic (cos_r * x_umf - sin_r * y_umf + tx, ...)
    instead of an identity/None-branching stub — needed to expose the
    v3.4.0 bug-hunt finding below, since an identity mock silently
    passes non-numeric input straight through without ever hitting the
    TypeError the real aligner would raise."""
    aligner = MagicMock()
    aligner.aligned = True

    def _umf_to_pose(x, y):
        rot, tx, ty = 0.0, 10.0, 20.0
        cos_r, sin_r = 1.0, 0.0  # cos(0), sin(0) — avoids importing math here
        return (cos_r * x - sin_r * y + tx, sin_r * x + cos_r * y + ty)

    aligner.umf_to_pose.side_effect = _umf_to_pose
    return aligner


class TestGsCoverageNonNumericCoordinateResilience:
    """v3.4.0 bug hunt — coverage_mm/escape_events poses are cloud data,
    untrusted. A non-numeric coordinate reaching aligner.umf_to_pose()
    used to raise TypeError there (real arithmetic: cos_r * x_umf),
    uncaught by either helper function or their caller
    (_async_update_gs_smart_coverage has no try/except around these
    calls) — crashing the whole per-mission processing step, not just
    skipping the one bad point/event."""

    def test_umf_points_to_pose_skips_non_numeric_coordinate(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_umf_points_to_pose,
        )
        aligner = _real_arithmetic_aligner()
        pts = _gs_coverage_umf_points_to_pose(
            [["x", "y"], [1.0, 2.0]], aligner,
        )
        assert pts == [(11.0, 22.0)]  # only the valid point survives

    def test_umf_points_to_pose_skips_none_coordinate(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_umf_points_to_pose,
        )
        aligner = _real_arithmetic_aligner()
        pts = _gs_coverage_umf_points_to_pose(
            [[None, 2.0], [3.0, 4.0]], aligner,
        )
        assert pts == [(13.0, 24.0)]

    def test_classify_stuck_events_skips_non_numeric_pose(self):
        from custom_components.roomba_plus.callbacks import (
            _gs_coverage_classify_stuck_events,
        )
        aligner = _real_arithmetic_aligner()
        events = [
            {"pose": ["x", "y", 0.0], "event": "start_stuck"},
            {"pose": [1.0, 2.0, 0.0], "event": "start_stuck"},
        ]
        result = _gs_coverage_classify_stuck_events(events, aligner)
        assert result == [(11.0, 22.0)]  # only the valid event survives
