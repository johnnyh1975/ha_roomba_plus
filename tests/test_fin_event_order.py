"""The cloud's finEvents arrive NEWEST FIRST (4.2.16).

Both real captures show it: the i3+ fixture, and a Braava m6 mission
exported by @ScenicSystemsLLC (roomba_plus_export_braava_m6.json: Guest
Bathroom, rid 10, then Bedroom 2, rid 16). Every reader that walked the
list as a sequence got the mission backwards -- the rooms listed
last-cleaned first, and time per room built from negative gaps, which
were then dropped, so the accessibility score's time figure stayed
empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.roomba_plus.const import fin_events_in_order
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.room_times import timeline_room_seconds

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = {"10": "Guest Bathroom", "16": "Bedroom 2"}


def _eva() -> dict:
    return json.loads((FIXTURES / "roomba_plus_export_braava_m6.json").read_text())


def _i3plus_records() -> list[dict]:
    return json.loads((FIXTURES / "irobot_missionhistory_i3plus.json").read_text())


def test_both_captures_really_are_newest_first():
    """The premise, pinned to the data: if a capture ever arrives in
    order, this is the test that says so."""
    for timeline in [_eva()["timeline"]] + [
        r["timeline"] for r in _i3plus_records() if isinstance(r.get("timeline"), dict)
    ]:
        stamps = [e["ts"] for e in timeline["finEvents"] if "ts" in e]
        assert stamps[0] > stamps[-1]


def test_events_come_back_oldest_first():
    events = fin_events_in_order(_eva()["timeline"])
    stamps = [e["ts"] for e in events]
    assert stamps == sorted(stamps)
    assert events[0]["type"] == "start"
    assert [e["room"]["rid"] for e in events if e["type"] == "room"] == ["10", "16"]


def test_events_without_a_time_keep_their_place_after_the_rest():
    timeline = {"finEvents": [{"type": "b", "ts": 5}, {"type": "x"}, "junk", {"type": "a", "ts": 1}, {"type": "y", "ts": True}]}
    assert [e["type"] for e in fin_events_in_order(timeline)] == ["a", "b", "x", "y"]
    assert fin_events_in_order(None) == []
    assert fin_events_in_order({"finEvents": None}) == []


def test_cleaned_rooms_are_listed_in_the_order_they_were_cleaned():
    """The export listed "Bedroom 2", "Guest Bathroom" -- the reverse."""
    store = MissionStore()
    assert store._record_room_names(_eva(), NAMES) == ["Guest Bathroom", "Bedroom 2"]
    assert MissionStore.record_region_ids(_eva()) == ["10", "16"]


def test_archived_visits_are_in_order_and_time_per_room_is_filled():
    derived = MissionArchive()._parse_derived({**_eva(), "startTime": 1790615250})
    assert [v["rid"] for v in derived["room_visits"]] == ["10", "16"]
    # From arriving in room 10 to arriving in room 16; the last room has
    # no next event to measure against.
    assert MissionArchive.time_per_room(derived["room_visits"]) == {"10": 251}
    assert [p["rid"] for p in MissionArchive.mission_path(derived["room_visits"])] == ["10", "16"]


def test_visits_archived_newest_first_before_4_2_16_still_count():
    stored = [{"rid": "16", "ts": 1790615534}, {"rid": "10", "ts": 1790615283}]
    assert MissionArchive.time_per_room(stored) == {"10": 251}
    assert [p["rid"] for p in MissionArchive.mission_path(stored)] == ["10", "16"]


def test_the_archived_event_timeline_runs_forwards():
    layer = MissionArchive()._parse_timeline(_eva())
    rooms = [entry[1]["rid"] for entry in layer if entry[0] == "room_done"]
    assert rooms == ["10", "16"]


def test_room_times_from_the_braava_capture():
    """ts to the next drive: 233 s and 742 s -- in this capture exactly
    each event's own `ets`, which (unlike the PyRoomba sample) is not a
    constant mission end here."""
    assert timeline_room_seconds(_eva()) == {"10": 233.0, "16": 742.0}
