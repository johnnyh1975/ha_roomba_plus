"""Room- and mission-related sensors for the Roomba+ sensor platform.

SENSOR-SPLIT (v3.4.0): extracted from the former sensor.py monolith.
Covers mission progress, zone/room summaries, dirt correlation, edge
coverage, per-room cleaning history/areas/accessibility, and
relocalisation rate — plus the room-order/time-estimate/smart-tier
helpers used both here and (via the sensor.py facade) by
callbacks.py, device_tracker.py, and services.py. No behaviour
change vs. v3.3.1.
"""
from __future__ import annotations

from typing import Any
import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import StateType

from homeassistant.util import dt as dt_util

from .const import CONF_ROOM_SCHEDULE
from .entity import IRobotEntity
from .models import RoombaConfigEntry

_LOGGER = logging.getLogger("custom_components.roomba_plus.sensor")


class RoombaEdgeCoverageSensor(IRobotEntity, SensorEntity):
    """Ratio of edge cells to total cells in GridStore.

    F12d (v2.4.0) — a low ratio with high total coverage indicates the robot
    is over-cleaning the centre and under-covering room edges/walls.

    State: float 0.0–1.0 (edge cells / total cells), or None when < 10 cells.
    entity_category: DIAGNOSTIC.
    Unit: None (dimensionless ratio).
    """

    entity_description = SensorEntityDescription(
        key="recent_edge_coverage_ratio",
        name="Recent edge coverage ratio",
        translation_key="recent_edge_coverage_ratio",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = None
    _attr_suggested_display_precision = 3

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_recent_edge_coverage_ratio"

    @property
    def native_value(self) -> float | None:
        """Return edge_coverage_ratio from GridStore, or None when insufficient data."""
        gs = self._config_entry.runtime_data.grid_store
        if gs is None:
            return None
        return gs.edge_coverage_ratio()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        gs = self._config_entry.runtime_data.grid_store
        if gs is None:
            return {}
        attrs: dict[str, Any] = {
            "total_cells": gs.cell_count,
            "edge_depth_mm": 300,
        }
        # L6 (v2.6.0): ratio vs personal baseline (1.0 = on-par; <1 = below norm)
        rps = getattr(self._config_entry.runtime_data, "robot_profile_store", None)
        if rps is not None and rps.coverage_baseline_ready:
            current = self.native_value
            if isinstance(current, float) and rps.coverage_baseline:
                attrs["coverage_vs_baseline"] = round(
                    current / rps.coverage_baseline, 3
                )
        return attrs

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "pose" in new_state


def _get_planned_room_order(data: Any) -> list[str]:
    """Resolve planned_room_order from lastCommand.regions in MQTT state.

    MP1 helper — reads the live MQTT state so planned order is available
    immediately at mission start without waiting for a cloud refresh.
    Returns an empty list when no room-select command was issued (whole-home).

    v2.6.3 — uses MissionStore.extract_rid() to handle all confirmed region
    key formats: {"region_id": ...} (Roomba+ app), {"rid": ...} (iRobot app /
    lewis 22.52.10+), and plain string (some firmware variants).

    v2.7.5 — falls back to mts.planned_rooms (set at mission start by
    set_mission_plan) when lastCommand.regions is temporarily empty or when
    cc.regions is unavailable mid-mission:
      * Lewis firmware can send cleanMissionStatus with a transitional
        lastCommand that has no regions field at inter-room boundaries.
      * The F4b cloud refresh triggered by a transient end-phase can leave
        active_pmap_id returning None, making cc.regions return [].
    Either path previously caused all tracker values to flip to unknown.
    """
    cc = getattr(data, "cloud_coordinator", None)
    if cc is None:
        return []
    reported = data.roomba.master_state.get("state", {}).get("reported", {})
    last_cmd = reported.get("lastCommand", {})

    from .mission_store import MissionStore as _MS
    region_ids = [
        _MS.extract_rid(r)
        for r in (last_cmd.get("regions") or [])
        if _MS.extract_rid(r)
    ]
    mts = getattr(data, "mission_timer_store", None)
    if not region_ids:
        # lastCommand.regions temporarily empty — fall back to MTS snapshot.
        _LOGGER.debug(
            "_get_planned_room_order: lastCommand.regions empty, "
            "falling back to mts.planned_rooms=%s",
            getattr(mts, "planned_rooms", None) if mts else None,
        )
        if mts is not None and mts.planned_rooms:
            return list(mts.planned_rooms)
        return []

    id_to_name = {r["id"]: r["name"] for r in cc.regions if r.get("id")}
    result = [id_to_name[rid] for rid in region_ids if rid in id_to_name]

    # v2.9.0 — region_ids had entries, but not all of them resolved via
    # cc.regions (id_to_name). The pre-v2.9.0 fallback only triggered when
    # `result` was COMPLETELY empty, missing the case where it's a PARTIAL
    # match (e.g. region_ids=["21","25"] but cc.regions is mid-refresh and
    # only resolves "21" — result=["Corridoio"], non-empty, no fallback
    # triggered). A genuine 2-room mission's planned_order would then
    # silently shrink to 1 room mid-mission, corrupting every
    # elapsed/estimate calculation derived from it (mission_progress,
    # current_room/next_room, estimated_remaining_min) — a strong candidate
    # for Thonno's "progress reset" report, since phase stayed "run" the
    # whole time in his capture (ruling out every phase-based mechanism
    # already investigated). Logged at debug so the next capture confirms
    # or refutes this directly instead of guessing again.
    if len(result) != len(region_ids):
        _LOGGER.debug(
            "_get_planned_room_order: PARTIAL resolution — "
            "region_ids=%s resolved=%s (cc.regions stale/mid-refresh?). "
            "mts.planned_rooms=%s",
            region_ids, result,
            getattr(mts, "planned_rooms", None) if mts else None,
        )
        if mts is not None and mts.planned_rooms:
            return list(mts.planned_rooms)
    return result


def _compute_room_time_estimates(
    config_entry: Any, planned_order: list[str]
) -> list[int | None]:
    """Return per-room time estimates (seconds) in planned order.

    Reads from cloud_coordinator.regions[*].time_estimates (TE1). Returns
    None for any room where confidence < GOOD_CONFIDENCE or pass mode is
    Auto (no estimate available at runtime for Auto).

    Module-level (not class-bound) so it can be called from callbacks.py at
    mission start to wire MissionTimerStore.room_estimates_sec — see v2.8.0
    AUTO-ADVANCE-ROOM. Originally a method on RoombaMissionProgress only.
    """
    cc = config_entry.runtime_data.cloud_coordinator
    if cc is None:
        return [None] * len(planned_order)

    # v2.7.5 (TP-EST-FIX): per-room params in lastCommand.regions take
    # priority over cleanMissionStatus global fields. cleanMissionStatus
    # reflects the robot's global default, which stays at the device-level
    # setting even when a room-clean mission is started with explicit per-
    # region twoPass/noAutoPasses params. Reading from the wrong source
    # caused two-pass missions to be estimated at one-pass durations.
    reported = config_entry.runtime_data.roomba_reported_state()
    last_cmd = reported.get("lastCommand") or {}
    last_regions = [
        r for r in (last_cmd.get("regions") or [])
        if isinstance(r, dict) and r.get("params")
    ]
    if last_regions:
        has_no_auto = any(r["params"].get("noAutoPasses") for r in last_regions)
        has_two_pass = any(r["params"].get("twoPass") for r in last_regions)
        if not has_no_auto:
            pass_key = None          # Auto — no reliable per-room estimate
        elif has_two_pass:
            pass_key = "two_pass_sec"
        else:
            pass_key = "one_pass_sec"
    else:
        # Fallback: read from cleanMissionStatus global fields
        _cms = reported.get("cleanMissionStatus") or {}
        noap = _cms.get("noAutoPasses", True)
        two_pass = _cms.get("twoPass", False)
        if not noap:
            pass_key = None
        elif two_pass:
            pass_key = "two_pass_sec"
        else:
            pass_key = "one_pass_sec"

    # Build name→estimates map from coordinator
    region_map: dict[str, dict] = {}
    for region in cc.regions:
        name = region.get("name", "")
        if name:
            region_map[name.lower()] = region.get("time_estimates") or {}

    result: list[int | None] = []
    for room_name in planned_order:
        # Bug-hunt: room_name can theoretically be None/empty if a future
        # caller builds planned_order differently than callbacks.py's
        # current name-resolution chain (which always falls back to a
        # string). (room_name or "") avoids an AttributeError crash on
        # .lower() rather than relying on that invariant holding forever.
        est = region_map.get((room_name or "").lower(), {})
        if pass_key is None:
            result.append(None)
        else:
            result.append(est.get(pass_key))
    return result


# ── MP1 — Mission Progress sensor ─────────────────────────────────────────────

def _resolve_smart_tier_room_state(config_entry: Any) -> dict[str, Any]:
    """Resolve current_room/next_room/elapsed/estimated_remaining_min for a
    SMART-tier robot's active mission.

    v2.9.0 — EXTRACTED from RoombaMissionProgress.extra_state_attributes
    (was inline there since v2.7.2/MP-ELAPSED-FIX) into a shared,
    module-level function so RoombaDeviceTracker can use the EXACT same
    room resolution — current_room shown by the device tracker and by
    mission_progress must always agree; two separately-maintained copies
    of this logic would risk drifting apart over time as either one gets
    tweaked independently.

    Prefers the estimate-based room when the calculation succeeds (all
    per-room estimates available and elapsed within range); falls back to
    MissionTimerStore's own current_room/next_room when no per-room
    estimates exist (e.g. Auto pass mode — see the v2.9.0 AUTO-ADVANCE-ROOM
    Auto-mode fallback fix for why that's now far less restrictive than
    it used to be).
    """
    data = config_entry.runtime_data
    state = data.roomba_reported_state()
    phase = (state.get("cleanMissionStatus") or {}).get("phase", "")
    mts = data.mission_timer_store
    if mts is None or mts.mission_id is None:
        return {}

    planned_order: list[str] = _get_planned_room_order(data)
    # v2.9.0 — elapsed is now (mission_duration_min - recharge_min) * 60,
    # i.e. wall-clock time since mission start MINUS robot-confirmed
    # recharge minutes (hmMidMsn/rechrgM, F4e) — no time-based gap clamp.
    # Falls back to the old live-delta clamp-based calculation only for a
    # mission that was already in progress when this code shipped (its
    # mission_started_wall_ts is 0 since that field didn't exist yet) —
    # this fallback naturally stops mattering once that one mission ends.
    effective_min = mts.effective_elapsed_min
    elapsed = (
        effective_min * 60 if effective_min is not None
        else RoombaMissionProgress._elapsed_sec(mts, phase)
    )
    estimates = (
        _compute_room_time_estimates(config_entry, planned_order)
        if planned_order else []
    )

    # Determine current and next room from elapsed
    current_room: str | None = None
    next_room: str | None = None
    estimated_remaining_min: int | None = None

    if planned_order and estimates and all(e is not None for e in estimates):
        cumulative = 0
        for i, est in enumerate(estimates):
            assert est is not None
            if elapsed < cumulative + est:
                current_room = planned_order[i]
                next_room = planned_order[i + 1] if i + 1 < len(planned_order) else None
                remaining_sec = (cumulative + est - elapsed) + sum(
                    estimates[j]  # type: ignore[arg-type]
                    for j in range(i + 1, len(estimates))
                )
                estimated_remaining_min = max(0, round(remaining_sec / 60))
                break
            cumulative += est
        else:
            # All rooms elapsed — in final room
            current_room = planned_order[-1]

    if estimated_remaining_min is None:
        # v2.9.0 — fallback for whenever the per-room estimate calculation
        # above didn't produce a value: no planned_order at all, OR a
        # planned_order with one or more None per-room estimates (e.g. Auto
        # pass mode, where TE1 cloud data has no per-room times at all —
        # confirmed via Thonno's field report). Mirrors native_value()'s
        # same fallback rather than leaving estimated_remaining_min (and
        # therefore the whole "Unknown" percentage) stuck for the entire
        # mission whenever per-room estimates aren't available.
        rps = getattr(data, "robot_profile_store", None)
        mean_sec = (
            round((rps.mission_duration_mean or 0) * 60)
            if rps is not None else 0
        )
        if mean_sec > 0:
            estimated_remaining_min = max(0, round((mean_sec - elapsed) / 60))

    return {
        # Prefer estimate-based room when the calculation succeeded (all
        # estimates available and elapsed > 0). MTS value is the fallback
        # for when no per-room estimates exist.
        "current_room": current_room if current_room is not None else mts.current_room,
        "next_room":    next_room    if current_room is not None else mts.next_room,
        "elapsed_run_min": round(elapsed / 60, 1),
        "estimated_remaining_min": estimated_remaining_min,
        "room_sequence": planned_order,
        # v2.9.0 — Gesamtdauer (always-correct wall-clock) and Charging-Zeit
        # (robot-confirmed via F4e), shown alongside elapsed_run_min so the
        # difference between "total time" and "effective time" is visible
        # rather than silently disappearing.
        "mission_duration_min": mts.mission_duration_min,
        "recharge_min": round(mts.recharge_min, 1),
    }


class RoombaMissionProgress(IRobotEntity, SensorEntity):
    """Estimated mission completion percentage (0–100).

    MP1 (v2.6.0) — uses per-room time estimates from TE1 and elapsed run-only
    seconds from MissionTimerStore to show real-time mission progress without
    requiring manual calibration.

    Available only for SMART robots with cloud credentials and a loaded
    MissionTimerStore. Shows Unknown when no mission is active.
    """

    entity_description = SensorEntityDescription(
        key="mission_progress",
        name="Mission progress",
        translation_key="mission_progress",
    )

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = None  # main entity — visible on device page

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_mission_progress"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _room_estimates(self, planned_order: list[str]) -> list[int | None]:
        """Return per-room time estimates (seconds) in planned order.

        Delegates to the module-level _compute_room_time_estimates (extracted
        in v2.8.0 so callbacks.py can reuse this logic for AUTO-ADVANCE-ROOM).
        """
        return _compute_room_time_estimates(self._config_entry, planned_order)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _elapsed_sec(mts: Any, phase: str) -> float:
        """Return elapsed run seconds including current in-progress live delta.

        v2.7.2 (MP-ELAPSED-FIX): previously only native_value added the live
        delta; extra_state_attributes used mts.run_sec only, causing
        elapsed_run_min to freeze and current_room/next_room to lag on lewis
        firmware that sends cleanMissionStatus only on state changes.
        """
        import time as _time_mod
        elapsed = mts.run_sec
        if phase == "run" and mts.last_phase_ts > 0:
            live_delta = int(_time_mod.monotonic() - mts.last_phase_ts)
            if 0 < live_delta < 7200:   # cap at 2 h; restart/long-pause guard
                elapsed += live_delta
        return elapsed

    # ── Sensor state ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register 30 s periodic refresh for smooth progress updates.

        Lewis firmware sends cleanMissionStatus only on state changes, not
        continuously. Gaps between messages can exceed the 120 s accumulation
        clamp in MissionTimerStore, causing run_sec to stall.  The tick drives
        schedule_update_ha_state() from the event loop so native_value() can
        add the live delta directly, giving smooth updates regardless of MQTT
        message frequency.
        """
        await super().async_added_to_hass()
        import datetime as _dt
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                lambda _: self.schedule_update_ha_state(),
                _dt.timedelta(seconds=30),
            )
        )

    @property
    def native_value(self) -> StateType:
        """Return completion % (0–100) or None when inactive."""
        data = self._config_entry.runtime_data
        state = data.roomba_reported_state()
        phase = (state.get("cleanMissionStatus") or {}).get("phase", "")
        mts = data.mission_timer_store

        # Not in a cleaning phase → not active
        if phase not in ("run", "hmMidMsn", "evac"):
            return None
        if mts is None or mts.mission_id is None:
            return None

        # v2.9.0 — elapsed is now wall-clock mission duration minus
        # robot-confirmed recharge_min (F4e), not the old gap-clamped
        # live-delta. Same fallback as _resolve_smart_tier_room_state() for
        # a mission already in progress when this code shipped.
        effective_min = mts.effective_elapsed_min
        elapsed = (
            effective_min * 60 if effective_min is not None
            else self._elapsed_sec(mts, phase)
        )

        planned_order: list[str] = _get_planned_room_order(data)

        # v2.9.0 — comprehensive diagnostic for Thonno's "progress reset"
        # report. Every phase-based mechanism already investigated has been
        # ruled out (phase stays "run" throughout his capture); this logs
        # every input to the progress calculation on every poll so the next
        # capture shows directly whether planned_order/estimates change
        # shape mid-mission while phase never leaves "run".
        _LOGGER.debug(
            "mission_progress: phase=%s elapsed=%.1fs mission_id=%s "
            "planned_order=%s",
            phase, elapsed, mts.mission_id, planned_order,
        )

        if not planned_order:
            # No room sequence — use elapsed vs. rolling mean as fallback
            rps = getattr(data, "robot_profile_store", None)
            mean_sec = (
                round((rps.mission_duration_mean or 0) * 60)
                if rps is not None else 0
            )
            if mean_sec > 0:
                return min(99, round(elapsed / mean_sec * 100))
            return None

        estimates = self._room_estimates(planned_order)
        _LOGGER.debug(
            "mission_progress: planned_order=%s estimates=%s",
            planned_order, estimates,
        )
        if any(e is None for e in estimates):
            # At least one room has no estimate — use count-based progress
            total_rooms = len(planned_order)
            # Estimate current room from elapsed vs. mean-per-room
            total_known = sum(e for e in estimates if e is not None)
            known_count = len([e for e in estimates if e is not None])
            avg_sec = total_known / max(known_count, 1)
            if avg_sec > 0:
                completed_rooms = min(total_rooms - 1, int(elapsed / avg_sec))
                _LOGGER.debug(
                    "mission_progress: count-based branch total_rooms=%d "
                    "avg_sec=%.1f completed_rooms=%d -> %d%%",
                    total_rooms, avg_sec, completed_rooms,
                    min(99, round(completed_rooms / total_rooms * 100)),
                )
                return min(99, round(completed_rooms / total_rooms * 100))
            # v2.9.0 — known_count==0 here (ALL per-room estimates are None,
            # e.g. Auto pass mode — TE1 cloud data has no per-room times for
            # that mode at all, confirmed via Thonno's field report). Falls
            # through to the SAME mission_duration_mean rolling-average
            # fallback as the "no room sequence" branch above, instead of
            # returning None — previously this meant percentage/remaining
            # time stayed "Unknown" for the entire mission whenever Auto
            # mode was used, not just transiently.
            rps = getattr(data, "robot_profile_store", None)
            mean_sec = (
                round((rps.mission_duration_mean or 0) * 60)
                if rps is not None else 0
            )
            if mean_sec > 0:
                return min(99, round(elapsed / mean_sec * 100))
            return None

        total_sec = sum(estimates)  # type: ignore[arg-type]
        if total_sec == 0:
            return None
        return min(99, round(elapsed / total_sec * 100))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _resolve_smart_tier_room_state(self._config_entry)

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "cleanMissionStatus" in new_state




# ── IA74-LP — Map Learning Percentage sensor ───────────────────────────────────

class RoombaLearningPercentageSensor(IRobotEntity, SensorEntity):
    """Map learning completeness score from the iRobot cloud (0–100 %).

    IA74-LP (v2.6.0) — SMART robots only. The robot's own assessment of how
    well it knows its environment. Low values indicate the robot has not fully
    explored the home; a stable map requires values near 100.
    """

    entity_description = SensorEntityDescription(
        key="map_learning",
        name="Map learning",
        translation_key="map_learning",
    )

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_map_learning"

    @property
    def native_value(self) -> StateType:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return None
        return cc.learning_percentage

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # Updated by cloud coordinator listener only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))


# ── IA74-ZONE — Zone Summary sensor ───────────────────────────────────────────

class RoombaZoneSummarySensor(IRobotEntity, SensorEntity):
    """Count of active clean zones on the SMART map.

    IA74-ZONE (v2.6.0) — surfaces the three zone categories as a single sensor
    (state = clean zone count) with keepout and observed counts as attributes.
    Provides a quick map health overview without requiring the user to open the
    iRobot app.

    SMART + cloud only. State = number of clean zones (int).
    """

    entity_description = SensorEntityDescription(
        key="zone_summary",
        name="Zone summary",
        translation_key="zone_summary",
    )

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_zone_summary"

    @property
    def native_value(self) -> StateType:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return None
        return cc.zone_counts.get("clean")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return {}
        counts = cc.zone_counts
        return {
            "clean_zones":    counts.get("clean", 0),
            "keepout_zones":  counts.get("keepout", 0),
            "observed_zones": counts.get("observed", 0),
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))


class RoombaRoomsOverdueSensor(IRobotEntity, SensorEntity):
    """v3.3.0 ROOM-SCHED — which rooms are overdue for cleaning.

    State = number of overdue rooms (0 = everything in rhythm).
    One dict sensor, no per-room entities (PRIMARY-SLIM).

    Merge semantics live in MissionStore.rooms_overdue_merged() — the
    single shared rule with the clean_overdue_rooms service:
    configured frequency (options flow) beats the self-calibrated
    learned interval (COVERAGE-FREQ); insufficient_data never flags.

    Self-calibration extras (DIRT-VEL):
    - suggested_interval_days: target_density / velocity per room —
      a recommendation only, never part of the overdue rule.
    - daily_suggested: rooms whose suggested interval is < 1.5 days
      and that are not already configured as daily.

    SMART + cloud only: the room data source (timeline.finEvents room
    events) only exists on cloud-enriched SMART records.
    """

    entity_description = SensorEntityDescription(
        key="rooms_overdue",
        name="Rooms overdue",
        translation_key="rooms_overdue",
    )

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_rooms_overdue"

    def _merged(self) -> dict[str, dict[str, Any]]:
        data = self._config_entry.runtime_data
        ms = data.mission_store
        if ms is None:
            return {}
        config = self._config_entry.options.get(CONF_ROOM_SCHEDULE) or {}
        region_map, umf_regions = _region_maps_for(data)
        return ms.rooms_overdue_merged(
            config, dt_util.now().isoformat(),
            region_map=region_map, umf_regions=umf_regions,
        )

    @property
    def native_value(self) -> StateType:
        return sum(
            1 for info in self._merged().values()
            if info["status"] == "overdue"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        merged = self._merged()
        data = self._config_entry.runtime_data
        config = self._config_entry.options.get(CONF_ROOM_SCHEDULE) or {}
        attrs: dict[str, Any] = {
            "rooms": merged,
            "overdue_rooms": sorted(
                (n for n, i in merged.items() if i["status"] == "overdue"),
                key=lambda n: merged[n]["overdue_factor"] or 0,
                reverse=True,
            ),
        }
        rps = getattr(data, "robot_profile_store", None)
        if rps is not None:
            suggested_by_rid = rps.suggested_cleaning_interval_days()
            if suggested_by_rid:
                region_map, umf_regions = _region_maps_for(data)
                name_of = region_map or (umf_regions or {})
                suggested = {
                    name_of.get(rid, rid): days
                    for rid, days in suggested_by_rid.items()
                }
                attrs["suggested_interval_days"] = suggested
                attrs["daily_suggested"] = sorted(
                    room for room, days in suggested.items()
                    if days < 1.5 and config.get(room) != "daily"
                )
        return attrs

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # room data changes via cloud enrichment only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))


class RoombaDirtCorrelationSensor(IRobotEntity, SensorEntity):
    """v3.3.0 CROSS-CORR — Pearson correlation between mission dirt and
    the configured external HA sensors (opt-in, fully local).

    State: r of the strongest passing correlation — only when |r| > 0.3
    AND n >= 30 (spec gates); None otherwise. Attributes expose every
    configured entity with its r and sample count so users see progress
    toward the 30-sample threshold.
    Registered only when correlation entities are configured (opt-in)
    and cloud is available (the dirt field is cloud-enriched).
    """

    entity_description = SensorEntityDescription(
        key="dirt_weather_correlation",
        name="Dirt correlation",
        translation_key="dirt_weather_correlation",
    )

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_dirt_weather_correlation"

    def _results(self) -> dict[str, dict[str, Any]]:
        rps = getattr(self._config_entry.runtime_data, "robot_profile_store", None)
        if rps is None:
            return {}
        return rps.correlation_results()

    @staticmethod
    def _passing(results: dict[str, dict[str, Any]]) -> list[tuple[str, float]]:
        return sorted(
            (
                (eid, info["r"]) for eid, info in results.items()
                if info["r"] is not None and abs(info["r"]) > 0.3
            ),
            key=lambda p: abs(p[1]),
            reverse=True,
        )

    @property
    def native_value(self) -> StateType:
        passing = self._passing(self._results())
        return passing[0][1] if passing else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        results = self._results()
        passing = self._passing(results)
        return {
            "by_entity": results,
            "strongest_entity": passing[0][0] if passing else None,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # samples only change via cloud enrichment

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cc = self._config_entry.runtime_data.cloud_coordinator
        if cc is None:
            return

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(cc.async_add_listener(_on_coordinator_update))
class RoombaLastMissionSummarySensor(IRobotEntity, SensorEntity):
    """LAST-MISSION-SUMMARY (v3.1.0) — last completed mission as a single entity.

    native_value = result string of the last mission record.
    extra_state_attributes = all relevant mission fields in one place.

    Primary use-cases:
    - Troubleshooting: attach one entity to a bug report instead of digging
      through diagnostics.
    - Notification automations: trigger on state change, use attributes for
      the notification body without template work.

    Gate: none — available for all robots and all tiers. Returns None /
    empty attributes when MissionStore has no records yet.
    """

    _attr_translation_key = "last_mission_summary"
    _attr_entity_category = None          # Primary — visible on the device page
    _attr_has_entity_name = True

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_last_mission_summary"

    @property
    def suggested_object_id(self) -> str:
        return "last_mission_summary"

    @property
    def _latest(self) -> dict[str, Any] | None:
        """Return the most recent MissionStore record, or None."""
        store = self._entry.runtime_data.mission_store
        if store is None:
            return None
        return store.latest()

    @property
    def native_value(self) -> str | None:
        """Return the result of the last mission, e.g. 'completed'."""
        rec = self._latest
        return rec.get("result") if rec else None

    @property
    def _region_map_and_umf(self) -> tuple[dict[str, str], dict[str, str] | None]:
        """v3.1.1 ROOM-COVERAGE-IN-SUMMARY — build (region_map, umf_regions)
        exactly like vacuum.py's extra_state_attributes does, so
        latest_cleaned_rooms()/latest_room_coverage() resolve room display
        names the same way on both entities.
        """
        data = self._entry.runtime_data
        region_map: dict[str, str] = {}
        if data.has_cloud and data.cloud_coordinator is not None:
            region_map = {
                r["id"]: r["name"]
                for r in data.cloud_coordinator.regions
                if r.get("id")
            }
        umf_regions: dict[str, str] | None = None
        if not region_map and data.umf_aligner and data.umf_aligner.aligned:
            umf_regions = data.umf_aligner.rid_to_name()
        return region_map, umf_regions

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose all mission fields as attributes for easy automation use.

        v3.1.1 ROOM-COVERAGE-IN-SUMMARY — fixes a pre-existing bug where
        cleaned_rooms always returned None: the MissionStore record itself
        never carries a "last_cleaned_rooms" key (that name only exists as
        vacuum.py's *computed* attribute name, derived on the fly from
        timeline.finEvents via MissionStore.latest_cleaned_rooms() — it was
        never a literal stored field). Both cleaned_rooms and the new
        room_coverage attribute now call the same MissionStore methods
        vacuum.py uses, with the same region_map/umf_regions resolution,
        instead of reading a record key that was never populated.
        """
        rec = self._latest
        if rec is None:
            return {
                "result": None,
                "duration_min": None,
                "area_sqft": None,
                "cleaned_rooms": None,
                "room_coverage": None,
                "cleaning_passes": None,
                "battery_start_pct": None,
                "battery_end_pct": None,
                "recharges": None,
                "dirt_events": None,
                "evacuations": None,
                "error_code": None,
                "initiator": None,
                "started_at": None,
                "ended_at": None,
            }

        store = self._entry.runtime_data.mission_store
        cleaned_rooms = None
        room_coverage = None
        if store is not None:
            region_map, umf_regions = self._region_map_and_umf
            if region_map or umf_regions:
                cleaned_rooms = store.latest_cleaned_rooms(region_map, umf_regions)
                room_coverage = store.latest_room_coverage(region_map, umf_regions)

        return {
            "result": rec.get("result"),
            "duration_min": rec.get("duration_min"),
            "area_sqft": rec.get("area_sqft"),
            "cleaned_rooms": cleaned_rooms,
            "room_coverage": room_coverage,
            "cleaning_passes": rec.get("cleaning_passes"),
            "battery_start_pct": rec.get("battery_start_pct"),
            "battery_end_pct": rec.get("battery_end_pct"),
            "recharges": rec.get("recharges"),
            "dirt_events": rec.get("dirt_events"),
            "evacuations": rec.get("evacuations"),
            "error_code": rec.get("error_code"),
            "initiator": rec.get("initiator"),
            "started_at": rec.get("started_at"),
            "ended_at": rec.get("ended_at"),
        }


class RoombaRoomCleaningHistorySensor(IRobotEntity, SensorEntity):
    """ROOM-CLEANING-HISTORY (v3.1.0) — last clean timestamp per room.

    native_value = number of rooms for which a cleaning timestamp is known.
    extra_state_attributes = {room_name: iso_timestamp} dict spanning all
    records in MissionStore, newest-first scan so each room shows its most
    recent clean.

    Gate: only created when the robot has ever recorded room data
    (``last_cleaned_rooms`` in at least one MissionStore record). In practice
    this means SMART robots with cloud credentials, but the sensor is
    tier-agnostic — if an EPHEMERAL robot gains room data via future
    enrichment it will appear automatically.

    Primary use-cases:
    - Dashboard: "When was the kitchen last cleaned?"
    - Automation: template sensor pulling a single room's timestamp for
      a time-based notification.
    """

    _attr_translation_key = "room_cleaning_history"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_cleaning_history"

    @property
    def suggested_object_id(self) -> str:
        return "room_cleaning_history"

    @property
    def _history(self) -> dict[str, str]:
        store = self._entry.runtime_data.mission_store
        if store is None:
            return {}
        # v3.3.0 ROOM-SCHED foundation fix — pass the name-resolution
        # maps: live records derive rooms from timeline.finEvents now.
        region_map, umf_regions = _region_maps_for(self._entry.runtime_data)
        return store.room_cleaning_history(region_map, umf_regions)

    @property
    def native_value(self) -> int:
        """Number of rooms with a known last-clean timestamp."""
        return len(self._history)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Dict mapping room display name → ISO timestamp of last clean."""
        return self._history


def _region_maps_for(runtime_data: Any) -> tuple[dict[str, str], dict[str, str] | None]:
    """v3.3.0 ROOM-SCHED foundation fix — module-level twin of
    RoombaLastMissionSummarySensor._region_map_and_umf so the
    room-history/overdue sensors resolve names the same way."""
    region_map: dict[str, str] = {}
    if runtime_data.has_cloud and runtime_data.cloud_coordinator is not None:
        region_map = {
            r["id"]: r["name"]
            for r in runtime_data.cloud_coordinator.regions
            if r.get("id")
        }
    umf_regions: dict[str, str] | None = None
    if not region_map and runtime_data.umf_aligner and runtime_data.umf_aligner.aligned:
        umf_regions = runtime_data.umf_aligner.rid_to_name()
    return region_map, umf_regions


def _id_to_display_name(cc: Any) -> dict[str, str]:
    """v3.2.0 ROOM-TYPE-SUGGEST — shared {rid: display_name} resolver for
    ROOM-SIZE and ROOM-ACCESS (the two sensors that fall back to a raw
    region ID when a room isn't named — RoombaRoomCleaningHistorySensor
    doesn't need this: its room names already come pre-resolved from
    MissionStore's last_cleaned_rooms, not raw IDs).

    Fallback chain: user-set cc.regions name (authoritative — never
    overridden) -> iRobot's own top-scored region_suggestions type,
    ONLY when that top score is positive (a negative score, confirmed in
    real field data, means "probably NOT this type" — using it as a
    label would be actively misleading, not just imprecise) -> the raw
    rid as the final fallback, same as before this existed.

    Reliability of region_suggestions across many pmaps beyond the one
    sample seen (see MISSIONSTORE_FIELD_REGISTRY.md) is unconfirmed for
    most model families — this conservative positive-score gate is
    deliberately cautious about that uncertainty, not just about
    formatting the suggestion nicely. Second independent sample received
    (July 2026, Thonno, i7/lewis firmware, RESEARCH-ROOMTYPE) — the
    positive/negative gating held up exactly as expected on that
    device/firmware. Still open for other model families.
    """
    if cc is None:
        return {}
    id_to_name: dict[str, str] = {
        r["id"]: r["name"]
        for r in (cc.regions or [])
        if r.get("id") and r.get("name")
    }
    for suggestion in (cc.region_suggestions or []):
        rid = suggestion.get("region_id")
        if not rid or rid in id_to_name:
            continue
        types = suggestion.get("suggested_types") or []
        if not types:
            continue
        best = max(types, key=lambda t: t.get("score", float("-inf")))
        score = best.get("score")
        region_type = best.get("region_type")
        if score is not None and score > 0 and region_type:
            id_to_name[rid] = region_type.replace("_", " ").title()
    return id_to_name


class RoombaRoomAreasSensor(IRobotEntity, SensorEntity):
    """ROOM-SIZE (v3.1.0) — per-room floor area in m² from UMF polygons.

    native_value = number of rooms with a known area.
    extra_state_attributes = {room_display_name: area_m2} dict.

    Source: UmfAligner.room_areas_m2 (shoelace formula on UMF polygon
    vertices). Keys are translated from region IDs to display names via
    cloud_coordinator.regions. Falls back to region ID as key when the
    display name is not available.

    Does NOT require UmfAligner.aligned — room_areas_m2 is populated by
    _resolve_room_polygons() before the alignment step, so areas are
    available even at low confidence.

    Gate: SMART-tier + umf_aligner present. The UmfAligner is only
    instantiated for SMART robots with cloud credentials.
    """

    _attr_translation_key = "room_areas"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_areas"

    @property
    def suggested_object_id(self) -> str:
        return "room_areas"

    @property
    def _areas(self) -> dict[str, float]:
        """Return {display_name: area_m2} from UmfAligner, or {} if unavailable."""
        data = self._entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None:
            return {}
        areas_by_rid = aligner.room_areas_m2          # {rid: float}
        if not areas_by_rid:
            return {}
        id_to_name = _id_to_display_name(data.cloud_coordinator)
        return {
            id_to_name.get(rid, rid): round(area, 2)
            for rid, area in areas_by_rid.items()
        }

    @property
    def native_value(self) -> int:
        """Number of rooms with a known floor area."""
        return len(self._areas)

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        """Dict mapping room display name → floor area in m²."""
        return self._areas


class RoombaRoomAccessibilityScoresSensor(IRobotEntity, SensorEntity):
    """ROOM-ACCESS (v3.2.0) — per-room accessibility score 0-100, combining
    three signals: coverage fraction (GridStore.coverage_by_polygon),
    stuck-event rate (GridStore.stuck_by_polygon), and traversal time
    efficiency (MissionArchive.time_per_room, aggregated across mission
    history and normalised by ROOM-SIZE's room areas).

    native_value = number of rooms with a computed score.
    extra_state_attributes = {room_display_name: {"score": float,
    "limiting_factor": str}} — dict-sensor design, deliberately not one
    entity per room, matching the ROOM-CLEANING-HISTORY / ROOM-SIZE
    precedent from v3.1.0's PRIMARY-SLIM direction (avoid entity sprawl)
    rather than the original version-plan wording's implied per-room
    entities.

    Stuck-rate and time-efficiency sub-scores are judged against this
    robot's OWN average across its OWN rooms — see
    RobotProfileStore.room_accessibility_scores()'s docstring for why a
    fixed external threshold doesn't work here.

    Gate: SMART-tier + umf_aligner present (same as ROOM-SIZE — room
    polygons come from the same source).
    """

    _attr_translation_key = "room_accessibility_scores"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_room_accessibility_scores"

    @property
    def suggested_object_id(self) -> str:
        return "room_accessibility_scores"

    @property
    def _scores(self) -> dict[str, dict[str, Any]]:
        """Return {display_name: {"score": float, "limiting_factor": str}},
        or {} if the required inputs (UMF room polygons) aren't available."""
        data = self._entry.runtime_data
        aligner = data.umf_aligner
        if aligner is None:
            return {}
        polygons = aligner.room_polygons_umf
        areas_m2 = aligner.room_areas_m2
        if not polygons:
            return {}

        gs = data.grid_store
        coverage_by_room = gs.coverage_by_polygon(polygons) if gs is not None else {}
        stuck_by_room = gs.stuck_by_polygon(polygons) if gs is not None else {}

        # Aggregate time-per-room across mission history, then normalise
        # by each room's area to get seconds/m2 (comparable across
        # differently-sized rooms).
        time_by_room: dict[str, int] = {}
        archive = data.mission_archive
        if archive is not None:
            from .mission_archive import MissionArchive
            for record in archive.all_derived_oldest_first():
                visits = record.get("room_visits") or []
                for rid, seconds in MissionArchive.time_per_room(visits).items():
                    time_by_room[rid] = time_by_room.get(rid, 0) + seconds

        time_per_area_by_room: dict[str, float] = {}
        for rid, seconds in time_by_room.items():
            area = areas_m2.get(rid)
            if area and area > 0:
                time_per_area_by_room[rid] = seconds / area

        from .robot_profile_store import RobotProfileStore
        raw_scores = RobotProfileStore.room_accessibility_scores(
            coverage_by_room, stuck_by_room, time_per_area_by_room,
        )

        id_to_name = _id_to_display_name(data.cloud_coordinator)
        return {
            id_to_name.get(rid, rid): v
            for rid, v in raw_scores.items()
            if v.get("score") is not None
        }

    @property
    def native_value(self) -> int:
        """Number of rooms with a computed accessibility score."""
        return len(self._scores)

    @property
    def extra_state_attributes(self) -> dict[str, dict[str, Any]]:
        return self._scores
class RoombaRelocalisationRateSensor(IRobotEntity, SensorEntity):
    """L9-MAP (v3.1.0) — self-calibrating relocalisation rate sensor.

    native_value = recent-window mean reLc per mission (rounded to 2dp),
    or None until reloc_baseline_ready (needs _RELOC_BASELINE_MIN_MISSIONS
    observations).

    extra_state_attributes expose the underlying baseline, window, and
    (v3.5.0) percentile_rank — where the current window sits in this
    robot's own historical distribution, 0-100, with no fixed threshold
    baked in. This replaces the old reloc_rate_elevated Repair Issue and
    its fixed 3.0x multiplier (fragile against zero-inflated reLc data —
    see RobotProfileStore.reloc_percentile_rank()'s docstring); the
    integration no longer decides what counts as "elevated" for you —
    automate on percentile_rank with whatever cutoff matters to you.

    Gate: SMART-tier only — mssnNavStats is confirmed present on i7+/s9+
    (lewis firmware) via field data (Thonno), absent on 980/900-series.
    DIAGNOSTIC, disabled by default — this is a debugging/power-user signal,
    not a primary daily-use sensor, consistent with nav_quality (l_squal)
    which uses the same gating pattern.
    """

    _attr_translation_key = "relocalisation_rate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_relocalisation_rate"

    @property
    def suggested_object_id(self) -> str:
        return "relocalisation_rate"

    @property
    def _rps(self) -> Any:
        return self._entry.runtime_data.robot_profile_store

    @property
    def native_value(self) -> float | None:
        rps = self._rps
        if rps is None or not rps.reloc_baseline_ready:
            return None
        if not rps.recent_relocs:
            return None
        return round(sum(rps.recent_relocs) / len(rps.recent_relocs), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rps = self._rps
        if rps is None:
            return {
                "baseline": None,
                "baseline_mission_count": 0,
                "recent_window": [],
                "percentile_rank": None,
            }
        return {
            "baseline": round(rps.reloc_baseline, 2) if rps.reloc_baseline is not None else None,
            "baseline_mission_count": rps.reloc_mission_count,
            "recent_window": list(rps.recent_relocs),
            "percentile_rank": rps.reloc_percentile_rank(),
        }
