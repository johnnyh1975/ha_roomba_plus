"""Device triggers for Roomba+.

Triggers available in the HA Automation editor under "Device":

  cleaning_started     — robot transitions into a cleaning phase
  cleaning_finished     — robot returns to dock after a mission
  stuck                 — robot reports a stuck condition
  bin_full              — dust bin is full
  docked                — robot is docked and charging
  error                 — robot reports any error code
  room_completed         — AUTO-ADVANCE-ROOM confirms a room finished (v2.9.0)
  maintenance_due        — filter/brush/battery maintenance is due (v2.9.0)
  health_score_drop      — integration_health score crosses to a worse band (v2.9.0)
  map_retrain_started     — Smart Map retrain detected, cloud sync starting (v2.9.0)
  map_retrain_completed   — Smart Map retrain cloud sync finished (v2.9.0)
  firmware_updated        — robot firmware version changed (v2.9.0)

The first six fire by listening for HA state-change events on the relevant
sensor entities. The five v2.9.0 additions split into two groups:
  - maintenance_due / firmware_updated: also state-based (existing binary
    sensors), same pattern as bin_full.
  - room_completed / map_retrain_started / map_retrain_completed /
    health_score_drop: EVENT-BUS (v2.8.6) events, not entity state. The
    first three use HA's built-in event trigger platform with an
    event_data={"entry_id": ...} filter (exact match is enough). health_
    score_drop needs an extra "did it get WORSE, not just change" check
    that exact-match event_data can't express, so it attaches its own
    hass.bus.async_listen() instead of delegating to the event trigger
    platform.

This keeps the implementation simple, correct, and independent of
roombapy internals.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_HEALTH_CHANGE,
    EVENT_MAP_RETRAIN_COMPLETED,
    EVENT_MAP_RETRAIN_STARTED,
    EVENT_ROOM_COMPLETED,
    HEALTH_BAND_RANK,
)

# ── Trigger type constants ────────────────────────────────────────────────────

TRIGGER_CLEANING_STARTED  = "cleaning_started"
TRIGGER_CLEANING_FINISHED = "cleaning_finished"
TRIGGER_STUCK             = "stuck"
TRIGGER_BIN_FULL          = "bin_full"
TRIGGER_DOCKED            = "docked"
TRIGGER_ERROR             = "error"
# v2.9.0 TRIGGER+
TRIGGER_ROOM_COMPLETED        = "room_completed"
TRIGGER_MAINTENANCE_DUE       = "maintenance_due"
TRIGGER_HEALTH_SCORE_DROP     = "health_score_drop"
TRIGGER_MAP_RETRAIN_STARTED   = "map_retrain_started"
TRIGGER_MAP_RETRAIN_COMPLETED = "map_retrain_completed"
TRIGGER_FIRMWARE_UPDATED      = "firmware_updated"

TRIGGER_TYPES = {
    TRIGGER_CLEANING_STARTED,
    TRIGGER_CLEANING_FINISHED,
    TRIGGER_STUCK,
    TRIGGER_BIN_FULL,
    TRIGGER_DOCKED,
    TRIGGER_ERROR,
    TRIGGER_ROOM_COMPLETED,
    TRIGGER_MAINTENANCE_DUE,
    TRIGGER_HEALTH_SCORE_DROP,
    TRIGGER_MAP_RETRAIN_STARTED,
    TRIGGER_MAP_RETRAIN_COMPLETED,
    TRIGGER_FIRMWARE_UPDATED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)

# ── Phase values from the phase sensor ───────────────────────────────────────
# These are the human-readable labels produced by sensor.py (PHASE_LABELS).
# Active cleaning phases — robot is cleaning or navigating during a mission.
_CLEANING_PHASES = {"Running", "Docking mid-mission"}

# Returning phases — robot is heading back to dock after a mission.
# "hmPostMsn" (Docking — end of mission) is the normal post-mission return.
# "hmUsrDock" (Sent home) is user-initiated return.
# "Emptying bin" (evac) means the Clean Base is evacuating mid-mission —
# this is NOT the end of the mission, so we exclude it here.
_RETURNING_PHASES = {"Docking — end of mission", "Sent home"}

_DOCKED_PHASE = "Charging"
_STUCK_PHASE  = "Stuck"

# All active states: cleaning OR on the way back — used as "from" for
# the cleaning_finished trigger so it fires after any active mission.
_ACTIVE_PHASES = _CLEANING_PHASES | _RETURNING_PHASES


def _find_entity(
    hass: HomeAssistant, device_id: str, translation_key: str
) -> str | None:
    """Return the entity_id for a Roomba+ entity by its translation_key."""
    ent_reg = er.async_get(hass)
    for entry in er.async_entries_for_device(ent_reg, device_id):
        if entry.domain == DOMAIN or entry.platform == DOMAIN:
            if entry.translation_key == translation_key:
                return entry.entity_id
    return None


def _entry_id_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the config_entry_id for a Roomba+ device.

    v2.9.0 TRIGGER+ — needed to filter EVENT-BUS events (which carry
    entry_id, not device_id, in their payload — see callbacks.py/sensor.py)
    down to the specific robot this trigger was configured for. Each
    Roomba+ device belongs to exactly one config entry in practice (one
    device per robot), so the first entry in device.config_entries is
    always the right one.
    """
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return None
    if device.primary_config_entry:
        return device.primary_config_entry
    for entry_id in device.config_entries:
        return entry_id
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict]:
    """Return the list of triggers for a Roomba+ device."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return []

    # Only expose triggers for devices that belong to this integration
    if not any(ident[0] == DOMAIN for ident in device.identifiers):
        return []

    base = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device_id,
    }
    return [
        {**base, CONF_TYPE: TRIGGER_CLEANING_STARTED},
        {**base, CONF_TYPE: TRIGGER_CLEANING_FINISHED},
        {**base, CONF_TYPE: TRIGGER_STUCK},
        {**base, CONF_TYPE: TRIGGER_BIN_FULL},
        {**base, CONF_TYPE: TRIGGER_DOCKED},
        {**base, CONF_TYPE: TRIGGER_ERROR},
        {**base, CONF_TYPE: TRIGGER_ROOM_COMPLETED},
        {**base, CONF_TYPE: TRIGGER_MAINTENANCE_DUE},
        {**base, CONF_TYPE: TRIGGER_HEALTH_SCORE_DROP},
        {**base, CONF_TYPE: TRIGGER_MAP_RETRAIN_STARTED},
        {**base, CONF_TYPE: TRIGGER_MAP_RETRAIN_COMPLETED},
        {**base, CONF_TYPE: TRIGGER_FIRMWARE_UPDATED},
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach the requested trigger and return a detach callable."""
    trigger_type = config[CONF_TYPE]
    device_id = config[CONF_DEVICE_ID]

    if trigger_type == TRIGGER_CLEANING_STARTED:
        # Fire when phase sensor enters any cleaning state
        entity_id = _find_entity(hass, device_id, "phase")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": list(_CLEANING_PHASES),
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_CLEANING_FINISHED:
        # Fire when phase sensor transitions FROM a cleaning state TO docked/charging
        entity_id = _find_entity(hass, device_id, "phase")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "from": list(_ACTIVE_PHASES),
                "to": _DOCKED_PHASE,
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_STUCK:
        entity_id = _find_entity(hass, device_id, "phase")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": _STUCK_PHASE,
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_BIN_FULL:
        # Fire when bin_full binary sensor turns ON
        entity_id = _find_entity(hass, device_id, "bin_full")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": "on",
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_DOCKED:
        # Fire when phase sensor enters the docked/charging state
        entity_id = _find_entity(hass, device_id, "phase")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": _DOCKED_PHASE,
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_ERROR:
        # Fire when the error sensor changes to anything other than "None"
        entity_id = _find_entity(hass, device_id, "error")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "from": "None",
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_MAINTENANCE_DUE:
        # v2.9.0 — fire when the existing maintenance_due binary sensor
        # turns ON. Same shape as TRIGGER_BIN_FULL above.
        entity_id = _find_entity(hass, device_id, "maintenance_due")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": "on",
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_FIRMWARE_UPDATED:
        # v2.9.0 — fire when the existing firmware_updated binary sensor
        # (FW-SENSOR, v2.8.3) turns ON.
        entity_id = _find_entity(hass, device_id, "firmware_updated")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": "on",
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type in (
        TRIGGER_ROOM_COMPLETED,
        TRIGGER_MAP_RETRAIN_STARTED,
        TRIGGER_MAP_RETRAIN_COMPLETED,
    ):
        # v2.9.0 — these are EVENT-BUS (v2.8.6) events, not entity state.
        # entry_id (carried in the event payload by callbacks.py) filters
        # the event down to THIS device's robot — an exact-match
        # event_data filter is sufficient, no custom logic needed.
        entry_id = _entry_id_for_device(hass, device_id)
        if not entry_id:
            return lambda: None
        event_type = {
            TRIGGER_ROOM_COMPLETED: EVENT_ROOM_COMPLETED,
            TRIGGER_MAP_RETRAIN_STARTED: EVENT_MAP_RETRAIN_STARTED,
            TRIGGER_MAP_RETRAIN_COMPLETED: EVENT_MAP_RETRAIN_COMPLETED,
        }[trigger_type]
        event_config = event_trigger.TRIGGER_SCHEMA(
            {
                CONF_PLATFORM: "event",
                "event_type": event_type,
                "event_data": {"entry_id": entry_id},
            }
        )
        return await event_trigger.async_attach_trigger(
            hass, event_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_HEALTH_SCORE_DROP:
        # v2.9.0 — roomba_plus_health_change fires on ANY band crossing,
        # in either direction (see sensor.py's _async_health_tick). This
        # trigger only wants the WORSENING direction, which a plain
        # event_data exact-match filter can't express — attach our own
        # listener instead of delegating to the event trigger platform.
        # Mirrors homeassistant.components.homeassistant.triggers.event's
        # own job-dispatch convention so this behaves identically to a
        # "real" trigger platform from the automation's point of view.
        entry_id = _entry_id_for_device(hass, device_id)
        if not entry_id:
            return lambda: None

        from homeassistant.core import HassJob

        trigger_data = trigger_info["trigger_data"]
        job = HassJob(action, f"health_score_drop device trigger {trigger_info}")

        @callback
        def _handle_health_change(event: Event) -> None:
            if event.data.get("entry_id") != entry_id:
                return
            new_rank = HEALTH_BAND_RANK.get(event.data.get("band", ""), -1)
            old_rank = HEALTH_BAND_RANK.get(event.data.get("previous_band", ""), -1)
            if new_rank >= old_rank:
                return  # not a drop — same band, or improved
            hass.async_run_hass_job(
                job,
                {
                    "trigger": {
                        **trigger_data,
                        "platform": "device",
                        "event": event,
                        "description": f"event '{event.event_type}'",
                    }
                },
                event.context,
            )

        return hass.bus.async_listen(EVENT_HEALTH_CHANGE, _handle_health_change)

    return lambda: None
