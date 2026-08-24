"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import struct
import sys
import pytest
from custom_components.roomba_plus.map_renderer import MapRenderer
from custom_components.roomba_plus.map_renderer import RendererConfig
from custom_components.roomba_plus.map_renderer import _STATE_VERSION
from custom_components.roomba_plus.map_renderer import CLEANED_COLOUR
import os
import types
import math
from unittest.mock import MagicMock
from custom_components.roomba_plus.geometry_store import GeometryStore
from custom_components.roomba_plus.geometry_store import DoorMarker
from custom_components.roomba_plus.geometry_store import UserWall
from custom_components.roomba_plus.geometry_store import UserDoor
from custom_components.roomba_plus.geometry_store import UserObstacle
from unittest.mock import AsyncMock
from unittest.mock import patch
from custom_components.roomba_plus.umf_aligner import UmfAligner


PNG_MAGIC = b"\x89PNG"
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _make_renderer(**kwargs) -> MapRenderer:
    cfg = RendererConfig(**kwargs)
    return MapRenderer(cfg)


def _make_renderer_with_stores(geometry_store=None, room_seg_store=None, **kwargs):
    cfg = RendererConfig(**kwargs)
    return MapRenderer(
        cfg, geometry_store=geometry_store, room_seg_store=room_seg_store,
    )


def _room_seg_store_with_room(x_min, x_max, y_min, y_max, room_id="room_1",
                               name="Room 1", hidden=False):
    """ROOM-SEG Stage 5 test helper -- builds a RoomSegStore with a single
    SegRoom whose bbox exactly matches the given mm rectangle, via a
    rectangular cell block at CELL_MM resolution (SegRoom.bbox is derived
    from actual cells, not stored directly -- see room_seg_store.py)."""
    from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom, CELL_MM

    rss = RoomSegStore()
    gx0, gx1 = int(x_min // CELL_MM), int(x_max // CELL_MM)
    gy0, gy1 = int(y_min // CELL_MM), int(y_max // CELL_MM)
    cells = {(x, y) for x in range(gx0, gx1 + 1) for y in range(gy0, gy1 + 1)}
    rss.rooms = {room_id: SegRoom(id=room_id, name=name, hidden=hidden, cells=cells)}
    return rss


def _render_is_valid_png(renderer) -> bool:
    result = renderer.render()
    return isinstance(result, bytes) and result[:4] == PNG_MAGIC


def _pixel_at(png_bytes: bytes, x: int, y: int, size: int = 600) -> tuple:
    """Extract RGBA pixel at (x,y) from a raw PNG rendered by our renderer."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return img.getpixel((x, y))


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


class TestMapRendererReset:
    def test_reset_clears_points(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(200, 300, 90)
        r.reset()
        assert r.point_count == 0
        assert not r.has_data

    def test_reset_clears_stuck(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        r.reset()
        assert len(r._stuck_px) == 0

    def test_reset_clears_robot_position(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        r.reset()
        assert r._robot_px is None

    def test_reset_with_persist_keeps_cached_png(self):
        r = _make_renderer(persist=True)
        r.add_pose(100, 200, 0)
        r.render()
        r.reset()
        # persist=True: cached PNG kept between missions
        assert r._last_png is not None

    def test_reset_without_persist_clears_png(self):
        r = _make_renderer(persist=False)
        r.add_pose(100, 200, 0)
        r.render()
        r.reset()
        assert r._last_png is None


class TestAddPose:
    def test_first_dock_point_ignored(self):
        r = _make_renderer()
        r.add_pose(0, 0, 0)  # dock origin — should be skipped
        assert r.point_count == 0

    def test_non_zero_point_recorded(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        assert r.point_count == 1
        assert r.has_data

    def test_multiple_points(self):
        r = _make_renderer()
        # Start from i=1 to avoid the dock-skip (0,0,0 is ignored)
        for i in range(1, 11):
            r.add_pose(i * 100, 0, 0)
        assert r.point_count == 10

    def test_robot_position_updated(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(300, 400, 90)
        assert r._robot_px is not None

    def test_theta_stored(self):
        r = _make_renderer()
        r.add_pose(100, 200, 135)
        assert r._theta == 135


class TestDiscontinuityMarking:
    """v3.2.2 — REPLACES rejection with marking.

    The old contract dropped a suspicious pose. That left _points[-1]
    as a stale anchor, so every later real position measured against it
    too — the field-confirmed cascade (2026-06-19, 980 OG): a real
    mid-mission checkpoint in a 106 m² home had 1413 points stranded
    inside a ~0.7 m × 0.7 m pocket.

    It was papered over with a rejection streak: after two drops the
    third pose was taken. That reconnected the path to a position two
    updates later — a straight line across a room nobody drove.

    The root cause was the threshold itself. 500 mm was borrowed from
    another project; measured on this robot, poses arrive every ~1.8 s,
    so full-speed straight-line driving covers up to 542 mm. Ordinary
    movement sat just over the limit.

    New contract: EVERY pose is stored. A suspicious one records a
    break index, and only the LINE is split there. The anchor is always
    the true last position, so the cascade cannot form — not "is
    handled", cannot form.
    """

    @staticmethod
    def _renderer():
        return MapRenderer(RendererConfig())

    def test_a_jump_no_longer_drops_the_point(self):
        r = self._renderer()
        r.add_pose(100.0, 0.0, 0.0)
        r.add_pose(200.0, 0.0, 0.0)
        before = len(r._points)

        r.add_pose(9000.0, 9000.0, 0.0)

        assert len(r._points) == before + 1
        assert r._points_mm[-1] == (9000.0, 9000.0)

    def test_the_anchor_follows_the_jump(self):
        """The cascade's mechanism, checked directly: after a jump the
        comparison point must be the NEW position, not the old one."""
        r = self._renderer()
        r.add_pose(100.0, 0.0, 0.0)
        r.add_pose(9000.0, 9000.0, 0.0)

        # A normal step away from the jumped-to position.
        r.add_pose(9200.0, 9000.0, 0.0)

        # Only the jump itself is a break; the step after it is not.
        assert sorted(r._breaks) == [1]

    def test_a_long_run_after_a_jump_stays_intact(self):
        """The cascade in full: one jump, then ordinary driving. Under
        the old contract everything after the jump was measured against
        a stale anchor and stranded. Here it must all survive."""
        r = self._renderer()
        r.add_pose(100.0, 0.0, 0.0)
        r.add_pose(9000.0, 9000.0, 0.0)
        for i in range(1, 21):
            r.add_pose(9000.0 + i * 150.0, 9000.0, 0.0)

        assert len(r._points) == 22
        assert sorted(r._breaks) == [1], "only the jump breaks, nothing after it"

    def test_normal_driving_records_no_break(self):
        r = self._renderer()
        for i in range(1, 15):
            r.add_pose(i * 300.0, 0.0, 0.0)

        assert r._breaks == set()

    def test_reset_clears_the_parallel_lists(self):
        """_breaks indexes into _points. Clearing one without the others
        would leave breaks pointing at last mission's positions."""
        r = self._renderer()
        r.add_pose(100.0, 0.0, 0.0)
        r.add_pose(9000.0, 9000.0, 0.0)
        assert r._breaks

        r.reset()

        assert r._points == [] and r._points_mm == []
        assert r._point_ts == [] and r._breaks == set()

    def test_the_lists_stay_index_aligned(self):
        r = self._renderer()
        for i in range(1, 10):
            r.add_pose(i * 400.0, 0.0, 0.0)
        r.add_pose(9000.0, 9000.0, 0.0)

        assert len(r._points) == len(r._points_mm) == len(r._point_ts)


class TestAddPoseReturnValue:
    """v3.2.1 DOCK-ANCHOR — add_pose() must report whether it accepted a
    sustained jump, so callers can mark interpolation waypoints (see
    Dock_Anchor_Korrektur_Plan.md, 4c)."""

    def test_dock_skip_returns_false(self):
        r = _make_renderer()
        assert r.add_pose(0, 0, 0) is False

    def test_normal_point_returns_false(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        assert r.add_pose(150, 150, 0) is False

    def test_a_discontinuity_returns_true(self):
        """v3.2.2 — the flag now means "a break was recorded here",
        which is the same signal the dock-anchor correction wants: a
        point where continuity was lost. Under the old contract a lone
        jump returned False because the point had been dropped; nothing
        is dropped now, so the caller is told about every break."""
        r = MapRenderer(RendererConfig())
        r.add_pose(100, 100, 0)

        assert r.add_pose(5000, 5000, 0) is True
        assert r._breaks == {1}

    def test_a_normal_step_after_a_break_returns_false(self):
        r = MapRenderer(RendererConfig())
        r.add_pose(100, 100, 0)
        r.add_pose(5000, 5000, 0)

        assert r.add_pose(5150, 5000, 0) is False


class TestReplaceRange:
    """v3.2.1 DOCK-ANCHOR — retroactive correction of an already-rendered
    point range, for the buffered-segment dock-anchor correction."""

    def test_replaces_points_from_index_onward(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.add_pose(200, 200, 0)
        r.add_pose(250, 250, 0)  # will be "corrected" away
        assert r.point_count == 3

        r.replace_range(2, [(300.0, 300.0)])
        assert r.point_count == 3, "2 untouched points + 1 replacement point = 3"
        # first two points untouched
        px0, py0 = r._points[0]
        assert (px0, py0) == r._mm_to_px(100, 100)

    def test_replace_range_clears_png_cache(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r._last_png = b"stale-cached-bytes"
        r.replace_range(1, [(200.0, 200.0)])
        assert r._last_png is None

    def test_replace_range_updates_robot_px_to_last_corrected_point(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.replace_range(1, [(500.0, 500.0)])
        assert r._robot_px == r._mm_to_px(500, 500)

    def test_out_of_range_start_index_is_a_noop(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        before = list(r._points)
        r.replace_range(99, [(1.0, 1.0)])
        assert r._points == before

    def test_negative_start_index_is_a_noop(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        before = list(r._points)
        r.replace_range(-1, [(1.0, 1.0)])
        assert r._points == before

    def test_empty_correction_truncates_without_updating_robot_px(self):
        """An empty replacement (whole buffered segment discarded, e.g.
        stuck_and_abandoned with no dock contact) truncates the point
        list but must not clobber robot_px with nothing."""
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.add_pose(200, 200, 0)
        prior_robot_px = r._robot_px
        r.replace_range(1, [])
        assert r.point_count == 1
        assert r._robot_px == prior_robot_px


    """v3.2.1 LANDMARK-LOG — logs WHERE (not just how many) sustained pose
    jumps get accepted, as scaffolding for a future landmark-clustering
    structural signal (per the 980's vSLAM sensor-fusion architecture: an
    accepted jump corresponds to a genuine move or a camera-landmark
    relocalisation correction). Deliberately survives reset() — unlike
    self._points, the whole point is cross-mission accumulation.
    """

    def test_empty_initially(self):
        r = _make_renderer()
        assert r.accepted_jump_log == []

    def test_every_discontinuity_is_logged(self):
        """v3.2.2 — there is no longer a "rejected" category to exclude.
        A pose is never dropped, so the only question is whether
        continuity broke; when it did, the dock-anchor correction wants
        to know, and the log is how it finds out."""
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.add_pose(5000, 5000, 0)

        assert len(r.accepted_jump_log) == 1

    def test_the_break_is_logged_at_the_new_position(self):
        """The position AFTER the discontinuity is the one worth
        recording — it is where the robot actually is."""
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.add_pose(5100, 100, 0)

        assert len(r.accepted_jump_log) == 1
        x, y, theta, ts = r.accepted_jump_log[0]
        assert (x, y) == (5100.0, 100.0)
        assert theta == 0.0
        assert ts > 0

    def test_accepted_jump_captures_theta(self):
        """v3.2.1 — accepted_jump_log's theta_deg must reflect the actual
        heading at the jump, not just always default to 0 (the previous
        test used theta=0 throughout, which alone wouldn't distinguish
        'captures theta' from 'always reports 0')."""
        r = _make_renderer()
        r.add_pose(100, 100, 45.0)
        # v3.2.2 — one call is enough: a discontinuity is
        # recorded immediately, with no rejection streak to
        # outlast first.
        r.add_pose(5100, 100, 199.5)
        _, _, theta, _ = r.accepted_jump_log[0]
        assert theta == 199.5

    def test_normal_moves_never_log(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        for i in range(1, 21):
            r.add_pose(100 + i * 50, 100, 0)
        assert r.accepted_jump_log == []

    def test_survives_reset(self):
        """The whole point: unlike _points, this log accumulates ACROSS
        missions — reset() must not clear it."""
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        # v3.2.2 — one call is enough: a discontinuity is
        # recorded immediately, with no rejection streak to
        # outlast first.
        r.add_pose(5100, 100, 0)
        assert len(r.accepted_jump_log) == 1

        r.reset()
        assert len(r.accepted_jump_log) == 1, "must survive reset()"

    def test_accumulates_across_multiple_missions(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        # v3.2.2 — one call is enough: a discontinuity is
        # recorded immediately, with no rejection streak to
        # outlast first.
        r.add_pose(5100, 100, 0)
        r.reset()
        r.add_pose(200, 200, 0)
        # v3.2.2 — one call is enough: a discontinuity is
        # recorded immediately, with no rejection streak to
        # outlast first.
        r.add_pose(8100, 200, 0)
        assert len(r.accepted_jump_log) == 2

    def test_capped_at_max_length(self):
        from custom_components.roomba_plus.map_renderer import MAX_ACCEPTED_JUMP_LOG
        r = _make_renderer()
        for j in range(MAX_ACCEPTED_JUMP_LOG + 10):
            r.reset()
            r.add_pose(100, 100, 0)  # non-(0,0) anchor so the jump check runs
            # v3.2.2 — one call is enough: a discontinuity is
            # recorded immediately, with no rejection streak to
            # outlast first.
            r.add_pose(5000 + j, 100, 0)
        assert len(r.accepted_jump_log) == MAX_ACCEPTED_JUMP_LOG

    def test_dump_and_restore_state_roundtrip(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        # v3.2.2 — one call is enough: a discontinuity is
        # recorded immediately, with no rejection streak to
        # outlast first.
        r.add_pose(5100, 100, 0)
        dumped = r.dump_state()
        assert "accepted_jump_log" in dumped

        r2 = _make_renderer()
        assert r2.restore_state(dumped) is True
        assert r2.accepted_jump_log == r.accepted_jump_log

    def test_restore_old_state_without_field_defaults_empty(self):
        """v3.2.1 — additive field, no _STATE_VERSION bump: a dump saved
        before this existed simply has no 'accepted_jump_log' key."""
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        old_dump = r.dump_state()
        del old_dump["accepted_jump_log"]

        r2 = _make_renderer()
        assert r2.restore_state(old_dump) is True
        assert r2.accepted_jump_log == []

    def test_restore_pre_theta_3tuple_format_gets_placeholder(self):
        """v3.2.1 — a dump saved after accepted_jump_log existed but
        BEFORE theta_deg was added to it has 3-element entries
        (x, y, timestamp). These must load with a 0.0 placeholder theta,
        not raise or get silently dropped."""
        r = _make_renderer()
        old_dump = r.dump_state()
        old_dump["accepted_jump_log"] = [[100.0, 200.0, 1234567890.0]]

        r2 = _make_renderer()
        assert r2.restore_state(old_dump) is True
        assert r2.accepted_jump_log == [(100.0, 200.0, 0.0, 1234567890.0)]


class TestMarkStuck:
    def test_stuck_recorded_at_robot_position(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        assert len(r._stuck_px) == 1

    def test_stuck_without_position_ignored(self):
        r = _make_renderer()
        r.mark_stuck()  # no pose yet
        assert len(r._stuck_px) == 0

    def test_multiple_stuck_events(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        r.add_pose(500, 600, 0)
        r.mark_stuck()
        assert len(r._stuck_px) == 2


class TestRender:
    def test_render_returns_bytes(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        result = r.render()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_render_is_valid_png(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        result = r.render()
        assert result[:4] == PNG_MAGIC

    def test_render_empty_returns_blank_png(self):
        r = _make_renderer()
        result = r.render()
        # No points: returns last_png (None initially) — actually renders blank
        assert result is None or result[:4] == PNG_MAGIC

    def test_render_updates_cache_on_each_call(self):
        """render() always re-renders when points exist and stores result in _last_png."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.render()
        assert r._last_png is not None
        first = r._last_png
        # Second call with same points — still re-renders (no point-equality check)
        r.render()
        assert r._last_png is not None


class TestPersistence:
    def test_dump_state_keys(self):
        r = _make_renderer()
        r.add_pose(100, 200, 45)
        r.mark_stuck()
        state = r.dump_state()
        assert "version" in state
        assert "points" in state
        assert "stuck_px" in state
        assert "robot_px" in state
        assert "theta" in state

    def test_dump_state_version(self):
        r = _make_renderer()
        state = r.dump_state()
        assert state["version"] == _STATE_VERSION

    def test_dump_restore_round_trip(self):
        r1 = _make_renderer()
        r1.add_pose(100, 200, 45)
        r1.add_pose(300, 400, 90)
        r1.mark_stuck()
        state = r1.dump_state()

        r2 = _make_renderer()
        success = r2.restore_state(state)
        assert success is True
        assert r2.point_count == 2
        assert len(r2._stuck_px) == 1
        assert r2._theta == 90

    def test_restore_clears_cached_png(self):
        r1 = _make_renderer()
        r1.add_pose(100, 200, 0)
        state = r1.dump_state()

        r2 = _make_renderer()
        r2.restore_state(state)
        # PNG should be regenerated on demand, not restored from state
        assert r2._last_png is None

    def test_restore_wrong_version_returns_false(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        state = r.dump_state()
        state["version"] = 999  # future version
        success = r.restore_state(state)
        assert success is False

    def test_restore_empty_state_returns_false(self):
        r = _make_renderer()
        success = r.restore_state({})
        assert success is False

    def test_restore_no_robot_px(self):
        state = {
            "version": _STATE_VERSION,
            "points": [[100, 100]],
            "stuck_px": [],
            "robot_px": None,
            "theta": 0.0,
        }
        r = _make_renderer()
        r.restore_state(state)
        assert r._robot_px is None


class TestDiagnosticInfo:
    def test_diagnostic_info_keys(self):
        r = _make_renderer()
        info = r.diagnostic_info()
        assert "size_px" in info
        assert "scale_mm_per_px" in info
        assert "persist" in info
        assert "point_count" in info
        assert "has_cached_image" in info
        assert "stuck_event_count" in info

    def test_point_count_correct(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.add_pose(300, 400, 0)
        info = r.diagnostic_info()
        assert info["point_count"] == 2

    def test_stuck_count_correct(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        info = r.diagnostic_info()
        assert info["stuck_event_count"] == 1

    def test_has_cached_image_false_before_render(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        assert r.diagnostic_info()["has_cached_image"] is False

    def test_has_cached_image_true_after_render(self):
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.render()
        assert r.diagnostic_info()["has_cached_image"] is True


class TestMapRendererConstructorSignature:
    def test_no_stores_creates_renderer(self):
        r = MapRenderer(RendererConfig())
        assert r._geometry_store is None
        assert r._room_seg_store is None

    def test_stores_stored_as_attributes(self):
        from custom_components.roomba_plus.room_seg_store import RoomSegStore

        gs = GeometryStore()
        rs = RoomSegStore()
        r = MapRenderer(RendererConfig(), geometry_store=gs, room_seg_store=rs)
        assert r._geometry_store is gs
        assert r._room_seg_store is rs

    def test_render_with_no_stores_returns_valid_png(self):
        r = _make_renderer_with_stores()
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)

    def test_render_with_none_stores_no_exception(self):
        """render() must never raise when stores are None."""
        r = _make_renderer_with_stores(geometry_store=None, room_seg_store=None)
        r.render()  # no points, no stores — should return None or bytes, not raise


class TestInferenceSuggestionsLayer:
    def test_suggestion_produces_non_white_pixels(self):
        """With a room, some pixels should differ from background white.

        ROOM-SEG Stage 5 — uses room_seg_store=, not zone_store=, since
        _draw_inference_suggestions now reads RoomSegStore for the outline.
        (Previously this test passed "vacuously" via the unrelated pose-
        trail pixels even when zone_store's outline code path had nothing
        to draw — same risk applies here if room_seg_store ever stops
        actually being read, so the dedicated outline-pixel test below is
        the one that would actually catch that.)
        """
        rs = _room_seg_store_with_room(-1000, 1000, -1000, 1000, name="Living")
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, room_seg_store=rs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        pixels = list(img.getdata())
        white_pixel = (255, 255, 255, 255)
        all_white = all(p == white_pixel for p in pixels)
        assert not all_white

    def test_room_outline_drawn_room_with_no_outline_not_drawn(self):
        """Direct regression check that the outline pixels themselves
        come from RoomSegStore, not merely from the unrelated pose dot.

        Auto-fit scales the canvas to the POSE TRAIL's bounds only (see
        _compute_fit -- unchanged, pre-existing behaviour, not affected by
        this ROOM-SEG swap), not to any room/zone bbox. So the pose trail
        here must itself span the same area as the room, or the room's
        outline rectangle would fall outside the auto-fitted canvas and
        this comparison would (mis)report "no difference" for that reason
        instead of actually testing whether the outline draws.
        """
        from custom_components.roomba_plus.room_seg_store import RoomSegStore

        def _add_spanning_trail(r):
            # A DRIVABLE PATH, not four corners. The original jumped
            # ~2546 mm between opposite corners -- physically impossible
            # at 350 mm/s between two updates, and with v3.2.2 every
            # step became a discontinuity, so no polyline had two
            # connected points and nothing was drawn at all. The
            # corners are still reached; the robot now travels to them.
            corners = [(-900, -900), (900, -900), (900, 900), (-900, 900), (100, 100)]
            prev = corners[0]
            r.add_pose(*prev, 0)
            for target in corners[1:]:
                dx, dy = target[0] - prev[0], target[1] - prev[1]
                steps = max(1, int(max(abs(dx), abs(dy)) / 300))
                for i in range(1, steps + 1):
                    r.add_pose(prev[0] + dx * i / steps, prev[1] + dy * i / steps, 0)
                prev = target

        gs = GeometryStore()
        r_empty = _make_renderer_with_stores(geometry_store=gs, room_seg_store=RoomSegStore())
        _add_spanning_trail(r_empty)
        png_empty = r_empty.render()

        rs = _room_seg_store_with_room(-1000, 1000, -1000, 1000, name="Living")
        gs2 = GeometryStore()
        r_with_room = _make_renderer_with_stores(geometry_store=gs2, room_seg_store=rs)
        _add_spanning_trail(r_with_room)
        png_with_room = r_with_room.render()

        assert png_empty != png_with_room

    def test_hidden_room_outline_not_drawn(self):
        """ROOM-SEG Stage 5 — hidden rooms are excluded from the map
        overlay (a deliberate fix: the old ZoneStore-backed code drew
        hidden zones' outlines too, inconsistently with hidden zones
        already being excluded from selectors/repair issues)."""
        def _add_spanning_trail(r):
            for x, y in [(-900, -900), (900, 900), (-900, 900), (900, -900), (100, 100)]:
                r.add_pose(x, y, 0)

        gs = GeometryStore()
        rs_hidden = _room_seg_store_with_room(-1000, 1000, -1000, 1000, hidden=True)
        r_hidden = _make_renderer_with_stores(geometry_store=gs, room_seg_store=rs_hidden)
        _add_spanning_trail(r_hidden)
        png_hidden = r_hidden.render()

        from custom_components.roomba_plus.room_seg_store import RoomSegStore
        gs2 = GeometryStore()
        r_empty = _make_renderer_with_stores(geometry_store=gs2, room_seg_store=RoomSegStore())
        _add_spanning_trail(r_empty)
        png_empty = r_empty.render()

        assert png_hidden == png_empty

    def test_suggestion_suppressed_when_user_geometry_exists(self):
        """When user walls exist, suggestions should not be drawn.
        We verify this by checking the room_seg_store suggestion is
        suppressed (no exception, valid PNG, user geometry takes over).
        """
        rs = _room_seg_store_with_room(-1000, 1000, -1000, 1000)
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -500.0, "y1": 500.0,
                       "x2": 500.0, "y2": 500.0, "label": ""}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs, room_seg_store=rs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_door_marker_below_mission_count_threshold_not_drawn(self):
        """Markers with mission_count < 2 must not appear."""
        gs = GeometryStore()
        # Manually insert a marker with mission_count=1
        m = DoorMarker(id="dm_1", cx=0.0, cy=0.0, mission_count=1,
                       observations=[[0.0, 0.0]])
        gs.door_markers.append(m)
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC  # no crash, valid output

    def test_door_marker_at_mission_count_2_drawn_without_crash(self):
        """Markers with mission_count >= 2 should render without raising."""
        gs = GeometryStore()
        m = DoorMarker(id="dm_1", cx=0.0, cy=100.0, mission_count=2,
                       observations=[[0.0, 100.0], [0.0, 100.0]])
        gs.door_markers.append(m)
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_no_room_seg_store_no_suggestion_no_crash(self):
        """With room_seg_store=None, suggestions are skipped silently."""
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, room_seg_store=None)
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)


class TestUserGeometryLayer:
    def test_user_wall_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -500.0, "y1": 0.0,
                       "x2": 500.0, "y2": 0.0, "label": "north wall"}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_user_wall_produces_non_white_pixels(self):
        """A wall crossing the canvas should produce at least one dark pixel.

        With auto_fit enabled the wall is not necessarily at canvas centre,
        so we scan the full PNG for any non-white, non-background pixel.
        """
        from PIL import Image
        import io
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [{"id": "w1", "x1": -2000.0, "y1": 0.0,
                       "x2": 2000.0, "y2": 0.0, "label": ""}],
            "doors": [], "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        raw = img.tobytes()
        pixels = [
            (raw[i], raw[i+1], raw[i+2], raw[i+3])
            for i in range(0, len(raw), 4)
        ]
        # At least one pixel should be darker than pure white
        non_white = [p for p in pixels if p[:3] != (255, 255, 255)]
        assert len(non_white) > 0, "Expected wall pixels but image is all white"
        # Wall colour is dark grey — check at least one pixel has low R value
        dark_pixels = [p for p in non_white if p[0] < 150 and p[3] > 100]
        assert len(dark_pixels) > 0, "Expected dark wall pixels in image"

    def test_user_door_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [],
            "doors": [{"id": "d1", "cx": 0.0, "cy": 0.0, "width_mm": 875.0,
                       "theta_deg": 0.0, "label": "bedroom door"}],
            "obstacles": [],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_user_obstacle_renders_without_crash(self):
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [],
            "doors": [],
            "obstacles": [{"id": "o1", "x": -500.0, "y": -500.0,
                           "w": 1000.0, "h": 800.0, "label": "sofa"}],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_obstacle_off_canvas_does_not_crash(self):
        """Obstacles outside the map extent should be clamped, not crash."""
        gs = GeometryStore()
        gs.apply_user_edit({
            "walls": [], "doors": [],
            "obstacles": [{"id": "o1", "x": 50000.0, "y": 50000.0,
                           "w": 100.0, "h": 100.0, "label": ""}],
        })
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(100, 100, 0)
        r.render()  # must not raise

    def test_no_geometry_store_skips_user_layer_silently(self):
        """geometry_store=None must skip _draw_user_geometry without crashing."""
        r = _make_renderer_with_stores(geometry_store=None)
        r.add_pose(100, 200, 0)
        assert _render_is_valid_png(r)


class TestLayerOrdering:
    def test_cleaned_area_renders_over_suggestion(self):
        """Cleaned area (light blue) must be visible on top of suggestion layer.
        We add a pose well away from the dock, render, and scan a region around
        that pose position to find at least one non-white pixel from the
        cleaned-area circle (radius = 15px at scale=10mm/px).
        """
        rs = _room_seg_store_with_room(-2000, 2000, -2000, 2000, name="Big Room")
        gs = GeometryStore()
        r = _make_renderer_with_stores(geometry_store=gs, room_seg_store=rs)
        # Pose at (500, 500) mm — clearly away from dock and canvas edges
        r.add_pose(500, 500, 0)
        png = r.render()
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        px, py = r._mm_to_px(500, 500)
        # Scan a region larger than the cleaned-area circle radius (15px)
        scan_radius = 20
        size = r._cfg.size_px
        non_white_found = False
        for dy in range(-scan_radius, scan_radius + 1):
            for dx in range(-scan_radius, scan_radius + 1):
                x, y = px + dx, py + dy
                if 0 <= x < size and 0 <= y < size:
                    if img.getpixel((x, y)) != (255, 255, 255, 255):
                        non_white_found = True
                        break
            if non_white_found:
                break
        assert non_white_found, (
            f"Expected non-white pixel near ({px},{py}) — "
            "cleaned-area circle should be visible"
        )

    def test_existing_tests_still_pass_with_geometry_stores(self):
        """Existing render tests work identically when stores are omitted."""
        r = MapRenderer(RendererConfig())  # original constructor form
        r.add_pose(100, 200, 0)
        result = r.render()
        assert result[:4] == PNG_MAGIC


class TestDashedLinePrimitive:
    def test_zero_length_no_crash(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_line(draw, 50, 50, 50, 50, (0, 0, 0, 255))

    def test_horizontal_line_leaves_pixels(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_line(draw, 0, 50, 200, 50, (0, 0, 0, 255))
        # At least some pixel along the line should be non-white
        pixels = [img.getpixel((x, 50)) for x in range(200)]
        assert any(p != (255, 255, 255, 255) for p in pixels)


class TestDashedRectPrimitive:
    def test_dashed_rect_no_crash(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_dashed_rect(draw, 10, 10, 150, 150, (100, 100, 100, 255))


class TestCoverageTargets:
    """Targeted tests for previously uncovered paths to reach >=95% coverage."""

    def test_render_cached_frame_returned_when_no_points(self):
        """render() returns last_png immediately when no points and cache exists."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        first = r.render()
        # Force the points list empty but keep cache
        r._points.clear()
        result = r.render()
        assert result is first  # same cached bytes object

    def test_points_mm_property(self):
        """points_mm converts pixel coordinates back to mm."""
        r = _make_renderer()
        r.add_pose(500, 0, 0)
        pts = r.points_mm
        assert len(pts) == 1
        x_mm, y_mm = pts[0]
        assert abs(x_mm - 500) < r._cfg.scale  # within one pixel
        assert abs(y_mm - 0) < r._cfg.scale

    def test_draw_door_arc_no_crash(self):
        """_draw_door_arc with a realistic radius draws without raising."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (600, 600), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        r = MapRenderer(RendererConfig())
        r._draw_door_arc(draw, 300, 300, 40, 0.0)
        r._draw_door_arc(draw, 300, 300, 40, 45.0)
        r._draw_door_arc(draw, 300, 300, 40, 270.0)

    def test_draw_hatch_no_crash(self):
        """_draw_hatch on a rectangular region draws without raising."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (600, 600), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_hatch(draw, 50, 50, 200, 150, (186, 117, 23, 190))

    def test_restore_state_logs_on_success(self):
        """restore_state returns True for a valid state (covers the debug log line)."""
        r1 = _make_renderer()
        r1.add_pose(100, 200, 45)
        state = r1.dump_state()
        r2 = _make_renderer()
        result = r2.restore_state(state)
        assert result is True
        assert r2.point_count == 1


class TestPhase2CoverageTargets:
    """Additional tests to cover previously uncovered renderer paths."""

    def test_stuck_triangle_drawn(self):
        """Stuck event causes _draw_triangle to execute during render."""
        r = _make_renderer()
        r.add_pose(100, 200, 0)
        r.mark_stuck()
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_draw_path_with_two_points(self):
        """_draw_path executes when ≥2 points are present."""
        r = _make_renderer()
        r.add_pose(100, 0, 0)
        r.add_pose(500, 0, 0)
        png = r.render()
        assert png[:4] == PNG_MAGIC

    def test_interpolated_with_large_gap(self):
        """_interpolated inserts intermediate points across a large gap."""
        from custom_components.roomba_plus.map_renderer import MapRenderer
        pts = [(0, 0), (1000, 0)]  # 1000px gap — well above any max_gap_px
        result = MapRenderer._interpolated(pts, max_gap_px=10)
        assert len(result) > 2

    def test_restore_state_exception_path(self):
        """restore_state returns False on malformed state dict."""
        r = _make_renderer()
        result = r.restore_state({"version": 1, "points": "not_a_list"})
        assert result is False

    def test_draw_triangle_produces_pixels(self):
        """_draw_triangle draws a visible polygon."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img)
        MapRenderer._draw_triangle(draw, 100, 100, 15, (220, 60, 60, 255))
        px = img.getpixel((100, 112))  # bottom vertex area
        assert px[0] > 100  # red channel present

    def test_interpolated_empty_input(self):
        """_interpolated([]) returns empty list."""
        from custom_components.roomba_plus.map_renderer import MapRenderer
        assert MapRenderer._interpolated([], max_gap_px=10) == []


class TestRenderKeeputZones:
    """Verify render_keepout_zones works on _last_png not phantom _cache."""

    def _valid_png(self) -> bytes:
        """Return a minimal valid 2×2 PNG."""
        from PIL import Image
        import io
        img = Image.new("RGB", (2, 2), (0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_empty_list_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        assert r.render_keepout_zones([]) is None

    def test_no_last_png_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = None
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert result is None

    def test_valid_polygon_returns_bytes(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_updates_last_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        original = self._valid_png()
        r._last_png = original
        result = r.render_keepout_zones([[(0, 0), (1, 0), (1, 1)]])
        assert result is r._last_png  # _last_png updated in place


class TestRenderObservedZones:
    """v3.0.0 ZONE-OVERLAY — render_observed_zones mirrors keepout compositing."""

    def _valid_png(self, size: int = 100) -> bytes:
        from PIL import Image
        import io
        img = Image.new("RGB", (size, size), (200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_empty_list_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        assert r.render_observed_zones([]) is None

    def test_no_last_png_returns_none(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = None
        assert r.render_observed_zones([(50, 50, 10)]) is None

    def test_valid_circle_returns_png_bytes(self):
        """Single circle produces valid PNG bytes and updates _last_png."""
        from custom_components.roomba_plus.map_renderer import MapRenderer
        r = object.__new__(MapRenderer)
        r._last_png = self._valid_png()
        result = r.render_observed_zones([(50, 50, 10)])
        assert isinstance(result, bytes)
        assert result[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature
        assert result is r._last_png  # _last_png updated in place


# v3.2.1 REMOVED — render_for_outline() and its whole fixed-window
# coordinate-space test class are gone along with the method itself. The
# room outline no longer goes through a per-mission PNG render at all;
# see tests/test_outline_store.py::TestComputeBoundaryPointsMm and
# TestRenderRoomOutlineFitAlignment for the replacement coverage.



# ── MAP-FONT (v2.9.0) ────────────────────────────────────────────────────────

class TestMapFont:
    """MAP-FONT — embedded DejaVu Sans TTF instead of PIL's tiny bitmap default."""

    def test_load_font_returns_freetype_font(self):
        from PIL import ImageFont
        from custom_components.roomba_plus.map_renderer import _load_font
        font = _load_font(13)
        assert isinstance(font, ImageFont.FreeTypeFont)

    def test_load_font_falls_back_to_default_when_file_missing(self, monkeypatch, caplog):
        from custom_components.roomba_plus import map_renderer

        monkeypatch.setattr(
            map_renderer, "_FONT_PATH", map_renderer._FONT_PATH.parent / "missing.ttf"
        )
        # Must not raise — and must log a warning so a packaging error is visible.
        font = map_renderer._load_font(13)
        assert font is not None
        assert hasattr(font, "getbbox")  # usable as a PIL font object
        assert any("MAP-FONT" in r.message for r in caplog.records)

    def test_wall_label_renders_with_embedded_font_without_error(self):
        """Integration check: a labelled UserWall must render via LABEL_FONT_SMALL
        without exception, producing valid, non-trivial PNG bytes."""
        gs = GeometryStore()
        gs.walls = [UserWall(id="w1", x1=-500, y1=0, x2=500, y2=0, label="Kitchen wall")]
        r = _make_renderer_with_stores(geometry_store=gs)
        r.add_pose(0, 0, 0)
        png = r.render()
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG signature
        assert len(png) > 100


class TestReplaceRangeAutoFitInteraction:
    """v3.2.1 DOCK-ANCHOR — confirms Abschnitt 4a/8's open question
    ("does replace_range need special auto-fit handling?") is a
    non-issue: render() already recomputes _compute_fit() fresh from
    self._points on every call where the PNG cache was invalidated —
    replace_range() already clears that cache, so no special handling
    is needed in replace_range() itself.
    """

    def test_render_after_replace_range_reflects_new_bounds(self):
        r = _make_renderer()
        r.add_pose(100, 100, 0)
        r.add_pose(200, 200, 0)
        r.render()  # establish a cached frame with the ORIGINAL small bounds

        # Correct the second point far away — bounds should grow a lot.
        r.replace_range(1, [(5000.0, 5000.0)])
        png_after = r.render()

        assert png_after is not None
        # the corrected point is reflected in the point list used by fit
        assert r._points[-1] == r._mm_to_px(5000.0, 5000.0)


class TestSelfCalibratingThreshold:
    """v3.2.2 — the threshold measures itself from the robot's own stream.

    `_MAX_POSE_JUMP_MM = 500` was borrowed from another project, and the
    comment justifying it refuted itself: ~300 mm/s with updates every
    1-5 s is up to 1500 mm. Measured on a real 980, poses arrive every
    ~1.8 s, so full-speed straight-line driving covers up to 542 mm --
    just over the limit. That is what the cascade was made of.

    Replacing it with another fixed number would have been the same
    mistake in a new place, so both terms are measured: the interval
    floor from the median of recent gaps, the speed ceiling from a high
    percentile of observed steps.

    BOTH USE A ROBUST STATISTIC, and that is load-bearing. A mean would
    be dragged upward by exactly the discontinuities these exist to
    catch: one long stall or one big jump would raise the allowance and
    wave the next real one through.
    """

    @staticmethod
    def _drive(step_mm, interval_s, n=30, jump_at=None):
        from unittest.mock import patch

        r = MapRenderer(RendererConfig())
        clock = [1000.0]
        with patch(
            "custom_components.roomba_plus.map_renderer._time_mod.time",
            side_effect=lambda: clock[0],
        ):
            x = 0.0
            for i in range(n):
                x += 9000.0 if i == jump_at else step_mm
                r.add_pose(x, 0.0, 0.0)
                clock[0] += interval_s
        return r

    def test_a_slow_update_rate_widens_the_allowance(self):
        """The failure the borrowed constant caused: at a slower rate,
        ordinary driving covers more ground per message and was read as
        a jump."""
        fast = self._drive(300.0, 1.0)
        slow = self._drive(300.0, 4.0)

        assert slow._allowed_step_mm(4.0) > fast._allowed_step_mm(1.0)
        assert slow._breaks == set(), "normal driving must never break"

    def test_one_long_stall_does_not_raise_the_interval_floor(self):
        """A mean would be pulled up by the outlier and let the next
        real jump through. The median ignores a minority outright."""
        from unittest.mock import patch

        r = MapRenderer(RendererConfig())
        clock = [1000.0]
        with patch(
            "custom_components.roomba_plus.map_renderer._time_mod.time",
            side_effect=lambda: clock[0],
        ):
            for i in range(20):
                r.add_pose(i * 200.0, 0.0, 0.0)
                clock[0] += 30.0 if i == 10 else 1.8

        assert r._typical_dt_s() < 3.0

    def test_a_big_jump_does_not_raise_the_speed_ceiling(self):
        """Same argument for the other term: if a 9 m jump counted
        towards the observed speed, the ceiling would climb and the
        next jump would pass unnoticed."""
        clean = self._drive(400.0, 1.8)
        with_jump = self._drive(400.0, 1.8, jump_at=15)

        assert with_jump._observed_speed_mm_s() == clean._observed_speed_mm_s()
        assert sorted(with_jump._breaks) == [15], "the jump itself still breaks"

    def test_both_terms_fall_back_before_enough_samples(self):
        """A median or percentile over two points is not robust. The
        fallbacks are deliberately generous: too high costs a missed
        break, too low costs an invented one -- and only the second is
        visible as a wrong gap."""
        r = MapRenderer(RendererConfig())
        r.add_pose(100.0, 0.0, 0.0)

        assert r._typical_dt_s() == r._MIN_STEP_DT_S
        assert r._observed_speed_mm_s() == r._MAX_SPEED_MM_S

    def test_an_idle_stretch_cannot_tighten_the_check(self):
        """A near-stationary run measures a low speed. Letting that
        become the ceiling would make the filter stricter than the
        fixed limit ever was."""
        idle = self._drive(5.0, 1.8)

        assert idle._observed_speed_mm_s() >= idle._MAX_SPEED_MM_S
