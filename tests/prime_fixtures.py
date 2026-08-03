"""Realistic Prime test data, built through roombapy-prime's own models.

WHY THIS MODULE EXISTS.

Every Prime test file used to build its own fakes by hand. Three of them
contained no `roombapy_prime` import at all, and one of those --
test_prime_schedule_switch.py -- declared a `_Schedule` dataclass with
`schedule_id` and `options` as ATTRIBUTES. The library returns dicts
there. The tests passed against a shape no server has ever sent, while
the feature created zero entities for every real user.

A hand-built fake records what someone believed the shape was. It cannot
notice when that belief is wrong, and it makes the question "is this
covered?" answer yes.

So: the JSON below is the server's side of the wire, and everything else
is derived from it by calling the same parsers the integration calls. If
a library shape changes, tests using this fail -- which is the point.

MagicMock is specifically what this replaces. `getattr(m, "anything")`
on a MagicMock returns a truthy MagicMock, so every dict-vs-attribute
mistake reads as working.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

# ── the server's side of the wire ────────────────────────────────────────

CURRENT_STATE: dict[str, Any] = {
    "batPct": 84,
    # tankLvl present: only some docks report it (fwVer 24 / pd 3 does,
    # fwVer 20 / pd 2 does not), and the tank sensor gates on presence.
    # gwTankLvl is deliberately absent -- it appears in no real capture
    # and is not modelled.
    "dock": {"known": True, "state": 301, "pwState": 601, "pdState": 701,
             "tankLvl": 90, "cap": {"evac": 1, "pd": 3, "pw": 1, "pwo": 1}},
    "cleanMissionStatus": {"phase": "charge", "cycle": "none", "mssnM": 0,
                           "expireM": 0},
    "bin": {"present": True, "full": False},
    "tankPresent": True,
    "tankLvl": 80,
    "padWetness": {"disposable": 2, "reusable": 2},
    "detectedPad": "reusableWet",
    "vacHigh": False,
    "noAutoPasses": False,
}

CONFIG_INFO: dict[str, Any] = {
    "sku": "N185240",
    "name": "Test robot",
    "batteryType": "lith",
    "hardwareRev": 2,
    "navSwVer": "22.29.3",
    "wifiSwVer": "2.4.16-59",
    "cap": {"binFullDetect": 1, "carpetBoost": 1, "pose": 2, "maps": 3,
            "eco": 1, "multiPass": 2, "tLine": 1, "area": 1, "mopLift": 1},
    "dockCap": {"evac": 1, "dryTime": 1, "padWash": 1},
}

SHADOWS: dict[str, Any] = {
    "ro-currentstate": CURRENT_STATE,
    "ro-configinfo": CONFIG_INFO,
    "ro-stats": {"nMssn": 120, "sqft": 4200},
    "ro-services": {},
    "classic": {"state": {"reported": {
        "pmaps": [{"pmap0": "AAA"}],
        "cap": {"pose": 2, "carpetBoost": 1, "maps": 3, "binFullDetect": 1},
        "batPct": 84,
        "cleanMissionStatus": {"phase": "charge"},
    }}},
}

SCHEDULES_JSON: dict[str, Any] = {"household_schedules": [{
    "household_schedule_id": "HS-1",
    "schedules": [
        {"schedule_id": "S-1", "options": {
            "enabled": True, "deleted": False, "name": "Weekdays",
            "frequency": "WEEKLY", "start": {"day": [1], "hour": 9, "min": 0}}},
        {"schedule_id": "S-2", "options": {
            "enabled": False, "deleted": False, "name": "Saturday",
            "frequency": "WEEKLY", "start": {"day": [6], "hour": 15, "min": 45}}},
    ],
}]}

FAVORITES_JSON: list[dict[str, Any]] = [
    {"favorite_id": "F-1", "name": "Kitchen only", "color": "#ff0000",
     "icon": "star", "order": 0, "command_defs": [{"command": "start"}]},
    {"favorite_id": "F-2", "name": "Whole home", "color": "#00ff00",
     "icon": "home", "order": 1, "command_defs": [{"command": "start"}]},
]

MAP_VERSIONS: list[dict[str, Any]] = [{
    "p2map_id": "MAP-1", "name": "Ground floor",
    "active_p2mapv_id": "MAPV-1", "user_orientation_rad": 0.0,
}]

PARTS: dict[str, Any] = {"parts": [
    {"part_id": "filter", "name": "Filter", "life_remaining": 61,
     "unit": "percent"},
    {"part_id": "side_brush", "name": "Side brush", "life_remaining": 40,
     "unit": "percent"},
]}


# ── parsed through the library, never by hand ────────────────────────────

def schedules_response():
    """What PrimeRobot.get_schedules() returns for SCHEDULES_JSON."""
    from roombapy_prime.models.schedules_dnd import SchedulesResponse

    return SchedulesResponse.from_json(SCHEDULES_JSON)


def favorites():
    """What PrimeRobot.get_favorites() returns for FAVORITES_JSON."""
    from roombapy_prime.models import FavoriteV1

    return [
        FavoriteV1(**{k: v for k, v in raw.items()
                      if k in FavoriteV1.__dataclass_fields__})
        for raw in FAVORITES_JSON
    ]


def favorites_attribute() -> list[dict[str, str]]:
    """What button_prime.async_favorites_attribute() puts in
    runtime_data.prime_favorites -- NOT the raw server JSON. The two
    differ (`id` vs `favorite_id`), and using the wrong one raises
    KeyError deep inside button setup."""
    return [{"id": raw["favorite_id"], "name": raw["name"]}
            for raw in FAVORITES_JSON]


def schedule_containers():
    """What PrimeScheduleCoordinator.data holds: (container_id, [parsed]).

    Parsed HouseholdSchedule objects, not the raw dicts the library
    returns from SchedulesList.schedules -- reading attributes off those
    dicts is the bug that left the schedule switches with zero entities
    for their entire life.
    """
    from roombapy_prime.models.schedules_dnd import HouseholdSchedule

    return [
        (container.household_schedule_id,
         [HouseholdSchedule.from_json(raw) for raw in container.schedules])
        for container in schedules_response().household_schedules
    ]


def prime_robot() -> AsyncMock:
    """A robot double whose methods return what the library returns."""
    robot = AsyncMock()
    robot.blid = "TESTBLID"
    robot.robot_id = "TESTBLID"
    robot.get_schedules.return_value = schedules_response()
    robot.get_schedules_raw.return_value = SCHEDULES_JSON
    robot.get_favorites.return_value = favorites()
    robot.get_active_map_versions.return_value = MAP_VERSIONS
    robot.get_household_id.return_value = "HH-1"
    return robot


def cloud_only_config_entry() -> MagicMock:
    """A CLOUD_ONLY config entry carrying realistic data throughout.

    The config entry itself stays a MagicMock -- it is Home Assistant's
    object, not this project's, and nothing here branches on a shape it
    could get wrong. Everything that crossed the roombapy-prime boundary
    does not.
    """
    from custom_components.roomba_plus.models import ConnectionType

    config_entry = MagicMock()
    data = config_entry.runtime_data
    data.connection_type = ConnectionType.CLOUD_ONLY
    data.blid = "TESTBLID"
    data.roomba = None
    data.prime_robot = prime_robot()
    data.prime_household_id = "HH-1"
    data.prime_favorites = favorites_attribute()

    data.prime_coordinator = MagicMock()
    data.prime_coordinator.data = dict(SHADOWS)
    data.prime_status_coordinator = MagicMock()
    data.prime_status_coordinator.data = dict(SHADOWS)
    data.prime_parts_coordinator = MagicMock()
    data.prime_parts_coordinator.data = dict(PARTS)

    # The schedule switches read their state from here and, since the
    # entity list follows the schedule list, are also BUILT from it.
    data.prime_schedule_coordinator = MagicMock()
    data.prime_schedule_coordinator.data = schedule_containers()
    # Room names live on the coordinator because the switch platform
    # builds its entities inside a listener and cannot await a cloud
    # call. The calendar reads the same set instead of fetching its own.
    data.prime_schedule_coordinator.room_names = {
        "13": "Kitchen", "10": "Bathroom", "12": "Hallway",
    }
    # iRobot's numbering: 0 = Sunday. Resolved from strings.json at
    # runtime so schedule labels are not hard-coded English.
    data.prime_schedule_coordinator.weekday_names = {
        0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat",
    }
    data.prime_schedule_coordinator.async_config_entry_first_refresh = AsyncMock()
    data.prime_schedule_coordinator.async_add_listener = MagicMock(
        return_value=lambda: None
    )
    return config_entry
