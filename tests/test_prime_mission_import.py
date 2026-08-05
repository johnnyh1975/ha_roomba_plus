"""Turning a Prime robot's mission history into MissionStore records.

Four sensors -- clean streak, last mission, last duration, area cleaned
today -- read that store, and only the Classic path fills it. On Prime
they read "unknown" forever, which was reported as four separate faults
and is one.

Tested against the vendor's OWN sample: 20 real entries shipped in the
Prime app's raw resources. That matters more than the count of tests --
a mapping checked only against fixtures we invented would agree with
whatever we assumed.
"""

import json
import pathlib

import pytest

_SAMPLE = pathlib.Path(__file__).parent / "fixtures" / "vendor_mission_history.json"


def _entries():
    from roombapy_prime.models.mission_history import parse_mission_history

    return parse_mission_history(json.loads(_SAMPLE.read_text()))


class TestAgainstTheVendorSample:
    def test_every_entry_becomes_a_record(self):
        """The first version of this reached for a field that does not
        exist on the model, so every entry would have been skipped for
        want of an id -- silently, leaving the sensors exactly as dead as
        before."""
        from custom_components.roomba_plus.prime_mission_import import (
            records_from_history,
        )

        entries = _entries()
        records = records_from_history(entries, "BLID")

        assert len(entries) == 20
        assert len(records) == 20

    def test_ids_are_unique_and_stable(self):
        """Built from robot id and mission number, because the payload
        carries no mission id at all. Stable so a re-import does not
        duplicate; unique so two robots in one Home Assistant cannot
        collide."""
        from custom_components.roomba_plus.prime_mission_import import (
            records_from_history,
        )

        first = records_from_history(_entries(), "BLID")
        second = records_from_history(_entries(), "BLID")

        ids = [r["id"] for r in first]
        assert len(set(ids)) == len(ids)
        assert ids == [r["id"] for r in second]

    def test_records_are_ordered_oldest_first(self):
        """The store trims to a maximum and keeps what it was given
        last. An unsorted import would decide which missions to keep by
        whatever order the server answered in."""
        from custom_components.roomba_plus.prime_mission_import import (
            records_from_history,
        )

        starts = [r["started_at"] for r in records_from_history(_entries(), "BLID")]

        assert starts == sorted(starts)

    def test_the_store_accepts_them(self):
        """The real check: not that the shape looks right, but that the
        store's own validator takes it."""
        from custom_components.roomba_plus.mission_store import MissionStore
        from custom_components.roomba_plus.prime_mission_import import (
            records_from_history,
        )

        store = MissionStore()
        accepted = sum(
            store.append_validated(r) for r in records_from_history(_entries(), "BLID")
        )

        assert accepted == 20


class TestTheResultVocabularyIsLoadBearing:
    """clean_streak counts only "completed" and "stuck_and_resumed", so
    a mission translated to the wrong word silently breaks a streak or
    extends one."""

    def _result(self, done_raw):
        from types import SimpleNamespace

        from custom_components.roomba_plus.prime_mission_import import (
            record_from_history_entry,
        )

        entry = SimpleNamespace(
            raw={"nMssn": 1}, robot_id="B", start_time=1, timestamp=2,
            minutes_running=5, duration_m=9, square_feet_covered=100,
            error_code=None, done_raw=done_raw, initiator="app", mission_id=None,
        )
        return record_from_history_entry(entry, "B")["result"]

    def test_ok_is_a_completed_mission(self):
        assert self._result("ok") == "completed"

    def test_a_user_ended_mission_is_cancelled_not_failed(self):
        """Deliberate, not a failure -- and Classic calls it that."""
        assert self._result("usrEnd") == "cancelled"

    def test_stuck_keeps_its_name(self):
        assert self._result("stuck") == "stuck"

    def test_an_unknown_outcome_passes_through_unchanged(self):
        """It should look unrecognised rather than be forced into one of
        ours."""
        assert self._result("somethingNew") == "somethingNew"


class TestDurationIsCleaningTimeNotWallClock:
    """Four minute fields exist and they are not interchangeable. A
    robot that returns to charge halfway through reports a wall-clock
    duration several times its cleaning time, and "last mission
    duration" means the second one."""

    def _record(self, **kwargs):
        from types import SimpleNamespace

        from custom_components.roomba_plus.prime_mission_import import (
            record_from_history_entry,
        )

        entry = SimpleNamespace(
            raw={"nMssn": 1}, robot_id="B", start_time=1, timestamp=2,
            square_feet_covered=100, error_code=None, done_raw="ok",
            initiator="app", mission_id=None, **kwargs,
        )
        return record_from_history_entry(entry, "B")

    def test_running_minutes_win(self):
        assert self._record(minutes_running=9, duration_m=40)["duration_min"] == 9

    def test_wall_clock_is_the_fallback(self):
        assert self._record(minutes_running=None, duration_m=40)["duration_min"] == 40


class TestTimestampsAndMissingData:
    def _record(self, **kwargs):
        from types import SimpleNamespace

        from custom_components.roomba_plus.prime_mission_import import (
            record_from_history_entry,
        )

        base = dict(
            raw={"nMssn": 1}, robot_id="B", start_time=1589884703,
            timestamp=1589885000, minutes_running=5, duration_m=9,
            square_feet_covered=100, error_code=None, done_raw="ok",
            initiator="app", mission_id=None,
        )
        base.update(kwargs)
        return record_from_history_entry(SimpleNamespace(**base), "B")

    def test_epochs_become_iso_strings(self):
        assert self._record()["started_at"].startswith("2020-05-19")

    def test_milliseconds_are_recognised(self):
        """Not expected -- the vendor sample is in seconds -- but a
        timestamp read as 1970 would put every mission on the same day
        and quietly ruin "area cleaned today"."""
        seconds = self._record(start_time=1589884703)["started_at"]
        millis = self._record(start_time=1589884703000)["started_at"]

        assert seconds == millis

    def test_an_entry_with_no_id_is_dropped_rather_than_faked(self):
        assert self._record(raw={}, robot_id=None, mission_id=None) is None

    def test_zones_are_null_not_empty(self):
        """This endpoint does not carry them. An empty list would claim
        the mission cleaned no rooms."""
        assert self._record()["zones"] is None


class TestTheImportIsWiredIntoSetup:
    """The store already existed on the Prime path and nothing ever put
    a mission in it. Four sensors read "unknown" forever as a result."""

    async def _run(self, *, robot, store):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus import _async_import_prime_missions

        entry = MagicMock()
        entry.entry_id = "E1"
        entry.runtime_data.prime_robot = robot
        await _async_import_prime_missions(MagicMock(), entry, store)

    def _store(self):
        from custom_components.roomba_plus.mission_store import MissionStore

        return MissionStore()

    def _robot(self, payload):
        from unittest.mock import AsyncMock

        robot = AsyncMock()
        robot.blid = "BLID"
        robot.get_mission_history.return_value = payload
        return robot

    @pytest.mark.asyncio
    async def test_the_vendor_sample_lands_in_the_store(self):
        store = self._store()

        await self._run(robot=self._robot(json.loads(_SAMPLE.read_text())), store=store)

        assert len(store.records) == 20

    @pytest.mark.asyncio
    async def test_a_second_import_adds_nothing(self):
        """Ids are built from robot id and mission number so a re-import
        is a no-op -- otherwise every restart would double the history."""
        store = self._store()
        payload = json.loads(_SAMPLE.read_text())

        await self._run(robot=self._robot(payload), store=store)
        await self._run(robot=self._robot(payload), store=store)

        assert len(store.records) == 20

    @pytest.mark.asyncio
    async def test_a_cloud_failure_leaves_setup_intact(self):
        """An empty store is where this has been all along. A setup that
        failed because the history could not be fetched would be worse
        than the problem being fixed."""
        robot = self._robot(None)
        robot.get_mission_history.side_effect = TimeoutError()
        store = self._store()

        await self._run(robot=robot, store=store)

        assert store.records == []

    @pytest.mark.asyncio
    async def test_a_robot_that_is_not_there_is_not_an_error(self):
        await self._run(robot=None, store=self._store())

    @pytest.mark.asyncio
    async def test_a_robot_with_no_history_is_fine(self):
        store = self._store()

        await self._run(robot=self._robot([]), store=store)

        assert store.records == []
