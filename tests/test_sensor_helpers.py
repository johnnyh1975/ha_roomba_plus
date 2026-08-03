

class TestReadinessStateDecoding:
    """The readiness sensor was untested and wrong.

    It treated notReady as a bitmask: a nine-entry table of exact values
    plus a bit-by-bit fallback that assembled labels like "Updating map,
    Pending task" out of a premise that does not hold.

    The iRobot Home app reads it as a scalar index into a 73-state enum
    with an offset above 10. Six of the nine entries were wrong against
    that; only 0 and 15 held up. Nothing caught it because nothing
    tested it -- the whole function had no coverage.
    """

    def _value(self, not_ready):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import _not_ready_value

        entity = MagicMock()
        entity.clean_mission_status = {"notReady": not_ready}
        return _not_ready_value(entity)

    def test_ready(self):
        assert self._value(0) == "Ready"

    def test_the_six_that_were_wrong(self):
        """Each of these showed something the app does not say."""
        assert self._value(2) == "Wheel drop both"      # was "Uneven ground"
        assert self._value(16) == "Bin full"            # was "Bumped unexpectedly"
        assert self._value(31) == "Schedule no clock"   # was "Fill tank"
        assert self._value(39) == "Charge timeout"      # was "Pending"
        assert self._value(48) == "Safety fault hardware"  # was "Path blocked"
        assert self._value(68) == "Off dock"            # was "Updating map"

    def test_the_map_state_is_67_not_68(self):
        """The seed of the whole bitmask story: 68 was labelled "Updating
        map", and 68 & 64 is true, so a bit test looked like it worked.
        The state that means the map is updating is 67."""
        assert self._value(67) == "Downloading map"
        assert self._value(68) != "Downloading map"

    def test_the_two_that_were_right_still_are(self):
        assert self._value(15) == "Insufficient charge"  # was "Low battery"

    def test_the_offset_applies_above_ten(self):
        """Wire 25 is index 22. Reading the wire value straight out of a
        73-entry list would give a different state."""
        assert self._value(25) == "Map version mismatch"

    def test_an_unlisted_value_keeps_its_number(self):
        """No decomposition into invented parts. A state this project
        does not know should say so."""
        assert self._value(200) == "Not ready (200)"

    def test_a_non_integer_does_not_raise(self):
        assert self._value("busy") == "Not ready (busy)"
        assert self._value(None) == "Ready"
