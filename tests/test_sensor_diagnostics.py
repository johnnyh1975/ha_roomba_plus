"""`sensor_diagnostics.py` -- firmware version and the reset-diagnostics
button sensor.

New file (August 2026). Both classes were in test_sensors.py; this
module had no test file of its own, so nothing named for it existed to
look in.

`_make_sensor` is COPIED from test_sensors.py: tests there still use it.
"""

from unittest.mock import MagicMock

from custom_components.roomba_plus.sensor_cloud import (
    CloudRawSensor,
    CloudRawSensorDescription,
)
from custom_components.roomba_plus.sensor_diagnostics import (
    RoombaFirmwareVersionSensor,
    RoombaResetDiagnosticsSensor,
)

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



def _make_reset_diagnostics_sensor(vacuum_state: dict):
    """Return a RoombaResetDiagnosticsSensor with the given vacuum_state."""
    from custom_components.roomba_plus.sensor import RoombaResetDiagnosticsSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": vacuum_state}}
    sensor = RoombaResetDiagnosticsSensor.__new__(RoombaResetDiagnosticsSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor.vacuum_state = vacuum_state
    sensor._attr_unique_id = "test_blid_reset_diagnostics"
    return sensor



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



class TestResetDiagnosticsSensor:
    """RESET-DIAGNOSTICS (v3.2.0) — bbrstinfo reset-cause breakdown."""

    def test_native_value_is_safety_reset_count(self):
        sensor = _make_reset_diagnostics_sensor({
            "bbrstinfo": {"nNavRst": 87, "nMobRst": 78, "nSafRst": 4, "safCauses": [18, 18, 21, 21]}
        })
        assert sensor.native_value == 4

    def test_none_when_bbrstinfo_absent(self):
        sensor = _make_reset_diagnostics_sensor({})
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {
            "nav_resets": None, "mobility_resets": None,
            "safety_resets": None, "safety_reset_causes": None,
        }

    def test_attributes_full_breakdown(self):
        sensor = _make_reset_diagnostics_sensor({
            "bbrstinfo": {"nNavRst": 87, "nMobRst": 78, "nSafRst": 4, "safCauses": [18, 18, 21, 21]}
        })
        attrs = sensor.extra_state_attributes
        assert attrs["nav_resets"] == 87
        assert attrs["mobility_resets"] == 78
        assert attrs["safety_resets"] == 4
        assert attrs["safety_reset_causes"] == [18, 18, 21, 21]
        assert "oom_resets" not in attrs

    def test_oom_resets_included_only_when_present(self):
        """nOomRst confirmed j-series-only (absent on Braava) — must not
        appear as None/0 on robots whose firmware never reports it."""
        sensor = _make_reset_diagnostics_sensor({
            "bbrstinfo": {"nNavRst": 197, "nOomRst": 2, "nMobRst": 199, "nSafRst": 0, "safCauses": []}
        })
        attrs = sensor.extra_state_attributes
        assert attrs["oom_resets"] == 2

    def test_no_oom_resets_key_on_braava(self):
        sensor = _make_reset_diagnostics_sensor({
            "bbrstinfo": {"nNavRst": 87, "nMapLoadRst": 0, "nMobRst": 78, "nSafRst": 4, "safCauses": []}
        })
        assert "oom_resets" not in sensor.extra_state_attributes


def _make_health_trend_sensor(rps):
    """Return a RoombaHealthScoreTrendSensor with the given robot_profile_store
    (or None) wired into runtime_data."""
    from custom_components.roomba_plus.sensor import RoombaHealthScoreTrendSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    entry.runtime_data.robot_profile_store = rps
    sensor = RoombaHealthScoreTrendSensor.__new__(RoombaHealthScoreTrendSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._config_entry = entry
    sensor._attr_unique_id = "test_blid_health_score_trend"
    return sensor


