"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import time
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
import statistics
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore


SQFT_TO_M2 = 0.092903  # from const.py


def _derived(
    n_mssn: int,
    duration_min: int = 45,
    sqft: float = 300.0,
    dirt: int = 5,
    result: str = "completed",
    rooms: dict | None = None,
) -> dict:
    return {
        "nMssn": n_mssn,
        "duration_min": duration_min,
        "sqft": sqft,
        "dirt": dirt,
        "result": result,
        "rooms_completed": rooms or {},
    }


def _make_archive(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    archive = MissionArchive()
    for rec in records:
        archive._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            archive._archived_nmssns.add(int(n))
    archive._initial_load_done = initial_load_done
    return archive


def _make_ms() -> MissionStore:
    return MissionStore()


def _make_archive_v280_l5_arc(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    """Build a MissionArchive with pre-populated derived records."""
    archive = MissionArchive()
    # Insert oldest-first so newest ends up at index 0 (archive is newest-first)
    for rec in records:
        archive._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            archive._archived_nmssns.add(int(n))
            if int(n) > archive._last_nMssn:
                archive._last_nMssn = int(n)
    archive._initial_load_done = initial_load_done
    return archive


def _derived_v280_l5_arc(
    n_mssn: int,
    rooms: dict | None = None,
) -> dict:
    """Build a minimal derived record for testing."""
    return {
        "nMssn": n_mssn,
        "result": "completed",
        "rooms_completed": rooms or {},
    }


def _make_rps() -> RobotProfileStore:
    return RobotProfileStore()


def _make_hass() -> MagicMock:
    return MagicMock()


class TestGsSmartUmfLogging:
    """_extract_traversal_umf_positions logs when < min_missions traversal found."""

    def _make_aligner(self, candidates: int = 5) -> MagicMock:
        aligner = MagicMock()
        aligner._door_candidates = [(float(i), float(i)) for i in range(candidates)]
        return aligner

    def _make_records(self, with_traversal: int, total: int = 10) -> list:
        records = []
        for i in range(total):
            if i < with_traversal:
                records.append({
                    "timeline": {
                        "finEvents": [{"type": "traversal", "traversal": {"rid": "1"}}]
                    }
                })
            else:
                records.append({"timeline": {"finEvents": []}})
        return records

    def test_info_log_emitted_when_below_min(self):
        from custom_components.roomba_plus.callbacks import _extract_traversal_umf_positions
        aligner = self._make_aligner()
        records = self._make_records(with_traversal=1, total=100)

        with patch("custom_components.roomba_plus.callbacks._LOGGER") as mock_log:
            result = _extract_traversal_umf_positions(records, aligner, min_missions=3)

        assert result == []
        mock_log.info.assert_called_once()
        call_args = mock_log.info.call_args[0]
        assert "GS-SMART-UMF" in call_args[0]

    def test_returns_candidates_when_enough_missions(self):
        from custom_components.roomba_plus.callbacks import _extract_traversal_umf_positions
        aligner = self._make_aligner(candidates=5)
        records = self._make_records(with_traversal=5, total=10)

        with patch("custom_components.roomba_plus.callbacks._LOGGER"):
            result = _extract_traversal_umf_positions(records, aligner, min_missions=3)

        assert len(result) == 5


class TestSeedL3FromArchive:
    async def _seed(
        self,
        archive: MissionArchive,
        ms: MissionStore | None = None,
    ) -> MissionStore:
        if ms is None:
            ms = _make_ms()
        from custom_components.roomba_plus.__init__ import _async_seed_l3_from_archive
        await _async_seed_l3_from_archive(archive, ms)
        return ms

    @pytest.mark.asyncio
    async def test_injects_baseline(self):
        records = [_derived(i) for i in range(1, 25)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is not None
        assert "duration_mean" in ms.archive_baseline

    @pytest.mark.asyncio
    async def test_guard_initial_load_not_done(self):
        records = [_derived(i) for i in range(1, 25)]
        archive = _make_archive(records, initial_load_done=False)
        ms = await self._seed(archive)
        assert ms.archive_baseline is None

    @pytest.mark.asyncio
    async def test_guard_too_few_records(self):
        records = [_derived(i) for i in range(1, 15)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is None

    @pytest.mark.asyncio
    async def test_baseline_stats_correct(self):
        records = [_derived(i, duration_min=50, sqft=400.0) for i in range(1, 25)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is not None
        assert abs(ms.archive_baseline["duration_mean"] - 50.0) < 0.01


class TestSeedL5FromArchive:
    async def _seed(
        self,
        archive: MissionArchive,
        rps: RobotProfileStore | None = None,
        save_side_effect: Exception | None = None,
    ) -> tuple[RobotProfileStore, MagicMock]:
        """Helper: run seed function with mocked storage."""
        if rps is None:
            rps = _make_rps()
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock(side_effect=save_side_effect)
        store_mock.async_load = AsyncMock(return_value=None)

        from custom_components.roomba_plus.__init__ import _async_seed_l5_from_archive

        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_seed_l5_from_archive(hass, "entry", archive, rps)
        return rps, store_mock

    @pytest.mark.asyncio
    async def test_seeds_room_dirt_index(self):
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 150.0}}),
            _derived_v280_l5_arc(2, rooms={"19": {"passes": 2, "area": 150.0}}),
        ])
        rps, _ = await self._seed(archive)
        assert "19" in rps.room_dirt_index
        assert rps.room_dirt_index["19"] > 0

    @pytest.mark.asyncio
    async def test_ema_oldest_first_ordering(self):
        """Two passes in mission 2 (recent) should weight higher than 1 pass (older)."""
        rps_low = _make_rps()
        rps_high = _make_rps()
        area = 100.0 * SQFT_TO_M2  # convert: area in sqft in archive

        # Low: only 1 pass
        archive_low = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}}),
        ])
        # High: 1 pass then 3 passes (recent high-pass mission dominates)
        archive_high = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}}),
            _derived_v280_l5_arc(2, rooms={"19": {"passes": 3, "area": 100.0}}),
        ])

        from custom_components.roomba_plus.__init__ import _async_seed_l5_from_archive
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        store_mock.async_load = AsyncMock(return_value=None)
        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_seed_l5_from_archive(hass, "e", archive_low, rps_low)
            await _async_seed_l5_from_archive(hass, "e", archive_high, rps_high)

        assert rps_high.room_dirt_index["19"] > rps_low.room_dirt_index["19"]

    @pytest.mark.asyncio
    async def test_guard_already_seeded(self):
        """Skips when room_dirt_index already has data."""
        rps = _make_rps()
        rps.room_dirt_index["19"] = 1.5  # pre-populated

        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"21": {"passes": 2, "area": 100.0}}),
        ])
        _, store_mock = await self._seed(archive, rps)
        # 21 should NOT have been added (guard hit)
        assert "21" not in rps.room_dirt_index
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_initial_load_not_done(self):
        """Skips when archive initial load is not complete."""
        archive = _make_archive_v280_l5_arc(
            [_derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}})],
            initial_load_done=False,
        )
        rps, store_mock = await self._seed(archive)
        assert rps.room_dirt_index == {}
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_empty_archive(self):
        """Skips when archive has no records."""
        archive = MissionArchive()
        archive._initial_load_done = True
        rps, store_mock = await self._seed(archive)
        assert rps.room_dirt_index == {}
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_zero_area_rooms(self):
        """Rooms with area=0 must not be seeded (avoids division by zero)."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 2, "area": 0.0}}),
        ])
        rps, _ = await self._seed(archive)
        assert "19" not in rps.room_dirt_index

    @pytest.mark.asyncio
    async def test_saves_when_seeded(self):
        """Store is saved when at least one room was seeded."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 150.0}}),
        ])
        _, store_mock = await self._seed(archive)
        store_mock.async_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_rooms(self):
        """All rooms from all missions in archive are seeded."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={
                "19": {"passes": 1, "area": 100.0},
                "21": {"passes": 2, "area": 200.0},
            }),
            _derived_v280_l5_arc(2, rooms={
                "19": {"passes": 1, "area": 100.0},
                "5":  {"passes": 1, "area": 80.0},
            }),
        ])
        rps, _ = await self._seed(archive)
        assert "19" in rps.room_dirt_index
        assert "21" in rps.room_dirt_index
        assert "5" in rps.room_dirt_index


class TestUpdateRobotProfileStoreMissionStats:
    """v2.10.2 bug-hunt fix: update_mission_stats() existed since at least
    v2.6.0 (docstring: "L3/L8 — called after each mission from the
    callback chain") but had no caller anywhere in the codebase — the
    mission_duration_mean/mission_area_mean baseline this feeds never
    populated in production regardless of how much MissionStore history
    existed. _async_update_robot_profile_store() is that callback chain
    (L5/L6/J already lived there); these tests cover the new L3/L8 block."""

    @staticmethod
    def _make_entry(entry_id: str = "e"):
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.runtime_data.grid_store = None  # skip L6 cleanly
        # Real dict (not a bare MagicMock) for the J section's
        # roomba_reported_state() lookup chain — a bare MagicMock's
        # auto-configured __float__ would otherwise make float(_sqft)
        # silently succeed with a fake value instead of hitting the
        # "no sqft reading" None path this test class wants.
        entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
        return entry

    @staticmethod
    def _ms_with_records(n: int, duration_min: int = 45, area_sqft: float = 300.0):
        # v3.2.0 bug-hunt fix: was hardcoded to "2026-06-01T07:00:00+00:00",
        # a fixed absolute date. query(days=30)'s cutoff is relative to
        # wall-clock "now" — once real time passed the point where that
        # fixed date fell outside the 30-day window, this test started
        # failing not because of a code bug, but because the fixture
        # itself had quietly gone stale. Use a relative offset instead so
        # this can't happen again regardless of when the suite runs.
        from homeassistant.util import dt as dt_util
        import datetime
        started = dt_util.now() - datetime.timedelta(days=5)
        ended = started + datetime.timedelta(minutes=30)
        ms = _make_ms()
        ms._records = [
            {
                "id": f"m_{i}",
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "duration_min": duration_min,
                "area_sqft": area_sqft,
                "result": "completed",
            }
            for i in range(n)
        ]
        return ms

    async def _run(self, ms, rps=None):
        from custom_components.roomba_plus.callbacks import _async_update_robot_profile_store
        if rps is None:
            rps = _make_rps()
        hass = _make_hass()
        entry = self._make_entry()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_update_robot_profile_store(hass, entry, ms, rps)
        return rps, store_mock

    @pytest.mark.asyncio
    async def test_populates_mission_duration_and_area_mean(self):
        ms = self._ms_with_records(5, duration_min=40, area_sqft=250.0)
        rps, _ = await self._run(ms)
        assert rps.mission_duration_mean == pytest.approx(40.0)
        assert rps.mission_area_mean == pytest.approx(250.0)

    @pytest.mark.asyncio
    async def test_below_minimum_records_does_not_populate(self):
        """Fewer than 5 qualifying records (the threshold inside
        update_mission_stats itself) must leave the means untouched —
        confirms this test isn't passing merely because any non-empty
        query() result trivially satisfies the assertion."""
        ms = self._ms_with_records(4)
        rps, _ = await self._run(ms)
        assert rps.mission_duration_mean is None
        assert rps.mission_area_mean is None

    @pytest.mark.asyncio
    async def test_saves_when_mission_stats_populated(self):
        ms = self._ms_with_records(5)
        _, store_mock = await self._run(ms)
        store_mock.async_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_save_when_nothing_changed(self):
        """Empty MissionStore + no GridStore + a roomba mock that yields no
        sqft reading: none of L3/L8, L5, L6, or J should report a change,
        so the store must not be saved at all."""
        ms = _make_ms()
        rps, store_mock = await self._run(ms)
        store_mock.async_save.assert_not_called()



class TestCleanupRemovedRepairsDispatched:
    """v3.5.0 Repairs redesign bug-hunt fix — source guard for
    async_cleanup_removed_repairs's dispatch.

    _phase_finalize is too heavy to call directly in a unit test (real
    platform forwarding, HTTP view registration, service registration) —
    same reasoning as TestMqttStampCallbackRegisteredBeforePlatforms above.
    This guards against the exact bug class this project has already found
    twice before (v3.2.0 bug-hunt: repair checks built, tested standalone,
    but never actually wired into anything the running integration calls):
    confirms the cleanup call is actually present in _phase_finalize's
    source, not just that async_cleanup_removed_repairs itself works when
    called directly (TestCleanupRemovedRepairs in test_repairs.py already
    covers that).
    """

    def test_cleanup_dispatched_from_phase_finalize(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "__init__.py").read_text()
        finalize_idx = src.find("async def _phase_finalize")
        assert finalize_idx != -1, "_phase_finalize not found in __init__.py"

        # Slice to just this function's body (up to the next top-level def)
        next_def_idx = src.find("\nasync def ", finalize_idx + 1)
        if next_def_idx == -1:
            next_def_idx = src.find("\ndef ", finalize_idx + 1)
        body = src[finalize_idx:next_def_idx if next_def_idx != -1 else None]

        assert "async_cleanup_removed_repairs" in body, (
            "_phase_finalize no longer dispatches "
            "async_cleanup_removed_repairs — users upgrading from a "
            "pre-v3.5.0 install would be left with permanently stuck "
            "stale Repair Issues (see async_cleanup_removed_repairs's "
            "own docstring in repairs.py for the full rationale)"
        )
        assert "hass.async_create_task" in body


class TestMqttStampCallbackRegisteredBeforePlatforms:
    """v3.2.1 — source-order guard for the watchdog race fix.

    The entire point of make_mqtt_stamp_callback is that it runs BEFORE
    any entity's on_message callback; roombapy invokes callbacks in
    registration order, and entities register theirs during platform
    setup.  If a future refactor moves the registration below
    async_forward_entry_setups, the false-"Problem"-blip race silently
    returns with every test still green — hence this explicit guard on
    the source order itself (same style as test_locale_slug_guard).
    """

    def test_stamp_registration_precedes_platform_forwarding(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "__init__.py").read_text()
        stamp_idx = src.find("make_mqtt_stamp_callback(config_entry)")
        forward_idx = src.find(
            "await hass.config_entries.async_forward_entry_setups"
        )
        assert stamp_idx != -1, "stamp callback registration missing from __init__.py"
        assert forward_idx != -1
        assert stamp_idx < forward_idx, (
            "make_mqtt_stamp_callback must be registered BEFORE "
            "async_forward_entry_setups — entities register their "
            "on_message callbacks during platform setup, and roombapy "
            "calls callbacks in registration order (v3.2.1 watchdog fix)"
        )


class TestOutlineSyncRecomputePrecedesFreezeSnapshot:
    """v3.2.1 FIELD FIX — source-order guard, same style as
    TestMqttStampCallbackRegisteredBeforePlatforms above.

    outline_store.recompute_sync() must run BEFORE the room-seg recompute
    block (which contains the FreezeSnapshotStore trigger) in image.py's
    mission-end handler — otherwise the freeze snapshot reads a stale
    (previous-mission, or on the very first-ever snapshot, empty)
    contour. Field-confirmed: the first FreezeSnapshotStore snapshot
    captured outline_points=0 for exactly this ordering reason. A future
    refactor that moves recompute_sync() below the room-seg block would
    silently reintroduce the bug with every test still green — hence
    this explicit guard on the source order itself.
    """

    def test_recompute_sync_call_precedes_room_seg_block(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        sync_idx = src.find("_gdata.outline_store.recompute_sync(")
        room_seg_idx = src.find("_gdata.room_seg_store.maybe_recompute(")
        assert sync_idx != -1, "outline_store.recompute_sync() call missing from image.py"
        assert room_seg_idx != -1
        assert sync_idx < room_seg_idx, (
            "outline_store.recompute_sync() must be called BEFORE the "
            "room-seg recompute block — the FreezeSnapshotStore trigger "
            "inside that block reads outline_store.contour_points and "
            "must see THIS mission's fresh value, not a stale one "
            "(v3.2.1 field-confirmed: first-ever snapshot had "
            "outline_points=0 because of this exact ordering bug)"
        )

    def test_later_outline_call_site_saves_not_recomputes(self):
        """The second (later) outline call site must call async_save(),
        NOT async_recompute() — calling the latter there too would
        silently double-increment OutlineStore.mission_count for the
        same mission (recompute_sync() already ran once, earlier)."""
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        sync_idx = src.find("_gdata.outline_store.recompute_sync(")
        # Search for the LATER outline_store call site, after the sync one.
        later_section = src[sync_idx + 1:]
        assert "_gdata.outline_store.async_save(" in later_section, (
            "expected a later outline_store.async_save() call for "
            "persistence after the synchronous recompute_sync() above"
        )
        assert "_gdata.outline_store.async_recompute(" not in later_section, (
            "a second async_recompute() call for the same mission would "
            "double-increment mission_count on top of the recompute_sync() "
            "call already made earlier in the same mission"
        )


class TestDockContactConfirmedPrecedesMissionEnd:
    """v3.2.1 DOCK-ANCHOR — source-order guard, same style as
    TestOutlineSyncRecomputePrecedesFreezeSnapshot above.

    _handle_dock_contact_confirmed() MUST be called before
    _handle_mission_end() in image.py's message handler — a real bug,
    caught before shipping: _handle_mission_end() feeds GridStore/
    RoomSegStore/OutlineStore via grid_store.update_from_mission(
    self._mission_points, ...) as a SINGLE, one-shot call. If the
    dock-anchor correction ran after that call (as it did in the first
    version of this feature), the most important case this mechanism
    exists for — a stuck-buffered segment resolving exactly at the
    mission's final dock contact — would have its correction reach only
    the live map, never the stores, for that mission's contribution. A
    future refactor that moves the call order back would silently
    reintroduce this with every other test still green — hence this
    explicit guard on the source order itself.
    """

    def test_dock_contact_confirmed_call_precedes_mission_end_call(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        dock_idx = src.find("self._handle_dock_contact_confirmed()")
        end_idx = src.find("self._handle_mission_end(current_phase)")
        assert dock_idx != -1, "_handle_dock_contact_confirmed() call missing from image.py"
        assert end_idx != -1
        assert dock_idx < end_idx, (
            "_handle_dock_contact_confirmed() must be called BEFORE "
            "_handle_mission_end() — the latter feeds GridStore/RoomSeg/"
            "Outline via a single one-shot grid_store.update_from_mission() "
            "call and must see the ALREADY-corrected _mission_points, not "
            "a stale pre-correction version (v3.2.1 field-confirmed: this "
            "exact ordering bug was caught before shipping)"
        )


class TestCalendarPlatformIfEnabled:
    """NEW (this session) -- CONF_ENABLE_SCHEDULE_CALENDAR opt-out for
    Platform.CALENDAR, default True so existing installations keep
    their calendar entity after upgrading (see that constant's own
    docstring, const.py, for why default True specifically)."""

    def test_returns_calendar_platform_by_default(self):
        from custom_components.roomba_plus import _calendar_platform_if_enabled
        from homeassistant.const import Platform

        config_entry = MagicMock()
        config_entry.options = {}

        assert _calendar_platform_if_enabled(config_entry) == [Platform.CALENDAR]

    def test_returns_empty_when_explicitly_disabled(self):
        from custom_components.roomba_plus import _calendar_platform_if_enabled
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}

        assert _calendar_platform_if_enabled(config_entry) == []

    def test_returns_calendar_platform_when_explicitly_enabled(self):
        from custom_components.roomba_plus import _calendar_platform_if_enabled
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR
        from homeassistant.const import Platform

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: True}

        assert _calendar_platform_if_enabled(config_entry) == [Platform.CALENDAR]


class TestRemoveCalendarEntityIfDisabled:
    """NEW (this session) -- explicit entity-registry cleanup so a
    disabled calendar entity doesn't linger forever as "unavailable"
    (unloading a platform doesn't remove its registry record on its
    own)."""

    def test_removes_calendar_entity_when_disabled(self):
        from custom_components.roomba_plus import _remove_calendar_entity_if_disabled
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"

        calendar_entry = MagicMock(domain="calendar", entity_id="calendar.roomba_schedule")
        sensor_entry = MagicMock(domain="sensor", entity_id="sensor.roomba_battery")
        fake_er = MagicMock()

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
             patch(
                 "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                 return_value=[calendar_entry, sensor_entry],
             ):
            _remove_calendar_entity_if_disabled(MagicMock(), config_entry)

        fake_er.async_remove.assert_called_once_with("calendar.roomba_schedule")

    def test_does_nothing_when_enabled(self):
        """Default (no option set at all) must NOT remove anything --
        the whole point of defaulting to True is that existing
        installations are left untouched."""
        from custom_components.roomba_plus import _remove_calendar_entity_if_disabled

        config_entry = MagicMock()
        config_entry.options = {}
        config_entry.entry_id = "entry1"

        fake_er = MagicMock()
        with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er), \
             patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry") as mock_entries:
            _remove_calendar_entity_if_disabled(MagicMock(), config_entry)

        mock_entries.assert_not_called()
        fake_er.async_remove.assert_not_called()


class TestReloadOnOptionsChangeIncludesCalendar:
    """NEW (this session) -- CONF_ENABLE_SCHEDULE_CALENDAR added to the
    reload-trigger key set (renamed _CONNECTION_KEYS ->
    _RELOAD_TRIGGER_KEYS internally). Without this, saving the option
    would silently do nothing until the user manually reloaded the
    integration -- unlike every other option on this form, which is
    read fresh at render/runtime rather than baked into the platforms
    list at setup time."""

    @pytest.mark.asyncio
    async def test_reload_triggered_when_calendar_option_changes(self):
        from custom_components.roomba_plus import _async_reload_on_options_change
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.data = {}  # never synced before -- an existing installation
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_awaited_once_with("entry1")

    @pytest.mark.asyncio
    async def test_no_reload_when_calendar_option_matches_default_and_was_never_touched(self):
        """The default-application fix (this session): before it, an
        existing installation's very first options save of ANYTHING
        (even something unrelated) would spuriously reload once, since
        .data has no key at all yet (reads as None) while .options
        would already resolve to the real default (True) -- None !=
        True looks like a change even though nothing meaningful did."""
        from custom_components.roomba_plus import _async_reload_on_options_change

        config_entry = MagicMock()
        config_entry.data = {}
        config_entry.options = {}  # both resolve to the same default -- no real change
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_reload_after_sync(self):
        from custom_components.roomba_plus import _async_reload_on_options_change
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.data = {CONF_ENABLE_SCHEDULE_CALENDAR: False}  # already synced
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_not_awaited()
