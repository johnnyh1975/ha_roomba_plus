"""Tests for CR4 — timeline room-event extraction from MissionStore.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from custom_components.roomba_plus.mission_store import MissionStore


# ── helpers ───────────────────────────────────────────────────────────────────

REGION_MAP = {"19": "Bathroom", "21": "Kitchen", "1": "Hallway", "25": "Bedroom"}

TYPICAL_TIMELINE = {
    "plan": {"upcoming": ["19", "21", "1"], "ordered": 1, "type": "drc"},
    "finEvents": [
        {"type": "start", "ts": 1000},
        {"type": "room", "room": {"rid": "19", "passCount": 1, "status": 1, "area": 72}},
        {"type": "room", "room": {
            "rid": "19", "passCount": 1, "status": 0,
            "area": 72, "passArea": 40, "totalArea": 42,
        }},
        {"type": "room", "room": {"rid": "21", "passCount": 1, "status": 1, "area": 120}},
        {"type": "room", "room": {
            "rid": "21", "passCount": 1, "status": 0,
            "area": 120, "passArea": 90, "totalArea": 95,
        }},
        {"type": "room", "room": {"rid": "1", "passCount": 1, "status": 1, "area": 55}},
        {"type": "room", "room": {
            "rid": "1", "passCount": 1, "status": 0,
            "area": 55, "passArea": 44, "totalArea": 44,
        }},
        {"type": "fin", "ts": 5000},
    ],
}


def _ms_with_timeline(timeline_dict):
    ms = MissionStore()
    ms._records = [{
        "id": "m_1",
        "started_at": "2026-06-01T08:00:00+00:00",
        "ended_at": "2026-06-01T09:00:00+00:00",
        "result": "completed",
        "timeline": timeline_dict,
    }]
    return ms


def _ms_without_timeline():
    ms = MissionStore()
    ms._records = [{
        "id": "m_1",
        "started_at": "2026-06-01T08:00:00+00:00",
        "ended_at": "2026-06-01T09:00:00+00:00",
        "result": "completed",
    }]
    return ms


# ── _resolve_region_ids ───────────────────────────────────────────────────────

class TestResolveRegionIds:
    def test_known_ids_resolved(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["19", "21"], REGION_MAP)
        assert result == ["Bathroom", "Kitchen"]

    def test_unknown_id_returned_as_raw(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["99"], REGION_MAP)
        assert result == ["99"]

    def test_empty_list(self):
        ms = MissionStore()
        assert ms._resolve_region_ids([], REGION_MAP) == []

    def test_empty_region_map(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["19", "21"], {})
        assert result == ["19", "21"]


# ── latest_cleaned_rooms ──────────────────────────────────────────────────────

class TestLatestCleanedRooms:
    def test_returns_rooms_in_completion_order(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_skips_status_1_events(self):
        # status=1 = pass in progress — must not appear in output
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert len(result) == 3  # only 3 status=0 events

    def test_status_6_treated_as_complete(self):
        # status=6 = completed after error recovery (lewis 22.52.10+ confirmed,
        # Mission 798 in Thonno's debug: error(5) + resume + done=ok)
        ms = _ms_with_timeline({
            "plan": {"upcoming": [{"type": "rid", "rid": "23"}]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "23", "passCount": 1, "status": 6,
                    "area": 37, "passArea": 16,
                    # totalArea absent on status=6 — expected; coverage will be skipped
                }},
            ],
        })
        result = ms.latest_cleaned_rooms({"23": "Bathroom"})
        assert result == ["Bathroom"]

    def test_status_5_excluded(self):
        # status=5 = interrupted by user/app (Mission 799: pause+dock by rmtApp)
        ms = _ms_with_timeline({
            "plan": {"upcoming": []},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "26", "passCount": 1, "status": 5,
                    "area": 251, "passArea": 40,
                }},
            ],
        })
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_for_whole_home_no_room_events(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": [], "ordered": 0},
            "finEvents": [{"type": "fin"}],
        })
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_when_no_timeline_field(self):
        ms = _ms_without_timeline()
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_unknown_rid_returned_as_raw(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["99"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "99", "status": 0,
                                           "area": 50, "totalArea": 40}},
            ],
        })
        result = ms.latest_cleaned_rooms({})
        assert result == ["99"]

    def test_traversal_events_ignored(self):
        # traversal events are NOT room completions — must be skipped
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "traversal", "traversal": {"rid": "19", "type": "region"}},
                {"type": "room", "room": {"rid": "19", "status": 0,
                                           "area": 72, "totalArea": 42}},
            ],
        })
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom"]

    def test_room_appears_once_even_with_multiple_passes(self):
        # Two passes in the same room → appears once in output
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "passCount": 1,
                                           "status": 0, "area": 72, "totalArea": 35}},
                {"type": "room", "room": {"rid": "19", "passCount": 2,
                                           "status": 0, "area": 72, "totalArea": 70}},
            ],
        })
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom"]
        assert len(result) == 1


# ── latest_planned_order ──────────────────────────────────────────────────────

class TestExtractRid:
    """_extract_rid handles two confirmed plan.upcoming formats."""

    def test_string_format(self):
        ms = MissionStore()
        assert ms._extract_rid("23") == "23"

    def test_object_format_rid_key(self):
        # lewis 22.52.10+: {"type": "rid", "rid": "23"}
        ms = MissionStore()
        assert ms._extract_rid({"type": "rid", "rid": "23"}) == "23"

    def test_object_format_region_id_key(self):
        # fallback key name
        ms = MissionStore()
        assert ms._extract_rid({"region_id": "19"}) == "19"

    def test_none_returns_empty(self):
        ms = MissionStore()
        assert ms._extract_rid(None) == ""

    def test_empty_dict_returns_empty(self):
        ms = MissionStore()
        assert ms._extract_rid({}) == ""


class TestLatestPlannedOrder:
    def test_returns_planned_order(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_object_format_upcoming(self):
        # lewis 22.52.10+: plan.upcoming as list of dicts (Mission 800 confirmed)
        ms = _ms_with_timeline({
            "plan": {
                "pmapId": "8VfoJEhaQ12ZGZaGlJp3wQ",
                "ordered": 1, "type": "drc",
                "upcoming": [
                    {"type": "rid", "rid": "19"},
                    {"type": "rid", "rid": "21"},
                ],
            },
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen"]

    def test_string_format_upcoming(self):
        # Older format: plan.upcoming as list of plain strings
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19", "21", "1"], "ordered": 1, "type": "drc"},
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_mixed_format_skips_unrecognised(self):
        # Defensive: mixed format drops empty-rid entries
        ms = _ms_with_timeline({
            "plan": {"upcoming": [{"type": "rid", "rid": "19"}, {}]},
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom"]  # empty dict dropped

    def test_returns_none_when_upcoming_empty(self):
        ms = _ms_with_timeline({"plan": {"upcoming": []}, "finEvents": []})
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_when_no_plan(self):
        ms = _ms_with_timeline({"finEvents": []})
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_without_timeline(self):
        ms = _ms_without_timeline()
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_planned_order(REGION_MAP) is None


# ── latest_mission_destination ────────────────────────────────────────────────

class TestLatestMissionDestination:
    def test_returns_last_room(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        # planned order is ["19", "21", "1"] → Hallway
        assert ms.latest_mission_destination(REGION_MAP) == "Hallway"

    def test_returns_none_when_no_planned_order(self):
        ms = MissionStore()
        assert ms.latest_mission_destination(REGION_MAP) is None

    def test_single_room_mission(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"], "ordered": 1},
            "finEvents": [],
        })
        assert ms.latest_mission_destination(REGION_MAP) == "Bathroom"


# ── latest_room_coverage ──────────────────────────────────────────────────────

class TestLatestRoomCoverage:
    def test_coverage_fractions_computed(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_room_coverage(REGION_MAP)
        assert result is not None
        assert pytest.approx(result["Bathroom"], abs=0.01) == 42 / 72
        assert pytest.approx(result["Kitchen"],  abs=0.01) == 95 / 120
        assert pytest.approx(result["Hallway"],  abs=0.01) == 44 / 55

    def test_status_6_included_when_totalArea_present(self):
        # status=6 with totalArea — should be included
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 6,
                    "area": 76, "passArea": 41, "totalArea": 50,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Bathroom"})
        assert result is not None
        assert pytest.approx(result["Bathroom"], abs=0.01) == 50 / 76

    def test_status_6_without_totalArea_skipped(self):
        # status=6 without totalArea — gracefully skipped (coverage shows None for room)
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["23"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "23", "status": 6,
                    "area": 37, "passArea": 16,
                    # totalArea absent — confirmed on Thonno's lewis 22.52.10 robot
                }},
            ],
        })
        # No qualifying events with totalArea → returns None
        assert ms.latest_room_coverage({"23": "Bathroom"}) is None

    def test_status_5_excluded_from_coverage(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": []},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "26", "status": 5, "area": 251,
                    "passArea": 40,
                }},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_coverage_clamped_to_1(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0, "area": 50, "totalArea": 60,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Room"})
        assert result is not None
        assert result["Room"] <= 1.0

    def test_coverage_clamped_to_0(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0, "area": 50, "totalArea": -5,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Room"})
        assert result is not None
        assert result["Room"] >= 0.0

    def test_returns_none_when_no_status0_events(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 1, "area": 50}},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_returns_none_without_timeline(self):
        ms = _ms_without_timeline()
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_skips_events_missing_area(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 0, "totalArea": 40}},
            ],
        })
        # area is missing → skip → None
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_skips_events_missing_total_area(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 0, "area": 72}},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_uses_region_map_for_keys(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_room_coverage(REGION_MAP)
        assert result is not None
        assert "Bathroom" in result
        assert "19" not in result  # raw ID not in output when map resolves it
