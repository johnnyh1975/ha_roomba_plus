# Roomba+ — Upgrade Notes

[← Roomba+](../README.md)

Per-version upgrade notes and config entry migrations, oldest relevant
first appears last. Only versions with a genuine migration step, a
behaviour change, or a "why is this sensor still Unknown" learning-period
note are listed — most releases need zero action beyond updating.

---

## v4.1.8 — from v4.1.7

Nothing to do.

**If `clean_room` ever told you a room does not exist** while you could
see it in the selector, that is fixed.

**The readiness sensor changes its values — check your automations.**
It used to report English text as its state: `Ready`, `Off dock`,
`Not ready (68)`. It now reports a key that your language file
translates for display: `ready`, `off_dock`, `not_ready_68`.

The dashboard will read better than before. But an automation comparing
the state to `"Ready"` stops matching, and it stops silently. Compare to
`"ready"` instead — lower case, and the displayed German text is never
what you compare against.

---

## v4.1.7 — from v4.1.6

Nothing to do.

**If your robot is an S9-series**, the room display will now follow it
through the house instead of staying on the first planned room.

**If you have two maps**, cleaning a zone works now.

**If you use the cleaning-mode selector**, it now lists only the modes
your robot can perform. If an option you used to pick has disappeared,
your robot could not do it — selecting it was being quietly ignored.

---

## v4.1.6 — from v4.1.5

Nothing to do.

**If you have zones**, cleaning one from the room selector works now.
Before this it started a mission that ended within a minute with nothing
cleaned — the zone was being sent as a room.

---

## v4.1.5 — from v4.1.4

Nothing to do. Diagnostics downloads carry two more fields; no
behaviour changes.

---

## v4.1.4 — from v4.1.3

**Take this one if you are on 4.1.3.** That release stopped every
entity from loading on Home Assistant 2026.x — if your robots
disappeared, this restores them. It also clears an error `clean_room`
logged after the robot had already cleaned.

---

## v4.1.3 — from v4.1.x or v4.0.x

Nothing to do. Field fixes; see the release notes.

**Worth knowing if you have more than one map.** Rooms on every floor
can now be mapped to Home Assistant areas, and a stored mapping that had
quietly stopped working should work again.

**If you built an automation around `robot_lifted`**, it still reads
exactly the same. The same value now also appears as `pick_events`,
which is what it actually measures.

**One thing worth re-checking if you built around a bug.** Several of
these were silent failures — a zone clean that reported success and did
nothing, a maintenance reminder for a part that does not exist, a
room/zone list that was short without saying so. An automation written
to work around any of them may behave differently now.

**If you came from 4.0.x**, read the v4.1.0 section below as well: it
added entities, and four other silent bugs were fixed there.

---

## v4.1.0 — from any v4.0.x

Nothing to do. Four silent bugs fixed and two new entities added.

Schedules created from the Home Assistant calendar did nothing on
i/s/j robots, `clean_zone` sent zones as rooms, the zone button ignored
selections made on a non-active map, and `clean_room` could not name
rooms on other maps. If an automation of yours worked around one of
those, it is worth a look.

---

## v4.0.0 (alpha) — from any v3.x

**No migration step, and no entity ids change.** The v4 line adds support for
Prime-generation robots (Roomba Max, Combo 400-series) and carries a number of
Classic fixes with it.

**One thing to do after upgrading:** the maintenance to-do list is now **opt-in**
on both generations. If you use it, switch it back on in the integration options
— it takes a place in the sidebar, and it should be asked for rather than
assumed.

**If you have several Prime robots:** expect **fewer** favourite buttons per
robot from v4.0.0a41 on. Favourites are an account-level list and every robot
was previously shown all of them; the ones that disappear belonged to a
different robot, and pressing them sent that robot's map regions to this one.

**Going back to v3.5.x** works for Classic robots and does not for Prime ones —
the stable line cannot connect to them at all.

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
404 until enough new ones accumulate. Mission maps are field-confirmed on
both Braava jet m6 (sapphire firmware family) and i-series lewis firmware
(July 2026, Thonno, i7) — see [Feature reference](FEATURES.md#mission-history--room-intelligence)
if you still get a 404 mentioning "no coverage layer" on another model.

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
