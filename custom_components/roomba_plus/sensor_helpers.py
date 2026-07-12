"""Descriptor value-functions for the Roomba+ sensor platform.

SENSOR-SPLIT (v3.4.0): extracted from the former sensor.py monolith.
Pure functions consumed by RoombaSensorDescription.value_fn (see
sensor_core.py) and, in a few cases, imported directly by other
sensor_* modules or by callbacks.py/device_tracker.py/repairs.py/
services.py via the sensor.py facade. No behaviour change vs. v3.3.1 —
this is a straight move, not a rewrite.
"""
from __future__ import annotations

from collections import Counter
from typing import Any
import datetime
import time as _time_mod

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import (
    CARPET_BOOST_LABELS,
    CLEAN_MODE_LABELS,
    CONF_BRUSH_HOURS,
    CONF_FILTER_HOURS,
    DEFAULT_BRUSH_HOURS,
    DEFAULT_FILTER_HOURS,
    DOMAIN,
    ERROR_CODE_LABELS,
    INTEGRATION_HEALTH_ARC1_STALE_HOURS,
    INTEGRATION_HEALTH_GOOD_THRESHOLD,
    INTEGRATION_HEALTH_LOW_THRESHOLD,
    INTEGRATION_HEALTH_MQTT_STALE_HOURS,
    NOT_READY_LABELS,
    PHASE_LABELS,
    SQFT_TO_M2,
    active_charge_cycles,
)
from .entity import IRobotEntity


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
    live = (entity.vacuum_state.get("cleanMissionStatus") or {}).get("error", 0)
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
