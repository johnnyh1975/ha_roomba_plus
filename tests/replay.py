"""Replay what a Classic robot said through the real integration (4.7.0).

WHY. The field fixes of 4.5 and 4.6 were checked against logs of the
integration's decisions, and the replays built from them drove one
callback with hand-made dependencies (tests/test_replay_field_mission.py).
That proves the callback; it does not prove the setup wired it, that the
other callbacks on the same message agree, or that the stores hold what
the callback left in them. A field regression has come through each of
those gaps at least once.

HOW. A robot entry is set up through Home Assistant's own entry manager
with a REAL roombapy client that never connects: `_phase_connect` is the
only phase replaced, and it builds the client the way the real one does.
Messages go in through `RoombaClient._handle_message`, the method the
MQTT read loop calls, so the library's state machine merges them and
fans them out to every callback the setup registered, in order.

TIME. One clock for the whole integration: every module that reads
`time.time()` or `time.monotonic()` -- directly or through an imported
`monotonic` -- reads `ReplayClock` instead, so a mission of an hour runs
in milliseconds and the gates that wait seconds see them pass. The event
loop keeps its own clock; patching `time.monotonic` itself would stall
every `asyncio.sleep`.

WHAT IS NOT REAL. Entity platforms are not set up (no entities, only the
vacuum's registry entry so the actions can be targeted), and nothing is
sent to a robot or fetched from the internet. A cloud account is
optional: given `cloud_pmaps`, the real cloud coordinator runs with only
its fetch replaced, answering with those maps -- rooms and time
estimates come from there, as on a robot with a Smart Map.
"""

from __future__ import annotations

import json
import sys
import time as _real_time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from roombapy import RoombaClient

import custom_components.roomba_plus as integration
from custom_components.roomba_plus import parts_catalog
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator
from custom_components.roomba_plus.const import DOMAIN
from custom_components.roomba_plus.reported_state import roomba_reported_state

BLID = "0123456789ABCDEF"
HOST = "192.0.2.10"

#: Wall-clock start of every replay: 2026-09-18 16:09:41 UTC, the start
#: of the Thonno mission the first replay rebuilds.
EPOCH = 1_789_747_781.0


class ReplayClock:
    """Stands in for the `time` module wherever the integration reads it."""

    def __init__(self) -> None:
        self.elapsed = 0.0

    def time(self) -> float:
        return EPOCH + self.elapsed

    def monotonic(self) -> float:
        return 10_000.0 + self.elapsed

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)


def _bound_to_clock(value: Any, name: str) -> bool:
    """A clock method a previous replay in the same test installed."""
    return getattr(value, "__name__", None) == name and isinstance(
        getattr(value, "__self__", None), ReplayClock
    )


def _install_clock(monkeypatch: pytest.MonkeyPatch, clock: ReplayClock) -> None:
    prefix = f"{integration.__name__}."
    for name, module in list(sys.modules.items()):
        if module is None or not (name == integration.__name__ or name.startswith(prefix)):
            continue
        for attr, value in list(vars(module).items()):
            if value is _real_time or isinstance(value, ReplayClock):
                monkeypatch.setattr(module, attr, clock)
            elif value is _real_time.monotonic or _bound_to_clock(value, "monotonic"):
                monkeypatch.setattr(module, attr, clock.monotonic)
            elif value is _real_time.time or _bound_to_clock(value, "time"):
                monkeypatch.setattr(module, attr, clock.time)


Seconds = int | dict[str, int | None] | None


def smart_map(pmap_id: str, rooms: list[tuple[str, str, Seconds]]) -> list[dict[str, Any]]:
    """One cloud Smart Map: `(region id, name, seconds)` per room.

    The shape of `tests/fixtures/irobot_pmaps_i3plus.json`, cut to what the
    mission course reads. Seconds are the one-pass estimate, or a recording's
    `{"one_pass_sec": ..., "two_pass_sec": ...}`; a room without them has no
    estimate the cloud is confident in.
    """
    regions = []
    for rid, name, seconds in rooms:
        region: dict[str, Any] = {"id": rid, "name": name, "region_type": "default"}
        if isinstance(seconds, int):
            seconds = {"one_pass_sec": seconds}
        estimates = []
        for key, two_pass in (("one_pass_sec", False), ("two_pass_sec", True)):
            value = (seconds or {}).get(key)
            if isinstance(value, int):
                estimates.append({
                    "confidence": "GOOD_CONFIDENCE",
                    "params": {"noAutoPasses": True, "twoPass": two_pass},
                    "estimate": value,
                })
        if estimates:
            region["time_estimates"] = estimates
        regions.append(region)
    return [{
        "pmap_id": pmap_id,
        "user_pmapv_id": "260918T160000",
        "active_pmapv_id": "260918T160000",
        "visible": True,
        "active_pmapv_details": {
            "active_pmapv": {"pmap_id": pmap_id, "pmapv_id": "260918T160000",
                             "creator": "user", "proc_state": "OK_Processed"},
            "regions": regions, "zones": [], "keepoutzones": [], "observed_zones": [],
        },
    }]


def _cloud_answering(pmaps: list[dict[str, Any]]) -> type[IrobotCloudCoordinator]:
    class _Cloud(IrobotCloudCoordinator):
        """The real coordinator; only the fetch is replaced."""

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()
            self.last_update_success = True

        async def _async_update_data(self) -> dict[str, Any]:
            return {"pmaps": pmaps, "mission_history_raw": [], "parts": {}, "umf": {}}

    return _Cloud


class Replay:
    """One Classic robot, set up for real, fed by hand."""

    def __init__(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch,
        *, blid: str = BLID, host: str = HOST,
    ) -> None:
        self.hass = hass
        self.blid = blid
        self.host = host
        self.clock = ReplayClock()
        self._monkeypatch = monkeypatch
        self.client: RoombaClient | None = None
        self.entry: MockConfigEntry | None = None
        self.vacuum_entity_id = ""

    async def setup(
        self,
        reported: dict[str, Any],
        *,
        options: dict[str, Any] | None = None,
        title: str = "Robbie",
        cloud_pmaps: list[dict[str, Any]] | None = None,
    ) -> MockConfigEntry:
        """Set the robot up with `reported` as the state it reported first."""
        mp = self._monkeypatch
        _install_clock(mp, self.clock)
        for fetch in ("async_fetch_help", "async_fetch_catalogue"):
            mp.setattr(parts_catalog, fetch, AsyncMock(return_value={}))
        data = {"blid": self.blid, "host": self.host, "password": "password"}
        if cloud_pmaps is not None:
            mp.setattr(integration, "IrobotCloudCoordinator", _cloud_answering(cloud_pmaps))
            data |= {"irobot_username": "user@example.com", "irobot_password": "password"}
        client = RoombaClient(self.host, self.blid, "password")
        self.client = client
        self.send(reported)

        async def _connect(ctx: Any) -> bool:
            ctx.roomba = client
            ctx.state = roomba_reported_state(client)
            return True

        mp.setattr(integration, "_phase_connect", _connect)
        entry = MockConfigEntry(
            domain=DOMAIN, version=25, unique_id=self.blid, title=title,
            data=data, options=options or {},
        )
        entry.add_to_hass(self.hass)
        mp.setattr(self.hass.config_entries, "async_forward_entry_setups", AsyncMock())
        mp.setattr(
            self.hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True),
        )
        mp.setattr(self.hass, "http", MagicMock())
        assert await self.hass.config_entries.async_setup(entry.entry_id)
        await self.hass.async_block_till_done()
        self.vacuum_entity_id = er.async_get(self.hass).async_get_or_create(
            "vacuum", DOMAIN, self.blid, config_entry=entry,
        ).entity_id
        self.entry = entry
        return entry

    def send(self, reported: dict[str, Any]) -> None:
        """One state message, as the read loop hands it to roombapy."""
        assert self.client is not None
        payload = json.dumps({"state": {"reported": reported}}).encode()
        self.client._handle_message(f"$aws/things/{self.blid}/shadow/update", payload)

    async def at(self, seconds: float, reported: dict[str, Any]) -> None:
        """Move the clock to `seconds` after the start, then send."""
        assert seconds >= self.clock.elapsed, "a replay does not go back in time"
        self.clock.elapsed = seconds
        self.send(reported)
        await self.hass.async_block_till_done()

    async def setup_from(self, recording: dict[str, Any]) -> MockConfigEntry:
        """Set the robot up as it was when the recording started.

        Its first state is the recording's `initial`. Its rooms, if the
        recording has any, are a cloud map with the recorded ids and
        estimates; the names are made up from the ids, since a recording
        has none (the room course matches rooms by id and estimates by
        the cloud's own name, so any consistent name will do).
        """
        assert recording.get("format") == 1, recording.get("format")
        rooms = recording.get("rooms") or []
        pmaps = None
        if rooms:
            pmaps = smart_map(
                str(rooms[0].get("pmap_id") or "map-1"),
                [(r["id"], f"Room {r['id']}", r.get("time_estimates")) for r in rooms],
            )
        return await self.setup(recording["initial"], cloud_pmaps=pmaps)

    async def play(self, recording: dict[str, Any]) -> None:
        """A recording from `mission_recording_get`, message by message."""
        assert recording.get("format") == 1, recording.get("format")
        for message in recording["messages"]:
            await self.at(float(message["t"]), message["reported"])

    async def unload(self) -> None:
        assert self.entry is not None
        assert await self.hass.config_entries.async_unload(self.entry.entry_id)
        await self.hass.async_block_till_done()


# ── Prime (4.7.0b2) ──────────────────────────────────────────────────────────
#
# A Prime robot has no client the integration builds: the account hands
# out a `PrimeRobot`, whose streams are async generators. The fake below
# is that robot with its network replaced -- every stream reads from a
# queue the replay fills, and every read the setup makes answers from
# the state the replay was given. The coordinators, stores and callbacks
# around it are the integration's own, set up through the entry manager.

PRIME_BLID = "0123456789ABCDEF0123456789ABCDEF"


class _Stream:
    """One of the robot's watch_* streams, fed by the replay.

    `join()` returns once the consumer has finished with every message:
    a message counts as handled when the consumer asks for the next one.
    """

    def __init__(self) -> None:
        import asyncio

        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def watch(self, *_args: Any, **_kwargs: Any) -> Any:
        while True:
            item = await self.queue.get()
            try:
                yield item
            finally:
                self.queue.task_done()


class FakePrimeRobot:
    """A `roombapy_prime.PrimeRobot` without a network."""

    def __init__(self, blid: str, sku: str, shadows: dict[str, dict[str, Any]]) -> None:
        from roombapy_prime.models import RobotSerialInfo

        self.blid = blid
        self.shadows = shadows
        self.history: Any = []
        #: History reads a recording made, answered in order.
        self.answers: list[Any] = []
        self.serial = RobotSerialInfo(serial_number="SN-REPLAY", sku=sku)
        self.named = _Stream()
        self.timeline = _Stream()
        self._idle = _Stream()
        self.watch_named_shadows_updates = self.named.watch
        self.watch_mission_timeline = self.timeline.watch
        self.watch_rejected_commands = self._idle.watch
        self.watch_dock_reports = self._idle.watch
        self.watch_live_map = self._idle.watch

    #: The broker never moves this robot's topics in a replay.
    topic_prefix_changed = False

    def set_topic_prefix_changed_callback(self, _callback: Any) -> None:
        return None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def get_named_shadow(self, name: str) -> Any:
        return SimpleNamespace(payload={"state": {"reported": self.shadows.get(name, {})}})

    async def get_state(self) -> Any:
        return SimpleNamespace(payload={"state": {"reported": self.shadows.get("classic", {})}})

    async def get_household_id(self) -> str:
        return "household-1"

    async def get_serial_number_data(self) -> Any:
        return self.serial

    async def get_mission_history(self, *_args: Any, **_kwargs: Any) -> Any:
        """The next recorded answer, or the last one again."""
        if self.answers:
            self.history = self.answers.pop(0)
        return self.history

    def __getattr__(self, name: str) -> Any:
        # Everything else the setup reads (parts, favorites, maps,
        # schedules, time estimates) has nothing to say in a replay.
        if name.startswith(("get_", "download_", "send_", "set_", "update_", "create_", "delete_")):
            return AsyncMock(return_value=None)
        raise AttributeError(name)


class PrimeReplay:
    """One Prime robot, set up for real, fed by hand."""

    def __init__(self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch,
                 *, blid: str = PRIME_BLID) -> None:
        self.hass = hass
        self.clock = ReplayClock()
        self._monkeypatch = monkeypatch
        self.blid = blid
        self.robot: FakePrimeRobot | None = None
        self.entry: MockConfigEntry | None = None
        self.vacuum_entity_id = ""

    async def setup(
        self, shadows: dict[str, dict[str, Any]], *, sku: str = "G185020",
        title: str = "Prime", history: Any = None,
    ) -> MockConfigEntry:
        mp = self._monkeypatch
        _install_clock(mp, self.clock)
        for fetch in ("async_fetch_help", "async_fetch_catalogue"):
            mp.setattr(parts_catalog, fetch, AsyncMock(return_value={}))
        robot = FakePrimeRobot(self.blid, sku, {k: dict(v) for k, v in shadows.items()})
        robot.history = history or []
        self.robot = robot
        account = MagicMock()
        account.generation.return_value = "prime"
        account.prime_robot = AsyncMock(return_value=robot)
        mp.setattr(integration, "async_acquire", AsyncMock(return_value=account))
        mp.setattr(integration, "async_release", MagicMock())
        entry = MockConfigEntry(
            domain=DOMAIN, version=25, unique_id=self.blid, title=title,
            data={
                "connection_type": "cloud_only", "blid": self.blid,
                "irobot_username": "user@example.com", "irobot_password": "password",
            },
        )
        entry.add_to_hass(self.hass)
        mp.setattr(self.hass.config_entries, "async_forward_entry_setups", AsyncMock())
        mp.setattr(
            self.hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True),
        )
        mp.setattr(self.hass, "http", MagicMock())
        assert await self.hass.config_entries.async_setup(entry.entry_id)
        await self._settle()
        self.vacuum_entity_id = er.async_get(self.hass).async_get_or_create(
            "vacuum", DOMAIN, self.blid, config_entry=entry,
        ).entity_id
        self.entry = entry
        return entry

    async def _settle(self) -> None:
        assert self.robot is not None
        await self.hass.async_block_till_done()
        await self.robot.named.queue.join()
        await self.robot.timeline.queue.join()
        await self.hass.async_block_till_done()

    async def shadow(self, seconds: float, name: str, reported: dict[str, Any]) -> None:
        """Move the clock, then one named shadow update."""
        assert self.robot is not None and seconds >= self.clock.elapsed
        self.clock.elapsed = seconds
        self.robot.named.queue.put_nowait(SimpleNamespace(
            topic=f"$aws/things/{self.blid}/shadow/name/{name}/update/accepted",
            payload={"state": {"reported": reported}},
        ))
        await self._settle()

    async def timeline(self, seconds: float, payload: dict[str, Any]) -> None:
        """Move the clock, then one mission timeline delta."""
        assert self.robot is not None and seconds >= self.clock.elapsed
        self.clock.elapsed = seconds
        self.robot.timeline.queue.put_nowait(SimpleNamespace(payload=payload))
        await self._settle()

    async def play(self, recording: dict[str, Any]) -> None:
        """A Prime recording, message by message. Each history read is
        what the robot answers when the integration next asks."""
        assert recording.get("format") == 1 and recording["robot"]["generation"] == "prime"
        assert self.robot is not None
        # A history read is an answer to a request the integration makes
        # itself, so the answers are lined up before the messages that
        # lead to the requests.
        self.robot.answers = [
            m["payload"] for m in recording["messages"] if m["source"] == "history"
        ]
        for message in recording["messages"]:
            t = float(message["t"])
            if message["source"] == "shadow":
                await self.shadow(t, message["shadow"], message["reported"])
            elif message["source"] == "timeline":
                await self.timeline(t, message["payload"])

    async def unload(self) -> None:
        assert self.entry is not None
        assert await self.hass.config_entries.async_unload(self.entry.entry_id)
        await self.hass.async_block_till_done()
