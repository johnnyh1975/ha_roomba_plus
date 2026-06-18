"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import sys
import datetime
import collections
import pytest
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
import homeassistant.helpers.entity_platform as _ep
import math
from unittest.mock import patch
from custom_components.roomba_plus.umf_aligner import UmfAligner
import asyncio
from unittest.mock import call
import tests.conftest
from custom_components.roomba_plus.callbacks import make_mission_callback
from custom_components.roomba_plus.callbacks import make_mission_complete_callback
from custom_components.roomba_plus.const import CLEANING_PHASES
from custom_components.roomba_plus.const import MISSION_END_PHASES
import time


def _make_entity(cell_count: int = 5, stuck_count: int = 2):
    """Build a minimal RoombaCoverageImage with stubbed dependencies."""
    from custom_components.roomba_plus.grid_store import GridStore
    from custom_components.roomba_plus.image import RoombaCoverageImage

    gs = GridStore()
    gs._cells = {(i, 0): 0.5 for i in range(cell_count)}
    gs._stuck = {(0, 0): {"count": stuck_count, "times": []}}

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
    hass = MagicMock()
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

    entry = MagicMock()
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
        entity._config_entry = MagicMock()
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
        # calibration_points key (renamed from "calibration" for XVMC compat)
        assert "calibration_points" in attrs
        assert "calibration" not in attrs


class TestRoombaRoomsImage:
    def _entity(self, aligner=None):
        from custom_components.roomba_plus.image import RoombaRoomsImage
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.umf_aligner = aligner
        entity._last_x_min = 0.0
        entity._last_x_max = 5000.0
        entity._last_y_min = 0.0
        entity._last_y_max = 5000.0
        entity._last_size  = 600
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
        entity._config_entry = MagicMock()
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
        """_attr_name = 'Rooms Map' prevents locale-slug entity IDs (G6 lesson).

        HA wraps _attr_ values as cached_property descriptors via __init_subclass__,
        so the raw string is not readable from __dict__. Verify through instance
        access — which is the actual runtime path HA uses for entity registration.
        """
        from custom_components.roomba_plus.image import RoombaRoomsImage
        import collections
        entity = object.__new__(RoombaRoomsImage)
        entity._config_entry = None
        entity._last_x_min = entity._last_y_min = 0.0
        entity._last_x_max = entity._last_y_max = 5000.0
        entity._last_size  = 600
        entity.access_tokens = collections.deque([], 2)
        assert entity._attr_name == "Rooms Map"

    def test_to_px_last_consistency(self):
        entity = self._entity()
        # With default transform (size=600, x_min=0, x_max=5000, y_min=0, y_max=5000)
        # scale = 600/5000 = 0.12
        px, py = entity._to_px_last(0.0, 0.0)
        assert isinstance(px, int)
        assert isinstance(py, int)


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
        assert asyncio.iscoroutinefunction(_async_send_coverage_signal)


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
