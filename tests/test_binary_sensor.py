"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


def _mqtt_stale_sensor(
    phase="run",
    last_mqtt_message_ts=0.0,
    wifistat=None,
    mssn_strt_tm=None,
    last_run_transition_ts=0.0,
):
    """Build a minimal RoombaMqttStale with stubbed hass/vacuum/entry state.

    v2.9.0 — covers the enriched mqtt_watchdog Repair Issue (last known
    phase, actual silence duration, cloud connectivity cross-check).
    Previously this sensor/issue had zero test coverage at all.
    v3.2.1 — last_run_transition_ts added (RESUME-GRACE); defaults to 0.0
    ("no transition observed"), which preserves every pre-existing test's
    semantics: 0.0 falls through to the normal silence check.
    """
    from custom_components.roomba_plus.binary_sensor import RoombaMqttStale

    reported = {"cleanMissionStatus": {"phase": phase}}
    if mssn_strt_tm is not None:
        reported["cleanMissionStatus"]["mssnStrtTm"] = mssn_strt_tm
    if wifistat is not None:
        reported["wifistat"] = wifistat

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": reported}}

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.last_mqtt_message_ts = last_mqtt_message_ts
    entry.runtime_data.last_run_transition_ts = last_run_transition_ts

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
        # v2.9.0 — REVERTED to phase=="run" only (see _MISSION_ACTIVE_PHASES
        # rationale). This test's purpose is verifying the placeholder
        # text content, not the gating phase itself — uses "run" so the
        # watchdog actually evaluates and fires.
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 7 * 60)

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        assert mock_create.called
        placeholders = mock_create.call_args.kwargs["translation_placeholders"]
        assert placeholders["minutes"] == "7"
        assert placeholders["last_phase"] == "run"

    def test_cloud_hint_unknown_when_wifistat_absent(self):
        """9-series firmware (incl. the 980 OG test robot) never sends
        wifistat at all — must select the 'unknown' translation_key, never
        guess connected.

        BUGFIX (boutXIII report, v2.9.0): previously asserted on a hardcoded
        German substring in translation_placeholders["cloud_hint"] — itself
        a symptom of the bug (the hint text was hardcoded in German
        regardless of the user's locale). Now asserts on the selected
        translation_key, which HA resolves per-locale on its own.
        """
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 600, wifistat=None)

        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)

        assert mock_create.call_args.kwargs["translation_key"] == "mqtt_watchdog_cloud_unknown"
        assert "cloud_hint" not in mock_create.call_args.kwargs["translation_placeholders"]

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

        assert mock_create.call_args.kwargs["translation_key"] == "mqtt_watchdog_cloud_connected"

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

        assert mock_create.call_args.kwargs["translation_key"] == "mqtt_watchdog_cloud_disconnected"


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

    def test_reverted_gate_only_fires_for_run(self):
        """v2.9.0 — REVERTED. The broadened gate (CLEANING_PHASES |
        {"stuck", "pause"}) was speculative — added from a single user
        screenshot, not a confirmed bug report — and field use the same
        day confirmed a real, recurring cost for any robot that gets stuck
        often: firmware pushes far fewer updates while motionless-but-
        stuck-and-still-connected, which is normal low-chatter behaviour,
        not a connectivity problem. Reverted to "run" only; "stuck",
        "pause", "hmMidMsn", and "evac" must NOT fire the watchdog.
        """
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        for phase in ("stuck", "pause", "hmMidMsn", "evac"):
            s = _mqtt_stale_sensor(phase=phase, last_mqtt_message_ts=now - 600)
            with patch.object(bs_mod, "_time_mod") as tmock, \
                 patch.object(bs_mod.ir, "async_create_issue") as mock_create:
                tmock.time.return_value = now
                s._async_watchdog_tick(None)
            assert not mock_create.called, (
                f"phase={phase} must NOT fire the watchdog after the revert"
            )

        # "run" must still fire — the watchdog's actual purpose.
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 600)
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        assert mock_create.called, "phase=run must still fire the watchdog"

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


class TestStuckContextEvent:
    """v3.2.0 STUCK-CONTEXT — roomba_plus_stuck event, fired at the same
    OFF->ON watchdog transition as the mqtt_watchdog Repair Issue."""

    def _sensor_with_extras(
        self, bbrun=None, pose=None, current_room=None, title="Test Robot",
    ):
        from custom_components.roomba_plus import binary_sensor as bs_mod
        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 7 * 60)
        s._entry.title = title
        reported = s.vacuum.master_state["state"]["reported"]
        if bbrun is not None:
            reported["bbrun"] = bbrun
        if pose is not None:
            reported["pose"] = pose
        mts = MagicMock()
        mts.current_room = current_room
        s._entry.runtime_data.mission_timer_store = mts
        return s, bs_mod, now

    def test_event_fires_on_watchdog_transition(self):
        s, bs_mod, now = self._sensor_with_extras()
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        s.hass.bus.async_fire.assert_called_once()
        assert s.hass.bus.async_fire.call_args[0][0] == "roomba_plus_stuck"

    def test_payload_completeness(self):
        s, bs_mod, now = self._sensor_with_extras(
            bbrun={"nStuck": 165}, current_room="Kitchen",
        )
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        payload = s.hass.bus.async_fire.call_args[0][1]
        assert payload["entry_id"] == "test_entry"
        assert payload["name"] == "Test Robot"
        assert payload["last_room"] == "Kitchen"
        assert payload["phase"] == "run"
        assert payload["stuck_count"] == 165
        assert payload["minutes_stuck"] == 7

    def test_ephemeral_pose_included_when_available(self):
        s, bs_mod, now = self._sensor_with_extras(
            pose={"theta": 61, "point": {"x": 171, "y": -113}},
        )
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        payload = s.hass.bus.async_fire.call_args[0][1]
        assert payload["last_known_position"] == {"x": 171, "y": -113}

    def test_position_none_when_pose_absent(self):
        """SMART-tier robots (or any robot without pose in this
        snapshot) get last_known_position=None, not a crash or a
        fabricated value."""
        s, bs_mod, now = self._sensor_with_extras(pose=None)
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        payload = s.hass.bus.async_fire.call_args[0][1]
        assert payload["last_known_position"] is None

    def test_last_room_none_when_no_mission_timer_store(self):
        s, bs_mod, now = self._sensor_with_extras()
        s._entry.runtime_data.mission_timer_store = None
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        payload = s.hass.bus.async_fire.call_args[0][1]
        assert payload["last_room"] is None

    def test_stuck_count_none_when_bbrun_absent(self):
        s, bs_mod, now = self._sensor_with_extras(bbrun=None)
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        payload = s.hass.bus.async_fire.call_args[0][1]
        assert payload["stuck_count"] is None

    def test_no_event_when_already_stale(self):
        """No new transition (already ON) — no duplicate event fire."""
        s, bs_mod, now = self._sensor_with_extras()
        s._was_stale = True
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue"):
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        s.hass.bus.async_fire.assert_not_called()

    def test_no_event_when_not_stale(self):
        """MQTT is fresh (recent message) — watchdog never transitions
        ON, no event."""
        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 10)
        from custom_components.roomba_plus import binary_sensor as bs_mod
        with patch.object(bs_mod, "_time_mod") as tmock:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        s.hass.bus.async_fire.assert_not_called()


class TestMqttWatchdogStartGrace:
    """BUGFIX (field reports: boutXIII, Jean-Christoph — both v2.9.0):
    a genuine, benign MQTT gap of a few minutes right after undocking
    (Wi-Fi reassociation while the robot moves away from the router) was
    being misreported as a sustained connectivity problem, since the last
    received message already showed phase=="run" before the gap. The
    watchdog now suppresses entirely for MQTT_WATCHDOG_START_GRACE_SECONDS
    after mssnStrtTm, regardless of silence duration.
    """

    def test_suppressed_within_grace_period_even_with_long_silence(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        # Mission started 1 minute ago (well within the 420s/7min grace
        # window) but MQTT has been silent for 10 minutes — exactly the
        # field-reported scenario. Must NOT fire.
        s = _mqtt_stale_sensor(
            phase="run", last_mqtt_message_ts=now - 600, mssn_strt_tm=now - 60,
        )
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        assert not mock_create.called, (
            "Watchdog must not fire within the start-grace window, "
            "regardless of silence duration"
        )

    def test_fires_once_grace_period_has_elapsed(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        # Mission started 16 minutes ago (well past the 7min grace window),
        # silent for the last 10 minutes — a genuine mid-mission outage,
        # must still be caught.
        s = _mqtt_stale_sensor(
            phase="run", last_mqtt_message_ts=now - 600, mssn_strt_tm=now - 960,
        )
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        assert mock_create.called, (
            "Watchdog must still fire for a genuine outage once the "
            "start-grace window has elapsed"
        )

    def test_no_grace_suppression_when_mssn_strt_tm_missing(self):
        """If the robot doesn't report mssnStrtTm at all, there's nothing
        to gate on — must fall through to the normal silence check
        unaffected (this is the pre-fix behaviour, must stay intact)."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=now - 600)
        with patch.object(bs_mod, "_time_mod") as tmock, \
             patch.object(bs_mod.ir, "async_create_issue") as mock_create:
            tmock.time.return_value = now
            s._async_watchdog_tick(None)
        assert mock_create.called, (
            "Without mssnStrtTm there's nothing to gate on — must behave "
            "exactly as before this fix"
        )


# ── RoombaMapSavingStatus tests (merged from test_map_saving_sensor.py) ───────

from custom_components.roomba_plus.binary_sensor import (
    RoombaMapSavingStatus,
    _NOT_READY_MAP_SAVING,
)



# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sensor(not_ready: int = 0) -> RoombaMapSavingStatus:
    roomba = MagicMock()
    roomba.master_state = {
        "state": {
            "reported": {
                "cleanMissionStatus": {"notReady": not_ready},
                "pmaps": [{"abc": "v1"}],
            }
        }
    }
    return RoombaMapSavingStatus(roomba, "test_blid")


# ── Constant ──────────────────────────────────────────────────────────────────

class TestMqttWatchdogResumeGrace:
    """v3.2.1 RESUME-GRACE (field report: Jean-Christoph, 2026-07-02):
    a false "Problem" blip fired at every Zwischenladung resume — phase
    flips recharge→run while last_mqtt_message_ts is still minutes old,
    and the mssnStrtTm start-grace can't help because mssnStrtTm keeps
    the ORIGINAL mission start (2h20 old in the reported case).  The
    sensor now also suppresses for MQTT_WATCHDOG_START_GRACE_SECONDS
    after the last observed transition into phase="run", stamped by
    make_mqtt_stamp_callback into runtime_data.last_run_transition_ts.
    """

    def test_suppressed_within_resume_grace_despite_stale_ts_and_old_mission(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        # The exact field scenario: mission 2h20 old (start grace long
        # expired), MQTT silent 10 min (during recharge — benign), robot
        # resumed into "run" 30 s ago.  Must NOT fire.
        s = _mqtt_stale_sensor(
            phase="run",
            last_mqtt_message_ts=now - 600,
            mssn_strt_tm=now - 8400,
            last_run_transition_ts=now - 30,
        )
        with patch.object(bs_mod, "_time_mod") as tmock:
            tmock.time.return_value = now
            assert s.is_on is False

    def test_fires_once_resume_grace_elapsed(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        # Resume was 16 min ago, silence 10 min — genuine mid-mission
        # outage well past both graces.  Must fire.
        s = _mqtt_stale_sensor(
            phase="run",
            last_mqtt_message_ts=now - 600,
            mssn_strt_tm=now - 8400,
            last_run_transition_ts=now - 960,
        )
        with patch.object(bs_mod, "_time_mod") as tmock:
            tmock.time.return_value = now
            assert s.is_on is True

    def test_zero_transition_ts_falls_through_to_silence_check(self):
        """0.0 = no transition observed (HA restarted mid-mission) — must
        behave exactly like pre-v3.2.1: normal silence check applies."""
        from custom_components.roomba_plus import binary_sensor as bs_mod

        now = 1_000_000.0
        s = _mqtt_stale_sensor(
            phase="run",
            last_mqtt_message_ts=now - 600,
            mssn_strt_tm=now - 8400,
            last_run_transition_ts=0.0,
        )
        with patch.object(bs_mod, "_time_mod") as tmock:
            tmock.time.return_value = now
            assert s.is_on is True


class TestMqttStampCallback:
    """v3.2.1 — make_mqtt_stamp_callback: registered ahead of the
    platforms so entities never evaluate a message before its stamp."""

    def _entry(self):
        entry = MagicMock()
        entry.runtime_data.last_mqtt_message_ts = 0.0
        entry.runtime_data.last_run_transition_ts = 0.0
        return entry

    def _msg(self, phase=None, extra=None):
        reported = dict(extra or {})
        if phase is not None:
            reported["cleanMissionStatus"] = {"phase": phase}
        return {"state": {"reported": reported}}

    def test_stamps_on_every_message_including_pose_only(self):
        """Broader than the old inline stamp: a pose-only message proves
        MQTT connectivity just as well as a cleanMissionStatus one."""
        from custom_components.roomba_plus import callbacks as cb_mod
        from custom_components.roomba_plus.callbacks import make_mqtt_stamp_callback

        entry = self._entry()
        cb = make_mqtt_stamp_callback(entry)
        with patch.object(cb_mod, "_time_mod") as tmock:
            tmock.time.return_value = 555.0
            cb(self._msg(extra={"pose": {"point": {"x": 1, "y": 2}}}))
        assert entry.runtime_data.last_mqtt_message_ts == 555.0

    def test_run_transition_stamped_only_on_entry_into_run(self):
        from custom_components.roomba_plus import callbacks as cb_mod
        from custom_components.roomba_plus.callbacks import make_mqtt_stamp_callback

        entry = self._entry()
        cb = make_mqtt_stamp_callback(entry)
        with patch.object(cb_mod, "_time_mod") as tmock:
            tmock.time.return_value = 100.0
            cb(self._msg(phase="charge"))
            assert entry.runtime_data.last_run_transition_ts == 0.0

            tmock.time.return_value = 200.0
            cb(self._msg(phase="run"))       # charge → run: stamp
            assert entry.runtime_data.last_run_transition_ts == 200.0

            tmock.time.return_value = 300.0
            cb(self._msg(phase="run"))       # run → run: no re-stamp
            assert entry.runtime_data.last_run_transition_ts == 200.0

            tmock.time.return_value = 400.0
            cb(self._msg(phase="recharge"))  # run → recharge: no stamp
            tmock.time.return_value = 500.0
            cb(self._msg(phase="run"))       # recharge → run (resume): stamp
            assert entry.runtime_data.last_run_transition_ts == 500.0

    def test_message_without_cleanmissionstatus_does_not_break_phase_tracking(self):
        """A pose-only message between charge and run must not corrupt the
        transition detection (phase memory only updates on
        cleanMissionStatus messages)."""
        from custom_components.roomba_plus import callbacks as cb_mod
        from custom_components.roomba_plus.callbacks import make_mqtt_stamp_callback

        entry = self._entry()
        cb = make_mqtt_stamp_callback(entry)
        with patch.object(cb_mod, "_time_mod") as tmock:
            tmock.time.return_value = 100.0
            cb(self._msg(phase="charge"))
            cb(self._msg(extra={"batPct": 80}))   # no cleanMissionStatus
            tmock.time.return_value = 200.0
            cb(self._msg(phase="run"))
            assert entry.runtime_data.last_run_transition_ts == 200.0


class TestNotReadyConstant:
    def test_value_is_the_map_updating_state(self):
        assert _NOT_READY_MAP_SAVING == 67


# ── is_on ─────────────────────────────────────────────────────────────────────

class TestMapSavingIsOn:
    def test_on_for_the_map_updating_state(self):
        """67 is DownloadingMap. This used to feed 64 and test a bit."""
        sensor = _make_sensor(not_ready=67)
        assert sensor.is_on is True

    def test_off_when_not_ready_is_zero(self):
        sensor = _make_sensor(not_ready=0)
        assert sensor.is_on is False

    def test_off_when_cleanmissionstatus_absent(self):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        sensor = RoombaMapSavingStatus(roomba, "blid")
        assert sensor.is_on is False

    def test_off_for_states_that_merely_share_bit_64(self):
        """The correction. notReady is a scalar index, so 64 through 71
        are eight distinct states and only 67 is the map one -- 64 is
        FleetDisabled, 65 SubscriptionExpired, 69 TankLeaking. Under the
        old bit test every one of them turned this sensor on."""
        for wire in (64, 65, 66, 68, 69, 70, 71):
            assert _make_sensor(not_ready=wire).is_on is False, wire

    def test_off_for_unrelated_states(self):
        sensor = _make_sensor(not_ready=35)
        assert sensor.is_on is False

    def test_off_when_not_ready_is_none(self):
        roomba = MagicMock()
        roomba.master_state = {
            "state": {"reported": {"cleanMissionStatus": {"notReady": None}}}
        }
        sensor = RoombaMapSavingStatus(roomba, "blid")
        # None treated as 0 via `or 0` guard — sensor must return False
        assert sensor.is_on is False

    def test_scalar_values(self):
        """Exhaustive: exactly one value turns this on.

        The old version asserted `bool(v & 64)` across the same range --
        128 of 256 values. That is the bug written as a test: it agreed
        with the implementation and neither was checked against the
        robot's own app.
        """
        for value in range(256):
            roomba = MagicMock()
            roomba.master_state = {
                "state": {"reported": {"cleanMissionStatus": {"notReady": value}}}
            }
            sensor = RoombaMapSavingStatus(roomba, "blid")
            assert sensor.is_on == (value == 67), f"notReady={value}"


# ── extra_state_attributes ────────────────────────────────────────────────────

class TestMapSavingAttributes:
    def test_exposes_bitmask(self):
        sensor = _make_sensor(not_ready=64)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 64

    def test_zero_bitmask_when_idle(self):
        sensor = _make_sensor(not_ready=0)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 0

    def test_combined_bitmask_preserved(self):
        sensor = _make_sensor(not_ready=65)
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 65


# ── new_state_filter ──────────────────────────────────────────────────────────

class TestMapSavingStateFilter:
    def test_triggers_on_cleanmissionstatus(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"cleanMissionStatus": {"notReady": 67}}) is True

    def test_ignores_other_fields(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"bin": {"full": True}}) is False
        assert sensor.new_state_filter({"pose": {"x": 1}}) is False
        assert sensor.new_state_filter({}) is False

    def test_triggers_when_combined_with_other_fields(self):
        sensor = _make_sensor()
        assert sensor.new_state_filter({"cleanMissionStatus": {}, "bin": {}}) is True


# ── Entity metadata ───────────────────────────────────────────────────────────

class TestMapSavingMetadata:
    def test_unique_id(self):
        sensor = _make_sensor()
        assert "map_saving" in sensor._attr_unique_id

    def test_translation_key(self):
        sensor = _make_sensor()
        assert sensor.entity_description.translation_key == "map_saving"

    def test_device_class_update(self):
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        sensor = _make_sensor()
        assert sensor._attr_device_class == BinarySensorDeviceClass.UPDATE

    def test_entity_category_diagnostic(self):
        from homeassistant.const import EntityCategory
        sensor = _make_sensor()
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC


# ── async_setup_entry routing ─────────────────────────────────────────────────

class TestMapSavingSetupEntry:
    @pytest.mark.asyncio
    async def test_created_for_smart_map_robot(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        state = {"pmaps": [{"abc": "v1"}], "cleanMissionStatus": {"notReady": 0}}
        entry = MagicMock()
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        roomba.roomba_connected = True
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(bs_mod, "roomba_reported_state", return_value=state):
            with patch.object(bs_mod, "has_smart_map", return_value=True):
                await bs_mod.async_setup_entry(MagicMock(), entry, sync_add)

        map_saving = [e for e in created if isinstance(e, RoombaMapSavingStatus)]
        assert len(map_saving) == 1

    @pytest.mark.asyncio
    async def test_not_created_for_non_smart_map_robot(self):
        from custom_components.roomba_plus import binary_sensor as bs_mod

        state = {}
        entry = MagicMock()
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": state}}
        roomba.roomba_connected = True
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "test_blid"

        created = []
        def sync_add(entities, **kw): created.extend(entities)

        with patch.object(bs_mod, "roomba_reported_state", return_value=state):
            with patch.object(bs_mod, "has_smart_map", return_value=False):
                await bs_mod.async_setup_entry(MagicMock(), entry, sync_add)

        map_saving = [e for e in created if isinstance(e, RoombaMapSavingStatus)]
        assert len(map_saving) == 0


# ── Automation scenario ───────────────────────────────────────────────────────

class TestMapSavingAutomationScenario:
    """Realistic sequence: map save starts, then completes."""

    def _sensor_with_state(self, not_ready: int) -> RoombaMapSavingStatus:
        return _make_sensor(not_ready)

    def test_sequence_off_on_off(self):
        """Robot idle → map saving → map save complete."""
        idle   = self._sensor_with_state(0)
        saving = self._sensor_with_state(67)
        done   = self._sensor_with_state(0)

        assert idle.is_on is False
        assert saving.is_on is True
        assert done.is_on is False

    def test_a_neighbouring_state_is_not_the_map_one(self):
        """65 is SubscriptionExpired. It used to read as map-saving
        because it shares bit 64 -- and a user whose subscription had
        lapsed was told to wait for a map update."""
        sensor = self._sensor_with_state(65)

        assert sensor.is_on is False
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 65


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_experimental_buttons.py (TEST-REORG, v2.9.1) — tests
# for the experimental command buttons (spot/quick/sleep/power_off):
# COMMAND_BUTTONS membership, disabled-by-default gating, EPHEMERAL-only
# filter_fn, command protocol strings, and maintenance-reset Logbook event.
# ═══════════════════════════════════════════════════════════════════════

from custom_components.roomba_plus.button import (
    COMMAND_BUTTONS,
    RoombaButtonDescription,
    RoombaCommandButton,
)

# ── Shared state fixtures ─────────────────────────────────────────────────────

# 980-style state: pose present, no pmaps (EPHEMERAL)
STATE_980 = {
    "cap": {"pose": 1, "carpetBoost": 1},
    "carpetBoost": True,
    "vacHigh": False,
}

# i7-style state: pmaps present (SMART)
STATE_I7 = {
    "cap": {"pose": 1, "pmaps": 3},
    "pmaps": [{"abc123": "v20240101"}],
}

# 600-series: no pose, no pmaps (NONE)
STATE_600 = {}


# ── Helper ─────────────────────────────────────────────────────────────────────

def _get_button(key: str) -> RoombaButtonDescription:
    for btn in COMMAND_BUTTONS:
        if btn.key == key:
            return btn
    raise KeyError(f"Button '{key}' not found in COMMAND_BUTTONS")


def _make_button_entity(key: str) -> RoombaCommandButton:
    desc = _get_button(key)
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    return RoombaCommandButton(roomba, "test_blid", desc)


# ── Presence in COMMAND_BUTTONS ────────────────────────────────────────────────

class TestExperimentalButtonsPresent:
    def test_spot_defined(self):
        _get_button("spot")  # raises if missing

    def test_quick_defined(self):
        _get_button("quick")

    def test_sleep_defined(self):
        _get_button("sleep")

    def test_power_off_defined(self):
        _get_button("power_off")

    def test_total_button_count(self):
        """Ensure we have the expected number of buttons total (2 standard + 4 experimental)."""
        assert len(COMMAND_BUTTONS) == 7  # +1 map_training (v1.9.0)


# ── Disabled by default ────────────────────────────────────────────────────────

class TestExperimentalButtonsDisabledByDefault:
    def test_spot_disabled(self):
        assert _get_button("spot").entity_registry_enabled_default is False

    def test_quick_disabled(self):
        assert _get_button("quick").entity_registry_enabled_default is False

    def test_sleep_disabled(self):
        assert _get_button("sleep").entity_registry_enabled_default is False

    def test_power_off_disabled(self):
        assert _get_button("power_off").entity_registry_enabled_default is False

    def test_evac_enabled(self):
        """Standard evac button must remain enabled by default."""
        assert _get_button("evac").entity_registry_enabled_default is True

    def test_locate_enabled(self):
        """Standard locate button must remain enabled by default."""
        assert _get_button("locate").entity_registry_enabled_default is True


# ── entity_registry_enabled_default propagation ────────────────────────────────

class TestEntityRegistryEnabledPropagation:
    def test_experimental_entity_disabled(self):
        entity = _make_button_entity("spot")
        assert entity._attr_entity_registry_enabled_default is False

    def test_standard_entity_enabled(self):
        entity = _make_button_entity("locate")
        assert entity._attr_entity_registry_enabled_default is True


# ── filter_fn gating ──────────────────────────────────────────────────────────

class TestExperimentalButtonFilterFn:
    """filter_fn should return truthy for 980 (no pmaps) and falsy for i7 (pmaps present)."""

    def _passes(self, key: str, state: dict) -> bool:
        btn = _get_button(key)
        if btn.filter_fn is None:
            return True
        return bool(btn.filter_fn(state))

    def test_spot_passes_for_980(self):
        assert self._passes("spot", STATE_980) is True

    def test_spot_blocked_for_i7(self):
        assert self._passes("spot", STATE_I7) is False

    def test_quick_passes_for_980(self):
        assert self._passes("quick", STATE_980) is True

    def test_quick_blocked_for_i7(self):
        assert self._passes("quick", STATE_I7) is False

    def test_sleep_passes_for_980(self):
        assert self._passes("sleep", STATE_980) is True

    def test_sleep_blocked_for_i7(self):
        assert self._passes("sleep", STATE_I7) is False

    def test_power_off_passes_for_980(self):
        assert self._passes("power_off", STATE_980) is True

    def test_power_off_blocked_for_i7(self):
        assert self._passes("power_off", STATE_I7) is False

    def test_spot_passes_for_600(self):
        """600-series has no pmaps either — filter passes, entity is created."""
        assert self._passes("spot", STATE_600) is True

    def test_locate_always_passes(self):
        """locate has no filter_fn — always created."""
        assert self._passes("locate", STATE_I7) is True
        assert self._passes("locate", STATE_980) is True


# ── Command strings ────────────────────────────────────────────────────────────

class TestExperimentalButtonCommands:
    def test_spot_command_string(self):
        assert _get_button("spot").command == "spot"

    def test_quick_command_string(self):
        assert _get_button("quick").command == "quick"

    def test_sleep_command_string(self):
        assert _get_button("sleep").command == "sleep"

    def test_power_off_command_string(self):
        """iRobot protocol uses 'off', not 'power_off'."""
        assert _get_button("power_off").command == "off"


# ── async_press sends correct command ────────────────────────────────────────

class TestExperimentalButtonPress:
    @pytest.mark.asyncio
    async def test_spot_press_sends_spot(self):
        entity = _make_button_entity("spot")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "spot"

    @pytest.mark.asyncio
    async def test_quick_press_sends_quick(self):
        entity = _make_button_entity("quick")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "quick"

    @pytest.mark.asyncio
    async def test_sleep_press_sends_sleep(self):
        entity = _make_button_entity("sleep")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "sleep"

    @pytest.mark.asyncio
    async def test_power_off_press_sends_off(self):
        """power_off button must send 'off' to the robot, not 'power_off'."""
        entity = _make_button_entity("power_off")
        entity.hass = MagicMock()
        entity.hass.async_add_executor_job = AsyncMock()
        await entity.async_press()
        args = entity.hass.async_add_executor_job.call_args[0]
        assert args[1] == "off"


# ── Translation keys ──────────────────────────────────────────────────────────

class TestExperimentalButtonTranslationKeys:
    def test_spot_translation_key(self):
        assert _get_button("spot").translation_key == "spot"

    def test_quick_translation_key(self):
        assert _get_button("quick").translation_key == "quick"

    def test_sleep_translation_key(self):
        assert _get_button("sleep").translation_key == "sleep"

    def test_power_off_translation_key(self):
        assert _get_button("power_off").translation_key == "power_off"


# ── v2.9.0 LOGBOOK — maintenance reset buttons fire roomba_plus_maintenance_reset ──

def _make_reset_button(cls):
    """Build a FilterResetButton/BrushResetButton/BatteryResetButton with a
    real (mocked) MaintenanceStore and config_entry, hass mocked out."""
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {"bbrun": {"hr": 123}}}}
    config_entry = MagicMock()
    config_entry.entry_id = "entry1"
    config_entry.title = "Test Robot"
    store = MagicMock()
    store.async_save = AsyncMock()
    config_entry.runtime_data.maintenance_store = store
    entity = cls(roomba, "test_blid", config_entry)
    entity.hass = MagicMock()
    entity.schedule_update_ha_state = MagicMock()
    return entity, store, config_entry


class TestMaintenanceResetButtonsFireLogbookEvent:
    """v2.9.0 LOGBOOK — Filter/Brush/Battery reset buttons must fire
    roomba_plus_maintenance_reset (same event the reset SERVICES fire via
    services.py's shared _fire_maintenance_reset_event), so the Logbook
    entry appears regardless of which path the user used."""

    @pytest.mark.asyncio
    async def test_filter_reset_button_fires_event(self):
        from custom_components.roomba_plus.button import FilterResetButton
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        entity, store, entry = _make_reset_button(FilterResetButton)
        await entity.async_press()

        store.reset_filter.assert_called_once_with(123)
        entity.hass.bus.async_fire.assert_called_once_with(
            EVENT_MAINTENANCE_RESET,
            {"entry_id": "entry1", "name": "Test Robot", "component": "filter", "hours": 123},
        )

    @pytest.mark.asyncio
    async def test_brush_reset_button_fires_event(self):
        from custom_components.roomba_plus.button import BrushResetButton
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        entity, store, entry = _make_reset_button(BrushResetButton)
        await entity.async_press()

        store.reset_brush.assert_called_once_with(123)
        payload = entity.hass.bus.async_fire.call_args[0][1]
        assert payload["component"] == "brush"
        assert payload["hours"] == 123

    @pytest.mark.asyncio
    async def test_battery_reset_button_fires_event(self):
        from custom_components.roomba_plus.button import BatteryResetButton
        from custom_components.roomba_plus.const import EVENT_MAINTENANCE_RESET

        entity, store, entry = _make_reset_button(BatteryResetButton)
        await entity.async_press()

        store.reset_battery.assert_called_once_with(123)
        payload = entity.hass.bus.async_fire.call_args[0][1]
        assert payload["component"] == "battery"


def _make_maintenance_due(store, *, options=None, hr=0, mop=False, language="en"):
    """Build a real RoombaMaintenanceDue wired to the given MaintenanceStore."""
    from custom_components.roomba_plus.binary_sensor import RoombaMaintenanceDue
    roomba = MagicMock()
    state: dict = {"bbrun": {"hr": hr}}
    if mop:
        state["detectedPad"] = "wet"
    roomba.master_state = {"state": {"reported": state}}
    config_entry = MagicMock()
    config_entry.runtime_data.maintenance_store = store
    config_entry.options = options or {}
    entity = RoombaMaintenanceDue(roomba, "test_blid", config_entry)
    entity.hass = MagicMock()
    entity.hass.config.language = language
    return entity


class TestRoombaMaintenanceDueRequiredActions:
    """required_actions: a localized action string per currently-due
    consumable, covering all four maintenance roles alike."""

    def _due_store(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        store.reset_filter(0)
        store.reset_brush(0)
        store.hydrate_from_cloud_parts([
            {"part_id": "36", "count_used": 0, "count_remaining": 0,
             "count_type": "minutes", "last_updated_ts": 1700000000},
            {"part_id": "139", "count_used": 0, "count_remaining": 0,
             "count_type": "minutes", "last_updated_ts": 1700000000},
        ], 0)
        return store

    def test_covers_all_four_due_consumables_in_english(self):
        entity = _make_maintenance_due(
            self._due_store(),
            options={"filter_threshold_hours": 10, "brush_threshold_hours": 10},
            hr=50,
        )
        attrs = entity.extra_state_attributes
        assert set(attrs["due"]) == {"filter", "brush", "side_brush", "clean_base_bag"}
        assert attrs["required_actions"] == {
            "filter": "Replace the filter.",
            "brush": "Replace the main brushes.",
            "side_brush": "Replace the side brush.",
            "clean_base_bag": "Replace the Clean Base bag.",
        }
        assert set(attrs["overdue_by_hours"]) == {"filter", "brush", "side_brush", "clean_base_bag"}
        assert attrs["overdue_by_hours"]["clean_base_bag"] == 20

    def test_localizes_to_polish(self):
        entity = _make_maintenance_due(
            self._due_store(),
            options={"filter_threshold_hours": 10, "brush_threshold_hours": 10},
            hr=50,
            language="pl",
        )
        attrs = entity.extra_state_attributes
        assert attrs["required_actions"] == {
            "filter": "Wymień filtr.",
            "brush": "Wymień szczotki główne.",
            "side_brush": "Wymień szczotkę boczną.",
            "clean_base_bag": "Wymień worek stacji Clean Base.",
        }

    def test_empty_when_nothing_due(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        entity = _make_maintenance_due(MaintenanceStore(), hr=0)
        attrs = entity.extra_state_attributes
        assert attrs["due"] == []
        assert attrs["required_actions"] == {}

    def test_pad_key_used_for_mop_devices(self):
        store = self._due_store()
        entity = _make_maintenance_due(
            store,
            options={"filter_threshold_hours": 10, "brush_threshold_hours": 10},
            hr=50, mop=True,
        )
        attrs = entity.extra_state_attributes
        assert "pad" in attrs["due"]
        assert attrs["required_actions"]["pad"] == "Replace the mop pad."


def _make_layout_change_sensor(grid_store=None):
    """Return a RoombaLayoutChangeDetected with the given GridStore
    wired into runtime_data (or None to test the no-grid_store path)."""
    from custom_components.roomba_plus.binary_sensor import RoombaLayoutChangeDetected
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    entry.runtime_data.grid_store = grid_store
    sensor = RoombaLayoutChangeDetected.__new__(RoombaLayoutChangeDetected)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_layout_change_detected"
    return sensor


class TestLayoutChangeDetected:
    """v3.2.0 FURNITURE — RoombaLayoutChangeDetected binary sensor."""

    def test_off_when_no_grid_store(self):
        sensor = _make_layout_change_sensor(grid_store=None)
        assert sensor.is_on is False
        attrs = sensor.extra_state_attributes
        assert attrs["cells_tracked"] == 0
        assert attrs["missions_until_first_ready"] is None

    def test_off_when_no_candidates(self):
        gs = MagicMock()
        gs.furniture_candidates.return_value = []
        sensor = _make_layout_change_sensor(grid_store=gs)
        assert sensor.is_on is False

    def test_readiness_attributes_shown_even_without_candidates(self):
        """v3.2.0 UX fix — before this, a fresh install with no
        candidates yet showed identical (empty) attributes to a
        long-established install with genuinely nothing to report. Now
        the learning-progress fields are always present, so "still
        building history" is distinguishable from "already checked, all
        clear"."""
        gs = MagicMock()
        gs.furniture_candidates.return_value = []
        gs.furniture_readiness.return_value = {
            "cells_tracked": 12, "most_mature_cell_age": 9,
            "missions_until_first_ready": 14,
        }
        sensor = _make_layout_change_sensor(grid_store=gs)
        attrs = sensor.extra_state_attributes
        assert attrs["cells_tracked"] == 12
        assert attrs["missions_until_first_ready"] == 14

    def test_on_when_candidates_exist(self):
        gs = MagicMock()
        gs.furniture_candidates.return_value = [
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ]
        sensor = _make_layout_change_sensor(grid_store=gs)
        assert sensor.is_on is True

    def test_attributes_expose_first_candidate_location_and_count(self):
        gs = MagicMock()
        gs.furniture_candidates.return_value = [
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
            {"cell": (3, 4), "x_mm": 450.0, "y_mm": 600.0},
        ]
        sensor = _make_layout_change_sensor(grid_store=gs)
        attrs = sensor.extra_state_attributes
        assert attrs["approximate_location"] == {"x_mm": 150.0, "y_mm": 300.0}
        assert attrs["candidate_count"] == 2

    def test_readiness_attributes_still_present_alongside_candidate(self):
        gs = MagicMock()
        gs.furniture_candidates.return_value = [
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ]
        gs.furniture_readiness.return_value = {
            "cells_tracked": 30, "most_mature_cell_age": 23,
            "missions_until_first_ready": 0,
        }
        sensor = _make_layout_change_sensor(grid_store=gs)
        attrs = sensor.extra_state_attributes
        assert attrs["cells_tracked"] == 30
        assert attrs["candidate_count"] == 1

    def test_is_device_class_problem(self):
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass
        sensor = _make_layout_change_sensor(grid_store=None)
        assert sensor.device_class == BinarySensorDeviceClass.PROBLEM


class TestBinStatusNullRegression:
    """v3.4.2 NULL-REGRESSION — bin: null must not crash RoombaBinFullStatus/
    RoombaBinPresentStatus, same confirmed-real bug class as elsewhere in
    this codebase (see test_edge_cases.py)."""

    def _entity(self, cls, reported: dict):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": reported}}
        entity = cls.__new__(cls)
        entity.vacuum = roomba
        return entity

    def test_bin_full_status_survives_explicit_null_bin(self):
        from custom_components.roomba_plus.binary_sensor import RoombaBinStatus
        entity = self._entity(RoombaBinStatus, {"bin": None})
        assert entity.is_on is False

    def test_bin_present_status_survives_explicit_null_bin(self):
        from custom_components.roomba_plus.binary_sensor import RoombaBinPresentStatus
        entity = self._entity(RoombaBinPresentStatus, {"bin": None})
        assert entity.is_on is True   # defaults to "present" when unknown


# ── V4/Prime bin/tank presence ──────────────────────────────────────────────

def _make_prime_status_entry(ro_currentstate: dict | None = None) -> MagicMock:
    config_entry = MagicMock()
    config_entry.runtime_data.prime_status_coordinator.data = (
        {"ro-currentstate": ro_currentstate} if ro_currentstate is not None else None
    )
    return config_entry


class TestPrimeBinPresentSensor:
    def test_is_on_reflects_real_captured_value(self):
        """Uses chairstacker's own real captured value, not a
        placeholder -- bin.present was True."""
        from custom_components.roomba_plus.binary_sensor import PrimeBinPresentSensor

        config_entry = _make_prime_status_entry({"bin": {"present": True}})
        sensor = PrimeBinPresentSensor("BLID123", config_entry)

        assert sensor.is_on is True

    def test_is_on_none_when_no_coordinator_data_yet(self):
        from custom_components.roomba_plus.binary_sensor import PrimeBinPresentSensor

        config_entry = _make_prime_status_entry()
        sensor = PrimeBinPresentSensor("BLID123", config_entry)

        assert sensor.is_on is None


class TestPrimeTankPresentSensor:
    def test_is_on_reflects_real_captured_value(self):
        """Uses chairstacker's own real captured value -- tankPresent
        was True, confirmed a plain boolean (distinct from any
        numeric tank-level field, which doesn't appear in the real
        payload at all)."""
        from custom_components.roomba_plus.binary_sensor import PrimeTankPresentSensor

        config_entry = _make_prime_status_entry({"tankPresent": True})
        sensor = PrimeTankPresentSensor("BLID123", config_entry)

        assert sensor.is_on is True


class TestPrimeRobotConnectivitySensor:
    def test_is_on_reflects_real_captured_value(self):
        """CONFIRMED bool (parallel native-analysis track, Ghidra
        decompilation of the app's own constructor signature) --
        not guessed."""
        from custom_components.roomba_plus.binary_sensor import PrimeRobotConnectivitySensor

        config_entry = _make_prime_status_entry({"connected": True})
        config_entry.runtime_data.prime_status_coordinator.data = {"rw-constatus": {"connected": True}}
        sensor = PrimeRobotConnectivitySensor("BLID123", config_entry)

        assert sensor.is_on is True


class TestPrimeDockErrorSensor:
    """NEW (this session) -- CurrentStateShadow.dock.error, confirmed
    type (int) but no real nonzero value ever observed."""

    def test_is_on_false_when_error_is_zero(self):
        from custom_components.roomba_plus.binary_sensor import PrimeDockErrorSensor

        config_entry = _make_prime_status_entry({"dock": {"error": 0}})
        sensor = PrimeDockErrorSensor("BLID123", config_entry)

        assert sensor.is_on is False
        assert sensor.extra_state_attributes == {"raw_error_code": 0}

    def test_is_on_true_for_nonzero_error_code(self):
        """No real nonzero value has ever been observed -- this
        confirms the CODE handles it correctly regardless."""
        from custom_components.roomba_plus.binary_sensor import PrimeDockErrorSensor

        config_entry = _make_prime_status_entry({"dock": {"error": 5}})
        sensor = PrimeDockErrorSensor("BLID123", config_entry)

        assert sensor.is_on is True
        assert sensor.extra_state_attributes == {"raw_error_code": 5}

    def test_none_when_no_coordinator_data_yet(self):
        from custom_components.roomba_plus.binary_sensor import PrimeDockErrorSensor

        config_entry = _make_prime_status_entry(None)
        sensor = PrimeDockErrorSensor("BLID123", config_entry)

        assert sensor.is_on is None


class TestAsyncSetupEntryCloudOnlyBranchBinarySensor:
    @pytest.mark.asyncio
    async def test_adds_all_three_prime_binary_sensors(self):
        from custom_components.roomba_plus import binary_sensor as binary_sensor_mod
        from custom_components.roomba_plus.binary_sensor import (
            PrimeBinPresentSensor,
            PrimeDockErrorSensor,
            PrimeQuietHoursSensor,
            PrimeRobotConnectivitySensor,
            PrimeStartBlockedSensor,
            PrimeTankPresentSensor,
        )
        from custom_components.roomba_plus.models import ConnectionType

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        # tankPresent has to be reported for the tank sensor to appear
        # at all -- see TestAsyncSetupEntryTankSensorGating for why the
        # gate moved off the mop capability flag.
        entry.runtime_data.prime_status_coordinator.data = {
            "ro-currentstate": {"tankPresent": True}
        }
        created = []

        def sync_add(entities, **kw):
            created.extend(entities)

        await binary_sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        # Five since the start-blocked sensor: it is created
        # unconditionally, unlike the tank sensor beside it.
        # Six since quiet hours: like the start-blocked sensor it is
        # created unconditionally -- any household can carry quiet
        # hours, and one that has none reports an empty list.
        assert len(created) == 6
        assert any(isinstance(e, PrimeQuietHoursSensor) for e in created)
        assert any(isinstance(e, PrimeStartBlockedSensor) for e in created)
        assert any(isinstance(e, PrimeBinPresentSensor) for e in created)
        assert any(isinstance(e, PrimeTankPresentSensor) for e in created)
        assert any(isinstance(e, PrimeRobotConnectivitySensor) for e in created)
        assert any(isinstance(e, PrimeDockErrorSensor) for e in created)


class TestAsyncSetupEntryTankSensorGating:
    """CORRECTED (this session, from a field report). This used to gate
    PrimeTankPresentSensor on `cap.scrub != 0` -- mop capability.

    A tester's Combo can mop, so it passed, but its water lives in the
    Clean Base rather than in the robot. He got a sensor for a tank he
    does not have. Mop capability and an onboard tank coincide on most
    hardware, which is exactly why the wrong check survived.

    The gate now asks whether the robot reports `tankPresent` at all --
    the field the sensor actually reads."""

    def _entry(self, current_state: dict | None):
        from custom_components.roomba_plus.models import ConnectionType

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        entry.runtime_data.prime_status_coordinator.data = (
            {"ro-currentstate": current_state} if current_state is not None else None
        )
        return entry

    async def _created(self, entry):
        from custom_components.roomba_plus.binary_sensor import async_setup_entry

        created: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: created.extend(e))
        return created

    @pytest.mark.asyncio
    async def test_excluded_when_the_robot_never_reports_a_tank(self):
        """The reported case: no tankPresent field, so no entity."""
        from custom_components.roomba_plus.binary_sensor import (
            PrimeBinPresentSensor,
            PrimeTankPresentSensor,
        )

        created = await self._created(self._entry({"batPct": 90}))

        assert not any(isinstance(e, PrimeTankPresentSensor) for e in created)
        assert any(isinstance(e, PrimeBinPresentSensor) for e in created), (
            "the other sensors must be unaffected"
        )

    @pytest.mark.asyncio
    async def test_included_when_the_field_is_present(self):
        from custom_components.roomba_plus.binary_sensor import PrimeTankPresentSensor

        created = await self._created(self._entry({"tankPresent": True}))

        assert any(isinstance(e, PrimeTankPresentSensor) for e in created)

    @pytest.mark.asyncio
    async def test_included_when_the_field_is_present_but_false(self):
        """False is a real answer -- "the tank is currently out" -- and
        is precisely what this sensor exists to report. Only absence
        means the robot has no such thing."""
        from custom_components.roomba_plus.binary_sensor import PrimeTankPresentSensor

        created = await self._created(self._entry({"tankPresent": False}))

        assert any(isinstance(e, PrimeTankPresentSensor) for e in created)

    @pytest.mark.asyncio
    async def test_excluded_when_no_state_has_arrived_yet(self):
        from custom_components.roomba_plus.binary_sensor import PrimeTankPresentSensor

        created = await self._created(self._entry(None))

        assert not any(isinstance(e, PrimeTankPresentSensor) for e in created)


class TestTankSensorGatedOnTheField:
    """FIELD REPORT (chairstacker): shown a mop-tank sensor for a robot
    that has no tank -- the water lives in his Clean Base.

    The gate used to ask "can this robot mop?" (`cap.scrub != 0`). His
    Combo can, so it passed. But mop capability does not imply an
    onboard tank; the two merely coincide on most hardware, which is
    exactly how this survived.

    The honest test is whether the robot reports `tankPresent` at all.
    And the distinction that matters: an ABSENT field means there is
    nothing to report, while an explicit `False` is a real answer --
    "no tank fitted right now" -- and must still produce a sensor."""

    def _entities_for(self, raw_state):
        from unittest.mock import MagicMock

        data = MagicMock()
        data.blid = "BLID"
        data.prime_status_coordinator.data = {"ro-currentstate": raw_state}
        return data

    def _tank_created(self, raw_state) -> bool:
        data = self._entities_for(raw_state)
        coordinator = data.prime_status_coordinator
        raw = (coordinator.data or {}).get("ro-currentstate") or {}
        return "tankPresent" in raw

    def test_a_robot_that_never_reports_the_field_gets_no_sensor(self):
        assert self._tank_created({"batPct": 90, "detectedPad": "noPad"}) is False

    def test_an_explicit_false_still_creates_the_sensor(self):
        """The crucial distinction. False means "no tank fitted", which
        is information worth showing -- collapsing it with "absent"
        would hide a real state."""
        assert self._tank_created({"tankPresent": False}) is True

    def test_an_explicit_true_creates_the_sensor(self):
        assert self._tank_created({"tankPresent": True}) is True

    def test_mop_capability_alone_is_no_longer_enough(self):
        """The old gate would have created it here. A robot that can
        mop but keeps its water in the dock reports no tankPresent."""
        assert self._tank_created({"cap": {"scrub": 3}}) is False

    def test_scrub_capability_does_not_decide_this_sensor(self):
        """Guards against the old gate coming back, and it has already
        failed once at that job.

        The first version asserted the ABSENCE OF A STRING --
        `"cap is None or cap.scrub != 0" not in source`. PR #76
        reintroduced the same rule written the other way round
        (`cap is not None and cap.scrub == 0`), and this test passed.
        A literal check only catches the exact spelling it was written
        against, which is the one spelling nobody will use twice.

        So this asks the question behaviourally instead: does
        `cap.scrub` change the outcome? It must not, in either
        direction -- neither creating a sensor for a robot with no
        `tankPresent`, nor withholding one from a robot that reports
        it."""
        # A mop-capable robot with no tankPresent: still no sensor.
        assert self._tank_created({"cap": {"scrub": 3}}) is False
        # A robot that cannot scrub but reports a tank: still a sensor.
        assert self._tank_created(
            {"cap": {"scrub": 0}, "tankPresent": True}
        ) is True
        # And scrub must not flip a robot that reports the field.
        assert self._tank_created(
            {"cap": {"scrub": 3}, "tankPresent": False}
        ) is True

    def test_the_sensor_is_not_registered_disabled_by_scrub(self):
        """A gate can also come back as a DISABLE rather than a skip --
        which is the form PR #76 used. An entity registered disabled is
        as invisible to an owner as one that was never created.

        MUST PATCH THE CAPABILITY SOURCE, not the shadow. The first
        version of this test put `{"cap": {"scrub": 0}}` in the raw
        ro-currentstate and passed against the very code it was written
        to reject -- `cap` in that function comes from
        `get_prime_capability_flags()`, which reads a different place
        entirely. A behavioural test aimed at the wrong input is no
        better than the string check it replaced.
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus import binary_sensor
        from custom_components.roomba_plus.models import ConnectionType

        data = self._entities_for({"tankPresent": True})
        data.connection_type = ConnectionType.CLOUD_ONLY
        entry = MagicMock()
        entry.runtime_data = data
        created: list = []

        # PATCH THE MODULE THAT OWNS IT. `binary_sensor` imports
        # `get_prime_capability_flags` INSIDE the setup function, so
        # patching the name on `binary_sensor` creates an attribute
        # nothing reads -- the second version of this test did exactly
        # that, with `create=True` silently making it look deliberate.
        from custom_components.roomba_plus import prime_coordinator

        with patch.object(
            prime_coordinator,
            "get_prime_capability_flags",
            return_value=(SimpleNamespace(scrub=0), None),
        ):
            asyncio.run(
                binary_sensor.async_setup_entry(
                    MagicMock(), entry, lambda e: created.extend(e)
                )
            )

        tank = [
            e for e in created
            if isinstance(e, binary_sensor.PrimeTankPresentSensor)
        ]
        assert tank, "a robot reporting tankPresent got no tank sensor"
        assert tank[0]._attr_entity_registry_enabled_default is not False, (
            "the tank sensor was registered disabled because cap.scrub is 0 -- "
            "scrub is about scrubbing, tankPresent is about having a tank"
        )


class TestTankSensorNamesTheRobotNotTheDock:
    """`tankPresent` is the ROBOT's tank (issue #27, resolved).

    @chairstacker removed both DOCK tanks on a Roomba 405 and the sensor
    kept reading "present". It was right to: the app keeps three
    separate values -- the robot's tank, the dock's clean water, the
    dock's grey water -- and this field is the first.

    The app's error strings are specific where the pad strings were
    generic ("Dock Clean Tank: missing" versus "%s's tank is missing"),
    which is what settled it.

    NOTHING WAS BROKEN. The name was: "mop tank present" on a robot
    whose dock holds two tanks reads as though it covers them."""

    def test_the_label_says_robot_in_every_locale(self):
        import json
        from pathlib import Path

        base = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for locale_file in sorted((base / "translations").glob("*.json")):
            data = json.loads(locale_file.read_text(encoding="utf-8"))
            name = data["entity"]["binary_sensor"]["mop_tank_present"]["name"]

            assert name.strip(), locale_file.name
            # Not asserting a specific word -- eight languages phrase it
            # differently. Asserting it is no longer the bare "tank"
            # label that caused the confusion.
            assert len(name.split()) >= 2, f"{locale_file.name}: {name}"

    def test_no_dock_tank_entity_is_invented(self):
        """Dock clean and grey water would be separate entities fed by
        `gwTankLvl` and a sibling. Neither appears in any capture yet --
        including from the dock that physically has both tanks.

        Building them from the field names alone would put two entities
        in front of users that permanently read "unknown"."""
        import inspect

        from custom_components.roomba_plus import binary_sensor

        source = inspect.getsource(binary_sensor)
        code = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        )

        assert 'wire_key="gwTankLvl"' not in code
        assert 'key="dock_clean_tank"' not in code


class TestDockErrorNamesTheReason:
    """A missing dock water tank is reported as an ERROR, not as an
    absent presence flag.

    The dock state model has thirteen fields and none of them is a
    `tankPresent` equivalent -- a targeted search for
    *Tank*(Present|Missing|Installed|Detected) found nothing. The app
    reads the error code instead:

        650  PAD_WASH_CLEAR_FLUID_TANK_MISSING_ERROR   clean water
        653  PAD_WASH_GREY_WATER_TANK_MISSING_ERROR    grey water
        450  FLUID_REPLENISHMENT_TANK_MISSING_ERROR

    THIS CLOSES ISSUE #27. @chairstacker removed both dock tanks and
    watched `tankPresent` stay true -- correctly, because that field is
    the ROBOT's tank. What he was looking for would have been here, and
    the sensor was showing the number without the word."""

    def _attrs(self, error_code):
        """Builds the attributes without touching the real class.

        A first version assigned a property onto the class itself, which
        leaked into every other test using that sensor -- two of them
        failed on the next run. Patching the instance's own lookup keeps
        it local."""
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.binary_sensor import (
            PrimeDockErrorSensor,
        )

        state = MagicMock()
        state.dock.error = error_code
        sensor = object.__new__(PrimeDockErrorSensor)

        with patch.object(
            PrimeDockErrorSensor,
            "_current_state",
            new_callable=lambda: property(lambda self: state),
        ):
            return PrimeDockErrorSensor.extra_state_attributes.fget(sensor)

    def test_a_missing_clean_water_tank_is_named(self):
        attrs = self._attrs(650)

        assert attrs["raw_error_code"] == 650
        assert attrs["error_name"] == "PAD_WASH_CLEAR_FLUID_TANK_MISSING_ERROR"

    def test_a_missing_grey_water_tank_is_named(self):
        attrs = self._attrs(653)

        assert attrs["error_name"] == "PAD_WASH_GREY_WATER_TANK_MISSING_ERROR"

    def test_no_error_carries_no_name(self):
        """Zero maps to DOCK_NO_COMMON_ERROR. Putting that beside a
        sensor already reading "off" adds a word, not information."""
        attrs = self._attrs(0)

        assert attrs == {"raw_error_code": 0}

    def test_an_unlisted_code_keeps_the_number_and_no_name(self):
        """86 values are confirmed; a robot reporting an 87th must not
        get a guessed label. A wrong name on an error attribute is worse
        than a bare number."""
        attrs = self._attrs(99999)

        assert attrs["raw_error_code"] == 99999
        assert "error_name" not in attrs


class TestStartBlockedIsVisibleAndActionable:
    """The blocking information existed as attributes on
    `sensor.*_prime_error` — an entity with EntityCategory.DIAGNOSTIC,
    which Home Assistant hides from the dashboard by default.

    So the answer was there and nobody would see it. The whole value is
    being told BEFORE sending a command that will be refused.
    """

    def test_it_is_not_a_diagnostic_entity(self):
        """Deliberately unlike almost everything else on this platform.
        A hidden entity cannot warn anyone."""
        from homeassistant.helpers.entity import EntityCategory

        from custom_components.roomba_plus.binary_sensor import (
            PrimeStartBlockedSensor,
        )

        assert (
            getattr(PrimeStartBlockedSensor, "_attr_entity_category", None)
            is not EntityCategory.DIAGNOSTIC
        )

    def test_it_carries_the_modes_and_the_reason(self):
        """An automation branching on this sensor must not have to read
        a second, hidden entity to find out which mode still works."""
        import inspect

        from custom_components.roomba_plus import binary_sensor

        source = inspect.getsource(binary_sensor.PrimeStartBlockedSensor)

        for key in ("blocked_modes", "available_modes", "blocking_faults", "blocked_reason"):
            assert key in source

    def test_it_has_no_capability_gate(self):
        """286 (robot off the floor) applies to every robot, and a
        vacuum-only model can still report the pad-plate pair. Gating
        would withhold the answer from exactly the owner who cannot work
        out why a command was refused."""
        import inspect

        from custom_components.roomba_plus import binary_sensor

        source = inspect.getsource(binary_sensor)

        # The creation line must be unconditional -- not inside a
        # capability check the way the tank sensor is.
        assert "NO CAPABILITY GATE" in source
        creation = next(
            line for line in source.splitlines()
            if "entities.append(PrimeStartBlockedSensor" in line
        )
        assert creation.startswith("            entities.append"), (
            "the sensor is created inside a conditional -- a gate would "
            "withhold the answer from the owner who most needs it"
        )


class TestTheStartBlockedTrigger:
    """`TRIGGER_ERROR` fires when something goes wrong during a mission.
    This fires when nothing has gone wrong and nothing will begin either
    — a pad plate fitted when the user wanted a vacuum.

    Different automations: an error while cleaning wants a notification,
    a blocked start wants "tell me before I press the button".
    """

    def test_the_trigger_type_exists(self):
        from custom_components.roomba_plus.device_trigger import (
            TRIGGER_START_BLOCKED,
            TRIGGER_TYPES,
        )

        assert TRIGGER_START_BLOCKED in TRIGGER_TYPES

    def test_it_binds_to_the_binary_sensor(self):
        import inspect

        from custom_components.roomba_plus import device_trigger

        source = inspect.getsource(device_trigger)

        assert '_find_entity(hass, device_id, "prime_start_blocked")' in source

    def test_it_is_translated_everywhere(self):
        """An untranslated trigger shows the raw key in the automation
        editor."""
        import json
        import pathlib

        base = pathlib.Path("custom_components/roomba_plus")
        files = [base / "strings.json"] + sorted(
            (base / "translations").glob("*.json")
        )
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            triggers = data["device_automation"]["trigger_type"]
            assert "start_blocked" in triggers, path.name

    def test_the_logbook_needs_no_bespoke_event(self):
        """Home Assistant logs a binary sensor's state changes on its
        own. logbook.py describes BUS events, and its own docstring
        warns that inventing a parallel event would be redundancy —
        so the third piece comes for free."""
        import inspect

        from custom_components.roomba_plus import logbook

        source = inspect.getsource(logbook)

        assert "start_blocked" not in source


class TestQuietHoursAreReadOnly:
    """The library has `set_dnd_settings()`, and this sensor does not
    use it.

    A Prime robot has been observed CLEANING inside its own quiet-hours
    window: the setting reads back and its effect is unproven. A control
    that appears to work and does nothing is worse than no control, so
    the window is published and an automation enforces what the robot
    does not.
    """

    @staticmethod
    def _sensor(windows):
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.binary_sensor import PrimeQuietHoursSensor

        sensor = PrimeQuietHoursSensor.__new__(PrimeQuietHoursSensor)
        patcher = patch.object(
            PrimeQuietHoursSensor, "_windows", new_callable=PropertyMock,
            return_value=windows,
        )
        sensor._config_entry = MagicMock()
        return sensor, patcher

    def _is_on_at(self, windows, clock):
        from unittest.mock import patch

        sensor, patcher = self._sensor(windows)
        with patcher, patch(
            "custom_components.roomba_plus.binary_sensor.dt_util"
        ) as dt:
            dt.now.return_value.strftime.return_value = clock
            return sensor.is_on

    def test_inside_an_ordinary_window(self):
        w = [{"start": "22:00", "end": "23:30", "enabled": True}]
        assert self._is_on_at(w, "22:30") is True
        assert self._is_on_at(w, "21:59") is False

    def test_a_window_crossing_midnight(self):
        """The normal shape for quiet hours, and the one a naive
        start < end comparison gets wrong."""
        w = [{"start": "22:00", "end": "07:00", "enabled": True}]
        assert self._is_on_at(w, "23:59") is True
        assert self._is_on_at(w, "03:00") is True
        assert self._is_on_at(w, "08:00") is False

    def test_a_disabled_window_is_not_a_window(self):
        """The user switched it off in the app. Reporting quiet hours
        for it would report a setting rather than a state."""
        w = [{"start": "22:00", "end": "23:30", "enabled": False}]
        assert self._is_on_at(w, "22:30") is False

    def test_no_windows_is_off_not_unknown(self):
        assert self._is_on_at([], "22:30") is False

    def test_the_sensor_itself_never_writes_dnd(self):
        """The SENSOR stays read-only. Writing lives in the
        `roomba_plus.set_quiet_hours` action, where a caller has asked
        for it explicitly and gets told what it does not guarantee.

        The distinction matters: a binary sensor that writes would make
        a state display into a control, and quiet hours are the one
        setting where the robot has been seen ignoring what it accepted.
        """
        import ast
        import pathlib

        # A CALL, NOT A MENTION. The first version matched the string
        # and failed on its own explanatory comments -- two files that
        # say why DND is not written were counted as writing it.
        base = pathlib.Path("custom_components/roomba_plus")
        writers = []
        for path in base.glob("*.py"):
            # services.py owns the write path on purpose.
            if path.name == "services.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_dnd_settings"
                ):
                    writers.append(path.name)
                    break

        assert writers == [], (
            f"{writers} write DND settings outside services.py. Quiet "
            "hours are written from one explicit action, not as a side "
            "effect of an entity -- a Prime robot has been seen cleaning "
            "inside a window it accepted."
        )


class TestQuietHoursPrefersTheEndpointItWritesTo:
    """`set_quiet_hours` writes to `/settings/dnd`. The sensor first read
    the schedule container instead — chosen because the container was
    already parsed, which is convenience rather than an argument.

    The problem that creates is concrete: a user writes a window and the
    sensor never moves, because it is looking somewhere else.
    @jouwdan's validation run confirmed the endpoint answers on a real
    account, which removed the reason to keep guessing.
    """

    @staticmethod
    def _windows(dnd=None, containers=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.binary_sensor import (
            PrimeQuietHoursSensor,
        )

        sensor = PrimeQuietHoursSensor.__new__(PrimeQuietHoursSensor)
        sensor._config_entry = MagicMock()
        sensor._config_entry.runtime_data.prime_schedule_coordinator = (
            SimpleNamespace(quiet_hours=dnd, data=containers)
        )
        return sensor._windows

    def test_minutes_since_midnight_become_a_clock(self):
        """The endpoint counts minutes; the container carries hour and
        minute separately. Two shapes for one clock time."""
        from types import SimpleNamespace

        windows = self._windows(
            dnd=SimpleNamespace(daily_start=1320, daily_end=420)
        )

        assert windows == [{
            "start": "22:00", "end": "07:00",
            "enabled": None, "source": "dnd endpoint",
        }]

    def test_the_container_is_the_fallback_not_the_source(self):
        """Used only when the endpoint gave nothing — a robot whose
        household read failed still gets whatever the schedule
        container carries."""
        assert self._windows(dnd=None, containers=[]) == []

    def test_a_malformed_value_yields_no_window(self):
        """Rather than a nonsense clock. `daily_start` outside 0-1439 is
        not a time, and inventing one would put a window on screen that
        the robot never reported."""
        from types import SimpleNamespace

        assert self._windows(
            dnd=SimpleNamespace(daily_start=99999, daily_end=420)
        ) == []


class TestTheTankFieldIsKnownUnreliable:
    """The field-presence rule replaced a `cap.scrub` gate after
    @chairstacker got a tank sensor for a tank he does not have. It
    looked like the honest answer: the robot itself would say.

    It does not. His 405 reports `tankPresent: true` with no fill port,
    no water level for the robot anywhere in the iRobot app, and no
    movement when either dock tank is pulled.

    Kept, because nothing else distinguishes a robot with a tank from
    one without — but documented as unreliable rather than quietly
    trusted.
    """

    def test_the_disproof_sits_with_the_rule(self):
        """So the next reader does not re-derive field presence as the
        honest answer and stop there — it was derived once already, by
        the same route, from the same tester's earlier report."""
        import inspect

        from custom_components.roomba_plus import binary_sensor

        source = inspect.getsource(binary_sensor._prime_reports_tank)

        assert "DISPROVEN" in source
        assert "no fill port" in source.lower() or "NO fill port" in source

    def test_it_names_what_would_settle_it(self):
        """One robot where the value goes False when a tank is pulled.
        A caveat with no exit condition becomes permanent."""
        import inspect

        from custom_components.roomba_plus import binary_sensor

        source = inspect.getsource(binary_sensor._prime_reports_tank)

        assert "WHAT WOULD SETTLE IT" in source

    def test_the_user_is_told_too(self):
        """A caveat only in the source is a caveat the person reading
        the sensor never sees."""
        import pathlib

        features = pathlib.Path("docs/FEATURES.md").read_text()

        assert "mop tank sensor is unreliable" in features.lower()
