[Roomba+](../README.md) › [Documentation](README.md) › Data privacy & data flow

# Data privacy & data flow

What Roomba+ sends where, what it keeps on your disk, and what is in a
diagnostics download — verified against the current code rather than written
from memory. If anything here stops matching the code, that is a documentation
bug; please open an issue.

This covers **the whole stack**. Roomba+ uses a library called
`roombapy-prime` for V4/Prime robots, which you did not choose separately and
should not have to read the documentation of. Its own
[DATA_PRIVACY.md](https://github.com/johnnyh1975/roombapy-prime/blob/main/docs/DATA_PRIVACY.md)
covers the same ground for people writing code against it, and it names two
things as "a decision for whatever you build on top" — what gets written to
disk, and how often data is fetched. Roomba+ is that thing, and both are
answered below.

## Where your data goes

**Only to iRobot's own infrastructure, and to your own Home Assistant.**
There is no analytics, telemetry, crash reporting, or intermediary server of
any kind, in either the integration or the library.

For V4/Prime robots:

- iRobot's login chain — your username and password go to iRobot's own
  identity provider and auth service, exactly as their app does
- AWS Cognito for temporary credentials, on **iRobot's** AWS account
- AWS IoT over MQTT for the live connection, and iRobot's REST API for maps,
  schedules, favourites and history

For Classic robots the local connection talks **only to the robot on your own
network**. Cloud features are optional and off unless you enter credentials.

Nothing is sent to the maintainer, to this project, or anywhere else. A
diagnostics download goes wherever *you* send it — see below.

## What Roomba+ keeps on your disk

The library writes nothing. Roomba+ does, and this is the list:

| Store | What is in it |
|---|---|
| `roomba_plus_missions` | mission history: times, durations, area, rooms cleaned |
| `roomba_plus_mission_archive` | the robot's mission history as iRobot's cloud keeps it (Classic with your iRobot login), read from `/missionhistory` |
| `roomba_plus_mission_timer` | the running mission's elapsed time |
| `roomba_plus_maintenance` | when each part was last reset |
| `roomba_plus_robot_profile` | model, battery type, capacity |
| `roomba_plus_zones` | your zone names and aliases |
| `roomba_plus_roomseg` | room ids and names |
| `roomba_plus_geometry`, `_grid`, `_outline` | map geometry for rendering |
| `roomba_plus_trajectories` | where the robot drove, for the map trail |
| `roomba_plus_dirt_threshold` | learned per-room dirt levels |
| `roomba_plus_freeze` | which rooms are excluded from automatic cleaning |

These live in Home Assistant's own `.storage` directory, on your machine, and
are removed when you delete the config entry.

**Nothing here is uploaded anywhere.** The map trail in particular is a record
of your floor plan and when rooms were occupied enough to need cleaning — it
stays local, and you can turn the trail off in the options.

Credentials are held by Home Assistant's config entry storage, the same as
every other integration.

## How data is updated, and how often it talks to the cloud

**Classic robots, local connection.** The robot pushes its state over the local
connection and Home Assistant updates the moment it reports; there is no
polling. If the connection drops, the entities that show the robot's live state
become unavailable until it reconnects, rather than showing old values as
current.

**Classic robots with an iRobot account (optional).** Mission history, Smart
Map rooms, favourites and consumable parts are read from iRobot's cloud once a
day and right after each mission: about 90 seconds after it ends, and once more
10 minutes after the end if iRobot's cloud had not caught up by then. A change made only in the
iRobot app, such as a renamed room, can therefore take up to a day to appear.
The robot itself stays on the local connection either way.

**Prime robots (cloud only):**

| Data | How it updates |
|---|---|
| Status, activity, live map and position | Pushed by iRobot's cloud as they change |
| Mission history | At startup, after each mission and every 6 hours |
| Consumable parts | Every 6 hours |
| Schedules, room names, quiet hours, favourites | Every 15 minutes |

A command from Home Assistant goes out at once; the state change it causes
arrives by push. A Classic robot without an account generates no traffic beyond
its own local connection.

## What is in a diagnostics download

This is the part worth reading before you send one to anybody, including to
this project.

**Removed automatically:**

```
irobot_username · irobot_password · password · blid
```

Any key whose name contains `password`, `passwd`, `secret`, `token`,
`apikey`, `privatekey`, `credential` or `passphrase` is replaced at every
depth of the file, whatever the section. A field added by a future
firmware is covered by the same rule rather than needing to be listed.

Your BLID is also replaced wherever it appears inside nested values, not only
where it is a key — including inside topic strings and URLs.

**NOT removed, because the download is useless without it:**

- **Room and zone names** — your own names for your own rooms
- **Schedules** — the days and times your robot runs, which describes when
  your home is likely empty
- **Map geometry** — room outlines and sizes, which is a floor plan
- **Mission history** — when the robot ran, for how long, and where
- **Capability flags and firmware versions** — identifies the model, not you
- **Commands Roomba+ sent** (v4.2.5+) — the last dozen, with their time,
  the rooms or zones named, and whether each reached the broker. This is
  what makes "it did nothing and reported no error" diagnosable at all

If any of that matters to you, edit the file before sending it, or send the
part that is relevant. **A partial download is more useful than none**, and
several of this project's investigations were solved from a single block.

The one thing worth saying plainly: nothing in a diagnostics download
identifies you personally unless your own room or zone names do.

## Getting rid of it

Deleting the config entry removes every store listed above. Nothing survives
in the integration's own files.

What Roomba+ cannot remove is anything held by **iRobot** — mission history,
maps and schedules live on their servers, and this integration only reads
them. Deleting the integration does not delete anything from your iRobot
account.

---

[Documentation home](README.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Questions and reports](https://github.com/johnnyh1975/ha_roomba_plus/issues)
