"""Constants for the Roomba+ integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from homeassistant.components.vacuum import VacuumActivity
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

# ── Domain ────────────────────────────────────────────────────────────────────
DOMAIN: Final = "roomba_plus"

# ── Platforms ─────────────────────────────────────────────────────────────────
LOCAL_PLATFORMS: Final[list[Platform]] = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.DEVICE_TRACKER,
    # v3.4.0 CAL — always present regardless of robot tier: scheduling
    # is a software feature virtually every iRobot model supports,
    # unlike map-dependent Platform.IMAGE (added conditionally further
    # down) which needs real pose/pmap hardware capability.
    Platform.CALENDAR,
    # v3.4.0 TODO — always present: filter/brush maintenance applies to
    # every robot tier. "Reconfigure rooms" (SMART-tier only, see
    # todo.py) simply never appears in the list on EPHEMERAL robots.
    Platform.TODO,
]

# Cloud credential keys — stored in config_entry.data (encrypted by HA)
CONF_IROBOT_USERNAME: Final = "irobot_username"
CONF_IROBOT_PASSWORD: Final = "irobot_password"

# Cloud-only platforms — added dynamically in __init__.py for SMART robots
# when cloud credentials are present.
CLOUD_PLATFORMS: Final[list[Platform]] = [
    Platform.SELECT,   # CloudRegionSelect, CloudZoneSelect (replace repair flow)
    Platform.BUTTON,   # FavoriteButton
]

# ── Config / Options keys ─────────────────────────────────────────────────────
CONF_BLID: Final = "blid"
CONF_CONTINUOUS: Final = "continuous"
CONF_DELAY: Final = "delay"
CONF_CERT: Final = "certificate"

# Options keys (Phase 2+)
CONF_MAP_ENABLED: Final = "map_enabled"
CONF_MAP_SIZE_PX: Final = "map_size_px"
CONF_MAP_SCALE: Final = "map_scale_mm_per_px"
CONF_FILTER_HOURS: Final = "filter_threshold_hours"
CONF_BRUSH_HOURS: Final = "brush_threshold_hours"

# ── v1.7.0 — L5 Blocking sensors ─────────────────────────────────────────────
CONF_BLOCKING_SENSORS: Final = "blocking_sensors"        # list[str] entity IDs
CONF_BLOCKING_BEHAVIOR: Final = "blocking_behavior"      # "abort" | "queue"
CONF_BLOCKING_TIMEOUT_MIN: Final = "blocking_timeout_min"

DEFAULT_BLOCKING_BEHAVIOR: Final = "queue"
DEFAULT_BLOCKING_TIMEOUT_MIN: Final = 30

# ── v1.8.0 — L6 Presence-aware scheduling ────────────────────────────────────
CONF_PRESENCE_SCHEDULING_ENABLED: Final = "presence_scheduling_enabled"
CONF_PRESENCE_ENTITIES: Final = "presence_entities"   # list[str] person entity IDs
CONF_PRESENCE_MODE: Final = "presence_mode"           # "away_only" | "always_ask"
CONF_AWAY_DELAY_MIN: Final = "away_delay_min"         # int, default 5

DEFAULT_PRESENCE_MODE: Final = "away_only"
DEFAULT_AWAY_DELAY_MIN: Final = 5

# L6 — Events
EVENT_ALL_AWAY: Final = f"{DOMAIN}_all_away"
EVENT_PERSON_DETECTED_DURING_CLEAN: Final = f"{DOMAIN}_person_detected_during_clean"

# v2.9.0 — EVENT-BUS. Unlike the L6 events above, these carry entry_id + name
# in their payload so multi-robot installs can distinguish the source robot
# without a separate lookup. The L6 events are intentionally left as-is
# (out of scope for this pass — see v2.8.6 session notes).
EVENT_MISSION_COMPLETED: Final = f"{DOMAIN}_mission_completed"
EVENT_ROOM_COMPLETED: Final = f"{DOMAIN}_room_completed"
EVENT_HEALTH_CHANGE: Final = f"{DOMAIN}_health_change"
EVENT_MAP_RETRAIN_STARTED: Final = f"{DOMAIN}_map_retrain_started"
EVENT_MAP_RETRAIN_COMPLETED: Final = f"{DOMAIN}_map_retrain_completed"
# v2.9.0 LOGBOOK — fired by services.py's shared reset helper, called from
# both the roomba_plus.reset_* services AND the Filter/Brush/Battery reset
# buttons (button.py) — one event regardless of which path the user took.
EVENT_MAINTENANCE_RESET: Final = f"{DOMAIN}_maintenance_reset"
EVENT_STUCK: Final = f"{DOMAIN}_stuck"  # v3.2.0 STUCK-CONTEXT

# v3.5.0 Repairs redesign — moment-shaped signals demoted from persistent
# Repair Issues to fire-once events (+ Logbook). Each fires only on the
# transition into the condition, not on every re-check while it persists —
# see _fire_once()/_disarm() in repairs.py.
EVENT_ERROR_RECURRENCE: Final = f"{DOMAIN}_error_recurrence"
EVENT_CANCELLATION_RECURRENCE: Final = f"{DOMAIN}_cancellation_recurrence"
EVENT_STUCK_PATTERN: Final = f"{DOMAIN}_stuck_pattern"
EVENT_MISSION_ANOMALY: Final = f"{DOMAIN}_mission_anomaly"
EVENT_MIXED_SCHEDULE: Final = f"{DOMAIN}_mixed_schedule"
EVENT_SCHEDULE_SUBOPTIMAL: Final = f"{DOMAIN}_schedule_suboptimal"
EVENT_MAP_DRIFT_DETECTED: Final = f"{DOMAIN}_map_drift_detected"
EVENT_MAP_RETRAIN_IN_PROGRESS: Final = f"{DOMAIN}_map_retrain_in_progress"
EVENT_CLOUD_STALE: Final = f"{DOMAIN}_cloud_stale"

# ── v1.7.0 — L7 Zone aliases & hidden ────────────────────────────────────────
# F11 — demand-based cleaning (DirtThresholdManager)
CONF_DEMAND_CLEANING_ENABLED: Final = "demand_cleaning_enabled"
CONF_DEMAND_MULTIPLIER: Final     = "demand_clean_multiplier"

CONF_SMART_ZONE_ALIASES: Final = "smart_zone_aliases"   # dict[str, str]: region_id → display name
CONF_SMART_ZONE_HIDDEN: Final = "smart_zone_hidden"     # list[str]: hidden region IDs

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_CONTINUOUS: Final = True
DEFAULT_DELAY: Final = 30
DEFAULT_CERT: Final = "/etc/ssl/certs/ca-certificates.crt"

DEFAULT_MAP_ENABLED: Final = True
DEFAULT_MAP_SIZE_PX: Final = 600
DEFAULT_MAP_SCALE: Final = 10.0  # mm per pixel → 600px = 6 m × 6 m

DEFAULT_FILTER_HOURS: Final = 60    # iRobot recommendation: every 2 months
DEFAULT_BRUSH_HOURS: Final = 200    # iRobot recommendation: every 6-12 months

ROOMBA_SESSION: Final = "roomba_session"

# ── clean_room action ─────────────────────────────────────────────────────────
SERVICE_CLEAN_ROOM: Final = "clean_room"
ATTR_ROOM_NAME: Final = "room_name"
ATTR_ORDERED: Final = "ordered"
ATTR_TWO_PASS: Final = "two_pass"
# CLEAN-ROOM-PER-ROOM-PASSES (v2.9.0) — optional structured field for
# individual pass control per room within the same multi-room sequence.
# Mutually exclusive with ATTR_ROOM_NAME at the service-call level.
# NOTE: named "room_passes", not "rooms" — ATTR_ROOMS ("rooms") already
# exists below for smart_start's plain string list; reusing that name would
# have collided with a different schema shape (list[str] vs list[dict]).
ATTR_ROOM_PASSES: Final = "room_passes"
# Options key — stores {region_id: {name, pmap_id}} for smart-map robots.
# Replaces the older flat smart_zone_labels dict; both are written on save
# so that a rollback to an older version still sees the label names.
CONF_SMART_ZONE_DATA: Final = "smart_zone_data"

# ── v1.7.0 — Services ────────────────────────────────────────────────────────
SERVICE_RESET_FILTER: Final = "reset_filter"
SERVICE_RESET_BRUSH: Final = "reset_brush"
SERVICE_RESET_BATTERY: Final = "reset_battery"
SERVICE_RESET_PAD: Final = "reset_pad"
SERVICE_SMART_START: Final = "smart_start"
ATTR_ROOMS: Final = "rooms"
ATTR_OVERRIDE_BLOCKING: Final = "override_blocking"

# ── v2.2.0 — new options keys ─────────────────────────────────────────────────
CONF_FLOOR: Final = "floor_label"          # str — user-assigned floor name for household view
CONF_CLEAN_DELAY_MIN: Final = "clean_delay_min"   # int — delay before second robot start (F10c)

DEFAULT_CLEAN_DELAY_MIN: Final = 0         # minutes

# v2.9.0 — CONFIRMED UNIT BUG (field data, 2026-06-19, 980 OG):
# firmware-reported pose.point.x / pose.point.y are in CENTIMETRES, not
# millimetres as every consumer in this codebase previously assumed (the
# "_mm" suffix on variables throughout image.py/callbacks.py/map_renderer.py/
# grid_store.py/zone_store.py was an unverified naming assumption, never
# checked against real hardware). Evidence: a real mid-mission checkpoint
# showed pose data confined to an apparent ~0.7m x 0.75m pocket; the user
# confirmed the robot had in fact covered roughly half of a 106 m² home by
# that point. Multiplying the raw pose values by 10 (cm -> mm) gives
# 7.0m x 7.5m = 52.5 m^2 = 49.5% of 106 m^2 — matching the user's own
# estimate almost exactly. This also explains why GAP_THRESHOLD_MM (800) /
# MIN_DOOR_WIDTH_MM (600) / MAX_DOOR_WIDTH_MM (1200) in zone_store.py could
# essentially never fire correctly: real ~600-1200mm door gaps were
# arriving as ~60-120 raw units, an order of magnitude under threshold.
# Apply this factor at every point pose.point.x/y is first read from the
# raw MQTT payload — never downstream, since downstream mm-calibrated
# constants (CELL_SIZE_MM, MAX_POSE_JUMP_MM, door widths, etc.) are correct
# once given genuine millimetres.
POSE_POINT_CM_TO_MM: Final = 10.0

# ── ROOM-SEG Stage 6 — relocated from zone_store.py (deleted) ────────────────
# Used by image.py's SMART-tier door-marker detection (inline gap-distance
# check on the raw pose trajectory, independent of EPHEMERAL's RoomSegStore
# pipeline) and by the historical comment above documenting the
# POSE_POINT_CM_TO_MM unit-mismatch bug.
GAP_THRESHOLD_MM: Final = 800     # > 80 cm gap -> doorway crossing
MIN_DOOR_WIDTH_MM: Final = 600.0  # Narrower -> likely furniture gap, not a door
MAX_DOOR_WIDTH_MM: Final = 1200.0 # Wider -> likely open archway

# ── v2.2.0 — new service ──────────────────────────────────────────────────────
SERVICE_CLEAN_SEQUENCE: Final = "clean_sequence"   # F10d — start robot B when robot A finishes
SERVICE_CLEAN_OVERDUE_ROOMS: Final = "clean_overdue_rooms"  # v3.3.0 ROOM-SCHED
SERVICE_AUTO_CLEAN_DIRTY_ROOMS: Final = "auto_clean_dirty_rooms"  # v3.3.0 SMART-ORDER
SERVICE_EXPLAIN_MISSION: Final = "explain_mission"  # v3.2.0 ANOMALY-EXPLAIN
SERVICE_CREATE_BACKUP: Final = "create_backup"  # v3.5.0 FULL-BACKUP
SERVICE_RESTORE_BACKUP: Final = "restore_backup"  # v3.5.0 FULL-BACKUP

# ── Roomba 980 hardware constants ─────────────────────────────────────────────
ROOMBA_CLEAN_WIDTH_MM: Final = 320  # 980 AeroForce cleaning path width

# ── Unit conversion ───────────────────────────────────────────────────────────
SQFT_TO_M2: Final = 0.09290304   # exact SI definition: 1 ft² = 0.09290304 m²

# ── RF0 — Robot manufacturer reference profiles ────────────────────────────────
# Prior data for all self-calibrating learning features.  Consumed by L1–L8
# and sensor.py for battery/maintenance computations.
#
# Data sources: iRobot support docs, FCC filings (battery mAh), NickWaterton
# cross-reference (confirmed June 2026).  Hours marked TODO need per-model
# iRobot support page verification.
#
# 9-series (Roomba 980): OEM battery is Li-ion 14.4V, confirmed June 2026.
# Aftermarket batteries exist as 14.4V NiMH or 14.8V Li-ion — aftermarket
# detection (L3) handles these at runtime via the estCap rolling-maximum.
# estCap BMS scaling: 9-series firmware reports raw_estcap ÷ 3.73 ≈ mAh for
# Li-ion; ÷ 1.87 for NiMH aftermarket packs.  Scalar applied in sensor.py.

@dataclass(frozen=True)
class RobotProfile:
    """Manufacturer reference data for a robot family.

    Used as the 'prior' by all self-calibrating features before enough
    personal history exists.
    """
    name: str
    battery_mah: int            # nominal NEW battery capacity (mAh)
    battery_chemistry: str      # "lipo" | "nimh"
    battery_voltage: float      # nominal pack voltage (V)
    battery_cycles_eol: int     # manufacturer rated cycle count at ~80% capacity
    filter_hours: int           # recommended replacement interval (h)  TODO: per-model
    main_brush_hours: int       # recommended replacement interval (h)  TODO: per-model
    side_brush_hours: int       # recommended replacement interval (h)  TODO: per-model
    typical_coverage_sqft: int | None   # typical per-mission area; None for mops
    map_capability: str         # "none" | "ephemeral" | "smart"
    # estCap BMS scaling factors — confirmed June 2026.
    # For i/s/j/e/6/m series: raw estCap == mAh directly → scale = 1.0.
    # For 9-series old firmware: raw estCap is chemistry-scaled by the BMS:
    #   Li-ion: raw ÷ 3.73 ≈ mAh   NiMH: raw ÷ 1.87 ≈ mAh  (ratio = 2.0 exactly)
    # Usage: actual_mah = raw_estcap / estcap_scale_liion (or _nimh)
    estcap_scale_liion: float = 1.0
    estcap_scale_nimh:  float = 1.0


ROBOT_PROFILES: Final[dict[str, RobotProfile]] = {
    # key = first character of SKU (case-insensitive), matching iRobot SKU convention
    "6": RobotProfile(
        name="600-series",
        battery_mah=1800, battery_chemistry="nimh", battery_voltage=14.4,
        battery_cycles_eol=400,
        filter_hours=60, main_brush_hours=120, side_brush_hours=120,
        typical_coverage_sqft=800, map_capability="none",
    ),
    "e": RobotProfile(
        name="e-series",
        battery_mah=1800, battery_chemistry="lipo", battery_voltage=14.8,
        battery_cycles_eol=400,
        filter_hours=60, main_brush_hours=150, side_brush_hours=150,
        typical_coverage_sqft=1000, map_capability="ephemeral",
    ),
    "9": RobotProfile(
        # OEM: Li-ion 14.4V, confirmed June 2026 (Roomba 980 R980040).
        # battery_mah=3300 confirmed via raw_estcap ÷ 3.73 ≈ 3300 mAh nominal.
        # Aftermarket: 14.4V NiMH (÷1.87) or 14.8V Li-ion (÷3.73).
        name="900-series",
        battery_mah=3300, battery_chemistry="lipo", battery_voltage=14.4,
        battery_cycles_eol=400,
        filter_hours=60, main_brush_hours=150, side_brush_hours=150,
        typical_coverage_sqft=1500, map_capability="ephemeral",
        estcap_scale_liion=3.73,   # raw ÷ 3.73 = mAh for OEM Li-ion
        estcap_scale_nimh=1.87,    # raw ÷ 1.87 = mAh for NiMH aftermarket
    ),
    "i": RobotProfile(
        name="i-series",
        # v2.8.0 RF0-IMAH: corrected from manufacturer spec (1800 mAh) to
        # field-validated value from 3 community robots (Thonno i7+, veronoicc
        # i7+ / i8+): estCap median ≈ 2488 mAh on lewis firmware (directly in
        # mAh, no BMS scale factor needed unlike 9-series).
        # Previous value (1800) caused battery_capacity_retention to read
        # ~138% for a healthy battery, making the sensor meaningless.
        battery_mah=2488, battery_chemistry="lipo", battery_voltage=14.8,
        battery_cycles_eol=400,
        filter_hours=60, main_brush_hours=150, side_brush_hours=150,
        typical_coverage_sqft=1200, map_capability="smart",
    ),
    "j": RobotProfile(
        name="j-series",
        battery_mah=2700, battery_chemistry="lipo", battery_voltage=14.8,
        battery_cycles_eol=300,
        filter_hours=60, main_brush_hours=150, side_brush_hours=150,
        typical_coverage_sqft=1400, map_capability="smart",
    ),
    "s": RobotProfile(
        name="s9-series",
        battery_mah=3300, battery_chemistry="lipo", battery_voltage=14.8,
        battery_cycles_eol=300,
        filter_hours=60, main_brush_hours=200, side_brush_hours=200,
        typical_coverage_sqft=2000, map_capability="smart",
    ),
    "m": RobotProfile(
        name="Braava m6",
        battery_mah=2600, battery_chemistry="lipo", battery_voltage=14.8,
        battery_cycles_eol=300,
        filter_hours=60, main_brush_hours=0, side_brush_hours=0,
        typical_coverage_sqft=None, map_capability="smart",
    ),
}

# APK-CONFIG-VERIFY (July 2026) — SKU prefixes confirmed real by iRobot's own
# res/raw/base_roomba_config.json (Classic app decompilation), covering the
# full public SKU list: R1/R67/R69/R89/R96/R97/R98, e4/e5/e6, s5/s9, m6,
# i1-i8, c3/c7, t72, q7, j7/j8/j9, a, p, q0, y0 (XST0020 is a VMRS test rig,
# excluded here). Prefixes with no ROBOT_PROFILES entry below are genuine
# iRobot product families (at minimum Combo "c", plus a/p/q/t/y) that no
# field tester in this project currently owns — not typos or noise. Kept
# separate from ROBOT_PROFILES so an unmatched-but-known prefix can be
# logged distinctly from a truly unrecognised one (see get_robot_profile()).
_KNOWN_IROBOT_SKU_PREFIXES: Final[frozenset[str]] = frozenset(
    "6 e r a c i j m p q s t y".split()
)


def get_robot_profile(
    sku: str | None,
    battery_type: str | None = None,
) -> RobotProfile | None:
    """Return the RobotProfile for a given SKU string, or None when unknown.

    Matches on the first character of the SKU (case-insensitive):
        "i755840"  → ROBOT_PROFILES["i"]   (i-series)
        "R980040"  → ROBOT_PROFILES["9"]   (900-series, note: "R" is not "9"!)
        "s955840"  → ROBOT_PROFILES["s"]   (s9-series)

    Note: some iRobot SKUs start with "R" for 900-series (e.g. "R980040").
    These are handled by the "r" → "9" alias below.

    battery_type — when provided (from the live MQTT ``batteryType`` field),
    overrides the profile's default ``battery_chemistry``.  This matters for
    900-series robots where the OEM Li-Ion pack may have been replaced with an
    aftermarket NiMH pack (or vice-versa).  The profile always stores both
    ``estcap_scale_liion`` and ``estcap_scale_nimh``; only the active chemistry
    selector needs to be updated so battery-related sensors pick the right
    scale factor.

    Example: Roomba 980 with aftermarket NiMH battery →
        battery_type="nimh", profile default="lipo"
        → returned profile has battery_chemistry="nimh"
        → battery_capacity_retention uses estcap_scale_nimh (1.87×) ✅
    """
    if not sku:
        return None
    prefix = sku[0].lower()
    # "r" prefix used on some 900-series SKUs (R980040, R960040)
    if prefix == "r":
        prefix = "9"
    profile = ROBOT_PROFILES.get(prefix)
    if profile is None:
        # APK-CONFIG-VERIFY — distinguish "real iRobot SKU family we just
        # haven't profiled yet" (e.g. a Combo "c"-series) from a genuinely
        # unrecognised prefix, so a field report about this is actionable
        # instead of a silent no-op.
        original_prefix = sku[0].lower()
        if original_prefix in _KNOWN_IROBOT_SKU_PREFIXES:
            _LOGGER.info(
                "Roomba+: SKU '%s' belongs to a known iRobot product family "
                "('%s'-prefix) without a RobotProfile entry yet — self-"
                "calibrating features will have no manufacturer prior until "
                "one is added.",
                sku, original_prefix,
            )
        else:
            _LOGGER.debug(
                "Roomba+: SKU '%s' has an unrecognised prefix — no "
                "RobotProfile available.",
                sku,
            )
        return None

    # Override battery_chemistry from live device state when it differs from
    # the profile default.  Only "lipo" and "nimh" are recognised; unknown
    # values are silently ignored so the profile default is preserved.
    if battery_type and battery_type.lower() in ("lipo", "nimh"):
        resolved = battery_type.lower()
        if resolved != profile.battery_chemistry:
            import dataclasses as _dc
            profile = _dc.replace(profile, battery_chemistry=resolved)

    return profile

# ── State/Phase mappings ──────────────────────────────────────────────────────
# Extended phase map (superset of Core's STATE_MAP)

PHASE_TO_ACTIVITY: Final[dict[str, VacuumActivity]] = {
    "": VacuumActivity.IDLE,
    "charge": VacuumActivity.DOCKED,
    "evac": VacuumActivity.RETURNING,
    "hmMidMsn": VacuumActivity.CLEANING,
    "hmPostMsn": VacuumActivity.RETURNING,
    "hmUsrDock": VacuumActivity.RETURNING,
    "pause": VacuumActivity.PAUSED,
    "run": VacuumActivity.CLEANING,
    "stop": VacuumActivity.IDLE,
    "stuck": VacuumActivity.ERROR,
}

# v2.3.0 — Phases used by image.py (pose handling) and vacuum.py (live CR4 source).
# Moved from image.py module-locals so vacuum.py can import without circular deps.
# v2.6.3 B1 — evac moved to CLEANING_PHASES: robots with self-emptying bases
# (i7+, s9+) go through evac mid-mission; treating it as MISSION_END would
# prematurely trigger _handle_mission_end() and reset the map renderer.
CLEANING_PHASES: Final[frozenset[str]] = frozenset({"run", "hmMidMsn", "evac"})
MISSION_END_PHASES: Final[frozenset[str]] = frozenset({"charge", "hmPostMsn", "stop"})

# v2.8.1 (END-DEBOUNCE) — shared between callbacks.py (MissionTimerStore /
# MissionStore mission-end detection) and image.py (map renderer / ZoneStore /
# GeometryStore / GridStore / OutlineStore mission-end detection). Both files
# independently implement the same "is this phase a genuine mission end or a
# transient inter-room transition blip" check; these two phases are the ones
# observed to appear both at genuine mission end AND transiently between
# rooms — "stop"/"completed"/"cancelled" are unambiguous, deliberate terminal
# phases never used for inter-room signalling and always confirm immediately.
ROOM_TRANSITION_CANDIDATE_PHASES: Final[frozenset[str]] = frozenset({"charge", "hmPostMsn"})
# Number of consecutive "looks like a genuine end" messages required on an
# ambiguous phase before committing to end-of-mission processing.
END_SIGNAL_DEBOUNCE_COUNT: Final[int] = 2
# v2.8.3 — minimum wall-clock seconds the end signal must have been active
# before committing to a mission end on an ambiguous phase (charge/hmPostMsn).
# Lewis firmware 22.52.10 sends exactly END_SIGNAL_DEBOUNCE_COUNT rapid
# cleanMissionStatus messages (~21 ms apart) during an inter-room transition,
# both with cycle outside {"clean","quick"} — this exactly satisfies the
# count-only gate, causing a false MissionTimerStore.clear().  A genuine
# mission end (robot docked) holds the "end-looking" signal for many seconds
# between MQTT updates; a burst resolves in milliseconds.  Requiring the
# signal to persist for at least this many seconds defeats the burst case
# without meaningfully delaying genuine end detection.
# Applies only to ROOM_TRANSITION_CANDIDATE_PHASES (ambiguous phases).
# Unambiguous terminal phases (stop/completed/cancelled) are unaffected.
END_SIGNAL_MIN_HOLD_SECONDS: Final[float] = 2.0

# v2.9.0 — UNVISITED-ROOMS SAFETY CAP. ROOM-INDEX CORROBORATION (added
# v2.9.0 for Thonno's progress-reset report) suppresses end confirmation on
# an ambiguous phase while MissionTimerStore still has unvisited planned
# rooms — but this assumes current_room_idx reliably advances via
# AUTO-ADVANCE-ROOM. Confirmed broken in the field (Thonno, i7+, lewis
# 22.52.10, 2026-06-19): a genuine 2-room mission that the robot fully
# completed and docked from never confirmed — end_signal_streak reached 10,
# time_held reached 66+ seconds, yet unvisited_rooms stayed True the entire
# time (current_room_idx never advanced past 0), permanently blocking
# MissionStore recording and MissionTimerStore.clear(). A corroboration
# signal that can hang a real mission end forever is worse than the
# false-positive it was built to prevent. This cap bounds the room-index
# suppression: past this many seconds of held ambiguous-phase signal, end
# confirmation proceeds regardless of unvisited_rooms — either
# current_room_idx tracking is unreliable for this firmware/scenario, or
# it really is a genuine end; either way, never hang indefinitely.
UNVISITED_ROOMS_MAX_SUPPRESSION_SECONDS: Final[float] = 90.0

# v2.8.3 — Cloud-staleness threshold (CLOUD-STALE Repair Issue).
# A cloud coordinator that has not successfully refreshed for this many minutes
# fires the cloud_stale Repair Issue.  Chosen to be comfortably larger than
# the coordinator's default 30-minute update interval — two consecutive missed
# refreshes before alerting.
CLOUD_STALE_MINUTES: Final[int] = 60

# v2.8.3 — MQTT-watchdog silence threshold (MQTT-WATCHDOG Repair Issue).
# If no MQTT message arrives for this many seconds while phase==run is active,
# the mqtt_stale binary sensor turns ON and the mqtt_watchdog Repair Issue fires.
# 300 s (5 min) is conservative — the robot normally sends multiple messages
# per minute while cleaning.
MQTT_WATCHDOG_SECONDS: Final[int] = 300

# v2.9.0 BUGFIX (field reports: boutXIII, Jean-Christoph — both observed the
# Repair Issue firing with minutes≈5, i.e. right at MQTT_WATCHDOG_SECONDS)
# — a genuine, common MQTT gap of a few minutes right after undocking
# (Wi-Fi reassociation while the robot physically moves away from the
# router, motor startup interference; the 980 OG with its aftermarket
# NiMH battery is more prone to this than newer i/s/j-series robots) was
# being misreported as a sustained connectivity problem. The watchdog's
# last-received message already showed phase=="run" before the gap, so it
# fired the instant the 5-minute silence threshold was crossed, regardless
# of how recently the mission had actually started.
# 420 s (7 min) chosen with margin above the observed ~5 min reports —
# exact gap duration wasn't precisely measured in either field report, so
# this errs slightly generous rather than risk re-introducing false
# positives at a tighter value. Suppresses the watchdog entirely (not just
# resets the silence clock) until this many seconds after mssnStrtTm — a
# genuine outage starting early in the mission is still caught once both
# this grace window AND the normal silence threshold have elapsed.
MQTT_WATCHDOG_START_GRACE_SECONDS: Final[int] = 420

# v2.9.0 — INTEG-HEALTH meta-sensor thresholds.
# Score (0-100) combines: active Repair Issue count, MQTT message age, and
# ARC1 (MissionArchive) freshness. See sensor.py's _compute_integration_health
# docstring for the exact scoring formula and the rationale for which
# originally-planned signals (cloud age, "last store save") were folded in
# or dropped.
INTEGRATION_HEALTH_TICK_SECONDS: Final[int] = 60
INTEGRATION_HEALTH_LOW_THRESHOLD: Final[int] = 50
INTEGRATION_HEALTH_SUSTAINED_MINUTES: Final[int] = 30
# MQTT silence beyond this is penalised even outside an active mission —
# distinct from MQTT_WATCHDOG_SECONDS (5 min, mission-specific): this is a
# much longer bar meant to catch "the integration's local connection appears
# entirely dead", not routine idle-time quiet.
INTEGRATION_HEALTH_MQTT_STALE_HOURS: Final[float] = 24.0
# ARC1 (MissionArchive) freshness — the newest archived mission is older
# than this despite cloud being enabled, suggesting the sync pipeline itself
# may be stuck (distinct from CLOUD-STALE, which only checks whether the
# coordinator's OWN refresh call is succeeding, not whether new missions are
# actually making it into the archive).
INTEGRATION_HEALTH_ARC1_STALE_HOURS: Final[float] = 48.0
# v2.9.0 EVENT-BUS — band split for health_change event (band-crossing only,
# not raw delta, to avoid event spam on minor score jitter). Three bands:
# critical (<50, shares INTEGRATION_HEALTH_LOW_THRESHOLD), degraded
# (50-79), healthy (>=80).
INTEGRATION_HEALTH_GOOD_THRESHOLD: Final[int] = 80
# v2.9.0 TRIGGER+ — ordinal ranking of sensor.py's _health_band() output
# strings, shared here (not duplicated in device_trigger.py) so the
# health_score_drop device trigger's "did it get worse" comparison can
# never silently diverge from the band names _health_band() actually
# returns. Higher = healthier.
HEALTH_BAND_RANK: Final[dict[str, int]] = {"critical": 0, "degraded": 1, "healthy": 2}

# v2.9.0 MAP-RETRAIN-WF — cleanMissionStatus.notReady is a bitmask; bit 64
# means "Smart Map updating" (services.py's clean_room guard already checks
# this exact bit — named here so both call sites share one source instead
# of two independent magic-number "64"s silently drifting apart).
MAP_UPDATING_NOT_READY_BIT: Final[int] = 64
# Escalation thresholds for the map_retrain_workflow Repair Issue: WARNING
# once notReady&64 has been continuously set for this long (a normal retrain
# is usually done within a few minutes; this is a conservative first-pass
# value, not derived from field data), ERROR if it's still set after the
# longer threshold (genuinely stuck, not just slow).
MAP_RETRAIN_WARN_MINUTES: Final[int] = 15
MAP_RETRAIN_STUCK_MINUTES: Final[int] = 45
# v2.9.0 — maintenance_due Repair Issue grace period. Unlike health_change
# (a noisy signal needing a sustained-duration gate to reject jitter),
# hours-since-reset is monotonic and never flickers once "due" — the grace
# period here is purely about not nagging the user for a marginal overage
# (filter hours are a heuristic threshold, not safety-critical), not about
# rejecting noise.
MAINTENANCE_DUE_GRACE_DAYS: Final[int] = 3

# Human-readable phase labels (from rest980 — extended)

# ── v1.8.0 — Error catalogue with descriptions and suggested actions ──────────
# Replaces the flat ERROR_CODE_LABELS dict. All existing sensor code that reads
# ERROR_CODE_LABELS continues to work unchanged — it is now a derived view.
ERROR_CATALOGUE: Final[dict[int, dict[str, str]]] = {
    0:   {"label": "None",                     "description": "No error.",                                                  "action": ""},
    1:   {"label": "Left wheel off floor",      "description": "The left wheel has lifted off the floor.",                  "action": "Check for objects under the robot and place it on a flat surface."},
    2:   {"label": "Main brushes stuck",        "description": "The main brush roll is jammed.",                            "action": "Remove the brush roll and clear hair or debris, then reinsert."},
    3:   {"label": "Right wheel off floor",     "description": "The right wheel has lifted off the floor.",                 "action": "Check for objects under the robot and place it on a flat surface."},
    4:   {"label": "Left wheel stuck",          "description": "The left wheel is stuck or jammed.",                        "action": "Remove any debris from around the left wheel and restart."},
    5:   {"label": "Right wheel stuck",         "description": "The right wheel is stuck or jammed.",                       "action": "Remove any debris from around the right wheel and restart."},
    6:   {"label": "Stuck near a cliff",        "description": "The robot is stuck near a drop-off or step.",               "action": "Move the robot to a flat surface away from stairs and restart."},
    7:   {"label": "Left wheel error",          "description": "The left wheel is not responding correctly.",                "action": "Check the wheel for obstructions. Reboot the robot if the error persists."},
    8:   {"label": "Bin error",                 "description": "The dust bin has an issue.",                                 "action": "Remove, empty, and reinsert the dust bin until it clicks."},
    9:   {"label": "Bumper stuck",              "description": "The front bumper is jammed or stuck.",                      "action": "Tap the bumper to free it and clear any debris around it."},
    10:  {"label": "Right wheel error",         "description": "The right wheel is not responding correctly.",               "action": "Check the wheel for obstructions. Reboot the robot if the error persists."},
    11:  {"label": "Bin error",                 "description": "The dust bin has an issue.",                                 "action": "Remove, empty, and reinsert the dust bin until it clicks."},
    12:  {"label": "Cliff sensor issue",        "description": "A cliff sensor is dirty or giving incorrect readings.",      "action": "Clean the cliff sensors on the underside with a dry cloth."},
    13:  {"label": "Both wheels off floor",     "description": "Both wheels have lifted off the floor.",                    "action": "Place the robot on a flat, level surface."},
    14:  {"label": "Bin missing",               "description": "The dust bin is not installed.",                            "action": "Insert the dust bin until it clicks into place."},
    15:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    16:  {"label": "Bumped unexpectedly",       "description": "The robot detected an unexpected bump.",                    "action": "Check for unstable objects near the robot's path."},
    17:  {"label": "Path blocked",              "description": "An obstacle is blocking the robot's path.",                 "action": "Clear the path and restart."},
    18:  {"label": "Docking issue",             "description": "The robot cannot find or dock at its home base.",           "action": "Check that the home base is plugged in and the path is clear."},
    19:  {"label": "Undocking issue",           "description": "The robot could not leave the home base.",                  "action": "Check that the home base area is clear and restart."},
    20:  {"label": "Docking issue",             "description": "The robot encountered a problem docking.",                  "action": "Check the home base contacts and clear any obstacles nearby."},
    21:  {"label": "Navigation problem",        "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    22:  {"label": "Navigation problem",        "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    23:  {"label": "Battery issue",             "description": "A battery problem has been detected.",                      "action": "Place the robot on its home base. Contact support if the issue persists."},
    24:  {"label": "Navigation problem",        "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    25:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    26:  {"label": "Vacuum problem",            "description": "The vacuum suction system has a problem.",                  "action": "Check the filter and bin for blockages. Reboot the robot."},
    27:  {"label": "Vacuum problem",            "description": "The vacuum suction system has a problem.",                  "action": "Check the filter and bin for blockages. Reboot the robot."},
    29:  {"label": "Software update needed",    "description": "A software update is required.",                           "action": "Connect the robot to Wi-Fi and allow the update to complete."},
    30:  {"label": "Vacuum problem",            "description": "The vacuum suction system has a problem.",                  "action": "Check the filter and bin for blockages. Reboot the robot."},
    31:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    32:  {"label": "Smart map problem",         "description": "The robot encountered an error with its Smart Map.",        "action": "Retrain the Smart Map in the iRobot app."},
    33:  {"label": "Path blocked",              "description": "An obstacle is blocking the robot's path.",                 "action": "Clear the path and restart."},
    34:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    35:  {"label": "Unrecognised cleaning pad", "description": "The mop pad type could not be identified.",                "action": "Remove the pad, clean the pad tray contacts, and reattach."},
    36:  {"label": "Bin full",                  "description": "The dust bin is full.",                                     "action": "Empty the bin and tap Clean to continue."},
    37:  {"label": "Tank needs refilling",      "description": "The water tank is empty or low.",                          "action": "Fill the water tank and reinsert it."},
    38:  {"label": "Vacuum problem",            "description": "The vacuum suction system has a problem.",                  "action": "Check the filter and bin for blockages. Reboot the robot."},
    39:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    40:  {"label": "Navigation problem",        "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    41:  {"label": "Timed out",                 "description": "The robot timed out waiting for a condition.",              "action": "Restart the mission from the iRobot app."},
    42:  {"label": "Localisation problem",      "description": "The robot cannot determine its position on the map.",       "action": "Place the robot in an open area and restart. Consider retraining the Smart Map."},
    43:  {"label": "Navigation problem",        "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    44:  {"label": "Pump issue",                "description": "The mop pump is not responding.",                           "action": "Check the water tank and clean the pump inlet."},
    45:  {"label": "Lid open",                  "description": "The robot lid is open.",                                   "action": "Close the lid securely before starting a mission."},
    46:  {"label": "Low battery",               "description": "The battery is too low to start a clean.",                 "action": "Place the robot on the home base and wait for it to charge."},
    47:  {"label": "Reboot required",           "description": "The robot requires a reboot to continue.",                  "action": "Press and hold the Clean button for 10 seconds to reboot."},
    48:  {"label": "Path blocked",              "description": "A virtual wall or obstacle blocked the robot.",             "action": "Clear the path or move the virtual wall barrier."},
    52:  {"label": "Pad requires attention",    "description": "The cleaning pad needs to be replaced or reattached.",     "action": "Replace the pad or check the pad tray for secure attachment."},
    53:  {"label": "Software update required",  "description": "A critical software update is required.",                  "action": "Connect the robot to Wi-Fi and allow the update to complete."},
    65:  {"label": "Hardware problem detected", "description": "A hardware component has reported a fault.",               "action": "Reboot the robot. Contact iRobot support if the error persists."},
    66:  {"label": "Low memory",                "description": "The robot's software encountered a memory issue.",         "action": "Reboot the robot. Contact iRobot support if the error persists."},
    68:  {"label": "Updating map",              "description": "A Smart Map update is in progress.",                       "action": "Wait for the map update to complete before sending new commands."},
    73:  {"label": "Pad type changed",          "description": "A different pad type has been detected.",                  "action": "Confirm the correct pad is attached in the iRobot app."},
    74:  {"label": "Max area reached",          "description": "The robot has reached the maximum cleanable area.",        "action": "This is informational. Dock and recharge, then continue if needed."},
    75:  {"label": "Navigation problem",        "description": "The robot could not complete navigation in time.",         "action": "Clear the area of obstacles and try again."},
    76:  {"label": "Hardware problem detected", "description": "A hardware component has reported a fault.",               "action": "Reboot the robot. Contact iRobot support if the error persists."},
    # v3.4.1 — codes 78/79/85/86/91-93/98/99 confirmed from direct iRobot Home
    # app APK analysis (push_notification_error_*/history_error_* string
    # resources, Klartext-verified). Codes 54-72/94-97 in the same numeric
    # neighbourhood were checked and excluded — those belong to iRobot's Terra
    # lawn-mower product line (shared app namespace), not vacuum/mop robots.
    78:  {"label": "Left wheel error",           "description": "The left wheel is not responding correctly.",               "action": "Check the wheel for obstructions. Reboot the robot if the error persists."},
    79:  {"label": "Right wheel error",          "description": "The right wheel is not responding correctly.",              "action": "Check the wheel for obstructions. Reboot the robot if the error persists."},
    85:  {"label": "Path to charging station blocked", "description": "The robot could not reach the home base.",            "action": "Ensure the path to the home base is clear and unobstructed."},
    86:  {"label": "Path to charging station blocked", "description": "The robot could not reach the home base.",            "action": "Ensure the path to the home base is clear and unobstructed."},
    88:  {"label": "Back-up refused",           "description": "The robot could not back up as required.",                 "action": "Check for obstacles behind the robot and clear the area."},
    89:  {"label": "Mission runtime too long",  "description": "The mission exceeded the maximum allowed runtime.",        "action": "The robot will dock and resume after charging."},
    # v3.4.1 — see comment above codes 78/79/85/86 for source/exclusion notes.
    91:  {"label": "Workspace path error",       "description": "The robot's understanding of the workspace no longer matches its surroundings.", "action": "Retrain the map for this space."},
    92:  {"label": "Workspace path error",       "description": "The robot's understanding of the workspace no longer matches its surroundings.", "action": "Retrain the map for this space."},
    93:  {"label": "Workspace path error",       "description": "The robot's understanding of the workspace no longer matches its surroundings.", "action": "Retrain the map for this space."},
    98:  {"label": "Software error",             "description": "An internal software error occurred.",                     "action": "Reboot the robot. Contact iRobot support if the error persists."},
    99:  {"label": "Navigation problem",         "description": "The robot is having trouble navigating.",                   "action": "Clear the area of obstacles and ensure good lighting."},
    101: {"label": "Battery not connected",     "description": "The battery is not detected.",                            "action": "Check that the battery is firmly seated. Contact support if needed."},
    102: {"label": "Charging error",            "description": "A charging error has occurred.",                          "action": "Check the home base contacts and the robot's charging port for debris."},
    103: {"label": "Charging error",            "description": "A charging error has occurred.",                          "action": "Check the home base contacts and the robot's charging port for debris."},
    104: {"label": "No charge current",         "description": "No charging current is being received.",                  "action": "Check the home base power cable and outlet. Clean the charging contacts."},
    105: {"label": "Charging current too low",  "description": "The charging current is below the expected level.",       "action": "Clean the charging contacts on the robot and home base."},
    106: {"label": "Battery too warm",          "description": "The battery temperature is too high to charge.",          "action": "Move the robot to a cooler location and wait before charging."},
    107: {"label": "Battery temperature incorrect", "description": "The battery temperature reading is out of range.",    "action": "Let the robot cool down, then attempt charging again."},
    108: {"label": "Battery communication failure", "description": "The robot cannot communicate with the battery.",      "action": "Reboot the robot. Contact support if the error persists."},
    109: {"label": "Battery error",             "description": "A battery error has been detected.",                      "action": "Reboot the robot. Contact support if the error persists."},
    110: {"label": "Battery cell imbalance",    "description": "Battery cells are out of balance.",                       "action": "Fully discharge and recharge the battery. Contact support if persistent."},
    111: {"label": "Battery communication failure", "description": "The robot cannot communicate with the battery.",      "action": "Reboot the robot. Contact support if the error persists."},
    112: {"label": "Invalid charging load",     "description": "The charging load is not as expected.",                   "action": "Check the home base and cable. Try a different outlet."},
    114: {"label": "Internal battery failure",  "description": "An internal battery failure has been detected.",          "action": "Contact iRobot support for battery replacement."},
    115: {"label": "Cell failure during charging", "description": "A battery cell failed during a charging cycle.",       "action": "Contact iRobot support for battery replacement."},
    116: {"label": "Charging error of home base", "description": "The home base has a charging error.",                   "action": "Unplug and replug the home base. Try a different outlet."},
    118: {"label": "Battery communication failure", "description": "The robot cannot communicate with the battery.",      "action": "Reboot the robot. Contact support if the error persists."},
    119: {"label": "Charging timeout",          "description": "The charging cycle timed out.",                           "action": "Check the home base contacts and try restarting the charging cycle."},
    120: {"label": "Battery not initialised",   "description": "The battery has not been initialised.",                   "action": "Reboot the robot. Contact support if the error persists."},
    122: {"label": "Charging system error",     "description": "The charging system has encountered an error.",           "action": "Check the home base and cable. Contact support if the error persists."},
    123: {"label": "Battery not initialised",   "description": "The battery has not been initialised.",                   "action": "Reboot the robot. Contact support if the error persists."},
    # IA74-EC additions (v2.5.0): codes 130–215 confirmed from ia74/jeremywillans references
    130: {"label": "Back-up limit detected",    "description": "The robot detected a back-up limit during cleaning.",      "action": "Remove obstacles behind the robot and retry."},
    131: {"label": "Obstacle following failed", "description": "The robot failed to navigate around an obstacle.",         "action": "Clear obstacles from the cleaning area."},
    132: {"label": "Hardware error",            "description": "A hardware component is not responding correctly.",        "action": "Reboot the robot. Contact support if the error persists."},
    133: {"label": "Timed out navigating",      "description": "Navigation took longer than expected.",                    "action": "Ensure the robot has a clear path and retry."},
    134: {"label": "Failed to recharge",        "description": "The robot could not locate or reach the dock to recharge.", "action": "Check the dock is accessible and unobstructed."},
    140: {"label": "Left brush error",          "description": "The left brush has stalled or is blocked.",               "action": "Clean the left brush and its guards."},
    141: {"label": "Right brush error",         "description": "The right brush has stalled or is blocked.",              "action": "Clean the right brush and its guards."},
    160: {"label": "Navigation problem",        "description": "The robot has a general navigation problem.",             "action": "Place the robot in an open area and restart the mission."},
    161: {"label": "Dock not found",            "description": "The robot could not find the dock after cleaning.",       "action": "Ensure the dock is plugged in and unobstructed."},
    162: {"label": "Low battery — abort",       "description": "Battery too low to complete the mission.",               "action": "Allow the robot to charge fully before the next mission."},
    163: {"label": "Mission failed",            "description": "The mission could not be completed.",                     "action": "Check for obstacles and retry."},
    216: {"label": "Charging base bag full",    "description": "The Clean Base bag is full and needs replacing.",         "action": "Replace the Clean Base bag."},
    224: {"label": "Smart Map localization failed", "description": "The robot could not localise on its Smart Map.",      "action": "Place the robot in an open area on the map and try again. Retrain the map if needed."},
    # v3.4.1 — Combo wet-mopping tank/dock error category (450-463, 501-509),
    # confirmed from direct iRobot Home app APK analysis (dock_history_error_*
    # string resources, Klartext-verified). Consistent degradation pattern:
    # the robot switches to vacuum-only rather than failing the mission outright.
    450: {"label": "Tank missing",              "description": "The clean water tank is missing. Switched to vacuum only.", "action": "Insert the clean water tank."},
    451: {"label": "Tank low",                  "description": "The clean water tank is low. Switched to vacuum only.",     "action": "Refill the clean water tank."},
    452: {"label": "Tank hardware issue",       "description": "A tank hardware issue was detected. Switched to vacuum only.", "action": "Reseat the tank. Contact iRobot support if the error persists."},
    453: {"label": "Port clog",                 "description": "A refill port is clogged. Switched to vacuum only.",      "action": "Clean the refill port on the dock and robot."},
    454: {"label": "Nozzle clog",                "description": "A nozzle is clogged. Switched to vacuum only.",           "action": "Clean the mopping nozzle."},
    455: {"label": "Clean Base pump issue",      "description": "The Clean Base pump has an issue.",                       "action": "Check the Clean Base for blockages. Contact support if the error persists."},
    456: {"label": "Incorrect bin",              "description": "The wrong bin type is installed. Switched to vacuum only.", "action": "Install the correct bin for this robot."},
    457: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    458: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    459: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    460: {"label": "Level sensor issue",         "description": "A tank level sensor issue was detected. Switched to vacuum only.", "action": "Clean the tank and level sensor. Contact support if the error persists."},
    461: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    462: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    463: {"label": "Possible leak",              "description": "A possible leak was detected. Switched to vacuum only.",  "action": "Check the tank and connections for leaks. Contact support if the error persists."},
    501: {"label": "Unable to refill",           "description": "The robot was unable to refill. Switched to vacuum only.", "action": "Check the Clean Base water supply and refill connections."},
    502: {"label": "Charging contacts need cleaning", "description": "The charging contacts need cleaning.",               "action": "Clean the charging contacts on the robot and Clean Base."},
    503: {"label": "Communication error",       "description": "A communication error occurred. Switched to vacuum only.", "action": "Reboot the robot. Contact support if the error persists."},
    504: {"label": "Refill docking issue",       "description": "The robot had a refill docking issue. Switched to vacuum only.", "action": "Check that the robot is docking correctly at the Clean Base."},
    505: {"label": "Communication error",       "description": "A communication error occurred. Reboot required.",         "action": "Reboot the robot."},
    506: {"label": "Communication error",       "description": "A communication error occurred. Reboot required.",         "action": "Reboot the robot."},
    507: {"label": "Communication error",       "description": "A communication error occurred. Reboot required.",         "action": "Reboot the robot."},
    508: {"label": "Clean Base update failure",  "description": "The Clean Base software update failed.",                  "action": "Retry the update. Contact iRobot support if the error persists."},
    509: {"label": "Clean Base update failure",  "description": "The Clean Base software update failed.",                  "action": "Retry the update. Contact iRobot support if the error persists."},
    1010: {"label": "Clear path",              "description": "The robot's path is obstructed.",                          "action": "Clear obstacles from the robot's path and restart."},
}

# Backward-compatible derived view — all existing code that reads ERROR_CODE_LABELS
# continues to work without any changes.
ERROR_CODE_LABELS: Final[dict[int, str]] = {
    k: v["label"] for k, v in ERROR_CATALOGUE.items()
}

# v3.4.1 — localised ERROR_CATALOGUE lookup. ERROR_CATALOGUE itself stays
# English-only (used directly by any pre-existing caller that hasn't been
# updated); this is the new, opt-in entry point for localised text.
# Deliberately NOT a method on an entity/hass-bound class so it can be unit
# tested without a running HA instance — callers pass the language string
# they already have (typically hass.config.language).
def get_localized_error_entry(code: int, language: str | None) -> dict[str, str]:
    """Return the label/description/action for an error code, localised.

    Falls back field-by-field to English — a language with an incomplete
    or missing translation for a given code never produces a blank string,
    it silently reverts to the English catalogue entry for that field.

    An error code not present in ERROR_CATALOGUE at all always returns an
    empty dict, regardless of language — callers rely on dict.get(key,
    fallback) patterns (e.g. repairs.py's `label = entry.get("label", f"Error
    {code}")`), which only trigger the fallback when the *key* is absent,
    not when its value is an empty string. Returning {"label": "", ...} for
    an unknown code would silently defeat that fallback and show a blank
    label instead — a v3.4.1 bug caught by test_const.py before release.
    """
    base = ERROR_CATALOGUE.get(code, {})
    if not base:
        return {}
    if not language or language == "en":
        return base
    # Import here (not at module top) to avoid a hard dependency from
    # const.py — a small, low-traffic module — on the much larger
    # translation data file for every consumer of const.py, most of which
    # never touch error codes at all.
    from .error_translations import ERROR_CATALOGUE_TRANSLATIONS
    localised = ERROR_CATALOGUE_TRANSLATIONS.get(language, {}).get(code, {})
    return {
        "label": localised.get("label", base.get("label", "")),
        "description": localised.get("description", base.get("description", "")),
        "action": localised.get("action", base.get("action", "")),
    }


PHASE_LABELS: Final[dict[str, str]] = {
    "new": "New mission",
    "resume": "Resumed",
    "recharge": "Recharging",
    "completed": "Mission completed",
    "cancelled": "Cancelled",
    "pause": "Paused",
    "chargingerror": "Base unplugged",
    "charge": "Charging",
    "run": "Running",
    "evac": "Emptying bin",
    "stop": "Stopped",
    "stuck": "Stuck",
    "hmUsrDock": "Sent home",
    "hmMidMsn": "Docking mid-mission",
    "hmPostMsn": "Docking — end of mission",
    "idle": "Idle",
}

CYCLE_LABELS: Final[dict[str, str]] = {
    "clean": "Clean",
    "quick": "Clean (quick)",
    "spot": "Spot",
    "evac": "Emptying",
    "dock": "Docking",
    "train": "Training",
    "none": "Ready",
}

NOT_READY_LABELS: Final[dict[int, str]] = {
    -1: "Unknown",
    0: "Ready",
    2: "Uneven ground",
    15: "Low battery",
    16: "Bumped unexpectedly",
    31: "Fill tank",
    34: "Not ready",
    39: "Pending",
    48: "Path blocked",
    68: "Updating map",
}

BIN_LABELS: Final[dict[bool, str]] = {True: "Full", False: "Not full"}

YES_NO_LABELS: Final[dict[bool, str]] = {True: "Yes", False: "No"}

CLEAN_BASE_LABELS: Final[dict[int, str]] = {
    -2: "Not available",
    -1: "Unknown",
    300: "Ready",
    301: "Ready",
    302: "Empty",
    303: "Empty",
    350: "Bag missing",
    351: "Clogged",
    352: "Sealing problem",
    353: "Bag full",
    360: "IR comms problem",
    364: "Bin full — sensors not cleared",
}

JOB_INITIATOR_LABELS: Final[dict[str, str]] = {
    "schedule": "iRobot schedule",
    "rmtApp": "iRobot app",
    "manual": "Robot",
    "localApp": "Home Assistant",
    "demand": "Demand clean",  # v3.4.3 — found while building demand_clean_alert
    # blueprint: DirtThresholdManager/callbacks.py's MS1 override writes
    # initiator="demand" (confirmed real, not hypothetical — see both
    # sites), but this dict had no mapping for it, silently falling
    # through to the "none" default below. A demand-triggered mission was
    # therefore indistinguishable from "no initiator info at all" on this
    # sensor — same value, "None", for two different real situations.
    "none": "None",
}

MOP_RANK_LABELS: Final[dict[int, str]] = {
    15: "No mop",
    25: "Extended",
    67: "Standard",
    85: "Deep",
}

PAD_LABELS: Final[dict[str, str]] = {
    "reusableDry": "Dry (reusable)",
    "reusableWet": "Wet (reusable)",
    "dispDry": "Dry (disposable)",
    "dispWet": "Wet (disposable)",
    "invalid": "No pad",
}

CARPET_BOOST_LABELS: Final[dict[str, str]] = {
    "auto": "Auto",
    "performance": "Performance",
    "eco": "Eco",
    "n-a": "Not available",
}

CLEAN_MODE_LABELS: Final[dict[str, str]] = {
    "auto": "Auto",
    "one": "One pass",
    "two": "Two passes",
    "n-a": "Not available",
}

# ── Attributes ────────────────────────────────────────────────────────────────
ATTR_STATUS: Final = "status"
ATTR_CLEANING_TIME: Final = "cleaning_time"
ATTR_CLEANED_AREA: Final = "cleaned_area"
ATTR_ERROR: Final = "error"
ATTR_ERROR_CODE: Final = "error_code"
ATTR_POSITION: Final = "position"
ATTR_SOFTWARE_VERSION: Final = "software_version"
ATTR_BIN_FULL: Final = "bin_full"
ATTR_BIN_PRESENT: Final = "bin_present"

# Braava / mop attributes
ATTR_DETECTED_PAD: Final = "detected_pad"
ATTR_LID_CLOSED: Final = "lid_closed"
ATTR_TANK_PRESENT: Final = "tank_present"
ATTR_TANK_LEVEL: Final = "tank_level"
ATTR_PAD_WETNESS: Final = "spray_amount"

# Fan speed labels for carpet-boost models
# v3.1.0 CARPET-BOOST-SLUG-FIX — lowercase slugs required by HA's select
# translation_key convention (hassfest enforces [a-z0-9-_]+ on state keys).
# Previously "Automatic"/"Eco"/"Performance" (Capital-Case) were both the
# select's option/state value AND directly displayed — that's how the
# select worked before translation_key was added, but it fails hassfest
# validation. select.py's _select_carpet_boost() accepts the old Capital-Case
# values too (case-insensitive match) so existing automations using
# select.select_option with "Automatic" keep working — only the *displayed*
# state value and the slugs in strings.json/translations changed.
FAN_SPEED_AUTOMATIC: Final = "automatic"
FAN_SPEED_ECO: Final = "eco"
FAN_SPEED_PERFORMANCE: Final = "performance"
FAN_SPEEDS: Final[list[str]] = [FAN_SPEED_AUTOMATIC, FAN_SPEED_ECO, FAN_SPEED_PERFORMANCE]

# Braava mop overlap constants
OVERLAP_STANDARD: Final = 67
OVERLAP_DEEP: Final = 85
OVERLAP_EXTENDED: Final = 25
MOP_STANDARD: Final = "Standard"
MOP_DEEP: Final = "Deep"
MOP_EXTENDED: Final = "Extended"
BRAAVA_MOP_BEHAVIORS: Final[list[str]] = [MOP_STANDARD, MOP_DEEP, MOP_EXTENDED]
BRAAVA_SPRAY_AMOUNT: Final[list[int]] = [1, 2, 3]

# ── Diagnostics ───────────────────────────────────────────────────────────────
DIAG_REDACT_KEYS: Final[set[str]] = {
    CONF_BLID,
    "password",
    "blid",
    "irobot_password",
    "irobot_username",
}

# ── Capability detection ───────────────────────────────────────────────────────
def has_carpet_boost(state: dict) -> bool:
    """Return True if this robot supports carpet boost / fan speed control."""
    cap = state.get("cap") or {}
    if cap.get("carpetBoost") == 1:
        return True
    return (
        "carpetBoost" in state
        and "vacHigh" in state
        and cap.get("carpetBoost") is None
    )


def has_pose(state: dict) -> bool:
    """Return True if this robot reports pose (position) data."""
    return (state.get("cap") or {}).get("pose", 0) >= 1


def has_smart_map(state: dict) -> bool:
    """Return True if this robot has persistent smart maps (pmaps)."""
    return bool(state.get("pmaps"))


def is_mop(state: dict) -> bool:
    """Return True if this device is a Braava mop (detectedPad present)."""
    return "detectedPad" in state


def has_clean_base(state: dict) -> bool:
    """Return True if a Clean Base dock is present and communicating."""
    dock = state.get("dock") or {}
    return "fwVer" in dock or isinstance(dock.get("state"), int)


def active_charge_cycles(bbchg3: dict) -> int | None:
    """v2.9.0 DAILY-DIGEST — chemistry-aware lifetime charge-cycle count.

    Extracted from sensor.py's _total_energy_consumed_kwh()/
    _estimated_battery_eol() duplication (third call site — callbacks.py
    needed the same logic at mission end — made this the moment to share
    one implementation instead of adding a third copy).

    Same heuristic as sensor.py: nNimhChrg > 0 means a NiMH aftermarket
    battery is currently installed (even if nLithChrg > 0 from an earlier
    OEM Li-ion period) — NiMH wins when present. Falls back to nLithChrg,
    then nAvail for older firmware that predates the chemistry split.
    Returns None when bbchg3 has none of these fields at all.
    """
    nimh_cycles = bbchg3.get("nNimhChrg") or 0
    if nimh_cycles > 0:
        return int(nimh_cycles)
    cycles = bbchg3.get("nLithChrg") or bbchg3.get("nAvail")
    return int(cycles) if cycles else None


def estcap_to_mah(
    raw_estcap: float | int | None,
    estcap_scale_liion: float,
    estcap_scale_nimh: float,
    nimh_cycles: int | None,
) -> float | None:
    """v3.1.0 L9-BATTERY — chemistry-aware raw estCap → mAh conversion.

    Extracted from sensor.py's _estcap_to_mah() (which is bound to an
    IRobotEntity and reads robot_profile off it) so callbacks.py can record
    estCap observations into RobotProfileStore's noise-floor baseline at
    mission end without needing the full entity wrapper — same
    extract-on-third-call-site reasoning as active_charge_cycles() above.

    For i/s/j/e/6 series (both scales == 1.0): raw estCap == mAh directly.
    For 9-series old firmware: raw estCap is BMS-scaled.
      Li-ion (scale != 1.0, no NiMH cycles recorded): raw ÷ estcap_scale_liion
      NiMH   (nimh_cycles > 0): raw ÷ estcap_scale_nimh
    Returns None when raw_estcap is absent or zero.
    """
    if not raw_estcap:
        return None
    if estcap_scale_liion == 1.0 and estcap_scale_nimh == 1.0:
        return float(raw_estcap)
    scale = estcap_scale_nimh if (nimh_cycles or 0) > 0 else estcap_scale_liion
    return round(float(raw_estcap) / scale)

# ── F7g — Region type icons ───────────────────────────────────────────────────
# Single source of truth for MDI icon names per iRobot region_type string.
# Used by CloudSmartZoneSelect.icon and by the companion Lovelace card
# (exposed via the region_icons extra_state_attribute).

REGION_TYPE_ICONS: Final[dict[str, str]] = {
    "bathroom":          "mdi:shower",
    "bedroom":           "mdi:bed-king",
    "breakfast_room":    "mdi:silverware-fork-knife",
    "closet":            "mdi:hanger",
    "den":               "mdi:sofa-single",
    "dining_room":       "mdi:silverware-fork-knife",
    "entryway":          "mdi:door-open",
    "family_room":       "mdi:sofa-single",
    "foyer":             "mdi:door-open",
    "garage":            "mdi:garage",
    "guest_bathroom":    "mdi:shower",
    "guest_bedroom":     "mdi:bed-king",
    "hallway":           "mdi:shoe-print",
    "kitchen":           "mdi:fridge",
    "kids_room":         "mdi:teddy-bear",
    "laundry_room":      "mdi:washing-machine",
    "living_room":       "mdi:sofa",
    "lounge":            "mdi:sofa",
    "media_room":        "mdi:television",
    "mud_room":          "mdi:landslide",
    "office":            "mdi:chair-rolling",
    "pantry":            "mdi:archive",
    "playroom":          "mdi:teddy-bear",
    "primary_bathroom":  "mdi:shower",
    "primary_bedroom":   "mdi:bed-king",
    "recreation_room":   "mdi:sofa",
    "storage_room":      "mdi:archive",
    "study":             "mdi:bookshelf",
    "sun_room":          "mdi:sun-angle",
    "workshop":          "mdi:toolbox",
    "outside":             "mdi:asterisk",
    "basement":            "mdi:home-floor-b",
    "unfinished_basement": "mdi:home-floor-b",
    "default":             "mdi:map-marker",
    "custom":              "mdi:map-marker",
}

ZONE_TYPE_ICONS: Final[dict[str, str]] = {
    "default":   "mdi:map-marker",
    "furniture": "mdi:sofa-single",
}


# ── Region ID extraction ───────────────────────────────────────────────────────

def extract_region_id(item: object) -> str:
    """Extract a region ID from an MQTT region entry or plan.upcoming item.

    Handles two confirmed formats from both local MQTT and the iRobot app:
    - String (some firmware):   "23"
    - Object sent by Roomba+:   {"region_id": "23", "type": "rid"}
    - Object sent by iRobot app: {"rid": "23", "type": "rid"}

    Returns an empty string when neither format is recognisable.
    """
    if isinstance(item, dict):
        return str(item.get("rid") or item.get("region_id") or "")
    return str(item) if item is not None else ""


# v3.3.0 ROOM-SCHED — per-room cleaning frequency configuration.
# Options-flow keys (ASCII, locale-independent) → uniform interval in days.
# "three_per_week" is deliberately a uniform 7/3 interval, NOT weekday
# logic — real weekday schedules exist natively in iRobot cleanSchedule2.
CONF_ROOM_SCHEDULE = "room_schedule"
CONF_CORRELATION_ENTITIES = "correlation_entities"  # v3.3.0 CROSS-CORR (opt-in)
ROOM_SCHEDULE_LEARNED = "learned"
ROOM_SCHEDULE_INTERVALS: dict[str, float] = {
    "daily": 1.0,
    "every_2_days": 2.0,
    "three_per_week": 7.0 / 3.0,
    "weekly": 7.0,
}
