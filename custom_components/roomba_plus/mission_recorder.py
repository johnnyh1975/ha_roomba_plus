"""A mission, recorded so it can be replayed as a test (4.7.0).

WHY. Most field fixes since 4.5 were about the mission's course: when it
starts and ends, which room the robot is in, what counts as done. Each
was found by a tester whose robot did something no test built -- a
robot without positions, a zone clean, a start from the app -- and
#257 needed several rounds because a fix could only be checked against
a log of decisions, not against what the robot had said. A recording is
what the robot said: every state message, in order, with its time.
`tests/replay.py` sends it through the real code.

WHAT IS KEPT. The state the robot reports, through an allow-list of the
keys the mission logic reads; anything else -- a network block, a name,
whatever a future firmware adds -- is left out rather than redacted
after the fact. Values are redacted as in the diagnostics and the debug
capture: robot ids, addresses, e-mail, token-shaped strings. Map ids
become `map-1`, `map-2` ... consistently within one recording.

POSITIONS ARE KEPT, because room tracking cannot be replayed without
them. They are millimetres from the dock with no map and no origin
attached (decided 28.11.), and only recorded on an explicit start.

THE ROOMS, WITHOUT THEIR NAMES. On a robot with a Smart Map the room
course runs on the cloud's rooms: which region ids the map has and how
long each takes. A message names the regions by id only, so a recording
without them could not be replayed. Each room is kept by id, map
placeholder, type and the cloud's time estimates -- never by name.

PRIME (4.7.0b2). A Prime robot says what a Classic robot says in one
stream through three: the named shadows (`ro-currentstate`, `ro-stats`;
the same reported keys as above), the mission timeline, and the mission
history read after a mission. Each message carries its `source`. The
timeline and the history are kept whole, cleaned the same way, except
for polygons (`poly`, the outline of a room: that is the map).

NO NAMES ANYWHERE. A key that names something (`name`, `region_name`,
`roomName` ...) is left out at every depth: a start from Home Assistant
can carry the room names in `lastCommand`.

Kept in memory, never written or sent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .debug_capture import redact_text
from .redaction import _is_secret_key
from .reported_state import roomba_reported_state

_LOGGER = logging.getLogger(__name__)

FORMAT_VERSION = 1
DEFAULT_MINUTES = 180
MAX_MINUTES = 360
MAX_MESSAGES = 20_000

#: Top-level keys of the reported state a recording keeps.
#:
#: Read by the mission course, the room tracking, the map, the dock and
#: the maintenance counters. Not kept: `netinfo`, `wlcfg`, `signal`,
#: `mac`, `name`, `cleanSchedule`/`cleanSchedule2` (when the home is
#: empty), `sku` and `softwareVer` (in the header, shortened).
REPORTED_KEYS: frozenset[str] = frozenset({
    "batPct", "batInfo", "batteryType", "bbchg", "bbchg3", "bbmssn",
    "bbnav", "bbpause", "bbrstinfo", "bbrun", "bin", "binPause", "cap",
    "carpetBoost", "cleanMissionStatus", "detectedPad", "dock",
    "ecoCharge", "lastCommand", "lidOpen", "mapUploadAllowed",
    "missionTelemetry", "mopReady", "mssnNavStats", "noAutoPasses",
    "openOnly", "padWetness", "pmapLearningAllowed", "pmaps", "pose",
    "rankOverlap", "runtimeStats", "schedHold", "tankLvl", "tankPresent",
    "twoPass", "vacHigh", "wifistat",
})

#: Keys whose string value is a map id.
_MAP_ID_KEYS: frozenset[str] = frozenset({
    "pmap_id", "p2map_id", "pmapId", "p2mapId", "mapId", "confirmedMapId",
})

#: Left out at every depth: a room's outline is the map.
_DROPPED_KEYS: frozenset[str] = frozenset({"poly"})

#: The Prime shadows a recording keeps, through `REPORTED_KEYS`.
PRIME_SHADOWS: tuple[str, ...] = ("ro-currentstate", "ro-stats")

#: Prime's own spelling of a counter Classic calls `runtimeStats`.
_PRIME_KEYS: frozenset[str] = frozenset({"runtimestats"})


def _is_name_key(key: str) -> bool:
    """`name`, `region_name`, `roomName`, `pmapName` -- not `nMssn`."""
    return key.lower().endswith("name")


class MissionRecording:
    """One robot's recording."""

    def __init__(self, robot_ids: tuple[str, ...]) -> None:
        self._robot_ids = robot_ids
        self._maps: dict[str, str] = {}
        self.header: dict[str, Any] = {}
        self.initial: dict[str, Any] = {}
        self.rooms: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.dropped = 0
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        #: `time.monotonic()` at the start: the seconds in each message
        #: are counted from here, so a clock change cannot reorder them.
        self._t0 = 0.0
        self._unsub_message: Callable[[], None] | None = None
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._active = False

    @property
    def running(self) -> bool:
        return self._active

    # ── cleaning ────────────────────────────────────────────────────────────

    def _map(self, value: str) -> str:
        if value not in self._maps:
            self._maps[value] = f"map-{len(self._maps) + 1}"
        return self._maps[value]

    def _clean(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                name = str(k)
                if key == "pmaps_entry":
                    # A pmaps entry is {map id: version}.
                    out[self._map(name)] = self._clean(v, name)
                    continue
                if _is_secret_key(name) or _is_name_key(name) or name in _DROPPED_KEYS:
                    # Not in any key the mission logic reads today; a
                    # firmware that adds one does not get it recorded.
                    continue
                out[name] = self._clean(v, name)
            return out
        if isinstance(value, list):
            child = "pmaps_entry" if key == "pmaps" else key
            return [self._clean(v, child) for v in value]
        if isinstance(value, str):
            if key in _MAP_ID_KEYS:
                return self._map(value)
            return redact_text(value, self._robot_ids)
        return value

    def clean_reported(self, reported: Any) -> dict[str, Any]:
        """The kept part of one reported state, cleaned."""
        if not isinstance(reported, dict):
            return {}
        return {
            k: self._clean(v, k) for k, v in reported.items()
            if k in REPORTED_KEYS or k in _PRIME_KEYS
        }

    def clean_shadows(self, shadows: Any) -> dict[str, dict[str, Any]]:
        """The kept Prime shadows, each cleaned like a reported state."""
        if not isinstance(shadows, dict):
            return {}
        kept = {name: self.clean_reported(shadows.get(name)) for name in PRIME_SHADOWS}
        return {name: reported for name, reported in kept.items() if reported}

    def clean_rooms(self, regions: Any) -> list[dict[str, Any]]:
        """The cloud's rooms of the active map, without their names.

        After `clean_reported` on the first state, so a map id already
        seen in `pmaps` gets the same placeholder here.
        """
        out: list[dict[str, Any]] = []
        for region in regions or ():
            if not isinstance(region, dict) or not region.get("id"):
                continue
            pmap = region.get("pmap_id")
            estimates = region.get("time_estimates")
            out.append({
                "id": str(region["id"]),
                "pmap_id": self._map(str(pmap)) if pmap else None,
                "region_type": str(region.get("region_type") or "default"),
                "time_estimates": {
                    k: v for k, v in (estimates or {}).items()
                    if k in ("one_pass_sec", "two_pass_sec") and (v is None or isinstance(v, int))
                } if isinstance(estimates, dict) else {},
            })
        return out

    # ── recording ───────────────────────────────────────────────────────────

    def _keep(self, message: dict[str, Any]) -> None:
        if len(self.messages) >= MAX_MESSAGES:
            self.dropped += 1
            return
        self.messages.append({"t": round(time.monotonic() - self._t0, 3), **message})

    def add(self, decoded: Any) -> None:
        """One message as roombapy delivered it to its callbacks."""
        if not isinstance(decoded, dict):
            return
        reported = (decoded.get("state") or {}).get("reported")
        kept = self.clean_reported(reported)
        if kept:
            self._keep({"reported": kept})

    def add_shadow(self, shadow: str, reported: Any) -> None:
        """One Prime shadow update, the delta as it arrived."""
        if shadow not in PRIME_SHADOWS:
            return
        kept = self.clean_reported(reported)
        if kept:
            self._keep({"source": "shadow", "shadow": shadow, "reported": kept})

    def add_payload(self, source: str, payload: Any) -> None:
        """One Prime timeline delta or history read, kept whole and cleaned."""
        if isinstance(payload, (dict, list)):
            self._keep({"source": source, "payload": self._clean(payload)})

    @callback
    def start(
        self, hass: HomeAssistant, roomba: Any, header: dict[str, Any],
        initial: dict[str, Any], minutes: int,
        rooms: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record for `minutes` at most.

        A Classic robot (`roomba` given) is listened to through its client;
        `initial` is its reported state. A Prime robot's coordinators hand
        their messages over (`note`); `initial` is its shadows.
        """
        self.header = header
        self.initial = (
            self.clean_reported(initial) if roomba is not None
            else {"shadows": self.clean_shadows(initial)}
        )
        self.rooms = self.clean_rooms(rooms)
        self.started_at = dt_util.utcnow()
        self._t0 = time.monotonic()
        self._active = True
        if roomba is not None:
            self._unsub_message = roomba.register_on_message_callback(self.add)

        @callback
        def _expire(_now: Any) -> None:
            self._unsub_timer = None
            self.stop()

        self._unsub_timer = async_call_later(hass, minutes * 60, _expire)

    @callback
    def stop(self) -> None:
        """Stop listening; what was kept stays until the next start."""
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_message is not None:
            self._unsub_message()
            self._unsub_message = None
        if self._active:
            self._active = False
            self.stopped_at = dt_util.utcnow()

    def report(self) -> dict[str, Any]:
        """What was recorded, for the action's response."""
        def _iso(ts: datetime | None) -> str | None:
            return None if ts is None else ts.isoformat()

        return {
            "format": FORMAT_VERSION,
            "running": self.running,
            "started_at": _iso(self.started_at),
            "stopped_at": _iso(self.stopped_at),
            "robot": dict(self.header),
            "initial": dict(self.initial),
            "rooms": [dict(r) for r in self.rooms],
            "message_count": len(self.messages),
            "dropped_messages": self.dropped,
            "messages": list(self.messages),
        }


_DATA_KEY = f"{DOMAIN}_mission_recordings"


def recordings(hass: HomeAssistant) -> dict[str, MissionRecording]:
    """The recordings kept in memory, by entry id."""
    found: dict[str, MissionRecording] = hass.data.setdefault(_DATA_KEY, {})
    return found


def _header(
    entry: Any, reported: dict[str, Any], version: str | None, *, prime: bool = False,
) -> dict[str, Any]:
    data = getattr(entry, "runtime_data", None)
    sku = reported.get("sku")
    if prime:
        sku = getattr(getattr(data, "prime_serial_info", None), "sku", None)
    firmware = reported.get("softwareVer")
    capability = getattr(getattr(data, "map_capability", None), "value", None)
    return {
        "generation": "prime" if prime else "classic",
        "sku_prefix": sku[:1].lower() if isinstance(sku, str) and sku else None,
        "firmware": firmware if isinstance(firmware, str) else None,
        "map_capability": capability,
        "cloud": bool(getattr(data, "has_cloud", False)),
        "integration": version,
    }


@callback
def async_start(
    hass: HomeAssistant, entry: Any, minutes: int, version: str | None = None
) -> MissionRecording:
    """Start (or restart) the recording of one robot, either generation."""
    found = recordings(hass)
    old = found.get(entry.entry_id)
    if old is not None:
        old.stop()
    data = entry.runtime_data
    blid = getattr(data, "blid", None)
    recording = MissionRecording((blid,) if isinstance(blid, str) and blid else ())
    if data.roomba is None:
        status = getattr(data, "prime_status_coordinator", None)
        shadows = dict(getattr(status, "data", None) or {})
        current = shadows.get("ro-currentstate") or {}
        recording.start(
            hass, None, _header(entry, current, version, prime=True), shadows, minutes,
        )
    else:
        reported = roomba_reported_state(data.roomba)
        cloud = getattr(data, "cloud_coordinator", None)
        rooms = list(getattr(cloud, "regions", None) or [])
        recording.start(
            hass, data.roomba, _header(entry, reported, version), reported, minutes, rooms,
        )
    found[entry.entry_id] = recording
    return recording


@callback
def async_stop_for_entry(hass: HomeAssistant, entry_id: str) -> None:
    """On unload: stop recording, keep what was recorded.

    The robot's client goes with the entry, so nothing more arrives; but
    a reload is no reason to lose a mission someone was recording -- it
    can still be fetched afterwards.
    """
    recording = recordings(hass).get(entry_id)
    if recording is not None:
        recording.stop()


def note(hass: Any, entry_id: str, source: str, payload: Any, shadow: str | None = None) -> None:
    """A Prime coordinator hands over one message, if a recording runs.

    Called on every shadow update and timeline delta, so it does nothing
    but a dictionary lookup unless the robot is being recorded -- and it
    never raises into the coordinator that called it.
    """
    try:
        found = hass.data.get(_DATA_KEY) if hass is not None else None
        recording = found.get(entry_id) if found else None
        if recording is None or not recording.running:
            return
        if source == "shadow" and shadow is not None:
            recording.add_shadow(shadow, payload)
        else:
            recording.add_payload(source, payload)
    except Exception:  # noqa: BLE001 - a recording must never break the robot
        _LOGGER.debug("Mission recording: a %s message was not kept", source, exc_info=True)
