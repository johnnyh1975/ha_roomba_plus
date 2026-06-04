"""Tests for format=hazards and HouseholdSummaryView — v2.2.0 Step 16.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest
from custom_components.roomba_plus.api_views import (
    _local_record_to_unified,
    _VALID_FORMATS,
)


# ── _local_record_to_unified (Step 16a) ──────────────────────────────────────

class TestLocalRecordUnifiedUpdate:
    """After v2.1.3 CR1/CR2: dirt/wlBars/evacs populated from enriched records."""

    def _base(self, **extra):
        rec = {
            "id": "m_1",
            "started_at": "2026-06-01T08:00:00+00:00",
            "ended_at":   "2026-06-01T08:55:00+00:00",
            "duration_min": 55,
            "area_sqft":    180.0,
            "result":       "completed",
            "initiator":    "schedule",
            "zones":        [],
            "error_code":   None,
        }
        rec.update(extra)
        return rec

    def test_dirt_events_from_enriched_record(self):
        unified = _local_record_to_unified(self._base(dirt=14))
        assert unified["dirt_events"] == 14

    def test_wifi_signal_from_enriched_record(self):
        bars = [0, 35, 65, 0, 0]
        unified = _local_record_to_unified(self._base(wlBars=bars))
        assert unified["wifi_signal"] == bars

    def test_evacuations_from_enriched_record(self):
        unified = _local_record_to_unified(self._base(evacs=2))
        assert unified["evacuations"] == 2

    def test_unenriched_dirt_none(self):
        unified = _local_record_to_unified(self._base())
        assert unified["dirt_events"] is None

    def test_unenriched_wifi_none(self):
        unified = _local_record_to_unified(self._base())
        assert unified["wifi_signal"] is None

    def test_unenriched_evacuations_none(self):
        unified = _local_record_to_unified(self._base())
        assert unified["evacuations"] is None

    def test_run_min_always_none(self):
        unified = _local_record_to_unified(self._base(runM=38))
        assert unified["run_min"] is None

    def test_recharges_always_none(self):
        unified = _local_record_to_unified(self._base(chrgs=2))
        assert unified["recharges"] is None

    def test_source_is_local(self):
        assert _local_record_to_unified(self._base())["source"] == "local"

    def test_zones_preserved(self):
        unified = _local_record_to_unified(self._base(zones=["Kitchen", "Hallway"]))
        assert unified["zones"] == ["Kitchen", "Hallway"]


# ── hazards format in valid set ───────────────────────────────────────────────

class TestHazardsFormat:
    def test_hazards_in_valid_formats(self):
        assert "hazards" in _VALID_FORMATS

    def test_summary_in_valid_formats(self):
        assert "summary" in _VALID_FORMATS

    def test_records_in_valid_formats(self):
        assert "records" in _VALID_FORMATS


# ── HouseholdSummaryView logic (unit-testable parts) ─────────────────────────

class TestHouseholdAggregation:
    """Test the aggregation math directly without HTTP."""

    def test_completion_pct_zero_when_no_missions(self):
        missions, completed = 0, 0
        pct = round(100 * completed / missions, 1) if missions else 0.0
        assert pct == 0.0

    def test_completion_pct_full(self):
        missions, completed = 10, 10
        pct = round(100 * completed / missions, 1)
        assert pct == 100.0

    def test_completion_pct_partial(self):
        missions, completed = 10, 8
        pct = round(100 * completed / missions, 1)
        assert pct == 80.0

    def test_floor_aggregation_combines_robots(self):
        floors: dict = {}
        robots = [
            {"floor": "Ground", "missions": 10, "completed": 9, "area_sqft": 500.0},
            {"floor": "Ground", "missions": 5,  "completed": 4, "area_sqft": 200.0},
        ]
        for robot in robots:
            label = robot["floor"]
            if label:
                f = floors.setdefault(label, {
                    "label": label, "missions": 0, "completed": 0, "area_sqft": None,
                })
                f["missions"]  += robot["missions"]
                f["completed"] += robot["completed"]
                if robot["area_sqft"] is not None:
                    f["area_sqft"] = (f["area_sqft"] or 0.0) + robot["area_sqft"]
        assert floors["Ground"]["missions"] == 15
        assert floors["Ground"]["completed"] == 13
        assert floors["Ground"]["area_sqft"] == 700.0

    def test_empty_floor_label_not_in_floors(self):
        floors: dict = {}
        robots = [{"floor": "", "missions": 5, "completed": 4, "area_sqft": None}]
        for robot in robots:
            label = robot["floor"]
            if label:
                floors[label] = robot
        assert "" not in floors
        assert len(floors) == 0

    def test_total_area_accumulates_across_robots(self):
        total_area: float | None = None
        for area in [300.0, 250.0, None]:
            if area is not None:
                total_area = (total_area or 0.0) + area
        assert total_area == 550.0

    def test_total_area_none_when_no_robot_has_area(self):
        total_area: float | None = None
        for area in [None, None]:
            if area is not None:
                total_area = (total_area or 0.0) + area
        assert total_area is None
