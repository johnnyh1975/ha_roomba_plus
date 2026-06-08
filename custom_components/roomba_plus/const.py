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

# ── v2.2.0 — new service ──────────────────────────────────────────────────────
SERVICE_CLEAN_SEQUENCE: Final = "clean_sequence"   # F10d — start robot B when robot A finishes

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

# v2.3.0 — Phases used by image.py (pose handling) and vacuum.py (live CR4 source).
# Moved from image.py module-locals so vacuum.py can import without circular deps.
CLEANING_PHASES: Final[frozenset[str]] = frozenset({"run", "hmMidMsn"})
MISSION_END_PHASES: Final[frozenset[str]] = frozenset({"charge", "hmPostMsn", "stop", "evac"})

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
    88:  {"label": "Back-up refused",           "description": "The robot could not back up as required.",                 "action": "Check for obstacles behind the robot and clear the area."},
    89:  {"label": "Mission runtime too long",  "description": "The mission exceeded the maximum allowed runtime.",        "action": "The robot will dock and resume after charging."},
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
    216: {"label": "Charging base bag full",    "description": "The Clean Base bag is full and needs replacing.",         "action": "Replace the Clean Base bag."},
    224: {"label": "Smart Map localization failed", "description": "The robot could not localise on its Smart Map.",      "action": "Place the robot in an open area on the map and try again. Retrain the map if needed."},
    1010: {"label": "Clear path",              "description": "The robot's path is obstructed.",                          "action": "Clear obstacles from the robot's path and restart."},
}

# Backward-compatible derived view — all existing code that reads ERROR_CODE_LABELS
# continues to work without any changes.
ERROR_CODE_LABELS: Final[dict[int, str]] = {
    k: v["label"] for k, v in ERROR_CATALOGUE.items()
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
def has_carpet_boost(state: dict) -> bool:
    """Return True if this robot supports carpet boost / fan speed control."""
    cap = state.get("cap", {})
    if cap.get("carpetBoost") == 1:
        return True
    return (
        "carpetBoost" in state
        and "vacHigh" in state
        and cap.get("carpetBoost") is None
    )


def has_pose(state: dict) -> bool:
    """Return True if this robot reports pose (position) data."""
    return state.get("cap", {}).get("pose", 0) >= 1


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
