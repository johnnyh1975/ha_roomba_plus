"""DirtThresholdManager — demand-based cleaning trigger (F11, v2.4.0).

Evaluates post-mission dirt density against a 30-day rolling baseline.
When density exceeds `baseline × multiplier`, all presence and blocking
conditions are clear, and the minimum inter-clean gap has elapsed, sends
a start command and records the mission with initiator="demand".

Trigger chain (assembled by CR2 + F4b in v2.2):
  Mission end → cloud refresh → CR2 merge
    → DirtThresholdManager.async_evaluate()
      → dirt_density > p30d_baseline × multiplier
      → home_away_ok (PresenceManager all persons away, or scheduling disabled)
      → not BlockingManager.is_queued
      → min_gap_elapsed (MIN_GAP_HOURS since last demand trigger)
      → send start → MissionStore records initiator="demand"

Gate: requires cloud credentials (dirt density from cloud records).
      Repair Issue raised when enabled without credentials or < MIN_RECORDS.

Design constraints:
  - Stateless between HA restarts except for _last_trigger_time (hass.storage).
  - async_evaluate() is a pure fire-and-forget coroutine — never blocks the
    coordinator update path.
  - All decisions logged at DEBUG; trigger logged at INFO.
  - No direct sensor import — reads raw_records directly from coordinator.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .cloud_coordinator import IrobotCloudCoordinator
    from .models import RoombaConfigEntry

from .const import SQFT_TO_M2

_LOGGER = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

STORAGE_KEY_PREFIX = "roomba_plus_dirt_threshold"
STORAGE_VERSION    = 1

# Minimum cloud records before the baseline is considered reliable.
MIN_RECORDS = 5

# Minimum number of records used to compute the p30d baseline median.
# This is a rolling window over the most recent cloud API records (~30 missions).
BASELINE_WINDOW = 20

# Trigger multiplier: demand clean fires when density > baseline × TRIGGER_MULTIPLIER.
# 1.5 = 50% above baseline. Conservative default — users may lower via options.
TRIGGER_MULTIPLIER_DEFAULT = 1.5

# Minimum hours between consecutive demand-triggered cleans.
# Prevents repeated triggers on days when the floor is genuinely dirty.
MIN_GAP_HOURS = 6

# Config option key (written by F13 options flow)
CONF_DEMAND_CLEANING_ENABLED   = "demand_cleaning_enabled"
CONF_DEMAND_MULTIPLIER         = "demand_clean_multiplier"


# ── Helper ─────────────────────────────────────────────────────────────────────

def _compute_dirt_density(record: dict) -> float | None:
    """Compute dirt events per m² for a single cloud record.

    Returns None when the record lacks the required fields (e.g. 600-series).
    Uses the same formula as _raw_dirt_density() in sensor.py.
    """
    dirt = record.get("dirt")
    sqft = record.get("sqft")
    if dirt is None or not sqft:
        return None
    sqft_f = float(sqft)
    if sqft_f <= 0:
        return None
    m2 = sqft_f * SQFT_TO_M2
    return float(dirt) / m2


class DirtThresholdManager:
    """Evaluates post-mission dirt density and triggers demand cleans.

    Lifecycle:
      - Instantiate in async_setup_entry (SMART + cloud + enabled).
      - Call async_load() immediately after.
      - Store in entry.runtime_data.dirt_threshold_manager.
      - Call async_evaluate() from the post-mission callback chain.
      - Call async_save() when _last_trigger_time changes.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: "RoombaConfigEntry",
    ) -> None:
        self._hass = hass
        self._entry = config_entry
        self._last_trigger_time: datetime | None = None
        # P2: Store is stateless — construct once and reuse across load/save calls
        self._store: "Store | None" = None
        # L1: per-weekday baseline; populated after ≥4 cloud records per weekday
        self._baseline_by_weekday: dict[int, float] = {}

    def _get_store(self, entry_id: str) -> "Store":
        """Return the cached Store, creating it on first call."""
        if self._store is None:
            from homeassistant.helpers.storage import Store
            self._store = Store(
                self._hass,
                STORAGE_VERSION,
                f"{STORAGE_KEY_PREFIX}_{entry_id}",
                private=True,
            )
        return self._store

    # ── Persistence ────────────────────────────────────────────────────────────

    async def async_load(self, entry_id: str) -> None:
        """Load persisted _last_trigger_time from hass.storage."""
        store = self._get_store(entry_id)
        data = await store.async_load()
        if data and isinstance(data, dict):
            ts = data.get("last_trigger_time")
            if ts:
                try:
                    self._last_trigger_time = datetime.fromisoformat(ts)
                except ValueError:
                    _LOGGER.warning(
                        "DirtThresholdManager: invalid stored timestamp %r — resetting", ts
                    )
            # L1: restore weekday baselines
            stored_wdb = data.get("baseline_by_weekday", {})
            if isinstance(stored_wdb, dict):
                self._baseline_by_weekday = {
                    int(k): float(v) for k, v in stored_wdb.items()
                }
        _LOGGER.debug(
            "DirtThresholdManager: loaded, last_trigger=%s weekday_baselines=%d",
            self._last_trigger_time, len(self._baseline_by_weekday),
        )

    async def async_save(self, entry_id: str) -> None:
        """Persist _last_trigger_time to hass.storage."""
        store = self._get_store(entry_id)
        await store.async_save({
            "version": STORAGE_VERSION,
            "last_trigger_time": (
                self._last_trigger_time.isoformat() if self._last_trigger_time else None
            ),
            # L1: persist weekday baselines so they survive HA restarts
            "baseline_by_weekday": self._baseline_by_weekday,
        })

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def last_trigger_time(self) -> datetime | None:
        """Return the UTC datetime of the last demand-triggered clean, or None."""
        return self._last_trigger_time

    @property
    def enabled(self) -> bool:
        """True when demand cleaning is configured and enabled."""
        return bool(self._entry.options.get(CONF_DEMAND_CLEANING_ENABLED, False))

    def compute_baseline(self, records: list[dict]) -> float | None:
        """Return the median dirt density over the most recent BASELINE_WINDOW records.

        Returns None when fewer than MIN_RECORDS usable records exist.
        """
        densities = [
            d for r in records[:BASELINE_WINDOW]
            if (d := _compute_dirt_density(r)) is not None
        ]
        if len(densities) < MIN_RECORDS:
            _LOGGER.debug(
                "DirtThresholdManager: baseline unavailable — only %d usable records "
                "(need %d)", len(densities), MIN_RECORDS
            )
            return None
        return statistics.median(densities)

    def _update_weekday_baseline(
        self, records: list[dict], weekday: int
    ) -> None:
        """L1 — Rebuild the baseline for a specific weekday from 12 weeks of records.

        Groups records by weekday using their startTime, computes the median
        dirt density for this weekday, and stores it in _baseline_by_weekday.
        Requires ≥ 4 records for the weekday to be reliable; falls back to the
        flat median (compute_baseline) for weekdays with insufficient history.

        Uses HA's configured timezone (dt_util.as_local) so weekday assignment
        matches the user's local calendar, not the server OS timezone.
        """
        cutoff_ts = (datetime.now(UTC) - timedelta(weeks=12)).timestamp()
        day_densities: list[float] = []
        for r in records:
            ts = r.get("startTime") or r.get("timestamp")
            if not ts or float(ts) < cutoff_ts:
                continue
            try:
                # Bug 1 fix: use HA timezone, not server local timezone
                local_dt = dt_util.as_local(
                    datetime.fromtimestamp(int(ts), tz=UTC)
                )
                wd = local_dt.weekday()
            except (ValueError, OSError):
                continue
            if wd != weekday:
                continue
            d = _compute_dirt_density(r)
            if d is not None:
                day_densities.append(d)
        if len(day_densities) >= 4:
            self._baseline_by_weekday[weekday] = statistics.median(day_densities)
        elif weekday in self._baseline_by_weekday:
            # Keep the existing baseline — insufficient new data to update
            pass

    def should_trigger(
        self,
        records: list[dict],
        multiplier: float | None = None,
    ) -> tuple[bool, str]:
        """Evaluate whether a demand clean should be triggered.

        Returns (trigger: bool, reason: str) — reason is logged at DEBUG.
        Does NOT check presence or blocking — caller handles those gates.

        L1: compares against weekday-specific baseline when ≥4 records exist
        for today's weekday; falls back to flat 30-day median otherwise.

        Args:
            records: raw_records from cloud coordinator (most-recent-first).
            multiplier: override trigger multiplier (default TRIGGER_MULTIPLIER_DEFAULT).
        """
        if not records:
            return False, "no cloud records"

        # L1: update weekday baseline for today, then select appropriate baseline
        today_wd = dt_util.now().weekday()
        self._update_weekday_baseline(records, today_wd)

        weekday_baseline = self._baseline_by_weekday.get(today_wd)
        flat_baseline = self.compute_baseline(records)

        baseline = weekday_baseline if weekday_baseline is not None else flat_baseline
        if baseline is None:
            return False, f"insufficient records for baseline (need {MIN_RECORDS})"
        if baseline <= 0:
            return False, "baseline is zero — skipping"

        # Most recent mission density
        current = _compute_dirt_density(records[0]) if records else None
        if current is None:
            return False, "most recent record missing dirt/sqft fields"

        mult = multiplier if multiplier is not None else TRIGGER_MULTIPLIER_DEFAULT
        threshold = baseline * mult
        if current <= threshold:
            return False, (
                f"density {current:.3f} ≤ threshold {threshold:.3f} "
                f"(baseline {baseline:.3f} × {mult}, "
                f"{'weekday' if weekday_baseline is not None else 'flat'})"
            )

        # Min gap check
        if self._last_trigger_time is not None:
            elapsed = datetime.now(UTC) - self._last_trigger_time
            if elapsed < timedelta(hours=MIN_GAP_HOURS):
                return False, (
                    f"min gap not elapsed — last trigger {elapsed.total_seconds()/3600:.1f}h ago "
                    f"(need {MIN_GAP_HOURS}h)"
                )

        return True, (
            f"density {current:.3f} > threshold {threshold:.3f} "
            f"(baseline {baseline:.3f} × {mult}, "
            f"{'weekday' if weekday_baseline is not None else 'flat'}), gap ok"
        )

    def gate_blocked(self) -> tuple[bool, str]:
        """Return (is_blocked, reason) for the first active gate.

        ALG3 (v2.6.0) — single source of truth for the demand-clean gate
        logic used by both async_evaluate (execution path) and
        RoombaDemandCleanBlocked sensor (binary display path). Previously the
        sensor duplicated all gate checks inline.

        Returns (True, reason_str) when any gate is blocking.
        Returns (False, "") when all gates are clear.
        The caller is responsible for also checking should_trigger().
        """
        if not self.enabled:
            return True, "demand_cleaning_disabled"

        data = self._entry.runtime_data

        # Gate 2: presence (all away) — only when PresenceManager is active
        pm = getattr(data, "presence_manager", None)
        if pm is not None:
            from .const import CONF_PRESENCE_ENTITIES
            person_ids: list[str] = self._entry.options.get(CONF_PRESENCE_ENTITIES, [])
            if person_ids:
                _UNUSABLE = frozenset({"unavailable", "unknown"})
                all_away = all(
                    (st := self._hass.states.get(eid)) is not None
                    and st.state not in _UNUSABLE   # unusable → treat as "might be home"
                    and st.state not in ("home", "Home")
                    for eid in person_ids
                )
                if not all_away:
                    return True, "not_all_away"

        # Gate 3: BlockingManager — no active block queued
        bm = getattr(data, "blocking_manager", None)
        if bm is not None and bm.is_queued:
            return True, "blocking_sensor_queued"

        # Gate 4: robot must be docked/idle
        state = data.roomba_reported_state()
        cycle = state.get("cleanMissionStatus", {}).get("cycle", "none")
        if cycle != "none":
            return True, f"robot_busy_{cycle}"

        return False, ""

    async def async_evaluate(
        self,
        coordinator: "IrobotCloudCoordinator",
        entry_id: str,
    ) -> None:
        """Main evaluation entry point — called post-mission.

        Checks all gates in order and sends a start command when all pass.
        Never raises — all errors are swallowed to protect the callback chain.
        """
        try:
            await self._async_evaluate_inner(coordinator, entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("DirtThresholdManager: unexpected error in async_evaluate")

    async def _async_evaluate_inner(
        self,
        coordinator: "IrobotCloudCoordinator",
        entry_id: str,
    ) -> None:
        """Inner evaluation — may raise; caller wraps in try/except."""
        if not self.enabled:
            return

        data = self._entry.runtime_data
        records = coordinator.raw_records

        # Gate 1: dirt density threshold
        multiplier = self._entry.options.get(CONF_DEMAND_MULTIPLIER, TRIGGER_MULTIPLIER_DEFAULT)
        trigger, reason = self.should_trigger(records, multiplier=float(multiplier))
        _LOGGER.debug("DirtThresholdManager: should_trigger=%s — %s", trigger, reason)
        if not trigger:
            return

        # Gate 2–4: use gate_blocked() — single source of truth (ALG3)
        blocked, block_reason = self.gate_blocked()
        if blocked:
            _LOGGER.debug("DirtThresholdManager: blocked — %s", block_reason)
            return

        # All gates passed — send start command
        _LOGGER.info(
            "DirtThresholdManager: triggering demand clean for %s (%s)",
            self._entry.entry_id, reason,
        )
        self._last_trigger_time = datetime.now(UTC)
        await self.async_save(entry_id)

        # MS1 (v2.6.0): stamp demand_triggered_ts so callbacks.py can mark
        # the resulting mission record with initiator="demand" within 30 s.
        import time as _time
        data.demand_triggered_ts = _time.monotonic()

        roomba = data.roomba
        await self._hass.async_add_executor_job(roomba.send_command, "start")

        _LOGGER.info("DirtThresholdManager: demand clean sent for %s", entry_id)
