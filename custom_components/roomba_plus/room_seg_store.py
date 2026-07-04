"""RoomSegStore — persisted room/door segmentation results for EPHEMERAL
robots, computed from GridStore's visited-cell grid via room_segmentation.py
(dependency-free distance-transform + watershed pipeline).

Only used for EPHEMERAL map capability (900-series robots like the Roomba 980).

This is a COMPLEMENT to ZoneStore, not a replacement. ZoneStore's per-mission
gap-split zones remain the live system that the room-naming UI, select
entities, etc. read from. RoomSegStore is the new watershed-based
segmentation engine, exposed for now via diagnostics only, while the two
approaches are compared in the field on real installations before any
decision to replace ZoneStore's room detection.

Identity stability: every recompute can shift cell-set boundaries slightly
as more missions accumulate. A new room candidate is matched against
EXISTING persisted rooms by Jaccard overlap (intersection / union of cell
sets) so that a user-assigned name or confirmation survives across
recomputations rather than being silently discarded and recreated as a
fresh, unnamed room each time. Existing rooms with no good match in a new
recompute are KEPT as-is, never auto-deleted — removal is left as an
explicit future user action, not something a noisy single mission should
trigger on its own.

Storage key: roomba_plus_roomseg_{entry_id}
Storage version: 1
"""
from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .room_segmentation import RoomSegmentationResult, segment_rooms

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_roomseg"
_HA_STORE_VERSION = 1
PAYLOAD_VERSION = 1

# Must match GridStore's CELL_MM — kept as a separate constant rather than
# importing grid_store.py, since this module takes the cell dict as a
# plain argument (caller already has it) and shouldn't need to know
# GridStore's internal layout to do so.
CELL_MM = 150.0

# Recompute gating: the pipeline is CPU-bound (~100ms-1.5s depending on
# home size, see ROOM_SEGMENTATION_NOTES.md) — skip recomputation if the
# grid has barely grown since last time, rather than re-running on every
# single mission end.
MIN_NEW_CELLS_TO_RECOMPUTE = 30

# A new room candidate is "the same" as an existing persisted room if
# their cell sets overlap by at least this Jaccard ratio. Below this,
# it's treated as a newly-discovered room.
ROOM_MATCH_JACCARD = 0.30

# ZoneStore migration (Stage 2): a SegRoom is considered "the same room"
# as a ZoneStore zone if at least this fraction of the SegRoom's actual
# cells fall within the zone's (EMA-approximated, less precise) bounding
# box. Deliberately stricter than ROOM_MATCH_JACCARD above — ZoneStore's
# bboxes are known to be imprecise (containment-trap merging, see
# project history), so a generous-but-not-trivial threshold avoids
# migrating a name onto the wrong room.
MIGRATION_MATCH_THRESHOLD = 0.5

# v3.2.1 STALE-ROOM CLEANUP — field-confirmed bug: a persisted room that
# lost every match this round (see ROOM_MATCH_JACCARD above) is normally
# kept forever, by design — GridStore cells can drop out of a recompute's
# clusters from ordinary grid churn without the room having genuinely
# disappeared. But when an unmatched room's ENTIRE former territory has
# been reclaimed by a DIFFERENT room that WAS matched this round, that is
# a different situation: the old room's cells now overlap a currently-
# live room, not merely "absent this round". On a real OG this produced
# a 122-cell phantom room 100% contained inside a 521-cell live room,
# complete with 3 doors connecting the phantom to rooms it no longer
# bordered anywhere. STALE_ABSORPTION_RATIO is the fraction of an
# unmatched room's cells that must now belong to one single currently-
# matched room before it's deleted as absorbed, rather than kept as
# possibly-just-decayed. Conservative (0.8, not the whole cell set) so a
# room that merely SHARES a large fringe with its neighbour after a
# recompute boundary wobble isn't deleted — only near-total absorption is.
STALE_ABSORPTION_RATIO = 0.8

# v3.2.1 DOOR-DISTANCE-GATE — _match_doors previously kept exactly ONE
# SegDoor per unordered room-pair, matched purely on which two rooms it
# connects. In an open-plan layout, two rooms can share more than one
# real doorway/opening; on a real OG two door observations for the same
# pair measured 2.35 m and 1.96 m apart — almost certainly two distinct
# physical openings, not sensor noise around one. A newly-detected
# crossing only merges into an existing door of that pair if it falls
# within this radius of it; otherwise it becomes a new, additional door
# for the same pair. 800mm is comfortably larger than the largest
# genuine single-doorway measurement jitter seen in the field data
# (max spread among doors NOT suspected of being two openings: 765mm)
# while remaining well under the two suspect pairs' 1956mm/2348mm gaps.
DOOR_MERGE_DISTANCE_MM = 800.0

# v3.2.1 BOUNDARY-HISTORY — rolling window of room-pair adjacency sets
# across recomputes, for a future temporal-stability confidence signal.
# Simulated (BFS-growth-approximated) end-to-end testing against real
# field data showed NO effect versus the current single-snapshot
# behaviour — every room-pair boundary was already 60-100% stable once
# aggregated per pair, not per individual cell edge. Kept as cheap
# scaffolding for re-evaluation once genuine multi-week recompute
# history exists (the simulation could only approximate growth, not
# replace real time), not because a benefit is expected here.
MAX_BOUNDARY_HISTORY = 15


@dataclass
class SegRoom:
    id: str
    cells: set[tuple[int, int]] = field(default_factory=set)
    name: str = ""
    confirmed: bool = False
    hidden: bool = False  # parity with ZoneStore.Zone.hidden (v1.7.0 L7)
    recompute_count: int = 0  # ZoneStore-confidence equivalent — see `confidence`

    @property
    def area_m2(self) -> float:
        return len(self.cells) * (CELL_MM / 1000.0) ** 2

    @property
    def confidence(self) -> float:
        """0.0-1.0, grows with the number of recomputes this room has
        survived a match through — same role and same /10 scaling as
        ZoneStore.Zone.confidence (min(1.0, observations/10))."""
        return min(1.0, self.recompute_count / 10.0)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(x_min, x_max, y_min, y_max) in mm, dock-relative — computed
        directly from the actual occupied cells, not an EMA approximation
        like ZoneStore.Zone.bbox. Always exact for whatever cells this
        room currently has, even though it isn't a rectangle."""
        if not self.cells:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [c[0] for c in self.cells]
        ys = [c[1] for c in self.cells]
        return (
            min(xs) * CELL_MM, (max(xs) + 1) * CELL_MM,
            min(ys) * CELL_MM, (max(ys) + 1) * CELL_MM,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cells": [[x, y] for x, y in sorted(self.cells)],
            "name": self.name,
            "confirmed": self.confirmed,
            "hidden": self.hidden,
            "recompute_count": self.recompute_count,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SegRoom:
        return SegRoom(
            id=d["id"],
            cells={(int(p[0]), int(p[1])) for p in d.get("cells", [])},
            name=d.get("name", ""),
            confirmed=bool(d.get("confirmed", False)),
            hidden=bool(d.get("hidden", False)),
            recompute_count=int(d.get("recompute_count", 0)),
        )


# Mirrors GeometryStore.DoorMarker's MAX_MARKER_OBSERVATIONS exactly —
# same cap, same reasoning (bounded memory, recent-biased without a full
# decay scheme).
MAX_DOOR_OBSERVATIONS = 20


@dataclass
class SegDoor:
    id: str
    room_a: str
    room_b: str
    cell: tuple[int, int]
    saddle_mm: float
    cx: float = 0.0
    cy: float = 0.0
    observations: list[list[float]] = field(default_factory=list)
    # observations stored as [[x_mm, y_mm], ...] — plain lists for JSON round-trip.

    def update_position(self, cell: tuple[int, int]) -> None:
        """Record this recompute's door-crossing cell and recompute the
        median (cx, cy) in mm across recent observations.

        Mirrors DoorMarker.update() exactly — the same median-of-recent-
        observations approach that makes the position robust to a door
        being at a different swing angle (and therefore a slightly
        different measured crossing point) from one mission to the next,
        rather than jumping to whatever the single latest recompute
        happened to measure.
        """
        self.cell = cell
        x_mm, y_mm = cell[0] * CELL_MM, cell[1] * CELL_MM
        self.observations.append([x_mm, y_mm])
        if len(self.observations) > MAX_DOOR_OBSERVATIONS:
            self.observations = self.observations[-MAX_DOOR_OBSERVATIONS:]
        self.cx = statistics.median(p[0] for p in self.observations)
        self.cy = statistics.median(p[1] for p in self.observations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "room_a": self.room_a,
            "room_b": self.room_b,
            "cell": [self.cell[0], self.cell[1]],
            "saddle_mm": self.saddle_mm,
            "cx": self.cx,
            "cy": self.cy,
            "observations": self.observations,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SegDoor:
        return SegDoor(
            id=d["id"],
            room_a=d["room_a"],
            room_b=d["room_b"],
            cell=(int(d["cell"][0]), int(d["cell"][1])),
            saddle_mm=float(d.get("saddle_mm", 0.0)),
            cx=float(d.get("cx", 0.0)),
            cy=float(d.get("cy", 0.0)),
            observations=[[float(p[0]), float(p[1])] for p in d.get("observations", [])],
        )


def _unordered_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


class RoomSegStore:
    def __init__(
        self,
        min_distance_cells: float = 8.0,
        merge_ratio: float = 0.55,
    ) -> None:
        self.rooms: dict[str, SegRoom] = {}
        self.doors: list[SegDoor] = []
        self.last_cell_count: int = 0
        self._next_room_n: int = 1
        self._next_door_n: int = 1
        self._min_distance_cells = min_distance_cells
        self._merge_ratio = merge_ratio
        self.migrated_from_zonestore: bool = False
        # v3.2.1 BOUNDARY-HISTORY — see MAX_BOUNDARY_HISTORY docstring.
        # Each entry: sorted list of [room_a, room_b] pairs adjacent
        # (connected by a door) at that recompute.
        self._boundary_history: deque[list[list[str]]] = deque(maxlen=MAX_BOUNDARY_HISTORY)

    # ── Recompute ────────────────────────────────────────────────────────────

    def maybe_recompute(self, cells: dict[tuple[int, int], float]) -> bool:
        """Re-run segmentation if the grid has grown enough since the last
        computation (or there's no prior result at all). Returns True if a
        recompute actually ran."""
        if self.rooms and (len(cells) - self.last_cell_count) < MIN_NEW_CELLS_TO_RECOMPUTE:
            return False
        if not cells:
            return False
        self._recompute(cells)
        self.last_cell_count = len(cells)
        return True

    def _recompute(self, cells: dict[tuple[int, int], float]) -> None:
        result = segment_rooms(
            cells,
            cell_mm=CELL_MM,
            min_distance_cells=self._min_distance_cells,
            merge_ratio=self._merge_ratio,
        )
        label_to_id = self._match_rooms(result)
        self._match_doors(result, label_to_id)
        # v3.2.1 BOUNDARY-HISTORY — snapshot this recompute's room-pair
        # adjacency (from the just-updated door list) into the rolling
        # window. See MAX_BOUNDARY_HISTORY docstring for status.
        pairs = sorted({_unordered_key(d.room_a, d.room_b) for d in self.doors})
        self._boundary_history.append([[a, b] for a, b in pairs])

    def _match_rooms(self, result: RoomSegmentationResult) -> dict[int, str]:
        """Match each new cluster (keyed by its segment_rooms() integer
        label) to an existing persisted room id, or assign a fresh one.
        Returns the label -> persisted-id mapping (used by _match_doors)."""
        matched_existing: set[str] = set()
        label_to_id: dict[int, str] = {}

        # Match in descending cluster-size order: bigger, more confident
        # clusters claim their best-matching existing room first.
        labels_by_size = sorted(result.rooms.keys(), key=lambda lbl: -len(result.rooms[lbl]))

        for label in labels_by_size:
            new_cells = result.rooms[label]
            best_id, best_ratio = None, 0.0
            for rid, room in self.rooms.items():
                if rid in matched_existing:
                    continue
                inter = len(new_cells & room.cells)
                if inter == 0:
                    continue
                union = len(new_cells | room.cells)
                ratio = inter / union if union else 0.0
                if ratio > best_ratio:
                    best_ratio, best_id = ratio, rid
            if best_id is not None and best_ratio >= ROOM_MATCH_JACCARD:
                label_to_id[label] = best_id
                matched_existing.add(best_id)
                self.rooms[best_id].cells = set(new_cells)
                self.rooms[best_id].recompute_count += 1
            else:
                rid = f"room_{self._next_room_n}"
                self._next_room_n += 1
                self.rooms[rid] = SegRoom(id=rid, cells=set(new_cells))
                label_to_id[label] = rid

        # v3.2.1 STALE-ROOM CLEANUP — see STALE_ABSORPTION_RATIO docstring
        # above. Runs AFTER matching so `self.rooms[mid].cells` already
        # reflects THIS round's freshly-matched cell sets, not last
        # round's. Deletion happens before _match_doors runs (called
        # right after this method in _recompute()), so the existing
        # "both connected rooms still exist" door-preservation guard in
        # _match_doors automatically drops any door that pointed at a
        # room deleted here — no separate cascade-delete needed.
        #
        # v3.2.1 FIELD FIX — generalised from "one single absorber
        # reaches the ratio" to "the UNION of everything absorbed by
        # matched rooms reaches the ratio". Field-confirmed gap in the
        # original single-absorber version: a real post-3.2.1 recompute
        # produced a room 100% claimed elsewhere but split across THREE
        # different matched rooms (72.7% / 10.0% / 17.2%) — no single
        # absorber crossed 80%, so the original `any(...)` check missed
        # a case that was, in aggregate, exactly as stale as the
        # original single-absorber room_7 case this cleanup was built
        # for. Union (not sum) of the per-absorber overlaps, in case a
        # cell were ever double-claimed by two matched rooms at once
        # (shouldn't happen post-recompute, but a union costs nothing
        # extra and is correct either way).
        stale_ids = []
        for rid, room in self.rooms.items():
            if rid in matched_existing or not room.cells:
                continue
            absorbed = set()
            for mid in matched_existing:
                absorbed |= (room.cells & self.rooms[mid].cells)
            if len(absorbed) / len(room.cells) >= STALE_ABSORPTION_RATIO:
                stale_ids.append(rid)
        for rid in stale_ids:
            _LOGGER.debug(
                "RoomSegStore: deleting %s — %d cells absorbed into another room",
                rid, len(self.rooms[rid].cells),
            )
            del self.rooms[rid]

        return label_to_id

    def _match_doors(
        self, result: RoomSegmentationResult, label_to_id: dict[int, str]
    ) -> None:
        # v3.2.1 DOOR-DISTANCE-GATE — see DOOR_MERGE_DISTANCE_MM docstring.
        # Was dict[pair, SegDoor] (exactly one door per pair); now
        # dict[pair, list[SegDoor]] so genuinely distinct openings between
        # the same two rooms (open-plan layouts) can coexist.
        existing_by_pair: dict[tuple[str, str], list[SegDoor]] = {}
        for d in self.doors:
            existing_by_pair.setdefault(_unordered_key(d.room_a, d.room_b), []).append(d)

        matched_door_ids: set[str] = set()
        new_doors: list[SegDoor] = []
        for d in result.doors:
            room_a = label_to_id.get(d["a"])
            room_b = label_to_id.get(d["b"])
            if room_a is None or room_b is None:
                continue
            key = _unordered_key(room_a, room_b)
            new_x_mm, new_y_mm = d["cell"][0] * CELL_MM, d["cell"][1] * CELL_MM

            # Find the CLOSEST existing door of this pair, if any is
            # within DOOR_MERGE_DISTANCE_MM — closest, not first, so a
            # pair with two existing doors merges into whichever one this
            # detection actually belongs to.
            best_existing, best_dist = None, None
            for existing in existing_by_pair.get(key, []):
                dist = ((existing.cx - new_x_mm) ** 2 + (existing.cy - new_y_mm) ** 2) ** 0.5
                if best_dist is None or dist < best_dist:
                    best_existing, best_dist = existing, dist

            if best_existing is not None and best_dist <= DOOR_MERGE_DISTANCE_MM:
                best_existing.update_position(d["cell"])
                best_existing.saddle_mm = d["saddle_mm"]
                matched_door_ids.add(best_existing.id)
                new_doors.append(best_existing)
            else:
                door = SegDoor(
                    id=f"door_{self._next_door_n}",
                    room_a=room_a, room_b=room_b,
                    cell=d["cell"], saddle_mm=d["saddle_mm"],
                )
                door.update_position(d["cell"])
                self._next_door_n += 1
                matched_door_ids.add(door.id)
                new_doors.append(door)

        # Mirror the room-preservation policy (module docstring + see
        # test_unmatched_existing_room_is_kept_not_deleted): a door whose
        # room pair wasn't re-detected this round is NOT auto-deleted.
        # GridStore decays/prunes low-traffic cells every mission (see
        # grid_store.py) — a narrow, infrequently-crossed doorway can
        # drop out of the visited-cell set for one recompute without
        # having genuinely stopped existing. Wiping the door in that
        # case loses its entire `observations` history and stable `id`,
        # defeating the point of update_position()'s median smoothing.
        # Kept only while both connected rooms still exist — rooms ARE
        # now auto-deleted on absorption (v3.2.1 STALE-ROOM CLEANUP,
        # _match_rooms above), which is exactly the case this guard
        # exists to cascade into: a door pointing at a just-deleted
        # phantom room is dropped here, no separate cleanup needed.
        for door in self.doors:
            if (
                door.id not in matched_door_ids
                and door.room_a in self.rooms
                and door.room_b in self.rooms
            ):
                new_doors.append(door)

        self.doors = new_doors

    def migrate_from_zone_store(self, zones: list[Any]) -> int:
        """One-time migration of names/confirmed/hidden from ZoneStore's
        zones into matching SegRooms, by bounding-box overlap.

        Takes a plain list of zone-like objects (duck-typed: needs
        .x_min/.x_max/.y_min/.y_max/.name/.confirmed/.hidden) rather than
        importing ZoneStore directly, keeping the two stores decoupled —
        the caller (typically __init__.py's setup flow) already has both.

        Guarded by self.migrated_from_zonestore so this only ever runs
        once per store: re-running on every load would silently overwrite
        a user's RoomSegStore-side renames with stale ZoneStore data after
        the fact, which is exactly the kind of silent-overwrite this
        module's docstring already promises never happens for room
        identity in general.

        Only migrates zones the user actually confirmed/named in
        ZoneStore — there is nothing useful to carry over from an
        unconfirmed, auto-generated zone name. Each zone is matched to
        whichever SegRoom has the highest fraction of its OWN cells
        falling inside that zone's bounding box; below
        MIGRATION_MATCH_THRESHOLD, no migration happens for that zone.

        Returns the number of rooms that received migrated data.
        """
        if self.migrated_from_zonestore:
            return 0
        self.migrated_from_zonestore = True  # one-shot attempt, even if 0 zones match well enough

        migrated = 0
        for zone in zones:
            if not getattr(zone, "confirmed", False):
                continue
            x_min = getattr(zone, "x_min", 0.0) / CELL_MM
            x_max = getattr(zone, "x_max", 0.0) / CELL_MM
            y_min = getattr(zone, "y_min", 0.0) / CELL_MM
            y_max = getattr(zone, "y_max", 0.0) / CELL_MM

            best_room, best_frac = None, 0.0
            for room in self.rooms.values():
                if not room.cells:
                    continue
                inside = sum(
                    1 for (gx, gy) in room.cells
                    if x_min <= gx < x_max and y_min <= gy < y_max
                )
                frac = inside / len(room.cells)
                if frac > best_frac:
                    best_frac, best_room = frac, room

            if best_room is not None and best_frac >= MIGRATION_MATCH_THRESHOLD:
                best_room.name = getattr(zone, "name", best_room.name)
                best_room.confirmed = True
                best_room.hidden = getattr(zone, "hidden", False)
                migrated += 1

        return migrated

    # ── User edits ───────────────────────────────────────────────────────────
    # Method names/behaviour mirror ZoneStore's rename_zone/hide_zone/
    # unhide_zone exactly (same "rename also confirms" semantics) so a
    # later consumer swap (select.py, config_flow.py) is a mechanical
    # rename of the call site, not a behaviour change.

    def rename_room(self, room_id: str, name: str) -> bool:
        """Rename a room and mark it confirmed. Returns True if found."""
        room = self.rooms.get(room_id)
        if room is None:
            return False
        room.name = name.strip()
        room.confirmed = True
        return True

    def hide_room(self, room_id: str) -> bool:
        """Set room.hidden = True. Removes from selectors and repair issues."""
        room = self.rooms.get(room_id)
        if room is None:
            return False
        room.hidden = True
        return True

    def unhide_room(self, room_id: str) -> bool:
        """Set room.hidden = False, restoring it to selectors."""
        room = self.rooms.get(room_id)
        if room is None:
            return False
        room.hidden = False
        return True

    def confirm_room(self, room_id: str) -> bool:
        """Mark a room confirmed without renaming it."""
        room = self.rooms.get(room_id)
        if room is None:
            return False
        room.confirmed = True
        return True

    # ── Diagnostics ──────────────────────────────────────────────────────────

    @property
    def unconfirmed_rooms(self) -> list[SegRoom]:
        """Rooms detected but not yet named by the user.

        Hidden rooms are excluded — no repair issue fires for hidden rooms.
        Mirrors ZoneStore.unconfirmed_zones exactly.
        """
        return [r for r in self.rooms.values() if not r.confirmed and not r.hidden]

    @property
    def has_unconfirmed_rooms(self) -> bool:
        return bool(self.unconfirmed_rooms)

    def diagnostic_info(self) -> dict[str, Any]:
        return {
            "room_count": len(self.rooms),
            "door_count": len(self.doors),
            "last_cell_count": self.last_cell_count,
            "rooms": [
                {
                    "id": r.id, "name": r.name, "confirmed": r.confirmed,
                    "hidden": r.hidden, "confidence": round(r.confidence, 2),
                    "cell_count": len(r.cells), "area_m2": round(r.area_m2, 1),
                    "bbox_mm": [round(v) for v in r.bbox],
                }
                for r in self.rooms.values()
            ],
            "doors": [
                {
                    "id": d.id, "room_a": d.room_a, "room_b": d.room_b,
                    "saddle_mm": round(d.saddle_mm, 0),
                    "cx": round(d.cx), "cy": round(d.cy),
                    "observation_count": len(d.observations),
                }
                for d in self.doors
            ],
        }

    # ── Persistence ──────────────────────────────────────────────────────────

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("RoomSegStore: no persisted data for %s", entry_id)
            return
        if data.get("version", 0) != PAYLOAD_VERSION:
            _LOGGER.warning(
                "RoomSegStore: incompatible storage version %s for %s, starting clean",
                data.get("version"), entry_id,
            )
            return
        try:
            self.rooms = {
                d["id"]: SegRoom.from_dict(d) for d in data.get("rooms", [])
            }
            self.doors = [SegDoor.from_dict(d) for d in data.get("doors", [])]
            self.last_cell_count = int(data.get("last_cell_count", 0))
            self._next_room_n = int(data.get("next_room_n", 1))
            self._next_door_n = int(data.get("next_door_n", 1))
            self.migrated_from_zonestore = bool(data.get("migrated_from_zonestore", False))
            # v3.2.1 BOUNDARY-HISTORY — additive field, same no-version-
            # bump precedent as GridStore's FURNITURE/DUAL-GRID fields: a
            # payload saved before this existed simply has no key, which
            # is indistinguishable from a fresh cold start for this
            # tracker specifically.
            raw_history = data.get("boundary_history") or []
            self._boundary_history = deque(
                (
                    [[str(a), str(b)] for a, b in entry]
                    for entry in raw_history
                    if isinstance(entry, list)
                ),
                maxlen=MAX_BOUNDARY_HISTORY,
            )
            _LOGGER.debug(
                "RoomSegStore: loaded %d rooms, %d doors for %s",
                len(self.rooms), len(self.doors), entry_id,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("RoomSegStore: failed to load data for %s: %s", entry_id, exc)
            self.__init__()

    def _boundary_stability(self, room_a: str, room_b: str) -> float:
        """v3.2.1 — fraction of the boundary-history window in which
        (room_a, room_b) appeared as an adjacent (door-connected) pair.
        1.0 if history is empty (nothing to contradict stability yet).
        Not yet consumed anywhere — scaffolding, see MAX_BOUNDARY_HISTORY.
        """
        if not self._boundary_history:
            return 1.0
        key = list(_unordered_key(room_a, room_b))
        hits = sum(1 for entry in self._boundary_history if key in entry)
        return hits / len(self._boundary_history)

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        store = Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "version": PAYLOAD_VERSION,
            "rooms": [r.to_dict() for r in self.rooms.values()],
            "doors": [d.to_dict() for d in self.doors],
            "last_cell_count": self.last_cell_count,
            "next_room_n": self._next_room_n,
            "next_door_n": self._next_door_n,
            "migrated_from_zonestore": self.migrated_from_zonestore,
            "boundary_history": list(self._boundary_history),
        })
        _LOGGER.debug(
            "RoomSegStore: saved %d rooms, %d doors for %s",
            len(self.rooms), len(self.doors), entry_id,
        )
