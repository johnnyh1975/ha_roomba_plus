




class TestDirtCauseFinallyHasItsInput:
    """`_classify_dirt_cause` has held a real distinction since v3.2 and
    **could never be called**: it needs a dirt TREND, and this module
    produced only a per-record density.

    Half a feature rather than a disconnected one — the logic was
    written before its input existed. Found by listing private helpers
    whose name appears exactly once in the component, the same check
    that found three genuinely orphaned functions the same day.
    """

    def _records(self, dirt_series, sqft=100):
        return [
            {"dirt": d, "sqft": sqft, "startTime": 1786000000 - i * 86400}
            for i, d in enumerate(dirt_series)
        ]

    def _trend(self, dirt_series):
        from custom_components.roomba_plus.sensor_cloud import (
            _raw_dirt_density_trend,
        )

        return _raw_dirt_density_trend(self._records(dirt_series))

    def test_a_dirtier_recent_window_reads_rising(self):
        assert self._trend([9] * 5 + [5] * 10) == "rising"

    def test_a_cleaner_recent_window_reads_falling(self):
        assert self._trend([5] * 5 + [9] * 10) == "falling"

    def test_a_small_change_is_stable(self):
        """Same 10% threshold as the speed trend. Two windows
        disagreeing about the same history would be worse than either
        being slightly wrong, and the pair is compared to each other."""
        assert self._trend([10] * 5 + [10.5] * 10) == "stable"

    def test_too_few_records_is_unknown(self):
        assert self._trend([9, 5, 9]) == "unknown"

    def test_the_gap_filter_matches_the_speed_trend_exactly(self):
        """**Not a test of what the filter should do — a test that both
        trends do the same thing.**

        Walking newest-first, the filter skips the three records
        *following* a gap in list order, which are the three *older*
        ones. Whether that is the right end is a question about
        `_raw_cleaning_speed_trend`, which has shipped that way since
        v3.2 and is not being changed here.

        What matters for the classification is that the two windows
        agree: they are compared against each other, and a filter that
        excluded different records in each would make the comparison
        meaningless."""
        import inspect

        from custom_components.roomba_plus import sensor_cloud

        source = inspect.getsource(sensor_cloud)
        speed = source.index("def _raw_cleaning_speed_trend")
        dirt = source.index("def _raw_dirt_density_trend")
        for fragment in ("> 7:", "skip_remaining = 3", "skip_remaining -= 1"):
            assert fragment in source[speed:speed + 2500], fragment
            assert fragment in source[dirt:dirt + 2500], fragment


    def test_rising_dirt_with_falling_speed_reads_as_brush_wear(self):
        """Debris the brush is not picking up: the sensor re-fires on
        the same mess and the robot slows down carrying it."""
        from custom_components.roomba_plus.sensor_cloud import (
            _classify_dirt_cause,
        )

        assert _classify_dirt_cause("rising", "declining") == "brush_wear"

    def test_rising_dirt_with_steady_speed_reads_as_a_dirty_floor(self):
        from custom_components.roomba_plus.sensor_cloud import (
            _classify_dirt_cause,
        )

        assert _classify_dirt_cause("rising", "stable") == "floor_dirty"

    def test_the_sensor_surfaces_both(self):
        """The wiring, not the parts — which is the half that was
        missing three times over on the same day."""
        import inspect

        from custom_components.roomba_plus import sensor_cloud

        source = inspect.getsource(sensor_cloud)

        assert 'attrs["dirt_trend"] = dirt_trend' in source
        assert 'attrs["dirt_cause"] = _classify_dirt_cause(' in source


# ============================================================================
# CLOUD MISSION HISTORY -- the sensors and the field readers behind them.
#
# Moved here from test_sensors.py (August 2026). Nine classes, 46 tests.
#
# The `Mh*` ones are the reason a search for imports missed this block
# the first time: they test `_mh_sqft_to_m2` and friends, which live in
# sensor_cloud.py, but reached them through the sensor.py facade. Only
# searching for the *names defined in this module* found them.
#
# The four `_make_history*` helpers are COPIED, not moved:
# TestSensorSetupEntryCloud and TestNextFromScheduleV1 stay behind and
# still use them.
# ============================================================================


from unittest.mock import MagicMock

import pytest

# IMPORTED FROM sensor_cloud DIRECTLY, not through the sensor.py facade
# as they were in test_sensors.py. All six are defined in this module;
# the facade only re-exported them, which is exactly why a search for
# "which tests import sensor_cloud" came back empty.
from unittest.mock import PropertyMock

from custom_components.roomba_plus.sensor_cloud import (
    CLOUD_HISTORY_SENSORS,
    CloudRawSensor,
    CloudRawSensorDescription,
    RoombaCleaningAnalytics30dSensor,
    RoombaCleaningPerformanceSensor,
    CloudHistorySensor,
    _mh_sqft_to_m2,
    _mh_total_minutes,
    _mh_total_missions,
)


# Copied from test_sensors.py with the block above.
def _make_config_entry(has_cloud: bool = False, favorites=None, pmaps=None):
    """Return a minimal mock config entry."""
    entry = MagicMock()
    entry.unique_id = "test_blid"
    entry.options = {}
    entry.data = {"blid": "test_blid"}

    cc = MagicMock()
    cc.data = {
        "pmaps": pmaps or [],
        "favorites": favorites or [],
        "mission_history": {},
    }
    cc.active_pmap_id = (pmaps[0].get("active_pmapv_details", {}).get("active_pmapv", {}).get("pmap_id") if pmaps else None)
    runtime = MagicMock()
    runtime.has_cloud = has_cloud
    runtime.cloud_coordinator = cc if has_cloud else None
    entry.runtime_data = runtime
    return entry


def _make_roomba():
    r = MagicMock()
    r.master_state = {"state": {"reported": {}}}
    return r


# The six CloudSmartZoneSelect classes that stood here moved to
# test_select.py (August 2026) -- they tested `select.py` from a file
# named for sensors. `_make_config_entry` and `_make_roomba` above stay:
# tests in this file still use them.


# `_fav_button` and TestFavoriteButton moved to test_button.py
# (August 2026) -- that file's header claimed button.py had zero
# coverage, which was true only because its tests were filed here.



def _make_history(sqft: int = 0, hr: int = 0, mn: int = 0, n_mssn: int = 0) -> dict:
    """Build a fake coordinator.data["mission_history"] dict for CloudHistorySensor tests."""
    return {
        "runtimeStats": {"sqft": sqft, "hr": hr, "min": mn},
        "bbmssn": {"nMssn": n_mssn},
    }


def _make_history_list(**kwargs) -> list:
    """Wrap _make_history in a list — simulates the raw API response before normalisation."""
    return [_make_history(**kwargs)]


def _make_history_sensor(key: str, history: dict | None = None, *, success: bool = True):
    """Return a CloudHistorySensor instance wired to a fake coordinator."""
    desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == key)
    coordinator = MagicMock()
    coordinator.last_update_success = success
    coordinator.data = {"mission_history": history or {}} if success else None
    entry = _make_config_entry(has_cloud=True)
    entry.runtime_data.cloud_coordinator = coordinator
    roomba = _make_roomba()
    sensor = CloudHistorySensor(roomba, "test_blid", coordinator, desc)
    sensor._config_entry = entry
    return sensor


def _make_history_coordinator(history: dict):
    """Return a fake coordinator whose data contains mission_history."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {"mission_history": history, "pmaps": [], "mission_history_raw": []}
    coordinator.raw_records = []
    return coordinator


# ── 600-series cloud sensor creation (Q1 verification) ───────────────────────




class TestMissionHistoryListResponse:
    """The /missionhistory API returns a list, not a dict.

    The coordinator must normalize this before storing. The value functions
    must never receive a list — that was the crash in the bug report.
    """

    def test_list_response_does_not_crash_sqft(self):
        """Passing a list to _mh_sqft_to_m2 must not raise AttributeError."""
        history_list = _make_history_list(sqft=10764)
        # Before the fix this would crash: 'list' has no attribute 'get'
        # After the fix the coordinator extracts [0] before passing to value_fn.
        # Test the value_fn directly to confirm it still handles a dict correctly.
        history_dict = history_list[0]
        assert _mh_sqft_to_m2(history_dict) == pytest.approx(1000.0, abs=1)

    def test_list_response_does_not_crash_time(self):
        history_list = _make_history_list(hr=2, mn=30)
        assert _mh_total_minutes(history_list[0]) == 150

    def test_list_response_does_not_crash_missions(self):
        history_list = _make_history_list(n_mssn=99)
        assert _mh_total_missions(history_list[0]) == 99

    def test_coordinator_normalizes_list_to_dict(self):
        """Coordinator must store history as a dict, never a list."""
        cc = _make_history_coordinator(_make_history(sqft=500, hr=5, mn=0, n_mssn=20))
        history = cc.data["mission_history"]
        assert isinstance(history, dict), (
            f"mission_history must be a dict, got {type(history).__name__}"
        )

    def test_value_fn_receives_dict_not_list(self):
        """Simulate what native_value does — must not receive a list."""
        cc = _make_history_coordinator(_make_history(sqft=500))
        history = cc.data.get("mission_history", {})
        # This is the exact call that was crashing:
        result = _mh_sqft_to_m2(history)
        assert result is not None
        assert isinstance(result, float)

    def test_empty_list_produces_empty_dict(self):
        """Empty list from API must produce empty dict, not IndexError."""
        # The `cc` fake that stood here was never used: the test calls the
        # three readers directly with `{}`. Removed rather than kept for
        # shape -- a fixture nobody reads is a claim nobody checks.
        #
        # The coordinator normalizes [] → {}; the readers return None.
        assert _mh_sqft_to_m2({}) is None
        assert _mh_total_minutes({}) is None
        assert _mh_total_missions({}) is None


# ── Value functions — unit tests ───────────────────────────────────────────────

class TestMhSqftToM2:
    def test_converts_sqft_to_m2(self):
        h = _make_history(sqft=10764)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(1000.0, abs=1)

    def test_rounds_to_one_decimal(self):
        h = _make_history(sqft=100)
        result = _mh_sqft_to_m2(h)
        assert result == round(100 / 10.764, 1)

    def test_none_when_sqft_missing(self):
        assert _mh_sqft_to_m2({}) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_sqft_to_m2({"bbmssn": {"nMssn": 5}}) is None

    def test_none_when_runtimestats_is_none(self):
        assert _mh_sqft_to_m2({"runtimeStats": None}) is None

    def test_zero_sqft(self):
        h = _make_history(sqft=0)
        assert _mh_sqft_to_m2(h) == 0.0

    def test_large_value(self):
        h = _make_history(sqft=50000)
        result = _mh_sqft_to_m2(h)
        assert result == pytest.approx(4645.2, abs=1)


class TestMhTotalMinutes:
    def test_converts_hr_min_to_minutes(self):
        h = _make_history(hr=2, mn=30)
        assert _mh_total_minutes(h) == 150

    def test_zero_hours(self):
        h = _make_history(hr=0, mn=45)
        assert _mh_total_minutes(h) == 45

    def test_zero_minutes(self):
        h = _make_history(hr=3, mn=0)
        assert _mh_total_minutes(h) == 180

    def test_none_when_hr_missing(self):
        h = {"runtimeStats": {"min": 30}}
        assert _mh_total_minutes(h) is None

    def test_none_when_min_missing(self):
        h = {"runtimeStats": {"hr": 2}}
        assert _mh_total_minutes(h) is None

    def test_none_when_runtimestats_missing(self):
        assert _mh_total_minutes({}) is None

    def test_none_when_runtimestats_none(self):
        assert _mh_total_minutes({"runtimeStats": None}) is None

    def test_large_values(self):
        h = _make_history(hr=100, mn=59)
        assert _mh_total_minutes(h) == 6059


class TestMhTotalMissions:
    def test_returns_nmssn(self):
        h = _make_history(n_mssn=987)
        assert _mh_total_missions(h) == 987

    def test_none_when_bbmssn_missing(self):
        assert _mh_total_missions({}) is None

    def test_none_when_nmssn_missing(self):
        assert _mh_total_missions({"bbmssn": {}}) is None

    def test_none_when_bbmssn_none(self):
        assert _mh_total_missions({"bbmssn": None}) is None

    def test_zero_missions(self):
        h = _make_history(n_mssn=0)
        assert _mh_total_missions(h) == 0


# ── CLOUD_HISTORY_SENSORS descriptions ────────────────────────────────────────

class TestCloudHistorySensorsDescriptions:
    """Verify the three CLOUD_HISTORY_SENSORS descriptions match current code.

    Keys as of v2.1.x: recent_area_30d, recent_time_30d, lifetime_missions.
    recent_area_30d and recent_time_30d deliberately have no translation_key —
    name= alone locks the entity_id slug to English regardless of HA locale.
    """

    def test_three_sensors_defined(self):
        assert len(CLOUD_HISTORY_SENSORS) == 3

    def test_keys(self):
        keys = {d.key for d in CLOUD_HISTORY_SENSORS}
        assert keys == {"recent_area_30d", "recent_time_30d", "lifetime_missions"}

    def test_lifetime_missions_has_translation_key(self):
        """lifetime_missions uses translation_key for localised friendly name."""
        missions = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_missions")
        assert missions.translation_key == "lifetime_missions"

    def test_area_and_time_have_translation_key(self):
        """recent_area_30d and recent_time_30d must have translation_key set.

        Step 23 (v2.2.0 card audit fix): translation_key locks the entity_id
        slug to the key string, independent of locale. Without it, fresh installs
        on non-English HA produce language-specific slugs
        (e.g. sensor.*_gereinigte_flache_30_t on DE). The key, not the translated
        name string, is used as the slug when translation_key is present.
        """
        for key in ("recent_area_30d", "recent_time_30d"):
            desc = next(d for d in CLOUD_HISTORY_SENSORS if d.key == key)
            assert desc.translation_key == key, (
                f"{key}: translation_key must equal key to lock entity_id slug"
            )

    def test_area_unit_m2(self):
        area = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_area_30d")
        assert area.native_unit_of_measurement == "m²"

    def test_time_unit_minutes(self):
        from homeassistant.const import UnitOfTime
        time = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "recent_time_30d")
        assert time.native_unit_of_measurement == UnitOfTime.MINUTES

    def test_missions_unit(self):
        missions = next(d for d in CLOUD_HISTORY_SENSORS if d.key == "lifetime_missions")
        assert missions.native_unit_of_measurement == "missions"

    def test_all_diagnostic(self):
        from homeassistant.const import EntityCategory
        for d in CLOUD_HISTORY_SENSORS:
            assert d.entity_category == EntityCategory.DIAGNOSTIC


# ── CloudHistorySensor entity ─────────────────────────────────────────────────

class TestCloudHistorySensorNativeValue:
    def test_recent_area_value(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=10764))
        assert sensor.native_value == pytest.approx(1000.0, abs=1)

    def test_recent_time_value(self):
        sensor = _make_history_sensor("recent_time_30d", _make_history(hr=1, mn=30))
        assert sensor.native_value == 90

    def test_lifetime_missions_value(self):
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=42))
        assert sensor.native_value == 42

    def test_none_when_no_history_data(self):
        sensor = _make_history_sensor("recent_area_30d", {})
        assert sensor.native_value is None

    def test_none_when_coordinator_has_no_data(self):
        sensor = _make_history_sensor("recent_area_30d", success=False)
        assert sensor.native_value is None


class TestCloudHistorySensorAvailability:
    def test_available_when_coordinator_ok(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        assert sensor.available is True

    def test_unavailable_when_last_update_failed(self):
        sensor = _make_history_sensor("recent_area_30d", success=False)
        assert sensor.available is False

    def test_unavailable_when_data_none(self):
        sensor = _make_history_sensor("recent_area_30d")
        sensor._coordinator.data = None
        assert sensor.available is False


class TestCloudHistorySensorNoMqttUpdate:
    def test_new_state_filter_always_false(self):
        """Cloud sensor must not react to MQTT messages."""
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=10))
        assert sensor.new_state_filter({"bbmssn": {"nMssn": 99}}) is False
        assert sensor.new_state_filter({}) is False


class TestCloudHistorySensorUniqueId:
    def test_unique_id_contains_key(self):
        sensor = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        assert "recent_area_30d" in sensor._attr_unique_id

    def test_unique_id_contains_blid(self):
        sensor = _make_history_sensor("lifetime_missions", _make_history(n_mssn=5))
        assert "test_blid" in sensor._attr_unique_id

    def test_unique_ids_distinct(self):
        s1 = _make_history_sensor("recent_area_30d", _make_history(sqft=100))
        s2 = _make_history_sensor("recent_time_30d", _make_history(hr=1, mn=0))
        assert s1._attr_unique_id != s2._attr_unique_id


# ── async_setup_entry integration ─────────────────────────────────────────────


# ============================================================================
# LAST-ERROR SENSORS from the cloud history.
#
# Moved here from test_sensors.py (August 2026). All three test
# `_raw_cloud_last_error_{code,time,attrs}`, which are defined in this
# module.
#
# `_rec` is COPIED, not moved: thirteen tests elsewhere in
# test_sensors.py still build records with it.
# ============================================================================


def _rec(done="done", done_raw="done", pause_id=0, chrgs=0, evacs=0,
         dirt=0, timestamp=1700000000, classified=None):
    """Build a minimal raw record dict with classified_result pre-computed."""
    r = {
        "done":      done,
        "done_raw":  done_raw,
        "pauseId":   pause_id,
        "chrgs":     chrgs,
        "evacs":     evacs,
        "dirt":      dirt,
        "timestamp": timestamp,
    }
    if classified is None:
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        classified = classify_mission_result(r)
    r["classified_result"] = classified
    return r



class TestCloudLastErrorCode:

    def test_returns_pause_id_from_first_error(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="done"),                          # completed — skip
            _rec(done="stuck", pause_id=17),            # error_17 — match
            _rec(done="stuck", pause_id=18),            # older error — not used
        ]
        assert _raw_cloud_last_error_code(records) == 17

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_code(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        assert _raw_cloud_last_error_code([]) is None

    def test_stuck_with_pause_id_zero_returns_none(self):
        """stuck + pauseId=0 → classified as 'stuck', no specific code."""
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=0)]
        # classified_result = "stuck", pause_id=0 → None
        assert _raw_cloud_last_error_code(records) is None

    def test_error_224_smart_map(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [_rec(done="stuck", pause_id=224)]
        assert _raw_cloud_last_error_code(records) == 224

    def test_skips_cancelled_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_code
        records = [
            _rec(done="cncl", done_raw="usrEnd"),       # cancelled_by_user — skip
            _rec(done="cncl"),                           # cancelled — skip
            _rec(done="stuck", pause_id=6),             # error_6 — match
        ]
        assert _raw_cloud_last_error_code(records) == 6


class TestCloudLastErrorTime:

    def test_returns_datetime_from_timestamp(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="stuck", pause_id=17, timestamp=1700000000)]
        result = _raw_cloud_last_error_time(records)
        assert result is not None
        assert isinstance(result, datetime.datetime)
        assert result.year == 2023
        assert result.tzinfo == datetime.timezone.utc

    def test_none_when_no_failed_missions(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        records = [_rec(done="done", timestamp=1700000000)] * 3
        assert _raw_cloud_last_error_time(records) is None

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        assert _raw_cloud_last_error_time([]) is None

    def test_uses_most_recent_error(self):
        import datetime
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_time
        # Records newest-first — first error timestamp wins
        records = [
            _rec(done="stuck", pause_id=17, timestamp=1700010000),
            _rec(done="stuck", pause_id=18, timestamp=1700000000),
        ]
        result = _raw_cloud_last_error_time(records)
        expected = datetime.datetime.fromtimestamp(1700010000, tz=datetime.timezone.utc)
        assert result == expected


class TestCloudLastErrorAttrs:

    def test_returns_catalogue_fields(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=17)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs["error_code"] == 17
        assert attrs["source"] == "cloud_pauseId"
        assert "label" in attrs
        assert "description" in attrs
        assert "action" in attrs

    def test_empty_when_no_errors(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="done")] * 3
        assert _raw_cloud_last_error_attrs(records) == {}

    def test_error_code_none_for_stuck_no_pause_id(self):
        from custom_components.roomba_plus.sensor import _raw_cloud_last_error_attrs
        records = [_rec(done="stuck", pause_id=0)]
        attrs = _raw_cloud_last_error_attrs(records)
        assert attrs.get("error_code") is None


# ============================================================================
# CLOUD RAW SENSOR and the cleaning-performance sensors.
#
# Moved here from test_sensors.py (August 2026). They test
# `CloudRawSensor`, `RoombaCleaningPerformanceSensor` and
# `RoombaCleaningAnalytics`, all defined in this module.
#
# Two classes that sat between them in the old file -- CompletionRate
# and LastMissionTeamId -- did NOT come: they test helpers in
# sensor_helpers.py and sensor_core.py, and moving a block wholesale
# would have filed them under the wrong module.
#
# `_make_sensor` is COPIED: five tests elsewhere still use it.
# ============================================================================


def _make_entry(mission_store=None, cloud_coordinator=None, umf_aligner=None):
    entry = MagicMock()
    rd = MagicMock()
    rd.has_cloud = cloud_coordinator is not None
    rd.cloud_coordinator = cloud_coordinator
    rd.umf_aligner = umf_aligner
    rd.mission_store = mission_store
    rd.robot_profile_store = None
    entry.runtime_data = rd
    return entry


def _make_coordinator(records=None, data=None):
    cc = MagicMock()
    cc.raw_records = records or []
    cc.data = data or {}
    cc.last_update_success = True
    return cc




def _make_sensor_v270_consolidated_sensors(cls, records=None, data=None, mission_store=None):
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    cc = _make_coordinator(records=records, data=data)
    entry = _make_entry(mission_store=mission_store)
    sensor = cls.__new__(cls)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._coordinator = cc
    sensor._config_entry = entry
    sensor._attr_unique_id = f"test_{cls.entity_description.key}"
    return sensor




def _make_sensor(
    has_cloud: bool = True,
    last_update_success: bool = True,
    coordinator_data: dict | None = None,
) -> CloudRawSensor:
    """Build a minimal CloudRawSensor with mocked internals."""
    roomba = MagicMock()
    blid = "test_blid"

    coordinator = MagicMock()
    coordinator.last_update_success = last_update_success
    coordinator.data = coordinator_data if coordinator_data is not None else {"pmaps": []}
    coordinator.raw_records = []

    config_entry = MagicMock()
    runtime_data = MagicMock()
    # has_cloud is a property — set it on the mock
    type(runtime_data).has_cloud = PropertyMock(return_value=has_cloud)
    config_entry.runtime_data = runtime_data

    description = CloudRawSensorDescription(
        key="recent_dirt_events",
        translation_key="recent_dirt_events",
        name="Dirt events",
        value_fn=lambda records: None,
    )

    sensor = CloudRawSensor(roomba, blid, coordinator, description, config_entry)
    return sensor



class TestCloudRawSensorAvailable:
    def test_unavailable_when_has_cloud_false(self):
        """Sensor must be unavailable when cloud coordinator is not configured."""
        sensor = _make_sensor(has_cloud=False, last_update_success=True)
        assert sensor.available is False

    def test_available_when_cloud_active_and_success(self):
        """Sensor must be available when cloud is configured and last update succeeded."""
        sensor = _make_sensor(
            has_cloud=True,
            last_update_success=True,
            coordinator_data={"pmaps": []},
        )
        assert sensor.available is True

    def test_unavailable_when_last_update_failed(self):
        """Sensor must be unavailable when last coordinator update failed."""
        sensor = _make_sensor(
            has_cloud=True,
            last_update_success=False,
            coordinator_data={"pmaps": []},
        )
        assert sensor.available is False

    def test_unavailable_when_coordinator_data_none(self):
        """Sensor must be unavailable when coordinator has not yet fetched data."""
        # Pass coordinator_data=None but we need to set it explicitly on the mock
        sensor = _make_sensor(has_cloud=True, last_update_success=True)
        sensor._coordinator.data = None
        assert sensor.available is False



class TestCleaningPerformanceSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=[])
        assert s.native_value is None

    def test_returns_completion_rate_with_records(self):
        records = [
            {"done": "done", "sqft": 300, "runM": 40},
            {"done": "done", "sqft": 280, "runM": 38},
            {"done": "hmPostMsn"},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=records)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        assert 0 <= val <= 100

    def test_attributes_include_trend(self):
        # Need ≥6 records for trend to be non-unknown
        records = [
            {"done": "done", "sqft": 300, "runM": 40, "startTime": 1700000000 - i * 86400}
            for i in range(10)
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningPerformanceSensor, records=records)
        attrs = s.extra_state_attributes
        # trend key should be present
        assert "trend" in attrs
        assert attrs["trend"] in ("improving", "stable", "declining", "unknown")

    # test_f6a_check_not_rescheduled_when_trend_unchanged removed in v3.5.0:
    # the performance_degradation Repair Issue it guarded was deleted (the
    # cleaning_speed_trend sensor already exposes this signal). The sensor no
    # longer schedules any repair-check side-effect from attribute reads.


class TestCleaningAnalytics30dSensor:

    def test_returns_none_without_runtime_stats(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data={})
        assert s.native_value is None

    def test_returns_area_m2_from_runtime_stats(self):
        data = {"runtimeStats": {"sqft": 10764, "hr": 5, "min": 30}}
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data=data)
        val = s.native_value
        assert val is not None
        assert isinstance(val, float)
        # 10764 sqft × 0.09290304 ≈ 1000.5 m²
        assert 990 < val < 1010

    def test_attributes_include_time_h(self):
        data = {"runtimeStats": {"sqft": 5000, "hr": 3, "min": 0}}
        s = _make_sensor_v270_consolidated_sensors(RoombaCleaningAnalytics30dSensor, data=data)
        attrs = s.extra_state_attributes
        assert "time_h" in attrs
        assert attrs["time_h"] == 3.0


# ============================================================================
# WI-FI SENSOR ENTITIES.
#
# Moved here from test_sensors.py (August 2026): `RoombaWifiHealthSensor`,
# `RoombaWifiLastChannelSensor`, `RoombaWifiChannelStabilitySensor` and
# `_channel_to_band` are defined in this module.
#
# The raw readers they call -- `_raw_wifi_floor`, `_raw_wifi_quality_pct`
# -- live in sensor_helpers.py, and their own tests went to
# test_sensor_helpers.py. Two modules, two test files, split along the
# same line as the source.
#
# `_make_archive` is COPIED: fourteen tests elsewhere still use it.
# ============================================================================


from datetime import UTC, timedelta  # noqa: E402
from datetime import datetime as datetime_v280_bat_arch  # noqa: E402

from custom_components.roomba_plus.mission_archive import MissionArchive  # noqa: E402
from custom_components.roomba_plus.sensor_cloud import (  # noqa: E402
    RoombaWifiChannelStabilitySensor,
    RoombaWifiHealthSensor,
    RoombaWifiLastChannelSensor,
    _channel_to_band,
)
from custom_components.roomba_plus.sensor_helpers import (  # noqa: E402
    _raw_wifi_floor,
    _raw_wifi_quality_pct,
)


def _make_sensor_v280_bat_arch(cls, archive: MissionArchive | None, available: bool = True):
    """Create a sensor instance with mocked dependencies."""
    sensor = object.__new__(cls)
    # IRobotEntity needs _blid and _roomba; robot_unique_id = f"roomba_plus_{_blid}"
    sensor._blid = "testblid"
    sensor._roomba = MagicMock()
    sensor._attr_unique_id = f"roomba_plus_testblid_{cls.entity_description.key}"

    config_entry = MagicMock()
    config_entry.runtime_data.mission_archive = archive
    config_entry.runtime_data.has_cloud = available

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {}

    sensor._config_entry = config_entry
    sensor._coordinator = coordinator
    return sensor




def _derived(
    n_mssn: int,
    wifi_channel: int | None = 6,
    recharge_count: int = 0,
    days_ago: int = 1,
) -> dict:
    ts = (datetime_v280_bat_arch.now(UTC) - timedelta(days=days_ago)).isoformat()
    return {
        "nMssn": n_mssn,
        "wifi_channel": wifi_channel,
        "recharge_count": recharge_count,
        "result": "completed",
        "sqft": 300.0,
        "duration_min": 45,
        "dirt": 5,
        "start_ts": ts,
    }





def _make_archive(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    arc = MissionArchive()
    for rec in records:
        arc._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            arc._archived_nmssns.add(int(n))
    arc._initial_load_done = initial_load_done
    return arc



class TestWifiQualityPct:
    """v2.9.0 — replaces _raw_wifi_floor as RoombaWifiHealthSensor's primary
    state. Weighted mean bucket index per mission (full histogram
    distribution, not just whether the weakest bucket was ever touched),
    averaged across records, scaled 0.0-4.0 to a genuine 0-100% percentage.
    """

    def test_all_strongest_bucket_is_100_percent(self):
        # Every reading in bucket 4 (strongest) → weighted mean = 4.0 → 100%
        records = [{"wlBars": [0, 0, 0, 0, 100]}]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_all_weakest_bucket_is_0_percent(self):
        # Every reading in bucket 0 (weakest) → weighted mean = 0.0 → 0%
        records = [{"wlBars": [100, 0, 0, 0, 0]}]
        assert _raw_wifi_quality_pct(records) == 0.0

    def test_middle_bucket_is_50_percent(self):
        # All in bucket 2 (middle of 0-4) → weighted mean = 2.0 → 50%
        records = [{"wlBars": [0, 0, 100, 0, 0]}]
        assert _raw_wifi_quality_pct(records) == 50.0

    def test_single_brief_dip_does_not_collapse_to_0_percent(self):
        """The exact bug this fix addresses: a single brief dip into the
        weakest bucket during an otherwise excellent connection must NOT
        read as 0% — _raw_wifi_floor() would have returned 0 (bucket index)
        here, which the old code mislabelled as a percentage."""
        # Mostly strong signal (bucket 4), one weak reading (bucket 0).
        records = [{"wlBars": [1, 0, 0, 0, 99]}]
        val = _raw_wifi_quality_pct(records)
        assert val is not None and val > 90.0, (
            "A single brief weak-signal blip must not collapse the "
            "percentage to near-zero — the weighted mean correctly "
            "reflects that the connection was excellent almost the "
            "entire time"
        )

    def test_averages_across_multiple_missions(self):
        records = [
            {"wlBars": [0, 0, 0, 0, 100]},  # mission 1: 100%
            {"wlBars": [100, 0, 0, 0, 0]},  # mission 2: 0%
        ]
        # Average of per-mission weighted means: (4.0 + 0.0) / 2 = 2.0 -> 50%
        assert _raw_wifi_quality_pct(records) == 50.0

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_quality_pct([]) is None

    def test_returns_none_when_no_valid_histograms(self):
        records = [{"wlBars": None}, {"wlBars": [70, 60, 80]}]  # invalid shapes
        assert _raw_wifi_quality_pct(records) is None

    def test_skips_non_5element_histograms_but_uses_valid_ones(self):
        records = [
            {"wlBars": [70, 60, 80]},          # invalid — skipped
            {"wlBars": [0, 0, 0, 0, 100]},      # valid — 100%
        ]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_skips_all_zero_histogram(self):
        """A histogram present but summing to zero (no readings at all)
        must be skipped, not treated as a 0% mission."""
        records = [
            {"wlBars": [0, 0, 0, 0, 0]},        # no data — skipped
            {"wlBars": [0, 0, 0, 0, 100]},      # valid — 100%
        ]
        assert _raw_wifi_quality_pct(records) == 100.0

    def test_result_is_rounded_to_1_decimal(self):
        records = [{"wlBars": [10, 20, 30, 25, 15]}]
        result = _raw_wifi_quality_pct(records)
        assert result == round(result, 1)

    def test_single_record_with_one_mission_minimum(self):
        """Unlike stability (needs >=3 records), a single mission's
        quality estimate is still meaningful and must not return None."""
        records = [{"wlBars": [0, 0, 0, 0, 100]}]
        assert _raw_wifi_quality_pct(records) is not None


class TestWifiHealthSensorUsesQualityPct:
    """RoombaWifiHealthSensor.native_value must use the new weighted-average
    percentage, with the old floor-based diagnostic moved to an attribute."""

    def test_native_value_uses_quality_pct_not_floor(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        # A single brief dip — floor would be 0, quality_pct should be high.
        coordinator.raw_records = [{"wlBars": [1, 0, 0, 0, 99]}]

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        val = sensor.native_value
        assert val is not None and val > 90.0, (
            "native_value must use the weighted-average quality percentage, "
            "not the raw worst-bucket-touched floor value"
        )

    def test_weakest_bucket_observed_attribute_present(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        coordinator.raw_records = [{"wlBars": [1, 0, 0, 0, 99]}]

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        attrs = sensor.extra_state_attributes
        assert attrs.get("weakest_bucket_observed") == 0, (
            "The original floor diagnostic must still be available as an "
            "attribute, just not as the misleading primary percentage"
        )

    def test_stability_attribute_still_present(self):
        from custom_components.roomba_plus.sensor import RoombaWifiHealthSensor

        coordinator = MagicMock()
        coordinator.raw_records = [{"wlBars": [0, 0, 0, 100, 0]}] * 3

        sensor = RoombaWifiHealthSensor.__new__(RoombaWifiHealthSensor)
        sensor._coordinator = coordinator

        attrs = sensor.extra_state_attributes
        assert "stability_pct" in attrs



class TestWifiHealthSensor:

    def test_returns_none_without_records(self):
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=[])
        assert s.native_value is None

    def test_returns_floor_pct_with_wl_bars(self):
        # wlBars histogram: index 0=weakest, 4=strongest
        records = [
            {"wlBars": [0, 10, 60, 30, 0]},
            {"wlBars": [0, 5,  70, 25, 0]},
        ]
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=records)
        val = s.native_value
        # Should return something (floor signal % computation)
        # If records lack valid wlBars, returns None — accept either
        # With valid data it should return a numeric value
        if val is not None:
            assert isinstance(val, (int, float))

    def test_attributes_include_stability(self):
        records = [{"wlBars": [0, 0, 50, 50, 0]}, {"wlBars": [0, 0, 60, 40, 0]}]
        s = _make_sensor_v270_consolidated_sensors(RoombaWifiHealthSensor, records=records)
        attrs = s.extra_state_attributes
        # stability_pct present when records have wlBars
        # (may be absent if wlBars computation returns None)
        assert isinstance(attrs, dict)



class TestChannelToBand:
    def test_ch1_is_24ghz(self):
        assert _channel_to_band(1) == "2.4 GHz"

    def test_ch6_is_24ghz(self):
        assert _channel_to_band(6) == "2.4 GHz"

    def test_ch13_is_24ghz(self):
        assert _channel_to_band(13) == "2.4 GHz"

    def test_ch36_is_5ghz(self):
        assert _channel_to_band(36) == "5 GHz"

    def test_ch149_is_5ghz(self):
        assert _channel_to_band(149) == "5 GHz"

    def test_none_returns_none(self):
        assert _channel_to_band(None) is None


class TestWifiLastChannelSensor:
    def test_returns_latest_channel(self):
        archive = _make_archive([
            _derived(1, wifi_channel=6),
            _derived(2, wifi_channel=36),   # newer
        ])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        # Archive is newest-first: derived[0] = nMssn=2, channel=36
        assert sensor.native_value == 36

    def test_returns_none_when_no_channel(self):
        archive = _make_archive([_derived(1, wifi_channel=None)] * 5)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        assert sensor.native_value is None

    def test_returns_none_when_archive_none(self):
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, None)
        assert sensor.native_value is None

    def test_band_attribute_24ghz(self):
        archive = _make_archive([_derived(i, wifi_channel=6) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs.get("band") == "2.4 GHz"

    def test_band_attribute_5ghz(self):
        archive = _make_archive([_derived(i, wifi_channel=36) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs.get("band") == "5 GHz"

    def test_unavailable_below_5_records(self):
        archive = _make_archive([_derived(1, wifi_channel=6)])
        assert archive.record_count == 1
        sensor = _make_sensor_v280_bat_arch(RoombaWifiLastChannelSensor, archive)
        # _ArchiveSensor.available requires record_count >= 5
        assert sensor._archive.record_count < 5


class TestWifiChannelStabilitySensor:
    def test_100pct_when_all_same(self):
        archive = _make_archive([_derived(i, wifi_channel=6) for i in range(1, 11)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value == 100.0

    def test_partial_stability(self):
        # 8 on ch6, 2 on ch36 → 80%
        records = (
            [_derived(i, wifi_channel=6) for i in range(1, 9)] +
            [_derived(i, wifi_channel=36) for i in range(9, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value == 80.0

    def test_returns_none_when_no_channels(self):
        archive = _make_archive([_derived(i, wifi_channel=None) for i in range(1, 6)])
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        assert sensor.native_value is None

    def test_attributes_dominant_channel(self):
        records = [_derived(i, wifi_channel=6) for i in range(1, 9)] + \
                  [_derived(i, wifi_channel=36) for i in range(9, 11)]
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaWifiChannelStabilitySensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs["dominant_channel"] == 6
        assert attrs["dominant_channel_band"] == "2.4 GHz"
        assert attrs["sample_count"] == 10


# ============================================================================
# RECENT-EVENT READERS -- completion rate, recharges, evacuations, dirt.
#
# Moved here from test_sensors.py (August 2026). All four
# `_raw_*` functions are defined in this module. `_rec` is already in
# this file, copied with the last-error classes.
# ============================================================================


from custom_components.roomba_plus.sensor_cloud import (  # noqa: E402
    _raw_completion_rate,
    _raw_dirt_events,
    _raw_evacuations,
    _raw_recharges,
)


class TestRecentCompletionRate:

    def test_all_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 4
        assert _raw_completion_rate(records) == 100.0

    def test_half_completed(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="done")] * 2 + [_rec(done="stuck", pause_id=17)] * 2
        assert _raw_completion_rate(records) == 50.0

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        assert _raw_completion_rate([]) is None

    def test_rounded_to_one_decimal(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        # 2 of 3 = 66.666...% → 66.7
        records = [_rec(done="done")] * 2 + [_rec(done="stuck")]
        assert _raw_completion_rate(records) == 66.7

    def test_zero_percent(self):
        from custom_components.roomba_plus.sensor import _raw_completion_rate
        records = [_rec(done="stuck")] * 3
        assert _raw_completion_rate(records) == 0.0


class TestRecentRecharges:

    def test_sums_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=2), _rec(chrgs=1), _rec(chrgs=0)]
        assert _raw_recharges(records) == 3

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        assert _raw_recharges([]) is None

    def test_zero_recharges(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [_rec(chrgs=0)] * 5
        assert _raw_recharges(records) == 0

    def test_handles_missing_chrgs(self):
        from custom_components.roomba_plus.sensor import _raw_recharges
        records = [{"done": "done", "classified_result": "completed"}]
        assert _raw_recharges(records) == 0


class TestRecentEvacuations:

    def test_sums_evacs(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=1), _rec(evacs=3), _rec(evacs=0)]
        assert _raw_evacuations(records) == 4

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        assert _raw_evacuations([]) is None

    def test_zero_evacuations(self):
        from custom_components.roomba_plus.sensor import _raw_evacuations
        records = [_rec(evacs=0)] * 3
        assert _raw_evacuations(records) == 0


class TestRecentDirtEvents:

    def test_sums_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=5), _rec(dirt=10), _rec(dirt=2)]
        assert _raw_dirt_events(records) == 17

    def test_none_when_empty(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        assert _raw_dirt_events([]) is None

    def test_zero_dirt(self):
        from custom_components.roomba_plus.sensor import _raw_dirt_events
        records = [_rec(dirt=0)] * 4
        assert _raw_dirt_events(records) == 0


# The three CloudLastError classes moved to test_sensor_cloud.py
# (August 2026) -- they test _raw_cloud_last_error_* , defined there.
# `_rec` above stays: thirteen tests here still use it.


# Moved to test_sensor_helpers.py (August 2026).


# ============================================================================
# MISSIONS-PER-CHARGE and HEALTH-SCORE-TREND sensors.
#
# Moved here from test_sensors.py (August 2026); both entity classes are
# defined in this module. `_make_health_trend_sensor` came with them --
# nothing else used it.
# ============================================================================


from custom_components.roomba_plus.sensor_cloud import (  # noqa: E402
    RoombaHealthScoreTrendSensor,
    RoombaMissionsPerChargeSensor,
)


def _make_health_trend_sensor(rps):
    """Return a RoombaHealthScoreTrendSensor with the given robot_profile_store
    (or None) wired into runtime_data."""
    from custom_components.roomba_plus.sensor import RoombaHealthScoreTrendSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    entry.runtime_data.robot_profile_store = rps
    sensor = RoombaHealthScoreTrendSensor.__new__(RoombaHealthScoreTrendSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._config_entry = entry
    sensor._attr_unique_id = "test_blid_health_score_trend"
    return sensor



class TestMissionsPerChargeSensor:
    def test_high_when_no_recharges(self):
        archive = _make_archive([_derived(i, recharge_count=0) for i in range(1, 11)])
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        # 10 missions / (1 + 0) = 10.0
        assert sensor.native_value == 10.0

    def test_lower_with_recharges(self):
        # 10 missions, 4 mid-mission recharges → 10 / (1 + 4) = 2.0
        records = (
            [_derived(i, recharge_count=1) for i in range(1, 5)] +
            [_derived(i, recharge_count=0) for i in range(5, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        assert sensor.native_value == 2.0

    def test_returns_none_when_no_recent(self):
        # Records older than 30 days
        records = [_derived(i, days_ago=60) for i in range(1, 6)]
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        assert sensor.native_value is None

    def test_attributes_breakdown(self):
        records = (
            [_derived(i, recharge_count=1) for i in range(1, 3)] +
            [_derived(i, recharge_count=0) for i in range(3, 11)]
        )
        archive = _make_archive(records)
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, archive)
        attrs = sensor.extra_state_attributes
        assert attrs["missions_30d"] == 10
        assert attrs["mid_mission_recharges_30d"] == 2
        assert attrs["single_charge_pct"] == 80.0

    def test_returns_none_when_archive_none(self):
        sensor = _make_sensor_v280_bat_arch(RoombaMissionsPerChargeSensor, None)
        assert sensor.native_value is None



class TestHealthScoreTrendSensor:
    """L10 (v3.2.0) — RoombaHealthScoreTrendSensor delegates to
    RobotProfileStore.health_score_trend() / .health_score_declining_days().
    """

    def test_native_value_none_when_no_profile_store(self):
        sensor = _make_health_trend_sensor(None)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_native_value_none_when_not_enough_history(self):
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        rps.record_health_score(80.0, "2026-06-01")
        sensor = _make_health_trend_sensor(rps)
        assert sensor.native_value is None

    def test_native_value_reflects_declining_trend(self):
        """v3.2.0 bug-hunt fix — 36 stable reference days keeps the
        exclusion buffer (30 days) clean of the decline; 14 declining
        days matches the Repair Issue's own trigger threshold."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        import datetime as _dt
        rps = RobotProfileStore()
        d0 = _dt.date(2026, 6, 1)
        dates = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(36 + 14)]
        for d in dates[:36]:
            rps.record_health_score(85.0, d)
        for d in dates[36:]:
            rps.record_health_score(40.0, d)
        sensor = _make_health_trend_sensor(rps)
        assert sensor.native_value == "declining"

    def test_attributes_expose_baseline_and_declining_days(self):
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        import datetime as _dt
        rps = RobotProfileStore()
        d0 = _dt.date(2026, 6, 1)
        dates = [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(36 + 14)]
        for d in dates[:36]:
            rps.record_health_score(85.0, d)
        for d in dates[36:]:
            rps.record_health_score(40.0, d)
        sensor = _make_health_trend_sensor(rps)
        attrs = sensor.extra_state_attributes
        assert attrs["baseline_ready"] is True
        assert attrs["days_recorded"] == 50
        assert attrs["declining_days"] == 14
        assert attrs["days_until_ready"] == 0

    def test_days_until_ready_counts_down_before_baseline_ready(self):
        """v3.2.0 UX fix — days_recorded alone required the user to
        already know the 44-day threshold and do the subtraction
        themselves. This makes it explicit."""
        from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
        rps = RobotProfileStore()
        for i in range(10):
            rps.record_health_score(80.0, f"2026-06-{i + 1:02d}")
        sensor = _make_health_trend_sensor(rps)
        attrs = sensor.extra_state_attributes
        assert attrs["baseline_ready"] is False
        assert attrs["days_recorded"] == 10
        assert attrs["days_until_ready"] == 34


