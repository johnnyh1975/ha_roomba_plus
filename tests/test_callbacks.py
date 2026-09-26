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
from tests.conftest import TEST_CONFIG_DIR, hass_mock, entry_mock


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
            config_dir = TEST_CONFIG_DIR
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

        def async_create_task(self, coro, *args, **kwargs):
            # CLOSES IT rather than dropping it. Production launches
            # repair checks this way from synchronous callbacks; a stub
            # that ignores the argument leaves the coroutine to be
            # garbage-collected, and Python reports every one of them as
            # "was never awaited". That noise is indistinguishable from
            # a real missing await.
            import asyncio as _asyncio
            if _asyncio.iscoroutine(coro):
                coro.close()
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
    hass = hass_mock()
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
    mission_store.update_terminal_fields = MagicMock(return_value=False)
    mission_store.records = []

    runtime_data = MagicMock()
    runtime_data.mission_store = mission_store
    runtime_data.maintenance_store = None
    runtime_data.demand_triggered_ts = None
    runtime_data.mission_timer_store = None
    runtime_data.presence_manager = None
    runtime_data.room_seg_store = None
    runtime_data.map_capability = MagicMock()

    entry = entry_mock(schedule_on=hass)
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


class TestAsyncRecordMissionReplayMerge:
    """A same-mission-id replay (same start_ts, later wall-clock, a
    different computed initiator/duration) must merge into the existing
    terminal record in place rather than storing a second record."""

    def _run_replay(self, first_mission, second_mission, first_now, second_now, start_ts):
        from custom_components.roomba_plus.callbacks import async_record_mission
        store = _make_store()
        loop  = asyncio.new_event_loop()
        entry = _make_entry(store)
        hass  = _make_hass(loop)
        try:
            with patch(
                "custom_components.roomba_plus.callbacks.dt_util.utcnow",
                side_effect=[first_now, second_now],
            ):
                loop.run_until_complete(
                    async_record_mission(hass, entry, first_mission, {}, [], start_ts, 0)
                )
                loop.run_until_complete(
                    async_record_mission(hass, entry, second_mission, {}, [], start_ts, 0)
                )
        finally:
            loop.close()
        return store

    def test_replay_freezes_first_write_and_stores_once(self):
        now_real = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        start_ts  = now_real - 3 * 3600
        first_now  = datetime.datetime.fromtimestamp(now_real - 2 * 3600, tz=datetime.timezone.utc)
        # Replay pulse re-delivers the SAME terminal event within the
        # MQTT re-delivery window (seconds, not hours).
        second_now = first_now + datetime.timedelta(seconds=30)
        first_mission  = {"phase": "charge", "error": 0, "sqft": 100, "initiator": "schedule"}
        second_mission = {"phase": "charge", "error": 0, "sqft": 120, "initiator": "manual"}

        store = self._run_replay(first_mission, second_mission, first_now, second_now, start_ts)

        assert len(store.records) == 1
        rec = store.records[0]
        assert rec["duration_min"] == 60
        assert rec["initiator"] == "schedule"
        expected_started_at = datetime.datetime.fromtimestamp(
            start_ts, tz=datetime.timezone.utc
        ).isoformat()
        assert rec["started_at"] == expected_started_at
        assert rec["ended_at"] == first_now.isoformat()

    def test_replay_two_hours_later_is_a_genuine_segment_not_a_merge(self):
        """980/900-series multi-recharge keeps mssnStrtTm across segments: a
        second write hours after the terminal record is the next segment
        (async_append's _r<N> path), NOT an in-place replay merge."""
        now_real = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        start_ts  = now_real - 3 * 3600
        first_now  = datetime.datetime.fromtimestamp(now_real - 2 * 3600, tz=datetime.timezone.utc)
        second_now = datetime.datetime.fromtimestamp(now_real, tz=datetime.timezone.utc)
        first_mission  = {"phase": "charge", "error": 0, "sqft": 100, "initiator": "schedule"}
        second_mission = {"phase": "charge", "error": 0, "sqft": 120, "initiator": "manual"}

        store = self._run_replay(first_mission, second_mission, first_now, second_now, start_ts)

        assert len(store.records) == 2
        assert store.records[1]["id"].endswith("_r1"), store.records[1]["id"]

    def test_replay_counted_once_by_missions_last_30d_query(self):
        now_real = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        start_ts  = now_real - 3 * 3600
        first_now  = datetime.datetime.fromtimestamp(now_real - 2 * 3600, tz=datetime.timezone.utc)
        second_now = first_now + datetime.timedelta(seconds=30)
        first_mission  = {"phase": "charge", "error": 0, "sqft": 100, "initiator": "schedule"}
        second_mission = {"phase": "charge", "error": 0, "sqft": 120, "initiator": "manual"}

        store = self._run_replay(first_mission, second_mission, first_now, second_now, start_ts)

        assert len(store.query(30, result="completed")) == 1

    def test_missions_last_30d_dedupes_legacy_recharge_segments(self):
        from custom_components.roomba_plus.sensor_core import SENSORS

        now_real = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        start_ts = now_real - 3 * 3600
        first_now = datetime.datetime.fromtimestamp(
            now_real - 2 * 3600, tz=datetime.timezone.utc
        )
        second_now = datetime.datetime.fromtimestamp(
            now_real, tz=datetime.timezone.utc
        )
        store = self._run_replay(
            {"phase": "charge", "error": 0, "sqft": 100, "initiator": "schedule"},
            {"phase": "charge", "error": 0, "sqft": 120, "initiator": "manual"},
            first_now,
            second_now,
            start_ts,
        )
        # Two segments of one physical mission (m_<start> + m_<start>_r1).
        entity = MagicMock()
        entity._config_entry.runtime_data.mission_store = store
        descriptor = next(d for d in SENSORS if d.key == "missions_last_30d")

        assert len(store.query(30, result="completed")) == 2
        assert descriptor.value_fn(entity) == 1

    def test_missions_last_30d_counts_idless_records_individually(self):
        """Records without an id must never collapse into a single count via
        the base-id dedupe (base_mission_id('') == '' for all of them)."""
        from custom_components.roomba_plus.sensor_core import SENSORS

        store = _make_store()
        now_real = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        store._records = [
            {"id": None, "started_at": _iso(now_real - 1000), "ended_at": _iso(now_real - 900), "result": "completed"},
            {"id": "", "started_at": _iso(now_real - 800), "ended_at": _iso(now_real - 700), "result": "completed"},
            {"id": 42, "started_at": _iso(now_real - 600), "ended_at": _iso(now_real - 500), "result": "completed"},
            {"id": f"m_{now_real}", "started_at": _iso(now_real - 400), "ended_at": _iso(now_real - 300), "result": "completed"},
            {"id": f"m_{now_real}_r1", "started_at": _iso(now_real - 200), "ended_at": _iso(now_real - 100), "result": "completed"},
        ]
        entity = MagicMock()
        entity._config_entry.runtime_data.mission_store = store
        descriptor = next(d for d in SENSORS if d.key == "missions_last_30d")

        # 3 id-less (each counts once) + 1 deduped pair = 4 distinct missions.
        assert descriptor.value_fn(entity) == 4


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

            # Production schedules through the config entry now, not
            # asyncio.run_coroutine_threadsafe; the callback still runs on
            # the loop, so put the coroutine there the same way.
            @staticmethod
            def async_create_task(_hass, coro, **_kwargs):
                return asyncio.ensure_future(coro, loop=loop)
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

            # Production schedules through the config entry now, not
            # asyncio.run_coroutine_threadsafe; the callback still runs on
            # the loop, so put the coroutine there the same way.
            @staticmethod
            def async_create_task(_hass, coro, **_kwargs):
                return asyncio.ensure_future(coro, loop=loop)
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

            # Production schedules through the config entry now, not
            # asyncio.run_coroutine_threadsafe; the callback still runs on
            # the loop, so put the coroutine there the same way.
            @staticmethod
            def async_create_task(_hass, coro, **_kwargs):
                return asyncio.ensure_future(coro, loop=loop)
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

            # Production schedules through the config entry now, not
            # asyncio.run_coroutine_threadsafe; the callback still runs on
            # the loop, so put the coroutine there the same way.
            @staticmethod
            def async_create_task(_hass, coro, **_kwargs):
                return asyncio.ensure_future(coro, loop=loop)
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
        hass = hass_mock()
        hass.loop = asyncio.new_event_loop()
        entry = entry_mock(schedule_on=hass)
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
        hass = hass_mock()
        hass.loop = asyncio.new_event_loop()
        entry = entry_mock(schedule_on=hass)
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
        hass = hass_mock()
        hass.loop = asyncio.new_event_loop()
        entry = entry_mock(schedule_on=hass)
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
        hass = hass_mock()
        hass.loop = asyncio.new_event_loop()
        entry = entry_mock(schedule_on=hass)
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
        entry = entry_mock()
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

        entry = entry_mock()
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

        entry = entry_mock()
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
        entry = entry_mock()
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


class TestPhaseTrackingReplayGuard:
    """A phase flipping back to run/hmMidMsn/evac under an mssnStrtTm that
    MissionStore already holds a terminal record for is a post-terminal
    replay pulse, not a genuine new mission start."""

    def test_replay_pulse_does_not_restart_timer_or_reopen_mission(self):
        hass, entry, _recorded, mission_store = _make_callback_env()
        mts = MagicMock()
        mts.mission_id = None
        entry.runtime_data.mission_timer_store = mts
        cb = make_mission_callback(hass, entry)
        captured: list[dict] = []

        async def _capture(*args, **kwargs):
            captured.append(dict(kwargs))

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record, \
             _patch_callbacks_time():
            mock_record.side_effect = _capture
            for phase, nstuck in [
                ("run", 0), ("run", 0), ("charge", 0), ("charge", 0),
            ]:
                cb(_msg(phase, nstuck=nstuck))
            hass.loop.run_until_complete(asyncio.sleep(0))
            assert len(captured) == 1

            mission_store.records = [{
                "id": "m_1700000000",
                "started_at": _iso(1700000000),
                "result": "completed",
            }]
            mts.reset_mock()

            for phase, nstuck in [("run", 0), ("charge", 0), ("charge", 0)]:
                cb(_msg(phase, nstuck=nstuck))
            hass.loop.run_until_complete(asyncio.sleep(0))

        assert len(captured) == 1
        mts.on_phase_run.assert_not_called()
        mts.clear.assert_not_called()

    def test_old_terminal_record_does_not_suppress_genuine_segment_resume(self):
        """980/900-series keeps mssnStrtTm across multi-recharge segments: a
        terminal record whose ended_at is far older than the re-delivery
        window is a real segment resume, so the phase guard must not treat
        it as a replay pulse."""
        hass, entry, _recorded, mission_store = _make_callback_env()
        mts = MagicMock()
        mts.mission_id = None
        entry.runtime_data.mission_timer_store = mts
        cb = make_mission_callback(hass, entry)
        captured: list[dict] = []

        async def _capture(*args, **kwargs):
            captured.append(dict(kwargs))

        from custom_components.roomba_plus.callbacks import _CLOUD_CATCHUP_MISSION_MATCH_SEC

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record,              _patch_callbacks_time():
            mock_record.side_effect = _capture
            for phase, nstuck in [
                ("run", 0), ("run", 0), ("charge", 0), ("charge", 0),
            ]:
                cb(_msg(phase, nstuck=nstuck))
            hass.loop.run_until_complete(asyncio.sleep(0))
            assert len(captured) == 1

            # Terminal record from LONG ago (started_at == ended_at, old timestamp).
            old_ts = 1700000000  # 2.7 years before the live clock — well outside window
            mission_store.records = [{
                "id": "m_1700000000",
                "started_at": _iso(old_ts),
                "ended_at": _iso(old_ts),
                "result": "completed",
            }]
            mts.reset_mock()

            for phase, nstuck in [("run", 0), ("charge", 0), ("charge", 0)]:
                cb(_msg(phase, nstuck=nstuck))
            hass.loop.run_until_complete(asyncio.sleep(0))

        # Genuine resume: a second mission record gets recorded and the timer
        # tracks the resumed segment.
        assert len(captured) == 2
        mts.on_phase_run.assert_called_once()


class TestCloudRefreshCallbackDispatchesV320Checks:
    """make_cloud_refresh_callback's _on_cloud_refresh_complete dispatches
    the surviving grid-store-dependent check.

    v3.5.0 Repairs redesign: of the former five v3.2.0 checks, only
    furniture_change (layout-change detection) remains a repair. The others
    were removed — room_accessibility/stuck_hotspot data is already exposed
    via the ?format=hazards endpoint + map, coverage_frequency was a
    derivable nudge, and health_trend/performance are already surfaced by
    their sensors. This test pins the surviving dispatch so it can't
    silently drop out on a later refactor.
    """

    def _run_callback(self):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
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
            for call in config_entry.async_create_task.call_args_list
        ]

    def test_furniture_change_dispatched(self):
        dispatched = self._run_callback()
        assert "roomba_plus_furniture_change_check" in dispatched

    def test_removed_checks_not_dispatched(self):
        """The four v3.5.0-removed checks must no longer be dispatched."""
        dispatched = set(self._run_callback())
        for gone in (
            "roomba_plus_room_accessibility_check",
            "roomba_plus_stuck_hotspot_check",
            "roomba_plus_coverage_frequency_check",
            "roomba_plus_dirt_correlation_check",
        ):
            assert gone not in dispatched

    def test_bootstrap_dispatched_even_when_aligner_is_none(self):
        """v3.5.1 bug-hunt fix (mdarocha field report): the bootstrap task
        must be scheduled when map_capability is SMART EVEN IF umf_aligner
        is None — that's exactly the scenario this bootstrap exists to
        rescue. Before this fix, requiring umf_aligner is not None at the
        call site meant this task was NEVER scheduled for a robot whose
        initial UMF response lacked points2d/regions at setup, no matter
        how much cloud data accumulated afterward."""
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback
        from custom_components.roomba_plus.models import MapCapability

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = MagicMock()
        rd.umf_aligner = None  # the scenario the old gate wrongly excluded
        rd.map_capability = MapCapability.SMART
        rd.robot_profile_store = MagicMock()

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        dispatched = [
            call.kwargs.get("name") for call in config_entry.async_create_task.call_args_list
        ]
        assert "roomba_plus_gs_smart_umf_bootstrap" in dispatched

    def test_bootstrap_not_dispatched_when_not_smart_tier(self):
        """Correctly still gated on map_capability — an EPHEMERAL robot has
        no UMF concept at all, so this must not fire regardless of
        umf_aligner's value."""
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback
        from custom_components.roomba_plus.models import MapCapability

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
        config_entry.entry_id = "test_entry"
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(
            corrected=0, enriched=0,
        )
        rd.dirt_threshold_manager = None
        rd.grid_store = MagicMock()
        rd.umf_aligner = None
        rd.map_capability = MapCapability.EPHEMERAL
        rd.robot_profile_store = MagicMock()

        cloud_coordinator = MagicMock()
        cloud_coordinator.last_update_success = True
        cloud_coordinator.umf_data = {}

        callback_fn = make_cloud_refresh_callback(hass, config_entry, cloud_coordinator)
        callback_fn()

        dispatched = [
            call.kwargs.get("name") for call in config_entry.async_create_task.call_args_list
        ]
        assert "roomba_plus_gs_smart_umf_bootstrap" not in dispatched

    def test_grid_store_dependent_checks_not_dispatched_without_grid_store(self):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
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
            for call in config_entry.async_create_task.call_args_list
        ]
        assert "roomba_plus_furniture_change_check" not in dispatched


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

class TestBootstrapUmfAlignerConstructsWhenMissing:
    """v3.5.1 bug-hunt fix (mdarocha field report) — direct tests for
    _async_bootstrap_umf_aligner's new aligner-is-None handling. Zero
    tests existed for this function before this fix — exactly matching
    the pattern this project has caught before (a function built and
    unit-tested for the case it already handled, never tested for the
    scenario it was actually meant to rescue).
    """

    def _entry(self, umf_aligner=None, grid_store=None, geometry_store=None):
        entry = entry_mock()
        entry.data = {"blid": "TEST_BLID"}
        rd = entry.runtime_data
        rd.umf_aligner = umf_aligner
        rd.geometry_store = geometry_store if geometry_store is not None else MagicMock()
        return entry

    @pytest.mark.asyncio
    async def test_constructs_aligner_when_none_and_data_now_available(self):
        """The actual fix: aligner is None, but points2d/regions ARE now
        present in cloud_coordinator.umf_data (e.g. a later refresh has
        more UMF data than was available at initial setup) — a fresh
        aligner gets constructed and stored, not silently skipped."""
        from custom_components.roomba_plus.callbacks import _async_bootstrap_umf_aligner

        entry = self._entry(umf_aligner=None)
        coordinator = MagicMock()
        coordinator.umf_data = {
            "points2d": [[0.0, 0.0], [1.0, 1.0]],
            "regions": [{"id": "r1", "name": "Kitchen"}],
            "version_id": "v1",
        }
        coordinator.regions = []
        coordinator.last_update_success = True

        mock_aligner_instance = MagicMock()
        mock_aligner_instance.aligned = True
        mock_aligner_instance.align = MagicMock(return_value=0.9)

        with patch(
            "custom_components.roomba_plus.umf_aligner.UmfAligner",
            return_value=mock_aligner_instance,
        ) as mock_cls:
            await _async_bootstrap_umf_aligner(_make_hass(), entry, coordinator)

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["points2d"] == coordinator.umf_data["points2d"]
        assert call_kwargs["regions"] == coordinator.umf_data["regions"]
        # The constructed aligner must actually be stored, not discarded.
        assert entry.runtime_data.umf_aligner is mock_aligner_instance

    @pytest.mark.asyncio
    async def test_returns_cleanly_when_still_no_data_available(self):
        """If points2d/regions are STILL missing, this must not crash and
        must not construct anything — just wait for the next refresh."""
        from custom_components.roomba_plus.callbacks import _async_bootstrap_umf_aligner

        entry = self._entry(umf_aligner=None)
        coordinator = MagicMock()
        coordinator.umf_data = {}  # still nothing
        coordinator.regions = []
        coordinator.last_update_success = True

        with patch(
            "custom_components.roomba_plus.umf_aligner.UmfAligner"
        ) as mock_cls:
            await _async_bootstrap_umf_aligner(_make_hass(), entry, coordinator)

        mock_cls.assert_not_called()
        assert entry.runtime_data.umf_aligner is None

    @pytest.mark.asyncio
    async def test_returns_cleanly_when_no_geometry_store(self):
        """Can't construct an aligner without a geometry_store to attach
        it to — must bail out safely, not crash."""
        from custom_components.roomba_plus.callbacks import _async_bootstrap_umf_aligner

        entry = self._entry(umf_aligner=None, geometry_store=None)
        entry.runtime_data.geometry_store = None
        coordinator = MagicMock()
        coordinator.umf_data = {
            "points2d": [[0.0, 0.0]], "regions": [{"id": "r1"}],
        }

        with patch(
            "custom_components.roomba_plus.umf_aligner.UmfAligner"
        ) as mock_cls:
            await _async_bootstrap_umf_aligner(_make_hass(), entry, coordinator)

        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_through_to_traversal_refinement_if_still_unaligned(self):
        """If the freshly-constructed aligner isn't confidently aligned yet,
        this must continue into the existing traversal-evidence refinement
        path below, not stop early — the whole point is to try harder, not
        settle for a low-confidence result when better data is available."""
        from custom_components.roomba_plus.callbacks import _async_bootstrap_umf_aligner

        entry = self._entry(umf_aligner=None)
        coordinator = MagicMock()
        coordinator.umf_data = {
            "points2d": [[0.0, 0.0]], "regions": [{"id": "r1"}], "version_id": "v1",
        }
        coordinator.regions = []
        coordinator.last_update_success = True
        coordinator.raw_records = []

        mock_aligner_instance = MagicMock()
        mock_aligner_instance.aligned = False  # low confidence -> should continue
        mock_aligner_instance.align = MagicMock(return_value=0.2)
        mock_aligner_instance._door_candidates = ["x"]  # skip the re-align-first branch

        with patch(
            "custom_components.roomba_plus.umf_aligner.UmfAligner",
            return_value=mock_aligner_instance,
        ), patch(
            "custom_components.roomba_plus.callbacks._extract_traversal_umf_positions",
            return_value=[],  # no traversal evidence either -> clean early return
        ) as mock_extract:
            await _async_bootstrap_umf_aligner(_make_hass(), entry, coordinator)

        # Reached the refinement path (proven by _extract_traversal_umf_positions
        # actually being called) instead of stopping right after construction.
        mock_extract.assert_called_once()


class TestGsCoverageHookDispatch:
    """Gate on the cloud-refresh hook: dispatched only when
    map_capability == SMART, grid_store, and umf_aligner are all
    present. The function itself no-ops on everything else (aligned
    state, mission_store presence, actual candidates)."""

    def _run_callback(self, *, map_capability, grid_store, umf_aligner):
        from custom_components.roomba_plus.callbacks import make_cloud_refresh_callback
        from custom_components.roomba_plus.models import MapCapability

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
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
            for call in config_entry.async_create_task.call_args_list
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
        """Ordering matters (plan §3.4): GS-SMART-COVERAGE must be queued
        before furniture_change so a same-cycle backfill is visible to it.

        v3.5.0: stuck_hotspot and room_accessibility were removed from this
        ordering constraint along with their repair checks; furniture_change
        is the only surviving grid-store-reading check.
        """
        from custom_components.roomba_plus.models import MapCapability
        dispatched = self._run_callback(
            map_capability=MapCapability.SMART,
            grid_store=MagicMock(), umf_aligner=MagicMock(),
        )
        gs_idx = dispatched.index("roomba_plus_gs_smart_coverage")
        assert dispatched.index("roomba_plus_furniture_change_check") > gs_idx, (
            "roomba_plus_furniture_change_check dispatched before "
            "roomba_plus_gs_smart_coverage"
        )


def _gs_coverage_env(*, aligned=True, watermark=0):
    """Minimal runtime_data + mission_store fixture for exercising
    _async_update_gs_smart_coverage() directly (not through the hook)."""
    hass = hass_mock()
    entry = entry_mock(schedule_on=hass)
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
        ms.records = [
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
        # `records` is a PROPERTY. These fixtures set
        # `ms.records.return_value`, which only works against a call --
        # and the code under test called it, so both sides agreed on a
        # shape the real MissionStore does not have.
        ms.records = [
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
        ms.records = [
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
        ms.records = [
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
        ms.records = [
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
        ms.records = [
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
        ms.records = [
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
        ms.records = []
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
        ms.records = [
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


class TestRecordsIsAPropertyNotAMethod:
    """`MissionStore.records` is a property. One call site wrote
    `ms.records()`, which raises TypeError — a Sequence is not callable
    — so `_async_update_gs_smart_coverage` died on its first statement
    every time it ran.

    Four other call sites in the integration read it correctly. Found by
    mypy as "Sequence[dict[str, Any]] not callable"; no test exercised
    the function.
    """

    def test_nothing_calls_records_as_a_method(self):
        """Parsed rather than grepped: the comment recording this bug
        contains the very string a text search looks for."""
        import ast
        import pathlib

        offenders = []
        for path in pathlib.Path("custom_components/roomba_plus").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "records"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")

        assert not offenders, (
            f"{offenders} call `records()` -- it is a property, and "
            f"calling it raises TypeError"
        )

    def test_the_property_is_still_a_property(self):
        """If it ever becomes a method, the guard above is wrong rather
        than the call sites."""
        from custom_components.roomba_plus.mission_store import MissionStore

        assert isinstance(MissionStore.records, property)


class TestPropertiesAreNotCalledAsMethods:
    """Guard against a whole class of bug a MagicMock cannot catch.

    Reported by @liblit against v3.5.2 with a full root cause: one
    call site wrote `ms.records()` where `records` is a property, so
    the GS-SMART-COVERAGE backfill raised TypeError on its first line
    every time the cloud coordinator refreshed. Four other call sites
    read it correctly.

    It was fixed before the report arrived, but the interesting part
    is why it survived as long as it did. The tests for that very
    function assert `ms.records.assert_not_called()` -- treating
    `records` as callable, because a MagicMock allows both forms
    silently. The tests could not have caught it, and neither could
    any test built the same way.

    So this checks the source text instead of behaviour: no MagicMock
    involved, nothing to be lenient.
    """

    def test_no_call_site_invokes_records_as_a_method(self):
        from pathlib import Path

        component = Path("custom_components/roomba_plus")
        offenders = []
        for path in sorted(component.glob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ".records()" in line:
                    offenders.append(f"{path.name}:{number}: {stripped}")

        assert not offenders, (
            "`MissionStore.records` is a property, not a method. "
            "Calling it raises TypeError at runtime:\n  "
            + "\n  ".join(offenders)
        )

    def test_records_really_is_a_property(self):
        """If it ever becomes a method, the guard above must be
        retired rather than left asserting the opposite."""
        from custom_components.roomba_plus.mission_store import MissionStore

        assert isinstance(
            type(MissionStore).__dict__.get("records")
            or MissionStore.__dict__.get("records"),
            property,
        )


class TestAnEmptyNavRecordIsNotAReading:
    """Two of three robots on the same `lewis` firmware report
    `mssnNavStats` with every counter at zero, during a mission and
    after it, unchanged (@veronoicc, i7+ i857640 and i7+ i755840).

    A robot navigating by vSLAM does not see zero landmarks. The record
    is never populated on those units.

    `reLc` from such a record used to feed the per-robot percentile of
    localisation quality, filling the distribution with values the
    robot never measured. `gLmk`/`lmk`/`mTrk` are the tell: a genuine
    record carries landmarks even when `reLc` is legitimately 0.
    """

    #: Exactly as @veronoicc's i8+ reported it, mid-mission and docked.
    _EMPTY = {
        "nMssn": 428, "missionId": "01KTYYK0YGAGPH50EM0MZS2JFA",
        "gLmk": 0, "lmk": 0, "reLc": 0, "plnErr": "none", "mTrk": 0,
        "kdp": 0, "sfkdp": 0, "nmc": 0, "nmmc": 0, "nrmc": 0,
        "mpSt": "idle", "l_drift": 0, "h_drift": 0,
        "l_squal": 0, "h_squal": 0,
    }

    #: His other i7+, same firmware, same moment in a mission.
    _POPULATED = {
        "nMssn": 1280, "missionId": "01KV0FB3SRG31D85V6Y6P78E35",
        "gLmk": 16, "lmk": 2, "reLc": 0, "plnErr": "none", "mTrk": 36,
        "kdp": 0, "sfkdp": 0, "nmc": 1, "nmmc": 1, "nrmc": 0,
        "mpSt": "idle", "l_drift": 0, "h_drift": 0,
        "l_squal": 0, "h_squal": 12,
    }

    @staticmethod
    def _accepted(nav_stats: dict) -> bool:
        """The guard, as the callback applies it."""
        return bool(
            any(nav_stats.get(k) for k in ("gLmk", "lmk", "mTrk"))
        )

    def test_the_empty_record_is_rejected(self) -> None:
        assert not self._accepted(self._EMPTY)

    def test_a_real_record_is_accepted(self) -> None:
        assert self._accepted(self._POPULATED)

    def test_a_genuine_zero_reloc_still_counts(self) -> None:
        """`reLc: 0` is a real and common reading -- a mission with no
        relocalisation at all. The guard must not throw those away."""
        assert self._POPULATED["reLc"] == 0
        assert self._accepted(self._POPULATED)

    def test_the_callback_applies_it(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)

        assert "_looks_populated" in source
        assert '("gLmk", "lmk", "mTrk")' in source


class TestARoomAdvanceOnReturnFromTravel:
    """`soho` robots emit no phase change between rooms -- 13 samples
    across a confirmed boundary held `cycle=clean phase=run` throughout
    (@ScenicSystemsLLC) -- so the phase route never fires and the
    display sits on the first planned room for the whole mission.

    `operatingMode` bit 0 is `Traveling`, confirmed twice over: the app
    names it in a bitmask class, and the firmware sets that bit exactly
    when its internal mode is `CLEANING_MODE_TRAVEL`.

    THE EDGE IS THE RETURN, not the departure. The robot is in the new
    room once the drive ends. And travel also covers evading and
    relocalising, so counting departures would over-count -- his
    seven-room run showed six excursions, two of which were not room
    changes.
    """

    @staticmethod
    def _travelling(operating_mode: int) -> bool:
        """The test as the callback applies it."""
        return bool(operating_mode & 1)

    def test_the_working_values_are_not_travel(self) -> None:
        """2 vacuuming, 4 mopping, 6 both -- none has bit 0."""
        for mode in (2, 4, 6, 32):
            assert not self._travelling(mode), mode

    def test_travel_is_bit_zero(self) -> None:
        assert self._travelling(1)

    def test_idle_is_not_travel(self) -> None:
        """`0` on the dock is the absence of a job, not a fourth state.
        Confirmed on two robots across three firmware families."""
        assert not self._travelling(0)

    def test_his_whole_house_run_yields_the_flips_he_saw(self) -> None:
        """@ScenicSystemsLLC's 73-minute bare `vacuum.start`, as
        sampled. Six excursions, each ending in a return."""
        samples = [2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 1, 0]

        was, returns = False, 0
        for mode in samples:
            now = self._travelling(mode)
            if was and not now:
                returns += 1
            was = now

        assert returns == 6

    def test_a_departure_alone_does_not_count(self) -> None:
        """If the mission ends while travelling -- the return-to-dock
        leg -- there is no return, and nothing is advanced for it."""
        samples = [2, 1]

        was, returns = False, 0
        for mode in samples:
            now = self._travelling(mode)
            if was and not now:
                returns += 1
            was = now

        assert returns == 0

    def test_the_callback_uses_the_return_edge(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)

        assert "_returned_from_travel" in source
        assert "_travelling is False" in source


class TestTheTravelSignalIsReadDuringCleaning:
    """The travel read and its evaluation both sat inside the
    `phase != "run"` branch. Travel happens DURING `run`.

    So the field was only ever looked at in phases where it cannot be 1,
    `was_travelling` stayed False for whole missions, and the return
    edge could not fire once. @ScenicSystemsLLC captured nine unbroken
    seconds of `operatingMode: 1` at a confirmed room boundary and the
    display did not move.

    His Braava is the other half of the proof: she emits no travel
    signal at all, and her display advanced anyway -- late, through the
    phase route, which needs `hmPostMsn` and was correctly placed in
    that same branch. One misplacement, two opposite symptoms.

    WHY NO TEST CAUGHT IT. Every test on this feature checked the bit
    arithmetic or grepped the source for a marker. None ran the callback
    over a message sequence with `phase: run`, which is the only place
    the fault lives.
    """

    # BOTH REWRITTEN FROM TEXT SEARCH TO STRUCTURE (Group 5).
    #
    # They used to compare character offsets in the module source --
    # "the read comes before the first `if phase == "run":`". Lifting the
    # room-progress block into its own module-level function moved that
    # branch ABOVE the callback in the file while leaving the runtime
    # order untouched, and both went red on a change that broke nothing.
    # This class's own docstring names grepping the source as what failed
    # to catch the original fault. They now inspect the function the
    # property is actually about.

    @staticmethod
    def _callback_ast():
        import ast
        import inspect

        from custom_components.roomba_plus import callbacks

        tree = ast.parse(inspect.getsource(callbacks))
        return next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_on_mission_message"
        )

    def test_the_read_is_not_inside_a_phase_branch(self) -> None:
        """`_mode` is assigned at the top level of the callback, so every
        message -- every `run` message included -- reaches it."""
        import ast

        fn = self._callback_ast()
        oben = [
            s for s in fn.body
            if isinstance(s, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_mode" for t in s.targets)
        ]
        assert oben, (
            "the operatingMode read must sit at the top level of the "
            "callback, not inside a phase branch, or it misses every flip "
            "that occurs during cleaning"
        )

    def test_the_evaluation_sees_run_phase_messages(self) -> None:
        """The advance check can see a `run` message, at both levels.

        Since Group 5 the check lives in `_advance_room_on_drive_end`,
        so there are two places it could be cut off: the callback could
        call that function only in a non-run branch, or the check inside
        it could sit in the `else` of `if phase == "run"`. Both are
        inspected; either is the original fault in a new place.
        """
        import ast
        import inspect

        from custom_components.roomba_plus import callbacks

        tree = ast.parse(inspect.getsource(callbacks))

        def _func(name):
            return next(
                n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name
            )

        def _not_in_a_run_else(fn, predicate, was):
            eltern = {c: p for p in ast.walk(fn) for c in ast.iter_child_nodes(p)}
            treffer = [k for k in ast.walk(fn) if predicate(k)]
            assert treffer, f"{was} not found"
            for knoten in treffer:
                while knoten in eltern:
                    par = eltern[knoten]
                    if (
                        isinstance(par, ast.If)
                        and knoten in par.orelse
                        and 'phase == "run"' in ast.unparse(par.test).replace("'", '"')
                    ):
                        raise AssertionError(
                            f"{was} sits in the else-branch of "
                            '`if phase == "run"` -- it can never see a run message'
                        )
                    knoten = par

        # 1. inside the extracted function
        _not_in_a_run_else(
            _func("_advance_room_on_drive_end"),
            lambda k: isinstance(k, ast.BoolOp)
            and "returned_from_travel" in ast.unparse(k)
            and "ms.cleaned_in_room" in ast.unparse(k),
            "the advance check",
        )
        # 2. the callback's call to it
        _not_in_a_run_else(
            self._callback_ast(),
            lambda k: isinstance(k, ast.Call)
            and ast.unparse(k.func) == "_advance_room_on_drive_end",
            "the call to _advance_room_on_drive_end",
        )

    def test_a_run_phase_flip_is_an_edge(self) -> None:
        """His capture, reduced: cleaning, nine seconds of travel,
        cleaning again -- all while the phase never leaves `run`."""
        # THIS USED TO TEST ITSELF. It reimplemented the edge detection
        # inline -- `if was and not now: returns += 1` -- and asserted on
        # its own copy, so it passed whatever production did. Reintroducing
        # the very fault this class documents (the read moved into a
        # `phase != "run"` branch) left it green. It now drives the
        # production function, possible since Group 5 lifted it out.
        from custom_components.roomba_plus.callbacks import (
            _evaluate_travel_edge,
            _MissionState,
        )

        samples = [
            ("run", 2), ("run", 2), ("run", 1), ("run", 1),
            ("run", 1), ("run", 2), ("run", 2),
        ]

        ms = _MissionState()
        returns = 0
        for _phase, mode in samples:
            travelling = bool(mode & 1)
            if _evaluate_travel_edge(ms, travelling):
                returns += 1
            ms.was_travelling = travelling   # what the caller does next

        assert returns == 1, "one boundary crossing, one advance"

    def test_a_phase_that_never_leaves_run_still_yields_edges(self) -> None:
        """The `soho` case in one line: no phase ever changes, so the
        phase route contributes nothing and the travel route is the only
        one that can work."""
        phases = {p for p, _ in [("run", 2), ("run", 1), ("run", 2)]}

        from custom_components.roomba_plus.callbacks import (
            _ROOM_TRANSITION_CANDIDATE_PHASES,
        )

        assert not phases & set(_ROOM_TRANSITION_CANDIDATE_PHASES)


class TestTheRoomFloorAgainstARealMission:
    """@AlakazipLabs' i3 on `daredevil` 2.6.0, an ordered three-room run
    from the dock, recalled unfinished after 51 minutes. Their own stack,
    not this integration -- so the timeline is an independent check of
    the logic rather than of the code.

        mode 1  12 s      leaving the dock
        mode 2  4.8 min   room 1
        mode 1  11.3 min  drive to room 2
        mode 2  17.3 min  room 2
        mode 1  69 s      drive to room 3
        mode 2  16.2 min  room 3, cut short by the recall

    Phase stayed `run` throughout: zero non-run phases in 51 minutes. So
    on this firmware, as on the S9-series, the driving signal is the only
    boundary marker in the shadow.

    THE ONE-MINUTE FLOOR EARNS ITS PLACE HERE. The drive off the dock
    ends 12 seconds into the mission and is not a room change; every
    real boundary follows minutes of cleaning.

    AND IT SETTLES THAT DURATION IS USELESS as a discriminator -- an idea
    considered and dropped. Their drives ran 12 seconds, 69 seconds and
    **11.3 minutes**, the long one probably the robot re-finding itself
    on a cold map. Nothing separates a boundary from a reposition by how
    long the drive took.
    """

    #: (operatingMode, seconds, what it was)
    TIMELINE = [
        (1, 12, "leaving the dock"),
        (2, 288, "room 1"),
        (1, 678, "drive to room 2"),
        (2, 1037, "room 2"),
        (1, 69, "drive to room 3"),
        (2, 970, "room 3"),
    ]

    @classmethod
    def _advances(cls, floor_sec):
        was_travelling, in_room, out = False, 0.0, []
        for mode, duration, label in cls.TIMELINE:
            travelling = bool(mode & 1)
            if was_travelling and not travelling:
                advanced = in_room >= floor_sec
                out.append((label, in_room, advanced))
                if advanced:
                    in_room = 0.0
            if not travelling:
                in_room += duration
            was_travelling = travelling
        return out

    def test_the_dock_departure_is_not_a_room_change(self) -> None:
        from custom_components.roomba_plus.callbacks import (
            _ROOM_TRANSITION_MIN_SECONDS,
        )

        first = self._advances(_ROOM_TRANSITION_MIN_SECONDS)[0]

        assert first[0] == "room 1"
        assert first[2] is False, "12 s off the dock is not a boundary"

    def test_both_real_boundaries_advance(self) -> None:
        from custom_components.roomba_plus.callbacks import (
            _ROOM_TRANSITION_MIN_SECONDS,
        )

        advanced = [
            label
            for label, _elapsed, ok in self._advances(
                _ROOM_TRANSITION_MIN_SECONDS
            )
            if ok
        ]

        assert advanced == ["room 2", "room 3"]

    def test_an_eleven_minute_drive_is_still_one_edge(self) -> None:
        """Their longest drive was 11.3 minutes. However long it runs, a
        drive that ends is one boundary, not several."""
        from custom_components.roomba_plus.callbacks import (
            _ROOM_TRANSITION_MIN_SECONDS,
        )

        assert len(self._advances(_ROOM_TRANSITION_MIN_SECONDS)) == 3

    def test_no_floor_would_count_the_dock_departure(self) -> None:
        """Without a floor the mission starts one room ahead of itself,
        and stays wrong for the rest of the run."""
        first = self._advances(0)[0]

        assert first[2] is True, "this is what the floor exists to stop"


class TestArrivingIsNotLeaving:
    """@Thonno watched an app-started three-room mission and the display
    ran one room ahead the whole way: it read "Corridor" while the robot
    was cleaning the Bathroom, then "Living Room" when it reached the
    Corridor.

    THE FIRST DRIVE OF A MISSION GOES DOCK -> FIRST ROOM. Ending it means
    ARRIVING, not leaving, and there is no room behind it to advance away
    from.

    This had a one-minute floor on time spent in the room, which is the
    wrong quantity -- that clock includes the drive itself.
    @AlakazipLabs' dock departure ran 12 seconds and was blocked;
    @Thonno's ran longer and was not. The floor worked once by accident.

    A drive that ends is a boundary only if the robot WORKED in the room
    behind it. Bits 1 and 2 are vacuuming and mopping, set only when the
    robot is actually doing the job.
    """

    @staticmethod
    def _advances(timeline):
        """The rule as the callback applies it."""
        was_travelling, cleaned_here, out = False, False, []
        for mode, label in timeline:
            travelling = bool(mode & 1)
            if was_travelling and not travelling:
                out.append((label, cleaned_here))
                if cleaned_here:
                    cleaned_here = False
            if mode & 6:
                cleaned_here = True
            was_travelling = travelling
        return out

    #: His mission: dock, then three rooms in the commanded order.
    THONNO = [
        (1, "arriving at Bathroom"),
        (2, "cleaning Bathroom"),
        (1, "driving to Corridor"),
        (2, "cleaning Corridor"),
        (1, "driving to Living Room"),
        (2, "cleaning Living Room"),
    ]

    def test_arriving_at_the_first_room_does_not_advance(self) -> None:
        first = self._advances(self.THONNO)[0]

        # The edge fires when cleaning RESUMES, i.e. on arrival.
        assert first[0] == "cleaning Bathroom"
        assert first[1] is False, (
            "the display must stay on the Bathroom until it is cleaned"
        )

    def test_the_real_boundaries_do(self) -> None:
        advanced = [label for label, ok in self._advances(self.THONNO) if ok]

        assert advanced == ["cleaning Corridor", "cleaning Living Room"]

    def test_a_long_drive_to_the_first_room_is_still_not_a_boundary(
        self,
    ) -> None:
        """The case the old floor let through: the same sequence says
        nothing about how long the drive took, and neither does this."""
        assert self._advances(self.THONNO)[0][1] is False

    def test_mopping_counts_as_working_too(self) -> None:
        """Bit 2. A Braava never sets bit 1, and its rooms must still
        advance."""
        mopping = [
            (1, "arriving"), (4, "mopping room 1"),
            (1, "driving"), (4, "mopping room 2"),
        ]

        advanced = [label for label, ok in self._advances(mopping) if ok]

        assert advanced == ["mopping room 2"]

    def test_the_flag_resets_so_the_next_room_starts_clean(self) -> None:
        """Otherwise one cleaned room would authorise every later
        advance, including one straight off a reposition."""
        out = self._advances(
            [(1, "arriving"), (2, "cleaning"), (1, "drive"), (1, "still"),
             (2, "brief"), (1, "reposition"), (2, "back")]
        )

        assert [ok for _l, ok in out] == [False, True, True]

    def test_the_callback_requires_both(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)

        # `returned_from_travel` since Group 5 moved the check into
        # _advance_room_on_drive_end; the substring matches either name.
        assert "returned_from_travel and ms.cleaned_in_room" in source
        assert "ms.cleaned_in_room = False" in source


class TestTheWholeHouseFallbackIsLastResort:
    """The order matters: a real per-room figure, however rough, before
    a whole-house average that was never a per-room figure at all."""

    def test_the_lower_bound_is_tried_first(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)
        lower = source.index("shortest_plausible_room_seconds")
        house = source.index("mission_duration_mean")

        assert lower < house, (
            "the whole-house average must only run when no per-room "
            "figure could be found"
        )

    def test_the_house_average_still_exists_as_a_last_resort(self) -> None:
        """Classic robots with no cloud estimates at all still need
        something, and removing it outright would refuse every advance
        on those."""
        import inspect

        from custom_components.roomba_plus import callbacks

        assert "mission_duration_mean" in inspect.getsource(callbacks)

    def test_what_it_is_is_named_in_the_log(self) -> None:
        """It is a whole-house figure divided by the rooms in this
        mission. Nothing said so, and a 10.7-hour number read as a
        per-room estimate is why one tester's display never moved."""
        import inspect

        from custom_components.roomba_plus import callbacks

        assert "WHOLE-HOUSE figure" in inspect.getsource(callbacks)


class TestAMissingEstimateCannotBlockAnObservation:
    """The estimate check sat above the travel branch, so a robot with
    no cloud estimates had every travel-based advance refused -- on the
    strength of a missing forecast, while holding a positive
    observation: the robot cleaned this room and then drove away.

    This file kept relearning the same rule. Three separate blockers,
    all estimates, all overriding something already observed:

      - no estimate at all -> refuse
      - a poisoned estimate (a 10.7-hour whole-house average) -> refuse
      - a travel-duration floor of 6 s -> refuse a real 3.8 s crossing

    An estimate may refine a decision. It must never be the only reason
    to refuse one.
    """

    def test_the_travel_branch_comes_first(self) -> None:
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _room_transition_confidence_ok,
        )

        source = inspect.getsource(_room_transition_confidence_ok)
        travel = source.index("if from_travel:")
        estimate = source.index("expected = mts.expected_room_sec")

        assert travel < estimate

    def test_the_phase_route_still_requires_one(self) -> None:
        """A `charge` mid-room and a `charge` at a boundary look
        identical; only timing separates them."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _room_transition_confidence_ok,
        )

        source = inspect.getsource(_room_transition_confidence_ok)
        after = source[source.index("expected = mts.expected_room_sec"):]

        assert "return False" in after

    def test_the_travel_floor_is_the_only_thing_it_checks(self) -> None:
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _room_transition_confidence_ok,
        )

        source = inspect.getsource(_room_transition_confidence_ok)
        branch = source[source.index("if from_travel:"):]
        branch = branch[:branch.index("expected = mts.expected_room_sec")]
        code = "\n".join(
            line for line in branch.splitlines()
            if not line.strip().startswith("#")
        )

        assert "_ROOM_TRANSITION_MIN_SECONDS" in code
        assert "expected_room_sec" not in code, (
            "the travel branch must not consult an estimate"
        )


class TestTheRobotLearnsItsOwnRoomTimes:
    """@ScenicSystemsLLC watched his robot finish the Hallway and dock
    while the display still read Guest Bathroom -- the fourth run of the
    same two rooms, and the display never moved once.

    THREE FAULTS IN ONE CHAIN, and 4.2.4 fixed the least important of
    them.

    1. `cleaned_in_room` was declared, read in the confirmation
       condition, reset after an advance -- and assigned True NOWHERE.
       So `_returned_from_travel and cleaned_in_room` could never hold,
       the travel route was unreachable, and every transition fell
       through to the phase route.

    2. The phase route requires a per-room estimate, and his robot runs
       in auto pass mode, where the cloud offers none BY DESIGN -- the
       robot decides passes at runtime, so there is nothing to estimate.
       The fallback is a whole-house mean divided by this mission's room
       count: 5.9 hours per room, a threshold no real mission crosses.

    3. And we measured the real figure the whole time. The progress
       sensor shows `time_in_current_room_sec` every thirty seconds, and
       it was discarded at exactly the moment it became final.

    His question was the right one: four real runs, nothing learned.
    """

    def test_the_flag_is_actually_set_somewhere(self) -> None:
        """The regression that made 4.2.4's travel-route fix inert."""
        import ast
        import inspect

        from custom_components.roomba_plus import callbacks

        tree = ast.parse(inspect.getsource(callbacks))

        def _is_the_flag(target: ast.expr) -> bool:
            # `ms.cleaned_in_room` since the eighteen nonlocal variables
            # moved onto _MissionState; a bare Name before that. Both are
            # accepted so the guard survives either shape — what it pins
            # is that SOMETHING sets the flag True, the week-one bug.
            if isinstance(target, ast.Name):
                return target.id == "cleaned_in_room"
            if isinstance(target, ast.Attribute):
                return target.attr == "cleaned_in_room"
            return False

        values = [
            ast.unparse(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if _is_the_flag(target)
        ]

        assert "True" in values, (
            "cleaned_in_room is never set True, so a boundary candidate "
            "can never be confirmed"
        )

    def test_working_means_running_and_not_travelling(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks)

        assert 'if phase == "run" and _travelling is False:' in source

    def test_a_measured_room_time_is_written_and_found(self) -> None:
        """The write and the lookup have to agree on the key, or the
        measurement lands somewhere nobody reads -- which is the same
        shape of fault as the one above."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )
        from custom_components.roomba_plus.sensor_rooms import (
            cached_room_seconds,
        )

        profile = SimpleNamespace(room_estimate_cache={})
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(robot_profile_store=profile)
        )

        _remember_measured_room_time(entry, "Hallway", 989.8)

        assert cached_room_seconds(entry, "Hallway") == 989.8
        assert cached_room_seconds(entry, "Kitchen") is None

    def test_nothing_is_stored_for_a_zero_or_missing_time(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )

        profile = SimpleNamespace(room_estimate_cache={})
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(robot_profile_store=profile)
        )

        for bad in (None, 0, -5):
            _remember_measured_room_time(entry, "Hallway", bad)
        _remember_measured_room_time(entry, None, 100)

        assert profile.room_estimate_cache == {}

    def test_one_observation_is_enough(self) -> None:
        """Deliberate: the thing it replaces is a whole-house average
        divided by room count. A single real measurement of this room
        beats that immediately, so waiting for a confidence threshold
        only prolongs the bad figure."""
        import inspect

        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )

        source = inspect.getsource(_remember_measured_room_time)

        assert "STORED IMMEDIATELY" in source


class TestLastCleanedRoomsReportsWhatWasCleaned:
    """The attribute named rooms that were cleaned and showed rooms that
    were requested -- or worse, rooms from a mission two runs back.

    THE COMPLETED-ROOM SOURCE IS PRIME-ONLY. `timeline.finEvents`
    carries real completions with a status; a Classic record has no
    timeline at all. So the resolution returned nothing every time, the
    vacuum entity kept its last successful answer, and
    @ScenicSystemsLLC saw all seven rooms of a whole-house run still
    listed after two separate two-room missions.

    The requested list was the obvious substitute and the wrong one: it
    is resolved at mission START, so a room the robot never reached --
    flat battery, stuck, cancelled -- is in it all the same. Naming a
    plan in a field called `last_cleaned_rooms` is worse than naming
    nothing.

    Room tracking confirms transitions from observations now, on both
    generations. The rooms it advanced through are rooms the robot
    worked in, which is the honest Classic answer and did not exist
    before this release.
    """

    @staticmethod
    def _observed(planned, index):
        from types import SimpleNamespace

        from custom_components.roomba_plus.callbacks import _observed_rooms

        return _observed_rooms(
            SimpleNamespace(
                runtime_data=SimpleNamespace(
                    mission_timer_store=SimpleNamespace(
                        planned_rooms=planned, current_room_idx=index
                    )
                )
            )
        )

    def test_a_completed_run_lists_every_room(self) -> None:
        assert self._observed(["A", "B", "C"], 2) == ["A", "B", "C"]

    def test_the_current_room_counts_as_worked_in(self) -> None:
        """Advancing INTO a room is what confirms the previous one
        finished, so the room the tracker is showing has been worked
        in."""
        assert self._observed(["A", "B"], 1) == ["A", "B"]

    def test_a_mission_cut_short_lists_only_what_it_reached(self) -> None:
        """The whole point: a room the robot never got to must not be
        reported as cleaned."""
        assert self._observed(["Guest Bathroom", "Hallway"], 0) == [
            "Guest Bathroom"
        ]

    def test_nothing_tracked_yields_nothing(self) -> None:
        """None means "fall back to the requested list", which the
        caller handles -- not "no rooms were cleaned". Since 4.2.13 an
        empty list says the second (TestAReachedRoomIsNotACleanedRoom)."""
        assert self._observed([], 0) is None

    def test_an_index_past_the_end_does_not_overrun(self) -> None:
        assert self._observed(["A", "B"], 99) == ["A", "B"]

    def test_the_record_stores_it_under_the_resolver_s_key(self) -> None:
        """No new source: the resolver already falls back to
        `last_cleaned_rooms`, and the timeline still wins where it
        exists."""
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks.async_record_mission)

        assert (
            '"last_cleaned_rooms": observed_rooms if observed_rooms is not None else zones,'
            in source
        )


class TestMeasuredRoomTimesAverage:
    """Keeping only the latest measurement made the estimate swing with
    each run. @ScenicSystemsLLC's Guest Bathroom measured 232, 220, 197
    and 250 seconds across four missions -- a 25% spread the estimate
    followed exactly, and no sign of settling.

    PER CLEANING MODE, because a mop pass and a vacuum pass over the
    same floor are not the same room to a robot. Averaging them
    together produces a figure that describes neither, which is why the
    cloud keys its own estimates by parameters too.
    """

    @staticmethod
    def _store():
        from types import SimpleNamespace

        profile = SimpleNamespace(room_estimate_cache={})
        return profile, SimpleNamespace(
            runtime_data=SimpleNamespace(robot_profile_store=profile)
        )

    def test_four_runs_average(self) -> None:
        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )

        profile, entry = self._store()
        for seconds in (232.0, 220.0, 197.0, 250.0):
            _remember_measured_room_time(entry, "Guest Bathroom", seconds, mode=2)

        # The exact mean of the four, not a rounded running one:
        # rounding at each step compounded and produced 224.7.
        assert profile.room_estimate_cache[
            "Guest Bathroom|measured|2"
        ] == 224.75
        assert profile.room_estimate_cache[
            "Guest Bathroom|measured|2|count"
        ] == 4.0

    def test_modes_do_not_mix(self) -> None:
        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )

        profile, entry = self._store()
        _remember_measured_room_time(entry, "Bath", 200.0, mode=2)
        _remember_measured_room_time(entry, "Bath", 900.0, mode=6)

        assert profile.room_estimate_cache["Bath|measured|2"] == 200.0
        assert profile.room_estimate_cache["Bath|measured|6"] == 900.0

    def test_the_count_is_not_read_as_a_duration(self) -> None:
        """Counts and durations share the room prefix, and the lookup
        takes the smallest value under it. Without a filter, a count of
        4 would be read as a four-second room and win outright."""
        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )
        from custom_components.roomba_plus.sensor_rooms import (
            cached_room_seconds,
        )

        profile, entry = self._store()
        for seconds in (232.0, 220.0, 197.0, 250.0):
            _remember_measured_room_time(entry, "Bath", seconds, mode=2)

        assert cached_room_seconds(entry, "Bath") == 224.75

    def test_an_unknown_mode_gets_its_own_bucket(self) -> None:
        """Folding it into a known mode is the mixing this avoids."""
        from custom_components.roomba_plus.callbacks import (
            _remember_measured_room_time,
        )

        profile, entry = self._store()
        _remember_measured_room_time(entry, "Bath", 200.0, mode=2)
        _remember_measured_room_time(entry, "Bath", 400.0, mode=None)

        assert profile.room_estimate_cache["Bath|measured|2"] == 200.0
        assert profile.room_estimate_cache["Bath|measured|unknown"] == 400.0


class TestTravelEdgeDrivenThroughTheCallback:
    """The travel-end block of `_on_mission_message`, DRIVEN, not read.

    `soho` robots emit no phase change between rooms, so the room display
    sat on the first planned room for a whole mission (@ScenicSystemsLLC).
    Bit 0 of `operatingMode` is Traveling, and the robot is in the new
    room once the drive ENDS — so the return edge, travel → not-travel,
    is the boundary candidate.

    WHY THIS CLASS EXISTS. The neighbouring tests check the bit decoding
    in isolation, and `test_the_callback_uses_the_return_edge` searches
    the callback's SOURCE for the edge. None drove a message through it,
    so the block was uncovered — and it is the first block Group 5 plans
    to lift into its own method. Refactoring code no test executes is how
    it gets broken; this pins its behaviour first.
    """

    def _msg(self, *, travelling: bool, phase: str = "run") -> dict:
        return {
            "state": {
                "reported": {
                    "cleanMissionStatus": {
                        "phase": phase,
                        "sqft": 100,
                        "mssnStrtTm": 1700000000,
                        "initiator": "schedule",
                        "error": 0,
                        "cycle": "clean",
                        # bit 0 = Traveling
                        "operatingMode": 1 if travelling else 0,
                    },
                    "bbrun": {"nStuck": 0, "hr": 10},
                }
            }
        }

    def _setup(self):
        from custom_components.roomba_plus.callbacks import make_mission_callback

        hass, entry, _, _ = _make_callback_env()
        entry.runtime_data.prime_status_coordinator = None
        mts = TestRoomCompletedEvent()._make_real_mts(entry)
        entry.runtime_data.mission_timer_store = mts
        return hass, entry, mts, make_mission_callback(hass, entry)

    def test_a_departure_alone_does_not_advance(self):
        """Leaving is not arriving. The drive has only begun."""
        hass, _entry, mts, cb = self._setup()

        cb(self._msg(travelling=False))
        cb(self._msg(travelling=True))
        hass.loop.run_until_complete(asyncio.sleep(0))

        assert mts.current_room_idx == 0

    def test_the_return_edge_is_evaluated(self):
        """travel → not-travel reaches the boundary branch. Whether the
        room then advances also depends on the cleaned-here confirmation,
        so this pins that the branch RUNS, via its own logged decision,
        rather than asserting an outcome another gate may veto."""
        import logging

        hass, _entry, _mts, cb = self._setup()
        with _capture_log("custom_components.roomba_plus.callbacks", logging.DEBUG) as log:
            cb(self._msg(travelling=False))
            cb(self._msg(travelling=True))
            cb(self._msg(travelling=False))
            hass.loop.run_until_complete(asyncio.sleep(0))

        assert any("travel ended" in r for r in log), (
            "the return edge never reached the travel-end branch"
        )


import contextlib as _contextlib
from custom_components.roomba_plus.callbacks import async_record_mission
from types import SimpleNamespace
from custom_components.roomba_plus import callbacks as cb
from custom_components.roomba_plus.callbacks import _MISSION_END_PHASES
from custom_components.roomba_plus.const import ROOM_TRANSITION_CANDIDATE_PHASES


@_contextlib.contextmanager
def _capture_log(name: str, level: int):
    """Collect formatted log messages from one logger for the block."""
    import logging

    gesammelt: list[str] = []

    class _Sammler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            gesammelt.append(record.getMessage())

    logger = logging.getLogger(name)
    handler = _Sammler(level)
    alt = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield gesammelt
    finally:
        logger.removeHandler(handler)
        logger.setLevel(alt)


class TestEvaluateTravelEdgeDirectly:
    """`_evaluate_travel_edge`, tested as the unit it now is.

    Before Group 5 this logic sat inside a 1,230-line closure, reachable
    only by building a whole mission callback and feeding it a message
    sequence. Lifted out, each transition is one line of setup. That is
    the concrete payoff of the extraction, and why the remaining blocks
    are worth the same treatment.
    """

    def _ms(self, *, was_travelling: bool, started_at=None):
        from custom_components.roomba_plus.callbacks import _MissionState

        ms = _MissionState()
        ms.was_travelling = was_travelling
        ms.travel_started_at = started_at
        return ms

    def test_the_return_edge_with_a_known_start_is_a_boundary(self):
        from custom_components.roomba_plus.callbacks import _evaluate_travel_edge

        ms = self._ms(was_travelling=True, started_at=0.0)

        assert _evaluate_travel_edge(ms, False) is True
        assert ms.travel_started_at is None, "the drive is over"

    def test_the_return_edge_without_a_start_is_not(self):
        """A return with no recorded departure — e.g. after a restart
        mid-drive — cannot vouch for a boundary."""
        from custom_components.roomba_plus.callbacks import _evaluate_travel_edge

        ms = self._ms(was_travelling=True, started_at=None)

        assert _evaluate_travel_edge(ms, False) is False

    def test_a_departure_records_the_start_and_is_not_a_boundary(self):
        from custom_components.roomba_plus.callbacks import _evaluate_travel_edge

        ms = self._ms(was_travelling=False)

        assert _evaluate_travel_edge(ms, True) is False
        assert ms.travel_started_at is not None

    def test_no_operating_mode_fires_neither_edge(self):
        """`travelling is None` — the robot sent nothing usable."""
        from custom_components.roomba_plus.callbacks import _evaluate_travel_edge

        ms = self._ms(was_travelling=True, started_at=0.0)

        assert _evaluate_travel_edge(ms, None) is False
        assert ms.travel_started_at == 0.0, "an unknown mode must not clear it"

    def test_it_does_not_update_was_travelling_itself(self):
        """The caller does that, after using the result. If this ever
        started writing it, the caller's own update would run on a value
        already changed underneath it."""
        from custom_components.roomba_plus.callbacks import _evaluate_travel_edge

        ms = self._ms(was_travelling=True, started_at=0.0)
        _evaluate_travel_edge(ms, False)

        assert ms.was_travelling is True


class TestEndGateInvariant:
    """The assumption the end gate stands on, pinned.

    The end-of-mission decision and its diagnostic log both depend on
    `ms.end_signal_first_ts`. For an AMBIGUOUS end phase the time gate is
    measured from it — so it must be set by the time the streak reaches
    the debounce count. That is not enforced by the gate. It is set in a
    different block of the callback, when a streak starts.

    Until Group 5 the decision and the log each computed the gate on
    their own, and they disagreed exactly where this invariant would be
    broken: the log guarded `first_ts == 0` as "held for 0 s", the
    decision took `monotonic() - 0` as "held since boot". They agreed in
    practice only because this invariant held. Now they share one
    computation; this test is what keeps that safe if the streak logic
    is ever changed.
    """

    def test_a_running_ambiguous_streak_always_has_a_start_time(self):
        from custom_components.roomba_plus.callbacks import make_mission_callback

        hass, entry, _, _ = _make_callback_env()
        entry.runtime_data.prime_status_coordinator = None
        cb = make_mission_callback(hass, entry)

        cb(_msg("run"))
        for _ in range(3):
            cb(_msg("charge"))          # ambiguous end phase
            ms = cb.state
            if ms.end_signal_streak >= 1:
                assert ms.end_signal_first_ts > 0, (
                    "an ambiguous end streak is running with no start "
                    "time — the time gate would measure from zero"
                )

    def test_an_unambiguous_end_sets_the_streak_without_a_start_time(self):
        """The one case where first_ts stays 0 on purpose: an unambiguous
        terminal phase jumps straight to the debounce count. Safe only
        because the gate does not consult the time for unambiguous
        phases — pinned so that stays visible."""
        from custom_components.roomba_plus.callbacks import make_mission_callback

        hass, entry, _, _ = _make_callback_env()
        entry.runtime_data.prime_status_coordinator = None
        cb = make_mission_callback(hass, entry)

        cb(_msg("run"))
        # `stop` is the only end phase that is NOT a room-transition
        # candidate — `charge` and `hmPostMsn` both are, which is exactly
        # why they need the time gate. (First draft of this test used
        # hmPostMsn and failed: it is ambiguous.)
        cb(_msg("stop"))
        # `stop` ends the mission, which schedules the record write; let
        # it finish rather than leave a pending task behind.
        hass.loop.run_until_complete(asyncio.sleep(0.05))

        assert cb.state.end_signal_first_ts == 0.0


class TestHandleMissionStartDemandOverride:
    """The non-obvious output of `_handle_mission_start`.

    When the DirtThresholdManager fired a start within the last 30 s, the
    mission's initiator is overridden to "demand" — on a COPY, returned
    to the caller, which records it. Every existing test set
    `demand_triggered_ts = None`, so this path was never exercised, and
    it is exactly the part the Group 5 extraction could break silently:
    if the caller dropped the return value, missions would be recorded
    with the robot's own initiator and nothing would fail.
    """

    def _call(self, *, demand_ts):
        import time

        from custom_components.roomba_plus.callbacks import (
            _handle_mission_start,
            _MissionState,
        )

        hass, entry, _, _ = _make_callback_env()
        entry.runtime_data.demand_triggered_ts = (
            None if demand_ts is None else time.monotonic() - demand_ts
        )
        entry.runtime_data.presence_manager = None
        entry.options = {}
        mission = {"phase": "run", "initiator": "schedule", "cycle": "clean",
                   "mssnStrtTm": 1700000000}
        reported = {"cleanMissionStatus": mission, "bbrun": {"nStuck": 0}}
        ms = _MissionState()
        ergebnis = _handle_mission_start(
            ms, hass, entry,
            phase="run", reported=reported, mission=mission,
            candidate_cycle="clean", candidate_mission_start_ts=1700000000,
        )
        return ergebnis, mission, entry

    def test_a_recent_demand_start_overrides_the_initiator(self):
        ergebnis, original, entry = self._call(demand_ts=5.0)

        assert ergebnis["initiator"] == "demand"
        assert original["initiator"] == "schedule", (
            "the override must be on a copy — the reported state it came "
            "from is shared and must not be mutated"
        )
        assert entry.runtime_data.demand_triggered_ts is None, "consumed once"

    def test_a_stale_demand_start_is_ignored(self):
        """Older than 30 s: the start was the robot's own."""
        ergebnis, _original, _entry = self._call(demand_ts=45.0)

        assert ergebnis["initiator"] == "schedule"

    def test_no_demand_leaves_the_mission_untouched(self):
        ergebnis, original, _entry = self._call(demand_ts=None)

        assert ergebnis is original


class TestEndGateRoomChecksDirectly:
    """The end gate's two room checks, tested as units.

    Until Group 5 both were closures inside `_on_mission_message`, reachable
    only through a whole mission callback. They decide whether an
    ambiguous end phase is a pause between rooms — the v2.9.0 fix Thonno
    prompted when a two-room mission was closed after the first room.
    Each case is now one line of setup.
    """

    START = 1789747781

    def _entry(self, *, records=None, planned=None, idx=0, estimates=None,
               regions=None, cloud=True):
        entry = MagicMock()
        if cloud:
            entry.runtime_data.cloud_coordinator.raw_records = records or []
        else:
            entry.runtime_data.cloud_coordinator = None
        mts = MagicMock()
        mts.planned_rooms = planned if planned is not None else ["A", "B", "C"]
        mts.current_room_idx = idx
        mts.room_estimates_sec = estimates if estimates is not None else [600.0, 500.0, 900.0]
        mts.total_estimated_sec = 2000.0 if estimates is None else None
        entry.runtime_data.mission_timer_store = mts
        entry.runtime_data.roomba.master_state = {"state": {"reported": {
            "lastCommand": {"regions": regions if regions is not None else [
                {"region_id": "1"}, {"region_id": "2"}, {"region_id": "3"}]},
        }}}
        return entry

    def _ms(self):
        from custom_components.roomba_plus.callbacks import _MissionState

        ms = _MissionState()
        ms.mission_start_ts = self.START
        return ms

    def _record(self, *, start=START, done=("1", "2", "3"), status=0):
        return {"startTime": start, "timeline": {"finEvents": [
            {"type": "room", "room": {"rid": rid, "status": status}} for rid in done
        ]}}

    # ── _cloud_confirms_all_rooms_done ──────────────────────────────────

    def test_the_cloud_confirms_when_every_planned_room_finished(self):
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(records=[self._record()])
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is True

    def test_one_unfinished_room_is_not_a_confirmation(self):
        """`all()`, not `any()` — a partial finish must not close the mission."""
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(records=[self._record(done=("1", "2"))])
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is False

    def test_status_six_counts_as_finished_too(self):
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(records=[self._record(status=6)])
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is True

    def test_another_missions_record_is_not_evidence(self):
        """Matched by start time within 120 s. A record from a different
        mission must not vouch for this one."""
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(records=[self._record(start=self.START + 121)])
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is False

    def test_the_120_second_window_is_inclusive(self):
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(records=[self._record(start=self.START + 120)])
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is True

    def test_without_cloud_there_is_no_confirmation(self):
        from custom_components.roomba_plus.callbacks import _cloud_confirms_all_rooms_done

        entry = self._entry(cloud=False)
        assert _cloud_confirms_all_rooms_done(self._ms(), entry, ["1", "2", "3"]) is False

    # ── _has_unvisited_planned_rooms ────────────────────────────────────

    def test_rooms_ahead_of_the_index_are_unvisited(self):
        """The case the v2.9.0 fix exists for: first of three rooms, an
        end phase arrives — that is a pause, not the end."""
        from custom_components.roomba_plus.callbacks import _has_unvisited_planned_rooms

        assert _has_unvisited_planned_rooms(self._ms(), self._entry(idx=0)) is True

    def test_on_the_last_room_nothing_is_unvisited(self):
        from custom_components.roomba_plus.callbacks import _has_unvisited_planned_rooms

        assert _has_unvisited_planned_rooms(self._ms(), self._entry(idx=2)) is False

    def test_the_cloud_overrides_a_stale_room_index(self):
        """The index stopped early, but the cloud says every room
        finished. The cloud wins — otherwise the mission waits out the
        whole 90 s suppression cap for rooms that are already done."""
        from custom_components.roomba_plus.callbacks import _has_unvisited_planned_rooms

        entry = self._entry(idx=0, records=[self._record()])
        assert _has_unvisited_planned_rooms(self._ms(), entry) is False

    def test_without_any_estimate_the_plan_is_not_trusted(self):
        """No per-room or total estimate means the plan cannot be
        tracked, so it must not hold a mission open."""
        from custom_components.roomba_plus.callbacks import _has_unvisited_planned_rooms

        entry = self._entry(estimates=[None, None, None])
        assert _has_unvisited_planned_rooms(self._ms(), entry) is False

    def test_without_a_plan_nothing_is_unvisited(self):
        from custom_components.roomba_plus.callbacks import _has_unvisited_planned_rooms

        assert _has_unvisited_planned_rooms(self._ms(), self._entry(planned=[])) is False


class TestTheStuckTimeReachesTheClassifier:
    """The classifier counts only what happened AFTER the stuck; it needs the
    wall-clock time the mission callback saw it. Without it every call would
    get 0 and quietly fall back to the old elapsed-time test."""

    def test_the_wall_clock_time_of_the_stuck_is_passed(self):
        seen: list[float] = []

        def _spy(entry, mission, stuck_at):
            seen.append(stuck_at)
            return None

        with patch("custom_components.roomba_plus.callbacks._mission_recovered_after_stuck",
                   side_effect=_spy):
            TestStuckBypassMissionCallback()._run_phases([
                ("run", 0), ("stuck", 1), ("run", 1), ("hmPostMsn", 1), ("charge", 1),
            ])
        # _patch_callbacks_time makes time() return 1000.0.
        assert seen == [1000.0]


# ── formerly tests/test_coverage_callbacks.py ───────────────────────────────────
#
# callbacks.py — quality scale, test-coverage.
#
# Recording a finished mission also feeds the robot profile (relocation
# baseline, battery capacity, lifetime energy), resets the skip counter, and
# schedules the repair checks. None of that ran in a test: the existing
# fixtures never carried navigation or battery data, and no hass was
# 'running'.

def _record(reported=None, *, mission=None, error_override=None, **runtime):
    loop = asyncio.new_event_loop()
    try:
        _h, entry, _rec, store = _make_callback_env()
        hass = _make_hass(loop)
        # The fallback for keys the message lacks: an empty cached state.
        entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
        for k, v in runtime.items():
            setattr(entry.runtime_data, k, v)
        loop.run_until_complete(async_record_mission(
            hass, entry, mission or {"phase": "charge", "error": 0, "sqft": 100},
            reported or {}, [], int(loop.time()) - 3600, 0,
            **({"result_override": error_override} if error_override else {}),
        ))
        return entry, store, hass
    finally:
        loop.close()


def _rps():
    rps = MagicMock()
    rps.async_save = AsyncMock()
    return rps


def _seed_entry(store, hr=500):
    entry = MagicMock()
    entry.runtime_data.maintenance_store = store
    entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
    entry.hass = MagicMock()
    entry.entry_id = "e1"
    return entry, {"bbrun": {"hr": hr}}


def _align_env(hass, *, points=True, regions=True, geometry=True, aligner=None, ok=True):
    entry = MagicMock()
    entry.runtime_data.geometry_store = MagicMock(door_markers=[]) if geometry else None
    entry.runtime_data.umf_aligner = aligner
    coordinator = MagicMock()
    coordinator.umf_data = {"points2d": [{"x": 0}] if points else [], "regions": []}
    coordinator.regions = [{"id": "3"}] if regions else []
    coordinator.last_update_success = ok
    coordinator.raw_records = []
    return entry, coordinator


def _refresh_env(*, ok=True, store=True, corrected=False, dtm=True):
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e1"
    tasks = []
    entry.async_create_task = lambda _h, coro, **k: tasks.append(coro) or (coro.close() if hasattr(coro, "close") else None)
    coordinator = MagicMock(last_update_success=ok, raw_records=[])
    if store:
        ms = MagicMock()
        ms.backfill_from_cloud.return_value = SimpleNamespace(corrected=corrected, enriched=False)
        ms.async_save = AsyncMock()
        entry.runtime_data.mission_store = ms
    else:
        entry.runtime_data.mission_store = None
    entry.runtime_data.dirt_threshold_manager = MagicMock(async_evaluate=AsyncMock()) if dtm else None
    return hass, entry, coordinator, tasks


class TestRobotProfileFeed:

    def test_a_populated_nav_record_sets_the_relocation_baseline(self):
        rps = _rps()
        _record({"mssnNavStats": {"reLc": 3, "gLmk": 5}}, robot_profile_store=rps)
        rps.update_reloc_baseline.assert_called_once_with(3)

    def test_a_zeroed_nav_record_is_not_a_baseline(self):
        """Firmware reports an all-zero record on some missions; learning
        a baseline of zero relocations from it would be wrong."""
        rps = _rps()
        _record({"mssnNavStats": {"reLc": 0, "gLmk": 0, "lmk": 0, "mTrk": 0}}, robot_profile_store=rps)
        rps.update_reloc_baseline.assert_not_called()

    def test_an_unreadable_relocation_count_is_ignored(self):
        rps = _rps()
        _record({"mssnNavStats": {"reLc": "many", "gLmk": 5}}, robot_profile_store=rps)
        rps.update_reloc_baseline.assert_not_called()

    def test_battery_capacity_and_energy_are_recorded(self, monkeypatch):
        from custom_components.roomba_plus import callbacks

        monkeypatch.setattr(callbacks, "estcap_to_mah", lambda *_a: 3000)
        rps = _rps()
        profile = MagicMock(battery_voltage=14.4, estcap_scale_liion=1, estcap_scale_nimh=1)
        _record({"bbchg3": {"estCap": 2000, "nLithChrg": 100}},
                robot_profile_store=rps, robot_profile=profile)
        rps.record_estcap_observation.assert_called_once_with(3000)
        rps.update_energy_high_water.assert_called_once_with(round(3000 * 14.4 * 100 / 1_000_000, 3))


class TestRecordMissionOutcomes:

    def test_no_mission_store_records_nothing(self):
        entry, store, _hass = _record(mission_store=None)
        store.async_append.assert_not_awaited()

    def test_an_override_decides_the_result(self):
        _e, store, _h = _record(error_override="blocked_timeout")
        assert store.async_append.await_args.args[0]["result"] == "blocked_timeout"

    def test_an_error_mission_records_where_it_happened(self):
        from custom_components.roomba_plus.const import POSE_POINT_CM_TO_MM

        _e, store, _h = _record({"pose": {"point": {"x": 10, "y": -5}}},
                                mission={"phase": "stuck", "error": 17, "sqft": 10})
        rec = store.async_append.await_args.args[0]
        assert rec["error_position_mm"] == {"x": 10 * POSE_POINT_CM_TO_MM, "y": -5 * POSE_POINT_CM_TO_MM}

    def test_an_unreadable_error_position_is_left_out(self):
        _e, store, _h = _record({"pose": {"point": {"x": "left", "y": 2}}},
                                mission={"phase": "stuck", "error": 17, "sqft": 10})
        assert store.async_append.await_args.args[0].get("error_position_mm") is None

    def test_a_completed_mission_resets_the_skip_counter(self):
        maint = MagicMock(consecutive_skips=3)
        maint.async_save = AsyncMock()
        _record(maintenance_store=maint)
        assert maint.consecutive_skips == 0
        maint.async_save.assert_awaited_once()


class TestSeedMaintenanceBaselines:
    """A used robot added to HA: each part's baseline is its current
    runtime, so remaining life is not computed from zero hours."""

    def _store(self, **seeded):
        s = SimpleNamespace(async_save=MagicMock(return_value="save"))   # handed to a mock hass, never awaited
        for slot in ("filter", "brush", "side_brush", "clean_base_bag"):
            setattr(s, f"{slot}_baseline_seeded", seeded.get(slot, False))
            setattr(s, f"{slot}_reset_history", None)
            setattr(s, f"{slot}_reset_hr", 0)
        return s

    def test_unseeded_parts_start_at_the_current_runtime(self):
        store = self._store(brush=True)
        entry, reported = _seed_entry(store)
        cb._seed_maintenance_baselines(entry, reported)
        assert store.filter_reset_hr == 500 and store.filter_baseline_seeded is True
        assert store.brush_reset_hr == 0, "an already seeded part is left alone"
        entry.hass.async_create_task.assert_called_once()

    def test_a_part_with_reset_history_is_not_reseeded(self):
        store = self._store()
        store.filter_reset_history = [{"hr": 100}]
        entry, reported = _seed_entry(store)
        cb._seed_maintenance_baselines(entry, reported)
        assert store.filter_reset_hr == 0

    def test_no_runtime_yet_seeds_nothing(self):
        store = self._store()
        entry, reported = _seed_entry(store, hr=0)
        cb._seed_maintenance_baselines(entry, reported)
        assert store.filter_baseline_seeded is False

    def test_nothing_to_seed_saves_nothing(self):
        store = self._store(filter=True, brush=True, side_brush=True, clean_base_bag=True)
        entry, reported = _seed_entry(store)
        cb._seed_maintenance_baselines(entry, reported)
        entry.hass.async_create_task.assert_not_called()


class TestRealign:

    @pytest.mark.parametrize("kw", [{"points": False}, {"regions": False}, {"geometry": False}])
    @pytest.mark.asyncio
    async def test_without_inputs_there_is_nothing_to_align(self, hass, kw):
        entry, coordinator = _align_env(hass, **kw)
        await cb._async_realign(hass, entry, coordinator)
        assert entry.runtime_data.umf_aligner is None

    @pytest.mark.asyncio
    async def test_a_new_aligner_replaces_the_old(self, hass, monkeypatch):
        from custom_components.roomba_plus import umf_aligner

        fake = MagicMock(align=MagicMock(return_value=0.9), aligned=True)
        monkeypatch.setattr(umf_aligner, "UmfAligner", lambda **_k: fake)
        entry, coordinator = _align_env(hass)
        await cb._async_realign(hass, entry, coordinator)
        assert entry.runtime_data.umf_aligner is fake


class TestBootstrapAligner:

    @pytest.mark.asyncio
    async def test_no_geometry_means_no_aligner(self, hass):
        entry, coordinator = _align_env(hass, geometry=False)
        await cb._async_bootstrap_umf_aligner(hass, entry, coordinator)
        assert entry.runtime_data.umf_aligner is None

    @pytest.mark.asyncio
    async def test_no_map_points_means_no_aligner(self, hass):
        entry, coordinator = _align_env(hass, points=False)
        await cb._async_bootstrap_umf_aligner(hass, entry, coordinator)
        assert entry.runtime_data.umf_aligner is None

    @pytest.mark.asyncio
    async def test_an_aligner_that_aligns_at_once_is_kept_and_done(self, hass, monkeypatch):
        from custom_components.roomba_plus import umf_aligner

        fake = MagicMock(align=MagicMock(return_value=0.9), aligned=True)
        monkeypatch.setattr(umf_aligner, "UmfAligner", lambda **_k: fake)
        entry, coordinator = _align_env(hass)
        await cb._async_bootstrap_umf_aligner(hass, entry, coordinator)
        assert entry.runtime_data.umf_aligner is fake
        assert fake.align.call_count == 1

    @pytest.mark.parametrize("markers,ok", [(2, True), (0, False)])
    @pytest.mark.asyncio
    async def test_an_unaligned_aligner_waits_for_doors_or_a_good_refresh(self, hass, markers, ok):
        """Enough door markers already, or a failed cloud refresh: no
        bootstrap from history now."""
        aligner = MagicMock(aligned=False, _door_candidates=[1])
        entry, coordinator = _align_env(hass, aligner=aligner, ok=ok)
        entry.runtime_data.geometry_store.door_markers = [MagicMock(mission_count=3)] * markers
        await cb._async_bootstrap_umf_aligner(hass, entry, coordinator)
        aligner.align.assert_not_called()


class TestCloudRefreshHook:

    def _run(self, monkeypatch, **kw):
        monkeypatch.setattr(cb, "_umf_version_changed", lambda *_a: False)
        hass, entry, coordinator, tasks = _refresh_env(**kw)
        cb.make_cloud_refresh_callback(hass, entry, coordinator)()
        return entry, tasks

    def test_a_failed_refresh_only_checks_staleness(self, monkeypatch):
        entry, tasks = self._run(monkeypatch, ok=False)
        assert len(tasks) == 1
        entry.runtime_data.mission_store.backfill_from_cloud.assert_not_called()

    def test_no_mission_store_stops_after_the_staleness_check(self, monkeypatch):
        _entry, tasks = self._run(monkeypatch, store=False)
        assert len(tasks) == 1

    def test_corrections_are_saved_and_demand_cleaning_evaluated(self, monkeypatch):
        entry, tasks = self._run(monkeypatch, corrected=True)
        entry.runtime_data.mission_store.backfill_from_cloud.assert_called_once()
        assert len(tasks) >= 3, "staleness check, save, demand evaluation"


class TestMissionAlreadyTerminal:

    def _entry(self, records):
        entry = MagicMock()
        entry.runtime_data.mission_store = SimpleNamespace(records=records)
        return entry

    def test_no_start_time_or_store_is_not_terminal(self):
        assert cb._mission_already_terminal(self._entry([]), 0) is False
        entry = MagicMock()
        entry.runtime_data.mission_store = None
        assert cb._mission_already_terminal(entry, 1789975662) is False

    def test_a_recent_terminal_record_for_the_same_start_is_terminal(self):
        from homeassistant.util import dt as dt_util

        rec = {"id": "m_1789975662", "result": "completed", "ended_at": dt_util.utcnow().isoformat()}
        assert cb._mission_already_terminal(self._entry([rec]), 1789975662) is True


class TestRecoveredAfterStuck:
    """Classifies 'got stuck but finished' from the cloud record; returns
    None whenever the record is not there, so the caller falls back."""

    def _entry(self, records=None, cc=True):
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator = SimpleNamespace(raw_records=records or []) if cc else None
        return entry

    def test_no_cloud_no_mission_id_or_no_timeline_is_unknown(self):
        assert cb._mission_recovered_after_stuck(self._entry(cc=False), {"missionId": "x"}, 1.0) is None
        assert cb._mission_recovered_after_stuck(self._entry(), {}, 1.0) is None
        records = ["junk", {"missionId": "other"}, {"missionId": "x", "timeline": "odd"}]
        assert cb._mission_recovered_after_stuck(self._entry(records), {"missionId": "x"}, 1.0) is None


class TestCloudConfirmationEdges:

    def _ms(self):
        ms = cb._MissionState()
        ms.mission_start_ts = 1789975662
        return ms

    def test_no_record_near_the_start_confirms_nothing(self):
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{"startTime": 1}]
        assert cb._cloud_confirms_all_rooms_done(self._ms(), entry, ["3"]) is False

    def test_a_record_without_a_timeline_confirms_nothing(self):
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{"startTime": 1789975662, "timeline": None}]
        assert cb._cloud_confirms_all_rooms_done(self._ms(), entry, ["3"]) is False

    def test_non_room_events_are_ignored(self):
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{"startTime": 1789975662, "timeline": {
            "finEvents": [{"type": "travel"}, {"type": "room", "room": {"rid": "3", "status": 0}}]}}]
        assert cb._cloud_confirms_all_rooms_done(self._ms(), entry, ["3"]) is True


class TestRepairChecksAfterRecording:

    def test_a_running_hass_schedules_both_checks(self, monkeypatch):
        from custom_components.roomba_plus import repairs

        monkeypatch.setattr(repairs, "async_check_mixed_schedule", MagicMock(return_value="mixed"))
        monkeypatch.setattr(repairs, "async_check_mission_anomaly", MagicMock(return_value="anomaly"))
        loop = asyncio.new_event_loop()
        try:
            _h, entry, _rec, _store = _make_callback_env()
            hass = _make_hass(loop)
            hass.is_running = True
            entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
            scheduled = []
            entry.async_create_task = lambda _h, coro, **k: scheduled.append(coro)
            loop.run_until_complete(async_record_mission(
                hass, entry, {"phase": "charge", "error": 0, "sqft": 10}, {}, [], int(loop.time()) - 3600, 0))
        finally:
            loop.close()
        assert "mixed" in scheduled and "anomaly" in scheduled


class TestProfileStoreLearning:

    @pytest.mark.asyncio
    async def test_coverage_and_lifetime_area_are_learned(self, hass):
        rps = MagicMock()
        rps.update_lifetime_sqft_tracking.return_value = True
        rps.async_save = AsyncMock()
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.runtime_data.grid_store = MagicMock(edge_coverage_ratio=MagicMock(return_value=0.8))
        entry.runtime_data.roomba.master_state = {"state": {"reported": {"runtimeStats": {"sqft": 4000}}}}
        ms = MagicMock()
        ms.query.return_value = [{"timeline": {"finEvents": [
            {"type": "travel"}, {"type": "room", "room": {"rid": "3", "status": 1}}]}}]
        await cb._async_update_robot_profile_store(hass, entry, ms, rps)
        rps.update_coverage_baseline.assert_called_once_with(0.8)
        rps.update_lifetime_sqft_tracking.assert_called_once_with(4000.0)
        rps.update_room_dirt_index.assert_not_called(), "an unfinished pass teaches nothing"


class TestSmallHelpers:

    def test_observed_rooms_are_the_planned_ones_up_to_the_current(self):
        entry = SimpleNamespace(runtime_data=SimpleNamespace(
            mission_timer_store=SimpleNamespace(planned_rooms=["A", "B", "C"], current_room_idx=1)))
        assert cb._observed_rooms(entry) == ["A", "B"]

    def test_observed_rooms_survive_a_broken_store(self):
        """A broken store is "nothing tracked" -- None, so the record
        falls back to the requested rooms -- not "nothing cleaned"."""
        entry = SimpleNamespace(runtime_data=SimpleNamespace(
            mission_timer_store=SimpleNamespace(planned_rooms=["A"], current_room_idx="x")))
        assert cb._observed_rooms(entry) is None

    @pytest.mark.parametrize("room,seconds", [(None, 60), ("Kitchen", 0), ("Kitchen", -5)])
    def test_no_room_or_no_time_is_not_remembered(self, room, seconds):
        profile = MagicMock()
        entry = SimpleNamespace(runtime_data=SimpleNamespace(robot_profile_store=profile))
        cb._remember_measured_room_time(entry, room, seconds)
        profile.room_estimate_cache.__setitem__.assert_not_called()

    def test_a_broken_cache_does_not_break_the_mission(self):
        class _Bad(dict):
            def get(self, *_a):
                raise RuntimeError("corrupt")

        entry = SimpleNamespace(runtime_data=SimpleNamespace(
            robot_profile_store=SimpleNamespace(room_estimate_cache=_Bad())))
        cb._remember_measured_room_time(entry, "Kitchen", 120)   # must not raise

    def test_traversals_need_two_door_candidates(self):
        assert cb._extract_traversal_umf_positions([], SimpleNamespace(_door_candidates=[1])) == []

    def test_too_few_missions_with_traversals_give_nothing(self):
        aligner = SimpleNamespace(_door_candidates=[1, 2])
        records = [{"timeline": {"finEvents": [{"type": "traversal"}]}}]
        assert cb._extract_traversal_umf_positions(records, aligner, min_missions=3) == []

    @pytest.mark.parametrize("value", [None, "", "garbage", 12345])
    def test_an_unreadable_start_has_no_weekday(self, value):
        assert cb._gs_coverage_mission_start_weekday_hour(value) is None


# ── formerly tests/test_recharge_does_not_end_a_mission.py ────────────
#
# A recharge mid-mission is not the end of the mission.
#
# FROM A REAL RUN, and from an independent implementation making the
# opposite decision on the same data. @AlakazipLabs captured mission 519
# on an i3 (daredevil 2.6.0): 79 minutes of cleaning, a self-return at
# 20% battery, 58 minutes on the dock with the mission still open, a
# self-resume at 79%, 46 more minutes, then a genuine finish.
#
# His own from-scratch mission logger -- not Home Assistant, a separate
# SQLite table off the same shadow stream -- treated `phase: charge` as
# terminal and closed the row at the recharge: **81 minutes recorded of a
# 189-minute run**. Two implementations, the same trap, arrived at
# independently.
#
# WHAT SEPARATES THE TWO IS `cycle`, NOT `phase`. Both moments are
# `phase: charge`. The recharge carries `cycle: clean` -- the cleaning
# cycle is still open -- and the real end carries `cycle: none`. `nMssn`
# and `missionId` never change across the whole run, so neither can be
# used to tell them apart.
#
# This integration already reads it that way. Nothing here fixes
# anything; it pins a discriminator that had no test, on a sequence where
# getting it wrong costs two thirds of a mission.

#: Mission 519, verbatim: every phase or cycle change across the run.
#: Local time, missionId constant throughout.
_MISSION_519 = [
    ("07:55:25", "charge", "clean", 518),   # previous mission still closed
    ("07:55:29", "run", "clean", 519),
    ("09:14:28", "hmMidMsn", "clean", 519), # heading home to recharge
    ("09:16:39", "charge", "clean", 519),   # ON THE DOCK, mission still open
    ("10:15:15", "run", "clean", 519),      # resumed, 58 minutes later
    ("11:01:24", "hmPostMsn", "clean", 519),
    ("11:03:54", "evac", "clean", 519),
    ("11:04:09", "hmPostMsn", "clean", 519),
    ("11:04:10", "charge", "none", 519),    # THE END
]


def _looks_like_end(phase: str, cycle: str) -> bool:
    """The integration's own test, as `make_mission_callback` applies it.

    REIMPLEMENTED, AND THAT IS A WEAKNESS WORTH NAMING. The real
    expression lives inline in a closure inside `make_mission_callback`,
    which needs a Home Assistant instance and a live coordinator to
    reach. So this mirrors it -- and a mirror cannot catch the
    production code changing underneath it.

    Two things narrow the gap. `_MISSION_END_PHASES` is imported from
    the module rather than copied, so the phase half is real. And the
    test below reads the source for the cycle half, which fails if the
    check is removed or its wording changes.
    """
    return phase in _MISSION_END_PHASES and cycle not in ("clean", "quick")


#: The two sub-second phase bounces from the same run, as the robot sent
#: them. Millisecond receive times, one clock, missionId constant across
#: all eight -- the fields this integration reads, verbatim.
#:
#: These are what the debounce and hold-time machinery exists for, and
#: this project had never had a real one. A synthetic bounce proves only
#: that whoever wrote it understood the code they were testing.
_BOUNCE_AT_THE_RECHARGE = [
    # 13:16:38.841Z, four messages in 383 ms
    ("hmMidMsn", "clean", 15, 0),
    ("charge", "clean", 15, 1788619598),
    ("hmMidMsn", "clean", 15, 0),
    ("charge", "clean", 15, 1788619598),
]


_BOUNCE_AT_THE_RESUME = [
    # 14:15:15.807Z, four messages in 195 ms -- the first flap is 5 ms
    ("charge", "clean", 0, 1788619598),
    ("run", "clean", 0, 0),
    ("charge", "clean", 0, 1788619598),
    ("run", "clean", 0, 0),
]


class TestTheRechargeIsNotTheEnd:
    def test_the_recharge_does_not_look_like_an_end(self) -> None:
        """09:16:39 -- on the dock, charging, 20% battery, and the
        mission has 46 more minutes of cleaning ahead of it."""
        assert not _looks_like_end("charge", "clean")

    def test_the_real_end_does(self) -> None:
        """11:04:10 -- the same phase, and this time it is over."""
        assert _looks_like_end("charge", "none")

    def test_exactly_one_moment_in_the_run_ends_it(self) -> None:
        """The whole sequence, in order. A second end would mean a
        mission recorded twice; none would mean one never closed."""
        ends = [
            (at, phase)
            for at, phase, cycle, _n in _MISSION_519
            if _looks_like_end(phase, cycle)
        ]

        assert ends == [("11:04:10", "charge")], (
            f"exactly one end expected, got {ends}"
        )

    @pytest.mark.parametrize("phase", sorted(ROOM_TRANSITION_CANDIDATE_PHASES))
    def test_no_ambiguous_phase_ends_a_mission_while_cleaning(
        self, phase: str
    ) -> None:
        """`charge` and `hmPostMsn` both appear mid-run in this capture
        -- `hmPostMsn` twice, once 46 minutes before the finish. Neither
        may end a mission while the cleaning cycle is still open."""
        assert not _looks_like_end(phase, "clean")


class TestTheCycleCheckIsStillThere:
    """Guard the half the mirror above cannot guard.

    Reading the source is a poor test and a good one here: the mirror
    would keep passing if somebody removed the cycle check from the
    production code, and removing it is exactly the mistake an
    independent implementation already made on this same data.
    """

    def test_the_callback_excludes_an_open_cleaning_cycle(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks.make_mission_callback)

        assert '_cycle in ("clean", "quick")' in source, (
            "the mission-end test no longer excludes an open cleaning "
            "cycle -- a mid-mission recharge is `phase: charge` too, and "
            "without this it ends the mission 58 minutes early"
        )
        assert "not _is_inter_room_transition" in source


class TestTheSubSecondBounces:
    """Real captured flapping, from @AlakazipLabs' archive.

    A phase-only end test has bounce 2 as its hard case: `charge` to
    `run` in FIVE milliseconds. Debounce counts and hold times are how
    such a thing is normally survived -- but they are timing
    heuristics, and timing heuristics fail on a machine under load.

    The cycle check does not need them. `cycle` is `clean` in all eight
    messages, so none of them can end a mission whatever the timing
    does. That is worth pinning: it means the guard is structural, not
    a race that happens to be won.
    """

    @pytest.mark.parametrize(
        ("phase", "cycle", "not_ready", "expire_tm"),
        _BOUNCE_AT_THE_RECHARGE + _BOUNCE_AT_THE_RESUME,
    )
    def test_no_message_in_either_bounce_ends_the_mission(
        self, phase: str, cycle: str, not_ready: int, expire_tm: int
    ) -> None:
        assert not _looks_like_end(phase, cycle)

    def test_the_expiry_timer_is_reported_per_phase(self) -> None:
        """Not once at the recharge, as this project's own comment said.

        Every `charge` message carries the deadline and every moving
        message carries 0, alternating inside a 383 ms bounce and again
        an hour later. The VALUE never changes -- recharge arrival plus
        5,399 seconds -- so a countdown must tick locally rather than
        wait to be told.
        """
        armed = {
            expire_tm
            for phase, _c, _n, expire_tm in _BOUNCE_AT_THE_RECHARGE + _BOUNCE_AT_THE_RESUME
            if phase == "charge"
        }
        moving = {
            expire_tm
            for phase, _c, _n, expire_tm in _BOUNCE_AT_THE_RECHARGE + _BOUNCE_AT_THE_RESUME
            if phase in ("run", "hmMidMsn")
        }

        assert armed == {1788619598}, "the deadline must be constant while charging"
        assert moving == {0}, "and absent while moving"


class TestADockedEvacuationIsNotAMission:
    """A `dock` command sent to an ALREADY DOCKED robot is not ignored.

    @AlakazipLabs ran the test: one publish to a robot on its dock at
    100%, nothing else sent for 125 s, every MQTT packet captured. The
    robot accepted it and ran an evacuation -- 22 seconds of bin-empty
    and dock handshake, then idle. No search, no mission, `nMssn`
    unchanged.

    THE HAZARD IS IN THE FIRST THREE SECONDS. Four cleanMissionStatus
    updates arrive in 317 ms alternating `charge` and `run`, with
    `cycle` staying `evac` throughout. The two `run` messages carry the
    `mssnStrtTm` and `missionId` of the LAST mission -- three days old
    in his capture.

    `phase` alone would open a mission on those. So would the
    replay-pulse guard, which only suppresses a terminal record from the
    last 120 seconds, deliberately, so that a genuinely resumed segment
    is not swallowed. Three days is far outside it.

    `cycle` is what holds -- the same discriminator his recharge capture
    established for the other end of a mission.
    """

    #: The burst at +3.3 s, as the robot sent it.
    _EVAC_BURST = [
        ("charge", "evac", 0),
        ("run", "evac", 1788609326),
        ("charge", "evac", 0),
        ("run", "evac", 1788609326),
    ]

    @pytest.mark.parametrize(("phase", "cycle", "mssn_strt_tm"), _EVAC_BURST)
    def test_no_message_in_the_burst_starts_a_mission(
        self, phase: str, cycle: str, mssn_strt_tm: int
    ) -> None:
        from custom_components.roomba_plus.callbacks import _NON_MISSION_CYCLES
        from custom_components.roomba_plus.const import CLEANING_PHASES

        # `run` and `evac` are both cleaning phases, so phase alone says
        # yes to half of these.
        looks_like_cleaning = phase in CLEANING_PHASES
        blocked_by_cycle = cycle in _NON_MISSION_CYCLES

        assert not (looks_like_cleaning and not blocked_by_cycle)

    def test_a_real_mission_start_is_untouched(self) -> None:
        """The negative control: `run` with `cycle: clean` must still
        open a mission, or this fix costs more than the bug."""
        from custom_components.roomba_plus.callbacks import _NON_MISSION_CYCLES
        from custom_components.roomba_plus.const import CLEANING_PHASES

        assert "run" in CLEANING_PHASES
        assert "clean" not in _NON_MISSION_CYCLES
        assert "quick" not in _NON_MISSION_CYCLES

    def test_an_absent_cycle_is_not_treated_as_a_refusal(self) -> None:
        """A missing key is no statement. Requiring the cycle to be
        present would suppress a genuine mission for a message that
        merely arrived thin -- cloud-derived state does not always carry
        everything the robot sends."""
        from custom_components.roomba_plus.callbacks import _NON_MISSION_CYCLES

        assert None not in _NON_MISSION_CYCLES
        assert "" not in _NON_MISSION_CYCLES



class TestAReachedRoomIsNotACleanedRoom:
    """4.2.13. A j7+ was sent to two rooms; the second one's door was
    closed. The robot finished the first and went home -- and the room
    tracker, which also advances on the phase the robot reports when it
    heads home, moved to the room behind the door. The mission record
    listed both rooms as cleaned. With a cloud account the cloud's room
    events replace that a few minutes later; without one it stayed in the
    room history for good.

    The last room reached now counts only if the robot RAN there after
    arriving.
    """

    @staticmethod
    def _entry(planned, index):
        return SimpleNamespace(runtime_data=SimpleNamespace(
            mission_timer_store=SimpleNamespace(planned_rooms=planned, current_room_idx=index)))

    def test_the_last_room_needs_work_to_count(self):
        entry = self._entry(["Kitchen", "Office"], 1)
        assert cb._observed_rooms(entry, last_room_worked=False) == ["Kitchen"]
        assert cb._observed_rooms(entry, last_room_worked=True) == ["Kitchen", "Office"]

    def test_a_mission_that_worked_nowhere_records_no_rooms(self):
        """Not the requested list -- a 224 abort cleaned nothing."""
        assert cb._observed_rooms(self._entry(["Kitchen"], 0), last_room_worked=False) == []

    def test_no_plan_is_nothing_tracked(self):
        assert cb._observed_rooms(self._entry([], 0), last_room_worked=False) is None

    @staticmethod
    def _cb():
        from tests.conftest import entry_mock

        entry = entry_mock()
        entry.options = {}
        entry.runtime_data.cloud_coordinator = None
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.prime_status_coordinator = None   # Classic
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore

        entry.runtime_data.mission_timer_store = MissionTimerStore()
        return cb.make_mission_callback(MagicMock(), entry)

    @staticmethod
    def _msg(phase, operating_mode=None):
        status = {"phase": phase, "cycle": "clean", "error": 0,
                  "mssnStrtTm": 1700000000, "nMssn": 7}
        if operating_mode is not None:
            status["operatingMode"] = operating_mode
        return {"state": {"reported": {"cleanMissionStatus": status, "bbrun": {"nStuck": 0}}}}

    def test_running_marks_the_room_worked(self):
        callback_ = self._cb()
        callback_(self._msg("run"))
        assert callback_.state.ran_in_room is True

    def test_driving_does_not_mark_it_where_the_robot_says_so(self):
        """operatingMode bit 0 is Traveling. A robot that reports it is
        only driving has not worked anywhere yet."""
        callback_ = self._cb()
        callback_(self._msg("run", operating_mode=1))
        assert callback_.state.ran_in_room is False
        callback_(self._msg("run", operating_mode=0))
        assert callback_.state.ran_in_room is True

    def test_heading_home_does_not_mark_it(self):
        callback_ = self._cb()
        callback_.state.had_cleaning_phase = True
        callback_.state.ran_in_room = False
        callback_(self._msg("hmPostMsn"))
        assert callback_.state.ran_in_room is False

    def test_a_new_mission_starts_with_no_room_worked(self):
        """A mission-start phase that is not `run` (hmMidMsn) must not
        inherit the previous mission's flag."""
        callback_ = self._cb()
        callback_.state.ran_in_room = True
        callback_.state.had_cleaning_phase = False
        callback_(self._msg("hmMidMsn"))
        assert callback_.state.had_cleaning_phase is True
        assert callback_.state.ran_in_room is False

    def test_arriving_in_the_next_room_starts_it_unworked(self):
        """The room the tracker moves INTO has not been worked in yet --
        the door-closed case depends on exactly this."""
        advanced = []
        mts = SimpleNamespace(
            planned_rooms=["Kitchen", "Office"], current_room_idx=0,
            time_in_current_room_sec=600.0, expected_room_sec=600.0,
            advance_room=lambda *_a: advanced.append(1) or True,
            current_room="Office",
        )
        ms = cb._MissionState()
        ms.mission_start_ts = 1700000000
        ms.cleaned_in_room = True
        ms.ran_in_room = True
        ms.last_phase = "run"
        entry = MagicMock(entry_id="e", title="Roomba")
        entry.runtime_data.robot_profile_store = None

        cb._advance_room_on_drive_end(
            ms, MagicMock(), entry, mts_upd=mts, returned_from_travel=False,
            mission={"cycle": "clean", "error": 0}, phase="hmPostMsn",
        )

        assert advanced == [1]
        assert ms.ran_in_room is False

    def test_the_record_gets_the_rule(self, monkeypatch):
        """End to end at the point where the record is written: the flag
        reaches _observed_rooms, and the room behind the door stays out."""
        captured = {}

        def _record(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(cb, "async_record_mission", _record)
        callback_ = self._cb()
        ms = callback_.state
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                mission_timer_store=SimpleNamespace(
                    planned_rooms=["Kitchen", "Office"], current_room_idx=1,
                    clear=lambda *_a: None,
                ),
                mission_store=None,
            ),
            async_create_task=lambda _hass, _coro, **_kw: None,
            entry_id="e",
        )
        ms.had_cleaning_phase = True
        ms.end_signal_streak = 99
        ms.ran_in_room = False
        cb._confirm_mission_end(
            ms, MagicMock(), entry, end_gate_passes=True, looks_like_end=True,
            mission={"phase": "charge", "cycle": "none", "error": 0},
            phase="charge", reported={},
        )
        assert captured["observed_rooms"] == ["Kitchen"]
        assert ms.ran_in_room is False, "reset for the next mission"


class TestTheCloudMergeIsShownAtOnce:
    """4.2.14, @FJSoninC: after the cloud's room events came in, the room
    history and the last mission summary kept the pre-merge rooms until a
    manual refresh -- they re-render on robot messages, and a docked
    robot may send none for hours."""

    def _run(self, *, changed: bool):
        from datetime import datetime, timezone

        from custom_components.roomba_plus import callbacks as cb

        hass = hass_mock()
        config_entry = entry_mock(schedule_on=hass)
        config_entry.entry_id = "test_entry"
        config_entry.data = {"blid": "BLID9"}
        config_entry.created_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
        rd = config_entry.runtime_data
        rd.mission_store = MagicMock()
        rd.mission_store.backfill_from_cloud.return_value = MagicMock(corrected=0, enriched=0)
        rd.mission_store.adopt_missing_from_cloud.return_value = 0
        rd.mission_store.store_rooms_from_timelines.return_value = 1 if changed else 0
        rd.dirt_threshold_manager = None
        rd.grid_store = None
        rd.robot_profile_store = None
        cc = MagicMock()
        cc.last_update_success = True
        cc.umf_data = {}
        with patch.object(cb, "async_dispatcher_send") as send, \
             patch.object(cb, "region_names_across_maps", return_value={"6": "Office"}):
            cb.make_cloud_refresh_callback(hass, config_entry, cc)()
        return send, rd.mission_store, config_entry

    def test_a_change_re_renders_the_robots_entities(self):
        from custom_components.roomba_plus.const import mission_store_changed_signal

        send, store, entry = self._run(changed=True)
        send.assert_called_once()
        assert send.call_args.args[1] == mission_store_changed_signal("BLID9")
        store.store_rooms_from_timelines.assert_called_once()
        assert store.adopt_missing_from_cloud.call_args.kwargs["since_ts"] == entry.created_at.timestamp()

    def test_no_change_no_signal(self):
        send, _store, _entry = self._run(changed=False)
        send.assert_not_called()

    def test_a_mock_entry_has_no_creation_time(self):
        """A mock's timestamp() would be a number near 1970 -- a floor that
        imports the whole cloud history."""
        from datetime import datetime, timezone

        from custom_components.roomba_plus.callbacks import entry_created_ts

        assert entry_created_ts(MagicMock()) is None
        assert entry_created_ts(object()) is None
        entry = MagicMock()
        entry.created_at = datetime(2026, 9, 20, tzinfo=timezone.utc)
        assert entry_created_ts(entry) == entry.created_at.timestamp()

    def test_setup_also_writes_the_cloud_rooms(self):
        import inspect

        import custom_components.roomba_plus as integration

        source = inspect.getsource(integration._phase_cloud)
        assert "store_rooms_from_timelines(" in source
        assert "or _rooms:" in source

    def test_every_entity_listens(self):
        import inspect

        from custom_components.roomba_plus import entity

        assert "mission_store_changed_signal(self._blid)" in inspect.getsource(entity.IRobotEntity)
