# Roomba+ — Upgrade Notes

[← Roomba+](../README.md)

Per-version upgrade notes and config entry migrations, oldest relevant
first appears last. Only versions with a genuine migration step, a
behaviour change, or a "why is this sensor still Unknown" learning-period
note are listed — most releases need zero action beyond updating.

---

## v4.2.13 — from v4.2.12

**Missions can appear in the history after the fact.** A Classic robot
with a cloud account now records cloud missions that have no local
record, at the next cloud refresh. Missions lost since 4.2.11 come back
this way, so mission counts and statistics for the past days can go up
once after updating. They do not fire the *mission completed* event.

**A room the robot only reached is no longer recorded as cleaned.** The
last room of a mission counts only if the robot cleaned there. Room
history and overdue rooms can therefore list one room fewer than before
for missions that ended early.

---

## v4.2.12 — from v4.2.11

**Mission elapsed time reads *Unknown* on the dock.** It kept a number
between missions that was the time since the last mission started, not
how long it ran. An automation or card that read it after a mission
ends should use **Missions – Last duration** instead.

**Mission start and elapsed time stay filled during a mid-mission
recharge.** Mission start used to read *Unknown* while the robot topped
up on the dock. An automation that treated that as "mission over" should
use the **Mission active** binary sensor, which has always stayed on
through a recharge.

---

## v4.2.11 — from v4.2.10

### Config entries on schema versions 4 to 10 could not be migrated

**If your Roomba+ stopped loading at some point and you never worked
out why, this may have been it.** An entry on one of those versions —
from the v2.1.x series — matched no migration step, never advanced, and
Home Assistant refuses to load an entry it cannot bring up to date. No
error named the cause.

Seven per-entity migration steps were collapsed into a single version
jump some releases ago. The jump's own condition was correct; it was
nested one level too deep, inside the branch for version 3 alone. An
entry on 3 worked. Everything from 4 to 10 fell through.

Nothing is needed from you: the next start migrates the entry and it
loads. A test now walks every version from 1 to the current one and
asserts each arrives — the guard that was there before searched the
source for the condition's text, found it, and could not see which
branch it sat in.

### Fifteen entities report *Unknown* rather than *Unavailable*

**Fifteen entities no longer go *Unavailable* when they simply have
nothing to report.** They read *Unknown* instead, and two are no longer
created at all on robots that cannot supply them.

Alongside **Mission start** and **Mission elapsed time**, this covers
the last-mission sensors (result, duration, area), the error sensors
(code, time, zone), problem zone, consecutive mission anomalies,
estimated battery end-of-life, lifetime completion rate, and the three
maintenance "last cleaned" timestamps.

In Home Assistant, *Unavailable* means the device cannot be reached.
*Unknown* means it is fine and has no value right now — which is what a
docked robot that has never errored, or a fresh install with no mission
history, actually is. Tools that list broken entities, such as Orphan
Entity Cleaner, stop flagging these.

**Battery cycle count** and **Battery age** now disappear entirely on
robots that do not report the underlying data, instead of existing
permanently unavailable. If your robot never populated them, they will
vanish from the entity list — nothing was lost; they never had a value.

**Nothing about the values changes.** Nothing is retained that was not
retained before, and no sensor reports something it did not report
before — only the state it shows while empty is different.

**If you have an automation or template that tests any of these sensors
for being unavailable, update it.** A condition written as
`is_state('sensor.roomba_mission_elapsed_time', 'unavailable')` will no
longer fire; test for `unknown`, or better, check the robot's activity
directly. Anything that tests `has_value()` or compares the numeric
state keeps working unchanged.

Looking for the **completed** mission's figures? Those live in
**Missions – Last** (when it started) and **Missions – Last duration**
(how long it took), and they keep their values until the next mission.

### Room history counts a room the robot cleaned in, even if it did not finish

On a robot with a cloud account, room history is built from iRobot's own
mission record. Until now a room counted only when its pass *finished*.
A room the robot cleaned but did not finish — cut short by a recharge, a
time limit, or the end of the mission — was left out, while the iRobot app
showed it cleaned (@Thonno: the Kitchen, first of four rooms, missing).

It now counts whenever the robot cleaned floor there. **One consequence:**
if you stop a mission part-way through a room, that room's *last cleaned*
time moves to that mission. Room *coverage* is unchanged — it still counts
only finished passes, as does everything that decides when a mission has
ended.

### Live state goes *Unavailable* when the robot cannot be reached

**If the local connection stays down for more than a minute, the entities
that describe what the robot is doing right now become *Unavailable*** —
the vacuum, phase, readiness, battery, signal strength, position, mission
progress and the running mission's times. Until now they kept their last
value indefinitely: a robot with a flat battery read "cleaning, 99 %" for
hours (@mdarocha).

History, counters, settings and the last mission keep their values, since
they are still true while the robot is away. **Connected** and **MQTT
stale** stay available — they are how you see the outage. Cloud-connected
Prime robots are not affected.

The minute of grace rides out a Wi-Fi blip: the robot reconnects on its
own, and nothing flickers.

**If an automation reacts to the vacuum's state,** decide what it should
do when that state is `unavailable` — it will now see it whenever the
robot is out of reach.

### Side brush and Clean Base bag reset buttons on local robots

These two existed only when iRobot's cloud reported the part, and
Classic robots never receive that data — a fully working cloud
connection still returns no consumable list for them. So a locally
operated robot had no way to reset those counters at all, and the
maintenance sensor stayed overdue for good after a replacement.

They now appear for any robot that has the parts: a Braava still gets
no side-brush button, a robot without a Clean Base still gets no bag
button. **If you did not have these two before, they will show up as
new entities.** Pressing one records the replacement locally and then
reports it to iRobot if there is a cloud connection — the order the
filter and main-brush buttons have always used, so a cloud outage no
longer costs you the reset.

### Under the hood

Nothing to do for any of these; listed because they change behaviour
you could otherwise notice.

- **Background work is now owned by the config entry.** Forty-two places
  scheduled tasks on Home Assistant itself, which meant they kept
  running after a reload — including statistics backfills writing to
  stores that were being closed. They are cancelled on unload now, and
  their exceptions reach the log: twenty-one of them used a bridge that
  swallowed errors completely, repair checks after every mission
  included
- **The four stores whose contents cannot be rebuilt** — maintenance
  history, mission history, mission timers, learned measurements — now
  record a payload version. Nothing changes today; it means a future
  format change is refused cleanly instead of misread. Existing files
  without the field are read as the current format, so nothing is lost
- **The REST endpoints send an `X-Roomba-Plus-Api-Version` header.**
  Deliberately useless today: the Lovelace card ships separately and
  cannot check a version that is not being sent, so this has to be out
  in the field first
- **The mission callback was restructured, with no change in behaviour.**
  It is the code that decides when a mission starts, which room the
  robot is in, and when a mission has really ended. Its state had lived
  in eighteen hidden variables and its logic in one 1,230-line function;
  it is now five named steps. Along the way the end-of-mission check and
  its own log line turned out to compute the same decision twice, in two
  slightly different ways that only agreed because of a rule enforced
  somewhere else — they now share one computation, so a log saying why
  a mission was not yet closed always describes what the code did.
  Checked by replaying a real three-room field mission through the old
  and new code: identical room changes, identical refusals, identical
  end

---

## v4.2.10 — from v4.2.9

One new sensor appears on its own: **Missions – Last area**, the square
metres your robot covered on its last completed mission. It stays empty
until the next mission finishes, and on robots that report no area at
all (600-series) it stays empty for good.

**Room time estimates settle from here.** Roomba+ measures how long each
room takes and now averages those measurements instead of keeping only
the most recent one, separately per cleaning mode. Anything measured
before this release counts as a single run, so the figures move for a
mission or two and then steady.

---

## v4.2.9 — from v4.2.8

Nothing to do.

**If your remaining-time estimate dropped to zero mid-mission**, that is
this release. It showed up on robots whose measured room times are
shorter than the estimate they replaced.

**If you saw "Two Roomba integrations installed" with only one
visible**, that notice was counting entries that never load — an ignored
discovery or a disabled entry. It will not reappear.

---

## v4.2.8 — from v4.2.7

Nothing to do.

**If your room display moved but the percentage stayed at 0%**, that is
this release. It affected any robot that cleans a room faster than its
estimate.

---

## v4.2.7 — from v4.2.6

Nothing to do.

**If your room display started working in 4.2.6 but every mission still
began with no time estimates**, that is this release. The measurements
4.2.6 was keeping are now read back.

**`last_cleaned_rooms` on a locally connected robot** was showing the
rooms of whichever mission last resolved successfully — often a much
older one. It now lists the rooms the robot actually worked in.

---

## v4.2.6 — from v4.2.5

Nothing to do.

**If your room display never moved past the first room**, that is the
one this release repairs. The first mission after upgrading also gives
your robot a real time for each room it finishes, so the second one
tracks better than the first.

---

## v4.2.5 — from v4.2.4

Nothing to do.

**If your robot has more than one map**, zones now follow the robot the
way rooms already did in 4.2.4. Picking a map explicitly in the Map
entity applies to both.

**Four new buttons** appear on every robot: mop pad replaced, wheels
cleaned, charging contacts cleaned, bin cleaned. These were documented
as actions in earlier versions and never actually existed, so any
automation calling `roomba_plus.reset_pad`, `reset_wheel_cleaning`,
`reset_contact_cleaning` or `reset_bin_cleaning` has been failing with
"service not found" — press the button or call `button.press` instead.
The same applies to `reset_filter`, `reset_brush` and `reset_battery`,
whose buttons already existed.

**`room_passes` takes more per room.** Alongside `two_pass`, each entry
now accepts `cleaning_mode`, `smart_scrub` and `pad_wetness`. Existing
`room_passes` blocks keep working unchanged.

---

## v4.2.4 — from v4.2.3

Nothing to do.

**If the live room display never moved for you**, this is the release
that addresses it. It now follows the robot's own report that it
finished a room and drove away, rather than waiting for a time estimate
to agree.

---

## v4.2.3 — from v4.2.2

Nothing to do.

**If cleaning a room on a second map still failed after 4.2.2**, your
robot was holding the bad value rather than us sending it. This clears
it — or one cleaning started from the iRobot app on that map clears it
immediately, on any version.

---

## v4.2.2 — from v4.2.1

Nothing to do.

**If your robot has more than one map** and cleaning rooms on the
second one used to fail with a localisation error, that is fixed.

---

## v4.2.1 — from v4.2.0

Nothing to do.

---

## v4.2.0 — from any 4.1.x

Nothing to reconfigure. Every fix from 4.1.1 through 4.1.8 is included,
and the 4.1 line has ended.

**Check automations that read the readiness sensor.** Its state used to
be English text (`Ready`, `Off dock`) and is now a key your language
translates for display (`ready`, `off_dock`). A comparison against
`"Ready"` stops matching, and it stops silently.

**If a cleaning mode you used to select has disappeared**, your robot
cannot perform it — the selection was being quietly ignored before.

**If you are on 4.1.2 or earlier**, cleaning passes and suction level
never worked on i7/s9/j7-class robots. They do now; you may want to set
them again.

---

## v4.2.0b7 — from v4.2.0b6

**The readiness sensor changes its values — check your automations.**
It used to report English text as its state: `Ready`, `Off dock`. It now
reports a key your language file translates: `ready`, `off_dock`.

An automation comparing the state to `"Ready"` stops matching, and it
stops silently. Compare to `"ready"` instead.

Otherwise the same fixes as 4.1.8.

---

## v4.2.0b6 — from v4.2.0b5

Nothing to do. Same changes as 4.1.7 — see those notes.

---

## v4.2.0b5 — from v4.2.0b4

Nothing to do. Three fixes, also in 4.1.6 — see those notes.

---

## v4.2.0b4 — from v4.2.0b3

**Take this one.** b3 stopped every entity from loading on Home
Assistant 2026.x. If your robots disappeared after updating to b3, this
restores them; nothing needs reconfiguring.

---

## v4.2.0b3 — from v4.2.0b2

A straight update, and a worthwhile one for anyone with more than one
map: rooms on every floor can now be mapped to Home Assistant areas on
both generations, and a stored mapping that quietly stopped working
should work again.

**If you built an automation around `robot_lifted`**, it still reads
exactly the same. The same value now also appears as `pick_events`,
which is what it actually measures — the old key is not going away
without notice.

---

## v4.2.0b2 — from v4.2.0b1

A straight update. Four field fixes and one that needed a library
release; see the release notes.

**Cleaning passes and suction actually work now** on i/s/j robots. They
never did: the firmware reads each pair as one value and dropped both
halves when they arrived separately, while reporting success. If you had
given up on those two controls, try them again.

---

## v4.2.0b1 — from v4.1.2

**Two settings disappear from the options form, and one of them may change
how your robot behaves.**

Roomba+ now uses roombapy 2.x, which keeps one supervised connection and
reconnects on its own. It has no polling mode, so **Continuous connection**
and **Connection delay** no longer exist — offering them would offer a
choice nothing reads.

**If you had continuous connection turned OFF**, your robot now keeps a
persistent connection anyway. That is the behaviour the option's own
description recommended, and for almost everyone it is an improvement — but
it is a change to a setting you chose, so it is written to your log once on
startup. Nothing to do about it; there is no polling mode to go back to.

The stored values stay in your config entry, unread. No migration runs, and
downgrading to 4.1.0 restores the form with your old values intact.

**Everything else should be identical.** Every command to a Classic robot
now travels a different route internally — 46 call sites moved from a worker
thread to the event loop — and no part of that has met real hardware yet. If
something that worked in 4.1.0 does not work here, that is the bug this beta
exists to find.

**Also:** the tested Home Assistant minimum is now genuinely 2025.5 (Python
3.13.2+), which is what the manifest has claimed since 4.0. CI had been
testing 2025.1.4 on Python 3.12 — a combination no installation can be in.

---

## v4.1.4 — from v4.1.x

Nothing to do. Four field fixes; see the 4.1.2 release notes. Several
were silent failures, so an automation written to work around one may
behave differently now.

---

## v4.1.0 — from v4.0.x

Nothing to do. Four silent bugs fixed and two new entities added; see the
release notes.

**One thing worth checking if you built around a bug**: schedules created
from the calendar did nothing on i/s/j robots, `clean_zone` sent zones as
rooms, the zone button ignored selections from non-active maps, and
`clean_room` could not name rooms on other maps. An automation written to
work around any of those may behave differently now that they work.

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
