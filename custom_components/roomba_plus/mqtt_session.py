"""Two fixes roombapy 2.0.2 needs, until a release carries them.

1. CLOSE THE CONNECT ATTEMPTS IT LEAVES OPEN (#230, #265).
   roombapy opens its session with ``async with aiomqtt.Client(...)``,
   and aiomqtt does not close paho's socket when that enter fails. On its
   own CONNACK timeout it raises with the socket still open; when the
   caller cancels -- the setup and the config flow both bound the
   connect -- the connect keeps running in an executor thread and opens
   the socket after the caller has gone. A Classic robot takes one local
   connection at a time, so each abandoned attempt held the slot the
   next attempt needed. Proposed upstream as pschmitt/roombapy#631;
   roombapy with it has `_open_session`.

2. DO NOT DROP A SESSION OVER A MISSING SUBACK (#265, roombapy #624).
   Firmware 3.5.x (Roomba 671, 675, 694) never answers SUBSCRIBE, and a
   Combo Essential appears to behave the same. These robots publish
   their state to a connected client regardless. roombapy 1.x subscribed
   without waiting; 2.x waits for the SUBACK, gives up after aiomqtt's
   10 s with "Operation timed out" and drops a working session, on every
   reconnect, so the robot never connects. Proposed upstream as the fix
   for pschmitt/roombapy#624; roombapy with it has
   `SUBSCRIBE_ACK_TIMEOUT`.

`ensure_installed` gives roombapy's module a view of aiomqtt whose
``Client`` carries whichever of the two the installed roombapy lacks, and
does nothing once it has both. Remove this module when the oldest
supported Home Assistant pins a roombapy that does.

THE VIEW IS PROCESS-WIDE. Python loads roombapy once, so the built-in
Roomba integration, when loaded, uses the same view. It changes only a
failed or abandoned enter, closing a socket nothing would ever read, and
how long a subscription waits for an answer some robots never give.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import types
from typing import Any, Self

import aiomqtt

_LOGGER = logging.getLogger(__name__)

# Attempts a caller stopped waiting for, until each is closed. The loop
# holds tasks weakly; without this one could be collected mid-connect.
_ABANDONED: set[asyncio.Future[Any]] = set()


#: How long a subscription waits for the robot's SUBACK before carrying
#: on without it. One that answers does so in milliseconds.
SUBSCRIBE_ACK_TIMEOUT = 3.0


class ClosingClient(aiomqtt.Client):
    """An aiomqtt client with the fixes roombapy 2.0.2 lacks.

    Each is switched by a class attribute, so a roombapy that carries one
    of them gets only the other (`ensure_installed`).
    """

    #: Close the socket of an enter that fails or is abandoned.
    close_abandoned = True
    #: Carry on when the robot does not acknowledge a subscription.
    tolerate_missing_suback = True

    async def __aenter__(self) -> Self:
        """Connect, closing the socket when the connect does not complete.

        A cancelled caller returns at once. The attempt it leaves runs on,
        bounded by aiomqtt's own timeout, and is closed when it finishes.
        `asyncio.wait`, not `asyncio.shield`: the second logs the attempt's
        own timeout as an error once the caller is gone.
        """
        if not self.close_abandoned:
            return await super().__aenter__()
        entering = asyncio.ensure_future(super().__aenter__())
        try:
            await asyncio.wait({entering})
        except asyncio.CancelledError:
            _ABANDONED.add(entering)
            entering.add_done_callback(
                lambda done: _close_abandoned(self, done)
            )
            raise
        try:
            return entering.result()
        except BaseException:
            _close_socket(self)
            raise

    async def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        """Subscribe, waiting at most `SUBSCRIBE_ACK_TIMEOUT` for the SUBACK.

        Without an answer by then the subscription stands as sent and the
        session carries on; the robots that never answer publish their
        state anyway. The bound is asyncio's, so the missing SUBACK is told
        apart without reading aiomqtt's error message, and anything else
        the subscription raises still ends the session.
        """
        if not self.tolerate_missing_suback:
            return await super().subscribe(*args, **kwargs)
        try:
            async with asyncio.timeout(SUBSCRIBE_ACK_TIMEOUT):
                return await super().subscribe(
                    *args, **{**kwargs, "timeout": math.inf}
                )
        except TimeoutError:
            _LOGGER.debug(
                "The robot did not acknowledge the subscription within %.0fs; "
                "carrying on without it",
                SUBSCRIBE_ACK_TIMEOUT,
            )
            return None


def _close_abandoned(
    client: aiomqtt.Client, entering: asyncio.Future[Any]
) -> None:
    """Close an attempt its caller stopped waiting for.

    Its outcome is retrieved first: nobody else will, and asyncio would
    otherwise log a timeout nobody asked about as never retrieved.
    """
    _ABANDONED.discard(entering)
    if not entering.cancelled():
        entering.exception()
    _close_socket(client)


def _close_socket(client: aiomqtt.Client) -> None:
    """Close the socket of a client whose session will never be used.

    Reaching into paho is the only way: aiomqtt offers no close for a
    client that never entered, and its ``__aexit__`` waits for a
    DISCONNECT acknowledgement a robot that never answered CONNACK will not
    send. ``_sock_close`` also runs aiomqtt's socket-close hook, so the
    reader and the keepalive task go with the socket.
    """
    with contextlib.suppress(Exception):
        client._client._sock_close()  # noqa: SLF001


class _AiomqttView(types.ModuleType):
    """aiomqtt as roombapy sees it: everything the same but ``Client``."""

    def __init__(self, client: type[aiomqtt.Client]) -> None:
        super().__init__("aiomqtt")
        self.Client = client

    def __getattr__(self, name: str) -> Any:
        """Hand every other name through, so ``MqttError`` stays the class."""
        return getattr(aiomqtt, name)


def ensure_installed() -> bool:
    """Give roombapy the fixes it lacks, unless it lacks none.

    Returns whether the view is in place. Idempotent; called before every
    local connect rather than at import, so loading the integration has no
    effect on a library another integration may already be using.
    """
    try:
        from roombapy import roomba as roomba_module  # noqa: PLC0415
    except ImportError:
        return False
    close_abandoned = not hasattr(roomba_module, "_open_session")
    tolerate_missing_suback = not hasattr(roomba_module, "SUBSCRIBE_ACK_TIMEOUT")
    if not (close_abandoned or tolerate_missing_suback):
        # roombapy does both itself.
        return False
    current = getattr(roomba_module, "aiomqtt", None)
    if isinstance(current, _AiomqttView):
        return True
    if current is not aiomqtt:
        # Not the layout this was written for. Leave it alone rather than
        # replace something unknown.
        _LOGGER.debug(
            "roomba_plus: roombapy does not use aiomqtt as expected; "
            "its connect fixes are not applied"
        )
        return False
    client = type(
        "ClosingClient",
        (ClosingClient,),
        {
            "close_abandoned": close_abandoned,
            "tolerate_missing_suback": tolerate_missing_suback,
        },
    )
    roomba_module.aiomqtt = _AiomqttView(client)  # type: ignore[attr-defined]
    return True
