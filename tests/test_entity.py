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
