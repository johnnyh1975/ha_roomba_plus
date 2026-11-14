[Roomba+](../README.md) › [Documentation](README.md) › V4/Prime robots

# Roomba+ — V4/Prime robots

iRobot's newer robots — the Combo and Plus 400 series, the Combo 105 and 505,
the Roomba Max 705 and similar — use a different protocol, called *Prime* or
V4 here. They have no local connection: everything goes through iRobot's
cloud, using the same account and servers as the iRobot app. Roomba+ talks to
them through its companion library
[roombapy-prime](https://github.com/johnnyh1975/roombapy-prime).

Prime support has been stable since 4.0.0 and is tested by field testers on
several models and accounts. Since 4.6.0 a Prime robot gets the entities a
Classic robot has wherever it reports the data; what still differs, and why,
is listed per section in
[Which generation, section by section](FEATURES.md#which-generation-section-by-section-460).

---

## Contents

- [Setting one up](#setting-one-up)
- [What works](#what-works)
- [What is not there](#what-is-not-there)
- [Not yet confirmed on a robot](#not-yet-confirmed-on-a-robot)
- [How data is updated](#how-data-is-updated)

---

## Setting one up

Settings → Devices & services → Add integration → **Roomba+** → **Set up with
my iRobot account**. Each Prime robot on the account becomes its own entry. The
account's Classic robots are offered alongside them under **Discovered**, and
need no press of the HOME button because their password comes from the account.

There is nothing to discover on your network for a Prime robot, so the
"add manually" path does not apply to it.

---

## What works

**Control**

- Start, pause, stop, return to the dock, and **locate**: an audible chime,
  without moving the robot.
- **Rooms by name** — `roomba_plus.clean_room`, with two-pass and suction
  level per room, and rooms from every saved map, so an automation works
  whichever floor the robot is parked on. Names match accent-tolerantly
  ("kuche" finds "Küche").
- **Zones** — `roomba_plus.clean_zone`, by name or id. The iRobot app no
  longer makes zone favourites; this is the way to send a robot to a zone from
  Home Assistant.
- **A room and zone select with a clean button**, across every map.
- **Favourites** — a button each, a `favorites` attribute on the vacuum, and
  `roomba_plus.run_favorite` keyed on the favourite's id, so renaming it in the
  app does not break an automation. A favourite carries its map, so it reaches
  a room on another floor.
- **Cleaning mode** (vacuum, mop, both) as a select that the next start uses
  for every room; **pad wetness** where the robot reports a wetness range;
  **SmartScrub** per room through `clean_room`; **suction level** with the
  app's names.
- **Settings as switches** — child lock, eco charging, two-pass cleaning,
  extra suction, and one switch per schedule.
- **Empty bin**, as a button, where the dock can empty the robot.
- **Quiet hours** (do not disturb) and the schedule as a calendar you can edit:
  `calendar.<robot>_prime_schedule`. Weekly schedules only; others are skipped
  rather than shown on a guessed date.

**Maps**

- **Live cleaning map** — the path drawn as the robot cleans, on the map it is
  using. Where the robot labels its paths with the cleaning mode, vacuumed,
  mopped and both are drawn in their own colours, and paths it only drove along
  are grey.
- **Rooms map** with outlines and names, ready at once because the cloud hands
  over finished polygons, with xiaomi-vacuum-map-card calibration.
- **A floor plan of each saved map** (off by default), and virtual walls read
  from the account.

**Status and history**

- Activity, battery, bin, mop tank, detected pad, runtime, firmware, dock,
  pad-wash and pad-dry status, and the robot's own connection to iRobot's cloud.
- **The error the robot reports**, in iRobot's own words in all eight
  languages, with the stale-error suppression Classic uses, and the robot's
  reasons for refusing a start.
- **Mission sensors and history** (since 4.6.0) — start, elapsed time,
  recharge and expiry, the last mission and its result, missions and
  completion rate over 30 days, the last error, the presence sensors, and a
  mission archive of up to 800 missions in the backup.
- **Room progress** for missions started in the app or by a schedule, from the
  mission timeline.
- **Events** — mission started, mission result, room completed, stuck and the
  command events, as on Classic.
- **A device tracker** with the room and its Home Assistant area.
- **Cloud stream times** — four diagnostic sensors (off by default) saying when
  each cloud stream last delivered.

**Maintenance**

- The maintenance list, calendar, events and repairs Classic robots have, with
  the parts and intervals from iRobot's cloud, in the app's own units: hours,
  routines, evacuations.

**Capability-gated entities.** An entity your robot cannot support — a mop pad
on a vacuum-only model, a pad-wash dock it does not have — is not created at
all. The diagnostics download names the flag that decided it.

---

## What is not there

- **Power off, spot clean, map training.** No Prime command for them is known.
  They are absent rather than shown and not working.
- **Wi-Fi signal and IP address** — a Prime robot sends no `signal` or
  `netinfo`. **Navigation statistics** have not been seen from one.
- **Layout-change detection, coverage map, stuck hotspots** — they are built on
  the local position grid of Classic robots.
- **Repairs that need the local connection or position data** — layout
  changes, the zone-naming prompt, the conflict with the built-in Roomba
  integration. The maintenance repairs work on Prime.
- **Schedule hold** is deliberately not offered: the robot accepts the setting
  and runs its schedule anyway, so a switch would say something false.

---

## Not yet confirmed on a robot

These work as far as the cloud confirms — the write is accepted and read back —
but their effect on the robot has not been observed:

- eco charging, two-pass cleaning and extra suction (child lock is confirmed:
  the robot announces it);
- the carpet boost setting;
- whether the robot keeps to quiet hours;
- marking a part done, which resets it at iRobot as the app does;
- the upper pad-wash range on some docks;
- a virtual-wall write that changes the list. Every confirmed write resent the
  existing walls unchanged, and the command replaces the whole list, so a
  partial list deletes what it leaves out.

Uploading a changed map is not built.

---

## How data is updated

| Data | How it updates |
|---|---|
| Status, activity, live map and position | Pushed by iRobot's cloud as they change |
| Mission history | At startup, after each mission and every 6 hours |
| Consumable parts | Every 6 hours |
| Schedules, room names, quiet hours, favourites | Every 15 minutes |

A command sent from Home Assistant goes out at once; the state change it causes
arrives by push. All the Prime robots of one iRobot account share one cloud
connection (since 4.5.4).

The protocol work behind each confirmed piece is in the
[release notes](../release-notes/).

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
