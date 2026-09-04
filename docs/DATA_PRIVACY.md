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
| `roomba_plus_mission_archive` | older missions, rolled off the store above |
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

## How often it talks to the cloud

The library polls nothing on its own. Roomba+ decides, and the intervals are:

- **Live state** — pushed by the robot, not polled. No interval
- **Named shadows (Prime)** — every 2 minutes
- **Cloud enrichment (Classic)** — every 15 minutes, and only if you entered
  credentials
- **Schedules** — every 6 hours
- **Mission history** — every 24 hours

A robot that is idle and cloud-disabled generates no outbound traffic beyond
its own local connection.

## What is in a diagnostics download

This is the part worth reading before you send one to anybody, including to
this project.

**Removed automatically:**

```
irobot_username · irobot_password · password · blid
```

Your BLID is also replaced wherever it appears inside nested values, not only
where it is a key — including inside topic strings and URLs.

**NOT removed, because the download is useless without it:**

- **Room and zone names** — your own names for your own rooms
- **Schedules** — the days and times your robot runs, which describes when
  your home is likely empty
- **Map geometry** — room outlines and sizes, which is a floor plan
- **Mission history** — when the robot ran, for how long, and where
- **Capability flags and firmware versions** — identifies the model, not you

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
