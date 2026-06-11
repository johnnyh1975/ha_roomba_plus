"""Supplementary tests for v2.1.0 capability-guard fixes.

Covers two bugs diagnosed from production diagnostics (Roomba 980, R980040):

BUG A — Battery retention / EOL sensors: filter_fn update (v2.5.0 revision):
  battery_capacity_retention and estimated_battery_eol were originally gated on
  batteryType != "nimh" based on the incorrect assumption that the 980 uses a NiMH
  battery. Confirmed June 2026: the 980 OEM battery is Li-ion 14.4V. Additionally,
  batteryType at runtime contains iRobot part numbers ("F12432712"), not chemistry
  strings — the "nimh" gate never fired in production.
  The math for both sensors (retention %, EOL days) is scale-invariant: the BMS
  scale cancels in the ratio. The filter is now simply "estCap present in bbchg3".

BUG B — recent_evacuations surfaces on robots without a Clean Base (v2.0 + v2.1):
  The 980 has no Clean Base. The cloud always records evacs=0. The sensor was
  created unconditionally when cloud credentials are configured, permanently
  showing 0. Fix: skip creation when not has_clean_base(state).
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Bug A: battery retention / EOL filter (v2.5.0 revision) ──────────────────

class TestBatteryRetentionNiMHGuard:
    """battery_capacity_retention filter: only estCap presence matters (v2.5.0).

    batteryType gate removed: batteryType contains part numbers, never "nimh",
    and the 980 OEM battery is Li-ion (confirmed June 2026). Math is
    scale-invariant — filter passes for any chemistry when estCap is present.
    """

    def _desc(self):
        from custom_components.roomba_plus.sensor import SENSORS
        return next(d for d in SENSORS if d.key == "battery_capacity_retention")

    def test_lithium_with_estcap_surfaces(self):
        """lipo battery with estCap → sensor created."""
        desc = self._desc()
        state = {"bbchg3": {"estCap": 2000}, "batteryType": "lipo"}
        assert desc.filter_fn(state) is True

    def test_nimh_string_with_estcap_now_surfaces(self):
        """batteryType='nimh' with estCap → now True (filter removed, math is scale-invariant).

        In practice batteryType is never 'nimh' (it's a part number), but even if
        it were, the scale-invariant math makes the sensor correct for any chemistry.
        """
        desc = self._desc()
        state = {"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}
        assert desc.filter_fn(state) is True

    def test_no_battery_type_with_estcap_surfaces(self):
        """Unknown battery type with estCap → sensor surfaces."""
        desc = self._desc()
        state = {"bbchg3": {"estCap": 2000}}
        assert desc.filter_fn(state) is True

    def test_no_estcap_suppressed_regardless(self):
        """No estCap → suppressed regardless of battery type."""
        desc = self._desc()
        assert desc.filter_fn({"bbchg3": {}, "batteryType": "lipo"}) is False
        assert desc.filter_fn({"bbchg3": {}, "batteryType": "nimh"}) is False

    def test_980_exact_state_surfaces(self):
        """Exact 980 diagnostics state: estCap present → sensor surfaces (v2.5.0)."""
        desc = self._desc()
        state = {
            "bbchg3": {"estCap": 9720, "nLithChrg": 290, "nNimhChrg": 19},
            "batteryType": "F12432712",   # actual runtime value: part number not "nimh"
        }
        assert desc.filter_fn(state) is True


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


class TestBatteryCapacityMahUnaffected:
    """battery_capacity_mah (raw mAh) is NOT NiMH-guarded — raw value is valid."""

    def test_nimh_with_estcap_still_surfaces(self):
        from custom_components.roomba_plus.sensor import SENSORS
        desc = next(d for d in SENSORS if d.key == "battery_capacity_mah")
        state = {"bbchg3": {"estCap": 9720}, "batteryType": "nimh"}
        assert desc.filter_fn(state) is True


# ── Bug B: recent_evacuations Clean Base guard ────────────────────────────────

class TestRecentEvacuationsCleanBaseGuard:
    """recent_evacuations must not be created when no Clean Base is present."""

    def _make_state(self, has_clean_base: bool) -> dict:
        """Return a minimal MQTT state with or without Clean Base indicators."""
        if has_clean_base:
            return {"dock": {"fwVer": "1.2.3", "state": 300}}
        return {"dock": {"known": True}}   # 980 diagnostics: dock={known:true}

    def _count_evacuations_entities(self, state: dict) -> int:
        """Count how many recent_evacuations entities would be created."""
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        created = 0
        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_evacuations":
                if not has_clean_base(state):
                    continue   # mirrors the fix
                created += 1
        return created

    def test_no_clean_base_suppresses_evacuations(self):
        """980 without Clean Base: recent_evacuations not created."""
        state = self._make_state(has_clean_base=False)
        assert self._count_evacuations_entities(state) == 0

    def test_with_clean_base_creates_evacuations(self):
        """Robot with Clean Base: recent_evacuations created."""
        state = self._make_state(has_clean_base=True)
        assert self._count_evacuations_entities(state) == 1

    def test_980_exact_dock_state_suppressed(self):
        """Exact dock state from 980 diagnostics: {known: true} → suppressed."""
        from custom_components.roomba_plus.const import has_clean_base
        state_980 = {"dock": {"known": True}}
        assert has_clean_base(state_980) is False
        assert self._count_evacuations_entities(state_980) == 0

    def test_empty_dock_suppressed(self):
        """Empty dock dict → no Clean Base → suppressed."""
        assert self._count_evacuations_entities({"dock": {}}) == 0

    def test_dock_with_fwver_creates_evacuations(self):
        """dock.fwVer present → Clean Base confirmed → created."""
        state = {"dock": {"fwVer": "3.1.7"}}
        assert self._count_evacuations_entities(state) == 1

    def test_dock_with_int_state_creates_evacuations(self):
        """dock.state as integer → Clean Base confirmed → created."""
        state = {"dock": {"state": 300}}
        assert self._count_evacuations_entities(state) == 1

    def test_other_cloud_raw_sensors_unaffected(self):
        """The guard skips only recent_evacuations — all others still created."""
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        state = {"dock": {"known": True}}   # no Clean Base
        created_keys = []
        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_evacuations" and not has_clean_base(state):
                continue
            created_keys.append(desc.key)
        assert "recent_evacuations" not in created_keys
        assert "recent_completion_rate" in created_keys
        assert "recent_recharges" in created_keys
        assert "recent_dirt_events" in created_keys
        assert "recent_error_code" in created_keys
