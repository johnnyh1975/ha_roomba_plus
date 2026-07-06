"""Todo platform for Roomba+.

v3.4.0 TODO — `todo.roomba_*_maintenance`: three maintenance tasks with
real backing data and a coherent "done" semantic (reduced scope after
reflection — see the TODO plan §1.4 for why a fourth candidate,
"Retrain Smart Map", was dropped: no existing signal or action maps to
it cleanly).

  Replace filter / Clean brush roll (or pad) — real actionable items.
  Due date and "is it due" both reuse existing computations
  (sensor_helpers.py's _filter_days_until_due()/_brush_days_until_due(),
  same source binary_sensor.py's RoombaMaintenanceDue already uses).
  Marking complete calls the same MaintenanceStore reset methods +
  event-fire helper the existing Filter/Brush reset buttons (button.py)
  already use — no new reset logic, just a new caller. One correction
  found during the v3.4.0 bug hunt: button.py's BrushResetButton always
  calls reset_brush() regardless of model, even though reset_pad() is a
  distinct method (same underlying store slot, different log message)
  already used by the roomba_plus.reset_pad service. This entity calls
  whichever is semantically correct for the model, matching the
  service's convention rather than replicating the button's oversight.

  Reconfigure rooms — a live status item, not a manually-completable
  action. Sourced from zone_naming.py's unlabelled_zone_ids() (same
  condition driving the existing smart_zones_need_naming repair issue).
  Resolves itself once the user labels all zones via the existing
  naming wizard — todo_items is recomputed on every MQTT update, so a
  premature "complete" attempt is harmless: the item simply reappears
  on the next refresh if the underlying condition still holds.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
import datetime as dt_stdlib

from .const import is_mop
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .sensor_helpers import _brush_days_until_due, _filter_days_until_due
from .zone_naming import unlabelled_zone_ids

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0

_RECONFIGURE_ROOMS_DESCRIPTION = (
    "Unnamed zones detected. Use the zone-naming wizard (Settings or the "
    "linked Repair) to resolve — this item clears itself automatically "
    "once every zone has a name."
)


class RoombaMaintenanceTodo(IRobotEntity, TodoListEntity):
    """Filter/brush replacement reminders + a live room-naming status item."""

    _attr_translation_key = "maintenance"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_maintenance"

    # ── Shared helpers (same pattern as button.py's reset buttons) ──────────

    def _current_hr(self) -> int:
        return self.vacuum_state.get("bbrun", {}).get("hr", 0)

    def _maintenance_store(self) -> Any:
        return self._config_entry.runtime_data.maintenance_store

    @property
    def _brush_key(self) -> str:
        """"pad" for Braava/mop models, "brush" otherwise — same
        distinction RoombaMaintenanceDue already makes."""
        return "pad" if is_mop(self.vacuum_state) else "brush"

    # ── TodoListEntity interface ─────────────────────────────────────────────

    @property
    def todo_items(self) -> list[TodoItem]:
        """Recomputed on every call — same "always live, never a stale
        cache" approach as RoombaMaintenanceDue._due_items() and
        SmartZoneSelect's zone collection."""
        items: list[TodoItem] = []
        today = dt_util.now().date()

        filter_days = _filter_days_until_due(self)
        items.append(TodoItem(
            summary="Replace filter",
            uid="filter_maintenance",
            status=TodoItemStatus.NEEDS_ACTION,
            due=(today + dt_stdlib.timedelta(days=max(0, filter_days))
                 if filter_days is not None else None),
        ))

        brush_days = _brush_days_until_due(self)
        brush_key = self._brush_key
        items.append(TodoItem(
            summary=(
                "Clean brush roll" if brush_key == "brush" else "Clean/replace pad"
            ),
            uid=f"{brush_key}_maintenance",
            status=TodoItemStatus.NEEDS_ACTION,
            due=(today + dt_stdlib.timedelta(days=max(0, brush_days))
                 if brush_days is not None else None),
        ))

        if unlabelled_zone_ids(self.vacuum_state, self._config_entry.options):
            items.append(TodoItem(
                summary="Reconfigure rooms",
                uid="reconfigure_rooms",
                status=TodoItemStatus.NEEDS_ACTION,
                description=_RECONFIGURE_ROOMS_DESCRIPTION,
            ))

        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Only reacts to marking an item COMPLETED — there is no
        "un-complete" action to take (a NEEDS_ACTION transition just
        means the item will reappear on the next todo_items refresh
        anyway, since nothing here is cached)."""
        if item.status != TodoItemStatus.COMPLETED:
            return

        if item.uid == "filter_maintenance":
            store = self._maintenance_store()
            if store is not None:
                hr = self._current_hr()
                store.reset_filter(hr)
                await store.async_save(self.hass, self._config_entry.entry_id)
                from .services import _fire_maintenance_reset_event
                _fire_maintenance_reset_event(
                    self.hass, self._config_entry, "filter", hr
                )
        elif item.uid in ("brush_maintenance", "pad_maintenance"):
            store = self._maintenance_store()
            if store is not None:
                hr = self._current_hr()
                brush_key = self._brush_key
                # v3.4.0 bug-hunt fix: reset_pad() is a distinct method
                # from reset_brush() (same underlying store slot — see
                # MaintenanceStore.reset_pad()'s own docstring, "Braava
                # alias for reset_brush" — but a different log message).
                # Calling the semantically-correct one matches the
                # existing roomba_plus.reset_pad SERVICE's convention
                # (services.py's generic reset dispatcher does the same
                # getattr(store, f"reset_{part}") split) — unlike
                # button.py's BrushResetButton, which always calls
                # reset_brush() regardless of model, a pre-existing
                # inconsistency out of scope for this feature to fix.
                getattr(store, f"reset_{brush_key}")(hr)
                await store.async_save(self.hass, self._config_entry.entry_id)
                from .services import _fire_maintenance_reset_event
                _fire_maintenance_reset_event(
                    self.hass, self._config_entry, brush_key, hr
                )
        # "reconfigure_rooms": deliberately no action — see module
        # docstring. The item self-resolves once zones are named.

        self.async_write_ha_state()

    # ── Push update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        """Union of RoombaMaintenanceDue's gate (bbrun — filter/brush
        hours) and SmartZoneSelect's gate (cleanSchedule2/lastCommand —
        zone discovery)."""
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
    """Set up the maintenance todo list for this Roomba.

    Always created — filter/brush apply to every robot tier. On
    EPHEMERAL robots, "Reconfigure rooms" simply never appears (no
    cleanSchedule2/region data exists to be unlabelled in the first
    place — unlabelled_zone_ids() returns empty for them naturally).
    """
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    async_add_entities([RoombaMaintenanceTodo(roomba, blid, config_entry)])
