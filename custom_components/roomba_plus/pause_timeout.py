"""Send a robot home that was left paused (4.6.0, B15).

A paused robot stays where it stopped, its battery running down, until
someone resumes or docks it -- the app's own pause has no end. With the
option set, Roomba+ sends it home after that many minutes of standing
paused. Off by default (0): a pause in the hallway while someone carries
a chair is not abandoned.

BY THE VACUUM ENTITY'S STATE, so both generations behave the same: the
state `paused` is what the vacuum platform derives from each robot's own
report. The timer restarts whenever the state changes, and at the end
the state is read again -- a robot resumed and paused again a minute ago
gets its full time.

HOME THROUGH `vacuum.return_to_base`, the action a person would call,
so whatever that does on the robot (and the logbook entry it leaves) is
the same.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import CONF_PAUSE_TIMEOUT_MIN, DEFAULT_PAUSE_TIMEOUT_MIN, DOMAIN

_LOGGER = logging.getLogger(__name__)

_PAUSED = "paused"


def _vacuum_entity_id(hass: HomeAssistant, entry: Any) -> str | None:
    registry = er.async_get(hass)
    return next((
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "vacuum"
    ), None)


@callback
def async_setup_pause_timeout(hass: HomeAssistant, entry: Any) -> None:
    """Watch this robot's vacuum for a pause that lasts, when set."""
    try:
        minutes = int(entry.options.get(CONF_PAUSE_TIMEOUT_MIN, DEFAULT_PAUSE_TIMEOUT_MIN) or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return
    pending: list[Any] = []

    def _cancel() -> None:
        while pending:
            pending.pop()()

    async def _send_home(entity_id: str) -> None:
        state = hass.states.get(entity_id)
        if state is None or state.state != _PAUSED:
            return
        _LOGGER.info(
            "%s: paused for %d minutes, sending it home (option 'Send home after a pause')",
            entry.title, minutes,
        )
        try:
            await hass.services.async_call(
                "vacuum", "return_to_base", {"entity_id": entity_id}, blocking=True,
            )
        except Exception:  # noqa: BLE001 -- an unreachable robot stays paused
            _LOGGER.warning("%s: could not send the paused robot home", entry.title, exc_info=True)

    @callback
    def _on_state(event: Event[Any]) -> None:
        entity_id = (event.data or {}).get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.startswith("vacuum."):
            return
        if entity_id != _vacuum_entity_id(hass, entry):
            return
        new = (event.data or {}).get("new_state")
        _cancel()
        if new is None or new.state != _PAUSED:
            return

        @callback
        def _fire(_now: Any) -> None:
            pending.clear()
            entry.async_create_background_task(
                hass, _send_home(entity_id), name=f"{DOMAIN}_pause_timeout",
            )

        pending.append(async_call_later(hass, minutes * 60, _fire))

    entry.async_on_unload(hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state))
    entry.async_on_unload(_cancel)
