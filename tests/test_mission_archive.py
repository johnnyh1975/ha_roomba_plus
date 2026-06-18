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
    async def test_load_empty(self):
        archive = MissionArchive()
        store_mock = MagicMock()
        store_mock.async_load = AsyncMock(return_value=None)
        hass = _make_hass()
        with patch("custom_components.roomba_plus.mission_archive.Store",
                   return_value=store_mock):
            await archive.async_load(hass, "e")
        assert archive.record_count == 0


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
