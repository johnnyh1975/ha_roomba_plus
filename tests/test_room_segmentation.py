

class TestConstrictionWidthIsPhysical:
    """v3.2.2 — the distance transform is short by the robot's radius.

    A cell counts as visited when the robot's CENTRE was inside its
    footprint, so the covered area stops one radius short of every
    obstacle. The distance field therefore measures clearance for a
    point robot; the real half-width of a gap is that value plus r.

    Measured on this project's own grid archives, saddle values run
    54-172 mm. As widths those would be narrower than the robot itself,
    which cannot be true of a gap it demonstrably drove through.
    Corrected they land at 448-684 mm: doors and furniture gaps.
    """

    @staticmethod
    def _two_rooms_with_a_gap():
        """Two blocks joined by a one-cell neck."""
        cells = {}
        for x in range(0, 8):
            for y in range(0, 8):
                cells[(x, y)] = 1.0
        for x in range(12, 20):
            for y in range(0, 8):
                cells[(x, y)] = 1.0
        for x in range(8, 12):
            cells[(x, 4)] = 1.0
        return cells

    def test_a_door_is_wider_than_the_robot(self):
        """The check that makes the correction non-optional: an
        uncorrected saddle can report a gap the robot could not fit
        through, which is self-contradictory for a gap it drove."""
        from custom_components.roomba_plus.room_segmentation import segment_rooms

        result = segment_rooms(self._two_rooms_with_a_gap())

        assert result.doors, "the neck should register as a constriction"
        for d in result.doors:
            assert d["width_mm"] > 340.0, "narrower than the chassis"

    def test_the_correction_is_exactly_the_radius(self):
        from custom_components.roomba_plus.room_segmentation import segment_rooms

        result = segment_rooms(self._two_rooms_with_a_gap(), robot_radius_mm=170.0)

        for d in result.doors:
            assert d["clearance_mm"] == d["saddle_mm"] + 170.0
            assert d["width_mm"] == 2.0 * d["clearance_mm"]

    def test_the_raw_value_is_still_reported(self):
        """Stored results hold the uncorrected figure; dropping it would
        make old and new records incomparable."""
        from custom_components.roomba_plus.room_segmentation import segment_rooms

        result = segment_rooms(self._two_rooms_with_a_gap())

        for d in result.doors:
            assert "saddle_mm" in d
