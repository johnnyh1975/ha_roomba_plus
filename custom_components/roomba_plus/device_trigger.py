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

from typing import Any

import logging

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
    CONF_CONNECTION_TYPE,
    DOMAIN,
    EVENT_HEALTH_CHANGE,
    EVENT_MAP_RETRAIN_COMPLETED,
    EVENT_MAP_RETRAIN_STARTED,
    EVENT_ROOM_COMPLETED,
    HEALTH_BAND_RANK,
    PHASE_LABELS,
)
from .models import ConnectionType

_LOGGER = logging.getLogger(__name__)

# ── Trigger type constants ────────────────────────────────────────────────────

TRIGGER_CLEANING_STARTED  = "cleaning_started"
TRIGGER_CLEANING_FINISHED = "cleaning_finished"
TRIGGER_STUCK             = "stuck"
TRIGGER_BIN_FULL          = "bin_full"
#: A fault that stops a mission from STARTING, as opposed to one that
#: interrupts a running one. `TRIGGER_ERROR` covers the second; this
#: covers the case where nothing has gone wrong yet and nothing will
#: begin either -- a pad plate fitted when the user wanted a vacuum.
#:
#: Worth its own trigger because the useful automation is different:
#: an error while cleaning wants a notification, a blocked start wants
#: "tell me before I press the button".
TRIGGER_START_BLOCKED     = "start_blocked"
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
    TRIGGER_START_BLOCKED,
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
# THE SENSOR'S OWN VALUES, from the one table both generations use.
#
# These were display words -- "Running", "Charging", "Stuck", "Docking —
# end of mission" -- while the phase sensor reports translation keys
# (`running`, `charging`, ...) since 4.0.0. A state trigger compares the
# raw state, so cleaning_started, cleaning_finished, stuck and docked
# never fired on either generation, and nothing said so (4.5.0).
#
# Cleaning: the robot sets out. Entering a mid-mission dock is not a
# start, so it is not here.
_CLEANING_PHASES = {PHASE_LABELS["run"]}

# Returning: heading back after the mission, or sent home. Emptying the
# bin mid-mission is not the end of one.
_RETURNING_PHASES = {PHASE_LABELS["hmPostMsn"], PHASE_LABELS["hmUsrDock"]}

#: On its dock again. Charging mid-mission (`charging_mid_mission`) is a
#: pause, not the end.
_DOCKED_PHASE = PHASE_LABELS["charge"]
_STUCK_PHASE  = PHASE_LABELS["stuck"]

# All active states: cleaning OR on the way back -- the "from" of
# cleaning_finished, so it fires after any mission.
_ACTIVE_PHASES = _CLEANING_PHASES | {PHASE_LABELS["hmMidMsn"]} | _RETURNING_PHASES

#: The error sensor without an error: no value, so `unknown`.
_NO_ERROR_STATES = ["unknown", "unavailable"]

#: Where every entity comes from after a restart. A robot that is still
#: docked, stuck or showing an error then is not news.
_RESTART_STATES = ["unavailable", "unknown"]


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


#: THE ENTITY EACH STATE-BASED TRIGGER WATCHES, by translation key (4.6.0).
#:
#: Every trigger was offered to every robot, and one whose entity the
#: robot does not have attached to nothing: the automation saved, loaded
#: and never fired. On a Prime robot five of the thirteen were like that
#: (no bin-full, maintenance-due, firmware or health entity, and the
#: map-retrain events are Classic's). `start_blocked` looked only for the
#: Prime sensor, so on Classic it never fired at all.
#:
#: Offered now only where one of these exists, and `start_blocked` takes
#: both: the robot refusing a start (Prime, `prime_start_blocked`) and a
#: start held back by the blocking sensors of `smart_start`
#: (`start_blocked`, either generation).
_TRIGGER_ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    TRIGGER_CLEANING_STARTED: ("phase",),
    TRIGGER_CLEANING_FINISHED: ("phase",),
    TRIGGER_STUCK: ("phase",),
    TRIGGER_DOCKED: ("phase",),
    TRIGGER_ERROR: ("error",),
    TRIGGER_BIN_FULL: ("bin_full",),
    TRIGGER_START_BLOCKED: ("prime_start_blocked", "start_blocked"),
    TRIGGER_MAINTENANCE_DUE: ("maintenance_due",),
    TRIGGER_FIRMWARE_UPDATED: ("firmware_updated",),
    # `roomba_plus_health_change` comes from the integration-health
    # sensor; without it the event is never fired for this robot.
    TRIGGER_HEALTH_SCORE_DROP: ("integration_health",),
}

#: Bus events only the local connection produces (Smart Map retraining,
#: `callbacks.py`). A Prime robot never sends them.
_CLASSIC_ONLY_EVENT_TRIGGERS = frozenset({
    TRIGGER_MAP_RETRAIN_STARTED,
    TRIGGER_MAP_RETRAIN_COMPLETED,
})


def _find_entities(
    hass: HomeAssistant, device_id: str, translation_keys: tuple[str, ...]
) -> list[str]:
    """Every Roomba+ entity of the device with one of `translation_keys`."""
    found = (_find_entity(hass, device_id, key) for key in translation_keys)
    return [entity_id for entity_id in found if entity_id]


def _device_is_classic(hass: HomeAssistant, device_id: str) -> bool:
    """Whether the device's entry uses the local (Classic) connection.

    Read from the entry's data, which exists before and after setup; an
    entry that cannot be found counts as Classic, the older behaviour."""
    entry_id = _entry_id_for_device(hass, device_id)
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry is None:
        return True
    return entry.data.get(CONF_CONNECTION_TYPE) != ConnectionType.CLOUD_ONLY.value


def _trigger_can_fire(hass: HomeAssistant, device_id: str, trigger_type: str) -> bool:
    """Whether a trigger of this type has anything to fire on for the device."""
    keys = _TRIGGER_ENTITY_KEYS.get(trigger_type)
    if keys is not None:
        return bool(_find_entities(hass, device_id, keys))
    if trigger_type in _CLASSIC_ONLY_EVENT_TRIGGERS:
        return _device_is_classic(hass, device_id)
    return True


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
) -> list[dict[str, Any]]:
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
    offered = (
        TRIGGER_CLEANING_STARTED,
        TRIGGER_CLEANING_FINISHED,
        TRIGGER_STUCK,
        TRIGGER_BIN_FULL,
        TRIGGER_START_BLOCKED,
        TRIGGER_DOCKED,
        TRIGGER_ERROR,
        TRIGGER_ROOM_COMPLETED,
        TRIGGER_MAINTENANCE_DUE,
        TRIGGER_HEALTH_SCORE_DROP,
        TRIGGER_MAP_RETRAIN_STARTED,
        TRIGGER_MAP_RETRAIN_COMPLETED,
        TRIGGER_FIRMWARE_UPDATED,
    )
    # ONLY WHAT CAN FIRE FOR THIS ROBOT (4.6.0): see _TRIGGER_ENTITY_KEYS.
    # An automation stored with a trigger no longer offered still attaches
    # as before; this only stops the editor offering a dead one.
    return [
        {**base, CONF_TYPE: trigger_type}
        for trigger_type in offered
        if _trigger_can_fire(hass, device_id, trigger_type)
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
        # Fire when the phase sensor enters a cleaning state -- not when
        # the robot carries on after a mid-mission recharge or bin empty.
        entity_id = _find_entity(hass, device_id, "phase")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "to": sorted(_CLEANING_PHASES),
                "not_from": [
                    PHASE_LABELS["hmMidMsn"], "charging_mid_mission", PHASE_LABELS["evac"],
                    *_RESTART_STATES,
                ],
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
                # Also from emptying the bin or a mid-mission recharge
                # (4.5.0): a Clean Base empties after docking, and a
                # mission cancelled while recharging ends there.
                "from": sorted(
                    _ACTIVE_PHASES | {PHASE_LABELS["evac"], "charging_mid_mission"}
                ),
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
                "not_from": _RESTART_STATES,
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_START_BLOCKED:
        # Fires when a blocking fault APPEARS. The binary sensor carries
        # `blocked_reason` and `available_modes` as attributes, so an
        # automation can say what to do instead rather than only that
        # something is wrong.
        #
        # BOTH KINDS OF HELD-BACK START (4.6.0). This looked only for the
        # Prime sensor, so on Classic it attached to nothing. A start held
        # back by the blocking sensors of `smart_start` is the other kind,
        # on either generation (@ar2000ar, #241: "failing to start a job
        # should ... trigger an event").
        entity_ids = _find_entities(
            hass, device_id, _TRIGGER_ENTITY_KEYS[TRIGGER_START_BLOCKED]
        )
        if not entity_ids:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_ids,
                "to": "on",
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
                # Not from `idle` (4.5.0): a docked robot reads `idle` at
                # 100 % and `charging` once it drops to 99, so it "docked"
                # again at every top-up.
                "not_from": [*_RESTART_STATES, "idle"],
            }
        )
        return await state_trigger.async_attach_trigger(
            hass, state_config, action, trigger_info, platform_type="device"
        )

    if trigger_type == TRIGGER_ERROR:
        # Fire when the error sensor gets an error. Without one it has no
        # value -- `unknown`, never the text "None" this compared with,
        # so the trigger never fired.
        entity_id = _find_entity(hass, device_id, "error")
        if not entity_id:
            return lambda: None
        state_config = state_trigger.TRIGGER_STATE_SCHEMA(
            {
                CONF_PLATFORM: "state",
                CONF_ENTITY_ID: entity_id,
                "not_to": _NO_ERROR_STATES,
                "not_from": ["unavailable"],
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
        # NO KeyError ON AN UNKNOWN TRIGGER TYPE.
        #
        # `trigger_type` comes from a stored automation, so it is
        # whatever was written when that automation was created. A
        # trigger renamed or removed in a later version arrives here as
        # a string this dict does not have, and a KeyError inside an
        # automation's attach step breaks the automation with a
        # traceback rather than a message.
        #
        # The same lookup-without-a-fallback pattern crashed a tester's
        # tooling run twice in one day, once for "FAIL" and once for
        # "SKIP". Worth not repeating where a user's automation is on
        # the other end.
        event_type = {
            TRIGGER_ROOM_COMPLETED: EVENT_ROOM_COMPLETED,
            TRIGGER_MAP_RETRAIN_STARTED: EVENT_MAP_RETRAIN_STARTED,
            TRIGGER_MAP_RETRAIN_COMPLETED: EVENT_MAP_RETRAIN_COMPLETED,
        }.get(trigger_type)
        if event_type is None:   # unreachable: the three keys are the branch
            return lambda: None
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

    # AN UNKNOWN TRIGGER TYPE LANDS HERE — a type from an older version, or
    # a hand-edited automation. The warning used to sit inside the event
    # branch above, which only the three known event types can enter, so
    # it could never fire; an unknown type returned here in silence and
    # the automation loaded, never fired, and said nothing. Found by the
    # coverage work for the quality scale.
    _LOGGER.warning(
        "roomba_plus: automation uses unknown trigger type %r -- "
        "it will never fire. Recreate the trigger.",
        trigger_type,
    )
    return lambda: None
