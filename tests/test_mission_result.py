"""How a mission ended, as a type, with the rooms it left out (4.6.0)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus.mission_result import (
    RESULT_TYPES,
    async_setup_mission_result,
    mission_outcome,
    rooms_to_retry,
)


def _record(**fields):
    base = {
        "id": "m_1", "result": "completed", "scope": "rooms",
        "zones": ["Kitchen", "Hall", "Bath"],
        "last_cleaned_rooms": ["Kitchen", "Hall", "Bath"],
        "skipped_rooms": [],
    }
    base.update(fields)
    return base


class TestTheType:
    @pytest.mark.parametrize(("fields", "kind"), [
        ({}, "completed"),
        ({"result": "stuck_and_resumed"}, "completed"),
        ({"skipped_rooms": ["Hall"], "last_cleaned_rooms": ["Kitchen", "Bath"]},
         "completed_with_skips"),
        ({"last_cleaned_rooms": ["Kitchen"]}, "completed_with_skips"),
        ({"result": "cancelled"}, "cancelled"),
        ({"result": "blocked_timeout"}, "cancelled"),
        ({"result": "stuck"}, "stuck_abandoned"),
        ({"result": "stuck_and_abandoned"}, "stuck_abandoned"),
        ({"result": "error", "error_code": 48}, "error"),
        # Prime: a done code the table does not know, with an error.
        ({"result": "unknown", "error_code": 48}, "error"),
    ])
    def test_each_result(self, fields, kind):
        assert mission_outcome(_record(**fields))["type"] == kind

    @pytest.mark.parametrize("fields", [
        {"result": "in_progress"},
        {"result": "unknown"},
        {"result": None},
    ])
    def test_an_unsettled_or_unknown_result_has_no_type(self, fields):
        assert mission_outcome(_record(**fields)) is None

    def test_not_a_record(self):
        assert mission_outcome(None) is None

    def test_every_type_is_listed(self):
        assert set(RESULT_TYPES) == {
            "completed", "completed_with_skips", "cancelled", "stuck_abandoned", "error",
        }


class TestRoomsNotReached:
    """Claimed only where the rooms the robot cleaned were observed."""

    def test_an_observed_run_names_the_room_it_missed(self):
        out = mission_outcome(_record(last_cleaned_rooms=["Kitchen", "Bath"]))
        assert out["missed_rooms"] == ["Hall"]

    @pytest.mark.parametrize("source", ["requested", "awaiting_cloud"])
    def test_a_guess_claims_nothing(self, source):
        out = mission_outcome(_record(rooms_source=source, last_cleaned_rooms=["Kitchen"]))
        assert out["missed_rooms"] == []
        assert out["type"] == "completed"

    def test_a_whole_home_run_has_no_requested_rooms_to_miss(self):
        out = mission_outcome(_record(scope="whole_home", last_cleaned_rooms=["Kitchen"]))
        assert out["missed_rooms"] == []

    def test_a_skipped_room_is_not_also_missed(self):
        out = mission_outcome(_record(skipped_rooms=["Hall"], last_cleaned_rooms=["Kitchen", "Bath"]))
        assert out["skipped_rooms"] == ["Hall"]
        assert out["missed_rooms"] == []


class TestRoomsToRetry:
    def test_in_the_order_the_mission_asked(self):
        record = _record(skipped_rooms=["Bath"], last_cleaned_rooms=["Hall"])
        assert rooms_to_retry(record) == ["Kitchen", "Bath"]

    def test_a_skipped_room_outside_the_request_comes_last(self):
        record = _record(scope="whole_home", zones=[], skipped_rooms=["Hall"])
        assert rooms_to_retry(record) == ["Hall"]

    def test_nothing_left_out(self):
        assert rooms_to_retry(_record()) == []
        assert rooms_to_retry(None) == []


class TestTheEventFollowsTheMissionEnd:
    """With Home Assistant's own bus."""

    @staticmethod
    def _entry(hass, record):
        entry = MagicMock()
        entry.entry_id = "E1"
        entry.title = "Robo"
        entry.runtime_data.mission_store.find_by_id = (
            lambda mid: record if mid == record["id"] else None
        )
        unloads = []
        entry.async_on_unload = unloads.append
        async_setup_mission_result(hass, entry)
        return entry, unloads

    @staticmethod
    def _collect(hass):
        seen = []
        hass.bus.async_listen("roomba_plus_mission_result", lambda e: seen.append(e.data))
        return seen

    async def test_it_fires_with_the_rooms(self, hass):
        _, unloads = self._entry(hass, _record(skipped_rooms=["Hall"], last_cleaned_rooms=["Kitchen"]))
        seen = self._collect(hass)
        hass.bus.async_fire("roomba_plus_mission_completed", {
            "entry_id": "E1", "mission_id": "m_1", "missions_added": 1,
        })
        await hass.async_block_till_done()
        (data,) = seen
        assert data["type"] == "completed_with_skips"
        assert data["skipped_rooms"] == ["Hall"]
        assert data["missed_rooms"] == ["Bath"]
        assert data["mission_id"] == "m_1"
        assert data["name"] == "Robo"
        unloads[0]()

    @pytest.mark.parametrize("payload", [
        {"entry_id": "OTHER", "mission_id": "m_1", "missions_added": 1},
        # A Prime backfill adds the whole history at once.
        {"entry_id": "E1", "mission_id": "m_1", "missions_added": 40},
        {"entry_id": "E1", "mission_id": "m_unknown", "missions_added": 1},
        {"entry_id": "E1", "missions_added": 1},
    ])
    async def test_nothing_for_another_robot_a_backfill_or_an_unknown_mission(self, hass, payload):
        _, unloads = self._entry(hass, _record())
        seen = self._collect(hass)
        hass.bus.async_fire("roomba_plus_mission_completed", payload)
        await hass.async_block_till_done()
        assert seen == []
        unloads[0]()


class TestLastError:
    """4.6.0: `last_error_code`/`last_error_at` on Prime, from the records."""

    def test_the_newest_mission_that_ended_in_error(self):
        from custom_components.roomba_plus.mission_result import last_error

        records = [
            {"result": "unknown", "error_code": 48, "ended_at": "2026-11-01T10:00:00+00:00"},
            {"result": "completed", "error_code": 17, "ended_at": "2026-11-02T10:00:00+00:00"},
            {"result": "cancelled", "ended_at": "2026-11-03T10:00:00+00:00"},
        ]
        assert last_error(records) == (48, "2026-11-01T10:00:00+00:00")
        assert last_error([{"result": "completed"}]) is None
        assert last_error(None) is None

    def test_noted_on_the_runtime_data_and_kept_without_a_newer_one(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.mission_result import note_last_error

        data = SimpleNamespace(
            mission_store=SimpleNamespace(records=[
                {"result": "error", "error_code": 3, "ended_at": "T1"},
            ]),
            last_error_code=None, last_error_at=None,
        )
        note_last_error(data)
        assert (data.last_error_code, data.last_error_at) == (3, "T1")
        data.mission_store.records = [{"result": "completed"}]
        note_last_error(data)
        assert (data.last_error_code, data.last_error_at) == (3, "T1")


class TestPrimeRoomsLeftOut:
    """4.6.0, A5 on Prime: the requested region ids against the regions the
    timeline cleaned, named from the maps for the event and the retry."""

    RECORD = {
        "id": "p_m1", "result": "completed", "scope": "rooms",
        "requested_region_ids": ["10", "11", "12"], "rooms_cleaned": ["10"],
    }

    def test_missed_regions(self):
        from custom_components.roomba_plus.mission_result import mission_outcome, rooms_to_retry

        outcome = mission_outcome(self.RECORD)
        assert outcome["type"] == "completed_with_skips"
        assert outcome["missed_rooms"] == ["11", "12"]
        assert rooms_to_retry(self.RECORD) == ["11", "12"]

    def test_named_from_the_maps(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.mission_result import named_rooms

        data = SimpleNamespace(prime_room_names={"11": "Hall"})
        assert named_rooms(data, self.RECORD, ["11", "12"]) == ["Hall", "12"]
        classic = {"zones": ["Kitchen"], "result": "completed"}
        assert named_rooms(data, classic, ["Kitchen"]) == ["Kitchen"]

    def test_whole_home_prime_has_nothing_missed(self):
        from custom_components.roomba_plus.mission_result import mission_outcome

        outcome = mission_outcome({"result": "completed", "scope": "whole_home", "rooms_cleaned": ["10"]})
        assert outcome["type"] == "completed"
