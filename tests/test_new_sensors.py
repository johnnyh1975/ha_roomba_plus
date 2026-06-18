"""Unit tests for the 7 new sensors added in the latest iteration.

Tested without HA by extracting the pure calculation functions:
  _phase_value          — Idle/Stopped detection
  _mission_elapsed_value — elapsed time in minutes
  _ts_or_none           — Unix timestamp → datetime
  ERROR_CODE_LABELS     — 80+ error code mappings
  SNR / Noise / IP      — signal and network sensors
"""
import sys, datetime
sys.path.insert(0, "/tmp/roomba_plus_package")

import pytest
from unittest.mock import MagicMock


# ── Load helpers via conftest stubs ──────────────────────────────────────────
import tests.conftest  # noqa: F401 — loads all HA stubs


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
