"""The robot's reported state, read from a roombapy client (4.6.13).

A LEAF MODULE. This one helper used to live in `__init__.py`, and sixteen
modules imported it from the package -- which made the package the hub
of an import cycle through 33 modules (independent review, 28.11.).
Here it imports nothing from the integration, so every module can use
it without depending on the package. `__init__` still re-exports it for
anything that reads it from there.
"""

from __future__ import annotations

from typing import Any


def roomba_reported_state(roomba: Any) -> dict[str, Any]:
    """Return the 'reported' sub-dict from master_state.

    Uses ``or {}`` rather than a dict default so that an explicit JSON null
    (``{"state": null}``) — which a sparse or initial MQTT frame can produce —
    is coerced to an empty dict instead of raising AttributeError. A dict
    default (``.get("state", {})``) only guards against a *missing* key, not a
    present-but-null value.

    NEW (V4/Prime): roomba is None for CLOUD_ONLY entries -- returns {}
    rather than crashing, same reasoning as RoombaData.roomba_reported_state()
    (models.py): honest "no data available", not a fabricated guess. Every
    one of this function's 59+ call sites across the integration goes
    through here, so this single guard is what keeps all of them safe for
    a Prime entity without touching each call site individually.
    """
    if roomba is None:
        return {}
    return (roomba.master_state.get("state") or {}).get("reported") or {}
