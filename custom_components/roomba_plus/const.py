"""Constants for the Roomba+ integration."""
from __future__ import annotations

from typing import Final

from homeassistant.components.vacuum import VacuumActivity
from homeassistant.const import Platform

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
]

# Cloud and map platforms are added dynamically in __init__.py
CLOUD_PLATFORMS: Final[list[Platform]] = []  # Phase 3

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

# ── Roomba 980 hardware constants ─────────────────────────────────────────────
ROOMBA_CLEAN_WIDTH_MM: Final = 320  # 980 AeroForce cleaning path width

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

# Human-readable phase labels (from rest980 — extended)

ERROR_CODE_LABELS: Final[dict[int, str]] = {
    0: "None",
    1: "Left wheel off floor",
    2: "Main brushes stuck",
    3: "Right wheel off floor",
    4: "Left wheel stuck",
    5: "Right wheel stuck",
    6: "Stuck near a cliff",
    7: "Left wheel error",
    8: "Bin error",
    9: "Bumper stuck",
    10: "Right wheel error",
    11: "Bin error",
    12: "Cliff sensor issue",
    13: "Both wheels off floor",
    14: "Bin missing",
    15: "Reboot required",
    16: "Bumped unexpectedly",
    17: "Path blocked",
    18: "Docking issue",
    19: "Undocking issue",
    20: "Docking issue",
    21: "Navigation problem",
    22: "Navigation problem",
    23: "Battery issue",
    24: "Navigation problem",
    25: "Reboot required",
    26: "Vacuum problem",
    27: "Vacuum problem",
    29: "Software update needed",
    30: "Vacuum problem",
    31: "Reboot required",
    32: "Smart map problem",
    33: "Path blocked",
    34: "Reboot required",
    35: "Unrecognised cleaning pad",
    36: "Bin full",
    37: "Tank needs refilling",
    38: "Vacuum problem",
    39: "Reboot required",
    40: "Navigation problem",
    41: "Timed out",
    42: "Localisation problem",
    43: "Navigation problem",
    44: "Pump issue",
    45: "Lid open",
    46: "Low battery",
    47: "Reboot required",
    48: "Path blocked",
    52: "Pad requires attention",
    53: "Software update required",
    65: "Hardware problem detected",
    66: "Low memory",
    68: "Updating map",
    73: "Pad type changed",
    74: "Max area reached",
    75: "Navigation problem",
    76: "Hardware problem detected",
    88: "Back-up refused",
    89: "Mission runtime too long",
    101: "Battery not connected",
    102: "Charging error",
    103: "Charging error",
    104: "No charge current",
    105: "Charging current too low",
    106: "Battery too warm",
    107: "Battery temperature incorrect",
    108: "Battery communication failure",
    109: "Battery error",
    110: "Battery cell imbalance",
    111: "Battery communication failure",
    112: "Invalid charging load",
    114: "Internal battery failure",
    115: "Cell failure during charging",
    116: "Charging error of home base",
    118: "Battery communication failure",
    119: "Charging timeout",
    120: "Battery not initialised",
    122: "Charging system error",
    123: "Battery not initialised",
    216: "Charging base bag full",
    1010: "Clear path",
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
FAN_SPEED_AUTOMATIC: Final = "Automatic"
FAN_SPEED_ECO: Final = "Eco"
FAN_SPEED_PERFORMANCE: Final = "Performance"
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
# Used by vacuum.py, sensor.py, __init__.py, map_renderer.py, zone_store.py
#
# Carpet boost detection:
#   - i/s/j series: cap.carpetBoost == 1
#   - 900 series (980/985): cap.carpetBoost absent, but "carpetBoost" + "vacHigh"
#     both present as top-level state keys.
# → Use has_carpet_boost(state) everywhere instead of raw cap check.

def has_carpet_boost(state: dict) -> bool:
    """Return True if this robot supports carpet boost / fan speed control."""
    cap = state.get("cap", {})
    # i/s/j series: explicit cap flag
    if cap.get("carpetBoost") == 1:
        return True
    # 900 series: top-level keys present but NOT in cap{}
    return (
        "carpetBoost" in state
        and "vacHigh" in state
        and cap.get("carpetBoost") is None
    )


def has_pose(state: dict) -> bool:
    """Return True if this robot reports pose (position) data."""
    return state.get("cap", {}).get("pose") == 1


def has_smart_map(state: dict) -> bool:
    """Return True if this robot has persistent smart maps (pmaps)."""
    return bool(state.get("pmaps"))


def is_mop(state: dict) -> bool:
    """Return True if this device is a Braava mop (detectedPad present)."""
    return "detectedPad" in state


def has_clean_base(state: dict) -> bool:
    """Return True if a Clean Base dock is present and communicating."""
    dock = state.get("dock", {})
    return "fwVer" in dock or isinstance(dock.get("state"), int)
