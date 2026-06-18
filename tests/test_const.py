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
