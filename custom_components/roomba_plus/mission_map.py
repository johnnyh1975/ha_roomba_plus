"""v3.3.0 MISSION-MAP — per-mission coverage fetch + compositor.

Fetch half: `async_fetch_mission_map()` is the ONE reusable, verified
code path from a MissionStore record to that mission's coverage layers —
deliberately not REST-handler internals, so the future incremental
GridStore aggregator (GS-SMART-COVERAGE backlog candidate) reuses the
same verify-gated fetch instead of growing a second one.

Render half: `render_mission_map_png()` — stateless compositor for
finished missions. `map_renderer.py` stays the live-mission machine
(point buffers, dock anchor, checkpoints); a completed mission only
needs room outlines + coverage points, so this is a pure function that
imports the renderer's style constants (one style, two consumers).

Data chain (field-verified, boutXIII RESEARCH-MISSIONMAP + V2):
record.pmaps_info -> (pmap_id, pmapv_id of THIS mission)
-> cloud get_pmap_umf(activeDetails=2)
-> maps[0].map_header verifies nmssn against the record
-> layers[coverage].geometry: multipoint2d coordinates (m) + point_area.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import RoombaData

# UMF coordinates come in metres (point_area [0.1049, 0.1049] = one robot
# footprint); the project's canonical spatial unit is mm.
_M_TO_MM = 1000.0

_CACHE_MAX = 10
_CACHE_TTL_SEC = 24 * 3600


class MissionMapUnavailable(Exception):
    """404 class — record unknown, no pmaps_info, or empty coverage layer
    (the latter is the untested-lewis-firmware case, plan D5)."""


class MissionMapMismatch(Exception):
    """409 class — the cloud returned a map whose header does not match
    the requested mission. Never serve the wrong map silently (plan D4)."""


def _extract_layers(
    umf: dict[str, Any],
) -> tuple[dict[str, Any], list[Any], list[Any], list[Any]]:
    """Return (map_header, coverage_coords, point_area, coverage_poly)
    from a UMF response; every part defaults to empty on absence."""
    maps = umf.get("maps") or []
    map0 = maps[0] if maps and isinstance(maps[0], dict) else {}
    header = map0.get("map_header") or {}
    coverage: list[Any] = []
    point_area: list[Any] = []
    coverage_poly: list[Any] = []
    for layer in map0.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        geom = layer.get("geometry") or {}
        if layer.get("layer_type") == "coverage":
            coverage = geom.get("coordinates") or []
            point_area = geom.get("point_area") or []
        elif layer.get("layer_type") == "coverage_poly":
            coverage_poly = geom.get("coordinates") or []
    return header, coverage, point_area, coverage_poly


def _points_to_mm(coverage: list[Any]) -> list[list[float]]:
    """Bug-hunt round 3 — per-point conversion guard: a single
    non-numeric cloud coordinate skips that point instead of turning
    the whole request into a 500."""
    out: list[list[float]] = []
    for p in coverage:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            out.append([float(p[0]) * _M_TO_MM, float(p[1]) * _M_TO_MM])
        except (TypeError, ValueError):
            continue
    return out


async def async_fetch_mission_map(
    data: "RoombaData", record: dict[str, Any]
) -> dict[str, Any]:
    """Fetch + verify the coverage payload for one mission record.

    Raises MissionMapUnavailable / MissionMapMismatch; cloud transport
    errors (CloudApiError) propagate for the caller's 502 handling.
    Results are cached in-memory (TTL 24 h, max 10 missions) — repeated
    card/browser hits cost no second cloud call.
    """
    record_id = str(record.get("id", ""))
    cache = data.mission_map_cache
    now = time.time()
    hit = cache.get(record_id)
    if hit is not None and now - hit[0] < _CACHE_TTL_SEC:
        return hit[1]

    pmaps_info = record.get("pmaps_info") or []
    first = pmaps_info[0] if pmaps_info and isinstance(pmaps_info[0], dict) else {}
    pmap_id = first.get("pmap_id")
    pmapv_id = first.get("pmapv_id")
    if not pmap_id or not pmapv_id:
        raise MissionMapUnavailable(
            "record carries no pmaps_info (EPHEMERAL tier, or recorded "
            "before v3.3.0's cloud merge of that field)"
        )
    cc = data.cloud_coordinator
    if cc is None:
        raise MissionMapUnavailable("cloud not configured")

    umf = await cc.api.get_pmap_umf(data.blid, pmap_id, pmapv_id)
    header, coverage, point_area, coverage_poly = _extract_layers(umf)

    # Plan D4 — verification gate: the boutXIII confirmation logic as a
    # runtime guard. nMssn is cloud-merged into records since v2.x.
    rec_nmssn = record.get("nMssn")
    hdr_nmssn = header.get("nmssn")
    # Bug-hunt round 2 — non-numeric values (degraded cloud data) must
    # not turn the guard itself into a 500; unparseable → treat as
    # unverifiable and let the coverage check decide.
    try:
        _rec_n = int(rec_nmssn) if rec_nmssn is not None else None
        _hdr_n = int(hdr_nmssn) if hdr_nmssn is not None else None
    except (TypeError, ValueError):
        _rec_n = _hdr_n = None
    if _rec_n is not None and _hdr_n is not None and _rec_n != _hdr_n:
        raise MissionMapMismatch(
            f"map_header.nmssn={hdr_nmssn} does not match record "
            f"nMssn={rec_nmssn} — refusing to serve the wrong map"
        )

    if not coverage:
        raise MissionMapUnavailable(
            "cloud returned no coverage layer for this mission — known "
            "open question for lewis/i-series firmware (plan D5); "
            "confirmed working on sapphire/j-series"
        )

    payload: dict[str, Any] = {
        "record_id": record_id,
        "mission_id": header.get("mission_id"),
        "nmssn": hdr_nmssn,
        "pmap_id": pmap_id,
        "pmapv_id": pmapv_id,
        "point_area_m": point_area,
        "coverage_mm": _points_to_mm(coverage),
        "coverage_poly": coverage_poly,
    }

    cache[record_id] = (now, payload)
    if len(cache) > _CACHE_MAX:  # evict the oldest entry
        oldest = min(cache, key=lambda k: cache[k][0])
        del cache[oldest]
    return payload


# ── Render half — stateless compositor for finished missions ─────────────────

_PNG_SIZE_PX = 800
_PNG_MARGIN_PX = 30
_MIN_CONTENT_MM = 1000.0  # guard against degenerate extents (zero division)


def render_mission_map_png(
    coverage_mm: list[list[float]],
    point_area_m: list[float],
    room_polygons_mm: list[list[tuple[float, float]]],
) -> bytes:
    """Compose room outlines + one mission's coverage points into a PNG.

    Pure function (no state, no I/O) — runs in the executor. Style
    constants come from map_renderer (one style, two consumers). The
    coverage point radius derives from the real point_area footprint so
    dense/sparse coverage reads truthfully instead of cosmetically.
    """
    from PIL import Image, ImageDraw

    from .map_renderer import BG_COLOUR, CLEANED_COLOUR, FLOOR_BORDER, PATH_COLOUR

    # Extent over everything we draw
    xs = [p[0] for p in coverage_mm] + [v[0] for poly in room_polygons_mm for v in poly]
    ys = [p[1] for p in coverage_mm] + [v[1] for poly in room_polygons_mm for v in poly]
    if not xs:
        img = Image.new("RGBA", (_PNG_SIZE_PX, _PNG_SIZE_PX), BG_COLOUR)
        buf = __import__("io").BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    content_w = max(max_x - min_x, _MIN_CONTENT_MM)
    content_h = max(max_y - min_y, _MIN_CONTENT_MM)
    avail = _PNG_SIZE_PX - 2 * _PNG_MARGIN_PX
    scale = avail / max(content_w, content_h)  # px per mm, aspect kept

    def to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
        # y flipped: UMF y grows up, image y grows down
        return (
            _PNG_MARGIN_PX + (x_mm - min_x) * scale,
            _PNG_SIZE_PX - _PNG_MARGIN_PX - (y_mm - min_y) * scale,
        )

    img = Image.new("RGBA", (_PNG_SIZE_PX, _PNG_SIZE_PX), BG_COLOUR)
    draw = ImageDraw.Draw(img)

    for poly in room_polygons_mm:
        if len(poly) >= 3:
            draw.polygon(
                [to_px(v[0], v[1]) for v in poly],
                outline=FLOOR_BORDER, width=2,
            )

    # Coverage dot radius from the real robot footprint (point_area is
    # [w_m, h_m] of one coverage point); floor of 2 px for visibility.
    try:
        side_mm = float(point_area_m[0]) * _M_TO_MM if point_area_m else 100.0
    except (TypeError, ValueError):  # bug-hunt round 3 — garbage point_area
        side_mm = 100.0
    radius = max(2.0, side_mm * scale / 2.0)
    for x_mm, y_mm in coverage_mm:
        px, py = to_px(x_mm, y_mm)
        draw.ellipse(
            [px - radius, py - radius, px + radius, py + radius],
            fill=CLEANED_COLOUR, outline=PATH_COLOUR,
        )

    buf = __import__("io").BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
