"""Diagnostic and meta sensors for the Roomba+ sensor platform.

SENSOR-SPLIT (v3.4.0): extracted from the former sensor.py monolith.
Universal, always-created diagnostic sensors: raw MQTT state,
optimal-clean-window (presence scheduling), firmware version,
integration health meta-sensor, and reset-cause breakdown. No
behaviour change vs. v3.3.1.
"""
from __future__ import annotations

from typing import Any
import datetime as dt_stdlib

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    EVENT_HEALTH_CHANGE,
    INTEGRATION_HEALTH_TICK_SECONDS,
)
from .entity import IRobotEntity
from .models import RoombaConfigEntry
from .sensor_helpers import (
    _compute_integration_health,
    _health_band,
    _integration_health_plain_status,
)


class RawStateSensor(IRobotEntity, SensorEntity):
    """Opt-in sensor that exposes the full MQTT state as extra_state_attributes.

    The sensor state value is a simple count of top-level keys in the reported
    state — useful as a change indicator. All actual data lives in attributes.

    Disabled by default — must be explicitly enabled in the HA UI.
    Intended for power users and debugging unknown robot models.
    """

    entity_description = SensorEntityDescription(
        key="raw_state",
        name="Raw MQTT state",
        translation_key="raw_state",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_raw_state"

    @property
    def native_value(self) -> int:
        """Return count of top-level reported state keys."""
        return len(self.vacuum_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full reported state as attributes.

        Complex nested values (dicts, lists) are JSON-serialised to strings
        so that HA's attribute storage never receives un-serialisable objects.
        All values are HA-safe primitives after this conversion.
        """
        import json as _json
        result: dict[str, Any] = {}
        for key, value in self.vacuum_state.items():
            if isinstance(value, (dict, list)):
                try:
                    result[key] = _json.dumps(value, default=str)
                except Exception:  # noqa: BLE001
                    result[key] = str(value)
            else:
                result[key] = value
        return result

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Update on any MQTT message — this is a catch-all debug sensor
        return True


# ── F12a — Optimal Clean Window sensor ────────────────────────────────────────

class RoombaOptimalCleanWindow(IRobotEntity, SensorEntity):
    """Timestamp sensor showing the optimal time to clean today.

    F12a (v2.4.0) — derived from PresenceManager.preferred_window(), which
    builds a day×hour reliability matrix from historical clean events.

    State: ISO-8601 datetime of the next occurrence of the best window today.
    None (unavailable) when fewer than 5 historical clean events exist.

    device_class: TIMESTAMP — HA renders as a relative time widget.
    entity_category: DIAGNOSTIC — opt-in; not shown in default dashboard view.
    """

    entity_description = SensorEntityDescription(
        key="optimal_clean_window",
        name="Optimal clean window",
        translation_key="optimal_clean_window",
    )

    _attr_entity_category = None  # reclassified: main entity (v2.6.0)
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = None  # timestamp sensors have no state_class

    def __init__(self, roomba: Any, blid: str, config_entry: Any) -> None:
        super().__init__(roomba, blid)
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_optimal_clean_window"

    @property
    def native_value(self) -> Any:
        """Return the next datetime for the preferred cleaning window."""
        import datetime as _dt
        pm = self._config_entry.runtime_data.presence_manager
        if pm is None:
            return None
        slot = pm.preferred_window()
        if slot is None:
            return None
        _, hour = slot
        # Build a datetime for the next occurrence of this hour today (or tomorrow
        # if the hour has already passed today)
        now = _dt.datetime.now(_dt.timezone.utc).astimezone()
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += _dt.timedelta(days=1)
        return candidate

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full presence_windows matrix as attributes."""
        pm = self._config_entry.runtime_data.presence_manager
        if pm is None:
            return {}
        windows = pm.presence_windows()
        # Serialise (weekday, hour) tuples as "wd_hr" strings for JSON
        return {
            "windows": {
                f"{wd}_{hr}": round(score, 3)
                for (wd, hr), score in windows.items()
            },
            "preferred_slot": pm.preferred_window(),
            # ALG2 (v2.6.0): True when the best window is today, so cards and
            # automations can distinguish "clean now" from "clean tomorrow".
            "window_is_today": pm.window_is_today,
        }

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        # Sensor is not MQTT-driven — update on demand only
        return False
class RoombaFirmwareVersionSensor(IRobotEntity, SensorEntity):
    """FW-SENSOR (v2.8.3) — robot firmware version string.

    Reads `softwareVer` from the live MQTT state.  Present on all robot
    families (9-series, i/s/j-series, Braava m6).  Stays at its last-known
    value when offline — sensor is available whenever MQTT is connected.

    Paired with RoombaFirmwareUpdated (binary_sensor.*_firmware_updated) which
    turns ON for 24 h after a version change is detected.
    """

    entity_description = SensorEntityDescription(
        key="firmware_version",
        name="Firmware version",
        translation_key="firmware_version",
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_firmware_version"

    @property
    def native_value(self) -> str | None:
        """Return the firmware version string from softwareVer."""
        return self.vacuum_state.get("softwareVer")

    def new_state_filter(self, new_state: dict[str, Any]) -> bool:
        return "softwareVer" in new_state


class RoombaIntegrationHealthSensor(IRobotEntity, SensorEntity):
    """INTEG-HEALTH (v2.9.0) — integration health meta-sensor (0-100).

    Combines three signals into one diagnostic score: active Repair Issue
    count, MQTT message age, and ARC1 (MissionArchive) freshness. See
    _compute_integration_health()'s docstring for the exact formula and
    why two originally-planned signals (cloud age, "last store save")
    were folded in or dropped.

    Score is computed on every poll (cheap — no I/O, just registry/store
    reads already held in memory) AND on a 60-second periodic tick, which
    additionally fires/clears the integration_health Repair Issue when the
    score has been below INTEGRATION_HEALTH_LOW_THRESHOLD (50) for at
    least INTEGRATION_HEALTH_SUSTAINED_MINUTES (30 min) — a single bad
    reading should not alarm the user; a sustained one should.

    v2.9.0 EVENT-BUS: the same 60-second tick also fires
    roomba_plus_health_change, but only on BAND-crossing (healthy/degraded/
    critical — see _health_band()), not on every score recompute. Deliberately
    NOT done in native_value, since that property is read on every poll
    (including polls triggered by other entities/HA internals) and would
    fire far more often than the score meaningfully changes.
    """

    entity_description = SensorEntityDescription(
        key="integration_health",
        name="Integration health",
        translation_key="integration_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, roomba: Any, blid: str, config_entry: RoombaConfigEntry) -> None:
        super().__init__(roomba, blid)
        self._entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_integration_health"
        self._unsub_tick: Any | None = None
        # v2.9.0 EVENT-BUS — None until the first tick so the very first
        # evaluation never fires a "change" (there is no prior band yet).
        self._last_health_band: str | None = None
        self._last_health_score: int | None = None

    async def async_added_to_hass(self) -> None:
        """Start the 60-second periodic tick that drives the Repair Issue."""
        await super().async_added_to_hass()
        self._unsub_tick = async_track_time_interval(
            self.hass,
            self._async_health_tick,
            dt_stdlib.timedelta(seconds=INTEGRATION_HEALTH_TICK_SECONDS),
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        ir.async_delete_issue(
            self.hass, DOMAIN, f"integration_health_{self._entry.entry_id}"
        )

    @callback
    def _async_health_tick(self, _now: Any) -> None:
        """Re-evaluate health on a timer and fire/clear the Repair Issue."""
        from .repairs import async_check_integration_health
        async_check_integration_health(self.hass, self._entry)

        # v2.9.0 EVENT-BUS — band-crossing health_change event. First tick
        # only seeds _last_health_band (no prior state to compare against,
        # so no event fires on startup).
        score, _ = _compute_integration_health(self.hass, self._entry)
        band = _health_band(score)
        if self._last_health_band is not None and band != self._last_health_band:
            self.hass.bus.async_fire(
                EVENT_HEALTH_CHANGE,
                {
                    "entry_id": self._entry.entry_id,
                    "name": self._entry.title,
                    "score": score,
                    "previous_score": self._last_health_score,
                    "band": band,
                    "previous_band": self._last_health_band,
                },
            )
        self._last_health_band = band
        self._last_health_score = score

        self.schedule_update_ha_state(force_refresh=True)

    @property
    def native_value(self) -> int:
        score, _ = _compute_integration_health(self.hass, self._entry)
        return score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        _, breakdown = _compute_integration_health(self.hass, self._entry)
        status_text, recommendation = _integration_health_plain_status(
            self.hass, breakdown
        )
        return {
            **breakdown,
            "status_text": status_text,
            "recommendation": recommendation,
        }
class RoombaResetDiagnosticsSensor(IRobotEntity, SensorEntity):
    """RESET-DIAGNOSTICS (v3.2.0) — bbrstinfo reset-cause breakdown.

    Previously entirely unread. Deliberately NOT folded into the L8 health
    score: L8's five weighted signals (25/20/20/20/15%) are a closed system
    — adding a sixth would mean re-normalising every weight plus building
    new 30-day windowed-rate infrastructure this field doesn't have yet.
    nOomRst is also j-series-only (confirmed absent on Braava's bbrstinfo
    in the KingAntDesigns field captures), so it would be structurally
    unavailable for a large share of robots if it became a scored signal.
    A plain diagnostic sensor avoids all of that.

    native_value = nSafRst (safety-triggered resets — the most actionable
    single counter; nav/mobility resets are comparatively routine).
    extra_state_attributes carries the full breakdown, including nOomRst
    (out-of-memory resets) only where the firmware actually reports it.

    Gate: "bbrstinfo" in state — present on every robot captured so far
    (both Braava and j-series), but treated as optional rather than
    assumed universal, consistent with this project's general stance on
    field presence.
    """

    _attr_translation_key = "reset_diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, roomba: Any, blid: str) -> None:
        super().__init__(roomba, blid)
        self._attr_unique_id = f"{self.robot_unique_id}_reset_diagnostics"

    @property
    def suggested_object_id(self) -> str:
        return "reset_diagnostics"

    @property
    def _info(self) -> dict[str, Any]:
        return self.vacuum_state.get("bbrstinfo") or {}

    @property
    def native_value(self) -> int | None:
        """Safety-triggered reset count — the most actionable single number."""
        info = self._info
        if not info:
            return None
        return info.get("nSafRst")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full reset breakdown. nOomRst included only when the firmware
        reports it (confirmed j-series-only; absent on Braava)."""
        info = self._info
        attrs: dict[str, Any] = {
            "nav_resets": info.get("nNavRst"),
            "mobility_resets": info.get("nMobRst"),
            "safety_resets": info.get("nSafRst"),
            "safety_reset_causes": info.get("safCauses"),
        }
        if "nOomRst" in info:
            attrs["oom_resets"] = info.get("nOomRst")
        return attrs
