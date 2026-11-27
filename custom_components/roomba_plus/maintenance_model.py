"""The maintenance model: one entry per part and per care task (4.5.0).

ONE CALCULATION, MANY READERS. Until 4.5.0 every surface worked out
maintenance for itself -- the remaining-hours sensors, the due sensor,
the to-do list, the household API -- from the same store but not the
same rules, so a 980 on its series profile showed one interval and was
due at another, and a reset button left an i3+ due until the next
restart. This module builds the entries once; the status sensor, the
part sensors' attributes, the event entity, the actions and the REST
view read them.

AN ENTRY, for Classic and Prime alike (`MaintenanceItem`):

    id              stable: "replace.filter", "clean.wheel",
                    "replace.part_202"
    action          "replace" or "clean"
    part            the role: filter, main_brush, wheel, bag, ...
    level           ok, soon, due, overdue
    used            share of the interval used, 0 ... 1+
    remaining/unit  what is left, in the counter's unit
    due             when it is (or was) due, where that can be said
    interval/...    the interval, its unit and where it comes from
    last_done       when it was last replaced or cleaned
    done            what "done" writes: local, cloud, both

THE LEVELS. Replacing: soon from 80 % of the interval (an option: 80,
60 or never), due at 100 %, overdue seven days after it was first seen
due. Cleaning: due on its date, overdue after half an interval more --
a weekly filter after three and a half days. Cleaning has no "soon": at
intervals of three to thirty days it would be noise.

THE TRACKER (`MaintenanceTracker`) keeps the entries current and is the
only part that touches Home Assistant: it recomputes on a timer and
whenever counters or resets change, remembers per entry when it became
due and what was last announced, and sends one event per level change
-- not one per restart. It also carries out "done" and "snooze".

Plain functions above the tracker, no entity imports: the API views
read this module, and the package root imports those.
"""
from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .parts_catalog import help_url_for
from .const import (
    CARE_TASKS,
    CONF_CARE_GUIDE_ON_ERROR,
    DEFAULT_CARE_GUIDE_ON_ERROR,
    CONF_MAINTENANCE_OVERDUE_REPAIRS,
    CONF_MAINTENANCE_SOON,
    DEFAULT_MAINTENANCE_OVERDUE_REPAIRS,
    CONF_PETS,
    CONSUMABLE_ROLES,
    DEFAULT_MAINTENANCE_SOON,
    DEFAULT_PETS,
    DOMAIN,
    IROBOT_PART_ROLE_CLEAN_BASE_BAG,
    IROBOT_PART_ROLE_FILTER,
    IROBOT_PART_ROLE_MAIN_BRUSH,
    IROBOT_PART_ROLE_SIDE_BRUSH,
    MAINTENANCE_SOON_SHARES,
    care_reminders_enabled,
    decode_not_ready,
    has_clean_base,
    is_braava,
    lifetime_hours,
    maintenance_changed_signal,
    maintenance_event_signal,
    maintenance_items_signal,
)
from .care_guides import guide_id, guide_link, guide_title
from .maintenance_store import MAINTENANCE_LOG_SIZE, MaintenanceStore, _parse
from .models import ConnectionType
from .repairs import async_sync_maintenance_issues
from .services import (
    _async_push_part_reset_to_cloud,
    _fire_maintenance_reset_event,
    async_forget_old_battery,
)
from .prime_parts import (
    _name_key,
    needs_attention,
    part_count_in_display_unit,
    parts_the_robot_cannot_have,
    prime_part_role,
)

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

LEVEL_OK = "ok"
LEVEL_SOON = "soon"
LEVEL_DUE = "due"
LEVEL_OVERDUE = "overdue"
LEVELS: tuple[str, ...] = (LEVEL_OK, LEVEL_SOON, LEVEL_DUE, LEVEL_OVERDUE)
_RANK = {level: rank for rank, level in enumerate(LEVELS)}

ACTION_REPLACE = "replace"
ACTION_CLEAN = "clean"
#: An entry from a robot error that points to a care task (4.5.0, E12):
#: "check the main brushes". There while the robot reports the error.
ACTION_CHECK = "check"

#: Robot errors that point to a care task (4.5.0b2: checked against
#: iRobot's help page for each code, claude/IROBOT_PFLEGE_ONLINEINHALTE.md
#: §6/§7, and the robot's app text).
#:
#: CLASSIC WAS FIRST BUILT FROM OUR OWN ERROR TABLE'S ACTION SENTENCES --
#: the table 4.5.0b2 replaced with the apps' texts. Checked again, code by
#: code, against iRobot's pages for the 980:
#:   kept    2 brushes; 4, 5, 7, 10 wheel (pages 8958/8959/8963/8964: free
#:           the wheel); 12 cliff sensors; 14, 36, 216 bin; 26, 30 filter;
#:           104, 105, 119 contacts; 453, 454, 460 wash dock; 502 contacts
#:   new     1, 3, 13 wheel (8955/8956/532: check the wheels when it
#:           happens on a flat floor); 6, 25 cliff sensors (8960/8974);
#:           121 contacts (26166)
#:   moved   8 to the filter (8961: vacuum motor or clogged filter, not
#:           the bin)
#:   gone    11 (8965: replace the bin, no care step), 20, 27, 38 (no
#:           page; the app says "needs maintenance" or "Robot error"),
#:           68 (the app says hold CLEAN 10 s -- a reboot, not a clean),
#:           78, 79 (Terra mower codes), 102, 103 (no contact step)
#:   not added 17 (4.5.0b4): "Path blocked". The app says clear obstacles
#:           and check the lighting; page 8969 (980) lists ten steps, eight
#:           about the room (doors, carpet, light, clutter) and two about
#:           parts (camera window, floor sensor). A "check the camera"
#:           entry would be wrong most of the time, and on a robot with no
#:           camera always; there is no camera check per model. iRobot's
#:           article for the model is linked from the error (help_url).
#:
#: PRIME WAITED FOR A TESTER'S ERROR (L10) beyond 2, 6, 201, 1000, 1001.
#: Decided 19.10.2026 (D3): iRobot's own Prime help pages are the
#: evidence -- 4/5 wheels (10512/10510), 14 bin (10514), 26 filter
#: (10526), 104/105/119/121 contacts (10604/10605/10619/10622), 517 the
#: wash system (40517/70517).
ERROR_CARE_TASKS_CLASSIC: dict[int, str] = {
    2: "brushes",
    **{code: "filter" for code in (8, 26, 30)},
    **{code: "wheel" for code in (1, 3, 4, 5, 7, 10, 13)},
    **{code: "cliff_sensors" for code in (6, 12, 25)},
    **{code: "contact" for code in (104, 105, 119, 121, 502)},
    **{code: "bin" for code in (14, 36, 216)},
    **{code: "wash_dock" for code in (453, 454, 460)},
}
ERROR_CARE_TASKS_PRIME: dict[int, str] = {
    2: "brushes",
    **{code: "wheel" for code in (4, 5)},
    6: "cliff_sensors",
    14: "bin",
    26: "filter",
    **{code: "contact" for code in (104, 105, 119, 121)},
    201: "cliff_sensors",
    517: "wash_dock",
    1000: "side_brush",
    1001: "side_brush",
}
#: Classic errors that point to the cliff sensors without saying they
#: are dirty: 6 and 25 are "stuck near a cliff" -- most often a real edge
#: or a dark floor (pages 8960/8974). They show the guide and go with the
#: error; they do not make the cleaning due (E10). Prime 6 does: its app
#: says "Clean Cliff Sensors".
CLASSIC_HINT_ONLY_ERRORS = frozenset({6, 25})
#: Readiness states (decode_not_ready's index) that point to a care
#: task, by the name of the reason: the robot will not start because it
#: sees a drop, a brush is stuck, the bin is missing or full, or its
#: camera does not answer (4.5.0). Classic only -- a Prime robot's
#: refusals carry no state that names a part.
NOT_READY_REASONS: dict[int, str] = {
    1: "cliff",
    6: "brush_stall",
    7: "no_bin",
    13: "bin_full",
    38: "camera",
}
#: A reason -- a refusal, or the bin reported full -- and its care task.
REASON_CARE_TASKS: dict[str, str] = {
    "cliff": "cliff_sensors",
    "brush_stall": "brushes",
    "no_bin": "bin",
    "bin_full": "bin",
    "camera": "camera",
}
#: Errors that say the part is dirty: with care reminders on, they make
#: the task due at once, and it stays due until marked done (E10). A
#: wheel or contact error is often an obstacle or the dock's position,
#: so those only point to the guide and go with the error.
DIRT_TASKS = frozenset({"filter", "brushes", "side_brush", "cliff_sensors"})
#: The entries a care task can already have, by its name.
_TASK_ITEM_PARTS: dict[str, tuple[str, ...]] = {"cliff_sensors": ("cliff_sensors", "sensor")}


#: What the event entity can report.
EVENT_TYPES: tuple[str, ...] = ("due", "overdue", "done", "snoozed")

#: A replacement is overdue a week after it was first seen due.
OVERDUE_AFTER = timedelta(days=7)
#: How often the entries are recomputed without anything else asking.
REFRESH_INTERVAL = timedelta(minutes=15)
#: A last-done time that moves by less than this is the same act read
#: back: a reset recorded here, then the cloud's own stamp for it.
_SAME_ACT = timedelta(minutes=10)
#: How long a "check" entry's announcement is remembered after the
#: robot stops reporting its error.
_CHECK_MEMORY = timedelta(hours=12)
#: The wear-rate estimate needs this many days since the last reset.
_MIN_RATE_DAYS = 3

#: A replacement and the cleaning it implies: a new filter is a clean
#: filter. Replacing records both; only the replacement is announced.
REPLACE_CLEANS: dict[str, str] = {
    "filter": "filter",
    "main_brush": "brushes",
    "side_brush": "side_brush",
    "sensor": "cliff_sensors",
}

#: Count types to the unit an entry reports. Minutes are hours, as in
#: the app and on the part sensors (part_count_in_display_unit).
_COUNT_UNITS: dict[str, str] = {
    "minutes": "hours",
    "hr": "hours",
    "hours": "hours",
    "missions": "missions",
    "routines": "missions",
    "combo_missions": "missions",
    "evacs": "evacuations",
    "evacuations": "evacuations",
    "pad_washes_used": "pad_washes",
    "padwashesused": "pad_washes",
    "battery": "charge_cycles",
    "sqft": "sqft",
}

#: The legacy reset method and logbook component per Classic role.
_CLASSIC_RESET: dict[str, str] = {
    IROBOT_PART_ROLE_FILTER: "filter",
    IROBOT_PART_ROLE_MAIN_BRUSH: "brush",
    IROBOT_PART_ROLE_SIDE_BRUSH: "side_brush",
    IROBOT_PART_ROLE_CLEAN_BASE_BAG: "clean_base_bag",
    "pad": "pad",
    "battery": "battery",
}


@dataclass(frozen=True)
class MaintenanceItem:
    """One part or care task -- see the module docstring."""

    id: str
    action: str
    part: str
    level: str = LEVEL_OK
    used: float | None = None
    remaining: float | None = None
    unit: str | None = None
    due: str | None = None
    interval: float | None = None
    interval_unit: str | None = None
    interval_source: str | None = None
    last_done: str | None = None
    wear_rate: float | None = None
    part_id: str | None = None
    guide_url: str | None = None
    buy_url: str | None = None
    hint: str | None = None
    done: str = "local"
    snoozed_until: str | None = None
    #: The robot error that points to this entry, while it is reported.
    error: int | None = None
    #: Or the other reason that does: "bin_full", "no_bin", "cliff",
    #: "brush_stall", "camera" (NOT_READY_REASONS).
    reason: str | None = None
    #: Our care guide for this entry (care_guides.py), if there is one.
    guide_id: str | None = None
    #: iRobot's article for `error` on this model (4.5.0b4): the list's
    #: link beside ours, and in the event for a notification.
    help_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Levels ──────────────────────────────────────────────────────────────────

def soon_share(options: Mapping[str, Any]) -> float | None:
    """The share of an interval from which a part is "soon", or None."""
    value = str(options.get(CONF_MAINTENANCE_SOON, DEFAULT_MAINTENANCE_SOON))
    return MAINTENANCE_SOON_SHARES.get(value, MAINTENANCE_SOON_SHARES[DEFAULT_MAINTENANCE_SOON])


def replace_level(
    used: float | None,
    due_since: datetime | None,
    now: datetime,
    soon: float | None,
) -> str:
    """ok / soon / due / overdue for a part to replace."""
    if used is None:
        return LEVEL_OK
    if used >= 1.0:
        if due_since is not None and now - due_since >= OVERDUE_AFTER:
            return LEVEL_OVERDUE
        return LEVEL_DUE
    if soon is not None and used >= soon:
        return LEVEL_SOON
    return LEVEL_OK


def clean_level(due: datetime, interval_days: float, now: datetime) -> str:
    """ok / due / overdue for a care task: due on its date, overdue
    after half an interval more."""
    if now < due:
        return LEVEL_OK
    if now >= due + timedelta(days=interval_days / 2):
        return LEVEL_OVERDUE
    return LEVEL_DUE


def overall_level(items: Iterable[MaintenanceItem], now: datetime | None = None) -> str:
    """The worst level of the entries that are not snoozed."""
    now = now or dt_util.now()
    worst = LEVEL_OK
    for item in items:
        until = _parse(item.snoozed_until)
        if until is not None and until > now:
            continue
        if _RANK[item.level] > _RANK[worst]:
            worst = item.level
    return worst


def _due_since(state: Mapping[str, Mapping[str, Any]], item_id: str) -> datetime | None:
    return _parse((state.get(item_id) or {}).get("due_since"))


def _snoozed(state: Mapping[str, Mapping[str, Any]], item_id: str) -> str | None:
    value = (state.get(item_id) or {}).get("snoozed_until")
    return value if isinstance(value, str) and value else None


# ── Classic ─────────────────────────────────────────────────────────────────

def _catalogue_for_role(
    catalogue: Mapping[str, Mapping[str, str]], role: str, part_id: str | None,
) -> Mapping[str, str]:
    """The catalogue entry for a role: by the counter's part id where
    there is one, else the part whose name names the role."""
    if part_id is not None and part_id in catalogue:
        return catalogue[part_id]
    wanted = "bag" if role == IROBOT_PART_ROLE_CLEAN_BASE_BAG else role
    for entry in catalogue.values():
        if prime_part_role(None, entry.get("part_name")) == wanted:
            return entry
    return {}


def active_error(status: Any) -> int | None:
    """The error a robot reports now, from its `cleanMissionStatus`.

    The same rule as the error sensors: the firmware keeps the last
    error after the robot docks, so an error with no mission running and
    the robot at rest is over.
    """
    if not isinstance(status, Mapping):
        return None
    if status.get("cycle", "") == "none" and status.get("phase", "") in (
        "charge", "stop", "idle", "",
    ):
        return None
    try:
        code = int(status.get("error") or 0)
    except (TypeError, ValueError):
        return None
    return code or None


def care_cause(state: Mapping[str, Any]) -> int | str | None:
    """What a Classic robot reports now that points to a care task: an
    error from the tables (int), else a refusal to start or a full bin
    (the reason's name). A full bin counts only without a Clean Base --
    with one, the base empties it."""
    error = active_error(state.get("cleanMissionStatus"))
    if error is not None and error in ERROR_CARE_TASKS_CLASSIC:
        return error
    status = state.get("cleanMissionStatus")
    if isinstance(status, Mapping):
        reason = NOT_READY_REASONS.get(decode_not_ready(status.get("notReady")) or 0)
        if reason is not None:
            return reason
    bin_state = state.get("bin")
    if (
        isinstance(bin_state, Mapping) and bin_state.get("full") is True
        and not has_clean_base(dict(state))
    ):
        return "bin_full"
    return error


def cause_task(cause: int | str | None, *, prime: bool) -> str | None:
    """The care task a cause points to."""
    if isinstance(cause, str):
        return REASON_CARE_TASKS.get(cause)
    if cause is None:
        return None
    table = ERROR_CARE_TASKS_PRIME if prime else ERROR_CARE_TASKS_CLASSIC
    return table.get(cause)


def apply_error(
    items: list[MaintenanceItem],
    store: MaintenanceStore,
    error: int | str | None,
    options: Mapping[str, Any],
    *,
    prime: bool,
) -> list[MaintenanceItem]:
    """The entries with the robot's current error applied (E10, E12).

    At most one entry per care task: the task's own entry carries the
    error and is due while it lasts; without one, a "check" entry
    stands for it. An error marked done stays out until the robot stops
    reporting it.
    """
    task = cause_task(error, prime=prime)
    if task is None:
        return items
    code = error if isinstance(error, int) else None
    reason = error if isinstance(error, str) else None
    out = list(items)
    for index, item in enumerate(out):
        if item.action == ACTION_CLEAN and item.part in _TASK_ITEM_PARTS.get(task, (task,)):
            dismissed = (store.maintenance_state.get(item.id) or {}).get("error_dismissed")
            if dismissed == error:
                return out
            level = item.level if _RANK[item.level] >= _RANK[LEVEL_DUE] else LEVEL_DUE
            out[index] = replace(item, error=code, reason=reason, level=level)
            return out
    if options.get(CONF_CARE_GUIDE_ON_ERROR, DEFAULT_CARE_GUIDE_ON_ERROR) != "list":
        return out
    item_id = f"{ACTION_CHECK}.{task}"
    if (store.maintenance_state.get(item_id) or {}).get("error_dismissed") == error:
        return out
    out.append(MaintenanceItem(
        id=item_id,
        action=ACTION_CHECK,
        part=task,
        level=LEVEL_DUE,
        last_done=getattr(store, f"{task}_cleaned_at", None),
        hint=f"{ACTION_CHECK}.{task}",
        done="local" if hasattr(store, f"reset_{task}_cleaning") else "none",
        error=code,
        reason=reason,
    ))
    return out


def classic_items(
    store: MaintenanceStore,
    state: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    catalogue: Mapping[str, Mapping[str, str]] | None = None,
    wear_rate: Callable[[int, str | None, int], float | None] | None = None,
    has_cloud_parts: bool = False,
    now: datetime | None = None,
) -> list[MaintenanceItem]:
    """Every entry of a Classic robot.

    `wear_rate(reset_hr, reset_at, current_hr)` gives runtime hours per
    day since a reset (MissionStore.wear_rate_since_reset); without it
    there is no due date for the parts.
    """
    now = now or dt_util.now()
    catalogue = catalogue or {}
    current_hr = lifetime_hours(state)
    soon = soon_share(options)
    braava = is_braava(dict(state))
    items: list[MaintenanceItem] = []
    mstate = store.maintenance_state

    for role, spec in CONSUMABLE_ROLES.items():
        if spec.absent_when is not None and spec.absent_when(dict(state)):
            continue
        if braava and role in (IROBOT_PART_ROLE_FILTER, IROBOT_PART_ROLE_SIDE_BRUSH):
            continue
        part = "pad" if braava and role == IROBOT_PART_ROLE_MAIN_BRUSH else role
        item_id = f"{ACTION_REPLACE}.{part}"
        used, remaining, interval, source = store.role_usage(role, current_hr, options)
        reset_hr, reset_at = store.reset_baseline_for_role(role)
        rate = wear_rate(reset_hr, reset_at, current_hr) if wear_rate else None
        due_since = _due_since(mstate, item_id)
        level = replace_level(used, due_since, now, soon)
        due: datetime | None = None
        if level in (LEVEL_DUE, LEVEL_OVERDUE):
            due = due_since or now
        elif rate:
            due = now + timedelta(days=remaining / rate)
        record = store.cloud_part_by_role(role)
        part_id = str(record["part_id"]) if record and record.get("part_id") else None
        links = _catalogue_for_role(catalogue, role, part_id)
        items.append(MaintenanceItem(
            id=item_id,
            action=ACTION_REPLACE,
            part=part,
            level=level,
            used=round(used, 3) if used is not None else None,
            remaining=float(int(remaining)),
            unit="hours",
            due=due.isoformat() if due else None,
            interval=round(interval, 1),
            interval_unit="hours",
            interval_source=source,
            last_done=reset_at,
            wear_rate=rate,
            part_id=part_id,
            guide_url=links.get("guide_url"),
            buy_url=links.get("buy_url"),
            hint=f"{ACTION_REPLACE}.{part}",
            done="both" if has_cloud_parts and part_id else "local",
            snoozed_until=_snoozed(mstate, item_id),
        ))

    # The battery has no interval: its entry is there so a new pack can
    # be recorded, and it is never due.
    items.append(MaintenanceItem(
        id=f"{ACTION_REPLACE}.battery",
        action=ACTION_REPLACE,
        part="battery",
        last_done=store.battery_reset_at,
        hint=f"{ACTION_REPLACE}.battery",
        snoozed_until=_snoozed(mstate, f"{ACTION_REPLACE}.battery"),
    ))

    if has_cloud_parts and not braava:
        pad = _cloud_counted_pad(store, catalogue, soon=soon, now=now)
        if pad is not None:
            items.append(pad)

    if care_reminders_enabled(options):
        items.extend(care_items(
            store, options, braava=braava, prime=False, now=now,
        ))
    return apply_error(
        items, store, care_cause(state), options, prime=False,
    )


def _cloud_counted_pad(
    store: MaintenanceStore,
    catalogue: Mapping[str, Mapping[str, str]],
    *,
    soon: float | None,
    now: datetime,
) -> MaintenanceItem | None:
    """The washable mop pad of a Classic Combo, as iRobot's cloud counts it.

    @jamd-pt's Combo j9+ (c975840, local): part 135
    `care_cleaning_pad_dmc`, counted in missions, was in the cloud parts
    and in the diagnostics, but no entry showed it. A Classic entry names
    a pad only on a Braava (its main brush slot), and the cloud's counted
    parts became entries only on a Prime robot.

    The pad keeps the cloud's own count, like a Prime part, rather than a
    role slot: those are runtime minutes on the local hour baseline, and
    a count of missions read as minutes would be wrong there. Marked done,
    it resets that counter in the cloud (`_async_cloud_reset`).
    """
    for pid, record in store.cloud_parts.items():
        entry = catalogue.get(str(pid), {})
        if prime_part_role(pid, entry.get("part_name")) != "pad":
            continue
        if str(record.get("counter_category") or "").lower() == "maintenance":
            continue
        return _counted_part_item(
            store, f"{ACTION_REPLACE}.pad", str(pid), "pad", ACTION_REPLACE,
            SimpleNamespace(**record), entry, soon=soon, now=now,
        )
    return None


def _forced(store: MaintenanceStore, item_id: str, level: str) -> str:
    """A care task a dirt error made due stays due until it is done."""
    st = store.maintenance_state.get(item_id) or {}
    if st.get("forced_due") and _RANK[level] < _RANK[LEVEL_DUE]:
        return LEVEL_DUE
    return level


def care_items(
    store: MaintenanceStore,
    options: Mapping[str, Any],
    *,
    braava: bool,
    prime: bool,
    skip: Iterable[str] = (),
    now: datetime | None = None,
) -> list[MaintenanceItem]:
    """The calendar care tasks, for either generation. `skip` names
    tasks the robot counts itself (a Prime robot's cliff sensors)."""
    now = now or dt_util.now()
    pets = bool(options.get(CONF_PETS, DEFAULT_PETS))
    skipped = set(skip)
    out: list[MaintenanceItem] = []
    for task in CARE_TASKS:
        if (braava and not task.braava) or task.slot in skipped:
            continue
        due = store.care_due(task, pets, prime)
        if due is None:
            continue
        days = task.interval_days(pets, prime)
        item_id = f"{ACTION_CLEAN}.{task.slot}"
        left = (due - now).total_seconds() / 86400
        out.append(MaintenanceItem(
            id=item_id,
            action=ACTION_CLEAN,
            part=task.slot,
            level=_forced(store, item_id, clean_level(due, days, now)),
            used=round(max(0.0, 1 - left / days), 3) if days else None,
            remaining=round(max(0.0, left), 1),
            unit="days",
            due=due.isoformat(),
            interval=days,
            interval_unit="days",
            interval_source="calendar",
            last_done=getattr(store, f"{task.slot}_cleaned_at", None),
            hint=f"{ACTION_CLEAN}.{task.slot}",
            snoozed_until=_snoozed(store.maintenance_state, item_id),
        ))
    return out


# ── Prime ───────────────────────────────────────────────────────────────────

def _epoch_iso(ts: Any) -> str | None:
    try:
        epoch = int(ts)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return dt_util.utc_from_timestamp(epoch).astimezone(dt_util.DEFAULT_TIME_ZONE).isoformat()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _counted_part_item(
    store: MaintenanceStore,
    item_id: str,
    pid: str,
    role: str | None,
    action: str,
    part: Any,
    entry: Mapping[str, str],
    *,
    soon: float | None,
    now: datetime,
) -> MaintenanceItem:
    """The entry of one part iRobot's cloud counts itself.

    Shared by every Prime part and by the counted parts of a Classic
    robot iRobot also counts in the cloud (its washable pad, 4.6.12).
    Marking it done resets that counter in the cloud (`done` "cloud").
    """
    mstate = store.maintenance_state
    count_type = str(getattr(part, "count_type", "") or "").lower()
    used_n = _number(getattr(part, "count_used", None))
    left_n = _number(getattr(part, "count_remaining", None))
    counter = _number(getattr(part, "counter", None))
    total = (used_n + left_n) if used_n is not None and left_n is not None else None
    if counter is not None:
        used = counter / 100
    elif total:
        used = (used_n or 0.0) / total
    else:
        used = None
    if action == ACTION_CLEAN:
        level = LEVEL_OK
    else:
        level = replace_level(used, _due_since(mstate, item_id), now, soon)
        if needs_attention(part) and _RANK[level] < _RANK[LEVEL_DUE]:
            level = LEVEL_DUE
    remaining = part_count_in_display_unit(part, int(left_n)) if left_n is not None else None
    interval = part_count_in_display_unit(part, int(total)) if total is not None else None
    last_done = _epoch_iso(getattr(part, "last_updated_ts", None))
    rate: float | None = None
    due: datetime | None = None
    if level in (LEVEL_DUE, LEVEL_OVERDUE):
        due = _due_since(mstate, item_id) or now
    elif last_done and used_n and remaining is not None:
        started = _parse(last_done)
        days = (now - started).total_seconds() / 86400 if started else 0
        served = part_count_in_display_unit(part, int(used_n)) or 0
        if days >= _MIN_RATE_DAYS and served > 0:
            rate = round(served / days, 2)
            due = now + timedelta(days=remaining / rate)
    return MaintenanceItem(
        id=item_id,
        action=action,
        part=role or f"part_{pid}",
        level=level,
        used=round(used, 3) if used is not None else None,
        remaining=float(remaining) if remaining is not None else None,
        unit=_COUNT_UNITS.get(count_type),
        due=due.isoformat() if due else None,
        interval=float(interval) if interval is not None else None,
        interval_unit=_COUNT_UNITS.get(count_type),
        interval_source="cloud" if interval is not None else None,
        last_done=last_done,
        wear_rate=rate,
        part_id=pid,
        guide_url=entry.get("guide_url"),
        buy_url=entry.get("buy_url"),
        hint=f"{action}.{role}" if role else None,
        done="cloud",
        snoozed_until=_snoozed(mstate, item_id),
    )


def prime_items(
    store: MaintenanceStore,
    parts: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    catalogue: Mapping[str, Mapping[str, str]] | None = None,
    absent: Iterable[str] = (),
    error: int | None = None,
    now: datetime | None = None,
) -> list[MaintenanceItem]:
    """Every entry of a Prime robot: one per part it reports, plus the
    calendar care tasks with care reminders on.

    THE ROBOT'S COUNTERS ARE THE INTERVAL. `counter` is percent used;
    a part that reports none is measured by used / (used + remaining).

    A "maintenance" part is never due here. Its counter resets when
    the job is done, so zero means just done, and whether it ever
    climbs to say a job IS due is unknown (prime_parts.needs_attention).
    It is listed, so it can be marked done, and stays "ok".
    """
    now = now or dt_util.now()
    catalogue = catalogue or {}
    soon = soon_share(options)
    absent_ids = {str(a) for a in absent}

    rows: list[tuple[str, str | None, str, Any, Mapping[str, str]]] = []
    for part_id, part in parts.items():
        pid = str(part_id)
        if pid in absent_ids:
            continue
        entry = catalogue.get(pid, {})
        role = prime_part_role(pid, entry.get("part_name"))
        if role is None and (getattr(part, "count_type", "") or "").lower() == "battery":
            role = "battery"
        category = str(getattr(part, "counter_category", "") or "").lower()
        action = ACTION_CLEAN if category == "maintenance" else ACTION_REPLACE
        rows.append((pid, role, action, part, entry))

    # Named by role where the role is unique for its action, by number
    # otherwise -- two parts sharing a name must not share an entry.
    taken: dict[tuple[str, str], int] = {}
    for _pid, role, action, _part, _entry in rows:
        if role is not None:
            taken[(action, role)] = taken.get((action, role), 0) + 1

    items: list[MaintenanceItem] = []
    for pid, role, action, part, entry in rows:
        name = role if role is not None and taken[(action, role)] == 1 else f"part_{_name_key(pid)}"
        items.append(_counted_part_item(
            store, f"{action}.{name}", pid, role, action, part, entry, soon=soon, now=now,
        ))

    if care_reminders_enabled(options):
        # A robot that counts its cliff sensors itself gets no calendar
        # reminder for them beside its own counter.
        own = {"cliff_sensors"} if any(i.part == "sensor" for i in items) else set()
        items.extend(care_items(store, options, braava=False, prime=True, skip=own, now=now))
    return apply_error(items, store, error, options, prime=True)


# ── The tracker ─────────────────────────────────────────────────────────────

class MaintenanceTracker:
    """Keeps a robot's maintenance entries current -- see the module
    docstring. One per config entry, on `runtime_data.maintenance`."""

    def __init__(self, hass: HomeAssistant, config_entry: RoombaConfigEntry) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self._items: list[MaintenanceItem] = []
        self._via: dict[str, str] = {}
        #: The store changed outside maintenance_state (a cloud poll's
        #: counters) and is saved with the next recompute.
        self._dirty = False
        self._unsubs: list[CALLBACK_TYPE] = []
        #: The cause last seen, to recompute when it changes.
        self._seen_error: int | str | None = None
        #: The error issue closed from its fix flow, as (task, cause):
        #: not raised again until the robot reports something else.
        self._closed_error: tuple[str, int | str] | None = None

    # ── reading ──

    @property
    def _data(self) -> Any:
        return self.config_entry.runtime_data

    @property
    def _store(self) -> MaintenanceStore | None:
        return getattr(self._data, "maintenance_store", None)

    @property
    def _prime(self) -> bool:
        return getattr(self._data, "connection_type", None) is ConnectionType.CLOUD_ONLY

    @property
    def items(self) -> list[MaintenanceItem]:
        return list(self._items)

    def item(self, item_id: str) -> MaintenanceItem | None:
        return next((i for i in self._items if i.id == item_id), None)

    def item_for_part(self, part: str) -> MaintenanceItem | None:
        """The replacement entry of a role or Prime part id, for the
        part sensors."""
        for item in self._items:
            if item.action == ACTION_REPLACE and part in (item.part, item.part_id):
                return item
        for item in self._items:
            if item.part_id == part:
                return item
        return None

    @property
    def level(self) -> str:
        return overall_level(self._items)

    def prime_items_now(self) -> None:
        """Fill the entries without announcing or saving anything -- for
        the entities created before the first refresh."""
        self._items = self.compute()

    def compute(self, now: datetime | None = None) -> list[MaintenanceItem]:
        """The entries from the robot's current data, without side
        effects, each with its care guide."""
        return [
            replace(
                item,
                guide_id=guide_id(item.action, item.part),
                help_url=(
                    help_url_for(self._data, item.error, prime=self._prime)
                    if item.error is not None
                    else None
                ),
            )
            for item in self._compute(now)
        ]

    def _compute(self, now: datetime | None) -> list[MaintenanceItem]:
        store = self._store
        if store is None:
            return []
        data = self._data
        options = self.config_entry.options
        catalogue = getattr(data, "parts_catalogue", None) or {}
        if self._prime:
            coordinator = getattr(data, "prime_parts_coordinator", None)
            parts = getattr(coordinator, "data", None)
            return prime_items(
                store,
                parts if isinstance(parts, dict) else {},
                options,
                catalogue=catalogue,
                absent=parts_the_robot_cannot_have(self.config_entry),
                error=self.current_error(),
                now=now,
            )
        mission_store = getattr(data, "mission_store", None)
        return classic_items(
            store,
            data.roomba_reported_state(),
            options,
            catalogue=catalogue,
            wear_rate=getattr(mission_store, "wear_rate_since_reset", None),
            has_cloud_parts=getattr(data, "cloud_coordinator", None) is not None,
            now=now,
        )

    def current_error(self) -> int | None:
        """The error the robot reports now, on either generation."""
        if self._prime:
            coordinator = getattr(self._data, "prime_status_coordinator", None)
            shadows = getattr(coordinator, "data", None)
            current = shadows.get("ro-currentstate") if isinstance(shadows, dict) else None
            status = current.get("cleanMissionStatus") if isinstance(current, dict) else None
            return active_error(status)
        return active_error(self._data.roomba_reported_state().get("cleanMissionStatus"))

    def current_cause(self) -> int | str | None:
        """What the robot reports now that may point to a care task: its
        error, or on Classic a refusal to start or a full bin."""
        if self._prime:
            return self.current_error()
        return care_cause(self._data.roomba_reported_state())

    def care_cause_task(self) -> str | None:
        """The care task the robot's current error or state points to, if
        any -- for the vacuum's `care_task` attribute (H)."""
        return cause_task(self.current_cause(), prime=self._prime)

    def _note_error(self, store: MaintenanceStore, now: datetime) -> None:
        """What the current error leaves behind (E10): a dirt error makes
        its care task due until it is done, with care reminders on; an
        error marked done stays out only while it lasts."""
        error = self.current_cause()
        task = cause_task(error, prime=self._prime)
        for st in store.maintenance_state.values():
            if st.get("error_dismissed") is not None and st["error_dismissed"] != error:
                st.pop("error_dismissed")
        if (
            isinstance(error, int)
            and task in DIRT_TASKS
            and (self._prime or error not in CLASSIC_HINT_ONLY_ERRORS)
            and care_reminders_enabled(self.config_entry.options)
            and store.care_since is not None
        ):
            st = store.maintenance_state.setdefault(f"{ACTION_CLEAN}.{task}", {})
            if st.get("error_dismissed") != error:
                st.setdefault("forced_due", now.isoformat())

    # ── keeping current ──

    @callback
    def async_start(self) -> None:
        """Listen for everything that moves an entry. The entry's unload
        stops it (`async_stop`)."""
        data = self._data
        blid = data.blid
        self._unsubs.append(async_track_time_interval(
            self.hass, self._on_tick, REFRESH_INTERVAL,
        ))
        self._unsubs.append(async_dispatcher_connect(
            self.hass, maintenance_changed_signal(blid), self._schedule,
        ))
        cloud = getattr(data, "cloud_coordinator", None)
        if cloud is not None and not self._prime:
            self._unsubs.append(cloud.async_add_listener(self._on_cloud_parts))
        parts = getattr(data, "prime_parts_coordinator", None)
        if parts is not None and self._prime:
            self._unsubs.append(parts.async_add_listener(self._schedule))
        # AN ERROR RECOMPUTES AT ONCE: its "check" entry and its repair
        # issue should not wait for the next quarter hour.
        status = getattr(data, "prime_status_coordinator", None)
        if status is not None and self._prime:
            self._unsubs.append(status.async_add_listener(self._on_status))
        roomba = getattr(data, "roomba", None)
        register = getattr(roomba, "register_on_message_callback", None)
        if register is not None and not self._prime:
            unsub = register(self._on_robot_message)
            if callable(unsub):
                self._unsubs.append(unsub)
        self._seen_error = self.current_cause()
        self._schedule()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._schedule()

    @callback
    def _on_status(self) -> None:
        error = self.current_cause()
        if error != self._seen_error:
            self._seen_error = error
            self._schedule()

    @callback
    def _on_robot_message(self, _message: Any) -> None:
        """A Classic robot's message. On the event loop since roombapy 2.x,
        so called directly (4.5.0; it went through call_soon_threadsafe)."""
        self._on_status()

    @callback
    def _schedule(self) -> None:
        self.config_entry.async_create_task(
            self.hass, self._safe_refresh(), name="roomba_plus_maintenance_refresh",
        )

    @callback
    def _on_cloud_parts(self) -> None:
        """A Classic cloud poll: adopt its counters, then recompute.

        Until 4.5.0 the counters were adopted once, at setup, so a part
        replaced in the iRobot app showed as worn until a restart."""
        store = self._store
        cloud = getattr(self._data, "cloud_coordinator", None)
        parts = getattr(cloud, "parts", None)
        if store is not None and parts:
            hr = lifetime_hours(self._data.roomba_reported_state())
            if store.hydrate_from_cloud_parts(parts, hr):
                self._dirty = True
                self._via.setdefault("*", "irobot")
        self._schedule()

    async def _safe_refresh(self) -> None:
        try:
            await self.async_refresh()
        except Exception:  # noqa: BLE001 -- a background recompute must not raise unseen
            _LOGGER.exception("Roomba+: could not update the maintenance entries")

    async def async_refresh(self, now: datetime | None = None) -> None:
        """Recompute, note what changed, announce it, save if needed."""
        store = self._store
        if store is None:
            return
        now = now or dt_util.now()
        before = copy.deepcopy(store.maintenance_state)
        self._note_error(store, now)
        items = self.compute(now)
        events: list[tuple[str, MaintenanceItem]] = []
        done_now: set[str] = set()

        for item in items:
            st = store.maintenance_state.setdefault(item.id, {})
            # Done when last_done moved on since the entry was last seen
            # -- the first replacement ever included. An entry seen for
            # the first time (an upgrade, a new part) only records it.
            previous = _parse(st.get("last_done"))
            current = _parse(item.last_done)
            if "last_done" in st and current is not None and (
                previous is None or current - previous > _SAME_ACT
            ):
                done_now.add(item.id)
                for key in ("due_since", "notified", "snoozed_until", "forced_due"):
                    st.pop(key, None)
            if item.last_done or "last_done" not in st:
                st["last_done"] = item.last_done

        # A replacement that also counts as a cleaning is announced once.
        cleaned_by = {
            f"{ACTION_CLEAN}.{REPLACE_CLEANS[i.part]}"
            for i in items
            if i.id in done_now and i.action == ACTION_REPLACE and i.part in REPLACE_CLEANS
        }
        for item in items:
            if item.id in done_now and item.id not in cleaned_by:
                via = self._via.pop(item.id, None) or self._via.get("*") or "other"
                self._log(item.id, item.action, via, now)
                events.append(("done", item))
        self._via.pop("*", None)

        # The levels, with the due_since just cleared or set.
        items = self.compute(now)
        for item in items:
            st = store.maintenance_state.setdefault(item.id, {})
            if item.level in (LEVEL_DUE, LEVEL_OVERDUE):
                st.setdefault("due_since", now.isoformat())
            else:
                st.pop("due_since", None)
                st.pop("notified", None)
            until = _parse(st.get("snoozed_until"))
            if until is not None and until <= now:
                st.pop("snoozed_until", None)
                until = None
            if (
                item.level in (LEVEL_DUE, LEVEL_OVERDUE)
                and until is None
                and st.get("notified") != item.level
            ):
                st["notified"] = item.level
                events.append((item.level, item))
        # The state of entries the robot no longer has goes with them --
        # except a "check" entry's for half a day: an error that goes and
        # comes back (a robot restarted, set back on its dock) is one
        # error, announced once.
        known = {i.id for i in items}
        for item in items:
            if item.action == ACTION_CHECK:
                store.maintenance_state[item.id]["seen_at"] = now.isoformat()
        for stale in [k for k in store.maintenance_state if k not in known]:
            seen = _parse(store.maintenance_state[stale].get("seen_at"))
            if seen is not None and now - seen < _CHECK_MEMORY:
                continue
            del store.maintenance_state[stale]
        for key in [k for k, v in store.maintenance_state.items() if not v]:
            del store.maintenance_state[key]

        items = self.compute(now)
        changed = [i.as_dict() for i in items] != [i.as_dict() for i in self._items]
        self._items = items
        if store.maintenance_state != before or events or self._dirty:
            self._dirty = False
            await store.async_save(self.hass, self.config_entry.entry_id)
        blid = self._data.blid
        if changed:
            async_dispatcher_send(self.hass, maintenance_items_signal(blid))
        for event_type, item in events:
            self._announce(event_type, item)
        if self._sync_issues(store, items, now):
            await store.async_save(self.hass, self.config_entry.entry_id)

    def _sync_issues(
        self, store: MaintenanceStore, items: list[MaintenanceItem], now: datetime,
    ) -> bool:
        """The repair issues the options ask for (D1, D2); True when the
        stored state changed."""
        options = self.config_entry.options
        changed = False
        overdue: list[MaintenanceItem] = []
        if options.get(CONF_MAINTENANCE_OVERDUE_REPAIRS, DEFAULT_MAINTENANCE_OVERDUE_REPAIRS):
            for item in items:
                st = store.maintenance_state.get(item.id) or {}
                snoozed = _parse(item.snoozed_until)
                if (
                    item.action == ACTION_REPLACE
                    and item.level == LEVEL_OVERDUE
                    and not (snoozed is not None and snoozed > now)
                    and not st.get("no_repair")
                ):
                    overdue.append(item)
        else:
            # Switching the option off and on again asks about every
            # part again, those answered "never" included.
            for st in store.maintenance_state.values():
                if st.pop("no_repair", None) is not None:
                    changed = True
        error_care: tuple[str, int | str] | None = None
        error = self.current_cause()
        task = cause_task(error, prime=self._prime)
        if self._closed_error is not None and self._closed_error[1] != error:
            self._closed_error = None
        if (
            options.get(CONF_CARE_GUIDE_ON_ERROR, DEFAULT_CARE_GUIDE_ON_ERROR) == "repairs"
            and task is not None
            and error is not None
            and self._closed_error != (task, error)
            and (store.maintenance_state.get(f"{ACTION_CLEAN}.{task}") or {}).get(
                "error_dismissed"
            ) != error
        ):
            error_care = (task, error)
        async_sync_maintenance_issues(self.hass, self.config_entry, overdue, error_care)
        return changed

    def _log(self, item_id: str, action: str, via: str, now: datetime) -> None:
        store = self._store
        if store is None:
            return
        store.maintenance_log.append({
            "id": item_id, "action": action, "at": now.isoformat(), "via": via,
        })
        del store.maintenance_log[:-MAINTENANCE_LOG_SIZE]

    def _announce(self, event_type: str, item: MaintenanceItem) -> None:
        async_dispatcher_send(
            self.hass,
            maintenance_event_signal(self._data.blid),
            event_type,
            {
                "entry_id": self.config_entry.entry_id,
                "name": self.config_entry.title,
                **item.as_dict(),
                # The signed path of our care guide, for a notification.
                "guide": guide_link(self.hass, self.config_entry.entry_id, item.guide_id),
                # Its title in Home Assistant's language, for the message.
                "guide_title": guide_title(self.hass, item.guide_id),
                # Whether "only cleaned" can be recorded, for a button.
                "can_clean": item.action == ACTION_REPLACE and self.can_mark_cleaned(item),
            },
        )

    # ── acting ──

    def _require(self, item_id: str) -> MaintenanceItem:
        item = self.item(item_id) or next(
            (i for i in self.compute() if i.id == item_id), None
        )
        if item is None:
            raise ServiceValidationError(
                f"No maintenance entry {item_id}",
                translation_domain=DOMAIN,
                translation_key="maintenance_item_unknown",
                translation_placeholders={
                    "item": item_id,
                    "items": ", ".join(i.id for i in self._items) or "-",
                },
            )
        return item

    async def async_done(
        self, item_id: str, action: str | None = None, via: str = "action",
    ) -> None:
        """Mark an entry done -- replaced or cleaned -- and recompute.

        `action` may differ from the entry's own: a part to replace can
        be reported as only cleaned (its counter stays), and a care task
        as its part replaced. Replacing and cleaning stay apart: a
        cleaning never moves a replacement counter.
        """
        item = self._require(item_id)
        action = action or item.action
        if action == item.action or (item.action == ACTION_CHECK and action == ACTION_CLEAN):
            target = item
        elif action == ACTION_CLEAN and item.part in REPLACE_CLEANS:
            target = self._require(f"{ACTION_CLEAN}.{REPLACE_CLEANS[item.part]}")
        elif action == ACTION_REPLACE and (
            replaced := next(
                (
                    i for i in self._items
                    if i.action == ACTION_REPLACE and REPLACE_CLEANS.get(i.part) == item.part
                ),
                None,
            )
        ) is not None:
            target = replaced
        else:
            raise ServiceValidationError(
                f"{item_id} cannot be marked as {action}",
                translation_domain=DOMAIN,
                translation_key="maintenance_action_unsupported",
                translation_placeholders={"item": item_id, "action": str(action)},
            )
        self._via[target.id] = via
        store = self._store
        assert store is not None
        cause = target.error if target.error is not None else target.reason
        if cause is not None:
            # Done while the robot still reports it: it stays out until
            # the robot stops reporting it.
            store.maintenance_state.setdefault(target.id, {})["error_dismissed"] = cause
        if target.action == ACTION_CHECK:
            await self._async_check_done(target, via)
        elif target.done == "cloud":
            await self._async_cloud_reset(target)
        elif target.action == ACTION_CLEAN:
            await self._async_clean_locally(target)
        else:
            await self._async_replace_classic(target)
        await self.async_refresh()

    async def _async_check_done(self, item: MaintenanceItem, via: str) -> None:
        """A "check" entry marked done: a cleaning where the task has one
        and care reminders are on (E10), otherwise only noted -- the
        entry goes either way and comes back with the next error."""
        store = self._store
        assert store is not None
        if item.done == "local" and care_reminders_enabled(self.config_entry.options):
            await self._async_clean_locally(item)
            return
        now = dt_util.now()
        self._via.pop(item.id, None)
        self._log(item.id, item.action, via, now)
        await store.async_save(self.hass, self.config_entry.entry_id)
        self._announce("done", item)

    async def _async_clean_locally(self, item: MaintenanceItem) -> None:
        store = self._store
        assert store is not None
        reset = getattr(store, f"reset_{item.part}_cleaning", None)
        if reset is None:
            raise ServiceValidationError(
                f"{item.id} cannot be marked as cleaned",
                translation_domain=DOMAIN,
                translation_key="maintenance_action_unsupported",
                translation_placeholders={"item": item.id, "action": ACTION_CLEAN},
            )
        reset()
        await store.async_save(self.hass, self.config_entry.entry_id)
        _fire_maintenance_reset_event(self.hass, self.config_entry, item.part, None)
        async_dispatcher_send(self.hass, maintenance_changed_signal(self._data.blid))

    async def _async_replace_classic(self, item: MaintenanceItem) -> None:
        store = self._store
        assert store is not None
        component = _CLASSIC_RESET.get(item.part)
        if component is None:
            raise ServiceValidationError(
                f"{item.id} cannot be marked as replaced",
                translation_domain=DOMAIN,
                translation_key="maintenance_action_unsupported",
                translation_placeholders={"item": item.id, "action": ACTION_REPLACE},
            )
        hours = lifetime_hours(self._data.roomba_reported_state())
        getattr(store, f"reset_{component}")(hours)
        await store.async_save(self.hass, self.config_entry.entry_id)
        if component == "battery":
            await async_forget_old_battery(self.hass, self.config_entry)
        _fire_maintenance_reset_event(self.hass, self.config_entry, component, hours)
        await _async_push_part_reset_to_cloud(self.config_entry, self._data, component)
        async_dispatcher_send(self.hass, maintenance_changed_signal(self._data.blid))

    async def _async_cloud_reset(self, item: MaintenanceItem) -> None:
        """The app's confirmation, sent the same way: the part's counter
        set to zero. A Prime robot through roombapy-prime's
        reset_robot_parts; a Classic robot's cloud-counted part (its
        washable pad, 4.6.12) through the Classic cloud client, as its
        other parts are pushed. The cloud answers with what it applied;
        nothing applied is an error, not a success wearing a 200. On a
        Classic robot too: this entry has no local counter to fall back
        on, so a silent failure would leave it due."""
        robot = getattr(self._data, "prime_robot", None)
        cloud = getattr(self._data, "cloud_coordinator", None)
        if item.part_id is None or (robot is None and cloud is None):
            raise HomeAssistantError(
                "The robot is not connected",
                translation_domain=DOMAIN,
                translation_key="maintenance_reset_failed",
                translation_placeholders={"item": item.id, "error": "not connected"},
            )
        try:
            if robot is not None:
                result = await robot.reset_robot_parts([item.part_id])
            else:
                assert cloud is not None
                result = await cloud.api.set_robot_part_counter(
                    self._data.blid, item.part_id, 0
                )
        except Exception as exc:
            raise HomeAssistantError(
                f"iRobot did not take the reset of {item.id}: {exc}",
                translation_domain=DOMAIN,
                translation_key="maintenance_reset_failed",
                translation_placeholders={"item": item.id, "error": str(exc)},
            ) from exc
        if not isinstance(result, Mapping) or not result.get("num_parts"):
            _LOGGER.warning(
                "Roomba+: iRobot accepted the reset of part %s but applied none: %s",
                item.part_id, result,
            )
            raise HomeAssistantError(
                f"iRobot applied no part for {item.id}",
                translation_domain=DOMAIN,
                translation_key="maintenance_reset_not_applied",
                translation_placeholders={"item": item.id},
            )
        coordinator = getattr(self._data, "prime_parts_coordinator", None)
        if coordinator is not None:
            await coordinator.async_refresh()
        elif cloud is not None:
            await cloud.async_request_refresh()

    async def async_forget_learned(self, item_ids: Iterable[str] = ()) -> list[str]:
        """Forget the learned intervals of the named parts, or of every
        part of the robot; returns the entries that had one (4.5.0).

        Classic only: a Prime robot's intervals are its own counters and
        nothing here learns them."""
        store = self._store
        assert store is not None
        wanted = list(item_ids)
        parts = [
            item for item in (self._items or self.compute())
            if item.action == ACTION_REPLACE and item.done != "cloud"
            and (item.part == "pad" or item.part in CONSUMABLE_ROLES)
        ]
        if wanted:
            known = {item.id for item in parts}
            unknown = [i for i in wanted if i not in known]
            if unknown:
                raise ServiceValidationError(
                    f"No learned interval for {', '.join(unknown)}",
                    translation_domain=DOMAIN,
                    translation_key="maintenance_action_unsupported",
                    translation_placeholders={"item": ", ".join(unknown), "action": "forget"},
                )
            parts = [item for item in parts if item.id in wanted]
        forgotten = [
            item.id for item in parts
            if store.forget_learned("main_brush" if item.part == "pad" else item.part)
        ]
        if forgotten:
            await store.async_save(self.hass, self.config_entry.entry_id)
            async_dispatcher_send(self.hass, maintenance_changed_signal(self._data.blid))
        return forgotten

    def can_mark_cleaned(self, item: MaintenanceItem) -> bool:
        """Whether "only cleaned" is something this entry can record."""
        if item.action != ACTION_REPLACE:
            return item.done != "none"
        twin = REPLACE_CLEANS.get(item.part)
        return twin is not None and self.item(f"{ACTION_CLEAN}.{twin}") is not None

    async def async_close_error_issue(self, task: str) -> None:
        """The error issue closed: not raised again for this error."""
        error = self.current_cause()
        if error is not None:
            self._closed_error = (task, error)
        await self.async_refresh()

    async def async_never_repair(self, item_id: str) -> None:
        """No overdue issue for this part again -- until the option is
        switched off and on."""
        item = self._require(item_id)
        store = self._store
        assert store is not None
        store.maintenance_state.setdefault(item.id, {})["no_repair"] = True
        await store.async_save(self.hass, self.config_entry.entry_id)
        await self.async_refresh()

    async def async_snooze(self, item_id: str, days: float) -> None:
        """No reminder for this entry for `days` days."""
        item = self._require(item_id)
        store = self._store
        assert store is not None
        until = dt_util.now() + timedelta(days=days)
        st = store.maintenance_state.setdefault(item.id, {})
        st["snoozed_until"] = until.isoformat()
        st.pop("notified", None)
        await store.async_save(self.hass, self.config_entry.entry_id)
        self._items = self.compute()
        async_dispatcher_send(self.hass, maintenance_items_signal(self._data.blid))
        self._announce("snoozed", self.item(item.id) or item)

    def diagnostics(self) -> dict[str, Any]:
        """The entries, their tracked state and the done log."""
        store = self._store
        return {
            "level": self.level,
            "items": [i.as_dict() for i in self._items],
            "state": copy.deepcopy(store.maintenance_state) if store else {},
            "log": list(store.maintenance_log) if store else [],
        }
