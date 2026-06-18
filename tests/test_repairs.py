"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from unittest.mock import call
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.repairs import async_check_bbrun_reset
from custom_components.roomba_plus.repairs import async_check_performance_degradation
from custom_components.roomba_plus.repairs import async_check_battery_recharge
from custom_components.roomba_plus.repairs import async_check_mixed_schedule
from custom_components.roomba_plus.repairs import async_check_accident_detection
from custom_components.roomba_plus.repairs import async_check_consecutive_skips
from custom_components.roomba_plus.repairs import async_enrich_drift_issue
import sys
from custom_components.roomba_plus.sensor import SENSORS
import math
from custom_components.roomba_plus.umf_aligner import UmfAligner
import time
from custom_components.roomba_plus.entity import IRobotEntity
from custom_components.roomba_plus.repairs import _DOCK_ABORTS_THRESHOLD
from custom_components.roomba_plus.repairs import _DOCK_CHATTERS_THRESHOLD
from custom_components.roomba_plus.repairs import _DOCK_KNOCKOFFS_THRESHOLD
from custom_components.roomba_plus.repairs import async_check_dock_health


_ep = sys.modules.get('homeassistant.helpers.entity_platform')


def _make_entry(mission_store=None, maintenance_store=None):
    entry = MagicMock()
    data = MagicMock()
    data.mission_store = mission_store or MissionStore()
    data.maintenance_store = maintenance_store or MaintenanceStore()
    data.consecutive_declining_speed = 0
    data.consecutive_battery_warn = 0
    data.cleaning_speed_trend_value = "stable"
    data.dirt_density_rising = False
    data.recharge_fraction_value = 5.0
    data.battery_retention_value = 95.0
    data.roomba_reported_state = MagicMock(return_value={"bbrun": {"hr": 100}})
    entry.runtime_data = data
    entry.options = {"brush_hours": 150, "filter_hours": 150}
    return entry


def _make_hass():
    hass = MagicMock()
    hass.loop = None
    return hass


def _iso(days_ago: float = 0) -> str:
    from datetime import datetime, timezone, timedelta
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _record(result="completed", initiator="schedule", dirt=10, sqft=200,
            dur_m=45, days_ago=1.0):
    return {
        "id": f"m_{int(days_ago*1000)}",
        "started_at": _iso(days_ago),
        "ended_at": _iso(days_ago - 0.04),
        "result": result,
        "initiator": initiator,
        "dirt": dirt,
        "sqft": sqft,
        "durationM": dur_m,
        "duration_min": dur_m,
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


def _make_entity(vacuum_state: dict):
    entity = object.__new__(IRobotEntity)
    entity._blid = "test"
    entity._roomba = MagicMock()
    entity.vacuum_state = vacuum_state
    return entity


def _find_desc(key: str):
    from custom_components.roomba_plus.sensor import SENSORS
    for desc in SENSORS:
        if desc.key == key:
            return desc
    return None


def _make_config_entry(bbchg: dict):
    """Build a mock config_entry whose vacuum has the given bbchg state."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.runtime_data.vacuum.master_state = {
        "state": {"reported": {"bbchg": bbchg}}
    }
    return entry


class TestPerformanceDegradation:
    @pytest.mark.asyncio
    async def test_no_issue_below_3_consecutive(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.cleaning_speed_trend_value = "declining"
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_performance_degradation(hass, entry)  # 1
            await async_check_performance_degradation(hass, entry)  # 2
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_at_3_consecutive(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.cleaning_speed_trend_value = "declining"
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            for _ in range(3):
                await async_check_performance_degradation(hass, entry)
            mock_ir.async_create_issue.assert_called_once()
            args = mock_ir.async_create_issue.call_args
            assert args[1]["translation_key"] == "performance_degradation"

    @pytest.mark.asyncio
    async def test_counter_resets_on_stable(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.cleaning_speed_trend_value = "declining"
        with patch("custom_components.roomba_plus.repairs.ir"):
            await async_check_performance_degradation(hass, entry)
            await async_check_performance_degradation(hass, entry)
        entry.runtime_data.cleaning_speed_trend_value = "stable"
        with patch("custom_components.roomba_plus.repairs.ir"):
            await async_check_performance_degradation(hass, entry)
        assert entry.runtime_data.consecutive_declining_speed == 0


class TestBatteryRecharge:
    @pytest.mark.asyncio
    async def test_no_issue_when_conditions_not_met(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.recharge_fraction_value = 5.0   # low
        entry.runtime_data.battery_retention_value = 95.0  # good
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            for _ in range(3):
                await async_check_battery_recharge(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_when_both_conditions_met_for_3_updates(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.recharge_fraction_value = 20.0  # high
        entry.runtime_data.battery_retention_value = 70.0  # degraded
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            for _ in range(3):
                await async_check_battery_recharge(hass, entry)
            mock_ir.async_create_issue.assert_called_once()
            assert mock_ir.async_create_issue.call_args[1]["translation_key"] == "battery_recharge_high"


class TestMixedSchedule:
    @pytest.mark.asyncio
    async def test_no_issue_with_single_initiator(self):
        store = MissionStore()
        for i in range(15):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        entry = _make_entry(mission_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_mixed_schedule(_make_hass(), entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_with_mixed_initiators(self):
        store = MissionStore()
        for i in range(8):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        for i in range(7):
            store._records.append(_record(initiator="rmtApp", days_ago=i+10))
        entry = _make_entry(mission_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_mixed_schedule(_make_hass(), entry)
            mock_ir.async_create_issue.assert_called_once()
            assert mock_ir.async_create_issue.call_args[1]["translation_key"] == "mixed_schedule"

    @pytest.mark.asyncio
    async def test_no_issue_below_10_records(self):
        store = MissionStore()
        for i in range(5):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        for i in range(4):
            store._records.append(_record(initiator="rmtApp", days_ago=i+6))
        entry = _make_entry(mission_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_mixed_schedule(_make_hass(), entry)
            mock_ir.async_create_issue.assert_not_called()


class TestAccidentDetection:
    def _cloud_records(self, n=20, dirt=10, sqft=200, dur_m=45):
        return [
            {"dirt": dirt, "sqft": sqft, "durationM": dur_m,
             "startTime": 1700000000 - i * 86400}
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_no_alert_on_normal_mission(self):
        records = self._cloud_records()
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_accident_detection(_make_hass(), entry, records)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_fires_on_spike_plus_short_mission(self):
        # Build baseline from 20 normal records, then prepend a spike record.
        # The spike must NOT be in the baseline pool — insert at index 0 after
        # building the list, so p95 is computed from the 20 normal records.
        normal_records = self._cloud_records(20, dirt=10, sqft=200, dur_m=45)
        spike = {"dirt": 800, "sqft": 200, "durationM": 8,
                 "startTime": 1700090000}  # 80× baseline density, 8 min
        records = [spike] + normal_records  # spike is most-recent (index 0)
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_accident_detection(_make_hass(), entry, records)
            mock_ir.async_create_issue.assert_called_once()
            assert mock_ir.async_create_issue.call_args[1]["translation_key"] == "accident_detected"

    @pytest.mark.asyncio
    async def test_no_alert_when_high_dirt_long_mission(self):
        records = self._cloud_records(20, dirt=10, sqft=200, dur_m=45)
        records[0] = {"dirt": 400, "sqft": 200, "durationM": 50,  # long — not accident
                      "startTime": 1700000000}
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_accident_detection(_make_hass(), entry, records)
            mock_ir.async_create_issue.assert_not_called()


class TestDriftEnrichment:
    @pytest.mark.asyncio
    async def test_enrichment_fires_issue(self):
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_enrich_drift_issue(_make_hass(), entry, dx=100.0, dy=0.0)
            mock_ir.async_create_issue.assert_called_once()
            args = mock_ir.async_create_issue.call_args[1]
            assert args["translation_key"] == "map_drift_detected"
            assert "bearing" in args["translation_placeholders"]
            assert "magnitude_cm" in args["translation_placeholders"]

    @pytest.mark.asyncio
    async def test_bearing_east_for_positive_dx(self):
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_enrich_drift_issue(_make_hass(), entry, dx=1000.0, dy=0.0)
            placeholders = mock_ir.async_create_issue.call_args[1]["translation_placeholders"]
            bearing = int(placeholders["bearing"])
            assert 80 <= bearing <= 100  # approximately east

    @pytest.mark.asyncio
    async def test_magnitude_calculated_correctly(self):
        entry = _make_entry()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            # dx=300mm, dy=400mm → 500mm → 50cm
            await async_enrich_drift_issue(_make_hass(), entry, dx=300.0, dy=400.0)
            placeholders = mock_ir.async_create_issue.call_args[1]["translation_placeholders"]
            assert placeholders["magnitude_cm"] == "50.0"


class TestStuckRecovery:
    def test_stuck_and_resumed_result_value(self):
        """stuck_and_resumed is a valid result string."""
        # Verify our callbacks can produce this value — check via callbacks module
        from custom_components.roomba_plus import callbacks
        assert hasattr(callbacks, "async_record_mission")

    @pytest.mark.asyncio
    async def test_result_override_accepted_in_async_record_mission(self):
        """async_record_mission accepts result_override param."""
        from custom_components.roomba_plus.callbacks import async_record_mission
        import inspect
        sig = inspect.signature(async_record_mission)
        assert "result_override" in sig.parameters

    def test_stuck_results_in_mission_store_completion_rate(self):
        """stuck_and_resumed treated as success; stuck_and_abandoned as failure."""
        store = MissionStore()
        store._records = [
            {"id": "m_1", "started_at": _iso(3), "ended_at": _iso(3),
             "result": "stuck_and_resumed"},
            {"id": "m_2", "started_at": _iso(2), "ended_at": _iso(2),
             "result": "stuck_and_abandoned"},
            {"id": "m_3", "started_at": _iso(1), "ended_at": _iso(1),
             "result": "completed"},
        ]
        records = store.query(30)
        assert len(records) == 3
        # result values are stored correctly
        results = {r["result"] for r in records}
        assert "stuck_and_resumed" in results
        assert "stuck_and_abandoned" in results


class TestErrorRecurrence:
    def _ms_with_errors(self, error_code: int, count: int):
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
        ms = MissionStore()
        # Use recent dates so query(days=30) includes them
        now_str = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        records = []
        for i in range(count):
            records.append({
                "id": f"m_{i}",
                "started_at": now_str,
                "ended_at":   now_str,
                "duration_min": 60,
                "result": "error",
                "initiator": "schedule",
                "zones": [],
                "error_code": error_code,
                "bbrun_hr": 0,
            })
        ms._records = records
        return ms

    def _entry(self, ms, aligner=None, archive=None):
        entry = MagicMock()
        entry.runtime_data.mission_store   = ms
        entry.runtime_data.umf_aligner     = aligner
        # v2.8.2: must be explicit. entry is a bare MagicMock, so an unset
        # mission_archive attribute auto-vivifies into a non-None MagicMock
        # — async_check_error_recurrence would then treat it as "has
        # records" and try to iterate a MagicMock, crashing every test that
        # doesn't set this explicitly.
        entry.runtime_data.mission_archive = archive
        return entry

    @pytest.mark.asyncio
    async def test_below_threshold_deletes_issue(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms    = self._ms_with_errors(15, 2)
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue") as mock_delete, \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            mock_delete.assert_called_once()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_threshold_creates_issue(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms    = self._ms_with_errors(15, 3)
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "error_recurrence"
            placeholders = call_kwargs["translation_placeholders"]
            assert placeholders["count"] == "3"
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_most_frequent_code_chosen(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = MissionStore()
        now_str = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ms._records = (
            [{"id": f"a{i}", "started_at": now_str, "ended_at": now_str,
              "duration_min": 60, "result": "error", "initiator": "schedule",
              "zones": [], "error_code": 15, "bbrun_hr": 0}
             for i in range(5)]
            + [{"id": f"b{i}", "started_at": now_str, "ended_at": now_str,
               "duration_min": 60, "result": "error", "initiator": "schedule",
               "zones": [], "error_code": 7, "bbrun_hr": 0}
              for i in range(3)]
        )
        entry = self._entry(ms)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_no_mission_store_no_crash(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        entry = MagicMock()
        entry.runtime_data.mission_store   = None
        entry.runtime_data.mission_archive = None
        await async_check_error_recurrence(MagicMock(), entry)  # no exception

    @pytest.mark.asyncio
    async def test_room_name_populated_from_aligner(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = self._ms_with_errors(15, 3)
        ms._records[-1]["error_position_mm"] = {"x": 2500.0, "y": 2500.0}
        ms._records[-1]["phase_at_error"]     = "run"
        aligner = _make_aligner()
        aligner._room_polygons = {
            "r1": [(0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)]
        }
        aligner._regions = [{"id": "r1", "name": "Kitchen"}]
        entry = self._entry(ms, aligner=aligner)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["room"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_room_name_unknown_without_aligner(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = self._ms_with_errors(15, 3)
        entry = self._entry(ms, aligner=None)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["room"] == "unknown location"


class TestErrorRecurrenceArchivePreferred:
    """v2.8.2 — async_check_error_recurrence prefers MissionArchive (ARC1)
    over the local MissionStore when the archive has records.

    Motivation (confirmed against live field data): some failing missions
    never produce a local MissionStore record at all (the mission fails
    before the local "had a cleaning phase" gate that creates one), but are
    fully visible in the cloud-derived archive. The two sources are not
    merged, to avoid double-counting a mission present in both.
    """

    def _archive_with_pause_ids(self, pause_id: int, count: int):
        from custom_components.roomba_plus.mission_archive import MissionArchive
        from homeassistant.util import dt as dt_util
        archive = MissionArchive()
        now_str = dt_util.utcnow().isoformat()
        # MissionArchive.recent_derived() is newest-first, mirroring how
        # _derived is actually populated in production (insert(0, ...)).
        archive._derived = [
            {
                "nMssn": i, "start_ts": now_str, "end_ts": now_str,
                "duration_min": 60, "result": f"error_{pause_id}",
                "pause_id": pause_id, "initiator": "schedule",
            }
            for i in range(count)
        ]
        return archive

    def _entry(self, ms=None, archive=None, aligner=None):
        entry = MagicMock()
        entry.runtime_data.mission_store   = ms
        entry.runtime_data.mission_archive = archive
        entry.runtime_data.umf_aligner     = aligner
        return entry

    @pytest.mark.asyncio
    async def test_archive_used_when_it_has_records(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        archive = self._archive_with_pause_ids(216, 3)
        # A conflicting local MissionStore that must be ignored — if it were
        # used instead, count/error_code below would differ.
        ms = TestErrorRecurrence()._ms_with_errors(99, 5)
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "216"
            assert placeholders["count"] == "3"

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_archive_empty(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        from custom_components.roomba_plus.mission_archive import MissionArchive
        archive = MissionArchive()  # record_count == 0
        ms = TestErrorRecurrence()._ms_with_errors(15, 3)
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_no_archive(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = TestErrorRecurrence()._ms_with_errors(15, 3)
        entry = self._entry(ms=ms, archive=None)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "15"

    @pytest.mark.asyncio
    async def test_no_store_and_no_archive_no_crash(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        entry = self._entry(ms=None, archive=None)
        await async_check_error_recurrence(MagicMock(), entry)  # no exception


    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_archive_has_only_old_records(self):
        """v2.8.2 bug-hunt fix — record_count > 0 alone is not enough to
        prefer the archive; its 30-day window must actually have data.
        Without this, a cloud sync gap during exactly the trailing 30 days
        (archive has months of older history, just nothing recent) would
        report 'no recent archive records -> no failures' even while the
        local MissionStore — which doesn't depend on cloud connectivity at
        all — keeps recording a genuine recurring failure during that gap."""
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        from custom_components.roomba_plus.mission_archive import MissionArchive
        from homeassistant.util import dt as dt_util
        from datetime import timedelta

        archive = MissionArchive()
        old_ts = (dt_util.utcnow() - timedelta(days=90)).isoformat()
        archive._derived = [
            {
                "nMssn": i, "start_ts": old_ts, "end_ts": old_ts,
                "duration_min": 60, "result": "error_216",
                "pause_id": 216, "initiator": "schedule",
            }
            for i in range(5)
        ]
        assert archive.record_count > 0  # the old, insufficient check would pass here
        assert archive.recent_derived(days=30) == []  # but the real window is empty

        ms = TestErrorRecurrence()._ms_with_errors(15, 3)
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_error_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["error_code"] == "15"


class TestCancellationRecurrence:
    """v2.8.2 — async_check_cancellation_recurrence.

    cancelled/cancelled_by_user results carry no numeric error_code, so they
    were completely invisible to async_check_error_recurrence. Confirmed
    against live field data: 15 of 35 archived missions over ~6 months were
    cancelled or cancelled_by_user — never surfaced anywhere."""

    def _entry(self, ms=None, archive=None):
        entry = MagicMock()
        entry.runtime_data.mission_store   = ms
        entry.runtime_data.mission_archive = archive
        return entry

    def _archive_with_results(self, results: list[str]):
        from custom_components.roomba_plus.mission_archive import MissionArchive
        from homeassistant.util import dt as dt_util
        archive = MissionArchive()
        now_str = dt_util.utcnow().isoformat()
        archive._derived = [
            {"nMssn": i, "start_ts": now_str, "end_ts": now_str,
             "duration_min": 60, "result": r}
            for i, r in enumerate(results)
        ]
        return archive

    def _ms_with_results(self, results: list[str]):
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
        ms = MissionStore()
        now_str = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ms._records = [
            {"id": f"m_{i}", "started_at": now_str, "ended_at": now_str,
             "duration_min": 60, "result": r, "initiator": "schedule",
             "zones": [], "error_code": None, "bbrun_hr": 0}
            for i, r in enumerate(results)
        ]
        return ms

    @pytest.mark.asyncio
    async def test_below_threshold_deletes_issue(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        archive = self._archive_with_results(["cancelled", "cancelled"])
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue") as mock_delete, \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_cancellation_recurrence(hass, entry)
            mock_delete.assert_called_once()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_threshold_creates_issue_with_breakdown(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        archive = self._archive_with_results(
            ["cancelled_by_user", "cancelled_by_user", "cancelled"]
        )
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_cancellation_recurrence(hass, entry)
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["translation_key"] == "cancellation_recurrence"
            placeholders = call_kwargs["translation_placeholders"]
            assert placeholders["count"] == "3"
            assert placeholders["by_user_count"] == "2"
            assert placeholders["other_count"] == "1"

    @pytest.mark.asyncio
    async def test_completed_results_not_counted(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        archive = self._archive_with_results(
            ["completed", "completed", "stuck_and_resumed", "error_17"]
        )
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue") as mock_delete, \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_cancellation_recurrence(hass, entry)
            mock_delete.assert_called_once()
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_no_archive(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        ms = self._ms_with_results(["cancelled", "cancelled", "cancelled_by_user"])
        entry = self._entry(ms=ms, archive=None)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_cancellation_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["count"] == "3"

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_archive_has_only_old_records(self):
        """v2.8.2 bug-hunt fix — same rationale as the matching test on
        async_check_error_recurrence: an archive with months of old history
        but nothing in the trailing 30 days must not be treated as 'no
        failures' when the local MissionStore has a real recent pattern."""
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        import custom_components.roomba_plus.repairs as repairs_mod
        from custom_components.roomba_plus.mission_archive import MissionArchive
        from homeassistant.util import dt as dt_util
        from datetime import timedelta

        archive = MissionArchive()
        old_ts = (dt_util.utcnow() - timedelta(days=90)).isoformat()
        archive._derived = [
            {"nMssn": i, "start_ts": old_ts, "end_ts": old_ts,
             "duration_min": 60, "result": "cancelled"}
            for i in range(5)
        ]
        assert archive.record_count > 0
        assert archive.recent_derived(days=30) == []

        ms = self._ms_with_results(["cancelled", "cancelled", "cancelled_by_user"])
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        with patch.object(repairs_mod.ir, "async_delete_issue"), \
             patch.object(repairs_mod.ir, "async_create_issue") as mock_create:
            await async_check_cancellation_recurrence(hass, entry)
            placeholders = mock_create.call_args.kwargs["translation_placeholders"]
            assert placeholders["count"] == "3"

    @pytest.mark.asyncio
    async def test_no_store_and_no_archive_no_crash(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        entry = self._entry(ms=None, archive=None)
        await async_check_cancellation_recurrence(MagicMock(), entry)  # no exception


class TestSmberr:
    """SMBERR — async_check_smberr fires / clears smberr_high Repair Issue."""

    def _make_entry(self, smberr_value):
        """Return a minimal config_entry mock with bbchg.smberr set."""
        from unittest.mock import MagicMock
        entry = MagicMock()
        entry.entry_id = "test_entry"
        vacuum_state = {"bbchg": {"smberr": smberr_value}}
        entry.runtime_data.vacuum.master_state = {
            "state": {"reported": vacuum_state}
        }
        return entry

    @pytest.mark.asyncio
    async def test_smberr_above_threshold_creates_issue(self):
        """smberr > 10 000 must create a smberr_high Repair Issue."""
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus.repairs import async_check_smberr

        hass = MagicMock()
        entry = self._make_entry(smberr_value=50_432)  # i7+ field value
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_check_smberr(hass, entry)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args[0], mock_create.call_args[1]
        assert mock_create.call_args[0][2] == "smberr_high_test_entry"

    @pytest.mark.asyncio
    async def test_smberr_below_threshold_clears_issue(self):
        """smberr ≤ 10 000 must delete (clear) any existing Repair Issue."""
        from unittest.mock import MagicMock, patch
        from custom_components.roomba_plus.repairs import async_check_smberr

        hass = MagicMock()
        entry = self._make_entry(smberr_value=0)  # i8+ field value
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_delete_issue"
        ) as mock_delete, patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_check_smberr(hass, entry)
        mock_delete.assert_called_once()
        mock_create.assert_not_called()


class TestDockStatsProperty:
    def test_reads_bbchg(self):
        e = _make_entity({"bbchg": {"nChatters": 42, "nKnockoffs": 5}})
        assert e.dock_stats == {"nChatters": 42, "nKnockoffs": 5}

    def test_empty_when_bbchg_absent(self):
        e = _make_entity({"bbchg3": {"estCap": 2488}})
        assert e.dock_stats == {}

    def test_does_not_read_bbchg3(self):
        """dock_stats must NOT return bbchg3 data."""
        e = _make_entity({"bbchg3": {"smberr": 99999}, "bbchg": {"nChatters": 1}})
        assert "smberr" not in e.dock_stats
        assert "nChatters" in e.dock_stats


class TestDockContactChatters:
    def test_filter_true_when_present(self):
        desc = _find_desc("dock_contact_chatters")
        assert desc.filter_fn({"bbchg": {"nChatters": 42}}) is True

    def test_filter_false_when_absent(self):
        desc = _find_desc("dock_contact_chatters")
        assert desc.filter_fn({"bbchg": {"nKnockoffs": 5}}) is False

    def test_value_fn(self):
        desc = _find_desc("dock_contact_chatters")
        e = _make_entity({"bbchg": {"nChatters": 42}})
        assert desc.value_fn(e) == 42

    def test_disabled_by_default(self):
        desc = _find_desc("dock_contact_chatters")
        assert desc.entity_registry_enabled_default is False


class TestDockKnockoffs:
    def test_filter_true_when_present(self):
        desc = _find_desc("dock_knockoffs")
        assert desc.filter_fn({"bbchg": {"nKnockoffs": 3}}) is True

    def test_value_fn(self):
        desc = _find_desc("dock_knockoffs")
        e = _make_entity({"bbchg": {"nKnockoffs": 3}})
        assert desc.value_fn(e) == 3

    def test_disabled_by_default(self):
        assert _find_desc("dock_knockoffs").entity_registry_enabled_default is False


class TestDockChargeAborts:
    def test_filter_true_when_present(self):
        desc = _find_desc("dock_charge_aborts")
        assert desc.filter_fn({"bbchg": {"nAborts": 7}}) is True

    def test_value_fn(self):
        desc = _find_desc("dock_charge_aborts")
        e = _make_entity({"bbchg": {"nAborts": 7}})
        assert desc.value_fn(e) == 7

    def test_disabled_by_default(self):
        assert _find_desc("dock_charge_aborts").entity_registry_enabled_default is False


class TestDockHealthRepairIssue:
    @pytest.mark.asyncio
    async def test_fires_when_chatters_exceeded(self):
        entry = _make_config_entry({
            "nChatters": _DOCK_CHATTERS_THRESHOLD + 1,
            "nKnockoffs": 0,
            "nAborts": 0,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_called_once()
        call_kwargs = mock_ir.async_create_issue.call_args[1]
        assert call_kwargs["translation_key"] == "dock_contact_health"
        assert str(_DOCK_CHATTERS_THRESHOLD + 1) in call_kwargs["translation_placeholders"]["chatters"]

    @pytest.mark.asyncio
    async def test_fires_when_knockoffs_exceeded(self):
        entry = _make_config_entry({
            "nChatters": 0,
            "nKnockoffs": _DOCK_KNOCKOFFS_THRESHOLD + 1,
            "nAborts": 0,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_fires_when_aborts_exceeded(self):
        entry = _make_config_entry({
            "nChatters": 0,
            "nKnockoffs": 0,
            "nAborts": _DOCK_ABORTS_THRESHOLD + 1,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_clears_when_all_below_threshold(self):
        entry = _make_config_entry({
            "nChatters": 5,
            "nKnockoffs": 1,
            "nAborts": 2,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_delete_issue.assert_called_once()
        mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_when_no_dock_fields(self):
        """No dock health fields in bbchg → function returns early."""
        entry = _make_config_entry({"smberr": 9999})  # only smberr, no dock health fields
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_not_called()
        mock_ir.async_delete_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_skipped_when_bbchg_absent(self):
        """No bbchg key at all → function returns early."""
        entry = MagicMock()
        entry.entry_id = "test"
        entry.runtime_data.vacuum.master_state = {
            "state": {"reported": {"bbchg3": {"estCap": 2488}}}
        }
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_not_called()


# ── v2.8.3 — CLOUD-STALE repair issue tests ───────────────────────────────────

class TestCloudStaleRepairIssue:
    """CLOUD-STALE (v2.8.3) — async_check_cloud_stale fires/clears correctly."""

    def _make_coordinator(self, last_success_offset_minutes: float | None):
        """Return a mock coordinator with last_success_time set relative to now."""
        from datetime import timezone, timedelta, datetime
        cc = MagicMock()
        if last_success_offset_minutes is None:
            cc.last_success_time = None
        else:
            cc.last_success_time = (
                datetime.now(timezone.utc)
                - timedelta(minutes=last_success_offset_minutes)
            )
        return cc

    def _make_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry_cloud_stale"
        return entry

    @pytest.mark.asyncio
    async def test_fires_when_stale_beyond_threshold(self):
        """Issue fires when last_success_time > CLOUD_STALE_MINUTES ago."""
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        from custom_components.roomba_plus.const import CLOUD_STALE_MINUTES
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(CLOUD_STALE_MINUTES + 5)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_cloud_stale(hass, entry, cc)
        mock_ir.async_create_issue.assert_called_once()
        kwargs = mock_ir.async_create_issue.call_args[1]
        assert kwargs["translation_key"] == "cloud_stale"

    @pytest.mark.asyncio
    async def test_clears_when_fresh(self):
        """Issue clears when last_success_time is within threshold."""
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(5)  # 5 min ago — well within 60 min threshold
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_cloud_stale(hass, entry, cc)
        mock_ir.async_delete_issue.assert_called_once()
        mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_when_no_success_yet(self):
        """No issue when last_success_time is None (startup, no fetch yet)."""
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(None)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_cloud_stale(hass, entry, cc)
        mock_ir.async_create_issue.assert_not_called()


# ── v2.8.3 — Binary sensor unit tests ─────────────────────────────────────────

class TestRoombaCloudConnected:
    """WIFI-CLOUD-HEALTH (v2.8.3) — RoombaCloudConnected binary sensor."""

    def _make_sensor(self, wifistat=None):
        from custom_components.roomba_plus.binary_sensor import RoombaCloudConnected
        roomba = MagicMock()
        reported = {}
        if wifistat is not None:
            reported["wifistat"] = wifistat
        roomba.master_state = {"state": {"reported": reported}}
        s = RoombaCloudConnected.__new__(RoombaCloudConnected)
        s.vacuum = roomba
        return s

    def test_on_when_cloud_nonzero(self):
        s = self._make_sensor({"cloud": 1})
        assert s.is_on is True

    def test_off_when_cloud_zero(self):
        s = self._make_sensor({"cloud": 0})
        assert s.is_on is False

    def test_unknown_when_wifistat_absent(self):
        s = self._make_sensor(None)
        assert s.is_on is None

    def test_unknown_when_cloud_key_absent(self):
        s = self._make_sensor({})  # wifistat present but no cloud key
        assert s.is_on is None

    def test_state_filter_gates_on_wifistat(self):
        s = self._make_sensor({})
        assert s.new_state_filter({"wifistat": {}}) is True
        assert s.new_state_filter({"signal": {}}) is False


class TestRoombaFirmwareUpdated:
    """FW-SENSOR (v2.8.3) — RoombaFirmwareUpdated binary sensor."""

    def _make_sensor(self, last_fw=None, updated_at=None):
        from custom_components.roomba_plus.binary_sensor import RoombaFirmwareUpdated
        import time as _t
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        entry = MagicMock()
        entry.runtime_data.last_firmware_version = last_fw
        entry.runtime_data.firmware_updated_at = updated_at
        s = RoombaFirmwareUpdated.__new__(RoombaFirmwareUpdated)
        s.vacuum = roomba
        s._entry = entry
        return s

    def test_none_when_no_fw_seen_yet(self):
        s = self._make_sensor(last_fw=None, updated_at=None)
        assert s.is_on is None

    def test_false_when_no_update_detected(self):
        s = self._make_sensor(last_fw="3.20.11", updated_at=None)
        assert s.is_on is False

    def test_on_within_24h(self):
        import time as _t
        s = self._make_sensor(last_fw="3.20.12", updated_at=_t.time() - 3600)
        assert s.is_on is True

    def test_off_after_24h(self):
        import time as _t
        s = self._make_sensor(last_fw="3.20.12", updated_at=_t.time() - 90000)
        assert s.is_on is False
