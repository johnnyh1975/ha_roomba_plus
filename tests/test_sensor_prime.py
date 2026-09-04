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
        # 35 since the initiator sensor. Prime reports
        # `cleanMissionStatus.initiator` exactly as Classic does and had
        # no sensor for it, so the 19 labels added to the shared table
        # were readable on one generation only.
        # 36 since the overdue sensor. The Classic one sat behind a
        # `map_capability` check in a branch CLOUD_ONLY never reaches,
        # so Prime had none -- on a generation that has zones as well
        # as rooms and therefore more to fall behind.
        # 37 since the room-cleaning history sensor: the single-entity,
        # attribute-carrying form Classic has always shipped, which
        # Prime had no equivalent of. The per-region entities are NOT
        # counted here -- they are opt-in on both tiers.
        assert len(created) == 37, (
            "rooms-overdue was added for Prime: it reads the mission "
            "store and cloud regions, both of which Prime has, and it "
            "was missing only because of the order things were built in"
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
        # Not every Prime sensor carries an entity_description -- the
        # room-cleaning history names itself with _attr_translation_key,
        # like its Classic parent. Reaching straight for the attribute
        # raised AttributeError the moment one such sensor appeared.
        keys = {
            getattr(getattr(e, "entity_description", None), "key", "")
            for e in created
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
        stable [a-z0-9_]+ slugs for the real, named DockState members,
        not just bare numbers or English prose."""
        assert _dock_state_label(301) == "dock_ready"
        assert _dock_state_label(601) == "pad_wash_okay"
        assert _dock_state_label(701) == "pad_dry_okay"

    def test_unrecognized_value_does_not_crash(self):
        """DockState has 86 confirmed values -- an out-of-range value
        (a server-side addition this library doesn't know about yet)
        must degrade gracefully, not raise, and must still be a valid
        state slug rather than a number embedded in text."""
        assert _dock_state_label(99999) == "unrecognized"

    def test_none_returns_none(self):
        assert _dock_state_label(None) is None


class TestPrimeDockStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"state": 301}})
        sensor = PrimeDockStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "dock_ready"
        assert sensor.extra_state_attributes == {"code": 301}

    def test_unrecognized_code_falls_back_to_a_slug(self):
        config_entry = _make_status_config_entry({"dock": {"state": 99999}})
        sensor = PrimeDockStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "unrecognized"
        assert sensor.extra_state_attributes == {"code": 99999}


class TestPrimePadWashStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"pwState": 601}})
        sensor = PrimePadWashStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "pad_wash_okay"
        assert sensor.extra_state_attributes == {"code": 601}


class TestPrimePadDryStatusSensor:
    def test_native_value_reflects_real_captured_value(self):
        config_entry = _make_status_config_entry({"dock": {"pdState": 701}})
        sensor = PrimePadDryStatusSensor("BLID123", config_entry)

        assert sensor.native_value == "pad_dry_okay"
        assert sensor.extra_state_attributes == {"code": 701}


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

        assert sensor.native_value is None

    def test_suppresses_stale_error_when_idle_too(self):
        sensor = self._entity(cycle="none", phase="idle", error=671)

        assert sensor.native_value is None

    def test_no_error_during_a_mission_reports_none_label(self):
        sensor = self._entity(cycle="clean", phase="run", error=0)

        assert sensor.native_value is None

    def test_exposes_readiness_fields_as_attributes(self):
        """A readiness-based START REFUSAL leaves `error` at 0 and puts
        the reasons in cond_not_ready -- see the class docstring.

        THE CODES ARE INTEGERS. This fixture used `["binFull"]`, a phase
        name borrowed as a plausible-looking refuse reason, and nothing
        rejected it because the field was typed `list[Any]`.

        A real robot showed `notReady: 0` beside `condNotReady: [234]`,
        and the app-side deserializer declares `List<Integer>`. The
        library now types it accordingly and drops non-integers rather
        than passing through whatever arrives, so an invented string no
        longer survives to the attribute.

        The meanings of the codes are still unknown -- 234 is the only
        one anyone has seen. Naming them would be the same mistake this
        fixture made.
        """
        sensor = self._entity(cycle="none", phase="charge", error=0, notReady=8, condNotReady=[234])

        attrs = sensor.extra_state_attributes
        assert attrs["not_ready"] == 8
        assert attrs["cond_not_ready"] == [234]

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

        # THE CONTRACT, NOT THE SPELLING. This asserted on
        # `not dock_cap_known or`, a named flag holding
        # `dock_cap is not None` -- which is why it narrowed nothing and
        # mypy flagged every read past it. Asking the value directly
        # says the same thing and carries the type, so the flag went.
        #
        # What has to hold is the fail-open direction: a missing dock
        # capability creates the entity rather than withholding it.
        assert "dock_cap is None or" in source
        assert source.count("dock_cap is None or") >= 2



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
        assert self._label(671) == "pad_wash_blocked_check_tanks"

    def test_it_does_not_say_empty(self):
        """The tank was REMOVED, not empty. "Empty" sends someone to
        refill a tank that is not in the dock -- and 671 was seen in
        both states, so neither word is right on its own."""
        assert "empty" not in self._label(671).lower()

    def test_enum_values_still_win(self):
        """The overlay must not shadow the decompiled enum."""
        assert self._label(601) == "pad_wash_okay"
        assert self._label(301) == "dock_ready"

    def test_an_unknown_code_still_falls_back(self):
        assert self._label(9999) == "unrecognized"

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
        "hmUsrDock", "sent_to_charge", "chargingError", "mapUpd", "evac",
        "refill", "padWash", "padDry",
    }

    def _options(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        entity = object.__new__(PrimePhaseSensor)
        entity._config_entry = MagicMock()
        return set(PrimePhaseSensor.options.fget(entity))

    def test_every_vendor_phase_is_offered(self):
        # THROUGH THE TABLE NOW. Options carry translation keys, not
        # firmware words, so a vendor phase is "offered" when
        # PHASE_LABELS maps it to something the options list contains.
        # Comparing the two sets directly stopped meaning anything the
        # moment the sensor became translatable.
        from custom_components.roomba_plus.const import PHASE_LABELS

        options = self._options()
        missing = sorted(
            v for v in self.VENDOR_PHASES
            if PHASE_LABELS.get(v, v) not in options
        )

        assert not missing, (
            "these phases would make the entity unavailable rather than "
            f"showing a value: {missing}"
        )

    def test_hm_usr_chrg_in_particular(self):
        """Returning to charge mid-mission — the one most likely to be
        hit, and the one a captures-only list would miss because it
        happens on long cleans."""
        assert "sent_to_charge" in self._options()

    def test_the_older_values_are_kept(self):
        """They cost nothing if never sent, and dropping them on the
        strength of one app version would trade a confirmed list for a
        complete one — Classic robots reach this code too."""
        assert {"paused", "new_mission"} <= self._options()


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

    def test_a_rising_battery_during_a_long_run_reads_not_responding(self):
        """His exact case: climbing while the document says run -- and
        the run is old enough that no charge tail can explain it. The
        real freeze rose for 61 hours; eleven minutes is generous."""
        assert self._sensor([75, 75, 80])[-1] == "not_responding"

    def test_one_reading_is_never_enough(self):
        """A single sample cannot show a direction, and calling a robot
        stale on its first update would be worse than the freeze."""
        assert self._sensor([75])[0] == "running"

    def test_a_falling_battery_is_a_real_mission(self):
        """Which is what cleaning looks like."""
        assert self._sensor([90, 85, 80])[-1] == "running"

    def test_a_steady_battery_is_not_stale(self):
        """Conservative on purpose: it takes a real increase."""
        assert self._sensor([80, 80])[-1] == "running"

    def test_stale_is_a_declared_option(self):
        """An ENUM value outside `options` takes the entity down, which
        would replace one silent failure with another."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        entity = object.__new__(PrimePhaseSensor)
        entity._config_entry = MagicMock()

        assert "not_responding" in PrimePhaseSensor.options.fget(entity)


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

        assert readings[-1] == "running"

    def test_every_resume_restarts_the_grace_clock(self):
        """A long mission with a second recharge stop must not inherit
        the first run's elapsed grace."""
        readings = self._sensor(
            [("run", 75), ("run", 70), ("charge", 80), ("run", 80), ("run", 84)]
        )

        assert readings[-1] == "running"

    def test_a_restart_into_a_frozen_shadow_still_detects_it(self):
        """Home Assistant restarting mid-freeze sees `run` from its very
        first reading, with no transition to anchor on. The clock starts
        there, so detection is delayed by one window rather than lost."""
        assert self._sensor([75, 80, 85])[-1] == "not_responding"


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


class TestBlockingFaultsSayWhatStillWorks:
    """`blockFault` (app 3.0.0) checks exactly four codes before letting
    a mission start. All four have been in the error catalogue for
    weeks, and the integration drew no conclusion from any of them.

    Three do not mean "broken" — they mean one half of what you asked is
    impossible right now. A user sending a vacuum command against 287
    got a command that left, was refused, and produced no explanation an
    automation could read.
    """

    @staticmethod
    def _blocking(**kwargs):
        from types import SimpleNamespace

        from custom_components.roomba_plus.sensor_prime import _blocking_faults

        base = {"error": 0, "cond_not_ready": []}
        return _blocking_faults(SimpleNamespace(**{**base, **kwargs}))

    def test_the_pad_plate_pair_are_opposite_states(self):
        """287 = plate fitted, mop only. 290 = plate missing, vacuum
        only. Folding them together would tell a user to attach the
        plate they already have on."""
        assert self._blocking(error=287) == {287: frozenset({"mop"})}
        assert self._blocking(error=290) == {290: frozenset({"vacuum"})}

    def test_no_cloth_is_not_the_same_as_no_plate(self):
        """234 is the plate WITH no cloth on it — which is exactly what
        `detectedPad: "padPlate"` reports on a real robot. It still
        vacuums."""
        assert self._blocking(error=234) == {234: frozenset({"vacuum"})}

    def test_a_lifted_robot_can_do_neither(self):
        assert self._blocking(error=286) == {286: frozenset()}

    def test_a_readiness_refusal_is_found_too(self):
        """A refusal leaves `error` at 0 and puts its reasons in
        `cond_not_ready` — documented in the sensor's own docstring and
        never used for anything until now. Checking one field would miss
        the case the check exists for."""
        assert self._blocking(error=0, cond_not_ready=[287]) == {
            287: frozenset({"mop"})
        }
        assert self._blocking(error=0, cond_not_ready=["290"]) == {
            290: frozenset({"vacuum"})
        }

    def test_an_ordinary_fault_blocks_nothing(self):
        """Error 48 is an obstacle, not a start gate. An empty result
        means "no opinion" — this knows four codes out of 112."""
        assert self._blocking(error=48) == {}
        assert self._blocking() == {}

    def test_the_catalogue_and_the_gate_agree(self):
        """Every blocking code must have a text. A gate that names a
        code the catalogue cannot explain would produce an attribute
        nobody can read."""
        from custom_components.roomba_plus.const import (
            PRIME_BLOCKING_FAULTS,
            PRIME_ERROR_SEVERITY,
        )

        for code in PRIME_BLOCKING_FAULTS:
            assert code in PRIME_ERROR_SEVERITY, code


class TestTheFaultSceneReachesTheEntity:
    """`FaultScene.scene_for()` shipped in roombapy-prime b5 with five
    fully specified rules and was called by nothing.

    The same code means different things per running task — a stall
    during `padWash` is a dock problem, the same stall during
    `cleanTask` is a robot problem. The robot never sends a scene;
    `getFaultScene({cmStatus, command})` computes it.
    """

    def test_a_dock_task_is_recognised_from_the_phase(self):
        from roombapy_prime.models.mission_history import FaultScene

        assert FaultScene.scene_for(phase="padWash") is FaultScene.WASH_TASK

    def test_the_command_alone_is_enough(self):
        """The dock rules match on command OR phase: a fault during a
        wash is a wash fault whether the user asked for it or the robot
        started it."""
        from roombapy_prime.models.mission_history import FaultScene

        assert FaultScene.scene_for(command="drypad") is FaultScene.DRY_TASK

    def test_an_ordinary_clean_yields_no_scene(self):
        """Seven of twelve scenes have no stated condition. Falling back
        to the documented default would put a plausible wrong task name
        on a real error message."""
        from roombapy_prime.models.mission_history import FaultScene

        assert FaultScene.scene_for(phase="run", cycle="clean") is None

    def test_the_sensor_asks_for_it_at_all(self):
        """The point of this change: the rules existed and nothing
        called them."""
        import inspect

        from custom_components.roomba_plus import sensor_prime

        source = inspect.getsource(sensor_prime)

        assert "FaultScene.scene_for(" in source
        assert "fault_scene" in source


class TestTheBlockingReportDoesNotBlock:
    """This reports; it does not gate — and that distinction is the same
    one made for the dock controls.

    App 3.0.0 greys out every Dock Control once a task begins, so a
    drying cycle it started cannot be stopped there. @chairstacker calls
    that the big drawback of the new UI, and being able to stop it from
    Home Assistant the reason to keep ours as it is.

    So a vacuum command against 287 still goes out. What changed is that
    the robot's refusal is explainable in advance.
    """

    def test_no_command_path_consults_the_table(self):
        """The guard. If a future change gates a command on this, the
        integration starts refusing things the robot would accept —
        which is the app's mistake, not one worth copying."""
        import pathlib

        base = pathlib.Path("custom_components/roomba_plus")
        readers = sorted(
            p.name
            for p in base.glob("*.py")
            if "PRIME_BLOCKING_FAULTS" in p.read_text()
            or "_blocking_faults" in p.read_text()
        )

        # binary_sensor.py joined the list when the visible sensor was
        # added -- it REPORTS the same answer on a non-diagnostic
        # entity. What must never appear here is a command path:
        # room_cleaning, services, vacuum, button_prime.
        assert readers == [
            "binary_sensor.py",
            "const.py",
            "sensor_prime.py",
        ], (
            f"{readers} read the blocking table. It is advisory: a command "
            "path reading it would refuse what the robot accepts, which is "
            "the app's mistake and not one worth copying."
        )

    def test_a_message_comes_with_the_mode_lists(self):
        """Mode lists without words are half an answer. The vendor's own
        text says why."""
        from custom_components.roomba_plus.sensor_prime import (
            get_localized_error_entry,
        )

        assert get_localized_error_entry(287, "en").get("label")
        assert get_localized_error_entry(290, "de").get("label")

    def test_the_message_works_for_a_readiness_refusal_too(self):
        """`error_title` only fills when `status.error` is set. A
        refusal leaves it at 0 and puts the code in `cond_not_ready` —
        exactly the case this block exists for, which would have
        produced mode lists with nothing beside them."""
        import inspect

        from custom_components.roomba_plus import sensor_prime

        source = inspect.getsource(sensor_prime.PrimeErrorSensor)

        assert "blocked_reason" in source
        # The language lookup must sit OUTSIDE the `if status.error`
        # branch, or the refusal case has no locale to render with.
        before_error = source.split("if status.error:")[0]
        assert "language = (" in before_error


class TestPrimeReadinessSensor:
    """Untested before this session -- `notReady` was slug-converted
    alongside the dock/pad-wash/pad-dry/job-initiator sensors, and
    needed its own coverage for the same reason they did."""

    @staticmethod
    def _sensor(not_ready):
        from unittest.mock import PropertyMock, patch
        from types import SimpleNamespace

        from custom_components.roomba_plus.sensor_prime import (
            PrimeReadinessSensor,
        )

        sensor = PrimeReadinessSensor.__new__(PrimeReadinessSensor)
        state = SimpleNamespace(
            clean_mission_status=SimpleNamespace(not_ready=not_ready)
        )
        return sensor, patch.object(
            PrimeReadinessSensor, "_current_state",
            new_callable=PropertyMock, return_value=state,
        )

    def test_zero_means_ready(self):
        sensor, state = self._sensor(0)
        with state:
            assert sensor.native_value == "none"
            assert sensor.extra_state_attributes == {"code": 0}

    def test_a_named_code_becomes_a_slug(self):
        """13 is "Bin full" in READINESS_STATE_LABELS."""
        sensor, state = self._sensor(13)
        with state:
            assert sensor.native_value == "bin_full"
            assert sensor.extra_state_attributes == {"code": 13}

    def test_an_unrecognized_code_still_falls_back(self):
        """@connormxy's error 236 is exactly this case -- a code the
        table has no entry for must still show something, not a blank
        sensor, and it must still be a valid state slug."""
        sensor, state = self._sensor(236)
        with state:
            assert sensor.native_value == "unrecognized"
            assert sensor.extra_state_attributes == {"code": 236}

    def test_no_code_reads_none(self):
        sensor, state = self._sensor(None)
        with state:
            assert sensor.native_value is None
            assert sensor.extra_state_attributes == {}


class TestTheInitiatorSensorSeparatesTwoQuestions:
    """`cleanMissionStatus.initiator` and `lastCommand.initiator` are
    different fields, and @chairstacker's dump shows them disagreeing at
    the same moment:

        cleanMissionStatus.initiator   "cloud"    started the mission
        lastCommand.initiator          "rmtApp"   sent `stoppaddry`

    The sensor reads the first, matching Classic's "Started by" exactly.
    Reading the other under the same name would give two generations two
    meanings for one sensor.
    """

    @staticmethod
    def _sensor(mission_initiator, last_command=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.sensor_prime import (
            PrimeJobInitiatorSensor,
        )

        sensor = PrimeJobInitiatorSensor.__new__(PrimeJobInitiatorSensor)
        sensor._config_entry = MagicMock()
        state = SimpleNamespace(
            clean_mission_status=SimpleNamespace(initiator=mission_initiator)
        )
        return sensor, patch.object(
            PrimeJobInitiatorSensor, "_current_state",
            new_callable=PropertyMock, return_value=state,
        ), patch(
            "custom_components.roomba_plus.sensor_prime.prime_last_command",
            return_value=last_command or {},
        )

    def test_it_reports_the_mission_initiator(self):
        sensor, state, _ = self._sensor("schedule")
        with state:
            assert sensor.native_value == "schedule"

    def test_the_last_command_is_an_attribute_not_the_state(self):
        """Both are real. A second entity called "Last command by" beside
        "Started by" would be two names one letter apart in meaning."""
        sensor, state, last = self._sensor(
            "cloud",
            {"command": "stoppaddry", "initiator": "rmtApp", "time": 1784831254},
        )
        with state, last:
            assert sensor.native_value == "cloud"
            attrs = sensor.extra_state_attributes

        assert attrs["last_command"] == "stoppaddry"
        assert attrs["last_command_by"] == "remote_app"
        assert attrs["initiator"] == "cloud"

    def test_the_raw_value_is_kept_beside_the_label(self):
        """An automation branching on this should not have to reverse a
        translation."""
        sensor, state, last = self._sensor("dockBtn", {"initiator": "alexa"})
        with state, last:
            attrs = sensor.extra_state_attributes

        assert attrs["initiator"] == "dockBtn"
        assert attrs["last_command_initiator"] == "alexa"

    def test_an_unmapped_value_falls_back_to_none_like_classic_does(self):
        """Prime and Classic share `translation_key="job_initiator"` by
        design, so an unrecognised wire value must resolve the same way
        on both -- Classic's value_fn already falls back to "none" via
        `JOB_INITIATOR_SLUGS.get(raw, "none")`, and this sensor now uses
        the exact same table rather than a separately-derived slug."""
        sensor, state, _ = self._sensor("somethingNew")
        with state:
            assert sensor.native_value == "none"


class TestTheThirdPhaseCategory:
    """`CLEANING_PHASES` and `MISSION_END_PHASES` leave `padWash`,
    `padDry` and `refill` in neither set — a gap this project has been
    asking testers to resolve since a mid-mission wash was found.

    The answer was in the research package. `isMissionPhaseStillRunning`
    puts all three in "not running", and `isCleanDockTask` puts the same
    three in a category of their own: a DOCK task. Not a missing
    decision — a third state nothing modelled.
    """

    def test_the_three_dock_phases_are_named(self):
        from custom_components.roomba_plus.const import DOCK_TASK_PHASES

        assert DOCK_TASK_PHASES == frozenset({"padWash", "padDry", "refill"})

    def test_they_are_in_neither_of_the_other_two_sets(self):
        from custom_components.roomba_plus.const import (
            CLEANING_PHASES,
            DOCK_TASK_PHASES,
            MISSION_END_PHASES,
        )

        assert not DOCK_TASK_PHASES & CLEANING_PHASES
        assert not DOCK_TASK_PHASES & MISSION_END_PHASES

    def test_evac_stays_a_cleaning_phase_against_the_vendor_rule(self):
        """The vendor calls `evac` "not running". We call it cleaning,
        deliberately since v2.6.3: an i7+ goes through evac MID-mission
        and treating it as an ending reset the map renderer early.

        A field observation beats a rule table."""
        from custom_components.roomba_plus.const import CLEANING_PHASES

        assert "evac" in CLEANING_PHASES

    def test_the_disagreement_is_written_down(self):
        """Three differences from the vendor's own rule, and one of them
        we are right about. A reader who finds the rule table later must
        not "fix" evac back."""
        import inspect

        from custom_components.roomba_plus import const

        source = inspect.getsource(const)

        assert "isMissionPhaseStillRunning" in source
        assert "A field observation beats a rule table" in source


class TestADockTheRobotDoesNotKnowSaysSo:
    """@utkjmitch has reported `prime_dock_status` reading "unknown"
    since a32. His dock block is the whole of
    `{"fwVer": "", "known": false, "error": 0}` — no `state`, no `cap`.

    @chairstacker's, for contrast, carries
    `{"cap": {...}, "state": 301, "pdState": 701, "known": true}`.

    That comparison answers the question @utkjmitch left open: he asked
    whether another robot carries a `cap` where his carries nothing,
    because that would mean `known: false` is about IDENTITY rather
    than capability. It does. His dock is mute, not passive — and the
    sensor was right to have nothing to report; it just did not say
    which nothing.
    """

    @staticmethod
    def _sensor(dock):
        from unittest.mock import MagicMock, PropertyMock, patch

        from custom_components.roomba_plus.sensor_prime import (
            PrimeDockStatusSensor,
        )

        sensor = object.__new__(PrimeDockStatusSensor)
        state = MagicMock(dock=dock)
        with patch.object(
            type(sensor), "_current_state", PropertyMock(return_value=state)
        ):
            return sensor.native_value

    def test_an_unknown_dock_names_its_silence(self):
        from unittest.mock import MagicMock

        value = self._sensor(MagicMock(state=None, known=False))

        assert value == "not_reported"

    def test_a_known_dock_with_no_state_still_reads_none(self):
        """`known: true` and no state is a different situation — the
        robot recognises the dock and it has not said anything yet."""
        from unittest.mock import MagicMock

        assert self._sensor(MagicMock(state=None, known=True)) is None

    def test_a_reporting_dock_is_unaffected(self):
        """@chairstacker's 301."""
        from unittest.mock import MagicMock

        value = self._sensor(MagicMock(state=301, known=True))

        assert value not in (None, "not_reported")


# ============================================================================
# CONSUMABLE PARTS.
#
# Moved here from test_sensors.py (August 2026). `PrimeConsumablePartSensor`
# is defined in this module; the class carries its own `_sensor` builder.
# ============================================================================


class TestPrimeConsumableParts:
    """Consumables for Prime robots, from chairstacker's request.

    The data was already reachable -- the library had supported the
    endpoint for a while and the integration simply never called it.
    His screenshot made that obvious: everything in it was already in
    our reach.

    The values below are taken from that screenshot, which is also why
    the unit handling matters: the SAME robot reports hours for its
    filter and routines for its mop pads. A single hard-coded unit
    would be wrong for most parts."""

    def _sensor(self, part_id, **fields):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimeConsumablePartSensor

        part = MagicMock(part_id=part_id, **fields)
        entry = MagicMock()
        entry.runtime_data.prime_parts_coordinator.data = {part_id: part}

        sensor = object.__new__(PrimeConsumablePartSensor)
        sensor._config_entry = entry
        sensor._part_id = part_id
        return sensor

    def test_a_filter_reports_hours(self):
        """"~24 hr left" in his screenshot."""
        from homeassistant.const import UnitOfTime

        sensor = self._sensor("filter", count_remaining=24, count_type="hr")

        assert sensor.native_value == 24
        assert sensor.native_unit_of_measurement == UnitOfTime.HOURS

    def test_mop_pads_report_routines_not_hours(self):
        """"~14 routines left". This is the case a fixed hours unit
        would silently mislabel."""
        sensor = self._sensor("mop_pads", count_remaining=14, count_type="routines")

        assert sensor.native_value == 14
        assert sensor.native_unit_of_measurement == "routines"

    def test_the_dirt_bag_reports_evacuations(self):
        """"~60 evacs left" -- the one dock-adjacent consumable that is
        actually tracked."""
        sensor = self._sensor("dirt_bag", count_remaining=60, count_type="evacs")

        assert sensor.native_unit_of_measurement == "evacuations"

    def test_an_unknown_count_type_gets_no_unit_rather_than_a_wrong_one(self):
        """A wrong unit is worse than none: it invites arithmetic that
        does not hold. Better to show a bare number."""
        sensor = self._sensor("mystery", count_remaining=5, count_type="somethingelse")

        assert sensor.native_value == 5
        assert sensor.native_unit_of_measurement is None

    def test_a_part_that_disappears_makes_the_sensor_unavailable(self):
        """Rather than reporting a stale count as if it were current."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimeConsumablePartSensor

        entry = MagicMock()
        entry.runtime_data.prime_parts_coordinator.data = {}
        sensor = object.__new__(PrimeConsumablePartSensor)
        sensor._config_entry = entry
        sensor._part_id = "filter"

        assert sensor.native_value is None

    def test_the_raw_details_are_kept_as_attributes(self):
        """count_used and minutes_remaining are not worth their own
        entities, but throwing them away would lose the only record of
        what the server actually said."""
        sensor = self._sensor(
            "filter", count_remaining=24, count_type="hr",
            count_used=76, minutes_remaining=1440, counter_category="maintenance",
        )

        attrs = sensor.extra_state_attributes
        assert attrs["count_used"] == 76
        assert attrs["minutes_remaining"] == 1440
        assert attrs["count_type"] == "hr"




class TestPrimeRegionLastCleanedSensor:
    """One entity per room and zone, per @chairstacker's request:
    "a date/time entity ... for each room and cleaning zone
    automatically and I don't have to build helpers for each"."""

    @staticmethod
    def _sensor(history, pmap_id="MAP-A", region_id="2", name="Guest room"):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.sensor_prime import (
            PrimeRegionLastCleanedSensor,
        )

        entry = MagicMock()
        entry.runtime_data.mission_store.region_last_cleaned.return_value = history

        with patch.object(
            PrimeRegionLastCleanedSensor, "robot_unique_id", "BLID1"
        ), patch(
            "custom_components.roomba_plus.sensor_prime.IRobotEntity.__init__",
            return_value=None,
        ):
            sensor = PrimeRegionLastCleanedSensor(
                "BLID1", entry, pmap_id, region_id, name
            )
        sensor._config_entry = entry
        return sensor

    def test_it_reports_the_completion_time(self):
        sensor = self._sensor({"MAP-A/2": "2026-08-21T10:00:00+00:00"})

        assert sensor.native_value is not None
        assert sensor.native_value.year == 2026

    def test_it_reads_its_own_map_not_another(self):
        """A clean of region 2 on a DIFFERENT floor must not show up
        here -- the fault @dduff617 found in the history sensor."""
        sensor = self._sensor({"MAP-B/2": "2026-08-21T10:00:00+00:00"})

        assert sensor.native_value is None

    def test_an_unqualified_record_still_counts(self):
        """Older entries carry no map; their data is still this
        region's."""
        sensor = self._sensor({"2": "2026-08-21T10:00:00+00:00"})

        assert sensor.native_value is not None

    def test_a_never_cleaned_region_is_unknown_not_an_error(self):
        sensor = self._sensor({})

        assert sensor.native_value is None

    def test_the_unique_id_carries_map_and_region_not_the_name(self):
        """A rename in the iRobot app must not orphan the entity and
        lose its recorded history."""
        sensor = self._sensor({}, region_id="101", name="Sofa corner")

        assert "101" in sensor._attr_unique_id
        assert "MAP-A" in sensor._attr_unique_id
        assert "Sofa" not in sensor._attr_unique_id

    def test_the_map_is_exposed_as_an_attribute(self):
        """So two rooms sharing a name on different floors can be told
        apart in the UI."""
        sensor = self._sensor({})

        assert sensor.extra_state_attributes["pmap_id"] == "MAP-A"
        assert sensor.extra_state_attributes["region_id"] == "2"

    @pytest.mark.asyncio
    async def test_it_subscribes_so_the_value_moves(self):
        """A sensor with no coordinator of its own reads once at
        start-up and never again. It looks correct in the UI right up
        until a mission finishes and the state should have changed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        sensor = self._sensor({})
        coordinator = sensor._config_entry.runtime_data.prime_status_coordinator
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        sensor.async_on_remove = MagicMock()

        with patch(
            "custom_components.roomba_plus.sensor_prime.IRobotEntity"
            ".async_added_to_hass",
            AsyncMock(),
        ):
            await sensor.async_added_to_hass()

        coordinator.async_add_listener.assert_called_once()

    def test_the_name_is_translated_not_an_english_suffix(self):
        """The room name comes from the user's own iRobot account, so
        it is already in their language. Hard-coding "last cleaned"
        beside it produced "Küche last cleaned" for everyone else."""
        sensor = self._sensor({}, name="Küche")

        assert sensor._attr_translation_key == "region_last_cleaned"
        assert sensor._attr_translation_placeholders == {"region": "Küche"}
        assert not hasattr(sensor, "_attr_name") or sensor._attr_name is None


class TestTheMissionStatusNamesItsRoom:
    """The timeline report carries ids and nothing else, so
    `current_room_id` was a number and there was no name beside it.

    The device tracker resolves the same ids against `prime_room_names`
    and shows a name. This sensor did not, so the two entities
    disagreed about the same robot in the same moment.

    `prime_room_names` holds rooms and zones together, which matters
    here: a zone-targeted mission has never had a name to show
    anywhere.
    """

    @staticmethod
    def _attrs(region_id, names, is_room=True):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import (
            PrimeMissionEventSensor,
        )

        sensor = PrimeMissionEventSensor.__new__(PrimeMissionEventSensor)
        entry = MagicMock()
        entry.runtime_data.prime_room_names = names
        sensor._config_entry = entry

        event = MagicMock()
        region = MagicMock()
        region.region_id = region_id
        region.area = 12.0
        region.pass_count = 1
        event.room = region if is_room else None
        event.travel = None if is_room else region

        report = MagicMock()
        report.event = [event]
        report.mission_id = "m1"
        # `_report` is a property reading the coordinator, not a field.
        entry.runtime_data.prime_coordinator.data = report
        return sensor.extra_state_attributes

    def test_a_room_gets_its_name(self):
        attrs = self._attrs("10", {"10": "Kitchen"})

        assert attrs["current_room"] == "Kitchen"

    def test_a_zone_gets_its_name_too(self):
        """The case that had no name anywhere before."""
        attrs = self._attrs("101", {"101": "Guest Access Zone"})

        assert attrs["current_room"] == "Guest Access Zone"

    def test_the_id_stays_alongside(self):
        """It is what a command takes; dropping it would break anything
        already reading it."""
        attrs = self._attrs("10", {"10": "Kitchen"})

        assert attrs["current_room_id"] == "10"

    def test_an_unknown_id_adds_no_name(self):
        """Better no attribute than an invented one."""
        attrs = self._attrs("99", {"10": "Kitchen"})

        assert "current_room" not in attrs
        assert attrs["current_room_id"] == "99"


class TestBothGenerationsReportTheSameStates:
    """Classic and Prime deliberately spell their status differently --
    Classic translates, Prime reports the robot's own words, because
    every existing template matches on those.

    What must match is WHICH states exist, not how they read. Two were
    missing on both sides:

    `chargeMidMission` -- a robot topping up mid-run reports `charge`
    with its cycle still live. @chairstacker's timeline read Cleaning,
    Docked, Cleaning three times in one morning.

    `noContact` -- @utkjmitch's Combo (Y3-series) stopped transmitting at 36% and
    every entity held its last value for nine days. This sensor said
    `stuck` as if it had just arrived.
    """

    @staticmethod
    def _prime_phase(phase, cycle="none", last_ts=None):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        sensor = PrimePhaseSensor.__new__(PrimePhaseSensor)
        # A SimpleNamespace CARRYING WHAT THE COORDINATORS ACTUALLY
        # WRITE, and a bare namespace for the coordinator so a phantom
        # attribute read fails loudly.
        #
        # This used to set `coordinator.last_message_ts` on a MagicMock
        # -- the exact attribute the code asked for, on an object that
        # answers to anything. So the test described the code instead of
        # the coordinator, and `no_contact` could not fire on any Prime
        # robot while this passed (@utkjmitch, third instance of the
        # same seam).
        from types import SimpleNamespace

        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            last_mqtt_message_ts=last_ts,
            prime_status_coordinator=SimpleNamespace(),
        )
        sensor._config_entry = entry
        sensor._last_phase = phase

        status = MagicMock()
        status.phase = phase
        status.cycle = cycle
        state = MagicMock()
        state.clean_mission_status = status
        type(sensor)._current_state = property(lambda s: state)
        return sensor.native_value

    def test_prime_charging_mid_mission(self):
        import time

        assert self._prime_phase(
            "charge", cycle="clean", last_ts=time.time()
        ) == "charging_mid_mission"

    def test_prime_charging_between_runs_is_plain_charge(self):
        import time

        assert self._prime_phase(
            "charge", cycle="none", last_ts=time.time()
        ) == "charging"

    def test_prime_silence_overrides_the_frozen_phase(self):
        """Nine days of `stuck` is what this exists to stop."""
        import time

        assert self._prime_phase(
            "stuck", last_ts=time.time() - 9 * 86400
        ) == "no_contact"

    def test_prime_a_brief_gap_does_not_trigger_it(self):
        """A dropout must not rewrite the status mid-mission."""
        import time

        assert self._prime_phase(
            "run", cycle="clean", last_ts=time.time() - 300
        ) == "running"

    def test_both_new_states_are_offered_as_options(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        sensor = PrimePhaseSensor.__new__(PrimePhaseSensor)
        sensor._config_entry = MagicMock()

        assert "charging_mid_mission" in sensor.options
        assert "no_contact" in sensor.options


class TestAPrimeRegionSensorFindsItsTimestamp:
    """@chairstacker (#84): every per-region sensor read Unknown —
    including the zone a mission had just finished.

    `region_last_cleaned()` keys `{pmap_id}/{rid}` when the record knows
    a map, and bare `rid` when it does not. A Prime sensor carries no
    pmap, because region ids are unique across a Prime robot's maps —
    so `None/107` missed, bare `107` missed, and the value was never
    found.
    """

    @staticmethod
    def _sensor(history, pmap_id=None):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import (
            PrimeRegionLastCleanedSensor,
        )

        s = PrimeRegionLastCleanedSensor.__new__(PrimeRegionLastCleanedSensor)
        s._region_id = "107"
        s._pmap_id = pmap_id
        entry = MagicMock()
        entry.runtime_data.mission_store.region_last_cleaned.return_value = history
        s._config_entry = entry
        return s

    def test_a_qualified_record_is_found_without_a_pmap(self):
        """His exact case: the store knows the map, the sensor does
        not."""
        s = self._sensor({"MAP-A/107": "2026-08-27T07:10:00+00:00"})

        assert s.native_value is not None

    def test_a_bare_record_still_works(self):
        s = self._sensor({"107": "2026-08-27T07:10:00+00:00"})

        assert s.native_value is not None

    def test_another_region_is_not_borrowed(self):
        s = self._sensor({"MAP-A/108": "2026-08-27T07:10:00+00:00"})

        assert s.native_value is None

    def test_a_sensor_with_a_pmap_stays_strict(self):
        """The multi-map rule @dduff617 found: with a map of its own,
        matching on the region alone would show a clean on a different
        floor."""
        s = self._sensor(
            {"MAP-B/107": "2026-08-27T07:10:00+00:00"}, pmap_id="MAP-A"
        )

        assert s.native_value is None


class TestSilenceThatPredatesTheRestart:
    """A robot that went quiet BEFORE Home Assistant started never gets
    a message timestamp — nothing arrives to set one — so the staleness
    check had nothing to measure and reported the last known phase.

    @utkjmitch's robot was silent for nine days, and every restart in
    that window put it back to a confident `stuck`.

    It now falls back to when the config entry was set up. An hour of
    uptime with no message is an hour of silence, and that reading
    cannot false-positive at startup because the elapsed time there is
    zero.
    """

    @staticmethod
    def _phase(last_ts, setup_ts=0.0):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_prime import PrimePhaseSensor

        sensor = PrimePhaseSensor.__new__(PrimePhaseSensor)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            last_mqtt_message_ts=last_ts,
            setup_ts=setup_ts,
            prime_status_coordinator=SimpleNamespace(),
        )
        sensor._config_entry = entry
        sensor._last_phase = "stuck"

        status = MagicMock()
        status.phase = "stuck"
        status.cycle = "none"
        state = MagicMock()
        state.clean_mission_status = status
        type(sensor)._current_state = property(lambda s: state)
        return sensor.native_value

    def test_a_long_uptime_with_no_message_is_silence(self):
        """The nine-day robot, one hour after a restart."""
        import time

        assert self._phase(0.0, setup_ts=time.time() - 9 * 3600) == "no_contact"

    def test_a_fresh_start_is_not(self):
        """The failure mode a connection-state check would have had:
        every robot briefly unreachable before its first message."""
        import time

        assert self._phase(0.0, setup_ts=time.time() - 5) == "stuck"

    def test_a_real_timestamp_still_wins(self):
        import time

        assert self._phase(time.time() - 5) == "stuck"

    def test_a_stale_real_timestamp_still_fires(self):
        import time

        assert self._phase(time.time() - 9 * 86400) == "no_contact"
