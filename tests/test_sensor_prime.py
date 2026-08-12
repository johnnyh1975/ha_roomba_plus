"""Tests for sensor_prime.py -- the CLOUD_ONLY (V4/Prime) sensors.

See that module's own docstring for why these are separate, minimal
entity classes rather than routed through the existing SENSORS/
RoombaSensor machinery (sensor_core.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.const import ERROR_CODE_LABELS
from custom_components.roomba_plus.models import ConnectionType
from custom_components.roomba_plus.sensor_prime import (
    PrimeBatterySensor,
    PrimeConnectionHealthSensor,
    PrimeDetectedPadSensor,
    PrimeDockStatusSensor,
    PrimeMissionEventSensor,
    PrimePadDryStatusSensor,
    PrimePadWashStatusSensor,
    PrimeRuntimeHoursSensor,
    PrimeSuctionLevelSensor,
    _dock_state_label,
)


def _make_settings_config_entry(rw_settings: dict | None = None) -> MagicMock:
    """For the RobotSettings-backed sensors (suction_level) -- a
    separate named shadow (rw-settings) from the CurrentStateShadow-
    backed sensors above."""
    config_entry = MagicMock()
    config_entry.runtime_data.prime_status_coordinator.data = (
        {"rw-settings": rw_settings} if rw_settings is not None else None
    )
    return config_entry


def _make_status_config_entry(ro_currentstate: dict | None = None) -> MagicMock:
    """For the CurrentStateShadow-backed sensors (battery/detected_pad/
    runtime_hours) -- a separate coordinator attribute
    (prime_status_coordinator) from PrimeMissionEventSensor's own
    prime_coordinator, see prime_coordinator.py's own docstring for why."""
    config_entry = MagicMock()
    config_entry.runtime_data.prime_status_coordinator.data = (
        {"ro-currentstate": ro_currentstate} if ro_currentstate is not None else None
    )
    return config_entry


class TestPrimeBatterySensor:
    def test_native_value_none_when_no_coordinator_data_yet(self):
        config_entry = _make_status_config_entry()
        sensor = PrimeBatterySensor("BLID123", config_entry)

        assert sensor.native_value is None

    def test_native_value_reflects_real_captured_bat_pct(self):
        """Uses chairstacker's own real captured value (72), not an
        arbitrary placeholder."""
        config_entry = _make_status_config_entry({"batPct": 72})
        sensor = PrimeBatterySensor("BLID123", config_entry)

        assert sensor.native_value == 72


class TestPrimeDetectedPadSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"detectedPad": "padPlate"})
        sensor = PrimeDetectedPadSensor("BLID123", config_entry)

        # SLUG, not the wire value. Home Assistant requires
        # [a-z0-9-_]+ for translated ENUM states, so  is
        # mapped to  before publishing.
        assert sensor.native_value == "pad_plate"


class TestPrimeRuntimeHoursSensor:
    def test_native_value_and_minutes_attribute(self):
        config_entry = _make_status_config_entry({"runtimeStats": {"hr": 44, "min": 44}})
        sensor = PrimeRuntimeHoursSensor("BLID123", config_entry)

        assert sensor.native_value == 44
        assert sensor.extra_state_attributes == {"minutes": 44}


def _make_mission_timeline_report(event_type: str, **event_kwargs):
    from roombapy_prime.models import MissionTimelineEvent, MissionTimelineReport

    event_data = {"type": event_type, "ts": 1, **event_kwargs}
    return MissionTimelineReport(
        mission_id="m1", event=[MissionTimelineEvent.from_json(event_data)],
    )


def _make_config_entry() -> MagicMock:
    config_entry = MagicMock()
    config_entry.runtime_data.prime_coordinator.data = None
    config_entry.runtime_data.prime_coordinator.last_update_success = True
    config_entry.runtime_data.prime_coordinator.last_exception = None
    return config_entry


class TestPrimeMissionEventSensor:
    def test_unique_id_and_object_id(self):
        config_entry = _make_config_entry()
        sensor = PrimeMissionEventSensor("BLID123", config_entry)

        assert sensor.unique_id == "roomba_plus_BLID123_prime_mission_event"
        assert sensor.suggested_object_id == "prime_mission_event"

    def test_native_value_none_when_no_coordinator_data_yet(self):
        config_entry = _make_config_entry()
        sensor = PrimeMissionEventSensor("BLID123", config_entry)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_native_value_reflects_current_event_type(self):
        config_entry = _make_config_entry()
        config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report("room", room={"rid": "11", "area": 354, "passCount": 2})
        sensor = PrimeMissionEventSensor("BLID123", config_entry)

        assert sensor.native_value == "room"
        attrs = sensor.extra_state_attributes
        assert attrs["mission_id"] == "m1"
        assert attrs["current_room_id"] == "11"
        assert attrs["current_room_area"] == 354
        assert attrs["current_room_pass_count"] == 2

    def test_start_event_has_no_room_attributes(self):
        """The "start" event carries no nested room/travel sub-object at
        all -- must not crash, must simply omit the room-specific keys."""
        config_entry = _make_config_entry()
        config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report("start")
        sensor = PrimeMissionEventSensor("BLID123", config_entry)

        assert sensor.native_value == "start"
        attrs = sensor.extra_state_attributes
        assert attrs["mission_id"] == "m1"
        assert "current_room_id" not in attrs

    def test_no_config_entry_does_not_crash(self):
        sensor = PrimeMissionEventSensor("BLID123", None)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    @pytest.mark.asyncio
    async def test_async_added_to_hass_registers_coordinator_listener(self):
        from unittest.mock import patch

        config_entry = _make_config_entry()
        sensor = PrimeMissionEventSensor("BLID123", config_entry)
        sensor.hass = MagicMock()
        with patch.object(sensor, "_async_update_device_name", new=AsyncMock()):
            with patch.object(sensor, "schedule_update_ha_state") as mock_schedule:
                await sensor.async_added_to_hass()

                config_entry.runtime_data.prime_coordinator.async_add_listener.assert_called_once_with(
                    mock_schedule
                )


class TestPrimeConnectionHealthSensor:
    def test_unique_id_and_object_id(self):
        config_entry = _make_config_entry()
        sensor = PrimeConnectionHealthSensor("BLID123", config_entry)

        assert sensor.unique_id == "roomba_plus_BLID123_prime_connection_health"
        assert sensor.suggested_object_id == "prime_connection_health"

    def test_native_value_ok_when_no_coordinator_yet(self):
        config_entry = _make_config_entry()
        config_entry.runtime_data.prime_coordinator = None
        sensor = PrimeConnectionHealthSensor("BLID123", config_entry)

        assert sensor.native_value == "ok"
        assert sensor.extra_state_attributes == {}

    def test_native_value_ok_when_last_update_succeeded(self):
        config_entry = _make_config_entry()
        sensor = PrimeConnectionHealthSensor("BLID123", config_entry)

        assert sensor.native_value == "ok"

    def test_native_value_error_when_last_update_failed(self):
        config_entry = _make_config_entry()
        config_entry.runtime_data.prime_coordinator.last_update_success = False
        config_entry.runtime_data.prime_coordinator.last_exception = RuntimeError("connection dropped")
        sensor = PrimeConnectionHealthSensor("BLID123", config_entry)

        assert sensor.native_value == "error"
        assert sensor.extra_state_attributes == {"last_error": "connection dropped"}

    def test_no_config_entry_does_not_crash(self):
        sensor = PrimeConnectionHealthSensor("BLID123", None)

        assert sensor.native_value == "ok"
        assert sensor.extra_state_attributes == {}


class TestAsyncSetupEntryCloudOnlyBranch:
    """async_setup_entry()'s early CLOUD_ONLY return -- must add exactly
    the two Prime sensors and skip every classic-path code entirely
    (SENSORS/RoombaSensor, cloud-history, edge-coverage, etc.), since
    those are built on roomba_reported_state()/cloud_coordinator, a
    completely different data source for a CLOUD_ONLY entry."""

    @pytest.mark.asyncio
    async def test_adds_every_prime_sensor(self):
        from custom_components.roomba_plus import sensor as sensor_mod
        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.sensor_prime import (
            PrimeBatterySensor,
            PrimeCanceledMissionsSensor,
            PrimeChargeCyclesErrorSensor,
            PrimeChargeCyclesOkSensor,
            PrimeDetectedPadSensor,
            PrimeDockStatusSensor,
            PrimeErrorSensor,
            PrimeFailedMissionsSensor,
            PrimeFirmwareVersionSensor,
            PrimeNavigationResetsSensor,
            PrimePadDryStatusSensor,
            PrimePadWashStatusSensor,
            PrimeRuntimeHoursSensor,
            PrimeSerialNumberSensor,
            PrimeSuccessfulMissionsSensor,
            PrimeSuctionLevelSensor,
            PrimeSystemUptimeSensor,
            PrimeTotalMissionsSensor,
        )

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        created = []

        def sync_add(entities, **kw):
            created.extend(entities)

        await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        # 6 always-present + 4 capability-gated (unknown -> created by
        # default) + 8 ro-stats-backed + 1 ro-configinfo-backed + 1
        # error sensor (this session) = 20.
        # 31 = 20 Prime-specific + 6 mission-history + 4 maintenance-date
        # + 1 mission-progress sensor,
        # the latter added once MissionStore was filled for Prime.
        #
        # A count assertion looks brittle and earns its keep: it fired
        # the moment the mission sensors were wired in, which is the
        # point at which you confirm the addition was deliberate rather
        # than an accident of a shared code path.
        assert len(created) == 34, (
            "phase and readiness were added: the most basic state sensor "
            "there is plus the only place a Prime robot says why it will "
            "not start"
        )
        assert any(isinstance(e, PrimeMissionEventSensor) for e in created)
        assert any(isinstance(e, PrimeConnectionHealthSensor) for e in created)
        assert any(isinstance(e, PrimeBatterySensor) for e in created)
        assert any(isinstance(e, PrimeDetectedPadSensor) for e in created)
        assert any(isinstance(e, PrimeRuntimeHoursSensor) for e in created)
        assert any(isinstance(e, PrimeFirmwareVersionSensor) for e in created)
        assert any(isinstance(e, PrimeDockStatusSensor) for e in created)
        assert any(isinstance(e, PrimePadWashStatusSensor) for e in created)
        assert any(isinstance(e, PrimePadDryStatusSensor) for e in created)
        assert any(isinstance(e, PrimeSuctionLevelSensor) for e in created)
        assert any(isinstance(e, PrimeTotalMissionsSensor) for e in created)
        assert any(isinstance(e, PrimeSuccessfulMissionsSensor) for e in created)
        assert any(isinstance(e, PrimeCanceledMissionsSensor) for e in created)
        assert any(isinstance(e, PrimeFailedMissionsSensor) for e in created)
        assert any(isinstance(e, PrimeChargeCyclesOkSensor) for e in created)
        assert any(isinstance(e, PrimeChargeCyclesErrorSensor) for e in created)
        assert any(isinstance(e, PrimeSystemUptimeSensor) for e in created)
        assert any(isinstance(e, PrimeNavigationResetsSensor) for e in created)
        assert any(isinstance(e, PrimeSerialNumberSensor) for e in created)
        assert any(isinstance(e, PrimeErrorSensor) for e in created)


class TestAsyncSetupEntryCapabilityGating:
    """NEW (this session) -- the four capability-gated sensors are
    excluded when the classic/unnamed shadow's cap explicitly reports
    0 (confirmed-negative pattern, see get_prime_capability_flags()'s
    own docstring) -- complements the test above, which covers the
    "capability unknown -> create anyway" default."""

    def _entry_with_cap(self, cap: dict, dock_cap: dict | None = None):
        from custom_components.roomba_plus.prime_coordinator import PrimeStatusCoordinator

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        classic_reported: dict = {"cap": cap}
        current_state_reported: dict = {"dock": {"cap": dock_cap}} if dock_cap is not None else {}
        entry.runtime_data.prime_status_coordinator.data = {
            PrimeStatusCoordinator.CLASSIC_SHADOW_KEY: classic_reported,
            "ro-currentstate": current_state_reported,
        }
        return entry

    @pytest.mark.asyncio
    async def test_scrub_zero_excludes_pad_and_suction_but_not_others(self):
        from custom_components.roomba_plus import sensor as sensor_mod
        from custom_components.roomba_plus.sensor_prime import PrimeDetectedPadSensor

        entry = self._entry_with_cap({"scrub": 0, "suctionLvl": 0})
        created = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e, **kw: created.extend(e))

        assert not any(isinstance(e, PrimeDetectedPadSensor) for e in created)
        assert not any(isinstance(e, PrimeSuctionLevelSensor) for e in created)
        assert any(isinstance(e, PrimeBatterySensor) for e in created)
        assert any(isinstance(e, PrimeDockStatusSensor) for e in created)

    @pytest.mark.asyncio
    async def test_nonzero_scrub_includes_pad_sensor(self):
        from custom_components.roomba_plus import sensor as sensor_mod
        from custom_components.roomba_plus.sensor_prime import PrimeDetectedPadSensor

        entry = self._entry_with_cap({"scrub": 3, "suctionLvl": 4})
        created = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e, **kw: created.extend(e))

        assert any(isinstance(e, PrimeDetectedPadSensor) for e in created)
        assert any(isinstance(e, PrimeSuctionLevelSensor) for e in created)

    @pytest.mark.asyncio
    async def test_an_absent_cap_keeps_the_entities(self):
        """THE CONTRACT, OBSERVED. None means unknown -- the shadow has
        not arrived -- and only an explicit 0 means absent. A robot that
        has not reported its capabilities yet must get its entities
        rather than lose them.

        This replaces two assertions that looked for
        `cap is None or cap.scrub != 0` in the source. Those passed on
        any spelling of the expression and failed on a reformat, and the
        behaviour tests beside them already covered 0 and non-zero --
        the None case, which is the half that once cost a bug, was
        covered by neither."""
        from custom_components.roomba_plus import sensor as sensor_mod

        created: list = []
        await sensor_mod.async_setup_entry(
            MagicMock(), self._entry_with_cap(None),
            lambda e, **kw: created.extend(e),
        )
        keys = {
            getattr(e.entity_description, "key", "") for e in created
        }

        assert "prime_detected_pad" in keys
        assert "prime_suction_level" in keys or "suction_level" in keys
    @pytest.mark.asyncio
    async def test_dock_cap_zero_excludes_pad_wash_and_dry(self):
        from custom_components.roomba_plus import sensor as sensor_mod
        from custom_components.roomba_plus.sensor_prime import (
            PrimePadDryStatusSensor, PrimePadWashStatusSensor,
        )

        entry = self._entry_with_cap({"scrub": 3}, dock_cap={"pw": 0, "pd": 0})
        created = []
        await sensor_mod.async_setup_entry(MagicMock(), entry, lambda e, **kw: created.extend(e))

        assert not any(isinstance(e, PrimePadWashStatusSensor) for e in created)
        assert not any(isinstance(e, PrimePadDryStatusSensor) for e in created)


class TestDockStateLabel:
    def test_confirmed_real_captured_values(self):
        """Uses chairstacker's own real captured values (301/601/701),
        confirming the dock/pad-wash/pad-dry status labels resolve to
        the real, named DockState members, not just bare numbers."""
        assert _dock_state_label(301) == "Dock ready"
        assert _dock_state_label(601) == "Pad wash okay"
        assert _dock_state_label(701) == "Pad dry okay"

    def test_unrecognized_value_does_not_crash(self):
        """DockState has 86 confirmed values -- an out-of-range value
        (a server-side addition this library doesn't know about yet)
        must degrade gracefully, not raise."""
        assert _dock_state_label(99999) == "Unknown (99999)"

    def test_none_returns_none(self):
        assert _dock_state_label(None) is None


class TestPrimeDockStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"state": 301}})
        sensor = PrimeDockStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "Dock ready"


class TestPrimePadWashStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"pwState": 601}})
        sensor = PrimePadWashStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "Pad wash okay"


class TestPrimePadDryStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"pdState": 701}})
        sensor = PrimePadDryStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "Pad dry okay"


class TestPrimeSuctionLevelSensor:
    def test_native_value_none_when_no_coordinator_data_yet(self):
        config_entry = _make_settings_config_entry()
        sensor = PrimeSuctionLevelSensor("BLID123", config_entry)

        assert sensor.native_value is None

    def test_native_value_resolves_confirmed_enum_member(self):
        config_entry = _make_settings_config_entry({"suctionLevel": 3})
        sensor = PrimeSuctionLevelSensor("BLID123", config_entry)

        assert sensor.native_value == "high"


def _make_stats_config_entry(ro_stats: dict | None = None) -> MagicMock:
    """For the StatsShadow-backed sensors (mission stats, charge
    cycles, uptime, nav resets) -- confirmed with REAL VALUES this
    session (chairstacker's raw_shadows.json), see StatsShadow's own
    docstring."""
    config_entry = MagicMock()
    config_entry.runtime_data.prime_status_coordinator.data = (
        {"ro-stats": ro_stats} if ro_stats is not None else None
    )
    return config_entry


def _make_configinfo_config_entry(ro_configinfo: dict | None = None) -> MagicMock:
    config_entry = MagicMock()
    config_entry.runtime_data.prime_status_coordinator.data = (
        {"ro-configinfo": ro_configinfo} if ro_configinfo is not None else None
    )
    return config_entry


# Real bbmssn capture (chairstacker) -- sums exactly, cross-validated
# against ro-currentstate's own cleanMissionStatus.nMssn in the same session.
_REAL_BBMSSN = {"nMssn": 276, "nMssnC": 25, "nMssnF": 4, "nMssnOk": 247}


class TestPrimeMissionStatsSensors:
    """NEW (this session) -- four sensors reusing Classic's own
    translation_keys, since StatsShadow.bbmssn's fields are confirmed
    identical to what Classic's own equivalent sensors read."""

    def test_total_missions(self):
        from custom_components.roomba_plus.sensor_prime import PrimeTotalMissionsSensor

        config_entry = _make_stats_config_entry({"bbmssn": _REAL_BBMSSN})
        sensor = PrimeTotalMissionsSensor("BLID123", config_entry)
        assert sensor.native_value == 276

    def test_successful_missions(self):
        from custom_components.roomba_plus.sensor_prime import PrimeSuccessfulMissionsSensor

        config_entry = _make_stats_config_entry({"bbmssn": _REAL_BBMSSN})
        sensor = PrimeSuccessfulMissionsSensor("BLID123", config_entry)
        assert sensor.native_value == 247

    def test_canceled_missions(self):
        from custom_components.roomba_plus.sensor_prime import PrimeCanceledMissionsSensor

        config_entry = _make_stats_config_entry({"bbmssn": _REAL_BBMSSN})
        sensor = PrimeCanceledMissionsSensor("BLID123", config_entry)
        assert sensor.native_value == 25

    def test_failed_missions(self):
        from custom_components.roomba_plus.sensor_prime import PrimeFailedMissionsSensor

        config_entry = _make_stats_config_entry({"bbmssn": _REAL_BBMSSN})
        sensor = PrimeFailedMissionsSensor("BLID123", config_entry)
        assert sensor.native_value == 4

    def test_counts_sum_to_total_internal_consistency_check(self):
        """The same cross-validation that confirmed these are genuine
        lifetime counters, not arbitrary numbers, expressed as a test.

        IMPORTANT, per a real field observation (chairstacker): this
        sum holds only while the robot is IDLE. Mid-mission the total
        is one HIGHER, because n_mssn increments at mission START while
        the outcome counters increment at mission END. _REAL_BBMSSN is
        a capture from an idle robot, so the assertion is valid here --
        but this is NOT a general invariant and must not be treated as
        one elsewhere. See PrimeTotalMissionsSensor's own docstring."""
        assert _REAL_BBMSSN["nMssnC"] + _REAL_BBMSSN["nMssnF"] + _REAL_BBMSSN["nMssnOk"] == _REAL_BBMSSN["nMssn"]

    def test_none_when_no_coordinator_data_yet(self):
        from custom_components.roomba_plus.sensor_prime import PrimeTotalMissionsSensor

        config_entry = _make_stats_config_entry(None)
        sensor = PrimeTotalMissionsSensor("BLID123", config_entry)
        assert sensor.native_value is None


class TestPrimeChargeCycleSensors:
    """NEW (this session) -- new translation keys, NOT the same
    concept as Classic's own battery_cycles (see each class's own
    docstring for why)."""

    def test_charge_cycles_ok(self):
        from custom_components.roomba_plus.sensor_prime import PrimeChargeCyclesOkSensor

        config_entry = _make_stats_config_entry({"bbchg": {"nChgOk": 561, "nChgErr": 0}})
        sensor = PrimeChargeCyclesOkSensor("BLID123", config_entry)
        assert sensor.native_value == 561

    def test_charge_cycles_error(self):
        from custom_components.roomba_plus.sensor_prime import PrimeChargeCyclesErrorSensor

        config_entry = _make_stats_config_entry({"bbchg": {"nChgOk": 561, "nChgErr": 0}})
        sensor = PrimeChargeCyclesErrorSensor("BLID123", config_entry)
        assert sensor.native_value == 0


class TestPrimeSystemUptimeSensor:
    def test_native_value_reflects_real_captured_hours(self):
        from custom_components.roomba_plus.sensor_prime import PrimeSystemUptimeSensor

        config_entry = _make_stats_config_entry({"bbsys": {"hr": 7354, "min": 0}})
        sensor = PrimeSystemUptimeSensor("BLID123", config_entry)
        assert sensor.native_value == 7354


class TestPrimeNavigationResetsSensor:
    def test_native_value_reflects_real_captured_value(self):
        from custom_components.roomba_plus.sensor_prime import PrimeNavigationResetsSensor

        config_entry = _make_stats_config_entry({"bbrstinfo": {"nNavRst": 22}})
        sensor = PrimeNavigationResetsSensor("BLID123", config_entry)
        assert sensor.native_value == 22


class TestPrimeSerialNumberSensor:
    def test_native_value_reflects_real_captured_serial(self):
        from custom_components.roomba_plus.sensor_prime import PrimeSerialNumberSensor

        config_entry = _make_configinfo_config_entry(
            {"hwPartsRev": {"navSerialNo": "G185020H250311N105749"}}
        )
        sensor = PrimeSerialNumberSensor("BLID123", config_entry)
        assert sensor.native_value == "G185020H250311N105749"

    def test_none_when_serial_is_empty_string(self):
        """Most hwPartsRev fields are empty strings in the one real
        capture seen (only nav_serial_no was populated) -- an empty
        string should read as "no data", not a literal empty value."""
        from custom_components.roomba_plus.sensor_prime import PrimeSerialNumberSensor

        config_entry = _make_configinfo_config_entry({"hwPartsRev": {"navSerialNo": ""}})
        sensor = PrimeSerialNumberSensor("BLID123", config_entry)
        assert sensor.native_value is None


class TestPrimeErrorSensor:
    """NEW (this session) -- reuses Classic's ERROR_CODE_LABELS and its
    translation_key, and critically INHERITS Classic's hard-won
    stale-error suppression: cleanMissionStatus.error persists across
    missions (the firmware never resets it to 0 on docking), so a naive
    sensor would show a long-finished error forever while charging."""

    def _entity(self, **status):
        from custom_components.roomba_plus.sensor_prime import PrimeErrorSensor

        config_entry = MagicMock()
        config_entry.runtime_data.prime_status_coordinator.data = {
            "ro-currentstate": {"cleanMissionStatus": status}
        }
        return PrimeErrorSensor("BLID123", config_entry)

    def test_reports_the_code_during_an_active_mission(self):
        """The CODE, not a label. APK analysis found no error-code table
        in the app at all -- for either generation -- and a field
        capture contradicts the Classic reading outright: error 46 on a
        physically stuck robot at 55% battery, which ERROR_CODE_LABELS
        calls "Low battery".

        Same reasoning as consumable parts 202/212: a wrong label gets
        believed, a number invites a question."""
        sensor = self._entity(cycle="clean", phase="run", error=46)

        assert sensor.native_value == "Error 46"

    def test_the_classic_reading_survives_as_a_flagged_attribute(self):
        """Thrown away would be wrong too -- it is probably right more
        often than not. It just must not be asserted, and the attribute
        name has to say so."""
        sensor = self._entity(cycle="clean", phase="run", error=46)

        attrs = sensor.extra_state_attributes
        assert attrs["error_code"] == 46
        assert attrs["classic_label_unconfirmed"] == ERROR_CODE_LABELS[46]

    def test_an_unknown_code_still_reports_something_useful(self):
        """234-style values that map to nothing used to render as
        "None", which reads as "no error" for a robot that has one."""
        sensor = self._entity(cycle="clean", phase="run", error=9999)

        assert sensor.native_value == "Error 9999"
        assert "classic_label_unconfirmed" not in sensor.extra_state_attributes

    def test_suppresses_a_stale_error_while_charging(self):
        """THE trap this sensor exists to avoid -- error 671 still set
        from a finished mission, robot back on the dock."""
        sensor = self._entity(cycle="none", phase="charge", error=671)

        assert sensor.native_value == "None"

    def test_suppresses_stale_error_when_idle_too(self):
        sensor = self._entity(cycle="none", phase="idle", error=671)

        assert sensor.native_value == "None"

    def test_no_error_during_a_mission_reports_none_label(self):
        sensor = self._entity(cycle="clean", phase="run", error=0)

        assert sensor.native_value == "None"

    def test_exposes_readiness_fields_as_attributes(self):
        """A readiness-based START REFUSAL leaves `error` at 0 and puts
        the reasons in cond_not_ready -- see the class docstring."""
        sensor = self._entity(cycle="none", phase="charge", error=0, notReady=8, condNotReady=["binFull"])

        attrs = sensor.extra_state_attributes
        assert attrs["not_ready"] == 8
        assert attrs["cond_not_ready"] == ["binFull"]

    def test_none_when_no_coordinator_data_yet(self):
        from custom_components.roomba_plus.sensor_prime import PrimeErrorSensor

        config_entry = MagicMock()
        config_entry.runtime_data.prime_status_coordinator.data = None
        sensor = PrimeErrorSensor("BLID123", config_entry)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


class TestDetectedPadStatesAreTranslated:
    """`padPlate` and `NoPad` are wire values, not user-facing words.

    @chairstacker ran a deliberate sequence -- both pads on, left off,
    left back, right off, right back -- and the sensor tracked it
    exactly. The sensor was never wrong; only its output was raw.

    THIS ALSO CORRECTED A WRONG CONCLUSION. An earlier report showed
    `padPlate` across two missions where the tester believed no pad was
    fitted, and this project recorded a doubt that the sensor might be
    reporting the mounting plate rather than the pad. It is not. A
    deliberate sequence from one account beat two incidental
    observations from another."""

    def test_the_sensor_declares_its_options(self):
        """Without device_class ENUM and an options list, Home Assistant
        shows the raw value and the state translations are ignored."""
        import inspect

        from custom_components.roomba_plus.sensor_prime import PrimeDetectedPadSensor

        source = inspect.getsource(PrimeDetectedPadSensor)

        assert "SensorDeviceClass.ENUM" in source
        assert '"pad_plate"' in source and '"no_pad"' in source
        # The types too, not just what this project has observed.
        assert '"reusable_wet"' in source and '"disp_dry"' in source

    def test_both_states_are_translated_in_every_locale(self):
        import json
        from pathlib import Path

        base = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for locale_file in sorted((base / "translations").glob("*.json")):
            data = json.loads(locale_file.read_text(encoding="utf-8"))
            states = data["entity"]["sensor"]["prime_detected_pad"]["state"]

            # ALL SEVEN RobotPadCategory values, plus both spellings of
            # "no pad". An ENUM sensor renders anything outside its
            # options list as a raw string, so a robot reporting
            # `reusableWet` would show that word to the user.
            #
            # APK analysis found six type-specific UI strings in the app
            # ("Reusable Wet Mopping Pad attached", ...), so the types
            # exist -- pad-plate robots simply never report them.
            # SLUGS, not wire values. Home Assistant requires
            # [a-z0-9-_]+ for translated ENUM states and rejects
            # camelCase at validation time -- which is how the first
            # attempt at this was caught, in CI, after a release.
            #
            # Both observed spellings of "no pad" collapse onto `no_pad`,
            # which incidentally settles a question nobody could answer:
            # one robot reported `NoPad`, the library's enum says
            # `noPad`.
            assert set(states) >= {
                "pad_plate", "no_pad", "disp_dry", "disp_wet",
                "reusable_dry", "reusable_wet", "invalid",
            }, locale_file.name
            for value in states.values():
                assert value.strip(), locale_file.name

    def test_wire_values_map_onto_declared_options(self):
        """Every wire value the robot can send must land on a slug the
        sensor declares. A value outside the options list renders raw.

        This is the pairing that broke: the map and the list were edited
        separately, and nothing checked they agreed."""
        from custom_components.roomba_plus.sensor_prime import (
            _PAD_STATE_SLUGS,
            PrimeDetectedPadSensor,
        )
        import inspect

        source = inspect.getsource(PrimeDetectedPadSensor)

        for slug in set(_PAD_STATE_SLUGS.values()):
            assert f'"{slug}"' in source, slug

    def test_both_no_pad_spellings_collapse(self):
        """One robot reported `NoPad`, the library's enum says `noPad`,
        and nobody knows which the wire uses. Mapping both onto one slug
        makes the question stop mattering."""
        from custom_components.roomba_plus.sensor_prime import _PAD_STATE_SLUGS

        assert _PAD_STATE_SLUGS["NoPad"] == _PAD_STATE_SLUGS["noPad"] == "no_pad"


class TestDockCapabilityGating:
    """A present `dock.cap` without a key means the dock cannot do it.

    That is different from having no `dock.cap` at all, and the
    difference cost @jouwdan two useless entities: his vacuum-only
    Roomba Max 705 has an evacuation-only dock reporting
    `cap: {"evac": 1}`, and got a pad-wash sensor and a pad-dry sensor.

    His own diagnostics named the reasoning: "created (capability
    unknown -- failing open)". The object was there; the absence of `pw`
    inside it was a statement, not a gap.

    THE FAIL-OPEN CONTRACT IS STILL RIGHT FOR THE ROBOT capabilities,
    where a missing cap object means the shadow has not arrived yet.
    Applying the same rule to a nested object that IS present was the
    mistake."""

    def test_an_evac_only_dock_gets_no_pad_sensors(self):
        import inspect

        from custom_components.roomba_plus import sensor

        source = inspect.getsource(sensor.async_setup_entry)

        # None and 0 both suppress; only a real capability creates.
        assert "pad_wash not in (0, None)" in source
        assert "pad_dry not in (0, None)" in source

    def test_a_missing_dock_cap_still_fails_open(self):
        """A robot whose shadow has not arrived must not silently lose
        entities it should have -- that is the case the contract was
        written for."""
        import inspect

        from custom_components.roomba_plus import sensor

        source = inspect.getsource(sensor.async_setup_entry)

        assert "dock_cap_known" in source
        assert "not dock_cap_known or" in source



class TestCapabilityGatesHandleNone:
    """`None` and `0` mean different things, and the difference has
    already cost one bug.

    THE ROBOT RULE: a missing `cap` object means the shadow has not
    arrived, so entities are created rather than silently lost. Within a
    present cap, every key this project gates on has always arrived with
    a value.

    THE DOCK RULE IS THE OPPOSITE, and that is what broke: @jouwdan's
    evac-only dock reports `cap: {"evac": 1}`. The object is present, so
    "no cap means unknown" does not apply -- the absence of `pw` inside
    it says the dock cannot wash. Failing open gave a vacuum-only robot
    two pad sensors.

    FIVE ROBOT CAPABILITY FIELDS DO ARRIVE AS None on every account seen
    so far -- cmds, e_cmd, mop_lift, odoa, p2maps_editv2_feats. None of
    them currently gates anything, which is why this has not bitten
    twice. This test exists so that adding a gate on one of them is a
    deliberate act."""

    _ALWAYS_NONE = ("cmds", "e_cmd", "mop_lift", "p2maps_editv2_feats")

    def test_no_gate_uses_a_field_that_always_arrives_none(self):
        """These five have been None in every capture. A gate written as
        `cap.mop_lift != 0` would pass for every robot ever made, which
        is not a capability check -- it is a no-op that looks like one."""
        import re
        from pathlib import Path

        root = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for path in root.glob("*.py"):
            code = "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("#")
            )
            for field in self._ALWAYS_NONE:
                assert not re.search(rf"cap\.{field}\s*[!=]=\s*0", code), (
                    f"{path.name}: gating on cap.{field}, which is always None"
                )

    def test_dock_gates_treat_none_as_absent(self):
        import inspect

        from custom_components.roomba_plus import sensor

        source = inspect.getsource(sensor.async_setup_entry)

        for field in ("pad_wash", "pad_dry"):
            assert f"{field} not in (0, None)" in source, field

    def test_robot_gates_treat_none_as_unknown(self):
        """Deliberately the other way round, and it must stay that way:
        a robot whose shadow has not arrived should keep its entities."""
        import inspect

        from custom_components.roomba_plus import sensor

        source = inspect.getsource(sensor.async_setup_entry)

        assert "cap is None or cap.scrub != 0" in source


class TestPrimeCleaningModeSensor:
    """Vacuuming or mopping, as a visible entity.

    @DaRealGuGu asked for this as a vacuum sub-state. VacuumActivity has
    six members and none of them is "mopping", so a vacuum entity
    reporting it would be broken rather than extended -- a sensor is as
    far as this can honestly go, plus the same value as a
    `cleaning_mode` attribute for templates.

    The values were confirmed by his own captures, including one taken
    during the mopping half of a scheduled vacuum-then-mop run:

        2  vacuuming
        4  mopping
        6  both engaged together"""

    def _value(self, cycle, mode):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import (
            PrimeCleaningModeSensor,
        )

        state = MagicMock()
        state.clean_mission_status.cycle = cycle
        state.clean_mission_status.operating_mode = mode

        class _Stub(PrimeCleaningModeSensor):
            _current_state = property(lambda self: state)

        return PrimeCleaningModeSensor.native_value.fget(object.__new__(_Stub))

    def test_mopping(self):
        """The value from the capture that settled this after two
        days."""
        assert self._value("clean", 4) == "mopping"

    def test_vacuuming(self):
        assert self._value("clean", 2) == "vacuuming"

    def test_both_together(self):
        assert self._value("clean", 6) == "vacuuming_and_mopping"

    def test_docked_reports_nothing(self):
        """A docked robot still carries a mode, describing the last or
        next job. Reporting it as current activity is the misreading
        that made an earlier look at this field conclude it never
        moves."""
        assert self._value("none", 2) is None

    def test_command_values_are_not_translated(self):
        """512 asks for vacuum-then-mop and 32 for a combined run. They
        belong to the command table and never appear here -- reading one
        against the other made 6 look impossible for two days."""
        assert self._value("clean", 512) is None
        assert self._value("clean", 32) is None

    def test_the_states_are_slugs_and_translated_everywhere(self):
        import json
        from pathlib import Path

        base = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for locale_file in sorted((base / "translations").glob("*.json")):
            data = json.loads(locale_file.read_text(encoding="utf-8"))
            states = data["entity"]["sensor"]["prime_cleaning_mode"]["state"]

            assert set(states) == {"vacuuming", "mopping", "vacuuming_and_mopping"}, (
                locale_file.name
            )

    def test_it_is_created_for_every_prime_robot(self):
        """No capability gate: every robot vacuums, so the sensor is
        meaningful even on one that cannot mop -- it just never reports
        "mopping". Gating on scrub would hide the vacuum half from
        exactly the robots where it is the only half."""
        import inspect

        from custom_components.roomba_plus import sensor

        source = inspect.getsource(sensor.async_setup_entry)
        line = next(
            line for line in source.splitlines()
            if "PrimeCleaningModeSensor(" in line
        )

        assert "if " not in line


class TestFieldObservedDockStates:
    """@chairstacker's pad wash status read "Unknown (671)" for a
    condition this project can name.

    671 is not in DockState -- the enum's 84 members come from APK
    analysis and the pad-wash family stops at 669. But the meaning is
    established by a controlled before/after: tank removed -> pwState
    671; tank refitted -> 601 (PAD_WASH_OKAY), with dock.state,
    dock.error and pdState unchanged throughout. One field carried the
    whole signal.
    """

    def _label(self, value):
        from custom_components.roomba_plus.sensor_prime import _dock_state_label

        return _dock_state_label(value)

    def test_671_is_named(self):
        assert self._label(671) == "Pad wash not possible (check tanks)"

    def test_it_does_not_say_empty(self):
        """The tank was REMOVED, not empty. "Empty" sends someone to
        refill a tank that is not in the dock -- and 671 was seen in
        both states, so neither word is right on its own."""
        assert "empty" not in self._label(671).lower()

    def test_enum_values_still_win(self):
        """The overlay must not shadow the decompiled enum."""
        assert self._label(601) == "Pad wash okay"
        assert self._label(301) == "Dock ready"

    def test_an_unknown_code_still_falls_back(self):
        assert self._label(9999) == "Unknown (9999)"

    def test_the_enum_is_not_polluted(self):
        """DockState stays purely APK-derived, so the next reader can
        tell decompiled values from field-inferred ones."""
        from roombapy_prime.models.robot_info import DockState

        with pytest.raises(ValueError):
            DockState(671)


class TestTheDockTankLevelSensorGatesOnPresence:
    """Two docks, one capture each: fwVer 24 / dock.cap.pd 3 reports
    `tankLvl`, fwVer 20 / pd 2 never sends the key -- not even while a
    pad wash was failing for want of water.

    Two variables differ at once, so the field cannot say which governs,
    and the APK cannot either: pd/pw/pwo are not literals, the mapping
    is a runtime-filled map<string, DockCapability>, and DockCapability
    is purely categorical with no notion of a level 2 or 3.

    Hence presence, not capability. The dock that stays silent produces
    no entity rather than one reading "unknown" forever -- the lesson
    from @jouwdan's vacuum-only Max 705, which got a pad-wash sensor it
    could never populate.
    """

    async def _sensors(self, dock):
        import importlib
        from unittest.mock import MagicMock

        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        shadows = dict(prime_fixtures.SHADOWS)
        shadows["ro-currentstate"] = dict(prime_fixtures.CURRENT_STATE) | {"dock": dock}
        entry.runtime_data.prime_status_coordinator.data = shadows
        entry.runtime_data.prime_coordinator.data = shadows

        module = importlib.import_module("custom_components.roomba_plus.sensor")
        created: list = []
        await module.async_setup_entry(MagicMock(), entry, created.extend)
        return [type(e).__name__ for e in created]

    @pytest.mark.asyncio
    async def test_a_dock_that_reports_the_level_gets_a_sensor(self):
        names = await self._sensors(
            {"known": True, "tankLvl": 100, "cap": {"pd": 3, "pw": 1}}
        )

        assert "PrimeDockTankLevelSensor" in names

    @pytest.mark.asyncio
    async def test_a_dock_that_never_reports_it_gets_none(self):
        """The silent dock still has pad wash and pad dry -- so this is
        not "no dock", it is a dock that does not publish the level."""
        names = await self._sensors(
            {"known": True, "cap": {"pd": 2, "pw": 1}, "pwState": 671}
        )

        assert "PrimeDockTankLevelSensor" not in names
        assert "PrimePadWashStatusSensor" in names

    @pytest.mark.asyncio
    async def test_an_empty_tank_still_gets_a_sensor(self):
        """0 is a reading. Gating on truthiness would hide the sensor
        exactly when it matters most."""
        names = await self._sensors(
            {"known": True, "tankLvl": 0, "cap": {"pd": 3, "pw": 1}}
        )

        assert "PrimeDockTankLevelSensor" in names


class TestNoDockEntitiesWithoutADock:
    """@utkjmitch's Y351020 sits on a plain charge dock and reports
    `dock: {"known": false, "error": 0, "fwVer": ""}` -- no `cap` object
    at all.

    A missing cap was read as "the shadow has not arrived, fail open",
    which is right when the shadow really is incomplete and wrong here:
    `known: false` is the robot stating there is no such dock. It
    produced pad wash and pad dry sensors on a robot that can do
    neither.

    Same family as the a17 Max 705 fix, different trigger -- there the
    cap object was present and the key absent, here the object never
    comes.
    """

    async def _sensors(self, dock):
        import importlib
        from unittest.mock import MagicMock

        from tests import prime_fixtures

        entry = prime_fixtures.cloud_only_config_entry()
        shadows = dict(prime_fixtures.SHADOWS)
        shadows["ro-currentstate"] = dict(prime_fixtures.CURRENT_STATE) | {"dock": dock}
        entry.runtime_data.prime_status_coordinator.data = shadows
        entry.runtime_data.prime_coordinator.data = shadows

        module = importlib.import_module("custom_components.roomba_plus.sensor")
        created: list = []
        await module.async_setup_entry(MagicMock(), entry, created.extend)
        return [type(e).__name__ for e in created]

    @pytest.mark.asyncio
    async def test_a_plain_charge_dock_gets_no_wash_or_dry_sensors(self):
        names = await self._sensors({"known": False, "error": 0, "fwVer": ""})

        assert "PrimePadWashStatusSensor" not in names
        assert "PrimePadDryStatusSensor" not in names

    @pytest.mark.asyncio
    async def test_a_wash_dock_still_gets_them(self):
        names = await self._sensors(
            {"known": True, "cap": {"pw": 1, "pd": 3}, "tankLvl": 90}
        )

        assert "PrimePadWashStatusSensor" in names
        assert "PrimePadDryStatusSensor" in names

    @pytest.mark.asyncio
    async def test_an_absent_dock_key_still_fails_open(self):
        """No `known` field at all is a genuine gap -- the shadow may
        simply not have arrived yet. Only an explicit false suppresses."""
        names = await self._sensors({"cap": {"pw": 1, "pd": 3}})

        assert "PrimePadWashStatusSensor" in names


class TestErrorSeverityFromTheVendorsOwnConfig:
    """The Prime error sensor shows a raw number on purpose: iRobot gives
    Prime and Classic different help articles for the same code, so no
    label of ours would be sourced.

    A severity bucket is not a label. It is iRobot's own classification,
    from the app's `error_allowed_modes` config, and it answers the one
    question a bare number cannot: is this serious.
    """

    def _severity(self, code):
        from custom_components.roomba_plus.const import PRIME_ERROR_SEVERITY

        return PRIME_ERROR_SEVERITY.get(code)

    def test_the_table_covers_the_vendor_list(self):
        from custom_components.roomba_plus.const import PRIME_ERROR_SEVERITY

        assert len(PRIME_ERROR_SEVERITY) == 171

    def test_the_urgent_bucket_holds_the_ones_you_would_expect(self):
        """68 is the camera fault this project mislabelled as "Updating
        map" until a22, and 266 is an expired subscription."""
        for code in (68, 114, 115, 266):
            assert self._severity(code)[0] == "p2", code

    def test_workaroundable_errors_all_allow_something(self):
        """Every `standard` code carries a non-zero mask -- that is what
        makes the bucket coherent rather than a name."""
        from custom_components.roomba_plus.const import PRIME_ERROR_SEVERITY

        standard = [c for c, (b, _m) in PRIME_ERROR_SEVERITY.items() if b == "standard"]
        assert standard
        for code in standard:
            assert PRIME_ERROR_SEVERITY[code][1] != 0, code

    def test_671_says_the_robot_can_still_work(self):
        """Pad wash blocked, and the dock's own text is "switched to
        vacuum only"."""
        bucket, modes = self._severity(671)

        assert bucket == "standard"
        assert modes != 0

    def test_the_vendors_spelling_is_kept(self):
        """`maintanance`, their typo. Normalising it would break a future
        diff against their own data."""
        from custom_components.roomba_plus.const import PRIME_ERROR_SEVERITY

        assert any(b == "maintanance" for b, _m in PRIME_ERROR_SEVERITY.values())

    def test_an_unlisted_code_yields_nothing_rather_than_a_default(self):
        """Absent from the vendor's list is not the same as harmless."""
        assert self._severity(99999) is None

    def test_the_bitmask_is_not_decoded(self):
        """Bin-full reads 3 and pad-wash-blocked reads 5, which rules out
        the obvious vacuum/mop reading. Inventing a bit layout to print a
        prettier attribute is how this project has been wrong before."""
        assert self._severity(36)[1] == 3
        assert self._severity(671)[1] == 5


class TestEveryCountTypeTheAppKnows:
    """`RobotHealthCountType` has exactly seven values, from the app's
    own enum: Minutes, Missions, ComboMissions, Evacs, Battery,
    PadWashesUsed, Sqft.

    Five had appeared in catalogue responses. Battery and Sqft had not,
    and were added anyway -- one line each against a sensor that would
    otherwise show a bare number the day a robot reports one.
    """

    def _units(self):
        from custom_components.roomba_plus.sensor_prime import _PART_COUNT_UNITS

        return _PART_COUNT_UNITS

    def test_all_seven_are_covered(self):
        units = self._units()
        for wire in ("minutes", "missions", "combo_missions", "evacs",
                     "battery", "pad_washes_used", "sqft"):
            assert wire in units, wire

    def test_pad_washes_is_the_confirmed_name(self):
        """`PadWashesUsed` in the enum confirms what part 202 reports --
        the part this project deliberately named after its counter
        rather than guessing which component it belongs to."""
        assert self._units()["pad_washes_used"] == "pad washes"

    def test_an_unknown_type_gets_no_unit(self):
        """A wrong unit on a number is worse than none: it invites
        arithmetic that does not hold."""
        assert self._units().get("furlongs") is None


class TestThePhaseOptionsMatchTheVendorEnum:
    """A phase outside `options` makes Home Assistant reject the value
    and the entity goes unavailable. A missing one is not a cosmetic
    gap: a robot returning to charge mid-mission (`hmUsrChrg`) would
    have taken the sensor down.

    The first version of this list was assembled from field captures. It
    guessed at two values that are not in the vendor enum, and missed
    five that are.
    """

    #: `Phase`, app 3.0.0, in the JSON casing the shadow uses.
    VENDOR_PHASES = {
        "stop", "charge", "run", "stuck", "hmPostMsn", "hmMidMsn",
        "hmUsrDock", "hmUsrChrg", "chargingError", "mapUpd", "evac",
        "refill", "padWash", "padDry",
    }

    def _options(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        entity = object.__new__(PrimePhaseSensor)
        entity._config_entry = MagicMock()
        return set(PrimePhaseSensor.options.fget(entity))

    def test_every_vendor_phase_is_offered(self):
        missing = sorted(self.VENDOR_PHASES - self._options())

        assert not missing, (
            "these phases would make the entity unavailable rather than "
            f"showing a value: {missing}"
        )

    def test_hm_usr_chrg_in_particular(self):
        """Returning to charge mid-mission — the one most likely to be
        hit, and the one a captures-only list would miss because it
        happens on long cleans."""
        assert "hmUsrChrg" in self._options()

    def test_the_older_values_are_kept(self):
        """They cost nothing if never sent, and dropping them on the
        strength of one app version would trade a confirmed list for a
        complete one — Classic robots reach this code too."""
        assert {"pause", "new"} <= self._options()


class TestUnfinishedRoomsAreVisible:
    """@chairstacker reported a mission that failed on a blocked door
    and **left no trace anywhere**: the robot came back, the history
    showed a completed entry, and nothing said a room had been skipped.

    `mission_last_unfinished` carried the answer the whole time, and it
    is an object rather than a flag — so this says "room 11, mission 61"
    rather than "something was unfinished". With the mission number an
    automation can tell a room still waiting from one picked up on the
    next pass.
    """

    def _attrs(self, scores, names=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import (
            PrimeMissionEventSensor,
        )

        entity = object.__new__(PrimeMissionEventSensor)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_clean_scores=scores, prime_room_names=names or {}
        )
        entity._config_entry = entry
        return entity._unfinished_rooms()

    def _scores(self, regions):
        return {"regions": regions}

    def test_a_skipped_room_is_named_with_its_mission(self):
        attrs = self._attrs(
            self._scores([{
                "region_id": "11",
                "mission_last_unfinished": {"nMssn": 61, "missionId": "M1"},
            }]),
            names={"11": "Kitchen"},
        )

        assert attrs == {"Kitchen": 61}

    def test_an_unnamed_room_falls_back_to_its_id(self):
        """Ugly and honest — a room id somebody can match against the
        map beats a room that does not appear."""
        attrs = self._attrs(self._scores([{
            "region_id": "11", "mission_last_unfinished": {"nMssn": 61},
        }]))

        assert attrs == {"11": 61}

    def test_a_completed_run_reports_nothing(self):
        assert self._attrs(self._scores([{"region_id": "11"}])) == {}

    def test_no_scores_yet_is_not_an_error(self):
        assert self._attrs(None) == {}

    def test_rubbish_does_not_take_the_sensor_down(self):
        """An attribute that cannot be built is left out; the sensor's
        own state is unaffected."""
        assert self._attrs("nonsense") == {}


class TestAFrozenShadowIsNotReportedAsCleaning:
    """@utkjmitch's Y351020 errored mid-mission on a Saturday and the
    shadow froze at `{phase: "run", error: 48}` for **61 hours**. The
    robot kept updating `batPct` — 75 up to 96, so it was alive and
    talking — and never wrote a terminal phase.

    Two daily schedules were silently skipped, the vacuum showed
    "cleaning" for two and a half days, and **every cloud command was
    swallowed**: stop, start, dock and find, each broker-confirmed, none
    with any effect. iRobot's own app could not end its own phantom
    mission. Only a power cycle cleared it.

    Nothing here can unfreeze it. This stops the sensor repeating the
    lie, which is what automations are built on — using the test he
    proposed: **charging and running are mutually exclusive, and the
    robot supplies both numbers.**
    """

    def _sensor(self, readings_spec, step_sec=340.0):
        """Feed (phase, bat_pct) pairs through one entity, advancing a
        fake clock. A bare int means ("run", level), so the freeze cases
        read unchanged."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        entity = object.__new__(PrimePhaseSensor)
        entity._config_entry = MagicMock()
        readings = []
        clock = {"now": 1000.0}
        with patch(
            "custom_components.roomba_plus.sensor_prime.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            for spec in readings_spec:
                phase, level = spec if isinstance(spec, tuple) else ("run", spec)
                state = SimpleNamespace(
                    bat_pct=level,
                    clean_mission_status=SimpleNamespace(phase=phase),
                )
                with patch.object(
                    type(entity), "_current_state",
                    new=PropertyMock(return_value=state),
                ):
                    readings.append(entity.native_value)
                clock["now"] += step_sec
        return readings

    def test_a_rising_battery_during_a_long_run_reads_stale(self):
        """His exact case: climbing while the document says run -- and
        the run is old enough that no charge tail can explain it. The
        real freeze rose for 61 hours; eleven minutes is generous."""
        assert self._sensor([75, 75, 80])[-1] == "stale"

    def test_one_reading_is_never_enough(self):
        """A single sample cannot show a direction, and calling a robot
        stale on its first update would be worse than the freeze."""
        assert self._sensor([75])[0] == "run"

    def test_a_falling_battery_is_a_real_mission(self):
        """Which is what cleaning looks like."""
        assert self._sensor([90, 85, 80])[-1] == "run"

    def test_a_steady_battery_is_not_stale(self):
        """Conservative on purpose: it takes a real increase."""
        assert self._sensor([80, 80])[-1] == "run"

    def test_stale_is_a_declared_option(self):
        """An ENUM value outside `options` takes the entity down, which
        would replace one silent failure with another."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        entity = object.__new__(PrimePhaseSensor)
        entity._config_entry = MagicMock()

        assert "stale" in PrimePhaseSensor.options.fget(entity)


class TestTheGraceWindowFromTheField:
    """@utkjmitch's Y351020, on the first morning this detector existed:
    a recharge-and-resume went `charge` → `run` at 37% and the next
    reports still climbed to 40 — **the charge tail, on a robot that was
    physically driving.** The sensor read `stale` twice, ~4 s and then
    ~2 min, before settling.

    The state `stale` names is cured by a power cycle, so a false
    positive pages somebody for a healthy robot. The real freeze rises
    for 61 hours; waiting out ten minutes costs detection nothing.
    """

    def _sensor(self, *args, **kwargs):
        from tests.test_sensor_prime import TestAFrozenShadowIsNotReportedAsCleaning

        return TestAFrozenShadowIsNotReportedAsCleaning._sensor(
            TestAFrozenShadowIsNotReportedAsCleaning(), *args, **kwargs
        )

    def test_the_charge_tail_of_a_resume_is_not_stale(self):
        readings = self._sensor(
            [("charge", 37), ("run", 37), ("run", 40)], step_sec=30.0
        )

        assert readings[-1] == "run"

    def test_every_resume_restarts_the_grace_clock(self):
        """A long mission with a second recharge stop must not inherit
        the first run's elapsed grace."""
        readings = self._sensor(
            [("run", 75), ("run", 70), ("charge", 80), ("run", 80), ("run", 84)]
        )

        assert readings[-1] == "run"

    def test_a_restart_into_a_frozen_shadow_still_detects_it(self):
        """Home Assistant restarting mid-freeze sees `run` from its very
        first reading, with no transition to anchor on. The clock starts
        there, so detection is delayed by one window rather than lost."""
        assert self._sensor([75, 80, 85])[-1] == "stale"


class TestTheVendorTextArrivesAsAttributes:
    """The state stays a raw code — that argument holds — but it predates
    `vendor_errors.py`, whose catalogue came from the **Prime** app's own
    locale files. "No text of ours would be sourced" stopped being true
    the moment iRobot's arrived.

    Error 48 reads "An obstacle blocked the entrance to a room", which
    on one field robot explained 93 timeline error events and every
    incomplete mission in its archive — a household that keeps the
    playroom door shut so the dog cannot steal toys.
    """

    def _attrs(self, error, language="en", with_hass=True):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.sensor_prime import PrimeErrorSensor

        entity = object.__new__(PrimeErrorSensor)
        entity._config_entry = MagicMock()
        if with_hass:
            entity.hass = MagicMock()
            entity.hass.config.language = language
        # The sensor reads more of the status than the error alone, so
        # a two-field namespace is not enough to exercise it.
        state = SimpleNamespace(
            clean_mission_status=SimpleNamespace(
                error=error, cycle="clean", phase="run", not_ready=0,
                operating_mode=2, initiator="rmtApp", mission_id="M1",
                n_missions=1, sqft=0, cond_not_ready=[],
                mission_start_time=None, expire_time=None,
                recharge_time=None,
            )
        )
        with patch.object(
            type(entity), "_current_state", new=PropertyMock(return_value=state)
        ):
            return entity.extra_state_attributes

    def test_a_documented_code_carries_its_title(self):
        attrs = self._attrs(48)

        assert "obstacle" in attrs["error_title"].lower()

    def test_the_explanation_comes_with_it(self):
        assert self._attrs(48).get("error_description")

    def test_an_undocumented_code_invents_nothing(self):
        """Same rule as the unknown part names: guessing from one
        household is how wrong mappings get made."""
        attrs = self._attrs(9999)

        assert "error_title" not in attrs

    def test_no_error_means_no_text(self):
        assert "error_title" not in self._attrs(0)

    def test_it_works_before_the_entity_has_hass(self):
        """Attributes are readable before the entity is added, and the
        tests construct it bare. English rather than a crash."""
        attrs = self._attrs(48, with_hass=False)

        assert attrs.get("error_title")
