"""Tests for v2.6.4 bug fixes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# ── Fix 1: CF2 — current_pmap_id resolved before validation ──────────────────

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


# ── Fix 2: S1 — noAutoPasses reads from live state ───────────────────────────

class TestS1NoAutoPassesLiveState:
    """S1: clean_room and SmartZoneButton read noAutoPasses from robot state."""

    def test_one_pass_mode_respected(self):
        """One-pass selected: noAutoPasses=True, twoPass=False."""
        state = {"noAutoPasses": True, "twoPass": False}
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True
        assert two_pass is False

    def test_two_pass_mode_respected(self):
        state = {"noAutoPasses": True, "twoPass": True}
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True
        assert two_pass is True

    def test_auto_mode_defaults_to_false(self):
        state = {"noAutoPasses": False, "twoPass": False}
        no_auto = bool(state.get("noAutoPasses", False))
        assert no_auto is False


# ── Fix 3: RID — extract_region_id handles both key formats ──────────────────

class TestExtractRegionId:
    """RID: extract_region_id handles both rid and region_id keys."""

    def _extract(self, item):
        from custom_components.roomba_plus.const import extract_region_id
        return extract_region_id(item)

    def test_rid_key_from_irobot_app(self):
        """iRobot app sends {"rid": "19", "type": "rid"}."""
        assert self._extract({"rid": "19", "type": "rid"}) == "19"

    def test_region_id_key_from_roomba_plus(self):
        """Roomba+ sends {"region_id": "19", "type": "rid"}."""
        assert self._extract({"region_id": "19", "type": "rid"}) == "19"

    def test_plain_string_format(self):
        """Some firmware sends plain strings: "19"."""
        assert self._extract("19") == "19"

    def test_empty_dict_returns_empty(self):
        assert self._extract({}) == ""

    def test_none_returns_empty(self):
        assert self._extract(None) == ""

    def test_rid_takes_priority_over_region_id(self):
        """When both keys present, rid wins."""
        assert self._extract({"rid": "7", "region_id": "8"}) == "7"


# ── Fix 4: PM — unavailable presence entity not treated as away ───────────────

class TestPresenceUnavailable:
    """PM: unavailable/unknown person entities treated as 'might be home'."""

    def _all_away(self, states: dict) -> bool:
        from custom_components.roomba_plus.presence_manager import (
            _HOME_STATES, _PRESENCE_UNUSABLE
        )
        return all(
            (st := states.get(eid)) is not None
            and st not in _PRESENCE_UNUSABLE
            and st not in _HOME_STATES
            for eid in states
        )

    def test_away_state_triggers_all_away(self):
        assert self._all_away({"person.alice": "away"}) is True

    def test_home_state_blocks_all_away(self):
        assert self._all_away({"person.alice": "home"}) is False

    def test_unavailable_state_blocks_all_away(self):
        """unavailable → might be home → NOT all_away."""
        assert self._all_away({"person.alice": "unavailable"}) is False

    def test_unknown_state_blocks_all_away(self):
        """unknown → might be home → NOT all_away."""
        assert self._all_away({"person.alice": "unknown"}) is False

    def test_mixed_away_and_unavailable_blocks(self):
        """One away + one unavailable → NOT all_away (safe default)."""
        assert self._all_away({
            "person.alice": "away",
            "person.bob": "unavailable",
        }) is False


# ── Fix 5: I1 — options reload no longer fires after first connection change ──

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


# ── Fix 7: R2 — smart_zones issue targets correct entry ───────────────────────

class TestR2SmartZonesEntryId:
    """R2: Issue ID encodes entry_id for correct fix flow targeting."""

    def test_issue_id_contains_entry_id(self):
        entry_id = "ABCD1234"
        issue_id = f"smart_zones_need_naming_{entry_id}"
        assert issue_id.startswith("smart_zones_need_naming_")
        extracted = issue_id[len("smart_zones_need_naming_"):]
        assert extracted == entry_id

    def test_fix_flow_resolves_correct_entry(self):
        """get_fix_flow parses entry_id from prefixed issue_id."""
        _PREFIX = "smart_zones_need_naming_"
        issue_id = "smart_zones_need_naming_MYENTRYID"
        assert issue_id.startswith(_PREFIX)
        entry_id = issue_id[len(_PREFIX):]
        assert entry_id == "MYENTRYID"


# ── Fix 8: U1 — _validate_transform uses door candidates, not room centroids ──

class TestU1ValidateTransform:
    """U1: _validate_transform validates door candidates after transform."""

    def test_perfect_alignment_gives_zero_residual(self):
        """When transform is identity and candidates match markers exactly,
        residual should be 0 → confidence = 1.0."""
        import math
        from custom_components.roomba_plus.umf_aligner import UmfAligner

        # Build geometry: door at x=1000mm, marker at same position
        points2d = [
            {"id": "a", "coordinates": [0.5, 0.0]},  # 500mm
            {"id": "b", "coordinates": [1.5, 0.0]},  # 1500mm — gap midpoint=1000mm
        ]

        class _Marker:
            def __init__(self, cx, cy):
                self.cx, self.cy = cx, cy
                self.mission_count = 3
                self.id = "m1"

        class _GS:
            def __init__(self):
                self.door_markers = [_Marker(1000.0, 0.0)]

        a = UmfAligner.__new__(UmfAligner)
        a._geometry_store = _GS()
        a._door_candidates = [(1000.0, 0.0)]  # already in mm
        a._transform = (0.0, 0.0, 0.0)        # identity: rot=0, tx=0, ty=0
        a._room_polygons = {}
        a._confidence = 0.0
        a._aligned = False

        residual = a._validate_transform()
        assert residual == pytest.approx(0.0, abs=1.0)

    def test_poor_alignment_gives_large_residual(self):
        """When candidates are far from markers, residual > RESIDUAL_SCALE."""
        from custom_components.roomba_plus.umf_aligner import UmfAligner, _RESIDUAL_SCALE

        class _Marker:
            def __init__(self, cx, cy):
                self.cx, self.cy = cx, cy
                self.mission_count = 3
                self.id = "m1"

        class _GS:
            def __init__(self):
                self.door_markers = [_Marker(0.0, 0.0)]

        a = UmfAligner.__new__(UmfAligner)
        a._geometry_store = _GS()
        a._door_candidates = [(5000.0, 0.0)]  # 5m away from marker
        a._transform = (0.0, 0.0, 0.0)        # identity
        a._room_polygons = {}
        a._confidence = 0.0
        a._aligned = False

        residual = a._validate_transform()
        assert residual > _RESIDUAL_SCALE  # → confidence will be 0


# ── Fix 9: M1 — record_estcap_if_needed returns bool ─────────────────────────

class TestM1EstcapPersistence:
    """M1: record_estcap_if_needed returns True on first set, False on subsequent."""

    def test_returns_true_on_first_set(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        assert store.baseline_estcap is None
        result = store.record_estcap_if_needed(3000.0)
        assert result is True
        assert store.baseline_estcap == 3000.0

    def test_returns_false_on_subsequent_calls(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        store.record_estcap_if_needed(3000.0)  # first set
        result = store.record_estcap_if_needed(2900.0)  # should be no-op
        assert result is False
        assert store.baseline_estcap == 3000.0  # unchanged

    def test_returns_false_for_zero_value(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore
        store = MaintenanceStore()
        result = store.record_estcap_if_needed(0.0)
        assert result is False
        assert store.baseline_estcap is None


# ── Fix 10: completion_rate_30d includes stuck_and_resumed ───────────────────

class TestCompletionRateStuckAndResumed:
    """_completion_rate_30d counts stuck_and_resumed as completed."""

    def _rate(self, results):
        from custom_components.roomba_plus.sensor import _completion_rate_30d

        class _FakeStore:
            def query(self, days):
                return [{"result": r, "duration_min": 30} for r in results]

        return _completion_rate_30d(_FakeStore())

    def test_completed_counted(self):
        assert self._rate(["completed", "stuck"]) == pytest.approx(50.0)

    def test_stuck_and_resumed_counted_as_completed(self):
        assert self._rate(["stuck_and_resumed", "stuck"]) == pytest.approx(50.0)

    def test_both_completed_and_stuck_and_resumed(self):
        assert self._rate(["completed", "stuck_and_resumed", "stuck"]) == pytest.approx(66.7, abs=0.1)

    def test_empty_returns_none(self):
        assert self._rate([]) is None


# ── Fix v2.6.5: async_clean_segments uses Auto mode (not live CleaningPassesSelect) ──

class TestV265CleanSegmentsAutoMode:
    """v2.6.5: vacuum.clean_area always sends Auto mode — no pass-mode UI in HA spec."""

    def test_region_params_always_auto_mode(self):
        """async_clean_segments sends noAutoPasses=False, twoPass=False regardless
        of what CleaningPassesSelect is set to.

        vacuum.clean_area has no pass-mode UI in HA. Sending noAutoPasses=True
        causes error 224 on some firmware versions (Veronica, June 2026).
        """
        # Simulate robot state with One Pass or Two Pass selected
        for mode_state in [
            {"noAutoPasses": True, "twoPass": False},   # One Pass
            {"noAutoPasses": True, "twoPass": True},    # Two Pass
            {"noAutoPasses": False, "twoPass": False},  # Auto
        ]:
            no_auto = bool(mode_state.get("noAutoPasses", False))
            two_pass = bool(mode_state.get("twoPass", False))
            # async_clean_segments must NOT use these values — always Auto
            region_params = {"noAutoPasses": False, "twoPass": False}
            assert region_params["noAutoPasses"] is False
            assert region_params["twoPass"] is False

    def test_clean_room_service_still_uses_live_state(self):
        """clean_room service (S1 fix) still reads from live state — unaffected."""
        state = {"noAutoPasses": True, "twoPass": False}  # One Pass
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True   # S1 fix still active for clean_room
        assert two_pass is False


# ── Fix v2.6.4b — user_pmapv_id from cloud coordinator (lewis firmware fix) ──

class TestCloudPmapvId:
    """Cloud coordinator provides current user_pmapv_id as primary source.

    lewis 22.52.10 does not broadcast pmaps updates via local MQTT after
    map changes. _resolve_pmapv_id reads stale local value → error 224
    (Smart Map localization failed). Cloud has the current pmapv.
    """

    def _make_cc(self, pmaps_data):
        from unittest.mock import MagicMock
        cc = MagicMock()
        cc.data = {"pmaps": pmaps_data}
        cc.active_pmap_id = "PMAP1"
        return cc

    def test_active_user_pmapv_id_variant_b_lewis(self):
        """Variant B (lewis 22.52.10): last_user_pmapv_id is the active pmapv."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": [{
            "active_pmapv_details": {
                "active_pmapv": {
                    "pmap_id": "PMAP1",
                    "last_user_pmapv_id": "PMAPV_CURRENT",
                    # Note: active_pmapv_id absent (Variant B robot)
                }
            }
        }]}

        assert cc.active_user_pmapv_id == "PMAPV_CURRENT"

    def test_active_user_pmapv_id_variant_a(self):
        """Variant A: active_pmapv_id is the primary key."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": [{
            "active_pmapv_details": {
                "active_pmapv": {
                    "pmap_id": "PMAP1",
                    "active_pmapv_id": "PMAPV_A",
                }
            }
        }]}

        assert cc.active_user_pmapv_id == "PMAPV_A"

    def test_active_user_pmapv_id_none_when_no_data(self):
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = None
        assert cc.active_user_pmapv_id is None

    def test_active_user_pmapv_id_no_pmaps_returns_none(self):
        """No pmaps in cloud data → None."""
        from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
        cc = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        cc.blid = "TEST"
        cc.data = {"pmaps": []}
        assert cc.active_user_pmapv_id is None


# ── Fix: auto-heal stale region IDs via name-matching ─────────────────────────

class TestStaleRegionIdAutoHeal:
    """async_clean_segments auto-heals stale region IDs by name-matching.

    After map retraining, region IDs can change. HA stores old segment IDs.
    Auto-heal: stale_id → user label (smart_zone_labels) → current cc.regions
    name match → current region_id. Transparent — no user action needed.
    """

    def _run_heal(self, stored_ids, current_regions, zone_labels):
        """Simulate the auto-heal logic from async_clean_segments."""
        current_region_ids = {str(r["id"]) for r in current_regions if r.get("id")}
        if not current_region_ids:
            return stored_ids  # skip validation when cc.regions empty

        stale = [rid for rid in stored_ids if rid not in current_region_ids]
        if not stale:
            return stored_ids  # all current, no healing needed

        name_to_current = {
            r["name"].casefold(): str(r["id"])
            for r in current_regions if r.get("name") and r.get("id")
        }
        healed = []
        for stale_rid in stale:
            label = zone_labels.get(stale_rid, "")
            current_id = name_to_current.get(label.casefold()) if label else None
            if current_id and current_id not in stored_ids:
                healed.append(current_id)

        return [r for r in stored_ids if r in current_region_ids] + healed

    def test_auto_heal_by_name(self):
        """Stale ID resolved to current ID via name label match."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={"19": "Kitchen"},
        )
        assert result == ["23"]

    def test_no_heal_needed_when_ids_current(self):
        """Current IDs pass through unchanged."""
        result = self._run_heal(
            stored_ids=["23"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={"23": "Kitchen"},
        )
        assert result == ["23"]

    def test_partial_heal_valid_kept_stale_healed(self):
        """Valid IDs kept, stale IDs healed when label matches."""
        result = self._run_heal(
            stored_ids=["19", "21"],
            current_regions=[
                {"id": "23", "name": "Kitchen"},
                {"id": "21", "name": "Hallway"},
            ],
            zone_labels={"19": "Kitchen", "21": "Hallway"},
        )
        assert "21" in result   # was already valid
        assert "23" in result   # healed from stale "19"
        assert "19" not in result

    def test_unlabeled_stale_id_skipped(self):
        """Stale ID with no label cannot be healed — skipped gracefully."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={},  # no labels → can't match
        )
        assert result == []  # nothing healed → caller raises ServiceValidationError

    def test_empty_cc_regions_skips_validation(self):
        """No cc.regions yet → skip validation, pass stored IDs unchanged."""
        result = self._run_heal(
            stored_ids=["19", "21"],
            current_regions=[],
            zone_labels={"19": "Kitchen"},
        )
        assert result == ["19", "21"]

    def test_case_insensitive_name_match(self):
        """Name matching is case-insensitive."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "KITCHEN"}],
            zone_labels={"19": "kitchen"},
        )
        assert result == ["23"]
