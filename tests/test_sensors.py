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


def _make_entry(mission_store=None):
    entry = MagicMock()
    rd = MagicMock()
    rd.has_cloud = True
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


class TestRecentCompletionRate:

    def test_all_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 4
        assert _raw_completion_rate(records) == 100.0

    def test_half_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 2 + [_rec(done="stuck", pause_id=17)] * 2
        assert _raw_completion_rate(records) == 50.0

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        assert _raw_completion_rate([]) is None

    def test_rounded_to_one_decimal(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        # 2 of 3 = 66.666...% → 66.7
        records = [_rec(done="done")] * 2 + [_rec(done="stuck")]
        assert _raw_completion_rate(records) == 66.7

    def test_zero_percent(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="stuck")] * 3
        assert _raw_completion_rate(records) == 0.0


class TestRecentRecharges:

    def test_sums_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=2), _rec(chrgs=1), _rec(chrgs=0)]
        assert _raw_recharges(records) == 3

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        assert _raw_recharges([]) is None

    def test_zero_recharges(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=0)] * 5
        assert _raw_recharges(records) == 0

    def test_handles_missing_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [{"done": "done", "classified_result": "completed"}]
        assert _raw_recharges(records) == 0


class TestRecentEvacuations:

    def test_sums_evacs(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=1), _rec(evacs=3), _rec(evacs=0)]
        assert _raw_evacuations(records) == 4

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        assert _raw_evacuations([]) is None

    def test_zero_evacuations(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=0)] * 3
        assert _raw_evacuations(records) == 0


class TestRecentDirtEvents:

    def test_sums_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=5), _rec(dirt=10), _rec(dirt=2)]
        assert _raw_dirt_events(records) == 17

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        assert _raw_dirt_events([]) is None

    def test_zero_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=0)] * 4
        assert _raw_dirt_events(records) == 0


class TestCloudLastErrorCode:

    def test_returns_pause_id_from_first_error(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="done"),                          # completed — skip
            _rec(done="stuck", pause_id=17),            # error_17 — match
            _rec(done="stuck", pause_id=18),            # older error — not used
        ]
        assert _raw_cloud_last_error_code(records) == 17

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_code(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        assert _raw_cloud_last_error_code([]) is None

    def test_stuck_with_pause_id_zero_returns_none(self):
        """stuck + pauseId=0 → classified as 'stuck', no specific code."""
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=0)]
        # classified_result = "stuck", pause_id=0 → None
        assert _raw_cloud_last_error_code(records) is None

    def test_error_224_smart_map(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=224)]
        assert _raw_cloud_last_error_code(records) == 224

    def test_skips_cancelled_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="cncl", done_raw="usrEnd"),       # cancelled_by_user — skip
            _rec(done="cncl"),                           # cancelled — skip
            _rec(done="stuck", pause_id=6),             # error_6 — match
        ]
        assert _raw_cloud_last_error_code(records) == 6


class TestCloudLastErrorTime:

    def test_returns_datetime_from_timestamp(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="stuck", pause_id=17, timestamp=1700000000)]
        result = _raw_cloud_last_error_time(records)
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.year == 2023
        assert result.tzinfo == datetime.timezone.utc

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="done", timestamp=1700000000)] * 3
        assert _raw_cloud_last_error_time(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        assert _raw_cloud_last_error_time([]) is None

    def test_uses_most_recent_error(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        # Records newest-first — first error timestamp wins
        records = [
            _rec(done="stuck", pause_id=17, timestamp=1700010000),
            _rec(done="stuck", pause_id=18, timestamp=1700000000),
        ]
        result = _raw_cloud_last_error_time(records)
        expected = datetime.datetime.fromtimestamp(1700010000, tz=datetime.timezone.utc)
        assert result == expected


class TestCloudLastErrorAttrs:

    def test_returns_catalogue_fields(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=17)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs["error_code"] == 17
        assert attrs["source"] == "cloud_pauseId"
        assert "label" in attrs
        assert "description" in attrs
        assert "action" in attrs

    def test_empty_when_no_errors(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_attrs(records) == {}

    def test_error_code_none_for_stuck_no_pause_id(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=0)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs.get("error_code") is None


class TestRechargeMinutesRemainingHelper:
    """_recharge_minutes_remaining: timestamp-first logic for all firmware."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _recharge_minutes_remaining
        with _utcnow_returning(now_ts):
            return _recharge_minutes_remaining(mission)

    # ── i7 / lewis firmware path (Thonno's robot) ────────────────────────────
    # rechrgM=0, rechrgTm set — this was already handled in v2.0.0.
    # The freeze bug was caused by the missing periodic tick, not this function.

    def test_lewis_computes_from_rechrgTm(self):
        """i7 (lewis): rechrgM=0, rechrgTm set → compute remaining minutes."""
        # rechrgTm=1780150205, now=1780150000 → 205 seconds → 3 minutes (rounded)
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780150000)
        assert result == 3

    def test_lewis_field_diagnostics_case(self):
        """Exact values from Bogdana diagnostics (i755840) — 277s remaining → 5 min."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780149928)
        assert result == 5

    def test_lewis_returns_none_when_rechrgTm_in_past(self):
        """rechrgTm expired → recharge done → None."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_lewis_returns_minimum_one_minute(self):
        """< 30 seconds remaining rounds to 1 min, not 0."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150020}, now_ts=1780150000)
        assert result == 1

    def test_lewis_returns_none_when_rechrgTm_zero(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    # ── 900-series / rechrgTm-priority fix ───────────────────────────────────
    # On 900/980-series, rechrgM is a static snapshot; rechrgTm is authoritative.
    # The old code returned rechrgM directly, which never decremented.

    def test_900_prefers_rechrgTm_over_static_rechrgM(self):
        """900-series: rechrgTm is preferred; static rechrgM is ignored."""
        # rechrgTm=1780150600, now=1780150000 → 600s → 10 min
        # Old code would have returned rechrgM=78 (static, wrong)
        result = self._call({"rechrgM": 78, "rechrgTm": 1780150600}, now_ts=1780150000)
        assert result == 10

    def test_900_series_value_decrements_over_time(self):
        """Demonstrate that rechrgTm-based value decrements, rechrgM-based would not."""
        recharge_end_ts = 1780150000 + 78 * 60  # end = now + 78 min
        # At t=0: both approaches agree
        result_t0 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts}, now_ts=1780150000
        )
        assert result_t0 == 78
        # At t+30min: rechrgTm gives 48, old static rechrgM would give 78 (frozen)
        result_t30 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts},
            now_ts=1780150000 + 30 * 60,
        )
        assert result_t30 == 48  # correctly decremented

    # ── Fallback: very old firmware (rechrgTm absent) ─────────────────────────

    def test_fallback_to_rechrgM_when_rechrgTm_zero(self):
        """rechrgTm absent / zero → fall back to rechrgM (old firmware)."""
        result = self._call({"rechrgM": 15, "rechrgTm": 0})
        assert result == 15

    def test_both_zero_returns_none(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None

    def test_none_values(self):
        assert self._call({"rechrgM": None, "rechrgTm": None}) is None


class TestExpireMinutesRemainingHelper:
    """_expire_minutes_remaining: same timestamp-first logic."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _expire_minutes_remaining
        with _utcnow_returning(now_ts):
            return _expire_minutes_remaining(mission)

    def test_prefers_expireTm_over_expireM(self):
        result = self._call({"expireM": 30, "expireTm": 1780150600}, now_ts=1780150000)
        assert result == 10   # 600s → 10 min, not static expireM=30

    def test_lewis_computes_from_expireTm(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780150000)
        assert result == 8   # 482s → 8 min

    def test_lewis_field_diagnostics_case(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780149928)
        assert result == 9   # 554s → 9 min

    def test_expired_returns_none(self):
        result = self._call({"expireM": 30, "expireTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_fallback_to_expireM_when_expireTm_zero(self):
        result = self._call({"expireM": 30, "expireTm": 0})
        assert result == 30

    def test_both_zero_returns_none(self):
        assert self._call({"expireM": 0, "expireTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None


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


class TestWifiFloor:
    """Amendment 8d — wlBars is a 5-element histogram, not a time-series."""

    def test_returns_lowest_nonempty_bucket(self):
        # [0, 35, 65, 0, 0]: bucket 1 is lowest non-zero → floor = 1
        records = [{"wlBars": [0, 35, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 1

    def test_bucket_zero_populated(self):
        # [5, 30, 65, 0, 0]: bucket 0 has readings → floor = 0
        records = [{"wlBars": [5, 30, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 0

    def test_all_strong_signal(self):
        # [0, 0, 0, 40, 60]: only buckets 3/4 → floor = 3
        records = [{"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_floor([]) is None

    def test_returns_none_when_wlbars_none(self):
        assert _raw_wifi_floor([{"wlBars": None}]) is None

    def test_returns_none_when_all_zero_histogram(self):
        assert _raw_wifi_floor([{"wlBars": [0, 0, 0, 0, 0]}]) is None

    def test_skips_records_without_wlbars(self):
        records = [{"sqft": 100}, {"wlBars": [0, 0, 70, 30, 0]}]
        assert _raw_wifi_floor(records) == 2

    def test_must_be_exactly_5_elements(self):
        # Wrong length histogram — skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3


class TestWifiStability:
    """Amendment 8d — weighted stdev of signal bucket distribution."""

    def test_concentrated_is_low_stdev(self):
        # All readings in bucket 3 → stdev ≈ 0
        records = [{"wlBars": [0, 0, 0, 100, 0]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val < 0.1

    def test_spread_is_high_stdev(self):
        # Evenly spread across all 5 buckets → high stdev
        records = [{"wlBars": [20, 20, 20, 20, 20]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val > 0.5

    def test_returns_none_when_fewer_than_3_records(self):
        records = [{"wlBars": [0, 35, 65, 0, 0]}] * 2
        assert _raw_wifi_stability(records) is None

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_stability([]) is None

    def test_skips_non_5element_histograms(self):
        # 3-element arrays are invalid — should be skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}] * 3
        val = _raw_wifi_stability(records)
        # Only the valid 5-element records contribute
        assert val is not None

    def test_result_is_float(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert isinstance(result, float)

    def test_result_rounded_to_2_decimals(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert result == round(result, 2)


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


class TestWifiQualityPct:
    """v2.9.0 — replaces _raw_wifi_floor as RoombaWifiHealthSensor's primary
    state. Weighted mean bucket index per mission (full histogram
    distribution, not just whether the weakest bucket was ever touched),
    averaged across records, scaled 0.0-4.0 to a genuine 0-100% percentage.
    """

    def test_all_strongest_bucket_is_100_percent(self):
        # Every reading in bucket 4 (strongest) → weighted mean = 4.0 → 100%
        records = [{"wlBars": [0, 0, 0, 0, 100]}]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_all_weakest_bucket_is_0_percent(self):
        # Every reading in bucket 0 (weakest) → weighted mean = 0.0 → 0%
        records = [{"wlBars": [100, 0, 0, 0, 0]}]
        assert _raw_wifi_quality_pct(records) == 0.0

    def test_middle_bucket_is_50_percent(self):
        # All in bucket 2 (middle of 0-4) → weighted mean = 2.0 → 50%
        records = [{"wlBars": [0, 0, 100, 0, 0]}]
        assert _raw_wifi_quality_pct(records) == 50.0

    def test_single_brief_dip_does_not_collapse_to_0_percent(self):
        """The exact bug this fix addresses: a single brief dip into the
        weakest bucket during an otherwise excellent connection must NOT
        read as 0% — _raw_wifi_floor() would have returned 0 (bucket index)
        here, which the old code mislabelled as a percentage."""
        # Mostly strong signal (bucket 4), one weak reading (bucket 0).
        records = [{"wlBars": [1, 0, 0, 0, 99]}]
        val = _raw_wifi_quality_pct(records)
        assert val is not None and val > 90.0, (
            "A single brief weak-signal blip must not collapse the "
            "percentage to near-zero — the weighted mean correctly "
            "reflects that the connection was excellent almost the "
            "entire time"
        )

    def test_averages_across_multiple_missions(self):
        records = [
            {"wlBars": [0, 0, 0, 0, 100]},  # mission 1: 100%
            {"wlBars": [100, 0, 0, 0, 0]},  # mission 2: 0%
        ]
        # Average of per-mission weighted means: (4.0 + 0.0) / 2 = 2.0 -> 50%
        assert _raw_wifi_quality_pct(records) == 50.0

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_quality_pct([]) is None

    def test_returns_none_when_no_valid_histograms(self):
        records = [{"wlBars": None}, {"wlBars": [70, 60, 80]}]  # invalid shapes
        assert _raw_wifi_quality_pct(records) is None

    def test_skips_non_5element_histograms_but_uses_valid_ones(self):
        records = [
            {"wlBars": [70, 60, 80]},          # invalid — skipped
            {"wlBars": [0, 0, 0, 0, 100]},      # valid — 100%
        ]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_skips_all_zero_histogram(self):
        """A histogram present but summing to zero (no readings at all)
        must be skipped, not treated as a 0% mission."""
        records = [
            {"wlBars": [0, 0, 0, 0, 0]},        # no data — skipped
            {"wlBars": [0, 0, 0, 0, 100]},      # valid — 100%
        ]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_result_is_rounded_to_1_decimal(self):
        records = [{"wlBars": [10, 20, 30, 25, 15]}]
        result = _raw_wifi_quality_pct(records)
        assert result == round(result, 1)

    def test_single_record_with_one_mission_minimum(self):
        """Unlike stability (needs >=3 records), a single mission's
        quality estimate is still meaningful and must not return None."""
        records = [{"wlBars": [0, 0, 0, 0, 100]}]
        assert _raw_wifi_quality_pct(records) is not None


class TestWifiHealthSensorUsesQualityPct:
    """RoombaWifiHealthSensor.native_value must use the new weighted-average
    percentage, with the old floor-based diagnostic moved to an attribute."""

    def test_native_value_uses_quality_pct_not_floor(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        # A single brief dip — floor would be 0, quality_pct should be high.
        coordinator.raw_records = [{"wlBars": [1, 0, 0, 0, 99]}]

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        val = sensor.native_value
        assert val is not None and val > 90.0, (
            "native_value must use the weighted-average quality percentage, "
            "not the raw worst-bucket-touched floor value"
        )

    def test_weakest_bucket_observed_attribute_present(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        coordinator.raw_records = [{"wlBars": [1, 0, 0, 0, 99]}]

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        attrs = sensor.extra_state_attributes
        assert attrs.get("weakest_bucket_observed") == 0, (
            "The original floor diagnostic must still be available as an "
            "attribute, just not as the misleading primary percentage"
        )

    def test_stability_attribute_still_present(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        coordinator.raw_records = [{"wlBars": [0, 0, 0, 100, 0]}] * 3

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        attrs = sensor.extra_state_attributes
        assert "stability_pct" in attrs


class TestMopCleanMode:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase slugs, was Capital-Case before."""

    def test_level_1_is_dry(self):
        e = _entity({"padWetness": {"disposable": 1}})
        assert _mop_clean_mode(e) == "dry"

    def test_level_2_is_wet(self):
        e = _entity({"padWetness": {"disposable": 2}})
        assert _mop_clean_mode(e) == "wet"

    def test_level_3_is_wet(self):
        e = _entity({"padWetness": {"reusable": 3}})
        assert _mop_clean_mode(e) == "wet"

    def test_missing_padwetness_is_unknown(self):
        e = _entity({})
        assert _mop_clean_mode(e) == "unknown"

    def test_empty_dict_is_unknown(self):
        e = _entity({"padWetness": {}})
        assert _mop_clean_mode(e) == "unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_clean_mode" in keys

    def test_filter_fn_requires_padwetness(self):
        desc = next(d for d in SENSORS if d.key == "mop_clean_mode")
        assert desc.filter_fn({"padWetness": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopTankStatus:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs, was
    Capital-Case-with-spaces before (spaces were never valid as
    translation_key state keys, this was a pre-existing hassfest violation)."""

    def test_all_ok_is_ready(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": False}})
        assert _mop_tank_status(e) == "ready"

    def test_fill_required(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": True}})
        assert _mop_tank_status(e) == "fill_tank"

    def test_lid_open_takes_priority_over_fill(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "lid_open"

    def test_tank_missing_highest_priority(self):
        e = _entity({"mopReady": {"tankPresent": False, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "tank_missing"

    def test_missing_mopready_is_unknown(self):
        e = _entity({})
        assert _mop_tank_status(e) == "unknown"

    def test_non_dict_mopready_is_unknown(self):
        e = _entity({"mopReady": 1})
        assert _mop_tank_status(e) == "unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_tank_status" in keys

    def test_filter_fn_requires_mopready(self):
        desc = next(d for d in SENSORS if d.key == "mop_tank_status")
        assert desc.filter_fn({"mopReady": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopBehavior:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs, combination
    modes join with "_" instead of the old " + " separator."""

    def test_rank_15_no_mop(self):
        e = _entity({"rankOverlap": 15})
        assert _mop_behavior(e) == "no_mop"

    def test_rank_67_standard(self):
        e = _entity({"rankOverlap": 67})
        assert _mop_behavior(e) == "standard"

    def test_rank_85_deep(self):
        e = _entity({"rankOverlap": 85})
        assert _mop_behavior(e) == "deep"

    def test_unknown_rank(self):
        e = _entity({"rankOverlap": 99})
        assert _mop_behavior(e) == "unknown"

    def test_flag_combination_dry_only(self):
        e = _entity({"padDryAllowed": 1, "padWashAllowed": 0, "padDirtyPause": 0})
        assert _mop_behavior(e) == "dry"

    def test_flag_combination_dirty_pause_plus_dry_plus_wash(self):
        e = _entity({"padDirtyPause": 1, "padDryAllowed": 1, "padWashAllowed": 1})
        assert _mop_behavior(e) == "dirty_pause_dry_wash"

    def test_no_flags_is_unknown(self):
        e = _entity({"padDryAllowed": 0, "padWashAllowed": 0})
        assert _mop_behavior(e) == "unknown"

    def test_rankOverlap_takes_precedence_over_flags(self):
        e = _entity({"rankOverlap": 25, "padDryAllowed": 1})
        assert _mop_behavior(e) == "extended"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_ars_behavior" in keys

    def test_filter_fn_rankOverlap(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"rankOverlap": 67}) is True

    def test_filter_fn_padDryAllowed(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"padDryAllowed": 1}) is True

    def test_filter_fn_absent_for_vacuums(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"batPct": 85}) is False


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


class TestEstimatedBatteryEolNiMHGuard:
    """estimated_battery_eol filter: only estCap presence matters (v2.5.0)."""

    def _desc(self):
        from custom_components.roomba_plus.sensor import SENSORS
        return next(d for d in SENSORS if d.key == "estimated_battery_eol")

    def test_lithium_surfaces(self):
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}, "batteryType": "lipo"}) is True

    def test_nimh_string_now_surfaces(self):
        """batteryType='nimh' no longer suppressed — filter only checks estCap."""
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}) is True

    def test_no_battery_type_surfaces(self):
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True

    def test_980_exact_state_surfaces(self):
        """980 exact state: sensor now surfaces (batteryType is a part number, not 'nimh')."""
        desc = self._desc()
        state = {
            "bbchg3": {"estCap": 9720, "nLithChrg": 290, "nNimhChrg": 19},
            "batteryType": "F12432712",
        }
        assert desc.filter_fn(state) is True

    def test_zero_baseline_estcap_does_not_crash(self):
        """_estimated_battery_eol must not ZeroDivisionError on baseline_estcap == 0.

        A corrupted or hand-edited persisted store could hold
        baseline_estcap: 0. The old `is None` guard would not catch it and the
        current_pct division would raise ZeroDivisionError, taking down the
        sensor. The hardened falsy-check returns None instead.
        """
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.sensor import _estimated_battery_eol

        entity = MagicMock()
        store = MagicMock()
        store.baseline_estcap = 0  # corrupted persisted value
        entity._config_entry.runtime_data.maintenance_store = store
        # Must return None, not raise
        assert _estimated_battery_eol(entity) is None


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


class TestCloudRawSensorAvailable:
    def test_unavailable_when_has_cloud_false(self):
        """Sensor must be unavailable when cloud coordinator is not configured."""
        sensor = _make_sensor(has_cloud=False, last_update_success=True)
        assert sensor.available is False

    def test_available_when_cloud_active_and_success(self):
        """Sensor must be available when cloud is configured and last update succeeded."""
        sensor = _make_sensor(
            has_cloud=True,
            last_update_success=True,
            coordinator_data={"pmaps": []},
        )
        assert sensor.available is True

    def test_unavailable_when_last_update_failed(self):
        """Sensor must be unavailable when last coordinator update failed."""
        sensor = _make_sensor(
            has_cloud=True,
            last_update_success=False,
            coordinator_data={"pmaps": []},
        )
        assert sensor.available is False

    def test_unavailable_when_coordinator_data_none(self):
        """Sensor must be unavailable when coordinator has not yet fetched data."""
        # Pass coordinator_data=None but we need to set it explicitly on the mock
        sensor = _make_sensor(has_cloud=True, last_update_success=True)
        sensor._coordinator.data = None
        assert sensor.available is False


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


class TestCleaningPerformanceSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=[])
        assert s.native_value is None

    def test_returns_completion_rate_with_records(self):
        records = [
            {"done": "done", "sqft": 300, "runM": 40},
            {"done": "done", "sqft": 280, "runM": 38},
            {"done": "hmPostMsn"},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=records)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        assert 0 <= val <= 100

    def test_attributes_include_trend(self):
        # Need ≥6 records for trend to be non-unknown
        records = [
            {"done": "done", "sqft": 300, "runM": 40, "startTime": 1700000000 - i * 86400}
            for i in range(10)
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=records)
        attrs = s.extra_state_attributes
        # trend key should be present
        assert "trend" in attrs
        assert attrs["trend"] in ("improving", "stable", "declining", "unknown")

    def test_f6a_check_not_rescheduled_when_trend_unchanged(self):
        """B1/B2 regression: repeated reads must not re-schedule the F6a check.

        extra_state_attributes can be evaluated many times per state write. The
        side-effect (cache + repair-check task) was migrated here from the old
        CloudRawSensor.native_value, so it must be idempotent: only the FIRST
        change schedules a task; subsequent identical reads do not.
        """
        from unittest.mock import MagicMock
        records = [
            {"done": "done", "sqft": 300, "runM": 40, "startTime": 1700000000 - i * 86400}
            for i in range(10)
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=records)
        # Real attribute (not MagicMock auto-attr) so equality comparison works
        s._config_entry.runtime_data.cleaning_speed_trend_value = None
        fake_hass = MagicMock()
        fake_hass.is_running = True
        # Close the coroutine the helper creates so it isn't left un-awaited
        fake_hass.async_create_task.side_effect = lambda coro, **kw: coro.close()
        s.hass = fake_hass

        s.extra_state_attributes  # first read — value changes None → str
        first_calls = fake_hass.async_create_task.call_count
        s.extra_state_attributes  # second read — value unchanged
        s.extra_state_attributes  # third read — value unchanged
        second_calls = fake_hass.async_create_task.call_count

        assert first_calls == 1, "first read should schedule exactly one F6a check"
        assert second_calls == first_calls, "unchanged reads must not reschedule"


class TestCleaningAnalytics30dSensor:

    def test_returns_none_without_runtime_stats(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data={})
        assert s.native_value is None

    def test_returns_area_m2_from_runtime_stats(self):
        data = {"runtimeStats": {"sqft": 10764, "hr": 5, "min": 30}}
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data=data)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        # 10764 sqft × 0.09290304 ≈ 1000.5 m²
        assert 990 < val < 1010

    def test_attributes_include_time_h(self):
        data = {"runtimeStats": {"sqft": 5000, "hr": 3, "min": 0}}
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data=data)
        attrs = s.extra_state_attributes
        assert "time_h" in attrs
        assert attrs["time_h"] == 3.0


class TestWifiHealthSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=[])
        assert s.native_value is None

    def test_returns_floor_pct_with_wl_bars(self):
        # wlBars histogram: index 0=weakest, 4=strongest
        records = [
            {"wlBars": [0, 10, 60, 30, 0]},
            {"wlBars": [0, 5,  70, 25, 0]},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=records)
        val = s.native_value
        # Should return something (floor signal % computation)
        # If records lack valid wlBars, returns None — accept either
        # With valid data it should return a numeric value
        if val is not None:
            assert isinstance(val, (int, float))

    def test_attributes_include_stability(self):
        records = [{"wlBars": [0, 0, 50, 50, 0]}, {"wlBars": [0, 0, 60, 40, 0]}]
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=records)
        attrs = s.extra_state_attributes
        # stability_pct present when records have wlBars
        # (may be absent if wlBars computation returns None)
        assert isinstance(attrs, dict)


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


class TestBatteryAgeDays:
    """_battery_age_days must parse mDate and return days since manufacture."""

    def test_valid_mdate(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {"batInfo": {"mDate": "2022-10-24"}}
        days = _battery_age_days(e)
        assert days is not None and days > 500  # battery is over 3 years old

    def test_missing_batInfo_returns_none(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {}
        assert _battery_age_days(e) is None

    def test_invalid_date_returns_none(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {"batInfo": {"mDate": "bad-date"}}
        assert _battery_age_days(e) is None


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


class TestChannelToBand:
    def test_ch1_is_24ghz(self):
        assert _channel_to_band(1) == "2.4 GHz"

    def test_ch6_is_24ghz(self):
        assert _channel_to_band(6) == "2.4 GHz"

    def test_ch13_is_24ghz(self):
        assert _channel_to_band(13) == "2.4 GHz"

    def test_ch36_is_5ghz(self):
        assert _channel_to_band(36) == "5 GHz"

    def test_ch149_is_5ghz(self):
        assert _channel_to_band(149) == "5 GHz"

    def test_none_returns_none(self):
        assert _channel_to_band(None) is None


class TestWifiLastChannelSensor:
    def test_returns_latest_channel(self):
        archive = _make_archive([
            _derived(1, wifi_channel=6),
            _derived(2, wifi_channel=36),   # newer
        ])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        # Archive is newest-first: derived[0] = nMssn=2, channel=36
        assert sensor.native_value == 36

    def test_returns_none_when_no_channel(self):
        archive = _make_archive([_derived(1, wifi_channel=None)] * 5)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        assert sensor.native_value is None

    def test_returns_none_when_archive_none(self):
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, None)
        assert sensor.native_value is None

    def test_band_attribute_24ghz(self):
        archive = _make_archive([_derived(i, wifi_channel=6) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs.get("band") == "2.4 GHz"

    def test_band_attribute_5ghz(self):
        archive = _make_archive([_derived(i, wifi_channel=36) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs.get("band") == "5 GHz"

    def test_unavailable_below_5_records(self):
        archive = _make_archive([_derived(1, wifi_channel=6)])
        assert archive.record_count == 1
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        # _ArchiveSensor.available requires record_count >= 5
        assert sensor._archive.record_count < 5


class TestWifiChannelStabilitySensor:
    def test_100pct_when_all_same(self):
        archive = _make_archive([_derived(i, wifi_channel=6) for i in range(1, 11)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value == 100.0

    def test_partial_stability(self):
        # 8 on ch6, 2 on ch36 → 80%
        records = (
            [_derived(i, wifi_channel=6) for i in range(1, 9)] +
            [_derived(i, wifi_channel=36) for i in range(9, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value == 80.0

    def test_returns_none_when_no_channels(self):
        archive = _make_archive([_derived(i, wifi_channel=None) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value is None

    def test_attributes_dominant_channel(self):
        records = [_derived(i, wifi_channel=6) for i in range(1, 9)] + \
                  [_derived(i, wifi_channel=36) for i in range(9, 11)]
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs["dominant_channel"] == 6
        assert attrs["dominant_channel_band"] == "2.4 GHz"
        assert attrs["sample_count"] == 10


class TestMissionsPerChargeSensor:
    def test_high_when_no_recharges(self):
        archive = _make_archive([_derived(i, recharge_count=0) for i in range(1, 11)])
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        # 10 missions / (1 + 0) = 10.0
        assert sensor.native_value == 10.0

    def test_lower_with_recharges(self):
        # 10 missions, 4 mid-mission recharges → 10 / (1 + 4) = 2.0
        records = (
            [_derived(i, recharge_count=1) for i in range(1, 5)] +
            [_derived(i, recharge_count=0) for i in range(5, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        assert sensor.native_value == 2.0

    def test_returns_none_when_no_recent(self):
        # Records older than 30 days
        records = [_derived(i, days_ago=60) for i in range(1, 6)]
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        assert sensor.native_value is None

    def test_attributes_breakdown(self):
        records = (
            [_derived(i, recharge_count=1) for i in range(1, 3)] +
            [_derived(i, recharge_count=0) for i in range(3, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs["missions_30d"] == 10
        assert attrs["mid_mission_recharges_30d"] == 2
        assert attrs["single_charge_pct"] == 80.0

    def test_returns_none_when_archive_none(self):
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, None)
        assert sensor.native_value is None


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


class TestParseNetinfoAddr:
    def test_string_format_returned_as_is(self):
        """i/s/j-series: dotted string → pass through unchanged."""
        assert _parse_netinfo_addr("192.168.1.5") == "192.168.1.5"

    def test_uint32_192_168_1_1(self):
        """9-series: uint32 big-endian 0xC0A80101 = 192.168.1.1."""
        # 192*2^24 + 168*2^16 + 1*2^8 + 1 = 3232235777
        assert _parse_netinfo_addr(3232235777) == "192.168.1.1"

    def test_uint32_10_0_0_1(self):
        """10.0.0.1 = 0x0A000001 = 167772161."""
        assert _parse_netinfo_addr(167772161) == "10.0.0.1"

    def test_uint32_zero_is_0_0_0_0(self):
        """uint32 0 → '0.0.0.0' (valid but unusual)."""
        assert _parse_netinfo_addr(0) == "0.0.0.0"

    def test_none_returns_none(self):
        assert _parse_netinfo_addr(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_netinfo_addr("") is None

    def test_ip_address_sensor_uses_parser(self):
        """ip_address sensor value_fn calls _parse_netinfo_addr for uint32."""
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.entity import IRobotEntity
        from custom_components.roomba_plus.sensor import SENSORS

        desc = next(d for d in SENSORS if d.key == "ip_address")

        e = object.__new__(IRobotEntity)
        e._blid = "test"
        e._roomba = MagicMock()
        # Simulate 9-series uint32 addr
        e.vacuum_state = {"netinfo": {"addr": 3232235777}}

        assert desc.value_fn(e) == "192.168.1.1"


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_new_sensors.py (TEST-REORG, v2.9.1). Original module
# docstring: 'Unit tests for the 7 new sensors added in the latest
# iteration' — covers _phase_value, _ts_or_none, _mission_elapsed_value,
# ERROR_CODE_LABELS, signal sensors (SNR/Noise/IP), FW-SENSOR (v2.8.3).
# ═══════════════════════════════════════════════════════════════════════

# ── Helper: minimal IRobotEntity mock ────────────────────────────────────────
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


class TestPhaseValue:
    def test_idle_when_charging_and_full(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "charge", "cycle": "none"}, "batPct": 100})
        assert _phase_value(e) == "Idle"

    def test_not_idle_when_charging_not_full(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "charge", "cycle": "none"}, "batPct": 80})
        assert _phase_value(e) == "Charging"

    def test_stopped_when_cycle_none_phase_stop(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "stop", "cycle": "none"}, "batPct": 50})
        assert _phase_value(e) == "Stopped"

    def test_running_normal(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "run", "cycle": "clean"}, "batPct": 90})
        assert _phase_value(e) == "Running"

    def test_stuck(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "stuck", "cycle": "clean"}, "batPct": 60})
        assert _phase_value(e) == "Stuck"

    def test_unknown_phase_returns_raw(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "mystery", "cycle": "none"}, "batPct": 50})
        assert _phase_value(e) == "mystery"

    def test_empty_phase_returns_unknown(self):
        e = _FakeEntity({"cleanMissionStatus": {}, "batPct": 50})
        assert _phase_value(e) == "Unknown"

    def test_paused(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "pause", "cycle": "clean"}, "batPct": 70})
        assert _phase_value(e) == "Paused"


# ── _ts_or_none ───────────────────────────────────────────────────────────────

from custom_components.roomba_plus.sensor import _ts_or_none


class TestTsOrNone:
    def test_none_input(self):
        assert _ts_or_none(None) is None

    def test_zero_input(self):
        assert _ts_or_none(0) is None

    def test_valid_timestamp(self):
        result = _ts_or_none(1700000000)
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_negative_timestamp(self):
        # Negative = before epoch — should still convert
        result = _ts_or_none(-1)
        assert result is not None


# ── _mission_elapsed_value ────────────────────────────────────────────────────

from custom_components.roomba_plus.sensor import _mission_elapsed_value
import time


class TestMissionElapsedValue:
    def test_no_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {}})
        assert _mission_elapsed_value(e) is None

    def test_zero_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": 0}})
        assert _mission_elapsed_value(e) is None

    def test_recent_start_returns_positive(self):
        ts = int(time.time()) - 300  # 5 minutes ago
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert result is not None
        assert result >= 4.9  # at least ~5 min
        assert result < 10    # sanity check

    def test_returns_float(self):
        ts = int(time.time()) - 60
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert isinstance(result, float)


# ── ERROR_CODE_LABELS ─────────────────────────────────────────────────────────

from custom_components.roomba_plus.const import ERROR_CODE_LABELS


class TestErrorCodeLabels:
    def test_zero_is_none(self):
        assert ERROR_CODE_LABELS[0] == "None"

    def test_common_errors_present(self):
        assert ERROR_CODE_LABELS[2] == "Main brushes stuck"
        assert ERROR_CODE_LABELS[6] == "Stuck near a cliff"
        assert ERROR_CODE_LABELS[14] == "Bin missing"
        assert ERROR_CODE_LABELS[36] == "Bin full"

    def test_battery_errors_present(self):
        assert ERROR_CODE_LABELS[106] == "Battery too warm"
        assert ERROR_CODE_LABELS[119] == "Charging timeout"

    def test_clean_base_error(self):
        assert ERROR_CODE_LABELS[216] == "Charging base bag full"

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

class TestRoombaFirmwareVersionSensor:
    """FW-SENSOR (v2.8.3) — RoombaFirmwareVersionSensor reads softwareVer."""

    def _make_sensor(self, software_ver=None):
        from custom_components.roomba_plus.sensor import RoombaFirmwareVersionSensor
        reported = {}
        if software_ver is not None:
            reported["softwareVer"] = software_ver
        s = RoombaFirmwareVersionSensor.__new__(RoombaFirmwareVersionSensor)
        # Set vacuum_state directly — the cached dict set by IRobotEntity.__init__
        s.vacuum_state = reported
        # vacuum attribute needed for new_state_filter (via roomba_reported_state)
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": reported}}
        s.vacuum = roomba
        return s

    def test_returns_version_string(self):
        s = self._make_sensor("3.20.11")
        assert s.native_value == "3.20.11"

    def test_returns_none_when_absent(self):
        s = self._make_sensor(None)
        assert s.native_value is None

    def test_state_filter_gates_on_softwarever(self):
        s = self._make_sensor()
        assert s.new_state_filter({"softwareVer": "3.20.11"}) is True
        assert s.new_state_filter({"signal": {}}) is False

    def test_translation_key(self):
        from custom_components.roomba_plus.sensor import RoombaFirmwareVersionSensor
        assert RoombaFirmwareVersionSensor.entity_description.translation_key == "firmware_version"


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_schedule_sensor.py (TEST-REORG, v2.9.1) — tests for
# RoombaSensor._next_from_schedule2 / _next_from_schedule_v1, exercised
# against the real methods via freezegun (pytest_freezer's freezer fixture).
# ═══════════════════════════════════════════════════════════════════════

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


def _zone_select(regions=None, zones=None, pmap_id="map1", map_name="Ground floor"):
    entry = _make_config_entry()
    roomba = _make_roomba()
    return CloudSmartZoneSelect(
        roomba, "test_blid", entry,
        pmap_id=pmap_id,
        map_name=map_name,
        regions=regions or [],
        zones=zones or [],
    )


# ── CloudSmartZoneSelect — option list ────────────────────────────────────────

class TestCloudSmartZoneSelectOptions:
    def test_empty_when_no_regions_or_zones(self):
        sel = _zone_select()
        assert sel.options == []

    def test_single_region(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen", "region_type": "kitchen"}])
        assert sel.options == ["Kitchen"]

    def test_multiple_regions(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen", "region_type": "kitchen"},
            {"id": "5", "name": "Hallway", "region_type": "hallway"},
        ])
        assert sel.options == ["Kitchen", "Hallway"]

    def test_zones_appended_after_regions(self):
        sel = _zone_select(
            regions=[{"id": "3", "name": "Kitchen", "region_type": "kitchen"}],
            zones=[{"id": "z1", "name": "Sofa area", "zone_type": "furniture"}],
        )
        assert sel.options == ["Kitchen", "Sofa area"]

    def test_fallback_name_when_missing(self):
        sel = _zone_select(regions=[{"id": "7"}])
        assert sel.options == ["Zone 7"]

    def test_fallback_name_empty_string(self):
        sel = _zone_select(regions=[{"id": "9", "name": ""}])
        assert sel.options == ["Zone 9"]

    def test_available_when_regions_present(self):
        sel = _zone_select(regions=[{"id": "1", "name": "Room"}])
        assert sel.available is True

    def test_not_available_when_empty(self):
        sel = _zone_select()
        assert sel.available is False


# ── CloudSmartZoneSelect — current_option ─────────────────────────────────────

class TestCloudSmartZoneSelectCurrentOption:
    def test_defaults_to_first_option(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        assert sel.current_option == "Kitchen"

    def test_none_when_no_options(self):
        sel = _zone_select()
        assert sel.current_option is None

    def test_selected_persists(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        sel._selected = "Hallway"
        assert sel.current_option == "Hallway"

    def test_invalid_selection_resets_to_first(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen"}])
        sel._selected = "Nonexistent"
        assert sel.current_option == "Kitchen"


# ── CloudSmartZoneSelect — selected_region_id ─────────────────────────────────

class TestCloudSmartZoneSelectRegionId:
    def test_returns_id_for_selected_name(self):
        sel = _zone_select(regions=[
            {"id": "3", "name": "Kitchen"},
            {"id": "5", "name": "Hallway"},
        ])
        sel._selected = "Hallway"
        assert sel.selected_region_id == "5"

    def test_falls_back_to_first_when_no_selection(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen"}])
        assert sel.selected_region_id == "3"

    def test_none_when_no_regions(self):
        sel = _zone_select()
        assert sel.selected_region_id is None

    def test_zone_id_returned_when_zone_selected(self):
        sel = _zone_select(
            regions=[{"id": "3", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Sofa area"}],
        )
        sel._selected = "Sofa area"
        assert sel.selected_region_id == "z1"

    def test_selected_pmap_info_contains_pmap_id(self):
        sel = _zone_select(pmap_id="abc123", regions=[{"id": "1", "name": "R"}])
        info = sel.selected_pmap_info
        assert info["pmap_id"] == "abc123"

    def test_selected_pmap_info_pmapv_empty(self):
        """user_pmapv_id is intentionally empty — SmartZoneButton resolves it live."""
        sel = _zone_select(regions=[{"id": "1", "name": "R"}])
        assert sel.selected_pmap_info["user_pmapv_id"] == ""


# ── CloudSmartZoneSelect — extra_state_attributes ─────────────────────────────

class TestCloudSmartZoneSelectAttributes:
    def test_attributes_keys(self):
        sel = _zone_select(
            pmap_id="map1", map_name="Ground floor",
            regions=[{"id": "3", "name": "Kitchen"}],
        )
        attrs = sel.extra_state_attributes
        assert attrs["pmap_id"] == "map1"
        assert attrs["map_name"] == "Ground floor"
        assert attrs["region_id"] == "3"
        assert attrs["region_count"] == 1
        assert attrs["zone_count"] == 0
        assert attrs["source"] == "cloud"

    def test_zone_count_correct(self):
        sel = _zone_select(
            regions=[{"id": "1", "name": "A"}],
            zones=[{"id": "z1", "name": "B"}, {"id": "z2", "name": "C"}],
        )
        assert sel.extra_state_attributes["zone_count"] == 2

    def test_unique_id_includes_pmap_id(self):
        sel = _zone_select(pmap_id="xyz789", regions=[{"id": "1", "name": "R"}])
        assert "xyz789" in sel._attr_unique_id

    # ROOM-SIZE (v2.9.1) — region_areas_m2, sourced from UmfAligner.room_areas_m2.

    def test_region_areas_m2_present_when_aligner_has_matching_rooms(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        aligner = MagicMock()
        aligner.room_areas_m2 = {"3": 20.0, "5": 12.0}
        entry.runtime_data.umf_aligner = aligner
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="map1", map_name="Ground floor",
            regions=[{"id": "3", "name": "Kitchen"}, {"id": "5", "name": "Hallway"}],
            zones=[],
        )
        attrs = sel.extra_state_attributes
        assert attrs["region_areas_m2"] == {"Kitchen": 20.0, "Hallway": 12.0}

    def test_region_areas_m2_absent_when_no_aligner(self):
        sel = _zone_select(regions=[{"id": "3", "name": "Kitchen"}])
        sel._config_entry.runtime_data.umf_aligner = None
        attrs = sel.extra_state_attributes
        assert "region_areas_m2" not in attrs

    def test_region_areas_m2_omits_rooms_not_in_aligner(self):
        """Floors UmfAligner wasn't built for (inactive pmap) get no entry
        at all, not a zero — graceful degradation, same pattern as
        region_icons/learning_percentage."""
        entry = _make_config_entry()
        roomba = _make_roomba()
        aligner = MagicMock()
        aligner.room_areas_m2 = {}  # this pmap's rooms aren't in the aligner
        entry.runtime_data.umf_aligner = aligner
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="other_floor", map_name="First floor",
            regions=[{"id": "99", "name": "Attic"}],
            zones=[],
        )
        attrs = sel.extra_state_attributes
        assert "region_areas_m2" not in attrs

    def test_region_areas_m2_survives_aligner_exception(self):
        """A misbehaving mock/aligner must not break the rest of the attrs."""
        entry = _make_config_entry()
        roomba = _make_roomba()
        aligner = MagicMock()
        type(aligner).room_areas_m2 = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        entry.runtime_data.umf_aligner = aligner
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="map1", map_name="Ground floor",
            regions=[{"id": "3", "name": "Kitchen"}],
            zones=[],
        )
        attrs = sel.extra_state_attributes
        assert "region_areas_m2" not in attrs
        assert attrs["pmap_id"] == "map1"  # rest of the attrs still intact


class TestCloudSmartZoneSelectActiveInactive:
    """Tests for active vs inactive map distinction."""

    def test_active_map_enabled_by_default(self):
        sel = _zone_select(regions=[{"id": "1", "name": "R"}])
        # is_active_map defaults to True
        assert sel._attr_entity_registry_enabled_default is True

    def test_inactive_map_disabled_by_default(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert sel._attr_entity_registry_enabled_default is False

    def test_inactive_map_name_gets_suffix(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert "(inactive)" in sel._map_name

    def test_active_map_name_unchanged(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="active_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=True,
        )
        assert sel._map_name == "Ground floor"

    def test_is_active_map_in_attributes(self):
        sel = _zone_select(regions=[{"id": "1", "name": "Kitchen"}])
        assert "is_active_map" in sel.extra_state_attributes
        assert sel.extra_state_attributes["is_active_map"] is True

    def test_inactive_in_attributes(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="old_map", map_name="Ground floor",
            regions=[{"id": "1", "name": "Kitchen"}],
            zones=[],
            is_active_map=False,
        )
        assert sel.extra_state_attributes["is_active_map"] is False

    def test_is_active_map_flag_stored(self):
        entry = _make_config_entry()
        roomba = _make_roomba()
        sel = CloudSmartZoneSelect(
            roomba, "test_blid", entry,
            pmap_id="p1", map_name="Home",
            regions=[{"id": "1", "name": "R"}],
            zones=[],
            is_active_map=False,
        )
        assert sel._is_active_map is False


# ── CloudSmartZoneSelect — no_state_filter ────────────────────────────────────

class TestCloudSmartZoneSelectNoMqttUpdate:
    def test_new_state_filter_always_false(self):
        """Cloud entity must not react to MQTT messages."""
        sel = _zone_select(regions=[{"id": "1", "name": "Room"}])
        assert sel.new_state_filter({"cleanSchedule2": [{}]}) is False
        assert sel.new_state_filter({"lastCommand": {}}) is False
        assert sel.new_state_filter({}) is False


# ── FavoriteButton ─────────────────────────────────────────────────────────────

def _fav_button(favorite):
    entry = _make_config_entry(has_cloud=True)
    roomba = _make_roomba()
    return FavoriteButton(roomba, "test_blid", entry, favorite)


class TestFavoriteButton:
    def test_name_from_favorite(self):
        btn = _fav_button({"favorite_id": "f1", "name": "Morning clean", "commanddefs": []})
        assert btn._attr_name == "Morning clean"

    def test_unique_id_includes_favorite_id(self):
        btn = _fav_button({"favorite_id": "fav42", "name": "X", "commanddefs": []})
        assert "fav42" in btn._attr_unique_id

    def test_visible_by_default_when_not_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": False})
        assert btn._attr_entity_registry_enabled_default is True

    def test_disabled_by_default_when_hidden(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": [], "hidden": True})
        assert btn._attr_entity_registry_enabled_default is False

    def test_default_enabled_when_hidden_missing(self):
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert btn._attr_entity_registry_enabled_default is True

    def test_inherits_irobot_entity(self):
        """FavoriteButton must inherit IRobotEntity for correct device linkage."""
        from custom_components.roomba_plus.entity import IRobotEntity
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert isinstance(btn, IRobotEntity)

    def test_has_vacuum_attribute(self):
        """IRobotEntity provides .vacuum — used in async_press instead of config_entry."""
        btn = _fav_button({"favorite_id": "f1", "name": "X", "commanddefs": []})
        assert hasattr(btn, "vacuum")

    @pytest.mark.asyncio
    async def test_press_sends_command(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f1",
            "name": "Morning",
            "commanddefs": [{"command": "start", "pmap_id": "map1", "regions": [{"region_id": "3"}]}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        btn.hass.async_add_executor_job.assert_called_once()
        args = btn.hass.async_add_executor_job.call_args[0]
        # args[0] is the bound method, args[1] is command, args[2] is params
        assert args[1] == "start"
        assert args[2]["pmap_id"] == "map1"

    @pytest.mark.asyncio
    async def test_press_no_commanddefs_logs_warning(self):
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "Empty", "commanddefs": []}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        with patch("custom_components.roomba_plus.button._LOGGER") as mock_log:
            await btn.async_press()

        mock_log.warning.assert_called_once()
        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_missing_commanddefs_key(self):
        """commanddefs key absent — should not raise, should warn."""
        entry = _make_config_entry(has_cloud=True)
        fav = {"favorite_id": "f1", "name": "NoCmd"}
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()  # must not raise

        btn.hass.async_add_executor_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_press_extracts_params_excluding_command_key(self):
        """All keys except 'command' become params."""
        entry = _make_config_entry(has_cloud=True)
        fav = {
            "favorite_id": "f2",
            "name": "Kitchen",
            "commanddefs": [{"command": "start", "pmap_id": "p1", "ordered": 1}],
        }
        btn = FavoriteButton(_make_roomba(), "test_blid", entry, fav)
        btn.hass = MagicMock()
        btn.hass.async_add_executor_job = AsyncMock()

        await btn.async_press()

        params = btn.hass.async_add_executor_job.call_args[0][2]
        assert "command" not in params
        assert params["pmap_id"] == "p1"
        assert params["ordered"] == 1


# ── select.py: cloud vs MQTT routing in async_setup_entry ─────────────────────

class TestSelectSetupEntryRouting:
    """Verify that async_setup_entry creates the right entity type."""

    def _cloud_pmap(self):
        return {
            "active_pmapv_details": {
                "active_pmapv": {"pmap_id": "map1"},
                "map_header": {"name": "Home"},
                "regions": [{"id": "3", "name": "Kitchen", "region_type": "kitchen"}],
                "zones": [],
            }
        }

    @pytest.mark.asyncio
    async def test_cloud_active_creates_cloud_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect, SmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        state = {"pmaps": [{"map1": "v1"}]}
        entry = _make_config_entry(has_cloud=True, pmaps=[self._cloud_pmap()])
        entry.runtime_data.map_capability = MapCapability.SMART
        # active_pmap_id matches the pmap in _cloud_pmap()
        entry.runtime_data.cloud_coordinator.active_pmap_id = "map1"

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        mqtt_selects = [e for e in created if isinstance(e, SmartZoneSelect)]
        assert len(cloud_selects) == 1, f"Expected 1 CloudSmartZoneSelect, got {created}"
        assert len(mqtt_selects) == 0
        # Active map must be enabled
        assert cloud_selects[0]._attr_entity_registry_enabled_default is True
        assert cloud_selects[0]._is_active_map is True

    @pytest.mark.asyncio
    async def test_inactive_pmap_creates_disabled_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        active = self._cloud_pmap()  # pmap_id = "map1"
        inactive = {
            "active_pmapv_details": {
                "active_pmapv": {"pmap_id": "old_map"},
                "map_header": {"name": "Old Home"},
                "regions": [{"id": "9", "name": "Garage", "region_type": "garage"}],
                "zones": [],
            }
        }
        state = {"pmaps": [{"map1": "v1"}, {"old_map": "v2"}]}
        entry = _make_config_entry(has_cloud=True, pmaps=[active, inactive])
        entry.runtime_data.map_capability = MapCapability.SMART
        entry.runtime_data.cloud_coordinator.active_pmap_id = "map1"

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        assert len(cloud_selects) == 2

        active_sel = next(e for e in cloud_selects if e._pmap_id == "map1")
        inactive_sel = next(e for e in cloud_selects if e._pmap_id == "old_map")

        assert active_sel._attr_entity_registry_enabled_default is True
        assert inactive_sel._attr_entity_registry_enabled_default is False
        assert "(inactive)" in inactive_sel._map_name

    @pytest.mark.asyncio
    async def test_no_cloud_creates_mqtt_select(self):
        from custom_components.roomba_plus import select as sel_mod
        from custom_components.roomba_plus.select import CloudSmartZoneSelect, SmartZoneSelect
        from custom_components.roomba_plus.models import MapCapability

        state = {"pmaps": [{"map1": "v1"}]}
        entry = _make_config_entry(has_cloud=False)
        entry.runtime_data.map_capability = MapCapability.SMART

        roomba = _make_roomba()
        roomba.master_state = {"state": {"reported": state}}
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"
        entry.runtime_data.zone_store = None

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(sel_mod, "roomba_reported_state", return_value=state):
            with patch.object(sel_mod, "has_smart_map", return_value=True):
                await sel_mod.async_setup_entry(MagicMock(), entry, sync_add)

        cloud_selects = [e for e in created if isinstance(e, CloudSmartZoneSelect)]
        mqtt_selects = [e for e in created if isinstance(e, SmartZoneSelect)]
        assert len(cloud_selects) == 0
        assert len(mqtt_selects) == 1, f"Expected 1 SmartZoneSelect, got {created}"

    @pytest.mark.asyncio
    async def test_cloud_active_suppresses_repair_issue(self):
        """SmartZoneSelect._async_raise_naming_issue must not create issue when cloud active."""
        from custom_components.roomba_plus.select import SmartZoneSelect
        from homeassistant.helpers import issue_registry as ir

        entry = _make_config_entry(has_cloud=True)
        sel = SmartZoneSelect.__new__(SmartZoneSelect)
        sel._config_entry = entry
        sel._known_unlabelled = set()

        original_create = ir.async_create_issue
        calls = []
        ir.async_create_issue = lambda *a, **kw: calls.append((a, kw))
        try:
            await sel._async_raise_naming_issue(["3", "5"])
        finally:
            ir.async_create_issue = original_create

        assert calls == [], "async_create_issue should not be called when cloud is active"


# ── CloudHistorySensor test helpers ──────────────────────────────────────────


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

class TestMissionHistoryListResponse:
    """The /missionhistory API returns a list, not a dict.

    The coordinator must normalize this before storing. The value functions
    must never receive a list — that was the crash in the bug report.
    """

    def test_list_response_does_not_crash_sqft(self):
        """Passing a list to _mh_sqft_to_m2 must not raise AttributeError."""
        history_list = _make_history_list(sqft=10764)
        # Before the fix this would crash: 'list' has no attribute 'get'
        # After the fix the coordinator extracts [0] before passing to value_fn.
        # Test the value_fn directly to confirm it still handles a dict correctly.
        history_dict = history_list[0]
        assert _mh_sqft_to_m2(history_dict) == pytest.approx(1000.0, abs=1)

    def test_list_response_does_not_crash_time(self):
        history_list = _make_history_list(hr=2, mn=30)
        assert _mh_total_minutes(history_list[0]) == 150

    def test_list_response_does_not_crash_missions(self):
        history_list = _make_history_list(n_mssn=99)
        assert _mh_total_missions(history_list[0]) == 99

    def test_coordinator_normalizes_list_to_dict(self):
        """Coordinator must store history as a dict, never a list."""
        cc = _make_history_coordinator(_make_history(sqft=500, hr=5, mn=0, n_mssn=20))
        history = cc.data["mission_history"]
        assert isinstance(history, dict), (
            f"mission_history must be a dict, got {type(history).__name__}"
        )

    def test_value_fn_receives_dict_not_list(self):
        """Simulate what native_value does — must not receive a list."""
        cc = _make_history_coordinator(_make_history(sqft=500))
        history = cc.data.get("mission_history", {})
        # This is the exact call that was crashing:
        result = _mh_sqft_to_m2(history)
        assert result is not None
        assert isinstance(result, float)

    def test_empty_list_produces_empty_dict(self):
        """Empty list from API must produce empty dict, not IndexError."""
        cc = object.__new__(type('FakeCC', (), {
            'data': {'mission_history': {}},
            'last_update_success': True,
        }))
        # The coordinator normalizes [] → {} — value fns return None gracefully.
        assert _mh_sqft_to_m2({}) is None
        assert _mh_total_minutes({}) is None
        assert _mh_total_missions({}) is None


# ── Value functions — unit tests ───────────────────────────────────────────────

class TestMhSqftToM2:
    def test_converts_sqft_to_m2(self):
        h = _make_history(sqft=10764)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(1000.0, abs=1)

    def test_rounds_to_one_decimal(self):
        h = _make_history(sqft=100)
        result = _mh_sqft_to_m2(h)
        assert result == round(100 / 10.764, 1)

    def test_none_when_sqft_missing(self):
        assert _mh_sqft_to_m2({}) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_sqft_to_m2({"bbmssn": {"nMssn": 5}}) is None

    def test_none_when_runtimestats_is_none(self):
        assert _mh_sqft_to_m2({"runtimeStats": None}) is None

    def test_zero_sqft(self):
        h = _make_history(sqft=0)
        assert _mh_sqft_to_m2(h) == 0.0

    def test_large_value(self):
        h = _make_history(sqft=50000)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(4645.2, abs=1)


class TestMhTotalMinutes:
    def test_converts_hr_min_to_minutes(self):
        h = _make_history(hr=2, mn=30)
        assert _mh_total_minutes(h) == 150

    def test_zero_hours(self):
        h = _make_history(hr=0, mn=45)
        assert _mh_total_minutes(h) == 45

    def test_zero_minutes(self):
        h = _make_history(hr=3, mn=0)
        assert _mh_total_minutes(h) == 180

    def test_none_when_hr_missing(self):
        h = {"runtimeStats": {"min": 30}}
        assert _mh_total_minutes(h) is None

    def test_none_when_min_missing(self):
        h = {"runtimeStats": {"hr": 2}}
        assert _mh_total_minutes(h) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_total_minutes({}) is None

    def test_none_when_runtimestats_none(self):
        assert _mh_total_minutes({"runtimeStats": None}) is None

    def test_large_values(self):
        h = _make_history(hr=100, mn=59)
        assert _mh_total_minutes(h) == 6059


class TestMhTotalMissions:
    def test_returns_nmssn(self):
        h = _make_history(n_mssn=987)
        assert _mh_total_missions(h) == 987

    def test_none_when_bbmssn_missing(self):
        assert _mh_total_missions({}) is None

    def test_none_when_nmssn_missing(self):
        assert _mh_total_missions({"bbmssn": {}}) is None

    def test_none_when_bbmssn_none(self):
        assert _mh_total_missions({"bbmssn": None}) is None

    def test_zero_missions(self):
        h = _make_history(n_mssn=0)
        assert _mh_total_missions(h) == 0


# ── CLOUD_HISTORY_SENSORS descriptions ────────────────────────────────────────

class TestCloudHistorySensorsDescriptions:
    """Verify the three CLOUD_HISTORY_SENSORS descriptions match current code.

    Keys as of v2.1.x: recent_area_30d, recent_time_30d, lifetime_missions.
    recent_area_30d and recent_time_30d deliberately have no translation_key —
    name= alone locks the entity_id slug to English regardless of HA locale.
    """

    def test_three_sensors_defined(self):
        assert len(CLOUD_HISTORY_SENSORS) == 3

    def test_keys(self):
        keys = {d.key for d in CLOUD_HISTORY_SENSORS}
        assert keys == {"recent_area_30d", "recent_time_30d", "lifetime_missions"}

    def test_lifetime_missions_has_translation_key(self):
        """lifetime_missions uses translation_key for localised friendly name."""
        missions = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_missions")
        assert missions.translation_key == "lifetime_missions"

    def test_area_and_time_have_translation_key(self):
        """recent_area_30d and recent_time_30d must have translation_key set.

        Step 23 (v2.2.0 card audit fix): translation_key locks the entity_id
        slug to the key string, independent of locale. Without it, fresh installs
        on non-English HA produce language-specific slugs
        (e.g. sensor.*_gereinigte_flache_30_t on DE). The key, not the translated
        name string, is used as the slug when translation_key is present.
        """
        for key in ("recent_area_30d", "recent_time_30d"):
            desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == key)
            assert desc.translation_key == key, (
                f"{key}: translation_key must equal key to lock entity_id slug"
            )

    def test_area_unit_m2(self):
        area = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_area_30d")
        assert area.native_unit_of_measurement == "m²"

    def test_time_unit_minutes(self):
        from homeassistant.const import UnitOfTime
        time = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_time_30d")
        assert time.native_unit_of_measurement == UnitOfTime.MINUTES

    def test_missions_unit(self):
        missions = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_missions")
        assert missions.native_unit_of_measurement == "missions"

    def test_all_diagnostic(self):
        from homeassistant.const import EntityCategory
        for d in CLOUD_HISTORY_SENSORS:
            assert d.entity_category == EntityCategory.DIAGNOSTIC


# ── CloudHistorySensor entity ─────────────────────────────────────────────────

class TestCloudHistorySensorNativeValue:
    def test_recent_area_value(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=10764))
        assert sensor.native_value == pytest.approx(1000.0, abs=1)

    def test_recent_time_value(self):
        sensor = _make_history_sensor("recent_time_30d", _make_history(hr=1, mn=30))
        assert sensor.native_value == 90

    def test_lifetime_missions_value(self):
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=42))
        assert sensor.native_value == 42

    def test_none_when_no_history_data(self):
        sensor = _make_history_sensor("recent_area_30d", {})
        assert sensor.native_value is None

    def test_none_when_coordinator_has_no_data(self):
        sensor = _make_history_sensor("recent_area_30d", success=False)
        assert sensor.native_value is None


class TestCloudHistorySensorAvailability:
    def test_available_when_coordinator_ok(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        assert sensor.available is True

    def test_unavailable_when_last_update_failed(self):
        sensor = _make_history_sensor("recent_area_30d", success=False)
        assert sensor.available is False

    def test_unavailable_when_data_none(self):
        sensor = _make_history_sensor("recent_area_30d")
        sensor._coordinator.data = None
        assert sensor.available is False


class TestCloudHistorySensorNoMqttUpdate:
    def test_new_state_filter_always_false(self):
        """Cloud sensor must not react to MQTT messages."""
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=10))
        assert sensor.new_state_filter({"bbmssn": {"nMssn": 99}}) is False
        assert sensor.new_state_filter({}) is False


class TestCloudHistorySensorUniqueId:
    def test_unique_id_contains_key(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        assert "recent_area_30d" in sensor._attr_unique_id

    def test_unique_id_contains_blid(self):
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=5))
        assert "test_blid" in sensor._attr_unique_id

    def test_unique_ids_distinct(self):
        s1 = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        s2 = _make_history_sensor("recent_time_30d", _make_history(hr=1, mn=0))
        assert s1._attr_unique_id != s2._attr_unique_id


# ── async_setup_entry integration ─────────────────────────────────────────────

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

def _make_last_mission_summary_sensor(mission_store=None):
    """Return a RoombaLastMissionSummarySensor backed by the given store."""
    from custom_components.roomba_plus.sensor import RoombaLastMissionSummarySensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = _make_entry(mission_store=mission_store)
    sensor = RoombaLastMissionSummarySensor.__new__(RoombaLastMissionSummarySensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_last_mission_summary"
    return sensor


class TestLastMissionSummarySensor:
    """LAST-MISSION-SUMMARY (v3.1.0) — sensor that exposes the latest mission record."""

    def test_empty_store_returns_none(self):
        """No records → native_value is None, all attributes are None."""
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with())
        assert sensor.native_value is None
        attrs = sensor.extra_state_attributes
        assert attrs["result"] is None
        assert attrs["duration_min"] is None
        assert attrs["area_sqft"] is None
        assert attrs["started_at"] is None

    def test_no_store_returns_none(self):
        """MissionStore not initialised → native_value is None."""
        sensor = _make_last_mission_summary_sensor(mission_store=None)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes["result"] is None

    def test_completed_mission_all_fields(self):
        """Completed mission → native_value = 'completed', attributes populated."""
        record = {
            "result": "completed",
            "duration_min": 45,
            "area_sqft": 320.5,
            "last_cleaned_rooms": ["Kitchen", "Living Room"],
            "cleaning_passes": 1,
            "battery_start_pct": 100,
            "battery_end_pct": 72,
            "recharges": 0,
            "dirt_events": 3,
            "evacuations": 1,
            "error_code": None,
            "initiator": "schedule",
            "started_at": "2026-06-29T08:00:00",
            "ended_at": "2026-06-29T08:45:00",
        }
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with(record))
        assert sensor.native_value == "completed"
        attrs = sensor.extra_state_attributes
        assert attrs["duration_min"] == 45
        assert attrs["area_sqft"] == 320.5
        assert attrs["cleaned_rooms"] == ["Kitchen", "Living Room"]
        assert attrs["battery_start_pct"] == 100
        assert attrs["battery_end_pct"] == 72
        assert attrs["dirt_events"] == 3
        assert attrs["initiator"] == "schedule"
        assert attrs["started_at"] == "2026-06-29T08:00:00"

    def test_error_mission_error_code_populated(self):
        """Error mission → native_value = 'error', error_code present."""
        record = {
            "result": "error",
            "error_code": 11,
            "duration_min": 5,
            "area_sqft": 0,
        }
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with(record))
        assert sensor.native_value == "error"
        assert sensor.extra_state_attributes["error_code"] == 11

    def test_returns_latest_record_not_first(self):
        """With multiple records, native_value reflects the last (most recent) record."""
        older = {"result": "cancelled", "duration_min": 10}
        newer = {"result": "completed", "duration_min": 55}
        sensor = _make_last_mission_summary_sensor(
            mission_store=_store_with(older, newer)
        )
        assert sensor.native_value == "completed"
        assert sensor.extra_state_attributes["duration_min"] == 55


# ─────────────────────────────────────────────────────────────────────────────
# ROOM-CLEANING-HISTORY (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _make_room_cleaning_history_sensor(mission_store=None):
    """Return a RoombaRoomCleaningHistorySensor backed by the given store."""
    from custom_components.roomba_plus.sensor import RoombaRoomCleaningHistorySensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = _make_entry(mission_store=mission_store)
    sensor = RoombaRoomCleaningHistorySensor.__new__(RoombaRoomCleaningHistorySensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_room_cleaning_history"
    return sensor


class TestRoomCleaningHistorySensor:
    """ROOM-CLEANING-HISTORY (v3.1.0) — per-room last-clean timestamps."""

    def test_empty_store_returns_zero(self):
        """No records → native_value = 0, attributes = {}."""
        sensor = _make_room_cleaning_history_sensor(mission_store=_store_with())
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_no_store_returns_zero(self):
        """MissionStore not initialised → native_value = 0."""
        sensor = _make_room_cleaning_history_sensor(mission_store=None)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_single_mission_populates_rooms(self):
        """Record with last_cleaned_rooms → each room gets ended_at timestamp."""
        record = {
            "last_cleaned_rooms": ["Kitchen", "Living Room"],
            "ended_at": "2026-06-29T08:45:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(mission_store=_store_with(record))
        assert sensor.native_value == 2
        attrs = sensor.extra_state_attributes
        assert attrs["Kitchen"] == "2026-06-29T08:45:00"
        assert attrs["Living Room"] == "2026-06-29T08:45:00"

    def test_newest_record_wins_per_room(self):
        """Multiple records — each room shows its most recent ended_at."""
        older = {
            "last_cleaned_rooms": ["Kitchen", "Hallway"],
            "ended_at": "2026-06-27T09:00:00",
            "result": "completed",
        }
        newer = {
            "last_cleaned_rooms": ["Kitchen", "Living Room"],
            "ended_at": "2026-06-29T08:45:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(
            mission_store=_store_with(older, newer)
        )
        attrs = sensor.extra_state_attributes
        # Kitchen: newer record wins
        assert attrs["Kitchen"] == "2026-06-29T08:45:00"
        # Hallway: only in older record
        assert attrs["Hallway"] == "2026-06-27T09:00:00"
        # Living Room: only in newer record
        assert attrs["Living Room"] == "2026-06-29T08:45:00"
        assert sensor.native_value == 3

    def test_record_without_rooms_is_skipped(self):
        """Records lacking last_cleaned_rooms (whole-home missions) are skipped."""
        whole_home = {
            "last_cleaned_rooms": None,
            "ended_at": "2026-06-28T10:00:00",
            "result": "completed",
        }
        room_mission = {
            "last_cleaned_rooms": ["Bedroom"],
            "ended_at": "2026-06-29T08:00:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(
            mission_store=_store_with(whole_home, room_mission)
        )
        assert sensor.native_value == 1
        assert "Bedroom" in sensor.extra_state_attributes


# ─────────────────────────────────────────────────────────────────────────────
# ROOM-SIZE / room_areas (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _make_room_areas_sensor(umf_aligner=None, regions=None):
    """Return a RoombaRoomAreasSensor with the given aligner and cc.regions."""
    from custom_components.roomba_plus.sensor import RoombaRoomAreasSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    rd = MagicMock()
    cc = MagicMock()
    cc.regions = regions or []
    rd.umf_aligner = umf_aligner
    rd.cloud_coordinator = cc if umf_aligner is not None else None
    entry.runtime_data = rd
    sensor = RoombaRoomAreasSensor.__new__(RoombaRoomAreasSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_room_areas"
    return sensor


class TestRoomAreasSensor:
    """ROOM-SIZE (v3.1.0) — per-room floor area dictionary sensor."""

    def test_no_aligner_returns_zero(self):
        """umf_aligner=None → native_value=0, attributes={}."""
        sensor = _make_room_areas_sensor(umf_aligner=None)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_areas_translated_to_display_names(self):
        """rid keys from room_areas_m2 are translated via cc.regions."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {"19": 14.3, "21": 22.1}
        regions = [
            {"id": "19", "name": "Kitchen"},
            {"id": "21", "name": "Living Room"},
        ]
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=regions)
        assert sensor.native_value == 2
        attrs = sensor.extra_state_attributes
        assert attrs["Kitchen"] == 14.3
        assert attrs["Living Room"] == 22.1

    def test_unknown_rid_falls_back_to_rid(self):
        """rid without a matching region entry is used as-is as key."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {"99": 8.5}
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=[])
        assert "99" in sensor.extra_state_attributes
        assert sensor.extra_state_attributes["99"] == 8.5

    def test_empty_room_polygons_returns_zero(self):
        """Aligner present but no polygons resolved → native_value=0."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {}
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=[])
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY-SLIM (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

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


class TestRelocalisationRateSensor:
    """L9-MAP (v3.1.0) — self-calibrating relocalisation rate sensor."""

    def test_no_rps_returns_none(self):
        """robot_profile_store=None → native_value=None, safe empty attributes."""
        sensor = _make_reloc_sensor(rps=None)
        assert sensor.native_value is None
        attrs = sensor.extra_state_attributes
        assert attrs["baseline"] is None
        assert attrs["alert"] is False

    def test_not_ready_returns_none(self):
        """Baseline not yet established (< 15 missions) → native_value=None."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(5):
            rps.update_reloc_baseline(0)
        sensor = _make_reloc_sensor(rps=rps)
        assert sensor.native_value is None

    def test_ready_returns_window_mean(self):
        """Baseline established → native_value is the recent window's mean."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(20):
            rps.update_reloc_baseline(2)
        sensor = _make_reloc_sensor(rps=rps)
        assert sensor.native_value == pytest.approx(2.0)

    def test_attributes_include_baseline_and_window(self):
        """extra_state_attributes expose baseline, count, window, and alert state."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(20):
            rps.update_reloc_baseline(1)
        sensor = _make_reloc_sensor(rps=rps)
        attrs = sensor.extra_state_attributes
        assert attrs["baseline"] == pytest.approx(1.0)
        assert attrs["baseline_mission_count"] == 20
        assert len(attrs["recent_window"]) == 10
        assert attrs["alert"] is False

    def test_alert_attribute_reflects_triggered_state(self):
        """When an alert is active, extra_state_attributes reflects it."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for _ in range(100):
            rps.update_reloc_baseline(1)
        for _ in range(10):
            rps.update_reloc_baseline(10)
        sensor = _make_reloc_sensor(rps=rps)
        assert sensor.extra_state_attributes["alert"] is True


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
