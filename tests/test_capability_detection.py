"""Unit tests for capability detection helpers in const.py.

Tests has_pose, has_carpet_boost, has_smart_map, has_clean_base, is_mop
against realistic state dicts for each robot series.
"""
import pytest
import sys, os
sys.path.insert(0, "/tmp/roomba_plus_package")

from custom_components.roomba_plus.const import (
    has_carpet_boost,
    has_clean_base,
    has_pose,
    has_smart_map,
    is_mop,
)


# ── Fixture state dicts (representative per series) ───────────────────────────

STATE_980 = {
    # 900-series: top-level keys, no cap{} dict
    "carpetBoost": True,
    "vacHigh": False,
    "cleanMissionStatus": {"phase": "charge"},
    "pose": {"point": {"x": 0, "y": 0}, "theta": 0},
}

STATE_I7 = {
    "cap": {
        "carpetBoost": 1,
        "pose": 1,
    },
    "pmaps": [{"id": "abc123"}],
    "cleanMissionStatus": {"phase": "charge"},
}

STATE_I7_PLUS = {
    "cap": {"carpetBoost": 1, "pose": 1},
    "pmaps": [{"id": "abc123"}],
    "dock": {"fwVer": "1.2.3", "state": 300},
}

STATE_600 = {
    # No pose, no carpet boost, no pmaps
    "cleanMissionStatus": {"phase": "charge"},
    "bin": {"full": False},
}

STATE_BRAAVA = {
    "detectedPad": "reusable",
    "mopReady": {"tankPresent": True, "lidClosed": True},
    "pmaps": [{"id": "xyz789"}],
    "cap": {"pose": 1},
}

STATE_EMPTY = {}


class TestHasPose:
    def test_980_no_cap_dict(self):
        # 980 reports pose data but does not set cap.pose=1.
        # has_pose checks cap.pose — so 980 correctly returns False here.
        # The 980 map is handled via MapCapability.EPHEMERAL, not has_pose.
        assert has_pose(STATE_980) is False

    def test_i7_has_pose(self):
        assert has_pose(STATE_I7) is True

    def test_600_no_pose(self):
        assert has_pose(STATE_600) is False

    def test_braava_has_pose(self):
        assert has_pose(STATE_BRAAVA) is True

    def test_empty_state(self):
        assert has_pose(STATE_EMPTY) is False


class TestHasCarpetBoost:
    def test_980_top_level_key(self):
        assert has_carpet_boost(STATE_980) is True

    def test_i7_cap_flag(self):
        assert has_carpet_boost(STATE_I7) is True

    def test_600_no_carpet_boost(self):
        assert has_carpet_boost(STATE_600) is False

    def test_braava_no_carpet_boost(self):
        assert has_carpet_boost(STATE_BRAAVA) is False

    def test_empty_state(self):
        assert has_carpet_boost(STATE_EMPTY) is False

    def test_cap_flag_zero_means_no_boost(self):
        state = {"cap": {"carpetBoost": 0}}
        assert has_carpet_boost(state) is False


class TestHasSmartMap:
    def test_i7_has_smart_map(self):
        assert has_smart_map(STATE_I7) is True

    def test_braava_has_smart_map(self):
        assert has_smart_map(STATE_BRAAVA) is True

    def test_980_no_smart_map(self):
        assert has_smart_map(STATE_980) is False

    def test_600_no_smart_map(self):
        assert has_smart_map(STATE_600) is False

    def test_empty_pmaps_list(self):
        state = {"pmaps": []}
        assert has_smart_map(state) is False

    def test_empty_state(self):
        assert has_smart_map(STATE_EMPTY) is False


class TestHasCleanBase:
    def test_i7_plus_clean_base_fwver(self):
        assert has_clean_base(STATE_I7_PLUS) is True

    def test_i7_plus_clean_base_state_int(self):
        state = {"dock": {"state": 300}}
        assert has_clean_base(state) is True

    def test_i7_no_clean_base(self):
        state = {"dock": {}}
        assert has_clean_base(state) is False

    def test_980_no_clean_base(self):
        assert has_clean_base(STATE_980) is False

    def test_empty_state(self):
        assert has_clean_base(STATE_EMPTY) is False

    def test_dock_string_state_not_int(self):
        """dock.state as string should not trigger clean_base detection."""
        state = {"dock": {"state": "ok"}}
        assert has_clean_base(state) is False


class TestIsMop:
    def test_braava_is_mop(self):
        assert is_mop(STATE_BRAAVA) is True

    def test_roomba_is_not_mop(self):
        assert is_mop(STATE_I7) is False
        assert is_mop(STATE_980) is False
        assert is_mop(STATE_600) is False

    def test_empty_state(self):
        assert is_mop(STATE_EMPTY) is False
