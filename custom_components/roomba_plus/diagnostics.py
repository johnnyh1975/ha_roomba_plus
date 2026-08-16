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
        "last_update_success": cc.last_update_success,
        "last_exception": str(cc.last_exception) if cc.last_exception else None,
    }
    if cc.data:
        result["pmap_count_total"] = len(cc.data.get("pmaps", []))   # all pmaps from API
        result["favorite_count"] = len(cc.data.get("favorites", []))
        result["active_pmap_id"] = cc.active_pmap_id
        result["region_count_active"] = len(cc.regions)   # active pmap only (post-filter)
        result["zone_count_active"] = len(cc.zones)       # active pmap only (post-filter)
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


def _prime_room_preferences(config_entry: RoombaConfigEntry) -> dict[str, Any]:
    """The Rooms Map's `room_preferences`, or why there are none.

    Read from the entity rather than recomputed, so a download reports
    what a consumer would actually see. A recomputation could succeed
    where the entity fails and hide the gap this exists to find.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    data = config_entry.runtime_data
    hass = getattr(config_entry, "hass", None)
    if hass is None:
        return {"note": "no hass on this entry"}
    registry = er.async_get(hass)
    unique = f"{data.blid}_rooms_map"
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
    return prefs


async def _prime_favorites_raw(data: Any) -> Any:
    """The unparsed favourites response, for a diagnostics download.

    Never raises: a diagnostics download that fails because one of its
    sections failed is worse than a section that reports why. The error
    text is the finding when the call cannot be made at all."""
    robot = getattr(data, "prime_robot", None)
    if robot is None:
        return {"note": "no prime robot on this entry"}
    try:
        return await robot.get_favorites_raw()
    except Exception as err:  # noqa: BLE001
        return {"error": f"{type(err).__name__}: {err}"}


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
            summary["mission_store"] = {
                "record_count": len(records),
                "latest_id": records[-1].get("id") if records else None,
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


def _vendor_capabilities(config_entry: Any) -> dict:
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


def _withheld_features(config_entry: Any, state: dict) -> dict:
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
    return _redact_secrets_everywhere(payload)


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
    # data.roomba's own attributes (roomba_connected, current_state,
    # etc.) further below -- data.roomba is None for every CLOUD_ONLY
    # (V4/Prime) entry, so calling HA's own "Download diagnostics"
    # button (Settings -> Devices -> a Prime robot) would have raised
    # AttributeError immediately, every single time, for every real
    # Prime user. Returns a separate, genuinely Prime-relevant
    # diagnostics dict instead of reaching any of the Classic-only
    # code below.
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        status_coordinator = data.prime_status_coordinator
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
            "room_preferences": _prime_room_preferences(config_entry),
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
            "favourites": {
                "count": len(getattr(data, "prime_favorites", None) or []),
                "buttons_enabled": config_entry.options.get(
                    CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
                ),
                # THE COUNT ALONE CANNOT DISTINGUISH the three causes
                # that produce an empty list, so the raw response goes in
                # beside it. `get_favorites_raw()` now unwraps a wrapped
                # payload and hands back the whole object when there is
                # no `favorites` key -- the outer keys being exactly what
                # is worth seeing.
                "raw": await _prime_favorites_raw(data),
            },
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

    roomba = data.roomba
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
    if data.room_seg_store is not None:
        room_diag.update(data.room_seg_store.diagnostic_info())

    diag: dict[str, Any] = {
        "integration": DOMAIN,
        "version": config_entry.version,
        "title": config_entry.title,

        # Config and options with sensitive values redacted
        "config": async_redact_data(dict(config_entry.data), _CLOUD_REDACT),
        "options": async_redact_data(dict(config_entry.options), _CLOUD_REDACT),

        # Connection state
        "connection": {
            "connected": roomba.roomba_connected,
            "current_state": roomba.current_state,
            "client_error": roomba.client_error,
            "continuous": roomba.continuous,
            "delay": roomba.delay,
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
            "pmap_ids": [
                next(iter(p)) for p in state.get("pmaps", []) if p
            ],
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
        "favourites": {
            "count": len(
                getattr(
                    getattr(config_entry, "runtime_data", None),
                    "prime_favorites",
                    None,
                )
                or []
            ),
            "buttons_enabled": config_entry.options.get(
                CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
            ),
        },

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
