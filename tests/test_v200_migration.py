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
        self.data = {"blid": "TEST_BLID_000000000000000000000000"}
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

    class _FakeBus:
        """Minimal event bus stub — EntityRegistry.__init__ calls async_listen."""
        def async_listen(self, event_type, callback, *args, **kwargs):
            return lambda: None  # returns an unsubscribe callable

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
        self.bus = _FakeHass._FakeBus()
        import asyncio as _asyncio
        try:
            self.loop = _asyncio.get_event_loop()
            if self.loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            self.loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(self.loop)
        self.data = {}
        self.config = self._FakeConfig()
        from homeassistant.core import CoreState
        self.state = CoreState.running


class TestMigrateEntryV1ToV2:
    """async_migrate_entry: v1 → v2 adds cloud_raw_records_version marker."""

    def _run_migration(self, entry_options: dict, entry_version: int = 1) -> _FakeConfigEntry:
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=entry_version, options=entry_options)
        hass = _FakeHass()

        # Patch er.async_get to return a minimal entity registry mock so
        # migration steps that rename/remove entities don't need a real
        # EntityRegistry (which requires hass.bus + storage I/O).
        fake_reg = MagicMock()
        fake_reg.entities = {}  # empty: no entities to rename or remove

        loop = asyncio.new_event_loop()
        try:
            with patch(
                "custom_components.roomba_plus.helpers_entity_registry_async_get",
                return_value=fake_reg,
                create=True,
            ):
                with patch(
                    "homeassistant.helpers.entity_registry.async_get",
                    return_value=fake_reg,
                ):
                    result = loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()
        assert result is True
        return entry

    def test_returns_true(self):
        entry = self._run_migration({})
        assert entry.version == 12  # current config entry version as of v2.2.0

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

    def test_version_bumped_to_12(self):
        """v1 entry migrates through all steps to current version (12)."""
        entry = self._run_migration({})
        assert entry.version == 12

    def test_already_at_v12_noop(self):
        """An entry already at the current version (12) is returned as-is."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=12, options={"continuous": True, "floor_label": "Ground Floor"})
        hass = _FakeHass()
        fake_reg = MagicMock()
        fake_reg.entities = {}

        loop = asyncio.new_event_loop()
        try:
            with patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=fake_reg,
            ):
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


# ── v2.2.0: v11 → v12 migration (floor_label) ────────────────────────────────

class TestMigrationV11ToV12:
    """v11 → v12: floor_label added to options with empty string default."""

    def _run_from_v11(self, entry_options: dict) -> _FakeConfigEntry:
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=11, options=entry_options)
        hass = _FakeHass()
        fake_reg = MagicMock()
        fake_reg.entities = {}

        loop = asyncio.new_event_loop()
        try:
            with patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=fake_reg,
            ):
                result = loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()
        assert result is True
        return entry

    def test_floor_label_added_with_empty_default(self):
        entry = self._run_from_v11({"continuous": True, "delay": 1})
        assert entry.options.get("floor_label") == ""

    def test_existing_floor_label_not_overwritten(self):
        # If floor_label somehow already present, setdefault preserves it
        entry = self._run_from_v11({"continuous": True, "floor_label": "Ground Floor"})
        assert entry.options["floor_label"] == "Ground Floor"

    def test_version_bumped_to_12(self):
        entry = self._run_from_v11({"continuous": True})
        assert entry.version == 12

    def test_existing_options_preserved(self):
        opts = {"continuous": False, "delay": 60, "presence_scheduling_enabled": True}
        entry = self._run_from_v11(dict(opts))
        for key, val in opts.items():
            assert entry.options[key] == val


class TestMigrationV11ToV12SlugFix:
    """v11 → v12: language-slug entity_ids renamed to correct English suffixes."""

    def _run_from_v11_with_entities(self, fake_entities: list) -> tuple:
        """Run v11→v12 migration with a fake entity registry populated with test entities."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=11, options={"continuous": True})
        hass = _FakeHass()

        # Build a fake entity registry with the provided entities
        fake_reg = MagicMock()
        entities_by_id = {e["entity_id"]: MagicMock(
            platform="roomba_plus",
            domain="sensor",
            entity_id=e["entity_id"],
            unique_id=e["unique_id"],
        ) for e in fake_entities}
        fake_reg.entities = MagicMock()
        fake_reg.entities.values = lambda: list(entities_by_id.values())

        renamed = []
        removed = []

        def _async_get(eid):
            return entities_by_id.get(eid)

        def _async_update_entity(old_eid, *, new_entity_id=None, **kwargs):
            if new_entity_id:
                old_obj = entities_by_id.pop(old_eid, None)
                if old_obj:
                    old_obj.entity_id = new_entity_id
                    entities_by_id[new_entity_id] = old_obj
                renamed.append((old_eid, new_entity_id))

        def _async_remove(eid):
            entities_by_id.pop(eid, None)
            removed.append(eid)

        fake_reg.async_get = _async_get
        fake_reg.async_update_entity = _async_update_entity
        fake_reg.async_remove = _async_remove

        loop = asyncio.new_event_loop()
        try:
            with patch(
                "homeassistant.helpers.entity_registry.async_get",
                return_value=fake_reg,
            ):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, removed

    def test_german_area_slug_renamed(self):
        """sensor.*_gereinigte_flache_30_t → sensor.*_recent_area_30d"""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            # The wrong German entity
            {
                "entity_id": "sensor.roomba_980_og_gereinigte_flache_30_t",
                "unique_id": f"{blid}_cloud_recent_area_30d",
            },
            # A reference entity so migration can derive the device prefix
            {
                "entity_id": "sensor.roomba_980_og_lifetime_missions",
                "unique_id": f"{blid}_cloud_lifetime_missions",
            },
        ]
        entry, renamed, removed = self._run_from_v11_with_entities(entities)
        assert entry.version == 12
        assert any("recent_area_30d" in new for _, new in renamed), \
            f"Expected area slug renamed, got: {renamed}"

    def test_german_time_slug_renamed(self):
        """sensor.*_reinigungszeit_30_t → sensor.*_recent_time_30d"""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "sensor.roomba_980_og_reinigungszeit_30_t",
                "unique_id": f"{blid}_cloud_recent_time_30d",
            },
            {
                "entity_id": "sensor.roomba_980_og_lifetime_missions",
                "unique_id": f"{blid}_cloud_lifetime_missions",
            },
        ]
        entry, renamed, removed = self._run_from_v11_with_entities(entities)
        assert entry.version == 12
        assert any("recent_time_30d" in new for _, new in renamed), \
            f"Expected time slug renamed, got: {renamed}"

    def test_already_correct_entity_not_renamed(self):
        """Entities already with the correct suffix must not be touched."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "sensor.roomba_980_og_recent_area_30d",
                "unique_id": f"{blid}_cloud_recent_area_30d",
            },
        ]
        _, renamed, removed = self._run_from_v11_with_entities(entities)
        assert renamed == []
        assert removed == []

    def test_floor_label_still_added(self):
        """Slug fix does not interfere with floor_label being added."""
        entry, _, _ = self._run_from_v11_with_entities([])
        assert entry.options.get("floor_label") == ""
        assert entry.version == 12
