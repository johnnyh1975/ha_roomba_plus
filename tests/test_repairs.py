"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
import re
from unittest.mock import MagicMock
from unittest.mock import patch
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.repairs import async_check_furniture_change
from custom_components.roomba_plus.repairs import async_check_battery_contact_issue
from custom_components.roomba_plus.repairs import async_check_mixed_schedule
from custom_components.roomba_plus.repairs import async_check_accident_detection
from custom_components.roomba_plus.repairs import async_enrich_drift_issue
from custom_components.roomba_plus.repairs import async_check_favorite_multi_command
import sys
from custom_components.roomba_plus.umf_aligner import UmfAligner
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
    entry.runtime_data.roomba_reported_state.return_value = {"bbchg": bbchg}
    return entry


class TestFurnitureChange:
    """v3.2.0 FURNITURE — async_check_furniture_change, including the
    dismiss-aware 30-day suppression (the first repair check in this
    codebase to actually check prior dismiss state)."""

    def _entry_with_candidates(self, candidates):
        entry = _make_entry()
        gs = MagicMock()
        gs.furniture_candidates.return_value = candidates
        gs._furniture_dismissed_at = {}
        gs.furniture_dismiss_suppressed = MagicMock(return_value=False)
        gs.record_furniture_dismissed = MagicMock(
            side_effect=lambda cell, ts: gs._furniture_dismissed_at.__setitem__(cell, ts)
        )
        gs.clear_furniture_dismissed = MagicMock(
            side_effect=lambda cell: gs._furniture_dismissed_at.pop(cell, None)
        )
        # v3.3.0 STORE-ENCAP — repairs.py now reads through the public
        # accessors; delegate them to the seeded dict so this mock keeps
        # mirroring real GridStore behaviour instead of auto-MagicMocks.
        gs.furniture_dismissed_cells = MagicMock(
            side_effect=lambda: tuple(gs._furniture_dismissed_at.keys())
        )
        gs.is_furniture_dismissed = MagicMock(
            side_effect=lambda cell: cell in gs._furniture_dismissed_at
        )
        entry.runtime_data.grid_store = gs
        return entry, gs

    @pytest.mark.asyncio
    async def test_no_op_without_grid_store(self):
        hass = _make_hass()
        entry = _make_entry()
        entry.runtime_data.grid_store = None
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_furniture_change(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_candidates_no_issue(self):
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([])
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_furniture_change(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_for_candidate(self):
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value.async_get_issue.return_value = None
            await async_check_furniture_change(hass, entry)
            mock_ir.async_create_issue.assert_called_once()
            args = mock_ir.async_create_issue.call_args
            assert args[0][2] == "furniture_1_2"

    @pytest.mark.asyncio
    async def test_suppressed_within_30_days_skips_create(self):
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        gs.furniture_dismiss_suppressed = MagicMock(return_value=True)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value.async_get_issue.return_value = None
            await async_check_furniture_change(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_dismissal_recorded_on_first_observation(self):
        """When the issue registry reports dismissed_version is set and
        GridStore has no prior record of it, record the timestamp now."""
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        gs.furniture_dismiss_suppressed = MagicMock(return_value=True)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            issue_entry = MagicMock()
            issue_entry.dismissed_version = 1
            mock_ir.async_get.return_value.async_get_issue.return_value = issue_entry
            await async_check_furniture_change(hass, entry)
            gs.record_furniture_dismissed.assert_called_once()
            assert gs.record_furniture_dismissed.call_args[0][0] == (1, 2)

    @pytest.mark.asyncio
    async def test_dismissal_not_re_recorded_if_already_tracked(self):
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        gs._furniture_dismissed_at[(1, 2)] = "2026-07-01T00:00:00"
        gs.furniture_dismiss_suppressed = MagicMock(return_value=True)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            issue_entry = MagicMock()
            issue_entry.dismissed_version = 1
            mock_ir.async_get.return_value.async_get_issue.return_value = issue_entry
            await async_check_furniture_change(hass, entry)
            gs.record_furniture_dismissed.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_dismiss_record_cleared_after_suppression_window(self):
        """Not suppressed anymore (30 days passed) but a stale record
        exists — must be cleared before re-firing."""
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        gs._furniture_dismissed_at[(1, 2)] = "2026-05-01T00:00:00"
        gs.furniture_dismiss_suppressed = MagicMock(return_value=False)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value.async_get_issue.return_value = None
            await async_check_furniture_change(hass, entry)
            gs.clear_furniture_dismissed.assert_called_once_with((1, 2))

    @pytest.mark.asyncio
    async def test_stale_issue_deleted_before_recreate_past_suppression(self):
        """v3.2.0 bug-hunt fix — confirmed against HA's actual
        IssueRegistry source: async_create_issue on an EXISTING issue
        does NOT reset dismissed_version (dataclasses.replace doesn't
        include it in the overridden fields), so without an explicit
        delete first, an issue that was dismissed once would stay
        invisible in the Repairs UI forever, even past the 30-day
        suppression window this code intends to re-fire after."""
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        gs._furniture_dismissed_at[(1, 2)] = "2026-05-01T00:00:00"
        gs.furniture_dismiss_suppressed = MagicMock(return_value=False)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value.async_get_issue.return_value = None
            await async_check_furniture_change(hass, entry)
            mock_ir.async_delete_issue.assert_called_once_with(
                hass, "roomba_plus", "furniture_1_2"
            )
            # Delete must happen before create, not after — otherwise
            # the fresh create would immediately get deleted.
            delete_call_order = mock_ir.method_calls.index(
                next(c for c in mock_ir.method_calls if c[0] == "async_delete_issue")
            )
            create_call_order = mock_ir.method_calls.index(
                next(c for c in mock_ir.method_calls if c[0] == "async_create_issue")
            )
            assert delete_call_order < create_call_order

    @pytest.mark.asyncio
    async def test_no_delete_issue_when_never_dismissed(self):
        """The common case — a fresh candidate that was never dismissed
        must not trigger a pointless delete-then-create cycle."""
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([
            {"cell": (1, 2), "x_mm": 150.0, "y_mm": 300.0},
        ])
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value.async_get_issue.return_value = None
            await async_check_furniture_change(hass, entry)
            mock_ir.async_delete_issue.assert_not_called()
            mock_ir.async_create_issue.assert_called_once()
            mock_ir.async_create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolved_cell_dismiss_record_cleared(self):
        """A cell that was a candidate before but no longer is — its
        stale dismiss record (if any) is cleared so a future unrelated
        recurrence at the same cell isn't pre-suppressed."""
        hass = _make_hass()
        entry, gs = self._entry_with_candidates([])   # no longer a candidate
        gs._furniture_dismissed_at[(1, 2)] = "2026-07-01T00:00:00"
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_furniture_change(hass, entry)
            gs.clear_furniture_dismissed.assert_called_once_with((1, 2))


def _make_contact_entry(batpct=None, phase=""):
    """Build a fake config entry for async_check_battery_contact_issue tests.

    Reconstructed in v3.5.0 after the Repairs redesign removed the sibling
    test class this helper originally lived in; TestBatteryContactIssue still
    relies on it. Provides every runtime_data attribute the contact-issue
    check reads.
    """
    entry = MagicMock()
    rd = entry.runtime_data
    rd.roomba_reported_state = MagicMock(
        return_value={"batPct": batpct, "cleanMissionStatus": {"phase": phase}}
    )
    rd.last_batpct_value = None
    rd.last_batpct_at = None
    rd.consecutive_battery_contact_anomaly = 0
    rd.charge_cycle_peaks = []
    rd.current_charge_cycle_peak = None
    rd.was_charging = False
    return entry


class TestBatteryContactIssue:
    @pytest.mark.asyncio
    async def test_no_batpct_returns_without_error(self):
        hass = _make_hass()
        entry = _make_contact_entry()
        entry.runtime_data.roomba_reported_state = MagicMock(return_value={})
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_create_issue.assert_not_called()
            mock_ir.async_delete_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_reading_establishes_baseline_no_issue(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=50.0)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_create_issue.assert_not_called()
        assert entry.runtime_data.last_batpct_value == 50.0

    @pytest.mark.asyncio
    async def test_gradual_change_is_not_flagged(self):
        """A normal, plausible charge progression must never fire."""
        hass = _make_hass()
        entry = _make_contact_entry(batpct=20.0, phase="charge")
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            for pct in [20.0, 25.0, 30.0, 35.0, 40.0]:
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": pct, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_implausible_jump_within_window_flagged_after_debounce(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=28.0)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)  # baseline=28
            entry.runtime_data.roomba_reported_state.return_value = {
                "batPct": 100.0, "cleanMissionStatus": {"phase": "charge"},
            }
            await async_check_battery_contact_issue(hass, entry)  # jump #1 (28->100)
            entry.runtime_data.roomba_reported_state.return_value = {
                "batPct": 30.0, "cleanMissionStatus": {"phase": "charge"},
            }
            await async_check_battery_contact_issue(hass, entry)  # jump #2 (100->30)
            mock_ir.async_create_issue.assert_called_once()
            call_kwargs = mock_ir.async_create_issue.call_args[1]
            assert call_kwargs["translation_key"] == "battery_contact_suspect"
            assert call_kwargs["translation_placeholders"]["cause"] == "jump"

    @pytest.mark.asyncio
    async def test_single_isolated_jump_does_not_fire_below_debounce(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=28.0)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)
            entry.runtime_data.roomba_reported_state.return_value = {
                "batPct": 100.0, "cleanMissionStatus": {"phase": "charge"},
            }
            await async_check_battery_contact_issue(hass, entry)  # only ONE jump so far
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_jump_outside_time_window_not_flagged(self):
        """A large change is plausible if enough real time passed."""
        hass = _make_hass()
        entry = _make_contact_entry(batpct=10.0)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)
            # Simulate a lot of elapsed time by directly backdating last_batpct_at
            entry.runtime_data.last_batpct_at -= 3600  # 1 hour ago
            entry.runtime_data.roomba_reported_state.return_value = {
                "batPct": 90.0, "cleanMissionStatus": {"phase": "charge"},
            }
            await async_check_battery_contact_issue(hass, entry)
        assert entry.runtime_data.consecutive_battery_contact_anomaly == 0

    @pytest.mark.asyncio
    async def test_declining_peak_trend_fires_issue(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=10.0, phase="run")
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)  # not charging yet

            for peak in [85.0, 60.0, 40.0, 28.0]:
                # enter a charge cycle, ramp to the peak, then leave it
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": peak, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": peak, "cleanMissionStatus": {"phase": "run"},
                }
                await async_check_battery_contact_issue(hass, entry)

            assert entry.runtime_data.charge_cycle_peaks[-3:] == [60.0, 40.0, 28.0]
            mock_ir.async_create_issue.assert_called()
            call_kwargs = mock_ir.async_create_issue.call_args[1]
            assert call_kwargs["translation_placeholders"]["cause"] in ("declining_trend", "jump")

    @pytest.mark.asyncio
    async def test_stable_peaks_do_not_fire(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=10.0, phase="run")
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            for peak in [98.0, 99.0, 100.0, 98.0]:
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": peak, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": peak, "cleanMissionStatus": {"phase": "run"},
                }
                await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_cycle_peaks_history_is_bounded(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=10.0, phase="run")
        with patch("custom_components.roomba_plus.repairs.ir"):
            for i in range(10):
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": 50.0 + i, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": 50.0 + i, "cleanMissionStatus": {"phase": "run"},
                }
                await async_check_battery_contact_issue(hass, entry)
        assert len(entry.runtime_data.charge_cycle_peaks) == 5  # _CHARGE_PEAK_HISTORY_LEN

    @pytest.mark.asyncio
    async def test_recovers_and_clears_issue(self):
        hass = _make_hass()
        entry = _make_contact_entry(batpct=28.0)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)
            for pct in [100.0, 30.0]:
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": pct, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_create_issue.assert_called_once()

            # Stable readings afterward must clear the issue.
            for pct in [30.0, 31.0, 32.0]:
                entry.runtime_data.roomba_reported_state.return_value = {
                    "batPct": pct, "cleanMissionStatus": {"phase": "charge"},
                }
                await async_check_battery_contact_issue(hass, entry)
            mock_ir.async_delete_issue.assert_called()


class TestMixedSchedule:
    @pytest.mark.asyncio
    async def test_no_event_with_single_initiator(self):
        store = MissionStore()
        for i in range(15):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        entry = _make_entry(mission_store=store)
        hass = _make_hass()
        await async_check_mixed_schedule(hass, entry)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_fires_with_mixed_initiators(self):
        from custom_components.roomba_plus.const import EVENT_MIXED_SCHEDULE
        store = MissionStore()
        for i in range(8):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        for i in range(7):
            store._records.append(_record(initiator="rmtApp", days_ago=i+10))
        entry = _make_entry(mission_store=store)
        hass = _make_hass()
        await async_check_mixed_schedule(hass, entry)
        hass.bus.async_fire.assert_called_once()
        args = hass.bus.async_fire.call_args[0]
        assert args[0] == EVENT_MIXED_SCHEDULE
        assert "schedule_pct" in args[1]
        assert "app_pct" in args[1]

    @pytest.mark.asyncio
    async def test_no_event_below_10_records(self):
        store = MissionStore()
        for i in range(5):
            store._records.append(_record(initiator="schedule", days_ago=i+1))
        for i in range(4):
            store._records.append(_record(initiator="rmtApp", days_ago=i+6))
        entry = _make_entry(mission_store=store)
        hass = _make_hass()
        await async_check_mixed_schedule(hass, entry)
        hass.bus.async_fire.assert_not_called()


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
    async def test_enrichment_fires_event(self):
        from custom_components.roomba_plus.const import EVENT_MAP_DRIFT_DETECTED
        entry = _make_entry()
        hass = _make_hass()
        await async_enrich_drift_issue(hass, entry, dx=100.0, dy=0.0)
        hass.bus.async_fire.assert_called_once()
        event_type, data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_MAP_DRIFT_DETECTED
        assert "bearing" in data
        assert "magnitude_cm" in data

    @pytest.mark.asyncio
    async def test_bearing_east_for_positive_dx(self):
        entry = _make_entry()
        hass = _make_hass()
        await async_enrich_drift_issue(hass, entry, dx=1000.0, dy=0.0)
        data = hass.bus.async_fire.call_args[0][1]
        assert 80 <= data["bearing"] <= 100  # approximately east

    @pytest.mark.asyncio
    async def test_magnitude_calculated_correctly(self):
        entry = _make_entry()
        hass = _make_hass()
        # dx=300mm, dy=400mm → 500mm → 50cm
        await async_enrich_drift_issue(hass, entry, dx=300.0, dy=400.0)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["magnitude_cm"] == 50.0


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
    async def test_below_threshold_no_event(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        ms    = self._ms_with_errors(15, 2)
        entry = self._entry(ms)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_threshold_fires_event(self):
        from custom_components.roomba_plus.const import EVENT_ERROR_RECURRENCE
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        ms    = self._ms_with_errors(15, 3)
        entry = self._entry(ms)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        hass.bus.async_fire.assert_called_once()
        event_type, data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_ERROR_RECURRENCE
        assert data["count"] == 3
        assert data["error_code"] == 15

    @pytest.mark.asyncio
    async def test_most_frequent_code_chosen(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        from custom_components.roomba_plus.mission_store import MissionStore
        from homeassistant.util import dt as dt_util
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
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["error_code"] == 15

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
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["room"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_room_name_unknown_without_aligner(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        ms = self._ms_with_errors(15, 3)
        entry = self._entry(ms, aligner=None)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["room"] == "unknown location"


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
        archive = self._archive_with_pause_ids(216, 3)
        # A conflicting local MissionStore that must be ignored — if it were
        # used instead, count/error_code below would differ.
        ms = TestErrorRecurrence()._ms_with_errors(99, 5)
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["error_code"] == 216
        assert data["count"] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_archive_empty(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        from custom_components.roomba_plus.mission_archive import MissionArchive
        archive = MissionArchive()  # record_count == 0
        ms = TestErrorRecurrence()._ms_with_errors(15, 3)
        entry = self._entry(ms=ms, archive=archive)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["error_code"] == 15

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_no_archive(self):
        from custom_components.roomba_plus.repairs import async_check_error_recurrence
        ms = TestErrorRecurrence()._ms_with_errors(15, 3)
        entry = self._entry(ms=ms, archive=None)
        hass  = MagicMock()
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["error_code"] == 15

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
        await async_check_error_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["error_code"] == 15


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
    async def test_below_threshold_no_event(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        archive = self._archive_with_results(["cancelled", "cancelled"])
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        await async_check_cancellation_recurrence(hass, entry)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_threshold_fires_event_with_breakdown(self):
        from custom_components.roomba_plus.const import EVENT_CANCELLATION_RECURRENCE
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        archive = self._archive_with_results(
            ["cancelled_by_user", "cancelled_by_user", "cancelled"]
        )
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        await async_check_cancellation_recurrence(hass, entry)
        hass.bus.async_fire.assert_called_once()
        event_type, data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_CANCELLATION_RECURRENCE
        assert data["count"] == 3
        assert data["by_user_count"] == 2
        assert data["other_count"] == 1

    @pytest.mark.asyncio
    async def test_completed_results_not_counted(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        archive = self._archive_with_results(
            ["completed", "completed", "stuck_and_resumed", "error_17"]
        )
        entry = self._entry(archive=archive)
        hass  = MagicMock()
        await async_check_cancellation_recurrence(hass, entry)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_no_archive(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        ms = self._ms_with_results(["cancelled", "cancelled", "cancelled_by_user"])
        entry = self._entry(ms=ms, archive=None)
        hass  = MagicMock()
        await async_check_cancellation_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["count"] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_mission_store_when_archive_has_only_old_records(self):
        """v2.8.2 bug-hunt fix — same rationale as the matching test on
        async_check_error_recurrence: an archive with months of old history
        but nothing in the trailing 30 days must not be treated as 'no
        failures' when the local MissionStore has a real recent pattern."""
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
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
        await async_check_cancellation_recurrence(hass, entry)
        data = hass.bus.async_fire.call_args[0][1]
        assert data["count"] == 3

    @pytest.mark.asyncio
    async def test_no_store_and_no_archive_no_crash(self):
        from custom_components.roomba_plus.repairs import async_check_cancellation_recurrence
        entry = self._entry(ms=None, archive=None)
        await async_check_cancellation_recurrence(MagicMock(), entry)  # no exception


class TestSmberrMergedIntoContactRepairs:
    """v3.5.0 Repairs redesign — the standalone smberr_high Repair was
    merged into async_check_battery_contact_issue and
    async_check_dock_health as a confidence input (via the shared
    _smberr_elevated() helper — see tests/test_edge_cases.py::
    TestSmberrTypeSafety for its own type-safety coverage), rather than
    just deleted. These tests verify the merge actually changes behavior
    in both consumers, not just that the helper itself works standalone.
    """

    @pytest.mark.asyncio
    async def test_dock_health_thresholds_halve_when_smberr_elevated(self):
        """A chatters count that would NOT fire on its own (below the
        normal threshold) DOES fire once smberr is also elevated —
        corroborating evidence lowers the bar."""
        below_normal_threshold = _DOCK_CHATTERS_THRESHOLD // 2 + 5
        entry = _make_config_entry({
            "nChatters": below_normal_threshold,
            "nKnockoffs": 0,
            "nAborts": 0,
            "smberr": 50_432,  # i7+ field value — elevated
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_called_once()
        placeholders = mock_ir.async_create_issue.call_args[1]["translation_placeholders"]
        assert "SMBus errors" in placeholders["smberr_context"]

    @pytest.mark.asyncio
    async def test_dock_health_normal_thresholds_when_smberr_not_elevated(self):
        """The same below-normal-threshold chatters count does NOT fire
        without smberr corroboration — confirms the halving is genuinely
        conditional, not a silent, permanent threshold change."""
        below_normal_threshold = _DOCK_CHATTERS_THRESHOLD // 2 + 5
        entry = _make_config_entry({
            "nChatters": below_normal_threshold,
            "nKnockoffs": 0,
            "nAborts": 0,
            "smberr": 0,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_dock_health_context_empty_when_not_elevated(self):
        """smberr_context is an empty string (not a stray 'None'/'0') in
        the fired issue's placeholders when smberr isn't elevated."""
        entry = _make_config_entry({
            "nChatters": _DOCK_CHATTERS_THRESHOLD + 1,
            "nKnockoffs": 0,
            "nAborts": 0,
            "smberr": 0,
        })
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        placeholders = mock_ir.async_create_issue.call_args[1]["translation_placeholders"]
        assert placeholders["smberr_context"] == ""

    @pytest.mark.asyncio
    async def test_battery_contact_debounce_drops_to_one_when_smberr_elevated(self):
        """A single implausible batPct jump — normally not enough to fire
        (_CONTACT_ANOMALY_DEBOUNCE requires 2+ consecutive) — DOES fire
        when smberr is also elevated."""
        entry = _make_contact_entry(batpct=28.0)
        entry.runtime_data.roomba_reported_state.return_value = {
            "batPct": 28.0,
            "cleanMissionStatus": {"phase": ""},
            "bbchg": {"smberr": 50_432},
        }
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_battery_contact_issue(hass, entry)  # establish baseline=28
            entry.runtime_data.roomba_reported_state.return_value = {
                "batPct": 60.0,  # implausible single jump
                "cleanMissionStatus": {"phase": ""},
                "bbchg": {"smberr": 50_432},
            }
            await async_check_battery_contact_issue(hass, entry)
        mock_ir.async_create_issue.assert_called_once()


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
        entry.runtime_data.roomba_reported_state.return_value = {
            "bbchg3": {"estCap": 2488}
        }
        hass = MagicMock()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_dock_health(hass, entry)
        mock_ir.async_create_issue.assert_not_called()


# ── v2.8.3 — CLOUD-STALE repair issue tests ───────────────────────────────────

class TestCloudStaleEvent:
    """CLOUD-STALE (v2.8.3) — async_check_cloud_stale fires/re-arms
    correctly. v3.5.0: demoted from Repair Issue to event, split by cause
    (auth failures are left to HA's own native ConfigEntryAuthFailed
    reauth Repair — this check must not duplicate that)."""

    def _make_coordinator(self, last_success_offset_minutes: float | None,
                           last_exception=None, last_update_success=True):
        """Return a mock coordinator with last_success_time set relative to
        now, and last_exception/last_update_success explicitly set (defaults
        chosen so a bare call reads as 'healthy' — being explicit here
        avoids relying on MagicMock's auto-vivified truthiness, which would
        silently make the auth-failure gate never trigger in tests)."""
        from datetime import timezone, timedelta, datetime
        cc = MagicMock()
        cc.last_exception = last_exception
        cc.last_update_success = last_update_success
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
        """Event fires when last_success_time > CLOUD_STALE_MINUTES ago."""
        from custom_components.roomba_plus.const import (
            CLOUD_STALE_MINUTES, EVENT_CLOUD_STALE,
        )
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(CLOUD_STALE_MINUTES + 5)
        await async_check_cloud_stale(hass, entry, cc)
        hass.bus.async_fire.assert_called_once()
        event_type, data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_CLOUD_STALE
        assert data["minutes"] >= CLOUD_STALE_MINUTES

    @pytest.mark.asyncio
    async def test_no_event_when_fresh(self):
        """No event when last_success_time is within threshold."""
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(5)  # 5 min ago — well within 60 min threshold
        await async_check_cloud_stale(hass, entry, cc)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_event_when_no_success_yet(self):
        """No event when last_success_time is None (startup, no fetch yet)."""
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(None)
        await async_check_cloud_stale(hass, entry, cc)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_event_when_cause_is_auth_failure(self):
        """v3.5.0 — the actual point of the split: a sustained staleness
        caused by a CURRENT auth failure must NOT also fire this event.
        HA's own ConfigEntryAuthFailed-driven reauth Repair already covers
        it; firing here too would be two signals for one root cause."""
        from homeassistant.exceptions import ConfigEntryAuthFailed
        from custom_components.roomba_plus.const import CLOUD_STALE_MINUTES
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(
            CLOUD_STALE_MINUTES + 5,
            last_exception=ConfigEntryAuthFailed("bad credentials"),
            last_update_success=False,
        )
        await async_check_cloud_stale(hass, entry, cc)
        hass.bus.async_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_for_unrelated_staleness_after_auth_recovery(self):
        """Bug-hunt fix, found by checking HA's own DataUpdateCoordinator
        source rather than trusting the mock: last_exception is STICKY —
        HA never resets it to None on a later successful refresh. Naively
        checking 'is last_exception a ConfigEntryAuthFailed' alone would
        mean a single historical auth failure, resolved via reauth days
        ago, permanently suppresses this event for any later, unrelated
        genuine staleness. This is exactly that scenario: last_exception
        still holds an old ConfigEntryAuthFailed, but last_update_success
        is True (the coordinator has since recovered) — a NEW staleness
        must still fire normally."""
        from custom_components.roomba_plus.const import (
            CLOUD_STALE_MINUTES, EVENT_CLOUD_STALE,
        )
        from homeassistant.exceptions import ConfigEntryAuthFailed
        from custom_components.roomba_plus.repairs import async_check_cloud_stale
        hass = MagicMock()
        entry = self._make_entry()
        cc = self._make_coordinator(
            CLOUD_STALE_MINUTES + 5,
            last_exception=ConfigEntryAuthFailed("old, already-resolved failure"),
            last_update_success=True,  # coordinator has since recovered
        )
        await async_check_cloud_stale(hass, entry, cc)
        hass.bus.async_fire.assert_called_once()
        event_type, _data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_CLOUD_STALE


class TestComputeIntegrationHealth:
    """v2.9.0 (INTEG-HEALTH) — _compute_integration_health() scoring logic."""

    def _make_entry(self, entry_id="test_health_entry"):
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.runtime_data.last_mqtt_message_ts = 0.0
        entry.runtime_data.mission_archive = None
        entry.runtime_data.cloud_coordinator = None
        return entry

    def test_perfect_score_with_no_issues_and_no_data(self):
        """No active issues, no MQTT/ARC1 data at all (fresh install) —
        nothing to penalise, score stays at 100."""
        from custom_components.roomba_plus.sensor import _compute_integration_health

        hass = MagicMock()
        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, breakdown = _compute_integration_health(hass, self._make_entry())

        assert score == 100
        assert breakdown["active_issues"] == 0

    def test_active_issues_penalised_and_capped(self):
        from custom_components.roomba_plus.sensor import _compute_integration_health
        from custom_components.roomba_plus.const import DOMAIN

        entry = self._make_entry()
        hass = MagicMock()
        registry = MagicMock()

        def _issue(active=True):
            e = MagicMock()
            e.active = active
            return e

        # 4 active issues for this entry — would be -80 uncapped, but the
        # penalty caps at -60.
        registry.issues = {
            (DOMAIN, f"issue1_{entry.entry_id}"): _issue(),
            (DOMAIN, f"issue2_{entry.entry_id}"): _issue(),
            (DOMAIN, f"issue3_{entry.entry_id}"): _issue(),
            (DOMAIN, f"issue4_{entry.entry_id}"): _issue(),
            # A dismissed/inactive issue must not count.
            (DOMAIN, f"issue5_{entry.entry_id}"): _issue(active=False),
            # An issue for a DIFFERENT entry must not count.
            (DOMAIN, "issue6_some_other_entry"): _issue(),
        }
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, breakdown = _compute_integration_health(hass, entry)

        assert breakdown["active_issues"] == 4
        assert score == 40  # 100 - min(60, 4*20) = 100 - 60

    def test_stale_mqtt_penalised(self):
        from custom_components.roomba_plus.sensor import _compute_integration_health
        import time

        entry = self._make_entry()
        entry.runtime_data.last_mqtt_message_ts = (
            time.time() - 25 * 3600  # 25h ago — beyond the 24h threshold
        )
        hass = MagicMock()
        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, breakdown = _compute_integration_health(hass, entry)

        assert score == 80  # 100 - 20
        assert breakdown["mqtt_age_hours"] == pytest.approx(25.0, abs=0.1)

    def test_fresh_mqtt_not_penalised(self):
        from custom_components.roomba_plus.sensor import _compute_integration_health
        import time

        entry = self._make_entry()
        entry.runtime_data.last_mqtt_message_ts = time.time() - 600  # 10 min ago
        hass = MagicMock()
        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, _ = _compute_integration_health(hass, entry)

        assert score == 100

    def test_stale_arc1_penalised_only_when_cloud_enabled(self):
        from custom_components.roomba_plus.sensor import _compute_integration_health
        from homeassistant.util import dt as dt_util
        import datetime as _dt

        entry = self._make_entry()
        old_ts = (dt_util.utcnow() - _dt.timedelta(hours=72)).isoformat()
        archive = MagicMock()
        archive.record_count = 5
        archive.all_derived_oldest_first.return_value = [{"end_ts": old_ts}]
        entry.runtime_data.mission_archive = archive
        entry.runtime_data.cloud_coordinator = MagicMock()  # cloud enabled

        hass = MagicMock()
        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, breakdown = _compute_integration_health(hass, entry)

        assert score == 80  # 100 - 20
        assert breakdown["arc1_age_hours"] == pytest.approx(72.0, abs=0.2)

    def test_arc1_freshness_skipped_without_cloud(self):
        """No cloud coordinator configured — ARC1 freshness must not be
        evaluated at all (it's meaningless without cloud syncing)."""
        from custom_components.roomba_plus.sensor import _compute_integration_health
        from homeassistant.util import dt as dt_util
        import datetime as _dt

        entry = self._make_entry()
        old_ts = (dt_util.utcnow() - _dt.timedelta(hours=200)).isoformat()
        archive = MagicMock()
        archive.record_count = 5
        archive.all_derived_oldest_first.return_value = [{"end_ts": old_ts}]
        entry.runtime_data.mission_archive = archive
        entry.runtime_data.cloud_coordinator = None  # no cloud

        hass = MagicMock()
        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, breakdown = _compute_integration_health(hass, entry)

        assert score == 100
        assert breakdown["arc1_age_hours"] is None

    def test_score_floors_at_zero_not_negative(self):
        from custom_components.roomba_plus.sensor import _compute_integration_health
        from custom_components.roomba_plus.const import DOMAIN
        import time

        entry = self._make_entry()
        entry.runtime_data.last_mqtt_message_ts = time.time() - 30 * 3600
        hass = MagicMock()
        registry = MagicMock()

        def _issue():
            e = MagicMock()
            e.active = True
            return e

        registry.issues = {
            (DOMAIN, f"i{n}_{entry.entry_id}"): _issue() for n in range(5)
        }
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            score, _ = _compute_integration_health(hass, entry)

        assert score == 20  # 100 - 60 (capped) - 20 (mqtt) = 20, never negative


class TestHealthBand:
    """v2.9.0 EVENT-BUS — _health_band() pure classification helper."""

    def test_healthy_at_and_above_threshold(self):
        from custom_components.roomba_plus.sensor import _health_band
        assert _health_band(100) == "healthy"
        assert _health_band(80) == "healthy"

    def test_degraded_between_thresholds(self):
        from custom_components.roomba_plus.sensor import _health_band
        assert _health_band(79) == "degraded"
        assert _health_band(50) == "degraded"

    def test_critical_below_low_threshold(self):
        from custom_components.roomba_plus.sensor import _health_band
        assert _health_band(49) == "critical"
        assert _health_band(0) == "critical"


class TestHealthChangeEvent:
    """v2.9.0 EVENT-BUS — roomba_plus_health_change fires only on band
    crossing during the periodic tick, never on the first tick (no prior
    band to compare against), and never on a same-band score wobble.
    """

    def _make_sensor(self, hass):
        from custom_components.roomba_plus.sensor import RoombaIntegrationHealthSensor

        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.title = "Test Robot"
        sensor = RoombaIntegrationHealthSensor(MagicMock(), "blid123", entry)
        sensor.hass = hass
        sensor.schedule_update_ha_state = MagicMock()
        return sensor, entry

    def test_first_tick_seeds_band_without_firing(self):
        hass = MagicMock()
        sensor, _ = self._make_sensor(hass)
        with patch(
            "custom_components.roomba_plus.sensor_diagnostics._compute_integration_health",
            return_value=(100, {}),
        ):
            sensor._async_health_tick(None)

        hass.bus.async_fire.assert_not_called()
        assert sensor._last_health_band == "healthy"
        assert sensor._last_health_score == 100

    def test_fires_on_band_crossing(self):
        from custom_components.roomba_plus.const import EVENT_HEALTH_CHANGE

        hass = MagicMock()
        sensor, entry = self._make_sensor(hass)
        sensor._last_health_band = "healthy"
        sensor._last_health_score = 90

        with patch(
            "custom_components.roomba_plus.sensor_diagnostics._compute_integration_health",
            return_value=(40, {}),
        ):
            sensor._async_health_tick(None)

        hass.bus.async_fire.assert_called_once_with(
            EVENT_HEALTH_CHANGE,
            {
                "entry_id": "test_entry",
                "name": "Test Robot",
                "score": 40,
                "previous_score": 90,
                "band": "critical",
                "previous_band": "healthy",
            },
        )
        assert sensor._last_health_band == "critical"

    def test_no_event_on_same_band_score_wobble(self):
        hass = MagicMock()
        sensor, _ = self._make_sensor(hass)
        sensor._last_health_band = "healthy"
        sensor._last_health_score = 100

        with patch(
            "custom_components.roomba_plus.sensor_diagnostics._compute_integration_health",
            return_value=(85, {}),  # still "healthy" band, minor jitter
        ):
            sensor._async_health_tick(None)

        hass.bus.async_fire.assert_not_called()
        # Score is still tracked even without a band crossing, so the next
        # genuine crossing reports an accurate previous_score.
        assert sensor._last_health_score == 85


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


class TestMapRetrainWorkflow:
    """v2.9.0 MAP-RETRAIN-WF — async_check_map_retrain_workflow() escalation.

    Tracks notReady&64 ("Smart Map updating") duration via repairs.py's
    in-memory _map_updating_since dict — same pattern as
    _health_low_since (INTEG-HEALTH).

    v3.5.0 Repairs redesign: stage 2 (WARN threshold) now fires the
    map_retrain_in_progress event instead of a Repair Issue — longer than a
    typical retrain is worth a Logbook entry, not yet a must-act problem.
    Stage 3 (STUCK threshold) remains a Repair Issue — genuinely stuck is
    Gate-C actionable. roomba_plus_map_retrain_started/completed already
    exist (cloud pmapv_id-driven) and are what TRIGGER+ listens to; this
    tracks a different signal (the live notReady bit), so firing an event
    at stage 2 doesn't duplicate that automation-facing signal.
    """

    def setup_method(self):
        from custom_components.roomba_plus import repairs as repairs_mod
        repairs_mod._map_updating_since.clear()

    def _make_entry(self, entry_id="test_map_entry"):
        entry = MagicMock()
        entry.entry_id = entry_id
        return entry

    def test_not_updating_clears_issue_and_state(self):
        from custom_components.roomba_plus.repairs import async_check_map_retrain_workflow
        from custom_components.roomba_plus import repairs as repairs_mod

        entry = self._make_entry()
        hass = MagicMock()
        repairs_mod._map_updating_since[entry.entry_id] = 12345.0
        repairs_mod._event_armed[f"{entry.entry_id}_map_retrain_in_progress"] = True

        with patch("custom_components.roomba_plus.repairs.ir.async_delete_issue") as mock_delete:
            async_check_map_retrain_workflow(hass, entry, False)

        mock_delete.assert_called_once_with(
            hass, "roomba_plus", f"map_retrain_workflow_{entry.entry_id}"
        )
        assert entry.entry_id not in repairs_mod._map_updating_since
        assert f"{entry.entry_id}_map_retrain_in_progress" not in repairs_mod._event_armed

    def test_just_started_no_issue_yet(self):
        """Stage 1 — map_updating just turned True. No issue fires
        immediately; brief updates are normal."""
        from custom_components.roomba_plus.repairs import async_check_map_retrain_workflow

        entry = self._make_entry()
        hass = MagicMock()

        with patch("custom_components.roomba_plus.repairs.ir.async_create_issue") as mock_create:
            async_check_map_retrain_workflow(hass, entry, True)

        mock_create.assert_not_called()

    def test_warn_stage_after_threshold(self):
        from custom_components.roomba_plus.repairs import async_check_map_retrain_workflow
        from custom_components.roomba_plus import repairs as repairs_mod
        from custom_components.roomba_plus.const import (
            EVENT_MAP_RETRAIN_IN_PROGRESS, MAP_RETRAIN_WARN_MINUTES,
        )

        from homeassistant.util import dt as dt_util
        entry = self._make_entry()
        hass = MagicMock()
        now = dt_util.utcnow().timestamp()
        repairs_mod._map_updating_since[entry.entry_id] = (
            now - (MAP_RETRAIN_WARN_MINUTES + 1) * 60
        )

        async_check_map_retrain_workflow(hass, entry, True)

        hass.bus.async_fire.assert_called_once()
        event_type, data = hass.bus.async_fire.call_args[0]
        assert event_type == EVENT_MAP_RETRAIN_IN_PROGRESS
        assert data["minutes"] >= MAP_RETRAIN_WARN_MINUTES

    def test_stuck_stage_after_longer_threshold(self):
        from custom_components.roomba_plus.repairs import async_check_map_retrain_workflow
        from custom_components.roomba_plus import repairs as repairs_mod
        from custom_components.roomba_plus.const import MAP_RETRAIN_STUCK_MINUTES

        from homeassistant.util import dt as dt_util
        entry = self._make_entry()
        hass = MagicMock()
        now = dt_util.utcnow().timestamp()
        repairs_mod._map_updating_since[entry.entry_id] = (
            now - (MAP_RETRAIN_STUCK_MINUTES + 1) * 60
        )

        with patch("custom_components.roomba_plus.repairs.ir.async_create_issue") as mock_create:
            async_check_map_retrain_workflow(hass, entry, True)

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        from homeassistant.helpers import issue_registry as ir
        assert kwargs["severity"] == ir.IssueSeverity.ERROR
        assert kwargs["translation_key"] == "map_retrain_stuck"

    def test_stuck_stage_does_not_fire_any_bus_event(self):
        """v3.5.0: only the WARN stage (map_retrain_in_progress) fires an
        event now — the STUCK stage remains a pure Repair Issue, since it's
        Gate-C actionable (check the robot, consider a manual restart)."""
        from custom_components.roomba_plus.repairs import async_check_map_retrain_workflow
        from custom_components.roomba_plus import repairs as repairs_mod
        from custom_components.roomba_plus.const import MAP_RETRAIN_STUCK_MINUTES

        from homeassistant.util import dt as dt_util
        entry = self._make_entry()
        hass = MagicMock()
        now = dt_util.utcnow().timestamp()
        repairs_mod._map_updating_since[entry.entry_id] = (
            now - (MAP_RETRAIN_STUCK_MINUTES + 1) * 60
        )

        async_check_map_retrain_workflow(hass, entry, True)

        hass.bus.async_fire.assert_not_called()


class TestMaintenanceDueRepairIssue:
    """v2.9.0 — async_check_maintenance_due() sustained-grace-period
    backstop for users without automations on the maintenance_due trigger.
    """

    def setup_method(self):
        from custom_components.roomba_plus import repairs as repairs_mod
        repairs_mod._maintenance_due_since.clear()

    def _make_entry(self, entry_id="test_maint_entry"):
        entry = MagicMock()
        entry.entry_id = entry_id
        return entry

    def test_nothing_due_clears_issue_and_state(self):
        from custom_components.roomba_plus.repairs import async_check_maintenance_due
        from custom_components.roomba_plus import repairs as repairs_mod

        entry = self._make_entry()
        hass = MagicMock()
        repairs_mod._maintenance_due_since[entry.entry_id] = 12345.0

        with patch("custom_components.roomba_plus.repairs.ir.async_delete_issue") as mock_delete:
            async_check_maintenance_due(hass, entry, [])

        mock_delete.assert_called_once_with(
            hass, "roomba_plus", f"maintenance_due_{entry.entry_id}"
        )
        assert entry.entry_id not in repairs_mod._maintenance_due_since

    def test_within_grace_period_no_issue_yet(self):
        from custom_components.roomba_plus.repairs import async_check_maintenance_due

        entry = self._make_entry()
        hass = MagicMock()

        with patch("custom_components.roomba_plus.repairs.ir.async_create_issue") as mock_create:
            async_check_maintenance_due(hass, entry, ["filter"])

        mock_create.assert_not_called()

    def test_fires_after_grace_period(self):
        from custom_components.roomba_plus.repairs import async_check_maintenance_due
        from custom_components.roomba_plus import repairs as repairs_mod
        from custom_components.roomba_plus.const import MAINTENANCE_DUE_GRACE_DAYS
        from homeassistant.util import dt as dt_util
        from homeassistant.helpers import issue_registry as ir

        entry = self._make_entry()
        hass = MagicMock()
        now = dt_util.utcnow().timestamp()
        repairs_mod._maintenance_due_since[entry.entry_id] = (
            now - (MAINTENANCE_DUE_GRACE_DAYS + 1) * 86400
        )

        with patch("custom_components.roomba_plus.repairs.ir.async_create_issue") as mock_create:
            async_check_maintenance_due(hass, entry, ["filter", "brush"])

        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        assert mock_create.call_args[0][2] == f"maintenance_due_{entry.entry_id}"
        assert kwargs["severity"] == ir.IssueSeverity.WARNING
        assert kwargs["translation_key"] == "maintenance_due"
        assert kwargs["translation_placeholders"] == {"items": "filter, brush"}

    def test_does_not_restart_timer_when_due_set_changes(self):
        """Resetting ONE overdue consumable while another remains due must
        not restart the grace-period timer (documented coarse tradeoff)."""
        from custom_components.roomba_plus.repairs import async_check_maintenance_due
        from custom_components.roomba_plus import repairs as repairs_mod
        from homeassistant.util import dt as dt_util

        entry = self._make_entry()
        hass = MagicMock()
        original_since = dt_util.utcnow().timestamp() - 1000
        repairs_mod._maintenance_due_since[entry.entry_id] = original_since

        async_check_maintenance_due(hass, entry, ["brush"])  # filter was reset, brush remains

        assert repairs_mod._maintenance_due_since[entry.entry_id] == original_since

    def test_does_not_fire_any_bus_event(self):
        """Triggers and Repairs must not be redundant."""
        from custom_components.roomba_plus.repairs import async_check_maintenance_due
        from custom_components.roomba_plus import repairs as repairs_mod
        from custom_components.roomba_plus.const import MAINTENANCE_DUE_GRACE_DAYS
        from homeassistant.util import dt as dt_util

        entry = self._make_entry()
        hass = MagicMock()
        now = dt_util.utcnow().timestamp()
        repairs_mod._maintenance_due_since[entry.entry_id] = (
            now - (MAINTENANCE_DUE_GRACE_DAYS + 1) * 86400
        )

        async_check_maintenance_due(hass, entry, ["filter"])

        hass.bus.async_fire.assert_not_called()


class TestMakeMapUpdatingCallback:
    """v2.9.0 MAP-RETRAIN-WF — make_map_updating_callback() MQTT factory."""

    def _msg(self, not_ready: int) -> dict:
        return {
            "state": {
                "reported": {
                    "cleanMissionStatus": {"notReady": not_ready},
                }
            }
        }

    def test_extracts_bit_64_and_schedules_check(self):
        from custom_components.roomba_plus.callbacks import make_map_updating_callback

        hass = MagicMock()
        entry = MagicMock()
        cb = make_map_updating_callback(hass, entry)

        cb(self._msg(64))  # bit 64 set

        assert hass.loop.call_soon_threadsafe.call_count == 1
        args = hass.loop.call_soon_threadsafe.call_args[0]
        assert args[1] is hass
        assert args[2] is entry
        assert args[3] is True

    def test_other_bits_without_64_means_not_updating(self):
        from custom_components.roomba_plus.callbacks import make_map_updating_callback

        hass = MagicMock()
        entry = MagicMock()
        cb = make_map_updating_callback(hass, entry)

        cb(self._msg(34))  # "Not ready" but NOT the map-updating bit

        args = hass.loop.call_soon_threadsafe.call_args[0]
        assert args[3] is False

    def test_no_clean_mission_status_is_noop(self):
        from custom_components.roomba_plus.callbacks import make_map_updating_callback

        hass = MagicMock()
        entry = MagicMock()
        cb = make_map_updating_callback(hass, entry)

        cb({"state": {"reported": {}}})

        hass.loop.call_soon_threadsafe.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_repairs_parser.py (TEST-REORG, v2.9.1) — SmartZoneNamingRepairFlow
# zone-name parser unit tests. Original module docstring:
#
#   Unit tests for the SmartZoneNamingRepairFlow zone name parser.
#   Covers both input styles supported after v1.4.4.9:
#     - Newline-separated  (canonical, one entry per line)
#     - Comma-separated    (fallback for when the textarea collapses to one line)
#   Also tests edge cases: mixed whitespace, duplicate IDs, names containing
#   commas, names containing equals signs, unknown IDs, and empty input.
# ═══════════════════════════════════════════════════════════════════════

# ── Extract the parser logic as a standalone helper for unit testing ──────────
# Rather than instantiating the full RepairsFlow (which needs HA internals),
# we extract the exact parsing algorithm from repairs.py into a function that
# takes (raw, unlabelled) and returns the parsed dict.  Any change to the
# production parser must be reflected here.

def _parse_zone_input(raw: str, unlabelled: list[str]) -> dict[str, str]:
    """Mirror of the parser in SmartZoneNamingRepairFlow.async_step_init."""
    parsed: dict[str, str] = {}
    raw = raw.strip()
    if not raw:
        return parsed

    _comma_delim = re.compile(r",\s*\d")
    if _comma_delim.search(raw):
        tokens = re.split(r",(?=\s*\d)", raw)
    else:
        tokens = raw.splitlines()

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            continue
        rid_part, _, name_part = token.partition("=")
        rid = rid_part.strip()
        name = name_part.strip()
        if rid in unlabelled and name:
            parsed[rid] = name

    return parsed


# ── Shared fixtures ───────────────────────────────────────────────────────────

UNLABELLED = ["1", "4", "17", "19", "23", "25", "26"]


# ── Newline-separated input ───────────────────────────────────────────────────

class TestNewlineSeparated:
    """Canonical format: one id=Name per line."""

    def test_single_zone(self):
        raw = "1=Cucina"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Cucina"}

    def test_all_zones(self):
        raw = (
            "1=Cucina\n"
            "17=CabinaArmadio\n"
            "19=Bagno\n"
            "23=BagnoStudio\n"
            "26=Soggiorno\n"
            "4=Studio\n"
            "25=Camera"
        )
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {
            "1": "Cucina",
            "17": "CabinaArmadio",
            "19": "Bagno",
            "23": "BagnoStudio",
            "26": "Soggiorno",
            "4": "Studio",
            "25": "Camera",
        }

    def test_trailing_newline_ignored(self):
        raw = "1=Cucina\n17=Armadio\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert len(result) == 2

    def test_blank_lines_skipped(self):
        raw = "1=Cucina\n\n17=Armadio\n\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_leading_trailing_whitespace_stripped(self):
        raw = "  1 = Cucina  \n  17 = Armadio  "
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_empty_name_after_equals_skipped(self):
        """A zone with id= but no name is silently skipped."""
        raw = "1=\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" not in result
        assert result["17"] == "Armadio"

    def test_unknown_id_ignored(self):
        """An id not in unlabelled is silently dropped."""
        raw = "1=Cucina\n99=Unknown"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "99" not in result
        assert result["1"] == "Cucina"

    def test_name_with_spaces(self):
        raw = "1=Camera da Letto"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Camera da Letto"}

    def test_name_with_equals_sign(self):
        """Only the first '=' is treated as the delimiter."""
        raw = "1=Room=A"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Room=A"

    def test_malformed_line_no_equals_skipped(self):
        raw = "1=Cucina\nJustText\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" in result
        assert "17" in result
        assert len(result) == 2


# ── Comma-separated input ─────────────────────────────────────────────────────

class TestCommaSeparated:
    """Fallback format: id=Name,id=Name on a single line."""

    def test_single_zone(self):
        raw = "1=Cucina"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Cucina"}

    def test_all_zones_comma_separated(self):
        """This is the exact input from the bug report."""
        raw = "1=Cucina,17=Cabina_Armadio,19=Bagno,23=Bagno_Studio,26=Soggiorno,4=Studio,25=Camera_da_Letto"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {
            "1": "Cucina",
            "17": "Cabina_Armadio",
            "19": "Bagno",
            "23": "Bagno_Studio",
            "26": "Soggiorno",
            "4": "Studio",
            "25": "Camera_da_Letto",
        }

    def test_spaces_around_commas(self):
        raw = "1=Cucina, 17=Armadio, 19=Bagno"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}

    def test_unknown_id_in_comma_list_ignored(self):
        raw = "1=Cucina,99=Unknown,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "99" not in result
        assert result["1"] == "Cucina"
        assert result["17"] == "Armadio"

    def test_empty_name_in_comma_list_skipped(self):
        raw = "1=,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" not in result
        assert result["17"] == "Armadio"

    def test_name_with_spaces_comma_list(self):
        raw = "1=Camera da Letto,17=Sala da Pranzo"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Camera da Letto"
        assert result["17"] == "Sala da Pranzo"

    def test_two_zones_no_trailing_comma(self):
        raw = "1=Cucina,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert len(result) == 2


# ── Ambiguous / mixed input ───────────────────────────────────────────────────

class TestAmbiguousInput:
    """Cases where comma-in-name vs comma-as-delimiter could be confused."""

    def test_name_containing_comma_without_digit_after(self):
        """'1=Living room, open plan' — comma not followed by digit, so no split."""
        raw = "1=Living room, open plan\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Living room, open plan"
        assert result["17"] == "Armadio"

    def test_comma_followed_by_non_digit_treated_as_name(self):
        """Comma not followed by a digit should not trigger comma-splitting."""
        raw = "1=Office, West wing\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Office, West wing"

    def test_comma_delimiter_detected_by_digit_lookahead(self):
        """Comma followed immediately by a digit is the delimiter signal."""
        raw = "1=Cucina,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        # Correctly split into two entries
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_mixed_newline_and_comma_newline_wins(self):
        """If there are real newlines but no comma-before-digit, use newlines."""
        raw = "1=Cucina\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Boundary and error-tolerance tests."""

    def test_empty_input_returns_empty(self):
        assert _parse_zone_input("", UNLABELLED) == {}

    def test_whitespace_only_returns_empty(self):
        assert _parse_zone_input("   \n  \n  ", UNLABELLED) == {}

    def test_all_ids_left_blank(self):
        """Pre-filled default text with no names filled in."""
        raw = "1=\n17=\n19=\n23=\n25=\n26=\n4="
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {}

    def test_partial_fill(self):
        """Only some zones named — the rest are left blank."""
        raw = "1=Cucina\n17=\n19=Bagno"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "19": "Bagno"}
        assert "17" not in result

    def test_duplicate_id_last_value_wins(self):
        """If the same id appears twice, the last non-empty name wins."""
        raw = "1=First\n1=Second"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Second"

    def test_single_unlabelled(self):
        result = _parse_zone_input("5=Kitchen", ["5"])
        assert result == {"5": "Kitchen"}

    def test_unicode_names(self):
        raw = "1=Küche\n17=Wohnzimmer\n19=Schlafzimmer"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Küche"

    def test_default_prefill_newline_format_parses_correctly(self):
        """Simulate the pre-filled textarea after the user fills in names."""
        unlabelled = ["1", "17", "19"]
        prefilled = "\n".join(f"{rid}=" for rid in unlabelled)
        # User fills in names:
        filled = prefilled.replace("1=", "1=Cucina").replace("17=", "17=Armadio").replace("19=", "19=Bagno")
        result = _parse_zone_input(filled, unlabelled)
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}


# ── Regression: the exact bug report scenario ─────────────────────────────────

class TestBugReportScenario:
    """Exact inputs from the bug report must now parse correctly."""

    def test_bug_report_comma_input(self):
        """v1.4.4.9 incorrectly stored the entire value under zone 1.
        This must now produce seven separate zone entries.
        """
        raw = "1=Cucina,17=Cabina_Armadio,19=Bagno,23=Bagno_Studio,26=Soggiorno,4=Studio,25=Camera_da_Letto"
        unlabelled = ["1", "17", "19", "23", "26", "4", "25"]
        result = _parse_zone_input(raw, unlabelled)

        # Must NOT store everything under zone 1
        assert result.get("1") == "Cucina", (
            "Zone 1 must be 'Cucina', not the entire comma-separated string"
        )
        # Must produce all 7 zones
        assert len(result) == 7

    def test_original_single_zone_still_works(self):
        """The fix that landed in v1.4.4.9 (single zone, newline) must still work."""
        raw = "1=Cucina\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina"}

    def test_concatenated_prefill_was_the_root_cause(self):
        """When all IDs appear on one line with no names (the buggy pre-fill),
        entering comma-separated values should now parse correctly.
        """
        # Buggy pre-fill rendered as: "1=17=19=23=26=4=25="
        # User then types comma-separated names for everything:
        raw = "1=Cucina,17=Armadio,19=Bagno"
        result = _parse_zone_input(raw, ["1", "17", "19"])
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}



# ─────────────────────────────────────────────────────────────────────────────
# PLAIN-STATUS (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegrationHealthPlainStatus:
    """PLAIN-STATUS (v3.1.0) — status_text/recommendation on integration_health."""

    def _make_entry(self, entry_id="test_plain_status_entry"):
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.runtime_data.last_mqtt_message_ts = 0.0
        entry.runtime_data.mission_archive = None
        entry.runtime_data.cloud_coordinator = None
        return entry

    def _hass(self, lang="en"):
        hass = MagicMock()
        hass.config.language = lang
        return hass

    def test_healthy_status_text_no_recommendation(self):
        """No issues, no stale data → healthy status_text, recommendation=None."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("en")
        breakdown = {"active_issues": 0, "mqtt_age_hours": None, "arc1_age_hours": None}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert text == "Everything is fine"
        assert rec is None

    def test_active_issues_status_text_and_recommendation(self):
        """Active issues → status_text mentions count, recommendation present."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("en")
        breakdown = {"active_issues": 3, "mqtt_age_hours": None, "arc1_age_hours": None}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert "3" in text
        assert rec is not None
        assert "Repairs" in rec

    def test_mqtt_stale_status_text(self):
        """Stale MQTT (no active issues) → MQTT-specific status_text."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("en")
        breakdown = {"active_issues": 0, "mqtt_age_hours": 30.0, "arc1_age_hours": None}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert "WiFi" in text or "WiFi" in rec

    def test_arc1_stale_status_text_includes_hours(self):
        """Stale ARC1 (no issues, MQTT fresh) → cloud-specific status_text with hours."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("en")
        breakdown = {"active_issues": 0, "mqtt_age_hours": 1.0, "arc1_age_hours": 60.0}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert "60" in text
        assert rec is not None

    def test_active_issues_takes_priority_over_stale_signals(self):
        """When multiple conditions apply, active_issues (strongest) wins."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("en")
        breakdown = {"active_issues": 1, "mqtt_age_hours": 30.0, "arc1_age_hours": 60.0}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert "1" in text  # active_issues text, not MQTT/ARC1 text

    def test_all_seven_languages_produce_non_empty_healthy_text(self):
        """Every supported language must have a non-empty healthy status_text."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        breakdown = {"active_issues": 0, "mqtt_age_hours": None, "arc1_age_hours": None}
        for lang in ("en", "de", "fr", "it", "es", "nl", "pt"):
            hass = self._hass(lang)
            text, rec = _integration_health_plain_status(hass, breakdown)
            assert text, f"{lang}: empty healthy status_text"
            assert rec is None

    def test_unsupported_language_falls_back_to_english(self):
        """A language not in the table falls back to English."""
        from custom_components.roomba_plus.sensor import _integration_health_plain_status
        hass = self._hass("ja")  # unsupported
        breakdown = {"active_issues": 0, "mqtt_age_hours": None, "arc1_age_hours": None}
        text, rec = _integration_health_plain_status(hass, breakdown)
        assert text == "Everything is fine"

    def test_extra_state_attributes_includes_plain_status_fields(self):
        """RoombaIntegrationHealthSensor.extra_state_attributes carries
        status_text/recommendation alongside the existing breakdown fields.
        """
        from custom_components.roomba_plus.sensor import RoombaIntegrationHealthSensor
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": {}}}
        entry = self._make_entry()
        sensor = RoombaIntegrationHealthSensor.__new__(RoombaIntegrationHealthSensor)
        sensor._roomba = roomba
        sensor._blid = "test_blid"
        sensor._entry = entry
        sensor.hass = self._hass("en")

        registry = MagicMock()
        registry.issues = {}
        with patch(
            "custom_components.roomba_plus.sensor.ir.async_get",
            return_value=registry,
        ):
            attrs = sensor.extra_state_attributes

        assert "status_text" in attrs
        assert "recommendation" in attrs
        assert attrs["active_issues"] == 0  # original breakdown field preserved


class TestRobotHealthPlainStatus:
    """PLAIN-STATUS (v3.1.0) — status_text/recommendation on robot_health_score."""

    def _hass(self, lang="en"):
        hass = MagicMock()
        hass.config.language = lang
        return hass

    def test_good_condition_when_weakest_signal_none(self):
        """weakest_signal=None (all signals >= 60) → good-condition text."""
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": None}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert text == "Robot is in good condition"
        assert rec is None

    def test_battery_retention_weakest_signal(self):
        """weakest_signal=battery_retention → battery-specific text + recommendation."""
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "battery_retention"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert "Battery" in text
        assert "battery" in rec.lower()

    def test_nav_efficiency_weakest_signal(self):
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "nav_efficiency"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert "Navigation" in text
        assert "Smart Map" in rec

    def test_cleaning_speed_trend_weakest_signal(self):
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "cleaning_speed_trend"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert "Cleaning time" in text
        assert "brushes" in rec.lower() or "filter" in rec.lower()

    def test_anomaly_rate_weakest_signal(self):
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "anomaly_rate"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert "unusual" in text.lower()
        assert "history" in rec.lower()

    def test_stuck_rate_weakest_signal(self):
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "stuck_rate"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert "stuck" in text.lower()
        assert "obstacles" in rec.lower()

    def test_all_seven_languages_produce_non_empty_text_for_each_signal(self):
        """Every (language, signal) combination must have non-empty text/rec."""
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        signals = (
            "battery_retention", "nav_efficiency", "cleaning_speed_trend",
            "anomaly_rate", "stuck_rate",
        )
        for lang in ("en", "de", "fr", "it", "es", "nl", "pt"):
            hass = self._hass(lang)
            for signal in signals:
                breakdown = {"weakest_signal": signal}
                text, rec = _robot_health_plain_status(hass, breakdown)
                assert text, f"{lang}/{signal}: empty status_text"
                assert rec, f"{lang}/{signal}: empty recommendation"

    def test_unknown_signal_name_falls_back_to_good_condition(self):
        """A weakest_signal value not in the mapping table is treated as
        'no clear weak signal' rather than crashing.
        """
        from custom_components.roomba_plus.sensor import _robot_health_plain_status
        hass = self._hass("en")
        breakdown = {"weakest_signal": "some_future_signal_not_yet_mapped"}
        text, rec = _robot_health_plain_status(hass, breakdown)
        assert text == "Robot is in good condition"
        assert rec is None


class TestFavoriteMultiCommandDefs:
    """v3.5.0 FAVORITE-FIX (issue #9 follow-up)."""

    @pytest.mark.asyncio
    async def test_no_issue_for_single_commanddef(self):
        from custom_components.roomba_plus.const import DOMAIN
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_favorite_multi_command(
                hass, "fav1", "Vacuum Everywhere", 1
            )
            mock_ir.async_create_issue.assert_not_called()
            mock_ir.async_delete_issue.assert_called_once_with(
                hass, DOMAIN, "favorite_multi_commanddefs_fav1"
            )

    @pytest.mark.asyncio
    async def test_no_issue_for_zero_commanddefs(self):
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_favorite_multi_command(hass, "fav2", "Empty", 0)
            mock_ir.async_create_issue.assert_not_called()
            mock_ir.async_delete_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_issue_fires_for_multiple_commanddefs(self):
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_favorite_multi_command(
                hass, "fav3", "Mystery Routine", 3
            )
            mock_ir.async_create_issue.assert_called_once()
            args, kwargs = mock_ir.async_create_issue.call_args
            assert args[2] == "favorite_multi_commanddefs_fav3"
            assert kwargs["translation_key"] == "favorite_multi_commanddefs"
            assert kwargs["translation_placeholders"] == {
                "name": "Mystery Routine",
                "count": "3",
            }
            assert kwargs["is_fixable"] is False

    @pytest.mark.asyncio
    async def test_issue_cleared_when_favorite_edited_down(self):
        from custom_components.roomba_plus.const import DOMAIN
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_favorite_multi_command(hass, "fav4", "Was Broken", 2)
            mock_ir.async_create_issue.assert_called_once()
            mock_ir.reset_mock()
            await async_check_favorite_multi_command(hass, "fav4", "Was Broken", 1)
            mock_ir.async_create_issue.assert_not_called()
            mock_ir.async_delete_issue.assert_called_once_with(
                hass, DOMAIN, "favorite_multi_commanddefs_fav4"
            )


class TestCleanupRemovedRepairs:
    """v3.5.0 Repairs redesign bug-hunt fix — async_cleanup_removed_repairs.

    Unit tests elsewhere in this file mock issue_registry entirely, so they
    can never catch this class of bug: a Repair Issue that was ACTIVE in a
    user's real registry before upgrading, which nothing in the new code
    would ever create OR delete again, left permanently stuck. These tests
    exercise the actual filtering logic against a registry-shaped fake
    (keyed the same way real IssueRegistry.issues is:
    dict[(domain, issue_id), entry-with-translation_key]) rather than
    mocking the whole thing away.
    """

    def _fake_registry(self, issues: dict):
        """issues: {(domain, issue_id): translation_key}"""
        reg = MagicMock()
        reg.issues = {
            key: MagicMock(translation_key=tk) for key, tk in issues.items()
        }
        return reg

    @pytest.mark.asyncio
    async def test_deletes_only_removed_translation_keys_for_our_domain(self):
        from custom_components.roomba_plus.const import DOMAIN
        from custom_components.roomba_plus.repairs import (
            async_cleanup_removed_repairs,
        )
        hass = _make_hass()
        reg = self._fake_registry({
            (DOMAIN, "performance_degradation"): "performance_degradation",  # removed
            (DOMAIN, "smberr_high_e1"): "smberr_high",  # removed
            (DOMAIN, "stuck_hotspot_12_7"): "stuck_hotspot_detected",  # removed, dynamic id
            (DOMAIN, "maintenance_due_e1"): "maintenance_due",  # survives, must NOT be touched
            ("other_domain", "performance_degradation"): "performance_degradation",  # different domain, must NOT be touched
        })
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value = reg
            removed_count = await async_cleanup_removed_repairs(hass)

        assert removed_count == 3
        deleted_ids = {
            call.args[2] for call in mock_ir.async_delete_issue.call_args_list
        }
        assert deleted_ids == {
            "performance_degradation", "smberr_high_e1", "stuck_hotspot_12_7",
        }

    @pytest.mark.asyncio
    async def test_noop_when_nothing_stale(self):
        from custom_components.roomba_plus.const import DOMAIN
        from custom_components.roomba_plus.repairs import (
            async_cleanup_removed_repairs,
        )
        hass = _make_hass()
        reg = self._fake_registry({
            (DOMAIN, "maintenance_due_e1"): "maintenance_due",
            (DOMAIN, "accident_detected_e1"): "accident_detected",
        })
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value = reg
            removed_count = await async_cleanup_removed_repairs(hass)

        assert removed_count == 0
        mock_ir.async_delete_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_on_empty_registry(self):
        from custom_components.roomba_plus.repairs import (
            async_cleanup_removed_repairs,
        )
        hass = _make_hass()
        reg = self._fake_registry({})
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            mock_ir.async_get.return_value = reg
            removed_count = await async_cleanup_removed_repairs(hass)

        assert removed_count == 0
        mock_ir.async_delete_issue.assert_not_called()

    def test_removed_keys_set_has_no_overlap_with_surviving_repairs(self):
        """Guards against a future edit accidentally adding a still-used
        translation_key to the removed set, which would make this cleanup
        silently delete issues this version still actively creates."""
        from custom_components.roomba_plus.repairs import (
            _REMOVED_REPAIR_TRANSLATION_KEYS,
        )
        surviving = {
            "accident_detected", "battery_contact_suspect", "dock_contact_health",
            "maintenance_baselines_reset", "maintenance_due", "layout_change_detected",
            "observed_zones_detected", "map_retrain_stuck", "favorite_multi_commanddefs",
        }
        assert not (_REMOVED_REPAIR_TRANSLATION_KEYS & surviving)

    def test_removed_keys_count_matches_release_notes(self):
        """20 translation_keys removed/converted in v3.5.0 (9 + 8 + 1 + 1 + 1
        across the six stages) — a guard against silently dropping one."""
        from custom_components.roomba_plus.repairs import (
            _REMOVED_REPAIR_TRANSLATION_KEYS,
        )
        assert len(_REMOVED_REPAIR_TRANSLATION_KEYS) == 20
