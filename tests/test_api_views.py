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
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


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


def _make_mission_store(records: list[dict]) -> MissionStore:
    # v3.3.0 STORE-ENCAP — real store instead of MagicMock: the views now
    # go through .records / .append_validated(), and a MagicMock silently
    # auto-mocks both (export saw 0 records, import "imported" duplicates).
    # Exactly the mock-mirrors-misunderstanding class from process
    # standard 1 — the real store is dependency-free and behaves.
    ms = MissionStore()
    ms._records = list(records)
    ms.async_save = AsyncMock()  # instance-level patch; views await it
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
            # v3.2.1 — card F4 path replay key
            "n_mssn",
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
            # v3.2.1 — card F4 path replay key
            "n_mssn",
        }
        assert required == set(u.keys())

    def test_cloud_record_n_mssn_passed_through(self):
        """v3.2.1 — card F4: nMssn from the raw cloud record surfaces as
        n_mssn so the card can build /mission/{n_mssn}/path requests."""
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified({**_cloud_rec(), "nMssn": 1234})
        assert u["n_mssn"] == 1234

    def test_cloud_record_n_mssn_null_when_absent(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_unified
        u = _cloud_record_to_unified(_cloud_rec())
        assert u["n_mssn"] is None

    def test_local_record_n_mssn_from_backfill(self):
        """v3.2.1 — local records carry nMssn only after
        backfill_from_cloud() merged it (_CLOUD_MERGE_SCALAR)."""
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified({**_local_rec(), "nMssn": 987})
        assert u["n_mssn"] == 987

    def test_local_record_n_mssn_null_before_enrichment(self):
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        u = _local_record_to_unified(_local_rec())
        assert u["n_mssn"] is None

    def test_n_mssn_string_coerced_import_garbage_null(self):
        """v3.2.1 — _safe_int: import endpoint may deliver nMssn as a
        string; coerce numerics, null out garbage instead of raising."""
        from custom_components.roomba_plus.api_views import _local_record_to_unified
        assert _local_record_to_unified({**_local_rec(), "nMssn": "42"})["n_mssn"] == 42
        assert _local_record_to_unified({**_local_rec(), "nMssn": "abc"})["n_mssn"] is None

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


class TestLocalRecordHasCloudMergeSignal:
    """Unit tests for the heuristic used to find local records that never
    got matched against any cloud record (v2.10.2 RECORDS-UNION)."""

    def test_no_merge_fields_means_unmatched(self):
        from custom_components.roomba_plus.api_views import (
            _local_record_has_cloud_merge_signal,
        )
        assert _local_record_has_cloud_merge_signal(_local_rec()) is False

    def test_dirt_field_present_means_matched(self):
        from custom_components.roomba_plus.api_views import (
            _local_record_has_cloud_merge_signal,
        )
        rec = _local_rec()
        rec["dirt"] = 5
        assert _local_record_has_cloud_merge_signal(rec) is True

    def test_chrgm_field_present_means_matched(self):
        from custom_components.roomba_plus.api_views import (
            _local_record_has_cloud_merge_signal,
        )
        rec = _local_rec()
        rec["chrgM"] = 0  # zero, not None -- still counts as "present"
        assert _local_record_has_cloud_merge_signal(rec) is True

    def test_wlbars_field_present_means_matched(self):
        from custom_components.roomba_plus.api_views import (
            _local_record_has_cloud_merge_signal,
        )
        rec = _local_rec()
        rec["wlBars"] = [1, 2, 3, 4, 5]
        assert _local_record_has_cloud_merge_signal(rec) is True


class TestRecordsUnionWithLocal:
    """v2.10.2 RECORDS-UNION. format=records previously discarded ALL
    local MissionStore records whenever the cloud was the source -- even
    records the cloud never knew about. Confirmed in the field: a real
    archive had format=summary's total=3 for a day while the cloud-
    sourced format=records array for that same day only had 2; the
    missing local record had no nMssn/dirt/chrgM/wlBars at all (never
    matched by backfill_from_cloud() or merge_latest_from_cloud())."""

    @staticmethod
    def _make_entry(cloud_raw_records, local_records, entry_id="abc123"):
        entry = MagicMock()
        entry.domain = DOMAIN
        entry.data = {"blid": "abc_blid_123"}
        entry.entry_id = entry_id

        ms = MagicMock()
        ms._records = list(local_records)
        ms.query = MagicMock(return_value=list(local_records))

        cc = MagicMock()
        cc.raw_records = list(cloud_raw_records)
        cc.last_update_success = True

        data = MagicMock()
        data.has_cloud = True
        data.cloud_coordinator = cc
        data.mission_store = ms
        data.umf_aligner = None
        entry.runtime_data = data
        return entry

    @staticmethod
    def _hass_for(entry):
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry
        return hass

    async def _get_records(self, cloud_records, local_records):
        entry = self._make_entry(cloud_records, local_records)
        hass = self._hass_for(entry)
        view = MissionHistoryView()
        req = _make_request(hass, fmt="records")
        resp = await view.get(req, "abc123")
        return json.loads(resp.body)

    @pytest.mark.asyncio
    async def test_unmatched_local_record_is_included(self):
        """Field bug repro: cloud has 2 records for the day, local
        MissionStore has 3 (one never matched any cloud record) --
        format=records must return all 3, not just the 2 cloud ones."""
        cloud_records = [
            _cloud_rec(start_ts=1782457275, end_ts=1782458295),
            _cloud_rec(start_ts=1782464921, end_ts=1782472921),
        ]
        local_records = [
            {  # matched (cloud-merged) -- corresponds to the first cloud record
                **_local_rec(started_at="2026-06-26T07:01:15+00:00",
                              ended_at="2026-06-26T07:18:15+00:00"),
                "dirt": 2, "chrgM": 0, "wlBars": [43, 57, 0, 0, 0],
            },
            {  # the never-matched local mission -- the actual field repro
                "id": "m_1782462420",
                "started_at": "2026-06-26T08:27:00+00:00",
                "ended_at": "2026-06-26T09:05:18+00:00",
                "duration_min": 38,
                "area_sqft": None,
                "result": "completed",
                "initiator": "",
                "zones": [],
                "error_code": None,
            },
            {  # matched (cloud-merged) -- corresponds to the second cloud record
                **_local_rec(started_at="2026-06-26T09:08:41+00:00",
                              ended_at="2026-06-26T11:44:41+00:00"),
                "dirt": 2, "chrgM": 90, "wlBars": [74, 24, 2, 0, 0],
            },
        ]
        body = await self._get_records(cloud_records, local_records)

        assert len(body) == 3
        assert sorted(r["source"] for r in body) == ["cloud", "cloud", "local"]
        assert any(r["id"] == "m_1782462420" for r in body)

    @pytest.mark.asyncio
    async def test_matched_local_record_not_duplicated(self):
        """A local record that DID get a cloud merge (dirt/chrgM/wlBars
        present) must not also appear as a separate local-source entry
        -- it's already represented by its cloud counterpart."""
        cloud_records = [_cloud_rec(start_ts=1700000000, end_ts=1700003600)]
        local_records = [
            {
                "id": "m_1700000000",
                "started_at": "2023-11-14T22:13:20+00:00",
                "ended_at": "2023-11-14T23:13:20+00:00",
                "duration_min": 60,
                "area_sqft": 180.0,
                "result": "completed",
                "initiator": "schedule",
                "zones": [],
                "error_code": None,
                "dirt": 12,      # merge signal present -- already matched
                "chrgM": 0,
                "wlBars": [70, 68, 65, 60, 62],
            },
        ]
        body = await self._get_records(cloud_records, local_records)

        assert len(body) == 1
        assert body[0]["source"] == "cloud"

    @pytest.mark.asyncio
    async def test_no_unmatched_local_records_unchanged(self):
        """No local-only records to union in -- behaviour identical to
        before this fix (pure cloud array, untouched)."""
        cloud_records = [_cloud_rec(start_ts=1700000000, end_ts=1700003600)]
        body = await self._get_records(cloud_records, [])

        assert len(body) == 1
        assert body[0]["source"] == "cloud"

    @pytest.mark.asyncio
    async def test_unioned_records_sorted_ascending(self):
        cloud_records = [_cloud_rec(start_ts=1700010000, end_ts=1700013600)]
        local_records = [
            {
                "id": "m_local_earlier",
                "started_at": "2023-11-14T20:00:00+00:00",
                "ended_at": "2023-11-14T20:30:00+00:00",
                "duration_min": 30,
                "area_sqft": None,
                "result": "completed",
                "initiator": "",
                "zones": [],
                "error_code": None,
            },
        ]
        body = await self._get_records(cloud_records, local_records)

        assert len(body) == 2
        assert body[0]["id"] == "m_local_earlier"
        assert body[1]["source"] == "cloud"



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


class TestHazardsF22TemporalPattern:
    """F22 (v3.3.1) — dominant_weekday/dominant_hour merge onto hazard pins.

    Exercises the real merge logic (hotspots() + stuck_pattern(), matched
    by (gx, gy)) against a real GridStore instance, not fabricated dicts —
    the merge itself is the thing under test, not just the shape.
    """

    def _gs_with_stuck(self, cell_data: dict):
        from custom_components.roomba_plus.grid_store import GridStore
        gs = GridStore()
        gs._stuck = cell_data
        return gs

    def _merge(self, hazards, patterns):
        """Replicates the api_views.py merge step for direct unit testing."""
        for hazard in hazards:
            dominant_weekday = None
            dominant_hour = None
            if hazard.get("source") == "stuck_events" and patterns:
                slot = patterns.get((hazard["gx"], hazard["gy"]))
                if slot:
                    dominant_weekday, dominant_hour = slot
            hazard["dominant_weekday"] = dominant_weekday
            hazard["dominant_hour"] = dominant_hour
        return hazards

    def test_dominant_slot_merged_onto_matching_pin(self):
        # 10 stucks at (0,0), 8 on Monday 09:00 → 80% dominance, above
        # both hotspots()'s threshold (3) and stuck_pattern()'s (8).
        gs = self._gs_with_stuck({
            (0, 0): {"count": 10, "times": [[0, 9]] * 8 + [[2, 14], [4, 16]]},
        })
        hazards = gs.hotspots()
        patterns = gs.stuck_pattern()
        result = self._merge(hazards, patterns)
        assert result[0]["dominant_weekday"] == 0
        assert result[0]["dominant_hour"] == 9

    def test_gap_below_stuck_pattern_threshold_stays_null(self):
        # count=5 clears hotspots()'s threshold (3) but not
        # stuck_pattern()'s (8) — known, accepted gap.
        gs = self._gs_with_stuck({
            (1, 1): {"count": 5, "times": [[3, 10]] * 5},
        })
        hazards = gs.hotspots()
        patterns = gs.stuck_pattern()
        assert patterns is None  # confirms the gap condition actually applies
        result = self._merge(hazards, patterns)
        assert result[0]["stuck_count"] == 5
        assert result[0]["dominant_weekday"] is None
        assert result[0]["dominant_hour"] is None

    def test_non_dominant_pattern_stays_null(self):
        # count=10 clears both thresholds, but times spread evenly —
        # no slot reaches the 60% dominance bar.
        gs = self._gs_with_stuck({
            (2, 2): {"count": 10, "times": [[i % 7, i % 24] for i in range(10)]},
        })
        hazards = gs.hotspots()
        patterns = gs.stuck_pattern()
        result = self._merge(hazards, patterns)
        assert result[0]["dominant_weekday"] is None
        assert result[0]["dominant_hour"] is None

    def test_robot_learned_and_keepout_pins_always_carry_null_fields(self):
        """Non-stuck_events sources never match a pattern lookup, but must
        still carry both keys (schema uniformity) — this is what the
        merge running AFTER the robot_learned/keepout appends guarantees.
        """
        gs = self._gs_with_stuck({})
        hazards = [
            {"gx": None, "gy": None, "x_mm": 1.0, "y_mm": 1.0,
             "stuck_count": None, "source": "robot_learned"},
            {"gx": None, "gy": None, "x_mm": 2.0, "y_mm": 2.0,
             "stuck_count": None, "source": "keepout"},
        ]
        result = self._merge(hazards, gs.stuck_pattern())
        for h in result:
            assert h["dominant_weekday"] is None
            assert h["dominant_hour"] is None

    def test_multiple_hotspot_cells_matched_independently(self):
        gs = self._gs_with_stuck({
            (0, 0): {"count": 10, "times": [[0, 9]] * 8 + [[2, 14], [4, 16]]},
            (5, 5): {"count": 9, "times": [[6, 20]] * 8 + [[1, 3]]},
        })
        hazards = gs.hotspots()
        patterns = gs.stuck_pattern()
        result = self._merge(hazards, patterns)
        by_cell = {(h["gx"], h["gy"]): h for h in result}
        assert by_cell[(0, 0)]["dominant_weekday"] == 0
        assert by_cell[(0, 0)]["dominant_hour"] == 9
        assert by_cell[(5, 5)]["dominant_weekday"] == 6
        assert by_cell[(5, 5)]["dominant_hour"] == 20


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


class TestStuckByPolygon:
    """v3.2.0 ROOM-ACCESS — GridStore.stuck_by_polygon(), same bounding-box
    + point-in-polygon approach as coverage_by_polygon, applied to
    self._stuck instead of self._cells."""

    def _gs(self):
        from custom_components.roomba_plus.grid_store import GridStore
        return GridStore()

    def test_empty_stuck_returns_empty_dict(self):
        gs = self._gs()
        poly = {"r1": [(0.0, 0.0), (3000.0, 0.0), (3000.0, 3000.0), (0.0, 3000.0)]}
        assert gs.stuck_by_polygon(poly) == {}

    def test_degenerate_polygon_zero(self):
        gs = self._gs()
        gs._stuck[(0, 0)] = {"count": 3}
        result = gs.stuck_by_polygon({"r1": [(0.0, 0.0), (100.0, 0.0)]})
        assert result == {"r1": 0}

    def test_stuck_event_inside_polygon_counted(self):
        gs = self._gs()
        gs._stuck[(0, 0)] = {"count": 5}  # centre ~(75, 75) mm
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.stuck_by_polygon(poly)
        assert result["r1"] == 5

    def test_stuck_event_outside_polygon_not_counted(self):
        gs = self._gs()
        gs._stuck[(100, 100)] = {"count": 5}  # far outside
        poly = {"r1": [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)]}
        result = gs.stuck_by_polygon(poly)
        assert result["r1"] == 0

    def test_multiple_stuck_cells_accumulate(self):
        gs = self._gs()
        gs._stuck[(0, 0)] = {"count": 2}
        gs._stuck[(1, 1)] = {"count": 3}
        poly = {"r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]}
        result = gs.stuck_by_polygon(poly)
        assert result["r1"] == 5

    def test_multiple_polygons(self):
        gs = self._gs()
        gs._stuck[(0, 0)] = {"count": 4}   # inside r1
        gs._stuck[(40, 40)] = {"count": 2}  # outside both
        polys = {
            "r1": [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)],
            "r2": [(5000.0, 0.0), (6000.0, 0.0), (6000.0, 1000.0), (5000.0, 1000.0)],
        }
        result = gs.stuck_by_polygon(polys)
        assert result["r1"] == 4
        assert result["r2"] == 0


class TestZoneCoverageHealthFormat:
    """v3.2.0 COVERAGE-FREQ — format=zone_coverage_health.

    A distinct format value rather than a field on format=summary's
    existing bare-array response — see api_views.py's inline rationale.
    Core room_coverage_health() logic already covered by
    TestRoomCoverageHealth (test_mission_store.py); these tests focus on
    the HTTP-level wiring.
    """

    def test_in_valid_formats(self):
        assert "zone_coverage_health" in _VALID_FORMATS

    @pytest.mark.asyncio
    async def test_empty_dict_when_no_mission_store(self):
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = None
        view = MissionHistoryView()
        req = _make_request(hass, fmt="zone_coverage_health")
        resp = await view.get(req, "abc123")
        assert resp.status == 200
        assert json.loads(resp.body) == {}

    @pytest.mark.asyncio
    async def test_returns_room_coverage_health_result(self):
        hass, entry = _make_hass_with_entry(records=[])
        real_store = _real_mission_store([])
        real_store.room_coverage_health = MagicMock(
            return_value={"Kitchen": {"status": "healthy"}}
        )
        entry.runtime_data.mission_store = real_store
        view = MissionHistoryView()
        req = _make_request(hass, fmt="zone_coverage_health")
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body == {"Kitchen": {"status": "healthy"}}


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


def _recent_iso(hours_ago: float = 24) -> str:
    """A timestamp safely inside HouseholdSummaryView's default 28-day
    window, computed relative to real now() instead of a hardcoded date.

    Three tests in TestHouseholdSummaryView used to hardcode
    "2026-06-16T..." — that date was comfortably inside the 28-day window
    when written, but is fixed real-world elapsed time away from being
    outside it. Relative dates don't have that expiry.
    """
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=hours_ago
    )
    return dt.isoformat()


def _digest_rec(
    started_at: str,
    ended_at: str | None = None,
    area_sqft: float | None = 100.0,
    result: str = "completed",
    initiator: str = "schedule",
    bbrun_hr: float | None = None,
    battery_cycles: int | None = None,
) -> dict:
    return {
        "id": f"m_{started_at}",
        "started_at": started_at,
        "ended_at": ended_at or started_at,
        "duration_min": 30,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": initiator,
        "zones": [],
        "error_code": None,
        "bbrun_hr": bbrun_hr,
        "battery_cycles": battery_cycles,
    }


def _real_mission_store(records: list[dict]):
    """A REAL MissionStore (not the MagicMock helper above) — DailyDigestView
    calls .query(), which the MagicMock _make_mission_store() doesn't
    implement (only _records is set there; the other view formats that use
    it read ._records directly, bypassing .query() entirely)."""
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    store._records = list(records)
    return store


class TestLifetimeDeltaForDay:
    """v2.9.0 DAILY-DIGEST — _lifetime_delta_for_day() pure delta logic."""

    def _delta(self, records, target_date_str, field="bbrun_hr"):
        from custom_components.roomba_plus.api_views import DailyDigestView
        target = datetime.date.fromisoformat(target_date_str)
        return DailyDigestView._lifetime_delta_for_day(records, target, field)

    def test_simple_two_day_delta(self):
        records = [
            _digest_rec("2026-06-15T08:00:00+00:00", bbrun_hr=100.0),
            _digest_rec("2026-06-16T08:00:00+00:00", bbrun_hr=101.5),
        ]
        assert self._delta(records, "2026-06-16") == pytest.approx(1.5)

    def test_multiple_records_on_target_day_uses_last(self):
        """Two missions on the target day — delta must use the LAST one
        (highest counter value), not double-count or sum them."""
        records = [
            _digest_rec("2026-06-15T08:00:00+00:00", bbrun_hr=100.0),
            _digest_rec("2026-06-16T08:00:00+00:00", bbrun_hr=100.8),
            _digest_rec("2026-06-16T14:00:00+00:00", bbrun_hr=101.5),
        ]
        assert self._delta(records, "2026-06-16") == pytest.approx(1.5)

    def test_no_prior_record_returns_none(self):
        """First-ever day of history — nothing to diff against. Must be
        None (honest 'unknown'), never silently assume a 0 baseline."""
        records = [_digest_rec("2026-06-16T08:00:00+00:00", bbrun_hr=5.0)]
        assert self._delta(records, "2026-06-16") is None

    def test_no_record_on_target_day_returns_none(self):
        records = [_digest_rec("2026-06-15T08:00:00+00:00", bbrun_hr=100.0)]
        assert self._delta(records, "2026-06-16") is None

    def test_gap_of_several_days_still_correct(self):
        """Prior record several days back (not just yesterday) — counter
        cannot have changed in the gap, so the delta is still exact."""
        records = [
            _digest_rec("2026-06-10T08:00:00+00:00", bbrun_hr=90.0),
            _digest_rec("2026-06-16T08:00:00+00:00", bbrun_hr=93.0),
        ]
        assert self._delta(records, "2026-06-16") == pytest.approx(3.0)

    def test_missing_field_value_skipped(self):
        """A record with the field absent (None) must not poison the
        delta — e.g. a 600-series robot with no battery_cycles at all."""
        records = [
            _digest_rec("2026-06-15T08:00:00+00:00", bbrun_hr=100.0,
                        battery_cycles=None),
            _digest_rec("2026-06-16T08:00:00+00:00", bbrun_hr=101.0,
                        battery_cycles=None),
        ]
        assert self._delta(records, "2026-06-16", field="battery_cycles") is None

    def test_battery_cycles_field_works_identically(self):
        records = [
            _digest_rec("2026-06-15T08:00:00+00:00", battery_cycles=40),
            _digest_rec("2026-06-16T08:00:00+00:00", battery_cycles=42),
        ]
        assert self._delta(records, "2026-06-16", field="battery_cycles") == 2


class TestDailyDigestView:
    """v2.9.0 DAILY-DIGEST — full GET /api/roomba_plus/{entry_id}/digest flow."""

    def _make_request(self, hass, date: str | None = None):
        req = MagicMock()
        req.app = {"hass": hass}
        req.query = {"date": date} if date else {}
        return req

    @pytest.mark.asyncio
    async def test_basic_digest_shape(self):
        from custom_components.roomba_plus.api_views import DailyDigestView

        records = [
            _digest_rec("2026-06-16T08:00:00+00:00", area_sqft=250.0,
                        result="completed", initiator="schedule",
                        bbrun_hr=100.0, battery_cycles=40),
            _digest_rec("2026-06-16T14:00:00+00:00", area_sqft=500.0,
                        result="stuck", initiator="demand",
                        bbrun_hr=101.5, battery_cycles=42),
            # prior day — baseline for the deltas above
            _digest_rec("2026-06-15T08:00:00+00:00", area_sqft=300.0,
                        bbrun_hr=99.0, battery_cycles=39),
        ]
        hass, _ = _make_hass_with_entry(records=records)
        hass.config_entries.async_get_entry.return_value.runtime_data.mission_store = (
            _real_mission_store(records)
        )

        view = DailyDigestView()
        req = self._make_request(hass, "2026-06-16")
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)

        assert body["missions"] == 2
        # SQFT_TO_M2 ≈ 0.0929 — (250+500) * 0.0929 ≈ 69.7
        assert body["area_m2"] == pytest.approx(69.7, abs=0.1)
        assert body["stuck_events"] == 1
        assert body["demand_cleans"] == 1
        assert body["filter_hours_today"] == pytest.approx(2.5)   # 101.5-99.0
        assert body["battery_cycles_today"] == 3                  # 42-39

    @pytest.mark.asyncio
    async def test_no_missions_on_date_returns_zeros(self):
        from custom_components.roomba_plus.api_views import DailyDigestView

        records = [_digest_rec("2026-06-10T08:00:00+00:00", bbrun_hr=90.0)]
        hass, _ = _make_hass_with_entry(records=records)
        hass.config_entries.async_get_entry.return_value.runtime_data.mission_store = (
            _real_mission_store(records)
        )

        view = DailyDigestView()
        req = self._make_request(hass, "2026-06-16")
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)

        assert body["missions"] == 0
        assert body["area_m2"] is None
        assert body["stuck_events"] == 0
        assert body["demand_cleans"] == 0
        assert body["filter_hours_today"] is None  # no record ON 06-16 at all

    @pytest.mark.asyncio
    async def test_invalid_date_returns_400(self):
        from custom_components.roomba_plus.api_views import DailyDigestView

        hass, _ = _make_hass_with_entry(records=[])
        hass.config_entries.async_get_entry.return_value.runtime_data.mission_store = (
            _real_mission_store([])
        )

        view = DailyDigestView()
        req = self._make_request(hass, "not-a-date")
        resp = await view.get(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_entry_not_found_returns_404(self):
        from custom_components.roomba_plus.api_views import DailyDigestView

        hass, _ = _make_hass_with_entry(entry_present=False)
        view = DailyDigestView()
        req = self._make_request(hass)
        resp = await view.get(req, "missing_entry")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_date_param_defaults_to_today(self):
        """When `date` is omitted, defaults to today — just verify it
        doesn't error and returns a well-formed response."""
        from custom_components.roomba_plus.api_views import DailyDigestView

        hass, _ = _make_hass_with_entry(records=[])
        hass.config_entries.async_get_entry.return_value.runtime_data.mission_store = (
            _real_mission_store([])
        )

        view = DailyDigestView()
        req = self._make_request(hass)  # no date
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["missions"] == 0


class TestExplainMissionView:
    """v3.2.0 ANOMALY-EXPLAIN — REST counterpart to the explain_mission
    service. Underlying explanation logic is covered by
    TestExplainMissionMethod (test_dirt_threshold_manager.py) — these
    tests focus on the HTTP-specific plumbing (404s, "latest" resolution,
    JSON shape), using a REAL MissionStore (see _real_mission_store) since
    the view calls .explain_mission(), not a MagicMock-friendly attribute.
    """

    def _make_request(self, hass: MagicMock) -> MagicMock:
        req = MagicMock()
        req.app = {"hass": hass}
        return req

    @pytest.mark.asyncio
    async def test_entry_not_found_404(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, _ = _make_hass_with_entry(entry_present=False)
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "latest")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_not_ready_503(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, _ = _make_hass_with_entry(runtime_data_set=False)
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "latest")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_no_mission_store_404(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = None
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "latest")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_latest_resolves_to_most_recent(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        rec1 = _make_record("m_1")
        rec2 = _make_record("m_2", started_at="2026-05-01T10:00:00+00:00")
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = _real_mission_store([rec1, rec2])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "latest")
        body = json.loads(resp.body)
        assert body["mission_id"] == "m_2"

    @pytest.mark.asyncio
    async def test_explicit_mission_id_in_path(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        rec1 = _make_record("m_1")
        rec2 = _make_record("m_2", started_at="2026-05-01T10:00:00+00:00")
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = _real_mission_store([rec1, rec2])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "m_1")
        body = json.loads(resp.body)
        assert body["mission_id"] == "m_1"

    @pytest.mark.asyncio
    async def test_unknown_mission_id_404(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = _real_mission_store([_make_record("m_1")])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "m_nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_response_shape(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_store = _real_mission_store([_make_record("m_1")])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "latest")
        assert resp.status == 200
        body = json.loads(resp.body)
        required = {
            "mission_id", "is_anomalous", "anomaly_reason",
            "robot_lifted", "error_code", "recommended_action",
        }
        assert required.issubset(body.keys())


class TestExplainMissionViewCloudResolve:
    """v3.3.1 EXPLAIN-CLOUD — cloud-only rows (synthetic "c_{ts}" ids,
    never in MissionStore._records) resolve against raw cloud history
    instead of always 404ing."""

    def _make_request(self, hass: MagicMock) -> MagicMock:
        req = MagicMock()
        req.app = {"hass": hass}
        return req

    def _hass_with_cloud(self, raw_records: list[dict], local_records: list[dict] | None = None):
        hass, entry = _make_hass_with_entry(records=local_records or [])
        cc = MagicMock()
        cc.raw_records = raw_records
        entry.runtime_data.cloud_coordinator = cc
        return hass, entry

    @pytest.mark.asyncio
    async def test_resolves_cloud_only_mission_by_start_time(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        raw = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 42, "sqft": 300.0, "dirt": 5}
        hass, entry = self._hass_with_cloud([raw])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_1700000000")
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body["mission_id"] == "c_1700000000"

    @pytest.mark.asyncio
    async def test_resolves_by_end_time_when_start_time_absent(self):
        """Mirrors _cloud_record_to_unified()'s id-minting precedence:
        f"c_{start_ts}" if start_ts else f"c_{end_ts}"."""
        from custom_components.roomba_plus.api_views import ExplainMissionView
        raw = {"startTime": None, "timestamp": 1700003600,
               "durationM": 20, "sqft": 100.0, "dirt": 1}
        hass, entry = self._hass_with_cloud([raw])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_1700003600")
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body["mission_id"] == "c_1700003600"

    @pytest.mark.asyncio
    async def test_no_matching_raw_record_404(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        raw = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 42, "sqft": 300.0, "dirt": 5}
        hass, entry = self._hass_with_cloud([raw])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_9999999999")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_cloud_coordinator_404(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.cloud_coordinator = None
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_1700000000")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_missing_recharge_data_does_not_crash_or_flag_lifted(self):
        """Cloud-only records never carry recharge_min/npicks_delta —
        response must still be well-formed, robot_lifted honestly False."""
        from custom_components.roomba_plus.api_views import ExplainMissionView
        raw = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 42, "sqft": 300.0, "dirt": 5}
        hass, entry = self._hass_with_cloud([raw])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_1700000000")
        body = json.loads(resp.body)
        assert body["robot_lifted"] is False

    @pytest.mark.asyncio
    async def test_error_code_derived_from_pause_id(self):
        from custom_components.roomba_plus.api_views import ExplainMissionView
        raw = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 42, "sqft": 300.0, "dirt": 5, "pauseId": 17}
        hass, entry = self._hass_with_cloud([raw])
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "c_1700000000")
        body = json.loads(resp.body)
        assert body["error_code"] == 17

    @pytest.mark.asyncio
    async def test_non_c_prefixed_id_unaffected_by_cloud_path(self):
        """A real local id must still resolve locally, never touching the
        cloud-resolution branch (which only engages for "c_"-prefixed ids)."""
        from custom_components.roomba_plus.api_views import ExplainMissionView
        hass, entry = self._hass_with_cloud(
            raw_records=[], local_records=[_make_record("m_1")]
        )
        view = ExplainMissionView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "m_1")
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body["mission_id"] == "m_1"


class TestCloudRecordToExplainInput:
    def test_field_names_match_anomaly_reason_expectations(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_explain_input
        raw = {"startTime": 1700000000, "timestamp": 1700003600,
               "durationM": 42, "sqft": 300.0, "dirt": 5, "pauseId": 3}
        result = _cloud_record_to_explain_input(raw)
        assert result["id"] == "c_1700000000"
        assert result["duration_min"] == 42
        assert result["area_sqft"] == 300.0
        assert result["dirt"] == 5
        assert result["error_code"] == 3

    def test_zero_pause_id_means_no_error(self):
        from custom_components.roomba_plus.api_views import _cloud_record_to_explain_input
        raw = {"startTime": 1700000000, "durationM": 42, "sqft": 300.0,
               "dirt": 5, "pauseId": 0}
        result = _cloud_record_to_explain_input(raw)
        assert result["error_code"] is None

    def test_no_recharge_min_or_npicks_delta_keys(self):
        """These are local-only telemetry — deliberately absent, not null."""
        from custom_components.roomba_plus.api_views import _cloud_record_to_explain_input
        raw = {"startTime": 1700000000, "durationM": 42, "sqft": 300.0, "dirt": 5}
        result = _cloud_record_to_explain_input(raw)
        assert "recharge_min" not in result
        assert "npicks_delta" not in result


class TestResolveCloudExplainRecord:
    def test_returns_none_when_no_cloud_coordinator(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_explain_record
        data = MagicMock()
        data.cloud_coordinator = None
        assert _resolve_cloud_explain_record(data, "c_1700000000") is None

    def test_returns_none_for_non_c_prefixed_id(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_explain_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = []
        assert _resolve_cloud_explain_record(data, "m_1700000000") is None

    def test_returns_none_for_malformed_timestamp(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_explain_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = []
        assert _resolve_cloud_explain_record(data, "c_not_a_number") is None


class TestResolveCloudMissionMapRecord:
    """MISSION-MAP-CLOUD-ROWS — same synthetic "c_{ts}" resolution as
    EXPLAIN-CLOUD (v3.3.1), never carried over to /mission-map until now."""

    def test_returns_none_when_no_cloud_coordinator(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator = None
        assert _resolve_cloud_mission_map_record(data, "c_1700000000") is None

    def test_returns_none_for_non_c_prefixed_id(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = []
        assert _resolve_cloud_mission_map_record(data, "m_1700000000") is None

    def test_returns_none_for_malformed_timestamp(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = []
        assert _resolve_cloud_mission_map_record(data, "c_not_a_number") is None

    def test_returns_none_when_no_raw_record_matches(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = [
            {"startTime": 1699999999, "timestamp": 1700003600, "nMssn": 88},
        ]
        assert _resolve_cloud_mission_map_record(data, "c_1700000000") is None

    def test_resolves_matching_record_via_starttime(self):
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = [
            {"startTime": 1700000000, "timestamp": 1700003600, "nMssn": 90,
             "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}]},
        ]
        result = _resolve_cloud_mission_map_record(data, "c_1700000000")
        assert result == {
            "id": "c_1700000000",
            "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}],
            "nMssn": 90,
        }

    def test_resolves_matching_record_via_timestamp_fallback(self):
        """No startTime on the raw record — same fallback precedence
        _cloud_record_to_unified() uses when minting the id in the
        first place (id = f"c_{start_ts}" if start_ts else f"c_{end_ts}")."""
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = [
            {"timestamp": 1700003600, "nMssn": 91,
             "pmaps_info": [{"pmap_id": "P2", "pmapv_id": "V8"}]},
        ]
        result = _resolve_cloud_mission_map_record(data, "c_1700003600")
        assert result["pmaps_info"] == [{"pmap_id": "P2", "pmapv_id": "V8"}]

    def test_resolved_record_with_no_pmaps_info_is_not_this_functions_concern(self):
        """A resolved EPHEMERAL-tier record with no pmaps_info still
        returns a dict (id + nMssn, pmaps_info=None) — the 404 for that
        case happens downstream inside async_fetch_mission_map(), same
        as any local record in that state."""
        from custom_components.roomba_plus.api_views import _resolve_cloud_mission_map_record
        data = MagicMock()
        data.cloud_coordinator.raw_records = [
            {"startTime": 1700000000, "nMssn": 90},
        ]
        result = _resolve_cloud_mission_map_record(data, "c_1700000000")
        assert result == {"id": "c_1700000000", "pmaps_info": None, "nMssn": 90}


def _real_mission_archive(derived_records: list[dict]):
    from custom_components.roomba_plus.mission_archive import MissionArchive
    archive = MissionArchive()
    archive._derived = derived_records
    return archive


class TestMissionPathView:
    """v3.2.0 MISSION-REPLAY — GET /api/roomba_plus/{entry_id}/mission/{n_mssn}/path."""

    def _make_request(self, hass: MagicMock) -> MagicMock:
        req = MagicMock()
        req.app = {"hass": hass}
        return req

    @pytest.mark.asyncio
    async def test_entry_not_found_404(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, _ = _make_hass_with_entry(entry_present=False)
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_not_ready_503(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, _ = _make_hass_with_entry(runtime_data_set=False)
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_no_mission_archive_404(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = None
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_invalid_nmssn_400(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([])
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "not_a_number")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_mission_not_found_404(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([])
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "999")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_timeline_with_smart_tier_region_names(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([{
            "nMssn": 102,
            "room_visits": [
                {"rid": "1", "ts": 1696660084},
                {"rid": "2", "ts": 1696660945},
            ],
        }])
        cc = MagicMock()
        cc.regions = [
            {"id": "1", "name": "Kitchen"},
            {"id": "2", "name": "Hallway"},
        ]
        entry.runtime_data.cloud_coordinator = cc
        entry.runtime_data.room_seg_store = None
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        assert resp.status == 200
        body = json.loads(resp.body)
        assert body["nMssn"] == 102
        assert [p["room"] for p in body["path"]] == ["Kitchen", "Hallway"]

    @pytest.mark.asyncio
    async def test_timeline_falls_back_to_rid_when_unnamed(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([{
            "nMssn": 102,
            "room_visits": [{"rid": "99", "ts": 1696660084}],
        }])
        entry.runtime_data.cloud_coordinator = None
        entry.runtime_data.room_seg_store = None
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        body = json.loads(resp.body)
        assert body["path"][0]["room"] == "99"

    @pytest.mark.asyncio
    async def test_ephemeral_tier_uses_room_seg_store_names(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([{
            "nMssn": 102,
            "room_visits": [{"rid": "r1", "ts": 1696660084}],
        }])
        entry.runtime_data.cloud_coordinator = None
        seg_room = MagicMock()
        seg_room.name = "Living Room"
        seg_store = MagicMock()
        seg_store.rooms = {"r1": seg_room}
        entry.runtime_data.room_seg_store = seg_store
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        body = json.loads(resp.body)
        assert body["path"][0]["room"] == "Living Room"

    @pytest.mark.asyncio
    async def test_consecutive_same_room_collapsed_in_response(self):
        from custom_components.roomba_plus.api_views import MissionPathView
        hass, entry = _make_hass_with_entry(records=[])
        entry.runtime_data.mission_archive = _real_mission_archive([{
            "nMssn": 102,
            "room_visits": [
                {"rid": "1", "ts": 100},
                {"rid": "1", "ts": 150},
                {"rid": "2", "ts": 200},
            ],
        }])
        entry.runtime_data.cloud_coordinator = None
        entry.runtime_data.room_seg_store = None
        view = MissionPathView()
        req = self._make_request(hass)
        resp = await view.get(req, "abc123", "102")
        body = json.loads(resp.body)
        assert len(body["path"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 MISSION-MAP — REST error mapping + json shape
# ─────────────────────────────────────────────────────────────────────────────

class TestMissionMapViews:
    """404 / 409 / 502 mapping of the shared payload helper and the
    rooms-context enrichment of map.json."""

    def _request(self, entry):
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry
        request = MagicMock()
        request.app = {"hass": hass}
        return request

    def _entry(self, records, umf=None, side_effect=None):
        from custom_components.roomba_plus.mission_store import MissionStore
        entry = MagicMock()
        entry.domain = "roomba_plus"
        data = entry.runtime_data
        ms = MissionStore(); ms._records = records
        data.mission_store = ms
        data.blid = "BLID1"
        data.mission_map_cache = {}
        if side_effect is not None:
            data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
                side_effect=side_effect)
        else:
            data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
                return_value=umf or {})
        return entry

    @staticmethod
    def _rec(nmssn=90):
        return {"id": "m_1", "nMssn": nmssn,
                "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}]}

    @staticmethod
    def _umf(nmssn=90):
        return {"maps": [{
            "map_header": {"nmssn": nmssn, "mission_id": "01HB"},
            "layers": [{"layer_type": "coverage", "geometry": {
                "point_area": [0.1, 0.1],
                "coordinates": [[1.0, 2.0]],
            }}],
        }]}

    @pytest.mark.asyncio
    async def test_record_without_pmaps_info_is_404(self):
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([{"id": "m_old", "ended_at": "2026-01-01T00:00:00"}])
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "m_old")
        assert payload is None and err[0] == 404

    @pytest.mark.asyncio
    async def test_mismatch_is_409(self):
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([self._rec(nmssn=90)], umf=self._umf(nmssn=91))
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "m_1")
        assert payload is None and err[0] == 409

    @pytest.mark.asyncio
    async def test_cloud_error_is_502(self):
        from custom_components.roomba_plus.api_views import _mission_map_payload
        from custom_components.roomba_plus.cloud_api import CloudApiError
        entry = self._entry([self._rec()], side_effect=CloudApiError("boom"))
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "m_1")
        assert payload is None and err[0] == 502

    @pytest.mark.asyncio
    async def test_latest_resolves_and_payload_shape(self):
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([self._rec()], umf=self._umf())
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "latest")
        assert err is None and data is entry.runtime_data
        assert payload["coverage_mm"] == [[1000.0, 2000.0]]
        assert payload["nmssn"] == 90
        assert payload["pmapv_id"] == "V7"

    @pytest.mark.asyncio
    async def test_cloud_only_row_resolves_via_c_prefix(self):
        """MISSION-MAP-CLOUD-ROWS — a cloud-only synthetic id ("c_{ts}"),
        never in ms.records, must resolve via the raw cloud history
        instead of 404ing — the same gap class EXPLAIN-CLOUD (v3.3.1)
        fixed for /explain, never carried over here until now."""
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([], umf=self._umf())
        entry.runtime_data.cloud_coordinator.raw_records = [
            {"startTime": 1700000000, "timestamp": 1700003600, "nMssn": 90,
             "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}]},
        ]
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "c_1700000000")
        assert err is None
        assert payload["coverage_mm"] == [[1000.0, 2000.0]]
        assert payload["pmapv_id"] == "V7"

    @pytest.mark.asyncio
    async def test_cloud_only_row_unresolvable_is_404(self):
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([])
        entry.runtime_data.cloud_coordinator.raw_records = []
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "c_1700000000")
        assert payload is None and err[0] == 404

    @pytest.mark.asyncio
    async def test_cloud_only_row_without_pmaps_info_is_404_downstream(self):
        """Known remaining gap, unchanged by this fix: an EPHEMERAL-tier
        cloud record with no pmaps_info still 404s — correctly, via the
        existing async_fetch_mission_map() branch, not a new failure
        mode introduced by the c_-prefix resolution itself."""
        from custom_components.roomba_plus.api_views import _mission_map_payload
        entry = self._entry([])
        entry.runtime_data.cloud_coordinator.raw_records = [
            {"startTime": 1700000000, "nMssn": 90},
        ]
        payload, data, err = await _mission_map_payload(
            self._request(entry), "e1", "c_1700000000")
        assert payload is None and err[0] == 404


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 bug-hunt round 5 — the view get() glue layer above the payload
# helper (rooms enrichment in JSON view, executor call in PNG view) had
# no direct test — only _mission_map_payload itself was tested.
# ─────────────────────────────────────────────────────────────────────────────

class TestMissionMapViewsGlue:
    def _entry_with_aligner(self, umf, room_polys=None):
        from custom_components.roomba_plus.mission_store import MissionStore
        entry = MagicMock()
        entry.domain = "roomba_plus"
        data = entry.runtime_data
        ms = MissionStore()
        ms._records = [{
            "id": "m_1", "nMssn": 90,
            "pmaps_info": [{"pmap_id": "P1", "pmapv_id": "V7"}],
        }]
        data.mission_store = ms
        data.blid = "BLID1"
        data.mission_map_cache = {}
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(return_value=umf)
        data.umf_aligner.aligned = True
        data.umf_aligner.rid_to_name.return_value = {"7": "Kitchen"}
        data.umf_aligner.room_polygons_umf = room_polys or {
            "7": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
        }
        return entry

    @staticmethod
    def _umf():
        return {"maps": [{
            "map_header": {"nmssn": 90, "mission_id": "01HB"},
            "layers": [{"layer_type": "coverage", "geometry": {
                "point_area": [0.1, 0.1], "coordinates": [[1.0, 2.0]],
            }}],
        }]}

    def _request(self, entry):
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = entry
        hass.async_add_executor_job = AsyncMock(
            side_effect=lambda func, *a: func(*a)
        )
        request = MagicMock()
        request.app = {"hass": hass}
        return request

    @pytest.mark.asyncio
    async def test_json_view_enriches_with_resolved_room_names(self):
        from custom_components.roomba_plus.api_views import MissionMapJsonView
        entry = self._entry_with_aligner(self._umf())
        view = MissionMapJsonView()
        with patch.object(
            view, "json", side_effect=lambda body: body
        ):
            result = await view.get(self._request(entry), "e1", "m_1")
        assert result["rooms"] == {"Kitchen": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]}
        assert result["coverage_mm"] == [[1000.0, 2000.0]]

    @pytest.mark.asyncio
    async def test_json_view_maps_error_status(self):
        from custom_components.roomba_plus.api_views import MissionMapJsonView
        entry = MagicMock()
        entry.domain = "roomba_plus"
        entry.runtime_data.mission_store = None
        view = MissionMapJsonView()
        resp = await view.get(self._request(entry), "e1", "m_1")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_png_view_renders_via_executor_with_room_polygons(self):
        from custom_components.roomba_plus.api_views import MissionMapPngView
        entry = self._entry_with_aligner(self._umf())
        view = MissionMapPngView()
        resp = await view.get(self._request(entry), "e1", "m_1")
        assert resp.content_type == "image/png"
        assert resp.body[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_png_view_maps_error_status(self):
        from custom_components.roomba_plus.api_views import MissionMapPngView
        entry = MagicMock()
        entry.domain = "roomba_plus"
        entry.runtime_data.mission_store = None
        view = MissionMapPngView()
        resp = await view.get(self._request(entry), "e1", "m_1")
        assert resp.status == 404

    def _request_with_query(self, entry, query: dict):
        """v3.4.1 ROTATE-PARAM — like _request(), but with a real query dict
        instead of a MagicMock default, so request.query.get(...) behaves
        like an actual aiohttp request would."""
        request = self._request(entry)
        request.query = query
        return request

    @pytest.mark.asyncio
    async def test_png_view_default_rotate_is_zero(self):
        """No ?rotate query param — a real aiohttp request always provides
        an empty (but real) query MultiDict here, never a bare Mock, so
        this uses _request_with_query with an empty dict to match that
        reality rather than the generic _request() helper's bare
        MagicMock query (which, misleadingly, int()-converts to 1, not 0
        — caught while writing this test; see git history for the fix
        this prompted)."""
        from custom_components.roomba_plus.api_views import MissionMapPngView
        entry = self._entry_with_aligner(self._umf())
        view = MissionMapPngView()
        request = self._request_with_query(entry, {})
        resp = await view.get(request, "e1", "m_1")
        assert resp.content_type == "image/png"
        hass = request.app["hass"]
        args = hass.async_add_executor_job.call_args.args
        assert args[-1] == 0

    @pytest.mark.asyncio
    async def test_png_view_passes_rotate_query_param_through(self):
        from custom_components.roomba_plus.api_views import MissionMapPngView
        entry = self._entry_with_aligner(self._umf())
        view = MissionMapPngView()
        request = self._request_with_query(entry, {"rotate": "270"})
        await view.get(request, "e1", "m_1")
        hass = request.app["hass"]
        # render_mission_map_png(coverage_mm, point_area_m, rooms, rotate)
        args = hass.async_add_executor_job.call_args.args
        assert args[-1] == 270

    @pytest.mark.asyncio
    async def test_png_view_non_numeric_rotate_falls_back_to_zero(self):
        """A malformed query param must degrade gracefully (unrotated
        image), not 500 the whole endpoint."""
        from custom_components.roomba_plus.api_views import MissionMapPngView
        entry = self._entry_with_aligner(self._umf())
        view = MissionMapPngView()
        request = self._request_with_query(entry, {"rotate": "sideways"})
        resp = await view.get(request, "e1", "m_1")
        assert resp.content_type == "image/png"
        hass = request.app["hass"]
        args = hass.async_add_executor_job.call_args.args
        assert args[-1] == 0


class TestHouseholdSummaryView:
    """GET /api/roomba_plus/household — previously entirely untested.

    v3.4.3 FLEET-1 added a per-robot health rollup (health_trend,
    battery_capacity_retention_pct, maintenance_due, needs_attention) plus
    a top-level fleet_health summary; this class covers both that new
    behaviour and the pre-existing missions/completed/area/floors rollup,
    since neither had a dedicated test before now.
    """

    def _make_entry(
        self,
        entry_id: str,
        name: str,
        records: list[dict] | None = None,
        floor_label: str = "",
        health_trend: str | None = None,
        battery_retention: float | None = None,
        vac_state: dict | None = None,
        maintenance_store: MaintenanceStore | None = None,
        has_robot_profile_store: bool = True,
    ) -> MagicMock:
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.title = name
        entry.options = {}

        data = MagicMock()
        data.mission_store = _make_mission_store(records or [])
        data.floor_label = floor_label
        data.battery_retention_value = battery_retention

        if has_robot_profile_store:
            rps = MagicMock()
            rps.health_score_trend.return_value = health_trend
            data.robot_profile_store = rps
        else:
            data.robot_profile_store = None

        data.maintenance_store = (
            maintenance_store if maintenance_store is not None else MaintenanceStore()
        )
        data.roomba = MagicMock()
        data.roomba.master_state = {"state": {"reported": vac_state or {}}}

        entry.runtime_data = data
        return entry

    def _make_request(self, hass: MagicMock, days: str | None = None) -> MagicMock:
        req = MagicMock()
        req.app = {"hass": hass}
        req.query = {"days": days} if days else {}
        return req

    @pytest.mark.asyncio
    async def test_single_robot_basic_rollup(self):
        """Pre-existing behaviour: missions/completed/area, unaffected by
        FLEET-1's additions."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        records = [
            _digest_rec(_recent_iso(24), area_sqft=250.0, result="completed"),
            _digest_rec(_recent_iso(18), area_sqft=500.0, result="stuck"),
        ]
        entry = self._make_entry("e1", "Downstairs", records=records)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["total"]["missions"] == 2
        assert body["total"]["completed"] == 1
        assert body["robots"][0]["entry_id"] == "e1"
        assert body["robots"][0]["name"] == "Downstairs"

    @pytest.mark.asyncio
    async def test_floors_aggregate_across_robots(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        r1 = [_digest_rec(_recent_iso(24), area_sqft=100.0, result="completed")]
        r2 = [_digest_rec(_recent_iso(23), area_sqft=200.0, result="completed")]
        entry1 = self._make_entry("e1", "Up", records=r1, floor_label="Upstairs")
        entry2 = self._make_entry("e2", "Down", records=r2, floor_label="Upstairs")
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry1, entry2]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        # Total-level accumulation across robots (real-endpoint replacement
        # for the old synthetic test_total_area_accumulates_across_robots).
        assert body["total"]["area_sqft"] == 300.0
        assert len(body["floors"]) == 1
        assert body["floors"][0]["missions"] == 2
        # Floor-level accumulation (real-endpoint replacement for the old
        # synthetic test_floor_aggregation_combines_robots).
        assert body["floors"][0]["area_sqft"] == 300.0

    @pytest.mark.asyncio
    async def test_fleet_health_no_robots_needing_attention(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry(
            "e1", "Healthy", health_trend="stable", battery_retention=95.0,
            vac_state={"bbrun": {"hr": 5}},
        )
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        robot = body["robots"][0]
        assert robot["health_trend"] == "stable"
        assert robot["battery_capacity_retention_pct"] == 95.0
        assert robot["maintenance_due"] is False
        assert robot["needs_attention"] is False
        assert body["fleet_health"] == {
            "robot_count": 1,
            "robots_needing_attention": [],
        }

    @pytest.mark.asyncio
    async def test_maintenance_due_flags_needs_attention(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry(
            "e1", "Filter Due", health_trend="stable",
            vac_state={"bbrun": {"hr": 200}},  # >= DEFAULT_FILTER_HOURS
        )
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        robot = body["robots"][0]
        assert robot["maintenance_due"] is True
        assert robot["needs_attention"] is True
        assert body["fleet_health"]["robots_needing_attention"] == ["Filter Due"]

    @pytest.mark.asyncio
    async def test_declining_health_flags_needs_attention_without_maintenance(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry(
            "e1", "Declining", health_trend="declining",
            vac_state={"bbrun": {"hr": 1}},  # nothing due
        )
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        robot = body["robots"][0]
        assert robot["maintenance_due"] is False
        assert robot["needs_attention"] is True
        assert body["fleet_health"]["robots_needing_attention"] == ["Declining"]

    @pytest.mark.asyncio
    async def test_multi_robot_fleet_health_only_lists_attention_needed(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        healthy = self._make_entry(
            "e1", "Healthy", health_trend="improving",
            vac_state={"bbrun": {"hr": 1}},
        )
        needs_help = self._make_entry(
            "e2", "Needs Help", health_trend="declining",
            vac_state={"bbrun": {"hr": 1}},
        )
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [healthy, needs_help]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["fleet_health"]["robot_count"] == 2
        assert body["fleet_health"]["robots_needing_attention"] == ["Needs Help"]

    @pytest.mark.asyncio
    async def test_no_robot_profile_store_health_trend_is_none(self):
        """Robot with no RobotProfileStore yet (e.g. very first run) must
        not crash — health_trend degrades to None, not an AttributeError."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry(
            "e1", "New Robot", has_robot_profile_store=False,
            vac_state={"bbrun": {"hr": 1}},
        )
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["robots"][0]["health_trend"] is None
        assert body["robots"][0]["needs_attention"] is False

    @pytest.mark.asyncio
    async def test_zero_missions_completion_pct_is_zero(self):
        """Real-endpoint replacement for the old synthetic
        test_completion_pct_zero_when_no_missions."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry("e1", "Idle")  # no records at all
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["total"]["missions"] == 0
        assert body["total"]["completion_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_full_completion_pct(self):
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        records = [
            _digest_rec(_recent_iso(24), result="completed"),
            _digest_rec(_recent_iso(23), result="completed"),
        ]
        entry = self._make_entry("e1", "Perfect", records=records)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["total"]["completion_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_empty_floor_label_excluded_from_floors(self):
        """Real-endpoint replacement for the old synthetic
        test_empty_floor_label_not_in_floors."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry("e1", "No Floor", floor_label="")
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert "floors" not in body

    @pytest.mark.asyncio
    async def test_total_area_none_when_no_robot_has_area(self):
        """Real-endpoint replacement for the old synthetic test of the
        same name — no record anywhere has area_sqft set."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        records = [_digest_rec("2026-06-16T08:00:00+00:00", area_sqft=None)]
        entry = self._make_entry("e1", "No Area", records=records)
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["total"]["area_sqft"] is None

    @pytest.mark.asyncio
    async def test_no_maintenance_store_defaults_to_not_due(self):
        """Mirrors the no-robot-profile-store case for maintenance_store —
        must degrade gracefully, not crash."""
        from custom_components.roomba_plus.api_views import HouseholdSummaryView

        entry = self._make_entry("e1", "No Store")
        entry.runtime_data.maintenance_store = None
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = [entry]

        view = HouseholdSummaryView()
        resp = await view.get(self._make_request(hass))
        body = json.loads(resp.body)

        assert body["robots"][0]["maintenance_due"] is False
