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

from .callbacks import make_map_retrain_callback, make_mission_callback, make_mission_complete_callback
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
    has_pose,
    has_smart_map,
)
from .api_views import MissionHistoryView, HouseholdSummaryView
from .grid_store import GridStore
from .mission_store import MissionStore
from .presence_manager import PresenceManager
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
from .dirt_threshold_manager import DirtThresholdManager
from .outline_store import OutlineStore
from .maintenance_store import MaintenanceStore
from .map_renderer import MapRenderer, RendererConfig
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
      10 → 11 (v2.1.2): Rename cloud history sensor entity_ids:
                        *_lifetime_area → *_recent_area_30d
                        *_lifetime_time → *_recent_time_30d
                        These sensors were misnamed — they aggregate the ~30-mission
                        API window, not true lifetime totals.
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

    if current == config_entry.version:
        _LOGGER.debug(
            "Roomba+: config entry %s already at version %d — no migration needed",
            config_entry.entry_id, current,
        )

    return True


# v2.3.0 F8 — UmfAligner re-alignment helpers ─────────────────────────────────

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

        renderer = MapRenderer(
            RendererConfig(
                size_px=config_entry.options.get(CONF_MAP_SIZE_PX, DEFAULT_MAP_SIZE_PX),
                scale=config_entry.options.get(CONF_MAP_SCALE, DEFAULT_MAP_SCALE),
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
    for _rec in reversed(mission_store._records):
        _res = _rec.get("result")
        if _res in ("error", "stuck") and _rec.get("error_code"):
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
        )
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
    )

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

    # ── v1.8.0 — REST API view ─────────────────────────────────────────────
    if not hass.data.get("_roomba_plus_view_registered"):
        hass.http.register_view(MissionHistoryView())
        hass.http.register_view(HouseholdSummaryView())
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
    if cloud_coordinator is not None:
        roomba.register_on_message_callback(
            make_map_retrain_callback(hass, cloud_coordinator)
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
    """Reload only when connection-relevant options change."""
    _CONNECTION_KEYS = {CONF_CONTINUOUS, CONF_DELAY}
    old_vals = {k: config_entry.data.get(k) for k in _CONNECTION_KEYS}
    new_vals = {k: config_entry.options.get(k) for k in _CONNECTION_KEYS}
    if old_vals != new_vals:
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
