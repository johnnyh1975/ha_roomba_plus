"""Tests for PresenceManager — presence-aware scheduling logic.

Uses the lightweight HA stub from conftest.py. Tests focus on the
state machine (all-away detection, delay, arrival cancellation) and
the is_managed_hold flag, without actual roomba MQTT.
"""
import asyncio
import datetime
import pytest
import sys
import types


# ── Minimal stubs not in conftest ─────────────────────────────────────────────

# Stub homeassistant.helpers.selector (needed by config_flow import chain)
_selector = types.ModuleType("homeassistant.helpers.selector")
_selector.EntitySelector = lambda *a, **kw: None
_selector.EntitySelectorConfig = lambda **kw: None
_selector.SelectSelector = lambda *a, **kw: None
_selector.SelectSelectorConfig = lambda **kw: None
_selector.NumberSelector = lambda *a, **kw: None
_selector.NumberSelectorConfig = lambda **kw: None
_selector.SelectSelectorMode = types.SimpleNamespace(LIST="list")
_selector.NumberSelectorMode = types.SimpleNamespace(SLIDER="slider")
sys.modules.setdefault("homeassistant.helpers.selector", _selector)

from custom_components.roomba_plus.presence_manager import PresenceManager


# ── Fake HA infrastructure ────────────────────────────────────────────────────

class _FakeState:
    def __init__(self, state: str):
        self.state = state


class _FakeStates:
    def __init__(self, states: dict):
        self._states = states

    def get(self, entity_id):
        s = self._states.get(entity_id)
        return _FakeState(s) if s is not None else None


class _FakeTask:
    def __init__(self, coro):
        self._coro = coro
        self._cancelled = False
        self._done = False

    def cancel(self):
        self._cancelled = True

    def done(self):
        return self._done


class _FakeBus:
    def __init__(self):
        self.fired: list[str] = []
        self._listeners: list = []

    def async_listen(self, event_type, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def async_fire(self, event_type, data):
        self.fired.append(event_type)


class _FakeHass:
    def __init__(self, person_states: dict):
        self.states = _FakeStates(person_states)
        self.bus = _FakeBus()
        self._tasks: list = []
        self._executor_calls: list = []

    def async_create_task(self, coro, name=None):
        import inspect
        if inspect.iscoroutine(coro):
            coro.close()  # prevent "coroutine never awaited" warning
        task = _FakeTask(coro)
        self._tasks.append(task)
        return task

    async def async_add_executor_job(self, fn, *args):
        self._executor_calls.append((fn, args))


class _FakeRoomba:
    def __init__(self, sched_hold=False, phase="charge"):
        self.master_state = {
            "state": {
                "reported": {
                    "schedHold": sched_hold,
                    "cleanMissionStatus": {"phase": phase},
                }
            }
        }

    def set_preference(self, key, value):
        self.master_state["state"]["reported"][key] = value


class _FakeRuntimeData:
    def __init__(self, sched_hold=False, phase="charge"):
        self.roomba = _FakeRoomba(sched_hold=sched_hold, phase=phase)
        self.presence_manager = None


class _FakeEntry:
    def __init__(self, options: dict, sched_hold=False, phase="charge"):
        self.options = options
        self.runtime_data = _FakeRuntimeData(sched_hold=sched_hold, phase=phase)


def _make_manager(
    person_states: dict,
    options: dict | None = None,
    sched_hold: bool = False,
    phase: str = "charge",
) -> tuple[PresenceManager, _FakeHass]:
    if options is None:
        options = {
            "presence_entities": list(person_states.keys()),
            "away_delay_min": 0,
            "presence_mode": "away_only",
        }
    hass = _FakeHass(person_states)
    entry = _FakeEntry(options, sched_hold=sched_hold, phase=phase)
    manager = PresenceManager(hass, entry)
    return manager, hass


# ── All-away detection ────────────────────────────────────────────────────────

class TestAllAwayDetection:
    def test_all_away_when_all_not_home(self):
        manager, hass = _make_manager({"person.alice": "not_home"})
        # Internal check: all persons not in _HOME_STATES
        from custom_components.roomba_plus.presence_manager import _HOME_STATES
        states = [hass.states.get("person.alice")]
        all_away = all(s is not None and s.state not in _HOME_STATES for s in states)
        assert all_away is True

    def test_not_all_away_when_someone_home(self):
        manager, hass = _make_manager({
            "person.alice": "not_home",
            "person.bob": "home",
        })
        from custom_components.roomba_plus.presence_manager import _HOME_STATES
        person_ids = ["person.alice", "person.bob"]
        all_away = all(
            (st := hass.states.get(eid)) is not None and st.state not in _HOME_STATES
            for eid in person_ids
        )
        assert all_away is False

    def test_home_states_include_on_and_true(self):
        from custom_components.roomba_plus.presence_manager import _HOME_STATES
        assert "home" in _HOME_STATES
        assert "on" in _HOME_STATES
        assert "true" in _HOME_STATES

    def test_away_states_not_in_home_states(self):
        from custom_components.roomba_plus.presence_manager import _HOME_STATES
        for state in ("not_home", "off", "false", "unknown", "unavailable"):
            assert state not in _HOME_STATES


# ── is_managed_hold ───────────────────────────────────────────────────────────

class TestIsManagedHold:
    def test_starts_false(self):
        manager, _ = _make_manager({"person.alice": "home"})
        assert manager.is_managed_hold is False


# ── cancel ────────────────────────────────────────────────────────────────────

class TestCancel:
    def test_cancel_clears_listeners(self):
        manager, hass = _make_manager({"person.alice": "home"})
        manager.start()
        assert len(manager._cancel_listeners) > 0
        manager.cancel()
        assert len(manager._cancel_listeners) == 0

    def test_cancel_idempotent(self):
        manager, _ = _make_manager({"person.alice": "home"})
        manager.cancel()
        manager.cancel()  # should not raise

    def test_cancel_cancels_away_task(self):
        manager, hass = _make_manager({"person.alice": "not_home"})
        # Manually set a fake away task
        fake_task = _FakeTask(None)
        manager._away_task = fake_task
        manager.cancel()
        assert fake_task._cancelled is True
        assert manager._away_task is None


# ── start — no presence entities ──────────────────────────────────────────────

class TestStartNoEntities:
    def test_start_without_entities_does_not_register_listeners(self):
        manager, hass = _make_manager({}, options={
            "presence_entities": [],
            "away_delay_min": 0,
            "presence_mode": "away_only",
        })
        manager.start()
        assert len(manager._cancel_listeners) == 0


# ── _handle_all_away / _away_delay ────────────────────────────────────────────

class TestHandleAllAway:
    @pytest.mark.asyncio
    async def test_sets_sched_hold_false_in_away_only_mode(self):
        manager, hass = _make_manager(
            {"person.alice": "not_home"},
            options={
                "presence_entities": ["person.alice"],
                "away_delay_min": 0,
                "presence_mode": "away_only",
            },
            sched_hold=True,
        )
        await manager._away_delay(0)
        # Should have called set_preference via executor
        assert len(hass._executor_calls) == 1
        fn, args = hass._executor_calls[0]
        assert args == ("schedHold", False)

    @pytest.mark.asyncio
    async def test_fires_event_in_always_ask_mode(self):
        manager, hass = _make_manager(
            {"person.alice": "not_home"},
            options={
                "presence_entities": ["person.alice"],
                "away_delay_min": 0,
                "presence_mode": "always_ask",
            },
        )
        await manager._away_delay(0)
        from custom_components.roomba_plus.const import EVENT_ALL_AWAY
        assert EVENT_ALL_AWAY in hass.bus.fired

    @pytest.mark.asyncio
    async def test_cancelled_delay_does_not_set_hold(self):
        manager, hass = _make_manager(
            {"person.alice": "not_home"},
            options={
                "presence_entities": ["person.alice"],
                "away_delay_min": 1,
                "presence_mode": "away_only",
            },
        )
        # Create and immediately cancel the delay
        task_coro = manager._away_delay(3600)
        import asyncio
        task = asyncio.ensure_future(task_coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # No executor calls since cancelled
        assert len(hass._executor_calls) == 0


# ── _handle_someone_home ──────────────────────────────────────────────────────

class TestHandleSomeoneHome:
    @pytest.mark.asyncio
    async def test_cancels_pending_away_task_on_arrival(self):
        manager, hass = _make_manager({"person.alice": "home"})
        fake_task = _FakeTask(None)
        manager._away_task = fake_task
        await manager._handle_someone_home()
        assert fake_task._cancelled is True
        assert manager._away_task is None

    @pytest.mark.asyncio
    async def test_no_sched_hold_write_when_task_cancelled(self):
        manager, hass = _make_manager({"person.alice": "home"})
        fake_task = _FakeTask(None)
        manager._away_task = fake_task
        await manager._handle_someone_home()
        # No executor calls — we only cancelled the task
        assert len(hass._executor_calls) == 0

    @pytest.mark.asyncio
    async def test_sets_sched_hold_true_when_unfrozen(self):
        """When schedule is currently unfrozen AND PM did the unfreeze, re-freeze on arrival."""
        manager, hass = _make_manager(
            {"person.alice": "home"},
            sched_hold=False,   # currently unfrozen
        )
        # Simulate that PM previously performed the unfreeze
        manager._managed_hold = False
        manager._did_unfreeze = True
        await manager._handle_someone_home()
        assert len(hass._executor_calls) == 1
        _, args = hass._executor_calls[0]
        assert args == ("schedHold", True)

    @pytest.mark.asyncio
    async def test_does_not_refreeze_when_pm_did_not_unfreeze(self):
        """PM must not claim ownership of a manual or pre-existing hold release."""
        manager, hass = _make_manager(
            {"person.alice": "home"},
            sched_hold=False,   # unfrozen, but NOT by PM
        )
        # _did_unfreeze is False (default) — PM never performed an unfreeze
        await manager._handle_someone_home()
        # Should NOT call set_preference
        assert len(hass._executor_calls) == 0

    @pytest.mark.asyncio
    async def test_fires_person_detected_event_during_clean(self):
        manager, hass = _make_manager(
            {"person.alice": "home"},
            sched_hold=False,
            phase="run",   # robot is actively cleaning
        )
        # Simulate that PM previously performed the unfreeze
        manager._did_unfreeze = True
        await manager._handle_someone_home()
        from custom_components.roomba_plus.const import EVENT_PERSON_DETECTED_DURING_CLEAN
        assert EVENT_PERSON_DETECTED_DURING_CLEAN in hass.bus.fired


# ── schedHold not supported ───────────────────────────────────────────────────

class TestSchedHoldNotSupported:
    @pytest.mark.asyncio
    async def test_no_write_when_sched_hold_not_in_state(self):
        """Robot without schedHold: set_preference is never called."""
        manager, hass = _make_manager({"person.alice": "home"})
        # Remove schedHold from state
        manager._entry.runtime_data.roomba.master_state["state"]["reported"].pop(
            "schedHold", None
        )
        await manager._set_sched_hold(False)
        assert len(hass._executor_calls) == 0
