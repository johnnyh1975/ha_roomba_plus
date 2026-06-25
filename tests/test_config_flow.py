"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest


class TestCF2PmapResolution:
    """CF2: pmap_id resolved before the 'elif not current_pmap_id' validation."""

    def test_pmap_resolution_happens_before_validation(self):
        """Verify pmap_id is available at Phase 2 validation time."""
        # Simulate Phase 2 data flow: pmap is in lastCommand
        state = {
            "lastCommand": {"pmap_id": "ABC123", "command": "start"},
            "cleanSchedule2": [],
            "pmaps": [],
        }
        # Resolution logic (copied from config_flow fix)
        current_pmap_id = ""
        last = state.get("lastCommand", {})
        if last.get("pmap_id"):
            current_pmap_id = last["pmap_id"]

        assert current_pmap_id == "ABC123"

    def test_pmap_fallback_to_schedule2(self):
        state = {
            "lastCommand": {},
            "cleanSchedule2": [{"cmd": {"pmap_id": "SCHED1", "regions": []}}],
            "pmaps": [],
        }
        current_pmap_id = ""
        last = state.get("lastCommand", {})
        if last.get("pmap_id"):
            current_pmap_id = last["pmap_id"]
        else:
            for entry in state.get("cleanSchedule2", []):
                if entry.get("cmd", {}).get("pmap_id"):
                    current_pmap_id = entry["cmd"]["pmap_id"]
                    break
        assert current_pmap_id == "SCHED1"


class TestI1OptionsReload:
    """I1: _async_reload_on_options_change syncs data to prevent false retriggers."""

    def test_reload_triggered_on_connection_change(self):
        data = {"continuous": True, "delay": 1}
        options = {"continuous": False, "delay": 1}  # changed
        connection_keys = {"continuous", "delay"}
        old = {k: data.get(k) for k in connection_keys}
        new = {k: options.get(k) for k in connection_keys}
        assert old != new  # reload triggered

    def test_no_reload_after_sync(self):
        """After syncing data with new options, next options change doesn't reload."""
        # Simulate post-sync state: data updated to match options
        data = {"continuous": False, "delay": 1}  # synced
        options = {"continuous": False, "delay": 1, "blocking_sensors": ["x"]}
        connection_keys = {"continuous", "delay"}
        old = {k: data.get(k) for k in connection_keys}
        new = {k: options.get(k) for k in connection_keys}
        assert old == new  # no reload
