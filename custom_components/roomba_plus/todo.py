"""Todo platform for Roomba+.

`todo.<robot>_maintenance` for a Classic robot (Prime has its own list,
todo_prime.py). Three kinds of item:

  REPLACE -- one per part the robot has: filter, main brushes (the mop
  pad on a Braava), side brush, Clean Base bag. Due when the same
  counter `binary_sensor.<robot>_maintenance_due` reads says so; the
  due date is the wear-rate estimate the part sensors show. Ticking one
  off records the replacement exactly as the reset button does: local
  counter, event, iRobot's cloud counter where the account has one.

  CLEAN -- iRobot's calendar care (4.3.2, CARE_TASKS in const.py):
  filter and main brushes weekly (twice weekly with pets), side brush
  monthly, wheels every two weeks, cliff sensors and charging contacts
  monthly. Ticking one off records the cleaning and moves its due date;
  no replacement counter moves.

  NAME THE ROOMS -- a live status item, not an action. It clears itself
  once every room on the map has a name.

THE LIST USED TO CALL A REPLACEMENT A CLEANING. Its brush item read
"Clean brush roll" and ticking it reset the main-brush replacement
counter -- so cleaning the brushes weekly, as the item invited, kept the
replacement reminder from ever coming due. The two are separate items
now, with separate effects.

AN ITEM NOT DUE IS LISTED AS DONE, with its due date. That is how the
Prime list reads too, and it keeps the open count meaningful: ten items
permanently open is a badge nobody looks at. An item comes back to
"needs action" on its due date; the list re-checks at midnight, since
nothing the robot sends marks a calendar day.

WORDING COMES FROM THE TRANSLATIONS (selector `maintenance_item`):
Home Assistant does not translate to-do summaries, and the list was
English in every language. English below is the fallback if the table
cannot be read.
"""
from __future__ import annotations

import datetime as dt_stdlib
import logging
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PETS,
    CONSUMABLE_ROLES,
    DEFAULT_PETS,
    DOMAIN,
    IROBOT_PART_ROLE_CLEAN_BASE_BAG,
    IROBOT_PART_ROLE_FILTER,
    IROBOT_PART_ROLE_MAIN_BRUSH,
    IROBOT_PART_ROLE_SIDE_BRUSH,
    is_braava,
    care_reminders_enabled,
    lifetime_hours,
    maintenance_changed_signal,
)
from .entity import IRobotEntity
from .models import ConnectionType, RoombaConfigEntry
from .room_cleaning import smart_rooms_to_name
from .sensor_helpers import _consumable_days_until_due
from .services import _async_push_part_reset_to_cloud, _fire_maintenance_reset_event
from .zone_naming import unlabelled_zone_ids

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

#: English wording, used when the translation table cannot be read.
#: Keys match `selector.maintenance_item.options` in strings.json.
_ENGLISH: dict[str, str] = {
    "replace_filter": "Replace the filter",
    "replace_main_brushes": "Replace the main brushes",
    "replace_mop_pad": "Replace the mop pad",
    "replace_side_brush": "Replace the side brush",
    "replace_clean_base_bag": "Replace the Clean Base bag",
    "clean_filter": "Clean the filter",
    "clean_brushes": "Clean the main brushes",
    "clean_side_brush": "Clean the side brush",
    "clean_wheel": "Clean the wheels",
    "clean_cliff_sensors": "Clean the cliff sensors",
    "clean_contact": "Clean the charging contacts",
    "every_days": "Every {days} days, as iRobot recommends.",
    "reconfigure_rooms": "Name the rooms",
    "reconfigure_rooms_description": (
        "Some rooms have no name yet. Open Configure → Rooms & zones: it "
        "shows the robot's map with their numbers, or says what is missing. "
        "This item clears itself once every room has a name."
    ),
}

#: Replacement items: (role, uid, wording key, store/event part name).
#: The uids are the ones the list has always used, so an automation
#: keyed on them keeps working.
_REPLACEMENTS: tuple[tuple[str, str, str, str], ...] = (
    (IROBOT_PART_ROLE_FILTER, "filter_maintenance", "replace_filter", "filter"),
    (IROBOT_PART_ROLE_MAIN_BRUSH, "brush_maintenance", "replace_main_brushes", "brush"),
    (IROBOT_PART_ROLE_SIDE_BRUSH, "side_brush_maintenance", "replace_side_brush", "side_brush"),
    (IROBOT_PART_ROLE_CLEAN_BASE_BAG, "clean_base_bag_maintenance",
     "replace_clean_base_bag", "clean_base_bag"),
)
_PAD_UID = "pad_maintenance"
_CARE_PREFIX = "care_"


class RoombaMaintenanceTodo(IRobotEntity, TodoListEntity):
    """Replacement and cleaning reminders + a live room-naming status item."""

    _attr_translation_key = "maintenance"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_maintenance"
        self._wording: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_load_wording()
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._midnight, hour=0, minute=0, second=5
            )
        )

    async def _async_load_wording(self) -> None:
        """The item texts in Home Assistant's language, once."""
        try:
            from homeassistant.helpers.translation import (  # noqa: PLC0415
                async_get_translations,
            )

            table = await async_get_translations(
                self.hass, self.hass.config.language, "selector", {DOMAIN}
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Maintenance list: no translation table", exc_info=True)
            return
        prefix = f"component.{DOMAIN}.selector.maintenance_item.options."
        self._wording = {
            key[len(prefix):]: text
            for key, text in table.items()
            if key.startswith(prefix) and isinstance(text, str)
        }

    @callback
    def _midnight(self, _now: Any) -> None:
        """A new day can bring a care item due; the robot will not say so."""
        self.async_write_ha_state()

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _text(self, key: str) -> str:
        wording = getattr(self, "_wording", None) or {}
        return wording.get(key) or _ENGLISH[key]

    def _current_hr(self) -> int:
        return lifetime_hours(self.vacuum_state)

    def _maintenance_store(self) -> Any:
        return self._config_entry.runtime_data.maintenance_store

    def _braava(self) -> bool:
        return is_braava(self.vacuum_state or {})

    # ── Items ────────────────────────────────────────────────────────────────

    @property
    def todo_items(self) -> list[TodoItem]:
        """Recomputed on every call: nothing here is cached."""
        store = self._maintenance_store()
        items: list[TodoItem] = []
        if store is not None:
            items.extend(self._replacement_items(store))
            if care_reminders_enabled(self._config_entry.options):
                items.extend(self._care_items(store))
        if self._rooms_need_naming():
            items.append(TodoItem(
                summary=self._text("reconfigure_rooms"),
                uid="reconfigure_rooms",
                status=TodoItemStatus.NEEDS_ACTION,
                description=self._text("reconfigure_rooms_description"),
            ))
        return items

    def _replacement_items(self, store: Any) -> list[TodoItem]:
        state = dict(self.vacuum_state or {})
        braava = self._braava()
        due_keys = set(store.due_items(state, self._config_entry.options))
        today = dt_util.now().date()
        items: list[TodoItem] = []
        for role, uid, key, _part in _REPLACEMENTS:
            spec = CONSUMABLE_ROLES[role]
            if spec.absent_when is not None and spec.absent_when(state):
                continue
            if braava and role != IROBOT_PART_ROLE_MAIN_BRUSH:
                # A Braava has a pad, and no filter, brushes or bag.
                continue
            if braava:
                uid, key = _PAD_UID, "replace_mop_pad"
            due_now = spec.due_key in due_keys or (
                spec.mop_due_key is not None and spec.mop_due_key in due_keys
            )
            days = _consumable_days_until_due(self, role)
            items.append(TodoItem(
                summary=self._text(key),
                uid=uid,
                status=TodoItemStatus.NEEDS_ACTION if due_now else TodoItemStatus.COMPLETED,
                due=(
                    today + dt_stdlib.timedelta(days=max(0, days))
                    if days is not None else None
                ),
            ))
        return items

    def _care_items(self, store: Any) -> list[TodoItem]:
        pets = bool(self._config_entry.options.get(CONF_PETS, DEFAULT_PETS))
        today = dt_util.now().date()
        items: list[TodoItem] = []
        for row in store.care_schedule(braava=self._braava(), pets=pets):
            due = dt_util.parse_datetime(row["due"])
            due_date = dt_util.as_local(due).date() if due is not None else None
            days = row["interval_days"]
            items.append(TodoItem(
                summary=self._text(f"clean_{row['task']}"),
                uid=f"{_CARE_PREFIX}{row['task']}",
                status=(
                    TodoItemStatus.NEEDS_ACTION
                    if due_date is not None and due_date <= today
                    else TodoItemStatus.COMPLETED
                ),
                due=due_date,
                description=self._text("every_days").replace(
                    "{days}", f"{days:g}"
                ),
            ))
        return items

    def _rooms_need_naming(self) -> bool:
        """The same question the "Rooms & zones" step answers.

        WITH AN IROBOT ACCOUNT, rooms take their names from it, and only
        a room on the map without a name anywhere needs one. Checked
        against the robot's own region ids, this item stood open for
        @liblit's i7 while every room on his map had its name.
        """
        options = self._config_entry.options
        data = getattr(self._config_entry, "runtime_data", None)
        coordinator = getattr(data, "cloud_coordinator", None)
        if coordinator is not None and getattr(coordinator, "data", None) is not None:
            return bool(smart_rooms_to_name(data, options))
        return bool(unlabelled_zone_ids(self.vacuum_state, options))

    # ── Ticking off ──────────────────────────────────────────────────────────

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticking an item off records what it names.

        Only the step to COMPLETED does anything: there is no "undo a
        replacement", and an item unticked simply shows its real state
        again on the next refresh.
        """
        if item.status != TodoItemStatus.COMPLETED:
            return
        store = self._maintenance_store()
        uid = item.uid or ""
        if store is not None and uid.startswith(_CARE_PREFIX):
            await self._record_cleaning(store, uid[len(_CARE_PREFIX):])
        elif store is not None:
            await self._record_replacement(store, uid)
        # "reconfigure_rooms": deliberately no action -- it clears itself.
        self.async_write_ha_state()

    async def _record_cleaning(self, store: Any, task: str) -> None:
        method = getattr(store, f"reset_{task}_cleaning", None)
        if method is None:
            return
        method()
        await store.async_save(self.hass, self._config_entry.entry_id)
        _fire_maintenance_reset_event(self.hass, self._config_entry, task, None)
        async_dispatcher_send(self.hass, maintenance_changed_signal(self._blid))

    async def _record_replacement(self, store: Any, uid: str) -> None:
        if uid == _PAD_UID:
            part = "pad"
        else:
            part = next((p for _r, u, _k, p in _REPLACEMENTS if u == uid), "")
            if not part:
                return
        hr = self._current_hr()
        getattr(store, f"reset_{part}")(hr)
        await store.async_save(self.hass, self._config_entry.entry_id)
        _fire_maintenance_reset_event(self.hass, self._config_entry, part, hr)
        async_dispatcher_send(self.hass, maintenance_changed_signal(self._blid))
        await _async_push_part_reset_to_cloud(
            self._config_entry, self._config_entry.runtime_data, part
        )

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """bbrun moves the replacement counters; cleanSchedule2 and
        lastCommand change which rooms exist."""
        return (
            "bbrun" in new_state
            or "cleanSchedule2" in new_state
            or "lastCommand" in new_state
        )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the maintenance todo list for this Roomba."""
    # PRIME HAS ITS OWN LIST, built from parts the robot counts itself
    # rather than from lifetime hours and thresholds of ours.
    if config_entry.runtime_data.connection_type is ConnectionType.CLOUD_ONLY:
        from .todo_prime import async_setup_prime_todo  # noqa: PLC0415

        await async_setup_prime_todo(hass, config_entry, async_add_entities)
        return

    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    async_add_entities([RoombaMaintenanceTodo(roomba, blid, config_entry)])
