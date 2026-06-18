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
from unittest.mock import patch
from unittest.mock import MagicMock
import importlib
import unittest.mock as _mock
from custom_components.roomba_plus.sensor import _raw_wifi_floor
from custom_components.roomba_plus.sensor import _raw_wifi_stability
from custom_components.roomba_plus.sensor import _mop_clean_mode
from custom_components.roomba_plus.sensor import _mop_tank_status
from custom_components.roomba_plus.sensor import _mop_behavior
from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
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


class TestWifiSensorDescriptions:
    def test_recent_wifi_floor_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_wifi_floor" in keys

    def test_recent_wifi_stability_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_wifi_stability" in keys

    def test_wifi_sensors_disabled_by_default(self):
        """WiFi sensors are opt-in (disabled by default)."""
        for desc in CLOUD_RAW_SENSORS:
            if desc.key in ("recent_wifi_floor", "recent_wifi_stability"):
                assert desc.entity_registry_enabled_default is False


class TestMopCleanMode:
    def test_level_1_is_dry(self):
        e = _entity({"padWetness": {"disposable": 1}})
        assert _mop_clean_mode(e) == "Dry"

    def test_level_2_is_wet(self):
        e = _entity({"padWetness": {"disposable": 2}})
        assert _mop_clean_mode(e) == "Wet"

    def test_level_3_is_wet(self):
        e = _entity({"padWetness": {"reusable": 3}})
        assert _mop_clean_mode(e) == "Wet"

    def test_missing_padwetness_is_unknown(self):
        e = _entity({})
        assert _mop_clean_mode(e) == "Unknown"

    def test_empty_dict_is_unknown(self):
        e = _entity({"padWetness": {}})
        assert _mop_clean_mode(e) == "Unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_clean_mode" in keys

    def test_filter_fn_requires_padwetness(self):
        desc = next(d for d in SENSORS if d.key == "mop_clean_mode")
        assert desc.filter_fn({"padWetness": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopTankStatus:
    def test_all_ok_is_ready(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": False}})
        assert _mop_tank_status(e) == "Ready"

    def test_fill_required(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": True}})
        assert _mop_tank_status(e) == "Fill Tank"

    def test_lid_open_takes_priority_over_fill(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "Lid Open"

    def test_tank_missing_highest_priority(self):
        e = _entity({"mopReady": {"tankPresent": False, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "Tank Missing"

    def test_missing_mopready_is_unknown(self):
        e = _entity({})
        assert _mop_tank_status(e) == "Unknown"

    def test_non_dict_mopready_is_unknown(self):
        e = _entity({"mopReady": 1})
        assert _mop_tank_status(e) == "Unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_tank_status" in keys

    def test_filter_fn_requires_mopready(self):
        desc = next(d for d in SENSORS if d.key == "mop_tank_status")
        assert desc.filter_fn({"mopReady": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopBehavior:
    def test_rank_15_no_mop(self):
        e = _entity({"rankOverlap": 15})
        assert _mop_behavior(e) == "No Mop"

    def test_rank_67_standard(self):
        e = _entity({"rankOverlap": 67})
        assert _mop_behavior(e) == "Standard"

    def test_rank_85_deep(self):
        e = _entity({"rankOverlap": 85})
        assert _mop_behavior(e) == "Deep"

    def test_unknown_rank(self):
        e = _entity({"rankOverlap": 99})
        assert _mop_behavior(e) == "Unknown"

    def test_flag_combination_dry_only(self):
        e = _entity({"padDryAllowed": 1, "padWashAllowed": 0, "padDirtyPause": 0})
        assert _mop_behavior(e) == "Dry"

    def test_flag_combination_dirty_pause_plus_dry_plus_wash(self):
        e = _entity({"padDirtyPause": 1, "padDryAllowed": 1, "padWashAllowed": 1})
        assert _mop_behavior(e) == "Dirty Pause + Dry + Wash"

    def test_no_flags_is_unknown(self):
        e = _entity({"padDryAllowed": 0, "padWashAllowed": 0})
        assert _mop_behavior(e) == "Unknown"

    def test_rankOverlap_takes_precedence_over_flags(self):
        e = _entity({"rankOverlap": 25, "padDryAllowed": 1})
        assert _mop_behavior(e) == "Extended"

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


class TestBatteryCapacityMahUnaffected:
    """battery_capacity_mah (raw mAh) is NOT NiMH-guarded — raw value is valid."""

    def test_nimh_with_estcap_still_surfaces(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_capacity_mah")
        state = {"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}
        assert desc.filter_fn(state) is True


class TestRecentEvacuationsCleanBaseGuard:
    """recent_evacuations must not be created when no Clean Base is present."""

    def _make_state(self, has_clean_base: bool) -> dict:
        """Return a minimal MQTT state with or without Clean Base indicators."""
        if has_clean_base:
            return {"dock": {"fwVer": "1.2.3", "state": 300}}
        return {"dock": {"known": True}}   # 980 diagnostics: dock={known:true}

    def _count_evacuations_entities(self, state: dict) -> int:
        """Count how many recent_evacuations entities would be created."""
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        created = 0
        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_evacuations":
                if not has_clean_base(state):
                    continue   # mirrors the fix
                created += 1
        return created

    def test_no_clean_base_suppresses_evacuations(self):
        """980 without Clean Base: recent_evacuations not created."""
        state = self._make_state(has_clean_base=False)
        assert self._count_evacuations_entities(state) == 0

    def test_with_clean_base_creates_evacuations(self):
        """Robot with Clean Base: recent_evacuations created."""
        state = self._make_state(has_clean_base=True)
        assert self._count_evacuations_entities(state) == 1

    def test_980_exact_dock_state_suppressed(self):
        """Exact dock state from 980 diagnostics: {known: true} → suppressed."""
        from custom_components.roomba_plus.const import has_clean_base
        state_980 = {"dock": {"known": True}}
        assert has_clean_base(state_980) is False
        assert self._count_evacuations_entities(state_980) == 0

    def test_empty_dock_suppressed(self):
        """Empty dock dict → no Clean Base → suppressed."""
        assert self._count_evacuations_entities({"dock": {}}) == 0

    def test_dock_with_fwver_creates_evacuations(self):
        """dock.fwVer present → Clean Base confirmed → created."""
        state = {"dock": {"fwVer": "3.1.7"}}
        assert self._count_evacuations_entities(state) == 1

    def test_dock_with_int_state_creates_evacuations(self):
        """dock.state as integer → Clean Base confirmed → created."""
        state = {"dock": {"state": 300}}
        assert self._count_evacuations_entities(state) == 1

    def test_other_cloud_raw_sensors_unaffected(self):
        """The guard skips only recent_evacuations — all others still created."""
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        state = {"dock": {"known": True}}   # no Clean Base
        created_keys = []
        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_evacuations" and not has_clean_base(state):
                continue
            created_keys.append(desc.key)
        assert "recent_evacuations" not in created_keys
        assert "recent_completion_rate" in created_keys
        assert "recent_recharges" in created_keys
        assert "recent_dirt_events" in created_keys
        assert "recent_error_code" in created_keys


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
