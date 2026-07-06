"""Cloud-derived sensors for the Roomba+ sensor platform.

SENSOR-SPLIT (v3.4.0): extracted from the former sensor.py monolith.
Everything here is derived from IrobotCloudCoordinator data: lifetime
cloud-history stats, raw-record-derived sensors, the consolidated
analytics family (_ConsolidatedCloudSensor), the archive-sourced
family (_ArchiveSensor), and the composite robot-health score + trend.
No behaviour change vs. v3.3.1.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.typing import StateType

from homeassistant.util import dt as dt_util

from .const import (
    SQFT_TO_M2,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .cloud_coordinator import IrobotCloudCoordinator
from .sensor_helpers import (
    _raw_wifi_floor,
    _raw_wifi_quality_pct,
    _raw_wifi_stability,
    _robot_health_plain_status,
)

_LOGGER = logging.getLogger("custom_components.roomba_plus.sensor")


@dataclass(frozen=True, kw_only=True)
class CloudHistorySensorDescription(SensorEntityDescription):
    """Description for a cloud-sourced history sensor."""
    value_fn: Callable[[dict[str, Any]], StateType]


def _mh_sqft_to_m2(history: dict[str, Any]) -> StateType:
    """Return recent cleaned area in m² summed across the API window (~30 missions).

    NOT a lifetime total. The iRobot cloud /missionhistory endpoint returns
    individual mission records; we sum sqft across the window. The true
    lifetime area comes from bbrun.sqft over local MQTT (sensor: total_cleaned_area).
    """
    sqft = (history.get("runtimeStats") or {}).get("sqft")
    if sqft is None:
        return None
    return round(sqft / 10.764, 1)


def _mh_total_minutes(history: dict[str, Any]) -> StateType:
    """Return recent cleaning time in minutes summed across the API window (~30 missions).

    NOT a lifetime total — same API limitation as _mh_sqft_to_m2.
    The true lifetime time comes from bbrun.hr/min over local MQTT.
    """
    stats = history.get("runtimeStats") or {}
    hr = stats.get("hr")
    mn = stats.get("min")
    if hr is None or mn is None:
        return None
    return hr * 60 + mn


def _mh_total_missions(history: dict[str, Any]) -> StateType:
    """Return lifetime mission count (true lifetime — nMssn is a robot-side counter)."""
    return (history.get("bbmssn") or {}).get("nMssn")


CLOUD_HISTORY_SENSORS: tuple[CloudHistorySensorDescription, ...] = (
    CloudHistorySensorDescription(
        key="recent_area_30d",
        translation_key="recent_area_30d",  # Locks entity_id slug to the key string,
        # independent of locale. translation_key uses the key value literally as the
        # slug — NOT the translated name string. This fixes fresh-install divergence
        # from migrated installs (card audit addendum, June 2026).
        name="Recent cleaned area (30 d)",
        native_unit_of_measurement="m²",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # SC1 (v2.7.0): disabled by default — superseded by cleaning_analytics_30d.
        entity_registry_enabled_default=False,
        value_fn=_mh_sqft_to_m2,
    ),
    CloudHistorySensorDescription(
        key="recent_time_30d",
        translation_key="recent_time_30d",  # Same fix — see recent_area_30d above.
        name="Recent cleaning time (30 d)",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        # SC1 (v2.7.0): disabled by default — superseded by cleaning_analytics_30d.
        entity_registry_enabled_default=False,
        value_fn=_mh_total_minutes,
    ),
    CloudHistorySensorDescription(
        key="lifetime_missions",
        translation_key="lifetime_missions",
        name="Lifetime missions",
        native_unit_of_measurement="missions",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mh_total_missions,
    ),
)


# ── v2.0 Cloud sensors from raw records ──────────────────────────────────────
#
# These sensors consume mission_history_raw — the per-mission list stored by
# the coordinator since v2.0. The window is the API fetch window (~30 days).
#
# Unlike the lifetime sensors above, these are window-relative: they reflect
# the last ~30 days, not all-time totals.

@dataclass(frozen=True, kw_only=True)
class CloudRawSensorDescription(SensorEntityDescription):
    """Description for a sensor reading from the raw per-mission record list."""
    value_fn: Callable[[list[dict[str, Any]]], StateType]
    attributes_fn: Callable[[list[dict[str, Any]]], dict[str, Any]] | None = None
    # When set, entity is unavailable (not unknown) when fn returns False.
    # Receives the CloudRawSensor entity so the lambda can access both
    # coordinator.raw_records and runtime_data (e.g. mission_store).
    available_fn: Callable[[Any], bool] | None = field(default=None)


# ── F5 — Performance intelligence (RoombaSensor + consolidated functions) ───
# SC1 (v3.0): _raw_dirt_density_attrs, _make_coverage_pct_fn removed — only
# used by the now-deactivated CLOUD_RAW_SENSORS descriptors.
# The functions below are KEPT because they are also used by the consolidated
# replacement sensors (cleaning_performance, event_counts_30d).

def _raw_completion_rate(records: list[dict[str, Any]]) -> StateType:
    """Return completion rate (%) across the API window records."""
    if not records:
        return None
    completed = sum(1 for r in records if r.get("done") == "done")
    return round(completed / len(records) * 100, 1)


def _raw_recharges(records: list[dict[str, Any]]) -> StateType:
    """Return total mid-mission recharges across the API window."""
    if not records:
        return None
    return sum(int(r.get("chrgs", 0) or 0) for r in records)


def _raw_evacuations(records: list[dict[str, Any]]) -> StateType:
    """Return total Clean Base evacuations across the API window."""
    if not records:
        return None
    return sum(int(r.get("evacs", 0) or 0) for r in records)


def _raw_dirt_events(records: list[dict[str, Any]]) -> StateType:
    """Return total dirt detection events across the API window."""
    if not records:
        return None
    return sum(int(r.get("dirt", 0) or 0) for r in records)


def _raw_cloud_last_error_code(records: list[dict[str, Any]]) -> StateType:
    """Return the pauseId from the most recent failed mission record."""
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            pause_id = int(r.get("pauseId", 0) or 0)
            return pause_id if pause_id > 0 else None
    return None


def _raw_cloud_last_error_time(records: list[dict[str, Any]]) -> StateType:
    """Return the end timestamp of the most recent failed mission as a datetime."""
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            ts = r.get("timestamp")
            if ts:
                import datetime
                return datetime.datetime.fromtimestamp(
                    int(ts), tz=datetime.timezone.utc
                )
    return None


def _raw_cloud_last_error_attrs(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return ERROR_CATALOGUE label + action for the most recent cloud error."""
    from .const import ERROR_CATALOGUE
    for r in records:
        classified = r.get("classified_result", "")
        if classified.startswith("error_") or classified == "stuck":
            pause_id = int(r.get("pauseId", 0) or 0)
            catalogue = ERROR_CATALOGUE.get(pause_id, {})
            return {
                "error_code": pause_id or None,
                "label": catalogue.get("label", ""),
                "description": catalogue.get("description", ""),
                "action": catalogue.get("action", ""),
                "source": "cloud_pauseId",
            }
    return {}


import statistics as _statistics


def _raw_cleaning_speed(records: list[dict]) -> StateType:
    """F5a — median cleaning speed (m²/min) across the API window.

    Cloud API returns sqft — converted to m² (× SQFT_TO_M2) for consistency
    with all other area sensors in Roomba+.
    Uses runM (clean time excl. recharge) preferred over durationM.
    Skips records missing either field or with zero time.
    """
    speeds = []
    for r in records:
        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            m2_per_min = float(sqft) * SQFT_TO_M2 / float(run_m)
            speeds.append(m2_per_min)
    if not speeds:
        return None
    return round(_statistics.median(speeds), 2)


def _raw_dirt_density(records: list[dict]) -> StateType:
    """F5b — median dirt events per m² across the API window.

    Cloud API returns sqft — converted to m² (÷ SQFT_TO_M2) for consistency.
    Rising values indicate a dirtier floor OR worn brushes (debris not
    captured, sensor re-fires).  The cause attribute distinguishes them.
    """
    densities = []
    for r in records:
        dirt = r.get("dirt")
        sqft = r.get("sqft")
        if dirt is not None and sqft and float(sqft) > 0:
            m2 = float(sqft) * SQFT_TO_M2
            densities.append(float(dirt) / m2)
    if not densities:
        return None
    return round(_statistics.median(densities), 3)


def _classify_dirt_cause(dirt_trend: str, speed_trend: str) -> str:
    """Classify the most probable cause of rising dirt density.

    F5b — 3-signal classification eliminating threshold-based guessing:
      brush_wear  — debris not captured, sensor re-fires (rising dirt + declining speed)
      floor_dirty — robot working harder but keeping up (rising dirt + stable/rising speed)
    """
    if dirt_trend == "rising" and speed_trend == "declining":
        return "brush_wear"
    if dirt_trend == "rising" and speed_trend in ("stable", "rising", "unknown"):
        return "floor_dirty"
    return "unknown"


def _raw_recharge_fraction(records: list[dict]) -> StateType:
    """F5c — median recharge fraction (chrgM / durationM) across window.

    Uses the cloud `chrgM` field (minutes recharging mid-mission) divided by
    `durationM` (total mission minutes), expressed as a percentage.

    Note: The `recharge_min` key from F4e (local MissionStore accumulation) is
    structurally read as a fallback, but `raw_records` are cloud-only and will
    never contain it at runtime. The fallback is retained for future use if
    local records are ever merged into `raw_records` at the coordinator level.
    Rising values indicate battery degradation or a home too large for one charge.
    """
    fractions = []
    for r in records:
        chrg_m = r.get("chrgM") or r.get("recharge_min")
        dur_m  = r.get("durationM") or r.get("duration_min")
        if chrg_m is not None and dur_m and float(dur_m) > 0:
            fractions.append(float(chrg_m) / float(dur_m) * 100)
    if not fractions:
        return None
    return round(_statistics.median(fractions), 1)


def _raw_cleaning_speed_trend(records: list[dict]) -> StateType:
    """F5e — cleaning speed trend: improving / stable / declining / unknown.

    Compares median of 5 most-recent vs previous 10 records.
    Gap filter: excludes the first 3 missions after a >7-day gap — they are
    catching-up cleans on an abnormally dirty floor and would produce false
    'declining' signals.

    Records must be newest-first (cloud API order). Local MissionStore records
    are oldest-first — do not pass them directly to this function.
    """
    # Build speed series newest-first (records are already newest-first)
    filtered = []
    prev_ts: float | None = None
    skip_remaining = 0

    for r in records:
        ts_raw = r.get("startTime") or r.get("timestamp")
        ts = float(ts_raw) if ts_raw else None

        if prev_ts is not None and ts is not None:
            gap_days = (prev_ts - ts) / 86400
            if gap_days > 7:
                skip_remaining = 3  # skip next 3 after the gap

        if skip_remaining > 0:
            skip_remaining -= 1
            if ts is not None:
                prev_ts = ts
            continue

        sqft = r.get("sqft")
        run_m = r.get("runM") or r.get("durationM")
        if sqft is not None and run_m and float(run_m) > 0:
            filtered.append(float(sqft) / float(run_m))

        if ts is not None:
            prev_ts = ts

    if len(filtered) < 6:
        return "unknown"

    recent = _statistics.median(filtered[:5])
    older  = _statistics.median(filtered[5:min(15, len(filtered))])
    if older == 0:
        return "unknown"
    delta = (recent - older) / older
    if delta > 0.10:
        return "improving"
    if delta < -0.10:
        return "declining"
    return "stable"


class CloudRawSensor(IRobotEntity, SensorEntity):
    """Sensor reading per-mission stats from the iRobot cloud raw record list.

    Reads from coordinator.raw_records — the per-mission list stored since v2.0.
    Updates whenever the coordinator refreshes (daily poll or map-retrain trigger).

    Available for all robots with cloud credentials (EPHEMERAL + SMART).

    SC1 (v2.7.0): these individual sensors are deprecated and disabled by default
    on fresh installs. Use the consolidated sensors instead:
      sensor.*_cleaning_performance, *_cleaning_analytics_30d,
      *_wifi_health, *_event_counts_30d.
    They will be removed in v3.0.
    """

    entity_description: CloudRawSensorDescription
    # SC1 (v2.7.0): tracks which keys have already logged a deprecation warning
    # this session. Class-level so it persists across sensor instances.
    _sc1_warned: ClassVar[set[str]] = set()

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        description: CloudRawSensorDescription,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_{description.key}"
        # Lock entity_id to the description key so it is locale-independent.

    @property
    def native_value(self) -> StateType:
        # SC1 (v2.7.0): log a one-shot deprecation warning per key per session.
        key = self.entity_description.key
        if key not in CloudRawSensor._sc1_warned:
            _LOGGER.warning(
                "Roomba+ sensor '%s' is deprecated (SC1, v2.7.0) and will be "
                "removed in v3.0. Use sensor.*_cleaning_performance, "
                "*_cleaning_analytics_30d, *_wifi_health, or *_event_counts_30d "
                "instead. Disable this sensor in HA to suppress this warning.",
                key,
            )
            CloudRawSensor._sc1_warned.add(key)
        value = self.entity_description.value_fn(self._coordinator.raw_records)
        # F6a/F6b — cache values to RoombaData for repair check functions
        data = self._config_entry.runtime_data
        if key == "cleaning_speed_trend":
            data.cleaning_speed_trend_value = str(value) if value else None
            # F6a — trigger performance degradation check on every trend update
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_performance_degradation
                self.hass.async_create_task(
                    async_check_performance_degradation(self.hass, self._config_entry),
                    name="roomba_plus_f6a_perf_check",
                )
        elif key == "recent_recharge_fraction":
            data.recharge_fraction_value = float(value) if value is not None else None
            # F6b — check battery/recharge correlation
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_battery_recharge
                self.hass.async_create_task(
                    async_check_battery_recharge(self.hass, self._config_entry),
                    name="roomba_plus_f6b_battery_check",
                )
        elif key == "recent_dirt_density":
            # Update dirt_density_rising flag for F6a cause classification
            records = self._coordinator.raw_records
            if records and len(records) >= 6:
                import statistics as _stat
                densities = [
                    float(r["dirt"]) / float(r["sqft"])
                    for r in records
                    if r.get("dirt") is not None and r.get("sqft") and float(r["sqft"]) > 0
                ]
                if len(densities) >= 6:
                    recent = _stat.median(densities[:5])
                    older  = _stat.median(densities[5:])
                    data.dirt_density_rising = (recent / older > 1.10) if older > 0 else False
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.entity_description.attributes_fn is None:
            return {}
        attrs = dict(self.entity_description.attributes_fn(self._coordinator.raw_records))
        # L5 (v2.6.0): add by_room dirtiness to recent_dirt_density sensor
        if self.entity_description.key == "recent_dirt_density":
            rps = getattr(self._config_entry.runtime_data, "robot_profile_store", None)
            if rps is not None:
                rel = rps.room_dirt_relative()
                if rel:
                    attrs["by_room"] = {rid: round(v, 3) for rid, v in rel.items()}
                # v3.3.0 DIRT-VEL — per-room accumulation velocity
                # ((passCount/m²)/day, EMA-smoothed). Present only once a
                # room has two sufficiently spaced cleanings.
                vel = rps.dirt_accumulation_rate()
                if vel:
                    attrs["by_room_velocity"] = vel
        return attrs

    @property
    def available(self) -> bool:
        # R3: return False when cloud is not configured so HA shows "Unavailable"
        # rather than showing None state with available=True. Distinguishes
        # "cloud not configured" from "coordinator not yet updated".
        if not self._config_entry.runtime_data.has_cloud:
            return False
        if not (self._coordinator.last_update_success
                and self._coordinator.data is not None):
            return False
        # available_fn: mark unavailable when insufficient data (not unknown).
        if self.entity_description.available_fn is not None:
            if not self.entity_description.available_fn(self):
                return False
        return True

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()
            # F6f -- trigger accident detection on each cloud poll,
            # but only from the dirt density sensor to avoid 6x redundant calls.
            if (
                self.entity_description.key == "recent_dirt_density"
                and self.hass.is_running
            ):
                from .repairs import async_check_accident_detection
                self.hass.async_create_task(
                    async_check_accident_detection(
                        self.hass,
                        self._config_entry,
                        self._coordinator.raw_records,
                    ),
                    name="roomba_plus_f6f_accident_check",
                )

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


class CloudHistorySensor(IRobotEntity, SensorEntity):
    """Sensor reading lifetime stats from the iRobot cloud mission history.

    Unlike RoombaSensor (which is driven by MQTT push), this sensor reads
    from the cloud coordinator cache. It updates whenever the coordinator
    refreshes (daily poll or map-retrain trigger) — not on every MQTT message.

    Available for all robots when cloud credentials are configured,
    including the 980 which does not expose lifetime stats over local MQTT.
    """

    entity_description: CloudHistorySensorDescription

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        description: CloudHistorySensorDescription,
    ) -> None:
        super().__init__(roomba, blid)
        self.entity_description = description
        self._coordinator = coordinator
        self._attr_unique_id = f"{self.robot_unique_id}_cloud_{description.key}"
        # Lock entity_id to the description key so it is locale-independent.
        # Without this, HA derives entity_id from the translated name, which
        # produces German slugs (e.g. gereinigte_flache_30_t) on DE installs.

    @property
    def native_value(self) -> StateType:
        if not self._coordinator.data:
            return None
        history = self._coordinator.data.get("mission_history", {})
        return self.entity_description.value_fn(history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose data source context for the cloud history sensors.

        lifetime_missions reads nMssn from each record — a true lifetime
        counter embedded by the robot in every mission entry.

        recent_area_30d and recent_time_30d are aggregated from the API
        window (the last ~30 missions), not true lifetime totals. The true
        lifetime area and time come from bbrun over local MQTT.
        """
        key = self.entity_description.key
        if key == "lifetime_missions":
            return {"source": "lifetime_counter_from_robot"}
        if key in ("recent_area_30d", "recent_time_30d"):
            return {
                "source": "recent_mission_window",
                "note": "Aggregated from recent mission history (~30 missions). "
                        "Not a lifetime total — use the local MQTT sensor "
                        "'Lifetime cleaned area' for the true value.",
            }
        return {}

    @property
    def available(self) -> bool:
        return (
            self._coordinator.last_update_success
            and self._coordinator.data is not None
        )

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Cloud sensor does not update from MQTT — coordinator handles refresh.
        return False

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )
class _ConsolidatedCloudSensor(IRobotEntity, SensorEntity):
    """Base for SC1 consolidated sensors — wires coordinator listener."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(self._coordinator.async_add_listener(_on_coordinator_update))

    @property
    def available(self) -> bool:
        return (
            self._config_entry.runtime_data.has_cloud
            and self._coordinator.last_update_success
            and self._coordinator.data is not None
        )

    def _cache_and_check_f6a(self, trend_value: str | None) -> None:
        """Cache cleaning_speed_trend_value; schedule F6a check only on change.

        B1/B2 (v3.0.0): migrated from the deactivated
        CloudRawSensor(key="cleaning_speed_trend").native_value side-effect.
        extra_state_attributes may be evaluated multiple times per state write,
        so the F6a performance-degradation check is scheduled only when the
        cached trend actually changes — keeping repeated reads side-effect-free.
        """
        data = self._config_entry.runtime_data
        changed = data.cleaning_speed_trend_value != trend_value
        data.cleaning_speed_trend_value = trend_value
        if changed and hasattr(self.hass, "is_running") and self.hass.is_running:
            from .repairs import async_check_performance_degradation
            self.hass.async_create_task(
                async_check_performance_degradation(self.hass, self._config_entry),
                name="roomba_plus_f6a_perf_check",
            )

    def _cache_and_check_f6b(
        self, recharge_value: float | None, dirt_rising: bool | None
    ) -> None:
        """Cache recharge_fraction_value + dirt_density_rising; F6b check on change.

        B1/B2 (v3.0.0): migrated from the deactivated CloudRawSensor side-effects
        (keys "recent_recharge_fraction" and "recent_dirt_density"). The F6b
        battery-recharge check is scheduled only when the recharge fraction
        changes, avoiding duplicate tasks on repeated property reads.
        """
        data = self._config_entry.runtime_data
        changed = data.recharge_fraction_value != recharge_value
        data.recharge_fraction_value = recharge_value
        if dirt_rising is not None:
            data.dirt_density_rising = dirt_rising
        if changed and hasattr(self.hass, "is_running") and self.hass.is_running:
            from .repairs import async_check_battery_recharge
            self.hass.async_create_task(
                async_check_battery_recharge(self.hass, self._config_entry),
                name="roomba_plus_f6b_battery_check",
            )


class RoombaCleaningPerformanceSensor(_ConsolidatedCloudSensor):
    """SC1 — Cleaning performance: completion rate + speed + trend + streak.

    State: completion rate (%) over the cloud API window.
    Attributes: speed_m2_per_min, coverage_pct, trend, clean_streak.

    Replaces: recent_completion_rate, recent_cleaning_speed,
              recent_coverage_pct, cleaning_speed_trend.
    """

    entity_description = SensorEntityDescription(
        key="cleaning_performance",
        name="Cleaning performance",
        translation_key="cleaning_performance",
    )
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_cleaning_performance"

    @property
    def native_value(self) -> StateType:
        return _raw_completion_rate(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        records = self._coordinator.raw_records
        attrs: dict[str, Any] = {}
        if records:
            speed = _raw_cleaning_speed(records)
            if speed is not None:
                attrs["speed_m2_per_min"] = speed
            trend = _raw_cleaning_speed_trend(records)
            if trend is not None:
                attrs["trend"] = trend
            # B1/B2-PRE — cache cleaning_speed_trend_value for F6a Repair and
            # RobotHealthSensor Signal 3.  Migrated from the now-deactivated
            # CloudRawSensor(key="cleaning_speed_trend") side-effect.
            # Idempotent: the F6a check is scheduled only when the trend changes.
            self._cache_and_check_f6a(
                str(trend) if trend is not None else None
            )
            # Coverage: most-recent sqft vs 60-day p75 baseline
            ms = self._config_entry.runtime_data.mission_store
            if ms is not None:
                recent_sqft = next(
                    (r["sqft"] for r in records if r.get("sqft") and r["sqft"] > 0), None
                )
                p75 = ms.p75_area(60)
                if recent_sqft is not None and p75 and p75 > 0:
                    attrs["coverage_pct"] = round(float(recent_sqft) / p75 * 100, 1)
            # Clean streak from local MissionStore
            if ms is not None:
                streak = ms.clean_streak()
                if streak is not None:
                    attrs["clean_streak"] = streak
        return attrs


class RoombaCleaningAnalytics30dSensor(_ConsolidatedCloudSensor):
    """SC1 — Cleaning analytics: area + time + dirt density + recharge fraction.

    State: cleaned area in m² over the cloud API window (~30 missions).
    Attributes: time_h, dirt_density, recharge_pct.

    Replaces: recent_area_30d, recent_time_30d, recent_dirt_density,
              recent_recharge_fraction.
    """

    entity_description = SensorEntityDescription(
        key="cleaning_analytics_30d",
        name="Cleaning analytics (30 d)",
        translation_key="cleaning_analytics_30d",
    )
    _attr_native_unit_of_measurement = "m²"
    _attr_device_class = SensorDeviceClass.AREA
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_cleaning_analytics_30d"

    @property
    def native_value(self) -> StateType:
        sqft = (self._coordinator.data.get("runtimeStats") or {}).get("sqft")
        if sqft is None:
            return None
        return round(float(sqft) * SQFT_TO_M2, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        stats = (self._coordinator.data.get("runtimeStats") or {})
        hr = stats.get("hr")
        mn = stats.get("min")
        if hr is not None and mn is not None:
            attrs["time_h"] = round((hr * 60 + mn) / 60, 1)
        records = self._coordinator.raw_records
        if records:
            dd = _raw_dirt_density(records)
            if dd is not None:
                attrs["dirt_density"] = dd
            rf = _raw_recharge_fraction(records)
            if rf is not None:
                attrs["recharge_pct"] = rf
            # B1/B2-PRE — cache recharge_fraction_value + dirt_density_rising for
            # F6b Repair and F6a cause classification.  Migrated from the now-
            # deactivated CloudRawSensor side-effects (keys "recent_recharge_fraction"
            # and "recent_dirt_density").  Idempotent: F6b check only on change.
            dirt_rising: bool | None = None
            if len(records) >= 6:
                densities = [
                    float(r["dirt"]) / float(r["sqft"])
                    for r in records
                    if r.get("dirt") is not None and r.get("sqft") and float(r["sqft"]) > 0
                ]
                if len(densities) >= 6:
                    recent = _statistics.median(densities[:5])
                    older  = _statistics.median(densities[5:])
                    dirt_rising = (recent / older > 1.10) if older > 0 else False
            self._cache_and_check_f6b(
                float(rf) if rf is not None else None,
                dirt_rising,
            )
        return attrs


class RoombaWifiHealthSensor(_ConsolidatedCloudSensor):
    """SC1 — Wi-Fi health: average signal quality + stability + worst dip.

    State: average WiFi signal quality (%) across the cloud API window —
    a weighted mean over each mission's full wlBars histogram distribution,
    not just whether the weakest bucket was ever touched (v2.9.0 fix; see
    _raw_wifi_quality_pct docstring for the full rationale — the previous
    implementation returned a raw 0-4 bucket index from a single mission
    mislabelled as a percentage, so a single brief signal dip could read
    as "0%" even with an otherwise excellent connection).
    Attributes: stability_pct, weakest_bucket_observed (0-4, the original
    "floor" diagnostic — still useful for spotting a dead zone, just not
    as the misleading primary percentage).

    Replaces: recent_wifi_floor, recent_wifi_stability.
    """

    entity_description = SensorEntityDescription(
        key="wifi_health",
        name="Wi-Fi health",
        translation_key="wifi_health",
    )
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_health"

    @property
    def native_value(self) -> StateType:
        return _raw_wifi_quality_pct(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        stab = _raw_wifi_stability(self._coordinator.raw_records)
        if stab is not None:
            attrs["stability_pct"] = stab
        floor = _raw_wifi_floor(self._coordinator.raw_records)
        if floor is not None:
            attrs["weakest_bucket_observed"] = floor
        return attrs


class RoombaEventCounts30dSensor(_ConsolidatedCloudSensor):
    """SC1 — Event counts: recharges + evacuations + dirt events + last error.

    State: most recent error code (int | None).
    Attributes: recharges, evacuations, dirt_events, error_time, error_label.

    Replaces: recent_recharges, recent_evacuations, recent_dirt_events,
              recent_error_code, recent_error_time.
    """

    entity_description = SensorEntityDescription(
        key="event_counts_30d",
        name="Event counts (30 d)",
        translation_key="event_counts_30d",
    )

    def __init__(self, roomba: Any, blid: str, coordinator: Any, config_entry: Any) -> None:
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_event_counts_30d"

    @property
    def native_value(self) -> StateType:
        return _raw_cloud_last_error_code(self._coordinator.raw_records)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        records = self._coordinator.raw_records
        attrs: dict[str, Any] = {}
        if not records:
            return attrs
        recharges = _raw_recharges(records)
        if recharges is not None:
            attrs["recharges"] = recharges
        evacs = _raw_evacuations(records)
        if evacs is not None:
            attrs["evacuations"] = evacs
        dirt = _raw_dirt_events(records)
        if dirt is not None:
            attrs["dirt_events"] = dirt
        err_time = _raw_cloud_last_error_time(records)
        if err_time is not None:
            attrs["error_time"] = err_time.isoformat()
        err_attrs = _raw_cloud_last_error_attrs(records)
        if err_attrs.get("label"):
            attrs["error_label"] = err_attrs["label"]
        return attrs


# ── L8 (v2.7.0) — Composite robot health score ────────────────────────────────

class RoombaRobotHealthSensor(IRobotEntity, SensorEntity):
    """L8 — single 0–100 health score combining all learned robot signals.

    Not DIAGNOSTIC — this is the number a non-technical user checks first.
    State is None (Unknown) until ≥20 missions have been recorded and at
    least 3 of the 5 component signals are available.

    Updates on cloud coordinator refresh (which triggers after every mission
    end via the F4b cloud-refresh hook).
    """

    entity_description = SensorEntityDescription(
        key="robot_health_score",
        name="Robot health score",
        translation_key="robot_health_score",
    )
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = None   # Main entity — deliberate per L8 spec

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_robot_health_score"

    def _score_and_breakdown(self) -> tuple[float | None, dict[str, Any]]:
        """Compute (score, breakdown) once; shared by native_value and
        extra_state_attributes so the underlying signals aren't recomputed
        twice per state update.
        """
        data = self._config_entry.runtime_data
        ms  = data.mission_store
        rps = getattr(data, "robot_profile_store", None)

        if ms is None or rps is None:
            return None, {}

        # Calibration gate: ≥20 missions needed for meaningful statistics
        records_30d = ms.query(30)
        if len(records_30d) < 20:
            return None, {}

        # Signal 1: battery retention (cached from battery retention sensor)
        bat_retention = data.battery_retention_value

        # Signal 2: navigation efficiency — current edge ratio vs stored baseline
        nav_ratio: float | None = None
        gs = data.grid_store
        if rps.coverage_baseline_ready and rps.coverage_baseline and gs is not None:
            current_ratio = gs.edge_coverage_ratio()
            if current_ratio is not None and rps.coverage_baseline > 0:
                nav_ratio = current_ratio / rps.coverage_baseline

        # Signal 3: cleaning speed trend (cached from CloudRawSensor)
        trend = data.cleaning_speed_trend_value

        # Signal 4: consecutive anomalous missions (L3)
        consecutive_anom = ms.consecutive_anomalous

        # Signal 5: stuck rate in last 30d — all three stuck variants count
        from .mission_store import MissionStore as _MS
        stuck_count = sum(
            1 for r in records_30d
            if r.get("result") in _MS.STUCK_RESULTS
        )
        stuck_rate = stuck_count / len(records_30d) if records_30d else None

        score, breakdown = rps.compute_health_score(
            battery_retention_pct=bat_retention,
            nav_efficiency_ratio=nav_ratio,
            cleaning_speed_trend=trend,
            consecutive_anomalous=consecutive_anom,
            stuck_rate_30d=stuck_rate,
        )

        # v3.2.0 L10 — snapshot into the rolling history whenever a real
        # score is available. record_health_score() is idempotent per
        # calendar day, so calling this from both native_value and
        # extra_state_attributes reads on the same day is harmless.
        if score is not None:
            rps.record_health_score(score, dt_util.now().date().isoformat())

        return score, breakdown

    @property
    def native_value(self) -> StateType:
        score, _ = self._score_and_breakdown()
        return score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """v3.1.0 PLAIN-STATUS — status_text/recommendation derived from the
        breakdown's weakest_signal, translated via _ROBOT_HEALTH_STATUS_MAP.
        """
        _, breakdown = self._score_and_breakdown()
        if not breakdown:
            return {}
        status_text, recommendation = _robot_health_plain_status(self.hass, breakdown)
        return {
            **breakdown,
            "status_text": status_text,
            "recommendation": recommendation,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )


class RoombaHealthScoreTrendSensor(IRobotEntity, SensorEntity):
    """L10 (v3.2.0) — self-calibrating trend classification of the L8
    health score, backed by RobotProfileStore.health_score_history.

    State is 'improving' / 'stable' / 'declining', or None until this
    robot's own baseline is established (>=14 days of recorded scores —
    RobotProfileStore.health_score_baseline_ready). Trend is judged against
    this robot's own learned mean/stdev, not a fixed point-difference
    threshold — see health_score_trend()'s docstring for why.

    Same update source as L8 itself (cloud coordinator refresh after every
    mission end) — the trend has nothing new to say between mission ends.
    """

    entity_description = SensorEntityDescription(
        key="health_score_trend",
        name="Health score trend",
        translation_key="health_score_trend",
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        roomba: Any,
        blid: str,
        coordinator: IrobotCloudCoordinator,
        config_entry: RoombaConfigEntry,
    ) -> None:
        super().__init__(roomba, blid)
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_health_score_trend"

    @property
    def _rps(self) -> Any | None:
        return getattr(self._config_entry.runtime_data, "robot_profile_store", None)

    @property
    def native_value(self) -> StateType:
        rps = self._rps
        if rps is None:
            return None
        return rps.health_score_trend()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rps = self._rps
        if rps is None:
            return {}
        from .robot_profile_store import (
            _HEALTH_SCORE_REFERENCE_EXCLUSION_DAYS,
            _HEALTH_SCORE_BASELINE_MIN_DAYS,
        )
        days_recorded = len(rps.health_score_history)
        min_days_needed = _HEALTH_SCORE_REFERENCE_EXCLUSION_DAYS + _HEALTH_SCORE_BASELINE_MIN_DAYS
        return {
            "baseline_ready": rps.health_score_baseline_ready,
            "days_recorded": days_recorded,
            # v3.2.0 UX fix — days_recorded alone requires the user to
            # already know the 44-day threshold from documentation and
            # do the subtraction themselves. Explicit here instead.
            "days_until_ready": max(0, min_days_needed - days_recorded),
            "baseline_score": (
                round(rps.health_score_baseline, 1)
                if rps.health_score_baseline is not None else None
            ),
            "declining_days": rps.health_score_declining_days(),
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return False  # updated by cloud coordinator only

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _on_coordinator_update() -> None:
            self.async_write_ha_state()
            if hasattr(self.hass, "is_running") and self.hass.is_running:
                from .repairs import async_check_health_trend_declining
                self.hass.async_create_task(
                    async_check_health_trend_declining(self.hass, self._config_entry),
                    name="roomba_plus_l10_health_trend_check",
                )

        self.async_on_remove(
            self._coordinator.async_add_listener(_on_coordinator_update)
        )
def _channel_to_band(channel: int | None) -> str | None:
    """Derive WiFi band string from 802.11 channel number."""
    if channel is None:
        return None
    if 1 <= channel <= 13:
        return "2.4 GHz"
    if 36 <= channel <= 177:
        return "5 GHz"
    return None


class _ArchiveSensor(_ConsolidatedCloudSensor):
    """Base for sensors reading from MissionArchive (BAT-ARCH v2.8.0).

    Available when the archive has >=5 records and cloud coordinator is live.
    """

    @property
    def _archive(self):
        return getattr(self._config_entry.runtime_data, "mission_archive", None)

    @property
    def available(self) -> bool:
        arc = self._archive
        return (
            super().available
            and arc is not None
            and arc.record_count >= 5
        )


class RoombaWifiLastChannelSensor(_ArchiveSensor):
    """BAT-ARCH -- Most recent WiFi channel from archive Layer 1."""

    entity_description = SensorEntityDescription(
        key="wifi_last_channel",
        name="Wi-Fi last channel",
        translation_key="wifi_last_channel",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_last_channel"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        latest = arc.latest_derived(1)
        return latest[0].get("wifi_channel") if latest else None

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        latest = arc.latest_derived(1)
        if not latest:
            return {}
        band = _channel_to_band(latest[0].get("wifi_channel"))
        return {"band": band} if band else {}


class RoombaWifiChannelStabilitySensor(_ArchiveSensor):
    """BAT-ARCH -- % of last 30 missions on dominant WiFi channel."""

    entity_description = SensorEntityDescription(
        key="wifi_channel_stability",
        name="Wi-Fi channel stability",
        translation_key="wifi_channel_stability",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_wifi_channel_stability"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        series = arc.wifi_channel_series(30)
        if not series:
            return None
        dominant_count = Counter(series).most_common(1)[0][1]
        return round(dominant_count / len(series) * 100, 1)

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        series = arc.wifi_channel_series(30)
        if not series:
            return {}
        dominant_ch, dominant_count = Counter(series).most_common(1)[0]
        return {
            "dominant_channel": dominant_ch,
            "dominant_channel_band": _channel_to_band(dominant_ch),
            "sample_count": len(series),
        }


class RoombaMissionsPerChargeSensor(_ArchiveSensor):
    """BAT-ARCH -- Avg missions per charge cycle (last 30 days).

    State: total_missions / (1 + total_mid_mission_recharges). Higher = healthier.
    """

    entity_description = SensorEntityDescription(
        key="missions_per_charge",
        name="Missions per charge",
        translation_key="missions_per_charge",
        entity_registry_enabled_default=False,
    )
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba, blid, coordinator, config_entry):
        super().__init__(roomba, blid, coordinator, config_entry)
        self._attr_unique_id = f"{self.robot_unique_id}_missions_per_charge"

    @property
    def native_value(self):
        arc = self._archive
        if arc is None:
            return None
        recent = arc.recent_derived(30)
        if not recent:
            return None
        total_recharges = sum(int(r.get("recharge_count") or 0) for r in recent)
        return round(len(recent) / max(1, 1 + total_recharges), 2)

    @property
    def extra_state_attributes(self):
        arc = self._archive
        if arc is None:
            return {}
        recent = arc.recent_derived(30)
        if not recent:
            return {}
        total = len(recent)
        total_recharges = sum(int(r.get("recharge_count") or 0) for r in recent)
        no_recharge = sum(1 for r in recent if not r.get("recharge_count"))
        return {
            "missions_30d": total,
            "mid_mission_recharges_30d": total_recharges,
            "single_charge_pct": round(no_recharge / total * 100, 1) if total else None,
        }
