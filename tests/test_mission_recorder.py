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
from tests.replay import BLID, PrimeReplay, Replay

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

    def test_names_are_left_out_wherever_they_are(self):
        """A room clean started from Home Assistant carries the room names
        in `lastCommand`; the recording keeps the ids."""
        rec = mr.MissionRecording(())
        rec.add(_message({"lastCommand": {"regions": [
            {"region_id": "3", "region_name": "Anna's room", "type": "rid"},
        ], "pmapName": "Ground floor"}}))
        assert rec.messages[0]["reported"] == {
            "lastCommand": {"regions": [{"region_id": "3", "type": "rid"}]},
        }

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


async def _call(hass, action: str, entity_id: str | list[str], **data: Any) -> Any:
    """The action as a script calls it, by target. One robot's recording
    back, or all of them for a list."""
    response = action != "mission_recording_start"
    result = await hass.services.async_call(
        DOMAIN, action, data, target={"entity_id": entity_id}, blocking=True,
        return_response=response,
    )
    if response and isinstance(entity_id, str):
        assert list(result) == [entity_id]
        return result[entity_id]
    return result


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
    async def test_one_call_records_two_robots_each_on_its_own(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        first = await _robot(hass, monkeypatch)
        second = Replay(hass, monkeypatch, blid="FEDCBA9876543210", host="192.0.2.11")
        await second.setup({"name": "Second", "cap": {"pose": 1}, "batPct": 50}, title="Second")
        both = [first.vacuum_entity_id, second.vacuum_entity_id]
        await _call(hass, "mission_recording_start", both)
        first.send({"batPct": 99})
        second.send({"batPct": 49})
        await hass.async_block_till_done()
        got = await _call(hass, "mission_recording_stop", both)
        assert {e: [m["reported"] for m in r["messages"]] for e, r in got.items()} == {
            first.vacuum_entity_id: [{"batPct": 99}],
            second.vacuum_entity_id: [{"batPct": 49}],
        }
        await second.unload()
        await first.unload()

    @pytest.mark.asyncio
    async def test_an_unloaded_robot_among_the_targets_starts_none(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        other = MockConfigEntry(domain=DOMAIN, title="Unloaded", unique_id="unloaded")
        other.add_to_hass(hass)
        other.mock_state(hass, ConfigEntryState.LOADED)
        entity_id = er.async_get(hass).async_get_or_create(
            "vacuum", DOMAIN, "unloaded", config_entry=other,
        ).entity_id
        with pytest.raises(ServiceValidationError):
            await _call(hass, "mission_recording_start", [robot.vacuum_entity_id, entity_id])
        assert mr.recordings(hass) == {}
        await robot.unload()

    @pytest.mark.asyncio
    async def test_another_integrations_vacuum_is_refused(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _robot(hass, monkeypatch)
        other = MockConfigEntry(domain="other_vacuum", title="Other")
        other.add_to_hass(hass)
        entity_id = er.async_get(hass).async_get_or_create(
            "vacuum", "other_vacuum", "x", config_entry=other,
        ).entity_id
        with pytest.raises(ServiceValidationError) as err:
            await _call(hass, "mission_recording_start", entity_id)
        assert err.value.translation_key == "not_a_roomba_plus_vacuum"
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


# ── Prime (4.7.0b2) ──────────────────────────────────────────────────────────

MAP_ID = "AbCdEfGhIjKlMnOpQrStUv"


def _timeline(ts: int, rid: str | None) -> dict[str, Any]:
    """A mission timeline delta as the robot sends it: the current room."""
    event = [] if rid is None else [{
        "type": "room", "ts": ts,
        "room": {"rid": rid, "pmapId": MAP_ID, "name": "Kitchen"},
        "poly": {"rid": rid, "poly": [[0, 0], [4100, 0], [4100, 3200]]},
    }]
    return {
        "cmd": {"command": "start", "initiator": "rmtApp", "time": 1789747000},
        "event": event, "mission_id": "M-REPLAY-1", "nMssn": 11,
    }


def _state(phase: str, cycle: str, battery: int) -> dict[str, Any]:
    return {"cleanMissionStatus": {"phase": phase, "cycle": cycle, "nMssn": 11}, "batPct": battery}


#: A two-room mission from the app: (seconds, kind, payload).
PRIME_MISSION: list[tuple[float, str, dict[str, Any]]] = [
    (1.0, "shadow", _state("run", "clean", 100)),
    (2.0, "timeline", _timeline(1789747002, "10")),
    (300.0, "shadow", _state("run", "clean", 90)),
    (600.0, "timeline", _timeline(1789747600, "11")),
    (1200.0, "timeline", _timeline(1789748200, None)),
    (1210.0, "shadow", _state("hmPostMsn", "clean", 80)),
    (1300.0, "shadow", _state("charge", "none", 80)),
]


class TestWhatAPrimeRecordingKeeps:
    def test_a_shadow_keeps_the_allowed_keys_of_the_recorded_shadows(self):
        rec = mr.MissionRecording(())
        rec.add_shadow("ro-currentstate", {"batPct": 80, "netinfo": {"addr": 1}, "name": "Robbie"})
        rec.add_shadow("rw-schedule", {"cmd": "on"})
        rec.add_shadow("ro-stats", {"bbrun": {"hr": 3}})
        assert rec.messages[0]["source"] == "shadow"
        assert [(m["shadow"], m["reported"]) for m in rec.messages] == [
            ("ro-currentstate", {"batPct": 80}), ("ro-stats", {"bbrun": {"hr": 3}}),
        ]

    def test_a_timeline_delta_loses_outlines_and_names_and_keeps_ids(self):
        rec = mr.MissionRecording(())
        rec.add_payload("timeline", _timeline(1789747002, "10"))
        event = rec.messages[0]["payload"]["event"][0]
        assert event["room"] == {"rid": "10", "pmapId": "map-1"}
        assert "poly" not in event and "Kitchen" not in str(rec.messages)
        assert MAP_ID not in str(rec.messages)

    @pytest.mark.asyncio
    async def test_a_coordinator_hands_over_only_while_recording_and_never_fails(self, hass):
        mr.note(hass, "no-such-entry", "timeline", {"event": []})
        rec = mr.MissionRecording(())
        mr.recordings(hass)["e1"] = rec
        mr.note(hass, "e1", "timeline", {"event": []})
        assert rec.messages == []
        rec.start(hass, None, {}, {}, 1)
        mr.note(hass, "e1", "timeline", {"event": []})
        mr.note(hass, "e1", "shadow", {"batPct": 5}, shadow="ro-currentstate")
        assert [m["source"] for m in rec.messages] == ["timeline", "shadow"]
        # A message the cleaning cannot take is dropped, not raised.
        mr.note(hass, "e1", "timeline", {_Unprintable(): 1})
        assert len(rec.messages) == 2
        rec.stop()
        assert rec.running is False


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("no")


async def _prime(hass, monkeypatch) -> PrimeReplay:
    robot = PrimeReplay(hass, monkeypatch)
    await robot.setup({"ro-currentstate": _state("charge", "none", 100) | {"name": "Robbie"}})
    return robot


async def _play(robot: PrimeReplay, mission: list[tuple[float, str, dict[str, Any]]]) -> None:
    for seconds, kind, payload in mission:
        if kind == "shadow":
            await robot.shadow(seconds, "ro-currentstate", payload)
        else:
            await robot.timeline(seconds, payload)


def _room_events(hass) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def _keep(event: Any) -> None:
        data = dict(event.data)
        data.pop("entry_id", None)
        found.append(data)

    hass.bus.async_listen("roomba_plus_room_completed", _keep)
    return found


class TestAPrimeRecording:
    @pytest.mark.asyncio
    async def test_it_records_shadows_and_the_timeline_with_their_source(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        robot = await _prime(hass, monkeypatch)
        await _call(hass, "mission_recording_start", robot.vacuum_entity_id)
        await _play(robot, PRIME_MISSION[:2])
        got = await _call(hass, "mission_recording_stop", robot.vacuum_entity_id)
        assert got["robot"]["generation"] == "prime"
        assert got["robot"]["sku_prefix"] == "g"
        assert got["initial"] == {"shadows": {"ro-currentstate": _state("charge", "none", 100)}}
        assert [(m["t"], m["source"]) for m in got["messages"]] == [
            (1.0, "shadow"), (2.0, "timeline"),
        ]
        await robot.unload()

    @pytest.mark.asyncio
    async def test_the_replayed_recording_completes_the_same_rooms(
        self, hass, monkeypatch, enable_custom_integrations,
    ):
        """Recorded through the real Prime setup, then played from the
        recording alone into a fresh one: the same room events, in order."""
        events = _room_events(hass)
        first = await _prime(hass, monkeypatch)
        await _call(hass, "mission_recording_start", first.vacuum_entity_id)
        await _play(first, PRIME_MISSION)
        recording = await _call(hass, "mission_recording_stop", first.vacuum_entity_id)
        await first.unload()
        await hass.config_entries.async_remove(first.entry.entry_id)
        await hass.async_block_till_done()
        recorded = list(events)
        events.clear()

        second = PrimeReplay(hass, monkeypatch)
        await second.setup(
            recording["initial"]["shadows"], sku="G185020",
        )
        await second.play(recording)

        # The history read after the mission is in it, as an answer.
        assert [m["source"] for m in recording["messages"]][-1] == "history"
        assert [e["room_id"] for e in recorded] == ["10", "11"]
        assert events == recorded
        await second.unload()
