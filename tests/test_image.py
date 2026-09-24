"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import datetime
import collections
import pytest

from tests.conftest import robot_mock, hass_mock, entry_mock
from unittest.mock import MagicMock
from custom_components.roomba_plus.entity import IRobotEntity
from unittest.mock import AsyncMock
import homeassistant.helpers.entity_platform as _ep
import math
from unittest.mock import patch
from custom_components.roomba_plus.models import MapCapability
from custom_components.roomba_plus.umf_aligner import UmfAligner
import asyncio
import inspect
from unittest.mock import call
import tests.conftest
from custom_components.roomba_plus.callbacks import make_mission_callback
from custom_components.roomba_plus.callbacks import make_mission_complete_callback
from custom_components.roomba_plus.const import CLEANING_PHASES
from custom_components.roomba_plus.const import MISSION_END_PHASES
import time
from typing import Any
from types import SimpleNamespace
from custom_components.roomba_plus import image as img
from custom_components.roomba_plus.const import GAP_THRESHOLD_MM
from custom_components.roomba_plus.const import MAX_DOOR_WIDTH_MM
from custom_components.roomba_plus.const import MIN_DOOR_WIDTH_MM
from roombapy_prime.models.livemap import MapUpdateMessage
from roombapy_prime.models.livemap import PositionUpdateMessage
import io
from PIL import Image
from custom_components.roomba_plus.prime_room_map import PrimeFloorPlan
from custom_components.roomba_plus.grid_store import GridStore
from custom_components.roomba_plus.models import ConnectionType
from custom_components.roomba_plus import prime_room_map
from custom_components.roomba_plus import room_cleaning
import json
from pathlib import Path
from custom_components.roomba_plus.image import RoombaMapImage
from custom_components.roomba_plus.mission_map import MissionMapMismatch
from custom_components.roomba_plus.mission_map import MissionMapUnavailable


def _make_entity(cell_count: int = 5, stuck_count: int = 2):
    """Build a minimal RoombaCoverageImage with stubbed dependencies."""
    from custom_components.roomba_plus.grid_store import GridStore
    from custom_components.roomba_plus.image import RoombaCoverageImage

    gs = GridStore()
    gs._cells = {(i, 0): 0.5 for i in range(cell_count)}
    gs._stuck = {(0, 0): {"count": stuck_count, "times": []}}

    roomba = robot_mock()
    roomba.master_state = {"state": {"reported": {}}}
    config_entry = entry_mock()
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


def _make_aligner(aligned: bool = True, confidence: float = 0.85) -> UmfAligner:
    """Return a minimal UmfAligner with controlled aligned/confidence state."""
    a = UmfAligner([], [], MagicMock())
    a._aligned    = aligned
    a._confidence = confidence
    a._transform  = (0.0, 0.0, 0.0)
    a.pmap_version_id = "v1"
    return a


def _make_runtime_data(
    *,
    aligner: UmfAligner | None = None,
    has_cloud: bool = True,
    regions: list | None = None,
    keepout_zones: list | None = None,
    mission_store=None,
    grid_store=None,
    map_capability=None,
    geometry_store=None,
):
    data = MagicMock()
    data.umf_aligner    = aligner
    data.has_cloud      = has_cloud
    data.mission_store  = mission_store
    data.grid_store     = grid_store
    data.geometry_store = geometry_store

    cc = MagicMock()
    cc.regions      = regions or []
    cc.keepout_zones = keepout_zones or []
    cc.observed_zone_centroids = []
    cc.last_update_success = True
    data.cloud_coordinator = cc if has_cloud else None

    if map_capability is not None:
        data.map_capability = map_capability

    return data


def _msg(phase: str, nstuck: int = 0, sqft: int = 100) -> dict:
    """Build a minimal MQTT reported-state message for a given phase."""
    return {
        "state": {
            "reported": {
                "cleanMissionStatus": {
                    "phase": phase,
                    "sqft": sqft,
                    "mssnStrtTm": 1700000000,
                    "initiator": "schedule",
                    "error": 0,
                },
                "bbrun": {"nStuck": nstuck, "hr": 10},
            }
        }
    }


def _make_callback_env():
    """Return (hass, entry, recorded_missions) for make_mission_callback tests."""
    hass = hass_mock()
    hass.loop = asyncio.get_event_loop()
    hass.is_running = True

    mission_store = MagicMock()
    mission_store.async_append = AsyncMock()
    mission_store.async_save = AsyncMock()
    mission_store.consecutive_skips = 0

    runtime_data = MagicMock()
    runtime_data.mission_store = mission_store
    runtime_data.maintenance_store = None
    runtime_data.demand_triggered_ts = None
    runtime_data.mission_timer_store = None
    runtime_data.presence_manager = None
    runtime_data.zone_store = None
    runtime_data.map_capability = MagicMock()

    entry = entry_mock()
    entry.runtime_data = runtime_data
    entry.entry_id = "test_entry"
    entry.data = {"blid": "TESTBLID"}

    recorded: list[dict] = []

    async def _capture_append(record):
        recorded.append(record)

    mission_store.async_append.side_effect = _capture_append

    return hass, entry, recorded, mission_store


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
        tk = (getattr(entity, "_attr_translation_key", None)
           or getattr(entity, "translation_key", None)
           or getattr(getattr(entity, "entity_description", None), "translation_key", None))
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


class TestRoombaMapImageAttrs:
    def _entity(self, aligner=None, renderer=None):
        from custom_components.roomba_plus.image import RoombaMapImage
        entity = object.__new__(RoombaMapImage)
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._renderer = renderer
        return entity

    def test_no_config_entry(self):
        from custom_components.roomba_plus.image import RoombaMapImage
        entity = object.__new__(RoombaMapImage)
        entity._config_entry = None
        entity._renderer = MagicMock()
        assert entity.extra_state_attributes == {}

    def test_no_renderer(self):
        entity = self._entity(aligner=_make_aligner(), renderer=None)
        assert entity.extra_state_attributes == {}

    def test_no_aligner(self):
        entity = self._entity(aligner=None, renderer=MagicMock())
        assert entity.extra_state_attributes == {}

    def test_aligner_not_aligned(self):
        entity = self._entity(aligner=_make_aligner(aligned=False), renderer=MagicMock())
        assert entity.extra_state_attributes == {}

    def test_aligned_empty_polygons(self):
        aligner = _make_aligner()
        aligner._room_polygons = {}
        entity = self._entity(aligner=aligner, renderer=MagicMock())
        # calibration needs polygons; rooms dict is empty → both absent
        attrs = entity.extra_state_attributes
        assert "rooms" not in attrs

    def test_aligned_with_polygons(self):
        aligner = _make_aligner()
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        renderer = MagicMock()
        renderer._mm_to_px_fit.side_effect = lambda x, y: (int(x), int(y))
        entity = self._entity(aligner=aligner, renderer=renderer)
        # Mock cloud_coordinator.regions for icon lookup
        entity._config_entry.runtime_data.cloud_coordinator.regions = [
            {"id": "r1", "region_type": "kitchen"}
        ]
        attrs = entity.extra_state_attributes
        assert "rooms" in attrs
        rooms = attrs["rooms"]
        # XVMC (v2.7.0): rooms is a dict keyed by display name
        assert isinstance(rooms, dict)
        assert "Kitchen" in rooms
        room = rooms["Kitchen"]
        assert room["name"] == "Kitchen"
        assert isinstance(room["outline"], list)
        assert isinstance(room["outline"][0], list)  # [x, y] arrays not {x, y} dicts
        assert "icon" in room
        assert "x" in room and "y" in room
        # XVMC couples on the display name (room_id slug), NOT region_id —
        # names survive map retraining, region_ids do not. region_id is
        # deliberately absent from the rooms attribute (see docs/xiaomi-
        # vacuum-map-card.md): clean_room takes room_name, and the field
        # report asking "where is region_id" reflects that design, not a bug.
        assert room["room_id"] == "kitchen"
        assert "region_id" not in room
        # calibration_points key (renamed from "calibration" for XVMC compat)
        assert "calibration_points" in attrs
        assert "calibration" not in attrs


class TestRoombaRoomsImage:
    def _entity(self, aligner=None):
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._last_x_min = 0.0
        entity._last_x_max = 5000.0
        entity._last_y_min = 0.0
        entity._last_y_max = 5000.0
        entity._last_size  = 600
        entity._room_render_cache_key = None
        entity._room_render_cache = None
        return entity

    def test_no_aligner_returns_blank(self):
        entity = self._entity(aligner=None)
        png = entity._render_rooms_png()
        assert isinstance(png, bytes)
        assert len(png) > 0

    def test_not_aligned_returns_blank(self):
        entity = self._entity(aligner=_make_aligner(aligned=False))
        png = entity._render_rooms_png()
        assert isinstance(png, bytes)

    def test_no_aligner_attrs_empty(self):
        entity = self._entity(aligner=None)
        assert entity.extra_state_attributes == {}

    def test_not_aligned_attrs_empty(self):
        entity = self._entity(aligner=_make_aligner(aligned=False))
        assert entity.extra_state_attributes == {}

    def test_unique_id_pattern(self):
        """Entity unique_id includes robot blid + rooms_map suffix."""
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = None
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size  = 600
        # Minimal init — check unique_id contains rooms_map
        import collections
        entity.access_tokens = collections.deque([], 2)
        entity._attr_unique_id = "blid123_rooms_map"
        assert "rooms_map" in entity._attr_unique_id

    def test_entity_name_not_locale_slug(self):
        """translation_key drives the display name; _attr_name must NOT be set.

        v3.0.0 locale-slug fix: having both _attr_name and _attr_translation_key
        caused _attr_name to override the translation in modern HA versions, so
        the English name showed in non-English locales (e.g. 'Rooms Map' in DE).
        Fix: remove _attr_name entirely; only _attr_translation_key remains.
        """
        import collections
        from custom_components.roomba_plus.image import RoombaRoomsImage
        # HA wraps _attr_* as cached_property; create a minimal instance to read the value.
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = None
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size  = 600
        entity.access_tokens = collections.deque([], 2)
        # translation_key must be 'rooms_map'
        assert entity.translation_key == "rooms_map", (
            f"translation_key must be 'rooms_map', got {entity.translation_key!r}"
        )
        # _attr_name must not be set — accessing it should raise AttributeError or return None
        raw_name = None
        for cls in type(entity).__mro__:
            if "_attr_name" in cls.__dict__ and not isinstance(cls.__dict__["_attr_name"], property):
                raw_name = cls.__dict__["_attr_name"]
                break
        assert raw_name is None, (
            f"_attr_name is set to {raw_name!r} — must be removed (overrides translation_key)"
        )

    def test_to_px_last_consistency(self):
        entity = self._entity()
        # With default transform (size=600, x_min=0, x_max=5000, y_min=0, y_max=5000)
        # scale = 600/5000 = 0.12
        px, py = entity._to_px_last(0.0, 0.0)
        assert isinstance(px, int)
        assert isinstance(py, int)


def _gs_with_furniture_candidate():
    """Real GridStore with one genuine furniture candidate — established
    coverage for 20 missions, then 3 consecutive absences (the FURNITURE
    signature), same recipe as test_grid_store.py's TestFurnitureCandidates.
    """
    from custom_components.roomba_plus.grid_store import GridStore
    gs = GridStore()
    cell_point = (75, 75)
    other_point = (10000, 10000)
    for _ in range(20):
        gs.update_from_mission([cell_point], [])
    for _ in range(3):
        gs.update_from_mission([other_point], [])
    return gs


def _geometry_store_with_door_marker():
    from custom_components.roomba_plus.geometry_store import GeometryStore, DoorMarker
    gstore = GeometryStore()
    marker = DoorMarker(id="dm_1", cx=500.0, cy=700.0, label="Hallway door")
    marker.update(500.0, 700.0)
    gstore.door_markers = [marker]
    return gstore


class TestZoneOverlayAndFurnitureRoombaMapImage:
    """ZONE-OVERLAY (v3.3.1) + F24 on RoombaMapImage.extra_state_attributes."""

    def _entity(self, aligner=None, renderer=None):
        from custom_components.roomba_plus.image import RoombaMapImage
        entity = object.__new__(RoombaMapImage)
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._config_entry.runtime_data.cloud_coordinator.regions = []
        entity._config_entry.runtime_data.cloud_coordinator.observed_zone_centroids = []
        entity._config_entry.runtime_data.cloud_coordinator.keepout_zones = []
        entity._config_entry.runtime_data.geometry_store = None
        entity._config_entry.runtime_data.grid_store = None
        entity._renderer = renderer
        return entity

    def _aligned_entity(self):
        aligner = _make_aligner()
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        renderer = MagicMock()
        renderer._mm_to_px_fit.side_effect = lambda x, y: (int(x), int(y))
        return self._entity(aligner=aligner, renderer=renderer)

    def test_zones_absent_when_no_cloud_data(self):
        entity = self._aligned_entity()
        attrs = entity.extra_state_attributes
        assert "zones" not in attrs

    def test_observed_zone_transformed_to_pose_space(self):
        entity = self._aligned_entity()
        entity._config_entry.runtime_data.cloud_coordinator.observed_zone_centroids = [
            {"x": 500.0, "y": 300.0}
        ]
        attrs = entity.extra_state_attributes
        assert "zones" in attrs
        observed = [z for z in attrs["zones"] if z["type"] == "observed"]
        assert len(observed) == 1
        # identity transform (rot=0, tx=0, ty=0) → pose == umf input
        assert observed[0]["x"] == pytest.approx(500.0)
        assert observed[0]["y"] == pytest.approx(300.0)

    def test_keepout_zone_polygon_transformed(self):
        entity = self._aligned_entity()
        entity._config_entry.runtime_data.cloud_coordinator.keepout_zones = [
            {"geometry": {"ids": [["p1", "p2", "p3"]]}}
        ]
        entity._config_entry.runtime_data.umf_aligner._coord_lookup = {
            "p1": (0.0, 0.0), "p2": (100.0, 0.0), "p3": (100.0, 100.0),
        }
        attrs = entity.extra_state_attributes
        keepout = [z for z in attrs["zones"] if z["type"] == "keepout"]
        assert len(keepout) == 1
        assert len(keepout[0]["polygon"]) == 3

    def test_door_markers_exposed_as_is_no_transform(self):
        entity = self._aligned_entity()
        entity._config_entry.runtime_data.geometry_store = _geometry_store_with_door_marker()
        attrs = entity.extra_state_attributes
        assert "door_markers" in attrs
        marker = attrs["door_markers"][0]
        # Exposed raw — cx/cy must match the store exactly, no aligner transform
        assert marker["cx"] == pytest.approx(500.0)
        assert marker["cy"] == pytest.approx(700.0)
        assert marker["label"] == "Hallway door"
        assert marker["mission_count"] == 1

    def test_door_markers_absent_when_no_geometry_store(self):
        entity = self._aligned_entity()
        attrs = entity.extra_state_attributes
        assert "door_markers" not in attrs

    def test_door_markers_absent_when_store_empty(self):
        entity = self._aligned_entity()
        from custom_components.roomba_plus.geometry_store import GeometryStore
        entity._config_entry.runtime_data.geometry_store = GeometryStore()
        attrs = entity.extra_state_attributes
        assert "door_markers" not in attrs

    def test_furniture_candidates_exposed_as_pose_space_mm(self):
        entity = self._aligned_entity()
        entity._config_entry.runtime_data.grid_store = _gs_with_furniture_candidate()
        attrs = entity.extra_state_attributes
        assert "furniture_candidates" in attrs
        assert len(attrs["furniture_candidates"]) >= 1
        cand = attrs["furniture_candidates"][0]
        assert "x_mm" in cand and "y_mm" in cand
        # Only x_mm/y_mm surfaced — not the internal "cell" tuple
        assert "cell" not in cand

    def test_furniture_candidates_absent_when_no_grid_store(self):
        entity = self._aligned_entity()
        attrs = entity.extra_state_attributes
        assert "furniture_candidates" not in attrs

    def test_furniture_candidates_absent_when_none_qualify(self):
        entity = self._aligned_entity()
        from custom_components.roomba_plus.grid_store import GridStore
        entity._config_entry.runtime_data.grid_store = GridStore()
        attrs = entity.extra_state_attributes
        assert "furniture_candidates" not in attrs


class TestZoneOverlayAndFurnitureRoombaRoomsImage:
    """ZONE-OVERLAY (v3.3.1) + F24 on RoombaRoomsImage.extra_state_attributes —
    parity with RoombaMapImage, but gated to aligned mode only (fallback mode
    renders in UMF-space, so pose-space overlays would be spatially wrong).
    """

    def _entity(self, aligner=None, aligned_render=True):
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._config_entry.runtime_data.cloud_coordinator.regions = []
        entity._config_entry.runtime_data.cloud_coordinator.observed_zone_centroids = []
        entity._config_entry.runtime_data.cloud_coordinator.keepout_zones = []
        entity._config_entry.runtime_data.geometry_store = None
        entity._config_entry.runtime_data.grid_store = None
        entity._last_x_min = 0.0
        entity._last_x_max = 5000.0
        entity._last_y_min = 0.0
        entity._last_y_max = 5000.0
        entity._last_size  = 600
        entity._room_render_cache_key = None
        entity._room_render_cache = None
        if aligned_render:
            entity._rendered_once = True
        else:
            entity._rendered_fallback = True
        return entity

    def _aligned_entity(self):
        aligner = _make_aligner(aligned=True)
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        return self._entity(aligner=aligner, aligned_render=True)

    def test_zones_door_markers_furniture_present_when_aligned(self):
        entity = self._aligned_entity()
        entity._config_entry.runtime_data.cloud_coordinator.observed_zone_centroids = [
            {"x": 200.0, "y": 400.0}
        ]
        entity._config_entry.runtime_data.geometry_store = _geometry_store_with_door_marker()
        entity._config_entry.runtime_data.grid_store = _gs_with_furniture_candidate()
        attrs = entity.extra_state_attributes
        assert "zones" in attrs
        assert "door_markers" in attrs
        assert "furniture_candidates" in attrs

    def test_zones_door_markers_furniture_withheld_in_fallback_mode(self):
        """Not-yet-aligned mode: image is UMF-space, pose-space overlays
        would be spatially wrong, so all three must be absent entirely —
        even though the underlying data exists."""
        aligner = _make_aligner(aligned=False)
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        entity = self._entity(aligner=aligner, aligned_render=False)
        entity._config_entry.runtime_data.cloud_coordinator.observed_zone_centroids = [
            {"x": 200.0, "y": 400.0}
        ]
        entity._config_entry.runtime_data.geometry_store = _geometry_store_with_door_marker()
        entity._config_entry.runtime_data.grid_store = _gs_with_furniture_candidate()
        attrs = entity.extra_state_attributes
        assert "zones" not in attrs
        assert "door_markers" not in attrs
        assert "furniture_candidates" not in attrs


class TestCoverageMapSignal:
    """Bug E — coverage signal constant must exist and be unique per entry."""

    def test_signal_constant_exists(self):
        from custom_components.roomba_plus.image import _SIGNAL_COVERAGE_UPDATED
        assert "{}" in _SIGNAL_COVERAGE_UPDATED, (
            "Signal must be a format string with entry_id placeholder"
        )

    def test_signal_unique_per_entry(self):
        from custom_components.roomba_plus.image import _SIGNAL_COVERAGE_UPDATED
        sig1 = _SIGNAL_COVERAGE_UPDATED.format("entry_aaa")
        sig2 = _SIGNAL_COVERAGE_UPDATED.format("entry_bbb")
        assert sig1 != sig2

    def test_coverage_image_no_longer_has_last_phase(self):
        """RoombaCoverageImage must not have _last_phase (dead code removed)."""
        from custom_components.roomba_plus.image import RoombaCoverageImage
        import inspect
        src = inspect.getsource(RoombaCoverageImage.__init__)
        assert "_last_phase" not in src, (
            "_last_phase was dead state; should be removed from __init__"
        )

    def test_coverage_image_has_no_trigger_grid_update(self):
        """_trigger_grid_update dead code must be removed."""
        from custom_components.roomba_plus.image import RoombaCoverageImage
        assert not hasattr(RoombaCoverageImage, "_trigger_grid_update"), (
            "_trigger_grid_update was dead code (wrong attr names); must be removed"
        )

    def test_async_send_coverage_signal_is_coroutine(self):
        """_async_send_coverage_signal must be an async function."""
        import asyncio
        from custom_components.roomba_plus.image import _async_send_coverage_signal
        # `inspect`, not `asyncio`: the asyncio spelling is deprecated
        # and goes away in Python 3.16.
        assert inspect.iscoroutinefunction(_async_send_coverage_signal)


class TestTerminalMissionImageRefresh:
    @pytest.mark.asyncio
    async def test_empty_terminal_refreshes_all_images_once(self):
        from custom_components.roomba_plus.grid_store import GridStore
        from custom_components.roomba_plus.image import (
            RoombaCoverageImage,
            RoombaMapImage,
            RoombaRoomsImage,
            _SIGNAL_COVERAGE_UPDATED,
        )
        from homeassistant.util import dt as dt_util

        entry = entry_mock()
        entry.entry_id = "test_entry"
        entry.runtime_data = MagicMock()
        entry.runtime_data.grid_store = GridStore()
        entry.runtime_data.umf_aligner = None
        hass = hass_mock()
        hass.loop = MagicMock()
        before = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        listeners: dict[str, list[Any]] = {}

        def connect(_hass, signal, listener):
            listeners.setdefault(signal, []).append(listener)
            return lambda: None

        coverage = RoombaCoverageImage.__new__(RoombaCoverageImage)
        coverage._config_entry = entry
        coverage._grid_store = entry.runtime_data.grid_store
        coverage._cache = b"old"
        coverage._attr_image_last_updated = before
        coverage.hass = hass
        coverage.async_update_token = MagicMock()
        coverage.async_on_remove = MagicMock()
        coverage.async_write_ha_state = MagicMock()

        rooms = RoombaRoomsImage.__new__(RoombaRoomsImage)
        rooms._config_entry = entry
        rooms._cache = b"old"
        rooms._attr_image_last_updated = before
        rooms.hass = hass
        rooms.async_update_token = MagicMock()
        rooms.async_on_remove = MagicMock()
        rooms.async_write_ha_state = MagicMock()

        with (
            patch.object(IRobotEntity, "async_added_to_hass", new=AsyncMock()),
            patch(
                "homeassistant.helpers.dispatcher.async_dispatcher_connect",
                side_effect=connect,
            ),
        ):
            await coverage.async_added_to_hass()
            await rooms.async_added_to_hass()

        route = RoombaMapImage.__new__(RoombaMapImage)
        route.hass = hass
        route._config_entry = entry
        route._mission_points = []
        route._mission_thetas = []
        route._stuck_mission_points = []
        route._mission_start_ts = "2026-09-02T10:00:00+00:00"
        route._mission_checkpoint_mssn_strt_tm = 987654
        route._last_terminal_mission_key = None
        route.vacuum_state = {
            "cleanMissionStatus": {"mssnStrtTm": 987654},
        }
        route._cache = b"old"
        route._attr_image_last_updated = before

        scheduled = []

        def schedule(_hass, coro, *_ignored):
            scheduled.append(coro)
            return MagicMock()

        with patch.object(
            route._config_entry, "async_create_task",
            side_effect=schedule,
        ):
            route._handle_mission_end()
            route._handle_mission_end()

        refresh_coroutines = [
            coro
            for coro in scheduled
            if coro.cr_code.co_name == "_async_send_coverage_signal"
        ]
        assert len(refresh_coroutines) == 1
        signal = _SIGNAL_COVERAGE_UPDATED.format(entry.entry_id)

        def send(_hass, dispatched_signal):
            for listener in listeners[dispatched_signal]:
                listener()

        with patch(
            "homeassistant.helpers.dispatcher.async_dispatcher_send",
            side_effect=send,
        ):
            await refresh_coroutines[0]

        for coro in scheduled:
            if coro not in refresh_coroutines:
                coro.close()

        assert route._attr_image_last_updated > before
        assert route._cache is None
        assert coverage._attr_image_last_updated > before
        assert coverage._cache is None
        coverage.async_write_ha_state.assert_called_once()
        assert rooms._attr_image_last_updated > before
        assert rooms._cache is None
        rooms.async_write_ha_state.assert_called_once()
        assert len(listeners[signal]) == 2

    @pytest.mark.asyncio
    async def test_pose_present_terminal_still_feeds_grid_and_refreshes_images(self):
        """A mission WITH pose data must both feed GridStore (unchanged
        live path) and fire the same terminal image-refresh signal — the
        refresh call moved earlier in _handle_mission_end but nothing
        after it changed."""
        from custom_components.roomba_plus.grid_store import GridStore
        from custom_components.roomba_plus.image import (
            RoombaCoverageImage,
            RoombaMapImage,
            _SIGNAL_COVERAGE_UPDATED,
        )

        entry = entry_mock()
        entry.entry_id = "test_entry"
        entry.runtime_data = MagicMock()
        gs = GridStore()
        entry.runtime_data.grid_store = gs
        entry.runtime_data.umf_aligner = None
        hass = hass_mock()
        hass.loop = MagicMock()
        before = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        listeners: dict[str, list[Any]] = {}

        def connect(_hass, signal, listener):
            listeners.setdefault(signal, []).append(listener)
            return lambda: None

        coverage = RoombaCoverageImage.__new__(RoombaCoverageImage)
        coverage._config_entry = entry
        coverage._grid_store = gs
        coverage._cache = b"old"
        coverage._attr_image_last_updated = before
        coverage.hass = hass
        coverage.async_update_token = MagicMock()
        coverage.async_on_remove = MagicMock()
        coverage.async_write_ha_state = MagicMock()

        with (
            patch.object(IRobotEntity, "async_added_to_hass", new=AsyncMock()),
            patch(
                "homeassistant.helpers.dispatcher.async_dispatcher_connect",
                side_effect=connect,
            ),
        ):
            await coverage.async_added_to_hass()

        route = RoombaMapImage.__new__(RoombaMapImage)
        route.hass = hass
        route._config_entry = entry
        route._renderer = MagicMock()
        route._renderer._cfg.robot_diameter_mm = 300
        route._zone_store = None
        route._map_capability = None
        route._mission_points = [(0.0, 0.0), (100.0, 100.0)]
        route._mission_thetas = [0.0, 0.0]
        route._stuck_mission_points = []
        route._mission_start_ts = "2026-09-02T10:00:00+00:00"
        route._mission_checkpoint_mssn_strt_tm = 987654
        route._last_terminal_mission_key = None
        route.vacuum_state = {
            "cleanMissionStatus": {"mssnStrtTm": 987654},
            "bbmssn": {"nMssn": 77},
        }
        route._cache = b"old"
        route._attr_image_last_updated = before

        scheduled = []

        def schedule(_hass, coro, *_ignored):
            scheduled.append(coro)
            return MagicMock()

        with patch.object(
            route._config_entry, "async_create_task",
            side_effect=schedule,
        ):
            route._handle_mission_end()

        assert gs.last_processed_nmssn == 77

        refresh_coroutines = [
            coro for coro in scheduled
            if coro.cr_code.co_name == "_async_send_coverage_signal"
        ]
        assert len(refresh_coroutines) == 1
        signal = _SIGNAL_COVERAGE_UPDATED.format(entry.entry_id)

        def send(_hass, dispatched_signal):
            for listener in listeners[dispatched_signal]:
                listener()

        with patch(
            "homeassistant.helpers.dispatcher.async_dispatcher_send",
            side_effect=send,
        ):
            await refresh_coroutines[0]

        for coro in scheduled:
            if coro not in refresh_coroutines:
                coro.close()

        assert route._attr_image_last_updated > before
        assert coverage._attr_image_last_updated > before
        assert coverage._cache is None
        coverage.async_write_ha_state.assert_called_once()


class TestXvmcCoords:
    """rooms.outline and x/y must be in vacuum mm, not image pixels."""

    def test_rooms_map_attributes_use_mm_not_px(self):
        """RoombaRoomsImage extra_state_attributes: outline in poly_umf coords."""
        from custom_components.roomba_plus.image import RoombaRoomsImage

        # The refactored code uses poly_coords (mm) instead of poly_px.
        # Verify the source file no longer calls _to_px_last for outline.
        import inspect
        src = inspect.getsource(RoombaRoomsImage.extra_state_attributes.fget
                                if hasattr(RoombaRoomsImage.extra_state_attributes, 'fget')
                                else RoombaRoomsImage.extra_state_attributes)
        # Must not compute pixel list for the outline
        assert "poly_px" not in src or "# XVMC-COORDS" in src

    def test_log_text_updated(self):
        """Misleading 'attributes withheld' log text must be gone."""
        import inspect
        from custom_components.roomba_plus import image
        src = inspect.getsource(image)
        assert "attributes withheld" not in src
        assert "fallback calibration active" in src


def _make_map_entity():
    """Build a minimal RoombaMapImage suitable for exercising on_message()'s
    phase-transition / END-DEBOUNCE decision logic in isolation.

    _handle_mission_end is replaced with a MagicMock so the test verifies
    *when* it gets called without exercising the heavy downstream renderer/
    ZoneStore/GeometryStore/GridStore/OutlineStore side effects — those are
    covered separately by their own store-level tests.
    """
    from custom_components.roomba_plus.image import RoombaMapImage

    roomba = robot_mock()
    roomba.master_state = {"state": {"reported": {}}}

    entity = RoombaMapImage.__new__(RoombaMapImage)
    entity.vacuum = roomba
    entity._renderer = MagicMock()
    # v3.2.1 DOCK-ANCHOR — a real int, not a bare MagicMock: incidental
    # dock-contact-confirmed triggers (e.g. tests that happen to send a
    # charge/hmPostMsn burst for unrelated reasons) do arithmetic on
    # this (max(0, point_count - len(segment))) — a MagicMock there
    # raises TypeError, unrelated to whatever that other test actually
    # checks.
    entity._renderer.point_count = 0
    entity._zone_store = None
    # v3.2.1 DOCK-ANCHOR — EPHEMERAL by default: this is the tier every
    # dock-anchor test in this file (and most of this evening's work)
    # actually concerns. Tests specifically verifying SMART-robot
    # behaviour (e.g. the new EPHEMERAL-only gates) override this
    # explicitly afterward.
    entity._map_capability = MapCapability.EPHEMERAL
    entity._config_entry = entry_mock()
    # v3.2.1 DOCK-ANCHOR — explicit None, not left as an
    # auto-generating MagicMock: incidental dock-contact-confirmed
    # triggers (see entity._renderer.point_count comment above) would
    # otherwise do arithmetic against a MagicMock
    # (dock_theta_baseline, geometry_store.record_drift's return value)
    # and raise TypeErrors unrelated to whatever a given test actually
    # checks. Tests that specifically need these stores set them
    # explicitly afterward.
    entity._config_entry.runtime_data.robot_profile_store = None
    entity._config_entry.runtime_data.geometry_store = None
    entity._attr_unique_id = "test_blid_map"
    entity.access_tokens = collections.deque([], 2)
    entity._cache = None
    entity._last_phase = ""
    entity._last_stuck_count = 0
    entity._mission_points = []
    entity._mission_thetas = []
    entity._stuck_mission_points = []
    entity._dock_anchor_buffering = False
    entity._pending_segment_points = []
    entity._pending_segment_thetas = []
    entity._last_dock_anchor_index = 0
    entity._dock_contact_streak = 0
    entity._dock_contact_first_ts = 0.0
    entity._had_cleaning_phase = False
    entity._end_signal_streak = 0
    entity._end_signal_first_ts = 0.0
    entity._mission_start_ts = None
    entity._mission_checkpoint_mssn_strt_tm = 0
    entity._pending_checkpoint = None
    entity.vacuum_state = {}
    entity.hass = hass_mock()
    entity.schedule_update_ha_state = MagicMock()
    entity._handle_mission_end = MagicMock()

    return entity


def _map_msg(phase: str, cycle: str | None = None, mssn_strt_tm: int | None = None) -> dict:
    """Build a minimal MQTT message dict carrying only cleanMissionStatus."""
    mission: dict[str, Any] = {"phase": phase}
    if cycle is not None:
        mission["cycle"] = cycle
    if mssn_strt_tm is not None:
        mission["mssnStrtTm"] = mssn_strt_tm
    return {"state": {"reported": {"cleanMissionStatus": mission}}}


def _stuck_msg(n_stuck: int) -> dict:
    """Build a minimal MQTT message dict carrying only bbrun.nStuck."""
    return {"state": {"reported": {"bbrun": {"nStuck": n_stuck}}}}


def _pose_msg(x_cm: float, y_cm: float, theta: float = 0.0) -> dict:
    """Build a minimal MQTT message dict carrying only pose.point (cm,
    matching real firmware units — see POSE_POINT_CM_TO_MM)."""
    return {"state": {"reported": {"pose": {"point": {"x": x_cm, "y": y_cm}, "theta": theta}}}}


def _feed_map_entity(entity, msg: dict) -> None:
    """Simulate roombapy merging the delta into master_state, then deliver it."""
    reported = entity.vacuum.master_state["state"]["reported"]
    reported.update(msg["state"]["reported"])
    entity.on_message(msg)


class TestImageEndDebounceV281:
    """v2.8.1 (END-DEBOUNCE) — image.py's independent mission-end detection
    had zero protection at all (not even the v2.8.0 cycle-only guard), so a
    single transient ambiguous-phase blip would fragment _mission_points
    (and therefore ZoneStore/GeometryStore/GridStore/OutlineStore) mid-
    mission. This mirrors the callbacks.py coverage for the matching fix.
    """

    def test_single_transient_charge_blip_does_not_trigger_mission_end(self):
        entity = _make_map_entity()
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))
        assert entity._had_cleaning_phase is True

        # Single transient blip — must not fire _handle_mission_end()
        _feed_map_entity(entity, _map_msg("charge", cycle="none"))
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))

        entity._handle_mission_end.assert_not_called()
        assert entity._had_cleaning_phase is True

    def test_streak_does_not_carry_across_an_interruption(self):
        entity = _make_map_entity()
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))
        _feed_map_entity(entity, _map_msg("charge", cycle="none"))
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))
        _feed_map_entity(entity, _map_msg("charge", cycle="none"))

        entity._handle_mission_end.assert_not_called()

    def test_two_consecutive_charge_messages_confirm_genuine_end(self):
        """Two consecutive charge+cycle-misreport messages with sufficient
        time between them DO confirm a genuine end.  Time is mocked so
        the second message appears 3 s after the first (> 2.0 s hold).

        v3.2.1 DOCK-ANCHOR — 4 monotonic() calls now, not 3: the new
        dock-contact debounce (separate from _end_signal_streak, see
        image.py) runs its own, independent hold-time check right after
        the existing end-signal one, for the SAME two charge messages.
        """
        entity = _make_map_entity()
        with patch("custom_components.roomba_plus.image._time_mod") as tmock:
            tmock.monotonic.side_effect = [
                1.0,   # end_signal_first_ts on first charge
                1.0,   # dock_contact_first_ts on first charge
                4.0,   # end-signal time_held check on second charge
                4.0,   # dock-contact time_held check on second charge
            ]
            _feed_map_entity(entity, _map_msg("run", cycle="clean"))
            _feed_map_entity(entity, _map_msg("charge", cycle="none"))
            _feed_map_entity(entity, _map_msg("charge", cycle="none"))

        entity._handle_mission_end.assert_called_once_with("charge")
        assert entity._had_cleaning_phase is False

    def test_rapid_burst_of_two_end_looking_messages_does_not_trigger_mission_end(self):
        """v2.8.3 regression — same burst scenario as test_mission_timer_store.py:
        lewis firmware sends two cleanMissionStatus messages ~21 ms apart
        during an inter-room transition.  image.py's independent mission-end
        detection must apply the same time gate and not call
        _handle_mission_end() mid-mission.

        v3.2.1 DOCK-ANCHOR — this is ALSO the regression test that caught
        a real gap in the first version of the new dock-contact debounce
        (image.py): it originally had no hold-time check at all, so this
        exact ~21ms glitch would have wrongly confirmed a dock contact
        and applied a bogus correction. 4 monotonic() calls now, not 3
        — see test_two_consecutive_charge_messages_confirm_genuine_end.
        """
        entity = _make_map_entity()

        with patch("custom_components.roomba_plus.image._time_mod") as tmock:
            tmock.monotonic.side_effect = [
                1.000,  # end_signal_first_ts on first charge
                1.000,  # dock_contact_first_ts on first charge
                1.021,  # end-signal time_held check on second charge (0.021s < 2.0s -> burst)
                1.021,  # dock-contact time_held check on second charge (same)
            ]
            _feed_map_entity(entity, _map_msg("run", cycle="clean"))
            # Set mission_points AFTER the run message — run triggers new-mission
            # detection which resets _mission_points, so points must be set here
            # to simulate accumulated pose data from an ongoing mission.
            entity._mission_points = [(0.0, 0.0), (100.0, 0.0)]
            _feed_map_entity(entity, _map_msg("charge", cycle="none"))
            _feed_map_entity(entity, _map_msg("charge", cycle="none"))
            _feed_map_entity(entity, _map_msg("run", cycle="clean"))

        entity._handle_mission_end.assert_not_called()
        assert entity._had_cleaning_phase is True
        assert entity._mission_points == [(0.0, 0.0), (100.0, 0.0)], (
            "A rapid burst must not fragment _mission_points"
        )

    def test_stop_phase_still_confirms_immediately_no_debounce(self):
        entity = _make_map_entity()
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))
        _feed_map_entity(entity, _map_msg("stop", cycle="none"))

        entity._handle_mission_end.assert_called_once_with("stop")

    def test_mission_points_not_wiped_by_a_transient_blip(self):
        """Direct regression test for the reported symptom: a transient
        blip must not cause the live map / zone tracking to lose its
        accumulated trajectory mid-mission."""
        entity = _make_map_entity()
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))
        entity._mission_points = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]

        _feed_map_entity(entity, _map_msg("charge", cycle="none"))
        _feed_map_entity(entity, _map_msg("run", cycle="clean"))

        assert entity._mission_points == [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)], (
            "A single transient blip must not clear _mission_points — this "
            "is what fragments ZoneStore/GeometryStore/GridStore/OutlineStore"
        )


class TestMissionCheckpointV282:
    """v2.8.2 — mission-in-progress checkpoint (save on stuck, resume-or
    -salvage on the next message). Protects against the case that matters
    most for a robot with a high mission-failure rate: a mission that gets
    stuck and never reaches a clean end (HA restart, manual intervention)
    before that happens — without this, _mission_points (and therefore
    ZoneStore/GeometryStore/GridStore/OutlineStore) would simply lose that
    data, since those stores are only ever fed at a genuine mission end.
    """

    # ── _consume_pending_checkpoint() ────────────────────────────────────

    def test_no_checkpoint_is_a_noop(self):
        entity = _make_map_entity()
        entity._pending_checkpoint = None
        entity._consume_pending_checkpoint()
        entity._handle_mission_end.assert_not_called()
        assert entity._mission_points == []

    def test_same_mission_still_active_resumes(self):
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,
            "mission_points": [(10.0, 20.0), (30.0, 40.0)],
            "stuck_mission_points": [(10.0, 20.0)],
            "mission_start_ts": "2026-06-18T10:00:00+00:00",
            "renderer_state": {"fake": "state"},
            "last_stuck_count": 3,
        }
        entity._consume_pending_checkpoint()

        assert entity._mission_points == [(10.0, 20.0), (30.0, 40.0)]
        assert entity._stuck_mission_points == [(10.0, 20.0)]
        assert entity._mission_start_ts == "2026-06-18T10:00:00+00:00"
        assert entity._had_cleaning_phase is True
        assert entity._mission_checkpoint_mssn_strt_tm == 12345
        assert entity._last_stuck_count == 3, (
            "bug-hunt fix — must be restored from the checkpoint, not left "
            "at the post-__init__ default of 0 (which would make the next "
            "bbrun message look like a brand-new stuck event)"
        )
        entity._renderer.restore_state.assert_called_once_with({"fake": "state"})
        entity._handle_mission_end.assert_not_called()
        assert entity._pending_checkpoint is None

    def test_same_mission_still_active_resumes_thetas_too(self):
        """v3.2.1 — mission_thetas must restore alongside mission_points
        via the same checkpoint path, keeping index alignment intact
        across an HA restart mid-mission."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,
            "mission_points": [(10.0, 20.0), (30.0, 40.0)],
            "mission_thetas": [0.0, 90.0],
            "stuck_mission_points": [(10.0, 20.0)],
            "mission_start_ts": "2026-06-18T10:00:00+00:00",
            "renderer_state": {"fake": "state"},
            "last_stuck_count": 3,
        }
        entity._consume_pending_checkpoint()

        assert entity._mission_thetas == [0.0, 90.0]

    def test_checkpoint_without_thetas_key_defaults_empty(self):
        """v3.2.1 — additive field: a checkpoint saved before
        mission_thetas existed simply has no such key."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,
            "mission_points": [(10.0, 20.0)],
            "stuck_mission_points": [],
            "mission_start_ts": "2026-06-18T10:00:00+00:00",
            "renderer_state": {"fake": "state"},
            "last_stuck_count": 0,
        }
        entity._consume_pending_checkpoint()

        assert entity._mission_thetas == []

    def test_resume_does_not_require_an_actively_cleaning_phase(self):
        """bug-hunt fix — a matching mssnStrtTm alone proves this is the
        same physical mission. Previously this also required current_phase
        to be in CLEANING_PHASES, which meant landing on an ordinary
        inter-room transition blip (charge/hmPostMsn) as the first
        post-restart message would wrongly salvage a still-running mission.
        Resume must fire regardless of what current_phase happens to be —
        the normal phase-transition/END-DEBOUNCE logic, run immediately
        after against this same message, is what actually decides whether
        the mission keeps going or has genuinely ended."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,
            "mission_points": [(10.0, 20.0)],
            "stuck_mission_points": [],
            "mission_start_ts": "2026-06-18T10:00:00+00:00",
            "renderer_state": None,
            "last_stuck_count": 0,
        }
        # current_phase is no longer a parameter at all — the live phase is
        # read internally from vacuum_state only for the mssnStrtTm match.
        entity._consume_pending_checkpoint()

        entity._handle_mission_end.assert_not_called()
        assert entity._had_cleaning_phase is True
        assert entity._mission_points == [(10.0, 20.0)]

    def test_different_mission_started_salvages(self):
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 99999}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,  # different from the live mission
            "mission_points": [(10.0, 20.0)],
            "stuck_mission_points": [],
            "mission_start_ts": "2026-06-18T10:00:00+00:00",
            "renderer_state": None,
        }
        entity._consume_pending_checkpoint()

        entity._handle_mission_end.assert_called_once_with(ending_phase="")
        assert entity._pending_checkpoint is None

    def test_checkpoint_without_mssn_strt_tm_salvages(self):
        """A checkpoint with no recorded mssnStrtTm can never be confirmed
        as 'the same mission' — must always be treated as orphaned, never
        silently resumed against an unrelated live mission."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 0,
            "mission_points": [(10.0, 20.0)],
            "stuck_mission_points": [],
            "mission_start_ts": None,
            "renderer_state": None,
        }
        entity._consume_pending_checkpoint()

        entity._handle_mission_end.assert_called_once_with(ending_phase="")

    def test_never_both_resume_and_salvage(self):
        """Whichever branch is taken, the other must never also fire — the
        core double-counting guard for the whole feature."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 12345}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 12345,
            "mission_points": [(10.0, 20.0)],
            "stuck_mission_points": [],
            "mission_start_ts": None,
            "renderer_state": None,
        }
        entity._consume_pending_checkpoint()
        assert entity._had_cleaning_phase is True
        entity._handle_mission_end.assert_not_called()  # resumed, not salvaged

    # ── _salvage_orphaned_checkpoint() ───────────────────────────────────

    def test_salvage_loads_points_before_calling_mission_end(self):
        entity = _make_map_entity()
        checkpoint = {
            "mission_points": [(1.0, 2.0), (3.0, 4.0)],
            "stuck_mission_points": [(1.0, 2.0)],
            "mission_start_ts": "2026-06-18T09:00:00+00:00",
            "renderer_state": {"fake": "state"},
        }

        captured: dict[str, Any] = {}

        def _capture_end(ending_phase):
            captured["mission_points"] = list(entity._mission_points)
            captured["stuck_mission_points"] = list(entity._stuck_mission_points)
            captured["ending_phase"] = ending_phase

        entity._handle_mission_end = MagicMock(side_effect=_capture_end)

        entity._salvage_orphaned_checkpoint(checkpoint)

        assert captured["mission_points"] == [(1.0, 2.0), (3.0, 4.0)]
        assert captured["stuck_mission_points"] == [(1.0, 2.0)]
        assert captured["ending_phase"] == ""
        entity._renderer.restore_state.assert_called_once_with({"fake": "state"})

    def test_empty_checkpoint_still_gets_cleared(self):
        """bug-hunt fix — _handle_mission_end() has an early-return when
        _mission_points is empty (nothing to process). A checkpoint can
        legitimately be saved with empty mission_points (a stuck event
        fired before any pose message had arrived yet), and
        _salvage_orphaned_checkpoint() loads exactly that. Before this fix,
        the checkpoint-clear call sat after the early-return and never ran
        for this case — the same empty checkpoint would be reloaded and
        re-salvaged (a no-op) on every subsequent HA restart forever. Uses
        the real _handle_mission_end (not the mocked one from
        _make_map_entity()) since that's exactly the code path being
        verified."""
        from custom_components.roomba_plus.image import RoombaMapImage

        entity = RoombaMapImage.__new__(RoombaMapImage)
        entity.hass = hass_mock()
        entity._config_entry = entry_mock()
        entity._renderer = MagicMock()
        entity._zone_store = None
        entity._map_capability = None
        entity._mission_points = []  # the empty-checkpoint case
        entity._mission_thetas = []
        entity._stuck_mission_points = []
        entity._mission_start_ts = "2026-06-18T09:00:00+00:00"

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            entity._handle_mission_end(ending_phase="")

        # args[1]: config_entry.async_create_task takes (hass, coro).
        scheduled_coros = [c.args[1] for c in mock_run.call_args_list]
        assert any(
            getattr(c, "__qualname__", "").endswith("_async_clear_mission_checkpoint")
            for c in scheduled_coros
        ), "checkpoint clear must still be scheduled even with no mission_points"
        for c in scheduled_coros:
            c.close()  # avoid "coroutine was never awaited" warnings

    def test_gs_smart_coverage_live_path_stamps_watermark(self):
        """v3.4.0 GS-SMART-COVERAGE — after a real (non-empty
        mission_points) GridStore update, the live path must stamp
        this mission's nMssn onto the shared watermark, using the
        real _handle_mission_end (not the mocked one from
        _make_map_entity()) since the stamp call site lives right
        after the real update_from_mission() call it's paired with."""
        from custom_components.roomba_plus.grid_store import GridStore
        from custom_components.roomba_plus.image import RoombaMapImage

        gs = GridStore()
        assert gs.last_processed_nmssn == 0

        entity = RoombaMapImage.__new__(RoombaMapImage)
        entity.hass = hass_mock()
        entity.hass.loop = MagicMock()
        entity._config_entry = entry_mock()
        entity._config_entry.entry_id = "test_entry"
        entity._config_entry.runtime_data.grid_store = gs
        entity._renderer = MagicMock()
        entity._renderer._cfg.robot_diameter_mm = 300
        entity._zone_store = None
        entity._map_capability = None  # skips the EPHEMERAL/SMART branches
        entity._mission_points = [(0.0, 0.0), (100.0, 100.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._stuck_mission_points = []
        entity._mission_start_ts = "2026-06-18T09:00:00+00:00"
        entity.vacuum_state = {"bbmssn": {"nMssn": 77}}

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            entity._handle_mission_end(ending_phase="")

        for c in mock_run.call_args_list:
            c.args[0].close()  # avoid "coroutine was never awaited" warnings

        assert gs.last_processed_nmssn == 77

    def test_gs_smart_coverage_missing_nmssn_does_not_crash(self):
        """No bbmssn.nMssn on this firmware/state — record_processed_nmssn()
        must silently no-op (per its own contract), not raise."""
        from custom_components.roomba_plus.grid_store import GridStore
        from custom_components.roomba_plus.image import RoombaMapImage

        gs = GridStore()

        entity = RoombaMapImage.__new__(RoombaMapImage)
        entity.hass = hass_mock()
        entity.hass.loop = MagicMock()
        entity._config_entry = entry_mock()
        entity._config_entry.entry_id = "test_entry"
        entity._config_entry.runtime_data.grid_store = gs
        entity._renderer = MagicMock()
        entity._renderer._cfg.robot_diameter_mm = 300
        entity._zone_store = None
        entity._map_capability = None
        entity._mission_points = [(0.0, 0.0), (100.0, 100.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._stuck_mission_points = []
        entity._mission_start_ts = "2026-06-18T09:00:00+00:00"
        entity.vacuum_state = {}  # no bbmssn at all

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            entity._handle_mission_end(ending_phase="")  # must not raise

        for c in mock_run.call_args_list:
            c.args[0].close()

        assert gs.last_processed_nmssn == 0

    # ── _async_save_mission_checkpoint() ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_checkpoint_persists_expected_shape(self):
        entity = _make_map_entity()
        entity._mission_points = [(1.0, 2.0)]
        entity._stuck_mission_points = [(1.0, 2.0)]
        entity._mission_start_ts = "2026-06-18T09:00:00+00:00"
        entity._mission_checkpoint_mssn_strt_tm = 555
        entity._renderer.dump_state.return_value = {"r": 1}

        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_store_cls.return_value = mock_instance
            await entity._async_save_mission_checkpoint()

        mock_instance.async_save.assert_called_once_with({
            "mssn_strt_tm": 555,
            "mission_points": [(1.0, 2.0)],
            "mission_thetas": [],
            "stuck_mission_points": [(1.0, 2.0)],
            "mission_start_ts": "2026-06-18T09:00:00+00:00",
            "renderer_state": {"r": 1},
            "last_stuck_count": 0,
            "dock_anchor_buffering": False,
            "pending_segment_points": [],
            "pending_segment_thetas": [],
            "last_dock_anchor_index": 0,
        })

    @pytest.mark.asyncio
    async def test_save_checkpoint_no_config_entry_is_noop(self):
        entity = _make_map_entity()
        entity._config_entry = None
        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            await entity._async_save_mission_checkpoint()
        mock_store_cls.assert_not_called()

    # ── _async_load_pending_checkpoint() ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_load_checkpoint_sets_pending(self):
        entity = _make_map_entity()
        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(
                return_value={"mssn_strt_tm": 1, "mission_points": [(1.0, 2.0)]}
            )
            mock_store_cls.return_value = mock_instance
            await entity._async_load_pending_checkpoint()

        assert entity._pending_checkpoint == {
            "mssn_strt_tm": 1, "mission_points": [(1.0, 2.0)],
        }

    @pytest.mark.asyncio
    async def test_load_checkpoint_no_data_leaves_pending_none(self):
        entity = _make_map_entity()
        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(return_value=None)
            mock_store_cls.return_value = mock_instance
            await entity._async_load_pending_checkpoint()

        assert entity._pending_checkpoint is None

    @pytest.mark.asyncio
    async def test_load_checkpoint_handles_exception_gracefully(self):
        entity = _make_map_entity()
        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(side_effect=RuntimeError("boom"))
            mock_store_cls.return_value = mock_instance
            await entity._async_load_pending_checkpoint()  # must not raise

        assert entity._pending_checkpoint is None

    # ── _async_clear_mission_checkpoint() ────────────────────────────────

    @pytest.mark.asyncio
    async def test_clear_checkpoint_removes_store_entry(self):
        entity = _make_map_entity()
        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_store_cls.return_value = mock_instance
            await entity._async_clear_mission_checkpoint()

        mock_instance.async_remove.assert_called_once()

    # ── Integration via on_message() ─────────────────────────────────────

    def test_stuck_event_triggers_checkpoint_save(self):
        """A stuck event during an active mission must schedule a
        checkpoint save — the actual end-to-end trigger condition."""
        entity = _make_map_entity()
        entity._had_cleaning_phase = True
        entity._mission_points = [(0.0, 0.0)]

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))

        assert mock_run.call_count == 1
        mock_run.call_args.args[0].close()  # avoid "never awaited" warning

    def test_stuck_event_before_cleaning_phase_does_not_checkpoint(self):
        """No mission has actually started yet (e.g. a stray stuck count
        left over from a prior session) — nothing meaningful to save."""
        entity = _make_map_entity()
        entity._had_cleaning_phase = False

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))

        assert mock_run.call_count == 0



# ── ROOM-PALETTE (v2.9.0) ────────────────────────────────────────────────────

class TestRoomPalette:
    """ROOM-PALETTE — rotating per-room fill colours in _render_rooms_png()."""

    def _entity_with_rooms(self, room_polygons: dict) -> Any:
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        aligner = _make_aligner(aligned=True)
        aligner._room_polygons = room_polygons
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size = 600
        entity._room_render_cache_key = None
        entity._room_render_cache = None
        return entity

    def test_two_rooms_get_different_fill_colours(self):
        from custom_components.roomba_plus.image import ROOM_FILL_PALETTE
        import io
        from PIL import Image as PILImage

        room_polygons = {
            "r1": [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
            "r2": [(2500, 0), (3500, 0), (3500, 1000), (2500, 1000)],
        }
        entity = self._entity_with_rooms(room_polygons)
        png = entity._render_rooms_png()
        img = PILImage.open(io.BytesIO(png)).convert("RGB")

        # Sample a pixel well inside each room's polygon (avoiding the outline).
        px_r1 = entity._to_px_last(500, 500)
        px_r2 = entity._to_px_last(3000, 500)
        colour_r1 = img.getpixel(px_r1)
        colour_r2 = img.getpixel(px_r2)

        assert colour_r1 == ROOM_FILL_PALETTE[0]
        assert colour_r2 == ROOM_FILL_PALETTE[1]
        assert colour_r1 != colour_r2

    def test_palette_wraps_around_after_eight_rooms(self):
        from custom_components.roomba_plus.image import ROOM_FILL_PALETTE
        import io
        from PIL import Image as PILImage

        # 9 small, non-overlapping rooms spaced along the x-axis — the 9th
        # (index 8) must reuse palette[0] via modulo wraparound.
        room_polygons = {
            f"r{i}": [
                (i * 400, 0), (i * 400 + 100, 0),
                (i * 400 + 100, 100), (i * 400, 100),
            ]
            for i in range(9)
        }
        entity = self._entity_with_rooms(room_polygons)
        entity._last_x_min, entity._last_x_max = 0.0, 9 * 400 + 100
        entity._last_y_min, entity._last_y_max = 0.0, 100.0
        png = entity._render_rooms_png()
        img = PILImage.open(io.BytesIO(png)).convert("RGB")

        px_first = entity._to_px_last(50, 50)
        px_ninth = entity._to_px_last(8 * 400 + 50, 50)
        assert img.getpixel(px_first) == ROOM_FILL_PALETTE[0]
        assert img.getpixel(px_ninth) == ROOM_FILL_PALETTE[8 % len(ROOM_FILL_PALETTE)]


# ── ZONE-LAYER-CACHE (v2.9.0) ────────────────────────────────────────────────

class TestZoneLayerCache:
    """Room polygon render is cached per (pmap_version_id, aligned) instead
    of re-rendering on every async_image() call."""

    def _entity_with_rooms(self, room_polygons: dict, pmap_version_id="v1") -> Any:
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        aligner = _make_aligner(aligned=True)
        aligner._room_polygons = room_polygons
        aligner.pmap_version_id = pmap_version_id
        entity._config_entry = entry_mock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size = 600
        entity._room_render_cache_key = None
        entity._room_render_cache = None
        return entity, aligner

    ROOMS = {"r1": [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]}

    def test_second_call_returns_identical_bytes_without_recomputing(self):
        entity, aligner = self._entity_with_rooms(self.ROOMS)
        png1 = entity._render_rooms_png()
        # Sabotage the live data with a different (still non-empty) room set
        # under the SAME cache key — emptying it instead would legitimately
        # hit the separate "no polygons → blank image" early-return, which
        # tests nothing about the cache. If the cache is working, the second
        # call must still return the original png unchanged.
        aligner._room_polygons = {
            "r1": [(0, 0), (9000, 0), (9000, 9000), (0, 9000)]
        }
        png2 = entity._render_rooms_png()
        assert png2 == png1

    def test_cache_key_set_after_first_render(self):
        entity, aligner = self._entity_with_rooms(self.ROOMS, pmap_version_id="v1")
        entity._render_rooms_png()
        assert entity._room_render_cache_key == ("v1", True)
        assert entity._room_render_cache is not None

    def test_pmap_version_change_invalidates_cache(self):
        entity, aligner = self._entity_with_rooms(self.ROOMS, pmap_version_id="v1")
        entity._render_rooms_png()
        x_max_v1 = entity._last_x_max

        # Map retrain: new version id, a much larger room (different
        # bounding box) — proves the cache was actually bypassed and the
        # transform recomputed from the new data, not just key bookkeeping.
        aligner.pmap_version_id = "v2"
        aligner._room_polygons = {
            "r1": [(0, 0), (9000, 0), (9000, 9000), (0, 9000)]
        }
        entity._render_rooms_png()

        assert entity._room_render_cache_key == ("v2", True)
        assert entity._last_x_max != x_max_v1

    def test_alignment_state_change_invalidates_cache(self):
        entity, aligner = self._entity_with_rooms(self.ROOMS, pmap_version_id="v1")
        entity._render_rooms_png()
        assert entity._room_render_cache_key == ("v1", True)

        aligner._aligned = False  # falls back to UMF-space rendering mode
        entity._render_rooms_png()
        assert entity._room_render_cache_key == ("v1", False)

    def test_cached_transform_parameters_restored_on_cache_hit(self):
        entity, _ = self._entity_with_rooms(self.ROOMS)
        entity._render_rooms_png()
        x_min_after_first = entity._last_x_min
        size_after_first = entity._last_size

        # Corrupt the live transform fields to prove the second call restores
        # them from the cache entry rather than leaving stale/wrong values.
        entity._last_x_min = -99999.0
        entity._last_size = 1
        entity._render_rooms_png()

        assert entity._last_x_min == x_min_after_first
        assert entity._last_size == size_after_first


class TestDockAnchorBuffering:
    """v3.2.1 DOCK-ANCHOR — field-confirmed rationale: 3 of the last 4 real
    missions ended stuck_and_resumed/stuck_and_abandoned/error, and a stuck
    event is exactly the moment a human is most likely to have physically
    lifted and repositioned the robot — breaking vSLAM's continuous
    camera-landmark tracking. self._mission_points (which feeds
    GridStore/RoomSegStore/OutlineStore) must stop accumulating directly
    for the REST of a mission once a stuck event occurs — but unlike the
    original flag-only version, points are now BUFFERED (not dropped) and
    retroactively corrected once a dock contact confirms the true
    position (see TestDockContactConfirmed below and
    Dock_Anchor_Korrektur_Plan.md).
    """

    def test_initially_not_buffering(self):
        entity = _make_map_entity()
        assert entity._dock_anchor_buffering is False
        assert entity._pending_segment_points == []
        assert entity._pending_segment_thetas == []

    def test_stuck_event_enters_buffering(self):
        entity = _make_map_entity()
        entity._had_cleaning_phase = True
        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))
            mock_run.call_args.args[0].close()
        assert entity._dock_anchor_buffering is True

    def test_points_before_stuck_kept_points_after_are_buffered_not_dropped(self):
        entity = _make_map_entity()
        entity._had_cleaning_phase = True

        # v3.2.1 AXIS-SWAP FIX — _pose_msg(x_cm, y_cm) feeds raw firmware
        # fields; with the swap now applied, mission_points holds
        # (y_cm*10, x_cm*10), not (x_cm*10, y_cm*10) as before the fix.
        _feed_map_entity(entity, _pose_msg(100, 0))
        _feed_map_entity(entity, _pose_msg(200, 0))
        assert entity._mission_points == [(0.0, 1000.0), (0.0, 2000.0)]

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))
            mock_run.call_args.args[0].close()

        # More pose updates arrive AFTER the stuck event (robot keeps
        # moving/resuming) — these must NOT reach _mission_points directly,
        # but must NOT be silently lost either.
        _feed_map_entity(entity, _pose_msg(300, 0))
        _feed_map_entity(entity, _pose_msg(400, 0))

        assert entity._mission_points == [(0.0, 1000.0), (0.0, 2000.0)], (
            "pose points reported after a stuck event must not reach "
            "self._mission_points directly — they may be mis-oriented "
            "relative to everything recorded before the stuck event"
        )
        assert entity._pending_segment_points == [(0.0, 3000.0), (0.0, 4000.0)], (
            "post-stuck points must be BUFFERED (not dropped) for possible "
            "retroactive correction, unlike the old flag-only behaviour"
        )

    def test_live_map_visual_keeps_recording_after_stuck(self):
        """The raw MapRenderer path (add_pose) is a DIFFERENT concern
        from room recognition — it must keep showing the full path,
        including post-stuck movement, for troubleshooting value (this
        exact distinction is what surfaced the underlying issue)."""
        entity = _make_map_entity()
        entity._had_cleaning_phase = True
        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))
            mock_run.call_args.args[0].close()

        _feed_map_entity(entity, _pose_msg(300, 0))
        # v3.2.1 AXIS-SWAP FIX — with the swap now applied, _pose_msg(300, 0)
        # (raw x_cm=300, y_cm=0) maps to add_pose(x_mm=0, y_mm=3000, theta).
        entity._renderer.add_pose.assert_called_with(0.0, 3000.0, 0.0)

    def test_buffering_resets_on_new_mission_start(self):
        """A still-buffered segment from a previous mission (never
        resolved by a dock contact — stuck_and_abandoned) must not
        contaminate the NEXT mission."""
        entity = _make_map_entity()
        entity._dock_anchor_buffering = True
        entity._pending_segment_points = [(1.0, 2.0)]
        entity._pending_segment_thetas = [45.0]
        entity._had_cleaning_phase = False
        entity._last_phase = "charge"

        _feed_map_entity(entity, _map_msg("run", cycle="clean", mssn_strt_tm=999))

        assert entity._dock_anchor_buffering is False
        assert entity._pending_segment_points == []
        assert entity._pending_segment_thetas == []

    def test_checkpoint_save_persists_buffering_state(self):
        import asyncio
        entity = _make_map_entity()
        entity._mission_points = [(1.0, 2.0)]
        entity._stuck_mission_points = []
        entity._mission_start_ts = "2026-07-03T09:00:00+00:00"
        entity._mission_checkpoint_mssn_strt_tm = 42
        entity._dock_anchor_buffering = True
        entity._pending_segment_points = [(5.0, 6.0)]
        entity._pending_segment_thetas = [90.0]
        entity._last_dock_anchor_index = 1
        entity._renderer.dump_state.return_value = {}

        with patch("custom_components.roomba_plus.image.Store") as mock_store_cls:
            mock_instance = AsyncMock()
            mock_store_cls.return_value = mock_instance
            asyncio.get_event_loop().run_until_complete(
                entity._async_save_mission_checkpoint()
            )

        saved = mock_instance.async_save.call_args.args[0]
        assert saved["dock_anchor_buffering"] is True
        assert saved["pending_segment_points"] == [(5.0, 6.0)]
        assert saved["pending_segment_thetas"] == [90.0]
        assert saved["last_dock_anchor_index"] == 1

    def test_checkpoint_restore_resumes_buffering_state(self):
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 42}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 42,
            "mission_points": [(1.0, 2.0)],
            "stuck_mission_points": [],
            "mission_start_ts": "2026-07-03T09:00:00+00:00",
            "renderer_state": None,
            "last_stuck_count": 1,
            "dock_anchor_buffering": True,
            "pending_segment_points": [(5.0, 6.0)],
            "pending_segment_thetas": [90.0],
            "last_dock_anchor_index": 1,
        }
        entity._consume_pending_checkpoint()
        assert entity._dock_anchor_buffering is True
        assert entity._pending_segment_points == [(5.0, 6.0)]
        assert entity._pending_segment_thetas == [90.0]
        assert entity._last_dock_anchor_index == 1

    def test_checkpoint_restore_defaults_for_old_payloads(self):
        """Additive fields — a checkpoint saved before this existed simply
        has no such keys, must default cleanly, not raise."""
        entity = _make_map_entity()
        entity.vacuum_state = {"cleanMissionStatus": {"mssnStrtTm": 42}}
        entity._pending_checkpoint = {
            "mssn_strt_tm": 42,
            "mission_points": [(1.0, 2.0)],
            "stuck_mission_points": [],
            "mission_start_ts": "2026-07-03T09:00:00+00:00",
            "renderer_state": None,
            "last_stuck_count": 0,
        }
        entity._consume_pending_checkpoint()
        assert entity._dock_anchor_buffering is False
        assert entity._pending_segment_points == []
        assert entity._pending_segment_thetas == []
        assert entity._last_dock_anchor_index == 0


class TestComputeDockCorrection:
    """v3.2.1 DOCK-ANCHOR — _compute_dock_correction: pure function,
    automatic v1 (translation-only) / v2 (translation+rotation) upgrade
    based on whether dock_theta_baseline is available. See
    Dock_Anchor_Korrektur_Plan.md.
    """

    def test_translation_only_when_baseline_none(self):
        from custom_components.roomba_plus.image import _compute_dock_correction
        dx, dy, rot = _compute_dock_correction((300.0, 150.0), 90.0, None)
        assert (dx, dy) == (-300.0, -150.0)
        assert rot == 0.0

    def test_zero_correction_when_already_at_dock(self):
        from custom_components.roomba_plus.image import _compute_dock_correction
        dx, dy, rot = _compute_dock_correction((0.0, 0.0), 45.0, None)
        assert (dx, dy) == (0.0, 0.0)
        assert rot == 0.0

    def test_rotation_included_when_baseline_available(self):
        from custom_components.roomba_plus.image import _compute_dock_correction
        dx, dy, rot = _compute_dock_correction((100.0, 0.0), 90.0, 90.0)
        # theta already matches baseline -> zero rotation needed
        assert rot == pytest.approx(0.0, abs=1e-9)
        assert (dx, dy) == pytest.approx((-100.0, 0.0))

    def test_nonzero_rotation_when_theta_differs_from_baseline(self):
        from custom_components.roomba_plus.image import _compute_dock_correction
        dx, dy, rot = _compute_dock_correction((100.0, 0.0), 0.0, 90.0)
        # baseline says heading should be 90, measured was 0 -> 90 deg rotation
        assert rot == pytest.approx(math.radians(90.0))
        # (100,0) rotated +90deg -> (0,100), correction is the negation
        assert dx == pytest.approx(0.0, abs=1e-9)
        assert dy == pytest.approx(-100.0)


class TestApplyDockCorrection:
    def test_pure_translation(self):
        from custom_components.roomba_plus.image import _apply_dock_correction
        assert _apply_dock_correction((10.0, 20.0), 5.0, -5.0, 0.0) == (15.0, 15.0)

    def test_rotation_then_translation_order(self):
        from custom_components.roomba_plus.image import _apply_dock_correction
        x, y = _apply_dock_correction((1.0, 0.0), 0.0, 0.0, math.radians(90.0))
        assert x == pytest.approx(0.0, abs=1e-9)
        assert y == pytest.approx(1.0)


class TestInterpolateAndCorrectSegment:
    """v3.2.1 DOCK-ANCHOR (4c) — proportional interpolation across a
    buffered segment: weight 0 at the first point, weight 1 at the last."""

    def test_empty_segment_returns_empty(self):
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        assert _interpolate_and_correct_segment([], 10.0, 10.0, 0.0) == []

    def test_single_point_gets_full_correction(self):
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        result = _interpolate_and_correct_segment([(0.0, 0.0)], 10.0, -5.0, 0.0)
        assert result == [(10.0, -5.0)]

    def test_first_point_gets_zero_correction(self):
        """The buffered segment's first point is still anchored to the
        last trusted pre-stuck position — must stay effectively
        unchanged (weight 0), not shifted by the full vector."""
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        result = _interpolate_and_correct_segment(
            [(100.0, 100.0), (200.0, 200.0), (300.0, 300.0)], 30.0, 30.0, 0.0,
        )
        assert result[0] == pytest.approx((100.0, 100.0))

    def test_last_point_gets_full_correction(self):
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        result = _interpolate_and_correct_segment(
            [(100.0, 100.0), (200.0, 200.0), (300.0, 300.0)], 30.0, 30.0, 0.0,
        )
        assert result[-1] == pytest.approx((330.0, 330.0))

    def test_middle_point_gets_proportional_correction(self):
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        result = _interpolate_and_correct_segment(
            [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)], 30.0, 0.0, 0.0,
        )
        # 3 points -> weights 0, 0.5, 1.0
        assert result[1] == pytest.approx((10.0 + 15.0, 0.0))

    def test_monotonic_growth_across_many_points(self):
        """Each successive point's correction magnitude must be >= the
        previous one's — no discontinuous jumps at internal accepted-jump
        positions (v1: jumps don't change the interpolation shape)."""
        from custom_components.roomba_plus.image import _interpolate_and_correct_segment
        points = [(float(i), 0.0) for i in range(10)]
        result = _interpolate_and_correct_segment(points, 100.0, 0.0, 0.0)
        corrections_x = [r[0] - p[0] for r, p in zip(result, points)]
        assert corrections_x == sorted(corrections_x)


class TestHandleDockContactConfirmed:
    """v3.2.1 DOCK-ANCHOR — _handle_dock_contact_confirmed(): the actual
    correction-application logic for both Fall A (buffered, after a
    stuck event) and Fall B (direct, no buffering). Called directly
    here — the debounce that triggers it is covered separately by
    TestImageEndDebounceV281 (mission-end path) and would need
    _time_mod mocking to also exercise the Fall-B mid-mission path,
    which is orthogonal to what this class actually verifies.
    """

    def test_fall_a_buffered_segment_is_corrected_and_merged(self):
        entity = _make_map_entity()
        entity._mission_points = [(0.0, 0.0), (100.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._dock_anchor_buffering = True
        entity._pending_segment_points = [(200.0, 0.0), (300.0, 0.0)]
        entity._pending_segment_thetas = [0.0, 0.0]
        entity._renderer.point_count = 4

        entity._handle_dock_contact_confirmed()

        # buffer resolved and cleared
        assert entity._dock_anchor_buffering is False
        assert entity._pending_segment_points == []
        assert entity._pending_segment_thetas == []
        # pre-stuck points (index 0,1) untouched; buffered points appended,
        # last one corrected to (0,0) (translation-only, no baseline)
        assert entity._mission_points[:2] == [(0.0, 0.0), (100.0, 0.0)]
        assert len(entity._mission_points) == 4
        assert entity._mission_points[-1] == pytest.approx((0.0, 0.0))
        # first buffered point (weight 0) stays effectively unmoved
        assert entity._mission_points[2] == pytest.approx((200.0, 0.0))

    def test_fall_a_does_not_feed_dock_theta_baseline(self):
        """A buffered (disturbed) dock contact must NOT be treated as a
        clean observation for dock_theta_baseline — see
        RobotProfileStore.update_dock_theta_baseline's docstring."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps
        entity._dock_anchor_buffering = True
        entity._pending_segment_points = [(50.0, 0.0)]
        entity._pending_segment_thetas = [10.0]

        entity._handle_dock_contact_confirmed()

        assert rps.dock_theta_count == 0

    def test_fall_b_corrects_segment_since_last_anchor_in_place(self):
        entity = _make_map_entity()
        entity._mission_points = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0, 0.0]
        entity._last_dock_anchor_index = 1
        entity._dock_anchor_buffering = False
        entity._renderer.point_count = 3

        entity._handle_dock_contact_confirmed()

        # index 0 (before the anchor) untouched
        assert entity._mission_points[0] == (0.0, 0.0)
        # segment [1:] corrected -> last point pulled to (0,0)
        assert entity._mission_points[-1] == pytest.approx((0.0, 0.0))
        assert entity._last_dock_anchor_index == len(entity._mission_points)

    def test_fall_b_feeds_dock_theta_baseline(self):
        """A direct (undisturbed) dock contact IS a clean observation."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps
        entity._mission_points = [(10.0, 0.0)]
        entity._mission_thetas = [33.0]
        entity._last_dock_anchor_index = 0
        entity._dock_anchor_buffering = False

        entity._handle_dock_contact_confirmed()

        assert rps.dock_theta_count == 1
        assert rps.dock_theta_baseline == pytest.approx(33.0)

    def test_empty_segment_is_a_safe_noop(self):
        """Fall B with nothing new since the last anchor — must not
        crash or misbehave."""
        entity = _make_map_entity()
        entity._mission_points = [(0.0, 0.0)]
        entity._mission_thetas = [0.0]
        entity._last_dock_anchor_index = 1  # nothing after this index
        entity._dock_anchor_buffering = False

        entity._handle_dock_contact_confirmed()

        assert entity._mission_points == [(0.0, 0.0)]
        assert entity._last_dock_anchor_index == 1

    def test_feeds_geometry_store_record_drift(self):
        entity = _make_map_entity()
        geometry_store = MagicMock()
        geometry_store.record_drift.return_value = False
        geometry_store.async_save = AsyncMock()
        entity._config_entry.runtime_data.geometry_store = geometry_store
        entity._mission_points = [(0.0, 0.0), (150.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._last_dock_anchor_index = 0
        entity._dock_anchor_buffering = False

        # CLOSES THE COROUTINE. A bare patch swallows the call but the
        # coroutine argument was already built, and dropping it is
        # reported as "never awaited" in whichever later test happens
        # to trigger collection.
        with patch.object(
            entity._config_entry, "async_create_task",
            side_effect=lambda _hass, coro, *a, **k: coro.close(),
        ):
            entity._handle_dock_contact_confirmed()

        geometry_store.record_drift.assert_called_once_with(-150.0, 0.0)

    def test_live_map_replace_range_called_with_best_effort_index(self):
        entity = _make_map_entity()
        entity._mission_points = [(0.0, 0.0), (100.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._last_dock_anchor_index = 0
        entity._dock_anchor_buffering = False
        entity._renderer.point_count = 2

        entity._handle_dock_contact_confirmed()

        entity._renderer.replace_range.assert_called_once()
        start_index_arg = entity._renderer.replace_range.call_args.args[0]
        assert start_index_arg == 0  # point_count(2) - segment_len(2) = 0


class TestMissionStartDockThetaBaselineCapture:
    """v3.2.1 DOCK-ANCHOR — the first (0,0) pose reading of a mission,
    otherwise entirely discarded by MapRenderer.add_pose()'s own skip
    logic, is captured here as an additional clean dock_theta_baseline
    sample — the robot is certainly at the dock, stationary, before any
    possible disturbance this mission.
    """

    def test_first_pose_at_dock_feeds_baseline(self):
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps

        _feed_map_entity(entity, _pose_msg(0, 0, theta=77.0))

        assert rps.dock_theta_count == 1
        assert rps.dock_theta_baseline == pytest.approx(77.0)

    def test_first_pose_at_dock_still_recorded_in_mission_points(self):
        """Capturing the baseline sample must not change the existing
        behaviour of recording this point in _mission_points."""
        entity = _make_map_entity()
        _feed_map_entity(entity, _pose_msg(0, 0, theta=10.0))
        assert entity._mission_points == [(0.0, 0.0)]
        assert entity._mission_thetas == [10.0]

    def test_second_pose_even_if_also_zero_zero_does_not_double_feed(self):
        """Only the FIRST point of the mission counts as the clean
        dock-departure sample — a later (coincidental) return to exactly
        (0,0) mid-mission must not be mistaken for another departure."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps

        _feed_map_entity(entity, _pose_msg(0, 0, theta=77.0))
        _feed_map_entity(entity, _pose_msg(0, 0, theta=99.0))

        assert rps.dock_theta_count == 1
        assert rps.dock_theta_baseline == pytest.approx(77.0)

    def test_nonzero_first_pose_does_not_feed_baseline(self):
        """Only a genuine (0,0) start counts — some other first reading
        (e.g. resuming mid-mission after an HA restart) must not be
        mistaken for a clean dock departure."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps

        _feed_map_entity(entity, _pose_msg(50, 0, theta=77.0))

        assert rps.dock_theta_count == 0

    def test_no_capture_while_buffering(self):
        """Defensive: buffering should never coincide with 'first point
        of the mission' in practice (buffering only starts after a
        stuck event mid-mission), but the guard must hold regardless."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps
        entity._dock_anchor_buffering = True

        _feed_map_entity(entity, _pose_msg(0, 0, theta=77.0))

        assert rps.dock_theta_count == 0

    def test_no_robot_profile_store_does_not_crash(self):
        entity = _make_map_entity()
        entity._config_entry.runtime_data.robot_profile_store = None
        _feed_map_entity(entity, _pose_msg(0, 0, theta=77.0))  # must not raise
        assert entity._mission_points == [(0.0, 0.0)]


class TestDockAnchorSmartRobotExclusion:
    """v3.2.1 DOCK-ANCHOR — field-confirmed gap: the whole mechanism
    (buffering, dock-contact debounce, mission-start baseline capture)
    originally had NO map_capability gate at all, unlike the old
    _check_dock_drift block it's meant to consolidate with (which was
    always EPHEMERAL-only). SMART robots get authoritative room data
    from the cloud's own persistent map — GridStore/RoomSegStore/
    OutlineStore (the actual beneficiaries) are themselves EPHEMERAL-
    only constructs, so this must not run for SMART at all.
    """

    def test_stuck_event_does_not_enter_buffering_for_smart(self):
        entity = _make_map_entity()
        entity._map_capability = MapCapability.SMART
        entity._had_cleaning_phase = True
        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))
            mock_run.call_args.args[0].close()
        assert entity._dock_anchor_buffering is False

    def test_mission_start_pose_does_not_feed_baseline_for_smart(self):
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        entity._map_capability = MapCapability.SMART
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps

        _feed_map_entity(entity, _pose_msg(0, 0, theta=77.0))

        assert rps.dock_theta_count == 0

    def test_handle_dock_contact_confirmed_is_a_noop_for_smart(self):
        """Defensive guard inside the handler itself."""
        entity = _make_map_entity()
        entity._map_capability = MapCapability.SMART
        entity._mission_points = [(0.0, 0.0), (100.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._last_dock_anchor_index = 0

        entity._handle_dock_contact_confirmed()

        assert entity._mission_points == [(0.0, 0.0), (100.0, 0.0)], (
            "SMART robots must be completely unaffected by dock-anchor "
            "correction"
        )

    def test_sanity_check_ephemeral_still_works(self):
        """Positive control: proves this test class actually exercises
        the gate, not vacuously passing because nothing would have
        fired anyway."""
        entity = _make_map_entity()
        assert entity._map_capability == MapCapability.EPHEMERAL
        entity._had_cleaning_phase = True
        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))
            mock_run.call_args.args[0].close()
        assert entity._dock_anchor_buffering is True


class TestCheckpointSavedAtDockContactResolution:
    """v3.2.1 DOCK-ANCHOR — checkpoint must be saved not just when
    entering BUFFERING (stuck event) but also when it RESOLVES (dock
    contact confirmed) — otherwise an HA restart between a successful
    resolution and the next stuck event would restore the stale,
    pre-resolution checkpoint (still buffering, original uncorrected
    segment), silently reverting the correction and losing whatever
    _mission_points accumulated since.
    """

    def test_checkpoint_saved_on_resolution_when_mission_still_active(self):
        entity = _make_map_entity()
        entity._had_cleaning_phase = True
        entity._mission_points = [(0.0, 0.0), (150.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._last_dock_anchor_index = 0
        entity._dock_anchor_buffering = False

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            entity._handle_dock_contact_confirmed()

        checkpoint_calls = [
            c for c in mock_run.call_args_list
            if "_async_save_mission_checkpoint" in repr(c)
        ]
        assert len(checkpoint_calls) == 1

    def test_no_checkpoint_saved_if_mission_already_ended(self):
        """Once _had_cleaning_phase is False (mission genuinely over,
        e.g. this dock contact coincided with the final end), there is
        no in-progress mission left to checkpoint."""
        entity = _make_map_entity()
        entity._had_cleaning_phase = False
        entity._mission_points = [(0.0, 0.0), (150.0, 0.0)]
        entity._mission_thetas = [0.0, 0.0]
        entity._last_dock_anchor_index = 0
        entity._dock_anchor_buffering = False

        with patch.object(
                entity._config_entry, "async_create_task",
                side_effect=lambda _hass, coro, *a, **k: coro.close(),
            ) as mock_run:
            entity._handle_dock_contact_confirmed()

        checkpoint_calls = [
            c for c in mock_run.call_args_list
            if "_async_save_mission_checkpoint" in repr(c)
        ]
        assert len(checkpoint_calls) == 0


class TestAxisSwapFix:
    """v3.2.1 AXIS-SWAP FIX — roombapy's own source (roomba.py) documents
    `pose_point_x -> co_ords["y"]`, `pose_point_y -> co_ords["x"]`
    ("# x and y are reversed..."). _handle_pose() must apply this swap
    at the single point raw firmware fields enter the system. Confirmed
    independently NOT to be the explanation for the "live map doesn't
    match room layout" symptom investigated this session (that was
    vSLAM continuity loss after stuck events, see Dock_Anchor_Korrektur_
    Plan.md) — fixed anyway as a real, independently-confirmed
    discrepancy from the documented convention.
    """

    def test_raw_x_field_becomes_mission_points_y(self):
        entity = _make_map_entity()
        _feed_map_entity(entity, _pose_msg(x_cm=123, y_cm=0))
        assert entity._mission_points == [(0.0, 1230.0)]

    def test_raw_y_field_becomes_mission_points_x(self):
        entity = _make_map_entity()
        _feed_map_entity(entity, _pose_msg(x_cm=0, y_cm=456))
        assert entity._mission_points == [(4560.0, 0.0)]

    def test_dock_start_still_recognised_as_zero_zero_after_swap(self):
        """(0,0) is symmetric under a swap — the mission-start dock skip
        (MapRenderer) and dock_theta_baseline capture (image.py) must
        both still correctly recognise a genuine dock start."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        entity = _make_map_entity()
        rps = RobotProfileStore()
        entity._config_entry.runtime_data.robot_profile_store = rps

        _feed_map_entity(entity, _pose_msg(x_cm=0, y_cm=0, theta=42.0))

        assert entity._mission_points == [(0.0, 0.0)]
        assert rps.dock_theta_count == 1
        assert rps.dock_theta_baseline == pytest.approx(42.0)


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 NULL-REGRESSION — explicit MQTT nulls through on_message()
# ─────────────────────────────────────────────────────────────────────────────

class TestNullRegressionExplicitNulls:
    """v3.3.0 NULL-REGRESSION — lewis firmware sends explicit `null` for
    entire state objects (v3.2.0 review class: `.get(key, {})` guards the
    MISSING key, not the null VALUE). These tests push explicit nulls
    through the real on_message() path so a refactor reverting the
    `(x or {})` idiom fails loudly instead of crashing the MQTT thread."""

    def _null_msg(self, key: str) -> dict:
        return {"state": {"reported": {key: None}}}

    def test_clean_mission_status_explicit_null(self):
        entity = _make_map_entity()
        entity.vacuum.master_state = {
            "state": {"reported": {"cleanMissionStatus": None}}
        }
        entity.on_message(self._null_msg("cleanMissionStatus"))  # must not raise
        assert entity._last_phase == ""

    def test_bbrun_explicit_null(self):
        entity = _make_map_entity()
        entity._last_stuck_count = 0
        entity.vacuum.master_state = {"state": {"reported": {"bbrun": None}}}
        entity.on_message(self._null_msg("bbrun"))  # must not raise
        entity._renderer.mark_stuck.assert_not_called()

    def test_mssn_strt_tm_explicit_null_inside_status(self):
        entity = _make_map_entity()
        status = {"phase": "run", "cycle": "quick", "mssnStrtTm": None}
        entity.vacuum.master_state = {
            "state": {"reported": {"cleanMissionStatus": status}}
        }
        entity.on_message(
            {"state": {"reported": {"cleanMissionStatus": status}}}
        )  # `or 0` fallback path — must not raise
        assert entity._last_phase == "run"


class TestPrimeMapImage:
    """NEW (V4/Prime live map) -- minimal coverage of the core
    behavior, not an exhaustive suite: async_image() returns the
    cached PNG once a live map update has been processed, and falls
    back to a valid blank image before the first one arrives."""

    def _entity(self):
        from custom_components.roomba_plus.image import PrimeMapImage
        entity = object.__new__(PrimeMapImage)
        entity._png_bytes = None
        return entity

    @pytest.mark.asyncio
    async def test_async_image_returns_blank_before_first_update(self):
        entity = self._entity()
        result = await entity.async_image()
        assert result is not None
        assert result.startswith(b"\x89PNG")

    @pytest.mark.asyncio
    async def test_async_image_returns_cached_png_after_update(self):
        entity = self._entity()
        entity._png_bytes = b"fake-png-bytes"
        result = await entity.async_image()
        assert result == b"fake-png-bytes"


class TestPrimeMapImageWatchRetry:
    """_async_watch_live_map()'s own outer retry loop -- REAL BUG FOUND
    AND FIXED (architecture review, not a field report): this had the
    exact same missing-retry gap already found and fixed twice before
    in PrimeCoordinator/PrimeStatusCoordinator, but this entity had
    NEVER actually run before this session (Platform.IMAGE was missing
    from PRIME_PLATFORMS the whole time), so the bug never had a
    chance to surface via real usage the way the other two did."""

    def _entity(self, prime_robot=None, config_entry=None):
        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity._png_bytes = None
        entity._blid = "TESTBLID"
        entity.hass = hass_mock()
        entity._prime_robot = prime_robot or MagicMock()
        entity._config_entry = config_entry or MagicMock()
        return entity

    @pytest.mark.asyncio
    async def test_retries_after_unexpected_exception_instead_of_dying_permanently(self):
        entity = self._entity()
        call_count = 0

        def fake_watch_live_map():
            nonlocal call_count
            call_count += 1

            async def _gen():
                if call_count == 1:
                    raise RuntimeError("simulated unexpected failure")
                    yield  # pragma: no cover -- unreachable, makes this an async generator
                raise asyncio.CancelledError

            return _gen()

        entity._prime_robot.watch_live_map = fake_watch_live_map

        with patch("asyncio.sleep", new=AsyncMock()), patch(
            "custom_components.roomba_plus.image.async_get_clientsession", return_value=MagicMock()
        ):
            with pytest.raises(asyncio.CancelledError):
                await entity._async_watch_live_map()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_normal_generator_completion_also_retries_not_tight_loops(self):
        """The SAME lesson already learned twice before: a generator
        ending WITHOUT an exception is also anomalous (it's meant to
        run forever) and must get the same backoff, not an immediate,
        undelayed re-call that would busy-loop."""
        entity = self._entity()
        call_count = 0

        def fake_watch_live_map():
            nonlocal call_count
            call_count += 1

            async def _gen():
                if call_count >= 2:
                    raise asyncio.CancelledError
                return
                yield  # pragma: no cover -- unreachable, makes this an async generator

            return _gen()

        entity._prime_robot.watch_live_map = fake_watch_live_map
        sleep_calls = []

        async def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("asyncio.sleep", new=_fake_sleep), patch(
            "custom_components.roomba_plus.image.async_get_clientsession", return_value=MagicMock()
        ):
            with pytest.raises(asyncio.CancelledError):
                await entity._async_watch_live_map()

        assert call_count == 2
        assert sleep_calls == [5.0]  # backoff was actually applied, not skipped


class TestPrimeMapImageBackgroundTask:
    """CONSISTENCY FIX (this session): async_added_to_hass() previously
    used a bare asyncio.create_task() -- every other background task
    in this project uses config_entry.async_create_background_task()
    instead, which ties the task's lifetime to the config entry itself
    (auto-cancelled on unload/reload by HA's own framework)."""

    @pytest.mark.asyncio
    async def test_uses_config_entry_background_task_not_bare_asyncio_create_task(self):
        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity._blid = "TESTBLID"
        entity._prime_robot = MagicMock()
        entity._config_entry = entry_mock()
        # async_create_background_task() receives the coroutine as an argument
        # but (being a MagicMock) never awaits/schedules it -- close it
        # explicitly so this test doesn't leak a "coroutine was never
        # awaited" warning, same pattern already used elsewhere in this suite.
        entity._config_entry.async_create_background_task.side_effect = (
            lambda hass, coro, name, **kw: coro.close()
        )
        entity.hass = hass_mock()
        entity.access_tokens = None

        with patch.object(IRobotEntity, "async_added_to_hass", new=AsyncMock()), patch.object(
            PrimeMapImage, "async_update_token",
        ):
            await entity.async_added_to_hass()

        entity._config_entry.async_create_background_task.assert_called_once()
        call_kwargs = entity._config_entry.async_create_background_task.call_args
        assert call_kwargs.kwargs.get("name") == "roomba_plus_prime_live_map_TESTBLID"


class TestMissionEndStepOrder:
    """`_handle_mission_end` is order-sensitive, and getting it wrong
    has shipped as a real bug four times -- v2.8.2 plus three separate
    fixes in v3.2.1.

    None of those would have been caught by a test, because the
    dependencies flow through shared stores as side effects rather than
    through values. Nothing fails; a store is simply fed something
    empty, or fed before the data it needs exists, and the symptom
    surfaces later as a missing room outline or a lost trajectory.

    Reading the source is a blunt instrument, and deliberately so: the
    constraint IS textual ordering, so that is what gets asserted. A
    behavioural test would need seven stores wired together to observe
    the same thing."""

    def _source(self) -> str:
        import ast
        import inspect

        from custom_components.roomba_plus import image

        src = inspect.getsource(image)
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "_handle_mission_end":
                return "\n".join(lines[node.lineno - 1 : node.end_lineno])
        raise AssertionError("_handle_mission_end not found")

    def _line_of(self, needle: str) -> int:
        body = self._source()
        for i, line in enumerate(body.splitlines()):
            if needle in line and not line.strip().startswith("#"):
                return i
        raise AssertionError(f"{needle!r} not found in _handle_mission_end")

    def test_grid_is_fed_before_the_trajectory_is_recorded(self):
        """Both read the same pose list. GridStore consumes it first;
        the trajectory store records the same points afterwards."""
        assert self._line_of("grid_store.update_from_mission") < self._line_of(
            "trajectory_store.record_mission"
        )

    def test_the_trajectory_is_recorded_before_the_points_are_cleared(self):
        """THE v3.2.1 fix. Clearing first leaves the store recording an
        empty mission -- no error, no warning, just a permanently blank
        trajectory for that run."""
        assert self._line_of("trajectory_store.record_mission") < self._line_of(
            "self._mission_points = []"
        )

    def test_the_ordering_contract_is_stated_at_the_top(self):
        """The constraints were previously discoverable only by reading
        all fourteen comment blocks in the body. If someone removes the
        summary, the next ordering bug becomes as expensive to find as
        the last four were."""
        import inspect

        from custom_components.roomba_plus.image import RoombaMapImage

        doc = inspect.getdoc(RoombaMapImage._handle_mission_end) or ""

        assert "ORDER-SENSITIVE" in doc
        assert "BEFORE clearing" in doc, "the pose-clearing constraint must stay stated"
        assert "GridStore" in doc, "the store-ordering constraint must stay stated"


class TestPrimeMapSurvivesRestart:
    """The last map is stored and restored.

    Prime shows a PNG that iRobot renders, and that arrives only during
    and after a mission -- so a restart, reload or update leaves the
    entity with nothing until the robot next runs. Two testers hit this
    within minutes of updating to a11.

    The a11 change to report `unavailable` rather than a blank white
    square did not cause it; it made it visible. Both states mean "no
    map", but one of them looks like a broken image and the other says
    so."""

    def _entity(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity.hass = hass_mock()
        entity._png_bytes = None
        entity._map_stored_at = None
        entity._map_store = None
        entity._config_entry = MagicMock(entry_id="entry1")
        return entity

    @pytest.mark.asyncio
    async def test_a_stored_map_is_restored(self):
        import base64
        from unittest.mock import AsyncMock, patch

        entity = self._entity()
        payload = {
            "png_b64": base64.b64encode(b"PNGDATA").decode("ascii"),
            "saved_at": "2026-07-28T10:00:00+00:00",
        }

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value.async_load = AsyncMock(return_value=payload)
            await entity._async_restore_png()

        assert entity._png_bytes == b"PNGDATA"
        assert entity._map_stored_at == "2026-07-28T10:00:00+00:00"

    @pytest.mark.asyncio
    async def test_no_stored_map_leaves_the_entity_blank(self):
        """First run on a new install. Unavailable is the honest state."""
        from unittest.mock import AsyncMock, patch

        entity = self._entity()

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value.async_load = AsyncMock(return_value=None)
            await entity._async_restore_png()

        assert entity._png_bytes is None

    @pytest.mark.asyncio
    async def test_a_corrupt_stored_map_does_not_raise(self):
        """Truncated storage should cost the map, not the whole image
        platform. Everything else on that entity keeps working."""
        from unittest.mock import AsyncMock, patch

        entity = self._entity()

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value.async_load = AsyncMock(
                return_value={"png_b64": "not valid base64!!!"}
            )
            await entity._async_restore_png()

        assert entity._png_bytes is None

    @pytest.mark.asyncio
    async def test_a_storage_failure_does_not_raise(self):
        """Disk problems are not this entity's to solve, and a map is
        not worth failing setup over."""
        from unittest.mock import AsyncMock, patch

        entity = self._entity()

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value.async_load = AsyncMock(side_effect=OSError("disk"))
            await entity._async_restore_png()

        assert entity._png_bytes is None

    def test_saving_records_when_it_was_saved(self):
        """So a restored map can be shown as what it is -- the most
        recent one, not necessarily a current one."""
        from unittest.mock import patch

        entity = self._entity()
        entity._map_store = None
        entity._png_bytes = b"PNGDATA"

        with patch("custom_components.roomba_plus.image.Store"):
            entity._async_save_png()

        payload = entity._map_save_payload()
        assert payload["saved_at"]
        assert payload["png_b64"]

    def test_writes_are_delayed_rather_than_immediate(self):
        """A mission produces ~26 frames and only the last is ever read
        back. Writing each one meant roughly 17 MB per mission of pure
        flash wear, on hardware that is commonly an SD card."""
        from unittest.mock import MagicMock, patch

        entity = self._entity()
        entity._map_store = None
        entity._png_bytes = b"PNGDATA"

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store = MagicMock()
            store_cls.return_value = store
            entity._async_save_png()

        store.async_delay_save.assert_called_once()
        store.async_save.assert_not_called()

    def test_the_store_is_cached_across_frames(self):
        """A fresh Store each call would restart the delay timer every
        frame and defeat the coalescing entirely."""
        from unittest.mock import MagicMock, patch

        entity = self._entity()
        entity._map_store = None
        entity._png_bytes = b"PNGDATA"

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value = MagicMock()
            entity._async_save_png()
            entity._async_save_png()
            entity._async_save_png()

        assert store_cls.call_count == 1

    def test_the_payload_is_taken_when_the_delay_fires(self):
        """The callback reads the CURRENT frame, so a burst of frames
        persists the newest rather than the one that scheduled it."""
        entity = self._entity()
        entity._png_bytes = b"FIRST"

        callback_payload_later = entity._map_save_payload
        entity._png_bytes = b"NEWEST"

        import base64
        assert base64.b64decode(callback_payload_later()["png_b64"]) == b"NEWEST"

    def test_nothing_is_scheduled_when_there_is_no_map(self):
        """Avoids replacing a good stored map with an empty one."""
        from unittest.mock import MagicMock, patch

        entity = self._entity()
        entity._map_store = None

        with patch("custom_components.roomba_plus.image.Store") as store_cls:
            store_cls.return_value = MagicMock()
            entity._async_save_png()

        store_cls.assert_not_called()

    def test_the_prime_storage_key_differs_from_the_classic_one(self):
        """Different contents entirely -- a PNG against renderer state.
        A shared key would make one silently unreadable as the other."""
        from custom_components.roomba_plus.image import (
            _map_storage_key,
            _prime_map_storage_key,
        )

        assert _prime_map_storage_key("e1") != _map_storage_key("e1")


class TestPrimeMapFlushOnRemoval:
    """The delayed map write is flushed when the entity goes away.

    async_delay_save means a reload can leave the OLD entity's pending
    write to land after the NEW one has already loaded -- overwriting a
    current map with a stale one, which is worse than losing an update.
    Store.async_save cancels the pending timer as well as writing, so it
    is both flush and guard.

    Classic solved exactly this for MissionTimerStore in v3.3.0, in its
    own bug hunt. This entity was written today with the delayed saving
    copied from that store, and the flush was not copied with it."""

    def _entity(self, *, has_store=True, has_png=True):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity._watch_task = None
        entity._png_bytes = b"PNGDATA" if has_png else None
        entity._map_stored_at = None
        entity._map_store = MagicMock(async_save=AsyncMock()) if has_store else None
        entity._config_entry = MagicMock(entry_id="e1")
        entity.hass = hass_mock()
        return entity

    @pytest.mark.asyncio
    async def test_a_pending_write_is_flushed(self):
        entity = self._entity()

        await entity.async_will_remove_from_hass()

        entity._map_store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nothing_is_written_without_a_map(self):
        """Would replace a good stored map with an empty one."""
        entity = self._entity(has_png=False)

        await entity.async_will_remove_from_hass()

        entity._map_store.async_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_store_yet_does_not_raise(self):
        """Removal can happen before any frame arrived, so before the
        store was ever created."""
        entity = self._entity(has_store=False)

        await entity.async_will_remove_from_hass()

    @pytest.mark.asyncio
    async def test_a_failing_flush_does_not_block_removal(self):
        """Unload must complete. A stale map is recoverable; a config
        entry stuck mid-unload is not."""
        entity = self._entity()
        entity._map_store.async_save.side_effect = OSError("disk full")

        await entity.async_will_remove_from_hass()

    @pytest.mark.asyncio
    async def test_the_watch_task_is_cancelled_and_waited_for(self):
        """The flush was inserted ahead of existing cleanup -- which must
        still happen, or a reload leaks a background task per cycle.

        AND THE TASK IS AWAITED. `cancel()` only requests a stop; it
        returns before the task has unwound, so the livemap unsubscribe
        in watch_live_map()'s finally could still be pending when
        async_unload_entry disconnects the robot on the next line.

        This asserted only the cancel, which is why the missing await
        went unnoticed.
        """
        import asyncio

        entity = self._entity()

        async def _forever():
            await asyncio.sleep(3600)

        task = asyncio.ensure_future(_forever())
        entity._watch_task = task

        await entity.async_will_remove_from_hass()

        assert task.cancelled(), "the task was cancelled but never awaited"

    @pytest.mark.asyncio
    async def test_a_cancelled_task_does_not_raise_out_of_removal(self):
        """Cancellation is the expected outcome here, not an error --
        letting CancelledError escape would abort the rest of the unload
        and leave the robot connected."""
        import asyncio

        entity = self._entity()
        task = asyncio.ensure_future(asyncio.sleep(3600))
        entity._watch_task = task

        await entity.async_will_remove_from_hass()


def test_prime_cleaning_map_does_not_advertise_card_rooms():
    """Only the static Rooms Map is a xiaomi-vacuum-map-card source."""
    from custom_components.roomba_plus.image import PrimeRoomsImage

    entity = PrimeRoomsImage.__new__(PrimeRoomsImage)
    entity._include_live = True

    assert entity.extra_state_attributes == {}


def test_prime_rooms_map_uses_stable_ids_for_generated_room_config():
    """The card generator uses the rooms mapping key as its selection ID."""
    from custom_components.roomba_plus.image import PrimeRoomsImage

    entity = PrimeRoomsImage.__new__(PrimeRoomsImage)
    entity._include_live = False
    entity._polygons = {"10": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]}
    entity._names = {"10": "Kitchen"}
    entity._preferences = {}
    entity._renderer = MagicMock()
    entity._renderer._mm_to_px_fit.side_effect = lambda x, y: (x, y)

    rooms = entity.extra_state_attributes["rooms"]
    assert list(rooms) == ["10"]
    assert rooms["10"]["name"] == "Kitchen"
    assert rooms["10"]["room_id"] == "10"


class TestRoomLabelsAreNotGatedOnTheMapSplit:
    """Splitting the two Prime maps gave the rooms map plain outlines,
    on the reasoning that xiaomi-vacuum-map-card draws its own overlay.
    Defensible for the FILLS.

    Carrying it to the labels disabled an explicit user option on
    exactly the entity the code's own comment says the option exists
    for: "a plain picture-entity shows an image and nothing else, so for
    them the names have to be in the picture or they do not exist".

    @chairstacker: "no room shading colors and no names at all (even
    room names are gone)". The shading is deliberate; the names were
    not.
    """

    def test_the_label_branch_does_not_consult_show_room_fills(self):
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image)
        idx = source.find("CONF_MAP_ROOM_LABELS, DEFAULT_MAP_ROOM_LABELS")
        assert idx > 0
        guard = source[max(0, idx - 200):idx]

        assert "show_room_fills and" not in guard, (
            "room labels are gated on the map split again -- the option "
            "belongs to the user, not to which of the two maps this is"
        )

    def test_fills_are_still_split(self):
        """The other half stays: the card draws its own room overlay,
        and both at once doubles them up."""
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image)

        assert 'show_room_fills = getattr(self, "_include_live", True)' in source
        assert "if show_room_fills:" in source


class TestRepairIssuesComeFromTheHelper:
    """`async_create_issue` and `IssueSeverity` live in
    `homeassistant.helpers.issue_registry`. `homeassistant.components.repairs`
    has neither.

    `_trigger_zone_issue` imported the components module, so the
    zones-need-naming prompt raised AttributeError instead of appearing.
    Found by mypy as "Module has no attribute async_create_issue".
    """

    def test_nothing_imports_repairs_for_issue_creation(self):
        import pathlib
        import re

        offenders = []
        for path in pathlib.Path("custom_components/roomba_plus").glob("*.py"):
            text = path.read_text()
            for m in re.finditer(
                r"from homeassistant\.components import repairs(?: as (\w+))?", text
            ):
                alias = m.group(1) or "repairs"
                if f"{alias}.async_create_issue" in text:
                    offenders.append(path.name)

        assert not offenders, (
            f"{offenders} create repair issues through "
            f"homeassistant.components.repairs -- it has no "
            f"async_create_issue; use helpers.issue_registry"
        )

    def test_the_helper_is_the_one_with_the_function(self):
        """If Home Assistant ever moves it, this guard is wrong rather
        than the call sites."""
        from homeassistant.components import repairs
        from homeassistant.helpers import issue_registry

        assert hasattr(issue_registry, "async_create_issue")
        assert not hasattr(repairs, "async_create_issue")


class TestPickupIsNotDrift:
    """v3.2.2 — a carried robot gets a constant offset, not a ramp.

    The dock-anchor ramp assumes error compounds gradually, which is
    true for odometry and vSLAM error and false for a robot that was
    lifted. Set one down two metres away and EVERY pose afterwards is
    off by the same two metres — the first as much as the last. A ramp
    then under-corrects the start of the segment and drags points that
    were still accurate away from where the robot actually was.

    `bbrun.nPicks` is already read, stored per mission and documented in
    callbacks.py as "the robot was physically picked up during this
    mission". The map never asked; the code comment deferred this
    "pending real field validation that simple linear interpolation
    isn't enough".
    """

    @staticmethod
    def _segment():
        return [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0), (300.0, 0.0)]

    def test_a_ramp_barely_moves_the_first_point(self):
        """The existing behaviour, stated so the difference is visible."""
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        out = _interpolate_and_correct_segment(
            self._segment(), 900.0, 0.0, 0.0, picked_up=False
        )

        assert out[0][0] == 0.0, "weight 0 at the first buffered point"
        assert out[-1][0] == 300.0 + 900.0, "full correction at the last"

    def test_a_pickup_shifts_every_point_equally(self):
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        out = _interpolate_and_correct_segment(
            self._segment(), 900.0, 0.0, 0.0, picked_up=True
        )
        original = self._segment()

        shifts = [a[0] - b[0] for a, b in zip(out, original, strict=False)]
        assert shifts == [900.0] * 4

    def test_the_default_is_unchanged_behaviour(self):
        """Callers that do not know must keep the ramp."""
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        assert _interpolate_and_correct_segment(
            self._segment(), 900.0, 0.0, 0.0
        ) == _interpolate_and_correct_segment(
            self._segment(), 900.0, 0.0, 0.0, picked_up=False
        )


class TestSeveralPickupsInOneMission:
    """A mission can break more than once, and the correction has to
    say which stretch the dock measurement actually covers.

    The first version of the pickup branch shifted the entire buffered
    stretch by one offset. That is right for one lift and wrong for two:
    the second stretch has its own independent offset, so a single shift
    is as wrong for it as the linear ramp it replaced was for the first.

    What the dock verifies is the position of the LAST segment — the one
    that ends at the dock. Earlier segments are unverified by that
    measurement and are left alone rather than corrected on evidence
    that does not cover them.
    """

    @staticmethod
    def _segment():
        return [(float(i * 100), 0.0) for i in range(10)]

    def test_only_the_stretch_after_the_last_break_moves(self):
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        out = _interpolate_and_correct_segment(
            self._segment(), 500.0, 0.0, 0.0, picked_up=True, breaks={6}
        )
        original = self._segment()

        assert out[:6] == original[:6], "before the break: untouched"
        for a, b in zip(out[6:], original[6:], strict=False):
            assert a[0] - b[0] == 500.0

    def test_the_last_break_wins_when_there_are_several(self):
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        out = _interpolate_and_correct_segment(
            self._segment(), 500.0, 0.0, 0.0, picked_up=True, breaks={3, 7}
        )
        original = self._segment()

        assert out[:7] == original[:7]
        assert out[7][0] - original[7][0] == 500.0

    def test_without_breaks_the_whole_stretch_shifts(self):
        """One lift, one segment — the case that already worked."""
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        out = _interpolate_and_correct_segment(
            self._segment(), 500.0, 0.0, 0.0, picked_up=True, breaks=set()
        )
        original = self._segment()

        assert all(
            a[0] - b[0] == 500.0 for a, b in zip(out, original, strict=False)
        )

    def test_a_ramp_is_unaffected_by_breaks(self):
        """Drift without a pickup still ramps across the whole stretch:
        breaks describe where the FRAME changed, and gradual error
        accumulates regardless."""
        from custom_components.roomba_plus.image import (
            _interpolate_and_correct_segment,
        )

        assert _interpolate_and_correct_segment(
            self._segment(), 500.0, 0.0, 0.0, picked_up=False, breaks={5}
        ) == _interpolate_and_correct_segment(
            self._segment(), 500.0, 0.0, 0.0, picked_up=False
        )


class TestAnchoringBeforeTheGrid:
    """v3.2.2 — pre-break segments are placed, or left out.

    They were being stamped into the accumulated grid at unverified
    positions all along: `_mission_points` holds every pose, including
    those before a discontinuity, and the dock correction deliberately
    does not touch them. So this is not a new risk over the status quo
    — it replaces "stamp at an arbitrary wrong offset" with "stamp at a
    checked offset, or not at all".
    """

    @staticmethod
    def _distinctive(ox=0.0, oy=0.0, cell=150.0):
        """An L-shaped run. A filled rectangle cannot locate itself —
        shifted a few cells it overlaps almost as well — so a test built
        on one would measure the single case the method cannot do."""
        pts = []
        for row in range(5):
            for col in range(14):
                pts.append((ox + col * cell, oy + row * cell))
        for row in range(14):
            for col in range(5):
                pts.append((ox + col * cell, oy + row * cell))
        return pts

    @staticmethod
    def _store(cells=None):
        from unittest.mock import MagicMock

        store = MagicMock()
        store.cells = cells or {}
        return store

    def test_a_single_segment_passes_through_untouched(self):
        from custom_components.roomba_plus.image import _anchored_mission_points

        pts = self._distinctive()

        assert _anchored_mission_points(pts, set(), self._store()) == pts

    def test_a_displaced_segment_is_moved_back(self):
        from custom_components.roomba_plus.image import _anchored_mission_points

        anchored = self._distinctive()
        displaced = self._distinctive(ox=450.0, oy=300.0)
        combined = displaced + anchored

        out = _anchored_mission_points(
            combined, {len(displaced)}, self._store()
        )

        # The dock-verified tail is untouched, and the head came back.
        assert out[-len(anchored):] == anchored
        assert out[0] == (0.0, 0.0)

    def test_an_unplaceable_segment_is_dropped(self):
        """Losing a segment's cells costs coverage the next mission
        re-drives. Writing it at the wrong offset costs data nothing
        repairs."""
        from custom_components.roomba_plus.image import _anchored_mission_points

        anchored = self._distinctive()
        # Two points cannot carry a pattern.
        combined = [(9e5, 9e5), (9e5 + 10, 9e5)] + anchored

        out = _anchored_mission_points(combined, {2}, self._store())

        assert out == anchored

    def test_the_dock_verified_tail_is_always_kept(self):
        """Whatever happens to the rest, the segment the dock measured
        must reach the grid."""
        from custom_components.roomba_plus.image import _anchored_mission_points

        anchored = self._distinctive()
        combined = [(9e5, 9e5), (9e5 + 10, 9e5)] + anchored

        out = _anchored_mission_points(combined, {2}, self._store())

        assert out[-len(anchored):] == anchored


class TestZonesAreDrawnAndNamed:
    """`_zone_polygons` was initialised to an empty dict and never
    filled, while three separate passes read it -- the bounding box, the
    outline pass, and the label pass. All three had been working on
    nothing since they were written.

    So zones were not drawn at all, which is the stronger version of
    "zone names are not visible on the map" (@chairstacker). The data
    was there the whole time: the floor-plan build parses `cleanZones`
    into `zone_polygons` and nothing collected it.

    SAME SHAPE AS TWO OTHER FAULTS THIS WEEK: a value declared, read,
    and never assigned. It is worth checking that a write exists before
    trusting a read.
    """

    def test_the_zone_polygons_are_collected(self) -> None:
        import ast
        import inspect

        from custom_components.roomba_plus import image

        tree = ast.parse(inspect.getsource(image))
        assigned = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and target.attr == "_zone_polygons"
        ]

        assert assigned, (
            "_zone_polygons is read but never assigned -- every pass "
            "that uses it works on an empty dict"
        )

    def test_it_comes_from_the_floor_plan(self) -> None:
        import inspect

        from custom_components.roomba_plus import image

        assert 'getattr(floor_plan, "zone_polygons", None)' in (
            inspect.getsource(image)
        )

    def test_zones_get_labels_as_well_as_outlines(self) -> None:
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image)

        # The label pass walks both collections, not just rooms.
        assert "_labelled = [" in source
        assert "self._polygons, (230, 230, 230)" in source


class TestUnnamedZonesShowTheirNumber:
    """The repair notice asks you to name zones by number, and the map
    skipped them for having no name — so the only place that could show
    you where zone 23 is was the one place refusing to draw it.

    @liblit, twice: "Nothing shows me where these new zones might be
    relative to existing landmarks that I would recognize on a map."

    Drawing the bare id closes the loop: the notice names a number, the
    map shows that number in a room you recognise.
    """

    @staticmethod
    def _label(region_id, names):
        """The derivation the renderer now uses."""
        name = names.get(region_id)
        if not name:
            name = str(region_id).rsplit("/", 1)[-1]
            if name.startswith("zid_"):
                name = name[4:]
        return name

    def test_an_unnamed_zone_shows_its_number(self) -> None:
        assert self._label("MAP-A/zid_23", {}) == "23"

    def test_the_type_marker_is_not_shown(self) -> None:
        """`zid_23` is an internal id shape; the notice says 23."""
        assert "zid" not in self._label("MAP-A/zid_23", {})

    def test_a_named_zone_keeps_its_name(self) -> None:
        assert self._label(
            "MAP-A/zid_23", {"MAP-A/zid_23": "Foyer Zone"}
        ) == "Foyer Zone"

    def test_a_bare_id_works_too(self) -> None:
        assert self._label("23", {}) == "23"

    def test_the_renderer_no_longer_skips_unnamed(self) -> None:
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image)

        assert "if not name or not ring:" not in source
        assert "if not ring:" in source


# ── formerly tests/test_coverage_image.py ───────────────────────────────────────
#
# image.py — quality scale, test-coverage.
#
# At mission end the Classic map hands the mission's path to the learning
# stores: geometry (doorways from gaps in the path), the occupancy grid
# (with weekday and hour of stuck events), outline and room segmentation.
# Built through the real constructor, which no test used before.

def _entry(**runtime):
    entry = MagicMock()
    entry.options = {}
    scheduled = []

    def _task(_hass, coro, *a, **k):
        scheduled.append(getattr(coro, "__qualname__", repr(coro)))
        if hasattr(coro, "close"):
            coro.close()

    entry.async_create_task = _task
    for k in ("geometry_store", "grid_store", "outline_store", "room_seg_store",
              "freeze_snapshot_store", "mission_store"):
        setattr(entry.runtime_data, k, runtime.get(k))
    return entry, scheduled


def _map(capability, entry, renderer=None):
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {"bbmssn": {"nMssn": 7}}}}
    m = img.RoombaMapImage(roomba, "B", renderer, capability, entry)
    m.hass = MagicMock()
    m.vacuum_state = {"bbmssn": {"nMssn": 7}}
    m._refresh_terminal_mission_images = MagicMock()
    return m


def _path(n=30, step=100.0):
    return [(i * step, 0.0) for i in range(n)]


class _Resp:
    def __init__(self, status=200, body=b"raw"):
        self.status, self._body, self.headers = status, body, {"Content-Type": "application/octet-stream"}

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _prime_map(hass, monkeypatch, streams, *, status=200, decode=b"PNG"):
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.runtime_data.prime_positions = []
    robot = MagicMock()
    it = iter(streams)

    def _watch():
        s = next(it)
        if isinstance(s, asyncio.CancelledError):
            raise s          # ends the test's loop

        async def _gen():
            # Like the real library: an async generator that fails while
            # being iterated, not when it is created.
            if isinstance(s, BaseException):
                raise s
            for m in s:
                yield m
        return _gen()

    robot.watch_live_map = _watch
    m = img.PrimeMapImage(robot, "PB", entry)
    m.hass = hass
    m.async_write_ha_state = MagicMock()
    m._async_save_png = MagicMock()
    session = MagicMock()
    session.get = lambda url: _Resp(status)
    monkeypatch.setattr(img, "async_get_clientsession", lambda _h: session)
    from roombapy_prime.models import livemap

    monkeypatch.setattr(livemap, "decode_rawmap_to_png", lambda raw: decode)
    monkeypatch.setattr(img.asyncio, "sleep", AsyncMock())
    return m, entry


def _position(*points):
    return PositionUpdateMessage(sequence_number=1, last_update_timestamp=0, expires_at=0,
                                 updates=[SimpleNamespace(point=p, orientation=0.5) for p in points])


def _renderer(has_data=True):
    r = MagicMock(has_data=has_data, _fit_scale=10.0, _breaks=[])
    r.render.return_value = b"base"
    r._mm_to_px.return_value = (1, 1)
    r._mm_to_px_fit.return_value = (1, 1)
    r.render_keepout_zones.return_value = b"keepout"
    r.render_observed_zones.return_value = b"observed"
    r.render_room_outline.return_value = b"outline"
    return r


def _image_map(hass, *, renderer, capability=MapCapability.SMART, aligned=True, keepout=(),
               centroids=(), outline_ready=False, extent=None):
    entry, _s = _entry()
    data = entry.runtime_data
    aligner = MagicMock(aligned=aligned)
    aligner.keepout_polygon_umf.side_effect = lambda z: z.get("poly")
    aligner.umf_to_pose.side_effect = lambda x, y: None if x < 0 else (x, y)
    data.umf_aligner = aligner
    data.cloud_coordinator = SimpleNamespace(keepout_zones=list(keepout), observed_zone_centroids=list(centroids))
    data.outline_store = MagicMock(ready=outline_ready)
    data.map_capability = capability
    data.room_map_extent_mm = extent
    m = _map(capability, entry, renderer=renderer)
    m.hass = hass
    return m


def _square(x0, y0, size=2000.0):
    return [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]


def _rooms_image(*, live=None, positions=(), floor=True):
    r = img.PrimeRoomsImage.__new__(img.PrimeRoomsImage)
    r._renderer = None
    r._polygons = {"3": _square(0, 0), "5": _square(2500, 0)}
    r._names = {"3": "Kitchen", "5": "Hall"}
    r._zone_polygons = {"z1": _square(500, 500, 400)}
    r._floor_plan = PrimeFloorPlan(
        room_names={}, room_polygons={},
        floor_plan=[_square(-100, -100, 4700)] if floor else [],
        borders=[_square(0, 0, 4500)], carpet=[_square(200, 200, 600)],
        furniture=[_square(3000, 300, 300)], dock=(100.0, 100.0), zone_layers={},
        zone_polygons={}, p2map_id="m1")
    r._live_bundle = live
    r._show_live_overlay = True
    r._include_live = True
    r._dock_position = lambda: (100.0, 100.0)   # a method on the real class
    r._live_position_belongs_here = lambda: True
    r._config_entry = MagicMock()
    r._config_entry.runtime_data.prime_positions = list(positions)
    return r


def _decode(png):
    im = Image.open(io.BytesIO(png))
    im.load()
    return im


def _real_entry(hass, **runtime):
    entry, _s = _entry(**runtime)
    entry.entry_id = "e1"
    d = entry.runtime_data
    d.cloud_coordinator = None
    d.umf_aligner = None
    d.room_seg_store = None
    d.prime_positions = []
    d.map_capability = MapCapability.EPHEMERAL
    return entry


def _build_all(hass):
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = _real_entry(hass, grid_store=GridStore())
    built = [
        img.RoombaMapImage(roomba, "B", _renderer(), MapCapability.EPHEMERAL, entry),
        img.RoombaRoomsImage(roomba, "B", entry),
        img.RoombaCoverageImage(roomba, "B", entry.runtime_data.grid_store, entry),
        img.PrimeMapImage(MagicMock(), "PB", entry),
        img.PrimeRoomsImage("PB", entry, hass),
    ]
    for e in built:
        e.hass = hass
        e.async_write_ha_state = MagicMock()
        e.schedule_update_ha_state = MagicMock()
    return built


def _prime_rooms(hass, monkeypatch, *, map_ids, current=None, chosen=None, versions=None,
                 polygons=None, floor=None):
    entry = _real_entry(hass)
    entry.runtime_data.prime_selected_map_id = chosen
    robot = MagicMock(get_active_map_versions=AsyncMock(return_value=versions or []))
    entry.runtime_data.prime_robot = robot
    backend = MagicMock()
    backend._all_map_ids = AsyncMock(return_value=map_ids)
    backend._current_map_id = AsyncMock(return_value=current)
    monkeypatch.setattr(room_cleaning, "async_get_room_cleaning_backend", lambda *_a: backend)
    built = []

    async def _polys(_e, map_id):
        built.append(map_id)
        return dict(polygons or {}), {k: f"R{k}" for k in (polygons or {})}, {}

    monkeypatch.setattr(prime_room_map, "async_build_prime_room_polygons", _polys)
    fp = floor or PrimeFloorPlan(room_names={}, room_polygons={}, floor_plan=[], borders=[], carpet=[],
                                 furniture=[], dock=None, zone_layers={}, zone_polygons={}, p2map_id="")
    monkeypatch.setattr(prime_room_map, "async_build_prime_floor_plan", AsyncMock(return_value=fp))
    r = img.PrimeRoomsImage("PB", entry, hass)
    r.hass = hass
    r._render_png = MagicMock(return_value=b"PNG")
    return r, built


def _rooms_attrs(hass, *, aligned=True, rendered=True, polygons=None, aligner=True):
    entry = _real_entry(hass)
    data = entry.runtime_data
    if aligner:
        al = MagicMock(aligned=aligned)
        al.room_polygons_umf = polygons if polygons is not None else {
            "3": [(1.0, 1.0), (4.0, 1.0), (4.0, 4.0)],
            "5": [(-1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]}         # one corner does not map back
        al.umf_to_pose.side_effect = lambda x, y: None if x < 0 else (x * 10, y * 10)
        al.keepout_polygon_umf.side_effect = lambda z: z.get("poly")
        al.rid_to_name.return_value = {"3": "Kitchen"}
        al.calibration_points.return_value = [{"cal": 1}]
        data.umf_aligner = al
    else:
        data.umf_aligner = None
    data.cloud_coordinator = SimpleNamespace(
        regions=[{"id": "3", "region_type": "kitchen"}],
        observed_zone_centroids=[{"x": 2.0, "y": 2.0}, {"x": -3.0, "y": 0.0}],
        keepout_zones=[{"poly": [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]},
                       {"poly": [(-1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]}, {"poly": None}])
    data.geometry_store = SimpleNamespace(door_markers=[
        SimpleNamespace(id="d1", cx=1.0, cy=2.0, label="Door", mission_count=4)])
    data.grid_store = MagicMock()
    data.grid_store.furniture_candidates.return_value = [{"x_mm": 5.0, "y_mm": 6.0, "extra": 1}]
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    r = img.RoombaRoomsImage(roomba, "B", entry)
    r.hass = hass
    r._rendered_once = rendered
    r._to_px_last = lambda x, y: (x, y)
    return r.extra_state_attributes


class TestMissionEnd:

    def test_no_path_saves_nothing_but_clears_the_checkpoint(self):
        geo = MagicMock()
        entry, scheduled = _entry(geometry_store=geo)
        m = _map(MapCapability.EPHEMERAL, entry)
        m._handle_mission_end("charge")
        assert any("clear_mission_checkpoint" in s for s in scheduled)
        geo.async_save.assert_not_called()

    def test_a_docked_ephemeral_mission_saves_the_geometry(self):
        geo = MagicMock(drift_recovered=MagicMock(return_value=True))
        entry, scheduled = _entry(geometry_store=geo)
        m = _map(MapCapability.EPHEMERAL, entry)
        m._mission_points = _path()
        m._handle_mission_end("charge")
        geo.async_save.assert_called_once()
        assert any("clear_drift_issue" in s for s in scheduled), "a recovered drift clears its issue"

    def test_an_ephemeral_mission_that_did_not_dock_saves_no_geometry(self):
        """Without the dock as anchor the path may have drifted."""
        geo = MagicMock()
        entry, _s = _entry(geometry_store=geo)
        m = _map(MapCapability.EPHEMERAL, entry)
        m._mission_points = _path()
        m._handle_mission_end("stuck")
        geo.async_save.assert_not_called()

    def test_on_a_smart_map_door_sized_gaps_become_doorways(self):
        geo = MagicMock()
        entry, _s = _entry(geometry_store=geo)
        m = _map(MapCapability.SMART, entry)
        door = (MIN_DOOR_WIDTH_MM + MAX_DOOR_WIDTH_MM) / 2
        assert door > GAP_THRESHOLD_MM
        path = _path(25) + [(2400.0 + door, 0.0)] + [(2400.0 + door + i * 100.0, 0.0) for i in range(1, 5)]
        m._mission_points = path
        m._handle_mission_end("charge")
        (mid,), = [c.args for c in geo.update_from_midpoints.call_args_list]
        assert mid == [(2400.0 + door / 2, 0.0)]


class TestGridAtMissionEnd:

    def test_the_path_and_the_stuck_hour_go_to_the_grid(self, monkeypatch):
        grid = MagicMock(cells={})
        entry, _s = _entry(grid_store=grid)
        m = _map(MapCapability.SMART, entry, renderer=MagicMock(has_data=False, _breaks=[]))
        m._mission_points = _path(5)
        m._stuck_mission_points = [(100.0, 0.0)]
        m._mission_start_ts = "2026-09-21T07:30:00+00:00"
        monkeypatch.setattr(img, "_anchored_mission_points", lambda pts, *_a, **_k: pts)
        m._handle_mission_end("charge")
        args, kwargs = grid.update_from_mission.call_args
        assert args[1] == [(100.0, 0.0)] and kwargs["stuck_wh"] is not None
        grid.record_processed_nmssn.assert_called_once_with(7)

    def test_new_unconfirmed_rooms_raise_the_zone_issue(self, monkeypatch):
        grid = MagicMock(cells={})
        seg = MagicMock()
        seg.unconfirmed_rooms = []
        def _recompute(*_a, **_k):
            seg.unconfirmed_rooms = ["r1"]
            return True
        seg.maybe_recompute = _recompute
        outline = MagicMock()
        entry, scheduled = _entry(grid_store=grid, room_seg_store=seg, outline_store=outline)
        m = _map(MapCapability.EPHEMERAL, entry, renderer=MagicMock(has_data=False, _breaks=[]))
        m._mission_points = _path(5)
        monkeypatch.setattr(img, "_anchored_mission_points", lambda pts, *_a, **_k: pts)
        m._handle_mission_end("charge")
        outline.recompute_sync.assert_called_once()
        assert any("trigger_zone_issue" in s for s in scheduled)


class TestPrimeLiveMap:

    @pytest.mark.asyncio
    async def test_positions_feed_the_trail_in_millimetres_and_bad_samples_are_counted(self, hass, monkeypatch):
        msg = _position((1.0, 2.0), None, ("x",), (None, 3.0))
        m, entry = _prime_map(hass, monkeypatch, [[msg], asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        assert entry.runtime_data.prime_positions[0][:2] == (1000.0, 2000.0)
        stats = m._live_map_stats()
        assert stats["trail_points_added"] == 1
        assert stats["trail_skipped_no_point"] == 1 and stats["trail_skipped_no_xy"] == 2

    @pytest.mark.asyncio
    async def test_a_raw_map_becomes_the_image(self, hass, monkeypatch):
        msg = MapUpdateMessage(livemap_url=None, livemap_url_raw="https://x/raw", timestamp=0)
        m, _e = _prime_map(hass, monkeypatch, [[msg], asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        assert m._png_bytes == b"PNG"
        m._async_save_png.assert_called_once()
        assert m._live_map_stats()["decode_ok"] == 1

    @pytest.mark.asyncio
    async def test_a_failed_download_keeps_the_previous_image(self, hass, monkeypatch):
        msg = MapUpdateMessage(livemap_url=None, livemap_url_raw="https://x/raw", timestamp=0)
        m, _e = _prime_map(hass, monkeypatch, [[msg], asyncio.CancelledError()], status=503)
        m._png_bytes = b"OLD"
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        assert m._png_bytes == b"OLD"

    @pytest.mark.asyncio
    async def test_an_undecodable_map_is_counted_and_keeps_the_image(self, hass, monkeypatch):
        from roombapy_prime.models import livemap

        msg = MapUpdateMessage(livemap_url=None, livemap_url_raw="https://x/raw", timestamp=0)
        m, _e = _prime_map(hass, monkeypatch, [[msg], asyncio.CancelledError()])
        monkeypatch.setattr(livemap, "decode_rawmap_to_png", MagicMock(side_effect=ValueError("bad")))
        m._png_bytes = b"OLD"
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        assert m._png_bytes == b"OLD" and m._live_map_stats()["decode_failed"] == 1

    @pytest.mark.asyncio
    async def test_a_dropped_stream_reconnects_with_growing_backoff(self, hass, monkeypatch):
        m, _e = _prime_map(hass, monkeypatch, [RuntimeError("down"), [], asyncio.CancelledError()])
        m._record_watch_failure = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        waits = [c.args[0] for c in img.asyncio.sleep.await_args_list]
        assert waits[:2] == [5.0, 10.0]
        assert m._record_watch_failure.call_count == 2


class TestMapImageLayers:

    @pytest.mark.asyncio
    async def test_without_a_renderer_a_blank_image(self, hass):
        m = _image_map(hass, renderer=None)
        m._blank_image = MagicMock(return_value=b"blank")
        assert await m.async_image() == b"blank"

    @pytest.mark.asyncio
    async def test_without_own_data_the_cloud_coverage_is_shown(self, hass):
        m = _image_map(hass, renderer=_renderer(has_data=False))
        m._async_cloud_coverage_png = AsyncMock(return_value=b"cloud")
        assert await m.async_image() == b"cloud"

    @pytest.mark.asyncio
    async def test_keepout_zones_are_drawn_skipping_ones_that_do_not_map_back(self, hass):
        r = _renderer()
        m = _image_map(hass, renderer=r, keepout=[
            {"poly": [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]},
            {"poly": [(-1.0, 1.0), (2.0, 1.0), (2.0, 2.0)]},     # one corner outside: skipped whole
            {"poly": None}])
        assert await m.async_image() == b"keepout"
        (polys,) = r.render_keepout_zones.call_args.args
        assert len(polys) == 1

    @pytest.mark.asyncio
    async def test_observed_zones_are_drawn_as_circles(self, hass):
        r = _renderer()
        m = _image_map(hass, renderer=r, centroids=[{"x": 5.0, "y": 5.0}, {"x": -1.0, "y": 0.0}])
        assert await m.async_image() == b"observed"
        (circles,) = r.render_observed_zones.call_args.args
        assert len(circles) == 1 and circles[0][2] >= 3

    @pytest.mark.asyncio
    async def test_an_unaligned_map_gets_no_cloud_overlays(self, hass):
        r = _renderer()
        m = _image_map(hass, renderer=r, aligned=False, keepout=[{"poly": [(1.0, 1.0)] * 3}])
        assert await m.async_image() == b"base"
        r.render_keepout_zones.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_learned_outline_on_a_robot_without_a_smart_map(self, hass):
        r = _renderer()
        m = _image_map(hass, renderer=r, capability=MapCapability.EPHEMERAL, outline_ready=True)
        assert await m.async_image() == b"outline"

    @pytest.mark.asyncio
    async def test_the_room_extent_fixes_the_frame(self, hass):
        r = _renderer()
        m = _image_map(hass, renderer=r, extent=(0, 1000, 0, 800))
        await m.async_image()
        assert r._fit_bounds_px is not None
        m2 = _image_map(hass, renderer=_renderer(), extent=None)
        await m2.async_image()
        assert m2._renderer._fit_bounds_px is None


class TestPrimeRoomsRender:

    def test_a_full_floor_renders_to_a_valid_square_png_with_the_room_colours(self):
        from custom_components.roomba_plus.image import ROOM_FILL_PALETTE

        r = _rooms_image()
        im = _decode(r._render_png())
        assert im.format == "PNG" and im.size[0] == im.size[1]
        colours = {c for _n, c in im.convert("RGB").getcolors(maxcolors=1_000_000)}
        assert ROOM_FILL_PALETTE[0][:3] in colours or tuple(ROOM_FILL_PALETTE[0])[:3] in colours

    def test_live_coverage_and_the_robots_own_trail_are_drawn(self):
        live = {"coverage": {"type": "FeatureCollection", "features": []},
                "trajectories": {"features": []}, "hazard": {"features": []}, "manifest": {}}
        r = _rooms_image(live=live, positions=[(100.0, 100.0, 0.0), (900.0, 400.0, 0.0), (1500.0, 800.0, 0.0)])
        im = _decode(r._render_png())
        assert im.size[0] > 0

    def test_without_a_floor_plan_the_rooms_still_render(self):
        im = _decode(_rooms_image(floor=False)._render_png())
        assert im.format == "PNG"


class TestEveryImageClass:

    @pytest.mark.asyncio
    async def test_built_for_real_each_answers_its_attributes(self, hass):
        for e in _build_all(hass):
            attrs = e.extra_state_attributes
            assert attrs is None or isinstance(attrs, dict), type(e).__name__
            assert e.unique_id, type(e).__name__

    @pytest.mark.parametrize("state", [{}, {"cleanMissionStatus": {"phase": "run", "mssnStrtTm": 1},
                                            "pose": {"point": {"x": 1, "y": 2}, "theta": 0}}],
                             ids=["empty", "running"])
    def test_a_classic_message_is_handled(self, hass, state):
        for e in _build_all(hass)[:3]:
            e.vacuum.master_state = {"state": {"reported": state}}
            e.on_message({"state": {"reported": state}})   # must not raise


class TestRoomsImage:

    def test_a_learned_room_layout_names_its_rooms(self, hass):
        entry = _real_entry(hass)
        seg = MagicMock()
        seg.rooms = {"r1": SimpleNamespace(name="Kitchen", confirmed=True, cells=[(0, 0)], area_m2=12.0),
                     "r2": SimpleNamespace(name=None, confirmed=False, cells=[(1, 1)], area_m2=3.0)}
        seg.unconfirmed_rooms = ["r2"]
        entry.runtime_data.room_seg_store = seg
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        r = img.RoombaRoomsImage(roomba, "B", entry)
        r.hass = hass
        attrs = r.extra_state_attributes
        assert isinstance(attrs, dict)


class TestImageSetup:

    async def _run(self, hass, **d):
        entry, _s = _entry()
        data = entry.runtime_data
        data.connection_type = d.get("conn", ConnectionType.LOCAL_PUSH)
        data.prime_robot = d.get("prime_robot")
        data.map_capability = d.get("cap", MapCapability.EPHEMERAL)
        data.grid_store = d.get("grid")
        data.room_seg_store = d.get("seg")
        data.renderer = _renderer()
        data.roomba = MagicMock(master_state={"state": {"reported": {}}})
        data.blid = "B"
        added = []
        await img.async_setup_entry(hass, entry, lambda ents, *a, **k: added.extend(ents))
        return sorted(type(e).__name__ for e in added)

    @pytest.mark.asyncio
    async def test_prime_with_a_robot_gets_its_images(self, hass):
        names = await self._run(hass, conn=ConnectionType.CLOUD_ONLY, prime_robot=MagicMock())
        assert "PrimeMapImage" in names

    @pytest.mark.asyncio
    async def test_prime_without_a_robot_gets_none(self, hass):
        assert await self._run(hass, conn=ConnectionType.CLOUD_ONLY, prime_robot=None) == []

    @pytest.mark.asyncio
    async def test_a_robot_that_cannot_map_gets_none(self, hass):
        assert await self._run(hass, cap=MapCapability.NONE) == []

    @pytest.mark.asyncio
    async def test_a_mapping_robot_gets_map_coverage_and_rooms(self, hass):
        names = await self._run(hass, cap=MapCapability.SMART, grid=GridStore())
        assert names == ["RoombaCoverageImage", "RoombaMapImage", "RoombaRoomsImage"]


class TestMapStateAcrossRestarts:

    def _m(self, hass, renderer):
        entry, _s = _entry()
        entry.entry_id = "e1"
        m = _map(MapCapability.EPHEMERAL, entry, renderer=renderer)
        m.hass = hass
        return m

    @pytest.mark.asyncio
    async def test_a_stored_map_is_restored(self, hass, monkeypatch):
        r = _renderer()
        r.restore_state.return_value = True
        monkeypatch.setattr(img, "Store", lambda *a, **k: MagicMock(async_load=AsyncMock(return_value={"x": 1})))
        m = self._m(hass, r)
        before = m._attr_image_last_updated
        await m._async_restore_map_state()
        r.restore_state.assert_called_once_with({"x": 1})
        assert m._attr_image_last_updated >= before

    @pytest.mark.parametrize("load", [AsyncMock(side_effect=OSError("disk")), AsyncMock(return_value=None)],
                             ids=["unreadable", "empty"])
    @pytest.mark.asyncio
    async def test_an_unreadable_or_empty_store_restores_nothing(self, hass, monkeypatch, load):
        r = _renderer()
        monkeypatch.setattr(img, "Store", lambda *a, **k: MagicMock(async_load=load))
        await self._m(hass, r)._async_restore_map_state()
        r.restore_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_stored_map_the_renderer_rejects_is_not_applied(self, hass, monkeypatch):
        r = _renderer()
        r.restore_state.return_value = False
        monkeypatch.setattr(img, "Store", lambda *a, **k: MagicMock(async_load=AsyncMock(return_value={"old": 1})))
        m = self._m(hass, r)
        before = m._attr_image_last_updated
        await m._async_restore_map_state()
        assert m._attr_image_last_updated == before

    @pytest.mark.asyncio
    async def test_without_a_renderer_there_is_nothing_to_restore(self, hass, monkeypatch):
        store = MagicMock(async_load=AsyncMock())
        monkeypatch.setattr(img, "Store", lambda *a, **k: store)
        await self._m(hass, None)._async_restore_map_state()
        store.async_load.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_saving_writes_the_renderers_state(self, hass, monkeypatch):
        r = _renderer()
        r.export_state.return_value = {"points": [1, 2]}
        store = MagicMock(async_save=AsyncMock())
        monkeypatch.setattr(img, "Store", lambda *a, **k: store)
        await self._m(hass, r)._async_save_map_state()
        store.async_save.assert_awaited_once()


class TestPrimeRoomsRefresh:

    @pytest.mark.asyncio
    async def test_the_users_chosen_map_wins(self, hass, monkeypatch):
        r, built = _prime_rooms(hass, monkeypatch, map_ids=["m1", "m2"], current="m1", chosen="m2")
        await r._async_refresh_rooms()
        assert built == ["m2"]

    @pytest.mark.asyncio
    async def test_then_the_map_the_robot_is_on(self, hass, monkeypatch):
        r, built = _prime_rooms(hass, monkeypatch, map_ids=["m1", "m2"], current="m2")
        await r._async_refresh_rooms()
        assert built == ["m2"]

    @pytest.mark.asyncio
    async def test_then_the_first_map(self, hass, monkeypatch):
        r, built = _prime_rooms(hass, monkeypatch, map_ids=["m1", "m2"], current="gone")
        await r._async_refresh_rooms()
        assert built == ["m1"]

    @pytest.mark.asyncio
    async def test_rooms_are_rendered_and_the_map_versions_remembered(self, hass, monkeypatch):
        r, _b = _prime_rooms(hass, monkeypatch, map_ids=["m1"], current="m1",
                             versions=[{"p2map_id": "m1", "active_p2mapv_id": "v3"}],
                             polygons={"3": _square(0, 0)})
        await r._async_refresh_rooms()
        assert r._png == b"PNG"
        assert r._config_entry.runtime_data.prime_map_versions == {"m1": "v3"}

    @pytest.mark.asyncio
    async def test_no_maps_or_nothing_to_draw_changes_nothing(self, hass, monkeypatch):
        r, built = _prime_rooms(hass, monkeypatch, map_ids=[])
        await r._async_refresh_rooms()
        assert built == []
        assert getattr(r, "_png", None) is None
        r2, _b = _prime_rooms(hass, monkeypatch, map_ids=["m1"], current="m1", polygons={})
        await r2._async_refresh_rooms()
        r2._render_png.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_version_read_still_draws_the_rooms(self, hass, monkeypatch):
        r, _b = _prime_rooms(hass, monkeypatch, map_ids=["m1"], current="m1", polygons={"3": _square(0, 0)})
        r._config_entry.runtime_data.prime_robot.get_active_map_versions = AsyncMock(side_effect=RuntimeError())
        await r._async_refresh_rooms()
        assert r._png == b"PNG"


class TestDockDrift:

    @pytest.mark.parametrize("final,correction", [
        ((0.0, 0.0), (0.0, 0.0)),
        ((250.0, -299.0), (0.0, 0.0)),          # within 30 cm: no drift
        ((450.0, 10.0), (-450.0, -10.0)),        # beyond: correct by the reverse
        ((0.0, -800.0), (-0.0, 800.0)),
    ])
    def test_a_mission_ending_away_from_the_dock_is_drift(self, final, correction):
        assert img._check_dock_drift(final) == correction


class TestZoneLayer:

    def test_zones_are_outlined_never_filled(self):
        draw = MagicMock()
        layer = {"features": [
            {"geometry": {"coordinates": [[(0, 0), (1, 0), (1, 1)]]}},
            {"geometry": {"coordinates": [[("x", 0), (1, 0), (1, 1)]]}},   # unreadable ring: skipped
            {"geometry": {"coordinates": [[(0, 0), (1, 1)]]}},             # two points: not a polygon
            {"geometry": None}]}
        img.PrimeRoomsImage._draw_zone_layer(draw, lambda x, y: (int(x), int(y)), layer, outline=(1, 2, 3))
        assert draw.polygon.call_count == 1
        _args, kwargs = draw.polygon.call_args
        assert kwargs == {"outline": (1, 2, 3)}, "outline only, no fill"


class TestPrimeRoomsStart:

    @pytest.mark.asyncio
    async def test_a_stored_live_bundle_is_loaded_and_signals_are_followed(self, hass, monkeypatch):
        from custom_components.roomba_plus.entity import IRobotEntity

        monkeypatch.setattr(IRobotEntity, "async_added_to_hass", AsyncMock())
        store = MagicMock(async_load=AsyncMock(return_value={"bundle": {"coverage": None}, "plan": "x"}))
        monkeypatch.setattr(img, "Store", lambda *a, **k: store)
        entry = _real_entry(hass)
        r = img.PrimeRoomsImage("PB", entry, hass, include_live=True)
        r.hass = hass
        r._async_refresh_rooms = AsyncMock()
        r.async_on_remove = MagicMock()
        await r.async_added_to_hass()
        assert r._stored_live_bundle == {"bundle": {"coverage": None}, "plan": "x"}
        r._async_refresh_rooms.assert_awaited_once()
        assert r.async_on_remove.call_count >= 2


class TestLiveZonesBelongToTheirMap:
    """Zone layers come from the live bundle only when it belongs to the
    map being drawn; a bundle from the other floor would draw its zones
    here."""

    def _render(self, bundle_map):
        from custom_components.roomba_plus.const import CONF_MAP_CLEAN_ZONES

        live_layer = {"features": ["live"]}
        stored_layer = {"features": ["stored"]}
        r = _rooms_image(live={"manifest": {"pmap_id": bundle_map} if bundle_map else "odd",
                               "cleanZones": live_layer})
        r._floor_plan = PrimeFloorPlan(
            room_names={}, room_polygons={}, floor_plan=[], borders=[], carpet=[], furniture=[],
            dock=None, zone_layers={"cleanZones": stored_layer}, zone_polygons={}, p2map_id="m1")
        r._config_entry.options = {CONF_MAP_CLEAN_ZONES: True}
        r._draw_zone_layer = MagicMock()
        r._render_png()
        layers = [c.args[2] for c in r._draw_zone_layer.call_args_list]
        return live_layer in layers, stored_layer in layers

    def test_a_bundle_from_this_map_supplies_the_zones(self):
        assert self._render("m1") == (True, False)

    def test_a_bundle_from_another_map_does_not(self):
        assert self._render("m2") == (False, True)

    def test_a_bundle_that_names_no_map_is_trusted(self):
        assert self._render(None) == (True, False)


class TestLiveBundleFetch:

    @pytest.mark.asyncio
    async def test_a_bundle_is_parsed_off_the_loop_and_dispatched(self, hass, monkeypatch):
        from roombapy_prime.models import map_bundle

        parsed = {"manifest": {"pmap_id": "m1"}}
        monkeypatch.setattr(map_bundle, "parse_map_bundle", lambda raw: parsed)
        sent = []
        monkeypatch.setattr(img, "async_dispatcher_send", lambda _h, signal, *a: sent.append((signal, a)))
        msg = MapUpdateMessage(livemap_url="https://x/bundle", livemap_url_raw=None, timestamp=0)
        m, _e = _prime_map(hass, monkeypatch, [[msg], asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        bundle_signals = [a for s, a in sent if "bundle" in s]
        assert bundle_signals and bundle_signals[0][0] == parsed

    @pytest.mark.asyncio
    async def test_a_failing_bundle_download_is_recorded_and_the_stream_goes_on(self, hass, monkeypatch):
        from roombapy_prime.models import map_bundle

        monkeypatch.setattr(map_bundle, "parse_map_bundle", MagicMock(side_effect=ValueError("corrupt")))
        failures = []
        monkeypatch.setattr(img, "record_failure", lambda what, *a: failures.append(what))
        sent = []
        monkeypatch.setattr(img, "async_dispatcher_send", lambda _h, signal, *a: sent.append(signal))
        bad = MapUpdateMessage(livemap_url="https://x/bundle", livemap_url_raw=None, timestamp=0)
        pos = _position((1.0, 1.0))
        m, entry = _prime_map(hass, monkeypatch, [[bad, pos], asyncio.CancelledError()])
        with pytest.raises(asyncio.CancelledError):
            await m._async_watch_live_map()
        assert "live bundle fetch" in failures
        assert entry.runtime_data.prime_positions, "the position after the bad bundle still arrived"


class TestAfterARoomRecompute:

    def _run(self, monkeypatch, *, snapshot_due, start_ts="2026-09-21T07:30:00+00:00"):
        grid = MagicMock(cells={})
        seg = MagicMock()
        seg.unconfirmed_rooms = []
        seg.maybe_recompute.return_value = True
        seg.rooms = {"r1": MagicMock(to_dict=lambda: {"id": "r1"})}
        seg.doors = [MagicMock(to_dict=lambda: {"id": "d1"})]
        geo = MagicMock()
        freeze = MagicMock()
        freeze.due.return_value = snapshot_due
        outline = MagicMock(contour_points=[(0, 0), (1, 1)])
        entry, scheduled = _entry(grid_store=grid, room_seg_store=seg, geometry_store=geo,
                                  freeze_snapshot_store=freeze, outline_store=outline)
        m = _map(MapCapability.EPHEMERAL, entry, renderer=MagicMock(has_data=False, _breaks=[]))
        m._mission_points = _path(5)
        m._mission_start_ts = start_ts
        monkeypatch.setattr(img, "_anchored_mission_points", lambda pts, *_a, **_k: pts)
        m._handle_mission_end("stuck")
        return grid, geo, freeze

    def test_the_geometry_follows_the_new_rooms_and_a_due_snapshot_is_taken(self, monkeypatch):
        _g, geo, freeze = self._run(monkeypatch, snapshot_due=True)
        geo.update_from_room_seg_store.assert_called_once()
        freeze.note_recompute.assert_called_once()
        rooms, doors, outline, _when = freeze.snapshot.call_args.args
        assert rooms == [{"id": "r1"}] and doors == [{"id": "d1"}] and outline == [(0, 0), (1, 1)]

    def test_a_snapshot_not_yet_due_is_not_taken(self, monkeypatch):
        _g, _geo, freeze = self._run(monkeypatch, snapshot_due=False)
        freeze.snapshot.assert_not_called()

    def test_an_unreadable_start_time_still_records_the_mission(self, monkeypatch):
        grid, _geo, _f = self._run(monkeypatch, snapshot_due=False, start_ts=object())
        _args, kwargs = grid.update_from_mission.call_args
        assert kwargs["stuck_wh"] is None


class TestRoomsImageAttributes:

    def test_rooms_zones_doors_and_furniture_for_the_card(self, hass):
        attrs = _rooms_attrs(hass)
        assert attrs["alignment_pending"] is False and attrs["calibration_points"] == [{"cal": 1}]
        assert list(attrs["rooms"]) == ["Kitchen"], "the room whose outline does not map back is left out"
        types = [z["type"] for z in attrs["zones"]]
        assert types.count("observed") == 1 and types.count("keepout") == 1
        assert attrs["door_markers"][0]["id"] == "d1"
        assert attrs["furniture_candidates"] == [{"x_mm": 5.0, "y_mm": 6.0}]

    def test_an_unaligned_map_gives_provisional_calibration_and_no_zones(self, hass):
        attrs = _rooms_attrs(hass, aligned=False)
        assert attrs["alignment_pending"] is True
        assert len(attrs["calibration_points"]) == 3
        assert "zones" not in attrs

    @pytest.mark.parametrize("kw", [{"aligner": False}, {"polygons": {}}, {"rendered": False}],
                             ids=["no_aligner", "no_rooms", "not_rendered_yet"])
    def test_nothing_before_there_is_something_to_describe(self, hass, kw):
        assert _rooms_attrs(hass, **kw) == {}


class TestPolicyZonesFollowTheOptions:

    def _drawn(self, keepout, nomop):
        from custom_components.roomba_plus.const import CONF_MAP_KEEPOUT_ZONES, CONF_MAP_NOMOP_ZONES

        feats = [{"properties": {"type": t}, "geometry": {"coordinates": []}}
                 for t in ("KeepOutZone", "NoMopZone", "SomethingNew")]
        r = _rooms_image(live={"manifest": {}, "policyZones": {"features": feats}})
        r._floor_plan = PrimeFloorPlan(room_names={}, room_polygons={}, floor_plan=[], borders=[], carpet=[],
                                       furniture=[], dock=None, zone_layers={}, zone_polygons={}, p2map_id="")
        r._config_entry.options = {CONF_MAP_KEEPOUT_ZONES: keepout, CONF_MAP_NOMOP_ZONES: nomop}
        r._draw_zone_layer = MagicMock()
        r._render_png()
        return sorted(c.args[2]["features"][0]["properties"]["type"]
                      for c in r._draw_zone_layer.call_args_list
                      if isinstance(c.args[2], dict) and c.args[2].get("features")
                      and isinstance(c.args[2]["features"][0], dict))

    @pytest.mark.parametrize("keepout,nomop,shown", [
        (True, True, ["KeepOutZone", "NoMopZone", "SomethingNew"]),
        (True, False, ["KeepOutZone"]),
        (False, True, ["NoMopZone"]),
        (False, False, []),
    ])
    def test_each_type_follows_its_switch_and_unknown_types_need_both(self, keepout, nomop, shown):
        assert self._drawn(keepout, nomop) == shown


class TestImageDelivery:

    @pytest.mark.asyncio
    async def test_prime_rooms_redraw_only_when_live_data_changed(self, hass):
        r = _rooms_image()
        r.hass = hass
        r._async_refresh_if_map_changed = AsyncMock()
        r._png = b"cached"
        r._live_dirty = False
        assert await r.async_image() == b"cached"
        r._live_dirty = True
        r._render_png = MagicMock(return_value=b"fresh")
        assert await r.async_image() == b"fresh"
        assert r._live_dirty is False

    @pytest.mark.parametrize("rendered,expected", [(None, b"blank"), (b"heat", b"heat")])
    @pytest.mark.asyncio
    async def test_the_coverage_heatmap_or_a_blank_image(self, hass, rendered, expected):
        grid = MagicMock()
        grid.render_heatmap.return_value = rendered
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        c = img.RoombaCoverageImage(roomba, "B", grid, _real_entry(hass))
        c.hass = hass
        c._blank_image = MagicMock(return_value=b"blank")
        assert await c.async_image() == expected


# ── formerly tests/test_image_cloud_coverage.py ───────────────────────
#
# Cloud-coverage fallback on RoombaMapImage.async_image() for pose-less
# robots (i3+/lewis-daredevil firmware) — real captured i3+ fixtures.
#
# Covers RoombaMapImage._async_cloud_coverage_png() and its integration
# into async_image(): a robot whose `cap` has no `pose` key can never fill
# the local renderer (nothing ever calls add_pose()), so when the renderer
# has no data, async_image() falls back to compositing the newest cloud
# mission's coverage layer instead of serving a blank canvas. Pose-capable
# robots are untouched (const.has_pose() gates the whole path).

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text())


_MISSION_HISTORY = _load("irobot_missionhistory_i3plus.json")


_MISSION_UMF = _load("irobot_mission_umf_i3plus.json")


_HOUSEHOLD_PMAPS = _load("irobot_pmaps_i3plus.json")


# Newest record (index 0): nMssn 434, startTime 1787562322, pmaps_info
# points at pmap_id "0wOiGRFqRaKPJuVkOdwRtQ" / pmapv_id "260824T091539" —
# the MISSION's OWN map, deliberately different from the household pmap
# list's pmap_id "D8MepS5KRD6DTWlG-g5IEw" (irobot_pmaps_i3plus.json).
_NEWEST_RECORD = _MISSION_HISTORY[0]


_MISSION_PMAP_ID = "0wOiGRFqRaKPJuVkOdwRtQ"


_MISSION_PMAPV_ID = "260824T091539"


_HOUSEHOLD_PMAP_ID = _HOUSEHOLD_PMAPS[0]["pmap_id"]


def _pose_less_state() -> dict:
    """Mirror the real i3+ reported state: no 'pose' key under cap."""
    return {"cap": {}, "sku": "i355640", "softwareVer": "daredevil+2.6.0"}


def _pose_capable_state() -> dict:
    """A robot that has ACTUALLY SENT a pose.

    The gate read `cap.pose` -- the capability flag. @pk-1966's i7 on
    lewis firmware declares `pose: 2` and never sends one, so the gate
    blocked the fallback for exactly the robots needing it.
    """
    return {
        "cap": {"pose": 1},
        "pose": {"theta": 0, "point": {"x": 0, "y": 0}},
        "sku": "j755840",
        "softwareVer": "sapphire+1.0.0",
    }


def _promises_pose_but_never_sends() -> dict:
    """@pk-1966's i7: `cap.pose: 2`, no `pose` key, ever.

    Three robots have shown this -- @veronoicc in June, @Thonno's field
    dump, and his. A property of lewis 22.52.10, not an install.
    """
    return {"cap": {"pose": 2}, "sku": "i755640", "softwareVer": "lewis+22.52.10"}


async def _run_executor(fn, *args):
    return fn(*args)


def _make_entity_m(
    *,
    vacuum_state: dict,
    has_data: bool,
    raw_records=None,
    cloud_coordinator=True,
    config_entry_present=True,
):
    entity = RoombaMapImage.__new__(RoombaMapImage)
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)
    entity.vacuum = MagicMock()
    # The implementation resolves capabilities through
    # roomba_reported_state(self.vacuum), i.e. master_state["state"]["reported"]
    # — the same shape the MQTT layer delivers and that test_image.py builds.
    # Setting only .vacuum_state would leave the real lookup on a MagicMock.
    entity.vacuum.master_state = {"state": {"reported": vacuum_state}}
    entity.vacuum_state = vacuum_state

    renderer = MagicMock()
    renderer.has_data = has_data
    renderer.render = MagicMock(return_value=b"local-render-bytes")
    entity._renderer = renderer

    entity._cloud_coverage_png = None
    entity._cloud_coverage_png_for = None

    if not config_entry_present:
        entity._config_entry = None
        return entity, None

    config_entry = MagicMock()
    entity._config_entry = config_entry
    data = config_entry.runtime_data
    data.blid = "9A37307A20804F0CABE9B6011B82DDBE"
    data.mission_map_cache = {}
    # No keepout/observed-zone/room-outline overlays by default — kept
    # minimal so tests assert on the cloud-vs-local split, not overlays.
    data.umf_aligner = None
    data.outline_store = None

    if cloud_coordinator:
        cc = data.cloud_coordinator
        cc.raw_records = raw_records if raw_records is not None else []
        cc.api.get_pmap_umf = AsyncMock(return_value=_MISSION_UMF)
    else:
        data.cloud_coordinator = None

    return entity, data


class TestCloudCoverageFallbackCoreRegression:
    """A pose-less robot with an empty renderer must get the real cloud
    coverage PNG, not the blank 200x200 canvas."""

    @pytest.mark.asyncio
    async def test_async_image_returns_cloud_render_not_blank(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert result != RoombaMapImage._blank_image()

    @pytest.mark.asyncio
    async def test_fetch_uses_missions_own_pmap_not_household_pmap(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        await entity.async_image()

        cc = data.cloud_coordinator
        cc.api.get_pmap_umf.assert_awaited_once_with(
            data.blid, _MISSION_PMAP_ID, _MISSION_PMAPV_ID
        )
        # The whole feature rests on this distinction: the fetch must use
        # the MISSION's own pmap_id/pmapv_id (from pmaps_info on the
        # mission-history record), never the household pmap list's
        # pmap_id — the two are deliberately different in the fixtures.
        called_args = cc.api.get_pmap_umf.await_args.args
        assert _HOUSEHOLD_PMAP_ID not in called_args

    @pytest.mark.asyncio
    async def test_decoded_png_has_real_coverage_drawn(self):
        from PIL import Image

        from custom_components.roomba_plus.mission_map import _PNG_SIZE_PX

        entity, _ = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        img = Image.open(io.BytesIO(result))
        assert img.size == (_PNG_SIZE_PX, _PNG_SIZE_PX)
        colours = img.convert("RGB").getcolors(maxcolors=1_000_000)
        assert colours is not None and len(colours) > 1, (
            "917-point coverage layer must produce more than one colour "
            "— a single-colour image would mean nothing was drawn"
        )


class TestCloudCoverageFeatureGate:
    """Pose-capable robots must never take the cloud path, even with an
    empty renderer."""

    @pytest.mark.asyncio
    async def test_pose_capable_robot_never_calls_cloud_fetch(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_capable_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()
        assert result == b"local-render-bytes"
        entity._renderer.render.assert_called_once()


class TestCloudCoverageNoRegressionWhenRendererHasData:
    @pytest.mark.asyncio
    async def test_has_data_true_skips_cloud_path_entirely(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=True,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()
        assert result == b"local-render-bytes"


class TestCloudCoverageRendererNoneStillBlank:
    @pytest.mark.asyncio
    async def test_no_renderer_returns_blank_image(self):
        entity, _ = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        entity._renderer = None

        result = await entity.async_image()

        assert result == RoombaMapImage._blank_image()


class TestCloudCoverageFallsBackToLocalRenderGracefully:
    """Every "nothing to serve from the cloud" case must fall through to
    the existing local (blank, since has_data=False) render rather than
    raising."""

    @pytest.mark.asyncio
    async def test_no_config_entry(self):
        entity, _ = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            config_entry_present=False,
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        entity._renderer.render.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cloud_coordinator(self):
        entity, _ = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            cloud_coordinator=False,
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_empty_raw_records(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_record_has_pmaps_info(self):
        record_without_pmaps = {**_NEWEST_RECORD, "pmaps_info": []}
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[record_without_pmaps],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_missing_both_starttime_and_timestamp(self):
        record = {**_NEWEST_RECORD}
        record.pop("startTime", None)
        record.pop("timestamp", None)
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[record],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()


class TestCloudCoverageErrorHandling:
    @pytest.mark.asyncio
    async def test_mission_map_unavailable_falls_back_to_local(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            side_effect=MissionMapUnavailable("no coverage layer")
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_mission_map_mismatch_falls_back_to_local(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        # nmssn in the UMF header disagrees with the record's nMssn.
        mismatched_umf = json.loads(json.dumps(_MISSION_UMF))
        mismatched_umf["maps"][0]["map_header"]["nmssn"] = 999
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            return_value=mismatched_umf
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_generic_exception_does_not_propagate(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            side_effect=RuntimeError("cloud transport exploded")
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_empty_coverage_mm_falls_back_to_local(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        empty_coverage_umf = json.loads(json.dumps(_MISSION_UMF))
        for layer in empty_coverage_umf["maps"][0]["layers"]:
            if layer.get("layer_type") == "coverage":
                layer["geometry"]["coordinates"] = []
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            return_value=empty_coverage_umf
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"


class TestCloudCoverageCaching:
    @pytest.mark.asyncio
    async def test_two_calls_for_same_mission_render_only_once(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        first = await entity.async_image()
        second = await entity.async_image()

        assert first == second
        data.cloud_coordinator.api.get_pmap_umf.assert_awaited_once()
        assert entity._cloud_coverage_png_for == (
            f"c_{int(_NEWEST_RECORD['startTime'])}"
        )
        assert entity._cloud_coverage_png == first

    @pytest.mark.asyncio
    async def test_new_newest_record_invalidates_cache_and_rerenders(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        first = await entity.async_image()
        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 1

        newer_record = {**_NEWEST_RECORD, "startTime": _NEWEST_RECORD["startTime"] + 100}
        data.cloud_coordinator.raw_records = [newer_record]

        second = await entity.async_image()

        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 2
        assert entity._cloud_coverage_png_for == f"c_{int(newer_record['startTime'])}"
        assert second == first  # same UMF fixture -> identical render, different cache key


class TestCloudCoverageSkipsOverlays:
    """When the cloud path returns a PNG, async_image() must return it
    unchanged — the keepout/observed-zone overlay code (which projects
    pose-space mm through the local renderer's transform) must not run,
    since it has no meaning for the cloud-composited canvas."""

    @pytest.mark.asyncio
    async def test_keepout_zones_present_but_never_drawn(self):
        entity, data = _make_entity_m(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        # Configure keepout data that WOULD be drawn if the overlay code
        # ran — an aligned aligner plus non-empty keepout_zones.
        aligner = MagicMock()
        aligner.aligned = True
        aligner.keepout_polygon_umf.return_value = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        aligner.umf_to_pose.side_effect = lambda x, y: (x, y)
        data.umf_aligner = aligner
        data.cloud_coordinator.keepout_zones = [{"id": "z1"}]

        result = await entity.async_image()

        # render_keepout_zones is only reachable via self._renderer, and
        # the renderer here is a bare MagicMock with has_data=False — if
        # the overlay branch ran it would call render_keepout_zones() on
        # it and that call's return value (a MagicMock, not real PNG
        # bytes) would replace the result. Getting real PNG bytes back
        # proves the overlay branch was skipped.
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        entity._renderer.render_keepout_zones.assert_not_called()
        entity._renderer.render.assert_not_called()


class TestARobotThatPromisesPoseAndNeverSendsOne:
    """@pk-1966 asked whether the cleaning path would simply never work
    on his model. It would not have, and his asking is what found it.

    The fallback was gated on `cap.pose`. His i7 declares `pose: 2` and
    never sends one, so the gate stood aside on the robot that promises
    pose and does not deliver.
    """

    @pytest.mark.asyncio
    async def test_the_fallback_is_not_blocked_by_the_flag(self):
        entity, data = _make_entity_m(
            vacuum_state=_promises_pose_but_never_sends(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        data.cloud_coordinator.api.get_pmap_umf.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_robot_that_did_send_a_pose_is_untouched(self):
        """The rule the gate exists for: an empty renderer on a
        pose-reporting robot means the mission has not started yet."""
        entity, data = _make_entity_m(
            vacuum_state=_pose_capable_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()
        assert result == b"local-render-bytes"
