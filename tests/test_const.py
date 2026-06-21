"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import datetime
import pytest
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.sensor import _completion_rate_30d
from custom_components.roomba_plus.sensor import _area_cleaned_today
from custom_components.roomba_plus.sensor import _problem_zone_value
from custom_components.roomba_plus.sensor import _last_error_code_value
from custom_components.roomba_plus.sensor import _mission_store_value
from custom_components.roomba_plus.const import ERROR_CATALOGUE
from custom_components.roomba_plus.const import (
    has_carpet_boost,
    has_clean_base,
    has_pose,
    has_smart_map,
    is_mop,
)
from custom_components.roomba_plus.const import ERROR_CODE_LABELS
import time as _time_mod
from custom_components.roomba_plus.const import ROBOT_PROFILES


__make_record_seq = 0


def _iso(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_unique_id(days_ago):
    global __make_record_seq
    __make_record_seq += 1
    return f"m_{days_ago}_{__make_record_seq}"


def _make_record(days_ago=0, result="completed", area_sqft=400.0, zones=None):
    return {
        "id": _make_unique_id(days_ago),
        "started_at": _iso(days_ago),
        "ended_at": _iso(days_ago),
        "duration_min": 30,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": zones or [],
        "error_code": None,
        "bbrun_hr": 100,
    }


async def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        await store.async_append(r)
    return store


class TestErrorCatalogueBackwardCompat:
    def test_every_old_key_in_catalogue(self):
        """Every error code that existed in ERROR_CODE_LABELS is still present."""
        # Spot-check key codes that sensors rely on
        for code in [0, 2, 6, 14, 17, 18, 36, 46, 48, 68, 224]:
            assert code in ERROR_CATALOGUE, f"Code {code} missing from ERROR_CATALOGUE"

    def test_catalogue_entries_have_required_keys(self):
        for code, entry in ERROR_CATALOGUE.items():
            assert "label" in entry, f"Code {code} missing 'label'"
            assert "description" in entry, f"Code {code} missing 'description'"
            assert "action" in entry, f"Code {code} missing 'action'"

    def test_all_values_are_strings(self):
        for code, entry in ERROR_CATALOGUE.items():
            for field in ("label", "description", "action"):
                assert isinstance(entry[field], str), \
                    f"Code {code} field '{field}' is not a string"

    def test_derived_error_code_labels_matches_catalogue(self):
        for code, label in ERROR_CODE_LABELS.items():
            assert ERROR_CATALOGUE[code]["label"] == label, \
                f"Code {code}: ERROR_CODE_LABELS={label!r} != catalogue label={ERROR_CATALOGUE[code]['label']!r}"


class TestRF0IMAH:
    def test_i_series_battery_mah_corrected(self):
        """Field-validated: estCap median ≈ 2488 mAh on lewis firmware."""
        assert ROBOT_PROFILES["i"].battery_mah == 2488

    def test_i_series_was_not_1800(self):
        """Confirm old incorrect manufacturer spec value is gone."""
        assert ROBOT_PROFILES["i"].battery_mah != 1800

    def test_other_profiles_unchanged(self):
        """Ensure RF0-IMAH only touched i-series."""
        assert ROBOT_PROFILES["9"].battery_mah == 3300
        assert ROBOT_PROFILES["s"].battery_mah == 3300
        assert ROBOT_PROFILES["j"].battery_mah == 2700
        assert ROBOT_PROFILES["m"].battery_mah == 2600


# ── Capability detection helpers (merged from test_capability_detection.py) ───

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
