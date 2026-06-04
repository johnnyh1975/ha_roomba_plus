"""Tests for MissionStore.backfill_from_cloud — Step 6.

Covers:
  - Basic timestamp correction for 900-series (duration_min≈0 records)
  - id regeneration from corrected started_at
  - area_sqft backfill when local has None
  - Tolerance matching (within 120s)
  - Skip records already accurate (delta < 5 min)
  - Skip when no cloud records or no local records
  - Multiple records, partial match
  - Cloud records without startTime/timestamp are skipped
"""
import datetime
import pytest


def _utc(ts: int) -> str:
    """Unix timestamp → ISO UTC string."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _local_rec(
    started_ts: int,
    ended_ts: int,
    area_sqft=None,
    result="completed",
    zones=None,
):
    """Build a local MissionStore record."""
    started = datetime.datetime.fromtimestamp(started_ts, tz=datetime.timezone.utc)
    ended   = datetime.datetime.fromtimestamp(ended_ts,   tz=datetime.timezone.utc)
    return {
        "id":           f"m_{started_ts}",
        "started_at":   started.isoformat(),
        "ended_at":     ended.isoformat(),
        "duration_min": max(0, round((ended - started).total_seconds() / 60)),
        "area_sqft":    area_sqft,
        "result":       result,
        "initiator":    "schedule",
        "zones":        zones or [],
        "error_code":   None,
        "bbrun_hr":     100,
    }


def _cloud_rec(start_ts: int, end_ts: int, sqft=200):
    """Build a minimal cloud raw record."""
    return {
        "startTime": start_ts,
        "timestamp": end_ts,
        "sqft":      sqft,
        "done":      "done",
        "done_raw":  "done",
        "classified_result": "completed",
    }


def _make_store(records: list) -> object:
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    store._records = records
    return store


# ── core correction ───────────────────────────────────────────────────────────

class TestBackfillBasicCorrection:

    def test_corrects_started_at(self):
        """900-series: local started_at = ended_at (mssnStrtTm=0), corrected from cloud."""
        end_ts   = 1700003600
        start_ts = 1700000000   # real start: 60 min before end
        # Simulate 980 bug: local record has started_at = ended_at (wall-clock fallback)
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)  # duration=0
        store = _make_store([local])

        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert n.corrected == 1
        assert store._records[0]["started_at"] == _utc(start_ts)

    def test_corrects_duration_min(self):
        end_ts   = 1700003600
        start_ts = 1700000000   # 60 min mission
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert store._records[0]["duration_min"] == 60

    def test_corrects_id(self):
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert store._records[0]["id"] == f"m_{start_ts}"

    def test_backfills_area_sqft_when_none(self):
        """area_sqft=None (MQTT gap on 980) is filled from cloud sqft."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts, area_sqft=None)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts, sqft=185)])
        assert store._records[0]["area_sqft"] == 185

    def test_does_not_overwrite_existing_area_sqft(self):
        """area_sqft already set locally is never overwritten."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts, area_sqft=120)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts, sqft=999)])
        assert store._records[0]["area_sqft"] == 120


# ── matching logic ────────────────────────────────────────────────────────────

class TestBackfillMatching:

    def test_matches_within_tolerance(self):
        """Cloud end timestamp 60 s off from local ended_at — still matches."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        # Cloud timestamp 60s later — within default 120s tolerance
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 60)])
        assert n.corrected == 1

    def test_no_match_outside_tolerance(self):
        """Cloud end timestamp 200 s off — outside tolerance, no correction."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 200)])
        assert n.corrected == 0

    def test_custom_tolerance(self):
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        # 200s off, custom tolerance of 300s
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 200)], tolerance_sec=300)
        assert n.corrected == 1

    def test_skips_already_accurate_records(self):
        """Records with delta_start < 5 min are not corrected (already accurate)."""
        # Local record with correct start (i7/s9 gives mssnStrtTm reliably)
        start_ts = 1700000000
        end_ts   = 1700003600
        local = _local_rec(started_ts=start_ts, ended_ts=end_ts)
        store = _make_store([local])

        # Cloud record matches — but local is already accurate
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert n.corrected == 0

    def test_multiple_records_partial_match(self):
        """Only records with inaccurate timestamps are corrected."""
        end1 = 1700003600
        end2 = 1700007200
        start_accurate = 1700003600 - 3600   # i7: accurate start
        start_bad      = end2                  # 980: bad start = end

        local1 = _local_rec(started_ts=start_accurate, ended_ts=end1)
        local2 = _local_rec(started_ts=start_bad,      ended_ts=end2)
        store  = _make_store([local1, local2])

        cloud = [
            _cloud_rec(1700000000, end1),
            _cloud_rec(1700003700, end2),
        ]
        n = store.backfill_from_cloud(cloud)
        assert n.corrected == 1
        # local1 unchanged
        assert store._records[0]["started_at"] == _utc(start_accurate)
        # local2 corrected
        assert store._records[1]["started_at"] == _utc(1700003700)


# ── edge cases ────────────────────────────────────────────────────────────────

class TestBackfillEdgeCases:

    def test_empty_cloud_records(self):
        store = _make_store([_local_rec(1700003600, 1700003600)])
        assert store.backfill_from_cloud([]).corrected == 0

    def test_empty_local_records(self):
        store = _make_store([])
        assert store.backfill_from_cloud([_cloud_rec(1700000000, 1700003600)]).corrected == 0

    def test_cloud_record_missing_start_time(self):
        """Cloud record without startTime is skipped gracefully."""
        end_ts = 1700003600
        local  = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store  = _make_store([local])
        cr = {"timestamp": end_ts, "sqft": 100, "classified_result": "completed"}
        assert store.backfill_from_cloud([cr]).corrected == 0

    def test_cloud_record_missing_timestamp(self):
        """Cloud record without timestamp is not indexed."""
        end_ts = 1700003600
        local  = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store  = _make_store([local])
        cr = {"startTime": 1700000000, "sqft": 100}
        assert store.backfill_from_cloud([cr]).corrected == 0

    def test_local_record_missing_ended_at(self):
        """Local record without ended_at is skipped."""
        store = _make_store([{"id": "m_bad", "started_at": _utc(1700003600)}])
        assert store.backfill_from_cloud([_cloud_rec(1700000000, 1700003600)]).corrected == 0

    def test_returns_count_of_corrected(self):
        recs = [
            _local_rec(1700003600, 1700003600),  # bad — will be corrected
            _local_rec(1700007200, 1700007200),  # bad — will be corrected
        ]
        store = _make_store(recs)
        cloud = [
            _cloud_rec(1700000000, 1700003600),
            _cloud_rec(1700003700, 1700007200),
        ]
        assert store.backfill_from_cloud(cloud).corrected == 2

    def test_zones_and_other_fields_preserved(self):
        """Backfill only touches timestamp fields — other fields untouched."""
        end_ts = 1700003600
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts,
                           result="error", zones=["Kitchen"])
        local["error_code"] = 17
        local["bbrun_hr"]   = 250
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(1700000000, end_ts)])
        rec = store._records[0]
        assert rec["zones"]      == ["Kitchen"]
        assert rec["result"]     == "error"
        assert rec["error_code"] == 17
        assert rec["bbrun_hr"]   == 250


# ── Amendment 8f — dockedAtStart, missionId, pauseM, cmd ─────────────────────

class TestAmendment8fMergeFields:
    """Amendment 8f — four new scalar fields merged from cloud records."""

    def _local(self):
        return {
            "id": "m_1700000000",
            "started_at": _utc(1700000000),
            "ended_at":   _utc(1700003300),
            "duration_min": 55,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 0,
        }

    def _cloud(self, **extra):
        base = {
            "startTime": 1700000000,
            "timestamp": 1700003300,
            "sqft": 180,
            "dockedAtStart": True,
            "missionId": "01KT7BVQ50JD8WF2KM6E9RNYRN",
            "pauseM": 3,
            "cmd": {"command": "start", "initiator": "schedule"},
        }
        base.update(extra)
        return base

    def test_amendment_8f_fields_merged(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        local = self._local()
        ms._merge_cloud_fields(local, self._cloud())
        assert local["dockedAtStart"] is True
        assert local["missionId"] == "01KT7BVQ50JD8WF2KM6E9RNYRN"
        assert local["pauseM"] == 3
        assert local["cmd"]["command"] == "start"

    def test_amendment_8f_does_not_overwrite_existing(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        local = self._local()
        local["missionId"] = "EXISTING"
        ms = MissionStore()
        ms._merge_cloud_fields(local, self._cloud())
        assert local["missionId"] == "EXISTING"
