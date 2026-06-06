# Roomba+ — Enhanced iRobot Integration for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/Version-2.3.5-brightgreen.svg)](https://github.com/johnnyh1975/ha_roomba_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2024.11%2B-blue.svg)](https://www.home-assistant.io/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-gold.svg)](https://www.home-assistant.io/docs/quality_scale/)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)

Roomba+ is a Gold-quality Home Assistant custom integration for iRobot Roomba and Braava robots. It connects directly over local Wi-Fi MQTT — no cloud account required, no polling, no subscription — and exposes far more sensors, intelligence, and controls than the built-in HA integration.

**Why Roomba+?**
- **100+ entities** (91 sensors, 15 binary sensors, 19 controls, 2 map images) vs 13 in the Core integration — maintenance life, wear rates, mission history, error intelligence, presence analytics, performance tracking, and more
- **Zero cloud dependency** — all robot control goes through local MQTT; cloud credentials are optional and only used for map sync and history enrichment
- **Automation-ready** — blocking sensor gate, presence-aware scheduling, device triggers, and named HA actions make Roomba a first-class citizen in your automations

> 📊 **[Full feature comparison with HA Core Roomba and roomba_rest980 →](COMPARISON.md)**

---

| Series | Examples | Map | Zones | Cloud features | Schedule hold | Bin present | Tested |
|---|---|---|---|---|---|---|---|
| **600** (Bump & Run) | Roomba 694, 692 | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ untested |
| **900** (VSLAM) | Roomba 980, 985 | ✅ ephemeral | ✅ automatic | ✅ mission history | ❌ | ❌ | ✅ **Roomba 980** |
| **i-series** | i3, i7, i7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ✅ **i7+** |
| **s-series** | s9+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ⚠️ untested |
| **j-series** | j7, j7+ | ✅ | ✅ Smart Map | ✅ optional | ✅ | ✅ | ✅ **j-series** |
| **Braava** | m6 | ✅ | ✅ Smart Map | ✅ optional | ✅ | ❌ (mop ready ✅) | ⚠️ untested |

> **Tested hardware:** Roomba 980, Roomba i7+, and j-series (lewis firmware). Support for other series is implemented based on protocol documentation, capability flags, and the roombapy library.

> Cloud features require your iRobot app email and password. They are entirely optional — all local MQTT functionality works identically without them.

---

## Installation

### Requirements

- Home Assistant 2024.11 or newer
- HACS installed (recommended) or manual install

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. URL: `https://github.com/johnnyh1975/ha_roomba_plus` | Category: Integration
3. Install **Roomba+** → restart HA

### Manual

1. Copy `custom_components/roomba_plus/` into your HA configuration directory
2. Restart HA

### Setup

1. Settings → Devices & Services → Add integration → **Roomba+**
2. Roomba is discovered automatically via DHCP/Zeroconf
3. Hold the **HOME** button on the robot for ~2 seconds until it plays tones
4. Integration connects
5. **(Smart Map robots, optional)** Enter your iRobot app email and password to enable cloud features — or leave blank to skip

> **Note:** Roomba+ and the built-in Core Roomba integration cannot run at the same time — they compete for the same local MQTT connection. Remove the Core integration first.

### Adding or updating cloud credentials after setup

Settings → Devices & Services → Roomba+ → Configure → **iRobot cloud credentials**

Enter your iRobot email and password, or clear both fields to disable cloud features. A credential test runs before saving.

### Reconfiguration

Host IP address and password can be changed without removing and re-adding the integration:
**Settings → Devices → Roomba+ → ⋮ → Reconfigure**

---

## Migration

### From the Core Roomba integration

1. Settings → Devices & Services → iRobot Roomba and Braava → Delete
2. Restart Home Assistant
3. Add Roomba+ via HACS

### From roomba_rest980

1. Remove roomba_rest980 and stop the rest980 Docker container
2. Add Roomba+ — it connects directly to the robot without middleware
3. Enter your iRobot credentials in the setup flow to restore cloud zone names and favorites

---

## Multiple robots

Each robot is set up as a separate integration entry with its own device, entities, and storage. Repeat the Add Integration flow for each robot. Cloud credentials are stored per robot.

---

## Features

### 🔴 Robot Status & Control

**Status sensors** give you full visibility into what the robot is doing at every moment:

| Sensor / Entity | Notes |
|---|---|
| Phase | Idle / Stopped detection beyond the standard HA states |
| Error code | 80+ error codes with `description` and `action` attributes |
| Readiness | Whether the robot is ready to start |
| Job initiator | Who triggered the current mission |
| Next scheduled clean | From `cleanSchedule2` or legacy `cleanSchedule` |
| Battery | Charge percentage |
| Connected | MQTT connectivity binary sensor |
| IP address | Current robot IP |
| RSSI / SNR / Signal noise | Wi-Fi signal quality |

**Controls** let you drive the robot beyond the standard HA vacuum card:

| Control | Type | Notes |
|---|---|---|
| Cleaning passes | Select | Auto / One pass / Two passes |
| Carpet boost | Select | Automatic / Eco / Performance (980 and compatible) |
| Edge cleaning | Switch | |
| Always finish | Switch | Keep cleaning even when bin is full (i7+/s9+/j7+ with Clean Base) |
| Schedule hold | Switch | Freeze schedule without deleting it (i/s/j/Braava) |
| Locate robot | Button | Play find-me tone |
| Evacuate bin | Button | Clean Base models only |

**Actions** callable from automations (Settings → Automations → Actions → Roomba+):

| Action | Applies to | Description |
|---|---|---|
| `roomba_plus.smart_start` | All robots | Start with blocking-sensor check |
| `roomba_plus.clean_room` | SMART robots (i/s/j) | Clean one or more named rooms |
| `roomba_plus.reset_filter` | All robots | Record filter replacement |
| `roomba_plus.reset_brush` | All robots | Record brush replacement |
| `roomba_plus.reset_battery` | All robots | Record battery replacement |
| `roomba_plus.reset_pad` | Braava | Record cleaning pad replacement |
| `roomba_plus.clean_sequence` | All robots | Start robot B when robot A finishes |

**Device triggers** for automations:

| Trigger | Description |
|---|---|
| Cleaning started | Robot transitions into an active cleaning phase |
| Cleaning finished | Robot returns to dock after completing a mission |
| Robot stuck | Robot reports a stuck condition |
| Bin full | Dust bin is full |
| Docked | Robot is docked and charging |
| Error reported | Robot reports any error |

**Experimental buttons — 900-series only (disabled by default)**

Enable individually via Settings → Devices & Services → Roomba+ → device → entity list.

| Button | Command | What it does |
|---|---|---|
| Spot clean | `spot` | Cleans a small area around the robot's current position |
| Quick clean | `quick` | Shorter full-floor mission |
| Sleep | `sleep` | Sends the robot to low-power sleep state |
| Power off | `off` | Powers the robot off completely |

> After `power off` the robot will not respond to local MQTT until physically woken — press the Clean button or use the iRobot app.

---

### 🟠 Cleaning & Zones

#### Smart Start with blocking sensor gate — all robots

Prevent cleaning from starting when a door is open, a room is occupied, or people are home. Configure via **Settings → Devices & Services → Roomba+ → Configure → Blocking sensors**.

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

Binary sensor: `start_blocked` (ON while queued, with `blocking_entities`, `queued_since`, `timeout_at` attributes).

#### Zone Management UI — 900-series and Smart Map robots

A unified config flow replaces the disconnected rename + repair flows:

**Settings → Devices & Services → Roomba+ → Configure → Zone management**

- Browse all zones in a structured index
- Rename any zone with a text input (Smart Map robots: alias overrides cloud name)
- Hide zones — removed from selectors, `clean_room`, and repair issues
- Changes saved atomically — one write, no partial state
- Alias-clear-on-match: typing the same name as the cloud name removes the alias so future cloud renames flow through automatically

#### Cloud zone sync — Smart Map robots (i/s/j/Braava, optional)

When cloud credentials are configured:

- Room and zone names come directly from your Smart Map — no manual repair-flow naming required
- One select entity per floor; robots with multiple Smart Maps get one per map (inactive maps disabled by default)
- Each saved cleaning routine in the iRobot app appears as a button entity
- `clean_room` action uses cloud names directly — no naming flow required
- Roomba+ detects map version changes in MQTT and immediately refreshes cloud data without waiting for the 24-hour poll
- The `smart_zones_need_naming` repair issue is suppressed when cloud is active

> Cloud credentials are stored encrypted in HA. All robot control commands go through local MQTT — only map metadata is fetched from the cloud.

#### Zone naming — Smart Map robots without cloud credentials

When new room IDs are discovered via MQTT, a HA Repair Issue is raised automatically. The fix flow opens directly in the Repairs dialog. The form accepts both newline-separated (`1=Kitchen` per line) and comma-separated (`1=Kitchen,17=Hallway`) input.

#### Zone detection — 900-series

Automatic room segmentation from travel data:

- Doorway crossings detected and shown as markers on the map
- Markers clustered across missions — shown after ≥2 sightings
- Detected zones persist across restarts
- User naming via Options Flow after each mission
- Calibration via door-width wizard

#### Cleaning map — 900-series and Smart Map robots

Live map of the current cleaning mission as a HA image entity:

- White background, blue travel path, light-blue cleaned area
- Dock marker, robot position with direction arrow
- Stuck events marked on the map
- Map state survives HA restarts — persisted to `hass.storage` after each mission
- Room outline suggestions — dashed rectangles from zone bounding boxes (900-series)
- Door crossing markers — small circles showing door crossings accumulated across missions

```yaml
# Minimal dashboard card
type: picture-entity
entity: image.roomba_cleaning_map
show_name: false
show_state: false
```

#### Coverage map (v2.2+)

`image.{name}_coverage_map` — EMA-weighted occupancy heatmap. Updated at each mission end. Attributes include `cell_count`, `stuck_event_count`, `decay`, `visit_increment`, and bounding box. Gated behind pose capability — registered automatically when `image.{name}_cleaning_map` would also be registered.

#### Rooms map (v2.3+, SMART robots)

`image.{name}_rooms_map` — static room layout from the UMF floor plan. Shows
room polygons on a dark canvas with no cleaning history overlay. Only visible
once UmfAligner confidence ≥ 0.70. This is the preferred source for
`lovelace-xiaomi-vacuum-map-card` configuration.

Both `image.{name}_cleaning_map` and `image.{name}_rooms_map` expose:
- `calibration` — three anchor point pairs for coordinate mapping
- `rooms` — per-room polygon outlines in image pixel space

**xiaomi-vacuum-map-card (HACS) — v2.3+ setup:**

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_source:
  camera: image.roomba_rooms_map        # use rooms_map for clean display
calibration_source:
  camera: true                          # reads calibration attribute
map_modes:
  - template: vacuum_clean_segment
    predefined_selections:
      - id: Kitchen
        label: Kitchen
        rooms:
          - Kitchen
      # add one entry per room; id and label must match the room names
      # returned by Roomba+ in the rooms attribute
```

> **Prerequisites for attributes to appear:**
> 1. SMART robot with cloud credentials configured.
> 2. UmfAligner confidence ≥ 0.70 (typically after 3+ missions with door crossings).
> 3. At least one mission completed since integration setup (so cloud UMF geometry is available).
>
> Until these conditions are met, `image.roomba_rooms_map` serves a blank dark image and exposes no `calibration` or `rooms` attributes — the card will show "Invalid calibration". This is expected.

For the cleaning history map instead:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.roomba
map_source:
  camera: image.roomba_cleaning_map
calibration_source:
  camera: true
```

> Both entities require a SMART robot with cloud credentials and UmfAligner
> confidence ≥ 0.70. The `rooms_map` entity returns a blank dark image until
> that threshold is reached (typically after 3+ missions with door crossings).

**Recommended for non-SMART robots:**

```yaml
type: picture-entity
entity: image.roomba_cleaning_map
show_name: false
show_state: false
```

#### Smart Map saving binary sensor — Smart Map robots

`binary_sensor.roomba_smart_map_saving` is ON while the robot is saving or uploading its Smart Map after a training run or boundary edit. Use this in automations that need to wait before issuing zone clean commands.

#### Start map training — Smart Map robots

Button that triggers a mapping survey without cleaning. Useful after moving furniture or adding a new room before the robot rebuilds its Smart Map.

---

### 🟡 Maintenance & Health

Track consumable life and get ahead of replacements before the robot starts failing missions.

#### Remaining life sensors

| Sensor | Notes |
|---|---|
| Filter remaining hours | With configurable threshold; `threshold_hours` attribute |
| Brush remaining hours | With configurable threshold; `threshold_hours` attribute |
| Cleaning pad remaining hours | Braava only |
| Charge cycles | From `bbchg3` |
| Battery capacity retention (%) | Degradation relative to design capacity |
| Estimated battery end of life (days) | Projected days until battery needs replacement |

#### Replacement tracking

Press the corresponding button (or call the action) after replacing a consumable — the remaining-life countdown restarts:

| Sensor | Button / Action | Available for |
|---|---|---|
| `filter_last_replaced` | `reset_filter` | All robots |
| `brush_last_replaced` | `reset_brush` | Vacuums |
| `pad_last_replaced` | `reset_pad` | Braava |
| `battery_last_replaced` | `reset_battery` | All robots |

Settings → device page → Configuration → press the reset button, or call the action from an automation.

#### Maintenance due binary sensor

`binary_sensor.roomba_maintenance_due` is ON when any consumable has reached zero remaining hours.
Attributes: `due` (list of consumables), `overdue_by_hours` (hours past threshold per consumable). One trigger instead of four separate threshold checks.

#### Wear Intelligence

Filter, brush, and pad wear rates tracked since the last replacement reset:

| Sensor | Notes |
|---|---|
| Filter wear rate (h/day) | Recalculated after every reset |
| Brush wear rate (h/day) | Recalculated after every reset |
| Filter days until due | Projected days remaining at current rate |
| Brush days until due | Projected days remaining at current rate |
| Pad wear rate / days until due | Braava only |

> All wear sensors show `Unknown` for the first 3 days after a reset — this is expected. A rate based on fewer days would be unreliable.

#### Device diagnostics — opt-in

Disabled by default. Enable via Settings → device → entity list.

| Sensor | Notes |
|---|---|
| Battery capacity (mAh) | From `bbchg3` |
| Navigation panic events | From `bbrun` |
| Cliff events front / rear | From `bbrun` |

---

### 🟢 Mission History & Intelligence

#### Mission Log

Every mission is recorded to a persistent log (up to 365 entries, FIFO). The log survives HA restarts.

| Sensor | Notes |
|---|---|
| Clean streak | Consecutive days with at least one completed mission |
| Missions last 30 days | Count of completed missions |
| Completion rate (30 days) | Completed ÷ total × 100 |
| Area cleaned today | Sum of mission area today (VSLAM robots) |
| Last mission result | `completed` / `stuck` / `cancelled` / `error` |
| Last mission duration | Duration in minutes |

Zone attribution by robot type: 600-series returns result and duration only; 900-series uses ZoneStore zone names; i/s/j-series uses `lastCommand.regions` reverse-looked-up against the active Smart Map.

#### Mission Phase Intelligence

| Sensor / Binary sensor | Notes |
|---|---|
| `mission_elapsed_min` | Minutes elapsed in the current mission. Uses `mssnM` from MQTT when available; falls back to wall-clock elapsed from `mssnStrtTm` (lewis firmware does not report `mssnM` mid-mission). |
| `mission_recharge_minutes` | Minutes until robot resumes after a mid-mission dock. Decrements live every 60 seconds — no MQTT push required. |
| `mission_expire_minutes` | Minutes until the mission expires. Same live countdown. |
| `mission_id` | Stable string across all recharge cycles of one mission (opt-in) |
| `binary_sensor.mission_active` | ON for the entire mission arc — run, hmMidMsn, mid-mission recharge, hmPostMsn. OFF when cycle returns to `none`. Distinct from `mid_mission_recharge` which is ON only during the charge phase. |
| `binary_sensor.mid_mission_recharge` | ON when `phase=charge` and `cycle≠none` — distinguishes mid-mission recharge from user-pause and from completed charging |

#### Room Intelligence — Smart Map robots, cloud required

Vacuum entity attributes populated from the active mission and post-mission timeline:

| Attribute | When available | Notes |
|---|---|---|
| `planned_room_order` | **During mission** (live) | Rooms in the order requested, resolved from `lastCommand.regions` against the active Smart Map. Populated immediately at mission start — no cloud poll required. |
| `mission_destination` | **During mission** (live) | Last room in `planned_room_order` — the final destination of the current mission. |
| `last_cleaned_rooms` | **Post-mission** | Rooms confirmed cleaned (status=0 or status=6 `room` events from timeline). Available within ~30 minutes of mission end once cloud timeline is merged. |
| `room_coverage` | **Post-mission** | Per-room cleaned fraction (0.0–1.0) from timeline `totalArea`. Dict keyed by room name. |

**Source priority:**
- During an active mission (`phase: run` or `hmMidMsn`): `planned_room_order` and `mission_destination` come from `lastCommand.regions` (lewis firmware) or `cleanMissionStatus.cmd.regions` (other variants), resolved against the active Smart Map region names.
- Post-mission: all four attributes are populated from the merged MissionStore timeline. `planned_room_order` and `mission_destination` switch to the timeline source at mission end.

**Prerequisites:** Cloud credentials configured, `region_count_active > 0` in diagnostics. All four attributes are `null` on robots without a Smart Map or without cloud credentials.

#### Error Intelligence

| Sensor | Notes |
|---|---|
| `last_error_code` | From live MQTT (priority) or persisted MissionStore value |
| `last_error_at` | Timestamp of the last error or stuck event |
| `last_error_zone` | Zone where the error occurred |
| `stuck_count_30d` | Stuck events in the last 30 days |
| `problem_zone` | Most frequently stuck zone over 30 days (VSLAM robots) |

`last_error_code` exposes actionable attributes:
```yaml
description: "The main brush roll is jammed."
action: "Remove the brush roll and clear hair or debris, then reinsert."
```

The error state is cleared automatically when the next mission completes successfully.

#### Performance sensors — cloud, opt-in

Derived from the iRobot cloud mission history:

| Sensor | Notes |
|---|---|
| Cleaning speed (sqft/min) | Median across recent missions |
| Cleaning speed trend | `improving` / `stable` / `declining` |
| Dirt density (events/sqft) | With `cause` attribute: `brush_wear` vs `floor_dirty` |
| Recharge fraction (%) | Share of mission time spent recharging |
| Coverage (%) | % of home baseline area cleaned, self-calibrating |
| Consecutive clean skips | Opt-in |

#### Wi-Fi sensors — cloud, opt-in

| Sensor | Notes |
|---|---|
| Wi-Fi signal floor (%) | Minimum signal seen during the mission — useful for dead-zone detection |
| Wi-Fi signal stability (%) | Variance across the mission — high variance indicates a dead zone |

#### Cloud diagnostics — all robots with credentials

Six sensors derived from the iRobot `/missionhistory` API (~30-day window):

| Sensor | Description |
|---|---|
| Recent completion rate | % of missions completed |
| Recent mid-mission recharges | Total recharge events |
| Recent Clean Base evacuations | Total bin evacuations |
| Recent dirt events | Total dirt detection events |
| Recent error code (cloud) | `pauseId` from the most recent failed mission — more reliable than MQTT. Attributes: `label`, `description`, `action` |
| Recent error time (cloud) | Timestamp of the most recent failed mission |

**900-series timestamp backfill:** 900-series firmware resets `mssnStrtTm=0` at mission end. Roomba+ automatically corrects these timestamps on startup using authoritative cloud values — no action required.

**Cloud analytics persistence (v2.1.3+):** Cloud fields (`dirt`, `chrgM`, `wlBars`, `timeline`) are now written into the local MissionStore after each mission, both at startup and within minutes of mission completion. These fields are no longer lost when HA restarts before the next cloud poll.

#### Cloud history sensors — all robots with credentials

| Sensor | Notes |
|---|---|
| Lifetime missions | True lifetime count from the cloud record |
| Recent cleaned area (30 d) | Sum of cleaned area across the ~30-mission API window (m²) |
| Recent cleaning time (30 d) | Sum of `runM` (actual cleaning time, excluding recharges) |

> Area and time reflect the API window (~30 recent missions), not a true lifetime accumulator — the iRobot API does not expose one. The `source: recent_mission_window` attribute documents this.

#### HA Long-Term Statistics

Roomba+ automatically backfills up to 365 days of mission history into HA Long-Term Statistics on every startup. To view it:

1. Add a **Statistics graph card** to your dashboard
2. Search for `roomba_plus:` to find the three series:
   - **Area cleaned** (m², daily sum — requires cloud credentials)
   - **Mission duration** (min, daily sum)
   - **Missions completed** (count, daily sum)

This works without the companion card and survives HA recorder purges indefinitely.

#### REST History API

```
GET /api/roomba_plus/{entry_id}/mission_history
```

| Parameter | Default | Values | Description |
|---|---|---|---|
| `format` | `summary` | `summary` / `records` / `hazards` | Response shape |
| `days` | `28` (summary) / `90` (records) | `1`–`90` | Lookback window |

`format=summary` — day-aggregated records: `date`, `total`, `completed`, `stuck`, `area_sqft`, `result`.

`format=records` — per-mission records with unified shape. Cloud robots include `run_min`, `recharges`, `evacuations`, `dirt_events`, `wifi_signal`. All robots include `started_at`, `ended_at`, `duration_min`, `area_sqft`, `result`, `initiator`, `zones`, `error_code`, `source`. From v2.3.0: `room_coverage` (per-room fraction from timeline, no cloud required) and `alignment_confidence` (null until UmfAligner aligned).

`format=hazards` — obstacle pin array. Returns GridStore stuck hotspots (`stuck_events`), cloud-detected obstacle centroids (`robot_learned`), and user-configured no-go zones (`keepout`, v2.3+). Each entry has `gx`, `gy`, `x_mm`, `y_mm`, `stuck_count`, `bearing_deg`, `distance_mm`, `source`, `room_name` (populated from UmfAligner in v2.3+ when confidence ≥ 0.70).

`GET /api/roomba_plus/household?days=28` — household aggregate. Returns `period_days`, `total` (missions/completed/completion_pct/area_sqft), `robots[]` (per-entry breakdown with `floor` label), and `floors[]` (grouped by floor label when any robot has a floor label set).

The `entry_id` is found in **Settings → Devices → Roomba+ → ⋮ → System information**.

Requires a valid HA long-lived access token:
```
Authorization: Bearer <long-lived-token>
```

---

### 🔵 Presence & Scheduling

#### Presence-Aware Scheduling — i/s/j/Braava

Automatically unfreeze the cleaning schedule when everyone leaves home, and re-freeze it when someone returns.

Configure via **Settings → Devices & Services → Roomba+ → Configure → Presence-aware scheduling**.

| Option | Description | Default |
|---|---|---|
| Enable | Master toggle | Off |
| Tracked persons | One or more `person.*` entities | — |
| Mode | `Unfreeze when all away` or `Fire event (manual control)` | Unfreeze when all away |
| Delay after leaving | Minutes to wait before unfreezing (0–60) | 5 min |

How it works:
- When all tracked persons leave, a configurable delay starts
- After the delay, `schedHold` is set to `false` — the robot's existing schedule runs normally
- When anyone returns, `schedHold` is set back to `true`
- The manager only re-freezes a hold it created — it never interferes with a hold set manually via the Schedule Hold switch

Events fired:
- `roomba_plus_all_away` — when delay expires in `always_ask` mode
- `roomba_plus_person_detected_during_clean` — when someone returns while a clean is running

Binary sensor: `schedule_hold_active` — ON when `schedHold` is true. The `source` attribute distinguishes `presence_manager` from `manual`.

#### Presence analytics sensors

| Sensor | Notes |
|---|---|
| Clean opportunities (7 days) | Away windows long enough for a full clean |
| Clean utilisation (7 days) | % of those windows that resulted in a clean |
| Next likely clean window | Heuristic forecast of the next likely away window |

---

### ⚪ Connectivity & Advanced

#### Braava / Mop sensors

| Sensor | Notes |
|---|---|
| Tank level | Water tank fill level |
| Mop pad type | Pad material |
| Mop clean mode | `Dry` / `Wet` derived from `padWetness` |
| Mop tank status | `Ready` / `Fill Tank` / `Lid Open` / `Tank Missing` |
| Mop ARS behavior | Auto Replenishment System mode |
| Mop ready / tank present / lid closed | Binary sensors |
| Lid open | Binary sensor — ON when lid is open |
| Tank present | Binary sensor — ON when water tank is installed |

**Braava pad wetness control:** select wetness level (Low / Medium / High) independently for disposable and reusable pads.

#### Navigation

| Sensor | Notes |
|---|---|
| Navigation quality / l_squal | VSLAM robots, opt-in |
| Nav panics | Opt-in |

#### Configuration reference

All options: **Settings → Devices → Roomba+ → Configure**

**Connection settings:**

| Parameter | Default | Description |
|---|---|---|
| Continuous connection | `true` | Keep MQTT connection open permanently. Disable on flaky networks. |
| Connection delay (s) | `30` | Seconds between reconnect attempts. |
| Map enabled | `true` | Enable live map rendering (900-series / EPHEMERAL robots). |
| Map size (px) | `600` | Rendered map image size in pixels (400–1200). |
| Map scale (mm/px) | `10.0` | Millimetres per pixel. Lower = more detail, smaller coverage area. |

#### Diagnostics download

The diagnostics download (Settings → device → ⋮ → Download diagnostics) includes:
- Map subsystem: renderer config, pose point count, stuck events, cached image status
- Zone subsystem: gap threshold, calibration scale, full zone list with bounding boxes
- Geometry subsystem: door marker count, wall/door/obstacle counts, drift, wall offset
- Cloud subsystem: coordinator status, last exception, `pmap_count_total`, `active_pmap_id`, `region_count_active`

---

## Automation ideas

### I want the robot to clean automatically when I leave home

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

### I want to clean specific rooms on a schedule

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

### I want presence-aware cleaning with full control over timing

```yaml
# Use "always_ask" mode in Presence-Aware Scheduling, then:
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

### I want to pause the robot automatically when someone comes home mid-clean

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

### I want to clean a room only after the Smart Map has finished saving

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
| Sensors | 13 | 73 local + 20 cloud |
| Cleaning map | ❌ | ✅ |
| Map persists across restarts | ❌ | ✅ |
| Zone detection (900-series) | ❌ | ✅ |
| Smart Map zone naming | ❌ | ✅ |
| Smart Map zones from cloud | ❌ | ✅ |
| Multi-map support | ❌ | ✅ |
| Favorites from cloud | ❌ | ✅ |
| Maintenance reset | ❌ | ✅ |
| Wear Intelligence | ❌ | ✅ |
| Battery capacity retention | ❌ | ✅ |
| Edge cleaning toggle | ❌ | ✅ |
| Always finish (binPause) | ❌ | ✅ |
| Schedule hold | ❌ | ✅ |
| Bin present sensor | ❌ | ✅ |
| Mop ready / clean mode / tank status | ❌ | ✅ |
| Mission recharge / expire sensors (live countdown) | ❌ | ✅ |
| SNR + signal noise sensors | ❌ | ✅ |
| Wi-Fi floor + stability sensors | ❌ | ✅ |
| IP address sensor | ❌ | ✅ |
| Idle / Stopped phase detection | ❌ | ✅ |
| Error codes (80+) with description + action | ❌ | ✅ |
| Device triggers | ❌ | ✅ |
| Consumable timestamp sensors | ❌ | ✅ |
| Blocking sensor gate (smart_start) | ❌ | ✅ |
| Zone management UI | ❌ | ✅ |
| Mission log (365 entries, persisted) | ❌ | ✅ |
| Error intelligence | ❌ | ✅ |
| Performance sensors (speed, coverage, dirt density) | ❌ | ✅ |
| Presence-aware scheduling | ❌ | ✅ |
| REST mission history API | ❌ | ✅ |
| HA Long-Term Statistics backfill | ❌ | ✅ |
| Cloud diagnostics (completion rate, recharges, dirt) | ❌ | ✅ |
| Lifetime stats from cloud | ❌ | ✅ |
| Spot / quick clean (980, experimental) | ❌ | ✅ |
| Sleep / power off (980, experimental) | ❌ | ✅ |
| Carpet Boost (980) | ❌ | ✅ |
| Extended diagnostics download | ❌ | ✅ |
| Coverage heatmap (GridStore) | ❌ | ✅ |
| Household REST endpoint | ❌ | ✅ |
| Obstacle hazards REST endpoint | ❌ | ✅ |
| Mission-active binary sensor | ❌ | ✅ |
| Carpet boost select entity | ❌ | ✅ |
| Sequential cleaning (clean_sequence) | ❌ | ✅ |
| CR4 room coverage attributes | ❌ | ✅ |
| German translation | ✅ | ✅ |

---

## Troubleshooting

**"Failed to connect" during setup**

Press the physical **Clean** button on the robot to start a manual cleaning job, then immediately attempt credential retrieval in HA. Some models only respond while actively running.

If automatic pairing fails entirely: → [Retrieve iRobot credentials manually](https://www.home-assistant.io/integrations/roomba/#retrieving-your-credentials)

**The iRobot app loses connection when Roomba+ is running**

Expected — the robot only allows one local MQTT connection. Either disable continuous mode in Settings → Roomba+ → Configure, or accept that the app will use the cloud while Roomba+ is connected.

**Cloud authentication fails**

Check your iRobot app email and password. If you see an "mqtt slot" error, close the iRobot app on all devices and wait a few minutes before retrying.

**smart_start queues forever / never starts**

Check that the blocking sensors are reporting correctly. Unavailable or unknown sensors are treated as non-blocking. If the queue expires, `roomba_plus_start_timeout` is fired — automate on this event to alert or retry.

**Zone management — changes not reflected in dropdown immediately**

Alias and hidden changes are written immediately, but the zone select dropdown may take one MQTT message cycle to refresh. This is a known limitation — typically resolves within seconds when the robot is active.

**filter_last_replaced / brush_last_replaced shows Unknown**

These sensors are Unknown until the first reset is performed. Press the reset button or call the reset action to populate them.

**Wear Intelligence sensors show Unknown**

Wear sensors need at least 3 days of mission data since the last reset to calculate a meaningful rate. They will populate automatically.

**Mission log sensors show Unknown after upgrading**

The mission log is populated going forward only. Streak, completion rate, and area sensors will be Unknown until the first mission completes after upgrading — this is expected.

**`last_error_code` shows a stale error after the robot has recovered**

The error state clears automatically when the next mission completes successfully. If it persists, restart HA to force re-reading the mission log from storage.

**Presence-aware scheduling step not visible in options menu**

The presence scheduling step only appears for robots that report `schedHold` in their MQTT state (i/s/j/Braava m6). It will not appear for 900-series or 600-series robots.

**Presence manager unfreezes schedule but robot doesn't clean**

Confirm the cleaning schedule is set in the iRobot app and enabled for the correct days. Roomba+ controls the hold — it does not set the schedule itself.

**Smart Map zones not appearing (i/s/j-series)**

Check that `"cap": {"pose": ...}` in the diagnostics download shows a value ≥ 1. If cloud credentials are configured, the repair flow is suppressed and names come directly from the cloud.

**Recent cleaned area / cleaning time show lower values than expected**

These sensors aggregate data from the iRobot API window (~30 recent missions). The iRobot API does not expose a lifetime accumulator for area or time. The `source: recent_mission_window` attribute documents this. The **total missions** sensor is different — it reads the lifetime counter embedded in every cloud record.

**Cloud mission history not available for my Roomba 980**

Go to Settings → Roomba+ → Configure → iRobot cloud credentials and re-save your credentials, then restart HA.

**`clean_room` says "rooms from different maps" after deleting the old map**

The iRobot cloud cache may take up to 24 hours to clear. Re-save the cloud credentials step in Configure to force an immediate coordinator refresh.

---

## Translations

| Language | Code | Status |
|---|---|---|
| English | `en` | ✅ Complete |
| German | `de` | ✅ Complete |
| French | `fr` | ✅ Complete (community contribution) |
| Italian | `it` | ✅ Complete — native speaker review welcome |
| Spanish | `es` | ✅ Complete — native speaker review welcome |
| Portuguese | `pt` | ✅ Complete (European) — native speaker review welcome |
| Dutch | `nl` | ✅ Complete — native speaker review welcome |

To contribute a translation or report an incorrect phrase, open an issue or pull request with the corrected `translations/<lang>.json` file.

---

## Credits

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
