

class TestDirtCauseFinallyHasItsInput:
    """`_classify_dirt_cause` has held a real distinction since v3.2 and
    **could never be called**: it needs a dirt TREND, and this module
    produced only a per-record density.

    Half a feature rather than a disconnected one — the logic was
    written before its input existed. Found by listing private helpers
    whose name appears exactly once in the component, the same check
    that found three genuinely orphaned functions the same day.
    """

    def _records(self, dirt_series, sqft=100):
        return [
            {"dirt": d, "sqft": sqft, "startTime": 1786000000 - i * 86400}
            for i, d in enumerate(dirt_series)
        ]

    def _trend(self, dirt_series):
        from custom_components.roomba_plus.sensor_cloud import (
            _raw_dirt_density_trend,
        )

        return _raw_dirt_density_trend(self._records(dirt_series))

    def test_a_dirtier_recent_window_reads_rising(self):
        assert self._trend([9] * 5 + [5] * 10) == "rising"

    def test_a_cleaner_recent_window_reads_falling(self):
        assert self._trend([5] * 5 + [9] * 10) == "falling"

    def test_a_small_change_is_stable(self):
        """Same 10% threshold as the speed trend. Two windows
        disagreeing about the same history would be worse than either
        being slightly wrong, and the pair is compared to each other."""
        assert self._trend([10] * 5 + [10.5] * 10) == "stable"

    def test_too_few_records_is_unknown(self):
        assert self._trend([9, 5, 9]) == "unknown"

    def test_the_gap_filter_matches_the_speed_trend_exactly(self):
        """**Not a test of what the filter should do — a test that both
        trends do the same thing.**

        Walking newest-first, the filter skips the three records
        *following* a gap in list order, which are the three *older*
        ones. Whether that is the right end is a question about
        `_raw_cleaning_speed_trend`, which has shipped that way since
        v3.2 and is not being changed here.

        What matters for the classification is that the two windows
        agree: they are compared against each other, and a filter that
        excluded different records in each would make the comparison
        meaningless."""
        import inspect

        from custom_components.roomba_plus import sensor_cloud

        source = inspect.getsource(sensor_cloud)
        speed = source.index("def _raw_cleaning_speed_trend")
        dirt = source.index("def _raw_dirt_density_trend")
        for fragment in ("> 7:", "skip_remaining = 3", "skip_remaining -= 1"):
            assert fragment in source[speed:speed + 2500], fragment
            assert fragment in source[dirt:dirt + 2500], fragment


    def test_rising_dirt_with_falling_speed_reads_as_brush_wear(self):
        """Debris the brush is not picking up: the sensor re-fires on
        the same mess and the robot slows down carrying it."""
        from custom_components.roomba_plus.sensor_cloud import (
            _classify_dirt_cause,
        )

        assert _classify_dirt_cause("rising", "declining") == "brush_wear"

    def test_rising_dirt_with_steady_speed_reads_as_a_dirty_floor(self):
        from custom_components.roomba_plus.sensor_cloud import (
            _classify_dirt_cause,
        )

        assert _classify_dirt_cause("rising", "stable") == "floor_dirty"

    def test_the_sensor_surfaces_both(self):
        """The wiring, not the parts — which is the half that was
        missing three times over on the same day."""
        import inspect

        from custom_components.roomba_plus import sensor_cloud

        source = inspect.getsource(sensor_cloud)

        assert 'attrs["dirt_trend"] = dirt_trend' in source
        assert 'attrs["dirt_cause"] = _classify_dirt_cause(' in source
