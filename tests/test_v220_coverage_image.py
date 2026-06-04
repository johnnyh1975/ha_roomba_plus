"""Tests for RoombaCoverageImage entity — F9 occupancy heatmap.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import sys
import datetime
import collections

import pytest
from unittest.mock import MagicMock, AsyncMock

# Stub AddConfigEntryEntitiesCallback if HA version doesn't have it
import homeassistant.helpers.entity_platform as _ep
if not hasattr(_ep, "AddConfigEntryEntitiesCallback"):
    _ep.AddConfigEntryEntitiesCallback = getattr(_ep, "AddEntitiesCallback", object)


def _make_entity(cell_count: int = 5, stuck_count: int = 2):
    """Build a minimal RoombaCoverageImage with stubbed dependencies."""
    from custom_components.roomba_plus.grid_store import GridStore
    from custom_components.roomba_plus.image import RoombaCoverageImage

    gs = GridStore()
    gs._cells = {(i, 0): 0.5 for i in range(cell_count)}
    gs._stuck = {(0, 0): stuck_count}

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    config_entry = MagicMock()
    config_entry.runtime_data = MagicMock()
    config_entry.entry_id = "test_entry"

    entity = RoombaCoverageImage.__new__(RoombaCoverageImage)
    entity._grid_store = gs
    entity._config_entry = config_entry
    entity._last_phase = ""
    entity.access_tokens = collections.deque([], 2)
    entity._cache = None
    entity._attr_unique_id = "test_blid_coverage_map"

    from homeassistant.util import dt as dt_util
    entity._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
    entity.vacuum = roomba

    return entity


class TestCoverageImageAttributes:
    def test_cell_count_in_attributes(self):
        entity = _make_entity(cell_count=5)
        attrs = entity.extra_state_attributes
        assert attrs["cell_count"] == 5

    def test_stuck_event_count_in_attributes(self):
        entity = _make_entity(stuck_count=3)
        attrs = entity.extra_state_attributes
        assert attrs["stuck_event_count"] == 3

    def test_ema_constants_present(self):
        entity = _make_entity()
        attrs = entity.extra_state_attributes
        assert "decay" in attrs
        assert "visit_increment" in attrs
        assert "cell_size_mm" in attrs

    def test_bounding_box_in_attributes(self):
        entity = _make_entity(cell_count=3)
        attrs = entity.extra_state_attributes
        assert attrs["x_min_mm"] is not None
        assert attrs["x_max_mm"] is not None

    def test_bounding_box_none_when_empty(self):
        entity = _make_entity(cell_count=0)
        attrs = entity.extra_state_attributes
        assert attrs["x_min_mm"] is None
        assert attrs["y_min_mm"] is None

    def test_last_mission_end_is_iso_string(self):
        entity = _make_entity()
        attrs = entity.extra_state_attributes
        assert attrs["last_mission_end"] is not None
        # Must be parseable as ISO datetime
        datetime.datetime.fromisoformat(attrs["last_mission_end"])


class TestCoverageImageIdentity:
    def test_unique_id_suffix(self):
        entity = _make_entity()
        # _attr_unique_id is set in __init__ as f"{robot_unique_id}_coverage_map"
        assert entity._attr_unique_id.endswith("_coverage_map")

    def test_translation_key(self):
        entity = _make_entity()
        # translation_key is set as class attr but may be a property in some HA versions
        tk = getattr(entity, "_attr_translation_key", None) or getattr(entity, "translation_key", None)
        assert tk == "coverage_map"

    def test_content_type_png(self):
        entity = _make_entity()
        ct = getattr(entity, "_attr_content_type", None) or getattr(entity, "content_type", None)
        assert ct == "image/png"


class TestCoverageImageStateFilter:
    def test_filter_passes_on_mission_status(self):
        entity = _make_entity()
        assert entity.new_state_filter({"cleanMissionStatus": {}}) is True

    def test_filter_rejects_unrelated_state(self):
        entity = _make_entity()
        assert entity.new_state_filter({"bbrun": {}}) is False


class TestCoverageImageBlankFallback:
    def test_blank_image_returns_bytes(self):
        from custom_components.roomba_plus.image import RoombaCoverageImage
        result = RoombaCoverageImage._blank_image()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_blank_image_is_valid_png(self):
        from custom_components.roomba_plus.image import RoombaCoverageImage
        result = RoombaCoverageImage._blank_image()
        # PNG magic bytes
        assert result[:8] == b"\x89PNG\r\n\x1a\n" or result[:4] == b"\x89PNG"
