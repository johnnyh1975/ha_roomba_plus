"""How much battery a mission used (4.6.0, B12)."""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.roomba_plus import mission_battery as mb


def _data():
    return SimpleNamespace(mission_battery=None, prime_mission_end_figures={})


class TestStartAndEnd:
    def test_the_drop_is_the_use(self):
        data = _data()
        mb.note_start(data, 95, 12)
        mb.note_level(data, 80)
        assert mb.finish(data, 61) == {
            "battery_start_pct": 95, "battery_end_pct": 61,
            "battery_used_pct": 34, "battery_recharged": False,
        }
        assert data.mission_battery is None

    def test_a_rise_on_the_way_is_a_recharge(self):
        data = _data()
        mb.note_start(data, 40)
        mb.note_level(data, 15)
        mb.note_level(data, 70)
        fields = mb.finish(data, 60)
        assert fields["battery_used_pct"] is None
        assert fields["battery_recharged"] is True

    def test_a_recharge_the_caller_knows(self):
        data = _data()
        mb.note_start(data, 90)
        assert mb.finish(data, 50, recharged=True)["battery_used_pct"] is None

    def test_no_start_no_figure(self):
        data = _data()
        mb.note_level(data, 50)
        assert mb.finish(data, 40) == {
            "battery_start_pct": None, "battery_end_pct": 40,
            "battery_used_pct": None, "battery_recharged": None,
        }

    def test_nonsense_levels_are_not_levels(self):
        data = _data()
        mb.note_start(data, True)
        assert data.mission_battery is None
        mb.note_start(data, 150)
        assert data.mission_battery is None
        mb.note_start(data, 90)
        mb.note_level(data, "x")
        assert mb.finish(data, None)["battery_used_pct"] is None

    def test_a_higher_end_without_a_seen_rise_is_no_figure(self):
        data = _data()
        mb.note_start(data, 50)
        assert mb.finish(data, 60)["battery_used_pct"] is None


class TestPerSquareMetre:
    def test_per_m2(self):
        record = {"area_sqft": 215.278, "battery_used_pct": 20}
        assert mb.per_m2(record) == round(20 / (215.278 * mb.SQFT_TO_M2), 3)

    def test_without_area_or_use_none(self):
        assert mb.per_m2({"battery_used_pct": 20}) is None
        assert mb.per_m2({"area_sqft": 0, "battery_used_pct": 20}) is None
        assert mb.per_m2({"area_sqft": 100, "battery_used_pct": None}) is None

    def test_apply(self):
        record = {"area_sqft": 107.639}
        mb.apply(record, {"battery_used_pct": 10})
        assert record["battery_used_pct_per_m2"] == round(10 / (107.639 * mb.SQFT_TO_M2), 3)


class TestPrimeHandOver:
    def test_the_sync_takes_what_the_coordinator_kept(self):
        data = _data()
        mb.keep_prime(data, 77, {"battery_used_pct": 12, "estimated_duration_min": 30.0})
        record = {"nMssn": 77, "area_sqft": None}
        mb.take_prime(data, record)
        assert record["battery_used_pct"] == 12
        assert record["estimated_duration_min"] == 30.0
        assert data.prime_mission_end_figures == {}

    def test_another_mission_takes_nothing(self):
        data = _data()
        mb.keep_prime(data, 77, {"battery_used_pct": 12})
        record = {"nMssn": 78}
        mb.take_prime(data, record)
        assert "battery_used_pct" not in record

    def test_no_number_nothing_kept(self):
        data = _data()
        mb.keep_prime(data, None, {"battery_used_pct": 12})
        assert data.prime_mission_end_figures == {}
        mb.take_prime(data, {})

    def test_only_the_newest_are_kept(self):
        data = SimpleNamespace()
        for n in range(30):
            mb.keep_prime(data, n, {"battery_used_pct": n})
        assert len(data.prime_mission_end_figures) == 20
        assert "0" not in data.prime_mission_end_figures
