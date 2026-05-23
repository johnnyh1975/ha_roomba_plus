# Roomba+ — Enhanced iRobot Integration for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/Version-1.5.0-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2024.11%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Silver-silver.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)

Home Assistant Custom Integration for iRobot Roomba and Braava. Fully local, no cloud required, no subscription — significantly more sensors, map, and zone features than the built-in HA integration. **v1.5 adds optional iRobot cloud integration** for Smart Map robots, bringing authoritative room names, favorites, and stable pmap resolution without any manual naming flow.

---

> 📊 **[Full feature comparison with HA Core Roomba and roomba_rest980 →](COMPARISON.md)**

## Supported devices

| Series | Examples | Map | Zones | Cloud features | Schedule hold | Bin present | Tested |
|---|---|---|---|---|---|---|---|
| **600** (Bump & Run) | Roomba 694, 692 | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ untested |
| **900** (VSLAM) | Roomba 980, 985 | ✅ ephemeral | ✅ automatic | ❌ | ❌ | ❌ | ✅ **Roomba 980** |
| **i-series** | i3, i7, i7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ✅ **i7+** |
| **s-series** | s9+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ⚠️ untested |
| **j-series** | j7, j7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ⚠️ untested |
| **Braava** | m6 | ✅ | ✅ Smart Map | ✅ optional | ✅ | ❌ (mop ready ✅) | ⚠️ untested |

> **Tested hardware:** Roomba 980 and Roomba i7+. Support for other series is implemented based on protocol documentation, capability flags and the roombapy library.

> Cloud features (v1.5+) require your iRobot app email and password. They are entirely optional — all local MQTT functionality works identically without them.

---

## Features

### Sensors (35)

- **Status** — phase (with Idle/Stopped detection), error (80+ codes), readiness, job initiator, next scheduled clean
- **Settings** — cleaning passes, carpet boost mode (900/i/s/j)
- **Maintenance** — filter remaining, brushes remaining, charge cycles
- **Missions** — total, successful, cancelled, failed, total time, avg. duration, cleaned area, last mission, mission start, mission elapsed, recharge time, expire time
- **Connectivity** — connected, bin full, battery, RSSI, SNR, signal noise, IP address
- **Clean Base** — dock status, dock tank level
- **Braava** — tank level, mop pad type, mop behaviour, mop tank level
- **Navigation** — navigation quality / l_squal (VSLAM robots, opt-in)

### Controls

- **Cleaning passes** — Auto / One pass / Two passes (select)
- **Edge cleaning** — On/Off (switch)
- **Always finish** — keep cleaning even when the bin is full (switch, i7+/s9+/j7+ with Clean Base only)
- **Schedule hold** — freeze the cleaning schedule without deleting it (switch, i/s/j/Braava)
- **Maintenance reset** — confirm filter, brush, and battery replacement (buttons)
- **Locate robot** — play find-me tone (button)
- **Evacuate bin** — Clean Base models (button)

### Cloud features — Smart Map robots with iRobot credentials (v1.5, optional)

When you enter your iRobot app email and password during setup (or later via **Configure → iRobot cloud credentials**), Roomba+ connects to the iRobot cloud to fetch authoritative Smart Map data:

- **Zone select from cloud** — room and zone names come directly from your Smart Map; no manual repair-flow naming required. One select entity per floor.
- **Favorites as buttons** — each saved cleaning routine from the iRobot app appears as a button entity that fires that routine from HA.
- **Stable pmap_id** — `clean_room` and the zone buttons use the authoritative pmap_id from the cloud, eliminating stale-MQTT failures after a full-home clean overwrites `lastCommand`.
- **Auto-refresh on map retrain** — Roomba+ detects map version changes in the MQTT stream and immediately refreshes cloud data, so new room names appear in HA without waiting for the 24-hour background poll.
- **Repair flow suppressed** — when cloud is active the `smart_zones_need_naming` repair issue is not raised; names are always current from the cloud.

> Cloud credentials are stored in the HA config entry (encrypted at rest by HA). All robot control commands continue to go through local MQTT — only map metadata is fetched from the cloud.

### Cleaning map (Roomba 900 / i / s / j / Braava m6)

Live map of the current cleaning mission as a HA image entity:

- White background, blue travel path, light-blue cleaned area
- Dock marker, robot position with direction arrow
- Stuck events marked on the map
- **Map state survives HA restarts** — persisted to `hass.storage` after each mission
- **Room outline suggestions** — dashed rectangles from zone bounding boxes (900-series)
- **Door crossing markers** — small circles showing door crossings accumulated across missions

### Zone detection (Roomba 900)

Automatic room segmentation from travel data:

- Doorway crossings detected and shown as markers on the map
- Markers clustered across missions — shown after ≥2 sightings
- Detected zones persist across restarts
- User naming via Options Flow after each mission
- Calibration via door-width wizard

### Smart Map zone naming (i / s / j / Braava — without cloud credentials)

When new room IDs are discovered via MQTT, a **HA Repair Issue** is raised automatically. The fix flow opens directly in the Repairs dialog.

**v1.5 parser improvement:** the zone naming form now accepts both newline-separated entries (`1=Kitchen` per line) and comma-separated entries on one line (`1=Kitchen,17=Hallway`). Both formats produce identical results — the parser detects the delimiter automatically.

**With cloud credentials:** the repair flow is suppressed entirely — room names come from the cloud and are always current.

### Diagnostics

- Map subsystem: renderer config, pose point count, stuck events, cached image status
- Zone subsystem: gap threshold, calibration scale, full zone list with bounding boxes
- Geometry subsystem: door marker count, wall/door/obstacle counts, drift, wall offset
- **Cloud subsystem (v1.5):** coordinator status, pmap count, region count, favorite count, last exception

---

## Installation

### Requirements

- Home Assistant 2024.11 or newer
- HACS installed (recommended) or manual install

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. URL: `https://github.com/johnnyh1975/ha_roomba_plus` | Category: Integration
3. Install Roomba+ → restart HA

### Manual

1. Copy `custom_components/roomba_plus/` into your HA configuration directory
2. Restart HA

### Setup

1. Settings → Devices & Services → Add integration → **Roomba+**
2. Roomba is discovered automatically via DHCP/Zeroconf
3. Hold the HOME button on the Roomba for ~2 seconds until it plays tones
4. Integration connects
5. **(Smart Map robots, optional)** Enter your iRobot app email and password to enable cloud features — or leave blank to skip

> **Note:** Roomba+ and the built-in Core Roomba integration must not be active at the same time — they compete for the same local MQTT connection.

### Adding or updating cloud credentials after setup

Settings → Devices & Services → Roomba+ → Configure → **iRobot cloud credentials**

Enter your iRobot email and password, or clear both fields to disable cloud features. A credential test is run before saving — if it fails you will see a clear error message.

---

## Multiple Roomba robots

Each robot is set up as a separate integration entry with its own device, entities and storage. Repeat the Add Integration flow for each robot. Cloud credentials are stored per-robot.

---

## Retrieving credentials manually

If the automatic HOME button pairing fails:
→ [Retrieve iRobot credentials](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

---

## Dashboard card for the cleaning map

```yaml
type: picture-entity
entity: image.roomba_cleaning_map
show_name: false
show_state: false
```

**Recommended: xiaomi-vacuum-map-card (HACS)**

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_camera: image.roomba_cleaning_map
calibration_source:
  camera: true
```

---

## Maintenance reset

After replacing the filter, brushes, or battery:

1. Device page → Configuration
2. Press the corresponding reset button
3. The remaining-life countdown restarts from zero

---

## Device triggers

| Trigger | Description |
|---|---|
| Cleaning started | Robot transitions into an active cleaning phase |
| Cleaning finished | Robot returns to dock after completing a mission |
| Robot stuck | Robot reports a stuck condition |
| Bin full | Dust bin is full |
| Docked | Robot is docked and charging |
| Error reported | Robot reports any error |

---

## Automation ideas

### Absence-triggered cleaning

```yaml
automation:
  - alias: "Roomba — start when everyone is away"
    trigger:
      - platform: state
        entity_id: group.all_people
        to: "not_home"
    condition:
      - condition: time
        after: "09:00:00"
        before: "18:00:00"
      - condition: state
        entity_id: input_boolean.roomba_cleaned_today
        state: "off"
    action:
      - service: vacuum.start
        target:
          entity_id: vacuum.roomba
      - service: input_boolean.turn_on
        target:
          entity_id: input_boolean.roomba_cleaned_today
```

### Room-specific clean via service action

```yaml
service: roomba_plus.clean_room
target:
  entity_id: vacuum.roomba
data:
  room_name:
    - Kitchen
    - Hallway
  ordered: true
```

---

## Comparison with the Core integration

| Feature | Core Roomba | Roomba+ |
|---|---|---|
| Sensors | 13 | 35 |
| Cleaning map | ❌ | ✅ |
| Map persists across restarts | ❌ | ✅ |
| Zone detection (900-series) | ❌ | ✅ |
| Smart Map zone naming (repair flow) | ❌ | ✅ |
| Smart Map zones from cloud | ❌ | ✅ (v1.5) |
| Favorites from cloud | ❌ | ✅ (v1.5) |
| Maintenance reset | ❌ | ✅ |
| Edge cleaning toggle | ❌ | ✅ |
| Always finish (binPause) | ❌ | ✅ |
| Schedule hold | ❌ | ✅ |
| Bin present sensor | ❌ | ✅ |
| Mop ready sensor | ❌ | ✅ |
| Mission elapsed / recharge / expire sensors | ❌ | ✅ |
| SNR + signal noise sensors | ❌ | ✅ |
| IP address sensor | ❌ | ✅ |
| Idle / Stopped phase detection | ❌ | ✅ |
| Error codes (80+) | ❌ | ✅ |
| Device triggers | ❌ | ✅ |
| Carpet Boost (980) | ❌ | ✅ |
| Extended diagnostics | ❌ | ✅ |
| German translation | ✅ | ✅ |

---

## Troubleshooting

**"Failed to connect" during setup**

Press the physical **Clean** button on the robot to start a manual cleaning job, then immediately attempt credential retrieval in HA. Some models only respond while actively running.

**App loses connection when Roomba+ is running**

Expected with continuous mode enabled — the robot only allows one local MQTT connection. Disable continuous mode in Settings → Devices & Services → Roomba+ → Configure, or accept that the app will use the cloud while Roomba+ is connected.

**Cloud authentication fails**

Check your iRobot app email and password. If authentication is rate-limited ("mqtt slot" error), close the iRobot app on all devices and wait a few minutes before trying again.

**Smart Map zones not appearing / Repair Issue never fires (i / s / j-series)**

- Check `"cap": {"pose": ...}` in the diagnostics download shows a value ≥ 1
- If the Repair Issue appears but Fix shows "Problem resolved" without a form, ensure you have the full v1.4.4.9+ package including `repairs.py`
- **v1.5:** consider adding cloud credentials — the repair flow is replaced entirely by cloud-sourced zone names

**Zone naming form — all IDs on one line (pre-v1.5)**

Upgrading to v1.5 fixes this. The parser now accepts comma-separated input (`1=Kitchen,17=Hallway`) as well as the canonical newline-separated format. The pre-filled textarea also shows each zone on its own line.

**Error 224 / Smart Map localization failed**

Two causes, both fixed in v1.4.4.6:
1. Robot is updating its Smart Map — the integration now checks `notReady` bit 6 before sending and raises a clear error instead of silently sending.
2. region_id type mismatch on older firmware — numeric IDs are now sent as integers.

**Smart Map Zone selector goes unavailable after mission ends**

Fixed in v1.4.4.4 — `discovered_zone_ids` is backfilled from `smart_zone_data` on startup. Self-heals on next HA restart.

**Map entity shows blank white image (i7 / s9 / j-series)**

Smart Map robots do not broadcast local pose data via MQTT. The map image entity is suppressed for these robots from v1.4.4.4. Use the iRobot app to view the Smart Map.

**Migration from Core Roomba integration**

1. Settings → Devices & Services → iRobot Roomba and Braava → Delete
2. Restart Home Assistant
3. Add Roomba+ via HACS

**Migration from roomba_rest980**

1. Remove roomba_rest980 and stop the rest980 Docker container
2. Add Roomba+ — it connects directly to the robot without middleware
3. Enter your iRobot credentials in the setup flow to restore cloud zone names and favorites

---

## Credits

**[roombapy](https://github.com/pschmitt/roombapy)** — Python library for local MQTT/TLS communication with Roomba robots.

**[dorita980](https://github.com/koalazak/dorita980)** by Facu Decena — Pioneering work documenting the local MQTT protocol, cloud auth flows, and Smart Map commands.

**[rest980](https://github.com/koalazak/rest980)** by Facu Decena — REST interface and cloud API analysis, including the Gigya → AWS Cognito auth flow documented in cloudReverse.md.

**[roomba_rest980](https://github.com/ia74/roomba_rest980)** — Reverse-engineered iRobot cloud API client (Gigya → Cognito → AWS SigV4) whose auth implementation the v1.5 cloud layer is based on.

**[Roomba980-Python](https://github.com/NickWaterton/Roomba980-Python)** by Nick Waterton — Comprehensive Python implementation with detailed Roomba protocol documentation.

**[Home Assistant Core Roomba Integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/roomba)** — Architecture foundation for Roomba+.

> Roomba+ is an independent community project with no affiliation to iRobot or Picea Robotics.

---

## License

MIT License — use at your own risk.
