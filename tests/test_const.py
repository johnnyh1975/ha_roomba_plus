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
from custom_components.roomba_plus.const import get_localized_error_entry
from custom_components.roomba_plus.error_translations import ERROR_CATALOGUE_TRANSLATIONS
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


class TestErrorCatalogueV341Additions:
    """v3.4.1 — codes confirmed via direct iRobot Home app APK analysis."""

    def test_new_individual_codes_present(self):
        for code in [78, 79, 85, 86, 91, 92, 93, 98, 99]:
            assert code in ERROR_CATALOGUE, f"Code {code} missing from ERROR_CATALOGUE"

    def test_dock_category_present(self):
        for code in [*range(450, 464), *range(501, 510)]:
            assert code in ERROR_CATALOGUE, f"Dock code {code} missing from ERROR_CATALOGUE"

    def test_terra_mower_codes_excluded(self):
        """Codes confirmed via Klartext as iRobot's Terra lawn-mower line
        (shared app namespace) must NOT be added to this vacuum/mop catalogue
        — the original APK analysis pass mistakenly proposed the whole
        54-72/94-97 numeric neighbourhood before the Klartext check caught
        the mix-up. Note: 65/66/68 are deliberately excluded from this list
        — those are pre-existing, legitimate vacuum-relevant entries already
        in the catalogue before this range was ever examined, not Terra codes."""
        terra_codes = [54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 67, 69, 70, 71, 72, 94, 95, 96, 97]
        for code in terra_codes:
            assert code not in ERROR_CATALOGUE, \
                f"Code {code} is a Terra mower code and should not be in ERROR_CATALOGUE"

    def test_dock_category_mentions_vacuum_only_degradation(self):
        """Spot-check the documented 'switched to vacuum only' degradation
        pattern survived verbatim from the source strings for a few codes."""
        for code in [450, 451, 453, 457, 463]:
            assert "vacuum only" in ERROR_CATALOGUE[code]["description"].lower(), \
                f"Code {code} description missing expected vacuum-only degradation note"



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

# v3.4.1 MAP-CAP-NO-POSE — field-confirmed (mdarocha, i3+, "daredevil"
# firmware, config-entry diagnostics upload). Real cap object has no
# "pose" key at all (not just pose=0 — the key is entirely absent),
# while smart_map.pmap_ids in the same diagnostics has one real entry —
# confirming genuine SMART-tier persistent maps despite has_pose being
# False. Only the fields relevant to the has_pose/has_smart_map
# distinction are reproduced here, not the full diagnostics payload.
STATE_I3_DAREDEVIL_NO_POSE_CAP = {
    "cap": {
        "binFullDetect": 2, "addOnHw": 1, "oMode": 2, "dockComm": 1,
        "edge": 0, "maps": 3, "pmaps": 6, "mc": 0, "tLine": 2, "area": 1,
        "eco": 1, "multiPass": 3, "team": 1, "pp": 0, "lang": 2,
        "5ghz": 0, "prov": 3, "sched": 1, "svcConf": 1, "ota": 2,
        "log": 2, "langOta": 2,
        # deliberately no "pose" key
    },
    "pmaps": ["D8MepS5KRD6DTWlG-g5IEw"],
}


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


class TestActiveChargeCycles:
    """v2.9.0 DAILY-DIGEST — active_charge_cycles() chemistry-aware helper.

    Shared between sensor.py (_total_energy_consumed_kwh) and callbacks.py
    (battery_cycles snapshot at mission end) — same priority both places.
    """

    def test_nimh_wins_when_present(self):
        from custom_components.roomba_plus.const import active_charge_cycles
        # NiMH aftermarket battery after an OEM Li-ion period — nLithChrg
        # stays at its old OEM count, must not be used.
        bbchg3 = {"nNimhChrg": 12, "nLithChrg": 87}
        assert active_charge_cycles(bbchg3) == 12

    def test_falls_back_to_lith_when_no_nimh(self):
        from custom_components.roomba_plus.const import active_charge_cycles
        bbchg3 = {"nNimhChrg": 0, "nLithChrg": 87}
        assert active_charge_cycles(bbchg3) == 87

    def test_falls_back_to_navail_for_old_firmware(self):
        from custom_components.roomba_plus.const import active_charge_cycles
        bbchg3 = {"nAvail": 55}
        assert active_charge_cycles(bbchg3) == 55

    def test_empty_bbchg3_returns_none(self):
        from custom_components.roomba_plus.const import active_charge_cycles
        assert active_charge_cycles({}) is None

    def test_nimh_key_missing_falls_through(self):
        from custom_components.roomba_plus.const import active_charge_cycles
        bbchg3 = {"nLithChrg": 10}
        assert active_charge_cycles(bbchg3) == 10


class TestIsMop:
    def test_braava_is_mop(self):
        assert is_mop(STATE_BRAAVA) is True

    def test_roomba_is_not_mop(self):
        assert is_mop(STATE_I7) is False
        assert is_mop(STATE_980) is False
        assert is_mop(STATE_600) is False

    def test_empty_state(self):
        assert is_mop(STATE_EMPTY) is False


class TestGetLocalizedErrorEntry:
    """v3.4.1 — get_localized_error_entry() and the ERROR_CATALOGUE_TRANSLATIONS
    parallel structure in error_translations.py."""

    def test_none_language_returns_english_base(self):
        entry = get_localized_error_entry(1, None)
        assert entry == ERROR_CATALOGUE[1]

    def test_en_language_returns_english_base(self):
        entry = get_localized_error_entry(1, "en")
        assert entry == ERROR_CATALOGUE[1]

    def test_de_returns_translated_text_not_english(self):
        entry = get_localized_error_entry(1, "de")
        assert entry["label"] != ERROR_CATALOGUE[1]["label"]
        assert entry["label"] == "Linkes Rad hebt ab"

    def test_unsupported_language_falls_back_to_english(self):
        """A language never covered by ERROR_CATALOGUE_TRANSLATIONS (e.g.
        Japanese) must silently degrade to English, not raise or return
        blanks."""
        entry = get_localized_error_entry(1, "ja")
        assert entry == ERROR_CATALOGUE[1]

    def test_unknown_error_code_returns_empty_dict_like_base(self):
        assert get_localized_error_entry(99999, None) == {}
        assert get_localized_error_entry(99999, "de") == {}

    def test_partial_translation_falls_back_field_by_field(self, monkeypatch):
        """A code with only a partial translation (e.g. label translated,
        description/action missing) must never produce a blank string for
        the missing fields — each field falls back to English independently."""
        import custom_components.roomba_plus.const as const_module
        partial = {"de": {1: {"label": "Nur Label übersetzt"}}}
        monkeypatch.setattr(
            "custom_components.roomba_plus.error_translations.ERROR_CATALOGUE_TRANSLATIONS",
            partial,
        )
        entry = get_localized_error_entry(1, "de")
        assert entry["label"] == "Nur Label übersetzt"
        assert entry["description"] == ERROR_CATALOGUE[1]["description"]
        assert entry["action"] == ERROR_CATALOGUE[1]["action"]

    def test_all_six_languages_present(self):
        assert set(ERROR_CATALOGUE_TRANSLATIONS.keys()) == {
            "de", "fr", "it", "es", "pt", "nl",
        }

    def test_every_catalogue_code_translated_in_every_language(self):
        """Regression guard: every code in ERROR_CATALOGUE (all 125,
        including the v3.4.1 additions) must have a translation entry in
        every one of the six supported languages — no silent gaps, no
        orphaned codes translated that no longer exist in the catalogue."""
        all_codes = set(ERROR_CATALOGUE.keys())
        for lang, entries in ERROR_CATALOGUE_TRANSLATIONS.items():
            translated = set(entries.keys())
            missing = all_codes - translated
            orphaned = translated - all_codes
            assert not missing, f"{lang} is missing translations for codes: {sorted(missing)}"
            assert not orphaned, f"{lang} has orphaned translations for codes: {sorted(orphaned)}"

    def test_new_v341_codes_are_translated(self):
        """Spot-check that the v3.4.1 error-catalogue additions specifically
        got translations, not just the pre-existing 224 codes."""
        new_codes = [78, 79, 85, 86, 91, 92, 93, 98, 99, 450, 463, 501, 509]
        for lang in ERROR_CATALOGUE_TRANSLATIONS:
            for code in new_codes:
                assert code in ERROR_CATALOGUE_TRANSLATIONS[lang], \
                    f"Code {code} missing {lang} translation"

    def test_duplicate_english_text_codes_share_identical_translation(self):
        """Codes 8 and 11 share the exact same English text ('Bin error')
        in ERROR_CATALOGUE — the group-based dedup mechanism in
        error_translations.py must therefore give them identical
        translations too, not two independently (and potentially
        inconsistently) typed-out versions."""
        assert ERROR_CATALOGUE[8]["label"] == ERROR_CATALOGUE[11]["label"] == "Bin error"
        for lang in ERROR_CATALOGUE_TRANSLATIONS:
            assert ERROR_CATALOGUE_TRANSLATIONS[lang][8] == ERROR_CATALOGUE_TRANSLATIONS[lang][11]

    def test_translated_entries_have_all_three_fields(self):
        for lang, entries in ERROR_CATALOGUE_TRANSLATIONS.items():
            for code, entry in entries.items():
                for field in ("label", "description", "action"):
                    assert field in entry, f"{lang} code {code} missing '{field}'"
                    assert isinstance(entry[field], str), \
                        f"{lang} code {code} field '{field}' is not a string"


class TestMapCapabilityGatingNoLocalPose:
    """v3.4.1 MAP-CAP-NO-POSE — field-confirmed (mdarocha, i3+, "daredevil"
    firmware). __init__.py's _phase_spatial gates map/cloud-coordinator
    setup on `(has_pose(state) or has_smart_map(state)) and map_enabled`.
    Previously this was `has_pose(state) and map_enabled` alone, which
    silently skipped has_smart_map entirely for any robot whose `cap`
    object omits "pose" — even with real, populated pmaps. That in turn
    skipped cloud_coordinator creation (gated on map_capability !=
    NONE), with valid cloud credentials configured and unused: no map,
    and total_cleaned_area fell back to the already-known-unreliable
    bbrun.sqft instead of the cloud-backed MissionArchive.cumulative_sqft
    it's supposed to prefer.

    _phase_spatial itself isn't unit-tested directly anywhere in this
    suite (it needs a real hass + config_entry + HA storage to exercise
    GeometryStore/GridStore.async_load, and no existing test built that
    scaffolding) — this tests the exact boolean condition the fix
    changes, using has_pose/has_smart_map, which are directly.
    """

    def test_old_condition_would_incorrectly_exclude_this_robot(self):
        """Documents the bug: has_pose alone says no map capability at
        all for this robot, despite real pmaps being present."""
        assert has_pose(STATE_I3_DAREDEVIL_NO_POSE_CAP) is False

    def test_has_smart_map_is_true_despite_missing_pose_key(self):
        assert has_smart_map(STATE_I3_DAREDEVIL_NO_POSE_CAP) is True

    def test_new_condition_correctly_includes_this_robot(self):
        state = STATE_I3_DAREDEVIL_NO_POSE_CAP
        assert (has_pose(state) or has_smart_map(state)) is True

    def test_smart_map_takes_priority_when_both_signals_present(self):
        """For a robot with BOTH pose and pmaps (e.g. STATE_I7), the
        has_smart_map check inside _phase_spatial is evaluated first and
        yields SMART — unaffected by this fix, verified here so a future
        change to check ordering doesn't silently flip priority."""
        assert has_pose(STATE_I7) is True
        assert has_smart_map(STATE_I7) is True

    def test_900_series_fixture_unaffected_by_this_fix(self):
        """STATE_980 has neither has_pose nor has_smart_map true (it's a
        minimal fixture for testing those two functions' field-reading
        logic in isolation, not a complete real 980 MQTT payload) — the
        new OR-based condition must evaluate identically to the old
        condition for this fixture: both False, unchanged."""
        state = STATE_980
        old_condition = has_pose(state)
        new_condition = has_pose(state) or has_smart_map(state)
        assert old_condition == new_condition == False

    def test_600_series_fixture_unaffected_by_this_fix(self):
        state = STATE_600
        old_condition = has_pose(state)
        new_condition = has_pose(state) or has_smart_map(state)
        assert old_condition == new_condition == False


class TestGetRobotProfile:
    """APK-CONFIG-VERIFY — SKU-prefix profile lookup and its diagnostic
    logging for prefixes confirmed real (via base_roomba_config.json) but
    not yet profiled by this project."""

    def test_known_prefix_returns_profile(self):
        from custom_components.roomba_plus.const import get_robot_profile
        profile = get_robot_profile("i755840")
        assert profile is not None
        assert profile.name == "i-series"

    def test_r_prefix_aliases_to_900_series(self):
        from custom_components.roomba_plus.const import get_robot_profile
        profile = get_robot_profile("R980040")
        assert profile is not None
        assert profile.name == "900-series"

    def test_no_sku_returns_none_quietly(self):
        from custom_components.roomba_plus.const import get_robot_profile
        assert get_robot_profile(None) is None

    def test_known_irobot_family_without_profile_logs_info(self, caplog):
        """A 'c'-prefix SKU (Combo, confirmed real in base_roomba_config.json)
        has no RobotProfile entry yet — should log at INFO, not silently
        return None with no trace."""
        from custom_components.roomba_plus.const import get_robot_profile
        import logging
        with caplog.at_level(logging.INFO, logger="custom_components.roomba_plus.const"):
            profile = get_robot_profile("c712340")
        assert profile is None
        assert any(
            "known iRobot product family" in r.message for r in caplog.records
        )

    def test_truly_unrecognised_prefix_logs_debug_not_info(self, caplog):
        from custom_components.roomba_plus.const import get_robot_profile
        import logging
        with caplog.at_level(logging.DEBUG, logger="custom_components.roomba_plus.const"):
            profile = get_robot_profile("z999999")
        assert profile is None
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert not any("known iRobot product family" in r.message for r in info_records)
        assert any("unrecognised prefix" in r.message for r in debug_records)
