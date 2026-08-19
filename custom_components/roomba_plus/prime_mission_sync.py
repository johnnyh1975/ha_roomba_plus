"""Prime mission history, in the shape MissionStore already holds.

WHY THIS EXISTS.

MissionStore is the basis for mission statistics, anomaly detection
(dirt spikes, excessive recharging), rolling means and standard
deviations, and cleaning-interval tracking. Around 30 sensor lookups
read from it. For Prime robots it has been empty since v4.0.0a0 --
`_async_setup_entry_prime` never created a single store -- while the
data itself was available over REST the whole time.

So this file does not compute anything. It translates
`get_mission_history()` entries into the record shape Classic writes
from MQTT, and every existing consumer works unchanged.

WHERE THE TWO GENERATIONS DIFFER.

Classic accumulates records as missions END, from the MQTT stream: one
append per mission, driven by a phase transition. Prime has no such
event -- the history is a REST endpoint returning the last N missions.

That means this path is a RECONCILIATION, not an append: it has to skip
missions already stored, or every poll would duplicate the entire
history. Classic never needed that because its trigger fires once per
mission by construction.

WHAT IS DELIBERATELY NOT TRANSLATED.

`bbrun_hr`, `battery_cycles`, `npicks_delta`, `error_position_mm`,
`phase_at_error`, `self_recovered` and `zones` have no Prime equivalent.
They are left absent rather than defaulted: a zero would feed the
anomaly detectors real-looking values, and "no data" is not "zero
dirt". Consumers already handle missing keys, since Classic robots vary
in what they report.
"""

from __future__ import annotations

import asyncio
import logging

from roombapy_prime.models.mission_history import parse_mission_history
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

from .structural_failures import record_failure, record_success

_LOGGER = logging.getLogger(__name__)

#: One lock per config entry, guarding the read-then-append sequence.
#:
#: HA's DataUpdateCoordinator does NOT serialise its updates -- verified
#: against the installed version, which has no lock in _async_refresh.
#: So a manual async_request_refresh() during the six-hourly update can
#: overlap it, and this function is not idempotent under overlap:
#:
#:   run A: known = query()   -> {}
#:   run B: known = query()   -> {}      (A has not appended yet)
#:   run A: append(p_x)
#:   run B: append(p_x)       -> duplicate
#:
#: Duplicated missions corrupt exactly what the store exists for: means,
#: standard deviations, cleaning intervals and the anomaly detectors.
#:
#: Keyed by entry_id rather than a module-level lock, so two robots on
#: one Home Assistant do not serialise against each other.
_SYNC_LOCKS: dict[str, asyncio.Lock] = {}

#: Prime `done_code` values, mapped to the result strings MissionStore
#: and its consumers already use. Confirmed from field captures; an
#: unrecognised code becomes "unknown" rather than being guessed at,
#: because "cancelled" and "completed" drive different sensors.
#: THIS TABLE MATCHED NONE OF THE ROBOT'S ACTUAL VALUES, TWICE.
#:
#: @chairstacker (#68, #69): `clean_streak` and `area_cleaned_today`
#: both read 0 on a robot with 128 stored missions. Every one of them
#: mapped to "unknown", which no sensor counts.
#:
#: FIRST VERSION -- invented. `success`, `completed`, `failed`,
#: `aborted`: plausible English words nobody had checked against
#: anything.
#:
#: SECOND VERSION -- derived, and still wrong. Aligned to
#: `roombapy_prime.DoneCode`, whose nineteen values came from bytecode
#: constant names lowercased by analogy with `RegionType`. Only `ok`
#: had ever been confirmed.
#:
#: THIS VERSION -- read out of app 3.0.0. The wire values are
#: **abbreviated camelCase**, and not one snake_case form appears
#: anywhere in the APK:
#:
#:     isMissionHistorySuccess    {ok, busy, dndEnd, returnHomeEnd,
#:                                 timeboxEnd}
#:     isMissionHistoryCancelled  {cncl, usrSlp, plcDoc, usrEnd,
#:                                 usrSpt, batcncl}
#:
#: WORTH KNOWING: the history endpoint takes
#: `supportedDoneCodes=dndEnd,returnHomeEnd`, telling the server which
#: newer codes the caller understands. These eleven are what a caller
#: sending that parameter gets; one that omits it may see others.
#:
#: Anything unmatched still becomes "unknown" rather than being guessed
#: into a bucket -- which is how this stayed invisible for two rounds.
_DONE_CODE_TO_RESULT: dict[str, str] = {
    # SUCCESS, per the app's own `isMissionHistorySuccess`.
    #
    # `busy`, `dndEnd` and `timeboxEnd` count as completed -- a mission
    # ended by quiet hours or a time window did its job. That is the
    # app's judgement, not ours.
    "ok": "completed",
    "busy": "completed",
    "dndEnd": "completed",
    "returnHomeEnd": "completed",
    "timeboxEnd": "completed",

    # CANCELLED, per `isMissionHistoryCancelled`. Note `batcncl` sits
    # here rather than under error: a robot that stopped for battery
    # was cancelled, not broken.
    "cncl": "cancelled",
    "usrSlp": "cancelled",
    "plcDoc": "cancelled",
    "usrEnd": "cancelled",
    "usrSpt": "cancelled",
    "batcncl": "cancelled",
}


def _done_code_key(raw: Any) -> str:
    """The table key for a done code, matching case-exactly first.

    See `_DONE_CODE_TO_RESULT` -- the wire values are abbreviated
    camelCase, so a blanket `.lower()` matched nothing.
    """
    value = str(raw or "")
    if value in _DONE_CODE_TO_RESULT:
        return value
    folded = {k.lower(): k for k in _DONE_CODE_TO_RESULT}
    return folded.get(value.lower(), value)


def _as_iso(value: Any) -> str | None:
    """A timestamp as ISO text, whatever shape it arrived in.

    Prime entries have been seen carrying datetimes, epoch seconds and
    ISO strings across firmware versions, so this normalises rather than
    assuming one.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    return None


def _first_timeline_error(entry: Any) -> Any:
    """The first error code in a mission's timeline, or None.

    First rather than last: the error that ended a mission is the one
    worth recording, and a robot that recovers from one obstacle and
    fails on another is rarer than one that stops at the first.

    Best-effort -- a timeline this cannot read leaves the field as it
    was, which is what it would have been anyway.
    """
    for event in getattr(entry, "timeline", None) or []:
        error = getattr(event, "error", None)
        code = getattr(error, "value", None) if error is not None else None
        if isinstance(code, int):
            return code
    return None


def prime_entry_to_record(entry: Any) -> dict[str, Any] | None:
    """One Prime history entry as a MissionStore record.

    Returns None when the entry cannot be identified or placed in time:
    a record with no id would be re-appended on every poll, and one with
    no end time would corrupt the interval statistics it feeds.
    """
    mission_id = getattr(entry, "mission_id", None)
    ended_at = _as_iso(getattr(entry, "timestamp", None))
    if not mission_id or not ended_at:
        return None

    record: dict[str, Any] = {
        # Prefixed so a Prime record can never collide with a Classic
        # `m_<epoch>` id, which matters if a robot is ever migrated
        # between connection types on the same config entry.
        "id": f"p_{mission_id}",
        "ended_at": ended_at,
        "result": _DONE_CODE_TO_RESULT.get(
            # NOT lowercased -- the wire values are camelCase
            # (`returnHomeEnd`, `usrSlp`, `batcncl`), and folding the
            # case turned every one of them into "unknown". The old
            # table was all-lowercase, so the fold was invisible.
            #
            # An exact match first, then a case-insensitive fallback:
            # `ok` is confirmed lowercase and a robot sending `OK`
            # should still count.
            _done_code_key(getattr(entry, "done_code", None)), "unknown"
        ),
    }

    # `started_at` FALLS BACK TO THE END TIME.
    #
    # Several sensors bucket missions by day using started_at rather
    # than ended_at -- area_cleaned_today among them. A record without
    # it is invisible to them, silently: no error, just a zero that
    # looks like a real measurement of nothing.
    #
    # Falling back to the end time is wrong by at most the mission's own
    # duration, which matters only for a mission spanning midnight.
    # A wrong day for one mission an evening beats a permanent zero.
    started_at = _as_iso(getattr(entry, "start_time", None))
    record["started_at"] = started_at or ended_at

    # Each mapped one-for-one, and only when actually present. A missing
    # value stays missing: the anomaly detectors treat a zero as a real
    # measurement, and "no dirt data" is not "no dirt".
    for target, source in (
        ("duration_min", "duration_m"),
        ("area_sqft", "square_feet_covered"),
        ("dirt", "number_of_dirt_detects"),
        ("recharge_min", "minutes_charging"),
        ("error_code", "error_code"),
    ):
        value = getattr(entry, source, None)
        if target == "error_code" and value is None:
            # THE PER-MISSION FIELD IS EMPTY AND THE TIMELINE IS NOT.
            #
            # @utkjmitch's 49-mission archive: `error_code` None on all
            # 49 records, on a robot with 16 missions ending `stuck` --
            # while the timelines carry 111 error events, **93 of them
            # error 48**, the blocked-entrance code.
            #
            # So a mission that plainly failed reported no error at
            # mission level. Taking the first timeline error gives the
            # record the code the robot actually raised.
            value = _first_timeline_error(entry)
        if value is not None:
            record[target] = value

    # Prime-only fields, kept because they are genuinely useful and cost
    # nothing: MissionStore stores records as opaque dicts.
    for target, source in (
        ("paused_min", "minutes_paused"),
        ("evacuations", "number_of_evacuations"),
        ("docked_at_start", "docked_at_start"),
        ("ended_on_dock", "ended_on_dock"),
        ("coverage_strategy", "coverage_strategy"),
    ):
        value = getattr(entry, source, None)
        if value is not None:
            record[target] = value

    # `command` is the closest thing Prime has to Classic's `initiator`,
    # which the schedule sensors read. Not renamed silently -- kept under
    # the Classic key because that is what consumers look for, and noted
    # here so the mapping is findable.
    command = getattr(entry, "command", None)
    if command:
        record["initiator"] = str(command)

    # MEASURED per-room durations, from the mission's own timeline.
    #
    # This is what Prime has instead of the cloud time estimates Classic
    # reads from regions[*].time_estimates. Prime's cloud supplies none:
    # not in RoomFeatureProperties, not in room metadata, and the one
    # endpoint that would (`/v1/time-estimates`) builds its request body
    # in native code, so its key names are not determinable.
    #
    # Measuring is arguably better than predicting -- it is this robot in
    # this home rather than a model value -- at the cost of needing a
    # room cleaned once before there is anything to say.
    per_room = _room_durations(getattr(entry, "timeline", None))
    if per_room:
        record["room_durations_sec"] = per_room

    return record


def _room_durations(timeline: Any) -> dict[str, float]:
    """{region_id: seconds} from a mission timeline.

    A room event carries region_id; the event around it carries
    start_time and end_time. Rooms visited more than once in a mission
    accumulate rather than overwrite -- a robot that leaves a room to
    empty its bin and comes back spent the sum of both visits there.
    """
    durations: dict[str, float] = {}
    for event in timeline or []:
        room = getattr(event, "room", None)
        region_id = getattr(room, "region_id", None) if room else None
        if not region_id:
            continue
        start, end = getattr(event, "start_time", None), getattr(event, "end_time", None)
        if start is None or end is None:
            continue
        try:
            seconds = (end - start).total_seconds()
        except (TypeError, AttributeError):
            continue
        # Guard against clock skew and unfinished events: a negative or
        # implausibly long room visit would poison the average it feeds.
        if seconds <= 0 or seconds > 4 * 3600:
            continue
        durations[str(region_id)] = durations.get(str(region_id), 0.0) + seconds
    return durations


def _parsed(history: Any) -> list[Any]:
    """The history as typed entries, whatever came back.

    Already-typed entries pass through untouched. A caller that has done
    its own parsing -- and every test in this file -- must not have the
    work undone, and a second parse of a dataclass produces nothing at
    all.
    """
    items = list(history or [])
    if items and not isinstance(items[0], dict):
        return items
    return list(parse_mission_history(history) or [])


async def async_sync_prime_missions(config_entry: RoombaConfigEntry) -> int:
    """Brings MissionStore up to date from Prime's mission history.

    Returns the number of records added, for logging and diagnostics.

    Reconciles rather than appends: the REST endpoint returns the whole
    recent history on every call, so appending blindly would duplicate
    it. Classic never faced this because its trigger fires once per
    mission.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    store = getattr(data, "mission_store", None)
    if robot is None or store is None:
        return 0

    lock = _SYNC_LOCKS.setdefault(config_entry.entry_id, asyncio.Lock())
    async with lock:
        return await _async_sync_locked(config_entry, robot, store)


async def _async_sync_locked(
    config_entry: RoombaConfigEntry, robot: Any, store: Any
) -> int:
    """The read-then-append sequence, holding the entry's lock.

    `config_entry` is threaded through rather than dropped: extracting
    this body left the profile update below reaching for a name that no
    longer existed in scope. Ruff caught it; nothing at runtime would
    have until the first sync actually added a record.

    That is the same shape as three other mistakes today -- a change
    applied to one layer and not the next.
    """
    try:
        # THE BLID IS REQUIRED, and this call has never supplied it.
        # The comment below documents the previous life of the same bug
        # -- a required `days` argument that made every sync fail
        # silently since the feature shipped. Fixing that one moved the
        # TypeError up a line rather than ending it (@utkjmitch).
        # THE PARSE STEP WAS MISSING, and that is why 46 missions
        # imported as 0 (@utkjmitch, Y351020).
        #
        # `get_mission_history()` returns the RAW response by design --
        # its own docstring calls conversion "a separate, optional step".
        # Nothing here took that step, so this iterated plain dicts and
        # asked them for attributes: `getattr(dict, "mission_id", None)`
        # is None on every entry, so every entry converted to None and
        # every one was dropped.
        #
        # SILENTLY, because dropping id-less entries is the correct
        # handling of a malformed record. The data was not malformed; it
        # was unparsed.
        #
        # THE THIRD LIFE OF THIS CALL SITE: a wrong `days` argument,
        # then a missing `blid`, now a missing parse. Each fix moved the
        # failure one layer deeper -- and no test covered the path,
        # which is how.
        #
        # The structural-failure tracker cannot see this one, through no
        # fault of its own: `record_success` fires after the fetch,
        # which did succeed. The loss happens past the exception
        # boundary, in data nothing watches.
        history = _parsed(await robot.get_mission_history(robot.blid))
        # THE SUCCESS SIDE MATTERS AS MUCH AS THE FAILURE SIDE. Without
        # it, a path that works would still be reported as structurally
        # broken the first two times a cloud call happened to time out.
        record_success("mission history import")
    except Exception:  # noqa: BLE001
        # A missing argument here made this fail on every call for weeks
        # while reporting "imported 0 missions". Now it says so.
        record_failure("mission history import", "reading the history")
        _LOGGER.debug("roomba_plus: could not read mission history", exc_info=True)
        return 0

    # `days` is REQUIRED, and omitting it raised TypeError before the
    # first record was ever compared. Every sync since the feature
    # shipped failed here, silently: the caller catches broadly and logs
    # at debug level, so nothing ever surfaced.
    #
    # A wide window on purpose -- this is a duplicate check, and a
    # mission older than the window would be re-added on every run.
    known = {rec.get("id") for rec in store.query(days=3650)}
    added = 0
    # Oldest first, so the store's own ordering assumptions and any
    # rolling statistics see missions in the order they happened.
    for entry in sorted(
        history or [],
        key=lambda e: _as_iso(getattr(e, "timestamp", None)) or "",
    ):
        record = prime_entry_to_record(entry)
        if record is None or record["id"] in known:
            continue
        await store.async_append(record)
        known.add(record["id"])
        added += 1

    # SAVED ONCE, AFTER THE LOOP.
    #
    # async_append() only mutates memory -- Classic follows every append
    # with its own async_save(), and this path had none at all. Without
    # it the whole sync was lost on restart and re-fetched from REST
    # every time, which happened to look like it worked because the
    # endpoint returns the same history again.
    #
    # It would have shown up as mission statistics resetting on every
    # Home Assistant restart, and only for records older than the
    # endpoint's window.
    #
    # Once rather than per record: a backfill of a hundred missions on
    # first run would otherwise be a hundred disk writes.
    if added:
        # BACKFILL STATISTICS HERE TOO, not only at setup.
        #
        # Setup starts the backfill before this sync has ever run, so on
        # a FIRST install the store is empty at that moment and the
        # long-term statistics stay blank until the next Home Assistant
        # restart. Running it again after records arrive closes that gap.
        #
        # Safe to repeat: async_add_external_statistics is idempotent, so
        # re-injecting the same points overwrites rather than duplicates.
        try:
            hass = _hass_of(config_entry)
            hass.async_create_task(
                store.async_backfill_statistics(
                    hass, config_entry.entry_id, config_entry.title or "Roomba"
                ),
                name="roomba_plus_prime_statistics_backfill_after_sync",
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "roomba_plus: statistics backfill after sync failed", exc_info=True
            )

        try:
            await store.async_save(_hass_of(config_entry), config_entry.entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "roomba_plus: could not persist %d new mission record(s); they "
                "will be re-read from the cloud next time",
                added, exc_info=True,
            )

    if added:
        _LOGGER.debug("roomba_plus: added %d mission record(s) from Prime history", added)
        await _async_update_profile(config_entry, store)
    return added


def _hass_of(config_entry: RoombaConfigEntry) -> Any:
    """The HomeAssistant instance, however this entry exposes it.

    ONLY from runtime_data. `config_entry.hass` does not exist --
    ConfigEntry carries no such attribute, and reading it raises
    AttributeError.

    Returns None when runtime_data has no reference, and every caller
    handles that: persisting is enrichment, and a sync that cannot write
    its result still leaves the records in memory for the next attempt.
    """
    runtime = getattr(config_entry, "runtime_data", None)
    return getattr(runtime, "hass_ref", None)


async def _async_update_profile(config_entry: RoombaConfigEntry, store: Any) -> None:
    """Recomputes rolling mission statistics after new records arrive.

    Same 30-day window Classic uses, and only when something was added:
    the statistics cannot move without new missions, so recomputing on
    every poll would write the store to disk for nothing.

    RobotProfileStore needs at least five missions before it produces
    means at all, so on a fresh install this quietly does nothing for
    the first few days -- which is correct rather than a gap. A mean of
    two missions is not a baseline, and the anomaly detectors that read
    it would flag ordinary variation.

    Only mission statistics are updated here. update_coverage_baseline,
    update_room_dirt_index and finalize_correlation need per-room dirt
    and zone data that Prime does not report, and calling them with
    nothing would establish an empty baseline that later real data would
    be compared against.
    """
    profile = getattr(config_entry.runtime_data, "robot_profile_store", None)
    if profile is None:
        return
    try:
        if profile.update_mission_stats(store.query(days=30)):
            await profile.async_save(
                _hass_of(config_entry),
                config_entry.entry_id,
            )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: profile statistics update failed", exc_info=True)


def estimate_room_seconds(
    store: Any, room_ids: list[str], *, max_missions: int = 10
) -> list[float | None]:
    """Expected seconds per room, from what past missions actually took.

    Returns one entry per requested room, None where that room has never
    been cleaned. Callers pass the list straight to
    MissionTimerStore.set_mission_plan(room_estimates_sec=...), which
    already accepts None per room -- a partially known plan is normal
    for Classic too, where a newly named room has no cloud estimate yet.

    MEDIAN, NOT MEAN, and over a bounded window. One mission where the
    robot got stuck in a doorway for forty minutes would drag a mean
    permanently; a median ignores it. The window keeps the estimate
    following reality after furniture moves, rather than averaging over
    a layout that no longer exists.

    A single past mission is enough to produce an estimate. It is a poor
    one, and it beats no progress indication at all -- which is what
    this replaces.
    """
    if store is None or not room_ids:
        return [None] * len(room_ids)

    try:
        records = store.query(days=3650)
        record_success("room time estimates")
    except Exception:  # noqa: BLE001
        # A missing argument here made this fail on every call for weeks
        # while reporting "imported 0 missions". Now it says so.
        record_failure("room time estimates", "reading estimates")
        _LOGGER.debug("roomba_plus: could not read mission history", exc_info=True)
        return [None] * len(room_ids)

    samples: dict[str, list[float]] = {}
    # Newest first, so the window is the most recent N missions that
    # actually mention the room rather than the most recent N overall.
    for record in reversed(records or []):
        for region_id, seconds in (record.get("room_durations_sec") or {}).items():
            bucket = samples.setdefault(str(region_id), [])
            if len(bucket) < max_missions:
                bucket.append(float(seconds))

    estimates: list[float | None] = []
    for room_id in room_ids:
        values = sorted(samples.get(str(room_id), []))
        if not values:
            estimates.append(None)
            continue
        middle = len(values) // 2
        estimates.append(
            values[middle]
            if len(values) % 2
            else (values[middle - 1] + values[middle]) / 2
        )
    return estimates
