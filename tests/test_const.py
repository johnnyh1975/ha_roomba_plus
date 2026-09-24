"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import datetime

from homeassistant.util import dt as dt_util
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
import ast
import pathlib
from unittest.mock import MagicMock
from custom_components.roomba_plus.const import ROOM_EVENT_DONE_STATUSES
from custom_components.roomba_plus.const import room_event_was_cleaned
import importlib
from pathlib import Path
from homeassistant.const import Platform
from custom_components.roomba_plus.const import PRIME_PLATFORMS
from custom_components.roomba_plus.models import ConnectionType
import json


__make_record_seq = 0


def _iso(days_ago: float = 0, hour: int = 10) -> str:
    """Local, not UTC. Anything comparing a record's DATE against
    `dt_util.now().date()` reads a local date, so a record built in UTC
    lands on the wrong day whenever the two disagree -- 00:00 to 08:00
    UTC under HA's pinned US/Pacific test timezone, which includes the
    00:00 nightly CI run. See test_mission_store._iso()."""
    dt = dt_util.now() - datetime.timedelta(days=days_ago)
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


class TestVendorTextTakesPrecedence:
    """Of 126 labels written here, exactly two matched iRobot's.

    Ours said "Left wheel off floor"; iRobot says "@val moved or on an
    uneven surface". The first names a sensor reading, the second
    describes what happened -- and it is what the user sees in the app
    beside ours.
    """

    def test_a_code_the_vendor_documents_uses_their_wording(self):
        from custom_components.roomba_plus.const import (
            ERROR_CATALOGUE,
            get_localized_error_entry,
        )

        entry = get_localized_error_entry(1, "en")

        assert entry["label"] != ERROR_CATALOGUE[1]["label"]
        assert "uneven surface" in entry["label"]

    def test_their_explanation_replaces_ours(self):
        from custom_components.roomba_plus.const import get_localized_error_entry

        entry = get_localized_error_entry(46, "en")

        assert "charge" in entry["description"].lower()

    def test_the_placeholder_is_left_alone(self):
        """`@val` is the robot's name in iRobot's own strings. A caller
        that knows it substitutes; one that does not gets a sentence with
        a placeholder rather than a mangled one."""
        from custom_components.roomba_plus.const import get_localized_error_entry

        assert "@val" in get_localized_error_entry(1, "en")["label"]

    def test_a_code_only_we_know_still_answers(self):
        """Ours covers 75 codes iRobot does not document. The vendor
        text wins where both have an entry; ours answers where the
        vendor is silent."""
        from custom_components.roomba_plus.const import (
            ERROR_CATALOGUE,
            get_localized_error_entry,
        )

        ours_only = next(
            c for c in ERROR_CATALOGUE
            if __import__(
                "custom_components.roomba_plus.vendor_errors",
                fromlist=["vendor_error"],
            ).vendor_error(c) is None and c != 0
        )
        entry = get_localized_error_entry(ours_only, "en")

        assert entry["label"] == ERROR_CATALOGUE[ours_only]["label"]

    def test_our_action_field_survives(self):
        """iRobot has no equivalent, so replacing their two fields must
        not drop our third."""
        from custom_components.roomba_plus.const import get_localized_error_entry

        assert "action" in get_localized_error_entry(1, "en")


#: A code iRobot's own catalogue has no text for, so these tests keep
#: exercising OUR translation chain rather than the vendor override that
#: now sits in front of it.
_OURS_ONLY = 3
_DE_LABEL = "Rechtes Rad hebt ab"


class TestGetLocalizedErrorEntry:
    """v3.4.1 — get_localized_error_entry() and the ERROR_CATALOGUE_TRANSLATIONS
    parallel structure in error_translations.py."""

    def test_none_language_returns_english_base(self):
        entry = get_localized_error_entry(_OURS_ONLY, None)
        assert entry == ERROR_CATALOGUE[_OURS_ONLY]

    def test_en_language_returns_english_base(self):
        entry = get_localized_error_entry(_OURS_ONLY, "en")
        assert entry == ERROR_CATALOGUE[_OURS_ONLY]

    def test_de_returns_translated_text_not_english(self):
        entry = get_localized_error_entry(_OURS_ONLY, "de")
        assert entry["label"] != ERROR_CATALOGUE[_OURS_ONLY]["label"]
        assert entry["label"] == _DE_LABEL

    def test_unsupported_language_falls_back_to_english(self):
        """A language never covered by ERROR_CATALOGUE_TRANSLATIONS (e.g.
        Japanese) must silently degrade to English, not raise or return
        blanks."""
        entry = get_localized_error_entry(_OURS_ONLY, "ja")
        assert entry == ERROR_CATALOGUE[_OURS_ONLY]

    def test_unknown_error_code_returns_empty_dict_like_base(self):
        assert get_localized_error_entry(99999, None) == {}
        assert get_localized_error_entry(99999, "de") == {}

    def test_partial_translation_falls_back_field_by_field(self, monkeypatch):
        """A code with only a partial translation (e.g. label translated,
        description/action missing) must never produce a blank string for
        the missing fields — each field falls back to English independently."""
        import custom_components.roomba_plus.const as const_module
        partial = {"de": {_OURS_ONLY: {"label": "Nur Label übersetzt"}}}
        monkeypatch.setattr(
            "custom_components.roomba_plus.error_translations.ERROR_CATALOGUE_TRANSLATIONS",
            partial,
        )
        entry = get_localized_error_entry(_OURS_ONLY, "de")
        assert entry["label"] == "Nur Label übersetzt"
        assert entry["description"] == ERROR_CATALOGUE[_OURS_ONLY]["description"]
        assert entry["action"] == ERROR_CATALOGUE[_OURS_ONLY]["action"]

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

    def test_v4_prime_sku_prefix_known_but_unprofiled(self, caplog):
        """'G185020' — the real, confirmed SKU for both live V4/Prime test
        accounts (chairstacker, jadestar1864 — Roomba 405 Combo) — is a
        known family via the 'g' prefix (added alongside V4 onboarding
        prep), same 'known but no RobotProfile yet' treatment as 'c'
        above. No RobotProfile entry exists for it — no real battery/
        maintenance field data has been collected for this SKU, only
        login/state confirmation via roombapy-prime."""
        from custom_components.roomba_plus.const import get_robot_profile
        import logging
        with caplog.at_level(logging.INFO, logger="custom_components.roomba_plus.const"):
            profile = get_robot_profile("G185020")
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


class TestThePlaceholdersAreRepairedOnTheWayOut:
    """iRobot's own strings use more than one placeholder form, and two
    of them are broken:

        `@val`          667 times -- the normal one
        `%robotName`    once, in English code 251
        `@valUpewnij`   the placeholder run together with the next word,
                        in Spanish and Polish -- a lost space

    A user seeing `%robotName` or `@valUpewnij` reads a bug. It is
    iRobot's, but it is ours to display.
    """

    def test_the_odd_english_placeholder_is_unified(self):
        from custom_components.roomba_plus.vendor_errors import vendor_error

        content = vendor_error(251, "en")["content"]

        assert "%robotName" not in content
        assert content.startswith("@val")

    def test_no_locale_ships_a_glued_placeholder(self):
        """One token to substitute, not five variants of it."""
        import re

        from custom_components.roomba_plus.vendor_errors import (
            VENDOR_ERROR_TEXTS,
            vendor_error,
        )

        glued = []
        for code in VENDOR_ERROR_TEXTS:
            for locale in ("en", "de", "es", "fr", "it", "nl", "pl", "pt"):
                entry = vendor_error(code, locale)
                if entry is None:
                    continue
                # CHECKED SEPARATELY, not concatenated. A title ending
                # in `@val` beside a content starting with a capital
                # letter reads as glued when the two are joined -- which
                # is a seam in the test, not a fault in the data. The
                # first version of this made exactly that mistake.
                for text in (entry["title"], entry["content"]):
                    glued += re.findall(r"@val\w+", text)

        assert not glued, f"placeholder run into the next word: {set(glued)}"

    def test_the_stored_catalogue_is_left_faithful(self):
        """The repair happens on the way out, so the data stays a
        faithful copy of what iRobot ships -- and regenerating it does
        not have to reproduce our fixes."""
        from custom_components.roomba_plus.vendor_errors import VENDOR_ERROR_TEXTS

        assert "%robotName" in VENDOR_ERROR_TEXTS[251]["en"]["content"]

    def test_an_ordinary_message_is_untouched(self):
        from custom_components.roomba_plus.vendor_errors import vendor_error

        assert vendor_error(46, "de")["title"] == "Akkustand zu niedrig für die Reinigung"


class TestEveryInitiatorHasALabel:
    """An unmapped initiator falls through to "None" — the same answer
    as "no initiator information at all". Two different real situations,
    one indistinguishable display.

    That is how `demand` was found: one value at a time, by someone
    building a blueprint and noticing his demand-triggered mission
    looked like it had no initiator.

    The vendor's `Initiator` enum lists 25. This table had six.
    """

    def test_the_table_covers_the_vendor_enum(self):
        from roombapy_prime.models.mission_history import Initiator

        from custom_components.roomba_plus.const import JOB_INITIATOR_LABELS

        missing = {
            m.value for m in Initiator
        } - set(JOB_INITIATOR_LABELS)

        assert not missing, f"initiators with no label: {sorted(missing)}"

    def test_the_two_button_sources_are_distinguishable(self):
        """`dockBtn` is the button on the dock, `manual` the one on the
        robot. Both are "somebody pressed something" and they are not
        the same somebody."""
        from custom_components.roomba_plus.const import JOB_INITIATOR_LABELS

        assert JOB_INITIATOR_LABELS["dockBtn"] != JOB_INITIATOR_LABELS["manual"]

    def test_the_project_specific_value_survived(self):
        """`demand` is written by this integration's own dirt-threshold
        path and appears in no vendor enum. A refresh from the enum must
        not drop it."""
        from custom_components.roomba_plus.const import JOB_INITIATOR_LABELS

        assert JOB_INITIATOR_LABELS["demand"] == "Demand clean"


class TestThePhaseSetsAgreeAcrossModules:
    """`{"run", "hmMidMsn", "evac"}` is written out as a literal in
    three places: `const.CLEANING_PHASES`, `callbacks`
    and `presence_manager`.

    Only const.py carries the reasoning — including that `evac` is in
    the set DELIBERATELY, against the vendor's own rule, because an i7+
    goes through evac mid-mission and treating it as an ending reset the
    map renderer early.

    So a later reader who finds the vendor rule and "fixes" const.py
    leaves two copies behind, and the ones without the comment are the
    ones that keep the old behaviour. This makes that loud.
    """

    def test_callbacks_matches_const(self):
        from custom_components.roomba_plus.callbacks import (
            _ACTIVE_CLEANING_PHASES,
        )
        from custom_components.roomba_plus.const import CLEANING_PHASES

        assert _ACTIVE_CLEANING_PHASES == CLEANING_PHASES

    def test_presence_manager_matches_const(self):
        from custom_components.roomba_plus.const import CLEANING_PHASES
        from custom_components.roomba_plus.presence_manager import (
            _ACTIVE_CLEANING_PHASES,
        )

        assert _ACTIVE_CLEANING_PHASES == CLEANING_PHASES

    def test_the_wider_end_set_is_a_superset(self):
        """`callbacks._MISSION_END_PHASES` adds `completed` and
        `cancelled` on purpose — valid end states in some firmware
        variants. Deliberately wider is fine; diverging is not."""
        from custom_components.roomba_plus.callbacks import (
            _MISSION_END_PHASES,
        )
        from custom_components.roomba_plus.const import MISSION_END_PHASES

        assert _MISSION_END_PHASES >= MISSION_END_PHASES


class TestThePhaseSetHasOneSource:
    """`{"run", "hmMidMsn", "evac"}` existed as a literal in three
    files: const.py, callbacks.py and presence_manager.py.

    They agreed, which is why nothing caught it. The risk is what
    happens next: only const.py carries the reasoning — including why
    `evac` deliberately differs from the vendor's own rule table, an
    i7+ evacuating mid-mission and a map renderer that reset early when
    that was read as an ending.

    A copy without the reasoning is the one somebody "corrects" later,
    and then presence logic and mission logic disagree about whether the
    robot is cleaning.
    """

    def test_no_module_repeats_the_literal(self):
        import pathlib
        import re

        base = pathlib.Path("custom_components/roomba_plus")
        pattern = re.compile(
            r'frozenset\(\{\s*"run",\s*"hmMidMsn",\s*"evac"\s*\}\)'
        )
        offenders = [
            path.name for path in base.glob("*.py")
            if path.name != "const.py" and pattern.search(path.read_text())
        ]

        assert not offenders, (
            f"{offenders} repeat the cleaning-phase literal instead of "
            "importing CLEANING_PHASES -- and would not carry the reason "
            "`evac` is in it"
        )

    def test_the_importers_get_the_same_set(self):
        from custom_components.roomba_plus.callbacks import (
            _ACTIVE_CLEANING_PHASES as from_callbacks,
        )
        from custom_components.roomba_plus.const import CLEANING_PHASES
        from custom_components.roomba_plus.presence_manager import (
            _ACTIVE_CLEANING_PHASES as from_presence,
        )

        assert from_callbacks is CLEANING_PHASES
        assert from_presence is CLEANING_PHASES

    def test_the_mission_end_set_is_deliberately_wider(self):
        """callbacks.py's end set is NOT the same as const.py's — it
        adds `completed` and `cancelled`, which are valid end states on
        some firmware. That difference is documented and must survive
        this consolidation."""
        from custom_components.roomba_plus.callbacks import _MISSION_END_PHASES
        from custom_components.roomba_plus.const import MISSION_END_PHASES

        assert MISSION_END_PHASES < _MISSION_END_PHASES
        assert {"completed", "cancelled"} <= _MISSION_END_PHASES


# ── formerly tests/test_room_event_rules.py ─────────────────────────────────────
#
# Room events answer two questions, and they must not be confused.
#
# "Was this room CLEANED?" — room history — counts a finished pass or any
# pass that cleaned floor. "Is this room FINISHED?" — the end gate, stuck
# recovery, learned figures, coverage, the archive's classification — counts
# only a finished pass (status 0 or 6).
#
# They shared one constant until 4.2.11. The iRobot app does not evaluate
# `status` at all (APK 7.18.0), so its history is looser than {0, 6}; that
# cost @Thonno his Kitchen. But loosening the constant everywhere would let
# a room still in progress close a mission after its first room — the
# v2.9.0 fault — and let half a pass shrink learned room sizes.

def _room(rid, status, pass_area=None):
    room = {"rid": rid, "status": status, "passCount": 1, "area": 100}
    if pass_area is not None:
        room["passArea"] = pass_area
    return {"type": "room", "room": room}


_PKG = pathlib.Path("custom_components/roomba_plus")


#: Every function that reads a room event's status, and which question it
#: asks. A new reader has to be placed here, which makes it a decision.
FINISHED = {
    ("callbacks.py", "_async_update_robot_profile_store"),  # learned area/time
    ("mission_store.py", "latest_room_coverage"),          # measurement
    ("mission_archive.py", "_parse_derived"),               # completed/interrupted split
    ("mission_archive.py", "_parse_timeline"),              # done/enter/interrupted split
}


#: The end gate asks "is the mission over?" -- a room event closed at
#: mission end (5, 6) proves that as well as a finished room (0).
CLOSED_AT_END = {
    ("callbacks.py", "_cloud_confirms_all_rooms_done"),
}


#: Stuck recovery asks "did the robot work on after being stuck?" -- a pass
#: finished after the stuck (0, 1); a 5 or 6 closed at mission end does not.
PASS_DONE = {
    ("callbacks.py", "_mission_recovered_after_stuck"),
}


CLEANED = {
    ("mission_store.py", "_record_room_names"),             # room history
    ("mission_store.py", "record_region_ids"),              # per-region last cleaned
    ("diagnostics.py", "_last_mission_room_events"),        # shows both
}


def _users() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for f in _PKG.glob("*.py"):
        if f.name == "const.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            namen = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            genutzt = namen & {"ROOM_EVENT_DONE_STATUSES", "room_event_was_cleaned",
                               "ROOM_EVENT_CLOSED_AT_END_STATUSES", "ROOM_EVENT_PASS_DONE_STATUSES"}
            if genutzt:
                # innermost function only
                key = (f.name, fn.name)
                found.setdefault(key, set()).update(genutzt)
    # drop outer functions that merely contain a classified inner one
    return found


class TestTheCleanedRule:

    @pytest.mark.parametrize("status", [0, 1])
    def test_a_completed_pass_counts_even_without_area(self, status):
        """0 = last pass done, 1 = a pass done with more to follow (firmware:
        CleanStatus 3, `get_remaining_sub_clean_zone_passes`). Either way
        the robot drove and cleaned the room."""
        assert room_event_was_cleaned({"rid": "1", "status": status}) is True

    @pytest.mark.parametrize("status", [3, 4, 5, 6])
    def test_any_other_status_counts_with_cleaned_floor(self, status):
        """@Thonno's Kitchen: the app showed it cleaned. 5 and 6 are rooms
        still open when the mission ended, 4 a robot picked up — cleaned as
        far as they got."""
        assert room_event_was_cleaned({"rid": "1", "status": status, "passArea": 58}) is True

    @pytest.mark.parametrize("status", [3, 4, 5, 6])
    def test_any_other_status_without_cleaned_floor_does_not(self, status):
        """No area means the firmware had no coverage data for the room —
        it was never driven. What keeps a skipped room out, and a room
        force-closed at mission end without having been reached.

        6 is here although it stays in ROOM_EVENT_DONE_STATUSES: the end
        gate asks whether the ROOM IS DONE, history asks whether FLOOR WAS
        CLEANED. A 6 without area can be the first and not the second."""
        assert room_event_was_cleaned({"rid": "1", "status": status}) is False
        assert room_event_was_cleaned({"rid": "1", "status": status, "passArea": 0}) is False

    def test_an_event_without_status_counts_by_its_area(self):
        """`status` is optional in the firmware schema."""
        assert room_event_was_cleaned({"rid": "1", "passArea": 12}) is True
        assert room_event_was_cleaned({"rid": "1"}) is False

    def test_nonsense_is_not_a_clean(self):
        assert room_event_was_cleaned(None) is False
        assert room_event_was_cleaned({"rid": "1", "passArea": True}) is False
        assert room_event_was_cleaned({"rid": "1", "passArea": "58"}) is False


class TestThonnosKitchen:
    """21 Sep 2026: regions 1, 20, 12, 19; the app showed four rooms
    cleaned, Roomba+ recorded three. Region 1 — the Kitchen — is modelled
    on a pass that cleaned floor but did not finish, the shape real
    Classic data shows (PyRoomba nMssn 96: status 1, done_raw ok)."""

    def test_the_kitchen_is_in_the_room_history(self):
        from custom_components.roomba_plus.mission_store import MissionStore

        store = MissionStore()
        store._records.append({
            "id": "m_1789975662",
            "started_at": "2026-09-21T07:27:42+00:00",
            "ended_at": "2026-09-21T10:02:01+00:00",
            "timeline": {"finEvents": [
                _room("1", 1, 58), _room("20", 0, 90),
                _room("12", 0, 70), _room("19", 0, 40),
            ]},
        })
        names = {"1": "Cucina", "20": "Soggiorno", "12": "Camera da letto",
                 "19": "Cabina Armadio"}

        history = store.room_cleaning_history(names)

        assert history.get("Cucina") == "2026-09-21T10:02:01+00:00"
        assert set(history) == set(names.values())


class TestTheEndGateStillRequiresAFinishedPass:
    """The safety half of the split. If the end gate took the history
    rule, a room still in progress with some floor cleaned would count as
    finished, and a mission could close after its first room."""

    def test_a_cleaned_but_unfinished_room_does_not_confirm_the_mission(self):
        from custom_components.roomba_plus.callbacks import (
            _cloud_confirms_all_rooms_done,
            _MissionState,
        )

        ms = _MissionState()
        ms.mission_start_ts = 1789975662
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{
            "startTime": 1789975662,
            "timeline": {"finEvents": [_room("1", 1, 58), _room("20", 0, 90)]},
        }]

        assert _cloud_confirms_all_rooms_done(ms, entry, ["1", "20"]) is False

    def test_once_it_finishes_the_mission_is_confirmed(self):
        from custom_components.roomba_plus.callbacks import (
            _cloud_confirms_all_rooms_done,
            _MissionState,
        )

        ms = _MissionState()
        ms.mission_start_ts = 1789975662
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{
            "startTime": 1789975662,
            "timeline": {"finEvents": [_room("1", 0, 58), _room("20", 0, 90)]},
        }]

        assert _cloud_confirms_all_rooms_done(ms, entry, ["1", "20"]) is True

    @pytest.mark.parametrize("closing", [5, 6])
    def test_a_last_room_closed_at_mission_end_confirms_it(self, closing):
        """5 and 6 are closed by the firmware in handle_mission_end, which
        runs only once the mission is over -- proof enough for the end gate,
        which asks exactly that. 5 used to be left out, so such a mission
        waited out the full cap instead."""
        from custom_components.roomba_plus.callbacks import (
            _cloud_confirms_all_rooms_done,
            _MissionState,
        )

        ms = _MissionState()
        ms.mission_start_ts = 1789975662
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.raw_records = [{
            "startTime": 1789975662,
            "timeline": {"finEvents": [_room("1", 0, 58), _room("20", closing, 40)]},
        }]

        assert _cloud_confirms_all_rooms_done(ms, entry, ["1", "20"]) is True


class TestEveryReaderIsDeliberatelyPlaced:

    def test_finished_sites_use_the_finished_rule_only(self):
        users = _users()
        for key in FINISHED:
            assert users.get(key) == {"ROOM_EVENT_DONE_STATUSES"}, (
                f"{key} must count only a finished pass; it uses {users.get(key)}"
            )

    def test_the_end_gate_uses_the_closed_at_end_rule_only(self):
        users = _users()
        for key in CLOSED_AT_END:
            assert users.get(key) == {"ROOM_EVENT_CLOSED_AT_END_STATUSES"}, users.get(key)

    def test_stuck_recovery_uses_the_pass_done_rule_only(self):
        users = _users()
        for key in PASS_DONE:
            assert users.get(key) == {"ROOM_EVENT_PASS_DONE_STATUSES"}, users.get(key)

    def test_cleaned_sites_use_the_cleaned_rule(self):
        users = _users()
        for key in CLEANED:
            assert "room_event_was_cleaned" in users.get(key, set()), (
                f"{key} is room history and must use room_event_was_cleaned"
            )

    def test_no_reader_is_unclassified(self):
        """A new place reading room status must choose a question."""
        users = _users()
        klassifiziert = FINISHED | CLEANED | CLOSED_AT_END | PASS_DONE
        # Enclosing functions that only contain a classified inner function
        # (make_mission_callback around the end-gate helpers) are not readers.
        offen = sorted(k for k in users if k not in klassifiziert
                       and not k[1].startswith("make_"))
        assert not offen, f"unclassified readers of room status: {offen}"


class TestTheDirtIndexDividesByRoomSize:
    """`update_room_dirt_index` divides passes by the ROOM's area. Both the
    live path and the archive seeding used `totalArea or area`. Since the
    firmware writes `totalArea` only from the second pass on, that gave the
    room size for a one-pass room and the covered union for a two-pass
    one — the same room's density jumped with its pass count.

    The registry's own example: area 72, two passes, totalArea 42. The
    divisor must be 72.
    """

    TWO_PASSES = {"rid": "19", "status": 0, "passCount": 2,
                  "area": 72, "passArea": 40, "totalArea": 42}

    def test_the_live_path_uses_the_room_size(self):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.callbacks import (
            _async_update_robot_profile_store,
        )
        from custom_components.roomba_plus.const import SQFT_TO_M2
        from tests.test_callbacks import _make_callback_env

        hass, entry, _rec, mission_store = _make_callback_env()
        rps = MagicMock()
        rps.update_mission_stats.return_value = False
        rps.finalize_correlation.return_value = False
        rps.async_save = AsyncMock()
        mission_store.query.return_value = [{
            "id": "m_1", "started_at": "2026-09-21T07:00:00+00:00",
            "timeline": {"finEvents": [{"type": "room", "room": dict(self.TWO_PASSES)}]},
        }]

        hass.loop.run_until_complete(
            _async_update_robot_profile_store(hass, entry, mission_store, rps)
        )

        rid, passes, area_m2 = rps.update_room_dirt_index.call_args.args
        assert (rid, passes) == ("19", 2)
        assert area_m2 == pytest.approx(72 * SQFT_TO_M2)

    def test_the_archive_seeds_with_the_room_size(self):
        from custom_components.roomba_plus.mission_archive import MissionArchive

        arc = MissionArchive.__new__(MissionArchive)
        derived = arc._parse_derived({
            "nMssn": 1, "done": "ok",
            "timeline": {"finEvents": [{"type": "room", "room": dict(self.TWO_PASSES)}]},
        })

        assert derived["rooms_completed"]["19"]["area"] == pytest.approx(72.0)


# ── formerly tests/test_prime_platform_coverage.py ────────────────────
#
# Comprehensive PRIME_PLATFORMS coverage test.
#
# REAL BUG THIS IS BUILT TO PREVENT: PrimeBinPresentSensor/
# PrimeTankPresentSensor/PrimeRobotConnectivitySensor (binary_sensor.py)
# and PrimeCarpetBoostSwitch (switch.py) were built and unit-tested in
# isolation, but PRIME_PLATFORMS (const.py) was never updated to include
# their platforms — meaning HA never actually called either module's
# async_setup_entry() for a real CLOUD_ONLY config entry, so none of
# these entities were ever created for a real user despite fully working,
# individually-tested code existing for them. Caught only by manually
# reviewing a real field tester's own screenshots (which never showed
# these entities), not by any test.
#
# TWO DIRECTIONS, BOTH NEEDED (an earlier version of this file only had
# the first one, which turned out to have the SAME structural flaw as
# the original bug: a second, separately-maintained list that could
# drift out of sync just as easily as PRIME_PLATFORMS itself did):
#
# 1. FORWARD: for every platform actually listed in PRIME_PLATFORMS,
#    calling that module's own async_setup_entry() against a realistic
#    CLOUD_ONLY config_entry must produce at least one entity. Iterates
#    PRIME_PLATFORMS directly -- no separate list to drift.
#
# 2. BACKWARD (the direction the original bug needed): scans every
#    platform .py file for a literal "ConnectionType.CLOUD_ONLY"
#    reference -- any file that has one is a module claiming to support
#    CLOUD_ONLY, and must have a corresponding entry in PRIME_PLATFORMS.
#    THIS is what would have caught the original bug: binary_sensor.py
#    and switch.py both had real CLOUD_ONLY branches, fully coded and
#    unit-tested, while PRIME_PLATFORMS simply never listed them.

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "roomba_plus"


# Maps a Platform enum value to its own platform module's filename --
# NOT a duplicate risk-of-drift list like the removed
# _EXPECTED_MINIMUM_ENTITIES was: this is a STATIC, structural fact
# about Home Assistant's own platform-name convention (Platform.SENSOR
# always means sensor.py, for every HA integration, not just this
# one) -- it cannot itself silently drift the way a maintained "which
# platforms does OUR integration support" list can.
_PLATFORM_TO_MODULE: dict[Platform, str] = {
    Platform.VACUUM: "vacuum",
    Platform.SENSOR: "sensor",
    Platform.BINARY_SENSOR: "binary_sensor",
    Platform.SWITCH: "switch",
    Platform.CALENDAR: "calendar",
    Platform.TODO: "todo",
    Platform.IMAGE: "image",
    Platform.BUTTON: "button",
    Platform.SELECT: "select",
    Platform.DEVICE_TRACKER: "device_tracker",
}


def _make_cloud_only_config_entry() -> MagicMock:
    """A single, realistic CLOUD_ONLY config_entry, explicit about
    every attribute each platform's own CLOUD_ONLY branch actually
    reads (deliberately NOT relying on MagicMock's own
    auto-attribute-generation for anything a real async_setup_entry
    branches on, e.g. prime_status_coordinator -- an accidentally
    "truthy" auto-generated MagicMock there would mask whether that
    guard is real)."""
    config_entry = MagicMock()
    # The maintenance list is opt-in (CONF_ENABLE_MAINTENANCE_LIST,
    # default off), so a realistic entry for THIS check is one where the
    # user asked for it -- otherwise the class reads as unreachable when
    # it is merely unrequested. The guard's own message says to add the
    # data here rather than exempt the class, and it is right.
    config_entry.options = {"enable_maintenance_list": True}
    data = config_entry.runtime_data
    data.connection_type = ConnectionType.CLOUD_ONLY
    data.blid = "TESTBLID"
    data.roomba = None
    data.prime_robot = MagicMock()
    data.prime_coordinator = MagicMock()
    data.prime_status_coordinator = MagicMock()
    data.prime_status_coordinator.data = {}
    data.prime_household_id = "hh1"
    return config_entry


def _platform_files_referencing_cloud_only() -> set[str]:
    """Every platform .py file (direct children of the integration
    directory, matching a name in _PLATFORM_TO_MODULE's own values)
    that contains a literal "ConnectionType.CLOUD_ONLY" reference --
    a module claiming CLOUD_ONLY support, regardless of whether
    PRIME_PLATFORMS agrees."""
    referencing: set[str] = set()
    for module_name in _PLATFORM_TO_MODULE.values():
        path = INTEGRATION_DIR / f"{module_name}.py"
        if path.exists() and "ConnectionType.CLOUD_ONLY" in path.read_text():
            referencing.add(module_name)
    return referencing


class TestForwardEveryListedPlatformCreatesEntities:
    """Direction 1: PRIME_PLATFORMS -> does it actually work."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", PRIME_PLATFORMS)
    async def test_platform_creates_at_least_one_entity(self, platform: Platform) -> None:
        module_name = _PLATFORM_TO_MODULE[platform]
        module = importlib.import_module(f"custom_components.roomba_plus.{module_name}")
        config_entry = _make_cloud_only_config_entry()
        created: list = []

        await module.async_setup_entry(MagicMock(), config_entry, created.extend)

        assert created, (
            f"Platform.{platform.name} ({module_name}.py) is listed in PRIME_PLATFORMS "
            f"but created ZERO entities for a realistic CLOUD_ONLY config entry. Either "
            f"its own CLOUD_ONLY branch is broken, or it doesn't have one at all."
        )


class TestBackwardEveryCloudOnlyModuleIsListed:
    """Direction 2: does the code -> is it actually reachable at all.

    This is the direction that would have caught the REAL bug:
    binary_sensor.py/switch.py both had real, working, unit-tested
    CLOUD_ONLY branches while PRIME_PLATFORMS simply never listed
    them, so HA never called either module's async_setup_entry() for
    a real CLOUD_ONLY entry at all."""

    def test_every_module_referencing_cloud_only_is_in_prime_platforms(self) -> None:
        """CARVED-OUT EXCEPTIONS: calendar.py and todo_prime.py are
        deliberately NOT in the static PRIME_PLATFORMS list anymore --
        Platform.CALENDAR is now conditional on
        CONF_ENABLE_SCHEDULE_CALENDAR (default True), added at runtime
        by __init__.py's _optional_platforms(), called
        identically at all four platform-list build sites (Classic
        setup/unload, Prime setup/unload). This is a deliberate,
        single, well-tested exception to the invariant this test
        otherwise enforces -- not a reopening of the original bug this
        test exists to catch (every OTHER CLOUD_ONLY module must still
        be unconditionally listed)."""
        referencing = _platform_files_referencing_cloud_only()
        listed_modules = {_PLATFORM_TO_MODULE[p] for p in PRIME_PLATFORMS}
        # `todo` joins calendar for the same reason and a stronger one:
        # a maintenance list takes a place in Home Assistant's sidebar,
        # and @chairstacker found one there he had not asked for. It is
        # gated on CONF_ENABLE_MAINTENANCE_LIST and, unlike the calendar,
        # defaults to OFF.
        deliberately_conditional = {"calendar", "todo"}
        missing = referencing - listed_modules - deliberately_conditional
        assert not missing, (
            f"{missing} reference ConnectionType.CLOUD_ONLY but are NOT in "
            f"PRIME_PLATFORMS -- this is exactly the shape of the original bug "
            f"(binary_sensor.py/switch.py had real CLOUD_ONLY code that was never "
            f"actually reachable). Add the corresponding Platform to PRIME_PLATFORMS."
        )


class TestEveryPlatformWorksOnDataTheLibraryActuallyReturns:
    """The MagicMock run above answers "is this platform wired at all".
    It cannot answer "does it work", and the difference cost a feature.

    `getattr(m, "anything")` on a MagicMock returns a truthy MagicMock.
    So code reading `.schedule_id` off what is really a dict passes here
    and produces nothing in the field. PrimeScheduleSwitch did exactly
    that for its entire life: green in this file, zero entities for
    every real user, found by manual review rather than by any test.

    This run uses the library's own parsers (tests/prime_fixtures.py).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", PRIME_PLATFORMS)
    async def test_platform_creates_entities_from_real_library_data(
        self, platform: Platform
    ) -> None:
        from tests import prime_fixtures

        module = importlib.import_module(
            f"custom_components.roomba_plus.{_PLATFORM_TO_MODULE[platform]}"
        )
        created: list = []

        await module.async_setup_entry(
            MagicMock(), prime_fixtures.cloud_only_config_entry(), created.extend
        )

        assert created, (
            f"Platform.{platform.name} produces entities for a MagicMock but NONE "
            f"for data the library actually returns. Something in its setup path "
            f"is reading attributes off a value that is a plain dict at runtime."
        )


class TestNoPrimeEntityClassIsNeverBuilt:
    """The third direction, and the one the two above cannot see.

    Both existing checks compare what a run produces. A class that is
    never instantiated in ANY run is absent from every comparison --
    which is the exact shape of the schedule-switch bug: nothing was
    missing from a list, nothing failed, a class simply never got built
    and no test was looking for it.

    So this asks the code what Prime entity classes exist, and the
    platforms what they actually build.
    """

    @pytest.mark.asyncio
    async def test_every_prime_entity_class_is_reachable(self) -> None:
        import ast

        from tests import prime_fixtures

        declared: dict[str, str] = {}
        for path in sorted(INTEGRATION_DIR.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(node, ast.ClassDef)
                        and node.name.startswith("Prime")):
                    continue
                bases = [
                    base.id if isinstance(base, ast.Name)
                    else getattr(base, "attr", "")
                    for base in node.bases
                ]
                if any(b.endswith(("Entity", "Sensor", "Switch", "Button",
                                   "Select", "Image", "Tracker", "Vacuum",
                                   "Calendar"))
                       for b in bases):
                    declared[node.name] = path.name

        built: set[str] = set()
        # The two conditional platforms are added by hand, exactly as
        # __init__.py adds them at runtime when their option is on. A
        # platform being opt-in must not make its entities look dead.
        for platform in list(PRIME_PLATFORMS) + [
            Platform.CALENDAR, Platform.TODO
        ]:
            module = importlib.import_module(
                f"custom_components.roomba_plus.{_PLATFORM_TO_MODULE[platform]}"
            )
            created: list = []
            # OPTIONAL ENTITIES NEED THEIR OPTION ON. The fixture's
            # `options` is a MagicMock, so `.get()` used to return a
            # truthy mock and every option read as enabled -- which is
            # why this guard passed while the region sensors were gated
            # on a coordinator a Prime entry does not have.
            #
            # Explicit options make the guard mean what it says: these
            # classes are reachable on an entry that has asked for them.
            _entry = prime_fixtures.cloud_only_config_entry()
            _entry.options = {"region_sensors": True, "room_schedule": True}
            _entry.runtime_data.prime_room_names = {"10": "Kitchen"}
            await module.async_setup_entry(
                MagicMock(), _entry, created.extend
            )
            built |= {type(entity).__name__ for entity in created}

        never_built = sorted(set(declared) - built)

        assert not never_built, (
            "These Prime entity classes exist but no platform builds them for a "
            "realistic CLOUD_ONLY entry: "
            + ", ".join(f"{name} ({declared[name]})" for name in never_built)
            + ". Either the class is dead code, or its setup path drops it "
            "silently -- which is how PrimeScheduleSwitch shipped without ever "
            "creating a single entity. If a class is genuinely gated on data "
            "this fixture does not carry, add that data to tests/prime_fixtures.py "
            "rather than removing the class from this check."
        )


class TestEveryPrimeEntityBelongsToTheRobotDevice:
    """A device-metadata fix that was applied to some entities and not
    others -- and nothing noticed for two releases.

    `IRobotEntity.__init__` takes an optional config_entry, and for a
    Prime entity it is the ONLY source of model, serial and the device
    name (there is no roombapy state to read them from). Its own
    docstring records this being fixed after an architecture review.
    Six Prime classes across four files never got the argument:
    PrimeFavoriteButton, PrimeLocateButton, PrimeDockButton,
    PrimeSettingSelect, PrimeSettingSwitch, PrimeRoomsImage. Their
    device pages carried model=None, serial=None, and a device name
    that depended on which entity happened to register first.

    A textual check on the call sites would drift with the next
    refactor. This asks the entities.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", list(PRIME_PLATFORMS) + [Platform.CALENDAR])
    async def test_entities_carry_complete_device_info(
        self, platform: Platform
    ) -> None:
        from custom_components.roomba_plus.const import DOMAIN
        from tests import prime_fixtures

        module = importlib.import_module(
            f"custom_components.roomba_plus.{_PLATFORM_TO_MODULE[platform]}"
        )
        created: list = []
        await module.async_setup_entry(
            MagicMock(), prime_fixtures.cloud_only_config_entry(), created.extend
        )

        incomplete = []
        for entity in created:
            info = getattr(entity, "device_info", None)
            if not info or not info.get("identifiers"):
                incomplete.append((type(entity).__name__, "no device_info"))
            elif info["identifiers"] != {(DOMAIN, f"roomba_plus_{entity._blid}")}:
                incomplete.append((type(entity).__name__, "wrong device"))
            elif info.get("model") is None:
                # For a Prime entity this means config_entry was not passed
                # to IRobotEntity.__init__ -- there is no other source.
                incomplete.append((type(entity).__name__, "model=None"))

        assert not incomplete, (
            f"Platform.{platform.name} built entities with incomplete device "
            f"info: {sorted(set(incomplete))}. For Prime entities model and "
            "serial come only from config_entry -- pass it to "
            "IRobotEntity.__init__."
        )


class TestRegionSensorsGrowWithTheMap:
    """@chairstacker adds and renames zones regularly -- he went from
    eight to twelve between two reports. A list frozen at setup leaves
    every new zone without an entity until he reloads the integration,
    which is the manual step this feature exists to remove.
    """

    @pytest.mark.asyncio
    async def test_a_zone_added_later_gets_its_sensor(self):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.sensor import async_setup_entry
        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        # `prime_room_names` ON THE ENTRY, not `regions_by_pmap` on a
        # Classic coordinator. A Prime entry has no `cloud_coordinator`
        # at all, so this fixture agreed with a gate that could never
        # open -- @chairstacker ticked the option, ran a zone mission,
        # reloaded, and got no entities (#84) while this test passed.
        # THE OPTION HAS TO BE ON. It defaults to False, and the old
        # gate never opened on Prime anyway -- so this test passed
        # without ever exercising the code it names.
        entry.options = {**getattr(entry, "options", {}), "region_sensors": True}
        entry.runtime_data.prime_room_names = {"10": "Kitchen"}
        coordinator = entry.runtime_data.prime_status_coordinator

        listeners: list = []
        coordinator.async_add_listener = lambda cb: listeners.append(cb) or (
            lambda: None
        )

        created: list = []
        with patch(
            # Patched where sensor.py BINDS it, not where it lives: the
            # import moved to module level, so patching the source no
            # longer affects the already-bound name.
            "custom_components.roomba_plus.sensor.async_dispatcher_connect",
            lambda _h, _sig, cb: listeners.append(cb) or (lambda: None),
        ):
            await async_setup_entry(MagicMock(), entry, created.extend)

        before = sum(
            1 for e in created if type(e).__name__ == "PrimeRegionLastCleanedSensor"
        )

        # A new zone appears, and the status coordinator fires.
        entry.runtime_data.prime_room_names["101"] = "Sofa corner"
        for callback in listeners:
            callback()

        after = sum(
            1 for e in created if type(e).__name__ == "PrimeRegionLastCleanedSensor"
        )

        assert after == before + 1, "a zone added after setup must get an entity"

    @pytest.mark.asyncio
    async def test_an_unchanged_map_adds_nothing(self):
        """The listener fires on every cloud refresh; it must not
        create duplicates of what already exists."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import async_setup_entry
        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        coordinator = entry.runtime_data.cloud_coordinator
        coordinator.regions_by_pmap = {"MAP-A": {"10": "Kitchen"}}
        listeners: list = []
        coordinator.async_add_listener = lambda cb: listeners.append(cb) or (
            lambda: None
        )

        created: list = []
        await async_setup_entry(MagicMock(), entry, created.extend)
        before = len(created)

        for callback in listeners:
            callback()

        assert len(created) == before


# ── formerly tests/test_vendor_platform_fixtures.py ───────────────────
#
# Capability gates checked against iRobot's OWN sample robot states.
#
# WHERE THIS DATA COMES FROM. The Prime app ships simulator responses in
# `res/raw` -- one complete `state.reported` per hardware platform, kept
# by the vendor for its own demo mode. Seven platforms, covering a Braava
# jet m6, an i7, a j9, an s9 and three others.
#
# WHY IT MATTERS. Every field structure this project relies on was
# reconstructed from tester captures, and testers own the robots they own:
# nobody in the group has an s9 or a j9 or an R111840. These fixtures cover
# capability combinations no capture could reach, and they come from the
# vendor rather than from us.
#
# WHAT THEY ARE NOT. A snapshot from app version 2.2.4, and simulator data
# rather than live robots -- so a disagreement between a fixture and a real
# capture is a question, not a verdict. Read them as "the vendor thinks
# this shape is representative", which is worth a great deal and is not the
# same as "this is what your robot sends".

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "vendor_platform_shadows.json"


def _platforms() -> dict:
    return json.loads(_FIXTURE.read_text())


class TestTheFixtureItself:
    def test_every_platform_carries_a_capability_block(self):
        for name, data in _platforms().items():
            assert data["cap"], name

    def test_the_platforms_are_meaningfully_different(self):
        """A fixture set where every entry is the same shape tests
        nothing. These differ in 27 of 32 capability keys."""
        platforms = _platforms()
        keys = {k for p in platforms.values() for k in p["cap"]}
        varying = [
            k for k in keys
            if len({str(p["cap"].get(k)) for p in platforms.values()}) > 1
        ]

        assert len(varying) > 20


class TestCapabilityValuesAreNotBooleans:
    """The failure this fixture set exists to catch.

    `dict.get(key, default)` does not protect against an explicit 0, and
    a capability flag read as a boolean turns a graduated value into
    on/off. Both mistakes have been made in this project, repeatedly.
    """

    def test_operating_mode_is_a_number_not_a_flag(self):
        """`oMode` reads 2 on three platforms and 6 on another. Anything
        treating it as truthy loses the distinction entirely."""
        values = {
            name: p["cap"]["oMode"]
            for name, p in _platforms().items() if "oMode" in p["cap"]
        }

        assert len(set(values.values())) > 1
        assert all(isinstance(v, int) for v in values.values())

    def test_an_explicit_zero_is_a_real_value(self):
        """`pp` reads 0 on every platform that reports it, and `edge`
        likewise. A gate that treats absent and zero alike would offer
        features no robot in this set has."""
        zeros = {
            name: [k for k, v in p["cap"].items() if v == 0]
            for name, p in _platforms().items()
        }

        assert any(zeros.values()), "no platform reports an explicit zero"

    def test_missing_is_not_the_same_as_zero(self):
        """`carpetBoost` is absent on five platforms and present on two.
        Absent means the robot did not say; zero means it said no."""
        platforms = _platforms()
        absent = [n for n, p in platforms.items() if "carpetBoost" not in p["cap"]]
        present = [n for n, p in platforms.items() if "carpetBoost" in p["cap"]]

        assert absent and present


class TestPadValuesHaveTwoSpellings:
    """Found by these fixtures, not by a tester.

    `san_marino` (Braava jet m6) reports `reusablewet` and `stingray`
    reports `reusableWet` -- the same value with different casing, from
    the vendor's own sample data. PAD_LABELS knows only the camelCase
    form, so a Braava would have shown "Unknown".

    No tester could have found this: it needs two robots that differ
    only in how the vendor spelled a string.
    """

    def test_both_spellings_appear_in_the_vendors_own_data(self):
        pads = {
            name: p["detectedPad"]
            for name, p in _platforms().items() if p.get("detectedPad")
        }

        assert "reusablewet" in pads.values()
        assert "reusableWet" in pads.values()

    def test_every_reported_pad_value_resolves_to_a_label(self):
        from custom_components.roomba_plus.const import PAD_LABELS

        for name, p in _platforms().items():
            value = p.get("detectedPad")
            if value is None:
                continue
            assert PAD_LABELS.get(value) is not None, f"{name}: {value!r}"


class TestBraavaDetectionAgainstRealSkus:
    """`is_braava()` decides on the SKU prefix. These are the vendor's
    own SKUs, which is a better test than any invented string."""

    def test_the_braava_is_recognised(self):
        from custom_components.roomba_plus.const import is_braava

        sku = _platforms()["san_marino"]["sku"]
        assert sku.lower().startswith("m")
        assert is_braava({"sku": sku, "detectedPad": "reusablewet"}) is True

    def test_the_vacuums_are_not(self):
        from custom_components.roomba_plus.const import is_braava

        for name in ("lewis", "ruby", "soho", "sapphire"):
            sku = _platforms()[name]["sku"]
            assert is_braava({"sku": sku}) is False, f"{name} ({sku})"

    def test_a_mopping_vacuum_is_not_a_braava(self):
        """`stingray` reports a wet pad and is not an m-series SKU --
        the case the is_mop/is_braava split exists for."""
        from custom_components.roomba_plus.const import is_braava, is_mop

        data = _platforms()["stingray"]
        state = {"sku": data["sku"], "detectedPad": data["detectedPad"]}

        assert is_mop(state) is True
        assert is_braava(state) is False
