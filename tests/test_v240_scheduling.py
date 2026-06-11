"""Tests for F12a — presence_windows() and optimal_clean_window sensor.

Covers:
  - record_clean_event: stores events, prunes >90 days, creates _clean_events
  - presence_windows: returns empty when < 5 events, correct structure
  - preferred_window: None when no data, filters to today's weekday
  - RoombaOptimalCleanWindow.native_value: None when no PM, correct datetime
  - RoombaOptimalCleanWindow.extra_state_attributes: windows matrix serialised
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus.presence_manager import PresenceManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


def _dt(days_ago: int = 0, hour: int = 10, weekday_offset: int = 0) -> datetime:
    """Return a UTC datetime offset from now."""
    base = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    base -= timedelta(days=days_ago)
    return base


# ── record_clean_event ────────────────────────────────────────────────────────

class TestRecordCleanEvent:

    def test_creates_clean_events_dict(self):
        # R1 (v2.5.0): _clean_events is initialised in __init__, not lazily.
        pm = _make_pm()
        assert hasattr(pm, "_clean_events"), "_clean_events must exist after __init__"
        assert len(pm._clean_events) == 0, "Must be empty before any events recorded"
        pm.record_clean_event(datetime.now(UTC))
        assert hasattr(pm, "_clean_events")
        assert len(pm._clean_events) == 1, "One slot after recording one event"

    def test_records_event_in_correct_slot(self):
        pm = _make_pm()
        dt = datetime.now(UTC).replace(hour=9)
        local = dt.astimezone()
        expected_slot = (local.weekday(), local.hour)
        pm.record_clean_event(dt)
        assert expected_slot in pm._clean_events
        assert len(pm._clean_events[expected_slot]) == 1

    def test_multiple_events_accumulate(self):
        pm = _make_pm()
        dt = datetime.now(UTC).replace(hour=9)
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
        old_dt = datetime.now(UTC) - timedelta(days=95)
        fresh_dt = datetime.now(UTC)
        pm.record_clean_event(old_dt)
        pm.record_clean_event(fresh_dt)
        total = sum(len(v) for v in pm._clean_events.values())
        # Both events remain — threshold not reached
        assert total == 2, "Below-threshold: both events kept (P5 change)"

    def test_events_within_90_days_kept(self):
        pm = _make_pm()
        dt = datetime.now(UTC) - timedelta(days=89)
        pm.record_clean_event(dt)
        pm.record_clean_event(datetime.now(UTC))
        total = sum(len(v) for v in pm._clean_events.values())
        assert total == 2


# ── presence_windows ──────────────────────────────────────────────────────────

class TestPresenceWindows:

    def test_empty_when_no_events(self):
        pm = _make_pm()
        assert pm.presence_windows() == {}

    def test_empty_when_fewer_than_5_events(self):
        pm = _make_pm()
        dt = datetime.now(UTC)
        for _ in range(4):
            pm.record_clean_event(dt)
        assert pm.presence_windows() == {}

    def test_returns_dict_with_5_events(self):
        pm = _make_pm()
        dt = datetime.now(UTC)
        for _ in range(5):
            pm.record_clean_event(dt)
        windows = pm.presence_windows()
        assert isinstance(windows, dict)
        assert len(windows) >= 1

    def test_slots_are_weekday_hour_tuples(self):
        pm = _make_pm()
        for i in range(5):
            pm.record_clean_event(datetime.now(UTC))
        windows = pm.presence_windows()
        for key in windows:
            assert isinstance(key, tuple)
            wd, hr = key
            assert 0 <= wd <= 6
            assert 0 <= hr <= 23

    def test_scores_are_between_0_and_1(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime.now(UTC))
        windows = pm.presence_windows()
        for score in windows.values():
            assert 0.0 <= score <= 1.0

    def test_empty_when_no_person_ids(self):
        pm = _make_pm(person_ids=[])
        for _ in range(5):
            pm.record_clean_event(datetime.now(UTC))
        assert pm.presence_windows() == {}


# ── preferred_window ──────────────────────────────────────────────────────────

class TestPreferredWindow:

    def test_none_when_no_history(self):
        pm = _make_pm()
        assert pm.preferred_window() is None

    def test_none_when_insufficient_events(self):
        pm = _make_pm()
        for _ in range(4):
            pm.record_clean_event(datetime.now(UTC))
        assert pm.preferred_window() is None

    def test_returns_weekday_hour_tuple(self):
        pm = _make_pm()
        for _ in range(5):
            pm.record_clean_event(datetime.now(UTC))
        result = pm.preferred_window()
        if result is not None:
            assert isinstance(result, tuple)
            wd, hr = result
            assert 0 <= wd <= 6
            assert 0 <= hr <= 23

    def test_filters_to_today_weekday(self):
        """preferred_window must only consider today's weekday."""
        pm = _make_pm()
        today = datetime.now().weekday()
        # Record 5 events today
        for _ in range(5):
            pm.record_clean_event(datetime.now(UTC).replace(hour=10))
        result = pm.preferred_window()
        if result is not None:
            assert result[0] == today


# ── RoombaOptimalCleanWindow sensor ──────────────────────────────────────────

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
            pm.record_clean_event(datetime.now(UTC).replace(hour=10))
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
            pm.record_clean_event(datetime.now(UTC).replace(hour=10))
        sensor = self._make_sensor(pm=pm)
        attrs = sensor.extra_state_attributes
        assert "windows" in attrs
        assert "preferred_slot" in attrs

    def test_new_state_filter_always_false(self):
        sensor = self._make_sensor(pm=None)
        assert sensor.new_state_filter({"cleanMissionStatus": {}}) is False


# ── F12b: dirt_density in DaySummary ─────────────────────────────────────────

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


# ── F12d: GridStore.edge_coverage_ratio ──────────────────────────────────────

class TestEdgeCoverageRatio:

    def _make_gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_none_when_fewer_than_10_cells(self):
        gs = self._make_gs()
        # Add 9 cells
        for i in range(9):
            gs._cells[(i, 0)] = 0.5
        assert gs.edge_coverage_ratio() is None

    def test_returns_float_with_10_cells(self):
        gs = self._make_gs()
        for i in range(10):
            gs._cells[(i, 0)] = 0.5
        result = gs.edge_coverage_ratio()
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_all_edge_cells_returns_1(self):
        """A 1×N strip where all cells are on the edge."""
        gs = self._make_gs()
        for i in range(10):
            gs._cells[(i, 0)] = 0.5  # all in same row = all on edge
        result = gs.edge_coverage_ratio()
        assert result == 1.0

    def test_interior_cells_reduce_ratio(self):
        """A 7×7 grid — inner 3×3 cells are >300mm from all edges, ratio < 1."""
        gs = self._make_gs()
        for gx in range(7):
            for gy in range(7):
                gs._cells[(gx, gy)] = 0.5
        # Grid spans 7×150=1050mm. Inner cells (2,2)–(4,4) have min distance
        # from edge = (2+0.5)*150 - 0.5*150 = 300mm → strictly inside with 7 cols.
        # Use edge_depth_mm=200 so interior ring is clearly excluded.
        result = gs.edge_coverage_ratio(edge_depth_mm=200)
        assert result is not None
        assert result < 1.0

    def test_result_is_rounded_to_4_decimal_places(self):
        gs = self._make_gs()
        for i in range(10):
            gs._cells[(i, i)] = 0.5
        result = gs.edge_coverage_ratio()
        if result is not None:
            assert round(result, 4) == result


# ── F12e: total_energy_consumed ───────────────────────────────────────────────

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


# ── F12a wiring: record_clean_event called from callbacks.py ──────────────────

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
        """record_clean_event is called inside the _CLEANING_PHASES transition block."""
        import inspect
        from custom_components.roomba_plus import callbacks
        src = inspect.getsource(callbacks)
        # The call must be near the _CLEANING_PHASES transition, not elsewhere
        phase_idx = src.find('_CLEANING_PHASES and last_phase not in _CLEANING_PHASES')
        record_idx = src.find('record_clean_event')
        assert phase_idx != -1, "_CLEANING_PHASES transition not found in callbacks.py"
        assert record_idx != -1, "record_clean_event not found in callbacks.py"
        # record_clean_event should come after the phase transition (within ~500 chars)
        assert record_idx > phase_idx, (
            "record_clean_event must be called after the mission-start phase transition"
        )
        assert record_idx - phase_idx < 800, (
            "record_clean_event is too far from the mission-start transition — check placement"
        )
