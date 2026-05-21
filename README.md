# Roomba+ — Enhanced iRobot Integration for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/Version-1.4.2-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2024.11%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Silver-silver.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)

Home Assistant Custom Integration für iRobot Roomba und Braava. Vollständig lokal, kein Cloud-Zwang, kein Abo — deutlich mehr Sensoren, Karte und Zonen als die eingebaute HA-Integration.

---

> 📊 **[Full feature comparison with HA Core Roomba and roomba_rest980 →](COMPARISON.md)**

## Supported devices

| Series | Examples | Map | Zones | Always finish | Schedule hold | Bin present | Tested |
|---|---|---|---|---|---|---|---|
| **600** (Bump & Run) | Roomba 694, 692 | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ untested |
| **900** (VSLAM) | Roomba 980, 985 | ✅ ephemeral | ✅ automatic | ❌ | ❌ | ❌ | ✅ **Roomba 980** |
| **i-series** | i3, i7, i7+ | ✅ | ✅ Smart Map | ✅ i7+ only | ✅ | ✅ | ✅ **i7+** |
| **s-series** | s9+ | ✅ | ✅ Smart Map | ✅ | ✅ | ✅ | ⚠️ untested |
| **j-series** | j7, j7+ | ✅ | ✅ Smart Map | ✅ j7+ only | ✅ | ✅ | ⚠️ untested |
| **Braava** | m6 | ✅ | ✅ Smart Map | ❌ | ✅ | ❌ (mop ready ✅) | ⚠️ untested |

> **Tested hardware:** Roomba 980 and Roomba i7+. Support for other series is implemented based on protocol documentation, capability flags and the roombapy library — but has not been verified on real hardware. If you own a different model and run into issues, please [open an issue](https://github.com/johnnyh1975/ha_roomba_plus/issues).

> "Always finish" requires a Clean Base dock. "Bin present" requires a removable bin (i/s/j-series). Availability is detected automatically from the robot's reported state — no manual configuration needed.

---

## Features

### Sensors (35)

Grouped for easy navigation in the diagnostics section:

- **Status** — phase (with Idle/Stopped detection), error (80+ codes), readiness, job initiator, next scheduled clean
- **Settings** — cleaning passes, carpet boost mode (900/i/s/j)
- **Maintenance** — filter remaining, brushes remaining, charge cycles
- **Missions** — total, successful, cancelled, failed, total time, avg. duration, cleaned area, last mission, mission start (active only), mission elapsed time, recharge time, expire time
- **Connectivity** — connected, bin full, battery, RSSI, SNR, signal noise (all opt-in), IP address
- **Clean Base** — dock status, dock tank level (when Clean Base present)
- **Braava** — tank level, mop pad type, mop behaviour, mop tank level
- **Navigation** — navigation quality / l_squal (VSLAM robots, opt-in)

### Controls

- **Cleaning passes** — Auto / One pass / Two passes (select)
- **Edge cleaning** — On/Off (switch)
- **Always finish** — keep cleaning even when the bin is full; the Clean Base empties it automatically mid-mission (switch, i7+/s9+/j7+ with Clean Base only — detected automatically)
- **Schedule hold** — freeze the cleaning schedule without deleting it, ideal for holidays or when guests are present (switch, i/s/j/Braava — detected automatically)
- **Maintenance reset** — confirm filter, brush, and battery replacement (buttons)
- **Locate robot** — play find-me tone (button)
- **Evacuate bin** — Clean Base models (button)

### Additional binary sensors

- **Bin present** — whether the dust bin is physically inserted. Relevant for i-series robots where the bin is removed during Clean Base evacuation and can accidentally be left out (i/s/j-series).
- **Mop problem** — combines `mopReady.tankPresent` and `mopReady.lidClosed` into a single problem sensor. ON when the Braava is not ready to mop — useful for automations that warn before a scheduled mopping mission (Braava m6).

### Cleaning map (Roomba 900 / i / s / j / Braava m6)

Live map of the current cleaning mission as a HA image entity:

- White background, blue travel path, light-blue cleaned area
- Dock marker, robot position with direction arrow
- Stuck events are marked on the map
- Map is retained after the mission ends
- **Map state survives HA restarts** — pose points and stuck markers are persisted to `hass.storage` after each mission and restored on startup
- **Room outline suggestions** — dashed grey rectangles showing approximate room boundaries derived from zone bounding boxes (900-series, v1.4.0+)
- **Door crossing markers** — small blue circles showing where the Roomba crossed between rooms, accumulated across missions (900-series, v1.4.0+)

### Zone detection (Roomba 900)

Automatic room segmentation from travel data:

- Doorway crossings are detected as room boundaries and shown as markers on the map
- Door crossing positions are clustered across missions — a marker seen in ≥2 missions is displayed on the map
- Detected zones are persistently stored across restarts
- User naming via Options Flow after each mission
- Calibration via door-width wizard (DIN 875 mm standard)

### Smart Map zone naming (i / s / j / Braava m6)

When the robot reports previously unseen room IDs from its Smart Map, a **HA Repair Issue** is automatically raised. The check runs both at HA startup and on live MQTT updates — no room-specific clean required to trigger it.

The fix flow opens directly in the Repairs dialog where you can assign names to each discovered zone. Zone IDs are persisted to integration storage so they survive robot state changes between discovery and naming. The issue dismisses itself once all zones have a name.

**Robots with multiple Smart Maps** are fully supported — the capability detection now correctly identifies all Smart Map robots regardless of how many maps are configured.

### Diagnostics

The diagnostics download (Settings → Devices & Services → Roomba+ → Download diagnostics) now includes:

- Map subsystem: renderer configuration, number of recorded pose points, stuck event count, whether a cached image is present
- Zone subsystem: gap threshold, calibration scale factor, full zone list with bounding boxes and confidence scores
- Geometry subsystem: door marker count, wall/door/obstacle counts, cumulative drift, wall offset setting

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

1. Copy `custom_components/roomba_plus/` from this repository into your HA configuration directory
2. Restart HA

### Setup

1. Settings → Devices & Services → Add integration → **Roomba+**
2. Roomba is discovered automatically via DHCP/Zeroconf
3. Hold the HOME button on the Roomba for ~2 seconds until it plays tones
4. Integration is connected

> **Note:** Roomba+ and the built-in Core Roomba integration must not be active at the same time — they compete for the same local MQTT connection. Disable the Core integration under Settings → Devices & Services before adding Roomba+.

> **Continuous mode:** The Roomba's local MQTT server only allows a single connection at a time. With continuous mode enabled (the default), the iRobot app is forced to connect via the cloud when Roomba+ is active. You can disable continuous mode in the integration options after setup to allow the app to connect locally — at the cost of a slightly longer reconnect delay after HA restarts. See the [Roomba 980 repository](https://github.com/NickWaterton/Roomba980-Python#firmware-2xx-notes) for details.

---

## Multiple Roomba robots

Roomba+ fully supports households with more than one robot. Each robot is set up as a separate integration entry with its own device, entities and storage.

1. Settings → Devices & Services → Add integration → **Roomba+**
2. Repeat for each robot — each gets its own BLID-based unique ID
3. All entities are named after the robot's name from the iRobot app, so `vacuum.roomba_downstairs` and `vacuum.roomba_upstairs` stay distinct

Each robot has completely separate state: its own ZoneStore, MaintenanceStore, map renderer state and hass.storage keys. There is no cross-contamination between robots even if they are the same model.

---

## Retrieving credentials manually

If the automatic HOME button pairing fails:
→ [Retrieve iRobot credentials](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

---

## Dashboard card for the cleaning map

The simplest option using the built-in picture-entity card:

```yaml
type: picture-entity
entity: image.roomba_cleaning_map
show_name: false
show_state: false
```

**Recommended: xiaomi-vacuum-map-card (HACS)**

The [lovelace-xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) gives you a fully interactive map with room selection, robot position, and real-time path overlay. It works with Roomba+ out of the box:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_camera: image.roomba_cleaning_map
calibration_source:
  camera: true
```

**Roomba Vacuum Card (HACS)**

The [lovelace-roomba-vacuum-card](https://github.com/jeremywillans/lovelace-roomba-vacuum-card) by jeremywillans shows status, battery, bin level and map in a single card:

```yaml
type: custom:vacuum-map-card
entity: vacuum.roomba
map_camera: image.roomba_cleaning_map
```

---

## Maintenance reset

After replacing the filter, brushes, or battery:

1. Device page → Configuration
2. Press the corresponding reset button
3. The remaining-life countdown restarts from zero

---

## Device triggers

Roomba+ registers native HA device triggers visible directly in the Automation editor under the device card. No entity monitoring needed — just pick the device and choose a trigger.

| Trigger | Description |
|---|---|
| Cleaning started | Robot transitions into an active cleaning phase |
| Cleaning finished | Robot returns to dock after completing a mission |
| Robot stuck | Robot reports a stuck condition |
| Bin full | Dust bin is full |
| Docked | Robot is docked and charging |
| Error reported | Robot reports any error |

Example — notify when the robot gets stuck:

```yaml
automation:
  trigger:
    platform: device
    domain: roomba_plus
    device_id: <your_device_id>
    type: stuck
  action:
    service: notify.mobile_app
    data:
      message: "Roomba is stuck and needs help!"
```

---

## Automation ideas

Roomba+ exposes enough sensors and controls to build meaningful automations. Here are some ideas to get you started.

### Start/stop cleaning automatically

**Absence-triggered cleaning** — start the Roomba when everyone leaves home during the day, stop and return to base when someone arrives. Add an `input_boolean.roomba_cleaned_today` helper as a guard so it only runs once per day:

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

  - alias: "Roomba — return to base when someone arrives"
    trigger:
      - platform: state
        entity_id: group.all_people
        to: "home"
    condition:
      - condition: state
        entity_id: vacuum.roomba
        state: "cleaning"
    action:
      - service: vacuum.return_to_base
        target:
          entity_id: vacuum.roomba

  - alias: "Roomba — reset daily guard at midnight"
    trigger:
      - platform: time
        at: "00:00:00"
    action:
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.roomba_cleaned_today
```

**Time window as extra condition** — add a time condition to the absence trigger so a late-night departure does not start the Roomba unexpectedly (already included in the example above).

**Guest mode** — create an `input_boolean.guest_mode` toggle. Add it as a condition to the absence automation so cleaning is suppressed while guests are present.

**Trigger after kitchen appliances** — if you have a dishwasher or oven integration, start the Roomba 10 minutes after they finish.

### Notifications and alerts

**Mission complete** — send a notification with the total cleaning time and cleaned area from the `sensor.roomba_total_cleaned_area` and `sensor.roomba_total_cleaning_time` sensors when the phase changes to `charge`.

**Roomba stuck** — use the native "Robot stuck" device trigger (see Device triggers section above) for a single-click setup in the Automation editor. Alternatively trigger on `sensor.roomba_phase` changing to `Stuck`.

**Bin full** — use the native "Bin full" device trigger, or watch `binary_sensor.roomba_bin_full` turning `on`.

**Maintenance due** — when `sensor.roomba_filter_remaining_hours` or `sensor.roomba_brush_remaining_hours` drops below a threshold (e.g. 5 h), send a weekly reminder.

**Connection lost** — alert when `binary_sensor.roomba_connected` stays `off` for more than 5 minutes.

### Integration with other devices

**Door sensor** — pause the Roomba when the front door opens during a cleaning mission, then resume after it closes again.

**Motion detector fallback** — if motion is detected at home while the Roomba is running (someone unexpectedly stayed home), send it back to base.

**Status light** — set a smart bulb to orange while the Roomba is cleaning and green when it is docked and idle.

**Voice assistant routine** — include a `vacuum.start` call in an Alexa or Google "leaving home" routine.

### Maintenance and monitoring

**Weekly maintenance report** — every Sunday, send a summary with the number of missions completed, total cleaned area, and remaining hours on filter and brushes.

**Charge cycle tracking** — log `sensor.roomba_battery_cycles` over time using the History Stats integration or InfluxDB to track battery wear.

**Stuck event monitoring** — watch `bbrun.nStuck` via the diagnostics data. A steep increase over time indicates a recurring obstacle that should be removed.

**Automatic reset reminder** — display a persistent notification or dashboard card after a set number of operating hours prompting the user to replace the filter.

---

## Comparison with the Core integration

For a detailed comparison of all three Roomba integrations (HA Core, Roomba+, roomba_rest980) including maps, zones, sensors and controls, see **[COMPARISON.md](COMPARISON.md)**.


| Feature | Core Roomba | Roomba+ |
|---|---|---|
| Sensors | 13 | 35 |
| Cleaning map | ❌ | ✅ |
| Map persists across restarts | ❌ | ✅ |
| Zone detection (900-series) | ❌ | ✅ |
| Smart Map zone naming prompt | ❌ | ✅ |
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
| Cleaning passes | ❌ | ✅ |
| Carpet Boost (980) | ❌ | ✅ |
| Extended diagnostics (map + zones) | ❌ | ✅ |
| German translation | ✅ | ✅ |
| Alphabetically grouped entities | ❌ | ✅ |

---

## Troubleshooting

**"Failed to connect" during setup**

Before attempting a factory reset, try the following: press the physical **Clean** button on the robot to start a manual cleaning job (do not use the app), then immediately attempt the credential retrieval in HA. Some models only respond to credential requests while actively running. If this still fails, factory reset the robot and try again.

**Credentials cannot be retrieved automatically**

Some newer models (j7 and later) require retrieving the password via the cloud. Use [dorita980's credential tool](https://github.com/koalazak/dorita980#how-to-get-your-usernameblid-and-password) which authenticates against the iRobot cloud and returns the local password.

**App loses connection when Roomba+ is running**

This is expected with continuous mode enabled — the robot only allows one local MQTT connection. Either disable continuous mode in the integration options (Settings → Devices & Services → Roomba+ → Configure), or accept that the app will use the cloud while Roomba+ is connected.

**Robot name shows MAC address instead of robot name**

Roomba+ fixes this automatically on the first state update after setup. If it persists, reload the integration once (Settings → Devices & Services → Roomba+ → ⋮ → Reload). The device name is updated in the HA Device Registry from the robot's reported `name` field.

**Map is empty after HA restart**

The map state is persisted to `hass.storage` after each completed mission and restored on startup. If the map appears empty after a restart, check that at least one mission has completed with v1.4.0 installed — the first mission writes the initial storage state. Door markers and zone outlines accumulate across missions and are also persisted separately. If the storage file is corrupted, removing and re-adding the integration clears it.

**Zone detection not working (900-series)**

Zone detection requires at least 20 pose points per segment. Short missions or missions in very open spaces may not produce enough data for reliable segmentation. Run a full cleaning mission covering the entire floor for best results. Use the door-width calibration wizard (Settings → Devices & Services → Roomba+ → Configure) to improve accuracy.

**Smart Map zones not appearing / Repair Issue never fires (i / s / j-series)**

In v1.4.2 the zone check runs at HA startup, so the Repair Issue should appear within seconds of restarting HA if any zones are unnamed — no room-specific clean required. If the issue still does not appear:

- Check that your robot is reporting `"cap": {"pose": ...}` with a value ≥ 1 in the HA diagnostics download. Robots with multiple Smart Maps previously required exactly `pose == 1` which excluded them — this is fixed in v1.4.2.
- Ensure `regions` in `lastCommand` is not reported as `null` by your firmware — this is also handled in v1.4.2.
- If the Repair Issue appears but clicking Fix immediately shows "Problem resolved" without a form, deploy the full v1.4.2 package which includes the required `repairs.py` file.

**Migration from Core Roomba integration**

1. Settings → Devices & Services → iRobot Roomba and Braava → Delete
2. Restart Home Assistant
3. Add Roomba+ via HACS and set it up as described above

Your automations targeting `vacuum.roomba` will continue to work — the entity ID is preserved if the robot name matches. Dashboard cards referencing `binary_sensor.roomba_bin_full` or similar may need updating to the new entity IDs.

**Migration from roomba_rest980**

1. Remove roomba_rest980 and stop the rest980 Docker container
2. Add Roomba+ — it connects directly to the robot without any middleware
3. Zone/room selections from cloud pmaps are not automatically carried over — use the SmartZone naming flow in Roomba+ to assign names to the locally discovered region IDs

---

## Credits

Roomba+ stands on the shoulders of an active open-source community:

**[roombapy](https://github.com/pschmitt/roombapy)** — Python library for local MQTT/TLS communication with Roomba robots. The foundation of the entire local connection layer in Roomba+. Originally developed for the Home Assistant Core Roomba integration.

**[dorita980](https://github.com/koalazak/dorita980)** by Facu Decena (koalazak) — Unofficial Node.js SDK for iRobot robots. Pioneering work documenting the local MQTT protocol, cloud auth flows, and Smart Map commands (`pmap_id`, `regions`).

**[rest980](https://github.com/koalazak/rest980)** by Facu Decena (koalazak) — REST interface built on dorita980. Source for analysing available API endpoints, command structures, and device capabilities.

**[Roomba980-Python](https://github.com/NickWaterton/Roomba980-Python)** by Nick Waterton — Comprehensive Python implementation with detailed documentation of the Roomba protocol, state dictionaries for all model series, and AWS Sig V4-based cloud authentication. Essential for analysing capability differences across the 600/900/i/s/j series.

**[Home Assistant Core Roomba Integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/roomba)** — The official integration whose architecture served as the starting point for Roomba+. Roomba+ extends and improves on this foundation while remaining compatible with the same local protocol.

**[PyRoomba](https://doi.org/10.1016/j.fsidi.2023.301686)** by Onik et al. (2024) — Forensic analysis of the Roomba cloud infrastructure documenting undisclosed API endpoints.

> Roomba+ is an independent community project with no affiliation to iRobot or Picea Robotics.

---

## License

MIT License — use at your own risk.
