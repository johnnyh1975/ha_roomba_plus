"""Which room the robot is in, from where it is (4.4.0).

THE ROOM FROM THE POSITION, WHERE THERE IS ONE (@frnchfrgg). Room
tracking moves through the planned rooms on phase changes and drives,
and decides whether a room is done from how long the robot has been in
it against an estimate. A robot that cleans in another order, or a run
whose rooms took longer or shorter than estimated, gives room changes
that never happened.

A robot that reports its position, or answers when asked for it, says
where it is. Where that position can be placed in a room -- a Smart Map
whose rooms the aligner has placed, or a 900-series whose floor Roomba+
has divided into areas -- the room comes from there instead:

  * A room counts once the robot has stayed in it for DWELL_SEC, with
    at least MIN_SAMPLES positions. A robot crossing a doorway, or
    passing through a hall, does not change the room.
  * A position outside every room (a doorway, floor the map does not
    cover) changes nothing.
  * Positions that stop for STALE_SEC hand room tracking back to the
    phase changes and estimates (callbacks.py).

This module only decides. callbacks._apply_position_room() moves the
room display and fires the room events.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
import math
from time import monotonic
from typing import Any

from .const import LIVE_PATH_TURN_CHOICES

#: Seconds in a room before it counts.
DWELL_SEC = 15.0
#: Positions in a room before it counts (with DWELL_SEC).
MIN_SAMPLES = 3
#: Seconds without a position after which room tracking falls back.
STALE_SEC = 60.0
#: How recent a position must be to say the robot is still in a room
#: it was told to skip (4.4.1).
SKIP_STILL_IN_SEC = 10.0

#: Grid cell of the 900-series areas (room_seg_store.CELL_MM).
_SEG_CELL_MM = 150.0


class PositionRoomTracker:
    """Turns a stream of rooms-at-a-position into confirmed room changes."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._listeners: list[Callable[[str | None, str], None]] = []
        self.reset()

    def reset(self) -> None:
        """Forget the room; a new mission starts from no room."""
        #: The confirmed room, or None.
        self.current: str | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._candidate_samples = 0
        #: When the last position arrived (clock), and its room.
        self.last_seen: float | None = None
        self.last_room: str | None = None
        #: When a position last lay in a room (clock). Only these make
        #: the tracker active: positions no room can be found for must
        #: not switch off the phase-based room tracking.
        self.last_in_room: float | None = None
        #: Confirmed changes this mission (diagnostics).
        self.changes = 0
        #: Every room confirmed since the reset, in order, each once
        #: (4.5.0): the rooms a 900-series mission that cleaned the whole
        #: floor actually went into.
        self.visited: list[str] = []
        #: Positions this mission per room, None for those in no room
        #: (4.4.1, diagnostics): a turn that puts the path beside the
        #: floor plan shows as positions in no room, without a
        #: coordinate leaving the house.
        self.counts: dict[str | None, int] = {}
        #: Per angle of "Align the path with the rooms", how many of this
        #: mission's positions would lie in a room turned by it (4.4.2).
        #: Counts only, like `counts`.
        self.in_room_by_turn: dict[str, int] = {}
        self.turn_samples = 0

    def add_listener(
        self, listener: Callable[[str | None, str], None]
    ) -> Callable[[], None]:
        """Call `listener(previous, room)` on every confirmed change."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def observe(self, room: str | None) -> None:
        """One position, in `room` or in none."""
        now = self._clock()
        self.last_seen = now
        self.last_room = room
        self.counts[room] = self.counts.get(room, 0) + 1
        if room is None:
            return
        self.last_in_room = now
        if room == self.current:
            self._candidate = None
            return
        if room != self._candidate:
            self._candidate = room
            self._candidate_since = now
            self._candidate_samples = 1
            return
        self._candidate_samples += 1
        if (
            now - self._candidate_since >= DWELL_SEC
            and self._candidate_samples >= MIN_SAMPLES
        ):
            previous, self.current = self.current, room
            self._candidate = None
            self.changes += 1
            if room not in self.visited:
                self.visited.append(room)
            for listener in list(self._listeners):
                listener(previous, room)

    def observe_turns(self, in_room: dict[str, bool]) -> None:
        """One position, tried at every angle the setting offers (4.4.2).

        WHICH TURN PUTS THE PATH ON THE ROOMS. "Automatic" reads the turn
        from the map's header, and that read wrong on @Thonno's i7+ and
        @manni-72's j7 -- the same angles that were right for
        @frnchfrgg's i-series. The share of positions inside a room at
        each angle says which turn fits, from the robot's own path.
        """
        self.turn_samples += 1
        for angle, inside in in_room.items():
            if inside:
                self.in_room_by_turn[angle] = self.in_room_by_turn.get(angle, 0) + 1

    def active(self) -> bool:
        """Whether positions are arriving: room tracking follows them."""
        return (
            self.last_in_room is not None
            and self._clock() - self.last_in_room <= STALE_SEC
        )

    def diagnostics(self) -> dict[str, Any]:
        """Room names and counts only; no position."""
        return {
            "active": self.active(),
            "current": self.current,
            "candidate": self._candidate,
            "last_position_room": self.last_room,
            "seconds_since_position": (
                round(self._clock() - self.last_seen, 1)
                if self.last_seen is not None
                else None
            ),
            "changes_this_mission": self.changes,
            # Room names and counts; "(none)" for positions in no room.
            "positions_by_room": {
                ("(none)" if room is None else room): n
                for room, n in sorted(
                    self.counts.items(), key=lambda kv: (kv[0] is None, kv[0] or "")
                )
            },
            # Positions in a room at each angle of "Align the path with
            # the rooms", of `positions` tried (4.4.2). None without any.
            "in_room_by_turn": (
                {
                    "positions": self.turn_samples,
                    **{
                        angle: self.in_room_by_turn.get(angle, 0)
                        for angle in TURN_ANGLES
                    },
                }
                if self.turn_samples
                else None
            ),
        }

    def still_in(self, room: str) -> bool:
        """Whether the latest position, a moment ago, lay in `room`."""
        return (
            self.last_room == room
            and self.last_seen is not None
            and self._clock() - self.last_seen <= SKIP_STILL_IN_SEC
        )


#: The fixed angles of "Align the path with the rooms", in its sense
#: (cloud_coordinator.requested_position_turn).
TURN_ANGLES: tuple[str, ...] = tuple(a for a in LIVE_PATH_TURN_CHOICES if a != "auto")


def in_room_by_turn(data: Any, x_mm: float, y_mm: float) -> dict[str, bool] | None:
    """Whether a requested position, turned by each angle, lies in a room.

    `x_mm`, `y_mm` as the robot answered, before any turn: the same turn
    image._handle_live_position applies, tried at every angle. None
    where no room can be said at all (no aligner, no rooms), so that a
    missing floor plan does not count as "in no room" at every angle.
    """
    aligner = getattr(data, "umf_aligner", None)
    if aligner is None or not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    if not getattr(aligner, "room_polygons_umf", None):
        return None
    result: dict[str, bool] = {}
    for angle in TURN_ANGLES:
        turn = math.radians(int(angle))
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        x, y = x_mm * cos_t - y_mm * sin_t, x_mm * sin_t + y_mm * cos_t
        result[angle] = bool(aligner.room_name_at(x, y))
    return result


#: LEARNING THE TURN FROM THE PATH (4.5.0b3). "Automatic" took the turn
#: from the header of the map the robot saved, and on two of three robots
#: whose owners checked, that was wrong: @Thonno's i7+ (-90 read, 0
#: right; his positions in no room fell from 1413 to 140) and @nareso's
#: j7+ (180 read, 0 right). @frnchfrgg's i7+ is the one where it holds.
#: The robot's own path says which turn fits: the counts of
#: in_room_by_turn(), summed per map, decide once they are clear.
#:
#: Positions per map before the counts may overrule the header. A run
#: answers about one a second while cleaning, so this is five minutes.
TURN_LEARN_MIN_POSITIONS = 300
#: The winning angle must put at least this share of them in a room ...
TURN_LEARN_MIN_SHARE = 0.5
#: ... and this many times as many as the angle in force (the header's,
#: or one learned before), so that two close angles do not take turns.
TURN_LEARN_MARGIN = 1.5
#: Counts are halved above this, so a map that changed is relearned
#: within a few runs rather than outvoted by months of old ones.
TURN_LEARN_CAP = 20000
_TURN_STORE_VERSION = 1
_TURN_SAVE_DELAY_S = 300


def path_turn_store_key(entry_id: str) -> str:
    """The storage key of the learned turns (removed with the entry)."""
    return f"roomba_plus_path_turn_{entry_id}"


def nearest_turn_angle(turn_rad: float) -> str:
    """The angle of the setting nearest to a turn, in its sense."""
    degrees = math.degrees(turn_rad) % 360.0
    return min(
        TURN_ANGLES,
        key=lambda a: min(abs(degrees - int(a)), 360.0 - abs(degrees - int(a))),
    )


def turn_map_key(umf: Any, header_turn_rad: float) -> str | None:
    """Which map the counts belong to: its id and the header's turn.

    A map turned in the iRobot app gets a new header, and whether the
    robot's answers turn with it is exactly what the header got wrong.
    So a new header starts its counts afresh instead of inheriting a
    verdict about the old one. None without a cloud map.
    """
    if not isinstance(umf, dict) or not umf.get("pmap_id"):
        return None
    return f"{umf['pmap_id']}|{round(math.degrees(header_turn_rad)) % 360}"


class PathTurnLearner:
    """Per map, how many positions each angle puts in a room, and the
    angle those counts chose (4.5.0b3). Counts and map ids only: no
    position is kept."""

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, Any]] = {}
        self._store: Any = None

    def add(self, key: str | None, in_room: dict[str, bool], header_angle: str) -> bool:
        """One position tried at every angle. True when the angle in
        force for the map changed with it."""
        if key is None:
            return False
        entry = self._maps.setdefault(
            key, {"positions": 0, **{angle: 0 for angle in TURN_ANGLES}}
        )
        before = self.angle(key, header_angle)
        entry["positions"] += 1
        for angle, inside in in_room.items():
            if inside and angle in TURN_ANGLES:
                entry[angle] += 1
        if entry["positions"] > TURN_LEARN_CAP:
            for name in ("positions", *TURN_ANGLES):
                entry[name] //= 2
        positions = entry["positions"]
        if positions >= TURN_LEARN_MIN_POSITIONS:
            best = max(TURN_ANGLES, key=lambda a: entry[a])
            if (
                best != before
                and entry[best] >= TURN_LEARN_MIN_SHARE * positions
                and entry[best] >= TURN_LEARN_MARGIN * entry[before]
            ):
                entry["decided"] = best
        if self._store is not None:
            self._store.async_delay_save(self._payload, _TURN_SAVE_DELAY_S)
        return self.angle(key, header_angle) != before

    def angle(self, key: str | None, header_angle: str) -> str:
        """The angle in force: the one learned, else the header's."""
        decided = self._maps.get(key, {}).get("decided") if key is not None else None
        return decided if decided in TURN_ANGLES else header_angle

    def learned(self, key: str | None) -> str | None:
        """The angle the counts chose for the map, or None."""
        decided = self._maps.get(key, {}).get("decided") if key is not None else None
        return decided if decided in TURN_ANGLES else None

    def diagnostics(self, key: str | None) -> dict[str, Any] | None:
        """The counts for one map, without its id."""
        entry = self._maps.get(key) if key is not None else None
        if entry is None:
            return None
        return {
            "positions": entry["positions"],
            **{angle: entry[angle] for angle in TURN_ANGLES},
            "learned": self.learned(key),
        }

    def _payload(self) -> dict[str, Any]:
        return {"maps": self._maps}

    async def async_load(self, hass: Any, entry_id: str) -> None:
        """Read the counts kept for this entry; anything odd is dropped."""
        from homeassistant.helpers.storage import Store

        self._store = Store(hass, _TURN_STORE_VERSION, path_turn_store_key(entry_id))
        stored = await self._store.async_load()
        maps = stored.get("maps") if isinstance(stored, dict) else None
        if not isinstance(maps, dict):
            return
        for key, entry in maps.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            counts = {
                name: entry.get(name) for name in ("positions", *TURN_ANGLES)
            }
            if not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0
                for v in counts.values()
            ):
                continue
            if entry.get("decided") in TURN_ANGLES:
                counts["decided"] = entry["decided"]
            self._maps[key] = counts


def auto_position_turn(learner: Any, umf: Any) -> tuple[float, str | None]:
    """What "Automatic" turns requested positions by (4.5.0b3).

    The header's turn (cloud_coordinator.umf_position_turn), unless this
    map's path has shown another angle clearly: source "learned" then.
    The header's exact turn is kept where the counts agree with it.
    """
    from .cloud_coordinator import umf_position_turn

    header, source = umf_position_turn(umf)
    if not isinstance(learner, PathTurnLearner):
        return header, source
    learned = learner.learned(turn_map_key(umf, header))
    if learned is None or learned == nearest_turn_angle(header):
        return header, source
    turn = math.radians(int(learned))
    return math.atan2(math.sin(turn), math.cos(turn)), "learned"


def room_at_position(
    data: Any, x_mm: float, y_mm: float, *, frame: str, zones: Collection[str] = ()
) -> str | None:
    """The room a position lies in, or None where none can be said.

    `zones`: the clean zones of the running clean (4.6.2, #252). A
    position inside one of their outlines gives the zone, so a zone
    clean moves from zone to zone as rooms do; anywhere else, the room.

    `frame` "umf": a position requested from a Smart Map robot, turned
    into the cloud map's own frame (image._handle_live_position) -- the
    frame the aligner keeps the rooms in. "pose": the robot's own
    position, converted by the aligner on a Smart Map robot, or looked
    up in the areas Roomba+ divided a 900-series floor into.
    """
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        return None
    aligner = getattr(data, "umf_aligner", None)
    if frame == "umf":
        # Already in the rooms' frame: no alignment needed, only rooms.
        if aligner is None:
            return None
        return _place_at(aligner, x_mm, y_mm, zones)
    if aligner is not None and getattr(aligner, "aligned", False):
        point = aligner.pose_to_umf(x_mm, y_mm)
        if point is None:
            return None
        return _place_at(aligner, point[0], point[1], zones)
    return _seg_room_at(getattr(data, "room_seg_store", None), x_mm, y_mm)


def _place_at(
    aligner: Any, x_umf: float, y_umf: float, zones: Collection[str]
) -> str | None:
    """The zone of the running clean at a UMF point, else the room."""
    if zones:
        zone = aligner.zone_name_at(x_umf, y_umf, zones)
        if zone:
            return str(zone)
    name = aligner.room_name_at(x_umf, y_umf)
    return str(name) if name else None


def _seg_room_at(store: Any, x_mm: float, y_mm: float) -> str | None:
    """The 900-series area at a position: its cell, or one next to it.

    The areas are cells the robot covered, so a position on an area's
    edge can fall just outside; one cell around is searched, nearest
    first. Hidden areas are not rooms anyone named or wants.
    """
    rooms = getattr(store, "rooms", None)
    if not isinstance(rooms, dict) or not rooms:
        return None
    gx = int(math.floor(x_mm / _SEG_CELL_MM))
    gy = int(math.floor(y_mm / _SEG_CELL_MM))
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        cell = (gx + dx, gy + dy)
        for room in rooms.values():
            if getattr(room, "hidden", False):
                continue
            if cell in getattr(room, "cells", ()):
                return _seg_room_name(room)
    return None


def _seg_room_name(room: Any) -> str:
    """An area's name, or "Area N" as the naming form shows it."""
    name = getattr(room, "name", "")
    if name:
        return str(name)
    room_id = str(getattr(room, "id", ""))
    return f"Area {room_id.removeprefix('room_')}"
