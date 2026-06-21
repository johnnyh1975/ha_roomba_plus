"""Describe Roomba+ logbook events.

v2.9.0 LOGBOOK — turns the existing EVENT-BUS events into rich, searchable
Logbook entries:

  roomba_plus_mission_completed  — fired by callbacks.py's
                                    async_record_mission() at every mission end
  roomba_plus_maintenance_reset  — fired by services.py's shared
                                    _fire_maintenance_reset_event(), called
                                    from both the roomba_plus.reset_* services
                                    AND the Filter/Brush/Battery reset buttons
                                    (button.py) — one event regardless of
                                    which path the user took.

Deliberately reuses the EVENT-BUS (v2.8.6) events rather than firing new
ones — describing an existing event for the Logbook is exactly what this
platform is for; inventing a parallel event here would be the same kind of
redundancy already avoided for MAP-RETRAIN-WF (Triggers vs. Repairs).
"""
from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_MAINTENANCE_RESET, EVENT_MISSION_COMPLETED

# Human-readable component names for the maintenance_reset message.
_COMPONENT_LABELS: dict[str, str] = {
    "filter": "filter",
    "brush": "brush",
    "battery": "battery",
    "pad": "mop pad",
    "wheel": "wheel cleaning",
    "contact": "charging contact cleaning",
    "bin": "bin cleaning",
}

# Mission result -> Logbook message fragment.
_RESULT_MESSAGES: dict[str, str] = {
    "completed": "finished cleaning",
    "stuck": "got stuck while cleaning",
    "stuck_and_resumed": "got stuck but resumed and finished cleaning",
    "stuck_and_abandoned": "got stuck and abandoned the mission",
    "error": "ended with an error",
    "cancelled": "cancelled the mission",
    "blocked_timeout": "could not start — blocked for too long",
}


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe Roomba+ logbook events."""

    @callback
    def describe_mission_completed(event: Event) -> dict[str, str]:
        data = event.data
        result = data.get("result", "completed")
        fragment = _RESULT_MESSAGES.get(result, f"ended (result: {result})")

        details: list[str] = []
        rooms_cleaned = data.get("rooms_cleaned")
        if rooms_cleaned:
            details.append(
                f"{rooms_cleaned} room{'s' if rooms_cleaned != 1 else ''}"
            )
        area_sqft = data.get("area_sqft")
        if area_sqft:
            details.append(f"{area_sqft} sqft")
        stuck_count = data.get("stuck_count")
        if stuck_count:
            details.append(
                f"{stuck_count} stuck event{'s' if stuck_count != 1 else ''}"
            )

        message = fragment
        if details:
            message += f" ({', '.join(details)})"

        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    @callback
    def describe_maintenance_reset(event: Event) -> dict[str, str]:
        data = event.data
        component = data.get("component", "")
        label = _COMPONENT_LABELS.get(component, component or "maintenance")
        hours = data.get("hours")
        message = (
            f"{label} reset at {hours}h"
            if hours is not None
            else f"{label} reset"
        )

        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(
        DOMAIN, EVENT_MISSION_COMPLETED, describe_mission_completed
    )
    async_describe_event(
        DOMAIN, EVENT_MAINTENANCE_RESET, describe_maintenance_reset
    )
