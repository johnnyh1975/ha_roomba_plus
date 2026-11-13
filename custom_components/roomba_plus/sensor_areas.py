"""When each Home Assistant area was last vacuumed and mopped (4.6.0, B14).

ACROSS EVERY ROBOT. A household with a Roomba and a Braava has one
kitchen: "last mopped" is the Braava's mission, "last vacuumed" the
Roomba's, and an automation asking about the kitchen should not have to
know which robot does what. The value of each sensor is the newest
mission of its kind among all loaded Roomba+ robots whose rooms map to
the area; `robot` and `room` say which.

ONE PAIR PER ROBOT AND AREA. An entity belongs to one config entry, and
an area two robots clean appears under both -- with the same value, the
household's. Off by default: a twelve-room home is twenty-four sensors,
and most people want two or three of them.

THE AREA OF A ROOM is area_resolver.async_area_for_room: the segment
mapping first, the name second. A room without an area gets no sensor.
WHAT COUNTS AS VACUUM OR MOP is the mission's cleaning mode
(MissionStore.room_cleaning_history_by_mode); a vacuum-and-mop mission
counts for both.

Areas found later -- a Prime map whose names arrive after setup, a room
mapped since -- get their pair at the next mission end (add-only).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .area_resolver import async_area_for_room, async_area_name
from .const import DOMAIN, EVENT_MISSION_COMPLETED
from .entity import IRobotEntity
from .mission_queries import reported_state, rooms_of
from .room_times import robot_default_mode
from .sensor_rooms import _region_maps_for

_LOGGER = logging.getLogger(__name__)

KINDS: tuple[str, ...] = ("vacuum", "mop")


def _robot_history(hass: HomeAssistant, entry: Any) -> dict[str, dict[str, tuple[str, str]]]:
    """{area_id: {kind: (iso, room)}} from one robot's missions."""
    data = getattr(entry, "runtime_data", None)
    store = getattr(data, "mission_store", None)
    if data is None or store is None:
        return {}
    region_map, umf = _region_maps_for(data)
    cloud = getattr(data, "cloud_coordinator", None)
    by_pmap = getattr(cloud, "regions_by_pmap", None) if cloud is not None else None
    history = store.room_cleaning_history_by_mode(
        region_map, umf, by_pmap, robot_default_mode(reported_state(data)),
    )
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for room, ends in history.items():
        area = async_area_for_room(hass, entry, room)
        if not area:
            continue
        for kind, iso in ends.items():
            held = out.setdefault(area, {}).get(kind)
            if held is None or iso > held[0]:
                out[area][kind] = (iso, room)
    return out


def household_area_history(hass: HomeAssistant) -> dict[str, dict[str, dict[str, Any]]]:
    """{area_id: {kind: {"at", "robot", "room"}}} across every loaded robot."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if getattr(entry, "runtime_data", None) is None:
            continue
        try:
            robot = _robot_history(hass, entry)
        except Exception:  # noqa: BLE001 -- one robot's store must not hide the others
            _LOGGER.debug("roomba_plus: area history of %s failed", entry.title, exc_info=True)
            continue
        for area, kinds in robot.items():
            for kind, (iso, room) in kinds.items():
                held = out.setdefault(area, {}).get(kind)
                if held is None or iso > held["at"]:
                    out[area][kind] = {"at": iso, "robot": entry.title, "room": room}
    return out


def robot_areas(hass: HomeAssistant, entry: Any) -> set[str]:
    """The areas this robot's known rooms map to."""
    return {r["area_id"] for r in rooms_of(hass, entry) if r.get("area_id")}


class RoombaAreaLastCleanedSensor(IRobotEntity, SensorEntity):
    """When an area was last vacuumed or mopped, by any Roomba+ robot."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True

    def __init__(
        self, roomba: Any, blid: str, config_entry: Any, area_id: str, kind: str,
        area_name: str | None = None,
    ) -> None:
        super().__init__(roomba, blid, config_entry)
        self._config_entry = config_entry
        self._area_id = area_id
        self._kind = kind
        key = "area_last_vacuumed" if kind == "vacuum" else "area_last_mopped"
        self.entity_description = SensorEntityDescription(key=key, translation_key=key)
        self._attr_unique_id = f"{self.robot_unique_id}_{key}_{area_id}"
        # The area's name when the sensor was made; renaming the area
        # later renames the sensor at the next start.
        self._attr_translation_placeholders = {"area": area_name or area_id}

    def _held(self) -> dict[str, Any] | None:
        if self.hass is None:
            return None
        return household_area_history(self.hass).get(self._area_id, {}).get(self._kind)

    @property
    def native_value(self) -> Any:
        held = self._held()
        return dt_util.parse_datetime(held["at"]) if held else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        held = self._held() or {}
        return {"area_id": self._area_id, "robot": held.get("robot"), "room": held.get("room")}

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_mission(_event: Event[Any]) -> None:
            # ANY ROBOT'S MISSION: the value is the household's.
            self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_MISSION_COMPLETED, _on_mission))


@callback
def async_setup_area_sensors(
    hass: HomeAssistant, config_entry: Any, async_add_entities: Any, roomba: Any,
) -> None:
    """Add a pair per area this robot maps, now and at each mission end."""
    data = config_entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        try:
            areas = robot_areas(hass, config_entry) - added
        except Exception:  # noqa: BLE001 -- area sensors are a bonus
            _LOGGER.debug("roomba_plus: area sensors not built", exc_info=True)
            return
        if not areas:
            return
        added.update(areas)
        async_add_entities([
            RoombaAreaLastCleanedSensor(
                roomba, data.blid, config_entry, area, kind, async_area_name(hass, area),
            )
            for area in sorted(areas) for kind in KINDS
        ])

    @callback
    def _on_mission(event: Event[Any]) -> None:
        if (event.data or {}).get("entry_id") == config_entry.entry_id:
            _add_new()

    _add_new()
    config_entry.async_on_unload(hass.bus.async_listen(EVENT_MISSION_COMPLETED, _on_mission))
