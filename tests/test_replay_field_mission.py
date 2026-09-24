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
    return {"raum_nach": raum_nach, "aufgezeichnet": aufgezeichnet,
            "meldungen": meldungen}


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

    def test_the_mission_closes_exactly_once_after_the_gate_opens(self, mission):
        """Log, 19:00:51: recorded once, as completed. Not during the
        burst — only when the hold passed 2.0 s."""
        assert mission["aufgezeichnet"] == [pytest.approx(3070.02)]
