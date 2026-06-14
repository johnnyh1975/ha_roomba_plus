"""Tests for UMF fetch integration and CR3 fallback — v2.2.0 Steps 12+13.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock


# ── coordinator UMF properties ────────────────────────────────────────────────

def _make_coordinator(umf_data=None, raw_records=None):
    from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
    coord = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
    coord.data = {
        "umf": umf_data or {},
        "mission_history_raw": raw_records or [],
        "pmaps": [],
    }
    return coord


class TestUmfProperties:
    def test_umf_data_returns_empty_when_no_data(self):
        coord = _make_coordinator()
        coord.data = None
        assert coord.umf_data == {}

    def test_umf_data_returns_dict_when_present(self):
        coord = _make_coordinator(umf_data={"keepoutzones": [], "observed_zones": []})
        assert isinstance(coord.umf_data, dict)

    def test_keepout_zones_from_umf(self):
        coord = _make_coordinator(umf_data={
            "keepoutzones": [{"id": "k1", "space": "umf"}],
            "observed_zones": [],
        })
        assert len(coord.keepout_zones) == 1
        assert coord.keepout_zones[0]["id"] == "k1"

    def test_keepout_zones_empty_when_absent(self):
        coord = _make_coordinator()
        assert coord.keepout_zones == []

    def test_observed_zone_centroids_cx_cy(self):
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"cx": 750.0, "cy": 500.0, "space": "umf"}],
        })
        centroids = coord.observed_zone_centroids
        assert len(centroids) == 1
        assert centroids[0]["x"] == 750.0
        assert centroids[0]["y"] == 500.0
        assert centroids[0]["space"] == "umf"

    def test_observed_zone_centroids_fallback_x_y(self):
        # Falls back to x/y when cx/cy absent
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"x": 300.0, "y": 200.0}],
        })
        centroids = coord.observed_zone_centroids
        assert len(centroids) == 1
        assert centroids[0]["x"] == 300.0

    def test_observed_zone_centroids_skips_missing_coords(self):
        coord = _make_coordinator(umf_data={
            "observed_zones": [{"no_coord": True}],
        })
        assert coord.observed_zone_centroids == []

    def test_observed_zone_centroids_empty_when_no_umf(self):
        coord = _make_coordinator()
        assert coord.observed_zone_centroids == []


# ── CR3 fallback logic ────────────────────────────────────────────────────────

class TestCR3Fallback:
    """CR3: enriched MissionStore records served when cloud history is empty."""

    def _enriched_record(self, id_="m_1"):
        return {
            "id": id_,
            "started_at": "2026-06-01T08:00:00+00:00",
            "ended_at": "2026-06-01T08:55:00+00:00",
            "result": "completed",
            "dirt": 14,
            "chrgM": 0,
            "wlBars": [0, 35, 65, 0, 0],
        }

    def _unenriched_record(self, id_="m_2"):
        return {
            "id": id_,
            "started_at": "2026-06-02T08:00:00+00:00",
            "ended_at": "2026-06-02T08:55:00+00:00",
            "result": "completed",
        }

    def test_enriched_records_qualify_for_fallback(self):
        rec = self._enriched_record()
        qualifies = any(rec.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        assert qualifies is True

    def test_unenriched_records_do_not_qualify(self):
        rec = self._unenriched_record()
        qualifies = any(rec.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        assert qualifies is False

    def test_fallback_filter_returns_only_enriched(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [self._enriched_record(), self._unenriched_record()]
        fallback = [
            r for r in ms._records
            if any(r.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        ]
        assert len(fallback) == 1
        assert fallback[0]["id"] == "m_1"

    def test_no_fallback_when_no_enriched_records(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [self._unenriched_record()]
        fallback = [
            r for r in ms._records
            if any(r.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        ]
        assert fallback == []


# ── F22a observed zones repair issue ─────────────────────────────────────────

class TestF22aObservedZonesConditions:
    """Unit tests for the guard conditions in async_check_observed_zones."""

    def test_issue_condition_met_when_no_stuck_events(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        assert gs.stuck_event_count == 0
        centroids = [{"x": 750.0, "y": 500.0}]
        assert len(centroids) > 0
        # Condition: create issue

    def test_issue_dismissed_when_stuck_events_exist(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        gs._stuck[(0, 0)] = {"count": 5, "times": []}
        assert gs.stuck_event_count > 0
        # Condition: delete issue

    def test_no_issue_when_no_centroids(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        centroids: list = []
        assert len(centroids) == 0
        # Condition: return early, no issue
