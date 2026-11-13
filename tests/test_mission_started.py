"""A mission began, as one event (4.6.0, B2)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.roomba_plus import mission_started as ms
from custom_components.roomba_plus.const import EVENT_MISSION_STARTED


def _data(command, names=None):
    return SimpleNamespace(prime_room_names=names or {}, _command=command)


def _details(command, *, now=1_000_000.0, names=None):
    with patch(
        "custom_components.roomba_plus.prime_coordinator.prime_last_command",
        return_value=command,
    ):
        return ms.prime_start_details(_data(command, names), now)


class TestPrimeStartDetails:
    """Who and where come from the last command, only from a recent one."""

    def test_a_room_start(self):
        command = {
            "command": "start", "initiator": "rmtApp", "time": 1_000_000 - 30,
            "regions": [{"region_id": "7", "region_name": "Küche"}, {"region_id": "9"}],
        }
        assert _details(command, names={"9": "Flur"}) == ("rmtApp", "rooms", ["Küche", "Flur"])

    def test_a_whole_home_start(self):
        command = {"command": "start", "initiator": "localApp", "time": 1_000_000 - 5}
        assert _details(command) == ("localApp", "whole_home", [])

    def test_an_old_command_claims_nothing(self):
        """A schedule start issues no command; the one there is older."""
        command = {"command": "start", "initiator": "rmtApp", "time": 1_000_000 - 3600}
        assert _details(command) == (None, None, [])

    def test_a_command_without_a_time_claims_nothing(self):
        assert _details({"command": "start", "initiator": "rmtApp"}) == (None, None, [])
        assert _details({}) == (None, None, [])
        assert _details({"time": True}) == (None, None, [])

    def test_a_command_from_the_future_claims_nothing(self):
        assert _details({"initiator": "x", "time": 1_000_000 + 60}) == (None, None, [])


class TestFireMissionStarted:
    def test_the_payload(self):
        hass = MagicMock()
        entry = SimpleNamespace(entry_id="e1", title="Robbie")
        with patch.object(ms, "async_area_for_room", side_effect=["kitchen", None]):
            ms.fire_mission_started(
                hass, entry, initiator="schedule", scope="rooms",
                rooms=["Küche", "Flur", ""], mission_number=12,
            )
        event, payload = hass.bus.async_fire.call_args.args
        assert event == EVENT_MISSION_STARTED
        started = payload.pop("started_at")
        assert isinstance(started, str) and started
        assert payload == {
            "entry_id": "e1", "name": "Robbie", "initiator": "schedule",
            "scope": "rooms", "rooms": ["Küche", "Flur"],
            "areas": ["kitchen", None], "mission_number": 12,
        }

    def test_unknowns_stay_none(self):
        hass = MagicMock()
        ms.fire_mission_started(
            hass, SimpleNamespace(entry_id="e1"), initiator=None, scope=None, rooms=None,
        )
        payload = hass.bus.async_fire.call_args.args[1]
        assert (payload["initiator"], payload["scope"], payload["rooms"], payload["areas"]) == (
            None, None, [], [],
        )
