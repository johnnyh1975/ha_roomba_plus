"""Device triggers for Roomba+.

Triggers available in the HA Automation editor under "Device":

  cleaning_started  — robot transitions into a cleaning phase
  cleaning_finished — robot returns to dock after a mission
  stuck             — robot reports a stuck condition
  bin_full          — dust bin is full
  docked            — robot is docked and charging
  error             — robot reports any error code

All triggers fire by listening for HA state-change events on the relevant
sensor entities rather than raw MQTT messages. This keeps the implementation
simple, correct, and independent of roombapy internals.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

# ── Trigger type constants ────────────────────────────────────────────────────

TRIGGER_CLEANING_STARTED  = "cleaning_started"
TRIGGER_CLEANING_FINISHED = "cleaning_finished"
TRIGGER_STUCK             = "stuck"
TRIGGER_BIN_FULL          = "bin_full"
TRIGGER_DOCKED            = "docked"
TRIGGER_ERROR             = "error"

TRIGGER_TYPES = {
    TRIGGER_CLEANING_STARTED,
    TRIGGER_CLEANING_FINISHED,
    TRIGGER_STUCK,
    TRIGGER_BIN_FULL,
    TRIGGER_DOCKED,
    TRIGGER_ERROR,
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
        state_config = state_trigger.TRIGGER_SCHEMA(
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
        state_config = state_trigger.TRIGGER_SCHEMA(
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
        state_config = state_trigger.TRIGGER_SCHEMA(
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
        state_config = state_trigger.TRIGGER_SCHEMA(
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
        state_config = state_trigger.TRIGGER_SCHEMA(
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
        state_config = state_trigger.TRIGGER_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "from": "None",
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    return lambda: None
