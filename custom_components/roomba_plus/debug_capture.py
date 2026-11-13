"""A debug log a tester can hand over without reading it first (4.6.0, A1).

WHY. Several field questions were settled only by a debug log:
@nareso's dots on the cleaning path (27 Oct), the drops of two Prime
robots on one account (@Baby-scarf, #243). Getting one meant a
`logger:` block in `configuration.yaml`, a restart, and then a tester
going through thousands of lines for addresses, network names and robot
ids before posting them, which most will not do and some do badly.

WHAT. `roomba_plus.debug_capture_start` turns on debug logging for
Roomba+, roombapy and roombapy-prime and keeps the lines in memory,
`debug_capture_stop` ends it and puts the levels back, and
`debug_capture_get` returns what was kept. Nothing is written to disk
and nothing needs a restart. It stops on its own after the time given.

REDACTED AS IT IS KEPT, by key and by value, with the same rules as the
diagnostics (`diagnostics._is_secret_key`, `_MAC_PATTERN`):

- by key, wherever a `key: value` or `key=value` appears: anything
  secret-shaped, the network block (`netinfo`, `wlcfg`, addresses,
  SSIDs, MACs), and positions in the home (`pose`, `point`, `x_mm` ...);
- by value: every robot id of every Roomba+ entry, IPv4 and IPv6
  addresses, MAC addresses, e-mail addresses, and long token-shaped
  strings.

A line that still holds something private is a bug in this module, not
the tester's job to find.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .redaction import _MAC_PATTERN, _is_secret_key

#: The loggers a capture listens to.
LOGGERS: tuple[str, ...] = (
    "custom_components.roomba_plus",
    "roombapy",
    "roombapy_prime",
)

#: How much is kept. A busy robot logs a few lines a second at debug; an
#: hour of that fits, and the response stays small enough for an issue.
MAX_LINES = 5000
MAX_LINE_CHARS = 2000
DEFAULT_MINUTES = 30
MAX_MINUTES = 240

REDACTED = "**REDACTED**"

#: Keys whose value is private whatever it looks like (lower case).
_PRIVATE_KEYS: frozenset[str] = frozenset({
    # network
    "netinfo", "wlcfg", "addr", "gw", "dns1", "dns2", "mask", "ip", "ipv4",
    "ipv6", "host", "hostname", "ssid", "bssid", "mac", "hwaddr", "wlan0hwaddr",
    "router", "gateway",
    # people and places
    "email", "username", "user", "latitude", "longitude", "lat", "lon",
    "address", "tz_location",
    # positions in the home
    "pose", "point", "x_mm", "y_mm", "map_x_mm", "map_y_mm",
    "position", "positions", "trajectory",
    # the robot itself
    "sn", "serial", "serialnumber", "serial_number",
})

_KEY = re.compile(r"""(?P<q>["']?)(?P<key>[A-Za-z_][\w-]{0,40})(?P=q)\s*(?P<sep>[:=])\s*""")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
#: Token-shaped: 40+ characters of base64 or hex with no spaces.
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}")
#: A Classic robot id: 16 upper-case hex characters.
_CLASSIC_BLID = re.compile(r"\b[0-9A-F]{16}\b")


def _private_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _PRIVATE_KEYS or _is_secret_key(key)


def _value_end(text: str, start: int) -> int:
    """Where the value starting at `start` ends: a balanced object or list,
    a quoted string, or a bare token up to the next separator."""
    if start >= len(text):
        return start
    opener = text[start]
    if opener in "{[":
        closer = "}" if opener == "{" else "]"
        depth = 0
        quote = ""
        for i in range(start, len(text)):
            ch = text[i]
            if quote:
                if ch == quote and text[i - 1] != "\\":
                    quote = ""
                continue
            if ch in "\"'":
                quote = ch
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return i + 1
        return len(text)
    if opener in "\"'":
        end = text.find(opener, start + 1)
        return len(text) if end < 0 else end + 1
    match = re.compile(r"[,}\]\s)]").search(text, start)
    return match.start() if match else len(text)


def _redact_keys(text: str) -> str:
    out: list[str] = []
    pos = 0
    while True:
        match = _KEY.search(text, pos)
        if match is None:
            out.append(text[pos:])
            return "".join(out)
        if not _private_key(match.group("key")):
            out.append(text[pos:match.end()])
            pos = match.end()
            continue
        end = _value_end(text, match.end())
        out.append(text[pos:match.end()])
        out.append(REDACTED)
        pos = end


def redact_text(text: str, robot_ids: Iterable[str] = ()) -> str:
    """One log line with everything private replaced."""
    for robot_id in sorted({r for r in robot_ids if r}, key=len, reverse=True):
        text = text.replace(robot_id, "**REDACTED_BLID**")
    text = _redact_keys(text)
    text = _MAC_PATTERN.sub("**REDACTED_MAC**", text)
    text = _EMAIL.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _AWS_KEY.sub(REDACTED, text)
    text = _IPV6.sub("**REDACTED_IP**", text)
    text = _IPV4.sub("**REDACTED_IP**", text)
    text = _LONG_TOKEN.sub(REDACTED, text)
    text = _CLASSIC_BLID.sub("**REDACTED_BLID**", text)
    return text


class _Handler(logging.Handler):
    def __init__(self, capture: DebugCapture) -> None:
        super().__init__(logging.DEBUG)
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._capture.add(record)
        except Exception:  # noqa: BLE001 - a log handler must never raise
            self.handleError(record)


class DebugCapture:
    """One capture for the whole integration (the loggers are global)."""

    def __init__(self, robot_ids: Callable[[], Iterable[str]]) -> None:
        self._robot_ids = robot_ids
        self.lines: deque[str] = deque(maxlen=MAX_LINES)
        self.dropped = 0
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self._handler: _Handler | None = None
        self._levels: dict[str, int] = {}
        self._timer: CALLBACK_TYPE | None = None
        self._ids: tuple[str, ...] = ()

    @property
    def running(self) -> bool:
        return self._handler is not None

    def add(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            message = f"{message}\n{record.exc_text}"
        line = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.created))}"
            f".{int(record.msecs):03d} {record.levelname} [{record.name}] {message}"
        )
        line = redact_text(line[:MAX_LINE_CHARS * 2], self._ids)[:MAX_LINE_CHARS]
        if len(self.lines) == self.lines.maxlen:
            self.dropped += 1
        self.lines.append(line)

    @callback
    def start(self, hass: HomeAssistant, minutes: int) -> None:
        """Start, or restart with a new time limit; earlier lines are cleared."""
        self.stop()
        self.lines.clear()
        self.dropped = 0
        self._ids = tuple(self._robot_ids())
        self._handler = _Handler(self)
        for name in LOGGERS:
            logger = logging.getLogger(name)
            self._levels[name] = logger.level
            logger.addHandler(self._handler)
            if logger.getEffectiveLevel() > logging.DEBUG:
                logger.setLevel(logging.DEBUG)
        self.started_at = time.time()
        self.stopped_at = None

        @callback
        def _expire(_now: Any) -> None:
            self._timer = None
            self.stop()

        self._timer = async_call_later(hass, minutes * 60, _expire)

    @callback
    def stop(self) -> None:
        """Stop and put the log levels back. The lines stay until the next start."""
        if self._timer is not None:
            self._timer()
            self._timer = None
        if self._handler is None:
            return
        for name in LOGGERS:
            logger = logging.getLogger(name)
            logger.removeHandler(self._handler)
            if name in self._levels:
                logger.setLevel(self._levels.pop(name))
        self._handler = None
        self.stopped_at = time.time()

    def report(self) -> dict[str, Any]:
        """What was kept, for the service response."""
        def _iso(ts: float | None) -> str | None:
            if ts is None:
                return None
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))

        return {
            "running": self.running,
            "started_at": _iso(self.started_at),
            "stopped_at": _iso(self.stopped_at),
            "line_count": len(self.lines),
            "dropped_lines": self.dropped,
            "lines": list(self.lines),
        }


_DATA_KEY = f"{DOMAIN}_debug_capture"


def get_capture(hass: HomeAssistant) -> DebugCapture:
    """The integration's one capture, made on first use."""
    capture = hass.data.get(_DATA_KEY)
    if capture is None:

        def _robot_ids() -> list[str]:
            ids: list[str] = []
            for entry in hass.config_entries.async_entries(DOMAIN):
                blid = entry.data.get("blid")
                if isinstance(blid, str):
                    ids.append(blid)
            return ids

        capture = DebugCapture(_robot_ids)
        hass.data[_DATA_KEY] = capture
    return capture
