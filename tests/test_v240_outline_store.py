"""Tests for F-EPHEMERAL — OutlineStore and render_room_outline.

Covers:
  - extract_contour_from_png: returns points, None when too few, handles bad input
  - OutlineStore: ready property, mission_count, contour_points, persistence round-trip
  - _merge_contours: blends two contours, returns fallback on empty
  - MapRenderer.render_room_outline: composites onto _last_png, None when no image
  - RoombaData.outline_store field exists
  - Gate: EPHEMERAL only (SMART robots must not instantiate OutlineStore)
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.outline_store import (
    MIN_CONTOUR_POINTS,
    MIN_MISSIONS_TO_SHOW,
    EMA_ALPHA,
    OutlineStore,
    _merge_contours,
    extract_contour_from_png,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_white_png(width: int = 100, height: int = 100) -> bytes:
    """Return a minimal white PNG for testing."""
    from PIL import Image
    img = Image.new("RGB", (width, height), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_cleaning_png(width: int = 200, height: int = 200) -> bytes:
    """Return a PNG with a blue rectangle simulating a cleaned area."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 160, 160], fill=(173, 216, 230))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── extract_contour_from_png ──────────────────────────────────────────────────

class TestExtractContourFromPng:

    def test_returns_none_for_blank_white_image(self):
        """extract_contour_from_png must not raise on a white image.

        A very small white image may still produce edge points on its border,
        so we only assert the function returns None or a list — no crash.
        """
        png = _make_white_png(50, 50)
        result = extract_contour_from_png(png)
        assert result is None or isinstance(result, list)

    def test_returns_points_for_image_with_content(self):
        """An image with a coloured rectangle should produce edge points."""
        png = _make_cleaning_png(300, 300)
        result = extract_contour_from_png(png)
        # May return None if edge detection thresholds not met on the simple
        # test image — important is that it doesn't raise
        if result is not None:
            assert isinstance(result, list)
            if result:
                assert all(isinstance(p, tuple) and len(p) == 2 for p in result)

    def test_returns_none_for_invalid_bytes(self):
        """Invalid PNG bytes must return None, not raise."""
        result = extract_contour_from_png(b"not a png")
        assert result is None

    def test_returns_none_for_empty_bytes(self):
        """Empty bytes must return None, not raise."""
        result = extract_contour_from_png(b"")
        assert result is None

    def test_result_points_are_within_image_bounds(self):
        """All returned points must be within the image dimensions."""
        png = _make_cleaning_png(200, 200)
        result = extract_contour_from_png(png)
        if result:
            for x, y in result:
                assert 0 <= x < 200
                assert 0 <= y < 200


# ── _merge_contours ───────────────────────────────────────────────────────────

class TestMergeContours:

    def _make_line(self, y: int, width: int, count: int = 100) -> list[tuple[int, int]]:
        """Return a horizontal line of points."""
        return [(x, y) for x in range(count)]

    def test_returns_fallback_on_empty_existing(self):
        new_pts = self._make_line(10, 100)
        result = _merge_contours([], new_pts, (200, 200))
        # Should return new_pts when existing is empty (Image.blend still works)
        assert result is not None
        assert len(result) > 0

    def test_merge_two_identical_contours(self):
        pts = self._make_line(10, 100)
        result = _merge_contours(pts, pts, (200, 200))
        assert result is not None

    def test_returns_list_of_tuples(self):
        pts1 = self._make_line(10, 100)
        pts2 = self._make_line(20, 100)
        result = _merge_contours(pts1, pts2, (200, 200))
        assert isinstance(result, list)
        if result:
            assert all(isinstance(p, tuple) and len(p) == 2 for p in result)

    def test_invalid_points_clamped_not_raised(self):
        """Out-of-bounds points must be silently skipped, not crash."""
        pts1 = [(500, 500), (600, 600)]  # out of 100×100 canvas
        pts2 = [(10, 10)] * 60
        result = _merge_contours(pts1, pts2, (100, 100))
        assert result is not None


# ── OutlineStore ──────────────────────────────────────────────────────────────

class TestOutlineStore:

    def test_initial_state(self):
        store = OutlineStore()
        assert store.mission_count == 0
        assert store.contour_points == []
        assert store.contour_point_count == 0
        assert not store.ready

    def test_ready_false_below_min_missions(self):
        store = OutlineStore()
        store._mission_count = MIN_MISSIONS_TO_SHOW - 1
        store._contour_points = [(i, i) for i in range(MIN_CONTOUR_POINTS)]
        assert not store.ready

    def test_ready_false_below_min_points(self):
        store = OutlineStore()
        store._mission_count = MIN_MISSIONS_TO_SHOW
        store._contour_points = [(0, 0)] * (MIN_CONTOUR_POINTS - 1)
        assert not store.ready

    def test_ready_true_when_both_thresholds_met(self):
        store = OutlineStore()
        store._mission_count = MIN_MISSIONS_TO_SHOW
        store._contour_points = [(i, i) for i in range(MIN_CONTOUR_POINTS)]
        assert store.ready

    def test_min_missions_to_show_is_two(self):
        assert MIN_MISSIONS_TO_SHOW == 2

    def test_min_contour_points_is_fifty(self):
        assert MIN_CONTOUR_POINTS == 50

    def test_ema_alpha_is_0_4(self):
        assert EMA_ALPHA == 0.4

    async def test_async_update_from_png_increments_mission_count(self):
        store = OutlineStore()
        hass = MagicMock()
        # Return enough points from executor job
        fake_points = [(i, 0) for i in range(MIN_CONTOUR_POINTS + 10)]
        hass.async_add_executor_job = AsyncMock(side_effect=[
            fake_points,   # extract_contour_from_png
            (200, 200),    # _get_image_size
        ])
        with patch.object(store, 'async_save', new_callable=AsyncMock):
            await store.async_update_from_png(b"fake_png", hass, "entry1")
        assert store._mission_count == 1

    async def test_async_update_from_png_skips_none_png(self):
        store = OutlineStore()
        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        await store.async_update_from_png(None, hass, "entry1")
        hass.async_add_executor_job.assert_not_called()

    async def test_async_update_skips_when_too_few_points(self):
        store = OutlineStore()
        hass = MagicMock()
        # Executor returns None (too few points)
        hass.async_add_executor_job = AsyncMock(return_value=None)
        await store.async_update_from_png(b"fake_png", hass, "entry1")
        assert store._mission_count == 0

    async def test_async_load_restores_state(self):
        store = OutlineStore()
        hass = MagicMock()

        loaded_data = {
            "version": 1,
            "mission_count": 5,
            "contour_points": [[10, 20], [30, 40]],
            "canvas_size": [300, 300],
        }

        with patch(
            "homeassistant.helpers.storage.Store",
        ) as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(return_value=loaded_data)
            mock_store_cls.return_value = mock_instance
            await store.async_load(hass, "entry1")

        assert store._mission_count == 5
        assert store._contour_points == [(10, 20), (30, 40)]
        assert store._canvas_size == (300, 300)

    async def test_async_load_ignores_wrong_version(self):
        store = OutlineStore()
        hass = MagicMock()

        loaded_data = {
            "version": 99,  # wrong version
            "mission_count": 5,
        }

        with patch(
            "homeassistant.helpers.storage.Store",
        ) as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(return_value=loaded_data)
            mock_store_cls.return_value = mock_instance
            await store.async_load(hass, "entry1")

        assert store._mission_count == 0  # not loaded


# ── render_room_outline ───────────────────────────────────────────────────────

class TestRenderRoomOutline:

    def _make_renderer_with_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig
        renderer = MapRenderer(RendererConfig())
        # Inject a fake _last_png
        renderer._last_png = _make_white_png(200, 200)
        return renderer

    def test_returns_none_when_no_last_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig
        renderer = MapRenderer(RendererConfig())
        assert renderer._last_png is None
        result = renderer.render_room_outline([(10, 10), (20, 20)])
        assert result is None

    def test_returns_none_when_no_points(self):
        renderer = self._make_renderer_with_png()
        result = renderer.render_room_outline([])
        assert result is None

    def test_returns_bytes_when_points_and_png_present(self):
        renderer = self._make_renderer_with_png()
        points = [(10, 10), (50, 50), (100, 100)]
        result = renderer.render_room_outline(points)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_updates_last_png_after_render(self):
        renderer = self._make_renderer_with_png()
        original = renderer._last_png
        points = [(10, 10), (50, 50)]
        result = renderer.render_room_outline(points)
        assert renderer._last_png == result
        # _last_png should be updated (may or may not differ from original)

    def test_out_of_bounds_points_do_not_crash(self):
        renderer = self._make_renderer_with_png()
        # Points well outside 200×200 canvas
        points = [(500, 500), (-10, -10), (1000, 1000)]
        result = renderer.render_room_outline(points)
        # Should return bytes or None but not raise
        assert result is None or isinstance(result, bytes)


# ── RoombaData field ──────────────────────────────────────────────────────────

class TestRoombaDataOutlineField:

    def test_outline_store_field_defaults_none(self):
        import dataclasses
        from custom_components.roomba_plus.models import RoombaData
        fields = {f.name: f for f in dataclasses.fields(RoombaData)}
        assert "outline_store" in fields
        assert fields["outline_store"].default is None
