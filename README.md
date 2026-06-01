# Roomba+ — Enhanced iRobot Integration for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/Version-2.0.0-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2024.11%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-gold.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)

Roomba+ is a Gold-quality Home Assistant custom integration for iRobot Roomba and Braava robots. It connects directly over local Wi-Fi MQTT — no cloud account required, no polling, no subscription. It exposes significantly more sensors, intelligence, and controls than the built-in HA integration, while remaining fully functional without internet access.

**v2.0** brings complete cloud diagnostics: per-mission history from the iRobot cloud API, six new cloud sensors (completion rate, recharges, evacuations, dirt events, cloud error code and time), automatic timestamp correction for 900-series robots, and a per-mission REST API for the companion Lovelace card. Architecture refactored into focused modules (`callbacks.py`, `services.py`). Gold Quality Scale. 1022 unit tests + 11 integration tests.

**v1.9** added wear intelligence, device diagnostics, Braava controls, cloud mission history for 900/980, and Mission Phase Intelligence. **v1.8** added the mission log, error intelligence, presence-aware scheduling, and REST history API.

---

> 📊 **[Full feature comparison with HA Core Roomba and roomba_rest980 →](COMPARISON.md)**

## Supported devices

| Series | Examples | Map | Zones | Cloud features | Schedule hold | Bin present | Tested |
|---|---|---|---|---|---|---|---|
| **600** (Bump & Run) | Roomba 694, 692 | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ untested |
| **900** (VSLAM) | Roomba 980, 985 | ✅ ephemeral | ✅ automatic | ✅ mission history | ❌ | ❌ | ✅ **Roomba 980** |
| **i-series** | i3, i7, i7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ✅ **i7+** |
| **s-series** | s9+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ⚠️ untested |
| **j-series** | j7, j7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ⚠️ untested |
| **Braava** | m6 | ✅ | ✅ Smart Map | ✅ optional | ✅ | ❌ (mop ready ✅) | ⚠️ untested |

> **Tested hardware:** Roomba 980 and Roomba i7+. Support for other series is implemented based on protocol documentation, capability flags and the roombapy library.

> Cloud features (v1.5+) require your iRobot app email and password. They are entirely optional — all local MQTT functionality works identically without them.

---

## Features

### Sensors (69 local + 9 cloud)

- **Status** — phase (with Idle/Stopped detection), error (80+ codes), readiness, job initiator, next scheduled clean
- **Settings** — cleaning passes, carpet boost mode (900/i/s/j)
- **Maintenance** — filter remaining (with threshold), brushes remaining (with threshold), charge cycles, filter last replaced, brushes last replaced, battery last replaced, cleaning pad last replaced (Braava)
- **Missions** — total, successful, cancelled, failed, total time, avg. duration, cleaned area, last mission, mission start, mission elapsed, recharge time, expire time
- **Connectivity** — connected, bin full, battery, RSSI, SNR, signal noise, IP address
- **Clean Base** — dock status, dock tank level
- **Braava** — tank level, mop pad type, mop behaviour, mop tank level
- **Navigation** — navigation quality / l_squal (VSLAM robots, opt-in)
- **Cloud history (v1.6)** — lifetime cleaned area (m²), lifetime cleaning time, lifetime mission count — available for all robots when cloud credentials are configured, including the 980
- **Cloud diagnostics (v2.0)** — recent completion rate (%), recent mid-mission recharges, recent Clean Base evacuations, recent dirt events, recent error code (cloud, with `label`/`description`/`action` attributes from the iRobot error catalogue), recent error time (cloud) — sourced from `/missionhistory` API, ~30-day window
- **Mission log (v1.8)** — clean streak (days), missions last 30 days, completion rate 30 days, area cleaned today (VSLAM robots), last mission result, last mission duration
- **Error intelligence (v1.8)** — last error code (with `description` and `action` attributes), last error time, last error zone, stuck events 30 days, problem zone (most frequent stuck zone, VSLAM robots)
- **Presence analytics (v1.8)** — clean opportunities 7 days, clean utilisation 7 days, next likely clean window
- **Mission Phase Intelligence (v1.9)** — `mission_recharge_minutes` (minutes until robot resumes after mid-mission dock), `mission_expire_minutes` (minutes until mission expires), `mission_id` (opt-in, stable string across all recharge cycles of one mission — useful for grouping related events in dashboards).
- **Wear Intelligence (v1.9)** — filter wear rate (h/day), brush wear rate (h/day), filter days until due, brush days until due. Equivalent pad sensors for Braava. Recalculated after every maintenance reset — all show `Unknown` until 3 days of data accumulate after the first reset.
- **Device diagnostics (v1.9, opt-in)** — battery capacity (mAh), navigation panic events, cliff events front, cliff events rear. Disabled by default — enable via the entity list.

### Binary sensors

- **Bin full** — problem indicator when bin is full
- **Bin present** — presence indicator when bin is inserted (i-series)
- **Connected** — MQTT connectivity
- **Mop ready / tank present / lid closed** — Braava m6
- **Smart Map saving (v1.6)** — ON while the robot is saving or uploading its Smart Map after a training run or boundary edit. Only present on Smart Map robots (i/s/j/Braava). Useful for automations that need to wait before issuing zone clean commands.
- **Maintenance due (v1.7)** — ON when any consumable has reached zero remaining hours. Attributes: `due` (list of consumables), `overdue_by_hours` (hours past threshold per consumable). One automation trigger instead of four separate threshold checks.
- **Start blocked (v1.7)** — ON while a `smart_start` is queued waiting for blocking sensors to clear. Attributes: `blocking_entities`, `queued_since`, `timeout_at`.
- **Schedule hold active (v1.8)** — ON when `schedHold` is true for any reason. Attribute `source` distinguishes `presence_manager` (automated by Roomba+) from `manual` (toggled by the switch). Only present on i/s/j/Braava robots.
- **Mid-mission recharge (v1.9)** — ON when the robot is recharging between cleaning passes (`phase=charge`, `cycle≠none`). Distinguishes mid-mission recharge from user-pause (both show `Paused` in HA) and from completed charging (`Docked`). Use this to drive dynamic dashboards that keep showing mission tiles during recharge.
- **Lid open (v1.9)** — ON when the Braava lid is open. Only present on Braava. Useful in automations to warn before a scheduled mopping run.
- **Tank present (v1.9)** — ON when the Braava water tank is physically installed (top-level MQTT field, distinct from `mop_ready.tank_present`). Only present on Braava.

### Controls

- **Cleaning passes** — Auto / One pass / Two passes (select)
- **Edge cleaning** — On/Off (switch)
- **Always finish** — keep cleaning even when the bin is full (switch, i7+/s9+/j7+ with Clean Base only)
- **Schedule hold** — freeze the cleaning schedule without deleting it (switch, i/s/j/Braava)
- **Maintenance reset** — confirm filter, brush, and battery replacement (buttons and named HA actions: `reset_filter`, `reset_brush`, `reset_battery`, `reset_pad`)
- **Smart start (v1.7)** — `roomba_plus.smart_start` action with blocking-sensor gate; supports optional room list (SMART robots) and `override_blocking` field
- **Locate robot** — play find-me tone (button)
- **Evacuate bin** — Clean Base models (button)
- **Start map training (v1.9)** — triggers a mapping survey without cleaning (button, Smart Map robots only — i/s/j-series). Useful after moving furniture or adding a new room before the robot rebuilds its Smart Map.
- **Braava pad wetness (v1.9)** — select wetness level (Low / Medium / High) independently for disposable and reusable pads. Only present on Braava when `padWetness` is reported in MQTT state.

### Experimental buttons — 900-series / 980 (v1.5.1, disabled by default)

Four additional buttons are available for 900-series robots (980/985), disabled by default. Enable them individually via **Settings → Devices & Services → Roomba+ → device → entity list**. These commands are confirmed present in the iRobot firmware protocol but their exact behaviour across all firmware versions has not been fully verified.

| Button | Command | What it does |
|---|---|---|
| Spot clean | `spot` | Cleans a small area around the robot's current position |
| Quick clean | `quick` | Shorter full-floor mission |
| Sleep | `sleep` | Sends the robot to low-power sleep state |
| Power off | `off` | Powers the robot off completely |

> `sleep` and `power off` are most useful in power management automations. Note that after `power off` the robot will not respond to local MQTT commands until physically woken — press the Clean button or use the iRobot app.

### Cloud features — all robots with iRobot credentials (v1.5/v2.0, optional)

When you enter your iRobot app email and password during setup (or later via **Configure → iRobot cloud credentials**), Roomba+ connects to the iRobot cloud.

**For 900-series robots (980, 985) — v1.9/v2.0:**
- **Mission statistics from iRobot cloud** — three sensors populated from the `/missionhistory` API:
  - **Total missions** (`lifetime_missions`) — the true lifetime mission count. The iRobot firmware embeds this counter in every cloud record; it reflects the full history, not just the API window.
  - **Cleaned area** (`lifetime_cleaned_area`) — sum of area across the API response window (~30 recent missions). The iRobot cloud does not expose a cumulative lifetime area, so this reflects recent activity. The sensor attribute `source: recent_mission_window` documents this.
  - **Cleaning time** (`lifetime_cleaning_time`) — sum of actual cleaning time (`runM`, excluding mid-mission recharge) across the API window. Same caveat as area. Available even though the 980 has no persistent Smart Map.

**For Smart Map robots (i/s/j/Braava m6) — available since v1.5:**

- **Zone select from cloud** — room and zone names come directly from your Smart Map; no manual repair-flow naming required. One select entity per floor.
- **Multi-map support** — robots with more than one stored Smart Map get one zone select entity per map. The active map is enabled by default; legacy/inactive maps are disabled by default and labelled `(inactive)`. Zones from inactive maps cannot conflict with cleaning operations.
- **Favorites as buttons** — each saved cleaning routine from the iRobot app appears as a button entity grouped under the robot device, firing that routine via local MQTT.
- **`clean_room` service works without naming flow** — room names from the cloud are available directly to the `roomba_plus.clean_room` service action; no repair flow or manual naming required.
- **Stable pmap_id** — `clean_room` and the zone buttons use the authoritative pmap_id from the cloud, eliminating stale-MQTT failures after a full-home clean overwrites `lastCommand`.
- **Auto-refresh on map retrain** — Roomba+ detects map version changes in the MQTT stream and immediately refreshes cloud data, so new room names appear in HA without waiting for the 24-hour background poll.
- **Repair flow suppressed** — when cloud is active the `smart_zones_need_naming` repair issue is not raised; names are always current from the cloud.

> Cloud credentials are stored in the HA config entry (encrypted at rest by HA). All robot control commands continue to go through local MQTT — only map metadata is fetched from the cloud.


### Consumable Intelligence — all robots (v1.7)

Four new timestamp sensors record when each consumable was last replaced:

| Sensor | Created for |
|---|---|
| `sensor.roomba_filter_last_replaced` | All robots |
| `sensor.roomba_brushes_last_replaced` | Vacuums (not Braava) |
| `sensor.roomba_cleaning_pad_last_replaced` | Braava only |
| `sensor.roomba_battery_last_replaced` | All robots |

Existing remaining-hours sensors now expose `threshold_hours` as an attribute — the Lovelace card (v1.8) uses this to render health bars without hard-coded thresholds.

Named HA actions callable from automations: `reset_filter`, `reset_brush`, `reset_battery`, `reset_pad`. These call the same store methods as the reset buttons — both surfaces remain valid.

### Blocking Sensors & Smart Start — all robots (v1.7)

A pre-start environment gate that checks configured binary sensors before allowing a clean to begin. Useful for preventing cleaning while a door is open, a room is occupied, or people are home.

Configure via **Settings → Devices & Services → Roomba+ → Configure → Blocking sensors**.

| Option | Values | Default |
|---|---|---|
| Blocking sensors | Any binary sensor entity IDs | (empty) |
| Behavior when blocked | `abort` or `queue and wait` | `queue and wait` |
| Queue timeout | 5–120 min | 30 min |

Use `roomba_plus.smart_start` instead of `vacuum.start` in automations:

```yaml
action: roomba_plus.smart_start
target:
  entity_id: vacuum.roomba
data:
  override_blocking: false   # set true to bypass sensors
  # rooms: [Kitchen, Hallway]  # SMART robots only
```

- **abort** — fires `roomba_plus_start_blocked` event immediately if any sensor is ON
- **queue** — waits until all sensors clear (up to timeout), then starts; fires `roomba_plus_start_timeout` if expired
- Unavailable/unknown sensors are treated as non-blocking

### Zone Management UI — EPHEMERAL + SMART robots (v1.7)

A unified config flow step replaces the disconnected rename + textarea + repair flows:

**Settings → Devices & Services → Roomba+ → Configure → Zone management**

- Browse all zones in a structured index
- Rename any zone with a text input (SMART robots: alias overrides cloud name)
- Hide zones: removed from selectors, clean_room, and repair issues
- Changes saved atomically — one write, no partial state
- Alias-clear-on-match: if you type the same name as the cloud name, the alias is deleted (future cloud renames flow through automatically)

**Options menu order (v1.8):** Settings → Zone management → Cloud credentials → Blocking sensors → Presence-aware scheduling *(i/s/j/Braava only)*


### Mission Log — all robots (v1.8)

Roomba+ now records every mission to a persistent log (up to 365 entries, FIFO). The log survives HA restarts and powers six new sensors:

| Sensor | Notes |
|---|---|
| `clean_streak` | Consecutive days with at least one completed mission |
| `missions_last_30d` | Count of completed missions in the last 30 days |
| `completion_rate_30d` | Completed / total × 100 over 30 days |
| `area_cleaned_today` | Sum of completed mission area today (VSLAM robots) |
| `last_mission_result` | `completed` / `stuck` / `cancelled` / `error` |
| `last_mission_duration` | Duration in minutes |

The log also feeds the Error Intelligence sensors, Presence Analytics sensors, and the REST history API used by the Lovelace card heatmap.

**Zone attribution per robot type:**
- **600-series** — result and duration only; no area or zone data
- **900-series (EPHEMERAL)** — zone names from `ZoneStore` at mission start
- **i/s/j-series (SMART)** — zone names from `lastCommand.regions` at mission start, reverse-looked-up against the active Smart Map

### Error Intelligence — all robots (v1.8)

Five new diagnostic sensors surface error context that was previously only visible in the iRobot app:

| Sensor | Notes |
|---|---|
| `last_error_code` | Populated from live MQTT (priority) or persisted MissionStore value |
| `last_error_at` | Timestamp of the last error or stuck event |
| `last_error_zone` | Zone where the error occurred (SMART: from `lastCommand`; EPHEMERAL: from ZoneStore) |
| `stuck_count_30d` | Number of stuck events in the last 30 days |
| `problem_zone` | Most frequently stuck zone over 30 days (VSLAM robots) |

`last_error_code` exposes two extra attributes readable by automations and the Lovelace card:
```yaml
description: "The main brush roll is jammed."
action: "Remove the brush roll and clear hair or debris, then reinsert."
```

The error state is cleared automatically when the next mission completes successfully. On HA restart, the last known error is restored from the mission log — if a completed mission followed the error, the state is correctly empty.

### Presence-Aware Scheduling — i/s/j/Braava robots (v1.8)

Automatically unfreeze the cleaning schedule when everyone leaves home, and re-freeze it when someone returns. Replaces the typical manual absence automation.

Configure via **Settings → Devices & Services → Roomba+ → Configure → Presence-aware scheduling**.

| Option | Description | Default |
|---|---|---|
| Enable presence-aware scheduling | Master toggle | Off |
| Tracked persons | One or more `person.*` entities | — |
| Mode | `Unfreeze when all away` or `Fire event (manual control)` | Unfreeze when all away |
| Delay after leaving | Minutes to wait before unfreezing (0–60) | 5 min |

**How it works:**
- When all tracked persons leave home, a configurable delay starts (default 5 min)
- After the delay, `schedHold` is set to `false` — the robot's existing cleaning schedule runs normally
- When anyone returns, `schedHold` is set back to `true` — the schedule is re-frozen
- If someone returns during the delay, the delay is cancelled and no write is made
- The manager only re-freezes a hold it created — it never interferes with a hold you set manually via the Schedule Hold switch
- `always_ask` mode fires the `roomba_plus_all_away` event instead of writing `schedHold`, giving you full control via automation

**Events fired:**
- `roomba_plus_all_away` — when delay expires in `always_ask` mode
- `roomba_plus_person_detected_during_clean` — when someone returns home while a clean is running

Three new analytics sensors (fed by the mission log) show how well your schedule is being used:
- `presence_clean_opportunities_7d` — away windows long enough for a full clean
- `presence_clean_utilisation_7d` — percentage of those windows that resulted in a clean
- `next_likely_clean_window` — heuristic forecast of the next likely away window

**Presence-aware scheduling example:**

```yaml
# "always_ask" mode — full control via automation
automation:
  - alias: "Roomba — start when all away"
    trigger:
      - platform: event
        event_type: roomba_plus_all_away
    action:
      - service: vacuum.start
        target:
          entity_id: vacuum.roomba
```

### Multi-Map Bug Fix — SMART robots with multiple iRobot accounts (v1.8)

If your iRobot account contains an old disabled Smart Map alongside the active one, and both maps share room names (e.g. `Kitchen`, `Studio`), v1.7 and earlier had two failure modes:

- **Silent wrong-map clean** — all selected rooms exist in both maps → robot localises on the wrong map for ~6 minutes then docks. No error returned.
- **`rooms_different_floors` error** — mixed unique and shared room names → rooms resolve to different maps → validation fires with a confusing error, even though the room selection was correct.

v1.8 fixes this at the source: `cloud_coordinator.regions` and `.zones` now only return data from the **active pmap**. The old map is ignored entirely. The diagnostics download now shows `pmap_count_total` (all maps returned by the API) alongside `region_count_active` (active map only), making this class of issue immediately visible.

**Who is affected:** Any SMART robot user with an old Smart Map still present in their iRobot account, even if that map is disabled in the iRobot app.

**Action required:** None — the fix is automatic. To permanently prevent the issue, delete the old map in the iRobot app under **More → Maps**.

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
- **Cloud subsystem (v1.5):** coordinator status, last exception
- **Cloud subsystem (v1.8):** `pmap_count_total` (all maps from API), `active_pmap_id`, `region_count_active` (active pmap only, after filter) — visible difference when a disabled old map is present

### REST History API (v1.8 / v2.0)

The integration exposes a REST endpoint for the companion Lovelace card and custom dashboards:

```
GET /api/roomba_plus/{entry_id}/mission_history
```

**Query parameters:**

| Parameter | Default | Values | Description |
|---|---|---|---|
| `format` | `summary` | `summary` / `records` | Response shape (see below) |
| `days` | `28` | `1`–`90` | Lookback window (summary format only) |

**`format=summary`** (default, backward compatible with card v0.1-beta):
Returns day-aggregated records: `date`, `total`, `completed`, `stuck`, `area_sqft`, `result`.

**`format=records`** (v2.0, for card v1.0):
Returns per-mission records with unified shape regardless of robot type or cloud availability. Cloud robots include `run_min`, `recharges`, `evacuations`, `dirt_events`, `wifi_signal`. All robots include `started_at`, `ended_at`, `duration_min`, `area_sqft`, `result`, `initiator`, `zones`, `error_code`, `source`.

The `entry_id` is found in **Settings → Devices → Roomba+ → ⋮ → System information**.

The mission log is available to the Lovelace card via an authenticated REST endpoint:

```
GET /api/roomba_plus/{entry_id}/mission_history?days=28
Authorization: Bearer <long-lived-token>
```

Returns a JSON array of daily summaries, sorted ascending by date:

```json
[
  {
    "date": "2025-05-01",
    "total": 2,
    "completed": 2,
    "stuck": 0,
    "area_sqft": 824,
    "result": "completed"
  }
]
```

`days` parameter: 1–90, default 28. The endpoint is used by the Roomba+ Lovelace card to render the cleaning history heatmap. It requires a valid HA long-lived access token.


---


### Cloud Diagnostics — all robots with iRobot credentials (v2.0)

Roomba+ v2.0 fetches per-mission records from the iRobot `/missionhistory` cloud API and exposes them as six new sensors:

| Sensor | Description |
|---|---|
| Recent completion rate | % of missions completed in the last ~30 days |
| Recent mid-mission recharges | Total mid-mission recharge events (~30 days) |
| Recent Clean Base evacuations | Total bin evacuations (~30 days) |
| Recent dirt events | Total dirt detection events (~30 days) |
| Recent error code (cloud) | `pauseId` from the most recent failed mission — more reliable than MQTT error codes. Attributes: `label`, `description`, `action` from the iRobot error catalogue |
| Recent error time (cloud) | Timestamp of the most recent failed mission |

**Timestamp backfill:** On 980/900-series robots, firmware resets `mssnStrtTm=0` at mission end, causing local mission records to show `duration=0`. v2.0 automatically corrects these timestamps using the authoritative cloud `startTime`/`timestamp` fields on the next HA start.

---

### Actions (v2.0)

All Roomba+ actions are available in **Settings → Automations → Actions → Roomba+**. Full field documentation is available in the action UI.

| Action | Applies to | Description |
|---|---|---|
| `roomba_plus.clean_room` | SMART robots (i/s/j) | Clean one or more named rooms. Room names must match the labels you assigned in Roomba+ options. |
| `roomba_plus.smart_start` | All robots | Start with blocking-sensor check. Queues or aborts if blocking sensors are active. Optionally targets rooms (SMART robots). |
| `roomba_plus.reset_filter` | All robots | Record filter replacement and restart remaining-life counter. |
| `roomba_plus.reset_brush` | All robots | Record brush replacement and restart remaining-life counter. |
| `roomba_plus.reset_battery` | All robots | Record battery replacement date. |
| `roomba_plus.reset_pad` | Braava | Record cleaning pad replacement date. |

---

### Configuration parameters

All options are available under **Settings → Devices → Roomba+ → Configure**.

**Connection settings:**
| Parameter | Default | Description |
|---|---|---|
| Continuous connection | `true` | Keep MQTT connection open permanently. Disable on flaky networks. |
| Connection delay (s) | `30` | Seconds between reconnect attempts. |
| Map enabled | `true` | Enable live map rendering (900-series / EPHEMERAL robots). |
| Map size (px) | `600` | Rendered map image size in pixels (400–1200). |
| Map scale (mm/px) | `10.0` | Millimetres per pixel. Lower = more detail, smaller coverage area. |

**Blocking sensors (L5):**
Configure one or more `binary_sensor` entities whose active state should block automatic cleaning. When a blocking sensor is active, `smart_start` either queues the start (queue mode) or aborts it (abort mode).

**Presence-aware scheduling (L6):**
Configure `person` entities to track. When all tracked persons are away, the scheduled clean is unfrozen (`schedHold` lifted). Requires at least one `person` entity.

**iRobot cloud credentials:**
Optional. Required for cloud features (lifetime stats, per-mission history, Smart Map zone sync). Enter your iRobot app email and password. Credentials are stored encrypted in HA. Can be added, updated, or cleared at any time without re-adding the robot.

**Reconfiguration:**
Host IP address and password can be changed without removing and re-adding the integration via **Settings → Devices → Roomba+ → ⋮ → Reconfigure**.

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

### Presence-based clean with smart_start

```yaml
# "always_ask" mode — start a room-targeted clean only when everyone is away
automation:
  - alias: "Roomba — targeted clean when all away"
    trigger:
      - platform: event
        event_type: roomba_plus_all_away
    condition:
      - condition: time
        after: "09:00:00"
        before: "20:00:00"
    action:
      - service: roomba_plus.smart_start
        target:
          entity_id: vacuum.roomba
        data:
          rooms:
            - Kitchen
            - Hallway
```

### Alert when person detected during clean

```yaml
automation:
  - alias: "Roomba — pause when someone comes home mid-clean"
    trigger:
      - platform: event
        event_type: roomba_plus_person_detected_during_clean
    action:
      - service: vacuum.pause
        target:
          entity_id: vacuum.roomba
      - service: notify.mobile_app
        data:
          message: "Roomba paused — someone came home."
```

### Wait for map save before zone clean

```yaml
automation:
  - alias: "Roomba — clean kitchen after map save completes"
    trigger:
      - platform: state
        entity_id: binary_sensor.roomba_smart_map_saving
        to: "off"
    condition:
      - condition: state
        entity_id: input_boolean.roomba_kitchen_pending
        state: "on"
    action:
      - service: roomba_plus.clean_room
        target:
          entity_id: vacuum.roomba
        data:
          room_name: Kitchen
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.roomba_kitchen_pending
```

---

## Comparison with the Core integration

| Feature | Core Roomba | Roomba+ |
|---|---|---|
| Sensors | 13 | 53 (+ 3 cloud) |
| Lifetime stats (area / time / missions) | ❌ | ✅ (v1.6, cloud) |
| Smart Map saving indicator | ❌ | ✅ (v1.6) |
| Cleaning map | ❌ | ✅ |
| Map persists across restarts | ❌ | ✅ |
| Zone detection (900-series) | ❌ | ✅ |
| Smart Map zone naming (repair flow) | ❌ | ✅ |
| Smart Map zones from cloud | ❌ | ✅ (v1.5+) |
| Multi-map support (active/inactive) | ❌ | ✅ (v1.6) |
| Favorites from cloud | ❌ | ✅ (v1.5+) |
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
| Consumable timestamp sensors | ❌ | ✅ (v1.7) |
| Blocking sensor gate (smart_start) | ❌ | ✅ (v1.7) |
| Zone management UI (alias / hide) | ❌ | ✅ (v1.7) |
| Mid-mission area + elapsed attrs | ❌ | ✅ (v1.7) |
| Mission log (365 entries, persisted) | ❌ | ✅ (v1.8) |
| Error intelligence (code + description + action) | ❌ | ✅ (v1.8) |
| Presence-aware scheduling | ❌ | ✅ (v1.8, i/s/j/Braava) |
| Multi-map collision fix | ❌ | ✅ (v1.8) |
| REST mission history API | ❌ | ✅ (v1.8) |
| Spot / quick clean (980) | ❌ | ✅ (v1.6, experimental) |
| Sleep / power off (980) | ❌ | ✅ (v1.6, experimental) |
| Carpet Boost (980) | ❌ | ✅ |
| Extended diagnostics | ❌ | ✅ |
| German translation | ✅ | ✅ |

---

## Troubleshooting

**Blocking sensors step missing from options menu**

The blocking sensors step appears for all robots. If it is not visible, ensure you are running v1.7.0 and have restarted HA after upgrading.

**smart_start queues forever / never starts**

Check that the blocking sensors are reporting correctly. Unavailable or unknown sensors are treated as non-blocking. If the queue expires, `roomba_plus_start_timeout` is fired — automate on this event to alert or retry.

**Zone management — changes not reflected in dropdown immediately**

Alias and hidden changes write to config options immediately, but the zone select dropdown in the UI may take one MQTT message cycle to refresh (typically seconds when the robot is active). This is a known limitation addressed in v1.7.1.

**filter_last_replaced / brush_last_replaced shows Unknown**

These sensors are Unknown until the first reset is performed after upgrading to v1.7.0. Press the reset button or call the reset action to populate them.

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

**"expected float" error on the Scale field in Connection settings**

Fixed in v1.6.1. The scale field validator required a Python `float` type but the stored default `10` is an `int`, which HA's frontend rejected before the form could be submitted. Please update and the Connection settings dialog will open and save normally.

**Configuration changes require a restart / "Config entry was never loaded!" in logs**

Fixed in v1.6.1. The `select` and `button` platforms were registered as loaded during setup for all Smart Map robots, but the unload path only unloaded them when cloud credentials were present. This mismatch left platforms in a broken state after any options change, requiring a restart to recover.

**Lifetime statistics sensors crash on startup (AttributeError: 'list' object has no attribute 'get')**

Fixed in v1.6.1. The `/missionhistory` API returns a JSON array, not a dict. The coordinator was storing the raw list directly, causing all three lifetime sensors to crash immediately. Please update and restart HA.

**Smart Map saving sensor not visible**

The `binary_sensor.roomba_smart_map_saving` entity is only created for Smart Map robots (i/s/j/Braava m6). It will not appear on 900-series or 600-series robots. If it is missing on an i7/s9/j7, verify that `"cap": {"pose": ...}` is present in the diagnostics download.

**Favorites appear under an unnamed/separate device**

Upgrade to v1.6. Earlier versions constructed device info incorrectly, causing HA to create a phantom unnamed device. Favorites now appear under the correct robot device automatically.

**Two Smart Maps — zones from the wrong map selected**

This cannot happen from v1.6. Inactive map zone selects are disabled by default and labelled `(inactive)`. The Clean Zone button only considers the active map's select entity. Enable the inactive map select only if you want to inspect its zones.

**`clean_room` service says "No rooms configured" even though cloud is active**

Upgrade to v1.6. Earlier versions required the repair naming flow to populate zone data even when cloud credentials were present. From v1.6 the service reads room names directly from the cloud coordinator.

**Experimental buttons not visible**

They are disabled by default. Go to Settings → Devices & Services → Roomba+ → your device → the entity list (including disabled entities) → enable the ones you want. They only appear on 900-series robots (980/985) — Smart Map robots (i/s/j) do not get them.

**Wear Intelligence sensors show Unknown for first 3 days after a reset**

This is expected — the wear rate calculation requires at least 3 days of mission data. They will populate automatically.

**Mission log sensors show "Unknown" after upgrading to v1.8.0**

The mission log is populated going forward — it has no history from before the upgrade. The streak, completion rate, and area sensors will be `Unknown` until the first mission completes. This is expected.

**`last_error_code` shows a stale error after the robot has recovered**

The error state is cleared automatically when the next mission completes successfully. If the sensor still shows an error after a successful clean, restart HA to force re-reading the mission log from storage.

**Presence-aware scheduling step not visible in options menu**

The presence scheduling step only appears for robots that report `schedHold` in their MQTT state (i/s/j/Braava m6). It will not appear for 900-series or 600-series robots.

**Wear rate sensors show Unknown after replacement reset**

Wear Intelligence sensors need at least 3 days of mission data recorded since the reset to calculate a meaningful rate. This is by design — a rate based on fewer days would be unreliable. The sensors will populate automatically once 3 days have elapsed.

**Lifetime cleaned area / cleaning time show surprisingly low values**

These sensors aggregate data from the iRobot cloud API response window (the most recent ~30 missions). The iRobot API does not expose a single lifetime accumulator for area or time — only the per-mission values are available. The sensor `extra_state_attributes` include `source: recent_mission_window` and a note explaining this. The **total missions** sensor is different: it reads the lifetime counter that the robot embeds in every cloud record, so it reflects the true lifetime count.

**Cloud mission history not available for my Roomba 980**

Cloud mission history for 900-series robots was introduced in v1.9. If you upgraded from an earlier version, go to **Settings → Devices → Roomba+ → Configure → iRobot cloud credentials** and re-enter (or just save) your credentials to activate the feature. A restart is required for the lifetime sensors to appear.

**Presence manager unfreezes schedule but robot doesn't clean**

Check that `schedHold` was actually blocking the schedule — press the **Schedule hold** switch to verify. Also confirm the cleaning schedule is set in the iRobot app and is enabled for the correct days. Roomba+ controls the hold; it does not set the schedule itself.

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear after deleting a map. Trigger an immediate refresh by pulling-to-refresh in the iRobot app, then go to **Settings → Devices → Roomba+ → Configure** and re-enter (or just save) the cloud credentials step to force a coordinator refresh.

**Migration from Core Roomba integration**

1. Settings → Devices & Services → iRobot Roomba and Braava → Delete
2. Restart Home Assistant
3. Add Roomba+ via HACS

**Migration from roomba_rest980**

1. Remove roomba_rest980 and stop the rest980 Docker container
2. Add Roomba+ — it connects directly to the robot without middleware
3. Enter your iRobot credentials in the setup flow to restore cloud zone names and favorites

## Translations

Roomba+ is available in the following languages:

| Language | Code | Status |
|---|---|---|
| English | `en` | ✅ Complete |
| German | `de` | ✅ Complete |
| French | `fr` | ✅ Complete (community contribution) |
| Italian | `it` | ✅ Complete — native speaker review welcome |
| Spanish | `es` | ✅ Complete — native speaker review welcome |
| Portuguese | `pt` | ✅ Complete (European) — native speaker review welcome |
| Dutch | `nl` | ✅ Complete — native speaker review welcome |

To contribute a translation or report an incorrect phrase, please open an issue or pull request with the corrected `translations/<lang>.json` file.

---

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
