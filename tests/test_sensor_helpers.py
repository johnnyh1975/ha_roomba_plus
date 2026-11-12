"""Sensor helper behaviour, including readiness decoding."""

import datetime
from unittest.mock import MagicMock, patch


from custom_components.roomba_plus.sensor_core import SENSORS
from custom_components.roomba_plus.sensor_helpers import (
    _parse_netinfo_addr,
    _raw_wifi_floor,
    _raw_wifi_stability,
)

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
    """`notReady` is decoded through an explicit wire-to-index table.

    THERE IS NO OFFSET RULE. This used to apply `raw <= 10 ? raw :
    raw - 3`, taken from the app, and it was wrong for most of the
    range: the firmware maps wire values through an arbitrary lookup
    whose order is deliberately broken. `BUMPED` is wire 33 and state
    21; no arithmetic joins them.

    The rule worked below 10, where wire and index were assigned in
    step, and LOOKED right above 60, where they run parallel again but
    four apart rather than three. Everywhere else it produced a
    confident wrong name.

    Two field reports paid for that: @Thonno's i7+ and
    @ScenicSystemsLLC's S9+ both reported wire 68 while docked and
    charging, and it was read as "Off dock". It is `LOADING_MAP`.

    The sensor returns translation KEYS, not English labels -- see
    `_readiness_slug`.
    """

    @staticmethod
    def _value(not_ready):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import (
            _not_ready_value,
        )

        entity = MagicMock()
        entity.clean_mission_status = {"notReady": not_ready}
        return _not_ready_value(entity)

    def test_ready(self):
        assert self._value(0) == "ready"

    def test_the_block_that_was_off_by_one(self):
        """Four consecutive firmware pairs. All four were wrong before,
        each naming the state one place below the right one."""
        assert self._value(66) == "subscription_expired"
        assert self._value(67) == "dead_navigation_board"
        assert self._value(68) == "downloading_map"
        assert self._value(69) == "off_dock"

    def test_sixty_eight_is_the_map_not_the_dock(self):
        """The one that cost two testers their diagnosis. Both robots
        were charging on their docks while this read "Off dock"."""
        assert self._value(68) == "downloading_map"
        assert self._value(68) != "off_dock"

    def test_where_no_arithmetic_could_have_worked(self):
        """Wire 33 is state 21, wire 39 is state 37. Any single offset
        gets both wrong, which is why the table is explicit."""
        assert self._value(33) == "bumped"
        assert self._value(39) == "saving_map"

    def test_below_ten_wire_and_state_coincide(self):
        """Firmware-confirmed, and the reason the old rule survived."""
        assert self._value(1) == "cliff"
        assert self._value(2) == "wheel_drop_both"
        assert self._value(6) == "brush_stall"
        assert self._value(7) == "no_bin"

    def test_the_everyday_states(self):
        """The ones a user actually meets. All were bare numbers until
        the firmware table was extracted."""
        assert self._value(16) == "bin_full"
        assert self._value(15) == "insufficient_charge"
        assert self._value(31) == "tank_low"
        assert self._value(24) == "map_version_mismatch"
        assert self._value(34) == "invalid_pad"

    def test_the_three_battery_values_are_three_states(self):
        """4.5.0: 37 and 38 were collapsed onto `BatteryAuthError` for
        want of a closer state. The Classic app's own refusal table
        (keyed by the wire value) names them."""
        assert self._value(23) == "battery_auth_error"
        assert self._value(37) == "sm_bus_permanent_failure"
        assert self._value(38) == "charge_timeout"

    def test_the_apps_table_fills_the_gaps(self):
        """Values that showed as a bare number, now with the state the
        Classic app files them under -- 27 among them, the `PP_COMM`
        whose bridge to MobilityOffline waited for a confirmation."""
        assert self._value(27) == "mobility_offline"
        assert self._value(14) == "invalid_command"
        assert self._value(271) == "dead_floor_type_sensor"

    def test_states_the_firmware_cannot_express(self):
        """`LocalizationFailed`, `NotDocked`, `LidOpen`, `ChargeTimeout`,
        `NoPad` and `OtaUpdate` are in the app's enum and absent from the
        firmware's map. A robot hitting one reports 99, not a state.

        This is why @Thonno's failed start showed 68 (LOADING_MAP)
        rather than a localisation state while erroring with 224
        "Smart Map localization failed" -- not a contradiction.
        """
        from custom_components.roomba_plus.const import (
            READINESS_STATE_LABELS,
            READINESS_WIRE_TO_INDEX,
        )

        reachable = set(READINESS_WIRE_TO_INDEX.values())
        for index, label in (
            (48, "Localization failed"),
            (53, "Not docked"),
        ):
            assert READINESS_STATE_LABELS[index] == label
            assert index not in reachable, label

    def test_an_unconfirmed_value_keeps_its_number(self):
        """No invented name. A value this project has not confirmed
        should say so -- guessing one is what caused the bug above."""
        assert self._value(200) == "not_ready_200"
        assert self._value(45) == "not_ready_45"

    def test_a_non_integer_does_not_raise(self):
        assert self._value("nonsense") is not None
        assert self._value(None) is not None



class TestWifiFloor:
    """Amendment 8d — wlBars is a 5-element histogram, not a time-series.

    4.5.3: ordered STRONGEST FIRST, and the floor is returned in bars
    (0 = none, 4 = full). The histograms were mirrored, so the expected
    floors stay what they meant (@albertompinto)."""

    def test_returns_lowest_nonempty_bucket(self):
        # [0, 0, 65, 35, 0]: weakest share at 1 bar → floor = 1
        records = [{"wlBars": [0, 0, 65, 35, 0]}]
        assert _raw_wifi_floor(records) == 1

    def test_bucket_zero_populated(self):
        # [0, 0, 65, 30, 5]: some time at no bars → floor = 0
        records = [{"wlBars": [0, 0, 65, 30, 5]}]
        assert _raw_wifi_floor(records) == 0

    def test_all_strong_signal(self):
        # [60, 40, 0, 0, 0]: only 4 and 3 bars → floor = 3
        records = [{"wlBars": [60, 40, 0, 0, 0]}]
        assert _raw_wifi_floor(records) == 3

    def test_returns_none_on_empty_list(self):
        assert _raw_wifi_floor([]) is None

    def test_returns_none_when_wlbars_none(self):
        assert _raw_wifi_floor([{"wlBars": None}]) is None

    def test_returns_none_when_all_zero_histogram(self):
        assert _raw_wifi_floor([{"wlBars": [0, 0, 0, 0, 0]}]) is None

    def test_skips_records_without_wlbars(self):
        records = [{"sqft": 100}, {"wlBars": [0, 30, 70, 0, 0]}]
        assert _raw_wifi_floor(records) == 2

    def test_must_be_exactly_5_elements(self):
        # Wrong length histogram — skipped
        records = [{"wlBars": [70, 60, 80]}, {"wlBars": [60, 40, 0, 0, 0]}]
        assert _raw_wifi_floor(records) == 3


class TestWlBarsIsOrderedStrongestFirst:
    """4.5.3, @albertompinto: a j9+ at -54 dBm on average showed 3.5 %
    Wi-Fi health. wlBars index i holds the time spent at 4 - i bars."""

    _J9 = [
        [91, 9, 0, 0, 0], [92, 8, 0, 0, 0], [80, 18, 2, 0, 0], [86, 14, 0, 0, 0],
        [83, 16, 1, 0, 0], [38, 62, 0, 0, 0], [100, 0, 0, 0, 0], [94, 6, 0, 0, 0],
        [81, 16, 3, 0, 0], [100, 0, 0, 0, 0], [92, 8, 0, 0, 0], [61, 39, 0, 0, 0],
        [91, 9, 0, 0, 0], [79, 17, 4, 0, 0], [84, 16, 0, 0, 0],
    ]

    def test_his_missions_read_as_strong_wifi(self):
        from custom_components.roomba_plus.sensor_helpers import _raw_wifi_quality_pct

        value = _raw_wifi_quality_pct([{"wlBars": bars} for bars in self._J9])
        assert value is not None and 95.0 <= value <= 97.0

    def test_the_clouds_own_start_and_end_bars_agree(self):
        """Every captured mission with a single bucket in wlBars sits at
        the index 4 - bars of its `startEndWlBars`, when both ends agree."""
        import json
        import pathlib

        checked = 0
        for name in ("vendor_mission_history.json", "irobot_missionhistory_i3plus.json"):
            data = json.loads(pathlib.Path("tests/fixtures", name).read_text(encoding="utf-8"))
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    ends, bars = node.get("startEndWlBars"), node.get("wlBars")
                    if (
                        isinstance(ends, list) and len(ends) == 2 and ends[0] == ends[1]
                        and isinstance(bars, list) and len(bars) == 5
                        and sorted(bars)[-2] == 0 and any(bars)
                    ):
                        assert bars.index(max(bars)) == 4 - ends[0], (ends, bars)
                        checked += 1
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
        assert checked >= 5


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
    _mission_elapsed_value,
    _mop_behavior,
    _mop_clean_mode,
    _mop_tank_status,
    _phase_value,
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

    def test_a_bin_empty_on_the_dock_is_not_running(self):
        """4.5.0: a dock command to a docked robot empties the bin with
        `cycle` evac while `phase` alternates charge and run. Read as run
        it said Running and fired *Cleaning started*."""
        for phase in ("run", "charge"):
            e = _FakeEntity({"cleanMissionStatus": {"phase": phase, "cycle": "evac"}, "batPct": 100})
            assert _phase_value(e) == "emptying_bin", phase

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
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from custom_components.roomba_plus import switch as sw
import datetime as _dt
from custom_components.roomba_plus import sensor_helpers as sh


class TestMissionElapsedValue:
    def test_no_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {}})
        assert _mission_elapsed_value(e) is None

    def test_zero_timestamp_returns_none(self):
        e = _FakeEntity({"cleanMissionStatus": {"mssnStrtTm": 0}})
        assert _mission_elapsed_value(e) is None

    RUNNING = {"cycle": "clean", "phase": "run"}

    def test_recent_start_returns_positive(self):
        ts = int(time.time()) - 300  # 5 minutes ago
        e = _FakeEntity({"cleanMissionStatus": {**self.RUNNING, "mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert result is not None
        assert result >= 4.9  # at least ~5 min
        assert result < 10    # sanity check

    def test_returns_float(self):
        ts = int(time.time()) - 60
        e = _FakeEntity({"cleanMissionStatus": {**self.RUNNING, "mssnStrtTm": ts}})
        result = _mission_elapsed_value(e)
        assert isinstance(result, float)

    def test_start_time_kept_after_the_mission_is_not_elapsed_time(self):
        """nareso: docked after a mission, the sensor read 748 min -- the
        time since the previous start. The firmware keeps mssnStrtTm."""
        ts = int(time.time()) - 748 * 60
        e = _FakeEntity({"cleanMissionStatus": {
            "cycle": "none", "phase": "charge", "mssnStrtTm": ts,
        }})
        assert _mission_elapsed_value(e) is None


# ── ERROR_CODE_LABELS ─────────────────────────────────────────────────────────





class TestTheRobotsOwnAbortHistory:
    """`bbpause.pauses` is a rolling ten-entry list of what stopped the
    last ten runs, and nothing read it.

    @utkjmitch's Y351020 carried four docking failures in ten runs while
    he experienced each as a one-off. The pattern was in every
    diagnostics download he had ever sent.

    Values below are real, from captures on both generations.
    """

    @staticmethod
    def _read(bbpause):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import (
            recent_pause_reasons,
        )

        entity = MagicMock()
        entity.vacuum_state = {"bbpause": bbpause}
        return recent_pause_reasons(entity)

    def test_the_flat_shape(self):
        """@utkjmitch's Y351020."""
        assert self._read(
            {"pauses": [1010, 1010, 46, 1010, 1010, 46, 46, 33, 33, 48]}
        ) == [1010, 1010, 46, 1010, 1010, 46, 46, 33, 33, 48]

    def test_the_nested_shape(self):
        """@chairstacker's G185020 nests it, and carries both. Two
        shapes from the same week, so neither is 'the' correct one."""
        assert self._read(
            {"bbpause": {"pauses": [29, -1]},
             "pauses": [48, 48, 48, 48, 1, 48, 48, 48, 48, 48]}
        ) == [48, 48, 48, 48, 1, 48, 48, 48, 48, 48]

    def test_nested_only(self):
        assert self._read({"bbpause": {"pauses": [29, 46]}}) == [29, 46]

    def test_placeholders_are_dropped(self):
        """-1 fills an unused slot; reporting it as an abort reason
        would invent a failure."""
        assert self._read({"pauses": [29, -1, -1]}) == [29]

    def test_a_robot_without_the_field(self):
        assert self._read(None) == []


class TestTheWireTableTravelsBetweenFamilies:
    """Ruby and lewis assign different SUBSETS of one shared value
    space. They do not disagree.

    This was briefly recorded as a divergence, because the
    hardware-mismatch state sits on wire 78 in ruby and wire 40 in
    lewis. A value-by-value comparison showed the opposite: every wire
    value present in both means the same thing, and the differences are
    values one family uses and the other never emits.

    THE STRONGER ARGUMENT IS ARCHITECTURAL. The iRobot app has one
    `RobotReadinessState` enum and no branch on codename or SKU in the
    readiness path -- one binary for i7, S9+, j9 and m6. It cannot read
    a wire value differently per family, so either the values are
    family-independent or iRobot's own app is wrong on half its fleet.

    `soho` and `sanmarino` have never been read and cannot be: no
    firmware image of either exists.
    """

    def test_the_two_family_specific_values_do_not_collide(self):
        """40 (lewis) and 78 (ruby) are two different states: the Classic
        app files 40 as NewCleaningHead, 78 is CleaningHeadHwMismatch."""
        from custom_components.roomba_plus.const import (
            READINESS_WIRE_TO_INDEX,
        )

        assert READINESS_WIRE_TO_INDEX[78] != READINESS_WIRE_TO_INDEX[40]

    def test_the_shared_values_are_the_bulk_of_the_table(self):
        """33 wire values appear in both firmwares and agree in every
        one. That agreement is what makes the table portable."""
        from custom_components.roomba_plus.const import (
            READINESS_WIRE_TO_INDEX,
        )

        shared = {
            1, 2, 3, 4, 6, 7, 10, 15, 16, 18, 21, 22, 23, 24, 26, 28,
            29, 31, 33, 34, 35, 36, 37, 38, 39, 51, 57, 66, 67, 68, 69, 72,
        }
        assert shared <= set(READINESS_WIRE_TO_INDEX)

    def test_lewis_only_values_are_carried(self):
        """25 and 32 exist in lewis and not in ruby. Adding them cannot
        break a ruby robot, which never sends them."""
        assert self_value_is(25, "bumper_offline")
        assert self_value_is(32, "lid_open")


def self_value_is(not_ready, expected):
    """Helper: what the readiness sensor reports for a wire value."""
    from unittest.mock import MagicMock

    from custom_components.roomba_plus.sensor_helpers import (
        _not_ready_value,
    )

    entity = MagicMock()
    entity.clean_mission_status = {"notReady": not_ready}
    return _not_ready_value(entity) == expected


class TestSixtyEightIsSettledThreeWaysOver:
    """The value that cost two field reports, confirmed from three
    independent directions.

        firmware constant   `LOADING_MAP`
        app enum index      [64] `DownloadingMap`
        app display string  `history_start_refuse_68` = "Map was
                            unavailable"

    Three artefacts, three extraction methods, one answer. It is not
    "Off dock" -- that is wire 69, `DRC_OFF_DOCK`.

    @Thonno's i7+ and @ScenicSystemsLLC's S9+ both reported it while
    docked and charging, and the old formula named it one place short.
    """

    def test_the_map_state_and_the_dock_state_are_different_values(self):
        from custom_components.roomba_plus.const import (
            READINESS_STATE_LABELS,
            READINESS_WIRE_TO_INDEX,
        )

        assert READINESS_STATE_LABELS[READINESS_WIRE_TO_INDEX[68]] == (
            "Downloading map"
        )
        assert READINESS_STATE_LABELS[READINESS_WIRE_TO_INDEX[69]] == "Off dock"

    def test_wire_forty_is_the_new_cleaning_head(self):
        """Read from lewis as `HARDWARE_MISMATCH`, shown by the app as
        "Software update required" -- and both make sense now that the
        app's own refusal table files 40 as NewCleaningHead: "New
        cleaning head detected. Please update the software." (4.5.0)."""
        from custom_components.roomba_plus.const import (
            READINESS_STATE_LABELS,
            READINESS_WIRE_TO_INDEX,
        )

        assert READINESS_STATE_LABELS[READINESS_WIRE_TO_INDEX[40]] == "New cleaning head"


# ── formerly tests/test_coverage_mid_gaps_4.py ──────────────────────────────────
#
# Coverage gaps, fourth batch — quality scale, test-coverage.
#
# switch.py: the Classic setting switches only react to their own key, and
# the Prime switches read cloud shadows that may be absent. A switch whose
# source is missing shows unknown, never a guessed on/off.

def _ent(state=None, *, mission_store="unset", maintenance_store="unset", hr=100, options=None, **rt):
    e = SimpleNamespace(vacuum_state=state or {}, run_stats={"hr": hr})
    e._config_entry = MagicMock()
    e._config_entry.options = options or {}
    runtime = e._config_entry.runtime_data
    if mission_store != "unset":
        runtime.mission_store = mission_store
    if maintenance_store != "unset":
        runtime.maintenance_store = maintenance_store
    for k, v in rt.items():
        setattr(runtime, k, v)
    return e


class TestModeLabels:

    @pytest.mark.parametrize("state,label", [
        ({}, "n-a"),
        ({"vacHigh": False, "carpetBoost": True}, "auto"),
        ({"vacHigh": True, "carpetBoost": False}, "performance"),
        ({"vacHigh": False, "carpetBoost": False}, "eco"),
    ])
    def test_carpet_boost(self, state, label):
        assert sh._carpet_boost_mode(_ent(state)) == sh.CARPET_BOOST_LABELS[label]

    @pytest.mark.parametrize("state,label", [
        ({}, "n-a"),
        ({"noAutoPasses": True, "twoPass": True}, "two"),
        ({"noAutoPasses": True, "twoPass": False}, "one"),
        ({"noAutoPasses": False, "twoPass": False}, "auto"),
    ])
    def test_clean_mode(self, state, label):
        assert sh._clean_mode(_ent(state)) == sh.CLEAN_MODE_LABELS[label]

    @pytest.mark.parametrize("raw,mode", [({"disposable": "x"}, "unknown"), ({}, "unknown"), (1, "dry")])
    def test_mop_mode_without_a_readable_level_is_unknown(self, raw, mode):
        assert sh._mop_clean_mode(_ent({"padWetness": raw})) == mode


class TestTimeHelpers:

    def test_an_impossible_timestamp_is_none(self):
        assert sh._ts_or_none(10**20) is None
        assert sh._ts_or_none(0) is None

    def test_a_naive_start_time_is_read_as_utc(self):
        store = MagicMock()
        store.latest.return_value = {"started_at": "2026-09-21T07:00:00"}
        value = sh._mission_store_last_started_at(_ent(mission_store=store))
        assert value.tzinfo is not None and value.hour == 7

    def test_an_unparseable_start_time_is_none(self):
        store = MagicMock()
        store.latest.return_value = {"started_at": object()}
        assert sh._mission_store_last_started_at(_ent(mission_store=store)) is None

    def test_no_last_error_time_is_none(self):
        assert sh._last_error_at_value(_ent(last_error_at="")) is None
        assert sh._last_error_at_value(_ent(last_error_at="2026-09-21T07:00:00+00:00")).year == 2026


class TestConsumableHelpers:

    def test_without_stores_there_is_no_rate_or_life(self):
        e = _ent(mission_store=None, maintenance_store=None)
        assert sh._consumable_wear_rate(e, "filter") is None
        assert sh._consumable_max_hours(e, "filter") is None

    def test_the_wear_rate_counts_with_the_remembered_meter(self):
        """@tankdeer: no hours since the reconnect is not hour 0."""
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        maint = MaintenanceStore(filter_reset_hr=290)
        maint.note_hours(300)
        store = MagicMock()
        sh._consumable_wear_rate(_ent(mission_store=store, maintenance_store=maint, hr=0), "filter")
        assert store.wear_rate_since_reset.call_args.args[2] == 300

    def test_days_until_due_falls_back_to_local_hours(self):
        """Without a cloud figure the local remaining hours decide."""
        maint = MagicMock()
        maint.role_remaining_hours.return_value = 20
        maint.reset_baseline_for_role.return_value = (0, None)
        store = MagicMock()
        store.wear_rate_since_reset.return_value = 2.0
        e = _ent(mission_store=store, maintenance_store=maint)
        assert sh._filter_days_until_due(e) == 10
        assert sh._brush_days_until_due(e) == 10

    def test_no_wear_yet_means_no_due_date(self):
        store = MagicMock()
        store.wear_rate_since_reset.return_value = 0
        maint = MagicMock()
        maint.reset_baseline_for_role.return_value = (0, None)
        assert sh._consumable_days_until_due(_ent(mission_store=store, maintenance_store=maint), "filter") is None


class TestStoreBackedHelpers:

    def test_a_failing_store_reader_gives_none(self):
        assert sh._mission_store_value(_ent(mission_store=MagicMock()), MagicMock(side_effect=KeyError)) is None
        assert sh._mission_store_value(_ent(mission_store=None), lambda s: 1) is None

    def test_no_presence_windows_means_no_utilisation(self):
        store = MagicMock()
        store.presence_windows.return_value = []
        assert sh._presence_utilisation(_ent(mission_store=store), 14) is None

    def test_too_few_windows_give_no_likely_window(self):
        store = MagicMock()
        store.presence_windows.return_value = [SimpleNamespace(started_at=_dt.datetime(2026, 9, 1, 9))] * 2
        assert sh._next_likely_clean_window(_ent(mission_store=store)) is None

    def test_the_most_common_hour_is_the_likely_window(self):
        store = MagicMock()
        store.presence_windows.return_value = [
            SimpleNamespace(started_at=_dt.datetime(2026, 9, d, 10)) for d in (1, 2, 3)
        ] + [SimpleNamespace(started_at=_dt.datetime(2026, 9, 4, 18))]
        assert sh._next_likely_clean_window(_ent(mission_store=store)).hour == 10


class TestNetinfoAddress:

    @pytest.mark.parametrize("addr,erwartet", [
        (None, None), ("", None), ("10.0.0.9", "10.0.0.9"),
        (0x0A000009, "10.0.0.9"),
        (-1, None), (2**40, None),       # outside an IPv4 address
        (True, None), ([10, 0, 0, 9], None),
    ])
    def test_only_a_valid_address_is_shown(self, addr, erwartet):
        assert sh._parse_netinfo_addr(addr) == erwartet


class TestEnergyWithoutAStore:

    def test_without_the_profile_store_the_raw_figure_is_shown(self, monkeypatch):
        monkeypatch.setattr(sh, "_estcap_to_mah", lambda _e: 3000)
        monkeypatch.setattr(sh, "active_charge_cycles", lambda _s: 100)
        e = _ent(robot_profile=None, robot_profile_store=None)
        e.battery_stats = {}
        assert sh._total_energy_consumed_kwh(e) == round(3000 * 14.8 * 100 / 1_000_000, 3)


class TestIrobotsArticleAndTheAppsWordsOnTheStatusSensors:
    """4.5.0b4: *Error – Last code* carried iRobot's article for the
    error since b2, the sensor people look at did not; and readiness
    named a refusal by the firmware's word ("SM bus permanent failure")
    where the app says what to do. Placeholder URLs, keyed as the help
    catalogue is (parts_catalog.help_key)."""

    CATALOGUE = {
        "error2": "https://example.com/error-2",
        "start-refuse7": "https://example.com/start-refuse-7",
    }

    def _entity(self, status, *, language="de"):
        entity = MagicMock()
        entity.clean_mission_status = status
        entity.vacuum_state = {"sku": "R980020"}
        entity.vacuum.error_message = None
        entity._config_entry.title = "Robbie"
        entity._config_entry.runtime_data.help_catalogue = dict(self.CATALOGUE)
        entity.hass.config.language = language
        return entity

    @staticmethod
    def _attrs(key, entity):
        description = next(d for d in SENSORS if d.key == key)
        return description.extra_attributes_fn(entity)

    def test_the_error_sensor_links_the_article_while_it_shows_the_error(self):
        running = self._entity({"cycle": "clean", "phase": "run", "error": 2})
        assert self._attrs("error", running)["help_url"] == "https://example.com/error-2"

    def test_no_link_for_an_error_the_state_does_not_show(self):
        """Docked after a failure the firmware keeps the code; the state
        is empty, and so is the link."""
        docked = self._entity({"cycle": "none", "phase": "charge", "error": 2})
        assert "help_url" not in self._attrs("error", docked)
        unknown = self._entity({"cycle": "clean", "phase": "run", "error": 3})
        assert "help_url" not in self._attrs("error", unknown)

    def test_readiness_says_what_the_app_says_and_links_the_article(self):
        attrs = self._attrs("readiness", self._entity({"notReady": 7}))
        assert attrs == {
            "app_label": "Der Auffangbehälter von Robbie fehlte.",
            "description": "Setzen Sie den Auffangbehälter Ihres Roboters wieder ein.",
            "text_source": "classic_app",
            "help_url": "https://example.com/start-refuse-7",
        }

    def test_the_text_is_keyed_by_the_wire_value(self):
        """Wire 37 is the battery: "SM bus permanent failure" by the
        firmware's name, "battery is not detected" by the app's."""
        attrs = self._attrs("readiness", self._entity({"notReady": 37}, language="en"))
        assert attrs["description"] == "Robbie's battery is not detected."
        assert "help_url" not in attrs  # no article in this catalogue

    def test_ready_or_odd_values_carry_nothing(self):
        for raw in (0, None, "7", True, 99999):
            assert self._attrs("readiness", self._entity({"notReady": raw})) == {}, raw
