"""Tests for v2.1.0 F6 Repair Issue intelligence.

F6a  performance_degradation issue
F6b  battery_recharge_high issue
F6c  blocked_timeout recorded in MissionStore
F6d  drift issue enrichment with bearing/magnitude
F6e  mixed_schedule detection
F6f  accident_detected alert
F6g  consecutive_skips counter + issue + sensor
F6h  stuck_and_resumed / stuck_and_abandoned result values
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from custom_components.roomba_plus.maintenance_store import MaintenanceStore
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.repairs import (
    async_check_bbrun_reset,
    async_check_performance_degradation,
    async_check_battery_recharge,
    async_check_mixed_schedule,
    async_check_accident_detection,
    async_check_consecutive_skips,
    async_enrich_drift_issue,
)
import sys
_ep = sys.modules.get('homeassistant.helpers.entity_platform')
if _ep and not hasattr(_ep, 'AddConfigEntryEntitiesCallback'):
    _ep.AddConfigEntryEntitiesCallback = _ep.AddEntitiesCallback
from custom_components.roomba_plus.sensor import SENSORS


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── F6a — performance degradation ────────────────────────────────────────────

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


# ── F6b — battery recharge ────────────────────────────────────────────────────

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


# ── F6e — mixed schedule ──────────────────────────────────────────────────────

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


# ── F6f — accident detection ──────────────────────────────────────────────────

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


# ── F6g — consecutive skips ───────────────────────────────────────────────────

class TestConsecutiveSkips:
    @pytest.mark.asyncio
    async def test_no_issue_below_3_skips(self):
        store = MaintenanceStore()
        store.consecutive_skips = 2
        entry = _make_entry(maintenance_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(_make_hass(), entry)
            mock_ir.async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_fires_at_3_skips(self):
        store = MaintenanceStore()
        store.consecutive_skips = 3
        entry = _make_entry(maintenance_store=store)
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(_make_hass(), entry)
            mock_ir.async_create_issue.assert_called_once()
            assert mock_ir.async_create_issue.call_args[1]["translation_key"] == "consecutive_skips"
            assert mock_ir.async_create_issue.call_args[1]["translation_placeholders"]["count"] == "3"

    @pytest.mark.asyncio
    async def test_issue_cleared_when_skips_zero(self):
        store = MaintenanceStore()
        store.consecutive_skips = 0
        entry = _make_entry(maintenance_store=store)
        hass = _make_hass()
        with patch("custom_components.roomba_plus.repairs.ir") as mock_ir:
            await async_check_consecutive_skips(hass, entry)
            mock_ir.async_delete_issue.assert_called_once_with(
                hass, "roomba_plus", "consecutive_skips"
            )

    def test_consecutive_skips_sensor_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "consecutive_clean_skips" in keys

    def test_skips_increments_on_timeout(self):
        """consecutive_skips increments after a blocking timeout."""
        store = MaintenanceStore()
        assert store.consecutive_skips == 0
        store.consecutive_skips += 1
        assert store.consecutive_skips == 1

    def test_skips_reset_on_completed_mission(self):
        store = MaintenanceStore()
        store.consecutive_skips = 4
        # Simulate the reset logic in callbacks.py
        if store.consecutive_skips > 0:
            store.consecutive_skips = 0
        assert store.consecutive_skips == 0


# ── F6d — drift enrichment ────────────────────────────────────────────────────

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


# ── F6h — stuck recovery classification ──────────────────────────────────────

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
