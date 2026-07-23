"""Tests for entity.py's IRobotEntity base class.

v3.4.2 NULL-REGRESSION — this base class had NO dedicated test file before
this addition, despite being the constructor every single entity in this
integration runs through. Found via the same systematic sweep that fixed
select.py/cloud_coordinator.py's active_pmapv_details null-guard gap:
`hwPartsRev`/`dock` sub-objects being explicitly `null` (not just absent)
in a robot's local MQTT state — the same confirmed-real class of bug as
`cleanMissionStatus: None`/`bbrun: None` elsewhere in this codebase — would
previously raise AttributeError right in DeviceInfo construction inside
__init__, before HA even finishes setting up the entity. A single affected
robot would have broken every entity's setup, not just one.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roomba_plus.entity import IRobotEntity


def _make_roomba(reported: dict) -> MagicMock:
    r = MagicMock()
    r.master_state = {"state": {"reported": reported}}
    return r


class TestIRobotEntityInitNullGuards:
    def test_init_survives_explicit_null_hwpartsrev(self):
        """hwPartsRev: null must not crash DeviceInfo construction."""
        roomba = _make_roomba({"hwPartsRev": None, "sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info["serial_number"] is None

    def test_init_survives_missing_hwpartsrev(self):
        """hwPartsRev absent entirely — the pre-existing, already-safe case,
        kept here so both null and missing are covered side by side."""
        roomba = _make_roomba({"sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info["serial_number"] is None

    def test_init_survives_explicit_null_hwpartsrev_no_mac_fallback(self):
        """hwPartsRev: null with no top-level `mac` fallback either —
        mac_address resolution must not raise and should end up None."""
        roomba = _make_roomba({"hwPartsRev": None, "sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info.get("connections") in (None, set())

    def test_dock_tank_level_survives_explicit_null_dock(self):
        roomba = _make_roomba({"dock": None, "sku": "m611020"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity.dock_tank_level is None

    def test_dock_tank_level_normal_case_unaffected(self):
        roomba = _make_roomba({"dock": {"tankLvl": 42}, "sku": "m611020"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity.dock_tank_level == 42


class TestPrimeDeviceInfo:
    """REAL BUG FOUND AND FIXED (architecture review, not a field
    report): every Prime entity passes roomba=None (no roombapy Roomba
    object exists for a cloud-only device) -- IRobotEntity.__init__
    previously always built DeviceInfo from roomba_reported_state(None)
    == {}, regardless of connection type. Every Prime robot's device
    page showed a generic "Roomba XXXX" name, no model, no serial, no
    firmware version -- despite PrimeFirmwareVersionSensor and others
    already showing the SAME underlying data correctly as individual
    sensors. Device-level info and sensor-level info are entirely
    separate code paths; only the sensor one had ever been built
    correctly for Prime."""

    def _make_prime_config_entry(self, title="Bogdana", serial_info=None, software_shadow=None):
        config_entry = MagicMock()
        config_entry.title = title
        config_entry.runtime_data.prime_serial_info = serial_info
        config_entry.runtime_data.prime_status_coordinator.data = (
            {"rw-software": software_shadow} if software_shadow is not None else {}
        )
        return config_entry

    def test_name_comes_from_config_entry_title(self):
        """config_entry.title has ALWAYS correctly held the real robot
        name since this project's very first Prime release (set at
        onboarding time) -- no migration needed for already-configured
        installs, unlike model/serial/firmware below."""
        config_entry = self._make_prime_config_entry(title="Bogdana")

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["name"] == "Bogdana"

    def test_falls_back_to_blid_when_title_is_empty(self):
        config_entry = self._make_prime_config_entry(title="")

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["name"] == "Roomba D123"

    def test_model_and_serial_from_prime_serial_info(self):
        from roombapy_prime.models import RobotSerialInfo

        serial_info = RobotSerialInfo(serial_number="SN123456", sku="G185020", family="Roomba Combo")
        config_entry = self._make_prime_config_entry(serial_info=serial_info)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["serial_number"] == "SN123456"
        assert entity._attr_device_info["model"] == "G185020"
        assert entity._attr_device_info["model_id"] == "G185020"

    def test_model_falls_back_to_family_when_sku_missing(self):
        from roombapy_prime.models import RobotSerialInfo

        serial_info = RobotSerialInfo(serial_number="SN123456", sku=None, family="Roomba Combo")
        config_entry = self._make_prime_config_entry(serial_info=serial_info)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["model"] == "Roomba Combo"

    def test_missing_serial_info_degrades_gracefully_not_raises(self):
        config_entry = self._make_prime_config_entry(serial_info=None)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["serial_number"] is None
        assert entity._attr_device_info["model"] is None

    def test_firmware_version_from_coordinator_data(self):
        config_entry = self._make_prime_config_entry(software_shadow={"softwareVer": "p25-405+9.3.7"})

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["sw_version"] == "p25-405+9.3.7"

    def test_missing_coordinator_data_degrades_gracefully_not_raises(self):
        config_entry = self._make_prime_config_entry()
        config_entry.runtime_data.prime_status_coordinator = None

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["sw_version"] is None

    def test_no_config_entry_at_all_falls_back_to_original_classic_behavior(self):
        """roomba=None with NO config_entry either (shouldn't happen in
        practice for a real Prime entity, but must not crash) -- same
        as the original, pre-fix behavior."""
        entity = IRobotEntity(None, "BLID123")

        assert entity._attr_device_info["name"] == "Roomba D123"
        assert entity._attr_device_info["model"] is None

    def test_manufacturer_is_always_irobot(self):
        config_entry = self._make_prime_config_entry()

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["manufacturer"] == "iRobot"
