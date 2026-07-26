"""Config entry migrations, one per schema version.

MOVED OUT OF __init__.py (this session). It had grown to 2,145 lines --
roughly sixty percent of that file -- and pushed the actual setup and
teardown logic far enough down that reading either meant scrolling past
the other.

Migrations are the one part of an integration that only ever grows:
every historical version's migration has to stay forever, because
somebody out there is still on it. Keeping that permanent, monotonically
growing body in the same file as the code that changes most often makes
both harder to work with.

Nothing about the migrations themselves changed in the move -- same
function, same version history, same behaviour. __init__.py re-exports
it so Home Assistant's own lookup of `async_migrate_entry` on the
integration module keeps working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_FLOOR, DOMAIN
from .models import RoombaConfigEntry

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

    if current == 22:
        # v22 → v23 (v3.0.0): stabilise FavoriteButton entity_ids.
        #
        # Root cause: FavoriteButton used _attr_name = fav_name (user-defined
        # iRobot routine name) without suggested_object_id.  With
        # has_entity_name=True this generated entity_ids from the routine name,
        # e.g. button.roomba_980_og_montag_morgen, making them
        # user-locale-dependent and impossible for the card to discover.
        #
        # Fix (v3.0.0): IRobotEntity.suggested_object_id now returns
        # fav_{fav_id} for FavoriteButton (via unique_id prefix strip).
        # HA will use button.{device_slug}_fav_{fav_id} for all NEW entities.
        #
        # This migration renames existing FavoriteButton entity_ids that do NOT
        # already contain "_fav_" to the canonical form so the card's
        # button.{robotName}_fav_* prefix scan works immediately after upgrade
        # without requiring users to delete and re-add the integration.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import device_registry as dr
        from homeassistant.util import slugify as ha_slugify

        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)

        # Build the exact prefix that FavoriteButton unique_ids start with.
        # unique_id format: f"{robot_unique_id}_fav_{fav_id}"
        # robot_unique_id = f"roomba_plus_{blid}"
        # Using the exact prefix (not rfind) makes the check unambiguous even
        # when fav_id itself contains the string "_fav_".
        blid = config_entry.data.get("blid", "")
        fav_uid_prefix = f"roomba_plus_{blid}_fav_"

        renamed = 0
        if not blid:
            _LOGGER.warning(
                "Roomba+: v22→v23 migration — blid not in config entry data, "
                "skipping FavoriteButton rename pass"
            )
            fav_entries: list[Any] = []
        else:
            fav_entries = list(entity_reg.entities.values())

        for entry in fav_entries:
            if entry.platform != DOMAIN:
                continue
            uid = entry.unique_id or ""

            # Exact prefix match — only FavoriteButton entities for THIS robot
            if not uid.startswith(fav_uid_prefix):
                continue
            fav_id = uid[len(fav_uid_prefix):]
            if not fav_id:
                continue  # empty fav_id — skip

            eid = entry.entity_id
            # Already canonical: entity_id suffix contains _fav_
            if "_fav_" in eid:
                continue

            # Compute canonical entity_id: button.{device_slug}_fav_{fav_id_slug}
            device = device_reg.async_get(entry.device_id) if entry.device_id else None
            # Match HA's own entity_id generation: name_by_user overrides name.
            device_name = (device.name_by_user or device.name or "") if device else ""
            if not device_name:
                _LOGGER.warning(
                    "Roomba+: cannot rename FavoriteButton %s — device name unknown",
                    eid,
                )
                continue

            fav_slug = ha_slugify(fav_id)
            device_slug = ha_slugify(device_name)
            new_eid = f"button.{device_slug}_fav_{fav_slug}"

            # Avoid collision — skip if target already taken
            if entity_reg.async_get(new_eid) is not None:
                _LOGGER.warning(
                    "Roomba+: target entity_id %s already exists — skipping rename of %s",
                    new_eid, eid,
                )
                continue

            entity_reg.async_update_entity(eid, new_entity_id=new_eid)
            renamed += 1
            _LOGGER.debug(
                "Roomba+: FavoriteButton renamed %s → %s", eid, new_eid
            )

        hass.config_entries.async_update_entry(config_entry, version=23)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 23 "
            "(%d FavoriteButton entity_id(s) stabilised)",
            config_entry.entry_id, renamed,
        )
        current = 23

    if current == 23:
        # v23 → v24 (v3.0.0): disable sensors that are permanently unavailable
        # for most robots and have no UI path to become available.
        #
        # Root cause: entity_registry_enabled_default=False only prevents
        # auto-enabling on *new* registrations.  Entities already present in
        # the registry as enabled stay enabled even after the flag is set,
        # and they continue to show as "Nicht verfügbar" / "unavailable"
        # cluttering the entity list with sensors the user cannot act on.
        #
        # Which sensors are targeted:
        #   battery_age_days        — requires batInfo.mDate (BMS chip), absent
        #                             on 900-series firmware; never available
        #   battery_cycle_count_bms — requires batInfo (BMS chip), same
        #   bin_last_cleaned        — requires roomba_plus.reset_bin_cleaning
        #   contact_last_cleaned    — requires roomba_plus.reset_contact_cleaning
        #   wheel_last_cleaned      — requires roomba_plus.reset_wheel_cleaning
        #   The last three have no button entity; only a service call can set
        #   them, making them permanently unavailable for typical users.
        #
        # All five are disabled with disabled_by=INTEGRATION so the user can
        # manually re-enable via the entity registry UI if they need them.
        # On robots where these sensors actually have data (i/s-series BMS),
        # re-enabling takes two clicks.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler

        entity_reg = er.async_get(hass)

        _DISABLE_SUFFIXES = frozenset({
            "battery_age_days",
            "battery_cycle_count_bms",
            "bin_last_cleaned",
            "contact_last_cleaned",
            "wheel_last_cleaned",
        })

        blid = config_entry.data.get("blid", "")
        disabled_count = 0
        if not blid:
            _LOGGER.warning(
                "Roomba+: v23→v24 migration — blid not in config entry data, "
                "skipping sensor disable pass"
            )
        else:
            prefix = f"roomba_plus_{blid}_"
            for entry in list(entity_reg.entities.values()):
                if entry.platform != DOMAIN:
                    continue
                uid = entry.unique_id or ""
                # Match exact unique_id pattern: roomba_plus_{blid}_{suffix}
                if not uid.startswith(prefix):
                    continue
                suffix = uid[len(prefix):]
                if suffix not in _DISABLE_SUFFIXES:
                    continue
                if entry.disabled_by is not None:
                    continue  # already disabled — leave as-is
                entity_reg.async_update_entity(
                    entry.entity_id,
                    disabled_by=RegistryEntryDisabler.INTEGRATION,
                )
                disabled_count += 1
                _LOGGER.debug(
                    "Roomba+: disabled permanently-unavailable sensor %s",
                    entry.entity_id,
                )

        hass.config_entries.async_update_entry(config_entry, version=24)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 24 "
            "(%d permanently-unavailable sensor(s) disabled)",
            config_entry.entry_id, disabled_count,
        )
        current = 24

    if current == 24:
        # v24 → v25 (v3.2.1): re-enable the device_tracker (current-room
        # position) entity for EXISTING installations.
        #
        # Root cause (already fixed in v2.10.3, see device_tracker.py):
        # entity_registry_enabled_default=False (the pre-v2.10.3 implicit
        # default, since neither mac_address nor device_info is set) only
        # prevents auto-enabling on *new* registrations. The code-level fix
        # (_attr_entity_registry_enabled_default = True) has no effect on
        # entities already present in the registry as disabled from before
        # that fix shipped — exactly the community report this migration
        # is named for ("I don't seem to have that entity on my i7+"):
        # the fix has been in the code all along, but does nothing for
        # anyone who installed before it existed.
        #
        # Only clears disabled_by when it is exactly INTEGRATION (i.e. the
        # entity was auto-disabled by the old default) — a user who
        # deliberately disabled this entity themselves (disabled_by=USER)
        # is left untouched; their own choice is not overridden.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler

        entity_reg = er.async_get(hass)
        blid = config_entry.data.get("blid", "")
        reenabled_count = 0
        if not blid:
            _LOGGER.warning(
                "Roomba+: v24→v25 migration — blid not in config entry data, "
                "skipping device_tracker re-enable pass"
            )
        else:
            expected_uid = f"roomba_plus_{blid}_position"
            for entry in list(entity_reg.entities.values()):
                if entry.platform != DOMAIN:
                    continue
                if entry.domain != "device_tracker":
                    continue
                if entry.unique_id != expected_uid:
                    continue
                if entry.disabled_by != RegistryEntryDisabler.INTEGRATION:
                    continue  # not disabled, or disabled by the user — leave as-is
                entity_reg.async_update_entity(
                    entry.entity_id,
                    disabled_by=None,
                )
                reenabled_count += 1
                _LOGGER.debug(
                    "Roomba+: re-enabled current-room device_tracker %s",
                    entry.entity_id,
                )

        hass.config_entries.async_update_entry(config_entry, version=25)
        _LOGGER.info(
            "Roomba+: migrated entry %s to version 25 "
            "(%d current-room device_tracker entit(y/ies) re-enabled)",
            config_entry.entry_id, reenabled_count,
        )
        current = 25

    if current == config_entry.version:
        _LOGGER.debug(
            "Roomba+: config entry %s already at version %d — no migration needed",
            config_entry.entry_id, current,
        )

    return True
