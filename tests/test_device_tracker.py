"""Tests for the device_tracker platform (v2.9.0 DEVICE-TRACKER).

Tier-aware location reporting: SMART robots get room-level granularity via
the shared _resolve_smart_tier_room_state() function (same source as
RoombaMissionProgress's current_room), EPHEMERAL robots get "Angedockt"/
"Unterwegs" only for now (room/zone detection extension point, currently
returning None — see _resolve_ephemeral_tier_room's docstring).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_tracker(map_capability_value: str = "smart"):
    """Build a minimal RoombaDeviceTracker with stubbed vacuum/entry state."""
    from custom_components.roomba_plus.device_tracker import RoombaDeviceTracker

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}

    entry = MagicMock()
    entry.runtime_data.map_capability.value = map_capability_value

    tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
    tracker.vacuum = roomba
    tracker._blid = "TESTBLID"
    tracker.vacuum_state = {}
    tracker._config_entry = entry
    tracker.hass = MagicMock()
    tracker.hass.config.language = "de"
    return tracker, roomba, entry


def _set_state(roomba, phase: str = "", pose: dict | None = None):
    reported: dict = {"cleanMissionStatus": {"phase": phase}}
    if pose is not None:
        reported["pose"] = pose
    roomba.master_state = {"state": {"reported": reported}}


class TestLocationNameDockedFallback:
    """Docked/idle must always show the dock label, never "home"."""

    def test_docked_phase_shows_docked_label(self):
        tracker, roomba, _ = _make_tracker()
        _set_state(roomba, phase="charge")
        assert tracker.location_name == "Angedockt"

    def test_idle_empty_phase_shows_docked_label(self):
        tracker, roomba, _ = _make_tracker()
        _set_state(roomba, phase="")
        assert tracker.location_name == "Angedockt"


class TestLocationNameNullRegression:
    """v3.4.2 NULL-REGRESSION — cleanMissionStatus: null must not crash
    location_name, the same confirmed-real class of bug as bbrun/bin/cap
    elsewhere in this codebase (see test_edge_cases.py)."""

    def test_explicit_null_clean_mission_status_does_not_raise(self):
        tracker, roomba, _ = _make_tracker()
        roomba.master_state = {"state": {"reported": {"cleanMissionStatus": None}}}
        # Falls through to the empty-phase branch, same as a docked robot.
        assert tracker.location_name == "Angedockt"

    def test_docked_label_respects_language(self):
        tracker, roomba, _ = _make_tracker()
        tracker.hass.config.language = "en"
        _set_state(roomba, phase="stop")
        assert tracker.location_name == "Docked"

    def test_unknown_language_falls_back_to_english(self):
        tracker, roomba, _ = _make_tracker()
        tracker.hass.config.language = "ja"
        _set_state(roomba, phase="charge")
        assert tracker.location_name == "Docked"


class TestLocationNameSmartTier:
    """SMART-tier robots get room-level granularity, shared with
    RoombaMissionProgress's current_room via _resolve_smart_tier_room_state."""

    def test_returns_room_name_when_resolved(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="smart")
        _set_state(roomba, phase="run")
        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={"current_room": "Kitchen", "next_room": "Hallway"},
        ):
            assert tracker.location_name == "Kitchen"

    def test_falls_back_to_active_label_when_room_unknown(self):
        """No room resolved (e.g. no MTS mission, or estimates entirely
        unavailable) — must show the generic active-mission label, not
        None and not crash."""
        tracker, roomba, _ = _make_tracker(map_capability_value="smart")
        _set_state(roomba, phase="run")
        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={},
        ):
            assert tracker.location_name == "Unterwegs"


class TestLocationNameEphemeralTier:
    """v2.9.0 — EPHEMERAL tier (e.g. the 980) must ALWAYS get a sensible
    state, even though room/zone detection isn't available yet. The
    extension point (_resolve_ephemeral_tier_room) currently always
    returns None — confirmed structurally limited for dense-MQTT-sampling
    robots — but the platform around it is fully tier-agnostic, ready for
    when that's fixed.
    """

    def test_active_mission_shows_generic_fallback_not_none(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="ephemeral")
        _set_state(roomba, phase="run")
        assert tracker.location_name == "Unterwegs"

    def test_docked_shows_docked_label_same_as_smart_tier(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="ephemeral")
        _set_state(roomba, phase="charge")
        assert tracker.location_name == "Angedockt"

    def test_extension_point_returns_none_today(self):
        """Documents current behaviour explicitly — once EPHEMERAL room/
        zone detection is fixed, only this function's return value
        should need to change."""
        tracker, roomba, entry = _make_tracker(map_capability_value="ephemeral")
        result = tracker._resolve_ephemeral_tier_room(entry.runtime_data)
        assert result is None


class TestExtraStateAttributes:
    """Raw pose (x_mm/y_mm) always exposed when available, regardless of
    tier — for users who want their own zone logic externally."""

    def test_pose_converted_cm_to_mm(self):
        """v2.9.0 units fix: pose.point.x/y is in centimetres, not
        millimetres — must be converted, matching POSE_POINT_CM_TO_MM."""
        tracker, roomba, entry = _make_tracker()
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="charge", pose={"point": {"x": 120, "y": 45}})

        attrs = tracker.extra_state_attributes
        assert attrs["x_mm"] == 1200
        assert attrs["y_mm"] == 450

    def test_no_pose_data_omits_coordinates(self):
        tracker, roomba, entry = _make_tracker()
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="charge", pose=None)

        attrs = tracker.extra_state_attributes
        assert "x_mm" not in attrs
        assert "y_mm" not in attrs

    def test_room_and_next_room_exposed_during_active_smart_mission(self):
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        mts = MagicMock()
        mts.mission_id = "m1"
        entry.runtime_data.mission_timer_store = mts
        _set_state(roomba, phase="run")

        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={"current_room": "Kitchen", "next_room": "Hallway"},
        ):
            attrs = tracker.extra_state_attributes

        assert attrs["room"] == "Kitchen"
        assert attrs["next_room"] == "Hallway"

    def test_room_omitted_when_docked(self):
        """No active mission — room/next_room attributes must not appear
        at all (not even as None), since they're meaningless while docked."""
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        mts = MagicMock()
        mts.mission_id = "m1"
        entry.runtime_data.mission_timer_store = mts
        _set_state(roomba, phase="charge")

        attrs = tracker.extra_state_attributes
        assert "room" not in attrs
        assert "next_room" not in attrs

    def test_room_omitted_when_no_mission_timer_store(self):
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="run")

        attrs = tracker.extra_state_attributes
        assert "room" not in attrs


class TestNewStateFilter:
    def test_filters_on_mission_status_or_pose(self):
        tracker, _, _ = _make_tracker()
        assert tracker.new_state_filter({"cleanMissionStatus": {}}) is True
        assert tracker.new_state_filter({"pose": {}}) is True
        assert tracker.new_state_filter({"batPct": 50}) is False


class TestSourceType:
    def test_source_type_is_router(self):
        """ROUTER is the closest existing SourceType for a locally-
        determined, non-GPS data source — there is no dedicated 'robot
        odometry' source type in HA core."""
        from homeassistant.components.device_tracker import SourceType
        tracker, _, _ = _make_tracker()
        assert tracker.source_type == SourceType.ROUTER


class TestEntityRegistryEnabledDefault:
    """v2.10.3 — device tracker must be enabled by default.

    TrackerEntity.entity_registry_enabled_default returns False when both
    mac_address and device_info are None. Both are always None here:
    we use BLID for identity (no MAC), and TrackerEntity's own device_info
    is None by design ('device tracker entities should not create device
    registry entries'). Without _attr_entity_registry_enabled_default = True
    the entity is registered but disabled, invisible in the UI.

    Confirmed root cause of Thonno's field report: 'I don't seem to have
    that entity on my i7+' — the entity existed in the registry but was
    disabled by default on every installation regardless of robot tier.
    """

    def test_entity_registry_enabled_default_is_true(self):
        """entity_registry_enabled_default must return True on instances.
        HA's Entity base class mangles _attr_* keys internally, so the
        class-level __dict__ key name is not reliable to test — the
        property's runtime return value is what actually matters."""
        tracker, _, _ = _make_tracker()
        assert tracker.entity_registry_enabled_default is True

    def test_instance_entity_registry_enabled_default_is_true(self):
        tracker, _, _ = _make_tracker()
        assert tracker.entity_registry_enabled_default is True

