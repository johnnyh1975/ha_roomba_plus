"""OutlineStore — accumulated room boundary for EPHEMERAL robots (F-EPHEMERAL, v2.4.0).

900-series robots have no persistent Cloud map — every mission starts fresh.
NickWaterton's roomba980-Python library shows that a usable room outline can
be derived from accumulated pose paths using PIL edge detection.

This module extracts a contour from each mission's rendered cleaning image,
EMA-merges it across missions, and persists the result so the outline
sharpens progressively without Cloud dependency.

Gate: EPHEMERAL robots only. SMART robots use UmfAligner room polygons.

Design:
  - extract_contour_from_png() runs in executor at mission end.
  - EMA merge: rasterise both contours onto same-size masks, blend, re-extract.
  - Storage key: roomba_plus_outline_{entry_id}.
  - ready property: mission_count >= MIN_MISSIONS_TO_SHOW.

Reference: NickWaterton/Roomba980-Python draw_final_map() + draw_room_outline()
"""
from __future__ import annotations

import io
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX   = "roomba_plus_outline"
STORAGE_VERSION      = 1

# Don't show outline until we have at least this many missions
MIN_MISSIONS_TO_SHOW = 2
# Weight of new contour vs accumulated (higher = faster to adapt)
EMA_ALPHA            = 0.4
# Minimum contour points needed for a meaningful outline
MIN_CONTOUR_POINTS   = 50
# Edge-detection threshold (0–255): only strong edges kept
EDGE_THRESHOLD       = 200


def extract_contour_from_png(png_bytes: bytes) -> list[tuple[int, int]] | None:
    """Extract room boundary contour from a cleaning-path PNG using PIL edge detection.

    Mirrors NickWaterton draw_final_map() PIL fallback — no OpenCV required:
      1. Convert to greyscale and smooth to reduce noise in the pose path
      2. FIND_EDGES to detect the boundary of the cleaned area
      3. Invert and threshold to get a binary edge mask
      4. Extract non-zero pixel coordinates as the contour

    Returns list of (x, y) pixel points, or None when the image has fewer
    than MIN_CONTOUR_POINTS pixels (too sparse to produce a useful outline).

    Gate: caller is responsible for EPHEMERAL-only invocation.
    This function has no map-capability awareness.
    """
    try:
        from PIL import Image, ImageFilter, ImageOps
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        smoothed = img.filter(ImageFilter.SMOOTH_MORE)
        edges = smoothed.filter(ImageFilter.FIND_EDGES)
        edges = ImageOps.invert(edges)
        # Threshold: keep only strong edges
        edges = edges.point(lambda p: 255 if p > EDGE_THRESHOLD else 0)
        # Extract dark (edge) pixels — inverted so edges are black (0)
        points = [
            (x, y)
            for y in range(edges.height)
            for x in range(edges.width)
            if edges.getpixel((x, y)) == 0
        ]
        return points if len(points) >= MIN_CONTOUR_POINTS else None
    except Exception:  # noqa: BLE001
        _LOGGER.debug("OutlineStore: extract_contour_from_png failed", exc_info=True)
        return None


def _merge_contours(
    existing: list[tuple[int, int]],
    new_points: list[tuple[int, int]],
    size: tuple[int, int],
) -> list[tuple[int, int]]:
    """EMA-merge two contour point sets onto the same canvas.

    Rasterises both contours onto same-size binary masks, blends with EMA_ALPHA,
    then re-extracts points from the blended mask. Avoids point-matching complexity.

    Args:
        existing: accumulated contour pixels from previous missions.
        new_points: contour pixels from the latest mission.
        size: (width, height) canvas size in pixels.

    Returns merged contour as a list of (x, y) pixels.
    """
    try:
        from PIL import Image
        w, h = size

        def _to_mask(points: list[tuple[int, int]]) -> Image.Image:
            mask = Image.new("L", (w, h), 0)
            for x, y in points:
                if 0 <= x < w and 0 <= y < h:
                    mask.putpixel((x, y), 255)
            return mask

        mask_old = _to_mask(existing)
        mask_new = _to_mask(new_points)

        # EMA blend: new × alpha + old × (1 - alpha)
        blended = Image.blend(mask_old, mask_new, alpha=EMA_ALPHA)
        # Threshold the blend at 50% to produce a binary mask
        blended = blended.point(lambda p: 255 if p > 127 else 0)
        merged = [
            (x, y)
            for y in range(h)
            for x in range(w)
            if blended.getpixel((x, y)) > 127
        ]
        return merged if len(merged) >= MIN_CONTOUR_POINTS else new_points
    except Exception:  # noqa: BLE001
        _LOGGER.debug("OutlineStore: _merge_contours failed", exc_info=True)
        return new_points


class OutlineStore:
    """Accumulates room boundary outline from pose path images across missions.

    After each EPHEMERAL mission, extract_contour_from_png() extracts a contour
    from the rendered cleaning image. Contours are EMA-merged so the outline
    sharpens progressively. Persisted as a list of (x, y) pixel points.

    Lifecycle:
      - Instantiate in async_setup_entry for EPHEMERAL robots with map_enabled.
      - Call async_load() immediately after.
      - Store in entry.runtime_data.outline_store.
      - Call async_update_from_png() at mission end (image.py _handle_mission_end).
      - render_room_outline() reads contour_points from this store.
    """

    def __init__(self) -> None:
        self._mission_count: int = 0
        self._contour_points: list[tuple[int, int]] = []
        self._canvas_size: tuple[int, int] | None = None
        # P2: Store is stateless — construct once and reuse across load/save calls
        self._store: Any = None

    def _get_store(self, hass: Any, entry_id: str) -> Any:
        """Return the cached Store, creating it on first call."""
        if self._store is None:
            from homeassistant.helpers.storage import Store
            self._store = Store(
                hass,
                STORAGE_VERSION,
                f"{STORAGE_KEY_PREFIX}_{entry_id}",
                private=True,
            )
        return self._store

    # ── Persistence ────────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        """Load persisted outline state from hass.storage."""
        store = self._get_store(hass, entry_id)
        data = await store.async_load()
        if data and isinstance(data, dict) and data.get("version") == STORAGE_VERSION:
            self._mission_count = int(data.get("mission_count", 0))
            raw = data.get("contour_points", [])
            self._contour_points = [(int(p[0]), int(p[1])) for p in raw if len(p) == 2]
            canvas = data.get("canvas_size")
            if canvas and len(canvas) == 2:
                self._canvas_size = (int(canvas[0]), int(canvas[1]))
        _LOGGER.debug(
            "OutlineStore: loaded mission_count=%d contour_points=%d",
            self._mission_count, len(self._contour_points),
        )

    async def async_save(self, hass: Any, entry_id: str) -> None:
        """Persist outline state to hass.storage."""
        store = self._get_store(hass, entry_id)
        await store.async_save({
            "version": STORAGE_VERSION,
            "mission_count": self._mission_count,
            "contour_points": list(self._contour_points),
            "canvas_size": list(self._canvas_size) if self._canvas_size else None,
        })

    # ── Update ─────────────────────────────────────────────────────────────────

    async def async_update_from_png(
        self,
        png_bytes: bytes | None,
        hass: Any,
        entry_id: str,
    ) -> None:
        """Extract contour from mission PNG and merge into accumulated outline.

        Runs extract_contour_from_png in executor (PIL is synchronous).
        Saves to hass.storage on success.
        """
        if not png_bytes:
            return
        try:
            new_points = await hass.async_add_executor_job(
                extract_contour_from_png, png_bytes
            )
            if new_points is None:
                _LOGGER.debug(
                    "OutlineStore: contour extraction yielded too few points — skipping"
                )
                return

            # Determine canvas size from the image
            canvas_size = await hass.async_add_executor_job(
                self._get_image_size, png_bytes
            )

            if self._contour_points and canvas_size:
                merged = await hass.async_add_executor_job(
                    _merge_contours,
                    self._contour_points,
                    new_points,
                    canvas_size,
                )
                self._contour_points = merged
            else:
                self._contour_points = new_points

            if canvas_size:
                self._canvas_size = canvas_size

            self._mission_count += 1
            await self.async_save(hass, entry_id)
            _LOGGER.debug(
                "OutlineStore: updated mission_count=%d contour_points=%d",
                self._mission_count, len(self._contour_points),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("OutlineStore: unexpected error in async_update_from_png")

    @staticmethod
    def _get_image_size(png_bytes: bytes) -> tuple[int, int] | None:
        """Return (width, height) of a PNG without full decode."""
        try:
            from PIL import Image
            with Image.open(io.BytesIO(png_bytes)) as img:
                return img.size
        except Exception:  # noqa: BLE001
            return None

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """True when enough missions have accumulated to show the outline."""
        return (
            self._mission_count >= MIN_MISSIONS_TO_SHOW
            and len(self._contour_points) >= MIN_CONTOUR_POINTS
        )

    @property
    def mission_count(self) -> int:
        """Number of missions that have contributed to the outline."""
        return self._mission_count

    @property
    def contour_points(self) -> list[tuple[int, int]]:
        """List of (x, y) pixel points forming the accumulated room boundary."""
        return self._contour_points

    @property
    def contour_point_count(self) -> int:
        """Number of contour points (for diagnostics)."""
        return len(self._contour_points)
