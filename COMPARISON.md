# Roomba Integrations — Feature Comparison

> Based on source code analysis · May 2026  
> Covers all three main integration paths for iRobot robots in Home Assistant.

**Legend:** ✅ Supported &nbsp;·&nbsp; ⚠️ Partial / limited &nbsp;·&nbsp; ❌ Not available &nbsp;·&nbsp; ★ Best in class

---

## Basics

| Feature | HA Core *(built-in)* | Roomba+ *(HACS)* | roomba_rest980 *(HACS)* |
|---|---|---|---|
| Connection type | ✅ Local MQTT/TLS | ✅ Local MQTT/TLS ★ | ⚠️ HTTP polling to rest980 container |
| Push vs. poll | ✅ Push (MQTT events) | ✅ Push ★ | ⚠️ Poll every N seconds |
| External prerequisites | ✅ None | ✅ None ★ | ❌ Docker + Node.js container must run 24/7 |
| Cloud-free operation | ✅ Fully local | ✅ Fully local ★ | ⚠️ Map + zone selection requires iRobot cloud |
| iRobot cloud dependency | ✅ None | ⚠️ Optional (cloud features only) ★ | ❌ Required for maps/zones — Gigya auth unstable since Oct 2024 |
| Setup effort | ✅ Low — auto-discovery | ✅ Low — auto-discovery | ❌ High — manual Docker + credential config |
| Supported models | 690, 890, 960, 980, s9+, Braava m6 | 600–900, i, s, j, Braava m6 ★ | i7+, s9+ focus |
| HA Long-Term Statistics backfill | ❌ | ✅ area, duration, completions — 3 LTS series, auto-backfilled from MissionStore on every startup ★ | ❌ |
| Unit tests | ✅ | ✅ 1196 tests ★ (1042 v2.0 baseline + 154 new v2.1) | ❌ |
| Quality Scale | Silver | **Gold ★** | Not rated |
| Translations | ⚠️ EN only | ✅ DE / EN / ES / FR / IT / NL / PT ★ | ⚠️ EN only |

---

## Sensors

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| **Total sensor count** | 13 | **97+ ★** (69 local + 28 cloud) | ~27 |
| Battery | ✅ | ✅ | ✅ + dynamic icon + `batInfo` attributes |
| Phase / status | ⚠️ via vacuum state only | ✅ dedicated sensor + idle/stopped detection ★ | ✅ idle/stopped detection |
| Error code (80+ codes) | ❌ | ✅ with label + description + recommended action ★ | ✅ code only |
| Readiness / not-ready | ❌ | ✅ | ✅ |
| Job initiator | ❌ | ✅ | ✅ |
| Next scheduled clean | ❌ | ✅ cleanSchedule + cleanSchedule2 ★ | ❌ |
| Mission statistics | ⚠️ total, ok, failed | ✅ + cancelled, avg time, cleaned area ★ | ⚠️ total jobs only |
| Mission elapsed time | ❌ | ✅ | ✅ |
| Mission recharge / expire time | ❌ | ✅ all firmware families ★ | ✅ |
| Mission log (persistent, 365 entries) | ❌ | ✅ hass.storage, `query_by_day()` ★ | ❌ |
| Maintenance — filter / brushes | ❌ | ✅ hours remaining + wear rate + reset buttons ★ | ❌ |
| Navigation quality (`l_squal`) | ❌ | ✅ opt-in, VSLAM robots ★ | ❌ |
| Wi-Fi — RSSI / SNR / Noise | ❌ | ✅ all three, opt-in | ✅ all three, enabled by default ★ |
| IP address | ❌ | ✅ opt-in | ✅ |
| Carpet Boost mode (readable) | ❌ | ✅ | ✅ |
| Clean mode / passes (readable) | ❌ | ✅ | ✅ |
| Edge cleaning (readable) | ❌ | ✅ | ✅ |
| Clean Base status | ❌ | ✅ | ✅ 6 detailed states |
| Mop sensors — Braava m6 | ❌ | ✅ 5 sensors: clean mode, tank status, ARS behavior, pad type, tank level ★ | ✅ 4 sensors: tank, pad, level, mode ★ |
| Raw state attribute sensor | ❌ | ❌ diagnostics download only | ✅ ★ |
| Cloud pmap sensor | ❌ | ❌ | ✅ one sensor per saved map ★ |
| **Cloud diagnostics (v2.0)** | ❌ | ✅ 6 sensors: completion rate, recharges, evacuations, dirt events, error code + time ★ | ❌ |
| Cloud lifetime stats | ❌ | ✅ area, time, mission count ★ | ❌ |

---

## Controls

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Start / Stop / Pause / Return | ✅ | ✅ | ✅ |
| Cleaning passes — writable HA entity | ❌ | ✅ Select entity, fully local ★ | ⚠️ REST API only, no HA entity |
| Edge cleaning — writable HA entity | ❌ | ✅ Switch entity, fully local ★ | ⚠️ REST API only, no HA entity |
| Always finish (`binPause`) | ❌ | ✅ Switch entity ★ | ⚠️ REST API only, no HA entity |
| Schedule hold (`schedHold`) | ❌ | ✅ Switch entity ★ | ❌ |
| Carpet Boost — writable | ✅ via `fan_speed` on 980 | ✅ Switch (980) + fan_speed | ⚠️ REST API only, no HA entity |
| Repeat last mission | ❌ | ✅ Button entity ★ | ❌ |
| Locate robot | ✅ | ✅ | ❌ |
| Evacuate Clean Base | ❌ | ✅ ★ | ❌ |
| Maintenance reset | ❌ | ✅ with hass.storage persistence ★ | ❌ |
| Favorites / cloud routines | ❌ | ❌ | ✅ Button per favorite ★ |

---

## Map & Zones

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Live cleaning map — data source | ❌ | ✅ local MQTT `pose` stream | ✅ rest980 HTTP + jailbreak for realtime ★ |
| Live cleaning map — rendering | ❌ | ✅ Python/Pillow in-process, `ImageEntity` | ✅ Camera entity fetching from rest980 container |
| Map survives HA restart | ❌ | ✅ hass.storage persistence ★ | ❌ |
| Realtime robot position sensor | ❌ | ⚠️ requires `pose` in MQTT (firmware < 3.20) | ✅ requires jailbreak or firmware < 3.20 ★ |
| Map data note | — | ⚠️ `pose` removed in firmware 3.20+ | ⚠️ `pose` removed in firmware 3.20+; jailbreak WIP |
| Zone / room selection | ❌ | ✅ local via `region_id` | ✅ select per room with real names from cloud ★ |
| Zone selection — fully local | ❌ | ✅ ★ | ❌ pmap sync requires cloud |
| Real room names | ❌ | ⚠️ manually named via Repair Issue | ✅ directly from cloud pmaps ★ |
| Keep-out zone visibility | ❌ | ❌ *(v2.2 planned)* | ✅ rendered on UMF map |
| Observed zone visibility | ❌ | ❌ *(v2.2 planned)* | ✅ robot-learned obstacles rendered |
| Per-room cleaning pass count | ❌ | ⚠️ single zone select only | ✅ select entity per room ★ |
| Automatic room detection (900-series) | ❌ | ✅ gap segmentation + EMA confidence ★ | ❌ |
| Door-width calibration | ❌ | ✅ ★ | ❌ |
| Lovelace map card | ❌ | ✅ companion card v1 (HACS, beta) | ⚠️ compatible with xiaomi-vacuum-map-card |

---

## Intelligence & Scheduling

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Presence-aware scheduling | ❌ | ✅ `PresenceManager` + `schedHold` ★ | ❌ |
| Blocking sensors (prevent start) | ❌ | ✅ configurable queue/abort ★ | ❌ |
| Wear rate anomaly detection (L4) | ❌ | ✅ per-part threshold + days-until-due ★ | ❌ |
| Mission log REST API | ❌ | ✅ `/api/roomba_plus/{id}/mission_history` ★ | ❌ |
| Per-mission records (format=records) | ❌ | ✅ cloud + local fallback, unified schema ★ | ❌ |
| 900-series timestamp backfill | ❌ | ✅ auto-corrects from cloud on startup ★ | ❌ |

---

## HA Integration Quality

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Quality Scale | Silver | **Gold ★** | Not rated |
| `async_migrate_entry` | ✅ | ✅ v1→v2 ★ | ❌ |
| `reconfiguration-flow` | ✅ | ✅ ★ | ❌ |
| `icon-translations` | ✅ | ✅ 98 icons ★ | ❌ |
| `stale-devices` | ✅ | ✅ ★ | ❌ |
| `strict-typing` | ✅ | ✅ ★ | ❌ |
| Device triggers | ❌ | ✅ 6 triggers: started, finished, stuck, bin full, docked, error ★ | ❌ |
| Repair Issues | ❌ | ✅ 8 issue types: zone naming, performance, battery, scheduling, accident, skips, drift, firmware ★ | ❌ |
| Diagnostics download | ⚠️ basic | ✅ includes map + zone + cloud state ★ | ❌ |
| Multi-robot support | ✅ | ✅ BLID-based, separate stores per entry ★ | ⚠️ one container per robot |
| Integration tests | ✅ | ✅ pytest-homeassistant-custom-component ★ | ❌ |
| GitHub Actions CI | ❌ | ✅ push + PR + nightly hassfest + HACS validation ★ | ❌ |

---

## Notes

**¹ roomba_rest980 map approach** uses the rest980 Node.js container as a middle layer: coordinates arrive via MQTT locally, rest980 logs them, and the integration fetches the rendered map image over HTTP. This works well when `pose` data is available but requires the container to run 24/7. Since iRobot firmware 3.20+, `pose` is no longer reported locally — the map feature is broken on all updated robots. A jailbreak approach (SSH + internal MQTT broker access) is in active development by the author but not yet publicly released. Notably, roomba_rest980 correctly renders user-configured keep-out zones and robot-learned obstacle zones on the map — a genuine advantage for users who configure no-go zones in the iRobot app. Roomba+ plans to surface these in v2.2.

**² Roomba+ map approach** renders entirely in-process using the same local MQTT `pose` stream, with no external container. It also stopped working on firmware 3.20+ for the same reason. For robots on older firmware both approaches are comparable in accuracy; Roomba+ has the advantage of surviving HA restarts via `hass.storage`.

**³ roomba_rest980 controls** (cleaning passes, edge cleaning, always finish, carpet boost) are available via the rest980 REST API but have no corresponding HA entity — they cannot be used in the HA Automation editor without custom scripts.

**⁴ iRobot / Picea Robotics cloud** — iRobot was acquired by Picea Robotics in January 2026. The Gigya authentication stack used by roomba_rest980's cloud features has been unstable since October 2024. Roomba+'s cloud features use the iRobot AWS endpoint directly, which has been stable throughout.

**⁵ ★ = best or most native implementation** for that feature across the three integrations. Multiple entries can share ★ where equally capable.
