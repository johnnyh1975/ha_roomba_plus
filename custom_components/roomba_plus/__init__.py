"""The Roomba+ integration — extends the HA Core Roomba integration.

Connects to Wi-Fi enabled iRobot Roomba vacuums via local MQTT (push-based,
no polling). Cloud features are optional.

v2.0: __init__.py is now the thin setup/teardown shell. Business logic lives in:
  callbacks.py  — MQTT message handlers and mission recording
  services.py   — all service/action handlers and registration
"""
from __future__ import annotations

import asyncio
import contextlib
from functools import partial
import logging
from typing import Any

from roombapy import Roomba, RoombaConnectionError, RoombaFactory

from homeassistant import exceptions
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DELAY,
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .callbacks import make_map_retrain_callback, make_map_updating_callback, make_mission_callback, make_mission_complete_callback
from .const import (
    CONF_BLID,
    CONF_BLOCKING_SENSORS,
    CONF_CONTINUOUS,
    CONF_FLOOR,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRESENCE_SCHEDULING_ENABLED,
    CONF_DEMAND_CLEANING_ENABLED,
    CONF_SMART_ZONE_DATA,
    DEFAULT_CONTINUOUS,
    DEFAULT_DELAY,
    DEFAULT_MAP_ENABLED,
    DEFAULT_MAP_SCALE,
    DEFAULT_MAP_SIZE_PX,
    DOMAIN,
    LOCAL_PLATFORMS,
    ROOMBA_SESSION,
    get_robot_profile,
    has_pose,
    has_smart_map,
)
from .api_views import DailyDigestView, MissionHistoryView, HouseholdSummaryView, MissionHistoryImportView
from .grid_store import GridStore
from .mission_store import MissionStore
from .mission_archive import MissionArchive  # v2.8.0 ARC1
from .presence_manager import PresenceManager
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
from .dirt_threshold_manager import DirtThresholdManager
from .outline_store import OutlineStore
from .maintenance_store import MaintenanceStore
from .robot_profile_store import RobotProfileStore  # v2.6 L4
from .mission_timer_store import MissionTimerStore  # v2.6 MP1
from .map_renderer import (
    MapRenderer,
    RendererConfig,
    ROBOT_DIAMETER_MM_900_SERIES,
    ROBOT_DIAMETER_MM_ISJ_SERIES,
    ROBOT_DIAMETER_MM_DEFAULT,
)
from .models import MapCapability, RoombaConfigEntry, RoombaData
from .services import async_register_services, async_remove_services
from .zone_store import ZoneStore
from .geometry_store import GeometryStore

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Migrate config entry to the current version.

    Version history:
      1 → 2 (v2.0):   Cloud coordinator now stores raw mission records alongside
                      aggregates. A marker key is added to options so the coordinator
                      knows to persist raw_records on its next fetch. All existing
                      user data (zone names, maintenance baselines, blocking/presence
                      config) is preserved unchanged. MissionStore hass.storage data
                      is unaffected — it is keyed by entry_id, not entry version.
      2 → 3 (v2.1.0): MaintenanceStore gains baseline_estcap and consecutive_skips.
      3 → 4 (v2.1.1): Entity unique_ids normalised — 37 entities renamed from
                      German slugs to English slugs in the entity registry so that
                      automations, history, and the Lovelace card are unaffected.
      9 → 10 (v2.1.1): Fix swapped completion_rate entity_ids.
      13 → 14 (v2.5.0): Fix locale-dependent entity_id slugs for signal_noise,
                        mission_recharge_time, and mission_expire_time sensors
                        (added in v2.4.3 with translation_key; German installs
                        got slugs "signalrauschen", "ladezeit", "missionsablauf").
                        translation_key removed from those descriptions; slugs
                        locked to English name= values.
    """
    current = config_entry.version
    _LOGGER.info(
        "Roomba+: migrating config entry %s from version %d",
        config_entry.entry_id, current,
    )

    if current == 1:
        # v1 → v2: mark that raw cloud records should be stored.
        # No existing data is removed or altered.
        new_options = dict(config_entry.options)
        new_options.setdefault("cloud_raw_records_version", 1)
        hass.config_entries.async_update_entry(
            config_entry,
            options=new_options,
            version=2,
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 2 (raw cloud records enabled)",
            config_entry.entry_id,
        )
        current = 2

    if current == 2:
        # v2 → v3 (v2.1.0): add baseline_estcap and consecutive_skips to
        # MaintenanceStore storage so F5d and F6g have correct defaults on
        # first load.  MaintenanceStore.async_load() already handles missing
        # keys gracefully via .get() — this migration adds the keys explicitly
        # so the storage file reflects the current schema.
        from homeassistant.helpers.storage import Store as _Store
        _store = _Store(
            hass, 1,
            f"roomba_plus_maintenance_{config_entry.entry_id}"
        )
        _data = await _store.async_load() or {}
        _data.setdefault("baseline_estcap", None)
        _data.setdefault("consecutive_skips", 0)
        await _store.async_save(_data)
        hass.config_entries.async_update_entry(
            config_entry,
            version=3,
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 3 "
            "(baseline_estcap + consecutive_skips added to MaintenanceStore)",
            config_entry.entry_id,
        )
        current = 3

    if current == 3:
        # v3 → v4 (v2.1.1): normalise entity unique_ids from German slugs to
        # English slugs.  A previous release shipped with translation_key set to
        # translated German strings, causing entity IDs to be generated in German.
        # This migration renames the affected entity registry entries so that
        # existing automations, history, and the Lovelace card continue to work.
        #
        # Rename map derived from production entity registry (Roomba 980 OG,
        # HA 2026.5.4). The migration matches any unique_id whose suffix (after
        # "roomba_plus_{blid}_" or "roomba_plus_{blid}_cloud_") is in the map,
        # so cloud sensor entries (which use a "cloud_" infix) are handled
        # automatically without separate entries.
        from homeassistant.helpers import entity_registry as er

        _SUFFIX_RENAMES: dict[str, str] = {
            # sensor — local MQTT
            "missionen_letzte_dauer":                       "last_mission_duration",
            "missionen_letztes_ergebnis":                   "last_mission_result",
            "missionen_reinigungsserie":                    "clean_streak",
            "schmutz_events_letzte_30_tage":                "recent_dirt_events",
            "wartung_akkukapazitat":                        "battery_cycles",
            "wartung_bursten_tage_bis_fallig":              "brush_remaining_hours",
            "wartung_bursten_verschleissrate":              "brush_wear_rate",
            "wartung_filter_tage_bis_fallig":               "filter_remaining_hours",
            "wartung_filter_verschleissrate":               "filter_wear_rate",
            "wartung_gesch_akkuende":                       "estimated_battery_eol",
            "gesamte_reinigungen":                          "lifetime_missions",
            "gesamte_reinigungszeit":                       "lifetime_time",
            "abschlussrate_letzte_30_tage":                 "completion_rate_30d",
            "missionen_heute_gereinigte_flache":            "area_cleaned_today",
            "missionen_letzte_30_tage":                     "missions_last_30d",
            "mission_verbleibende_ladezeit":                "mission_recharge_minutes",
            "mission_zeit_bis_ablauf":                      "mission_expire_minutes",
            "anwesenheit_nachstes_reinigungsfenster":       "next_likely_clean_window",
            "anwesenheit_reinigungsauslastung_7_tage":      "presence_clean_utilisation_7d",
            "anwesenheit_reinigungsgelegenheiten_7_tage":   "presence_clean_opportunities_7d",
            "brushes_last_replaced":                        "brush_last_replaced",
            "fehler_letzte_zone":                           "last_error_zone",
            "fehler_letzter_code":                          "last_error_code",
            "fehler_letzter_zeitpunkt":                     "last_error_at",
            "fehler_problemzone":                           "problem_zone",
            "fehler_steckenbleiber_30_tage":                "stuck_count_30d",
            "abgebrochene_missionen":                       "canceled_missions",
            "auftrag_gestartet_von":                        "job_initiator",
            "batterie_2":                                   "battery_level",
            # sensor — cloud (matched via suffix; cloud_ infix handled automatically)
            "zwischenladungen_letzte_30_tage":              "recent_recharges",
            "leistung_abdeckung":                           "recent_coverage_pct",
            "leistung_ladeanteil":                          "battery_capacity_retention",
            "leistung_reinigungsgeschwindigkeit":            "recent_cleaning_speed",
            "leistung_reinigungsgeschwindigkeitstrend":      "cleaning_speed_trend",
            "leistung_schmutzdichte":                       "recent_dirt_density",
            "letzter_fehlercode_letzte_30_tage":            "recent_error_code",
            "letzter_fehlerzeitpunkt_letzte_30_tage":       "recent_error_time",
            "clean_base_leerungen_letzte_30_tage":          "recent_evacuations",
            # binary_sensor
            "zeitplansperre_aktiv":                         "schedule_hold_active",
            "zwischenladung_aktiv":                         "mid_mission_recharge",
            "behalter_eingesetzt":                          "bin_present",
            # button
            "akkuwechsel_bestatigen":                       "reset_battery",
            "burstenwechsel_bestatigen":                    "reset_brush",
            "filterwechsel_bestatigen":                     "reset_filter",
            "letzte_mission_wiederholen":                   "repeat_mission",
            "roboter_suchen":                               "locate",
            "zone_reinigen":                                "clean_zone",
            # select
            "reinigungsdurchgange":                         "cleaning_passes",
            # image
            "reinigungskarte":                              "map",
        }

        # Stale orphan entities — existed in old naming, have no equivalent in
        # current code and will never be recreated. Remove to keep registry clean.
        _ORPHAN_SUFFIXES: set[str] = {
            "missionen_abschlussrate",   # duplicate of abschlussrate_letzte_30_tage
        }

        entity_reg = er.async_get(hass)
        blid = config_entry.data["blid"]
        bare_prefix = f"roomba_plus_{blid}_"
        cloud_prefix = f"roomba_plus_{blid}_cloud_"
        renamed = 0
        removed = 0

        for entity_entry in list(entity_reg.entities.values()):
            if entity_entry.platform != DOMAIN:
                continue
            uid = entity_entry.unique_id

            # Determine suffix — strip bare or cloud_ prefix
            if uid.startswith(cloud_prefix):
                infix = "cloud_"
                suffix = uid[len(cloud_prefix):]
            elif uid.startswith(bare_prefix):
                infix = ""
                suffix = uid[len(bare_prefix):]
            else:
                continue

            # Remove orphans
            if suffix in _ORPHAN_SUFFIXES:
                entity_reg.async_remove(entity_entry.entity_id)
                removed += 1
                _LOGGER.debug(
                    "Roomba+: removed orphan entity %s (unique_id %s)",
                    entity_entry.entity_id, uid,
                )
                continue

            # Rename German → English
            if suffix in _SUFFIX_RENAMES:
                new_suffix = _SUFFIX_RENAMES[suffix]
                new_uid = f"{bare_prefix}{infix}{new_suffix}"
                entity_reg.async_update_entity(
                    entity_entry.entity_id,
                    new_unique_id=new_uid,
                )
                renamed += 1
                _LOGGER.debug(
                    "Roomba+: renamed entity unique_id %s → %s",
                    uid, new_uid,
                )

        hass.config_entries.async_update_entry(config_entry, version=4)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 4 "
            "(%d entity unique_ids normalised, %d orphans removed)",
            config_entry.entry_id, renamed, removed,
        )
        current = 4

    if current == 4:
        # v4 → v5 (v2.1.1): rename German entity_ids to English.
        #
        # Root cause: unique_ids were always English (set from description.key).
        # entity_ids were German because they are generated from translation_key
        # at entity creation time and never automatically updated when
        # translation_key changes. Previous migrations targeted unique_ids and
        # found 0 matches because the unique_ids were already correct.
        #
        # Fix: rename entity_ids directly via async_update_entity(new_entity_id=).
        # The rename map is derived from production entity registry screenshots
        # (Roomba 980 OG, HA 2026.5.4).
        from homeassistant.helpers import entity_registry as er

        _ENTITY_ID_RENAMES_V5: dict[str, str] = {
            # sensor — roomba_980_og prefix
            "sensor.roomba_980_og_missionen_letzte_dauer":                     "sensor.roomba_980_og_last_mission_duration",
            "sensor.roomba_980_og_missionen_letztes_ergebnis":                 "sensor.roomba_980_og_last_mission_result",
            "sensor.roomba_980_og_missionen_reinigungsserie":                  "sensor.roomba_980_og_clean_streak",
            "sensor.roomba_980_og_schmutz_events_letzte_30_tage":              "sensor.roomba_980_og_recent_dirt_events",
            "sensor.roomba_980_og_wartung_akkukapazitat":                      "sensor.roomba_980_og_battery_cycles",
            "sensor.roomba_980_og_wartung_bursten_tage_bis_fallig":            "sensor.roomba_980_og_brush_remaining_hours",
            "sensor.roomba_980_og_wartung_bursten_verschleissrate":            "sensor.roomba_980_og_brush_wear_rate",
            "sensor.roomba_980_og_wartung_filter_tage_bis_fallig":             "sensor.roomba_980_og_filter_remaining_hours",
            "sensor.roomba_980_og_wartung_filter_verschleissrate":             "sensor.roomba_980_og_filter_wear_rate",
            "sensor.roomba_980_og_wartung_gesch_akkuende":                     "sensor.roomba_980_og_estimated_battery_eol",
            "sensor.roomba_980_og_zwischenladungen_letzte_30_tage":            "sensor.roomba_980_og_recent_recharges",
            "sensor.roomba_980_og_gesamte_reinigungen":                        "sensor.roomba_980_og_lifetime_missions",
            "sensor.roomba_980_og_gesamte_reinigungszeit":                     "sensor.roomba_980_og_lifetime_time",
            "sensor.roomba_980_og_leistung_abdeckung":                         "sensor.roomba_980_og_recent_coverage_pct",
            "sensor.roomba_980_og_leistung_ladeanteil":                        "sensor.roomba_980_og_battery_capacity_retention",
            "sensor.roomba_980_og_leistung_reinigungsgeschwindigkeit":          "sensor.roomba_980_og_recent_cleaning_speed",
            "sensor.roomba_980_og_leistung_reinigungsgeschwindigkeitstrend":    "sensor.roomba_980_og_cleaning_speed_trend",
            "sensor.roomba_980_og_leistung_schmutzdichte":                     "sensor.roomba_980_og_recent_dirt_density",
            "sensor.roomba_980_og_letzter_fehlercode_letzte_30_tage":          "sensor.roomba_980_og_recent_error_code",
            "sensor.roomba_980_og_letzter_fehlerzeitpunkt_letzte_30_tage":     "sensor.roomba_980_og_recent_error_time",
            "sensor.roomba_980_og_mission_verbleibende_ladezeit":              "sensor.roomba_980_og_mission_recharge_minutes",
            "sensor.roomba_980_og_mission_zeit_bis_ablauf":                    "sensor.roomba_980_og_mission_expire_minutes",
            "sensor.roomba_980_og_missionen_heute_gereinigte_flache":          "sensor.roomba_980_og_area_cleaned_today",
            "sensor.roomba_980_og_missionen_letzte_30_tage":                   "sensor.roomba_980_og_missions_last_30d",
            "sensor.roomba_980_og_abschlussrate_letzte_30_tage":               "sensor.roomba_980_og_completion_rate_30d",
            "sensor.roomba_980_og_anwesenheit_nachstes_reinigungsfenster":     "sensor.roomba_980_og_next_likely_clean_window",
            "sensor.roomba_980_og_anwesenheit_reinigungsauslastung_7_tage":    "sensor.roomba_980_og_presence_clean_utilisation_7d",
            "sensor.roomba_980_og_anwesenheit_reinigungsgelegenheiten_7_tage": "sensor.roomba_980_og_presence_clean_opportunities_7d",
            "sensor.roomba_980_og_brushes_last_replaced":                      "sensor.roomba_980_og_brush_last_replaced",
            "sensor.roomba_980_og_clean_base_leerungen_letzte_30_tage":        "sensor.roomba_980_og_recent_evacuations",
            "sensor.roomba_980_og_fehler_letzte_zone":                         "sensor.roomba_980_og_last_error_zone",
            "sensor.roomba_980_og_fehler_letzter_code":                        "sensor.roomba_980_og_last_error_code",
            "sensor.roomba_980_og_fehler_letzter_zeitpunkt":                   "sensor.roomba_980_og_last_error_at",
            "sensor.roomba_980_og_fehler_problemzone":                         "sensor.roomba_980_og_problem_zone",
            "sensor.roomba_980_og_fehler_steckenbleiber_30_tage":              "sensor.roomba_980_og_stuck_count_30d",
            # sensor — short prefix
            "sensor.roomba_abgebrochene_missionen":                            "sensor.roomba_canceled_missions",
            "sensor.roomba_auftrag_gestartet_von":                             "sensor.roomba_job_initiator",
            "sensor.roomba_batterie_2":                                        "sensor.roomba_battery_level",
            # binary_sensor
            "binary_sensor.roomba_980_og_zeitplansperre_aktiv":                "binary_sensor.roomba_980_og_schedule_hold_active",
            "binary_sensor.roomba_980_og_zwischenladung_aktiv":                "binary_sensor.roomba_980_og_mid_mission_recharge",
            "binary_sensor.roomba_behalter_eingesetzt":                        "binary_sensor.roomba_bin_present",
            # button
            "button.roomba_akkuwechsel_bestatigen":                            "button.roomba_reset_battery",
            "button.roomba_burstenwechsel_bestatigen":                         "button.roomba_reset_brush",
            "button.roomba_filterwechsel_bestatigen":                          "button.roomba_reset_filter",
            "button.roomba_letzte_mission_wiederholen":                        "button.roomba_repeat_mission",
            "button.roomba_roboter_suchen":                                    "button.roomba_locate",
            "button.roomba_zone_reinigen":                                     "button.roomba_clean_zone",
            # select
            "select.roomba_reinigungsdurchgange":                              "select.roomba_cleaning_passes",
            # image
            "image.roomba_reinigungskarte":                                    "image.roomba_map",
        }

        # Orphan entity_ids — no longer exist in code, remove from registry
        _ORPHAN_ENTITY_IDS_V5: set[str] = {
            "sensor.roomba_980_og_missionen_abschlussrate",
        }

        entity_reg = er.async_get(hass)
        renamed = 0
        removed = 0

        for old_eid in _ORPHAN_ENTITY_IDS_V5:
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_remove(old_eid)
                removed += 1
                _LOGGER.debug("Roomba+: removed orphan entity %s", old_eid)

        for old_eid, new_eid in _ENTITY_ID_RENAMES_V5.items():
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_update_entity(old_eid, new_entity_id=new_eid)
                renamed += 1
                _LOGGER.debug("Roomba+: renamed entity_id %s → %s", old_eid, new_eid)

        hass.config_entries.async_update_entry(config_entry, version=5)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 5 "
            "(%d entity_ids renamed to English, %d orphans removed)",
            config_entry.entry_id, renamed, removed,
        )
        current = 5

    if current == 5:
        # v5 → v6 (v2.1.1): re-execute entity_id rename.
        # The v4→v5 step ran with the correct logic but the entry was already
        # at version 5 from a previous failed attempt, so HA skipped it.
        # This step is identical to v4→v5 — fully idempotent.
        from homeassistant.helpers import entity_registry as er

        _ENTITY_ID_RENAMES_V6: dict[str, str] = {
            "sensor.roomba_980_og_missionen_letzte_dauer":                     "sensor.roomba_980_og_last_mission_duration",
            "sensor.roomba_980_og_missionen_letztes_ergebnis":                 "sensor.roomba_980_og_last_mission_result",
            "sensor.roomba_980_og_missionen_reinigungsserie":                  "sensor.roomba_980_og_clean_streak",
            "sensor.roomba_980_og_schmutz_events_letzte_30_tage":              "sensor.roomba_980_og_recent_dirt_events",
            "sensor.roomba_980_og_wartung_akkukapazitat":                      "sensor.roomba_980_og_battery_cycles",
            "sensor.roomba_980_og_wartung_bursten_tage_bis_fallig":            "sensor.roomba_980_og_brush_remaining_hours",
            "sensor.roomba_980_og_wartung_bursten_verschleissrate":            "sensor.roomba_980_og_brush_wear_rate",
            "sensor.roomba_980_og_wartung_filter_tage_bis_fallig":             "sensor.roomba_980_og_filter_remaining_hours",
            "sensor.roomba_980_og_wartung_filter_verschleissrate":             "sensor.roomba_980_og_filter_wear_rate",
            "sensor.roomba_980_og_wartung_gesch_akkuende":                     "sensor.roomba_980_og_estimated_battery_eol",
            "sensor.roomba_980_og_zwischenladungen_letzte_30_tage":            "sensor.roomba_980_og_recent_recharges",
            "sensor.roomba_980_og_gesamte_reinigungen":                        "sensor.roomba_980_og_lifetime_missions",
            "sensor.roomba_980_og_gesamte_reinigungszeit":                     "sensor.roomba_980_og_lifetime_time",
            "sensor.roomba_980_og_leistung_abdeckung":                         "sensor.roomba_980_og_recent_coverage_pct",
            "sensor.roomba_980_og_leistung_ladeanteil":                        "sensor.roomba_980_og_battery_capacity_retention",
            "sensor.roomba_980_og_leistung_reinigungsgeschwindigkeit":          "sensor.roomba_980_og_recent_cleaning_speed",
            "sensor.roomba_980_og_leistung_reinigungsgeschwindigkeitstrend":    "sensor.roomba_980_og_cleaning_speed_trend",
            "sensor.roomba_980_og_leistung_schmutzdichte":                     "sensor.roomba_980_og_recent_dirt_density",
            "sensor.roomba_980_og_letzter_fehlercode_letzte_30_tage":          "sensor.roomba_980_og_recent_error_code",
            "sensor.roomba_980_og_letzter_fehlerzeitpunkt_letzte_30_tage":     "sensor.roomba_980_og_recent_error_time",
            "sensor.roomba_980_og_mission_verbleibende_ladezeit":              "sensor.roomba_980_og_mission_recharge_minutes",
            "sensor.roomba_980_og_mission_zeit_bis_ablauf":                    "sensor.roomba_980_og_mission_expire_minutes",
            "sensor.roomba_980_og_missionen_heute_gereinigte_flache":          "sensor.roomba_980_og_area_cleaned_today",
            "sensor.roomba_980_og_missionen_letzte_30_tage":                   "sensor.roomba_980_og_missions_last_30d",
            "sensor.roomba_980_og_abschlussrate_letzte_30_tage":               "sensor.roomba_980_og_completion_rate_30d",
            "sensor.roomba_980_og_anwesenheit_nachstes_reinigungsfenster":     "sensor.roomba_980_og_next_likely_clean_window",
            "sensor.roomba_980_og_anwesenheit_reinigungsauslastung_7_tage":    "sensor.roomba_980_og_presence_clean_utilisation_7d",
            "sensor.roomba_980_og_anwesenheit_reinigungsgelegenheiten_7_tage": "sensor.roomba_980_og_presence_clean_opportunities_7d",
            "sensor.roomba_980_og_brushes_last_replaced":                      "sensor.roomba_980_og_brush_last_replaced",
            "sensor.roomba_980_og_clean_base_leerungen_letzte_30_tage":        "sensor.roomba_980_og_recent_evacuations",
            "sensor.roomba_980_og_fehler_letzte_zone":                         "sensor.roomba_980_og_last_error_zone",
            "sensor.roomba_980_og_fehler_letzter_code":                        "sensor.roomba_980_og_last_error_code",
            "sensor.roomba_980_og_fehler_letzter_zeitpunkt":                   "sensor.roomba_980_og_last_error_at",
            "sensor.roomba_980_og_fehler_problemzone":                         "sensor.roomba_980_og_problem_zone",
            "sensor.roomba_980_og_fehler_steckenbleiber_30_tage":              "sensor.roomba_980_og_stuck_count_30d",
            "sensor.roomba_abgebrochene_missionen":                            "sensor.roomba_canceled_missions",
            "sensor.roomba_auftrag_gestartet_von":                             "sensor.roomba_job_initiator",
            "sensor.roomba_batterie_2":                                        "sensor.roomba_battery_level",
            "binary_sensor.roomba_980_og_zeitplansperre_aktiv":                "binary_sensor.roomba_980_og_schedule_hold_active",
            "binary_sensor.roomba_980_og_zwischenladung_aktiv":                "binary_sensor.roomba_980_og_mid_mission_recharge",
            "binary_sensor.roomba_behalter_eingesetzt":                        "binary_sensor.roomba_bin_present",
            "button.roomba_akkuwechsel_bestatigen":                            "button.roomba_reset_battery",
            "button.roomba_burstenwechsel_bestatigen":                         "button.roomba_reset_brush",
            "button.roomba_filterwechsel_bestatigen":                          "button.roomba_reset_filter",
            "button.roomba_letzte_mission_wiederholen":                        "button.roomba_repeat_mission",
            "button.roomba_roboter_suchen":                                    "button.roomba_locate",
            "button.roomba_zone_reinigen":                                     "button.roomba_clean_zone",
            "select.roomba_reinigungsdurchgange":                              "select.roomba_cleaning_passes",
            "image.roomba_reinigungskarte":                                    "image.roomba_map",
        }
        _ORPHAN_ENTITY_IDS_V6: set[str] = {
            "sensor.roomba_980_og_missionen_abschlussrate",
        }

        entity_reg = er.async_get(hass)
        renamed = 0
        removed = 0

        for old_eid in _ORPHAN_ENTITY_IDS_V6:
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_remove(old_eid)
                removed += 1
                _LOGGER.debug("Roomba+: removed orphan entity %s", old_eid)

        for old_eid, new_eid in _ENTITY_ID_RENAMES_V6.items():
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_update_entity(old_eid, new_entity_id=new_eid)
                renamed += 1
                _LOGGER.debug("Roomba+: renamed entity_id %s → %s", old_eid, new_eid)

        hass.config_entries.async_update_entry(config_entry, version=6)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 6 "
            "(%d entity_ids renamed to English, %d orphans removed)",
            config_entry.entry_id, renamed, removed,
        )
        current = 6

    if current == 6:
        # v6 → v7 (v2.1.1): definitive entity_id normalisation.
        #
        # Built from the actual entity registry (core.entity_registry) of the
        # affected installation. Two actions:
        # 1. RENAME — German/translated entity_id → correct English entity_id
        # 2. REMOVE — stale German entity_id where the English version already
        #    exists (left behind by earlier migration attempts that created
        #    duplicates instead of renaming in-place).
        #
        # This migration is idempotent: entities not found are silently skipped.
        from homeassistant.helpers import entity_registry as er

        _RENAMES_V7: dict[str, str] = {
            # button
            "button.roomba_980_og_ausschalten_experimentell":      "button.roomba_980_og_power_off",
            "button.roomba_980_og_ruhemodus_experimentell":        "button.roomba_980_og_sleep",
            "button.roomba_980_og_schnellreinigung_experimentell": "button.roomba_980_og_quick",
            "button.roomba_980_og_spot_reinigung_experimentell":   "button.roomba_980_og_spot",
            # select
            "select.roomba":                                       "select.roomba_zone_select",
            # sensor — roomba_980_og prefix
            "sensor.roomba_980_og_absturzsensoren_hinten":         "sensor.roomba_980_og_cliff_events_rear",
            "sensor.roomba_980_og_absturzsensoren_vorne":          "sensor.roomba_980_og_cliff_events_front",
            "sensor.roomba_980_og_akkukapazitat":                  "sensor.roomba_980_og_battery_capacity_mah",
            "sensor.roomba_980_og_battery_capacity_retention":     "sensor.roomba_980_og_recent_recharge_fraction",
            "sensor.roomba_980_og_brush_remaining_hours":          "sensor.roomba_980_og_brush_days_until_due",
            "sensor.roomba_980_og_filter_remaining_hours":         "sensor.roomba_980_og_filter_days_until_due",
            "sensor.roomba_980_og_gesamte_gereinigte_flache":      "sensor.roomba_980_og_lifetime_area",
            "sensor.roomba_980_og_leistung_aufeinanderfolgende_ausfalle": "sensor.roomba_980_og_consecutive_clean_skips",
            "sensor.roomba_980_og_navigations_panik_ereignisse":   "sensor.roomba_980_og_nav_panics",
            "sensor.roomba_980_og_rohzustand_debug":               "sensor.roomba_980_og_raw_state",
            "sensor.roomba_980_og_wlan_signalminimum":             "sensor.roomba_980_og_recent_wifi_floor",
            "sensor.roomba_980_og_wlan_signalstabilitat":          "sensor.roomba_980_og_recent_wifi_stability",
            # sensor — roomba prefix
            "sensor.roomba_betriebsbereitschaft":                  "sensor.roomba_readiness",
            "sensor.roomba_durchschnittliche_missionsdauer":       "sensor.roomba_average_mission_time",
            "sensor.roomba_erfolgreiche_missionen":                "sensor.roomba_successful_missions",
            "sensor.roomba_fehler":                                "sensor.roomba_error",
            "sensor.roomba_fehlgeschlagene_missionen":             "sensor.roomba_failed_missions",
            "sensor.roomba_gereinigte_flache_gesamt":              "sensor.roomba_total_cleaned_area",
            "sensor.roomba_gesamtreinigungszeit":                  "sensor.roomba_total_cleaning_time",
            "sensor.roomba_ip_adresse":                            "sensor.roomba_ip_address",
            "sensor.roomba_ladezeit":                              "sensor.roomba_mission_recharge_time",
            "sensor.roomba_letzte_mission":                        "sensor.roomba_last_mission",
            "sensor.roomba_missionen_gesamt":                      "sensor.roomba_total_missions",
            "sensor.roomba_missionsablauf":                        "sensor.roomba_mission_expire_time",
            "sensor.roomba_missionsdauer":                         "sensor.roomba_mission_elapsed_time",
            "sensor.roomba_missionsstart":                         "sensor.roomba_mission_start_time",
            "sensor.roomba_navigationsqualitat":                   "sensor.roomba_nav_quality",
            "sensor.roomba_reinigungsdurchgange":                  "sensor.roomba_clean_mode",
            "sensor.roomba_schmutzerkennungs_ereignisse":          "sensor.roomba_scrubs_count",
            "sensor.roomba_signalrauschen":                        "sensor.roomba_signal_noise",
            "sensor.roomba_status_nachste_reinigung":              "sensor.roomba_next_clean",
            "sensor.roomba_teppich_boost_modus":                   "sensor.roomba_carpet_boost_mode",
            "sensor.roomba_wlan_signal":                           "sensor.roomba_rssi",
            # switch
            "switch.roomba_einstellung_mission_immer_beenden":     "switch.roomba_always_finish",
            "switch.roomba_einstellung_zeitplan_pausieren":        "switch.roomba_schedule_hold",
            "switch.roomba_randreinigung":                         "switch.roomba_edge_clean",
        }

        # Stale German entity_ids where the correct English version already exists.
        # These were created as duplicates by earlier migration attempts.
        _REMOVES_V7: list[str] = [
            "sensor.roomba_980_og_battery_cycles",           # battery_capacity_retention exists
            "sensor.roomba_980_og_missionen_abschlussrate",  # completion_rate_30d exists
            "sensor.roomba_burstenlebensdauer_verbleibend",  # brush_remaining_hours exists
            "sensor.roomba_filterlebensdauer_verbleibend",   # filter_remaining_hours exists
            "sensor.roomba_ladezyklen",                      # battery_cycles exists
        ]

        entity_reg = er.async_get(hass)
        renamed = 0
        removed = 0

        for old_eid in _REMOVES_V7:
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_remove(old_eid)
                removed += 1
                _LOGGER.debug("Roomba+: removed stale entity %s", old_eid)

        for old_eid, new_eid in _RENAMES_V7.items():
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_update_entity(old_eid, new_entity_id=new_eid)
                renamed += 1
                _LOGGER.debug("Roomba+: renamed %s → %s", old_eid, new_eid)

        hass.config_entries.async_update_entry(config_entry, version=7)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 7 "
            "(%d entity_ids renamed, %d stale entities removed)",
            config_entry.entry_id, renamed, removed,
        )
        current = 7

    if current == 7:
        # v7 → v8 (v2.1.1): complete entity normalisation in one pass.
        # Combines prefix standardisation (roomba → roomba_980_og for all
        # entities) with German → English rename. Derived from the actual
        # core.entity_registry snapshot of the affected installation.
        # All actions are idempotent — missing entities are silently skipped.
        from homeassistant.helpers import entity_registry as er

        _RENAMES_V8: dict[str, str] = {
            # binary_sensor — prefix fix
            "binary_sensor.roomba_bin_full":                        "binary_sensor.roomba_980_og_bin_full",
            "binary_sensor.roomba_bin_present":                     "binary_sensor.roomba_980_og_bin_present",
            "binary_sensor.roomba_connected":                       "binary_sensor.roomba_980_og_connected",
            # button — German rename
            "button.roomba_980_og_ausschalten_experimentell":       "button.roomba_980_og_power_off",
            "button.roomba_980_og_ruhemodus_experimentell":         "button.roomba_980_og_sleep",
            "button.roomba_980_og_schnellreinigung_experimentell":  "button.roomba_980_og_quick",
            "button.roomba_980_og_spot_reinigung_experimentell":    "button.roomba_980_og_spot",
            # button — prefix fix
            "button.roomba_clean_zone":                             "button.roomba_980_og_clean_zone",
            "button.roomba_locate":                                 "button.roomba_980_og_locate",
            "button.roomba_repeat_mission":                         "button.roomba_980_og_repeat_mission",
            "button.roomba_reset_battery":                          "button.roomba_980_og_reset_battery",
            "button.roomba_reset_brush":                            "button.roomba_980_og_reset_brush",
            "button.roomba_reset_filter":                           "button.roomba_980_og_reset_filter",
            # image — prefix fix
            "image.roomba_map":                                     "image.roomba_980_og_map",
            # select — prefix fix
            "select.roomba":                                        "select.roomba_980_og_zone_select",
            "select.roomba_cleaning_passes":                        "select.roomba_980_og_cleaning_passes",
            # sensor — German rename (roomba_980_og prefix, wrong key)
            "sensor.roomba_980_og_absturzsensoren_hinten":          "sensor.roomba_980_og_cliff_events_rear",
            "sensor.roomba_980_og_absturzsensoren_vorne":           "sensor.roomba_980_og_cliff_events_front",
            "sensor.roomba_980_og_akkukapazitat":                   "sensor.roomba_980_og_battery_capacity_mah",
            "sensor.roomba_980_og_battery_capacity_retention":      "sensor.roomba_980_og_recent_recharge_fraction",
            "sensor.roomba_980_og_brush_remaining_hours":           "sensor.roomba_980_og_brush_days_until_due",
            "sensor.roomba_980_og_completion_rate_30d":             "sensor.roomba_980_og_recent_completion_rate",
            "sensor.roomba_980_og_filter_remaining_hours":          "sensor.roomba_980_og_filter_days_until_due",
            "sensor.roomba_980_og_gesamte_gereinigte_flache":       "sensor.roomba_980_og_lifetime_area",
            "sensor.roomba_980_og_leistung_aufeinanderfolgende_ausfalle": "sensor.roomba_980_og_consecutive_clean_skips",
            "sensor.roomba_980_og_navigations_panik_ereignisse":    "sensor.roomba_980_og_nav_panics",
            "sensor.roomba_980_og_rohzustand_debug":                "sensor.roomba_980_og_raw_state",
            "sensor.roomba_980_og_wlan_signalminimum":              "sensor.roomba_980_og_recent_wifi_floor",
            "sensor.roomba_980_og_wlan_signalstabilitat":           "sensor.roomba_980_og_recent_wifi_stability",
            # sensor — prefix fix + German rename combined
            "sensor.roomba_battery_level":                          "sensor.roomba_980_og_battery_level",
            "sensor.roomba_betriebsbereitschaft":                   "sensor.roomba_980_og_readiness",
            "sensor.roomba_canceled_missions":                      "sensor.roomba_980_og_canceled_missions",
            "sensor.roomba_durchschnittliche_missionsdauer":        "sensor.roomba_980_og_average_mission_time",
            "sensor.roomba_erfolgreiche_missionen":                 "sensor.roomba_980_og_successful_missions",
            "sensor.roomba_fehler":                                 "sensor.roomba_980_og_error",
            "sensor.roomba_fehlgeschlagene_missionen":              "sensor.roomba_980_og_failed_missions",
            "sensor.roomba_gereinigte_flache_gesamt":               "sensor.roomba_980_og_total_cleaned_area",
            "sensor.roomba_gesamtreinigungszeit":                   "sensor.roomba_980_og_total_cleaning_time",
            "sensor.roomba_ip_adresse":                             "sensor.roomba_980_og_ip_address",
            "sensor.roomba_job_initiator":                          "sensor.roomba_980_og_job_initiator",
            "sensor.roomba_ladezeit":                               "sensor.roomba_980_og_mission_recharge_time",
            "sensor.roomba_letzte_mission":                         "sensor.roomba_980_og_last_mission",
            "sensor.roomba_missionen_gesamt":                       "sensor.roomba_980_og_total_missions",
            "sensor.roomba_missionsablauf":                         "sensor.roomba_980_og_mission_expire_time",
            "sensor.roomba_missionsdauer":                          "sensor.roomba_980_og_mission_elapsed_time",
            "sensor.roomba_missionsstart":                          "sensor.roomba_980_og_mission_start_time",
            "sensor.roomba_navigationsqualitat":                    "sensor.roomba_980_og_nav_quality",
            "sensor.roomba_phase":                                  "sensor.roomba_980_og_phase",
            "sensor.roomba_reinigungsdurchgange":                   "sensor.roomba_980_og_clean_mode",
            "sensor.roomba_schmutzerkennungs_ereignisse":           "sensor.roomba_980_og_scrubs_count",
            "sensor.roomba_signalrauschen":                         "sensor.roomba_980_og_signal_noise",
            "sensor.roomba_snr":                                    "sensor.roomba_980_og_snr",
            "sensor.roomba_status_nachste_reinigung":               "sensor.roomba_980_og_next_clean",
            "sensor.roomba_teppich_boost_modus":                    "sensor.roomba_980_og_carpet_boost_mode",
            "sensor.roomba_wlan_signal":                            "sensor.roomba_980_og_rssi",
            # switch — prefix fix + German rename
            "switch.roomba_einstellung_mission_immer_beenden":      "switch.roomba_980_og_always_finish",
            "switch.roomba_einstellung_zeitplan_pausieren":         "switch.roomba_980_og_schedule_hold",
            "switch.roomba_randreinigung":                          "switch.roomba_980_og_edge_clean",
            # vacuum
            "vacuum.roomba":                                        "vacuum.roomba_980_og",
        }

        _REMOVES_V8: list[str] = [
            "sensor.roomba_980_og_battery_cycles",           # battery_capacity_retention exists
            "sensor.roomba_980_og_missionen_abschlussrate",  # completion_rate_30d (local) orphan
            "sensor.roomba_burstenlebensdauer_verbleibend",  # brush_remaining_hours exists
            "sensor.roomba_filterlebensdauer_verbleibend",   # filter_remaining_hours exists
            "sensor.roomba_ladezyklen",                      # battery_cycles exists
        ]

        entity_reg = er.async_get(hass)
        renamed = 0
        removed = 0

        for old_eid in _REMOVES_V8:
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_remove(old_eid)
                removed += 1
                _LOGGER.debug("Roomba+: removed stale entity %s", old_eid)

        for old_eid, new_eid in _RENAMES_V8.items():
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_update_entity(old_eid, new_entity_id=new_eid)
                renamed += 1
                _LOGGER.debug("Roomba+: renamed %s → %s", old_eid, new_eid)

        hass.config_entries.async_update_entry(config_entry, version=8)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 8 "
            "(%d entity_ids renamed, %d stale entities removed)",
            config_entry.entry_id, renamed, removed,
        )
        current = 8

    if current == 8:
        # v8 → v9 (v2.1.1): final entity_id cleanup.
        # Previous migrations renamed German→English but left some entities
        # with the short "roomba" prefix instead of "roomba_980_og".
        # This step moves all remaining short-prefix entities to roomba_980_og
        # and fixes the last remaining German entity_ids.
        from homeassistant.helpers import entity_registry as er

        _RENAMES_V9: dict[str, str] = {
            "select.roomba_zone_select":                        "select.roomba_980_og_zone_select",
            "sensor.roomba_average_mission_time":               "sensor.roomba_980_og_average_mission_time",
            "sensor.roomba_burstenlebensdauer_verbleibend":     "sensor.roomba_980_og_brush_remaining_hours",
            "sensor.roomba_carpet_boost_mode":                  "sensor.roomba_980_og_carpet_boost_mode",
            "sensor.roomba_clean_mode":                         "sensor.roomba_980_og_clean_mode",
            "sensor.roomba_error":                              "sensor.roomba_980_og_error",
            "sensor.roomba_failed_missions":                    "sensor.roomba_980_og_failed_missions",
            "sensor.roomba_filterlebensdauer_verbleibend":      "sensor.roomba_980_og_filter_remaining_hours",
            "sensor.roomba_ip_address":                         "sensor.roomba_980_og_ip_address",
            "sensor.roomba_ladezyklen":                         "sensor.roomba_980_og_battery_cycles",
            "sensor.roomba_last_mission":                       "sensor.roomba_980_og_last_mission",
            "sensor.roomba_mission_elapsed_time":               "sensor.roomba_980_og_mission_elapsed_time",
            "sensor.roomba_mission_expire_time":                "sensor.roomba_980_og_mission_expire_time",
            "sensor.roomba_mission_recharge_time":              "sensor.roomba_980_og_mission_recharge_time",
            "sensor.roomba_mission_start_time":                 "sensor.roomba_980_og_mission_start_time",
            "sensor.roomba_nav_quality":                        "sensor.roomba_980_og_nav_quality",
            "sensor.roomba_next_clean":                         "sensor.roomba_980_og_next_clean",
            "sensor.roomba_readiness":                          "sensor.roomba_980_og_readiness",
            "sensor.roomba_rssi":                               "sensor.roomba_980_og_rssi",
            "sensor.roomba_scrubs_count":                       "sensor.roomba_980_og_scrubs_count",
            "sensor.roomba_signal_noise":                       "sensor.roomba_980_og_signal_noise",
            "sensor.roomba_snr":                                "sensor.roomba_980_og_snr",
            "sensor.roomba_successful_missions":                "sensor.roomba_980_og_successful_missions",
            "sensor.roomba_total_cleaned_area":                 "sensor.roomba_980_og_total_cleaned_area",
            "sensor.roomba_total_cleaning_time":                "sensor.roomba_980_og_total_cleaning_time",
            "sensor.roomba_total_missions":                     "sensor.roomba_980_og_total_missions",
            "switch.roomba_always_finish":                      "switch.roomba_980_og_always_finish",
            "switch.roomba_edge_clean":                         "switch.roomba_980_og_edge_clean",
            "switch.roomba_schedule_hold":                      "switch.roomba_980_og_schedule_hold",
        }

        _REMOVES_V9: list[str] = [
            "sensor.roomba_980_og_missionen_abschlussrate",  # orphan, completion_rate_30d exists
        ]

        entity_reg = er.async_get(hass)
        renamed = 0

        for old_eid in _REMOVES_V9:
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                entity_reg.async_remove(old_eid)
                renamed += 1
                _LOGGER.debug("Roomba+: removed orphan %s", old_eid)

        for old_eid, new_eid in _RENAMES_V9.items():
            entry = entity_reg.async_get(old_eid)
            if entry is not None and entry.platform == DOMAIN:
                # Skip if target already exists (avoid duplicate)
                if entity_reg.async_get(new_eid) is None:
                    entity_reg.async_update_entity(old_eid, new_entity_id=new_eid)
                    renamed += 1
                    _LOGGER.debug("Roomba+: renamed %s → %s", old_eid, new_eid)
                else:
                    entity_reg.async_remove(old_eid)
                    renamed += 1
                    _LOGGER.debug("Roomba+: removed duplicate %s", old_eid)

        hass.config_entries.async_update_entry(config_entry, version=9)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 9 (%d entity_ids finalised)",
            config_entry.entry_id, renamed,
        )
        current = 9

    if current == 9:
        # v9 → v10 (v2.1.1): fix swapped completion_rate entity_ids.
        #
        # Two sensors ended up with swapped entity_ids:
        #   sensor.roomba_980_og_completion_rate_30d
        #     uid: cloud_recent_completion_rate  ← should be recent_completion_rate
        #   sensor.roomba_980_og_missionen_abschlussrate
        #     uid: completion_rate_30d            ← should be completion_rate_30d
        #
        # The local completion_rate_30d sensor is live (code recreates it) so
        # it cannot be removed. Must rename both in the correct order:
        # Step 1: cloud sensor → temp name (frees the target slot)
        # Step 2: local sensor → completion_rate_30d
        # Step 3: cloud sensor → recent_completion_rate
        from homeassistant.helpers import entity_registry as er

        entity_reg = er.async_get(hass)
        renamed = 0

        CLOUD_EID   = "sensor.roomba_980_og_completion_rate_30d"
        LOCAL_EID   = "sensor.roomba_980_og_missionen_abschlussrate"
        CLOUD_FINAL = "sensor.roomba_980_og_recent_completion_rate"
        LOCAL_FINAL = "sensor.roomba_980_og_completion_rate_30d"
        TEMP_EID    = "sensor.roomba_980_og_completion_rate_cloud_tmp"

        cloud_entry = entity_reg.async_get(CLOUD_EID)
        local_entry = entity_reg.async_get(LOCAL_EID)

        if cloud_entry is not None and cloud_entry.platform == DOMAIN:
            if local_entry is not None:
                # Step 1: move cloud to temp
                entity_reg.async_update_entity(CLOUD_EID, new_entity_id=TEMP_EID)
                renamed += 1
                # Step 2: move local to correct name
                entity_reg.async_update_entity(LOCAL_EID, new_entity_id=LOCAL_FINAL)
                renamed += 1
                # Step 3: move cloud from temp to final
                entity_reg.async_update_entity(TEMP_EID, new_entity_id=CLOUD_FINAL)
                renamed += 1
                _LOGGER.debug(
                    "Roomba+: swapped completion_rate entity_ids: "
                    "%s → %s, %s → %s",
                    CLOUD_EID, CLOUD_FINAL, LOCAL_EID, LOCAL_FINAL,
                )
            else:
                # Local already gone, just fix cloud
                entity_reg.async_update_entity(CLOUD_EID, new_entity_id=CLOUD_FINAL)
                renamed += 1

        elif local_entry is not None and local_entry.platform == DOMAIN:
            # Cloud already correct, just fix local
            entity_reg.async_update_entity(LOCAL_EID, new_entity_id=LOCAL_FINAL)
            renamed += 1

        hass.config_entries.async_update_entry(config_entry, version=10)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 10 (%d entity_ids fixed)",
            config_entry.entry_id, renamed,
        )
        current = 10

    if current == 10:
        # v10 → v11 (v2.1.2): rename cloud history sensor entity_ids.
        #
        # The sensors lifetime_area and lifetime_time were misnamed — they
        # aggregate the ~30-mission API window, not true lifetime totals.
        # Correct names: recent_area_30d and recent_time_30d.
        #
        # Pattern: sensor.<device_name>_lifetime_area  → sensor.<device_name>_recent_area_30d
        #          sensor.<device_name>_lifetime_time  → sensor.<device_name>_recent_time_30d
        #
        # We scan the entity registry for all roomba_plus entities matching
        # the old suffixes rather than hardcoding device-specific names, so
        # this works for any installation regardless of device name.
        from homeassistant.helpers import entity_registry as er

        entity_reg = er.async_get(hass)
        renamed = 0

        for entry in list(entity_reg.entities.values()):
            if entry.platform != DOMAIN:
                continue
            if entry.entity_id.endswith("_lifetime_area"):
                new_eid = entry.entity_id[:-len("_lifetime_area")] + "_recent_area_30d"
                if entity_reg.async_get(new_eid) is None:
                    entity_reg.async_update_entity(entry.entity_id, new_entity_id=new_eid)
                    renamed += 1
                    _LOGGER.debug("Roomba+: renamed %s → %s", entry.entity_id, new_eid)
                else:
                    entity_reg.async_remove(entry.entity_id)
                    renamed += 1
                    _LOGGER.debug("Roomba+: removed duplicate %s", entry.entity_id)
            elif entry.entity_id.endswith("_lifetime_time"):
                new_eid = entry.entity_id[:-len("_lifetime_time")] + "_recent_time_30d"
                if entity_reg.async_get(new_eid) is None:
                    entity_reg.async_update_entity(entry.entity_id, new_entity_id=new_eid)
                    renamed += 1
                    _LOGGER.debug("Roomba+: renamed %s → %s", entry.entity_id, new_eid)
                else:
                    entity_reg.async_remove(entry.entity_id)
                    renamed += 1
                    _LOGGER.debug("Roomba+: removed duplicate %s", entry.entity_id)

        hass.config_entries.async_update_entry(config_entry, version=11)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 11 (%d entity_ids renamed/removed)",
            config_entry.entry_id, renamed,
        )
        current = 11

    if current == 11:
        # v11 → v12 (v2.2.0): add floor_label to options.
        #
        # floor_label (CONF_FLOOR) — user-assigned floor name for the household
        # REST endpoint (/api/roomba_plus/household). Defaults to empty string
        # meaning "no floor assigned". Does not create any entity.
        #
        # GridStore uses a separate hass.storage key (roomba_plus_grid_{id}),
        # so no options migration is needed for it.
        #
        # Entity rename: recent_area_30d and recent_time_30d were registered
        # without translation_key in v2.1.x, causing HA to use the translated
        # name string as the entity_id slug on fresh installs.
        #
        # Example on DE installation:
        #   sensor.*_gereinigte_flache_30_t  → sensor.*_recent_area_30d
        #   sensor.*_reinigungszeit_30_t     → sensor.*_recent_time_30d
        #
        # We find them by unique_id (always "*_cloud_recent_area_30d" / "*_cloud_recent_time_30d")
        # which is locale-independent — then rename the entity_id if it
        # doesn't already end with the correct suffix.
        #
        # Device prefix derivation: for each wrong entity, find any sibling
        # Roomba+ sensor entity for the same blid whose entity_id ends with
        # its unique_id's trailing key. Use that to compute the device prefix.
        from homeassistant.helpers import entity_registry as er

        entity_reg = er.async_get(hass)
        slug_renamed = 0

        # Map unique_id suffix → correct entity_id suffix
        _UID_SUFFIX_TO_EID_SUFFIX: dict[str, str] = {
            "_cloud_recent_area_30d": "_recent_area_30d",
            "_cloud_recent_time_30d": "_recent_time_30d",
        }

        # Build a blid → list[entity] map for all roomba_plus sensors
        blid_entities: dict[str, list] = {}
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            # unique_id format is "{blid}_cloud_{key}" or "{blid}_{key}"
            # We can extract blid up to the first "_cloud_" or "_{key}"
            # For our purposes just group by first 32 chars (blid is typically 32 hex)
            for uid_suffix in _UID_SUFFIX_TO_EID_SUFFIX:
                if uid.endswith(uid_suffix):
                    blid_key = uid[: -len(uid_suffix)]
                    blid_entities.setdefault(blid_key, []).append(entry_er)
                    break
            else:
                # Add to lookup by blid prefix for cross-reference
                # Extract blid as longest known-prefix part
                for uid_suffix in _UID_SUFFIX_TO_EID_SUFFIX:
                    pass  # just to have blid_entities populated with all entities below
                # We'll populate a separate full map
                pass

        # Full map: all roomba_plus entities grouped by whatever precedes "_cloud_" or first "_"
        all_by_blid: dict[str, list] = {}
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            # Find the blid — try "_cloud_" separator first
            if "_cloud_" in uid:
                blid_key = uid.split("_cloud_")[0]
            else:
                continue
            all_by_blid.setdefault(blid_key, []).append(entry_er)

        for blid_key, wrong_entries in blid_entities.items():
            # Find siblings for device prefix derivation
            siblings = all_by_blid.get(blid_key, [])

            for wrong_entry in wrong_entries:
                uid = wrong_entry.unique_id or ""
                uid_suffix = next(s for s in _UID_SUFFIX_TO_EID_SUFFIX if uid.endswith(s))
                correct_eid_suffix = _UID_SUFFIX_TO_EID_SUFFIX[uid_suffix]

                if wrong_entry.entity_id.endswith(correct_eid_suffix):
                    continue  # already correct

                # Derive device prefix from a sibling entity whose entity_id
                # ends with "_" + the last part of its unique_id's cloud key.
                device_prefix: str | None = None
                for sibling in siblings:
                    s_uid = sibling.unique_id or ""
                    if "_cloud_" not in s_uid:
                        continue
                    cloud_key = s_uid.split("_cloud_", 1)[1]  # e.g. "lifetime_missions"
                    if sibling.entity_id.endswith("_" + cloud_key):
                        device_prefix = sibling.entity_id[: -(len(cloud_key) + 1)]
                        break

                if device_prefix is None:
                    # Try all roomba_plus entities for this blid
                    for any_entry in entity_reg.entities.values():
                        if (any_entry.platform != DOMAIN
                                or not (any_entry.unique_id or "").startswith(blid_key + "_cloud_")):
                            continue
                        cloud_key = (any_entry.unique_id or "").split("_cloud_", 1)[1]
                        if any_entry.entity_id.endswith("_" + cloud_key):
                            device_prefix = any_entry.entity_id[: -(len(cloud_key) + 1)]
                            break

                if device_prefix is None:
                    _LOGGER.warning(
                        "Roomba+: could not compute correct entity_id for %s "
                        "(unique_id=%s) — skipping slug fix",
                        wrong_entry.entity_id, uid,
                    )
                    continue

                correct_eid = f"{device_prefix}{correct_eid_suffix}"

                if entity_reg.async_get(correct_eid) is None:
                    entity_reg.async_update_entity(
                        wrong_entry.entity_id, new_entity_id=correct_eid
                    )
                    slug_renamed += 1
                    _LOGGER.info(
                        "Roomba+: renamed language-slug entity %s → %s",
                        wrong_entry.entity_id, correct_eid,
                    )
                else:
                    entity_reg.async_remove(wrong_entry.entity_id)
                    slug_renamed += 1
                    _LOGGER.info(
                        "Roomba+: removed duplicate language-slug entity %s",
                        wrong_entry.entity_id,
                    )

        new_options = dict(config_entry.options)
        new_options.setdefault(CONF_FLOOR, "")
        hass.config_entries.async_update_entry(
            config_entry,
            options=new_options,
            version=12,
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 12 "
            "(floor_label added, %d language-slug entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed,
        )
        current = 12

    if current == 12:
        # v12 → v13 (v2.3.0): fix language-slug entity_ids for entities that
        # were registered without _attr_name in v2.2.x and earlier.
        #
        # Affected entities (unique_id suffix → correct entity_id suffix):
        #   _map                  → _cleaning_map         (image)
        #   _coverage_map         → _coverage_map         (image)
        #   _carpet_boost_select  → _carpet_boost_select  (select)
        #   _raw_state            → _raw_state            (sensor)
        #   _reset_filter         → _reset_filter         (button)
        #   _reset_brush          → _reset_brush          (button)
        #   _reset_battery        → _reset_battery        (button)
        #   _clean_zone           → _clean_zone           (button)
        #   _repeat_mission       → _repeat_mission       (button)
        #   _clean_smart_zone     → _clean_smart_zone     (button)
        #
        # On non-English installs, HA derived the entity_id slug from the
        # translated display name (no _attr_name → HA used translation string
        # as slug). Example on a German install:
        #   image.*_reinigungskarte       → image.*_cleaning_map
        #   button.*_zone_reinigen        → button.*_clean_zone
        #
        # On English installs the slug already matches — the rename is a no-op.
        #
        # The _map suffix is special: the unique_id is "{blid}_map" but the
        # intended entity_id suffix is "_cleaning_map" (not "_map"), so we
        # always rename regardless of locale.
        #
        # Migration strategy: same pattern as v12 (unique_id based, derive
        # device prefix from sibling, idempotent).

        from homeassistant.helpers import entity_registry as er
        entity_reg = er.async_get(hass)
        slug_renamed_13 = 0

        # uid_suffix → (correct eid suffix, platform)
        # Sorted longest-first so "_coverage_map" matches before "_map", etc.
        _V13_RENAMES: dict[str, tuple[str, str]] = {
            "_map":                 ("_cleaning_map",        "image"),
            "_coverage_map":        ("_coverage_map",        "image"),
            "_carpet_boost_select": ("_carpet_boost_select", "select"),
            "_raw_state":           ("_raw_state",           "sensor"),
            "_reset_filter":        ("_reset_filter",        "button"),
            "_reset_brush":         ("_reset_brush",         "button"),
            "_reset_battery":       ("_reset_battery",       "button"),
            "_clean_zone":          ("_clean_zone",          "button"),
            "_repeat_mission":      ("_repeat_mission",      "button"),
            "_clean_smart_zone":    ("_clean_smart_zone",    "button"),
        }
        _V13_RENAMES_SORTED = sorted(
            _V13_RENAMES.items(), key=lambda kv: len(kv[0]), reverse=True
        )

        # Build: uid_suffix → list[(entity_reg_entry, correct_eid_suffix)]
        targets: list[tuple[Any, str, str]] = []
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            for uid_suffix, (correct_eid_suffix, _platform) in _V13_RENAMES_SORTED:
                if uid.endswith(uid_suffix) and not uid.endswith("_cloud" + uid_suffix):
                    targets.append((entry_er, uid_suffix, correct_eid_suffix))
                    break

        # Build full blid → sibling map for prefix derivation
        all_blid_entities: dict[str, list] = {}
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            # Derive blid: everything before the first known suffix match
            for uid_suffix, _ in _V13_RENAMES_SORTED:
                if uid.endswith(uid_suffix):
                    blid_key = uid[: -len(uid_suffix)]
                    all_blid_entities.setdefault(blid_key, []).append(entry_er)
                    break

        for entry_er, uid_suffix, correct_eid_suffix in targets:
            uid = entry_er.unique_id or ""
            blid_key = uid[: -len(uid_suffix)]

            if entry_er.entity_id.endswith(correct_eid_suffix):
                continue  # already correct (English install or already migrated)

            # Derive device prefix from any sibling whose entity_id name (without
            # domain) ends with the correct suffix for its own unique_id.
            # device_prefix = name portion only (no domain), e.g. "roomba"
            device_prefix: str | None = None
            for sibling in all_blid_entities.get(blid_key, []):
                s_uid = sibling.unique_id or ""
                s_name = sibling.entity_id.split(".", 1)[-1]   # strip domain
                for s_suffix, (s_correct, _) in _V13_RENAMES_SORTED:
                    if s_uid.endswith(s_suffix) and not s_uid.endswith("_cloud" + s_suffix):
                        if s_name.endswith(s_correct):
                            device_prefix = s_name[: -len(s_correct)]
                            break
                if device_prefix is not None:
                    break

            # Fallback: any sibling entity whose entity_id name ends with the
            # non-blid tail of its own unique_id gives us the prefix
            if device_prefix is None:
                for any_e in list(entity_reg.entities.values()):
                    if any_e.platform != DOMAIN:
                        continue
                    s_uid = any_e.unique_id or ""
                    if not s_uid.startswith(blid_key):
                        continue
                    tail = s_uid[len(blid_key):]   # e.g. "_lifetime_missions"
                    s_name = any_e.entity_id.split(".", 1)[-1]
                    if tail and s_name.endswith(tail):
                        device_prefix = s_name[: -len(tail)]
                        break

            if not device_prefix:
                _LOGGER.warning(
                    "Roomba+: v13 migration — could not compute prefix for %s "
                    "(uid=%s) — skipping",
                    entry_er.entity_id, uid,
                )
                continue

            correct_eid = f"{entry_er.domain}.{device_prefix}{correct_eid_suffix}"

            if entity_reg.async_get(correct_eid) is None:
                entity_reg.async_update_entity(
                    entry_er.entity_id, new_entity_id=correct_eid
                )
                slug_renamed_13 += 1
                _LOGGER.info(
                    "Roomba+: v13 renamed language-slug entity %s → %s",
                    entry_er.entity_id, correct_eid,
                )
            else:
                entity_reg.async_remove(entry_er.entity_id)
                slug_renamed_13 += 1
                _LOGGER.info(
                    "Roomba+: v13 removed duplicate language-slug entity %s",
                    entry_er.entity_id,
                )

        hass.config_entries.async_update_entry(config_entry, version=13)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 13 "
            "(%d language-slug entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed_13,
        )
        current = 13

    if current == 13:
        # v13 → v14 (v2.5.0): fix locale-dependent entity_id slugs for three
        # sensors added in v2.4.3 whose descriptions had translation_key set,
        # causing HA to generate entity_ids from the translated display name
        # on first registration.
        #
        # Affected sensors (unique_id suffix → correct entity_id suffix):
        #   _signal_noise          → _signal_noise         (DE was: _signalrauschen)
        #   _mission_recharge_time → _recharge_time        (DE was: _ladezeit)
        #   _mission_expire_time   → _mission_expire_time  (DE was: _missionsablauf)
        #
        # Fix: translation_key removed from these descriptions in v2.5.0.
        # Migration: detect entities by unique_id suffix; if entity_id does not
        # end with the expected suffix, derive the device prefix from a sibling
        # entity and rename. Idempotent on English installs.

        from homeassistant.helpers import entity_registry as er

        entity_reg = er.async_get(hass)
        slug_renamed_14 = 0

        # uid_suffix → correct entity_id suffix
        _V14_RENAMES: dict[str, str] = {
            "_signal_noise":          "_signal_noise",
            "_mission_recharge_time": "_recharge_time",
            "_mission_expire_time":   "_mission_expire_time",
        }
        _V14_SORTED = sorted(_V14_RENAMES.items(), key=lambda kv: len(kv[0]), reverse=True)

        # Identify target entities
        targets_14: list[tuple[Any, str, str]] = []
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            for uid_suffix, correct_eid_suffix in _V14_SORTED:
                if uid.endswith(uid_suffix):
                    targets_14.append((entry_er, uid_suffix, correct_eid_suffix))
                    break

        # Build blid → sibling list for device-prefix derivation
        all_blid_entities_14: dict[str, list] = {}
        for entry_er in list(entity_reg.entities.values()):
            if entry_er.platform != DOMAIN:
                continue
            uid = entry_er.unique_id or ""
            tail = uid  # any tail we can strip to find the device prefix
            for uid_suffix, _ in _V14_SORTED:
                if uid.endswith(uid_suffix):
                    blid_key = uid[: -len(uid_suffix)]
                    all_blid_entities_14.setdefault(blid_key, []).append(entry_er)
                    break

        for entry_er, uid_suffix, correct_eid_suffix in targets_14:
            uid = entry_er.unique_id or ""
            blid_key = uid[: -len(uid_suffix)]

            if entry_er.entity_id.endswith(correct_eid_suffix):
                continue  # already has the correct English suffix

            # Derive device prefix from a sibling entity whose entity_id
            # name already ends with the tail of its own unique_id.
            device_prefix: str | None = None
            for sibling in all_blid_entities_14.get(blid_key, []):
                s_uid = sibling.unique_id or ""
                s_name = sibling.entity_id.split(".", 1)[-1]
                tail = s_uid[len(blid_key):]  # e.g. "_lifetime_missions"
                if tail and s_name.endswith(tail):
                    device_prefix = s_name[: -len(tail)]
                    break

            # Second fallback: any sibling entity for the same blid
            if device_prefix is None:
                for any_e in list(entity_reg.entities.values()):
                    if any_e.platform != DOMAIN:
                        continue
                    s_uid = any_e.unique_id or ""
                    if not s_uid.startswith(blid_key):
                        continue
                    tail = s_uid[len(blid_key):]
                    s_name = any_e.entity_id.split(".", 1)[-1]
                    if tail and s_name.endswith(tail):
                        device_prefix = s_name[: -len(tail)]
                        break

            if not device_prefix:
                _LOGGER.warning(
                    "Roomba+: v14 migration — could not compute prefix for %s "
                    "(uid=%s) — skipping",
                    entry_er.entity_id, uid,
                )
                continue

            correct_eid = f"{entry_er.domain}.{device_prefix}{correct_eid_suffix}"

            if entity_reg.async_get(correct_eid) is None:
                entity_reg.async_update_entity(
                    entry_er.entity_id, new_entity_id=correct_eid
                )
                slug_renamed_14 += 1
                _LOGGER.info(
                    "Roomba+: v14 renamed locale-slug entity %s → %s",
                    entry_er.entity_id, correct_eid,
                )
            else:
                entity_reg.async_remove(entry_er.entity_id)
                slug_renamed_14 += 1
                _LOGGER.info(
                    "Roomba+: v14 removed duplicate locale-slug entity %s "
                    "(target %s already existed)",
                    entry_er.entity_id, correct_eid,
                )

        hass.config_entries.async_update_entry(config_entry, version=14)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 14 "
            "(%d locale-slug entity_id(s) processed — see v15 for corrected fix)",
            config_entry.entry_id, slug_renamed_14,
        )
        current = 14

    if current == 14:
        # v14 → v15 (v2.5.0 hotfix): redo the locale-slug entity_id fix using the
        # device registry instead of the sibling-based prefix derivation that was
        # used in v13→v14.  The sibling approach failed because German entity_id
        # suffixes (e.g. "_ladezeit") do not match the English uid suffixes
        # (e.g. "_mission_recharge_time"), so no sibling ever produced a device
        # prefix and the three affected entities were silently skipped.
        #
        # New approach: look up each entity's device via device_registry, compute
        # device_slug = slugify(device.name_by_user or device.name), then construct
        # the correct entity_id as "sensor.{device_slug}_{en_slug}".  This works
        # for any locale.
        #
        # Affected sensors (unique_id suffix → correct English entity_id suffix):
        #   _signal_noise          → _signal_noise        (DE was: _signalrauschen)
        #   _mission_recharge_time → _recharge_time       (DE was: _ladezeit)
        #   _mission_expire_time   → _mission_expire_time  (DE was: _missionsablauf)

        from homeassistant.helpers import (
            entity_registry as er_helper,
            device_registry as dr_helper,
        )
        from homeassistant.util import slugify as _slugify

        entity_reg = er_helper.async_get(hass)
        device_reg  = dr_helper.async_get(hass)
        slug_renamed_15 = 0

        # uid_suffix → correct English entity_id slug (derived from name= value)
        _V15_TARGETS: list[tuple[str, str]] = [
            ("_signal_noise",          "signal_noise"),
            ("_mission_recharge_time", "recharge_time"),
            ("_mission_expire_time",   "mission_expire_time"),
        ]

        # Use the blid from config entry data to construct exact unique_ids
        # for the three affected sensors, then search the full entity registry
        # by exact unique_id.  This bypasses all platform/domain filters which
        # were silently returning zero results on HA 2026.x installs.
        blid = config_entry.data.get("blid", "")
        if not blid:
            _LOGGER.warning(
                "Roomba+: v15 migration — blid not in config entry data, "
                "cannot locate locale-slug entities"
            )
        else:
            # One-pass unique_id → EntityEntry index (all entries, no filtering)
            uid_index: dict[str, Any] = {
                e.unique_id: e
                for e in entity_reg.entities.values()
                if e.unique_id
            }
            _LOGGER.debug(
                "Roomba+: v15 migration — registry has %d entries, blid=%s…",
                len(uid_index), blid[:8],
            )

            for uid_suffix, en_slug in _V15_TARGETS:
                target_uid = f"{blid}{uid_suffix}"
                entry_er = uid_index.get(target_uid)

                if entry_er is None:
                    _LOGGER.debug(
                        "Roomba+: v15 — entity not in registry (uid=%s) — skip", target_uid
                    )
                    continue

                eid = entry_er.entity_id
                expected_suffix = f"_{en_slug}"
                if eid.endswith(expected_suffix):
                    _LOGGER.debug(
                        "Roomba+: v15 — %s already correct — skip", eid
                    )
                    continue

                # Derive correct entity_id from device name
                device = device_reg.async_get(entry_er.device_id) if entry_er.device_id else None
                if device is None:
                    _LOGGER.warning(
                        "Roomba+: v15 — no device for %s (uid=%s) — skip", eid, target_uid
                    )
                    continue

                device_name = device.name_by_user or device.name or ""
                device_slug = _slugify(device_name)
                if not device_slug:
                    _LOGGER.warning(
                        "Roomba+: v15 — empty device slug for %s — skip", eid
                    )
                    continue

                correct_eid = f"sensor.{device_slug}{expected_suffix}"
                existing = entity_reg.async_get(correct_eid)

                if existing is not None and existing.entity_id != eid:
                    entity_reg.async_remove(eid)
                    slug_renamed_15 += 1
                    _LOGGER.info(
                        "Roomba+: v15 removed locale-slug duplicate %s "
                        "(target %s already exists)", eid, correct_eid,
                    )
                else:
                    entity_reg.async_update_entity(eid, new_entity_id=correct_eid)
                    slug_renamed_15 += 1
                    _LOGGER.info(
                        "Roomba+: v15 renamed locale-slug %s → %s", eid, correct_eid,
                    )

        hass.config_entries.async_update_entry(config_entry, version=15)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 15 "
            "(%d locale-slug entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed_15,
        )
        current = 15

    if current == 15:
        # v15 → v16 (v2.5.0 hotfix-2): rename battery_capacity_retention whose
        # German translation "Wartung – Akkukapazität" produced the entity_id
        # sensor.*_wartung_akkukapazitat in old HA versions.
        #
        # Also handles any remaining locale-slug entity_ids not caught by v14/v15
        # by expanding the target list, still using the blid-based exact-uid lookup
        # introduced in v15 so the platform filter bypass remains in effect.
        #
        # Confirmed affected on user's HA 2026.6 / v2.5.0 (June 2026):
        #   battery_capacity_retention → sensor.*_wartung_akkukapazitat
        #
        # The sensors signal_noise / mission_recharge_time / mission_expire_time
        # are confirmed absent from the entity registry on the Roomba 980 because
        # firmware v2.4.17-138 does not expose the required data fields — no entry
        # to rename, consistent with v15 reporting 0 renames.

        from homeassistant.helpers import (
            entity_registry as er_helper_16,
            device_registry as dr_helper_16,
        )
        from homeassistant.util import slugify as _slugify_16

        entity_reg_16 = er_helper_16.async_get(hass)
        device_reg_16  = dr_helper_16.async_get(hass)
        slug_renamed_16 = 0

        # Complete set: v15 targets + battery_capacity_retention
        _V16_TARGETS: list[tuple[str, str]] = [
            ("_signal_noise",           "signal_noise"),
            ("_mission_recharge_time",  "recharge_time"),
            ("_mission_expire_time",    "mission_expire_time"),
            ("_battery_capacity_retention", "battery_capacity_retention"),
        ]

        blid_16 = config_entry.data.get("blid", "")
        if not blid_16:
            _LOGGER.warning(
                "Roomba+: v16 migration — blid not in config entry data"
            )
        else:
            uid_index_16: dict[str, Any] = {
                e.unique_id: e
                for e in entity_reg_16.entities.values()
                if e.unique_id
            }
            _LOGGER.debug(
                "Roomba+: v16 migration — registry %d entries, blid=%s…",
                len(uid_index_16), blid_16[:8],
            )

            for uid_suffix, en_slug in _V16_TARGETS:
                target_uid = f"{blid_16}{uid_suffix}"
                entry_er = uid_index_16.get(target_uid)

                if entry_er is None:
                    _LOGGER.debug(
                        "Roomba+: v16 — uid=%s not in registry — skip", target_uid
                    )
                    continue

                eid = entry_er.entity_id
                expected_suffix = f"_{en_slug}"
                if eid.endswith(expected_suffix):
                    _LOGGER.debug("Roomba+: v16 — %s already correct — skip", eid)
                    continue

                device = device_reg_16.async_get(entry_er.device_id) if entry_er.device_id else None
                if device is None:
                    _LOGGER.warning("Roomba+: v16 — no device for %s — skip", eid)
                    continue

                device_name = device.name_by_user or device.name or ""
                device_slug = _slugify_16(device_name)
                if not device_slug:
                    _LOGGER.warning("Roomba+: v16 — empty device slug for %s — skip", eid)
                    continue

                correct_eid = f"sensor.{device_slug}{expected_suffix}"
                existing = entity_reg_16.async_get(correct_eid)

                if existing is not None and existing.entity_id != eid:
                    entity_reg_16.async_remove(eid)
                    slug_renamed_16 += 1
                    _LOGGER.info(
                        "Roomba+: v16 removed duplicate %s (target %s exists)",
                        eid, correct_eid,
                    )
                else:
                    entity_reg_16.async_update_entity(eid, new_entity_id=correct_eid)
                    slug_renamed_16 += 1
                    _LOGGER.info(
                        "Roomba+: v16 renamed %s → %s", eid, correct_eid,
                    )

        hass.config_entries.async_update_entry(config_entry, version=16)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 16 "
            "(%d locale-slug entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed_16,
        )
        current = 16

    if current == 16:
        # v16 → v17: final locale-slug fix using entity_id suffix search.
        #
        # Root cause: the affected entity was registered in a very old Roomba+
        # version with a unique_id format that no longer matches the current
        # {blid}_{key} pattern, so all uid-based lookups (v13–v16) returned
        # nothing.  The only reliable anchor is the German entity_id suffix
        # itself (e.g. "*_wartung_akkukapazitat").
        #
        # Fix: iterate all entities for this config entry, match any that end
        # with a known German suffix, rename the entity_id to the English
        # equivalent AND patch the unique_id to the current {blid}_{key} format
        # so future HA startups find the entity correctly without creating a
        # duplicate.
        #
        # Confirmed affected entity (June 2026):
        #   sensor.*_wartung_akkukapazitat  (battery_capacity_retention)
        #   DE translation: "Wartung – Akkukapazität"  ← slugifies to this
        #
        # The full de.json-derived German→English map is included so any
        # other locale-slug survivors (on other users' installs) are also fixed.

        from homeassistant.helpers import (
            entity_registry as er_helper_17,
            device_registry as dr_helper_17,
        )
        from homeassistant.util import slugify as _slugify_17

        entity_reg_17 = er_helper_17.async_get(hass)
        device_reg_17  = dr_helper_17.async_get(hass)
        blid_17 = config_entry.data.get("blid", "")
        slug_renamed_17 = 0

        # German entity_id suffix → (sensor key, entity domain)
        # Generated from de.json: all keys where slugify(DE name) != key.
        _V17_DE_EN: dict[str, tuple[str, str]] = {
            # sensor entities
            "_wartung_akkukapazitat":                  ("battery_capacity_retention", "sensor"),
            "_wartung_ladezyklen":                     ("battery_cycles",              "sensor"),
            "_wartung_akku_zuletzt_gewechselt":        ("battery_last_replaced",       "sensor"),
            "_wartung_bursten_tage_bis_fallig":        ("brush_days_until_due",        "sensor"),
            "_wartung_bursten_zuletzt_gewechselt":     ("brush_last_replaced",         "sensor"),
            "_wartung_bursten":                        ("brush_remaining_hours",       "sensor"),
            "_wartung_bursten_verschleissrate":        ("brush_wear_rate",             "sensor"),
            "_wartung_gesch_akkuende":                 ("estimated_battery_eol",       "sensor"),
            "_wartung_filter_tage_bis_fallig":         ("filter_days_until_due",       "sensor"),
            "_wartung_filter_zuletzt_gewechselt":      ("filter_last_replaced",        "sensor"),
            "_wartung_filter":                         ("filter_remaining_hours",      "sensor"),
            "_wartung_filter_verschleissrate":         ("filter_wear_rate",            "sensor"),
            "_ladezeit":                               ("mission_recharge_time",       "sensor"),
            "_missionsablauf":                         ("mission_expire_time",         "sensor"),
            "_signalrauschen":                         ("signal_noise",                "sensor"),
            "_mission_aktiv":                          ("mission_active",              "binary_sensor"),
        }

        # Sort longest suffix first to avoid prefix collisions (e.g. _wartung_bursten
        # vs _wartung_bursten_tage_bis_fallig)
        _V17_SORTED = sorted(_V17_DE_EN.items(), key=lambda kv: len(kv[0]), reverse=True)

        for entry_er in list(entity_reg_17.entities.values()):
            if entry_er.config_entry_id != config_entry.entry_id:
                continue

            eid = entry_er.entity_id

            for de_suffix, (en_key, domain) in _V17_SORTED:
                if not eid.endswith(de_suffix):
                    continue

                # Derive correct entity_id from device name
                device = device_reg_17.async_get(entry_er.device_id) if entry_er.device_id else None
                if device is None:
                    _LOGGER.warning(
                        "Roomba+: v17 — no device for %s — skip", eid
                    )
                    break

                device_name = device.name_by_user or device.name or ""
                device_slug = _slugify_17(device_name)
                if not device_slug:
                    _LOGGER.warning(
                        "Roomba+: v17 — empty device slug for %s — skip", eid
                    )
                    break

                correct_eid = f"{domain}.{device_slug}_{en_key}"
                correct_uid = f"{blid_17}_{en_key}" if blid_17 else None

                existing = entity_reg_17.async_get(correct_eid)
                if existing is not None and existing.entity_id != eid:
                    # Target entity_id already taken — remove the stale entry
                    entity_reg_17.async_remove(eid)
                    slug_renamed_17 += 1
                    _LOGGER.info(
                        "Roomba+: v17 removed stale locale-slug %s "
                        "(target %s already exists)", eid, correct_eid,
                    )
                else:
                    kwargs: dict[str, Any] = {"new_entity_id": correct_eid}
                    if correct_uid and entry_er.unique_id != correct_uid:
                        kwargs["new_unique_id"] = correct_uid
                    entity_reg_17.async_update_entity(eid, **kwargs)
                    slug_renamed_17 += 1
                    _LOGGER.info(
                        "Roomba+: v17 renamed locale-slug %s → %s (uid→%s)",
                        eid, correct_eid, correct_uid,
                    )
                break  # matched — move to next entity

        # Phase 2 — wrong device-name prefix.
        # When the device is renamed in HA, existing entity_ids are NOT updated.
        # Entities first registered under the old device name keep the old prefix
        # (e.g. "abstellraum_roomba_980_og_*") while newer entities use the
        # current name ("roomba_980_og_*").
        #
        # Detection: entity_id prefix doesn't match {domain}.{current_device_slug}_
        # Anchor: unique_id starts with {blid}_ → entity name = uid[len(blid)+1:]
        # Only renames when we can verify the sensor key from the unique_id.

        for entry_er in list(entity_reg_17.entities.values()):
            if entry_er.config_entry_id != config_entry.entry_id:
                continue
            if not entry_er.device_id:
                continue
            if not entry_er.unique_id:
                continue
            uid = entry_er.unique_id
            if not (blid_17 and uid.startswith(f"{blid_17}_")):
                continue  # unique_id in old format — can't derive key safely

            entity_name = uid[len(blid_17) + 1:]  # e.g. "total_energy_consumed"
            domain = entry_er.entity_id.split(".", 1)[0]
            device = device_reg_17.async_get(entry_er.device_id)
            if device is None:
                continue

            device_name = device.name_by_user or device.name or ""
            device_slug = _slugify_17(device_name)
            if not device_slug:
                continue

            expected_eid = f"{domain}.{device_slug}_{entity_name}"
            if entry_er.entity_id == expected_eid:
                continue  # already correct

            existing = entity_reg_17.async_get(expected_eid)
            if existing is not None and existing.entity_id != entry_er.entity_id:
                # Target already taken — skip (the active entity is already there)
                _LOGGER.debug(
                    "Roomba+: v17 ph2 — target %s already exists, skipping %s",
                    expected_eid, entry_er.entity_id,
                )
                continue

            entity_reg_17.async_update_entity(
                entry_er.entity_id, new_entity_id=expected_eid
            )
            slug_renamed_17 += 1
            _LOGGER.info(
                "Roomba+: v17 renamed old-device-prefix %s → %s",
                entry_er.entity_id, expected_eid,
            )

        hass.config_entries.async_update_entry(config_entry, version=17)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 17 "
            "(%d locale-slug entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed_17,
        )
        current = 17

    if current == 17:
        # v17 → v18 (v2.5.0 hotfix-3): two remaining problems from v17.
        #
        # Problem A — wartung_akkukapazitat was renamed in v17 phase 1 but
        # immediately re-created by HA because battery_capacity_retention still
        # had translation_key set, making German HA generate the German slug on
        # every fresh entity registration.  Fixed in v2.5.0: translation_key
        # removed from battery_capacity_retention descriptor.  The orphaned
        # German-slug entity must be removed so there is no duplicate when HA
        # registers the sensor under the English entity_id.
        #
        # Problem B — the three entities with the old device-name prefix
        # (abstellraum_roomba_980_og_*) were not renamed by v17 phase 2
        # because the uid-prefix check silently found nothing.  Replaced here
        # with entity_id substring matching — the same technique that worked in
        # v17 phase 1.
        #
        # Unified algorithm (single pass over all entities for this config entry):
        #
        #   1. Skip entities that already start with {domain}.{device_slug}_
        #   2. German suffix: rename using the DE→EN map (same as v17)
        #      If the target entity_id already exists → remove the stale one.
        #   3. Old device prefix: find {device_slug}_ as a substring, extract
        #      the entity_name from what follows, rename to correct prefix.
        #      If the target already exists → remove the stale one.

        from homeassistant.helpers import (
            entity_registry as er_helper_18,
            device_registry as dr_helper_18,
        )
        from homeassistant.util import slugify as _slugify_18

        entity_reg_18 = er_helper_18.async_get(hass)
        device_reg_18  = dr_helper_18.async_get(hass)
        slug_renamed_18 = 0

        # German suffix → (English sensor key, entity domain) — full map from de.json
        _V18_DE_EN: dict[str, tuple[str, str]] = {
            "_wartung_akkukapazitat":               ("battery_capacity_retention", "sensor"),
            "_wartung_ladezyklen":                  ("battery_cycles",             "sensor"),
            "_wartung_akku_zuletzt_gewechselt":     ("battery_last_replaced",      "sensor"),
            "_wartung_bursten_tage_bis_fallig":     ("brush_days_until_due",       "sensor"),
            "_wartung_bursten_zuletzt_gewechselt":  ("brush_last_replaced",        "sensor"),
            "_wartung_bursten":                     ("brush_remaining_hours",      "sensor"),
            "_wartung_bursten_verschleissrate":     ("brush_wear_rate",            "sensor"),
            "_wartung_gesch_akkuende":              ("estimated_battery_eol",      "sensor"),
            "_wartung_filter_tage_bis_fallig":      ("filter_days_until_due",      "sensor"),
            "_wartung_filter_zuletzt_gewechselt":   ("filter_last_replaced",       "sensor"),
            "_wartung_filter":                      ("filter_remaining_hours",     "sensor"),
            "_wartung_filter_verschleissrate":      ("filter_wear_rate",           "sensor"),
            "_ladezeit":                            ("mission_recharge_time",      "sensor"),
            "_missionsablauf":                      ("mission_expire_time",        "sensor"),
            "_signalrauschen":                      ("signal_noise",               "sensor"),
            "_mission_aktiv":                       ("mission_active",             "binary_sensor"),
        }
        _V18_DE_SORTED = sorted(_V18_DE_EN.items(), key=lambda kv: len(kv[0]), reverse=True)

        def _v18_rename_or_remove(old_eid: str, correct_eid: str) -> bool:
            """Rename old_eid → correct_eid; if target exists, remove old_eid.
            Returns True when an action was taken."""
            if old_eid == correct_eid:
                return False
            existing = entity_reg_18.async_get(correct_eid)
            if existing is not None:
                entity_reg_18.async_remove(old_eid)
                _LOGGER.info(
                    "Roomba+: v18 removed stale %s (target %s already exists)",
                    old_eid, correct_eid,
                )
            else:
                entity_reg_18.async_update_entity(old_eid, new_entity_id=correct_eid)
                _LOGGER.info("Roomba+: v18 renamed %s → %s", old_eid, correct_eid)
            return True

        for entry_er in list(entity_reg_18.entities.values()):
            if entry_er.config_entry_id != config_entry.entry_id:
                continue

            eid = entry_er.entity_id
            domain, _, eid_body = eid.partition(".")

            device = device_reg_18.async_get(entry_er.device_id) if entry_er.device_id else None
            if device is None:
                continue
            device_name = device.name_by_user or device.name or ""
            device_slug = _slugify_18(device_name)
            if not device_slug:
                continue

            # Skip entities that already have the correct device prefix
            if eid_body.startswith(f"{device_slug}_"):
                continue

            # Step A: German suffix → English key rename
            handled = False
            for de_suffix, (en_key, en_domain) in _V18_DE_SORTED:
                if eid.endswith(de_suffix):
                    correct_eid = f"{en_domain}.{device_slug}_{en_key}"
                    if _v18_rename_or_remove(eid, correct_eid):
                        slug_renamed_18 += 1
                    handled = True
                    break

            if handled:
                continue

            # Step B: old device prefix — find device_slug substring, extract entity_name
            marker = f"{device_slug}_"
            idx = eid_body.find(marker)
            if idx < 0:
                continue  # device_slug not found in entity_id body — can't determine name

            entity_name = eid_body[idx + len(marker):]
            if not entity_name:
                continue

            correct_eid = f"{domain}.{device_slug}_{entity_name}"
            if _v18_rename_or_remove(eid, correct_eid):
                slug_renamed_18 += 1

        hass.config_entries.async_update_entry(config_entry, version=18)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 18 "
            "(%d entity_id(s) fixed)",
            config_entry.entry_id, slug_renamed_18,
        )
        current = 18

    if current == 18:
        # v18 → v19: remove orphaned entity registry entries that have no
        # unique_id and that are left over from old Roomba+ versions.
        #
        # Root cause: very old Roomba+ versions registered entities without
        # unique_ids. When unique_ids were added later, HA created new registry
        # entries for the new unique_ids, leaving the old no-uid entries as
        # orphans. The v18 migration renamed them but since they had no uid,
        # HA registered fresh entities alongside them, resulting in duplicates.
        #
        # Fix: remove all entities for this config entry that:
        #   a) have unique_id = None  (no-uid orphans — safe to delete; the
        #      active sensor will create a correct entry via uid lookup), OR
        #   b) end with a known German locale suffix  (stale German-slug entry
        #      that keeps being re-created in the same HA session due to HA's
        #      "recently deleted" entity_id reuse mechanism — removing it in a
        #      separate migration version ensures the deletion persists across
        #      a cold restart, after which the sensor registers with the correct
        #      English entity_id from name=).

        from homeassistant.helpers import entity_registry as er_helper_19

        entity_reg_19 = er_helper_19.async_get(hass)
        removed_19 = 0

        # German suffixes to catch any remaining locale-slug entries
        _V19_DE_SUFFIXES = tuple(
            "_" + k for k in [
                "wartung_akkukapazitat", "wartung_ladezyklen",
                "wartung_akku_zuletzt_gewechselt", "wartung_bursten",
                "wartung_bursten_tage_bis_fallig",
                "wartung_bursten_zuletzt_gewechselt",
                "wartung_bursten_verschleissrate", "wartung_gesch_akkuende",
                "wartung_filter", "wartung_filter_tage_bis_fallig",
                "wartung_filter_zuletzt_gewechselt",
                "wartung_filter_verschleissrate",
                "ladezeit", "missionsablauf", "signalrauschen",
            ]
        )

        for entry_er in list(entity_reg_19.entities.values()):
            if entry_er.config_entry_id != config_entry.entry_id:
                continue

            remove = False

            # Case a: no unique_id — orphaned entry from old Roomba+ version
            if entry_er.unique_id is None:
                remove = True

            # Case b: German locale slug suffix
            elif entry_er.entity_id.endswith(_V19_DE_SUFFIXES):
                remove = True

            if remove:
                entity_reg_19.async_remove(entry_er.entity_id)
                removed_19 += 1
                _LOGGER.info(
                    "Roomba+: v19 removed orphaned/stale entity %s (uid=%s)",
                    entry_er.entity_id, entry_er.unique_id,
                )

        hass.config_entries.async_update_entry(config_entry, version=19)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 19 "
            "(%d orphaned entity_id(s) removed)",
            config_entry.entry_id, removed_19,
        )
        current = 19

    if current == 19:
        # v19 → v20: no-op version bump.
        #
        # The original v20 migration used uid suffixes to compute expected
        # entity_ids, but several uids themselves contained the wrong key
        # (e.g. "cloud_recent_dirt_events" instead of "recent_dirt_events")
        # because a previous buggy task had mutated them.  This caused v20 to
        # rename CORRECT entity_ids to WRONG ones (reverse direction).
        #
        # The corrective pass is in v21 (suffix-based, direction-safe).
        hass.config_entries.async_update_entry(config_entry, version=20)
        _LOGGER.info("Roomba+: migrated entry %s to version 20 (no-op)",
                     config_entry.entry_id)
        current = 20

    if current == 20:
        # v20 → v21: suffix-based entity_id correction.
        #
        # Fixes entity_ids that were corrupted by the bad v20 migration
        # (which renamed correct → wrong using uid-based keys) and by a
        # previous buggy post-setup task that added "cloud_" prefixes or
        # stripped descriptive words from entity_id suffixes.
        #
        # Uses entity_id SUFFIX matching (longest-first, no uid lookup)
        # so the direction is always correct regardless of uid content.

        from homeassistant.helpers import entity_registry as er_helper_21

        entity_reg_21 = er_helper_21.async_get(hass)
        renamed_21 = 0

        # Suffix corrections — sorted longest-first to prevent partial matches.
        # Format: wrong_suffix → correct_suffix
        _FIXES_21 = sorted({
            "_cloud_recent_recharge_fraction": "_recent_recharge_fraction",
            "_cloud_recent_completion_rate":   "_recent_completion_rate",
            "_cloud_recent_cleaning_speed":    "_recent_cleaning_speed",
            "_cloud_cleaning_speed_trend":     "_cleaning_speed_trend",
            "_cloud_recent_coverage_pct":      "_recent_coverage_pct",
            "_cloud_recent_dirt_density":      "_recent_dirt_density",
            "_cloud_recent_dirt_events":       "_recent_dirt_events",
            "_cloud_recent_error_code":        "_recent_error_code",
            "_cloud_recent_error_time":        "_recent_error_time",
            "_cloud_recent_recharges":         "_recent_recharges",
            "_cloud_lifetime_missions":        "_lifetime_missions",
            "_cloud_recent_area_30d":          "_recent_area_30d",
            "_cloud_recent_time_30d":          "_recent_time_30d",
            "_cloud_recent_wifi_floor":        "_recent_wifi_floor",
            "_cloud_recent_wifi_stability":    "_recent_wifi_stability",
        }.items(), key=lambda kv: len(kv[0]), reverse=True)

        for entry_er in list(entity_reg_21.entities.values()):
            if entry_er.config_entry_id != config_entry.entry_id:
                continue
            eid = entry_er.entity_id
            domain = eid.split(".", 1)[0]
            new_eid = None

            # Cloud-prefix sensors
            for wrong, correct in _FIXES_21:
                if eid.endswith(wrong):
                    new_eid = eid[: -len(wrong)] + correct
                    break

            # battery → battery_level (sensor only)
            if new_eid is None and domain == "sensor" and eid.endswith("_battery"):
                new_eid = eid + "_level"

            # image _map → _cleaning_map
            # Guard: must NOT already end with _cleaning_map or _coverage_map
            if new_eid is None and domain == "image":
                if (eid.endswith("_map")
                        and not eid.endswith("_cleaning_map")
                        and not eid.endswith("_coverage_map")):
                    new_eid = eid[:-4] + "_cleaning_map"

            if new_eid is None or new_eid == eid:
                continue

            existing = entity_reg_21.async_get(new_eid)
            if existing is not None and existing.entity_id != eid:
                entity_reg_21.async_remove(new_eid)
                _LOGGER.info("Roomba+: v21 removed zombie %s", new_eid)

            entity_reg_21.async_update_entity(eid, new_entity_id=new_eid)
            renamed_21 += 1
            _LOGGER.info("Roomba+: v21 renamed %s → %s", eid, new_eid)

        hass.config_entries.async_update_entry(config_entry, version=21)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 21 (%d entity_id(s) corrected)",
            config_entry.entry_id, renamed_21,
        )
        current = 21

    if current == 21:
        # v21 → v22: set demand_clean_multiplier default for existing entries.
        # Prior versions may have demand_cleaning_enabled=True without the
        # multiplier key (added in v2.6.0 config flow). Defaulting here ensures
        # existing demand-cleaning users keep their current behaviour (1.5×).
        from .dirt_threshold_manager import TRIGGER_MULTIPLIER_DEFAULT
        new_options = dict(config_entry.options)
        new_options.setdefault("demand_clean_multiplier", TRIGGER_MULTIPLIER_DEFAULT)
        hass.config_entries.async_update_entry(
            config_entry, options=new_options, version=22
        )
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 22 "
            "(demand_clean_multiplier default set)",
            config_entry.entry_id,
        )
        current = 22

    if current == config_entry.version:
        _LOGGER.debug(
            "Roomba+: config entry %s already at version %d — no migration needed",
            config_entry.entry_id, current,
        )

    return True


# v2.3.0 F8 — UmfAligner re-alignment helpers ─────────────────────────────────

async def _async_seed_l5_from_archive(
    hass: Any,
    entry_id: str,
    mission_archive: "MissionArchive",
    robot_profile_store: "RobotProfileStore",
) -> None:
    """Seed per-room dirt index (L5) from full ARC1 archive history.

    L5-ARC (v2.8.0) — One-time bootstrap of room_dirt_index EMA over the
    complete cloud mission history, replacing the cold-start behaviour where
    L5 converges slowly from zero after a fresh install.

    Processes records oldest-to-newest so the EMA weights recent missions most
    heavily (same update rule as the incremental path).

    Guards:
      - Skips when room_dirt_index is already populated (avoid re-seeding).
      - Skips when archive initial load is not yet complete.
      - Skips when archive is empty.
    """
    if robot_profile_store.room_dirt_index:
        return  # already seeded — incremental path handles new missions
    if not mission_archive.initial_load_done:
        return  # archive back-fill still running; will be seeded next restart
    if mission_archive.record_count == 0:
        return

    from .const import SQFT_TO_M2

    seeded_count = 0
    for record in mission_archive.all_derived_oldest_first():
        rooms_completed: dict = record.get("rooms_completed") or {}
        for rid, data in rooms_completed.items():
            # Bug-hunt (v2.8.0): this function is awaited directly inside
            # async_setup_entry (not via hass.async_create_task) — an
            # uncaught exception here fails integration setup entirely,
            # not just this one feature. A corrupted/hand-edited persisted
            # archive record could have a non-dict value for a room entry
            # even though mission_archive.py's own writer always produces
            # dicts; defend at the read boundary rather than trusting the
            # storage file's shape forever.
            if not isinstance(data, dict):
                continue
            passes = int(data.get("passes") or 0)
            area_sqft = float(data.get("area") or 0)   # 'or 0' handles area=None
            area_m2 = area_sqft * SQFT_TO_M2
            if rid and passes > 0 and area_m2 > 0:
                robot_profile_store.update_room_dirt_index(rid, passes, area_m2)
                seeded_count += 1

    if robot_profile_store.room_dirt_index:
        await robot_profile_store.async_save(hass, entry_id)
        _LOGGER.info(
            "L5-ARC: seeded room_dirt_index for %d room(s) from %d archive "
            "mission(s) for entry %s",
            len(robot_profile_store.room_dirt_index),
            mission_archive.record_count,
            entry_id,
        )


async def _async_seed_l3_from_archive(
    mission_archive: "MissionArchive",
    mission_store: "MissionStore",
) -> None:
    """Seed MissionStore.archive_baseline from ARC1 full history.

    L3-ARC (v2.8.0) — Computes the statistical anomaly-detection baseline
    (duration mean/std, area mean/std, dirt p75) from the complete cloud
    mission history and injects it into MissionStore.archive_baseline.

    This allows consecutive_anomalous to detect anomalies even during the
    first weeks of use when the local MQTT store has < 20 missions.

    Guards:
      - Skips when archive initial load is not complete.
      - Skips when archive has < 20 completed missions (compute_archive_stats
        returns None).

    Not persisted — recomputed at each startup to reflect latest archive.
    """
    if not mission_archive.initial_load_done:
        return
    if mission_archive.record_count < 20:
        return

    from .mission_store import MissionStore

    baseline = MissionStore.compute_archive_stats(
        mission_archive.all_derived_oldest_first()
    )
    if baseline is not None:
        mission_store.archive_baseline = baseline
        _LOGGER.info(
            "L3-ARC: archive baseline set from %d record(s) — "
            "duration_mean=%.1f std=%.1f area_mean=%s dirt_p75=%s",
            mission_archive.record_count,
            baseline["duration_mean"],
            baseline["duration_std"],
            f"{baseline['area_mean']:.1f}" if baseline["area_mean"] else "n/a",
            f"{baseline['dirt_p75']:.1f}" if baseline["dirt_p75"] else "n/a",
        )


async def _async_update_robot_profile_store(
    hass: Any,
    entry: "RoombaConfigEntry",
    mission_store: "MissionStore",
    robot_profile_store: "RobotProfileStore",
) -> None:
    """Update RobotProfileStore from the latest MissionStore and GridStore data.

    v2.6.0 L5/L6 — called after each successful cloud refresh so the learned
    state stays fresh. Saves the store only when at least one value changes.

    L5: extracts timeline.finEvents room passCount data from the most recent
    merged record and calls update_room_dirt_index() for each completed room.

    L6: reads edge_coverage_ratio from GridStore (if available) and calls
    update_coverage_baseline() so the navigation efficiency baseline converges.
    """
    from .const import SQFT_TO_M2

    changed = False

    # ── L5: per-room dirtiness from latest record's timeline ─────────────────
    records = mission_store.query(days=1)  # last 24 h — pick the most recent
    if records:
        latest = records[-1]
        timeline = latest.get("timeline") or {}
        fin_events = timeline.get("finEvents", [])
        for event in fin_events:
            if event.get("type") != "room":
                continue
            room = event.get("room", {})
            # Only status=0 (pass complete) or status=6 (post-error recovery)
            if room.get("status") not in (0, 6):
                continue
            rid = str(room.get("rid", ""))
            pass_count = int(room.get("passCount", 0))
            area_sqft = room.get("totalArea") or room.get("area") or 0
            area_m2 = float(area_sqft) * SQFT_TO_M2
            if rid and pass_count > 0 and area_m2 > 0:
                robot_profile_store.update_room_dirt_index(rid, pass_count, area_m2)
                changed = True

    # ── L6: navigation efficiency baseline from GridStore ─────────────────────
    gs = getattr(entry.runtime_data, "grid_store", None)
    if gs is not None:
        try:
            ratio = gs.edge_coverage_ratio()
            if ratio is not None and ratio > 0:
                robot_profile_store.update_coverage_baseline(ratio)
                changed = True
        except Exception:  # noqa: BLE001
            pass

    # ── J: lifetime sqft staleness tracking ────────────────────────────────
    # v2.9.0 — field-confirmed: bbrun.sqft/runtimeStats.sqft can remain
    # frozen for weeks while the robot keeps actively cleaning and every
    # OTHER bbrun.* counter keeps incrementing normally. We cannot make the
    # firmware send fresher data, but we CAN detect and surface "this
    # number hasn't changed in N days" via the total_cleaned_area sensor's
    # extra_attributes_fn, instead of silently displaying a stale number as
    # if it were a live reading.
    try:
        _state = roomba_reported_state(entry.runtime_data.roomba)
        _bbrun = _state.get("bbrun", {})
        _runtime = _state.get("runtimeStats", {})
        _sqft = _runtime.get("sqft", _bbrun.get("sqft"))
        if _sqft is not None:
            if robot_profile_store.update_lifetime_sqft_tracking(float(_sqft)):
                changed = True
    except Exception:  # noqa: BLE001
        pass

    if changed:
        await robot_profile_store.async_save(hass, entry.entry_id)


# ── GS-SMART-UMF (v2.7.0) — Bootstrap UmfAligner from cloud traversal events ──

def _extract_traversal_umf_positions(
    records: list[dict],
    aligner: Any,
    min_missions: int = 3,
) -> list[tuple[float, float]]:
    """Return UMF door candidate positions confirmed by ≥min_missions traversal missions.

    Traversal events in cloud timeline confirm the robot crossed room boundaries.
    A mission is counted when it has ≥1 traversal event in its finEvents.
    After min_missions of confirmed crossings, all UMF door candidates are returned
    (the geometric analysis already filters real doors; traversal confirms the robot
    actually navigated through them).
    """
    door_candidates = getattr(aligner, "_door_candidates", [])
    if len(door_candidates) < 2:
        return []

    missions_with_traversals = 0
    for record in records:
        timeline = record.get("timeline") or {}
        fin_events = timeline.get("finEvents", [])
        if any(e.get("type") == "traversal" for e in fin_events):
            missions_with_traversals += 1
        if missions_with_traversals >= min_missions:
            return list(door_candidates)

    # GS-LOG: explicit log so users can see why bootstrap hasn't fired
    _LOGGER.info(
        "GS-SMART-UMF: %d/%d missions with traversal events in last %d cloud records "
        "— need %d more complete room-specific cleans for bootstrap",
        missions_with_traversals, min_missions, len(records),
        max(0, min_missions - missions_with_traversals),
    )
    return []


async def _async_bootstrap_umf_aligner(
    hass: HomeAssistant,
    entry: "RoombaConfigEntry",
    coordinator: Any,
) -> None:
    """GS-SMART-UMF — align without local pose data using cloud traversal evidence.

    Called from _on_cloud_refresh_complete when:
    - SMART robot with cloud credentials
    - GeometryStore has < 2 confirmed markers (no local pose data arrived)
    - UmfAligner not yet aligned
    - ≥3 cloud missions with traversal events confirm room crossings

    Sets synthetic markers on UmfAligner (UMF-space, identity transform) so
    room coverage maps and calibration attributes become available even when
    lewis firmware suppresses local MQTT pose broadcasts.
    """
    data = entry.runtime_data
    aligner = data.umf_aligner
    gs = data.geometry_store

    if aligner is None or aligner.aligned:
        return  # Already aligned (normal or previous bootstrap) — nothing to do

    if gs is not None and sum(
        1 for m in gs.door_markers if m.mission_count >= 2
    ) >= 2:
        return  # Local GS markers exist — normal alignment path is running

    if not coordinator.last_update_success:
        return

    # Bug 4 fix (v2.7.0): _door_candidates is only populated after align() runs.
    # On the first call the aligner has never run, so candidates would be empty
    # and _extract_traversal_umf_positions would silently return [].
    # Run align() once (returns 0.0 without markers) to populate _door_candidates.
    if not getattr(aligner, "_door_candidates", []):
        await hass.async_add_executor_job(aligner.align)

    positions = _extract_traversal_umf_positions(
        coordinator.raw_records, aligner
    )

    # GS-QUICK (v2.7.1): when last 100 records lack enough traversal missions
    # (e.g. recent error 224 floods), paginate further back in history.
    if not positions:
        blid = entry.data.get(CONF_BLID, "")
        oldest_ts = min(
            (r.get("startTime") for r in coordinator.raw_records if r.get("startTime")),
            default=None,
        )
        if blid and oldest_ts:
            _LOGGER.info(
                "GS-SMART-UMF: fetching older cloud records for %s (before ts=%s)",
                blid, oldest_ts,
            )
            try:
                cloud_api = coordinator.api
                older = await cloud_api.get_mission_history(
                    blid, count=500, before_ts=oldest_ts
                )
                if older:
                    positions = _extract_traversal_umf_positions(older, aligner)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("GS-SMART-UMF: paginated fetch failed — %s", exc)

    if not positions:
        return

    aligner.set_bootstrap_markers(positions)
    conf = await hass.async_add_executor_job(aligner.align)
    _LOGGER.info(
        "GS-SMART-UMF: bootstrap alignment confidence=%.2f aligned=%s for %s",
        conf, aligner.aligned, entry.data.get(CONF_BLID, "unknown"),
    )


def _umf_version_changed(
    coordinator: Any,
    entry: RoombaConfigEntry,
) -> bool:
    """Return True when the active UMF version differs from the last aligned version."""
    current_version = coordinator.umf_data.get("version_id")
    if not current_version:
        return False
    aligner = entry.runtime_data.umf_aligner
    if aligner is None:
        return True
    return aligner.pmap_version_id != current_version


async def _async_realign(
    hass: HomeAssistant,
    entry: RoombaConfigEntry,
    coordinator: Any,
) -> None:
    """Re-instantiate and run UmfAligner after a pmap version change."""
    from .umf_aligner import UmfAligner
    data = entry.runtime_data
    if not coordinator.umf_data.get("points2d") or not coordinator.regions:
        return
    if data.geometry_store is None:
        return
    aligner = UmfAligner(
        points2d=coordinator.umf_data["points2d"],
        regions=coordinator.regions,
        geometry_store=data.geometry_store,
        pmap_version_id=coordinator.umf_data.get("version_id", ""),
    )
    conf = await hass.async_add_executor_job(aligner.align)
    data.umf_aligner = aligner
    _LOGGER.info(
        "Roomba+ UmfAligner: re-aligned confidence=%.2f aligned=%s for %s",
        conf, aligner.aligned, entry.data[CONF_BLID],
    )


async def async_setup_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Set up Roomba+ from a config entry."""
    # Migrate options from data if this is a fresh entry
    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_CONTINUOUS: config_entry.data.get(CONF_CONTINUOUS, DEFAULT_CONTINUOUS),
                CONF_DELAY: config_entry.data.get(CONF_DELAY, DEFAULT_DELAY),
            },
        )

    # ── Data migration: backfill discovered_zone_ids ───────────────────────
    _opts = config_entry.options
    _zone_data_keys = set(_opts.get(CONF_SMART_ZONE_DATA, {}).keys())
    _discovered = set(_opts.get("discovered_zone_ids", []))
    if _zone_data_keys and not _zone_data_keys.issubset(_discovered):
        _new_discovered = sorted(_discovered | _zone_data_keys)
        hass.config_entries.async_update_entry(
            config_entry,
            options={**_opts, "discovered_zone_ids": _new_discovered},
        )
        _LOGGER.info(
            "Roomba+: backfilled discovered_zone_ids with %s from smart_zone_data",
            sorted(_zone_data_keys - _discovered),
        )

    roomba = await hass.async_add_executor_job(
        partial(
            RoombaFactory.create_roomba,
            address=config_entry.data[CONF_HOST],
            blid=config_entry.data[CONF_BLID],
            password=config_entry.data[CONF_PASSWORD],
            continuous=config_entry.options[CONF_CONTINUOUS],
            delay=config_entry.options[CONF_DELAY],
        )
    )

    try:
        if not await async_connect_or_timeout(hass, roomba):
            return False
    except CannotConnect as err:
        raise exceptions.ConfigEntryNotReady(
            f"Cannot connect to Roomba at {config_entry.data[CONF_HOST]}"
        ) from err

    async def _async_disconnect_on_stop(event: Any) -> None:
        await async_disconnect_or_timeout(hass, roomba)

    config_entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_disconnect_on_stop
        )
    )

    # ── Detect map capability ──────────────────────────────────────────────
    state = roomba_reported_state(roomba)
    map_capability = MapCapability.NONE
    renderer: MapRenderer | None = None
    zone_store: ZoneStore | None = None
    geometry_store: GeometryStore | None = None

    map_enabled = config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)

    if has_pose(state) and map_enabled:
        if has_smart_map(state):
            map_capability = MapCapability.SMART
            _LOGGER.debug("Roomba+ map: SMART (persistent pmaps detected)")
        else:
            map_capability = MapCapability.EPHEMERAL
            _LOGGER.debug("Roomba+ map: EPHEMERAL (900-series pose, no pmaps)")

        if map_capability == MapCapability.EPHEMERAL:
            zone_store = ZoneStore()
            await zone_store.async_load(hass, config_entry.entry_id)

        # GeometryStore is needed for both EPHEMERAL (door markers from ZoneStore)
        # and SMART (UmfAligner door marker accumulation for alignment confidence).
        if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
            geometry_store = GeometryStore()
            await geometry_store.async_load(hass, config_entry.entry_id)

        # v2.9.0 — robot footprint circle radius matches the real chassis
        # diameter, not an arbitrary cleaning-width guess. 900-series (incl.
        # EPHEMERAL test robot 980) has a slightly larger chassis than
        # i/s/j-series (SMART).
        if map_capability == MapCapability.EPHEMERAL:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_900_SERIES
        elif map_capability == MapCapability.SMART:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_ISJ_SERIES
        else:
            _robot_diameter_mm = ROBOT_DIAMETER_MM_DEFAULT

        renderer = MapRenderer(
            RendererConfig(
                size_px=config_entry.options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                scale=config_entry.options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE),
                robot_diameter_mm=_robot_diameter_mm,
            ),
            geometry_store=geometry_store,
            zone_store=zone_store,
        )
    else:
        _LOGGER.debug(
            "Roomba+ map: NONE (cap.pose=%s, map_enabled=%s)",
            state.get("cap", {}).get("pose"), map_enabled,
        )

    # F9 — GridStore for all pose-capable robots (EMA occupancy heatmap + hazard detection)
    grid_store: GridStore | None = None
    if map_capability != MapCapability.NONE and map_enabled:
        grid_store = GridStore()
        await grid_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ GridStore: loaded %d cell(s) for %s",
            grid_store.cell_count, config_entry.data[CONF_BLID],
        )

    maintenance_store = MaintenanceStore()
    await maintenance_store.async_load(hass, config_entry.entry_id)

    # F4d -- detect bbrun.hr firmware reset (current_hr < stored reset_hr).
    # This happens silently after a firmware update that resets the runtime
    # counter.  The stored reset baselines are now wrong; fire a Repair Issue
    # so the user knows to re-reset consumables.
    _state_for_bbrun = roomba_reported_state(roomba)
    _bbrun = _state_for_bbrun.get("bbrun", {})
    _runtime = _state_for_bbrun.get("runtimeStats", {})
    _current_hr = _bbrun.get("hr") or _runtime.get("hr") or 0
    if _current_hr > 0:
        from .repairs import async_check_bbrun_reset
        await async_check_bbrun_reset(hass, config_entry, maintenance_store, _current_hr)

    # ── v1.8.0 L1 — Mission store ─────────────────────────────────────────
    mission_store = MissionStore()
    await mission_store.async_load(hass, config_entry.entry_id)

    # F7j — backfill HA Long-Term Statistics from stored mission records.
    # Idempotent — safe to run on every startup. Runs in background so it
    # does not block setup even on large mission histories.
    robot_name = config_entry.title or "Roomba"
    hass.async_create_task(
        mission_store.async_backfill_statistics(
            hass, config_entry.entry_id, robot_name
        ),
        name="roomba_plus_statistics_backfill",
    )

    # Restore L3 last-error state from mission history.
    last_error_code: int | None = None
    last_error_at: str | None = None
    last_error_zone: str | None = None
    _ERROR_RESULTS = frozenset({
        "error", "stuck", "stuck_and_resumed", "stuck_and_abandoned"
    })
    for _rec in reversed(mission_store._records):
        if _rec.get("result") in _ERROR_RESULTS and _rec.get("error_code"):
            last_error_code = _rec["error_code"]
            last_error_at   = _rec.get("ended_at")
            last_error_zone = (_rec.get("zones") or [None])[0]
            break

    # ── v1.7.0 L5 — Blocking manager ──────────────────────────────────────
    blocking_manager: BlockingManager | None = None
    if config_entry.options.get(CONF_BLOCKING_SENSORS):
        blocking_manager = BlockingManager(hass, config_entry)
        _LOGGER.debug(
            "Roomba+ blocking manager active — sensors: %s",
            config_entry.options[CONF_BLOCKING_SENSORS],
        )

    # ── v1.8.0 L6 — Presence manager ──────────────────────────────────────
    presence_manager: PresenceManager | None = None
    if config_entry.options.get(CONF_PRESENCE_SCHEDULING_ENABLED):
        presence_manager = PresenceManager(hass, config_entry)
        _LOGGER.debug("Roomba+ presence manager active")

    # ── Cloud coordinator ──────────────────────────────────────────────────
    cloud_coordinator: IrobotCloudCoordinator | None = None
    irobot_username = config_entry.data.get(CONF_IROBOT_USERNAME)
    irobot_password = config_entry.data.get(CONF_IROBOT_PASSWORD)

    # v2.8.0 ARC1 — MissionArchive (requires cloud credentials; same gate as coordinator)
    mission_archive: MissionArchive | None = None
    if map_capability != MapCapability.NONE and irobot_username and irobot_password:
        mission_archive = MissionArchive()
        await mission_archive.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "MissionArchive: loaded %d record(s) for %s",
            mission_archive.record_count, config_entry.data[CONF_BLID],
        )

    if map_capability != MapCapability.NONE and irobot_username and irobot_password:
        has_pmaps = map_capability == MapCapability.SMART
        cloud_coordinator = IrobotCloudCoordinator(
            hass=hass,
            config_entry=config_entry,
            blid=config_entry.data[CONF_BLID],
            username=irobot_username,
            password=irobot_password,
            has_pmaps=has_pmaps,
            mission_store=mission_store,   # CR3 — fallback source
            mission_archive=mission_archive,  # ARC1 — v2.8.0
        )
        # IA74-PMAP: seed active_pmap_id from local MQTT before first cloud fetch
        # so vacuum.clean_area is not blocked during the initial coordinator refresh.
        cloud_coordinator.seed_pmap_id_from_local(state)
        try:
            await cloud_coordinator.async_config_entry_first_refresh()
            _LOGGER.info(
                "Roomba+ cloud: coordinator active for %s (%d pmap(s), mode=%s)",
                config_entry.data[CONF_BLID],
                len(cloud_coordinator.data.get("pmaps", [])),
                map_capability.value,
            )
            # v2.0 Step 6 — backfill MissionStore timestamps from cloud.
            # 980/900-series firmware resets mssnStrtTm=0 in the end-of-mission
            # MQTT message, leaving local records with duration_min≈0. Correct
            # them now using the authoritative cloud timestamps.
            if cloud_coordinator.raw_records:
                _bf = mission_store.backfill_from_cloud(
                    cloud_coordinator.raw_records
                )
                if _bf.corrected or _bf.enriched:
                    await mission_store.async_save(hass, config_entry.entry_id)

                # F22a prerequisite — seed GridStore with cloud-detected obstacle
                # centroids from UMF observed_zones. Only seeds cells not already
                # present in GridStore (no overwrite of local data).
                if grid_store is not None:
                    centroids = cloud_coordinator.observed_zone_centroids
                    if centroids:
                        seeded = grid_store.seed_from_observed_zones(centroids)
                        if seeded:
                            await grid_store.async_save(hass, config_entry.entry_id)
                            _LOGGER.debug(
                                "Roomba+: seeded %d GridStore cell(s) from UMF "
                                "observed_zones for %s",
                                seeded, config_entry.data[CONF_BLID],
                            )
        except exceptions.ConfigEntryAuthFailed:
            _LOGGER.warning(
                "Roomba+ cloud: authentication failed for %s — "
                "check iRobot credentials in integration options",
                config_entry.data[CONF_BLID],
            )
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Roomba+ cloud: initial fetch failed for %s — "
                "local operation unaffected, cloud features unavailable until retry",
                config_entry.data[CONF_BLID],
            )

    # v2.4.0 F-EPHEMERAL — OutlineStore (EPHEMERAL robots with map enabled)
    outline_store: OutlineStore | None = None
    if (
        map_capability == MapCapability.EPHEMERAL
        and config_entry.options.get(CONF_MAP_ENABLED, DEFAULT_MAP_ENABLED)
    ):
        outline_store = OutlineStore()
        await outline_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug(
            "Roomba+ OutlineStore: loaded %d points for %s",
            outline_store.contour_point_count, config_entry.data[CONF_BLID],
        )

    # v2.4.0 F11 — DirtThresholdManager (SMART + cloud + demand enabled)
    dirt_threshold_manager: DirtThresholdManager | None = None
    if (
        map_capability == MapCapability.SMART
        and cloud_coordinator is not None
        and config_entry.options.get(CONF_DEMAND_CLEANING_ENABLED, False)
    ):
        dirt_threshold_manager = DirtThresholdManager(hass, config_entry)
        await dirt_threshold_manager.async_load(config_entry.entry_id)
        _LOGGER.debug("Roomba+ DirtThresholdManager: active for %s", config_entry.data[CONF_BLID])

    # v2.6.0 L4 — RobotProfileStore (all capability tiers)
    robot_profile_store = RobotProfileStore()
    await robot_profile_store.async_load(hass, config_entry.entry_id)
    _LOGGER.debug("RobotProfileStore: loaded for %s", config_entry.data[CONF_BLID])

    # v2.8.0 L5-ARC — seed room_dirt_index from archive if already complete
    # (archive.initial_load_done=True = loaded from storage, not first run).
    #
    # Bug-hunt: both seeding calls are awaited directly here, not via
    # hass.async_create_task — an uncaught exception would fail
    # async_setup_entry entirely, blocking the whole integration from
    # loading after a restart until storage is manually cleared. Wrapped
    # as defense-in-depth on top of the type-guard inside the function
    # itself, since a corrupted/hand-edited persisted archive file could
    # be malformed in ways beyond the one shape this session's bug hunt
    # specifically enumerated.
    if mission_archive is not None and mission_archive.initial_load_done:
        try:
            await _async_seed_l5_from_archive(
                hass, config_entry.entry_id, mission_archive, robot_profile_store
            )
            # L3-ARC — seed anomaly detection baseline from same archive
            await _async_seed_l3_from_archive(mission_archive, mission_store)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "L5-ARC/L3-ARC: archive seeding failed — continuing setup "
                "without archive-based baselines (will retry next restart)",
                exc_info=True,
            )

    # v2.6.0 MP1 — MissionTimerStore (SMART + cloud only)
    mission_timer_store: MissionTimerStore | None = None
    if map_capability == MapCapability.SMART and cloud_coordinator is not None:
        mission_timer_store = MissionTimerStore()
        await mission_timer_store.async_load(hass, config_entry.entry_id)
        _LOGGER.debug("MissionTimerStore: loaded for %s", config_entry.data[CONF_BLID])

    # v2.3.0 F8 — UMF spatial fusion aligner
    umf_aligner: Any | None = None
    if cloud_coordinator is not None and geometry_store is not None:
        _points2d   = cloud_coordinator.umf_data.get("points2d")
        # Use regions from UMF maps[] — these contain geometry.ids for polygon
        # resolution. Regions from get_pmaps() only have metadata (name, policies
        # etc.) without geometry. Fall back to get_pmaps() regions if UMF regions
        # are absent (e.g. older API versions).
        _umf_regions = cloud_coordinator.umf_data.get("regions") or []
        _regions     = _umf_regions or cloud_coordinator.regions
        if not _points2d:
            _LOGGER.debug(
                "Roomba+ UmfAligner: skipped for %s — no points2d in UMF data "
                "(umf_data keys: %s). rooms_map will show fallback until UMF "
                "geometry is available from the cloud.",
                config_entry.data[CONF_BLID],
                list(cloud_coordinator.umf_data.keys()),
            )
        elif not _regions:
            _LOGGER.debug(
                "Roomba+ UmfAligner: skipped for %s — no regions from cloud coordinator.",
                config_entry.data[CONF_BLID],
            )
        else:
            from .umf_aligner import UmfAligner
            _aligner = UmfAligner(
                points2d=_points2d,
                regions=_regions,
                geometry_store=geometry_store,
                pmap_version_id=cloud_coordinator.umf_data.get("version_id", ""),
            )
            _conf = await hass.async_add_executor_job(_aligner.align)
            umf_aligner = _aligner
            _LOGGER.info(
                "Roomba+ UmfAligner: confidence=%.2f aligned=%s for %s",
                _conf, _aligner.aligned, config_entry.data[CONF_BLID],
            )

    config_entry.runtime_data = RoombaData(
        roomba=roomba,
        blid=config_entry.data[CONF_BLID],
        map_capability=map_capability,
        renderer=renderer,
        zone_store=zone_store,
        geometry_store=geometry_store,
        maintenance_store=maintenance_store,
        cloud_coordinator=cloud_coordinator,
        blocking_manager=blocking_manager,
        mission_store=mission_store,
        last_error_code=last_error_code,
        last_error_at=last_error_at,
        last_error_zone=last_error_zone,
        grid_store=grid_store,
        floor_label=config_entry.options.get(CONF_FLOOR, ""),
        umf_aligner=umf_aligner,
        dirt_threshold_manager=dirt_threshold_manager,
        outline_store=outline_store,
        robot_profile=get_robot_profile(
            state.get("sku"),
            battery_type=state.get("batteryType"),   # override chemistry from live state
        ),
        robot_profile_store=robot_profile_store,
        mission_timer_store=mission_timer_store,
        mission_archive=mission_archive,
    )

    # v2.8.0 ARC1 — start one-time paginated back-fill as background task.
    # Runs after runtime_data is set so any concurrent delta updates (from the
    # first coordinator refresh) are already in the archive and will be skipped
    # via _archived_nmssns.
    if (
        mission_archive is not None
        and cloud_coordinator is not None
        and not mission_archive.initial_load_done
    ):
        hass.async_create_task(
            mission_archive.async_initial_load(
                cloud_coordinator.api,
                config_entry.data[CONF_BLID],
                hass,
                config_entry.entry_id,
            ),
            name=f"roomba_plus_arc1_initial_load_{config_entry.entry_id}",
        )

    # v2.6.3 B9 — the 980 may not include "sku" in the first MQTT state dump
    # that arrives before async_setup_entry completes.  Register a one-time
    # callback that fills in robot_profile when "sku" first appears.
    if config_entry.runtime_data.robot_profile is None:
        def _set_robot_profile_on_sku(json_data: dict) -> None:
            if config_entry.runtime_data.robot_profile is not None:
                return  # already resolved
            reported = json_data.get("state", {}).get("reported", {})
            sku = reported.get("sku")
            if sku:
                profile = get_robot_profile(sku, reported.get("batteryType"))
                if profile is not None:
                    config_entry.runtime_data.robot_profile = profile
                    _LOGGER.debug(
                        "RobotProfile resolved late for SKU %s → %s mAh %s",
                        sku, profile.battery_mah, profile.battery_chemistry,
                    )
        roomba.register_on_message_callback(_set_robot_profile_on_sku)

    if presence_manager is not None:
        config_entry.runtime_data.presence_manager = presence_manager
        presence_manager.start()

    # ── Platform setup ─────────────────────────────────────────────────────
    platforms = list(LOCAL_PLATFORMS)
    if map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        # IMAGE platform covers both RoombaMapImage and RoombaCoverageImage
        if Platform.IMAGE not in platforms:
            platforms.append(Platform.IMAGE)
    if map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)

    await hass.config_entries.async_forward_entry_setups(config_entry, platforms)

    # ── v1.8.0 — REST API view    # ── v1.8.0 — REST API view ─────────────────────────────────────────────
    if not hass.data.get("_roomba_plus_view_registered"):
        hass.http.register_view(MissionHistoryView())
        hass.http.register_view(HouseholdSummaryView())
        hass.http.register_view(MissionHistoryImportView())
        hass.http.register_view(DailyDigestView())
        hass.data["_roomba_plus_view_registered"] = True

    # ── Register services ──────────────────────────────────────────────────
    async_register_services(hass)

    # ── F22a — check for cloud-detected obstacle zones ─────────────────────
    if cloud_coordinator is not None and grid_store is not None:
        from .repairs import async_check_observed_zones
        hass.async_create_task(
            async_check_observed_zones(hass, config_entry),
            name=f"roomba_plus_observed_zones_check_{config_entry.entry_id}",
        )

    # ── MQTT callbacks ─────────────────────────────────────────────────────
    # v2.9.0 MAP-RETRAIN-WF — local-MQTT-only signal (notReady bit), so this
    # is registered independent of cloud_coordinator presence, unlike the
    # block below.
    if map_capability == MapCapability.SMART:
        roomba.register_on_message_callback(
            make_map_updating_callback(hass, config_entry)
        )

    if cloud_coordinator is not None:
        roomba.register_on_message_callback(
            make_map_retrain_callback(hass, cloud_coordinator, config_entry)
        )
        # F4b -- trigger cloud refresh at mission end to eliminate 24h staleness
        roomba.register_on_message_callback(
            make_mission_complete_callback(hass, cloud_coordinator)
        )

        # CR2 — after each post-mission cloud refresh, merge latest cloud
        # analytics into the most recent MissionStore record so fields like
        # dirt, chrgM, and wlBars are persisted across restarts.
        @callback
        def _on_cloud_refresh_complete() -> None:
            # v2.8.3 CLOUD-STALE — check before the success gate so the Repair
            # Issue fires even during a streak of failed refreshes (which is
            # exactly when it's most actionable) and clears immediately on the
            # first successful one.
            from .repairs import async_check_cloud_stale
            hass.async_create_task(
                async_check_cloud_stale(hass, config_entry, cloud_coordinator),
                name="roomba_plus_cloud_stale_check",
            )
            if not cloud_coordinator.last_update_success:
                return
            ms = config_entry.runtime_data.mission_store
            if ms is None:
                return
            if ms.merge_latest_from_cloud(cloud_coordinator.raw_records):
                hass.async_create_task(
                    ms.async_save(hass, config_entry.entry_id),
                    name="roomba_plus_cloud_merge_save",
                )
            # v2.4.2 F11-WIRE — evaluate demand cleaning after every cloud refresh.
            _dtm = config_entry.runtime_data.dirt_threshold_manager
            if _dtm is not None:
                hass.async_create_task(
                    _dtm.async_evaluate(cloud_coordinator, config_entry.entry_id),
                    name="roomba_plus_demand_clean_eval",
                )
            # v2.3.0 — re-align UmfAligner when UMF version changes
            if _umf_version_changed(cloud_coordinator, config_entry):
                hass.async_create_task(
                    _async_realign(hass, config_entry, cloud_coordinator),
                    name="roomba_plus_umf_realign",
                )
            # v2.3.0 Step 6c — F8b error recurrence Repair Issue
            from .repairs import async_check_error_recurrence
            hass.async_create_task(
                async_check_error_recurrence(hass, config_entry),
                name="roomba_plus_error_recurrence_check",
            )
            # v2.8.2 — cancellation recurrence Repair Issue (separate from
            # error_recurrence since cancelled/cancelled_by_user results
            # carry no numeric error_code and were previously invisible to
            # any recurrence check).
            from .repairs import async_check_cancellation_recurrence
            hass.async_create_task(
                async_check_cancellation_recurrence(hass, config_entry),
                name="roomba_plus_cancellation_recurrence_check",
            )
            # v2.6.0 L5/L6 — update RobotProfileStore from latest cloud data
            _rps = config_entry.runtime_data.robot_profile_store
            if _rps is not None:
                hass.async_create_task(
                    _async_update_robot_profile_store(hass, config_entry, ms, _rps),
                    name="roomba_plus_profile_store_update",
                )
            # v2.7.0 L7 — stuck pattern time-correlation check
            if config_entry.runtime_data.grid_store is not None:
                from .repairs import async_check_stuck_pattern
                hass.async_create_task(
                    async_check_stuck_pattern(hass, config_entry),
                    name="roomba_plus_l7_stuck_pattern_check",
                )
            # v2.7.1 SMBERR — SMBus battery communication error check
            from .repairs import async_check_smberr
            hass.async_create_task(
                async_check_smberr(hass, config_entry),
                name="roomba_plus_smberr_check",
            )
            # v2.8.0 DOCK-HEALTH — dock contact health check
            from .repairs import async_check_dock_health
            hass.async_create_task(
                async_check_dock_health(hass, config_entry),
                name="roomba_plus_dock_health_check",
            )
            # v2.7.0 GS-SMART-UMF — bootstrap alignment for robots without local pose
            if (
                config_entry.runtime_data.map_capability == MapCapability.SMART
                and config_entry.runtime_data.umf_aligner is not None
            ):
                hass.async_create_task(
                    _async_bootstrap_umf_aligner(
                        hass, config_entry, cloud_coordinator
                    ),
                    name="roomba_plus_gs_smart_umf_bootstrap",
                )
        config_entry.async_on_unload(
            cloud_coordinator.async_add_listener(_on_cloud_refresh_complete)
        )

    roomba.register_on_message_callback(
        make_mission_callback(hass, config_entry)
    )

    # Reload on options change (continuous/delay require reconnect)
    config_entry.async_on_unload(
        config_entry.add_update_listener(_async_reload_on_options_change)
    )

    _LOGGER.info(
        "Roomba+ connected to %s (blid=%s)",
        config_entry.data[CONF_HOST],
        config_entry.data[CONF_BLID],
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> bool:
    """Unload a config entry and disconnect from the Roomba."""
    data = config_entry.runtime_data
    platforms = list(LOCAL_PLATFORMS)
    if data.map_capability in (MapCapability.EPHEMERAL, MapCapability.SMART):
        if Platform.IMAGE not in platforms:
            platforms.append(Platform.IMAGE)
    if data.map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)

    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, platforms
    )
    if unload_ok:
        bm = config_entry.runtime_data.blocking_manager
        if bm is not None:
            bm.cancel_queue()

        pm = config_entry.runtime_data.presence_manager
        if pm is not None:
            pm.cancel()

        await async_disconnect_or_timeout(
            hass, roomba=config_entry.runtime_data.roomba
        )

        if not hass.config_entries.async_entries(DOMAIN):
            async_remove_services(hass)

    return unload_ok


async def _async_reload_on_options_change(
    hass: HomeAssistant, config_entry: RoombaConfigEntry
) -> None:
    """Reload only when connection-relevant options change.

    Compares current data against current options for CONF_CONTINUOUS / CONF_DELAY.
    When they differ, syncs data first so subsequent option changes do NOT
    re-trigger a reload (prevents false reconnect on every options edit after
    the first connection-relevant change).
    """
    _CONNECTION_KEYS = {CONF_CONTINUOUS, CONF_DELAY}
    old_vals = {k: config_entry.data.get(k) for k in _CONNECTION_KEYS}
    new_vals = {k: config_entry.options.get(k) for k in _CONNECTION_KEYS}
    if old_vals != new_vals:
        # Sync data to match new options so the next options change starts from
        # a clean baseline and does not re-trigger an unintended reload.
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, **new_vals},
        )
        await hass.config_entries.async_reload(config_entry.entry_id)


# ── Connection helpers ────────────────────────────────────────────────────────

async def async_connect_or_timeout(
    hass: HomeAssistant, roomba: Roomba
) -> dict[str, Any]:
    """Connect to the vacuum and wait for first state report."""
    try:
        name: str | None = None
        async with asyncio.timeout(16):
            _LOGGER.debug("Connecting to Roomba")
            await hass.async_add_executor_job(roomba.connect)
            while not roomba.roomba_connected or name is None:
                name = roomba_reported_state(roomba).get("name")
                if name:
                    break
                await asyncio.sleep(1)
            await asyncio.sleep(2)
            cap = roomba_reported_state(roomba).get("cap", {})
            if cap.get("pmaps", 0) > 0 or cap.get("maps", 0) > 1:
                for _ in range(6):
                    if roomba_reported_state(roomba).get("pmaps"):
                        break
                    await asyncio.sleep(1)
    except RoombaConnectionError as err:
        _LOGGER.debug("Connection error: %s", err)
        raise CannotConnect from err
    except TimeoutError as err:
        await async_disconnect_or_timeout(hass, roomba)
        _LOGGER.debug("Connection timed out: %s", err)
        raise CannotConnect from err

    return {ROOMBA_SESSION: roomba, CONF_NAME: name}


async def async_disconnect_or_timeout(
    hass: HomeAssistant, roomba: Roomba
) -> None:
    """Disconnect from the vacuum with a 3 s safety timeout."""
    _LOGGER.debug("Disconnecting from Roomba")
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(3):
            await hass.async_add_executor_job(roomba.disconnect)


# ── State helpers (used across all platforms) ─────────────────────────────────

def roomba_reported_state(roomba: Roomba) -> dict[str, Any]:
    """Return the 'reported' sub-dict from master_state."""
    return roomba.master_state.get("state", {}).get("reported", {})


# ── Exceptions ────────────────────────────────────────────────────────────────

class CannotConnect(exceptions.HomeAssistantError):
    """Raised when a connection to the Roomba cannot be established."""


async def async_remove_config_entry_devices(
    hass: HomeAssistant, config_entry: RoombaConfigEntry, devices: list[Any]
) -> bool:
    """Return whether stale devices can be removed from the device registry.

    Called by HA when the user requests removal of a device that is no longer
    associated with any entity in this config entry. For Roomba+, each config
    entry manages exactly one physical robot — there are no child devices or
    dynamically-discovered sub-devices, so any device presented for removal
    is safe to remove.
    """
    return True
