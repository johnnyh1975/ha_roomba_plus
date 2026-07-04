"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.

v3.2.1 — rewritten wholesale for the OutlineStore redesign: contour
extraction moved from per-mission PNG edge-detection + EMA-blend
(extract_contour_from_png / _merge_contours, removed) to direct boundary
derivation from GridStore.cells (compute_boundary_points_mm). See
outline_store.py module docstring and PAYLOAD_VERSION 4 comment for the
two field-confirmed bugs (offset, clipping) that drove this.
"""
from __future__ import annotations

import io
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from custom_components.roomba_plus.outline_store import MIN_CONTOUR_POINTS
from custom_components.roomba_plus.outline_store import MIN_MISSIONS_TO_SHOW
from custom_components.roomba_plus.outline_store import CELL_MM
from custom_components.roomba_plus.outline_store import OutlineStore
from custom_components.roomba_plus.outline_store import PAYLOAD_VERSION
from custom_components.roomba_plus.outline_store import compute_boundary_points_mm


def _make_white_png(width: int = 100, height: int = 100) -> bytes:
    """Return a minimal white PNG for testing."""
    from PIL import Image
    img = Image.new("RGB", (width, height), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestComputeBoundaryPointsMm:
    """compute_boundary_points_mm — pure geometric derivation, no I/O."""

    def test_empty_cells_returns_empty(self):
        assert compute_boundary_points_mm({}) == []

    def test_single_cell_is_its_own_boundary(self):
        pts = compute_boundary_points_mm({(0, 0): 1.0})
        assert pts == [(CELL_MM / 2, CELL_MM / 2)]

    def test_interior_cell_of_solid_block_is_not_boundary(self):
        """A 3x3 solid block: only the centre cell has all 4 orthogonal
        neighbours present — it must be excluded."""
        cells = {(x, y): 1.0 for x in range(-1, 2) for y in range(-1, 2)}
        pts = compute_boundary_points_mm(cells)
        centre_pt = (0 * CELL_MM + CELL_MM / 2, 0 * CELL_MM + CELL_MM / 2)
        assert centre_pt not in pts
        assert len(pts) == 8  # all but the centre

    def test_uses_4_connectivity_not_8(self):
        """A single diagonal-only gap (orthogonal neighbours present, only
        the diagonal missing) must NOT be flagged as boundary — verifies
        the check is 4-connectivity, matching the docstring's stated
        rationale (narrow corridor walls need orthogonal, not diagonal,
        gap detection)."""
        # Cross shape: centre has all 4 orthogonal neighbours, no diagonals
        cells = {
            (0, 0): 1.0,
            (1, 0): 1.0, (-1, 0): 1.0,
            (0, 1): 1.0, (0, -1): 1.0,
        }
        pts = compute_boundary_points_mm(cells)
        centre_pt = (CELL_MM / 2, CELL_MM / 2)
        assert centre_pt not in pts  # all 4 orthogonal neighbours present

    def test_thin_corridor_every_cell_is_boundary(self):
        """A 1-cell-wide corridor: every cell has at least one missing
        orthogonal neighbour (above/below), so all cells are boundary —
        exactly the case a doorway/corridor outline most needs to show."""
        cells = {(x, 0): 1.0 for x in range(5)}
        pts = compute_boundary_points_mm(cells)
        assert len(pts) == 5

    def test_returns_millimetre_cell_centres(self):
        pts = compute_boundary_points_mm({(2, -3): 1.0})
        assert pts == [(2 * CELL_MM + CELL_MM / 2, -3 * CELL_MM + CELL_MM / 2)]

    def test_custom_cell_mm(self):
        pts = compute_boundary_points_mm({(1, 1): 1.0}, cell_mm=100.0)
        assert pts == [(150.0, 150.0)]


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
        store._contour_points = [(float(i), float(i)) for i in range(MIN_CONTOUR_POINTS)]
        assert not store.ready

    def test_ready_false_below_min_points(self):
        store = OutlineStore()
        store._mission_count = MIN_MISSIONS_TO_SHOW
        store._contour_points = [(0.0, 0.0)] * (MIN_CONTOUR_POINTS - 1)
        assert not store.ready

    def test_ready_true_when_both_thresholds_met(self):
        store = OutlineStore()
        store._mission_count = MIN_MISSIONS_TO_SHOW
        store._contour_points = [(float(i), float(i)) for i in range(MIN_CONTOUR_POINTS)]
        assert store.ready

    def test_min_missions_to_show_is_two(self):
        assert MIN_MISSIONS_TO_SHOW == 2

    def test_min_contour_points_is_fifty(self):
        assert MIN_CONTOUR_POINTS == 50

    def test_cell_mm_matches_grid_store(self):
        assert CELL_MM == 150.0

    @pytest.mark.asyncio
    async def test_async_recompute_increments_mission_count(self):
        store = OutlineStore()
        hass = MagicMock()
        cells = {(x, 0): 1.0 for x in range(MIN_CONTOUR_POINTS + 10)}
        with patch.object(store, 'async_save', new_callable=AsyncMock):
            await store.async_recompute(cells, hass, "entry1")
        assert store._mission_count == 1
        assert len(store._contour_points) == MIN_CONTOUR_POINTS + 10

    @pytest.mark.asyncio
    async def test_async_recompute_skips_empty_cells(self):
        store = OutlineStore()
        hass = MagicMock()
        with patch.object(store, 'async_save', new_callable=AsyncMock) as mock_save:
            await store.async_recompute({}, hass, "entry1")
        assert store._mission_count == 0
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_recompute_replaces_not_blends(self):
        """v3.2.1 — deterministic recompute, no EMA: a smaller GridStore
        subset must fully replace the previous contour, not blend with it."""
        store = OutlineStore()
        store._contour_points = [(9999.0, 9999.0)]  # stale from a prior recompute
        hass = MagicMock()
        cells = {(0, 0): 1.0}
        with patch.object(store, 'async_save', new_callable=AsyncMock):
            await store.async_recompute(cells, hass, "entry1")
        assert store._contour_points == [(CELL_MM / 2, CELL_MM / 2)]
        assert (9999.0, 9999.0) not in store._contour_points

    def test_recompute_sync_updates_contour_and_count_without_io(self):
        """v3.2.1 FIELD FIX — the pure computation half must work with
        zero I/O and zero hass/entry_id, so a caller needing fresh
        contour_points RIGHT NOW (before persistence) can call it
        directly on the sync callback thread."""
        store = OutlineStore()
        store.recompute_sync({(2, 3): 1.0})
        assert store._mission_count == 1
        assert store._contour_points == [(2 * CELL_MM + CELL_MM / 2, 3 * CELL_MM + CELL_MM / 2)]

    def test_recompute_sync_skips_empty_cells(self):
        store = OutlineStore()
        store.recompute_sync({})
        assert store._mission_count == 0

    @pytest.mark.asyncio
    async def test_async_recompute_increments_mission_count_only_once(self):
        """v3.2.1 FIELD FIX — async_recompute() must call recompute_sync()
        internally EXACTLY once per invocation. Field-confirmed risk: if
        a caller ALSO calls recompute_sync() directly for the same
        mission (as image.py now does, for freeze-snapshot freshness)
        and then separately calls the full async_recompute() too,
        mission_count would silently double-count — this test pins
        async_recompute()'s OWN contract (one call in, one increment
        out) so that contract can't quietly regress even though the
        image.py-level double-call risk is a wiring concern, not this
        method's own.
        """
        store = OutlineStore()
        hass = MagicMock()
        cells = {(x, 0): 1.0 for x in range(60)}
        with patch.object(store, 'async_save', new_callable=AsyncMock):
            await store.async_recompute(cells, hass, "entry1")
        assert store._mission_count == 1

    @pytest.mark.asyncio
    async def test_async_load_restores_state(self):
        """v3.2.1 — PAYLOAD_VERSION bumped 3 -> 4 for the mm-not-px
        redesign; fixture must use the current PAYLOAD_VERSION."""
        store = OutlineStore()
        hass = MagicMock()

        loaded_data = {
            "version": PAYLOAD_VERSION,
            "mission_count": 5,
            "contour_points": [[10.5, 20.5], [30.0, 40.0]],
        }

        with patch(
            "homeassistant.helpers.storage.Store",
        ) as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(return_value=loaded_data)
            mock_store_cls.return_value = mock_instance
            await store.async_load(hass, "entry1")

        assert store._mission_count == 5
        assert store._contour_points == [(10.5, 20.5), (30.0, 40.0)]

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_async_load_discards_pre_v3_2_1_pixel_data(self):
        """v3.2.1 — payload version bumped 3 -> 4 specifically because
        contour_points changed MEANING from fixed-canvas pixels to
        real-world millimetres. A persisted file still at version 3 must
        be discarded outright, never reinterpreted — old pixel values
        would silently masquerade as plausible-looking millimetres."""
        store = OutlineStore()
        hass = MagicMock()

        loaded_data = {
            "version": 3,  # pre-redesign pixel-space format
            "mission_count": 8,
            "contour_points": [[0, 0], [599, 599]],  # px-space shape
            "canvas_size": [600, 600],  # field no longer exists
        }

        with patch(
            "homeassistant.helpers.storage.Store",
        ) as mock_store_cls:
            mock_instance = AsyncMock()
            mock_instance.async_load = AsyncMock(return_value=loaded_data)
            mock_store_cls.return_value = mock_instance
            await store.async_load(hass, "entry1")

        assert store._mission_count == 0
        assert store._contour_points == []


class TestRealStoreVersionRegression:
    """v2.8.3 — every test above mocks homeassistant.helpers.storage.Store
    entirely, which means none of them ever exercised HA's *own* on-disk
    version-mismatch handling. That blind spot is exactly how the v2.8.2
    field crash (NotImplementedError from Store._async_migrate_func, every
    EPHEMERAL install with pre-existing outline data on disk) shipped
    despite a fully green suite.

    A genuine real-Store, real-hass round-trip test was attempted here and
    deliberately dropped: `pytest_homeassistant_custom_component`'s
    `async_test_home_assistant()` is not currently wired into this project's
    pytest-asyncio setup (no `hass` fixture is used anywhere else in this
    suite), and using it ad hoc in a single test corrupted the event loop
    for unrelated, later tests in the same run, and separately broke this
    project's `custom_components` namespace-package import when done from
    inside the HA test context. Wiring up real-hass testing properly is a
    test-infrastructure project of its own, not something to bolt on
    mid-hotfix.

    The fix was instead confirmed directly against the real (unmocked)
    `homeassistant.helpers.storage.Store` class in a standalone script
    outside pytest: constructing `Store(hass, 2, key)` against an on-disk
    file saved with `Store(hass, 1, key)` reproduces the exact field
    NotImplementedError; constructing `Store(hass, 1, key)` (matching the
    pinned `_HA_STORE_VERSION` below) against the same file loads cleanly.
    The cheap guard rail below is what's practical to keep permanently in
    this suite.
    """

    def test_ha_store_version_is_pinned(self):
        """Guard rail: _HA_STORE_VERSION must never change again unless a
        real Store._async_migrate_func override ships alongside it. This
        constant is what's already embedded in every existing installation's
        on-disk file — changing it without a migration function reproduces
        the exact v2.8.2 field crash this test exists to prevent."""
        from custom_components.roomba_plus.outline_store import (
            _HA_STORE_VERSION,
        )
        assert _HA_STORE_VERSION == 1


class TestRenderRoomOutlineFitAlignment:
    """v3.2.1 — render_room_outline now takes real-world millimetre points
    and converts them via MapRenderer._mm_to_px_fit(), the same transform
    every other geometry overlay (walls, doors, zones) already uses — so
    the outline can never drift out of alignment with the auto-fitted
    live-map render the way the old fixed-px reconstruction risked.
    """

    def _make_renderer_with_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig
        renderer = MapRenderer(RendererConfig())
        renderer._last_png = _make_white_png(200, 200)
        return renderer

    def _px_of_grey_dots(self, png_bytes, base_png_bytes=None):
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        if base_png_bytes is not None:
            base = Image.open(io.BytesIO(base_png_bytes)).convert("RGB")
            return {
                (x, y)
                for x in range(w)
                for y in range(h)
                if img.getpixel((x, y)) != base.getpixel((x, y))
            }
        return {
            (x, y)
            for x in range(w)
            for y in range(h)
            if img.getpixel((x, y)) not in ((255, 255, 255), (0, 0, 0))
            and abs(img.getpixel((x, y))[0] - img.getpixel((x, y))[1]) < 12
            and abs(img.getpixel((x, y))[1] - img.getpixel((x, y))[2]) < 12
            and img.getpixel((x, y))[0] < 250
        }

    def test_returns_none_when_no_last_png(self):
        from custom_components.roomba_plus.map_renderer import MapRenderer, RendererConfig
        renderer = MapRenderer(RendererConfig())
        assert renderer._last_png is None
        result = renderer.render_room_outline([(1000.0, 1000.0)])
        assert result is None

    def test_returns_none_when_no_points(self):
        renderer = self._make_renderer_with_png()
        result = renderer.render_room_outline([])
        assert result is None

    def test_returns_bytes_when_points_and_png_present(self):
        renderer = self._make_renderer_with_png()
        points = [(1000.0, 1000.0), (5000.0, 5000.0), (10000.0, 10000.0)]
        result = renderer.render_room_outline(points)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_updates_last_png_after_render(self):
        renderer = self._make_renderer_with_png()
        points = [(1000.0, 1000.0), (5000.0, 5000.0)]
        result = renderer.render_room_outline(points)
        assert renderer._last_png == result

    def test_out_of_bounds_points_do_not_crash(self):
        renderer = self._make_renderer_with_png()
        # Points far outside the render canvas in world mm terms
        points = [(500_000.0, 500_000.0), (-500_000.0, -500_000.0)]
        result = renderer.render_room_outline(points)
        assert result is None or isinstance(result, bytes)

    def test_identity_fit_draws_contour_at_mm_to_px_fit_result(self):
        """Fresh renderer (identity fit state, no render() called yet) —
        a millimetre point must land exactly where _mm_to_px_fit() itself
        would place it, proving render_room_outline delegates rather than
        reimplementing the transform."""
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer, RendererConfig,
        )
        renderer = MapRenderer(RendererConfig())
        renderer._last_png = _make_white_png(600, 600)
        fx, fy = renderer._mm_to_px_fit(0.0, 0.0)
        png = renderer.render_room_outline([(0.0, 0.0)])
        dots = self._px_of_grey_dots(png)
        assert (fx, fy) in dots
        assert dots <= {(fx, fy), (fx + 1, fy), (fx, fy + 1), (fx + 1, fy + 1)}

    def test_fitted_render_maps_dock_mm_to_fit_dock_px(self):
        """After an auto-fit render, a contour point at the dock (0,0 mm)
        must land exactly on the FITTED dock position (_fit_cx/_fit_cy) —
        the same alignment invariant the field bug violated, now
        guaranteed structurally by delegating to _mm_to_px_fit()."""
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer, RendererConfig,
        )
        renderer = MapRenderer(RendererConfig())
        # Reproduce the field shape: content far off-centre and larger
        # than the canvas, so auto-fit produces ratio<1 plus translation.
        renderer.add_pose(-4400.0, 1700.0, 0.0)
        for x_mm in range(-4400, 3500, 200):
            renderer.add_pose(float(x_mm), 1700.0 - (x_mm + 4400) * 1.05, 0.0)
        renderer.render()
        fit_cx, fit_cy = renderer._fit_cx, renderer._fit_cy
        assert (fit_cx, fit_cy) != (300, 300), "test needs a non-identity fit"

        base_png = renderer._last_png
        png = renderer.render_room_outline([(0.0, 0.0)])
        dots = self._px_of_grey_dots(png, base_png_bytes=base_png)
        assert dots, "contour dot missing entirely"
        assert any(
            abs(x - fit_cx) <= 1 and abs(y - fit_cy) <= 1 for (x, y) in dots
        ), f"dock contour dot at {sorted(dots)[:4]} not at fitted dock ({fit_cx},{fit_cy})"


class TestRoombaDataOutlineField:

    def test_outline_store_field_defaults_none(self):
        import dataclasses
        from custom_components.roomba_plus.models import RoombaData
        fields = {f.name: f for f in dataclasses.fields(RoombaData)}
        assert "outline_store" in fields
        assert fields["outline_store"].default is None


class TestOutlineStoreCorruptionResilience:
    """Stress-test (real-store bug-hunt): async_load had NO try/except, so any
    corrupted field (mission_count=null → int(None); contour_points=[null] →
    len(None)) crashed the load. Now wrapped with a clean reset.
    """
    from custom_components.roomba_plus.outline_store import PAYLOAD_VERSION as _PV

    def _load_with(self, payload):
        from custom_components.roomba_plus.outline_store import OutlineStore
        from unittest.mock import patch, MagicMock, AsyncMock
        import asyncio
        async def mock_load_fn():
            return payload
        store_mock = MagicMock()
        store_mock.async_load = mock_load_fn
        store_mock.async_save = AsyncMock()
        os_ = OutlineStore()
        hass = MagicMock()
        # OutlineStore uses _get_store(hass, entry_id); patch Store at module level
        with patch("homeassistant.helpers.storage.Store",
                   return_value=store_mock):
            asyncio.get_event_loop().run_until_complete(os_.async_load(hass, "e1"))
        return os_

    def test_null_mission_count(self):
        os_ = self._load_with({"version": self._PV, "mission_count": None,
                               "contour_points": []})
        assert os_._mission_count == 0

    def test_null_contour_point(self):
        os_ = self._load_with({"version": self._PV, "mission_count": 3,
                               "contour_points": [None, [1, 2]]})
        # Bad point skipped or clean reset — no crash
        assert isinstance(os_._contour_points, list)
