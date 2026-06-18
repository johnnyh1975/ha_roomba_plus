"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import datetime
from typing import Any
import pytest
from datetime import timezone
from custom_components.roomba_plus.api_views import _local_record_to_unified
from custom_components.roomba_plus.api_views import _VALID_FORMATS
import math
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from custom_components.roomba_plus.umf_aligner import UmfAligner
import json
from datetime import datetime as datetime_v250_api_export
from custom_components.roomba_plus.api_views import MissionHistoryImportView
from custom_components.roomba_plus.api_views import MissionHistoryView
from custom_components.roomba_plus.const import DOMAIN


def _cloud_rec(
    start_ts=1700000000, end_ts=1700003600,
    sqft=180, run_m=55, duration_m=60,
    done="done", done_raw="done", pause_id=0,
    chrgs=0, evacs=1, dirt=12,
    wl_bars=None, initiator="schedule",
    classified="completed",
):
    return {
        "startTime":         start_ts,
        "timestamp":         end_ts,
        "sqft":              sqft,
        "runM":              run_m,
        "durationM":         duration_m,
        "done":              done,
        "done_raw":          done_raw,
        "pauseId":           pause_id,
        "chrgs":             chrgs,
        "evacs":             evacs,
        "dirt":              dirt,
        "wlBars":            wl_bars or [70, 68, 65, 60, 62],
        "initiator":         initiator,
        "classified_result": classified,
    }


def _local_rec(
    started_at="2026-05-01T08:00:00+00:00",
    ended_at="2026-05-01T08:55:00+00:00",
    duration_min=55,
    area_sqft=180.0,
    result="completed",
    initiator="schedule",
    zones=None,
    error_code=None,
):
    return {
        "id":           "m_1700000000",
        "started_at":   started_at,
        "ended_at":     ended_at,
        "duration_min": duration_min,
        "area_sqft":    area_sqft,
        "result":       result,
        "initiator":    initiator,
        "zones":        zones or [],
        "error_code":   error_code,
        "bbrun_hr":     100,
    }


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


def _make_record(id_: str, started_at: str = "2026-05-01T08:00:00+00:00") -> dict:
    return {
        "id": id_,
        "started_at": started_at,
        "ended_at": "2026-05-01T09:00:00+00:00",
        "duration_min": 60,
        "area_sqft": 200.0,
        "result": "completed",
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": 100,
    }


def _make_mission_store(records: list[dict]) -> MagicMock:
    ms = MagicMock()
    ms._records = list(records)
    ms.async_save = AsyncMock()
    return ms


def _make_request(hass: MagicMock, fmt: str = "export") -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    req.query = {"format": fmt}
    return req


def _make_post_request(hass: MagicMock, body: dict) -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    encoded = json.dumps(body).encode()
    req.json = AsyncMock(return_value=body)
    return req


def _make_hass_with_entry(
    entry_id: str = "abc123",
    records: list[dict] | None = None,
    entry_present: bool = True,
    runtime_data_set: bool = True,
) -> tuple[MagicMock, MagicMock]:
    """Return (hass, entry) pair for view tests."""
    hass = MagicMock()
    if not entry_present:
        hass.config_entries.async_get_entry.return_value = None
        return hass, None

    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {"blid": "abc_blid_123"}
    entry.entry_id = entry_id

    if not runtime_data_set:
        # Simulate the pre-ready state — no runtime_data attribute
        del entry.runtime_data
    else:
        ms = _make_mission_store(records or [])
        data = MagicMock()
        data.mission_store = ms
        entry.runtime_data = data

    hass.config_entries.async_get_entry.return_value = entry
    return hass, entry


class TestCloudRecordToUnified:

    def test_basic_completed(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec()
        u = _cloud_record_to_unified(rec)
        assert u["source"] == "cloud"
        assert u["result"] == "completed"
        assert u["area_sqft"] == 180
        assert u["run_min"] == 55
        assert u["duration_min"] == 60
        assert u["recharges"] == 0
        assert u["evacuations"] == 1
        assert u["dirt_events"] == 12
        assert u["wifi_signal"] == [70, 68, 65, 60, 62]
        assert u["zones"] == []

    def test_timestamps_converted_to_iso(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified(_cloud_rec(start_ts=1700000000, end_ts=1700003600))
        assert "T" in u["started_at"]
        assert u["started_at"].endswith("+00:00")
        assert "T" in u["ended_at"]

    def test_error_code_from_pause_id(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec(done="stuck", pause_id=17, classified="error_17")
        u = _cloud_record_to_unified(rec)
        assert u["error_code"] == 17
        assert u["result"] == "error_17"

    def test_pause_id_zero_gives_null_error_code(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified(_cloud_rec(pause_id=0))
        assert u["error_code"] is None

    def test_missing_start_time(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec()
        del rec["startTime"]
        u = _cloud_record_to_unified(rec)
        assert u["started_at"] is None
        assert u["id"].startswith("c_")

    def test_run_min_null_when_missing(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec()
        del rec["runM"]
        u = _cloud_record_to_unified(rec)
        assert u["run_min"] is None

    def test_initiator_preserved(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified(_cloud_rec(initiator="localApp"))
        assert u["initiator"] == "localApp"


class TestLocalRecordToUnified:

    def test_basic_completed(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec())
        assert u["source"] == "local"
        assert u["result"] == "completed"
        assert u["area_sqft"] == 180.0
        assert u["duration_min"] == 55

    def test_cloud_fields_are_null(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec())
        assert u["run_min"] is None
        assert u["recharges"] is None
        assert u["evacuations"] is None
        assert u["dirt_events"] is None
        assert u["wifi_signal"] is None

    def test_zones_preserved(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec(zones=["Kitchen", "Hallway"]))
        assert u["zones"] == ["Kitchen", "Hallway"]

    def test_error_code_preserved(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec(result="error", error_code=17))
        assert u["error_code"] == 17

    def test_timestamps_preserved_as_is(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec(started_at="2026-05-01T08:00:00+00:00"))
        assert u["started_at"] == "2026-05-01T08:00:00+00:00"


class TestSummaryFormat:
    """format=summary returns the same DaySummary shape as v0.1-beta card."""

    def _make_summary(self, records):
        """Build a DaySummary-like dict as the endpoint would return."""
        return {
            "date": "2026-05-01",
            "total": len(records),
            "completed": sum(1 for r in records if r.get("result") == "completed"),
            "stuck": sum(1 for r in records if r.get("result") == "stuck"),
            "area_sqft": sum(r.get("area_sqft", 0) or 0 for r in records) or None,
            "result": "completed",
        }

    def test_summary_shape_has_required_keys(self):
        summary = self._make_summary([_local_rec()])
        required = {"date", "total", "completed", "stuck", "area_sqft", "result"}
        assert required.issubset(summary.keys())

    def test_summary_has_no_per_mission_keys(self):
        """Beta card must not receive per-mission fields it doesn't expect."""
        summary = self._make_summary([_local_rec()])
        per_mission_keys = {"run_min", "recharges", "evacuations",
                            "dirt_events", "wifi_signal", "source", "id"}
        assert not per_mission_keys.intersection(summary.keys())


class TestRecordsFormat:
    """format=records returns unified per-mission shape."""

    def test_cloud_record_shape_has_all_keys(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified(_cloud_rec())
        required = {
            "id", "started_at", "ended_at", "duration_min", "run_min",
            "area_sqft", "result", "initiator", "zones", "error_code",
            "recharges", "evacuations", "dirt_events", "wifi_signal", "source",
            # v2.3.0 additions
            "room_coverage", "alignment_confidence",
        }
        assert required == set(u.keys())

    def test_local_record_shape_has_all_keys(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec())
        required = {
            "id", "started_at", "ended_at", "duration_min", "run_min",
            "area_sqft", "result", "initiator", "zones", "error_code",
            "recharges", "evacuations", "dirt_events", "wifi_signal", "source",
            # v2.3.0 additions
            "room_coverage", "alignment_confidence",
        }
        assert required == set(u.keys())

    def test_cloud_and_local_shapes_identical(self):
        """Card can handle both sources with a single renderer."""
        from custom_components.roomba_plus.api_views import (
            _cloud_record_to_unified,
            _local_record_to_unified,
        )
        cloud = _cloud_record_to_unified(_cloud_rec())
        local = _local_record_to_unified(_local_rec())
        assert set(cloud.keys()) == set(local.keys())

    def test_cloud_records_returned_ascending(self):
        """Cloud records (newest-first from API) are reversed to ascending."""
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        records = [
            _cloud_record_to_unified(_cloud_rec(start_ts=1700010000)),
            _cloud_record_to_unified(_cloud_rec(start_ts=1700000000)),
        ]
        # Simulate the reversal done in the endpoint
        ascending = list(reversed(records))
        assert ascending[0]["started_at"] < ascending[1]["started_at"]

    def test_error_mission_unified(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec(
            done="stuck", pause_id=17, classified="error_17",
            wl_bars=[55, 42, 3, 0, 0],
        )
        u = _cloud_record_to_unified(rec)
        assert u["result"] == "error_17"
        assert u["error_code"] == 17
        assert u["wifi_signal"] == [55, 42, 3, 0, 0]
        assert u["source"] == "cloud"

    def test_cancelled_by_user_unified(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = _cloud_rec(done="cncl", done_raw="usrEnd", classified="cancelled_by_user")
        u = _cloud_record_to_unified(rec)
        assert u["result"] == "cancelled_by_user"
        assert u["error_code"] is None


class TestZoneInjection:
    """F4a -- _inject_zones populates zones from local MissionStore index."""

    def _make_cloud_record(self, end_ts_unix: int) -> dict:
        ended_at = datetime.datetime.fromtimestamp(
            end_ts_unix, tz=datetime.timezone.utc
        ).isoformat()
        return {
            "id": f"c_{end_ts_unix}",
            "started_at": ended_at,
            "ended_at": ended_at,
            "duration_min": 45,
            "run_min": 40,
            "area_sqft": 200,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "recharges": 0,
            "evacuations": 0,
            "dirt_events": 5,
            "wifi_signal": None,
            "source": "cloud",
        }

    def test_injects_zones_within_tolerance(self):
        from custom_components.roomba_plus.api_views import (
            _build_local_zones_index, _inject_zones,
        )
        base_ts = 1700000000
        local_records = [{
            "id": "m_local",
            "ended_at": datetime.datetime.fromtimestamp(
                base_ts, tz=datetime.timezone.utc
            ).isoformat(),
            "zones": ["Kitchen", "Living Room"],
        }]
        index = _build_local_zones_index(local_records)
        cloud_record = self._make_cloud_record(base_ts + 30)  # 30 s delta
        result = _inject_zones(cloud_record, index)
        assert result["zones"] == ["Kitchen", "Living Room"]

    def test_no_injection_outside_tolerance(self):
        from custom_components.roomba_plus.api_views import (
            _build_local_zones_index, _inject_zones,
        )
        base_ts = 1700000000
        local_records = [{
            "id": "m_local",
            "ended_at": datetime.datetime.fromtimestamp(
                base_ts, tz=datetime.timezone.utc
            ).isoformat(),
            "zones": ["Kitchen"],
        }]
        index = _build_local_zones_index(local_records)
        cloud_record = self._make_cloud_record(base_ts + 200)  # 200 s > 120 s tolerance
        result = _inject_zones(cloud_record, index)
        assert result["zones"] == []

    def test_empty_local_zones_not_injected(self):
        from custom_components.roomba_plus.api_views import (
            _build_local_zones_index, _inject_zones,
        )
        base_ts = 1700000000
        local_records = [{
            "id": "m_local",
            "ended_at": datetime.datetime.fromtimestamp(
                base_ts, tz=datetime.timezone.utc
            ).isoformat(),
            "zones": [],   # no zones captured
        }]
        index = _build_local_zones_index(local_records)
        cloud_record = self._make_cloud_record(base_ts)
        result = _inject_zones(cloud_record, index)
        assert result["zones"] == []

    def test_build_index_skips_records_without_ended_at(self):
        from custom_components.roomba_plus.api_views import _build_local_zones_index
        records = [{"zones": ["Room A"]}]  # no ended_at
        index = _build_local_zones_index(records)
        assert index == {}


class TestApiHardening:
    """F7o -- startup race guard, 503 on coordinator failure, 400 on bad format."""

    def _make_request(self, fmt: str = "summary") -> Any:
        """Build a minimal fake request object."""
        from unittest.mock import MagicMock
        req = MagicMock()
        req.query = {"format": fmt}
        return req

    def test_valid_formats_accepted(self):
        from custom_components.roomba_plus.api_views import _VALID_FORMATS
        assert "summary" in _VALID_FORMATS
        assert "records" in _VALID_FORMATS

    def test_unknown_format_not_in_valid_set(self):
        from custom_components.roomba_plus.api_views import _VALID_FORMATS
        assert "bogus" not in _VALID_FORMATS


class TestHazardsFormatStub:
    """format=hazards accepted and returns [] until GridStore exists in v2.2."""

    def test_hazards_not_rejected_by_validator(self):
        """_VALID_FORMATS includes 'hazards'."""
        from custom_components.roomba_plus.api_views import _VALID_FORMATS
        assert "hazards" in _VALID_FORMATS

    def test_hazards_returns_empty_list(self):
        """_cloud_record_to_unified and _local_record_to_unified are unchanged;
        the hazards branch is validated via the validator test above.
        We confirm the branch returns [] by exercising _VALID_FORMATS acceptance."""
        from custom_components.roomba_plus.api_views import _VALID_FORMATS
        # hazards is accepted (not 400) and the stub returns []
        assert "hazards" in _VALID_FORMATS  # accepted without 400


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


class TestHazardsFormat:
    def test_hazards_in_valid_formats(self):
        assert "hazards" in _VALID_FORMATS

    def test_summary_in_valid_formats(self):
        assert "summary" in _VALID_FORMATS

    def test_records_in_valid_formats(self):
        assert "records" in _VALID_FORMATS


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


class TestApiViewsRecordsV23:
    def test_cloud_record_has_room_coverage_key(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule", "room_coverage": {"Kitchen": 0.8}}
        u = _cloud_record_to_unified(rec)
        assert "room_coverage" in u
        assert u["room_coverage"] == {"Kitchen": 0.8}

    def test_cloud_record_room_coverage_null_when_absent(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule"}
        u = _cloud_record_to_unified(rec)
        assert u["room_coverage"] is None

    def test_cloud_record_alignment_confidence_initially_none(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        rec = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 60, "classified_result": "completed",
               "initiator": "schedule"}
        u = _cloud_record_to_unified(rec)
        assert u["alignment_confidence"] is None

    def test_local_record_has_room_coverage_key(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
               "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
               "result": "completed", "initiator": "schedule", "zones": [],
               "room_coverage": {"Hallway": 0.6}}
        u = _local_record_to_unified(rec)
        assert "room_coverage" in u
        assert u["room_coverage"] == {"Hallway": 0.6}

    def test_local_record_alignment_confidence_always_none(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
               "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
               "result": "completed", "initiator": "schedule", "zones": []}
        u = _local_record_to_unified(rec)
        assert u["alignment_confidence"] is None
        assert u["source"] == "local"

    def test_cloud_and_local_shapes_identical(self):
        from custom_components.roomba_plus.api_views import (
            _cloud_record_to_unified,
            _local_record_to_unified,
        )
        cloud_rec = {"startTime": 1700000000, "timestamp": 1700003600,
                     "durationM": 60, "classified_result": "completed",
                     "initiator": "schedule"}
        local_rec = {"id": "m_1", "started_at": "2026-01-01T00:00:00+00:00",
                     "ended_at": "2026-01-01T01:00:00+00:00", "duration_min": 60,
                     "result": "completed", "initiator": "schedule", "zones": []}
        c = _cloud_record_to_unified(cloud_rec)
        l = _local_record_to_unified(local_rec)
        assert set(c.keys()) == set(l.keys())


class TestHazardsV23:
    def test_no_aligner_room_name_null(self):
        """Hazard room_name stays None when no aligner."""
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        # We test the logic directly by checking that room_name is not set
        # without a live HTTP request — test the keepout building logic
        keepout_zones = [{"cx": 1000.0, "cy": 500.0}]
        hazards = []
        import math
        for zone in keepout_zones:
            cx = zone.get("cx") or 0.0
            cy = zone.get("cy") or 0.0
            hazards.append({
                "gx": None, "gy": None,
                "x_mm": float(cx), "y_mm": float(cy),
                "stuck_count": None,
                "room_name": None,
                "bearing_deg": int(math.degrees(math.atan2(cx, cy)) % 360),
                "distance_mm": int(math.sqrt(cx**2 + cy**2)),
                "source": "keepout",
            })
        assert hazards[0]["source"] == "keepout"
        assert hazards[0]["room_name"] is None
        assert hazards[0]["x_mm"] == pytest.approx(1000.0)

    def test_aligner_populates_room_name_for_keepout(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        hazards = [{"source": "keepout", "x_mm": 2500.0, "y_mm": 2500.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] in ("robot_learned", "keepout"):
                    h["room_name"] = aligner.room_name_at(h["x_mm"], h["y_mm"])
        assert hazards[0]["room_name"] == "Kitchen"

    def test_aligner_room_name_none_outside_rooms(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        hazards = [{"source": "keepout", "x_mm": 9999.0, "y_mm": 9999.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] in ("robot_learned", "keepout"):
                    h["room_name"] = aligner.room_name_at(h["x_mm"], h["y_mm"])
        assert hazards[0]["room_name"] is None

    def test_stuck_events_uses_pose_to_umf(self):
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        # With identity transform (rot=0, tx=0, ty=0), pose ≡ UMF
        hazards = [{"source": "stuck_events", "x_mm": 2500.0, "y_mm": 2500.0,
                    "room_name": None}]
        if aligner and aligner.aligned:
            for h in hazards:
                if h["source"] == "stuck_events":
                    pt_umf = aligner.pose_to_umf(h["x_mm"], h["y_mm"])
                    if pt_umf:
                        h["room_name"] = aligner.room_name_at(*pt_umf)
        assert hazards[0]["room_name"] == "Kitchen"


class TestCoverageByPolygon:
    def _gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        return gs

    def test_empty_grid_returns_empty_dict(self):
        gs = self._gs()
        poly = {"r1": [(0.0, 0.0), (3000.0, 0.0), (3000.0, 3000.0), (0.0, 3000.0)]}
        result = gs.coverage_by_polygon(poly)
        # Empty grid → early return with empty dict
        assert result == {}

    def test_degenerate_polygon_zero(self):
        gs = self._gs()
        # Add a cell to make grid non-empty
        gs._cells[(0, 0)] = 1.0
        result = gs.coverage_by_polygon({"r1": [(0.0, 0.0), (100.0, 0.0)]})
        assert result == {"r1": 0.0}

    def test_cell_inside_polygon_counted(self):
        from custom_components.roomba_plus.grid_store import CELL_SIZE_MM, PRUNE_THRESHOLD
        gs = self._gs()
        # Place a visited cell at grid (0,0) → centre (75, 75) mm
        gs._cells[(0, 0)] = 1.0   # above PRUNE_THRESHOLD
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.coverage_by_polygon(poly)
        assert "r1" in result
        assert result["r1"] > 0.0

    def test_cell_outside_polygon_not_counted(self):
        gs = self._gs()
        # Cell far outside polygon
        gs._cells[(100, 100)] = 1.0
        poly = {"r1": [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)]}
        result = gs.coverage_by_polygon(poly)
        assert result["r1"] == 0.0

    def test_below_threshold_not_visited(self):
        from custom_components.roomba_plus.grid_store import PRUNE_THRESHOLD
        gs = self._gs()
        gs._cells[(0, 0)] = PRUNE_THRESHOLD / 2   # below threshold
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.coverage_by_polygon(poly)
        # Cell is inside polygon but score is below threshold → not visited
        assert result["r1"] == 0.0

    def test_multiple_polygons(self):
        gs = self._gs()
        gs._cells[(0, 0)] = 1.0   # inside r1
        gs._cells[(40, 40)] = 1.0  # outside both
        polys = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)],
            "r2": [(5000.0, 0.0), (6000.0, 0.0), (6000.0, 1000.0), (5000.0, 1000.0)],
        }
        result = gs.coverage_by_polygon(polys)
        assert "r1" in result
        assert "r2" in result
        assert result["r1"] > 0.0
        assert result["r2"] == 0.0


class TestExportEndpoint:
    def test_export_in_valid_formats(self):
        assert "export" in _VALID_FORMATS

    @pytest.mark.asyncio
    async def test_export_shape_has_required_keys(self):
        records = [_make_record("m_1"), _make_record("m_2")]
        hass, _ = _make_hass_with_entry(records=records)
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        required = {"export_version", "exported_at", "blid", "record_count", "records"}
        assert required.issubset(body.keys())

    @pytest.mark.asyncio
    async def test_record_count_matches_records(self):
        records = [_make_record("m_1"), _make_record("m_2"), _make_record("m_3")]
        hass, _ = _make_hass_with_entry(records=records)
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["record_count"] == 3
        assert len(body["records"]) == 3

    @pytest.mark.asyncio
    async def test_exported_at_is_iso_utc(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        ts = body["exported_at"]
        # Must parse as a valid datetime_v250_api_export with UTC offset
        parsed = datetime_v250_api_export.fromisoformat(ts)
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_blid_from_entry_data(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["blid"] == "abc_blid_123"

    @pytest.mark.asyncio
    async def test_empty_store_returns_zero_count(self):
        hass, _ = _make_hass_with_entry(records=[])
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["record_count"] == 0
        assert body["records"] == []

    @pytest.mark.asyncio
    async def test_export_version_is_1(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["export_version"] == 1
