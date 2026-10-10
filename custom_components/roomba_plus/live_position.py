"""Live position for Classic robots that do not publish one (4.3).

A 900-series puts its position into its own shadow as it drives, and the
cleaning map has drawn from that since v1. Newer Classic robots -- an i7
or S9+ on lewis, an i3 on daredevil -- keep it to themselves: the shadow
never carries `pose`, and the map fell back to the cloud's record of the
last finished mission. lewis answers when asked (rrtp: publish to `req`,
the reply arrives on `data`), which roombapy 2.0 implements as
`RoombaClient.watch_position()`.

daredevil does not, on the one i3 measured (2.6.0, @AlakazipLabs): no
reply to 2,875 requests at 1 Hz over a 51-minute driving spell. An
earlier version of this note counted it among the robots that answer;
nothing on record supports that. It gets the same treatment as a
j-series, which has not been tried: three unanswered requests and this
module stops asking until the next mission.

This module asks, and only while it is worth asking: during a mission,
while the robot is moving, and only when the shadow carries no position
of its own. It hands each position to whoever listens -- the cleaning
map and the device tracker -- and keeps nothing.

WHAT IT DELIBERATELY DOES NOT DO. The positions feed the live picture
only, not the stores that learn from positions (coverage grid, door
markers, trajectories). Those stores were built on the 900-series frame,
and for these robots they are filled from the cloud's coverage record
in the aligner's frame instead. roombapy documents the requested frame
as the shadow's (metres from the dock, x along the docked heading); that
has not been compared against a cloud map on any robot, and a wrong
assumption written into a persistent store does not wash out. Drawing
it costs nothing to get wrong; storing it would.

WHAT IT COSTS. One request per second while the robot moves, over the
local connection the integration already holds. Verified at 2 Hz (100
of 100 answered) on an i7+, for fifty seconds -- never across a whole
mission. That is why giving up is built in: on a robot that never
answers (`RrtpUnsupportedError`), and on one that answers without ever
having a fix (a Braava jet m6 did that for a whole run).

A ROBOT THAT HAS ANSWERED IS NOT ONE THAT CANNOT (4.3.4, @pk-1966). An
i7+ answered for eight minutes of a mission, then left three requests
unanswered for a minute and answered all three at once, 61 to 69 s
late. roombapy reads three silent requests as "does not implement
rrtp" and raises RrtpUnsupportedError, and this module gave up for the
rest of the mission. It now tells the two apart: from a robot that has
answered before, the same error is a stall, and the stream is opened
again after a pause (30 s, then 60, then every 120 s; at most
`MAX_STALLS_PER_MISSION` times). Only a robot that has never answered
is given up on.

roombapy also keeps its verdict for the rest of the CONNECTION, and
refuses every later stream with the same error without asking -- the
next mission's included, although this module meant to ask again then.
The verdict is cleared before each stream this module opens: when to
ask is decided here, per mission.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import reports_local_pose

# GUARDED, as in __init__.py: this module is imported at the top of it,
# and a missing roombapy must reach the repair that names the conflict
# rather than fail the import here.
try:
    from roombapy import RrtpUnsupportedError
except ImportError:  # pragma: no cover - environment

    class RrtpUnsupportedError(Exception):  # type: ignore[no-redef]
        """Never raised: without roombapy there is no stream."""

if TYPE_CHECKING:
    from roombapy import RobotPosition

_LOGGER = logging.getLogger(__name__)

#: Phases in which the robot moves and a position is worth asking for.
#: Cleaning, driving home mid-mission (to recharge or empty) and at the
#: end, sent home by the user, and stuck -- where "where exactly" is the
#: question being asked. Not `charge` or `evac`: the robot is in its dock.
MOVING_PHASES: Final[frozenset[str]] = frozenset(
    {"run", "hmMidMsn", "hmPostMsn", "hmUsrDock", "stuck"}
)

#: How long a stream may run without a single position before it stops
#: for the rest of the mission. A robot that answers "no fix" is not
#: silent, so the library's own give-up never fires for it; a Braava jet
#: m6 answered like that for a whole run. Two minutes is long enough for
#: a robot leaving its dock to localise.
NO_FIX_GIVE_UP_S: Final = 120.0

#: How often a stream may end on an error and start again in one
#: mission. A dropped connection ends it and the next shadow message
#: starts it again, which is right once or twice and a loop beyond that.
MAX_RESTARTS_PER_MISSION: Final = 5

#: Pauses before asking again after a robot that has answered falls
#: silent. The last one repeats. The stall that prompted this lasted a
#: little over a minute; asking every second through it would only queue
#: requests the robot answers late, all at once.
STALL_RETRY_DELAYS_S: Final[tuple[float, ...]] = (30.0, 60.0, 120.0)

#: How many stalls one mission may have before this module stops asking.
#: At the 120 s pause a robot that stays silent costs three requests
#: every two minutes until then.
MAX_STALLS_PER_MISSION: Final = 10

#: `status` values, for diagnostics.
STATUS_OFF: Final = "off"
STATUS_IDLE: Final = "idle"
STATUS_STREAMING: Final = "streaming"
STATUS_POSE_IN_SHADOW: Final = "pose_in_shadow"
STATUS_UNSUPPORTED: Final = "unsupported"
#: A robot that has answered before stopped answering; asking again
#: after a pause.
STATUS_STALLED: Final = "stalled"
STATUS_NO_FIX: Final = "no_fix"
STATUS_ERROR: Final = "error"


class LivePositionStream:
    """Ask a Classic robot where it is while it cleans.

    One per config entry. `start()` subscribes to the robot's messages
    and decides on each one whether a stream should be running;
    `stop()` ends everything (registered as an unload hook).

    `enabled` is asked on every decision rather than read once, so the
    option takes effect on the robot's next message without a reload --
    the same as the other map options, which are read per render.
    """

    def __init__(
        self, hass: HomeAssistant, roomba: Any, *, enabled: Callable[[], bool]
    ) -> None:
        self._hass = hass
        self._roomba = roomba
        self._enabled = enabled
        self._task: asyncio.Task[None] | None = None
        self._unsub_message: Callable[[], None] | None = None
        self._listeners: list[Callable[[RobotPosition], None]] = []
        #: Set when this mission is not worth asking about any more --
        #: cleared when the robot reports no mission.
        self._given_up = False
        self._restarts = 0
        #: Whether the robot has ever answered a request. Once it has, it
        #: implements them, and silence is a stall rather than a verdict.
        self._has_answered = False
        #: Stalls in this mission, and the pending "ask again" timer.
        self._stalls = 0
        self._retry_cancel: Callable[[], None] | None = None
        self.status: str = STATUS_IDLE
        #: The newest requested position. Kept after the mission so the
        #: tracker can say where the robot stopped; never a shadow pose.
        self.latest: RobotPosition | None = None
        self.positions_received = 0
        self.streams_started = 0
        #: Exception class name only: a message may carry the address.
        self.last_error: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to the robot's messages and evaluate the state now."""
        unsub = self._roomba.register_on_message_callback(self._on_message)
        # roombapy 2.0 returns the unsubscribe handle; a test double may not.
        self._unsub_message = unsub if callable(unsub) else None
        self._evaluate()

    @callback
    def stop(self) -> None:
        """Unsubscribe and cancel the stream. Safe to call more than once.

        Synchronous, as an unload hook should be: the cancelled stream
        closes its position watcher on its way out, which stops
        roombapy's poller once nobody listens.
        """
        if self._unsub_message is not None:
            self._unsub_message()
            self._unsub_message = None
        self._cancel_retry()
        self._cancel_soon()

    @callback
    def add_listener(
        self, listener: Callable[[RobotPosition], None]
    ) -> Callable[[], None]:
        """Call `listener` with each position. Returns the remover."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    # ── Decision ─────────────────────────────────────────────────────────

    @callback
    def _on_message(self, _json_data: dict[str, Any]) -> None:
        self._evaluate()

    def _state(self) -> dict[str, Any]:
        state = getattr(self._roomba, "master_state", None) or {}
        reported = (state.get("state") or {}).get("reported")
        return reported if isinstance(reported, dict) else {}

    @callback
    def _evaluate(self) -> None:
        """Start or stop the stream to match what the robot reports."""
        if not self._enabled():
            self.status = STATUS_OFF
            # The tracker reads `latest`; switched off means no position
            # from here, not the last one held indefinitely.
            self.latest = None
            self._cancel_retry()
            self._cancel_soon()
            return
        state = self._state()
        mission = state.get("cleanMissionStatus") or {}
        cycle = mission.get("cycle") or "none"
        phase = mission.get("phase") or ""

        if cycle == "none" and phase not in MOVING_PHASES:
            # No mission: the next one starts with a clean slate. A
            # robot that could not answer last time may have been
            # updated, and asking costs three requests.
            self._given_up = False
            self._restarts = 0
            self._stalls = 0
            self._cancel_retry()
            if self.status == STATUS_STALLED:
                self.status = STATUS_IDLE

        if reports_local_pose(state):
            # The robot publishes after all. Its own pose is free and
            # the map already draws it; asking as well would only add
            # traffic.
            self.status = STATUS_POSE_IN_SHADOW
            self._cancel_soon()
            return

        wanted = (
            phase in MOVING_PHASES
            and not self._given_up
            and self._retry_cancel is None
        )
        # A TASK BEING CANCELLED IS NOT RUNNING. lewis sends brief
        # `charge` bursts between rooms; two messages in one loop turn
        # cancel the stream and want it back before the cancellation has
        # landed, and counting the dying task as running left the map
        # without positions until the robot's next message.
        running = (
            self._task is not None
            and not self._task.done()
            and not self._task.cancelling()
        )
        if wanted and not running:
            self._task = self._hass.async_create_background_task(
                self._async_run(), name="roomba_plus_live_position"
            )
        elif not wanted and running:
            self._cancel_soon()
        if not wanted and self.status in (STATUS_STREAMING, STATUS_OFF) and (
            self._retry_cancel is None
        ):
            self.status = STATUS_IDLE

    # ── Stream ───────────────────────────────────────────────────────────

    async def _async_run(self) -> None:
        """Run one stream until it ends, is cancelled, or gives up."""
        self.streams_started += 1
        self.status = STATUS_STREAMING
        received_this_stream = 0
        stream: Any = None
        # Opened inside the try: a failure to open the stream must be
        # counted like any other, not leave the status at "streaming"
        # while every later message starts another failing task.
        try:
            self._forget_library_verdict()
            stream = self._roomba.watch_position()
            while True:
                # A DEADLINE FOR THE FIRST POSITION ONLY. Once one has
                # arrived the robot has a fix, and later gaps are the
                # library's to judge.
                deadline = NO_FIX_GIVE_UP_S if received_this_stream == 0 else None
                try:
                    async with asyncio.timeout(deadline):
                        position = await anext(stream)
                except StopAsyncIteration:
                    # The connection closed for good; a new one brings a
                    # full shadow, whose message starts a new stream.
                    self.status = STATUS_IDLE
                    return
                if position.source != "request":
                    # The shadow started carrying a pose, which the map
                    # reads directly. Shadow poses are not handed on:
                    # roombapy converts them as millimetres, and this
                    # integration measured centimetres (POSE_POINT_CM_TO_MM).
                    self.status = STATUS_POSE_IN_SHADOW
                    return
                received_this_stream += 1
                self.positions_received += 1
                if self._stalls and received_this_stream == 1:
                    _LOGGER.info(
                        "Roomba+ live position: the robot answers again "
                        "(after %d stall(s) this mission)",
                        self._stalls,
                    )
                self._has_answered = True
                # The budget is for streams that fail, not for a stream
                # that worked and then lost the connection: a long
                # mission on flaky Wi-Fi would otherwise use it up.
                self._restarts = 0
                self.latest = position
                for listener in list(self._listeners):
                    try:
                        listener(position)
                    except Exception:  # noqa: BLE001 - one consumer must not end the stream
                        _LOGGER.exception("Roomba+ live position: listener failed")
        except TimeoutError:
            self._give_up(STATUS_NO_FIX)
            _LOGGER.info(
                "Roomba+ live position: no position within %.0f s; not "
                "asking again until the next mission",
                NO_FIX_GIVE_UP_S,
            )
        except RrtpUnsupportedError:
            if self._has_answered:
                self._stalled()
            else:
                self._give_up(STATUS_UNSUPPORTED)
                _LOGGER.info(
                    "Roomba+ live position: the robot does not answer position "
                    "requests; not asking again until the next mission"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dropped connection lands here
            self.last_error = type(exc).__name__
            self._restarts += 1
            if self._restarts >= MAX_RESTARTS_PER_MISSION:
                self._give_up(STATUS_ERROR)
                _LOGGER.warning(
                    "Roomba+ live position: stream failed %d times (%s); "
                    "not asking again until the next mission",
                    self._restarts, self.last_error,
                )
            else:
                # The next message from the robot -- which after a
                # reconnect is the full shadow -- starts it again.
                self.status = STATUS_IDLE
                _LOGGER.debug(
                    "Roomba+ live position: stream ended (%s)", self.last_error
                )
        finally:
            if stream is not None:
                await stream.aclose()

    def _give_up(self, status: str) -> None:
        self._given_up = True
        self.status = status

    def _stalled(self) -> None:
        """A robot that has answered before fell silent: pause, ask again."""
        self._stalls += 1
        if self._stalls > MAX_STALLS_PER_MISSION:
            self._give_up(STATUS_STALLED)
            _LOGGER.warning(
                "Roomba+ live position: the robot stopped answering position "
                "requests %d times this mission; not asking again until the "
                "next mission",
                MAX_STALLS_PER_MISSION,
            )
            return
        delay = STALL_RETRY_DELAYS_S[
            min(self._stalls, len(STALL_RETRY_DELAYS_S)) - 1
        ]
        self.status = STATUS_STALLED
        _LOGGER.info(
            "Roomba+ live position: the robot stopped answering position "
            "requests; asking again in %.0f s",
            delay,
        )
        self._cancel_retry()
        self._retry_cancel = async_call_later(self._hass, delay, self._retry)

    @callback
    def _retry(self, _now: Any) -> None:
        self._retry_cancel = None
        # Idle until a stream starts: the robot may be docked mid-mission
        # to recharge, and "stalled" would then describe nothing.
        if self.status == STATUS_STALLED:
            self.status = STATUS_IDLE
        self._evaluate()

    @callback
    def _cancel_retry(self) -> None:
        if self._retry_cancel is not None:
            self._retry_cancel()
            self._retry_cancel = None

    def _forget_library_verdict(self) -> None:
        """Clear roombapy's "does not answer" verdict before asking.

        roombapy 2.0.2 keeps it for the whole connection (cleared only
        on a reconnect) and then refuses every stream without sending a
        request. This module decides per mission and per stall, so the
        verdict must not outlast the stream that produced it. A private
        attribute of the pinned version; absent, there is nothing to
        clear.
        """
        if getattr(self._roomba, "_position_unsupported", False):
            self._roomba._position_unsupported = False  # noqa: SLF001

    @callback
    def _cancel_soon(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # ── Diagnostics ──────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """State of the stream. No coordinates: diagnostics are shared."""
        return {
            "enabled": self._enabled(),
            "status": self.status,
            "streams_started": self.streams_started,
            "positions_received": self.positions_received,
            "given_up_this_mission": self._given_up,
            "has_answered": self._has_answered,
            "stalls_this_mission": self._stalls,
            "waiting_to_ask_again": self._retry_cancel is not None,
            "last_error": self.last_error,
            "has_map_id": bool(self.latest is not None and self.latest.pmap_id),
        }
