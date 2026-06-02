"""Capability-guard fix for v2.0.0: recent_evacuations Clean Base guard.

BUG: recent_evacuations surfaces on robots without a Clean Base (e.g. Roomba 980).
The cloud always records evacs=0 when there is no Clean Base, producing a
permanently-zero sensor that is misleading and wastes an entity slot.

Fix: skip creation of recent_evacuations when not has_clean_base(state).

Diagnosed from production diagnostics (Roomba 980 R980040, v2.0.0):
  dock: {"known": true}  →  has_clean_base = False  →  entity must not be created.
"""


class TestRecentEvacuationsCleanBaseGuard:
    """recent_evacuations must not be created when no Clean Base is present."""

    def _count_evacuations_entities(self, state: dict) -> int:
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        created = 0
        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_evacuations":
                if not has_clean_base(state):
                    continue
                created += 1
        return created

    def test_980_dock_state_suppressed(self):
        """Exact dock state from 980 diagnostics: {known: true} → suppressed."""
        assert self._count_evacuations_entities({"dock": {"known": True}}) == 0

    def test_empty_dock_suppressed(self):
        assert self._count_evacuations_entities({"dock": {}}) == 0

    def test_no_dock_suppressed(self):
        assert self._count_evacuations_entities({}) == 0

    def test_clean_base_with_fwver_created(self):
        assert self._count_evacuations_entities({"dock": {"fwVer": "3.1.7"}}) == 1

    def test_clean_base_with_state_int_created(self):
        assert self._count_evacuations_entities({"dock": {"state": 300}}) == 1

    def test_other_sensors_unaffected(self):
        """All other CloudRawSensors still created when no Clean Base."""
        from custom_components.roomba_plus.const import has_clean_base
        from custom_components.roomba_plus.sensor import CLOUD_RAW_SENSORS
        state = {"dock": {"known": True}}
        created_keys = [
            desc.key for desc in CLOUD_RAW_SENSORS
            if not (desc.key == "recent_evacuations" and not has_clean_base(state))
        ]
        assert "recent_evacuations" not in created_keys
        assert "recent_completion_rate" in created_keys
        assert "recent_recharges" in created_keys
        assert "recent_dirt_events" in created_keys
        assert "recent_error_code" in created_keys
