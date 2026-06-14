"""L8 (v2.7.0) — Composite robot health score tests.

Tests for RobotProfileStore.compute_health_score() covering signal weighting,
None-gating, and boundary conditions.
"""
import pytest
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore


def _rps(coverage_baseline=None, coverage_mission_count=0) -> RobotProfileStore:
    rps = RobotProfileStore()
    rps.coverage_baseline = coverage_baseline
    rps.coverage_mission_count = coverage_mission_count
    return rps


class TestComputeHealthScore:

    def test_returns_none_when_fewer_than_3_signals(self):
        """With only battery + anomaly (2 signals), score is None."""
        rps = _rps()  # no coverage baseline → nav signal missing
        # Only battery (1) + anomaly (always counted) = 2 signals
        score = rps.compute_health_score(
            battery_retention_pct=85.0,
            nav_efficiency_ratio=None,    # no baseline → skipped
            cleaning_speed_trend="unknown",  # not in map → skipped
            consecutive_anomalous=0,
            stuck_rate_30d=None,          # skipped
        )
        assert score is None

    def test_returns_score_with_all_signals_healthy(self):
        """All signals healthy → score near 100."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=95.0,   # → 90 pts
            nav_efficiency_ratio=1.0,     # current == baseline → 100 pts
            cleaning_speed_trend="improving",  # → 100 pts
            consecutive_anomalous=0,      # → 100 pts
            stuck_rate_30d=0.02,          # 2% < 5% → 100 pts
        )
        assert score is not None
        assert isinstance(score, float)
        assert 85.0 <= score <= 100.0

    def test_returns_low_score_with_all_signals_poor(self):
        """All signals poor → score near 0."""
        rps = _rps(coverage_baseline=0.7, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=55.0,   # → 10 pts (near 0)
            nav_efficiency_ratio=0.4,     # below 0.5 → 0 pts
            cleaning_speed_trend="declining",  # → 40 pts
            consecutive_anomalous=3,      # → 10 pts
            stuck_rate_30d=0.40,          # > 30% → 0 pts
        )
        assert score is not None
        assert score <= 40.0

    def test_score_capped_between_0_and_100(self):
        """Score must always be in [0, 100]."""
        rps = _rps(coverage_baseline=0.5, coverage_mission_count=25)
        score = rps.compute_health_score(
            battery_retention_pct=200.0,  # extreme value
            nav_efficiency_ratio=5.0,     # extreme value
            cleaning_speed_trend="improving",
            consecutive_anomalous=0,
            stuck_rate_30d=-0.5,          # extreme negative
        )
        assert score is not None
        assert 0.0 <= score <= 100.0

    def test_partial_signals_renormalise_weights(self):
        """With nav signal missing, remaining weights are renormalised."""
        rps = _rps()  # no coverage baseline → nav skipped
        # Provide battery + trend + anomaly + stuck (4 of 5) = enough
        score = rps.compute_health_score(
            battery_retention_pct=80.0,
            nav_efficiency_ratio=None,    # skipped
            cleaning_speed_trend="stable",
            consecutive_anomalous=0,
            stuck_rate_30d=0.05,
        )
        assert score is not None
        assert 0.0 <= score <= 100.0

    def test_nav_signal_requires_baseline_ready(self):
        """Nav signal only counted when coverage_baseline_ready is True."""
        rps_no_baseline = _rps(coverage_baseline=0.7, coverage_mission_count=5)
        rps_ready = _rps(coverage_baseline=0.7, coverage_mission_count=25)

        kwargs = dict(
            battery_retention_pct=80.0,
            nav_efficiency_ratio=1.0,
            cleaning_speed_trend="stable",
            consecutive_anomalous=0,
            stuck_rate_30d=0.05,
        )
        score_no_baseline = rps_no_baseline.compute_health_score(**kwargs)
        score_ready = rps_ready.compute_health_score(**kwargs)

        # Both should produce a score (nav just gets skipped vs included)
        assert score_no_baseline is not None
        assert score_ready is not None
        # With perfect nav signal included, ready score should be ≥ no-baseline score
        assert score_ready >= score_no_baseline
