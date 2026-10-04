"""Room times learned from the cloud's room events, per cleaning mode.

4.2.15, @ScenicSystemsLLC's Braava m6: no pass setting for the cloud's
per-room estimates, no travel signal for room tracking to measure, so a
five-room mop run was estimated from the average of all recent missions
and read 99 % after an hour of two and a half.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.roomba_plus import room_times as rt


def _room(rid, ts, status=0, area=100, pass_area=80):
    return {"type": "room", "ts": ts, "ets": 99999,
            "room": {"rid": rid, "status": status, "area": area, "passArea": pass_area}}


def _travel(ts):
    return {"type": "travel", "ts": ts, "ets": 99999, "travel": {"dest": "room"}}


class TestCleaningMode:
    @pytest.mark.parametrize(
        ("value", "mode"),
        [(2, "vacuum"), (3, "vacuum"), (4, "mop"), (5, "mop"), (6, "vacuum_mop"),
         (32, "vacuum_mop"), (0, None), (1, None), (None, None), (True, None), ("2", None)],
    )
    def test_the_bits_not_the_number(self, value, mode):
        """Bit 0 is travel: vacuuming while driving is still vacuuming."""
        assert rt.cleaning_mode(value) == mode

    def test_a_braava_mops_when_its_status_says_nothing(self):
        state = {"sku": "m611020", "cleanMissionStatus": {"operatingMode": 0}}
        assert rt.current_cleaning_mode(state) == "mop"

    def test_the_status_wins(self):
        assert rt.current_cleaning_mode({"cleanMissionStatus": {"operatingMode": 3}}) == "vacuum"

    def test_a_region_can_carry_the_mode(self):
        state = {"lastCommand": {"regions": [{"params": {"operatingMode": 6}}]}}
        assert rt.current_cleaning_mode(state) == "vacuum_mop"

    def test_robot_defaults(self):
        assert rt.robot_default_mode({"sku": "m611020"}) == "mop"
        assert rt.robot_default_mode({"sku": "i755840"}) == "vacuum"
        assert rt.robot_default_mode({"sku": "c755020", "detectedPad": "reusable"}) is None


class TestTimelineRoomSeconds:
    def test_a_room_runs_until_the_next_room_or_drive(self):
        rec = {"timestamp": 3000, "timeline": {"finEvents": [
            {"type": "start", "ts": 0},
            _room("1", 100), _travel(700), _room("2", 760),
        ]}}
        assert rt.timeline_room_seconds(rec) == {"1": 600.0, "2": 2240.0}

    def test_events_newest_first_are_sorted(self):
        """The i3+ fixture lists its events newest first."""
        rec = {"timestamp": 3000, "timeline": {"finEvents": [
            _room("2", 760), _travel(700), _room("1", 100),
        ]}}
        assert rt.timeline_room_seconds(rec) == {"1": 600.0, "2": 2240.0}

    def test_ets_is_not_the_end_of_the_room(self):
        """`ets` is one mission-end value repeated on every event."""
        rec = {"timestamp": 1000, "timeline": {"finEvents": [_room("1", 100)]}}
        assert rt.timeline_room_seconds(rec) == {"1": 900.0}

    def test_passes_over_one_room_add_up(self):
        rec = {"timestamp": 2000, "timeline": {"finEvents": [
            _room("1", 100, status=1), _room("1", 500, status=0), _travel(900),
        ]}}
        assert rt.timeline_room_seconds(rec) == {"1": 800.0}

    def test_only_a_finished_pass_teaches(self):
        """A room closed at a cancelled mission's end (5) is a fraction
        of a room: the learned-figures rule is a finished pass."""
        rec = {"timestamp": 2000, "timeline": {"finEvents": [_room("1", 100, status=5)]}}
        assert rt.timeline_room_seconds(rec) == {}

    def test_a_room_only_reached_into_teaches_nothing(self):
        rec = {"timestamp": 2000, "timeline": {"finEvents": [
            _room("3", 100, area=106, pass_area=4), _room("6", 200, area=160, pass_area=97),
        ]}}
        assert rt.timeline_room_seconds(rec) == {"6": 1800.0}

    def test_implausible_figures_are_dropped(self):
        rec = {"timestamp": 20000, "timeline": {"finEvents": [
            _room("1", 100), _room("2", 110), _travel(200),
        ]}}
        assert rt.timeline_room_seconds(rec) == {"2": 90.0}
        rec = {"timestamp": 5 * 3600, "timeline": {"finEvents": [_room("1", 0)]}}
        assert rt.timeline_room_seconds(rec) == {}

    def test_a_local_record_ends_at_ended_at(self):
        rec = {"ended_at": "1970-01-01T00:16:40+00:00", "timeline": {"finEvents": [_room("1", 100)]}}
        assert rt.timeline_room_seconds(rec) == {"1": 900.0}

    def test_no_timeline_no_times(self):
        assert rt.timeline_room_seconds({}) == {}
        assert rt.timeline_room_seconds({"timeline": {"finEvents": [{"type": "room", "ts": "x"}]}}) == {}


class TestReadingTheTimes:
    def test_the_same_mode_first(self):
        """On a Combo, the vacuum time is not the mop estimate."""
        cache: dict[str, float] = {}
        rt.remember(cache, "Kitchen", 300, "vacuum")
        rt.remember(cache, "Kitchen", 900, "mop")
        assert rt.measured_room_seconds(cache, "Kitchen", "mop") == 900
        assert rt.measured_room_seconds(cache, "Kitchen", "vacuum") == 300

    def test_without_the_mode_the_smallest(self):
        cache: dict[str, float] = {}
        rt.remember(cache, "Kitchen", 300, "vacuum")
        rt.remember(cache, "Kitchen", 900, None)
        assert rt.measured_room_seconds(cache, "Kitchen", "mop") == 300
        assert rt.measured_room_seconds(cache, "Hall", "mop") is None

    def test_keys_from_4_2_14_are_read_by_their_bits(self):
        cache = {"Kitchen|measured|2": 300.0, "Kitchen|measured|2|count": 3.0}
        assert rt.measured_room_seconds(cache, "Kitchen", "vacuum") == 300.0

    def test_old_and_new_buckets_of_one_mode_are_averaged_not_raced(self):
        """Review: a 4.2.14 "2" bucket of 100 s would otherwise beat a new
        "vacuum" mean of 600 s forever."""
        cache = {"K|measured|2": 100.0, "K|measured|2|count": 1.0,
                 "K|measured|vacuum": 600.0, "K|measured|vacuum|count": 4.0}
        assert rt.measured_room_seconds(cache, "K", "vacuum") == 500.0

    def test_a_typical_room_in_the_same_mode(self):
        cache: dict[str, float] = {}
        rt.remember(cache, "A", 600, "mop")
        rt.remember(cache, "B", 1200, "mop")
        rt.remember(cache, "C", 100, "vacuum")
        assert rt.typical_room_seconds(cache, "mop") == 900
        assert rt.typical_room_seconds(cache, None) == pytest.approx(633.33, abs=0.01)
        assert rt.typical_room_seconds({}, "mop") is None


class TestLearning:
    @staticmethod
    def _rec(**extra):
        return {"id": "m_1", "timestamp": 3000, "timeline": {"finEvents": [
            _room("1", 100), _travel(700), _room("2", 760),
        ]}, **extra}

    def test_every_room_learned_once(self):
        cache: dict[str, float] = {}
        keys: list[str] = []
        rec = self._rec()
        names = {"1": "Office", "2": "Bath"}
        assert rt.learn_from_records([rec], cache, keys, lambda _r: names, "mop") == (2, 1)
        assert cache["Office|measured|mop"] == 600.0
        assert rt.learn_from_records([rec], cache, keys, lambda _r: names, "mop") == (0, 0)
        assert cache["Office|measured|mop|count"] == 1.0
        assert keys == ["id:m_1"]

    def test_the_start_command_names_the_mode(self):
        cache: dict[str, float] = {}
        rec = self._rec(cmd={"operatingMode": 2})
        rt.learn_from_records([rec], cache, [], lambda _r: {"1": "O", "2": "B"}, "mop")
        assert "O|measured|vacuum" in cache

    def test_a_combo_region_names_the_mode(self):
        cache: dict[str, float] = {}
        rec = self._rec(cmd={"regions": [{"params": {"operatingMode": 4}}]})
        rt.learn_from_records([rec], cache, [], lambda _r: {"1": "O", "2": "B"}, None)
        assert "O|measured|mop" in cache

    def test_unnamed_rooms_wait_for_the_map(self):
        cache: dict[str, float] = {}
        keys: list[str] = []
        rec = self._rec()
        assert rt.learn_from_records([rec], cache, keys, lambda _r: {"1": "Office"}, None) == (0, 0)
        assert keys == []

    def test_a_record_without_rooms_is_remembered_so_it_is_not_read_again(self):
        keys: list[str] = []
        rec = {"missionId": "01ABC", "timestamp": 10, "timeline": {"finEvents": [_travel(5)]}}
        assert rt.learn_from_records([rec], {}, keys, lambda _r: {}, None) == (0, 1)
        assert keys == ["missionId:01ABC"]

    def test_the_same_mission_under_a_new_record_is_not_counted_twice(self):
        """The key is the cloud's mission id: a record rebuilt without
        any mark (re-adopted, re-imported) is still the same mission."""
        cache: dict[str, float] = {}
        keys: list[str] = []
        names = {"1": "O", "2": "B"}
        rt.learn_from_records([self._rec(missionId="01X")], cache, keys, lambda _r: names, None)
        rt.learn_from_records([self._rec(missionId="01X", id="m_other")], cache, keys, lambda _r: names, None)
        assert cache["O|measured|unknown|count"] == 1.0

    def test_the_remembered_missions_are_bounded(self):
        keys = [f"id:{i}" for i in range(rt.MAX_LEARNED_KEYS)]
        rt.learn_from_records([self._rec(id="new")], {}, keys, lambda _r: {"1": "O", "2": "B"}, None)
        assert len(keys) == rt.MAX_LEARNED_KEYS and keys[-1] == "id:new" and keys[0] == "id:1"

    @pytest.mark.parametrize("extra", [
        {"result": "stuck_and_resumed"}, {"result": "cancelled"},
        {"classified_result": "error"}, {"pauseM": 12},
    ])
    def test_a_mission_whose_clock_ran_without_cleaning_teaches_nothing(self, extra):
        cache: dict[str, float] = {}
        assert rt.learn_from_records([self._rec(**extra)], cache, [], lambda _r: {"1": "O", "2": "B"}, None) == (0, 1)
        assert cache == {}

    def test_a_broken_store_does_not_stop_a_refresh(self):
        assert rt.learn_via(SimpleNamespace()) == (0, 0)
        assert rt.learn_via(SimpleNamespace(learn_room_times=lambda *a: "junk")) == (0, 0)
        assert rt.learn_via(SimpleNamespace(learn_room_times=lambda *a: (1, 2))) == (1, 2)


class TestTheMissionStoreLearns:
    def test_names_come_from_the_missions_own_map(self):
        from custom_components.roomba_plus.mission_store import MissionStore

        store = MissionStore()
        rec = {"id": "m_1", "timestamp": 3000, "pmaps_info": [{"pmap_id": "up"}],
               "timeline": {"finEvents": [_room("1", 100), _travel(700)]}}
        store._records = [rec]
        cache: dict[str, float] = {}
        assert store.learn_room_times(cache, [], {"1": "Kitchen"}, {"up": {"1": "Study"}}, "mop") == (1, 1)
        assert cache == {"Study|measured|mop": 600.0, "Study|measured|mop|count": 1.0}


class TestTheEstimatesUseThem:
    """_compute_room_time_estimates: the mission's mode first, and a
    typical room where a room has no figure yet."""

    @staticmethod
    def _entry(cache, state):
        from custom_components.roomba_plus.const import CONF_BLID  # noqa: F401 - import check only

        cc = SimpleNamespace(regions=[])
        runtime = SimpleNamespace(
            cloud_coordinator=cc,
            robot_profile_store=SimpleNamespace(room_estimate_cache=cache),
            roomba_reported_state=lambda: state,
            prime_status_coordinator=None,
        )
        return SimpleNamespace(runtime_data=runtime)

    def test_a_braava_gets_its_mop_times_and_a_typical_room(self):
        from custom_components.roomba_plus.sensor_rooms import _compute_room_time_estimates

        cache: dict[str, float] = {}
        rt.remember(cache, "Bedroom", 1800, "mop")
        rt.remember(cache, "Bedroom", 400, "vacuum")
        rt.remember(cache, "Hallway", 1200, "mop")
        entry = self._entry(cache, {"sku": "m611020", "cleanMissionStatus": {"operatingMode": 0}})

        assert _compute_room_time_estimates(entry, ["Bedroom", "Hallway", "Closet"]) == [1800, 1200, 1500]

    def test_without_any_measurement_nothing_is_invented(self):
        from custom_components.roomba_plus.sensor_rooms import _compute_room_time_estimates

        entry = self._entry({}, {"sku": "m611020"})
        assert _compute_room_time_estimates(entry, ["Bedroom"]) == [None]


class TestOddInput:
    """Every branch that guards against a malformed record or state."""

    def test_modes_from_odd_places(self):
        assert rt.current_cleaning_mode({"lastCommand": {"operatingMode": 4}}) == "mop"
        assert rt.current_cleaning_mode({"lastCommand": {"regions": ["x", {"params": {}}]}}) is None
        assert rt.current_cleaning_mode("junk") is None  # type: ignore[arg-type]
        assert rt.robot_default_mode("junk") is None  # type: ignore[arg-type]
        assert rt.record_cleaning_mode({"cmd": {"operatingMode": 0}}, "mop") == "mop"

    def test_odd_end_times(self):
        assert rt._record_end({"ended_at": "not a date"}) is None
        assert rt._record_end({"ended_at": "2026-01-01T00:00:00"}) == 1767225600.0
        assert rt._record_end({}) is None

    def test_odd_events_are_skipped(self):
        rec = {"timestamp": 1000, "timeline": {"finEvents": [
            "junk",
            {"type": "room", "ts": 1, "room": "junk"},
            {"type": "room", "ts": 2, "room": {"status": 0}},
            {"type": "room", "ts": 100, "room": {"rid": "1", "status": 0, "area": 100, "passArea": 80}},
        ]}}
        assert rt.timeline_room_seconds(rec) == {"1": 900.0}

    def test_odd_records_are_skipped(self):
        assert rt.learn_from_records(["junk", {"id": "x"}], {}, [], lambda _r: {}, None) == (0, 0)
        assert rt.learn_from_records([{"timeline": {}}], {}, [], lambda _r: {}, None) == (0, 0)
        assert rt.learn_via(SimpleNamespace(learn_room_times=lambda *a: (True, 1))) == (0, 0)

    def test_odd_cache_entries_are_ignored(self):
        cache = {"A|measured|mop": True, "B|other": 5.0, "C|measured|nonsense": 50.0}
        assert rt.measured_room_seconds(cache, "A", "mop") is None
        assert rt.measured_room_seconds(cache, "C", "mop") == 50.0


class TestRegionVisits:
    """4.2.21: per room or zone of one mission -- time spent, whether it
    was cleaned, when the robot was done with it."""

    @staticmethod
    def _room(rid, ts, ets=None, status=0, kind="room"):
        key = "rid" if kind == "room" else "zid"
        ev = {"type": kind, "ts": ts, kind: {key: rid, "status": status}}
        if ets is not None:
            ev["ets"] = ets
        return ev

    def test_a_visit_ends_at_the_next_event_not_a_constant_ets(self):
        """The older capture stamps every event with the mission's end as
        `ets`; the next drive is where the room really ended."""
        from custom_components.roomba_plus.room_times import region_visits

        events = [
            {"type": "start", "ts": 1696660066},
            self._room("5", 1696660084, ets=1696662000),
            {"type": "travel", "ts": 1696660945, "ets": 1696662000},
        ]

        visit = region_visits(events, 1696662000)["5"]
        assert visit["seconds"] == 861.0
        assert visit["ended_at"] == 1696660945.0

    def test_the_last_room_ends_at_its_own_ets_else_the_mission_end(self):
        from custom_components.roomba_plus.room_times import region_visits

        with_ets = region_visits([self._room("5", 100, ets=400)], 900)["5"]
        without = region_visits([self._room("5", 100)], 900)["5"]

        assert with_ets["ended_at"] == 400.0
        assert without["ended_at"] == 900.0

    def test_a_revisit_dates_the_room_by_its_last_visit(self):
        from custom_components.roomba_plus.room_times import region_visits

        events = [
            self._room("5", 100),
            {"type": "evac", "ts": 400},
            self._room("5", 500),
            {"type": "travel", "ts": 700},
        ]

        visit = region_visits(events, 900)["5"]
        assert visit["seconds"] == 500.0
        assert visit["ended_at"] == 700.0

    def test_a_zone_ends_the_room_before_it(self):
        from custom_components.roomba_plus.room_times import region_visits

        events = [self._room("5", 100), self._room("9", 300, kind="zone")]

        assert region_visits(events, 900)["5"]["ended_at"] == 300.0

    def test_an_event_at_the_same_second_does_not_end_the_room(self):
        """The drive is stamped with the room's start second; it is the
        drive into it."""
        from custom_components.roomba_plus.room_times import region_visits

        events = [
            {"type": "travel", "ts": 100},
            self._room("5", 100),
            {"type": "travel", "ts": 400},
        ]

        assert region_visits(events, 900)["5"]["seconds"] == 300.0

    def test_classic_reads_rooms_apart_from_zones_with_the_same_id(self):
        """Room 3 and zone 3 are different places on Classic."""
        from custom_components.roomba_plus.room_times import record_region_visits

        rec = {"ended_at": "2026-09-28T17:00:00+00:00", "timeline": {"finEvents": [
            self._room("3", 0),
            {"type": "travel", "ts": 600},
            self._room("3", 700, kind="zone"),
            {"type": "travel", "ts": 900},
        ]}}

        visit = record_region_visits(rec)["3"]
        assert visit["seconds"] == 600.0
        assert visit["ended_at"] == 600.0
