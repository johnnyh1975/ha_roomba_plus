"""Tests for v2.6.3 bug fixes.

Covers:
  - Bug A: stuck → stop/charge bypassed make_mission_callback (mission not recorded)
  - Bug A: stuck → stop/charge bypassed make_mission_complete_callback (no cloud refresh)
  - Bug D: stuck → run fired false mission restart (corrupted start_ts/nstuck_at_start)
  - Bug B1: evac in MISSION_END_PHASES caused premature mission end on i7+ with Clean Base
  - Bug E: RoombaCoverageImage._attr_image_last_updated never updated (dead code path)
  - Bug C: entity.py log spam (no assertion — verified absence of log output)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.callbacks import (
    make_mission_callback,
    make_mission_complete_callback,
)
from custom_components.roomba_plus.const import CLEANING_PHASES, MISSION_END_PHASES


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Bug A: stuck → stop bypasses mission callback ─────────────────────────────

class TestStuckBypassMissionCallback:
    """Bug A — make_mission_callback must fire for stuck → stop/charge."""

    def _run_phases(self, phases_nstuck: list[tuple[str, int]]) -> list[dict]:
        """Drive the callback through a phase sequence and return recorded missions."""
        hass, entry, recorded, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record:
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
        """run → stuck → charge must record mission."""
        recorded = self._run_phases([
            ("run", 0),
            ("stuck", 1),
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


# ── Bug D: stuck → run must NOT re-fire mission start ─────────────────────────

class TestFalseMissionRestartOnRecovery:
    """Bug D — stuck → run must not corrupt mission_start_ts or nstuck_at_start."""

    def test_mission_start_ts_preserved_after_stuck_recovery(self):
        """start_ts captured at first 'run' must survive stuck → run recovery."""
        hass, entry, _, _ = _make_callback_env()
        cb = make_mission_callback(hass, entry)

        captured_start_ts: list[int] = []

        with patch("custom_components.roomba_plus.callbacks.async_record_mission",
                   new_callable=AsyncMock) as mock_record:

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
            # Mission ends normally
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
                   new_callable=AsyncMock) as mock_record:

            async def _capture(*args, **kwargs):
                captured_nstuck_delta.append(kwargs.get("nstuck_delta", -1))

            mock_record.side_effect = _capture

            cb(_msg("run", nstuck=5))    # baseline = 5
            cb(_msg("stuck", nstuck=6)) # nstuck_at_start should still be 5
            cb(_msg("run", nstuck=6))   # recovery — must NOT reset baseline to 6
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
            # Wait >300 s (mock monotonic)
            with patch("custom_components.roomba_plus.callbacks._time_mod") as tmock:
                tmock.monotonic.return_value = 9999.0  # well past 300s
                cb(_msg("charge", nstuck=1))

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))

        # result_override should be "stuck_and_resumed" or "stuck_and_abandoned"
        # — definitely NOT None (which would mean had_stuck_event was False)
        assert len(captured_result_override) == 1
        assert captured_result_override[0] is not None, (
            "result_override must be set when had_stuck_event=True (not reset by recovery)"
        )


# ── Bug A: stuck → stop bypasses make_mission_complete_callback ───────────────

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


# ── Bug B1: evac phase classification ─────────────────────────────────────────

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
                   new_callable=AsyncMock) as mock_record:
            cb(_msg("run", nstuck=0))
            cb(_msg("evac", nstuck=0))  # bin empty
            cb(_msg("run", nstuck=0))   # resume cleaning
            cb(_msg("charge", nstuck=0))  # done

            loop = asyncio.get_event_loop()
            loop.run_until_complete(asyncio.sleep(0))
            assert mock_record.call_count == 1, (
                "Mission must be recorded once after evac → run → charge"
            )


# ── Bug E: Coverage Map image_last_updated never updated ──────────────────────

class TestCoverageMapSignal:
    """Bug E — coverage signal constant must exist and be unique per entry."""

    def test_signal_constant_exists(self):
        from custom_components.roomba_plus.image import _SIGNAL_COVERAGE_UPDATED
        assert "{}" in _SIGNAL_COVERAGE_UPDATED, (
            "Signal must be a format string with entry_id placeholder"
        )

    def test_signal_unique_per_entry(self):
        from custom_components.roomba_plus.image import _SIGNAL_COVERAGE_UPDATED
        sig1 = _SIGNAL_COVERAGE_UPDATED.format("entry_aaa")
        sig2 = _SIGNAL_COVERAGE_UPDATED.format("entry_bbb")
        assert sig1 != sig2

    def test_coverage_image_no_longer_has_last_phase(self):
        """RoombaCoverageImage must not have _last_phase (dead code removed)."""
        from custom_components.roomba_plus.image import RoombaCoverageImage
        import inspect
        src = inspect.getsource(RoombaCoverageImage.__init__)
        assert "_last_phase" not in src, (
            "_last_phase was dead state; should be removed from __init__"
        )

    def test_coverage_image_has_no_trigger_grid_update(self):
        """_trigger_grid_update dead code must be removed."""
        from custom_components.roomba_plus.image import RoombaCoverageImage
        assert not hasattr(RoombaCoverageImage, "_trigger_grid_update"), (
            "_trigger_grid_update was dead code (wrong attr names); must be removed"
        )

    def test_async_send_coverage_signal_is_coroutine(self):
        """_async_send_coverage_signal must be an async function."""
        import asyncio
        from custom_components.roomba_plus.image import _async_send_coverage_signal
        assert asyncio.iscoroutinefunction(_async_send_coverage_signal)
