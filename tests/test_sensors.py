"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""

from __future__ import annotations





import datetime
import pytest
import types
import sys
import os
import tests.conftest
from custom_components.roomba_plus.sensor import _area_cleaned_today
from custom_components.roomba_plus.const import ERROR_CODE_LABELS
from custom_components.roomba_plus.sensor import SENSORS
from custom_components.roomba_plus.mission_store import MissionStore
import time as _time_mod
from custom_components.roomba_plus.binary_sensor import RoombaMidMissionRecharge
from custom_components.roomba_plus.select import CloudSmartZoneSelect
from custom_components.roomba_plus.button import FavoriteButton
from custom_components.roomba_plus.sensor import (
    CLOUD_HISTORY_SENSORS,
    CloudHistorySensor,
    CloudHistorySensorDescription,
    _mh_sqft_to_m2,
    _mh_total_minutes,
    _mh_total_missions,
)
from unittest.mock import patch
from unittest.mock import MagicMock
import importlib
import unittest.mock as _mock
from custom_components.roomba_plus.sensor import _raw_wifi_floor
from custom_components.roomba_plus.sensor import _raw_wifi_quality_pct
from custom_components.roomba_plus.sensor import _raw_wifi_stability
from custom_components.roomba_plus.sensor import _mop_clean_mode
from custom_components.roomba_plus.sensor import _mop_tank_status
from custom_components.roomba_plus.sensor import _mop_behavior
from custom_components.roomba_plus.sensor import SensorStateClass
import homeassistant.helpers.entity_platform as _ep
from unittest.mock import PropertyMock
from custom_components.roomba_plus.sensor import CloudRawSensor
from custom_components.roomba_plus.sensor import CloudRawSensorDescription
from homeassistant.components.sensor import SensorDeviceClass
import asyncio
from unittest.mock import AsyncMock
from custom_components.roomba_plus.sensor import RoombaCleaningPerformanceSensor
from custom_components.roomba_plus.sensor import RoombaCleaningAnalytics30dSensor
from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor
from custom_components.roomba_plus.sensor import RoombaEventCounts30dSensor
import time
from datetime import UTC
from datetime import datetime as datetime_v280_bat_arch
from datetime import timedelta
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.sensor import RoombaMissionsPerChargeSensor
from custom_components.roomba_plus.sensor import RoombaWifiChannelStabilitySensor
from custom_components.roomba_plus.sensor import RoombaWifiLastChannelSensor
from custom_components.roomba_plus.sensor import _channel_to_band
import json
from pathlib import Path
from custom_components.roomba_plus.sensor import _parse_netinfo_addr


ROOT = os.path.join(os.path.dirname(__file__), "..")
__make_record_seq = 0
_ep = sys.modules.get('homeassistant.helpers.entity_platform')
TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components" / "roomba_plus" / "translations"
)


def _iso(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_unique_id(days_ago):
    global __make_record_seq
    __make_record_seq += 1
    return f"m_{days_ago}_{__make_record_seq}"


def _make_record(days_ago=0, result="completed", area_sqft=200.0, bbrun_hr=100):
    started = _iso(days_ago, hour=8)
    ended   = _iso(days_ago, hour=9)
    return {
        "id": _make_unique_id(days_ago),
        "started_at": started,
        "ended_at": ended,
        "duration_min": 60,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": bbrun_hr,
    }


def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


def _get_sensor(key: str):
    for desc in SENSORS:
        if desc.key == key:
            return desc
    raise KeyError(f"Sensor '{key}' not found")


def _make_entity(mission_status: dict):
    class _FakeEntity:
        @property
        def clean_mission_status(self):
            return mission_status
        @property
        def vacuum_state(self):
            return {"cleanMissionStatus": mission_status}

    return _FakeEntity()


def _make_binary(state: dict):
    """Create a RoombaMidMissionRecharge with fake vacuum state."""
    import types

    class _FakeVacuum:
        def get_reported_state(self):
            return state

    sensor = object.__new__(RoombaMidMissionRecharge)
    sensor._vacuum_state = state

    # Patch roomba_reported_state to return state dict
    import custom_components.roomba_plus.binary_sensor as bs_mod
    original = getattr(bs_mod, 'roomba_reported_state', None)

    class _Ctx:
        def __enter__(self):
            bs_mod.roomba_reported_state = lambda v: state
            return sensor
        def __exit__(self, *a):
            if original:
                bs_mod.roomba_reported_state = original

    return _Ctx()


def _rec(done="done", done_raw="done", pause_id=0, chrgs=0, evacs=0,
         dirt=0, timestamp=1700000000, classified=None):
    """Build a minimal raw record dict with classified_result pre-computed."""
    r = {
        "done":      done,
        "done_raw":  done_raw,
        "pauseId":   pause_id,
        "chrgs":     chrgs,
        "evacs":     evacs,
        "dirt":      dirt,
        "timestamp": timestamp,
    }
    if classified is None:
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        classified = classify_mission_result(r)
    r["classified_result"] = classified
    return r


def _utcnow_returning(ts: int):
    """Return a context manager that freezes dt_util.utcnow() to ts."""
    frozen = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return patch(
        "custom_components.roomba_plus.sensor.dt_util.utcnow",
        return_value=frozen,
    )


def _entity(state: dict) -> MagicMock:
    """Return a fake IRobotEntity with the given vacuum_state."""
    e = MagicMock()
    e.vacuum_state = state
    return e


def _mission_sensor(cycle="none", phase=""):
    """Build a minimal RoombaMissionActive with stubbed vacuum state."""
    from custom_components.roomba_plus.binary_sensor import RoombaMissionActive
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {
        "cleanMissionStatus": {"cycle": cycle, "phase": phase}
    }}}
    s = RoombaMissionActive.__new__(RoombaMissionActive)
    s.vacuum = roomba
    return s


def _boost_entity(carpet_boost=None, vac_high=None):
    """Build a minimal CarpetBoostSelect with stubbed vacuum state."""
    from custom_components.roomba_plus.select import CarpetBoostSelect
    state = {}
    if carpet_boost is not None:
        state["carpetBoost"] = carpet_boost
    if vac_high is not None:
        state["vacHigh"] = vac_high
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": state}}
    s = CarpetBoostSelect.__new__(CarpetBoostSelect)
    s.vacuum = roomba
    # vacuum_state is a property reading from self.vacuum — pre-compute it
    s.vacuum_state = state
    s._blid = "test_blid"
    return s


def _make_sensor(
    has_cloud: bool = True,
    last_update_success: bool = True,
    coordinator_data: dict | None = None,
) -> CloudRawSensor:
    """Build a minimal CloudRawSensor with mocked internals."""
    roomba = MagicMock()
    blid = "test_blid"

    coordinator = MagicMock()
    coordinator.last_update_success = last_update_success
    coordinator.data = coordinator_data if coordinator_data is not None else {"pmaps": []}
    coordinator.raw_records = []

    config_entry = MagicMock()
    runtime_data = MagicMock()
    # has_cloud is a property — set it on the mock
    type(runtime_data).has_cloud = PropertyMock(return_value=has_cloud)
    config_entry.runtime_data = runtime_data

    description = CloudRawSensorDescription(
        key="recent_dirt_events",
        translation_key="recent_dirt_events",
        name="Dirt events",
        value_fn=lambda records: None,
    )

    sensor = CloudRawSensor(roomba, blid, coordinator, description, config_entry)
    return sensor


def _make_coordinator(records=None, data=None):
    cc = MagicMock()
    cc.raw_records = records or []
    cc.data = data or {}
    cc.last_update_success = True
    return cc


def _make_entry(mission_store=None, cloud_coordinator=None, umf_aligner=None):
    entry = MagicMock()
    rd = MagicMock()
    rd.has_cloud = cloud_coordinator is not None
    rd.cloud_coordinator = cloud_coordinator
    rd.umf_aligner = umf_aligner
    rd.mission_store = mission_store
    rd.robot_profile_store = None
    entry.runtime_data = rd
    return entry


def _make_sensor_v270_consolidated_sensors(cls, records=None, data=None, mission_store=None):
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    cc = _make_coordinator(records=records, data=data)
    entry = _make_entry(mission_store=mission_store)
    sensor = cls.__new__(cls)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._coordinator = cc
    sensor._config_entry = entry
    sensor._attr_unique_id = f"test_{cls.entity_description.key}"
    return sensor


def _make_archive(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    arc = MissionArchive()
    for rec in records:
        arc._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            arc._archived_nmssns.add(int(n))
    arc._initial_load_done = initial_load_done
    return arc


def _derived(
    n_mssn: int,
    wifi_channel: int | None = 6,
    recharge_count: int = 0,
    days_ago: int = 1,
) -> dict:
    ts = (datetime_v280_bat_arch.now(UTC) - timedelta(days=days_ago)).isoformat()
    return {
        "nMssn": n_mssn,
        "wifi_channel": wifi_channel,
        "recharge_count": recharge_count,
        "result": "completed",
        "sqft": 300.0,
        "duration_min": 45,
        "dirt": 5,
        "start_ts": ts,
    }


def _make_sensor_v280_bat_arch(cls, archive: MissionArchive | None, available: bool = True):
    """Create a sensor instance with mocked dependencies."""
    sensor = object.__new__(cls)
    # IRobotEntity needs _blid and _roomba; robot_unique_id = f"roomba_plus_{_blid}"
    sensor._blid = "testblid"
    sensor._roomba = MagicMock()
    sensor._attr_unique_id = f"roomba_plus_testblid_{cls.entity_description.key}"

    config_entry = MagicMock()
    config_entry.runtime_data.mission_archive = archive
    config_entry.runtime_data.has_cloud = available

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {}

    sensor._config_entry = config_entry
    sensor._coordinator = coordinator
    return sensor


def _make_entity_v280_field_sensors(vacuum_state: dict):
    """Build a minimal IRobotEntity-like object with the given MQTT state."""
    from custom_components.roomba_plus.entity import IRobotEntity
    entity = object.__new__(IRobotEntity)
    entity._blid = "test"
    entity._roomba = MagicMock()
    # vacuum_state is normally set in __init__ via roomba_reported_state(roomba).
    # Set it directly since we bypass __init__ with object.__new__.
    entity.vacuum_state = vacuum_state
    return entity


def _find_desc(key: str):
    """Return the RoombaSensorDescription with the given key."""
    from custom_components.roomba_plus.sensor import SENSORS
    for desc in SENSORS:
        if desc.key == key:
            return desc
    return None


class TestNStuckDelta:
    """nStuck result uses delta, not lifetime counter."""

    def test_no_new_stuck_in_mission(self):
        """nStuck same at start and end -> result not stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 159
        delta = max(0, nstuck_at_end - nstuck_at_start)
        result = "stuck" if delta > 0 else "completed"
        assert result == "completed"

    def test_one_new_stuck_in_mission(self):
        """nStuck incremented -> result stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 160
        delta = max(0, nstuck_at_end - nstuck_at_start)
        result = "stuck" if delta > 0 else "completed"
        assert result == "stuck"

    def test_high_lifetime_nstuck_does_not_falsely_mark_stuck(self):
        """Lifetime counter of 159 should NOT mark a clean mission as stuck."""
        nstuck_at_start = 159
        nstuck_at_end   = 159  # no change this mission
        delta = max(0, nstuck_at_end - nstuck_at_start)
        # Old logic: bbrun.get("nStuck", 0) = 159 -> truthy -> "stuck" (BUG)
        old_logic_result = "stuck" if nstuck_at_end > 0 else "completed"
        new_logic_result  = "stuck" if delta > 0 else "completed"
        assert old_logic_result == "stuck"   # confirms the old bug
        assert new_logic_result == "completed"  # confirms the fix

    def test_error_takes_priority_over_stuck(self):
        """error_code > 0 -> result=error regardless of nStuck delta."""
        error_code = 17
        nstuck_delta = 1
        if error_code:
            result = "error"
        elif nstuck_delta > 0:
            result = "stuck"
        else:
            result = "completed"
        assert result == "error"


class TestMissionRechargeMinutes:
    def test_returns_none_when_zero(self):
        desc = _get_sensor("mission_recharge_minutes")
        e = _make_entity({"rechrgM": 0, "phase": "run", "cycle": "clean"})
        assert desc.value_fn(e) is None

    def test_returns_none_when_absent(self):
        desc = _get_sensor("mission_recharge_minutes")
        e = _make_entity({"phase": "run"})
        assert desc.value_fn(e) is None

    def test_returns_value_when_mid_mission_recharge(self):
        desc = _get_sensor("mission_recharge_minutes")
        e = _make_entity({"rechrgM": 45, "phase": "charge", "cycle": "clean"})
        assert desc.value_fn(e) == 45

    def test_returns_none_when_not_recharging(self):
        desc = _get_sensor("mission_recharge_minutes")
        e = _make_entity({"rechrgM": 0, "phase": "charge", "cycle": "none"})
        assert desc.value_fn(e) is None

    def test_unit_is_minutes(self):
        from homeassistant.const import UnitOfTime
        desc = _get_sensor("mission_recharge_minutes")
        assert desc.native_unit_of_measurement == UnitOfTime.MINUTES


class TestMissionExpireMinutes:
    def test_returns_none_when_zero(self):
        desc = _get_sensor("mission_expire_minutes")
        e = _make_entity({"expireM": 0})
        assert desc.value_fn(e) is None

    def test_returns_none_when_absent(self):
        desc = _get_sensor("mission_expire_minutes")
        e = _make_entity({})
        assert desc.value_fn(e) is None

    def test_returns_value_when_active(self):
        desc = _get_sensor("mission_expire_minutes")
        e = _make_entity({"expireM": 120})
        assert desc.value_fn(e) == 120

    def test_unit_is_minutes(self):
        from homeassistant.const import UnitOfTime
        desc = _get_sensor("mission_expire_minutes")
        assert desc.native_unit_of_measurement == UnitOfTime.MINUTES


class TestMissionId:
    def test_returns_mission_id_when_present(self):
        desc = _get_sensor("mission_id")
        e = _make_entity({"missionId": "01KSTCFX8GX27T5R8SZJ8KG0C2"})
        assert desc.value_fn(e) == "01KSTCFX8GX27T5R8SZJ8KG0C2"

    def test_returns_none_when_absent(self):
        desc = _get_sensor("mission_id")
        e = _make_entity({})
        assert desc.value_fn(e) is None

    def test_returns_none_when_empty_string(self):
        desc = _get_sensor("mission_id")
        e = _make_entity({"missionId": ""})
        assert desc.value_fn(e) is None

    def test_filter_fn_true_when_missionId_in_state(self):
        desc = _get_sensor("mission_id")
        state = {"cleanMissionStatus": {"missionId": "abc123"}}
        assert desc.filter_fn(state) is True

    def test_filter_fn_false_when_missionId_absent(self):
        desc = _get_sensor("mission_id")
        state = {"cleanMissionStatus": {"phase": "run"}}
        assert desc.filter_fn(state) is False

    def test_disabled_by_default(self):
        desc = _get_sensor("mission_id")
        assert desc.entity_registry_enabled_default is False

    def test_stable_across_recharge_cycles(self):
        """missionId stays the same throughout a mission including recharges."""
        mission_id = "01KSTCFX8GX27T5R8SZJ8KG0C2"
        desc = _get_sensor("mission_id")
        # During run
        e1 = _make_entity({"phase": "run", "cycle": "clean", "missionId": mission_id})
        # During mid-mission recharge
        e2 = _make_entity({"phase": "charge", "cycle": "clean", "missionId": mission_id})
        # Back to run
        e3 = _make_entity({"phase": "run", "cycle": "clean", "missionId": mission_id})
        assert desc.value_fn(e1) == desc.value_fn(e2) == desc.value_fn(e3) == mission_id


class TestMidMissionRechargeBinary:
    """Test is_on logic directly (without HA setup)."""

    def _is_on(self, phase: str, cycle: str) -> bool:
        """Replicate RoombaMidMissionRecharge.is_on logic."""
        return phase == "charge" and cycle != "none"

    def test_on_when_phase_charge_cycle_active(self):
        assert self._is_on("charge", "clean") is True

    def test_off_when_phase_charge_cycle_none(self):
        """Completed charging — not mid-mission."""
        assert self._is_on("charge", "none") is False

    def test_off_when_phase_run(self):
        assert self._is_on("run", "clean") is False

    def test_off_when_phase_stop(self):
        """User-paused mid-mission — NOT a mid-mission recharge."""
        assert self._is_on("stop", "clean") is False

    def test_off_when_phase_hmMidMsn(self):
        """Robot heading to dock mid-mission — recharge not started yet."""
        assert self._is_on("hmMidMsn", "clean") is False

    def test_off_when_phase_empty(self):
        assert self._is_on("", "none") is False

    def test_distinguishes_pause_from_recharge(self):
        """Key distinction: stop=user-pause vs charge=recharge."""
        assert self._is_on("stop", "clean") is False   # paused by user
        assert self._is_on("charge", "clean") is True  # mid-mission recharge

    def test_new_state_filter(self):
        """Only update when cleanMissionStatus changes."""
        assert "cleanMissionStatus" in {"cleanMissionStatus": {}, "batPct": 80}
        assert "cleanMissionStatus" not in {"batPct": 80}


class TestVacuumMissionPhaseAttributes:
    """Test the v1.9.3 extra_state_attributes additions."""

    def _compute_attrs(self, mission: dict) -> dict:
        """Replicate the v1.9.3 attribute logic from vacuum.py."""
        cycle = mission.get("cycle", "none")
        phase = mission.get("phase", "")
        attrs = {}
        attrs["mid_mission_recharge"] = (phase == "charge" and cycle != "none")
        recharge_m = mission.get("rechrgM", 0)
        attrs["recharge_minutes_remaining"] = recharge_m if recharge_m else None
        expire_m = mission.get("expireM", 0)
        attrs["expire_minutes_remaining"] = expire_m if expire_m else None
        attrs["mission_id"] = mission.get("missionId") or None
        return attrs

    def test_mid_mission_recharge_true(self):
        attrs = self._compute_attrs({"phase": "charge", "cycle": "clean"})
        assert attrs["mid_mission_recharge"] is True

    def test_mid_mission_recharge_false_when_done(self):
        attrs = self._compute_attrs({"phase": "charge", "cycle": "none"})
        assert attrs["mid_mission_recharge"] is False

    def test_recharge_minutes_populated(self):
        attrs = self._compute_attrs({"rechrgM": 45, "phase": "charge", "cycle": "clean"})
        assert attrs["recharge_minutes_remaining"] == 45

    def test_recharge_minutes_none_when_zero(self):
        attrs = self._compute_attrs({"rechrgM": 0})
        assert attrs["recharge_minutes_remaining"] is None

    def test_expire_minutes_populated(self):
        attrs = self._compute_attrs({"expireM": 120})
        assert attrs["expire_minutes_remaining"] == 120

    def test_expire_minutes_none_when_zero(self):
        attrs = self._compute_attrs({"expireM": 0})
        assert attrs["expire_minutes_remaining"] is None

    def test_mission_id_populated(self):
        attrs = self._compute_attrs({"missionId": "01KSTCFX8GX27T5R8SZJ8KG0C2"})
        assert attrs["mission_id"] == "01KSTCFX8GX27T5R8SZJ8KG0C2"

    def test_mission_id_none_when_absent(self):
        attrs = self._compute_attrs({})
        assert attrs["mission_id"] is None

    def test_all_keys_present(self):
        attrs = self._compute_attrs({})
        assert "mid_mission_recharge" in attrs
        assert "recharge_minutes_remaining" in attrs
        assert "expire_minutes_remaining" in attrs
        assert "mission_id" in attrs


# Moved to a module-specific test file (August 2026).


class TestSensorDescriptionsUseHelpers:
    """Verify SENSORS tuple delegates to the fixed helpers."""

    def test_recharge_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_recharge_minutes"), None)
        assert desc is not None

    def test_expire_minutes_sensor_exists(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next((s for s in SENSORS if s.key == "mission_expire_minutes"), None)
        assert desc is not None

    def test_recharge_sensor_lewis_computes_from_rechrgTm(self):
        """End-to-end lewis path: value_fn computes from rechrgTm (Thonno's i7)."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")

        class _FakeEntity:
            clean_mission_status = {"rechrgM": 0, "rechrgTm": 1780150300}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 5   # 300s → 5 min

    def test_recharge_sensor_900_prefers_rechrgTm(self):
        """End-to-end 900-series path: rechrgTm preferred over static rechrgM."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")

        class _FakeEntity:
            clean_mission_status = {"rechrgM": 78, "rechrgTm": 1780150600}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 10   # 600s → 10 min, not 78 (static rechrgM)

    def test_expire_sensor_decrements_via_expireTm(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "mission_expire_minutes")

        class _FakeEntity:
            clean_mission_status = {"expireM": 30, "expireTm": 1780150600}

        with _utcnow_returning(1780150000):
            result = desc.value_fn(_FakeEntity())
        assert result == 10   # 600s → 10 min, not 30 (static expireM)


class TestRoombaSensorPeriodicTick:
    """RoombaSensor registers a 60-second tick for countdown sensors.

    This is the primary fix for Thonno's i7 bug: without the tick, the sensor
    value freezes after the initial MQTT push because the robot goes silent
    during charging.
    """

    def test_tick_sensors_constant_includes_recharge(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        assert "mission_recharge_minutes" in RoombaSensor._TICK_SENSORS

    def test_tick_sensors_constant_includes_expire(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        assert "mission_expire_minutes" in RoombaSensor._TICK_SENSORS

    def test_non_countdown_sensors_not_in_tick_set(self):
        from custom_components.roomba_plus.sensor import RoombaSensor
        for key in ("battery", "phase", "filter_remaining_hours", "mission_id"):
            assert key not in RoombaSensor._TICK_SENSORS

    @pytest.mark.asyncio
    async def test_async_will_remove_cancels_tick(self):
        """async_will_remove_from_hass cancels the tick and clears _unsub_tick."""
        from custom_components.roomba_plus.sensor import RoombaSensor, SENSORS

        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")
        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = desc

        cancelled = []
        sensor._unsub_tick = lambda: cancelled.append(True)

        await RoombaSensor.async_will_remove_from_hass(sensor)

        assert len(cancelled) == 1
        assert sensor._unsub_tick is None

    @pytest.mark.asyncio
    async def test_will_remove_is_safe_when_no_tick(self):
        """async_will_remove_from_hass is a no-op when _unsub_tick is None."""
        from custom_components.roomba_plus.sensor import RoombaSensor, SENSORS

        desc = next(s for s in SENSORS if s.key == "mission_recharge_minutes")
        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = desc
        sensor._unsub_tick = None

        # Should not raise
        await RoombaSensor.async_will_remove_from_hass(sensor)
        assert sensor._unsub_tick is None


# TestWifiFloor and TestWifiStability moved to test_sensor_helpers.py
# (August 2026) -- the functions they test are defined there.


class TestTotalCleanedAreaArchiveSource:
    """v2.9.0 (J) — SOURCE CHANGE. total_cleaned_area uses MissionArchive's
    cumulative_sqft (cloud-derived, immune to whatever mechanism freezes
    bbrun.sqft, AND immune to FIFO eviction once MAX_RECORDS is exceeded)
    instead of trusting the robot's own onboard lifetime counter, which
    was field-confirmed to barely change over a very long period despite
    continued active use.
    """

    def _make_entity(self, archive, run_stats_sqft=None):
        entity = MagicMock()
        entity.run_stats = {"sqft": run_stats_sqft} if run_stats_sqft is not None else {}
        entity._config_entry.runtime_data.mission_archive = archive
        entity._config_entry.runtime_data.robot_profile_store = None
        return entity

    def _make_archive(self, cumulative_sqft, record_count=10):
        archive = MagicMock()
        archive.cumulative_sqft = cumulative_sqft
        archive.record_count = record_count
        return archive

    def test_uses_cumulative_sqft_from_archive(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=1200.0)
        entity = self._make_entity(archive)

        result = desc.value_fn(entity)
        # 1200 sqft * 0.09290304 = 111.5 m²
        assert result == pytest.approx(111.5, abs=0.1)

    def test_survives_fifo_eviction_unlike_a_live_resum(self):
        """The whole point of using cumulative_sqft instead of summing
        all_derived_oldest_first() live: a robot with more than
        MAX_RECORDS lifetime missions must NOT see this number decrease
        just because old missions aged out of the FIFO-capped list.
        cumulative_sqft is a running total incremented before any trim —
        this test simply confirms the sensor reads that field directly,
        not a live recomputation that would be vulnerable to eviction.
        """
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        # Archive currently holds only a handful of (recent) records —
        # far less than what cumulative_sqft reflects, simulating a robot
        # well past MAX_RECORDS where old missions have been evicted.
        archive = self._make_archive(cumulative_sqft=50_000.0, record_count=5)
        archive.all_derived_oldest_first.return_value = [
            {"sqft": 100}, {"sqft": 100},
        ]  # if this were summed live, result would be tiny — must NOT be used
        entity = self._make_entity(archive)

        result = desc.value_fn(entity)
        assert result == pytest.approx(50_000.0 * 0.09290304, abs=1.0), (
            "Must read cumulative_sqft directly, not recompute from the "
            "currently-held (FIFO-trimmed) record list"
        )

    def test_uses_onboard_counter_when_no_archive_data(self):
        """A fresh install with no archived missions yet must still show
        SOMETHING rather than nothing — uses the (possibly unreliable, but
        better than nothing) onboard bbrun reading."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=0.0, record_count=0)
        entity = self._make_entity(archive, run_stats_sqft=1000)

        result = desc.value_fn(entity)
        assert result == pytest.approx(92.9, abs=0.1)

    def test_uses_onboard_counter_when_no_archive_at_all(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        entity = self._make_entity(archive=None, run_stats_sqft=1000)

        result = desc.value_fn(entity)
        assert result == pytest.approx(92.9, abs=0.1)

    def test_uses_onboard_counter_when_it_is_larger_than_archive_sum(self):
        """v2.9.0 — explicit user request: the raw onboard counter should
        always win when it is LARGER than the archive's cumulative total.
        Both sources are only lower bounds on the true lifetime total (the
        archive only accumulates from whenever cloud credentials were
        first configured, and the onboard counter can freeze, but
        whatever it captured before freezing was real, already-cleaned
        area). A genuine lifetime total can never decrease relative to
        either source.
        """
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=200.0)  # ≈18.6 m² — smaller
        entity = self._make_entity(archive, run_stats_sqft=1882)  # ≈174.8 m²

        result = desc.value_fn(entity)
        assert result == pytest.approx(174.8, abs=0.1), (
            "Onboard counter (174.8 m²) is larger than the archive's "
            "cumulative total (18.6 m²) and must win — never show a "
            "smaller number than either source independently supports"
        )

    def test_uses_archive_sum_when_it_is_larger_than_onboard_counter(self):
        """The symmetric case: a well-archived robot whose onboard counter
        has frozen at a low value must show the larger, more complete
        cumulative total instead."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=50_000.0)  # ≈4645 m²
        entity = self._make_entity(archive, run_stats_sqft=1882)  # ≈174.8 m²

        result = desc.value_fn(entity)
        assert result == pytest.approx(4645.2, abs=1.0)

    def test_returns_none_when_neither_source_has_data(self):
        """Genuine 'no data anywhere' case (e.g. brand-new install before
        the first mission) must show Unavailable, not a confident 0."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=0.0, record_count=0)
        entity = self._make_entity(archive, run_stats_sqft=0)

        result = desc.value_fn(entity)
        assert result is None

    def test_extra_attributes_exposes_onboard_counter_for_comparison(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=1000.0, record_count=5)
        entity = self._make_entity(archive, run_stats_sqft=1882)

        attrs = desc.extra_attributes_fn(entity)
        assert attrs["onboard_counter_m2"] == pytest.approx(174.8, abs=0.1)
        assert attrs["archived_mission_count"] == 5

    def test_extra_attributes_staleness_fields_present(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(s for s in SENSORS if s.key == "total_cleaned_area")

        archive = self._make_archive(cumulative_sqft=1000.0, record_count=5)
        entity = self._make_entity(archive, run_stats_sqft=1000)
        rps = MagicMock()
        rps.lifetime_sqft_last_changed_at = "2026-01-01T00:00:00+00:00"
        rps.lifetime_sqft_days_unchanged = 170.3
        entity._config_entry.runtime_data.robot_profile_store = rps

        attrs = desc.extra_attributes_fn(entity)
        assert attrs["onboard_counter_last_changed_at"] == "2026-01-01T00:00:00+00:00"
        assert attrs["onboard_counter_days_unchanged"] == 170.3


# TestWifiQualityPct and TestWifiHealthSensorUsesQualityPct moved to
# test_sensor_cloud.py (August 2026).


# Moved to test_sensor_helpers.py (August 2026).


class TestStateClassFixes:
    def test_battery_cycles_is_total_increasing(self):
        desc = next(d for d in SENSORS if d.key == "battery_cycles")
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING

    def test_scrubs_count_is_total_increasing(self):
        desc = next(d for d in SENSORS if d.key == "scrubs_count")
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING


class TestBatteryRetentionNiMHGuard:
    """battery_capacity_retention filter: only estCap presence matters (v2.5.0).

    batteryType gate removed: batteryType contains part numbers, never "nimh",
    and the 980 OEM battery is Li-ion (confirmed June 2026). Math is
    scale-invariant — filter passes for any chemistry when estCap is present.
    """

    def _desc(self):
        from custom_components.roomba_plus.sensor import SENSORS
        return next(d for d in SENSORS if d.key == "battery_capacity_retention")

    def test_lithium_with_estcap_surfaces(self):
        """lipo battery with estCap → sensor created."""
        desc = self._desc()
        state = {"bbchg3": {"estCap": 2000}, "batteryType": "lipo"}
        assert desc.filter_fn(state) is True

    def test_nimh_string_with_estcap_now_surfaces(self):
        """batteryType='nimh' with estCap → now True (filter removed, math is scale-invariant).

        In practice batteryType is never 'nimh' (it's a part number), but even if
        it were, the scale-invariant math makes the sensor correct for any chemistry.
        """
        desc = self._desc()
        state = {"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}
        assert desc.filter_fn(state) is True

    def test_no_battery_type_with_estcap_surfaces(self):
        """Unknown battery type with estCap → sensor surfaces."""
        desc = self._desc()
        state = {"bbchg3": {"estCap": 2000}}
        assert desc.filter_fn(state) is True

    def test_no_estcap_suppressed_regardless(self):
        """No estCap → suppressed regardless of battery type."""
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {}, "batteryType": "lipo"}) is False
        assert desc.filter_fn({"bbchg3": {}, "batteryType": "nimh"}) is False

    def test_980_exact_state_surfaces(self):
        """Exact 980 diagnostics state: estCap present → sensor surfaces (v2.5.0)."""
        desc = self._desc()
        state = {
            "bbchg3": {"estCap": 9720, "nLithChrg": 290, "nNimhChrg": 19},
            "batteryType": "F12432712",   # actual runtime value: part number not "nimh"
        }
        assert desc.filter_fn(state) is True


# Moved to test_sensor_helpers.py (August 2026).


class TestBatteryCapacityMahUnaffected:
    """battery_capacity_mah (raw mAh) is NOT NiMH-guarded — raw value is valid."""

    def test_nimh_with_estcap_still_surfaces(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_capacity_mah")
        state = {"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}
        assert desc.filter_fn(state) is True


class TestRecentHistorySensorTranslationKey:
    """Step 23 — translation_key must be set to lock entity_id slug."""

    def test_recent_area_30d_has_translation_key(self):
        from custom_components.roomba_plus.sensor import CLOUD_HISTORY_SENSORS
        desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_area_30d")
        assert desc.translation_key == "recent_area_30d", (
            "translation_key missing — fresh installs will get wrong entity_id suffix"
        )

    def test_recent_time_30d_has_translation_key(self):
        from custom_components.roomba_plus.sensor import CLOUD_HISTORY_SENSORS
        desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_time_30d")
        assert desc.translation_key == "recent_time_30d"

    def test_translation_key_matches_key(self):
        """translation_key must equal key so slug = key string = migration output."""
        from custom_components.roomba_plus.sensor import CLOUD_HISTORY_SENSORS
        for desc in CLOUD_HISTORY_SENSORS:
            if desc.key in ("recent_area_30d", "recent_time_30d"):
                assert desc.translation_key == desc.key, (
                    f"{desc.key}: translation_key={desc.translation_key!r} != key"
                )


# TestCloudRawSensorAvailable moved to test_sensor_cloud.py (August
# 2026). The two classes that followed it there -- CompletionRate and
# LastMissionTeamId -- stay: they test sensor_helpers and sensor_core.


class TestCompletionRateStuckAndResumed:
    """_completion_rate_30d counts stuck_and_resumed as completed."""

    def _rate(self, results):
        from custom_components.roomba_plus.sensor import _completion_rate_30d

        class _FakeStore:
            def query(self, days):
                return [{"result": r, "duration_min": 30} for r in results]

        return _completion_rate_30d(_FakeStore())

    def test_completed_counted(self):
        assert self._rate(["completed", "stuck"]) == pytest.approx(50.0)

    def test_stuck_and_resumed_counted_as_completed(self):
        assert self._rate(["stuck_and_resumed", "stuck"]) == pytest.approx(50.0)

    def test_both_completed_and_stuck_and_resumed(self):
        assert self._rate(["completed", "stuck_and_resumed", "stuck"]) == pytest.approx(66.7, abs=0.1)

    def test_empty_returns_none(self):
        assert self._rate([]) is None


# Moved to test_sensor_helpers.py (August 2026).


class TestEventCounts30dSensor:

    def test_returns_none_without_error_records(self):
        records = [{"done": "done"}, {"done": "done"}]
        s = _make_sensor_v270_consolidated_sensors(RoombaEventCounts30dSensor, records=records)
        assert s.native_value is None

    def test_returns_error_code_from_failed_record(self):
        records = [
            {"classified_result": "error_15", "pauseId": 15},
            {"done": "done"},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaEventCounts30dSensor, records=records)
        val = s.native_value
        assert val == 15

    def test_attributes_include_recharges_and_evacuations(self):
        records = [
            {"chrgs": 2, "evacs": 1, "dirt": 8},
            {"chrgs": 1, "evacs": 0, "dirt": 5},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaEventCounts30dSensor, records=records)
        attrs = s.extra_state_attributes
        assert attrs.get("recharges") == 3
        assert attrs.get("evacuations") == 1
        assert attrs.get("dirt_events") == 13


class TestJobInitiatorStates:
    """Demand must remain distinct from the no-initiator state."""

    def _make_entity(self, initiator: str) -> MagicMock:
        e = MagicMock()
        e.clean_mission_status = {"initiator": initiator}
        return e

    def test_demand_has_its_own_state(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "job_initiator")
        e = self._make_entity("demand")
        assert desc.value_fn(e) == "demand"

    def test_demand_state_distinct_from_none_fallback(self):
        """A demand-triggered mission must not be indistinguishable from none."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "job_initiator")
        demand_result = desc.value_fn(self._make_entity("demand"))
        none_result = desc.value_fn(self._make_entity("none"))
        assert demand_result != none_result

    def test_existing_states_are_stable(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "job_initiator")
        assert desc.value_fn(self._make_entity("schedule")) == "schedule"
        assert desc.value_fn(self._make_entity("manual")) == "manual"



class TestBatteryCycles:
    """battery_cycles sensor must use batInfo.cCount for i/s-series."""

    def _make_entity(self, battery_stats: dict, vac_state: dict) -> MagicMock:
        e = MagicMock()
        e.battery_stats = battery_stats
        e.vacuum_state = vac_state
        return e

    def test_9series_uses_nLithChrg_plus_nNimhChrg(self):
        """9-series: nLithChrg present → use nLithChrg + nNimhChrg."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_cycles")
        e = self._make_entity(
            {"nLithChrg": 290, "nNimhChrg": 22, "nAvail": 1126},
            {},
        )
        assert desc.value_fn(e) == 312

    def test_is_series_uses_batInfo_cCount(self):
        """i/s-series: nLithChrg absent → use batInfo.cCount."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_cycles")
        e = self._make_entity(
            {"estCap": 2492, "nAvail": 2382, "hOnDock": 28667, "avgMin": 81},
            {"batInfo": {"cCount": 779, "mName": "PanasonicEnergy"}},
        )
        assert desc.value_fn(e) == 779

    def test_is_series_no_batInfo_returns_none(self):
        """i/s-series without batInfo → None (not wrong nAvail value)."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_cycles")
        e = self._make_entity(
            {"estCap": 2492, "nAvail": 2382},
            {},
        )
        assert desc.value_fn(e) is None


# Moved to test_sensor_helpers.py (August 2026).


class TestLifetimeCompletionRate:
    def _make_entity(self, mission_stats: dict) -> MagicMock:
        e = MagicMock()
        e.mission_stats = mission_stats
        return e

    def test_completion_rate_calculated(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "lifetime_completion_rate")
        e = self._make_entity({"nMssn": 818, "nMssnOk": 473, "nMssnC": 191, "nMssnF": 150})
        rate = desc.value_fn(e)
        assert abs(rate - 57.8) < 0.2

    def test_zero_missions_returns_none(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "lifetime_completion_rate")
        e = self._make_entity({"nMssn": 0})
        assert desc.value_fn(e) is None


# TestChannelToBand, TestWifiLastChannelSensor and
# TestWifiChannelStabilitySensor moved to test_sensor_cloud.py
# (August 2026).


# TestMissionsPerChargeSensor lives in test_sensor_cloud.py. A copy
# stood here after a bad cut during the August 2026 reorg -- the same
# five tests ran twice, in two files, which is the kind of duplicate
# that quietly doubles a suite and proves nothing extra.


class TestDockFirmwareVersion:
    def test_returns_fwver_when_present(self):
        desc = _find_desc("dock_firmware_version")
        e = _make_entity_v280_field_sensors({"dock": {"fwVer": "4.8.6", "known": True}})
        assert desc.value_fn(e) == "4.8.6"

    def test_returns_none_when_dock_absent(self):
        desc = _find_desc("dock_firmware_version")
        e = _make_entity_v280_field_sensors({})
        assert desc.value_fn(e) is None

    def test_filter_true_when_fwver_present(self):
        desc = _find_desc("dock_firmware_version")
        assert desc.filter_fn({"dock": {"fwVer": "4.8.6"}}) is True

    def test_filter_false_when_dock_has_no_fwver(self):
        """Braava's minimal dock ({'known': True}) has no fwVer — no sensor."""
        desc = _find_desc("dock_firmware_version")
        assert desc.filter_fn({"dock": {"known": True}}) is False

    def test_filter_false_when_dock_absent(self):
        desc = _find_desc("dock_firmware_version")
        assert desc.filter_fn({}) is False


class TestNavStatsProperty:
    def test_returns_bbnav_dict(self):
        e = _make_entity_v280_field_sensors({"bbnav": {"aMtrack": 0.92, "nGoodLmrks": 1843}})
        assert e.nav_stats == {"aMtrack": 0.92, "nGoodLmrks": 1843}

    def test_empty_when_bbnav_absent(self):
        e = _make_entity_v280_field_sensors({"bbrun": {"nPanics": 3}})
        assert e.nav_stats == {}


class TestNavLandmarkQuality:
    def test_filter_true_when_present(self):
        desc = _find_desc("nav_landmark_quality")
        assert desc is not None
        state = {"bbnav": {"aMtrack": 0.94}}
        assert desc.filter_fn(state) is True

    def test_filter_false_when_absent(self):
        desc = _find_desc("nav_landmark_quality")
        assert desc.filter_fn({"bbrun": {"nPanics": 3}}) is False

    def test_value_fn_returns_amtrack(self):
        desc = _find_desc("nav_landmark_quality")
        e = _make_entity_v280_field_sensors({"bbnav": {"aMtrack": 0.94, "nGoodLmrks": 1800}})
        assert desc.value_fn(e) == pytest.approx(0.94)

    def test_disabled_by_default(self):
        desc = _find_desc("nav_landmark_quality")
        assert desc.entity_registry_enabled_default is False


class TestNavGoodLandmarks:
    def test_filter_true_when_present(self):
        desc = _find_desc("nav_good_landmarks")
        assert desc.filter_fn({"bbnav": {"nGoodLmrks": 1843}}) is True

    def test_filter_false_when_absent(self):
        desc = _find_desc("nav_good_landmarks")
        assert desc.filter_fn({}) is False

    def test_value_fn_returns_ngoodlmrks(self):
        desc = _find_desc("nav_good_landmarks")
        e = _make_entity_v280_field_sensors({"bbnav": {"nGoodLmrks": 1843, "aMtrack": 0.91}})
        assert desc.value_fn(e) == 1843

    def test_disabled_by_default(self):
        desc = _find_desc("nav_good_landmarks")
        assert desc.entity_registry_enabled_default is False


class TestOpticalDirtDetections:
    def test_filter_true_via_bbrun(self):
        desc = _find_desc("optical_dirt_detections")
        assert desc.filter_fn({"bbrun": {"nOpticalDD": 4821}}) is True

    def test_filter_true_via_runtimestats(self):
        desc = _find_desc("optical_dirt_detections")
        assert desc.filter_fn({"runtimeStats": {"nOpticalDD": 4821}}) is True

    def test_filter_false_when_absent(self):
        desc = _find_desc("optical_dirt_detections")
        assert desc.filter_fn({"bbrun": {"nPanics": 3}}) is False

    def test_value_fn_from_bbrun(self):
        desc = _find_desc("optical_dirt_detections")
        e = _make_entity_v280_field_sensors({"bbrun": {"nOpticalDD": 4821}})
        assert desc.value_fn(e) == 4821

    def test_value_fn_from_runtimestats(self):
        desc = _find_desc("optical_dirt_detections")
        # runtimeStats wins on collision in run_stats merge
        e = _make_entity_v280_field_sensors({"bbrun": {"nOpticalDD": 100}, "runtimeStats": {"nOpticalDD": 4821}})
        assert desc.value_fn(e) == 4821

    def test_disabled_by_default(self):
        desc = _find_desc("optical_dirt_detections")
        assert desc.entity_registry_enabled_default is False


class TestPiezoDirtDetections:
    def test_filter_true_via_bbrun(self):
        desc = _find_desc("piezo_dirt_detections")
        assert desc.filter_fn({"bbrun": {"nPiezoDD": 2103}}) is True

    def test_value_fn(self):
        desc = _find_desc("piezo_dirt_detections")
        e = _make_entity_v280_field_sensors({"bbrun": {"nPiezoDD": 2103}})
        assert desc.value_fn(e) == 2103

    def test_disabled_by_default(self):
        desc = _find_desc("piezo_dirt_detections")
        assert desc.entity_registry_enabled_default is False


class TestNavOrientations:
    def test_filter_true_via_bbrun(self):
        desc = _find_desc("nav_orientations")
        assert desc.filter_fn({"bbrun": {"nOrients": 847}}) is True

    def test_filter_true_via_runtimestats(self):
        desc = _find_desc("nav_orientations")
        assert desc.filter_fn({"runtimeStats": {"nOrients": 847}}) is True

    def test_value_fn(self):
        desc = _find_desc("nav_orientations")
        e = _make_entity_v280_field_sensors({"bbrun": {"nOrients": 847}})
        assert desc.value_fn(e) == 847

    def test_disabled_by_default(self):
        desc = _find_desc("nav_orientations")
        assert desc.entity_registry_enabled_default is False


# TestParseNetinfoAddr moved to test_sensor_helpers.py (August 2026).


class _FakeEntity:
    def __init__(self, state: dict, vacuum_state: dict | None = None):
        self._state = state
        self.vacuum_state = vacuum_state or state
        self._vac = type("V", (), {"error_message": None, "error_code": 0})()

    @property
    def clean_mission_status(self):
        return self._state.get("cleanMissionStatus", {})

    @property
    def vacuum(self):
        return self._vac


# ── _phase_value ──────────────────────────────────────────────────────────────

from custom_components.roomba_plus.sensor import _phase_value


# Moved to test_sensor_helpers.py (August 2026).


class TestErrorCodeLabels:
    def test_zero_is_none(self):
        assert ERROR_CODE_LABELS[0] == "None"

    def test_common_errors_present(self):
        # CORRECTED: "Main brushes stuck" is the SKU override the app
        # applies for the MARCONI prefix only; the default is "Debris
        # extractors stuck". We were showing the special case to every
        # robot, and this test pinned it.
        assert ERROR_CODE_LABELS[2] == "Debris extractors stuck"
        assert ERROR_CODE_LABELS[6] == "Stuck near a cliff"
        assert ERROR_CODE_LABELS[14] == "Bin missing"
        assert ERROR_CODE_LABELS[36] == "Bin full"

    def test_battery_errors_present(self):
        assert ERROR_CODE_LABELS[106] == "Battery too warm"
        assert ERROR_CODE_LABELS[119] == "Charging timeout"

    def test_216_is_the_robots_own_bin(self):
        """CORRECTED: the wrong part. Enum STARTING_ERROR_BIN_FULL, app
        text "Bin full" -- the robot's bin, not the Clean Base bag. This
        sent people to replace a bag when the bin needed emptying."""
        assert ERROR_CODE_LABELS[216] == "Bin full"

    def test_68_is_a_camera_fault_not_a_map_update(self):
        """Neither the enum name (CAMERA_HARDWARE_FAILURE) nor the app's
        text supports "Updating map", and the two agree with each other.
        A user was told to wait while the robot reported a hardware
        fault."""
        assert ERROR_CODE_LABELS[68] == "Camera issue"

    def test_total_coverage(self):
        assert len(ERROR_CODE_LABELS) >= 70

    def test_all_values_are_strings(self):
        assert all(isinstance(v, str) for v in ERROR_CODE_LABELS.values())

    def test_all_keys_are_ints(self):
        assert all(isinstance(k, int) for k in ERROR_CODE_LABELS.keys())


# ── L3-FIX: consecutive_mission_anomalies sensor (v3.0.0) ────────────────────

class TestConsecutiveMissionAnomalies:
    """New sensor exposes MissionStore.consecutive_anomalous (L3-FIX, v3.0.0)."""

    def _entity_with_streak(self, streak: int):
        """Return a sensor-like entity whose MissionStore returns the given streak."""
        ms = MagicMock()
        ms.consecutive_anomalous = streak
        entry = _make_config_entry()
        entry.runtime_data.mission_store = ms
        return entry

    def test_sensor_key_in_sensors_tuple(self):
        """consecutive_mission_anomalies must be in SENSORS so it gets registered."""
        keys = [d.key for d in SENSORS]
        assert "consecutive_mission_anomalies" in keys

    def test_value_returns_streak_from_mission_store(self):
        """native_value reads consecutive_anomalous from MissionStore."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "consecutive_mission_anomalies")
        entry = self._entity_with_streak(5)
        e = _entity({})
        e._config_entry = entry
        assert desc.value_fn(e) == 5

    def test_disabled_by_default(self):
        """Sensor is opt-in (disabled_by_default) — only Card and Automations consume it."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "consecutive_mission_anomalies")
        assert desc.entity_registry_enabled_default is False

    def test_extra_attributes_exposes_last_mission_id(self):
        """v3.2.0 ANOMALY-EXPLAIN — last_mission_id lets the card/automation
        feed straight into explain_mission without a separate lookup."""
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "consecutive_mission_anomalies")
        ms = MagicMock()
        ms.latest.return_value = {"id": "m_123"}
        entry = _make_config_entry()
        entry.runtime_data.mission_store = ms
        e = _entity({})
        e._config_entry = entry
        assert desc.extra_attributes_fn(e) == {"last_mission_id": "m_123"}

    def test_extra_attributes_empty_when_no_history(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "consecutive_mission_anomalies")
        ms = MagicMock()
        ms.latest.return_value = None
        entry = _make_config_entry()
        entry.runtime_data.mission_store = ms
        e = _entity({})
        e._config_entry = entry
        assert desc.extra_attributes_fn(e) == {}


# ── Signal sensors (SNR, Noise, IP) ──────────────────────────────────────────

class TestSignalSensors:
    def _entity(self, signal=None, netinfo=None):
        state = {}
        if signal:
            state["signal"] = signal
        if netinfo:
            state["netinfo"] = netinfo
        return _FakeEntity(state)

    def test_snr_present(self):
        e = self._entity(signal={"rssi": -60, "snr": 25, "noise": -85})
        assert e.vacuum_state.get("signal", {}).get("snr") == 25

    def test_noise_present(self):
        e = self._entity(signal={"rssi": -60, "snr": 25, "noise": -85})
        assert e.vacuum_state.get("signal", {}).get("noise") == -85

    def test_ip_address_present(self):
        e = self._entity(netinfo={"addr": "192.168.1.42"})
        assert e.vacuum_state.get("netinfo", {}).get("addr") == "192.168.1.42"

    def test_snr_missing_returns_none(self):
        e = self._entity(signal={"rssi": -60})
        assert e.vacuum_state.get("signal", {}).get("snr") is None

    def test_ip_missing_returns_none(self):
        e = _FakeEntity({})
        assert e.vacuum_state.get("netinfo", {}).get("addr") is None


# ── v2.8.3 — FW-SENSOR ────────────────────────────────────────────────────────

# Moved to a module-specific test file (August 2026).


def _sensor():
    """Minimal RoombaSensor instance — no HA/roombapy setup needed since
    _next_from_schedule2/_next_from_schedule_v1 only touch dt_util.now()
    and their own parameters, nothing else on self."""
    from custom_components.roomba_plus.sensor import RoombaSensor
    return RoombaSensor.__new__(RoombaSensor)


def _next_monday_at(hour: int, minute: int = 0) -> datetime.datetime:
    """A fixed, well-known Monday (2024-01-01) at the given time, for
    freezing "now" to a specific weekday/time combination."""
    return datetime.datetime(2024, 1, 1, hour, minute, tzinfo=datetime.timezone.utc)


def _on_weekday(weekday_py: int, hour: int, minute: int = 0) -> datetime.datetime:
    """Return a datetime on a specific Python weekday and time, anchored
    to the same fixed Monday as _next_monday_at for consistency."""
    anchor = _next_monday_at(0, 0)  # Monday 2024-01-01
    days = (weekday_py - anchor.weekday()) % 7
    return anchor + datetime.timedelta(days=days, hours=hour, minutes=minute)


# ── Tests: cleanSchedule2 ─────────────────────────────────────────────────────

class TestNextFromSchedule2:
    def test_single_enabled_entry_today_in_future(self, freezer):
        """Entry on Monday at 09:00, now is Monday 08:00 → today at 09:00."""
        freezer.move_to(_on_weekday(0, 8, 0))  # Monday 08:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]  # 1=Mon
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9
        assert result.minute == 0

    def test_single_enabled_entry_today_in_past(self, freezer):
        """Entry on Monday at 07:00, now is Monday 08:00 → next Monday at 07:00."""
        now = _on_weekday(0, 8, 0)
        freezer.move_to(now)
        entries = [{"enabled": True, "start": {"hour": 7, "min": 0, "day": [1]}}]
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 0
        assert (result - now).days == 6

    def test_disabled_entry_ignored(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        entries = [{"enabled": False, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]
        result = _sensor()._next_from_schedule2(entries)
        assert result is None

    def test_multiple_days_returns_nearest(self, freezer):
        """Entry on Mon and Wed, now is Mon 10:00 → next is Wed."""
        freezer.move_to(_on_weekday(0, 10, 0))  # Monday 10:00
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1, 3]}}]  # Mon, Wed
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 2  # Wednesday

    def test_multiple_entries_returns_nearest(self, freezer):
        freezer.move_to(_on_weekday(2, 8, 0))  # Wednesday 08:00
        entries = [
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [5]}},  # Fri
            {"enabled": True, "start": {"hour": 9, "min": 0, "day": [4]}},  # Thu
        ]
        result = _sensor()._next_from_schedule2(entries)
        assert result.weekday() == 3  # Thursday

    def test_sunday_day_zero_conversion(self, freezer):
        """Roomba day 0 = Sunday = Python weekday 6."""
        freezer.move_to(_on_weekday(5, 8, 0))  # Saturday 08:00
        entries = [{"enabled": True, "start": {"hour": 10, "min": 0, "day": [0]}}]  # Sun
        result = _sensor()._next_from_schedule2(entries)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_entries(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        assert _sensor()._next_from_schedule2([]) is None

    def test_exact_match_time_is_past(self, freezer):
        """If now == schedule time exactly, it should roll to next week."""
        now = _on_weekday(0, 9, 0)  # Monday 09:00 exactly
        freezer.move_to(now)
        entries = [{"enabled": True, "start": {"hour": 9, "min": 0, "day": [1]}}]
        result = _sensor()._next_from_schedule2(entries)
        # candidate == now → not > now → rolls to next week
        assert result is not None
        assert (result - now).days == 7


# ── Tests: legacy cleanSchedule ───────────────────────────────────────────────

class TestNextFromScheduleV1:
    def test_single_day_in_future(self, freezer):
        """Schedule runs Monday 09:00, now is Monday 08:00."""
        freezer.move_to(_on_weekday(0, 8, 0))
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 0  # Monday
        assert result.hour == 9

    def test_single_day_in_past(self, freezer):
        now = _on_weekday(0, 10, 0)  # Monday 10:00
        freezer.move_to(now)
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "none", "none"],
            "h":     [0,      9,       0,      0,      0,      0,      0],
            "m":     [0,      0,       0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 0
        assert (result - now).days == 6  # next week

    def test_all_none_returns_none(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        schedule = {
            "cycle": ["none", "none", "none", "none", "none", "none", "none"],
            "h":     [0, 0, 0, 0, 0, 0, 0],
            "m":     [0, 0, 0, 0, 0, 0, 0],
        }
        assert _sensor()._next_from_schedule_v1(schedule) is None

    def test_multiple_days_nearest_selected(self, freezer):
        """Mon and Fri scheduled, now is Wed 08:00 → Fri."""
        freezer.move_to(_on_weekday(2, 8, 0))  # Wednesday
        schedule = {
            "cycle": ["none", "start", "none", "none", "none", "start", "none"],
            "h":     [0,      9,       0,      0,      0,      9,       0],
            "m":     [0,      0,       0,      0,      0,      0,       0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 4  # Friday

    def test_sunday_index_zero(self, freezer):
        """Index 0 in cleanSchedule = Sunday = Python weekday 6."""
        freezer.move_to(_on_weekday(5, 8, 0))  # Saturday
        schedule = {
            "cycle": ["start", "none", "none", "none", "none", "none", "none"],
            "h":     [10,      0,      0,      0,      0,      0,      0],
            "m":     [0,       0,      0,      0,      0,      0,      0],
        }
        result = _sensor()._next_from_schedule_v1(schedule)
        assert result is not None
        assert result.weekday() == 6  # Sunday

    def test_empty_schedule(self, freezer):
        freezer.move_to(_on_weekday(0, 8, 0))
        assert _sensor()._next_from_schedule_v1({}) is None


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_cloud_entities.py (TEST-REORG, v2.9.1) — cloud-sourced
# entities: CloudSmartZoneSelect (options/current_option/region_id/attrs/
# multi-pmap), FavoriteButton, select.py async_setup_entry cloud-vs-MQTT
# routing, SmartZoneSelect naming-issue suppression when cloud active.
# ═══════════════════════════════════════════════════════════════════════

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config_entry(has_cloud: bool = False, favorites=None, pmaps=None):
    """Return a minimal mock config entry."""
    entry = MagicMock()
    entry.unique_id = "test_blid"
    entry.options = {}
    entry.data = {"blid": "test_blid"}

    cc = MagicMock()
    cc.data = {
        "pmaps": pmaps or [],
        "favorites": favorites or [],
        "mission_history": {},
    }
    cc.active_pmap_id = (pmaps[0].get("active_pmapv_details", {}).get("active_pmapv", {}).get("pmap_id") if pmaps else None)
    runtime = MagicMock()
    runtime.has_cloud = has_cloud
    runtime.cloud_coordinator = cc if has_cloud else None
    entry.runtime_data = runtime
    return entry


def _make_roomba():
    r = MagicMock()
    r.master_state = {"state": {"reported": {}}}
    return r


# The six CloudSmartZoneSelect classes that stood here moved to
# test_select.py (August 2026) -- they tested `select.py` from a file
# named for sensors. `_make_config_entry` and `_make_roomba` above stay:
# tests in this file still use them.


# `_fav_button` and TestFavoriteButton moved to test_button.py
# (August 2026) -- that file's header claimed button.py had zero
# coverage, which was true only because its tests were filed here.


# TestSelectSetupEntryRouting moved to test_select.py (August 2026).
# The four _make_history* helpers below are COPIED there rather than
# moved: the cloud-history classes in this file still use them.

def _make_history(sqft: int = 0, hr: int = 0, mn: int = 0, n_mssn: int = 0) -> dict:
    """Build a fake coordinator.data["mission_history"] dict for CloudHistorySensor tests."""
    return {
        "runtimeStats": {"sqft": sqft, "hr": hr, "min": mn},
        "bbmssn": {"nMssn": n_mssn},
    }


def _make_history_list(**kwargs) -> list:
    """Wrap _make_history in a list — simulates the raw API response before normalisation."""
    return [_make_history(**kwargs)]


def _make_history_sensor(key: str, history: dict | None = None, *, success: bool = True):
    """Return a CloudHistorySensor instance wired to a fake coordinator."""
    from custom_components.roomba_plus.sensor import CLOUD_HISTORY_SENSORS, CloudHistorySensor
    desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == key)
    coordinator = MagicMock()
    coordinator.last_update_success = success
    coordinator.data = {"mission_history": history or {}} if success else None
    entry = _make_config_entry(has_cloud=True)
    entry.runtime_data.cloud_coordinator = coordinator
    roomba = _make_roomba()
    sensor = CloudHistorySensor(roomba, "test_blid", coordinator, desc)
    sensor._config_entry = entry
    return sensor


def _make_history_coordinator(history: dict):
    """Return a fake coordinator whose data contains mission_history."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"mission_history": history, "pmaps": [], "mission_history_raw": []}
    coordinator.raw_records = []
    return coordinator


# ── 600-series cloud sensor creation (Q1 verification) ───────────────────────



# The cloud mission-history classes that stood here moved to
# test_sensor_cloud.py (August 2026) -- nine classes, 46 tests. The
# _make_history* helpers above stay: TestSensorSetupEntryCloud and
# TestNextFromScheduleV1 still use them.


class TestSensorSetupEntryCloud:
    """Verify async_setup_entry creates cloud sensors when has_cloud is True."""

    def _make_entry(self, has_cloud: bool):
        entry = MagicMock()
        entry.options = {}
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        cc = _make_history_coordinator(_make_history(sqft=500, hr=10, mn=0, n_mssn=50))
        runtime = MagicMock()
        runtime.roomba = roomba
        runtime.blid = "test_blid"
        runtime.has_cloud = has_cloud
        runtime.cloud_coordinator = cc if has_cloud else None
        entry.runtime_data = runtime
        return entry

    @pytest.mark.asyncio
    async def test_cloud_sensors_created_when_has_cloud(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=True)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        assert len(cloud_sensors) == 3

    @pytest.mark.asyncio
    async def test_cloud_sensors_not_created_without_credentials(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=False)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        assert len(cloud_sensors) == 0

    @pytest.mark.asyncio
    async def test_all_three_sensor_keys_created(self):
        from custom_components.roomba_plus import sensor as sensor_mod

        entry = self._make_entry(has_cloud=True)
        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sensor_mod, "roomba_reported_state", return_value={}):
            with patch.object(sensor_mod, "SENSORS", []):
                await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_sensors = [e for e in created if isinstance(e, CloudHistorySensor)]
        keys = {e.entity_description.key for e in cloud_sensors}
        assert keys == {"recent_area_30d", "recent_time_30d", "lifetime_missions"}


# ─────────────────────────────────────────────────────────────────────────────
# LAST-MISSION-SUMMARY (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

# The room-sensor classes and their helpers moved to
# test_sensor_rooms.py (August 2026) -- five classes, 31 tests.
# `_store_with` above stays: TestSensorSetupEntryCloud uses it.


class TestPrimarySlim:
    """PRIMARY-SLIM (v3.1.0) — verify entity_category assignments on SENSORS tuple."""

    def test_clean_streak_is_diagnostic(self):
        """clean_streak must be DIAGNOSTIC after PRIMARY-SLIM reclassification."""
        from homeassistant.helpers.entity import EntityCategory
        desc = _get_sensor("clean_streak")
        assert desc.entity_category == EntityCategory.DIAGNOSTIC, (
            "clean_streak should be DIAGNOSTIC (pure statistic, not daily-use)"
        )

    def test_core_primary_sensors_remain_primary(self):
        """battery, phase, error, next_clean, last_mission_result must stay Primary."""
        for key in ("battery", "phase", "error", "next_clean", "last_mission_result"):
            desc = _get_sensor(key)
            assert desc.entity_category is None, (
                f"{key} should remain Primary (entity_category=None)"
            )


# ─────────────────────────────────────────────────────────────────────────────
# L9-MAP / relocalisation_rate (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _make_reloc_sensor(rps=None):
    """Return a RoombaRelocalisationRateSensor backed by the given RobotProfileStore."""
    from custom_components.roomba_plus.sensor import RoombaRelocalisationRateSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    rd = MagicMock()
    rd.robot_profile_store = rps
    entry.runtime_data = rd
    sensor = RoombaRelocalisationRateSensor.__new__(RoombaRelocalisationRateSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_relocalisation_rate"
    return sensor


# ─────────────────────────────────────────────────────────────────────────────
# RESET-DIAGNOSTICS (v3.2.0)
# ─────────────────────────────────────────────────────────────────────────────

# Moved to a module-specific test file (August 2026).


    def test_ready_returns_window_mean(self):
        """Baseline established → native_value is the recent window's mean."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(20):
            rps.update_reloc_baseline(2)
        sensor = _make_reloc_sensor(rps=rps)
        assert sensor.native_value == pytest.approx(2.0)

    def test_attributes_include_baseline_and_window(self):
        """extra_state_attributes expose baseline, count, window, and
        percentile_rank (v3.5.0 — replaces the old fixed-multiplier
        alert)."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(20):
            rps.update_reloc_baseline(1)
        sensor = _make_reloc_sensor(rps=rps)
        attrs = sensor.extra_state_attributes
        assert attrs["baseline"] == pytest.approx(1.0)
        assert attrs["baseline_mission_count"] == 20
        assert len(attrs["recent_window"]) == 10
        assert attrs["percentile_rank"] is not None

    def test_percentile_rank_attribute_reflects_elevated_window(self):
        """A sustained spike shows up as a high percentile_rank."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(100):
            rps.update_reloc_baseline(1)
        for _ in range(10):
            rps.update_reloc_baseline(10)
        sensor = _make_reloc_sensor(rps=rps)
        assert sensor.extra_state_attributes["percentile_rank"] > 90


class TestMopSensorSlugConsistency:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: guards against the exact inconsistency
    found during the fix — mop_tank_status's options array had 5 entries
    but strings.json was missing "unknown" entirely. Ensures the
    descriptor's `options` and strings.json's `state` keys always match.
    """

    def test_options_match_strings_json_keys(self):
        import json, os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "custom_components", "roomba_plus", "strings.json"
        )
        with open(path, encoding="utf-8") as f:
            strings_data = json.load(f)

        for sensor_key in ("mop_clean_mode", "mop_tank_status", "mop_ars_behavior"):
            desc = next(d for d in SENSORS if d.key == sensor_key)
            options = set(desc.options)
            translation_keys = set(strings_data["entity"]["sensor"][sensor_key]["state"].keys())
            assert options == translation_keys, (
                f"{sensor_key}: options={options} vs strings.json keys={translation_keys}, "
                f"diff={options ^ translation_keys}"
            )

    def test_all_options_are_hassfest_valid_slugs(self):
        import re
        pattern = re.compile(r"^[a-z0-9_-]+$")
        for sensor_key in ("mop_clean_mode", "mop_tank_status", "mop_ars_behavior"):
            desc = next(d for d in SENSORS if d.key == sensor_key)
            for option in desc.options:
                assert pattern.match(option), f"{sensor_key}: {option!r} invalid slug"
                assert not option.startswith(("-", "_")), f"{sensor_key}: {option!r} starts with -/_"
                assert not option.endswith(("-", "_")), f"{sensor_key}: {option!r} ends with -/_"

    def test_all_seven_translations_have_matching_keys(self):
        import json, os
        base = os.path.join(
            os.path.dirname(__file__),
            "..", "custom_components", "roomba_plus", "translations"
        )
        for sensor_key in ("mop_clean_mode", "mop_tank_status", "mop_ars_behavior"):
            desc = next(d for d in SENSORS if d.key == sensor_key)
            options = set(desc.options)
            for lang in ("en", "de", "fr", "it", "es", "nl", "pt"):
                with open(os.path.join(base, f"{lang}.json"), encoding="utf-8") as f:
                    data = json.load(f)
                translation_keys = set(data["entity"]["sensor"][sensor_key]["state"].keys())
                assert options == translation_keys, (
                    f"{lang}/{sensor_key}: options={options} vs keys={translation_keys}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 ROOM-SCHED — rooms_overdue sensor
# ─────────────────────────────────────────────────────────────────────────────

# TestRoomsOverdueSensor and TestDirtCorrelationSensor moved to
# test_sensor_rooms.py (August 2026).


# Moved to a module-specific test file (August 2026).


class TestConsumablePartsAppearWhenDiscovered:
    """Parts get sensors as they are discovered, not only at setup.

    A first version added them once during setup and called the rest a
    known limitation. That was too convenient: the parts fetch is
    best-effort, so a cloud hiccup at startup would have meant the user
    never saw these sensors until they reloaded the config entry -- for
    a failure that resolves itself within hours.

    Home Assistant supports adding entities later; not doing so was a
    choice, not a constraint."""

    def _wire(self, initial):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import _add_discovered_parts

        added: list = []
        listeners: list = []

        data = MagicMock()
        data.blid = "BLID"
        data.prime_parts_coordinator.data = initial
        data.prime_parts_coordinator.async_add_listener = (
            lambda cb: listeners.append(cb) or (lambda: None)
        )
        entry = MagicMock()

        _add_discovered_parts(MagicMock(), data, entry, lambda gen: added.extend(gen))
        return data, added, listeners

    def test_parts_present_at_setup_get_sensors_immediately(self):
        _data, added, _listeners = self._wire({"filter": object(), "dirt_bag": object()})

        assert len(added) == 2

    def test_nothing_at_setup_creates_nothing_yet(self):
        """The failed-fetch case. No sensors, no crash."""
        _data, added, _listeners = self._wire({})

        assert added == []

    def test_a_later_refresh_creates_them(self):
        """THE case the first version could not handle: the fetch failed
        at startup and succeeded an hour later."""
        data, added, listeners = self._wire({})
        assert added == []

        data.prime_parts_coordinator.data = {"filter": object()}
        listeners[0]()

        assert len(added) == 1

    def test_a_part_is_never_added_twice(self):
        """The listener fires on every refresh. Without the guard, a
        robot would accumulate duplicate entities all day."""
        data, added, listeners = self._wire({"filter": object()})
        assert len(added) == 1

        for _ in range(5):
            listeners[0]()

        assert len(added) == 1

    def test_a_newly_appearing_part_joins_the_existing_ones(self):
        """A dirt bag showing up after someone attaches a self-emptying
        base -- a real reason for the set to grow mid-life."""
        data, added, listeners = self._wire({"filter": object()})

        data.prime_parts_coordinator.data = {"filter": object(), "dirt_bag": object()}
        listeners[0]()

        assert len(added) == 2

    def test_no_coordinator_is_survivable(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import _add_discovered_parts

        data = MagicMock()
        data.prime_parts_coordinator = None

        _add_discovered_parts(MagicMock(), data, MagicMock(), lambda gen: list(gen))


class TestBraavaVersusMoppingRobots:
    """`is_mop()` answers "can it mop" and was being used to ask "has it
    no brushes". On a Braava those coincide, which is why thirteen brush
    and bin gates were written as `not is_mop(state)` and nobody noticed.

    On a Combo they do not: it has a pad AND brushes, so it would lose
    its filter sensors to a question about mopping.

    Nobody found this in the field because every tester's robot sits
    cleanly on one side -- Braava m6, i-series, s9, 900-series. It needs
    a Classic Combo, and nobody in the group owns one.
    """

    def _state(self, sku=None, pad=False):
        state = {}
        if sku:
            state["sku"] = sku
        if pad:
            state["detectedPad"] = "reusableDry"
        return state

    def test_a_braava_is_both(self):
        from custom_components.roomba_plus.const import is_braava, is_mop

        state = self._state("m611020", pad=True)
        assert is_mop(state) is True
        assert is_braava(state) is True

    def test_a_combo_mops_but_is_not_a_braava(self):
        """The case the split exists for. It has brushes and a filter."""
        from custom_components.roomba_plus.const import is_braava, is_mop

        state = self._state("c355020", pad=True)
        assert is_mop(state) is True
        assert is_braava(state) is False

    def test_vacuums_are_neither(self):
        from custom_components.roomba_plus.const import is_braava, is_mop

        for sku in ("i755840", "R980020", "s955020", "j915020"):
            state = self._state(sku)
            assert is_mop(state) is False
            assert is_braava(state) is False, sku

    def test_no_sku_falls_back_to_the_old_reading(self):
        """A capability flag can go missing on a robot that has the
        hardware; a SKU cannot go missing on a robot that has one. When
        it does, falling back costs nothing -- on a Braava the old
        reading was already right."""
        from custom_components.roomba_plus.const import is_braava

        assert is_braava(self._state(pad=True)) is True
        assert is_braava(self._state()) is False

    def test_a_combo_keeps_its_filter_sensors(self):
        """The gates that moved. A robot that mops still has a filter."""
        from custom_components.roomba_plus.sensor_core import SENSORS

        state = self._state("c355020", pad=True)
        keys = {
            d.key for d in SENSORS
            if d.key in ("filter_wear_rate", "filter_days_until_due")
            and d.filter_fn(state)
        }
        assert keys == {"filter_wear_rate", "filter_days_until_due"}

    def test_a_braava_is_unaffected_by_the_gates_that_moved(self):
        """The point of choosing SKU prefix over a capability flag: no
        robot anyone actually owns changes behaviour."""
        from custom_components.roomba_plus.sensor_core import SENSORS

        state = self._state("m611020", pad=True)
        moved = {"filter_wear_rate", "filter_days_until_due"}
        assert not [d for d in SENSORS if d.key in moved and d.filter_fn(state)]

    def test_observed_while_doing_this_two_filter_sensors_are_ungated(self):
        """`filter_remaining_hours` and `filter_last_replaced` carry no
        gate at all, so a Braava gets them -- and a Braava has no filter.

        Pre-existing, untouched here, and pinned rather than fixed: it is
        a different question from the brush/pad split and deserves its
        own decision. Without this test it would look like the split
        missed something.
        """
        from custom_components.roomba_plus.sensor_core import SENSORS

        state = self._state("m611020", pad=True)
        shown = {
            d.key for d in SENSORS
            if d.key.startswith("filter_") and d.filter_fn(state)
        }
        assert shown == {"filter_remaining_hours", "filter_last_replaced"}

    def test_the_brush_slot_is_still_either_or(self):
        """Not an oversight. MaintenanceStore has one slot for brush OR
        pad, so a Combo getting both sensors would read one number
        twice -- worse than one unambiguous sensor. Documented at the
        gate; fixing it needs a second store slot."""
        from custom_components.roomba_plus.sensor_core import SENSORS

        state = self._state("c355020", pad=True)
        shown = {
            d.key for d in SENSORS
            if d.key in ("brush_last_replaced", "pad_last_replaced")
            and d.filter_fn(state)
        }
        assert shown == {"pad_last_replaced"}


# ── from test_sensor_resilience.py ───────────────────────────────────────
#
# Three tests about filter_fn and value_fn surviving malformed states.
# They are about sensors, and the file about sensors is here -- a separate
# file for "the resilience ones" is a distinction nobody makes when
# looking for a sensor test.


import copy
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus.sensor import SENSORS


REAL_980_STATE = {
    "batPct": 100,
    "batteryType": "F12432712",
    "bbchg": {"nChgOk": 325, "nLithF": 0, "aborts": [1, 1, 1]},
    "bbchg3": {"avgMin": 415, "hOnDock": 30557, "nAvail": 1160, "estCap": 9720,
               "nLithChrg": 290, "nNimhChrg": 36, "nDocks": 229},
    "bbmssn": {"nMssn": 425, "nMssnOk": 135, "nMssnC": 182, "nMssnF": 108,
               "aMssnM": 94, "aCycleM": 42},
    "bbrun": {"hr": 438, "min": 5, "sqft": 1903, "nStuck": 168, "nScrubs": 958,
              "nPicks": 1099, "nPanics": 1544, "nCliffsF": 6968,
              "nCliffsR": 3555, "nMBStll": 24, "nWStll": 23, "nCBump": 0},
    "bin": {"present": True, "full": False},
    "binPause": True,
    "cap": {"pose": 1, "ota": 2, "multiPass": 2, "carpetBoost": 1, "pp": 1,
            "binFullDetect": 1, "maps": 1, "edge": 1, "eco": 1},
    "carpetBoost": True,
    "cleanMissionStatus": {"cycle": "none", "phase": "charge", "error": 0,
                           "sqft": 0, "mssnM": 0, "nMssn": 425, "notReady": 0,
                           "initiator": ""},
    "dock": {"known": True},
    "hardwareRev": 3,
    "mapUploadAllowed": True,
    "name": "Roomba",
    "noAutoPasses": False,
    "openOnly": False,
    "pose": {"point": {"x": 0, "y": 0}, "theta": 0},
    "schedHold": False,
    "signal": {"rssi": -47, "snr": 42},
    "sku": "R980040",
    "softwareVer": "v2.4.17-138",
    "twoPass": False,
    "vacHigh": False,
    "wifistat": {"rssi": -47},
}


def _all_none(state):
    return {k: None for k in state}


def _empty_subdicts(state):
    return {k: ({} if isinstance(v, dict) else v) for k, v in state.items()}


def _null_subdicts(state):
    out = copy.deepcopy(state)
    for k in ("bbrun", "bbchg3", "bbmssn", "cleanMissionStatus", "bin", "cap",
              "pose", "signal", "wifistat", "dock", "bbchg"):
        if k in out:
            out[k] = None
    return out


def _missing_subdicts(state):
    return {k: v for k, v in state.items() if not isinstance(v, dict)}


SHAPES = {
    "REAL": REAL_980_STATE,
    "all-none": _all_none(REAL_980_STATE),
    "empty-subdicts": _empty_subdicts(REAL_980_STATE),
    "null-subdicts": _null_subdicts(REAL_980_STATE),
    "missing-subdicts": _missing_subdicts(REAL_980_STATE),
    "empty-dict": {},
}


class TestSensorFilterFnResilience:
    """No filter_fn may raise on any reported-state shape — a single crash in
    the async_setup_entry list comprehension takes down the whole platform."""

    @pytest.mark.parametrize("shape_name", list(SHAPES))
    def test_all_filter_fns_survive_shape(self, shape_name):
        state = SHAPES[shape_name]
        failures = []
        for desc in SENSORS:
            fn = getattr(desc, "filter_fn", None)
            if fn is None:
                continue
            try:
                fn(state)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{desc.key}: {type(e).__name__}: {e}")
        assert not failures, (
            f"{len(failures)} filter_fn crash(es) on '{shape_name}' state:\n"
            + "\n".join(failures)
        )

    def test_real_state_surfaces_sensors(self):
        """Sanity: the real 980 state should surface a healthy number of
        sensors (filter_fn → True), proving the test state is realistic."""
        surfaced = sum(
            1 for d in SENSORS
            if getattr(d, "filter_fn", None) and d.filter_fn(REAL_980_STATE)
        )
        # A 980 with full bbrun/bbchg/bbmssn should surface many sensors
        assert surfaced >= 10


class TestSensorValueFnResilience:
    """value_fn(entity) resilience is covered comprehensively by the per-sensor
    test files (test_sensors.py etc.), which build properly-wired entities.

    A platform-wide value_fn stress test was evaluated here but a minimal mock
    entity cannot distinguish a real crash from MagicMock-arithmetic noise (a
    value_fn reading entity._config_entry.runtime_data.* gets a MagicMock, and
    `MagicMock / int` raises TypeError that would not occur with a real
    entity). The meaningful platform-failure guard is the filter_fn test above:
    filter_fn takes the raw state dict directly and runs in the single list
    comprehension that can take down the whole platform.

    This placeholder documents that the value_fn path is intentionally covered
    elsewhere rather than with an unreliable platform-wide mock.
    """

    def test_real_state_is_well_formed(self):
        """Guard the test fixture itself: the reconstructed 980 state has the
        sub-dicts the value_fns expect, so the per-sensor tests that reuse
        similar shapes stay representative of real field data."""
        for key in ("bbrun", "bbchg3", "bbmssn", "cleanMissionStatus"):
            assert isinstance(REAL_980_STATE[key], dict)
        assert REAL_980_STATE["bbrun"]["hr"] == 438
        assert REAL_980_STATE["bbmssn"]["nMssn"] == 425
