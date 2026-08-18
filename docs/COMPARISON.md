[← Roomba+](../README.md)

# Roomba Integrations — Feature Comparison

> **Fully re-verified August 2026**, all three columns read from source.
>
> | Column | Version | How it was checked |
> |---|---|---|
> | **Roomba+** | v4.0.0a38 | this repository |
> | **HA Core** `roomba` | ships with Home Assistant, `roombapy==1.8.1` | the installed component |
> | **roomba_rest980** | v1.20.0-beta4 (`ia74/roomba_rest980`) | cloned from GitHub |
>
> Counts below are entity and feature counts read out of each codebase, not
> estimates. Where a row says ❌ it means the code has no such thing, not that it
> was not found.

**Legend:** ✅ Supported &nbsp;·&nbsp; ⚠️ Partial / limited &nbsp;·&nbsp; ❌ Not available &nbsp;·&nbsp; ★ Best in class

---

## Prime-generation robots

**No other integration supports them.** Roomba Max, Combo/Plus 400-series and
other robots on iRobot's newer cloud protocol do not speak the local MQTT
protocol that the built-in HA integration and rest980 are built on — there is
nothing for those paths to connect to.

Roomba+ v4 talks to iRobot's cloud instead. That is a different trade rather
than a free win: it needs your iRobot credentials, it needs internet, and the
v4 line is still alpha. But it is the only option, and the comparison below
does not apply to those robots at all.

---

## Thematic overview

| Theme | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| 🔌 [Setup & prerequisites](#setup--prerequisites) | Local MQTT push, auto-discovery, no extras | Local MQTT push, built-in | HTTP poll to Docker container, cloud required |
| 🗺️ [Map & rooms](#map--rooms) | Live path + UMF polygons + zone overlays, automatic room detection (900-series) | None | Static UMF floor plan + zone overlays, no live path |
| 🎮 [Control](#controls) | Room targeting, blocking sensors, favourites, sequences | Start / stop / return | Per-room staging select + cloud routines |
| 🧠 [Intelligence](#intelligence--scheduling) | Presence scheduling, demand cleaning, anomaly detection, learned per-room rhythms, mission maps | None | None |
| 📊 [Monitoring](#sensors--monitoring) | 100+ entities — maintenance, performance, error detail | 13 entities | ~29 base sensors + dynamic room selects and favourite buttons |
| 🏆 [HA quality](#ha-integration-quality) | Gold, 3,863 tests, 8 languages, CI/CD | Silver, built-in | Bronze, EN only |

---

## When to choose

**Choose Roomba+ if:**
- You want to see a live map of where your robot is cleaning
- You want to clean specific rooms — by name, from automations or the UI
- You want automations that actually work: start only when everyone's away, pause when a door opens, clean again when it's dirtier than usual
- You want maintenance reminders for filter, brush, and battery
- You want the integration to keep working regardless of cloud availability or API changes
- You have an older 900-series robot and want automatic room detection without cloud
- You want the robot to catch up on overdue or extra-dirty rooms with one command, routed efficiently

**Choose HA Core if:**
- You want the simplest possible setup — installed in two minutes, no extras
- Start, stop, and check battery is all you need
- You're already using it and it meets your needs — there's no reason to switch

**Choose roomba_rest980 if:**
- You want a persistent view of your floor plan with room boundaries, no-go zones, and obstacles
- You already have it running and it works for you
- ⚠️ Requires a Docker container running 24/7, cloud credentials, and a Smart Map capable robot (i/s/j-series only)

---

## 🔌 Setup & Prerequisites

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Connection type | ✅ Local MQTT/TLS ★ | ✅ Local MQTT/TLS | ⚠️ HTTP polling to rest980 container |
| Push vs. poll | ✅ Push ★ | ✅ Push | ⚠️ Poll every N seconds |
| External prerequisites | ✅ None ★ | ✅ None | ❌ Docker container must run 24/7 |
| Cloud-free operation | ✅ Fully local ★ | ✅ Fully local | ❌ Cloud required for map and zone features |
| iRobot cloud dependency | ⚠️ Optional — same Gigya→AWS Cognito flow | ✅ None | ⚠️ Required — same Gigya→AWS Cognito flow |
| Setup effort | ✅ Low — auto-discovery ★ | ✅ Low — auto-discovery | ❌ High — manual Docker + credential config, no auto-discovery |
| Supported models | ✅ 600–900, i, s, j, Braava m6, **and Prime-generation** ★ | ⚠️ 690, 890, 960, 980, s9+, Braava m6 | ⚠️ Smart Map robots (i/s/j-series) only |
| HA Long-Term Statistics backfill | ✅ area, duration, completions — auto-backfilled on startup ★ | ❌ | ❌ |
| Unit tests | ✅ **5,499 tests** ★ | ✅ in the HA core suite | ❌ none in the repository |
| Quality Scale | **Gold ★** | not declared in its manifest | **Bronze** (rules file present, several `todo`) |
| Translations | ✅ 8 languages, complete and enforced by a check | ✅ **38 languages ★** — it ships with Home Assistant | ⚠️ 1 |

---

## 📊 Sensors & Monitoring

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| **Entity count** | **236 ★** — 154 sensors, 22 binary sensors, 20 buttons, 17 selects, 15 switches, 5 images, calendar, to-do, device tracker | **11** — 10 sensors, 1 binary sensor | ~51 sensor descriptions, 4 selects, 2 buttons, 1 camera |
| Battery | ✅ | ✅ | ✅ + dynamic icon + `batInfo` attributes |
| Battery cycles | ✅ | ✅ | ✅ |
| Phase / status | ✅ dedicated sensor + idle/stopped detection ★ | ⚠️ via vacuum state only | ✅ idle/stopped detection |
| Error codes | ✅ **112 codes in iRobot's own wording**, eight languages, with description and recommended action ★ | ❌ | ✅ mapped text label — no raw code, description or action |
| Readiness / not-ready | ✅ | ❌ | ✅ dedicated sensor with mapped labels |
| Job initiator | ✅ 25 values ★ | ❌ | ✅ |
| Next scheduled clean | ✅ ★ | ❌ | ❌ |
| Per-mission history | ✅ 365 entries with full breakdown ★ | ⚠️ totals only: missions, successful, cancelled, failed | ❌ lifetime totals only |
| Lifetime stats (area, time, jobs) | ✅ | ✅ total cleaning time, area, average mission time | ✅ from local MQTT |
| Last mission | ✅ result, duration, rooms ★ | ✅ timestamp only | ⚠️ partial |
| Mission elapsed time | ✅ | ❌ | ✅ |
| Mission progress (%) | ✅ SMART + cloud, and Prime ★ | ❌ | ❌ |
| Mission recharge / expire time | ✅ all firmware families ★ | ❌ | ✅ |
| Maintenance — filter / brushes | ✅ hours remaining + wear rate + reset buttons ★ | ❌ | ❌ |
| Maintenance — wheel / contacts / bin | ✅ last-cleaned timestamp + reset ★ | ❌ | ❌ |
| Scrub count | ✅ | ✅ | ⚠️ |
| Navigation quality (`l_squal`) | ✅ opt-in, VSLAM robots ★ | ❌ | ❌ |
| Wi-Fi — RSSI / SNR / noise | ✅ all three, opt-in | ❌ | ✅ all three, on by default ★ |
| Bin full / present | ✅ | ✅ binary sensor | ✅ |
| Clean Base status | ✅ | ❌ | ✅ 12 state codes |
| Mop sensors — Braava m6 | ✅ 5 sensors ★ | ❌ | ✅ 5 sensors ★ |
| Cloud diagnostics | ✅ 4 consolidated sensors ★ | ❌ | ❌ |
| Map learning / completeness | ✅ SMART + cloud ★ | ❌ | ❌ |
| Zone summary (clean / keep-out / observed) | ✅ SMART + cloud ★ | ❌ | ❌ |
| Raw state dump as a sensor | ❌ diagnostics download instead | ❌ | ✅ local + cloud raw dumps ★ |
| Cloud pmap sensor | ❌ | ❌ | ✅ one per saved map ★ |

> **HA Core's ten sensors** are battery, battery cycles, total cleaning time,
> average mission time, total missions, successful missions, cancelled missions,
> failed missions, scrub count, total cleaned area and last mission — plus one
> binary sensor. That is the whole set, read from its `strings.json`.

---

## 🎮 Controls

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Start / stop / pause / return | ✅ | ✅ | ✅ |
| Locate | ✅ | ✅ | ❌ |
| Fan speed | ✅ | ✅ | ⚠️ REST only |
| Send raw command | ✅ | ✅ | ✅ `action` service |
| Clean a specific room | ✅ by name or HA area ★ | ❌ | ✅ `clean` service + selects |
| Cleaning passes per room | ✅ Select, fully local ★ | ❌ | ⚠️ staging Select — stages the value, you press Start |
| Edge cleaning | ✅ Switch ★ | ❌ | ❌ REST only |
| Always finish (`binPause`) | ✅ Switch ★ | ❌ | ❌ REST only |
| Schedule hold | ✅ Switch ★ | ❌ | ❌ |
| Carpet boost — writable | ✅ Switch + fan_speed | ✅ via `fan_speed` on 980 | ❌ REST only |
| Repeat last mission | ✅ Button ★ | ❌ | ❌ |
| Evacuate Clean Base | ✅ ★ | ❌ | ❌ |
| Maintenance reset | ✅ with persistence ★ | ❌ | ❌ |
| Favourites / cloud routines | ✅ Button per favourite, **filtered to the robot it belongs to** ★ | ❌ | ✅ Button per favourite |
| Schedule create / edit / delete | ✅ plus an editable HA calendar ★ | ❌ | ❌ |
| **AutoWash dock controls** | ✅ six: wash frequency, area and time intervals, dry duration, wash and dry permissions ★ | ❌ | ❌ |
| **Do Not Disturb** | ✅ write the window, switch it on now, read whether one covers this moment ★ | ❌ | ❌ |
| **Start-blocked reasons** | ✅ names the fault *and which half of the robot still works* ★ | ❌ | ❌ |
| **Pad wetness** | ✅ ★ | ❌ | ❌ |

> **HA Core's vacuum entity** advertises battery, fan speed, locate, pause,
> return home, send command, start, state and stop — read from its
> `VacuumEntityFeature` flags. It has no services of its own.

---

## 🗺️ Map & Rooms

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Floor plan map | ✅ local MQTT `pose` stream ¹ | ❌ | ✅ UMF from iRobot cloud (static) ³ |
| Live cleaning path during mission | ✅ local MQTT `pose` stream ★ | ❌ | ❌ |
| Map survives HA restart | ✅ hass.storage persistence ★ | ❌ | ❌ |
| Room outline — Smart Map robots | ✅ UMF polygon overlay, per-room colour palette, embedded font, cached per map version (v2.9.0) | ❌ | ✅ rendered on UMF floor plan ³ |
| Room outline — 900-series | ✅ progressive edge detection (v2.4+) ★ | ❌ | ❌ |
| Zone / room selection | ✅ local via `region_id` | ❌ | ✅ select per room with real names ★ |
| Zone selection — fully local | ✅ ★ | ❌ | ❌ cloud required |
| Real room names from cloud | ⚠️ cloud credentials required | ❌ | ✅ directly from cloud pmaps ★ |
| Room names without cloud (900) | ✅ automatic zone detection ★ | ❌ | ❌ |
| Keep-out zone visibility | ✅ (v2.2+) | ❌ | ✅ rendered on map ★ |
| Observed obstacle zone overlay | ✅ orange circles from UMF observed_zones (v3.0.0) | ❌ | ❌ |
| Observed zone visibility | ✅ (v2.2+) | ❌ | ✅ rendered on map ★ |
| HA area mapping (`vacuum.clean_area`) | ✅ v2.4+, HA 2026.3+, SMART + cloud | ❌ | ❌ |
| Automatic room detection (900-series) | ✅ gap segmentation + EMA confidence ★ | ❌ | ❌ |
| Door-width calibration | ✅ ★ | ❌ | ❌ |
| xiaomi-vacuum-map-card support | ✅ `calibration_points` + `rooms` on both map entities, auto-detected by card (v2.7+) ★ | ❌ | ✅ calibration + rooms on floor plan ★ |
| **Prime-generation map** | ✅ floor plan with walls, doorway cut-outs and furniture; live coverage and trajectories composited over it ★ | ❌ no support for these robots | ❌ no support for these robots |
| **Stable room ids** | ✅ renaming a room in the iRobot app does not break a saved card configuration ★ | ❌ | ❌ names only |
| **Several maps, zones follow the selected one** | ✅ ★ | ❌ | ⚠️ one sensor per saved map, no selection |

---

## 🧠 Intelligence & Scheduling

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Presence-aware scheduling | ✅ `PresenceManager`, both generations ★ | ❌ | ❌ |
| Blocking sensors (prevent start) | ✅ configurable queue / abort ★ | ❌ | ❌ |
| Demand cleaning (dirt threshold) | ✅ v2.4+, SMART + cloud ★ | ❌ | ❌ |
| Weekday-aware dirt baseline | ✅ v2.5+ ★ | ❌ | ❌ |
| Optimal clean window sensor | ✅ v2.4+ ★ | ❌ | ❌ |
| Mission anomaly detection | ✅ v2.5+ ★ | ❌ | ❌ |
| Stuck pattern time-correlation | ✅ v2.7+ — Repair Issue when same spot/time recurs ★ | ❌ | ❌ |
| Robot health score (0–100) | ✅ v2.7+ — composite battery/nav/trend/anomaly/stuck ★ | ❌ | ❌ |
| Self-calibrating maintenance thresholds | ✅ v2.5+ ★ | ❌ | ❌ |
| Performance sensors (speed, dirt density, coverage) | ✅ cloud, opt-in ★ | ❌ | ❌ |
| Wear rate anomaly detection | ✅ ★ | ❌ | ❌ |
| Mission log REST API | ✅ ★ | ❌ | ❌ |
| Per-mission records (cloud + local) | ✅ unified schema ★ | ❌ | ❌ |
| Mission history export / import | ✅ v2.5+ ★ | ❌ | ❌ |
| Self-calibrated per-room cleaning rhythm | ✅ v3.3.0, SMART + cloud ★ | ❌ | ❌ |
| One-call overdue/dirty-room cleaning, route-optimized | ✅ v3.3.0, SMART + cloud ★ | ❌ | ❌ |
| Mission cleaning maps (per-mission coordinate replay) | ✅ v3.3.0, SMART + cloud ★ | ❌ | ❌ |
| Dirt ↔ external-sensor correlation (opt-in, local) | ✅ v3.3.0, SMART + cloud ★ | ❌ | ❌ |
| Cleaning schedule as native HA calendar | ✅ v3.4.0 ★ | ❌ | ❌ |
| Maintenance tasks as native HA to-do list | ✅ v3.4.0 ★ | ❌ | ❌ |
| Coverage analytics on pose-less lewis-firmware robots | ✅ v3.4.0, cloud-sourced ★ | ❌ — no coverage analytics at all | ❌ |

---

## 🏆 HA Integration Quality

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Quality Scale | **Gold ★** | Silver | **Bronze** — self-declared; `quality_scale.yaml` marks `config-flow-test-coverage`, `test-before-configure`, `test-before-setup`, `has-entity-name`, `unique-config-entry`, `docs-installation-instructions`, `docs-removal-instructions` as `todo`, even within Bronze tier |
| `async_migrate_entry` | ✅ v1→v22 ★ | ✅ | ❌ |
| `reconfiguration-flow` | ✅ ★ | ✅ | ❌ |
| `icon-translations` | ✅ 98 icons ★ | ✅ | ❌ |
| `stale-devices` | ✅ ★ | ✅ | ❌ |
| `strict-typing` | ✅ ★ | ✅ | ❌ |
| Device triggers | ✅ 6 triggers ★ | ❌ | ❌ |
| Repair Issues | ✅ 10 issue types ★ | ❌ | ❌ |
| Diagnostics download | ✅ map + zone + cloud + robot profile ★ | ⚠️ basic | ❌ |
| Multi-robot support | ✅ BLID-based, separate stores per entry ★ | ✅ | ⚠️ one container per robot |
| Integration tests | ✅ 3,803 pytest tests ★ | ✅ | ❌ |
| GitHub Actions CI | ✅ ★ | ❌ | ✅ push + PR + nightly hassfest + HACS validation |

---

## Notes

**¹ Roomba+ map approach** renders entirely in-process using the local MQTT `pose` stream with no external container. From v2.7.0, robots on lewis firmware (i7+/i8+ on 22.x) that do not broadcast local pose data are now bootstrapped automatically from cloud traversal events.  It stopped working on firmware 3.20+ for robots where iRobot removed local `pose` reporting entirely. For robots on older firmware the live cleaning path renders accurately and persists across HA restarts via `hass.storage`. Smart Map robots gain UMF room polygon overlays from v2.3+.

**² roomba_rest980 controls:** Cleaning passes are exposed as a staging `CleanRoomPasses` Select entity — one per room and one per zone. Selecting "One Pass" or "Two Passes" stages the value locally but does NOT send a command to the robot; cleaning only begins when the user presses Start. Edge cleaning, always finish, and carpet boost have no HA entity at all — REST API only.

**³ roomba_rest980 map approach** fetches the iRobot cloud UMF floor plan and renders it as a static `CameraEntity` using Python/Pillow. The map shows the stored floor plan — not the live cleaning path. Keep-out zones and robot-learned obstacle zones are overlaid on the floor plan. Cloud credentials and a trained Smart Map are required. Supports `calibration` and `rooms` attributes for xiaomi-vacuum-map-card.

**⁴ ha-rest980** (jeremywillans/ha-rest980) is a separate project from roomba_rest980. It used the rest980 Node.js container as middleware to provide a live cleaning path, but has been broken since firmware 3.20+ removed local `pose` reporting.

**⁷ Prime-generation rows** are marked ❌ for HA Core and roomba_rest980 because
neither supports those robots at all — they speak only the local MQTT protocol,
and a Prime robot has none. Verified against `ia74/roomba_rest980` v1.20.0-beta4:
no reference to the newer cloud protocol anywhere in the component.

**⁵ iRobot / Picea Robotics cloud** — iRobot was acquired by Picea Robotics in January 2026. Both Roomba+ and roomba_rest980 use the same Gigya→AWS Cognito authentication flow against iRobot's API endpoints.

**⁶ roomba_rest980 repo also bundles `runjailed`**, a separate, unrelated side-project (not part of the HA custom component, not invoked by it) documenting a root-access exploit for `lewis`-firmware (i/j-series) robots via an MQTT input-sanitization vulnerability. It is out of scope for this comparison — included here only for completeness, since it ships in the same repository. No part of Roomba+ relies on, recommends, or interacts with this.

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [Automations](AUTOMATIONS.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
