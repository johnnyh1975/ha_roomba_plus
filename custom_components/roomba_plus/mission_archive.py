"""MissionArchive — CloudMissionArchive for Roomba+ (v2.8.0 ARC1).

Persistent 3-layer history of all cloud missions:

  Layer 1 — Derived Record (ALL missions, newest first)
    26 computed signals extracted from raw /missionhistory records.
    ~450 bytes/mission.  Used by: BAT-ARCH sensors, L5-ARC, L3-ARC,
    GS-SMART-UMF bootstrapping, REST API format=export.

  Layer 2 — Compact Event Timeline (ALL missions, parallel list to Layer 1)
    Ordered sequence of finEvent summaries (type, minimal data).
    ~300 bytes/mission.  Used by: traversal detection (GS-SMART-UMF),
    room-sequence analytics, mission replay endpoint (v4.1.0).

  Layer 3 — Raw finEvents (anomalous missions only, ~30%)
    Full finEvents list for post-mortem diagnosis.
    ~3–5 KB/record.  Gated by _is_anomalous().
    Keyed by nMssn (int) for O(1) lookup.

Storage:
  - File: .storage/roomba_plus_mission_archive_{entry_id}
  - Format: {"version": 1, "last_nMssn": int,
              "derived": [...], "timeline": [...], "raw": {nMssn: [...]}}
  - MAX_RECORDS = 800 (≈2 years at 1 clean/day).  FIFO when exceeded.
  - Derived + Timeline are co-indexed: derived[i] ↔ timeline[i].

Lifecycle (callers must respect):
  - async_load()          — call once per async_setup_entry before any query
  - async_initial_load()  — one-time paginated back-fill (background task)
  - async_delta_update()  — call after each cloud mission record arrives
  - async_save()          — called internally; callers need not call directly

GS-SMART-UMF:
  After ARC1 is deployed the GS-QUICK bridging patch (v2.7.1) becomes
  redundant: traversal_missions() returns all history, so the ≥3 mission
  gate is always satisfiable for any robot with a complete cloud history.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .cloud_api import IrobotCloudApi

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "roomba_plus_mission_archive"
MAX_RECORDS = 800          # FIFO cap — about 2 years at 1 mission/day
_INITIAL_LOAD_BATCH = 100  # records per /missionhistory page
_RATE_LIMIT_SLEEP = 2.0    # seconds between pagination requests


# ── Helpers (pure functions, no HA imports needed) ─────────────────────────

def _classify_result(record: dict) -> str:
    """Classify a raw /missionhistory record into a canonical result string.

    Mirrors cloud_coordinator.classify_mission_result but kept here to avoid
    a circular import (mission_archive ← cloud_coordinator ← mission_archive).
    """
    done: str = record.get("done", "") or ""
    done_raw: str = record.get("done_raw", "") or ""
    pause_id: int = int(record.get("pauseId", 0) or 0)

    if done == "done" or done == "ok":
        return "completed"
    if done_raw == "usrEnd":
        return "cancelled_by_user"
    if done in ("cncl", "full"):
        return "cancelled"
    if done == "bat":
        return "error_battery"
    if done == "stuck":
        return f"error_{pause_id}" if pause_id > 0 else "stuck"
    if done in ("schErr", "inc"):
        return f"error_{pause_id}" if pause_id > 0 else "cancelled"
    return "unknown"


def _wl_floor(wl_bars: list | None) -> int | None:
    """Return median signal bucket (0–4) from wlBars 5-element histogram."""
    if not isinstance(wl_bars, list) or len(wl_bars) < 5:
        return None
    total = sum(wl_bars)
    if total == 0:
        return None
    mid = total / 2
    cumulative = 0
    for i, count in enumerate(wl_bars):
        cumulative += count
        if cumulative >= mid:
            return i
    return len(wl_bars) - 1


def _wl_stability(wl_bars: list | None) -> float | None:
    """Return fraction of readings in the dominant signal bucket (0.0–1.0)."""
    if not isinstance(wl_bars, list) or len(wl_bars) < 5:
        return None
    total = sum(wl_bars)
    if total == 0:
        return None
    return round(max(wl_bars) / total, 3)


def _extract_rid(item: Any) -> str:
    """Extract region ID from plan.upcoming entry (string or object format)."""
    if isinstance(item, dict):
        return str(item.get("rid") or item.get("region_id") or "")
    return str(item) if item is not None else ""


def _ts_to_iso(unix_ts: Any) -> str | None:
    """Convert a Unix timestamp (int or float) to ISO 8601 UTC string."""
    if not unix_ts:
        return None
    try:
        return datetime.fromtimestamp(int(unix_ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    """Convert val to int, returning default on TypeError/ValueError.

    Used by _parse_derived to handle firmware-version mismatches where a
    field expected to be numeric arrives as a string (e.g. 'channel_6',
    '45m') or is unexpectedly None.
    """
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert val to float, returning default on TypeError/ValueError."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ── MissionArchive ─────────────────────────────────────────────────────────

class MissionArchive:
    """3-layer persistent history of cloud mission records.

    Thread safety: all public methods are async and must be called from
    the HA event loop thread.  The initial load is run as a background
    task and uses asyncio.sleep() for rate-limiting.
    """

    def __init__(self) -> None:
        # Layer 1: derived records, newest first (index 0 = most recent)
        self._derived: list[dict[str, Any]] = []
        # Layer 2: compact event timelines, co-indexed with _derived
        self._timeline: list[list[list]] = []
        # Layer 3: raw finEvents for anomalous missions, keyed by nMssn
        self._raw: dict[int, list] = {}
        # Set of all archived nMssn values — O(1) dedup for initial load + delta
        self._archived_nmssns: set[int] = set()
        # Highest nMssn seen — used by sensors and REST API
        self._last_nMssn: int = 0
        # v2.8.6 — startTime of the record that set the current
        # _last_nMssn high-water mark. Needed to tell apart a genuine
        # counter reset (a chronologically NEWER mission reporting a
        # lower nMssn) from an ordinary re-delivery of an already-archived
        # OLDER record (same or older startTime) — nMssn value comparison
        # alone cannot distinguish these (see async_delta_update).
        self._last_nMssn_start_ts: int = 0
        # Set to True once the initial load completes
        self._initial_load_done: bool = False
        # v2.9.0 (J) — CUMULATIVE-SQFT ACCUMULATOR. Summing area_sqft over
        # the CURRENTLY-held _derived list undercounts on any robot with
        # more than MAX_RECORDS (800) lifetime missions: _append()'s FIFO
        # trim permanently discards the oldest record (and its sqft
        # contribution) once the cap is exceeded, which would make a
        # live-recomputed sum DECREASE over time as old missions age out —
        # the opposite of what a lifetime total should ever do. This field
        # is incremented once per newly-archived mission, BEFORE any FIFO
        # trim happens, so evicted records' area is never lost. Persisted
        # across restarts; never recomputed from the current _derived list.
        self._cumulative_sqft: float = 0.0

    # ── Persistence ───────────────────────────────────────────────────────

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        """Load persisted archive from hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        data: dict | None = await store.async_load()
        if not data:
            _LOGGER.debug("MissionArchive: no persisted data for %s", entry_id)
            return
        try:
            self._derived = list(data.get("derived", []))
            self._timeline = list(data.get("timeline", []))
            self._raw = {
                int(k): v for k, v in (data.get("raw") or {}).items()
            }
            self._last_nMssn = int(data.get("last_nMssn", 0))
            self._last_nMssn_start_ts = int(data.get("last_nMssn_start_ts", 0))
            self._initial_load_done = bool(data.get("initial_load_done", False))
            # Rebuild set from derived list (not persisted to save space)
            self._archived_nmssns = {
                _safe_int(r.get("nMssn"))
                for r in self._derived
                if _safe_int(r.get("nMssn")) > 0
            }

            # v2.8.6 — one-time migration seed for installations upgrading
            # from a version that didn't persist last_nMssn_start_ts at
            # all (it would load as 0 above). Without this, the very
            # first async_delta_update() call after upgrade would see
            # last_nMssn_start_ts=0, making is_chronologically_newer
            # trivially True for any genuinely-new-but-older gap-filler
            # record (start_ts > 0 alone), reintroducing the exact
            # false-positive this version fixes for one cycle. Seed from
            # whichever currently-held derived record actually has
            # last_nMssn — self-healing after that.
            if (
                "last_nMssn_start_ts" not in data
                and self._last_nMssn
                and self._derived
            ):
                for _rec in self._derived:
                    if _safe_int(_rec.get("nMssn")) == self._last_nMssn:
                        _parsed = dt_util.parse_datetime(_rec.get("start_ts", ""))
                        if _parsed is not None:
                            self._last_nMssn_start_ts = int(_parsed.timestamp())
                        break

            # v2.9.0 (J) — load the persisted accumulator, or SEED it once
            # from whatever's currently held if this is the first load
            # after the feature was added (existing installations already
            # have up to MAX_RECORDS archived — starting the accumulator
            # at 0.0 would undercount for a long time until enough NEW
            # missions accumulate to "catch up", which is worse than the
            # old live-recompute behaviour it's meant to replace).
            if "cumulative_sqft" in data:
                self._cumulative_sqft = float(data["cumulative_sqft"] or 0.0)
            else:
                self._cumulative_sqft = sum(
                    _s for r in self._derived if (_s := r.get("sqft"))
                )
                _LOGGER.debug(
                    "MissionArchive: seeded cumulative_sqft=%.0f from %d "
                    "already-archived record(s) (one-time migration)",
                    self._cumulative_sqft, len(self._derived),
                )

            _LOGGER.debug(
                "MissionArchive: loaded %d record(s) (last_nMssn=%d, "
                "raw_anomalous=%d) for %s",
                len(self._derived), self._last_nMssn,
                len(self._raw), entry_id,
            )
        except (TypeError, ValueError, KeyError) as exc:
            _LOGGER.warning(
                "MissionArchive: failed to load — %s; starting empty", exc
            )
            self._derived = []
            self._timeline = []
            self._raw = {}
            self._last_nMssn = 0
            self._last_nMssn_start_ts = 0
            self._cumulative_sqft = 0.0

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        """Persist current archive to hass.storage."""
        store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        await store.async_save({
            "version": STORAGE_VERSION,
            "last_nMssn": self._last_nMssn,
            "last_nMssn_start_ts": self._last_nMssn_start_ts,
            "initial_load_done": self._initial_load_done,
            "derived": self._derived,
            "timeline": self._timeline,
            # JSON keys must be strings
            "raw": {str(k): v for k, v in self._raw.items()},
            "cumulative_sqft": self._cumulative_sqft,
        })

    # ── Initial load (background task) ────────────────────────────────────

    async def async_initial_load(
        self,
        cloud_api: "IrobotCloudApi",
        blid: str,
        hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        """One-time paginated back-fill of full cloud mission history.

        Fetches /missionhistory in batches of 100, oldest-to-newest after
        reversal.  Respects a 2-second rate-limit sleep between pages.
        Saves to storage after completion.

        This runs as a background task — it must not block the HA event loop
        and must handle failures gracefully (coordinator may not be available).
        """
        if self._initial_load_done and self._derived:
            _LOGGER.debug(
                "MissionArchive: initial load already done for %s (%d records)",
                entry_id, len(self._derived),
            )
            return

        _LOGGER.info(
            "MissionArchive: starting initial load for %s", entry_id
        )
        all_raw: list[dict] = []
        before_ts: int | None = None
        pages_fetched = 0

        while True:
            try:
                batch = await cloud_api.get_mission_history(
                    blid, count=_INITIAL_LOAD_BATCH, before_ts=before_ts
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "MissionArchive: initial load page %d failed for %s — %s; "
                    "stopping with %d records so far",
                    pages_fetched + 1, entry_id, exc, len(all_raw),
                )
                break

            if not isinstance(batch, list) or not batch:
                break

            mission_records = [r for r in batch if isinstance(r, dict)]
            if not mission_records:
                break

            all_raw.extend(mission_records)
            pages_fetched += 1
            before_ts = int(mission_records[-1].get("startTime", 0) or 0)
            if not before_ts:
                break

            _LOGGER.debug(
                "MissionArchive: fetched page %d (%d records, total=%d) for %s",
                pages_fetched, len(mission_records), len(all_raw), entry_id,
            )

            # Stop if we've reached our storage cap
            if len(all_raw) >= MAX_RECORDS:
                _LOGGER.debug(
                    "MissionArchive: reached MAX_RECORDS=%d for %s", MAX_RECORDS, entry_id
                )
                break

            await asyncio.sleep(_RATE_LIMIT_SLEEP)

        # Process all fetched records (oldest first → newest appended last)
        # Skip any nMssn already in the archive (may have arrived via delta update)
        all_raw.reverse()
        skipped = 0
        for raw in all_raw:
            n = int(raw.get("nMssn", 0) or 0)
            if n and n in self._archived_nmssns:
                skipped += 1
                continue
            self._append(raw)

        self._initial_load_done = True
        await self.async_save(hass, entry_id)
        _LOGGER.info(
            "MissionArchive: initial load complete for %s — %d records stored "
            "(%d skipped, last_nMssn=%d, pages=%d)",
            entry_id, len(self._derived), skipped, self._last_nMssn, pages_fetched,
        )

    # ── Delta update (called after each mission completion) ────────────────

    async def async_delta_update(
        self,
        raw_record: dict[str, Any],
        hass: HomeAssistant,
        entry_id: str,
    ) -> bool:
        """Append a single new mission record received from the cloud coordinator.

        Returns True when the record was new and appended, False when skipped
        (duplicate or older than last seen nMssn).

        The coordinator already adds "classified_result" to raw_record — this
        method can rely on it being present.
        """
        n_mssn = _safe_int(raw_record.get("nMssn"))
        if n_mssn <= 0:
            return False  # invalid nMssn — skip entirely

        # v2.9.0 — DISCONTINUITY GUARD. Checked BEFORE the simple dedup-set
        # membership check below — under normal operation nMssn only ever
        # increases, so seeing one significantly lower than our high-water
        # mark is itself the signal that the robot's lifetime mission
        # counter has reset or discontinued for some reason (factory
        # reset, RMA replacement unit, etc. — the exact cause doesn't
        # matter here, and detecting/labelling the cause was judged not
        # worth a whole feature on its own). Critically, this must run
        # FIRST: the recycled nMssn value will almost always ALREADY be
        # in _archived_nmssns from before the discontinuity (that's the
        # whole problem) — checking membership first would immediately
        # return False and this guard would never run for the exact case
        # it exists to catch.
        #
        # v2.8.6 CONFIRMED BUG FIX, ROUND 1 (field report, Thonno): the
        # original check was `n_mssn < self._last_nMssn` alone — true on
        # every single cloud refresh whenever the upstream caller re-feeds
        # the last ~100 cloud history records (it does — this function is
        # NOT only called once per genuinely-new mission, despite the
        # docstring; observed in the field re-feeding the same window on
        # every refresh), and that window's oldest entry routinely sits
        # below the high-water mark simply because it's an older,
        # legitimately-never-archived gap-filler — not a reset at all.
        # That false trigger cleared _archived_nmssns (every refresh,
        # forever) while leaving _derived untouched, so every
        # already-archived record in the re-fed window got silently
        # RE-appended as a duplicate, unboundedly, on every single
        # refresh (confirmed in the field: 109 -> 209 -> 309 records
        # across two refreshes ~83 min apart, same robot).
        #
        # ROUND 2 (caught while writing the regression test for round 1):
        # requiring nMssn to ALSO already be in _archived_nmssns is not
        # enough either — an ordinary re-delivery of an already-archived
        # OLDER record (ANY record below the current high-water mark,
        # which is the common case once the archive holds more than one
        # mission) satisfies that exact same compound condition, since
        # its value is both lower than last_nMssn AND already archived.
        # That's not a reset, it's just a duplicate.
        #
        # nMssn alone — at any single value, in any combination — cannot
        # distinguish "counter reset" from "ordinary old duplicate",
        # because both present as "a value at or below the high-water
        # mark, possibly already seen before". The only available signal
        # that actually tells them apart is TIME: a genuine reset means
        # this report is for a mission that happened chronologically
        # AFTER the one that set our current high-water mark, despite
        # carrying a lower nMssn — an old duplicate's startTime is the
        # same as (or older than) what's already on record.
        start_ts = _safe_int(raw_record.get("startTime"))
        is_chronologically_newer = (
            start_ts > 0 and start_ts > self._last_nMssn_start_ts
        )
        if (
            n_mssn < self._last_nMssn
            and n_mssn in self._archived_nmssns
            and is_chronologically_newer
        ):
            _LOGGER.warning(
                "MissionArchive: nMssn discontinuity detected for %s "
                "(reported=%d, high-water mark=%d) — robot's lifetime "
                "mission counter appears to have reset; clearing dedup "
                "set so future missions are no longer silently blocked",
                entry_id, n_mssn, self._last_nMssn,
            )
            self._archived_nmssns.clear()
            self._last_nMssn = 0
            self._last_nMssn_start_ts = 0
        elif n_mssn in self._archived_nmssns:
            return False  # already archived (exact match, same epoch)

        self._append(raw_record)
        await self.async_save(hass, entry_id)
        _LOGGER.debug(
            "MissionArchive: delta update nMssn=%d for %s (total=%d)",
            n_mssn, entry_id, len(self._derived),
        )
        return True

    # ── Internal append ───────────────────────────────────────────────────

    def _append(self, raw: dict[str, Any]) -> None:
        """Parse and prepend a raw record to all three layers.

        New records are prepended (index 0) so the archive stays newest-first.
        Trims to MAX_RECORDS when the cap is exceeded.
        """
        derived = self._parse_derived(raw)
        timeline = self._parse_timeline(raw)
        n_mssn = derived.get("nMssn")

        # v2.9.0 (J) — increment BEFORE the FIFO trim below, so an evicted
        # record's area is never lost from the running lifetime total.
        _sqft = derived.get("sqft")
        if _sqft:
            self._cumulative_sqft += _sqft

        # Prepend to Layers 1 and 2 (newest first)
        self._derived.insert(0, derived)
        self._timeline.insert(0, timeline)

        # Track archived nMssn for O(1) dedup
        if n_mssn:
            self._archived_nmssns.add(int(n_mssn))
        if n_mssn and self._is_anomalous(derived):
            fin_events = (
                (raw.get("timeline") or {}).get("finEvents") or []
            )
            if fin_events:
                self._raw[int(n_mssn)] = fin_events

        # Update last-seen nMssn (and the start_ts that goes with it — see
        # async_delta_update's discontinuity guard for why these two must
        # always move together).
        if n_mssn and int(n_mssn) > self._last_nMssn:
            self._last_nMssn = int(n_mssn)
            self._last_nMssn_start_ts = _safe_int(raw.get("startTime"))

        # FIFO trim
        if len(self._derived) > MAX_RECORDS:
            removed = self._derived.pop()
            self._timeline.pop()
            old_n = removed.get("nMssn")
            if old_n:
                self._raw.pop(int(old_n), None)
                self._archived_nmssns.discard(int(old_n))

    # ── Parsing: Layer 1 (derived) ─────────────────────────────────────────

    def _parse_derived(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Extract 26 computed signals from a raw /missionhistory record."""
        timeline: dict = raw.get("timeline") or {}
        fin_events: list = timeline.get("finEvents") or []
        plan: dict = timeline.get("plan") or {}

        # ── Planned room order from timeline.plan.upcoming ─────────────────
        upcoming = plan.get("upcoming") or []
        planned_rids = [r for r in (_extract_rid(u) for u in upcoming) if r]

        # ── Parse all finEvents in a single pass ───────────────────────────
        traversal_rids: list[str] = []
        rooms_completed: dict[str, dict] = {}
        rooms_interrupted: list[str] = []
        recharge_count = 0
        evac_count = 0
        kidnap_count = 0
        reloc_count = 0
        disc_count = 0
        error_in_mission: list[int] = []
        unknown_types: list[str] = []
        _known_types = {
            "room", "traversal", "recharge", "evac",
            "kidnap", "reloc", "disc", "error", "missionstart",
        }

        for ev in fin_events:
            if not isinstance(ev, dict):
                continue
            ev_type = ev.get("type", "")

            if ev_type == "room":
                room = ev.get("room") or {}
                rid = str(room.get("rid") or "")
                status = room.get("status")
                if not rid:
                    continue
                if status in (0, 6):    # 0=complete, 6=complete after recovery
                    rooms_completed[rid] = {
                        "passes": _safe_int(room.get("passCount")),
                        "area": _safe_float(
                            room.get("totalArea") or room.get("area")
                        ),
                    }
                elif status == 5:       # 5=interrupted
                    if rid not in rooms_interrupted:
                        rooms_interrupted.append(rid)

            elif ev_type == "traversal":
                # Traversal events confirm robot crossed a room boundary.
                # Capture the room IDs involved for GS-SMART-UMF.
                t_data = ev.get("traversal") or {}
                for key in ("srcRid", "dstRid", "rid"):
                    rid = str(t_data.get(key) or "")
                    if rid and rid not in traversal_rids:
                        traversal_rids.append(rid)

            elif ev_type == "recharge":
                recharge_count += 1
            elif ev_type == "evac":
                evac_count += 1
            elif ev_type == "kidnap":
                kidnap_count += 1
            elif ev_type == "reloc":
                reloc_count += 1
            elif ev_type == "disc":
                disc_count += 1
            elif ev_type == "error":
                err_code = _safe_int((ev.get("error") or {}).get("code"))
                if err_code and err_code not in error_in_mission:
                    error_in_mission.append(err_code)
            elif ev_type and ev_type not in _known_types:
                if ev_type not in unknown_types:
                    unknown_types.append(ev_type)

        # ── WiFi signals from wlBars histogram ────────────────────────────
        wl_bars = raw.get("wlBars") if isinstance(raw.get("wlBars"), list) else None

        # ── Top-level fields ──────────────────────────────────────────────
        pause_id = int(raw.get("pauseId", 0) or 0)
        result = raw.get("classified_result") or _classify_result(raw)

        # pmap_id and pmapv_id from timeline.plan or top-level
        pmap_id = (
            plan.get("pmap_id")
            or raw.get("pmap_id")
            or None
        )
        pmapv_id = (
            plan.get("pmapv_id")
            or raw.get("pmapv_id")
            or None
        )

        return {
            # Identity
            "nMssn":              _safe_int(raw.get("nMssn")),
            "start_ts":           _ts_to_iso(raw.get("startTime")),
            "end_ts":             _ts_to_iso(raw.get("timestamp")),
            # Duration
            "duration_min":       _safe_int(raw.get("durationM") or raw.get("doneM")) or None,
            "run_min":            _safe_int(raw.get("runM")) or None,
            # Area / dirt
            "sqft":               _safe_float(raw.get("sqft")) or None,
            "dirt":               _safe_int(raw.get("dirt")) or None,
            # WiFi
            "wl_floor":           _wl_floor(wl_bars),
            "wl_stability":       _wl_stability(wl_bars),
            "wifi_channel":       _safe_int(raw.get("wifiChannel")) or None,
            # Result
            "result":             result,
            "pause_id":           pause_id,
            "initiator":          str(raw.get("initiator") or "none"),
            # Map references
            "pmap_id":            pmap_id,
            "pmapv_id":           pmapv_id,
            # From finEvents
            "traversal_rids":     traversal_rids,
            "planned_room_order": planned_rids,
            "rooms_completed":    rooms_completed,
            "rooms_interrupted":  rooms_interrupted,
            "recharge_count":     recharge_count,
            "evac_count":         evac_count,
            "kidnap_count":       kidnap_count,
            "reloc_count":        reloc_count,
            "disc_count":         disc_count,
            "error_in_mission":   error_in_mission,
            "unknown_event_types": unknown_types,
        }

    # ── Parsing: Layer 2 (compact timeline) ───────────────────────────────

    def _parse_timeline(self, raw: dict[str, Any]) -> list[list]:
        """Build compact event sequence from timeline.finEvents.

        Format: list of [event_type, data_dict].
        plan entry is prepended when upcoming rooms are present.
        """
        events: list[list] = []
        timeline: dict = raw.get("timeline") or {}
        plan: dict = timeline.get("plan") or {}
        fin_events: list = timeline.get("finEvents") or []

        # Prepend plan entry
        upcoming = plan.get("upcoming") or []
        if upcoming:
            rids = [r for r in (_extract_rid(u) for u in upcoming) if r]
            if rids:
                events.append([
                    "plan",
                    {
                        "rooms": rids,
                        "ordered": bool(plan.get("ordered", True)),
                    },
                ])

        for ev in fin_events:
            if not isinstance(ev, dict):
                continue
            ev_type = ev.get("type", "")

            if ev_type == "room":
                room = ev.get("room") or {}
                rid = str(room.get("rid") or "")
                status = room.get("status")
                if not rid:
                    continue
                if status in (0, 6):
                    events.append([
                        "room_done",
                        {
                            "rid": rid,
                            "passes": int(room.get("passCount", 0)),
                            "area": float(
                                room.get("totalArea") or room.get("area") or 0
                            ),
                        },
                    ])
                elif status == 1:
                    events.append(["room_enter", {"rid": rid}])
                elif status == 5:
                    events.append(["room_interrupted", {"rid": rid}])

            elif ev_type == "traversal":
                t_data = ev.get("traversal") or {}
                events.append([
                    "traversal",
                    {k: str(v) for k, v in t_data.items() if k in ("srcRid", "dstRid")},
                ])

            elif ev_type in ("recharge", "evac", "kidnap", "reloc", "disc"):
                events.append([ev_type, {}])

            elif ev_type == "error":
                err = ev.get("error") or {}
                code = int(err.get("code", 0) or 0)
                events.append(["error", {"code": code} if code else {}])

        return events

    # ── Anomaly gate: Layer 3 ─────────────────────────────────────────────

    @staticmethod
    def _is_anomalous(derived: dict[str, Any]) -> bool:
        """Return True for missions that warrant storing raw finEvents.

        Gate: result not clean-complete, kidnap, high reloc, error codes, or
        unexpected disconnections.  Targets ~30% of missions for Layer 3.
        """
        result = derived.get("result", "")
        if result not in ("completed", "cancelled_by_user"):
            return True
        if derived.get("kidnap_count", 0) > 0:
            return True
        if derived.get("reloc_count", 0) > 2:
            return True
        if derived.get("error_in_mission"):
            return True
        if derived.get("disc_count", 0) > 1:
            return True
        return False

    # ── Query API (consumed by sensors, coordinator, GS-SMART-UMF) ────────

    @property
    def record_count(self) -> int:
        """Number of derived records currently in the archive."""
        return len(self._derived)

    @property
    def cumulative_sqft(self) -> float:
        """Running lifetime total of area_sqft across every mission ever
        archived (v2.9.0 J) — survives FIFO eviction once MAX_RECORDS is
        exceeded, unlike summing all_derived_oldest_first() live. See the
        accumulator's definition in __init__ for the full rationale.
        """
        return self._cumulative_sqft

    @property
    def last_nMssn(self) -> int:
        """Highest mission number seen — used to skip already-archived records."""
        return self._last_nMssn

    @property
    def initial_load_done(self) -> bool:
        """True once the one-time paginated back-fill has completed."""
        return self._initial_load_done

    def latest_derived(self, n: int = 1) -> list[dict[str, Any]]:
        """Return the n most recent derived records (newest first)."""
        return self._derived[:n]

    def recent_derived(self, days: int = 30) -> list[dict[str, Any]]:
        """Return derived records from the last N days (newest first).

        Uses start_ts for filtering.  Records without start_ts are skipped.
        """
        cutoff = dt_util.utcnow() - timedelta(days=days)
        result: list[dict[str, Any]] = []
        for rec in self._derived:
            ts = rec.get("start_ts")
            if not ts:
                continue
            try:
                rec_dt = datetime.fromisoformat(ts)
                if rec_dt.tzinfo is None:
                    rec_dt = rec_dt.replace(tzinfo=UTC)
                if rec_dt >= cutoff:
                    result.append(rec)
            except (ValueError, TypeError):
                continue
        return result

    def traversal_missions(self) -> list[dict[str, Any]]:
        """Return derived records that have ≥1 traversal event.

        Used by GS-SMART-UMF to bootstrap UmfAligner from full history,
        replacing the 100-record window previously used by raw_records.
        """
        return [r for r in self._derived if r.get("traversal_rids")]

    def missions_by_room(self, rid: str, days: int | None = None) -> list[dict[str, Any]]:
        """Return derived records where the given room was completed.

        Args:
            rid:  Region ID string.
            days: Optional window; None = all history.

        Used by L5-ARC for per-room dirt index over full history.
        """
        candidates = self.recent_derived(days) if days else self._derived
        return [
            r for r in candidates
            if rid in (r.get("rooms_completed") or {})
        ]

    def raw_finEvents(self, n_mssn: int) -> list | None:
        """Return Layer 3 raw finEvents for a given mission number, or None."""
        return self._raw.get(n_mssn)

    def all_derived_oldest_first(self) -> list[dict[str, Any]]:
        """Return all derived records ordered oldest-first.

        Used by L5-ARC and L3-ARC seeding to replay history in chronological
        order so EMA converges correctly (most recent mission has highest weight).
        """
        return list(reversed(self._derived))

    def wifi_channel_series(self, n: int = 30) -> list[int]:
        """Return wifi_channel values for the last n missions (newest first).

        None values are omitted.  Used by BAT-ARCH wifi_last_channel /
        wifi_channel_stability sensors.
        """
        channels: list[int] = []
        for rec in self._derived[:n]:
            ch = rec.get("wifi_channel")
            if ch is not None:
                channels.append(ch)
        return channels

    def dirt_series(self, days: int = 30) -> list[dict[str, Any]]:
        """Return recent derived records that have sqft and dirt data.

        Used by L3-ARC anomaly baseline and BAT-ARCH dirt analytics.
        """
        return [
            r for r in self.recent_derived(days)
            if r.get("sqft") and r.get("dirt") is not None
        ]
