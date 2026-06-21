"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from custom_components.roomba_plus.mission_archive import MAX_RECORDS
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.mission_archive import _classify_result
from custom_components.roomba_plus.mission_archive import _extract_rid
from custom_components.roomba_plus.mission_archive import _ts_to_iso
from custom_components.roomba_plus.mission_archive import _wl_floor
from custom_components.roomba_plus.mission_archive import _wl_stability


_NOW_TS = int(datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC).timestamp())
_WL_BARS_STRONG = [0, 0, 10, 80, 10]   # dominant bucket 3 = strong
_WL_BARS_WEAK   = [70, 20, 10,  0,  0]  # dominant bucket 0 = weak


def _raw(
    n_mssn: int = 1,
    done: str = "done",
    done_raw: str = "",
    pause_id: int = 0,
    sqft: float = 300.0,
    run_m: int = 40,
    duration_m: int = 45,
    dirt: int = 5,
    wl_bars: list | None = None,
    wifi_channel: int | None = 6,
    initiator: str = "schedule",
    fin_events: list | None = None,
    upcoming: list | None = None,
    start_time: int | None = None,
) -> dict:
    """Build a minimal raw /missionhistory record."""
    if wl_bars is None:
        wl_bars = _WL_BARS_STRONG
    rec: dict = {
        "nMssn": n_mssn,
        "startTime": start_time or _NOW_TS + n_mssn * 3600,
        "timestamp": (start_time or _NOW_TS + n_mssn * 3600) + duration_m * 60,
        "done": done,
        "done_raw": done_raw,
        "pauseId": pause_id,
        "sqft": sqft,
        "runM": run_m,
        "durationM": duration_m,
        "dirt": dirt,
        "wlBars": wl_bars,
        "wifiChannel": wifi_channel,
        "initiator": initiator,
    }
    timeline: dict = {}
    if upcoming is not None:
        timeline["plan"] = {"upcoming": upcoming, "ordered": True}
    if fin_events is not None:
        timeline["finEvents"] = fin_events
    if timeline:
        rec["timeline"] = timeline
    return rec


def _room_done(rid: str, passes: int = 1, area: float = 150.0) -> dict:
    return {
        "type": "room",
        "room": {"rid": rid, "status": 0, "passCount": passes, "totalArea": area},
    }


def _room_enter(rid: str) -> dict:
    return {"type": "room", "room": {"rid": rid, "status": 1, "passCount": 0}}


def _traversal(src: str = "19", dst: str = "21") -> dict:
    return {"type": "traversal", "traversal": {"srcRid": src, "dstRid": dst}}


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=None)
    return hass


class TestClassifyResult:
    def test_done_done(self):
        assert _classify_result({"done": "done"}) == "completed"

    def test_done_ok(self):
        assert _classify_result({"done": "ok"}) == "completed"

    def test_usr_end(self):
        assert _classify_result({"done": "cncl", "done_raw": "usrEnd"}) == "cancelled_by_user"

    def test_cncl(self):
        assert _classify_result({"done": "cncl"}) == "cancelled"

    def test_full(self):
        assert _classify_result({"done": "full"}) == "cancelled"

    def test_stuck_with_code(self):
        assert _classify_result({"done": "stuck", "pauseId": 17}) == "error_17"

    def test_stuck_no_code(self):
        assert _classify_result({"done": "stuck", "pauseId": 0}) == "stuck"

    def test_bat(self):
        assert _classify_result({"done": "bat"}) == "error_battery"

    def test_unknown(self):
        assert _classify_result({"done": "xyzzy"}) == "unknown"

    def test_empty(self):
        assert _classify_result({}) == "unknown"


class TestWlHelpers:
    def test_floor_strong(self):
        assert _wl_floor(_WL_BARS_STRONG) == 3

    def test_floor_weak(self):
        assert _wl_floor(_WL_BARS_WEAK) == 0

    def test_floor_none(self):
        assert _wl_floor(None) is None

    def test_floor_zeros(self):
        assert _wl_floor([0, 0, 0, 0, 0]) is None

    def test_stability_strong(self):
        # dominant bucket = 80/100 = 0.8
        assert _wl_stability(_WL_BARS_STRONG) == 0.8

    def test_stability_none(self):
        assert _wl_stability(None) is None


class TestExtractRid:
    def test_string(self):
        assert _extract_rid("19") == "19"

    def test_dict_rid(self):
        assert _extract_rid({"type": "rid", "rid": "21"}) == "21"

    def test_dict_region_id(self):
        assert _extract_rid({"region_id": "5"}) == "5"

    def test_none(self):
        assert _extract_rid(None) == ""


class TestTsToIso:
    def test_valid(self):
        ts = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())
        iso = _ts_to_iso(ts)
        assert "2026-06-01" in iso

    def test_none(self):
        assert _ts_to_iso(None) is None

    def test_zero(self):
        assert _ts_to_iso(0) is None


class TestParseDerived:
    def setup_method(self):
        self.archive = MissionArchive()

    def test_basic_fields(self):
        raw = _raw(n_mssn=42, sqft=350.0, dirt=8, wifi_channel=36)
        d = self.archive._parse_derived(raw)
        assert d["nMssn"] == 42
        assert d["sqft"] == 350.0
        assert d["dirt"] == 8
        assert d["wifi_channel"] == 36
        assert d["result"] == "completed"
        assert d["initiator"] == "schedule"

    def test_wl_signals(self):
        raw = _raw(wl_bars=_WL_BARS_STRONG)
        d = self.archive._parse_derived(raw)
        assert d["wl_floor"] == 3
        assert d["wl_stability"] == 0.8

    def test_room_completion(self):
        raw = _raw(fin_events=[
            _room_done("19", passes=2, area=200.0),
            _room_done("21", passes=1, area=120.0),
        ])
        d = self.archive._parse_derived(raw)
        assert "19" in d["rooms_completed"]
        assert d["rooms_completed"]["19"]["passes"] == 2
        assert "21" in d["rooms_completed"]

    def test_traversal_rids_extracted(self):
        raw = _raw(fin_events=[_traversal("19", "21")])
        d = self.archive._parse_derived(raw)
        assert "19" in d["traversal_rids"]
        assert "21" in d["traversal_rids"]

    def test_planned_room_order(self):
        raw = _raw(upcoming=["19", "21", "5"])
        d = self.archive._parse_derived(raw)
        assert d["planned_room_order"] == ["19", "21", "5"]

    def test_planned_room_order_lewis_format(self):
        raw = _raw(upcoming=[
            {"type": "rid", "rid": "19"},
            {"type": "rid", "rid": "21"},
        ])
        d = self.archive._parse_derived(raw)
        assert d["planned_room_order"] == ["19", "21"]

    def test_recharge_kidnap_count(self):
        raw = _raw(fin_events=[
            {"type": "recharge"},
            {"type": "recharge"},
            {"type": "kidnap"},
        ])
        d = self.archive._parse_derived(raw)
        assert d["recharge_count"] == 2
        assert d["kidnap_count"] == 1

    def test_error_in_mission(self):
        raw = _raw(fin_events=[
            {"type": "error", "error": {"code": 224}},
        ])
        d = self.archive._parse_derived(raw)
        assert 224 in d["error_in_mission"]

    def test_uses_classified_result(self):
        raw = _raw()
        raw["classified_result"] = "cancelled_by_user"
        d = self.archive._parse_derived(raw)
        assert d["result"] == "cancelled_by_user"


class TestParseTimeline:
    def setup_method(self):
        self.archive = MissionArchive()

    def test_plan_prepended(self):
        raw = _raw(upcoming=["19", "21"])
        tl = self.archive._parse_timeline(raw)
        assert tl[0][0] == "plan"
        assert tl[0][1]["rooms"] == ["19", "21"]

    def test_room_done_entry(self):
        raw = _raw(fin_events=[_room_done("19")])
        tl = self.archive._parse_timeline(raw)
        done_entries = [e for e in tl if e[0] == "room_done"]
        assert len(done_entries) == 1
        assert done_entries[0][1]["rid"] == "19"

    def test_traversal_entry(self):
        raw = _raw(fin_events=[_traversal("19", "21")])
        tl = self.archive._parse_timeline(raw)
        t_entries = [e for e in tl if e[0] == "traversal"]
        assert len(t_entries) == 1
        assert t_entries[0][1].get("srcRid") == "19"

    def test_empty_finEvents(self):
        raw = _raw()
        tl = self.archive._parse_timeline(raw)
        assert isinstance(tl, list)


class TestIsAnomalous:
    def _d(self, result="completed", kidnap=0, reloc=0, errors=None, disc=0):
        return {
            "result": result, "kidnap_count": kidnap,
            "reloc_count": reloc, "error_in_mission": errors or [],
            "disc_count": disc,
        }

    def test_completed_normal(self):
        assert MissionArchive._is_anomalous(self._d()) is False

    def test_error_result(self):
        assert MissionArchive._is_anomalous(self._d(result="error_17")) is True

    def test_kidnap(self):
        assert MissionArchive._is_anomalous(self._d(kidnap=1)) is True

    def test_high_reloc(self):
        assert MissionArchive._is_anomalous(self._d(reloc=3)) is True

    def test_reloc_below_threshold(self):
        assert MissionArchive._is_anomalous(self._d(reloc=2)) is False

    def test_error_code(self):
        assert MissionArchive._is_anomalous(self._d(errors=[224])) is True

    def test_cancelled_by_user(self):
        assert MissionArchive._is_anomalous(self._d(result="cancelled_by_user")) is False


class TestMissionArchiveAppend:
    def setup_method(self):
        self.archive = MissionArchive()

    def test_append_sets_last_nmssn(self):
        self.archive._append(_raw(n_mssn=10))
        assert self.archive.last_nMssn == 10

    def test_derived_newest_first(self):
        self.archive._append(_raw(n_mssn=1))
        self.archive._append(_raw(n_mssn=2))
        assert self.archive._derived[0]["nMssn"] == 2
        assert self.archive._derived[1]["nMssn"] == 1

    def test_timeline_co_indexed(self):
        raw = _raw(n_mssn=5, upcoming=["19"])
        self.archive._append(raw)
        assert len(self.archive._derived) == len(self.archive._timeline)
        assert self.archive._timeline[0][0][0] == "plan"

    def test_layer3_stored_for_anomalous(self):
        raw = _raw(n_mssn=7, done="stuck", pause_id=17,
                   fin_events=[_room_done("19")])
        self.archive._append(raw)
        assert 7 in self.archive._raw

    def test_layer3_not_stored_for_normal(self):
        raw = _raw(n_mssn=8)
        self.archive._append(raw)
        assert 8 not in self.archive._raw

    def test_fifo_trim(self):
        # Fill to MAX_RECORDS + 1
        for i in range(1, MAX_RECORDS + 2):
            self.archive._append(_raw(n_mssn=i))
        assert self.archive.record_count == MAX_RECORDS
        # Oldest (nMssn=1) should have been dropped
        assert self.archive._derived[-1]["nMssn"] == 2

    def test_cumulative_sqft_increments_per_mission(self):
        """v2.9.0 (J) — running total for total_cleaned_area, immune to
        the FIFO eviction that summing _derived live would suffer from."""
        self.archive._append(_raw(n_mssn=1, sqft=300.0))
        self.archive._append(_raw(n_mssn=2, sqft=400.0))
        assert self.archive.cumulative_sqft == 700.0

    def test_cumulative_sqft_survives_fifo_eviction(self):
        """The whole point of this field: once an old mission's record is
        evicted by the FIFO trim, its area must still count toward the
        running total — unlike summing _derived (the currently-held list)
        live, which would silently lose it.
        """
        for i in range(1, MAX_RECORDS + 2):
            self.archive._append(_raw(n_mssn=i, sqft=10.0))
        # MAX_RECORDS + 1 missions appended, each contributing 10.0 sqft —
        # the running total must reflect ALL of them, even though only
        # MAX_RECORDS records remain in _derived after the trim.
        assert self.archive.cumulative_sqft == (MAX_RECORDS + 1) * 10.0
        assert self.archive.record_count == MAX_RECORDS

    def test_cumulative_sqft_ignores_missing_sqft(self):
        """A record with no usable sqft must not contribute (and must not
        raise) — same null-safety as everywhere else sqft is summed."""
        self.archive._append(_raw(n_mssn=1, sqft=0.0))
        self.archive._append(_raw(n_mssn=2, sqft=300.0))
        assert self.archive.cumulative_sqft == 300.0


class TestMissionArchiveStorage:
    """Round-trip save/load through mocked HA Store."""

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self):
        archive = MissionArchive()
        archive._append(_raw(n_mssn=1))
        archive._append(_raw(n_mssn=2))
        archive._initial_load_done = True

        stored: dict = {}

        async def mock_save(data):
            stored.update(data)

        async def mock_load():
            return stored if stored else None

        store_mock = MagicMock()
        store_mock.async_save = mock_save
        store_mock.async_load = mock_load

        hass = _make_hass()
        entry_id = "test_entry"

        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_save(hass, entry_id)

            archive2 = MissionArchive()
            await archive2.async_load(hass, entry_id)

        assert archive2.record_count == 2
        assert archive2.last_nMssn == 2
        assert archive2.initial_load_done is True
        assert archive2._derived[0]["nMssn"] == 2  # newest first

    @pytest.mark.asyncio
    async def test_last_nmssn_start_ts_roundtrips(self):
        """v2.8.6 — the new companion field must survive a save/load cycle
        (needed by the discontinuity guard's chronological check)."""
        archive = MissionArchive()
        archive._append(_raw(n_mssn=1, start_time=99999))
        archive._last_nMssn_start_ts = 99999

        stored: dict = {}

        async def mock_save(data):
            stored.update(data)

        async def mock_load():
            return stored if stored else None

        store_mock = MagicMock()
        store_mock.async_save = mock_save
        store_mock.async_load = mock_load

        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_save(hass, "e")

            archive2 = MissionArchive()
            await archive2.async_load(hass, "e")

        assert archive2._last_nMssn_start_ts == 99999

    @pytest.mark.asyncio
    async def test_last_nmssn_start_ts_migration_seed_from_derived(self):
        """v2.8.6 — installations upgrading from a version that never
        persisted last_nMssn_start_ts must have it seeded once from the
        currently-held derived record matching last_nMssn, not left at 0
        (which would make the discontinuity guard's chronological check
        trivially true for the first delta_update call after upgrade).
        """
        archive = MissionArchive()
        archive._append(_raw(n_mssn=5, start_time=55555))
        # Simulate OLD persisted data (no last_nMssn_start_ts key at all).
        old_data = {
            "version": 1,
            "last_nMssn": 5,
            "initial_load_done": True,
            "derived": archive._derived,
            "timeline": archive._timeline,
            "raw": {},
            "cumulative_sqft": 0.0,
        }
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=old_data)

        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            archive2 = MissionArchive()
            await archive2.async_load(hass, "e")

        assert archive2._last_nMssn_start_ts == 55555

    @pytest.mark.asyncio
    async def test_load_empty(self):
        archive = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=None)
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_load(hass, "e")
        assert archive.record_count == 0

    @pytest.mark.asyncio
    async def test_cumulative_sqft_persists_across_save_load(self):
        archive = MissionArchive()
        archive._append(_raw(n_mssn=1, sqft=300.0))
        archive._append(_raw(n_mssn=2, sqft=400.0))

        stored: dict = {}

        async def mock_save(data):
            stored.update(data)

        async def mock_load():
            return stored if stored else None

        store_mock = MagicMock()
        store_mock.async_save = mock_save
        store_mock.async_load = mock_load

        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_save(hass, "test_entry")

            archive2 = MissionArchive()
            await archive2.async_load(hass, "test_entry")

        assert archive2.cumulative_sqft == 700.0

    @pytest.mark.asyncio
    async def test_cumulative_sqft_seeded_on_first_load_for_existing_installs(self):
        """v2.9.0 (J) — MIGRATION. An existing installation already has
        archived records from before this field existed. On first load
        without a persisted "cumulative_sqft" key, the accumulator must
        be SEEDED from whatever's currently held — starting at 0.0 would
        undercount for a long time until enough NEW missions accumulate
        to "catch up", which is worse than the old live-recompute
        behaviour this field replaces.
        """
        archive = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "last_nMssn": 2,
            "initial_load_done": True,
            "derived": [
                {"nMssn": 2, "sqft": 400.0},
                {"nMssn": 1, "sqft": 300.0},
            ],
            "timeline": [[], []],
            "raw": {},
            # deliberately no "cumulative_sqft" key — simulates data
            # persisted before this field was introduced
        })
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_load(hass, "e")

        assert archive.cumulative_sqft == 700.0, (
            "Must seed from the currently-held records on first load when "
            "no persisted cumulative_sqft exists yet"
        )

    @pytest.mark.asyncio
    async def test_cumulative_sqft_not_reseeded_once_persisted(self):
        """Once cumulative_sqft has been persisted (even as 0.0, a valid
        value for a robot truly with no sqft data anywhere), it must be
        loaded as-is — never re-derived from _derived on subsequent loads,
        since that live recomputation is exactly the bug this field fixes.
        """
        archive = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value={
            "last_nMssn": 1,
            "initial_load_done": True,
            "derived": [{"nMssn": 1, "sqft": 999.0}],
            "timeline": [[]],
            "raw": {},
            "cumulative_sqft": 50_000.0,  # the "true" running total
        })
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_load(hass, "e")

        assert archive.cumulative_sqft == 50_000.0, (
            "Must use the persisted value, not re-sum the (possibly "
            "FIFO-trimmed) currently-held _derived list"
        )


class TestMissionArchiveDeltaUpdate:
    @pytest.mark.asyncio
    async def test_new_record_appended(self):
        archive = MissionArchive()
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            result = await archive.async_delta_update(_raw(n_mssn=5), hass, "e")
        assert result is True
        assert archive.record_count == 1
        assert archive.last_nMssn == 5

    @pytest.mark.asyncio
    async def test_duplicate_skipped(self):
        archive = MissionArchive()
        archive._append(_raw(n_mssn=5))
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            result = await archive.async_delta_update(_raw(n_mssn=5), hass, "e")
        assert result is False
        assert archive.record_count == 1

    @pytest.mark.asyncio
    async def test_nmssn_discontinuity_does_not_permanently_block_new_missions(self):
        """v2.9.0 — DISCONTINUITY GUARD. Without this fix, a robot's nMssn
        counter resetting (factory reset, RMA replacement, etc.) would
        cause every future mission to silently collide with already-
        archived nMssn values from before the reset and get dropped
        forever, since nMssn alone is the dedup key.

        v2.8.6 — a genuine reset must ALSO look chronologically newer than
        the existing high-water mark (see async_delta_update's discontinuity
        guard) — pass an explicit start_time far in the future of the
        pre-reset history to simulate that correctly; using the default
        (n_mssn-derived) timestamp would make this look like an ordinary
        OLD duplicate of mission 1, not a reset.
        """
        archive = MissionArchive()
        # Pre-reset history: missions 1 through 5 already archived.
        for i in range(1, 6):
            archive._append(_raw(n_mssn=i))
        assert archive.last_nMssn == 5

        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            # Robot's counter has reset — reports "mission 1" again, but
            # this is a genuinely NEW, different mission that happened
            # long after the original mission 5 (hence the far-future
            # start_time — that's what makes this a reset, not a stale
            # duplicate of the original mission 1).
            result = await archive.async_delta_update(
                _raw(n_mssn=1, start_time=_NOW_TS + 30 * 86400), hass, "e",
            )

        assert result is True, (
            "A discontinuity must not silently block the new mission — "
            "without the fix, n_mssn=1 would already be in "
            "_archived_nmssns from before the reset and get skipped"
        )
        assert archive.record_count == 6

    @pytest.mark.asyncio
    async def test_discontinuity_warning_does_not_repeat_every_mission(self):
        """The discontinuity check resets last_nMssn to the new (lower)
        value — without that, every subsequent mission in the new epoch
        would ALSO be "< last_nMssn" and re-trigger the same clear/warning
        on every single mission until the new epoch's count organically
        catches up to the old high-water mark.
        """
        archive = MissionArchive()
        for i in range(1, 6):
            archive._append(_raw(n_mssn=i))

        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_delta_update(
                _raw(n_mssn=1, start_time=_NOW_TS + 30 * 86400), hass, "e",
            )
            # last_nMssn must now reflect the NEW epoch, not the old one.
            assert archive.last_nMssn == 1

            with patch.object(
                __import__(
                    "custom_components.roomba_plus.mission_archive",
                    fromlist=["_LOGGER"],
                )._LOGGER,
                "warning",
            ) as mock_warn:
                # Mission 2 of the new epoch — must NOT re-trigger the
                # discontinuity warning (1 < 5 would have, 2 < 1 does not).
                await archive.async_delta_update(
                    _raw(n_mssn=2, start_time=_NOW_TS + 30 * 86400 + 3600),
                    hass, "e",
                )
            mock_warn.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_sequential_missions_never_trigger_discontinuity(self):
        """Sanity check: ordinary monotonically-increasing nMssn values
        must never trigger the discontinuity path at all."""
        archive = MissionArchive()
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            with patch.object(
                __import__(
                    "custom_components.roomba_plus.mission_archive",
                    fromlist=["_LOGGER"],
                )._LOGGER,
                "warning",
            ) as mock_warn:
                for i in range(1, 6):
                    await archive.async_delta_update(_raw(n_mssn=i), hass, "e")
            mock_warn.assert_not_called()
        assert archive.record_count == 5
        assert archive.last_nMssn == 5

    @pytest.mark.asyncio
    async def test_unarchived_older_record_below_highwater_is_not_a_discontinuity(self):
        """v2.8.6 CONFIRMED FIELD BUG (Thonno) — async_delta_update() is, in
        practice, re-fed the entire recent cloud-history window on EVERY
        coordinator refresh (not only once per genuinely-new mission,
        despite the docstring). The oldest record in that window is
        routinely BELOW the high-water mark simply because it's an older,
        legitimately-never-archived gap-filler — not a counter reset.

        Before the fix, this numerically-lower-but-never-seen-before value
        alone triggered the discontinuity clear on EVERY refresh, which in
        turn caused every already-archived record in the same re-fed
        window to be silently RE-appended as a duplicate (confirmed in the
        field: 109 -> 209 -> 309 records across two refreshes ~83 min
        apart, same robot, same "reported=742, high-water mark=858"
        warning both times).

        The fix requires the value to ALSO already be a collision in
        _archived_nmssns (a true recycled value) before declaring a
        discontinuity — an older never-archived record is numerically
        lower but is NOT a collision, so it must now just append
        normally, with NO warning and NO dedup-set clear.
        """
        archive = MissionArchive()
        # Recent missions already archived — high-water mark is 858.
        for n in (850, 851, 852, 856, 858):
            archive._append(_raw(n_mssn=n))
        assert archive.last_nMssn == 858
        archived_before = set(archive._archived_nmssns)

        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            with patch.object(
                __import__(
                    "custom_components.roomba_plus.mission_archive",
                    fromlist=["_LOGGER"],
                )._LOGGER,
                "warning",
            ) as mock_warn:
                # 742 was never archived before — a legitimate gap, not a
                # recycled value. Far below last_nMssn=858 but NOT a collision.
                result = await archive.async_delta_update(_raw(n_mssn=742), hass, "e")
            mock_warn.assert_not_called()

        assert result is True  # appended normally, as any new record would be
        assert archive.last_nMssn == 858, (
            "Must NOT have been reset to 0 — that's what caused every "
            "already-archived record in the field bug to be re-appended "
            "as a duplicate on the very next call in the same batch"
        )
        # The dedup set must be intact — none of the pre-existing entries
        # were wiped, so re-feeding 850/851/852/856/858 in the same batch
        # (as the real cloud refresh does) would correctly dedupe them.
        assert archived_before.issubset(archive._archived_nmssns)

    @pytest.mark.asyncio
    async def test_refeeding_same_batch_does_not_duplicate_after_gap_fill(self):
        """End-to-end reproduction of the exact field sequence: an older
        gap-filler (742) followed by re-feeding already-archived records
        from the same batch (850...858) must NOT create duplicates."""
        archive = MissionArchive()
        for n in (850, 851, 852, 856, 858):
            archive._append(_raw(n_mssn=n))
        count_before = archive.record_count

        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_delta_update(_raw(n_mssn=742), hass, "e")
            # Same cloud refresh re-feeds the tail of the window, including
            # records already archived before this call.
            for n in (850, 851, 852, 856, 858):
                result = await archive.async_delta_update(_raw(n_mssn=n), hass, "e")
                assert result is False, (
                    f"nMssn={n} was already archived — must be deduped, "
                    "not re-appended"
                )

        assert archive.record_count == count_before + 1  # only 742 is new


class TestMissionArchiveQueries:
    def setup_method(self):
        self.archive = MissionArchive()
        # Add records: nMssn 1 (old), 2 (recent), 3 (recent with traversal)
        old_ts = int((datetime.now(UTC) - timedelta(days=45)).timestamp())
        new_ts = int((datetime.now(UTC) - timedelta(days=5)).timestamp())

        self.archive._append(_raw(n_mssn=1, start_time=old_ts))
        self.archive._append(_raw(
            n_mssn=2, start_time=new_ts,
            fin_events=[_room_done("19")],
        ))
        self.archive._append(_raw(
            n_mssn=3, start_time=new_ts + 3600,
            fin_events=[_traversal("19", "21"), _room_done("19"), _room_done("21")],
        ))

    def test_traversal_missions(self):
        tm = self.archive.traversal_missions()
        assert len(tm) == 1
        assert tm[0]["nMssn"] == 3

    def test_missions_by_room(self):
        m = self.archive.missions_by_room("19")
        # nMssn 2 and 3 both completed room 19
        n_mssns = {r["nMssn"] for r in m}
        assert 2 in n_mssns
        assert 3 in n_mssns
        assert 1 not in n_mssns

    def test_recent_derived_30d(self):
        recent = self.archive.recent_derived(days=30)
        n_mssns = {r["nMssn"] for r in recent}
        assert 2 in n_mssns
        assert 3 in n_mssns
        assert 1 not in n_mssns

    def test_wifi_channel_series(self):
        channels = self.archive.wifi_channel_series(n=10)
        # All 3 records have wifi_channel=6
        assert len(channels) == 3
        assert all(c == 6 for c in channels)

    def test_latest_derived(self):
        latest = self.archive.latest_derived(n=1)
        assert len(latest) == 1
        assert latest[0]["nMssn"] == 3  # newest
