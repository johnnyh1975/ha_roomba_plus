"""v2.9.0 LOGBOOK — logbook.py describer function tests.

No prior tests existed (the file is new in v2.9.0). Tests call the
describe_* closures directly by capturing them via a fake
async_describe_event collector, rather than spinning up the real
homeassistant.components.logbook integration (consistent with this test
suite's general avoidance of the full real-hass machinery).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roomba_plus.const import DOMAIN, EVENT_MAINTENANCE_RESET, EVENT_MISSION_COMPLETED, EVENT_STUCK
from custom_components.roomba_plus.logbook import async_describe_events


def _collect_describers() -> dict[str, callable]:
    """Call async_describe_events() and capture the registered describers,
    keyed by event_type, instead of by relying on the real logbook
    integration's registration plumbing."""
    described: dict[str, callable] = {}

    def _capture(domain: str, event_type: str, describer) -> None:
        assert domain == DOMAIN
        described[event_type] = describer

    async_describe_events(MagicMock(), _capture)
    return described


def _event(data: dict) -> MagicMock:
    e = MagicMock()
    e.data = data
    return e


class TestDescribeMissionCompleted:
    def setup_method(self):
        self.describe = _collect_describers()[EVENT_MISSION_COMPLETED]

    def test_completed_no_extras(self):
        result = self.describe(_event({"name": "Roomba 980", "result": "completed"}))
        assert result["name"] == "Roomba 980"
        assert result["message"] == "finished cleaning"

    def test_completed_with_rooms_and_area(self):
        result = self.describe(_event({
            "name": "Roomba 980", "result": "completed",
            "rooms_cleaned": 2, "area_sqft": 250,
        }))
        assert result["message"] == "finished cleaning (2 rooms, 250 sqft)"

    def test_singular_room(self):
        result = self.describe(_event({
            "name": "Roomba 980", "result": "completed", "rooms_cleaned": 1,
        }))
        assert "1 room" in result["message"]
        assert "1 rooms" not in result["message"]

    def test_stuck_result(self):
        result = self.describe(_event({
            "name": "Roomba 980", "result": "stuck", "stuck_count": 1,
        }))
        assert "got stuck while cleaning" in result["message"]
        assert "1 stuck event" in result["message"]

    def test_unknown_result_falls_back_gracefully(self):
        result = self.describe(_event({"name": "Roomba 980", "result": "something_new"}))
        assert "something_new" in result["message"]

    def test_missing_name_falls_back(self):
        result = self.describe(_event({"result": "completed"}))
        assert result["name"] == "Roomba+"

    def test_zero_rooms_cleaned_not_mentioned(self):
        """rooms_cleaned=0 (NONE-tier robot, no zone data) shouldn't render
        as a nonsensical '0 rooms' detail."""
        result = self.describe(_event({
            "name": "Roomba 980", "result": "completed", "rooms_cleaned": 0,
        }))
        assert "room" not in result["message"]


class TestDescribeMaintenanceReset:
    def setup_method(self):
        self.describe = _collect_describers()[EVENT_MAINTENANCE_RESET]

    def test_with_hours(self):
        result = self.describe(_event({
            "name": "Roomba 980", "component": "filter", "hours": 142,
        }))
        assert result["name"] == "Roomba 980"
        assert result["message"] == "filter reset at 142h"

    def test_without_hours(self):
        result = self.describe(_event({
            "name": "Roomba 980", "component": "wheel", "hours": None,
        }))
        assert result["message"] == "wheel cleaning reset"

    def test_pad_label(self):
        result = self.describe(_event({
            "name": "Braava M6", "component": "pad", "hours": 30,
        }))
        assert "mop pad" in result["message"]

    def test_unknown_component_falls_back_to_raw_value(self):
        result = self.describe(_event({
            "name": "Roomba 980", "component": "something_new", "hours": None,
        }))
        assert "something_new" in result["message"]


class TestDescribeStuck:
    """v3.2.0 STUCK-CONTEXT — describe_stuck shares wording with the
    push-notification-facing EVENT_STUCK payload, same dual-use pattern
    as EVENT_MISSION_COMPLETED."""

    def setup_method(self):
        self.describe = _collect_describers()[EVENT_STUCK]

    def test_basic_message(self):
        result = self.describe(_event({"name": "Roomba 980"}))
        assert result["name"] == "Roomba 980"
        assert result["message"] == "got stuck"

    def test_includes_room_when_known(self):
        result = self.describe(_event({
            "name": "Roomba 980", "last_room": "Kitchen",
        }))
        assert "Kitchen" in result["message"]

    def test_includes_minutes_when_known(self):
        result = self.describe(_event({
            "name": "Roomba 980", "last_room": "Kitchen", "minutes_stuck": 5,
        }))
        assert result["message"] == "got stuck — Kitchen, 5 min"

    def test_no_room_no_dash(self):
        result = self.describe(_event({
            "name": "Roomba 980", "minutes_stuck": 5,
        }))
        assert "—" not in result["message"]

    def test_falls_back_to_roomba_plus_name(self):
        result = self.describe(_event({}))
        assert result["name"] == "Roomba+"


class TestRegistersAllEvents:
    def test_all_event_types_registered(self):
        from custom_components.roomba_plus.const import (
            EVENT_CANCELLATION_RECURRENCE,
            EVENT_CLOUD_STALE,
            EVENT_ERROR_RECURRENCE,
            EVENT_MAP_DRIFT_DETECTED,
            EVENT_MAP_RETRAIN_IN_PROGRESS,
            EVENT_MISSION_ANOMALY,
            EVENT_MIXED_SCHEDULE,
            EVENT_SCHEDULE_SUBOPTIMAL,
            EVENT_STUCK_PATTERN,
        )
        described = _collect_describers()
        assert EVENT_MISSION_COMPLETED in described
        assert EVENT_MAINTENANCE_RESET in described
        assert EVENT_STUCK in described
        # v3.5.0 Repairs redesign — signals demoted from Repair Issue to event
        assert EVENT_ERROR_RECURRENCE in described
        assert EVENT_CANCELLATION_RECURRENCE in described
        assert EVENT_STUCK_PATTERN in described
        assert EVENT_MISSION_ANOMALY in described
        assert EVENT_MIXED_SCHEDULE in described
        assert EVENT_SCHEDULE_SUBOPTIMAL in described
        assert EVENT_MAP_DRIFT_DETECTED in described
        assert EVENT_MAP_RETRAIN_IN_PROGRESS in described
        assert EVENT_CLOUD_STALE in described
        assert len(described) == 12
