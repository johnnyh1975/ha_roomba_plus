"""Room-targeted cleaning, for both robot generations.

WHY A FACADE RATHER THAN A BRANCH IN services.py.

Four services clean named rooms: clean_room, clean_overdue_rooms,
auto_clean_dirty_rooms and smart_start. All four ask the same two
questions -- "what rooms exist and what are they called" and "clean
these rooms" -- and both generations can answer both.

They just answer differently. Classic reads regions off its cloud
coordinator and sends pmap_id/region ids; Prime reads rooms_metadata
off a map and sends p2map_id/region_id plus an initiator. Branching on
that inside each service would put the same two-way decision in four
places, which this project has already paid for once: vacuum.py carried
four identical copies of one transport branch, and a fix that belonged
in one of them went into the wrong one.

So the services ask a backend, and the backend knows which generation
it is. Adding a third generation later touches this file and nothing
else.

A SECOND THING THIS SETTLES. Room cleaning used to be gated on
`map_capability == SMART`, which is a Classic concept -- it describes
whether a Classic robot has persistent pmaps. Prime robots leave it at
NONE (has_smart_map() looks for a "pmaps" key; Prime reports "p2maps"),
so every Prime robot was refused room cleaning even after the transport
was confirmed working on real hardware.

Rather than redefining that flag -- it is read in 32 places across seven
modules, many of them Classic cloud paths that do not exist for Prime --
the question becomes "is there a backend for this robot". Absence of a
backend is the honest answer to "can this robot clean a named room",
and it needs no flag to be interpreted correctly at 32 call sites.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    CONF_SMART_ZONE_LABELS,
    room_slug,
    CONF_FLOOR,
    CONF_SMART_ZONE_DATA,
    DOMAIN,
    MAP_UPDATING_NOT_READY,
)
from .structural_failures import record_failure, record_success
from .models import ConnectionType, MapCapability

if TYPE_CHECKING:
    from .cloud_coordinator import IrobotCloudCoordinator
    from .models import RoombaConfigEntry, RoombaData

_LOGGER = logging.getLogger(__name__)

#: Marks a ZONE inside a region-id list. Rooms carry a bare id.
#: Both backends read this: Classic since v2.7.0, Prime since the
#: clean_zone service exposed zones to callers.
ZID_PREFIX = "zid_"


def match_room_names(
    available: dict[str, str], requested: list[str]
) -> tuple[list[str], list[str]]:
    """Matches user-typed room names against a robot's actual rooms.

    Returns (matched ids, unmatched names), preserving request order --
    clean_overdue_rooms and auto_clean_dirty_rooms sort their lists by
    urgency, and reordering silently discards that work.

    EXTRACTED FROM services.py::_resolve_rooms (this session), which
    could not simply be reused: 51 of its 141 lines deal with pmap ids
    and cross-map conflicts, and it returns (region_id, pmap_id) tuples
    that mean nothing to a Prime robot.

    What IS shared is the name matching itself, and specifically the
    slug fallback. Users type "kuche" for "Küche" and "salle a manger"
    for "Salle à manger" -- accents are awkward on phone keyboards and
    absent from voice assistants entirely. Without this, a German or
    French user's automation fails with "unknown room" for a room that
    plainly exists, which is a miserable thing to debug.

    Case-insensitive first, slug second. Both are exact matches within
    their own space -- deliberately no fuzzy scoring, because cleaning
    the wrong room is worse than reporting an honest miss.
    """
    by_name = {name.casefold(): rid for name, rid in available.items()}
    by_slug = {room_slug(name): rid for name, rid in available.items()}

    # BARE REGION IDS, because that is what the map card sends.
    #
    # @jouwdan (#88): the Rooms Map publishes each room's own id in its
    # `rooms` attribute, and xiaomi-vacuum-map-card sends that id back
    # when somebody taps a room. Matching only on name and slug meant a
    # card selection resolved to nothing -- so the card he made work in
    # an earlier PR could draw rooms and not clean them.
    #
    # AMBIGUOUS IDS ARE REFUSED RATHER THAN GUESSED. A household with
    # several maps can hold the same bare id under two different
    # qualified ids; picking one would clean a room on the wrong floor.
    # An honest miss is better, which is the same rule the name matching
    # above already follows.
    by_id: dict[str, str] = {}
    ambiguous_ids: set[str] = set()
    for qualified in available.values():
        bare_id = qualified.rsplit("/", 1)[-1]
        if bare_id in by_id and by_id[bare_id] != qualified:
            ambiguous_ids.add(bare_id)
        else:
            by_id[bare_id] = qualified

    matched: list[str] = []
    unmatched: list[str] = []
    for raw in requested:
        wanted = raw.strip()
        rid: str | None = (
            by_name.get(wanted.casefold())
            or by_slug.get(room_slug(wanted))
            or (by_id.get(wanted) if wanted not in ambiguous_ids else None)
        )
        if rid is None:
            unmatched.append(raw)
        elif rid not in matched:
            matched.append(rid)
    return matched, unmatched




def _per_room(values: list[Any] | None, index: int) -> Any:
    """One per-room setting, or None when the caller said nothing.

    Positional and possibly short: callers pass what the user supplied,
    which may cover fewer rooms than were requested. Missing entries
    mean "leave the robot's own setting alone" -- not False, which would
    actively switch two-pass off for someone who had turned it on.
    """
    if not values or index >= len(values):
        return None
    return values[index]


def _region_params(
    params_cls: type,
    two_pass: list[bool | None] | None,
    suction_level: list[int | None] | None,
    index: int,
    operating_mode: list[int | None] | None = None,
) -> Any:
    """Per-region params, or None when the caller expressed no opinion.

    None rather than an empty params object: the confirmed field payload
    only ever carried params the app itself had set, and sending an
    empty one is a difference from observed behaviour with no benefit.
    """
    tp = _per_room(two_pass, index)
    sl = _per_room(suction_level, index)
    # PER ROOM LIKE THE OTHER TWO. A caller that wants one room mopped
    # and the rest vacuumed can say so; one that wants the same
    # everywhere passes a single value and gets it applied throughout.
    om = _per_room(operating_mode, index)
    if tp is None and sl is None and om is None:
        return None
    fields = {}
    if om is not None:
        fields["operating_mode"] = om
    if tp is not None:
        fields["two_pass"] = tp
    if sl is not None:
        fields["suction_level"] = sl
    return params_cls(**fields)


class RoomCleaningBackend(ABC):
    """What a service needs in order to clean named rooms."""

    @abstractmethod
    async def available_rooms(self) -> dict[str, str]:
        """Room display name -> the id this backend cleans it by.

        Names as the user set them in the iRobot app, because that is
        what they will type into a service call or automation.
        """

    async def _all_map_ids(self) -> list[str]:
        """Every map this robot holds, or empty when it has only one.

        NOT ABSTRACT, and a default rather than a raise: three callers
        (the rooms image, the calendar, the dirt manager) ask the
        backend for map ids through this base type. `PrimeRoomCleaning`
        answers properly; a Classic robot has a single implicit map and
        nothing to enumerate.

        Those callers used `backend._all_map_ids()  # noqa: SLF001` on a
        method only the Prime subclass defined -- an AttributeError on
        every Classic entry, two of them inside a bare except that
        swallowed it.
        """
        return []

    async def _current_map_id(self) -> str | None:
        """The map the robot is on, or None when it does not track one.

        Same story as `_all_map_ids` above.
        """
        return None

    @abstractmethod
    async def get_segments(self) -> list[Any]:
        """Rooms as Home Assistant Clean Area segments.

        The id format is each backend's own business and is agreed with
        its clean_segments() alone. Classic prefixes with the pmap id so
        segments from another map can be recognised; Prime does not,
        because it resolves the map at send time.

        Both halves of that agreement live in the same class on purpose:
        nothing outside these two methods would notice them drifting
        apart, and for a while they lived in different files.
        """

    @abstractmethod
    async def clean_segments(self, segment_ids: list[str]) -> None:
        """Clean the given Home Assistant segments."""

    @abstractmethod
    async def clean_rooms(
        self,
        room_ids: list[str],
        *,
        ordered: bool = True,
        two_pass: list[bool | None] | None = None,
        suction_level: list[int | None] | None = None,
        operating_mode: list[int | None] | None = None,
    ) -> None:
        """Sends the robot to clean these rooms, in the order given.

        Order is preserved deliberately: clean_overdue_rooms and
        auto_clean_dirty_rooms sort their lists by urgency, and a
        backend that reorders them silently discards that work.

        `ordered` asks the robot to visit them in that sequence rather
        than picking its own route.

        `two_pass` is per room and positional, aligned with room_ids;
        None means "whatever the robot is already set to". A first draft
        of this interface took only room_ids, which would have thrown
        away a capability the Classic path has always had -- and which
        Prime turns out to support identically (the confirmed field
        payload carries twoPass per region).

        `suction_level` is per room as well, and PRIME ONLY -- the
        Classic room payload has no equivalent. A backend that cannot
        honour it ignores it rather than failing, so a caller need not
        know which generation it is talking to.

        DELIBERATELY NOT OFFERED: operatingMode, which the confirmed
        Prime payload also carries. It relates to the fitted mop pad,
        and the compatibility rule lives robot-side where this code
        cannot check it -- our own diagnostic script says as much when
        it reports the value. Exposing a setting whose valid range we
        cannot determine invites a service call that is silently
        rejected, or worse, accepted and wrong. It stays out until
        somebody establishes what the values mean.

        UPDATE (30 July 2026): the values are now known, from a
        chairstacker map-edit response that returned the full
        p2map_metadata. Each room carries operating_mode_defaults keyed
        by mode, plus a last_operating_mode:

            2   -> profile "normal"
            4   -> profile "normal" (seen only with carpetBoost + swScrub)
            32  -> profile "light"
            512 -> profile "deep"  (suctionLevel 4, twoPass, swScrub 1)

        Still not offered, and the reason has changed. It is no longer
        "we do not know the range" but "the robot already stores a
        per-room default for each mode, including which one was used
        last". Overriding that from a service call would discard a
        preference the user set in the iRobot app, per room, and there is
        no way to put it back.

        The useful feature here is not an operating_mode parameter -- it
        is READING these defaults, so a service call can honour what the
        user configured rather than replace it. Not built yet, and
        deliberately noted rather than guessed at.
        """


def _selected_cleaning_mode(config_entry: Any) -> int | None:
    """The mode the cleaning-mode select is showing, if there is one.

    Read through the entity rather than from a stored preference,
    because the select reflects the robot's own last start when it has
    one -- so a user who chose in the iRobot app gets what the machine
    actually did rather than a stale pick of ours.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    hass = getattr(config_entry, "hass", None)
    data = getattr(config_entry, "runtime_data", None)
    if hass is None or data is None:
        return None
    try:
        registry = er.async_get(hass)
        unique = f"{data.blid}_prime_cleaning_mode"
        entity_id = registry.async_get_entity_id("select", DOMAIN, unique)
        if entity_id is None:
            return None
        state = hass.states.get(entity_id)
        if state is None:
            return None
        # LATE ON PURPOSE: select_prime imports back into this module,
        # so a module-level import here is a real cycle -- unlike the
        # other flagged pairs in this project, which were only laziness.
        from .select_prime import PrimeCleaningModeSelect  # noqa: PLC0415

        return PrimeCleaningModeSelect.MODES.get(state.state)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: could not read the cleaning mode", exc_info=True)
        return None


class PrimeRoomCleaning(RoomCleaningBackend):
    """V4/Prime rooms, over the cloud command topic.

    Confirmed on real hardware (DaRealGuGu): the robot travelled to the
    named room and cleaned it, both from a saved favorite and from a
    command built from scratch.

    Two requirements, neither obvious, and both cost field sessions to
    establish:

      - `initiator` is MANDATORY. A stored favorite does not carry one;
        the app adds it when sending. Without it the command is
        delivered, acknowledged with a PUBACK, and silently ignored.
      - The wire keys are `start` and `region_id`, not `clean` and `id`.

    A map version is NOT required, which is worth knowing because it
    looks like it should be. The robot re-versions its map every few
    seconds while cleaning -- five values inside 37 seconds in one real
    capture -- so any stored version is stale within a minute, and
    confirmed-working commands carried versions hours out of date or
    none at all.
    """

    def __init__(
        self,
        data: RoombaData,
        config_entry: RoombaConfigEntry | None = None,
        hass: HomeAssistant | None = None,
    ) -> None:
        self._data = data

        # NARROWED HERE, where it is true. A CLOUD_ONLY entry always has
        # a prime robot -- `ClassicRoomCleaning` is what a local entry
        # gets -- and mypy cannot see that across the factory. Stating
        # it once beats four checks for a condition that cannot occur.
        if data.prime_robot is None:  # pragma: no cover - Classic gets the other backend
            raise RuntimeError(
                "PrimeRoomCleaning built for an entry with no Prime robot"
            )
        self._robot = data.prime_robot
        # Optional, and only needed to record the mission plan. Kept
        # optional rather than required because several call sites build
        # this backend for read-only use -- listing rooms needs neither.
        self._config_entry = config_entry
        self._hass = hass

    async def _all_map_ids(self) -> list[str]:
        """Every visible map on this robot, in whatever order the server
        returned them.

        Order is NOT meaningful. The endpoint documents no sort, and
        picking the first entry would be a coin flip -- one real account
        (arielgr) has two maps, "1st floor" and "2nd floor".
        """
        try:
            versions = await self._robot.get_active_map_versions()
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "roomba_plus: could not read map versions for %s",
                self._data.blid, exc_info=True,
            )
            return []

        # Wire data arrives as plain dicts, not typed models -- getattr()
        # would silently return None on every field. That exact mistake
        # has been made three times across these two codebases.
        return [
            entry["p2map_id"]
            for entry in versions or []
            if isinstance(entry, dict) and entry.get("p2map_id")
        ]

    async def _current_map_id(self) -> str | None:
        """The map the robot is actually standing on, if it says.

        MULTI-FLOOR IS REAL, not hypothetical: one tester's account
        holds a map per floor. Room ids only mean anything within their
        own map, so choosing the wrong one cleans the wrong floor --
        the kind of failure that is obvious to the user and baffling
        from the logs.

        The robot reports its map in cleanMissionStatus while it knows
        where it is (its own reloc events carry p2mapId). When it does
        not -- parked, freshly booted, not yet relocalised -- there is
        no honest single answer, and this returns None rather than
        guessing.
        """
        coordinator = getattr(self._data, "prime_status_coordinator", None)
        if coordinator is None or not coordinator.data:
            return None
        current = coordinator.data.get("ro-currentstate") or {}
        mission = current.get("cleanMissionStatus") or {}
        return mission.get("p2mapId") or mission.get("p2map_id") or None

    def _raise_if_map_updating(self) -> None:
        """Refuses while the robot is rebuilding its map.

        Bit 64 of notReady, the same value and meaning Classic uses --
        confirmed present in Prime's own cleanMissionStatus.
        """
        from homeassistant.exceptions import ServiceValidationError  # noqa: PLC0415


        coordinator = getattr(self._data, "prime_status_coordinator", None)
        if coordinator is None or not coordinator.data:
            return
        current = coordinator.data.get("ro-currentstate") or {}
        not_ready = (current.get("cleanMissionStatus") or {}).get("notReady", 0)
        # EQUALITY, not a bit test. notReady is a scalar index, and `&`
        # fired for eight unrelated states -- see const.py.
        if not_ready == MAP_UPDATING_NOT_READY:
            raise ServiceValidationError(
                "The robot is currently updating its map. Wait for the update to "
                "complete, then try again.",
                translation_domain="roomba_plus",
                translation_key="map_updating",
            )

    async def available_rooms(self) -> dict[str, str]:
        """Rooms across ALL maps, not just the current one.

        A user asking to clean "Bedroom" should get a match whether or
        not the robot happens to be parked on that floor right now.
        Restricting this to the current map would make the same
        automation work in the evening and fail in the morning.

        IDS ARE QUALIFIED BY THEIR MAP -- "<p2map_id>/<room_id>".
        
        Room ids are assigned per map and start low, so two floors
        almost certainly both have a room "1". Returning bare ids meant
        "Kitchen" downstairs and "Bedroom" upstairs could both resolve
        to "1", and cleaning then targeted whichever map the robot
        happened to be on. Choosing "Kitchen" while the robot was
        upstairs cleaned the bedroom -- silently, since the id was
        valid on that map too.

        Found by feeding two maps with colliding ids, not by reading
        the code: both halves are individually correct.

        DUPLICATE NAMES: the room from the map the robot is currently
        on wins. Borrowed from services.py::_resolve_rooms, which has
        handled this for Classic robots for a long time -- a first draft
        here used "whichever came back first", i.e. server order, which
        decides by coin flip what Classic decides by relevance.

        "Hallway" upstairs and downstairs is an ordinary thing to have,
        and the name alone cannot say which was meant. Preferring the
        floor the robot is standing on is the only reading that is right
        more often than not, and the warning tells the user how to make
        it unambiguous.
        """
        current_map = await self._current_map_id()
        rooms: dict[str, str] = {}
        from_current: set[str] = set()

        for p2map_id in await self._all_map_ids():
            try:
                map_data = await self._robot.get_map_metadata(p2map_id)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "roomba_plus: could not read rooms from map %s for %s",
                    p2map_id, self._data.blid, exc_info=True,
                )
                continue

            is_current = bool(current_map) and p2map_id == current_map

            for room in map_data.rooms_metadata or []:
                if not (room.name and room.room_id):
                    continue

                if room.name in rooms:
                    if is_current and room.name not in from_current:
                        _LOGGER.warning(
                            "roomba_plus: room name %r exists on more than one map "
                            "for %s -- using the one on the map the robot is "
                            "currently on. Rename one of them in the iRobot app to "
                            "target them separately.",
                            room.name, self._data.blid,
                        )
                        rooms[room.name] = f"{p2map_id}/{room.room_id}"
                        from_current.add(room.name)
                    else:
                        _LOGGER.warning(
                            "roomba_plus: room name %r exists on more than one map "
                            "for %s -- keeping the first match. Rename one of them "
                            "in the iRobot app to target them separately.",
                            room.name, self._data.blid,
                        )
                    continue

                rooms[room.name] = f"{p2map_id}/{room.room_id}"
                if is_current:
                    from_current.add(room.name)
        return rooms

    _SEGMENT_PREFIX = "rid_"

    async def get_segments(self) -> list[Any]:
        """Prime rooms as HA Clean Area segments.

        The id format is agreed with clean_segments() below and nowhere
        else, which is why both live here rather than one of them in
        vacuum.py. Classic makes the same agreement about its own
        pmap-prefixed format, in the same file, for the same reason.

        Prime needs no pmap prefix: unlike Classic it resolves the map
        at send time, from the one the robot reports being on.
        """
        try:
            from homeassistant.components.vacuum import Segment  # noqa: PLC0415
        except ImportError:
            return []

        return [
            Segment(id=f"{self._SEGMENT_PREFIX}{room_id}", name=name, group="Room")
            for name, room_id in sorted((await self.available_rooms()).items())
        ]

    async def clean_segments(self, segment_ids: list[str]) -> None:
        """Decode HA segment ids back to room ids and clean them."""
        room_ids = [
            seg_id[len(self._SEGMENT_PREFIX):]
            for seg_id in segment_ids
            if seg_id.startswith(self._SEGMENT_PREFIX)
        ]
        if not room_ids:
            # A stale area mapping, or segments belonging to another
            # vacuum. Cleaning nothing quietly would look like success.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_valid_segments",
            )
        await self.clean_rooms(room_ids)

    async def clean_rooms(
        self,
        room_ids: list[str],
        *,
        ordered: bool = True,
        two_pass: list[bool | None] | None = None,
        suction_level: list[int | None] | None = None,
        operating_mode: list[int | None] | None = None,
    ) -> None:

        # SAME READINESS CHECK CLASSIC HAS. A robot rebuilding its map
        # has region ids in flux, so a command sent now can target a
        # room that no longer exists by the time it arrives -- it either
        # fails or cleans somewhere else.
        #
        # Added after comparing the two generations feature by feature:
        # Prime reports the same notReady bit and simply was not being
        # asked. Everything else had an equivalent; this was the one
        # thing Classic did that Prime did not.
        self._raise_if_map_updating()

        # Ids arrive qualified as "<p2map_id>/<room_id>" (see
        # available_rooms), which removes the guesswork entirely: the
        # map is not inferred from where the robot happens to be, it
        # comes with the room the caller asked for.
        qualified = [r for r in room_ids if "/" in r]
        if qualified:
            maps = {r.split("/", 1)[0] for r in qualified}
            if len(maps) > 1:
                raise HomeAssistantError(
                    "Those rooms are on different maps. A single cleaning command "
                    "can only target one map, so please clean them separately."
                )
            # Optional across both branches: the block further down
            # clears it when the robot names a map that is not in its
            # own list, and the fallback keys on `not p2map_id`.
            p2map_id: str | None = maps.pop()
            room_ids = [r.split("/", 1)[1] for r in qualified]
            self._raise_if_map_updating()
            # `maps.pop()` yields a str; the annotation is Optional
            # because a later branch clears it, and mypy keeps the
            # widened type across the whole function.
            assert p2map_id is not None  # noqa: S101
            await self._send_region_command(
                p2map_id, room_ids, ordered=ordered,
                two_pass=two_pass, suction_level=suction_level,
                operating_mode=operating_mode,
            )
            return

        p2map_id = await self._current_map_id()

        # THE ROBOT'S MAP MUST BE ONE WE LISTED ROOMS FROM.
        #
        # Room ids come from get_map_metadata() per map; the map to
        # clean against comes from the robot's own report. Two sources,
        # and nothing checked them against each other -- so a robot
        # reporting a map that is not in its own list (carried to
        # another floor, a map deleted in the app while HA was running,
        # a freshly created one) would have had ids from map A sent
        # against map B. The robot then cleans the wrong room or
        # nothing, with no error anywhere.
        #
        # Falling back to the single-map path is right here: if there is
        # exactly one map, its ids are the only ids there are. With
        # several, refusing is the only honest answer.
        if p2map_id and p2map_id not in await self._all_map_ids():
            _LOGGER.warning(
                "roomba_plus: robot %s reports being on map %s, which is not in its "
                "own map list -- ignoring it and falling back",
                self._data.blid, p2map_id,
            )
            p2map_id = None

        if not p2map_id:
            # Falling back to a single map is safe; guessing between
            # several is not. On one map there is nothing to get wrong.
            map_ids = await self._all_map_ids()
            if len(map_ids) == 1:
                p2map_id = map_ids[0]
            elif len(map_ids) > 1:
                raise HomeAssistantError(
                    f"This robot has {len(map_ids)} maps and is not currently "
                    "reporting which one it is on, so there is no way to tell which "
                    "floor's rooms you mean. Start the robot from the iRobot app "
                    "first, or wait until it has relocalised."
                )
            else:
                raise HomeAssistantError(
                    "This robot has no saved maps yet, so it cannot clean a named "
                    "room. Let it finish a full mapping run first."
                )

        await self._send_region_command(
            p2map_id, room_ids, ordered=ordered,
            two_pass=two_pass, suction_level=suction_level,
            operating_mode=operating_mode,
        )

    async def _send_region_command(
        self,
        p2map_id: str,
        room_ids: list[str],
        *,
        ordered: bool,
        two_pass: list[bool | None] | None,
        suction_level: list[int | None] | None,
        operating_mode: list[int | None] | None = None,
    ) -> None:
        """Builds and sends the region command for one map.

        Extracted so the qualified-id path and the fallback path cannot
        drift apart -- two copies of a payload this specific is exactly
        how a wire key ends up correct in one place and wrong in the
        other.
        """
        from roombapy_prime.models.mission_control import (  # noqa: PLC0415
            CommandParams,
            MissionCommandType,
            Region,
            RegionType,
            RoutineCommand,
        )

        command = RoutineCommand(
            command_type=MissionCommandType.START,
            asset_id=self._data.blid,
            # The field two field sessions were spent establishing.
            initiator="rmtApp",
            # The dataclass field is map_id; it serialises to the wire
            # key p2map_id. Assuming the two matched would have failed
            # on the first real call.
            map_id=p2map_id,
            ordered=1 if ordered else 0,
            regions=[
                Region(
                    region_id=(
                        rid[len(ZID_PREFIX):] if rid.startswith(ZID_PREFIX) else rid
                    ),
                    # ZONES ARE MARKED BY PREFIX, NOT BY A SEPARATE
                    # ARGUMENT.
                    #
                    # The Classic backend has split rooms from zones on
                    # a `zid_` prefix in this same list since v2.7.0.
                    # Prime hardcoded RID, so a zone id arriving here
                    # was sent as a room -- which the robot answers by
                    # cleaning nothing, or the wrong region if a room
                    # happens to carry that number.
                    #
                    # Found when wiring the clean_zone service for
                    # @chairstacker, whose robot is Prime: the service
                    # would have looked correct and done nothing.
                    region_type=(
                        RegionType.ZID
                        if rid.startswith(ZID_PREFIX)
                        else RegionType.RID
                    ),
                    # Omitted entirely when the caller has no opinion,
                    # rather than sent as False. The confirmed field
                    # payload only ever carried params the app itself
                    # set, and inventing a value here would override
                    # whatever the user configured on the robot.
                    params=_region_params(
                        CommandParams, two_pass, suction_level, i,
                        operating_mode=operating_mode,
                    ),
                )
                for i, rid in enumerate(room_ids)
            ],
        )
        await self._robot.send_routine_command_via_cmd_topic(command)

        # THE PLAN GETS THE IDS THE ROBOT WILL REPORT.
        #
        # `room_ids` still carries our `zid_` markers here. The mission
        # timer matches planned regions against what the robot reports
        # as it cleans, and the robot reports a bare `100` -- so a
        # prefixed plan would never match, and a zone mission would show
        # every region as unvisited for its whole run.
        self._note_mission_plan(
            p2map_id,
            [
                rid[len(ZID_PREFIX):] if rid.startswith(ZID_PREFIX) else rid
                for rid in room_ids
            ],
        )

    def _note_mission_plan(self, p2map_id: str, room_ids: list[str]) -> None:
        """Tells MissionTimerStore which rooms this mission will visit.

        THE LINK THAT WAS MISSING. The store was created for Prime and
        fed phase transitions, but nothing ever called set_mission_plan()
        -- only the Classic MQTT callback path does. Four things depend
        on it:

          - the advance_room service, whose planned_rooms was always
            empty, so it silently did nothing
          - current_room and next_room, both blank
          - the mission progress sensor, which showed elapsed time and no
            remaining estimate -- half working, and looking whole

        ESTIMATES COME FROM MEASURED HISTORY, not from the cloud.
        get_time_estimates() would supply predictions, and an APK pass
        established that its request body is assembled in native code --
        the key names are not determinable, and guessing them is what
        cost three testers weeks on virtual walls.

        So the estimate is the median of how long this robot has actually
        taken in that room, from past missions. Better than a model
        value, and None for a room never cleaned -- which
        set_mission_plan already accepts per room, because Classic has
        the same gap for a newly named room.

        ONLY COVERS MISSIONS HOME ASSISTANT STARTED. A robot cleaning on
        its own schedule still has no plan here -- the timeline reports
        rooms as it enters them, but not the list up front. That is a
        real limitation and not one this can fix.
        """
        try:
            store = getattr(self._data, "mission_timer_store", None)
            hass = getattr(self._data, "hass_ref", None) or self._hass
            entry_id = getattr(self._config_entry, "entry_id", None)
            if store is None or hass is None or not entry_id:
                return
            from .prime_mission_sync import estimate_room_seconds  # noqa: PLC0415

            per_room = estimate_room_seconds(
                getattr(self._data, "mission_store", None), list(room_ids)
            )
            known = [s for s in per_room if s is not None]
            store.set_mission_plan(
                f"{p2map_id}:{'+'.join(room_ids)}",
                list(room_ids),
                # Total only when EVERY room has a measurement. A partial
                # sum would read as the whole mission and be short by
                # however many rooms are missing -- worse than showing
                # nothing, because nothing is visibly nothing.
                sum(known) if len(known) == len(room_ids) else None,
                hass,
                entry_id,
                room_estimates_sec=per_room,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                # NOT INSTRUMENTED: the plan is a convenience record for
                # room progress, and a robot cleaning everywhere has no
                # plan to write -- absence here is a normal state.
                "roomba_plus: could not record the mission plan", exc_info=True
            )


class ClassicRoomCleaning(RoomCleaningBackend):
    """Classic rooms, via the Classic cloud coordinator.

    Wraps what services.py already did, unchanged in behaviour. It is
    here so the services stop having to know which generation they are
    talking to -- not because the Classic path needed fixing.
    """

    def __init__(
        self, data: RoombaData, config_entry: RoombaConfigEntry, hass: Any = None
    ) -> None:
        self._data = data
        self._config_entry = config_entry
        self._hass = hass
        self._pmap_by_region: dict[str, str] = {}

        #: The local robot, narrowed once for this whole class.
        #:
        #: A CLOUD_ONLY entry gets `PrimeRoomCleaning`, never this one,
        #: so `data.roomba` is always set here. mypy cannot see that
        #: across the factory, and reported it at each send site --
        #: which invites an `if roomba is None` at every one of them,
        #: turning a class-wide invariant into scattered runtime checks
        #: for a condition that cannot occur.
        #:
        #: Stated once, where it is true.
        if data.roomba is None:  # pragma: no cover - Prime gets the other backend
            raise RuntimeError(
                "ClassicRoomCleaning built for an entry with no local "
                "robot -- a CLOUD_ONLY entry should have PrimeRoomCleaning"
            )
        self._roomba = data.roomba

    @property
    def _cloud(self) -> IrobotCloudCoordinator:
        """The cloud coordinator, for the room-targeting paths.

        Region ids come from the cloud pmap, so `clean_rooms` and
        `clean_segments` cannot work without one. Listing rooms and
        reading names can, and those run on entries with no cloud at
        all -- which is why this raises where it is read rather than
        refusing in the constructor.

        Five reads share it. Checking at each would be five runtime
        tests for one invariant, and mypy cannot follow `has_cloud`
        because a property narrows nothing.
        """
        cloud = self._data.cloud_coordinator
        if cloud is None:
            raise ServiceValidationError(
                "room targeting needs cloud credentials on this robot",
                translation_domain=DOMAIN,
                translation_key="cloud_required",
            )
        return cloud

    def _raise_if_map_updating(self) -> None:
        """Refuses while the robot is rebuilding its map.

        Was inline in services.py; moved with everything else. A robot
        rebuilding its map has region ids in flux, so a command sent now
        can target a room that no longer exists on arrival.
        """
        state = self._data.roomba_reported_state()
        not_ready = (state.get("cleanMissionStatus") or {}).get("notReady", 0)
        # Coerced: a robot that reports notReady as a string, or a test
        # fixture handing back a mock, must not make this raise -- a
        # false "map is updating" refusal blocks a legitimate request
        # for a reason the user cannot act on.
        if not isinstance(not_ready, int):
            return
        if not_ready == MAP_UPDATING_NOT_READY:
            raise ServiceValidationError(
                "The robot is currently updating its Smart Map. Wait for the update "
                "to complete (readiness sensor shows 'Ready'), then try again.",
                translation_domain=DOMAIN,
                translation_key="map_updating",
            )

    async def available_rooms(self) -> dict[str, str]:
        coordinator = self._data.cloud_coordinator
        if coordinator is None or coordinator.data is None:
            return {}
        # BOTH SOURCES, in the original's order: stored zone data
        # first, cloud regions and zones layered on top.
        #
        # A first version of this read only the cloud coordinator and
        # lost every user-named room on installs without cloud
        # credentials. Six existing tests caught it -- which is the
        # reason this was moved rather than rewritten.
        #
        # Also records region_id -> pmap_id as a side effect, and that
        # is what makes clean_rooms() work on ids alone: the only thing
        # Classic needs beyond a region id is its map, and it already
        # sits right here.
        self._pmap_by_region = {}
        rooms: dict[str, str] = {}

        zone_data: dict[str, Any] = self._config_entry.options.get(CONF_SMART_ZONE_DATA, {})
        entries: list[tuple[str, str, str]] = [
            (str(rid), meta["name"], str(meta.get("pmap_id") or ""))
            for rid, meta in zone_data.items()
            if meta.get("name")
        ]

        coordinator = self._data.cloud_coordinator
        if coordinator is not None and coordinator.data is not None:
            for source in (coordinator.regions or [], coordinator.zones or []):
                entries += [
                    (str(item["id"]), item["name"], str(item.get("pmap_id") or ""))
                    for item in source
                    if item.get("id") and item.get("name")
                ]

        for rid, name, pmap_id in entries:
            rooms[name] = rid
            self._pmap_by_region[rid] = pmap_id
        return rooms

    async def clean_rooms(
        self,
        room_ids: list[str],
        *,
        ordered: bool = True,
        two_pass: list[bool | None] | None = None,
        suction_level: list[int | None] | None = None,
        operating_mode: list[int | None] | None = None,
    ) -> None:
        """The Classic path, moved here verbatim from services.py.

        MOVED, NOT REWRITTEN. An earlier draft copied it and lost four
        things in the process -- the cross-map conflict rules, the
        map-updating readiness check, cloud enrichment of stored zone
        data, and the distinct "no rooms configured" error. Everything
        below is the original code with names adjusted for its new home.

        `suction_level` is accepted and ignored: the Classic room
        payload has no field for it. Ignoring beats raising, so callers
        need not know which generation they are talking to.

        WHAT USED TO BE HERE INSTEAD:

        Classic room cleaning stays in services.py. A version of it did
        live here briefly and was removed, because comparing the two
        line by line showed the copy had quietly lost four things the
        original does:

          - the 141-line resolution step, including cross-map conflict
            rules for duplicate room names
          - the "map is currently updating" readiness check
          - enriching stored zone data with the cloud coordinator's
            regions and zones
          - the distinct "no rooms configured yet" error, which tells a
            user what to do rather than just failing

        A copy that looks finished and is not is worse than no copy:
        whoever finds it later may switch the service over to it and
        lose all four without noticing.

        Moving it properly is worthwhile and is its own task. It touches
        a path real users depend on daily, and it should not ride along
        with adding a new generation -- one working generation at a time
        is easier to verify than two changing at once.
        """
        if not self._pmap_by_region:
            # clean_rooms is always reached via available_rooms, which
            # populates the index. Refusing beats sending a command with
            # an empty pmap_id, which the robot accepts and ignores.
            raise HomeAssistantError(
                "Room list has not been read yet, so the map each room belongs to "
                "is unknown."
            )

        self._raise_if_map_updating()

        state = self._data.roomba_reported_state()
        # Every requested region carries its own map; they must all come
        # from the same one, since the payload has a single pmap_id.
        pmap_id = next(
            (self._pmap_by_region.get(rid) for rid in room_ids if self._pmap_by_region.get(rid)),
            "",
        )
        # AN EMPTY MAP ID IS NOT A NEUTRAL VALUE.
        #
        # @Echovictor37 sent a region command with `map_id=None` on a
        # Combo 105: the broker returned a PUBACK **and the robot cleaned
        # the whole house.** Not the requested room, and not nothing --
        # accepted, effective, and not what was asked.
        #
        # This fell through to `""` and sent it. A user asking for the
        # kitchen would get every room, with a success in the log.
        # Refusing is the only honest answer: we cannot address a room
        # without knowing its map.
        if not pmap_id:
            raise ServiceValidationError(
                "The map these rooms belong to is not known yet, so a "
                "room-targeted clean cannot be sent. Sending it anyway would "
                "start a whole-house clean instead. Wait for the map to load, "
                "or start the robot without naming rooms."
            )
        user_pmapv_id: str = (
            (self._cloud.active_user_pmapv_id
             if self._data.has_cloud else None)
            or _resolve_pmapv_id(state, pmap_id)
            or ""
        )
        # WHAT THE USER CHOSE, from the cleaning-mode select. None when
        # they never picked one, and then nothing is sent.
        #
        # This was deliberately left out until "somebody establishes what
        # the values mean" -- see the note on the room payload above.
        # They are established now: 2 vacuum, 4 mop, 32 both at once,
        # 512 vacuum then mop, confirmed from schedules on two accounts
        # and from the app's own operating_mode_defaults.
        # THE CALLER'S CHOICE OUTRANKS THE SELECT.
        #
        # `clean_room` has a `cleaning_mode` field for a run that should
        # differ from the everyday default -- it was described in
        # services.yaml and never read, so the service accepted it and
        # threw it away. The select's value is the fallback, which is
        # what "leave empty to use the selector" in that description
        # promises.
        selected = _selected_cleaning_mode(self._config_entry)
        def _mode_for(index: int) -> int | None:
            """This room's mode: caller first, then the select.

            A ONE-ELEMENT LIST APPLIES TO EVERY ROOM. The service passes
            `[mode]` for a single choice, and reading it positionally
            would give room one the caller's mode and every other room
            the select's -- a schedule that mops the kitchen and vacuums
            the hall, from one field nobody meant that way.
            """
            if operating_mode:
                per_room = (
                    operating_mode[index] if index < len(operating_mode)
                    else operating_mode[0] if len(operating_mode) == 1
                    else None
                )
                if per_room is not None:
                    return per_room
            return selected

        robot_two_pass = bool(state.get("twoPass", False))
        no_auto = bool(state.get("noAutoPasses", False))

        params = {
            "ordered": 1 if ordered else 0,
            "pmap_id": pmap_id,
            "user_pmapv_id": user_pmapv_id,
            "regions": [
                {
                    "region_id": rid,
                    "type": "rid",
                    "params": {
                        "noAutoPasses": no_auto,
                        # THE CLEANING MODE, when the user has expressed
                        # one. Omitted entirely otherwise, so a robot
                        # keeps whatever it would have done -- sending a
                        # default here would decide for people who never
                        # touched the control.
                        # Per room, because the signature carries a list:
                        # a caller that names one mode gets it everywhere,
                        # and one that names none falls back to the select.
                        **(
                            {"operatingMode": _mode_for(i)}
                            if _mode_for(i) is not None else {}
                        ),
                        "twoPass": (
                            value if (value := _per_room(two_pass, i)) is not None
                            else robot_two_pass
                        ),
                    },
                }
                for i, rid in enumerate(room_ids)
            ],
        }
        _LOGGER.info(
            "clean_room: %s → regions=%s pmap=%s pmapv=%s",
            self._data.blid, room_ids, pmap_id[:12],
            user_pmapv_id[:12] if user_pmapv_id else "none",
        )
        await self._hass.async_add_executor_job(
            self._roomba.send_command, "start", params
        )



    async def get_segments(self) -> list[Any]:
        """The Classic side of HA's Clean Area segment list.

        MOVED (this session), completing what clean_segments started.
        Leaving the producer in vacuum.py while the consumer lived here
        split one contract across two files: the pmap-prefixed id format
        is agreed between exactly these two methods, and nothing else
        would have flagged them drifting apart.

        Checked before moving rather than after: no entity-only
        attribute is reached in here, unlike clean_segments, which
        silently used self.vacuum and self.vacuum_state and needed three
        rounds of test failures to surface them.
        """
        # Segment lands in HA 2026.3. Returning [] on older versions
        # matches supported_features, which gates CLEAN_AREA on the same
        # hasattr -- so capability and data agree about whether this HA
        # can do segments at all.
        try:
            from homeassistant.components.vacuum import Segment  # noqa: PLC0415
        except ImportError:
            return []


        if not self._data.has_cloud or self._data.cloud_coordinator is None:
            return []
        active_pmap_id = self._cloud.active_pmap_id
        if not active_pmap_id:
            # Coordinator has not yet fetched pmap data — returning segments with a
            # None prefix would create IDs that never match in async_clean_segments.
            _LOGGER.debug(
                "async_get_segments: active_pmap_id not yet available — returning empty"
            )
            return []
        floor_label = (
            self._config_entry.options.get(CONF_FLOOR) or None
            if self._config_entry else None
        )
        # Room segments (rid)
        segments = [
            Segment(
                id=f"{active_pmap_id}_{region['id']}",
                name=region.get("name", region["id"]),
                group=floor_label,
            )
            for region in self._cloud.regions
            if region.get("id")
        ]
        # IA74-ZONE full (v2.7.0): zone segments (zid).
        # Zone segment IDs use the format "{pmap_id}_zid_{zone_id}" so
        # async_clean_segments can distinguish them from room segments.
        # Zone type is surfaced as the group label for HA's mapping UI.
        for zone in self._cloud.zones:
            zid = zone.get("id")
            if not zid:
                continue
            zone_type = zone.get("zone_type", "zone")
            zone_name = zone.get("name") or f"Zone {zid}"
            segments.append(Segment(
                id=f"{active_pmap_id}_zid_{zid}",
                name=zone_name,
                group=zone_type.replace("_", " ").capitalize() if zone_type else "Zone",
            ))
        return segments

    async def clean_segments(self, segment_ids: list[str]) -> None:
        """HA Clean Area segments, which are NOT the same as room ids.

        MOVED VERBATIM from vacuum.py (this session), with two entity
        attributes rebound: `self.vacuum` and `self.vacuum_state` are
        conveniences IRobotEntity provides, and they resolve here to
        `self._data.roomba` and `roomba_reported_state()` -- the same
        objects by a different route.

        Both were missed by the mechanical rewrite that moved this and
        were caught by existing tests. Worth naming: a move that renames
        `data.` to `self._data.` looks complete and silently leaves
        anything that was reached another way. Kept as its own
        method rather than folded into clean_rooms(), because Classic
        segments carry three things room ids do not:

          - a pmap_id prefix, so segments from another map can be
            recognised and skipped rather than sent to the wrong map
          - a "zid_" marker separating user-drawn ZONES from named
            rooms; both clean, through different payload fields
          - self-healing: a room id that no longer exists on the current
            map is looked up again by its stored name, because a retrain
            renumbers rooms and would otherwise break every saved area
            mapping silently

        None of that has a Prime equivalent, which is why PrimeRoom-
        Cleaning implements this as a thin decode of its own prefix.

        An earlier plan was to drop this into clean_rooms() and lose the
        distinction. That would have quietly removed zone cleaning and
        the self-healing from every Classic user -- the same shape of
        mistake as an earlier draft here that dropped four capabilities
        while "moving" code that it was actually rewriting.
        """
        active_pmap_id = self._cloud.active_pmap_id
        if not active_pmap_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_valid_segments",
            )
        region_ids: list[str] = []
        prefix = f"{active_pmap_id}_"
        for seg_id in segment_ids:
            # v2.4.3 PMAP-UNDERSCORE: pmap_ids may contain underscores (URL-safe
            # base64 encoding). partition("_") splits on the first underscore and
            # produces a wrong pmap_id for IDs like "2Bly_kGURy6OcUVTX7FN3w_19".
            # Use a prefix check instead — region_ids are always plain integers.
            if seg_id.startswith(prefix):
                region_ids.append(seg_id[len(prefix):])

        if not region_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_valid_segments",
            )

        # IA74-ZONE full (v2.7.0): split room IDs from zone IDs.
        # Zone segment IDs were encoded as "zid_{zone_id}" after prefix-stripping
        # in async_get_segments().  Rooms are plain integers (e.g. "19").
        # Was a local literal; now the module constant, so Classic and
        # Prime cannot drift apart on what marks a zone.
        _ZONE_PREFIX = ZID_PREFIX
        raw_zone_ids = [r for r in region_ids if r.startswith(_ZONE_PREFIX)]
        raw_room_ids = [r for r in region_ids if not r.startswith(_ZONE_PREFIX)]
        # Extract the bare zone_id (strip "zid_" prefix)
        bare_zone_ids = [z[len(_ZONE_PREFIX):] for z in raw_zone_ids]

        # Validate + auto-heal room IDs against the current cloud map (zone IDs
        # are stable across map retrains — no validation needed for them).
        current_regions = self._cloud.regions
        current_region_ids: set[str] = {
            str(r["id"]) for r in current_regions if r.get("id")
        }
        validated_room_ids = list(raw_room_ids)
        if current_region_ids and raw_room_ids:
            stale = [rid for rid in raw_room_ids if rid not in current_region_ids]
            if stale:
                # Build name → current_id lookup from live cloud data
                name_to_current: dict[str, str] = {
                    r["name"].casefold(): str(r["id"])
                    for r in current_regions
                    if r.get("name") and r.get("id")
                }
                zone_labels: dict[str, str] = (
                    self._config_entry.options.get("smart_zone_labels", {})
                    if self._config_entry else {}
                )
                healed: list[str] = []
                for stale_rid in stale:
                    label = zone_labels.get(stale_rid, "")
                    current_id = name_to_current.get(label.casefold()) if label else None
                    if current_id and current_id not in raw_room_ids:
                        healed.append(current_id)
                        _LOGGER.info(
                            "async_clean_segments: auto-healed stale region %s → %s "
                            "('%s') — map retrained but room name matched current map",
                            stale_rid, current_id, label,
                        )
                    else:
                        _LOGGER.warning(
                            "async_clean_segments: stale region %s could not be "
                            "auto-healed (label=%r, not in current map) — skipping. "
                            "Re-map vacuum segments to areas in HA to fix permanently.",
                            stale_rid, label or "<unlabeled>",
                        )
                validated_room_ids = (
                    [r for r in raw_room_ids if r in current_region_ids] + healed
                )

        if not validated_room_ids and not bare_zone_ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_valid_segments",
            )

        regions = [
            {
                "region_id": rid,
                "type": "rid",
                "params": {"noAutoPasses": False, "twoPass": False},
            }
            for rid in validated_room_ids
        ] + [
            # IA74-ZONE full (v2.7.0): zones use type "zid" with bare zone_id
            {
                "region_id": zid,
                "type": "zid",
                "params": {"noAutoPasses": False, "twoPass": False},
            }
            for zid in bare_zone_ids
        ]

        from .room_cleaning import _resolve_pmapv_id  # moved there with the Classic send path
        # Primary: cloud coordinator — always authoritative, never stale.
        user_pmapv_id: str | None = self._cloud.active_user_pmapv_id
        if not user_pmapv_id:
            user_pmapv_id = _resolve_pmapv_id(self._data.roomba_reported_state(), active_pmap_id)
        if user_pmapv_id is None:
            _LOGGER.warning(
                "async_clean_segments: user_pmapv_id not found in cloud or "
                "state.pmaps for pmap %s — sending command without version ID",
                active_pmap_id,
            )

        # Bug 5 fix (v2.7.0): per field logs, including user_pmapv_id in pure
        # zone (zid) commands causes error 224. Omit it when no room regions
        # are present. Mixed room+zone commands retain it for room-ID validation.
        include_pmapv = bool(validated_room_ids)

        params: dict[str, Any] = {
            "ordered": 1,
            "pmap_id": active_pmap_id,
            "regions": regions,
        }
        if include_pmapv and user_pmapv_id is not None:
            params["user_pmapv_id"] = user_pmapv_id
        await self._hass.async_add_executor_job(
            self._roomba.send_command,
            "start",
            params,
        )
        # F-RB-1: best-effort state update after segment clean command.
        # Cloud may not yet have the new state; failure is non-fatal.
        try:
            await self._cloud.async_refresh()
            record_success("post-command cloud refresh")
        except Exception:  # noqa: BLE001
            record_failure("post-command cloud refresh")
            _LOGGER.debug("async_clean_segments: post-command cloud refresh failed (non-fatal)")

def _classic_has_room_data(data: RoombaData, config_entry: RoombaConfigEntry) -> bool:
    """Whether a Classic robot has any room names to work with.

    NOT the same as has_cloud, which a first draft used and which is too
    strict: room names live in config_entry.options (smart_zone_data),
    and the cloud coordinator only ENRICHES that list. Requiring cloud
    would lock out every Classic user running without credentials -- a
    real configuration, and one the gate this replaced allowed.

    Also not "always true for SMART", which a second draft used and
    which is too loose: a robot with a smart map but no named rooms
    anywhere has nothing to target, and offering room cleaning would
    produce a service call that can only fail. An existing test caught
    that one.
    """
    # TWO KEYS FOR THE SAME THING, and the check read the wrong one.
    #
    # The naming flow writes `smart_zone_labels`. `smart_zone_data` is
    # written by exactly one path: the rest980 migration, for people
    # arriving from another integration. So a user who named their rooms
    # through our own flow still failed this check, and the docstring
    # above -- "requiring cloud is too strict, room names live in
    # options" -- described a route that did not exist for them.
    #
    # Either key answers the question the check is actually asking: does
    # this robot have room names to work with.
    if config_entry.options.get(CONF_SMART_ZONE_DATA):
        return True
    if config_entry.options.get(CONF_SMART_ZONE_LABELS):
        return True
    coordinator = data.cloud_coordinator
    return coordinator is not None and coordinator.data is not None


def async_get_room_cleaning_backend(
    config_entry: RoombaConfigEntry, hass: Any = None
) -> RoomCleaningBackend | None:
    """The backend for this robot, or None when it cannot clean rooms.

    None IS the answer to "does this robot support room cleaning". It
    replaces the old `map_capability == SMART` check, which was a
    Classic-shaped question that no Prime robot could ever pass.
    """
    if config_entry is None:
        # Entities can briefly exist without one -- and the code this
        # replaced checked for it. A move that drops a guard the
        # original had is the quiet kind of regression, caught here by
        # an existing test rather than by the move itself.
        return None

    data: RoombaData = config_entry.runtime_data

    if data.connection_type == ConnectionType.CLOUD_ONLY:
        return (
            PrimeRoomCleaning(data, config_entry, hass)
            if data.prime_robot is not None
            else None
        )

    if data.map_capability == MapCapability.SMART and _classic_has_room_data(
        data, config_entry
    ):
        # hass COMES FROM THE CALLER OR IS NONE. It must never be read off
        # the config entry: ConfigEntry has no `hass` attribute, so
        # `config_entry.hass` raises AttributeError.
        #
        # This factory is called from `supported_features`, a property
        # Home Assistant evaluates while registering the entity. An
        # exception there means the entity is never added -- which is how
        # the vacuum entity, the single most important one in this
        # integration, disappeared for a tester on v4.0.0a14.
        #
        # THE TEST SUITE COULD NOT SEE IT. Every test passes a MagicMock
        # as the config entry, and a MagicMock answers any attribute
        # access. All 4,577 tests stayed green against code that raises
        # on the first real ConfigEntry it meets.
        #
        # hass is only needed to record the mission plan; a backend built
        # without it still lists rooms and still cleans.
        # is the same object in production but not in tests, whose
        # fixtures prepare the one the service call carries -- and the
        # send path runs through hass.async_add_executor_job.
        return ClassicRoomCleaning(data, config_entry, hass)

    return None


# ── Classic room resolution ──────────────────────────────────────────
#
# MOVED WHOLESALE FROM services.py (this session), not rewritten. Every
# line below is the original, because the point of the move is that
# Classic behaviour does not change -- and a rewrite is exactly how four
# capabilities went missing in an earlier draft that copied instead of
# moved.
#
# 21 existing clean_room tests cover this and were the check that the
# move preserved behaviour.

def _resolve_pmapv_id(state: dict[str, Any], pmap_id: str) -> str | None:
    """Return user_pmapv_id for pmap_id from local MQTT state.

    v2.7.4 (PMAP-PMAPV): prefers lastCommand.user_pmapv_id over state.pmaps.

    rest980 protocol: get user_pmapv_id from lastCommand (the stable committed
    version the robot last accepted).  state.pmaps reflects the live value that
    the robot writes to MQTT immediately after updating its map — this version
    is not yet committed for command use and will cause error 224 if sent.

    Fallback to state.pmaps when lastCommand has a different pmap_id (e.g. the
    robot was last commanded on a different map) or when cloud data is absent.
    """
    last = state.get("lastCommand", {})
    if last.get("pmap_id") == pmap_id and last.get("user_pmapv_id"):
        return str(last["user_pmapv_id"])
    for pmap in state.get("pmaps", []):
        if pmap_id in pmap:
            return str(pmap[pmap_id])
    return None


def _resolve_rooms(
    zone_data: dict[str, dict[str, Any]],
    room_names: list[str],
    state: dict[str, Any],
    cloud_pmap_id: str | None = None,
) -> list[tuple[str, str]]:
    """Resolve room names to (region_id, pmap_id) tuples.

    Args:
        zone_data:      smart_zone_data from config_entry.options —
                        {region_id: {"name": str, "pmap_id": str}}
        room_names:     user-supplied room names from the service call.
        state:          live robot state — used to resolve pmap_id when the stored
                        value is empty (MQTT fallback).
        cloud_pmap_id:  authoritative pmap_id from the cloud coordinator.
                        When present this is preferred over the MQTT cascade.

    Returns:
        Ordered list of (region_id, pmap_id) matching each room name.

    Raises:
        ServiceValidationError: unknown name, unresolvable pmap_id, or
            rooms spanning more than one pmap_id.
    """
    index: dict[str, tuple[str, str]] = {}
    for rid, meta in zone_data.items():
        if not meta.get("name"):
            continue
        key = meta["name"].casefold()
        pmap_id = meta.get("pmap_id", "")
        if key in index:
            existing_rid, existing_pmap = index[key]
            if cloud_pmap_id and pmap_id == cloud_pmap_id:
                _LOGGER.warning(
                    "clean_room: duplicate room name '%s' across maps "
                    "(region %s from map %.8s overwrites region %s from map %.8s). "
                    "Delete the old Smart Map in the iRobot app to prevent this.",
                    meta["name"], rid, pmap_id, existing_rid, existing_pmap,
                )
                index[key] = (rid, pmap_id)
            else:
                _LOGGER.warning(
                    "clean_room: duplicate room name '%s' — keeping region %s "
                    "(region %s ignored, not from active map %.8s). "
                    "Delete the old Smart Map in the iRobot app.",
                    meta["name"], existing_rid, rid,
                    cloud_pmap_id[:8] if cloud_pmap_id else "unknown",
                )
        else:
            index[key] = (rid, pmap_id)

    resolved: list[tuple[str, str]] = []
    unknown: list[str] = []

    # v2.7.3 (ROOM-SLUG): XVMC sends room_id (ASCII slug) as [[selection]].
    # Build a secondary slug index so "kuche" resolves to "Küche".

    # Uses the shared room_slug from const.py. A THIRD copy of this rule
    # lived here as a nested function, invisible to a module-level scan --
    # and it used a different unicode test (`category(c) != "Mn"` rather
    # than `combining(c)`), which agree for accents but not for every mark.

    slug_index: dict[str, tuple[str, str]] = {
        room_slug(meta["name"]): val
        for val in [index[k] for k in index]
        for meta in zone_data.values()
        if meta.get("name") and index.get(meta["name"].casefold()) == val
    }
    # Simpler rebuild: slug → (rid, pmap_id) from the name index
    slug_index = {
        room_slug(display_name): match_val
        for display_name, match_val in (
            (meta["name"], index.get(meta["name"].casefold()))
            for meta in zone_data.values()
            if meta.get("name") and index.get(meta["name"].casefold())
        )
        if match_val is not None
    }

    for name in room_names:
        match = index.get(name.casefold()) or slug_index.get(room_slug(name))
        if match is None:
            unknown.append(name)
        else:
            resolved.append(match)

    if unknown:
        raise ServiceValidationError(
            f"Unknown room(s): {', '.join(unknown)}. "
            f"Known rooms: {', '.join(meta['name'] for meta in zone_data.values() if meta.get('name'))}",
            translation_domain=DOMAIN,
            translation_key="rooms_not_found",
            translation_placeholders={"names": ", ".join(unknown)},
        )

    # Resolve empty pmap_ids — priority:
    #   0. cloud_pmap_id  — authoritative, immune to stale MQTT
    #   1. lastCommand.pmap_id
    #   2. cleanSchedule2[].cmd.pmap_id
    #   3. pmaps[0] key   — last resort
    last = state.get("lastCommand", {})
    pmaps: list[dict[str, Any]] = state.get("pmaps", [])
    fallback_pmap_id: str = (
        cloud_pmap_id
        or last.get("pmap_id")
        or next(
            (
                cmd.get("cmd", {}).get("pmap_id")
                for cmd in state.get("cleanSchedule2", [])
                if cmd.get("cmd", {}).get("pmap_id")
            ),
            None,
        )
        or (next(iter(pmaps[0]), None) if pmaps else None)
        or ""
    )
    resolved = [
        (rid, pmap_id if pmap_id else fallback_pmap_id)
        for rid, pmap_id in resolved
    ]

    pmap_ids = {pmap_id for _, pmap_id in resolved}
    if "" in pmap_ids:
        raise ServiceValidationError(
            "Could not resolve map ID (pmap_id) for one or more rooms. "
            "Ensure the robot has reported its map state via MQTT.",
            translation_domain=DOMAIN,
            translation_key="pmap_not_resolved",
        )
    if len(pmap_ids) > 1:
        raise ServiceValidationError(
            "All rooms must be on the same floor (same pmap). "
            f"Got rooms from maps: {', '.join(pmap_ids)}",
            translation_domain=DOMAIN,
            translation_key="rooms_different_floors",
            translation_placeholders={"pmap_ids": ", ".join(pmap_ids)},
        )

    return resolved
