"""Diagnostics support for Roomba+.

Provides structured debug output for bug reports without leaking credentials.
Accessible via Settings → Devices & Services → Roomba+ → Download diagnostics.
"""
from __future__ import annotations

import re

import time as _time_mod

import dataclasses
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .structural_failures import diagnostic_info
from .const import (
    CONF_PRIME_FAVORITE_BUTTONS,
    DEFAULT_PRIME_FAVORITE_BUTTONS,
)
from .withheld_features import withheld_features
from .const import DIAG_REDACT_KEYS, DOMAIN, ERROR_CODE_LABELS
from .models import ConnectionType, RoombaConfigEntry
from .binary_sensor import _prime_reports_tank

_CLOUD_REDACT = DIAG_REDACT_KEYS | {"irobot_username", "irobot_password"}


def _cloud_diag(data: Any) -> dict[str, Any]:
    """Return cloud coordinator diagnostics (no credentials)."""
    cc = data.cloud_coordinator
    if cc is None:
        return {"enabled": False}
    result: dict[str, Any] = {
        "enabled": True,
        # WHAT THIS DOES AND DOES NOT MEAN. This is a PUSH
        # coordinator (update_interval=None), so it is never
        # polled: the flag records that the last time data was
        # set it went well, and then stays put. A robot that has
        # sent nothing for hours still reports True here, and
        # correctly so. `push_freshness` above answers the other
        # question -- how long since anything arrived -- and it
        # is the one to read when a robot looks healthy but is
        # not responding.
        "last_update_success": cc.last_update_success,
        "last_exception": str(cc.last_exception) if cc.last_exception else None,
    }
    if cc.data:
        result["pmap_count_total"] = len(cc.data.get("pmaps", []))   # all pmaps from API
        result["favorite_count"] = len(cc.data.get("favorites", []))
        result["active_pmap_id"] = cc.active_pmap_id
        result["region_count_active"] = len(cc.regions)   # active pmap only (post-filter)
        result["zone_count_active"] = len(cc.zones)       # active pmap only (post-filter)

        # WHO OWNS EACH MAP. A cloud pmap entry carries `robot_ids` and
        # `shared`, and this download dropped both -- so when a
        # multi-robot household reports something that looks like one
        # robot showing another's rooms, the file cannot answer whether
        # a map is genuinely shared between them.
        #
        # @ScenicSystemsLLC hit exactly that: asked to check the field,
        # he found it absent and had to compare `pmap_id` lists by hand
        # instead. That was a gap here, not something he missed.
        result["pmap_ownership"] = [
            {
                "pmap_id": pm.get("pmap_id"),
                "robot_ids": pm.get("robot_ids"),
                "shared": pm.get("shared"),
                "merged_pmap_ids": pm.get("merged_pmap_ids"),
            }
            for pm in (cc.data.get("pmaps") or [])
            if isinstance(pm, dict)
        ]
    return result


def _parts_report(data: Any) -> dict[str, Any]:
    """Consumable parts as the server reported them.

    Included because the part SET differs by model and is discovered
    rather than known in advance -- so a robot missing a sensor someone
    expected is answered here, by showing exactly which parts its own
    cloud record contains.

    part_id and counts only: nothing here identifies a household."""
    coordinator = getattr(data, "prime_parts_coordinator", None)
    if coordinator is None:
        return {"started": False}
    parts = coordinator.data or {}
    return {
        "started": True,
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "parts": {
            part_id: {
                "count_remaining": getattr(part, "count_remaining", None),
                "count_type": getattr(part, "count_type", None),
                "count_used": getattr(part, "count_used", None),
                "category": getattr(part, "counter_category", None),
            }
            for part_id, part in parts.items()
        },
    }


def _prime_token_expiry(data: Any) -> dict[str, Any]:
    """Does this account's login carry a usable expiry?

    ANSWERED 30 July 2026 (jayjay13011): yes, and the token lasts about
    an hour. Two downloads twenty minutes apart reported 3217 and 1998
    seconds remaining.

    That was worth confirming rather than assuming: PrimeFactory is
    already called with auto_refresh=True, which refreshes proactively
    shortly before expiry AND reactively on an HTTP 403. Until this
    capture nobody had established that there was anything to schedule
    against -- the mechanism was in place and its input unverified.

    The "no expiry" branch below still matters: not every account's
    login response is guaranteed to carry the field, and a robot whose
    token has no stated lifetime falls back to blind periodic renewal
    inside the library.

    Deliberately reports lifetime and remaining seconds, never the
    token itself or anything derived from it.
    """
    robot = data.prime_robot
    token = getattr(getattr(robot, "_mqtt", None), "_token", None)
    if token is None:
        return {"known": False, "note": "no MQTT token available to inspect"}
    expires = getattr(token, "expires", None)
    if expires is None:
        return {
            "known": False,
            "note": (
                "login response carries no 'expires' field -- proactive token "
                "refresh cannot be scheduled on this account, which is a real "
                "limitation rather than a bug"
            ),
        }
    remaining = getattr(token, "seconds_until_expiry", lambda: None)()
    return {
        "known": True,
        "seconds_remaining": None if remaining is None else round(remaining),
        "note": "proactive refresh is schedulable against this",
    }


#: Shadow keys withheld from the dump.
#:
#: Not credentials -- those never reach a shadow -- but identifiers that
#: tie a capture to a household or a device, and would follow the file
#: into a public issue.
#:
#: `mac` and `blid` in particular: a diagnostics file gets pasted into
#: GitHub, and a MAC address is not something a tester intends to
#: publish. The BLID appears elsewhere in this file already, but adding
#: more copies is not a reason to add more.
_SHADOW_REDACT: Final[set[str]] = {
    "blid", "mac", "wifi", "ssid", "bssid", "sn", "serial",
    "navSerialNo", "hwPartsRev", "softwareVer", "uuid", "userId",
    "householdId", "household_id", "cloudEnv", "svcEndpoints",
    # CREDENTIALS. Reported by @jouwdan, who found `passwordHash` in his
    # own export and redacted it by hand before attaching it to a public
    # issue.
    #
    # That is exactly the failure this set exists to prevent, and it got
    # through because the set was assembled from fields somebody
    # happened to notice -- never from asking what a CATEGORY of secret
    # looks like.
    #
    # So: anything hash-, token-, key- or password-shaped, whether or
    # not it has been seen in a capture. A field nobody has observed is
    # precisely the one nobody will check before pasting a file into an
    # issue.
    "passwordHash",
    "password",
    "passwd",
    "secret",
    "token",
    "accessToken",
    "refreshToken",
    "idToken",
    "apiKey",
    "privateKey",
    "certificate",
}


#: A MAC address in any of the usual separators.
#:
#: Matched on VALUES, not on key names. Reported by @chairstacker: the
#: dump contained several unredacted MAC addresses, because they sat
#: under keys this code had never seen -- redacting a key called `mac`
#: does nothing for one called `wlan0HwAddr`.
_MAC_PATTERN = re.compile(r"\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b")


#: Substrings that make a key a secret regardless of its exact name.
#:
#: The named set above lists fields somebody noticed. This catches the
#: ones nobody has -- `wifiPasswd`, `authToken`, `deviceSecret` and
#: whatever the next firmware invents.
#:
#: Matched case-insensitively against the whole key, so `passwordHash`
#: and `hashedPassword` both go.
_SECRET_SUBSTRINGS: Final[tuple[str, ...]] = (
    "password", "passwd", "secret", "token", "apikey", "privatekey",
    "credential", "passphrase",
)


def _is_secret_key(key: str) -> bool:
    """Whether a key names something that must never leave the machine.

    DELIBERATELY BROAD. A false positive redacts a harmless field and
    costs a question in an issue thread; a false negative puts a
    credential in a file somebody attaches publicly.

    `hash` is NOT in the substring list on its own -- it would catch
    `hashedMapId` and similar. `passwordHash` is covered by "password".
    """
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_SUBSTRINGS)


def _redact_values(value: Any, blid: str | None) -> Any:
    """Redacts identifying CONTENT, wherever it appears.

    Key-based redaction is not enough, and a tester found both gaps
    within a day of the dump shipping:

      - `blid` was redacted, but `p2map_id` is literally
        "<BLID>-<epoch>" and was not. So was `smart_map_id`, and
        `robot_id` inside REST responses.
      - MAC addresses appeared under keys this code did not know.

    A diagnostics file gets pasted into a public issue. Redacting the
    field that happens to be called `blid` while leaving five copies of
    the same value in other fields is worse than not claiming to redact
    at all, because it invites trust the output does not earn.

    So: substring for the blid, pattern for MAC addresses, applied to
    every string at every depth.
    """
    if isinstance(value, str):
        if blid and blid in value:
            value = value.replace(blid, "**REDACTED_BLID**")
        return _MAC_PATTERN.sub("**REDACTED_MAC**", value)
    if isinstance(value, dict):
        return {k: _redact_values(v, blid) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_values(v, blid) for v in value]
    return value


def _prime_room_preferences(
    config_entry: RoombaConfigEntry, hass: HomeAssistant
) -> dict[str, Any]:
    """The Rooms Map's `room_preferences`, or why there are none.

    Read from the entity rather than recomputed, so a download reports
    what a consumer would actually see. A recomputation could succeed
    where the entity fails and hide the gap this exists to find.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    # `hass` IS A PARAMETER, NOT AN ATTRIBUTE OF THE ENTRY.
    #
    # This read `config_entry.hass`, which `ConfigEntry` does not
    # define -- so every download since this block was written returned
    # `{"note": "no hass on this entry"}` and nothing else.
    #
    # @chairstacker's download is what surfaced it, and it explains why
    # `room_preferences` has sat in the tester notes as "never
    # confirmed" for weeks: nobody could confirm it, because the block
    # meant to show it never ran.
    #
    # `async_get_config_entry_diagnostics` receives hass as its first
    # argument. Passing it down is all this needed.
    data = config_entry.runtime_data
    registry = er.async_get(hass)
    # THE PREFIX WAS MISSING. The image entity sets its unique id from
    # `IRobotEntity.robot_unique_id`, which is `roomba_plus_{blid}` --
    # so looking up `{blid}_rooms_map` never matched, and every
    # diagnostics download said "no rooms map entity" whether one
    # existed or not.
    #
    # Two Prime users reported map problems with that note in their
    # file while their map was on screen and working (@theChef163,
    # @mrsnyds). It sent me looking at map creation twice for nothing.
    unique = f"roomba_plus_{data.blid}_rooms_map"
    entity_id = registry.async_get_entity_id("image", DOMAIN, unique)
    if entity_id is None:
        return {"note": "no rooms map entity"}
    state = hass.states.get(entity_id)
    if state is None:
        return {"note": "rooms map has no state yet"}
    prefs = state.attributes.get("room_preferences")
    if not prefs:
        return {
            "note": (
                "attribute absent or empty -- the robot reported no "
                "operating_mode_defaults, or no room carried a "
                "last_operating_mode to read them under"
            )
        }
    return dict(prefs)


async def _favourites_diagnostics(data: Any, config_entry: Any) -> dict[str, Any]:
    """Favourites as the running robot actually sees them.

    THERE ARE TWO SOURCES AND THIS USED TO REPORT ONLY ONE. Prime
    robots load favourites through `prime_robot.get_favorites()` into
    `runtime_data.prime_favorites`. Classic robots never have a
    `prime_robot`, so that list is permanently empty for them -- their
    buttons come from the cloud coordinator instead (see
    `button.py::_async_setup_favorite_buttons`).

    The old block read the Prime list unconditionally. On @pk-1966's
    i7+ it reported 0 favourites while `cloud.favorite_count` said 6
    in the same download, which reads as a favourites bug and is not
    one: the buttons were fine, the diagnostics were looking in the
    wrong place.

    `source` names which path this entry actually uses, so the two
    numbers can never be compared across tiers again.
    """
    result: dict[str, Any] = {
        "buttons_enabled": config_entry.options.get(
            CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
        ),
    }

    robot = getattr(data, "prime_robot", None)
    if robot is not None:
        result["source"] = "prime_robot.get_favorites()"
        result["count"] = len(getattr(data, "prime_favorites", None) or [])
        # THE COUNT ALONE CANNOT DISTINGUISH the causes that produce an
        # empty list, so the raw response goes in beside it.
        try:
            result["raw"] = await robot.get_favorites_raw()
        except Exception as err:  # noqa: BLE001
            # Never raises: a diagnostics download that fails because
            # one section failed is worse than a section reporting why.
            result["raw"] = {"error": f"{type(err).__name__}: {err}"}
        return result

    coordinator = getattr(data, "cloud_coordinator", None)
    coordinator_data = getattr(coordinator, "data", None)
    if not isinstance(coordinator_data, dict):
        result["source"] = "none -- no prime robot and no cloud coordinator"
        result["count"] = 0
        return result

    # The same read button.py makes, so this number is what the buttons
    # were built from rather than a parallel count that can drift.
    favourites = coordinator_data.get("favorites") or []
    result["source"] = "cloud_coordinator (Classic)"
    result["count_for_account"] = len(favourites)

    # PER ROBOT, NOT PER ACCOUNT -- the endpoint is account-wide, and
    # this entry only builds buttons for its own blid. Reporting the
    # account total alone made a three-robot account look wrong.
    try:
        from .button_prime import _raw_favorite_is_for  # noqa: PLC0415

        blid = getattr(data, "blid", None) or ""
        matched = [f for f in favourites if _raw_favorite_is_for(f, blid)]
        result["count"] = len(matched)
        result["filtered_out_for_other_robots"] = len(favourites) - len(matched)
    except Exception as err:  # noqa: BLE001
        result["count"] = len(favourites)
        result["filter_error"] = f"{type(err).__name__}: {err}"

    return result


async def _prime_schedule_summary(data: Any) -> Any:
    """What get_schedules() returns, reduced to the deciding fields.

    The calendar reads this call, not a shadow. When it shows nothing,
    the question is whether the robot reports a schedule at all -- and
    until now the diagnostics could not say.

    IT STILL COULD NOT SAY, for two reasons, both fixed here:

      - it read `data.household_id`. The field is `prime_household_id`
        (models.py), and this same file reads it correctly one screen
        further down. The wrong name meant an unconditional None: the
        probe never ran, on any install.
      - `SchedulesList.schedules` is `list[dict]`, so `getattr(schedule,
        "options")` returned None for every schedule. Even with the
        household id fixed, every entry would have summarised as
        `{"options": None}` -- a robot with schedules reported as a
        robot whose schedules cannot be read.

    Which is worse than nothing: this probe exists specifically to
    distinguish "the robot has no schedule" from "we cannot read it",
    and it answered the second while looking like the first.

    Both fixed here. The probe also now reports a SECOND count, taken
    from the raw response without the parser -- so a disagreement
    between what the server sent and what this project read is visible
    in the file itself, rather than needing another round trip.

    Deliberately NOT the raw response. The tooling in roombapy-prime
    prints that, and there the tester sees it and chooses to paste it.
    This file is generated and attached, so it keeps to the existing
    rule for this section: no schedule names, no room ids, no household
    id -- only the fields that decide whether an occurrence is computed.
    """
    robot = getattr(data, "prime_robot", None)
    household_id = getattr(data, "prime_household_id", None)
    if robot is None or not household_id:
        return {
            "household_id_resolved": bool(household_id),
            "note": (
                "no household id resolved for this robot -- the schedule "
                "endpoint is per-household and cannot be queried without one"
            ),
        }

    try:
        raw = await robot.get_schedules_raw(household_id)
    except Exception as exc:  # noqa: BLE001
        return {"household_id_resolved": True, "error": f"{type(exc).__name__}: {exc}"}

    from roombapy_prime.models.schedules_dnd import (  # noqa: PLC0415
        HouseholdSchedule,
        SchedulesResponse,
    )

    raw_count = 0
    recognised_shape = isinstance(raw, dict)
    if isinstance(raw, dict):
        containers = raw.get("household_schedules")
        for container in containers if isinstance(containers, list) else []:
            if isinstance(container, dict):
                inner = container.get("schedules")
                raw_count += len(inner) if isinstance(inner, list) else 0

    summary: list[dict[str, Any]] = []
    for container in SchedulesResponse.from_json(raw).household_schedules:
        for entry in container.schedules:
            if not isinstance(entry, dict):
                continue
            options = HouseholdSchedule.from_json(entry).options
            start = options.start
            summary.append({
                "enabled": options.enabled,
                "deleted": options.deleted,
                "frequency": str(options.frequency),
                "days": start.day if start else None,
                "hour": start.hour if start else None,
                "min": start.min if start else None,
                "has_commands": bool(options.commands),
            })
    return {
        "household_id_resolved": True,
        # Found in the a18 bug hunt: a response that is not a dict at all
        # produced count 0 / raw_count 0, indistinguishable from an
        # account with no schedules. The type name carries no content,
        # so it is safe to include here.
        "response_shape": type(raw).__name__ if not recognised_shape else "dict",
        "response_shape_recognised": recognised_shape,
        # count is this project's reading; raw_count is what the server
        # sent, counted without the parser. They should match.
        "count": len(summary),
        "raw_count": raw_count,
        "parser_disagrees": raw_count != len(summary),
        "schedules": summary,
    }


def _decoded_error(code: Any) -> dict[str, Any]:
    """An error code with its label, or a plain zero.

    The local dump decodes this from the MQTT client. Cloud-only had the
    same number sitting in `cleanMissionStatus` and reported it raw --
    so a reporter had to look up what 224 means, which is the kind of
    step that turns a five-minute answer into a round trip.
    """
    if not code:
        return {"error_code": 0, "error_message": None}
    return {
        "error_code": code,
        "error_message": ERROR_CODE_LABELS.get(code, "unknown error code"),
    }


def _state_from_shadows(data: Any) -> dict[str, Any]:
    """A reported-state-shaped dict, assembled from the named shadows.

    THE CLOUD-ONLY DUMP WAS NOT MISSING DATA, IT WAS MISSING A
    TRANSLATION. Every field the local sections read lives in the
    shadows this robot already publishes, just split across documents:

        ro-currentstate  batPct, bin, dock, tankPresent, runtimeStats,
                         cleanMissionStatus, p2maps
        ro-stats         bbchg, bbchg3, bbmssn, bbsys
        ro-configinfo    hwPartsRev
        classic          cap, sku

    Merged, that is the same shape a locally connected robot reports --
    so the sections written against `state` work unchanged.

    WHY THIS EXISTS AT ALL: the previous attempt to close the gap used
    "does the helper need only runtime data" as the test. That is a
    question about function signatures, not about what the robot can
    tell us, and it left every state-derived section local-only while
    the state sat in the dump three keys away.

    Later documents win on a key collision, and nothing here is
    invented: absent stays absent.
    """
    coordinator = getattr(data, "prime_status_coordinator", None)
    shadows = getattr(coordinator, "data", None)
    if not isinstance(shadows, dict):
        return {}

    merged: dict[str, Any] = {}
    for name in ("classic", "ro-configinfo", "ro-stats", "ro-currentstate"):
        document = shadows.get(name)
        if isinstance(document, dict):
            merged.update(document)
    # CREDENTIALS OUT, BY NAME AND BY SHAPE.
    #
    # `ro-configinfo` carries `passwordHash`, and merging documents is
    # exactly how something like that reaches a place nobody reviewed.
    # The existing redaction covers the dump's own top level; this dict
    # is built here and would not pass through it.
    #
    # A password in a debug line already cost this project a release
    # (@ScenicSystemsLLC found it), so the sweep is by substring rather
    # than an exact list: a field added upstream tomorrow is caught too.
    for key in list(merged):
        low = key.lower()
        if any(
            mark in low
            for mark in ("password", "passwd", "secret", "token", "svcendpoints")
        ):
            merged.pop(key, None)
    return merged


def _active_map_versions(data: Any, state: dict[str, Any] | None = None) -> Any:
    """{p2map_id: active version}, from whichever source this robot has.

    RECORDED IN A PRIME-ONLY PATH, and that was a mistake worth naming.
    The field exists so a download shows which version of which map the
    robot is on -- precisely so nobody reads its absence as the robot
    not reporting a map, which cost four rounds with one tester.

    It was then written only from `PrimeRoomsImage`, which needs
    `prime_robot`. On a Classic robot it therefore said "not read yet
    this session" while the same file carried correct versions three
    sections away, under `smart_map.pmap_versions`. Contradicting itself
    is worse than being silent.

    Classic keeps them in the reported state's `pmaps` list, so the
    fallback reads that rather than inventing a second mechanism.
    """
    recorded = getattr(data, "prime_map_versions", None)
    if recorded:
        return dict(recorded)

    pmaps = (state or {}).get("pmaps")
    if isinstance(pmaps, list) and pmaps:
        return {
            str(next(iter(entry))): str(entry[next(iter(entry))])
            for entry in pmaps
            if isinstance(entry, dict) and entry
        }
    return "not read yet this session"


def _sent_commands(data: Any) -> list[dict[str, Any]] | str:
    """The commands WE sent, newest last.

    THE QUESTION EVERY SILENT-FAILURE REPORT STARTS WITH. Three testers
    in one week reported a command that produced nothing, and each time
    the first thing to establish was whether it went out and with what.
    The robot's `lastCommand` records only what it RECEIVED -- which is
    exactly what was in doubt.

    `ok` is whether the publish succeeded. False proves the robot never
    saw it; True does not prove it acted, because a broker-confirmed
    command can still be ignored.
    """
    log = getattr(data, "sent_commands", None)
    if not log:
        return "nothing sent since startup"
    return list(log)


def _shadow_map_picture(data: Any) -> dict[str, Any] | str:
    """Last command and per-map versions, for a cloud-only robot.

    The local branch summarises these from the reported MQTT state. A
    cloud-only dump took an early return long before that and carried
    none of it -- no last command, no map ids, no versions.

    THE DATA WAS NEVER MISSING. The same fields sit in the named
    shadows, which that dump already includes, just raw and unsummarised
    across nine documents. Two testers in a row hit problems that turn
    on exactly these fields, and both times what they sent could not
    answer: one needed to know which map a command named, the other
    whether a command had been issued at all.
    """
    coordinator = getattr(data, "prime_status_coordinator", None)
    shadows = getattr(coordinator, "data", None)
    if not isinstance(shadows, dict):
        return "no named shadows cached"

    current = (shadows.get("ro-currentstate") or {})
    software = (shadows.get("rw-software") or {})
    last = software.get("lastCommand") or current.get("lastCommand") or {}
    pmaps = current.get("p2maps") or current.get("pmaps") or []

    return {
        # Ids and timestamps only -- no credentials live in these.
        "last_command": {
            k: v for k, v in last.items()
            if k in ("command", "initiator", "time", "pmap_id",
                     "p2map_id", "user_pmapv_id", "ordered")
        } if isinstance(last, dict) else None,
        "last_command_regions": [
            r.get("region_id") if isinstance(r, dict) else r
            for r in (last.get("regions") or [])
        ] if isinstance(last, dict) else None,
        "map_count": len(pmaps) if isinstance(pmaps, list) else None,
        # PRIME NAMES ITS FIELDS; Classic maps id to version directly.
        # Copying the Classic shape here produced `{"p2map_id": "..."}`
        # -- the key name as a key -- which testing against a real dump
        # caught immediately and reading the code would not have.
        "pmap_versions": {
            str(m.get("p2map_id")): str(m.get("p2mapv_id"))
            for m in pmaps
            if isinstance(m, dict) and m.get("p2map_id")
        } if isinstance(pmaps, list) else None,
    }


def _missions_with_traversals(data: Any) -> int | str:
    """How many stored missions carry `traversal` events.

    These are what the bootstrap alignment derives door positions from
    when the robot publishes no pose of its own. Two are needed. A
    download showed neither the count nor whether any existed.
    """
    store = getattr(data, "mission_store", None)
    records = getattr(store, "records", None)
    if not isinstance(records, list):
        return "no mission store"
    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        events = (record.get("timeline") or {}).get("finEvents") or []
        if any(
            isinstance(e, dict) and e.get("type") == "traversal"
            for e in events
        ):
            count += 1
    return count


def _position_chain(data: Any) -> dict[str, Any]:
    """Where the "which room" chain stands, link by link.

    Each link is useless without the one before it, and each fails
    quietly. Reporting them together turns a silent nothing into a
    readable answer.
    """
    aligner = getattr(data, "umf_aligner", None)
    renderer = getattr(data, "renderer", None)
    return {
        "position_points_collected": getattr(renderer, "point_count", None),
        "aligner_present": aligner is not None,
        "aligner_aligned": getattr(aligner, "aligned", None),
        # THE TWO NUMBERS THAT SAY WHY IT IS NOT ALIGNED.
        #
        # `aligner_aligned: false` with room polygons present says the
        # geometry arrived and the mapping did not, and stops there. The
        # mapping needs door candidates, which are the midpoints of gaps
        # in the floor plan's outline -- so a robot that publishes no
        # position can still align, provided the outline has
        # door-shaped gaps in it.
        #
        # @Thonno has eight room polygons, doors between every room, and
        # zero door markers. Whether the outline never arrived or the
        # gap search found nothing in it was not answerable from a
        # download, which is the sort of gap that turns a question into
        # a guess.
        "outline_points": len(
            getattr(aligner, "_points2d", None) or []
        ) if aligner is not None else None,
        "door_candidates": len(
            getattr(aligner, "_door_candidates", None) or []
        ) if aligner is not None else None,
        # WHAT A FALSE ALIGNMENT ACTUALLY COSTS.
        #
        # `aligner_aligned: false` reads like a defect and often is not
        # one. Matching needs door candidates from the floor plan AND
        # markers from observed positions; a robot that publishes no
        # position has the first and never the second. The fallback
        # calibration then runs in UMF space, room lookup is correct,
        # and only the drawn map lacks outlines.
        #
        # @Thonno removed and re-added his device chasing this, and
        # then asked whether that had broken it. It had not, and his
        # room tracking was working the whole time -- the field just
        # gave him no way to know that.
        "alignment_note": (
            None if getattr(aligner, "aligned", False) else
            "not aligned: needs door candidates AND position-derived "
            "markers. Room lookup still works via UMF-space fallback; "
            "only drawn room outlines are affected"
        ) if aligner is not None else None,
        # WHETHER THE FALLBACK CAN EVER START.
        #
        # Without positions, synthetic markers are derived from
        # `traversal` events in the cloud mission history. If there are
        # none, that route cannot run either -- and nothing showed it,
        # so the question could only be guessed at.
        "missions_with_traversals": _missions_with_traversals(data),
        "room_polygons": len(
            getattr(aligner, "room_polygons_umf", None) or {}
        ) if aligner is not None else None,
        # Two missions' worth of door sightings is what `align()` wants.
        "door_markers": len(
            getattr(
                getattr(data, "geometry_store", None), "door_markers", None
            ) or []
        ),
    }


def _nav_stats_with_provenance(state: dict[str, Any]) -> dict[str, Any] | None:
    """`mssnNavStats`, with whether it belongs to the running mission.

    The robot writes this up after a run finishes, so during a mission
    it usually describes the PREVIOUS one. Reporting it as `_live` made
    that invisible.
    """
    stats = state.get("mssnNavStats")
    if not isinstance(stats, dict):
        return None
    running = (state.get("cleanMissionStatus") or {}).get("nMssn")
    return {
        **stats,
        "belongs_to_running_mission": (
            None if running is None or stats.get("nMssn") is None
            else running == stats.get("nMssn")
        ),
        "running_mission_nmssn": running,
    }


def _prime_shadow_dump(data: Any) -> dict[str, Any]:
    """Every named shadow's contents, minus identifying fields.

    Dumped rather than summarised on purpose. A summary can only show
    what someone already thought to look for, and the recurring problem
    with this integration has been the opposite: fields nobody modelled,
    silently dropped, invisible until a tester pasted raw output.
    `googleControl` and five capability flags were both found that way.

    Redaction is by key NAME at every depth, because shadows nest and a
    top-level filter would miss `state.reported.hwPartsRev`.
    """
    coordinator = getattr(data, "prime_status_coordinator", None)
    if coordinator is None or not coordinator.data:
        return {"available": False}

    # STRING OR NOTHING. `blid in value` raises TypeError on anything
    # else, and diagnostics failing to produce is the worst possible
    # moment to fail -- they are read when something is already wrong.
    blid = getattr(data, "blid", None)
    blid = blid if isinstance(blid, str) and blid else None

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: (
                    "**REDACTED**"
                    if k in _SHADOW_REDACT or _is_secret_key(k)
                    else _clean(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    # KEY-BASED FIRST, THEN VALUE-BASED. The two catch different things:
    # a key called `svcEndpoints` has no recognisable value pattern, and
    # a blid embedded in `p2map_id` has no recognisable key.
    return {
        name: _redact_values(_clean(shadow), blid)
        for name, shadow in coordinator.data.items()
    }


def _room_tracking_summary(data: Any) -> dict[str, Any] | str:
    """What the room-advance logic is actually working with.

    NOT PRIME-ONLY. The two fields that existed were reported inside the
    Prime branch, so a Classic robot's dump carried `stores: null` and
    said nothing at all about room tracking -- @Thonno sent a dump from
    the middle of a mission and none of the four values that decide a
    room advance were in it.

    Each of these has cost a diagnosis:

      - `planned_rooms` / `current_room_idx`: whether the display is
        wrong or simply has nowhere to go
      - `expected_room_sec`: the estimate the confidence check compares
        against -- a whole-house average leaked in here as 19211 s per
        room on a two-room mission
      - `time_in_current_room_sec`: measured from accumulated message
        gaps, and it can silently stop growing
      - `room_progress_observed`: true only once an advance has really
        happened, which separates "never moved" from "moved wrongly"
    """
    store = getattr(data, "mission_timer_store", None)
    if store is None:
        return "not created"
    return {
        "mission_id": getattr(store, "mission_id", None),
        "planned_rooms": getattr(store, "planned_rooms", None),
        "current_room_idx": getattr(store, "current_room_idx", None),
        "current_room": getattr(store, "current_room", None),
        "room_progress_observed": getattr(
            store, "room_progress_observed", None
        ),
        "expected_room_sec": getattr(store, "expected_room_sec", None),
        "time_in_current_room_sec": getattr(
            store, "time_in_current_room_sec", None
        ),
        # Zero on a robot that has run is the signal that phase
        # transitions are not reaching the store.
        "elapsed_run_min": getattr(store, "elapsed_run_min", None),
        "run_sec": getattr(store, "run_sec", None),
    }


def _prime_store_summary(data: Any) -> dict[str, Any]:
    """Whether each Prime-relevant store exists and holds anything.

    Deliberately counts rather than dumps: a mission history is hundreds
    of records, and the question being answered is "is this populated",
    not "what is in it".
    """
    summary: dict[str, Any] = {}

    store = getattr(data, "mission_store", None)
    if store is None:
        summary["mission_store"] = "not created"
    else:
        try:
            # `days` is REQUIRED. Calling query() without it raises
            # TypeError, which this except turned into "unreadable" --
            # a store that was fine, reported as broken.
            records = store.query(days=3650)
            # THE COUNT WITHOUT A RECORD ANSWERS NOTHING.
            #
            # @chairstacker sent a download to settle why
            # `area_cleaned_today` and `clean_streak` both read 0, and
            # this block told him 128 records exist without showing one
            # -- so it could not distinguish "no records for today" from
            # "records with no area_sqft". A second round for a field
            # the first download should have carried.
            #
            # The most recent record, and the most recent that has an
            # area at all. Those two together say whether the field is
            # missing everywhere or only lately.
            #
            # Nothing here identifies a person or a home: durations,
            # areas, a result string and region ids. Room NAMES are
            # elsewhere in this file already.
            latest = records[-1] if records else None
            with_area = next(
                (r for r in reversed(records) if r.get("area_sqft") is not None),
                None,
            )
            summary["mission_store"] = {
                "record_count": len(records),
                "latest_id": latest.get("id") if latest else None,
                "latest_record": latest,
                # THE LAST TEN, in summary. One full record shows what a
                # record looks like; it cannot show whether two of them
                # describe the same run.
                #
                # @chairstacker (#64): "two previously cancelled
                # missions showing in the file while there was actually
                # only one". Deduplication keys on `p_{mission_id}`, so
                # a duplicate means two different mission ids for one
                # physical run -- and answering that needs both records
                # side by side, which this section could not show.
                #
                # Summary only: a full record carries the entire command
                # payload and ten of those would swamp the file.
                "recent": [
                    {
                        "id": r.get("id"),
                        "started_at": r.get("started_at"),
                        "ended_at": r.get("ended_at"),
                        "result": r.get("result"),
                        "duration_min": r.get("duration_min"),
                        "area_sqft": r.get("area_sqft"),
                        # WHETHER THE CLOUD MERGE LANDED. `cleaned_rooms`
                        # is derived from `timeline.finEvents`, so a null
                        # room list means the timeline never arrived --
                        # not that no rooms were cleaned.
                        #
                        # @ScenicSystemsLLC (#82) saw null on both of one
                        # robot's missions and populated lists on the
                        # other robot's, same model and same day. Without
                        # this flag the two cases are indistinguishable
                        # from outside.
                        "has_timeline": isinstance(r.get("timeline"), dict),
                        "room_event_count": len(
                            (r.get("timeline") or {}).get("finEvents") or []
                        ) if isinstance(r.get("timeline"), dict) else 0,
                    }
                    for r in records[-10:]
                ],
                "records_with_area": sum(
                    1 for r in records if r.get("area_sqft") is not None
                ),
                "records_with_result": sum(
                    1 for r in records if r.get("result") not in (None, "unknown")
                ),
                "latest_record_with_area": with_area,
                "result_values": sorted(
                    {str(r.get("result")) for r in records}
                )[:12],
            }
        except Exception:  # noqa: BLE001
            summary["mission_store"] = "unreadable"

    store = getattr(data, "maintenance_store", None)
    summary["maintenance_store"] = "not created" if store is None else {
        "filter_resets": len(getattr(store, "filter_reset_history", None) or []),
        "brush_resets": len(getattr(store, "brush_reset_history", None) or []),
    }

    store = getattr(data, "mission_timer_store", None)
    summary["mission_timer_store"] = "not created" if store is None else {
        # Zero elapsed on a robot that has run is the signal that phase
        # transitions are not reaching the store -- the failure mode that
        # would otherwise be invisible.
        "elapsed_run_min": getattr(store, "elapsed_run_min", None),
        "current_room": getattr(store, "current_room", None),
    }

    #: The five pose-derived stores plus freeze_snapshot_store are
    #: deliberately absent for Prime, so their absence is expected rather
    #: than a fault. Stated here so a reader does not go looking.
    store = getattr(data, "robot_profile_store", None)
    summary["robot_profile_store"] = "not created" if store is None else {
        # Needs at least five missions before it produces means at all,
        # so "has_stats: false" on a fresh install is correct rather than
        # a fault.
        # ANY LEARNED STATISTIC COUNTS, not just a mission counter.
        #
        # This read `bool(mission_count)`, and nothing on the Prime path
        # increments `mission_count` -- so it reported `false` on a
        # robot whose duration and area means were sitting right beside
        # it, computed from 49 imported missions.
        #
        # It answered "has the Classic path run" while appearing to
        # answer "are there stats", and cost @utkjmitch an hour of
        # chasing the wrong absence.
        "has_stats": any(
            getattr(store, name, None)
            for name in (
                "mission_count",
                "mission_duration_mean",
                "mission_area_mean",
                "learned_filter_hours",
                "learned_brush_hours",
            )
        ),
    }

    summary["pose_derived_stores"] = "not applicable to Prime (no pose data)"
    return summary


def _robot_cloud_connection(data: Any) -> dict[str, Any]:
    """Whether the robot itself is connected to iRobot's cloud.

    From the rw-constatus shadow, which the robot maintains. Distinct
    from our own MQTT connection: ours can be perfectly healthy while
    the robot sits offline, and then no amount of reconnecting on our
    side produces a single message.
    """
    coordinator = data.prime_status_coordinator
    if coordinator is None or not coordinator.data:
        return {"known": False, "note": "status coordinator has no data yet"}
    shadow = coordinator.data.get("rw-constatus") or {}
    connected = shadow.get("connected")
    if connected is None:
        return {"known": False, "note": "rw-constatus carries no connected field"}
    return {
        "known": True,
        "connected": bool(connected),
        "note": (
            "robot is online with iRobot's cloud; an empty push stream is on our side"
            if connected else
            "ROBOT IS OFFLINE from iRobot's cloud -- it is sending nothing, so an "
            "empty push stream is expected. Check the robot's Wi-Fi rather than the "
            "integration."
        ),
    }


def _shape_of(value: Any, depth: int = 0) -> Any:
    """Structure of a payload without its contents.

    Keys and types, with values truncated hard. Written for the Prime
    mission timeline, which is modelled from the app's source and has
    never been seen on the wire -- the mapping that would light up four
    dead sensors needs the field names and nothing else.

    Numbers are kept: a duration or an area is not private, and seeing
    that `duration_m` holds 43 rather than 43.0 is exactly the kind of
    detail that decides whether a mapping works first time. Strings are
    cut to eight characters, which leaves an id recognisable as an id
    and unusable as an id.
    """
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {str(k): _shape_of(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        if not value:
            return []
        # One sample rather than the whole list: a timeline of forty
        # missions has one shape and forty sets of values.
        return [_shape_of(value[0], depth + 1), f"…and {len(value) - 1} more"]
    if isinstance(value, str):
        return value[:8] + "…" if len(value) > 8 else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


def _vendor_capabilities(config_entry: Any) -> dict[str, Any]:
    """The `digiCap` flags, as the robot reports them.

    Empty for a Classic robot and for a Prime one that has not reported
    them yet -- an absent block is not a robot that lacks the features.
    """
    # READ FROM THE SHADOW, not from the login response. The login
    # entry carries `digiCap` too, but the integration never keeps it --
    # and the unnamed shadow has the same block, refreshed with
    # everything else.
    data = getattr(config_entry, "runtime_data", None)
    coordinator = getattr(data, "prime_status_coordinator", None)
    shadows = getattr(coordinator, "data", None)
    if not isinstance(shadows, dict):
        return {}
    # ALL THREE FAMILIES, ACROSS ALL THREE SHADOWS.
    #
    # `capabilityFromKey` gates 35 features, and they are not all in one
    # place. Twenty-eight read the unnamed THING shadow (`cap.*`,
    # `digiCap.*`), five read `ro-currentstate` (`dock.cap.*`), and two
    # read `rw-settings` (`detergent`, `suctionLevel`).
    #
    # A first version of this looked in every shadow for the same three
    # blocks, which happens to work -- but only because it searched
    # everywhere rather than because it knew where to look. Scanning all
    # of them is the right behaviour and now the documented reason: a
    # robot that reports `dock.cap` somewhere unexpected still gets
    # reported.
    #
    # A report showing one family and hiding the others invites the
    # wrong conclusion about the two it hides.
    out: dict[str, Any] = {}
    for body in shadows.values():
        if not isinstance(body, dict):
            continue
        for family in ("digiCap", "cap"):
            block = body.get(family)
            if isinstance(block, dict):
                out.update({f"{family}.{k}": v for k, v in block.items()})
        dock = body.get("dock")
        if isinstance(dock, dict) and isinstance(dock.get("cap"), dict):
            out.update({f"dock.cap.{k}": v for k, v in dock["cap"].items()})
        # The two settings-shadow gates, which are plain top-level keys
        # rather than a block: `detergent` and `suctionLevel`.
        for flat in ("detergent", "suctionLevel"):
            if flat in body:
                out[flat] = body[flat]
    return out


def _withheld_features(config_entry: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Capabilities this robot is not offered, and the condition that
    withheld each one.

    Best-effort: a diagnostics download that fails because of its own
    explanatory block would be worse than one without it.
    """
    try:
        return withheld_features(config_entry, state or {})
    except Exception as exc:  # noqa: BLE001
        # The failure itself, rather than a silent empty block -- a
        # missing explanation would leave the same gap this exists to
        # close.
        return {"error": f"{type(exc).__name__}: {exc}"}


def _structural_diagnostics() -> dict[str, Any]:
    """Sites that have failed and never once succeeded.

    Six faults in four days were invisible because their symptom read as
    "there is nothing here". This block is where that stops being
    invisible in a diagnostics download.
    """
    return diagnostic_info()


def _push_freshness(data: Any) -> dict[str, Any]:
    """How long since ANY Prime push message arrived.

    Written by both Prime coordinators on every message. Zero means
    nothing has ever arrived -- which on a robot that has been running
    is a far stronger signal than any of the success flags nearby."""
    # Coerced defensively: diagnostics must never be the thing that
    # raises. Someone downloading it is already trying to work out why
    # something is broken, and a traceback here replaces the answer
    # they came for with a second problem.
    try:
        ts = float(getattr(data, "last_mqtt_message_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        return {"last_message_ts": None, "seconds_ago": None, "note": "unreadable"}

    if ts <= 0:
        # DELIBERATELY NOT AN ACCUSATION (reworded this session).
        #
        # This used to read "the stream is not delivering", which states
        # a fault. It is often not one: shadow deltas arrive when the
        # shadow CHANGES, and a robot parked on a full battery changes
        # almost nothing. After a restart with no mission since, zero
        # messages is the expected reading.
        #
        # I wrote that wording, then read it back on a tester's
        # diagnostics and believed it -- and went looking for a
        # connection bug on a robot that simply had nothing to say. A
        # diagnostic that draws its own conclusion gets that conclusion
        # believed, including by its author.
        return {
            "last_message_ts": None,
            "seconds_ago": None,
            "note": (
                "no push message since startup. EXPECTED if the robot has been idle "
                "since Home Assistant started -- deltas arrive on change, and a "
                "docked robot on a full battery changes little. Only a concern if "
                "the robot has run a mission since startup, which would certainly "
                "have produced messages."
            ),
        }
    age = _time_mod.time() - ts
    return {
        "last_message_ts": round(ts),
        "seconds_ago": round(age),
        "note": (
            "stale -- a running robot should push far more often than this"
            if age > 900
            else "recent"
        ),
    }


def _prime_capability_report(config_entry: RoombaConfigEntry) -> dict[str, Any]:
    """NEW (this session): the single most common Prime support question
    is "why do I not have sensor X?" -- and since v4.0.0a6 the honest
    answer is often "because your robot's own capability flags say it
    can't do that". None of that was visible anywhere: the flags weren't
    in diagnostics, and neither was the decision they drove. Anyone
    asking had to be walked through it by hand.

    Reports the raw flags AND the resulting per-entity decision, in the
    same three-way form the gating itself uses (created / suppressed /
    created-because-unknown) -- see get_prime_capability_flags()'s own
    "None means unknown, only explicit 0 means absent" contract."""
    from .prime_coordinator import get_prime_capability_flags  # noqa: PLC0415

    cap, dock_cap = get_prime_capability_flags(config_entry)

    def _decision(flag: Any, label: str) -> str:
        if flag is None:
            return "created (capability unknown -- failing open)"
        if flag == 0:
            return f"suppressed ({label} == 0)"
        return f"created ({label} == {flag!r})"

    return {
        "cap_flags": dataclasses.asdict(cap) if cap is not None else None,
        "dock_cap_flags": dataclasses.asdict(dock_cap) if dock_cap is not None else None,
        "entity_decisions": {
            # CORRECTED: these two no longer share a rule, and this
            # report said they did.
            #
            # @chairstacker read his own diagnostics and asked whether
            # both should refer to `cap.scrub`. They should not.
            # `mop_tank_present` was moved to field presence
            # (`tankPresent`) after he reported a tank sensor for a tank
            # he does not have -- his water is in the Clean Base. This
            # line kept describing the rule that was removed.
            #
            # A stale explanation is worse than none: it sends the next
            # reader to the wrong gate, and the whole point of this block
            # is to answer "why does this entity exist".
            "detected_pad": _decision(getattr(cap, "scrub", None), "cap.scrub"),
            "mop_tank_present": (
                "created (tankPresent reported)"
                if _prime_reports_tank(config_entry)
                else "skipped (tankPresent absent)"
            ),
            "suction_level": _decision(getattr(cap, "suction_lvl", None), "cap.suctionLvl"),
            "carpet_boost_switch": _decision(getattr(cap, "carpet_boost", None), "cap.carpetBoost"),
            "pad_wash_status": _decision(getattr(dock_cap, "pad_wash", None), "dock.cap.pw"),
            "pad_dry_status": _decision(getattr(dock_cap, "pad_dry", None), "dock.cap.pd"),
        },
    }


def _prime_mission_status(config_entry: RoombaConfigEntry) -> dict[str, Any] | None:
    """NEW (this session): the fields that explain what the robot is
    actually doing -- and, crucially, why it might have REFUSED to do
    something. not_ready/cond_not_ready carry readiness-refusal reasons
    that appear in no error field and on no rejection topic; a mission
    that silently never starts leaves its trace here and nowhere else.
    regions_left shows whether a region-based mission actually began.

    Deliberately omits mission_id -- it identifies a specific run and
    adds nothing to triage."""
    from roombapy_prime.models import CurrentStateShadow, RobotReadinessState  # noqa: PLC0415

    coordinator = config_entry.runtime_data.prime_status_coordinator
    if coordinator is None or not coordinator.data:
        return None
    raw = coordinator.data.get("ro-currentstate")
    if not raw:
        return None

    status = CurrentStateShadow.from_json(raw).clean_mission_status
    if status is None:
        return None

    cond = status.cond_not_ready or []
    return {
        "phase": status.phase,
        "cycle": status.cycle,
        "error": status.error,
        "not_ready": status.not_ready,
        "not_ready_name": RobotReadinessState.name_for(status.not_ready),
        "cond_not_ready": [
            RobotReadinessState.name_for(c) if isinstance(c, int) else c for c in cond
        ],
        "regions_left": (raw.get("cleanMissionStatus") or {}).get("regions_left"),
        "detected_pad": CurrentStateShadow.from_json(raw).detected_pad,
    }


def _safe_region_names_from_command(data: Any) -> dict[str, str]:
    """Region names the last command carried, or {} if unreadable."""
    try:
        from .prime_coordinator import (  # noqa: PLC0415
            prime_region_names_from_command,
        )

        return prime_region_names_from_command(data)
    except Exception:  # noqa: BLE001
        return {}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
) -> dict[str, Any]:
    """Diagnostics, with a final credential sweep over everything.

    THE SWEEP IS THE POINT. Redaction used to be applied per section --
    the config entry here, the shadow dump there -- and a section that
    forgot to ask leaked. That is how `passwordHash` reached a public
    issue (@jouwdan, who caught it by hand).

    Anything hash-, token-, key- or password-shaped is removed on the
    way out, wherever it sits and however deep. A new section added
    later is covered without its author having to remember.
    """
    payload = await _build_diagnostics(hass, config_entry)
    # KEY-BASED REDACTION IS NOT ENOUGH, and this is the second time.
    #
    # `_redact_values` already replaces the blid wherever it appears in
    # a string, at any depth -- built after a tester found `p2map_id`
    # carrying "<BLID>-<epoch>" past a redaction list that only matched
    # key names. But it was only ever applied to the shadow section.
    #
    # The Prime payload's `lastCommand` repr embeds `map_id`, which is
    # the same "<blid>-<epoch>" shape, and it never went through it.
    # @utkjmitch found the blid in clear text twice in his own download
    # (#83) and has not attached one to an issue since.
    #
    # A diagnostics file exists to be pasted into a public issue. One
    # field leaking the blid through a side door defeats redacting it
    # everywhere else, and invites trust the output has not earned.
    _blid = config_entry.data.get("blid")
    return dict(_redact_values(_redact_secrets_everywhere(payload), _blid))


def _redact_secrets_everywhere(value: Any) -> Any:
    """Removes secret-shaped keys at every depth of the payload."""
    if isinstance(value, dict):
        return {
            k: ("**REDACTED**" if _is_secret_key(k) else _redact_secrets_everywhere(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets_everywhere(v) for v in value]
    return value


async def _build_diagnostics(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Sensitive keys (BLID, password, credentials) are redacted.
    The output is structured for easy triage of connectivity, map, and zone issues.
    """
    # Lazy import avoids circular dependency: diagnostics.py is imported by HA's
    # platform loader while __init__.py is still initialising.  By the time this
    # function is actually called, __init__.py is fully loaded.
    from . import roomba_reported_state  # noqa: PLC0415

    data = config_entry.runtime_data

    # REAL CRASH FOUND AND FIXED (architecture review, not a field
    # report): this whole function unconditionally accessed
    # data.roomba's own attributes (connected, current_state,
    # etc.) further below -- data.roomba is None for every CLOUD_ONLY
    # (V4/Prime) entry, so calling HA's own "Download diagnostics"
    # button (Settings -> Devices -> a Prime robot) would have raised
    # AttributeError immediately, every single time, for every real
    # Prime user. Returns a separate, genuinely Prime-relevant
    # diagnostics dict instead of reaching any of the Classic-only
    # code below.
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        status_coordinator = data.prime_status_coordinator
        # Once, not once per section that needs it.
        _shadow_state = _state_from_shadows(data)
        mission_coordinator = data.prime_coordinator
        return {
            "integration": DOMAIN,
            "version": config_entry.version,
            "title": config_entry.title,
            "connection_type": data.connection_type.value,
            "config": async_redact_data(dict(config_entry.data), _CLOUD_REDACT),
            "options": async_redact_data(dict(config_entry.options), _CLOUD_REDACT),
            "prime": {
                "household_id_resolved": data.prime_household_id is not None,
                # WHETHER THE LOGIN TELLS US WHEN IT EXPIRES.
                #
                # The library can refresh a token before it dies, but
                # only if the login response carries `expires`. That
                # field is confirmed in Classic login captures and has
                # NEVER been checked for Prime -- not because anyone
                # missed it, but because nothing currently needs it, so
                # it is parsed defensively and never shown.
                #
                # Which is the point: this value passes through on every
                # single login, by every tester, and no tool, log line
                # or diagnostic has ever displayed it. Same shape as the
                # five capability flags and googleControl -- data we
                # already hold and never look at.
                #
                # Reported as remaining seconds rather than a timestamp:
                # the question is "does proactive refresh have anything
                # to schedule against", and a raw epoch makes that a
                # subtraction rather than an answer.
                "token_expiry": _prime_token_expiry(data),
                "serial_info_resolved": data.prime_serial_info is not None,
                "model_sku": getattr(data.prime_serial_info, "sku", None),
                "family": getattr(data.prime_serial_info, "family", None),
                # serial_number itself is device-identifying -- deliberately
                # omitted, same reasoning as BLID redaction above.
            },
            # THE SHADOW CONTENTS, not just their names.
            #
            # Until now this listed which shadows had been seeded and
            # nothing about what was in them. That gap has cost real
            # time: the `audio` block in rw-settings is still unknown
            # months after a tester reported its key names by hand,
            # because he had to type them out rather than send a file.
            # And whether the settings shadow spells a field `padPlate`
            # or `pad_plate` is currently blocking a pad-wetness control
            # -- a question one download would answer.
            #
            # These are robot SETTINGS and STATE: child lock, eco
            # charging, suction level, schedules, firmware version, dock
            # status. Nothing here is a credential.
            # THE ROOM PATH, which this branch said nothing about.
            #
            # `cloud` and `smart_map` below belong to the Classic branch
            # and are absent here by design -- which reads like missing
            # cloud data if you do not know that. It cost a wrong
            # diagnosis on @mrsnyds' report: his `cloud: null` was
            # normal and present in every file he ever sent, including
            # ones from versions where room cleaning worked.
            #
            # What was actually needed was this: whether the map ids
            # resolve, whether the room names are cached, and whether
            # the two agree. `clean_room` fails when the first is empty
            # and the second is not.
            # OUR SIDE OF THE WIRE, and the robot's.
            "sent_commands": _sent_commands(data),
            # WHICH VERSION OF WHICH MAP, as last read from the cloud.
            #
            # Read in two places and recorded in neither, so its
            # absence from a download was taken four times over as the
            # robot not reporting a map at all. It was only ever this
            # file not being told.
            "active_map_versions": _active_map_versions(data),
            # THESE NEED NOTHING BUT RUNTIME DATA, so their absence here
            # was an oversight rather than a limitation. Both were added
            # to answer questions that came from cloud-only robots in
            # the first place -- @Thonno's room tracking and
            # @theChef613's zone names -- and neither could see them.
            "position_chain": _position_chain(data),
            "missions_with_traversals": _missions_with_traversals(data),
            # THE STATE-DERIVED SECTIONS, from the shadows. Same
            # helpers the local dump uses, same shape of input --
            # the only thing that was ever local-only is where the
            # state came from.
            "nav_telemetry": _nav_stats_with_provenance(
                _shadow_state
            ) or {},
            "withheld_features": _withheld_features(
                config_entry, _shadow_state
            ),
            "mission": {
                k: v for k, v in (
                    (_shadow_state.get("cleanMissionStatus") or {})
                ).items()
                if k in ("phase", "cycle", "error", "notReady",
                         "operatingMode", "nMssn", "mssnM", "mssnStrtTm",
                         "initiator", "sqft")
            },
            "battery": {
                k: _shadow_state.get(k)
                for k in ("batPct", "bbchg", "bbchg3")
            },
            "lifetime_stats": _shadow_state.get("bbmssn") or {},
            # THE DECODED ERROR. The local section reads it off the MQTT
            # client; the same number is in `cleanMissionStatus.error`,
            # and the label table is shared. A raw code is a lookup the
            # reporter should not have to do.
            "error": _decoded_error(
                (_shadow_state.get("cleanMissionStatus") or {}).get("error")
            ),
            "bin": _shadow_state.get("bin") or {},
            "dock": _shadow_state.get("dock") or {},
            "tank_present": _shadow_state.get("tankPresent"),
            # THE DOCK IT IS SITTING ON. Same three fields the local
            # dump reports, from the same `dock` document -- a Clean
            # Base that reports a different hardware revision than
            # expected has explained more than one evacuation fault.
            "dock_identity": {
                "hw_rev": (_shadow_state.get("dock") or {}).get("hwRev"),
                "var_id": (_shadow_state.get("dock") or {}).get("varID"),
                "part_number": (_shadow_state.get("dock") or {}).get("pn"),
            },
            # WHICH MAP TIER THIS ROBOT IS ON. Needs nothing but runtime
            # data, and decides which half of this integration applies
            # to a reader's question.
            "capability": data.map_capability.value,
            # PURE RUNTIME DATA, and missed because they are built
            # inline rather than through a helper -- which is exactly
            # what the both-dumps check looks for, so it did not catch
            # them. A maintenance question from a cloud-connected robot
            # had none of this to go on.
            "learned_maintenance": (
                {
                    "learned_filter_hours":
                        data.maintenance_store.learned_filter_hours,
                    "learned_brush_hours":
                        data.maintenance_store.learned_brush_hours,
                    "filter_reset_history_len":
                        len(data.maintenance_store.filter_reset_history),
                    "brush_reset_history_len":
                        len(data.maintenance_store.brush_reset_history),
                }
                if data.maintenance_store is not None else None
            ),
            "robot_profile": (
                {
                    "name": data.robot_profile.name,
                    "battery_mah": data.robot_profile.battery_mah,
                    "battery_chemistry": data.robot_profile.battery_chemistry,
                    "battery_voltage": data.robot_profile.battery_voltage,
                }
                if data.robot_profile is not None else None
            ),
            "detected_pad": _shadow_state.get("detectedPad"),
            "capabilities_raw": {
                "cap": _shadow_state.get("cap"),
                "sku": _shadow_state.get("sku"),
            },
            "room_tracking": _room_tracking_summary(data),
            "cloud": _cloud_diag(data),
            "map_picture": _shadow_map_picture(data),
            "rooms": {
                "cached_names": len(
                    getattr(data, "prime_room_names", None) or {}
                ),
                "cached_name_ids": sorted(
                    str(k) for k in (
                        getattr(data, "prime_room_names", None) or {}
                    )
                ),
                # WHICH REGIONS THE MAP ITSELF CALLS ZONES.
                #
                # `discovered_zone_ids` below is what the CONFIG ENTRY
                # has recorded. The map's own `region_type` is a
                # separate thing, and it is what `available_rooms()`
                # filters on -- a region marked as a zone there is not
                # offered as a room, correctly, but nothing said so.
                #
                # @mrsnyds has nine named regions and eight usable
                # rooms; `clean_room` refuses "Guest Room" while
                # accepting the other eight, and from the outside that
                # reads as a bug rather than as a zone.
                # NOT READABLE FROM HERE, and worth saying so rather
                # than reading a phantom.
                #
                # The room-cleaning backend is built per call by
                # `async_get_room_cleaning_backend()` and never stored
                # on runtime data, so `_zone_region_ids` -- which is
                # what would settle whether "Guest Room" is a zone --
                # does not exist to be dumped. An earlier attempt to
                # read it here would have returned None forever, which
                # is exactly the shape the runtime-data guard catches.
                "zones_per_map_metadata": "not stored on runtime data",
                "discovered_zone_ids": sorted(
                    str(z) for z in (
                        (getattr(config_entry, "options", None) or {}).get(
                            "discovered_zone_ids", ()
                        )
                    )
                ),
            },
            "shadows": _prime_shadow_dump(data),
            "status_coordinator": {
                "started": status_coordinator is not None,
                "last_update_success": getattr(status_coordinator, "last_update_success", None),
                "named_shadows_seeded": (
                    sorted((status_coordinator.data or {}).keys())
                    if status_coordinator is not None and status_coordinator.data is not None
                    else []
                ),
            },
            # THE FIRST THING TO LOOK AT when Prime sensors appear frozen.
            #
            # last_update_success stays True forever if the push stream
            # simply stops delivering, because nothing raises -- the
            # generator just never yields again. So a coordinator can
            # report itself perfectly healthy while showing hours-old
            # data, which is exactly what a field report described.
            #
            # This is the only field that distinguishes "quiet because
            # nothing is happening" from "quiet because the stream
            # died". A large seconds_ago on a robot that has been
            # active means the connection is gone, whatever else says.
            "push_freshness": _push_freshness(data),
            # WITHOUT THIS PREFIX, TWO SUBSCRIPTIONS DIE SILENTLY.
            #
            # watch_live_map() and watch_mission_timeline() both build
            # their topic as "{irbt_topic_prefix}/things/{blid}/..." and
            # both raise immediately when it is None. The outer retry
            # loops catch that, wait, and double the backoff to five
            # minutes -- so the symptom is a map that updates every few
            # minutes instead of every few seconds, with nothing broken
            # anywhere a user can see.
            #
            # Everything else keeps working, which is what makes it hard
            # to spot: the shadow watcher uses shadow topics and needs no
            # prefix, so nine shadows seed, push_freshness stays fresh
            # and the robot reports as connected.
            #
            # It comes from `deployment["irbtTopics"]` in the login
            # response via a plain .get() -- deliberately not a hard gate,
            # because the key name was uncertain when that code was
            # written. A missing key therefore costs two features and
            # says nothing.
            #
            # @chairstacker's capture is what this is for: mid-mission,
            # every live-map counter at zero, mission timeline empty, and
            # no error anywhere.
            "irbt_topic_prefix_present": bool(
                getattr(data.prime_robot, "_irbt_topic_prefix", None)
            ) if data.prime_robot is not None else None,
            # THE OTHER HALF of a silent stream. push_freshness says
            # nothing is arriving; this says whether the ROBOT is even
            # connected to the cloud to send anything.
            #
            # Without it the two cases look identical from here, and
            # they need opposite responses: a robot off the network is
            # the owner's Wi-Fi, while a connected robot whose messages
            # never arrive is ours.
            #
            # Reading shadows keeps working either way -- the cloud
            # returns the last reported state whether or not the robot
            # is currently online -- which is exactly why an empty push
            # stream is not evidence of a broken integration on its own.
            "robot_cloud_connection": _robot_cloud_connection(data),
            # THE STORES, because their sensors read from them and
            # nothing else would show whether they are populated.
            #
            # Without this, "my mission sensors are empty" is
            # undiagnosable: an empty store, a store that never loaded,
            # and a store nothing writes to all look identical from
            # outside. Prime had all three of those states at various
            # points today.
            "stores": _prime_store_summary(data),
            "consumable_parts": _parts_report(data),
            "live_map": data.live_map_stats,

            # WHICH REGION NAMES EXIST, AND FROM WHERE.
            #
            # @chairstacker: rooms picked up names after he renamed
            # them; his eight zones stayed `None` in `--list-rooms`.
            # That tool reads map metadata, which carries room names
            # only -- so its output says nothing about zones either way.
            #
            # Three sources feed the name store: map metadata (rooms),
            # the bundle's `cleanZones` layer (zones), and the last
            # command's `region_name`. A download that shows the merged
            # result answers in one look which of them produced
            # anything, instead of prompting another round.
            "region_names": {
                "merged": dict(getattr(data, "prime_room_names", None) or {}),
                "from_last_command": _safe_region_names_from_command(data),
            },

            # PER-ROOM PREFERENCES, WHICH NOTHING HAS EVER CONFIRMED.
            #
            # `prime_room_map` reads `profile`, `suctionLevel`, `twoPass`,
            # `carpetBoost` and `swScrub` per room from
            # `operating_mode_defaults`, and `image.py` publishes them as
            # the `room_preferences` attribute on the Rooms Map so an
            # automation can honour a room's own settings rather than
            # override them.
            #
            # The version plan has carried this as "a29, unverified"
            # since it was built. It stayed unverified because nothing
            # here could answer it: the data lives on an entity
            # attribute, and asking a tester to expand one is a worse
            # question than reading it from a download they already
            # send.
            #
            # Same fix as the favourites block, which sat in the Classic
            # path while the question was about Prime: put the
            # instrument where the question is.
            "room_preferences": _prime_room_preferences(config_entry, hass),
            # THE LIST THE ROBOT MARKER IS DRAWN FROM, and the one thing
            # the counters above cannot tell you.
            #
            # live_map counts what ARRIVED. This counts what SURVIVED
            # into the list the renderer reads -- and the marker is only
            # drawn when it is non-empty. @DaRealGuGu reported 2267
            # position messages and no marker on the map, which the
            # counters alone cannot explain: either the list is empty
            # when the render runs, or the marker is drawn and not seen.
            #
            # Two numbers separate those cases. The last point is
            # included because a marker drawn far outside the map's own
            # bounds would be invisible for a third reason again.
            "trail_points": len(data.prime_positions),
            "trail_last_point": (
                data.prime_positions[-1] if data.prime_positions else None
            ),
            "capabilities": _prime_capability_report(config_entry),
            "mission_status": _prime_mission_status(config_entry),
            # SCHEDULES AS THE ROBOT REPORTS THEM, from the REST call
            # the calendar actually uses.
            #
            # Neither shadow carries them: `rw-schedule.cleanSchedule2`
            # is empty and the classic one holds a placeholder with no
            # days and midnight as the time. So a tester saying "I have
            # a weekly schedule and the calendar shows nothing" produced
            # a diagnostics file with no way to tell whether the
            # schedule arrives at all.
            #
            # Redacted down to the fields that decide whether the parser
            # computes an occurrence: enabled, deleted, frequency, and
            # the days and time. Names and commands are left out --
            # room ids and schedule names are not needed to answer this
            # and would widen what a public paste contains.
            "prime_schedules": await _prime_schedule_summary(data),

            # THREE BLOCKS THAT WERE ONLY EVER IN THE CLASSIC PATH, and
            # none of them is Classic-specific.
            #
            # The early return exists because this function used to reach
            # `data.roomba`'s attributes unconditionally and would raise
            # on every Prime entry. Returning a separate dict fixed the
            # crash and, quietly, decided that everything below was
            # Classic -- which was never checked block by block.
            #
            # `vendor_capabilities` is the sharpest case: its own
            # docstring says "Empty for a Classic robot and for a Prime
            # one that has not reported them yet". It reads `digiCap`
            # from the unnamed shadow. Written for Prime, placed where
            # Prime cannot reach it.
            #
            # `never_succeeded` takes no arguments at all -- a global
            # record of sites that have failed and never once worked,
            # built precisely because "there is nothing here" is how
            # these faults look. A Prime user reporting an empty list
            # could not see whether the fetch behind it had ever
            # succeeded.
            #
            # `warnings` asks whether HA's core roomba integration is
            # also loaded. That conflict has nothing to do with which
            # generation the robot is.
            "vendor_capabilities": _vendor_capabilities(config_entry),
            "never_succeeded": _structural_diagnostics(),
            "warnings": {
                "core_roomba_integration_also_active": any(
                    entry.domain == "roomba"
                    for entry in hass.config_entries.async_entries()
                    if entry.state.value == "loaded"
                ),
            },

            # THE THIRD INSTRUMENT THAT COULD NOT REACH THE QUESTION.
            #
            # A `favourites` block has existed since the favourites bug
            # was first reported -- built specifically so a download
            # could tell "the option is off" from "the list arrived
            # empty". It sits in the Classic path below, which a Prime
            # entry returns before ever reaching.
            #
            # So every diagnostics download taken to investigate missing
            # favourites on a Prime robot omitted the favourites block
            # entirely. @chairstacker sent one on a33 and it is not in
            # there.
            #
            # That is the same shape twice over: `get_favorites_raw()`
            # carried the unwrapping bug it was built to reveal, and this
            # block was placed where the tier it describes cannot see it.
            "favourites": await _favourites_diagnostics(data, config_entry),
            "mission_coordinator": {
                "started": mission_coordinator is not None,
                "last_update_success": getattr(mission_coordinator, "last_update_success", None),
                "has_mission_data": (
                    mission_coordinator is not None and mission_coordinator.data is not None
                ),
                # WHY IT CAN BE FALSE ON A WORKING SETUP.
                #
                # The timeline arrives by push and is held in memory
                # only. After a restart it is empty until the robot
                # runs a mission -- so False on a docked robot that has
                # not cleaned since startup is expected, not a fault.
                #
                # A tester saw True in one capture and False in the
                # next, from the same robot, with an update and restart
                # in between. Without this note that reads like a
                # regression.
                # THE SHAPE, NOT JUST WHETHER THERE IS ONE.
                #
                # This reported a bare True/False and threw the rest
                # away. One capture already came back True -- the
                # timeline had arrived, and we recorded only that fact.
                #
                # It matters because the Prime mission history is
                # modelled but has NEVER been seen on the wire. Four
                # sensors (clean streak, last mission, last duration,
                # area cleaned today) read a store that the Prime path
                # does not fill, and the mapping to fill it is a small
                # function -- once somebody knows what the wire actually
                # looks like. Building it against a model instead cost
                # four field rounds the last time (create_schedules).
                #
                # Keys and types only, values truncated: enough to write
                # the mapping, not enough to carry a household around in
                # a bug report.
                "mission_data_shape": _shape_of(
                    getattr(mission_coordinator, "data", None)
                ),
                "mission_data_note": (
                    "the timeline arrives by push and is not persisted; "
                    "empty until the robot runs a mission after startup"
                    if mission_coordinator is not None
                    and mission_coordinator.data is None
                    else None
                ),
            },
        }

    # NARROWED, NOT ASSERTED. Every CLOUD_ONLY entry returned above, so
    # `data.roomba` is set from here down -- but that guard is thirty
    # lines away and mypy cannot follow it, which produced nine
    # `union-attr` errors in this function alone.
    #
    # The crash those errors describe is real and was already found once
    # by hand: see the comment on the CLOUD_ONLY branch above. This
    # states the invariant where the code relies on it, so the next
    # person to add a Classic-only read here gets told rather than
    # finding out from a Prime user's download.
    roomba = data.roomba
    if roomba is None:  # pragma: no cover - CLOUD_ONLY returned above
        raise RuntimeError(
            "Classic diagnostics reached with no local robot -- the "
            "CLOUD_ONLY branch above should have returned"
        )
    state = roomba_reported_state(roomba)

    # Check whether the Core roomba integration is also active (conflict warning)
    core_roomba_active = any(
        e.domain == "roomba"
        for e in hass.config_entries.async_entries()
        if e.state.value == "loaded"
    )

    # ── Map subsystem ──────────────────────────────────────────────────────────
    map_diag: dict[str, Any] = {
        "capability": data.map_capability.value,
        "sent_commands": _sent_commands(data),
        # WHICH VERSION OF WHICH MAP, as last read from the cloud.
        #
        # Read in two places and recorded in neither, so its
        # absence from a download was taken four times over as the
        # robot not reporting a map at all. It was only ever this
        # file not being told.
        "active_map_versions": _active_map_versions(data, state),
        # THE POSITION CHAIN, END TO END.
        #
        # Every part of resolving "which room is the robot in" was
        # invisible here, and it cost a full round of guesswork on
        # @Thonno's report: his `point_count: 0` was the answer, and it
        # took reading the aligner's source to know that mattered.
        #
        # The chain is: position points collected -> door markers seen
        # across missions -> aligner transform -> `room_name_at()`. It
        # fails silently at whichever link is missing, and nothing said
        # which.
        "position_chain": _position_chain(data),
    }
    if data.renderer is not None:
        map_diag["renderer"] = data.renderer.diagnostic_info()
        # Include raw trajectory in mm for gap-analysis and door-detection tuning.
        # Uses the initial-scale inverse transform (cfg.scale / cfg.size_px centre).
        # Kept at top-level map_diag so Claude/devs can paste the list directly.
        if data.renderer.point_count > 0:
            map_diag["last_mission_trajectory_mm"] = data.renderer.points_mm
    # F-EPHEMERAL: outline_store diagnostics
    _outline = getattr(data, "outline_store", None)
    if _outline is not None:
        map_diag["outline_store"] = {
            "mission_count": _outline.mission_count,
            "contour_point_count": _outline.contour_point_count,
            "ready": _outline.ready,
        }

    # ── Room subsystem (ROOM-SEG Stage 6 — RoomSegStore, not ZoneStore) ─────────
    room_diag: dict[str, Any] = {"available": data.room_seg_store is not None}
    # WHAT THE ROOM-ADVANCE LOGIC SEES, on both generations.
    #
    # These lived inside the Prime branch, so a Classic dump said
    # nothing about room tracking at all -- @Thonno sent one from the
    # middle of a mission and not one of the values that decide an
    # advance was in it.
    room_diag["tracking"] = _room_tracking_summary(data)
    if data.room_seg_store is not None:
        room_diag.update(data.room_seg_store.diagnostic_info(
            grid_cell_count=(
                len(data.grid_store.cells)
                if data.grid_store is not None else None
            ),
        ))

    diag: dict[str, Any] = {
        "integration": DOMAIN,
        "version": config_entry.version,
        "title": config_entry.title,

        # Config and options with sensitive values redacted
        "config": async_redact_data(dict(config_entry.data), _CLOUD_REDACT),
        "options": async_redact_data(dict(config_entry.options), _CLOUD_REDACT),

        # Connection state
        # `continuous` and `delay` are gone from roombapy 2.x, which
        # keeps one supervised connection and reconnects on its own.
        # Reporting them as null would suggest they exist and are unset;
        # dropping them says what is true, and an old diagnostic still
        # reads fine because nothing consumes this by position.
        # WHICH KIND OF ENTRY THIS IS. A cloud-only dump says so; a
        # local one never did, so working out which branch produced a
        # file meant inferring it from which sections were present. That
        # cost real time this week: a missing section was read as a
        # generation difference when it was a connection-type one.
        "connection_type": data.connection_type.value,
        # HOW STALE THIS IS. The cloud-only dump reports it and the
        # local one did not, although `last_mqtt_message_ts` has been
        # kept here all along for the staleness watchdog. A robot that
        # has said nothing for hours looks identical to a healthy one in
        # every other field.
        "push_freshness": _push_freshness(data),
        "connection": {
            "connected": roomba.connected,
            "current_state": roomba.current_state,
            # `client_error` is gone in roombapy 2.x; `error_code` and
            # `error_message` are what it exposes now. Found by checking
            # every attribute read off the client against the real class
            # -- `self.vacuum` is typed `Any`, so mypy saw none of this.
            "error_code": roomba.error_code,
            "error_message": roomba.error_message,
        },

        # Error state
        "error": {
            "error_code": roomba.error_code,
            "error_message": (
                ERROR_CODE_LABELS[roomba.error_code]
                if roomba.error_code and roomba.error_code in ERROR_CODE_LABELS
                else roomba.error_message
            ),
        },

        # Device identity (non-sensitive capability / version info)
        "device": {
            "sku": state.get("sku"),
            "software_version": state.get("softwareVer"),
            "hardware_revision": state.get("hardwareRev"),
            "battery_type": state.get("batteryType"),
            "capabilities": state.get("cap", {}),
            # v2.8.0 FIRMWARE-VER — per-module firmware versions (i/s/j-series only).
            # subModSwVer contains navigation, connectivity, motion module versions.
            # Absent on 9-series (980/960/900) firmware.
            "sub_module_sw_versions": state.get("subModSwVer"),
        },

        # Current mission status
        "mission": state.get("cleanMissionStatus", {}),

        # Smart Map state — critical for diagnosing region-clean failures.
        # pmap_ids shows which maps the robot has stored (pmapv values redacted
        # as they are session tokens). lastCommand shows the most recent command
        # type and region_id so pmap resolution can be verified without needing
        # the full HA log.
        "smart_map": {
            "map_upload_allowed": state.get("mapUploadAllowed"),
            "pmap_learning_allowed": state.get("pmapLearningAllowed"),
            "not_ready_raw": state.get("cleanMissionStatus", {}).get("notReady"),
            # THE VALUE, not just the key name. `missionTelemetry` is in
            # the state of every i/s robot and this integration reads it
            # nowhere -- room progress is inferred from phase changes
            # instead, which works on `lewis` and leaves `soho` frozen
            # on the first planned room for a whole mission
            # (@ScenicSystemsLLC, two S9+ robots).
            #
            # `master_state_keys` lists the key and stops there, so
            # nobody could see what is inside it without being asked for
            # a raw dump. If the robot reports its own progress, this is
            # where it will show up.
            "mission_telemetry": state.get("missionTelemetry"),
            # "LIVE" ONLY WHEN IT IS. This handed `mssnNavStats` over
            # under that name whatever it contained. @Thonno's dump
            # showed nav stats from mission 1009 while mission 1010 was
            # running -- read as live telemetry, it says the robot is
            # idle mid-clean.
            #
            # The field is a delayed post-mission summary; the robot
            # fills it when the previous run is written up. Same shape
            # as the stale room list fixed in 4.1.7: correct data,
            # wrong label.
            "mssn_nav_stats": _nav_stats_with_provenance(state),
            "pmap_ids": [
                next(iter(p)) for p in state.get("pmaps", []) if p
            ],
            # THE VERSION EACH MAP CARRIES, not just which maps exist.
            #
            # A start command pairs a map id with THAT map's version id,
            # and pairing one map with another's version makes the robot
            # refuse to localise -- error 224, docked, no mission.
            #
            # `pmap_ids` showed which maps there are and nothing about
            # their versions, so diagnosing that meant inferring the
            # pairing from `lastCommand` rather than reading it. It took
            # two wrong guesses on @Thonno's two-map i7+ before the real
            # cause surfaced.
            #
            # Map ids and version stamps, no credentials.
            "pmap_versions": {
                str(next(iter(p))): str(p[next(iter(p))])
                for p in state.get("pmaps", [])
                if isinstance(p, dict) and p
            },
            "last_command_summary": {
                "command": state.get("lastCommand", {}).get("command"),
                "pmap_id": state.get("lastCommand", {}).get("pmap_id"),
                "user_pmapv_id": state.get("lastCommand", {}).get("user_pmapv_id"),
                "initiator": state.get("lastCommand", {}).get("initiator"),
                "region_ids": [
                    r.get("region_id")
                    for r in (state.get("lastCommand", {}).get("regions") or [])
                ],
            },
            # cleanSchedule2 stores scheduled/recent app-initiated region cleans.
            # Shows the exact pmap_id and user_pmapv_id the app used — useful for
            # verifying that our resolved values match what works.
            "clean_schedule2_pmaps": [
                {
                    "pmap_id": entry.get("cmd", {}).get("pmap_id"),
                    "user_pmapv_id": entry.get("cmd", {}).get("user_pmapv_id"),
                    "region_ids": [
                        r.get("region_id")
                        for r in (entry.get("cmd", {}).get("regions") or [])
                    ],
                }
                for entry in state.get("cleanSchedule2", [])
                if entry.get("cmd", {}).get("pmap_id")
            ],
        },

        # Lifetime statistics (useful for maintenance sensor debugging)
        "lifetime_stats": {
            "bbrun": state.get("bbrun") or {},
            "bbmssn": state.get("bbmssn") or {},
            "bbchg3": state.get("bbchg3") or {},
            # v2.8.0 DOCK-HEALTH — dock contact counters (nChatters/nKnockoffs/nAborts)
            "bbchg": state.get("bbchg") or {},
        },

        # NAVIGATION TELEMETRY, WHOLE. Not a selection of keys, because
        # the point is to find out which keys a given robot fills.
        #
        # Firmware analysis of the i-series (lewis 22.52.08) documents
        # twelve fields here, validated against a real m6 dump: `kdp`
        # and `sfkdp` (kidnapped detection), `l_drift`/`h_drift` (pose
        # confidence), `gLmk`/`lmk` (landmark density), `mpSt` (map
        # state), `reLc` (relocalisations), and map-change counters.
        #
        # This integration reads exactly one of them. The others were
        # assumed absent -- including a kidnap signal that the live-map
        # work went looking for and concluded did not exist on Classic
        # robots. It does; nobody had looked here.
        #
        # ONLY FILLED DURING A MISSION. On the dock most of it reads
        # zero, so a diagnostics download taken afterwards shows nothing
        # even when the fields are supported. It has to be pulled while
        # the robot is cleaning.
        #
        # Emitted verbatim rather than key-by-key: the 980 is a
        # different platform from the i-series, and picking keys in
        # advance would decide the question this exists to answer.
        # THE SECOND ONE. `mssn_nav_stats` was corrected to carry its
        # provenance; this copy was missed and still handed the raw
        # field over under a name that implies live telemetry.
        #
        # @Thonno read it as live twice, across two releases: "still
        # reporting the previous mission (1016) while the actual running
        # mission is 1017". Correct observation, and the label was what
        # made it look wrong.
        "nav_telemetry": _nav_stats_with_provenance(state) or {},

        # DOCK IDENTITY. The i-series OTA carries dock firmware for 14
        # hardware/variant combinations named `dock_hw{N}_var{M}`, and
        # these two fields map straight onto N and M -- so the dock
        # model is determinable and currently is not determined.
        #
        # Bears on several open reports: which docks can evacuate, which
        # have a fresh-water tank, and why `empty-now` does nothing on
        # one tester's install.
        "dock_identity": {
            "hw_rev": (state.get("dock") or {}).get("hwRev"),
            "var_id": (state.get("dock") or {}).get("varID"),
            "part_number": (state.get("dock") or {}).get("pn"),
            "fw_version": (state.get("dock") or {}).get("fwVer"),
        },

        # RF0 — robot profile (confirms which profile was matched at startup)
        "robot_profile": (
            {
                "name": data.robot_profile.name,
                "battery_mah": data.robot_profile.battery_mah,
                "battery_chemistry": data.robot_profile.battery_chemistry,
                "battery_voltage": data.robot_profile.battery_voltage,
                "estcap_scale_liion": data.robot_profile.estcap_scale_liion,
                "estcap_scale_nimh": data.robot_profile.estcap_scale_nimh,
            }
            if data.robot_profile is not None else None
        ),

        # L2 — self-calibrating maintenance lifespan (v2.5.0)
        "learned_maintenance": (
            {
                "learned_filter_hours": data.maintenance_store.learned_filter_hours,
                "learned_brush_hours":  data.maintenance_store.learned_brush_hours,
                "filter_reset_history_len": len(data.maintenance_store.filter_reset_history),
                "brush_reset_history_len":  len(data.maintenance_store.brush_reset_history),
            }
            if data.maintenance_store is not None else None
        ),

        # Last known position
        "position": state.get("pose"),

        # Bin / dock state
        "bin": state.get("bin"),
        "dock": state.get("dock"),

        # Map and zone subsystem
        "map": map_diag,
        "rooms": room_diag,
        # WHAT HAS NEVER WORKED. Empty on a healthy install; anything
        # listed here is a code path that has failed every time it ran,
        # which is a lead rather than a statistic.
        "never_succeeded": _structural_diagnostics(),
        # WHY A CAPABILITY IS NOT ON OFFER. @connormxy's clean_area
        # simply did not appear -- no error, no log line -- and he
        # reinstalled three integrations to find out why. Empty when
        # everything this robot could have, it has.
        "withheld_features": _withheld_features(config_entry, state),
        # WHAT IROBOT'S OWN APP OFFERS ON THIS ROBOT.
        #
        # Reported, NOT enforced. `cwia` says whether iRobot's "Clean
        # While Away" exists here -- it does not say whether OUR
        # presence scheduling works, because ours disables schedules
        # through `enabled`, which every Prime robot can do. Using the
        # flag as a gate would hide a working feature.
        #
        # `ddAutomation` is the same shape: it says iRobot offers Dirt
        # Detective, not that `clean_score` is missing.
        #
        # What it is good for is a report: somebody comparing our
        # presence scheduling against the app's can see, in one line,
        # whether the app has one at all.
        "vendor_capabilities": _vendor_capabilities(config_entry),
        # HOW MANY FAVOURITES REACHED US, and whether their buttons are
        # switched on.
        #
        # @chairstacker's two favourites appear as buttons on v3.5.1 and
        # not on the alpha. Everything between the fetch and the
        # entities is wired correctly, so the answer is either "the
        # option is off" or "the list arrived empty" -- and a report had
        # no way to tell those apart.
        # THIS BLOCK USED TO READ `prime_favorites` -- IN THE CLASSIC
        # BRANCH, where it is permanently empty because the loader
        # behind it returns [] with no prime_robot. So it answered the
        # question above with a structural zero: @pk-1966's i7+
        # reported 0 favourites while `cloud` in the same download said
        # 6, which reads as a favourites bug and was a diagnostics one.
        # Now shared with the Prime branch, and it names its source.
        "favourites": await _favourites_diagnostics(
            getattr(config_entry, "runtime_data", None), config_entry
        ),

        # Cloud coordinator status
        "cloud": _cloud_diag(data),

        # All top-level keys in master_state (for debugging unknown models)
        "master_state_keys": sorted(state.keys()),

        # Conflict warning
        "warnings": {
            "core_roomba_integration_also_active": core_roomba_active,
        },
    }

    return diag
