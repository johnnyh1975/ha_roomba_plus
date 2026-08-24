

class TestDayScopedSensorsRollOver:
    """@chairstacker (#78): "Area cleaned today" kept yesterday's figure
    past midnight, and a reload cleared it.

    That pairing is the diagnosis. The calculation was always right — it
    reads today's date on every call. Nothing called it: the value comes
    from `mission_store`, written at mission end, so between the last run
    of one day and the first of the next there is no event to recompute
    on. A reload recomputes everything, which is why that worked.
    """

    def test_area_cleaned_today_is_registered_for_midnight(self):
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        assert "area_cleaned_today" in RoombaSensor._MIDNIGHT_SENSORS

    def test_the_value_function_itself_was_never_wrong(self):
        """Guards against 'fixing' the calculation, which is correct —
        the fault was that nobody called it."""
        import datetime
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor_helpers import (
            _area_cleaned_today,
        )
        from homeassistant.util import dt as dt_util

        today = dt_util.now()
        store = MagicMock()
        store.query.return_value = [
            {"area_sqft": 100.0, "started_at": today.isoformat()},
            {
                "area_sqft": 999.0,
                "started_at": (today - datetime.timedelta(days=1)).isoformat(),
            },
        ]

        # Yesterday's 999 must not appear in today's figure.
        assert _area_cleaned_today(store) == round(100.0 * 0.092903, 1)

    def test_every_date_reading_sensor_is_covered(self):
        """A GUARD THAT FINDS THEM, not one that restates a list.

        The first version of this matched function names containing
        "today". It passed while `clean_streak` and `battery_age_days`
        had exactly the same fault -- neither has "today" in its name,
        and one of them had to be found by a tester running a 13-day
        experiment.

        So this looks for the actual cause: a value function whose
        source reads the CURRENT DATE. Such a value changes because time
        passed, and nothing about the robot passing time generates an
        event, so it needs a rollover or it goes quietly stale.
        """
        import inspect
        import re

        from custom_components.roomba_plus import mission_store, sensor_helpers
        from custom_components.roomba_plus.sensor_core import SENSORS, RoombaSensor

        date_reading: set[str] = set()
        for module in (sensor_helpers, mission_store):
            for name, obj in vars(module).items():
                if not callable(obj) or isinstance(obj, type):
                    continue
                try:
                    src = inspect.getsource(obj)
                except (OSError, TypeError):
                    continue
                if re.search(r"now\(\)\s*\.date\(\)", src):
                    date_reading.add(name.lstrip("_"))

        # Methods on MissionStore need the class walked too.
        for name, obj in vars(mission_store.MissionStore).items():
            try:
                src = inspect.getsource(obj)
            except (OSError, TypeError):
                continue
            if re.search(r"now\(\)\s*\.date\(\)", src):
                date_reading.add(name.lstrip("_"))

        assert date_reading, "the detector itself stopped working"

        keys = {d.key for d in SENSORS}
        for name in date_reading:
            if name in keys:
                assert name in RoombaSensor._MIDNIGHT_SENSORS, (
                    f"{name} reads today's date but has no midnight rollover "
                    f"-- it will be stale from every midnight until something "
                    f"unrelated writes its state"
                )

    def test_the_three_known_ones_are_registered(self):
        """Named explicitly, because the detector above could break
        silently and the point is that these three are covered."""
        from custom_components.roomba_plus.sensor_core import RoombaSensor

        assert {"area_cleaned_today", "clean_streak", "battery_age_days"} <= (
            RoombaSensor._MIDNIGHT_SENSORS
        )
