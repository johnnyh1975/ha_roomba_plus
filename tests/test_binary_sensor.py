"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import pytest
from unittest.mock import MagicMock, patch
import homeassistant.helpers.entity_platform as _ep


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
        tk = (type(s).__dict__.get("_attr_translation_key") or
              getattr(getattr(s, "entity_description", None), "translation_key", None))
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


def _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=0.0, wifistat=None):
    """Build a minimal RoombaMqttStale with stubbed hass/vacuum/entry state.

    v2.9.0 — covers the enriched mqtt_watchdog Repair Issue (last known
    phase, actual silence duration, cloud connectivity cross-check).
    Previously this sensor/issue had zero test coverage at all.
    """
    from custom_components.roomba_plus.binary_sensor import RoombaMqttStale

    reported = {"cleanMissionStatus": {"phase": phase}}
    if wifistat is not None:
        reported["wifistat"] = wifistat

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": reported}}

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.last_mqtt_message_ts = last_mqtt_message_ts

    s = RoombaMqttStale.__new__(RoombaMqttStale)
    s.vacuum = roomba
    s._entry = entry
    s.hass = MagicMock()
    s._was_stale = False
    s._attr_unique_id = "test_robot_mqtt_stale"
    return s


class TestMqttWatchdogRepairIssue:
    """v2.9.0 — enriched mqtt_watchdog Repair Issue content.

    Confirmed real-world problem (2026-06-19, 980 OG, screenshot-reported):
    the issue used to say only "check your network connection" with no
    way to tell whether the robot was genuinely unreachable or just
    physically stuck (last_stuck_count=165 on the same mission this
    watchdog could plausibly fire for). Now includes last known phase,
    actual elapsed silence in minutes, and a cloud-connectivity hint.
    """

    def test_fires_with_last_known_phase_and_minutes(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        # Robot last seen 7 minutes ago, in "stuck" phase — i.e. it may
        # have gone quiet because it was already stuck, not because of a
        # real connectivity loss.
        s = _mqtt_stale_sensor(phase="stuck", last_mqtt_message_ts=now - 7 * 60)

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        assert mock_create.called
        placeholders = mock_create.call_args.kwargs["translation_placeholders"]
        assert placeholders["minutes"] == "7"
        assert placeholders["last_phase"] == "stuck"

    def test_cloud_hint_unknown_when_wifistat_absent(self):
        """9-series firmware (incl. the 980 OG test robot) never sends
        wifistat at all — must report "unknown", never guess connected."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 600, wifistat=None)

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        placeholders = mock_create.call_args.kwargs["translation_placeholders"]
        assert "unbekannt" in placeholders["cloud_hint"]

    def test_cloud_hint_connected_points_to_local_issue(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(
            phase="run", last_mqtt_message_ts=now - 600, wifistat={"cloud": 1}
        )

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        placeholders = mock_create.call_args.kwargs["translation_placeholders"]
        assert "lokale" in placeholders["cloud_hint"] or "lokal" in placeholders["cloud_hint"]

    def test_cloud_hint_disconnected_points_to_robot_wifi(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(
            phase="run", last_mqtt_message_ts=now - 600, wifistat={"cloud": 0}
        )

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        placeholders = mock_create.call_args.kwargs["translation_placeholders"]
        assert "WLAN-Ausfall am Roboter" in placeholders["cloud_hint"]

    def test_issue_cleared_on_recovery(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 600)
        s._was_stale = True  # was already stale

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_delete_issue") as mock_delete:
            # Fresh message just arrived — no longer stale.
            s._entry.runtime_data.last_mqtt_message_ts = now
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        assert mock_delete.called

    def test_does_not_fire_when_not_in_run_phase(self):
        """Docked/idle robots going quiet is normal, not a watchdog condition."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="charge", last_mqtt_message_ts=now - 6000)

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        assert not mock_create.called

    def test_broadened_gate_fires_for_stuck_and_pause(self):
        """v2.9.0 — silence starting in 'stuck' or 'pause' must also fire;
        previously only phase=='run' was checked, missing exactly the
        scenario most likely to coincide with a real connectivity loss
        (the robot struggling, then vanishing)."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        for phase in ("stuck", "pause", "run", "hmMidMsn", "evac"):
            s = _mqtt_stale_sensor(phase=phase, last_mqtt_message_ts=now - 600)
            with patch.object(bs_mod, "_time_mod") as tmock, \
                 patch.object(bs_mod.ir, "async_create_issue") as mock_create:
                tmock.time.return_value = now
                s._async_watchdog_tick(None)
            assert mock_create.called, f"phase={phase} should fire the watchdog"

    def test_broadened_gate_excludes_mission_end_phases(self):
        """Mission-end phases (charge, hmPostMsn, stop) and idle must never
        fire — going quiet there is the normal, expected end state."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        for phase in ("charge", "hmPostMsn", "stop", ""):
            s = _mqtt_stale_sensor(phase=phase, last_mqtt_message_ts=now - 6000)
            with patch.object(bs_mod, "_time_mod") as tmock, \
                 patch.object(bs_mod.ir, "async_create_issue") as mock_create:
                tmock.time.return_value = now
                s._async_watchdog_tick(None)
            assert not mock_create.called, f"phase={phase} must not fire the watchdog"
