# Roomba+ — Upgrade Notes

[← Roomba+](../README.md)

Per-version upgrade notes and config entry migrations, oldest relevant
first appears last. Only versions with a genuine migration step, a
behaviour change, or a "why is this sensor still Unknown" learning-period
note are listed — most releases need zero action beyond updating.

---

### Upgrading to v3.4.0

No config entry migration — all persisted data is additive, existing stored
data loads unchanged.

**New entities appear automatically:** `calendar.{name}_schedule` and
`todo.{name}_maintenance` are created on every robot tier with no
configuration step. If your robot's cleaning schedule is currently empty,
the calendar will simply show no events until you set one — expected,
not a bug.

**`todo.*` due dates need history before they show a real date, not
`Unknown`** — expected, not a bug: the due date comes from the same
self-calibrated wear-rate estimate `sensor.*_filter_days_until_due`/
`*_brush_days_until_due` already use, which needs an established wear
rate (a handful of missions since the last reset, or since first setup)
before it can project a date. The to-do item itself still appears
immediately; only the due date is initially absent.

**If you're on i/s/j-series lewis firmware (22.52.10+) and the coverage
heatmap, stuck-hotspot markers, furniture-change, or layout-change alerts
have always stayed empty:** this release adds a cloud-data path that
should populate all of them going forward. Give it a few cloud refresh
cycles after updating — it depends on the (already-shipped, silent) map
alignment bootstrap having completed first, which itself typically needs
a handful of missions. Stuck-hotspot detection specifically also depends
on iRobot's cloud data containing real stuck-event records for your
robot, which — unlike the other three — hasn't been field-confirmed to
populate on lewis firmware yet; if it stays empty while the heatmap and
layout-change detection do populate, that's the known open question, not
something to troubleshoot on your end.

---

### Upgrading to v3.3.0

No config entry migration — all persisted data is additive, existing stored
data loads unchanged.

**If `sensor.*_room_cleaning_history` or the `zone_coverage_health` REST
format always showed nothing for you, even on a long-running install:**
that was a real bug, now fixed. Both read a record field that only
*imported* mission histories ever populated; on a normally-running
installation it was silently always empty. Room names are now derived
directly from the mission timeline. No action needed — it starts working
on the very next mission.

New self-calibrating features need history before they show a real value
rather than `Unknown`/`insufficient_data` — expected, not a bug:

- `sensor.*_rooms_overdue`: needs at least one recorded cleaning per room
  to judge overdue status; the `suggested_interval_days` attribute needs
  two sufficiently-spaced cleanings of a room before it appears
- `roomba_plus.auto_clean_dirty_rooms`: rooms need 10+ recorded cleanings
  before their dirt index is trusted — falls back to a whole-house clean
  until then, not an error
- Dirt ↔ sensor correlation (opt-in): needs 30 paired missions before a
  correlation value appears, per entity you configured

**Mission maps (`.../missions/{id}/map.json` / `.../map.png`)** require a
mission recorded on v3.3.0 or later (the `pmaps_info` field is cloud-merged
going forward, not backfilled into older records) — older missions return
404 until enough new ones accumulate. Mission maps are field-confirmed on Braava jet m6 (sapphire firmware
family); i-series (lewis firmware) coverage layer support is unconfirmed as of this release — see the note in
[Feature reference](FEATURES.md#mission-history--room-intelligence) if you
get a 404 mentioning "no coverage layer".

---

### Upgrading to v3.0.0

Two automatic migration steps run on first load (config entry 22 → 24):

**Migration v22→v23 — FavoriteButton entity_id stabilisation.** Existing favorite buttons are renamed from their old user-name-based entity_ids (e.g. `button.roomba_monday_morning`) to the canonical `button.{device}_fav_{id}` form. New favorites registered after this version receive the canonical form automatically. No action required — the migration is fully automatic.

**Migration v23→v24 — Permanently unavailable sensors disabled.** Five sensors that are unavailable by design on most robots (`battery_age_days`, `battery_cycle_count_bms`, `bin_last_cleaned`, `contact_last_cleaned`, `wheel_last_cleaned`) are automatically disabled in the entity registry. On i/s-series robots where BMS data is available, re-enable `battery_age_days` and `battery_cycle_count_bms` in Settings → Entities if needed.

**Deprecated sensors:** If you had manually re-enabled any of the 13 deprecated sensors removed in this release, switch to the consolidated replacement listed in the release notes. HA removes the stale entity registry entries automatically on first load.

### Upgrading to v3.1.0

No config entry migration — the persisted schema is unchanged. New self-calibrating sensors (`relocalisation_rate`, the hardened `estimated_battery_eol`, and the redesigned `map_drift_detected`) need 10–20 missions of history before they show a value rather than `Unknown`/`None` — that's expected, not a bug. They're learning your specific robot's normal behaviour rather than using a generic threshold.

`FAN_SPEED_AUTOMATIC`/`ECO`/`PERFORMANCE` changed from `Automatic`/`Eco`/`Performance` to lowercase (`automatic`/`eco`/`performance`) for Home Assistant compliance. Existing automations using the old Capital-Case values continue to work unchanged — both `select.select_option` and `vacuum.set_fan_speed` accept either form.

`mop_clean_mode`, `mop_tank_status`, and `mop_ars_behavior` sensor states changed similarly — e.g. `"Dirty Pause + Dry"` → `"dirty_pause_dry"`. Update any automation that checks these sensors' raw `state` value with the old Capital-Case text.

### Upgrading to v3.2.0

No config entry migration — all persisted data is additive, existing stored data loads unchanged. Several new self-calibrating features need a stretch of mission history before they show a real value rather than `Unknown`/`insufficient_data`, learning your specific robot's own normal behaviour rather than using a fixed threshold — expected, not a bug:

- `sensor.*_health_score_trend`: 44 days of recorded health-score history — watch it count down via the `days_until_ready` attribute
- `binary_sensor.*_layout_change_detected`: 23 missions of coverage history per grid cell — `cells_tracked` and `missions_until_first_ready` are shown from the start, even before any candidate is found, so "still learning" no longer looks the same as "nothing to report"
- Room accessibility scores, stuck hotspot clusters, cleaning cadence health: a handful of missions with the relevant signal (stuck events, room-tagged cleans) before a meaningful score/status appears

### Upgrading to v3.2.1

Config entry migration (24 → 25): if your current-room `device_tracker` entity was never visible, it's re-enabled automatically on upgrade — this was a real bug (root-caused in v2.10.3, but the fix only applied to newly-created entities, never to ones already registered as disabled on an existing install).

**Coordinate-system fix, EPHEMERAL-tier (900-series) only — a genuine discontinuity, not silent:** a confirmed axis-convention bug in live-map/room-detection pose handling has been corrected. This changes how X/Y map to real-world directions for all data recorded from this update onward — GridStore, room detection, and outline data accumulated *before* this update will not spatially line up with data recorded *after* it. If your room map looks scrambled right after upgrading, this is why. There is currently no dedicated action to reset just the spatial/room data (removing and re-adding the integration does **not** clear it either — Home Assistant doesn't delete a removed integration's storage files automatically); the practical effect will fade out on its own as new missions' data outweighs the old, though a proper reset option is worth adding — feedback welcome. Also improved in this release: room-recognition data is no longer discarded after a stuck event — it's corrected against the dock position once the robot returns, instead of being thrown away for the rest of the mission.

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [Troubleshooting](TROUBLESHOOTING.md)*
