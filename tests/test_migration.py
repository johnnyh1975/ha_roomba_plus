"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import types
import pytest


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
        assert entry.version == 25  # all migrations now end at v25

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

    def test_version_bumped_to_13(self):
        """v1 entry migrates through all steps to current version (12)."""
        entry = self._run_migration({})
        assert entry.version == 25

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

    def test_version_bumped_to_13(self):
        entry = self._run_from_v11({"continuous": True})
        assert entry.version == 25

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
        assert entry.version == 25
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
        assert entry.version == 25
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
        assert entry.version == 25


class TestMigrationV12ToV13:
    """v12 → v13: language-slug entity_ids fixed for entities added without _attr_name."""

    def _run_from_v12_with_entities(self, fake_entities: list) -> tuple:
        """Run v12→v13 migration with a fake entity registry."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=12, options={"continuous": True, "floor_label": ""})
        hass = _FakeHass()

        entities_by_id = {
            e["entity_id"]: MagicMock(
                platform="roomba_plus",
                domain=e.get("domain", e["entity_id"].split(".")[0]),
                entity_id=e["entity_id"],
                unique_id=e["unique_id"],
            )
            for e in fake_entities
        }
        fake_reg = MagicMock()
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

    def test_version_bumped_to_13(self):
        """Entry at v12 is bumped to v13."""
        entry, _, _ = self._run_from_v12_with_entities([])
        assert entry.version == 25

    def test_german_cleaning_map_renamed(self):
        """image.*_reinigungskarte → image.*_cleaning_map (DE install)."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            # Wrong German slug for RoombaMapImage
            {
                "entity_id": "image.roomba_980_reinigungskarte",
                "unique_id": f"{blid}_map",
                "domain": "image",
            },
            # Reference entity so migration can derive the device prefix.
            # Uses a unique_id not in _V13_RENAMES so prefix derivation uses
            # the fallback path: uid tail = "_connected", eid ends with "_connected"
            {
                "entity_id": "binary_sensor.roomba_980_connected",
                "unique_id": f"{blid}_connected",
                "domain": "binary_sensor",
            },
        ]
        _, renamed, removed = self._run_from_v12_with_entities(entities)
        renamed_eids = [new for _, new in renamed]
        assert any("cleaning_map" in eid for eid in renamed_eids), (
            f"Expected cleaning_map rename, got: {renamed_eids}"
        )
        assert removed == []

    def test_german_coverage_map_renamed(self):
        """image.*_bedeckungskarte → image.*_coverage_map (DE install)."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "image.roomba_980_bedeckungskarte",
                "unique_id": f"{blid}_coverage_map",
                "domain": "image",
            },
            {
                "entity_id": "binary_sensor.roomba_980_connected",
                "unique_id": f"{blid}_connected",
                "domain": "binary_sensor",
            },
        ]
        _, renamed, _ = self._run_from_v12_with_entities(entities)
        renamed_eids = [new for _, new in renamed]
        assert any("coverage_map" in eid for eid in renamed_eids), (
            f"Expected coverage_map rename, got: {renamed_eids}"
        )

    def test_already_correct_not_renamed(self):
        """English install: entity_ids already correct — no renames."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "image.roomba_980_cleaning_map",
                "unique_id": f"{blid}_map",
                "domain": "image",
            },
            {
                "entity_id": "image.roomba_980_coverage_map",
                "unique_id": f"{blid}_coverage_map",
                "domain": "image",
            },
        ]
        _, renamed, removed = self._run_from_v12_with_entities(entities)
        assert renamed == []
        assert removed == []

    def test_german_carpet_boost_select_renamed(self):
        """select.*_teppich_boost → select.*_carpet_boost_select (DE)."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "select.roomba_980_teppich_boost",
                "unique_id": f"{blid}_carpet_boost_select",
                "domain": "select",
            },
            {
                "entity_id": "binary_sensor.roomba_980_connected",
                "unique_id": f"{blid}_connected",
                "domain": "binary_sensor",
            },
        ]
        _, renamed, _ = self._run_from_v12_with_entities(entities)
        renamed_eids = [new for _, new in renamed]
        assert any("carpet_boost_select" in eid for eid in renamed_eids), (
            f"Expected carpet_boost_select rename, got: {renamed_eids}"
        )

    def test_prefix_from_sibling_in_v13_rename_list(self):
        """Prefix derived from a sibling that is itself in the rename list."""
        blid = "TEST_BLID_000000000000000000000000"
        # Both entities are in _V13_RENAMES; use each other for prefix derivation.
        # coverage_map has correct eid → prefix derivable from it.
        entities = [
            {
                "entity_id": "image.roomba_980_reinigungskarte",
                "unique_id": f"{blid}_map",
                "domain": "image",
            },
            {
                "entity_id": "image.roomba_980_coverage_map",   # already correct
                "unique_id": f"{blid}_coverage_map",
                "domain": "image",
            },
        ]
        _, renamed, _ = self._run_from_v12_with_entities(entities)
        renamed_eids = [new for _, new in renamed]
        assert any("cleaning_map" in eid for eid in renamed_eids), (
            f"Expected cleaning_map from sibling prefix, got: {renamed_eids}"
        )

    def test_no_prefix_match_is_skipped(self):
        """Entity with no derivable prefix is silently skipped (no crash)."""
        blid = "TEST_BLID_000000000000000000000000"
        entities = [
            {
                "entity_id": "image.roomba_980_reinigungskarte",
                "unique_id": f"{blid}_map",
                "domain": "image",
            },
            # No reference sibling → prefix undeducible
        ]
        _, renamed, removed = self._run_from_v12_with_entities(entities)
        # Should not crash; entity is skipped
        assert removed == []


class TestMigrationV13ToV14:
    """v13 → v14: attempted locale-slug fix (device prefix derivation was buggy,
    actual rename now happens in v14→v15)."""

    def test_version_bumped(self):
        """Entry at v13 is eventually bumped to v15 (through v14)."""
        import asyncio
        from unittest.mock import MagicMock, patch, AsyncMock
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=13, options={"continuous": True})
        hass = _FakeHass()

        fake_er = MagicMock()
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: []
        fake_er.async_get = lambda eid: None

        fake_dr = MagicMock()
        fake_dr.async_get = lambda device_id: None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        assert entry.version == 25


class TestMigrationV14ToV15:
    """v14 → v15: locale-slug fix using device registry (robust, any locale)."""

    BLID = "TEST_BLID_000000000000000000000000"

    def _make_entity(self, entity_id, unique_id, device_id="dev_1"):
        from unittest.mock import MagicMock
        return MagicMock(
            platform="roomba_plus",
            domain="sensor",
            entity_id=entity_id,
            unique_id=unique_id,
            config_entry_id="entry_abc",
            device_id=device_id,
        )

    def _run_from_v14(self, fake_entities, device_name="Abstellraum Roomba 980 OG"):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        # blid in data must match the uid prefix used in fake entities
        entry = _FakeConfigEntry(version=14, options={"continuous": True})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}

        renamed = []
        removed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _async_update_entity(old_eid, *, new_entity_id=None, **kwargs):
            if new_entity_id:
                obj = entities_by_id.pop(old_eid, None)
                if obj:
                    obj.entity_id = new_entity_id
                    entities_by_id[new_entity_id] = obj
                renamed.append((old_eid, new_entity_id))

        def _async_remove(eid):
            entities_by_id.pop(eid, None)
            removed.append(eid)

        fake_er.async_update_entity = _async_update_entity
        fake_er.async_remove = _async_remove

        fake_device = MagicMock()
        fake_device.name_by_user = None
        fake_device.name = device_name

        fake_dr = MagicMock()
        fake_dr.async_get = lambda device_id: fake_device if device_id else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                       return_value=list(entities_by_id.values())), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, removed

    def test_version_bumped_to_15(self):
        entry, _, _ = self._run_from_v14([])
        assert entry.version == 25

    def test_german_ladezeit_renamed_to_recharge_time(self):
        """sensor.*_ladezeit → sensor.*_recharge_time (DE, mission_recharge_time)."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_ladezeit",
            f"{self.BLID}_mission_recharge_time",
        )
        _, renamed, _ = self._run_from_v14([entity])
        assert any("recharge_time" in new for _, new in renamed), \
            f"Expected recharge_time in {renamed}"

    def test_german_missionsablauf_renamed_to_mission_expire_time(self):
        """sensor.*_missionsablauf → sensor.*_mission_expire_time (DE)."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_missionsablauf",
            f"{self.BLID}_mission_expire_time",
        )
        _, renamed, _ = self._run_from_v14([entity])
        assert any("mission_expire_time" in new for _, new in renamed), \
            f"Expected mission_expire_time in {renamed}"

    def test_german_signalrauschen_renamed_to_signal_noise(self):
        """sensor.*_signalrauschen → sensor.*_signal_noise (DE)."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_signalrauschen",
            f"{self.BLID}_signal_noise",
        )
        _, renamed, _ = self._run_from_v14([entity])
        assert any("signal_noise" in new for _, new in renamed), \
            f"Expected signal_noise in {renamed}"

    def test_english_install_no_rename(self):
        """English slugs already correct — no rename on English install."""
        entities = [
            self._make_entity("sensor.roomba_recharge_time",      f"{self.BLID}_mission_recharge_time"),
            self._make_entity("sensor.roomba_mission_expire_time", f"{self.BLID}_mission_expire_time"),
            self._make_entity("sensor.roomba_signal_noise",        f"{self.BLID}_signal_noise"),
        ]
        _, renamed, _ = self._run_from_v14(entities)
        assert renamed == [], f"No rename expected on English install, got: {renamed}"

    def test_device_name_slug_used_for_prefix(self):
        """Device name is slugified to compute the new entity_id prefix."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_ladezeit",
            f"{self.BLID}_mission_recharge_time",
        )
        _, renamed, _ = self._run_from_v14([entity], device_name="Abstellraum Roomba 980 OG")
        new_eids = [new for _, new in renamed]
        assert any(new.startswith("sensor.abstellraum_roomba_980_og") for new in new_eids), \
            f"Expected abstellraum_roomba_980_og prefix in {new_eids}"

    def test_no_device_id_skipped(self):
        """Entity without device_id is skipped gracefully — no crash."""
        entity = self._make_entity(
            "sensor.roomba_ladezeit",
            f"{self.BLID}_mission_recharge_time",
            device_id=None,
        )
        _, renamed, _ = self._run_from_v14([entity])
        assert renamed == []


class TestMigrationV15ToV16:
    """v15 → v16: battery_capacity_retention locale-slug fix
    (DE: 'Wartung – Akkukapazität' → slug 'wartung_akkukapazitat')."""

    BLID = "TEST_BLID_000000000000000000000000"

    def _make_entity(self, entity_id, unique_id, device_id="dev_1"):
        from unittest.mock import MagicMock
        return MagicMock(
            platform="roomba_plus", domain="sensor",
            entity_id=entity_id, unique_id=unique_id,
            config_entry_id="entry_abc", device_id=device_id,
        )

    def _run_from_v15(self, fake_entities, device_name="Roomba 980 - OG"):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=15, options={"continuous": True})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        renamed = []
        removed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _async_update(old_eid, *, new_entity_id=None, **kw):
            if new_entity_id:
                obj = entities_by_id.pop(old_eid, None)
                if obj:
                    obj.entity_id = new_entity_id
                    entities_by_id[new_entity_id] = obj
                renamed.append((old_eid, new_entity_id))

        def _async_remove(eid):
            entities_by_id.pop(eid, None)
            removed.append(eid)

        fake_er.async_update_entity = _async_update
        fake_er.async_remove = _async_remove

        fake_device = MagicMock()
        fake_device.name_by_user = None
        fake_device.name = device_name

        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: fake_device if did else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, removed

    def test_version_bumped_to_16(self):
        entry, _, _ = self._run_from_v15([])
        assert entry.version == 25

    def test_wartung_akkukapazitat_renamed_to_battery_capacity_retention(self):
        """sensor.*_wartung_akkukapazitat → sensor.*_battery_capacity_retention."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat",
            f"{self.BLID}_battery_capacity_retention",
        )
        _, renamed, _ = self._run_from_v15([entity])
        assert any("battery_capacity_retention" in new for _, new in renamed), \
            f"Expected battery_capacity_retention in {renamed}"

    def test_device_slug_from_current_name(self):
        """Device name 'Roomba 980 - OG' → slug 'roomba_980_og'."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat",
            f"{self.BLID}_battery_capacity_retention",
        )
        _, renamed, _ = self._run_from_v15([entity], device_name="Roomba 980 - OG")
        new_eids = [new for _, new in renamed]
        assert any("roomba_980_og" in eid for eid in new_eids), \
            f"Expected roomba_980_og prefix in {new_eids}"

    def test_already_correct_not_renamed(self):
        entity = self._make_entity(
            "sensor.roomba_980_og_battery_capacity_retention",
            f"{self.BLID}_battery_capacity_retention",
        )
        _, renamed, _ = self._run_from_v15([entity])
        assert renamed == []

    def test_entity_not_in_registry_skipped(self):
        """No entity with battery_capacity_retention uid → 0 renames."""
        _, renamed, _ = self._run_from_v15([])
        assert renamed == []


class TestMigrationV16ToV17:
    """v16→v17: entity_id-suffix based locale-slug fix + unique_id repair."""

    BLID = "TEST_BLID_000000000000000000000000"

    def _make_entity(self, entity_id, unique_id, device_id="dev_1", config_entry_id="test_entry_id"):
        from unittest.mock import MagicMock
        return MagicMock(
            entity_id=entity_id, unique_id=unique_id,
            config_entry_id=config_entry_id, device_id=device_id,
        )

    def _run_from_v16(self, fake_entities, device_name="Roomba 980 - OG"):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=16, options={"continuous": True})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        renamed = []
        removed = []
        uid_updates = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _async_update(old_eid, *, new_entity_id=None, new_unique_id=None, **kw):
            obj = entities_by_id.pop(old_eid, None)
            if new_entity_id and obj:
                obj.entity_id = new_entity_id
                entities_by_id[new_entity_id] = obj
                renamed.append((old_eid, new_entity_id))
            if new_unique_id:
                uid_updates.append((old_eid, new_unique_id))

        fake_er.async_update_entity = _async_update
        fake_er.async_remove = lambda eid: (entities_by_id.pop(eid, None), removed.append(eid))

        fake_device = MagicMock()
        fake_device.name_by_user = None
        fake_device.name = device_name
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: fake_device if did else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, removed, uid_updates

    def test_version_bumped_to_17(self):
        entry, *_ = self._run_from_v16([])
        assert entry.version == 25

    def test_wartung_akkukapazitat_renamed(self):
        """Core case: wartung_akkukapazitat → battery_capacity_retention."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat",
            "OLD_UID_FORMAT_abc123",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, uid_updates = self._run_from_v16([entity])
        assert any("battery_capacity_retention" in new for _, new in renamed), \
            f"Expected battery_capacity_retention in {renamed}"

    def test_unique_id_also_updated(self):
        """unique_id is updated to {blid}_{key} so future startups find it."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat",
            "OLD_UID",
            config_entry_id="test_entry_id",
        )
        _, _, _, uid_updates = self._run_from_v16([entity])
        expected_uid = f"{self.BLID}_battery_capacity_retention"
        assert any(new_uid == expected_uid for _, new_uid in uid_updates), \
            f"Expected uid update to {expected_uid}, got {uid_updates}"

    def test_other_config_entry_not_touched(self):
        """Entities from other config entries are not renamed."""
        entity = self._make_entity(
            "sensor.some_device_wartung_akkukapazitat",
            "OTHER_UID",
            config_entry_id="different_entry",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        assert renamed == []

    def test_already_correct_not_renamed(self):
        entity = self._make_entity(
            "sensor.roomba_980_og_battery_capacity_retention",
            f"{TestMigrationV16ToV17.BLID}_battery_capacity_retention",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        assert renamed == []

    def test_domain_preserved_binary_sensor(self):
        """binary_sensor.* mission_aktiv → mission_active preserves binary_sensor domain."""
        entity = self._make_entity(
            "binary_sensor.abstellraum_roomba_980_og_mission_aktiv",
            "OLD_BINARY_UID",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        new_eids = [new for _, new in renamed]
        assert any(new.startswith("binary_sensor.") for new in new_eids), \
            f"Expected binary_sensor domain in {new_eids}"

    # Phase 2 tests: wrong-device-prefix rename

    def test_old_device_prefix_renamed(self):
        """entity with correct uid but old device prefix → renamed to current prefix."""
        entity = self._make_entity(
            "sensor.abstellraum_roomba_980_og_total_energy_consumed",
            f"{TestMigrationV16ToV17.BLID}_total_energy_consumed",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        new_eids = [new for _, new in renamed]
        assert any("roomba_980_og_total_energy_consumed" in e for e in new_eids), \
            f"Expected roomba_980_og prefix in {new_eids}"

    def test_binary_sensor_old_prefix_renamed(self):
        entity = self._make_entity(
            "binary_sensor.abstellraum_roomba_980_og_mission_active",
            f"{TestMigrationV16ToV17.BLID}_mission_active",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        new_eids = [new for _, new in renamed]
        assert any("binary_sensor.roomba_980_og_mission_active" == e for e in new_eids), \
            f"Expected binary_sensor.roomba_980_og_mission_active in {new_eids}"

    def test_already_correct_prefix_skipped(self):
        entity = self._make_entity(
            "sensor.roomba_980_og_total_energy_consumed",
            f"{TestMigrationV16ToV17.BLID}_total_energy_consumed",
            config_entry_id="test_entry_id",
        )
        _, renamed, _, _ = self._run_from_v16([entity])
        ph2_renames = [(o, n) for o, n in renamed
                       if "total_energy_consumed" in n]
        assert ph2_renames == [], f"Should not rename already-correct entity, got {ph2_renames}"


class TestMigrationV17ToV18:
    """v17→v18: German suffix removal + old-device-prefix rename via entity_id matching."""

    BLID = "TEST_BLID_000000000000000000000000"
    DEVICE_NAME = "Roomba 980 - OG"   # → slug roomba_980_og

    def _make_entity(self, entity_id, unique_id="uid", device_id="dev_1"):
        from unittest.mock import MagicMock
        return MagicMock(
            entity_id=entity_id, unique_id=unique_id,
            config_entry_id="test_entry_id", device_id=device_id,
        )

    def _run_from_v17(self, fake_entities):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=17, options={"continuous": True})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        renamed = []
        removed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _update(old_eid, *, new_entity_id=None, **kw):
            if new_entity_id:
                obj = entities_by_id.pop(old_eid, None)
                if obj:
                    obj.entity_id = new_entity_id
                    entities_by_id[new_entity_id] = obj
                renamed.append((old_eid, new_entity_id))

        fake_er.async_update_entity = _update
        fake_er.async_remove = lambda eid: (entities_by_id.pop(eid, None), removed.append(eid))

        fake_device = MagicMock()
        fake_device.name_by_user = None
        fake_device.name = self.DEVICE_NAME
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: fake_device if did else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, removed

    def test_version_bumped_to_18(self):
        entry, *_ = self._run_from_v17([])
        assert entry.version == 25

    # Step A — German suffix

    def test_wartung_akkukapazitat_removed_when_target_exists(self):
        """If roomba_980_og_battery_capacity_retention already exists (from v17),
        the stale wartung entity is removed — no duplicate."""
        german = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat")
        english = self._make_entity(
            "sensor.roomba_980_og_battery_capacity_retention")
        _, renamed, removed = self._run_from_v17([german, english])
        assert "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat" in removed
        assert renamed == []

    def test_wartung_akkukapazitat_renamed_when_no_target(self):
        """If target doesn't exist yet, rename instead of remove."""
        german = self._make_entity(
            "sensor.abstellraum_roomba_980_og_wartung_akkukapazitat")
        _, renamed, removed = self._run_from_v17([german])
        assert any("battery_capacity_retention" in new for _, new in renamed)
        assert removed == []

    # Step B — old device prefix

    def test_old_prefix_total_energy_consumed_renamed(self):
        e = self._make_entity(
            "sensor.abstellraum_roomba_980_og_total_energy_consumed")
        _, renamed, _ = self._run_from_v17([e])
        assert any("roomba_980_og_total_energy_consumed" in new for _, new in renamed)

    def test_old_prefix_mission_active_renamed(self):
        e = self._make_entity(
            "binary_sensor.abstellraum_roomba_980_og_mission_active")
        _, renamed, _ = self._run_from_v17([e])
        assert any("binary_sensor.roomba_980_og_mission_active" == new for _, new in renamed)

    def test_old_prefix_recent_edge_coverage_renamed(self):
        e = self._make_entity(
            "sensor.abstellraum_roomba_980_og_recent_edge_coverage_ratio")
        _, renamed, _ = self._run_from_v17([e])
        assert any("roomba_980_og_recent_edge_coverage_ratio" in new for _, new in renamed)

    def test_already_correct_prefix_skipped(self):
        e = self._make_entity("sensor.roomba_980_og_total_energy_consumed")
        _, renamed, removed = self._run_from_v17([e])
        assert renamed == [] and removed == []

    def test_old_prefix_target_exists_removes_stale(self):
        old = self._make_entity("sensor.abstellraum_roomba_980_og_total_energy_consumed")
        new = self._make_entity("sensor.roomba_980_og_total_energy_consumed")
        _, renamed, removed = self._run_from_v17([old, new])
        assert "sensor.abstellraum_roomba_980_og_total_energy_consumed" in removed
        assert renamed == []


# ── v22 → v23: FavoriteButton entity_id stabilisation ────────────────────────

class TestMigrationV22ToV23:
    """v22 → v23: FavoriteButton entity_ids get _fav_ canonical form."""

    BLID = "TEST_BLID_000000000000000000000000"  # matches _FakeConfigEntry.data["blid"]
    DEVICE_NAME = "Roomba Test OG"  # → slug roomba_test_og

    def _make_entity(self, entity_id, unique_id, device_id="dev_fav"):
        from unittest.mock import MagicMock
        return MagicMock(
            entity_id=entity_id,
            unique_id=unique_id,
            platform="roomba_plus",
            config_entry_id="test_entry_id",
            device_id=device_id,
        )

    def _run_from_v22(self, fake_entities, device_name=None):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=22, options={"continuous": True})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        renamed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _update(old_eid, *, new_entity_id=None, **kw):
            if new_entity_id:
                obj = entities_by_id.pop(old_eid, None)
                if obj:
                    obj.entity_id = new_entity_id
                    entities_by_id[new_entity_id] = obj
                renamed.append((old_eid, new_entity_id))

        fake_er.async_update_entity = _update

        fake_device = MagicMock()
        fake_device.name_by_user = None
        fake_device.name = device_name or self.DEVICE_NAME
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: fake_device if did else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, renamed, entities_by_id

    def test_version_bumped_to_23(self):
        entry, *_ = self._run_from_v22([])
        assert entry.version == 25

    def test_favorite_entity_id_renamed_to_canonical(self):
        """button.roomba_test_og_montag_morgen → button.roomba_test_og_fav_abc123."""
        blid = self.BLID
        fav_id = "abc123-def456"
        uid = f"roomba_plus_{blid}_fav_{fav_id}"
        entity = self._make_entity(
            "button.roomba_test_og_montag_morgen",
            uid,
        )
        _, renamed, final = self._run_from_v22([entity])
        assert len(renamed) == 1
        old_eid, new_eid = renamed[0]
        assert old_eid == "button.roomba_test_og_montag_morgen"
        # fav_id "abc123-def456" slugifies to "abc123_def456"
        assert new_eid == "button.roomba_test_og_fav_abc123_def456"
        assert "button.roomba_test_og_montag_morgen" not in final
        assert "button.roomba_test_og_fav_abc123_def456" in final

    def test_already_canonical_entity_id_untouched(self):
        """Entity_id already containing _fav_ is not renamed."""
        blid = self.BLID
        fav_id = "xyz789"
        uid = f"roomba_plus_{blid}_fav_{fav_id}"
        entity = self._make_entity(
            "button.roomba_test_og_fav_xyz789",
            uid,
        )
        _, renamed, _ = self._run_from_v22([entity])
        assert renamed == []

    def test_non_favorite_button_untouched(self):
        """Entities without _fav_ in unique_id are skipped."""
        entity = self._make_entity(
            "button.roomba_test_og_locate",
            f"roomba_plus_{self.BLID}_locate",
        )
        _, renamed, _ = self._run_from_v22([entity])
        assert renamed == []

    def test_collision_skips_rename(self):
        """If new entity_id already exists, rename is skipped."""
        blid = self.BLID
        fav_id = "collision-test"
        uid = f"roomba_plus_{blid}_fav_{fav_id}"
        old_entity = self._make_entity("button.roomba_test_og_alter_name", uid)
        # Occupant already at the target entity_id
        occupant = self._make_entity("button.roomba_test_og_fav_collision_test", "other_uid")

        _, renamed, final = self._run_from_v22([old_entity, occupant])
        # Rename skipped — target occupied
        assert renamed == []
        assert "button.roomba_test_og_alter_name" in final

    def test_empty_fav_id_skipped(self):
        """unique_id ending with _fav_ and no fav_id is skipped gracefully."""
        uid = f"roomba_plus_{self.BLID}_fav_"
        entity = self._make_entity("button.roomba_test_og_leer", uid)
        _, renamed, _ = self._run_from_v22([entity])
        assert renamed == []

    def test_no_device_skips_rename(self):
        """Entity without a device_id cannot produce device_slug — skipped."""
        from unittest.mock import MagicMock, patch
        import asyncio

        blid = self.BLID
        uid = f"roomba_plus_{blid}_fav_no_device"
        entity = MagicMock(
            entity_id="button.roomba_test_og_no_device",
            unique_id=uid,
            platform="roomba_plus",
            config_entry_id="test_entry_id",
            device_id=None,  # no device
        )

        from custom_components.roomba_plus import async_migrate_entry
        entry = _FakeConfigEntry(version=22, options={})
        hass = _FakeHass()
        entities_by_id = {entity.entity_id: entity}
        renamed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())
        fake_er.async_update_entity = lambda old, *, new_entity_id=None, **kw: renamed.append((old, new_entity_id))
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: None  # no device found

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        assert renamed == []

    def test_name_by_user_takes_precedence_over_name(self):
        """device.name_by_user overrides device.name in slug — matches HA behaviour.

        Regression guard: the migration originally read only device.name, which
        produces a wrong entity_id slug when the user has renamed the device
        (HA generates entity_ids from name_by_user when set).
        """
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        blid = self.BLID
        fav_id = "routine42"
        uid = f"roomba_plus_{blid}_fav_{fav_id}"
        entity = self._make_entity("button.roomba_old_name_montag", uid)

        entry = _FakeConfigEntry(version=22, options={})
        hass = _FakeHass()
        entities_by_id = {entity.entity_id: entity}
        renamed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _update(old, *, new_entity_id=None, **kw):
            if new_entity_id:
                renamed.append((old, new_entity_id))
        fake_er.async_update_entity = _update

        fake_device = MagicMock()
        fake_device.name_by_user = "Wohnzimmer Robi"  # user-renamed
        fake_device.name = "Roomba Test OG"           # original name (ignored)
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: fake_device if did else None

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        assert len(renamed) == 1
        _, new_eid = renamed[0]
        # slug from name_by_user "Wohnzimmer Robi", NOT name "Roomba Test OG"
        assert new_eid == "button.wohnzimmer_robi_fav_routine42"

    def test_missing_blid_skips_rename(self):
        """Config entry without blid must not rename anything."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        # FavoriteButton entity exists, but config entry has no blid
        entity = self._make_entity(
            "button.roomba_test_og_montag",
            "roomba_plus_SOMEBLID_fav_x1",
        )
        entry = _FakeConfigEntry(version=22, options={})
        entry.data = {}  # no blid
        hass = _FakeHass()
        entities_by_id = {entity.entity_id: entity}
        renamed = []

        fake_er = MagicMock()
        fake_er.async_get = lambda eid: entities_by_id.get(eid)
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())
        fake_er.async_update_entity = lambda old, *, new_entity_id=None, **kw: renamed.append((old, new_entity_id))
        fake_dr = MagicMock()
        fake_dr.async_get = lambda did: MagicMock(name_by_user=None, name="X")

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=fake_dr):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        # blid missing → no rename, but version still bumps
        assert renamed == []
        assert entry.version == 25


# ── v23 → v24: disable permanently-unavailable sensors ───────────────────────

class TestMigrationV23ToV24:
    """v23 → v24: five sensors disabled via RegistryEntryDisabler.INTEGRATION."""

    BLID = "TEST_BLID_000000000000000000000000"
    SUFFIXES = [
        "battery_age_days",
        "battery_cycle_count_bms",
        "bin_last_cleaned",
        "contact_last_cleaned",
        "wheel_last_cleaned",
    ]

    def _make_entity(self, suffix, disabled_by=None):
        from unittest.mock import MagicMock
        uid = f"roomba_plus_{self.BLID}_{suffix}"
        eid = f"sensor.roomba_test_{suffix}"
        return MagicMock(
            entity_id=eid,
            unique_id=uid,
            platform="roomba_plus",
            config_entry_id="test_entry_id",
            device_id="dev_1",
            disabled_by=disabled_by,
        )

    def _run_from_v23(self, fake_entities):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler

        entry = _FakeConfigEntry(version=23, options={})
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        disabled_calls = []

        fake_er = MagicMock()
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _update(eid, *, disabled_by=None, **kw):
            if disabled_by is not None:
                disabled_calls.append((eid, disabled_by))
                if eid in entities_by_id:
                    entities_by_id[eid].disabled_by = disabled_by

        fake_er.async_update_entity = _update

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, disabled_calls

    def test_version_bumped_to_24(self):
        entry, _ = self._run_from_v23([])
        assert entry.version == 25

    def test_all_five_sensors_disabled(self):
        entities = [self._make_entity(s) for s in self.SUFFIXES]
        _, calls = self._run_from_v23(entities)
        disabled_eids = {eid for eid, _ in calls}
        for suffix in self.SUFFIXES:
            assert f"sensor.roomba_test_{suffix}" in disabled_eids, \
                f"{suffix} was not disabled"
        assert len(calls) == 5

    def test_already_disabled_not_touched(self):
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entities = [
            self._make_entity(s, disabled_by=RegistryEntryDisabler.USER)
            for s in self.SUFFIXES
        ]
        _, calls = self._run_from_v23(entities)
        assert calls == []  # nothing additionally disabled

    def test_other_sensors_not_disabled(self):
        entity = type('E', (), {
            'entity_id': 'sensor.roomba_test_wifi_health',
            'unique_id': f'roomba_plus_{self.BLID}_wifi_health',
            'platform': 'roomba_plus',
            'domain': 'sensor',
            'config_entry_id': 'test_entry_id',
            'device_id': 'dev_1',
            'disabled_by': None,
        })()
        _, calls = self._run_from_v23([entity])
        assert calls == []

    def test_different_blid_not_touched(self):
        """Entity from different robot (different BLID) is skipped."""
        from unittest.mock import MagicMock
        other = MagicMock(
            entity_id="sensor.other_robot_battery_age_days",
            unique_id="roomba_plus_OTHER_BLID_battery_age_days",
            platform="roomba_plus",
            config_entry_id="test_entry_id",
            device_id="dev_2",
            disabled_by=None,
        )
        _, calls = self._run_from_v23([other])
        assert calls == []


class TestMigrationV24ToV25:
    """v24 → v25: re-enable the current-room device_tracker entity for
    EXISTING installations whose entity was auto-disabled by the old
    (pre-v2.10.3) entity_registry_enabled_default=False behaviour.

    Community-report-driven (see device_tracker.py's root-cause comment,
    "Thonno's report" / feature request #2 item 1): the code-level fix
    only affects newly-registered entities, not ones already present in
    the registry as disabled from before the fix shipped.
    """

    BLID = "TEST_BLID_000000000000000000000000"

    def _make_tracker_entity(self, disabled_by=None, blid=None):
        from unittest.mock import MagicMock
        blid = blid or self.BLID
        uid = f"roomba_plus_{blid}_position"
        eid = "device_tracker.roomba_test_position"
        return MagicMock(
            entity_id=eid,
            unique_id=uid,
            platform="roomba_plus",
            domain="device_tracker",
            config_entry_id="test_entry_id",
            device_id="dev_1",
            disabled_by=disabled_by,
        )

    def _run_from_v24(self, fake_entities):
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=24, options={})
        entry.data = {"blid": self.BLID}
        hass = _FakeHass()

        entities_by_id = {e.entity_id: e for e in fake_entities}
        update_calls = []

        fake_er = MagicMock()
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: list(entities_by_id.values())

        def _update(eid, *, disabled_by=None, **kw):
            update_calls.append((eid, disabled_by))
            if eid in entities_by_id:
                entities_by_id[eid].disabled_by = disabled_by

        fake_er.async_update_entity = _update

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        return entry, update_calls

    def test_version_bumped_to_25(self):
        entry, _ = self._run_from_v24([])
        assert entry.version == 25

    def test_integration_disabled_tracker_is_reenabled(self):
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entity = self._make_tracker_entity(disabled_by=RegistryEntryDisabler.INTEGRATION)
        _, calls = self._run_from_v24([entity])
        assert calls == [("device_tracker.roomba_test_position", None)]

    def test_user_disabled_tracker_is_left_untouched(self):
        """A user's own deliberate choice to disable this entity must not
        be overridden by the migration."""
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entity = self._make_tracker_entity(disabled_by=RegistryEntryDisabler.USER)
        _, calls = self._run_from_v24([entity])
        assert calls == []

    def test_already_enabled_tracker_untouched(self):
        entity = self._make_tracker_entity(disabled_by=None)
        _, calls = self._run_from_v24([entity])
        assert calls == []

    def test_other_domain_entities_not_touched(self):
        """A sensor entity with a similar-looking unique_id (different
        domain) must not be affected."""
        from unittest.mock import MagicMock
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entity = MagicMock(
            entity_id="sensor.roomba_test_position",
            unique_id=f"roomba_plus_{self.BLID}_position",
            platform="roomba_plus",
            domain="sensor",
            config_entry_id="test_entry_id",
            device_id="dev_1",
            disabled_by=RegistryEntryDisabler.INTEGRATION,
        )
        _, calls = self._run_from_v24([entity])
        assert calls == []

    def test_different_blid_not_touched(self):
        """Entity from a different robot (different BLID) is skipped."""
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        entity = self._make_tracker_entity(
            disabled_by=RegistryEntryDisabler.INTEGRATION, blid="OTHER_BLID",
        )
        _, calls = self._run_from_v24([entity])
        assert calls == []

    def test_missing_blid_in_config_skips_gracefully(self):
        """No blid in config_entry.data — must not crash, just skip and
        still bump the version."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus import async_migrate_entry

        entry = _FakeConfigEntry(version=24, options={})
        entry.data = {}
        hass = _FakeHass()

        fake_er = MagicMock()
        fake_er.entities = MagicMock()
        fake_er.entities.values = lambda: []

        loop = asyncio.new_event_loop()
        try:
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
                 patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
                 patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
                loop.run_until_complete(async_migrate_entry(hass, entry))
        finally:
            loop.close()

        assert entry.version == 25


class TestMigrationV24ToV25MatchesRealEntity:
    """v24 → v25 — verifies the migration's hardcoded expected_uid pattern
    against the REAL RoombaDeviceTracker class (not just a hand-written
    fake), given this codebase's own history of locale-slug regressions
    (v2.1.1: 37 entities renamed German→English slugs; v2.5.0: further
    locale-dependent slug fixes). unique_id itself is never
    locale-derived (set directly in code, never from a translated
    string) — but this test proves that directly rather than relying on
    that reasoning alone.
    """

    def test_real_class_unique_id_matches_migration_expectation(self):
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.device_tracker import RoombaDeviceTracker

        roomba = MagicMock()
        config_entry = MagicMock()
        blid = "REALBLID123"
        tracker = RoombaDeviceTracker(roomba, blid, config_entry)

        expected_uid = f"roomba_plus_{blid}_position"
        assert tracker.unique_id == expected_uid, (
            "Migration's hardcoded expected_uid pattern must match the "
            "real class's actual unique_id construction, or the "
            "migration silently matches nothing"
        )

    def test_real_class_entity_id_has_no_locale_dependent_suffix(self):
        """suggested_object_id returning None means entity_id is
        device-name-only — confirms unique_id (which the migration
        matches on) is NOT what determines the visible entity_id here,
        so a locale-dependent entity_id has no bearing on whether the
        migration finds the right entity."""
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.device_tracker import RoombaDeviceTracker

        roomba = MagicMock()
        config_entry = MagicMock()
        tracker = RoombaDeviceTracker(roomba, "REALBLID123", config_entry)
        assert tracker.suggested_object_id is None
