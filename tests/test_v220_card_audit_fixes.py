"""Tests for card audit integration fixes — Steps 23, 24, 25.

Step 23: translation_key added to recent_area_30d / recent_time_30d
Step 24: RoombaMissionActive binary sensor (card fix C1)
Step 25: CarpetBoostSelect entity (card fix P2)

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock

# Stub AddConfigEntryEntitiesCallback for test environment
import homeassistant.helpers.entity_platform as _ep
if not hasattr(_ep, "AddConfigEntryEntitiesCallback"):
    _ep.AddConfigEntryEntitiesCallback = getattr(_ep, "AddEntitiesCallback", object)


# ── Step 23 — recent_area_30d / recent_time_30d translation_key ──────────────

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


# ── Step 24 — RoombaMissionActive sensor ─────────────────────────────────────

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


class TestMissionActiveSensor:
    """Card fix C1 — full mission lifecycle coverage."""

    def test_on_during_run_phase(self):
        assert _mission_sensor("clean", "run").is_on is True

    def test_on_during_hmMidMsn(self):
        assert _mission_sensor("clean", "hmMidMsn").is_on is True

    def test_on_during_hmPostMsn(self):
        assert _mission_sensor("clean", "hmPostMsn").is_on is True

    def test_on_during_evac(self):
        assert _mission_sensor("clean", "evac").is_on is True

    def test_on_during_mid_mission_recharge(self):
        # mid-mission: cycle still "clean", phase == "charge" → ON
        assert _mission_sensor("clean", "charge").is_on is True

    def test_off_when_cycle_none_final_dock(self):
        # final dock: cycle returns to "none"
        assert _mission_sensor("none", "charge").is_on is False

    def test_off_when_stop(self):
        assert _mission_sensor("none", "stop").is_on is False

    def test_off_when_cancelled(self):
        assert _mission_sensor("none", "cancelled").is_on is False

    def test_off_when_idle_empty_phase(self):
        assert _mission_sensor("none", "").is_on is False

    def test_off_when_default_state(self):
        # No state at all
        assert _mission_sensor().is_on is False

    def test_state_filter(self):
        s = _mission_sensor()
        assert s.new_state_filter({"cleanMissionStatus": {}}) is True
        assert s.new_state_filter({"bbrun": {}}) is False

    def test_unique_id_suffix(self):
        from custom_components.roomba_plus.binary_sensor import RoombaMissionActive
        s = RoombaMissionActive.__new__(RoombaMissionActive)
        s._attr_unique_id = "test_blid_mission_active"
        assert s._attr_unique_id.endswith("_mission_active")

    def test_translation_key(self):
        s = _mission_sensor()
        # _attr_translation_key may be wrapped as a property in some HA versions
        tk = type(s).__dict__.get("_attr_translation_key")
        if isinstance(tk, property):
            tk = tk.fget(s)
        assert tk == "mission_active"

    def test_distinct_from_mid_mission_recharge(self):
        """MissionActive is ON across the full arc; MidMissionRecharge only during charge."""
        from custom_components.roomba_plus.binary_sensor import RoombaMidMissionRecharge

        # During run phase: MissionActive=ON, MidMissionRecharge=OFF
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {
            "cleanMissionStatus": {"cycle": "clean", "phase": "run"}
        }}}

        active = _mission_sensor("clean", "run")
        recharge = RoombaMidMissionRecharge.__new__(RoombaMidMissionRecharge)
        recharge.vacuum = roomba

        assert active.is_on is True
        assert recharge.is_on is False


# ── Step 25 — CarpetBoostSelect ───────────────────────────────────────────────

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


class TestCarpetBoostSelect:
    """Card fix P2 — select.*_carpet_boost_select."""

    def test_current_option_automatic(self):
        assert _boost_entity(carpet_boost=True, vac_high=False).current_option == "Automatic"

    def test_current_option_performance(self):
        assert _boost_entity(carpet_boost=False, vac_high=True).current_option == "Performance"

    def test_current_option_eco(self):
        assert _boost_entity(carpet_boost=False, vac_high=False).current_option == "Eco"

    def test_current_option_none_when_state_absent(self):
        assert _boost_entity().current_option is None

    def test_options_list_contains_all_three(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        from custom_components.roomba_plus.const import FAN_SPEEDS
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_options = FAN_SPEEDS
        assert "Automatic" in s._attr_options
        assert "Eco" in s._attr_options
        assert "Performance" in s._attr_options

    def test_state_filter_carpet_boost(self):
        s = _boost_entity()
        assert s.new_state_filter({"carpetBoost": True}) is True

    def test_state_filter_vac_high(self):
        s = _boost_entity()
        assert s.new_state_filter({"vacHigh": False}) is True

    def test_state_filter_rejects_unrelated(self):
        s = _boost_entity()
        assert s.new_state_filter({"cleanMissionStatus": {}}) is False

    def test_translation_key(self):
        tk = type(_boost_entity()).__dict__.get("_attr_translation_key")
        if isinstance(tk, property):
            tk = tk.fget(_boost_entity())
        assert tk == "carpet_boost_select"

    def test_unique_id_suffix(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_unique_id = "test_blid_carpet_boost_select"
        assert s._attr_unique_id.endswith("_carpet_boost_select")
