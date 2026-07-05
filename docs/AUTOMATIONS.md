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

A minimal dashboard combining the map, vacuum card, and key sensors:

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
```

---


---

*[Roomba+](../README.md) · [Features](FEATURES.md) · [API](API.md) · [Troubleshooting](TROUBLESHOOTING.md)*
