

class TestAFinishedMissionReadsOneHundred:
    """@chairstacker (#72 follow-up): a favourite that completed
    successfully — 4 minutes, 20 sq ft, app header "Cleaning
    Completed" — froze at **34%**.

    The percentage is elapsed time against a per-room estimate, and his
    estimate was three times the real duration. Holding the last value
    (a38) was right for an aborted run and wrong for a completed one.
    """

    @staticmethod
    def _sensor(phase, last):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import (
            RoombaMissionProgress,
        )

        sensor = object.__new__(RoombaMissionProgress)
        sensor._last_progress = last
        sensor._last_mission_id = "m1"
        entry = MagicMock()
        entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"phase": phase}
        }
        sensor._config_entry = entry
        return sensor, RoombaMissionProgress

    def test_docking_after_work_completes_it(self):
        sensor, RoombaMissionProgress = self._sensor("charge", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 100

    def test_returning_home_completes_it_too(self):
        sensor, RoombaMissionProgress = self._sensor("hmPostMsn", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 100

    def test_a_stopped_mission_keeps_what_it_reached(self):
        """`stop` is a mission halted where it stood. 34% is the honest
        figure there, and claiming 100 would be a lie."""
        sensor, RoombaMissionProgress = self._sensor("stop", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 34

    def test_nothing_run_yet_stays_unknown(self):
        sensor, RoombaMissionProgress = self._sensor("charge", None)

        assert RoombaMissionProgress.native_value.fget(sensor) is None
