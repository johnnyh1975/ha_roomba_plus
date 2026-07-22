"""Tests for sensor_prime.py -- the CLOUD_ONLY (V4/Prime) sensors.

See that module's own docstring for why these are separate, minimal
entity classes rather than routed through the existing SENSORS/
RoombaSensor machinery (sensor_core.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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

        assert sensor.native_value == "padPlate"


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
    async def test_adds_all_ten_prime_sensors(self):
        from custom_components.roomba_plus import sensor as sensor_mod
        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.sensor_prime import (
            PrimeBatterySensor,
            PrimeDetectedPadSensor,
            PrimeDockStatusSensor,
            PrimeFirmwareVersionSensor,
            PrimePadDryStatusSensor,
            PrimePadWashStatusSensor,
            PrimeRuntimeHoursSensor,
            PrimeSuctionLevelSensor,
        )

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        created = []

        def sync_add(entities, **kw):
            created.extend(entities)

        await sensor_mod.async_setup_entry(MagicMock(), entry, sync_add)

        assert len(created) == 10
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
