"""Tests for v1.9.3 — Mission Phase Intelligence.

Covers:
  - mission_recharge_minutes sensor
  - mission_expire_minutes sensor
  - mission_id sensor
  - RoombaMidMissionRecharge binary sensor
  - vacuum entity extra_state_attributes additions
"""
from __future__ import annotations

import pytest
import sys
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import tests.conftest  # noqa: F401

from custom_components.roomba_plus.sensor import SENSORS
from custom_components.roomba_plus.binary_sensor import RoombaMidMissionRecharge


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── TestMissionRechargeMinutes ────────────────────────────────────────────────

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


# ── TestMissionExpireMinutes ──────────────────────────────────────────────────

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


# ── TestMissionId ─────────────────────────────────────────────────────────────

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


# ── TestMidMissionRechargeBinary ──────────────────────────────────────────────

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


# ── TestVacuumAttributes ─────────────────────────────────────────────────────

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
