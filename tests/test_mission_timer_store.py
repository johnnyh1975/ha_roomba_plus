"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
from collections import defaultdict
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import itertools
import pytest
from contextlib import contextmanager
import tests.conftest
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
import time
from custom_components.roomba_plus.callbacks import _ROOM_TRANSITION_CANDIDATE_PHASES
from custom_components.roomba_plus.callbacks import _ROOM_TRANSITION_MIN_ELAPSED_RATIO
from custom_components.roomba_plus.callbacks import _room_transition_confidence_ok
from custom_components.roomba_plus.callbacks import make_mission_callback


@contextmanager
def _patch_callbacks_time():
    """Patch callbacks._time_mod so that monotonic() returns increasing values.

    Each call returns a value 10 s larger than the previous — ensures that the
    END_SIGNAL_MIN_HOLD_SECONDS (2.0 s) time gate is always satisfied.
    """
    _c = itertools.count(1)
    with patch("custom_components.roomba_plus.callbacks._time_mod") as tmock:
        tmock.monotonic.side_effect = lambda: float(next(_c)) * 10.0
        tmock.time.return_value = 1000.0
        yield tmock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_hass() -> MagicMock:
    hass = MagicMock()
    def _close_coro(*args, **kwargs):
        import asyncio as _asyncio
        for a in args:
            if _asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    # Close any coroutines passed to async_create_task so Python does not emit
    # "coroutine 'X' was never awaited" RuntimeWarnings during tests.
    def _close_coro(*args, **kwargs):
        import asyncio
        for a in args:
            if asyncio.iscoroutine(a):
                a.close()
    hass.async_create_task = _close_coro
    return hass


def _make_mts(
    planned_rooms: list[str] | None = None,
    current_room_idx: int = 0,
    mission_id: str | None = "mission_42",
) -> MissionTimerStore:
    mts = object.__new__(MissionTimerStore)
    mts.mission_id = mission_id
    mts.run_sec = 120.0
    mts.total_estimated_sec = 2700.0
    # Use sentinel to distinguish None (use default) from [] (empty list)
    mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"] if planned_rooms is None else planned_rooms
    mts.current_room_idx = current_room_idx
    mts.recharge_positions = []
    mts.snapshot_ts = time.time()
    mts._last_phase_ts = 0.0
    return mts


def _make_data(
    mts: MissionTimerStore | None,
    phase: str = "hmUsrDock",
) -> MagicMock:
    data = MagicMock()
    data.mission_timer_store = mts
    data.roomba_reported_state.return_value = {
        "cleanMissionStatus": {"phase": phase}
    }
    return data


def _make_service_call(entity_id: str = "vacuum.roomba_test") -> MagicMock:
    call = MagicMock()
    call.data = {"entity_id": [entity_id]}
    call.hass = MagicMock()
    return call


def _msg(phase: str, cycle: str = "clean", error: int = 0, nmssn: int = 42) -> dict:
    return {"state": {"reported": {
        "cleanMissionStatus": {
            "phase": phase,
            "cycle": cycle,
            "error": error,
            "mssnStrtTm": 1700000000,
            "nMssn": nmssn,
        },
        "bbrun": {"nStuck": 0},
    }}}


def _make_entry(mts: MissionTimerStore) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"blid": "ABC123"}
    entry.options = {}
    entry.runtime_data.mission_timer_store = mts
    entry.runtime_data.mission_store = MagicMock()
    entry.runtime_data.mission_store.async_append = AsyncMock()
    entry.runtime_data.mission_store.async_save = AsyncMock()
    entry.runtime_data.zone_store = None
    entry.runtime_data.map_capability = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.presence_manager = None
    entry.runtime_data.demand_triggered_ts = None
    entry.runtime_data.robot_profile_store = None
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
    return entry


def _make_entry_v280_auto_advance_room_live(mts: MissionTimerStore) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"blid": "ABC123"}
    entry.options = {}
    entry.runtime_data.mission_timer_store = mts
    entry.runtime_data.mission_store = MagicMock()
    entry.runtime_data.mission_store.async_append = AsyncMock()
    entry.runtime_data.mission_store.async_save = AsyncMock()
    entry.runtime_data.zone_store = None
    entry.runtime_data.map_capability = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.presence_manager = None
    entry.runtime_data.demand_triggered_ts = None
    entry.runtime_data.robot_profile_store = None
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.roomba.master_state = {
        "state": {"reported": {
            "lastCommand": {"regions": [
                {"region_name": "Kitchen", "region_id": "19"},
                {"region_name": "Hall", "region_id": "21"},
            ]},
        }}
    }
    return entry


def _msg_v280_inter_room_recharge(phase: str, cycle: str = "clean", nmssn: int = 42) -> dict:
    """Build a minimal MQTT cleanMissionStatus message."""
    return {"state": {"reported": {
        "cleanMissionStatus": {
            "phase": phase,
            "cycle": cycle,
            "mssnStrtTm": 1700000000,
            "nMssn": nmssn,
        },
        "bbrun": {"nStuck": 0},
    }}}


def _make_mts_v280_inter_room_recharge() -> MissionTimerStore:
    mts = MissionTimerStore()
    return mts


def _run_callback(cb, *msgs):
    """Fire callback with each message in sequence."""
    for msg in msgs:
        cb(msg)


class TestMissionTimerStore:

    def test_first_phase_run_initialises_mission(self):
        """First on_phase_run for a new mission_id resets and sets up."""
        mts = MissionTimerStore()
        hass = _make_hass()
        mts.on_phase_run("m_111", hass, "entry1")
        assert mts.mission_id == "m_111"
        assert mts.run_sec == 0  # no delta yet — just initialised

    def test_accumulates_delta_on_consecutive_calls(self):
        """Consecutive phase-run calls accumulate elapsed seconds."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 0
        mts._last_phase_ts = 90.0   # pre-set so delta=10 when monotonic()=100
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_timer_store.time") as mock_time:
            mock_time.monotonic.return_value = 100.0   # now=100, delta=100-90=10
            mts.on_phase_run("m_111", hass, "entry1")
        assert mts.run_sec == 10

    def test_large_gap_clamped(self):
        """Gaps > 120 s (HA restart, recharge) are not accumulated."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts._last_phase_ts = 0.0
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_timer_store.time") as mock_time:
            mock_time.monotonic.return_value = 500.0
            mts._last_phase_ts = 100.0  # 400 s gap → clamped
            mts.on_phase_run("m_111", hass, "entry1")
        assert mts.run_sec == 0

    def test_new_mission_id_resets_counter(self):
        """Different mission_id triggers a full reset."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 300
        hass = _make_hass()
        mts.on_phase_run("m_222", hass, "entry1")
        assert mts.mission_id == "m_222"
        assert mts.run_sec == 0

    def test_on_phase_other_resets_timestamp(self):
        """on_phase_other() resets _last_phase_ts to prevent gap accumulation."""
        mts = MissionTimerStore()
        mts._last_phase_ts = 100.0
        mts.on_phase_other()
        assert mts._last_phase_ts == 0.0

    def test_clear_resets_all_fields(self):
        """clear() wipes mission_id, run_sec, and timestamp."""
        mts = MissionTimerStore()
        mts.mission_id = "m_111"
        mts.run_sec = 200
        mts._last_phase_ts = 50.0
        hass = _make_hass()
        mts.clear(hass, "entry1")
        assert mts.mission_id is None
        assert mts.run_sec == 0
        assert mts._last_phase_ts == 0.0


class TestMissionTimerStorePersist:
    """MissionTimerStore: extended schema persistence and derived properties."""

    def _make_store(self) -> "MissionTimerStore":
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        return MissionTimerStore()

    def test_elapsed_run_min_is_float(self):
        """MP-ELAPSED: 12 seconds → 0.2 min, not 0."""
        store = self._make_store()
        store.mission_id = "test_mission"
        store.run_sec = 12.0
        assert store.elapsed_run_min == pytest.approx(0.2)

    def test_current_room_from_plan(self):
        """MP-ROOMS: current_room returns planned_rooms[current_room_idx]."""
        store = self._make_store()
        store.mission_id = "test"
        store.planned_rooms = ["Bagno", "Cucina", "Salotto"]
        store.current_room_idx = 1
        assert store.current_room == "Cucina"

    def test_next_room_from_plan(self):
        """MP-ROOMS: next_room returns planned_rooms[current_room_idx + 1]."""
        store = self._make_store()
        store.mission_id = "test"
        store.planned_rooms = ["Bagno", "Cucina", "Salotto"]
        store.current_room_idx = 1
        assert store.next_room == "Salotto"

    def test_stale_snapshot_discards_on_load(self):
        """Snapshot older than 7200s must be discarded on load."""
        import asyncio
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore

        store = MissionTimerStore()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = asyncio.new_event_loop()

        stale_ts = time.time() - 9000  # 2.5 hours ago

        mock_store_data = {
            "mission_id": "old_mission",
            "run_sec": 300,
            "total_estimated_sec": 4200.0,
            "planned_rooms": ["Room1"],
            "current_room_idx": 0,
            "recharge_positions": [],
            "snapshot_ts": stale_ts,
        }

        async def run_load():
            with patch("custom_components.roomba_plus.mission_timer_store.Store") as MockStore:
                mock_s = AsyncMock()
                mock_s.async_load.return_value = mock_store_data
                MockStore.return_value = mock_s
                await store.async_load(hass, "test_entry")

        hass.loop.run_until_complete(run_load())
        hass.loop.close()

        # Stale snapshot → not loaded
        assert store.mission_id is None
        assert store.run_sec == 0.0


class TestElapsedSecHelper:
    """_elapsed_sec adds live delta in run phase; returns bare run_sec otherwise."""

    def _make_mts(self, run_sec: float, last_phase_ts: float = 0.0):
        mts = MagicMock()
        mts.run_sec = run_sec
        mts._last_phase_ts = last_phase_ts
        return mts

    def test_adds_live_delta_during_run_phase(self):
        """Helper adds monotonic delta when phase=run and _last_phase_ts is set."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        mts = self._make_mts(run_sec=60.0, last_phase_ts=time.monotonic() - 30)
        elapsed = RoombaMissionProgress._elapsed_sec(mts, "run")
        # Should be ~90 s (60 stored + ~30 live)
        assert 85 <= elapsed <= 95

    def test_no_live_delta_when_phase_not_run(self):
        """Helper returns bare run_sec when phase is not 'run'."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        mts = self._make_mts(run_sec=120.0, last_phase_ts=time.monotonic() - 60)
        elapsed = RoombaMissionProgress._elapsed_sec(mts, "hmMidMsn")
        assert elapsed == 120.0

    def test_no_live_delta_when_last_phase_ts_zero(self):
        """Helper returns bare run_sec when _last_phase_ts == 0 (after on_phase_other)."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        mts = self._make_mts(run_sec=60.0, last_phase_ts=0.0)
        elapsed = RoombaMissionProgress._elapsed_sec(mts, "run")
        assert elapsed == 60.0


class TestAttributesUseElapsedSec:
    """extra_state_attributes uses live-delta elapsed (MP-ELAPSED-FIX)."""

    def test_elapsed_run_min_reflects_live_delta(self):
        """elapsed_run_min in attributes matches helper output, not bare run_sec."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        sensor = MagicMock(spec=RoombaMissionProgress)
        sensor._config_entry = MagicMock()
        sensor._elapsed_sec = RoombaMissionProgress._elapsed_sec
        sensor._room_estimates = MagicMock(return_value=[])

        mts = MagicMock()
        mts.mission_id = "m1"
        mts.run_sec = 60.0
        mts._last_phase_ts = time.monotonic() - 120  # 2 min live delta
        mts.current_room = "Bathroom"
        mts.next_room = "Corridor"

        state = {"cleanMissionStatus": {"phase": "run"}}
        data = MagicMock()
        data.roomba_reported_state.return_value = state
        data.mission_timer_store = mts
        sensor._config_entry.runtime_data = data

        with patch(
            "custom_components.roomba_plus.sensor._get_planned_room_order",
            return_value=[],
        ):
            attrs = RoombaMissionProgress.extra_state_attributes.fget(sensor)

        # elapsed_run_min should reflect run_sec + ~120s live delta ≈ 3 min
        assert attrs["elapsed_run_min"] >= 2.5


class TestCurrentRoomPrefersEstimate:
    """estimate-based current_room wins over stale MTS when estimates available."""

    def test_estimate_based_current_room_preferred_over_mts(self):
        """When all room estimates available, current_room comes from elapsed calc."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        sensor = MagicMock(spec=RoombaMissionProgress)
        sensor._config_entry = MagicMock()
        sensor._elapsed_sec = RoombaMissionProgress._elapsed_sec
        # Estimates: Bathroom=60s, Corridor=120s
        sensor._room_estimates = MagicMock(return_value=[60, 120])

        mts = MagicMock()
        mts.mission_id = "m1"
        mts.run_sec = 90.0        # 90 s elapsed → past Bathroom (60 s)
        mts._last_phase_ts = 0.0  # no live delta
        mts.current_room = "Bathroom"   # stale MTS value
        mts.next_room = "Corridor"

        state = {"cleanMissionStatus": {"phase": "run"}}
        data = MagicMock()
        data.roomba_reported_state.return_value = state
        data.mission_timer_store = mts
        sensor._config_entry.runtime_data = data

        with patch(
            "custom_components.roomba_plus.sensor._get_planned_room_order",
            return_value=["Bathroom", "Corridor"],
        ):
            attrs = RoombaMissionProgress.extra_state_attributes.fget(sensor)

        # 90 s elapsed > 60 s (Bathroom estimate) → should be in Corridor
        assert attrs["current_room"] == "Corridor"
        assert attrs["next_room"] is None


class TestOnPhaseOtherFlush:
    """on_phase_other flushes pending delta so elapsed does not drop at transitions."""

    def _make_mts(self, run_sec: float, last_phase_ts: float) -> object:
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        mts = MissionTimerStore.__new__(MissionTimerStore)
        mts.run_sec = run_sec
        mts._last_phase_ts = last_phase_ts
        mts._save_task = None
        return mts

    def test_flush_adds_pending_delta_to_run_sec(self):
        """Delta since last phase=run is flushed into run_sec on phase transition."""
        mts = self._make_mts(run_sec=120.0, last_phase_ts=time.monotonic() - 30)
        mts.on_phase_other()
        # run_sec should now be ~150 s (120 stored + ~30 flushed)
        assert 145 <= mts.run_sec <= 155

    def test_last_phase_ts_reset_after_flush(self):
        """_last_phase_ts is always 0 after on_phase_other regardless of flush."""
        mts = self._make_mts(run_sec=60.0, last_phase_ts=time.monotonic() - 10)
        mts.on_phase_other()
        assert mts._last_phase_ts == 0.0

    def test_no_flush_when_last_phase_ts_zero(self):
        """If _last_phase_ts is already 0 (consecutive on_phase_other), no delta added."""
        mts = self._make_mts(run_sec=60.0, last_phase_ts=0.0)
        mts.on_phase_other()
        assert mts.run_sec == 60.0

    def test_large_delta_not_flushed(self):
        """Delta >= 120 s is not flushed — same cap as on_phase_run."""
        mts = self._make_mts(run_sec=60.0, last_phase_ts=time.monotonic() - 200)
        mts.on_phase_other()
        assert mts.run_sec == 60.0   # large gap rejected, run_sec unchanged

    def test_elapsed_does_not_drop_at_transition(self):
        """Simulates the bounce-back pattern: elapsed stays continuous through transition."""
        from custom_components.roomba_plus.sensor import RoombaMissionProgress

        now = time.monotonic()
        mts = self._make_mts(run_sec=120.0, last_phase_ts=now - 30)

        # BEFORE transition: elapsed includes live delta
        elapsed_before = RoombaMissionProgress._elapsed_sec(mts, "run")
        assert 145 <= elapsed_before <= 155

        # Transition fires — flush pending delta
        mts.on_phase_other()

        # AFTER transition: elapsed equals flushed run_sec (no live delta, phase=hmMidMsn)
        elapsed_after = RoombaMissionProgress._elapsed_sec(mts, "hmMidMsn")
        # Should be ~150, NOT dropping back to 120
        assert 145 <= elapsed_after <= 155


class TestAdvanceRoomRemoved:
    """advance_room was removed in v2.7.5 as dead code, then re-added in
    v2.8.0 ADVANCE-ROOM-V2 as a condition-gated manual override service."""

    def test_advance_room_present_as_method(self):
        """v2.8.0 ADVANCE-ROOM-V2: advance_room is back as a guarded method."""
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        assert hasattr(MissionTimerStore, "advance_room"), (
            "advance_room re-added in v2.8.0 ADVANCE-ROOM-V2"
        )


class TestGetPlannedRoomOrderFallback:
    """_get_planned_room_order falls back to mts.planned_rooms when live data is empty."""

    def _make_data(self, last_cmd_regions, cc_regions, mts_rooms):
        from unittest.mock import MagicMock
        data = MagicMock()
        data.roomba.master_state = {
            "state": {"reported": {"lastCommand": {"regions": last_cmd_regions}}}
        }
        cc = MagicMock()
        cc.regions = cc_regions
        data.cloud_coordinator = cc
        mts = MagicMock()
        mts.planned_rooms = mts_rooms
        data.mission_timer_store = mts
        return data

    def test_falls_back_when_last_cmd_regions_empty(self):
        """When lastCommand.regions is empty, mts.planned_rooms is returned."""
        from custom_components.roomba_plus.sensor import _get_planned_room_order
        data = self._make_data(
            last_cmd_regions=[],          # transitional empty lastCommand
            cc_regions=[{"id": "1", "name": "Kitchen"}],
            mts_rooms=["Bedroom", "Kitchen", "Lounge"],
        )
        result = _get_planned_room_order(data)
        assert result == ["Bedroom", "Kitchen", "Lounge"]

    def test_falls_back_when_cc_regions_empty(self):
        """When cc.regions is empty (cloud mid-refresh), mts.planned_rooms is returned."""
        from custom_components.roomba_plus.sensor import _get_planned_room_order
        from unittest.mock import MagicMock

        data = MagicMock()
        data.roomba.master_state = {
            "state": {"reported": {"lastCommand": {
                "regions": [{"rid": "1"}, {"rid": "4"}]
            }}}
        }
        cc = MagicMock()
        cc.regions = []   # cloud temporarily empty
        data.cloud_coordinator = cc

        mts = MagicMock()
        mts.planned_rooms = ["Cabina Armadio", "Camera da letto", "Studio"]
        data.mission_timer_store = mts

        result = _get_planned_room_order(data)
        assert result == ["Cabina Armadio", "Camera da letto", "Studio"]

    def test_normal_path_unaffected(self):
        """When cc.regions has data, the normal id→name mapping is used."""
        from custom_components.roomba_plus.sensor import _get_planned_room_order
        from unittest.mock import MagicMock

        data = MagicMock()
        data.roomba.master_state = {
            "state": {"reported": {"lastCommand": {
                "regions": [{"rid": "3"}, {"rid": "7"}]
            }}}
        }
        cc = MagicMock()
        cc.regions = [
            {"id": "3", "name": "Kitchen"},
            {"id": "7", "name": "Study"},
        ]
        data.cloud_coordinator = cc
        mts = MagicMock()
        mts.planned_rooms = ["wrong", "rooms"]   # should NOT be used
        data.mission_timer_store = mts

        result = _get_planned_room_order(data)
        assert result == ["Kitchen", "Study"]


class TestSetMissionPlanCallSignature:
    """set_mission_plan call in callbacks passes hass and entry_id — v2.7.5 regression."""

    def test_set_mission_plan_accepts_hass_and_entry_id(self):
        """Method signature requires hass and entry_id (were missing in v2.7.5)."""
        import inspect
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        params = list(inspect.signature(MissionTimerStore.set_mission_plan).parameters)
        assert "hass" in params
        assert "entry_id" in params


class TestAdvanceRoom:
    def test_advances_idx(self):
        mts = _make_mts(current_room_idx=0)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            result = mts.advance_room(hass, "entry")
        assert result is True
        assert mts.current_room_idx == 1

    def test_returns_false_at_last_room(self):
        mts = _make_mts(planned_rooms=["Kitchen", "Hall"], current_room_idx=1)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            result = mts.advance_room(hass, "entry")
        assert result is False
        assert mts.current_room_idx == 1  # unchanged

    def test_returns_false_no_planned_rooms(self):
        mts = _make_mts(planned_rooms=[])
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        result = mts.advance_room(hass, "entry")
        assert result is False

    def test_saves_after_advance(self):
        mts = _make_mts(current_room_idx=0)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save") as mock_save:
            mts.advance_room(hass, "entry")
        mock_save.assert_called_once_with(hass, "entry")

    def test_current_room_after_advance(self):
        mts = _make_mts(planned_rooms=["Kitchen", "Hall", "Bedroom"], current_room_idx=0)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.advance_room(hass, "entry")
        assert mts.current_room == "Hall"


class TestAdvanceRoomService:
    def _setup(
        self,
        phase: str = "hmUsrDock",
        mts: MissionTimerStore | None = None,
        current_room_idx: int = 0,
    ):
        if mts is None:
            mts = _make_mts(current_room_idx=current_room_idx)
        data = _make_data(mts, phase=phase)
        call = _make_service_call()

        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "cfg_entry"

        config_entry = MagicMock()
        config_entry.entry_id = "cfg_entry"
        config_entry.runtime_data = data

        call.hass.config_entries.async_get_entry.return_value = config_entry
        return call, mts

    @pytest.mark.asyncio
    async def test_advances_when_not_in_run_phase(self):
        call, mts = self._setup(phase="hmUsrDock")
        from custom_components.roomba_plus.services import async_handle_advance_room
        with (
            patch("custom_components.roomba_plus.services.er") as mock_er,
            patch.object(mts, "_schedule_save"),
        ):
            mock_er.async_get.return_value.async_get.return_value = MagicMock(
                config_entry_id="cfg_entry"
            )
            await async_handle_advance_room(call)
        assert mts.current_room_idx == 1

    @pytest.mark.asyncio
    async def test_skips_when_phase_is_run(self):
        call, mts = self._setup(phase="run")
        from custom_components.roomba_plus.services import async_handle_advance_room
        with (
            patch("custom_components.roomba_plus.services.er") as mock_er,
            patch.object(mts, "_schedule_save"),
        ):
            mock_er.async_get.return_value.async_get.return_value = MagicMock(
                config_entry_id="cfg_entry"
            )
            await async_handle_advance_room(call)
        assert mts.current_room_idx == 0  # unchanged

    @pytest.mark.asyncio
    async def test_skips_when_no_active_mission(self):
        mts = _make_mts(mission_id=None)
        call, _ = self._setup(mts=mts)
        from custom_components.roomba_plus.services import async_handle_advance_room
        with (
            patch("custom_components.roomba_plus.services.er") as mock_er,
            patch.object(mts, "_schedule_save"),
        ):
            mock_er.async_get.return_value.async_get.return_value = MagicMock(
                config_entry_id="cfg_entry"
            )
            await async_handle_advance_room(call)
        assert mts.current_room_idx == 0  # unchanged

    @pytest.mark.asyncio
    async def test_raises_when_no_mts(self):
        """Raises ServiceValidationError when MissionTimerStore is None."""
        from homeassistant.exceptions import ServiceValidationError
        from custom_components.roomba_plus.services import async_handle_advance_room
        call = _make_service_call()
        data = _make_data(mts=None)
        config_entry = MagicMock()
        config_entry.entry_id = "cfg"
        config_entry.runtime_data = data
        call.hass.config_entries.async_get_entry.return_value = config_entry

        with (
            patch("custom_components.roomba_plus.services.er") as mock_er,
            pytest.raises(ServiceValidationError),
        ):
            mock_er.async_get.return_value.async_get.return_value = MagicMock(
                config_entry_id="cfg"
            )
            await async_handle_advance_room(call)


class TestRoomTransitionConfidenceOk:
    def _mts(self, expected_room_sec, time_in_current_room_sec):
        mts = MagicMock()
        mts.expected_room_sec = expected_room_sec
        mts.time_in_current_room_sec = time_in_current_room_sec
        return mts

    def test_false_when_error_present(self):
        mts = self._mts(200.0, 150.0)
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 224}, mts
        ) is False

    def test_false_when_cycle_none(self):
        """cycle=none means genuine mission end — not a transition signal."""
        mts = self._mts(200.0, 150.0)
        assert _room_transition_confidence_ok(
            {"cycle": "none", "error": 0}, mts
        ) is False

    def test_false_when_cycle_missing(self):
        mts = self._mts(200.0, 150.0)
        assert _room_transition_confidence_ok({"error": 0}, mts) is False

    def test_true_when_cycle_clean_and_time_ok(self):
        mts = self._mts(200.0, 150.0)  # 150 >= 200*0.5=100
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is True

    def test_true_when_cycle_quick(self):
        mts = self._mts(200.0, 150.0)
        assert _room_transition_confidence_ok(
            {"cycle": "quick", "error": 0}, mts
        ) is True

    def test_false_when_elapsed_below_threshold(self):
        """Elapsed time well below expected → likely genuine recharge, not done."""
        mts = self._mts(200.0, 50.0)  # 50 < 200*0.5=100
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is False

    def test_false_when_expected_room_sec_none(self):
        """KNOWN LIMITATION: expected_room_sec always None in current deployment."""
        mts = self._mts(None, 150.0)
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is False

    def test_false_when_expected_room_sec_zero(self):
        mts = self._mts(0.0, 150.0)
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is False

    def test_exactly_at_threshold_passes(self):
        """elapsed == expected * ratio is inclusive (>=)."""
        mts = self._mts(200.0, 100.0)  # exactly 0.5 ratio
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is True

    def test_error_string_zero_passes(self):
        """error='0' (string) — defensive, mission.get default handles int 0 only.
        This test documents current behaviour with a falsy non-zero-int value."""
        mts = self._mts(200.0, 150.0)
        # error=0 (int) is the only falsy case handled; this just confirms int 0 works
        assert _room_transition_confidence_ok(
            {"cycle": "clean", "error": 0}, mts
        ) is True


class TestMissionTimerStoreTimingProperties:
    def _mts(self) -> MissionTimerStore:
        return MissionTimerStore()

    def test_expected_room_sec_none_when_no_estimate(self):
        mts = self._mts()
        mts.total_estimated_sec = None
        mts.planned_rooms = ["A", "B"]
        assert mts.expected_room_sec is None

    def test_expected_room_sec_none_when_zero_estimate(self):
        """Documents the v2.8.0 known limitation: total_estimated_sec=0 → None."""
        mts = self._mts()
        mts.total_estimated_sec = 0
        mts.planned_rooms = ["A", "B"]
        assert mts.expected_room_sec is None

    def test_expected_room_sec_none_when_no_rooms(self):
        mts = self._mts()
        mts.total_estimated_sec = 1000.0
        mts.planned_rooms = []
        assert mts.expected_room_sec is None

    def test_expected_room_sec_uniform_split(self):
        mts = self._mts()
        mts.total_estimated_sec = 1000.0
        mts.planned_rooms = ["A", "B", "C", "D"]
        assert mts.expected_room_sec == 250.0

    def test_time_in_current_room_sec_basic(self):
        mts = self._mts()
        mts.run_sec = 500.0
        mts.room_entered_run_sec = 200.0
        assert mts.time_in_current_room_sec == 300.0

    def test_time_in_current_room_sec_never_negative(self):
        """Defensive: run_sec < room_entered_run_sec (clock skew) → 0, not negative."""
        mts = self._mts()
        mts.run_sec = 100.0
        mts.room_entered_run_sec = 200.0
        assert mts.time_in_current_room_sec == 0.0

    def test_room_entered_run_sec_resets_on_new_mission(self):
        mts = self._mts()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.set_mission_plan("m1", ["A", "B"], 0, hass, "e")
            mts.room_entered_run_sec = 50.0  # simulate some progress
            mts.set_mission_plan("m2", ["C", "D"], 0, hass, "e")  # new mission
        assert mts.room_entered_run_sec == 0.0

    def test_room_entered_run_sec_updates_on_advance(self):
        mts = self._mts()
        mts.planned_rooms = ["A", "B", "C"]
        mts.current_room_idx = 0
        mts.run_sec = 300.0
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.advance_room(hass, "e")
        assert mts.room_entered_run_sec == 300.0
        assert mts.time_in_current_room_sec == 0.0  # just entered new room


class TestAutoAdvanceRoomIntegration:
    @pytest.mark.asyncio
    async def test_no_advance_when_expected_room_sec_unavailable(self):
        """Current deployment state: total_estimated_sec=0 → never auto-advances.

        This is the DORMANT-BY-DESIGN behaviour — confirms the feature does not
        misfire given the current lack of a real per-room time estimate.
        """
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="clean"))

        assert mts.current_room_idx == idx_before, (
            "AUTO-ADVANCE-ROOM fired despite expected_room_sec being unavailable "
            "(total_estimated_sec always 0 in current deployment)"
        )

    @pytest.mark.asyncio
    async def test_advances_when_confidence_signal_present(self):
        """With a real time estimate wired in, the room advances on the
        transient phase once the time-in-room confidence check passes."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0  # 300s/room average
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 250.0  # >= 300*0.5=150 → confidence check passes
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="clean"))

        assert mts.current_room_idx == idx_before + 1, (
            "AUTO-ADVANCE-ROOM did not advance despite confidence signal present"
        )

    @pytest.mark.asyncio
    async def test_no_double_advance_on_repeated_charge_messages(self):
        """Edge-trigger: phase staying 'charge' across multiple messages must
        only advance once, not once per message (would corrupt room tracking
        during a genuine multi-minute recharge)."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 250.0
            cb(_msg("charge", cycle="clean"))
            idx_after_first = mts.current_room_idx
            # Same phase repeated (e.g. firmware re-sends identical state)
            cb(_msg("charge", cycle="clean"))
            cb(_msg("charge", cycle="clean"))

        assert mts.current_room_idx == idx_after_first, (
            "Room advanced multiple times for a single charge-phase dwell "
            "(edge-trigger guard failed)"
        )

    @pytest.mark.asyncio
    async def test_no_advance_when_error_present(self):
        """Error code present (e.g. 224) must block auto-advance even if
        timing would otherwise pass."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 250.0
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="clean", error=224))

        assert mts.current_room_idx == idx_before, \
            "AUTO-ADVANCE-ROOM fired despite error=224 present"

    @pytest.mark.asyncio
    async def test_no_advance_on_genuine_mission_end(self):
        """cycle=none (true mission end) must never trigger auto-advance."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 250.0
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="none"))

        assert mts.current_room_idx == idx_before, \
            "AUTO-ADVANCE-ROOM fired on genuine mission end (cycle=none)"

    @pytest.mark.asyncio
    async def test_no_advance_when_time_in_room_too_short(self):
        """Elapsed time well below expected → likely a genuine recharge
        interrupting the room, not a completion. Must not advance."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0  # 300s/room
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 30.0  # well below 300*0.5=150
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="clean"))

        assert mts.current_room_idx == idx_before, (
            "AUTO-ADVANCE-ROOM fired despite insufficient time in room "
            "(likely a genuine mid-room recharge, not room completion)"
        )

    @pytest.mark.asyncio
    async def test_stop_phase_never_triggers_auto_advance(self):
        """'stop' is excluded from _ROOM_TRANSITION_CANDIDATE_PHASES — a
        deliberate user stop must never auto-advance the room."""
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))
            mts.run_sec = 250.0
            idx_before = mts.current_room_idx
            cb(_msg("stop", cycle="clean"))

        assert mts.current_room_idx == idx_before, \
            "AUTO-ADVANCE-ROOM fired on 'stop' phase (should be excluded)"

    def test_candidate_phases_set_contents(self):
        """Documents exactly which phases are eligible for auto-advance."""
        assert _ROOM_TRANSITION_CANDIDATE_PHASES == frozenset({"charge", "hmPostMsn"})

    def test_min_elapsed_ratio_is_conservative(self):
        """Threshold should require at least half the expected room time."""
        assert _ROOM_TRANSITION_MIN_ELAPSED_RATIO == 0.5


class TestRoomEstimatesSecField:
    def _mts(self) -> MissionTimerStore:
        return MissionTimerStore()

    def test_default_empty_list(self):
        mts = self._mts()
        assert mts.room_estimates_sec == []

    def test_set_mission_plan_stores_room_estimates(self):
        mts = self._mts()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.set_mission_plan(
                "m1", ["Kitchen", "Hall"], 1000.0, hass, "e",
                room_estimates_sec=[600.0, 400.0],
            )
        assert mts.room_estimates_sec == [600.0, 400.0]

    def test_set_mission_plan_without_room_estimates_defaults_empty(self):
        """Backward compatibility: omitting the new kwarg still works."""
        mts = self._mts()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.set_mission_plan("m1", ["Kitchen", "Hall"], 1000.0, hass, "e")
        assert mts.room_estimates_sec == []

    def test_room_estimates_reset_on_new_mission(self):
        mts = self._mts()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.set_mission_plan(
                "m1", ["Kitchen"], 600.0, hass, "e", room_estimates_sec=[600.0]
            )
            mts.set_mission_plan(
                "m2", ["Hall"], 400.0, hass, "e", room_estimates_sec=[400.0]
            )
        assert mts.room_estimates_sec == [400.0]

    def test_room_estimates_reset_on_clear(self):
        mts = self._mts()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch.object(mts, "_schedule_save"):
            mts.set_mission_plan(
                "m1", ["Kitchen"], 600.0, hass, "e", room_estimates_sec=[600.0]
            )
            mts.clear(hass, "e")
        assert mts.room_estimates_sec == []

    @pytest.mark.asyncio
    async def test_persists_through_storage_roundtrip(self):
        mts = self._mts()
        mts.room_estimates_sec = [600.0, None, 400.0]

        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.mission_timer_store.Store",
            return_value=store_mock,
        ):
            await mts.async_save(hass, "entry")

        saved_dict = store_mock.async_save.call_args[0][0]
        assert saved_dict["room_estimates_sec"] == [600.0, None, 400.0]

        # Now verify restore: async_load reads the same key back
        store_mock2 = MagicMock()
        store_mock2.async_load = AsyncMock(return_value=saved_dict)
        mts2 = self._mts()
        with patch(
            "custom_components.roomba_plus.mission_timer_store.Store",
            return_value=store_mock2,
        ):
            await mts2.async_load(hass, "entry")
        assert mts2.room_estimates_sec == [600.0, None, 400.0]


class TestExpectedRoomSecPrefersRealData:
    def _mts(self) -> MissionTimerStore:
        return MissionTimerStore()

    def test_uses_real_estimate_when_available(self):
        mts = self._mts()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0  # uniform split would give 300.0
        mts.room_estimates_sec = [600.0, 200.0, 100.0]
        mts.current_room_idx = 0
        assert mts.expected_room_sec == 600.0  # real value, not 300.0

    def test_uses_real_estimate_for_current_room_index(self):
        mts = self._mts()
        mts.planned_rooms = ["Kitchen", "Hall", "Bedroom"]
        mts.total_estimated_sec = 900.0
        mts.room_estimates_sec = [600.0, 200.0, 100.0]
        mts.current_room_idx = 1
        assert mts.expected_room_sec == 200.0

    def test_falls_back_to_uniform_when_specific_room_is_none(self):
        """Auto pass mode room (None estimate) falls back to uniform split."""
        mts = self._mts()
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.total_estimated_sec = 1000.0
        mts.room_estimates_sec = [None, 400.0]
        mts.current_room_idx = 0
        assert mts.expected_room_sec == 500.0  # 1000 / 2 rooms

    def test_falls_back_to_uniform_when_room_estimates_empty(self):
        mts = self._mts()
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.total_estimated_sec = 1000.0
        mts.room_estimates_sec = []
        mts.current_room_idx = 0
        assert mts.expected_room_sec == 500.0

    def test_falls_back_to_uniform_when_index_out_of_range(self):
        mts = self._mts()
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.total_estimated_sec = 1000.0
        mts.room_estimates_sec = [600.0]  # only 1 entry, idx 1 out of range
        mts.current_room_idx = 1
        assert mts.expected_room_sec == 500.0

    def test_none_when_neither_source_available(self):
        mts = self._mts()
        mts.planned_rooms = ["Kitchen"]
        mts.total_estimated_sec = None
        mts.room_estimates_sec = []
        assert mts.expected_room_sec is None


class TestCallbacksWiresRealEstimates:
    @pytest.mark.asyncio
    async def test_set_mission_plan_receives_nonzero_total_when_estimates_exist(self):
        """The old hardcoded `0` is gone — real estimates produce a real total."""
        mts = MissionTimerStore()
        entry = _make_entry_v280_auto_advance_room_live(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        fake_estimates = [600, 400]  # Kitchen, Hall
        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=fake_estimates,
        ):
            from custom_components.roomba_plus.callbacks import make_mission_callback
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))

        assert mts.total_estimated_sec == 1000.0  # sum(600, 400), not 0
        assert mts.room_estimates_sec == [600, 400]

    @pytest.mark.asyncio
    async def test_total_estimated_sec_none_when_no_estimates_available(self):
        """All-None per-room estimates (e.g. Auto mode) → total is None, not 0."""
        mts = MissionTimerStore()
        entry = _make_entry_v280_auto_advance_room_live(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=[None, None],
        ):
            from custom_components.roomba_plus.callbacks import make_mission_callback
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))

        assert mts.total_estimated_sec is None
        assert mts.room_estimates_sec == [None, None]

    @pytest.mark.asyncio
    async def test_auto_advance_room_now_fires_with_real_estimates(self):
        """End-to-end: AUTO-ADVANCE-ROOM is no longer dormant once a real
        per-room estimate is wired in via set_mission_plan."""
        mts = MissionTimerStore()
        entry = _make_entry_v280_auto_advance_room_live(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        fake_estimates = [600, 400]  # Kitchen=600s, Hall=400s
        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=fake_estimates,
        ):
            from custom_components.roomba_plus.callbacks import make_mission_callback
            cb = make_mission_callback(hass, entry)
            cb(_msg("run", cycle="clean"))  # sets mission plan: Kitchen=600s
            mts.run_sec = 350.0  # >= 600*0.5=300 → confidence check passes
            idx_before = mts.current_room_idx
            cb(_msg("charge", cycle="clean"))  # transient phase → confidence check

        assert mts.current_room_idx == idx_before + 1, (
            "AUTO-ADVANCE-ROOM still dormant after wiring real per-room "
            "estimates — expected_room_sec should now resolve to 600.0"
        )


class TestInterRoomRechargeMTSNotReset:
    """Thonno regression: phase=charge with cycle=clean must not clear MTS."""

    @pytest.mark.asyncio
    async def test_mts_not_cleared_on_charge_with_cycle_clean(self):
        """phase=charge + cycle=clean (inter-room) → MTS NOT cleared."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            # Mission starts
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            # Simulate some run time accumulation
            mission_id_after_run = mts.mission_id

            # Inter-room charge phase (lewis firmware between rooms)
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="clean"))

        # MTS must NOT be cleared — mission_id still set
        assert mts.mission_id is not None, (
            "MTS was incorrectly cleared on phase=charge + cycle=clean "
            "(Thonno inter-room recharge regression)"
        )
        assert mts.mission_id == mission_id_after_run, \
            "MTS mission_id changed on inter-room charge transition"

    @pytest.mark.asyncio
    async def test_run_sec_not_reset_on_inter_room_charge(self):
        """run_sec accumulated before inter-room charge must be preserved."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            # Manually set some run_sec to simulate elapsed time
            mts.run_sec = 300.0  # 5 minutes

            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="clean"))

        # run_sec must NOT be reset to 0
        # (on_phase_other adds a tiny flush delta; we check >= not ==)
        assert mts.run_sec >= 300.0, \
            f"run_sec reset on inter-room charge: {mts.run_sec} < 300.0"

    @pytest.mark.asyncio
    async def test_progress_continues_after_inter_room_charge(self):
        """After inter-room charge, next run phase continues accumulating."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            # Phase 1: run (room A)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            mts.run_sec = 300.0
            mission_id_room_a = mts.mission_id

            # Phase 2: inter-room charge (lewis firmware)
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="clean"))

            # Phase 3: run (room B) — should continue, not new mission
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))

        # Mission ID must be the same (not a new mission)
        assert mts.mission_id == mission_id_room_a, (
            f"New mission detected after inter-room charge. "
            f"Expected {mission_id_room_a!r}, got {mts.mission_id!r}"
        )

    @pytest.mark.asyncio
    async def test_cycle_quick_also_guarded(self):
        """cycle='quick' between rooms also prevents false clear."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="quick"))
            mission_id = mts.mission_id
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="quick"))

        assert mts.mission_id == mission_id, \
            "MTS cleared on cycle=quick inter-room charge"

    @pytest.mark.asyncio
    async def test_hmpostmsn_with_cycle_clean_not_cleared(self):
        """hmPostMsn + cycle=clean (lewis inter-room) must NOT clear MTS.

        This was the gap in the original narrow fix (charge-only guard).
        Lewis firmware may send hmPostMsn between rooms if each room segment
        is treated as a sub-mission internally.
        """
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            mission_id = mts.mission_id
            assert mission_id is not None

            # Lewis firmware: hmPostMsn appears between rooms with cycle still "clean"
            _run_callback(cb, _msg_v280_inter_room_recharge("hmPostMsn", cycle="clean"))

        assert mts.mission_id == mission_id, (
            "MTS cleared on hmPostMsn + cycle=clean (inter-room transition). "
            "This was the gap in the original charge-only fix."
        )


class TestGenuineMissionEndClearsMTS:
    """True mission end (cycle=none) must still clear MTS."""

    @pytest.mark.asyncio
    async def test_mts_cleared_on_charge_with_cycle_none(self):
        """phase=charge + cycle=none (true end) → MTS IS cleared.

        v2.8.1: charge is debounced (ambiguous with inter-room transitions) —
        two consecutive charge messages confirm a genuine end.
        v2.8.3: also requires END_SIGNAL_MIN_HOLD_SECONDS gap — provided by
        _patch_callbacks_time() which makes each monotonic() call 10 s later.
        """
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), _patch_callbacks_time():
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            assert mts.mission_id is not None

            # True mission end — robot docked, cycle=none, confirmed over 2 messages
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))

        assert mts.mission_id is None, \
            "MTS was NOT cleared on true mission end (cycle=none)"

    @pytest.mark.asyncio
    async def test_mts_cleared_on_hmpostmsn(self):
        """phase=hmPostMsn clears MTS once confirmed.

        v2.8.1: hmPostMsn is debounced (ambiguous with inter-room transitions).
        v2.8.3: also requires END_SIGNAL_MIN_HOLD_SECONDS gap (provided by patch).
        """
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), _patch_callbacks_time():
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            assert mts.mission_id is not None
            _run_callback(cb, _msg_v280_inter_room_recharge("hmPostMsn", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("hmPostMsn", cycle="none"))

        assert mts.mission_id is None, \
            "MTS was NOT cleared on hmPostMsn"

    @pytest.mark.asyncio
    async def test_mts_cleared_on_stop(self):
        """phase=stop always clears MTS."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            assert mts.mission_id is not None
            _run_callback(cb, _msg_v280_inter_room_recharge("stop", cycle="none"))

        assert mts.mission_id is None, "MTS was NOT cleared on stop"

    @pytest.mark.asyncio
    async def test_mts_cleared_on_charge_no_cycle_field(self):
        """phase=charge with no cycle field → empty string → not guarded → clears."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        def _msg_no_cycle(phase: str) -> dict:
            return {"state": {"reported": {
                "cleanMissionStatus": {
                    "phase": phase,
                    "mssnStrtTm": 1700000000,
                },
                "bbrun": {"nStuck": 0},
            }}}

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), _patch_callbacks_time():
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_no_cycle("run"))
            assert mts.mission_id is not None
            # v2.8.1: charge is debounced — needs 2 consecutive messages
            # v2.8.3: also needs END_SIGNAL_MIN_HOLD_SECONDS gap (provided by patch)
            _run_callback(cb, _msg_no_cycle("charge"))
            _run_callback(cb, _msg_no_cycle("charge"))

        # No cycle field → cycle="" → not "clean" → mission end fires → cleared
        assert mts.mission_id is None, \
            "MTS NOT cleared on charge with no cycle field (pre-existing firmware)"


class TestEndDebounceV281:
    """v2.8.1 (END-DEBOUNCE) — regression coverage for the Thonno multi-room
    report: a single transient cycle misreport on an ambiguous phase
    (charge/hmPostMsn) must NOT clear MissionTimerStore or reset run_sec,
    since the mission is genuinely still running. A genuine end must still
    be detected once the signal is confirmed across two consecutive
    messages, and unambiguous terminal phases (stop/completed/cancelled)
    must still confirm on a single message as before.
    """

    def test_single_transient_charge_blip_does_not_clear_mts(self):
        """One inter-room MQTT message momentarily misreporting cycle on
        phase=charge must not clear the timer — this is the exact mechanism
        behind Thonno's "progress reset to 0% mid-mission" report."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            assert mts.mission_id is not None
            mission_id_before = mts.mission_id
            run_sec_before = mts.run_sec

            # Single transient blip: phase=charge, cycle misreported (not
            # "clean"/"quick") for exactly one message, then mission resumes.
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))

        assert mts.mission_id == mission_id_before, (
            "A single transient end-look message must not clear mission_id "
            "(this is the Thonno progress-reset regression)"
        )
        assert mts.run_sec >= run_sec_before, (
            "run_sec must not have been reset by the transient blip"
        )

    def test_streak_does_not_carry_across_an_interruption(self):
        """charge (1) → run (resets streak) → charge (1, not 2) must NOT
        confirm an end — the debounce counts *consecutive* messages only."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))

        assert mts.mission_id is not None, (
            "Non-consecutive charge signals must not accumulate into a "
            "confirmed end"
        )

    def test_two_consecutive_charge_messages_confirm_genuine_end(self):
        """Two consecutive charge+cycle-misreport messages with sufficient
        time between them (≥ END_SIGNAL_MIN_HOLD_SECONDS) DO confirm a
        genuine end — the debounce threshold is satisfied AND the time gate
        is cleared.  Time is mocked so the second message appears 3 s after
        the first, which is well above the 2 s hold threshold."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), patch(
            "custom_components.roomba_plus.callbacks._time_mod"
        ) as tmock:
            # Monotonic call sequence for run(cycle=clean) → charge(cycle=none) × 2:
            #   call 1: end_signal_first_ts = 1.0  (set when streak 0→1 on first charge)
            #   call 2: time_held on first charge  = 4.0 → held = 3.0 s (just logged; streak=1<2)
            #   call 3: time_held on second charge = 4.0 → held = 3.0 s (no burst: 3.0 ≥ 2.0)
            #   call 4: fire-condition check       = 4.0 → 4.0−1.0=3.0 ≥ 2.0 → FIRE ✓
            # Note: first value must be > 0; if 0.0 the `end_signal_first_ts > 0` guard
            # short-circuits all time_held checks, giving time_held=0.0 always → burst reject.
            tmock.monotonic.side_effect = [
                1.0,   # end_signal_first_ts recorded on first charge
                4.0,   # time_held check on first charge (logged; streak=1, no action)
                4.0,   # time_held check on second charge (streak=2, 3.0 s ≥ 2.0 → no burst)
                4.0,   # fire condition check on second charge → 3.0 s ≥ 2.0 → FIRE
            ]
            tmock.time.return_value = 1000.0
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))

        assert mts.mission_id is None, (
            "Two consecutive genuine-end-look messages with sufficient time "
            "gap must confirm the end"
        )

    def test_rapid_burst_of_two_end_looking_messages_does_not_clear_mts(self):
        """v2.8.3 regression — lewis firmware 22.52.10 sends exactly two
        cleanMissionStatus messages ~21 ms apart during an inter-room
        transition, both with cycle outside {'clean','quick'} and phase in
        {'charge','hmPostMsn'}.  The v2.8.1 count-only debounce
        (END_SIGNAL_DEBOUNCE_COUNT=2) was exactly satisfied by this burst,
        causing MissionTimerStore.clear() to fire and progress to reset to 0%.
        The v2.8.3 time gate must block the burst: time_held < 2.0 s."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ), patch(
            "custom_components.roomba_plus.callbacks._time_mod"
        ) as tmock:
            # Burst: first charge at T=1.000, second at T=1.021 (21 ms apart)
            tmock.monotonic.side_effect = [
                0.0,   # mission start bookkeeping
                1.000, # end_signal_first_ts recorded on first charge
                1.021, # time_held check in the debug/info log block (second charge)
                1.021, # time_held check in the fire condition (second charge)
            ]
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            mission_id_before = mts.mission_id
            run_sec_before = mts.run_sec

            # Rapid burst — both end-looking, but only 21 ms apart
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            # Mission resumes — robot sends run again
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))

        assert mts.mission_id == mission_id_before, (
            "A rapid burst of two end-looking messages must NOT clear "
            "mission_id — this is the Thonno v2.8.2 progress-reset regression"
        )
        assert mts.run_sec >= run_sec_before, (
            "run_sec must not have been reset by the rapid burst"
        )

    def test_stop_phase_still_confirms_immediately_no_debounce(self):
        """stop/completed/cancelled are unambiguous terminal phases — a
        single message must still confirm immediately, same as pre-v2.8.1."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            _run_callback(cb, _msg_v280_inter_room_recharge("stop", cycle="none"))

        assert mts.mission_id is None, (
            "stop is unambiguous and must confirm a genuine end on a single "
            "message, without waiting for debounce"
        )

    def test_mission_continues_correctly_after_blip_recovers(self):
        """After a transient blip resolves, the mission must keep accumulating
        run_sec normally on subsequent run-phase messages, with planned_rooms
        intact (not corrupted by a spurious re-derivation)."""
        mts = _make_mts_v280_inter_room_recharge()
        entry = _make_entry_v280_auto_advance_room_live(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda coro, loop: coro.close(),
        ):
            cb = make_mission_callback(hass, entry)
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))
            assert mts.planned_rooms == ["Kitchen", "Hall"]

            # Transient blip — does not clear, planned_rooms must stay intact
            _run_callback(cb, _msg_v280_inter_room_recharge("charge", cycle="none"))
            _run_callback(cb, _msg_v280_inter_room_recharge("run", cycle="clean"))

        assert mts.mission_id is not None
        assert mts.planned_rooms == ["Kitchen", "Hall"], (
            "planned_rooms must not be corrupted/re-derived by a transient "
            "end-look blip that did not actually end the mission"
        )
