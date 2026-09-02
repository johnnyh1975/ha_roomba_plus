"""`sensor_core.py` -- the RoombaSensor entity and the mission-store
sensors built on it.

Started as a regression file for @chairstacker's imperial-units bug
(#69); five classes moved in from test_sensors.py in August 2026, where
they had been testing this module all along under the facade's name.

`pytest` is imported at module level because `@pytest.mark.asyncio` is a
decorator and cannot be imported inside a method.
"""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

class TestAreaSensorsCanBeConverted:
    """@chairstacker (#69): his Home Assistant is on imperial and his
    iRobot app shows square feet, but the area sensors read square
    metres regardless.

    The unit was declared and the value converted correctly — HA just
    had nothing telling it what kind of quantity this is. Without
    `device_class`, it displays the native unit as-is and the user's
    unit system is ignored.
    """

    def test_every_area_sensor_declares_its_device_class(self):
        from homeassistant.components.sensor import SensorDeviceClass
        from homeassistant.const import UnitOfArea

        from custom_components.roomba_plus.sensor_core import SENSORS

        offenders = [
            d.key for d in SENSORS
            if d.native_unit_of_measurement in (
                UnitOfArea.SQUARE_METERS, UnitOfArea.SQUARE_FEET,
            )
            and d.device_class is not SensorDeviceClass.AREA
        ]

        assert not offenders, (
            f"{offenders} report an area without device_class=AREA, so "
            f"Home Assistant cannot convert them to the user's unit system"
        )

    def test_there_are_area_sensors_to_check(self):
        from homeassistant.const import UnitOfArea

        from custom_components.roomba_plus.sensor_core import SENSORS

        found = [
            d.key for d in SENSORS
            if d.native_unit_of_measurement in (
                UnitOfArea.SQUARE_METERS, UnitOfArea.SQUARE_FEET,
            )
        ]

        assert len(found) >= 2

    def test_total_cleaned_area_declares_total_increasing(self):
        from homeassistant.components.sensor import SensorStateClass

        from custom_components.roomba_plus.sensor_core import SENSORS

        desc = next(d for d in SENSORS if d.key == "total_cleaned_area")
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING


class TestCountsAreNotMeasurements:
    """@chairstacker's graph showed `9.89725` for `clean_streak` — a
    count of days.

    `MEASUREMENT` tells Home Assistant the value is continuous, so it
    interpolates between samples when drawing. Every point on that line
    is a number the sensor never reported, and it makes a working
    counter look broken.
    """

    COUNTS = ("clean_streak", "consecutive_clean_skips")

    def test_they_are_not_measurements(self):
        from homeassistant.components.sensor import SensorStateClass

        from custom_components.roomba_plus.sensor_core import SENSORS

        offenders = [
            d.key for d in SENSORS
            if d.key in self.COUNTS
            and d.state_class is SensorStateClass.MEASUREMENT
        ]

        assert not offenders, (
            f"{offenders} count things -- MEASUREMENT makes Home "
            f"Assistant draw a line between samples and invent values "
            f"in between"
        )

    def test_they_exist_to_be_checked(self):
        from custom_components.roomba_plus.sensor_core import SENSORS

        keys = {d.key for d in SENSORS}

        assert set(self.COUNTS) <= keys


# ============================================================================
# MISSION-STORE SENSORS
#
# Moved here from test_sensors.py (August 2026). Both classes import
# `RoombaSensor` from sensor_core directly -- they test this module, not
# the sensor.py facade, and neither used any of test_sensors.py's
# module-level helpers.
# ============================================================================


class TestMissionStoreSensorsRefreshOnTheStore:
    """@scenicsystemsllc: sensors do not refresh across rapid
    back-to-back missions. He flagged it as a hypothesis — source
    consistent, no captured payload — and proposed that a fast second
    mission could arrive on a delta omitting `cleanMissionStatus`.

    There is a second source-consistent mechanism: these sensors read
    from `mission_store`, while `new_state_filter` gates them on the
    delta. Mission-end processing is debounced (two consecutive signals
    plus a minimum hold on ambiguous phases), so the delta that triggers
    a recompute can arrive before the record exists. With a gap the next
    delta cleans it up; back to back, the next delta belongs to the next
    mission.

    Telling the two apart needs a captured payload. Listening for
    `EVENT_MISSION_COMPLETED` — fired *after* the store write — makes
    both moot, because the refresh trigger and the value source finally
    agree.
    """

    def test_the_store_backed_sensors_are_listed(self):
        from custom_components.roomba_plus.sensor_core import (
            _MISSION_STORE_SENSORS,
        )

        assert "last_mission_result" in _MISSION_STORE_SENSORS
        assert "area_cleaned_today" in _MISSION_STORE_SENSORS
        assert "clean_streak" in _MISSION_STORE_SENSORS

    def test_a_delta_only_sensor_is_not_listed(self):
        """`battery` reads the delta it is woken by. Subscribing it to
        mission-end would be a wasted state write per mission."""
        from custom_components.roomba_plus.sensor_core import (
            _MISSION_STORE_SENSORS,
        )

        assert "battery" not in _MISSION_STORE_SENSORS
        assert "phase" not in _MISSION_STORE_SENSORS

    def test_the_handler_filters_by_entry(self):
        """One household can hold several robots, and a mission ending
        on one says nothing about the others' counters."""
        import inspect

        from custom_components.roomba_plus.sensor_core import RoombaSensor

        source = inspect.getsource(RoombaSensor._async_mission_completed)

        assert "entry_id" in source
        assert "return" in source

    def test_it_forces_a_recompute(self):
        """A plain state write would re-publish the cached value — the
        point is to read the store again."""
        import inspect

        from custom_components.roomba_plus.sensor_core import RoombaSensor

        source = inspect.getsource(RoombaSensor._async_mission_completed)

        assert "force_refresh=True" in source


class TestTheMissionStoreKeysExist:
    """`_MISSION_STORE_SENSORS` is a hand-written list of keys, and a
    typo in it fails silently: the listener simply never fires for that
    sensor, and nothing goes red.

    That failure mode has turned up repeatedly in this project — a
    hand-kept list drifting from the thing it names. This makes it loud.
    """

    def test_every_key_names_a_real_sensor(self):
        from custom_components.roomba_plus.sensor_core import (
            _MISSION_STORE_SENSORS,
            SENSORS,
        )

        invented = _MISSION_STORE_SENSORS - {d.key for d in SENSORS}

        assert not invented, (
            f"keys that name no sensor: {sorted(invented)} -- the "
            "mission-end listener would never fire for these"
        )


# ============================================================================
# THE RoombaSensor ENTITY ITSELF -- value, availability, countdown tick.
#
# Moved here from test_sensors.py (August 2026). All three import
# `RoombaSensor` from sensor_core and carry their own per-class helpers
# (`self._sensor`, `self._available`, `self._async_tick`), so nothing
# came with them.
#
# They were never facade tests. They test this module's entity class,
# and sat in the file named for the facade purely because that is where
# sensor tests had always gone.
# ============================================================================


class TestRoombaSensorNativeValue:
    """`RoombaSensor` is the base class most Classic sensors inherit
    from, and it sat at ~32% coverage -- the lowest in the integration.

    The consumable branches below are the ones users act on: a wrong
    "filter remaining" number sends someone to buy a part they do not
    need, or lets a worn one keep running. They also carry a fallback
    that is easy to get backwards, because the maintenance store is
    optional and the arithmetic differs between the two paths."""

    def _sensor(self, key, *, run_stats=None, options=None, store=None):
        from custom_components.roomba_plus.sensor_core import RoombaSensor, SENSORS

        sensor = object.__new__(RoombaSensor)
        entry = MagicMock()
        entry.options = options or {}
        entry.runtime_data.maintenance_store = store
        sensor._config_entry = entry
        sensor.entity_description = next(d for d in SENSORS if d.key == key)
        type(sensor).run_stats = PropertyMock(return_value=run_stats or {})
        return sensor

    def test_filter_hours_fall_back_to_plain_arithmetic_without_a_store(self):
        sensor = self._sensor(
            "filter_remaining_hours", run_stats={"hr": 30}, options={"filter_threshold_hours": 100}
        )

        assert sensor.native_value == 70

    def test_filter_hours_never_go_negative(self):
        """A robot past its threshold must read zero, not a negative
        number -- the sensor is 'remaining', and negative remaining is
        not a thing a user can act on."""
        sensor = self._sensor(
            "filter_remaining_hours", run_stats={"hr": 250}, options={"filter_threshold_hours": 100}
        )

        assert sensor.native_value == 0

    def test_the_maintenance_store_takes_precedence_when_present(self):
        """The store knows about resets after a part was replaced; raw
        arithmetic does not. Preferring the store is the whole reason it
        exists."""
        store = MagicMock()
        # No cloud counter for this part — this test is about the local
        # threshold/store fallback, which only runs when the cloud has none.
        store.cloud_remaining_hours.return_value = None
        store.remaining_hours.return_value = 42

        sensor = self._sensor(
            "filter_remaining_hours", run_stats={"hr": 30},
            options={"filter_threshold_hours": 100}, store=store,
        )

        assert sensor.native_value == 42
        store.remaining_hours.assert_called_once_with("filter", 30, 100)

    def test_brush_hours_use_their_own_threshold_and_role(self):
        """Filter and brush wear at different rates and are replaced
        independently -- crossing the two would be silently wrong."""
        store = MagicMock()
        # As above: local-path test, so no cloud counter.
        store.cloud_remaining_hours.return_value = None
        store.remaining_hours.return_value = 11

        sensor = self._sensor(
            "brush_remaining_hours", run_stats={"hr": 60},
            options={"brush_threshold_hours": 200}, store=store,
        )

        assert sensor.native_value == 11
        store.remaining_hours.assert_called_once_with("main_brush", 60, 200)

    def test_missing_runtime_hours_are_treated_as_zero(self):
        """A freshly connected robot has no 'hr' yet; the sensor must
        show the full threshold rather than crashing."""
        sensor = self._sensor(
            "filter_remaining_hours", run_stats={}, options={"filter_threshold_hours": 100}
        )

        assert sensor.native_value == 100


class TestRoombaSensorMaxHoursAttribute:
    """max_hours in extra_state_attributes: cloud full-life hours when a
    cloud record exists, else the learned-or-hardcoded/configured
    threshold for every role alike."""

    def _sensor(self, key, *, options=None, store=None):
        from custom_components.roomba_plus.sensor_core import SENSORS, RoombaSensor

        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = next(d for d in SENSORS if d.key == key)
        entry = MagicMock()
        entry.options = options or {}
        entry.runtime_data.maintenance_store = store
        sensor._config_entry = entry
        return sensor

    def test_filter_remaining_hours_max_hours_falls_back_to_local_threshold(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        sensor = self._sensor(
            "filter_remaining_hours",
            options={"filter_threshold_hours": 80},
            store=MaintenanceStore(),
        )
        attrs = sensor.extra_state_attributes
        assert attrs["threshold_hours"] == 80
        assert attrs["max_hours"] == 80

    def test_filter_remaining_hours_max_hours_prefers_cloud_full_life(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([
            {"part_id": "35", "count_used": 600, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
        ], 500)
        sensor = self._sensor(
            "filter_remaining_hours", options={"filter_threshold_hours": 80}, store=store,
        )
        attrs = sensor.extra_state_attributes
        assert attrs["max_hours"] == 60  # (600+3000)/60, overrides the 80h configured threshold

    def test_part_edge_brush_falls_back_to_hardcoded_threshold_without_cloud_data(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        sensor = self._sensor("part_edge_brush", store=MaintenanceStore())
        attrs = sensor.extra_state_attributes
        assert attrs["threshold_hours"] == 150
        assert attrs["max_hours"] == 150

    def test_part_edge_brush_exposes_max_hours_with_cloud_data(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([
            {"part_id": "36", "count_used": 600, "count_remaining": 3000,
             "count_type": "minutes", "last_updated_ts": 1700000000},
        ], 500)
        sensor = self._sensor("part_edge_brush", store=store)
        attrs = sensor.extra_state_attributes
        assert attrs["max_hours"] == 60
        assert attrs["threshold_hours"] == 150

    def test_part_dirt_bag_falls_back_to_hardcoded_threshold_without_cloud_data(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        sensor = self._sensor("part_dirt_bag", store=MaintenanceStore())
        attrs = sensor.extra_state_attributes
        assert attrs["threshold_hours"] == 30
        assert attrs["max_hours"] == 30

    def test_part_dirt_bag_exposes_max_hours_with_cloud_data(self):
        from custom_components.roomba_plus.maintenance_store import MaintenanceStore

        store = MaintenanceStore()
        store.hydrate_from_cloud_parts([
            {"part_id": "139", "count_used": 1200, "count_remaining": 2400,
             "count_type": "minutes", "last_updated_ts": 1700000000},
        ], 500)
        sensor = self._sensor("part_dirt_bag", store=store)
        attrs = sensor.extra_state_attributes
        assert attrs["max_hours"] == 60
        assert attrs["threshold_hours"] == 30


class TestNewConsumableSensorDescriptors:
    """side_brush/clean_base_bag get the same wear-rate/days-until-due
    descriptor shape filter/main_brush already have."""

    def test_all_four_wear_rate_and_days_until_due_keys_present(self):
        from custom_components.roomba_plus.sensor_core import SENSORS

        keys = {d.key for d in SENSORS}
        for key in (
            "side_brush_wear_rate", "side_brush_days_until_due",
            "clean_base_bag_wear_rate", "clean_base_bag_days_until_due",
        ):
            assert key in keys, f"Missing sensor key: {key}"

    def test_new_descriptors_are_gated_on_robot_capability(self):
        from custom_components.roomba_plus.sensor_core import SENSORS

        for key in (
            "side_brush_wear_rate", "side_brush_days_until_due",
            "clean_base_bag_wear_rate", "clean_base_bag_days_until_due",
        ):
            desc = next(d for d in SENSORS if d.key == key)
            assert desc.filter_fn is not None
            assert desc.translation_key == key


class TestRoombaSensorCountdownTick:
    """The 60-second tick for the recharge/expire countdown sensors.

    Why it exists at all: the firmware sends `rechrgTm`/`expireTm` ONCE
    when recharging starts and pushes nothing further while charging.
    Without a tick the sensor freezes at its first reading, which looks
    like a broken sensor rather than a quiet robot.

    Two details here are easy to undo by accident, and both are
    recorded in the code:

    - it must use `schedule_update_ha_state(force_refresh=True)`, not
      `async_write_ha_state()` -- the latter is a no-op when Home
      Assistant believes the value has not changed, which is exactly
      the case a countdown needs to break out of;
    - the interval must be cancelled on removal, or it keeps firing
      against a dead entity for the lifetime of the process."""

    def _sensor(self, key):
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        sensor = object.__new__(RoombaSensor)
        sensor.entity_description = MagicMock(key=key)
        sensor._unsub_tick = None
        sensor.hass = MagicMock()
        return sensor

    def test_only_the_countdown_sensors_are_ticked(self):
        """Every Classic sensor inherits this class. Ticking all of them
        every minute would be pointless load."""
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        assert RoombaSensor._TICK_SENSORS == {
            "mission_recharge_minutes", "mission_expire_minutes",
        }

    @pytest.mark.asyncio
    async def test_a_countdown_sensor_registers_an_interval(self):
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        sensor = self._sensor("mission_recharge_minutes")

        with patch.object(RoombaSensor.__bases__[0], "async_added_to_hass", AsyncMock()), \
             patch("custom_components.roomba_plus.sensor_core.async_track_time_interval") as track:
            await sensor.async_added_to_hass()

        track.assert_called_once()
        assert track.call_args.args[2].total_seconds() == 60

    @pytest.mark.asyncio
    async def test_an_ordinary_sensor_registers_nothing(self):
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        sensor = self._sensor("battery")

        with patch.object(RoombaSensor.__bases__[0], "async_added_to_hass", AsyncMock()), \
             patch("custom_components.roomba_plus.sensor_core.async_track_time_interval") as track:
            await sensor.async_added_to_hass()

        track.assert_not_called()

    @pytest.mark.asyncio
    async def test_removal_cancels_the_interval(self):
        sensor = self._sensor("mission_recharge_minutes")
        unsub = MagicMock()
        sensor._unsub_tick = unsub

        await sensor.async_will_remove_from_hass()

        unsub.assert_called_once()
        assert sensor._unsub_tick is None

    @pytest.mark.asyncio
    async def test_removing_a_sensor_that_never_ticked_is_safe(self):
        sensor = self._sensor("battery")

        await sensor.async_will_remove_from_hass()

        assert sensor._unsub_tick is None

    def test_the_tick_forces_a_refresh_rather_than_writing_state(self):
        """THE detail that makes the countdown work. async_write_ha_state()
        is deduplicated by HA when the value looks unchanged -- which is
        precisely the situation a countdown has to break out of."""
        sensor = self._sensor("mission_recharge_minutes")
        sensor.schedule_update_ha_state = MagicMock()
        sensor.async_write_ha_state = MagicMock()

        sensor._async_tick(None)

        sensor.schedule_update_ha_state.assert_called_once_with(force_refresh=True)
        sensor.async_write_ha_state.assert_not_called()


class TestRoombaSensorAvailability:
    """`available_fn` lets a description declare "not applicable right
    now" -- a bin-full sensor on a robot with no bin fitted, say.

    Worth testing because the fallback direction is easy to invert: an
    entity wrongly reported unavailable disappears from dashboards and
    breaks automations that reference it, and the cause is far from
    obvious to whoever finds it.

    Uses a subclass rather than patching: `available` is defined on
    Home Assistant's own Entity via a cached-property mechanism that
    does not tolerate being patched, and fighting that would test the
    mock rather than the code."""

    def _available(self, available_fn, parent_available=True):
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        class _Probe(RoombaSensor):
            @property
            def available(self):
                # Same logic path, with a parent whose answer we control.
                if self.entity_description.available_fn is not None:
                    if not self.entity_description.available_fn(self):
                        return False
                return parent_available

        sensor = object.__new__(_Probe)
        sensor.entity_description = MagicMock(available_fn=available_fn)
        return sensor.available

    def test_no_available_fn_defers_entirely_to_the_parent(self):
        assert self._available(None, parent_available=True) is True
        assert self._available(None, parent_available=False) is False

    def test_available_fn_returning_false_wins_over_a_healthy_parent(self):
        """This is the whole point: the connection can be fine while a
        particular sensor still has nothing meaningful to report."""
        assert self._available(lambda _self: False, parent_available=True) is False

    def test_available_fn_returning_true_still_defers_to_the_parent(self):
        """It can veto, not override -- a disconnected robot must not be
        reported as available just because one description says so."""
        assert self._available(lambda _self: True, parent_available=False) is False

    def test_the_real_property_has_the_same_shape(self):
        """Guards the probe above from drifting away from the code it
        stands in for."""
        import inspect

        from custom_components.roomba_plus.sensor_core import RoombaSensor

        source = inspect.getsource(RoombaSensor.available.fget)
        assert "available_fn" in source
        assert "return super().available" in source


class TestClassicStatusReportsTheSameTwoStates:
    """The Classic side of the same pair Prime got.

    Both generations must report the same STATES; they deliberately
    spell them differently, because Classic translates and Prime uses
    the robot's own words that templates match on.
    """

    @staticmethod
    def _status(phase, cycle="none", battery=50, last_ts=None):
        import time
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import _phase_value

        entity = MagicMock()
        entity.clean_mission_status = {"phase": phase, "cycle": cycle}
        entity.vacuum_state = {"batPct": battery}
        entity._config_entry.runtime_data.last_mqtt_message_ts = (
            time.time() if last_ts is None else last_ts
        )
        return _phase_value(entity)

    def test_charging_mid_mission(self):
        """Plain "charging" would be indistinguishable from a finished
        mission, and an automation on it fires mid-clean."""
        assert self._status("charge", cycle="clean") == "charging_mid_mission"

    def test_charging_between_runs_is_plain_charging(self):
        assert self._status("charge", cycle="none") == "charging"

    def test_a_full_battery_still_reads_idle(self):
        """The existing rule must survive: charge at 100% is Idle."""
        assert self._status("charge", cycle="none", battery=100) == "idle"

    def test_silence_overrides_the_frozen_phase(self):
        import time

        value = self._status("stuck", last_ts=time.time() - 9 * 86400)

        assert value == "no_contact"
        assert value != "stuck"

    def test_a_brief_gap_does_not_trigger_it(self):
        """A dropout must not rewrite the status mid-mission."""
        import time

        assert self._status(
            "run", cycle="clean", last_ts=time.time() - 300
        ) == "running"


class TestErrorValueReturnsRealNone:
    """HA renders the literal string "None" as a displayed state instead of
    the entity going unavailable/unknown — _error_value must return the
    Python object None, never the string "None".
    """

    @staticmethod
    def _error(cycle, phase, error=0, error_message=None):
        from custom_components.roomba_plus.sensor_helpers import _error_value

        entity = MagicMock()
        entity.clean_mission_status = {"cycle": cycle, "phase": phase, "error": error}
        entity.vacuum.error_message = error_message
        return _error_value(entity)

    def test_suppressed_stale_error_is_none(self):
        result = self._error("none", "charge", error=17)
        assert result is None
        assert result != "None"

    def test_no_error_fallback_is_none(self):
        result = self._error("clean", "run", error=0, error_message=None)
        assert result is None
        assert result != "None"

class _FakeEntity:
    def __init__(self, vacuum_state=None, clean_mission_status=None):
        self.vacuum_state = vacuum_state or {}
        self.clean_mission_status = clean_mission_status or {}


def _descriptor(key):
    from custom_components.roomba_plus.sensor_core import SENSORS

    return next(d for d in SENSORS if d.key == key)


class TestEnumSensorsReturnTranslationReadySlugs:
    """A Polish install must see "Gotowy", not "Ready" -- but the sensor
    can only supply a slug for HA to translate, not the English word
    itself. Each case below pairs a raw robot/cloud value with the slug
    its descriptor's value_fn must produce.
    """

    def test_job_initiator_maps_known_and_unknown_values(self):
        value_fn = _descriptor("job_initiator").value_fn

        assert value_fn(_FakeEntity(clean_mission_status={"initiator": "schedule"})) == "schedule"
        assert value_fn(_FakeEntity(clean_mission_status={"initiator": "rmtApp"})) == "remote_app"
        assert value_fn(_FakeEntity(clean_mission_status={"initiator": "dockBtn"})) == "dock_button"
        assert value_fn(_FakeEntity(clean_mission_status={})) == "none"

    def test_clean_base_status_maps_dock_state_codes(self):
        value_fn = _descriptor("clean_base_status").value_fn

        assert value_fn(_FakeEntity(vacuum_state={"dock": {"state": 300}})) == "ready"
        assert value_fn(_FakeEntity(vacuum_state={"dock": {"state": 353}})) == "bag_full"
        assert value_fn(_FakeEntity(vacuum_state={})) == "not_available"

    def test_mop_pad_maps_detected_pad_including_the_lowercase_variant(self):
        value_fn = _descriptor("mop_pad").value_fn

        assert value_fn(_FakeEntity(vacuum_state={"detectedPad": "reusableWet"})) == "reusable_wet"
        # reusablewet (all-lowercase) is the Braava jet m6's own spelling
        # of reusableWet -- same pad, different case.
        assert value_fn(_FakeEntity(vacuum_state={"detectedPad": "reusablewet"})) == "reusable_wet"
        assert value_fn(_FakeEntity(vacuum_state={"detectedPad": "padPlate"})) == "plate_fitted"
        assert value_fn(_FakeEntity(vacuum_state={})) == "no_pad"

    def test_mop_behavior_legacy_maps_rank_overlap(self):
        value_fn = _descriptor("mop_behavior").value_fn

        assert value_fn(_FakeEntity(vacuum_state={"rankOverlap": 67})) == "standard"
        assert value_fn(_FakeEntity(vacuum_state={"rankOverlap": 15})) == "no_mop"
        assert value_fn(_FakeEntity(vacuum_state={"rankOverlap": 999})) == "unknown"

    def test_clean_mode_derives_a_slug_from_the_two_bit_flags(self):
        value_fn = _descriptor("clean_mode").value_fn

        assert value_fn(_FakeEntity(vacuum_state={"noAutoPasses": True, "twoPass": True})) == "two_passes"
        assert value_fn(_FakeEntity(vacuum_state={"noAutoPasses": True, "twoPass": False})) == "one_pass"
        assert value_fn(_FakeEntity(vacuum_state={"noAutoPasses": False, "twoPass": False})) == "auto"
        assert value_fn(_FakeEntity(vacuum_state={})) == "not_available"

    def test_carpet_boost_mode_derives_a_slug_from_vac_high_and_carpet_boost(self):
        value_fn = _descriptor("carpet_boost_mode").value_fn

        assert value_fn(_FakeEntity(vacuum_state={"carpetBoost": 1, "vacHigh": 0})) == "auto"
        assert value_fn(_FakeEntity(vacuum_state={"carpetBoost": 0, "vacHigh": 1})) == "performance"
        assert value_fn(_FakeEntity(vacuum_state={"carpetBoost": 0, "vacHigh": 0})) == "eco"
        assert value_fn(_FakeEntity(vacuum_state={})) == "not_available"

    def test_every_slug_is_a_legal_ha_state_key(self):
        """hassfest requires translation_key state keys to match
        [a-z0-9_]+ -- no spaces, dashes, or uppercase."""
        import re

        from custom_components.roomba_plus.const import (
            CARPET_BOOST_SLUGS,
            CLEAN_BASE_STATUS_SLUGS,
            CLEAN_MODE_SLUGS,
            JOB_INITIATOR_SLUGS,
            MOP_BEHAVIOR_SLUGS,
            MOP_PAD_SLUGS,
        )

        legal = re.compile(r"[a-z0-9_]+")
        offenders = [
            slug
            for mapping in (
                CARPET_BOOST_SLUGS, CLEAN_BASE_STATUS_SLUGS, CLEAN_MODE_SLUGS,
                JOB_INITIATOR_SLUGS, MOP_BEHAVIOR_SLUGS, MOP_PAD_SLUGS,
            )
            for slug in mapping.values()
            if not legal.fullmatch(slug)
        ]

        assert not offenders



class TestPoseCapabilityVersusActualPose:
    """`cap.pose` is a compile-time constant on lewis firmware. It says
    nothing about whether a robot publishes a position.

    Seven robots showed a perfect split — 1 reports, 2 does not — which
    read like causation and is a correlation with model generation. The
    integration treated `cap.pose >= 1` as "reports position" and was
    wrong on every one of them, costing three weeks on @pk-1966's i7 and
    surfacing again on @ScenicSystemsLLC's three robots across two more
    firmware families.
    """

    def test_the_declaration_is_not_the_fact(self):
        from custom_components.roomba_plus.const import (
            has_pose,
            reports_local_pose,
        )

        # An S9+ on soho: claims the capability, sends nothing.
        state = {"cap": {"pose": 2}}

        assert has_pose(state)
        assert not reports_local_pose(state)

    def test_an_arriving_pose_is_what_counts(self):
        from custom_components.roomba_plus.const import reports_local_pose

        assert reports_local_pose({"pose": {"theta": 0, "point": {"x": 1, "y": 2}}})

    def test_nav_quality_gates_on_its_own_field(self):
        """An R980040 reports `cap.pose: 1` and has no `mssnNavStats` —
        55 state keys, that one absent. Gating on the flag created a
        sensor that could never have a value."""
        from custom_components.roomba_plus.sensor_core import (
            SENSORS,
        )

        nav = next(
            d for d in SENSORS if d.key == "nav_quality"
        )

        assert nav.filter_fn is not None
        assert not nav.filter_fn({"cap": {"pose": 1}})
        assert nav.filter_fn({"mssnNavStats": {"l_squal": 50}})
