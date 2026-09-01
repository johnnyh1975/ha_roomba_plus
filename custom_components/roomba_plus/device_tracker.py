"""Device tracker platform for Roomba+.

v2.9.0 DEVICE-TRACKER. Tier-aware location reporting: the entity writes a
named string straight to its own `state`, so NO fake GPS coordinates are
needed at all — "named location, not a map point", which is what an indoor
robot actually has.

This went through TrackerEntity's `location_name` until issue #54: that
property is deprecated and warns on every route into it (overriding it, or
setting `_attr_location_name`), unsupported from Home Assistant 2027.7.
Since `TrackerEntity.state` only ever returned `location_name` verbatim
when set, writing `state` directly is the same value without the
deprecation — see RoombaDeviceTracker.state.

- SMART robots (i/s/j-series, Braava m6): current room name during an
  active mission — reuses sensor.py's _resolve_smart_tier_room_state(),
  the SAME function RoombaMissionProgress's current_room attribute uses,
  so the two entities always agree.
- EPHEMERAL robots (900-series, e.g. the 980): room/zone-level detection
  is NOT yet wired in here. ZoneStore's gap-based zone splitting (the
  original room-detection mechanism) was found structurally limited for
  robots with dense MQTT pose sampling (confirmed: max inter-sample step
  340mm, far short of the 800mm door-gap threshold — see project notes,
  June 2026) and has since been removed entirely (ROOM-SEG, see
  ROOM_SEGMENTATION_NOTES.md). RoomSegStore (watershed segmentation on
  GridStore) replaced it for room naming/the live map, and could in
  principle fill this extension point too — resolving "which room is the
  robot in right now" from RoomSegStore's room cells + live pose is a
  reasonable next step, just not implemented yet. Deliberately
  isolated in its own function (_resolve_ephemeral_tier_room) so that once
  EPHEMERAL room/zone detection improves, only that one function needs to
  change — nothing else in this platform.

Both tiers: "Angedockt" while docked/idle (NOT "home" — the robot is
always physically at home regardless of dock state; "home" carries a
GPS-zone connotation that doesn't fit here). Raw x_mm/y_mm pose
coordinates are always exposed as attributes (when available) regardless
of tier, for users who want to build their own zone logic externally.
"""
from __future__ import annotations

import logging

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import roomba_reported_state
from .const import MISSION_END_PHASES, POSE_POINT_CM_TO_MM
from .entity import IRobotEntity
from .structural_failures import record_failure, record_success
from .models import ConnectionType, RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# v2.9.0 — these labels ARE the entity's state (see module docstring), so
# they do NOT go through HA's normal translation_key lookup. A tiny manual
# table covers the two fixed labels this platform needs; expand here if
# more languages are needed later.
_DOCKED_LABEL: dict[str, str] = {
    "de": "Angedockt",
    "en": "Docked",
}
#: Shown while the robot reports `stuck`. Its own state, not a place --
#: which is the point: "Cleaning" was actively wrong there.
_STUCK_LABEL: dict[str, str] = {
    "de": "Steckt fest",
    "en": "Stuck",
}

#: Shown when the robot reports an error. Not a place -- which is the
#: point: "Docked" was actively wrong there.
_ERROR_LABEL: dict[str, str] = {
    "de": "Fehler",
    "en": "Error",
}

_ACTIVE_FALLBACK_LABEL: dict[str, str] = {
    "de": "Unterwegs",
    "en": "Cleaning",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: RoombaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the device tracker for this Roomba. Always created — works
    for every robot tier, just with different location granularity."""
    roomba = config_entry.runtime_data.roomba
    blid = config_entry.runtime_data.blid
    async_add_entities([RoombaDeviceTracker(roomba, blid, config_entry)])


class RoombaDeviceTracker(IRobotEntity, TrackerEntity):
    """DEVICE-TRACKER (v2.9.0) — robot location, room-level when available.

    See module docstring for the full tier-aware design and the
    EPHEMERAL-tier extension point.
    """

    _attr_name = None
    _attr_translation_key = "position"
    # TrackerEntity.entity_registry_enabled_default returns False when both
    # mac_address and device_info are None — which is always the case here,
    # since we identify the robot by BLID, not MAC, and deliberately inherit
    # TrackerEntity's device_info=None (device tracker entities should not
    # create device registry entries per HA core design). Without this
    # override the entity is registered but disabled by default, so users
    # never see it in the UI. Confirmed as the root cause of Thonno's report
    # ("I don't seem to have that entity on my i7+") — v2.10.3.
    _attr_entity_registry_enabled_default = True

    def __init__(
        self, roomba: Any, blid: str, config_entry: RoombaConfigEntry
    ) -> None:
        super().__init__(roomba, blid, config_entry)
        self._config_entry = config_entry
        #: Region ids the name merge has already looked at, whether or
        #: not they produced a cache entry. See _handle_coordinator_update.
        self._seen_region_ids: set[str] = set()
        self._attr_unique_id = f"{self.robot_unique_id}_position"
        #: Prime room names, {name: qualified_id}. Empty for Classic,
        #: which resolves names through its own tier-specific paths.
        self._prime_rooms: dict[str, str] = {}

    @property
    def suggested_object_id(self) -> str | None:
        """Override: device tracker keeps device-name-only entity_id.

        This entity sets _attr_name = None, so HA derives its entity_id from
        the device name alone (e.g. device_tracker.roomba_980_og) — there is no
        per-entity name suffix. Returning None here prevents the IRobotEntity
        base implementation from appending "_position" to the entity_id, which
        would change the established naming for both new and existing installs.
        """
        return None

    @property
    def source_type(self) -> SourceType:
        # Locally determined from the robot's own onboard pose estimate —
        # not GPS, not router-presence. ROUTER is the closest existing
        # SourceType to "determined by a local, non-GPS data source";
        # there is no dedicated "robot odometry" source type in HA core.
        return SourceType.ROUTER

    def _label(self, table: dict[str, str]) -> str:
        lang = (self.hass.config.language or "en")[:2]
        return table.get(lang, table["en"])

    @property
    def state(self) -> str | None:
        """Return the robot's location as this entity's state.

        REPORTED AS `state`, NOT `location_name` (issue #54, @mdarocha).

        `location_name` was the original route here, because
        TrackerEntity.state returns it verbatim when it isn't None — which
        is exactly the "named location, not a map point" case an indoor
        robot needs, with no fake GPS coordinates involved.

        Home Assistant now deprecates that property, and warns on BOTH
        routes into it: overriding `location_name` trips a warning in
        TrackerEntity.__init_subclass__, and setting `_attr_location_name`
        trips one in the getter. Both stop working in 2027.7.

        Writing `state` directly sidesteps the deprecation entirely and
        produces the identical value, because returning it from
        `location_name` only ever meant "let TrackerEntity.state return
        this". The lat/lon fallback that `location_name` short-circuited
        is not lost either: this platform never sets latitude/longitude,
        so that branch could only ever have yielded None anyway.

        The room also remains available as the `room` and `area_id` state
        attributes, which is where an automation should read it from —
        those are structured, while this is a display string.
        """
        data = self._config_entry.runtime_data

        # PRIME HAS NO LOCAL ROBOT TO READ A PHASE FROM.
        #
        # @chairstacker (#70): this always said "Docked", whatever the
        # robot was doing. `roomba_reported_state(self.vacuum)` returns
        # `{}` when there is no local robot -- which is every Prime
        # entry -- so `phase` was always "", the guard below always
        # matched, and `_resolve_room()`'s own Prime branch was never
        # reached.
        #
        # The phase lives in the `ro-currentstate` named shadow on
        # Prime, which is where the vacuum entity reads it.
        if data.connection_type is ConnectionType.CLOUD_ONLY:
            coordinator = getattr(data, "prime_status_coordinator", None)
            shadows = getattr(coordinator, "data", None) or {}
            _status = (
                (shadows.get("ro-currentstate") or {}).get("cleanMissionStatus")
                or {}
            )
            phase = _status.get("phase", "")
        else:
            state = roomba_reported_state(self.vacuum)
            _status = state.get("cleanMissionStatus") or {}
            phase = _status.get("phase", "")

        # AN ERROR IS NOT AN ARRIVAL.
        #
        # `stop` is a mission-end phase, and a robot that failed to dock
        # reports exactly that -- so this said "Docked" about a robot
        # flashing red two inches short of its dock, having aborted the
        # final approach (@utkjmitch, error 1010).
        #
        # Last week the same entity said "Cleaning" for nine days about
        # a robot that had stopped transmitting. One root: states
        # derived from what SHOULD follow rather than from what the
        # robot reports.
        #
        # Checked before the mission-end branch because a mission can
        # end BY failing, and the failure is the more important half.
        _err = _status.get("error")
        if isinstance(_err, int) and _err != 0:
            return self._label(_ERROR_LABEL)

        if phase in MISSION_END_PHASES or phase == "":
            return self._label(_DOCKED_LABEL)

        # A STUCK ROBOT IS NOT CLEANING, and saying so for days is worse
        # than saying nothing.
        #
        # `stuck` is not a mission-end phase -- correctly, the mission
        # has not ended -- so this fell through to the active label.
        # @utkjmitch's Combo (Y3-series) went `stuck` at 11:52, drained for six hours,
        # declared battery-low, and stopped transmitting. Nine days
        # later this entity still read "Cleaning", because the last
        # value any entity received simply stays until a new one
        # arrives.
        #
        # Deliberately NOT added to MISSION_END_PHASES: that set means
        # "the mission finished", which drives history and statistics.
        # A stuck robot's mission did not finish. This is a display
        # question, and it belongs here.
        if phase == "stuck":
            return self._label(_STUCK_LABEL)

        room = self._resolve_room(data)
        if room:
            return room
        return self._label(_ACTIVE_FALLBACK_LABEL)

    def _resolve_room(self, data: Any) -> str | None:
        """Tier-dispatch to the right room-resolution strategy."""
        # PRIME FIRST. map_capability is NONE for Prime robots by design
        # (has_smart_map() looks for "pmaps"; Prime reports "p2maps"), so
        # both branches below would miss -- neither "smart" nor
        # ephemeral, and the room would always be None.
        if data.connection_type is ConnectionType.CLOUD_ONLY:
            return self._resolve_prime_room(data)

        if data.map_capability.value == "smart":
            return self._resolve_smart_tier_room(data)
        return self._resolve_ephemeral_tier_room(data)

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)

        # PRIME ONLY. Classic resolves room names through its own paths
        # and needs no cache; Prime's come from a cloud call that cannot
        # happen inside a synchronous attribute read.
        data = self._config_entry.runtime_data
        if data.connection_type is ConnectionType.CLOUD_ONLY:
            await self._async_refresh_prime_rooms()
            coordinator = getattr(data, "prime_coordinator", None)
            if coordinator is not None:
                self.async_on_remove(
                    coordinator.async_add_listener(self._handle_prime_update)
                )

    @callback
    def _handle_prime_update(self) -> None:
        """State first, and refill the name cache when it is still empty.

        THE NAMES ARRIVE AFTER THE ENTITY DOES. `async_added_to_hass`
        fetches the room list once, at setup -- and on a cold start the
        map bundle has usually not been built yet, so the cache stays
        empty and every room falls through to `Room {id}`.

        @chairstacker (#70 follow-up): the tracker started reporting
        rooms in a38 and reported "Room 16". The map check was the fix;
        this is the half that gives them names.

        Retried on coordinator updates rather than on a timer, and only
        while empty -- a robot that genuinely has no named rooms should
        not refetch the list on every message.
        """
        self.async_write_ha_state()
        # "EMPTY" IS THE WRONG GATE. Rooms arrive first, from the map's
        # own metadata; zones arrive later, once `prime_room_names` has
        # been filled by a map build. Refetching only while empty means
        # the cache stops updating the moment the first room lands --
        # so rooms resolve to names and zones stay as `Room 100`
        # forever (@chairstacker).
        #
        # Refetch while either source could still add something: no
        # rooms yet, or the flat name table has entries this cache has
        # not picked up.
        _flat = getattr(
            getattr(
                getattr(self, "_config_entry", None), "runtime_data", None
            ),
            "prime_room_names", None,
        ) or {}
        # COMPARE REGION IDS, not counts. `_prime_rooms` is keyed by
        # NAME and `prime_room_names` by region id, so two regions
        # sharing a name make the flat table permanently larger -- and a
        # count comparison would then refetch on every single
        # coordinator message, forever.
        # REGIONS NOT YET CONSIDERED. Not "not in the cache": the merge
        # legitimately skips some (unnamed, or a name a room already
        # holds), and those would otherwise keep the gate open forever.
        _unseen = {str(rid) for rid in _flat} - getattr(
            self, "_seen_region_ids", set()
        )
        if not self._prime_rooms or _unseen:
            self.hass.async_create_task(self._async_refresh_prime_rooms())

    async def _async_refresh_prime_rooms(self) -> None:
        """Refills the room-name cache.

        Awaited from async_added_to_hass and after a map version change,
        not on every attribute read: the names come from a cloud call and
        change only when someone renames a room in the iRobot app.
        """
        from .room_cleaning import async_get_room_cleaning_backend  # noqa: PLC0415

        try:
            backend = async_get_room_cleaning_backend(self._config_entry, self.hass)
            if backend is not None:
                self._prime_rooms = await backend.available_rooms()

                # ZONES TOO. `available_rooms()` reads `rooms_metadata`,
                # which carries rooms and not zones -- so a zone mission
                # produced a region id matching nothing here and the
                # tracker showed the raw id. @chairstacker (#70), whose
                # own diagnostics carried every zone name.
                #
                # `prime_room_names` is flat and holds both. A room wins
                # a name collision: its name comes from the map's own
                # metadata, a zone's from whatever the last command
                # called it.
                zones = getattr(
                    self._config_entry.runtime_data, "prime_room_names", None
                ) or {}
                for region_id, name in zones.items():
                    if name and str(name) not in self._prime_rooms:
                        self._prime_rooms[str(name)] = str(region_id)
                # WHAT WAS CONSIDERED, not what landed. A region can be
                # skipped above for good reasons -- no name, or a name a
                # room already holds -- and deriving "still missing"
                # from the cache alone would then never settle, so the
                # refetch gate would fire on every coordinator message.
                if not hasattr(self, "_seen_region_ids"):
                    self._seen_region_ids = set()
                self._seen_region_ids |= {str(r) for r in zones}

                # ZONES ARE PLACES THE ROBOT CAN BE.
                #
                # `available_rooms()` reads `rooms_metadata` from each
                # map, which carries rooms and not zones. So a
                # zone-targeted mission produced a region id that
                # matched nothing in this cache and the tracker showed
                # nothing -- not because the zone was unnamed, but
                # because it was never in the list being searched.
                #
                # @chairstacker (#70) assumed the missing zone NAMES in
                # #47 were the cause. They are not: this would still be
                # empty if every zone on his map had a name.
                #
                # `prime_room_names` holds every region the coordinator
                # has seen, rooms and zones together, keyed the same
                # way. Rooms win a collision because they come from the
                # map's own metadata.
                zones = getattr(
                    self._config_entry.runtime_data, "prime_room_names", None
                ) or {}
                for region_id, name in zones.items():
                    if name and str(name) not in self._prime_rooms:
                        self._prime_rooms[str(name)] = str(region_id)

                record_success("prime room names")
        except Exception:  # noqa: BLE001
            record_failure("prime room names", "refreshing room names")
            _LOGGER.debug("roomba_plus: could not refresh Prime room names", exc_info=True)

    def _resolve_prime_room(self, data: Any) -> str | None:
        """The room from the mission timeline's most recent room event.

        NOT from MissionTimerStore.current_room: that is populated by
        set_mission_plan(), which only runs when Home Assistant itself
        started the mission. A robot cleaning on its own schedule -- the
        common case -- would leave it empty, and the tracker would report
        nothing for exactly the missions people care about.

        The timeline is the robot's own account of where it is, and it
        arrives regardless of who started the run. A tester's capture
        shows the event sequence plainly: start, reloc, travel,
        traversal, travel, room, travel, evac, fin.
        """
        coordinator = getattr(data, "prime_coordinator", None)
        report = getattr(coordinator, "data", None)
        if report is None:
            return None

        # Latest room event wins: the timeline accumulates, and the robot
        # moves on. Reading the first would name the room it started in
        # for the whole mission.
        # ZONES TOO, and they are a different field with a different id.
        #
        # A timeline event carries three place fields: `room` and
        # `travel` (both keyed `region_id`) and `zone` (keyed
        # `zone_id`). Only the first was read, so a zone-targeted
        # mission resolved nothing and this entity fell through to its
        # "somewhere, cleaning" label.
        #
        # @chairstacker (#64) reported that with zone names visible in
        # his own diagnostics -- `region_names.merged` held all seven.
        # The lookup was never the problem; there was no id to look up.
        #
        # Worth naming the shape of the mistake: this is the same one as
        # `zone_layers` and `pd_state` this week. The data was present
        # under a field nobody read, and three rounds of explanation
        # called it a protocol gap.
        region_id: str | None = None
        for entry in getattr(report, "event", None) or []:
            room_event = getattr(entry, "room", None)
            if room_event is not None and getattr(room_event, "region_id", None):
                region_id = str(room_event.region_id)
            zone_event = getattr(entry, "zone", None)
            if zone_event is not None and getattr(zone_event, "zone_id", None):
                region_id = str(zone_event.zone_id)

        if not region_id:
            return None

        # region_id is a number; the user cares about the name.
        #
        # CACHED, because the name lookup is a cloud call and this runs
        # from a synchronous property. A first draft called the async room
        # list directly from here -- which would have returned a coroutine
        # object as the room name.
        #
        # The cache is filled from _async_refresh_prime_rooms(), and it
        # holds the same names the cleaning services use, so the tracker
        # and clean_room always agree about what a room is called.
        for name, qualified in self._prime_rooms.items():
            if str(qualified).endswith(f"/{region_id}") or qualified == region_id:
                return name

        # Known region, unknown name -- a room added since the cache was
        # built. The number is worse than nothing for automations but
        # better than silence for a person reading the attribute.
        return f"Room {region_id}"

    def _resolve_smart_tier_room(self, data: Any) -> str | None:
        """SMART-tier room name — delegates to the SAME shared function
        RoombaMissionProgress's current_room attribute uses, so both
        entities always agree on where the robot currently is."""
        from .sensor import _resolve_smart_tier_room_state
        room_state = _resolve_smart_tier_room_state(self._config_entry)
        return room_state.get("current_room")

    def _resolve_ephemeral_tier_room(self, data: Any) -> str | None:
        """EPHEMERAL-tier room/zone resolution — EXTENSION POINT.

        Currently always returns None. The original mechanism this would
        have used (ZoneStore's gap-based zone detection) was found
        structurally limited for robots with dense MQTT pose sampling
        (confirmed June 2026: max inter-sample step 340mm vs. the 800mm
        door-gap threshold — a real doorway is crossed in many small
        steps, never one qualifying gap) and has since been removed
        entirely (ROOM-SEG, see ROOM_SEGMENTATION_NOTES.md). RoomSegStore
        replaced it for room naming/the live map and could fill this
        extension point too (resolve current room from RoomSegStore's
        room cells + live pose) — a reasonable next step, not yet done.
        Nothing else in this platform needs to change: the state
        resolution, the docked check, and the attribute exposure below are
        all tier-agnostic already.
        """
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}


        # Raw pose, always exposed when available, regardless of tier —
        # for users who want to build their own zone logic externally.
        # v2.9.0 units fix: pose.point.x/y is in centimetres, not
        # millimetres (see POSE_POINT_CM_TO_MM in const.py).
        state = roomba_reported_state(self.vacuum)
        pose = state.get("pose")
        if isinstance(pose, dict):
            point = pose.get("point", {})
            x = point.get("x")
            y = point.get("y")
            if x is not None and y is not None:
                attrs["x_mm"] = round(float(x) * POSE_POINT_CM_TO_MM)
                attrs["y_mm"] = round(float(y) * POSE_POINT_CM_TO_MM)

        data = self._config_entry.runtime_data
        mts = getattr(data, "mission_timer_store", None)
        phase = (state.get("cleanMissionStatus") or {}).get("phase", "")
        if (
            mts is not None
            and mts.mission_id is not None
            and phase not in MISSION_END_PHASES
            and phase != ""
        ):
            room = self._resolve_room(data)
            attrs["room"] = room

            # THE HA AREA the room maps to, alongside the robot's own
            # name for it.
            #
            # Added here rather than as a second `room` assignment: a
            # first attempt wrote attrs["room"] earlier in this method
            # with a looser condition, and this block then overwrote it.
            # Two writers for one key, the later one silently winning --
            # and the two disagreed about when a room counts as current.
            #
            # area_id is the one worth automating on: it survives a room
            # being renamed in the iRobot app and is stable across
            # languages. The robot's own name stays because it is what
            # the user sees in the app, and because it is what the
            # deprecated state used to carry.
            area_id = self._async_area_for(room) if room else None
            if area_id:
                attrs["area_id"] = area_id

            if data.map_capability.value == "smart":
                from .sensor import _resolve_smart_tier_room_state
                room_state = _resolve_smart_tier_room_state(self._config_entry)
                attrs["next_room"] = room_state.get("next_room")

        return attrs

    def _async_area_for(self, room: str) -> str | None:
        """The HA area for a room, mapping first and name second.

        The configured segment mapping wins: it is the user's own
        statement of which robot room is which area. Name matching is a
        fallback for the many setups where HA areas and robot rooms are
        simply named the same and nobody opened the mapping dialog.
        """
        from .area_resolver import (  # noqa: PLC0415
            async_area_for_room_name,
            async_area_for_segment,
        )

        try:
            data = self._config_entry.runtime_data

            # PRIME: the segment id is built from the room-name cache,
            # whose values are already "<p2map_id>/<room_id>" -- the same
            # qualified form async_get_segments() encodes. Without this
            # branch the mapping lookup found nothing and it fell through
            # to the name match silently, which works but throws away the
            # explicit mapping the user configured.
            if data.connection_type is ConnectionType.CLOUD_ONLY:
                qualified = self._prime_rooms.get(room)
                if qualified and "/" in str(qualified):
                    _map, _, rid = str(qualified).partition("/")
                    area = async_area_for_segment(
                        self.hass, self._config_entry, f"rid_{rid}"
                    )
                    if area:
                        return area
                return async_area_for_room_name(
                    self.hass, self._config_entry, room
                )

            regions = getattr(
                getattr(data, "cloud_coordinator", None), "regions", None
            ) or []
            for region in regions:
                if str(region.get("name") or "") != room:
                    continue
                pmap = region.get("pmap_id") or ""
                rid = region.get("id") or ""
                area = async_area_for_segment(
                    self.hass, self._config_entry, f"{pmap}_rid_{rid}"
                )
                if area:
                    return area
            return async_area_for_room_name(self.hass, self._config_entry, room)
        except Exception:  # noqa: BLE001
            # NOT INSTRUMENTED: an installation with no Home Assistant
            # areas defined resolves nothing, and that is a normal
            # configuration rather than a defect.
            _LOGGER.debug("roomba_plus: area resolution failed", exc_info=True)
            return None

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state or "pose" in new_state
