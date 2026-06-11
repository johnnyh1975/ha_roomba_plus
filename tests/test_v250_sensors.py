"""Tests for v2.5.0 CloudRawSensor.available (R3).

Covers:
  - available=False when has_cloud is False (cloud not configured)
  - available=True when cloud active and coordinator last_update_success=True
  - available=False when coordinator last_update_success=False
  - available=False when coordinator data is None
"""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from custom_components.roomba_plus.sensor import CloudRawSensor, CloudRawSensorDescription
from homeassistant.components.sensor import SensorDeviceClass


# ── Helpers ───────────────────────────────────────────────────────────────────

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
