"""IA74-MAINT full (v2.7.0) — calendar-based inspect reset services.

Tests that reset_wheel_cleaning, reset_contact_cleaning, and reset_bin_cleaning
set the correct timestamp fields in MaintenanceStore and that the corresponding
MaintenanceStore methods exist and behave correctly.
"""

from unittest.mock import patch
import pytest
from homeassistant.util import dt as dt_util

from custom_components.roomba_plus.maintenance_store import MaintenanceStore


class TestInspectResetMethods:
    """MaintenanceStore.reset_wheel/contact/bin_cleaning set timestamps."""

    def _store(self) -> MaintenanceStore:
        return MaintenanceStore()

    def test_reset_wheel_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.wheel_cleaned_at is None
        store.reset_wheel_cleaning()
        assert store.wheel_cleaned_at is not None
        # Must be a parseable ISO datetime
        parsed = dt_util.parse_datetime(store.wheel_cleaned_at)
        assert parsed is not None

    def test_reset_contact_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.contact_cleaned_at is None
        store.reset_contact_cleaning()
        assert store.contact_cleaned_at is not None
        parsed = dt_util.parse_datetime(store.contact_cleaned_at)
        assert parsed is not None

    def test_reset_bin_cleaning_sets_timestamp(self):
        store = self._store()
        assert store.bin_cleaned_at is None
        store.reset_bin_cleaning()
        assert store.bin_cleaned_at is not None
        parsed = dt_util.parse_datetime(store.bin_cleaned_at)
        assert parsed is not None

    def test_reset_wheel_cleaning_updates_existing_timestamp(self):
        """Calling reset again updates the timestamp to the new time."""
        store = self._store()
        store.reset_wheel_cleaning()
        first = store.wheel_cleaned_at
        # Patch dt_util.now to advance time
        import datetime
        future = dt_util.now() + datetime.timedelta(hours=24)
        with patch("custom_components.roomba_plus.maintenance_store.dt_util.now",
                   return_value=future):
            store.reset_wheel_cleaning()
        assert store.wheel_cleaned_at != first

    def test_timestamps_are_independent(self):
        """Each component has its own timestamp slot."""
        store = self._store()
        store.reset_wheel_cleaning()
        assert store.contact_cleaned_at is None
        assert store.bin_cleaned_at is None
        store.reset_contact_cleaning()
        assert store.bin_cleaned_at is None
        store.reset_bin_cleaning()
        # All three now set
        assert store.wheel_cleaned_at is not None
        assert store.contact_cleaned_at is not None
        assert store.bin_cleaned_at is not None
        # All different (unless they happen in the same microsecond — unlikely
        # but not asserted here to avoid flakiness)
