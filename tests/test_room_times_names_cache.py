"""4.2.22, after @mrsnyds' Roomba 105 reports.

- How long a room usually takes, without charging in it.
- Room names kept across restarts, so nothing starts on region numbers.
- One pass over the history for all per-room sensors, not one each.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import mission_store as ms
from custom_components.roomba_plus.mission_store import MissionStore


def _prime(rec_id: str, seconds: float, *, rid: str = "11", day: int = 1) -> dict:
    return {
        "id": rec_id,
        "ended_at": f"2026-09-{day:02d}T10:00:00+00:00",
        "rooms_cleaned": [rid],
        "room_ended_at": {rid: f"2026-09-{day:02d}T09:40:00+00:00"},
        "room_durations_sec": {rid: seconds},
    }


def _store(*records: dict) -> MissionStore:
    store = MissionStore()
    store._records = list(records)
    return store


def _sensor(store):
    from custom_components.roomba_plus.sensor_prime import PrimeRegionLastCleanedSensor

    s = PrimeRegionLastCleanedSensor.__new__(PrimeRegionLastCleanedSensor)
    s._region_id = "11"
    s._pmap_id = None
    s._config_entry = SimpleNamespace(runtime_data=SimpleNamespace(mission_store=store))
    return s


class TestHowLongARoomUsuallyTakes:
    def test_the_median_of_its_cleans(self) -> None:
        """One clean cut short or stuck must not move it."""
        store = _store(_prime("a", 600, day=1), _prime("b", 900, day=2), _prime("c", 3600, day=3))

        assert store.region_typical_seconds()["11"] == {"seconds": 900, "samples": 3}

    def test_only_the_newest_cleans(self) -> None:
        old = [_prime(f"o{i}", 60.0, day=1) for i in range(5)]
        new = [_prime(f"n{i}", 600.0, day=2) for i in range(ms.TYPICAL_REGION_CLEANS)]
        store = _store(*old, *new)

        typical = store.region_typical_seconds()["11"]
        assert typical == {"seconds": 600.0, "samples": ms.TYPICAL_REGION_CLEANS}

    def test_the_sensor_shows_it_in_minutes(self) -> None:
        store = _store(_prime("a", 600, day=1), _prime("b", 780, day=2), _prime("c", 900, day=3))

        attrs = _sensor(store).extra_state_attributes

        assert attrs["typical_duration_min"] == 13.0
        assert attrs["typical_duration_cleans"] == 3
        assert attrs["last_duration_min"] == 15.0

    def test_absent_when_no_clean_has_a_time(self) -> None:
        rec = _prime("a", 600)
        rec.pop("room_durations_sec")
        attrs = _sensor(_store(rec)).extra_state_attributes

        assert "typical_duration_min" not in attrs
        assert "typical_duration_cleans" not in attrs


class TestOnePassForEveryRoom:
    def test_a_burst_of_renders_reads_the_history_once(self) -> None:
        store = _store(_prime("a", 600))
        with patch.object(
            MissionStore, "_build_region_index", wraps=store._build_region_index
        ) as built:
            for _ in range(9):
                store.region_last_cleaned_details()
                store.region_typical_seconds()

        assert built.call_count == 1

    @pytest.mark.asyncio
    async def test_a_new_record_is_seen_at_once(self) -> None:
        store = MissionStore()
        await store.async_append(_prime("a", 600, day=1))
        assert store.region_last_cleaned_details()["11"]["seconds"] == 600

        await store.async_append(_prime("b", 300, day=2))

        assert store.region_last_cleaned_details()["11"]["seconds"] == 300

    @pytest.mark.asyncio
    async def test_a_record_changed_in_place_is_seen_after_its_save(self) -> None:
        store = _store(_prime("a", 600))
        assert store.region_last_cleaned_details()["11"]["seconds"] == 600

        store._records[0]["room_durations_sec"] = {"11": 120}
        await store.async_save(None, "E1")

        assert store.region_last_cleaned_details()["11"]["seconds"] == 120

    def test_filled_in_room_data_is_seen_at_once(self) -> None:
        bare = {"id": "a", "ended_at": "2026-09-01T10:00:00+00:00"}
        store = _store(bare)
        assert store.region_last_cleaned_details() == {}

        assert store.add_missing_room_data(_prime("a", 600)) is True

        assert store.region_last_cleaned_details()["11"]["seconds"] == 600

    def test_and_it_expires(self, monkeypatch) -> None:
        store = _store(_prime("a", 600))
        clock = [1000.0]
        monkeypatch.setattr(ms.time, "monotonic", lambda: clock[0])
        store.region_last_cleaned_details()
        store._records[0]["room_durations_sec"] = {"11": 120}

        clock[0] += ms.REGION_INDEX_TTL_S + 0.1

        assert store.region_last_cleaned_details()["11"]["seconds"] == 120


class TestRoomNamesSurviveARestart:
    @staticmethod
    def _entry(hass, names=None):
        robot = MagicMock()
        robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://x"})
        robot.download_map_bundle = AsyncMock(return_value=b"tgz")
        return SimpleNamespace(
            entry_id="E1",
            runtime_data=SimpleNamespace(
                prime_robot=robot,
                prime_room_names=dict(names or {}),
                prime_room_map_ids={},
                hass_ref=hass,
            ),
        )

    async def test_names_read_from_the_map_are_back_after_a_restart(self, hass) -> None:
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
            async_restore_prime_room_names,
        )

        before = self._entry(hass)
        rooms = {"features": [{"id": "15", "properties": {"name": "Guest Bath"}}]}
        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle",
            return_value={"rooms": rooms},
        ):
            await async_build_prime_floor_plan(before, "MAP-1", "V1")

        after = self._entry(hass)   # a new run: nothing in memory
        await async_restore_prime_room_names(hass, after)

        assert after.runtime_data.prime_room_names == {"15": "Guest Bath"}
        assert after.runtime_data.prime_room_map_ids == {"15": "MAP-1"}

    async def test_this_runs_names_win(self, hass) -> None:
        from custom_components.roomba_plus.prime_room_map import (
            _async_save_prime_room_names,
            async_restore_prime_room_names,
        )

        await _async_save_prime_room_names(hass, self._entry(hass, {"15": "Old name"}))
        entry = self._entry(hass, {"15": "Renamed"})

        await async_restore_prime_room_names(hass, entry)

        assert entry.runtime_data.prime_room_names == {"15": "Renamed"}

    async def test_nothing_saved_is_nothing_restored(self, hass) -> None:
        from custom_components.roomba_plus.prime_room_map import (
            async_restore_prime_room_names,
        )

        entry = self._entry(hass)
        await async_restore_prime_room_names(hass, entry)

        assert entry.runtime_data.prime_room_names == {}

    def test_restored_before_the_platforms_set_up(self) -> None:
        """After setup, every platform would start on numbers again."""
        import inspect

        from custom_components.roomba_plus import _async_setup_entry_prime

        source = inspect.getsource(_async_setup_entry_prime)
        restore = source.index("await async_restore_prime_room_names(hass, config_entry)")
        assert restore < source.index("async_forward_entry_setups")
