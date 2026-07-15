"""Descriptor-pattern core for the Roomba+ sensor platform.

SENSOR-SPLIT (v3.4.0): extracted from the former sensor.py monolith.
Holds RoombaSensorDescription, the SENSORS descriptor tuple, and the
generic RoombaSensor entity that renders any descriptor. Value
functions live in sensor_helpers.py. No behaviour change vs. v3.3.1.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfArea,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import StateType
import datetime as dt_stdlib

from homeassistant.util import dt as dt_util

from .const import (
    CLEAN_BASE_LABELS,
    CONF_BRUSH_HOURS,
    CONF_FILTER_HOURS,
    DEFAULT_BRUSH_HOURS,
    DEFAULT_FILTER_HOURS,
    JOB_INITIATOR_LABELS,
    MOP_RANK_LABELS,
    PAD_LABELS,
    SQFT_TO_M2,
    get_localized_error_entry,
    has_carpet_boost,
    has_clean_base,
    has_pose,
    is_mop,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .schedule_parser import (
    occurrences_from_schedule2,
    occurrences_from_schedule_v1,
    parse_schedule_occurrences,
)
from .sensor_helpers import (
    _ACTIVE_PHASES,
    _area_cleaned_today,
    _battery_age_days,
    _battery_capacity_retention,
    _brush_days_until_due,
    _brush_wear_rate,
    _carpet_boost_mode,
    _clean_mode,
    _completion_rate_30d,
    _error_value,
    _estcap_to_mah,
    _estimated_battery_eol,
    _expire_minutes_remaining,
    _filter_days_until_due,
    _filter_wear_rate,
    _last_error_at_value,
    _last_error_code_value,
    _last_mission_team_id,
    _mission_elapsed_value,
    _mission_store_last_started_at,
    _mission_store_value,
    _mop_behavior,
    _mop_clean_mode,
    _mop_tank_status,
    _next_likely_clean_window,
    _not_ready_value,
    _parse_netinfo_addr,
    _phase_value,
    _presence_opportunities,
    _presence_utilisation,
    _problem_zone_value,
    _recharge_minutes_remaining,
    _total_energy_consumed_kwh,
    _ts_or_none,
)


@dataclass(frozen=True, kw_only=True)
class RoombaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[IRobotEntity], StateType]
    filter_fn: Callable[[dict[str, Any]], bool] = field(
        default_factory=lambda: lambda _: True
    )
    # When set, the entity reports unavailable (not unknown) when fn returns False.
    # Use for sensors that only apply in a specific robot state (e.g. mid-mission
    # recharge). "Unknown" implies a data error; "Unavailable" is cleaner for
    # "not applicable right now".
    available_fn: Callable[[IRobotEntity], bool] | None = field(default=None)
    # v1.7.0 L2 — when set, exposed as "threshold_hours" in extra_state_attributes
    # Used by the Lovelace card to compute remaining % without hard-coded thresholds.
    threshold_fn: Callable[[IRobotEntity], int | None] = field(
        default_factory=lambda: lambda _: None
    )
    # v2.7.1 — optional extra attributes beyond what RoombaSensor.extra_state_attributes
    # provides by default. Merged into the default attributes dict when set.
    extra_attributes_fn: Callable[[IRobotEntity], dict[str, Any]] | None = field(
        default=None
    )
SENSORS: tuple[RoombaSensorDescription, ...] = (

    RoombaSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,
        value_fn=lambda e: e.vacuum_state.get("batPct"),
    ),
    RoombaSensorDescription(
        key="phase",
        translation_key="phase",
        name="Status",
        entity_category=None,
        value_fn=_phase_value,
    ),
    RoombaSensorDescription(
        key="error",
        translation_key="error",
        name="Status – Error",
        entity_category=None,
        value_fn=_error_value,
    ),

    # GROUP 2 — Operational (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="readiness",
        translation_key="readiness",
        name="Status – Readiness",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_not_ready_value,
    ),
    RoombaSensorDescription(
        key="job_initiator",
        translation_key="job_initiator",
        name="Status – Started by",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: JOB_INITIATOR_LABELS.get(
            e.clean_mission_status.get("initiator", "none"), "None"
        ),
    ),
    RoombaSensorDescription(
        key="clean_mode",
        translation_key="clean_mode",
        name="Setting – Cleaning passes",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_clean_mode,
    ),
    RoombaSensorDescription(
        key="carpet_boost_mode",
        translation_key="carpet_boost_mode",
        name="Setting – Carpet boost",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_carpet_boost,
        value_fn=_carpet_boost_mode,
    ),

    # GROUP 3 — Maintenance (DIAGNOSTIC, enabled)

    RoombaSensorDescription(
        key="filter_remaining_hours",
        translation_key="filter_remaining_hours",
        name="Maintenance – Filter",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        value_fn=lambda e: None,  # computed in RoombaSensor.native_value
        threshold_fn=lambda e: e._config_entry.options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS),
    ),
    RoombaSensorDescription(
        key="brush_remaining_hours",
        translation_key="brush_remaining_hours",
        name="Maintenance – Brushes",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        value_fn=lambda e: None,  # computed in RoombaSensor.native_value
        threshold_fn=lambda e: e._config_entry.options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS),
    ),
    RoombaSensorDescription(
        key="battery_cycles",
        translation_key="battery_cycles",
        name="Maintenance – Battery cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,  # F7h: was MEASUREMENT; lifetime counter
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            # 9-series (v2.4.x firmware): nLithChrg + nNimhChrg present in bbchg3.
            # Sum both because mixed chemistry replacements accumulate in each field.
            (e.battery_stats.get("nLithChrg") or 0) + (e.battery_stats.get("nNimhChrg") or 0)
            if "nLithChrg" in e.battery_stats
            # i/s-series (lewis/soho firmware): nLithChrg/nNimhChrg absent from bbchg3.
            # batInfo.cCount is the definitive BMS chip cycle counter (confirmed June 2026:
            # i7+ cCount=779 vs i8+ cCount=276 — correlates correctly with mission count).
            # nAvail on i/s-series has different semantics and is NOT a cycle counter.
            else e.vacuum_state.get("batInfo", {}).get("cCount")
        ),
    ),

    # GROUP 4 — Statistics (DIAGNOSTIC, enabled)

    # F12e — total energy consumed (HA Energy dashboard eligible)
    # F12e — total energy consumed.
    # RF0: scale applied via _total_energy_consumed_kwh for 9-series BMS correction.
    # Gate: bbchg3.estCap present.  batteryType field is unreliable (contains
    # iRobot part numbers, not chemistry strings) — chemistry detected at runtime
    # via nNimhChrg / nLithChrg cycle-count fields inside the helper.
    RoombaSensorDescription(
        key="total_energy_consumed",
        translation_key="total_energy_consumed",
        name="Total energy consumed",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_total_energy_consumed_kwh,
    ),

    RoombaSensorDescription(
        key="total_missions",
        translation_key="total_missions",
        name="Missions total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssn"),
    ),
    RoombaSensorDescription(
        key="successful_missions",
        translation_key="successful_missions",
        name="Missions successful",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnOk"),
    ),
    RoombaSensorDescription(
        key="canceled_missions",
        translation_key="canceled_missions",
        name="Missions canceled",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnC"),
    ),
    RoombaSensorDescription(
        key="failed_missions",
        translation_key="failed_missions",
        name="Missions failed",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("nMssnF"),
    ),
    RoombaSensorDescription(
        key="total_cleaning_time",
        translation_key="total_cleaning_time",
        name="Missions – Total time",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.run_stats.get("hr"),
    ),
    RoombaSensorDescription(
        key="average_mission_time",
        translation_key="average_mission_time",
        name="Missions – Avg. duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.mission_stats.get("aMssnM"),
    ),

    # LOCAL-RATE — lifetime completion rate from bbmssn (no cloud required, all robots)
    RoombaSensorDescription(
        key="lifetime_completion_rate",
        translation_key="lifetime_completion_rate",
        name="Missions – Lifetime completion rate",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda e: bool(e.mission_stats.get("nMssn")),
        value_fn=lambda e: (
            round(
                e.mission_stats.get("nMssnOk", 0)
                / max(e.mission_stats.get("nMssn", 1), 1)
                * 100,
                1,
            )
            if e.mission_stats.get("nMssn")
            else None
        ),
        extra_attributes_fn=lambda e: {
            "ok":             e.mission_stats.get("nMssnOk"),
            "cancelled":      e.mission_stats.get("nMssnC"),
            "failed":         e.mission_stats.get("nMssnF"),
            "total":          e.mission_stats.get("nMssn"),
            "avg_mission_min": e.mission_stats.get("aMssnM"),
            "avg_cycle_min":   e.mission_stats.get("aCycleM"),
        },
    ),

    # BAT-INFO sensors (i/s-series only — batInfo absent on 9-series firmware)

    RoombaSensorDescription(
        key="battery_cycle_count_bms",
        translation_key="battery_cycle_count_bms",
        name="Battery – BMS cycle count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        available_fn=lambda e: e.vacuum_state.get("batInfo") is not None,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("batInfo", {}).get("cCount"),
        extra_attributes_fn=lambda e: {
            "manufacturer": e.vacuum_state.get("batInfo", {}).get("mName"),
            "is_oem":       e.vacuum_state.get("batInfo", {}).get("mName") == "PanasonicEnergy",
        },
    ),

    RoombaSensorDescription(
        key="battery_age_days",
        translation_key="battery_age_days",
        name="Battery – Age",
        native_unit_of_measurement="d",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        available_fn=lambda e: bool(
            (e.vacuum_state.get("batInfo") or {}).get("mDate")
        ),
        value_fn=lambda e: _battery_age_days(e),
    ),

    # GROUP 5 — Opt-in (DIAGNOSTIC, disabled by default)

    RoombaSensorDescription(
        key="total_cleaned_area",
        translation_key="total_cleaned_area",
        name="Lifetime cleaned area",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        # v2.9.0 (J) — same AREA-ZERO-FIX family as v2.8.2's
        # mission.get("sqft") or None fix in callbacks.py: confirmed on the
        # same hardware (980 9-series + aftermarket NiMH battery) that
        # firmware can report a literal 0 instead of omitting the field
        # even with genuine cleaning history. `or None` treats a literal 0
        # as "no reliable value" — a falsely-missing display beats a
        # confidently wrong one.
        #
        # Field report investigated: this value (176 m²) appeared smaller
        # than cleaning_analytics_30d's window-sum (683 m² over its cloud
        # API window). Initially suspected a battery-replacement-triggered
        # counter reset as the explanation — DISPROVEN, not just
        # unconfirmed: the integration's "Reset battery" button
        # (maintenance_store.reset_battery()) only writes battery_reset_hr/
        # battery_reset_at to our OWN local MaintenanceStore for our own
        # wear-rate bookkeeping. It sends no MQTT command and has no path
        # to the robot's firmware at all — it cannot possibly reset
        # bbrun.sqft. The two values being non-comparable (cleaning_
        # analytics_30d explicitly sums "for the API window (not
        # lifetime)" per cloud_coordinator.py's own docstring) still holds,
        # but the remaining gap beyond that has NO confirmed explanation.
        # Left as an open, honestly-unexplained discrepancy rather than
        # attributed to a mechanism that was checked and does not exist.
        # v2.9.0 (J) — SOURCE CHANGE. The robot's own onboard lifetime
        # counter (bbrun.sqft / runtimeStats.sqft) was field-confirmed
        # (via a months-old diagnostics snapshot showing 1882 sqft ≈
        # 174.8 m², essentially the same as a recent reading of 176 m²)
        # to barely change over a very long period, while every OTHER
        # bbrun.* counter (nPanics, nStuck, etc.) keeps incrementing
        # normally. Investigated against dorita980/roombapy/Roomba980-
        # Python reference implementations: sqft/hr/min are reported
        # atomically alongside the actively-incrementing counters in the
        # SAME bbrun object — no documented firmware quirk explains this,
        # and our own merge logic (entity.py's run_stats, which could have
        # let a stale runtimeStats override a fresh bbrun value) was ruled
        # out: this robot's master_state_keys never contains "runtimeStats"
        # at all. The mechanism remains genuinely unexplained.
        #
        # Rather than keep trusting a counter we cannot independently
        # verify, the primary value uses MissionArchive's cumulative_sqft
        # — a persistent running total over EVERY mission ever archived
        # (not capped at MAX_RECORDS=800; see the accumulator's own
        # docstring in mission_archive.py for why a live re-sum over the
        # currently-held records would be wrong for any robot with more
        # lifetime missions than that). Built entirely from cloud
        # per-mission data, which does NOT share whatever mechanism
        # affects bbrun.sqft (confirmed non-zero, plausible per-mission
        # values throughout today's investigation). Still not necessarily
        # a true "since the robot was new" total (limited by however far
        # back the cloud account itself retains mission history, and only
        # starts accumulating from whenever cloud credentials were first
        # configured), but self-consistent, independently verifiable, and
        # immune to the bbrun staleness
        # question entirely.
        #
        # Both sources are only LOWER BOUNDS on the true lifetime total —
        # the archive can be incomplete (cloud credentials added only
        # after months of local-only use), and the onboard counter can
        # freeze, but whatever it captured before freezing was real,
        # already-cleaned area. A genuine lifetime total can never DECREASE
        # relative to either source, so the displayed value is always the
        # larger of the two — never let a more-complete archive disagree
        # downward from a frozen-but-still-real onboard reading, and vice
        # versa.
        #
        # Uses MissionArchive.cumulative_sqft (a persistent running total,
        # incremented once per newly-archived mission BEFORE any FIFO
        # trim) rather than summing all_derived_oldest_first() live — a
        # robot with more than MAX_RECORDS (800) lifetime missions would
        # otherwise see this number DECREASE every time an old mission
        # ages out of the FIFO-capped list, which is exactly the kind of
        # "lifetime total going backwards" this fix exists to prevent.
        value_fn=lambda e: (
            lambda arc: max(
                (
                    round(arc.cumulative_sqft * SQFT_TO_M2, 1)
                    if arc is not None and arc.cumulative_sqft > 0
                    else None
                ) or 0.0,
                (
                    round(sqft * SQFT_TO_M2, 1)
                    if (sqft := e.run_stats.get("sqft"))
                    else None
                ) or 0.0,
            ) or None
        )(getattr(e._config_entry.runtime_data, "mission_archive", None)),
        # v2.9.0 (J) — bbrun's raw reading + staleness tracking kept as
        # attributes for comparison/diagnosis, now that it's no longer the
        # primary value. RobotProfileStore staleness fields are updated in
        # __init__.py's _async_update_robot_profile_store, not here —
        # value_fn/extra_attributes_fn stay side-effect-free, consistent
        # with every other sensor in this module.
        extra_attributes_fn=lambda e: (
            lambda rps, arc: {
                "onboard_counter_m2": (
                    round(sqft * SQFT_TO_M2, 1)
                    if (sqft := e.run_stats.get("sqft"))
                    else None
                ),
                "onboard_counter_last_changed_at": (
                    rps.lifetime_sqft_last_changed_at if rps is not None else None
                ),
                "onboard_counter_days_unchanged": (
                    round(d, 1)
                    if rps is not None
                    and (d := rps.lifetime_sqft_days_unchanged) is not None
                    else None
                ),
                "archived_mission_count": (
                    arc.record_count if arc is not None else 0
                ),
            }
        )(
            getattr(e._config_entry.runtime_data, "robot_profile_store", None),
            getattr(e._config_entry.runtime_data, "mission_archive", None),
        ),
    ),
    RoombaSensorDescription(
        key="last_mission",
        translation_key="last_mission",
        name="Missions – Last",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Read from MissionStore rather than live MQTT cleanMissionStatus.mssnStrtTm.
        # On 900-series firmware (980/985) mssnStrtTm is reset to 0 when the robot
        # docks — the live value is always None outside of an active mission.
        # MissionStore holds the correctly cached started_at from mission start.
        value_fn=lambda e: _mission_store_last_started_at(e),
    ),
    RoombaSensorDescription(
        key="scrubs_count",
        translation_key="scrubs_count",
        name="Dirt detect events",
        state_class=SensorStateClass.TOTAL_INCREASING,  # F7h: was MEASUREMENT; lifetime counter
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.run_stats.get("nScrubs"),
    ),
    RoombaSensorDescription(
        key="rssi",
        translation_key="rssi",
        name="Wi-Fi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("rssi"),
    ),

    RoombaSensorDescription(
        key="snr",
        translation_key="snr",
        name="SNR",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("snr"),
    ),

    RoombaSensorDescription(
        key="signal_noise",
        translation_key="signal_noise",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.vacuum_state.get("signal", {}).get("noise"),
    ),

    RoombaSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        name="IP address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _parse_netinfo_addr(
            (e.vacuum_state.get("netinfo") or {}).get("addr")
        ),
    ),


    # Navigation quality (VSLAM robots: 900/i/s/j — opt-in, disabled by default)
    # l_squal: 0–100, measures how well the VSLAM algorithm can navigate.
    # Low values indicate poor lighting or significant environmental changes.
    RoombaSensorDescription(
        key="nav_quality",
        translation_key="nav_quality",
        name="Navigation quality",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=has_pose,
        value_fn=lambda e: e.vacuum_state.get("mssnNavStats", {}).get("l_squal"),
    ),

    # Mission-time sensors
    RoombaSensorDescription(
        key="mission_start_time",
        translation_key="mission_start_time",
        name="Mission start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            e.last_mission
            if e.clean_mission_status.get("phase") in _ACTIVE_PHASES
            else None
        ),
        available_fn=lambda e: e.clean_mission_status.get("phase") in _ACTIVE_PHASES,
    ),

    RoombaSensorDescription(
        key="mission_elapsed_time",
        translation_key="mission_elapsed_time",
        name="Mission elapsed time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mission_elapsed_value,
        available_fn=lambda e: e.clean_mission_status.get("phase") in
            ("run", "hmMidMsn", "evac", "charge", "hmPostMsn")
            and e.clean_mission_status.get("cycle") not in (None, "none"),
    ),

    # SC2 (v2.7.0): TIMESTAMP is the preferred variant — enabled by default.
    # mission_recharge_minutes (numeric) is disabled below.
    RoombaSensorDescription(
        key="mission_recharge_time",
        translation_key="mission_recharge_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("rechrgTm")),
    ),

    # SC2 (v2.7.0): TIMESTAMP is the preferred variant — enabled by default.
    # mission_expire_minutes (numeric) was already disabled in v2.6.2.
    RoombaSensorDescription(
        key="mission_expire_time",
        translation_key="mission_expire_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _ts_or_none(e.clean_mission_status.get("expireTm")),
    ),


    # ── v1.9.3 — Mission Phase Intelligence ──────────────────────────────────
    # These sensors expose the sub-state of the vacuum entity that the standard
    # VacuumActivity enum cannot express. The key distinction is mid-mission
    # recharge (phase=charge, cycle≠none) vs completed charging (cycle=none).
    # missionId is stable across all recharge cycles of a single mission,
    # allowing dashboards to group related events together.

    RoombaSensorDescription(
        key="mission_recharge_minutes",
        translation_key="mission_recharge_minutes",
        name="Mission – Recharge time remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # SC2 (v2.7.0): disabled by default — use mission_recharge_time (TIMESTAMP)
        # which HA renders natively as a countdown. Kept for users with existing
        # automations; will be removed in v3.0.
        entity_registry_enabled_default=False,
        # rechrgM is pre-computed by 980/900-series firmware.
        # lewis firmware (i/s/j-series) sends rechrgTm (Unix timestamp) and
        # leaves rechrgM=0. Fall back to computing remaining minutes from rechrgTm
        # so mid-mission recharge time is reported correctly on all robots.
        value_fn=lambda e: _recharge_minutes_remaining(e.clean_mission_status),
        # Unavailable (not Unknown) when no mid-mission recharge is active.
        available_fn=lambda e: bool(
            e.clean_mission_status.get("rechrgTm")
            or e.clean_mission_status.get("rechrgM")
        ),
    ),
    RoombaSensorDescription(
        key="mission_expire_minutes",
        translation_key="mission_expire_minutes",
        name="Mission – Time until expiry",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # expireM is pre-computed by 980/900-series firmware.
        # On 900-series (EPHEMERAL): expireTm == rechrgTm — mirrors the recharge
        # countdown, does not represent a separate mission deadline.
        # On i/s/j-series (SMART): expireTm can differ from rechrgTm.
        # Disabled by default to avoid confusion; SMART users can enable it.
        entity_registry_enabled_default=False,
        value_fn=lambda e: _expire_minutes_remaining(e.clean_mission_status),
        available_fn=lambda e: bool(
            e.clean_mission_status.get("expireTm")
            or e.clean_mission_status.get("expireM")
        ),
    ),
    RoombaSensorDescription(
        key="mission_id",
        translation_key="mission_id",
        name="Mission – ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        # missionId: stable string across all recharge cycles of one mission.
        # Populated only on i/s/j-series (older firmware may not send it).
        filter_fn=lambda s: "missionId" in (s.get("cleanMissionStatus") or {}),
        value_fn=lambda e: e.clean_mission_status.get("missionId") or None,
    ),
    # Schedule sensor (all models with cleanSchedule2 or cleanSchedule)

    RoombaSensorDescription(
        key="next_clean",
        translation_key="next_clean",
        name="Status – Next clean",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: bool(s.get("cleanSchedule2") or s.get("cleanSchedule")),
        value_fn=lambda e: None,   # computed in RoombaSensor.native_value
    ),

    # Device-specific: Clean Base

    RoombaSensorDescription(
        key="clean_base_status",
        translation_key="clean_base_status",
        name="Clean Base status",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=has_clean_base,
        value_fn=lambda e: CLEAN_BASE_LABELS.get(
            (e.vacuum_state.get("dock") or {}).get("state", -2), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="dock_firmware_version",
        translation_key="dock_firmware_version",
        name="Dock firmware version",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "fwVer" in (s.get("dock") or {}),
        value_fn=lambda e: (e.vacuum_state.get("dock") or {}).get("fwVer"),
    ),
    RoombaSensorDescription(
        key="dock_tank_level",
        translation_key="dock_tank_level",
        name="Dock tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in (s.get("dock") or {}),
        value_fn=lambda e: e.dock_tank_level,
    ),

    # Device-specific: Braava / mop

    RoombaSensorDescription(
        key="tank_level",
        translation_key="tank_level",
        name="Tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.tank_level,
    ),
    RoombaSensorDescription(
        key="mop_pad",
        translation_key="mop_pad",
        name="Mop pad",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "detectedPad" in s,
        value_fn=lambda e: PAD_LABELS.get(
            e.vacuum_state.get("detectedPad", "invalid"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_behavior",
        translation_key="mop_behavior",
        name="Mop – Clean passes (legacy)",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,  # superseded by mop_ars_behavior (F3b)
        filter_fn=lambda s: "rankOverlap" in s,
        value_fn=lambda e: MOP_RANK_LABELS.get(
            e.vacuum_state.get("rankOverlap"), "Unknown"
        ),
    ),
    RoombaSensorDescription(
        key="mop_tank_level",
        translation_key="mop_tank_level",
        name="Mop tank level",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "tankLvl" in s and "detectedPad" in s,
        value_fn=lambda e: e.vacuum_state.get("tankLvl"),
    ),

    # F2 -- Mop clean mode (padWetness as named enum)
    RoombaSensorDescription(
        key="mop_clean_mode",
        translation_key="mop_clean_mode",
        name="Mop – Clean mode",
        device_class=SensorDeviceClass.ENUM,
        options=["dry", "wet", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "padWetness" in s,
        value_fn=_mop_clean_mode,
    ),

    # F3 -- Mop tank status (consolidated from 4 binary mopReady sub-fields)
    RoombaSensorDescription(
        key="mop_tank_status",
        translation_key="mop_tank_status",
        name="Mop – Tank status",
        device_class=SensorDeviceClass.ENUM,
        options=["ready", "fill_tank", "lid_open", "tank_missing", "unknown"],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "mopReady" in s,
        value_fn=_mop_tank_status,
    ),

    # F3b -- Mop behavior / Auto Replenishment System mode
    RoombaSensorDescription(
        key="mop_ars_behavior",
        translation_key="mop_ars_behavior",
        name="Mop – ARS behavior",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "no_mop", "extended", "standard", "deep",
            "dirty_pause", "dry", "wash",
            "dirty_pause_dry", "dirty_pause_wash", "dry_wash",
            "dirty_pause_dry_wash", "unknown",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "rankOverlap" in s or "padDryAllowed" in s,
        value_fn=_mop_behavior,
    ),
    # State is "unknown" on pre-v1.7 installs until the first reset is performed.

    RoombaSensorDescription(
        key="filter_last_replaced",
        translation_key="filter_last_replaced",
        name="Maintenance – Filter last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.filter_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.filter_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="brush_last_replaced",
        translation_key="brush_last_replaced",
        name="Maintenance – Brushes last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),  # Braava uses pad_last_replaced
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="pad_last_replaced",
        translation_key="pad_last_replaced",
        name="Maintenance – Pad last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),  # Braava only — same store slot as brush
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.brush_reset_at
            )
            else None
        ),
    ),
    RoombaSensorDescription(
        key="battery_last_replaced",
        translation_key="battery_last_replaced",
        name="Maintenance – Battery last replaced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.battery_reset_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.battery_reset_at
            )
            else None
        ),
    ),

    # ── IA74-MAINT (v2.7.0) — calendar-based inspect timestamps ──────────────
    # Wheel module, charging contacts, and bin are cleaned on a calendar cadence
    # rather than an hours-of-use basis.  These sensors expose the last-cleaned
    # wall-clock timestamp so dashboards and reminders can be built from them.

    RoombaSensorDescription(
        key="wheel_last_cleaned",
        translation_key="wheel_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Wheel last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.wheel_cleaned_at is not None
        ),
    ),
    RoombaSensorDescription(
        key="contact_last_cleaned",
        translation_key="contact_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Contacts last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.contact_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.contact_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.contact_cleaned_at is not None
        ),
    ),
    RoombaSensorDescription(
        key="bin_last_cleaned",
        translation_key="bin_last_cleaned",
        entity_registry_enabled_default=False,
        name="Maintenance – Bin last cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: (
            dt_util.parse_datetime(
                e._config_entry.runtime_data.maintenance_store.bin_cleaned_at
            )
            if (
                e._config_entry.runtime_data.maintenance_store
                and e._config_entry.runtime_data.maintenance_store.bin_cleaned_at
            )
            else None
        ),
        available_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store is not None
            and e._config_entry.runtime_data.maintenance_store.bin_cleaned_at is not None
        ),
    ),

    # ── v1.8.0 L1 — Mission Log ───────────────────────────────────────────────

    RoombaSensorDescription(
        key="clean_streak",
        translation_key="clean_streak",
        name="Missions – Clean streak",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,  # PRIMARY-SLIM (v3.1.0): pure statistic, not daily-use
        value_fn=lambda e: _mission_store_value(e, lambda s: s.clean_streak()),
    ),
    RoombaSensorDescription(
        key="last_mission_team_id",
        translation_key="last_mission_team_id",
        name="Missions – Last mission team ID",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,  # niche — Imprint Link team-clean users only
        value_fn=lambda e: _mission_store_value(e, _last_mission_team_id),
    ),
    RoombaSensorDescription(
        key="missions_last_30d",
        translation_key="missions_last_30d",
        name="Missions – Last 30 days",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: len(s.query(30, result="completed"))
        ),
    ),
    RoombaSensorDescription(
        key="completion_rate_30d",
        translation_key="completion_rate_30d",
        name="Missions – Completion rate",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(e, _completion_rate_30d),
    ),
    RoombaSensorDescription(
        key="area_cleaned_today",
        translation_key="area_cleaned_today",
        name="Missions – Area cleaned today",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: has_pose(s),   # 600-series reports no sqft
        value_fn=lambda e: _mission_store_value(e, _area_cleaned_today),
    ),
    RoombaSensorDescription(
        key="last_mission_result",
        translation_key="last_mission_result",
        name="Missions – Last result",
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        value_fn=lambda e: _mission_store_value(
            e, lambda s: s.latest().get("result") if s.latest() else None
        ),
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store.records
        ),
    ),
    RoombaSensorDescription(
        key="last_mission_duration",
        translation_key="last_mission_duration",
        name="Missions – Last duration",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: s.latest().get("duration_min") if s.latest() else None
        ),
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store.records
        ),
    ),

    # ── v1.8.0 L3 — Error Intelligence ───────────────────────────────────────

    RoombaSensorDescription(
        key="last_error_code",
        translation_key="last_error_code",
        name="Error – Last code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_code_value,
        available_fn=lambda e: bool(
            e.vacuum_state.get("cleanMissionStatus", {}).get("error", 0)
            or e._config_entry.runtime_data.last_error_code is not None
        ),
    ),
    RoombaSensorDescription(
        key="last_error_at",
        translation_key="last_error_at",
        name="Error – Last time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_error_at_value,
        available_fn=lambda e: bool(e._config_entry.runtime_data.last_error_at),
    ),
    RoombaSensorDescription(
        key="last_error_zone",
        translation_key="last_error_zone",
        name="Error – Last zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        # No filter_fn — created for all robots.
        # SMART: resolved from lastCommand.regions at mission start.
        # EPHEMERAL: resolved from ZoneStore at mission start.
        value_fn=lambda e: e._config_entry.runtime_data.last_error_zone,
        available_fn=lambda e: e._config_entry.runtime_data.last_error_zone is not None,
    ),
    RoombaSensorDescription(
        key="stuck_count_30d",
        translation_key="stuck_count_30d",
        name="Error – Stuck events (30 days)",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _mission_store_value(
            e, lambda s: len(s.query(30, result=s.STUCK_RESULTS))
        ),
    ),
    RoombaSensorDescription(
        key="problem_zone",
        translation_key="problem_zone",
        name="Error – Problem zone",
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: has_pose(s),   # requires zone tracking — excludes 600-series
        value_fn=_problem_zone_value,
        available_fn=lambda e: bool(
            e._config_entry.runtime_data.mission_store
            and e._config_entry.runtime_data.mission_store.query(30, result=e._config_entry.runtime_data.mission_store.STUCK_RESULTS)
        ),
    ),

    # ── v3.0.0 L3-FIX — Consecutive anomalous missions ────────────────────────
    #
    # Exposes MissionStore.consecutive_anomalous as a standalone sensor.
    # Disabled by default — only the companion Card and Automations consume it.
    # Card C5-ANOMALY banner triggers at ≥3 (two consecutive anomalies can be
    # coincidence; three are a pattern).
    RoombaSensorDescription(
        key="consecutive_mission_anomalies",
        translation_key="consecutive_mission_anomalies",
        name="Error – Consecutive anomalous missions",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: (
            e._config_entry.runtime_data.mission_store.consecutive_anomalous
            if e._config_entry.runtime_data.mission_store is not None
            else None
        ),
        available_fn=lambda e: e._config_entry.runtime_data.mission_store is not None,
        # v3.2.0 ANOMALY-EXPLAIN — surfaces the mission id to pass straight
        # into the explain_mission service/REST endpoint, so the card (or
        # an automation) doesn't need to separately query mission history
        # just to find out which mission this count is even about.
        extra_attributes_fn=lambda e: (
            {"last_mission_id": e._config_entry.runtime_data.mission_store.latest().get("id")}
            if e._config_entry.runtime_data.mission_store is not None
            and e._config_entry.runtime_data.mission_store.latest() is not None
            else {}
        ),
    ),

    # ── v1.8.0 L6 — Presence Analytics ───────────────────────────────────────

    RoombaSensorDescription(
        key="presence_clean_opportunities_7d",
        translation_key="presence_clean_opportunities_7d",
        name="Presence – Clean opportunities (7 days)",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _presence_opportunities(e, 7),
    ),
    RoombaSensorDescription(
        key="presence_clean_utilisation_7d",
        translation_key="presence_clean_utilisation_7d",
        name="Presence – Clean utilisation (7 days)",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _presence_utilisation(e, 7),
    ),
    RoombaSensorDescription(
        key="next_likely_clean_window",
        translation_key="next_likely_clean_window",
        name="Presence – Next clean window",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_next_likely_clean_window,
    ),



    # ── v1.9.0 L4 — Wear Intelligence ────────────────────────────────────────

    RoombaSensorDescription(
        key="filter_wear_rate",
        translation_key="filter_wear_rate",
        name="Maintenance – Filter wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_filter_wear_rate,
    ),
    RoombaSensorDescription(
        key="brush_wear_rate",
        translation_key="brush_wear_rate",
        name="Maintenance – Brush wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: not is_mop(s),
        value_fn=_brush_wear_rate,
    ),
    RoombaSensorDescription(
        key="pad_wear_rate",
        translation_key="pad_wear_rate",
        name="Maintenance – Pad wear rate",
        native_unit_of_measurement="h/day",
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),
        value_fn=_brush_wear_rate,
    ),
    RoombaSensorDescription(
        key="filter_days_until_due",
        translation_key="filter_days_until_due",
        name="Maintenance – Filter days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: not is_mop(s),
        value_fn=_filter_days_until_due,
    ),
    RoombaSensorDescription(
        key="brush_days_until_due",
        translation_key="brush_days_until_due",
        name="Maintenance – Brush days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=None,  # reclassified DIAG→MAIN (v2.6.0)
        filter_fn=lambda s: not is_mop(s),
        value_fn=_brush_days_until_due,
    ),
    RoombaSensorDescription(
        key="pad_days_until_due",
        translation_key="pad_days_until_due",
        name="Maintenance – Pad days until due",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: is_mop(s),
        value_fn=_brush_days_until_due,
    ),
    # ── v1.9.0 Device Intelligence ───────────────────────────────────────────
    # opt-in (entity_registry_enabled_default=False): lifetime diagnostic
    # counters and static hardware values. Useful for power users and
    # debugging but not relevant for daily automations.

    RoombaSensorDescription(
        key="battery_capacity_mah",
        translation_key="battery_capacity_mah",
        name="Battery capacity",
        native_unit_of_measurement="mAh",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_estcap_to_mah,   # RF0: divides by BMS scale for 9-series
    ),
    RoombaSensorDescription(
        key="nav_panics",
        translation_key="nav_panics",
        name="Navigation panic events",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nPanics" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nPanics"),
    ),
    RoombaSensorDescription(
        key="cliff_events_front",
        translation_key="cliff_events_front",
        name="Cliff events – Front",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsF" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nCliffsF"),
    ),
    RoombaSensorDescription(
        key="cliff_events_rear",
        translation_key="cliff_events_rear",
        name="Cliff events – Rear",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nCliffsR" in (s.get("bbrun") or {}),
        value_fn=lambda e: e.run_stats.get("nCliffsR"),
    ),

    # FIELD-SENSORS (v2.8.0) — 5 additional diagnostic sensors from 3-robot
    # MQTT field analysis (nCliffsF / nCliffsR / nPanics already above).
    #
    # Navigation subsystem (bbnav) — 9-series only, absent on i/s/j-series:
    RoombaSensorDescription(
        key="nav_landmark_quality",
        translation_key="nav_landmark_quality",
        name="Navigation landmark quality",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "aMtrack" in (s.get("bbnav") or {}),
        value_fn=lambda e: e.nav_stats.get("aMtrack"),
    ),
    RoombaSensorDescription(
        key="nav_good_landmarks",
        translation_key="nav_good_landmarks",
        name="Navigation good landmarks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nGoodLmrks" in (s.get("bbnav") or {}),
        value_fn=lambda e: e.nav_stats.get("nGoodLmrks"),
    ),
    # Run statistics (bbrun / runtimeStats) — i/s-series only, absent on 9-series:
    RoombaSensorDescription(
        key="optical_dirt_detections",
        translation_key="optical_dirt_detections",
        name="Optical dirt detections",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nOpticalDD" in (s.get("bbrun") or {})
            or "nOpticalDD" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nOpticalDD"),
    ),
    RoombaSensorDescription(
        key="piezo_dirt_detections",
        translation_key="piezo_dirt_detections",
        name="Piezo dirt detections",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nPiezoDD" in (s.get("bbrun") or {})
            or "nPiezoDD" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nPiezoDD"),
    ),
    RoombaSensorDescription(
        key="nav_orientations",
        translation_key="nav_orientations",
        name="Navigation orientations",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: (
            "nOrients" in (s.get("bbrun") or {})
            or "nOrients" in (s.get("runtimeStats") or {})
        ),
        value_fn=lambda e: e.run_stats.get("nOrients"),
    ),

    # DOCK-HEALTH (v2.8.0) — dock contact health counters from bbchg.
    # Field confirmed present on i/s-series (lewis/soho firmware) and
    # some 9-series firmware variants.  Thresholds are conservative heuristics
    # pending community field data calibration (see COMM-A roadmap).
    RoombaSensorDescription(
        key="dock_contact_chatters",
        translation_key="dock_contact_chatters",
        name="Dock contact chatters",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nChatters" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nChatters"),
    ),
    RoombaSensorDescription(
        key="dock_knockoffs",
        translation_key="dock_knockoffs",
        name="Dock knockoffs",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nKnockoffs" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nKnockoffs"),
    ),
    RoombaSensorDescription(
        key="dock_charge_aborts",
        translation_key="dock_charge_aborts",
        name="Dock charge aborts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        filter_fn=lambda s: "nAborts" in (s.get("bbchg") or {}),
        value_fn=lambda e: e.dock_stats.get("nAborts"),
    ),

    # F5d -- battery capacity retention (% of baseline estCap)
    RoombaSensorDescription(
        key="battery_capacity_retention",
        translation_key="battery_capacity_retention",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_battery_capacity_retention,
    ),

    # F5g -- estimated battery end-of-life (days remaining until 65% threshold)
    # SEN2 (v2.7.0): state_class removed — this is a heuristic prediction, not
    # a physical measurement. HA should not build long-term statistics from it.
    RoombaSensorDescription(
        key="estimated_battery_eol",
        translation_key="estimated_battery_eol",
        name="Maintenance – Est. battery end of life",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.DIAGNOSTIC,
        filter_fn=lambda s: "estCap" in (s.get("bbchg3") or {}),
        value_fn=_estimated_battery_eol,
        # available_fn calls the function directly: covers all None conditions
        # (no baseline, no cycles, no degradation yet).
        available_fn=lambda e: _estimated_battery_eol(e) is not None,
    ),

    # F6g -- consecutive clean skips counter (diagnostic).
    # Reads from MaintenanceStore via _config_entry.runtime_data — the standard
    # HA pattern for RoombaSensor value_fn accessing integration-level storage.
    # Guarded defensively: sensor is created before storage is confirmed non-None.
    RoombaSensorDescription(
        key="consecutive_clean_skips",
        translation_key="consecutive_clean_skips",
        name="Performance – Consecutive clean skips",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: (
            e._config_entry.runtime_data.maintenance_store.consecutive_skips
            if (
                hasattr(e, "_config_entry")
                and e._config_entry.runtime_data is not None
                and e._config_entry.runtime_data.maintenance_store is not None
            )
            else None
        ),
    ),
)
class RoombaSensor(IRobotEntity, SensorEntity):
    """A sensor entity for Roomba+, driven by the EntityDescription pattern."""

    entity_description: RoombaSensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        description: RoombaSensorDescription,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"
        self._unsub_tick: Callable[[], None] | None = None

    @property
    def suggested_object_id(self) -> str:
        """Override: use description key directly (more explicit than uid-strip)."""
        return self.entity_description.key


    # ── Countdown tick for recharge/expire minute sensors ────────────────────
    # iRobot firmware sends rechrgTm / expireTm once at recharge start and does
    # not push further cleanMissionStatus updates during charging.  Without a
    # periodic tick the sensor value stays frozen at the initial reading.
    # We schedule a 60-second interval whenever the sensor is enabled so the
    # value decrements correctly, matching what the iRobot app displays.

    _TICK_SENSORS = frozenset({"mission_recharge_minutes", "mission_expire_minutes"})

    async def async_added_to_hass(self) -> None:
        """Register MQTT callback and start the 60-second countdown tick."""
        await super().async_added_to_hass()
        if self.entity_description.key in self._TICK_SENSORS:
            self._unsub_tick = async_track_time_interval(
                self.hass,
                self._async_tick,
                dt_stdlib.timedelta(seconds=60),
            )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the countdown tick when the entity is removed."""
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    @callback
    def _async_tick(self, _now: dt_stdlib.datetime) -> None:
        """Re-evaluate native_value every 60 s so the countdown decrements live.

        async_write_ha_state() would be a no-op if HA thinks the state has not
        changed.  schedule_update_ha_state(force_refresh=True) forces HA to
        re-read native_value and unconditionally push the new value to the
        state machine, which is what we need for the minute countdown.
        """
        self.schedule_update_ha_state(force_refresh=True)

    @property
    def available(self) -> bool:
        """Return False when available_fn signals sensor not applicable right now."""
        if self.entity_description.available_fn is not None:
            if not self.entity_description.available_fn(self):
                return False
        return super().available

    @property
    def native_value(self) -> StateType:
        key = self.entity_description.key
        options = self._config_entry.options
        store = self._config_entry.runtime_data.maintenance_store

        if key == "filter_remaining_hours":
            threshold = options.get(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.filter_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "brush_remaining_hours":
            threshold = options.get(CONF_BRUSH_HOURS, DEFAULT_BRUSH_HOURS)
            current_hr = self.run_stats.get("hr", 0)
            if store:
                return store.brush_remaining(current_hr, threshold)
            return max(0, threshold - current_hr)

        if key == "next_clean":
            return self._calc_next_clean()

        value = self.entity_description.value_fn(self)

        # F6b — cache battery retention value to RoombaData
        if key == "battery_capacity_retention":
            data = self._config_entry.runtime_data
            data.battery_retention_value = float(value) if value is not None else None

        # F6f — battery contact / bus-communication anomaly check, fed
        # directly from the same batPct value this sensor exposes (no
        # separate caching needed — the check function reads state itself).
        elif key == "battery":
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_battery_contact_issue
                self.hass.async_create_task(
                    async_check_battery_contact_issue(self.hass, self._config_entry),
                    name="roomba_plus_f6f_battery_contact_check",
                )

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose sensor-specific attributes used by the Lovelace card."""
        key = self.entity_description.key
        # v2.7.1: extra_attributes_fn takes priority over generic attribute logic
        extra_fn = self.entity_description.extra_attributes_fn
        if extra_fn is not None:
            return extra_fn(self)
        # v1.7.0 L2: threshold_hours for consumable remaining sensors
        threshold = self.entity_description.threshold_fn(self)
        if threshold is not None:
            return {"threshold_hours": threshold}
        # v1.8.0 L3: description + action for last_error_code
        # v3.4.1: localised via hass.config.language, falls back to English
        if key == "last_error_code":
            code = self.native_value
            if code is not None:
                catalogue = get_localized_error_entry(int(code), self.hass.config.language)
                return {
                    "description": catalogue.get("description", ""),
                    "action": catalogue.get("action", ""),
                }
        # v1.9.1: status hints for numeric sensors that show Unknown
        # native_value must stay None (HA requirement for MEASUREMENT sensors)
        # but extra attributes give the user context about why.
        if key in ("filter_wear_rate", "brush_wear_rate", "pad_wear_rate",
                   "filter_days_until_due", "brush_days_until_due", "pad_days_until_due"):
            if self.native_value is None:
                maint = self._config_entry.runtime_data.maintenance_store
                if maint is None:
                    return {"status": "Maintenance store not available"}
                reset_at = (
                    maint.filter_reset_at
                    if "filter" in key
                    else maint.brush_reset_at
                )
                if reset_at is None:
                    return {"status": "Press the replacement confirmation button to start tracking"}
                return {"status": "Collecting data — available after 3 days"}
        if key == "last_mission_duration":
            if self.native_value == 0 or self.native_value is None:
                return {"status": "No mission recorded yet"}
        # v2.1.2: expose raw counts so the card can render
        # "3 cleans · 2 opportunities" without back-calculating from %.
        # The sensor value itself stays uncapped (>100% is valid and useful
        # for automations that detect over-utilisation).
        if key == "presence_clean_utilisation_7d":
            store = self._config_entry.runtime_data.mission_store
            if store is not None:
                windows = store.presence_windows(7)
                opportunities = _presence_opportunities(self, 7) or 0
                cleans = sum(1 for w in windows if w.resulted_in_clean) if windows else 0
                return {
                    "cleans_7d": cleans,
                    "opportunities_7d": opportunities,
                }
        return {}

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        key = self.entity_description.key

        if key == "battery":
            return "batPct" in new_state
        if key in ("rssi", "snr", "signal_noise"):
            return "signal" in new_state
        if key == "ip_address":
            return "netinfo" in new_state
        if key in ("mission_start_time", "mission_elapsed_time",
                   "mission_recharge_time", "mission_expire_time"):
            return "cleanMissionStatus" in new_state
        if key in ("phase", "error", "readiness", "job_initiator",
                   "mission_recharge_minutes", "mission_expire_minutes",
                   "mission_id"):
            return "cleanMissionStatus" in new_state or "error" in new_state
        if key in ("clean_base_status", "dock_tank_level"):
            return "dock" in new_state
        if key in ("mop_pad", "mop_behavior", "mop_tank_level", "tank_level"):
            return any(k in new_state for k in ("detectedPad", "rankOverlap", "tankLvl"))
        if key in ("filter_remaining_hours", "brush_remaining_hours",
                   "scrubs_count", "total_cleaning_time",
                   "filter_last_replaced", "brush_last_replaced",
                   "pad_last_replaced", "battery_last_replaced"):
            # bbrun: 900-series source for hr/sqft; runtimeStats: i/s/j-series source.
            # Both must trigger updates so the merged run_stats property stays current.
            return "bbrun" in new_state or "runtimeStats" in new_state
        if key in ("total_missions", "successful_missions", "canceled_missions",
                   "failed_missions", "average_mission_time", "last_mission"):
            return "bbmssn" in new_state
        if key == "battery_cycles":
            return "bbchg3" in new_state
        if key in ("clean_mode", "carpet_boost_mode"):
            return any(k in new_state for k in
                       ("noAutoPasses", "twoPass", "carpetBoost", "vacHigh"))
        if key == "nav_quality":
            return "mssnNavStats" in new_state
        if key == "next_clean":
            return "cleanSchedule2" in new_state or "cleanSchedule" in new_state
        # v1.8.0 L1 — Mission log sensors
        if key in ("clean_streak", "missions_last_30d", "completion_rate_30d",
                   "area_cleaned_today", "last_mission_result", "last_mission_duration"):
            return "cleanMissionStatus" in new_state
        # v1.8.0 L3 — Error intelligence sensors
        if key in ("last_error_code", "last_error_at", "last_error_zone"):
            return "cleanMissionStatus" in new_state or "error" in new_state
        if key in ("stuck_count_30d", "problem_zone"):
            return "cleanMissionStatus" in new_state
        # v1.8.0 L6 — Presence analytics sensors
        if key in ("presence_clean_opportunities_7d", "presence_clean_utilisation_7d",
                   "next_likely_clean_window"):
            return "schedHold" in new_state or "cleanMissionStatus" in new_state
        # v1.9.0 Device Intelligence sensors
        if key in ("battery_capacity_mah",):
            return "bbchg3" in new_state
        if key in ("nav_panics", "cliff_events_front", "cliff_events_rear"):
            return "bbrun" in new_state
        # v1.9.0 L4 — Wear Intelligence sensors
        if key in ("filter_wear_rate", "brush_wear_rate", "pad_wear_rate",
                   "filter_days_until_due", "brush_days_until_due", "pad_days_until_due"):
            # bbrun: 900-series source for hr; runtimeStats: i/s/j-series source.
            # cleanMissionStatus: triggers recalc at mission end.
            return (
                "bbrun" in new_state
                or "runtimeStats" in new_state
                or "cleanMissionStatus" in new_state
            )

        return len(new_state) > 1 or "signal" not in new_state

    def _calc_next_clean(self) -> StateType:
        """Return next scheduled cleaning time as a timezone-aware datetime.

        v3.4.0 CAL — thin wrapper around schedule_parser.py's
        parse_schedule_occurrences(), extracted so calendar.py's
        RoombaScheduleCalendar can reuse the same parsing without
        importing this (deskriptor-heavy) module. Behaviour unchanged:
        strictly-future occurrences only, cleanSchedule2 preferred over
        legacy cleanSchedule — same precedence the pre-extraction code had.
        """
        now = dt_util.now()
        occurrences = parse_schedule_occurrences(
            self.vacuum_state, now, now + dt_stdlib.timedelta(weeks=2)
        )
        strictly_future = [start for start, _end in occurrences if start > now]
        return min(strictly_future) if strictly_future else None

    def _next_from_schedule2(self, entries: list) -> StateType:
        """Back-compat wrapper (v3.4.0 CAL extraction) — the actual
        per-format parsing now lives in schedule_parser.py, shared
        with calendar.py. Kept here, delegating, so existing call
        sites (and their tests) that ask "what's the single next
        occurrence from these raw cleanSchedule2 entries" keep working
        unchanged."""
        now = dt_util.now()
        occurrences = occurrences_from_schedule2(
            entries, now, now + dt_stdlib.timedelta(weeks=2)
        )
        future = [o for o in occurrences if o > now]
        return min(future) if future else None

    def _next_from_schedule_v1(self, schedule: dict) -> StateType:
        """Back-compat wrapper — see _next_from_schedule2()'s docstring."""
        now = dt_util.now()
        occurrences = occurrences_from_schedule_v1(
            schedule, now, now + dt_stdlib.timedelta(weeks=2)
        )
        future = [o for o in occurrences if o > now]
        return min(future) if future else None

