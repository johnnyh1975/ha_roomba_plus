"""Answers for scripts and Assist (4.6.0, B6, B8)."""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util

from custom_components.roomba_plus import mission_queries as mq
from custom_components.roomba_plus.models import ConnectionType
from custom_components.roomba_plus.room_times import remember

NOW = datetime.datetime(2026, 11, 13, 20, 0, tzinfo=datetime.timezone.utc)


def _data(prime=False, records=(), **extra):
    cache: dict[str, float] = {}
    for value in (600.0, 610.0, 590.0):
        remember(cache, "Kitchen", value, None)
    data = SimpleNamespace(
        connection_type=ConnectionType.CLOUD_ONLY if prime else ConnectionType.LOCAL_PUSH,
        prime_room_names={},
        prime_room_map_ids={},
        cloud_coordinator=None,
        mission_store=SimpleNamespace(records=list(records)),
        robot_profile_store=SimpleNamespace(room_estimate_cache=cache, mission_duration_mean_sec=3600),
        roomba_reported_state=lambda: {},
    )
    for key, value in extra.items():
        setattr(data, key, value)
    return data


def _entry(data):
    return SimpleNamespace(entry_id="e1", runtime_data=data)


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    monkeypatch.setattr(mq, "async_room_area_fields", lambda *_a: {"area_id": None, "area_name": None})
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)


def _estimates(mapping):
    return patch(
        "custom_components.roomba_plus.sensor_rooms._compute_room_time_estimates",
        side_effect=lambda _e, rooms: [mapping.get(r) for r in rooms],
    )


class TestRooms:
    def test_classic_rooms_from_the_active_map(self):
        cloud = SimpleNamespace(regions=[
            {"id": "1", "name": "Kitchen", "pmap_id": "P1", "region_type": "kitchen"},
            {"id": "2", "name": "", "pmap_id": "P1"},
        ])
        with _estimates({"Kitchen": 600}):
            rooms = mq.rooms_of(MagicMock(), _entry(_data(cloud_coordinator=cloud)))
        assert rooms == [{
            "id": "1", "name": "Kitchen", "map_id": "P1", "type": "kitchen",
            "area_id": None, "area_name": None, "estimate_sec": 600,
            "estimate_quality": {"runs": 3, "spread_sec": 10.0, "rating": "medium"},
        }]

    def test_prime_rooms_from_the_names_of_its_maps(self):
        data = _data(prime=True, prime_room_names={"7": "Flur", "8": None},
                     prime_room_map_ids={"7": "M1"})
        with _estimates({}):
            rooms = mq.rooms_of(MagicMock(), _entry(data))
        assert [(r["id"], r["name"], r["map_id"], r["type"], r["estimate_sec"]) for r in rooms] == [
            ("7", "Flur", "M1", None, None),
        ]
        assert rooms[0]["estimate_quality"]["rating"] == "none"

    def test_an_estimate_that_fails_is_none(self):
        cloud = SimpleNamespace(regions=[{"id": "1", "name": "Kitchen", "pmap_id": "P1"}])
        with patch(
            "custom_components.roomba_plus.sensor_rooms._compute_room_time_estimates",
            side_effect=RuntimeError,
        ):
            rooms = mq.rooms_of(MagicMock(), _entry(_data(cloud_coordinator=cloud)))
        assert rooms[0]["estimate_sec"] is None


_CLASSIC = {
    "id": "m_1", "started_at": "2026-11-13T09:00:00+00:00",
    "ended_at": "2026-11-13T10:00:00+00:00", "duration_min": 60,
    "result": "completed", "initiator": "schedule", "scope": "rooms",
    "zones": ["Kitchen", "Hall"], "last_cleaned_rooms": ["Kitchen"],
    "skipped_rooms": [], "rooms_source": "observed", "area_sqft": 200,
    "battery_used_pct": 20, "estimated_duration_min": 50.0,
    "room_learning": [{"room": "Kitchen", "seconds": 600.0, "accepted": True, "reason": None}],
}
_PRIME = {
    "id": "p_1", "ended_at": "2026-11-12T10:00:00+00:00", "result": "completed",
    "scope": "rooms", "requested_region_ids": ["7", "8"], "rooms_cleaned": ["7"],
    "room_learning": [{"room_id": "7", "seconds": 300.0, "accepted": True, "reason": None}],
    "nMssn": 42,
}


class TestMissions:
    def test_a_classic_summary(self):
        out = mq.mission_summary(_data(), _CLASSIC)
        assert out["type"] == "completed_with_skips"
        assert out["requested_rooms"] == ["Kitchen", "Hall"]
        assert out["missed_rooms"] == ["Hall"]
        assert out["cleaned_rooms"] == ["Kitchen"]
        assert out["battery_used_pct"] == 20
        assert out["estimated_duration_min"] == 50.0

    def test_a_prime_summary_names_its_rooms(self):
        data = _data(prime=True, prime_room_names={"7": "Flur", "8": "Bad"})
        out = mq.mission_summary(data, _PRIME)
        assert out["requested_rooms"] == ["Flur", "Bad"]
        assert out["cleaned_rooms"] == ["Flur"]
        assert out["missed_rooms"] == ["Bad"]

    def test_the_detail_carries_the_learning(self):
        data = _data(prime=True, prime_room_names={"7": "Flur"})
        out = mq.mission_detail(data, _PRIME)
        assert out["room_learning"] == [
            {"room_id": "7", "seconds": 300.0, "accepted": True, "reason": None, "room": "Flur"},
        ]
        assert out["room_learning_source"] == "timeline"
        assert out["nMssn"] == 42

    def test_room_tracking_learning(self):
        out = mq.mission_detail(_data(), _CLASSIC)
        assert out["room_learning_source"] == "room_tracking"

    def test_a_cloud_timeline_is_read_for_its_learning(self):
        record = {"id": "m_2", "result": "completed", "timestamp": 2000, "timeline": {"finEvents": [
            {"type": "room", "ts": 100, "room": {"rid": "1", "status": 0, "area": 10, "passArea": 9}},
            {"type": "travel", "ts": 700},
        ]}}
        cloud = SimpleNamespace(regions=[], regions_by_pmap={"P1": {"1": "Kitchen"}})
        out = mq.mission_detail(_data(cloud_coordinator=cloud), record)
        assert out["room_learning"][0]["room"] == "Kitchen"
        assert out["room_learning_source"] == "timeline"

    def test_no_learning(self):
        out = mq.mission_detail(_data(), {"id": "m_3", "result": "completed"})
        assert (out["room_learning"], out["room_learning_source"]) == ([], None)

    def test_newest_first_with_limit_and_days(self):
        records = [
            {"id": "old", "ended_at": "2026-10-01T10:00:00+00:00", "result": "completed"},
            {"id": "a", "ended_at": "2026-11-12T10:00:00+00:00", "result": "completed"},
            "junk",
            {"id": "b", "ended_at": "2026-11-13T10:00:00+00:00", "result": "completed"},
        ]
        data = _data(records=records)
        assert [m["id"] for m in mq.missions_of(data)] == ["b", "a", "old"]
        assert [m["id"] for m in mq.missions_of(data, limit=1)] == ["b"]
        assert [m["id"] for m in mq.missions_of(data, days=7)] == ["b", "a"]

    def test_find(self):
        data = _data(records=[{"id": "a"}, {"id": "b"}])
        assert mq.find_mission(data, None) == {"id": "b"}
        assert mq.find_mission(data, "a") == {"id": "a"}
        assert mq.find_mission(data, "z") is None
        assert mq.find_mission(_data(), None) is None


class TestPlanClean:
    def _plan(self, rooms, estimates, **kw):
        cloud = SimpleNamespace(regions=[
            {"id": "1", "name": "Kitchen", "pmap_id": "P1"},
            {"id": "2", "name": "Hall", "pmap_id": "P1"},
        ])
        data = _data(cloud_coordinator=cloud, records=[
            {"estimated_duration_min": 50, "duration_min": 55},
        ])
        with _estimates(estimates):
            return mq.plan_clean(MagicMock(), _entry(data), rooms, **kw)

    def test_rooms_in_order_with_the_total(self):
        plan = self._plan(["hall", "Kitchen", "Attic"], {"Kitchen": 600, "Hall": 300},
                          end_by=NOW + datetime.timedelta(minutes=20))
        assert [r["room"] for r in plan["rooms"]] == ["Hall", "Kitchen"]
        assert plan["unknown_rooms"] == ["Attic"]
        assert plan["total_sec"] == 900
        assert plan["end"] == (NOW + datetime.timedelta(seconds=900)).isoformat(timespec="seconds")
        assert plan["fits"] is True
        assert plan["past_error"] == {"missions": 1, "median_error_pct": 10.0}

    def test_a_room_without_an_estimate_leaves_the_total_open(self):
        plan = self._plan(["Kitchen", "Hall"], {"Kitchen": 600}, end_by=NOW)
        assert plan["total_sec"] is None
        assert plan["rooms_without_estimate"] == ["Hall"]
        assert plan["end"] is None and plan["fits"] is None

    def test_whole_home_is_the_mean_mission(self):
        plan = self._plan(None, {}, end_by=datetime.datetime(2026, 11, 13, 20, 30))
        assert (plan["scope"], plan["total_sec"]) == ("whole_home", 3600)
        assert plan["fits"] is False

    def test_no_known_room_no_total(self):
        plan = self._plan(["Attic"], {})
        assert plan["total_sec"] is None and plan["rooms"] == []
