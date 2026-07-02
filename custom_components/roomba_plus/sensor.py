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

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar
import logging
import time as _time_mod

_LOGGER = logging.getLogger(__name__)

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
from homeassistant.helpers import issue_registry as ir
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
    DOMAIN,
    ERROR_CATALOGUE,
    ERROR_CODE_LABELS,
    EVENT_HEALTH_CHANGE,
    INTEGRATION_HEALTH_ARC1_STALE_HOURS,
    INTEGRATION_HEALTH_GOOD_THRESHOLD,
    INTEGRATION_HEALTH_LOW_THRESHOLD,
    INTEGRATION_HEALTH_MQTT_STALE_HOURS,
    INTEGRATION_HEALTH_TICK_SECONDS,
    JOB_INITIATOR_LABELS,
    MOP_RANK_LABELS,
    NOT_READY_LABELS,
    PAD_LABELS,
    PHASE_LABELS,
    SQFT_TO_M2,
    active_charge_cycles,
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
    # When set, the entity reports unavailable (not unknown) when fn returns False.
    # Use for sensors that only apply in a specific robot state (e.g. mid-mission
    # recharge). "Unknown" implies a data error; "Unavailable" is cleaner for
    # "not applicable right now".
    available_fn: Callable[[IRobotEntity], bool] | None = field(default=None)
    # v1.7.0 L2 — when set, exposed as "threshold_hours" in extra_state_attributes
    # Used by the Lovelace card to compute remaining % without hard-coded thresholds.
    threshold_fn: Callable[[IRobotEntity], int | None] = field(
        default_factory=lambda: lambda _: None
    )
    # v2.7.1 — optional extra attributes beyond what RoombaSensor.extra_state_attributes
    # provides by default. Merged into the default attributes dict when set.
    extra_attributes_fn: Callable[[IRobotEntity], dict[str, Any]] | None = field(
        default=None
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
    # Per MISSIONSTORE_FIELD_REGISTRY: completed = "completed" OR "stuck_and_resumed"
    completed = sum(
        1 for r in records
        if r["result"] in ("completed", "stuck_and_resumed")
    )
    return round(completed / len(records) * 100, 1)


def _last_mission_team_id(store: Any) -> StateType:
    """v3.2.0 TEAM-INDICATOR — team_id of the most recent mission, if any.

    None for the vast majority of missions (ordinary single-robot runs).
    Purely informational — confirms whether the last mission was part of
    an Imprint Link team clean, with no new control path.
    """
    latest = store.latest()
    if latest is None:
        return None
    return latest.get("team_id")


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
    # Convert sqft -> m² (consistent with all other area sensors)
    return round(sum(areas) * SQFT_TO_M2, 1) if areas else 0.0


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
    stuck_records = store.query(30, result=store.STUCK_RESULTS)
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





# ── F1 — WiFi floor / stability (also used by RoombaWifiHealthSensor) ──────────

def _parse_netinfo_addr(addr: object) -> str | None:
    """Parse netinfo.addr to a dotted-decimal IP string.

    NETINFO-FMT (v2.8.0) — netinfo.addr has two formats across firmware families:
      - i/s/j-series (lewis/soho): dotted string, e.g. "192.168.1.5" → return as-is.
      - 9-series (980/900): uint32 big-endian, e.g. 3232235777 → "192.168.1.1".

    Returns None for missing or unparsable values.
    """
    if addr is None:
        return None
    if isinstance(addr, str):
        return addr if addr else None
    if isinstance(addr, (int, float)) and not isinstance(addr, bool):
        import socket
        import struct
        try:
            return socket.inet_ntoa(struct.pack(">I", int(addr)))
        except (struct.error, OSError, OverflowError, ValueError):
            return None
    return None


def _raw_wifi_floor(records: list[dict]) -> StateType:
    """Return the weakest WiFi signal bucket present in the most recent mission.

    F1 -- wlBars is a 5-element histogram, NOT a time-series.
    Index 0 = weakest signal bucket, index 4 = strongest.
    Floor = lowest index with a non-zero count (worst signal actually seen).

    Amendment 8d: corrects the previous min(bars) implementation which
    returned the minimum bucket count, not the weakest signal bucket.

    v2.9.0 — this is a worst-case/dead-zone diagnostic (did the signal ever
    dip into the weakest bucket at all), deliberately distinct from average
    quality. Still useful on its own (RoombaWifiHealthSensor exposes it as
    the weakest_bucket_observed attribute), but must not be used as a
    PERCENTAGE "quality" state — a single brief dip into bucket 0 during an
    otherwise excellent connection would make WLAN-Qualität read "0%" even
    though the WiFi is fine. See _raw_wifi_quality_pct for the actual
    quality-percentage metric.
    """
    for r in records:
        bars = r.get("wlBars")
        if isinstance(bars, list) and len(bars) == 5 and any(bars):
            for i, count in enumerate(bars):
                if count > 0:
                    return i     # 0 = worst present, 4 = best present
    return None


def _raw_wifi_quality_pct(records: list[dict]) -> StateType:
    """Return average WiFi signal quality (%) across the API window.

    v2.9.0 — replaces _raw_wifi_floor as RoombaWifiHealthSensor's primary
    state. _raw_wifi_floor returns a raw 0-4 bucket index from a SINGLE
    record (the first one found with valid data) — yet the entity declares
    PERCENTAGE as its unit and its docstring claimed "% of missions with
    acceptable floor signal". Neither matched the implementation: a "0"
    bucket-index value displayed as "0%" reads as "WiFi is unusable" when
    it may just mean one brief dip during an otherwise good connection.

    F1 -- wlBars is a 5-element histogram (index = signal bucket 0=weakest,
    4=strongest, value = count). For each mission record, compute the
    weighted mean bucket index (same approach as the already-correct
    _raw_wifi_stability, which uses the full histogram distribution rather
    than just whether the weakest bucket was ever touched), then average
    those per-mission means across all available records and scale the
    0.0-4.0 result to a genuine 0-100% PERCENTAGE.
    """
    means: list[float] = []
    for r in records:
        bars = r.get("wlBars")
        if not isinstance(bars, list) or len(bars) != 5:
            continue
        total = sum(bars)
        if total == 0:
            continue
        means.append(sum(i * b for i, b in enumerate(bars)) / total)
    if not means:
        return None
    return round((sum(means) / len(means)) / 4 * 100, 1)


def _health_band(score: int) -> str:
    """v2.9.0 EVENT-BUS — classify a score into one of three bands.

    Used only for health_change event band-crossing detection, not for the
    Repair Issue (which uses its own sustained-duration check against
    INTEGRATION_HEALTH_LOW_THRESHOLD directly). Band-crossing rather than
    raw score delta avoids firing an event on every minor score wobble.
    """
    if score >= INTEGRATION_HEALTH_GOOD_THRESHOLD:
        return "healthy"
    if score >= INTEGRATION_HEALTH_LOW_THRESHOLD:
        return "degraded"
    return "critical"


def _compute_integration_health(hass: Any, entry: Any) -> tuple[int, dict[str, Any]]:
    """Return (score 0-100, breakdown) for the integration_health sensor.

    v2.9.0 (INTEG-HEALTH). Three signals, each independently testable:

    1. Active Repair Issues for this config entry — the strongest signal,
       since each one already represents a confirmed, specific problem
       (cloud_stale, mqtt_watchdog, error_recurrence, etc.). -20 per issue,
       capped at -60 so a handful of issues doesn't immediately floor the
       score to 0 — still useful as a relative health trend.

    2. MQTT message age — only penalised beyond
       INTEGRATION_HEALTH_MQTT_STALE_HOURS (24h), a much longer bar than
       MQTT_WATCHDOG_SECONDS (5 min, mission-specific). This catches "the
       local connection looks entirely dead", not routine idle-time quiet
       — most installs go hours between missions with no MQTT traffic at
       all, and that's completely normal.

    3. ARC1 (MissionArchive) freshness — only evaluated when cloud is
       configured. The newest archived mission being older than
       INTEGRATION_HEALTH_ARC1_STALE_HOURS (48h) suggests the cloud→
       archive sync pipeline itself may be stuck, even if the coordinator's
       own refresh calls are nominally succeeding (a DIFFERENT failure
       mode than CLOUD-STALE, which only checks the refresh call itself).
       This is a proxy, not a direct "last sync attempt" timestamp — no
       such field exists yet, and recent mission age also legitimately
       reflects "the robot just hasn't cleaned in a while", not only sync
       health. Documented as a known limitation rather than over-claiming
       precision here.

    Two signals from the original plan were deliberately NOT implemented
    as separate items:
    - "Cloud age" — redundant with signal 1, since a stale cloud
      coordinator already raises the cloud_stale Repair Issue, which
      signal 1 already counts. A separate cloud-age penalty would
      double-count the same underlying condition.
    - "Last store save" — no generic "last saved" timestamp is tracked
      across all stores today; inventing one just for this score, without
      a real use case driving its precision, was judged not worth the
      new persisted state it would require.
    """
    score = 100
    breakdown: dict[str, Any] = {}

    registry = ir.async_get(hass)
    suffix = f"_{entry.entry_id}"
    active_issues = [
        e for (domain, issue_id), e in registry.issues.items()
        if domain == DOMAIN and issue_id.endswith(suffix) and e.active
    ]
    issue_count = len(active_issues)
    score -= min(60, issue_count * 20)
    breakdown["active_issues"] = issue_count

    data = entry.runtime_data
    last_mqtt_ts = getattr(data, "last_mqtt_message_ts", 0.0) or 0.0
    mqtt_age_hours: float | None = None
    if last_mqtt_ts > 0:
        mqtt_age_hours = (_time_mod.time() - last_mqtt_ts) / 3600
        if mqtt_age_hours > INTEGRATION_HEALTH_MQTT_STALE_HOURS:
            score -= 20
    breakdown["mqtt_age_hours"] = (
        round(mqtt_age_hours, 1) if mqtt_age_hours is not None else None
    )

    archive = getattr(data, "mission_archive", None)
    cloud = getattr(data, "cloud_coordinator", None)
    arc1_age_hours: float | None = None
    if archive is not None and cloud is not None and archive.record_count > 0:
        newest = archive.all_derived_oldest_first()[-1]
        end_ts = newest.get("end_ts")
        if end_ts:
            try:
                parsed = dt_util.parse_datetime(end_ts)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                arc1_age_hours = (
                    dt_util.utcnow() - parsed
                ).total_seconds() / 3600
                if arc1_age_hours > INTEGRATION_HEALTH_ARC1_STALE_HOURS:
                    score -= 20
    breakdown["arc1_age_hours"] = (
        round(arc1_age_hours, 1) if arc1_age_hours is not None else None
    )

    score = max(0, score)
    breakdown["score"] = score
    return score, breakdown


# ── v3.1.0 PLAIN-STATUS ──────────────────────────────────────────────────────
# Plain-language status_text/recommendation derived from the existing
# breakdown dicts of integration_health and robot_health_score — no new
# computation, just a human-readable translation layer. Mirrors the
# established hass.config.language pattern from device_tracker.py since
# extra_state_attributes values are not covered by the strings.json/
# translations/*.json mechanism (that only translates entity names and
# config-flow text, not runtime attribute values).

_PLAIN_LANG_TABLE = ("en", "de", "fr", "it", "es", "nl", "pt")


def _plain_lang(hass: Any) -> str:
    lang = (hass.config.language or "en")[:2]
    return lang if lang in _PLAIN_LANG_TABLE else "en"


_INTEG_HEALTH_HEALTHY: dict[str, str] = {
    "en": "Everything is fine",
    "de": "Alles in Ordnung",
    "fr": "Tout va bien",
    "it": "Tutto a posto",
    "es": "Todo está bien",
    "nl": "Alles in orde",
    "pt": "Tudo certo",
}

_INTEG_HEALTH_ACTIVE_ISSUES: dict[str, str] = {
    "en": "{n} active issue(s) detected",
    "de": "{n} aktive Probleme erkannt",
    "fr": "{n} problème(s) actif(s) détecté(s)",
    "it": "{n} problema/i attivo/i rilevato/i",
    "es": "{n} problema(s) activo(s) detectado(s)",
    "nl": "{n} actief(ve) probleem/problemen gedetecteerd",
    "pt": "{n} problema(s) ativo(s) detectado(s)",
}

_INTEG_HEALTH_ACTIVE_ISSUES_REC: dict[str, str] = {
    "en": "See details under Settings → Repairs",
    "de": "Details unter Einstellungen → Reparaturen",
    "fr": "Voir les détails dans Paramètres → Réparations",
    "it": "Vedi dettagli in Impostazioni → Riparazioni",
    "es": "Ver detalles en Ajustes → Reparaciones",
    "nl": "Bekijk details onder Instellingen → Reparaties",
    "pt": "Veja detalhes em Configurações → Reparos",
}

_INTEG_HEALTH_MQTT_STALE: dict[str, str] = {
    "en": "Local connection looks dead — is the robot on WiFi?",
    "de": "Lokale Verbindung wirkt tot — Roboter im WLAN?",
    "fr": "La connexion locale semble morte — le robot est-il sur le WiFi ?",
    "it": "La connessione locale sembra inattiva — il robot è sul WiFi?",
    "es": "La conexión local parece muerta — ¿está el robot en WiFi?",
    "nl": "Lokale verbinding lijkt dood — zit de robot op WiFi?",
    "pt": "A conexão local parece inativa — o robô está no WiFi?",
}

_INTEG_HEALTH_MQTT_STALE_REC: dict[str, str] = {
    "en": "Check the robot's WiFi connection",
    "de": "Roboter-WLAN-Verbindung prüfen",
    "fr": "Vérifiez la connexion WiFi du robot",
    "it": "Controlla la connessione WiFi del robot",
    "es": "Revisa la conexión WiFi del robot",
    "nl": "Controleer de WiFi-verbinding van de robot",
    "pt": "Verifique a conexão WiFi do robô",
}

_INTEG_HEALTH_ARC1_STALE: dict[str, str] = {
    "en": "Cloud connection has been stuck for {n}h",
    "de": "Cloud-Verbindung hängt seit {n}h",
    "fr": "La connexion cloud est bloquée depuis {n}h",
    "it": "La connessione cloud è bloccata da {n}h",
    "es": "La conexión a la nube lleva {n}h bloqueada",
    "nl": "Cloudverbinding hangt al {n}u vast",
    "pt": "A conexão com a nuvem está travada há {n}h",
}

_INTEG_HEALTH_ARC1_STALE_REC: dict[str, str] = {
    "en": "Check your iRobot credentials in the options",
    "de": "iRobot-Zugangsdaten in den Optionen prüfen",
    "fr": "Vérifiez vos identifiants iRobot dans les options",
    "it": "Controlla le credenziali iRobot nelle opzioni",
    "es": "Revisa tus credenciales de iRobot en las opciones",
    "nl": "Controleer je iRobot-gegevens in de opties",
    "pt": "Verifique suas credenciais iRobot nas opções",
}


def _integration_health_plain_status(
    hass: Any, breakdown: dict[str, Any]
) -> tuple[str, str | None]:
    """v3.1.0 PLAIN-STATUS — derive (status_text, recommendation) from the
    integration_health breakdown. Priority mirrors the score's own
    weighting: active_issues (strongest signal) > mqtt_age > arc1_age.
    Only one condition is surfaced even if several apply — the strongest
    signal is the most actionable one.
    """
    lang = _plain_lang(hass)
    issue_count = breakdown.get("active_issues", 0)
    mqtt_age = breakdown.get("mqtt_age_hours")
    arc1_age = breakdown.get("arc1_age_hours")

    if issue_count:
        text = _INTEG_HEALTH_ACTIVE_ISSUES[lang].format(n=issue_count)
        rec = _INTEG_HEALTH_ACTIVE_ISSUES_REC[lang]
        return text, rec

    if mqtt_age is not None and mqtt_age > INTEGRATION_HEALTH_MQTT_STALE_HOURS:
        return _INTEG_HEALTH_MQTT_STALE[lang], _INTEG_HEALTH_MQTT_STALE_REC[lang]

    if arc1_age is not None and arc1_age > INTEGRATION_HEALTH_ARC1_STALE_HOURS:
        text = _INTEG_HEALTH_ARC1_STALE[lang].format(n=round(arc1_age))
        rec = _INTEG_HEALTH_ARC1_STALE_REC[lang]
        return text, rec

    return _INTEG_HEALTH_HEALTHY[lang], None


_ROBOT_HEALTH_GOOD: dict[str, str] = {
    "en": "Robot is in good condition",
    "de": "Roboter ist in gutem Zustand",
    "fr": "Le robot est en bon état",
    "it": "Il robot è in buone condizioni",
    "es": "El robot está en buen estado",
    "nl": "Robot is in goede staat",
    "pt": "O robô está em bom estado",
}

_ROBOT_HEALTH_SIGNAL_TEXT: dict[str, dict[str, str]] = {
    "battery_retention": {
        "en": "Battery capacity is declining",
        "de": "Akkuleistung lässt nach",
        "fr": "La capacité de la batterie diminue",
        "it": "La capacità della batteria sta diminuendo",
        "es": "La capacidad de la batería está disminuyendo",
        "nl": "Batterijcapaciteit neemt af",
        "pt": "A capacidade da bateria está diminuindo",
    },
    "nav_efficiency": {
        "en": "Navigation performance is below normal",
        "de": "Navigationsleistung unter Normal",
        "fr": "Les performances de navigation sont inférieures à la normale",
        "it": "Le prestazioni di navigazione sono sotto la norma",
        "es": "El rendimiento de navegación está por debajo de lo normal",
        "nl": "Navigatieprestaties onder normaal",
        "pt": "O desempenho de navegação está abaixo do normal",
    },
    "cleaning_speed_trend": {
        "en": "Cleaning time is trending up",
        "de": "Reinigungsdauer steigt",
        "fr": "Le temps de nettoyage augmente",
        "it": "Il tempo di pulizia è in aumento",
        "es": "El tiempo de limpieza está aumentando",
        "nl": "Schoonmaaktijd neemt toe",
        "pt": "O tempo de limpeza está aumentando",
    },
    "anomaly_rate": {
        "en": "Frequent unusual missions",
        "de": "Häufige ungewöhnliche Missionen",
        "fr": "Missions inhabituelles fréquentes",
        "it": "Missioni insolite frequenti",
        "es": "Misiones inusuales frecuentes",
        "nl": "Vaak ongebruikelijke missies",
        "pt": "Missões incomuns frequentes",
    },
    "stuck_rate": {
        "en": "Robot is getting stuck more often",
        "de": "Roboter bleibt häufiger stecken",
        "fr": "Le robot reste coincé plus souvent",
        "it": "Il robot si blocca più spesso",
        "es": "El robot se atasca con más frecuencia",
        "nl": "Robot blijft vaker vastzitten",
        "pt": "O robô fica preso com mais frequência",
    },
}

_ROBOT_HEALTH_SIGNAL_REC: dict[str, dict[str, str]] = {
    "battery_retention": {
        "en": "Consider replacing the battery",
        "de": "Akkuwechsel in Erwägung ziehen",
        "fr": "Envisagez de remplacer la batterie",
        "it": "Valuta la sostituzione della batteria",
        "es": "Considera reemplazar la batería",
        "nl": "Overweeg de batterij te vervangen",
        "pt": "Considere substituir a bateria",
    },
    "nav_efficiency": {
        "en": "Retrain the Smart Map",
        "de": "Smart Map neu trainieren",
        "fr": "Réentraînez la Smart Map",
        "it": "Riaddestra la Smart Map",
        "es": "Vuelve a entrenar el Smart Map",
        "nl": "Train de Smart Map opnieuw",
        "pt": "Treine novamente o Smart Map",
    },
    "cleaning_speed_trend": {
        "en": "Check the brushes and filter",
        "de": "Bürsten und Filter prüfen",
        "fr": "Vérifiez les brosses et le filtre",
        "it": "Controlla le spazzole e il filtro",
        "es": "Revisa los cepillos y el filtro",
        "nl": "Controleer de borstels en het filter",
        "pt": "Verifique as escovas e o filtro",
    },
    "anomaly_rate": {
        "en": "Review recent missions in the history",
        "de": "Letzte Missionen in der Historie prüfen",
        "fr": "Consultez les missions récentes dans l'historique",
        "it": "Controlla le missioni recenti nella cronologia",
        "es": "Revisa las misiones recientes en el historial",
        "nl": "Bekijk recente missies in de geschiedenis",
        "pt": "Revise as missões recentes no histórico",
    },
    "stuck_rate": {
        "en": "Check for obstacles in the cleaning area",
        "de": "Hindernisse im Reinigungsbereich prüfen",
        "fr": "Vérifiez les obstacles dans la zone de nettoyage",
        "it": "Controlla gli ostacoli nell'area di pulizia",
        "es": "Revisa si hay obstáculos en el área de limpieza",
        "nl": "Controleer op obstakels in het schoonmaakgebied",
        "pt": "Verifique obstáculos na área de limpeza",
    },
}


def _robot_health_plain_status(
    hass: Any, breakdown: dict[str, Any]
) -> tuple[str, str | None]:
    """v3.1.0 PLAIN-STATUS — derive (status_text, recommendation) from the
    robot_health_score breakdown's weakest_signal field.
    """
    lang = _plain_lang(hass)
    weakest = breakdown.get("weakest_signal")
    if weakest is None or weakest not in _ROBOT_HEALTH_SIGNAL_TEXT:
        return _ROBOT_HEALTH_GOOD[lang], None
    text = _ROBOT_HEALTH_SIGNAL_TEXT[weakest][lang]
    rec = _ROBOT_HEALTH_SIGNAL_REC[weakest][lang]
    return text, rec


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

    v3.1.0 MOP-SENSOR-SLUG-FIX: values are lowercase slugs (hassfest
    requires [a-z0-9-_]+ on select/sensor-enum translation_key state keys).
    Was "Dry"/"Wet"/"Unknown" (Capital-Case) before this change.
    """
    level = entity.vacuum_state.get("padWetness", {})
    if isinstance(level, dict):
        level = level.get("disposable") or level.get("reusable")
    if level is None:
        return "unknown"
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "unknown"
    if level == 1:
        return "dry"
    if level in (2, 3):
        return "wet"
    return "unknown"


# ── F3 — Mop tank status (RoombaSensor value function) ───────────────────────

def _mop_tank_status(entity: "IRobotEntity") -> StateType:
    """Return consolidated mop tank status enum from mopReady sub-fields.

    F3 -- priority: tank missing > lid open > fill needed > ready.
    Replaces four separate binary sensors with one actionable status.
    Returns "unknown" when mopReady key is absent entirely.

    v3.1.0 MOP-SENSOR-SLUG-FIX: values are lowercase underscore slugs
    (hassfest [a-z0-9-_]+ requirement). Was "Ready"/"Fill Tank"/"Lid Open"/
    "Tank Missing"/"Unknown" (Capital-Case, some with spaces) before this
    change — spaces are not valid in translation_key state keys at all.
    """
    state = entity.vacuum_state
    if "mopReady" not in state:
        return "unknown"
    ready = state["mopReady"]
    if not isinstance(ready, dict):
        return "unknown"
    if not ready.get("tankPresent", True):
        return "tank_missing"
    if not ready.get("lidClosed", True):
        return "lid_open"
    if ready.get("fillRequired", False):
        return "fill_tank"
    return "ready"


# ── F3b — Mop behavior / ARS (RoombaSensor value function) ───────────────────

# v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs (hassfest
# [a-z0-9-_]+ requirement). Was {15: "No Mop", 25: "Extended", ...}
# (Capital-Case, some with spaces) before this change.
_MOD_RANKS: dict[int, str] = {
    15: "no_mop",
    25: "extended",
    67: "standard",
    85: "deep",
}


def _mop_behavior(entity: "IRobotEntity") -> StateType:
    """Return Braava m6 Auto Replenishment System behavior mode.

    F3b -- derives behavior from rankOverlap when present; falls back to
    padDirtyPause / padDryAllowed / padWashAllowed flag combination.
    Absent for all vacuum robots.

    v3.1.0 MOP-SENSOR-SLUG-FIX: lowercase underscore slugs (hassfest
    [a-z0-9-_]+ requirement). Combination modes (e.g. "dirty_pause_dry")
    join with "_" instead of the old " + " separator — both the separator
    character (space) and the individual mode names were invalid as
    translation_key state keys. The full set of valid combinations is
    listed explicitly in the sensor descriptor's `options` (RoombaSensorDescription
    in SENSORS) — any combination this function can produce must have a
    matching entry there and in strings.json/translations, kept in sync
    manually since this is a small, fixed combinatorial set (2^2 = 4 dirty_pause ×
    {dry, wash} combinations plus the single-flag and rankOverlap cases).
    """
    state = entity.vacuum_state
    rank = state.get("rankOverlap")
    if rank is not None:
        return _MOD_RANKS.get(rank, "unknown")

    dirty_pause  = state.get("padDirtyPause",  0) == 1
    dry_allowed  = state.get("padDryAllowed",  0) == 1
    wash_allowed = state.get("padWashAllowed", 0) == 1

    if not dry_allowed and not wash_allowed:
        return "unknown"

    modes = []
    if dirty_pause:
        modes.append("dirty_pause")
    if dry_allowed:
        modes.append("dry")
    if wash_allowed:
        modes.append("wash")
    return "_".join(modes) if modes else "unknown"



# ── F5d — Battery capacity retention ─────────────────────────────────────────

def _battery_capacity_retention(entity: "IRobotEntity") -> StateType:
    """F5d — battery capacity as % of learned initial capacity.

    Denominator: store.baseline_estcap (first observed mAh after install or
    battery reset) so the sensor measures actual degradation of the installed
    battery — 100% = full health, <100% = degraded — independent of whether
    it is OEM or aftermarket.

    Falls back to profile.battery_mah (OEM nominal) only on first boot before
    the baseline is established, so the sensor has a value from day one.

    Also records the converted mAh value as the self-learning baseline so
    aftermarket detection can compare against profile.battery_mah × 1.15.

    Below 75% explains rising recharge fraction (F5c) without schedule changes.
    """
    store = entity._config_entry.runtime_data.maintenance_store
    profile = entity._config_entry.runtime_data.robot_profile
    if store is None or profile is None or profile.battery_mah == 0:
        return None
    capacity_mah = _estcap_to_mah(entity)
    if capacity_mah is None:
        return None
    # Record converted mAh (not raw BMS value) as self-learning baseline.
    # When the baseline is set for the first time, schedule a save so it
    # survives an HA restart (baseline is only set once — idempotent).
    if store.record_estcap_if_needed(capacity_mah):
        entity.hass.async_create_task(
            store.async_save(entity.hass, entity._config_entry.entry_id),
            name="roomba_plus_estcap_baseline_save",
        )
    # Use learned baseline when available; OEM nominal only as cold-start fallback
    denominator = store.baseline_estcap if store.baseline_estcap else float(profile.battery_mah)
    return round(capacity_mah / denominator * 100, 1)


# ── F5g — Estimated battery end-of-life ──────────────────────────────────────

_EOL_THRESHOLD = 65.0  # % — typical lithium end-of-life

# v3.1.0 L9-BATTERY — fallback values used only in the (abnormal) case where
# entity._config_entry.runtime_data.robot_profile_store is None. Mirrors
# RobotProfileStore's own _ESTCAP_FALLBACK_MIN_RATE / sanity cap constants
# (robot_profile_store.py) — kept as a separate copy rather than imported to
# avoid reaching into that module's private (underscore-prefixed) constants.
# Keep these two values in sync with their robot_profile_store.py counterparts.
_ESTCAP_FALLBACK_MIN_RATE = 0.01
_ESTCAP_REMAINING_CYCLES_SANITY_CAP = 10_000


def _battery_age_days(entity: "IRobotEntity") -> StateType:
    """Return battery age in days from batInfo.mDate (i/s-series only).

    mDate format: 'YYYY-M-D' (e.g. '2019-5-17' or '2022-10-24').
    Returns None when mDate is absent or unparseable.
    """
    bat_info = entity.vacuum_state.get("batInfo") or {}
    mdate_str = bat_info.get("mDate")
    if not mdate_str:
        return None
    try:
        from datetime import date
        parts = [int(p) for p in mdate_str.split("-")]
        manufacture_date = date(parts[0], parts[1], parts[2])
        return (dt_util.now().date() - manufacture_date).days
    except (ValueError, IndexError, TypeError):
        return None


def _estcap_to_mah(entity: "IRobotEntity") -> float | None:
    """Return the robot's current estimated capacity in mAh, applying the
    9-series BMS scale when the profile requires it.

    For i/s/j/e/6 series (scale=1.0): raw estCap == mAh directly.
    For 9-series old firmware: raw estCap is BMS-scaled.
      Li-ion (nLithChrg present and > 0): raw ÷ 3.73
      NiMH   (nNimhChrg present and > 0): raw ÷ 1.87
    Chemistry is detected at runtime via nNimhChrg / nLithChrg fields.

    Returns None when estCap is absent or zero.
    """
    raw = entity.battery_stats.get("estCap")
    if not raw:
        return None
    profile = entity._config_entry.runtime_data.robot_profile
    if profile is None or (profile.estcap_scale_liion == 1.0
                           and profile.estcap_scale_nimh == 1.0):
        return float(raw)
    # 9-series: detect current chemistry from cycle-count fields.
    # nNimhChrg and nLithChrg are lifetime counters — both may be > 0 when
    # the user has replaced the OEM Li-ion pack with an NiMH aftermarket battery.
    # In that case nLithChrg > 0 still reflects the OEM period.
    # Heuristic: if any NiMH cycles have been recorded, assume NiMH is current.
    # This is correct for the common cases:
    #   - OEM Li-ion only:           nLithChrg > 0, nNimhChrg = 0 → Li-ion ✓
    #   - NiMH only (or swapped):    nNimhChrg > 0                 → NiMH  ✓
    nimh_cycles = entity.battery_stats.get("nNimhChrg") or 0
    if nimh_cycles > 0:
        scale = profile.estcap_scale_nimh
    else:
        scale = profile.estcap_scale_liion   # default: OEM Li-ion
    return round(float(raw) / scale)  # mAh to nearest integer


def _total_energy_consumed_kwh(entity: "IRobotEntity") -> StateType:
    """F12e — total energy consumed in kWh (HA Energy dashboard eligible).

    Formula: actual_mAh × voltage × cycle_count / 1_000_000 → kWh
    For 9-series: raw estCap is divided by the BMS scale before use.
    Cycle count is also chemistry-aware: uses nNimhChrg when NiMH is detected
    (nNimhChrg > 0), nLithChrg otherwise — important when the user has replaced
    the OEM Li-ion pack with NiMH aftermarket (nLithChrg stays at the OEM count).
    """
    actual_mah = _estcap_to_mah(entity)
    if actual_mah is None:
        return None

    # v2.9.0 — now shared with callbacks.py (DAILY-DIGEST) via
    # const.active_charge_cycles(); same chemistry-aware priority as before.
    cycles = active_charge_cycles(entity.battery_stats)

    if not cycles:
        return None
    profile = entity._config_entry.runtime_data.robot_profile
    voltage = profile.battery_voltage if profile is not None else 14.8
    return round(actual_mah * voltage * int(cycles) / 1_000_000, 3)


def _estimated_battery_eol(entity: "IRobotEntity") -> StateType:
    """F5g — days remaining until battery capacity falls to EOL_THRESHOLD (65%).

    Linear extrapolation from current degradation rate:
      degradation_rate = (100 - current_pct) / current_cycles  (% per cycle)
      remaining_cycles = (current_pct - 65) / degradation_rate
      remaining_days   ≈ remaining_cycles (at 1 charge/day)

    v3.1.0 L9-BATTERY: the raw linear extrapolation above is unreliable when
    estCap is still oscillating within normal measurement noise rather than
    showing genuine degradation — field data (Thonno's i7+, 8 estCap readings
    over 70 missions on a near-new battery) showed exactly this: a tiny
    positive degradation_rate driven by noise alone projected to ~354 years
    remaining, which is technically a correct computation but useless and
    misleading as a user-facing number. RobotProfileStore now learns this
    robot's own estCap reading-to-reading noise floor and only trusts
    degradation_rate when it clearly exceeds that floor (see
    degradation_rate_is_significant()). A sanity cap on the final result
    catches anything that still slips through implausibly large.

    Returns 0 when capacity is already below threshold (replace now).
    Returns None when insufficient data, or when degradation_rate is not
    yet distinguishable from this robot's own measurement noise.
    """
    store = entity._config_entry.runtime_data.maintenance_store
    # Guard against both None and 0 — a corrupted or hand-edited persisted
    # store could hold baseline_estcap: 0, which `is None` would not catch and
    # which would raise ZeroDivisionError at the current_pct computation below.
    if store is None or not store.baseline_estcap:
        return None

    # Use converted mAh to match baseline_estcap units (set from _estcap_to_mah)
    capacity_mah = _estcap_to_mah(entity)
    cycles = (
        entity.battery_stats.get("nLithChrg")
        or entity.battery_stats.get("nNimhChrg")
        or entity.battery_stats.get("nAvail")
    )
    if capacity_mah is None or not cycles:
        return None

    current_pct = capacity_mah / store.baseline_estcap * 100
    if current_pct <= _EOL_THRESHOLD:
        return 0

    degradation_rate = (100.0 - current_pct) / max(int(cycles), 1)
    if degradation_rate <= 0:
        return None

    # v3.1.0 L9-BATTERY — self-calibration gate
    rps = getattr(entity._config_entry.runtime_data, "robot_profile_store", None)
    if rps is not None:
        if not rps.degradation_rate_is_significant(degradation_rate, int(cycles)):
            return None
        remaining_cycles = (current_pct - _EOL_THRESHOLD) / degradation_rate
        remaining_cycles = rps.cap_remaining_cycles(remaining_cycles)
        if remaining_cycles is None:
            return None
        return max(0, round(remaining_cycles))

    # No RobotProfileStore at all (shouldn't normally happen, but handled
    # defensively) — fall back to the same conservative absolute threshold
    # degradation_rate_is_significant() uses when its own noise baseline
    # isn't ready yet, just without the store-bound cap helper.
    if degradation_rate < _ESTCAP_FALLBACK_MIN_RATE:
        return None
    remaining_cycles = (current_pct - _EOL_THRESHOLD) / degradation_rate
    if remaining_cycles > _ESTCAP_REMAINING_CYCLES_SANITY_CAP:
        return None
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
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        value_fn=lambda e: None,  # computed in RoombaSensor.native_value
        threshold_fn=lambda e: e._config_entry.options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS),
    ),
    RoombaSensorDescription(
        key="brush_remaining_hours",
        translation_key="brush_remaining_hours",
        name="Maintenance – Brushes",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
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
            # 9-series (v2.4.x firmware): nLithChrg + nNimhChrg present in bbchg3.
            # Sum both because mixed chemistry replacements accumulate in each field.
            (e.battery_stats.get("nLithChrg") or 0) + (e.battery_stats.get("nNimhChrg") or 0)
            if "nLithChrg" in e.battery_stats
            # i/s-series (lewis/soho firmware): nLithChrg/nNimhChrg absent from bbchg3.
            # batInfo.cCount is the definitive BMS chip cycle counter (confirmed June 2026:
            # i7+ cCount=779 vs i8+ cCount=276 — correlates correctly with mission count).
            # nAvail on i/s-series has different semantics and is NOT a cycle counter.
            else e.vacuum_state.get("batInfo", {}).get("cCount")
        ),
    ),

    # GROUP 4 — Statistics (DIAGNOSTIC, enabled)

    # F12e — total energy consumed (HA Energy dashboard eligible)
    # F12e — total energy consumed.
    # RF0: scale applied via _total_energy_consumed_kwh for 9-series BMS correction.
    # Gate: bbchg3.estCap present.  batteryType field is unreliable (contains
    # iRobot part numbers, not chemistry strings) — chemistry detected at runtime
    # via nNimhChrg / nLithChrg cycle-count fields inside the helper.
    RoombaSensorDescription(
        key="total_energy_consumed",
        translation_key="total_energy_consumed",
        name="Total energy consumed",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_total_energy_consumed_kwh,
    ),

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

    # LOCAL-RATE — lifetime completion rate from bbmssn (no cloud required, all robots)
    RoombaSensorDescription(
        key="lifetime_completion_rate",
        translation_key="lifetime_completion_rate",
        name="Missions – Lifetime completion rate",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda e: bool(e.mission_stats.get("nMssn")),
        value_fn=lambda e: (
            round(
                e.mission_stats.get("nMssnOk", 0)
                / max(e.mission_stats.get("nMssn", 1), 1)
                * 100,
                1,
            )
            if e.mission_stats.get("nMssn")
            else None
        ),
        extra_attributes_fn=lambda e: {
            "ok":             e.mission_stats.get("nMssnOk"),
            "cancelled":      e.mission_stats.get("nMssnC"),
            "failed":         e.mission_stats.get("nMssnF"),
            "total":          e.mission_stats.get("nMssn"),
            "avg_mission_min": e.mission_stats.get("aMssnM"),
            "avg_cycle_min":   e.mission_stats.get("aCycleM"),
        },
    ),

    # BAT-INFO sensors (i/s-series only — batInfo absent on 9-series firmware)

    RoombaSensorDescription(
        key="battery_cycle_count_bms",
        translation_key="battery_cycle_count_bms",
        name="Battery – BMS cycle count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda e: e.vacuum_state.get("batInfo") is not None,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("batInfo", {}).get("cCount"),
        extra_attributes_fn=lambda e: {
            "manufacturer": e.vacuum_state.get("batInfo", {}).get("mName"),
            "is_oem":       e.vacuum_state.get("batInfo", {}).get("mName") == "PanasonicEnergy",
        },
    ),

    RoombaSensorDescription(
        key="battery_age_days",
        translation_key="battery_age_days",
        name="Battery – Age",
        native_unit_of_measurement="d",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_fn=lambda e: bool(
            (e.vacuum_state.get("batInfo") or {}).get("mDate")
        ),
        value_fn=lambda e: _battery_age_days(e),
    ),

    # GROUP 5 — Opt-in (DIAGNOSTIC, disabled by default)

    RoombaSensorDescription(
        key="total_cleaned_area",
        translation_key="total_cleaned_area",
        name="Lifetime cleaned area",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        # v2.9.0 (J) — same AREA-ZERO-FIX family as v2.8.2's
        # mission.get("sqft") or None fix in callbacks.py: confirmed on the
        # same hardware (980 9-series + aftermarket NiMH battery) that
        # firmware can report a literal 0 instead of omitting the field
        # even with genuine cleaning history. `or None` treats a literal 0
        # as "no reliable value" — a falsely-missing display beats a
        # confidently wrong one.
        #
        # Field report investigated: this value (176 m²) appeared smaller
        # than cleaning_analytics_30d's window-sum (683 m² over its cloud
        # API window). Initially suspected a battery-replacement-triggered
        # counter reset as the explanation — DISPROVEN, not just
        # unconfirmed: the integration's "Reset battery" button
        # (maintenance_store.reset_battery()) only writes battery_reset_hr/
        # battery_reset_at to our OWN local MaintenanceStore for our own
        # wear-rate bookkeeping. It sends no MQTT command and has no path
        # to the robot's firmware at all — it cannot possibly reset
        # bbrun.sqft. The two values being non-comparable (cleaning_
        # analytics_30d explicitly sums "for the API window (not
        # lifetime)" per cloud_coordinator.py's own docstring) still holds,
        # but the remaining gap beyond that has NO confirmed explanation.
        # Left as an open, honestly-unexplained discrepancy rather than
        # attributed to a mechanism that was checked and does not exist.
        # v2.9.0 (J) — SOURCE CHANGE. The robot's own onboard lifetime
        # counter (bbrun.sqft / runtimeStats.sqft) was field-confirmed
        # (via a months-old diagnostics snapshot showing 1882 sqft ≈
        # 174.8 m², essentially the same as a recent reading of 176 m²)
        # to barely change over a very long period, while every OTHER
        # bbrun.* counter (nPanics, nStuck, etc.) keeps incrementing
        # normally. Investigated against dorita980/roombapy/Roomba980-
        # Python reference implementations: sqft/hr/min are reported
        # atomically alongside the actively-incrementing counters in the
        # SAME bbrun object — no documented firmware quirk explains this,
        # and our own merge logic (entity.py's run_stats, which could have
        # let a stale runtimeStats override a fresh bbrun value) was ruled
        # out: this robot's master_state_keys never contains "runtimeStats"
        # at all. The mechanism remains genuinely unexplained.
        #
        # Rather than keep trusting a counter we cannot independently
        # verify, the primary value uses MissionArchive's cumulative_sqft
        # — a persistent running total over EVERY mission ever archived
        # (not capped at MAX_RECORDS=800; see the accumulator's own
        # docstring in mission_archive.py for why a live re-sum over the
        # currently-held records would be wrong for any robot with more
        # lifetime missions than that). Built entirely from cloud
        # per-mission data, which does NOT share whatever mechanism
        # affects bbrun.sqft (confirmed non-zero, plausible per-mission
        # values throughout today's investigation). Still not necessarily
        # a true "since the robot was new" total (limited by however far
        # back the cloud account itself retains mission history, and only
        # starts accumulating from whenever cloud credentials were first
        # configured), but self-consistent, independently verifiable, and
        # immune to the bbrun staleness
        # question entirely.
        #
        # Both sources are only LOWER BOUNDS on the true lifetime total —
        # the archive can be incomplete (cloud credentials added only
        # after months of local-only use), and the onboard counter can
        # freeze, but whatever it captured before freezing was real,
        # already-cleaned area. A genuine lifetime total can never DECREASE
        # relative to either source, so the displayed value is always the
        # larger of the two — never let a more-complete archive disagree
        # downward from a frozen-but-still-real onboard reading, and vice
        # versa.
        #
        # Uses MissionArchive.cumulative_sqft (a persistent running total,
        # incremented once per newly-archived mission BEFORE any FIFO
        # trim) rather than summing all_derived_oldest_first() live — a
        # robot with more than MAX_RECORDS (800) lifetime missions would
        # otherwise see this number DECREASE every time an old mission
        # ages out of the FIFO-capped list, which is exactly the kind of
        # "lifetime total going backwards" this fix exists to prevent.
        value_fn=lambda e: (
            lambda arc: max(
                (
                    round(arc.cumulative_sqft * SQFT_TO_M2, 1)
                    if arc is not None and arc.cumulative_sqft > 0
                    else None
                ) or 0.0,
                (
                    round(sqft * SQFT_TO_M2, 1)
                    if (sqft := e.run_stats.get("sqft"))
                    else None
                ) or 0.0,
            ) or None
        )(getattr(e._config_entry.runtime_data, "mission_archive", None)),
        # v2.9.0 (J) — bbrun's raw reading + staleness tracking kept as
        # attributes for comparison/diagnosis, now that it's no longer the
        # primary value. RobotProfileStore staleness fields are updated in
        # __init__.py's _async_update_robot_profile_store, not here —
        # value_fn/extra_attributes_fn stay side-effect-free, consistent
        # with every other sensor in this module.
        extra_attributes_fn=lambda e: (
            lambda rps, arc: {
                "onboard_counter_m2": (
                    round(sqft * SQFT_TO_M2, 1)
                    if (sqft := e.run_stats.get("sqft"))
                    else None
                ),
                "onboard_counter_last_changed_at": (
                    rps.lifetime_sqft_last_changed_at if rps is not None else None
                ),
                "onboard_counter_days_unchanged": (
                    round(d, 1)
                    if rps is not None
                    and (d := rps.lifetime_sqft_days_unchanged) is not None
                    else None
                ),
                "archived_mission_count": (
                    arc.record_count if arc is not None else 0
                ),
            }
        )(
            getattr(e._config_entry.runtime_data, "robot_profile_store", None),
            getattr(e._config_entry.runtime_data, "mission_archive", None),
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
        value_fn=lambda e: _parse_netinfo_addr(
            (e.vacuum_state.get("netinfo") or {}).get("addr")
        ),
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
        available_fn=lambda e: e.clean_mission_status.get("phase") in _ACTIVE_PHASES,
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
        available_fn=lambda e: e.clean_mission_status.get("phase") in
            ("run", "hmMidMsn", "evac", "charge", "hmPostMsn")
            and e.clean_mission_status.get("cycle") not in (None, "none"),
    ),

    # SC2 (v2.7.0): TIMESTAMP is the preferred variant — enabled by default.
    # mission_recharge_minutes (numeric) is disabled below.
    RoombaSensorDescription(
        key="mission_recharge_time",
        translation_key="mission_recharge_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("rechrgTm")),
    ),

    # SC2 (v2.7.0): TIMESTAMP is the preferred variant — enabled by default.
    # mission_expire_minutes (numeric) was already disabled in v2.6.2.
    RoombaSensorDescription(
        key="mission_expire_time",
        translation_key="mission_expire_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        # SC2 (v2.7.0): disabled by default — use mission_recharge_time (TIMESTAMP)
        # which HA renders natively as a countdown. Kept for users with existing
        # automations; will be removed in v3.0.
        entity_registry_enabled_default=False,
        # rechrgM is pre-computed by 980/900-series firmware.
        # lewis firmware (i/s/j-series) sends rechrgTm (Unix timestamp) and
        # leaves rechrgM=0. Fall back to computing remaining minutes from rechrgTm
        # so mid-mission recharge time is reported correctly on all robots.
        value_fn=lambda e: _recharge_minutes_remaining(e.clean_mission_status),
        # Unavailable (not Unknown) when no mid-mission recharge is active.
        available_fn=lambda e: bool(
            e.clean_mission_status.get("rechrgTm")
            or e.clean_mission_status.get("rechrgM")
        ),
    ),
    RoombaSensorDescription(
        key="mission_expire_minutes",
        translation_key="mission_expire_minutes",
        name="Mission – Time until expiry",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # expireM is pre-computed by 980/900-series firmware.
        # On 900-series (EPHEMERAL): expireTm == rechrgTm — mirrors the recharge
        # countdown, does not represent a separate mission deadline.
        # On i/s/j-series (SMART): expireTm can differ from rechrgTm.
        # Disabled by default to avoid confusion; SMART users can enable it.
        entity_registry_enabled_default=False,
        value_fn=lambda e: _expire_minutes_remaining(e.clean_mission_status),
        available_fn=lambda e: bool(
            e.clean_mission_status.get("expireTm")
            or e.clean_mission_status.get("expireM")
        ),
    ),
    RoombaSensorDescription(
        key="mission_id",
        translation_key="mission_id",
        name="Mission – ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # missionId: stable string across all recharge cycles of one mission.
        # Populated only on i/s/j-series (older firmware may not send it).
        filter_fn=lambda s: "missionId" in (s.get("cleanMissionStatus") or {}),
        value_fn=lambda e: e.clean_mission_status.get("missionId") or None,
    ),
    # Schedule sensor (all models with cleanSchedule2 or cleanSchedule)

    RoombaSensorDescription(
        key="next_clean",
        translation_key="next_clean",
        name="Status – Next clean",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
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
            (e.vacuum_state.get("dock") or {}).get("state", -2), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="dock_firmware_version",
        translation_key="dock_firmware_version",
        name="Dock firmware version",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "fwVer" in (s.get("dock") or {}),
        value_fn=lambda e: (e.vacuum_state.get("dock") or {}).get("fwVer"),
    ),
    RoombaSensorDescription(
        key="dock_tank_level",
        translation_key="dock_tank_level",
        name="Dock tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in (s.get("dock") or {}),
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
        options=["dry", "wet", "unknown"],
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
        options=["ready", "fill_tank", "lid_open", "tank_missing", "unknown"],
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
            "no_mop", "extended", "standard", "deep",
            "dirty_pause", "dry", "wash",
            "dirty_pause_dry", "dirty_pause_wash", "dry_wash",
            "dirty_pause_dry_wash", "unknown",
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

    # ── IA74-MAINT (v2.7.0) — calendar-based inspect timestamps ──────────────
    # Wheel module, charging contacts, and bin are cleaned on a calendar cadence
    # rather than an hours-of-use basis.  These sensors expose the last-cleaned
    # wall-clock timestamp so dashboards and reminders can be built from them.

    RoombaSensorDescription(
        key="wheel_last_cleaned",
        translation_key="wheel_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Wheel last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at is not None
        ),
    ),
    RoombaSensorDescription(
        key="contact_last_cleaned",
        translation_key="contact_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Contacts last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.contact_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.contact_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.contact_cleaned_at is not None
        ),
    ),
    RoombaSensorDescription(
        key="bin_last_cleaned",
        translation_key="bin_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Bin last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.bin_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.bin_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.bin_cleaned_at is not None
        ),
    ),

    # ── v1.8.0 L1 — Mission Log ───────────────────────────────────────────────

    RoombaSensorDescription(
        key="clean_streak",
        translation_key="clean_streak",
        name="Missions – Clean streak",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,  # PRIMARY-SLIM (v3.1.0): pure statistic, not daily-use
        value_fn=lambda e: _mission_store_value(e, lambda s: s.clean_streak()),
    ),
    RoombaSensorDescription(
        key="last_mission_team_id",
        translation_key="last_mission_team_id",
        name="Missions – Last mission team ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,  # niche — Imprint Link team-clean users only
        value_fn=lambda e: _mission_store_value(e, _last_mission_team_id),
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
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: has_pose(s),   # 600-series reports no sqft
        value_fn=lambda e: _mission_store_value(e, _area_cleaned_today),
    ),
    RoombaSensorDescription(
        key="last_mission_result",
        translation_key="last_mission_result",
        name="Missions – Last result",
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        value_fn=lambda e: _mission_store_value(
            e, lambda s: s.latest().get("result") if s.latest() else None
        ),
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store._records
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
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store._records
        ),
    ),

    # ── v1.8.0 L3 — Error Intelligence ───────────────────────────────────────

    RoombaSensorDescription(
        key="last_error_code",
        translation_key="last_error_code",
        name="Error – Last code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_code_value,
        available_fn=lambda e: bool(
            e.vacuum_state.get("cleanMissionStatus", {}).get("error", 0)
            or e._config_entry.runtime_data.last_error_code is not None
        ),
    ),
    RoombaSensorDescription(
        key="last_error_at",
        translation_key="last_error_at",
        name="Error – Last time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_at_value,
        available_fn=lambda e: bool(e._config_entry.runtime_data.last_error_at),
    ),
    RoombaSensorDescription(
        key="last_error_zone",
        translation_key="last_error_zone",
        name="Error – Last zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        # No filter_fn — created for all robots.
        # SMART: resolved from lastCommand.regions at mission start.
        # EPHEMERAL: resolved from ZoneStore at mission start.
        value_fn=lambda e: e._config_entry.runtime_data.last_error_zone,
        available_fn=lambda e: e._config_entry.runtime_data.last_error_zone is not None,
    ),
    RoombaSensorDescription(
        key="stuck_count_30d",
        translation_key="stuck_count_30d",
        name="Error – Stuck events (30 days)",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: len(s.query(30, result=s.STUCK_RESULTS))
        ),
    ),
    RoombaSensorDescription(
        key="problem_zone",
        translation_key="problem_zone",
        name="Error – Problem zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: has_pose(s),   # requires zone tracking — excludes 600-series
        value_fn=_problem_zone_value,
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store.query(30, result=e._config_entry.runtime_data.mission_store.STUCK_RESULTS)
        ),
    ),

    # ── v3.0.0 L3-FIX — Consecutive anomalous missions ────────────────────────
    #
    # Exposes MissionStore.consecutive_anomalous as a standalone sensor.
    # Disabled by default — only the companion Card and Automations consume it.
    # Card C5-ANOMALY banner triggers at ≥3 (two consecutive anomalies can be
    # coincidence; three are a pattern).
    RoombaSensorDescription(
        key="consecutive_mission_anomalies",
        translation_key="consecutive_mission_anomalies",
        name="Error – Consecutive anomalous missions",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: (
            e._config_entry.runtime_data.mission_store.consecutive_anomalous
            if e._config_entry.runtime_data.mission_store is not None
            else None
        ),
        available_fn=lambda e: e._config_entry.runtime_data.mission_store is not None,
        # v3.2.0 ANOMALY-EXPLAIN — surfaces the mission id to pass straight
        # into the explain_mission service/REST endpoint, so the card (or
        # an automation) doesn't need to separately query mission history
        # just to find out which mission this count is even about.
        extra_attributes_fn=lambda e: (
            {"last_mission_id": e._config_entry.runtime_data.mission_store.latest().get("id")}
            if e._config_entry.runtime_data.mission_store is not None
            and e._config_entry.runtime_data.mission_store.latest() is not None
            else {}
        ),
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
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: not is_mop(s),
        value_fn=_filter_days_until_due,
    ),
    RoombaSensorDescription(
        key="brush_days_until_due",
        translation_key="brush_days_until_due",
        name="Maintenance – Brush days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
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
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_estcap_to_mah,   # RF0: divides by BMS scale for 9-series
    ),
    RoombaSensorDescription(
        key="nav_panics",
        translation_key="nav_panics",
        name="Navigation panic events",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nPanics" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nPanics"),
    ),
    RoombaSensorDescription(
        key="cliff_events_front",
        translation_key="cliff_events_front",
        name="Cliff events – Front",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsF" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nCliffsF"),
    ),
    RoombaSensorDescription(
        key="cliff_events_rear",
        translation_key="cliff_events_rear",
        name="Cliff events – Rear",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsR" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nCliffsR"),
    ),

    # FIELD-SENSORS (v2.8.0) — 5 additional diagnostic sensors from 3-robot
    # MQTT field analysis (nCliffsF / nCliffsR / nPanics already above).
    #
    # Navigation subsystem (bbnav) — 9-series only, absent on i/s/j-series:
    RoombaSensorDescription(
        key="nav_landmark_quality",
        translation_key="nav_landmark_quality",
        name="Navigation landmark quality",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "aMtrack" in (s.get("bbnav") or {}),
        value_fn=lambda e: e.nav_stats.get("aMtrack"),
    ),
    RoombaSensorDescription(
        key="nav_good_landmarks",
        translation_key="nav_good_landmarks",
        name="Navigation good landmarks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nGoodLmrks" in (s.get("bbnav") or {}),
        value_fn=lambda e: e.nav_stats.get("nGoodLmrks"),
    ),
    # Run statistics (bbrun / runtimeStats) — i/s-series only, absent on 9-series:
    RoombaSensorDescription(
        key="optical_dirt_detections",
        translation_key="optical_dirt_detections",
        name="Optical dirt detections",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nOpticalDD" in (s.get("bbrun") or {})
            or "nOpticalDD" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nOpticalDD"),
    ),
    RoombaSensorDescription(
        key="piezo_dirt_detections",
        translation_key="piezo_dirt_detections",
        name="Piezo dirt detections",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nPiezoDD" in (s.get("bbrun") or {})
            or "nPiezoDD" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nPiezoDD"),
    ),
    RoombaSensorDescription(
        key="nav_orientations",
        translation_key="nav_orientations",
        name="Navigation orientations",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nOrients" in (s.get("bbrun") or {})
            or "nOrients" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nOrients"),
    ),

    # DOCK-HEALTH (v2.8.0) — dock contact health counters from bbchg.
    # Field confirmed present on i/s-series (lewis/soho firmware) and
    # some 9-series firmware variants.  Thresholds are conservative heuristics
    # pending community field data calibration (see COMM-A roadmap).
    RoombaSensorDescription(
        key="dock_contact_chatters",
        translation_key="dock_contact_chatters",
        name="Dock contact chatters",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nChatters" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nChatters"),
    ),
    RoombaSensorDescription(
        key="dock_knockoffs",
        translation_key="dock_knockoffs",
        name="Dock knockoffs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nKnockoffs" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nKnockoffs"),
    ),
    RoombaSensorDescription(
        key="dock_charge_aborts",
        translation_key="dock_charge_aborts",
        name="Dock charge aborts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nAborts" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nAborts"),
    ),

    # F5d -- battery capacity retention (% of baseline estCap)
    RoombaSensorDescription(
        key="battery_capacity_retention",
        translation_key="battery_capacity_retention",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_battery_capacity_retention,
    ),

    # F5g -- estimated battery end-of-life (days remaining until 65% threshold)
    # SEN2 (v2.7.0): state_class removed — this is a heuristic prediction, not
    # a physical measurement. HA should not build long-term statistics from it.
    RoombaSensorDescription(
        key="estimated_battery_eol",
        translation_key="estimated_battery_eol",
        name="Maintenance – Est. battery end of life",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_estimated_battery_eol,
        # available_fn calls the function directly: covers all None conditions
        # (no baseline, no cycles, no degradation yet).
        available_fn=lambda e: _estimated_battery_eol(e) is not None,
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
        # SC1 (v3.0): CloudRawSensor descriptors removed — deprecated sensors
        # deactivated. Consolidated replacements: cleaning_performance,
        # cleaning_analytics_30d, wifi_health, event_counts_30d.
        pass

    # F12d — recent_edge_coverage_ratio (map-capable robots with GridStore)
    if data.grid_store is not None and data.map_capability.value != "none":
        entities.append(RoombaEdgeCoverageSensor(roomba, blid, config_entry))

    # IA74-LP (v2.6.0) — learning_percentage (SMART + cloud only)
    if data.map_capability.value == "smart" and data.cloud_coordinator is not None:
        entities.append(RoombaLearningPercentageSensor(roomba, blid, config_entry))

    # IA74-ZONE (v2.6.0) — zone summary (SMART + cloud only)
    if data.map_capability.value == "smart" and data.cloud_coordinator is not None:
        entities.append(RoombaZoneSummarySensor(roomba, blid, config_entry))

    # F12a — optimal_clean_window sensor (presence scheduling active)
    if data.presence_manager is not None:
        entities.append(RoombaOptimalCleanWindow(roomba, blid, config_entry))

    # MP1 (v2.6.0) — mission progress (SMART + cloud + timer store)
    if (
        data.map_capability.value == "smart"
        and data.cloud_coordinator is not None
        and data.mission_timer_store is not None
    ):
        entities.append(RoombaMissionProgress(roomba, blid, config_entry))

    # SC1 (v2.7.0) — consolidated analytics sensors (cloud credentials required)
    if data.has_cloud:
        cc = data.cloud_coordinator  # type: ignore[union-attr]
        entities.extend([
            RoombaCleaningPerformanceSensor(roomba, blid, cc, config_entry),
            RoombaCleaningAnalytics30dSensor(roomba, blid, cc, config_entry),
            RoombaWifiHealthSensor(roomba, blid, cc, config_entry),
            RoombaEventCounts30dSensor(roomba, blid, cc, config_entry),
        ])
        # BAT-ARCH (v2.8.0) — archive-sourced WiFi + charge sensors
        entities.extend([
            RoombaWifiLastChannelSensor(roomba, blid, cc, config_entry),
            RoombaWifiChannelStabilitySensor(roomba, blid, cc, config_entry),
            RoombaMissionsPerChargeSensor(roomba, blid, cc, config_entry),
        ])

    # L8 (v2.7.0) — composite robot health score (cloud credentials required)
    if data.has_cloud and data.cloud_coordinator is not None:
        entities.append(
            RoombaRobotHealthSensor(roomba, blid, data.cloud_coordinator, config_entry)
        )
        # v3.2.0 L10 — self-calibrating health score trend, same gate as L8
        # itself (the trend has nothing to track without the score it's
        # derived from).
        entities.append(
            RoombaHealthScoreTrendSensor(roomba, blid, data.cloud_coordinator, config_entry)
        )

    # Raw state sensor: opt-in, always created, exposes full MQTT state as attributes.
    entities.append(RawStateSensor(roomba, blid))

    # v2.8.3 FW-SENSOR — firmware version, always created (universal across all robot families).
    entities.append(RoombaFirmwareVersionSensor(roomba, blid))

    # v2.9.0 INTEG-HEALTH — integration health meta-sensor, always created.
    entities.append(RoombaIntegrationHealthSensor(roomba, blid, config_entry))

    # v3.1.0 LAST-MISSION-SUMMARY — always created; shows None when no record exists.
    entities.append(RoombaLastMissionSummarySensor(roomba, blid, config_entry))

    # v3.1.0 ROOM-CLEANING-HISTORY — per-room last-clean timestamps (SMART, cloud).
    # Only created when mission_store is available; no tier gate — any robot that
    # accumulates room data in last_cleaned_rooms will populate this sensor.
    if data.mission_store is not None:
        entities.append(RoombaRoomCleaningHistorySensor(roomba, blid, config_entry))

    # v3.1.0 ROOM-SIZE — per-room floor area in m² from UMF polygons (SMART only).
    if data.umf_aligner is not None:
        entities.append(RoombaRoomAreasSensor(roomba, blid, config_entry))

    # v3.2.0 ROOM-ACCESS — same gate as ROOM-SIZE (room polygons come from
    # the same UmfAligner source).
    if data.umf_aligner is not None:
        entities.append(RoombaRoomAccessibilityScoresSensor(roomba, blid, config_entry))

    # v3.2.0 RESET-DIAGNOSTICS — reset-cause breakdown, previously unread.
    if "bbrstinfo" in state:
        entities.append(RoombaResetDiagnosticsSensor(roomba, blid))

    # v3.1.0 L9-MAP — relocalisation rate (SMART only, mssnNavStats confirmed
    # absent on EPHEMERAL tier).
    if data.umf_aligner is not None:
        entities.append(RoombaRelocalisationRateSensor(roomba, blid, config_entry))

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
        self._unsub_tick: Callable[[], None] | None = None

    @property
    def suggested_object_id(self) -> str:
        """Override: use description key directly (more explicit than uid-strip)."""
        return self.entity_description.key


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
    def available(self) -> bool:
        """Return False when available_fn signals sensor not applicable right now."""
        if self.entity_description.available_fn is not None:
            if not self.entity_description.available_fn(self):
                return False
        return super().available

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

        # F6f — battery contact / bus-communication anomaly check, fed
        # directly from the same batPct value this sensor exposes (no
        # separate caching needed — the check function reads state itself).
        elif key == "battery":
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_battery_contact_issue
                self.hass.async_create_task(
                    async_check_battery_contact_issue(self.hass, self._config_entry),
                    name="roomba_plus_f6f_battery_contact_check",
                )

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose sensor-specific attributes used by the Lovelace card."""
        key = self.entity_description.key
        # v2.7.1: extra_attributes_fn takes priority over generic attribute logic
        extra_fn = self.entity_description.extra_attributes_fn
        if extra_fn is not None:
            return extra_fn(self)
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
        # SC1 (v2.7.0): disabled by default — superseded by cleaning_analytics_30d.
        entity_registry_enabled_default=False,
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
        # SC1 (v2.7.0): disabled by default — superseded by cleaning_analytics_30d.
        entity_registry_enabled_default=False,
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
    # When set, entity is unavailable (not unknown) when fn returns False.
    # Receives the CloudRawSensor entity so the lambda can access both
    # coordinator.raw_records and runtime_data (e.g. mission_store).
    available_fn: Callable[[Any], bool] | None = field(default=None)


# ── F5 — Performance intelligence (RoombaSensor + consolidated functions) ───
# SC1 (v3.0): _raw_dirt_density_attrs, _make_coverage_pct_fn removed — only
# used by the now-deactivated CLOUD_RAW_SENSORS descriptors.
# The functions below are KEPT because they are also used by the consolidated
# replacement sensors (cleaning_performance, event_counts_30d).

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
    """Return the pauseId from the most recent failed mission record."""
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            pause_id = int(r.get("pauseId", 0) or 0)
            return pause_id if pause_id > 0 else None
    return None


def _raw_cloud_last_error_time(records: list[dict[str, Any]]) -> StateType:
    """Return the end timestamp of the most recent failed mission as a datetime."""
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


import statistics as _statistics


def _raw_cleaning_speed(records: list[dict]) -> StateType:
    """F5a — median cleaning speed (m²/min) across the API window.

    Cloud API returns sqft — converted to m² (× SQFT_TO_M2) for consistency
    with all other area sensors in Roomba+.
    Uses runM (clean time excl. recharge) preferred over durationM.
    Skips records missing either field or with zero time.
    """
    speeds = []
    for r in records:
        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            m2_per_min = float(sqft) * SQFT_TO_M2 / float(run_m)
            speeds.append(m2_per_min)
    if not speeds:
        return None
    return round(_statistics.median(speeds), 2)


def _raw_dirt_density(records: list[dict]) -> StateType:
    """F5b — median dirt events per m² across the API window.

    Cloud API returns sqft — converted to m² (÷ SQFT_TO_M2) for consistency.
    Rising values indicate a dirtier floor OR worn brushes (debris not
    captured, sensor re-fires).  The cause attribute distinguishes them.
    """
    densities = []
    for r in records:
        dirt = r.get("dirt")
        sqft = r.get("sqft")
        if dirt is not None and sqft and float(sqft) > 0:
            m2 = float(sqft) * SQFT_TO_M2
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


class CloudRawSensor(IRobotEntity, SensorEntity):
    """Sensor reading per-mission stats from the iRobot cloud raw record list.

    Reads from coordinator.raw_records — the per-mission list stored since v2.0.
    Updates whenever the coordinator refreshes (daily poll or map-retrain trigger).

    Available for all robots with cloud credentials (EPHEMERAL + SMART).

    SC1 (v2.7.0): these individual sensors are deprecated and disabled by default
    on fresh installs. Use the consolidated sensors instead:
      sensor.*_cleaning_performance, *_cleaning_analytics_30d,
      *_wifi_health, *_event_counts_30d.
    They will be removed in v3.0.
    """

    entity_description: CloudRawSensorDescription
    # SC1 (v2.7.0): tracks which keys have already logged a deprecation warning
    # this session. Class-level so it persists across sensor instances.
    _sc1_warned: ClassVar[set[str]] = set()

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
        # SC1 (v2.7.0): log a one-shot deprecation warning per key per session.
        key = self.entity_description.key
        if key not in CloudRawSensor._sc1_warned:
            _LOGGER.warning(
                "Roomba+ sensor '%s' is deprecated (SC1, v2.7.0) and will be "
                "removed in v3.0. Use sensor.*_cleaning_performance, "
                "*_cleaning_analytics_30d, *_wifi_health, or *_event_counts_30d "
                "instead. Disable this sensor in HA to suppress this warning.",
                key,
            )
            CloudRawSensor._sc1_warned.add(key)
        value = self.entity_description.value_fn(self._coordinator.raw_records)
        # F6a/F6b — cache values to RoombaData for repair check functions
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
        attrs = dict(self.entity_description.attributes_fn(self._coordinator.raw_records))
        # L5 (v2.6.0): add by_room dirtiness to recent_dirt_density sensor
        if self.entity_description.key == "recent_dirt_density":
            rps = getattr(self._config_entry.runtime_data, "robot_profile_store", None)
            if rps is not None:
                rel = rps.room_dirt_relative()
                if rel:
                    attrs["by_room"] = {rid: round(v, 3) for rid, v in rel.items()}
        return attrs

    @property
    def available(self) -> bool:
        # R3: return False when cloud is not configured so HA shows "Unavailable"
        # rather than showing None state with available=True. Distinguishes
        # "cloud not configured" from "coordinator not yet updated".
        if not self._config_entry.runtime_data.has_cloud:
            return False
        if not (self._coordinator.last_update_success
                and self._coordinator.data is not None):
            return False
        # available_fn: mark unavailable when insufficient data (not unknown).
        if self.entity_description.available_fn is not None:
            if not self.entity_description.available_fn(self):
                return False
        return True

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

    entity_description = SensorEntityDescription(
        key="raw_state",
        name="Raw MQTT state",
        translation_key="raw_state",
    )

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


# ── F12a — Optimal Clean Window sensor ────────────────────────────────────────

class RoombaOptimalCleanWindow(IRobotEntity, SensorEntity):
    """Timestamp sensor showing the optimal time to clean today.

    F12a (v2.4.0) — derived from PresenceManager.preferred_window(), which
    builds a day×hour reliability matrix from historical clean events.

    State: ISO-8601 datetime of the next occurrence of the best window today.
    None (unavailable) when fewer than 5 historical clean events exist.

    device_class: TIMESTAMP — HA renders as a relative time widget.
    entity_category: DIAGNOSTIC — opt-in; not shown in default dashboard view.
    """

    entity_description = SensorEntityDescription(
        key="optimal_clean_window",
        name="Optimal clean window",
        translation_key="optimal_clean_window",
    )

    _attr_entity_category = None  # reclassified: main entity (v2.6.0)
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = None  # timestamp sensors have no state_class

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_optimal_clean_window"

    @property
    def native_value(self) -> Any:
        """Return the next datetime for the preferred cleaning window."""
        import datetime as _dt
        pm = self._config_entry.runtime_data.presence_manager
        if pm is None:
            return None
        slot = pm.preferred_window()
        if slot is None:
            return None
        _, hour = slot
        # Build a datetime for the next occurrence of this hour today (or tomorrow
        # if the hour has already passed today)
        now = _dt.datetime.now(_dt.timezone.utc).astimezone()
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += _dt.timedelta(days=1)
        return candidate

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full presence_windows matrix as attributes."""
        pm = self._config_entry.runtime_data.presence_manager
        if pm is None:
            return {}
        windows = pm.presence_windows()
        # Serialise (weekday, hour) tuples as "wd_hr" strings for JSON
        return {
            "windows": {
                f"{wd}_{hr}": round(score, 3)
                for (wd, hr), score in windows.items()
            },
            "preferred_slot": pm.preferred_window(),
            # ALG2 (v2.6.0): True when the best window is today, so cards and
            # automations can distinguish "clean now" from "clean tomorrow".
            "window_is_today": pm.window_is_today,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Sensor is not MQTT-driven — update on demand only
        return False


# ── F12d — Edge Coverage Ratio sensor ─────────────────────────────────────────

class RoombaEdgeCoverageSensor(IRobotEntity, SensorEntity):
    """Ratio of edge cells to total cells in GridStore.

    F12d (v2.4.0) — a low ratio with high total coverage indicates the robot
    is over-cleaning the centre and under-covering room edges/walls.

    State: float 0.0–1.0 (edge cells / total cells), or None when < 10 cells.
    entity_category: DIAGNOSTIC.
    Unit: None (dimensionless ratio).
    """

    entity_description = SensorEntityDescription(
        key="recent_edge_coverage_ratio",
        name="Recent edge coverage ratio",
        translation_key="recent_edge_coverage_ratio",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = None
    _attr_suggested_display_precision = 3

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_recent_edge_coverage_ratio"

    @property
    def native_value(self) -> float | None:
        """Return edge_coverage_ratio from GridStore, or None when insufficient data."""
        gs = self._config_entry.runtime_data.grid_store
        if gs is None:
            return None
        return gs.edge_coverage_ratio()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        gs = self._config_entry.runtime_data.grid_store
        if gs is None:
            return {}
        attrs: dict[str, Any] = {
            "total_cells": gs.cell_count,
            "edge_depth_mm": 300,
        }
        # L6 (v2.6.0): ratio vs personal baseline (1.0 = on-par; <1 = below norm)
        rps = getattr(self._config_entry.runtime_data, "robot_profile_store", None)
        if rps is not None and rps.coverage_baseline_ready:
            current = self.native_value
            if isinstance(current, float) and rps.coverage_baseline:
                attrs["coverage_vs_baseline"] = round(
                    current / rps.coverage_baseline, 3
                )
        return attrs

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "pose" in new_state


def _get_planned_room_order(data: Any) -> list[str]:
    """Resolve planned_room_order from lastCommand.regions in MQTT state.

    MP1 helper — reads the live MQTT state so planned order is available
    immediately at mission start without waiting for a cloud refresh.
    Returns an empty list when no room-select command was issued (whole-home).

    v2.6.3 — uses MissionStore._extract_rid() to handle all confirmed region
    key formats: {"region_id": ...} (Roomba+ app), {"rid": ...} (iRobot app /
    lewis 22.52.10+), and plain string (some firmware variants).

    v2.7.5 — falls back to mts.planned_rooms (set at mission start by
    set_mission_plan) when lastCommand.regions is temporarily empty or when
    cc.regions is unavailable mid-mission:
      * Lewis firmware can send cleanMissionStatus with a transitional
        lastCommand that has no regions field at inter-room boundaries.
      * The F4b cloud refresh triggered by a transient end-phase can leave
        active_pmap_id returning None, making cc.regions return [].
    Either path previously caused all tracker values to flip to unknown.
    """
    cc = getattr(data, "cloud_coordinator", None)
    if cc is None:
        return []
    reported = data.roomba.master_state.get("state", {}).get("reported", {})
    last_cmd = reported.get("lastCommand", {})

    from .mission_store import MissionStore as _MS
    region_ids = [
        _MS._extract_rid(r)
        for r in (last_cmd.get("regions") or [])
        if _MS._extract_rid(r)
    ]
    mts = getattr(data, "mission_timer_store", None)
    if not region_ids:
        # lastCommand.regions temporarily empty — fall back to MTS snapshot.
        _LOGGER.debug(
            "_get_planned_room_order: lastCommand.regions empty, "
            "falling back to mts.planned_rooms=%s",
            getattr(mts, "planned_rooms", None) if mts else None,
        )
        if mts is not None and mts.planned_rooms:
            return list(mts.planned_rooms)
        return []

    id_to_name = {r["id"]: r["name"] for r in cc.regions if r.get("id")}
    result = [id_to_name[rid] for rid in region_ids if rid in id_to_name]

    # v2.9.0 — region_ids had entries, but not all of them resolved via
    # cc.regions (id_to_name). The pre-v2.9.0 fallback only triggered when
    # `result` was COMPLETELY empty, missing the case where it's a PARTIAL
    # match (e.g. region_ids=["21","25"] but cc.regions is mid-refresh and
    # only resolves "21" — result=["Corridoio"], non-empty, no fallback
    # triggered). A genuine 2-room mission's planned_order would then
    # silently shrink to 1 room mid-mission, corrupting every
    # elapsed/estimate calculation derived from it (mission_progress,
    # current_room/next_room, estimated_remaining_min) — a strong candidate
    # for Thonno's "progress reset" report, since phase stayed "run" the
    # whole time in his capture (ruling out every phase-based mechanism
    # already investigated). Logged at debug so the next capture confirms
    # or refutes this directly instead of guessing again.
    if len(result) != len(region_ids):
        _LOGGER.debug(
            "_get_planned_room_order: PARTIAL resolution — "
            "region_ids=%s resolved=%s (cc.regions stale/mid-refresh?). "
            "mts.planned_rooms=%s",
            region_ids, result,
            getattr(mts, "planned_rooms", None) if mts else None,
        )
        if mts is not None and mts.planned_rooms:
            return list(mts.planned_rooms)
    return result


def _compute_room_time_estimates(
    config_entry: Any, planned_order: list[str]
) -> list[int | None]:
    """Return per-room time estimates (seconds) in planned order.

    Reads from cloud_coordinator.regions[*].time_estimates (TE1). Returns
    None for any room where confidence < GOOD_CONFIDENCE or pass mode is
    Auto (no estimate available at runtime for Auto).

    Module-level (not class-bound) so it can be called from callbacks.py at
    mission start to wire MissionTimerStore.room_estimates_sec — see v2.8.0
    AUTO-ADVANCE-ROOM. Originally a method on RoombaMissionProgress only.
    """
    cc = config_entry.runtime_data.cloud_coordinator
    if cc is None:
        return [None] * len(planned_order)

    # v2.7.5 (TP-EST-FIX): per-room params in lastCommand.regions take
    # priority over cleanMissionStatus global fields. cleanMissionStatus
    # reflects the robot's global default, which stays at the device-level
    # setting even when a room-clean mission is started with explicit per-
    # region twoPass/noAutoPasses params. Reading from the wrong source
    # caused two-pass missions to be estimated at one-pass durations.
    reported = config_entry.runtime_data.roomba_reported_state()
    last_cmd = reported.get("lastCommand") or {}
    last_regions = [
        r for r in (last_cmd.get("regions") or [])
        if isinstance(r, dict) and r.get("params")
    ]
    if last_regions:
        has_no_auto = any(r["params"].get("noAutoPasses") for r in last_regions)
        has_two_pass = any(r["params"].get("twoPass") for r in last_regions)
        if not has_no_auto:
            pass_key = None          # Auto — no reliable per-room estimate
        elif has_two_pass:
            pass_key = "two_pass_sec"
        else:
            pass_key = "one_pass_sec"
    else:
        # Fallback: read from cleanMissionStatus global fields
        _cms = reported.get("cleanMissionStatus") or {}
        noap = _cms.get("noAutoPasses", True)
        two_pass = _cms.get("twoPass", False)
        if not noap:
            pass_key = None
        elif two_pass:
            pass_key = "two_pass_sec"
        else:
            pass_key = "one_pass_sec"

    # Build name→estimates map from coordinator
    region_map: dict[str, dict] = {}
    for region in cc.regions:
        name = region.get("name", "")
        if name:
            region_map[name.lower()] = region.get("time_estimates") or {}

    result: list[int | None] = []
    for room_name in planned_order:
        # Bug-hunt: room_name can theoretically be None/empty if a future
        # caller builds planned_order differently than callbacks.py's
        # current name-resolution chain (which always falls back to a
        # string). (room_name or "") avoids an AttributeError crash on
        # .lower() rather than relying on that invariant holding forever.
        est = region_map.get((room_name or "").lower(), {})
        if pass_key is None:
            result.append(None)
        else:
            result.append(est.get(pass_key))
    return result


# ── MP1 — Mission Progress sensor ─────────────────────────────────────────────

def _resolve_smart_tier_room_state(config_entry: Any) -> dict[str, Any]:
    """Resolve current_room/next_room/elapsed/estimated_remaining_min for a
    SMART-tier robot's active mission.

    v2.9.0 — EXTRACTED from RoombaMissionProgress.extra_state_attributes
    (was inline there since v2.7.2/MP-ELAPSED-FIX) into a shared,
    module-level function so RoombaDeviceTracker can use the EXACT same
    room resolution — current_room shown by the device tracker and by
    mission_progress must always agree; two separately-maintained copies
    of this logic would risk drifting apart over time as either one gets
    tweaked independently.

    Prefers the estimate-based room when the calculation succeeds (all
    per-room estimates available and elapsed within range); falls back to
    MissionTimerStore's own current_room/next_room when no per-room
    estimates exist (e.g. Auto pass mode — see the v2.9.0 AUTO-ADVANCE-ROOM
    Auto-mode fallback fix for why that's now far less restrictive than
    it used to be).
    """
    data = config_entry.runtime_data
    state = data.roomba_reported_state()
    phase = state.get("cleanMissionStatus", {}).get("phase", "")
    mts = data.mission_timer_store
    if mts is None or mts.mission_id is None:
        return {}

    planned_order: list[str] = _get_planned_room_order(data)
    # v2.9.0 — elapsed is now (mission_duration_min - recharge_min) * 60,
    # i.e. wall-clock time since mission start MINUS robot-confirmed
    # recharge minutes (hmMidMsn/rechrgM, F4e) — no time-based gap clamp.
    # Falls back to the old live-delta clamp-based calculation only for a
    # mission that was already in progress when this code shipped (its
    # mission_started_wall_ts is 0 since that field didn't exist yet) —
    # this fallback naturally stops mattering once that one mission ends.
    effective_min = mts.effective_elapsed_min
    elapsed = (
        effective_min * 60 if effective_min is not None
        else RoombaMissionProgress._elapsed_sec(mts, phase)
    )
    estimates = (
        _compute_room_time_estimates(config_entry, planned_order)
        if planned_order else []
    )

    # Determine current and next room from elapsed
    current_room: str | None = None
    next_room: str | None = None
    estimated_remaining_min: int | None = None

    if planned_order and estimates and all(e is not None for e in estimates):
        cumulative = 0
        for i, est in enumerate(estimates):
            assert est is not None
            if elapsed < cumulative + est:
                current_room = planned_order[i]
                next_room = planned_order[i + 1] if i + 1 < len(planned_order) else None
                remaining_sec = (cumulative + est - elapsed) + sum(
                    estimates[j]  # type: ignore[arg-type]
                    for j in range(i + 1, len(estimates))
                )
                estimated_remaining_min = max(0, round(remaining_sec / 60))
                break
            cumulative += est
        else:
            # All rooms elapsed — in final room
            current_room = planned_order[-1]

    if estimated_remaining_min is None:
        # v2.9.0 — fallback for whenever the per-room estimate calculation
        # above didn't produce a value: no planned_order at all, OR a
        # planned_order with one or more None per-room estimates (e.g. Auto
        # pass mode, where TE1 cloud data has no per-room times at all —
        # confirmed via Thonno's field report). Mirrors native_value()'s
        # same fallback rather than leaving estimated_remaining_min (and
        # therefore the whole "Unknown" percentage) stuck for the entire
        # mission whenever per-room estimates aren't available.
        rps = getattr(data, "robot_profile_store", None)
        mean_sec = (
            round((rps.mission_duration_mean or 0) * 60)
            if rps is not None else 0
        )
        if mean_sec > 0:
            estimated_remaining_min = max(0, round((mean_sec - elapsed) / 60))

    return {
        # Prefer estimate-based room when the calculation succeeded (all
        # estimates available and elapsed > 0). MTS value is the fallback
        # for when no per-room estimates exist.
        "current_room": current_room if current_room is not None else mts.current_room,
        "next_room":    next_room    if current_room is not None else mts.next_room,
        "elapsed_run_min": round(elapsed / 60, 1),
        "estimated_remaining_min": estimated_remaining_min,
        "room_sequence": planned_order,
        # v2.9.0 — Gesamtdauer (always-correct wall-clock) and Charging-Zeit
        # (robot-confirmed via F4e), shown alongside elapsed_run_min so the
        # difference between "total time" and "effective time" is visible
        # rather than silently disappearing.
        "mission_duration_min": mts.mission_duration_min,
        "recharge_min": round(mts.recharge_min, 1),
    }


class RoombaMissionProgress(IRobotEntity, SensorEntity):
    """Estimated mission completion percentage (0–100).

    MP1 (v2.6.0) — uses per-room time estimates from TE1 and elapsed run-only
    seconds from MissionTimerStore to show real-time mission progress without
    requiring manual calibration.

    Available only for SMART robots with cloud credentials and a loaded
    MissionTimerStore. Shows Unknown when no mission is active.
    """

    entity_description = SensorEntityDescription(
        key="mission_progress",
        name="Mission progress",
        translation_key="mission_progress",
    )

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = None  # main entity — visible on device page

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_mission_progress"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _room_estimates(self, planned_order: list[str]) -> list[int | None]:
        """Return per-room time estimates (seconds) in planned order.

        Delegates to the module-level _compute_room_time_estimates (extracted
        in v2.8.0 so callbacks.py can reuse this logic for AUTO-ADVANCE-ROOM).
        """
        return _compute_room_time_estimates(self._config_entry, planned_order)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _elapsed_sec(mts: Any, phase: str) -> float:
        """Return elapsed run seconds including current in-progress live delta.

        v2.7.2 (MP-ELAPSED-FIX): previously only native_value added the live
        delta; extra_state_attributes used mts.run_sec only, causing
        elapsed_run_min to freeze and current_room/next_room to lag on lewis
        firmware that sends cleanMissionStatus only on state changes.
        """
        import time as _time_mod
        elapsed = mts.run_sec
        if phase == "run" and mts._last_phase_ts > 0:
            live_delta = int(_time_mod.monotonic() - mts._last_phase_ts)
            if 0 < live_delta < 7200:   # cap at 2 h; restart/long-pause guard
                elapsed += live_delta
        return elapsed

    # ── Sensor state ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register 30 s periodic refresh for smooth progress updates.

        Lewis firmware sends cleanMissionStatus only on state changes, not
        continuously. Gaps between messages can exceed the 120 s accumulation
        clamp in MissionTimerStore, causing run_sec to stall.  The tick drives
        schedule_update_ha_state() from the event loop so native_value() can
        add the live delta directly, giving smooth updates regardless of MQTT
        message frequency.
        """
        await super().async_added_to_hass()
        import datetime as _dt
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                lambda _: self.schedule_update_ha_state(),
                _dt.timedelta(seconds=30),
            )
        )

    @property
    def native_value(self) -> StateType:
        """Return completion % (0–100) or None when inactive."""
        data = self._config_entry.runtime_data
        state = data.roomba_reported_state()
        phase = state.get("cleanMissionStatus", {}).get("phase", "")
        mts = data.mission_timer_store

        # Not in a cleaning phase → not active
        if phase not in ("run", "hmMidMsn", "evac"):
            return None
        if mts is None or mts.mission_id is None:
            return None

        # v2.9.0 — elapsed is now wall-clock mission duration minus
        # robot-confirmed recharge_min (F4e), not the old gap-clamped
        # live-delta. Same fallback as _resolve_smart_tier_room_state() for
        # a mission already in progress when this code shipped.
        effective_min = mts.effective_elapsed_min
        elapsed = (
            effective_min * 60 if effective_min is not None
            else self._elapsed_sec(mts, phase)
        )

        planned_order: list[str] = _get_planned_room_order(data)

        # v2.9.0 — comprehensive diagnostic for Thonno's "progress reset"
        # report. Every phase-based mechanism already investigated has been
        # ruled out (phase stays "run" throughout his capture); this logs
        # every input to the progress calculation on every poll so the next
        # capture shows directly whether planned_order/estimates change
        # shape mid-mission while phase never leaves "run".
        _LOGGER.debug(
            "mission_progress: phase=%s elapsed=%.1fs mission_id=%s "
            "planned_order=%s",
            phase, elapsed, mts.mission_id, planned_order,
        )

        if not planned_order:
            # No room sequence — use elapsed vs. rolling mean as fallback
            rps = getattr(data, "robot_profile_store", None)
            mean_sec = (
                round((rps.mission_duration_mean or 0) * 60)
                if rps is not None else 0
            )
            if mean_sec > 0:
                return min(99, round(elapsed / mean_sec * 100))
            return None

        estimates = self._room_estimates(planned_order)
        _LOGGER.debug(
            "mission_progress: planned_order=%s estimates=%s",
            planned_order, estimates,
        )
        if any(e is None for e in estimates):
            # At least one room has no estimate — use count-based progress
            total_rooms = len(planned_order)
            # Estimate current room from elapsed vs. mean-per-room
            total_known = sum(e for e in estimates if e is not None)
            known_count = len([e for e in estimates if e is not None])
            avg_sec = total_known / max(known_count, 1)
            if avg_sec > 0:
                completed_rooms = min(total_rooms - 1, int(elapsed / avg_sec))
                _LOGGER.debug(
                    "mission_progress: count-based branch total_rooms=%d "
                    "avg_sec=%.1f completed_rooms=%d -> %d%%",
                    total_rooms, avg_sec, completed_rooms,
                    min(99, round(completed_rooms / total_rooms * 100)),
                )
                return min(99, round(completed_rooms / total_rooms * 100))
            # v2.9.0 — known_count==0 here (ALL per-room estimates are None,
            # e.g. Auto pass mode — TE1 cloud data has no per-room times for
            # that mode at all, confirmed via Thonno's field report). Falls
            # through to the SAME mission_duration_mean rolling-average
            # fallback as the "no room sequence" branch above, instead of
            # returning None — previously this meant percentage/remaining
            # time stayed "Unknown" for the entire mission whenever Auto
            # mode was used, not just transiently.
            rps = getattr(data, "robot_profile_store", None)
            mean_sec = (
                round((rps.mission_duration_mean or 0) * 60)
                if rps is not None else 0
            )
            if mean_sec > 0:
                return min(99, round(elapsed / mean_sec * 100))
            return None

        total_sec = sum(estimates)  # type: ignore[arg-type]
        if total_sec == 0:
            return None
        return min(99, round(elapsed / total_sec * 100))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _resolve_smart_tier_room_state(self._config_entry)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state




# ── IA74-LP — Map Learning Percentage sensor ───────────────────────────────────

class RoombaLearningPercentageSensor(IRobotEntity, SensorEntity):
    """Map learning completeness score from the iRobot cloud (0–100 %).

    IA74-LP (v2.6.0) — SMART robots only. The robot's own assessment of how
    well it knows its environment. Low values indicate the robot has not fully
    explored the home; a stable map requires values near 100.
    """

    entity_description = SensorEntityDescription(
        key="map_learning",
        name="Map learning",
        translation_key="map_learning",
    )

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map_learning"

    @property
    def native_value(self) -> StateType:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return None
        return cc.learning_percentage

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # Updated by cloud coordinator listener only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))


# ── IA74-ZONE — Zone Summary sensor ───────────────────────────────────────────

class RoombaZoneSummarySensor(IRobotEntity, SensorEntity):
    """Count of active clean zones on the SMART map.

    IA74-ZONE (v2.6.0) — surfaces the three zone categories as a single sensor
    (state = clean zone count) with keepout and observed counts as attributes.
    Provides a quick map health overview without requiring the user to open the
    iRobot app.

    SMART + cloud only. State = number of clean zones (int).
    """

    entity_description = SensorEntityDescription(
        key="zone_summary",
        name="Zone summary",
        translation_key="zone_summary",
    )

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_zone_summary"

    @property
    def native_value(self) -> StateType:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return None
        return cc.zone_counts.get("clean")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return {}
        counts = cc.zone_counts
        return {
            "clean_zones":    counts.get("clean", 0),
            "keepout_zones":  counts.get("keepout", 0),
            "observed_zones": counts.get("observed", 0),
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))


# ── SC1 (v2.7.0) — Consolidated analytics sensors ─────────────────────────────
#
# These four sensors consolidate the 15 deprecated recent_* / cleaning_speed_trend
# CloudRawSensor entities into a compact set. Each exposes one primary metric as
# the state and groups related values as extra_state_attributes.
#
# Gate: cloud credentials required (same as CLOUD_RAW_SENSORS).
# Registered in async_setup_entry when data.has_cloud is True.


class _ConsolidatedCloudSensor(IRobotEntity, SensorEntity):
    """Base for SC1 consolidated sensors — wires coordinator listener."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._coordinator.async_add_listener(_on_coordinator_update))

    @property
    def available(self) -> bool:
        return (
            self._config_entry.runtime_data.has_cloud
            and self._coordinator.last_update_success
            and self._coordinator.data is not None
        )

    def _cache_and_check_f6a(self, trend_value: str | None) -> None:
        """Cache cleaning_speed_trend_value; schedule F6a check only on change.

        B1/B2 (v3.0.0): migrated from the deactivated
        CloudRawSensor(key="cleaning_speed_trend").native_value side-effect.
        extra_state_attributes may be evaluated multiple times per state write,
        so the F6a performance-degradation check is scheduled only when the
        cached trend actually changes — keeping repeated reads side-effect-free.
        """
        data = self._config_entry.runtime_data
        changed = data.cleaning_speed_trend_value != trend_value
        data.cleaning_speed_trend_value = trend_value
        if changed and hasattr(self.hass, "is_running") and self.hass.is_running:
            from .repairs import async_check_performance_degradation
            self.hass.async_create_task(
                async_check_performance_degradation(self.hass, self._config_entry),
                name="roomba_plus_f6a_perf_check",
            )

    def _cache_and_check_f6b(
        self, recharge_value: float | None, dirt_rising: bool | None
    ) -> None:
        """Cache recharge_fraction_value + dirt_density_rising; F6b check on change.

        B1/B2 (v3.0.0): migrated from the deactivated CloudRawSensor side-effects
        (keys "recent_recharge_fraction" and "recent_dirt_density"). The F6b
        battery-recharge check is scheduled only when the recharge fraction
        changes, avoiding duplicate tasks on repeated property reads.
        """
        data = self._config_entry.runtime_data
        changed = data.recharge_fraction_value != recharge_value
        data.recharge_fraction_value = recharge_value
        if dirt_rising is not None:
            data.dirt_density_rising = dirt_rising
        if changed and hasattr(self.hass, "is_running") and self.hass.is_running:
            from .repairs import async_check_battery_recharge
            self.hass.async_create_task(
                async_check_battery_recharge(self.hass, self._config_entry),
                name="roomba_plus_f6b_battery_check",
            )


class RoombaCleaningPerformanceSensor(_ConsolidatedCloudSensor):
    """SC1 — Cleaning performance: completion rate + speed + trend + streak.

    State: completion rate (%) over the cloud API window.
    Attributes: speed_m2_per_min, coverage_pct, trend, clean_streak.

    Replaces: recent_completion_rate, recent_cleaning_speed,
              recent_coverage_pct, cleaning_speed_trend.
    """

    entity_description = SensorEntityDescription(
        key="cleaning_performance",
        name="Cleaning performance",
        translation_key="cleaning_performance",
    )
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_cleaning_performance"

    @property
    def native_value(self) -> StateType:
        return _raw_completion_rate(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        records = self._coordinator.raw_records
        attrs: dict[str, Any] = {}
        if records:
            speed = _raw_cleaning_speed(records)
            if speed is not None:
                attrs["speed_m2_per_min"] = speed
            trend = _raw_cleaning_speed_trend(records)
            if trend is not None:
                attrs["trend"] = trend
            # B1/B2-PRE — cache cleaning_speed_trend_value for F6a Repair and
            # RobotHealthSensor Signal 3.  Migrated from the now-deactivated
            # CloudRawSensor(key="cleaning_speed_trend") side-effect.
            # Idempotent: the F6a check is scheduled only when the trend changes.
            self._cache_and_check_f6a(
                str(trend) if trend is not None else None
            )
            # Coverage: most-recent sqft vs 60-day p75 baseline
            ms = self._config_entry.runtime_data.mission_store
            if ms is not None:
                recent_sqft = next(
                    (r["sqft"] for r in records if r.get("sqft") and r["sqft"] > 0), None
                )
                p75 = ms.p75_area(60)
                if recent_sqft is not None and p75 and p75 > 0:
                    attrs["coverage_pct"] = round(float(recent_sqft) / p75 * 100, 1)
            # Clean streak from local MissionStore
            if ms is not None:
                streak = ms.clean_streak()
                if streak is not None:
                    attrs["clean_streak"] = streak
        return attrs


class RoombaCleaningAnalytics30dSensor(_ConsolidatedCloudSensor):
    """SC1 — Cleaning analytics: area + time + dirt density + recharge fraction.

    State: cleaned area in m² over the cloud API window (~30 missions).
    Attributes: time_h, dirt_density, recharge_pct.

    Replaces: recent_area_30d, recent_time_30d, recent_dirt_density,
              recent_recharge_fraction.
    """

    entity_description = SensorEntityDescription(
        key="cleaning_analytics_30d",
        name="Cleaning analytics (30 d)",
        translation_key="cleaning_analytics_30d",
    )
    _attr_native_unit_of_measurement = "m²"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_cleaning_analytics_30d"

    @property
    def native_value(self) -> StateType:
        sqft = (self._coordinator.data.get("runtimeStats") or {}).get("sqft")
        if sqft is None:
            return None
        return round(float(sqft) * SQFT_TO_M2, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        stats = (self._coordinator.data.get("runtimeStats") or {})
        hr = stats.get("hr")
        mn = stats.get("min")
        if hr is not None and mn is not None:
            attrs["time_h"] = round((hr * 60 + mn) / 60, 1)
        records = self._coordinator.raw_records
        if records:
            dd = _raw_dirt_density(records)
            if dd is not None:
                attrs["dirt_density"] = dd
            rf = _raw_recharge_fraction(records)
            if rf is not None:
                attrs["recharge_pct"] = rf
            # B1/B2-PRE — cache recharge_fraction_value + dirt_density_rising for
            # F6b Repair and F6a cause classification.  Migrated from the now-
            # deactivated CloudRawSensor side-effects (keys "recent_recharge_fraction"
            # and "recent_dirt_density").  Idempotent: F6b check only on change.
            dirt_rising: bool | None = None
            if len(records) >= 6:
                densities = [
                    float(r["dirt"]) / float(r["sqft"])
                    for r in records
                    if r.get("dirt") is not None and r.get("sqft") and float(r["sqft"]) > 0
                ]
                if len(densities) >= 6:
                    recent = _statistics.median(densities[:5])
                    older  = _statistics.median(densities[5:])
                    dirt_rising = (recent / older > 1.10) if older > 0 else False
            self._cache_and_check_f6b(
                float(rf) if rf is not None else None,
                dirt_rising,
            )
        return attrs


class RoombaWifiHealthSensor(_ConsolidatedCloudSensor):
    """SC1 — Wi-Fi health: average signal quality + stability + worst dip.

    State: average WiFi signal quality (%) across the cloud API window —
    a weighted mean over each mission's full wlBars histogram distribution,
    not just whether the weakest bucket was ever touched (v2.9.0 fix; see
    _raw_wifi_quality_pct docstring for the full rationale — the previous
    implementation returned a raw 0-4 bucket index from a single mission
    mislabelled as a percentage, so a single brief signal dip could read
    as "0%" even with an otherwise excellent connection).
    Attributes: stability_pct, weakest_bucket_observed (0-4, the original
    "floor" diagnostic — still useful for spotting a dead zone, just not
    as the misleading primary percentage).

    Replaces: recent_wifi_floor, recent_wifi_stability.
    """

    entity_description = SensorEntityDescription(
        key="wifi_health",
        name="Wi-Fi health",
        translation_key="wifi_health",
    )
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_health"

    @property
    def native_value(self) -> StateType:
        return _raw_wifi_quality_pct(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        stab = _raw_wifi_stability(self._coordinator.raw_records)
        if stab is not None:
            attrs["stability_pct"] = stab
        floor = _raw_wifi_floor(self._coordinator.raw_records)
        if floor is not None:
            attrs["weakest_bucket_observed"] = floor
        return attrs


class RoombaEventCounts30dSensor(_ConsolidatedCloudSensor):
    """SC1 — Event counts: recharges + evacuations + dirt events + last error.

    State: most recent error code (int | None).
    Attributes: recharges, evacuations, dirt_events, error_time, error_label.

    Replaces: recent_recharges, recent_evacuations, recent_dirt_events,
              recent_error_code, recent_error_time.
    """

    entity_description = SensorEntityDescription(
        key="event_counts_30d",
        name="Event counts (30 d)",
        translation_key="event_counts_30d",
    )

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_event_counts_30d"

    @property
    def native_value(self) -> StateType:
        return _raw_cloud_last_error_code(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        records = self._coordinator.raw_records
        attrs: dict[str, Any] = {}
        if not records:
            return attrs
        recharges = _raw_recharges(records)
        if recharges is not None:
            attrs["recharges"] = recharges
        evacs = _raw_evacuations(records)
        if evacs is not None:
            attrs["evacuations"] = evacs
        dirt = _raw_dirt_events(records)
        if dirt is not None:
            attrs["dirt_events"] = dirt
        err_time = _raw_cloud_last_error_time(records)
        if err_time is not None:
            attrs["error_time"] = err_time.isoformat()
        err_attrs = _raw_cloud_last_error_attrs(records)
        if err_attrs.get("label"):
            attrs["error_label"] = err_attrs["label"]
        return attrs


# ── L8 (v2.7.0) — Composite robot health score ────────────────────────────────

class RoombaRobotHealthSensor(IRobotEntity, SensorEntity):
    """L8 — single 0–100 health score combining all learned robot signals.

    Not DIAGNOSTIC — this is the number a non-technical user checks first.
    State is None (Unknown) until ≥20 missions have been recorded and at
    least 3 of the 5 component signals are available.

    Updates on cloud coordinator refresh (which triggers after every mission
    end via the F4b cloud-refresh hook).
    """

    entity_description = SensorEntityDescription(
        key="robot_health_score",
        name="Robot health score",
        translation_key="robot_health_score",
    )
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = None   # Main entity — deliberate per L8 spec

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_robot_health_score"

    def _score_and_breakdown(self) -> tuple[float | None, dict[str, Any]]:
        """Compute (score, breakdown) once; shared by native_value and
        extra_state_attributes so the underlying signals aren't recomputed
        twice per state update.
        """
        data = self._config_entry.runtime_data
        ms  = data.mission_store
        rps = getattr(data, "robot_profile_store", None)

        if ms is None or rps is None:
            return None, {}

        # Calibration gate: ≥20 missions needed for meaningful statistics
        records_30d = ms.query(30)
        if len(records_30d) < 20:
            return None, {}

        # Signal 1: battery retention (cached from battery retention sensor)
        bat_retention = data.battery_retention_value

        # Signal 2: navigation efficiency — current edge ratio vs stored baseline
        nav_ratio: float | None = None
        gs = data.grid_store
        if rps.coverage_baseline_ready and rps.coverage_baseline and gs is not None:
            current_ratio = gs.edge_coverage_ratio()
            if current_ratio is not None and rps.coverage_baseline > 0:
                nav_ratio = current_ratio / rps.coverage_baseline

        # Signal 3: cleaning speed trend (cached from CloudRawSensor)
        trend = data.cleaning_speed_trend_value

        # Signal 4: consecutive anomalous missions (L3)
        consecutive_anom = ms.consecutive_anomalous

        # Signal 5: stuck rate in last 30d — all three stuck variants count
        from .mission_store import MissionStore as _MS
        stuck_count = sum(
            1 for r in records_30d
            if r.get("result") in _MS.STUCK_RESULTS
        )
        stuck_rate = stuck_count / len(records_30d) if records_30d else None

        score, breakdown = rps.compute_health_score(
            battery_retention_pct=bat_retention,
            nav_efficiency_ratio=nav_ratio,
            cleaning_speed_trend=trend,
            consecutive_anomalous=consecutive_anom,
            stuck_rate_30d=stuck_rate,
        )

        # v3.2.0 L10 — snapshot into the rolling history whenever a real
        # score is available. record_health_score() is idempotent per
        # calendar day, so calling this from both native_value and
        # extra_state_attributes reads on the same day is harmless.
        if score is not None:
            rps.record_health_score(score, dt_util.now().date().isoformat())

        return score, breakdown

    @property
    def native_value(self) -> StateType:
        score, _ = self._score_and_breakdown()
        return score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """v3.1.0 PLAIN-STATUS — status_text/recommendation derived from the
        breakdown's weakest_signal, translated via _ROBOT_HEALTH_STATUS_MAP.
        """
        _, breakdown = self._score_and_breakdown()
        if not breakdown:
            return {}
        status_text, recommendation = _robot_health_plain_status(self.hass, breakdown)
        return {
            **breakdown,
            "status_text": status_text,
            "recommendation": recommendation,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


class RoombaHealthScoreTrendSensor(IRobotEntity, SensorEntity):
    """L10 (v3.2.0) — self-calibrating trend classification of the L8
    health score, backed by RobotProfileStore.health_score_history.

    State is 'improving' / 'stable' / 'declining', or None until this
    robot's own baseline is established (>=14 days of recorded scores —
    RobotProfileStore.health_score_baseline_ready). Trend is judged against
    this robot's own learned mean/stdev, not a fixed point-difference
    threshold — see health_score_trend()'s docstring for why.

    Same update source as L8 itself (cloud coordinator refresh after every
    mission end) — the trend has nothing new to say between mission ends.
    """

    entity_description = SensorEntityDescription(
        key="health_score_trend",
        name="Health score trend",
        translation_key="health_score_trend",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_health_score_trend"

    @property
    def _rps(self) -> Any | None:
        return getattr(self._config_entry.runtime_data, "robot_profile_store", None)

    @property
    def native_value(self) -> StateType:
        rps = self._rps
        if rps is None:
            return None
        return rps.health_score_trend()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rps = self._rps
        if rps is None:
            return {}
        from .robot_profile_store import (
            _HEALTH_SCORE_REFERENCE_EXCLUSION_DAYS,
            _HEALTH_SCORE_BASELINE_MIN_DAYS,
        )
        days_recorded = len(rps.health_score_history)
        min_days_needed = _HEALTH_SCORE_REFERENCE_EXCLUSION_DAYS + _HEALTH_SCORE_BASELINE_MIN_DAYS
        return {
            "baseline_ready": rps.health_score_baseline_ready,
            "days_recorded": days_recorded,
            # v3.2.0 UX fix — days_recorded alone requires the user to
            # already know the 44-day threshold from documentation and
            # do the subtraction themselves. Explicit here instead.
            "days_until_ready": max(0, min_days_needed - days_recorded),
            "baseline_score": (
                round(rps.health_score_baseline, 1)
                if rps.health_score_baseline is not None else None
            ),
            "declining_days": rps.health_score_declining_days(),
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_health_trend_declining
                self.hass.async_create_task(
                    async_check_health_trend_declining(self.hass, self._config_entry),
                    name="roomba_plus_l10_health_trend_check",
                )

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


# ── BAT-ARCH (v2.8.0) — Archive-sourced WiFi + charge sensors ─────────────────

def _channel_to_band(channel: int | None) -> str | None:
    """Derive WiFi band string from 802.11 channel number."""
    if channel is None:
        return None
    if 1 <= channel <= 13:
        return "2.4 GHz"
    if 36 <= channel <= 177:
        return "5 GHz"
    return None


class _ArchiveSensor(_ConsolidatedCloudSensor):
    """Base for sensors reading from MissionArchive (BAT-ARCH v2.8.0).

    Available when the archive has >=5 records and cloud coordinator is live.
    """

    @property
    def _archive(self):
        return getattr(self._config_entry.runtime_data, "mission_archive", None)

    @property
    def available(self) -> bool:
        arc = self._archive
        return (
            super().available
            and arc is not None
            and arc.record_count >= 5
        )


class RoombaWifiLastChannelSensor(_ArchiveSensor):
    """BAT-ARCH -- Most recent WiFi channel from archive Layer 1."""

    entity_description = SensorEntityDescription(
        key="wifi_last_channel",
        name="Wi-Fi last channel",
        translation_key="wifi_last_channel",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_last_channel"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        latest = arc.latest_derived(1)
        return latest[0].get("wifi_channel") if latest else None

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        latest = arc.latest_derived(1)
        if not latest:
            return {}
        band = _channel_to_band(latest[0].get("wifi_channel"))
        return {"band": band} if band else {}


class RoombaWifiChannelStabilitySensor(_ArchiveSensor):
    """BAT-ARCH -- % of last 30 missions on dominant WiFi channel."""

    entity_description = SensorEntityDescription(
        key="wifi_channel_stability",
        name="Wi-Fi channel stability",
        translation_key="wifi_channel_stability",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_channel_stability"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        series = arc.wifi_channel_series(30)
        if not series:
            return None
        dominant_count = Counter(series).most_common(1)[0][1]
        return round(dominant_count / len(series) * 100, 1)

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        series = arc.wifi_channel_series(30)
        if not series:
            return {}
        dominant_ch, dominant_count = Counter(series).most_common(1)[0]
        return {
            "dominant_channel": dominant_ch,
            "dominant_channel_band": _channel_to_band(dominant_ch),
            "sample_count": len(series),
        }


class RoombaMissionsPerChargeSensor(_ArchiveSensor):
    """BAT-ARCH -- Avg missions per charge cycle (last 30 days).

    State: total_missions / (1 + total_mid_mission_recharges). Higher = healthier.
    """

    entity_description = SensorEntityDescription(
        key="missions_per_charge",
        name="Missions per charge",
        translation_key="missions_per_charge",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_missions_per_charge"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        recent = arc.recent_derived(30)
        if not recent:
            return None
        total_recharges = sum(int(r.get("recharge_count") or 0) for r in recent)
        return round(len(recent) / max(1, 1 + total_recharges), 2)

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        recent = arc.recent_derived(30)
        if not recent:
            return {}
        total = len(recent)
        total_recharges = sum(int(r.get("recharge_count") or 0) for r in recent)
        no_recharge = sum(1 for r in recent if not r.get("recharge_count"))
        return {
            "missions_30d": total,
            "mid_mission_recharges_30d": total_recharges,
            "single_charge_pct": round(no_recharge / total * 100, 1) if total else None,
        }


# ── v2.8.3 — FW-SENSOR ────────────────────────────────────────────────────────

class RoombaFirmwareVersionSensor(IRobotEntity, SensorEntity):
    """FW-SENSOR (v2.8.3) — robot firmware version string.

    Reads `softwareVer` from the live MQTT state.  Present on all robot
    families (9-series, i/s/j-series, Braava m6).  Stays at its last-known
    value when offline — sensor is available whenever MQTT is connected.

    Paired with RoombaFirmwareUpdated (binary_sensor.*_firmware_updated) which
    turns ON for 24 h after a version change is detected.
    """

    entity_description = SensorEntityDescription(
        key="firmware_version",
        name="Firmware version",
        translation_key="firmware_version",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_firmware_version"

    @property
    def native_value(self) -> str | None:
        """Return the firmware version string from softwareVer."""
        return self.vacuum_state.get("softwareVer")

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "softwareVer" in new_state


class RoombaIntegrationHealthSensor(IRobotEntity, SensorEntity):
    """INTEG-HEALTH (v2.9.0) — integration health meta-sensor (0-100).

    Combines three signals into one diagnostic score: active Repair Issue
    count, MQTT message age, and ARC1 (MissionArchive) freshness. See
    _compute_integration_health()'s docstring for the exact formula and
    why two originally-planned signals (cloud age, "last store save")
    were folded in or dropped.

    Score is computed on every poll (cheap — no I/O, just registry/store
    reads already held in memory) AND on a 60-second periodic tick, which
    additionally fires/clears the integration_health Repair Issue when the
    score has been below INTEGRATION_HEALTH_LOW_THRESHOLD (50) for at
    least INTEGRATION_HEALTH_SUSTAINED_MINUTES (30 min) — a single bad
    reading should not alarm the user; a sustained one should.

    v2.9.0 EVENT-BUS: the same 60-second tick also fires
    roomba_plus_health_change, but only on BAND-crossing (healthy/degraded/
    critical — see _health_band()), not on every score recompute. Deliberately
    NOT done in native_value, since that property is read on every poll
    (including polls triggered by other entities/HA internals) and would
    fire far more often than the score meaningfully changes.
    """

    entity_description = SensorEntityDescription(
        key="integration_health",
        name="Integration health",
        translation_key="integration_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_integration_health"
        self._unsub_tick: Any | None = None
        # v2.9.0 EVENT-BUS — None until the first tick so the very first
        # evaluation never fires a "change" (there is no prior band yet).
        self._last_health_band: str | None = None
        self._last_health_score: int | None = None

    async def async_added_to_hass(self) -> None:
        """Start the 60-second periodic tick that drives the Repair Issue."""
        await super().async_added_to_hass()
        self._unsub_tick = async_track_time_interval(
            self.hass,
            self._async_health_tick,
            dt_stdlib.timedelta(seconds=INTEGRATION_HEALTH_TICK_SECONDS),
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        ir.async_delete_issue(
            self.hass, DOMAIN, f"integration_health_{self._entry.entry_id}"
        )

    @callback
    def _async_health_tick(self, _now: Any) -> None:
        """Re-evaluate health on a timer and fire/clear the Repair Issue."""
        from .repairs import async_check_integration_health
        async_check_integration_health(self.hass, self._entry)

        # v2.9.0 EVENT-BUS — band-crossing health_change event. First tick
        # only seeds _last_health_band (no prior state to compare against,
        # so no event fires on startup).
        score, _ = _compute_integration_health(self.hass, self._entry)
        band = _health_band(score)
        if self._last_health_band is not None and band != self._last_health_band:
            self.hass.bus.async_fire(
                EVENT_HEALTH_CHANGE,
                {
                    "entry_id": self._entry.entry_id,
                    "name": self._entry.title,
                    "score": score,
                    "previous_score": self._last_health_score,
                    "band": band,
                    "previous_band": self._last_health_band,
                },
            )
        self._last_health_band = band
        self._last_health_score = score

        self.schedule_update_ha_state(force_refresh=True)

    @property
    def native_value(self) -> int:
        score, _ = _compute_integration_health(self.hass, self._entry)
        return score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        _, breakdown = _compute_integration_health(self.hass, self._entry)
        status_text, recommendation = _integration_health_plain_status(
            self.hass, breakdown
        )
        return {
            **breakdown,
            "status_text": status_text,
            "recommendation": recommendation,
        }


class RoombaLastMissionSummarySensor(IRobotEntity, SensorEntity):
    """LAST-MISSION-SUMMARY (v3.1.0) — last completed mission as a single entity.

    native_value = result string of the last mission record.
    extra_state_attributes = all relevant mission fields in one place.

    Primary use-cases:
    - Troubleshooting: attach one entity to a bug report instead of digging
      through diagnostics.
    - Notification automations: trigger on state change, use attributes for
      the notification body without template work.

    Gate: none — available for all robots and all tiers. Returns None /
    empty attributes when MissionStore has no records yet.
    """

    _attr_translation_key = "last_mission_summary"
    _attr_entity_category = None          # Primary — visible on the device page
    _attr_has_entity_name = True

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_last_mission_summary"

    @property
    def suggested_object_id(self) -> str:
        return "last_mission_summary"

    @property
    def _latest(self) -> dict[str, Any] | None:
        """Return the most recent MissionStore record, or None."""
        store = self._entry.runtime_data.mission_store
        if store is None:
            return None
        return store.latest()

    @property
    def native_value(self) -> str | None:
        """Return the result of the last mission, e.g. 'completed'."""
        rec = self._latest
        return rec.get("result") if rec else None

    @property
    def _region_map_and_umf(self) -> tuple[dict[str, str], dict[str, str] | None]:
        """v3.1.1 ROOM-COVERAGE-IN-SUMMARY — build (region_map, umf_regions)
        exactly like vacuum.py's extra_state_attributes does, so
        latest_cleaned_rooms()/latest_room_coverage() resolve room display
        names the same way on both entities.
        """
        data = self._entry.runtime_data
        region_map: dict[str, str] = {}
        if data.has_cloud and data.cloud_coordinator is not None:
            region_map = {
                r["id"]: r["name"]
                for r in data.cloud_coordinator.regions
                if r.get("id")
            }
        umf_regions: dict[str, str] | None = None
        if not region_map and data.umf_aligner and data.umf_aligner.aligned:
            umf_regions = data.umf_aligner.rid_to_name()
        return region_map, umf_regions

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all mission fields as attributes for easy automation use.

        v3.1.1 ROOM-COVERAGE-IN-SUMMARY — fixes a pre-existing bug where
        cleaned_rooms always returned None: the MissionStore record itself
        never carries a "last_cleaned_rooms" key (that name only exists as
        vacuum.py's *computed* attribute name, derived on the fly from
        timeline.finEvents via MissionStore.latest_cleaned_rooms() — it was
        never a literal stored field). Both cleaned_rooms and the new
        room_coverage attribute now call the same MissionStore methods
        vacuum.py uses, with the same region_map/umf_regions resolution,
        instead of reading a record key that was never populated.
        """
        rec = self._latest
        if rec is None:
            return {
                "result": None,
                "duration_min": None,
                "area_sqft": None,
                "cleaned_rooms": None,
                "room_coverage": None,
                "cleaning_passes": None,
                "battery_start_pct": None,
                "battery_end_pct": None,
                "recharges": None,
                "dirt_events": None,
                "evacuations": None,
                "error_code": None,
                "initiator": None,
                "started_at": None,
                "ended_at": None,
            }

        store = self._entry.runtime_data.mission_store
        cleaned_rooms = None
        room_coverage = None
        if store is not None:
            region_map, umf_regions = self._region_map_and_umf
            if region_map or umf_regions:
                cleaned_rooms = store.latest_cleaned_rooms(region_map, umf_regions)
                room_coverage = store.latest_room_coverage(region_map, umf_regions)

        return {
            "result": rec.get("result"),
            "duration_min": rec.get("duration_min"),
            "area_sqft": rec.get("area_sqft"),
            "cleaned_rooms": cleaned_rooms,
            "room_coverage": room_coverage,
            "cleaning_passes": rec.get("cleaning_passes"),
            "battery_start_pct": rec.get("battery_start_pct"),
            "battery_end_pct": rec.get("battery_end_pct"),
            "recharges": rec.get("recharges"),
            "dirt_events": rec.get("dirt_events"),
            "evacuations": rec.get("evacuations"),
            "error_code": rec.get("error_code"),
            "initiator": rec.get("initiator"),
            "started_at": rec.get("started_at"),
            "ended_at": rec.get("ended_at"),
        }


class RoombaRoomCleaningHistorySensor(IRobotEntity, SensorEntity):
    """ROOM-CLEANING-HISTORY (v3.1.0) — last clean timestamp per room.

    native_value = number of rooms for which a cleaning timestamp is known.
    extra_state_attributes = {room_name: iso_timestamp} dict spanning all
    records in MissionStore, newest-first scan so each room shows its most
    recent clean.

    Gate: only created when the robot has ever recorded room data
    (``last_cleaned_rooms`` in at least one MissionStore record). In practice
    this means SMART robots with cloud credentials, but the sensor is
    tier-agnostic — if an EPHEMERAL robot gains room data via future
    enrichment it will appear automatically.

    Primary use-cases:
    - Dashboard: "When was the kitchen last cleaned?"
    - Automation: template sensor pulling a single room's timestamp for
      a time-based notification.
    """

    _attr_translation_key = "room_cleaning_history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_cleaning_history"

    @property
    def suggested_object_id(self) -> str:
        return "room_cleaning_history"

    @property
    def _history(self) -> dict[str, str]:
        store = self._entry.runtime_data.mission_store
        if store is None:
            return {}
        return store.room_cleaning_history()

    @property
    def native_value(self) -> int:
        """Number of rooms with a known last-clean timestamp."""
        return len(self._history)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Dict mapping room display name → ISO timestamp of last clean."""
        return self._history


def _id_to_display_name(cc: Any) -> dict[str, str]:
    """v3.2.0 ROOM-TYPE-SUGGEST — shared {rid: display_name} resolver for
    ROOM-SIZE and ROOM-ACCESS (the two sensors that fall back to a raw
    region ID when a room isn't named — RoombaRoomCleaningHistorySensor
    doesn't need this: its room names already come pre-resolved from
    MissionStore's last_cleaned_rooms, not raw IDs).

    Fallback chain: user-set cc.regions name (authoritative — never
    overridden) -> iRobot's own top-scored region_suggestions type,
    ONLY when that top score is positive (a negative score, confirmed in
    real field data, means "probably NOT this type" — using it as a
    label would be actively misleading, not just imprecise) -> the raw
    rid as the final fallback, same as before this existed.

    Reliability of region_suggestions across many pmaps beyond the one
    sample seen (see MISSIONSTORE_FIELD_REGISTRY.md) is unconfirmed —
    this conservative positive-score gate is deliberately cautious about
    that uncertainty, not just about formatting the suggestion nicely.
    """
    if cc is None:
        return {}
    id_to_name: dict[str, str] = {
        r["id"]: r["name"]
        for r in (cc.regions or [])
        if r.get("id") and r.get("name")
    }
    for suggestion in (cc.region_suggestions or []):
        rid = suggestion.get("region_id")
        if not rid or rid in id_to_name:
            continue
        types = suggestion.get("suggested_types") or []
        if not types:
            continue
        best = max(types, key=lambda t: t.get("score", float("-inf")))
        score = best.get("score")
        region_type = best.get("region_type")
        if score is not None and score > 0 and region_type:
            id_to_name[rid] = region_type.replace("_", " ").title()
    return id_to_name


class RoombaRoomAreasSensor(IRobotEntity, SensorEntity):
    """ROOM-SIZE (v3.1.0) — per-room floor area in m² from UMF polygons.

    native_value = number of rooms with a known area.
    extra_state_attributes = {room_display_name: area_m2} dict.

    Source: UmfAligner.room_areas_m2 (shoelace formula on UMF polygon
    vertices). Keys are translated from region IDs to display names via
    cloud_coordinator.regions. Falls back to region ID as key when the
    display name is not available.

    Does NOT require UmfAligner.aligned — room_areas_m2 is populated by
    _resolve_room_polygons() before the alignment step, so areas are
    available even at low confidence.

    Gate: SMART-tier + umf_aligner present. The UmfAligner is only
    instantiated for SMART robots with cloud credentials.
    """

    _attr_translation_key = "room_areas"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_areas"

    @property
    def suggested_object_id(self) -> str:
        return "room_areas"

    @property
    def _areas(self) -> dict[str, float]:
        """Return {display_name: area_m2} from UmfAligner, or {} if unavailable."""
        data = self._entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None:
            return {}
        areas_by_rid = aligner.room_areas_m2          # {rid: float}
        if not areas_by_rid:
            return {}
        id_to_name = _id_to_display_name(data.cloud_coordinator)
        return {
            id_to_name.get(rid, rid): round(area, 2)
            for rid, area in areas_by_rid.items()
        }

    @property
    def native_value(self) -> int:
        """Number of rooms with a known floor area."""
        return len(self._areas)

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        """Dict mapping room display name → floor area in m²."""
        return self._areas


class RoombaRoomAccessibilityScoresSensor(IRobotEntity, SensorEntity):
    """ROOM-ACCESS (v3.2.0) — per-room accessibility score 0-100, combining
    three signals: coverage fraction (GridStore.coverage_by_polygon),
    stuck-event rate (GridStore.stuck_by_polygon), and traversal time
    efficiency (MissionArchive.time_per_room, aggregated across mission
    history and normalised by ROOM-SIZE's room areas).

    native_value = number of rooms with a computed score.
    extra_state_attributes = {room_display_name: {"score": float,
    "limiting_factor": str}} — dict-sensor design, deliberately not one
    entity per room, matching the ROOM-CLEANING-HISTORY / ROOM-SIZE
    precedent from v3.1.0's PRIMARY-SLIM direction (avoid entity sprawl)
    rather than the original version-plan wording's implied per-room
    entities.

    Stuck-rate and time-efficiency sub-scores are judged against this
    robot's OWN average across its OWN rooms — see
    RobotProfileStore.room_accessibility_scores()'s docstring for why a
    fixed external threshold doesn't work here.

    Gate: SMART-tier + umf_aligner present (same as ROOM-SIZE — room
    polygons come from the same source).
    """

    _attr_translation_key = "room_accessibility_scores"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_accessibility_scores"

    @property
    def suggested_object_id(self) -> str:
        return "room_accessibility_scores"

    @property
    def _scores(self) -> dict[str, dict[str, Any]]:
        """Return {display_name: {"score": float, "limiting_factor": str}},
        or {} if the required inputs (UMF room polygons) aren't available."""
        data = self._entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None:
            return {}
        polygons = aligner.room_polygons_umf()
        areas_m2 = aligner.room_areas_m2
        if not polygons:
            return {}

        gs = data.grid_store
        coverage_by_room = gs.coverage_by_polygon(polygons) if gs is not None else {}
        stuck_by_room = gs.stuck_by_polygon(polygons) if gs is not None else {}

        # Aggregate time-per-room across mission history, then normalise
        # by each room's area to get seconds/m2 (comparable across
        # differently-sized rooms).
        time_by_room: dict[str, int] = {}
        archive = data.mission_archive
        if archive is not None:
            from .mission_archive import MissionArchive
            for record in archive.all_derived_oldest_first():
                visits = record.get("room_visits") or []
                for rid, seconds in MissionArchive.time_per_room(visits).items():
                    time_by_room[rid] = time_by_room.get(rid, 0) + seconds

        time_per_area_by_room: dict[str, float] = {}
        for rid, seconds in time_by_room.items():
            area = areas_m2.get(rid)
            if area and area > 0:
                time_per_area_by_room[rid] = seconds / area

        from .robot_profile_store import RobotProfileStore
        raw_scores = RobotProfileStore.room_accessibility_scores(
            coverage_by_room, stuck_by_room, time_per_area_by_room,
        )

        id_to_name = _id_to_display_name(data.cloud_coordinator)
        return {
            id_to_name.get(rid, rid): v
            for rid, v in raw_scores.items()
            if v.get("score") is not None
        }

    @property
    def native_value(self) -> int:
        """Number of rooms with a computed accessibility score."""
        return len(self._scores)

    @property
    def extra_state_attributes(self) -> dict[str, dict[str, Any]]:
        return self._scores


class RoombaResetDiagnosticsSensor(IRobotEntity, SensorEntity):
    """RESET-DIAGNOSTICS (v3.2.0) — bbrstinfo reset-cause breakdown.

    Previously entirely unread. Deliberately NOT folded into the L8 health
    score: L8's five weighted signals (25/20/20/20/15%) are a closed system
    — adding a sixth would mean re-normalising every weight plus building
    new 30-day windowed-rate infrastructure this field doesn't have yet.
    nOomRst is also j-series-only (confirmed absent on Braava's bbrstinfo
    in the KingAntDesigns field captures), so it would be structurally
    unavailable for a large share of robots if it became a scored signal.
    A plain diagnostic sensor avoids all of that.

    native_value = nSafRst (safety-triggered resets — the most actionable
    single counter; nav/mobility resets are comparatively routine).
    extra_state_attributes carries the full breakdown, including nOomRst
    (out-of-memory resets) only where the firmware actually reports it.

    Gate: "bbrstinfo" in state — present on every robot captured so far
    (both Braava and j-series), but treated as optional rather than
    assumed universal, consistent with this project's general stance on
    field presence.
    """

    _attr_translation_key = "reset_diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_diagnostics"

    @property
    def suggested_object_id(self) -> str:
        return "reset_diagnostics"

    @property
    def _info(self) -> dict[str, Any]:
        return self.vacuum_state.get("bbrstinfo") or {}

    @property
    def native_value(self) -> int | None:
        """Safety-triggered reset count — the most actionable single number."""
        info = self._info
        if not info:
            return None
        return info.get("nSafRst")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full reset breakdown. nOomRst included only when the firmware
        reports it (confirmed j-series-only; absent on Braava)."""
        info = self._info
        attrs: dict[str, Any] = {
            "nav_resets": info.get("nNavRst"),
            "mobility_resets": info.get("nMobRst"),
            "safety_resets": info.get("nSafRst"),
            "safety_reset_causes": info.get("safCauses"),
        }
        if "nOomRst" in info:
            attrs["oom_resets"] = info.get("nOomRst")
        return attrs


class RoombaRelocalisationRateSensor(IRobotEntity, SensorEntity):
    """L9-MAP (v3.1.0) — self-calibrating relocalisation rate sensor.

    native_value = recent-window mean reLc per mission (rounded to 2dp),
    or None until reloc_baseline_ready (needs _RELOC_BASELINE_MIN_MISSIONS
    observations).

    extra_state_attributes expose the underlying baseline and window for
    troubleshooting and so a user/automation can see the comparison directly
    rather than just trusting the sensor's verdict.

    Gate: SMART-tier only — mssnNavStats is confirmed present on i7+/s9+
    (lewis firmware) via field data (Thonno), absent on 980/900-series.
    DIAGNOSTIC, disabled by default — this is a debugging/power-user signal,
    not a primary daily-use sensor, consistent with nav_quality (l_squal)
    which uses the same gating pattern.
    """

    _attr_translation_key = "relocalisation_rate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_relocalisation_rate"

    @property
    def suggested_object_id(self) -> str:
        return "relocalisation_rate"

    @property
    def _rps(self) -> Any:
        return self._entry.runtime_data.robot_profile_store

    @property
    def native_value(self) -> float | None:
        rps = self._rps
        if rps is None or not rps.reloc_baseline_ready:
            return None
        if not rps.recent_relocs:
            return None
        return round(sum(rps.recent_relocs) / len(rps.recent_relocs), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rps = self._rps
        if rps is None:
            return {
                "baseline": None,
                "baseline_mission_count": 0,
                "recent_window": [],
                "alert": False,
            }
        return {
            "baseline": round(rps.reloc_baseline, 2) if rps.reloc_baseline is not None else None,
            "baseline_mission_count": rps.reloc_mission_count,
            "recent_window": list(rps.recent_relocs),
            "alert": rps.reloc_alert_triggered(),
        }
