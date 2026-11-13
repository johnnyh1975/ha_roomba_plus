"""When each HA area was last vacuumed and mopped (4.6.0, B14)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus import sensor_areas as sa
from custom_components.roomba_plus.mission_store import MissionStore

_AREAS = {"Kitchen": "kitchen", "Küche": "kitchen", "Hall": "hall"}


def _entry(entry_id, title, records, default=None):
    store = MissionStore()
    store._records = list(records)
    data = SimpleNamespace(
        mission_store=store, has_cloud=False, cloud_coordinator=None,
        prime_room_names={}, umf_aligner=None, blid="B",
        roomba_reported_state=lambda: {},
    )
    return SimpleNamespace(entry_id=entry_id, title=title, runtime_data=data)


def _hass(*entries):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = list(entries)
    return hass


@pytest.fixture(autouse=True)
def _areas(monkeypatch):
    monkeypatch.setattr(sa, "async_area_for_room", lambda _h, _e, room: _AREAS.get(room))
    monkeypatch.setattr(sa, "async_area_name", lambda _h, area: area.title() if area else None)


ROOMBA = [
    {"id": "m_1", "ended_at": "2026-11-11T10:00:00+00:00", "last_cleaned_rooms": ["Kitchen", "Hall"],
     "cmd": {"operatingMode": 2}},
]
BRAAVA = [
    {"id": "m_2", "ended_at": "2026-11-12T09:00:00+00:00", "last_cleaned_rooms": ["Küche"]},
]


class TestTheHousehold:
    def test_vacuum_and_mop_from_different_robots(self):
        hass = _hass(_entry("r", "Roomba", ROOMBA), _entry("b", "Braava", BRAAVA))
        # The Braava's missions say nothing of their mode; it mops.
        hass.config_entries.async_entries.return_value[1].runtime_data.roomba_reported_state = (
            lambda: {"sku": "m611020"}
        )
        history = sa.household_area_history(hass)
        assert history["kitchen"]["vacuum"] == {
            "at": "2026-11-11T10:00:00+00:00", "robot": "Roomba", "room": "Kitchen",
        }
        assert history["kitchen"]["mop"]["robot"] == "Braava"
        assert history["hall"] == {"vacuum": {
            "at": "2026-11-11T10:00:00+00:00", "robot": "Roomba", "room": "Hall",
        }}

    def test_the_newest_wins_across_robots(self):
        newer = [{"id": "m_3", "ended_at": "2026-11-13T10:00:00+00:00",
                  "last_cleaned_rooms": ["Hall"], "cmd": {"operatingMode": 2}}]
        hass = _hass(_entry("r", "Roomba", ROOMBA), _entry("s", "S9", newer))
        assert sa.household_area_history(hass)["hall"]["vacuum"]["robot"] == "S9"

    def test_a_robot_not_loaded_or_broken_is_left_out(self):
        broken = SimpleNamespace(entry_id="x", title="X", runtime_data=SimpleNamespace(
            mission_store=SimpleNamespace(room_cleaning_history_by_mode=MagicMock(side_effect=RuntimeError)),
            has_cloud=False, cloud_coordinator=None, prime_room_names={}, umf_aligner=None,
            roomba_reported_state=lambda: {},
        ))
        unloaded = SimpleNamespace(entry_id="u", title="U", runtime_data=None)
        hass = _hass(broken, unloaded, _entry("r", "Roomba", ROOMBA))
        assert set(sa.household_area_history(hass)) == {"kitchen", "hall"}

    def test_a_room_without_area_is_no_area(self):
        hass = _hass(_entry("r", "Roomba", [
            {"id": "m", "ended_at": "2026-11-11T10:00:00+00:00", "last_cleaned_rooms": ["Attic"]},
        ]))
        assert sa.household_area_history(hass) == {}


class TestTheSensor:
    def test_value_and_attributes(self):
        entry = _entry("r", "Roomba", ROOMBA)
        sensor = sa.RoombaAreaLastCleanedSensor(None, "B", entry, "kitchen", "vacuum", "Kitchen")
        sensor.hass = _hass(entry)
        assert sensor.unique_id.endswith("_area_last_vacuumed_kitchen")
        assert sensor.native_value.isoformat() == "2026-11-11T10:00:00+00:00"
        assert sensor.extra_state_attributes == {"area_id": "kitchen", "robot": "Roomba", "room": "Kitchen"}
        assert sensor.translation_placeholders == {"area": "Kitchen"}
        assert sensor.entity_registry_enabled_default is False
        assert sensor.new_state_filter({}) is False

    def test_never_mopped(self):
        entry = _entry("r", "Roomba", ROOMBA)
        sensor = sa.RoombaAreaLastCleanedSensor(None, "B", entry, "kitchen", "mop")
        sensor.hass = _hass(entry)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes["robot"] is None

    def test_without_hass(self):
        sensor = sa.RoombaAreaLastCleanedSensor(None, "B", _entry("r", "R", []), "kitchen", "mop")
        assert sensor.native_value is None
        assert sensor.translation_placeholders == {"area": "kitchen"}


class TestSetup:
    def test_a_pair_per_area_and_new_ones_later(self, monkeypatch):
        entry = _entry("r", "Roomba", ROOMBA)
        entry.async_on_unload = MagicMock()
        hass = MagicMock()
        found = [{"kitchen"}]
        monkeypatch.setattr(sa, "robot_areas", lambda _h, _e: set(found[0]))
        added = MagicMock()
        sa.async_setup_area_sensors(hass, entry, added, None)
        first = added.call_args.args[0]
        assert [(e._area_id, e._kind) for e in first] == [("kitchen", "vacuum"), ("kitchen", "mop")]
        assert first[0].translation_placeholders == {"area": "Kitchen"}

        listener = hass.bus.async_listen.call_args.args[1]
        found[0] = {"kitchen", "hall"}
        listener(SimpleNamespace(data={"entry_id": "other"}))
        assert added.call_count == 1
        listener(SimpleNamespace(data={"entry_id": "r"}))
        assert [(e._area_id, e._kind) for e in added.call_args.args[0]] == [
            ("hall", "vacuum"), ("hall", "mop"),
        ]
        listener(SimpleNamespace(data={"entry_id": "r"}))
        assert added.call_count == 2

    def test_a_failure_builds_nothing(self, monkeypatch):
        entry = _entry("r", "Roomba", ROOMBA)
        entry.async_on_unload = MagicMock()
        monkeypatch.setattr(sa, "robot_areas", MagicMock(side_effect=RuntimeError))
        added = MagicMock()
        sa.async_setup_area_sensors(MagicMock(), entry, added, None)
        added.assert_not_called()

    def test_the_areas_are_those_of_the_rooms(self, monkeypatch):
        monkeypatch.setattr(sa, "rooms_of", lambda _h, _e: [
            {"area_id": "kitchen"}, {"area_id": None}, {"area_id": "hall"},
        ])
        assert sa.robot_areas(MagicMock(), MagicMock()) == {"kitchen", "hall"}


class TestEdges:
    def test_a_robot_without_a_store(self):
        entry = SimpleNamespace(entry_id="x", title="X", runtime_data=SimpleNamespace(mission_store=None))
        assert sa._robot_history(MagicMock(), entry) == {}

    @pytest.mark.asyncio
    async def test_any_mission_refreshes_the_value(self):
        entry = _entry("r", "Roomba", ROOMBA)
        sensor = sa.RoombaAreaLastCleanedSensor(None, "B", entry, "kitchen", "vacuum", "Kitchen")
        sensor.hass = MagicMock()
        sensor.async_on_remove = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        with patch("custom_components.roomba_plus.entity.IRobotEntity.async_added_to_hass"):
            await sensor.async_added_to_hass()
        listener = sensor.hass.bus.async_listen.call_args.args[1]
        listener(SimpleNamespace(data={"entry_id": "other"}))
        sensor.async_write_ha_state.assert_called_once()
