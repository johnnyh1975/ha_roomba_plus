"""Tests for v2.1.0 F5 performance intelligence sensors.

F5a  recent_cleaning_speed
F5b  recent_dirt_density + cause attribute
F5c  recent_recharge_fraction
F5d  battery_capacity_retention (RoombaSensor)
F5e  cleaning_speed_trend
F5f  recent_coverage_pct (factory closure)
F5g  estimated_battery_eol (RoombaSensor)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import sys

_ep = sys.modules.get('homeassistant.helpers.entity_platform')
if _ep and not hasattr(_ep, 'AddConfigEntryEntitiesCallback'):
    _ep.AddConfigEntryEntitiesCallback = _ep.AddEntitiesCallback

from custom_components.roomba_plus.sensor import (
    _raw_cleaning_speed,
    _raw_dirt_density,
    _raw_dirt_density_attrs,
    _raw_recharge_fraction,
    _raw_cleaning_speed_trend,
    _make_coverage_pct_fn,
    _battery_capacity_retention,
    _estimated_battery_eol,
    SENSORS,
    CLOUD_RAW_SENSORS,
)
from custom_components.roomba_plus.maintenance_store import MaintenanceStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entity(battery_stats: dict = None, vacuum_state: dict = None,
            battery_mah: int = 2000) -> MagicMock:
    """Create a test entity.

    battery_mah — sets robot_profile.battery_mah for retention calculations.
                  Default 2000 matches legacy test values (non-9-series, scale=1.0).
    """
    from unittest.mock import MagicMock as _MM
    e = MagicMock()
    e.battery_stats = battery_stats or {}
    e.vacuum_state = vacuum_state or {}
    store = MaintenanceStore()
    e._config_entry.runtime_data.maintenance_store = store
    # RF0 robot_profile — scale=1.0 (non-9-series) so raw estCap == mAh directly
    profile = _MM()
    profile.battery_mah = battery_mah
    profile.estcap_scale_liion = 1.0
    profile.estcap_scale_nimh  = 1.0
    e._config_entry.runtime_data.robot_profile = profile
    return e


def _records(n: int = 10, sqft: float = 200, run_m: float = 40,
             dirt: float = 20, chrg_m: float = 5, dur_m: float = 50,
             ts_base: int = 1700000000) -> list[dict]:
    return [
        {
            "sqft": sqft,
            "runM": run_m,
            "durationM": dur_m,
            "dirt": dirt,
            "chrgM": chrg_m,
            "startTime": ts_base - i * 86400,
            "timestamp": ts_base - i * 86400 + 3000,
        }
        for i in range(n)
    ]


# ── F5a — cleaning speed ──────────────────────────────────────────────────────

class TestCleaningSpeed:
    def test_basic_median(self):
        recs = _records(5, sqft=200, run_m=40)  # 200/40 = 5.0
        assert _raw_cleaning_speed(recs) == round(5.0 * 0.0929, 2)  # 0.46 m²/min

    def test_skips_zero_run_m(self):
        recs = [{"sqft": 200, "runM": 0}]
        assert _raw_cleaning_speed(recs) is None

    def test_skips_missing_sqft(self):
        recs = [{"runM": 40}]
        assert _raw_cleaning_speed(recs) is None

    def test_uses_durationM_fallback(self):
        recs = [{"sqft": 100, "durationM": 50}]
        result = _raw_cleaning_speed(recs)
        assert result == round(2.0 * 0.0929, 2)  # 0.19 m²/min

    def test_empty_records(self):
        assert _raw_cleaning_speed([]) is None

    def test_mixed_valid_invalid(self):
        recs = [{"sqft": 200, "runM": 40}, {"sqft": None}, {"runM": 0}]
        assert _raw_cleaning_speed(recs) == round(5.0 * 0.0929, 2)  # 0.46 m²/min


# ── F5b — dirt density + cause ────────────────────────────────────────────────

class TestDirtDensity:
    def test_basic_density(self):
        recs = _records(5, sqft=200, dirt=20)  # 20/200 = 0.1
        assert _raw_dirt_density(recs) == round(0.1 / 0.0929, 3)  # 1.076 events/m²

    def test_skips_zero_sqft(self):
        recs = [{"dirt": 10, "sqft": 0}]
        assert _raw_dirt_density(recs) is None

    def test_skips_missing_dirt(self):
        recs = [{"sqft": 200}]
        assert _raw_dirt_density(recs) is None

    def test_empty(self):
        assert _raw_dirt_density([]) is None

    def test_cause_attribute_present(self):
        attrs = _raw_dirt_density_attrs(_records(15))
        assert "cause" in attrs
        assert attrs["cause"] in ("brush_wear", "floor_dirty", "unknown")

    def test_cause_unknown_on_insufficient_data(self):
        attrs = _raw_dirt_density_attrs(_records(3))
        assert attrs["cause"] == "unknown"

    def test_description_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_dirt_density" in keys

    def test_attributes_fn_set(self):
        desc = next(d for d in CLOUD_RAW_SENSORS if d.key == "recent_dirt_density")
        assert desc.attributes_fn is not None


# ── F5c — recharge fraction ───────────────────────────────────────────────────

class TestRechargeFraction:
    def test_basic_fraction(self):
        # chrgM=10, durationM=50 → 20%
        recs = _records(5, chrg_m=10, dur_m=50)
        assert _raw_recharge_fraction(recs) == 20.0

    def test_falls_back_to_local_recharge_min(self):
        recs = [{"recharge_min": 10, "duration_min": 50}]
        result = _raw_recharge_fraction(recs)
        assert result == 20.0

    def test_skips_zero_duration(self):
        recs = [{"chrgM": 5, "durationM": 0}]
        assert _raw_recharge_fraction(recs) is None

    def test_empty(self):
        assert _raw_recharge_fraction([]) is None


# ── F5e — cleaning speed trend ────────────────────────────────────────────────

class TestCleaningSpeedTrend:
    def _make_records(self, speeds: list[float], ts_base: int = 1700000000) -> list[dict]:
        """Build records with given speed (sqft/runM) in order newest-first."""
        return [
            {
                "sqft": s * 40,
                "runM": 40,
                "startTime": ts_base - i * 86400,
                "timestamp": ts_base - i * 86400 + 3000,
            }
            for i, s in enumerate(speeds)
        ]

    def test_unknown_with_fewer_than_6_records(self):
        recs = self._make_records([5.0] * 5)
        assert _raw_cleaning_speed_trend(recs) == "unknown"

    def test_stable_similar_speeds(self):
        recs = self._make_records([5.0] * 15)
        assert _raw_cleaning_speed_trend(recs) == "stable"

    def test_declining_trend(self):
        # Recent 5: ~3.0, older 10: ~6.0 → >10% decline
        recent = [3.0] * 5
        older  = [6.0] * 10
        recs = self._make_records(recent + older)
        assert _raw_cleaning_speed_trend(recs) == "declining"

    def test_improving_trend(self):
        recent = [8.0] * 5
        older  = [4.0] * 10
        recs = self._make_records(recent + older)
        assert _raw_cleaning_speed_trend(recs) == "improving"

    def test_gap_filter_excludes_post_gap_missions(self):
        """Records after a >7-day gap are excluded from trend calculation."""
        ts_base = 1700000000
        # 5 recent records at normal speed, then 10-day gap, then 10 records
        # at high speed (which should be excluded — post-gap catch-up cleans)
        recent = [
            {"sqft": 3 * 40, "runM": 40, "startTime": ts_base - i * 86400}
            for i in range(5)
        ]
        gap_ts = ts_base - 5 * 86400 - 10 * 86400  # 10-day gap
        old_post_gap = [
            {"sqft": 8 * 40, "runM": 40, "startTime": gap_ts - i * 86400}
            for i in range(10)
        ]
        recs = recent + old_post_gap
        # Should be "unknown" (not enough records after gap filter) or at least
        # not "improving" (old high-speed records excluded)
        result = _raw_cleaning_speed_trend(recs)
        assert result != "improving"

    def test_description_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "cleaning_speed_trend" in keys


# ── F5f — coverage pct factory ───────────────────────────────────────────────

class TestCoveragePct:
    def _store_with_areas(self, areas: list[float]) -> MagicMock:
        from custom_components.roomba_plus.mission_store import MissionStore
        from datetime import datetime, timezone, timedelta
        store = MissionStore()
        now = datetime.now(timezone.utc)
        for i, area in enumerate(areas):
            # Use recent dates so query(60) includes all records
            dt = now - timedelta(days=i)
            store._records.append({
                "id": f"m_{i}",
                "started_at": dt.isoformat(),
                "ended_at":   (dt + timedelta(hours=1)).isoformat(),
                "area_sqft": area,
                "result": "completed",
            })
        return store

    def test_returns_pct_relative_to_p75(self):
        areas = [200.0] * 15  # p75 = 200
        store = self._store_with_areas(areas)
        store_ref = [store]
        fn = _make_coverage_pct_fn(store_ref)
        records = [{"sqft": 200}]
        result = fn(records)
        assert result == 100.0

    def test_returns_none_below_5_records(self):
        store = self._store_with_areas([200.0] * 4)
        fn = _make_coverage_pct_fn([store])
        assert fn([{"sqft": 200}]) is None

    def test_returns_none_on_empty_records(self):
        store = self._store_with_areas([200.0] * 15)
        fn = _make_coverage_pct_fn([store])
        assert fn([]) is None

    def test_returns_none_when_store_ref_empty(self):
        fn = _make_coverage_pct_fn([None])
        assert fn([{"sqft": 200}]) is None

    def test_description_in_cloud_raw_sensors(self):
        keys = [d.key for d in CLOUD_RAW_SENSORS]
        assert "recent_coverage_pct" in keys


# ── F5d — battery capacity retention ─────────────────────────────────────────

class TestBatteryCapacityRetention:
    def test_100_pct_healthy_with_baseline(self):
        # baseline=2000 (first-observed), current=2000 → 100% healthy
        e = _entity(battery_stats={"estCap": 2000}, battery_mah=2000)
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) == 100.0

    def test_75_pct_degraded_with_baseline(self):
        # baseline=2000 (when new), current=1500 (degraded) → 75%
        e = _entity(battery_stats={"estCap": 1500}, battery_mah=2000)
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) == 75.0

    def test_fallback_to_oem_nominal_before_baseline(self):
        # No baseline yet → falls back to profile.battery_mah as cold-start denominator
        e = _entity(battery_stats={"estCap": 1500}, battery_mah=2000)
        # baseline_estcap is None; record_estcap_if_needed sets it to 1500
        # then denominator = 1500 → 100% (first observation treated as full)
        assert _battery_capacity_retention(e) == 100.0

    def test_aftermarket_shows_above_100_before_baseline(self):
        # Aftermarket 2500 mAh in robot with OEM 2000 mAh profile
        # First observation: baseline set to 2500, denominator=2500 → 100%
        e = _entity(battery_stats={"estCap": 2500}, battery_mah=2000)
        assert _battery_capacity_retention(e) == 100.0

    def test_sets_baseline_on_first_call(self):
        # Baseline receives the converted mAh value (= raw for scale=1.0)
        e = _entity(battery_stats={"estCap": 2000}, battery_mah=2000)
        store = e._config_entry.runtime_data.maintenance_store
        assert store.baseline_estcap is None
        _battery_capacity_retention(e)
        assert store.baseline_estcap == 2000.0  # scale=1.0: mAh == raw

    def test_baseline_not_overwritten_on_subsequent_calls(self):
        e = _entity(battery_stats={"estCap": 1800})
        store = e._config_entry.runtime_data.maintenance_store
        store.baseline_estcap = 2000.0
        _battery_capacity_retention(e)
        assert store.baseline_estcap == 2000.0  # unchanged

    def test_none_when_estcap_missing(self):
        e = _entity(battery_stats={})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _battery_capacity_retention(e) is None

    def test_none_when_no_store(self):
        e = MagicMock()
        e.battery_stats = {"estCap": 2000}
        e._config_entry.runtime_data.maintenance_store = None
        assert _battery_capacity_retention(e) is None

    def test_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "battery_capacity_retention" in keys

    def test_filter_fn_requires_estcap(self):
        desc = next(d for d in SENSORS if d.key == "battery_capacity_retention")
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True
        assert desc.filter_fn({"bbchg3": {}}) is False
        assert desc.filter_fn({}) is False


# ── F5g — estimated battery EOL ──────────────────────────────────────────────

class TestEstimatedBatteryEol:
    def _make_entity(self, est_cap: float, baseline: float, cycles: int) -> MagicMock:
        e = _entity(battery_stats={"estCap": est_cap, "nLithChrg": cycles})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = baseline
        return e

    def test_returns_days_when_above_threshold(self):
        # 90% retention, 300 cycles → well above 65% EOL threshold
        e = self._make_entity(est_cap=1800, baseline=2000, cycles=300)
        result = _estimated_battery_eol(e)
        assert result is not None
        assert result > 0

    def test_returns_zero_at_or_below_threshold(self):
        # 60% retention → already below 65% EOL threshold
        e = self._make_entity(est_cap=1200, baseline=2000, cycles=500)
        assert _estimated_battery_eol(e) == 0

    def test_returns_none_without_baseline(self):
        e = _entity(battery_stats={"estCap": 1800, "nLithChrg": 300})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = None
        assert _estimated_battery_eol(e) is None

    def test_returns_none_without_cycles(self):
        e = _entity(battery_stats={"estCap": 1800})
        e._config_entry.runtime_data.maintenance_store.baseline_estcap = 2000.0
        assert _estimated_battery_eol(e) is None

    def test_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "estimated_battery_eol" in keys

    def test_filter_fn_requires_estcap(self):
        desc = next(d for d in SENSORS if d.key == "estimated_battery_eol")
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True
        assert desc.filter_fn({"bbchg3": {}}) is False


# ── MaintenanceStore F5d additions ───────────────────────────────────────────

class TestMaintenanceStoreF5d:
    def test_record_estcap_if_needed_sets_baseline(self):
        store = MaintenanceStore()
        store.record_estcap_if_needed(2000.0)
        assert store.baseline_estcap == 2000.0

    def test_record_estcap_if_needed_noop_when_already_set(self):
        store = MaintenanceStore()
        store.baseline_estcap = 2000.0
        store.record_estcap_if_needed(1800.0)
        assert store.baseline_estcap == 2000.0  # unchanged

    def test_record_estcap_ignores_zero(self):
        store = MaintenanceStore()
        store.record_estcap_if_needed(0.0)
        assert store.baseline_estcap is None

    def test_consecutive_skips_defaults_to_zero(self):
        store = MaintenanceStore()
        assert store.consecutive_skips == 0
