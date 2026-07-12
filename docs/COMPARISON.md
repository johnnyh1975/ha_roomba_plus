[← Roomba+](../README.md)

# Roomba Integrations — Feature Comparison

> Based on source code analysis · last verified July 2026 against
> Roomba+ **v3.3.0** and roomba_rest980 **v1.19.1**
> (manifest version; quality_scale.yaml documents Bronze→Platinum rules, all met).
> Covers all three main integration paths for iRobot robots in Home Assistant.

**Legend:** ✅ Supported &nbsp;·&nbsp; ⚠️ Partial / limited &nbsp;·&nbsp; ❌ Not available &nbsp;·&nbsp; ★ Best in class

---

## Thematic overview

| Theme | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| 🔌 [Setup & prerequisites](#-setup-prerequisites) | Local MQTT push, auto-discovery, no extras | Local MQTT push, built-in | HTTP poll to Docker container, cloud required |
| 🗺️ [Map & rooms](#-map-rooms) | Live path + UMF polygons + zone overlays, automatic room detection (900-series) | None | Static UMF floor plan + zone overlays, no live path |
| 🎮 [Control](#-controls) | Room targeting, blocking sensors, favourites, sequences | Start / stop / return | Per-room staging select + cloud routines |
| 🧠 [Intelligence](#-intelligence-scheduling) | Presence scheduling, demand cleaning, anomaly detection, learned per-room rhythms, mission maps | None | None |
| 📊 [Monitoring](#-sensors-monitoring) | 100+ entities — maintenance, performance, error detail | 13 entities | ~29 base sensors + dynamic room selects and favourite buttons |
| 🏆 [HA quality](#-ha-integration-quality) | Gold, 3,863 tests, 8 languages, CI/CD | Silver, built-in | Bronze, EN only |

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
| Supported models | ✅ 600–900, i, s, j, Braava m6 ★ | ⚠️ 690, 890, 960, 980, s9+, Braava m6 | ⚠️ Smart Map robots (i/s/j-series) only |
| HA Long-Term Statistics backfill | ✅ area, duration, completions — auto-backfilled on startup ★ | ❌ | ❌ |
| Unit tests | ✅ 3,803 tests ★ | ✅ | ❌ |
| Quality Scale | **Gold ★** | Silver | **Bronze** |
| Translations | ✅ DE / EN / ES / FR / IT / NL / PT ★ | ⚠️ EN only | ⚠️ EN only |

---

## 📊 Sensors & Monitoring

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| **Total sensor count** | **100+ ★** | 13 | ~29 base sensors + dynamic room/zone selects and favourite buttons |
| Battery | ✅ | ✅ | ✅ + dynamic icon + `batInfo` attributes |
| Phase / status | ✅ dedicated sensor + idle/stopped detection ★ | ⚠️ via vacuum state only | ✅ idle/stopped detection |
| Error code (80+ codes) | ✅ label + description + recommended action ★ | ❌ | ✅ mapped text label — no raw code, description, or action attributes |
| Readiness / not-ready | ✅ | ❌ | ✅ dedicated sensor with mapped text labels |
| Job initiator | ✅ | ❌ | ✅ |
| Next scheduled clean | ✅ cleanSchedule + cleanSchedule2 ★ | ❌ | ❌ |
| Mission statistics (per-mission) | ✅ MissionStore: 365 entries, full breakdown ★ | ⚠️ total, ok, failed | ❌ lifetime totals only — no per-mission breakdown |
| Lifetime stats (area, time, jobs) | ✅ from cloud ★ | ❌ | ✅ total area + time + jobs from local MQTT (`runtimeStats`, `bbmssn`) |
| Mission elapsed time | ✅ | ❌ | ✅ |
| Mission progress (%) | ✅ v2.6+, SMART + cloud ★ | ❌ | ❌ |
| Mission recharge / expire time | ✅ all firmware families ★ | ❌ | ✅ |
| Mission log (persistent, 365 entries) | ✅ hass.storage, `query_by_day()` ★ | ❌ | ❌ |
| Maintenance — filter / brushes | ✅ hours remaining + wear rate + reset buttons ★ | ❌ | ❌ |
| Maintenance — wheel / contacts / bin | ✅ last-cleaned timestamp + reset service (v2.7+) ★ | ❌ | ❌ |
| Navigation quality (`l_squal`) | ✅ opt-in, VSLAM robots ★ | ❌ | ❌ |
| Wi-Fi — RSSI / SNR / Noise | ✅ all three, opt-in | ❌ | ✅ all three, enabled by default ★ |
| IP address | ✅ opt-in | ❌ | ✅ |
| Carpet Boost mode (readable) | ✅ | ❌ | ✅ |
| Clean mode / passes (readable) | ✅ | ❌ | ✅ |
| Edge cleaning (readable) | ✅ | ❌ | ✅ |
| Clean Base status | ✅ | ❌ | ✅ 12 state codes |
| Mop sensors — Braava m6 | ✅ 5 sensors: clean mode, tank status, ARS behavior, pad type, tank level ★ | ❌ | ✅ 5 sensors ★ |
| Cloud diagnostics | ✅ 4 consolidated sensors: performance, analytics 30d, Wi-Fi health, event counts (v2.7+) ★ | ❌ | ❌ |
| Cloud lifetime stats | ✅ area, time, mission count ★ | ❌ | ❌ |
| Map learning / completeness | ✅ v2.6+, SMART + cloud ★ | ❌ | ❌ |
| Zone summary (clean / keepout / observed) | ✅ v2.6+, SMART + cloud ★ | ❌ | ❌ |
| Raw state / cloud state dump | ❌ diagnostics download only | ❌ | ✅ 2 sensors: local + cloud raw dumps ★ |
| Cloud pmap sensor | ❌ | ❌ | ✅ one sensor per saved map ★ |

---

## 🎮 Controls

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Start / Stop / Pause / Return | ✅ | ✅ | ✅ |
| Spot clean | ✅ ★ | ✅ | ✅ native vacuum feature |
| Cleaning passes per room | ✅ global Select entity, fully local ★ | ❌ | ⚠️ Staging Select entity per room + zone (set pass count, then press Start) ² |
| Edge cleaning — HA entity | ✅ Switch entity, fully local ★ | ❌ | ❌ REST API only ² |
| Always finish (`binPause`) | ✅ Switch entity ★ | ❌ | ❌ REST API only ² |
| Schedule hold (`schedHold`) | ✅ Switch entity ★ | ❌ | ❌ |
| Carpet Boost — writable | ✅ Switch (980) + fan_speed | ✅ via `fan_speed` on 980 | ❌ REST API only ² |
| Repeat last mission | ✅ Button entity ★ | ❌ | ❌ |
| Locate robot | ✅ | ✅ | ❌ |
| Evacuate Clean Base | ✅ ★ | ❌ | ❌ |
| Maintenance reset | ✅ with hass.storage persistence ★ | ❌ | ❌ |
| Favourites / cloud routines | ✅ Button per favourite ★ | ❌ | ✅ Button per favourite ★ |

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

---

## 🧠 Intelligence & Scheduling

| Feature | Roomba+ | HA Core | roomba_rest980 |
|---|---|---|---|
| Presence-aware scheduling | ✅ `PresenceManager` + `schedHold` ★ | ❌ | ❌ |
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

**⁵ iRobot / Picea Robotics cloud** — iRobot was acquired by Picea Robotics in January 2026. Both Roomba+ and roomba_rest980 use the same Gigya→AWS Cognito authentication flow against iRobot's API endpoints.

**⁶ roomba_rest980 repo also bundles `runjailed`**, a separate, unrelated side-project (not part of the HA custom component, not invoked by it) documenting a root-access exploit for `lewis`-firmware (i/j-series) robots via an MQTT input-sanitization vulnerability. It is out of scope for this comparison — included here only for completeness, since it ships in the same repository. No part of Roomba+ relies on, recommends, or interacts with this.

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [Automations](AUTOMATIONS.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
