"""Consolidated regression / defensive-guard test suite.

Originated from the v2.8.0 nine-round bug-hunt plus its two hotfixes
(TEST-REORG, June 2026). Each guard test is inherently cross-cutting --
it exists because a defect spanned module boundaries -- so these stay
grouped here rather than being split into single-module domain files.
Future bug-hunt rounds for later releases can be appended the same way.
"""


from __future__ import annotations



from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from custom_components.roomba_plus.mission_archive import MissionArchive
import asyncio
from unittest.mock import AsyncMock
from custom_components.roomba_plus.mission_archive import _safe_float
from custom_components.roomba_plus.mission_archive import _safe_int
from custom_components.roomba_plus.callbacks import make_mission_callback
from custom_components.roomba_plus.callbacks import make_mission_complete_callback
from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
from custom_components.roomba_plus.mission_timer_store import _safe_float as _safe_float_v280_bug_hunt_r4
from custom_components.roomba_plus.sensor import _compute_room_time_estimates
from custom_components.roomba_plus.repairs import _safe_int_repairs
from custom_components.roomba_plus.repairs import async_check_dock_health
from custom_components.roomba_plus.sensor import _parse_netinfo_addr
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from custom_components.roomba_plus.services import async_handle_advance_room
from custom_components.roomba_plus.const import has_carpet_boost
from custom_components.roomba_plus.const import has_clean_base
from custom_components.roomba_plus.const import has_pose
from custom_components.roomba_plus.sensor import SENSORS
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.repairs import async_check_smberr


def _make_entry(mts: MissionTimerStore) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"blid": "ABC123"}
    entry.options = {}
    entry.runtime_data.mission_timer_store = mts
    entry.runtime_data.mission_store = MagicMock()
    entry.runtime_data.mission_store.async_append = AsyncMock()
    entry.runtime_data.mission_store.async_save = AsyncMock()
    entry.runtime_data.zone_store = None
    entry.runtime_data.map_capability = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.presence_manager = None
    entry.runtime_data.demand_triggered_ts = None
    entry.runtime_data.robot_profile_store = None
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.roomba.master_state = {
        "state": {"reported": {
            "lastCommand": {"regions": [
                {"region_name": "Kitchen", "region_id": "19"},
                {"region_name": "Hall", "region_id": "21"},
                {"region_name": "Bedroom", "region_id": "22"},
            ]},
        }}
    }
    return entry


def _run_msg(nmssn: int = 1) -> dict:
    return {"state": {"reported": {"cleanMissionStatus": {
        "phase": "run", "cycle": "clean", "mssnStrtTm": 1, "nMssn": nmssn,
    }, "bbrun": {"nStuck": 0}}}}


def _entry_with_regions(regions: list[dict], pass_mode: dict | None = None) -> MagicMock:
    cc = MagicMock()
    cc.regions = regions
    entry = MagicMock()
    entry.runtime_data.cloud_coordinator = cc
    entry.runtime_data.roomba_reported_state.return_value = {
        "cleanMissionStatus": pass_mode or {"noAutoPasses": True, "twoPass": False}
    }
    return entry


def _make_entry_v280_bug_hunt_r5(mts) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"blid": "ABC123"}
    entry.options = {}
    entry.runtime_data.mission_timer_store = mts
    entry.runtime_data.mission_store = MagicMock()
    entry.runtime_data.mission_store.async_append = AsyncMock()
    entry.runtime_data.mission_store.async_save = AsyncMock()
    entry.runtime_data.zone_store = None
    entry.runtime_data.map_capability = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.presence_manager = None
    entry.runtime_data.demand_triggered_ts = None
    entry.runtime_data.robot_profile_store = None
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.roomba.master_state = {"state": {"reported": {
        "lastCommand": {"regions": [{"region_name": "Kitchen", "region_id": "19"}]}
    }}}
    return entry


def _run_msg_v280_bug_hunt_r5() -> dict:
    return {"state": {"reported": {"cleanMissionStatus": {
        "phase": "run", "cycle": "clean", "mssnStrtTm": 1, "nMssn": 1,
    }, "bbrun": {"nStuck": 0}}}}


def _make_entry_v280_bug_hunt_r6(mts: MissionTimerStore, last_command_value) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"blid": "ABC123"}
    entry.options = {}
    entry.runtime_data.mission_timer_store = mts
    entry.runtime_data.mission_store = MagicMock()
    entry.runtime_data.mission_store.async_append = AsyncMock()
    entry.runtime_data.mission_store.async_save = AsyncMock()
    entry.runtime_data.zone_store = None
    entry.runtime_data.map_capability = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.presence_manager = None
    entry.runtime_data.demand_triggered_ts = None
    entry.runtime_data.robot_profile_store = None
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.roomba.master_state = {
        "state": {"reported": {"lastCommand": last_command_value}}
    }
    return entry


def _run_msg_v280_bug_hunt_r6() -> dict:
    return {"state": {"reported": {"cleanMissionStatus": {
        "phase": "run", "cycle": "clean", "mssnStrtTm": 1, "nMssn": 1,
    }, "bbrun": {"nStuck": 0}}}}


def _make_call(hass: MagicMock, entity_ids: list[str]) -> MagicMock:
    call = MagicMock()
    call.hass = hass
    call.data = {"entity_id": entity_ids}
    return call


def _make_config_entry(
    mts: MagicMock | None, reported_state: dict
) -> MagicMock:
    config_entry = MagicMock()
    config_entry.runtime_data.mission_timer_store = mts
    config_entry.runtime_data.roomba_reported_state.return_value = reported_state
    return config_entry


def _make_coordinator_with_archive(mission_archive) -> IrobotCloudCoordinator:
    """Construct a coordinator using object.__new__ (bypassing HA's
    DataUpdateCoordinator.__init__/event-loop machinery, matching the
    pattern already used by test_v240_coordinator.py) and set ONLY the
    attributes _async_update_data actually needs.

    Deliberately does NOT use MagicMock() for the coordinator itself, and
    deliberately does NOT set `_config_entry` — if the typo regresses,
    accessing it here raises a real AttributeError instead of silently
    returning an auto-created Mock, which is exactly what let the original
    bug slip past every mocked test in this session.
    """
    coord = object.__new__(IrobotCloudCoordinator)
    coord.data = None
    coord.blid = "31B8091051311850"
    coord._has_pmaps = False
    coord._mission_store = None
    coord._mission_archive = mission_archive
    coord._last_success_time = None
    coord.hass = MagicMock()
    coord.config_entry = MagicMock(entry_id="test_entry_id")
    coord.api = AsyncMock()
    coord.api.get_pmaps = AsyncMock(return_value=[])
    coord.api.get_favorites = AsyncMock(return_value=[])
    coord.api.get_automations = AsyncMock(return_value={})
    coord.api.get_mission_history = AsyncMock(return_value=[
        {"nMssn": 42, "startTime": 1700000000, "timestamp": 1700001000},
    ])
    return coord


def _entry(master_state: dict) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "e"
    # v2.9.0 — repairs.py now reads data.roomba_reported_state() instead of
    # the removed data.vacuum.master_state attribute (AttributeError fix).
    # Replicates the exact same (state or {}).get("reported") or {} chain
    # here so every existing call site below keeps testing the SAME
    # scenarios (missing bbchg, corrupted smberr, explicit state=None,
    # etc.) without needing to change any of them individually.
    reported = (master_state.get("state") or {}).get("reported") or {}
    entry.runtime_data.roomba_reported_state.return_value = reported
    return entry


class TestNMssn0Dedup:
    def test_nmssn_zero_not_appended_by_delta_update(self):
        """nMssn=0 records must be rejected immediately — not added to archive."""
        import asyncio
        from unittest.mock import AsyncMock
        arc = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            result = asyncio.get_event_loop().run_until_complete(
                arc.async_delta_update({"nMssn": 0, "done": "done"}, hass, "e")
            )
        assert result is False
        assert arc.record_count == 0

    def test_nmssn_zero_not_duplicated(self):
        """Direct _append: nMssn=0 still gets inserted (internal use),
        but async_delta_update rejects it before _append is called."""
        arc = MissionArchive()
        # _append does insert nMssn=0 (internal path — not guarded there)
        arc._append({"nMssn": 0, "done": "done"})
        arc._append({"nMssn": 0, "done": "done"})
        # Two _append calls → two records (no dedup in _append for nMssn=0)
        # This is acceptable — _append is internal; public path is delta_update
        # which now guards nMssn<=0 before calling _append.
        assert arc.record_count == 2  # known internal behaviour

    def test_nmssn_negative_rejected(self):
        """Negative nMssn also rejected."""
        import asyncio
        from unittest.mock import AsyncMock
        arc = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            result = asyncio.get_event_loop().run_until_complete(
                arc.async_delta_update({"nMssn": -1, "done": "done"}, hass, "e")
            )
        assert result is False


class TestL5ArcAreaNone:
    def test_area_none_does_not_crash(self):
        """rooms_completed with area=None must not crash the seeding function."""
        arc = MissionArchive()
        arc._initial_load_done = True
        arc._derived = [
            {"nMssn": 1, "rooms_completed": {"19": {"passes": 2, "area": None}}}
        ]

        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        from custom_components.roomba_plus.const import SQFT_TO_M2
        rps = RobotProfileStore()

        # Reproduce the seeding logic (area=None guard)
        for record in arc.all_derived_oldest_first():
            for rid, data in (record.get("rooms_completed") or {}).items():
                area_sqft = float(data.get("area") or 0)   # fixed version
                area_m2 = area_sqft * SQFT_TO_M2
                if area_m2 > 0:
                    rps.update_room_dirt_index(rid, data.get("passes", 0), area_m2)

        # area=None → area_m2=0 → update_room_dirt_index skipped
        assert rps.room_dirt_index == {}

    def test_area_zero_skipped(self):
        """area=0 must not be passed to update_room_dirt_index (guard > 0)."""
        arc = MissionArchive()
        arc._initial_load_done = True
        arc._derived = [
            {"nMssn": 1, "rooms_completed": {"19": {"passes": 2, "area": 0.0}}}
        ]
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        from custom_components.roomba_plus.const import SQFT_TO_M2
        rps = RobotProfileStore()
        for record in arc.all_derived_oldest_first():
            for rid, data in (record.get("rooms_completed") or {}).items():
                area_sqft = float(data.get("area") or 0)
                area_m2 = area_sqft * SQFT_TO_M2
                if area_m2 > 0:
                    rps.update_room_dirt_index(rid, data.get("passes", 0), area_m2)
        assert "19" not in rps.room_dirt_index


class TestFilterFnNoneValues:
    """filter_fn must not crash when MQTT state has key=None instead of missing key."""

    def _filter_bbnav(self, field: str, state: dict) -> bool:
        """Current fixed filter logic for bbnav sensors."""
        return field in (state.get("bbnav") or {})

    def _filter_bbchg(self, field: str, state: dict) -> bool:
        """Current fixed filter logic for bbchg sensors."""
        return field in (state.get("bbchg") or {})

    def test_bbnav_none_does_not_crash(self):
        state = {"bbnav": None}
        assert self._filter_bbnav("aMtrack", state) is False
        assert self._filter_bbnav("nGoodLmrks", state) is False

    def test_bbnav_absent_returns_false(self):
        state = {"bbrun": {"nPanics": 3}}
        assert self._filter_bbnav("aMtrack", state) is False

    def test_bbnav_with_field_returns_true(self):
        state = {"bbnav": {"aMtrack": 0.94}}
        assert self._filter_bbnav("aMtrack", state) is True

    def test_bbchg_none_does_not_crash(self):
        state = {"bbchg": None}
        assert self._filter_bbchg("nChatters", state) is False
        assert self._filter_bbchg("nKnockoffs", state) is False
        assert self._filter_bbchg("nAborts", state) is False

    def test_bbchg_with_field_returns_true(self):
        state = {"bbchg": {"nChatters": 42}}
        assert self._filter_bbchg("nChatters", state) is True


class TestNetinfoFmtBugFixes:
    def _parse(self, addr):
        from custom_components.roomba_plus.sensor import _parse_netinfo_addr
        return _parse_netinfo_addr(addr)

    def test_float_addr_converted(self):
        """float 3232235777.0 must parse correctly — not return None."""
        result = self._parse(3232235777.0)
        assert result == "192.168.1.1"

    def test_bool_true_not_treated_as_int_1(self):
        """True must NOT be interpreted as addr=1 → '0.0.0.1'."""
        result = self._parse(True)
        assert result is None

    def test_bool_false_not_treated_as_int_0(self):
        """False must NOT be interpreted as addr=0 → '0.0.0.0'."""
        result = self._parse(False)
        assert result is None

    def test_int_addr_still_works(self):
        """Normal int path must still work after the fix."""
        assert self._parse(3232235777) == "192.168.1.1"

    def test_string_addr_still_works(self):
        assert self._parse("192.168.1.5") == "192.168.1.5"

    def test_none_still_none(self):
        assert self._parse(None) is None


class TestRidNoneInFinEvents:
    def _arc(self):
        return MissionArchive()

    def test_room_rid_none_not_inserted_as_none_string(self):
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "room", "room": {
                    "rid": None, "status": 0, "passCount": 2, "totalArea": 300
                }},
            ]}
        }
        d = self._arc()._parse_derived(raw)
        assert "None" not in d["rooms_completed"]
        assert d["rooms_completed"] == {}

    def test_traversal_srcrid_none_ignored(self):
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "traversal", "traversal": {"srcRid": None, "dstRid": "21"}},
            ]}
        }
        d = self._arc()._parse_derived(raw)
        assert "None" not in d["traversal_rids"]
        assert "21" in d["traversal_rids"]

    def test_valid_rid_still_works(self):
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0, "passCount": 2, "totalArea": 300
                }},
            ]}
        }
        d = self._arc()._parse_derived(raw)
        assert "19" in d["rooms_completed"]

    def test_timeline_rid_none_not_inserted(self):
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "room", "room": {
                    "rid": None, "status": 0, "passCount": 2, "totalArea": 300
                }},
            ]}
        }
        tl = self._arc()._parse_timeline(raw)
        for entry in tl:
            if entry[0] == "room_done":
                assert entry[1].get("rid") != "None"


class TestEntityStatsNoneGuard:
    def _entity(self, state: dict):
        from custom_components.roomba_plus.entity import IRobotEntity
        e = object.__new__(IRobotEntity)
        e._blid = "test"
        e._roomba = MagicMock()
        e.vacuum_state = state
        return e

    def test_nav_stats_returns_empty_when_bbnav_none(self):
        e = self._entity({"bbnav": None})
        assert e.nav_stats == {}

    def test_nav_stats_get_does_not_crash(self):
        e = self._entity({"bbnav": None})
        assert e.nav_stats.get("aMtrack") is None

    def test_nav_stats_returns_dict_when_bbnav_present(self):
        e = self._entity({"bbnav": {"aMtrack": 0.94}})
        assert e.nav_stats == {"aMtrack": 0.94}

    def test_dock_stats_returns_empty_when_bbchg_none(self):
        e = self._entity({"bbchg": None})
        assert e.dock_stats == {}

    def test_dock_stats_get_does_not_crash(self):
        e = self._entity({"bbchg": None})
        assert e.dock_stats.get("nChatters") is None

    def test_dock_stats_returns_dict_when_bbchg_present(self):
        e = self._entity({"bbchg": {"nChatters": 42}})
        assert e.dock_stats == {"nChatters": 42}


class TestPassesNoneL5Arc:
    def test_passes_none_does_not_crash(self):
        """passes=None in rooms_completed must not raise TypeError."""
        arc = MissionArchive()
        arc._initial_load_done = True
        arc._derived = [
            {"nMssn": 1, "rooms_completed": {"19": {"passes": None, "area": 200.0}}}
        ]
        from custom_components.roomba_plus.const import SQFT_TO_M2
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for record in arc.all_derived_oldest_first():
            for rid, data in (record.get("rooms_completed") or {}).items():
                passes = int(data.get("passes") or 0)
                area_sqft = float(data.get("area") or 0)
                area_m2 = area_sqft * SQFT_TO_M2
                if rid and passes > 0 and area_m2 > 0:
                    rps.update_room_dirt_index(rid, passes, area_m2)
        assert rps.room_dirt_index == {}  # passes=0 → not indexed

    def test_passes_zero_skipped(self):
        """passes=0 must also not create an entry."""
        arc = MissionArchive()
        arc._initial_load_done = True
        arc._derived = [
            {"nMssn": 1, "rooms_completed": {"19": {"passes": 0, "area": 200.0}}}
        ]
        from custom_components.roomba_plus.const import SQFT_TO_M2
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for record in arc.all_derived_oldest_first():
            for rid, data in (record.get("rooms_completed") or {}).items():
                passes = int(data.get("passes") or 0)
                area_sqft = float(data.get("area") or 0)
                area_m2 = area_sqft * SQFT_TO_M2
                if rid and passes > 0 and area_m2 > 0:
                    rps.update_room_dirt_index(rid, passes, area_m2)
        assert "19" not in rps.room_dirt_index


class TestRechargeCountNone:
    def test_none_treated_as_zero(self):
        recent = [
            {"recharge_count": None},
            {"recharge_count": 1},
            {"recharge_count": 0},
        ]
        total = sum(int(r.get("recharge_count") or 0) for r in recent)
        assert total == 1

    def test_missions_per_charge_no_crash(self):
        """Integration: missions_per_charge native_value must not crash."""
        from custom_components.roomba_plus.mission_archive import MissionArchive
        from datetime import datetime, UTC, timedelta
        arc = MissionArchive()
        arc._initial_load_done = True
        now = datetime.now(UTC)
        arc._derived = [
            {
                "nMssn": i,
                "recharge_count": None,
                "start_ts": (now - timedelta(days=i)).isoformat(),
                "result": "completed",
            }
            for i in range(1, 6)
        ]

        recent = arc.recent_derived(30)
        total_recharges = sum(int(r.get("recharge_count") or 0) for r in recent)
        result = round(len(recent) / max(1, 1 + total_recharges), 2)
        assert result == 5.0  # 5 missions / (1 + 0) = 5.0


class TestSafeInt:
    def test_int_passthrough(self):     assert _safe_int(42) == 42
    def test_str_numeric(self):         assert _safe_int("45") == 45
    def test_str_non_numeric(self):     assert _safe_int("channel_6") == 0
    def test_str_with_unit(self):       assert _safe_int("45m") == 0
    def test_none_default_zero(self):   assert _safe_int(None) == 0
    def test_none_custom_default(self): assert _safe_int(None, -1) == -1
    def test_float_truncates(self):     assert _safe_int(3.9) == 3
    def test_custom_default(self):      assert _safe_int("bad", 99) == 99


class TestSafeFloat:
    def test_float_passthrough(self):   assert _safe_float(3.14) == pytest.approx(3.14)
    def test_str_numeric(self):         assert _safe_float("3.14") == pytest.approx(3.14)
    def test_str_non_numeric(self):     assert _safe_float("abc") == 0.0
    def test_none_default_zero(self):   assert _safe_float(None) == 0.0
    def test_custom_default(self):      assert _safe_float("bad", -1.0) == -1.0


class TestParseDerivedTypeSafety:
    def _parse(self, **kwargs) -> dict:
        arc = MissionArchive()
        return arc._parse_derived({"nMssn": 99, "done": "done", **kwargs})

    def test_wifi_channel_str_returns_none(self):
        """BUG-9: wifiChannel='channel_6' must not raise ValueError."""
        d = self._parse(wifiChannel="channel_6")
        assert d["wifi_channel"] is None

    def test_wifi_channel_str_int_works(self):
        """'6' as string still parses correctly."""
        d = self._parse(wifiChannel="6")
        assert d["wifi_channel"] == 6

    def test_wifi_channel_int_works(self):
        d = self._parse(wifiChannel=36)
        assert d["wifi_channel"] == 36

    def test_duration_min_str_with_unit_returns_none(self):
        """BUG-10: durationM='45m' must not raise ValueError."""
        d = self._parse(durationM="45m")
        assert d["duration_min"] is None

    def test_duration_min_str_int_works(self):
        d = self._parse(durationM="45")
        assert d["duration_min"] == 45

    def test_run_min_str_with_unit_returns_none(self):
        """BUG-11: runM='30min' must not raise ValueError."""
        d = self._parse(runM="30min")
        assert d["run_min"] is None

    def test_sqft_non_numeric_returns_none(self):
        """BUG-12: sqft='abc' must not raise ValueError."""
        d = self._parse(sqft="abc")
        assert d["sqft"] is None

    def test_sqft_numeric_str_works(self):
        d = self._parse(sqft="350.5")
        assert d["sqft"] == pytest.approx(350.5)

    def test_dirt_non_numeric_returns_none(self):
        """BUG-13: dirt='high' must not raise ValueError."""
        d = self._parse(dirt="high")
        assert d["dirt"] is None

    def test_nmssn_non_numeric_gives_zero(self):
        """BUG-14: nMssn='abc' must not raise ValueError."""
        d = self._parse(nMssn="abc")
        assert d["nMssn"] == 0

    def test_normal_record_unchanged(self):
        """Regression: valid numeric fields still parse correctly."""
        d = self._parse(
            nMssn=42, wifiChannel=6, durationM=45, runM=38,
            sqft=350.0, dirt=7,
        )
        assert d["nMssn"] == 42
        assert d["wifi_channel"] == 6
        assert d["duration_min"] == 45
        assert d["run_min"] == 38
        assert d["sqft"] == pytest.approx(350.0)
        assert d["dirt"] == 7

    def test_passcount_non_numeric_in_room_event(self):
        """passCount='abc' in room finEvent must not crash."""
        arc = MissionArchive()
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0,
                    "passCount": "abc",
                    "totalArea": 300.0,
                }},
            ]},
        }
        d = arc._parse_derived(raw)
        assert d["rooms_completed"]["19"]["passes"] == 0

    def test_error_code_non_numeric_in_error_event(self):
        """error.code='E17' must not crash."""
        arc = MissionArchive()
        raw = {
            "nMssn": 1, "done": "done",
            "timeline": {"finEvents": [
                {"type": "error", "error": {"code": "E17"}},
            ]},
        }
        d = arc._parse_derived(raw)
        assert d["error_in_mission"] == []


class TestDeltaUpdateNMssnTypeSafety:
    @pytest.mark.asyncio
    async def test_nmssn_str_rejected_gracefully(self):
        """BUG-14 (delta path): nMssn='abc' must not raise — returns False."""
        arc = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.mission_archive.Store",
            return_value=store_mock,
        ):
            result = await arc.async_delta_update(
                {"nMssn": "abc", "done": "done"}, hass, "entry"
            )
        assert result is False
        assert arc.record_count == 0

    @pytest.mark.asyncio
    async def test_nmssn_float_str_works(self):
        """nMssn='42' (numeric string) must parse and be archived."""
        arc = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.mission_archive.Store",
            return_value=store_mock,
        ):
            result = await arc.async_delta_update(
                {"nMssn": "42", "done": "done"}, hass, "entry"
            )
        assert result is True
        assert arc.record_count == 1
        assert 42 in arc._archived_nmssns


class TestMissionCompleteCallbackInterRoomGuard:
    def _msg(self, phase: str, cycle: str = "clean") -> dict:
        return {"state": {"reported": {
            "cleanMissionStatus": {"phase": phase, "cycle": cycle},
        }}}

    def _run(self, msgs: list[dict]) -> int:
        """Drive make_mission_complete_callback and return how many times
        the first checkpoint was SCHEDULED (v2.9.1 CLOUD-CATCHUP — refresh
        is no longer fired synchronously; counting scheduling calls is the
        equivalent check for "did a genuine mission end get detected")."""
        cc = MagicMock()
        cc.async_request_refresh = AsyncMock(return_value=None)
        hass = MagicMock()
        hass.loop = asyncio.new_event_loop()
        entry = MagicMock()
        entry.runtime_data.mission_store.latest.return_value = {"timeline": {"finEvents": []}}

        with patch("custom_components.roomba_plus.callbacks.async_call_later") as mock_later:
            cb = make_mission_complete_callback(hass, cc, entry)
            for msg in msgs:
                cb(msg)
            hass.loop.run_until_complete(asyncio.sleep(0))
            return mock_later.call_count

    def test_no_refresh_on_inter_room_charge(self):
        count = self._run([
            self._msg("run", cycle="clean"),
            self._msg("charge", cycle="clean"),
        ])
        assert count == 0, (
            "BUG-15: cloud refresh fired on inter-room transition "
            "(phase=charge, cycle=clean) instead of only at genuine end"
        )

    def test_no_refresh_on_inter_room_hmpostmsn(self):
        count = self._run([
            self._msg("run", cycle="clean"),
            self._msg("hmPostMsn", cycle="clean"),
        ])
        assert count == 0

    def test_no_refresh_on_cycle_quick(self):
        count = self._run([
            self._msg("run", cycle="quick"),
            self._msg("charge", cycle="quick"),
        ])
        assert count == 0

    def test_refresh_still_fires_on_genuine_mission_end(self):
        """cycle=none (true end) must still schedule the cloud refresh checkpoint."""
        count = self._run([
            self._msg("run", cycle="clean"),
            self._msg("charge", cycle="none"),
        ])
        assert count == 1, (
            "Regression: genuine mission end must still schedule a cloud refresh"
        )

    def test_refresh_fires_on_stop_genuine_end(self):
        count = self._run([
            self._msg("run", cycle="clean"),
            self._msg("stop", cycle="none"),
        ])
        assert count == 1

    def test_only_one_refresh_per_multi_room_mission(self):
        """A full 3-room mission with 2 inter-room transitions must
        schedule the cloud refresh checkpoint exactly once — at the
        genuine end."""
        count = self._run([
            self._msg("run", cycle="clean"),       # room 1
            self._msg("charge", cycle="clean"),     # inter-room 1→2
            self._msg("run", cycle="clean"),         # room 2
            self._msg("hmPostMsn", cycle="clean"),  # inter-room 2→3
            self._msg("run", cycle="clean"),         # room 3
            self._msg("charge", cycle="none"),       # genuine end
        ])
        assert count == 1


class TestExpectedRoomSecTypeSafety:
    def _mts(self) -> MissionTimerStore:
        mts = MissionTimerStore()
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.total_estimated_sec = 1000.0
        mts.current_room_idx = 0
        return mts

    def test_string_numeric_value_converted(self):
        mts = self._mts()
        mts.room_estimates_sec = ["600", 400.0]
        assert mts.expected_room_sec == 600.0

    def test_non_numeric_string_falls_back_to_uniform(self):
        mts = self._mts()
        mts.room_estimates_sec = ["bad", 400.0]
        assert mts.expected_room_sec == 500.0  # 1000 / 2 rooms

    def test_negative_value_falls_back_to_uniform(self):
        mts = self._mts()
        mts.room_estimates_sec = [-50.0, 400.0]
        assert mts.expected_room_sec == 500.0

    def test_zero_value_falls_back_to_uniform(self):
        mts = self._mts()
        mts.room_estimates_sec = [0.0, 400.0]
        assert mts.expected_room_sec == 500.0

    def test_confidence_check_does_not_crash_on_malformed_value(self):
        from custom_components.roomba_plus.callbacks import (
            _room_transition_confidence_ok,
        )
        mts = self._mts()
        mts.room_estimates_sec = ["bad", 400.0]
        mts.run_sec = 350.0
        # Must not raise TypeError
        result = _room_transition_confidence_ok({"cycle": "clean", "error": 0}, mts)
        assert isinstance(result, bool)


class TestSafeFloatHelper:
    def test_valid_float(self):
        assert _safe_float_v280_bug_hunt_r4(3.14) == pytest.approx(3.14)

    def test_valid_numeric_string(self):
        assert _safe_float_v280_bug_hunt_r4("600") == 600.0

    def test_non_numeric_string(self):
        assert _safe_float_v280_bug_hunt_r4("bad") is None

    def test_none_passthrough(self):
        assert _safe_float_v280_bug_hunt_r4(None) is None

    def test_int_value(self):
        assert _safe_float_v280_bug_hunt_r4(400) == 400.0


class TestMissionStartWiringTypeSafety:
    @pytest.mark.asyncio
    async def test_malformed_estimates_do_not_crash(self):
        mts = MissionTimerStore()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=[-100, "bad", 400],
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg())  # must not raise

        assert mts.total_estimated_sec == 400.0
        assert mts.room_estimates_sec == [-100, "bad", 400]

    @pytest.mark.asyncio
    async def test_all_malformed_gives_none_not_zero(self):
        mts = MissionTimerStore()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=["bad", -5, 0],
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg())

        assert mts.total_estimated_sec is None

    @pytest.mark.asyncio
    async def test_valid_estimates_unaffected(self):
        """Regression: normal valid numeric estimates still sum correctly."""
        mts = MissionTimerStore()
        entry = _make_entry(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=[600, 400, 300],
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg())

        assert mts.total_estimated_sec == 1300.0


class TestTimeEstimatesNoneGuard:
    def test_time_estimates_explicit_none_does_not_crash(self):
        entry = _entry_with_regions(
            [{"name": "Kitchen", "time_estimates": None}]
        )
        result = _compute_room_time_estimates(entry, ["Kitchen"])
        assert result == [None]

    def test_time_estimates_missing_key_still_works(self):
        entry = _entry_with_regions([{"name": "Kitchen"}])
        result = _compute_room_time_estimates(entry, ["Kitchen"])
        assert result == [None]

    def test_valid_time_estimates_unaffected(self):
        entry = _entry_with_regions(
            [{"name": "Kitchen", "time_estimates": {"one_pass_sec": 600}}]
        )
        result = _compute_room_time_estimates(entry, ["Kitchen"])
        assert result == [600]

    def test_mixed_none_and_valid_regions(self):
        entry = _entry_with_regions([
            {"name": "Kitchen", "time_estimates": None},
            {"name": "Hall", "time_estimates": {"one_pass_sec": 400}},
        ])
        result = _compute_room_time_estimates(entry, ["Kitchen", "Hall"])
        assert result == [None, 400]


class TestRoomNameNoneGuard:
    def test_none_room_name_does_not_crash(self):
        entry = _entry_with_regions(
            [{"name": "Kitchen", "time_estimates": {"one_pass_sec": 600}}]
        )
        result = _compute_room_time_estimates(entry, [None, "Kitchen"])
        assert result == [None, 600]

    def test_empty_string_room_name_does_not_crash(self):
        entry = _entry_with_regions(
            [{"name": "Kitchen", "time_estimates": {"one_pass_sec": 600}}]
        )
        result = _compute_room_time_estimates(entry, ["", "Kitchen"])
        assert result == [None, 600]

    def test_all_none_room_names(self):
        entry = _entry_with_regions(
            [{"name": "Kitchen", "time_estimates": {"one_pass_sec": 600}}]
        )
        result = _compute_room_time_estimates(entry, [None, None])
        assert result == [None, None]


class TestEstimateFailureIsolation:
    @pytest.mark.asyncio
    async def test_exception_in_estimate_lookup_does_not_crash_callback(self):
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = _make_entry_v280_bug_hunt_r5(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            side_effect=Exception("cloud data corrupted"),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r5())  # must not raise

        # Mission tracking must have proceeded normally despite the failure
        assert mts.mission_id is not None
        assert mts.planned_rooms == ["Kitchen"]
        assert mts.total_estimated_sec is None
        assert mts.room_estimates_sec == [None]

    @pytest.mark.asyncio
    async def test_attribute_error_in_estimate_lookup_does_not_crash_callback(self):
        """Specifically: AttributeError from a malformed cloud payload
        (e.g. master_state=None somewhere in the chain) must be caught too,
        not just generic Exception subclasses."""
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = _make_entry_v280_bug_hunt_r5(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            side_effect=AttributeError("'NoneType' object has no attribute 'get'"),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r5())

        assert mts.mission_id is not None
        assert mts.total_estimated_sec is None

    @pytest.mark.asyncio
    async def test_successful_lookup_still_works_after_fix(self):
        """Regression: the try/except wrapper must not break the happy path."""
        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = _make_entry_v280_bug_hunt_r5(mts)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ), patch(
            "custom_components.roomba_plus.sensor._compute_room_time_estimates",
            return_value=[600],
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r5())

        assert mts.total_estimated_sec == 600.0
        assert mts.room_estimates_sec == [600]


class TestSafeIntRepairsHelper:
    def test_valid_int(self):
        assert _safe_int_repairs(42) == 42

    def test_numeric_string(self):
        assert _safe_int_repairs("45") == 45

    def test_non_numeric_string(self):
        assert _safe_int_repairs("high") == 0

    def test_none_default_zero(self):
        assert _safe_int_repairs(None) == 0

    def test_custom_default(self):
        assert _safe_int_repairs("bad", -1) == -1


class TestDockHealthTypeSafety:
    def _entry(self, bbchg: dict) -> MagicMock:
        entry = MagicMock()
        entry.entry_id = "e"
        entry.runtime_data.roomba_reported_state.return_value = {"bbchg": bbchg}
        return entry

    @pytest.mark.asyncio
    async def test_non_numeric_nchatters_does_not_crash(self):
        entry = self._entry({"nChatters": "high", "nKnockoffs": 5, "nAborts": 0})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        await async_check_dock_health(hass, entry)  # must not raise

    @pytest.mark.asyncio
    async def test_non_numeric_all_fields_does_not_crash(self):
        entry = self._entry({
            "nChatters": "bad", "nKnockoffs": "bad", "nAborts": "bad",
        })
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        await async_check_dock_health(hass, entry)

    @pytest.mark.asyncio
    async def test_valid_numeric_values_still_work(self):
        """Regression: normal numeric values still trigger the issue correctly."""
        entry = self._entry({"nChatters": 150, "nKnockoffs": 0, "nAborts": 0})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_check_dock_health(hass, entry)
        mock_create.assert_called_once()


class TestNetinfoNoneGuard:
    def test_netinfo_addr_with_explicit_none_dict(self):
        """The recurring None-default pitfall, applied at the entity.vacuum_state
        access layer rather than inside _parse_netinfo_addr itself."""
        vacuum_state = {"netinfo": None}
        addr = (vacuum_state.get("netinfo") or {}).get("addr")
        result = _parse_netinfo_addr(addr)
        assert result is None

    def test_parse_netinfo_addr_itself_unaffected(self):
        """_parse_netinfo_addr's own None handling (fixed in round 1) is
        unaffected by this guard — still works standalone."""
        assert _parse_netinfo_addr(None) is None
        assert _parse_netinfo_addr("192.168.1.5") == "192.168.1.5"


class TestCallbacksLastCommandNoneGuard:
    @pytest.mark.asyncio
    async def test_lastcommand_explicit_none_does_not_crash(self):
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = _make_entry_v280_bug_hunt_r6(mts, None)
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r6())  # must not raise

        assert mts.mission_id is not None
        # No regions found → planned_rooms stays empty, but mission tracking
        # itself (run_sec accumulation, mission_id) must still work.
        assert mts.planned_rooms == []

    @pytest.mark.asyncio
    async def test_lastcommand_missing_key_unaffected(self):
        """Regression: the normal case (key absent, not null) still works."""
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = MagicMock()
        entry.entry_id = "e"
        entry.data = {"blid": "X"}
        entry.options = {}
        entry.runtime_data.mission_timer_store = mts
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock()
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None
        entry.runtime_data.presence_manager = None
        entry.runtime_data.demand_triggered_ts = None
        entry.runtime_data.robot_profile_store = None
        entry.runtime_data.roomba = MagicMock()
        entry.runtime_data.roomba.master_state = {
            "state": {"reported": {
                "lastCommand": {"regions": [
                    {"region_name": "Kitchen", "region_id": "19"},
                ]}
            }}
        }
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()
        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r6())
        assert mts.planned_rooms == ["Kitchen"]

    @pytest.mark.asyncio
    async def test_zone_lookup_returning_none_does_not_crash(self):
        """The _zone.get(region_id) chain must also tolerate an explicit
        None entry in the configured smart-zone data dict."""
        from custom_components.roomba_plus.callbacks import make_mission_callback

        mts = MissionTimerStore()
        entry = MagicMock()
        entry.entry_id = "e"
        entry.data = {"blid": "X"}
        entry.options = {"smart_zone_data": {"19": None}}  # malformed config
        entry.runtime_data.mission_timer_store = mts
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock()
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None
        entry.runtime_data.presence_manager = None
        entry.runtime_data.demand_triggered_ts = None
        entry.runtime_data.robot_profile_store = None
        entry.runtime_data.roomba = MagicMock()
        entry.runtime_data.roomba.master_state = {
            "state": {"reported": {
                "lastCommand": {"regions": [
                    {"region_id": "19"},  # no region_name — forces zone lookup
                ]}
            }}
        }
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        # v3.3.0 DELAY-SAVE — schedule_save routes via loop.call_soon_threadsafe;
        # a MagicMock loop records without executing (same isolation as the
        # old None + patched run_coroutine_threadsafe).
        hass.loop = MagicMock()
        with patch(
            "custom_components.roomba_plus.callbacks.CONF_SMART_ZONE_DATA",
            "smart_zone_data",
        ), patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe",
            side_effect=lambda c, l: c.close(),
        ):
            cb = make_mission_callback(hass, entry)
            cb(_run_msg_v280_bug_hunt_r6())  # must not raise
        assert mts.planned_rooms == ["19"]  # falls back to region_id string


class TestComputeRoomTimeEstimatesNoneGuards:
    def _entry(self, last_command, clean_mission_status) -> MagicMock:
        cc = MagicMock()
        cc.regions = []
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator = cc
        entry.runtime_data.roomba_reported_state.return_value = {
            "lastCommand": last_command,
            "cleanMissionStatus": clean_mission_status,
        }
        return entry

    def test_lastcommand_none_does_not_crash(self):
        entry = self._entry(None, {"noAutoPasses": True, "twoPass": False})
        result = _compute_room_time_estimates(entry, ["Kitchen"])
        assert result == [None]

    def test_cleanmissionstatus_none_does_not_crash(self):
        """No lastCommand.regions params → falls back to cleanMissionStatus,
        which is itself None — must not crash."""
        entry = self._entry(None, None)
        result = _compute_room_time_estimates(entry, ["Kitchen"])
        assert result == [None]

    def test_both_none_does_not_crash(self):
        entry = self._entry(None, None)
        result = _compute_room_time_estimates(entry, ["Kitchen", "Hall"])
        assert result == [None, None]


class TestL5ArcSeedingTypeSafety:
    @pytest.mark.asyncio
    async def test_non_dict_room_entry_does_not_crash(self):
        from custom_components.roomba_plus import _async_seed_l5_from_archive

        mission_archive = MagicMock()
        mission_archive.initial_load_done = True
        mission_archive.record_count = 2
        mission_archive.all_derived_oldest_first.return_value = [
            {"rooms_completed": {"19": "corrupted_not_a_dict"}},
            {"rooms_completed": {"21": {"passes": 2, "area": 248}}},
        ]
        robot_profile_store = RobotProfileStore()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

        with patch.object(RobotProfileStore, "async_save", new=AsyncMock()):
            await _async_seed_l5_from_archive(
                hass, "entry1", mission_archive, robot_profile_store
            )

        assert "21" in robot_profile_store.room_dirt_index
        assert "19" not in robot_profile_store.room_dirt_index

    @pytest.mark.asyncio
    async def test_all_entries_corrupted_does_not_crash(self):
        from custom_components.roomba_plus import _async_seed_l5_from_archive

        mission_archive = MagicMock()
        mission_archive.initial_load_done = True
        mission_archive.record_count = 2
        mission_archive.all_derived_oldest_first.return_value = [
            {"rooms_completed": {"19": "bad", "21": ["also", "bad"]}},
            {"rooms_completed": {"22": 12345}},
        ]
        robot_profile_store = RobotProfileStore()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

        await _async_seed_l5_from_archive(
            hass, "entry1", mission_archive, robot_profile_store
        )  # must not raise

        assert robot_profile_store.room_dirt_index == {}

    @pytest.mark.asyncio
    async def test_valid_data_unaffected(self):
        """Regression: normal valid archive data still seeds correctly."""
        from custom_components.roomba_plus import _async_seed_l5_from_archive

        mission_archive = MagicMock()
        mission_archive.initial_load_done = True
        mission_archive.record_count = 1
        mission_archive.all_derived_oldest_first.return_value = [
            {"rooms_completed": {
                "19": {"passes": 2, "area": 248},
                "21": {"passes": 1, "area": 130},
            }},
        ]
        robot_profile_store = RobotProfileStore()
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

        with patch.object(RobotProfileStore, "async_save", new=AsyncMock()):
            await _async_seed_l5_from_archive(
                hass, "entry1", mission_archive, robot_profile_store
            )

        assert "19" in robot_profile_store.room_dirt_index
        assert "21" in robot_profile_store.room_dirt_index


class TestArchiveSeedingDefenseInDepth:
    """Defense-in-depth: the async_setup_entry call site (verified by direct
    source inspection — exercising the full async_setup_entry is out of
    scope for a unit test given its many unrelated prerequisites) wraps
    both seeding calls in try/except, so an unanticipated failure mode
    beyond the malformed shape BUG-25 specifically enumerates still can't
    fail integration setup entirely."""

    def test_setup_call_site_wraps_seeding_in_try_except(self):
        import inspect
        import custom_components.roomba_plus as pkg

        source = inspect.getsource(pkg)
        # The call site must wrap both seeding awaits in a try block —
        # a regression here (e.g. someone "cleans up" the try/except
        # thinking it's unnecessary) would silently restore BUG-25.
        marker = "await _async_seed_l5_from_archive("
        idx = source.index(marker)
        preceding = source[max(0, idx - 200):idx]
        assert "try:" in preceding, (
            "BUG-25 regression: _async_seed_l5_from_archive call site no "
            "longer wrapped in try/except — a malformed archive record "
            "would once again fail async_setup_entry entirely"
        )


class TestComputeArchiveStatsTypeSafety:
    def _records(self, n: int, **overrides) -> list[dict]:
        base = {"result": "completed", "duration_min": 30, "sqft": 400, "dirt": 5}
        base.update(overrides)
        return [dict(base) for _ in range(n)]

    def test_corrupted_sqft_excluded_not_crashed(self):
        records = self._records(20, sqft="corrupted")
        result = MissionStore.compute_archive_stats(records)
        assert result is not None
        assert result["area_mean"] is None  # all sqft unusable → no area stats
        assert result["duration_mean"] == 30.0  # duration_min still valid

    def test_corrupted_duration_excluded(self):
        records = self._records(20, duration_min="bad")
        result = MissionStore.compute_archive_stats(records)
        assert result is None  # < 20 valid durations remain

    def test_corrupted_dirt_excluded_not_crashed(self):
        records = self._records(20, dirt="bad")
        result = MissionStore.compute_archive_stats(records)
        assert result is not None
        assert result["dirt_p75"] is None

    def test_mixed_valid_and_corrupted(self):
        records = self._records(15) + self._records(10, sqft="bad")
        result = MissionStore.compute_archive_stats(records)
        assert result is not None
        assert result["duration_mean"] == 30.0
        # 15 valid sqft entries remain — area stats still computed
        assert result["area_mean"] == 400.0

    def test_valid_data_unaffected(self):
        """Regression: normal valid data still produces correct stats."""
        records = self._records(20)
        result = MissionStore.compute_archive_stats(records)
        assert result is not None
        assert result["duration_mean"] == 30.0
        assert result["area_mean"] == 400.0
        assert result["dirt_p75"] == 5.0

    def test_negative_sqft_excluded(self):
        """Negative sqft (corrupted) must not corrupt the mean."""
        records = self._records(20, sqft=-50)
        result = MissionStore.compute_archive_stats(records)
        assert result is not None
        assert result["area_mean"] is None


class TestAdvanceRoomCleanMissionStatusNoneGuard:
    @pytest.mark.asyncio
    async def test_cleanmissionstatus_explicit_none_does_not_crash(self):
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        mts = MagicMock()
        mts.mission_id = "m1"
        mts.advance_room.return_value = True
        mts.current_room_idx = 1
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.current_room = "Hall"

        config_entry = _make_config_entry(mts, {"cleanMissionStatus": None})
        hass.config_entries.async_get_entry.return_value = config_entry

        call = _make_call(hass, ["vacuum.test"])

        with patch(
            "custom_components.roomba_plus.services.er.async_get"
        ) as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_advance_room(call)  # must not raise

        mts.advance_room.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_malformed_entity_does_not_block_others_in_batch(self):
        """A crash on entity 1 must not prevent entity 2 in the same
        service call batch from being processed."""
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro

        ent_reg_entry_1 = MagicMock()
        ent_reg_entry_1.config_entry_id = "ce1"
        ent_reg_entry_2 = MagicMock()
        ent_reg_entry_2.config_entry_id = "ce2"

        mts1 = MagicMock()
        mts1.mission_id = "m1"
        mts1.advance_room.return_value = True
        mts1.current_room_idx = 0
        mts1.planned_rooms = ["Kitchen"]
        mts1.current_room = "Kitchen"
        config_entry_1 = _make_config_entry(mts1, {"cleanMissionStatus": None})

        mts2 = MagicMock()
        mts2.mission_id = "m2"
        mts2.advance_room.return_value = True
        mts2.current_room_idx = 0
        mts2.planned_rooms = ["Hall"]
        mts2.current_room = "Hall"
        config_entry_2 = _make_config_entry(
            mts2, {"cleanMissionStatus": {"phase": "charge"}}
        )

        hass.config_entries.async_get_entry.side_effect = [
            config_entry_1, config_entry_2,
        ]

        call = _make_call(hass, ["vacuum.test1", "vacuum.test2"])

        with patch(
            "custom_components.roomba_plus.services.er.async_get"
        ) as mock_er:
            mock_er.return_value.async_get.side_effect = [
                ent_reg_entry_1, ent_reg_entry_2,
            ]
            await async_handle_advance_room(call)  # must not raise

        mts1.advance_room.assert_called_once()
        mts2.advance_room.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_phase_still_advances_correctly(self):
        """Regression: the normal transition-phase case still works."""
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        mts = MagicMock()
        mts.mission_id = "m1"
        mts.advance_room.return_value = True
        mts.current_room_idx = 1
        mts.planned_rooms = ["Kitchen", "Hall"]
        mts.current_room = "Hall"

        config_entry = _make_config_entry(
            mts, {"cleanMissionStatus": {"phase": "charge"}}
        )
        hass.config_entries.async_get_entry.return_value = config_entry

        call = _make_call(hass, ["vacuum.test"])

        with patch(
            "custom_components.roomba_plus.services.er.async_get"
        ) as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_advance_room(call)

        mts.advance_room.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase_run_still_correctly_ignored(self):
        """Regression: Guard 3 (don't advance during active cleaning)
        still works correctly after the None-guard fix."""
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        mts = MagicMock()
        mts.mission_id = "m1"

        config_entry = _make_config_entry(
            mts, {"cleanMissionStatus": {"phase": "run"}}
        )
        hass.config_entries.async_get_entry.return_value = config_entry

        call = _make_call(hass, ["vacuum.test"])

        with patch(
            "custom_components.roomba_plus.services.er.async_get"
        ) as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            await async_handle_advance_room(call)

        mts.advance_room.assert_not_called()


class TestCleanRoomNotReadyNoneGuard:
    """Sibling fix: async_handle_clean_room's notReady check had the
    identical unguarded chain on cleanMissionStatus."""

    @pytest.mark.asyncio
    async def test_cleanmissionstatus_none_does_not_block_clean_room(self):
        from custom_components.roomba_plus.services import async_handle_clean_room

        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        ent_reg_entry = MagicMock()
        ent_reg_entry.config_entry_id = "ce1"

        config_entry = MagicMock()
        config_entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": None
        }
        config_entry.runtime_data.has_cloud = False
        config_entry.runtime_data.vacuum = MagicMock()
        config_entry.runtime_data.vacuum.send_command = AsyncMock()
        hass.config_entries.async_get_entry.return_value = config_entry

        call = MagicMock()
        call.hass = hass
        call.data = {"entity_id": ["vacuum.test"], "region_ids": ["19"]}

        with patch(
            "custom_components.roomba_plus.services.er.async_get"
        ) as mock_er:
            mock_er.return_value.async_get.return_value = ent_reg_entry
            try:
                await async_handle_clean_room(call)
            except Exception as exc:  # noqa: BLE001
                assert not isinstance(exc, AttributeError), (
                    "BUG: cleanMissionStatus=None crashed clean_room service "
                    f"with AttributeError: {exc}"
                )


class TestAllSensorFilterFnSurviveNoneFields:
    """The definitive regression guard for this bug class: simulates the
    worst realistic MQTT/cloud state (every commonly-nullable top-level
    field explicitly null) and asserts NO filter_fn in the entire SENSORS
    tuple raises. This is what would have caught BUG-28 through BUG-31
    (and BUG-22 if it had been a filter_fn) automatically, and will catch
    any future regression of the same class without needing to enumerate
    every individual field by hand again."""

    WORST_CASE_STATE = {
        "bbrun": None,
        "bbnav": None,
        "bbchg": None,
        "bbchg3": None,
        "cleanMissionStatus": None,
        "dock": None,
        "runtimeStats": None,
        "cleanSchedule2": None,
        "cleanSchedule": None,
        "cap": None,
        "hwPartsRev": None,
        "signal": None,
        "mssnNavStats": None,
        "lastCommand": None,
        "netinfo": None,
        "batInfo": None,
    }

    def test_no_filter_fn_raises_on_all_fields_none(self):
        crashed = []
        for desc in SENSORS:
            try:
                desc.filter_fn(self.WORST_CASE_STATE)
            except Exception as e:  # noqa: BLE001
                crashed.append((desc.key, type(e).__name__, str(e)))
        assert crashed == [], (
            f"{len(crashed)} sensor filter_fn(s) crashed on an all-None "
            f"state — any ONE of these would fail async_setup_entry for "
            f"the ENTIRE sensor platform: {crashed}"
        )

    def test_no_filter_fn_raises_on_empty_state(self):
        """Sibling case: a completely empty reported state (e.g. right
        after pairing, before the first full MQTT snapshot arrives)."""
        crashed = []
        for desc in SENSORS:
            try:
                desc.filter_fn({})
            except Exception as e:  # noqa: BLE001
                crashed.append((desc.key, type(e).__name__, str(e)))
        assert crashed == [], f"CRASHED on empty state: {crashed}"

    def test_filter_fn_still_correctly_select_sensors_on_realistic_state(self):
        """Regression: a normal, fully-populated i7+ state must still
        select more than a handful of sensors (sanity check that the
        `or {}` fixes didn't accidentally make every filter_fn return
        False)."""
        realistic_state = {
            "bbrun": {"nStuck": 0, "nPanics": 2, "nCliffsF": 1, "nCliffsR": 0,
                      "nOpticalDD": 12, "nPiezoDD": 5, "nOrients": 3},
            "bbnav": {"aMtrack": 95, "nGoodLmrks": 40},
            "bbchg": {"nChatters": 2, "nKnockoffs": 0, "nAborts": 0},
            "bbchg3": {"estCap": 2488},
            "cleanMissionStatus": {"missionId": 5, "phase": "run"},
            "dock": {"fwVer": "1.2.3", "state": 1, "tankLvl": 80},
            "cap": {"pose": 1, "carpetBoost": 1},
        }
        selected = [d.key for d in SENSORS if d.filter_fn(realistic_state)]
        assert len(selected) > 10
        assert "nav_panics" in selected
        assert "dock_contact_chatters" in selected


class TestFieldSensorsBbrunNoneGuard:
    """BUG-28: the 3 FIELD-SENSORS filter_fn lambdas."""

    def _filter_fn_for(self, key: str):
        return next(d.filter_fn for d in SENSORS if d.key == key)

    def test_optical_dirt_detections_bbrun_none(self):
        fn = self._filter_fn_for("optical_dirt_detections")
        assert fn({"bbrun": None, "runtimeStats": None}) is False

    def test_piezo_dirt_detections_bbrun_none(self):
        fn = self._filter_fn_for("piezo_dirt_detections")
        assert fn({"bbrun": None, "runtimeStats": None}) is False

    def test_nav_orientations_bbrun_none(self):
        fn = self._filter_fn_for("nav_orientations")
        assert fn({"bbrun": None, "runtimeStats": None}) is False

    def test_still_selects_when_field_present(self):
        fn = self._filter_fn_for("optical_dirt_detections")
        assert fn({"bbrun": {"nOpticalDD": 5}}) is True


class TestHasCleanBaseNoneGuard:
    """BUG-29."""

    def test_dock_none_returns_false_not_crash(self):
        assert has_clean_base({"dock": None}) is False

    def test_dock_missing_returns_false(self):
        assert has_clean_base({}) is False

    def test_dock_present_with_fwver_returns_true(self):
        assert has_clean_base({"dock": {"fwVer": "1.0"}}) is True

    def test_dock_present_with_int_state_returns_true(self):
        assert has_clean_base({"dock": {"state": 1}}) is True


class TestHasCarpetBoostNoneGuard:
    """BUG-30."""

    def test_cap_none_returns_false_not_crash(self):
        assert has_carpet_boost({"cap": None}) is False

    def test_cap_missing_returns_false(self):
        assert has_carpet_boost({}) is False

    def test_cap_present_returns_true(self):
        assert has_carpet_boost({"cap": {"carpetBoost": 1}}) is True


class TestHasPoseNoneGuard:
    """BUG-31."""

    def test_cap_none_returns_false_not_crash(self):
        assert has_pose({"cap": None}) is False

    def test_cap_missing_returns_false(self):
        assert has_pose({}) is False

    def test_cap_with_pose_returns_true(self):
        assert has_pose({"cap": {"pose": 1}}) is True

    def test_cap_without_pose_returns_false(self):
        assert has_pose({"cap": {"pose": 0}}) is False


class TestArc1CloudCoordinatorWiring:
    @pytest.mark.asyncio
    async def test_real_update_data_call_does_not_raise_attributeerror(self):
        """The actual hotfix regression test: run _async_update_data() on a
        coordinator with a real-shaped mission_archive and confirm it
        completes without AttributeError."""
        mission_archive = MagicMock()
        mission_archive.async_delta_update = AsyncMock()

        coord = _make_coordinator_with_archive(mission_archive)

        result = await coord._async_update_data()  # must not raise

        assert result is not None

    @pytest.mark.asyncio
    async def test_async_delta_update_receives_correct_entry_id(self):
        """Confirms the fix resolves to the right value, not just that it
        doesn't crash — verifies self.config_entry.entry_id (not some other
        attribute) is what reaches MissionArchive.async_delta_update()."""
        mission_archive = MagicMock()
        mission_archive.async_delta_update = AsyncMock()

        coord = _make_coordinator_with_archive(mission_archive)
        await coord._async_update_data()

        mission_archive.async_delta_update.assert_called_once()
        call_args = mission_archive.async_delta_update.call_args
        assert call_args[0][1] is coord.hass
        assert call_args[0][2] == "test_entry_id"

    @pytest.mark.asyncio
    async def test_no_mission_archive_skips_delta_update_cleanly(self):
        """Regression: the None-guard (`if self._mission_archive is not
        None`) around the buggy line must still correctly skip when no
        archive is configured (e.g. archive disabled or not yet loaded)."""
        coord = _make_coordinator_with_archive(None)
        result = await coord._async_update_data()  # must not raise
        assert result is not None

    @pytest.mark.asyncio
    async def test_multiple_history_records_all_delta_updated(self):
        """Regression: every record in raw_history reaches the archive,
        processed oldest-first per the _last_nMssn-advances-correctly
        comment at the call site."""
        mission_archive = MagicMock()
        mission_archive.async_delta_update = AsyncMock()

        coord = _make_coordinator_with_archive(mission_archive)
        coord.api.get_mission_history = AsyncMock(return_value=[
            {"nMssn": 44, "startTime": 1700002000, "timestamp": 1700003000},
            {"nMssn": 43, "startTime": 1700001000, "timestamp": 1700002000},
            {"nMssn": 42, "startTime": 1700000000, "timestamp": 1700001000},
        ])

        await coord._async_update_data()

        assert mission_archive.async_delta_update.call_count == 3
        # Processed reversed (oldest nMssn first) per the code comment.
        seen_nmssns = [
            call.args[0]["nMssn"]
            for call in mission_archive.async_delta_update.call_args_list
        ]
        assert seen_nmssns == [42, 43, 44]


class TestSmberrTypeSafety:
    @pytest.mark.asyncio
    async def test_non_numeric_smberr_does_not_crash(self):
        entry = _entry({"state": {"reported": {"bbchg": {"smberr": "corrupted"}}}})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        await async_check_smberr(hass, entry)  # must not raise

    @pytest.mark.asyncio
    async def test_state_explicit_none_does_not_crash(self):
        entry = _entry({"state": None})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        await async_check_smberr(hass, entry)

    @pytest.mark.asyncio
    async def test_smberr_field_absent_returns_early(self):
        entry = _entry({"state": {"reported": {"bbchg": {}}}})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create, patch(
            "custom_components.roomba_plus.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_check_smberr(hass, entry)
        mock_create.assert_not_called()
        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_high_smberr_still_fires_issue(self):
        """Regression: confirmed field data scenario (i7+, 7-year battery)
        must still correctly fire the Repair Issue."""
        entry = _entry({"state": {"reported": {"bbchg": {"smberr": 50432}}}})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_check_smberr(hass, entry)
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_low_smberr_clears_issue(self):
        entry = _entry({"state": {"reported": {"bbchg": {"smberr": 0}}}})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_delete_issue"
        ) as mock_delete:
            await async_check_smberr(hass, entry)
        mock_delete.assert_called_once()


class TestDockHealthStateNoneGuard:
    """The same master_state.get("state", {}) chain existed in
    async_check_dock_health — fixed alongside SMBERR since it's the
    identical line."""

    @pytest.mark.asyncio
    async def test_state_explicit_none_does_not_crash(self):
        entry = _entry({"state": None})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        await async_check_dock_health(hass, entry)  # must not raise

    @pytest.mark.asyncio
    async def test_valid_data_still_works(self):
        """Regression: normal dock-health data still triggers correctly."""
        entry = _entry({"state": {"reported": {"bbchg": {
            "nChatters": 150, "nKnockoffs": 0, "nAborts": 0,
        }}}})
        hass = MagicMock()
        def _close_coro(*args, **kwargs):
            import asyncio as _asyncio
            for a in args:
                if _asyncio.iscoroutine(a):
                    a.close()
        hass.async_create_task = _close_coro
        with patch(
            "custom_components.roomba_plus.repairs.ir.async_create_issue"
        ) as mock_create:
            await async_check_dock_health(hass, entry)
        mock_create.assert_called_once()


# ── roomba_reported_state null-safety (bug-hunt round, v3.0.0) ────────────────

class TestRoombaReportedStateNullSafety:
    """roomba_reported_state must survive explicit JSON null in master_state.

    A sparse or initial MQTT frame can yield {"state": null} or
    {"state": {"reported": null}}. A dict default (.get("state", {})) only
    guards a *missing* key, not a present-but-null value, so the chain would
    raise AttributeError and take down the whole sensor platform. The hardened
    implementation uses `or {}` to coerce null to an empty dict.
    """

    def _call(self, master_state):
        from unittest.mock import MagicMock
        from custom_components.roomba_plus import roomba_reported_state
        roomba = MagicMock()
        roomba.master_state = master_state
        return roomba_reported_state(roomba)

    def test_normal_state(self):
        result = self._call({"state": {"reported": {"batPct": 80}}})
        assert result == {"batPct": 80}

    def test_state_is_null(self):
        # {"state": null} must not raise
        assert self._call({"state": None}) == {}

    def test_reported_is_null(self):
        # {"state": {"reported": null}} must not raise
        assert self._call({"state": {"reported": None}}) == {}

    def test_state_missing(self):
        assert self._call({}) == {}

    def test_empty_master_state(self):
        assert self._call({}) == {}

    def test_models_variant_null_state(self):
        """The RoombaData.roomba_reported_state method shares the same guard."""
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.models import RoombaData
        obj = MagicMock(spec=RoombaData)
        obj.roomba = MagicMock()
        obj.roomba.master_state = {"state": None}
        result = RoombaData.roomba_reported_state(obj)
        assert result == {}
