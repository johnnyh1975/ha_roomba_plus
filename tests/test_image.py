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
from custom_components.roomba_plus.models import MapCapability
from custom_components.roomba_plus.umf_aligner import UmfAligner
import asyncio
from unittest.mock import call
import tests.conftest
from custom_components.roomba_plus.callbacks import make_mission_callback
from custom_components.roomba_plus.callbacks import make_mission_complete_callback
from custom_components.roomba_plus.const import CLEANING_PHASES
from custom_components.roomba_plus.const import MISSION_END_PHASES
import time
from typing import Any


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
        entity._config_entry = MagicMock()
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


def _make_map_entity():
    """Build a minimal RoombaMapImage suitable for exercising on_message()'s
    phase-transition / END-DEBOUNCE decision logic in isolation.

    _handle_mission_end is replaced with a MagicMock so the test verifies
    *when* it gets called without exercising the heavy downstream renderer/
    ZoneStore/GeometryStore/GridStore/OutlineStore side effects — those are
    covered separately by their own store-level tests.
    """
    from custom_components.roomba_plus.image import RoombaMapImage

    roomba = MagicMock()
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
    entity._config_entry = MagicMock()
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
    entity.hass = MagicMock()
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
        entity.hass = MagicMock()
        entity._config_entry = MagicMock()
        entity._renderer = MagicMock()
        entity._zone_store = None
        entity._map_capability = None
        entity._mission_points = []  # the empty-checkpoint case
        entity._mission_thetas = []
        entity._stuck_mission_points = []
        entity._mission_start_ts = "2026-06-18T09:00:00+00:00"

        with patch(
            "custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe"
        ) as mock_run:
            entity._handle_mission_end(ending_phase="")

        scheduled_coros = [c.args[0] for c in mock_run.call_args_list]
        assert any(
            getattr(c, "__qualname__", "").endswith("_async_clear_mission_checkpoint")
            for c in scheduled_coros
        ), "checkpoint clear must still be scheduled even with no mission_points"
        for c in scheduled_coros:
            c.close()  # avoid "coroutine was never awaited" warnings

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

        with patch(
            "custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe"
        ) as mock_run:
            _feed_map_entity(entity, _stuck_msg(1))

        assert mock_run.call_count == 1
        mock_run.call_args.args[0].close()  # avoid "never awaited" warning

    def test_stuck_event_before_cleaning_phase_does_not_checkpoint(self):
        """No mission has actually started yet (e.g. a stray stuck count
        left over from a prior session) — nothing meaningful to save."""
        entity = _make_map_entity()
        entity._had_cleaning_phase = False

        with patch(
            "custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe"
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
        entity._config_entry = MagicMock()
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
        entity._config_entry = MagicMock()
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
        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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

        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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
        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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

        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe"):
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
        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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
        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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

        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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

        with patch("custom_components.roomba_plus.image.asyncio.run_coroutine_threadsafe") as mock_run:
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
