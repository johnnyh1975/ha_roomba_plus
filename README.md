# Roomba+ for Home Assistant

[![Marketplace](https://img.shields.io/badge/Marketplace%20%2F%20HACS-Default-blue.svg)](https://github.com/hacs/default)
[![Version](https://img.shields.io/badge/Version-4.6.0b2-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2025.5%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Platinum-blueviolet.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Hassfest](https://github.com/johnnyh1975/ha_roomba_plus/actions/workflows/hassfest.yml/badge.svg)](https://github.com/johnnyh1975/ha_roomba_plus/actions/workflows/hassfest.yml)
[![HACS Validation](https://github.com/johnnyh1975/ha_roomba_plus/actions/workflows/hacs.yml/badge.svg)](https://github.com/johnnyh1975/ha_roomba_plus/actions/workflows/hacs.yml)
[![Downloads](https://img.shields.io/github/downloads/johnnyh1975/ha_roomba_plus/total?label=downloads)](https://github.com/johnnyh1975/ha_roomba_plus/releases)

**Your iRobot Roomba and Braava in Home Assistant — locally, with a live map, rooms,
maintenance and mission history.**

Roomba+ connects to Classic robots (600, 900, i, s, j series and Braava m6) over your own
network, with no cloud account and no polling, and to the newer V4/Prime robots (Combo and
Plus 400 series, Combo 505, Roomba Max) through iRobot's cloud. It is a custom integration
at Home Assistant's highest quality level, Platinum, in eight languages.

![A Home Assistant dashboard with Roomba+: the robot cleaning, its live path on the room map, status, history and maintenance](https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/docs/images/dashboard.png)

<sub>Screenshots come from a demo Home Assistant with a simulated Roomba 980; the rooms and
history are made up.</sub>

## Highlights

- **Live map and rooms.** The path is drawn as the robot cleans, on a map with your rooms.
  Clean rooms by name with `roomba_plus.clean_room` or Home Assistant's own
  `vacuum.clean_area`, mapped to your Home Assistant areas.
- **Starts that fit your home.** `roomba_plus.smart_start` waits while a door is open or
  someone is home, cleaning can follow your presence, and `clean_overdue_rooms` or
  `auto_clean_dirty_rooms` pick only the rooms that need it.
- **Maintenance that knows your robot.** Filter, brushes, battery and dock parts count down
  from your robot's own usage, show up as a to-do list, a calendar and a repair when
  overdue, and link to a care guide.
- **Missions you can automate on.** Events when a mission starts and how it ended, the rooms
  it missed, the room it is in and when it will finish, and a record of every mission.
- **Answers for scripts and Assist** *(4.6, beta)*. `plan_clean` says whether a clean started
  now would be done in time; `get_rooms`, `get_missions` and `get_mission` return the rest.
- **Local first.** Classic robots work without an iRobot account; the account adds cloud
  room names, favourites and history. Nothing goes anywhere but your Home Assistant and
  iRobot — see [data privacy](docs/DATA_PRIVACY.md).

## Supported robots

| Series | Examples | Connection | Tested on |
|---|---|---|---|
| 600 | Roomba 694, 692, 671 | local | Roomba 694 |
| 900 | Roomba 980, 985 | local | Roomba 980 |
| i, s, j series | i3, i7+, s9+, j7+, Combo j9+ | local, iRobot account optional | i7+, s9+, j7+, Combo j9+ |
| Braava | jet m6 | local, iRobot account optional | Braava jet m6 |
| V4/Prime | Combo 105, Plus 405/410/416 Combo, Combo 505, Roomba Max 705 | iRobot cloud | several models, see [V4/Prime robots](docs/PRIME.md) |

**What works on which robot:**

| | 600 | 900 | i / s / j | Braava m6 | Prime |
|---|---|---|---|---|---|
| Live map and cleaning path | — | ✅ | ✅ | ✅ | ✅ |
| Clean by room | — | — rooms are found and tracked, the robot cannot be sent to one | ✅ | ✅ | ✅ |
| Room names, favourites, history from the iRobot cloud | — | history | ✅ with account | ✅ with account | ✅ |
| Presence-aware scheduling, demand cleaning | — | ✅ | ✅ | ✅ | ✅ |
| Room rhythms, overdue rooms, mission maps | — | — | ✅ with account | ✅ with account | ✅ |
| Maintenance reminders, to-do list, calendar | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mop sensors: tank and pad | — | — | Combo models | ✅ | Combo models |
| Works without internet | ✅ | ✅ | ✅ | ✅ | — |

Each feature, and which robots it needs, is in the [feature reference](docs/FEATURES.md).
Prime robots have their own page: [V4/Prime robots](docs/PRIME.md).

## How it compares

| | Roomba+ | Built-in Roomba integration | roomba_rest980 |
|---|---|---|---|
| Connection | Local push; iRobot account optional | Local push | Polls a rest980 Docker container; iRobot account required |
| Robots | 600, 900, i, s, j, Braava m6 and Prime | Classic robots; it lists 690, 890, 960, 980, s9+, Braava m6 | Smart Map robots (i, s, j) |
| Map and rooms | Live path, rooms map, cleaning by room and area | — | Floor plan from the cloud, no live path |
| Automation | Blocking sensors, presence, demand cleaning, mission events, response actions | Start, stop, return | Room selection, cloud routines |
| Entities | 100+, maintenance and history included | 13 | About 30, plus room selects and favourite buttons |

Neither of the other two supports Prime robots. The full comparison, with when to choose
which: [Roomba+, the built-in integration and roomba_rest980](docs/COMPARISON.md).

## Installation

Requires **Home Assistant 2025.5** or newer.

Install the current stable release, whatever your robot: one release line serves both
generations. Releases before 4.0 cannot connect to Prime robots at all, and the 4.1 and 4.2
lines have ended. Which version you have: Settings → Devices & services → Roomba+.

### Marketplace — Home Assistant 2026.11 and later

[![Open your Home Assistant instance and show Roomba+ in the Marketplace (HACS before Home Assistant 2026.11).](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=johnnyh1975&repository=ha_roomba_plus&category=integration)

1. Settings → **Marketplace** → search for **Roomba+**.
2. Install it, then restart Home Assistant if it asks you to.

Roomba+ is in the catalog every Marketplace user sees, so there is no repository to add and
no GitHub account to connect. If you installed it from HACS before updating Home Assistant,
it moved to the Marketplace by itself and keeps getting updates.

### HACS — Home Assistant before 2026.11

1. HACS → search for **Roomba+**.
2. Download the newest release, then restart Home Assistant.

**Betas** are offered only when you switch them on for Roomba+: *Include pre-releases* in
the menu of its Marketplace page, or *Show beta versions* in HACS. The current beta is
**4.6.0b2** — see [what 4.6 adds](#whats-new-in-46-beta).

### Manual

Copy `custom_components/roomba_plus/` into the `custom_components` folder of your
configuration and restart Home Assistant.

## Setup

Settings → Devices & services → **Add integration** → **Roomba+**.

| Choice | When to use it | What you enter |
|---|---|---|
| **Set up with my iRobot account** (recommended) | Any robot; required for Prime | Your iRobot app email and password. Every robot on the account is found; the first is set up, the others appear under **Discovered** and need one click each. |
| A robot found on your network | Classic robots, without an account | Nothing: hold the robot's **HOME** button for about two seconds when asked, until it plays tones. |
| **Add manually** | Classic robots Home Assistant did not find | The robot's local IP address, as your router shows it, then HOME as above. You are asked for the robot's password only if reading it fails. |

You can add or change the iRobot account later: Roomba+ → **Configure** → *iRobot cloud
credentials*. A new IP address or password: ⋮ → **Reconfigure**.

<img src="https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/docs/images/options-menu.png" alt="The Configure dialog of Roomba+ with its sections: connection settings, iRobot cloud credentials, blocking sensors, presence-aware scheduling, zone management, rooms and zones, maintenance" width="480">

Every option and what it does: [every option, and what it costs you to leave it off](docs/FEATURES.md#every-option-and-what-it-costs-you-to-leave-it-off).

> **Coming from the built-in Roomba integration or roomba_rest980?** Remove or stop it
> first — a robot takes one local connection at a time. Step by step:
> [moving to Roomba+](docs/MIGRATING.md).

## First steps

1. **Open the robot's device page.** The live map is `image.<robot>_map` on 900-series and
   Smart Map robots; an installation from an older version may call it
   `image.<robot>_cleaning_map`.
2. **Name your rooms.** A 900-series robot's rooms are found from its missions after three
   of them, and a repair notice asks you to name them (Configure → *Rooms & zones*). Smart
   Map and Prime robots take the names from your iRobot account.
3. **Map rooms to Home Assistant areas** (Home Assistant 2026.3 and later): vacuum entity →
   settings → *Map vacuum segments to areas*. Then `vacuum.clean_area`, the device tracker
   and the area sensors know which room is which.
4. **Use `roomba_plus.smart_start` instead of `vacuum.start`** in automations if you want
   starts to wait for a door, a sleeping baby or an empty home
   (Configure → *Blocking sensors*).
5. **Mark parts done when you replace them** — in the to-do list `todo.<robot>_maintenance`,
   with the reset buttons, or with the Done button of the maintenance notification. Your
   schedule is `calendar.<robot>_schedule` (`calendar.<robot>_prime_schedule` on Prime),
   and you can edit it there.

The blueprints Roomba+ ships — notifications, maintenance with Done and Later buttons,
vacuum-then-mop and more — are already in Settings → Automations & scenes → Blueprints
from 4.6. Recipes: [automations & dashboards](docs/AUTOMATIONS.md).

## What's new in 4.6 (beta)

4.6.0b2 is a pre-release. Everything in it is additive, and none of it has run on a robot
yet, so reports are welcome.

- **Prime robots get the entities Classic robots already had** — mission and history
  sensors, the stuck event, room progress for missions started in the app, and more, each
  once the robot reports its data.
- **Mission events**: `roomba_plus_mission_started` and `roomba_plus_mission_result`, with
  event entities, the rooms skipped or not reached, and `roomba_plus.retry_missed_rooms`.
- **A start the robot ignored** is reported on `event.<robot>_command` and sent once more.
- **Response actions** `get_rooms`, `get_missions`, `get_mission` and `plan_clean`.
- **Current room and estimated end** as sensors (Prime, and Smart Map robots with an
  account), and **when each Home Assistant area was last vacuumed and mopped**.
- **Send home after a pause**, battery used per mission, and blueprints that install
  themselves.

Details: release notes [b1](release-notes/v4.6.0b1.md), [b2](release-notes/v4.6.0b2.md) · [upgrade notes](docs/UPGRADING.md#v460--from-v45x).

## Documentation

| | |
|---|---|
| [Feature reference](docs/FEATURES.md) | Every entity, action, event and option, and which robots support it |
| [V4/Prime robots](docs/PRIME.md) | What works on the cloud robots, what does not, and why |
| [Automations & dashboards](docs/AUTOMATIONS.md) | Recipes and the shipped blueprints |
| [Moving to Roomba+](docs/MIGRATING.md) | From the built-in integration or roomba_rest980, more robots, replacing a robot |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common problems by topic, and how to send a diagnostics download |
| [Upgrade notes](docs/UPGRADING.md) | What changes when you update, version by version |
| [Data privacy](docs/DATA_PRIVACY.md) | Where your data goes, how often, and what a diagnostics download contains |
| [REST API](docs/API.md) | Endpoints for cards and scripts |
| [xiaomi-vacuum-map-card](docs/xiaomi-vacuum-map-card.md) | Using Roomba+ with the interactive map card |
| [Comparison](docs/COMPARISON.md) | Roomba+, the built-in integration and roomba_rest980 side by side |
| [Release notes](https://github.com/johnnyh1975/ha_roomba_plus/releases) | What changed in each version |

**Companion card.** [ha_roomba_plus_card](https://github.com/johnnyh1975/ha_roomba_plus_card)
puts the map, room buttons and mission history in one card. Add it as a custom repository of
type *Dashboard* (Marketplace: add from link, with a connected GitHub account). Home
Assistant's own cards are enough for the controls alone.

## Removal

1. Settings → Devices & services → Roomba+ → ⋮ → **Delete**, once for each robot. This also
   deletes everything Roomba+ stored for the robot: mission history, maps, coverage and
   maintenance timers. To keep it, call `roomba_plus.create_backup` first.
2. Remove Roomba+ in the Marketplace (or HACS), or delete `custom_components/roomba_plus`
   for a manual install, and restart Home Assistant.

The robot keeps its schedules and settings, and the iRobot app works as before.

## Help and contributing

- **Something wrong?** Open an [issue](https://github.com/johnnyh1975/ha_roomba_plus/issues)
  and attach a diagnostics download (device page → *Download diagnostics*). It carries no
  password, address or account details.
- **A robot or firmware not listed above?** A diagnostics download is often more useful than
  a description.
- **Translations** — English, German, French, Italian, Spanish, Portuguese, Dutch and Polish.
  A native speaker's corrections are welcome as an issue or a pull request.
- **Code** — pull requests are welcome; [AGENTS.md](AGENTS.md) has the rules and the checks
  to run. For anything larger than a small fix, open an issue first.

## Credits

**Field testing** — real-device reports from these people drove fixes, cancelled features
that did not hold up and shaped the plan: **Thonno** (i7+), **veronoicc** (i7+, i8+),
**boutXIII** (Braava jet m6), **ronluna** (S9+), **KingAntDesigns** (Braava jet m6, j7+),
**mdarocha** (i3+), **chairstacker**, **jayjay13011**, **DaRealGuGu**, **arielgr**,
**utkjmitch**, **jadestar1864** (V4/Prime), and since 4.3 among others **Baby-scarf** (two
Prime robots on one account), **tankdeer** (600), **rodelero1** (i7), **AdamVig** (j7+),
**AlbertoMPinto** (Combo j9+), **kyoheimukaida** (Combo j9+), **homux-tech** (Combo 505),
**pk-1966**, **nareso** (j7+), **ScenicSystemsLLC**, **molnarpeterhu** and **mattdmrc**.
Thank you all.

**Code** — **[@jouwdan](https://github.com/jouwdan)** found the second map URL in the Prime
map stream and the room outlines in the map bundle, and brought the Roomba Max 705 into the
tested set. **[@mdarocha](https://github.com/mdarocha)** contributed i3+ support, Prime
maintenance parity and presence fixes; **[@tar-gezed](https://github.com/tar-gezed)** the
Prime mission history without a `missionId` and the Prime map colours by cleaning mode;
**[@JoshHobbs](https://github.com/JoshHobbs)** the same room on two maps;
**[@Rickeeh](https://github.com/Rickeeh)** the inner rings of Prime map borders.

**Translations** — **mdarocha** (Polish) and **boutXIII** (French).

**Libraries and prior work** —
[roombapy](https://github.com/pschmitt/roombapy) for the local connection (Roomba+ needs
2.0.2 or newer and contributed the position protocol back to it);
[roombapy-prime](https://github.com/johnnyh1975/roombapy-prime), the companion library for
iRobot's cloud and Prime robots; [dorita980](https://github.com/koalazak/dorita980) by Facu
ZAK (koalazak), which first documented the local protocol, the cloud login and Smart Map
commands.

## License

[MIT](LICENSE)
