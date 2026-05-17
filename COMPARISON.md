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
| iRobot cloud dependency | ✅ None | ✅ None ★ | ❌ Required — Gigya auth unstable since Oct 2024 |
| Setup effort | ✅ Low — auto-discovery | ✅ Low — auto-discovery | ❌ High — manual Docker + credential config |
| Supported models | 690, 890, 960, 980, s9+, Braava m6 | 600–900, i, s, j, Braava m6 ★ | i7+, s9+ focus |
| x05 models (105 / 405 / 505) | ❌ | ❌ | ❌ |
| Unit tests | ✅ | ✅ 133 tests ★ | ❌ |
| Translations | ⚠️ EN only | ✅ DE + EN ★ | ⚠️ EN only |

---

## Sensors

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| **Total sensor count** | 13 | **35 ★** | 27 |
| Battery | ✅ | ✅ | ✅ + dynamic icon + `batInfo` attributes |
| Phase / status | ⚠️ via vacuum activity only | ✅ own sensor + Idle/Stopped detection ★ | ✅ Idle/Stopped detection |
| Error code (80+ codes) | ❌ | ✅ ★ | ✅ |
| Readiness / not-ready | ❌ | ✅ | ✅ |
| Job initiator | ❌ | ✅ | ✅ |
| Next scheduled clean | ❌ | ✅ cleanSchedule + cleanSchedule2 ★ | ❌ |
| Mission statistics | ⚠️ total, ok, failed | ✅ + cancelled, avg time, cleaned area ★ | ⚠️ total jobs only |
| Mission start (active only) | ❌ | ✅ available only during active mission | ✅ |
| Mission elapsed time | ❌ | ✅ | ✅ |
| Mission recharge / expire time | ❌ | ✅ | ✅ |
| Maintenance — filter / brushes | ❌ | ✅ hours remaining + reset buttons ★ | ❌ |
| Navigation quality (`l_squal`) | ❌ | ✅ opt-in, VSLAM robots ★ | ❌ |
| Wi-Fi — RSSI / SNR / Noise | ❌ | ✅ all three, opt-in | ✅ all three, enabled by default ★ |
| IP address | ❌ | ✅ opt-in | ✅ |
| Carpet Boost mode (readable) | ❌ | ✅ Eco / Performance / Auto | ✅ Eco / Performance / Auto |
| Clean mode / passes (readable) | ❌ | ✅ | ✅ |
| Edge cleaning (readable) | ❌ | ✅ | ✅ |
| Clean Base status | ❌ | ✅ | ✅ 6 detailed states |
| Mop sensors — Braava m6 | ❌ | ⚠️ combined mop_ready sensor | ✅ 4 sensors: tank, pad, level, mode ★ |
| Raw state attributes sensor | ❌ | ❌ diagnostics download only | ✅ local + cloud raw attribute sensors ★ |
| Cloud pmap sensor | ❌ | ❌ | ✅ one sensor per saved map ★ |

---

## Controls

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Start / Stop / Pause / Return | ✅ | ✅ | ✅ |
| Cleaning passes — writable HA entity | ❌ | ✅ Select entity, fully local ★ | ⚠️ via REST API only, no HA entity |
| Edge cleaning — writable HA entity | ❌ | ✅ Switch entity, fully local ★ | ⚠️ via REST API only, no HA entity |
| Always finish (`binPause`) | ❌ | ✅ Switch entity ★ | ⚠️ via REST API only, no HA entity |
| Schedule hold (`schedHold`) | ❌ | ✅ Switch entity ★ | ❌ |
| Carpet Boost — writable | ✅ via `fan_speed` on 980 | ✅ Switch (980) + fan_speed | ⚠️ via REST API only, no HA entity |
| Repeat last mission | ❌ | ✅ Button entity ★ | ❌ |
| Locate robot | ✅ | ✅ | ❌ |
| Evacuate Clean Base | ❌ | ✅ ★ | ❌ |
| Maintenance reset | ❌ | ✅ with hass.storage persistence ★ | ❌ |
| Favorites / cloud routines | ❌ | ❌ | ✅ Button per favorite ★ |

---

## Map & Zones

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Live cleaning map | ❌ | ✅ ImageEntity, inline popup | ✅ Camera entity with real room names ★ |
| Map survives HA restart | ❌ | ✅ hass.storage persistence ★ | ❌ |
| Zone / room selection | ❌ | ✅ local via region\_id | ✅ Select per room with real names from cloud ★ |
| Zone selection — fully local | ❌ | ✅ ★ | ❌ pmap sync requires cloud |
| Real room names | ❌ | ⚠️ manually named via Repair Issue | ✅ directly from cloud pmaps ★ |
| Automatic room detection (900) | ❌ | ✅ gap segmentation + EMA confidence ★ | ❌ |
| Door-width calibration | ❌ | ✅ ★ | ❌ |

---

## HA Integration Quality

| Feature | HA Core | Roomba+ | roomba_rest980 |
|---|---|---|---|
| Device triggers | ❌ | ✅ 6 triggers: started, finished, stuck, bin full, docked, error ★ | ❌ |
| Repair Issues | ❌ | ✅ zone naming + Smart Map zone prompts ★ | ❌ |
| Diagnostics download | ⚠️ basic | ✅ includes map + zone state ★ | ❌ |
| Multi-Roomba support | ✅ | ✅ BLID-based, separate stores per entry ★ | ⚠️ |
| Entity grouping | ❌ | ✅ ★ | ❌ |

---

## Notes

**¹ roomba_rest980** requires a permanently running Docker container (rest980 + Node.js).
All cloud-dependent features (map with real room names, zone selection) rely on iRobot's Gigya authentication,
which has been unstable since October 2024. iRobot was acquired by Picea Robotics in January 2026.

**² roomba_rest980 controls** (cleaning passes, edge cleaning, always finish, carpet boost)
are available via the rest980 REST API but have no corresponding HA entity —
they cannot be used in the HA Automation editor without custom scripts.

**³ ★ = best or most native implementation** for that feature.
Multiple integrations can share ★ where equally capable.
