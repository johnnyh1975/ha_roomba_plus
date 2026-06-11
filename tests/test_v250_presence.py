"""Tests for v2.5.0 PresenceManager changes (R1, R2, P5).

Covers:
  - R1: _clean_events initialised in __init__ (no hasattr guard needed)
  - R2: preferred_window uses dt_util.now() — returns correct weekday in HA timezone
  - P5: record_clean_event prune only fires when total > 500, skips below threshold
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus.presence_manager import PresenceManager


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_manager() -> PresenceManager:
    """Build a PresenceManager with minimal mocks."""
    hass = MagicMock()
    entry = MagicMock()
    entry.options = {"presence_entities": ["person.alice"]}
    return PresenceManager(hass, entry)


def _utc(year: int = 2026, month: int = 1, day: int = 5, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


def _recent_monday() -> datetime:
    """Return a Monday datetime that is within the last 30 days from now."""
    now = datetime.now(UTC)
    # Walk back up to 6 days to find a Monday (weekday=0)
    day = now - timedelta(days=now.weekday())  # this week's Monday
    if (now - day).days > 25:
        # If this Monday is too old, use last week's but still recent
        day = day + timedelta(weeks=-1)
    return day.replace(hour=10, minute=0, second=0, microsecond=0)


# ── R1: _clean_events initialised in __init__ ─────────────────────────────────

class TestCleanEventsInit:
    def test_clean_events_present_immediately_after_init(self):
        """_clean_events must exist on a fresh instance without calling any method."""
        mgr = _make_manager()
        assert hasattr(mgr, "_clean_events"), "_clean_events should be set in __init__"
        assert isinstance(mgr._clean_events, defaultdict)

    def test_clean_events_empty_on_fresh_instance(self):
        mgr = _make_manager()
        assert len(mgr._clean_events) == 0

    def test_presence_windows_works_without_prior_record_call(self):
        """presence_windows must not raise when called before any records added."""
        mgr = _make_manager()
        result = mgr.presence_windows()
        assert result == {}

    def test_preferred_window_works_without_prior_record_call(self):
        mgr = _make_manager()
        result = mgr.preferred_window()
        assert result is None


# ── R2: preferred_window timezone fix ────────────────────────────────────────

class TestPreferredWindowTimezone:
    def test_preferred_window_uses_ha_timezone(self):
        """preferred_window should use dt_util.now() weekday, not datetime.now()."""
        mgr = _make_manager()

        # Seed 6 events on a recent Monday at 10:00 UTC so recent_count > 0
        monday_utc = _recent_monday()
        assert monday_utc.weekday() == 0, "Fixture must be a Monday"
        for _ in range(6):
            mgr.record_clean_event(monday_utc)

        # Mock dt_util.now() to return that same Monday
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.now",
            return_value=monday_utc,
        ):
            result = mgr.preferred_window()

        assert result is not None
        weekday, hour = result
        assert weekday == 0, "Should be Monday (weekday=0)"

    def test_preferred_window_no_results_for_different_day(self):
        """Events recorded on Monday should not appear for Tuesday."""
        mgr = _make_manager()

        monday_utc = _recent_monday()
        for _ in range(6):
            mgr.record_clean_event(monday_utc)

        # Mock dt_util.now() to return Tuesday (day after the Monday)
        mock_tuesday = monday_utc + timedelta(days=1)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.now",
            return_value=mock_tuesday,
        ):
            result = mgr.preferred_window()

        # Tuesday has no events, so result should be None
        assert result is None


# ── P5: conditional prune threshold ──────────────────────────────────────────

class TestRecordCleanEventPrune:
    def test_prune_not_triggered_below_threshold(self):
        """Below 500 total events, prune loop should not run."""
        mgr = _make_manager()

        # Add 10 old events (older than 90 days)
        old_dt = datetime(2020, 1, 1, 10, 0, tzinfo=UTC)
        for _ in range(10):
            mgr.record_clean_event(old_dt)

        # Total is 10 — well below 500. Old events must NOT be pruned.
        total = sum(len(v) for v in mgr._clean_events.values())
        assert total == 10, "Should have 10 events — prune threshold not reached"

    def test_prune_triggered_above_threshold(self):
        """Above 500 total events, old events (> 90 days) must be pruned."""
        mgr = _make_manager()

        # Fill up to 501 events, half old, half recent
        old_dt = datetime(2020, 1, 1, 10, 0, tzinfo=UTC)
        recent_dt = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)

        # 250 old events across different slots
        for i in range(250):
            slot_dt = old_dt.replace(hour=i % 24)
            mgr._clean_events[(i % 7, i % 24)].append(slot_dt)

        # 251 recent events to push over 500
        for i in range(251):
            slot_dt = recent_dt.replace(hour=i % 24)
            mgr._clean_events[(i % 7, i % 24)].append(slot_dt)

        assert sum(len(v) for v in mgr._clean_events.values()) == 501

        # Trigger via record_clean_event (total=501 > 500 triggers prune)
        with patch(
            "custom_components.roomba_plus.presence_manager.dt_util.utcnow",
            return_value=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        ):
            mgr.record_clean_event(recent_dt)

        # Old events should have been pruned; recent ones preserved (plus the new one)
        remaining = sum(len(v) for v in mgr._clean_events.values())
        assert remaining <= 252, f"Expected at most 252 recent events, got {remaining}"
        assert remaining >= 251, "Recent events must be preserved"
