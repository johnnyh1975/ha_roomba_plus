# Roomba+ — Enhanced iRobot Integration for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/Version-3.4.3-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2025.5%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-gold.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=johnnyh1975&repository=ha_roomba_plus&category=integration)

Roomba+ is a Gold-quality Home Assistant custom integration for iRobot Roomba and Braava robots. It connects directly over local Wi-Fi MQTT — no cloud account required, no polling, no subscription — and exposes far more sensors, intelligence, and controls than the built-in HA integration.

**Why Roomba+?**
- **No prerequisites** — local MQTT push, no Docker container, no polling. Cloud credentials are optional and used only for map sync and analytics.
- **Full automation support** — replace `vacuum.start` with `smart_start`: it waits if a blocking sensor fires (a door contact, a baby monitor), skips rooms that aren't actually dirty, and can pause and resume around your presence — all from automations you already have, no new workarounds needed.
- **Comprehensive monitoring** — 100+ entities covering maintenance life, wear rates, 365-entry mission history, performance trends, and error detail with recommended actions.
- **Self-calibrating** — maintenance thresholds, navigation health, battery degradation, and per-room cleaning rhythms all adapt to your robot's own usage history rather than fixed thresholds or manual configuration.
- **Gold quality scale** — 3,937 tests, 8 languages, full config entry migration chain, CI/CD.

> 📊 **[Full feature comparison with HA Core and roomba_rest980 →](docs/COMPARISON.md)**

---

## Contents

- [What you get](#what-you-get)
- [Feature status](#feature-status)
- [Supported hardware & capability matrix](#supported-hardware--capability-matrix)
- [Known limitations](#known-limitations)
- [Installation](#installation)
- [Getting started](#getting-started)
- [Migration](#migration)
- [Documentation](#documentation)
- [Data privacy & data flow](#data-privacy--data-flow)
- [Replacing or selling your robot](#replacing-or-selling-your-robot)
- [Translations](#translations)
- [Contributing](#contributing)
- [Credits](#credits)

---

## What you get

- **Status & control** — phase/error detail beyond the standard HA vacuum states, blocking-sensor gate, room-targeted cleaning
- **Maintenance & health** — self-calibrating filter/brush/battery countdowns, a composite 0–100 health score, wear-rate anomaly detection
- **Mission intelligence** — 365-entry history, per-mission coordinate replay maps, anomaly explanations in plain language
- **Room intelligence** *(SMART + cloud)* — per-room cleaning rhythms, overdue-room catch-up cleaning, dirt ↔ sensor correlation
- **Scheduling & presence** — presence-aware auto-scheduling, demand cleaning, your cleaning schedule as a native HA calendar
- **Automation-ready** — 8+ copy-paste recipes in [Automations & dashboards →](docs/AUTOMATIONS.md), a REST API, device triggers, HA Long-Term Statistics

Full reference, tagged by which robots support what: **[Feature reference →](docs/FEATURES.md)**

---

## Feature status

At a glance — what's shipped, what's next, and what was evaluated and
deliberately not built:

| Feature | Status |
|---|---|
| Live map, room targeting, blocking sensors, maintenance tracking | ✅ Shipped |
| Presence-aware scheduling, demand cleaning, self-calibrating thresholds | ✅ Shipped |
| Mission history, health scoring, anomaly detection | ✅ Shipped |
| Room rhythms, overdue-room cleaning, mission cleaning maps *(SMART + cloud)* | ✅ Shipped |
| Cleaning schedule as HA calendar, maintenance as HA to-do list | ✅ Shipped *(v3.4.0)* |
| Coverage analytics for pose-less lewis-firmware robots | ✅ Shipped *(v3.4.0)* |
| Curated notification blueprints, incl. demand-clean alert, vacuum-then-mop, smart-start-on-away *(v3.4.2/v3.4.3)* | ✅ Shipped — see [Automations](docs/AUTOMATIONS.md) |
| Multi-robot fleet health rollup *(v3.4.3)* | ✅ Shipped — see [API → GET /household](docs/API.md#get-household) |
| Gentle mode switch *(v3.4.3)* | ✅ Shipped |
| Braava mop-pad wear & water-level sensors | ✅ Shipped *(pre-existing — `pad_days_until_due`, `tank_level`; no separate water-consumption field exists, `tank_level` already covers it, see [Release notes →](RELEASE_NOTES_v3.4.3.md))* |
| Furniture-change detection from cloud map deltas | 🔲 Backlog, not yet scheduled |
| Room shape / door-position export | 🔲 Backlog, not yet scheduled |
| Voice commands ("clean the kitchen", etc.) | ❌ Evaluated, not pursued — see [Known limitations](#known-limitations) |

Full version-by-version history: **[GitHub Releases →](https://github.com/johnnyh1975/ha_roomba_plus/releases)**

---

## Supported hardware & capability matrix

| Series | Examples | Tested |
|---|---|---|
| **600** (Bump & run) | Roomba 694, 692 | ⚠️ untested |
| **900** (VSLAM) | Roomba 980, 985 | ✅ **Roomba 980** |
| **i-series** | i3, i7, i7+ | ✅ **i7+** |
| **s-series** | s9+ | ✅ **S9+** |
| **j-series** | j7, j7+ | ✅ **j-series** |
| **Braava** | m6 | ✅ **Braava jet m6** |

**What works on your robot** — the fast answer to the most common setup question:

| Capability | 600 | 900 (EPHEMERAL) | i / s / j-series (SMART) | Braava m6 |
|---|---|---|---|---|
| Live cleaning map & path | ❌ | ✅ | ✅ | ✅ |
| Clean by room name | ❌ | ✅ auto-detected zones | ✅ named rooms | ✅ named rooms |
| Cloud room names, favourites, history | ❌ | ⚠️ history only | ✅ optional | ✅ optional |
| Presence-aware scheduling & demand cleaning | ❌ | ✅ | ✅ | ✅ |
| Room rhythms, overdue-room cleaning, mission maps *(v3.3.0)* | ❌ | ❌ — needs cloud room data | ✅ requires cloud | ✅ requires cloud |
| Dirt ↔ sensor correlation *(v3.3.0)* | ❌ | ❌ | ✅ requires cloud | ✅ requires cloud |
| Maintenance reminders (filter/brush/battery) | ✅ | ✅ | ✅ | ✅ |
| Mop control (pad wetness, tank status) | — | — | — | ✅ |
| Cleaning schedule as HA calendar *(v3.4.0)* | ✅ | ✅ | ✅ | ✅ |
| Maintenance tasks as HA to-do list *(v3.4.0)* | ✅ | ✅ | ✅ | ✅ |
| — incl. "unnamed zones" reminder *(v3.4.0, SMART only)* | ❌ | ❌ | ✅ | ✅ |

*Cloud features require your iRobot app email and password and are entirely optional — all local MQTT functionality works without them.*

**Capability tiers, in plain terms:** 600-series = bump-and-run (no map, no room targeting). 900-series = VSLAM ephemeral map with automatic zone detection and cloud history. i/s/j-series and Braava = persistent Smart Map with named rooms, favourites, and the full room-intelligence feature set.

---

## Known limitations

- **600-series is untested** — should work (same local MQTT protocol), but no field confirmation yet. See the capability matrix above for what it does and doesn't support by design.
- **i-series (lewis firmware) mission cleaning maps confirmed** (July 2026, field-confirmed by Thonno on an i7) — previously confirmed on Braava jet m6 (sapphire firmware) only. See [Upgrade notes →](docs/UPGRADING.md).
- **Stuck-hotspot detection on lewis firmware is structurally wired up but not field-confirmed** — the coverage heatmap and layout-change detection this same release adds for lewis firmware *do* work; whether the cloud data actually populates for a genuine stuck incident on this specific firmware is still an open question. See [Release notes →](RELEASE_NOTES_v3.4.0.md).
- **No voice commands ("clean the kitchen", etc.)** — evaluated for this release and dropped, not delayed: there's currently no supported way for a third-party integration to ship Assist voice sentences that work without you creating a file yourself. See [Release notes →](RELEASE_NOTES_v3.4.0.md).
- **No "time to retrain your Smart Map" reminder** — considered for the new to-do list, dropped: no existing signal was reliable enough at the right granularity (the closest one fires per-furniture-item, not map-wide). See [Release notes →](RELEASE_NOTES_v3.4.0.md).

---

## Installation

**Requirements:** Home Assistant 2025.5 or newer · HACS installed (for recommended install)

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. URL: `https://github.com/johnnyh1975/ha_roomba_plus` · Category: Integration
3. Install **Roomba+** → restart HA

### Manual

Copy `custom_components/roomba_plus/` into your HA `config/` directory, then restart HA.

### First-time setup

1. Settings → Devices & Services → Add integration → **Roomba+**
2. Roomba is discovered automatically via DHCP/Zeroconf — or enter the IP manually
3. Hold the **HOME** button on the robot for ~2 seconds until it plays tones
4. *(Smart Map robots, optional)* Enter your iRobot app email and password to enable cloud features

> **Note:** Roomba+ and the built-in Core Roomba integration cannot run simultaneously — they share the same local MQTT connection. Remove the Core integration first.

### Adding or updating cloud credentials

Settings → Devices & Services → Roomba+ → Configure → **iRobot cloud credentials**

Enter email and password, or clear both fields to disable cloud. A connection test runs before saving.

### Reconfiguration (IP/password change)

Settings → Devices → Roomba+ → ⋮ → **Reconfigure** — no need to remove and re-add.

---

## Getting started

After installation, five steps to get the most out of Roomba+:

1. **Check the map** — open the device page. `image.{name}_cleaning_map` renders automatically for 900-series and Smart Map robots. No configuration required.
2. **Smart Map robots: add cloud credentials** — Settings → Devices → Roomba+ → Configure → iRobot cloud credentials. Room names, favorites, and history appear immediately.
3. **Replace `vacuum.start` with `roomba_plus.smart_start`** in all automations — it respects blocking sensors and optionally targets specific rooms. See [Automations & dashboards →](docs/AUTOMATIONS.md) for copy-paste examples.
4. **Set a blocking sensor** (optional) — Settings → Configure → Blocking sensors. Pick any binary sensor (door contact, occupancy, person home). The robot will queue or abort rather than starting when it fires.
5. **Reset consumables after replacing them** — Settings → device → press the Filter / Brush / Battery reset button, or mark the matching to-do item done. The remaining-life countdown restarts either way.

Two things also appear automatically, no setup needed: your cleaning schedule as `calendar.{name}_schedule`, and filter/brush maintenance as `todo.{name}_maintenance`.

---

## Migration

### From the Core Roomba integration

1. Settings → Devices & Services → iRobot Roomba and Braava → Delete
2. Restart Home Assistant
3. Install and set up Roomba+ via HACS

### From roomba_rest980

1. **Keep roomba_rest980 installed for now** — don't remove it yet
2. Add Roomba+ — it connects directly to the robot without middleware
3. Enter your iRobot credentials in the setup flow to restore cloud zone names and favorites
4. Settings → Devices → Roomba+ → Configure → **Import rooms from roomba_rest980** (only shown when an existing roomba_rest980 installation is detected on a Smart Map robot) — reads room names straight from its `select.*` entities and fills in any of your Roomba+ room labels that aren't set yet. Never overwrites a name you've already assigned through Roomba+'s own naming workflow.
5. Once you're happy with the result, remove roomba_rest980 and stop the rest980 Docker container

### Multiple robots

Each robot is a separate integration entry with its own device, entities, and storage. Repeat the Add Integration flow for each robot. Cloud credentials are stored per robot.

---

## Documentation

| | |
|---|---|
| [Feature reference →](docs/FEATURES.md) | Every entity, service, and configuration option — organized by what you're trying to do, tagged by which robots support it |
| [Automations & dashboards →](docs/AUTOMATIONS.md) | Copy-paste automation recipes and a starter dashboard |
| [REST API →](docs/API.md) | Full endpoint reference with response shapes and examples |
| [Feature comparison →](docs/COMPARISON.md) | Roomba+ vs HA Core vs roomba_rest980 |
| [xiaomi-vacuum-map-card →](docs/xiaomi-vacuum-map-card.md) | Interactive room map card integration guide |
| [Upgrade notes →](docs/UPGRADING.md) | Per-version migration steps and learning-period notes |
| [Troubleshooting →](docs/TROUBLESHOOTING.md) | Common problems grouped by topic |
| [GitHub Releases →](https://github.com/johnnyh1975/ha_roomba_plus/releases) | Changelogs and release notes |

Questions or issues? → [GitHub Issues](https://github.com/johnnyh1975/ha_roomba_plus/issues) · [HA Community Forum](https://community.home-assistant.io)

---

## Data privacy & data flow

Roomba+ is a local-first integration. Here's exactly what talks to what.

**Local MQTT (always on).** Base functionality — live status, cleaning map, room targeting, blocking sensors, maintenance tracking — runs entirely over a local MQTT connection between Home Assistant and the robot (BLID + local password). No internet connection or cloud account is required.

**iRobot cloud (optional).** If you enter your iRobot app email and password (Settings → Devices → Roomba+ → Configure → iRobot cloud credentials), Roomba+ additionally talks to iRobot's own cloud API — the same servers and the same account the official iRobot app itself uses. Nothing is proxied through a third party or a Roomba+-specific server. This unlocks Smart Map room names, favorites, mission history enrichment, and the room-intelligence features marked "requires cloud" in the capability matrix above. It's entirely optional — everything else works without it, and you can add or remove cloud credentials at any time (see [Adding or updating cloud credentials](#adding-or-updating-cloud-credentials)).

**How often cloud data refreshes.** Right after a mission ends, Roomba+ tries to pull the fresh cloud data almost immediately (within ~90 seconds, retrying once 10 minutes later if iRobot's own cloud hasn't caught up yet). Outside of that, cloud data refreshes on its own about once every 24 hours — so a change made only in the iRobot app (a renamed room, a new favorite) can take up to a day to appear here, not instantly.

**What's stored, and where.** Mission history, coverage/stuck-hotspot data, door markers, robot profile, and maintenance timers are all stored in Home Assistant's own `.storage/` directory, on your instance. This data never leaves your Home Assistant instance on its own, and is deleted along with everything else when you remove the integration *(v3.4.0+)* — see [Replacing or selling your robot](#replacing-or-selling-your-robot) if you want to export it first.

**No phone-home.** Roomba+ has no analytics, telemetry, or crash-reporting server of any kind — nothing is sent to the developer or anyone else automatically. The REST API (see [REST API →](docs/API.md)) is served by your own Home Assistant instance and requires your own long-lived access token; it's not a hosted service.

**Diagnostics downloads.** Settings → Devices → Roomba+ → Download diagnostics redacts your BLID, local password, and iRobot credentials automatically before the file is generated — and is only ever created when you click the button, never automatically or on a schedule.

**Filing a bug report?** GitHub issues and the diagnostics download above are the only ways any data leaves your instance for troubleshooting purposes — both are things you explicitly initiate.

---

## Replacing or selling your robot

Roomba+ stores learned data (mission history, coverage baselines, maintenance timers, health trends) inside HA — not on the robot. Before removing a robot, export your history so you can restore it later:

```bash
curl -H "Authorization: Bearer <token>" \
     "https://<ha>/api/roomba_plus/<entry_id>/mission_history?format=export" \
     -o roomba_backup.json
```

Then remove the integration via Settings → Devices & Services → Roomba+ → Delete — this permanently deletes the exported data above from your HA instance too *(v3.4.0+)*, so export first if you want to keep it.

**Factory reset** (if selling) is done through the **iRobot app**, not this integration: app → robot → Settings → Factory Reset.

For a replacement robot, add a new config entry and optionally restore history via the import endpoint. Full steps: [Troubleshooting → Replacing or selling your robot](docs/TROUBLESHOOTING.md)

---

## Translations

| Language | Status |
|---|---|
| English | ✅ Complete |
| German | ✅ Complete |
| French | ✅ Complete |
| Italian | ✅ Complete — native speaker review welcome |
| Spanish | ✅ Complete — native speaker review welcome |
| Portuguese | ✅ Complete (European) — native speaker review welcome |
| Dutch | ✅ Complete — native speaker review welcome |
| Polish | ✅ Complete — community contribution by **mdarocha**, native speaker review welcome |

To contribute or report an incorrect phrase: open an issue or PR with the corrected `translations/<lang>.json`.

---

## Contributing

Roomba+ is maintained by one person — field-tester reports and pull requests are genuinely how this project moves faster:

- **Found a bug, or a robot/firmware combination that doesn't work as documented?** → [GitHub Issues](https://github.com/johnnyh1975/ha_roomba_plus/issues) — a `diagnostics` download (Settings → Devices → Roomba+ → Download diagnostics, auto-redacted) attached to the report saves a lot of back-and-forth.
- **Own hardware nobody's field-tested yet** (600-series, s9+, newer firmware)? A short raw-state capture is often more useful than a bug report — see the [Credits](#credits) section below for the kind of field testing that has directly shaped past releases.
- **Translations** — see just above; a native-speaker pass on any non-English language is always welcome, even a one-line correction.
- **Code contributions** — welcome via PR, though given the single-maintainer reality, please open an issue to discuss scope first for anything beyond a small, self-contained fix. The version plan (linked from [GitHub Releases →](https://github.com/johnnyh1975/ha_roomba_plus/releases)) shows what's already planned, to avoid duplicate effort.

---

## Credits

**Field testing** — real-device reports from these community members have directly driven bug fixes, cancelled features that didn't hold up, and shaped the version plan: **Thonno** (i7+), **veronoicc** (i7+, i8+), **boutXIII** (Braava jet m6), **ronluna** (S9+), **KingAntDesigns** (Braava jet m6, j7+), **mdarocha** (i3+). Thank you all.

**Translation** — **mdarocha** contributed the Polish (`pl`) translation, **boutXIII** contributed the French (`fr`) translation.

**[roombapy](https://github.com/pschmitt/roombapy)** — Python library for local MQTT/TLS communication with Roomba robots.

**[dorita980](https://github.com/koalazak/dorita980)** by Facu Decena — Pioneering work documenting the local MQTT protocol, cloud auth flows, and Smart Map commands.

**[rest980](https://github.com/koalazak/rest980)** by Facu Decena — REST interface and cloud API analysis, including the Gigya → AWS Cognito auth flow.

**[roomba_rest980](https://github.com/ia74/roomba_rest980)** — Reverse-engineered iRobot cloud API client whose auth implementation the cloud layer is based on.

**[Roomba980-Python](https://github.com/NickWaterton/Roomba980-Python)** by Nick Waterton — Comprehensive Python implementation with detailed Roomba protocol documentation.

**[Home Assistant Core Roomba Integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/roomba)** — Architecture foundation for Roomba+.

> Roomba+ is an independent community project with no affiliation to iRobot or Picea Robotics.

---

## License

MIT License — use at your own risk.
