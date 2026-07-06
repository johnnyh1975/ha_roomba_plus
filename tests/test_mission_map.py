"""v3.3.0 MISSION-MAP — fetch layer (verify gate, cache, error classes)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.roomba_plus.mission_map import (
    MissionMapMismatch,
    MissionMapUnavailable,
    async_fetch_mission_map,
)


def _umf(nmssn=90, coverage=None, mission_id="01HB24", escape_events_layer=None):
    layers = [
        {"layer_type": "coverage", "geometry": {
            "type": "multipoint2d",
            "point_area": [0.1049, 0.1049],
            "coordinates": coverage if coverage is not None
            else [[1.0, 2.0], [3.0, 4.5]],
        }},
        {"layer_type": "coverage_poly", "geometry": {
            "type": "multipolygon2d", "coordinates": [[[0, 0], [1, 0]]],
        }},
    ]
    if escape_events_layer is not None:
        layers.append(escape_events_layer)
    return {"maps": [{
        "map_header": {"nmssn": nmssn, "mission_id": mission_id},
        "layers": layers,
    }]}


def _data(umf):
    data = MagicMock()
    data.blid = "BLID1"
    data.mission_map_cache = {}
    data.cloud_coordinator.api.get_pmap_umf = AsyncMock(return_value=umf)
    return data


def _record(nmssn=90):
    return {"id": "m_1", "nMssn": nmssn,
            "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}]}


class TestFetchMissionMap:
    @pytest.mark.asyncio
    async def test_happy_path_converts_to_mm_and_caches(self):
        data = _data(_umf())
        payload = await async_fetch_mission_map(data, _record())
        assert payload["coverage_mm"] == [[1000.0, 2000.0], [3000.0, 4500.0]]
        assert payload["nmssn"] == 90
        assert payload["point_area_m"] == [0.1049, 0.1049]
        # Second call: cache hit, no second cloud call
        await async_fetch_mission_map(data, _record())
        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 1

    @pytest.mark.asyncio
    async def test_verify_gate_raises_mismatch(self):
        """Plan D4 — boutXIII confirmation logic as a runtime guard."""
        data = _data(_umf(nmssn=91))
        with pytest.raises(MissionMapMismatch, match="refusing"):
            await async_fetch_mission_map(data, _record(nmssn=90))
        assert data.mission_map_cache == {}  # mismatches are never cached

    @pytest.mark.asyncio
    async def test_no_pmaps_info_unavailable(self):
        data = _data(_umf())
        with pytest.raises(MissionMapUnavailable, match="pmaps_info"):
            await async_fetch_mission_map(data, {"id": "m_old"})
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_coverage_layer_unavailable(self):
        """Plan D5 — the untested lewis/i-series case: clean 404 class,
        never an empty map."""
        data = _data(_umf(coverage=[]))
        with pytest.raises(MissionMapUnavailable, match="lewis"):
            await async_fetch_mission_map(data, _record())

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry_and_eviction(self, monkeypatch):
        data = _data(_umf())
        t = {"now": 1_000_000.0}
        monkeypatch.setattr(
            "custom_components.roomba_plus.mission_map.time.time",
            lambda: t["now"],
        )
        await async_fetch_mission_map(data, _record())
        # After TTL: refetch
        t["now"] += 24 * 3600 + 1
        await async_fetch_mission_map(data, _record())
        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 2
        # Eviction: 11th distinct mission evicts the oldest
        for i in range(2, 13):
            rec = {"id": f"m_{i}", "nMssn": 90,
                   "pmaps_info": [{"pmap_id": "P1", "pmapv_id": f"V{i}"}]}
            t["now"] += 1
            await async_fetch_mission_map(data, rec)
        assert len(data.mission_map_cache) == 10


class TestRenderMissionMapPng:
    """v3.3.0 MISSION-MAP — compositor smoke tests (pure function)."""

    def test_renders_valid_png_with_rooms_and_points(self):
        from custom_components.roomba_plus.mission_map import (
            render_mission_map_png,
        )
        png = render_mission_map_png(
            coverage_mm=[[1000.0, 2000.0], [3000.0, 4500.0], [3100.0, 4600.0]],
            point_area_m=[0.1049, 0.1049],
            room_polygons_mm=[
                [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)],
            ],
        )
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 500

    def test_empty_input_still_yields_png(self):
        from custom_components.roomba_plus.mission_map import (
            render_mission_map_png,
        )
        png = render_mission_map_png([], [], [])
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestBugHuntRound2:
    """v3.3.0 bug-hunt round 2 findings, locked in."""

    @pytest.mark.asyncio
    async def test_non_numeric_nmssn_does_not_crash_guard(self):
        """Degraded cloud data (non-numeric nmssn) must not turn the
        verification gate into a 500 — falls through to the coverage
        check (here: happy path serves)."""
        umf = _umf(nmssn="not-a-number")
        data = _data(umf)
        rec = _record(); rec["nMssn"] = "also-garbage"
        payload = await async_fetch_mission_map(data, rec)
        assert payload["coverage_mm"]  # served, no exception


class TestBugHuntRound3:
    @pytest.mark.asyncio
    async def test_garbage_coordinate_skipped_not_500(self):
        umf = _umf(coverage=[[1.0, 2.0], ["x", "y"], [3.0, None], [4.0, 5.0]])
        data = _data(umf)
        payload = await async_fetch_mission_map(data, _record())
        assert payload["coverage_mm"] == [[1000.0, 2000.0], [4000.0, 5000.0]]

    def test_garbage_point_area_falls_back(self):
        from custom_components.roomba_plus.mission_map import (
            render_mission_map_png,
        )
        png = render_mission_map_png([[100.0, 100.0]], ["garbage"], [])
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


class TestEscapeEvents:
    """v3.4.0 GS-SMART-COVERAGE — escape_events layer extraction.

    Shape verified against a real UMF payload (PyRoomba's committed
    mission_map.txt sample, fetched 2026-07): {"layer_type":
    "escape_events", "geometry": {"type": "pose2dconcise_event",
    "list": [...]}}. Structural shape is confirmed; whether
    lewis-firmware robots actually populate it for a real stuck
    incident remains the separate, still-open Feldverifikations-Gate
    item (plan §4.3) — these tests only cover parsing.
    """

    _EVENTS = [
        {"pose": [4.4767, 0.1159, -1.5911], "event": "brush_stall_detected"},
        {"pose": [3.5503, -1.3822, 0.236], "event": "wheel_dropped"},
        {"pose": [0.6332, 1.5764, -0.5963], "event": "stasis_detected"},
        {"pose": [0.2325, -1.8262, -0.3264], "event": "start_stuck"},
        {"pose": [-0.0571, 1.107, 1.6877], "event": "start_evade"},
    ]

    @staticmethod
    def _layer(events):
        return {
            "layer_type": "escape_events",
            "geometry": {"type": "pose2dconcise_event", "list": events},
        }

    @pytest.mark.asyncio
    async def test_absent_layer_yields_empty_list_no_warning(self, caplog):
        """The common case: no escape_events layer in this UMF response
        at all (most missions have no stuck events)."""
        data = _data(_umf())
        with caplog.at_level("WARNING"):
            payload = await async_fetch_mission_map(data, _record())
        assert payload["escape_events"] == []
        assert "escape_events" not in caplog.text

    @pytest.mark.asyncio
    async def test_empty_layer_yields_empty_list_no_warning(self, caplog):
        """Layer present but with an empty list — a genuinely
        stuck-free mission — must not warn."""
        umf = _umf(escape_events_layer=self._layer([]))
        data = _data(umf)
        with caplog.at_level("WARNING"):
            payload = await async_fetch_mission_map(data, _record())
        assert payload["escape_events"] == []
        assert "verify this against" not in caplog.text

    @pytest.mark.asyncio
    async def test_populated_layer_extracted_verbatim(self):
        """All 5 known event types, as seen in the real reference
        payload, round-trip through unchanged (UMF-space, unconverted
        — conversion is the caller's job)."""
        umf = _umf(escape_events_layer=self._layer(self._EVENTS))
        data = _data(umf)
        payload = await async_fetch_mission_map(data, _record())
        assert payload["escape_events"] == self._EVENTS

    @pytest.mark.asyncio
    async def test_unexpected_shape_warns_and_returns_empty_not_crash(self, caplog):
        """A future/unknown UMF shape change must not crash the whole
        fetch (same defensive posture as every other layer parse
        here), but must log loudly enough to be diagnosable."""
        umf = _umf(escape_events_layer={
            "layer_type": "escape_events", "some_other_key": self._EVENTS,
        })
        data = _data(umf)
        with caplog.at_level("WARNING"):
            payload = await async_fetch_mission_map(data, _record())
        assert payload["escape_events"] == []
        assert "verify this against" in caplog.text

    @pytest.mark.asyncio
    async def test_present_alongside_coverage_in_same_response(self):
        """escape_events extraction must not disturb the existing
        coverage/coverage_poly extraction from the same response."""
        umf = _umf(escape_events_layer=self._layer(self._EVENTS))
        data = _data(umf)
        payload = await async_fetch_mission_map(data, _record())
        assert payload["coverage_mm"] == [[1000.0, 2000.0], [3000.0, 4500.0]]
        assert payload["escape_events"] == self._EVENTS
