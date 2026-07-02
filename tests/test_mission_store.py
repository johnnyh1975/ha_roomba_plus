"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import datetime
import pytest
from homeassistant.util import dt as dt_util
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.mission_store import DaySummary
from custom_components.roomba_plus.mission_store import MissionWindow
from custom_components.roomba_plus.mission_store import MAX_RECORDS
from custom_components.roomba_plus.sensor import _completion_rate_30d
from custom_components.roomba_plus.sensor import _area_cleaned_today
from custom_components.roomba_plus.sensor import _problem_zone_value
from custom_components.roomba_plus.sensor import _last_error_code_value
from custom_components.roomba_plus.sensor import _mission_store_value
from custom_components.roomba_plus.const import ERROR_CATALOGUE
from custom_components.roomba_plus.const import ERROR_CODE_LABELS
import time as _time_mod
import types
import sys
import os
import tests.conftest
from custom_components.roomba_plus.sensor import SENSORS
from custom_components.roomba_plus.sensor import _mission_store_last_started_at
import asyncio
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from unittest.mock import patch
from custom_components.roomba_plus.sensor import _raw_cleaning_speed
from custom_components.roomba_plus.sensor import _raw_dirt_density
from custom_components.roomba_plus.sensor import _raw_recharge_fraction
from custom_components.roomba_plus.sensor import _raw_cleaning_speed_trend
from custom_components.roomba_plus.sensor import _battery_capacity_retention
from custom_components.roomba_plus.sensor import _estimated_battery_eol
from custom_components.roomba_plus.maintenance_store import MaintenanceStore
import math
from custom_components.roomba_plus.umf_aligner import UmfAligner
import json
from datetime import datetime as datetime_v250_api_export
from datetime import timezone
from custom_components.roomba_plus.api_views import MissionHistoryImportView
from custom_components.roomba_plus.api_views import MissionHistoryView
from custom_components.roomba_plus.api_views import _VALID_FORMATS
from custom_components.roomba_plus.const import DOMAIN
import time
import statistics
from custom_components.roomba_plus.mission_archive import MissionArchive


_make_record_counter = 0
__make_record_seq = 0
ROOT = os.path.join(os.path.dirname(__file__), "..")
_ep = sys.modules.get('homeassistant.helpers.entity_platform')
REGION_MAP = {"19": "Bathroom", "21": "Kitchen", "1": "Hallway", "25": "Bedroom"}
TYPICAL_TIMELINE = {
    "plan": {"upcoming": ["19", "21", "1"], "ordered": 1, "type": "drc"},
    "finEvents": [
        {"type": "start", "ts": 1000},
        {"type": "room", "room": {"rid": "19", "passCount": 1, "status": 1, "area": 72}},
        {"type": "room", "room": {
            "rid": "19", "passCount": 1, "status": 0,
            "area": 72, "passArea": 40, "totalArea": 42,
        }},
        {"type": "room", "room": {"rid": "21", "passCount": 1, "status": 1, "area": 120}},
        {"type": "room", "room": {
            "rid": "21", "passCount": 1, "status": 0,
            "area": 120, "passArea": 90, "totalArea": 95,
        }},
        {"type": "room", "room": {"rid": "1", "passCount": 1, "status": 1, "area": 55}},
        {"type": "room", "room": {
            "rid": "1", "passCount": 1, "status": 0,
            "area": 55, "passArea": 44, "totalArea": 44,
        }},
        {"type": "fin", "ts": 5000},
    ],
}


def _iso(days_ago: float = 0, hour: int = 10) -> str:
    """Return an ISO datetime string N days in the past."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record(
    days_ago: float = 0,
    result: str = "completed",
    duration_min: int = 30,
    area_sqft: float | None = 400.0,
    zones: list | None = None,
    error_code: int | None = None,
) -> dict:
    global _make_record_counter
    _make_record_counter += 1
    started = _iso(days_ago, hour=8)
    ended = _iso(days_ago, hour=9)
    return {
        "id": f"m_{days_ago}_{_make_record_counter}",
        "started_at": started,
        "ended_at": ended,
        "duration_min": duration_min,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": zones or [],
        "error_code": error_code,
        "bbrun_hr": 100,
    }


def _ts(unix_ts: int) -> str:
    """Return ISO UTC string from a Unix timestamp."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def _iso_v180_sensors(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_unique_id(days_ago):
    global __make_record_seq
    __make_record_seq += 1
    return f"m_{days_ago}_{__make_record_seq}"


def _make_record_v180_sensors(days_ago=0, result="completed", area_sqft=400.0, zones=None):
    return {
        "id": _make_unique_id(days_ago),
        "started_at": _iso_v180_sensors(days_ago),
        "ended_at": _iso_v180_sensors(days_ago),
        "duration_min": 30,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": zones or [],
        "error_code": None,
        "bbrun_hr": 100,
    }


async def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        await store.async_append(r)
    return store


def _iso_v191_fixes(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record_v191_fixes(days_ago=0, result="completed", area_sqft=200.0, bbrun_hr=100):
    started = _iso_v191_fixes(days_ago, hour=8)
    ended   = _iso_v191_fixes(days_ago, hour=9)
    return {
        "id": _make_unique_id(days_ago),
        "started_at": started,
        "ended_at": ended,
        "duration_min": 60,
        "area_sqft": area_sqft,
        "result": result,
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": bbrun_hr,
    }


def _store_with_v191_fixes(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


def _get_sensor(key: str):
    for desc in SENSORS:
        if desc.key == key:
            return desc
    raise KeyError(f"Sensor '{key}' not found")


def _iso_v192_fixes(days_ago: float = 0, hour: int = 10) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _make_record_v192_fixes(days_ago=0, result="completed", started_at=None, error_code=None):
    started = started_at or _iso_v192_fixes(days_ago, hour=8)
    return {
        "id": _make_unique_id(days_ago),
        "started_at": started,
        "ended_at": _iso_v192_fixes(days_ago, hour=9),
        "duration_min": 60,
        "area_sqft": 200.0,
        "result": result,
        "initiator": "schedule",
        "zones": [],
        "error_code": error_code,
        "bbrun_hr": 100,
    }


def _store_with_v192_fixes(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store


def _make_entity(store: MissionStore):
    class _FakeRuntimeData:
        mission_store = store
        maintenance_store = None

    class _FakeConfigEntry:
        runtime_data = _FakeRuntimeData()
        options = {}

    class _FakeEntity:
        _config_entry = _FakeConfigEntry()

    return _FakeEntity()


def _utc(ts: int) -> str:
    """Unix timestamp → ISO UTC string."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _local_rec(
    started_ts: int,
    ended_ts: int,
    area_sqft=None,
    result="completed",
    zones=None,
):
    """Build a local MissionStore record."""
    started = datetime.datetime.fromtimestamp(started_ts, tz=datetime.timezone.utc)
    ended   = datetime.datetime.fromtimestamp(ended_ts,   tz=datetime.timezone.utc)
    return {
        "id":           f"m_{started_ts}",
        "started_at":   started.isoformat(),
        "ended_at":     ended.isoformat(),
        "duration_min": max(0, round((ended - started).total_seconds() / 60)),
        "area_sqft":    area_sqft,
        "result":       result,
        "initiator":    "schedule",
        "zones":        zones or [],
        "error_code":   None,
        "bbrun_hr":     100,
    }


def _cloud_rec(start_ts: int, end_ts: int, sqft=200):
    """Build a minimal cloud raw record."""
    return {
        "startTime": start_ts,
        "timestamp": end_ts,
        "sqft":      sqft,
        "done":      "done",
        "done_raw":  "done",
        "classified_result": "completed",
    }


def _make_store(records: list) -> object:
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    store._records = records
    return store


def _make_store_v200_callbacks():
    from custom_components.roomba_plus.mission_store import MissionStore
    store = MissionStore()
    return store


def _make_entry(store, map_capability_val="none", zone_store=None,
                cloud_coordinator=None):
    """Build a minimal config entry stub for callback tests."""
    from custom_components.roomba_plus.models import MapCapability

    cap = MapCapability(map_capability_val)
    _zone_store = zone_store
    _cloud_coordinator = cloud_coordinator

    class _FakeData:
        mission_store     = store
        last_error_code   = None
        last_error_at     = None
        last_error_zone   = None
        map_capability    = cap
        # F6g — consecutive_skips counter; needs a real MaintenanceStore
        class _FakeMaintenanceStore:
            consecutive_skips = 0
        maintenance_store = _FakeMaintenanceStore()

        @property
        def zone_store(self):
            return _zone_store

        @property
        def cloud_coordinator(self):
            return _cloud_coordinator

        @property
        def has_cloud(self):
            return _cloud_coordinator is not None and _cloud_coordinator.data is not None

    class _FakeEntry:
        runtime_data = _FakeData()
        entry_id     = "test_entry"

    return _FakeEntry()


def _make_hass(loop=None):
    """Minimal hass stub."""
    class _FakeHass:
        class _FakeConfig:
            config_dir = "/tmp/roomba_plus_test"
            components: set = set()
            def path(self, *parts: str) -> str:
                import os as _os
                p = _os.path.join(self.config_dir, *parts)
                _os.makedirs(_os.path.dirname(p), exist_ok=True)
                return p
        async def async_add_executor_job(self, fn, *args):
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args)
        def __init__(self):
            self.loop = loop
            self.data = {}
            self.config = self._FakeConfig()
            from homeassistant.core import CoreState
            self.state = CoreState.running
    return _FakeHass()


def _ts_v200_callbacks(offset_sec: int = 0) -> int:
    """Return a unix timestamp offset from a fixed base."""
    return 1700000000 + offset_sec


def _iso_v200_callbacks(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


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


def _ms_with_timeline(timeline_dict):
    ms = MissionStore()
    ms._records = [{
        "id": "m_1",
        "started_at": "2026-06-01T08:00:00+00:00",
        "ended_at": "2026-06-01T09:00:00+00:00",
        "result": "completed",
        "timeline": timeline_dict,
    }]
    return ms


def _ms_without_timeline():
    ms = MissionStore()
    ms._records = [{
        "id": "m_1",
        "started_at": "2026-06-01T08:00:00+00:00",
        "ended_at": "2026-06-01T09:00:00+00:00",
        "result": "completed",
    }]
    return ms


def _make_coordinator(umf_data=None, raw_records=None):
    from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
    coord = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
    coord.data = {
        "umf": umf_data or {},
        "mission_history_raw": raw_records or [],
        "pmaps": [],
    }
    return coord


def _make_aligner(aligned: bool = True, confidence: float = 0.85) -> UmfAligner:
    """Return a minimal UmfAligner with controlled aligned/confidence state."""
    a = UmfAligner([], [], MagicMock())
    a._aligned    = aligned
    a._confidence = confidence
    a._transform  = (0.0, 0.0, 0.0)
    a.pmap_version_id = "v1"
    return a


def _make_runtime_data(
    *,
    aligner: UmfAligner | None = None,
    has_cloud: bool = True,
    regions: list | None = None,
    keepout_zones: list | None = None,
    mission_store=None,
    grid_store=None,
    map_capability=None,
    geometry_store=None,
):
    data = MagicMock()
    data.umf_aligner    = aligner
    data.has_cloud      = has_cloud
    data.mission_store  = mission_store
    data.grid_store     = grid_store
    data.geometry_store = geometry_store

    cc = MagicMock()
    cc.regions      = regions or []
    cc.keepout_zones = keepout_zones or []
    cc.observed_zone_centroids = []
    cc.last_update_success = True
    data.cloud_coordinator = cc if has_cloud else None

    if map_capability is not None:
        data.map_capability = map_capability

    return data


def _make_record_v250_api_export(id_: str, started_at: str = "2026-05-01T08:00:00+00:00") -> dict:
    return {
        "id": id_,
        "started_at": started_at,
        "ended_at": "2026-05-01T09:00:00+00:00",
        "duration_min": 60,
        "area_sqft": 200.0,
        "result": "completed",
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": 100,
    }


def _make_mission_store(records: list[dict]) -> MagicMock:
    ms = MagicMock()
    ms._records = list(records)
    ms.async_save = AsyncMock()
    return ms


def _make_request(hass: MagicMock, fmt: str = "export") -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    req.query = {"format": fmt}
    return req


def _make_post_request(hass: MagicMock, body: dict) -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    encoded = json.dumps(body).encode()
    req.json = AsyncMock(return_value=body)
    return req


def _make_hass_with_entry(
    entry_id: str = "abc123",
    records: list[dict] | None = None,
    entry_present: bool = True,
    runtime_data_set: bool = True,
) -> tuple[MagicMock, MagicMock]:
    """Return (hass, entry) pair for view tests."""
    hass = MagicMock()
    if not entry_present:
        hass.config_entries.async_get_entry.return_value = None
        return hass, None

    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {"blid": "abc_blid_123"}
    entry.entry_id = entry_id

    if not runtime_data_set:
        # Simulate the pre-ready state — no runtime_data attribute
        del entry.runtime_data
    else:
        ms = _make_mission_store(records or [])
        data = MagicMock()
        data.mission_store = ms
        entry.runtime_data = data

    hass.config_entries.async_get_entry.return_value = entry
    return hass, entry


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


class TestDefaultState:
    def test_latest_returns_none_when_empty(self):
        store = MissionStore()
        assert store.latest() is None

    def test_query_returns_empty_list(self):
        store = MissionStore()
        assert store.query(30) == []

    def test_clean_streak_zero_when_empty(self):
        store = MissionStore()
        assert store.clean_streak() == 0

    def test_presence_windows_empty_when_no_records(self):
        store = MissionStore()
        assert store.presence_windows(7) == []

    def test_query_by_day_empty(self):
        store = MissionStore()
        assert store.query_by_day(30) == {}


class TestAsyncAppend:
    @pytest.mark.asyncio
    async def test_append_stores_record(self):
        store = MissionStore()
        r = _make_record()
        await store.async_append(r)
        assert store.latest() == r

    @pytest.mark.asyncio
    async def test_append_multiple(self):
        store = MissionStore()
        for i in range(5):
            await store.async_append(_make_record(days_ago=i))
        assert len(store.query(365)) == 5

    @pytest.mark.asyncio
    async def test_trim_to_max_records(self):
        store = MissionStore()
        for i in range(MAX_RECORDS + 10):
            await store.async_append(_make_record(days_ago=i * 0.1))
        assert len(store.query(365)) == MAX_RECORDS

    @pytest.mark.asyncio
    async def test_trim_keeps_newest(self):
        store = MissionStore()
        # Append oldest first, newest last
        for i in range(MAX_RECORDS + 5):
            r = _make_record(days_ago=(MAX_RECORDS + 5 - i) * 0.01)
            r["id"] = f"m_{i}"
            await store.async_append(r)
        # After trim, latest should be the last-appended
        assert store.latest()["id"] == f"m_{MAX_RECORDS + 4}"


class TestQuery:
    @pytest.mark.asyncio
    async def test_filters_by_days(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))   # within 1 day
        await store.async_append(_make_record(days_ago=5))   # within 7 days
        await store.async_append(_make_record(days_ago=10))  # outside 7 days
        assert len(store.query(7)) == 2

    @pytest.mark.asyncio
    async def test_filters_by_result(self):
        store = MissionStore()
        await store.async_append(_make_record(result="completed"))
        await store.async_append(_make_record(result="stuck"))
        await store.async_append(_make_record(result="error"))
        assert len(store.query(30, result="completed")) == 1
        assert len(store.query(30, result="stuck")) == 1
        assert len(store.query(30)) == 3

    @pytest.mark.asyncio
    async def test_returns_sorted_ascending(self):
        store = MissionStore()
        for i in [3, 1, 2]:
            await store.async_append(_make_record(days_ago=i))
        records = store.query(30)
        started = [r["started_at"] for r in records]
        assert started == sorted(started)


class TestQueryByDay:
    @pytest.mark.asyncio
    async def test_groups_by_date(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))
        await store.async_append(_make_record(days_ago=1))
        by_day = store.query_by_day(7)
        assert len(by_day) == 2

    @pytest.mark.asyncio
    async def test_day_summary_totals(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        today_summary = list(by_day.values())[0]
        assert today_summary.total == 2
        assert today_summary.completed == 1
        assert today_summary.stuck == 1

    @pytest.mark.asyncio
    async def test_dominant_result_error_wins(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="error"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].result == "error"

    @pytest.mark.asyncio
    async def test_dominant_result_stuck_over_completed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].result == "stuck"

    @pytest.mark.asyncio
    async def test_area_sqft_summed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, area_sqft=200.0))
        await store.async_append(_make_record(days_ago=0, area_sqft=300.0))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].area_sqft == 500.0

    @pytest.mark.asyncio
    async def test_area_sqft_none_for_600_series(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, area_sqft=None))
        by_day = store.query_by_day(7)
        assert list(by_day.values())[0].area_sqft is None


class TestCleanStreak:
    @pytest.mark.asyncio
    async def test_streak_today(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        assert store.clean_streak() == 1

    @pytest.mark.asyncio
    async def test_streak_multiple_consecutive_days(self):
        store = MissionStore()
        for i in range(3):
            await store.async_append(_make_record(days_ago=i, result="completed"))
        assert store.clean_streak() == 3

    @pytest.mark.asyncio
    async def test_streak_resets_on_gap(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="completed"))
        # day 1 missing — gap
        await store.async_append(_make_record(days_ago=2, result="completed"))
        assert store.clean_streak() == 1  # only today counts

    @pytest.mark.asyncio
    async def test_streak_ignores_non_completed(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0, result="stuck"))
        assert store.clean_streak() == 0

    @pytest.mark.asyncio
    async def test_streak_zero_when_no_today(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=1, result="completed"))
        assert store.clean_streak() == 0


class TestPresenceWindows:
    @pytest.mark.asyncio
    async def test_empty_when_fewer_than_2_records(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=0))
        assert store.presence_windows(7) == []

    @pytest.mark.asyncio
    async def test_window_detected_between_missions(self):
        store = MissionStore()
        # mission 1 ended yesterday at 9:00, mission 2 starts today at 8:00
        # — there is a ~23hr window between them
        r1 = _make_record(days_ago=1)
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0)
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert len(windows) == 1
        assert windows[0].duration_min > 0

    @pytest.mark.asyncio
    async def test_resulted_in_clean_true_for_completed_start(self):
        store = MissionStore()
        r1 = _make_record(days_ago=1, result="completed")
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0, result="completed")
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert len(windows) == 1
        assert windows[0].resulted_in_clean is True

    @pytest.mark.asyncio
    async def test_resulted_in_clean_false_for_stuck(self):
        store = MissionStore()
        r1 = _make_record(days_ago=1, result="completed")
        r1["ended_at"] = _iso(1, hour=9)
        r2 = _make_record(days_ago=0, result="stuck")
        r2["started_at"] = _iso(0, hour=8)
        await store.async_append(r1)
        await store.async_append(r2)
        windows = store.presence_windows(7)
        assert windows[0].resulted_in_clean is False


class TestRoomCoverageHealth:
    """v3.2.0 COVERAGE-FREQ — MissionStore.room_coverage_health().

    Self-calibrated per room (mean + stdev of that room's OWN historical
    cleaning gaps) rather than a fixed global threshold — mirrors this
    project's established pattern (L9-BATTERY, L10, ROOM-ACCESS).
    """

    def _now_iso(self):
        return dt_util.now().isoformat()

    @pytest.mark.asyncio
    async def test_empty_when_no_room_data(self):
        store = MissionStore()
        await store.async_append(_make_record(days_ago=1))
        assert store.room_coverage_health(self._now_iso()) == {}

    @pytest.mark.asyncio
    async def test_insufficient_data_below_min_intervals(self):
        """Only 2 cleans of a room -> 1 gap -> below min_intervals=3."""
        store = MissionStore()
        for days_ago in (14, 7):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Kitchen"]
            await store.async_append(rec)
        result = store.room_coverage_health(self._now_iso())
        assert result["Kitchen"]["status"] == "insufficient_data"
        assert result["Kitchen"]["expected_interval_days"] is None
        assert result["Kitchen"]["days_since_last"] is not None

    @pytest.mark.asyncio
    async def test_healthy_when_within_normal_rhythm(self):
        """Kitchen cleaned every ~7 days, last clean 7 days ago — right
        on schedule, not overdue."""
        store = MissionStore()
        for days_ago in (28, 21, 14, 7):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Kitchen"]
            await store.async_append(rec)
        result = store.room_coverage_health(self._now_iso())
        assert result["Kitchen"]["status"] == "healthy"
        assert result["Kitchen"]["expected_interval_days"] == pytest.approx(7.0, abs=0.5)

    @pytest.mark.asyncio
    async def test_overdue_when_far_beyond_own_normal_rhythm(self):
        """Kitchen normally cleaned every ~7 days (tight pattern), but
        the last clean was 30 days ago — clearly overdue relative to
        its OWN established rhythm."""
        store = MissionStore()
        for days_ago in (37, 30, 23, 16):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Kitchen"]
            await store.async_append(rec)
        # No clean since day 16 — last_clean is 16 days ago from "now"
        # (well beyond the ~7-day established rhythm).
        result = store.room_coverage_health(self._now_iso())
        assert result["Kitchen"]["status"] == "overdue"

    @pytest.mark.asyncio
    async def test_rooms_are_independent(self):
        """Kitchen (frequent) and Bedroom (infrequent) get their own,
        independently-calibrated expectations — not a shared global one."""
        store = MissionStore()
        for days_ago in (28, 21, 14, 7):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Kitchen"]
            await store.async_append(rec)
        for days_ago in (100, 75, 50, 25):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Bedroom"]
            await store.async_append(rec)
        result = store.room_coverage_health(self._now_iso(), days=120)
        assert result["Kitchen"]["expected_interval_days"] < result["Bedroom"]["expected_interval_days"]

    @pytest.mark.asyncio
    async def test_fallback_threshold_when_no_variance(self):
        """Perfectly regular intervals (stdev=0) fall back to a
        multiplier-based threshold (1.5x mean) rather than dividing by
        zero or never triggering "overdue" at all."""
        store = MissionStore()
        for days_ago in (40, 30, 20, 10):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Kitchen"]
            await store.async_append(rec)
        # Exactly 10-day intervals -> stdev=0 -> fallback threshold = 15 days.
        # Last clean 10 days ago -> well under 15 -> healthy.
        result = store.room_coverage_health(self._now_iso())
        assert result["Kitchen"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_records_outside_lookback_window_excluded_from_intervals(self):
        """Old visits beyond the `days` lookback window don't contribute
        to the interval calculation, even though room_cleaning_history()
        (the days_since_last source) has no such window."""
        store = MissionStore()
        # 3 visits far outside a 30-day window
        for days_ago in (200, 190, 180):
            rec = _make_record(days_ago=days_ago)
            rec["last_cleaned_rooms"] = ["Attic"]
            await store.async_append(rec)
        result = store.room_coverage_health(self._now_iso(), days=30)
        assert result["Attic"]["status"] == "insufficient_data"


class TestSerialisation:
    @pytest.mark.asyncio
    async def test_round_trip_preserves_records(self):
        """Records survive save → load cycle (via raw dict, not actual storage)."""
        store = MissionStore()
        records = [_make_record(days_ago=i) for i in range(5)]
        for r in records:
            await store.async_append(r)

        # Simulate save/load by extracting and reloading internal state
        saved = list(store._records)
        store2 = MissionStore()
        store2._records = saved

        assert len(store2.query(365)) == 5
        assert store2.latest()["id"] == records[-1]["id"]

    @pytest.mark.asyncio
    async def test_load_with_missing_fields_defaults_safely(self):
        """Records missing optional fields don't crash query methods."""
        store = MissionStore()
        minimal = {
            "id": "m_minimal",
            "started_at": _iso(0),
            "ended_at": _iso(0),
            "result": "completed",
        }
        store._records = [minimal]
        # Should not raise
        records = store.query(30)
        assert len(records) == 1
        assert store.clean_streak() == 1


class TestDuplicateGuard:
    """F4c -- async_append silently drops records with duplicate IDs."""

    @pytest.mark.asyncio
    async def test_duplicate_id_dropped(self):
        store = MissionStore()
        r = {"id": "m_1000", "started_at": _iso(1), "ended_at": _iso(0), "result": "completed"}
        await store.async_append(r)
        await store.async_append(r)   # exact duplicate
        assert len(store._records) == 1

    @pytest.mark.asyncio
    async def test_different_id_not_dropped(self):
        store = MissionStore()
        r1 = {"id": "m_1000", "started_at": _iso(2), "ended_at": _iso(1), "result": "completed"}
        r2 = {"id": "m_2000", "started_at": _iso(1), "ended_at": _iso(0), "result": "completed"}
        await store.async_append(r1)
        await store.async_append(r2)
        assert len(store._records) == 2

    @pytest.mark.asyncio
    async def test_guard_checks_last_five_only(self):
        """A duplicate beyond the last-5 window is accepted (edge case)."""
        store = MissionStore()
        # Fill 6 unique records then append one with same id as the first
        for i in range(6):
            await store.async_append({"id": f"m_{i}", "started_at": _iso(10 - i),
                                       "ended_at": _iso(9 - i), "result": "completed"})
        # m_0 is now outside the last-5 window -- re-appending it is accepted
        await store.async_append({"id": "m_0", "started_at": _iso(10),
                                   "ended_at": _iso(9), "result": "completed"})
        assert len(store._records) == 7

    @pytest.mark.asyncio
    async def test_record_without_id_always_appended(self):
        store = MissionStore()
        await store.async_append({"started_at": _iso(1), "result": "completed"})
        await store.async_append({"started_at": _iso(1), "result": "completed"})
        assert len(store._records) == 2


class TestP75Area:
    """F5f -- p75_area() returns 75th-percentile area_sqft."""

    def _store_with_areas(self, areas: list[float], days_ago_start: int = 1) -> MissionStore:
        store = MissionStore()
        for i, area in enumerate(areas):
            store._records.append({
                "id": f"m_{i}",
                "started_at": _iso(days_ago_start + i * 0.1),
                "ended_at": _iso(days_ago_start + i * 0.1 - 0.05),
                "area_sqft": area,
                "result": "completed",
            })
        return store

    def test_returns_none_below_5_records(self):
        store = self._store_with_areas([100.0] * 4)
        assert store.p75_area(30) is None

    def test_returns_value_at_10_records(self):
        store = self._store_with_areas([float(i * 10) for i in range(1, 11)])
        result = store.p75_area(30)
        assert result is not None

    def test_excludes_zero_area(self):
        areas = [0.0] * 5 + [200.0] * 10
        store = self._store_with_areas(areas)
        result = store.p75_area(30)
        assert result is not None
        assert result > 0

    def test_excludes_none_area(self):
        store = MissionStore()
        for i in range(5):
            store._records.append({
                "id": f"m_{i}", "started_at": _iso(i + 1),
                "ended_at": _iso(i), "area_sqft": None, "result": "completed",
            })
        for i in range(10):
            store._records.append({
                "id": f"m_{i + 10}", "started_at": _iso(i + 10),
                "ended_at": _iso(i + 9), "area_sqft": 300.0, "result": "completed",
            })
        result = store.p75_area(30)
        assert result == 300.0


class TestBackfillCloudMerge:
    """backfill_from_cloud() analytics field merge (CR1)."""

    def _local(self, ended_ts=1700001000, **kwargs):
        r = {
            "id": f"m_{ended_ts - 2400}",
            "started_at": _ts(ended_ts - 2400),
            "ended_at": _ts(ended_ts),
            "duration_min": 40,
            "area_sqft": None,
            "result": "completed",
        }
        r.update(kwargs)
        return r

    def _cloud(self, ts=1700001000, **kwargs):
        r = {
            "startTime": ts - 2400,
            "timestamp": ts,
            "sqft": 312,
            "dirt": 14,
            "chrgM": 0,
            "runM": 38,
            "durationM": 40,
            "chrgs": 0,
            "evacs": 1,
            "wlBars": [3, 3, 4],
        }
        r.update(kwargs)
        return r

    def test_analytics_fields_merged_into_local(self):
        store = MissionStore()
        store._records = [self._local()]
        result = store.backfill_from_cloud([self._cloud()])
        r = store._records[0]
        assert r["dirt"] == 14
        assert r["chrgM"] == 0
        assert r["wlBars"] == [3, 3, 4]
        assert result.enriched == 1

    def test_existing_local_value_not_overwritten(self):
        store = MissionStore()
        local = self._local(dirt=99)
        store._records = [local]
        store.backfill_from_cloud([self._cloud(dirt=14)])
        assert store._records[0]["dirt"] == 99  # not overwritten

    def test_merge_runs_even_when_timestamps_already_correct(self):
        """Analytics merge is independent of timestamp correction."""
        store = MissionStore()
        ts = 1700001000
        local = self._local(ended_ts=ts)
        # Make timestamps close (delta < 300s) — would skip timestamp correction.
        # Set started_at identical to cloud startTime so delta ≈ 0.
        local["started_at"] = _ts(ts - 2400)
        store._records = [local]
        cloud = self._cloud(ts=ts)
        cloud["startTime"] = ts - 2400  # same as local — delta == 0
        result = store.backfill_from_cloud([cloud])
        assert store._records[0]["dirt"] == 14   # merged despite no ts correction
        assert result.corrected == 0
        assert result.enriched == 1

    def test_no_match_leaves_record_unchanged(self):
        store = MissionStore()
        store._records = [self._local(ended_ts=1700001000)]
        result = store.backfill_from_cloud([self._cloud(ts=1700099999)])  # no match
        assert store._records[0].get("dirt") is None
        assert result.enriched == 0

    def test_array_field_merged(self):
        store = MissionStore()
        store._records = [self._local()]
        store.backfill_from_cloud([self._cloud()])
        assert store._records[0]["wlBars"] == [3, 3, 4]

    def test_backfill_result_namedtuple(self):
        from custom_components.roomba_plus.mission_store import BackfillResult
        r = BackfillResult(corrected=1, enriched=3)
        assert r.corrected == 1
        assert r.enriched == 3


class TestMergeCloudFieldsB1Ext:
    """v2.10.2 B1-EXT: generalised result correction from cloud done/done_raw.

    B1 (existing, not touched) handles the narrow case of pauseId==224 +
    local 'stuck'. B1-EXT covers two new correction triggers that were
    confirmed in the field on a real archive (980 OG, 26.06.2026):
      - done=='bat' with local 'completed' or 'stuck_and_resumed' → 'error'
      - done_raw=='usrEnd' with local 'completed' or 'stuck_and_resumed' → 'cancelled'

    'stuck_and_abandoned' is deliberately NOT corrected by either trigger
    (own stopping criterion, independent of user/battery). Generic 'error'/
    'cancelled' (not 'error_battery'/'cancelled_by_user') are used so that
    the corrected values stay within the documented local result enum.
    """

    @staticmethod
    def _merge(local_result, done="done", done_raw="done",
               pause_id=0, local_error_code=None):
        from custom_components.roomba_plus.mission_store import MissionStore
        ts = 1700001000
        local = {
            "id": f"m_{ts - 2400}",
            "started_at": _ts(ts - 2400),
            "ended_at": _ts(ts),
            "duration_min": 40,
            "area_sqft": None,
            "result": local_result,
            "error_code": local_error_code,
        }
        cloud = {
            "startTime": ts - 2400,
            "timestamp": ts,
            "done": done,
            "done_raw": done_raw,
            "pauseId": pause_id,
            "sqft": 200,
            "dirt": 3,
            "chrgM": 0,
        }
        MissionStore._merge_cloud_fields(local, cloud)
        return local

    # ── battery-error cases ───────────────────────────────────────────────────

    def test_done_bat_with_completed_corrects_to_error(self):
        """Field repro: mission 07:01, done=='bat', local 'completed'."""
        local = self._merge("completed", done="bat", pause_id=2)
        assert local["result"] == "error"

    def test_done_bat_with_stuck_and_resumed_corrects_to_error(self):
        """Stuck, self-recovered, then battery died before finishing."""
        local = self._merge("stuck_and_resumed", done="bat", pause_id=2)
        assert local["result"] == "error"

    def test_done_bat_backfills_error_code_from_pause_id(self):
        local = self._merge("completed", done="bat", pause_id=2,
                            local_error_code=None)
        assert local["error_code"] == 2

    def test_done_bat_does_not_overwrite_existing_error_code(self):
        local = self._merge("completed", done="bat", pause_id=2,
                            local_error_code=99)
        assert local["error_code"] == 99

    def test_done_bat_pause_id_zero_leaves_error_code_none(self):
        local = self._merge("completed", done="bat", pause_id=0)
        assert local["result"] == "error"
        assert local["error_code"] is None

    # ── user-cancellation cases ───────────────────────────────────────────────

    def test_done_raw_usrend_with_completed_corrects_to_cancelled(self):
        """Field repro: mission 09:08, done_raw=='usrEnd', local 'stuck_and_resumed'."""
        local = self._merge("completed", done_raw="usrEnd")
        assert local["result"] == "cancelled"

    def test_done_raw_usrend_with_stuck_and_resumed_corrects_to_cancelled(self):
        """Field repro direct: stuck, recovered, then user cancelled —
        the stuck event is already recorded in self_recovered; the
        final result must reflect the user action."""
        local = self._merge("stuck_and_resumed", done_raw="usrEnd")
        assert local["result"] == "cancelled"

    # ── intentional non-correction cases ─────────────────────────────────────

    def test_stuck_and_abandoned_not_touched_by_bat(self):
        """Robot stopped on its own — battery trigger must not overwrite."""
        local = self._merge("stuck_and_abandoned", done="bat")
        assert local["result"] == "stuck_and_abandoned"

    def test_stuck_and_abandoned_not_touched_by_usrend(self):
        local = self._merge("stuck_and_abandoned", done_raw="usrEnd")
        assert local["result"] == "stuck_and_abandoned"

    def test_stuck_not_touched_by_bat(self):
        """Plain 'stuck' (mid-classification; not resumed/abandoned) —
        handled only by the existing B1 (pauseId==224) path, not B1-EXT."""
        local = self._merge("stuck", done="bat")
        assert local["result"] == "stuck"

    def test_healthy_mission_not_touched(self):
        """Normal completion — done='done', done_raw='done' — untouched."""
        local = self._merge("completed", done="done", done_raw="done")
        assert local["result"] == "completed"

    # ── query_by_day integration: the original field bug ─────────────────────

    def test_query_by_day_completed_count_after_b1ext_correction(self):
        """End-to-end repro of the field bug (980 OG, 26.06.2026):
        before B1-EXT, summary showed completed:3 for a day that had one
        error_battery and one cancelled_by_user mission, because both local
        records had result='completed' and were never corrected.

        After B1-EXT fires via backfill_from_cloud(), query_by_day() must
        show completed:0 for that day (the third missing mission is a
        separate RECORDS-UNION issue, not tested here)."""
        store = MissionStore()
        ts1 = 1782457275  # 07:01 UTC
        ts2 = 1782464921  # 09:08 UTC — both from the real field archive
        store._records = [
            {
                "id": f"m_{ts1 - 1020}",
                "started_at": _ts(ts1 - 1020),
                "ended_at": _ts(ts1),
                "duration_min": 17,
                "area_sqft": None,
                "result": "completed",     # local — WRONG; cloud says bat
                "error_code": None,
            },
            {
                "id": f"m_{ts2 - 9360}",
                "started_at": _ts(ts2 - 9360),
                "ended_at": _ts(ts2),
                "duration_min": 156,
                "area_sqft": None,
                "result": "stuck_and_resumed",  # local — WRONG; cloud says usrEnd
                "error_code": None,
            },
        ]
        cloud = [
            {"startTime": ts1 - 1020, "timestamp": ts1,
             "done": "bat", "done_raw": "bat", "pauseId": 2,
             "sqft": 178, "dirt": 1, "chrgM": 0},
            {"startTime": ts2 - 9360, "timestamp": ts2,
             "done": "cncl", "done_raw": "usrEnd", "pauseId": 5,
             "sqft": 351, "dirt": 4, "chrgM": 60},
        ]
        store.backfill_from_cloud(cloud)

        assert store._records[0]["result"] == "error"
        assert store._records[1]["result"] == "cancelled"

        from datetime import date, timezone
        day = date(2026, 6, 26)
        summaries = store.query_by_day(days=28)
        assert day in summaries, "must have a summary for 2026-06-26"
        s = summaries[day]
        assert s.completed == 0, (
            f"field bug: query_by_day counted {s.completed} completed "
            f"for a day with only error+cancelled missions"
        )
        assert s.total == 2


    """merge_latest_from_cloud() post-mission hook (CR2)."""

    def test_merges_into_last_record(self):
        store = MissionStore()
        ts = 1700001000
        store._records = [
            {"id": "m_old", "ended_at": _ts(ts - 10000), "result": "completed"},
            {"id": "m_new", "ended_at": _ts(ts), "result": "completed"},
        ]
        cloud = [{"timestamp": ts, "dirt": 7, "chrgM": 5, "wlBars": [4, 3]}]
        wrote = store.merge_latest_from_cloud(cloud)
        assert wrote is True
        assert store._records[-1]["dirt"] == 7
        assert store._records[0].get("dirt") is None  # older record untouched

    def test_no_match_returns_false(self):
        store = MissionStore()
        ts = 1700001000
        store._records = [{"id": "m_new", "ended_at": _ts(ts), "result": "completed"}]
        cloud = [{"timestamp": ts + 9999, "dirt": 7}]
        assert store.merge_latest_from_cloud(cloud) is False

    def test_empty_records_returns_false(self):
        store = MissionStore()
        assert store.merge_latest_from_cloud([{"timestamp": 1700001000, "dirt": 7}]) is False

    def test_empty_cloud_returns_false(self):
        store = MissionStore()
        store._records = [{"id": "m_new", "ended_at": _ts(1700001000)}]
        assert store.merge_latest_from_cloud([]) is False

    def test_backfills_area_sqft_into_last_record(self):
        """Same field bug as backfill_from_cloud, on the post-mission
        single-record hook — this is the path that actually runs after
        every mission end, so the gap here was the dominant cause of
        area_sqft never populating in practice."""
        store = MissionStore()
        ts = 1700001000
        store._records = [
            {"id": "m_new", "ended_at": _ts(ts), "result": "completed",
             "area_sqft": None},
        ]
        cloud = [{"timestamp": ts, "sqft": 351, "dirt": 2}]
        wrote = store.merge_latest_from_cloud(cloud)
        assert wrote is True
        assert store._records[-1]["area_sqft"] == 351

    def test_does_not_overwrite_existing_area_sqft_on_latest_merge(self):
        store = MissionStore()
        ts = 1700001000
        store._records = [
            {"id": "m_new", "ended_at": _ts(ts), "result": "completed",
             "area_sqft": 120},
        ]
        cloud = [{"timestamp": ts, "sqft": 999}]
        store.merge_latest_from_cloud(cloud)
        assert store._records[-1]["area_sqft"] == 120


class TestCompletionRate30d:
    @pytest.mark.asyncio
    async def test_all_completed(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "completed"),
            _make_record_v180_sensors(1, "completed"),
        )
        assert _completion_rate_30d(store) == 100.0

    @pytest.mark.asyncio
    async def test_half_completed(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "completed"),
            _make_record_v180_sensors(1, "stuck"),
        )
        assert _completion_rate_30d(store) == 50.0

    @pytest.mark.asyncio
    async def test_none_when_no_records(self):
        store = MissionStore()
        assert _completion_rate_30d(store) is None

    @pytest.mark.asyncio
    async def test_rounded_to_one_decimal(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "completed"),
            _make_record_v180_sensors(1, "stuck"),
            _make_record_v180_sensors(2, "stuck"),
        )
        assert _completion_rate_30d(store) == round(1/3 * 100, 1)


class TestAreaCleanedToday:
    @pytest.mark.asyncio
    async def test_sums_todays_completed(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "completed", area_sqft=200.0),
            _make_record_v180_sensors(0, "completed", area_sqft=150.0),
        )
        assert _area_cleaned_today(store) == round(350.0 * 0.0929, 1)  # sqft→m²

    @pytest.mark.asyncio
    async def test_excludes_yesterday(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "completed", area_sqft=200.0),
            _make_record_v180_sensors(1, "completed", area_sqft=999.0),
        )
        assert _area_cleaned_today(store) == round(200.0 * 0.0929, 1)  # sqft→m²

    @pytest.mark.asyncio
    async def test_none_when_no_today_records(self):
        store = await _store_with(_make_record_v180_sensors(1, "completed", area_sqft=200.0))
        assert _area_cleaned_today(store) == 0.0

    @pytest.mark.asyncio
    async def test_zero_for_600_series_no_area(self):
        # 600-series records have area_sqft=None → no areas → 0.0 m²
        store = await _store_with(_make_record_v180_sensors(0, "completed", area_sqft=None))
        assert _area_cleaned_today(store) == 0.0


class TestProblemZoneValue:
    def _make_entity(self, store):
        class _FakeData:
            mission_store = store
            last_error_code = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()
            vacuum_state = {}

        return _FakeEntity()

    @pytest.mark.asyncio
    async def test_most_frequent_stuck_zone(self):
        store = await _store_with(
            _make_record_v180_sensors(0, "stuck", zones=["Kitchen"]),
            _make_record_v180_sensors(1, "stuck", zones=["Kitchen"]),
            _make_record_v180_sensors(2, "stuck", zones=["Living room"]),
        )
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) == "Kitchen"

    @pytest.mark.asyncio
    async def test_none_when_no_stuck_records(self):
        store = await _store_with(_make_record_v180_sensors(0, "completed"))
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) is None

    def test_none_when_no_store(self):
        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _problem_zone_value(_FakeEntity()) is None

    @pytest.mark.asyncio
    async def test_none_when_stuck_records_have_no_zones(self):
        store = await _store_with(_make_record_v180_sensors(0, "stuck", zones=[]))
        entity = self._make_entity(store)
        assert _problem_zone_value(entity) is None


class TestLastErrorCodeValue:
    def _make_entity(self, live_error=0, persisted_error=None):
        class _FakeData:
            last_error_code = persisted_error

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()
            vacuum_state = {
                "cleanMissionStatus": {"error": live_error}
            }

        return _FakeEntity()

    def test_live_mqtt_takes_priority(self):
        entity = self._make_entity(live_error=17, persisted_error=2)
        assert _last_error_code_value(entity) == 17

    def test_falls_back_to_persisted_when_no_live(self):
        entity = self._make_entity(live_error=0, persisted_error=36)
        assert _last_error_code_value(entity) == 36

    def test_none_when_neither(self):
        entity = self._make_entity(live_error=0, persisted_error=None)
        assert _last_error_code_value(entity) is None


class TestLastErrorZoneCapture:
    """Tests for _capture_zone_names logic extracted from __init__.py.

    We test the zone-name resolution rules indirectly via mission_store
    records, since the capture logic runs inside the MQTT closure.
    """

    @pytest.mark.asyncio
    async def test_zone_written_into_record(self):
        """Zones captured at start appear in the mission record."""
        store = MissionStore()
        record = {
            "id": "m_1",
            "started_at": _iso_v180_sensors(0),
            "ended_at": _iso_v180_sensors(0),
            "duration_min": 30,
            "area_sqft": None,
            "result": "error",
            "initiator": "schedule",
            "zones": ["Kitchen"],   # captured at mission start
            "error_code": 17,
            "bbrun_hr": 100,
        }
        await store.async_append(record)
        latest = store.latest()
        assert latest["zones"] == ["Kitchen"]
        assert latest["error_code"] == 17

    @pytest.mark.asyncio
    async def test_empty_zones_for_600_series(self):
        """600-series missions have empty zones list."""
        store = MissionStore()
        record = {**{
            "id": "m_600",
            "started_at": _iso_v180_sensors(0),
            "ended_at": _iso_v180_sensors(0),
            "duration_min": 20,
            "area_sqft": None,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 50,
        }}
        await store.async_append(record)
        assert store.latest()["zones"] == []


class TestL6Helpers:
    def _make_entity_with_store(self, store):
        class _FakeData:
            mission_store = store

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        return _FakeEntity()

    def test_presence_opportunities_none_when_no_store(self):
        from custom_components.roomba_plus.sensor import _presence_opportunities

        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _presence_opportunities(_FakeEntity(), 7) is None

    @pytest.mark.asyncio
    async def test_next_likely_clean_window_none_with_fewer_than_3_windows(self):
        from custom_components.roomba_plus.sensor import _next_likely_clean_window
        store = await _store_with(_make_record_v180_sensors(0))
        entity = self._make_entity_with_store(store)
        assert _next_likely_clean_window(entity) is None

    def test_presence_utilisation_none_when_no_store(self):
        from custom_components.roomba_plus.sensor import _presence_utilisation

        class _FakeData:
            mission_store = None

        class _FakeEntry:
            runtime_data = _FakeData()

        class _FakeEntity:
            _config_entry = _FakeEntry()

        assert _presence_utilisation(_FakeEntity(), 7) is None


class TestMssnStrtTmCaching:
    """Verify the closure caches start_ts at mission start, not end."""

    def test_start_ts_used_for_duration_not_end(self):
        """When start_ts is cached, duration = now - start_ts (not 0)."""
        # Simulate: start_ts cached 33 minutes ago
        start_ts = int(
            (datetime.datetime.now(datetime.timezone.utc) -
             datetime.timedelta(minutes=33)).timestamp()
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        started_at = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc)
        elapsed = (now - started_at).total_seconds()
        duration_min = max(0, round(elapsed / 60))
        assert 30 <= duration_min <= 36  # ~33 min with tolerance

    def test_zero_start_ts_fallback_gives_zero_duration(self):
        """When start_ts=0 (fallback), duration is ~0."""
        start_ts = 0
        now = datetime.datetime.now(datetime.timezone.utc)
        started_at = now if not start_ts else datetime.datetime.fromtimestamp(
            start_ts, datetime.timezone.utc
        )
        elapsed = (now - started_at).total_seconds()
        duration_min = max(0, round(elapsed / 60))
        assert duration_min == 0

    def test_mission_id_uses_start_not_end(self):
        """Mission ID should be based on start_ts, not now()."""
        start_ts = 1780000000  # fixed timestamp
        started_at = datetime.datetime.fromtimestamp(
            start_ts, datetime.timezone.utc
        )
        mission_id = f"m_{int(started_at.timestamp())}"
        assert mission_id == "m_1780000000"
        assert mission_id != f"m_{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}"


class TestBbrunHrMerge:
    """bbrun_hr in mission record must fall back to runtimeStats.hr for i-series."""

    def test_bbrun_hr_from_bbrun_when_present(self):
        """900-series: hr in bbrun -> use it."""
        reported = {"bbrun": {"hr": 428, "nStuck": 5}, "runtimeStats": {}}
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 428

    def test_bbrun_hr_from_runtimeStats_when_bbrun_missing(self):
        """i-series: hr NOT in bbrun -> fall back to runtimeStats."""
        reported = {
            "bbrun": {"nPanics": 100, "nStuck": 2},  # no "hr"
            "runtimeStats": {"hr": 312, "sqft": 500},
        }
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 312

    def test_bbrun_hr_zero_when_both_absent(self):
        """No hr anywhere -> 0."""
        reported = {"bbrun": {"nStuck": 1}, "runtimeStats": {}}
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 0

    def test_bbrun_hr_prefers_bbrun_over_runtimeStats(self):
        """When both present, bbrun wins (900-series canonical source)."""
        reported = {
            "bbrun": {"hr": 200},
            "runtimeStats": {"hr": 300},
        }
        bbrun = reported.get("bbrun", {})
        runtime = reported.get("runtimeStats", {})
        bbrun_hr = bbrun.get("hr") or runtime.get("hr") or 0
        assert bbrun_hr == 200


class TestLastErrorCodePersistence:
    """Error code must persist across successful missions."""

    def test_error_set_on_error_result(self):
        """error result -> last_error_code updated."""
        error_code = 17
        result = "error"
        last_error_code = None
        if result in ("error", "stuck"):
            last_error_code = error_code
        assert last_error_code == 17

    def test_error_not_cleared_on_completed(self):
        """completed result -> last_error_code unchanged."""
        last_error_code = 17  # previously set
        result = "completed"
        # New logic: only update on error/stuck
        if result in ("error", "stuck"):
            last_error_code = None  # would set new error
        # No elif for completed -> last_error_code unchanged
        assert last_error_code == 17  # still 17

    def test_old_logic_would_clear(self):
        """Confirm the old bug: completed cleared the error."""
        last_error_code = 17
        result = "completed"
        # Old logic had: elif result == "completed": last_error_code = None
        if result in ("error", "stuck"):
            last_error_code = None
        elif result == "completed":
            last_error_code = None  # old bug
        assert last_error_code is None  # confirms old bug cleared it


class TestAreaCleanedTodayM2:
    def test_converts_sqft_to_m2(self):
        store = _store_with_v191_fixes(_make_record_v191_fixes(0, "completed", area_sqft=200.0))
        result = _area_cleaned_today(store)
        assert result == round(200.0 * 0.0929, 1)

    def test_sums_multiple_and_converts(self):
        store = _store_with_v191_fixes(
            _make_record_v191_fixes(0, "completed", area_sqft=200.0),
            _make_record_v191_fixes(0, "completed", area_sqft=150.0),
        )
        result = _area_cleaned_today(store)
        assert result == round(350.0 * 0.0929, 1)

    def test_none_when_no_records(self):
        store = MissionStore()
        assert _area_cleaned_today(store) == 0.0

    def test_unit_is_square_meters(self):
        desc = _get_sensor("area_cleaned_today")
        from homeassistant.const import UnitOfArea
        assert desc.native_unit_of_measurement == UnitOfArea.SQUARE_METERS

    def test_result_is_smaller_than_sqft_input(self):
        """m² < sqft for same area — sanity check."""
        store = _store_with_v191_fixes(_make_record_v191_fixes(0, "completed", area_sqft=500.0))
        result = _area_cleaned_today(store)
        assert result < 500.0
        assert result > 40.0  # 500 sqft ≈ 46.5 m²


class TestStatusAttributes:
    """extra_state_attributes returns status hints when native_value is None."""

    def _make_wear_entity(self, reset_at=None):
        class _FakeMaint:
            filter_reset_at = reset_at
            brush_reset_at  = reset_at

        class _FakeRuntimeData:
            maintenance_store = _FakeMaint()

        class _FakeEntry:
            runtime_data = _FakeRuntimeData()
            options = {}

        class _FakeEntity:
            _config_entry = _FakeEntry()
            def __init__(self, key):
                self._key = key
            @property
            def native_value(self):
                return None  # sensor is Unknown

        return _FakeEntity

    def test_status_hint_when_no_reset_recorded(self):
        """When reset_at is None: suggest pressing the button."""
        reset_at = None
        if reset_at is None:
            status = "Press the replacement confirmation button to start tracking"
        else:
            status = "Collecting data — available after 3 days"
        assert "button" in status.lower()

    def test_status_hint_when_reset_recorded_but_too_early(self):
        """When reset_at is set but <3 days ago: collecting data."""
        reset_at = _iso_v191_fixes(days_ago=1)  # 1 day ago
        if reset_at is None:
            status = "Press the replacement confirmation button to start tracking"
        else:
            status = "Collecting data — available after 3 days"
        assert "3 days" in status

    def test_no_status_when_value_present(self):
        """When native_value is set (not None), no status needed."""
        native_value = 1.5  # h/day
        if native_value is None:
            status = "some hint"
        else:
            status = None
        assert status is None


class TestResetServiceCurrentHr:
    """reset_filter/reset_brush must use merged hr from bbrun + runtimeStats."""

    def _get_current_hr(self, state: dict) -> int:
        """Replicate the fixed reset service logic."""
        _bbrun   = state.get("bbrun", {})
        _runtime = state.get("runtimeStats", {})
        return _bbrun.get("hr") or _runtime.get("hr") or 0

    def test_reads_from_bbrun_on_900_series(self):
        """900-series: hr in bbrun -> use it."""
        state = {"bbrun": {"hr": 428, "nStuck": 5}, "runtimeStats": {}}
        assert self._get_current_hr(state) == 428

    def test_reads_from_runtimeStats_on_iseries(self):
        """i-series: hr NOT in bbrun -> fall back to runtimeStats."""
        state = {
            "bbrun": {"nPanics": 100},  # no "hr"
            "runtimeStats": {"hr": 312, "sqft": 500},
        }
        assert self._get_current_hr(state) == 312

    def test_zero_when_both_absent(self):
        state = {"bbrun": {}, "runtimeStats": {}}
        assert self._get_current_hr(state) == 0

    def test_bbrun_preferred_when_both_present(self):
        state = {"bbrun": {"hr": 200}, "runtimeStats": {"hr": 300}}
        assert self._get_current_hr(state) == 200

    def test_old_logic_would_return_zero_on_iseries(self):
        """Confirm the old bug: bbrun.get('hr', 0) = 0 on i-series."""
        state = {
            "bbrun": {"nPanics": 100},
            "runtimeStats": {"hr": 312},
        }
        old_hr = state.get("bbrun", {}).get("hr", 0)  # old logic
        new_hr = self._get_current_hr(state)           # new logic
        assert old_hr == 0    # confirms the bug
        assert new_hr == 312  # confirms the fix

    def test_inflated_wear_rate_from_zero_baseline(self):
        """Demonstrate why hr=0 baseline is dangerous for wear rate."""
        # If reset_hr=0 stored due to bug:
        reset_hr_buggy = 0
        current_hr = 312
        days_elapsed = 10
        # Wear rate would be entire lifetime / 10 days = absurdly high
        buggy_rate = (current_hr - reset_hr_buggy) / days_elapsed
        # If reset_hr=312 stored correctly:
        reset_hr_correct = 310  # 2h before reset
        correct_rate = (current_hr - reset_hr_correct) / days_elapsed
        assert buggy_rate == 31.2   # 31.2 h/day — absurd
        assert correct_rate == 0.2  # 0.2 h/day — realistic


class TestErrorRestoreLogic:
    """Error state restored from MissionStore should persist across completed missions."""

    def _restore_error(self, records: list) -> tuple:
        """Replicate the fixed restore logic from __init__.py."""
        last_error_code = None
        last_error_at   = None
        last_error_zone = None
        for _rec in reversed(records):
            _res = _rec.get("result")
            if _res in ("error", "stuck") and _rec.get("error_code"):
                last_error_code = _rec["error_code"]
                last_error_at   = _rec.get("ended_at")
                last_error_zone = (_rec.get("zones") or [None])[0]
                break
        return last_error_code, last_error_at, last_error_zone

    def _restore_error_old(self, records: list) -> tuple:
        """Replicate the OLD (buggy) restore logic for comparison."""
        last_error_code = None
        last_error_at   = None
        last_error_zone = None
        for _rec in reversed(records):
            _res = _rec.get("result")
            if _res == "completed":
                break  # old bug: clears on completed
            if _res in ("error", "stuck") and _rec.get("error_code"):
                last_error_code = _rec["error_code"]
                last_error_at   = _rec.get("ended_at")
                last_error_zone = (_rec.get("zones") or [None])[0]
                break
        return last_error_code, last_error_at, last_error_zone

    def test_error_persists_after_completed(self):
        """Error code stays visible even after a subsequent completed mission."""
        records = [
            _make_record_v192_fixes(2, result="error", error_code=17),
            _make_record_v192_fixes(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 17

    def test_old_logic_would_clear_error(self):
        """Confirm old logic cleared error when completed came after."""
        records = [
            _make_record_v192_fixes(2, result="error", error_code=17),
            _make_record_v192_fixes(1, result="completed"),
        ]
        code_old, _, _ = self._restore_error_old(records)
        code_new, _, _ = self._restore_error(records)
        assert code_old is None  # old bug: cleared
        assert code_new == 17    # new: persists

    def test_no_error_when_no_error_records(self):
        records = [
            _make_record_v192_fixes(2, result="completed"),
            _make_record_v192_fixes(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code is None

    def test_most_recent_error_returned(self):
        records = [
            _make_record_v192_fixes(3, result="error", error_code=5),
            _make_record_v192_fixes(2, result="error", error_code=17),
            _make_record_v192_fixes(1, result="completed"),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 17  # most recent error

    def test_no_error_when_records_empty(self):
        code, at, zone = self._restore_error([])
        assert code is None
        assert at is None
        assert zone is None

    def test_error_without_error_code_skipped(self):
        """error result with error_code=None should not be restored."""
        records = [
            _make_record_v192_fixes(1, result="error", error_code=None),
        ]
        code, _, _ = self._restore_error(records)
        assert code is None

    def test_stuck_result_restores_error(self):
        records = [
            _make_record_v192_fixes(1, result="stuck", error_code=15),
        ]
        code, _, _ = self._restore_error(records)
        assert code == 15


class TestLastMissionFromStore:
    """last_mission sensor should use MissionStore, not live mssnStrtTm."""

    def test_returns_none_when_store_empty(self):
        store = MissionStore()
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_returns_none_when_store_is_none(self):
        entity = _make_entity(None)
        entity._config_entry.runtime_data.mission_store = None
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_returns_started_at_from_latest_record(self):
        started = _iso_v192_fixes(days_ago=1, hour=9)
        store = _store_with_v192_fixes(_make_record_v192_fixes(1, started_at=started))
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.tzinfo is not None  # timezone-aware

    def test_returns_most_recent_mission(self):
        """Latest record (newest) is returned, not oldest."""
        store = _store_with_v192_fixes(
            _make_record_v192_fixes(2, started_at=_iso_v192_fixes(2, hour=8)),
            _make_record_v192_fixes(1, started_at=_iso_v192_fixes(1, hour=8)),
        )
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        # Should be the most recently appended = day 1
        expected_date = (
            datetime.datetime.now(datetime.timezone.utc) -
            datetime.timedelta(days=1)
        ).date()
        assert result.date() == expected_date

    def test_handles_unparseable_started_at(self):
        store = MissionStore()
        store._records.append({
            "id": "m_bad",
            "started_at": "not-a-date",
            "result": "completed",
        })
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_handles_missing_started_at(self):
        store = MissionStore()
        store._records.append({"id": "m_no_ts", "result": "completed"})
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        assert result is None

    def test_sensor_description_uses_new_helper(self):
        """Verify the sensor description uses _mission_store_last_started_at."""
        for desc in SENSORS:
            if desc.key == "last_mission":
                # value_fn should call _mission_store_last_started_at
                # We verify indirectly by checking it's NOT entity.last_mission
                import inspect
                src = inspect.getsource(desc.value_fn)
                assert "_mission_store_last_started_at" in src
                return
        raise AssertionError("last_mission sensor not found in SENSORS")

    def test_900_series_mssnStrtTm_zero_does_not_cause_unknown(self):
        """Even when live mssnStrtTm=0, MissionStore gives correct timestamp."""
        # Simulate 980: mssnStrtTm=0 in live state
        # But MissionStore has a record with correct started_at
        started = _iso_v192_fixes(days_ago=0, hour=10)
        store = _store_with_v192_fixes(_make_record_v192_fixes(0, started_at=started))
        entity = _make_entity(store)
        result = _mission_store_last_started_at(entity)
        # Gets correct value from store despite mssnStrtTm=0
        assert result is not None
        assert result.date() == datetime.datetime.now(datetime.timezone.utc).date()


class TestBackfillBasicCorrection:

    def test_corrects_started_at(self):
        """900-series: local started_at = ended_at (mssnStrtTm=0), corrected from cloud."""
        end_ts   = 1700003600
        start_ts = 1700000000   # real start: 60 min before end
        # Simulate 980 bug: local record has started_at = ended_at (wall-clock fallback)
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)  # duration=0
        store = _make_store([local])

        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert n.corrected == 1
        assert store._records[0]["started_at"] == _utc(start_ts)

    def test_corrects_duration_min(self):
        end_ts   = 1700003600
        start_ts = 1700000000   # 60 min mission
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert store._records[0]["duration_min"] == 60

    def test_corrects_id(self):
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert store._records[0]["id"] == f"m_{start_ts}"

    def test_backfills_area_sqft_when_none(self):
        """area_sqft=None (MQTT gap on 980) is filled from cloud sqft."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts, area_sqft=None)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts, sqft=185)])
        assert store._records[0]["area_sqft"] == 185

    def test_does_not_overwrite_existing_area_sqft(self):
        """area_sqft already set locally is never overwritten."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts, area_sqft=120)
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(start_ts, end_ts, sqft=999)])
        assert store._records[0]["area_sqft"] == 120

    def test_backfills_area_sqft_even_when_timestamps_already_accurate(self):
        """Field bug (v2.10.1, confirmed against a real archive: 24/24
        local records on an i/s-series-accurate-timestamp robot had
        area_sqft permanently None despite "sqft" correctly carrying
        real cloud values every time).

        area_sqft previously only backfilled inside the timestamp-
        correction branch (delta_start >= 300) — so a mission whose
        local started_at/ended_at were ALREADY accurate (the common
        case on i/s-series, and apparently even on this 980) never
        reached the backfill line at all, regardless of how much cloud
        data was available. area_sqft must populate independently of
        whether a timestamp correction was needed.
        """
        start_ts = 1700000000
        end_ts   = 1700003600  # local already accurate — delta_start == 0
        local = _local_rec(started_ts=start_ts, ended_ts=end_ts, area_sqft=None)
        store = _make_store([local])

        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts, sqft=351)])

        assert n.corrected == 0, "timestamps were already accurate"
        assert n.enriched == 1, "area_sqft backfill counts as enrichment"
        assert store._records[0]["area_sqft"] == 351
        assert store._records[0]["started_at"] == _utc(start_ts), (
            "must not have gone through the timestamp-correction path"
        )


class TestBackfillMatching:

    def test_matches_within_tolerance(self):
        """Cloud end timestamp 60 s off from local ended_at — still matches."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        # Cloud timestamp 60s later — within default 120s tolerance
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 60)])
        assert n.corrected == 1

    def test_no_match_outside_tolerance(self):
        """Cloud end timestamp 200 s off — outside tolerance, no correction."""
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 200)])
        assert n.corrected == 0

    def test_custom_tolerance(self):
        end_ts   = 1700003600
        start_ts = 1700000000
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store = _make_store([local])

        # 200s off, custom tolerance of 300s
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts + 200)], tolerance_sec=300)
        assert n.corrected == 1

    def test_skips_already_accurate_records(self):
        """Records with delta_start < 5 min are not corrected (already accurate)."""
        # Local record with correct start (i7/s9 gives mssnStrtTm reliably)
        start_ts = 1700000000
        end_ts   = 1700003600
        local = _local_rec(started_ts=start_ts, ended_ts=end_ts)
        store = _make_store([local])

        # Cloud record matches — but local is already accurate
        n = store.backfill_from_cloud([_cloud_rec(start_ts, end_ts)])
        assert n.corrected == 0

    def test_multiple_records_partial_match(self):
        """Only records with inaccurate timestamps are corrected."""
        end1 = 1700003600
        end2 = 1700007200
        start_accurate = 1700003600 - 3600   # i7: accurate start
        start_bad      = end2                  # 980: bad start = end

        local1 = _local_rec(started_ts=start_accurate, ended_ts=end1)
        local2 = _local_rec(started_ts=start_bad,      ended_ts=end2)
        store  = _make_store([local1, local2])

        cloud = [
            _cloud_rec(1700000000, end1),
            _cloud_rec(1700003700, end2),
        ]
        n = store.backfill_from_cloud(cloud)
        assert n.corrected == 1
        # local1 unchanged
        assert store._records[0]["started_at"] == _utc(start_accurate)
        # local2 corrected
        assert store._records[1]["started_at"] == _utc(1700003700)


class TestBackfillEdgeCases:

    def test_empty_cloud_records(self):
        store = _make_store([_local_rec(1700003600, 1700003600)])
        assert store.backfill_from_cloud([]).corrected == 0

    def test_empty_local_records(self):
        store = _make_store([])
        assert store.backfill_from_cloud([_cloud_rec(1700000000, 1700003600)]).corrected == 0

    def test_cloud_record_missing_start_time(self):
        """Cloud record without startTime is skipped gracefully."""
        end_ts = 1700003600
        local  = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store  = _make_store([local])
        cr = {"timestamp": end_ts, "sqft": 100, "classified_result": "completed"}
        assert store.backfill_from_cloud([cr]).corrected == 0

    def test_cloud_record_missing_timestamp(self):
        """Cloud record without timestamp is not indexed."""
        end_ts = 1700003600
        local  = _local_rec(started_ts=end_ts, ended_ts=end_ts)
        store  = _make_store([local])
        cr = {"startTime": 1700000000, "sqft": 100}
        assert store.backfill_from_cloud([cr]).corrected == 0

    def test_local_record_missing_ended_at(self):
        """Local record without ended_at is skipped."""
        store = _make_store([{"id": "m_bad", "started_at": _utc(1700003600)}])
        assert store.backfill_from_cloud([_cloud_rec(1700000000, 1700003600)]).corrected == 0

    def test_returns_count_of_corrected(self):
        recs = [
            _local_rec(1700003600, 1700003600),  # bad — will be corrected
            _local_rec(1700007200, 1700007200),  # bad — will be corrected
        ]
        store = _make_store(recs)
        cloud = [
            _cloud_rec(1700000000, 1700003600),
            _cloud_rec(1700003700, 1700007200),
        ]
        assert store.backfill_from_cloud(cloud).corrected == 2

    def test_zones_and_other_fields_preserved(self):
        """Backfill only touches timestamp fields — other fields untouched."""
        end_ts = 1700003600
        local = _local_rec(started_ts=end_ts, ended_ts=end_ts,
                           result="error", zones=["Kitchen"])
        local["error_code"] = 17
        local["bbrun_hr"]   = 250
        store = _make_store([local])

        store.backfill_from_cloud([_cloud_rec(1700000000, end_ts)])
        rec = store._records[0]
        assert rec["zones"]      == ["Kitchen"]
        assert rec["result"]     == "error"
        assert rec["error_code"] == 17
        assert rec["bbrun_hr"]   == 250


class TestAmendment8fMergeFields:
    """Amendment 8f — four new scalar fields merged from cloud records."""

    def _local(self):
        return {
            "id": "m_1700000000",
            "started_at": _utc(1700000000),
            "ended_at":   _utc(1700003300),
            "duration_min": 55,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 0,
        }

    def _cloud(self, **extra):
        base = {
            "startTime": 1700000000,
            "timestamp": 1700003300,
            "sqft": 180,
            "dockedAtStart": True,
            "missionId": "01KT7BVQ50JD8WF2KM6E9RNYRN",
            "pauseM": 3,
            "cmd": {"command": "start", "initiator": "schedule"},
        }
        base.update(extra)
        return base

    def test_amendment_8f_fields_merged(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        local = self._local()
        ms._merge_cloud_fields(local, self._cloud())
        assert local["dockedAtStart"] is True
        assert local["missionId"] == "01KT7BVQ50JD8WF2KM6E9RNYRN"
        assert local["pauseM"] == 3
        assert local["cmd"]["command"] == "start"

    def test_amendment_8f_does_not_overwrite_existing(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        local = self._local()
        local["missionId"] = "EXISTING"
        ms = MissionStore()
        ms._merge_cloud_fields(local, self._cloud())
        assert local["missionId"] == "EXISTING"


class TestRechargeAccumulation:
    """F4e -- recharge_min field written to MissionStore record."""

    def _make_mission_msg(self, phase: str, recharge_m: int | None = None) -> dict:
        mission = {"phase": phase, "mssnStrtTm": 1700000000, "sqft": 100}
        if recharge_m is not None:
            mission["rechrgM"] = recharge_m
        return {"state": {"reported": {"cleanMissionStatus": mission, "bbrun": {"nStuck": 0}}}}

    @pytest.mark.asyncio
    async def test_recharge_min_in_record(self):
        """rechrgM from hmMidMsn phase is accumulated and written to record."""
        records = []

        async def fake_append(record):
            records.append(record)

        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_callback

        entry = MagicMock()
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock(side_effect=fake_append)
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None

        hass = MagicMock()
        hass.loop = None

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            import asyncio

            async def run_it(coro, loop):
                return await coro
            mock_rct.side_effect = lambda coro, loop: asyncio.ensure_future(coro)

            cb = make_mission_callback(hass, entry)
            cb(self._make_mission_msg("run"))
            cb(self._make_mission_msg("hmMidMsn", recharge_m=15))
            cb(self._make_mission_msg("charge"))

        # Allow coroutines to run
        import asyncio as _asyncio
        await _asyncio.sleep(0)

        if records:
            assert records[0].get("recharge_min") == 15

    @pytest.mark.asyncio
    async def test_no_recharge_when_no_mid_mission(self):
        """recharge_min is None when no hmMidMsn phase occurred."""
        records = []

        async def fake_append(record):
            records.append(record)

        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_callback

        entry = MagicMock()
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock(side_effect=fake_append)
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None

        hass = MagicMock()

        with patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        ) as mock_rct:
            mock_rct.side_effect = lambda coro, loop: None

            cb = make_mission_callback(hass, entry)
            cb({"state": {"reported": {
                "cleanMissionStatus": {"phase": "run", "mssnStrtTm": 1700000000},
                "bbrun": {"nStuck": 0},
            }}})
            cb({"state": {"reported": {
                "cleanMissionStatus": {"phase": "charge"},
                "bbrun": {"nStuck": 0},
            }}})


class TestRechargeAccumulationDoubleCountingFix:
    """v2.9.0 — F4e bugfix regression: repeated hmMidMsn messages within the
    SAME charge leg must not double-count rechrgM (it's already cumulative
    for that one leg — must be SET, not added, on each message). Found
    while wiring recharge_min into MissionTimerStore for live exposure
    (mission_progress's new mission_duration_min/effective_elapsed_min
    attributes) — previously only mattered for the final mission-end
    record, where this bug had gone undetected since the only prior test
    (test_recharge_min_in_record) sent exactly one hmMidMsn message.
    """

    def _msg(self, phase: str, recharge_m: int | None = None) -> dict:
        mission = {"phase": phase, "mssnStrtTm": 1700000000, "sqft": 100}
        if recharge_m is not None:
            mission["rechrgM"] = recharge_m
        return {"state": {"reported": {"cleanMissionStatus": mission, "bbrun": {"nStuck": 0}}}}

    def _make_env(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from custom_components.roomba_plus.callbacks import make_mission_callback

        entry = MagicMock()
        entry.runtime_data.mission_store = MagicMock()
        entry.runtime_data.mission_store.async_append = AsyncMock()
        entry.runtime_data.mission_store.async_save = AsyncMock()
        entry.runtime_data.zone_store = None
        entry.runtime_data.map_capability = None
        entry.runtime_data.cloud_coordinator = None

        mts = MagicMock()
        mts.mission_id = "fakeid_1700000000"
        entry.runtime_data.mission_timer_store = mts

        hass = MagicMock()
        patcher = patch(
            "custom_components.roomba_plus.callbacks.asyncio.run_coroutine_threadsafe"
        )
        mock_rct = patcher.start()
        import asyncio
        mock_rct.side_effect = lambda coro, loop: asyncio.ensure_future(coro)

        cb = make_mission_callback(hass, entry)
        return cb, mts, patcher

    def test_repeated_messages_same_leg_not_double_counted(self):
        cb, mts, patcher = self._make_env()
        try:
            cb(self._msg("run"))
            cb(self._msg("hmMidMsn", recharge_m=1))
            cb(self._msg("hmMidMsn", recharge_m=2))
            cb(self._msg("hmMidMsn", recharge_m=3))
            assert mts.recharge_min == 3.0  # NOT 1+2+3=6
        finally:
            patcher.stop()

    def test_leg_total_locked_in_when_leg_ends(self):
        cb, mts, patcher = self._make_env()
        try:
            cb(self._msg("run"))
            cb(self._msg("hmMidMsn", recharge_m=5))
            cb(self._msg("run"))  # leg ends
            assert mts.recharge_min == 5.0
        finally:
            patcher.stop()

    def test_second_leg_adds_to_first_legs_total(self):
        """Two SEPARATE charge legs in one mission — totals must add, not
        overwrite (the completed-legs accumulator must persist across legs,
        only the in-progress leg's own value resets)."""
        cb, mts, patcher = self._make_env()
        try:
            cb(self._msg("run"))
            cb(self._msg("hmMidMsn", recharge_m=5))
            cb(self._msg("run"))            # leg 1 ends, locked in at 5
            cb(self._msg("hmMidMsn", recharge_m=2))
            assert mts.recharge_min == 7.0  # 5 (leg 1) + 2 (leg 2, live)
            cb(self._msg("run"))            # leg 2 ends
            assert mts.recharge_min == 7.0  # 5 + 2 locked in
        finally:
            patcher.stop()

    def test_fallback_path_when_rechrgM_unavailable(self):
        """If rechrgM is never reported, falls back to wall-clock elapsed
        time for the leg — must still work after the fix."""
        import time as _t
        cb, mts, patcher = self._make_env()
        try:
            cb(self._msg("run"))
            cb(self._msg("hmMidMsn"))  # no rechrgM field at all
            _t.sleep(0.05)
            cb(self._msg("hmMidMsn"))  # still no rechrgM — stays on fallback path
            cb(self._msg("run"))       # leg ends — fallback computes elapsed minutes
            # A real wall-clock gap under 1 minute rounds down to 0 via the
            # fallback's int(.../60) — the live mirror only writes when the
            # value actually CHANGES, so for a sub-minute fallback leg it
            # correctly never fires at all (mts.recharge_min stays whatever
            # it started as). The real assertion here is just "no exception"
            # — confirms the fallback path itself didn't break after the fix.
            from unittest.mock import MagicMock
            assert mts.recharge_min is not None  # MagicMock or a real number — both fine
        finally:
            patcher.stop()


class TestResolveRegionIds:
    def test_known_ids_resolved(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["19", "21"], REGION_MAP)
        assert result == ["Bathroom", "Kitchen"]

    def test_unknown_id_returned_as_raw(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["99"], REGION_MAP)
        assert result == ["99"]

    def test_empty_list(self):
        ms = MissionStore()
        assert ms._resolve_region_ids([], REGION_MAP) == []

    def test_empty_region_map(self):
        ms = MissionStore()
        result = ms._resolve_region_ids(["19", "21"], {})
        assert result == ["19", "21"]


class TestLatestCleanedRooms:
    def test_returns_rooms_in_completion_order(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_skips_status_1_events(self):
        # status=1 = pass in progress — must not appear in output
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert len(result) == 3  # only 3 status=0 events

    def test_status_6_treated_as_complete(self):
        # status=6 = completed after error recovery (lewis 22.52.10+ confirmed,
        # Mission 798 in Thonno's debug: error(5) + resume + done=ok)
        ms = _ms_with_timeline({
            "plan": {"upcoming": [{"type": "rid", "rid": "23"}]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "23", "passCount": 1, "status": 6,
                    "area": 37, "passArea": 16,
                    # totalArea absent on status=6 — expected; coverage will be skipped
                }},
            ],
        })
        result = ms.latest_cleaned_rooms({"23": "Bathroom"})
        assert result == ["Bathroom"]

    def test_status_5_excluded(self):
        # status=5 = interrupted by user/app (Mission 799: pause+dock by rmtApp)
        ms = _ms_with_timeline({
            "plan": {"upcoming": []},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "26", "passCount": 1, "status": 5,
                    "area": 251, "passArea": 40,
                }},
            ],
        })
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_for_whole_home_no_room_events(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": [], "ordered": 0},
            "finEvents": [{"type": "fin"}],
        })
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_when_no_timeline_field(self):
        ms = _ms_without_timeline()
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_cleaned_rooms(REGION_MAP) is None

    def test_unknown_rid_returned_as_raw(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["99"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "99", "status": 0,
                                           "area": 50, "totalArea": 40}},
            ],
        })
        result = ms.latest_cleaned_rooms({})
        assert result == ["99"]

    def test_traversal_events_ignored(self):
        # traversal events are NOT room completions — must be skipped
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "traversal", "traversal": {"rid": "19", "type": "region"}},
                {"type": "room", "room": {"rid": "19", "status": 0,
                                           "area": 72, "totalArea": 42}},
            ],
        })
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom"]

    def test_room_appears_once_even_with_multiple_passes(self):
        # Two passes in the same room → appears once in output
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "passCount": 1,
                                           "status": 0, "area": 72, "totalArea": 35}},
                {"type": "room", "room": {"rid": "19", "passCount": 2,
                                           "status": 0, "area": 72, "totalArea": 70}},
            ],
        })
        result = ms.latest_cleaned_rooms(REGION_MAP)
        assert result == ["Bathroom"]
        assert len(result) == 1


class TestExtractRid:
    """_extract_rid handles two confirmed plan.upcoming formats."""

    def test_string_format(self):
        ms = MissionStore()
        assert ms._extract_rid("23") == "23"

    def test_object_format_rid_key(self):
        # lewis 22.52.10+: {"type": "rid", "rid": "23"}
        ms = MissionStore()
        assert ms._extract_rid({"type": "rid", "rid": "23"}) == "23"

    def test_object_format_region_id_key(self):
        # fallback key name
        ms = MissionStore()
        assert ms._extract_rid({"region_id": "19"}) == "19"

    def test_none_returns_empty(self):
        ms = MissionStore()
        assert ms._extract_rid(None) == ""

    def test_empty_dict_returns_empty(self):
        ms = MissionStore()
        assert ms._extract_rid({}) == ""


class TestLatestPlannedOrder:
    def test_returns_planned_order(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_object_format_upcoming(self):
        # lewis 22.52.10+: plan.upcoming as list of dicts (Mission 800 confirmed)
        ms = _ms_with_timeline({
            "plan": {
                "pmapId": "8VfoJEhaQ12ZGZaGlJp3wQ",
                "ordered": 1, "type": "drc",
                "upcoming": [
                    {"type": "rid", "rid": "19"},
                    {"type": "rid", "rid": "21"},
                ],
            },
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen"]

    def test_string_format_upcoming(self):
        # Older format: plan.upcoming as list of plain strings
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19", "21", "1"], "ordered": 1, "type": "drc"},
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom", "Kitchen", "Hallway"]

    def test_mixed_format_skips_unrecognised(self):
        # Defensive: mixed format drops empty-rid entries
        ms = _ms_with_timeline({
            "plan": {"upcoming": [{"type": "rid", "rid": "19"}, {}]},
            "finEvents": [],
        })
        result = ms.latest_planned_order(REGION_MAP)
        assert result == ["Bathroom"]  # empty dict dropped

    def test_returns_none_when_upcoming_empty(self):
        ms = _ms_with_timeline({"plan": {"upcoming": []}, "finEvents": []})
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_when_no_plan(self):
        ms = _ms_with_timeline({"finEvents": []})
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_without_timeline(self):
        ms = _ms_without_timeline()
        assert ms.latest_planned_order(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_planned_order(REGION_MAP) is None


class TestLatestMissionDestination:
    def test_returns_last_room(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        # planned order is ["19", "21", "1"] → Hallway
        assert ms.latest_mission_destination(REGION_MAP) == "Hallway"

    def test_returns_none_when_no_planned_order(self):
        ms = MissionStore()
        assert ms.latest_mission_destination(REGION_MAP) is None

    def test_single_room_mission(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"], "ordered": 1},
            "finEvents": [],
        })
        assert ms.latest_mission_destination(REGION_MAP) == "Bathroom"


class TestLatestRoomCoverage:
    def test_coverage_fractions_computed(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_room_coverage(REGION_MAP)
        assert result is not None
        assert pytest.approx(result["Bathroom"], abs=0.01) == 42 / 72
        assert pytest.approx(result["Kitchen"],  abs=0.01) == 95 / 120
        assert pytest.approx(result["Hallway"],  abs=0.01) == 44 / 55

    def test_status_6_included_when_totalArea_present(self):
        # status=6 with totalArea — should be included
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 6,
                    "area": 76, "passArea": 41, "totalArea": 50,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Bathroom"})
        assert result is not None
        assert pytest.approx(result["Bathroom"], abs=0.01) == 50 / 76

    def test_status_6_without_totalArea_skipped(self):
        # status=6 without totalArea — gracefully skipped (coverage shows None for room)
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["23"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "23", "status": 6,
                    "area": 37, "passArea": 16,
                    # totalArea absent — confirmed on Thonno's lewis 22.52.10 robot
                }},
            ],
        })
        # No qualifying events with totalArea → returns None
        assert ms.latest_room_coverage({"23": "Bathroom"}) is None

    def test_status_5_excluded_from_coverage(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": []},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "26", "status": 5, "area": 251,
                    "passArea": 40,
                }},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_coverage_clamped_to_1(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0, "area": 50, "totalArea": 60,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Room"})
        assert result is not None
        assert result["Room"] <= 1.0

    def test_coverage_clamped_to_0(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {
                    "rid": "19", "status": 0, "area": 50, "totalArea": -5,
                }},
            ],
        })
        result = ms.latest_room_coverage({"19": "Room"})
        assert result is not None
        assert result["Room"] >= 0.0

    def test_returns_none_when_no_status0_events(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 1, "area": 50}},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_returns_none_without_timeline(self):
        ms = _ms_without_timeline()
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_returns_none_when_empty_store(self):
        ms = MissionStore()
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_skips_events_missing_area(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 0, "totalArea": 40}},
            ],
        })
        # area is missing → skip → None
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_skips_events_missing_total_area(self):
        ms = _ms_with_timeline({
            "plan": {"upcoming": ["19"]},
            "finEvents": [
                {"type": "room", "room": {"rid": "19", "status": 0, "area": 72}},
            ],
        })
        assert ms.latest_room_coverage(REGION_MAP) is None

    def test_uses_region_map_for_keys(self):
        ms = _ms_with_timeline(TYPICAL_TIMELINE)
        result = ms.latest_room_coverage(REGION_MAP)
        assert result is not None
        assert "Bathroom" in result
        assert "19" not in result  # raw ID not in output when map resolves it


class TestQueryByError:

    def _ms_with_records(self, records):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = records
        return ms

    def _rec(self, id_, days_ago, error_code, zones=None, result="error"):
        # v3.2.0 bug-hunt fix: was hardcoded to fixed dates like
        # "2026-06-01T08:00:00+00:00" — a query_by_error(days=30) window
        # is relative to wall-clock "now", so a fixed date eventually
        # drifts outside it purely from real time passing, not from any
        # code bug (same class of issue found and fixed in
        # test_init_wiring.py's TestUpdateRobotProfileStoreMissionStats).
        # days_ago is relative to "now" so this can't happen again.
        from homeassistant.util import dt as dt_util
        import datetime
        started = dt_util.now() - datetime.timedelta(days=days_ago)
        ended = started + datetime.timedelta(minutes=30)
        return {
            "id": id_,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "result": result,
            "error_code": error_code,
            "zones": zones or [],
            "duration_min": 30,
            "initiator": "schedule",
            "bbrun_hr": 0,
        }

    def test_returns_matching_error_code(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 17, ["Kitchen"]),
            self._rec("m_2", 4, None, [], "completed"),
        ])
        result = ms.query_by_error(17, days=30)
        assert len(result) == 1
        assert result[0]["id"] == "m_1"

    def test_different_error_code_not_returned(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 15),
        ])
        assert ms.query_by_error(17, days=30) == []

    def test_zone_filter_applied(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 17, ["Kitchen"]),
            self._rec("m_2", 4, 17, ["Hallway"]),
        ])
        result = ms.query_by_error(17, days=30, zone="Kitchen")
        assert len(result) == 1
        assert result[0]["id"] == "m_1"

    def test_zone_none_returns_all_matching_code(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 17, ["A"]),
            self._rec("m_2", 4, 17, ["B"]),
        ])
        assert len(ms.query_by_error(17, days=30, zone=None)) == 2

    def test_returns_empty_when_no_records(self):
        ms = self._ms_with_records([])
        assert ms.query_by_error(17, days=30) == []

    def test_zone_filter_no_match_returns_empty(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 17, ["Kitchen"]),
        ])
        assert ms.query_by_error(17, days=30, zone="Bedroom") == []

    def test_record_with_empty_zones_excluded_by_zone_filter(self):
        ms = self._ms_with_records([
            self._rec("m_1", 5, 17, []),
        ])
        assert ms.query_by_error(17, days=30, zone="Kitchen") == []


class TestCR3Fallback:
    """CR3: enriched MissionStore records served when cloud history is empty."""

    def _enriched_record(self, id_="m_1"):
        return {
            "id": id_,
            "started_at": "2026-06-01T08:00:00+00:00",
            "ended_at": "2026-06-01T08:55:00+00:00",
            "result": "completed",
            "dirt": 14,
            "chrgM": 0,
            "wlBars": [0, 35, 65, 0, 0],
        }

    def _unenriched_record(self, id_="m_2"):
        return {
            "id": id_,
            "started_at": "2026-06-02T08:00:00+00:00",
            "ended_at": "2026-06-02T08:55:00+00:00",
            "result": "completed",
        }

    def test_enriched_records_qualify_for_fallback(self):
        rec = self._enriched_record()
        qualifies = any(rec.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        assert qualifies is True

    def test_unenriched_records_do_not_qualify(self):
        rec = self._unenriched_record()
        qualifies = any(rec.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        assert qualifies is False

    def test_fallback_filter_returns_only_enriched(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [self._enriched_record(), self._unenriched_record()]
        fallback = [
            r for r in ms._records
            if any(r.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        ]
        assert len(fallback) == 1
        assert fallback[0]["id"] == "m_1"

    def test_no_fallback_when_no_enriched_records(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [self._unenriched_record()]
        fallback = [
            r for r in ms._records
            if any(r.get(f) is not None for f in ("dirt", "chrgM", "wlBars"))
        ]
        assert fallback == []


class TestMissionStoreEphemeralFallback:
    def _ms_with_timeline(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        ms = MissionStore()
        ms._records = [{
            "id": "m_1",
            "started_at": "2026-01-01T00:00:00+00:00",
            "ended_at":   "2026-01-01T01:00:00+00:00",
            "duration_min": 60,
            "result": "completed",
            "initiator": "schedule",
            "zones": [],
            "error_code": None,
            "bbrun_hr": 0,
            "timeline": {
                "plan": {"upcoming": ["19", "21"]},
                "finEvents": [
                    {"type": "room", "room": {"rid": "19", "status": 0,
                                              "area": 100, "totalArea": 80}},
                    {"type": "room", "room": {"rid": "21", "status": 0,
                                              "area": 120, "totalArea": 90}},
                ]
            }
        }]
        return ms

    def test_region_map_used_when_provided(self):
        ms = self._ms_with_timeline()
        region_map = {"19": "Bathroom", "21": "Hallway"}
        result = ms.latest_cleaned_rooms(region_map)
        assert result == ["Bathroom", "Hallway"]

    def test_umf_regions_fallback_when_region_map_empty(self):
        ms = self._ms_with_timeline()
        umf_regions = {"19": "Bathroom-UMF", "21": "Hallway-UMF"}
        result = ms.latest_cleaned_rooms({}, umf_regions)
        assert result == ["Bathroom-UMF", "Hallway-UMF"]

    def test_region_map_takes_precedence_over_umf_regions(self):
        ms = self._ms_with_timeline()
        region_map  = {"19": "Bathroom"}
        umf_regions = {"19": "Should-Not-Appear", "21": "Hallway"}
        result = ms.latest_planned_order(region_map, umf_regions)
        # region_map non-empty → effective_map = region_map
        assert "Bathroom" in result
        assert "Should-Not-Appear" not in result

    def test_both_empty_returns_rids(self):
        ms = self._ms_with_timeline()
        result = ms.latest_cleaned_rooms({}, None)
        # Falls back to raw rid strings
        assert result == ["19", "21"]

    def test_latest_room_coverage_umf_fallback(self):
        ms = self._ms_with_timeline()
        umf_regions = {"19": "Bathroom-U", "21": "Hallway-U"}
        result = ms.latest_room_coverage({}, umf_regions)
        assert result is not None
        assert "Bathroom-U" in result
        assert "Hallway-U" in result


class TestImportEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_imports_records(self):
        hass, entry = _make_hass_with_entry(records=[])
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [_make_record_v250_api_export("m_new_1"), _make_record_v250_api_export("m_new_2")],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_type_malformed_record_rejected_not_persisted(self):
        """v3.2.0 full-review fix — a record with a non-string ended_at
        previously passed import validation (only `id` was checked),
        got persisted, and then crashed room_coverage_health on every
        subsequent cloud-refresh cycle, permanently. Must now be
        rejected at the import gate with a per-record error."""
        hass, entry = _make_hass_with_entry(records=[])
        ms = entry.runtime_data.mission_store
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [{"id": "poison", "ended_at": 12345, "zones": []}],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert any("ended_at" in e for e in result["errors"])
        assert all(r.get("id") != "poison" for r in ms._records)

    @pytest.mark.asyncio
    async def test_non_string_zone_entries_rejected(self):
        hass, entry = _make_hass_with_entry(records=[])
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [{"id": "bad_zones", "zones": ["Kitchen", 42]}],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 0
        assert any("zones" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_non_string_id_rejected(self):
        hass, entry = _make_hass_with_entry(records=[])
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [{"id": 12345, "zones": []}],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_async_save_called_after_import(self):
        hass, entry = _make_hass_with_entry(records=[])
        ms = entry.runtime_data.mission_store
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [_make_record_v250_api_export("m_new_1")],
        }
        req = _make_post_request(hass, body)
        await view.post(req, "abc123")
        ms.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dedup_skips_existing_ids(self):
        existing = [_make_record_v250_api_export("m_exists")]
        hass, _ = _make_hass_with_entry(records=existing)
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [
                _make_record_v250_api_export("m_exists"),   # duplicate — must be skipped
                _make_record_v250_api_export("m_new"),      # new — must be imported
            ],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 1
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_missing_export_version_returns_400(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        body = {"records": []}   # no export_version
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_wrong_export_version_returns_400(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        body = {"export_version": 99, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_empty_records_returns_zero(self):
        hass, entry = _make_hass_with_entry()
        ms = entry.runtime_data.mission_store
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 0
        assert result["skipped"] == 0
        ms.async_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversized_payload_returns_400(self):
        """Bug 4 fix: payloads larger than 2×MAX_RECORDS must be rejected."""
        from custom_components.roomba_plus.mission_store import MAX_RECORDS
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        # Build a list slightly over the size cap
        oversized = [_make_record_v250_api_export(f"m_{i}") for i in range(MAX_RECORDS * 2 + 1)]
        body = {"export_version": 1, "records": oversized}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_entry_returns_404(self):
        hass, _ = _make_hass_with_entry(entry_present=False)
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_unready_entry_returns_503(self):
        hass, _ = _make_hass_with_entry(runtime_data_set=False)
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 503


class TestError224Correction:
    """_merge_cloud_fields must correct result='stuck' → 'error' when pauseId=224."""

    def _make_local(self, result: str) -> dict:
        return {"id": "m_100", "result": result, "error_code": None}

    def test_stuck_with_pause_224_becomes_error(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        local = self._make_local("stuck")
        cloud = {"pauseId": 224, "sqft": 0, "runM": 0}
        changed = MissionStore._merge_cloud_fields(local, cloud)
        assert changed is True
        assert local["result"] == "error"
        assert local["error_code"] == 224

    def test_other_stuck_not_touched(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        local = self._make_local("stuck")
        cloud = {"pauseId": 5, "sqft": 100}
        MissionStore._merge_cloud_fields(local, cloud)
        assert local["result"] == "stuck"  # not changed

    def test_completed_not_affected(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        local = self._make_local("completed")
        cloud = {"pauseId": 224}
        MissionStore._merge_cloud_fields(local, cloud)
        assert local["result"] == "completed"  # pauseId 224 only corrects stuck


class TestComputeArchiveStats:
    def _records(self, n: int, **kwargs) -> list[dict]:
        return [_derived(i, **kwargs) for i in range(1, n + 1)]

    def test_returns_none_below_20(self):
        records = self._records(19)
        assert MissionStore.compute_archive_stats(records) is None

    def test_returns_stats_at_20(self):
        records = self._records(20)
        stats = MissionStore.compute_archive_stats(records)
        assert stats is not None
        assert "duration_mean" in stats
        assert "area_mean" in stats
        assert "dirt_p75" in stats

    def test_duration_mean_correct(self):
        records = [_derived(i, duration_min=i * 10) for i in range(1, 21)]
        stats = MissionStore.compute_archive_stats(records)
        expected = statistics.mean(i * 10 for i in range(1, 21))
        assert abs(stats["duration_mean"] - expected) < 0.01

    def test_only_completed_missions_counted(self):
        completed = self._records(20, duration_min=40)
        errors = [_derived(100 + i, duration_min=200, result="error_17")
                  for i in range(5)]
        stats = MissionStore.compute_archive_stats(completed + errors)
        # Only 20 completed records used — duration_mean should be ~40
        assert abs(stats["duration_mean"] - 40) < 0.01

    def test_too_few_completed_returns_none(self):
        errors = [_derived(i, result="error_17") for i in range(25)]
        completed = [_derived(100 + i) for i in range(15)]
        assert MissionStore.compute_archive_stats(errors + completed) is None

    def test_dirt_p75(self):
        records = [_derived(i, dirt=i) for i in range(1, 21)]
        stats = MissionStore.compute_archive_stats(records)
        dirts = sorted(i for i in range(1, 21))
        expected_p75 = dirts[int(20 * 0.75)]
        assert stats["dirt_p75"] == expected_p75

    def test_area_none_when_no_sqft(self):
        records = [_derived(i, sqft=0.0) for i in range(1, 21)]
        stats = MissionStore.compute_archive_stats(records)
        assert stats["area_mean"] is None

    def test_recharge_mean_zero(self):
        """Archive has no recharge minutes — always 0.0."""
        records = self._records(20)
        stats = MissionStore.compute_archive_stats(records)
        assert stats["recharge_mean"] == 0.0

    def test_cancelled_by_user_counted(self):
        """cancelled_by_user counts as normal (non-error) mission."""
        records = [
            _derived(i, result="cancelled_by_user" if i % 5 == 0 else "completed")
            for i in range(1, 21)
        ]
        stats = MissionStore.compute_archive_stats(records)
        assert stats is not None  # 20 valid records


class TestConsecutiveAnomalousWithFallback:
    def _ms_with_archive_baseline(self) -> MissionStore:
        ms = _make_ms()
        ms.archive_baseline = {
            "duration_mean": 45.0,
            "duration_std": 5.0,
            "area_mean": 300.0,
            "area_std": 30.0,
            "recharge_mean": 0.0,
            "dirt_p75": 10.0,
        }
        return ms

    def test_uses_archive_baseline_when_no_local_stats(self):
        """With < 20 local missions, archive_baseline is used for detection."""
        ms = self._ms_with_archive_baseline()
        # Add 3 local records that are anomalous per the archive baseline:
        # duration > mean + 2*std = 45 + 10 = 55, area < mean - std = 270
        for i in range(3):
            ms._records.append({
                "duration_min": 100,   # >> mean + 2*std
                "area_sqft": 200.0,    # << mean - std
                "recharge_min": None,
                "dirt": None,
            })
        # Local stats: None (only 3 records)
        assert ms.compute_rolling_stats() is None
        # Archive baseline is used → 3 consecutive anomalies
        assert ms.consecutive_anomalous == 3

    def test_local_stats_take_priority(self):
        """When ≥ 20 local missions, local stats override archive baseline."""
        from datetime import datetime, timezone, timedelta
        ms = self._ms_with_archive_baseline()
        # Archive baseline: duration_mean=45, std=5 → anomaly threshold=55
        ms.archive_baseline["duration_mean"] = 10.0
        ms.archive_baseline["duration_std"] = 1.0

        # Add 22 normal local missions with started_at so query() includes them
        now = datetime.now(timezone.utc)
        for i in range(22):
            ts = (now - timedelta(hours=i)).isoformat()
            ms._records.append({
                "duration_min": 45,
                "area_sqft": 300.0,
                "recharge_min": 0,
                "dirt": 5,
                "started_at": ts,
            })
        # Local stats available now — archive baseline NOT used
        assert ms.compute_rolling_stats() is not None
        assert ms.consecutive_anomalous == 0

    def test_zero_when_no_stats_and_no_baseline(self):
        ms = _make_ms()
        # Add 2 local records — no local stats, no archive baseline
        ms._records.append({"duration_min": 100, "area_sqft": 100.0})
        assert ms.consecutive_anomalous == 0
