"""A real mission, replayed: i755840 (lewis 22.52.10), 18 Sep 2026.

SOURCE. home-assistant_2026-09-18T17-10-07_230Z.log from the entry
01M2J5YH…, three planned rooms — Bagno principale, Corridoio, Cucina —
51 minutes, recorded as completed. Almost certainly Thonno's i7+ after a
re-install (same SKU and firmware; the entry id changes exactly at the
4.2.3/4.2.4 boundary of his diagnostics).

WHY THIS MISSION. It exercises every block Group 5 lifted out of the
mission callback, in one run: mission start, a dock drive that must NOT
advance the room, two drive ends that must, a drive end inside
`hmPostMsn` (a room-transition candidate) that must not, and then the
end gate refusing a docking burst three times before it lets the mission
close. That last part runs through the gate computation Group 5
consolidated, including the `first_ts == 0` case the log shows as
`time_held=0.000s`.

WHAT IS ASSERTED, AND FROM WHERE. Every expectation below is taken from
the field log — what the released code actually decided on the robot —
not from a run of the current code. A test that pinned today's output
would only prove the code agrees with itself. Before Group 5 and after
it, this replay produced identical output; with the time gate broken on
purpose, the three refusals disappeared. Both were checked when this
file was written.

WHAT IT CANNOT SHOW. The log records decisions, not raw MQTT values. The
input sequence is rebuilt so that it produces those decisions; the exact
agreement of the three refusal times (0.042 / 1.251 / 1.286 s) is what
says the rebuild is faithful. The phase names, cycle transitions and
drive durations are the robot's; the operatingMode values between drives
are the minimum that yields the logged edges.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _echte_zeit
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.test_callbacks import _make_callback_env

REINIGEN, FAHRT, ANGEDOCKT = 2, 1, 0
RAEUME = ["Bagno principale", "Corridoio", "Cucina"]

#: (Sekunden seit Missionsbeginn, Phase, operatingMode, cycle)
ABLAUF: list[tuple[float, str, int, str]] = [
    (0.0,      "run",       REINIGEN,  "clean"),   # 18:09:41 start
    (12.7,     "run",       FAHRT,     "clean"),   # dock drive begins
    (50.5,     "run",       REINIGEN,  "clean"),   # ends after 37.8 s
    (300.0,    "run",       REINIGEN,  "clean"),
    (651.2,    "run",       FAHRT,     "clean"),
    (665.4,    "run",       REINIGEN,  "clean"),   # 14.2 s → Corridoio
    (900.0,    "run",       REINIGEN,  "clean"),
    (1170.4,   "run",       FAHRT,     "clean"),
    (1175.4,   "run",       REINIGEN,  "clean"),   # 5.0 s → Cucina
    (2000.0,   "run",       REINIGEN,  "clean"),
    (2996.6,   "hmPostMsn", REINIGEN,  "clean"),   # 18:59:38 homebound
    (3026.6,   "hmPostMsn", FAHRT,     "clean"),
    (3057.2,   "hmPostMsn", REINIGEN,  "clean"),   # 30.6 s, no advance
    (3067.6,   "charge",    ANGEDOCKT, "none"),    # 19:00:49 docked
    (3067.642, "charge",    ANGEDOCKT, "none"),    # refused 0.042 s
    (3068.851, "charge",    ANGEDOCKT, "none"),    # refused 1.251 s
    (3068.886, "charge",    ANGEDOCKT, "none"),    # refused 1.286 s
    (3070.02,  "charge",    ANGEDOCKT, "none"),    # closes
]


class _Zeit:
    """Stands in for the callback module's `_time_mod`, which IS the
    `time` module. Patching `time.monotonic` itself would freeze the
    event loop's clock too and hang every `asyncio.sleep`."""

    def __init__(self, uhr: dict[str, float]) -> None:
        self._uhr = uhr

    def monotonic(self) -> float:
        return self._uhr["t"]

    def __getattr__(self, name: str) -> Any:
        return getattr(_echte_zeit, name)


@pytest.fixture
def mission(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Run the whole mission; return what the callback decided."""
    from custom_components.roomba_plus import callbacks
    from custom_components.roomba_plus.mission_timer_store import MissionTimerStore

    uhr = {"t": 1000.0}
    t0 = uhr["t"]
    monkeypatch.setattr(callbacks, "monotonic", lambda: uhr["t"])
    monkeypatch.setattr(callbacks, "_time_mod", _Zeit(uhr))

    aufgezeichnet: list[float] = []

    async def _record(*_a: Any, **_kw: Any) -> None:
        # The module-level recorder does battery arithmetic against
        # runtime_data. What this test is about is WHEN the mission
        # closes, so the body is replaced and the moment kept.
        aufgezeichnet.append(uhr["t"] - t0)

    monkeypatch.setattr(callbacks, "async_record_mission", _record)

    hass, entry, _, _ = _make_callback_env()
    entry.runtime_data.prime_status_coordinator = None

    mts = MissionTimerStore()
    mts.mission_id = f"{entry.data['blid']}_1789747781"
    mts.planned_rooms = list(RAEUME)
    mts.current_room_idx = 0
    mts.room_estimates_sec = [600.0, 500.0, 1900.0]
    mts.total_estimated_sec = 3000.0
    mts.room_entered_run_sec = 0.0
    mts._schedule_save = lambda *a, **kw: None
    entry.runtime_data.mission_timer_store = mts

    cb = callbacks.make_mission_callback(hass, entry)
    raum_nach: dict[float, int] = {}
    caplog.set_level(logging.DEBUG, logger="custom_components.roomba_plus.callbacks")

    try:
        for t, phase, mode, cycle in ABLAUF:
            uhr["t"] = t0 + t
            mts.run_sec = t
            cb({"state": {"reported": {
                "cleanMissionStatus": {
                    "phase": phase, "cycle": cycle, "sqft": 100,
                    "mssnStrtTm": 1789747781, "initiator": "schedule",
                    "error": 0, "operatingMode": mode,
                },
                "bbrun": {"nStuck": 277, "hr": 36},
            }}})
            hass.loop.run_until_complete(asyncio.sleep(0))
            raum_nach[t] = mts.current_room_idx
        hass.loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        hass.loop.close()

    meldungen = [r.getMessage() for r in caplog.records]
    stufen = {r.getMessage(): r.levelno for r in caplog.records}
    return {"raum_nach": raum_nach, "aufgezeichnet": aufgezeichnet,
            "meldungen": meldungen, "stufen": stufen}


class TestThonnoMissionOf18September:

    def test_the_dock_drive_does_not_advance_the_room(self, mission):
        """37.8 s of travel from the dock, nothing cleaned yet — the log
        shows a boundary candidate and no advance."""
        assert mission["raum_nach"][50.5] == 0

    def test_the_first_real_drive_end_advances_to_corridoio(self, mission):
        """Log, 18:20:46: advanced to room 2/3 (Corridoio)."""
        assert mission["raum_nach"][665.4] == 1

    def test_the_second_drive_end_advances_to_cucina(self, mission):
        """Log, 18:29:16: advanced to room 3/3 (Cucina)."""
        assert mission["raum_nach"][1175.4] == 2

    def test_a_drive_end_while_homebound_does_not_advance(self, mission):
        """Log, 19:00:08: travel ended after 30.6 s in hmPostMsn — a
        candidate, and no advance: the last room was already current."""
        assert mission["raum_nach"][3057.2] == 2

    def test_the_end_gate_refuses_the_docking_burst_three_times(self, mission):
        """Log, 19:00:49–19:00:50: three refusals, and these exact holds.
        This is the gate Group 5 computed once instead of twice."""
        gehalten = [
            float(m)
            for text in mission["meldungen"] if "burst rejected" in text
            for m in re.findall(r"time_held=([0-9.]+)s", text)
        ]
        assert gehalten == [0.042, 1.251, 1.286]

    def test_the_refusals_are_debug_lines_not_info(self, mission):
        """The gate holding the end back for 2 s is the design, not a
        problem; at INFO it read as one in @pk-1966's log (#207)."""
        stufen = [lvl for text, lvl in mission["stufen"].items() if "burst rejected" in text]
        assert stufen and set(stufen) == {logging.DEBUG}

    def test_the_mission_closes_exactly_once_after_the_gate_opens(self, mission):
        """Log, 19:00:51: recorded once, as completed. Not during the
        burst — only when the hold passed 2.0 s."""
        assert mission["aufgezeichnet"] == [pytest.approx(3070.02)]


# ── The same mission through the real setup (4.7.0) ─────────────────────────
#
# The fixture above drives the mission callback alone, with its
# dependencies built by hand. Here the robot is set up through Home
# Assistant's entry manager with a real roombapy client and a cloud that
# answers with the three rooms (tests/replay.py), and the mission goes in
# as MQTT payloads: every callback the setup registered sees every
# message, and the room course comes from the stores setup made. Nothing
# about the mission timer is set by hand.
#
# The cloud's estimates are the ones the fixture above used; the field
# log does not show them, and the room gates need them. Everything else
# -- the plan, the clock in the timer, the end gate -- is the
# integration's own.

THONNO_ROOMS = [("1", "Bagno principale", 600), ("2", "Corridoio", 500), ("3", "Cucina", 1900)]

#: The robot as setup finds it: an i7+ on its dock, with a Smart Map.
THONNO_ROBOT: dict[str, Any] = {
    "name": "Robbie", "sku": "i755840", "softwareVer": "lewis+22.52.10",
    "cap": {"pose": 1, "pmaps": 4}, "pmaps": [{"map-1": "260918T160000"}],
    "bbrun": {"hr": 36, "nStuck": 277},
    "cleanMissionStatus": {
        "phase": "charge", "cycle": "none", "operatingMode": 0, "error": 0,
        "mssnStrtTm": 0, "sqft": 0, "initiator": "none",
    },
}

#: The scheduled start that names the three rooms, sent before the mission.
THONNO_COMMAND: dict[str, Any] = {"lastCommand": {
    "command": "start", "initiator": "schedule", "pmap_id": "map-1",
    "regions": [
        {"region_id": rid, "type": "rid", "params": {"noAutoPasses": True, "twoPass": False}}
        for rid, _, _ in THONNO_ROOMS
    ],
}}


def thonno_messages() -> list[tuple[float, dict[str, Any]]]:
    """The mission as the robot would have sent it: (seconds, reported)."""
    return [
        (t, {"cleanMissionStatus": {
            "phase": phase, "cycle": cycle, "sqft": 100, "mssnStrtTm": 1789747781,
            "initiator": "schedule", "error": 0, "operatingMode": mode,
        }})
        for t, phase, mode, cycle in ABLAUF
    ]


class ThonnoRun:
    """What the integration decided while a replay ran."""

    def __init__(self, replay: Any, monkeypatch: pytest.MonkeyPatch,
                 caplog: pytest.LogCaptureFixture) -> None:
        from custom_components.roomba_plus import callbacks

        self.replay = replay
        self.closed_at: list[float] = []
        self.room_after: dict[float, int] = {}
        self._caplog = caplog
        record = callbacks.async_record_mission

        async def _record(*args: Any, **kwargs: Any) -> Any:
            # The real recorder runs; only the moment is noted.
            self.closed_at.append(replay.elapsed)
            return await record(*args, **kwargs)

        monkeypatch.setattr(callbacks, "async_record_mission", _record)
        caplog.set_level(logging.DEBUG, logger="custom_components.roomba_plus.callbacks")

    async def setup(self) -> Any:
        from tests.replay import smart_map

        entry = await self.replay.setup(THONNO_ROBOT, cloud_pmaps=smart_map("map-1", THONNO_ROOMS))
        self.replay.send(THONNO_COMMAND)
        return entry

    async def setup_from(self, recording: dict[str, Any]) -> Any:
        """From a recording alone: no command and no rooms given here."""
        return await self.replay.setup_from(recording)

    async def at(self, seconds: float, reported: dict[str, Any]) -> None:
        await self.replay.at(seconds, reported)
        mts = self.replay.entry.runtime_data.mission_timer_store
        self.room_after[seconds] = mts.current_room_idx

    def held(self) -> list[float]:
        return [
            float(m)
            # The mission runs in the fixture, so its lines are the setup phase's.
            for record in self._caplog.get_records("setup")
            if "burst rejected" in record.getMessage()
            for m in re.findall(r"time_held=([0-9.]+)s", record.getMessage())
        ]


@pytest.fixture
async def thonno(hass, monkeypatch, caplog, enable_custom_integrations):
    from tests.replay import Replay

    run = ThonnoRun(Replay(hass, monkeypatch), monkeypatch, caplog)
    await run.setup()
    for seconds, reported in thonno_messages():
        await run.at(seconds, reported)
    yield run
    await run.replay.unload()


class TestThonnoMissionThroughTheRealSetup:
    """Each expectation is the field log's, as in the class above."""

    @pytest.mark.asyncio
    async def test_setup_planned_the_three_rooms_from_the_command(self, thonno):
        assert thonno.room_after[0.0] == 0
        assert thonno.replay.entry.runtime_data.mission_timer_store is not None

    @pytest.mark.asyncio
    async def test_the_rooms_advance_where_the_log_says(self, thonno):
        after = thonno.room_after
        assert (after[50.5], after[665.4], after[1175.4], after[3057.2]) == (0, 1, 2, 2)

    @pytest.mark.asyncio
    async def test_the_end_gate_refuses_the_docking_burst_three_times(self, thonno):
        assert thonno.held() == [0.042, 1.251, 1.286]

    @pytest.mark.asyncio
    async def test_the_mission_closes_exactly_once_after_the_gate_opens(self, thonno):
        assert thonno.closed_at == [pytest.approx(3070.02)]
