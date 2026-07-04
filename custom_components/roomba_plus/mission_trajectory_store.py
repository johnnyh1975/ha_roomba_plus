"""MissionTrajectoryStore — bounded per-mission trajectory history for
EPHEMERAL robots (v3.2.1).

Motivated directly by a user request ("die letzten 2-3 Missionen mit
Linien sehen") that MapRenderer cannot serve on its own: MapRenderer's
reset() intentionally wipes self._points at the start of every mission
— matching the 980's own vSLAM design intent (a fresh map each cycle,
per manufacturer teardown sources: "creates a new map each time it
begins a cleaning cycle, just in case you've moved furniture"). Only the
CURRENT mission's raw path therefore exists in MapRenderer, either in
memory or in its own persisted state. This store keeps an independent,
bounded window of the last N missions' raw pose points (mm, dock-
relative), so a "show me the last few missions" view becomes possible
without touching MapRenderer's own reset-per-mission contract.

Secondary purpose: prerequisite for a future wall-follow trajectory-
curvature structural signal (unvalidated, see RoomSeg v2 design doc) —
that idea needs multiple real missions' worth of path data to test
against, which does not currently exist anywhere to test it with.

Storage key: roomba_plus_trajectories_{entry_id}
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_PREFIX = "roomba_plus_trajectories"
_HA_STORE_VERSION  = 1
PAYLOAD_VERSION    = 1

# ~516 KB for MAX_MISSIONS=10 missions at ~3300 points each (measured on
# real field data) — trivial storage cost, see design doc Abschnitt 7.
MAX_MISSIONS = 10


class MissionTrajectoryStore:
    """Bounded FIFO of the last MAX_MISSIONS missions' raw pose points.

    Lifecycle: instantiate in async_setup_entry for EPHEMERAL robots,
    async_load() immediately after, async_record_mission() at mission
    end (image.py), async_save() alongside it.
    """

    def __init__(self) -> None:
        self._missions: deque[dict[str, Any]] = deque(maxlen=MAX_MISSIONS)

    def _get_store(self, hass: Any, entry_id: str) -> Any:
        from homeassistant.helpers.storage import Store
        return Store(hass, _HA_STORE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}", private=True)

    # ── Persistence ────────────────────────────────────────────────────────────

    async def async_load(self, hass: Any, entry_id: str) -> None:
        store = self._get_store(hass, entry_id)
        data = await store.async_load()
        if not data or not isinstance(data, dict) or data.get("version") != PAYLOAD_VERSION:
            _LOGGER.debug("MissionTrajectoryStore: no compatible data for %s", entry_id)
            return
        try:
            raw = data.get("missions") or []
            self._missions = deque(
                (
                    {
                        "mission_key": str(m.get("mission_key", "")),
                        "ended_at": str(m.get("ended_at", "")),
                        "points": [
                            [float(p[0]), float(p[1])]
                            for p in m.get("points", [])
                            if isinstance(p, (list, tuple)) and len(p) == 2
                        ],
                        # v3.2.1 — additive field, same no-version-bump
                        # precedent as elsewhere: a payload saved before
                        # this existed simply has no "thetas" key.
                        "thetas": [
                            float(t) for t in m.get("thetas", [])
                            if isinstance(t, (int, float))
                        ],
                    }
                    for m in raw
                    if isinstance(m, dict)
                ),
                maxlen=MAX_MISSIONS,
            )
            _LOGGER.debug(
                "MissionTrajectoryStore: loaded %d mission(s) for %s",
                len(self._missions), entry_id,
            )
        except (TypeError, ValueError, KeyError, AttributeError, IndexError) as exc:
            _LOGGER.warning(
                "MissionTrajectoryStore: failed to load for %s — %s; starting empty",
                entry_id, exc,
            )
            self._missions = deque(maxlen=MAX_MISSIONS)

    async def async_save(self, hass: Any, entry_id: str) -> None:
        store = self._get_store(hass, entry_id)
        await store.async_save({
            "version": PAYLOAD_VERSION,
            "missions": list(self._missions),
        })

    # ── Update ─────────────────────────────────────────────────────────────────

    def record_mission(
        self,
        mission_key: str,
        points_mm: list[tuple[float, float]],
        ended_at: str = "",
        thetas_deg: list[float] | None = None,
    ) -> None:
        """Append one mission's trajectory to the bounded window.

        points_mm: dock-relative (x_mm, y_mm) pose points, same
        convention as GridStore.update_from_mission()'s pose_points.
        Oldest mission is dropped automatically once MAX_MISSIONS is
        exceeded (deque maxlen).

        thetas_deg: optional parallel list, same index alignment as
        points_mm (thetas_deg[i] is the heading for points_mm[i]).
        Kept as a SEPARATE list rather than widening points_mm's own
        tuple shape — same rationale as image.py's
        _mission_points/_mission_thetas split: avoids changing an
        already-established shape that other code (and tests) already
        depend on. Prerequisite for the Dock-Anchor-Korrektur v2
        (rotation correction, see design doc) and for a future
        wall-follow curvature signal — not consumed by anything yet.
        Omitted (None/mismatched length) simply means no theta data for
        that mission, not an error.
        """
        if not points_mm:
            return
        thetas = list(thetas_deg) if thetas_deg and len(thetas_deg) == len(points_mm) else []
        self._missions.append({
            "mission_key": mission_key,
            "ended_at": ended_at,
            "points": [[float(x), float(y)] for x, y in points_mm],
            "thetas": [float(t) for t in thetas],
        })

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def missions(self) -> list[dict[str, Any]]:
        """Oldest-first list of {mission_key, ended_at, points} dicts."""
        return list(self._missions)

    @property
    def mission_count(self) -> int:
        return len(self._missions)

    def last_n(self, n: int) -> list[dict[str, Any]]:
        """Most recent n missions, newest-last (same order as .missions)."""
        if n <= 0:
            return []
        return list(self._missions)[-n:]
