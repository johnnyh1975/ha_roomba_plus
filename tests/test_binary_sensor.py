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


def _mqtt_stale_sensor(phase="run", last_mqtt_message_ts=0.0, wifistat=None, mssn_strt_tm=None):
    """Build a minimal RoombaMqttStale with stubbed hass/vacuum/entry state.

    v2.9.0 — covers the enriched mqtt_watchdog Repair Issue (last known
    phase, actual silence duration, cloud connectivity cross-check).
    Previously this sensor/issue had zero test coverage at all.
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

class TestNotReadyConstant:
    def test_value_is_64(self):
        assert _NOT_READY_MAP_SAVING == 64


# ── is_on ─────────────────────────────────────────────────────────────────────

class TestMapSavingIsOn:
    def test_on_when_bit_6_set(self):
        sensor = _make_sensor(not_ready=64)
        assert sensor.is_on is True

    def test_off_when_not_ready_is_zero(self):
        sensor = _make_sensor(not_ready=0)
        assert sensor.is_on is False

    def test_off_when_cleanmissionstatus_absent(self):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        sensor = RoombaMapSavingStatus(roomba, "blid")
        assert sensor.is_on is False

    def test_on_when_bit_6_combined_with_others(self):
        """bit 6 set alongside other bits — still ON."""
        sensor = _make_sensor(not_ready=64 | 1 | 4)
        assert sensor.is_on is True

    def test_off_when_other_bits_set_but_not_bit_6(self):
        """bit 1 + bit 2 + bit 5 — no map saving."""
        sensor = _make_sensor(not_ready=1 | 2 | 32)
        assert sensor.is_on is False

    def test_off_when_not_ready_is_none(self):
        roomba = MagicMock()
        roomba.master_state = {
            "state": {"reported": {"cleanMissionStatus": {"notReady": None}}}
        }
        sensor = RoombaMapSavingStatus(roomba, "blid")
        # None treated as 0 via `or 0` guard — sensor must return False
        assert sensor.is_on is False

    def test_bitmask_values(self):
        """Exhaustive check: only multiples of 64 within reasonable range trigger ON."""
        sensor = _make_sensor(not_ready=0)
        for v in range(256):
            roomba = MagicMock()
            roomba.master_state = {
                "state": {"reported": {"cleanMissionStatus": {"notReady": v}}}
            }
            sensor2 = RoombaMapSavingStatus(roomba, "blid")
            expected = bool(v & 64)
            assert sensor2.is_on == expected, f"Failed for notReady={v}"


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
        assert sensor.new_state_filter({"cleanMissionStatus": {"notReady": 64}}) is True

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
        saving = self._sensor_with_state(64)
        done   = self._sensor_with_state(0)

        assert idle.is_on is False
        assert saving.is_on is True
        assert done.is_on is False

    def test_combined_with_other_not_ready_bits(self):
        """Map saving combined with 'new map' bit (1) — still ON."""
        sensor = self._sensor_with_state(64 | 1)
        assert sensor.is_on is True
        assert sensor.extra_state_attributes["not_ready_bitmask"] == 65
