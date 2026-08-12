"""Image platform for Roomba+ — live cleaning map as ImageEntity.

ImageEntity is the correct HA platform for periodically-updated still images.
Unlike Camera, it renders inline in the frontend popup without streaming.

Key behaviour per HA ImageEntity docs:
  - async_image() returns bytes on demand (called by frontend)
  - image_last_updated must be bumped when new image data is available
  - Frontend re-fetches async_image() whenever state changes
  - access_tokens deque must be initialized and async_update_token() called
    once hass is available (in async_added_to_hass)

Mission lifecycle:
  Phase 'run'         -> MapRenderer.reset(), accumulate pose points
  Pose updates        -> MapRenderer.add_pose(), bump image_last_updated
  bbrun.nStuck rises  -> MapRenderer.mark_stuck()
  Phase 'charge' etc  -> ZoneStore.process_mission() (EPHEMERAL only)
                      -> renderer.dump_state() saved to hass.storage

Persistence:
  After every mission end the renderer state (pose points, stuck positions,
  heading) is written to hass.storage under the key
  'roomba_plus_map_{entry_id}'. On async_added_to_hass the stored state
  is restored so the last mission's map survives an HA restart.

  The cached PNG is not stored — it is re-rendered from the persisted points
  on the first async_image() call, which takes <5 ms.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import collections
import datetime
import io
import logging
import math
import time as _time_mod
from datetime import datetime as dt_datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import roomba_reported_state
from .const import (
    CONF_MAP_CLEAN_ZONES,
    CONF_MAP_KEEPOUT_ZONES,
    CONF_MAP_NOMOP_ZONES,
    DEFAULT_MAP_ZONES,
    room_slug,
    CONF_MAP_ROOM_LABELS,
    DEFAULT_MAP_ROOM_LABELS,
    CLEANING_PHASES,
    DOMAIN,
    END_SIGNAL_DEBOUNCE_COUNT,
    END_SIGNAL_MIN_HOLD_SECONDS,
    GAP_THRESHOLD_MM,
    MAX_DOOR_WIDTH_MM,
    MIN_DOOR_WIDTH_MM,
    MISSION_END_PHASES,
    POSE_POINT_CM_TO_MM,
    REGION_TYPE_ICONS,
    ROOM_TRANSITION_CANDIDATE_PHASES,
)
from .entity import IRobotEntity
from .structural_failures import record_failure, record_success
from .grid_store import GridStore, CELL_SIZE_MM, DECAY, VISIT_INCREMENT
from .map_renderer import MapRenderer
from .models import ConnectionType, MapCapability, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: How many live positions to keep for the trail.
#:
#: A tester's single mission produced 904 points, so this holds several
#: missions' worth. The cap exists for the robot left running all day,
#: not for the normal case.
_MAX_PRIME_POSITIONS = 5000

#: Dispatcher signals for the two independent live-map message shapes.
#:
#: The two Prime image entities are split by what they can do: one
#: watches the map stream and shows iRobot's rendered PNG, the other
#: draws its own map and never sees the stream. A dispatcher signal is
#: how the second learns that the first has new data.
#:
#: Contributed by @jouwdan (PR #63), who found that MapUpdateMessage
#: carries a SECOND url -- `livemap_url` alongside `livemap_url_raw` --
#: and that it returns a full map bundle rather than the raw occupancy
#: grid. We had been consuming only the raw one.
_SIGNAL_PRIME_LIVE_BUNDLE = "roomba_plus_prime_live_bundle_{}"
_SIGNAL_PRIME_LIVE_POSITION = "roomba_plus_prime_live_position_{}"
_LIVE_MAP_STATE_UPDATE_INTERVAL = 2.0
PARALLEL_UPDATES = 0

# ROOM-PALETTE (v2.9.0) — rotating per-room fill colours for _render_rooms_png().
# Muted/desaturated tones chosen to read clearly against the dark (30,30,30)
# canvas background while staying visually distinct from each other and from
# the fixed outline colour (100,149,237). 8 entries — rotates via index % 8.
ROOM_FILL_PALETTE: list[tuple[int, int, int]] = [
    (61, 74, 94),    # slate blue   (close to the old single uniform fill)
    (74, 94, 61),    # olive green
    (94, 61, 74),    # muted maroon
    (94, 86, 61),    # warm ochre
    (61, 94, 91),    # teal
    (86, 61, 94),    # muted purple
    (94, 75, 61),    # burnt orange
    (61, 79, 94),    # steel blue
]

# CLEANING_PHASES and MISSION_END_PHASES moved to const.py (v2.3.0 Step 1)

_MAP_STORAGE_VERSION = 1

# v2.8.2 — mission-in-progress checkpoint. Separate storage key/version from
# _MAP_STORAGE_VERSION (the renderer's "last completed mission" snapshot)
# because this one represents a possibly-incomplete, still-in-flight mission
# and has a different lifecycle: written on every stuck event, consumed
# (resumed or salvaged) exactly once on the first MQTT message after
# startup, and deleted once a mission reaches a normal end. See
# RoombaMapImage._consume_pending_checkpoint() / _salvage_orphaned_checkpoint().
_MISSION_CHECKPOINT_STORAGE_VERSION = 1


def _mission_checkpoint_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_checkpoint_{entry_id}"


# v2.6.3 E — dispatcher signal fired by RoombaMapImage after GridStore update.
# RoombaCoverageImage listens to bump image_last_updated so the frontend re-fetches.
_SIGNAL_COVERAGE_UPDATED = "roomba_plus_coverage_updated_{}"


async def _async_send_coverage_signal(hass: HomeAssistant, entry_id: str) -> None:
    """Fire the coverage-updated dispatcher signal on the HA event loop."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(hass, _SIGNAL_COVERAGE_UPDATED.format(entry_id))


#: Long enough to absorb a mission's worth of frames, short enough that
#: a map is on disk well before anyone restarts deliberately.
_MAP_SAVE_DELAY_SECONDS = 60

def _prime_map_storage_key(entry_id: str) -> str:
    """Separate from the Classic key: different contents entirely (a PNG
    rather than renderer state), and a robot never switches generation,
    so the two can never collide."""
    return f"{DOMAIN}_prime_map_{entry_id}"


def _map_storage_key(entry_id: str) -> str:
    return f"roomba_plus_map_{entry_id}"




async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up map image entities — only if robot reports pose data."""
    data = config_entry.runtime_data

    # NEW (V4/Prime): entirely separate path -- CLOUD_ONLY entries have no
    # roombapy Roomba object, no MapCapability tier, and their live map
    # comes from a completely different source (watch_live_map()'s
    # MapUpdateMessage, downloaded + decoded via decode_rawmap_to_png()),
    # not local MQTT pose data. Deliberately its own class rather than
    # threading CLOUD_ONLY branches through the Classic-oriented
    # RoombaMapImage below, which is built around pose points, mission
    # tracking, and dock-anchor correction that don't apply here at all.
    if data.connection_type is ConnectionType.CLOUD_ONLY:
        if data.prime_robot is not None:
            # TWO ENTITIES, not one replacing the other.
            #
            # PrimeMapImage shows iRobot's own rendered PNG -- where the
            # robot ACTUALLY CLEANED. PrimeRoomsImage shows the floor
            # plan with room outlines and, via attributes, room names.
            # Different information, and Classic carries three map
            # entities for the same reason.
            #
            # Replacing the PNG would also lose it before the trajectory
            # half exists: the live-map stream carries position samples
            # too, but nobody has yet counted how many arrive, and the
            # Classic renderer rejects poses more than 500 mm apart.
            async_add_entities([
                PrimeMapImage(
                    prime_robot=data.prime_robot,
                    blid=data.blid,
                    config_entry=config_entry,
                ),
                PrimeRoomsImage(
                    blid=data.blid, config_entry=config_entry, hass=hass
                ),
            ])
        return

    if data.map_capability == MapCapability.NONE:
        _LOGGER.debug("Roomba+ image: skipped — no pose capability")
        return

    entities: list[Any] = [
        RoombaMapImage(
            roomba=data.roomba,
            blid=data.blid,
            renderer=data.renderer,
            map_capability=data.map_capability,
            config_entry=config_entry,
        )
    ]

    # F9 — coverage heatmap entity (all pose-capable robots with GridStore)
    if data.grid_store is not None:
        entities.append(
            RoombaCoverageImage(
                roomba=data.roomba,
                blid=data.blid,
                grid_store=data.grid_store,
                config_entry=config_entry,
            )
        )

    # v2.3.2 — room layout entity for xiaomi-vacuum-map-card.
    # Extended from SMART-only to include EPHEMERAL when UmfAligner is present:
    # 900-series robots (e.g. 980) have cloud UMF geometry and a functioning
    # aligner but were excluded by the SMART gate despite having all required data.
    if data.map_capability == MapCapability.SMART or (
        data.map_capability == MapCapability.EPHEMERAL
        and data.umf_aligner is not None
    ):
        entities.append(
            RoombaRoomsImage(
                roomba=data.roomba,
                blid=data.blid,
                config_entry=config_entry,
            )
        )

    async_add_entities(entities)


def _check_dock_drift(final_position_mm: tuple[float, float]) -> tuple[float, float]:
    """Detect coordinate drift by comparing final position to dock origin.

    The Roomba always returns to the dock (0,0) after a successful mission.
    If final_position_mm significantly differs from origin, this is drift.
    Returns a (dx, dy) correction offset; (0,0) if within threshold.

    ROOM-SEG Stage 6 — relocated from ZoneStore.check_dock_drift() (deleted
    along with the rest of ZoneStore). Always was a pure function of its
    one argument with no dependency on ZoneStore's own state; only ever
    called from this module, so it lives here now rather than anywhere
    that needs a ZoneStore instance just to reach it.
    """
    dx, dy = final_position_mm
    threshold = 300.0  # mm — 30 cm drift is detectable
    if abs(dx) > threshold or abs(dy) > threshold:
        _LOGGER.debug(
            "Map: dock drift detected — final pos (%.0f, %.0f), threshold %.0f mm",
            dx, dy, threshold,
        )
        return (-dx, -dy)
    return (0.0, 0.0)


def _compute_dock_correction(
    measured_final_pos: tuple[float, float],
    measured_final_theta: float,
    dock_theta_baseline: float | None,
) -> tuple[float, float, float]:
    """v3.2.1 DOCK-ANCHOR — compute the (dx, dy, rotation_rad) correction
    that maps measured_final_pos/theta onto the known-true dock state
    (position always (0,0); heading dock_theta_baseline if available).

    Automatic v1→v2 upgrade, no manual switch: dock_theta_baseline is
    None until RobotProfileStore.dock_theta_baseline_ready — until then
    this returns rotation_rad=0.0 (pure translation, same as the
    existing _check_dock_drift, just restructured to also carry a
    rotation component once available). See Dock_Anchor_Korrektur_Plan.md
    for why rotation cannot safely start from an unvalidated first
    theta observation.
    """
    dx, dy = -measured_final_pos[0], -measured_final_pos[1]
    if dock_theta_baseline is None:
        return (dx, dy, 0.0)
    rotation_rad = math.radians(dock_theta_baseline - measured_final_theta)
    # Rotation is applied to segment points *before* translation elsewhere
    # (see _apply_dock_correction) — the translation component here must
    # be computed against the ALREADY-ROTATED final position, not the raw
    # measured one, or the two corrections would fight each other.
    cos_r, sin_r = math.cos(rotation_rad), math.sin(rotation_rad)
    mx, my = measured_final_pos
    rotated_x = mx * cos_r - my * sin_r
    rotated_y = mx * sin_r + my * cos_r
    return (-rotated_x, -rotated_y, rotation_rad)


def _apply_dock_correction(
    point: tuple[float, float], dx: float, dy: float, rotation_rad: float,
) -> tuple[float, float]:
    """Apply one (dx, dy, rotation_rad) correction to a single point —
    rotate around the origin first, then translate. Order matters: see
    _compute_dock_correction's docstring."""
    x, y = point
    if rotation_rad:
        cos_r, sin_r = math.cos(rotation_rad), math.sin(rotation_rad)
        x, y = x * cos_r - y * sin_r, x * sin_r + y * cos_r
    return (x + dx, y + dy)


def _interpolate_and_correct_segment(
    points: list[tuple[float, float]],
    dx: float, dy: float, rotation_rad: float,
) -> list[tuple[float, float]]:
    """v3.2.1 DOCK-ANCHOR (4c) — distribute a dock-verified correction
    proportionally across a buffered segment instead of applying it
    uniformly or only to the last point.

    Rationale: drift accumulated since a stuck event is assumed to grow
    gradually (odometry/vSLAM error compounding over time), not appear
    in one jump right before the dock — so weight 0 at the FIRST
    buffered point (still anchored to the last trusted pre-stuck
    position) growing linearly to weight 1 (the full measured
    correction) at the LAST buffered point (right before dock contact).

    Internal accepted jumps within the segment (see MapRenderer.add_pose
    return value) are intentionally NOT treated as separate interpolation
    breakpoints in this first version — see Dock_Anchor_Korrektur_Plan.md
    4c: confidence-weighting for jump-adjacent sub-segments was
    deliberately deferred pending real field validation, not implemented
    speculatively ahead of evidence that simple linear interpolation
    isn't good enough.
    """
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [_apply_dock_correction(points[0], dx, dy, rotation_rad)]
    out = []
    for i, p in enumerate(points):
        weight = i / (n - 1)
        out.append(_apply_dock_correction(p, dx * weight, dy * weight, rotation_rad * weight))
    return out


class PrimeMapImage(IRobotEntity, ImageEntity):
    """Live cleaning map for V4/Prime (CLOUD_ONLY) robots.

    Deliberately minimal, separate from RoombaMapImage below: this
    consumes an entirely different data source (roombapy-prime's
    watch_live_map(), a cloud MQTT topic, not local pose messages) and
    needs none of RoombaMapImage's mission-tracking/dock-anchor/
    coverage-overlay machinery, since the "rawmap" occupancy grid IS
    the map directly -- there's no separate pose-trail to render on
    top of it (yet; see decode_rawmap_to_png()'s own docstring for
    the confirmed evidence trail behind this format and
    models/livemap.py's MapUpdateMessage for why no new download/
    parsing code was needed to consume this live feed).

    A MapUpdateMessage carries a presigned URL (livemap_url_raw), not
    the image bytes directly -- one extra download step per update,
    same pattern already used for the REST-fetched map bundle.
    """

    _attr_translation_key = "map"
    _attr_entity_category = None
    _attr_content_type = "image/png"

    def __init__(self, prime_robot: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(self, roomba=None, blid=blid, config_entry=config_entry)
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)
        self._prime_robot = prime_robot
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map"
        self._png_bytes: bytes | None = None
        self._map_stored_at: str | None = None
        self._map_store: Store | None = None
        self._watch_task: asyncio.Task[None] | None = None
        # NEW (this session): live-map decode statistics, kept on the
        # CONFIG ENTRY rather than on this entity, so diagnostics can
        # read them without having to locate the entity instance. Born
        # from a real field report where the map stayed blank while data
        # was arriving and failing to decode 106 times an hour -- the
        # counters make that visible in one glance instead of requiring
        # someone to scrape their log.
        # TWO INDEPENDENT STREAMS, and the names invite reading them as
        # one. A field report compared them directly and asked whether
        # two thirds of the messages were being coalesced away
        # (@utkjmitch: 139 messages against 46 updates, ~2.5s against
        # ~8s). They are not comparable at all:
        #
        #   position_messages  pose packets off the MQTT stream --
        #                      these drive the robot marker and trail
        #   position_points    poses inside them; one message can
        #                      carry several
        #   updates_received   HTTP downloads of the live-map BUNDLE --
        #                      coverage, trajectories, hazards
        #
        # So a gap between the two is a difference in cadence between
        # two channels, not loss. decode_ok tracks updates_received,
        # not position_messages: only the bundle is decoded.
        #
        # RESET ON EVERY RELOAD, because they live on runtime_data.
        # Worth knowing before asking anyone for a capture: three
        # testers reported all-zero counters and every one of those
        # files had been pulled after a reload.
        config_entry.runtime_data.live_map_stats = {
            "updates_received": 0,
            "position_messages": 0,
            "position_points": 0,
            #: What SURVIVED into prime_positions, against the two ways
            #: a sample can be dropped on the way. position_points minus
            #: trail_points_added is exactly the two skip counters.
            "trail_points_added": 0,
            "trail_skipped_no_point": 0,
            "trail_skipped_no_xy": 0,
            #: The OUTER retry loop's own failures. Separate from
            #: decode_failed, which only covers a payload that arrived
            #: and could not be read -- these are the cases where
            #: nothing arrived at all, and they used to be invisible in
            #: a diagnostics download.
            "watch_failures": 0,
            "last_watch_error": None,
            "watch_retry_backoff_s": None,
            "decode_ok": 0,
            "decode_failed": 0,
            "last_error": None,
            "last_payload_prefix_hex": None,
        }
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        await self._async_restore_png()
        self.async_update_token()
        # CONSISTENCY FIX (this session): was a bare asyncio.create_task()
        # here -- every OTHER background task in this project (both
        # coordinators) uses config_entry.async_create_background_task()
        # instead, which ties the task's lifetime to the config entry
        # itself (auto-cancelled on unload/reload by HA's own framework,
        # not just by this entity's own async_will_remove_from_hass()
        # below -- kept as a second, redundant cancellation path, not
        # removed, since it doesn't hurt to have both).
        self._watch_task = self._config_entry.async_create_background_task(
            self.hass,
            self._async_watch_live_map(),
            name=f"roomba_plus_prime_live_map_{self._blid}",
        )

    def _feed_trail(self, message: Any) -> None:
        """Feeds live positions into the trail renderer.

        UNITS ARE THE WHOLE TRAP HERE, and the comment above this call
        site warned about it before anything used them: Prime reports
        METRES -- a real keep-out zone measures 2.0 by 2.0 -- while
        MapRenderer.add_pose takes millimetres. Feeding metres straight
        in puts every point inside the same pixel and produces a blank
        map with no error anywhere.

        ORIENTATION IS IN RADIANS on the wire and add_pose wants degrees.
        Same class of mistake, quieter symptom: the trail would be drawn
        correctly and only the heading marker would point wrongly.

        Volume is not a concern. A tester's mission produced 904 points
        across 451 messages -- roughly two per message -- and the
        renderer already drops jumps over 500 mm as noise, which is what
        keeps a relocalisation from drawing a line across the room.
        """
        from .prime_room_map import METRES_TO_MM  # noqa: PLC0415

        # COLLECTED, NOT DRAWN HERE. This entity shows iRobot's own
        # rendered PNG and has no renderer of its own; the trail belongs
        # on the rooms map, which does. A first version called
        # self._renderer here -- which is None on this class, so every
        # point was silently discarded.
        # COUNTED WHERE IT IS DECIDED, not where the message arrives.
        #
        # `position_points` above counts len(message.updates) -- what the
        # robot sent. Everything below can still drop every one of them:
        # a sample without a `point`, or a point without x/y, is skipped
        # silently. So 5553 points counted proves nothing about whether
        # a single one reached the list the trail and the robot marker
        # are drawn from.
        #
        # @DaRealGuGu hit exactly that gap: 2267 messages, 5553 points,
        # and no marker on the map -- and no number in the capture could
        # say whether they arrived and were dropped here, or arrived and
        # were drawn somewhere he could not see.
        #
        # Same shape as every other counter mistake in this project:
        # counting arrival instead of survival.
        data = self._config_entry.runtime_data
        stats = data.live_map_stats
        try:
            for sample in getattr(message, "updates", None) or []:
                # A TUPLE, NOT AN OBJECT WITH .x AND .y.
                #
                # This read `getattr(point, "x", None)` and skipped every
                # sample when it came back None -- which it always did,
                # because PositionSample.point is `(x, y)`. So the trail
                # list stayed empty on every robot, forever, while
                # `position_points` counted the samples arriving.
                #
                # That is the whole missing-robot-marker story:
                # @DaRealGuGu's capture showed 2267 messages and 5553
                # points with no marker on the map, and the marker is
                # only drawn when this list is non-empty. What he saw
                # instead was the trajectories layer from the downloaded
                # bundle, which looks like a trail and is not one.
                #
                # Both getattr() calls were defensive code written
                # against a shape nobody had checked. The defensiveness
                # is what hid it: an exception would have been found in a
                # day.
                point = getattr(sample, "point", None)
                if point is None:
                    stats["trail_skipped_no_point"] = (
                        stats.get("trail_skipped_no_point", 0) + 1
                    )
                    continue
                try:
                    x_m, y_m = point
                except (TypeError, ValueError):
                    stats["trail_skipped_no_xy"] = (
                        stats.get("trail_skipped_no_xy", 0) + 1
                    )
                    continue
                if x_m is None or y_m is None:
                    stats["trail_skipped_no_xy"] = (
                        stats.get("trail_skipped_no_xy", 0) + 1
                    )
                    continue
                orientation_rad = getattr(sample, "orientation", None) or 0.0
                stats["trail_points_added"] = stats.get("trail_points_added", 0) + 1
                record_success("live trail points")
                data.prime_positions.append((
                    float(x_m) * METRES_TO_MM,
                    float(y_m) * METRES_TO_MM,
                    math.degrees(float(orientation_rad)),
                ))
            # Bounded. A mission produced 904 points on one tester's
            # robot; a robot left running for a day would otherwise grow
            # this without limit. The oldest go first, because a trail
            # showing the last stretch is more useful than one showing
            # the first.
            if len(data.prime_positions) > _MAX_PRIME_POSITIONS:
                del data.prime_positions[:-_MAX_PRIME_POSITIONS]
        except Exception:  # noqa: BLE001
            record_failure("live trail points", "appending a position")
            _LOGGER.debug("Prime map: could not add live positions", exc_info=True)

    async def async_will_remove_from_hass(self) -> None:
        # CANCEL AND THEN WAIT. `cancel()` alone only REQUESTS a stop --
        # it returns before the task has unwound, so its cleanup may
        # still be pending when the next line runs.
        #
        # What is pending here is the livemap unsubscribe, in the
        # `finally` of `watch_live_map()`. Leave it unfinished and the
        # broker keeps the old subscription while a reload builds a new
        # one, and `async_unload_entry` disconnects the robot underneath
        # both of them.
        #
        # Two testers saw a reload leave BOTH irbt-topic streams silent
        # -- live map and mission timeline together -- while shadow
        # traffic kept flowing, and a full Home Assistant restart cleared
        # it. A restart closes the event loop, which finalises async
        # generators; a reload does not.
        #
        # Whether that is the whole story is unproven. Cancelling
        # without waiting is wrong on its own.
        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task

        # FLUSH THE DELAYED MAP WRITE.
        #
        # The map is persisted via async_delay_save, so a reload can
        # leave the OLD entity's pending write to land after the NEW one
        # has already loaded -- overwriting a current map with a stale
        # one. Store.async_save cancels the pending timer as well as
        # writing, which is what makes this both a flush and a guard.
        #
        # Exactly the problem Classic solved for MissionTimerStore in
        # v3.3.0. This entity was written today, with delayed saving
        # copied from that store, and the flush was not copied with it.
        if self._map_store is not None and self._png_bytes:
            try:
                await self._map_store.async_save(self._map_save_payload())
            except Exception:  # noqa: BLE001
                # NOT INSTRUMENTED: runs once when the entity is removed,
                # where a failure has no next attempt to be compared
                # against and nothing left to affect.
                _LOGGER.debug("Prime map: flush on removal failed", exc_info=True)

    async def _async_watch_live_map(self) -> None:
        """Runs for the entity's lifetime -- watch_live_map() itself
        reconnects transparently across drops (same reconnect-hardened
        _watch_topic() engine as watch_mission_timeline(), see
        prime_coordinator.py's own docstring), so this loop only needs
        to react to messages, not to handle connection loss itself.

        DEFENSIVE OUTER RETRY LOOP (added alongside the identical fix
        already made in PrimeCoordinator._async_watch_mission_timeline()
        and PrimeStatusCoordinator._async_watch_status_updates() --
        this is the THIRD occurrence of the exact same gap, found only
        because this entity had NEVER actually run before this session
        [Platform.IMAGE was missing from PRIME_PLATFORMS the whole
        time, see const.py's own docstring], so the bug had no chance
        to surface via real usage the way the other two did. A single
        unexpected exception here -- even though watch_live_map() is
        "designed to retry forever internally" per its own docstring --
        would previously have ended this task PERMANENTLY for the rest
        of the session, silently freezing the live map at whatever the
        last-received frame happened to be. Now degrades to "retry
        after a short delay" instead, for both an exception and the
        generator simply ending on its own (also anomalous -- see the
        other two fixes' own docstrings for why that case needs the
        same backoff, not an immediate, undelayed re-call)."""
        from roombapy_prime.models.livemap import (
            MapUpdateMessage,
            PositionUpdateMessage,
            decode_rawmap_to_png,
        )

        session = async_get_clientsession(self.hass)
        backoff = 5.0
        while True:
            # HELD AND CLOSED EXPLICITLY, not consumed inline.
            #
            # `async for x in gen():` drops the generator on the floor
            # when the loop exits, and a cancelled task exits without
            # closing it -- so the `finally` inside watch_live_map(),
            # which unsubscribes from the livemap topic, does not run.
            # Python finalises stray async generators when the event
            # loop shuts down, which is a Home Assistant RESTART. A
            # reload leaves them hanging.
            stream = self._prime_robot.watch_live_map()
            try:
                async for message in stream:
                    # POSITION MESSAGES ARE COUNTED, NOT USED (this session).
                    #
                    # The stream carries two kinds: a URL to iRobot's own
                    # rendered PNG, which is what this entity displays, and
                    # trajectory samples -- which are the same input the
                    # Classic MapRenderer draws from. Prime could therefore
                    # render its own map, with room labels and keep-out
                    # zones, instead of showing a foreign image with none.
                    #
                    # Whether that is worth doing depends on ONE unknown:
                    # how many points actually arrive. Each message carries
                    # several, and nothing has ever counted them. A sparse
                    # stream would render a worse map than the PNG.
                    #
                    # So this counts first. Two testers download diagnostics
                    # regularly, and the answer costs them nothing.
                    #
                    # Units, if this is ever built on: Prime reports METRES
                    # (a real keep-out zone measures 2.0 x 2.0), while
                    # MapRenderer.add_pose takes millimetres. Feeding metres
                    # straight in puts every point in the same pixel and
                    # produces a blank map with no error.
                    if isinstance(message, MapUpdateMessage) and message.livemap_url:
                        # THE SECOND URL. `livemap_url_raw` is the
                        # occupancy grid this entity renders; this one is
                        # a full bundle with the layers the app draws.
                        #
                        # Fetched here because this is where the stream
                        # is, and handed to the rooms map by signal
                        # because that is where the renderer is.
                        try:
                            from roombapy_prime.models.map_bundle import (  # noqa: PLC0415
                                parse_map_bundle,
                            )

                            async with session.get(message.livemap_url) as resp:
                                if resp.status == 200:
                                    raw = await resp.read()
                                    record_success("live bundle fetch")
                                    bundle = await self.hass.async_add_executor_job(
                                        parse_map_bundle, raw
                                    )
                                    async_dispatcher_send(
                                        self.hass,
                                        _SIGNAL_PRIME_LIVE_BUNDLE.format(
                                            self._config_entry.entry_id
                                        ),
                                        bundle,
                                    )
                        except Exception:  # noqa: BLE001
                            record_failure("live bundle fetch", "downloading the live map")
                            _LOGGER.debug(
                                "Prime map: live bundle fetch failed", exc_info=True
                            )

                    if isinstance(message, PositionUpdateMessage):
                        stats = self._config_entry.runtime_data.live_map_stats
                        stats["position_messages"] = stats.get("position_messages", 0) + 1
                        stats["position_points"] = (
                            stats.get("position_points", 0) + len(message.updates)
                        )
                        self._feed_trail(message)
                        # Position packets carry no bundle. The Rooms Map
                        # already reads positions from runtime_data.
                        async_dispatcher_send(
                            self.hass,
                            _SIGNAL_PRIME_LIVE_POSITION.format(
                                self._config_entry.entry_id
                            )
                        )
                        continue

                    if not isinstance(message, MapUpdateMessage) or not message.livemap_url_raw:
                        continue
                    try:
                        async with session.get(message.livemap_url_raw) as resp:
                            raw_bytes = await resp.read()
                            http_status = resp.status
                            content_type = resp.headers.get("Content-Type", "?")
                        # NEW (this session, prompted by a real field report:
                        # 106 consecutive decode failures, chairstacker): the
                        # HTTP status was never checked at all -- an expired
                        # presigned URL (403) or any other error response would
                        # have its ERROR BODY fed straight into the protobuf
                        # parser, producing a confusing parse error instead of
                        # a clear "the download itself failed". Ruled out as
                        # the cause of THAT specific report (error-page bodies
                        # fail at offset 1, the real one failed at offset 6 --
                        # so the payload really was protobuf-shaped), but it's
                        # a genuine robustness gap either way.
                        stats = self._config_entry.runtime_data.live_map_stats
                        stats["updates_received"] += 1
                        if http_status != 200:
                            _LOGGER.warning(
                                "roomba_plus: live map download for %s returned HTTP %s "
                                "(%s, %d bytes) -- skipping this frame",
                                self._blid, http_status, content_type, len(raw_bytes),
                            )
                            continue
                        png_bytes = await self.hass.async_add_executor_job(decode_rawmap_to_png, raw_bytes)
                        stats["decode_ok"] += 1
                    except Exception:  # noqa: BLE001
                        # NEW (same field report): the old message said only
                        # "failed to decode", with nothing about WHAT arrived --
                        # leaving the actual payload a complete guess. The
                        # hex prefix is the single most useful thing for
                        # identifying an unexpected format (protobuf vs. an
                        # error page vs. compressed data) from a log alone,
                        # without needing another round-trip to the reporter.
                        stats = self._config_entry.runtime_data.live_map_stats
                        stats["decode_failed"] += 1
                        stats["last_error"] = repr(sys.exc_info()[1])
                        stats["last_payload_prefix_hex"] = (
                            locals().get("raw_bytes", b"")[:32].hex() or None
                        )
                        # The first two bytes are what solved this the
                        # first time round: 78 9c is a zlib header, and
                        # the payload turned out to be compressed rather
                        # than the protocol being wrong. Naming that
                        # directly saves the next person the same detour.
                        head = locals().get("raw_bytes", b"")[:2]
                        hint = (
                            " -- looks zlib-compressed (78 xx); this should be handled, so "
                            "please report it"
                            if head[:1] == b"\x78"
                            else ""
                        )
                        _LOGGER.exception(
                            "roomba_plus: failed to decode live map update for %s "
                            "(HTTP %s, %s, %d bytes, first 32 bytes: %s)" + hint,
                            self._blid,
                            locals().get("http_status", "?"),
                            locals().get("content_type", "?"),
                            len(locals().get("raw_bytes", b"")),
                            locals().get("raw_bytes", b"")[:32].hex(),
                        )
                        continue
                    self._png_bytes = png_bytes
                    self._map_stored_at = None
                    self._async_save_png()
                    self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
                    self.async_write_ha_state()
                    backoff = 5.0  # a live update means things are healthy again
                _LOGGER.warning(
                    "roomba_plus: watch_live_map() for %s ended without an exception "
                    "(unexpected -- it's meant to run forever) -- retrying in %.0fs",
                    self._blid, backoff,
                )
                self._record_watch_failure(
                    "the stream ended on its own", backoff
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception(
                    "roomba_plus: watch_live_map() for %s ended unexpectedly -- retrying "
                    "in %.0fs (this is this entity's own outer safety net; the library "
                    "itself already retries connection drops internally, so reaching "
                    "this suggests something else went wrong)", self._blid, backoff,
                )
                self._record_watch_failure(repr(exc), backoff)
            finally:
                # Runs on a normal exit, on an error, AND on
                # cancellation -- which is the case that matters. This
                # is what lets watch_live_map()'s own finally
                # unsubscribe from the topic before the entity goes
                # away.
                with contextlib.suppress(Exception):
                    await stream.aclose()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)

    def _record_watch_failure(self, detail: str, backoff: float) -> None:
        """Puts a watcher failure where a diagnostics download can see it.

        THE OUTER RETRY LOOP WAS SILENT IN DIAGNOSTICS. It logged, and
        only the DECODE path ever wrote to `last_error` -- so a capture
        showing every counter at zero and `last_error: null` could not
        distinguish "nothing arrived" from "this task has been crashing
        and retrying since setup".

        @chairstacker hit exactly that: all counters zero, mid-mission,
        no error anywhere. And his own aside is what points at this
        loop -- he saw the map's timestamp move at roughly four-minute
        intervals rather than the two to twelve seconds he was used to.
        `backoff` doubles from 5s up to 300s, so a four-minute gap is
        what a grown backoff looks like from the outside.

        The retry count matters as much as the message: one failure at
        startup is noise, forty is the whole story.
        """
        stats = self._config_entry.runtime_data.live_map_stats
        if stats is None:
            return
        stats["watch_failures"] = stats.get("watch_failures", 0) + 1
        stats["last_watch_error"] = detail
        stats["watch_retry_backoff_s"] = backoff

    @property
    def available(self) -> bool:
        """Unavailable until a map has actually been decoded.

        ADDED (this session, from a field report). This entity served a
        blank placeholder whenever it had no data, so a tester looked at
        a white square for weeks with nothing anywhere indicating a
        fault -- the integration was quietly saying "I have nothing to
        show" and it was indistinguishable from "the map is empty right
        now".

        There was a second symptom too: with no image ever produced,
        image_last_updated never moves, so the frontend keeps requesting
        a signed URL that eventually expires and logs an authentication
        error pointing at Home Assistant rather than at us.

        Unavailable is the honest state. The placeholder stays for the
        window between the entity appearing and the first map arriving,
        which is normal and brief."""
        return super().available and self._png_bytes is not None

    async def async_image(self) -> bytes | None:
        return self._png_bytes or self._blank_image()

    async def _async_restore_png(self) -> None:
        """Bring back the last map from storage.

        WHY THIS IS NEEDED AT ALL. Unlike Classic, which draws its own
        map from pose data and already persists renderer state, Prime
        displays a finished PNG that iRobot produces -- and that arrives
        only DURING AND AFTER a mission. So after any restart, reload or
        update there is no map until the robot next runs, which two
        testers hit within minutes of updating.

        The a11 change to report `unavailable` instead of a blank white
        square did not cause this; it made it visible. Before, the same
        absence merely looked like a broken image.

        Storing the last frame is not the ideal fix -- rendering our own
        map from the position samples in the same stream would bring
        room labels and keep-out zones with it -- but it is honest about
        what it is: the most recent map, replaced as soon as the next
        mission produces one.
        """
        import base64  # noqa: PLC0415

        store = Store(
            self.hass,
            _MAP_STORAGE_VERSION,
            _prime_map_storage_key(self._config_entry.entry_id),
        )
        try:
            stored = await store.async_load()
        except Exception:  # noqa: BLE001
            # DELIBERATELY NOT INSTRUMENTED. A fresh install has no
            # stored map, so failing here is the normal first-run state
            # rather than a defect -- reporting it would produce a false
            # positive on every new setup and teach people to ignore the
            # warning.
            _LOGGER.debug("Prime map: could not read stored map", exc_info=True)
            return
        if not stored or not stored.get("png_b64"):
            return
        try:
            self._png_bytes = base64.b64decode(stored["png_b64"])
        except Exception:  # noqa: BLE001
            # Corrupt or truncated: start blank rather than raising. A
            # missing map is a normal state here; a crash is not.
            _LOGGER.debug("Prime map: stored map unreadable", exc_info=True)
            return
        self._map_stored_at = stored.get("saved_at")
        _LOGGER.debug("Prime map: restored %d bytes", len(self._png_bytes))

    def _async_save_png(self) -> None:
        """Persist the current frame, on a delay.

        DELAYED, NOT IMMEDIATE. A mission produces around 26 map frames
        (26 in one real capture), and the first version wrote all of
        them -- roughly 670 KB of base64 JSON each time, so about 17 MB
        per mission and 6 GB a year on a daily schedule.
        
        Only the LAST frame is ever read back, so 25 of those 26 writes
        were pure flash wear. Home Assistant commonly runs from an SD
        card or eMMC, where that is a real cost rather than a
        theoretical one.

        async_delay_save coalesces them: the callback is invoked once,
        after the burst settles, with whatever the latest frame is by
        then. The store is cached on the entity so the pending timer
        survives between frames -- creating a new Store each call would
        restart the delay and defeat it entirely.

        HA also flushes pending delayed saves on shutdown, so the last
        map is not lost to a restart.
        """
        if not self._png_bytes:
            return
        if self._map_store is None:
            self._map_store = Store(
                self.hass,
                _MAP_STORAGE_VERSION,
                _prime_map_storage_key(self._config_entry.entry_id),
            )
        self._map_store.async_delay_save(self._map_save_payload, _MAP_SAVE_DELAY_SECONDS)

    def _map_save_payload(self) -> dict[str, Any]:
        """Called by the store when the delay elapses -- so it captures
        the frame current at THAT moment, not the one that scheduled the
        save. That is the intended behaviour: the newest map wins."""
        import base64  # noqa: PLC0415

        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        return {
            "png_b64": base64.b64encode(self._png_bytes or b"").decode("ascii"),
            "saved_at": dt_util.utcnow().isoformat(),
        }

    @staticmethod
    def _blank_image() -> bytes:
        from PIL import Image
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class RoombaMapImage(IRobotEntity, ImageEntity):
    """Live cleaning map as an ImageEntity.

    The image updates on every MQTT pose message. image_last_updated is
    bumped after each new pose point so the frontend re-fetches the PNG.

    access_tokens is initialized manually here because ImageEntity.__init__
    requires hass which is not yet available at entity creation time.
    async_update_token() is called in async_added_to_hass once hass is set.

    Map state (pose points, stuck markers, heading) is persisted to
    hass.storage after each mission end and restored after HA restarts.
    """

    _attr_translation_key = "map"
    _attr_entity_category = None
    _attr_content_type = "image/png"

    def __init__(
        self,
        roomba: Any,
        blid: str,
        renderer: MapRenderer | None,
        map_capability: MapCapability,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)

        # Manually initialize ImageEntity internals that require hass.
        # async_update_token() is called in async_added_to_hass.
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)

        self._renderer = renderer
        self._map_capability = map_capability
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map"

        # Mission tracking
        self._last_phase: str = ""
        self._last_stuck_count: int = 0
        self._mission_points: list[tuple[float, float]] = []
        # v3.2.1 — parallel theta list, same index alignment as
        # self._mission_points (mission_thetas[i] is the heading for
        # mission_points[i]). Kept SEPARATE rather than widening
        # _mission_points itself to (x,y,theta): that list is consumed
        # in many places as strict (x,y) pairs (GridStore.
        # update_from_mission, _check_dock_drift's final-position check,
        # etc.) — changing its shape would ripple through all of them.
        # Additive, no behaviour change: existing consumers are
        # untouched, new consumers (MissionTrajectoryStore, future
        # Dock-Anchor-Korrektur rotation math) read this list alongside.
        self._mission_thetas: list[float] = []
        self._stuck_mission_points: list[tuple[float, float]] = []
        # v3.2.1 DOCK-ANCHOR — replaces the old binary
        # _room_data_frozen_after_stuck with a proper buffer + state flag.
        # Field-confirmed rationale: a stuck event is exactly the moment a
        # human is most likely to have physically lifted and repositioned
        # the robot to free it — vSLAM's continuous camera-landmark
        # tracking breaks the instant the robot leaves the floor, and
        # after being set back down (possibly at a slightly different
        # heading than before), the pose stream may resume reporting
        # self-consistent-looking but subtly MISALIGNED positions relative
        # to everything recorded before the stuck event. A robot's own
        # motor-driven self-recovery (backing out, trying another angle)
        # does NOT necessarily break this — but we can't reliably tell the
        # two apart from the data alone, so the conservative rule applies
        # to every stuck event: only the DOCK gives a precise, independent
        # re-anchor (IR/contact-based, not vSLAM-dependent).
        #
        # While _dock_anchor_buffering is True, new pose points go into
        # _pending_segment_points/_thetas instead of _mission_points/
        # _mission_thetas. On a confirmed dock contact (see
        # _dock_contact_streak below), the buffered segment is corrected
        # (see _compute_dock_correction/_interpolate_and_correct_segment)
        # and merged INTO _mission_points/_mission_thetas — replacing the
        # old "freeze then discard at next mission start" behaviour with
        # "freeze then retroactively correct and keep, where possible".
        # Never touches self._renderer.add_pose() — the live-map visual
        # still shows the full path live (useful for troubleshooting);
        # only the GridStore/RoomSegStore/OutlineStore-feeding
        # _mission_points is affected, corrected in place once resolved.
        # See Dock_Anchor_Korrektur_Plan.md for the full design.
        self._dock_anchor_buffering: bool = False
        self._pending_segment_points: list[tuple[float, float]] = []
        self._pending_segment_thetas: list[float] = []
        # v3.2.1 DOCK-ANCHOR — index into _mission_points/_mission_thetas
        # marking the start of the segment since the LAST confirmed dock
        # contact (or mission start, index 0, if none yet this mission).
        # Used by Fall B (a clean recharge-and-resume, no buffering) to
        # know how much of _mission_points to correct — everything since
        # this index, not the whole mission.
        self._last_dock_anchor_index: int = 0
        # v3.2.1 DOCK-ANCHOR — separate debounce counter from
        # _end_signal_streak (below). "Confirmed at the dock" fires on
        # ANY sustained charge/hmPostMsn phase, whether the mission is
        # ending (Fall A/B end-of-mission) or just recharging mid-mission
        # (Fall B, mission continues) — unlike _end_signal_streak, this
        # doesn't need the extra END_SIGNAL_MIN_HOLD_SECONDS grace period
        # (that grace period exists to decide "is this really the END",
        # a question this mechanism doesn't need answered first).
        self._dock_contact_streak: int = 0
        # v3.2.1 DOCK-ANCHOR — field-confirmed gap in the FIRST version of
        # this mechanism: a rapid ~21ms firmware burst reporting
        # charge/hmPostMsn during a normal inter-room transition (the
        # EXACT scenario the existing END-DEBOUNCE mechanism's
        # END_SIGNAL_MIN_HOLD_SECONDS hold-time exists to filter out)
        # would satisfy a pure count-based streak threshold just as
        # easily as a genuine dock contact — count alone doesn't
        # distinguish "sustained" from "coincidentally happened twice
        # fast." Can't reuse _end_signal_first_ts/_end_signal_streak
        # directly: that mechanism deliberately RESETS its own streak for
        # exactly the Fall-B scenario this needs to catch (cycle=clean +
        # phase=charge, i.e. _looks_like_end=False) — needs independent
        # tracking, not shared state.
        self._dock_contact_first_ts: float = 0.0
        # v2.6.3 A+D — True once robot enters CLEANING_PHASES in this mission.
        # Replaces last_phase-in-CLEANING_PHASES guard; fixes stuck-bypass and
        # false mission-restart on stuck → run recovery.
        self._had_cleaning_phase: bool = False
        # v2.8.1 (END-DEBOUNCE) — consecutive-message counter, mirrors the
        # same mechanism in callbacks.py. See _on_message for details.
        self._end_signal_streak: int = 0
        # v2.8.3 — monotonic timestamp when the current streak started.
        # Mirrors callbacks.py end_signal_first_ts — see that module for the
        # full rationale.  Required in both places because image.py's
        # mission-end detection is independent of callbacks.py (it feeds
        # ZoneStore/GeometryStore/GridStore/OutlineStore).
        self._end_signal_first_ts: float = 0.0
        # v2.8.2 — mission-in-progress checkpoint state. mssn_strt_tm
        # identifies "is this still the same mission" across an HA restart
        # (same field callbacks.py already uses for this purpose — robust
        # because 980/900-series firmware does NOT reset it mid-mission,
        # unlike at mission end). _pending_checkpoint holds whatever was
        # loaded from storage at startup until the first MQTT message
        # resolves it (resume or salvage) — see _consume_pending_checkpoint().
        self._mission_checkpoint_mssn_strt_tm: int = 0
        self._pending_checkpoint: dict[str, Any] | None = None

        # Initial timestamp so frontend knows an image exists from the start
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

    # ── HA lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register MQTT callback, restore persisted map state, generate token."""
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # Restore last mission's map from hass.storage (if any)
        await self._async_restore_map_state()
        # v2.8.2 — load (but do not yet apply) a mission-in-progress
        # checkpoint, if one exists. The first live MQTT message decides
        # whether to resume it or salvage it — see _consume_pending_checkpoint().
        await self._async_load_pending_checkpoint()

    # ── ImageEntity interface ─────────────────────────────────────────────────

    async def async_image(self) -> bytes | None:
        """Return current map as PNG bytes. Always returns a valid image."""
        if self._renderer is None:
            return self._blank_image()
        png = await self.hass.async_add_executor_job(self._renderer.render)

        # v2.3.0 Step 6 — keepout zone overlay (Amendment 4)
        if self._config_entry is not None:
            _data = self._config_entry.runtime_data
            aligner = _data.umf_aligner
            if (
                aligner and aligner.aligned
                and _data.cloud_coordinator is not None
            ):
                keepout_raw = _data.cloud_coordinator.keepout_zones
                if keepout_raw:
                    polys_px: list[list[tuple[int, int]]] = []
                    for zone in keepout_raw:
                        poly_umf = aligner.keepout_polygon_umf(zone)
                        if not poly_umf:
                            continue
                        poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                        if not all(p is not None for p in poly_pose):
                            continue
                        polys_px.append(
                            [self._renderer._mm_to_px_fit(x, y) for x, y in poly_pose]
                        )
                    if polys_px:
                        overlay_png = await self.hass.async_add_executor_job(
                            self._renderer.render_keepout_zones, polys_px
                        )
                        if overlay_png is not None:
                            png = overlay_png

                # ZONE-OVERLAY (v3.0.0) — robot-observed obstacle zones as orange circles.
                # Gate: same as keepout (aligner aligned) — observed_zone_centroids are in
                # UMF space and require the aligner for pose conversion.
                # Available: any robot with active cloud coordinator (SMART + EPHEMERAL
                # with cloud credentials) — not limited to has_pmaps.
                centroids = _data.cloud_coordinator.observed_zone_centroids if _data.cloud_coordinator else []
                if centroids:
                    _OBSERVED_RADIUS_MM = 200  # approx obstacle circle radius in mm
                    circles_px: list[tuple[int, int, int]] = []
                    for c in centroids:
                        pose_xy = aligner.umf_to_pose(c["x"], c["y"])
                        if pose_xy is None:
                            continue
                        cx_px, cy_px = self._renderer._mm_to_px_fit(*pose_xy)
                        r_px = max(3, round(_OBSERVED_RADIUS_MM / self._renderer._fit_scale))
                        circles_px.append((int(cx_px), int(cy_px), r_px))
                    if circles_px:
                        overlay_png = await self.hass.async_add_executor_job(
                            self._renderer.render_observed_zones, circles_px
                        )
                        if overlay_png is not None:
                            png = overlay_png

        # F-EPHEMERAL — Room outline overlay (EPHEMERAL, mission_count >= 2)
        if self._config_entry is not None:
            _edata = self._config_entry.runtime_data
            _outline_store = getattr(_edata, "outline_store", None)
            if (
                _outline_store is not None
                and _outline_store.ready
                and self._renderer is not None
            ):
                from .models import MapCapability
                if _edata.map_capability == MapCapability.EPHEMERAL:
                    outline_png = await self.hass.async_add_executor_job(
                        self._renderer.render_room_outline,
                        _outline_store.contour_points,
                    )
                    if outline_png is not None:
                        png = outline_png
        return png

    # v2.3.0 Step 5 — calibration + rooms for xiaomi-vacuum-map-card
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and room polygon data for xiaomi-vacuum-map-card.

        Both attributes require UmfAligner confidence >= 0.70 and a renderer
        that has completed at least one render() call (so _mm_to_px() is valid).
        Returns empty dict when no aligner, not aligned, or no renderer.
        """
        attrs: dict[str, Any] = {}
        if self._config_entry is None or self._renderer is None:
            return attrs
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None or not aligner.aligned:
            return attrs

        # calibration — three anchor point pairs for xiaomi-vacuum-map-card
        # v2.6.3 B1: _mm_to_px_fit gives fit-adjusted pixels matching displayed image
        # XVMC (v2.7.0): calibration_points key enables calibration_source: { camera: true }
        cal = aligner.calibration_points(self._renderer._mm_to_px_fit)
        if cal:
            attrs["calibration_points"] = cal

        # rooms — dict {name: {outline:[[x,y],...], name, icon, x, y}}
        # XVMC (v2.7.0): dict keyed by display name; outline uses [x,y] arrays.
        cc = data.cloud_coordinator
        rid_to_type = (
            {r["id"]: r.get("region_type", "default") for r in cc.regions}
            if cc is not None else {}
        )
        rid_to_name = aligner.rid_to_name()
        rooms: dict[str, dict[str, Any]] = {}
        for rid, poly_umf in aligner.room_polygons_umf.items():
            poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
            # Bug 6 fix: guard against empty polygon (vacuous all() on [])
            if not poly_pose or not all(p is not None for p in poly_pose):
                continue
            room_name = rid_to_name.get(rid, rid)
            # XVMC-COORDS: outline and centroid in pose-space mm (not pixels).
            # XVMC applies calibration (pose mm → display px) itself.
            cx = sum(x for x, _ in poly_pose) / len(poly_pose)
            cy = sum(y for _, y in poly_pose) / len(poly_pose)
            icon = REGION_TYPE_ICONS.get(
                rid_to_type.get(rid, "default"), REGION_TYPE_ICONS["default"]
            )
            rooms[room_name] = {
                "outline": [[x, y] for x, y in poly_pose],
                "name":    room_name,
                "room_id": room_slug(room_name),  # v2.7.3: ASCII slug for XVMC id
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

        # ZONE-OVERLAY (v3.3.1) + F24 — mirrors RoombaRoomsImage's identical
        # block for parity ("Both map entities expose calibration_points and
        # rooms attributes", docs/FEATURES.md). This class is always in
        # aligned mode by this point (early-returned above if not
        # aligner.aligned), so no extra gate is needed here.
        # zones — UMF-space source (observed_zone_centroids, keepout_zones),
        # genuinely needs the aligner transform, unlike door_markers/
        # furniture_candidates below.
        if cc is not None:
            zones: list[dict[str, Any]] = []
            for centroid in cc.observed_zone_centroids:
                pose_xy = aligner.umf_to_pose(centroid["x"], centroid["y"])
                if pose_xy is None:
                    continue
                zones.append({
                    "type": "observed",
                    "x":    pose_xy[0],
                    "y":    pose_xy[1],
                })
            for zone in cc.keepout_zones:
                poly_umf = aligner.keepout_polygon_umf(zone)
                if not poly_umf:
                    continue
                poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                if not poly_pose or not all(p is not None for p in poly_pose):
                    continue
                zones.append({
                    "type":    "keepout",
                    "polygon": [[x, y] for x, y in poly_pose],
                })
            if zones:
                attrs["zones"] = zones

        # door_markers — already pose-space mm (collected directly from
        # self._mission_points / RoomSegStore.doors, never through UMF) —
        # exposed as-is, NOT through umf_to_pose(). Known caveat: markers
        # accumulate across missions and are not re-corrected by
        # GeometryStore.record_drift()/drift_recovered() (those only track
        # drift magnitude for the Repair Issue), so a marker's median
        # position can lag behind a large drift correction between
        # missions — same open-ended caveat class as observed_zone
        # centroids' Q6 note, not treated as a blocker.
        geometry_store = getattr(data, "geometry_store", None)
        if geometry_store is not None and geometry_store.door_markers:
            attrs["door_markers"] = [
                {
                    "id":            m.id,
                    "cx":            m.cx,
                    "cy":            m.cy,
                    "label":         m.label,
                    "mission_count": m.mission_count,
                }
                for m in geometry_store.door_markers
            ]

        # F24 — furniture shadow candidates. GridStore.furniture_
        # candidates()'s x_mm/y_mm come from _cell_to_mm(), the same
        # pose-space family hotspots()/format=hazards already documents —
        # no transform needed, exposed as-is.
        grid_store = getattr(data, "grid_store", None)
        if grid_store is not None:
            candidates = grid_store.furniture_candidates()
            if candidates:
                attrs["furniture_candidates"] = [
                    {"x_mm": c["x_mm"], "y_mm": c["y_mm"]} for c in candidates
                ]

        return attrs

    # ── Push-update wiring ────────────────────────────────────────────────────

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return (
            "pose" in new_state
            or "cleanMissionStatus" in new_state
            or "bbrun" in new_state
        )

    def on_message(self, json_data: dict[str, Any]) -> None:
        """Process MQTT update — feed pose to renderer, bump image timestamp."""
        state = json_data.get("state", {}).get("reported", {})
        if not self.new_state_filter(state):
            return

        self.vacuum_state = roomba_reported_state(self.vacuum)
        current_phase = (
            (self.vacuum_state.get("cleanMissionStatus") or {}).get("phase", "")
        )

        # v2.8.2 — resolve any checkpoint loaded at startup against this,
        # the first live MQTT message since restart. Must run before the
        # phase-transition block below: if this resumes a still-ongoing
        # mission, _had_cleaning_phase is set True here, which correctly
        # makes the "mission started" reset below a no-op for this message.
        if self._pending_checkpoint is not None:
            self._consume_pending_checkpoint()

        # Phase transitions
        if current_phase != self._last_phase:
            # v2.6.3 D — guard with _had_cleaning_phase so stuck → run (recovery)
            # does NOT reset the renderer mid-mission.
            if current_phase in CLEANING_PHASES and not self._had_cleaning_phase:
                self._had_cleaning_phase = True
                if self._renderer:
                    self._renderer.reset()
                    self._mission_points = []
                    self._mission_thetas = []
                    self._stuck_mission_points = []
                    # v3.2.1 DOCK-ANCHOR — a new mission starting means any
                    # still-buffered segment from the PREVIOUS mission never
                    # got a dock-contact confirmation (stuck_and_abandoned,
                    # see Dock_Anchor_Korrektur_Plan.md Abschnitt 5) — it is
                    # discarded here exactly as the old flag-based version
                    # discarded it, just via clearing the buffer instead of
                    # flipping a boolean.
                    self._dock_anchor_buffering = False
                    self._pending_segment_points = []
                    self._pending_segment_thetas = []
                    self._last_dock_anchor_index = 0
                    self._dock_contact_streak = 0
                    self._dock_contact_first_ts = 0.0
                    self._mission_start_ts: str | None = dt_util.now().isoformat()
                    # v2.8.2 — cached the same way callbacks.py caches it:
                    # needed so a later checkpoint (saved on a stuck event)
                    # can be matched against the live mission on restart.
                    self._mission_checkpoint_mssn_strt_tm = (
                        (self.vacuum_state.get("cleanMissionStatus") or {}).get("mssnStrtTm") or 0
                    )
                    _LOGGER.debug("Map: mission started, renderer reset")

            self._last_phase = current_phase

        # v2.8.1 (END-DEBOUNCE): mirrors the fix in callbacks.py's
        # _on_mission_message. Before this fix, a single transient MQTT
        # message momentarily reporting an ambiguous phase (charge/hmPostMsn)
        # — without even a cycle check, unlike callbacks.py's pre-v2.8.1
        # state — was enough to fire _handle_mission_end() mid-mission. That
        # call clears self._mission_points (line 556 below) and feeds the
        # partial trajectory-so-far into ZoneStore, GeometryStore, GridStore,
        # and OutlineStore. The next genuine "run" message then wipes
        # self._mission_points again via the mission-start reset above —
        # fragmenting one continuous multi-room mission into several small,
        # disconnected pieces, none of which individually shows the gap
        # needed to ever split a zone or register a door marker. This is the
        # same root cause confirmed (and fixed) in callbacks.py for the
        # MissionTimerStore progress-reset regression; this is the matching
        # fix for the map/zone/geometry/grid/outline side, which had no
        # protection at all (not even the v2.8.0 cycle-only guard).
        #
        # Deliberately evaluated on every message that actually carries a
        # cleanMissionStatus update — NOT folded into the "phase transitions"
        # edge-trigger above. A real "stays in charge for two consecutive
        # messages" sequence has the same current_phase value both times, so
        # an edge-triggered (`current_phase != self._last_phase`) check would
        # only ever see ONE transition and could never count two consecutive
        # confirmations. Restricting to "cleanMissionStatus" in state (the
        # raw per-message delta, not the merged self.vacuum_state) instead of
        # running on every on_message() call avoids over-counting against
        # pose-only/bbrun-only updates that don't represent a new mission
        # status reading at all.
        if "cleanMissionStatus" in state:
            _cycle = (self.vacuum_state.get("cleanMissionStatus") or {}).get("cycle", "")
            _is_inter_room_transition = _cycle in ("clean", "quick")
            _looks_like_end = (
                current_phase in MISSION_END_PHASES and not _is_inter_room_transition
            )
            _ambiguous_end_phase = current_phase in ROOM_TRANSITION_CANDIDATE_PHASES
            # v3.2.1 DOCK-ANCHOR — captured BEFORE the mission-end block
            # below can reset self._had_cleaning_phase to False. Without
            # this, a message that BOTH confirms mission-end AND is the
            # dock-contact-confirming message would see
            # self._had_cleaning_phase already flipped False and silently
            # skip dock-contact detection for that message.
            _was_in_cleaning_phase_this_message = self._had_cleaning_phase

            if self._had_cleaning_phase:
                if not _looks_like_end:
                    self._end_signal_streak = 0
                    self._end_signal_first_ts = 0.0
                elif _ambiguous_end_phase:
                    if self._end_signal_streak == 0:
                        self._end_signal_first_ts = _time_mod.monotonic()
                    self._end_signal_streak += 1
                else:
                    # Unambiguous terminal phase (stop) — confirm immediately.
                    self._end_signal_streak = END_SIGNAL_DEBOUNCE_COUNT

            # v3.2.1 DOCK-ANCHOR — MUST run before the mission-end block
            # below, not after (this was a real bug in the first version
            # of this feature, caught before shipping): _handle_mission_end()
            # calls grid_store.update_from_mission(self._mission_points, ...)
            # — a SINGLE, one-shot feed of GridStore/RoomSegStore/
            # OutlineStore. If the dock-anchor correction ran AFTER that
            # call (as it did in the first version), the most important
            # case this whole mechanism exists for — a stuck-buffered
            # segment resolving exactly at the mission's final dock
            # contact — would have its correction applied only to the
            # live map, never reaching the stores at all for that
            # mission's contribution. Running first here guarantees
            # _mission_points already reflects the correction by the
            # time _handle_mission_end() reads it.
            #
            # v3.2.1 DOCK-ANCHOR — separate, simpler debounce than
            # _end_signal_streak: "confirmed at the dock" fires on ANY
            # SUSTAINED charge/hmPostMsn phase, whether the mission is
            # ending (Fall A/B, handled above too) or just a mid-mission
            # recharge (Fall B only, mission continues —
            # _handle_mission_end() is NOT called for this case, so
            # without this block Fall B would never be detected at all).
            #
            # Field-confirmed gap in the first version of this block: it
            # originally skipped the hold-time check, reasoning that
            # END_SIGNAL_MIN_HOLD_SECONDS only exists to decide "is this
            # really the END." Wrong — a real regression test
            # (TestImageEndDebounceV281, the exact ~21ms lewis-firmware
            # burst scenario) showed the hold-time ALSO filters out
            # transient firmware glitches reporting charge/hmPostMsn
            # during a normal room transition, which is not a dock
            # contact at all. Count alone can't tell "sustained" from
            # "coincidentally happened twice fast" — the hold-time is
            # required for both purposes, not just the first.
            if (
                self._map_capability == MapCapability.EPHEMERAL
                and current_phase in ROOM_TRANSITION_CANDIDATE_PHASES
                and _was_in_cleaning_phase_this_message
            ):
                if self._dock_contact_streak >= 0:
                    if self._dock_contact_streak == 0:
                        self._dock_contact_first_ts = _time_mod.monotonic()
                    self._dock_contact_streak += 1
                    if (
                        self._dock_contact_streak >= END_SIGNAL_DEBOUNCE_COUNT
                        and (_time_mod.monotonic() - self._dock_contact_first_ts)
                        >= END_SIGNAL_MIN_HOLD_SECONDS
                    ):
                        self._handle_dock_contact_confirmed()
                        # Sentinel -1: already handled this contact episode;
                        # re-arms to 0 only once phase leaves the contact
                        # set (see the else-branch below) — otherwise every
                        # subsequent message while simply parked charging
                        # would re-fire the (harmless but wasteful) handler.
                        self._dock_contact_streak = -1
            else:
                self._dock_contact_streak = 0
                self._dock_contact_first_ts = 0.0

            # v2.6.3 A — use _had_cleaning_phase so stuck → stop/charge
            # (stuck_and_abandoned) correctly triggers _handle_mission_end().
            if (
                current_phase in MISSION_END_PHASES
                and self._had_cleaning_phase
                and _looks_like_end
                and self._end_signal_streak >= END_SIGNAL_DEBOUNCE_COUNT
                and (
                    not _ambiguous_end_phase
                    or (
                        _time_mod.monotonic() - self._end_signal_first_ts
                        >= END_SIGNAL_MIN_HOLD_SECONDS
                    )
                )
            ):
                self._had_cleaning_phase = False
                self._end_signal_streak = 0
                self._end_signal_first_ts = 0.0
                self._handle_mission_end(current_phase)

        # Pose update — process regardless of phase so the map and direction
        # vector stay live even when the robot is stuck, returning, or
        # between phases.  Renderer reset (mission-start) and _handle_mission_end()
        # remain gated on phase transitions.
        if "pose" in state and self._renderer:
            self._handle_pose(state["pose"])

        # Stuck detection
        if "bbrun" in state and self._renderer:
            stuck = (self.vacuum_state.get("bbrun") or {}).get("nStuck", 0) or 0
            if stuck > self._last_stuck_count:
                self._renderer.mark_stuck()
                # Record stuck position in mm for GridStore
                if self._mission_points:
                    self._stuck_mission_points.append(self._mission_points[-1])
                # v3.2.1 DOCK-ANCHOR — EPHEMERAL only, matching the old
                # _check_dock_drift block's own established scoping
                # (field-confirmed gap: this check was originally
                # missing entirely). SMART robots get authoritative room
                # data from the cloud's own persistent, self-correcting
                # map — GridStore/RoomSegStore/OutlineStore (the actual
                # beneficiaries of this correction) are themselves
                # EPHEMERAL-only constructs, so buffering/correcting a
                # SMART robot's local _mission_points would fix data
                # nothing downstream consumes, while still doing
                # unnecessary live-map replace_range() work and feeding
                # dock_theta_baseline/geometry_store.record_drift() for
                # a robot whose vSLAM-continuity story is different
                # (persistent cloud map, not a fresh-per-mission local
                # reconstruction).
                if self._map_capability == MapCapability.EPHEMERAL:
                    # v3.2.1 DOCK-ANCHOR — enter BUFFERING for the rest of
                    # this mission (or until a confirmed dock contact, see
                    # _handle_dock_contact_confirmed). See
                    # Dock_Anchor_Korrektur_Plan.md for the full rationale
                    # (vSLAM continuity risk after a likely pickup).
                    self._dock_anchor_buffering = True
                # v2.8.2 — checkpoint the in-progress mission. A stuck event
                # is exactly the moment a mission is most at risk of never
                # reaching a clean end (HA restart, manual intervention) —
                # see _async_save_mission_checkpoint() docstring.
                if self._config_entry is not None and self._had_cleaning_phase:
                    asyncio.run_coroutine_threadsafe(
                        self._async_save_mission_checkpoint(), self.hass.loop
                    )
            self._last_stuck_count = stuck

        self.schedule_update_ha_state()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _handle_pose(self, pose: dict[str, Any]) -> None:
        """Add pose point and signal frontend to re-fetch image.

        v2.9.0 — firmware reports pose.point.x/y in CENTIMETRES, not
        millimetres (confirmed from real field data; see POSE_POINT_CM_TO_MM
        in const.py for the full rationale). Converted here, at the single
        point this value first enters the system, so every downstream
        consumer (MapRenderer, self._mission_points -> GridStore/ZoneStore/
        OutlineStore) receives genuine millimetres and needs no changes.

        v3.2.1 DOCK-ANCHOR — while self._dock_anchor_buffering is True (a
        stuck event occurred and no confirmed dock contact has resolved
        it yet), points go into _pending_segment_points/_thetas instead
        of _mission_points/_mission_thetas — buffered for retroactive
        correction (see _handle_dock_contact_confirmed), not discarded
        outright as the old flag-based version did. The live MapRenderer
        visual is deliberately NOT frozen — add_pose() still runs
        unconditionally, so the on-screen path keeps showing what
        actually happened (troubleshooting value) until the buffered
        segment is corrected and merged in place.
        """
        point = pose.get("point", {})
        # v3.2.1 AXIS-SWAP FIX — roombapy's own source (roomba.py) does
        # `pose_point_x -> co_ords["y"]`, `pose_point_y -> co_ords["x"]`
        # ("# x and y are reversed..."), matching add_pose()'s own
        # long-standing docstring claim ("roombapy convention: co_ords['x']
        # = pose_point_y"). This code never actually applied that swap —
        # confirmed, independent bug from the raw firmware fields to this
        # single entry point. Tested in isolation (visualised trajectory)
        # and confirmed NOT to be the explanation for the "live map
        # doesn't match real room layout" symptom investigated this
        # session — that was the vSLAM continuity loss after stuck
        # events, fixed separately via the Dock-Anchor-Korrektur
        # mechanism. Fixed here anyway because it is a real, independently
        # confirmed discrepancy from the documented convention, not
        # because it explains that symptom.
        #
        # BREAKING DISCONTINUITY, not a silent one-line fix: every
        # downstream consumer of x/y (MapRenderer, GridStore, RoomSegStore,
        # OutlineStore, MissionTrajectoryStore) has been accumulating data
        # under the OLD (unswapped) axis meaning. Data recorded before
        # this fix and data recorded after it do NOT represent the same
        # physical directions — old and new history will not spatially
        # align if mixed. Combined with the already-recommended "fresh
        # start" for GridStore/RoomSegStore (stuck-event contamination
        # predating the Dock-Anchor-Korrektur, see that plan) rather than
        # attempting a coordinate-transform migration of old data.
        x = float(point.get("y", 0)) * POSE_POINT_CM_TO_MM
        y = float(point.get("x", 0)) * POSE_POINT_CM_TO_MM
        theta = float(pose.get("theta", 0))
        # v3.2.1 DOCK-ANCHOR — the very FIRST pose reading of a mission
        # (x=y=0, robot still literally on the dock before departure) is
        # arguably the CLEANEST possible dock_theta_baseline sample: the
        # robot is certainly at the dock, certainly stationary, and this
        # is before any stuck event or disturbance could have occurred
        # this mission — better grounds for "clean" than even a Fall B
        # recharge-return sample. MapRenderer.add_pose() already skips
        # this exact point for its own (unrelated) reasons, discarding
        # the theta entirely; captured here instead, roughly doubling
        # the sampling rate for dock_theta_baseline maturation (once at
        # start, once at end/recharge, per mission).
        if (
            self._map_capability == MapCapability.EPHEMERAL
            and x == 0.0 and y == 0.0
            and not self._dock_anchor_buffering
            and not self._mission_points
        ):
            if self._config_entry is not None:
                data = getattr(self._config_entry, "runtime_data", None)
                robot_profile_store = getattr(data, "robot_profile_store", None) if data else None
                if robot_profile_store is not None:
                    robot_profile_store.update_dock_theta_baseline(theta)
        if self._renderer:
            # v3.2.1 DOCK-ANCHOR — return value (accepted-jump flag) not
            # yet consumed here: confidence-weighting by internal jump
            # position was deliberately deferred (see
            # Dock_Anchor_Korrektur_Plan.md 4c) pending real field
            # validation that simple linear interpolation isn't enough.
            self._renderer.add_pose(x, y, theta)
        if self._dock_anchor_buffering:
            self._pending_segment_points.append((x, y))
            self._pending_segment_thetas.append(theta)
        else:
            self._mission_points.append((x, y))
            self._mission_thetas.append(theta)
        self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)

    def _handle_dock_contact_confirmed(self) -> None:
        """v3.2.1 DOCK-ANCHOR — fires once per confirmed dock contact
        (debounced in _on_message), whether the mission is ending or
        just recharging mid-mission (Fall B). See
        Dock_Anchor_Korrektur_Plan.md for the full design.

        Fall A (self._dock_anchor_buffering True): a stuck event
        happened earlier this mission and has not yet been resolved.
        The buffered segment is corrected (interpolated, see
        _interpolate_and_correct_segment) and merged into
        _mission_points/_mission_thetas — rescued instead of discarded.
        dock_theta_baseline is NOT fed from this contact: it followed a
        disturbance, not a clean docking (see RobotProfileStore.
        update_dock_theta_baseline's docstring).

        Fall B (not buffering): a normal, undisturbed dock contact
        (recharge-and-resume, or a clean mission end). No buffering
        needed — directly correct the segment since the last dock
        anchor. This IS a clean contact, so it feeds
        dock_theta_baseline.

        Live-map correction (MapRenderer.replace_range) is a best-effort
        approximation: MapRenderer's own point list can be shorter than
        _mission_points/_pending_segment_points (it silently drops
        implausible-jump points that image.py's unfiltered pose stream
        still recorded) — there is no guaranteed 1:1 index
        correspondence between the two. Replacing MapRenderer's last N
        points (N = corrected segment length) is therefore an
        approximation, not an exact replay; acceptable because rejected
        jumps are rare and the live map is a visual aid, not a data
        source GridStore/RoomSeg/Outline depend on.
        """
        # v3.2.1 DOCK-ANCHOR — defensive belt-and-suspenders: the caller
        # (the dock-contact debounce block) already gates on EPHEMERAL,
        # so this should never actually be reached for a SMART robot in
        # practice — kept anyway so a future refactor that calls this
        # method from a new call site can't silently reintroduce the
        # SMART-robot gap fixed here (see the buffering-entry gate for
        # the full rationale).
        if self._map_capability != MapCapability.EPHEMERAL:
            return
        robot_profile_store = None
        if self._config_entry is not None:
            data = getattr(self._config_entry, "runtime_data", None)
            robot_profile_store = getattr(data, "robot_profile_store", None) if data else None

        dock_theta_baseline = None
        if robot_profile_store is not None and robot_profile_store.dock_theta_baseline_ready:
            dock_theta_baseline = robot_profile_store.dock_theta_baseline

        if self._dock_anchor_buffering:
            segment = self._pending_segment_points
            thetas = self._pending_segment_thetas
            is_clean_contact = False
        else:
            segment = self._mission_points[self._last_dock_anchor_index:]
            thetas = self._mission_thetas[self._last_dock_anchor_index:]
            is_clean_contact = True

        if segment:
            measured_final_pos = segment[-1]
            measured_final_theta = thetas[-1] if thetas else 0.0
            dx, dy, rotation_rad = _compute_dock_correction(
                measured_final_pos, measured_final_theta, dock_theta_baseline,
            )
            corrected_points = _interpolate_and_correct_segment(segment, dx, dy, rotation_rad)
            rotation_deg = math.degrees(rotation_rad)
            n = len(thetas)
            corrected_thetas = [
                (t + rotation_deg * (i / (n - 1) if n > 1 else 1.0)) % 360.0
                for i, t in enumerate(thetas)
            ]

            if self._dock_anchor_buffering:
                self._mission_points.extend(corrected_points)
                self._mission_thetas.extend(corrected_thetas)
            else:
                self._mission_points[self._last_dock_anchor_index:] = corrected_points
                self._mission_thetas[self._last_dock_anchor_index:] = corrected_thetas

            if self._renderer is not None:
                start_index = max(0, self._renderer.point_count - len(segment))
                self._renderer.replace_range(start_index, corrected_points)

            if is_clean_contact and robot_profile_store is not None:
                robot_profile_store.update_dock_theta_baseline(measured_final_theta)

            # v3.2.1 DOCK-ANCHOR — consolidates the old, disconnected
            # _check_dock_drift()-only diagnostic (pure logging, no
            # correction applied) into this single place that now both
            # detects AND corrects. GeometryStore.record_drift() keeps
            # its existing Repair-Issue-triggering behaviour, fed from
            # the SAME correction vector this method just applied.
            if self._config_entry is not None:
                data = getattr(self._config_entry, "runtime_data", None)
                geometry_store = getattr(data, "geometry_store", None) if data else None
                if geometry_store is not None and (dx, dy) != (0.0, 0.0):
                    threshold_exceeded = geometry_store.record_drift(dx, dy)
                    if threshold_exceeded:
                        # v3.2.1 field-fix — self.hass.loop, not
                        # asyncio.get_event_loop(): this callback runs on
                        # roombapy's paho-MQTT thread (see
                        # _handle_mission_end's own "loop = self.hass.loop"
                        # a few lines below for the established pattern),
                        # not the HA event loop thread — get_event_loop()
                        # there is not guaranteed to return the same loop
                        # HA actually runs on.
                        asyncio.run_coroutine_threadsafe(
                            self._trigger_drift_issue_enriched(dx, dy), self.hass.loop,
                        )
                    # v3.2.1 field-fix — this save call was missing
                    # entirely in the first version: the old
                    # _check_dock_drift block always persisted
                    # geometry_store after recording a drift sample, and
                    # this new mechanism must too, or a HA restart right
                    # after a correction would silently lose the
                    # updated cumulative_drift_mm/recent_drifts_mm.
                    asyncio.run_coroutine_threadsafe(
                        geometry_store.async_save(self.hass, self._config_entry.entry_id),
                        self.hass.loop,
                    )

        self._dock_anchor_buffering = False
        self._pending_segment_points = []
        self._pending_segment_thetas = []
        self._last_dock_anchor_index = len(self._mission_points)
        # v3.2.1 DOCK-ANCHOR — checkpoint right after a successful
        # resolution too, not just at the stuck event that started
        # buffering. Without this, an HA restart between a correction
        # and the NEXT stuck event would restore the STALE pre-
        # resolution checkpoint — reverting _dock_anchor_buffering back
        # to True with the original, now-superseded pending segment,
        # and losing whatever _mission_points accumulated afterward.
        if self._config_entry is not None and self._had_cleaning_phase:
            asyncio.run_coroutine_threadsafe(
                self._async_save_mission_checkpoint(), self.hass.loop
            )

    def _handle_mission_end(self, ending_phase: str = "") -> None:
        """End-of-mission processing. ORDER-SENSITIVE THROUGHOUT.

        READ THIS BEFORE MOVING ANYTHING. Eight releases have patched
        this method, and four of those patches were ordering fixes --
        v2.8.2, and three separate ones in v3.2.1. Each shipped as a
        real bug first.

        The constraints, collected here because they were previously
        discoverable only by reading all fourteen comment blocks below:

          1. Clear the checkpoint BEFORE the "nothing to process"
             early-return. A checkpoint may legitimately hold zero pose
             points, and a checkpoint that survives the return is
             re-salvaged on every HA restart, forever. (v2.8.2)

          2. Feed GridStore BEFORE recomputing room segmentation or the
             outline. Both now derive from GridStore.cells, so they
             need this mission's cells already in it. (v3.2.1)

          3. Record poses into MissionTrajectoryStore BEFORE clearing
             self._mission_points. The store reads the same list
             GridStore just consumed. (v3.2.1)

          4. Dock-drift is handled in _handle_dock_contact_confirmed(),
             which runs BEFORE this method. Do not recompute it here --
             that redundancy existed until v3.2.1 and produced two
             independent drift vectors.

        WHY THIS IS A COMMENT AND NOT STRUCTURE. Splitting these steps
        into named methods was considered and rejected: the
        dependencies do not flow through values but through SEVEN
        shared stores as side effects. A signature cannot express "this
        must run after GridStore already contains the current mission",
        so extraction would move the constraint further from the code
        that depends on it, not closer. The ordering is the contract;
        it needs stating, not hiding.

        The step-order test in test_image.py enforces points 2 and 3.
        """
        # Called from roombapy's paho-MQTT thread — NOT the HA event loop.
        # hass.async_create_task() is not thread-safe and raises RuntimeError
        # on recent HA versions when called from a foreign thread.
        # All coroutine scheduling must go through asyncio.run_coroutine_threadsafe().
        loop = self.hass.loop

        # v2.8.2 bug-hunt fix — checkpoint clearing must happen unconditionally,
        # before the "nothing to process" early-return below. A checkpoint can
        # legitimately have empty mission_points (e.g. a stuck event fired
        # before any pose message had ever arrived this mission), and
        # _salvage_orphaned_checkpoint() loads exactly that into
        # self._mission_points before calling this method. With the clear
        # call previously placed after the early-return, that specific
        # checkpoint would never be deleted — it would be reloaded and
        # re-"salvaged" (a no-op) on every subsequent HA restart forever.
        # Store.async_remove() is a safe no-op when nothing is persisted, so
        # this is harmless on the (overwhelmingly common) normal-end path
        # where no checkpoint exists at all.
        if self._config_entry is not None:
            asyncio.run_coroutine_threadsafe(
                self._async_clear_mission_checkpoint(), loop
            )

        if not self._mission_points:
            return

        # v3.2.1 DOCK-ANCHOR — CONSOLIDATED (previously a KNOWN
        # REDUNDANCY, see Dock_Anchor_Korrektur_Plan.md Abschnitt 7
        # Punkt 1). This block used to independently recompute a drift
        # vector via _check_dock_drift() and call record_drift() —
        # exactly what _handle_dock_contact_confirmed() now does, and
        # (since the ordering fix) already does BEFORE this method runs.
        # By the time we reach here, self._mission_points[-1] is already
        # corrected (pulled to ~(0,0)) whenever a dock-anchor correction
        # applied — recomputing drift on that already-corrected point
        # would almost always find nothing (redundant at best). Detection
        # + correction + Repair-Issue-triggering is now solely
        # _handle_dock_contact_confirmed()'s responsibility. What remains
        # genuinely independent — and is kept — is the periodic
        # self-healing check below: it reads geometry_store's own
        # accumulated history, not anything this block itself computes.
        _dock_return = ending_phase in {"charge", "hmPostMsn"}

        if (self._map_capability == MapCapability.EPHEMERAL
                and _dock_return
                and len(self._mission_points) >= 20):
            data = self._config_entry.runtime_data
            if data.geometry_store:
                # v3.1.0 DRIFT-AUTO — self-healing check, independent of
                # this mission's own drift (already handled elsewhere,
                # see above). Recovery uses a lower hysteresis threshold
                # than the trigger, so the issue doesn't flap right at
                # the boundary.
                if data.geometry_store.drift_recovered():
                    asyncio.run_coroutine_threadsafe(
                        self._clear_drift_issue(), loop
                    )
                asyncio.run_coroutine_threadsafe(
                    data.geometry_store.async_save(self.hass, self._config_entry.entry_id),
                    loop,
                )

        # v2.4.2 GS-SMART — accumulate door-crossing markers for SMART robots.
        # SMART robots have no ZoneStore, so gap detection runs directly on
        # the accumulated pose trajectory using the same constants as ZoneStore.
        # Must be an elif so the EPHEMERAL block above (which already calls
        # update_from_midpoints via update_from_mission) does not double-write.
        elif (
            self._map_capability == MapCapability.SMART
            and self._config_entry is not None
            and len(self._mission_points) >= 20
        ):
            _data = self._config_entry.runtime_data
            if _data.geometry_store:
                _midpoints: list[tuple[float, float]] = []
                _pts = self._mission_points
                for _i in range(len(_pts) - 1):
                    _dist = math.hypot(
                        _pts[_i + 1][0] - _pts[_i][0],
                        _pts[_i + 1][1] - _pts[_i][1],
                    )
                    if _dist > GAP_THRESHOLD_MM and MIN_DOOR_WIDTH_MM <= _dist <= MAX_DOOR_WIDTH_MM:
                        _midpoints.append((
                            (_pts[_i][0] + _pts[_i + 1][0]) / 2.0,
                            (_pts[_i][1] + _pts[_i + 1][1]) / 2.0,
                        ))
                _LOGGER.debug(
                    "Map: SMART path — %d door gap midpoint(s) from %d pose points",
                    len(_midpoints), len(self._mission_points),
                )
                if _midpoints:
                    _data.geometry_store.update_from_midpoints(_midpoints)
                    asyncio.run_coroutine_threadsafe(
                        _data.geometry_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )

        # Persist renderer state so the map survives an HA restart
        if self._renderer and self._renderer.has_data:
            asyncio.run_coroutine_threadsafe(self._async_save_map_state(), loop)

        # F-EPHEMERAL — Room outline recompute moved AFTER the GridStore
        # update below (v3.2.1 redesign): it now derives directly from
        # GridStore.cells, so it must run once that store already
        # includes this just-finished mission's cells. See the block
        # after "Update GridStore for coverage heatmap".

        # Update GridStore for coverage heatmap (all pose-capable robots)
        if self._config_entry is not None and self._mission_points:
            _gdata = self._config_entry.runtime_data
            if _gdata.grid_store is not None:
                # L7 (v2.7.0): compute local (weekday, hour) from mission start here
                # so grid_store.py stays HA-free (no homeassistant imports).
                _stuck_wh: tuple[int, int] | None = None
                _start_ts = getattr(self, "_mission_start_ts", None)
                if _start_ts:
                    try:
                        _parsed = dt_util.parse_datetime(_start_ts)
                        if _parsed is not None:
                            _local = dt_util.as_local(_parsed)
                            _stuck_wh = (_local.weekday(), _local.hour)
                    except Exception:  # noqa: BLE001
                        pass
                _gdata.grid_store.update_from_mission(
                    self._mission_points,
                    self._stuck_mission_points,
                    stuck_wh=_stuck_wh,
                    # v2.9.0 (DISK-FILL) — mark each pose point's actual
                    # swept footprint, not just its single centre cell.
                    # _cfg.robot_diameter_mm is already set correctly per
                    # robot tier (see __init__.py's map_capability-based
                    # selection) — grid_store.py stays HA-free by taking
                    # a plain float here rather than importing the
                    # tier-detection logic itself.
                    robot_radius_mm=self._renderer._cfg.robot_diameter_mm / 2,
                )
                # v3.4.0 GS-SMART-COVERAGE — stamp this mission's nMssn as
                # "already fed into GridStore via the live path". Shared
                # watermark with the cloud-backfill path (callbacks.py):
                # whichever path processes a mission first claims it here,
                # so the other path's candidate filter skips it — this is
                # the actual real-pose robots' path, so it runs (and
                # therefore claims the mission) BEFORE any cloud refresh
                # would otherwise re-process the same mission from UMF
                # data. mission_stats comes from IRobotEntity (entity.py).
                _gdata.grid_store.record_processed_nmssn(
                    self.mission_stats.get("nMssn")
                )
                asyncio.run_coroutine_threadsafe(
                    _gdata.grid_store.async_save(
                        self.hass, self._config_entry.entry_id
                    ),
                    loop,
                )

                # v3.2.1 FIELD FIX — outline recompute's PURE, synchronous
                # half runs HERE, right after GridStore has this mission's
                # cells, unconditionally (matches the prior always-run
                # behaviour, not gated on room-seg's _recomputed below).
                # Moved out of the async-only path further down: anything
                # reading outline_store.contour_points later in THIS same
                # mission (the freeze-snapshot block below, inside the
                # room-seg branch) needs the fresh value available
                # immediately, not after an async_recompute() coroutine
                # merely gets SCHEDULED via run_coroutine_threadsafe — a
                # scheduled coroutine is not a completed one. Field-
                # confirmed gap: the very first FreezeSnapshotStore
                # snapshot captured outline_points=0 for exactly this
                # reason. Persistence (async_save) still happens further
                # down, unchanged in spirit — see recompute_sync()'s
                # docstring in outline_store.py for the full rationale.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.outline_store is not None
                ):
                    _gdata.outline_store.recompute_sync(_gdata.grid_store.cells)

                # ROOM-SEG — recompute room/door segmentation from the
                # just-updated GridStore. EPHEMERAL only (SMART robots get
                # authoritative room data from the cloud already). Runs
                # synchronously on THIS thread, not the event loop — this
                # whole method is already off-loop (see the
                # asyncio.run_coroutine_threadsafe calls throughout), so
                # the CPU-bound segmentation work here doesn't block HA.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.room_seg_store is not None
                ):
                    _unconfirmed_before = len(_gdata.room_seg_store.unconfirmed_rooms)
                    _recomputed = _gdata.room_seg_store.maybe_recompute(
                        _gdata.grid_store.cells
                    )
                    if _recomputed:
                        # ROOM-SEG — fire the same naming-wizard Repair Issue
                        # ZoneStore used to trigger, only when the count of
                        # unconfirmed rooms actually grew (mirrors `if
                        # new_zones:` above — a fresh genuinely-new room was
                        # found, not just an existing unconfirmed one
                        # persisting across this recompute).
                        if len(_gdata.room_seg_store.unconfirmed_rooms) > _unconfirmed_before:
                            asyncio.run_coroutine_threadsafe(self._trigger_zone_issue(), loop)
                        asyncio.run_coroutine_threadsafe(
                            _gdata.room_seg_store.async_save(
                                self.hass, self._config_entry.entry_id
                            ),
                            loop,
                        )
                        # ROOM-SEG — sync GeometryStore's door_markers from
                        # the just-recomputed RoomSegStore.doors, replacing
                        # the old zone_store-fed update_from_mission() path
                        # (gap heuristic, unreliable — see
                        # ROOM_SEGMENTATION_NOTES.md). Only when rooms
                        # actually changed this mission, same gating as the
                        # recompute itself above.
                        if _gdata.geometry_store is not None:
                            _gdata.geometry_store.update_from_room_seg_store(
                                _gdata.room_seg_store
                            )
                            asyncio.run_coroutine_threadsafe(
                                _gdata.geometry_store.async_save(
                                    self.hass, self._config_entry.entry_id
                                ),
                                loop,
                            )

                        # v3.2.1 — FreezeSnapshotStore: count this
                        # successful recompute, and if the interval is due,
                        # capture the current RoomSeg + Outline state into
                        # the immutable backup. Uses whatever
                        # outline_store.contour_points currently holds —
                        # doesn't need to be perfectly in sync with the
                        # async outline recompute below, "good enough" is
                        # the point of a periodic insurance snapshot, not
                        # a live mirror. See freeze_snapshot_store.py
                        # docstring for the firmware-cutoff rationale.
                        if _gdata.freeze_snapshot_store is not None:
                            _gdata.freeze_snapshot_store.note_recompute()
                            if _gdata.freeze_snapshot_store.due():
                                _outline_pts = (
                                    _gdata.outline_store.contour_points
                                    if _gdata.outline_store is not None
                                    else []
                                )
                                _gdata.freeze_snapshot_store.snapshot(
                                    [r.to_dict() for r in _gdata.room_seg_store.rooms.values()],
                                    [d.to_dict() for d in _gdata.room_seg_store.doors],
                                    _outline_pts,
                                    dt_util.now().isoformat(),
                                )
                                asyncio.run_coroutine_threadsafe(
                                    _gdata.freeze_snapshot_store.async_save(
                                        self.hass, self._config_entry.entry_id
                                    ),
                                    loop,
                                )

                # F-EPHEMERAL — Room outline (v3.2.1 redesign): recompute
                # from the same just-updated GridStore.cells room-seg reads
                # above, unconditionally (not gated on _recomputed — the
                # outline is a cheap pure dict pass, unlike room-seg's
                # watershed pipeline, so there's no cost reason to skip it
                # on missions where segmentation itself didn't change).
                # v3.2.1 — persistence only: the actual recompute (contour
                # points + mission_count) already happened synchronously
                # right after the GridStore update above, unconditionally,
                # so this just needs to save that already-current state —
                # NOT call async_recompute() again, which would recompute
                # (harmless) but ALSO increment mission_count a second
                # time for the same mission (not harmless: would silently
                # double-count mission_count against MIN_MISSIONS_TO_SHOW).
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.outline_store is not None
                ):
                    asyncio.run_coroutine_threadsafe(
                        _gdata.outline_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )

                # v3.2.1 — MissionTrajectoryStore: record this mission's raw
                # pose points (same self._mission_points GridStore just
                # consumed above) into the bounded last-N-missions window,
                # BEFORE they're cleared below. Same EPHEMERAL gate as
                # OutlineStore — see mission_trajectory_store.py docstring.
                if (
                    self._map_capability == MapCapability.EPHEMERAL
                    and _gdata.trajectory_store is not None
                ):
                    _mission_key = str(
                        getattr(self, "_mission_start_ts", "") or ""
                    )
                    _gdata.trajectory_store.record_mission(
                        _mission_key, self._mission_points,
                        thetas_deg=self._mission_thetas,
                    )
                    asyncio.run_coroutine_threadsafe(
                        _gdata.trajectory_store.async_save(
                            self.hass, self._config_entry.entry_id
                        ),
                        loop,
                    )
                _LOGGER.debug(
                    "GridStore: updated from mission — %d pose pts, %d stuck pts",
                    len(self._mission_points), len(self._stuck_mission_points),
                )
                # v2.6.3 E — notify RoombaCoverageImage so it bumps its
                # image_last_updated timestamp and the frontend re-fetches.
                _eid = self._config_entry.entry_id
                asyncio.run_coroutine_threadsafe(
                    _async_send_coverage_signal(self.hass, _eid),
                    loop,
                )

        self._mission_points = []
        self._mission_thetas = []
        self._stuck_mission_points = []
        # v3.2.1 DOCK-ANCHOR — mission has now genuinely ended; any
        # still-buffered segment (stuck_and_abandoned, no dock contact
        # ever confirmed) is discarded here, same as the mission-start
        # reset does for the next mission. Harmless to reset both places
        # — whichever fires first for a given mission wins, the other is
        # a no-op on already-empty state.
        self._dock_anchor_buffering = False
        self._pending_segment_points = []
        self._pending_segment_thetas = []
        self._last_dock_anchor_index = 0
        self._dock_contact_streak = 0
        self._dock_contact_first_ts = 0.0
        self._mission_start_ts = None

    async def _async_save_map_state(self) -> None:
        """Write renderer state to hass.storage after mission end."""
        if not self._renderer:
            return
        store = Store(
            self.hass,
            _MAP_STORAGE_VERSION,
            _map_storage_key(self._config_entry.entry_id),
        )
        await store.async_save(self._renderer.dump_state())
        _LOGGER.debug(
            "Map: saved %d points to storage", self._renderer.point_count
        )

    async def _async_restore_map_state(self) -> None:
        """Load renderer state from hass.storage on startup.

        If no stored state exists, or if it is incompatible, the renderer
        starts blank — nothing crashes, the user just sees an empty map until
        the next mission completes.
        """
        if not self._renderer:
            return
        store = Store(
            self.hass,
            _MAP_STORAGE_VERSION,
            _map_storage_key(self._config_entry.entry_id),
        )
        try:
            data = await store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Map: failed to load stored state: %s", exc)
            return

        if not data:
            _LOGGER.debug("Map: no stored state found")
            return

        if self._renderer.restore_state(data):
            # Bump image_last_updated so the frontend fetches the restored image
            self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
            _LOGGER.debug(
                "Map: restored %d points from storage",
                self._renderer.point_count,
            )
        else:
            _LOGGER.warning("Map: stored state was incompatible, starting blank")

    # ── Mission checkpoint (v2.8.2) ──────────────────────────────────────────
    #
    # Distinct from the renderer's _async_save_map_state()/_async_restore_map_state()
    # above, which only ever runs at a clean mission end. These four methods
    # protect against the case that matters most for a robot with a high
    # mission-failure rate: a mission that gets stuck and never reaches a
    # clean end (HA restart, manual intervention) before that happens.

    async def _async_load_pending_checkpoint(self) -> None:
        """Load a mission checkpoint (if any) at startup.

        Does not apply it yet — self._pending_checkpoint is only resolved
        (resumed or salvaged) once the first live MQTT message arrives and
        we know the robot's current mssnStrtTm/phase. See
        _consume_pending_checkpoint().
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        try:
            data = await store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Map: failed to load mission checkpoint: %s", exc)
            return
        if data:
            self._pending_checkpoint = data
            _LOGGER.debug(
                "Map: loaded pending mission checkpoint — %d pose pt(s)",
                len(data.get("mission_points", [])),
            )

    def _consume_pending_checkpoint(self) -> None:
        """Resolve self._pending_checkpoint against the first live message.

        Called at most once per entity lifetime — always sets
        self._pending_checkpoint back to None, whichever branch is taken,
        so a checkpoint is either resumed seamlessly (no extra
        process_mission()-style call) or salvaged exactly once. Never both,
        which would double-count the same trajectory data.

        Same mission still running (mssnStrtTm matches) -> resume: restore
        _mission_points/_stuck_mission_points/_mission_start_ts/
        _last_stuck_count and the renderer state, set
        _had_cleaning_phase=True so the normal "mission started" reset
        below this call becomes a no-op for this message.

        v2.8.2 bug-hunt fix — deliberately does NOT also require
        current_phase to be an actively-cleaning phase. mssnStrtTm matching
        already proves this is the same physical mission (980/900-series
        firmware does not reset it mid-mission); requiring CLEANING_PHASES
        on top of that meant landing on an ordinary inter-room transition
        blip (current_phase == "charge"/"hmPostMsn", not yet confirmed as a
        genuine end) as the very first post-restart message would wrongly
        treat a still-running mission as ended and salvage it — fragmenting
        one continuous mission into an orphaned piece plus a fresh restart,
        exactly the kind of fragmentation this whole feature exists to
        prevent. Resuming unconditionally on a mssnStrtTm match instead lets
        the normal phase-transition / END-DEBOUNCE logic below — which runs
        immediately after, against this same message — correctly decide
        what to do with whatever phase we're actually in: keep going if
        still cleaning, or end correctly (with the real ending_phase, e.g.
        for accurate dock-return/drift detection) if it turns out the
        mission genuinely did conclude while HA was down.

        Otherwise (different mission already started, or mssnStrtTm absent
        from either side) -> orphaned -> salvage once through the same
        store-feeding logic a normal mission end uses, so the data isn't
        silently lost.
        """
        checkpoint = self._pending_checkpoint
        self._pending_checkpoint = None
        if checkpoint is None:
            return

        live_mssn_strt_tm = (
            (self.vacuum_state.get("cleanMissionStatus") or {}).get("mssnStrtTm") or 0
        )
        checkpoint_mssn_strt_tm = checkpoint.get("mssn_strt_tm") or 0

        same_mission_still_active = (
            bool(live_mssn_strt_tm)
            and bool(checkpoint_mssn_strt_tm)
            and live_mssn_strt_tm == checkpoint_mssn_strt_tm
        )

        if same_mission_still_active:
            self._mission_points = list(checkpoint.get("mission_points", []))
            self._mission_thetas = list(checkpoint.get("mission_thetas", []))
            self._stuck_mission_points = list(checkpoint.get("stuck_mission_points", []))
            self._mission_start_ts = checkpoint.get("mission_start_ts")
            self._mission_checkpoint_mssn_strt_tm = live_mssn_strt_tm
            self._had_cleaning_phase = True
            # v2.8.2 bug-hunt fix — see _async_save_mission_checkpoint()
            # docstring for why this must be restored, not left at the
            # post-__init__ default of 0.
            self._last_stuck_count = checkpoint.get("last_stuck_count", 0)
            # v3.2.1 DOCK-ANCHOR — restore the full buffering state, not
            # just a boolean: a stuck event before the HA restart must
            # resume with its buffered segment intact, not silently lose
            # it (which discarding it here would do — worse than the old
            # flag-only version, which at least didn't have data to lose).
            self._dock_anchor_buffering = checkpoint.get("dock_anchor_buffering", False)
            self._pending_segment_points = list(checkpoint.get("pending_segment_points", []))
            self._pending_segment_thetas = list(checkpoint.get("pending_segment_thetas", []))
            self._last_dock_anchor_index = checkpoint.get("last_dock_anchor_index", 0)
            renderer_state = checkpoint.get("renderer_state")
            if self._renderer is not None and renderer_state:
                self._renderer.restore_state(renderer_state)
            _LOGGER.debug(
                "Map: resumed mission from checkpoint after restart — "
                "%d pose pt(s), %d stuck pt(s)",
                len(self._mission_points), len(self._stuck_mission_points),
            )
        else:
            _LOGGER.debug(
                "Map: checkpoint orphaned (mission ended or changed while "
                "HA was down) — salvaging %d pose pt(s)",
                len(checkpoint.get("mission_points", [])),
            )
            self._salvage_orphaned_checkpoint(checkpoint)

    def _salvage_orphaned_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Process an orphaned checkpoint exactly once via _handle_mission_end().

        Reuses _handle_mission_end() directly by temporarily loading the
        checkpoint's data into self._mission_points etc. — safe because at
        this point (entity just started, before the current message's own
        phase-transition handling has run) those attributes are still at
        their fresh __init__ defaults. _handle_mission_end() always clears
        them back to empty/None at the end and also deletes the now
        -consumed checkpoint file, so no explicit cleanup is needed here.

        ending_phase="" — matching the existing stuck-and-abandoned
        ("stop") case, since we don't actually know whether this mission
        made it back to the dock before HA went down. _dock_return inside
        _handle_mission_end() is False for any phase outside
        {"charge", "hmPostMsn"}, so drift detection is correctly skipped.
        """
        self._mission_points = list(checkpoint.get("mission_points", []))
        self._mission_thetas = list(checkpoint.get("mission_thetas", []))
        self._stuck_mission_points = list(checkpoint.get("stuck_mission_points", []))
        self._mission_start_ts = checkpoint.get("mission_start_ts")
        renderer_state = checkpoint.get("renderer_state")
        if self._renderer is not None and renderer_state:
            self._renderer.restore_state(renderer_state)
        self._handle_mission_end(ending_phase="")

    async def _async_save_mission_checkpoint(self) -> None:
        """Persist the in-progress mission so a stuck-then-interrupted
        mission doesn't silently lose its accumulated exploration data.

        Idempotent — safe to call repeatedly during the same mission (e.g.
        on every stuck event); each call overwrites the previous checkpoint
        for this config entry in place. Does NOT feed ZoneStore/GeometryStore
        /GridStore/OutlineStore — those still only run once, at a genuine
        mission end (normal or salvaged), to avoid double-counting.
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        await store.async_save({
            "mssn_strt_tm": self._mission_checkpoint_mssn_strt_tm,
            "mission_points": list(self._mission_points),
            "mission_thetas": list(self._mission_thetas),
            "stuck_mission_points": list(self._stuck_mission_points),
            "mission_start_ts": self._mission_start_ts,
            "renderer_state": self._renderer.dump_state() if self._renderer else None,
            # v2.8.2 bug-hunt fix — without this, a resumed mission would
            # see _last_stuck_count reset to its __init__ default of 0 (the
            # whole entity object is recreated on HA restart), so the very
            # next bbrun message with the robot's already-known nStuck count
            # would look like a brand-new stuck event (n > 0) and append a
            # spurious duplicate marker to _stuck_mission_points.
            "last_stuck_count": self._last_stuck_count,
            # v3.2.1 DOCK-ANCHOR — without this, a resumed mission after
            # an HA restart would lose an in-progress buffered segment
            # entirely (worse than the old flag-only version, which at
            # least had no data to lose) — a stuck event followed
            # immediately by an HA restart would silently discard
            # everything recorded since, instead of resuming buffering
            # and still being able to correct it at the next dock
            # contact.
            "dock_anchor_buffering": self._dock_anchor_buffering,
            "pending_segment_points": list(self._pending_segment_points),
            "pending_segment_thetas": list(self._pending_segment_thetas),
            "last_dock_anchor_index": self._last_dock_anchor_index,
        })
        _LOGGER.debug(
            "Map: saved mission checkpoint — %d pose pt(s), %d stuck pt(s)",
            len(self._mission_points), len(self._stuck_mission_points),
        )

    async def _async_clear_mission_checkpoint(self) -> None:
        """Delete the mission checkpoint — it is now redundant.

        Called from _handle_mission_end(), which covers both a normal
        mission end (the authoritative, complete processing just ran) and
        a salvage call (the checkpoint was just consumed and fed through
        the same method).
        """
        if self._config_entry is None:
            return
        store = Store(
            self.hass,
            _MISSION_CHECKPOINT_STORAGE_VERSION,
            _mission_checkpoint_storage_key(self._config_entry.entry_id),
        )
        await store.async_remove()

    async def _trigger_zone_issue(self) -> None:
        from homeassistant.components import repairs as ir
        ir.async_create_issue(
            self.hass, DOMAIN, "zones_need_naming",
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="zones_need_naming",
        )

    async def _trigger_drift_issue_enriched(self, dx: float, dy: float) -> None:
        """F6d -- fire the map_drift_detected event with bearing/magnitude
        enrichment. v3.5.0 Repairs redesign: demoted from Repair Issue to
        event — DRIFT-AUTO's own self-healing design already treats this as
        transient (see drift_recovered() below), which fits an event/
        Logbook model better than a persistent, must-dismiss Repair."""
        from .repairs import async_enrich_drift_issue
        await async_enrich_drift_issue(self.hass, self._config_entry, dx=dx, dy=dy)

    async def _clear_drift_issue(self) -> None:
        """v3.1.0 DRIFT-AUTO — self-healing: re-arm the map_drift_detected
        event once the recent drift window's mean has dropped back under
        the recovery threshold, so the next fresh occurrence fires again.
        """
        from .repairs import _disarm
        _disarm(self._config_entry.entry_id, "map_drift_detected")

    @staticmethod
    def _blank_image() -> bytes:
        from PIL import Image
        img = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class RoombaCoverageImage(IRobotEntity, ImageEntity):
    """GridStore occupancy grid heatmap — updated at mission end.

    F9 — renders the EMA-weighted GridStore as a PNG heatmap.
    Dark blue = high EMA (frequently visited), light = rarely visited,
    red overlay = stuck hotspot cells.

    EMA diagnostic attributes are exposed during the v2.2 validation period
    to allow users and developers to verify constants are appropriate for their
    cleaning frequency.

    Gate: registered only when data.grid_store is not None (controlled by
    __init__.py — only for map_capability != NONE with map enabled).
    """

    _attr_translation_key = "coverage_map"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_content_type = "image/png"

    def __init__(
        self,
        roomba: Any,
        blid: str,
        grid_store: GridStore,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)
        self._cache: bytes | None = None
        self.access_tokens: collections.deque = collections.deque([], 2)

        self._grid_store = grid_store
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_coverage_map"
        self._attr_image_last_updated: dt_datetime = dt_util.now(
            datetime.timezone.utc
        )

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # v2.6.3 E — listen for GridStore update signal from RoombaMapImage.
        # RoombaMapImage fires the signal after every successful mission end so
        # the frontend knows to re-fetch the coverage image.
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        @callback
        def _on_gridstore_updated() -> None:
            self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
            self._cache = None
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                _SIGNAL_COVERAGE_UPDATED.format(self._config_entry.entry_id),
                _on_gridstore_updated,
            )
        )

    async def async_image(self) -> bytes | None:
        rendered = await self.hass.async_add_executor_job(
            self._grid_store.render_heatmap
        )
        if rendered is None:
            return self._blank_image()
        return rendered

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """EMA diagnostic attributes — exposed during v2.2 validation period."""
        bbox = self._grid_store.bounding_box_mm()
        return {
            "cell_size_mm":      CELL_SIZE_MM,
            "decay":             DECAY,
            "visit_increment":   VISIT_INCREMENT,
            "cell_count":        self._grid_store.cell_count,
            "stuck_event_count": self._grid_store.stuck_event_count,
            "x_min_mm":          bbox[0] if bbox else None,
            "x_max_mm":          bbox[1] if bbox else None,
            "y_min_mm":          bbox[2] if bbox else None,
            "y_max_mm":          bbox[3] if bbox else None,
            "last_mission_end":  self._attr_image_last_updated.isoformat()
                                 if self._attr_image_last_updated else None,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state

    def on_message(self, json_data: dict[str, Any]) -> None:
        """React to MQTT state changes.

        GridStore updates and image_last_updated bumps are handled via the
        _SIGNAL_COVERAGE_UPDATED dispatcher signal (fired by RoombaMapImage
        after each mission end). This callback only triggers HA state refresh
        so the entity stays responsive to phase changes on the dashboard.
        """
        state = json_data.get("state", {}).get("reported", {})
        if not self.new_state_filter(state):
            return
        self.vacuum_state = roomba_reported_state(self.vacuum)
        self.schedule_update_ha_state()

    @staticmethod
    def _blank_image() -> bytes:
        """Return a transparent 400×400 PNG when no grid data exists yet."""
        try:
            from PIL import Image
            img = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
        except ImportError:
            # Pillow absent — return minimal valid PNG (1×1 transparent)
            import base64
            return base64.b64decode(
                b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
                b"AABjkB6QAAAABJRU5ErkJggg=="
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


# v2.3.0 Step 5b — Issue #14 ──────────────────────────────────────────────────

class RoombaRoomsImage(IRobotEntity, ImageEntity):
    """Static room-layout image for xiaomi-vacuum-map-card room selection.

    Renders UmfAligner room polygons onto a dark canvas using Pillow directly —
    no MapRenderer dependency. calibration and rooms attributes use the same
    local to_px() transform as the render so pixel coordinates are consistent.

    Distinct from RoombaMapImage (cleaning history + keepout overlay).
    Preferred source for xiaomi-vacuum-map-card configuration.
    """

    _attr_content_type    = "image/png"
    _attr_translation_key = "rooms_map"
    _attr_entity_category = None

    def __init__(
        self,
        roomba: Any,
        blid: str,
        config_entry: RoombaConfigEntry,
    ) -> None:
        IRobotEntity.__init__(self, roomba, blid)
        self._cache = None
        self.access_tokens: collections.deque = collections.deque([], 2)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_rooms_map"
        self._attr_image_last_updated: dt_datetime = dt_util.now(datetime.timezone.utc)

        # Persisted transform parameters for calibration_points consistency
        self._last_x_min: float = 0.0
        self._last_x_max: float = 1.0
        self._last_y_min: float = 0.0
        self._last_y_max: float = 1.0
        self._last_size:  int   = 600
        # Guard: do not expose calibration/rooms until at least one render has
        # set the transform parameters correctly (avoids wrong coords at startup).
        self._rendered_once: bool = False

        # ZONE-LAYER-CACHE (v2.9.0): room polygons only change on map retrain
        # (new pmap_version_id) or alignment-state transitions — re-running
        # the full PIL render on every async_image() call (every frontend
        # poll/refresh) was wasted work the overwhelming majority of the time.
        # Cache key captures everything that affects the rendered output;
        # _last_x_min/_max/_y_min/_y_max/_last_size are restored from the
        # cache entry too, since other code (calibration_points, _to_px_last)
        # depends on them matching whatever PNG was actually returned.
        self._room_render_cache_key: tuple[Any, ...] | None = None
        self._room_render_cache: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        self.async_update_token()
        # Prime the render immediately on startup so the image and attributes
        # are ready before the frontend first requests them.
        # Aligned path: calibration + rooms attributes populated.
        # Fallback path: UMF-space render visible even before alignment.
        data = self._config_entry.runtime_data
        if data.umf_aligner and data.umf_aligner.room_polygons_umf:
            await self.hass.async_add_executor_job(self._render_rooms_png)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # Cloud entity — no MQTT updates

    async def async_image(self) -> bytes | None:
        """Render room polygons from UmfAligner onto a dark canvas."""
        return await self.hass.async_add_executor_job(self._render_rooms_png)

    def _render_rooms_png(self) -> bytes:
        """CPU-bound render — called via async_add_executor_job.

        Two rendering modes:
        - Aligned (aligner.aligned=True): polygons in pose-space coordinates.
          calibration/rooms attributes are populated. Full xiaomi-card support.
        - Fallback (room_polygons_umf present but not aligned): polygons rendered
          directly in UMF-space coordinates. Image is visible immediately after
          install without requiring missions. calibration/rooms attributes are
          NOT set in this mode — xiaomi-card alignment pending. The image shows
          correct room shapes but may be rotated/mirrored vs. robot orientation.
          Once alignment succeeds (after 2+ missions), the aligned path takes over.
        """
        if self._config_entry is None:
            return self._blank_png()
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if not aligner:
            return self._blank_png()

        polygons_umf = aligner.room_polygons_umf
        if not polygons_umf:
            return self._blank_png()

        aligned = aligner.aligned

        # ZONE-LAYER-CACHE (v2.9.0): room polygons are identical between
        # calls unless the map was retrained (pmap_version_id changes) or
        # alignment state flipped (fallback → aligned after enough missions).
        # Restore both the cached PNG and the transform parameters it was
        # computed with — calibration_points/_to_px_last depend on them
        # matching the returned image exactly.
        cache_key = (aligner.pmap_version_id, aligned)
        # Known limitation: this assumes umf_to_pose()'s rotation/translation
        # is stable for a given pmap_version_id once aligned=True is reached.
        # If a later alignment run meaningfully refines the transform for the
        # same map (not currently expected to happen, but not structurally
        # prevented either), the cached image would be stale until the next
        # map retrain changes pmap_version_id. Matches the scope agreed for
        # ZONE-LAYER-CACHE: invalidate on map retrain, not on every render.
        if (
            self._room_render_cache_key == cache_key
            and self._room_render_cache is not None
        ):
            cached = self._room_render_cache
            self._last_x_min = cached["x_min"]
            self._last_x_max = cached["x_max"]
            self._last_y_min = cached["y_min"]
            self._last_y_max = cached["y_max"]
            self._last_size  = cached["size"]
            if aligned:
                self._rendered_once = True
            else:
                self._rendered_fallback = True
            return cached["png"]



        if aligned:
            # Pose-space path: transform UMF → pose coordinates
            all_coords: list[tuple[float, float]] = []
            for poly_umf in polygons_umf.values():
                for pt in poly_umf:
                    p = aligner.umf_to_pose(*pt)
                    if p:
                        all_coords.append(p)

            def resolve_poly(poly_umf: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
                pts = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                return pts if all(p is not None for p in pts) else None  # type: ignore[return-value]
        else:
            # Fallback: render directly in UMF-space coordinates
            _LOGGER.debug(
                "RoombaRoomsImage: pose alignment pending — rendering in UMF space "
                "(alignment_pending=True, fallback calibration active and accurate)"
            )
            all_coords = [
                pt for poly in polygons_umf.values() for pt in poly
            ]

            def resolve_poly(poly_umf: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
                return poly_umf if len(poly_umf) >= 3 else None

        if not all_coords:
            return self._blank_png()

        margin = 50.0
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        x_min = min(xs) - margin
        x_max = max(xs) + margin
        y_min = min(ys) - margin
        y_max = max(ys) + margin
        size  = 600
        scale = size / max(x_max - x_min, y_max - y_min, 1.0)

        # Store transform for _to_px_last consistency — both aligned and fallback.
        # In fallback mode these are UMF-space values; in aligned mode pose-space.
        # _to_px_last uses whichever was set last, which always matches the
        # coordinate space of the most recent render.
        self._last_x_min = x_min
        self._last_x_max = x_max
        self._last_y_min = y_min
        self._last_y_max = y_max
        self._last_size  = size
        if aligned:
            self._rendered_once = True
        else:
            self._rendered_fallback = True

        def to_px(x: float, y: float) -> tuple[int, int]:
            return (
                int((x - x_min) * scale),
                int(size - (y - y_min) * scale),  # y-flip: HA map convention
            )

        from PIL import Image, ImageDraw
        img  = Image.new("RGB", (size, size), (30, 30, 30))
        draw = ImageDraw.Draw(img)
        # v2.7.3: rid_to_name() lookup removed — labels are no longer drawn
        # into the PNG (XVMC card renders its own from predefined_selections).

        # ROOM-PALETTE (v2.9.0) — rotating per-room fill instead of a single
        # uniform colour, so adjacent rooms are visually distinguishable even
        # without the XVMC card's own room-name overlay. Outline stays fixed
        # (matches existing card highlight colour); only fill rotates.
        # Muted tones chosen to read clearly against the dark (30,30,30) canvas.
        for idx, (rid, poly_umf) in enumerate(polygons_umf.items()):
            resolved = resolve_poly(poly_umf)
            if not resolved:
                continue
            poly_px = [to_px(x, y) for x, y in resolved]
            fill = ROOM_FILL_PALETTE[idx % len(ROOM_FILL_PALETTE)]
            draw.polygon(poly_px, outline=(100, 149, 237), fill=fill)

            # ROOM NAME, off by default. v2.7.3 removed these because the
            # xiaomi-vacuum-map-card renders its own overlay from the
            # `rooms` attribute, and drawing both doubles them up.
            #
            # The option restores them for everyone not using that card:
            # a plain picture-entity shows an image and nothing else, so
            # for those dashboards the names have to be in the picture or
            # they do not exist. Same option as the Prime map uses --
            # this is a preference about maps, not about robot
            # generations.
            if self._config_entry is not None and self._config_entry.options.get(
                CONF_MAP_ROOM_LABELS, DEFAULT_MAP_ROOM_LABELS
            ):
                from .map_renderer import LABEL_FONT  # noqa: PLC0415

                name = aligner.rid_to_name().get(rid)
                if name:
                    cx = sum(x for x, _ in poly_px) / len(poly_px)
                    cy = sum(y for _, y in poly_px) / len(poly_px)
                    draw.text(
                        (cx, cy), name, fill=(230, 230, 230),
                        anchor="mm", font=LABEL_FONT,
                    )
            # v2.7.3: labels removed from PNG — XVMC card renders its own
            # labels from predefined_selections.label.text; drawing them here
            # produced duplicate overlapping labels in the card (veronoicc #2).

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # ZONE-LAYER-CACHE (v2.9.0): store for the next call.
        self._room_render_cache_key = cache_key
        self._room_render_cache = {
            "png": png_bytes,
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "size": size,
        }
        return png_bytes

    def _to_px_last(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        """Reproduce to_px() using persisted transform for attribute consistency."""
        scale = self._last_size / max(
            self._last_x_max - self._last_x_min,
            self._last_y_max - self._last_y_min,
            1.0,
        )
        return (
            int((x_mm - self._last_x_min) * scale),
            int(self._last_size - (y_mm - self._last_y_min) * scale),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose calibration and room polygon data for xiaomi-vacuum-map-card.

        Uses the same local to_px() as _render_rooms_png() so pixel coordinates
        in attributes match the rendered image exactly.

        Aligned mode: calibration + rooms attributes populated for xiaomi-card.
        Fallback mode (not yet aligned): only alignment_pending=True exposed.
          The image is visible but calibration/rooms are withheld because the
          UMF→pose transform is unknown — pixel coords would be meaningless.
        """
        attrs: dict[str, Any] = {}
        if self._config_entry is None:
            return attrs
        data    = self._config_entry.runtime_data
        aligner = data.umf_aligner
        if not aligner:
            return attrs

        polygons_umf = aligner.room_polygons_umf
        if not polygons_umf:
            return attrs

        aligned  = aligner.aligned
        rendered = (
            getattr(self, "_rendered_once", False)      # aligned render done
            or getattr(self, "_rendered_fallback", False)  # fallback render done
        )
        if not rendered:
            return attrs

        if aligned:
            attrs["alignment_pending"] = False
        else:
            # Fallback mode: image is in UMF-space, calibration uses UMF coords.
            # Works with calibration_source: camera: true — the card reads our
            # calibration attribute directly and does not use robot pose coords.
            attrs["alignment_pending"] = True

        # calibration — 3 anchor points mapping vacuum coords → image pixels.
        # Aligned: vacuum coords are pose-space mm (dock-relative).
        # Fallback: vacuum coords are UMF-space units — consistent with the
        #           rendered image so calibration_source: camera: true works.
        all_coords = [pt for poly in polygons_umf.values() for pt in poly]
        if all_coords:
            xs = [c[0] for c in all_coords]
            ys = [c[1] for c in all_coords]
            if aligned:
                # Pose-space anchors via aligner transform
                cal = aligner.calibration_points(self._to_px_last)
                if cal:
                    attrs["calibration_points"] = cal  # XVMC (v2.7.0): renamed
            else:
                # UMF-space anchors — three corners of polygon bounding box.
                # Use actual min/max corners so all three points are within the
                # rendered image area and the card can calibrate correctly.
                anchors = [
                    (min(xs), min(ys)),
                    (max(xs), min(ys)),
                    (min(xs), max(ys)),
                ]
                attrs["calibration_points"] = [  # XVMC (v2.7.0): renamed
                    {
                        "vacuum": {"x": x, "y": y},
                        "map":    {"x": px, "y": py},
                    }
                    for x, y in anchors
                    for px, py in [self._to_px_last(x, y)]
                ]

        # rooms — dict {name: {outline:[[x,y],...], name, icon, x, y}}
        # XVMC (v2.7.0): dict keyed by display name; outline uses [x,y] arrays.
        # In fallback mode polygon vertices are in UMF-space — consistent with
        # the fallback calibration so the card overlays them correctly.
        cc = self._config_entry.runtime_data.cloud_coordinator
        rid_to_type = (
            {r["id"]: r.get("region_type", "default") for r in cc.regions}
            if cc is not None else {}
        )
        rid_to_name = aligner.rid_to_name()
        rooms: dict[str, dict[str, Any]] = {}
        for rid, poly_umf in polygons_umf.items():
            if aligned:
                poly_coords = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                if not all(p is not None for p in poly_coords):
                    continue
            else:
                poly_coords = poly_umf  # type: ignore[assignment]
            if not poly_coords:  # Bug 6 fix: guard against empty polygon
                continue
            room_name = rid_to_name.get(rid, rid)
            # XVMC-COORDS: outline and centroid in vacuum mm (pose or UMF space).
            # XVMC applies calibration (vacuum mm → display px) itself.
            cx = sum(x for x, _ in poly_coords) / len(poly_coords)
            cy = sum(y for _, y in poly_coords) / len(poly_coords)
            icon = REGION_TYPE_ICONS.get(
                rid_to_type.get(rid, "default"), REGION_TYPE_ICONS["default"]
            )
            rooms[room_name] = {
                "outline": [[x, y] for x, y in poly_coords],
                "name":    room_name,
                "room_id": room_slug(room_name),  # v2.7.3: ASCII slug for XVMC id
                "icon":    icon,
                "x":       cx,
                "y":       cy,
            }
        if rooms:
            attrs["rooms"] = rooms

        # ZONE-OVERLAY (v3.3.1) + F24 — only meaningful in aligned mode:
        # zones/door_markers/furniture_candidates are all pose-space (or
        # transformed to pose-space), which only matches the rendered image
        # when aligned=True. In fallback mode the image is UMF-space and
        # these would be spatially wrong if shown, so they're withheld
        # entirely (same reasoning as `rooms`'s aligned/fallback split above).
        if aligned:
            # zones — UMF-space source (observed_zone_centroids, keepout_zones),
            # genuinely needs the aligner transform, unlike door_markers/
            # furniture_candidates below.
            if cc is not None:
                zones: list[dict[str, Any]] = []
                for centroid in cc.observed_zone_centroids:
                    pose_xy = aligner.umf_to_pose(centroid["x"], centroid["y"])
                    if pose_xy is None:
                        continue
                    zones.append({
                        "type": "observed",
                        "x":    pose_xy[0],
                        "y":    pose_xy[1],
                    })
                for zone in cc.keepout_zones:
                    poly_umf = aligner.keepout_polygon_umf(zone)
                    if not poly_umf:
                        continue
                    poly_pose = [aligner.umf_to_pose(x, y) for x, y in poly_umf]
                    if not poly_pose or not all(p is not None for p in poly_pose):
                        continue
                    zones.append({
                        "type":    "keepout",
                        "polygon": [[x, y] for x, y in poly_pose],
                    })
                if zones:
                    attrs["zones"] = zones

            # door_markers — already pose-space mm (collected directly from
            # self._mission_points / RoomSegStore.doors, never through UMF)
            # — exposed as-is, NOT through umf_to_pose(). Known caveat:
            # markers accumulate across missions and are not re-corrected by
            # GeometryStore.record_drift()/drift_recovered() (those only
            # track drift magnitude for the Repair Issue), so a marker's
            # median position can lag behind a large drift correction
            # between missions — same open-ended caveat class as
            # observed_zone centroids' Q6 note, not treated as a blocker.
            geometry_store = getattr(data, "geometry_store", None)
            if geometry_store is not None and geometry_store.door_markers:
                attrs["door_markers"] = [
                    {
                        "id":            m.id,
                        "cx":            m.cx,
                        "cy":            m.cy,
                        "label":         m.label,
                        "mission_count": m.mission_count,
                    }
                    for m in geometry_store.door_markers
                ]

            # F24 — furniture shadow candidates. GridStore.furniture_
            # candidates()'s x_mm/y_mm come from _cell_to_mm(), the same
            # pose-space family hotspots()/format=hazards already
            # documents — no transform needed, exposed as-is.
            grid_store = getattr(data, "grid_store", None)
            if grid_store is not None:
                candidates = grid_store.furniture_candidates()
                if candidates:
                    attrs["furniture_candidates"] = [
                        {"x_mm": c["x_mm"], "y_mm": c["y_mm"]} for c in candidates
                    ]

        return attrs

    @staticmethod
    def _blank_png() -> bytes:
        """Return a dark 600×600 PNG placeholder."""
        try:
            from PIL import Image
            img = Image.new("RGB", (600, 600), (30, 30, 30))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            import base64
            return base64.b64decode(
                b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
                b"AABjkB6QAAAABJRU5ErkJggg=="
            )

class PrimeRoomsImage(IRobotEntity, ImageEntity):
    """V4/Prime room map: polygons drawn, names exposed as attributes.

    BUILT THE SAME WAY AS RoombaRoomsImage, on purpose. The canvas
    colour, the rotating ROOM_FILL_PALETTE fill, the fixed outline
    colour, the auto-fit transform and the attribute shape the
    xiaomi-vacuum-map-card expects are all reused rather than
    reimplemented.

    NAMES ARE NOT DRAWN INTO THE IMAGE. That is easy to get backwards --
    "a room map with names" sounds like labels in the picture. Classic
    removed them in v2.7.3 precisely because the card renders its own
    overlay from `rooms`, and drawing both doubles them up. The rotating
    fill colours are what keep adjacent rooms distinguishable without
    labels.

    WHAT PRIME DOES NOT NEED. Classic has to fit its cloud UMF map onto
    the robot's pose coordinate space first, which is what UmfAligner
    and the whole `aligned` state exist for. Prime's polygons arrive
    already in the robot's own coordinates, so there is no transform and
    no alignment gate -- which is also why this entity is available
    immediately rather than after the first mission.
    """

    _attr_has_entity_name  = True
    _attr_content_type     = "image/png"
    _attr_translation_key  = "rooms_map"
    _attr_entity_category  = None

    def __init__(
        self, blid: str, config_entry: RoombaConfigEntry, hass: Any
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        # hass IS PASSED IN, not read off the config entry. ConfigEntry
        # has no `hass` attribute, so `config_entry.hass` raises
        # AttributeError -- and an exception in a constructor means the
        # entity is never created.
        ImageEntity.__init__(self, hass)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_rooms_map"
        self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)
        self._polygons: dict[str, list[tuple[float, float]]] = {}
        self._names: dict[str, str] = {}
        self._floor_plan: Any = None
        self._live_bundle: Any = None
        self._preferences: dict[str, Any] = {}
        self._renderer: Any = None
        self._png: bytes | None = None
        self._live_dirty = False
        self._last_live_state_write = 0.0

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug. With has_entity_name plus a
        translation_key, HA would otherwise derive the entity_id from the
        TRANSLATED name and produce different ids per language."""
        return "rooms_map"

    @property
    def available(self) -> bool:
        """Unavailable until rooms have been read.

        Honest rather than a blank canvas: a robot that has not finished
        mapping has no rooms, and an empty image looks like a fault.
        """
        return super().available and bool(self._polygons)

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        await self._async_refresh_rooms()

        # LIVE BUNDLES from the other Prime image entity, which watches
        # the map stream. The two are split by capability: that one has
        # the stream and no renderer, this one has the renderer and no
        # stream.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                _SIGNAL_PRIME_LIVE_BUNDLE.format(self._config_entry.entry_id),
                self._on_live_bundle,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                _SIGNAL_PRIME_LIVE_POSITION.format(self._config_entry.entry_id),
                self._on_live_position,
            )
        )

        # NO SUBSCRIPTION, and no re-render per image request.
        #
        # Both were tried and both are wrong for this entity.
        #
        # A status-coordinator subscription fires on every shadow change
        # -- battery percent, phase, dock state -- and each one would
        # trigger a get_map_metadata() call. A cloud request per battery
        # percentage point, for data that changes when somebody renames
        # a room.
        #
        # Classic's rooms map re-renders on every image request instead,
        # which is free there: its polygons already sit in runtime_data.
        # Prime's require a cloud call, so the same approach would hit
        # iRobot's servers for as long as a dashboard stays open.
        #
        # What actually invalidates this image is the MAP VERSION. The
        # robot re-versions its map when the geometry changes, so that is
        # the thing worth watching -- and it is already being read for
        # room cleaning. Checked when the image is requested, and the
        # cloud is only called when it has actually moved.
        self._rendered_for_map_version: str | None = None
        #: Which map that version belongs to. Without it, a robot moving
        #: between floors would render a different map while the version
        #: it is compared against stays put.
        self._rendered_map_id: str | None = None

    async def _async_refresh_rooms(self) -> None:
        """Reads the current map's rooms and renders them."""
        from .prime_room_map import async_build_prime_room_polygons
        from .room_cleaning import async_get_room_cleaning_backend

        backend = async_get_room_cleaning_backend(self._config_entry, self.hass)
        if backend is None:
            return
        map_ids = await backend._all_map_ids()  # noqa: SLF001
        if not map_ids:
            return

        # One image per entity, so the current map wins where the robot
        # says which one it is on; otherwise the first is as good a
        # choice as any, since with one map there is no ambiguity.
        # A USER CHOICE OUTRANKS THE ROBOT'S OWN REPORT, and only when
        # it is still a map this account has -- a map deleted in the app
        # must not leave the image stuck on nothing.
        chosen = getattr(self._config_entry.runtime_data, "prime_selected_map_id", None)
        if chosen in map_ids:
            p2map_id = chosen
        else:
            current = await backend._current_map_id()  # noqa: SLF001
            p2map_id = current if current in map_ids else map_ids[0]

        polygons, names, preferences = await async_build_prime_room_polygons(
            self._config_entry, p2map_id
        )


        # The floor plan is a SECOND cloud call, and a failure costs only
        # the walls and carpet -- the rooms are what the map is for.
        from .prime_room_map import (  # noqa: PLC0415
            PrimeFloorPlan,
            async_build_prime_floor_plan,
        )

        # The bundle link needs the map VERSION, which _all_map_ids()
        # does not carry -- it returns ids. Asked for separately rather
        # than widening that helper, whose callers all want ids.
        version = ""
        try:
            robot = self._config_entry.runtime_data.prime_robot
            versions = await robot.get_active_map_versions() or []
            record_success("map version read")
            for entry in versions:
                if entry.get("p2map_id") == p2map_id:
                    version = entry.get("active_p2mapv_id") or ""
                    break
        except Exception:  # noqa: BLE001
            record_failure("map version read", "listing active map versions")
            _LOGGER.debug("Prime map: could not read map versions", exc_info=True)

        floor_plan = (
            await async_build_prime_floor_plan(self._config_entry, p2map_id, version)
            if version
            else PrimeFloorPlan(
                room_names={}, room_polygons={}, borders=[], carpet=[], dock=None
            )
        )

        # BUNDLE NAMES WIN where present.
        #
        # get_map_metadata() is not always complete: one tester's robot
        # returns four rooms with only three names, while his iRobot app
        # shows all four. The bundle's rooms layer is what the app
        # appears to read.
        #
        # Metadata names stay as the fallback -- they are confirmed on
        # three accounts, and the bundle layer is not guaranteed to
        # exist. Only a name actually present in the bundle replaces one,
        # so a robot without that layer is unaffected.
        for room_id, name in (floor_plan.room_names or {}).items():
            if room_id in names:
                names[room_id] = name

        # OUTLINES FROM THE BUNDLE, where the metadata has none.
        #
        # On several robots get_map_metadata() returns names and cleaning
        # defaults per room but no geometry at all -- so `_polygons`
        # stayed empty and the rooms map reported itself unavailable,
        # permanently and without explanation.
        #
        # Confirmed on three models by three testers, each arriving at
        # rooms.geojson independently.
        #
        # Metadata geometry still wins where it exists: it is what this
        # entity was built on and is confirmed working elsewhere. The
        # bundle fills the gap rather than replacing the source.
        for room_id, ring in (floor_plan.room_polygons or {}).items():
            polygons.setdefault(room_id, ring)
            names.setdefault(
                room_id,
                (floor_plan.room_names or {}).get(room_id) or f"Room {room_id}",
            )
        if not polygons:
            # A post-mission shadow can announce a new map version before
            # its bundle is available. Do not turn a working map unavailable
            # just because this transient refresh has no geometry.
            _LOGGER.debug(
                "Prime rooms map: refresh returned no geometry; keeping cached map"
            )
            return

        self._polygons = polygons
        self._names = names
        self._preferences = preferences
        self._floor_plan = floor_plan
        if self._polygons:
            self._png = await self.hass.async_add_executor_job(self._render_png)
            self._attr_image_last_updated = dt_util.now(datetime.timezone.utc)

    @staticmethod
    def _draw_zone_layer(draw, to_px, layer, *, outline, scale=1000.0) -> None:
        """Outlines every polygon of one zone layer.

        Outline only, never filled: a filled zone hides the room under
        it, and the rooms are what the map is for. A reader needs to see
        that a zone is there and where its edge runs, not to have it
        painted over the floor.
        """
        for feature in (layer or {}).get("features") or []:
            coords = (feature.get("geometry") or {}).get("coordinates")
            for ring in coords or []:
                try:
                    # The scale is passed in rather than imported: this
                    # module reads METRES_TO_MM through a late import
                    # inside another method, and a static helper cannot
                    # see it.
                    points = [
                        to_px(float(x) * scale, float(y) * scale)
                        for x, y in ring
                    ]
                except (TypeError, ValueError):
                    continue
                if len(points) >= 3:
                    draw.polygon(points, outline=outline)

    def _dock_position(self) -> tuple[float, float] | None:
        """Where to draw the dock: seen beats remembered.

        The map bundle's `dockPose` records where the dock stood when
        the map was BUILT, and nothing corrects it when the dock moves.
        @utkjmitch's map put it on a spot now occupied by a treadmill
        while the iRobot app showed it correctly -- so the app reads
        something fresher, and `dockPose` is the only dock data the
        bundle carries.

        A robot reporting `charge` is standing on the dock, and its
        position comes through the same stream in the same coordinates.
        That observation wins when it exists.

        The bundle stays as the fallback rather than being dropped: it
        is available immediately, on every account, without waiting for
        a mission to end. A remembered dock in roughly the right place
        beats no dock at all, which is what a fresh install would
        otherwise show.
        """
        observed = getattr(
            self._config_entry.runtime_data, "prime_observed_dock", None
        )
        if isinstance(observed, (tuple, list)) and len(observed) >= 2:
            return (float(observed[0]), float(observed[1]))
        if self._floor_plan.dock is not None:
            dx, dy, _orientation = self._floor_plan.dock
            return (dx, dy)
        return None

    def _render_png(self) -> bytes:
        """Draws the polygons, reusing the Classic renderer's transform."""
        from PIL import Image, ImageDraw  # noqa: PLC0415

        from .map_renderer import MapRenderer, RendererConfig  # noqa: PLC0415
        from .prime_room_map import METRES_TO_MM, rings_mm  # noqa: PLC0415

        if self._renderer is None:
            self._renderer = MapRenderer(RendererConfig(), None, None)

        # Seed the auto-fit transform from the room extents. Classic gets
        # this from accumulated poses; Prime has the polygons up front,
        # which is why its map is complete before the first mission.
        rings = [
            *self._polygons.values(),
            *self._floor_plan.carpet,
            *self._floor_plan.borders,
        ]
        self._renderer._points = [  # noqa: SLF001
            self._renderer._mm_to_px(x, y)  # noqa: SLF001
            for ring in rings
            for x, y in ring
        ]
        _, _, _, fit_scale, fit_cx, fit_cy = self._renderer._compute_fit()  # noqa: SLF001
        self._renderer._fit_scale = fit_scale  # noqa: SLF001
        self._renderer._fit_cx = fit_cx  # noqa: SLF001
        self._renderer._fit_cy = fit_cy  # noqa: SLF001

        size = self._renderer._cfg.size_px  # noqa: SLF001
        img = Image.new("RGB", (size, size), (30, 30, 30))
        draw = ImageDraw.Draw(img)

        to_px = self._renderer._mm_to_px_fit  # noqa: SLF001
        live_bundle = self._live_bundle or {}
        live_coverage = rings_mm(live_bundle.get("coverage"))

        # LAYER ORDER MATTERS, bottom to top: rooms, then carpet, then
        # walls, then the dock, then labels.
        #
        # Carpet over rooms because it is a property OF a room, and a
        # room drawn on top would hide it. Walls over both because they
        # bound everything. The dock over walls because it sits against
        # one. Labels last so nothing covers them.
        for idx, ring in enumerate(self._polygons.values()):
            draw.polygon(
                [to_px(x, y) for x, y in ring],
                outline=(100, 149, 237),
                fill=ROOM_FILL_PALETTE[idx % len(ROOM_FILL_PALETTE)],
            )

        for ring in self._floor_plan.carpet:
            # Outline only, no fill: a filled overlay would flatten the
            # per-room colours that keep adjacent rooms distinguishable.
            draw.polygon([to_px(x, y) for x, y in ring], outline=(150, 120, 80))

        # The live bundle is the data the app uses for cleaned coverage,
        # trajectories and hazards. Keep the static-map fit: these layers
        # share its coordinate frame and must not move the room layout.
        for ring in live_coverage:
            draw.polygon([to_px(x, y) for x, y in ring], fill=(120, 190, 145))

        for ring in self._floor_plan.borders:
            # OUTLINE ONLY, for the reason written two lines above about
            # carpet and then not applied here.
            #
            # Borders are MultiPolygon AREAS, not thin walls. Filling
            # them paints a solid grey slab over the rooms underneath --
            # @jouwdan sent before/after screenshots of exactly that
            # (PR #64), with the room colours completely hidden.
            #
            # The wall is the outline. Drawing it as one shows the same
            # geometry without covering anything.
            draw.polygon([to_px(x, y) for x, y in ring], outline=(90, 90, 90))

        # THE BUNDLE'S TRAJECTORIES FILL A GAP, they do not compete with
        # the live trail.
        #
        # Our own trail only exists while Home Assistant is watching. A
        # restart mid-mission loses it; the bundle has the whole path,
        # because iRobot recorded it. That is what this layer is for.
        #
        # But it also carries the PREVIOUS mission until the robot
        # uploads a new bundle, which is why @chairstacker saw the old
        # route persist as a thin line under the new one and could only
        # clear it by reloading. Our trail had already been cleared; the
        # bundle brought the old path back.
        #
        # So: once we have points of our own, they are the better
        # answer and this layer stands down. With none -- a fresh start,
        # a restart mid-run -- the bundle fills in.
        have_own_trail = bool(
            getattr(self._config_entry.runtime_data, "prime_positions", None)
        )
        for feature in (
            [] if have_own_trail
            else (live_bundle.get("trajectories") or {}).get("features") or []
        ):
            coords = (feature.get("geometry") or {}).get("coordinates")
            try:
                points = [
                    to_px(float(x) * METRES_TO_MM, float(y) * METRES_TO_MM)
                    for x, y in coords
                ]
            except (TypeError, ValueError):
                continue
            if len(points) >= 2:
                draw.line(points, fill=(80, 150, 235), width=2)

        # ZONES, EACH BEHIND ITS OWN TICK BOX and all off by default.
        #
        # @chairstacker asked for exactly this shape: "my map in the app
        # is getting a little busy but it's still readable; the room map
        # would face the same issue as soon as the zones are being
        # displayed permanently. So, maybe there's a possibility for
        # making their display optional."
        #
        # Three options rather than one, because the three answer
        # different questions -- where the robot should go, where it must
        # not, and where it must not mop -- and wanting keep-out zones on
        # screen does not imply wanting clean zones too.
        options = self._config_entry.options
        # ZONES COME FROM WHICHEVER BUNDLE HAS THEM.
        #
        # The live bundle arrives on a map-update message -- only while a
        # mission runs. @chairstacker ticked all three boxes, reloaded
        # twice and saw nothing, which was correct behaviour and useless
        # to him: zones barely change, and nobody inspects their keep-out
        # areas mid-clean.
        #
        # The floor plan's bundle is fetched whenever the room map is
        # built and carries the same three files.
        def _zone_layer(name: str) -> Any:
            layer = live_bundle.get(name)
            if layer:
                return layer
            return (getattr(self._floor_plan, "zone_layers", None) or {}).get(name)

        if options.get(CONF_MAP_CLEAN_ZONES, DEFAULT_MAP_ZONES):
            self._draw_zone_layer(
                draw, to_px, _zone_layer("cleanZones"),
                outline=(120, 200, 120),
            )
            # Ad-hoc zones are clean zones the user drew for one run.
            # Same colour, because they mean the same thing to a reader.
            self._draw_zone_layer(
                draw, to_px, _zone_layer("adHocCleanZones"),
                outline=(120, 200, 120),
            )

        keepout = options.get(CONF_MAP_KEEPOUT_ZONES, DEFAULT_MAP_ZONES)
        nomop = options.get(CONF_MAP_NOMOP_ZONES, DEFAULT_MAP_ZONES)
        if keepout or nomop:
            # One raw list holds both kinds, told apart by `type`. The
            # only two real values are KeepOutZone and NoMopZone -- a
            # virtual wall is a KeepOutZone with a thin geometry, not a
            # third type.
            for feature in (_zone_layer("policyZones") or {}).get("features") or []:
                zone_type = str(
                    (feature.get("properties") or {}).get("type") or ""
                )
                if zone_type == "KeepOutZone" and not keepout:
                    continue
                if zone_type == "NoMopZone" and not nomop:
                    continue
                if zone_type not in ("KeepOutZone", "NoMopZone"):
                    # An unrecognised type is drawn only when both boxes
                    # are ticked -- it belongs to neither, and guessing
                    # which box governs it would hide it from somebody
                    # who asked to see everything.
                    if not (keepout and nomop):
                        continue
                self._draw_zone_layer(
                    draw, to_px, {"features": [feature]},
                    outline=(220, 120, 120) if zone_type == "KeepOutZone"
                    else (200, 160, 220),
                )

        for feature in (live_bundle.get("hazard") or {}).get("features") or []:
            coords = (feature.get("geometry") or {}).get("coordinates")
            try:
                px, py = to_px(
                    float(coords[0]) * METRES_TO_MM,
                    float(coords[1]) * METRES_TO_MM,
                )
            except (TypeError, ValueError, IndexError):
                continue
            draw.regular_polygon((px, py, 8), 3, fill=(240, 150, 60))

        # THE TRAIL, on top of the floor plan and under the labels.
        #
        # Fed from the live map stream, which the OTHER Prime image
        # entity watches -- the positions are collected into
        # runtime_data because the entity receiving them has no renderer
        # and the one that draws has no stream.
        #
        # Drawn as a polyline rather than through MapRenderer.add_pose:
        # that path maintains its own bounds and coordinate frame, and
        # this map is already anchored on the room polygons. Two frames
        # for one picture would misplace one of them.
        positions = getattr(
            self._config_entry.runtime_data, "prime_positions", None
        ) or []
        if len(positions) >= 2:
            trail: list[tuple[float, float]] = []
            previous: tuple[float, float] | None = None
            for x_mm, y_mm, _deg in positions:
                # Same 500 mm jump rejection the Classic renderer uses.
                # A relocalisation would otherwise draw a straight line
                # across the whole home.
                if previous is not None:
                    dx, dy = x_mm - previous[0], y_mm - previous[1]
                    if (dx * dx + dy * dy) ** 0.5 > 500.0:
                        if len(trail) >= 2:
                            draw.line(trail, fill=(120, 200, 255), width=2)
                        trail = []
                        previous = (x_mm, y_mm)
                        continue
                trail.append(to_px(x_mm, y_mm))
                previous = (x_mm, y_mm)
            if len(trail) >= 2:
                draw.line(trail, fill=(120, 200, 255), width=2)

        # SEEN BEATS REMEMBERED -- and `_dock_position()` was written to
        # say so and never called.
        #
        # `dockPose` records where the dock stood when the map was
        # BUILT. @utkjmitch's map drew it on a spot now occupied by a
        # treadmill while the iRobot app showed it correctly, so the app
        # reads something fresher.
        #
        # He reported that correction as unreachable in a frozen-shadow
        # state, which is true -- and it was unreachable in every state,
        # because nothing invoked it. Found by listing private helpers
        # whose name appears exactly once in the component.
        dock_xy = self._dock_position()
        if dock_xy is not None:
            # THE SAME WHITE RING AS THE ROBOT, for the same reason.
            #
            # The dock reads clearly against the trail (a difference of
            # 245 across three channels) and much less so against a
            # cleaned area: yellow on the coverage green scores 145, and
            # the dock's own room is exactly the area that gets covered.
            #
            # 145 is not the 90 that made the robot invisible, so this
            # is not a repeat of that bug -- but a marker whose
            # legibility depends on whether the room has been cleaned
            # yet is one worth making unconditional. The ring costs
            # nothing and works over any hue underneath.
            # A SQUARE, so shape alone says which marker this is.
            #
            # Dock and robot used to be circles of 12 and 14 pixels on a
            # 600-pixel render -- about five and six pixels on a typical
            # dashboard card. Indistinguishable by size, and a viewer
            # who spots one dot still cannot tell which it is.
            px, py = to_px(*dock_xy)
            draw.rectangle(
                (px - 8, py - 8, px + 8, py + 8),
                fill=(200, 200, 90), outline=(255, 255, 255), width=2,
            )

        # THE ROBOT LAST, and with a ring around it.
        #
        # Two testers independently reported the marker "not working" on
        # a20. It was being drawn the whole time -- in (60, 170, 255),
        # against a trail in (120, 200, 255), which is a difference of 90
        # across all three channels out of a possible 765. The dock, at
        # (200, 200, 90), differs from the trail by 245 and both of them
        # could see that immediately.
        #
        # So this was never a rendering bug: a small circle in nearly the
        # trail's own colour, sitting at the end of that trail, is
        # invisible. Measured rather than guessed at, because "I cannot
        # see it" and "it is not there" look identical in a screenshot.
        #
        # The white ring is what does the work. It separates the marker
        # from anything it happens to sit on, whatever hue that is --
        # trail, coverage fill, a room, or the dock. Drawing the robot
        # after the dock matters for the same reason: a docked robot used
        # to disappear underneath it.
        if positions:
            x_mm, y_mm, heading = positions[-1]
            px, py = to_px(x_mm, y_mm)
            # BIG ENOUGH TO SURVIVE BEING SCALED DOWN.
            #
            # At radius 7 this was fourteen pixels on a 600-pixel
            # render: under six on a 250-pixel dashboard card, and about
            # four and a half on the card @chairstacker screenshotted.
            # At that size a two-pixel ring is half a pixel, so contrast
            # had nothing left to work with -- which is why he still
            # could not make out the robot after the colour was fixed.
            #
            # Radius 11 gives twenty-two pixels, roughly nine on a
            # 250-pixel card. Large enough to read, small enough not to
            # swallow a room on a compact map.
            draw.ellipse(
                (px - 11, py - 11, px + 11, py + 11),
                fill=(20, 110, 220), outline=(255, 255, 255), width=3,
            )
            radians = math.radians(heading)
            draw.line(
                (px, py, px + math.cos(radians) * 20, py - math.sin(radians) * 20),
                fill=(255, 255, 255), width=4,
            )

        # ROOM LABELS ARE OFF BY DEFAULT, which reads backwards until you
        # know what Classic does: it removed its own in v2.7.3 because
        # the xiaomi-vacuum-map-card draws an overlay from the `rooms`
        # attribute, and both at once doubles them up.
        #
        # The option exists for everyone not using that card -- a plain
        # picture-entity shows an image and nothing else, so for them the
        # names have to be in the picture or they do not exist.
        if self._config_entry.options.get(
            CONF_MAP_ROOM_LABELS, DEFAULT_MAP_ROOM_LABELS
        ):
            from .map_renderer import LABEL_FONT  # noqa: PLC0415

            for room_id, ring in self._polygons.items():
                name = self._names.get(room_id)
                if not name:
                    continue
                cx = sum(x for x, _ in ring) / len(ring)
                cy = sum(y for _, y in ring) / len(ring)
                draw.text(
                    to_px(cx, cy), name, fill=(230, 230, 230),
                    anchor="mm", font=LABEL_FONT,
                )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def async_image(self) -> bytes | None:
        """Re-reads only when the robot's map version has moved.

        The version check is a shadow read that is already happening for
        other entities, so the common case costs nothing and a genuine
        map change is picked up without a subscription.
        """
        await self._async_refresh_if_map_changed()
        if self._live_dirty and self._polygons:
            self._png = await self.hass.async_add_executor_job(self._render_png)
            self._live_dirty = False
        return self._png

    async def _async_refresh_if_map_changed(self) -> None:
        """Re-reads the rooms when the map ON SCREEN has changed.

        THIS WATCHED p2maps[0] WHILE RENDERING A DIFFERENT MAP.
        _async_refresh_rooms() picks the map the robot says it is
        standing on and only falls back to the first one when that is
        unknown -- so on a two-map account the two could diverge, and
        then:

          - a change to the map being shown went unnoticed, because the
            version being compared belonged to the other map, and the
            image quietly went stale
          - a change to the OTHER map triggered a pointless refresh

        @chairstacker has two maps and edited the older one, which is
        exactly the arrangement where this shows.

        Comparing the id as well as the version matters just as much: a
        robot that moves between floors changes which map is rendered
        without either map's version moving at all.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_status_coordinator", None
        )
        version: str | None = None
        map_id: str | None = None
        if coordinator is not None and coordinator.data:
            current = coordinator.data.get("ro-currentstate") or {}
            mission = current.get("cleanMissionStatus") or {}
            reported = mission.get("p2mapId") or mission.get("p2map_id")
            p2maps = current.get("p2maps") or []
            if isinstance(p2maps, list) and p2maps:
                entries = [e for e in p2maps if isinstance(e, dict)]
                # The same choice _async_refresh_rooms() makes: the map
                # the robot reports, or the first one when it does not
                # report a usable one.
                # Same order of preference as _async_refresh_rooms():
                # the user's choice, then the robot's report, then the
                # first. The two MUST agree -- watching one map while
                # rendering another is the bug this check just had.
                selected = getattr(
                    self._config_entry.runtime_data, "prime_selected_map_id", None
                )
                chosen = next(
                    (e for e in entries if e.get("p2map_id") == selected),
                    None,
                ) or next(
                    (e for e in entries if e.get("p2map_id") == reported),
                    entries[0] if entries else None,
                )
                if chosen is not None:
                    map_id = chosen.get("p2map_id")
                    version = (
                        chosen.get("active_p2mapv_id")
                        or chosen.get("last_p2mapv_ts")
                    )

        unchanged = (
            version is not None
            and version == self._rendered_for_map_version
            and map_id == self._rendered_map_id
        )
        if unchanged:
            return
        await self._async_refresh_rooms()
        self._rendered_for_map_version = version
        self._rendered_map_id = map_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Calibration and room outlines for xiaomi-vacuum-map-card.

        Same keys and same coordinate convention as the Classic rooms
        map, so a card configuration written for one works for the other.
        """
        from .prime_room_map import prime_calibration_points

        if not self._polygons or self._renderer is None:
            return {}

        cal = prime_calibration_points(
            self._polygons,
            self._renderer._mm_to_px_fit,  # noqa: SLF001
        )
        rooms: dict[str, dict[str, Any]] = {}
        for room_id, ring in self._polygons.items():
            name = self._names.get(room_id) or f"Room {room_id}"
            rooms[name] = {
                "outline": [[x, y] for x, y in ring],
                "name": name,
                "room_id": room_id,
            }

        attrs: dict[str, Any] = {"rooms": rooms}
        # Per-room preferences set in the iRobot app, so an automation
        # can honour them rather than override them.
        if self._preferences:
            attrs["room_preferences"] = self._preferences
        if cal:
            attrs["calibration_points"] = cal
        return attrs

    @callback
    def _on_live_bundle(self, bundle: Any) -> None:
        """Marks the Rooms Map dirty when a new live bundle arrives.

        THE MAP NOW UPDATES DURING A MISSION rather than only when the
        map version changes. Contributed by @jouwdan (PR #63), who found
        the second url on MapUpdateMessage.

        If room geometry has not arrived yet, the initial room refresh renders
        the already-cached live data. This avoids a blank entity while the
        static map is loading.

        Rendering happens only when Home Assistant fetches the image.
        This keeps an idle dashboard from consuming CPU for updates it
        will never display.
        """
        self._live_bundle = bundle
        self._mark_live_dirty()

    @callback
    def _on_live_position(self) -> None:
        """Marks the Rooms Map dirty when a live position arrives."""
        self._mark_live_dirty()

    @callback
    def _mark_live_dirty(self) -> None:
        """Advertise new live data at a bounded frontend update rate."""
        self._live_dirty = True
        now = _time_mod.monotonic()
        if (
            now - getattr(self, "_last_live_state_write", 0.0)
            >= _LIVE_MAP_STATE_UPDATE_INTERVAL
        ):
            self._last_live_state_write = now
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()
