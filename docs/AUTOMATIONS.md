# Roomba+ — Automations & Dashboards

[← Roomba+](../README.md)

Copy-paste automation recipes and a starter dashboard. For the full list of
available events, device triggers, and services, see the
**[Feature reference →](FEATURES.md#events--device-triggers)**.

---

## Automation recipes

### Start cleaning when everyone leaves

```yaml
automation:
  alias: "Roomba — start when all away"
  trigger:
    - platform: state
      entity_id: group.all_people
      to: "not_home"
  condition:
    - condition: time
      after: "09:00:00"
      before: "18:00:00"
  action:
    - action: roomba_plus.smart_start
      target:
        entity_id: vacuum.roomba
```

### Clean specific rooms on a schedule

Wrap this in your own `trigger: time` (or `time_pattern`) — this recipe
is the *action*, not the schedule itself:

```yaml
action: roomba_plus.clean_room
target:
  entity_id: vacuum.roomba
data:
  room_name:
    - Kitchen
    - Hallway
  ordered: true
```

*(Looking for your robot's existing iRobot-app schedule instead of building
a new HA-side one? That's `calendar.{name}_schedule` — read-only, appears
automatically, no automation needed. This recipe is for building something
new, e.g. a schedule the app itself can't express, like room-specific
timing.)*

### Presence-aware cleaning with full timing control

```yaml
# Set Presence-aware scheduling to "Fire event" mode, then:
automation:
  alias: "Roomba — targeted clean when all away"
  trigger:
    - platform: event
      event_type: roomba_plus_all_away
  condition:
    - condition: time
      after: "09:00:00"
      before: "20:00:00"
  action:
    - action: roomba_plus.smart_start
      target:
        entity_id: vacuum.roomba
      data:
        rooms:
          - Kitchen
          - Hallway
```

### Pause when someone comes home mid-clean

```yaml
automation:
  alias: "Roomba — pause when someone arrives mid-clean"
  trigger:
    - platform: event
      event_type: roomba_plus_person_detected_during_clean
  action:
    - action: vacuum.pause
      target:
        entity_id: vacuum.roomba
    - action: notify.mobile_app
      data:
        message: "Roomba paused — someone came home."
```

### Wait for map save before cleaning

```yaml
automation:
  alias: "Roomba — clean kitchen after map save"
  trigger:
    - platform: state
      entity_id: binary_sensor.roomba_smart_map_saving
      to: "off"
  condition:
    - condition: state
      entity_id: input_boolean.roomba_kitchen_pending
      state: "on"
  action:
    - action: roomba_plus.clean_room
      target:
        entity_id: vacuum.roomba
      data:
        room_name: Kitchen
    - action: input_boolean.turn_off
      target:
        entity_id: input_boolean.roomba_kitchen_pending
```

---

### Daily catch-up clean for overdue rooms (v3.3.0)

Fires safely every day — does nothing when no room is actually due.

```yaml
automation:
  alias: "Roomba — daily catch-up clean"
  trigger:
    - platform: time
      at: "10:00:00"
  action:
    - action: roomba_plus.clean_overdue_rooms
      target:
        entity_id: vacuum.roomba
```

### Send the mission map after cleaning

Uses the v3.3.0 mission-map image endpoint directly — no card required.

```yaml
automation:
  alias: "Roomba — send mission map on finish"
  trigger:
    - platform: device
      domain: roomba_plus
      device_id: !input roomba_device
      type: cleaning_finished
  action:
    - action: notify.mobile_app
      data:
        message: "Cleaning finished."
        data:
          image: "/api/roomba_plus/{{ config_entry_id }}/missions/latest/map.png"
```

### Clean the dirtiest rooms when it's raining (indirect maintenance day)

Combines `auto_clean_dirty_rooms` (v3.3.0) with a simple weather condition —
no dirt-correlation setup required for this one, just the per-room dirt index.

```yaml
automation:
  alias: "Roomba — extra pass on dirty rooms, rainy day"
  trigger:
    - platform: state
      entity_id: weather.home
      to: "rainy"
  action:
    - action: roomba_plus.auto_clean_dirty_rooms
      target:
        entity_id: vacuum.roomba
      data:
        max_rooms: 2
```

---

## Dashboard example

A minimal dashboard combining the map, vacuum card, key sensors, and the
maintenance to-do list:

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.roomba_cleaning_map
    show_name: false
    show_state: false

  - type: vacuum
    entity: vacuum.roomba
    features:
      - type: start-pause
      - type: return-home

  - type: glance
    entities:
      - entity: sensor.roomba_clean_streak
        name: Streak
      - entity: sensor.roomba_last_mission_result
        name: Last mission
      - entity: sensor.roomba_filter_remaining_hours
        name: Filter
      - entity: sensor.roomba_mission_progress
        name: Progress
    columns: 4

  - type: todo-list
    entity: todo.roomba_maintenance
```

---

## Notification blueprint

v3.4.2 ships a ready-made automation blueprint covering a curated core set of
five notifications: mission complete, maintenance due, robot stuck, map
retrain detected, and battery capacity critical. Import it directly:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fjohnnyh1975%2Fha_roomba_plus%2Fmain%2Fblueprints%2Fautomation%2Froomba_plus_notifications.yaml)

Or manually: Settings → Automations & Scenes → Blueprints → Import Blueprint,
then paste:
```
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/roomba_plus_notifications.yaml
```

**Setup, once imported:**
1. Create a new automation from the blueprint (Blueprints tab → the blueprint's ⋮ menu → Create Automation).
2. **Robot** — pick your Roomba+ device. This is used to correctly match this robot's own events in a multi-robot household — matched on the robot's config entry internally, not its name, so a later rename won't break it.
3. **Notification action** — whatever you already use: a mobile app notify action, `persistent_notification.create`, a TTS announcement, or several of these chained together. Two variables are available inside it: `{{ notification_title }}` and `{{ notification_message }}`.
4. Toggle which of the five notifications you want (all five are on by default).
5. For **Maintenance due**, pick this robot's `binary_sensor.*_maintenance_due` entity.
6. For **Battery capacity critical**, pick this robot's `sensor.*_estimated_battery_eol` entity and, optionally, adjust the warning buffer (default: 14 days before the estimate hits zero). This sensor is self-calibrated per robot — it learns your robot's own degradation rate rather than using a fixed capacity percentage, so the same day-count buffer means something similar regardless of your robot's age or usage pattern. It also needs some cleaning history before it reports a value at all; that's expected, not a bug.

One blueprint import covers all your robots — repeat step 1 to create one automation instance per robot, each pointing at that robot's own entities.

## Three more blueprints (v3.4.3)

### Demand clean alert

Notifies you when your robot starts cleaning on its own — triggered by the
built-in dirt-sensor threshold, not your schedule or a manual start.

```
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/demand_clean_alert.yaml
```

Pick this robot's `sensor.*_job_initiator` entity ("Status – Started by")
and your notification action.

### Vacuum then mop

For two-robot households: starts a Braava mop automatically once a Roomba
vacuum finishes.

```
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/roomba_then_braava.yaml
```

Pick the vacuum device, the mop's vacuum entity, which mission results
should trigger the mop (default: completed or stuck-then-resumed — not
cancelled or errored runs), and a wait time (default 5 minutes) to let
dust settle first.

### Smart start on away

Starts cleaning when everyone leaves — either immediately, or timed so
cleaning is likely done before you return.

```
https://raw.githubusercontent.com/johnnyh1975/ha_roomba_plus/main/blueprints/automation/smart_start_on_away.yaml
```

Needs a presence entity (a `group`/`person`/`zone` that's `not_home` only
when everyone relevant has left — this blueprint watches it, it doesn't
build the presence logic itself) and the robot's vacuum entity. In "timed"
mode, also set an expected return time and an estimated cleaning duration
(check `sensor.*_average_mission_time` for a real number instead of
guessing) — if the computed start time has already passed by the time
everyone's away, cleaning starts immediately instead of not at all.

---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
