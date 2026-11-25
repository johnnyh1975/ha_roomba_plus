"""Close the local connect attempts roombapy 2.0.2 leaves open (#265).

roombapy opens its session with ``async with aiomqtt.Client(...)``, and
aiomqtt does not close paho's socket when that enter fails. On its own
CONNACK timeout it raises with the socket still open; when the caller
cancels -- the setup and the config flow both bound the connect -- the
connect keeps running in an executor thread and opens the socket after
the caller has gone. Nothing reads from that socket again, yet paho keeps
it alive for at least a keepalive period, and for good if a late CONNACK
arrives.

A Classic robot takes one local connection at a time, so each abandoned
attempt held the slot the next attempt needed. One slow answer, or the
built-in integration holding the robot for a moment, turned into a robot
that refused Roomba+ until Home Assistant restarted (#230, #265).

The fix belongs in roombapy and is proposed there (pschmitt/roombapy
#631). Until a release with it is the one installed, `ensure_installed`
gives roombapy's module a view of aiomqtt whose ``Client`` closes such
attempts. It does nothing once roombapy carries the fix itself, which it
recognises by `_open_session`; remove this module when the oldest
supported Home Assistant pins a roombapy that does.

THE VIEW IS PROCESS-WIDE. Python loads roombapy once, so the built-in
Roomba integration, when loaded, uses the same view. It changes only what
happens after a failed or abandoned enter, closing a socket nothing would
ever read.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import types
from typing import Any, Self

import aiomqtt

_LOGGER = logging.getLogger(__name__)

# Attempts a caller stopped waiting for, until each is closed. The loop
# holds tasks weakly; without this one could be collected mid-connect.
_ABANDONED: set[asyncio.Future[Any]] = set()


class ClosingClient(aiomqtt.Client):
    """An aiomqtt client whose failed or abandoned enter closes its socket."""

    async def __aenter__(self) -> Self:
        """Connect, closing the socket when the connect does not complete.

        A cancelled caller returns at once. The attempt it leaves runs on,
        bounded by aiomqtt's own timeout, and is closed when it finishes.
        `asyncio.wait`, not `asyncio.shield`: the second logs the attempt's
        own timeout as an error once the caller is gone.
        """
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

    Client = ClosingClient

    def __getattr__(self, name: str) -> Any:
        """Hand every other name through, so ``MqttError`` stays the class."""
        return getattr(aiomqtt, name)


def ensure_installed() -> bool:
    """Give roombapy the closing client, unless it no longer needs it.

    Returns whether the view is in place. Idempotent; called before every
    local connect rather than at import, so loading the integration has no
    effect on a library another integration may already be using.
    """
    try:
        from roombapy import roomba as roomba_module  # noqa: PLC0415
    except ImportError:
        return False
    if hasattr(roomba_module, "_open_session"):
        # roombapy closes these attempts itself.
        return False
    current = getattr(roomba_module, "aiomqtt", None)
    if isinstance(current, _AiomqttView):
        return True
    if current is not aiomqtt:
        # Not the layout this was written for. Leave it alone rather than
        # replace something unknown.
        _LOGGER.debug(
            "roomba_plus: roombapy does not use aiomqtt as expected; "
            "abandoned connect attempts are not closed"
        )
        return False
    roomba_module.aiomqtt = _AiomqttView("aiomqtt")  # type: ignore[attr-defined]
    return True
