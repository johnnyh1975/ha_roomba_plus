"""Sensor platform for Roomba+.

Sensors are arranged in four logical groups that determine their visibility
and placement on the HA device page:

GROUP 1 — Primary Status (EntityCategory = None)
  Appears under "Sensoren" alongside the vacuum control.
  Always visible, daily-use values: battery, phase, error.

GROUP 2 — Operational (EntityCategory.DIAGNOSTIC, enabled)
  Appears under "Diagnose". Useful for automations and troubleshooting.

GROUP 3 — Maintenance (EntityCategory.DIAGNOSTIC, enabled)
  Filter/brush life and battery wear — actionable values.

GROUP 4 — Statistics (EntityCategory.DIAGNOSTIC, enabled)
  Mission counters and timing — informational.

GROUP 5 — Opt-in (EntityCategory.DIAGNOSTIC, disabled)
  Hidden until user explicitly enables.

DEVICE-SPECIFIC (capability-gated)
  Created only when the robot reports the relevant hardware.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import StateType
import datetime
import datetime as dt_stdlib

from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import (
    CARPET_BOOST_LABELS,
    CLEAN_BASE_LABELS,
    CLEAN_MODE_LABELS,
    CONF_BRUSH_HOURS,
    CONF_FILTER_HOURS,
    DEFAULT_BRUSH_HOURS,
    DEFAULT_FILTER_HOURS,
    ERROR_CATALOGUE,
    ERROR_CODE_LABELS,
    JOB_INITIATOR_LABELS,
    MOP_RANK_LABELS,
    NOT_READY_LABELS,
    PAD_LABELS,
    PHASE_LABELS,
    has_carpet_boost,
    has_clean_base,
    has_pose,
    has_smart_map,
    is_mop,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .cloud_coordinator import IrobotCloudCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RoombaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[IRobotEntity], StateType]
    filter_fn: Callable[[dict[str, Any]], bool] = field(
        default_factory=lambda: lambda _: True
    )
    # v1.7.0 L2 — when set, exposed as "threshold_hours" in extra_state_attributes
    # Used by the Lovelace card to compute remaining % without hard-coded thresholds.
    threshold_fn: Callable[[IRobotEntity], int | None] = field(
        default_factory=lambda: lambda _: None
    )


def _carpet_boost_mode(entity: IRobotEntity) -> str:
    vac_high = entity.vacuum_state.get("vacHigh")
    carpet_boost = entity.vacuum_state.get("carpetBoost")
    if vac_high is None or carpet_boost is None:
        return CARPET_BOOST_LABELS["n-a"]
    if carpet_boost:
        return CARPET_BOOST_LABELS["auto"]
    if vac_high:
        return CARPET_BOOST_LABELS["performance"]
    return CARPET_BOOST_LABELS["eco"]


def _clean_mode(entity: IRobotEntity) -> str:
    no_auto = entity.vacuum_state.get("noAutoPasses")
    two_pass = entity.vacuum_state.get("twoPass")
    if no_auto is None or two_pass is None:
        return CLEAN_MODE_LABELS["n-a"]
    if no_auto and two_pass:
        return CLEAN_MODE_LABELS["two"]
    if no_auto and not two_pass:
        return CLEAN_MODE_LABELS["one"]
    return CLEAN_MODE_LABELS["auto"]


_ACTIVE_PHASES = {"run", "hmMidMsn", "hmPostMsn", "hmUsrDock", "new", "resume"}


# notReady bitmask — individual bit meanings for i7/s9/j-series
_NOT_READY_BITS: dict[int, str] = {
    1:   "Low battery",
    2:   "Bin full",
    4:   "Map not ready",
    8:   "Not on dock",
    16:  "Lid open",
    32:  "Tank empty",
    64:  "Updating map",
    128: "Pending task",
}


def _not_ready_value(entity: "IRobotEntity") -> str:
    """Decode notReady bitmask into a human-readable label.

    NOT_READY_LABELS covers exact combined values seen in the wild.
    For unlisted combinations, decode bit by bit so any value is readable
    rather than falling back to a raw integer.
    """
    nr: int = entity.clean_mission_status.get("notReady", 0)
    if nr in NOT_READY_LABELS:
        return NOT_READY_LABELS[nr]
    if nr == 0:
        return "Ready"
    # Decode individual bits for unknown combinations.
    parts = [label for bit, label in sorted(_NOT_READY_BITS.items()) if nr & bit]
    return ", ".join(parts) if parts else f"Not ready ({nr})"


def _error_value(entity: "IRobotEntity") -> str:
    """Error label — suppressed when the robot is docked/idle after a mission.

    cleanMissionStatus.error persists across missions: the firmware does not
    reset it to 0 when the robot docks after a failure. Showing the stale error
    while the robot charges would be misleading, so we return "None" whenever
    cycle is "none" (no active or queued mission) and phase indicates rest.
    """
    status = entity.clean_mission_status
    cycle = status.get("cycle", "")
    phase = status.get("phase", "")
    error = status.get("error", 0)

    # No active mission and robot is resting — suppress stale error.
    if cycle == "none" and phase in ("charge", "stop", "idle", ""):
        return "None"

    return ERROR_CODE_LABELS.get(error, entity.vacuum.error_message or "None")


def _phase_value(entity: "IRobotEntity") -> str:
    """Phase label with Idle and Stopped detection."""
    status = entity.clean_mission_status
    phase = status.get("phase", "")
    cycle = status.get("cycle", "")
    battery = entity.vacuum_state.get("batPct")
    if phase == "charge" and battery == 100:
        return "Idle"
    if cycle == "none" and phase == "stop":
        return "Stopped"
    return PHASE_LABELS.get(phase, phase or "Unknown")


def _mission_elapsed_value(entity: "IRobotEntity") -> float | None:
    """Elapsed mission time in minutes; None if no active mission."""
    ts = entity.clean_mission_status.get("mssnStrtTm")
    if not ts:
        return None
    try:
        elapsed = dt_util.now(datetime.timezone.utc) - datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        return round(elapsed.total_seconds() / 60, 1)
    except (TypeError, ValueError, OSError):
        return None


def _ts_or_none(ts: int | None) -> "datetime.datetime | None":
    """Convert Unix timestamp int to UTC datetime, or None."""
    if not ts or ts == 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _recharge_minutes_remaining(mission: dict[str, Any]) -> StateType:
    """Return remaining mid-mission recharge time in minutes.

    Both firmware families send rechrgTm (a Unix timestamp for when recharge
    ends).  We always prefer the timestamp because it self-decrements via
    dt_util.utcnow() — this is what the iRobot app displays.

      - lewis (i/s/j-series): rechrgM=0, rechrgTm set → compute from timestamp.
      - 980/900-series: rechrgM is a pre-computed static snapshot sent once at
        recharge start and never updated.  rechrgTm is also set and is correct.
        We prefer rechrgTm so the value decrements correctly during charging.

    Falls back to rechrgM only when rechrgTm is absent (very old firmware).
    Returns None when the robot is not mid-mission recharging.

    NOTE: Between MQTT pushes the value is recomputed from the stored timestamp
    by the 60-second periodic tick in RoombaSensor.async_added_to_hass().
    """
    recharge_ts: int = int(mission.get("rechrgTm", 0) or 0)
    if recharge_ts > 0:
        remaining = recharge_ts - int(dt_util.utcnow().timestamp())
        if remaining > 0:
            return max(1, round(remaining / 60))
        # rechrgTm is in the past — recharge finished but state not yet cleared.
        return None

    # Fallback: very old firmware only sets rechrgM (static, no timestamp).
    recharge_m: int = int(mission.get("rechrgM", 0) or 0)
    if recharge_m > 0:
        return recharge_m

    return None


def _expire_minutes_remaining(mission: dict[str, Any]) -> StateType:
    """Return remaining mission expiry time in minutes.

    Same timestamp-first logic as _recharge_minutes_remaining:
    Prefer expireTm (Unix timestamp) so the value self-decrements.
    Falls back to expireM (static snapshot) only when expireTm is absent.

    Returns None when expiry is not applicable.
    """
    expire_ts: int = int(mission.get("expireTm", 0) or 0)
    if expire_ts > 0:
        remaining = expire_ts - int(dt_util.utcnow().timestamp())
        if remaining > 0:
            return max(1, round(remaining / 60))
        return None

    # Fallback: old firmware without expireTm.
    expire_m: int = int(mission.get("expireM", 0) or 0)
    if expire_m > 0:
        return expire_m

    return None


# ── v1.9.0 L4 — Wear Intelligence helpers ────────────────────────────────────

def _filter_wear_rate(entity: "IRobotEntity") -> float | None:
    """Filter wear rate in bbrun hours/day since last reset."""
    store = entity._config_entry.runtime_data.mission_store
    maint = entity._config_entry.runtime_data.maintenance_store
    if store is None or maint is None:
        return None
    current_hr = entity.run_stats.get("hr", 0)
    return store.wear_rate_since_reset(
        maint.filter_reset_hr, maint.filter_reset_at, current_hr
    )


def _brush_wear_rate(entity: "IRobotEntity") -> float | None:
    """Brush/pad wear rate in bbrun hours/day since last reset."""
    store = entity._config_entry.runtime_data.mission_store
    maint = entity._config_entry.runtime_data.maintenance_store
    if store is None or maint is None:
        return None
    current_hr = entity.run_stats.get("hr", 0)
    return store.wear_rate_since_reset(
        maint.brush_reset_hr, maint.brush_reset_at, current_hr
    )


def _filter_days_until_due(entity: "IRobotEntity") -> int | None:
    """Estimated days until filter replacement at current wear rate."""
    rate = _filter_wear_rate(entity)
    if rate is None or rate <= 0:
        return None
    maint = entity._config_entry.runtime_data.maintenance_store
    if maint is None:
        return None
    threshold = entity._config_entry.options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
    current_hr = entity.run_stats.get("hr", 0)
    remaining_hr = max(0, threshold - (current_hr - maint.filter_reset_hr))
    return int(remaining_hr / rate)


def _brush_days_until_due(entity: "IRobotEntity") -> int | None:
    """Estimated days until brush/pad replacement at current wear rate."""
    rate = _brush_wear_rate(entity)
    if rate is None or rate <= 0:
        return None
    maint = entity._config_entry.runtime_data.maintenance_store
    if maint is None:
        return None
    threshold = entity._config_entry.options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
    current_hr = entity.run_stats.get("hr", 0)
    remaining_hr = max(0, threshold - (current_hr - maint.brush_reset_hr))
    return int(remaining_hr / rate)


def _mission_store_last_started_at(entity: "IRobotEntity") -> "datetime.datetime | None":
    """Return the started_at datetime of the most recent mission from MissionStore.

    Preferred over entity.last_mission (which reads mssnStrtTm from live MQTT)
    because 900-series firmware resets mssnStrtTm to 0 when the robot docks,
    making the live value permanently None outside of active missions.
    """
    store = entity._config_entry.runtime_data.mission_store
    if store is None:
        return None
    latest = store.latest()
    if latest is None:
        return None
    started_str = latest.get("started_at")
    if not started_str:
        return None
    try:
        dt = dt_util.parse_datetime(started_str)
        if dt and dt.tzinfo is None:
            import datetime as _dt
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None

# ── v1.8.0 — L1 / L3 / L6 helper functions ──────────────────────────────────

def _mission_store_value(entity: "IRobotEntity", fn: Any) -> StateType:
    """Safely access MissionStore — returns None if unavailable."""
    store = entity._config_entry.runtime_data.mission_store
    if store is None:
        return None
    try:
        return fn(store)
    except Exception:  # noqa: BLE001
        return None


def _completion_rate_30d(store: Any) -> StateType:
    records = store.query(30)
    if not records:
        return None
    completed = sum(1 for r in records if r["result"] == "completed")
    return round(completed / len(records) * 100, 1)


def _area_cleaned_today(store: Any) -> StateType:
    today = dt_util.now().date()
    records = store.query(1, result="completed")
    areas = []
    for r in records:
        if r.get("area_sqft") is None:
            continue
        dt = dt_util.parse_datetime(r["started_at"])
        if dt is not None and dt_util.as_local(dt).date() == today:
            areas.append(r["area_sqft"])
    if not areas:
        return None
    # Convert sqft -> m² (consistent with all other area sensors)
    return round(sum(areas) * 0.0929, 1)


def _last_error_code_value(entity: "IRobotEntity") -> StateType:
    """Live MQTT error code takes priority over persisted value."""
    live = entity.vacuum_state.get("cleanMissionStatus", {}).get("error", 0)
    if live:
        return live
    stored = entity._config_entry.runtime_data.last_error_code
    if stored is not None:
        return stored
    return None  # sensor shows Unknown until first error is recorded


def _last_error_at_value(entity: "IRobotEntity") -> StateType:
    at_str = entity._config_entry.runtime_data.last_error_at
    if not at_str:
        return None
    return dt_util.parse_datetime(at_str)


def _problem_zone_value(entity: "IRobotEntity") -> StateType:
    store = entity._config_entry.runtime_data.mission_store
    if not store:
        return None
    from collections import Counter
    stuck_records = store.query(30, result="stuck")
    if not stuck_records:
        return None
    zone_counts: Counter = Counter()
    for r in stuck_records:
        for z in (r.get("zones") or []):
            zone_counts[z] += 1
    if not zone_counts:
        return None
    return zone_counts.most_common(1)[0][0]


def _presence_opportunities(entity: "IRobotEntity", days: int) -> StateType:
    store = entity._config_entry.runtime_data.mission_store
    if store is None:
        return None
    windows = store.presence_windows(days)
    if not windows:
        return None
    recent = store.query(30, result="completed")
    avg_duration = (
        sum(r["duration_min"] for r in recent) / len(recent)
        if recent else 45
    )
    return sum(1 for w in windows if w.duration_min >= avg_duration)


def _presence_utilisation(entity: "IRobotEntity", days: int) -> StateType:
    store = entity._config_entry.runtime_data.mission_store
    if store is None:
        return None
    windows = store.presence_windows(days)
    if not windows:
        return None
    opportunities = _presence_opportunities(entity, days) or 0
    if opportunities == 0:
        return 0.0
    used = sum(1 for w in windows if w.resulted_in_clean)
    return round(used / opportunities * 100, 1)


def _next_likely_clean_window(entity: "IRobotEntity") -> StateType:
    from collections import Counter
    store = entity._config_entry.runtime_data.mission_store
    if store is None:
        return None
    windows = store.presence_windows(14)
    if len(windows) < 3:
        return None
    hour_counts: Counter = Counter()
    for w in windows:
        hour_counts[w.started_at.hour] += 1
    most_common_hour = hour_counts.most_common(1)[0][0]
    candidate = dt_util.now().replace(
        hour=most_common_hour, minute=0, second=0, microsecond=0
    )
    if candidate <= dt_util.now():
        candidate = candidate + datetime.timedelta(days=1)
    return candidate





# ── F1 — WiFi floor / stability (CloudRawSensor value functions) ──────────────

def _raw_wifi_floor(records: list[dict]) -> StateType:
    """Return the weakest WiFi signal bucket present in the most recent mission.

    F1 -- wlBars is a 5-element histogram, NOT a time-series.
    Index 0 = weakest signal bucket, index 4 = strongest.
    Floor = lowest index with a non-zero count (worst signal actually seen).

    Amendment 8d: corrects the previous min(bars) implementation which
    returned the minimum bucket count, not the weakest signal bucket.
    """
    for r in records:
        bars = r.get("wlBars")
        if isinstance(bars, list) and len(bars) == 5 and any(bars):
            for i, count in enumerate(bars):
                if count > 0:
                    return i     # 0 = worst present, 4 = best present
    return None


def _raw_wifi_stability(records: list[dict]) -> StateType:
    """Return mean weighted standard deviation of WiFi signal across the API window.

    F1 -- wlBars is a 5-element histogram (index = signal bucket, value = count).
    Computes weighted mean bucket index and its weighted stdev per mission.
    High stdev = readings spread across multiple signal buckets (unstable).
    Low stdev = readings concentrated in one bucket (stable).
    Requires at least 3 records with valid WiFi data.

    Amendment 8d: corrects the previous stdev(bars) which measured variance
    of bucket counts, not variance of signal strength.
    """
    stdevs = []
    for r in records:
        bars = r.get("wlBars")
        if not isinstance(bars, list) or len(bars) != 5:
            continue
        total = sum(bars)
        if total == 0:
            continue
        weights = [b / total for b in bars]
        mean = sum(i * w for i, w in enumerate(weights))
        variance = sum(w * (i - mean) ** 2 for i, w in enumerate(weights))
        stdevs.append(variance ** 0.5)
    if len(stdevs) < 3:
        return None
    return round(sum(stdevs) / len(stdevs), 2)


# ── F2 — Mop clean mode (RoombaSensor value function) ────────────────────────

def _mop_clean_mode(entity: "IRobotEntity") -> StateType:
    """Return current mop clean mode derived from padWetness.

    F2 -- exposes the readable pad wetness as a named mode enum.
    Level 1 = Dry; levels 2-3 = Wet.
    """
    level = entity.vacuum_state.get("padWetness", {})
    if isinstance(level, dict):
        level = level.get("disposable") or level.get("reusable")
    if level is None:
        return "Unknown"
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "Unknown"
    if level == 1:
        return "Dry"
    if level in (2, 3):
        return "Wet"
    return "Unknown"


# ── F3 — Mop tank status (RoombaSensor value function) ───────────────────────

def _mop_tank_status(entity: "IRobotEntity") -> StateType:
    """Return consolidated mop tank status enum from mopReady sub-fields.

    F3 -- priority: tank missing > lid open > fill needed > ready.
    Replaces four separate binary sensors with one actionable status.
    Returns Unknown when mopReady key is absent entirely.
    """
    state = entity.vacuum_state
    if "mopReady" not in state:
        return "Unknown"
    ready = state["mopReady"]
    if not isinstance(ready, dict):
        return "Unknown"
    if not ready.get("tankPresent", True):
        return "Tank Missing"
    if not ready.get("lidClosed", True):
        return "Lid Open"
    if ready.get("fillRequired", False):
        return "Fill Tank"
    return "Ready"


# ── F3b — Mop behavior / ARS (RoombaSensor value function) ───────────────────

_MOD_RANKS: dict[int, str] = {
    15: "No Mop",
    25: "Extended",
    67: "Standard",
    85: "Deep",
}


def _mop_behavior(entity: "IRobotEntity") -> StateType:
    """Return Braava m6 Auto Replenishment System behavior mode.

    F3b -- derives behavior from rankOverlap when present; falls back to
    padDirtyPause / padDryAllowed / padWashAllowed flag combination.
    Absent for all vacuum robots.
    """
    state = entity.vacuum_state
    rank = state.get("rankOverlap")
    if rank is not None:
        return _MOD_RANKS.get(rank, "Unknown")

    dirty_pause  = state.get("padDirtyPause",  0) == 1
    dry_allowed  = state.get("padDryAllowed",  0) == 1
    wash_allowed = state.get("padWashAllowed", 0) == 1

    if not dry_allowed and not wash_allowed:
        return "Unknown"

    modes = []
    if dirty_pause:
        modes.append("Dirty Pause")
    if dry_allowed:
        modes.append("Dry")
    if wash_allowed:
        modes.append("Wash")
    return " + ".join(modes) if modes else "Unknown"



# ── F5d — Battery capacity retention ─────────────────────────────────────────

def _battery_capacity_retention(entity: "IRobotEntity") -> StateType:
    """F5d — battery capacity as % of baseline estCap.

    Calls record_estcap_if_needed on every update so the baseline is
    established automatically on first MQTT message containing estCap.
    Below 75% explains rising recharge fraction (F5c) without schedule changes.
    """
    store = entity._config_entry.runtime_data.maintenance_store
    est_cap = entity.battery_stats.get("estCap")
    if store is None or est_cap is None:
        return None
    store.record_estcap_if_needed(float(est_cap))
    if store.baseline_estcap is None or store.baseline_estcap == 0:
        return None
    return round(float(est_cap) / store.baseline_estcap * 100, 1)


# ── F5g — Estimated battery end-of-life ──────────────────────────────────────

_EOL_THRESHOLD = 65.0  # % — typical lithium end-of-life


def _estimated_battery_eol(entity: "IRobotEntity") -> StateType:
    """F5g — days remaining until battery capacity falls to EOL_THRESHOLD (65%).

    Linear extrapolation from current degradation rate:
      degradation_rate = (100 - current_pct) / current_cycles  (% per cycle)
      remaining_cycles = (current_pct - 65) / degradation_rate
      remaining_days   ≈ remaining_cycles (at 1 charge/day)

    Returns 0 when capacity is already below threshold (replace now).
    Returns None when insufficient data is available.
    """
    store = entity._config_entry.runtime_data.maintenance_store
    if store is None or store.baseline_estcap is None:
        return None

    est_cap = entity.battery_stats.get("estCap")
    cycles = (
        entity.battery_stats.get("nLithChrg")
        or entity.battery_stats.get("nNimhChrg")
        or entity.battery_stats.get("nAvail")
    )
    if est_cap is None or not cycles:
        return None

    current_pct = float(est_cap) / store.baseline_estcap * 100
    if current_pct <= _EOL_THRESHOLD:
        return 0

    degradation_rate = (100.0 - current_pct) / max(int(cycles), 1)
    if degradation_rate <= 0:
        return None

    remaining_cycles = (current_pct - _EOL_THRESHOLD) / degradation_rate
    return max(0, round(remaining_cycles))


SENSORS: tuple[RoombaSensorDescription, ...] = (

    RoombaSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda e: e.vacuum_state.get("batPct"),
    ),
    RoombaSensorDescription(
        key="phase",
        translation_key="phase",
        name="Status",
        entity_category=None,
        value_fn=_phase_value,
    ),
    RoombaSensorDescription(
        key="error",
        translation_key="error",
        name="Status – Error",
        entity_category=None,
        value_fn=_error_value,
    ),

    # GROUP 2 — Operational (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="readiness",
        translation_key="readiness",
        name="Status – Readiness",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_not_ready_value,
    ),
    RoombaSensorDescription(
        key="job_initiator",
        translation_key="job_initiator",
        name="Status – Started by",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: JOB_INITIATOR_LABELS.get(
            e.clean_mission_status.get("initiator", "none"), "None"
        ),
    ),
    RoombaSensorDescription(
        key="clean_mode",
        translation_key="clean_mode",
        name="Setting – Cleaning passes",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_clean_mode,
    ),
    RoombaSensorDescription(
        key="carpet_boost_mode",
        translation_key="carpet_boost_mode",
        name="Setting – Carpet boost",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_carpet_boost,
        value_fn=_carpet_boost_mode,
    ),

    # GROUP 3 — Maintenance (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="filter_remaining_hours",
        translation_key="filter_remaining_hours",
        name="Maintenance – Filter",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: None,  # computed in RoombaSensor.native_value
        threshold_fn=lambda e: e._config_entry.options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS),
    ),
    RoombaSensorDescription(
        key="brush_remaining_hours",
        translation_key="brush_remaining_hours",
        name="Maintenance – Brushes",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: None,  # computed in RoombaSensor.native_value
        threshold_fn=lambda e: e._config_entry.options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS),
    ),
    RoombaSensorDescription(
        key="battery_cycles",
        translation_key="battery_cycles",
        name="Maintenance – Battery cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,  # F7h: was MEASUREMENT; lifetime counter
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            # nLithChrg / nNimhChrg: 900-series and some older firmware.
            # nAvail: i/s/j-series (lewis firmware) — the correct cycle counter
            # for these models. All three map to the same concept: total charge
            # cycles completed. nAvail is tried last so explicit lithium/NiMH
            # counters take precedence when present.
            e.battery_stats.get("nLithChrg")
            or e.battery_stats.get("nNimhChrg")
            or e.battery_stats.get("nAvail")
        ),
    ),

    # GROUP 4 — Statistics (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="total_missions",
        translation_key="total_missions",
        name="Missions total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssn"),
    ),
    RoombaSensorDescription(
        key="successful_missions",
        translation_key="successful_missions",
        name="Missions successful",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnOk"),
    ),
    RoombaSensorDescription(
        key="canceled_missions",
        translation_key="canceled_missions",
        name="Missions canceled",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnC"),
    ),
    RoombaSensorDescription(
        key="failed_missions",
        translation_key="failed_missions",
        name="Missions failed",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnF"),
    ),
    RoombaSensorDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        name="Missions – Total time",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.run_stats.get("hr"),
    ),
    RoombaSensorDescription(
        key="average_mission_time",
        translation_key="average_mission_time",
        name="Missions – Avg. duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("aMssnM"),
    ),

    # GROUP 5 — Opt-in (DIAGNOSTIC, disabled by default)

    RoombaSensorDescription(
        key="total_cleaned_area",
        translation_key="total_cleaned_area",
        name="Lifetime cleaned area",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            None
            if (sqft := e.run_stats.get("sqft")) is None
            else round(sqft * 0.0929, 1)
        ),
    ),
    RoombaSensorDescription(
        key="last_mission",
        translation_key="last_mission",
        name="Missions – Last",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Read from MissionStore rather than live MQTT cleanMissionStatus.mssnStrtTm.
        # On 900-series firmware (980/985) mssnStrtTm is reset to 0 when the robot
        # docks — the live value is always None outside of an active mission.
        # MissionStore holds the correctly cached started_at from mission start.
        value_fn=lambda e: _mission_store_last_started_at(e),
    ),
    RoombaSensorDescription(
        key="scrubs_count",
        translation_key="scrubs_count",
        name="Dirt detect events",
        state_class=SensorStateClass.TOTAL_INCREASING,  # F7h: was MEASUREMENT; lifetime counter
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.run_stats.get("nScrubs"),
    ),
    RoombaSensorDescription(
        key="rssi",
        translation_key="rssi",
        name="Wi-Fi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("rssi"),
    ),

    RoombaSensorDescription(
        key="snr",
        translation_key="snr",
        name="SNR",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("snr"),
    ),

    RoombaSensorDescription(
        key="signal_noise",
        translation_key="signal_noise",
        name="Signal noise",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("noise"),
    ),

    RoombaSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        name="IP address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("netinfo", {}).get("addr"),
    ),


    # Navigation quality (VSLAM robots: 900/i/s/j — opt-in, disabled by default)
    # l_squal: 0–100, measures how well the VSLAM algorithm can navigate.
    # Low values indicate poor lighting or significant environmental changes.
    RoombaSensorDescription(
        key="nav_quality",
        translation_key="nav_quality",
        name="Navigation quality",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=has_pose,
        value_fn=lambda e: e.vacuum_state.get("mssnNavStats", {}).get("l_squal"),
    ),

    # Mission-time sensors
    RoombaSensorDescription(
        key="mission_start_time",
        translation_key="mission_start_time",
        name="Mission start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            e.last_mission
            if e.clean_mission_status.get("phase") in _ACTIVE_PHASES
            else None
        ),
    ),

    RoombaSensorDescription(
        key="mission_elapsed_time",
        translation_key="mission_elapsed_time",
        name="Mission elapsed time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mission_elapsed_value,
    ),

    RoombaSensorDescription(
        key="mission_recharge_time",
        translation_key="mission_recharge_time",
        name="Recharge time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("rechrgTm")),
    ),

    RoombaSensorDescription(
        key="mission_expire_time",
        translation_key="mission_expire_time",
        name="Mission expire time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("expireTm")),
    ),


    # ── v1.9.3 — Mission Phase Intelligence ──────────────────────────────────
    # These sensors expose the sub-state of the vacuum entity that the standard
    # VacuumActivity enum cannot express. The key distinction is mid-mission
    # recharge (phase=charge, cycle≠none) vs completed charging (cycle=none).
    # missionId is stable across all recharge cycles of a single mission,
    # allowing dashboards to group related events together.

    RoombaSensorDescription(
        key="mission_recharge_minutes",
        translation_key="mission_recharge_minutes",
        name="Mission – Recharge time remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # rechrgM is pre-computed by 980/900-series firmware.
        # lewis firmware (i/s/j-series) sends rechrgTm (Unix timestamp) and
        # leaves rechrgM=0. Fall back to computing remaining minutes from rechrgTm
        # so mid-mission recharge time is reported correctly on all robots.
        value_fn=lambda e: _recharge_minutes_remaining(e.clean_mission_status),
    ),
    RoombaSensorDescription(
        key="mission_expire_minutes",
        translation_key="mission_expire_minutes",
        name="Mission – Time until expiry",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # expireM is pre-computed by 980/900-series firmware.
        # lewis firmware sends expireTm (Unix timestamp) and leaves expireM=0.
        value_fn=lambda e: _expire_minutes_remaining(e.clean_mission_status),
    ),
    RoombaSensorDescription(
        key="mission_id",
        translation_key="mission_id",
        name="Mission – ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # missionId: stable string across all recharge cycles of one mission.
        # Populated only on i/s/j-series (older firmware may not send it).
        filter_fn=lambda s: "missionId" in s.get("cleanMissionStatus", {}),
        value_fn=lambda e: e.clean_mission_status.get("missionId") or None,
    ),
    # Schedule sensor (all models with cleanSchedule2 or cleanSchedule)

    RoombaSensorDescription(
        key="next_clean",
        translation_key="next_clean",
        name="Status – Next clean",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: bool(s.get("cleanSchedule2") or s.get("cleanSchedule")),
        value_fn=lambda e: None,   # computed in RoombaSensor.native_value
    ),

    # Device-specific: Clean Base

    RoombaSensorDescription(
        key="clean_base_status",
        translation_key="clean_base_status",
        name="Clean Base status",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_clean_base,
        value_fn=lambda e: CLEAN_BASE_LABELS.get(
            e.vacuum_state.get("dock", {}).get("state", -2), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="dock_tank_level",
        translation_key="dock_tank_level",
        name="Dock tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s.get("dock", {}),
        value_fn=lambda e: e.dock_tank_level,
    ),

    # Device-specific: Braava / mop

    RoombaSensorDescription(
        key="tank_level",
        translation_key="tank_level",
        name="Tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.tank_level,
    ),
    RoombaSensorDescription(
        key="mop_pad",
        translation_key="mop_pad",
        name="Mop pad",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "detectedPad" in s,
        value_fn=lambda e: PAD_LABELS.get(
            e.vacuum_state.get("detectedPad", "invalid"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_behavior",
        translation_key="mop_behavior",
        name="Mop – Clean passes (legacy)",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,  # superseded by mop_ars_behavior (F3b)
        filter_fn=lambda s: "rankOverlap" in s,
        value_fn=lambda e: MOP_RANK_LABELS.get(
            e.vacuum_state.get("rankOverlap"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_tank_level",
        translation_key="mop_tank_level",
        name="Mop tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.vacuum_state.get("tankLvl"),
    ),

    # F2 -- Mop clean mode (padWetness as named enum)
    RoombaSensorDescription(
        key="mop_clean_mode",
        translation_key="mop_clean_mode",
        name="Mop – Clean mode",
        device_class=SensorDeviceClass.ENUM,
        options=["Dry", "Wet", "Unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "padWetness" in s,
        value_fn=_mop_clean_mode,
    ),

    # F3 -- Mop tank status (consolidated from 4 binary mopReady sub-fields)
    RoombaSensorDescription(
        key="mop_tank_status",
        translation_key="mop_tank_status",
        name="Mop – Tank status",
        device_class=SensorDeviceClass.ENUM,
        options=["Ready", "Fill Tank", "Lid Open", "Tank Missing", "Unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "mopReady" in s,
        value_fn=_mop_tank_status,
    ),

    # F3b -- Mop behavior / Auto Replenishment System mode
    RoombaSensorDescription(
        key="mop_ars_behavior",
        translation_key="mop_ars_behavior",
        name="Mop – ARS behavior",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "No Mop", "Extended", "Standard", "Deep",
            "Dirty Pause", "Dry", "Wash",
            "Dirty Pause + Dry", "Dirty Pause + Wash", "Dry + Wash",
            "Dirty Pause + Dry + Wash", "Unknown",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "rankOverlap" in s or "padDryAllowed" in s,
        value_fn=_mop_behavior,
    ),
    # State is "unknown" on pre-v1.7 installs until the first reset is performed.

    RoombaSensorDescription(
        key="filter_last_replaced",
        translation_key="filter_last_replaced",
        name="Maintenance – Filter last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.filter_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.filter_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="brush_last_replaced",
        translation_key="brush_last_replaced",
        name="Maintenance – Brushes last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),  # Braava uses pad_last_replaced
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="pad_last_replaced",
        translation_key="pad_last_replaced",
        name="Maintenance – Pad last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),  # Braava only — same store slot as brush
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="battery_last_replaced",
        translation_key="battery_last_replaced",
        name="Maintenance – Battery last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.battery_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.battery_reset_at
            )
            else None
        ),
    ),

    # ── v1.8.0 L1 — Mission Log ───────────────────────────────────────────────

    RoombaSensorDescription(
        key="clean_streak",
        translation_key="clean_streak",
        name="Missions – Clean streak",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(e, lambda s: s.clean_streak()),
    ),
    RoombaSensorDescription(
        key="missions_last_30d",
        translation_key="missions_last_30d",
        name="Missions – Last 30 days",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: len(s.query(30, result="completed"))
        ),
    ),
    RoombaSensorDescription(
        key="completion_rate_30d",
        translation_key="completion_rate_30d",
        name="Missions – Completion rate",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(e, _completion_rate_30d),
    ),
    RoombaSensorDescription(
        key="area_cleaned_today",
        translation_key="area_cleaned_today",
        name="Missions – Area cleaned today",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: has_pose(s),   # 600-series reports no sqft
        value_fn=lambda e: _mission_store_value(e, _area_cleaned_today),
    ),
    RoombaSensorDescription(
        key="last_mission_result",
        translation_key="last_mission_result",
        name="Missions – Last result",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: s.latest().get("result") if s.latest() else None
        ),
    ),
    RoombaSensorDescription(
        key="last_mission_duration",
        translation_key="last_mission_duration",
        name="Missions – Last duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: s.latest().get("duration_min") if s.latest() else None
        ),
    ),

    # ── v1.8.0 L3 — Error Intelligence ───────────────────────────────────────

    RoombaSensorDescription(
        key="last_error_code",
        translation_key="last_error_code",
        name="Error – Last code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_code_value,
    ),
    RoombaSensorDescription(
        key="last_error_at",
        translation_key="last_error_at",
        name="Error – Last time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_at_value,
    ),
    RoombaSensorDescription(
        key="last_error_zone",
        translation_key="last_error_zone",
        name="Error – Last zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        # No filter_fn — created for all robots.
        # Returns None for 600-series (no zone data) → correct HA "unknown" state.
        # SMART: resolved from lastCommand.regions at mission start.
        # EPHEMERAL: resolved from ZoneStore at mission start.
        value_fn=lambda e: e._config_entry.runtime_data.last_error_zone,
    ),
    RoombaSensorDescription(
        key="stuck_count_30d",
        translation_key="stuck_count_30d",
        name="Error – Stuck events (30 days)",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: len(s.query(30, result="stuck"))
        ),
    ),
    RoombaSensorDescription(
        key="problem_zone",
        translation_key="problem_zone",
        name="Error – Problem zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: has_pose(s),   # requires zone tracking — excludes 600-series
        value_fn=_problem_zone_value,
    ),

    # ── v1.8.0 L6 — Presence Analytics ───────────────────────────────────────

    RoombaSensorDescription(
        key="presence_clean_opportunities_7d",
        translation_key="presence_clean_opportunities_7d",
        name="Presence – Clean opportunities (7 days)",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _presence_opportunities(e, 7),
    ),
    RoombaSensorDescription(
        key="presence_clean_utilisation_7d",
        translation_key="presence_clean_utilisation_7d",
        name="Presence – Clean utilisation (7 days)",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _presence_utilisation(e, 7),
    ),
    RoombaSensorDescription(
        key="next_likely_clean_window",
        translation_key="next_likely_clean_window",
        name="Presence – Next clean window",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_next_likely_clean_window,
    ),



    # ── v1.9.0 L4 — Wear Intelligence ────────────────────────────────────────

    RoombaSensorDescription(
        key="filter_wear_rate",
        translation_key="filter_wear_rate",
        name="Maintenance – Filter wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_filter_wear_rate,
    ),
    RoombaSensorDescription(
        key="brush_wear_rate",
        translation_key="brush_wear_rate",
        name="Maintenance – Brush wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_brush_wear_rate,
    ),
    RoombaSensorDescription(
        key="pad_wear_rate",
        translation_key="pad_wear_rate",
        name="Maintenance – Pad wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),
        value_fn=_brush_wear_rate,
    ),
    RoombaSensorDescription(
        key="filter_days_until_due",
        translation_key="filter_days_until_due",
        name="Maintenance – Filter days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_filter_days_until_due,
    ),
    RoombaSensorDescription(
        key="brush_days_until_due",
        translation_key="brush_days_until_due",
        name="Maintenance – Brush days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_brush_days_until_due,
    ),
    RoombaSensorDescription(
        key="pad_days_until_due",
        translation_key="pad_days_until_due",
        name="Maintenance – Pad days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),
        value_fn=_brush_days_until_due,
    ),
    # ── v1.9.0 Device Intelligence ───────────────────────────────────────────
    # opt-in (entity_registry_enabled_default=False): lifetime diagnostic
    # counters and static hardware values. Useful for power users and
    # debugging but not relevant for daily automations.

    RoombaSensorDescription(
        key="battery_capacity_mah",
        translation_key="battery_capacity_mah",
        name="Battery capacity",
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "estCap" in s.get("bbchg3", {}),
        value_fn=lambda e: e.battery_stats.get("estCap"),
    ),
    RoombaSensorDescription(
        key="nav_panics",
        translation_key="nav_panics",
        name="Navigation panic events",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nPanics" in s.get("bbrun", {}),
        value_fn=lambda e: e.run_stats.get("nPanics"),
    ),
    RoombaSensorDescription(
        key="cliff_events_front",
        translation_key="cliff_events_front",
        name="Cliff events – Front",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsF" in s.get("bbrun", {}),
        value_fn=lambda e: e.run_stats.get("nCliffsF"),
    ),
    RoombaSensorDescription(
        key="cliff_events_rear",
        translation_key="cliff_events_rear",
        name="Cliff events – Rear",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsR" in s.get("bbrun", {}),
        value_fn=lambda e: e.run_stats.get("nCliffsR"),
    ),

    # F5d -- battery capacity retention (% of baseline estCap)
    RoombaSensorDescription(
        key="battery_capacity_retention",
        translation_key="battery_capacity_retention",
        name="Maintenance – Battery capacity retention",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in s.get("bbchg3", {}) and s.get("batteryType") != "nimh",
        value_fn=_battery_capacity_retention,
    ),

    # F5g -- estimated battery end-of-life (days remaining until 65% threshold)
    RoombaSensorDescription(
        key="estimated_battery_eol",
        translation_key="estimated_battery_eol",
        name="Maintenance – Est. battery end of life",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in s.get("bbchg3", {}) and s.get("batteryType") != "nimh",
        value_fn=_estimated_battery_eol,
    ),

    # F6g -- consecutive clean skips counter (diagnostic).
    # Reads from MaintenanceStore via _config_entry.runtime_data — the standard
    # HA pattern for RoombaSensor value_fn accessing integration-level storage.
    # Guarded defensively: sensor is created before storage is confirmed non-None.
    RoombaSensorDescription(
        key="consecutive_clean_skips",
        translation_key="consecutive_clean_skips",
        name="Performance – Consecutive clean skips",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store.consecutive_skips
            if (
                hasattr(e, "_config_entry")
                and e._config_entry.runtime_data is not None
                and e._config_entry.runtime_data.maintenance_store is not None
            )
            else None
        ),
    ),
)


# Raw state sensor is not in SENSORS tuple — it has a bespoke entity class
# (RawStateSensor) that exposes the full vacuum_state as extra_state_attributes.


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all applicable sensors for this Roomba."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    state = roomba_reported_state(roomba)
    data = config_entry.runtime_data

    entities: list = [
        RoombaSensor(roomba, blid, description, config_entry)
        for description in SENSORS
        if description.filter_fn(state)
    ]

    # Cloud history sensors: lifetime stats from /missionhistory.
    # Available for all robots when cloud credentials are configured.
    # Data comes from the cloud coordinator (daily poll) — not from MQTT.
    if data.has_cloud:
        cc = data.cloud_coordinator  # type: ignore[union-attr]
        entities.extend([
            CloudHistorySensor(roomba, blid, cc, desc)
            for desc in CLOUD_HISTORY_SENSORS
        ])
        # v2.0: per-mission raw record sensors (recent window + cloud error)
        # F5f: recent_coverage_pct needs a MissionStore reference via closure.
        # We use a list-cell so the store is captured by reference at setup time.
        mission_store_ref: list = [data.mission_store]
        coverage_pct_fn = _make_coverage_pct_fn(mission_store_ref)

        for desc in CLOUD_RAW_SENSORS:
            if desc.key == "recent_coverage_pct":
                # Replace sentinel value_fn with the live closure
                import dataclasses
                desc = dataclasses.replace(desc, value_fn=coverage_pct_fn)
            # recent_evacuations is only meaningful when a Clean Base is present.
            # Without one the cloud always records evacs=0 — suppress the entity
            # so 980/900-series robots without a Clean Base don't show a
            # permanently-zero sensor.
            if desc.key == "recent_evacuations" and not has_clean_base(state):
                continue
            entities.append(CloudRawSensor(roomba, blid, cc, desc, config_entry))

    # Raw state sensor: opt-in, always created, exposes full MQTT state as attributes.
    entities.append(RawStateSensor(roomba, blid))
    async_add_entities(entities)


class RoombaSensor(IRobotEntity, SensorEntity):
    """A sensor entity for Roomba+, driven by the EntityDescription pattern."""

    entity_description: RoombaSensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        description: RoombaSensorDescription,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"
        # Lock entity_id to the description key so it is locale-independent.
        # Without this, HA derives entity_id from the translated name, causing
        # German slugs on DE installs (the root cause of all v5–v9 migrations).
        self._unsub_tick: Callable[[], None] | None = None

    # ── Countdown tick for recharge/expire minute sensors ────────────────────
    # iRobot firmware sends rechrgTm / expireTm once at recharge start and does
    # not push further cleanMissionStatus updates during charging.  Without a
    # periodic tick the sensor value stays frozen at the initial reading.
    # We schedule a 60-second interval whenever the sensor is enabled so the
    # value decrements correctly, matching what the iRobot app displays.

    _TICK_SENSORS = frozenset({"mission_recharge_minutes", "mission_expire_minutes"})

    async def async_added_to_hass(self) -> None:
        """Register MQTT callback and start the 60-second countdown tick."""
        await super().async_added_to_hass()
        if self.entity_description.key in self._TICK_SENSORS:
            self._unsub_tick = async_track_time_interval(
                self.hass,
                self._async_tick,
                dt_stdlib.timedelta(seconds=60),
            )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the countdown tick when the entity is removed."""
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    @callback
    def _async_tick(self, _now: dt_stdlib.datetime) -> None:
        """Re-evaluate native_value every 60 s so the countdown decrements live.

        async_write_ha_state() would be a no-op if HA thinks the state has not
        changed.  schedule_update_ha_state(force_refresh=True) forces HA to
        re-read native_value and unconditionally push the new value to the
        state machine, which is what we need for the minute countdown.
        """
        self.schedule_update_ha_state(force_refresh=True)

    @property
    def native_value(self) -> StateType:
        key = self.entity_description.key
        options = self._config_entry.options
        store = self._config_entry.runtime_data.maintenance_store

        if key == "filter_remaining_hours":
            threshold = options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.filter_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "brush_remaining_hours":
            threshold = options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.brush_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "next_clean":
            return self._calc_next_clean()

        value = self.entity_description.value_fn(self)

        # F6b — cache battery retention value to RoombaData for repair check
        if key == "battery_capacity_retention":
            data = self._config_entry.runtime_data
            data.battery_retention_value = float(value) if value is not None else None
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_battery_recharge
                self.hass.async_create_task(
                    async_check_battery_recharge(self.hass, self._config_entry),
                    name="roomba_plus_f6b_battery_retention_check",
                )

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose sensor-specific attributes used by the Lovelace card."""
        key = self.entity_description.key
        # v1.7.0 L2: threshold_hours for consumable remaining sensors
        threshold = self.entity_description.threshold_fn(self)
        if threshold is not None:
            return {"threshold_hours": threshold}
        # v1.8.0 L3: description + action for last_error_code
        if key == "last_error_code":
            code = self.native_value
            if code is not None:
                catalogue = ERROR_CATALOGUE.get(int(code), {})
                return {
                    "description": catalogue.get("description", ""),
                    "action": catalogue.get("action", ""),
                }
        # v1.9.1: status hints for numeric sensors that show Unknown
        # native_value must stay None (HA requirement for MEASUREMENT sensors)
        # but extra attributes give the user context about why.
        if key in ("filter_wear_rate", "brush_wear_rate", "pad_wear_rate",
                   "filter_days_until_due", "brush_days_until_due", "pad_days_until_due"):
            if self.native_value is None:
                maint = self._config_entry.runtime_data.maintenance_store
                if maint is None:
                    return {"status": "Maintenance store not available"}
                reset_at = (
                    maint.filter_reset_at
                    if "filter" in key
                    else maint.brush_reset_at
                )
                if reset_at is None:
                    return {"status": "Press the replacement confirmation button to start tracking"}
                return {"status": "Collecting data — available after 3 days"}
        if key == "last_mission_duration":
            if self.native_value == 0 or self.native_value is None:
                return {"status": "No mission recorded yet"}
        # v2.1.2: expose raw counts so the card can render
        # "3 cleans · 2 opportunities" without back-calculating from %.
        # The sensor value itself stays uncapped (>100% is valid and useful
        # for automations that detect over-utilisation).
        if key == "presence_clean_utilisation_7d":
            store = self._config_entry.runtime_data.mission_store
            if store is not None:
                windows = store.presence_windows(7)
                opportunities = _presence_opportunities(self, 7) or 0
                cleans = sum(1 for w in windows if w.resulted_in_clean) if windows else 0
                return {
                    "cleans_7d": cleans,
                    "opportunities_7d": opportunities,
                }
        return {}

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        key = self.entity_description.key

        if key == "battery":
            return "batPct" in new_state
        if key in ("rssi", "snr", "signal_noise"):
            return "signal" in new_state
        if key == "ip_address":
            return "netinfo" in new_state
        if key in ("mission_start_time", "mission_elapsed_time",
                   "mission_recharge_time", "mission_expire_time"):
            return "cleanMissionStatus" in new_state
        if key in ("phase", "error", "readiness", "job_initiator",
                   "mission_recharge_minutes", "mission_expire_minutes",
                   "mission_id"):
            return "cleanMissionStatus" in new_state or "error" in new_state
        if key in ("clean_base_status", "dock_tank_level"):
            return "dock" in new_state
        if key in ("mop_pad", "mop_behavior", "mop_tank_level", "tank_level"):
            return any(k in new_state for k in ("detectedPad", "rankOverlap", "tankLvl"))
        if key in ("filter_remaining_hours", "brush_remaining_hours",
                   "scrubs_count", "total_cleaning_time",
                   "filter_last_replaced", "brush_last_replaced",
                   "pad_last_replaced", "battery_last_replaced"):
            # bbrun: 900-series source for hr/sqft; runtimeStats: i/s/j-series source.
            # Both must trigger updates so the merged run_stats property stays current.
            return "bbrun" in new_state or "runtimeStats" in new_state
        if key in ("total_missions", "successful_missions", "canceled_missions",
                   "failed_missions", "average_mission_time", "last_mission"):
            return "bbmssn" in new_state
        if key == "battery_cycles":
            return "bbchg3" in new_state
        if key in ("clean_mode", "carpet_boost_mode"):
            return any(k in new_state for k in
                       ("noAutoPasses", "twoPass", "carpetBoost", "vacHigh"))
        if key == "nav_quality":
            return "mssnNavStats" in new_state
        if key == "next_clean":
            return "cleanSchedule2" in new_state or "cleanSchedule" in new_state
        # v1.8.0 L1 — Mission log sensors
        if key in ("clean_streak", "missions_last_30d", "completion_rate_30d",
                   "area_cleaned_today", "last_mission_result", "last_mission_duration"):
            return "cleanMissionStatus" in new_state
        # v1.8.0 L3 — Error intelligence sensors
        if key in ("last_error_code", "last_error_at", "last_error_zone"):
            return "cleanMissionStatus" in new_state or "error" in new_state
        if key in ("stuck_count_30d", "problem_zone"):
            return "cleanMissionStatus" in new_state
        # v1.8.0 L6 — Presence analytics sensors
        if key in ("presence_clean_opportunities_7d", "presence_clean_utilisation_7d",
                   "next_likely_clean_window"):
            return "schedHold" in new_state or "cleanMissionStatus" in new_state
        # v1.9.0 Device Intelligence sensors
        if key in ("battery_capacity_mah",):
            return "bbchg3" in new_state
        if key in ("nav_panics", "cliff_events_front", "cliff_events_rear"):
            return "bbrun" in new_state
        # v1.9.0 L4 — Wear Intelligence sensors
        if key in ("filter_wear_rate", "brush_wear_rate", "pad_wear_rate",
                   "filter_days_until_due", "brush_days_until_due", "pad_days_until_due"):
            # bbrun: 900-series source for hr; runtimeStats: i/s/j-series source.
            # cleanMissionStatus: triggers recalc at mission end.
            return (
                "bbrun" in new_state
                or "runtimeStats" in new_state
                or "cleanMissionStatus" in new_state
            )

        return len(new_state) > 1 or "signal" not in new_state

    def _calc_next_clean(self) -> StateType:
        """Return next scheduled cleaning time as a timezone-aware datetime.

        Supports cleanSchedule2 (i/s/j, array of entries with cmd) and
        cleanSchedule (900/600-series, simple time list).

        Roomba day numbering: 0=Sunday … 6=Saturday.
        """
        # Try cleanSchedule2 first (richer format)
        schedule2 = self.vacuum_state.get("cleanSchedule2", [])
        if schedule2:
            return self._next_from_schedule2(schedule2)

        # Fall back to legacy cleanSchedule
        schedule = self.vacuum_state.get("cleanSchedule", {})
        if schedule:
            return self._next_from_schedule_v1(schedule)

        return None

    def _next_from_schedule2(self, entries: list) -> dt_util.dt.datetime | None:
        """Calculate next clean from cleanSchedule2 entries."""
        now = dt_util.now()
        candidates: list[dt_util.dt.datetime] = []

        for entry in entries:
            if not entry.get("enabled", False):
                continue
            start = entry.get("start", {})
            hour = start.get("hour", 0)
            minute = start.get("min", 0)
            for roomba_day in start.get("day", []):
                # Roomba: 0=Sun … 6=Sat → Python weekday: Mon=0 … Sun=6
                py_wd = (roomba_day - 1) % 7
                days_ahead = (py_wd - now.weekday()) % 7
                candidate = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                ) + dt_stdlib.timedelta(days=days_ahead)
                if candidate <= now:
                    candidate += dt_stdlib.timedelta(days=7)
                candidates.append(candidate)

        return min(candidates) if candidates else None

    def _next_from_schedule_v1(self, schedule: dict) -> dt_util.dt.datetime | None:
        """Calculate next clean from legacy cleanSchedule dict.

        cleanSchedule format: {cycle: ["none","start",...], h: [9,...], m: [0,...]}
        where cycle has one entry per weekday (Sun=0 … Sat=6).
        """
        now = dt_util.now()
        cycle = schedule.get("cycle", [])
        hours = schedule.get("h", [])
        mins = schedule.get("m", [])
        candidates: list[dt_util.dt.datetime] = []

        for i, (cyc, h, m) in enumerate(zip(cycle, hours, mins)):
            if cyc != "start":
                continue
            # i = Roomba day (0=Sun … 6=Sat)
            py_wd = (i - 1) % 7
            days_ahead = (py_wd - now.weekday()) % 7
            candidate = now.replace(
                hour=h, minute=m, second=0, microsecond=0
            ) + dt_stdlib.timedelta(days=days_ahead)
            if candidate <= now:
                candidate += dt_stdlib.timedelta(days=7)
            candidates.append(candidate)

        return min(candidates) if candidates else None



# ── Cloud history sensors ─────────────────────────────────────────────────────
# Lifetime stats from the iRobot /missionhistory cloud endpoint.
# Available for all robots when cloud credentials are configured.
# Updated by the cloud coordinator (daily poll + map-retrain trigger).
#
# Response structure from the API:
#   {
#     "runtimeStats": {"sqft": 12345, "hr": 42, "min": 30},
#     "bbmssn":       {"nMssn": 987},
#     ...
#   }
#
# sqft is in US square feet — converted to m² for non-US robots.
# hr + min together give total lifetime cleaning duration.
# nMssn is the total number of completed missions.

@dataclass(frozen=True, kw_only=True)
class CloudHistorySensorDescription(SensorEntityDescription):
    """Description for a cloud-sourced history sensor."""
    value_fn: Callable[[dict[str, Any]], StateType]


def _mh_sqft_to_m2(history: dict[str, Any]) -> StateType:
    """Return recent cleaned area in m² summed across the API window (~30 missions).

    NOT a lifetime total. The iRobot cloud /missionhistory endpoint returns
    individual mission records; we sum sqft across the window. The true
    lifetime area comes from bbrun.sqft over local MQTT (sensor: total_cleaned_area).
    """
    sqft = (history.get("runtimeStats") or {}).get("sqft")
    if sqft is None:
        return None
    return round(sqft / 10.764, 1)


def _mh_total_minutes(history: dict[str, Any]) -> StateType:
    """Return recent cleaning time in minutes summed across the API window (~30 missions).

    NOT a lifetime total — same API limitation as _mh_sqft_to_m2.
    The true lifetime time comes from bbrun.hr/min over local MQTT.
    """
    stats = history.get("runtimeStats") or {}
    hr = stats.get("hr")
    mn = stats.get("min")
    if hr is None or mn is None:
        return None
    return hr * 60 + mn


def _mh_total_missions(history: dict[str, Any]) -> StateType:
    """Return lifetime mission count (true lifetime — nMssn is a robot-side counter)."""
    return (history.get("bbmssn") or {}).get("nMssn")


CLOUD_HISTORY_SENSORS: tuple[CloudHistorySensorDescription, ...] = (
    CloudHistorySensorDescription(
        key="recent_area_30d",
        translation_key="recent_area_30d",  # Locks entity_id slug to the key string,
        # independent of locale. translation_key uses the key value literally as the
        # slug — NOT the translated name string. This fixes fresh-install divergence
        # from migrated installs (card audit addendum, June 2026).
        name="Recent cleaned area (30 d)",
        native_unit_of_measurement="m²",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mh_sqft_to_m2,
    ),
    CloudHistorySensorDescription(
        key="recent_time_30d",
        translation_key="recent_time_30d",  # Same fix — see recent_area_30d above.
        name="Recent cleaning time (30 d)",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mh_total_minutes,
    ),
    CloudHistorySensorDescription(
        key="lifetime_missions",
        translation_key="lifetime_missions",
        name="Lifetime missions",
        native_unit_of_measurement="missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mh_total_missions,
    ),
)


# ── v2.0 Cloud sensors from raw records ──────────────────────────────────────
#
# These sensors consume mission_history_raw — the per-mission list stored by
# the coordinator since v2.0. The window is the API fetch window (~30 days).
#
# Unlike the lifetime sensors above, these are window-relative: they reflect
# the last ~30 days, not all-time totals.

@dataclass(frozen=True, kw_only=True)
class CloudRawSensorDescription(SensorEntityDescription):
    """Description for a sensor reading from the raw per-mission record list."""
    value_fn: Callable[[list[dict[str, Any]]], StateType]
    attributes_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None


def _raw_completion_rate(records: list[dict[str, Any]]) -> StateType:
    """Return completion rate (%) across the API window records."""
    if not records:
        return None
    completed = sum(1 for r in records if r.get("done") == "done")
    return round(completed / len(records) * 100, 1)


def _raw_recharges(records: list[dict[str, Any]]) -> StateType:
    """Return total mid-mission recharges across the API window."""
    if not records:
        return None
    return sum(int(r.get("chrgs", 0) or 0) for r in records)


def _raw_evacuations(records: list[dict[str, Any]]) -> StateType:
    """Return total Clean Base evacuations across the API window."""
    if not records:
        return None
    return sum(int(r.get("evacs", 0) or 0) for r in records)


def _raw_dirt_events(records: list[dict[str, Any]]) -> StateType:
    """Return total dirt detection events across the API window."""
    if not records:
        return None
    return sum(int(r.get("dirt", 0) or 0) for r in records)


def _raw_cloud_last_error_code(records: list[dict[str, Any]]) -> StateType:
    """Return the pauseId from the most recent failed mission record.

    Iterates newest-first (API returns newest first). Returns None when no
    failed mission exists in the window.

    Cloud pauseId is more reliable than cleanMissionStatus.error from MQTT:
    on 980/900-series firmware the MQTT error code sometimes never arrives.
    """
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            pause_id = int(r.get("pauseId", 0) or 0)
            return pause_id if pause_id > 0 else None
    return None


def _raw_cloud_last_error_time(records: list[dict[str, Any]]) -> StateType:
    """Return the end timestamp of the most recent failed mission as a datetime.

    HA requires a timezone-aware datetime object for device_class=TIMESTAMP sensors.
    """
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            ts = r.get("timestamp")
            if ts:
                import datetime
                return datetime.datetime.fromtimestamp(
                    int(ts), tz=datetime.timezone.utc
                )
    return None


def _raw_cloud_last_error_attrs(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return ERROR_CATALOGUE label + action for the most recent cloud error."""
    from .const import ERROR_CATALOGUE
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            pause_id = int(r.get("pauseId", 0) or 0)
            catalogue = ERROR_CATALOGUE.get(pause_id, {})
            return {
                "error_code": pause_id or None,
                "label": catalogue.get("label", ""),
                "description": catalogue.get("description", ""),
                "action": catalogue.get("action", ""),
                "source": "cloud_pauseId",
            }
    return {}



# ── F5 — Performance intelligence (CloudRawSensor + RoombaSensor functions) ───

import statistics as _statistics
nPARALLEL_UPDATES = 0


def _raw_cleaning_speed(records: list[dict]) -> StateType:
    """F5a — median cleaning speed (m²/min) across the API window.

    Cloud API returns sqft — converted to m² (× 0.0929) for consistency
    with all other area sensors in Roomba+.
    Uses runM (clean time excl. recharge) preferred over durationM.
    Skips records missing either field or with zero time.
    """
    speeds = []
    for r in records:
        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            m2_per_min = float(sqft) * 0.0929 / float(run_m)
            speeds.append(m2_per_min)
    if not speeds:
        return None
    return round(_statistics.median(speeds), 2)


def _raw_dirt_density(records: list[dict]) -> StateType:
    """F5b — median dirt events per m² across the API window.

    Cloud API returns sqft — converted to m² (÷ 0.0929) for consistency.
    Rising values indicate a dirtier floor OR worn brushes (debris not
    captured, sensor re-fires).  The cause attribute distinguishes them.
    """
    densities = []
    for r in records:
        dirt = r.get("dirt")
        sqft = r.get("sqft")
        if dirt is not None and sqft and float(sqft) > 0:
            m2 = float(sqft) * 0.0929
            densities.append(float(dirt) / m2)
    if not densities:
        return None
    return round(_statistics.median(densities), 3)


def _classify_dirt_cause(dirt_trend: str, speed_trend: str) -> str:
    """Classify the most probable cause of rising dirt density.

    F5b — 3-signal classification eliminating threshold-based guessing:
      brush_wear  — debris not captured, sensor re-fires (rising dirt + declining speed)
      floor_dirty — robot working harder but keeping up (rising dirt + stable/rising speed)
    """
    if dirt_trend == "rising" and speed_trend == "declining":
        return "brush_wear"
    if dirt_trend == "rising" and speed_trend in ("stable", "rising", "unknown"):
        return "floor_dirty"
    return "unknown"


def _raw_dirt_density_attrs(records: list[dict]) -> dict:
    """F5b — cause attribute for recent_dirt_density sensor.

    Computes independent dirt-density and cleaning-speed trends from the
    raw records, then classifies the cause via _classify_dirt_cause().
    """
    # Compute independent trends: dirt density trend vs speed trend
    dirt_densities = []
    for r in records:
        dirt = r.get("dirt")
        sqft = r.get("sqft")
        if dirt is not None and sqft and float(sqft) > 0:
            dirt_densities.append(float(dirt) / (float(sqft) * 0.0929))
    speeds = []
    for r in records:
        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            speeds.append(float(sqft) / float(run_m))

    def _trend(values: list[float]) -> str:
        if len(values) < 6:
            return "unknown"
        recent = _statistics.median(values[:5])
        older  = _statistics.median(values[5:])
        if older == 0:
            return "unknown"
        delta = (recent - older) / older
        if delta > 0.10:
            return "rising" if values is dirt_densities else "improving"
        if delta < -0.10:
            return "declining"
        return "stable"

    dt = _trend(dirt_densities)
    st = _trend(speeds)
    return {"cause": _classify_dirt_cause(dt, st)}


def _raw_recharge_fraction(records: list[dict]) -> StateType:
    """F5c — median recharge fraction (chrgM / durationM) across window.

    Uses the cloud `chrgM` field (minutes recharging mid-mission) divided by
    `durationM` (total mission minutes), expressed as a percentage.

    Note: The `recharge_min` key from F4e (local MissionStore accumulation) is
    structurally read as a fallback, but `raw_records` are cloud-only and will
    never contain it at runtime. The fallback is retained for future use if
    local records are ever merged into `raw_records` at the coordinator level.
    Rising values indicate battery degradation or a home too large for one charge.
    """
    fractions = []
    for r in records:
        chrg_m = r.get("chrgM") or r.get("recharge_min")
        dur_m  = r.get("durationM") or r.get("duration_min")
        if chrg_m is not None and dur_m and float(dur_m) > 0:
            fractions.append(float(chrg_m) / float(dur_m) * 100)
    if not fractions:
        return None
    return round(_statistics.median(fractions), 1)


def _raw_cleaning_speed_trend(records: list[dict]) -> StateType:
    """F5e — cleaning speed trend: improving / stable / declining / unknown.

    Compares median of 5 most-recent vs previous 10 records.
    Gap filter: excludes the first 3 missions after a >7-day gap — they are
    catching-up cleans on an abnormally dirty floor and would produce false
    'declining' signals.

    Records must be newest-first (cloud API order). Local MissionStore records
    are oldest-first — do not pass them directly to this function.
    """
    # Build speed series newest-first (records are already newest-first)
    filtered = []
    prev_ts: float | None = None
    skip_remaining = 0

    for r in records:
        ts_raw = r.get("startTime") or r.get("timestamp")
        ts = float(ts_raw) if ts_raw else None

        if prev_ts is not None and ts is not None:
            gap_days = (prev_ts - ts) / 86400
            if gap_days > 7:
                skip_remaining = 3  # skip next 3 after the gap

        if skip_remaining > 0:
            skip_remaining -= 1
            if ts is not None:
                prev_ts = ts
            continue

        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            filtered.append(float(sqft) / float(run_m))

        if ts is not None:
            prev_ts = ts

    if len(filtered) < 6:
        return "unknown"

    recent = _statistics.median(filtered[:5])
    older  = _statistics.median(filtered[5:min(15, len(filtered))])
    if older == 0:
        return "unknown"
    delta = (recent - older) / older
    if delta > 0.10:
        return "improving"
    if delta < -0.10:
        return "declining"
    return "stable"


def _make_coverage_pct_fn(mission_store_ref: list) -> Callable:
    """F5f — factory returning a value_fn that captures the MissionStore reference.

    Uses a list-cell reference so the closure sees the live store even though
    it is set after this function is called (during async_setup_entry).
    """
    def _coverage_pct(records: list[dict]) -> StateType:
        store = mission_store_ref[0] if mission_store_ref else None
        if store is None or not records:
            return None
        recent_sqft = records[0].get("sqft")
        p75 = store.p75_area(60)
        if recent_sqft is None or p75 is None or p75 == 0:
            return None
        return round(float(recent_sqft) / p75 * 100, 1)
    return _coverage_pct


# Sentinel description for F5f — value_fn swapped in async_setup_entry
_COVERAGE_PCT_SENTINEL = "coverage_pct_sentinel"


CLOUD_RAW_SENSORS: tuple[CloudRawSensorDescription, ...] = (
    CloudRawSensorDescription(
        key="recent_completion_rate",
        translation_key="recent_completion_rate",
        name="Recent completion rate",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_completion_rate,
    ),
    CloudRawSensorDescription(
        key="recent_recharges",
        translation_key="recent_recharges",
        name="Recent mid-mission recharges",
        native_unit_of_measurement="recharges",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_recharges,
    ),
    CloudRawSensorDescription(
        key="recent_evacuations",
        translation_key="recent_evacuations",
        name="Recent Clean Base evacuations",
        native_unit_of_measurement="evacuations",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_evacuations,
    ),
    CloudRawSensorDescription(
        key="recent_dirt_events",
        translation_key="recent_dirt_events",
        name="Recent dirt events",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_dirt_events,
    ),
    CloudRawSensorDescription(
        key="recent_error_code",
        translation_key="recent_error_code",
        name="Recent error code (cloud)",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_cloud_last_error_code,
        attributes_fn=_raw_cloud_last_error_attrs,
    ),
    CloudRawSensorDescription(
        key="recent_error_time",
        translation_key="recent_error_time",
        name="Recent error time (cloud)",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_cloud_last_error_time,
    ),

    # F1 -- WiFi floor and stability from per-mission wlBars arrays
    CloudRawSensorDescription(
        key="recent_wifi_floor",
        translation_key="recent_wifi_floor",
        name="Wi-Fi – Signal floor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw_wifi_floor,
    ),
    CloudRawSensorDescription(
        key="recent_wifi_stability",
        translation_key="recent_wifi_stability",
        name="Wi-Fi – Signal stability",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_raw_wifi_stability,
    ),

    # F5a -- cleaning speed (m²/min, converted from cloud sqft)
    CloudRawSensorDescription(
        key="recent_cleaning_speed",
        translation_key="recent_cleaning_speed",
        name="Performance – Cleaning speed",
        native_unit_of_measurement="m²/min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_cleaning_speed,
    ),

    # F5b -- dirt density (events/sqft) with cause attribute
    CloudRawSensorDescription(
        key="recent_dirt_density",
        translation_key="recent_dirt_density",
        name="Performance – Dirt density",
        native_unit_of_measurement="events/m²",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_dirt_density,
        attributes_fn=_raw_dirt_density_attrs,
    ),

    # F5c -- recharge fraction (%)
    CloudRawSensorDescription(
        key="recent_recharge_fraction",
        translation_key="recent_recharge_fraction",
        name="Performance – Recharge fraction",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_recharge_fraction,
    ),

    # F5e -- cleaning speed trend (ENUM)
    CloudRawSensorDescription(
        key="cleaning_speed_trend",
        translation_key="cleaning_speed_trend",
        name="Performance – Cleaning speed trend",
        device_class=SensorDeviceClass.ENUM,
        options=["improving", "stable", "declining", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_raw_cleaning_speed_trend,
    ),

    # F5f -- coverage pct — value_fn swapped in async_setup_entry via factory
    # The sentinel key is detected at setup time and replaced with the closure.
    CloudRawSensorDescription(
        key="recent_coverage_pct",
        translation_key="recent_coverage_pct",
        name="Performance – Coverage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda records: None,  # replaced in async_setup_entry
    ),
)


class CloudRawSensor(IRobotEntity, SensorEntity):
    """Sensor reading per-mission stats from the iRobot cloud raw record list.

    Reads from coordinator.raw_records — the per-mission list stored since v2.0.
    Updates whenever the coordinator refreshes (daily poll or map-retrain trigger).

    Available for all robots with cloud credentials (EPHEMERAL + SMART).
    """

    entity_description: CloudRawSensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        description: CloudRawSensorDescription,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_{description.key}"
        # Lock entity_id to the description key so it is locale-independent.

    @property
    def native_value(self) -> StateType:
        value = self.entity_description.value_fn(self._coordinator.raw_records)
        # F6a/F6b — cache values to RoombaData for repair check functions
        key = self.entity_description.key
        data = self._config_entry.runtime_data
        if key == "cleaning_speed_trend":
            data.cleaning_speed_trend_value = str(value) if value else None
            # F6a — trigger performance degradation check on every trend update
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_performance_degradation
                self.hass.async_create_task(
                    async_check_performance_degradation(self.hass, self._config_entry),
                    name="roomba_plus_f6a_perf_check",
                )
        elif key == "recent_recharge_fraction":
            data.recharge_fraction_value = float(value) if value is not None else None
            # F6b — check battery/recharge correlation
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_battery_recharge
                self.hass.async_create_task(
                    async_check_battery_recharge(self.hass, self._config_entry),
                    name="roomba_plus_f6b_battery_check",
                )
        elif key == "recent_dirt_density":
            # Update dirt_density_rising flag for F6a cause classification
            records = self._coordinator.raw_records
            if records and len(records) >= 6:
                import statistics as _stat
                densities = [
                    float(r["dirt"]) / float(r["sqft"])
                    for r in records
                    if r.get("dirt") is not None and r.get("sqft") and float(r["sqft"]) > 0
                ]
                if len(densities) >= 6:
                    recent = _stat.median(densities[:5])
                    older  = _stat.median(densities[5:])
                    data.dirt_density_rising = (recent / older > 1.10) if older > 0 else False
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.entity_description.attributes_fn is None:
            return {}
        return self.entity_description.attributes_fn(self._coordinator.raw_records)

    @property
    def available(self) -> bool:
        return (
            self._coordinator.last_update_success
            and self._coordinator.data is not None
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()
            # F6f -- trigger accident detection on each cloud poll,
            # but only from the dirt density sensor to avoid 6x redundant calls.
            if (
                self.entity_description.key == "recent_dirt_density"
                and self.hass.is_running
            ):
                from .repairs import async_check_accident_detection
                self.hass.async_create_task(
                    async_check_accident_detection(
                        self.hass,
                        self._config_entry,
                        self._coordinator.raw_records,
                    ),
                    name="roomba_plus_f6f_accident_check",
                )

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


class CloudHistorySensor(IRobotEntity, SensorEntity):
    """Sensor reading lifetime stats from the iRobot cloud mission history.

    Unlike RoombaSensor (which is driven by MQTT push), this sensor reads
    from the cloud coordinator cache. It updates whenever the coordinator
    refreshes (daily poll or map-retrain trigger) — not on every MQTT message.

    Available for all robots when cloud credentials are configured,
    including the 980 which does not expose lifetime stats over local MQTT.
    """

    entity_description: CloudHistorySensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        description: CloudHistorySensorDescription,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_{description.key}"
        # Lock entity_id to the description key so it is locale-independent.
        # Without this, HA derives entity_id from the translated name, which
        # produces German slugs (e.g. gereinigte_flache_30_t) on DE installs.

    @property
    def native_value(self) -> StateType:
        if not self._coordinator.data:
            return None
        history = self._coordinator.data.get("mission_history", {})
        return self.entity_description.value_fn(history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose data source context for the cloud history sensors.

        lifetime_missions reads nMssn from each record — a true lifetime
        counter embedded by the robot in every mission entry.

        recent_area_30d and recent_time_30d are aggregated from the API
        window (the last ~30 missions), not true lifetime totals. The true
        lifetime area and time come from bbrun over local MQTT.
        """
        key = self.entity_description.key
        if key == "lifetime_missions":
            return {"source": "lifetime_counter_from_robot"}
        if key in ("recent_area_30d", "recent_time_30d"):
            return {
                "source": "recent_mission_window",
                "note": "Aggregated from recent mission history (~30 missions). "
                        "Not a lifetime total — use the local MQTT sensor "
                        "'Lifetime cleaned area' for the true value.",
            }
        return {}

    @property
    def available(self) -> bool:
        return (
            self._coordinator.last_update_success
            and self._coordinator.data is not None
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Cloud sensor does not update from MQTT — coordinator handles refresh.
        return False

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


class RawStateSensor(IRobotEntity, SensorEntity):
    """Opt-in sensor that exposes the full MQTT state as extra_state_attributes.

    The sensor state value is a simple count of top-level keys in the reported
    state — useful as a change indicator. All actual data lives in attributes.

    Disabled by default — must be explicitly enabled in the HA UI.
    Intended for power users and debugging unknown robot models.
    """

    _attr_translation_key = "raw_state"
    _attr_name            = "Raw MQTT state"   # G6: locale-independent entity_id slug
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_raw_state"

    @property
    def native_value(self) -> int:
        """Return count of top-level reported state keys."""
        return len(self.vacuum_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full reported state as attributes.

        Complex nested values (dicts, lists) are JSON-serialised to strings
        so that HA's attribute storage never receives un-serialisable objects.
        All values are HA-safe primitives after this conversion.
        """
        import json as _json
        result: dict[str, Any] = {}
        for key, value in self.vacuum_state.items():
            if isinstance(value, (dict, list)):
                try:
                    result[key] = _json.dumps(value, default=str)
                except Exception:  # noqa: BLE001
                    result[key] = str(value)
            else:
                result[key] = value
        return result

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Update on any MQTT message — this is a catch-all debug sensor
        return True
