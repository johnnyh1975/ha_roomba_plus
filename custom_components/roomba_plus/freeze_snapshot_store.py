"""FreezeSnapshotStore — periodic, immutable backup of the best-known
RoomSeg + Outline state (v3.2.1).

Motivated by the firmware-3.20.7 pose-cutoff risk (RoomSeg v2 design
doc, Abschnitt 0): iRobot can silently stop delivering the `pose` field
via a future OTA update — confirmed independently by multiple sources
(dorita980 issue #148, Roomba980-Python changelog: "Robots are no
longer reporting tracking information, therefore realtime maps will not
work"). At that point whatever RoomSegStore/OutlineStore state exists
is ALL there will ever be: no further recomputes, no further healing of
transient bugs like the room_7 phantom-room case this project already
hit once.

This store snapshots the current RoomSeg + Outline state periodically
(every INTERVAL_RECOMPUTES successful recomputes, not every one — cheap
but not free) into an entirely separate, immutable payload. It is never
itself decayed, pruned, or overwritten by the normal recompute cycle —
only replaced by a NEWER snapshot when one is taken.

Storage key: roomba_plus_freeze_{entry_id}
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_freeze"
_HA_STORE_VERSION  = 1
PAYLOAD_VERSION    = 1

# Snapshot every 20th successful recompute — frequent enough that a
# firmware cutoff loses at most ~20 recomputes' worth of improvement,
# cheap enough (one dict copy + one storage write) not to matter.
INTERVAL_RECOMPUTES = 20


class FreezeSnapshotStore:
    """Immutable last-known-good backup of RoomSeg + Outline state."""

    def __init__(self) -> None:
        self._rooms: list[dict[str, Any]] = []
        self._doors: list[dict[str, Any]] = []
        self._outline_points: list[list[float]] = []
        self._snapshotted_at: str = ""
        self._recomputes_since_snapshot: int = 0

    def _get_store(self, hass: Any, entry_id: str) -> Any:
        from homeassistant.helpers.storage import Store
        return Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}", private=True)

    # ── Persistence ────────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        store = self._get_store(hass, entry_id)
        data = await store.async_load()
        if not data or not isinstance(data, dict) or data.get("version") != PAYLOAD_VERSION:
            _LOGGER.debug("FreezeSnapshotStore: no compatible data for %s", entry_id)
            return
        try:
            self._rooms = list(data.get("rooms") or [])
            self._doors = list(data.get("doors") or [])
            self._outline_points = [
                [float(p[0]), float(p[1])] for p in (data.get("outline_points") or [])
            ]
            self._snapshotted_at = str(data.get("snapshotted_at", ""))
            _LOGGER.debug(
                "FreezeSnapshotStore: loaded snapshot from %s (%d rooms, %d doors) for %s",
                self._snapshotted_at, len(self._rooms), len(self._doors), entry_id,
            )
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            _LOGGER.warning(
                "FreezeSnapshotStore: failed to load for %s — %s; starting empty",
                entry_id, exc,
            )
            self._rooms, self._doors, self._outline_points = [], [], []
            self._snapshotted_at = ""

    async def async_save(self, hass: Any, entry_id: str) -> None:
        store = self._get_store(hass, entry_id)
        await store.async_save({
            "version": PAYLOAD_VERSION,
            "rooms": self._rooms,
            "doors": self._doors,
            "outline_points": self._outline_points,
            "snapshotted_at": self._snapshotted_at,
        })

    # ── Update ─────────────────────────────────────────────────────────────────

    def note_recompute(self) -> None:
        """Call once per successful RoomSegStore recompute — advances the
        interval counter that maybe_snapshot() checks."""
        self._recomputes_since_snapshot += 1

    def due(self) -> bool:
        """True once INTERVAL_RECOMPUTES recomputes have happened since
        the last snapshot (or none has ever been taken)."""
        return not self._snapshotted_at or self._recomputes_since_snapshot >= INTERVAL_RECOMPUTES

    def snapshot(
        self,
        rooms: list[dict[str, Any]],
        doors: list[dict[str, Any]],
        outline_points: list[tuple[float, float]],
        snapshotted_at: str,
    ) -> None:
        """Overwrite the stored snapshot with the given current-good state.

        Caller is responsible for calling this only when due() is True
        and for calling async_save() afterwards to persist it.
        """
        self._rooms = list(rooms)
        self._doors = list(doors)
        self._outline_points = [[float(x), float(y)] for x, y in outline_points]
        self._snapshotted_at = snapshotted_at
        self._recomputes_since_snapshot = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return list(self._rooms)

    @property
    def doors(self) -> list[dict[str, Any]]:
        return list(self._doors)

    @property
    def outline_points(self) -> list[list[float]]:
        return list(self._outline_points)

    @property
    def snapshotted_at(self) -> str:
        return self._snapshotted_at

    @property
    def has_snapshot(self) -> bool:
        return bool(self._snapshotted_at)
