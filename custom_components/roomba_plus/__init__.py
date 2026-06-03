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
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .callbacks import make_map_retrain_callback, make_mission_callback, make_mission_complete_callback
from .const import (
    CONF_BLID,
    CONF_BLOCKING_SENSORS,
    CONF_CONTINUOUS,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
    CONF_MAP_ENABLED,
    CONF_MAP_SCALE,
    CONF_MAP_SIZE_PX,
    CONF_PRESENCE_SCHEDULING_ENABLED,
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
from .api_views import MissionHistoryView
from .mission_store import MissionStore
from .presence_manager import PresenceManager
from .cloud_coordinator import IrobotCloudCoordinator
from .blocking_manager import BlockingManager
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

    if current == config_entry.version:
        _LOGGER.debug(
            "Roomba+: config entry %s already at version %d — no migration needed",
            config_entry.entry_id, current,
        )

    return True


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
                n = mission_store.backfill_from_cloud(
                    cloud_coordinator.raw_records
                )
                if n:
                    await mission_store.async_save(hass, config_entry.entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Roomba+ cloud: initial fetch failed for %s — "
                "local operation unaffected, cloud features unavailable until retry",
                config_entry.data[CONF_BLID],
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
    )

    if presence_manager is not None:
        config_entry.runtime_data.presence_manager = presence_manager
        presence_manager.start()

    # ── Platform setup ─────────────────────────────────────────────────────
    platforms = list(LOCAL_PLATFORMS)
    if map_capability == MapCapability.EPHEMERAL:
        platforms.append(Platform.IMAGE)
    if map_capability == MapCapability.SMART:
        from .const import CLOUD_PLATFORMS
        platforms.extend(p for p in CLOUD_PLATFORMS if p not in platforms)

    await hass.config_entries.async_forward_entry_setups(config_entry, platforms)

    # ── v1.8.0 — REST API view ─────────────────────────────────────────────
    if not hass.data.get("_roomba_plus_view_registered"):
        hass.http.register_view(MissionHistoryView())
        hass.data["_roomba_plus_view_registered"] = True

    # ── Register services ──────────────────────────────────────────────────
    async_register_services(hass)

    # ── MQTT callbacks ─────────────────────────────────────────────────────
    if cloud_coordinator is not None:
        roomba.register_on_message_callback(
            make_map_retrain_callback(hass, cloud_coordinator)
        )
        # F4b -- trigger cloud refresh at mission end to eliminate 24h staleness
        roomba.register_on_message_callback(
            make_mission_complete_callback(hass, cloud_coordinator)
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
    if data.map_capability == MapCapability.EPHEMERAL:
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
