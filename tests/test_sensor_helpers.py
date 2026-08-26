


def _make_entity(mission_status: dict):
    class _FakeEntity:
        @property
        def clean_mission_status(self):
            return mission_status
        @property
        def vacuum_state(self):
            return {"cleanMissionStatus": mission_status}

    return _FakeEntity()




# A module-level fake, not a `def` -- which is why the helper scan
# missed it twice.
class _FakeEntity:
    def __init__(self, state: dict, vacuum_state: dict | None = None):
        self._state = state
        self.vacuum_state = vacuum_state or state
        self._vac = type("V", (), {"error_message": None, "error_code": 0})()

    @property
    def clean_mission_status(self):
        return self._state.get("cleanMissionStatus", {})

    @property
    def vacuum(self):
        return self._vac



class TestReadinessStateDecoding:
    """The readiness sensor was untested and wrong.

    It treated notReady as a bitmask: a nine-entry table of exact values
    plus a bit-by-bit fallback that assembled labels like "Updating map,
    Pending task" out of a premise that does not hold.

    The iRobot Home app reads it as a scalar index into a 73-state enum
    with an offset above 10. Six of the nine entries were wrong against
    that; only 0 and 15 held up. Nothing caught it because nothing
    tested it -- the whole function had no coverage.
    """

    def _value(self, not_ready):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import _not_ready_value

        entity = MagicMock()
        entity.clean_mission_status = {"notReady": not_ready}
        return _not_ready_value(entity)

    def test_ready(self):
        assert self._value(0) == "Ready"

    def test_the_six_that_were_wrong(self):
        """Each of these showed something the app does not say."""
        assert self._value(2) == "Wheel drop both"      # was "Uneven ground"
        assert self._value(16) == "Bin full"            # was "Bumped unexpectedly"
        assert self._value(31) == "Schedule no clock"   # was "Fill tank"
        assert self._value(39) == "Charge timeout"      # was "Pending"
        assert self._value(48) == "Safety fault hardware"  # was "Path blocked"
        assert self._value(68) == "Off dock"            # was "Updating map"

    def test_the_map_state_is_67_not_68(self):
        """The seed of the whole bitmask story: 68 was labelled "Updating
        map", and 68 & 64 is true, so a bit test looked like it worked.
        The state that means the map is updating is 67."""
        assert self._value(67) == "Downloading map"
        assert self._value(68) != "Downloading map"

    def test_the_two_that_were_right_still_are(self):
        assert self._value(15) == "Insufficient charge"  # was "Low battery"

    def test_the_offset_applies_above_ten(self):
        """Wire 25 is index 22. Reading the wire value straight out of a
        73-entry list would give a different state."""
        assert self._value(25) == "Map version mismatch"

    def test_an_unlisted_value_keeps_its_number(self):
        """No decomposition into invented parts. A state this project
        does not know should say so."""
        assert self._value(200) == "Not ready (200)"

    def test_a_non_integer_does_not_raise(self):
        assert self._value("busy") == "Not ready (busy)"
        assert self._value(None) == "Ready"


# ============================================================================
# WI-FI AND NETWORK READERS.
#
# Moved here from test_sensors.py (August 2026). `_raw_wifi_floor`,
# `_raw_wifi_stability` and `_parse_netinfo_addr` are all defined in
# this module.
#
# Not all the "Wifi" test classes came: the ones testing
# `RoombaWifiHealthSensor` and its siblings went to test_sensor_cloud.py
# instead, because those entity classes live there. The functions and
# the entities that call them sit in different modules, and the test
# file names now say which is which.
# ============================================================================


from unittest.mock import MagicMock  # noqa: E402

# Imported from sensor_helpers directly. test_sensors.py reached these
# through the sensor.py facade, which re-exports them -- which is why
# searching for "who imports sensor_helpers" found nothing.
import datetime  # noqa: E402
from unittest.mock import patch  # noqa: E402

from custom_components.roomba_plus.sensor import SENSORS  # noqa: E402
from custom_components.roomba_plus.sensor_helpers import (  # noqa: E402
    _parse_netinfo_addr,
    _raw_wifi_floor,
    _raw_wifi_stability,
)


class TestWifiFloor:
    """Amendment 8d — wlBars is a 5-element histogram, not a time-series."""

    def test_returns_lowest_nonempty_bucket(self):
        # [0, 35, 65, 0, 0]: bucket 1 is lowest non-zero → floor = 1
        records = [{"wlBars": [0, 35, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 1

    def test_bucket_zero_populated(self):
        # [5, 30, 65, 0, 0]: bucket 0 has readings → floor = 0
        records = [{"wlBars": [5, 30, 65, 0, 0]}]
        assert _raw_wifi_floor(records) == 0

    def test_all_strong_signal(self):
        # [0, 0, 0, 40, 60]: only buckets 3/4 → floor = 3
        records = [{"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_floor([]) is None

    def test_returns_none_when_wlbars_none(self):
        assert _raw_wifi_floor([{"wlBars": None}]) is None

    def test_returns_none_when_all_zero_histogram(self):
        assert _raw_wifi_floor([{"wlBars": [0, 0, 0, 0, 0]}]) is None

    def test_skips_records_without_wlbars(self):
        records = [{"sqft": 100}, {"wlBars": [0, 0, 70, 30, 0]}]
        assert _raw_wifi_floor(records) == 2

    def test_must_be_exactly_5_elements(self):
        # Wrong length histogram — skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}]
        assert _raw_wifi_floor(records) == 3


class TestWifiStability:
    """Amendment 8d — weighted stdev of signal bucket distribution."""

    def test_concentrated_is_low_stdev(self):
        # All readings in bucket 3 → stdev ≈ 0
        records = [{"wlBars": [0, 0, 0, 100, 0]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val < 0.1

    def test_spread_is_high_stdev(self):
        # Evenly spread across all 5 buckets → high stdev
        records = [{"wlBars": [20, 20, 20, 20, 20]}] * 3
        val = _raw_wifi_stability(records)
        assert val is not None and val > 0.5

    def test_returns_none_when_fewer_than_3_records(self):
        records = [{"wlBars": [0, 35, 65, 0, 0]}] * 2
        assert _raw_wifi_stability(records) is None

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_stability([]) is None

    def test_skips_non_5element_histograms(self):
        # 3-element arrays are invalid — should be skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [0, 0, 0, 40, 60]}] * 3
        val = _raw_wifi_stability(records)
        # Only the valid 5-element records contribute
        assert val is not None

    def test_result_is_float(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert isinstance(result, float)

    def test_result_rounded_to_2_decimals(self):
        records = [{"wlBars": [0, 20, 60, 20, 0]}] * 3
        result = _raw_wifi_stability(records)
        assert result == round(result, 2)



class TestParseNetinfoAddr:
    def test_string_format_returned_as_is(self):
        """i/s/j-series: dotted string → pass through unchanged."""
        assert _parse_netinfo_addr("192.168.1.5") == "192.168.1.5"

    def test_uint32_192_168_1_1(self):
        """9-series: uint32 big-endian 0xC0A80101 = 192.168.1.1."""
        # 192*2^24 + 168*2^16 + 1*2^8 + 1 = 3232235777
        assert _parse_netinfo_addr(3232235777) == "192.168.1.1"

    def test_uint32_10_0_0_1(self):
        """10.0.0.1 = 0x0A000001 = 167772161."""
        assert _parse_netinfo_addr(167772161) == "10.0.0.1"

    def test_uint32_zero_is_0_0_0_0(self):
        """uint32 0 → '0.0.0.0' (valid but unusual)."""
        assert _parse_netinfo_addr(0) == "0.0.0.0"

    def test_none_returns_none(self):
        assert _parse_netinfo_addr(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_netinfo_addr("") is None

    def test_ip_address_sensor_uses_parser(self):
        """ip_address sensor value_fn calls _parse_netinfo_addr for uint32."""
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.entity import IRobotEntity
        from custom_components.roomba_plus.sensor import SENSORS

        desc = next(d for d in SENSORS if d.key == "ip_address")

        e = object.__new__(IRobotEntity)
        e._blid = "test"
        e._roomba = MagicMock()
        # Simulate 9-series uint32 addr
        e.vacuum_state = {"netinfo": {"addr": 3232235777}}

        assert desc.value_fn(e) == "192.168.1.1"


# ═══════════════════════════════════════════════════════════════════════
# Merged from test_new_sensors.py (TEST-REORG, v2.9.1). Original module
# docstring: 'Unit tests for the 7 new sensors added in the latest
# iteration' — covers _phase_value, _ts_or_none, _mission_elapsed_value,
# ERROR_CODE_LABELS, signal sensors (SNR/Noise/IP), FW-SENSOR (v2.8.3).
# ═══════════════════════════════════════════════════════════════════════

# ── Helper: minimal IRobotEntity mock ────────────────────────────────────────


# ============================================================================
# VALUE READERS -- battery age, mop state, phase, timestamps, countdowns.
#
# Moved here from test_sensors.py (August 2026): eleven classes, 72
# tests, all exercising functions defined in this module.
#
# This was the last group a name-based search found, and the largest.
# Searching for CLASS names had already moved the entity tests; these
# test plain FUNCTIONS, so only a second pass looking for `def` names
# turned them up. Two searches, two different kinds of miss.
#
# `_utcnow_returning` and `_entity` are COPIED: tests elsewhere use them.
# ============================================================================


from custom_components.roomba_plus.sensor_helpers import (  # noqa: E402
    _area_cleaned_today,
    _battery_age_days,
    _estimated_battery_eol,
    _expire_minutes_remaining,
    _last_mission_team_id,
    _mission_elapsed_value,
    _mop_behavior,
    _mop_clean_mode,
    _mop_tank_status,
    _phase_value,
    _recharge_minutes_remaining,
    _ts_or_none,
)


def _utcnow_returning(ts: int):
    """Return a context manager that freezes dt_util.utcnow() to ts."""
    frozen = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return patch(
        "custom_components.roomba_plus.sensor.dt_util.utcnow",
        return_value=frozen,
    )



def _entity(state: dict) -> MagicMock:
    """Return a fake IRobotEntity with the given vacuum_state."""
    e = MagicMock()
    e.vacuum_state = state
    return e



class TestRechargeMinutesRemainingHelper:
    """_recharge_minutes_remaining: timestamp-first logic for all firmware."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _recharge_minutes_remaining
        with _utcnow_returning(now_ts):
            return _recharge_minutes_remaining(mission)

    # ── i7 / lewis firmware path (Thonno's robot) ────────────────────────────
    # rechrgM=0, rechrgTm set — this was already handled in v2.0.0.
    # The freeze bug was caused by the missing periodic tick, not this function.

    def test_lewis_computes_from_rechrgTm(self):
        """i7 (lewis): rechrgM=0, rechrgTm set → compute remaining minutes."""
        # rechrgTm=1780150205, now=1780150000 → 205 seconds → 3 minutes (rounded)
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780150000)
        assert result == 3

    def test_lewis_field_diagnostics_case(self):
        """Exact values from Bogdana diagnostics (i755840) — 277s remaining → 5 min."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150205}, now_ts=1780149928)
        assert result == 5

    def test_lewis_returns_none_when_rechrgTm_in_past(self):
        """rechrgTm expired → recharge done → None."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_lewis_returns_minimum_one_minute(self):
        """< 30 seconds remaining rounds to 1 min, not 0."""
        result = self._call({"rechrgM": 0, "rechrgTm": 1780150020}, now_ts=1780150000)
        assert result == 1

    def test_lewis_returns_none_when_rechrgTm_zero(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    # ── 900-series / rechrgTm-priority fix ───────────────────────────────────
    # On 900/980-series, rechrgM is a static snapshot; rechrgTm is authoritative.
    # The old code returned rechrgM directly, which never decremented.

    def test_900_prefers_rechrgTm_over_static_rechrgM(self):
        """900-series: rechrgTm is preferred; static rechrgM is ignored."""
        # rechrgTm=1780150600, now=1780150000 → 600s → 10 min
        # Old code would have returned rechrgM=78 (static, wrong)
        result = self._call({"rechrgM": 78, "rechrgTm": 1780150600}, now_ts=1780150000)
        assert result == 10

    def test_900_series_value_decrements_over_time(self):
        """Demonstrate that rechrgTm-based value decrements, rechrgM-based would not."""
        recharge_end_ts = 1780150000 + 78 * 60  # end = now + 78 min
        # At t=0: both approaches agree
        result_t0 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts}, now_ts=1780150000
        )
        assert result_t0 == 78
        # At t+30min: rechrgTm gives 48, old static rechrgM would give 78 (frozen)
        result_t30 = self._call(
            {"rechrgM": 78, "rechrgTm": recharge_end_ts},
            now_ts=1780150000 + 30 * 60,
        )
        assert result_t30 == 48  # correctly decremented

    # ── Fallback: very old firmware (rechrgTm absent) ─────────────────────────

    def test_fallback_to_rechrgM_when_rechrgTm_zero(self):
        """rechrgTm absent / zero → fall back to rechrgM (old firmware)."""
        result = self._call({"rechrgM": 15, "rechrgTm": 0})
        assert result == 15

    def test_both_zero_returns_none(self):
        assert self._call({"rechrgM": 0, "rechrgTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None

    def test_none_values(self):
        assert self._call({"rechrgM": None, "rechrgTm": None}) is None


class TestExpireMinutesRemainingHelper:
    """_expire_minutes_remaining: same timestamp-first logic."""

    def _call(self, mission: dict, now_ts: int = 1780150000) -> int | None:
        from custom_components.roomba_plus.sensor import _expire_minutes_remaining
        with _utcnow_returning(now_ts):
            return _expire_minutes_remaining(mission)

    def test_prefers_expireTm_over_expireM(self):
        result = self._call({"expireM": 30, "expireTm": 1780150600}, now_ts=1780150000)
        assert result == 10   # 600s → 10 min, not static expireM=30

    def test_lewis_computes_from_expireTm(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780150000)
        assert result == 8   # 482s → 8 min

    def test_lewis_field_diagnostics_case(self):
        result = self._call({"expireM": 0, "expireTm": 1780150482}, now_ts=1780149928)
        assert result == 9   # 554s → 9 min

    def test_expired_returns_none(self):
        result = self._call({"expireM": 30, "expireTm": 1780149000}, now_ts=1780150000)
        assert result is None

    def test_fallback_to_expireM_when_expireTm_zero(self):
        result = self._call({"expireM": 30, "expireTm": 0})
        assert result == 30

    def test_both_zero_returns_none(self):
        assert self._call({"expireM": 0, "expireTm": 0}) is None

    def test_missing_fields(self):
        assert self._call({}) is None



class TestMopCleanMode:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase slugs, was Capital-Case before."""

    def test_level_1_is_dry(self):
        e = _entity({"padWetness": {"disposable": 1}})
        assert _mop_clean_mode(e) == "dry"

    def test_level_2_is_wet(self):
        e = _entity({"padWetness": {"disposable": 2}})
        assert _mop_clean_mode(e) == "wet"

    def test_level_3_is_wet(self):
        e = _entity({"padWetness": {"reusable": 3}})
        assert _mop_clean_mode(e) == "wet"

    def test_missing_padwetness_is_unknown(self):
        e = _entity({})
        assert _mop_clean_mode(e) == "unknown"

    def test_empty_dict_is_unknown(self):
        e = _entity({"padWetness": {}})
        assert _mop_clean_mode(e) == "unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_clean_mode" in keys

    def test_filter_fn_requires_padwetness(self):
        desc = next(d for d in SENSORS if d.key == "mop_clean_mode")
        assert desc.filter_fn({"padWetness": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopTankStatus:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs, was
    Capital-Case-with-spaces before (spaces were never valid as
    translation_key state keys, this was a pre-existing hassfest violation)."""

    def test_all_ok_is_ready(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": False}})
        assert _mop_tank_status(e) == "ready"

    def test_fill_required(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": True, "fillRequired": True}})
        assert _mop_tank_status(e) == "fill_tank"

    def test_lid_open_takes_priority_over_fill(self):
        e = _entity({"mopReady": {"tankPresent": True, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "lid_open"

    def test_tank_missing_highest_priority(self):
        e = _entity({"mopReady": {"tankPresent": False, "lidClosed": False, "fillRequired": True}})
        assert _mop_tank_status(e) == "tank_missing"

    def test_missing_mopready_is_unknown(self):
        e = _entity({})
        assert _mop_tank_status(e) == "unknown"

    def test_non_dict_mopready_is_unknown(self):
        e = _entity({"mopReady": 1})
        assert _mop_tank_status(e) == "unknown"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_tank_status" in keys

    def test_filter_fn_requires_mopready(self):
        desc = next(d for d in SENSORS if d.key == "mop_tank_status")
        assert desc.filter_fn({"mopReady": {}}) is True
        assert desc.filter_fn({}) is False


class TestMopBehavior:
    """v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs, combination
    modes join with "_" instead of the old " + " separator."""

    def test_rank_15_no_mop(self):
        e = _entity({"rankOverlap": 15})
        assert _mop_behavior(e) == "no_mop"

    def test_rank_67_standard(self):
        e = _entity({"rankOverlap": 67})
        assert _mop_behavior(e) == "standard"

    def test_rank_85_deep(self):
        e = _entity({"rankOverlap": 85})
        assert _mop_behavior(e) == "deep"

    def test_unknown_rank(self):
        e = _entity({"rankOverlap": 99})
        assert _mop_behavior(e) == "unknown"

    def test_flag_combination_dry_only(self):
        e = _entity({"padDryAllowed": 1, "padWashAllowed": 0, "padDirtyPause": 0})
        assert _mop_behavior(e) == "dry"

    def test_flag_combination_dirty_pause_plus_dry_plus_wash(self):
        e = _entity({"padDirtyPause": 1, "padDryAllowed": 1, "padWashAllowed": 1})
        assert _mop_behavior(e) == "dirty_pause_dry_wash"

    def test_no_flags_is_unknown(self):
        e = _entity({"padDryAllowed": 0, "padWashAllowed": 0})
        assert _mop_behavior(e) == "unknown"

    def test_rankOverlap_takes_precedence_over_flags(self):
        e = _entity({"rankOverlap": 25, "padDryAllowed": 1})
        assert _mop_behavior(e) == "extended"

    def test_sensor_description_in_sensors(self):
        keys = [d.key for d in SENSORS]
        assert "mop_ars_behavior" in keys

    def test_filter_fn_rankOverlap(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"rankOverlap": 67}) is True

    def test_filter_fn_padDryAllowed(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"padDryAllowed": 1}) is True

    def test_filter_fn_absent_for_vacuums(self):
        desc = next(d for d in SENSORS if d.key == "mop_ars_behavior")
        assert desc.filter_fn({"batPct": 85}) is False



class TestEstimatedBatteryEolNiMHGuard:
    """estimated_battery_eol filter: only estCap presence matters (v2.5.0)."""

    def _desc(self):
        from custom_components.roomba_plus.sensor import SENSORS
        return next(d for d in SENSORS if d.key == "estimated_battery_eol")

    def test_lithium_surfaces(self):
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}, "batteryType": "lipo"}) is True

    def test_nimh_string_now_surfaces(self):
        """batteryType='nimh' no longer suppressed — filter only checks estCap."""
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}) is True

    def test_no_battery_type_surfaces(self):
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {"estCap": 2000}}) is True

    def test_980_exact_state_surfaces(self):
        """980 exact state: sensor now surfaces (batteryType is a part number, not 'nimh')."""
        desc = self._desc()
        state = {
            "bbchg3": {"estCap": 9720, "nLithChrg": 290, "nNimhChrg": 19},
            "batteryType": "F12432712",
        }
        assert desc.filter_fn(state) is True

    def test_zero_baseline_estcap_does_not_crash(self):
        """_estimated_battery_eol must not ZeroDivisionError on baseline_estcap == 0.

        A corrupted or hand-edited persisted store could hold
        baseline_estcap: 0. The old `is None` guard would not catch it and the
        current_pct division would raise ZeroDivisionError, taking down the
        sensor. The hardened falsy-check returns None instead.
        """
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.sensor import _estimated_battery_eol

        entity = MagicMock()
        store = MagicMock()
        store.baseline_estcap = 0  # corrupted persisted value
        entity._config_entry.runtime_data.maintenance_store = store
        # Must return None, not raise
        assert _estimated_battery_eol(entity) is None



class TestLastMissionTeamId:
    """v3.2.0 TEAM-INDICATOR — _last_mission_team_id reads team_id off the
    most recent mission record, None-safe when absent or no history yet."""

    def _team_id(self, latest):
        from custom_components.roomba_plus.sensor import _last_mission_team_id

        class _FakeStore:
            def latest(self):
                return latest

        return _last_mission_team_id(_FakeStore())

    def test_returns_team_id_when_present(self):
        assert self._team_id({"id": "m_1", "team_id": "IplhZn-R"}) == "IplhZn-R"

    def test_returns_none_when_team_id_absent_from_record(self):
        assert self._team_id({"id": "m_1"}) is None

    def test_returns_none_when_no_mission_history(self):
        assert self._team_id(None) is None


# TestCleaningPerformanceSensor and TestCleaningAnalytics30dSensor
# moved to test_sensor_cloud.py (August 2026).


# TestWifiHealthSensor moved to test_sensor_cloud.py (August 2026).



class TestBatteryAgeDays:
    """_battery_age_days must parse mDate and return days since manufacture."""

    def test_valid_mdate(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {"batInfo": {"mDate": "2022-10-24"}}
        days = _battery_age_days(e)
        assert days is not None and days > 500  # battery is over 3 years old

    def test_missing_batInfo_returns_none(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {}
        assert _battery_age_days(e) is None

    def test_invalid_date_returns_none(self):
        from custom_components.roomba_plus.sensor import _battery_age_days
        e = MagicMock()
        e.vacuum_state = {"batInfo": {"mDate": "bad-date"}}
        assert _battery_age_days(e) is None



class TestPhaseValue:
    def test_idle_when_charging_and_full(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "charge", "cycle": "none"}, "batPct": 100})
        assert _phase_value(e) == "idle"

    def test_not_idle_when_charging_not_full(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "charge", "cycle": "none"}, "batPct": 80})
        assert _phase_value(e) == "charging"

    def test_stopped_when_cycle_none_phase_stop(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "stop", "cycle": "none"}, "batPct": 50})
        assert _phase_value(e) == "stopped"

    def test_running_normal(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "run", "cycle": "clean"}, "batPct": 90})
        assert _phase_value(e) == "running"

    def test_stuck(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "stuck", "cycle": "clean"}, "batPct": 60})
        assert _phase_value(e) == "stuck"

    def test_unknown_phase_returns_the_unknown_key(self):
        """WAS "returns the phase verbatim". Since the sensor became
        translatable its value is a translation key, and a raw firmware
        word has none -- so an unmapped phase reports `unknown` rather
        than a string Home Assistant cannot translate."""
        e = _FakeEntity({"cleanMissionStatus": {"phase": "mystery", "cycle": "none"}, "batPct": 50})
        assert _phase_value(e) == "unknown"

    def test_empty_phase_returns_unknown(self):
        e = _FakeEntity({"cleanMissionStatus": {}, "batPct": 50})
        assert _phase_value(e) == "unknown"

    def test_paused(self):
        e = _FakeEntity({"cleanMissionStatus": {"phase": "pause", "cycle": "clean"}, "batPct": 70})
        assert _phase_value(e) == "paused"


# ── _ts_or_none ───────────────────────────────────────────────────────────────

from custom_components.roomba_plus.sensor import _ts_or_none


class TestTsOrNone:
    def test_none_input(self):
        assert _ts_or_none(None) is None

    def test_zero_input(self):
        assert _ts_or_none(0) is None

    def test_valid_timestamp(self):
        result = _ts_or_none(1700000000)
        assert result is not None
        assert isinstance(result, datetime.datetime)

    def test_negative_timestamp(self):
        # Negative = before epoch — should still convert
        result = _ts_or_none(-1)
        assert result is not None


# ── _mission_elapsed_value ────────────────────────────────────────────────────

from custom_components.roomba_plus.sensor import _mission_elapsed_value
import time


class TestMissionElapsedValue:
    def test_no_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {}})
        assert _mission_elapsed_value(e) is None

    def test_zero_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": 0}})
        assert _mission_elapsed_value(e) is None

    def test_recent_start_returns_positive(self):
        ts = int(time.time()) - 300  # 5 minutes ago
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert result is not None
        assert result >= 4.9  # at least ~5 min
        assert result < 10    # sanity check

    def test_returns_float(self):
        ts = int(time.time()) - 60
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert isinstance(result, float)


# ── ERROR_CODE_LABELS ─────────────────────────────────────────────────────────

from custom_components.roomba_plus.const import ERROR_CODE_LABELS


