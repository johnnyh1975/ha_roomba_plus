"""Event entities for the moments a robot reports (4.2.19).

THE BUS EVENTS DO NOT REACH EVERYONE. `roomba_plus_mission_completed`,
`roomba_plus_room_completed` and `roomba_plus_stuck` are fired on Home
Assistant's event bus, and a non-admin user cannot subscribe to a
custom bus event: Home Assistant's websocket allow-list covers its own
event types only. A dashboard card opened by such a user never heard a
mission end, so its history stayed stale until the next reload (I4 of
the card plan).

An event entity is an ordinary state: every user who can see the robot
can see it change. Each one mirrors one bus event -- the event is
still fired as before, so automations and device triggers keep working
-- and carries that event's payload as its attributes, `entry_id`
included.

ONE ENTITY PER KIND OF MOMENT, ONE EVENT TYPE EACH. An entity per
outcome ("completed", "cancelled", ...) would split the one question a
card asks -- "did a mission end?" -- across several states.

Stuck is reported by the Classic local-connection watchdog only; a
Prime robot gets no stuck entity rather than one that never fires.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_MISSION_COMPLETED, EVENT_ROOM_COMPLETED, EVENT_STUCK
from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry

#: (unique-id key, bus event, event type). The key doubles as the
#: translation key and the entity id suffix.
_MOMENTS: tuple[tuple[str, str, str], ...] = (
    ("mission_completed", EVENT_MISSION_COMPLETED, "mission_completed"),
    ("room_completed", EVENT_ROOM_COMPLETED, "room_completed"),
    ("stuck", EVENT_STUCK, "stuck"),
)

#: Moments a Prime robot never reports.
_CLASSIC_ONLY = frozenset({"stuck"})


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = config_entry.runtime_data
    prime = data.connection_type == ConnectionType.CLOUD_ONLY
    async_add_entities(
        RoombaMomentEvent(
            None if prime else data.roomba, data.blid, config_entry,
            key=key, bus_event=bus_event, event_type=event_type,
        )
        for key, bus_event, event_type in _MOMENTS
        if not (prime and key in _CLASSIC_ONLY)
    )


class RoombaMomentEvent(IRobotEntity, EventEntity):
    """One bus event of this robot, as an event entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
        *,
        key: str,
        bus_event: str,
        event_type: str,
    ) -> None:
        super().__init__(roomba, blid, config_entry)
        self._config_entry = config_entry
        self._bus_event = bus_event
        self._event_type = event_type
        self._attr_translation_key = key
        self._attr_event_types = [event_type]
        self._attr_unique_id = f"{self.robot_unique_id}_{key}"

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """Never on a robot message: the state changes only on an event."""
        return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(self._bus_event, self._on_bus_event)
        )

    @callback
    def _on_bus_event(self, event: Event[Any]) -> None:
        payload = dict(event.data or {})
        # THIS ROBOT'S EVENTS ONLY. Every payload carries the entry id;
        # one without it cannot be placed and is not this robot's.
        if payload.get("entry_id") != self._config_entry.entry_id:
            return
        self._trigger_event(self._event_type, payload)
        self.async_write_ha_state()
