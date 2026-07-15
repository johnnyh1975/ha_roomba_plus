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

from .const import (
    DOMAIN,
    EVENT_CANCELLATION_RECURRENCE,
    EVENT_CLOUD_STALE,
    EVENT_ERROR_RECURRENCE,
    EVENT_MAINTENANCE_RESET,
    EVENT_MAP_DRIFT_DETECTED,
    EVENT_MAP_RETRAIN_IN_PROGRESS,
    EVENT_MISSION_ANOMALY,
    EVENT_MISSION_COMPLETED,
    EVENT_MIXED_SCHEDULE,
    EVENT_SCHEDULE_SUBOPTIMAL,
    EVENT_STUCK,
    EVENT_STUCK_PATTERN,
)

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

    @callback
    def describe_stuck(event: Event) -> dict[str, str]:
        """v3.2.0 STUCK-CONTEXT — same underlying data as the
        mqtt_watchdog Repair Issue, phrased as a logbook sentence so the
        push notification (built by the user from EVENT_STUCK directly)
        and the searchable Logbook history share the same wording."""
        data = event.data
        message = "got stuck"
        room = data.get("last_room")
        if room:
            message += f" — {room}"
        minutes = data.get("minutes_stuck")
        if minutes:
            message += f", {minutes} min"

        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    # v3.5.0 Repairs redesign — describers for signals demoted from
    # persistent Repair Issues to fire-once events. Each mirrors the wording
    # its former translation_key used, so anyone who saw the Repair before
    # sees the same substance in the Logbook now.

    @callback
    def describe_error_recurrence(event: Event) -> dict[str, str]:
        data = event.data
        message = (
            f"recurring error: {data.get('label')} "
            f"({data.get('count')} times in 30 days)"
        )
        room = data.get("room")
        if room and room != "unknown location":
            message += f" — {room}"
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    @callback
    def describe_cancellation_recurrence(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"cancelled {data.get('count')} times in 30 days"
            ),
        }

    @callback
    def describe_stuck_pattern(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"repeatedly gets stuck {data.get('time')} "
                f"— {data.get('room')}"
            ),
        }

    @callback
    def describe_mission_anomaly(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"{data.get('count')} consecutive unusual missions"
            ),
        }

    @callback
    def describe_mixed_schedule(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"mixed schedule detected ({data.get('schedule_pct')}% "
                f"scheduled, {data.get('app_pct')}% app-started)"
            ),
        }

    @callback
    def describe_schedule_suboptimal(event: Event) -> dict[str, str]:
        data = event.data
        days = data.get("days") or []
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"high-dirt days without a scheduled clean: {', '.join(days)}"
            ),
        }

    @callback
    def describe_map_drift_detected(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"map drift detected — {data.get('magnitude_cm')} cm "
                f"at {data.get('bearing')}°"
            ),
        }

    @callback
    def describe_map_retrain_in_progress(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"Smart Map still updating after {data.get('minutes')} min"
            ),
        }

    @callback
    def describe_cloud_stale(event: Event) -> dict[str, str]:
        data = event.data
        return {
            LOGBOOK_ENTRY_NAME: data.get("name") or "Roomba+",
            LOGBOOK_ENTRY_MESSAGE: (
                f"cloud data hasn't refreshed in {data.get('minutes')} min"
            ),
        }

    async_describe_event(
        DOMAIN, EVENT_MISSION_COMPLETED, describe_mission_completed
    )
    async_describe_event(
        DOMAIN, EVENT_MAINTENANCE_RESET, describe_maintenance_reset
    )
    async_describe_event(
        DOMAIN, EVENT_STUCK, describe_stuck
    )
    async_describe_event(
        DOMAIN, EVENT_ERROR_RECURRENCE, describe_error_recurrence
    )
    async_describe_event(
        DOMAIN, EVENT_CANCELLATION_RECURRENCE, describe_cancellation_recurrence
    )
    async_describe_event(
        DOMAIN, EVENT_STUCK_PATTERN, describe_stuck_pattern
    )
    async_describe_event(
        DOMAIN, EVENT_MISSION_ANOMALY, describe_mission_anomaly
    )
    async_describe_event(
        DOMAIN, EVENT_MIXED_SCHEDULE, describe_mixed_schedule
    )
    async_describe_event(
        DOMAIN, EVENT_SCHEDULE_SUBOPTIMAL, describe_schedule_suboptimal
    )
    async_describe_event(
        DOMAIN, EVENT_MAP_DRIFT_DETECTED, describe_map_drift_detected
    )
    async_describe_event(
        DOMAIN, EVENT_MAP_RETRAIN_IN_PROGRESS, describe_map_retrain_in_progress
    )
    async_describe_event(
        DOMAIN, EVENT_CLOUD_STALE, describe_cloud_stale
    )
