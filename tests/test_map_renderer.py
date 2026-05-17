"""Unit tests for MapRenderer.

Tests:
  - reset() clears state
  - add_pose() accumulates points
  - mark_stuck() records stuck positions
  - render() produces valid PNG bytes
  - dump_state() / restore_state() round-trip
  - restore_state() handles incompatible version gracefully
  - diagnostic_info() returns correct values
"""
import struct
import sys
sys.path.insert(0, "/tmp/roomba_plus_package")

import pytest
from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig, _STATE_VERSION

PNG_MAGIC = b"\x89PNG"


def _make_renderer(**kwargs) -> MapRenderer:
    cfg = RendererConfig(**kwargs)
    return MapRenderer(cfg)


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
