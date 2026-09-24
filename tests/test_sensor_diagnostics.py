"""`sensor_diagnostics.py` -- firmware version and the reset-diagnostics
button sensor.

New file (August 2026). Both classes were in test_sensors.py; this
module had no test file of its own, so nothing named for it existed to
look in.

`_make_sensor` is COPIED from test_sensors.py: tests there still use it.
"""

from unittest.mock import MagicMock

from custom_components.roomba_plus.sensor_diagnostics import (
    RoombaFirmwareVersionSensor,
    RoombaResetDiagnosticsSensor,
)

from tests.conftest import robot_mock
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest




def _make_reset_diagnostics_sensor(vacuum_state: dict):
    """Return a RoombaResetDiagnosticsSensor with the given vacuum_state."""
    from custom_components.roomba_plus.sensor import RoombaResetDiagnosticsSensor
    roomba = robot_mock()
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
        roomba = robot_mock()
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

    def test_translation_key_is_summary_noun_phrase(self):
        """The prior key ("reset_diagnostics") resolved to an imperative-
        looking display name for a passive counter sensor.
        """
        roomba = robot_mock()
        roomba.master_state = {"state": {"reported": {}}}
        sensor = RoombaResetDiagnosticsSensor(roomba, "test_blid")
        assert sensor.translation_key == "reset_diagnostics_summary"

    def test_unique_id_and_entity_id_construction_unchanged(self):
        """The translation_key rename affects display name only; unique_id
        (and thus the initial entity_id) must stay byte-for-byte identical
        so existing entity registrations are not broken.
        """
        roomba = robot_mock()
        roomba.master_state = {"state": {"reported": {}}}
        sensor = RoombaResetDiagnosticsSensor(roomba, "test_blid")
        assert sensor.unique_id == "roomba_plus_test_blid_reset_diagnostics"
        assert sensor.suggested_object_id == "reset_diagnostics"


def _make_health_trend_sensor(rps):
    """Return a RoombaHealthScoreTrendSensor with the given robot_profile_store
    (or None) wired into runtime_data."""
    from custom_components.roomba_plus.sensor import RoombaHealthScoreTrendSensor
    roomba = robot_mock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    entry.runtime_data.robot_profile_store = rps
    sensor = RoombaHealthScoreTrendSensor.__new__(RoombaHealthScoreTrendSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._config_entry = entry
    sensor._attr_unique_id = "test_blid_health_score_trend"
    return sensor


# ── formerly tests/test_coverage_mid_gaps.py ────────────────────────────────────
#
# Mid-sized coverage gaps — quality scale, test-coverage (Silver).
#
# Error branches and fallbacks: bad cloud values, missing fields, foreign
# entities. Each test pins what the branch protects against.

class TestRawStateSensor:

    def _sensor(self, state):
        from custom_components.roomba_plus.sensor_diagnostics import RawStateSensor

        s = RawStateSensor.__new__(RawStateSensor)
        s.vacuum_state = state
        return s

    def test_value_is_the_number_of_reported_keys(self):
        assert self._sensor({"a": 1, "b": 2}).native_value == 2

    def test_nested_values_become_json_strings(self):
        attrs = self._sensor({"batPct": 90, "cap": {"maps": 3}, "l": [1, 2]}).extra_state_attributes
        assert attrs["batPct"] == 90
        assert attrs["cap"] == '{"maps": 3}'
        assert attrs["l"] == "[1, 2]"

    def test_an_unserialisable_value_falls_back_to_str(self):
        """A self-referencing structure must not break the attributes."""
        loop: dict = {}
        loop["self"] = loop
        attrs = self._sensor({"x": loop}).extra_state_attributes
        assert isinstance(attrs["x"], str)

    def test_it_updates_on_every_message(self):
        assert self._sensor({}).new_state_filter({"signal": {}}) is True


class TestOptimalCleanWindow:

    def test_a_window_already_past_today_is_tomorrow(self):
        import datetime as dt

        from custom_components.roomba_plus.sensor_diagnostics import RoombaOptimalCleanWindow

        s = RoombaOptimalCleanWindow.__new__(RoombaOptimalCleanWindow)
        now = dt.datetime.now(dt.timezone.utc).astimezone()
        s._config_entry = MagicMock()
        s._config_entry.runtime_data.presence_manager.preferred_window.return_value = (0, now.hour)
        value = s.native_value
        assert value > now
        assert value.hour == now.hour and value.date() > now.date() or value.date() == (now + dt.timedelta(days=1)).date()


class TestIntegrationHealthSensor:

    @pytest.mark.asyncio
    async def test_the_periodic_tick_is_started_and_stopped(self, monkeypatch):
        from custom_components.roomba_plus import sensor_diagnostics as sd
        from custom_components.roomba_plus.entity import IRobotEntity

        s = sd.RoombaIntegrationHealthSensor.__new__(sd.RoombaIntegrationHealthSensor)
        s.hass = MagicMock()
        s._unsub_tick = None
        stop = MagicMock()
        monkeypatch.setattr(sd, "async_track_time_interval", lambda *a, **k: stop)
        monkeypatch.setattr(IRobotEntity, "async_added_to_hass", AsyncMock())
        await s.async_added_to_hass()
        assert s._unsub_tick is stop
        await s.async_will_remove_from_hass()
        stop.assert_called_once()
        assert s._unsub_tick is None

    def test_the_value_is_the_computed_score(self, monkeypatch):
        from custom_components.roomba_plus import sensor_diagnostics as sd

        s = sd.RoombaIntegrationHealthSensor.__new__(sd.RoombaIntegrationHealthSensor)
        s.hass, s._entry = MagicMock(), MagicMock()
        monkeypatch.setattr(sd, "_compute_integration_health", lambda _h, _e: (87, {}))
        assert s.native_value == 87
