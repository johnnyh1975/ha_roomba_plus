"""The mission recorder (4.7.0): what it keeps, what it leaves out, and
that a recording replays to the decisions the robot's mission produced.

The last class is the one the recorder exists for: a mission is
recorded while it runs through the real setup, and the recording, played
into a fresh setup, gives the same rooms, the same end gate and the same
close. If a key the mission course needs were missing from the
allow-list, or a value were redacted that it reads, that is where it
would show.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.roomba_plus import mission_recorder as mr
from custom_components.roomba_plus.const import DOMAIN
from tests.replay import BLID, Replay

def _message(reported: dict[str, Any]) -> dict[str, Any]:
    return {"state": {"reported": reported}}


class TestWhatIsKept:
    def test_only_the_allowed_keys_are_recorded(self):
        rec = mr.MissionRecording((BLID,))
        rec.add(_message({
            "batPct": 80, "pose": {"point": {"x": 1, "y": 2}, "theta": 3},
            "netinfo": {"addr": 3221225985}, "wlcfg": {"ssid": "4d79"}, "signal": {"rssi": -50},
            "name": "Kitchen robot", "cleanSchedule2": [{"start": {"hour": 9}}],
            "sku": "i755840", "softwareVer": "lewis+22.52.10", "someFutureKey": 1,
        }))
        assert [m["reported"] for m in rec.messages] == [
            {"batPct": 80, "pose": {"point": {"x": 1, "y": 2}, "theta": 3}},
        ]

    def test_every_allowed_key_is_one_the_integration_reads(self):
        """An allow-list entry nobody reads is a value recorded for nothing."""
        from pathlib import Path

        package = Path(mr.__file__).parent
        source = "".join(
            p.read_text() for p in package.glob("*.py") if p.name != "mission_recorder.py"
        )
        unread = sorted(k for k in mr.REPORTED_KEYS if f'"{k}"' not in source)
        assert unread == []

    def test_a_message_with_nothing_kept_is_not_recorded(self):
        rec = mr.MissionRecording(())
        rec.add(_message({"signal": {"rssi": -50}}))
        rec.add({"state": None})
        rec.add("not a message")
        assert rec.messages == []

    @pytest.mark.parametrize(("value", "gone"), [
        (f"started by {BLID}", BLID),
        ("user@example.com", "user@example.com"),
        ("peer 192.0.2.17", "192.0.2.17"),
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ])
    def test_values_are_redacted(self, value, gone):
        rec = mr.MissionRecording((BLID,))
        rec.add(_message({"lastCommand": {"initiator": value}}))
        assert gone not in str(rec.messages)

    def test_secret_shaped_keys_are_left_out_wherever_they_are(self):
        rec = mr.MissionRecording(())
        rec.add(_message({"lastCommand": {"command": "start", "password": "pw", "authToken": "x"}}))
        assert rec.messages[0]["reported"] == {"lastCommand": {"command": "start"}}

    def test_map_ids_become_placeholders_the_same_way_everywhere(self):
        rec = mr.MissionRecording(())
        first, second = "AbCdEfGhIjKlMnOpQrStUv", "ZyXwVuTsRqPoNmLkJiHgFe"
        rec.add(_message({"pmaps": [{first: "260918T160000"}, {second: "260101T000000"}]}))
        rec.add(_message({"lastCommand": {"pmap_id": second, "regions": [{"region_id": "3"}]}}))
        assert rec.messages[0]["reported"]["pmaps"] == [
            {"map-1": "260918T160000"}, {"map-2": "260101T000000"},
        ]
        assert rec.messages[1]["reported"]["lastCommand"] == {
            "pmap_id": "map-2", "regions": [{"region_id": "3"}],
        }
        assert first not in str(rec.messages) and second not in str(rec.messages)

    def test_it_stops_keeping_at_the_limit_and_says_how_many_it_missed(self, monkeypatch):
        monkeypatch.setattr(mr, "MAX_MESSAGES", 2)
        rec = mr.MissionRecording(())
        for pct in (90, 89, 88, 87):
            rec.add(_message({"batPct": pct}))
        assert [m["reported"]["batPct"] for m in rec.messages] == [90, 89]
        assert rec.report()["dropped_messages"] == 2


async def _robot(hass, monkeypatch) -> Replay:
    replay = Replay(hass, monkeypatch)
    await replay.setup({
        "name": "Robbie", "sku": "R980040", "softwareVer": "v2.4.17-138",
        "cap": {"pose": 1}, "pose": {"point": {"x": 0, "y": 0}, "theta": 0},
        "batPct": 100, "netinfo": {"addr": 3221225985},
    })
    return replay


async def _call(hass, action: str, entity_id: str, **data: Any) -> Any:
    response = action != "mission_recording_start"
    return await hass.services.async_call(
        DOMAIN, action, {"entity_id": entity_id, **data}, blocking=True,
        return_response=response,
    )


class TestTheRooms:
    def test_rooms_are_kept_by_id_and_estimate_without_their_names(self):
        rec = mr.MissionRecording(())
        rec.clean_reported({"pmaps": [{"AbCdEfGhIjKlMnOpQrStUv": "v"}]})
        rooms = rec.clean_rooms([
            {"id": "7", "name": "Anna's room", "region_type": "bedroom",
             "pmap_id": "AbCdEfGhIjKlMnOpQrStUv",
             "time_estimates": {"one_pass_sec": 600, "two_pass_sec": None}},
            {"id": "", "name": "no id"}, "not a room",
        ])
        assert rooms == [{
            "id": "7", "pmap_id": "map-1", "region_type": "bedroom",
            "time_estimates": {"one_pass_sec": 600, "two_pass_sec": None},
        }]


class TestTheActions:
    @pytest.mark.asyncio
    async def test_a_recording_keeps_what_arrives_with_its_time(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id, minutes=10)
        await robot.at(2.5, {"batPct": 99})
        await robot.at(4.0, {"pose": {"point": {"x": 12, "y": -3}, "theta": 90}})
        got = await _call(hass, "mission_recording_get", robot.vacuum_entity_id)
        assert got["running"] is True
        assert got["robot"] == {
            "generation": "classic", "sku_prefix": "r", "firmware": "v2.4.17-138",
            "map_capability": "ephemeral", "cloud": False,
            "integration": got["robot"]["integration"],
        }
        assert got["robot"]["integration"]
        assert got["initial"]["batPct"] == 100 and "netinfo" not in got["initial"]
        assert [(m["t"], m["reported"]) for m in got["messages"]] == [
            (2.5, {"batPct": 99}),
            (4.0, {"pose": {"point": {"x": 12, "y": -3}, "theta": 90}}),
        ]
        await robot.unload()

    @pytest.mark.asyncio
    async def test_stop_ends_it_and_returns_what_was_kept(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id)
        await robot.at(1.0, {"batPct": 99})
        stopped = await _call(hass, "mission_recording_stop", robot.vacuum_entity_id)
        await robot.at(2.0, {"batPct": 98})
        got = await _call(hass, "mission_recording_get", robot.vacuum_entity_id)
        assert stopped["running"] is False and stopped["stopped_at"]
        assert got["message_count"] == 1
        await robot.unload()

    @pytest.mark.asyncio
    async def test_it_stops_by_itself_after_the_minutes_given(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id, minutes=5)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
        await hass.async_block_till_done()
        await robot.at(400.0, {"batPct": 99})
        got = await _call(hass, "mission_recording_get", robot.vacuum_entity_id)
        assert got["running"] is False and got["message_count"] == 0
        await robot.unload()

    @pytest.mark.asyncio
    async def test_a_new_start_begins_a_new_recording(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id)
        listening = len(robot.client._on_message)
        await robot.at(1.0, {"batPct": 99})
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id)
        await robot.at(2.0, {"batPct": 98})
        got = await _call(hass, "mission_recording_get", robot.vacuum_entity_id)
        assert [m["reported"] for m in got["messages"]] == [{"batPct": 98}]
        # The first recording no longer listens: one callback, not two.
        assert len(robot.client._on_message) == listening
        await robot.unload()

    @pytest.mark.asyncio
    async def test_a_reload_stops_it_and_keeps_what_was_recorded(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id)
        await robot.at(1.0, {"batPct": 99})
        assert await hass.config_entries.async_reload(robot.entry.entry_id)
        await hass.async_block_till_done()
        await robot.at(2.0, {"batPct": 98})
        got = await _call(hass, "mission_recording_get", robot.vacuum_entity_id)
        assert got["running"] is False
        assert [m["reported"] for m in got["messages"]] == [{"batPct": 99}]
        await robot.unload()

    @pytest.mark.asyncio
    async def test_nothing_recorded_is_an_error_the_caller_sees(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        for action in ("mission_recording_get", "mission_recording_stop"):
            with pytest.raises(ServiceValidationError) as err:
                await _call(hass, action, robot.vacuum_entity_id)
            assert err.value.translation_key == "no_mission_recording"
        await robot.unload()

    @pytest.mark.asyncio
    async def test_a_prime_robot_is_told_it_is_not_supported_yet(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        prime = MockConfigEntry(domain=DOMAIN, title="Prime", unique_id="prime")
        prime.add_to_hass(hass)
        prime.mock_state(hass, ConfigEntryState.LOADED)
        prime.runtime_data = SimpleNamespace(roomba=None)
        entity_id = er.async_get(hass).async_get_or_create(
            "vacuum", DOMAIN, "prime", config_entry=prime,
        ).entity_id
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, "mission_recording_start", entity_id)
        assert err.value.translation_key == "mission_recording_classic_only"
        await robot.unload()

    @pytest.mark.asyncio
    async def test_more_than_six_hours_is_refused(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        import voluptuous as vol

        robot = await _robot(hass, monkeypatch)
        with pytest.raises(vol.Invalid):
            await _call(hass, "mission_recording_start", robot.vacuum_entity_id,
                        minutes=mr.MAX_MINUTES + 1)
        await robot.unload()


class TestARecordingReplaysToTheSameMission:
    """Thonno's mission (tests/test_replay_field_mission.py), recorded as it
    runs, then played from the recording alone into a fresh setup."""

    @pytest.mark.asyncio
    async def test_the_replayed_recording_decides_as_the_mission_did(
        self, hass, monkeypatch, caplog, enable_custom_integrations,
    ):
        from tests.test_replay_field_mission import ThonnoRun, thonno_messages

        first = ThonnoRun(Replay(hass, monkeypatch), monkeypatch, caplog)
        await first.setup()
        await _call(hass, "mission_recording_start", first.replay.vacuum_entity_id)
        for seconds, reported in thonno_messages():
            await first.at(seconds, reported)
        recording = await _call(hass, "mission_recording_stop", first.replay.vacuum_entity_id)
        rooms, closed = dict(first.room_after), list(first.closed_at)
        await first.replay.unload()
        await hass.config_entries.async_remove(first.replay.entry.entry_id)
        await hass.async_block_till_done()
        caplog.clear()

        # From the recording alone: its first state carries the start
        # command, its rooms the map -- ids and estimates, no names.
        assert {r["id"] for r in recording["rooms"]} == {"1", "2", "3"}
        assert "Cucina" not in str(recording)
        second = ThonnoRun(Replay(hass, monkeypatch), monkeypatch, caplog)
        await second.setup_from(recording)
        for message in recording["messages"]:
            await second.at(float(message["t"]), message["reported"])

        assert closed == [pytest.approx(3070.02)]
        assert second.room_after == rooms
        assert second.closed_at == closed
        held = [
            r.getMessage() for r in caplog.records if "burst rejected" in r.getMessage()
        ]
        assert len(held) == 3
        await second.replay.unload()
