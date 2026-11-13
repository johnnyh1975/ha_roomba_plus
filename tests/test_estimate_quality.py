"""How good a time estimate is (4.6.0, B11)."""
from __future__ import annotations

from custom_components.roomba_plus import estimate_quality as eq
from custom_components.roomba_plus.room_times import remember


def _cache(*seconds):
    cache: dict[str, float] = {}
    for value in seconds:
        remember(cache, "Kitchen", value, "vacuum")
    return cache


class TestRoomQuality:
    def test_many_steady_runs_are_high(self):
        assert eq.room_quality(_cache(600, 610, 590, 605, 595), "Kitchen", "vacuum") == {
            "runs": 5, "spread_sec": 7.9, "rating": "high",
        }

    def test_three_looser_runs_are_medium(self):
        assert eq.room_quality(_cache(500, 600, 700), "Kitchen", "vacuum")["rating"] == "medium"

    def test_one_run_is_low(self):
        quality = eq.room_quality(_cache(600), "Kitchen", "vacuum")
        assert quality == {"runs": 1, "spread_sec": None, "rating": "low"}

    def test_a_wild_spread_is_low(self):
        assert eq.room_quality(_cache(100, 900, 300, 1200, 50), "Kitchen", "vacuum")["rating"] == "low"

    def test_no_measurement_is_none(self):
        assert eq.room_quality({}, "Kitchen", "vacuum") == {
            "runs": 0, "spread_sec": None, "rating": "none",
        }

    def test_a_figure_from_before_has_runs_and_no_spread(self):
        cache = {"Kitchen|measured|vacuum": 600.0, "Kitchen|measured|vacuum|count": 7.0}
        assert eq.room_quality(cache, "Kitchen", "vacuum") == {
            "runs": 7, "spread_sec": None, "rating": "low",
        }


class TestMissionEstimateError:
    def test_the_median_of_the_newest(self):
        records = [
            {"estimated_duration_min": 60, "duration_min": 66},   # +10 %
            {"estimated_duration_min": 50, "duration_min": 40},   # -20 %
            {"estimated_duration_min": 40, "duration_min": 42},   # +5 %
            {"duration_min": 30},
            {"estimated_duration_min": 0, "duration_min": 30},
            "junk",
        ]
        assert eq.mission_estimate_error(records) == {"missions": 3, "median_error_pct": 10.0}

    def test_the_window(self):
        records = [{"estimated_duration_min": 10, "duration_min": 20}] + [
            {"estimated_duration_min": 10, "duration_min": 10}
        ] * 3
        assert eq.mission_estimate_error(records, window=3) == {
            "missions": 3, "median_error_pct": 0.0,
        }

    def test_none_without_any(self):
        assert eq.mission_estimate_error([]) == {"missions": 0, "median_error_pct": None}
        assert eq.mission_estimate_error(None) == {"missions": 0, "median_error_pct": None}
