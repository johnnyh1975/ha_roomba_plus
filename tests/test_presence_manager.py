"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
import datetime
import pytest
import sys
import types
from custom_components.roomba_plus.presence_manager import PresenceManager
from datetime import UTC
from datetime import datetime as datetime_v240_scheduling
from datetime import timedelta
from unittest.mock import MagicMock
from collections import defaultdict
from datetime import datetime as datetime_v250_presence
from unittest.mock import patch
from datetime import datetime as datetime_v260_learning
from datetime import timezone
from unittest.mock import AsyncMock
import tests.conftest
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


_selector = types.ModuleType("homeassistant.helpers.selector")
_selector.EntitySelector = lambda *a, **kw: None
_selector.EntitySelectorConfig = lambda **kw: None
_selector.SelectSelector = lambda *a, **kw: None
_selector.SelectSelectorConfig = lambda **kw: None
_selector.NumberSelector = lambda *a, **kw: None
_selector.NumberSelectorConfig = lambda **kw: None
_selector.SelectSelectorMode = types.SimpleNamespace(LIST="list")
_selector.NumberSelectorMode = types.SimpleNamespace(SLIDER="slider")


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


def _make_pm(person_ids: list[str] | None = None) -> PresenceManager:
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {
        "presence_entities": person_ids if person_ids is not None else ["person.alice"],
        "away_delay_min": 5,
        "presence_mode": "away_only",
    }
    entry.entry_id = "test_pm"
    return PresenceManager(hass, entry)


def _dt(days_ago: int = 0, hour: int = 10, weekday_offset: int = 0) -> datetime_v240_scheduling:
    """Return a UTC datetime_v240_scheduling offset from now."""
    base = datetime_v240_scheduling.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    base -= timedelta(days=days_ago)
    return base


def _make_manager_v250_presence() -> PresenceManager:
    """Build a PresenceManager with minimal mocks."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {"presence_entities": ["person.alice"]}
    return PresenceManager(hass, entry)


def _utc(year: int = 2026, month: int = 1, day: int = 5, hour: int = 10) -> datetime_v250_presence:
    return datetime_v250_presence(year, month, day, hour, 0, 0, tzinfo=UTC)


def _recent_monday() -> datetime_v250_presence:
    """Return a Monday datetime_v250_presence that is within the last 30 days from now."""
    now = datetime_v250_presence.now(UTC)
    # Walk back up to 6 days to find a Monday (weekday=0)
    day = now - timedelta(days=now.weekday())  # this week's Monday
    if (now - day).days > 25:
        # If this Monday is too old, use last week's but still recent
        day = day + timedelta(weeks=-1)
    return day.replace(hour=10, minute=0, second=0, microsecond=0)


def _utcnow() -> datetime_v260_learning:
    return datetime_v260_learning.now(timezone.utc)


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


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


class TestIsManagedHold:
    def test_starts_false(self):
        manager, _ = _make_manager({"person.alice": "home"})
        assert manager.is_managed_hold is False


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


class TestStartNoEntities:
    def test_start_without_entities_does_not_register_listeners(self):
        manager, hass = _make_manager({}, options={
            "presence_entities": [],
            "away_delay_min": 0,
            "presence_mode": "away_only",
        })
        manager.start()
        assert len(manager._cancel_listeners) == 0


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

    @pytest.mark.asyncio
    async def test_explicit_null_clean_mission_status_does_not_raise(self):
        """v3.4.2 NULL-REGRESSION — cleanMissionStatus: null must not crash
        the event-fire check, same confirmed-real bug class as bbrun/bin/cap
        elsewhere (see test_edge_cases.py)."""
        manager, hass = _make_manager(
            {"person.alice": "home"}, sched_hold=False,
        )
        manager._entry.runtime_data.roomba.master_state["state"]["reported"][
            "cleanMissionStatus"
        ] = None
        manager._did_unfreeze = True
        await manager._handle_someone_home()  # must not raise
        from custom_components.roomba_plus.const import EVENT_PERSON_DETECTED_DURING_CLEAN
        assert EVENT_PERSON_DETECTED_DURING_CLEAN not in hass.bus.fired


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


class TestRecordCleanEvent:

    def test_creates_clean_events_dict(self):
        # R1 (v2.5.0): _clean_events is initialised in __init__, not lazily.
        pm = _make_pm()
        assert hasattr(pm, "_clean_events"), "_clean_events must exist after __init__"
        assert len(pm._clean_events) == 0, "Must be empty before any events recorded"
        pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        assert hasattr(pm, "_clean_events")
        assert len(pm._clean_events) == 1, "One slot after recording one event"

    def test_records_event_in_correct_slot(self):
        pm = _make_pm()
        dt = datetime_v240_scheduling.now(UTC).replace(hour=9)
        local = dt.astimezone()
        expected_slot = (local.weekday(), local.hour)
        pm.record_clean_event(dt)
        assert expected_slot in pm._clean_events
        assert len(pm._clean_events[expected_slot]) == 1

    def test_multiple_events_accumulate(self):
        pm = _make_pm()
        dt = datetime_v240_scheduling.now(UTC).replace(hour=9)
        for _ in range(5):
            pm.record_clean_event(dt)
        local = dt.astimezone()
        slot = (local.weekday(), local.hour)
        assert len(pm._clean_events[slot]) == 5

    def test_prunes_events_older_than_90_days(self):
        # P5 (v2.5.0): prune only fires when total > 500 events.
        # With just 2 events (well below threshold), old events are NOT pruned.
        # This is intentional — the overhead of pruning 2 events would exceed
        # the cost of storing them; prune only matters at scale.
        pm = _make_pm()
        old_dt = datetime_v240_scheduling.now(UTC) - timedelta(days=95)
        fresh_dt = datetime_v240_scheduling.now(UTC)
        pm.record_clean_event(old_dt)
        pm.record_clean_event(fresh_dt)
        total = sum(len(v) for v in pm._clean_events.values())
        # Both events remain — threshold not reached
        assert total == 2, "Below-threshold: both events kept (P5 change)"

    def test_events_within_90_days_kept(self):
        pm = _make_pm()
        dt = datetime_v240_scheduling.now(UTC) - timedelta(days=89)
        pm.record_clean_event(dt)
        pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        total = sum(len(v) for v in pm._clean_events.values())
        assert total == 2


class TestPresenceWindows:

    def test_empty_when_no_events(self):
        pm = _make_pm()
        assert pm.presence_windows() == {}

    def test_empty_when_fewer_than_5_events(self):
        pm = _make_pm()
        dt = datetime_v240_scheduling.now(UTC)
        for _ in range(4):
            pm.record_clean_event(dt)
        assert pm.presence_windows() == {}

    def test_returns_dict_with_5_events(self):
        pm = _make_pm()
        dt = datetime_v240_scheduling.now(UTC)
        for _ in range(5):
            pm.record_clean_event(dt)
        windows = pm.presence_windows()
        assert isinstance(windows, dict)
        assert len(windows) >= 1

    def test_slots_are_weekday_hour_tuples(self):
        pm = _make_pm()
        for i in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        windows = pm.presence_windows()
        for key in windows:
            assert isinstance(key, tuple)
            wd, hr = key
            assert 0 <= wd <= 6
            assert 0 <= hr <= 23

    def test_scores_are_between_0_and_1(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        windows = pm.presence_windows()
        for score in windows.values():
            assert 0.0 <= score <= 1.0

    def test_empty_when_no_person_ids(self):
        pm = _make_pm(person_ids=[])
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        assert pm.presence_windows() == {}


class TestPreferredWindow:

    def test_none_when_no_history(self):
        pm = _make_pm()
        assert pm.preferred_window() is None

    def test_none_when_insufficient_events(self):
        pm = _make_pm()
        for _ in range(4):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        assert pm.preferred_window() is None

    def test_returns_weekday_hour_tuple(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC))
        result = pm.preferred_window()
        if result is not None:
            assert isinstance(result, tuple)
            wd, hr = result
            assert 0 <= wd <= 6
            assert 0 <= hr <= 23

    def test_filters_to_today_weekday(self):
        """preferred_window must only consider today's weekday."""
        pm = _make_pm()
        today = datetime_v240_scheduling.now().weekday()
        # Record 5 events today
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC).replace(hour=10))
        result = pm.preferred_window()
        if result is not None:
            assert result[0] == today


class TestRoombaOptimalCleanWindow:

    def _make_sensor(self, pm=None):
        from custom_components.roomba_plus.sensor import RoombaOptimalCleanWindow
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        entry = MagicMock()
        entry.runtime_data.presence_manager = pm
        entry.runtime_data.blid = "BLID"
        roomba.address = "10.0.0.1"
        sensor = object.__new__(RoombaOptimalCleanWindow)
        sensor.vacuum = roomba
        sensor.vacuum_state = {}
        sensor._config_entry = entry
        sensor._attr_unique_id = "test_optimal"
        return sensor

    def test_native_value_none_when_no_pm(self):
        sensor = self._make_sensor(pm=None)
        assert sensor.native_value is None

    def test_native_value_none_when_no_history(self):
        pm = _make_pm()
        sensor = self._make_sensor(pm=pm)
        assert sensor.native_value is None

    def test_native_value_is_datetime_when_window_available(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC).replace(hour=10))
        sensor = self._make_sensor(pm=pm)
        val = sensor.native_value
        if val is not None:
            import datetime as _dt
            assert isinstance(val, _dt.datetime)

    def test_extra_attrs_empty_when_no_pm(self):
        sensor = self._make_sensor(pm=None)
        assert sensor.extra_state_attributes == {}

    def test_extra_attrs_has_windows_key(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime_v240_scheduling.now(UTC).replace(hour=10))
        sensor = self._make_sensor(pm=pm)
        attrs = sensor.extra_state_attributes
        assert "windows" in attrs
        assert "preferred_slot" in attrs

    def test_new_state_filter_always_false(self):
        sensor = self._make_sensor(pm=None)
        assert sensor.new_state_filter({"cleanMissionStatus": {}}) is False


class TestDaySummaryDirtDensity:

    def test_dirt_density_field_defaults_none(self):
        from custom_components.roomba_plus.mission_store import DaySummary
        from datetime import date
        s = DaySummary(date=date.today(), total=1, completed=1, stuck=0,
                       area_sqft=100.0, result="completed")
        assert s.dirt_density is None

    def test_dirt_density_can_be_set(self):
        from custom_components.roomba_plus.mission_store import DaySummary
        from datetime import date
        s = DaySummary(date=date.today(), total=1, completed=1, stuck=0,
                       area_sqft=100.0, result="completed", dirt_density=1.23)
        assert s.dirt_density == 1.23


class TestTotalEnergyConsumed:

    def test_sensor_description_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        keys = [s.key for s in SENSORS]
        assert "total_energy_consumed" in keys

    def test_sensor_device_class_is_energy(self):
        from custom_components.roomba_plus.sensor import SENSORS
        from homeassistant.components.sensor import SensorDeviceClass
        desc = next(s for s in SENSORS if s.key == "total_energy_consumed")
        assert desc.device_class == SensorDeviceClass.ENERGY

    def test_energy_formula_no_profile(self):
        """Falls back to 14.8V when robot_profile is None (non-9-series)."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_energy_consumed")
        entity = MagicMock()
        entity.battery_stats = {"estCap": 2500, "nLithChrg": 100}
        entity._config_entry.runtime_data.robot_profile = None
        # 2500 mAh × 14.8 V × 100 cycles = 3.7 kWh
        result = desc.value_fn(entity)
        assert result is not None
        assert abs(result - 3.7) < 0.01

    def test_energy_formula_9series_liion_scale(self):
        """9-series Li-ion: raw estCap ÷ 3.73 before energy calculation."""
        from custom_components.roomba_plus.sensor import _total_energy_consumed_kwh
        from custom_components.roomba_plus.const import ROBOT_PROFILES
        entity = MagicMock()
        # raw estCap 12311 ÷ 3.73 ≈ 3300 mAh; × 14.4V × 1 cycle
        entity.battery_stats = {
            "estCap": 12311,
            "nLithChrg": 1,
            "nNimhChrg": 0,
        }
        entity._config_entry.runtime_data.robot_profile = ROBOT_PROFILES["9"]
        result = _total_energy_consumed_kwh(entity)
        assert result is not None
        # 3300 mAh × 14.4V × 1 cycle / 1_000_000 ≈ 0.0475 kWh
        assert abs(result - round(3300 * 14.4 * 1 / 1_000_000, 3)) < 0.002

    def test_energy_formula_9series_nimh_aftermarket(self):
        """9-series NiMH aftermarket: raw estCap ÷ 1.87."""
        from custom_components.roomba_plus.sensor import _total_energy_consumed_kwh
        from custom_components.roomba_plus.const import ROBOT_PROFILES
        entity = MagicMock()
        # raw ≈ 3300 × 1.87 = 6171 for NiMH pack
        entity.battery_stats = {
            "estCap": 6171,
            "nLithChrg": 0,
            "nNimhChrg": 1,
        }
        entity._config_entry.runtime_data.robot_profile = ROBOT_PROFILES["9"]
        result = _total_energy_consumed_kwh(entity)
        assert result is not None
        # 3300 mAh × 14.4V × 1 cycle / 1_000_000
        assert abs(result - round(3300 * 14.4 * 1 / 1_000_000, 3)) < 0.005

    def test_energy_formula_9series_nimh_after_battery_swap(self):
        """NiMH detection must work even when nLithChrg > 0 from the OEM period.

        When a user replaces OEM Li-ion with NiMH aftermarket, nLithChrg stays
        at the OEM cycle count (lifetime counter, never resets). The old check
        'nNimhChrg > 0 and nLithChrg == 0' always evaluated to False in this
        case, silently applying the Li-ion scale.  Fixed in v2.5.0: use NiMH
        scale whenever nNimhChrg > 0, regardless of nLithChrg.
        """
        from custom_components.roomba_plus.sensor import _total_energy_consumed_kwh
        from custom_components.roomba_plus.const import ROBOT_PROFILES
        entity = MagicMock()
        # OEM had 163 Li-ion cycles; user then installed NiMH (1 cycle so far)
        entity.battery_stats = {
            "estCap": 6171,       # raw ≈ 3300 × 1.87 (NiMH new pack)
            "nLithChrg": 163,     # OEM period — still > 0 after swap
            "nNimhChrg": 1,       # first NiMH cycle
        }
        entity._config_entry.runtime_data.robot_profile = ROBOT_PROFILES["9"]
        result = _total_energy_consumed_kwh(entity)
        assert result is not None
        # Must use NiMH scale (÷ 1.87), not Li-ion (÷ 3.73)
        # 3300 mAh × 14.4V × 1 cycle / 1_000_000
        assert abs(result - round(3300 * 14.4 * 1 / 1_000_000, 3)) < 0.005

    def test_returns_none_when_no_cycles(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_energy_consumed")
        entity = MagicMock()
        entity.battery_stats = {"estCap": 2500}   # no nLithChrg
        entity._config_entry.runtime_data.robot_profile = None
        assert desc.value_fn(entity) is None

    def test_filter_passes_when_estcap_present(self):
        """Filter passes for any robot with estCap regardless of batteryType."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_energy_consumed")
        # batteryType is a part number, not "nimh" — filter must pass
        state = {"bbchg3": {"estCap": 2500}, "batteryType": "F12432712"}
        assert desc.filter_fn(state)

    def test_filter_blocks_when_estcap_absent(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_energy_consumed")
        assert not desc.filter_fn({"bbchg3": {}})


class TestRecordCleanEventWiring:

    def test_callbacks_py_calls_record_clean_event(self):
        """callbacks.py must invoke presence_manager.record_clean_event() at mission start.

        Without this wire, _clean_events is never populated and presence_windows()
        always returns {}, making optimal_clean_window permanently None.
        """
        import inspect
        from custom_components.roomba_plus import callbacks
        src = inspect.getsource(callbacks)
        assert 'record_clean_event' in src, (
            "callbacks.py must call presence_manager.record_clean_event() at mission start. "
            "Without this, F12a presence_windows() is never populated."
        )

    def test_record_clean_event_called_on_mission_start_path(self):
        """record_clean_event is called inside the mission-start detection block."""
        import inspect
        from custom_components.roomba_plus import callbacks
        src = inspect.getsource(callbacks)
        # v2.6.3: _CLEANING_PHASES guard replaced by had_cleaning_phase flag
        phase_idx = src.find('_ACTIVE_CLEANING_PHASES and not had_cleaning_phase')
        record_idx = src.find('record_clean_event')
        assert phase_idx != -1, "_ACTIVE_CLEANING_PHASES mission-start guard not found in callbacks.py"
        assert record_idx != -1, "record_clean_event not found in callbacks.py"
        # record_clean_event should come after the phase transition (within ~500 chars)
        assert record_idx > phase_idx, (
            "record_clean_event must be called after the mission-start phase transition"
        )
        # v2.9.0 — threshold bumped 800→900: F4e's current_leg_rechrgM
        # double-counting bugfix added one legitimate reset line to this
        # exact span (mission-start block). record_clean_event's actual
        # placement (still immediately after the phase-guard block) is
        # unchanged — this is proximity slack, not a placement regression.
        assert record_idx - phase_idx < 900, (
            "record_clean_event is too far from the mission-start transition — check placement"
        )


class TestCleanEventsInit:
    def test_clean_events_present_immediately_after_init(self):
        """_clean_events must exist on a fresh instance without calling any method."""
        mgr = _make_manager_v250_presence()
        assert hasattr(mgr, "_clean_events"), "_clean_events should be set in __init__"
        assert isinstance(mgr._clean_events, defaultdict)

    def test_clean_events_empty_on_fresh_instance(self):
        mgr = _make_manager_v250_presence()
        assert len(mgr._clean_events) == 0

    def test_presence_windows_works_without_prior_record_call(self):
        """presence_windows must not raise when called before any records added."""
        mgr = _make_manager_v250_presence()
        result = mgr.presence_windows()
        assert result == {}

    def test_preferred_window_works_without_prior_record_call(self):
        mgr = _make_manager_v250_presence()
        result = mgr.preferred_window()
        assert result is None


class TestPreferredWindowTimezone:
    def test_preferred_window_uses_ha_timezone(self):
        """preferred_window should use dt_util.now() weekday, not datetime_v250_presence.now()."""
        mgr = _make_manager_v250_presence()

        # Seed 6 events on a recent Monday at 10:00 UTC so recent_count > 0
        monday_utc = _recent_monday()
        assert monday_utc.weekday() == 0, "Fixture must be a Monday"
        for _ in range(6):
            mgr.record_clean_event(monday_utc)

        # Mock dt_util.now() to return that same Monday
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.now",
            return_value=monday_utc,
        ):
            result = mgr.preferred_window()

        assert result is not None
        weekday, hour = result
        assert weekday == 0, "Should be Monday (weekday=0)"

    def test_preferred_window_no_results_for_different_day(self):
        """Events recorded on Monday should not appear for Tuesday."""
        mgr = _make_manager_v250_presence()

        monday_utc = _recent_monday()
        for _ in range(6):
            mgr.record_clean_event(monday_utc)

        # Mock dt_util.now() to return Tuesday (day after the Monday)
        mock_tuesday = monday_utc + timedelta(days=1)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.now",
            return_value=mock_tuesday,
        ):
            result = mgr.preferred_window()

        # Tuesday has no events, so result should be None
        assert result is None


class TestRecordCleanEventPrune:
    def test_prune_not_triggered_below_threshold(self):
        """Below 500 total events, prune loop should not run."""
        mgr = _make_manager_v250_presence()

        # Add 10 old events (older than 90 days)
        old_dt = datetime_v250_presence(2020, 1, 1, 10, 0, tzinfo=UTC)
        for _ in range(10):
            mgr.record_clean_event(old_dt)

        # Total is 10 — well below 500. Old events must NOT be pruned.
        total = sum(len(v) for v in mgr._clean_events.values())
        assert total == 10, "Should have 10 events — prune threshold not reached"

    def test_prune_triggered_above_threshold(self):
        """Above 500 total events, old events (> 90 days) must be pruned."""
        mgr = _make_manager_v250_presence()

        # Fill up to 501 events, half old, half recent
        old_dt = datetime_v250_presence(2020, 1, 1, 10, 0, tzinfo=UTC)
        recent_dt = datetime_v250_presence(2026, 5, 1, 10, 0, tzinfo=UTC)

        # 250 old events across different slots
        for i in range(250):
            slot_dt = old_dt.replace(hour=i % 24)
            mgr._clean_events[(i % 7, i % 24)].append(slot_dt)

        # 251 recent events to push over 500
        for i in range(251):
            slot_dt = recent_dt.replace(hour=i % 24)
            mgr._clean_events[(i % 7, i % 24)].append(slot_dt)

        assert sum(len(v) for v in mgr._clean_events.values()) == 501

        # Trigger via record_clean_event (total=501 > 500 triggers prune)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.utcnow",
            return_value=datetime_v250_presence(2026, 6, 1, 12, 0, tzinfo=UTC),
        ):
            mgr.record_clean_event(recent_dt)

        # Old events should have been pruned; recent ones preserved (plus the new one)
        remaining = sum(len(v) for v in mgr._clean_events.values())
        assert remaining <= 252, f"Expected at most 252 recent events, got {remaining}"
        assert remaining >= 251, "Recent events must be preserved"


class TestPresenceWindowsALG1:

    def _make_manager(self, person_ids: list[str], states: dict[str, str]):
        """Build a PresenceManager with mocked hass.states."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        from custom_components.roomba_plus.const import CONF_PRESENCE_ENTITIES

        entry = MagicMock()
        entry.options = {CONF_PRESENCE_ENTITIES: person_ids}
        entry.runtime_data = MagicMock()

        hass = MagicMock()
        hass.states.get = lambda eid: (
            MagicMock(state=states.get(eid, "home")) if eid in states else None
        )
        return PresenceManager(hass, entry)

    def test_record_clean_event_stores_tuple(self):
        """record_clean_event stores (datetime_v260_learning, was_all_away) tuples."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        dt = _utcnow()
        pm.record_clean_event(dt)
        all_events = [item for items in pm._clean_events.values() for item in items]
        assert len(all_events) == 1
        item = all_events[0]
        assert isinstance(item, tuple)
        assert isinstance(item[0], datetime_v260_learning)
        assert item[1] is True  # person was away

    def test_record_clean_event_away_false_when_home(self):
        """was_all_away=False when a person is home."""
        pm = self._make_manager(["person.alice"], {"person.alice": "home"})
        pm.record_clean_event(_utcnow())
        events = [i for items in pm._clean_events.values() for i in items]
        assert events[0][1] is False

    def test_presence_windows_scores_away_fraction(self):
        """presence_windows returns away_count/total_count per slot."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        # Seed 5 events: 3 away, 2 home — all in same slot
        now = _utcnow()
        slot_dt = now.replace(hour=9)
        from homeassistant.util import dt as dt_util
        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            mock_dt.as_local.return_value = slot_dt
            mock_dt.now.return_value = now
            for away in [True, True, True, False, False]:
                pm._clean_events[(slot_dt.weekday(), 9)].append((slot_dt, away))

        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            windows = pm.presence_windows()

        # Should have one slot with score 3/5 = 0.6
        assert len(windows) == 1
        score = list(windows.values())[0]
        assert abs(score - 0.6) < 0.01

    def test_presence_windows_slot_with_fewer_than_3_excluded(self):
        """Slots with < 3 recent events are excluded from windows."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        now = _utcnow()
        # Only 2 events — should be excluded
        slot_dt = now.replace(hour=10)
        for _ in range(2):
            pm._clean_events[(slot_dt.weekday(), 10)].append((slot_dt, True))

        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            windows = pm.presence_windows()

        assert windows == {}

    def test_presence_windows_empty_below_5_total(self):
        """Returns {} when fewer than 5 total events recorded."""
        pm = self._make_manager(["person.alice"], {"person.alice": "not_home"})
        now = _utcnow()
        for _ in range(4):
            pm._clean_events[(0, 9)].append((_utcnow(), True))
        with patch("custom_components.roomba_plus.presence_manager.dt_util") as mock_dt:
            mock_dt.utcnow.return_value = now
            result = pm.presence_windows()
        assert result == {}


class TestWindowIsToday:

    def test_window_is_today_true(self):
        """window_is_today returns True when preferred_window is today."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = MagicMock(spec=PresenceManager)
        today = datetime_v260_learning.now().weekday()
        pm.preferred_window.return_value = (today, 10)
        pm.window_is_today = PresenceManager.window_is_today.fget(pm)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util"
        ) as mock_dt:
            mock_dt.now.return_value = MagicMock(weekday=lambda: today)
            result = PresenceManager.window_is_today.fget(pm)
        assert result is True

    def test_window_is_today_false_when_none(self):
        """window_is_today returns False when no preferred window."""
        from custom_components.roomba_plus.presence_manager import PresenceManager
        pm = MagicMock(spec=PresenceManager)
        pm.preferred_window.return_value = None
        result = PresenceManager.window_is_today.fget(pm)
        assert result is False


class TestGateBlocked:

    def _make_dtm(self, options: dict, state_overrides: dict | None = None):
        from custom_components.roomba_plus.dirt_threshold_manager import DirtThresholdManager
        entry = MagicMock()
        entry.options = {"demand_cleaning_enabled": True, **options}
        data = MagicMock()
        reported = {"cleanMissionStatus": {"cycle": "none"}, **(state_overrides or {})}
        data.roomba_reported_state.return_value = reported
        data.presence_manager = None
        data.blocking_manager = None
        entry.runtime_data = data
        dtm = DirtThresholdManager.__new__(DirtThresholdManager)
        dtm._entry = entry
        dtm._hass = MagicMock()
        return dtm

    def test_gate_blocked_false_when_all_clear(self):
        dtm = self._make_dtm({})
        blocked, reason = dtm.gate_blocked()
        assert blocked is False
        assert reason == ""

    def test_gate_blocked_true_when_disabled(self):
        dtm = self._make_dtm({})
        dtm._entry.options["demand_cleaning_enabled"] = False
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert reason == "demand_cleaning_disabled"

    def test_gate_blocked_true_when_robot_busy(self):
        dtm = self._make_dtm(
            {},
            {"cleanMissionStatus": {"cycle": "quick"}}
        )
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert "robot_busy" in reason

    def test_gate_blocked_true_when_blocking_manager_queued(self):
        dtm = self._make_dtm({})
        dtm._entry.runtime_data.blocking_manager = MagicMock()
        dtm._entry.runtime_data.blocking_manager.is_queued = True
        blocked, reason = dtm.gate_blocked()
        assert blocked is True
        assert reason == "blocking_sensor_queued"


class TestPresenceUnavailable:
    """PM: unavailable/unknown person entities treated as 'might be home'."""

    def _all_away(self, states: dict) -> bool:
        from custom_components.roomba_plus.presence_manager import (
            _HOME_STATES, _PRESENCE_UNUSABLE
        )
        return all(
            (st := states.get(eid)) is not None
            and st not in _PRESENCE_UNUSABLE
            and st not in _HOME_STATES
            for eid in states
        )

    def test_away_state_triggers_all_away(self):
        assert self._all_away({"person.alice": "away"}) is True

    def test_home_state_blocks_all_away(self):
        assert self._all_away({"person.alice": "home"}) is False

    def test_unavailable_state_blocks_all_away(self):
        """unavailable → might be home → NOT all_away."""
        assert self._all_away({"person.alice": "unavailable"}) is False

    def test_unknown_state_blocks_all_away(self):
        """unknown → might be home → NOT all_away."""
        assert self._all_away({"person.alice": "unknown"}) is False

    def test_mixed_away_and_unavailable_blocks(self):
        """One away + one unavailable → NOT all_away (safe default)."""
        assert self._all_away({
            "person.alice": "away",
            "person.bob": "unavailable",
        }) is False
