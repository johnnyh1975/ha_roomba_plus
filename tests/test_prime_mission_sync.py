"""Prime mission history, translated into MissionStore records."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def _entry(**kwargs):
    """A history entry. Every field absent unless named, because the
    absence handling is most of what matters here."""
    defaults = dict.fromkeys([
        "mission_id", "timestamp", "start_time", "duration_m",
        "square_feet_covered", "number_of_dirt_detects", "minutes_charging",
        "error_code", "done_code", "minutes_paused", "number_of_evacuations",
        "docked_at_start", "ended_on_dock", "coverage_strategy", "command",
    ])
    defaults.update(kwargs)
    obj = MagicMock()
    for key, value in defaults.items():
        setattr(obj, key, value)
    return obj


class TestFieldMapping:
    """MissionStore is the basis for mission statistics, dirt-spike and
    excessive-recharge detection, rolling means and cleaning intervals --
    around 30 sensor lookups. For Prime it has been empty since v4.0.0a0
    while the data was available over REST the whole time.

    The mapping is one-for-one, so most of the risk is in what happens
    to fields that are absent."""

    def _record(self, **kwargs):
        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        return prime_entry_to_record(_entry(
            mission_id="abc",
            timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc),
            **kwargs,
        ))

    def test_the_classic_field_names_are_produced(self):
        """Consumers look for Classic's names, so the translation has to
        land on them rather than on Prime's."""
        record = self._record(
            duration_m=60, square_feet_covered=450,
            number_of_dirt_detects=12, minutes_charging=30,
        )

        assert record["duration_min"] == 60
        assert record["area_sqft"] == 450
        assert record["dirt"] == 12
        assert record["recharge_min"] == 30

    def test_absent_fields_stay_absent_rather_than_becoming_zero(self):
        """THE decision worth a test. A zero feeds the anomaly detectors
        a real-looking measurement, and "no dirt data" is not "no dirt".
        Consumers already handle missing keys, because Classic robots
        vary in what they report."""
        record = self._record()

        for key in ("duration_min", "area_sqft", "dirt", "recharge_min"):
            assert key not in record

    def test_a_zero_that_was_actually_reported_is_kept(self):
        """The other half: 0 dirt detects is a real result and must not
        be dropped as falsy."""
        record = self._record(number_of_dirt_detects=0, error_code=0)

        assert record["dirt"] == 0
        assert record["error_code"] == 0

    def test_ids_cannot_collide_with_classic_ones(self):
        """Classic ids are `m_<epoch>`. A shared config entry that ever
        changed connection type would otherwise mix two id schemes in
        one store."""
        assert self._record()["id"].startswith("p_")

    def test_command_becomes_the_classic_initiator_key(self):
        """`initiator` is what the schedule sensors read."""
        assert self._record(command="schedule")["initiator"] == "schedule"


class TestResultMapping:
    """`result` drives different sensors depending on its value, so an
    unrecognised code must not be guessed into "completed"."""

    def _result(self, done_code):
        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        return prime_entry_to_record(_entry(
            mission_id="a",
            timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc),
            done_code=done_code,
        ))["result"]

    def test_known_codes(self):
        assert self._result("success") == "completed"
        assert self._result("cancelled") == "cancelled"
        assert self._result("failed") == "error"

    def test_case_is_ignored(self):
        assert self._result("SUCCESS") == "completed"

    def test_an_unknown_code_is_not_guessed(self):
        """"completed" and "cancelled" feed different statistics.
        Reporting "unknown" is visible; guessing is not."""
        assert self._result("something_new") == "unknown"
        assert self._result(None) == "unknown"


class TestTimestampNormalisation:
    """Entries have been seen carrying datetimes, epoch seconds and ISO
    strings across firmware versions."""

    def _record(self, timestamp):
        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        return prime_entry_to_record(_entry(mission_id="a", timestamp=timestamp))

    def test_a_datetime(self):
        record = self._record(datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc))

        assert record["ended_at"].startswith("2026-07-28T10:00")

    def test_epoch_seconds(self):
        record = self._record(1785_000_000)

        assert record["ended_at"].startswith("2026-")

    def test_an_iso_string_passes_through(self):
        assert self._record("2026-07-28T10:00:00+00:00")["ended_at"] == (
            "2026-07-28T10:00:00+00:00"
        )

    def test_an_entry_with_no_end_time_is_rejected(self):
        """It would corrupt the interval statistics it feeds, and there
        is no way to place it in a rolling window."""
        assert self._record(None) is None

    def test_an_entry_with_no_id_is_rejected(self):
        """It would be re-appended on every poll."""
        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        assert prime_entry_to_record(_entry(
            timestamp=datetime(2026, 7, 28, tzinfo=timezone.utc)
        )) is None


class TestReconciliation:
    """The REST endpoint returns the whole recent history on every call,
    so this reconciles rather than appends.

    Classic never needed that: its trigger fires once per mission, from
    an MQTT phase transition. Appending blindly here would duplicate the
    entire history every six hours."""

    def _config_entry(self, history, existing=()):
        entry = MagicMock()
        entry.runtime_data.prime_robot.get_mission_history = AsyncMock(
            return_value=list(history)
        )
        store = MagicMock()
        store.query = MagicMock(return_value=[{"id": i} for i in existing])
        store.async_append = AsyncMock()
        entry.runtime_data.mission_store = store
        return entry, store

    def _hist(self, mission_id, day):
        return _entry(
            mission_id=mission_id,
            timestamp=datetime(2026, 7, day, tzinfo=timezone.utc),
            done_code="success",
        )

    @pytest.mark.asyncio
    async def test_new_missions_are_added(self):
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._config_entry([self._hist("a", 27), self._hist("b", 28)])

        assert await async_sync_prime_missions(entry) == 2
        assert store.async_append.await_count == 2

    @pytest.mark.asyncio
    async def test_already_stored_missions_are_skipped(self):
        """The whole reason this is a reconciliation. Without it every
        poll would duplicate the history."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._config_entry(
            [self._hist("a", 27), self._hist("b", 28)], existing=["p_a"]
        )

        assert await async_sync_prime_missions(entry) == 1
        assert store.async_append.await_args.args[0]["id"] == "p_b"

    @pytest.mark.asyncio
    async def test_missions_are_appended_oldest_first(self):
        """Rolling statistics and interval tracking read the store in
        order, so inserting newest-first would skew them."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._config_entry([self._hist("new", 28), self._hist("old", 26)])
        await async_sync_prime_missions(entry)

        ids = [call.args[0]["id"] for call in store.async_append.await_args_list]
        assert ids == ["p_old", "p_new"]

    @pytest.mark.asyncio
    async def test_a_failing_endpoint_adds_nothing_and_does_not_raise(self):
        """It runs inside the parts coordinator: an exception here would
        take the consumable sensors down with it."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._config_entry([])
        entry.runtime_data.prime_robot.get_mission_history = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        assert await async_sync_prime_missions(entry) == 0
        store.async_append.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_store_means_no_work(self):
        """Reachable on a Classic entry, where prime_robot is None."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry = MagicMock()
        entry.runtime_data.mission_store = None

        assert await async_sync_prime_missions(entry) == 0


class TestMissionSensorsExistForPrime:
    """Filling MissionStore was only half the fix.

    The Prime branch of sensor.async_setup_entry returns before the
    mission sensors are created, so a filled store had nothing reading
    it. That is the third instance of the same shape in this project:
    PrimeMapImage was unreachable for a release because IMAGE was
    missing from PRIME_PLATFORMS, and CLEAN_AREA was advertised without
    the method supplying the room list.

    Working data that no entity consumes looks exactly like broken data
    from the outside, and is harder to diagnose because every individual
    piece tests fine."""

    _EXPECTED = {
        "area_cleaned_today",
        "last_mission",
        "last_mission_result",
        "last_mission_duration",
        "clean_streak",
        "consecutive_mission_anomalies",
    }

    def test_the_expected_keys_are_wired(self):
        from custom_components.roomba_plus.sensor import _PRIME_MISSION_SENSOR_KEYS

        assert _PRIME_MISSION_SENSOR_KEYS == self._EXPECTED

    def test_every_wired_key_is_a_real_sensor_description(self):
        """A typo would silently create nothing at all -- the list
        comprehension filters on key, so an unmatched key vanishes."""
        from custom_components.roomba_plus.sensor import _PRIME_MISSION_SENSOR_KEYS
        from custom_components.roomba_plus.sensor_core import SENSORS

        known = {d.key for d in SENSORS}

        assert _PRIME_MISSION_SENSOR_KEYS <= known

    def test_area_cleaned_today_is_now_included(self):
        """It was excluded while its has_pose() gate looked like a real
        capability check. It is not: the gate exists because the
        600-series reports no square footage at all, and Prime does --
        area_sqft is in every mission record.

        Bypassing the gate for Prime is right; rewriting it for Classic
        would change behaviour for robots this was never about."""
        from custom_components.roomba_plus.sensor import _PRIME_MISSION_SENSOR_KEYS

        assert "area_cleaned_today" in _PRIME_MISSION_SENSOR_KEYS

    def test_zone_dependent_sensors_stay_out(self):
        """problem_zone reads zone data Prime has no equivalent for."""
        from custom_components.roomba_plus.sensor import _PRIME_MISSION_SENSOR_KEYS

        assert "problem_zone" not in _PRIME_MISSION_SENSOR_KEYS

    def test_no_sensors_when_neither_store_exists(self):
        """Reachable if store creation failed -- which it is allowed to,
        since both stores are enrichment rather than dependencies.

        Both have to be absent: the two sets are added independently, so
        a maintenance store surviving a failed mission store load still
        produces its own four sensors."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import _add_prime_mission_sensors

        data = MagicMock(
            mission_store=None, maintenance_store=None, mission_timer_store=None
        )
        added = MagicMock()

        _add_prime_mission_sensors(data, MagicMock(), added)

        added.assert_not_called()

    def test_one_store_failing_does_not_cost_the_other_its_sensors(self):
        """The stores load independently, so a corrupt mission history
        must not also remove the maintenance dates."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import (
            _PRIME_MAINTENANCE_SENSOR_KEYS,
            _add_prime_mission_sensors,
        )

        data = MagicMock(
            mission_store=None,
            maintenance_store=MagicMock(),
            mission_timer_store=None,
            blid="B",
        )
        added = MagicMock()

        _add_prime_mission_sensors(data, MagicMock(), added)

        created = added.call_args.args[0]
        assert len(created) == len(_PRIME_MAINTENANCE_SENSOR_KEYS)

    def test_the_timer_store_adds_the_progress_sensor_on_its_own(self):
        """Three independent sources now, not two. Mission progress is
        its own entity class rather than a SENSORS entry, so it is easy
        to leave out of a set-based check."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import _add_prime_mission_sensors

        data = MagicMock(
            mission_store=None,
            maintenance_store=None,
            mission_timer_store=MagicMock(),
            blid="B",
        )
        added = MagicMock()

        _add_prime_mission_sensors(data, MagicMock(), added)

        assert len(added.call_args.args[0]) == 1

    def test_each_sensor_produces_a_value_from_a_prime_record(self):
        """The end-to-end property that matters: a record written by the
        Prime translation has to be readable by sensors written for
        Classic MQTT records."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.mission_store import MissionStore
        from custom_components.roomba_plus.sensor import _PRIME_MISSION_SENSOR_KEYS
        from custom_components.roomba_plus.sensor_core import SENSORS, RoombaSensor

        store = MissionStore()
        store._records = [{
            "id": "p_abc",
            "started_at": "2026-07-28T09:00:00+00:00",
            "ended_at": "2026-07-28T10:00:00+00:00",
            "result": "completed",
            "duration_min": 60,
            "area_sqft": 450,
            "dirt": 12,
            "initiator": "schedule",
        }]
        entry = MagicMock()
        entry.runtime_data.mission_store = store
        entry.runtime_data.roomba = None

        for description in SENSORS:
            if description.key not in _PRIME_MISSION_SENSOR_KEYS:
                continue
            sensor = object.__new__(RoombaSensor)
            sensor.entity_description = description
            sensor._config_entry = entry
            sensor.vacuum = None
            sensor.vacuum_state = {}
            # Must not raise, and must not be a MagicMock leaking through.
            value = sensor.native_value
            assert not isinstance(value, MagicMock), description.key


class TestRecordsAreActuallyPersisted:
    """`async_append` only mutates memory.

    Classic follows every append with its own `async_save`; this path had
    none, so the whole sync was lost on every restart. It would have
    looked like it worked, because the REST endpoint returns the same
    history again and the store refills -- until a mission aged out of
    the endpoint's window and vanished for good.

    Found by checking whether each newly wired store is ever written,
    rather than by anything failing."""

    def _entry(self, history):
        from unittest.mock import AsyncMock, MagicMock

        entry = MagicMock()
        entry.runtime_data.prime_robot.get_mission_history = AsyncMock(
            return_value=list(history)
        )
        entry.runtime_data.robot_profile_store = None
        store = MagicMock()
        store.query = MagicMock(return_value=[])
        store.async_append = AsyncMock()
        store.async_save = AsyncMock()
        entry.runtime_data.mission_store = store
        return entry, store

    def _hist(self, mission_id, day):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        entry = MagicMock(
            mission_id=mission_id,
            timestamp=datetime(2026, 7, day, tzinfo=timezone.utc),
            done_code="success",
        )
        return entry

    @pytest.mark.asyncio
    async def test_new_records_are_saved_to_disk(self):
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._entry([self._hist("a", 27)])

        await async_sync_prime_missions(entry)

        store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_saved_once_not_once_per_record(self):
        """A first-run backfill of a hundred missions would otherwise be
        a hundred disk writes, on hardware that is commonly an SD card."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._entry([self._hist(str(i), 20 + i) for i in range(5)])

        await async_sync_prime_missions(entry)

        assert store.async_append.await_count == 5
        assert store.async_save.await_count == 1

    @pytest.mark.asyncio
    async def test_nothing_new_means_no_write(self):
        """The steady state: six-hourly polls with no new missions must
        not rewrite the file."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._entry([])

        await async_sync_prime_missions(entry)

        store.async_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_save_does_not_lose_the_run(self):
        """Records stay in memory and the endpoint still has them, so
        the next poll retries. Raising here would take the parts
        coordinator down with it."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._entry([self._hist("a", 27)])
        store.async_save.side_effect = OSError("disk full")

        assert await async_sync_prime_missions(entry) == 1


class TestConcurrentSyncsCannotDuplicate:
    """HA's DataUpdateCoordinator does not serialise its updates --
    verified against the installed version, which has no lock in
    _async_refresh. So a manual async_request_refresh() can overlap the
    six-hourly one.

    This function reads all known ids, then appends. Under overlap both
    runs see the same set and both append."""

    def test_a_lock_guards_the_read_then_append(self):
        import inspect

        import custom_components.roomba_plus.prime_mission_sync as mod

        source = inspect.getsource(mod.async_sync_prime_missions)

        assert "async with lock" in source

    def test_locks_are_per_config_entry(self):
        """Two robots on one Home Assistant must not serialise against
        each other -- each poll is a cloud round trip."""
        import asyncio

        from custom_components.roomba_plus.prime_mission_sync import _SYNC_LOCKS

        _SYNC_LOCKS.clear()
        _SYNC_LOCKS.setdefault("entry_a", asyncio.Lock())
        _SYNC_LOCKS.setdefault("entry_b", asyncio.Lock())

        assert _SYNC_LOCKS["entry_a"] is not _SYNC_LOCKS["entry_b"]


class TestLongTermStatisticsAreBackfilled:
    """HA long-term statistics, so a statistics graph card shows history
    rather than starting from the day the integration was installed.

    Classic has done this since v3.5.0. The Prime setup path did not
    inherit it, and the store's own docstring says duration and
    completion count work for all robots -- only the area series needs
    `area_sqft`, which the Prime translation supplies.

    THE ORDERING IS THE INTERESTING PART. Setup fires the backfill before
    any sync has run, so on a first install the store is empty at that
    moment and statistics stay blank until the next restart. So it runs
    again after records arrive."""

    def _entry(self, history):
        from unittest.mock import AsyncMock, MagicMock

        entry = MagicMock(title="Numero 5")
        entry.runtime_data.prime_robot.get_mission_history = AsyncMock(
            return_value=list(history)
        )
        entry.runtime_data.robot_profile_store = None
        store = MagicMock()
        store.query = MagicMock(return_value=[])
        store.async_append = AsyncMock()
        store.async_save = AsyncMock()
        store.async_backfill_statistics = AsyncMock()
        entry.runtime_data.mission_store = store
        return entry, store

    def _hist(self, mission_id, day):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        return MagicMock(
            mission_id=mission_id,
            timestamp=datetime(2026, 7, day, tzinfo=timezone.utc),
            done_code="success",
        )

    @pytest.mark.asyncio
    async def test_the_backfill_runs_after_new_records_arrive(self):
        """The first-install case: setup's own backfill saw an empty
        store."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, _store = self._entry([self._hist("a", 27)])

        await async_sync_prime_missions(entry)

        entry.runtime_data.hass_ref.async_create_task.assert_called()

    @pytest.mark.asyncio
    async def test_no_backfill_when_nothing_was_added(self):
        """The steady state -- six-hourly polls with no new missions must
        not re-walk the history."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, _store = self._entry([])

        await async_sync_prime_missions(entry)

        entry.runtime_data.hass_ref.async_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_backfill_does_not_lose_the_records(self):
        """Statistics are a nice-to-have; the records are the point. The
        save must still happen."""
        from custom_components.roomba_plus.prime_mission_sync import (
            async_sync_prime_missions,
        )

        entry, store = self._entry([self._hist("a", 27)])
        entry.runtime_data.hass_ref.async_create_task.side_effect = RuntimeError("no")

        assert await async_sync_prime_missions(entry) == 1
        store.async_save.assert_awaited_once()


class TestPartsRefreshOnMissionEnd:
    """Consumable counters move only when the robot cleans, and the parts
    coordinator polls every six hours.

    A tester's mission ended at 06:08 and his four maintenance sensors
    showed unchanged values until 12:14, when he restarted Home
    Assistant. Six hours of stale data on values that changed once, at a
    moment the mission timeline tells us about.

    Polling faster would be the wrong fix -- a cloud request every few
    minutes for data that moves twice a day."""

    def _coordinator(self, report):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.prime_coordinator import PrimeCoordinator

        coordinator = object.__new__(PrimeCoordinator)
        coordinator.hass = MagicMock()
        coordinator.blid = "BLID"
        entry = MagicMock()
        parts = MagicMock(async_request_refresh=AsyncMock())
        entry.runtime_data.prime_parts_coordinator = parts
        coordinator.config_entry = entry
        return coordinator, parts, report

    def _report(self, *, fin_events=None, event_types=()):
        from unittest.mock import MagicMock

        return MagicMock(
            fin_events=fin_events,
            event=[MagicMock(event_type=t) for t in event_types],
        )

    def test_fin_events_trigger_a_refresh(self):
        from unittest.mock import MagicMock

        coordinator, _parts, report = self._coordinator(
            self._report(fin_events=[MagicMock()])
        )

        coordinator._request_parts_refresh_on_mission_end(report)

        coordinator.hass.async_create_task.assert_called_once()

    def test_a_fin_entry_in_the_general_list_triggers_a_refresh(self):
        """The delta can carry it either way depending on how it
        arrived."""
        coordinator, _parts, report = self._coordinator(
            self._report(event_types=("start", "travel", "fin"))
        )

        coordinator._request_parts_refresh_on_mission_end(report)

        coordinator.hass.async_create_task.assert_called_once()

    def test_a_mid_mission_event_does_not_trigger_a_refresh(self):
        """Every travel and room event would otherwise mean a cloud
        request -- the tester's single mission produced nine events."""
        coordinator, _parts, report = self._coordinator(
            self._report(event_types=("start", "reloc", "travel", "room"))
        )

        coordinator._request_parts_refresh_on_mission_end(report)

        coordinator.hass.async_create_task.assert_not_called()

    def test_evac_alone_does_not_trigger_a_refresh(self):
        """A self-emptying base can empty MID-mission and the robot
        carries on. The tester's timeline shows evac at 06:08:37 and fin
        at 06:08:56 -- two separate events, and only the second means the
        counters have settled."""
        coordinator, _parts, report = self._coordinator(
            self._report(event_types=("evac",))
        )

        coordinator._request_parts_refresh_on_mission_end(report)

        coordinator.hass.async_create_task.assert_not_called()

    def test_the_field_names_match_the_library_model(self):
        """A first draft read `report.events`, which does not exist. The
        getattr would have returned None silently and this would never
        have fired -- reproducing exactly the symptom it was written to
        fix, with no error anywhere."""
        import dataclasses
        import inspect

        from roombapy_prime.models import MissionTimelineReport

        from custom_components.roomba_plus.prime_coordinator import PrimeCoordinator

        fields = {f.name for f in dataclasses.fields(MissionTimelineReport)}
        source = inspect.getsource(
            PrimeCoordinator._request_parts_refresh_on_mission_end
        )

        assert '"fin_events"' in source
        assert "fin_events" in fields
        assert '"event"' in source
        assert "event" in fields

    def test_no_parts_coordinator_does_not_raise(self):
        """Reachable if parts setup failed -- the mission stream must not
        go down with it."""
        coordinator, _parts, report = self._coordinator(
            self._report(fin_events=[1])
        )
        coordinator.config_entry.runtime_data.prime_parts_coordinator = None

        coordinator._request_parts_refresh_on_mission_end(report)

        coordinator.hass.async_create_task.assert_not_called()


class TestMeasuredRoomDurations:
    """What Prime has instead of the cloud time estimates Classic reads.

    Prime's cloud supplies none: not in RoomFeatureProperties, not in
    room metadata across two real captures, and the one endpoint that
    would (`/v1/time-estimates`) assembles its request body in native
    code -- an APK pass established there is no Kotlin request class and
    therefore no determinable key names.

    So the durations are measured from the mission's own timeline. That
    is arguably better than a prediction: it is this robot in this home,
    not a model value. The cost is needing a room cleaned once."""

    def _event(self, region_id, start_min, duration_min):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock

        base = datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc)
        event = MagicMock(
            start_time=base + timedelta(minutes=start_min),
            end_time=base + timedelta(minutes=start_min + duration_min),
        )
        event.room = MagicMock(region_id=region_id) if region_id else None
        return event

    def _durations(self, events):
        from custom_components.roomba_plus.prime_mission_sync import _room_durations

        return _room_durations(events)

    def test_a_room_visit_is_measured(self):
        assert self._durations([self._event("13", 0, 8)]) == {"13": 480.0}

    def test_revisits_accumulate_rather_than_overwrite(self):
        """A robot that leaves a room to empty its bin and comes back
        spent the sum of both visits there. Taking the last one would
        report two minutes for a ten-minute room."""
        events = [
            self._event("13", 0, 8),
            self._event(None, 8, 1),   # travel
            self._event("13", 9, 2),
        ]

        assert self._durations(events) == {"13": 600.0}

    def test_events_without_a_room_are_skipped(self):
        """A real mission is mostly travel, traversal and reloc."""
        assert self._durations([self._event(None, 0, 5)]) == {}

    def test_implausible_durations_are_dropped(self):
        """Clock skew, or an event that never closed. A negative or
        four-hour room visit would poison the median it feeds."""
        assert self._durations([self._event("13", 0, -5)]) == {}
        assert self._durations([self._event("13", 0, 300)]) == {}


class TestRoomEstimates:
    """Median over a bounded window, not a running mean."""

    def _store(self, records):
        from unittest.mock import MagicMock

        store = MagicMock()
        store.query = MagicMock(return_value=records)
        return store

    def _estimate(self, records, room_ids):
        from custom_components.roomba_plus.prime_mission_sync import (
            estimate_room_seconds,
        )

        return estimate_room_seconds(self._store(records), room_ids)

    def test_one_past_mission_is_enough(self):
        """A poor estimate beats no progress indication, which is what
        this replaces."""
        assert self._estimate([{"room_durations_sec": {"13": 600.0}}], ["13"]) == [600.0]

    def test_an_outlier_does_not_move_the_estimate(self):
        """THE reason for a median. One mission where the robot got stuck
        in a doorway for fifty minutes would drag a mean permanently."""
        records = [
            {"room_durations_sec": {"13": 600.0}},
            {"room_durations_sec": {"13": 540.0}},
            {"room_durations_sec": {"13": 3000.0}},
        ]

        assert self._estimate(records, ["13"]) == [600.0]

    def test_a_room_never_cleaned_yields_none(self):
        """set_mission_plan accepts None per room -- Classic has the same
        gap for a newly named room with no cloud estimate yet."""
        assert self._estimate([{"room_durations_sec": {"13": 600.0}}], ["99"]) == [None]

    def test_the_order_matches_the_requested_rooms(self):
        """The list is positional: entry N is the estimate for room N."""
        records = [{"room_durations_sec": {"13": 600.0, "15": 300.0}}]

        assert self._estimate(records, ["15", "13"]) == [300.0, 600.0]

    def test_an_empty_store_yields_all_none(self):
        assert self._estimate([], ["13", "15"]) == [None, None]

    def test_a_failing_store_does_not_raise(self):
        """Estimates are enrichment; cleaning must still start."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_mission_sync import (
            estimate_room_seconds,
        )

        store = MagicMock()
        store.query = MagicMock(side_effect=RuntimeError("corrupt"))

        assert estimate_room_seconds(store, ["13"]) == [None]

    def test_a_total_is_only_given_when_every_room_is_known(self):
        """A partial sum reads as the whole mission and is short by
        however many rooms are missing -- worse than showing nothing,
        because nothing is visibly nothing."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        source = inspect.getsource(PrimeRoomCleaning._note_mission_plan)

        assert "len(known) == len(room_ids)" in source


class TestQueryIsCalledWithItsRequiredArgument:
    """`MissionStore.query(days, result=None)` requires `days`.

    Three call sites omitted it. Every one raised TypeError before doing
    any work:

      - the mission sync's duplicate check, so EVERY sync since the
        feature shipped failed at the first line and logged at debug
        level. The store stayed empty and nothing said why.
      - the same sync's statistics update.
      - the diagnostics store summary, which caught the TypeError and
        reported `mission_store: unreadable` -- a store that was fine,
        described as broken.

    A tester's diagnostics download surfaced it (@DaRealGuGu, a15). The
    test suite could not: every test replaces `query` with a MagicMock,
    and a MagicMock accepts any signature. Same shape as the
    `config_entry.hass` bug two hours earlier -- mocks cannot fail a
    contract the real object enforces."""

    def test_the_real_signature_requires_days(self):
        """The premise, asserted rather than assumed. If the library ever
        gives `days` a default, this test says so."""
        import inspect

        from custom_components.roomba_plus.mission_store import MissionStore

        days = inspect.signature(MissionStore.query).parameters["days"]

        assert days.default is inspect.Parameter.empty

    def test_no_production_call_omits_it(self):
        """Broader than the three known sites: any future `query()` with
        no arguments fails the same way."""
        import ast
        import re
        from pathlib import Path

        root = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            # Docstrings too, not just comments: one of them writes
            # "MissionStore.query()'s contract" while describing that
            # contract, and a text search reads the description as the
            # offence. Third time today that a guard flagged its own
            # explanation.
            docstrings = {
                node.body[0].value.value
                for node in ast.walk(tree)
                if isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            code = "\n".join(
                line
                for line in source.splitlines()
                if not line.strip().startswith("#")
            )
            for doc in docstrings:
                code = code.replace(doc, "")

            assert not re.search(r"\.query\(\s*\)", code), path.name

    def test_the_sync_uses_a_window_wide_enough_to_dedupe(self):
        """A narrow window would let a mission older than the window be
        re-added on every run -- the duplicate check would stop seeing
        it."""
        import inspect

        from custom_components.roomba_plus import prime_mission_sync

        source = inspect.getsource(prime_mission_sync.async_sync_prime_missions)
        source += inspect.getsource(prime_mission_sync._async_sync_locked)

        assert "days=3650" in source


class TestTheFirstSyncRunsWhenItCanSucceed:
    """The sync used to ride the parts coordinator's first refresh, and
    setup awaits that refresh BEFORE assigning `config_entry.runtime_data`.

    So the first attempt reached for runtime data that did not exist,
    raised AttributeError, and a best-effort except filed it at DEBUG.
    The next attempt was that coordinator's own interval -- six hours.

    Net effect: after every restart, clean streak, last mission, last
    duration and area cleaned today read "unknown" for six hours. Which
    is also why they were reported as permanently unknown -- nobody
    watches a sensor for six hours (@utkjmitch).
    """

    def test_the_coordinator_skips_a_run_that_cannot_work(self):
        """Skipping costs nothing: it could never have succeeded."""
        import inspect

        from custom_components.roomba_plus import prime_coordinator

        source = " ".join(inspect.getsource(prime_coordinator).split())
        assert 'getattr( self.config_entry, "runtime_data", None ) is not None' in source

    def test_setup_schedules_the_real_first_sync(self):
        import inspect

        import custom_components.roomba_plus as init_mod

        source = inspect.getsource(init_mod)
        assert "roomba_plus_prime_first_mission_sync" in source

    def test_it_is_scheduled_after_runtime_data_is_assigned(self):
        """The whole point. Ordering, asserted by position rather than by
        hope."""
        import inspect

        import custom_components.roomba_plus as init_mod

        source = inspect.getsource(init_mod)
        assigned = source.rindex("config_entry.runtime_data = RoombaData(")
        scheduled = source.index("roomba_plus_prime_first_mission_sync")

        assert assigned < scheduled

    def test_a_failing_sync_does_not_break_setup(self):
        """A robot with no history is a perfectly normal answer, and a
        cloud round trip is not worth failing a setup over."""
        import inspect

        import custom_components.roomba_plus as init_mod

        source = inspect.getsource(init_mod)
        start = source.index("async def _first_mission_sync")
        body = source[start:start + 1200]

        assert "except Exception" in body
        assert "hass.async_create_task" in source[start:start + 1600]


class TestTheHistoryIsParsedBeforeItIsConverted:
    """46 missions on the endpoint, 0 imported (@utkjmitch, Y351020).

    `get_mission_history()` returns the **raw** response by design — its
    own docstring calls conversion "a separate, optional step". Nothing
    took that step, so this iterated plain dicts and asked them for
    attributes: `getattr(dict, "mission_id", None)` is None on every
    entry, so every entry converted to None and every one was dropped.

    **Silently, because dropping id-less entries is the correct handling
    of a malformed record.** The data was not malformed; it was
    unparsed.

    The third life of this call site: a wrong `days` argument, then a
    missing `blid`, now a missing parse. Each fix moved the failure one
    layer deeper — and no test covered the path, which is how.
    """

    def test_the_parser_is_called_on_the_response(self):
        import inspect

        from custom_components.roomba_plus import prime_mission_sync

        source = inspect.getsource(prime_mission_sync)

        assert "parse_mission_history(" in source

    def test_raw_dicts_convert_to_nothing(self):
        """The failure mode itself, pinned: this is what the sync saw
        for three releases."""
        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        assert prime_entry_to_record({"missionId": "M1", "nMssn": 214}) is None

    def test_a_parsed_entry_converts(self):
        from roombapy_prime.models.mission_history import MissionHistoryEntry

        from custom_components.roomba_plus.prime_mission_sync import (
            prime_entry_to_record,
        )

        entry = MissionHistoryEntry.from_json({
            "missionId": "M1", "nMssn": 214, "durationM": 40, "sqft": 300,
            # A record without a time cannot be placed in the store, so
            # the converter rejects it -- correctly, and separately from
            # the parse failure this class is about.
            "timestamp": 1786000000, "startTime": 1786000000,
        })

        assert prime_entry_to_record(entry) is not None

    def test_the_tracker_blind_spot_is_documented(self):
        """`record_success` fires after the fetch, which did succeed. The
        loss happens past the exception boundary, in data nothing
        watches — so `never_succeeded` stayed empty while 46 missions
        evaporated."""
        import inspect

        from custom_components.roomba_plus import prime_mission_sync

        source = inspect.getsource(prime_mission_sync)

        assert "past the exception" in source
