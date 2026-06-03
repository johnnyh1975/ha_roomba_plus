"""Tests for v2.0 migration and raw cloud records.

Covers:
  - async_migrate_entry: v1 → v2 options marker, existing data preserved
  - IrobotCloudCoordinator.raw_records property
  - mission_history_raw stored alongside aggregates
  - raw_records empty when coordinator has no data
"""
import sys
import types
import pytest


# ── migration tests ───────────────────────────────────────────────────────────

class _FakeConfigEntry:
    """Minimal config entry stub for migration tests."""

    def __init__(self, version: int, options: dict, entry_id: str = "test_entry_id"):
        self.version = version
        self.options = dict(options)
        self.entry_id = entry_id
        self._updated_options: dict | None = None
        self._updated_version: int | None = None

    def _apply_update(self, options: dict, version: int) -> None:
        self.options = options
        self.version = version
        self._updated_options = options
        self._updated_version = version


class _FakeHass:
    """Minimal hass stub for migration tests."""

    class _FakeConfig:
        config_dir = "/tmp/roomba_plus_test"
        components: set = set()
        def path(self, *parts: str) -> str:
            import os as _os
            p = _os.path.join(self.config_dir, *parts)
            _os.makedirs(_os.path.dirname(p), exist_ok=True)
            return p

    class _ConfigEntries:
        def __init__(self, hass: "_FakeHass"):
            self._hass = hass

        def async_update_entry(self, entry, *, options=None, version=None, **kwargs):
            if options is not None:
                entry.options = options
            if version is not None:
                entry.version = version

    async def async_add_executor_job(self, fn, *args):
        import asyncio as _asyncio
        return await _asyncio.get_event_loop().run_in_executor(None, fn, *args)

    def __init__(self):
        self.config_entries = _FakeHass._ConfigEntries(self)
        import asyncio as _asyncio
        self.loop = _asyncio.get_event_loop()
        self.data = {}
        self.config = self._FakeConfig()
        from homeassistant.core import CoreState
        self.state = CoreState.running


class TestMigrateEntryV1ToV2:
    """async_migrate_entry: v1 → v2 adds cloud_raw_records_version marker."""

    def _run_migration(self, entry_options: dict, entry_version: int = 1) -> _FakeConfigEntry:
        import asyncio
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=entry_version, options=entry_options)
        hass = _FakeHass()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()
        assert result is True
        return entry

    def test_returns_true(self):
        entry = self._run_migration({})
        assert entry.version == 3  # v2.1: v1 migrates through v2 to v3

    def test_adds_marker_key(self):
        entry = self._run_migration({})
        assert entry.options.get("cloud_raw_records_version") == 1

    def test_preserves_existing_options(self):
        original = {
            "continuous": True,
            "delay": 30,
            "smart_zone_data": {"5": {"name": "Kitchen", "pmap_id": "abc"}},
            "blocking_sensors": ["binary_sensor.door"],
            "presence_scheduling_enabled": True,
        }
        entry = self._run_migration(dict(original))
        for key, val in original.items():
            assert entry.options[key] == val, f"Key {key!r} was altered by migration"

    def test_idempotent_marker(self):
        """Running migration twice does not change the marker value."""
        entry = self._run_migration({"cloud_raw_records_version": 1}, entry_version=1)
        assert entry.options["cloud_raw_records_version"] == 1

    def test_version_bumped_to_2(self):
        """v1 entry migrates through v2 to v3 (current as of v2.1.0)."""
        entry = self._run_migration({})
        assert entry.version == 3

    def test_already_at_v2_noop(self):
        """An entry already at version 2 is returned as-is without modification."""
        import asyncio
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=2, options={"continuous": True})
        hass = _FakeHass()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()
        assert result is True
        assert entry._updated_version is None
        assert entry._updated_options is None


# ── raw records in coordinator ────────────────────────────────────────────────

class TestCoordinatorRawRecords:
    """IrobotCloudCoordinator.raw_records returns the stored per-mission list."""

    def _make_coordinator(self, data: dict | None):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator

        class _FakeCoordinator(IrobotCloudCoordinator):
            def __init__(self, data_value):
                # Bypass __init__ — set data directly
                self.data = data_value

        return _FakeCoordinator(data)

    def test_returns_raw_list(self):
        records = [
            {"startTime": 1700000000, "sqft": 120, "done": "done", "nMssn": 42},
            {"startTime": 1699990000, "sqft": 95,  "done": "stuck", "nMssn": 41},
        ]
        coord = self._make_coordinator({"mission_history_raw": records})
        assert coord.raw_records == records

    def test_empty_when_no_data(self):
        coord = self._make_coordinator(None)
        assert coord.raw_records == []

    def test_empty_when_key_missing(self):
        coord = self._make_coordinator({"mission_history": {}, "pmaps": []})
        assert coord.raw_records == []

    def test_empty_list_when_api_returned_nothing(self):
        coord = self._make_coordinator({"mission_history_raw": []})
        assert coord.raw_records == []

    def test_preserves_all_fields(self):
        record = {
            "startTime": 1700000000,
            "timestamp": 1700003600,
            "nMssn": 99,
            "done": "done",
            "done_raw": "done",
            "durationM": 60,
            "runM": 55,
            "chrgM": 0,
            "chrgs": 0,
            "sqft": 200,
            "dirt": 12,
            "evacs": 1,
            "pauseId": 0,
            "wlBars": [70, 68, 65, 60, 62],
            "initiator": "schedule",
        }
        coord = self._make_coordinator({"mission_history_raw": [record]})
        assert coord.raw_records[0] == record


class TestAggregateHistoryPreserved:
    """Existing _aggregate_history output is unchanged alongside raw records."""

    def test_aggregate_still_present(self):
        from custom_components.roomba_plus.cloud_coordinator import _aggregate_history

        records = [
            {"nMssn": 10, "runM": 30, "sqft": 100, "done": "done"},
            {"nMssn": 10, "runM": 20, "sqft": 80,  "done": "stuck"},
        ]
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 10
        assert result["runtimeStats"]["sqft"] == 180
        assert result["runtimeStats"]["hr"] == 0
        assert result["runtimeStats"]["min"] == 50

    def test_nmssn_from_first_record(self):
        """nMssn is the lifetime counter from record[0], not len(records)."""
        from custom_components.roomba_plus.cloud_coordinator import _aggregate_history

        records = [{"nMssn": 414, "runM": 45}] + [{"runM": 30}] * 5
        result = _aggregate_history(records)
        assert result["bbmssn"]["nMssn"] == 414

    def test_empty_records_returns_empty(self):
        from custom_components.roomba_plus.cloud_coordinator import _aggregate_history
        assert _aggregate_history([]) == {}
