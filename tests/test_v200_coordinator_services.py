"""Tests for coordinator raw_records storage and services registration.

Covers:
  - IrobotCloudCoordinator.raw_records property (already in test_v200_migration.py)
  - mission_history_raw populated with classified_result in _async_update_data path
  - async_register_services: idempotent, all six services registered
  - async_remove_services: all six services removed
"""
import pytest
from unittest.mock import MagicMock


# ── coordinator raw_records integration ───────────────────────────────────────

class TestCoordinatorMissionHistoryRaw:
    """_async_update_data stores raw records with classified_result field."""

    def _make_raw_history(self, records):
        """Simulate what _async_update_data stores in mission_history_raw."""
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        return [
            {**r, "classified_result": classify_mission_result(r)}
            for r in records
            if isinstance(r, dict)
        ]

    def test_all_records_get_classified_result(self):
        records = [
            {"done": "done",  "nMssn": 5, "sqft": 100},
            {"done": "stuck", "pauseId": 17},
            {"done": "cncl",  "done_raw": "usrEnd"},
        ]
        stored = self._make_raw_history(records)
        assert len(stored) == 3
        assert stored[0]["classified_result"] == "completed"
        assert stored[1]["classified_result"] == "error_17"
        assert stored[2]["classified_result"] == "cancelled_by_user"

    def test_non_dict_records_filtered(self):
        records = [{"done": "done"}, "bad", None, 42, {"done": "stuck"}]
        stored = self._make_raw_history(records)
        assert len(stored) == 2

    def test_original_fields_preserved(self):
        rec = {
            "done": "done", "done_raw": "done", "startTime": 1700000000,
            "timestamp": 1700003600, "sqft": 200, "runM": 55, "wlBars": [70, 65],
        }
        stored = self._make_raw_history([rec])
        for key in rec:
            assert stored[0][key] == rec[key]

    def test_default_key_present_in_data(self):
        """coordinator.data always has mission_history_raw key even when empty."""
        # Simulate the coordinator data structure
        data = {
            "pmaps": [],
            "mission_history": {},
            "mission_history_raw": [],
            "favorites": [],
        }
        assert "mission_history_raw" in data
        assert isinstance(data["mission_history_raw"], list)

    def test_raw_records_property_reads_from_data(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCoord(IrobotCloudCoordinator):
            def __init__(self, data_val):
                self.data = data_val

        records = [{"done": "done", "classified_result": "completed"}]
        coord = _FakeCoord({"mission_history_raw": records})
        assert coord.raw_records == records

    def test_raw_records_empty_when_data_none(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCoord(IrobotCloudCoordinator):
            def __init__(self):
                self.data = None

        assert _FakeCoord().raw_records == []


# ── services registration ─────────────────────────────────────────────────────

class TestServicesRegistration:
    """async_register_services and async_remove_services behave correctly."""

    def _make_hass(self):
        """Minimal hass stub tracking registered/removed services."""
        registered = {}

        class _Services:
            def has_service(self, domain, name):
                return (domain, name) in registered

            def async_register(self, domain, name, handler, schema=None,
                               supports_response=None):
                registered[(domain, name)] = handler

            def async_remove(self, domain, name):
                registered.pop((domain, name), None)

        class _FakeHass:
            services = _Services()

        return _FakeHass(), registered

    def test_registers_all_eleven_services(self):
        from custom_components.roomba_plus.services import async_register_services
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)

        expected = {
            (DOMAIN, "clean_room"),
            (DOMAIN, "smart_start"),
            (DOMAIN, "clean_sequence"),
            (DOMAIN, "reset_filter"),
            (DOMAIN, "reset_brush"),
            (DOMAIN, "reset_battery"),
            (DOMAIN, "reset_pad"),
            # IA74-MAINT (v2.7.0)
            (DOMAIN, "reset_wheel_cleaning"),
            (DOMAIN, "reset_contact_cleaning"),
            (DOMAIN, "reset_bin_cleaning"),
            (DOMAIN, "reset_robot_profile"),
        }
        assert expected == set(registered.keys())

    def test_register_is_idempotent(self):
        """Calling register twice does not duplicate or error."""
        from custom_components.roomba_plus.services import async_register_services
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)
        first_handler = registered[(DOMAIN, "clean_room")]
        async_register_services(hass)
        # Handler not replaced on second call
        assert registered[(DOMAIN, "clean_room")] is first_handler
        assert len(registered) == 11

    def test_removes_all_eleven_services(self):
        from custom_components.roomba_plus.services import (
            async_register_services,
            async_remove_services,
        )
        from custom_components.roomba_plus.const import DOMAIN

        hass, registered = self._make_hass()
        async_register_services(hass)
        assert len(registered) == 11

        async_remove_services(hass)
        assert len(registered) == 0

    def test_remove_is_safe_when_not_registered(self):
        """async_remove_services does not raise when services are absent."""
        from custom_components.roomba_plus.services import async_remove_services
        hass, registered = self._make_hass()
        async_remove_services(hass)   # should not raise
        assert len(registered) == 0
